"""Recorded UI authority crosses HTTP, recovery, the coordinator broker and ordinary landing.

Only external engine execution and hosted GitHub responses are fixtures. Interpreting the
operator's words is coordinator judgment; these tests prove persistence and authority fences.
"""
import json
import socket
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from tests import test_land, test_offline_journeys as journeys
from altitude import config, dispatch, incidents, l3, land, server, state as S, tasks as T


class TestMergeApprovalJourney(AltitudeCase):
    # Reuse the HTTP harness without inheriting and rerunning unrelated journeys.
    request = journeys.TestOfflineJourneys.request
    wait_state = journeys.TestOfflineJourneys.wait_state
    queue = journeys.TestOfflineJourneys.queue
    launch = journeys.TestOfflineJourneys.launch
    join_background = journeys.TestOfflineJourneys.join_background
    fake_runner = test_land.TestLand.fake_runner
    record_commands = test_land.TestLand.record_commands
    local_policy = test_land.TestLand.local_policy

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.register(self.project, routing=[[{"engine": config.ENGINES[-1], "model": "fixture-model"}]], wip=1)
        self.engine = FakeL2()
        self.engine.install(self)
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.join_background)
        self.at = "2026-09-09T07:00:00+00:00"
        self.patch(S, "now", side_effect=lambda: self.at)
        self.patch(T, "_conversation_time", side_effect=lambda: self.at)

    def tick(self):
        self.at = (datetime.fromisoformat(self.at) + timedelta(seconds=1)).isoformat()

    def escalate(self, slug, question, recommendation, label="Use recommendation"):
        self.tick()
        T.block(self.project, slug, question, actor="l2", updates={"waiting_on": "l3"})
        self.tick()
        result = T.escalate(self.project, slug, question, recommendation=recommendation,
                            recommendation_label=label)
        return result["questions"][-1]

    def choose(self, slug, question):
        self.tick()
        response = self.request("/api/decide", {"project": self.project, "slug": slug,
                                "question_id": question["id"], "revision": question["revision"]})
        self.wait_state(slug, "running")
        return response

    def prepare_held_pr(self, request, hold, content):
        git("config", f"url.{self.tmp / 'origin.git'}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        initial = self.launch(self.queue(request))
        slug, worktree = initial["slug"], Path(initial["worktree"])
        T.set_hold_merge(self.project, slug, hold)
        (worktree / "README.md").write_text(content)
        git("add", "README.md", cwd=worktree)
        gh = self.fake_gh()
        for key, value in dispatch.l2_env(self.project, slug, initial["attempt"]).items():
            self.setenv(key, value)
        prepared = land.land("test: prepare a held change", cwd=worktree, wait=0)
        self.assertEqual((prepared["pr"], prepared["merged"], prepared["checks"]), (101, False, "pass"))
        self.tick()
        pull = S.read_json(gh / "pr.json")
        pull.update(url="https://github.com/team/demo/pull/101", isDraft=False, isCrossRepository=False,
                    headRefOid=prepared["head"])
        S.write_json(gh / "pr.json", pull)
        self.tick()
        return initial, slug, worktree, gh, prepared, pull

    def broker(self):
        # Git's transport rewrite supports local landing; the broker observes the canonical origin.
        git("remote", "set-url", "origin", "git@github.com:team/demo.git", cwd=self.repo)
        broker = server.start_l3_verb_broker(self.project, self.tmp / "approval.sock")
        self.addCleanup(server.stop_l3_verb_broker, broker)
        return broker

    def reconcile(self, broker, arguments):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(broker.socket_path))
            client.sendall((json.dumps({"kind": "alt", "args": arguments}) + "\n").encode())
            return json.loads(client.makefile().readline())

    def late_presentation_journey(self, *, renew_hold, quick):
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Review the final story", "Review the story before merging", "The final story.\n")
        question = self.escalate(slug, "May the owner merge the final story?",
                                 f"Merge {pull['url']} after review.", "Approve merge")
        if renew_hold:
            self.tick()
            T.set_hold_merge(self.project, slug, "Security review of the final story")
        self.tick()
        pull["updatedAt"] = self.at
        S.write_json(gh / "pr.json", pull)
        self.tick()
        presentation = T.message(self.project, slug, "l2", f"Ready for review: {pull['url']} at {prepared['head']}")
        if quick:
            approval_id = self.choose(slug, question)["decision"]["message_id"]
        else:
            self.tick()
            approval = self.request("/api/l2/message", {"project": self.project, "slug": slug,
                "question_id": question["id"], "revision": question["revision"],
                "text": "You can merge the final reviewed PR."})["message"]
            self.wait_state(slug, "running")
            approval_id = approval["id"]
            self.tick()
            T.resolve_question(self.project, slug, question["id"], question["revision"], approval_id,
                               disposition="answered", reason="Merge the final reviewed PR", expected_attempt=1)
        before = S.load_task(self.project, slug)
        conversation = (S.task_dir(self.project, slug) / "conversation.jsonl").read_bytes()
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--approval", approval_id, "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "The original operator answer approves the final presentation of this PR."]
        for flag, value in (("--approval", "0" * 32), ("--question", "0" * 32),
                            ("--revision", str(question["revision"] + 1)), ("--head", "e" * 40),
                            ("--pr-number", "102")):
            invalid = list(args)
            invalid[invalid.index(flag) + 1] = value
            with self.subTest(flag=flag):
                self.assertNotEqual(self.reconcile(broker, invalid).get("returncode"), 0)
                self.assertEqual(S.load_task(self.project, slug), before)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, response)
        receipt = json.loads(response["stdout"])
        self.assertEqual(S.load_task(self.project, slug), {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual((S.task_dir(self.project, slug) / "conversation.jsonl").read_bytes(), conversation)
        self.assertEqual((receipt["approval"], receipt["question"], receipt["revision"], receipt["hold_id"]),
                         (approval_id, question["id"], question["revision"], before["hold_merge_id"]))
        self.assertEqual((receipt["head"], receipt["option_key"], receipt["question_context_only"]),
                         (prepared["head"], "recommended" if quick else None, False))
        self.assertLess(question["asked"], presentation["at"])
        if renew_hold:
            self.assertLess(question["asked"], receipt["hold_at"])
        else:
            self.assertLess(receipt["hold_at"], question["asked"])
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        (gh / "merge_git.txt").write_text("advance the local remote\n")
        (gh / "checks.json").write_text('[{"bucket": "fail"}]')
        self.assertFalse(land.land("test: retain current checks", cwd=worktree, merge=True, wait=0)["merged"])
        (gh / "checks.json").write_text('[{"bucket": "pass"}]')
        self.assertTrue(land.land("test: land final reviewed story", cwd=worktree, merge=True, wait=0)["merged"])
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), "The final story.\n")

    def test_question_before_hold_and_final_presentation_accepts_original_ui_consent(self):
        self.late_presentation_journey(renew_hold=True, quick=True)

    def test_question_after_hold_before_final_presentation_accepts_original_typed_consent(self):
        self.late_presentation_journey(renew_hold=False, quick=False)

    def test_ui_escalation_reaffirmation_fault_recovery_and_normal_land(self):
        # I-20260909-074919: a UI choice after L3 escalation is original operator evidence.
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Review the story", "Review the story before merging", "The reviewed story.\n")
        question = self.escalate(slug, "May the owner merge the reviewed story?", f"Merge {pull['url']}.", "Approve merge")
        self.assertEqual(question["asked_by"], "l3")
        approved = self.choose(slug, question)
        approval = approved["decision"]["message_id"]
        self.assertEqual(approved["decision"]["disposition"], "answered")
        held = S.load_task(self.project, slug)
        self.assertTrue(held["hold_merge"], "a saved UI decision does not itself release a hold")
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("test: held owner cannot merge", cwd=worktree, merge=True, wait=0)

        manual = self.escalate(slug, "Can you manually release the hold?", "Run the manual hold-release command.")
        self.tick()
        reaffirmation = self.request("/api/l2/message", {"project": self.project, "slug": slug,
            "question_id": manual["id"], "revision": manual["revision"],
            "text": "I clearly gave you the go. Report the failure and apply my recorded merge decision."})["message"]
        self.wait_state(slug, "running")
        T.resolve_question(self.project, slug, manual["id"], manual["revision"], reaffirmation["id"],
                           source="task", disposition="superseded", expected_attempt=initial["attempt"],
                           reason="The existing approval supersedes the request for manual operator repair.")
        self.tick()
        fault = incidents.system_fault("recorded-approval", "Recorded UI approval could not release the hold",
                                       project=self.project, task=slug)
        self.assertTrue(fault["incident"])
        before = S.load_task(self.project, slug)
        self.assertEqual((before["state"], before["fault"]), ("blocked", "recorded-approval"))
        before_messages = T.task_messages(self.project, slug)
        self.assertEqual(next(q for q in reversed(before["questions"]) if q["id"] == manual["id"])
                         ["resolution"]["disposition"], "superseded")
        engine_calls = len(self.engine.calls)
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--approval", approval, "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "Original UI merge approval remains valid; the latest operator message reaffirms it."]
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, (response, self.logs))
        receipt = json.loads(response["stdout"])
        released = S.load_task(self.project, slug)
        self.assertEqual(released, {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual((receipt["approval"], receipt["pr"], receipt["head"]),
                         (approval, 101, prepared["head"]))
        self.assertEqual((receipt["question"], receipt["revision"], receipt["option_key"]),
                         (question["id"], question["revision"], approved["decision"]["option_key"]))
        self.assertEqual(receipt["authorized_by"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(T.task_messages(self.project, slug), before_messages)
        self.assertEqual(len(self.engine.calls), engine_calls, "release neither resumes the faulted owner nor changes its session")
        self.assertIn("no active merge hold", self.reconcile(broker, args).get("error", ""))
        self.assertEqual([e for e in S.read_events(self.project, slug) if e["kind"] == "release-merge"],
                         [{"kind": "release-merge", **receipt}])
        self.assertEqual(S.load_task(self.project, slug), released)

        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        self.tick()
        self.request("/api/task/action", {"project": self.project, "slug": slug, "action": "resume",
                                         "reason": "The approval repair is applied and its release receipt is verified"})
        resumed = self.wait_state(slug, "running")
        for key in ("session_id", "attempt", "worktree", "branch", "l2_engine"):
            self.assertEqual(resumed[key], initial[key], key)
        self.assertFalse(resumed.get("fault"))
        (gh / "merge_git.txt").write_text("advance the local remote\n")
        (gh / "checks.json").write_text('[{"bucket": "fail"}]')
        refused = land.land("test: recheck the authorized story", cwd=worktree, merge=True, wait=0)
        self.assertFalse(refused["merged"], "release preserves normal owner check requirements")
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_log()))
        (gh / "checks.json").write_text('[{"bucket": "pass"}]')
        landed = land.land("test: land the authorized story", cwd=worktree, merge=True, wait=0)
        self.assertTrue(landed["merged"])
        self.assertEqual(landed["head"], receipt["head"])
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), "The reviewed story.\n")
        self.assertEqual((self.repo / "README.md").read_text(), "readme\n")

    def test_typed_answer_after_a_plain_explanation_releases_the_exact_pr(self):
        # The explanation request and the subsequent answer carry distinct authority.
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Clarify the instruction", "Changes authority; operator security review is required",
            "The clarified instruction.\n")
        question = self.escalate(slug, f"Will you complete the held security review and merge {pull['url']}?",
                                 "Review the authority boundary and merge the prepared PR if satisfied.",
                                 "Security review and merge")
        context = {"project": self.project, "slug": slug, "question_id": question["id"], "revision": question["revision"]}
        self.tick()
        asked = self.request("/api/l2/message", {**context, "text": "Simply describe what this change does."})["message"]
        self.wait_state(slug, "running")
        self.tick()
        T.message(self.project, slug, "l2", "It reduces unnecessary items in Needs you. It grants no approval on your behalf.")
        self.tick()
        approval = self.request("/api/l2/message", {**context, "text": "You can merge it"})["message"]
        self.tick()
        T.resolve_question(self.project, slug, question["id"], question["revision"], approval["id"], source="task",
                           disposition="answered", expected_attempt=initial["attempt"],
                           reason="The operator approved merging PR #101. Record the release through L3 before landing.")
        before = S.load_task(self.project, slug)
        self.assertTrue(before["hold_merge"], "the owner's resolution records the decision without releasing the hold")
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--approval", approval["id"], "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "The typed answer authorizes this reviewed PR; the earlier message only asked for an explanation."]
        mistaken = list(args)
        mistaken[mistaken.index("--approval") + 1] = asked["id"]
        self.assertIn("follow the earlier question resolution", self.reconcile(broker, mistaken).get("error", ""),
                      "the explanation request carries the question context but is not its answer")
        self.assertEqual(S.load_task(self.project, slug), before)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, (response, self.logs))
        receipt = json.loads(response["stdout"])
        self.assertEqual(S.load_task(self.project, slug), {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual((receipt["approval"], receipt["question"], receipt["revision"]),
                         (approval["id"], question["id"], question["revision"]))
        self.assertEqual((receipt["option_key"], receipt["question_context_only"], receipt["authorized_by"]),
                         (None, False, T.OPERATOR_MESSAGE_ROLE))
        self.assertEqual((receipt["pr"], receipt["head"]), (101, prepared["head"]))

    def test_project_approval_preserves_an_independent_question(self):
        _, slug, _, _, prepared, _ = self.prepare_held_pr(
            "Integrate the approved story", "Review the story", "The approved story.\n")
        self.tick()
        original = l3.chat_log(self.project, "user", "You can merge it and resolve integration within this outcome.",
                               trigger="chat", turn_id="a" * 12)
        question = self.escalate(slug, "Clarify a separate remaining detail", "Discuss the detail.")
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--source", "project", "--approval", original["turn_id"],
                "--pr-number", "101", "--head", prepared["head"],
                "--reason", "Original project decision delegates integration within this PR; the owner verified that scope."]
        before = S.load_task(self.project, slug)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, response)
        receipt = json.loads(response["stdout"])
        self.assertEqual(S.load_task(self.project, slug), {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["id"], question["id"])
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["status"], "open")
        self.assertEqual(receipt["source"], "project")

    def test_approval_survives_conflict_resolution_and_scoped_same_owner_followup(self):
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Deliver the complete story", "Review the story", "The approved story.\n")
        self.tick()
        original = l3.chat_log(self.project, "user",
            "Merge the reviewed story and its agreed conclusion; resolve overlaps within this outcome.",
            trigger="chat", turn_id="c" * 12)
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--source", "project", "--approval", original["turn_id"],
                "--pr-number", "101", "--head", prepared["head"], "--reason",
                "Original decision approves the story and its conclusion; this PR delivers the reviewed story."]
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, response)
        receipt = json.loads(response["stdout"])
        self.tick()
        l3.chat_log(self.project, "user", "Explain next week's unrelated roadmap later.",
                    trigger="chat", turn_id="d" * 12)
        T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, "Thanks for the update.", wake_blocked=False)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)

        # Real concurrent main edit produces a conflict; rebasing preserves the task's provenance.
        (self.repo / "README.md").write_text("The shared introduction.\n")
        git("add", "README.md", cwd=self.repo)
        git("commit", "-qm", "fixture: concurrent introduction", cwd=self.repo)
        git("push", "origin", "main", cwd=self.repo)
        git("fetch", "origin", cwd=worktree)
        conflict = subprocess.run(["git", "rebase", "origin/main"], cwd=worktree, capture_output=True, text=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("README.md", git("diff", "--name-only", "--diff-filter=U", cwd=worktree))
        combined = "The shared introduction.\nThe approved story.\n"
        (worktree / "README.md").write_text(combined)
        git("add", "README.md", cwd=worktree)
        git("-c", "core.editor=true", "rebase", "--continue", cwd=worktree)
        rebased = git("rev-parse", "HEAD", cwd=worktree).strip()
        self.assertNotEqual(rebased, receipt["head"])
        pull.pop("headRefOid")  # The GitHub fixture observes the actual remote head after each push.
        S.write_json(gh / "pr.json", pull)
        (gh / "merge_git.txt").write_text("advance the local remote\n")
        self.local_policy(exit_code=1, output="Ran 1 test in 0.1s\n\nFAILED (failures=1)\n")
        failed = land.land("test: verify rebased story", cwd=worktree, merge=True, wait=0)
        self.assertFalse(failed["merged"])
        self.assertEqual(failed["local_tests"]["head"], rebased)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_log()))
        self.fake_runner("make", script=(
            "import subprocess\n"
            "assert sys.argv[1:] == ['check']\n"
            "assert open('README.md').read() == " + repr(combined) + "\n"
            "assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()\n"
            "print('Ran 1 test in 0.1s\\n\\nOK')\n"))
        first = land.land("test: merge rebased story", cwd=worktree, merge=True, wait=0)
        self.assertTrue(first["merged"])
        self.assertEqual(first["checks"], "local-pass")
        self.assertEqual(first["local_tests"]["head"], rebased)
        self.assertEqual(S.load_task(self.project, slug)["merge_approval"], receipt)
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), combined)

        # Follow-up retains this owner and original requirement, but release remains specific to a PR.
        (worktree / "README.md").write_text(combined + "The agreed conclusion.\n")
        git("add", "README.md", cwd=worktree)
        self.fake_runner("make", 0, "Ran 1 test in 0.1s\n\nOK\n")
        second = land.land("test: deliver the agreed conclusion", cwd=worktree, wait=0)
        self.assertEqual((second["pr"], second["merged"]), (102, False))
        held = S.load_task(self.project, slug)
        self.assertEqual((held["hold_merge"], held["hold_merge_id"]), (receipt["hold"], receipt["hold_id"]))
        for key in ("session_id", "attempt", "worktree", "branch", "l2_engine"):
            self.assertEqual(held[key], initial[key], key)
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("test: previous release is not blanket approval", cwd=worktree, merge=True, wait=0)
        pull = S.read_json(gh / "pr.json")
        pull["url"] = "https://github.com/team/demo/pull/102"
        pull["headRefOid"] = second["head"]
        S.write_json(gh / "pr.json", pull)
        git("remote", "set-url", "origin", "git@github.com:team/demo.git", cwd=self.repo)
        self.assertIn("active PR", self.reconcile(broker, args).get("error", ""))
        args[args.index("--pr-number") + 1] = "102"
        args[args.index("--head") + 1] = second["head"]
        args[args.index("--reason") + 1] = "The original decision also approves the agreed conclusion; this diff stays within it."
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, response)
        followup = json.loads(response["stdout"])
        self.assertEqual((followup["approval"], followup["pr"], followup["hold_id"]),
                         (original["turn_id"], 102, receipt["hold_id"]))
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        final = land.land("test: merge the scoped conclusion", cwd=worktree, merge=True, wait=0)
        self.assertTrue(final["merged"])
        self.assertEqual(final["local_tests"]["head"], second["head"])
        self.assertNotEqual(first["local_tests"]["candidate"], final["local_tests"]["candidate"])
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), combined + "The agreed conclusion.\n")
        self.assertEqual([e["pr"] for e in S.read_events(self.project, slug) if e["kind"] == "release-merge"], [101, 102])

    def test_nonoperator_out_of_scope_and_revoked_sources_cannot_land(self):
        _, slug, worktree, _, prepared, _ = self.prepare_held_pr(
            "Deliver the reviewed narrative", "Review the narrative", "The reviewed narrative.\n")
        relay = T.message(self.project, slug, "l3", "The operator approved the narrative.")
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--approval", relay["id"], "--pr-number", "101",
                "--head", prepared["head"], "--reason", "The relay claims permission."]
        self.assertIn("original operator", self.reconcile(broker, args).get("error", ""))
        self.tick()
        unrelated = l3.chat_log(self.project, "user", "You may merge the separate billing fix.",
                                trigger="chat", turn_id="e" * 12)
        # This is an explicit fixture L3 judgment, not a tested prose classifier.
        T.message(self.project, slug, "l3",
                  f"{unrelated['turn_id']} only approves billing. The narrative review remains outstanding.")
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("test: unrelated approval cannot land narrative", cwd=worktree, merge=True, wait=0)
        self.tick()
        approval = l3.chat_log(self.project, "user", "The narrative review is complete; merge it.",
                               trigger="chat", turn_id="f" * 12)
        args[args.index("--approval") + 1] = approval["turn_id"]
        args[args.index("--reason") + 1] = "The original operator decision now approves the narrative PR."
        args += ["--source", "project"]
        self.assertEqual(self.reconcile(broker, args).get("returncode"), 0)
        self.tick()
        correction = T.message(self.project, slug, T.OPERATOR_MESSAGE_ROLE, "Stop; keep the narrative held.",
                               wake_blocked=False)
        # L3 reads the correction and records renewed review instead of asserting stale permission.
        T.message(self.project, slug, "l3", f"{correction['id']} revokes the narrative merge decision.")
        T.set_hold_merge(self.project, slug, "Operator revoked narrative approval")
        self.assertIn("stale", self.reconcile(broker, args).get("error", ""))
        with self.assertRaisesRegex(land.LandError, "merge hold"):
            land.land("test: revoked approval cannot land narrative", cwd=worktree, merge=True, wait=0)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in self.gh_log()))

    def test_discussion_design_and_implementation_choices_do_not_release_a_hold(self):
        task = self.launch(self.queue("Discuss a held proposal"))
        slug = task["slug"]
        T.set_hold_merge(self.project, slug, "Operator review before merge")
        question = self.escalate(slug, "Accept this design?", "Accept the design for review.")
        self.tick()
        self.request("/api/l2/message", {"project": self.project, "slug": slug,
            "question_id": question["id"], "revision": question["revision"], "text": "How does the design handle failures?"})
        self.wait_state(slug, "running")
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["status"], "open")
        T.block(self.project, slug, question["detail"], actor="l2")
        self.choose(slug, question)
        implementation = self.escalate(slug, "Implement the design and keep its review hold?",
                                       "Implement the design; retain operator review before merge.")
        self.choose(slug, implementation)
        held = S.load_task(self.project, slug)
        self.assertEqual(held["hold_merge"], "Operator review before merge")
        self.assertFalse(held.get("merge_approval"))
        self.assertFalse(any(e["kind"] == "release-merge" for e in S.read_events(self.project, slug)))
        # No model is run here: refusing to assert that either answer authorizes merge remains L3's judgment.
