"""Concrete requests queue one L2 directly; no classifier or proposal stages exist."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-direct-dispatch-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from altitude import config, dispatch, server, state as S, tasks as T  # noqa: E402


class TestDirectDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"direct": {"name": "direct", "path": config.ROOT.as_posix()}})

    def test_new_task_is_immediately_queued_without_pipeline_metadata(self):
        task = T.new("direct", "Fix the focused bug", "Fix it and test it.", actor="burak")

        self.assertEqual(task["state"], "queued")
        for removed in ("class", "envelope", "proposal_attempts", "decision", "estimate"):
            self.assertNotIn(removed, task)
        self.assertEqual(T.decisions("direct"), [])

    def test_server_has_no_classifier_proposal_or_critic_entrypoint(self):
        self.assertFalse(hasattr(server, "size_task"))
        self.assertFalse(hasattr(server, "run_proposal_flow"))
        for removed in ("intake.py", "propose.py"):
            self.assertFalse((config.REPO / "altitude" / removed).exists())
        for removed in ("size.md", "proposal.md", "critic.md"):
            self.assertFalse((config.PERSONAS / removed).exists())

    def test_task_source_is_only_chat_or_explicit_recovery(self):
        with self.assertRaisesRegex(T.TransitionError, "source must be chat or recovery"):
            T.new("direct", "Invented source", "request", source="autonomous-backlog")

    def test_waiting_task_dispatches_directly_to_l2(self):
        queued = {"slug": "direct-one", "state": "queued"}
        result = {"attempt": 1, "agent": {"id": "l2-agent"}}
        with mock.patch.object(S, "list_tasks", return_value=[queued]), \
             mock.patch.object(server.dispatch, "wip_hold", return_value=None), \
             mock.patch.object(server.dispatch, "run", return_value=result) as run:
            server.dispatch_waiting("direct")
        run.assert_called_once_with("direct", "direct-one")

    def test_launch_failure_holds_dispatch_without_orphaning_the_task(self):
        task = T.new("direct", "Engine launch fails", "Try to dispatch it.", actor="burak")
        task["dispatching"] = S.now()
        S.save_task("direct", task)
        with mock.patch.object(S, "list_tasks", return_value=[task]), \
             mock.patch.object(server.dispatch, "wip_hold", return_value=None), \
             mock.patch.object(server.dispatch, "run", side_effect=RuntimeError("engine unavailable")), \
             mock.patch.object(server.incidents, "system_fault") as fault:
            server.dispatch_waiting("direct")

        waiting = S.load_task("direct", task["slug"])
        self.assertEqual(waiting["state"], "queued")
        self.assertIsNone(waiting.get("dispatching"))
        self.assertEqual(S.read_events("direct", task["slug"])[-1]["kind"], "dispatch-failed")
        fault.assert_called_once_with(
            "dispatch-failed",
            f"direct/{task['slug']}: engine unavailable",
            project="direct",
            task=task["slug"],
        )

    def test_zero_exit_without_a_concrete_agent_never_marks_running(self):
        task = T.new("direct", "Missing launch identity", "Try to dispatch it.", actor="burak")
        fake = {"stdout": "started", "stderr": "", "returncode": 0, "agent": None}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=config.ROOT), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake), \
             mock.patch("altitude.incidents.system_fault") as fault:
            with self.assertRaisesRegex(dispatch.DispatchFailure, "concrete worker id and session id"):
                dispatch.run("direct", task["slug"])

        waiting = S.load_task("direct", task["slug"])
        self.assertEqual(waiting["state"], "queued")
        self.assertIsNone(waiting.get("dispatching"))
        fault.assert_called_once()

    def test_successful_launch_binds_the_attempt_given_to_the_l2(self):
        task = T.new("direct", "Concrete launch identity", "Dispatch it.", actor="burak")
        fake = {"stdout": "started", "stderr": "", "returncode": 0,
                "agent": {"id": "agent-1", "sessionId": "session-1"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=config.ROOT), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake) as launch:
            dispatch.run("direct", task["slug"])

        running = S.load_task("direct", task["slug"])
        self.assertEqual((running["state"], running["agent_id"], running["session_id"]),
                         ("running", "agent-1", "session-1"))
        self.assertEqual(running["attempt"], 1)
        env = launch.call_args.kwargs["extra_env"]
        self.assertEqual((env["ALTITUDE_ATTEMPT"], env["ALTITUDE_SESSION_KEY"]),
                         ("1", S.session_key("direct", task["slug"], 1)))
        self.assertEqual(running["l2_engine"], "codex")
        self.assertIn("default policy", running["routing"])

if __name__ == "__main__":
    unittest.main()
