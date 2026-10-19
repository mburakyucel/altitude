"""Shown files are fixed copies of named worktree files, read beside the task conversation; showing approves nothing."""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import unittest
import zlib
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from tests.test_design_viewer import _RecordingHTTPServer
from altitude import config, door, server, state as S, tasks as T


def png(red=32, width=1, height=1):
    """A complete PNG, decoded by the same ordinary image path as real captures."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes([red, 80, 120]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# One-pixel JPEG fixture; no imaging library or external program is needed by the suite.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1R"
    "V19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2Nj"
    "Y2NjY2NjY2NjY2NjY2P/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCOiiig9s//2Q==")
GIF = b"GIF89a" + struct.pack("<HH", 3, 2) + b"\x00\x00\x00" + b",\x00\x00\x00\x00\x03\x00\x02\x00\x00\x02\x02D\x01\x00;"
WEBP = b"RIFF" + struct.pack("<I", 26) + b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00" * 4 \
    + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")

README = """# Lumen

<p align="center"><img src="docs/images/logo.png" alt="Lumen"></p>

Lumen turns noisy logs into a **timeline** you can [read](docs/guide.md).

![Tour](docs/images/tour.png "The tour")
![Badge](https://img.shields.io/badge/ci-passing-green)
![Missing](docs/images/missing.png)
![Outside](../outside.png)
![Vector](docs/images/diagram.svg)
![Logo again][logo]

```md
![In code](docs/images/code-only.png)
```

Inline `![span](docs/images/span.png)` code names no image, nor does an escaped \\![example](docs/images/escaped.png).

[logo]: <docs/images/logo.png>
"""


class TestShownFiles(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        task = T.new(self.project, "Rewrite the README", "Rewrite the README for a newcomer.",
                     hold_merge="Operator reviews the finished PR")
        self.slug = task["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        task.update(state="running", attempt=1, worktree=str(self.worktree))
        S.save_task(self.project, task)
        images = self.worktree / "docs" / "images"
        images.mkdir(parents=True)
        self.readme = self.worktree / "README.md"
        self.readme.write_text(README)
        (images / "logo.png").write_bytes(png(10, 4, 2))
        (images / "tour.png").write_bytes(png(20))
        (images / "diagram.svg").write_text("<svg onload='alert(1)'/>")
        (images / "code-only.png").write_bytes(png(30))
        (images / "span.png").write_bytes(png(40))
        (images / "escaped.png").write_bytes(png(45))
        (self.tmp / "outside.png").write_bytes(png(240))
        self.screen = self.worktree / "overview.png"
        self.screen.write_bytes(png(50))
        self.httpd = None

    def reply(self, *paths, text="The README draft is ready to read."):
        return T.message(self.project, self.slug, "l2", text, by="l2", expected_attempt=1,
                         show=self.sent(list(paths) or ["README.md", "overview.png"]))

    def sent(self, paths):
        """What the owner's `alt` reads in its worker and sends with a `--show`."""
        return T.shown_files(self.project, S.load_task(self.project, self.slug), paths)

    def publish(self, show=None, **kwargs):
        if S.load_task(self.project, self.slug)["state"] == "blocked":
            T.resume(self.project, self.slug)
        task = T.block(self.project, self.slug, "Build the README as shown?", actor="l2",
                       expected_attempt=1, updates={"waiting_on": "burak"},
                       show=self.sent(show or ["README.md"]), **kwargs)
        return task["questions"][-1]

    def saved(self, name):
        return S.task_dir(self.project, self.slug) / T.SHOWN_FOLDER / name

    def views(self):
        return {row["id"]: row for row in T.message_views(self.project, self.slug, [])}

    def api(self, identity, revision=None):
        return T.shown_api(self.project, self.slug, identity, revision)

    def request(self, method, path, body=None):
        if self.httpd is None:
            self.logs = []
            self.patch(server, "log", new=self.logs.append)
            server.Handler._seen_clients.clear()
            self.httpd = _RecordingHTTPServer(("127.0.0.1", 0), server.Handler)
            self.httpd.daemon_threads = True
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            self.addCleanup(self.stop_server)
        data = json.dumps(body).encode() if body is not None else b""
        host, port = self.httpd.server_address
        headers = (f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n"
                   f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n")
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.sendall(headers.encode() + data)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        raw, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        lines = raw.split(b"\r\n")
        returned = dict(line.decode().split(": ", 1) for line in lines[1:])
        return int(lines[0].split()[1]), {k.lower(): v for k, v in returned.items()}, payload

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))

    def test_a_reply_shows_a_document_with_its_own_images_after_the_named_ones(self):
        row = self.reply()
        shown = row["shown"]
        self.assertEqual(shown["document"], {
            "path": "README.md", "name": hashlib.sha256(README.encode()).hexdigest() + ".md", "size": len(README.encode()),
            "heading": "Lumen", "excerpt": "Lumen turns noisy logs into a timeline you can read."})
        self.assertEqual([(i["path"], i["embedded"], i["width"], i["height"]) for i in shown["images"]],
                         [("overview.png", False, 1, 1), ("docs/images/logo.png", True, 4, 2),
                          ("docs/images/tour.png", True, 1, 1)])
        self.assertEqual(shown["not_shown"], ["docs/images/missing.png", "../outside.png", "docs/images/diagram.svg"])
        for item in shown["images"]:
            self.assertEqual(self.saved(item["name"]).read_bytes(), (self.worktree / item["path"]).read_bytes())
        self.assertEqual(self.saved(shown["document"]["name"]).read_text(), README)
        self.assertEqual(len(list(self.saved("x").parent.iterdir())), 4, "nothing unnamed or remote is copied")
        conversation = next(r for r in T.task_messages(self.project, self.slug) if r["id"] == row["id"])
        self.assertNotIn(README, json.dumps(conversation), "the record holds names, never the document")
        view = self.views()[row["id"]]["shown"]
        self.assertEqual((view["url"], view["available"], view["newer_url"]),
                         (T.shown_url(self.project, self.slug, row["id"]), True, None))
        self.assertEqual(view["document"], {"name": "README.md", "path": "README.md", "heading": "Lumen",
                                            "excerpt": "Lumen turns noisy logs into a timeline you can read."})
        self.assertEqual([i["name"] for i in view["images"]], ["overview.png", "logo.png", "tour.png"])

    def test_references_resolve_from_the_documents_folder_or_the_worktree_root(self):
        guide = self.worktree / "docs" / "guide.md"
        guide.write_text("Setup\n=====\n\n![a](images/tour.png) ![b](../overview.png?raw=1#top) ![c](/docs/images/logo.png)\n"
                         "![d](images/%74our.png) ![e](./images/../../../outside.png) ![f](//cdn.example/x.png)\n"
                         "![g](data:image/png;base64,AAAA) ![h](file:///etc/passwd)\n")
        shown = self.reply("docs/guide.md")["shown"]
        self.assertEqual([i["path"] for i in shown["images"]], ["docs/images/tour.png", "overview.png", "docs/images/logo.png"])
        self.assertEqual(shown["not_shown"], ["./images/../../../outside.png"])
        self.assertEqual(shown["document"]["heading"], "Setup")
        for source, expected in (("a.png", "docs/a.png"), ("/a.png", "a.png"), ("../a.png", "a.png"),
                                 ("https://example.com/a.png", None), ("//host/a.png", None), ("mailto:x", None)):
            self.assertEqual(T.shown_reference(source, "docs/guide.md"), expected)
        for source in ("../../a.png", "/../a.png", "", "a\\b.png", "?x"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                T.shown_reference(source, "docs/guide.md")

    def test_images_only_and_text_documents_carry_their_size_and_excerpt(self):
        (self.worktree / "notes.txt").write_text("Release notes\n\nThe export page loads in half the time.\n")
        (self.worktree / "loop.gif").write_bytes(GIF)
        (self.worktree / "wide.webp").write_bytes(WEBP)
        (self.worktree / "photo.jpeg").write_bytes(JPEG)
        images = self.reply("loop.gif", "wide.webp", "photo.jpeg")["shown"]
        self.assertNotIn("document", images)
        self.assertEqual([(i["name"].rsplit(".", 1)[1], i["width"], i["height"]) for i in images["images"]],
                         [("gif", 3, 2), ("webp", 640, 360), ("jpg", 1, 1)])
        text = self.reply("notes.txt")["shown"]
        self.assertEqual((text["document"]["heading"], text["document"]["excerpt"], text["images"]),
                         ("", "Release notes", []))

    def test_a_later_publication_of_the_same_document_marks_the_earlier_one(self):
        first = self.reply()
        self.readme.write_text(README.replace("noisy", "loud"))
        second = self.reply("README.md")
        pictures = self.reply("overview.png")
        views = self.views()
        self.assertEqual(views[first["id"]]["shown"]["newer_url"], views[second["id"]]["shown"]["url"])
        self.assertIsNone(views[second["id"]]["shown"]["newer_url"])
        self.assertIsNone(views[pictures["id"]]["shown"]["newer_url"])
        question = self.publish()
        views = self.views()
        self.assertEqual(views[second["id"]]["shown"]["newer_url"], T.shown_url(self.project, self.slug, question["id"], 1))
        status, _, raw = self.request("GET", self.api(first["id"]))
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["newer_url"], T.shown_url(self.project, self.slug, question["id"], 1))

    def test_the_reader_serves_the_saved_copy_and_only_listed_images(self):
        row = self.reply()
        before = S.load_task(self.project, self.slug)
        status, headers, raw = self.request("GET", self.api(row["id"]))
        self.assertEqual(status, 200, raw)
        self.assertEqual(headers["cache-control"], "no-store")
        body = json.loads(raw)
        self.assertEqual((body["document"]["text"], body["document"]["markdown"], body["document"]["name"]),
                         (README, True, "README.md"))
        self.readme.write_text("# Changed after publication\n")
        self.screen.unlink()
        for image in body["images"]:
            with self.subTest(image=image["path"]):
                status, headers, data = self.request("GET", image["url"])
                self.assertEqual((status, data), (200, png(50) if image["path"] == "overview.png"
                                                  else (self.worktree / image["path"]).read_bytes()))
                self.assertEqual(headers["content-type"], "image/png")
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertEqual(headers["content-security-policy"], "default-src 'none'; sandbox")
        self.assertEqual(json.loads(self.request("GET", self.api(row["id"]))[2])["document"]["text"], README)
        self.assertEqual(S.load_task(self.project, self.slug), before, "viewing is a read")

    def test_unavailable_altered_and_foreign_requests_are_plain_404s(self):
        row = self.reply()
        asset = json.loads(self.request("GET", self.api(row["id"]))[2])["images"][0]["url"]
        other = T.new(self.project, "Another task", "Own evidence")
        other_project = "different-shown-project"
        self.register(other_project, path=self.tmp / "other")
        operator = T.message(self.project, self.slug, "burak", "Thanks.")
        document = asset.rsplit("/", 1)[0] + "/" + row["shown"]["document"]["name"]
        for path in (self.api(row["id"]).replace(self.project, "unregistered"),
                     self.api(row["id"]).replace(self.project, other_project),
                     self.api(row["id"]).replace(self.slug, other["slug"]),
                     self.api(operator["id"]), self.api(row["id"], 1), self.api(row["id"]) + "/zero",
                     asset.replace(self.slug, other["slug"]), document,
                     asset.rsplit("/", 1)[0] + "/../status.json", asset.rsplit("/", 1)[0] + "/..%2fstatus.json",
                     asset.rsplit("/", 1)[0] + "/" + "0" * 64 + ".png"):
            with self.subTest(path=path):
                status, _, raw = self.request("GET", path)
                self.assertEqual(status, 404, raw)
                self.assertNotIn(str(config.ROOT).encode(), raw)
                self.assertNotIn(b"Traceback", raw)
        self.saved(row["shown"]["images"][0]["name"]).write_bytes(png(250))
        self.assertEqual(self.request("GET", asset)[0], 404)
        self.saved(row["shown"]["images"][0]["name"]).write_bytes(png(250, 2))
        self.assertFalse(self.views()[row["id"]]["shown"]["available"], "a truncated or replaced file is known before opening")
        self.saved(row["shown"]["images"][0]["name"]).write_bytes(png(50))
        self.saved(row["shown"]["document"]["name"]).unlink()
        self.assertEqual(self.request("GET", self.api(row["id"]))[0], 404)
        self.assertFalse(self.views()[row["id"]]["shown"]["available"])

    def test_only_the_owner_shows_files_and_never_to_l3(self):
        for role, options in (("burak", {}), ("l3", {}), ("l2", {"to_l3": True})):
            with self.subTest(role=role, options=options), self.assertRaises(T.TransitionError):
                T.message(self.project, self.slug, role, "Look.", by=role, show=self.sent(["overview.png"]), **options)
        self.assertFalse(any(r.get("shown") for r in T.task_messages(self.project, self.slug)))

    def test_refused_files_add_no_reply(self):
        (self.worktree / "second.md").write_text("# Two\n")
        (self.worktree / "page.html").write_text("<script>fetch('/api/project')</script>")
        (self.worktree / "pretend.png").write_text("<script>fetch('/api/project')</script>")
        (self.worktree / "pretend.jpg").write_bytes(png())
        (self.worktree / "pretend.gif").write_bytes(png())
        (self.worktree / "empty.md").write_text("  \n")
        (self.worktree / "latin.md").write_bytes(b"\xff\xfe")
        (self.worktree / "large.md").write_text("x" * (T.SHOWN_DOCUMENT_LIMIT + 1))
        (self.worktree / "linked.png").symlink_to(self.screen)
        (self.worktree / "folder.png").mkdir()
        os.mkfifo(self.worktree / "pipe.png")
        (self.worktree / "linked").symlink_to(self.worktree / "docs", target_is_directory=True)
        image = png()
        (self.worktree / "huge.png").write_bytes(image[:-12] + b"x" * T.SHOWN_IMAGE_LIMIT + image[-12:])
        for paths in (["README.md", "second.md"], ["page.html"], ["pretend.png"], ["pretend.jpg"], ["pretend.gif"],
                      ["empty.md"], ["latin.md"], ["large.md"], ["linked.png"], ["folder.png"], ["pipe.png"],
                      ["linked/images/tour.png"], ["huge.png"], ["missing.png"], [str(self.screen)], ["../outside.png"],
                      ["docs/../overview.png"], ["./overview.png"], ["docs//images/tour.png"], ["docs\\images\\tour.png"],
                      [], "overview.png", [3]):
            with self.subTest(paths=paths), self.assertRaises(T.TransitionError):
                T.message(self.project, self.slug, "l2", "Look.", by="l2", expected_attempt=1, show=self.sent(paths))
        self.assertFalse(any(r.get("shown") for r in T.task_messages(self.project, self.slug)))

    def test_count_and_total_limits_include_the_documents_own_images(self):
        for index in range(T.SHOWN_IMAGE_COUNT):
            (self.worktree / f"screen-{index}.png").write_bytes(png(index))
        (self.worktree / "many.md").write_text("".join(f"![s](screen-{index}.png)\n" for index in range(T.SHOWN_IMAGE_COUNT)))
        self.assertEqual(len(self.reply("many.md")["shown"]["images"]), T.SHOWN_IMAGE_COUNT)
        with self.assertRaisesRegex(T.TransitionError, "at most 24 images"):
            self.reply("many.md", "overview.png")
        image = png()
        (self.worktree / "seven.png").write_bytes(image[:-12] + b"x" * (7 << 20) + image[-12:])
        (self.worktree / "heavy.md").write_text("".join(f"![s{index}](seven.png?{index})\n![t](copy-{index}.png)\n"
                                                        for index in range(5)))
        for index in range(5):
            (self.worktree / f"copy-{index}.png").write_bytes(image[:-12] + bytes([index]) * (7 << 20) + image[-12:])
        with self.assertRaisesRegex(T.TransitionError, "exceed 32 MiB"):
            self.reply("heavy.md")

    def test_racing_symlink_swap_cannot_copy_an_outside_file(self):
        original = os.open
        swapped = []

        def racing_open(path, flags, *args, **kwargs):
            if path == "overview.png" and not swapped:
                self.screen.unlink()
                self.screen.symlink_to(self.tmp / "outside.png")
                swapped.append(True)
            return original(path, flags, *args, **kwargs)

        with mock.patch.object(T.os, "open", side_effect=racing_open), self.assertRaises(T.TransitionError):
            self.reply("overview.png")
        self.assertEqual(swapped, [True])

    def test_a_rewritten_worktree_record_never_shows_another_checkout(self):
        other = add_worktree(self.repo, "another-owner")
        (other / "overview.png").write_bytes(png(99))
        task = S.load_task(self.project, self.slug)
        for path in (self.repo, other, self.worktree / "docs"):
            with self.subTest(path=path):
                task["worktree"] = str(path)
                S.save_task(self.project, task)
                row = self.reply("overview.png")
                self.assertEqual(self.saved(row["shown"]["images"][0]["name"]).read_bytes(), png(50))
        task["worktree"] = None
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "registered worktree"):
            self.reply("overview.png")

    def test_interrupted_publication_is_retryable_without_a_partial_saved_file(self):
        original = os.replace
        interrupted = []

        def interrupt(source, destination, *args, **kwargs):
            if str(destination).endswith(".png") and not interrupted:
                interrupted.append(True)
                raise OSError("fixture disk failure before image publication")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(T.os, "replace", side_effect=interrupt), self.assertRaises(OSError):
            self.reply("overview.png")
        self.assertEqual([p.name for p in self.saved("x").parent.iterdir()], [])
        self.assertFalse(any(r.get("shown") for r in T.task_messages(self.project, self.slug)))
        row = self.reply("overview.png")
        self.assertEqual(self.saved(row["shown"]["images"][0]["name"]).read_bytes(), png(50))

    def test_copies_survive_source_changes_deletion_and_task_archive(self):
        row = self.reply()
        self.readme.unlink()
        self.screen.unlink()
        T.reject(self.project, self.slug, "Review completed; keep the shown files")
        task, shown = T.shown_target(self.project, self.slug, row["id"])
        self.assertEqual(task["state"], "rejected")
        self.assertEqual(T.shown_file(self.project, self.slug, shown, shown["document"]["name"]).decode(), README)
        self.assertIn("archive", str(S.task_dir(self.project, self.slug)))

    def test_a_question_keeps_its_revision_for_identical_files_and_advances_for_changed_ones(self):
        first = self.publish(recommendation="Build as shown", recommendation_label="Build")
        T.resume(self.project, self.slug)
        parked = T.block(self.project, self.slug, first["detail"], actor="l2", expected_attempt=1)
        self.assertEqual(parked["questions"][-1], first)
        self.assertEqual(self.publish(), first)
        for change in (lambda: self.readme.write_text(README + "\nMore.\n"),
                       lambda: (self.worktree / "docs/images/tour.png").write_bytes(png(90))):
            change()
            current = self.publish()
            self.assertEqual((current["id"], current["revision"]), (first["id"], first["revision"] + 1))
            self.assertEqual(current["options"], first["options"])
            with self.assertRaises(T.TransitionError):
                T.accept_question(self.project, self.slug, first["id"], first["revision"])
            first = current
        views = T.question_views(self.project, self.slug)
        self.assertEqual([v["shown"]["newer_url"] is None for v in views], [False, False, True])
        self.assertEqual(views[-1]["shown"]["url"], T.shown_url(self.project, self.slug, first["id"], 3))
        self.assertEqual(self.request("GET", self.api(first["id"], 1))[0], 200)

    def test_a_question_with_files_rests_on_the_proposal_review_and_its_own_files(self):
        question = self.publish(recommendation="Build as shown", recommendation_label="Build")
        task = S.load_task(self.project, self.slug)
        self.assertIsNotNone(T.question_view(self.project, task, question)["review"])
        answer = T.message(self.project, self.slug, "burak", "Build it.", question_id=question["id"], revision=1)
        self.saved(question["shown"]["images"][0]["name"]).unlink()
        with self.assertRaisesRegex(T.TransitionError, "Preview unavailable"):
            T.accept_question(self.project, self.slug, question["id"], 1)
        with self.assertRaisesRegex(T.TransitionError, "Preview unavailable"):
            T.resolve_question(self.project, self.slug, question["id"], 1, answer["id"],
                               disposition="answered", reason="Build it.", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "open")
        current = self.publish()
        self.assertEqual(current["revision"], 1, "restoring the same bytes keeps the revision")
        T.resolve_question(self.project, self.slug, question["id"], 1, answer["id"],
                           disposition="answered", reason="Build it.", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "resolved")

    def group(self, proposal="proposal", merge="changes"):
        """The held PR's merge question beside a proposal go; a mark of None leaves that member unmarked."""
        items = [{"question": "Merge PR #7?", "review": merge, "recommended_key": "merge",
                  "options": [{"key": "merge", "label": "Approve merge", "text": "Merge PR #7."}]},
                 {"question": "Build the README as shown?", "review": proposal, "recommended_key": "build",
                  "options": [{"key": "build", "label": "Build as shown", "text": "Build the README as shown."}]}]
        return {"questions": [{k: v for k, v in item.items() if v is not None} for item in items]}

    def test_a_group_shows_files_only_on_its_proposal_and_leaves_the_merge_question_as_without_files(self):
        task = S.load_task(self.project, self.slug)
        task.update(prs=[7], delivery={"pr": 7})
        S.save_task(self.project, task)
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                       updates={"waiting_on": "burak"}, questions=self.group(), show=self.sent(["README.md"]))
        merge, proposal = task["questions"]
        self.assertEqual([q["status"] for q in task["questions"]], ["open", "open"])
        self.assertEqual(proposal["shown"]["document"]["path"], "README.md")
        self.assertNotIn("shown", merge)
        views = {v["id"]: v for v in T.question_views(self.project, self.slug)}
        self.assertEqual(views[proposal["id"]]["review"]["subject"], "proposal")
        self.assertIsNone(views[proposal["id"]]["pr"])
        self.assertEqual(views[merge["id"]]["review"]["subject"], "changes")
        self.assertEqual(views[merge["id"]]["pr"], 7)
        self.assertNotIn("shown", views[merge["id"]])
        T.accept_question(self.project, self.slug, merge["id"], 1)
        self.saved(proposal["shown"]["images"][0]["name"]).unlink()
        with self.assertRaisesRegex(T.TransitionError, "Preview unavailable"):
            T.accept_question(self.project, self.slug, proposal["id"], 1)
        # Showing the group's files again restores the proposal's copies at its revision; the merge question keeps its own.
        T.resume(self.project, self.slug)
        payload = self.group()
        for item, question in zip(payload["questions"], (merge, proposal)):
            item["id"] = question["id"]
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                       questions=payload, show=self.sent(["README.md"]))
        current = {q["id"]: q for q in task["questions"] if q["status"] == "open"}
        self.assertEqual(current[proposal["id"]]["revision"], 1)
        self.assertNotIn("shown", current[merge["id"]])
        T.accept_question(self.project, self.slug, proposal["id"], 1)

    def test_a_group_with_files_refuses_without_exactly_one_proposal_and_publishes_nothing(self):
        lone = {"questions": [{"question": "Merge PR #7?", "review": "none"}]}
        two = self.group(proposal="changes")
        for payload in (self.group(proposal=None, merge=None), self.group(proposal="none", merge="none"),
                        self.group(merge="proposal"), two, lone):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(T.TransitionError, 'mark exactly one question "review": "proposal"'):
                    T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                            updates={"waiting_on": "burak"}, questions=payload, show=self.sent(["README.md"]))
                task = S.load_task(self.project, self.slug)
                self.assertEqual(task["state"], "running")
                self.assertFalse(task.get("questions"))
                self.assertFalse(self.saved("").exists())
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                       updates={"waiting_on": "burak"}, questions=self.group(proposal=None, merge=None))
        self.assertEqual([q.get("shown") for q in task["questions"]], [None, None])

    def test_a_merge_question_shows_files_beside_its_review_and_pr_and_keeps_them_across_revisions(self):
        task = S.load_task(self.project, self.slug)
        task.update(prs=[7], delivery={"number": 7})
        S.save_task(self.project, task)
        payload = self.group(proposal="none")
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                       updates={"waiting_on": "burak"}, questions=payload, show=self.sent(["overview.png"]))
        merge, other = task["questions"]
        self.assertEqual([i["path"] for i in merge["shown"]["images"]], ["overview.png"])
        self.assertNotIn("shown", other)
        view = {v["id"]: v for v in T.question_views(self.project, self.slug)}[merge["id"]]
        self.assertEqual((view["review"]["subject"], view["pr"]), ("changes", 7))
        self.assertEqual([i["path"] for i in view["shown"]["images"]], ["overview.png"])
        status, _, data = self.request("GET", view["shown"]["images"][0]["url"])
        self.assertEqual((status, data), (200, png(50)))
        # Re-parking the reworded merge question without files keeps them; showing others replaces them.
        T.resume(self.project, self.slug)
        payload["questions"][0].update(id=merge["id"], question="Merge PR #7 now that the screenshots match?")
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1, questions=payload)
        revised = next(q for q in task["questions"] if q["id"] == merge["id"] and q["status"] == "open")
        self.assertEqual((revised["revision"], revised["shown"]), (2, merge["shown"]))
        T.resume(self.project, self.slug)
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1, questions=payload,
                       show=self.sent(["docs/images/tour.png"]))
        revised = next(q for q in task["questions"] if q["id"] == merge["id"] and q["status"] == "open")
        self.assertEqual((revised["revision"], [i["path"] for i in revised["shown"]["images"]]),
                         (3, ["docs/images/tour.png"]))

    def deliver(self, *changes):
        """Main with pictures and a text file, then the PR's head with `changes` applied; the task's delivery
        records PR #7 at that head."""
        site = self.repo / "site"
        site.mkdir()
        for name, data in (("hero.png", png(1, 4, 3)), ("gone.png", png(2)), ("moved.gif", GIF),
                           ("notes.md", b"notes\n")):
            (site / name).write_bytes(data)
        git("add", "site", cwd=self.repo)
        git("commit", "-q", "-m", "site", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("checkout", "-q", "-B", f"worktree-{self.slug}", "origin/main", cwd=self.worktree)
        for change in changes:
            change(self.worktree / "site")
        git("add", "-A", "site", cwd=self.worktree)
        git("commit", "-q", "-m", "pictures", cwd=self.worktree)
        head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        task = S.load_task(self.project, self.slug)
        task.update(state="reported", prs=[7], delivery={"number": 7, "head": head, "base": "main", "branch": "b"})
        S.save_task(self.project, task)
        return head

    def test_a_held_prs_card_shows_the_pictures_it_adds_replaces_and_removes_from_its_commit(self):
        def change(site):
            (site / "hero.png").write_bytes(png(9, 8, 6))
            (site / "gone.png").unlink()
            (site / "moved.gif").rename(site / "renamed.gif")
            (site / "new.webp").write_bytes(WEBP)
            (site / "diagram.svg").write_text("<svg/>")
            (site / "broken.png").write_bytes(b"not a picture")
            (site / "notes.md").write_text("more notes\n")
        self.deliver(change)
        (self.worktree / "site" / "hero.png").write_bytes(png(200, 2, 2))  # an uncommitted edit is not the PR
        card = T.review_row(self.project, S.load_task(self.project, self.slug))
        pictures = {p["path"]: p for p in card["pr_images"]["pictures"]}
        self.assertEqual(list(pictures), ["site/hero.png", "site/renamed.gif", "site/new.webp", "site/gone.png"],
                         "replaced pictures lead, then new, then removed ones")
        self.assertTrue(card["pr_images"]["available"])
        self.assertEqual(sorted(pictures), ["site/gone.png", "site/hero.png", "site/new.webp", "site/renamed.gif"])
        self.assertEqual(sorted(card["pr_images"]["not_shown"]), ["site/broken.png", "site/diagram.svg"])
        hero = pictures["site/hero.png"]
        self.assertEqual((hero["name"], hero["change"]), ("hero.png", "changed"))
        self.assertEqual([(hero[side]["width"], hero[side]["height"]) for side in ("before", "after")], [(4, 3), (8, 6)])
        self.assertEqual([(p["change"], bool(p["before"]), bool(p["after"])) for p in
                          (pictures["site/gone.png"], pictures["site/new.webp"], pictures["site/renamed.gif"])],
                         [("removed", True, False), ("added", False, True), ("changed", True, True)])
        for url, data in ((hero["before"]["url"], png(1, 4, 3)), (hero["after"]["url"], png(9, 8, 6)),
                          (pictures["site/gone.png"]["before"]["url"], png(2))):
            with self.subTest(url=url):
                status, headers, body = self.request("GET", url)
                self.assertEqual((status, body, headers["content-security-policy"]),
                                 (200, data, "default-src 'none'; sandbox"))
        base = hero["after"]["url"].rsplit("/", 1)[0]
        for path in (base.rsplit("/files", 1)[0], base + "/" + "0" * 64 + ".png",
                     hero["after"]["url"].replace("/pr/7/", "/pr/8/")):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        # The owner's merge question shows the same pictures; the record is read once per head.
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Merge?", actor="l2", expected_attempt=1, updates={"waiting_on": "burak"},
                questions={"questions": [self.group()["questions"][0]]})
        with mock.patch.object(T.subprocess, "run", side_effect=AssertionError("read again")):
            merge = T.question_views(self.project, self.slug)[-1]
            self.assertEqual(merge["pr_images"], card["pr_images"])
            self.assertIsNone(T.review_row(self.project, S.load_task(self.project, self.slug)))
        self.saved(hero["after"]["url"].rsplit("/", 1)[1]).unlink()
        self.assertFalse(T.question_views(self.project, self.slug)[-1]["pr_images"]["available"])
        # A released hold shows no merge card and no pictures.
        task = S.load_task(self.project, self.slug)
        task["hold_merge"] = None
        S.save_task(self.project, task)
        self.assertIsNone(T.question_views(self.project, self.slug)[-1]["pr_images"])
        self.assertEqual(self.request("GET", pictures["site/gone.png"]["before"]["url"])[0], 200,
                         "the current PR's pictures stay readable from the conversation")

    def test_a_text_only_or_unreadable_pr_shows_no_pictures(self):
        head = self.deliver(lambda site: (site / "notes.md").write_text("more\n"))
        self.assertIsNone(T.review_row(self.project, S.load_task(self.project, self.slug))["pr_images"])
        task = S.load_task(self.project, self.slug)
        task["delivery"]["head"] = "f" * 40  # a head the repository does not hold
        S.save_task(self.project, task)
        self.assertIsNone(T.review_row(self.project, task)["pr_images"])
        self.assertTrue(self.saved(f"pr-7-{'f' * 40}.json").exists(), "an unreadable head is not read again")
        self.assertTrue(self.saved(f"pr-7-{head}.json").exists())

    def test_moving_files_to_another_member_revises_the_earlier_one_without_them(self):
        first = self.publish(recommendation="Build as shown", recommendation_label="Build")
        T.resume(self.project, self.slug)
        payload = {"questions": [
            {"id": first["id"], "question": first["detail"], "review": "none"},
            {"question": "Publish the guide as shown?", "review": "proposal"}]}
        task = T.block(self.project, self.slug, "Two decisions.", actor="l2", expected_attempt=1,
                       questions=payload, show=self.sent(["overview.png"]))
        earlier, guide = [q for q in task["questions"] if q["status"] == "open"]
        self.assertEqual((earlier["id"], earlier["revision"]), (first["id"], 2))
        self.assertNotIn("shown", earlier)
        self.assertEqual([i["path"] for i in guide["shown"]["images"]], ["overview.png"])

    def test_an_altered_record_is_unavailable_and_cannot_be_accepted(self):
        question = self.publish(recommendation="Build as shown", recommendation_label="Build")
        task = S.load_task(self.project, self.slug)
        task["questions"][-1]["shown"]["document"]["path"] = "SECRET.md"
        S.save_task(self.project, task)
        self.assertEqual(self.request("GET", self.api(question["id"], 1))[0], 404)
        self.assertFalse(T.question_views(self.project, self.slug)[-1]["shown"]["available"])
        with self.assertRaisesRegex(T.TransitionError, "Preview unavailable"):
            T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertNotIn("response", S.load_task(self.project, self.slug)["questions"][-1])

    def test_a_preview_saved_before_shown_files_is_a_closed_card_and_cannot_be_accepted(self):
        task = T.block(self.project, self.slug, "Build the rows?", actor="l2", expected_attempt=1,
                       updates={"waiting_on": "burak"}, recommendation="Build", recommendation_label="Build")
        task["questions"][-1]["design"] = {"title": "Compact rows", "text": "Spec", "images": [], "id": "0" * 64}
        S.save_task(self.project, task)
        question = task["questions"][-1]
        view = T.question_views(self.project, self.slug)[-1]
        self.assertEqual(view["shown"], {"url": None, "available": False, "images": [], "newer_url": None,
                                         "document": {"name": "Compact rows", "path": "", "heading": "", "excerpt": ""}})
        self.assertNotIn("design", view)
        with self.assertRaisesRegex(T.TransitionError, "Preview unavailable"):
            T.accept_question(self.project, self.slug, question["id"], 1)
        self.assertNotIn("response", S.load_task(self.project, self.slug)["questions"][-1])
        self.assertEqual(self.request("GET", self.api(question["id"], 1))[0], 404)
        current = self.publish()
        self.assertEqual((current["id"], current["revision"]), (question["id"], 2))
        self.assertNotIn("design", current)

    def test_viewing_and_discussion_change_no_question_state_or_hold(self):
        question = self.publish(recommendation="Build as shown", recommendation_label="Build")
        before = S.load_task(self.project, self.slug)
        self.assertEqual(self.request("GET", self.api(question["id"], 1))[0], 200)
        with mock.patch.object(server, "request_task_resume"):
            status, _, raw = self.request("POST", "/api/l2/message", {
                "project": self.project, "slug": self.slug, "text": "Can the tour be shorter?",
                "question_id": question["id"], "revision": 1})
        self.assertEqual(status, 200, raw)
        current = S.load_task(self.project, self.slug)
        self.assertEqual(current["questions"][-1]["status"], "open")
        self.assertEqual(current["hold_merge"], before["hold_merge"])

    def owner_env(self, **overrides):
        return {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                "ALTITUDE_ATTEMPT": "1", **overrides}

    def test_owner_cli_shows_untracked_and_ignored_files_without_git_changes(self):
        with (self.worktree / ".gitignore").open("a") as stream:
            stream.write("overview.png\n")
        head, index = git("rev-parse", "HEAD", cwd=self.worktree), git("ls-files", "--stage", cwd=self.worktree)
        result = self.alt("task", "reply", "--show", "README.md", "--show", "overview.png", "The draft.",
                          env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["view_url"], T.shown_url(self.project, self.slug, row["id"]))
        self.assertEqual([i["path"] for i in row["shown"]["images"]][:1], ["overview.png"])
        result = self.alt("task", "block", self.slug, "--reason", "Build the README as shown?", "--for-operator",
                          "--show", "README.md", env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        question = S.load_task(self.project, self.slug)["questions"][-1]
        self.assertEqual(json.loads(result.stdout)["view_url"],
                         T.shown_url(self.project, self.slug, question["id"], question["revision"]))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree), head)
        self.assertEqual(git("ls-files", "--stage", cwd=self.worktree), index)

    def test_owner_cli_sends_megabytes_of_shown_images_and_hears_why_more_is_refused(self):
        # I-20261015-172004: ten screenshots, past altd's ordinary request bound, ended in a closed connection.
        def capture(size):
            header = png()[:33]
            data = os.urandom(size)
            return (header + struct.pack(">I", len(data)) + b"IDAT" + data + struct.pack(">I", zlib.crc32(b"IDAT" + data))
                    + png()[-12:])
        for number in range(3):
            (self.worktree / f"capture-{number}.png").write_bytes(capture(1 << 20))
        shown = [argument for number in range(3) for argument in ("--show", f"capture-{number}.png")]
        result = self.alt("task", "reply", *shown, "Three large captures.", env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["shown"]["images"]), 3)
        for key, value in self.owner_env().items():
            self.setenv(key, value)
        with mock.patch.object(door, "MAX_REQUEST", 1 << 20), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("nothing is sent")):
            with self.assertRaisesRegex(door.DoorError, "more than the 1 MiB altd takes from a task; send fewer or "
                                                        "smaller files"):
                door.call("alt", {"args": [], "stdin": "x" * (1 << 20)}, wait=0)

    def test_owner_cli_shows_a_groups_files_on_its_proposal(self):
        group = self.tmp / "questions.json"
        group.write_text(json.dumps(self.group()))
        result = self.alt("task", "block", self.slug, "--reason", "Two decisions.", "--for-operator",
                          "--questions-file", str(group), "--show", "README.md", env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        merge, proposal = S.load_task(self.project, self.slug)["questions"]
        self.assertEqual(json.loads(result.stdout)["view_url"],
                         T.shown_url(self.project, self.slug, proposal["id"], proposal["revision"]))
        self.assertEqual((proposal["review"], merge["review"]), ("proposal", "changes"))
        self.assertNotIn("shown", merge)

    def test_cli_refuses_nonowner_stale_attempt_fault_ambiguous_group_and_l3_reply(self):
        group = self.tmp / "questions.json"
        group.write_text(json.dumps({"questions": [{"question": "Which one?"}, {"question": "When?"}]}))
        base = ("task", "block", self.slug, "--reason", "Review?", "--show", "README.md")
        for env, extra in ((self.owner_env(ALTITUDE_TASK="another-owner"), ()),
                           (self.owner_env(ALTITUDE_ATTEMPT="2"), ()),
                           (self.owner_env(ALTITUDE_ACTOR="burak"), ()),
                           (self.owner_env(ALTITUDE_ACTOR="l3"), ()),
                           (self.owner_env(), ("--fault",)),
                           (self.owner_env(), ("--questions-file", str(group)))):
            with self.subTest(env=env, extra=extra):
                result = self.alt(*base, *extra, env=env)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(S.load_task(self.project, self.slug)["state"], "running")
                self.assertFalse(S.load_task(self.project, self.slug).get("questions"))
        result = self.alt("task", "reply", "--to", "l3", "--show", "README.md", "For you.", env=self.owner_env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(r.get("shown") for r in T.task_messages(self.project, self.slug)))

    def test_cli_project_override_cannot_publish_a_matching_slug_in_another_project(self):
        other_project = "separate-review-project"
        self.register(other_project, path=self.tmp / "separate")
        other = T.new(other_project, "Rewrite the README", "Unrelated")
        self.assertEqual(other["slug"], self.slug)
        other.update(state="running", attempt=1, worktree=str(self.worktree))
        S.save_task(other_project, other)
        result = self.alt("--project", other_project, "task", "block", self.slug, "--reason", "Review?",
                          "--show", "README.md", env=self.owner_env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("own project/task", result.stderr)
        self.assertEqual(S.load_task(other_project, self.slug), other)


if __name__ == "__main__":
    unittest.main()
