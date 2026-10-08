"""Concrete requests queue one L2 directly; no classifier or proposal stages exist."""
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, server, state as S, tasks as T


class TestDirectDispatch(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.quiet_engines()

    def test_overlapping_queued_tasks_both_launch_and_second_brief_names_overlap(self):
        self.register(self.project)
        first = T.new(self.project, "First", "request", paths=["README.md", "docs/"])
        second = T.new(self.project, "Second", "request", paths=["README.md", "docs/ARCHITECTURE.md"])
        launches = []

        def launch(*args, **kwargs):
            launches.append((args, kwargs))
            return {"returncode": 0, "agent": {"id": f"agent-{len(launches)}",
                                               "sessionId": f"session-{len(launches)}"}}

        with mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", side_effect=launch):
            server.dispatch_waiting(self.project)

        self.assertEqual(len(launches), 2)
        for task in (first, second):
            self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        brief = (S.task_dir(self.project, second["slug"]) / "brief.md").read_text()
        self.assertIn("Shared with `first` on README.md, docs/ARCHITECTURE.md", brief)

    def test_only_machine_cap_applies_even_with_a_stored_project_override(self):
        self.register(self.project, wip=1)
        self.assertEqual(config.WIP_PER_MACHINE, 80)
        for index in range(8):
            self.assertIsNone(dispatch.wip_hold(self.project))
            task = T.new(self.project, f"Worker {index}", "request")
            task["state"] = "running"
            S.save_task(self.project, task)
        self.assertIsNone(dispatch.wip_hold(self.project))
        self.register("another")
        for project_index in range(9):
            project = f"parallel-{project_index}"
            self.register(project)
            for index in range(8):
                self.assertIsNone(dispatch.wip_hold(project))
                task = T.new(project, f"Worker {index}", "request")
                task["state"] = "running"
                S.save_task(project, task)
        self.assertEqual(dispatch.wip_hold("another"), "WIP limit: 80 running on this machine")

    def test_new_task_is_immediately_queued_without_pipeline_metadata(self):
        task = T.new(self.project, "Fix the focused bug", "Fix it and test it.", actor="burak")

        self.assertEqual(task["state"], "queued")
        self.assertEqual(T.decisions(self.project), [])

    def test_task_source_is_only_chat_or_explicit_recovery(self):
        with self.assertRaisesRegex(T.TransitionError, "source must be chat or recovery"):
            T.new(self.project, "Invented source", "request", source="autonomous-backlog")

    def test_waiting_task_dispatches_directly_to_l2(self):
        queued = {"slug": "direct-one", "state": "queued"}
        result = {"attempt": 1, "agent": {"id": "l2-agent"}}
        with mock.patch.object(S, "list_tasks", return_value=[queued]), \
             mock.patch.object(server.dispatch, "wip_hold", return_value=None), \
             mock.patch.object(server.dispatch, "run", return_value=result) as run:
            server.dispatch_waiting(self.project)
        run.assert_called_once_with(self.project, "direct-one")

    def test_launch_failure_holds_dispatch_without_orphaning_the_task(self):
        task = T.new(self.project, "Engine launch fails", "Try to dispatch it.", actor="burak")
        task["dispatching"] = S.now()
        S.save_task(self.project, task)
        with mock.patch.object(S, "list_tasks", return_value=[task]), \
             mock.patch.object(server.dispatch, "wip_hold", return_value=None), \
             mock.patch.object(server.dispatch, "run", side_effect=RuntimeError("engine unavailable")), \
             mock.patch.object(server.incidents, "system_fault") as fault:
            server.dispatch_waiting(self.project)

        waiting = S.load_task(self.project, task["slug"])
        self.assertEqual(waiting["state"], "queued")
        self.assertIsNone(waiting.get("dispatching"))
        self.assertEqual(S.read_events(self.project, task["slug"])[-1]["kind"], "dispatch-failed")
        fault.assert_called_once_with(
            "dispatch-failed",
            f"{self.project}/{task['slug']}: engine unavailable",
            project=self.project,
            task=task["slug"],
            expected_block_id=None,
            step="the L2 launch",
        )

    def test_zero_exit_without_a_concrete_agent_never_marks_running(self):
        task = T.new(self.project, "Missing launch identity", "Try to dispatch it.", actor="burak")
        fake = {"stdout": "started", "stderr": "", "returncode": 0, "agent": None}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake), \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(dispatch.DispatchFailure, "concrete worker id and session id"):
                dispatch.run(self.project, task["slug"])

        waiting = S.load_task(self.project, task["slug"])
        self.assertEqual(waiting["state"], "queued")
        self.assertIsNone(waiting.get("dispatching"))
        fault.assert_called_once()

    def test_successful_launch_binds_the_attempt_given_to_the_l2(self):
        task = T.new(self.project, "Concrete launch identity", "Dispatch it.", actor="burak")
        fake = {"stdout": "started", "stderr": "", "returncode": 0,
                "agent": {"id": "agent-1", "sessionId": "session-1"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake) as launch:
            dispatch.run(self.project, task["slug"])

        running = S.load_task(self.project, task["slug"])
        self.assertEqual((running["state"], running["agent_id"], running["session_id"]),
                         ("running", "agent-1", "session-1"))
        self.assertEqual(running["attempt"], 1)
        env = launch.call_args.kwargs["extra_env"]
        self.assertEqual((env["ALTITUDE_ATTEMPT"], env["ALTITUDE_SESSION_KEY"]),
                         ("1", S.session_key(self.project, task["slug"], 1)))
        self.assertEqual(running["l2_engine"], "codex")
        self.assertIn("configured tie order", running["routing"])

    def test_l2_preference_controls_fresh_dispatch_only(self):
        fake = {"stdout": "started", "stderr": "", "returncode": 0, "agent": {"id": "agent-1", "sessionId": "session-1"}}
        running = T.new(self.project, "Already running", "Keep going.", actor="burak")
        running.update(state="running", l2_engine="codex", session_id="kept")
        S.save_task(self.project, running)
        dispatch.request_setting(self.project, "l2_preference", "claude", "Use Claude more for L2", actor="burak")
        dispatch.run_settings(self.project)
        task = T.new(self.project, "Preferred launch", "Dispatch it.", actor="burak")
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake):
            dispatch.run(self.project, task["slug"])
        launched = S.load_task(self.project, task["slug"])
        self.assertEqual((launched["l2_engine"], launched["launch_model"], launched["routing_pinned"]), ("claude", "opus", False))
        self.assertIn("prefers Claude", launched["routing"])
        self.assertEqual((S.load_task(self.project, running["slug"])["l2_engine"],
                          S.load_task(self.project, running["slug"])["session_id"]), ("codex", "kept"))
        l3 = dispatch.route.pick_engine("l3", project=config.project(self.project))
        self.assertEqual(l3["engine"], "codex", "the L2 preference never switches L3")
        dispatch.request_setting(self.project, "l2_preference", None, "Back to Auto", actor="burak")
        dispatch.run_settings(self.project)
        self.assertNotIn("l2_preference", config.project(self.project))
        self.assertEqual(dispatch.route.pick_engine("l2", project=config.project(self.project))["engine"], "codex")

    def test_dispatch_completion_preserves_a_newer_block(self):
        task = T.new(self.project, "Block during launch", "Wait for a scope decision")

        def launch(*args, **kwargs):
            T.block(self.project, task["slug"], "Confirm scope first", updates={"waiting_on": "burak"})
            return {"returncode": 0, "agent": {"id": "late-worker", "sessionId": "late-session"}}

        with mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", side_effect=launch), \
             mock.patch.object(dispatch.engines, "stop_l2_worker") as stop, \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(T.TransitionError, "no longer owns a queued task"):
                dispatch.run(self.project, task["slug"])
        stop.assert_called_once()
        fault.assert_not_called()
        current = S.load_task(self.project, task["slug"])
        self.assertEqual((current["state"], current["blocked_reason"], current["waiting_on"]),
                         ("blocked", "Confirm scope first", "burak"))
        self.assertFalse(current.get("agent_id"))
        self.assertFalse(current.get("dispatching"))

    def test_dispatch_failure_records_evidence_without_retagging_a_newer_question(self):
        for thrown in (False, True):
            with self.subTest(thrown=thrown):
                task = T.new(self.project, f"Block then launch fails {thrown}", "Confirm scope")

                def launch(*args, **kwargs):
                    T.block(self.project, task["slug"], "Confirm scope", updates={"waiting_on": "burak"})
                    if thrown:
                        raise OSError("Fixture launch failed")
                    return {"returncode": 1, "stderr": "Fixture launch failed"}

                with mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
                     mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
                     mock.patch.object(dispatch.engines, "start_l2", side_effect=launch):
                    with self.assertRaises(dispatch.DispatchFailure):
                        dispatch.run(self.project, task["slug"])
                current = S.load_task(self.project, task["slug"])
                self.assertEqual((current["state"], current["waiting_on"], current["blocked_reason"]),
                                 ("blocked", "burak", "Confirm scope"))
                self.assertFalse(current.get("fault"))
                self.assertFalse(current.get("dispatching"))


if __name__ == "__main__":
    unittest.main()
