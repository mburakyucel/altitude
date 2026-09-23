"""Composed operator journeys with real HTTP, state, routing and local Git.

Only worker/provider execution and hosted GitHub responses are fixtures. These cases prevent
false delivery, lost steering messages and duplicate workers across component boundaries.
"""
import http.client
import json
import threading
import time
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, land, server, state as S, tasks as T


class TestOfflineJourneys(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        # Real routing chooses the configured tier, given only deterministic installation/quota evidence.
        self.register(self.project, routing=[[{"engine": config.ENGINES[-1], "model": "fixture-model"}]])
        self.patch(config, "WIP_PER_MACHINE", 1)
        self.engine = FakeL2()
        self.engine.install(self)
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.join_background)

    def join_background(self):
        for key, thread in list(server._bg.items()):
            if self.project in key:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), key)
        self.assertEqual([line for line in self.logs if "] failed:" in line], [],
                         "a completed background thread must not hide an unexpected workflow exception")

    def request(self, path, body=None, *, status=200):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request("GET" if body is None else "POST", path,
                               body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def wait_state(self, slug, expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = S.load_task(self.project, slug)
            if task["state"] == expected:
                self.join_background()
                return S.load_task(self.project, slug)
            time.sleep(.01)
        self.fail(f"{slug}: expected {expected}; found {task}")

    def queue(self, title):
        result = self.alt("--project", self.project, "task", "new", "--title", title, "Implement and validate the request",
                          "--paths", "README.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def launch(self, task):
        self.request("/api/task/action", {"project": self.project, "slug": task["slug"], "action": "dispatch"})
        task = self.wait_state(task["slug"], "running")
        self.assertEqual(task["attempt"], 1)
        self.assertIn("Auto tier 1", task["routing"])
        self.assertTrue(Path(task["worktree"]).is_relative_to(self.repo / ".claude/worktrees"))
        return task

    def test_delivery_through_http_dispatch_real_git_landing_verified_report_and_archive(self):
        self.delivery()

    def test_replayed_guidance_completes_with_fresh_report_and_no_new_delivery(self):
        self.delivery(continuation="fresh")

    def test_replayed_guidance_acknowledgement_cannot_complete_with_stale_report(self):
        self.delivery(continuation="stale")

    def delivery(self, continuation=None):
        git("config", f"url.{self.tmp / 'origin.git'}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        task = self.launch(self.queue("Deliver one change"))
        slug = task["slug"]
        view = self.request(f"/api/task/{self.project}/{slug}")
        self.assertEqual(view["state"], "running")
        self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
        worktree = Path(task["worktree"])
        (worktree / "README.md").write_text("Delivered and validated.\n")
        git("add", "README.md", cwd=worktree)
        gh = self.fake_gh()
        (gh / "merge_git.txt").write_text("advance the local remote\n")
        for key, value in dispatch.l2_env(self.project, slug, 1).items():
            self.setenv(key, value)
        landed = land.land("test: deliver isolated fixture", cwd=worktree, merge=True, wait=0)
        self.assertEqual(landed["pr"], 101)
        self.assertEqual(git("show", "main:README.md", cwd=self.tmp / "origin.git"), "Delivered and validated.\n")
        self.assertEqual((self.repo / "README.md").read_text(), "readme\n", "landing does not activate the project")
        self.assertTrue(any(call[:2] == ["pr", "checks"] for call in self.gh_log()))
        self.assertTrue(any(call[:2] == ["pr", "merge"] for call in self.gh_log()))
        self.assertEqual(S.load_task(self.project, slug)["state"], "running", "a merged PR alone is not completion")

        oid = git("rev-parse", "main", cwd=self.tmp / "origin.git").strip()
        report = {"landed": {"prs": [{"number": 101, "title": "Deliver isolated fixture", "merged": True,
                                      "merge_sha": oid}], "main_runs": [{"id": "7", "conclusion": "success"}],
                              "deploy": "not-applicable"}, "review": [], "blocked": ""}
        S.write_json(S.task_dir(self.project, slug) / "report.json", report)
        self.engine.workers[task["agent_id"]].update(state="done", status="exited")
        finished = dispatch.poll(self.project)
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0].get("died"))
        if continuation:
            T.message(self.project, slug, "l3", "Keep the validated change; this guidance is already incorporated.", by="l3")
            server.on_l2_finished(self.project, finished[0])
            blocked = S.load_task(self.project, slug)
            self.assertEqual(blocked["state"], "blocked")
            self.assertTrue(blocked.get("report_after"))
            self.assertEqual(next(e["report"] for e in S.read_events(self.project, slug)
                                  if e["kind"] == "report-superseded"), report)
            dispatch.resume(self.project, slug)
            resumed = S.load_task(self.project, slug)
            for key in ("session_id", "attempt", "worktree", "branch"):
                self.assertEqual(resumed[key], task[key])
            self.assertEqual(resumed["prs"], [101])
            self.assertTrue(self.engine.calls[-1]["prompt"].startswith("Message from"))
            self.assertNotIn("Task question", self.engine.calls[-1]["prompt"])
            self.assertFalse(T.report_current(resumed, S.task_dir(self.project, slug) / "report.json"))
            T.message(self.project, slug, "l2", "Guidance already incorporated; delivery is unchanged.", by="l2")
            if continuation == "fresh":
                S.write_json(S.task_dir(self.project, slug) / "report.json", report)
            self.engine.workers[resumed["agent_id"]].update(state="done", status="exited")
            finished = dispatch.poll(self.project)
            self.assertEqual(len(finished), 1)
            self.assertEqual(bool(finished[0].get("died")), continuation == "stale")
            self.assertEqual(len([c for c in self.gh_log() if c[:2] == ["pr", "create"]]), 1)
            if continuation == "stale":
                server.on_l2_finished(self.project, finished[0])
                self.assertEqual(S.load_task(self.project, slug)["fault"], "l2-died")
                self.assertEqual(S.task_dir(self.project, slug).parent, S.tasks_dir(self.project))
                return
        server.on_l2_finished(self.project, finished[0])
        archived = self.request(f"/api/task/{self.project}/{slug}")
        self.assertEqual(archived["state"], "done")
        self.assertEqual(archived["verified"]["verdict"], "ok")
        self.assertEqual(S.task_dir(self.project, slug).parent, S.archive_dir(self.project))
        self.assertIn("PR #101", (S.task_dir(self.project, slug) / "digest.md").read_text())
        self.assertEqual(len(self.engine.calls), 2 if continuation else 1)
        self.assertEqual(S.read_json(S.task_dir(self.project, slug) / "report.json"), report)

    def test_decision_resume_keeps_session_and_late_http_message_for_next_checkpoint(self):
        task = self.launch(self.queue("Resume with steering"))
        slug = task["slug"]
        question = T.block(self.project, slug, "Use the conservative default?", actor="l2",
                           updates={"waiting_on": "burak"}, recommendation="Use the conservative default")["questions"][-1]
        self.assertEqual(T.decisions(self.project)[0]["slug"], slug)
        # This arrives after the durable resume claim took its inbox snapshot.
        self.engine.on_resume = lambda: self.request("/api/l2/message", {
            "project": self.project, "slug": slug, "text": "Also retain the old format"})
        self.request("/api/decide", {"project": self.project, "slug": slug,
                                      "question_id": question["id"], "revision": question["revision"]})
        resumed = self.wait_state(slug, "running")
        self.assertEqual(resumed["session_id"], task["session_id"])
        self.assertEqual(resumed["attempt"], task["attempt"])
        self.assertNotEqual(resumed["agent_id"], task["agent_id"])
        self.assertFalse(resumed.get("resume_claim"))
        self.assertEqual(T.decisions(self.project), [])
        self.assertIn("Use the conservative default", self.engine.calls[-1]["prompt"])
        self.assertNotIn("Also retain", self.engine.calls[-1]["prompt"])
        self.assertEqual([row["text"] for row in T.pending(self.project, slug)], ["Also retain the old format"])
        self.assertEqual(self.engine.workers[task["agent_id"]]["state"], "stopped")
        self.assertEqual(dispatch.resume(self.project, slug), {"already_running": True})
        self.assertEqual(len(self.engine.calls), 2)
        delivered = T.take_inbox(self.project, slug)
        self.assertEqual([row["text"] for row in delivered], ["Also retain the old format"])
        self.assertEqual(T.take_inbox(self.project, slug), [])
        self.assertEqual([row["text"] for row in T.task_messages(self.project, slug)],
                         ["Use the conservative default?", "Use the conservative default?\nUse the conservative default",
                          "Also retain the old format"])

    def test_question_block_survives_old_inbox_and_exit_until_a_new_answer(self):
        # I-20260908-045037: pre-block steering must not relaunch a worker waiting on a new question.
        for waiting_on in ("l3", "burak"):
            with self.subTest(waiting_on=waiting_on):
                task = self.launch(self.queue(f"Question for {waiting_on}"))
                slug = task["slug"]
                T.set_hold_merge(self.project, slug, "Review the proposed experience")
                self.request("/api/l2/message", {"project": self.project, "slug": slug,
                                                "text": "Discuss the choice before implementation"})
                args = ["--project", self.project, "task", "block", slug, "--reason", "Include grouped questions?"]
                if waiting_on == "burak":
                    args.append("--for-burak")
                result = self.alt(*args, env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": slug, "ALTITUDE_ATTEMPT": "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                blocked = S.load_task(self.project, slug)
                self.assertEqual(blocked["waiting_on"], waiting_on)
                self.assertNotIn(slug, dispatch.resume_due(self.project))
                self.assertFalse(server.request_task_resume(self.project, slug))
                self.assertEqual(dispatch.resume(self.project, slug), {"waiting": True}, "a previously queued wake is stale")
                self.assertNotIn(slug, [lease["slug"] for lease in dispatch.leases(self.project)])
                self.engine.workers[task["agent_id"]].update(state="done", status="exited")
                self.assertEqual(dispatch.poll(self.project), [])
                current = S.load_task(self.project, slug)
                self.assertEqual((current["state"], current["blocked_reason"], current["hold_merge"]),
                                 ("blocked", "Include grouped questions?", "Review the proposed experience"))
                self.assertFalse(current.get("fault"))
                self.assertEqual([m["text"] for m in T.pending(self.project, slug)],
                                 ["Discuss the choice before implementation"])

                self.request("/api/l2/message", {"project": self.project, "slug": slug,
                                                "text": "Yes, include grouped questions"})
                resumed = self.wait_state(slug, "running")
                self.assertEqual((resumed["session_id"], resumed["attempt"], resumed["hold_merge"]),
                                 (task["session_id"], task["attempt"], blocked["hold_merge"]))
                self.assertNotEqual(resumed["agent_id"], task["agent_id"])
                self.assertIn("Yes, include grouped questions", self.engine.calls[-1]["prompt"])
                self.assertEqual(T.pending(self.project, slug), [])
                # The authorized turn must record its own wait/completion/report; the old block cannot excuse it.
                self.engine.workers[resumed["agent_id"]].update(state="done", status="exited")
                item = dispatch.poll(self.project)[0]
                self.assertTrue(item["died"])
                server.on_l2_finished(self.project, item)
                self.assertEqual(S.load_task(self.project, slug)["fault"], "l2-died")

    def test_capacity_retry_retains_identity_respects_wip_and_missing_report_never_completes(self):
        task = self.launch(self.queue("Capacity retry"))
        waiting = self.queue("Wait for capacity")
        server.dispatch_waiting(self.project)
        self.assertEqual(S.load_task(self.project, waiting["slug"])["state"], "queued")
        self.assertEqual(len(self.engine.calls), 1)
        self.engine.workers[task["agent_id"]].update(state="failed", status="exited", detail=engines.TEMPORARY_CAPACITY_TEXT)
        item = dispatch.poll(self.project)[0]
        self.assertTrue(item["capacity"])
        server.on_l2_finished(self.project, item)
        held = S.load_task(self.project, task["slug"])
        self.assertEqual(held["state"], "blocked")
        self.assertEqual(held["capacity_retries"], 1)
        self.assertNotIn(task["slug"], dispatch.resume_due(self.project))
        self.assertFalse(held.get("fault"))
        held["resume_after"] = "2000-01-01T00:00:00+00:00"  # advance only the stored deadline, no real wait
        S.save_task(self.project, held)
        self.assertIn(task["slug"], dispatch.resume_due(self.project))
        dispatch.resume(self.project, task["slug"])
        resumed = S.load_task(self.project, task["slug"])
        self.assertEqual((resumed["session_id"], resumed["attempt"], resumed["l2_engine"], resumed["launch_model"]),
                         (task["session_id"], task["attempt"], task["l2_engine"], task["launch_model"]))
        self.assertEqual(dispatch.resume(self.project, task["slug"]), {"already_running": True})
        self.assertEqual(len(self.engine.calls), 2)
        self.engine.workers[resumed["agent_id"]].update(state="done", status="exited")
        died = dispatch.poll(self.project)[0]
        self.assertTrue(died["died"])
        server.on_l2_finished(self.project, died)
        stopped = S.load_task(self.project, task["slug"])
        self.assertEqual((stopped["state"], stopped["fault"]), ("blocked", "l2-died"))
        self.assertEqual(S.task_dir(self.project, task["slug"]).parent, S.tasks_dir(self.project))

    def test_acceptance_recovers_a_lost_immediate_wake_and_coalesces_timer_and_http_retries(self):
        task = self.launch(self.queue("One acceptance wake"))
        slug = task["slug"]
        question = T.block(self.project, slug, "Use the existing index?", actor="l2",
                           updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, recommendation="Use the existing index.")["questions"][-1]
        body = {"project": self.project, "slug": slug, "question_id": question["id"], "revision": 1}
        with mock.patch.object(server, "spawn", return_value=False):
            accepted = self.request("/api/decide", body)
        self.assertIn(slug, dispatch.resume_due(self.project), "the durable request survives a lost immediate scheduler call")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hold_resume():
            started.set()
            release.wait(5)

        self.engine.on_resume = hold_resume
        try:
            self.assertTrue(server.request_task_resume(self.project, slug))
            self.assertTrue(started.wait(5))
            retried = self.request("/api/decide", body)
            self.assertEqual(retried["response"], accepted["response"])
            self.assertFalse(server.request_task_resume(self.project, slug), "timer and HTTP share the active resume claim")
        finally:
            release.set()
        resumed = self.wait_state(slug, "running")
        self.assertEqual((resumed["session_id"], resumed["attempt"]), (task["session_id"], task["attempt"]))
        self.assertEqual(len(self.engine.calls), 2, "one initial launch and one resume")
        self.assertEqual(T.pending(self.project, slug), [])
        operator_rows = [row for row in T.task_messages(self.project, slug) if row["role"] == T.OPERATOR_MESSAGE_ROLE]
        self.assertEqual([row["id"] for row in operator_rows], [accepted["response"]["message_id"]])

    def test_requeued_question_remains_discussable_and_reaches_the_fresh_attempt_with_its_inbox(self):
        for accept in (False, True):
            with self.subTest(accept=accept):
                task = self.launch(self.queue(f"Retained question {accept}"))
                slug = task["slug"]
                question = T.block(self.project, slug, "How long should we retain the index?", actor="l2",
                                   updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, recommendation="Keep fourteen days.")["questions"][-1]
                T.resume(self.project, slug)
                T.block(self.project, slug, "The execution window is exhausted.", actor="altd",
                        updates={"resume_after": "2099-01-01T00:00:00+00:00"})
                engines.remove_l2_worker(task["l2_engine"], task["agent_id"],
                                         job_root=dispatch.l2_job_root(self.project, slug))
                T.requeue(self.project, slug, clear_worker=True)
                target = {"project": self.project, "slug": slug,
                          "question_id": question["id"], "revision": question["revision"]}
                with mock.patch.object(server, "request_task_resume", side_effect=AssertionError("queued work uses normal dispatch")):
                    if accept:
                        response = self.request("/api/decide", target)
                        self.assertEqual(self.request("/api/decide", target)["response"], response["response"])
                        message_id = response["response"]["message_id"]
                    else:
                        response = self.request("/api/l2/message", {**target, "text": "Can we roll back after day fourteen?"})
                        message_id = response["message"]["id"]
                queued = S.load_task(self.project, slug)
                self.assertEqual(queued["state"], "queued")
                self.assertFalse(queued.get("resume_after") or queued.get("resume_request"))
                self.assertEqual(queued["questions"][-1]["status"], "open")
                self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [message_id])
                self.request("/api/task/action", {"project": self.project, "slug": slug, "action": "dispatch"})
                fresh = self.wait_state(slug, "running")
                self.assertEqual(fresh["attempt"], task["attempt"] + 1)
                self.assertNotEqual(fresh["agent_id"], task["agent_id"])
                self.assertIn(question["id"], self.engine.calls[-1]["prompt"])
                self.assertIn(question["detail"], self.engine.calls[-1]["prompt"])
                self.assertIn("Response in message" if accept else question["detail"], self.engine.calls[-1]["prompt"])
                self.assertIn(message_id, self.engine.calls[-1]["prompt"])
                self.assertIn("Keep fourteen days." if accept else "Can we roll back after day fourteen?",
                              self.engine.calls[-1]["prompt"])
                self.assertEqual(fresh["message_deliveries"][message_id]["agent_id"], fresh["agent_id"])
                self.assertEqual(T.take_inbox(self.project, slug), [])
                if accept:
                    self.assertEqual(self.request("/api/decide", target)["response"], response["response"])
                    self.assertEqual(T.pending(self.project, slug), [])
                T.reject(self.project, slug, "Fixture complete")

    def test_exhausted_model_falls_back_from_saved_dirty_work_without_a_reset_or_fault(self):
        self.register(self.project, routing=config.parse_routing("claude:fable>codex"))
        task = self.launch(self.queue("Continue saved work after allowance exhaustion"))
        slug, worktree = task["slug"], Path(task["worktree"])
        (worktree / "README.md").write_text("Saved uncommitted implementation\n")
        (S.task_dir(self.project, slug) / "progress.md").write_text("Next: validate the saved implementation.")
        T.set_hold_merge(self.project, slug, "Operator review is still required")
        self.engine.workers[task["agent_id"]].update(state="done", status="exited",
            detail="You've reached your Fable limit. Switch to another model, or manage usage credits to continue.")
        finished = dispatch.poll(self.project)
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["limited"]["model"], finished[0]["limited"]["until"]), ("fable", None))
        server.on_l2_finished(self.project, finished[0])
        queued = S.load_task(self.project, slug)
        self.assertEqual(queued["state"], "queued")
        self.assertFalse(queued.get("resume_after") or queued.get("fault") or queued.get("engine"))
        self.assertFalse(engines.usage_limit_path().exists())
        dispatch.run(self.project, slug)
        fresh = S.load_task(self.project, slug)
        self.assertEqual((fresh["state"], fresh["attempt"], fresh["l2_engine"]), ("running", 2, "codex"))
        self.assertNotEqual(fresh["session_id"], task["session_id"])
        self.assertEqual((fresh["worktree"], fresh["branch"], fresh["paths"]),
                         (task["worktree"], task["branch"], task["paths"]))
        self.assertEqual(fresh["hold_merge"], "Operator review is still required")
        self.assertEqual((worktree / "README.md").read_text(), "Saved uncommitted implementation\n")
        self.assertIn("Next: validate the saved implementation.", self.engine.calls[-1]["prompt"])
        self.assertIsNone(self.engine.calls[-1]["session_id"])

    def test_failed_launch_records_fault_and_explicit_retry_creates_only_one_worker(self):
        task = self.queue("Retry failed launch")
        self.engine.outcomes.append({"returncode": 0, "agent": None})
        server.dispatch_waiting(self.project)
        failed = S.load_task(self.project, task["slug"])
        self.assertEqual((failed["state"], failed["fault"]), ("blocked", "dispatch-failed"))
        self.assertFalse(failed.get("dispatching"))
        self.assertFalse(failed.get("agent_id"))
        self.assertEqual(self.engine.workers, {})
        server.dispatch_waiting(self.project)
        self.assertEqual(len(self.engine.calls), 1)
        self.request("/api/task/action", {"project": self.project, "slug": task["slug"],
                                          "action": "resume", "reason": "The local launch fixture is ready"})
        self.wait_state(task["slug"], "queued")
        running = self.launch(task)
        self.assertEqual(running["attempt"], 1, "the failed unbound launch consumed no attempt")
        self.assertEqual(len(self.engine.calls), 2)
        self.assertEqual(len(self.engine.workers), 1)
