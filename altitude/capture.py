"""Small looping GIFs of what a validation lane did, recorded only when asked.

A lane hands this module a recording (a video file) or its terminal output with each line's arrival time; it keeps
4 frames a second, drops frames that do not change, holds a still screen at most 2 s and speeds a long run up evenly
to at most 60 s of playback, then encodes a 64-colour GIF of at most 1 MiB. `ffmpeg` is an optional capability: without
it, or when a capture does not fit its budget, the capture fails with `CaptureError` and the lane's own result stands.
`describe` checks a saved GIF before Altitude serves it. See docs/DEVELOPMENT.md#validation-captures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

BUDGET = 1 << 20            # bytes in one capture
RUN_LIMIT = 12              # captures one run keeps
FPS = 4
HOLD = 2.0                  # seconds a still screen is shown at most
PLAYBACK = 60.0             # seconds of playback at most
COLORS = 64
MAX_SIDE = 1024             # pixels on either side of a capture Altitude serves
MAX_FRAMES = 300
TIMEOUT = 120               # seconds for each ffmpeg step
PHONE, DESKTOP = 390, 800   # capture widths: about the phone's own layout, and a desktop page
COLUMNS, ROWS = 80, 24      # the terminal replay
UNAVAILABLE = "ffmpeg unavailable"


class CaptureError(Exception):
    """Why a capture was not kept; the lane's own result is unaffected."""


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffmpeg(*args: str) -> None:
    binary = ffmpeg()
    if not binary:
        raise CaptureError(UNAVAILABLE)
    try:
        subprocess.run([binary, "-nostdin", "-hide_banner", "-loglevel", "error", *args], capture_output=True,
                       timeout=TIMEOUT, check=True)
    except subprocess.TimeoutExpired:
        raise CaptureError(f"ffmpeg took longer than {TIMEOUT} s") from None
    except subprocess.CalledProcessError as exc:
        raise CaptureError(f"ffmpeg failed: {exc.stderr.decode(errors='replace').strip()[-300:]}") from None


def video(source: Path, target: Path, width: int) -> dict:
    """A recording as a capture at most `width` pixels wide. Its frames are made beside the recording, so they are
    removed with whatever holds it."""
    with tempfile.TemporaryDirectory(prefix="capture-", dir=source.parent) as work:
        # mpdecimate drops a frame only when almost nothing in it moved, so a typed letter or a spinner still counts.
        _ffmpeg("-i", str(source), "-vf", f"fps={FPS},mpdecimate=hi=128:lo=64:frac=0.01,"
                f"scale='min(iw,{width})':-2:flags=lanczos", "-fps_mode", "passthrough", "-frame_pts", "1",
                str(Path(work) / "%08d.png"))
        frames = [(path, int(path.stem) / FPS) for path in sorted(Path(work).glob("*.png"))]
        return _encode(frames, target, Path(work))


def terminal(lines: list[tuple[float, str]], target: Path, title: str, steps: tuple[str, ...] = ()) -> dict:
    """Terminal output, each line with its arrival in seconds from the start, replayed as an 80×24 terminal. Each line
    starts with its real elapsed time; the top line shows `title` and the newest of the output lines named in `steps`
    (the lane's own progress lines). Lines fill the screen and it then clears, like a pager, so consecutive frames
    differ by a line and a long run stays within the budget."""
    if not lines:
        raise CaptureError("the lane wrote no output")
    lines = sorted(((max(0.0, t), _printable(text)) for t, text in lines), key=lambda row: row[0])
    steps = {_printable(text) for text in steps}
    step = max(1 / FPS, lines[-1][0] / MAX_FRAMES)
    with tempfile.TemporaryDirectory(prefix="capture-") as work:
        frames, page, shown, at, current = [], [], 0, 0.0, ""
        while shown < len(lines):
            at = max(at + step, lines[shown][0])
            while shown < len(lines) and lines[shown][0] <= at:
                arrived, text = lines[shown]
                current = text if text in steps else current
                elapsed = int(arrived)
                page = (page if len(page) < ROWS - 1 else []) + [f"{elapsed // 60:02d}:{elapsed % 60:02d} {text}"]
                shown += 1
            screen = [_printable(f" {title}{' - ' + current.strip() if current else ''}"), *page]
            if not frames or screen != frames[-1][2]:
                path = Path(work) / f"{len(frames):08d}.pgm"
                path.write_bytes(_render(screen))
                frames.append((path, at, screen))
        return _encode([(path, t) for path, t, _ in frames], target, Path(work))


def _printable(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text.replace("\t", "    ").rstrip("\r\n"))
    return "".join(c if " " <= c <= "~" else "?" for c in text)[:COLUMNS]


# The public-domain X11 "fixed" 6×13 font (misc-fixed 6x13.bdf: "Public domain font. Share and enjoy."), printable
# ASCII from space to tilde, 13 rows of one byte each per glyph, the 6 pixels in each byte's high bits.
_FONT = bytes.fromhex(
    "0000000000000000000000000000002020202020202000200000000050505000000000000000000000005050F850F85050000000"
    "00002078A0A0702828F0200000000048A85010204050A890000000000040A0A040A0989068000000002020200000000000000000"
    "0010202040404040402020100000402020101010101020204000000020A870A820000000000000000000002020F8202000000000"
    "00000000000000000030204000000000000000F80000000000000000000000000000002070200000000808101020404080800000"
    "0000205088888888885020000000002060A02020202020F8000000007088880810204080F800000000F808102070080888700000"
    "0000101030505090F8101000000000F88080B0C8080888700000000070888080F08888887000000000F808101020204040400000"
    "00007088888870888888700000000070888888780808887000000000000020702000002070200000000000207020000030204000"
    "000008102040804020100800000000000000F80000F8000000000000804020100810204080000000007088880810202000200000"
    "000070888898A8A8B08078000000002050888888F888888800000000F048484870484848F0000000007088808080808088700000"
    "0000F048484848484848F000000000F8808080F0808080F800000000F8808080F080808080000000007088808080988888700000"
    "000088888888F888888888000000007020202020202020700000000038101010101010906000000000888890A0C0A09088880000"
    "00008080808080808080F8000000008888D8A8A8888888880000000088C8C8A8A898988888000000007088888888888888700000"
    "0000F0888888F0808080800000000070888888888888A87008000000F0888888F0A0908888000000007088808070080888700000"
    "0000F8202020202020202000000000888888888888888870000000008888888850505020200000000088888888A8A8A8A8500000"
    "00008888505020505088880000000088885050202020202000000000F808101020404080F8000000704040404040404040407000"
    "0000808040402010100808000000701010101010101010107000000020508800000000000000000000000000000000000000F800"
    "00201000000000000000000000000000000070087888986800000000808080F088888888F0000000000000007088808088700000"
    "0000080808788888888878000000000000007088F88088700000000030484040F040404040000000000000007088888878088870"
    "0000808080B0C88888888800000000002000602020202070000000000010003010101010909060000080808090A0C0A090880000"
    "000060202020202020207000000000000000D0A8A8A8A88800000000000000B0C888888888000000000000007088888888700000"
    "0000000000F0888888F0808080000000000078888888780808080000000000B0C880808080000000000000007088601088700000"
    "0000004040F040404048300000000000000088888888986800000000000000888888505020000000000000008888A8A8A8500000"
    "00000000008850202050880000000000000088888898680888700000000000F810204080F80000001820202020C0202020201800"
    "0000202020202020202020000000C0202020201820202020C000000048A8900000000000000000")
_GLYPH_WIDTH, _GLYPH_HEIGHT = 6, 13
_INK, _PAPER = 0xD8, 0x18


def _glyph_rows(ink: int, paper: int) -> list[list[bytes]]:
    rows = []
    for code in range(95):
        rows.append([bytes(ink if byte & (0x80 >> x) else paper for x in range(_GLYPH_WIDTH))
                     for byte in _FONT[code * _GLYPH_HEIGHT:(code + 1) * _GLYPH_HEIGHT]])
    return rows


_TEXT, _HEADER = _glyph_rows(_INK, _PAPER), _glyph_rows(_PAPER, _INK)


def _render(screen: list[str]) -> bytes:
    """One grayscale frame (binary PGM) of the terminal, the top line inverted."""
    width, height = COLUMNS * _GLYPH_WIDTH, ROWS * _GLYPH_HEIGHT
    out = [f"P5 {width} {height} 255\n".encode()]
    for index in range(ROWS):
        text = (screen[index] if index < len(screen) else "")[:COLUMNS].ljust(COLUMNS)
        glyphs = _HEADER if index == 0 else _TEXT
        for row in range(_GLYPH_HEIGHT):
            out.append(b"".join(glyphs[ord(c) - 32][row] for c in text))
    return b"".join(out)


def _encode(frames: list[tuple[Path, float]], target: Path, work: Path) -> dict:
    """Frames with their times as a looping GIF within the budget; anything over it is re-encoded once at three
    quarters of the size, and then refused."""
    if not frames:
        raise CaptureError("the recording has no frames")
    keep = MAX_FRAMES - 1   # the concat list repeats the last frame, which the encoder keeps as one more
    if len(frames) > keep:  # keep evenly spaced frames, each shown until the next kept one
        frames = [frames[round(i * (len(frames) - 1) / (keep - 1))] for i in range(keep)]
    holds = [min(HOLD, max(1 / FPS, later[1] - frame[1])) for frame, later in zip(frames, frames[1:])] + [HOLD]
    scale = min(1.0, PLAYBACK / sum(holds))
    listing = work / "frames.txt"
    listing.write_text("".join(f"file '{path}'\nduration {hold * scale:.3f}\n" for (path, _), hold in zip(frames, holds))
                       + f"file '{frames[-1][0]}'\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    for size in ("iw", "iw*3/4"):
        _ffmpeg("-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-vf",
                f"scale={size}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors={COLORS}:stats_mode=diff[p];"
                "[b][p]paletteuse=dither=none:diff_mode=rectangle", "-fps_mode", "vfr", "-loop", "0", str(target))
        if target.stat().st_size <= BUDGET:
            try:
                return describe(target.read_bytes())
            except ValueError as exc:
                target.unlink()
                raise CaptureError(str(exc)) from exc
    target.unlink()
    raise CaptureError(f"over the {BUDGET >> 20} MiB budget even at three-quarter size")


def describe(data: bytes) -> dict:
    """A capture's size, dimensions, frames and playback seconds; raises ValueError for anything that is not one
    complete GIF within the budget, MAX_SIDE and MAX_FRAMES, each frame inside its screen with image data."""
    if len(data) > BUDGET or data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF within the capture budget")
    try:
        width, height, flags = struct.unpack_from("<HHB", data, 6)
        at, frames, delay = 13 + (3 << ((flags & 7) + 1) if flags & 0x80 else 0), 0, 0

        def blocks(at: int) -> int:  # past a chain of data sub-blocks
            while data[at]:
                at += data[at] + 1
            return at + 1

        while data[at] != 0x3B:
            kind, at = data[at], at + 1
            if kind == 0x21:
                if data[at] == 0xF9 and data[at + 1] == 4:
                    delay += struct.unpack_from("<H", data, at + 3)[0]
                at = blocks(at + 1)
            elif kind == 0x2C:
                left, top, w, h, local = struct.unpack_from("<HHHHB", data, at)
                if not (w and h and left + w <= width and top + h <= height):
                    raise ValueError("a GIF frame outside its screen")
                at += 9 + (3 << ((local & 7) + 1) if local & 0x80 else 0)
                if not 2 <= data[at] <= 8 or not data[at + 1]:
                    raise ValueError("a GIF frame without image data")
                at = blocks(at + 1)
                frames += 1
            else:
                raise ValueError("unknown GIF block")
        if at != len(data) - 1:
            raise ValueError("data after the GIF trailer")
    except (IndexError, struct.error):
        raise ValueError("truncated GIF") from None
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE and 1 <= frames <= MAX_FRAMES):
        raise ValueError("GIF dimensions or frame count outside the capture limits")
    return {"bytes": len(data), "width": width, "height": height, "frames": frames, "seconds": round(delay / 100, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert one recording to a capture GIF (docs/DEVELOPMENT.md#validation-captures).")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--width", type=int, default=DESKTOP)
    args = parser.parse_args()
    try:
        print(json.dumps(video(args.source, args.target, args.width)))
    except CaptureError as exc:
        print(json.dumps({"none": str(exc)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
