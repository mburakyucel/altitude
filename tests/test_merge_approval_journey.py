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
from altitude import config, dispatch, incidents, land, server, state as S, tasks as T


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

    def test_ui_escalation_reaffirmation_fault_recovery_and_normal_land(self):
        # I-20260909-074919: a UI choice after L3 escalation is original operator evidence.
        git("config", f"url.{self.tmp / 'origin.git'}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        initial = self.launch(self.queue("Review the story"))
        slug, worktree = initial["slug"], Path(initial["worktree"])
        T.set_hold_merge(self.project, slug, "Review the story before merging")
        (worktree / "README.md").write_text("The reviewed story.\n")
        gh = self.fake_gh()
        for key, value in dispatch.l2_env(self.project, slug, initial["attempt"]).items():
            self.setenv(key, value)
        prepared = land.land("test: prepare a held story", cwd=worktree, wait=0)
        self.assertEqual((prepared["pr"], prepared["merged"], prepared["checks"]), (101, False, "pass"))
        self.tick()
        pull = S.read_json(gh / "pr.json")
        pull.update(url="https://github.com/team/demo/pull/101", isDraft=False, isCrossRepository=False,
                    headRefOid=prepared["head"], updatedAt=self.at)
        S.write_json(gh / "pr.json", pull)
        self.tick()
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
        # Git's transport rewrite supports local landing; the broker observes the canonical origin.
        git("remote", "set-url", "origin", "git@github.com:team/demo.git", cwd=self.repo)
        broker = server.start_l3_verb_broker(self.project, self.tmp / "approval.sock")
        self.addCleanup(server.stop_l3_verb_broker, broker)
        args = ["task", "hold-merge", slug, "--approval", approval, "--presentation", presentation["id"],
                "--latest-operator", reaffirmation["id"], "--question", question["id"],
                "--revision", str(question["revision"]), "--pr-number", "101", "--head", prepared["head"],
                "--reason", "Original UI merge approval remains valid; the latest operator message reaffirms it."]

        def reconcile(arguments):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(str(broker.socket_path))
                client.sendall((json.dumps({"kind": "alt", "args": arguments}) + "\n").encode())
                return json.loads(client.makefile().readline())

        stale = list(args)
        stale[stale.index("--latest-operator") + 1] = approval
        self.assertIn("latest operator", reconcile(stale).get("error", ""),
                      "reconciliation must inspect later operator corrections")
        self.assertEqual(S.load_task(self.project, slug), before)
        response = reconcile(args)
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
        self.assertIn("no active merge hold", reconcile(args).get("error", ""))
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
