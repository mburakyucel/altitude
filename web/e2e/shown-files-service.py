"""Files an owner showed, published by `--show`'s own path from a fictional worktree and served over HTTP."""
import struct
import zlib

from service_support import configure, serve
from tests.support import add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, engines, server, state as S, tasks as T

QUESTION = "Build the README as proposed?"
HOLD = "Operator reviews the finished README before merge."
README = """# Lumen

Lumen turns noisy logs into a **timeline** you can read on any screen. [Install](#install) it, then open the
[guide](docs/guide.md) or the [project site](https://example.org/lumen).

![Phone timeline](docs/images/phone.png)

## Features

| Feature | Phone | Desktop |
| ------- | :---: | :-----: |
| Timeline | Yes | Yes |
| Search | Yes | Yes |

- [x] Group repeated lines
- [ ] Export a timeline

![Desktop timeline](docs/images/desktop.png)
![Build](https://example.org/badge.svg)
![Not copied](docs/images/missing.png)

## Install

```sh
lumen open ./logs
```
"""
PROPOSAL = """# Proposal: a shorter README

The README opens with what Lumen does, one picture of each screen, and the install step.

![Phone timeline](../docs/images/phone.png)
"""
HOSTILE = """# Hostile fixture

<script>window.shownExecuted = 1</script>
<img src="docs/images/phone.png" onerror="window.shownExecuted = 2" onload="window.shownExecuted = 3">
<p style="background:url(https://example.org/t.png)" onclick="window.shownExecuted = 4">Styled paragraph</p>
<iframe src="https://example.org/frame"></iframe>
<form action="https://example.org/post"><input name="q"><button>Go</button></form>
<picture><source srcset="https://example.org/s.png"><img src="https://example.org/p.png" alt="Remote picture"></picture>

[Run](javascript:window.shownExecuted=5) · ![Inline](data:image/png;base64,AAAA) · ![Tracker](https://example.org/pixel.png)
"""


def picture(width: int, height: int, accent: tuple[int, int, int]) -> bytes:
    """A fictional timeline illustration: a header band and staggered bars on a light page."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    page, ink, band = (248, 250, 252), (203, 213, 225), (30, 41, 59)
    rows = []
    for y in range(height):
        row = bytearray(b"\0")
        lane, offset = divmod(y - height // 6, max(height // 14, 8))
        for x in range(width):
            colour = page
            if y < height // 9:
                colour = band
            elif lane >= 0 and offset < max(height // 28, 4) and x > width // 12:
                start = width // 12 + (lane * 37) % (width // 3)
                end = start + width // 4 + (lane * 53) % (width // 3)
                colour = accent if start <= x < end else ink if x < width // 12 + 6 else page
            row += bytes(colour)
        rows.append(bytes(row))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def main():
    configure()
    fake = FakeL2()
    for name in ("start_l2", "resume_l2", "worker", "stop_l2_worker", "remove_l2_worker"):
        setattr(engines, name, getattr(fake, name))
    repo = make_repo(config.PROJECT_ROOTS[0] / "atlas")
    with config.add_project("atlas", path=repo):
        pass
    slug = T.new("atlas", "Rewrite the README", "Rewrite the fictional README for a newcomer.", hold_merge=HOLD)["slug"]
    worktree = add_worktree(repo, slug)
    T.dispatch("atlas", slug, attempt=1, session_id="fixture-shown", agent_id="fixture-shown",
               worktree=str(worktree), branch=f"worktree-{slug}", l2_engine=config.ENGINES[0])
    images = worktree / "docs" / "images"
    images.mkdir(parents=True)
    blue, green, amber, rose, violet = (37, 99, 235), (22, 163, 74), (217, 119, 6), (225, 29, 72), (124, 58, 237)
    (images / "phone.png").write_bytes(picture(390, 720, blue))
    (images / "desktop.png").write_bytes(picture(960, 540, green))
    (worktree / "overview.png").write_bytes(picture(960, 400, violet))
    screens = ["timeline", "search", "filters", "export", "settings"]
    for name, accent in zip(screens, (blue, green, amber, rose, violet)):
        (images / f"{name}.png").write_bytes(picture(390, 720, accent))
    readme = worktree / "README.md"
    readme.write_text(README)
    (worktree / "docs" / "hostile.md").write_text(HOSTILE)
    proposal = worktree / "design" / "PROPOSAL.md"
    proposal.parent.mkdir()
    proposal.write_text(PROPOSAL)

    def reply(text, show):
        return T.message("atlas", slug, "l2", text, by="l2", expected_attempt=1, show=show)["id"]

    def present():
        if S.load_task("atlas", slug)["state"] == "blocked":
            T.resume("atlas", slug)
        question = T.block("atlas", slug, QUESTION, actor="l2", expected_attempt=1, updates={"waiting_on": "burak"},
                           recommendation="Build the README as proposed.", recommendation_label="Build as proposed",
                           show=["design/PROPOSAL.md"])["questions"][-1]
        return {"id": question["id"], "revision": question["revision"]}

    ids = {"readme": reply("The README draft is ready to read.", ["README.md", "overview.png"]),
           "screens": reply("These are the phone screens the README links to.",
                            [f"docs/images/{name}.png" for name in screens]),
           "hostile": reply("This fixture checks that nothing in a document runs.", ["docs/hostile.md"])}
    question = present()
    # A preview saved before shown files existed: its question names it and cannot be opened.
    legacy = T.new("atlas", "Compact rows", "Make the fictional list rows compact.")["slug"]
    task = T.block("atlas", legacy, "Build the compact rows?", actor="l2", updates={"waiting_on": "burak"},
                   recommendation="Build the compact rows.", recommendation_label="Build")
    task["questions"][-1]["design"] = {"title": "Compact rows", "text": "Spec", "images": [], "id": "0" * 64}
    S.save_task("atlas", task)
    # The file reader opens task files by path; the hostile document also sits in the task's folder.
    hostile = S.task_dir("atlas", slug) / "hostile.md"
    hostile.write_text(HOSTILE)

    def view(identity):
        return next(row for row in T.message_views("atlas", slug, []) if row["id"] == identity)["shown"]

    class Handler(server.Handler):
        def do_GET(self):
            if self.path == "/fixture/shown":
                legacy_question = S.load_task("atlas", legacy)["questions"][-1]
                return self._json({"slug": slug, **ids, "question": question, "hostile_path": str(hostile),
                                   "legacy": {"slug": legacy, "id": legacy_question["id"]}})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/republish":
                readme.write_text(README.replace("noisy logs", "long, noisy logs"))
                ids["newer"] = reply("The README now names long logs.", ["README.md"])
                return self._json({"newer": ids["newer"]})
            if self.path == "/fixture/revise":
                proposal.write_text(PROPOSAL.replace("one picture", "a picture"))
                question.update(present())
                return self._json(question)
            if self.path == "/fixture/damage":
                saved = S.task_dir("atlas", slug) / T.SHOWN_FOLDER
                for image in view(ids["readme"])["images"]:
                    (saved / image["url"].rsplit("/", 1)[1]).write_bytes(b"Changed after publication")
                return self._json({"ok": True})
            if self.path == "/fixture/checkpoint":
                saved = S.load_task("atlas", slug)["questions"][-1]
                response = saved["response"]
                T.resolve_question("atlas", slug, saved["id"], saved["revision"], response["message_id"],
                                   disposition="answered", reason=response["text"], expected_attempt=1)
                return self._json({"ok": True})
            if self.path == "/fixture/park":
                # The owner reads a follow-up and parks its question again.
                with server._bg_guard:
                    resumed = server._bg.get(f"resume:atlas:{slug}")
                if resumed:
                    resumed.join(10)
                    assert not resumed.is_alive(), "The fixture resume did not finish"
                question.update(present())
                return self._json(question)
            return super().do_POST()

    serve(Handler)


if __name__ == "__main__":
    main()
