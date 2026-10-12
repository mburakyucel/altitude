"""Compose the README's opening loop and feature pictures from the readme.pw.ts and readme-features.pw.ts captures.

Usage, from the repository root: python3 design/readme/compose.py <images folder>
Each frame places a desktop and a phone capture with hero.html's layout. A picture of several frames becomes an
animated WebP that loops; a picture of one frame, a still WebP. Needs Google Chrome and ffmpeg with libwebp.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAYOUT = ROOT / "design/readme/hero.html"
WIDTH, HEIGHT = 2240, 1360
# Each picture in docs/images: its frames as (desktop capture, phone capture, seconds the frame holds).
PICTURES = {
    "opening": [("opening-1-question-desktop", "opening-1-question-phone", 2.6),
                ("opening-2-chosen-desktop", "opening-2-chosen-phone", 1.2),
                ("opening-3-resumed-desktop", "opening-3-resumed-phone", 3.2)],
    "feature-live": [("task-desktop", "session-phone", None)],
    "feature-review": [("review-desktop", "review-phone", None)],
    "feature-heal": [("heal-1-fault-desktop", "heal-1-fault-phone", 2.4),
                     ("heal-2-repaired-desktop", "heal-2-repaired-phone", 3.6)],
    "feature-improve": [("improve-desktop", "improve-phone", None)],
}


def render(layout: str, desktop: pathlib.Path, phone: pathlib.Path, png: pathlib.Path):
    for capture in (desktop, phone):
        if not capture.is_file():
            sys.exit(f"missing capture: {capture}")
    page = png.with_suffix(".html")
    page.write_text(layout.replace("../../docs/images/project-desktop.png", desktop.resolve().as_uri())
                    .replace("../../docs/images/decision-phone.png", phone.resolve().as_uri()))
    chrome = subprocess.run(["google-chrome", "--headless", "--hide-scrollbars", "--default-background-color=00000000",
                             f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={png}", page.as_uri()],
                            capture_output=True, text=True)
    if chrome.returncode or not png.is_file():  # Chrome's routine stderr is noise; show it only on failure
        sys.exit(f"Chrome could not render {png.stem}:\n{chrome.stderr[-2000:]}")


def main(images: pathlib.Path):
    layout = LAYOUT.read_text()
    for name, frames in PICTURES.items():
        out = ROOT / f"docs/images/{name}.webp"
        with tempfile.TemporaryDirectory() as tmp:
            rendered = []
            for n, (desktop, phone, seconds) in enumerate(frames):
                png = pathlib.Path(tmp) / f"{n}.png"
                render(layout, images / f"{desktop}.png", images / f"{phone}.png", png)
                rendered.append((png, seconds))
            encode = ["-vf", "scale=1600:-1:flags=lanczos", "-lossless", "0", "-quality", "82"]
            if len(rendered) == 1:
                command = ["-i", str(rendered[0][0]), *encode, "-c:v", "libwebp", "-frames:v", "1"]
            else:
                script = pathlib.Path(tmp) / "frames.txt"
                script.write_text("".join(f"file '{png}'\nduration {seconds}\n" for png, seconds in rendered)
                                  + f"file '{rendered[-1][0]}'\n")  # the demuxer applies the last duration only before another entry
                command = ["-f", "concat", "-safe", "0", "-i", str(script), *encode,
                           "-c:v", "libwebp_anim", "-loop", "0"]
                command[command.index("-vf") + 1] = "fps=10,scale=1600:-1:flags=lanczos,format=yuva420p"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *command, str(out)], check=True)
        print(f"{out.relative_to(ROOT)}: {out.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
