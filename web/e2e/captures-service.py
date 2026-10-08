"""Validation captures attached to L2 replies by `alt task reply --capture`'s own path and served over HTTP; fictional
GIFs, no ffmpeg."""
import hashlib
import struct
import uuid

from service_support import configure, serve
from altitude import config, server, state as S, tasks as T

COLOURS = [(243, 244, 246), (37, 99, 235), (17, 24, 39), (22, 163, 74), (220, 38, 38)]


def gif(width: int, height: int, frames: int, bar: int) -> bytes:
    """A looping GIF whose bar fills across its frames. Each code is one byte (a 128-colour table) and a clear code
    every 100 pixels keeps the LZW table from growing, so no compressor is needed."""
    table = b"".join(bytes(COLOURS[i]) if i < len(COLOURS) else b"\0\0\0" for i in range(128))
    out = [b"GIF89a", struct.pack("<HHBBB", width, height, 0xF6, 0, 0), table,
           b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"]
    for frame in range(frames):
        filled = (frame + 1) * (width - 20) // frames
        pixels = [2 if y < 12 else bar if 30 <= y < 50 and 10 <= x < 10 + filled else 0
                  for y in range(height) for x in range(width)]
        codes = bytearray()
        for index, pixel in enumerate(pixels):
            if index % 100 == 0:
                codes.append(128)
            codes.append(pixel)
        codes.append(129)
        blocks = b"".join(bytes([len(codes[at:at + 255])]) + codes[at:at + 255] for at in range(0, len(codes), 255))
        out += [b"\x21\xF9\x04\x00", struct.pack("<H", 60), b"\x00\x00",
                b"\x2C", struct.pack("<HHHHB", 0, 0, width, height, 0), b"\x07", blocks, b"\x00"]
    return b"".join(out + [b"\x3B"])


def main():
    configure()
    repo = config.PROJECT_ROOTS[0] / "atlas"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("Fictional browser project.\n")
    with config.add_project("atlas", path=repo):
        pass
    slug = T.new("atlas", "Send flow captures", "Record the fictional send flow.")["slug"]
    T.block("atlas", slug, "Fictional captured validation.", actor="l2", updates={"waiting_on": "l3"})
    T.message("atlas", slug, "l2", "I am recording the send flow on phone and desktop.")
    frames = {"Phone send": (3, 1), "Desktop send": (3, 3), "Desktop reconnect": (2, 4)}
    files = {title: gif(120, 80, count, bar) for title, (count, bar) in frames.items()}
    rows = {}
    for key, run, text, titles in (
            ("single", 3, "The phone send capture shows the reply arriving.", ["Phone send"]),
            ("pair", 4, "Run 4 recorded the desktop send and a reconnect.", ["Desktop send", "Desktop reconnect"])):
        evidence = S.task_dir("atlas", slug) / "validation" / str(run) / "captures"
        evidence.mkdir(parents=True)
        for title in titles:
            (evidence / f"{title.lower().replace(' ', '-')}.gif").write_bytes(files[title])
        rows[key] = T.message("atlas", slug, "l2", text, capture_run=run)["id"]
    # The reconnect capture's attached copy starts missing, so its viewer walks the unavailable state until restored.
    folder = S.task_dir("atlas", slug) / "captures"
    reconnect = folder / f"{hashlib.sha256(files['Desktop reconnect']).hexdigest()}.gif"
    reconnect.unlink()
    S.regen_state_md("atlas")

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/captures":
                return self._json({"slug": slug, **rows})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/restore-capture":
                reconnect.write_bytes(files["Desktop reconnect"])
                return self._json({"ok": True})
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
