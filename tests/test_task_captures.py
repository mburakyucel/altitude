"""Validation captures attached to an owner's reply: fixed, content-named copies served only as the reply lists them."""
import hashlib
import json
import os

from tests.support import AltitudeCase, add_worktree, make_repo
from tests.test_capture import gif
from tests import test_task_design as design
from altitude import capture as C, state as S, tasks as T


class TestTaskCaptures(AltitudeCase):
    request, stop_server = design.TestTaskDesign.request, design.TestTaskDesign.stop_server

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        task = T.new(self.project, "Phone captures", "Record the phone walkthrough.")
        self.slug = task["slug"]
        task.update(state="running", attempt=1, worktree=str(add_worktree(self.repo, self.slug)))
        S.save_task(self.project, task)
        self.evidence = S.task_dir(self.project, self.slug) / "validation"
        self.lane = self.evidence / "2" / "captures"
        self.lane.mkdir(parents=True)
        self.httpd = None

    def reply(self, run=2, text="The walkthrough, recorded.", **options):
        return T.message(self.project, self.slug, "l2", text, by="l2", expected_attempt=1, capture_run=run, **options)

    def conversation(self):
        return T.task_messages(self.project, self.slug)

    def test_a_reply_attaches_a_runs_captures_as_fixed_copies_served_as_listed(self):
        (self.evidence / "2.simulator.gif").write_bytes(gif(390, 300, frames=4))
        (self.lane / "phone-pairing-a-wrong-code.gif").write_bytes(gif(390, 200))
        (self.lane / "captures.json").write_text("[]")
        result = self.alt("task", "reply", "--capture", "2", "Run 2, recorded.",
                          env={"ALTITUDE_PROJECT": self.project, "ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug,
                               "ALTITUDE_ATTEMPT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.conversation()[-1]
        self.assertEqual(row["capture_run"], 2)
        self.assertEqual([(c["title"], c["width"], c["frames"]) for c in row["captures"]],
                         [("Simulator iPhone", 390, 4), ("Phone pairing a wrong code", 390, 3)])
        saved = S.task_dir(self.project, self.slug) / "captures"
        self.assertEqual(sorted(p.name for p in saved.iterdir()), sorted(c["name"] for c in row["captures"]))
        for capture in row["captures"]:
            self.assertEqual(capture["name"], hashlib.sha256((saved / capture["name"]).read_bytes()).hexdigest() + ".gif")
        (self.lane / "phone-pairing-a-wrong-code.gif").write_bytes(gif(390, 200, frames=5))  # later edits change nothing

        status, _, raw = self.request("GET", f"/api/captures/{self.project}/{self.slug}/{row['id']}")
        self.assertEqual(status, 200, raw)
        listed = json.loads(raw)
        self.assertEqual((listed["run"], listed["conversation_url"]), (2, f"/projects/{self.project}/tasks/{self.slug}"))
        self.assertEqual(listed["captures"][1], {"title": "Phone pairing a wrong code", "bytes": len(gif(390, 200)),
                                                 "width": 390, "height": 200, "frames": 3, "seconds": 1.5,
                                                 "url": f"/api/captures/{self.project}/{self.slug}/{row['id']}/"
                                                        f"{row['captures'][1]['name']}"})
        status, headers, data = self.request("GET", listed["captures"][1]["url"])
        self.assertEqual((status, data), (200, gif(390, 200)))
        self.assertEqual({key: headers[key] for key in ("content-type", "cache-control", "x-content-type-options",
                                                         "content-security-policy", "referrer-policy")},
                         {"content-type": "image/gif", "cache-control": "no-store", "x-content-type-options": "nosniff",
                          "content-security-policy": "default-src 'none'; sandbox", "referrer-policy": "no-referrer"})

    def test_only_a_runs_own_bounded_gifs_are_attached_and_a_refusal_adds_no_reply(self):
        outside = self.tmp / "outside.gif"
        outside.write_bytes(gif())
        for name, setup, why in (
                ("none", lambda: None, "has no captures; ask its lane for them with CAPTURE=1"),
                ("not a GIF", lambda: (self.lane / "a.gif").write_bytes(b"<svg onload=alert(1)>"), "A: not a capture"),
                ("trailing data", lambda: (self.lane / "a.gif").write_bytes(gif() + b"<script>"), "not a capture"),
                ("link", lambda: os.symlink(outside, self.lane / "a.gif"), "not a capture"),
                ("folder link", lambda: (self.lane.rmdir(), os.symlink(self.tmp, self.lane)), "captures unavailable"),
                ("too many", lambda: [(self.lane / f"{i:02d}.gif").write_bytes(gif()) for i in range(C.RUN_LIMIT + 1)],
                 f"a reply shows at most {C.RUN_LIMIT}"),
                ("run number", lambda: None, "names a validation run number")):
            with self.subTest(name):
                before = len(self.conversation())
                setup()
                with self.assertRaisesRegex(T.TransitionError, why):
                    self.reply("2" if name == "run number" else 2)
                self.assertEqual(len(self.conversation()), before)
                self.assertFalse((S.task_dir(self.project, self.slug) / "captures").exists())
                if self.lane.is_symlink():
                    self.lane.unlink()
                    self.lane.mkdir()
                for path in self.lane.iterdir():
                    path.unlink()
        (self.lane / "a.gif").write_bytes(gif())
        with self.assertRaisesRegex(T.TransitionError, "Only the owner"):
            T.message(self.project, self.slug, "l3", "Look", capture_run=2)
        with self.patch_limit(len(gif()) - 1), self.assertRaisesRegex(T.TransitionError, "MiB of attached captures"):
            self.reply()
        self.reply()
        with self.patch_limit(len(gif())):
            self.reply(text="The same capture again needs no more room.")

    def patch_limit(self, limit):
        from unittest import mock
        return mock.patch.object(T, "CAPTURE_TASK_LIMIT", limit)

    def test_missing_altered_or_unlisted_captures_are_unavailable_never_other_content(self):
        (self.lane / "a.gif").write_bytes(gif())
        row = self.reply()
        other = T.message(self.project, self.slug, "l2", "No captures here.", by="l2", expected_attempt=1)
        base = f"/api/captures/{self.project}/{self.slug}"
        saved = S.task_dir(self.project, self.slug) / "captures" / row["captures"][0]["name"]
        for path in (f"{base}/{other['id']}", f"{base}/unknown", f"{base}/{row['id']}/x/y",
                     f"/api/captures/{self.project}/../{row['id']}"):
            status, _, raw = self.request("GET", path)
            self.assertEqual((status, json.loads(raw)), (404, {"error": "Capture unavailable"}), path)
        url = f"{base}/{row['id']}/{row['captures'][0]['name']}"
        for name, change in (("unlisted", None), ("altered", lambda: saved.write_bytes(gif(frames=2))),
                             ("link", lambda: (saved.unlink(), os.symlink(self.lane / "a.gif", saved))),
                             ("missing", lambda: saved.unlink())):
            with self.subTest(name):
                if change:
                    change()
                    status, headers, data = self.request("GET", url)
                else:
                    status, headers, data = self.request("GET", f"{base}/{row['id']}/{'0' * 64}.gif")
                self.assertEqual((status, data), (404, b"Capture unavailable\n"))
