/*
 * Host voice: the microphone streams to the Altitude computer, which transcribes it with its own speech
 * model and answers each chunk with the text so far. Wrapped in the same shape as RecognitionCapture, so
 * the composer runs one state machine: words arrive through `onupdate` while listening, and `text` holds
 * the final words once the capture stops.
 *
 * A dedicated audio graph, separate from the waveform, runs a worklet that turns the microphone into 16 kHz
 * 16-bit samples. One request is in flight at a time: each carries the samples gathered since the last
 * answer (normally half a second, at most ten seconds) and the next sequence number. A request lost on the
 * network is repeated with the same number, and the host answers it again without adding it twice. Stop
 * sends what is left as the final chunk and ends with the host's final text. No audio is kept.
 */
import { ApiError, cancelHostVoice, sendHostVoice, startHostVoice } from "../data/api";
import { traceVoice, voiceError } from "./voiceDiagnostics";

const RATE = 16000;
/** Send once this much is gathered; while a request is in flight, samples keep gathering. */
const CHUNK_SAMPLES = RATE / 2;
const MAX_CHUNK_SAMPLES = RATE * 10;
/** The microphone must deliver samples this soon after it opens. */
const FIRST_SAMPLES_MS = 2000;
/** A request that has no answer by then is repeated; the final one waits for the host's last words. */
const REQUEST_MS = 12000;
const FINAL_MS = 25000;
const RETRIES = 3;

/** Resample the microphone to 16 kHz 16-bit samples: the average of each output sample's input window. */
const WORKLET = `
class Resample extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / ${RATE}; this.at = 0; this.sum = 0; this.count = 0; this.out = []; }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i]; this.count++; this.at++;
      if (this.at >= this.step) {
        this.at -= this.step;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.out.push(value < 0 ? value * 32768 : value * 32767);
        this.sum = 0; this.count = 0;
      }
    }
    if (this.out.length >= ${RATE / 10}) {
      const samples = Int16Array.from(this.out);
      this.out = [];
      this.port.postMessage(samples, [samples.buffer]);
    }
    return true;
  }
}
registerProcessor("altitude-resample", Resample);
`;

/** Start delivering 16 kHz samples from the stream; resolves with the function that stops it. */
export type Listen = (stream: MediaStream, samples: (chunk: Int16Array) => void) => Promise<() => void>;

export const listenWithWorklet: Listen = async (stream, samples) => {
  const context = new AudioContext();
  try {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    try {
      await context.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    const node = new AudioWorkletNode(context, "altitude-resample");
    node.port.onmessage = (event: MessageEvent<Int16Array>) => samples(event.data);
    const source = context.createMediaStreamSource(stream);
    source.connect(node);
    // A node with no path to the destination is never processed in some browsers; a muted gain adds none.
    const mute = context.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(context.destination);
    if (context.state !== "running") await context.resume();
    return () => {
      node.port.onmessage = null;
      source.disconnect();
      void context.close().catch(() => undefined);
    };
  } catch (error) {
    void context.close().catch(() => undefined);
    throw error;
  }
};

export class HostCapture {
  /** How every capture hears the microphone; tests replace it, as the page has no audio worklet there. */
  static listen: Listen = listenWithWorklet;

  state: RecordingState = "inactive";
  readonly stream: MediaStream;
  readonly mimeType = "";
  /** "failed" when the recording stopped before its final words; `reason` says why. */
  failure: "denied" | "failed" | null = null;
  reason = "";
  /** The host refused this recording's setting or setup (409): the page's voice settings are out of date. */
  outdated = false;
  readonly unpunctuated = null;
  text = "";
  onstop: (() => void) | null = null;
  onupdate: ((text: string) => void) | null = null;
  /** The first microphone samples arrived: the composer shows Listening from here. */
  onlistening: (() => void) | null = null;

  private id: string | null = null;
  private seq = -1;
  private gathered: Int16Array[] = [];
  private gatheredSamples = 0;
  private sending = false;
  private stopping = false;
  private finished = false;
  private heard = false;
  private unlisten: (() => void) | null = null;
  private silence: ReturnType<typeof setTimeout> | null = null;
  private request: AbortController | null = null;
  private readonly selection: string;

  constructor(stream: MediaStream, selection: string) {
    this.stream = stream;
    this.selection = selection;
  }

  start() {
    traceVoice("host.start", this);
    this.state = "recording";
    for (const track of this.stream.getAudioTracks()) {
      track.addEventListener("ended", () => this.fail("Voice stopped: the microphone was interrupted."));
    }
    this.silence = setTimeout(() => { if (!this.heard) this.fail("Voice stopped: the microphone produced no audio."); }, FIRST_SAMPLES_MS);
    HostCapture.listen(this.stream, (chunk) => this.samples(chunk)).then(
      (unlisten) => {
        if (this.state === "inactive" || this.stopping) unlisten();
        else this.unlisten = unlisten;
      },
      (error) => {
        traceVoice("host.listen-error", this, { error: voiceError(error) });
        this.fail("Voice stopped: this browser could not process the microphone.");
      },
    );
    startHostVoice(this.selection).then(
      (opened) => {
        if (this.state === "inactive") { void cancelHostVoice(opened.id).catch(() => undefined); return; }
        this.id = opened.id;
        traceVoice("host.opened", this);
        this.pump();
      },
      (error) => this.fail(message(error), error),
    );
  }

  /** Stop listening, send what is left as the final chunk and end with the host's final words. */
  stop() {
    traceVoice("host.stop", this);
    if (this.state !== "recording" || this.stopping) return;
    this.stopping = true;
    this.release();
    this.pump();
  }

  /** Discard everything at once; the host drops the recording. */
  cancel() {
    traceVoice("host.cancel", this);
    if (this.state === "inactive") return;
    this.request?.abort();
    if (this.id) void cancelHostVoice(this.id).catch(() => undefined);
    this.end();
  }

  private samples(chunk: Int16Array) {
    if (this.state !== "recording" || this.stopping) return;
    if (!this.heard) {
      this.heard = true;
      traceVoice("host.samples", this);
      this.onlistening?.();
    }
    this.gathered.push(chunk);
    this.gatheredSamples += chunk.length;
    this.pump();
  }

  /** Send the next chunk when none is in flight and enough is gathered, or the rest once stopping. */
  private pump() {
    if (this.sending || this.finished || this.state !== "recording" || !this.id) return;
    if (!this.stopping && this.gatheredSamples < CHUNK_SAMPLES) return;
    const take = Math.min(this.gatheredSamples, MAX_CHUNK_SAMPLES);
    const pcm = new Int16Array(take);
    let filled = 0;
    while (filled < take) {
      const next = this.gathered[0]!;
      const used = Math.min(next.length, take - filled);
      pcm.set(next.subarray(0, used), filled);
      filled += used;
      if (used === next.length) this.gathered.shift();
      else this.gathered[0] = next.subarray(used);
    }
    this.gatheredSamples -= take;
    const final = this.stopping && this.gatheredSamples === 0;
    this.sending = true;
    this.seq += 1;
    void this.send(this.id, this.seq, pcm, final);
  }

  private async send(id: string, seq: number, pcm: Int16Array, final: boolean) {
    for (let attempt = 0; ; attempt++) {
      const request = new AbortController();
      this.request = request;
      const timer = setTimeout(() => request.abort(), final ? FINAL_MS : REQUEST_MS);
      try {
        const answer = await sendHostVoice(id, seq, pcm, final, request.signal);
        if (this.state === "inactive") return;
        this.text = answer.text;
        this.onupdate?.(this.text);
        this.sending = false;
        if (final) { this.finished = true; this.end(); } else this.pump();
        return;
      } catch (error) {
        if (this.state === "inactive") return;
        // A refusal is final; a lost or slow request is repeated with the same number.
        if (error instanceof ApiError || attempt >= RETRIES) {
          this.fail(error instanceof ApiError ? message(error) : "Voice stopped: the connection to Altitude was lost.", error);
          return;
        }
        traceVoice("host.retry", this, { error: voiceError(error) });
      } finally {
        clearTimeout(timer);
        if (this.request === request) this.request = null;
      }
    }
  }

  private fail(reason: string, error?: unknown) {
    if (this.state === "inactive" || this.failure) return;
    this.outdated = error instanceof ApiError && error.status === 409;
    traceVoice("host.failed", this);
    this.failure = "failed";
    this.reason = reason;
    this.request?.abort();
    if (this.id) void cancelHostVoice(this.id).catch(() => undefined);
    this.end();
  }

  private release() {
    if (this.silence) clearTimeout(this.silence);
    this.silence = null;
    this.unlisten?.();
    this.unlisten = null;
    this.stream.getTracks().forEach((track) => track.stop());
  }

  private end() {
    if (this.state === "inactive") return;
    this.release();
    this.state = "inactive";
    this.onstop?.();
  }
}

function message(error: unknown): string {
  if (error instanceof ApiError && error.status !== 401 && !error.message.startsWith("HTTP ")) return error.message;
  return "Voice stopped: the connection to Altitude was lost.";
}
