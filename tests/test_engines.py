"""I-20260907-165145: background workers share the transient-unit execution boundary."""
import subprocess
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines


class TestBackgroundUnits(AltitudeCase):
    def test_launch_and_resume_use_the_shared_unit_launcher(self):
        name = f"{self.project}/worker-1"
        row = {"id": "new", "sessionId": "session", "name": name, "state": "working"}
        for resume in (False, True):
            with self.subTest(resume=resume), \
                 mock.patch.object(engines, "claude_agents", side_effect=[[], [row]]), \
                 mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                kw = dict(cwd=self.repo, extra_env={"ALTITUDE_TASK": "worker"})
                result = (engines.claude_resume_bg(name, "session", "continue", **kw) if resume
                          else engines.claude_bg(name, "brief", **kw))
                self.assertEqual(result["agent"], row)
                cmd = run.call_args.args[0]
                self.assertEqual(cmd[0], engines.SYSTEMD_RUN_BIN)
                for flag in ("--user", f"--unit={engines._claude_unit(name)}", "--service-type=forking",
                             "--property=ExitType=cgroup", "--property=KillMode=control-group"):
                    self.assertIn(flag, cmd)
                self.assertNotIn("--wait", cmd)
                self.assertNotIn("--pipe", cmd)
                child = cmd[cmd.index("--") + 1:]
                self.assertEqual(child[:2], [engines.ENV_BIN, "-i"])
                self.assertIn("ALTITUDE_TASK=worker", child)
                self.assertFalse(any(x.startswith("DBUS_SESSION_BUS_ADDRESS=") for x in child))
                self.assertIn(config.CLAUDE_BIN, child)
                self.assertEqual("--resume" in child, resume)

    def test_launch_timeout_stops_the_unit(self):
        name = "timeout-worker"
        with mock.patch.object(engines.subprocess, "run", side_effect=[subprocess.TimeoutExpired("run", 120),
                                                                 subprocess.CompletedProcess([], 0)]) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                engines._run_cli(["fake-worker"], name=name, cwd=self.repo, env={})
        self.assertEqual(run.call_args.args[0], [engines.SYSTEMCTL_BIN, "--user", "stop", engines._claude_unit(name)])

    def test_stop_owns_unit_and_remove_uses_stop(self):
        row = {"id": "worker", "name": "task-1"}
        with mock.patch.object(engines, "claude_agents", return_value=[row]), \
             mock.patch.object(engines, "_unit_active", return_value=False), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            engines.claude_rm("worker")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], [engines.SYSTEMCTL_BIN, "--user", "stop", engines._claude_unit("task-1")])
        self.assertEqual(commands[-1], [config.CLAUDE_BIN, "rm", "worker"])

    def test_registry_outlives_unit_and_terminal_registry_can_have_live_descendants(self):
        row = {"id": "worker", "sessionId": "same", "name": "task-1", "cwd": str(self.repo), "state": "working"}
        task = {"agent_id": "worker", "session_id": "same", "worktree": str(self.repo)}
        with mock.patch.object(engines, "_unit_active", return_value=False):
            actual = engines.worker("claude", task, rows=[{**row, "id": "old"}, row])
        self.assertEqual((actual["id"], actual["state"], actual["status"]), ("worker", "failed", "exited"))
        self.assertIsNone(engines.worker("claude", task, rows=[{**row, "id": "old"}]),
                          "I-20260907-165145: an old session row cannot hide the current worker's disappearance")
        row["state"] = "done"
        with mock.patch.object(engines, "_unit_active", return_value=False):
            self.assertEqual(engines.worker("claude", task, rows=[row])["state"], "done")
        with mock.patch.object(engines, "claude_agents", return_value=[row]), \
             mock.patch.object(engines, "_unit_active", return_value=True):
            self.assertTrue(engines.worker_live("claude", task))
