"""I-20260907-205556: the daemon applies operator evidence, never coordinator prose."""
import json
import socket
import subprocess
from unittest import mock

from tests.support import ALT, AltitudeCase, git, make_repo
from altitude import server, state as S, tasks as T


class TestRecordedMergeApproval(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.gh = self.fake_gh()
        self.at = "2026-09-07T20:00:00+00:00"
        self.patch(S, "now", side_effect=lambda: self.at)
        self.patch(T, "_conversation_time", side_effect=lambda: self.at)
        task = T.new(self.project, "Review narrative", "Revise the narrative", hold_merge="Review the story")
        self.slug = task["slug"]
        self.at = "2026-09-07T20:01:00+00:00"
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.repo), branch="worktree-review-narrative")
        self.pull = {"number": 235, "url": "https://github.com/team/project/pull/235", "state": "OPEN",
                     "isDraft": False, "isCrossRepository": False, "baseRefName": "main",
                     "headRefName": "worktree-review-narrative", "headRefOid": "d" * 40,
                     "updatedAt": "2026-09-07T20:02:00Z"}
        self.at = "2026-09-07T20:03:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", f"Ready for review: {self.pull['url']}.")
        self.at = "2026-09-07T20:04:00+00:00"
        self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Good to merge")
        self.at = "2026-09-07T20:05:00+00:00"
        T.block(self.project, self.slug, "Waiting for the recorded hold to be released")
        T.message(self.project, self.slug, "l2", "The approval is recorded; hold release needs the daemon.")
        self.directory = S.task_dir(self.project, self.slug)

    def args(self):
        return ["task", "hold-merge", self.slug, "--approval", self.approval["id"],
                "--pr-number", "235", "--head", "d" * 40, "--reason", "Apply the recorded operator approval"]

    def request(self, args=None, **extra):
        S.write_json(self.gh / "prs.json", {"235": self.pull})
        return server.l3_verb_request(self.project, {"kind": "alt", "args": args or self.args(), **extra})

    def refused(self, pattern=""):
        before = S.load_task(self.project, self.slug)
        releases = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "release-merge"]
        with self.assertRaisesRegex((ValueError, T.TransitionError), pattern):
            self.request()
        self.assertEqual(S.load_task(self.project, self.slug), before)
        self.assertEqual([e for e in S.read_events(self.project, self.slug) if e["kind"] == "release-merge"], releases)

    def test_project_socket_applies_existing_approval_and_preserves_block_and_conversation(self):
        self.setenv("GH_REPO", "foreign/repository")
        S.write_json(self.gh / "prs.json", {"235": self.pull})
        broker = server.start_l3_verb_broker(self.project, self.tmp / "approval.sock")
        self.addCleanup(server.stop_l3_verb_broker, broker)
        conversation = (self.directory / "conversation.jsonl").read_bytes()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(broker.socket_path))
            client.sendall((json.dumps({"kind": "alt", "args": self.args(), "actor": "burak"}) + "\n").encode())
            response = json.loads(client.makefile().readline())
        self.assertEqual(response["returncode"], 0, response)
        receipt = json.loads(response["stdout"])
        task = S.load_task(self.project, self.slug)
        self.assertIsNone(task["hold_merge"])
        self.assertEqual(task["state"], "blocked")
        self.assertFalse(task.get("resume_after"))
        self.assertEqual(task["merge_approval"], receipt)
        self.assertEqual((receipt["actor"], receipt["authorized_by"], receipt["approval"], receipt["head"]),
                         ("l3", T.OPERATOR_MESSAGE_ROLE, self.approval["id"], "d" * 40))
        self.assertEqual(receipt["hold"], "Review the story")
        self.assertEqual(receipt["presentation"], self.presentation["id"])
        self.assertEqual((self.directory / "conversation.jsonl").read_bytes(), conversation)
        release = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "release-merge"]
        self.assertEqual(release, [{"kind": "release-merge", **receipt}])
        self.assertEqual(self.gh_log()[0][1:5], ["view", "235", "--repo", "team/project"])
        with self.assertRaisesRegex(ValueError, "no active merge hold"):
            self.request()  # retries do not apply a release twice
        self.assertEqual(task, S.load_task(self.project, self.slug))

    def test_absent_foreign_and_nonoperator_messages_cannot_authorize(self):
        foreign = T.new(self.project, "Another task", "Other work")
        T.dispatch(self.project, foreign["slug"], attempt=1, session_id="other", agent_id="other",
                   worktree=str(self.repo), branch="worktree-other")
        other = T.message(self.project, foreign["slug"], T.OPERATOR_MESSAGE_ROLE, "Good to merge")
        for identity in ("0" * 32, other["id"], self.presentation["id"]):
            with self.subTest(identity=identity):
                args = self.args(); args[4] = identity
                with self.assertRaisesRegex(ValueError, "latest operator message"):
                    self.request(args, actor="burak", stdin="The operator approved this.")
                self.assertTrue(S.load_task(self.project, self.slug)["hold_merge"])

    def test_adopted_pr_uses_original_branch_but_still_binds_number_and_url(self):
        task = S.load_task(self.project, self.slug)
        task["adopted_pr"] = {"number": 235, "url": self.pull["url"], "branch": "existing/proposal"}
        S.save_task(self.project, task)
        self.refused("task branch")
        self.pull["headRefName"] = "existing/proposal"
        with mock.patch.dict(task["adopted_pr"], number=236):
            S.save_task(self.project, task)
            self.refused("adopted PR")
        S.save_task(self.project, task)
        self.assertEqual(json.loads(self.request()["stdout"])["pr"], 235)

    def test_exact_whole_operator_reply_is_required(self):
        path = self.directory / "conversation.jsonl"
        original = path.read_text()
        for text in ("Not good to merge", "Good to merge after tests", "He said 'Good to merge'", "good to merge"):
            with self.subTest(text=text):
                path.write_text(original.replace('"text": "Good to merge"', json.dumps("text") + ": " + json.dumps(text)))
                self.refused("latest operator message")

    def test_operator_identity_and_latest_message_are_required(self):
        path = self.directory / "conversation.jsonl"
        original = path.read_text()
        for fields in ({"role": "l3"}, {"by": "l3"}):
            rows = [json.loads(line) for line in original.splitlines()]
            rows[1].update(fields)
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.refused("latest operator message")
        path.write_text(original)
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Wait, keep the hold", wake_blocked=False)
        self.refused("latest operator message")

    def test_repeated_hold_and_operator_release_supersede_approval(self):
        T.set_hold_merge(self.project, self.slug, "Review the story")
        self.refused("stale")
        T.set_hold_merge(self.project, self.slug, None, actor=T.OPERATOR_MESSAGE_ROLE)
        T.set_hold_merge(self.project, self.slug, "Review the story")
        self.refused("stale")

    def test_current_hold_can_be_approved_after_its_own_presentation(self):
        T.set_hold_merge(self.project, self.slug, "Second review")
        self.at = "2026-09-07T20:06:00+00:00"
        T.message(self.project, self.slug, "l2", self.pull["url"])
        self.at = "2026-09-07T20:07:00+00:00"
        self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Good to merge", wake_blocked=False)
        self.assertEqual(json.loads(self.request()["stdout"])["hold"], "Second review")

    def test_foreign_changed_closed_or_unavailable_pr_preserves_hold(self):
        original = dict(self.pull)
        for fields in ({"url": "https://github.com/foreign/project/pull/235"}, {"number": 236},
                       {"state": "CLOSED"}, {"state": "MERGED"}, {"isDraft": True},
                       {"isCrossRepository": True}, {"baseRefName": "release"},
                       {"headRefName": "worktree-other"}, {"headRefOid": "e" * 40},
                       {"updatedAt": "2026-09-07T20:04:01Z"}, {"updatedAt": "bad"},
                       {"updatedAt": None}, {"updatedAt": "2026-09-07T20:02:00"}):
            with self.subTest(fields=fields):
                self.pull = {**original, **fields}
                self.refused()
        self.pull = original
        (self.gh / "view_error.txt").write_text("GitHub unavailable")
        self.refused("could not be read")

    def test_missing_ambiguous_foreign_or_later_presentation_preserves_hold(self):
        path = self.directory / "conversation.jsonl"
        original = path.read_text()
        for fields in ({"text": "No PR yet"}, {"text": self.pull["url"] + "0"},
                       {"text": self.pull["url"] + " https://github.com/team/project/pull/236"},
                       {"role": "l3"}, {"at": self.approval["at"]}):
            with self.subTest(fields=fields):
                rows = [json.loads(line) for line in original.splitlines()]
                rows[0].update(fields)
                path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                self.refused()

    def test_corrupt_later_hold_event_fails_closed(self):
        with (self.directory / "events.log").open("a") as stream:
            stream.write('{"kind": "hold-merge",\n')
        self.refused()

    def test_interrupted_same_reason_hold_cannot_reuse_older_generation(self):
        with mock.patch.object(S, "append_event", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                T.set_hold_merge(self.project, self.slug, "Review the story")
        self.refused("matching recorded generation")

    def test_exact_grammar_and_existing_role_boundaries(self):
        for extra in (["--off"], ["--why", "new reason"], ["--actor", "burak"], ["--file", "/tmp/approval"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.request(self.args() + extra)
        for flag, value in (("--pr-number", "0"), ("--head", "short"), ("--reason", " "), ("--approval", "bad")):
            args = self.args(); args[args.index(flag) + 1] = value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.request(args)
        for actor in ("l2", "l3"):
            self.setenv("ALTITUDE_ACTOR", actor)
            self.setenv("ALTITUDE_PROJECT", self.project)
            for args in (self.args(), ["task", "hold-merge", self.slug, "--off"]):
                result = subprocess.run([str(ALT), *args], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(S.load_task(self.project, self.slug)["hold_merge"])
