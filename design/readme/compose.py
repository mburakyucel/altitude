"""Compose the README's opening loop and feature pictures from the readme.pw.ts and readme-features.pw.ts captures.

Usage, from the repository root: python3 design/readme/compose.py <images folder>
The opening loop's frames place a desktop and a phone capture with hero.html's layout and become an animated WebP
that loops. Each feature picture places two phone captures side by side with pair.html's layout, each cropped to
its top or bottom, and becomes a still WebP. Needs Google Chrome and ffmpeg with libwebp.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERO = ROOT / "design/readme/hero.html"
PAIR = ROOT / "design/readme/pair.html"
# The opening loop: each frame's desktop and phone captures and the seconds it holds.
OPENING = [("opening-1-question-desktop", "opening-1-question-phone", 2.6),
           ("opening-2-chosen-desktop", "opening-2-chosen-phone", 1.2),
           ("opening-3-resumed-desktop", "opening-3-resumed-phone", 3.2)]
# Each feature picture: its left and right phone captures and the end of each that stays in view.
FEATURES = {
    "feature-live": [("task-phone", "top"), ("session-phone", "top")],
    "feature-review": [("review-findings-phone", "top"), ("review-phone", "top")],
    "feature-heal": [("heal-1-fault-phone", "top"), ("heal-2-repaired-phone", "bottom")],
    "feature-improve": [("improve-phone", "top"), ("improve-accepted-phone", "top")],
    "feature-terminal": [("terminal-1-command-phone", "bottom"), ("terminal-2-typed-phone", "top")],
}


def render(layout: str, size: tuple[int, int], captures: dict[str, pathlib.Path], png: pathlib.Path):
    """Chrome screenshots the layout with each placeholder image replaced by its capture."""
    for placeholder, capture in captures.items():
        if not capture.is_file():
            sys.exit(f"missing capture: {capture}")
        layout = layout.replace(placeholder, capture.resolve().as_uri())
    page = png.with_suffix(".html")
    page.write_text(layout)
    chrome = subprocess.run(["google-chrome", "--headless", "--hide-scrollbars", "--default-background-color=00000000",
                             f"--window-size={size[0]},{size[1]}", f"--screenshot={png}", page.as_uri()],
                            capture_output=True, text=True)
    if chrome.returncode or not png.is_file():  # Chrome's routine stderr is noise; show it only on failure
        sys.exit(f"Chrome could not render {png.stem}:\n{chrome.stderr[-2000:]}")


def encode(out: pathlib.Path, command: list[str]):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *command, "-lossless", "0", "-quality", "82", str(out)],
                   check=True)
    print(f"{out.relative_to(ROOT)}: {out.stat().st_size // 1024} KiB")


def main(images: pathlib.Path):
    hero, pair = HERO.read_text(), PAIR.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        frames = []
        for n, (desktop, phone, seconds) in enumerate(OPENING):
            png = tmp / f"opening-{n}.png"
            render(hero, (2240, 1360), {"../../docs/images/project-desktop.png": images / f"{desktop}.png",
                                        "../../docs/images/decision-phone.png": images / f"{phone}.png"}, png)
            frames.append(f"file '{png}'\nduration {seconds}\n")
        script = tmp / "opening.txt"
        script.write_text("".join(frames) + frames[-1].split("\n")[0] + "\n")  # the demuxer applies the last duration only before another entry
        encode(ROOT / "docs/images/opening.webp", ["-f", "concat", "-safe", "0", "-i", str(script), "-vf",
               "fps=10,scale=1600:-1:flags=lanczos,format=yuva420p", "-c:v", "libwebp_anim", "-loop", "0"])
        for name, ((left, left_end), (right, right_end)) in FEATURES.items():
            png = tmp / f"{name}.png"
            layout = pair.replace("left top", f"left {left_end}").replace("right top", f"right {right_end}")
            render(layout, (1720, 1200), {"../../docs/images/task-phone.png": images / f"{left}.png",
                                          "../../docs/images/session-phone.png": images / f"{right}.png"}, png)
            encode(ROOT / f"docs/images/{name}.webp", ["-i", str(png), "-c:v", "libwebp", "-frames:v", "1"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
