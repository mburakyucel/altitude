"""#302: accepted Stop and a newer worker retain ownership over late completion results."""
from unittest import mock
from pathlib import Path

from tests.support import AltitudeCase, add_worktree, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, server, state as S, tasks as T


class TestStopResults(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.worker = FakeL2()
        self.worker.install(self)
        self.patch(server, "log")
        self.report_turn = self.patch(server, "report_turn")

    def running(self, engine):
        task = T.new(self.project, "Completion race", "Review a proposal.")
        worktree = add_worktree(self.repo, task["slug"])
        result = self.worker.start_l2(engine, task["slug"], "Review a proposal.", cwd=worktree)
        worker = result["agent"]
        task = T.dispatch(self.project, task["slug"], attempt=1, session_id=worker["sessionId"],
                          agent_id=worker["id"], worktree=str(worktree), branch=f"worktree-{task['slug']}",
                          l2_engine=engine)
        self.worker.workers[worker["id"]].update(state="done", status="exited")
        return task

    def stop(self, task, *, complete):
        request = dispatch.request_task_operation(self.project, task["slug"], "stop", "Stop this work",
                                                  actor=T.OPERATOR_MESSAGE_ROLE, generation=task["agent_id"])
        if complete:
            dispatch.run_task_operation(self.project, task["slug"])
        return request["request"]["id"]

    def assert_stopped(self, task, *, complete):
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["state"], "blocked" if complete else "running")
        self.assertEqual(T.steering_view(current, S.read_events(self.project, task["slug"]))["state"],
                         "stopped" if complete else "stopping")
        self.assertEqual(current["session_id"], task["session_id"])
        self.assertIsNone(current.get("verified"))
        self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())
        self.report_turn.assert_not_called()
        if not complete:
            dispatch.run_task_operation(self.project, task["slug"])

    def test_stop_accepted_or_completed_during_verification_preserves_the_hold(self):
        for engine in config.ENGINES:
            for verdict in ("done", "blocked", "missing", "fault"):
                for complete in (False, True):
                    with self.subTest(engine=engine, verdict=verdict, complete=complete):
                        task = self.running(engine)

                        def verify(*_):
                            self.stop(task, complete=complete)
                            return {"verdict": verdict, "problems": [], "fault": "fixture verification error",
                                    "report": {"blocked": "Report asks for input"}}

                        with mock.patch.object(server.verify, "verify", side_effect=verify):
                            server._on_l2_finished(self.project, {"task": task, "agent": {"state": "done"}})
                        self.assert_stopped(task, complete=complete)

    def test_late_report_cannot_attach_to_a_resumed_worker_in_the_same_session(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.running(engine)

                def verify(*_):
                    self.stop(task, complete=True)
                    replacement = self.worker.resume_l2(engine, task["slug"], task["session_id"], "Continue")
                    T.resume(self.project, task["slug"], agent_id=replacement["agent"]["id"],
                             session_id=task["session_id"])
                    return {"verdict": "done", "problems": []}

                with mock.patch.object(server.verify, "verify", side_effect=verify):
                    server._on_l2_finished(self.project, {"task": task, "agent": {"state": "done"}})
                current = S.load_task(self.project, task["slug"])
                self.assertEqual(current["state"], "running")
                self.assertEqual(current["session_id"], task["session_id"])
                self.assertNotEqual(current["agent_id"], task["agent_id"])
                self.assertIsNone(current.get("verified"))
                self.report_turn.assert_not_called()

    def test_stop_accepted_before_no_code_finalization_prevents_archival(self):
        for engine in config.ENGINES:
            for complete in (False, True):
                with self.subTest(engine=engine, complete=complete):
                    task = self.running(engine)
                    T.done(self.project, task["slug"], actor="l2", digest="Proposal reviewed")
                    task = S.load_task(self.project, task["slug"])
                    finalize = T.finalize_completion

                    def finish(*args, **kwargs):
                        self.stop(task, complete=complete)
                        return finalize(*args, **kwargs)

                    with mock.patch.object(T, "finalize_completion", side_effect=finish):
                        server._on_l2_finished(self.project, {"task": task, "agent": {"state": "done"}})
                    self.assert_stopped(task, complete=complete)
                    self.assertIn("completion_requested", S.load_task(self.project, task["slug"]))

    def test_current_report_and_no_code_completion_still_finish(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.running(engine)
                with mock.patch.object(server.verify, "verify", return_value={"verdict": "done", "problems": []}):
                    server._on_l2_finished(self.project, {"task": task, "agent": {"state": "done"}})
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")
                self.report_turn.assert_called()
                self.report_turn.reset_mock()
                task = self.running(engine)
                T.done(self.project, task["slug"], actor="l2", digest="Proposal reviewed")
                server._on_l2_finished(self.project, {"task": S.load_task(self.project, task["slug"]),
                                                    "agent": {"state": "done"}})
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "done")
                self.assertTrue((S.archive_dir(self.project) / task["slug"]).is_dir())

    def test_no_code_validation_failure_is_not_hidden_as_a_lifecycle_race(self):
        task = self.running(config.ENGINES[0])
        T.done(self.project, task["slug"], actor="l2", digest="Proposal reviewed")
        (Path(task["worktree"]) / "README.md").write_text("Unexpected code change after completion request\n")
        with self.assertRaisesRegex(T.TransitionError, "uncommitted changes"):
            server._on_l2_finished(self.project, {"task": S.load_task(self.project, task["slug"]),
                                                "agent": {"state": "done"}})
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

    def test_continuation_supersedes_the_stopped_workers_no_code_completion(self):
        for engine in config.ENGINES:
            for has_report in (False, True):
                with self.subTest(engine=engine, has_report=has_report):
                    task = self.running(engine)
                    T.done(self.project, task["slug"], actor="l2", digest="Old completed proposal")
                    self.stop(task, complete=True)
                    correction = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE,
                                           "Continue with the corrected scope.",
                                           stop_id=S.load_task(self.project, task["slug"])["stop_id"])
                    claim = T.claim_resume(self.project, task["slug"])
                    replacement = self.worker.resume_l2(engine, task["slug"], task["session_id"], correction["text"])
                    T.resume(self.project, task["slug"], agent_id=replacement["agent"]["id"],
                             session_id=task["session_id"], expected_claim=claim["id"], input_delivered=True)
                    current = S.load_task(self.project, task["slug"])
                    self.assertNotIn("completion_requested", current)
                    item = {"task": current, "agent": {"id": current["agent_id"], "state": "done"}}
                    if not has_report:
                        item["died"] = True
                    with mock.patch.object(server.verify, "verify", return_value={"verdict": "done", "problems": []}) as verify:
                        server._on_l2_finished(self.project, item)
                    self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported" if has_report else "blocked")
                    self.assertEqual(verify.call_count, 1 if has_report else 0)
                    self.assertFalse((S.archive_dir(self.project) / task["slug"]).exists())
