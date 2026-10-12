"""Compose the README's opening loop from the readme.pw.ts opening frames.

Usage, from the repository root: python3 design/readme/opening.py <images folder>
Each frame places that moment's desktop and phone captures with hero.html's layout; the frames become
docs/images/opening.webp, an animated WebP that loops. Needs Google Chrome and ffmpeg with libwebp.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAYOUT = ROOT / "design/readme/hero.html"
OUT = ROOT / "docs/images/opening.webp"
WIDTH, HEIGHT = 2240, 1360
# Each moment and how long it holds, in seconds.
FRAMES = [("1-question", 2.6), ("2-chosen", 1.2), ("3-resumed", 3.2)]


def main(images: pathlib.Path):
    layout = LAYOUT.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        rendered = []
        for name, seconds in FRAMES:
            desktop, phone = (images / f"opening-{name}-{view}.png" for view in ("desktop", "phone"))
            for capture in (desktop, phone):
                if not capture.is_file():
                    sys.exit(f"missing capture: {capture}")
            page = tmp / f"{name}.html"
            page.write_text(layout.replace("../../docs/images/project-desktop.png", desktop.resolve().as_uri())
                            .replace("../../docs/images/decision-phone.png", phone.resolve().as_uri()))
            png = tmp / f"{name}.png"
            subprocess.run(["google-chrome", "--headless", "--hide-scrollbars", "--default-background-color=00000000",
                            f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={png}", page.as_uri()],
                           check=True, stdout=subprocess.DEVNULL)
            rendered.append((png, seconds))
        script = tmp / "frames.txt"
        script.write_text("".join(f"file '{png}'\nduration {seconds}\n" for png, seconds in rendered)
                          + f"file '{rendered[-1][0]}'\n")  # the demuxer applies the last duration only before another entry
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(script),
                        "-vf", "fps=10,scale=1600:-1:flags=lanczos,format=yuva420p",
                        "-c:v", "libwebp_anim", "-lossless", "0", "-quality", "82", "-loop", "0", str(OUT)],
                       check=True)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
