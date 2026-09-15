"""Pending designs are fixed, task-owned evidence, with the existing question as decision authority."""
import base64
import copy
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
from altitude import config, server, state as S, tasks as T


def png(red=32):
    """A complete one-pixel PNG, decoded by the same ordinary image path as real captures."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 80, 120]))) + chunk(b"IEND", b""))


# One-pixel JPEG fixture; no imaging library or external program is needed by the suite.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwc"
    "KDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQF"
    "BgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRol"
    "JicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKz"
    "tLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQF"
    "BgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcY"
    "GRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmq"
    "srO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDhaKKK9k8c/9k=")


class TestTaskDesign(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        task = T.new(self.project, "Mobile proposal", "Review the compact conversation.",
                     hold_merge="Operator reviews the finished PR")
        self.slug = task["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        task.update(state="running", attempt=1, worktree=str(self.worktree))
        S.save_task(self.project, task)
        self.boards = self.worktree / "design" / "wireframes"
        self.boards.mkdir(parents=True)
        self.proposal = self.boards / "proposal.md"
        self.proposal.write_text("# Compact conversation\nKeep feedback in the existing task.\n")
        self.screen = self.boards / "phone.png"
        self.screen.write_bytes(png())
        self.selection = {"title": "Compact conversation", "proposal": "design/wireframes/proposal.md",
                          "images": [{"title": "Phone — typing", "path": "design/wireframes/phone.png"}]}
        self.httpd = None

    def publish(self, selection=None, **kwargs):
        if S.load_task(self.project, self.slug)["state"] == "blocked":
            T.resume(self.project, self.slug)
        task = T.block(self.project, self.slug, "May I implement this proposal?", actor="l2",
                       expected_attempt=1, updates={"waiting_on": "burak"},
                       design=self.selection if selection is None else selection, **kwargs)
        return task["questions"][-1]

    def capture_path(self, question):
        return S.task_dir(self.project, self.slug) / "designs" / question["design"]["images"][0]["name"]

    def api(self, question):
        return f"/api/design/{self.project}/{self.slug}/{question['id']}/{question['revision']}"

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

    def test_owner_publishes_a_fixed_selection_linked_to_the_question(self):
        (self.boards / "private.txt").write_text("Unselected private notes")
        question = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        design = question["design"]
        self.assertEqual(design["text"], self.proposal.read_text())
        self.assertEqual(design["images"], [{"title": "Phone — typing", "size": len(png()),
                                            "name": hashlib.sha256(png()).hexdigest() + ".png"}])
        self.assertEqual(self.capture_path(question).read_bytes(), png())
        self.assertEqual(len(list(self.capture_path(question).parent.iterdir())), 1)
        views = T.question_views(self.project, self.slug)
        self.assertIn(T.design_url(self.project, self.slug, question), json.dumps(views))
        self.assertEqual(T.task_design(self.project, self.slug, question["id"], 1)[1], question)

    def test_repark_and_identical_capture_preserve_question_revision_and_choices(self):
        first = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        T.resume(self.project, self.slug)
        parked = T.block(self.project, self.slug, first["detail"], actor="l2", expected_attempt=1)
        self.assertEqual(parked["questions"][-1], first)
        repeated = self.publish()
        self.assertEqual((repeated["id"], repeated["revision"], repeated["design"], repeated["options"]),
                         (first["id"], 1, first["design"], first["options"]))

    def test_existing_waiting_owner_attaches_preview_to_its_same_unanswered_question(self):
        task = T.block(self.project, self.slug, "May I implement this proposal?", actor="l2",
                       expected_attempt=1, updates={"waiting_on": "burak"},
                       recommendation="Implement the shown layout", recommendation_label="Implement")
        waiting = task["questions"][-1]
        self.assertNotIn("design", waiting)
        presented = self.publish()
        self.assertEqual((presented["id"], presented["revision"], presented["status"]), (waiting["id"], 2, "open"))
        self.assertEqual(presented["options"], waiting["options"])
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], task["hold_merge"])
        status, _, raw = self.request("GET", self.api(presented))
        self.assertEqual(status, 200, raw)
        with self.assertRaises(T.TransitionError):
            T.accept_question(self.project, self.slug, waiting["id"], 1)

    def test_changed_image_text_title_and_caption_each_version_the_same_question(self):
        previous = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        for change in (lambda: self.screen.write_bytes(png(90)),
                       lambda: self.proposal.write_text("Changed explanatory scope."),
                       lambda: self.selection.update(title="Refined conversation"),
                       lambda: self.selection["images"][0].update(title="Phone — listening")):
            with self.subTest(revision=previous["revision"] + 1):
                change()
                current = self.publish()
                self.assertEqual((current["id"], current["revision"]), (previous["id"], previous["revision"] + 1))
                self.assertNotEqual(current["design"]["id"], previous["design"]["id"])
                self.assertEqual(current["options"], previous["options"])
                with self.assertRaises(T.TransitionError):
                    T.accept_question(self.project, self.slug, previous["id"], previous["revision"])
                saved = T.task_design(self.project, self.slug, previous["id"], previous["revision"])[1]
                self.assertEqual(saved["design"], previous["design"])
                previous = current

    def test_copies_survive_source_changes_deletion_and_task_archive(self):
        question = self.publish()
        self.screen.write_bytes(png(90))
        self.proposal.unlink()
        T.reject(self.project, self.slug, "Review completed; retain the proposal evidence")
        task, saved = T.task_design(self.project, self.slug, question["id"], 1)
        self.assertEqual(task["state"], "rejected")
        self.assertIn("archive", str(self.capture_path(saved)))
        self.assertEqual(T.design_image(self.project, self.slug, saved["design"],
                                       saved["design"]["images"][0]["name"]), png())

    def test_failed_recapture_never_replaces_the_published_question(self):
        first = self.publish()
        self.screen.unlink()
        with self.assertRaises(T.TransitionError):
            self.publish()
        saved = S.load_task(self.project, self.slug)
        self.assertEqual(saved["questions"][-1], first)
        self.assertEqual(self.capture_path(first).read_bytes(), png())

    def test_interrupted_publication_is_retryable_without_a_partial_saved_image(self):
        first = self.publish()
        self.screen.write_bytes(png(90))
        original = os.replace
        interrupted = []

        def interrupt_image(source, destination, *args, **kwargs):
            if str(destination).endswith(".png") and not interrupted:
                interrupted.append(True)
                raise OSError("fixture disk failure before image publication")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(T.os, "replace", side_effect=interrupt_image), self.assertRaises(OSError):
            self.publish()
        self.assertEqual(interrupted, [True])
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1], first)
        self.assertEqual(self.capture_path(first).read_bytes(), png())
        self.assertEqual(list(self.capture_path(first).parent.iterdir()), [self.capture_path(first)])
        retried = self.publish()
        self.assertEqual((retried["id"], retried["revision"]), (first["id"], 2))
        self.assertEqual(self.capture_path(retried).read_bytes(), png(90))

    def test_owner_can_restore_altered_capture_without_changing_the_proposal_revision(self):
        first = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        for damaged in (b"interrupted or altered evidence", b"x" * (T.DESIGN_IMAGE_LIMIT + 1)):
            with self.subTest(size=len(damaged)):
                self.capture_path(first).write_bytes(damaged)
                status, _, _ = self.request("GET", self.api(first))
                self.assertEqual(status, 404)
                restored = self.publish()
                self.assertEqual((restored["id"], restored["revision"], restored["design"], restored["options"]),
                                 (first["id"], 1, first["design"], first["options"]))
                status, _, raw = self.request("GET", self.api(first))
                self.assertEqual(status, 200, raw)
                self.assertEqual(self.capture_path(first).read_bytes(), png())

    def test_read_followup_and_explicit_acceptance_use_existing_decision_rules(self):
        question = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        before = S.load_task(self.project, self.slug)
        status, headers, raw = self.request("GET", self.api(question))
        self.assertEqual(status, 200, raw)
        preview = json.loads(raw)
        self.assertEqual((preview["title"], preview["revision"], preview["text"], preview["superseded"]),
                         (question["design"]["title"], 1, question["design"]["text"], False))
        self.assertIn(f"?question={question['id']}&revision=1", preview["question_url"])
        self.assertIsNone(preview["current_question_url"], "a current proposal needs no newer-version link")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(S.load_task(self.project, self.slug), before, "viewing is a read")
        with mock.patch.object(server, "request_task_resume"):
            status, _, raw = self.request("POST", "/api/l2/message", {
                "project": self.project, "slug": self.slug, "text": "Can the transcript be shorter?",
                "question_id": question["id"], "revision": 1})
        self.assertEqual(status, 200, raw)
        current = S.load_task(self.project, self.slug)
        self.assertEqual(current["questions"][-1]["status"], "open")
        self.assertEqual(current["hold_merge"], before["hold_merge"])
        with mock.patch.object(server, "request_task_resume"):
            status, _, raw = self.request("POST", "/api/decide", {
                "project": self.project, "slug": self.slug, "question_id": question["id"], "revision": 1})
        self.assertEqual(status, 200, raw)
        current = S.load_task(self.project, self.slug)
        self.assertEqual(current["questions"][-1]["status"], "open")
        response = current["questions"][-1]["response"]
        T.resolve_question(self.project, self.slug, question["id"], 1, response["message_id"],
                           disposition="answered", reason="Implement the captured layout", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "resolved")
        self.assertEqual(current["hold_merge"], before["hold_merge"])

    def test_old_url_serves_its_own_version_and_points_back_to_current_question(self):
        first = self.publish()
        self.screen.write_bytes(png(90))
        current = self.publish()
        status, _, raw = self.request("GET", self.api(first))
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertTrue(body["superseded"])
        self.assertIn(f"?question={current['id']}&revision=2", body["current_question_url"])
        status, headers, raw = self.request("GET", body["images"][0]["url"])
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, png())
        self.assertEqual(headers["content-type"], "image/png")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", headers["content-security-policy"])

    def test_jpeg_capture_is_served_as_a_bounded_raster(self):
        (self.boards / "desktop.jpeg").write_bytes(JPEG)
        self.selection["images"] = [{"title": "Desktop", "path": "design/wireframes/desktop.jpeg"}]
        question = self.publish()
        self.assertEqual(question["design"]["images"][0]["name"], hashlib.sha256(JPEG).hexdigest() + ".jpg")
        status, _, raw = self.request("GET", self.api(question))
        self.assertEqual(status, 200, raw)
        status, headers, raw = self.request("GET", json.loads(raw)["images"][0]["url"])
        self.assertEqual(status, 200, raw)
        self.assertEqual(raw, JPEG)
        self.assertEqual(headers["content-type"], "image/jpeg")
        self.assertEqual(headers["content-length"], str(len(JPEG)))
        self.assertEqual(headers["x-content-type-options"], "nosniff")

    def test_capture_refuses_active_content_and_disguised_image_files(self):
        html = b"<!doctype html><script>fetch('/api/project')</script>"
        for name, content in (("active.html", html), ("active.svg", b"<svg onload='alert(1)'/>"),
                              ("pretend.png", html), ("pretend.jpg", png()), ("pretend.png", b"\xff\xd8\xff\xff\xd9")):
            with self.subTest(name=name, content=content[:10]):
                (self.boards / name).write_bytes(content)
                selection = copy.deepcopy(self.selection)
                selection["images"][0]["path"] = "design/wireframes/" + name
                with self.assertRaises(T.TransitionError):
                    self.publish(selection)
        self.assertFalse(S.load_task(self.project, self.slug).get("questions"))

    def test_capture_refuses_paths_outside_selected_worktree_subtree(self):
        outside = self.tmp / "outside.png"
        outside.write_bytes(png(240))
        for path in (str(outside), "../outside.png", "design/wireframes/../../../outside.png",
                     "design/wireframes/../phone.png", "design/wireframes//phone.png",
                     "design/wireframes/..\\phone.png", "README.md", "web/design/phone.png"):
            with self.subTest(path=path):
                selection = copy.deepcopy(self.selection)
                selection["images"][0]["path"] = path
                with self.assertRaises(T.TransitionError):
                    self.publish(selection)

    def test_capture_refuses_symlinks_directories_and_fifos_without_hanging(self):
        (self.boards / "linked.png").symlink_to(self.screen)
        (self.boards / "directory.png").mkdir()
        os.mkfifo(self.boards / "pipe.png")
        (self.boards / "linked").symlink_to(self.boards, target_is_directory=True)
        for path in ("linked.png", "directory.png", "pipe.png", "linked/phone.png"):
            with self.subTest(path=path):
                selection = copy.deepcopy(self.selection)
                selection["images"][0]["path"] = "design/wireframes/" + path
                with self.assertRaises(T.TransitionError):
                    self.publish(selection)

    def test_racing_symlink_swap_cannot_capture_an_outside_file(self):
        outside = self.tmp / "outside.png"
        outside.write_bytes(png(240))
        original = os.open
        swapped = []

        def racing_open(path, flags, *args, **kwargs):
            if path == "phone.png" and not swapped:
                self.screen.unlink()
                self.screen.symlink_to(outside)
                swapped.append(True)
            return original(path, flags, *args, **kwargs)

        with mock.patch.object(T.os, "open", side_effect=racing_open), self.assertRaises(T.TransitionError):
            self.publish()
        self.assertEqual(swapped, [True])
        self.assertFalse(S.load_task(self.project, self.slug).get("questions"))

    def test_registered_worktree_cannot_be_replaced_with_another_tasks_checkout(self):
        other = add_worktree(self.repo, "another-owner")
        task = S.load_task(self.project, self.slug)
        for path in (self.repo, other, self.boards):
            with self.subTest(path=path):
                task["worktree"] = str(path)
                S.save_task(self.project, task)
                with self.assertRaises(T.TransitionError):
                    self.publish()

    def test_invalid_manifest_text_and_size_limits_fail_before_publication(self):
        for patch in ({"images": []}, {"images": self.selection["images"] * 13}, {"title": ""},
                      {"title": "x" * 161}, {"proposal": "design/wireframes/active.html"}, {"extra": "secret"}):
            with self.subTest(patch=patch), self.assertRaises(T.TransitionError):
                self.publish({**self.selection, **patch})
        for text in (b"", b"\xff", b"x" * ((64 << 10) + 1)):
            with self.subTest(length=len(text)):
                self.proposal.write_bytes(text)
                with self.assertRaises(T.TransitionError):
                    self.publish()
        self.proposal.write_text("Proposal")
        self.screen.write_bytes(png() + b"x" * (8 << 20))
        with self.assertRaises(T.TransitionError):
            self.publish()
        # Valid signatures with a bounded 7 MiB payload exercise the aggregate limit,
        # independently of the per-image limit and without allocating hundreds of MiB.
        image = png()
        self.screen.write_bytes(image[:-12] + b"x" * (7 << 20) + image[-12:])
        with self.assertRaises(T.TransitionError):
            self.publish({**self.selection, "images": self.selection["images"] * 5})
        self.assertFalse(S.load_task(self.project, self.slug).get("questions"))

    def test_unavailable_corrupt_and_foreign_http_requests_are_plain_404s(self):
        question = self.publish()
        status, _, raw = self.request("GET", self.api(question))
        self.assertEqual(status, 200, raw)
        asset = json.loads(raw)["images"][0]["url"]
        other = T.new(self.project, "Another proposal", "Own evidence")
        other_project = "different-design-project"
        self.register(other_project, path=self.tmp / "other")
        requests = [
            self.api(question).replace(self.project, "unregistered"),
            self.api(question).replace(self.project, other_project),
            self.api(question).replace(self.slug, other["slug"]),
            self.api(question).rsplit("/", 1)[0] + "/999",
            self.api(question).rsplit("/", 1)[0] + "/zero",
            asset.replace(self.project, "unregistered"),
            asset.replace(self.slug, other["slug"]),
            asset.rsplit("/", 1)[0] + "/private.txt",
            asset.rsplit("/", 1)[0] + "/../status.json",
            asset.rsplit("/", 1)[0] + "/..%2fstatus.json",
            asset.rsplit("/", 1)[0] + "/" + "0" * 64 + ".png",
        ]
        for path in requests:
            with self.subTest(path=path):
                status, headers, raw = self.request("GET", path)
                self.assertEqual(status, 404, raw)
                self.assertNotIn(str(config.ROOT).encode(), raw)
                self.assertNotIn(b"Traceback", raw)
        self.capture_path(question).write_bytes(png(250))
        for path in (self.api(question), asset):
            with self.subTest(path=path):
                status, _, raw = self.request("GET", path)
                self.assertEqual(status, 404, raw)

    def test_missing_or_symlinked_capture_cannot_be_read_or_accepted(self):
        question = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        capture = self.capture_path(question)
        capture.unlink()
        response = T.accept_question(self.project, self.slug, question["id"], 1,
                                     text="The preview is unavailable; please restore it.")["response"]
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    capture.symlink_to(self.screen)
                with self.assertRaises((ValueError, OSError, T.TransitionError)):
                    T.design_image(self.project, self.slug, question["design"], capture.name)
                with self.assertRaises(T.TransitionError):
                    T.resolve_question(self.project, self.slug, question["id"], 1, response["message_id"],
                                       disposition="answered", reason="Accept the design", expected_attempt=1)
                self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "open")

    def test_changed_saved_proposal_is_unavailable_and_cannot_be_accepted(self):
        question = self.publish(recommendation="Implement the shown layout", recommendation_label="Implement")
        task = S.load_task(self.project, self.slug)
        task["questions"][-1]["design"]["text"] = "Substituted scope"
        S.save_task(self.project, task)
        status, _, raw = self.request("GET", self.api(question))
        self.assertEqual(status, 404, raw)
        response = T.accept_question(self.project, self.slug, question["id"], 1)["response"]
        with self.assertRaises(T.TransitionError):
            T.resolve_question(self.project, self.slug, question["id"], 1, response["message_id"],
                               disposition="answered", reason="Accept the design", expected_attempt=1)

    def test_conversational_answer_cannot_accept_changed_or_unavailable_evidence(self):
        first = self.publish()
        answer = T.message(self.project, self.slug, "burak", "Use the captured layout.",
                           question_id=first["id"], revision=1)
        self.capture_path(first).unlink()
        with self.assertRaises(T.TransitionError):
            T.resolve_question(self.project, self.slug, first["id"], 1, answer["id"],
                               disposition="answered", reason="Use the captured layout.", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "open")
        self.screen.write_bytes(png(90))
        current = self.publish()
        with self.assertRaises(T.TransitionError):
            T.resolve_question(self.project, self.slug, current["id"], 2, answer["id"],
                               disposition="answered", reason="Use the captured layout.", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "open")

    def test_wording_only_republication_is_reconciled_by_owner_without_reapproval(self):
        task = S.load_task(self.project, self.slug)
        task["session_id"] = "original-owner-session"
        S.save_task(self.project, task)
        first = self.publish()
        answer = T.message(self.project, self.slug, "burak", "Implement this scope.",
                           question_id=first["id"], revision=1)
        self.proposal.write_text(self.proposal.read_text() + "\nThe same feedback stays in this task.\n")
        current = self.publish()
        before = S.load_task(self.project, self.slug)
        with self.assertRaisesRegex(T.TransitionError, "different question revision"):
            T.resolve_question(self.project, self.slug, current["id"], 2, answer["id"],
                               disposition="answered", reason="Same scope", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug), before)
        reason = f"Redundant wording-only checkpoint; unchanged scope approved in task message {answer['id']}."
        T.resolve_question(self.project, self.slug, current["id"], 2, None,
                           disposition="withdrawn", reason=reason, expected_attempt=1)
        saved = S.load_task(self.project, self.slug)
        receipt = saved["questions"][-1]["resolution"]
        self.assertEqual(receipt["disposition"], "withdrawn")
        self.assertIsNone(receipt["message_id"])
        self.assertIn(answer["id"], receipt["text"])
        for key in ("hold_merge", "attempt", "session_id", "state"):
            self.assertEqual(saved.get(key), before.get(key))
        self.assertIn(answer["id"], {row["id"] for row in T.task_messages(self.project, self.slug)})
        # Genuine revised scope remains a new decision; withdrawal supplies no approval.
        T.resume(self.project, self.slug)
        self.proposal.write_text("Add a new external publication capability.\n")
        revised = self.publish()
        with self.assertRaises(T.TransitionError):
            T.resolve_question(self.project, self.slug, revised["id"], revised["revision"], answer["id"],
                               disposition="answered", reason="Use old approval", expected_attempt=1)
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["status"], "open")

    def test_current_owner_cli_serves_untracked_and_ignored_captures_without_git_changes(self):
        captures = self.boards / "captures"
        captures.mkdir()
        (captures / "desktop.png").write_bytes(png(90))
        with (self.worktree / ".gitignore").open("a") as stream:
            stream.write("design/wireframes/captures/\n")
        self.selection["images"].append({"title": "Desktop", "path": "design/wireframes/captures/desktop.png"})
        self.assertEqual(git("check-ignore", self.selection["images"][1]["path"], cwd=self.worktree).strip(),
                         self.selection["images"][1]["path"])
        self.assertIn("design/wireframes/phone.png", git("ls-files", "--others", "--exclude-standard", cwd=self.worktree))
        self.assertEqual(git("ls-files", "--", "design/wireframes", cwd=self.worktree), "")
        head, index = git("rev-parse", "HEAD", cwd=self.worktree), git("ls-files", "--stage", cwd=self.worktree)
        manifest = self.tmp / "selection.json"
        manifest.write_text(json.dumps(self.selection))
        result = self.alt("task", "block", self.slug, "--reason", "Review this proposal?", "--for-burak",
                          "--design-file", str(manifest), env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        question = S.load_task(self.project, self.slug)["questions"][-1]
        self.assertEqual(json.loads(result.stdout)["design_url"], T.design_url(self.project, self.slug, question))
        status, _, raw = self.request("GET", self.api(question))
        self.assertEqual(status, 200, raw)
        for image, expected in zip(json.loads(raw)["images"], (png(), png(90)), strict=True):
            status, _, data = self.request("GET", image["url"])
            self.assertEqual((status, data), (200, expected))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree), head)
        self.assertEqual(git("ls-files", "--stage", cwd=self.worktree), index)

    def owner_env(self, **overrides):
        return {"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                "ALTITUDE_ATTEMPT": "1", **overrides}

    def test_cli_refuses_nonowner_stale_attempt_fault_and_ambiguous_design_group(self):
        manifest = self.tmp / "selection.json"
        manifest.write_text(json.dumps(self.selection))
        group = self.tmp / "questions.json"
        group.write_text(json.dumps({"questions": [{"question": "Which one?"}, {"question": "When?"}]}))
        base = ("task", "block", self.slug, "--reason", "Review?", "--design-file", str(manifest))
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

    def test_reasked_design_cli_preserves_independent_choice_and_old_capture(self):
        first = self.publish(recommendation="Implement the shown layout")
        T.resume(self.project, self.slug)
        task = T.block(self.project, self.slug, "Which rollback window?", actor="l2", expected_attempt=1,
                       questions={"questions": [{"question": "Which rollback window?"}]})
        independent = task["questions"][-1]
        T.resume(self.project, self.slug)
        T.resolve_question(self.project, self.slug, first["id"], 1, None, disposition="withdrawn",
                           reason="Assess the layout guidance first.", expected_attempt=1)
        self.screen.write_bytes(png(90))
        manifest, questions = self.tmp / "selection.json", self.tmp / "questions.json"
        manifest.write_text(json.dumps(self.selection))
        questions.write_text(json.dumps({"questions": [{"question": first["question"]}]}))
        result = self.alt("task", "block", self.slug, "--reason", "The revised layout is ready.",
                          "--questions-file", str(questions), "--design-file", str(manifest),
                          "--for-burak", env=self.owner_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        current = S.load_task(self.project, self.slug)
        fresh = current["questions"][-1]
        self.assertEqual(current["questions"][1], independent)
        self.assertNotEqual(fresh["id"], first["id"])
        self.assertEqual(json.loads(result.stdout)["design_url"], T.design_url(self.project, self.slug, fresh))
        self.assertEqual(self.capture_path(first).read_bytes(), png())
        self.assertEqual(self.capture_path(fresh).read_bytes(), png(90))
        self.assertEqual(self.request("GET", self.api(first))[0], 200)
        self.assertEqual(self.request("GET", self.api(fresh))[0], 200)
        with self.assertRaises(T.TransitionError):
            T.accept_question(self.project, self.slug, first["id"], 1)
        self.assertEqual(current["hold_merge"], "Operator reviews the finished PR")

    def test_cli_project_override_cannot_publish_a_matching_slug_in_another_project(self):
        other_project = "separate-review-project"
        self.register(other_project, path=self.tmp / "separate")
        other = T.new(other_project, "Mobile proposal", "Unrelated design")
        self.assertEqual(other["slug"], self.slug)
        other.update(state="running", attempt=1, worktree=str(self.worktree))
        S.save_task(other_project, other)
        manifest = self.tmp / "selection.json"
        manifest.write_text(json.dumps(self.selection))
        result = self.alt("--project", other_project, "task", "block", self.slug, "--reason", "Review?",
                          "--design-file", str(manifest), env=self.owner_env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("own project/task", result.stderr)
        self.assertEqual(S.load_task(other_project, self.slug), other)


if __name__ == "__main__":
    unittest.main()
