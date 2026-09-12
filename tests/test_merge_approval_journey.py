"""Recorded UI authority crosses HTTP, recovery, the coordinator broker and ordinary landing.

Only external engine execution and hosted GitHub responses are fixtures. Interpreting the
operator's words is coordinator judgment; these tests prove persistence and authority fences.
"""
import json
import socket
import threading
from datetime import datetime, timedelta
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from tests import test_offline_journeys as journeys
from altitude import config, dispatch, incidents, l3, land, server, state as S, tasks as T


class TestMergeApprovalJourney(AltitudeCase):
    # Reuse the HTTP harness without inheriting and rerunning unrelated journeys.
    request = journeys.TestOfflineJourneys.request
    wait_state = journeys.TestOfflineJourneys.wait_state
    queue = journeys.TestOfflineJourneys.queue
    launch = journeys.TestOfflineJourneys.launch
    join_background = journeys.TestOfflineJourneys.join_background

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
                    headRefOid=prepared["head"], updatedAt=self.at)
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
        args = ["task", "hold-merge", slug, "--approval", approval_id, "--presentation", presentation["id"],
                "--latest-operator", approval_id, "--question", question["id"],
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
        presentation = T.message(self.project, slug, "l2", f"Reviewed and green: {pull['url']} at {prepared['head']}.")
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
        args = ["task", "hold-merge", slug, "--approval", approval, "--presentation", presentation["id"],
                "--latest-operator", reaffirmation["id"], "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "Original UI merge approval remains valid; the latest operator message reaffirms it."]
        stale = list(args)
        stale[stale.index("--latest-operator") + 1] = approval
        self.assertIn("latest operator", self.reconcile(broker, stale).get("error", ""),
                      "reconciliation must inspect later operator corrections")
        self.assertEqual(S.load_task(self.project, slug), before)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, (response, self.logs))
        receipt = json.loads(response["stdout"])
        released = S.load_task(self.project, slug)
        self.assertEqual(released, {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual((receipt["approval"], receipt["presentation"], receipt["pr"], receipt["head"]),
                         (approval, presentation["id"], 101, prepared["head"]))
        self.assertEqual((receipt["latest_operator"], receipt["question"], receipt["revision"], receipt["option_key"]),
                         (reaffirmation["id"], question["id"], question["revision"], approved["decision"]["option_key"]))
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
        # PR #304 sequence: between the owner's presentation and the operator's typed decision the
        # operator asked for a plain explanation and the owner answered without the PR URL. No phrase
        # or adjacency rule breaks that citation; L3 reads the exchange and cites the original presentation.
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Clarify the instruction", "Changes authority; operator security review is required",
            "The clarified instruction.\n")
        presentation = T.message(self.project, slug, "l2", f"Ready for security review: {pull['url']} at {prepared['head']}.")
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
        args = ["task", "hold-merge", slug, "--approval", approval["id"], "--presentation", presentation["id"],
                "--latest-operator", approval["id"], "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "The typed answer authorizes this reviewed PR; the earlier message only asked for an explanation."]
        mistaken = list(args)
        mistaken[mistaken.index("--approval") + 1] = asked["id"]
        self.assertIn("resolved before the PR presentation", self.reconcile(broker, mistaken).get("error", ""),
                      "the explanation request carries the question context but is not its answer")
        self.assertEqual(S.load_task(self.project, slug), before)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, (response, self.logs))
        receipt = json.loads(response["stdout"])
        self.assertEqual(S.load_task(self.project, slug), {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual((receipt["approval"], receipt["presentation"], receipt["question"], receipt["revision"]),
                         (approval["id"], presentation["id"], question["id"], question["revision"]))
        self.assertEqual((receipt["option_key"], receipt["question_context_only"], receipt["authorized_by"]),
                         (None, False, T.OPERATOR_MESSAGE_ROLE))
        self.assertEqual((receipt["pr"], receipt["head"]), (101, prepared["head"]))

    def test_project_authorized_integration_preserves_questions_and_normal_landing(self):
        initial, slug, worktree, gh, prepared, pull = self.prepare_held_pr(
            "Integrate the approved story", "Review the story", "The approved story.\n")
        presentation = T.message(self.project, slug, "l2", pull["url"])
        self.tick()
        original = l3.chat_log(self.project, "user", "You can merge it and resolve integration within this outcome.",
                               trigger="chat", turn_id="a" * 12)
        self.tick()
        # A later PR update needs current owner evidence, while the original operator source remains.
        pull["updatedAt"] = self.at
        S.write_json(gh / "pr.json", pull)
        self.tick()
        integrated = T.message(self.project, slug, "l2", f"Integration reviewed: {pull['url']} head {prepared['head']}")
        question = self.escalate(slug, "Clarify a separate remaining detail", "Discuss the detail.")
        broker = self.broker()
        args = ["task", "hold-merge", slug, "--source", "project", "--approval", original["turn_id"],
                "--latest-operator", original["turn_id"], "--presentation", presentation["id"],
                "--integration-presentation", integrated["id"], "--pr-number", "101", "--head", prepared["head"],
                "--reason", "Original project decision delegates integration within this PR; the owner verified that scope."]
        before = S.load_task(self.project, slug)
        response = self.reconcile(broker, args)
        self.assertEqual(response.get("returncode"), 0, response)
        receipt = json.loads(response["stdout"])
        self.assertEqual(S.load_task(self.project, slug), {**before, "hold_merge": None, "merge_approval": receipt})
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["id"], question["id"])
        self.assertEqual(S.load_task(self.project, slug)["questions"][-1]["status"], "open")
        self.assertEqual(receipt["source"], "project")
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        (gh / "merge_git.txt").write_text("advance the local remote\n")
        # Resolve the unrelated question before owner continuation; release itself grants no answer.
        self.tick()
        answer = l3.chat_log(self.project, "user", "The separate detail is settled.", trigger="chat", turn_id="b" * 12)
        T.resolve_question(self.project, slug, question["id"], question["revision"], answer["turn_id"],
                           source="project", disposition="answered", expected_attempt=initial["attempt"],
                           reason="The operator settled the detail.")
        self.request("/api/task/action", {"project": self.project, "slug": slug, "action": "resume", "reason": "Continue approved delivery"})
        self.wait_state(slug, "running")
        (gh / "checks.json").write_text('[{"bucket": "fail"}]')
        self.assertFalse(land.land("test: preserve normal checks", cwd=worktree, merge=True, wait=0)["merged"])
        (gh / "checks.json").write_text('[{"bucket": "pass"}]')
        self.assertTrue(land.land("test: deliver approved integration", cwd=worktree, merge=True, wait=0)["merged"])
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), "The approved story.\n")

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
