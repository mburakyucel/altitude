"""Concrete requests queue one L2 directly; no classifier or proposal stages exist."""
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, server, state as S, tasks as T


class TestDirectDispatch(AltitudeCase):
    def test_overlapping_queued_tasks_both_launch_and_second_brief_names_overlap(self):
        self.register(self.project, wip=config.WIP_PER_PROJECT)
        first = T.new(self.project, "First", "request", paths=["README.md", "docs/"])
        second = T.new(self.project, "Second", "request", paths=["README.md", "docs/ARCHITECTURE.md"])
        launches = []

        def launch(*args, **kwargs):
            launches.append((args, kwargs))
            return {"returncode": 0, "agent": {"id": f"agent-{len(launches)}",
                                               "sessionId": f"session-{len(launches)}"}}

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
             mock.patch.object(dispatch.engines, "start_l2", side_effect=launch):
            server.dispatch_waiting(self.project)

        self.assertEqual(len(launches), 2)
        for task in (first, second):
            self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        brief = (S.task_dir(self.project, second["slug"]) / "brief.md").read_text()
        self.assertIn("Shared paths with `first` on README.md, docs/ARCHITECTURE.md", brief)

    def test_default_project_cap_is_eight_and_machine_cap_is_ten(self):
        self.register(self.project)
        self.assertEqual(config.WIP_PER_PROJECT, 8)
        self.assertEqual(config.WIP_PER_MACHINE, 10)
        for index in range(8):
            self.assertIsNone(dispatch.wip_hold(self.project))
            task = T.new(self.project, f"Worker {index}", "request")
            task["state"] = "running"
            S.save_task(self.project, task)
        self.assertEqual(dispatch.wip_hold(self.project), f"WIP limit: 8 running in {self.project}")
        self.register("another")
        for index in range(2):
            self.assertIsNone(dispatch.wip_hold("another"))
            task = T.new("another", f"Worker {index}", "request")
            task["state"] = "running"
            S.save_task("another", task)
        self.assertEqual(dispatch.wip_hold("another"), "WIP limit: 10 running on this machine")

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
        )

    def test_zero_exit_without_a_concrete_agent_never_marks_running(self):
        task = T.new(self.project, "Missing launch identity", "Try to dispatch it.", actor="burak")
        fake = {"stdout": "started", "stderr": "", "returncode": 0, "agent": None}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
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
        self.assertIn("default policy", running["routing"])


if __name__ == "__main__":
    unittest.main()
