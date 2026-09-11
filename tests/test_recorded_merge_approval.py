"""Recorded approval provenance; semantic merge judgment belongs to the coordinator (#294)."""
import copy
import json
import socket
import subprocess
from datetime import datetime, timedelta
from unittest import mock

from tests.support import ALT, AltitudeCase, git, make_repo
from altitude import config, l3, land, server, state as S, tasks as T


class TestRecordedMergeApproval(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.gh = self.fake_gh()
        self.patch(S, "now", side_effect=lambda: self.at)
        self.patch(T, "_conversation_time", side_effect=lambda: self.at)
        self.record_approval("You can merge it")

    def record_approval(self, text, title="Review narrative"):
        self.question = self.latest = None
        self.at = "2026-09-07T20:00:00+00:00"
        task = T.new(self.project, title, "Revise the narrative", hold_merge="Review the story")
        self.slug = task["slug"]
        self.at = "2026-09-07T20:01:00+00:00"
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.repo), branch=f"worktree-{self.slug}")
        self.pull = {"number": 235, "url": "https://github.com/team/project/pull/235", "state": "OPEN",
                     "isDraft": False, "isCrossRepository": False, "baseRefName": "main",
                     "headRefName": f"worktree-{self.slug}", "headRefOid": "d" * 40,
                     "updatedAt": "2026-09-07T20:02:00Z"}
        self.at = "2026-09-07T20:03:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", f"Ready for review: {self.pull['url']}.")
        self.at = "2026-09-07T20:04:00+00:00"
        self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, text)
        self.at = "2026-09-07T20:05:00+00:00"
        T.block(self.project, self.slug, "Waiting for the recorded hold to be released")
        T.message(self.project, self.slug, "l2", "The approval is recorded; hold release needs the daemon.")
        self.directory = S.task_dir(self.project, self.slug)

    def args(self):
        args = ["task", "hold-merge", self.slug, "--approval", self.approval["id"],
                "--pr-number", "235", "--head", "d" * 40, "--reason", "Original sources authorize this merge",
                "--presentation", self.presentation["id"], "--latest-operator", (self.latest or self.approval)["id"]]
        return args + (["--question", self.question["id"], "--revision", str(self.question["revision"])]
                       if self.question else [])

    def decide(self, *, quick=True):
        self.at = (datetime.fromisoformat(self.at) + timedelta(seconds=1)).isoformat()
        task = T.escalate(self.project, self.slug, "May the owner merge the reviewed PR?",
                          recommendation=f"Merge {self.pull['url']} after reviewing the result.",
                          recommendation_label="Approve merge")
        self.question = task["questions"][-1]
        self.at = (datetime.fromisoformat(self.at) + timedelta(seconds=1)).isoformat()
        if quick:
            decision = T.accept_question(self.project, self.slug, self.question["id"], self.question["revision"])
            self.approval = next(r for r in T.task_messages(self.project, self.slug)
                                 if r["id"] == decision["resolution"]["message_id"])
        else:
            self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Yes, ship the reviewed PR.")
            T.resolve_question(self.project, self.slug, self.question["id"], self.question["revision"],
                               self.approval["id"], disposition="answered", reason="Merge the reviewed PR",
                               expected_attempt=1)

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

    def project_approval(self, text="Both may merge; resolve overlaps and rebase within these outcomes."):
        self.at = "2026-09-07T20:04:30+00:00"
        return l3.chat_log(self.project, "user", text, trigger="chat", turn_id="a" * 12)

    def project_args(self):
        args = self.args()
        for flag in ("--approval", "--latest-operator"):
            args[args.index(flag) + 1] = "a" * 12
        return args + ["--source", "project", "--latest-other-operator", self.approval["id"]]

    def test_project_original_is_applied_independently_to_two_task_prs(self):
        self.project_approval("Merge https://github.com/team/project/pull/235 and https://github.com/team/project/pull/236.")
        l3.chat_log(self.project, "assistant", "I will coordinate both deliveries.", trigger="chat", turn_id="a" * 12)
        first = json.loads(self.request(self.project_args())["stdout"])
        self.record_approval("Ready for project coordination", title="Second story")
        self.pull.update(number=236, url="https://github.com/team/project/pull/236")
        self.at = "2026-09-07T20:03:30+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", self.pull["url"])
        task = S.load_task(self.project, self.slug)
        task["prs"] = [236]
        S.save_task(self.project, task)
        S.write_json(self.gh / "prs.json", {"236": self.pull})
        args = self.project_args()
        args[args.index("--pr-number") + 1] = "236"
        second = json.loads(server.l3_verb_request(self.project, {"kind": "alt", "args": args})["stdout"])
        self.assertEqual((first["pr"], second["pr"]), (235, 236))
        for receipt in (first, second):
            self.assertEqual((receipt["source"], receipt["approval"], receipt["authorized_by"]),
                             ("project", "a" * 12, T.OPERATOR_MESSAGE_ROLE))

    def test_project_and_task_corrections_require_both_reviewed_watermarks(self):
        self.project_approval()
        args = self.project_args()
        self.at = "2026-09-07T20:06:00+00:00"
        l3.chat_log(self.project, "user", "Stop; retain both holds.", turn_id="b" * 12, trigger="chat")
        with self.assertRaisesRegex(ValueError, "latest operator"):
            self.request(args)
        args[args.index("--latest-operator") + 1] = "b" * 12
        T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Do not merge this PR", wake_blocked=False)
        with self.assertRaisesRegex(ValueError, "other conversation"):
            self.request(args)
        self.assertTrue(S.load_task(self.project, self.slug)["hold_merge"])
        # Coordinator judgment must reject the now-reviewed revocations; no classifier applies them.

    def test_task_source_also_cannot_ignore_project_correction(self):
        self.project_approval("Retain the hold after all.")
        self.refused("other conversation")

    def test_pending_project_correction_is_not_yet_reviewed_authority(self):
        self.project_approval()
        before = S.load_task(self.project, self.slug)
        l3.queue_message(self.project, "Stop; do not merge.", trigger="chat", role=T.OPERATOR_MESSAGE_ROLE)
        for args in (self.args(), self.project_args()):
            with self.assertRaisesRegex(ValueError, "pending project chat"):
                self.request(args)
        self.assertEqual(S.load_task(self.project, self.slug), before)

    def test_unidentified_latest_correction_refuses_but_older_history_remains_readable(self):
        for _ in range(2):
            l3.chat_log(self.project, "user", "Earlier discussion", trigger="chat")
        self.refused("other conversation")
        self.project_approval()
        receipt = json.loads(self.request(self.project_args())["stdout"])
        self.assertEqual(receipt["approval"], "a" * 12)

    def test_project_forgery_foreign_source_corrupt_and_duplicate_evidence_refuse(self):
        path = config.project_dir(self.project) / "chat.jsonl"
        for fields in ({"role": "assistant"}, {"trigger": "task-blocked"}, {"by": "l3"},
                       {"turn_id": "b" * 12}):
            row = {"role": "user", "trigger": "chat", "turn_id": "a" * 12,
                   "at": "2026-09-07T20:04:30+00:00", "text": "Merge it", **fields}
            path.write_text(json.dumps(row) + "\n")
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "original operator"):
                self.request(self.project_args())
        path.unlink()
        self.project_approval()
        original = path.read_text()
        for suffix in (original, "{broken later correction\n"):
            path.write_text(original + suffix)
            with self.assertRaises(ValueError):
                self.request(self.project_args())
        path.unlink()
        self.register("another")
        l3.chat_log("another", "user", "Merge it", trigger="chat", turn_id="a" * 12)
        with self.assertRaisesRegex(ValueError, "original operator"):
            self.request(self.project_args())

    def test_project_wrong_pr_or_repository_refuses(self):
        path = config.project_dir(self.project) / "chat.jsonl"
        for url in ("https://github.com/team/project/pull/236", "https://github.com/foreign/project/pull/235"):
            if path.exists():
                path.unlink()
            self.project_approval(f"Merge {url}")
            with self.assertRaisesRegex(ValueError, "different PR"):
                self.request(self.project_args())

    def test_project_answer_preserves_exact_question_source_and_revision(self):
        self.at = "2026-09-07T20:04:10+00:00"
        task = T.escalate(self.project, self.slug, "May this reviewed PR merge?",
                          recommendation=f"Merge {self.pull['url']}.")
        self.question = task["questions"][-1]
        original = self.project_approval("Merge the reviewed PR.")
        args = self.project_args()
        with self.assertRaisesRegex(ValueError, "current answered"):
            self.request(args)
        T.resolve_question(self.project, self.slug, self.question["id"], self.question["revision"],
                           original["turn_id"], source="project", disposition="answered", reason="Merge this PR",
                           expected_attempt=1)
        receipt = json.loads(self.request(args)["stdout"])
        self.assertEqual((receipt["source"], receipt["question"], receipt["revision"], receipt["question_context_only"]),
                         ("project", self.question["id"], self.question["revision"], False))

    def test_integration_reuses_original_authority_but_checks_current_head_and_hold(self):
        self.project_approval()
        args = self.project_args()
        self.pull.update(headRefOid="e" * 40, updatedAt="2026-09-07T20:06:00Z")
        args[args.index("--head") + 1] = "e" * 40
        with self.assertRaisesRegex(ValueError, "stale"):
            self.request(args)
        self.at = "2026-09-07T20:07:00+00:00"
        integrated = T.message(self.project, self.slug, "l2",
                               f"Rebased within the approved outcome: {self.pull['url']} head {'e' * 40}")
        args += ["--integration-presentation", integrated["id"]]
        args[args.index("--reason") + 1] = "Original decision delegates rebasing; reviewed diff retains the approved outcome."
        before = S.load_task(self.project, self.slug)
        stale = list(args)
        stale[stale.index("--head") + 1] = "d" * 40
        with self.assertRaisesRegex(ValueError, "exact head"):
            self.request(stale)
        self.assertEqual(S.load_task(self.project, self.slug), before)
        receipt = json.loads(self.request(args)["stdout"])
        self.assertEqual((receipt["head"], receipt["integration_presentation"], receipt["approval"]),
                         ("e" * 40, integrated["id"], "a" * 12))
        T.set_hold_merge(self.project, self.slug, "New review")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.request(args)

    def test_absent_foreign_and_nonoperator_messages_cannot_authorize(self):
        foreign = T.new(self.project, "Another task", "Other work")
        T.dispatch(self.project, foreign["slug"], attempt=1, session_id="other", agent_id="other",
                   worktree=str(self.repo), branch="worktree-other")
        other = T.message(self.project, foreign["slug"], T.OPERATOR_MESSAGE_ROLE, "You can merge it")
        for identity in ("0" * 32, other["id"], self.presentation["id"]):
            with self.subTest(identity=identity):
                args = self.args(); args[4] = identity
                with self.assertRaisesRegex(ValueError, "latest operator message"):
                    self.request(args, actor="burak", stdin="The operator approved this.")
                self.assertTrue(S.load_task(self.project, self.slug)["hold_merge"])

    def test_delegated_l3_resolution_cannot_supply_operator_merge_approval(self):
        self.at = "2026-09-07T20:06:00+00:00"
        task = T.escalate(self.project, self.slug, "May the owner edit the tests already in its lease?")
        self.question = task["questions"][-1]
        self.at = "2026-09-07T20:07:00+00:00"
        answer = T.message(self.project, self.slug, "l3", "Those tests are already authorized in the lease.")
        resolved = T.resolve_question(self.project, self.slug, self.question["id"], self.question["revision"],
                                      answer["id"], disposition="answered", reason="Existing lease permits the tests",
                                      expected_attempt=1, l3_authority="The recorded task lease includes tests/.")
        self.assertEqual(resolved["resolution"]["by"], "l3")
        self.assertEqual(resolved["audience"], "operator")
        self.assertEqual(S.load_task(self.project, self.slug)["hold_merge"], "Review the story")
        self.latest, self.approval = self.approval, answer
        self.refused("original operator approval")

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

    def test_cited_conversational_authorizations_do_not_depend_on_a_phrase_list(self):
        for index, text in enumerate(("Good to merge", "good to merge.", "GOOD TO MERGE!",
                                      "You can merge it", "you can merge it.", "YOU CAN MERGE IT!",
                                      "  You can merge it.\n", "Ship this reviewed PR, please.")):
            with self.subTest(text=text):
                self.record_approval(text, title=f"Review wording {index}")
                conversation = (self.directory / "conversation.jsonl").read_bytes()
                receipt = json.loads(self.request()["stdout"])
                task = S.load_task(self.project, self.slug)
                self.assertIsNone(task["hold_merge"])
                self.assertEqual(task["merge_approval"], receipt)
                self.assertEqual(receipt["approval"], self.approval["id"])
                self.assertEqual((self.directory / "conversation.jsonl").read_bytes(), conversation)

    def test_sequential_adoption_requires_approval_of_the_new_active_pr(self):
        authority = {"actor": T.OPERATOR_MESSAGE_ROLE}
        first, _ = land._record_adoption(self.project, self.slug,
            {"number": 235, "url": self.pull["url"], "branch": "proposal/first", "head": "d" * 40},
            authority, previous=None, dry_run=False)
        self.pull["headRefName"] = first["branch"]
        released = json.loads(self.request()["stdout"])
        old_release = [e for e in S.read_events(self.project, self.slug) if e["kind"] == "release-merge"]
        self.at = "2026-09-07T20:06:00+00:00"
        second, hold = land._record_adoption(self.project, self.slug,
            {"number": 236, "url": "https://github.com/team/project/pull/236",
             "branch": "proposal/second", "head": "e" * 40}, authority, previous=first, dry_run=False)
        self.assertEqual(hold, "Review the story")
        self.assertEqual(S.load_task(self.project, self.slug)["adoption_history"], [first])
        self.refused("stale")  # The previous approval cannot authorize this fresh hold.

        # Even a fresh approval of the old PR cannot release the active PR's hold.
        self.at = "2026-09-07T20:07:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", first["url"])
        self.decide()
        self.refused("adopted PR")

        self.pull.update(number=236, url=second["url"], headRefName=second["branch"], headRefOid=second["head"],
                         updatedAt="2026-09-07T20:09:00Z")
        self.at = "2026-09-07T20:10:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", second["url"])
        self.decide()
        self.at = "2026-09-07T20:12:00+00:00"
        accepted = T.apply_merge_approval(self.project, self.slug, self.approval["id"], self.pull,
                                         head=second["head"], reason="Approve the second PR", actor="l3",
                                         presentation=self.presentation["id"], latest_operator=self.approval["id"],
                                         question=self.question["id"], revision=self.question["revision"])
        self.assertEqual((accepted["pr"], accepted["head"]), (236, second["head"]))
        self.assertNotEqual(accepted["hold_id"], released["hold_id"])
        self.assertIsNone(S.load_task(self.project, self.slug)["hold_merge"])
        self.assertEqual([e for e in S.read_events(self.project, self.slug)
                          if e["kind"] == "release-merge"][:1], old_release)
        self.assertEqual(S.load_task(self.project, self.slug)["adoption_history"], [first])

    def test_approval_from_another_project_cannot_authorize_the_same_task_slug(self):
        self.register("foreign")
        foreign = T.new("foreign", "Review narrative", "Other work", hold_merge="Other review")
        self.assertEqual(foreign["slug"], self.slug)
        T.dispatch("foreign", self.slug, attempt=1, session_id="other", agent_id="other",
                   worktree=str(self.repo), branch=self.pull["headRefName"])
        other = T.message("foreign", self.slug, T.OPERATOR_MESSAGE_ROLE, "You can merge it")
        self.approval = other
        self.refused("latest operator message")
        with self.assertRaisesRegex(ValueError, "latest operator message"):
            self.request(project="foreign", actor=T.OPERATOR_MESSAGE_ROLE, stdin="You can merge it")
        self.assertEqual(S.load_task("foreign", self.slug)["hold_merge"], "Other review")

    def test_later_discussion_or_revocation_invalidates_unreviewed_context(self):
        # Interpretation is L3's contract, not a classifier under test. Every unseen later
        # operator message must fence an old request, even if it merely discusses the PR.
        path = self.directory / "conversation.jsonl"
        original = path.read_text()
        for text in ("Looks good", "Yes", "Maybe you can merge it", "You can merge it?",
                     "Good to merge?", "Not good to merge", "You cannot merge it", "You can't merge it",
                     "Do not merge it", "Good to merge after tests", "You can merge it after tests",
                     "You can merge it if CI passes", "If CI passes, you can merge it",
                     "You can merge it, but wait for review", "You can merge it. Wait for my review.",
                     "You can merge it\nunless checks fail", "You can merge it...",
                     "He said 'Good to merge'", '"You can merge it"', "> You can merge it"):
            with self.subTest(text=text):
                path.write_text(original)
                T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, text, wake_blocked=False)
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
        self.presentation = T.message(self.project, self.slug, "l2", self.pull["url"])
        self.decide()
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

    def test_ui_and_typed_decisions_share_original_source_validation(self):
        for quick in (True, False):
            with self.subTest(quick=quick):
                self.record_approval("Earlier discussion", title=f"Decision {quick}")
                self.decide(quick=quick)
                receipt = json.loads(self.request()["stdout"])
                self.assertEqual((receipt["question"], receipt["revision"], receipt["latest_operator"]),
                                 (self.question["id"], self.question["revision"], self.approval["id"]))
                self.assertEqual(receipt["option_key"], "recommended" if quick else None)

    def test_question_binding_cannot_be_omitted_or_replaced_by_a_different_decision(self):
        self.decide()
        with self.assertRaisesRegex(ValueError, "question and revision"):
            self.request(self.args()[:-4])
        original = S.load_task(self.project, self.slug)
        for fields in ({"audience": "l3"}, {"status": "open"},
                       {"resolution": {**original["questions"][-1]["resolution"], "disposition": "superseded"}},
                       {"resolution": {**original["questions"][-1]["resolution"], "remaining": "Review needed"}},
                       {"resolution": {**original["questions"][-1]["resolution"], "by": "l3"}},
                       {"resolution": {**original["questions"][-1]["resolution"], "source": "project"}},
                       {"resolution": {**original["questions"][-1]["resolution"], "message_id": "0" * 32}},
                       {"resolution": {**original["questions"][-1]["resolution"], "option_key": "another"}},
                       {"asked": "2026-09-07T20:02:59+00:00"}):
            with self.subTest(fields=fields):
                task = copy.deepcopy(original)
                task["questions"][-1].update(fields)
                S.save_task(self.project, task)
                self.refused()
        S.save_task(self.project, original)
        task = T.escalate(self.project, self.slug, "Which follow-up is wanted?")
        self.question = task["questions"][-1]
        self.refused("answered operator question revision")

    def test_changed_question_refs_and_later_revision_refuse_even_with_reaffirmation(self):
        self.decide()
        original = S.load_task(self.project, self.slug)
        for refs in ([], [{"id": self.question["id"], "revision": self.question["revision"] + 1}]):
            task = copy.deepcopy(original)
            task["questions"][-1]["acceptance_message"]["question_refs"] = refs
            S.save_task(self.project, task)
            self.refused("different question revision")
        task = copy.deepcopy(original)
        task["questions"].append({**task["questions"][-1], "revision": self.question["revision"] + 1,
                                  "status": "open", "acceptance_message": None, "resolution": None})
        S.save_task(self.project, task)
        self.at = "2026-09-07T20:08:00+00:00"
        self.latest = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "I already approved the merge.",
                                wake_blocked=False)
        self.refused("current answered")

    def test_fresh_conversational_approval_keeps_old_design_answer_as_context_only(self):
        self.at = "2026-09-07T20:06:00+00:00"
        asked = T.escalate(self.project, self.slug, "Accept the design for implementation?",
                           recommendation="Implement this design; retain the merge hold.")
        self.question = asked["questions"][-1]
        self.at = "2026-09-07T20:07:00+00:00"
        T.accept_question(self.project, self.slug, self.question["id"], self.question["revision"])
        design = copy.deepcopy(S.load_task(self.project, self.slug)["questions"][-1])
        self.at = "2026-09-07T20:08:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", f"Ready for merge review: {self.pull['url']}")
        self.at = "2026-09-07T20:09:00+00:00"
        self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "You can merge it", wake_blocked=False)
        self.assertEqual(self.approval["question_refs"], [{"id": design["id"], "revision": design["revision"]}])
        receipt = json.loads(self.request()["stdout"])
        self.assertTrue(receipt["question_context_only"])
        self.assertIsNone(receipt["option_key"], "the earlier implementation option is not merge authority")
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1], design)

    def test_inherited_superseded_context_does_not_accept_its_abandoned_recommendation(self):
        self.at = "2026-09-07T20:06:00+00:00"
        self.question = T.escalate(self.project, self.slug, "Implement a different design?",
                                   recommendation="Implement the abandoned alternative.")["questions"][-1]
        self.at = "2026-09-07T20:07:00+00:00"
        source = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Drop that alternative.")
        T.resolve_question(self.project, self.slug, self.question["id"], self.question["revision"], source["id"],
                           disposition="superseded", reason="The alternative is abandoned", expected_attempt=1)
        self.at = "2026-09-07T20:08:00+00:00"
        self.presentation = T.message(self.project, self.slug, "l2", self.pull["url"])
        self.at = "2026-09-07T20:09:00+00:00"
        self.approval = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Merge this reviewed result.")
        task = S.load_task(self.project, self.slug)
        # An explicitly viewed older revision cannot be passed as incidental context.
        task["questions"].append({**task["questions"][-1], "revision": self.question["revision"] + 1,
                                  "status": "open", "resolution": None})
        S.save_task(self.project, task)
        self.refused("current answered")
        task["questions"].pop()
        S.save_task(self.project, task)
        receipt = json.loads(self.request()["stdout"])
        self.assertTrue(receipt["question_context_only"])
        self.assertIsNone(receipt["option_key"])
        self.assertEqual(S.load_task(self.project, self.slug)["questions"][-1]["resolution"]["disposition"], "superseded")

    def test_reaffirmation_does_not_refresh_hold_or_pr_authority(self):
        self.decide()
        self.at = "2026-09-07T20:08:00+00:00"
        self.latest = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "I already gave the go-ahead.",
                                wake_blocked=False)
        self.pull["updatedAt"] = "2026-09-07T20:07:00Z"
        self.refused("stale")
        self.pull["updatedAt"] = "2026-09-07T20:02:00Z"
        T.set_hold_merge(self.project, self.slug, "Review the story")
        self.refused("stale")

    def test_recorded_active_pr_cannot_be_replaced_by_another_on_the_same_branch(self):
        task = S.load_task(self.project, self.slug)
        task["prs"] = [235, 236]
        S.save_task(self.project, task)
        self.refused("active PR")

    def test_decision_for_a_different_pr_cannot_use_this_prs_presentation(self):
        self.decide()
        task = S.load_task(self.project, self.slug)
        task["questions"][-1]["detail"] = "May I merge https://github.com/team/project/pull/236?"
        S.save_task(self.project, task)
        self.refused("different PR")

    def test_latest_operator_context_and_presentation_require_original_authorship(self):
        self.decide()
        self.latest = T.message(self.project, self.slug, "l3", "The operator approved the merge.")
        self.refused("latest operator message")
        self.latest = None
        self.presentation = T.message(self.project, self.slug, "l2", self.pull["url"], by="l3")
        self.refused("owner's presentation")

    def test_interrupted_same_reason_hold_cannot_reuse_older_generation(self):
        with mock.patch.object(S, "append_event", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                T.set_hold_merge(self.project, self.slug, "Review the story")
        self.refused("matching recorded generation")

    def test_exact_grammar_and_existing_role_boundaries(self):
        for extra in (["--off"], ["--why", "new reason"], ["--actor", "burak"], ["--file", "/tmp/approval"],
                      ["--question", "a" * 32], ["--revision", "1"],
                      ["--question", "a" * 32, "--revision", "0"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.request(self.args() + extra)
        for flag, value in (("--pr-number", "0"), ("--head", "short"), ("--reason", " "), ("--approval", "bad"),
                            ("--presentation", "bad"), ("--latest-operator", "bad")):
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
