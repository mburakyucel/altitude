"""Compose the README's opening loop and feature pictures from the readme.pw.ts and readme-features.pw.ts captures.

Usage, from the repository root: python3 design/readme/compose.py <captures folder> <output folder>
The opening loop's frames place a desktop and a phone capture with hero.html's layout and become an animated WebP
that loops; the task view and the terminal are such stills. Each other feature picture places two phone captures side
by side with pair.html's layout, each cropped to its top or bottom, and becomes a still WebP; its phone-width version
is the one capture that proves it, whole. Needs ffmpeg with libwebp and Playwright's Chromium headless shell under
PLAYWRIGHT_BROWSERS_PATH, which runs with its own sandbox, as in a validation run.
"""
import os
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
# The task view and the terminal: desktop and phone captures in the opening loop's layout, the phone covering the
# whole sidebar.
SCENES = {"feature-task": ("task-view-desktop", "task-view-phone"),
          "feature-terminal": ("terminal-2-typed-desktop", "terminal-2-typed-phone")}
# Each feature picture: its left and right phone captures and the end of each that stays in view.
FEATURES = {
    "feature-create": [("create-1-offer-phone", "bottom"), ("create-2-created-phone", "bottom")],
    "feature-proposal": [("proposal-phone", "top"), ("proposal-review-phone", "top")],
    "feature-live": [("task-phone", "top"), ("session-phone", "top")],
    "feature-review": [("review-findings-phone", "top"), ("review-phone", "top")],
    "feature-heal": [("heal-1-fault-phone", "top"), ("heal-2-repaired-phone", "bottom")],
    "feature-improve": [("improve-phone", "top"), ("improve-accepted-phone", "top")],
}
# The capture each feature shows at phone width; the live session's is docs/images/session-phone.png.
PROOF = {"feature-create": "create-2-created-phone", "feature-proposal": "proposal-review-phone",
         "feature-task": "task-view-phone", "feature-review": "review-findings-phone",
         "feature-heal": "heal-2-repaired-phone", "feature-improve": "improve-accepted-phone",
         "feature-terminal": "terminal-2-typed-phone"}


def browser() -> pathlib.Path:
    """Playwright's headless shell, from the folder `pnpm ui` and the validation runner install it into."""
    folder = pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
                          or pathlib.Path(os.environ.get("ALTITUDE_HOME", pathlib.Path.home() / ".altitude")) / "browsers")
    found = sorted(folder.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"),
                   key=lambda shell: int(shell.parts[-3].rsplit("-", 1)[1]))  # the newest build
    if not found:
        sys.exit(f"no Chromium headless shell under {folder}; run pnpm exec playwright install chromium in web/")
    return found[-1]


def render(chrome: pathlib.Path, layout: str, size: tuple[int, int], captures: dict[str, pathlib.Path],
           png: pathlib.Path):
    """The headless shell screenshots the layout with each placeholder image replaced by its capture."""
    for placeholder, capture in captures.items():
        if not capture.is_file():
            sys.exit(f"missing capture: {capture}")
        layout = layout.replace(placeholder, capture.resolve().as_uri())
    page = png.with_suffix(".html")
    page.write_text(layout)
    run = subprocess.run([str(chrome), f"--user-data-dir={png.parent / 'profile'}", "--hide-scrollbars",
                          "--default-background-color=00000000", f"--window-size={size[0]},{size[1]}",
                          f"--screenshot={png}", page.as_uri()], capture_output=True, text=True)
    if run.returncode or not png.is_file():  # the shell's routine stderr is noise; show it only on failure
        sys.exit(f"Chromium could not render {png.stem}:\n{run.stderr[-2000:]}")


def encode(out: pathlib.Path, command: list[str]):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *command, "-lossless", "0", "-quality", "82", str(out)],
                   check=True)
    print(f"{out.name}: {out.stat().st_size // 1024} KiB")


def main(images: pathlib.Path, out: pathlib.Path):
    chrome, hero, pair = browser(), HERO.read_text(), PAIR.read_text()
    out.mkdir(parents=True, exist_ok=True)

    def scene(desktop: str, phone: str, png: pathlib.Path, layout: str = hero, size: tuple[int, int] = (2240, 1360)):
        render(chrome, layout, size, {"../../docs/images/project-desktop.png": images / f"{desktop}.png",
                                            "../../docs/images/decision-phone.png": images / f"{phone}.png"}, png)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        frames = []
        for n, (desktop, phone, seconds) in enumerate(OPENING):
            png = tmp / f"opening-{n}.png"
            scene(desktop, phone, png)
            frames.append(f"file '{png}'\nduration {seconds}\n")
        script = tmp / "opening.txt"
        script.write_text("".join(frames) + frames[-1].split("\n")[0] + "\n")  # the demuxer applies the last duration only before another entry
        encode(out / "opening.webp", ["-f", "concat", "-safe", "0", "-i", str(script), "-vf",
               "fps=10,scale=1600:-1:flags=lanczos,format=yuva420p", "-c:v", "libwebp_anim", "-loop", "0"])
        for name, (desktop, phone) in SCENES.items():
            scene(desktop, phone, tmp / f"{name}.png", hero.replace("<body>", '<body class="task">'), (2060, 1240))
            encode(out / f"{name}.webp", ["-i", str(tmp / f"{name}.png"), "-vf", "scale=1720:-1:flags=lanczos",
                                          "-c:v", "libwebp", "-frames:v", "1"])
        for name, ((left, left_end), (right, right_end)) in FEATURES.items():
            png = tmp / f"{name}.png"
            layout = pair.replace("left top", f"left {left_end}").replace("right top", f"right {right_end}")
            render(chrome, layout, (1720, 1200), {"../../docs/images/task-phone.png": images / f"{left}.png",
                                                  "../../docs/images/session-phone.png": images / f"{right}.png"}, png)
            encode(out / f"{name}.webp", ["-i", str(png), "-c:v", "libwebp", "-frames:v", "1"])
        for name, capture in PROOF.items():
            encode(out / f"{name}-phone.webp", ["-i", str(images / f"{capture}.png"), "-c:v", "libwebp"])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
