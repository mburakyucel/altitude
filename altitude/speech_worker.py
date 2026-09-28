"""The host speech worker: one process that runs the speech model for live dictation.

`altitude.speech` starts it with the interpreter the runtime was set up for, isolated (`python -I`), and the
runtime folder as its only argument. It imports nothing from Altitude: the model's libraries come from the
runtime's unpacked wheels, which only this process loads. It reads one JSON request per line on stdin and
writes one JSON answer per line on stdout:

    {"op": "open", "id": ...}                       a recording starts
    {"op": "audio", "id": ..., "pcm": base64}       16 kHz mono 16-bit little-endian samples follow
    {"op": "finish", "id": ...}                     no more audio: answer the final text
    {"op": "close", "id": ...}                      discard the recording

    {"ready": true}                                 the model is loaded
    {"id": ..., "text": ...}                        the recording's text so far
    {"id": ..., "text": ..., "final": true}         after finish
    {"error": ...}                                  the worker cannot go on (it exits)

Text is committed at pauses: once COMMIT_S of audio is unfinished, the audio up to the last pause is
transcribed once and kept, and later updates re-read only the rest. Without a pause by FORCE_S, the words
that started at least HOLD_S before the end are kept instead, so no update reads more than about FORCE_S.
"""
from __future__ import annotations

import base64
import json
import os
import select
import sys
from pathlib import Path

RATE = 16000
MODEL = "nemo-parakeet-tdt-0.6b-v2"
COMMIT_S = 8.0
FORCE_S = 12.0
HOLD_S = 1.0
MIN_PAUSE_S = 0.3
FRAME_S = 0.03
LEAD_S = 2.0
MIN_TAIL_S = 0.2
MAX_LINE = 1 << 20
THREADS = max(1, min(4, os.cpu_count() or 1))


def pause_cut(levels: list[float], lead: int, need: int) -> int | None:
    """The frame in the middle of the last run of at least `need` quiet frames after frame `lead`, or None.
    A frame is quiet below a tenth of the loud frames' level, so the microphone's gain does not matter."""
    if len(levels) <= lead:
        return None
    loud = sorted(levels)[int(len(levels) * 0.9)]
    quiet = max(0.003, loud * 0.1)
    run = 0
    for index in range(len(levels) - 1, lead - 1, -1):
        run = run + 1 if levels[index] < quiet else 0
        if run >= need:
            return index + run // 2
    return None


def word_cut(tokens: list[str], starts: list[float], end: float) -> tuple[int, float] | None:
    """Without a pause: keep the words that start at least HOLD_S before `end` except the last of them, which
    may still be growing. Returns how many tokens to keep and where their audio ends, or None."""
    words = [index for index, token in enumerate(tokens) if index == 0 or token.startswith(" ")]
    settled = [index for index in words if starts[index] <= end - HOLD_S]
    if len(settled) < 2:
        return None
    cut = settled[-1]
    return cut, max(0.0, starts[cut] - 0.04)


class Recording:
    def __init__(self) -> None:
        self.kept: list[str] = []
        self.pending = bytearray()
        self.changed = False
        self.finishing = False


class Worker:
    def __init__(self, runtime: Path) -> None:
        sys.path.insert(0, str(runtime / "site"))
        import numpy
        import onnx_asr
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        self.np = numpy
        self.model = onnx_asr.load_model(MODEL, str(runtime / "model"), quantization="int8",
                                         sess_options=options).with_timestamps()
        self.model.recognize(numpy.zeros(RATE, dtype=numpy.float32))  # the first call pays one-time setup
        self.recordings: dict[str, Recording] = {}

    def samples(self, pcm: bytes):
        return self.np.frombuffer(bytes(pcm[: len(pcm) // 2 * 2]), dtype="<i2").astype(self.np.float32) / 32768

    def read(self, audio):
        result = self.model.recognize(audio)
        return result.text.strip(), list(result.tokens or []), list(result.timestamps or [])

    def commit(self, recording: Recording) -> bool:
        """Keep the text of the audio up to a pause, or of the settled words; whether anything was kept."""
        audio = self.samples(recording.pending)
        seconds = len(audio) / RATE
        if seconds < COMMIT_S:
            return False
        frame = int(FRAME_S * RATE)
        count = len(audio) // frame
        levels = self.np.sqrt((audio[: count * frame].reshape(count, frame) ** 2).mean(1)).tolist()
        cut = pause_cut(levels, int(LEAD_S / FRAME_S), int(MIN_PAUSE_S / FRAME_S))
        if cut is not None:
            at = cut * frame
            text, _, _ = self.read(audio[:at])
        elif seconds >= FORCE_S:
            text, tokens, starts = self.read(audio)
            found = word_cut(tokens, starts, seconds)
            if found is None:
                return False
            kept, until = found
            text, at = "".join(tokens[:kept]).strip(), int(until * RATE)
        else:
            return False
        if text:
            recording.kept.append(text)
        del recording.pending[: at * 2]
        return at > 0

    def update(self, ident: str, recording: Recording) -> dict:
        while self.commit(recording):
            pass
        tail = ""
        if len(recording.pending) >= MIN_TAIL_S * RATE * 2:
            tail, _, _ = self.read(self.samples(recording.pending))
        answer = {"id": ident, "text": " ".join(part for part in [*recording.kept, tail] if part)}
        if recording.finishing:
            answer["final"] = True
            self.recordings.pop(ident, None)
        return answer

    def apply(self, request: dict) -> None:
        ident, op = request.get("id"), request.get("op")
        if not isinstance(ident, str):
            return
        if op == "open":
            self.recordings[ident] = Recording()
        elif op == "close":
            self.recordings.pop(ident, None)
        elif ident in self.recordings:
            recording = self.recordings[ident]
            if op == "audio":
                recording.pending.extend(base64.b64decode(request.get("pcm", "")))
                recording.changed = True
            elif op == "finish":
                recording.finishing = recording.changed = True

    def serve(self, source, sink) -> None:
        """Apply every waiting request, then bring each changed recording up to date, finishing ones first."""
        send(sink, {"ready": True})
        while True:
            waiting = bool(self.recordings) and any(r.changed for r in self.recordings.values())
            if not waiting or select.select([source], [], [], 0)[0]:
                line = source.readline(MAX_LINE)
                if not line:
                    return
                self.apply(json.loads(line))
                continue
            ident, recording = min(((i, r) for i, r in self.recordings.items() if r.changed),
                                   key=lambda item: not item[1].finishing)
            recording.changed = False
            send(sink, self.update(ident, recording))


def send(sink, answer: dict) -> None:
    sink.write(json.dumps(answer) + "\n")
    sink.flush()


def main() -> None:
    try:
        worker = Worker(Path(sys.argv[1]))
    except Exception as exc:  # noqa: BLE001 — the daemon shows a fixed reason and logs this one
        send(sys.stdout, {"error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(1)
    worker.serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
