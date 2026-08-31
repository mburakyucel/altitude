"""Claude workers are OS-confined and privileged Altitude calls use an exact Unix socket."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_TEST_HOME = Path(tempfile.mkdtemp(prefix="altitude-claude-isolation-home-"))
os.environ.setdefault("ALTITUDE_HOME", str(_TEST_HOME))

from altitude import alt_broker, config, dispatch, engines


class _PrintProc:
    def __init__(self):
        self.pid = 43210
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self, *_args, **_kwargs):
        return self.returncode

    def kill(self):
        self.returncode = -9


class TestRestrictedClaudeCommand(unittest.TestCase):
    def test_settings_deny_host_state_credentials_network_and_git_writes(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-policy-") as tmp:
            root = Path(tmp); cwd = root / "worktree"; broker = root / "broker"; git_dir = root / "git"
            for path in (cwd, broker, git_dir):
                path.mkdir()
            settings = engines.claude_worker_settings(
                root / "settings.json", cwd=cwd, writable=True,
                broker_dir=broker, git_read_paths=(git_dir,),
            )
            value = json.loads(settings.read_text())
        sandbox = value["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])
        self.assertEqual(sandbox["network"]["allowUnixSockets"], [str((broker / "broker.fifo").resolve())])
        self.assertTrue(sandbox["network"]["strictAllowlist"])
        filesystem = sandbox["filesystem"]
        self.assertIn(str(Path.home().resolve()), filesystem["denyRead"])
        self.assertIn(str(config.ROOT), [entry["path"] for entry in sandbox["credentials"]["files"]])
        self.assertIn(str(cwd.resolve()), filesystem["allowWrite"])
        self.assertIn(str(git_dir.resolve()), filesystem["denyWrite"])
        self.assertNotIn(str(broker.resolve()), filesystem["allowWrite"])
        self.assertIn(str(broker.resolve()), filesystem["allowRead"])
        self.assertNotIn(str((broker / "broker.fifo").resolve()), filesystem["allowWrite"])
        denied = {entry["name"] for entry in sandbox["credentials"]["envVars"]}
        self.assertTrue({"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY"} <= denied)
        self.assertTrue({"Agent", "Task", "SendMessage", "WebFetch"} <= set(value["permissions"]["deny"]))

    def test_print_and_background_launches_force_restricted_and_scrub(self):
        captured = []

        def run(cmd, **kwargs):
            captured.append((cmd, kwargs.get("env") or {}))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory(prefix="altitude-claude-command-") as tmp, \
             mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", return_value=_PrintProc()) as popen, \
             mock.patch.object(engines.subprocess, "run", side_effect=run), \
             mock.patch.object(engines, "find_agent", return_value={}):
            root = Path(tmp); settings = root / "settings.json"; settings.write_text("{}")
            with mock.patch.dict(os.environ, {"GH_TOKEN": "secret", "SSH_AUTH_SOCK": "/secret"}):
                engines.claude_print("prompt", cwd=root, settings=settings,
                                     restricted=True, tools="Read,Bash", timeout=2)
                engines.claude_bg("worker", "prompt", cwd=root, settings=settings,
                                  restricted=True, tools="Read,Bash")
                engines.claude_resume_bg("worker", "session-1", "continue", cwd=root,
                                         settings=settings, restricted=True, tools="Read,Bash")
        print_cmd = popen.call_args.args[0]
        self.assertIn("--restricted", print_cmd)
        self.assertIn("--strict-mcp-config", print_cmd)
        self.assertEqual(print_cmd[print_cmd.index("--tools") + 1], "Read,Bash")
        print_env = popen.call_args.kwargs["env"]
        self.assertEqual(print_env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"], "1")
        self.assertNotIn("GH_TOKEN", print_env)
        self.assertNotIn("SSH_AUTH_SOCK", print_env)
        self.assertEqual(len(captured), 2)
        self.assertIn("--restricted", captured[0][0])
        self.assertIn("--strict-mcp-config", captured[0][0])
        self.assertIn("--restricted", captured[1][0])
        self.assertIn("--strict-mcp-config", captured[1][0])
        self.assertEqual(captured[0][1]["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"], "1")

    def test_caller_cannot_inject_parent_auth_or_host_credentials(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "trusted-parent"}, clear=True):
            value = engines._restricted_claude_env({
                "ANTHROPIC_API_KEY": "attacker", "CLAUDE_CODE_OAUTH_TOKEN": "attacker",
                "GH_TOKEN": "attacker", "SSH_AUTH_SOCK": "/attacker",
            })
        self.assertEqual(value.get("ANTHROPIC_API_KEY"), "trusted-parent")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", value)
        self.assertNotIn("GH_TOKEN", value)
        self.assertNotIn("SSH_AUTH_SOCK", value)


class TestClaudeBrokerBoundary(unittest.TestCase):
    def test_cancel_requested_immediately_revokes_pending_broker_generation(self):
        generation = "a" * 24
        for field, state in (("pending_dispatch", "approved"), ("pending_resume", "running")):
            task = {"slug": "t", "state": state, "dispatch_id": "t-1" if field == "pending_resume" else None,
                    "l2_engine": "claude",
                    field: {"dispatch_id": "t-1", "generation": generation,
                            "engine": "claude", "cancel_requested": "now"},
                    "claude_broker": {"dispatch_id": "t-1", "generation": generation}}
            with self.subTest(field=field), \
                 mock.patch.object(dispatch.S, "load_task", return_value=task):
                self.assertFalse(dispatch._claude_generation_current("p", "t", "t-1", generation))

    def test_predeploy_writable_directory_transport_is_not_adopted(self):
        generation = "e" * 24
        task = {"slug": "t", "state": "running", "dispatch_id": "t-1", "l2_engine": "claude",
                "claude_broker": {"dispatch_id": "t-1", "generation": generation,
                                  "token": "legacy", "owner_pid": 999999,
                                  "owner_pid_start": "dead"}}
        with tempfile.TemporaryDirectory(prefix="altitude-legacy-broker-") as tmp, \
             mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext()), \
             mock.patch.object(dispatch.S, "load_task", return_value=task), \
             mock.patch.object(dispatch, "_proc_start_time", return_value=None):
            worktree = Path(tmp); git_dir = worktree / "git"; git_dir.mkdir()
            with self.assertRaisesRegex(alt_broker.BrokerDenied, "predeploy Claude broker"):
                dispatch._ensure_claude_broker(
                    "p", "t", "t-1", generation, worktree, "b" * 40, git_dir)

    def test_predeploy_unbrokered_live_worker_is_stopped_and_recovered(self):
        task = {"slug": "legacy", "state": "running", "dispatch_id": "legacy-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid"}
        row = {"id": "agent", "sessionId": "sid", "state": "working", "status": "busy"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task",
                               side_effect=alt_broker.BrokerDenied("missing capability")), \
             mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents",
                               side_effect=[[row], [{**row, "state": "stopped"}]]), \
             mock.patch.object(dispatch.engines, "claude_stop") as stop:
            outcomes = dispatch.poll("p")
        stop.assert_called_once_with("agent")
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0]["died"])
        self.assertIn("capability unavailable", outcomes[0]["error"])

    def test_broker_failure_retains_live_authority_when_stop_raises(self):
        task = {"slug": "legacy", "state": "running", "dispatch_id": "legacy-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid"}
        row = {"id": "agent", "sessionId": "sid", "state": "working", "status": "busy"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task",
                               side_effect=alt_broker.BrokerDenied("missing capability")), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[row]), \
             mock.patch.object(dispatch.engines, "claude_stop", side_effect=RuntimeError("refused")):
            outcomes = dispatch.poll("p")
        self.assertEqual(outcomes, [])
        close.assert_not_called()

    def test_broker_failure_retains_live_authority_when_stop_does_not_stop(self):
        task = {"slug": "legacy", "state": "running", "dispatch_id": "legacy-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid"}
        row = {"id": "agent", "sessionId": "sid", "state": "working", "status": "busy"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task",
                               side_effect=alt_broker.BrokerDenied("missing capability")), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[row], [row]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="accepted"):
            outcomes = dispatch.poll("p")
        self.assertEqual(outcomes, [])
        close.assert_not_called()

    def test_idle_report_stops_exact_writer_before_completion(self):
        task = {"slug": "reported", "state": "running", "dispatch_id": "reported-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid",
                "claude_broker": {"generation": "g"}}
        row = {"id": "agent", "sessionId": "sid", "state": "working", "status": "idle"}
        stopped = {**row, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_current_report", return_value=True), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task"), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[row], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop:
            outcomes = dispatch.poll("p")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["agent"]["state"], "stopped")
        stop.assert_called_once_with("agent")
        close.assert_called_once_with("p", "reported", "g")

    def test_late_blocked_report_stops_exact_writer_before_completion(self):
        task = {"slug": "late", "state": "blocked", "dispatch_id": "late-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid",
                "blocked_reason": "idle without a report", "claude_broker": {"generation": "g"}}
        row = {"id": "agent", "sessionId": "sid", "state": "blocked", "status": "idle"}
        stopped = {**row, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_current_report", return_value=True), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[row], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped"):
            outcomes = dispatch.poll("p")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["agent"]["state"], "stopped")
        close.assert_called_once_with("p", "late", "g")

    def test_quota_block_stops_exact_writer_before_transition(self):
        task = {"slug": "limited", "state": "running", "dispatch_id": "limited-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid",
                "claude_broker": {"generation": "g"}}
        row = {"id": "agent", "sessionId": "sid", "state": "blocked", "status": "idle"}
        stopped = {**row, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task"), \
             mock.patch.object(dispatch, "job_detail", return_value=("limit", None)), \
             mock.patch.object(dispatch.engines, "usage_limit_in", return_value="2099-01-01T00:00:00Z"), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[row], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped"):
            outcomes = dispatch.poll("p")
        self.assertEqual(outcomes[0]["limited"], "2099-01-01T00:00:00Z")
        self.assertEqual(outcomes[0]["agent"]["state"], "stopped")
        close.assert_called_once_with("p", "limited", "g")

    def test_idle_quota_limit_stops_exact_writer_before_transition(self):
        task = {"slug": "idle-limit", "state": "running", "dispatch_id": "idle-limit-1",
                "l2_engine": "claude", "agent_id": "agent", "session_id": "sid",
                "claude_broker": {"generation": "g"}}
        row = {"id": "agent", "sessionId": "sid", "state": "working", "status": "idle"}
        stopped = {**row, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "write_json"), \
             mock.patch.object(dispatch, "_adopt_claude_broker_for_task"), \
             mock.patch.object(dispatch, "job_detail", return_value=("limit", None)), \
             mock.patch.object(dispatch.engines, "usage_limit_in", return_value="2099-01-01T00:00:00Z"), \
             mock.patch.object(dispatch, "_close_claude_broker") as close, \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[row], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped"):
            outcomes = dispatch.poll("p")
        self.assertEqual(outcomes[0]["limited"], "2099-01-01T00:00:00Z")
        self.assertEqual(outcomes[0]["agent"]["state"], "stopped")
        close.assert_called_once_with("p", "idle-limit", "g")

    def test_python_script_cannot_turn_broker_env_into_unlisted_state_action(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-script-") as tmp:
            root = Path(tmp); worktree = root / "worktree"; worktree.mkdir()
            broker = alt_broker.AltBroker(
                socket_path=root / "broker" / "broker.fifo", token="secret",
                project="p", slug="t", generation="g", worktree=worktree,
                trusted_alt=config.REPO / "bin" / "alt", policy=alt_broker.l2_policy,
                validate_generation=lambda: True,
            )
            env = {**os.environ, **broker.env()}
            # This is the prior guard bypass: generate/run arbitrary Python and
            # invoke the CLI from it. The early broker forwarding still applies.
            script = ("import subprocess,sys; "
                      f"raise SystemExit(subprocess.run([sys.executable,{str(config.REPO / 'bin' / 'alt')!r},"
                      "'task','done','t'],cwd='" + str(worktree) + "').returncode)")
            with broker:
                result = subprocess.run([sys.executable, "-c", script], cwd=str(worktree),
                                        capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the current-task L2 broker allowlist", result.stderr)

    def test_broker_reconstructs_with_same_capability_after_dead_daemon(self):
        with tempfile.TemporaryDirectory(prefix="altitude-claude-adopt-") as tmp:
            root = Path(tmp); worktree = root / "worktree"; git_dir = root / "git"
            worktree.mkdir(); git_dir.mkdir()
            generation = "a" * 24
            task = {"slug": "t", "state": "running", "dispatch_id": "t-1", "l2_engine": "claude",
                    "worktree": str(worktree), "origin_sha": "b" * 40,
                    "claude_broker": {"dispatch_id": "t-1", "generation": generation,
                                      "transport": "unix-v2",
                                      "token": "durable-secret", "owner_pid": 999999,
                                      "owner_pid_start": "dead", "worktree": str(worktree),
                                      "origin_sha": "b" * 40, "trusted_git_dir": str(git_dir)}}

            def save(_project, value):
                task.clear(); task.update(value)

            with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext()), \
                 mock.patch.object(dispatch.S, "load_task", side_effect=lambda _p, _s: dict(task)), \
                 mock.patch.object(dispatch.S, "save_task", side_effect=save), \
                 mock.patch.object(dispatch, "_proc_start_time",
                                   side_effect=lambda pid: None if pid == 999999 else "current"):
                first = dispatch._ensure_claude_broker(
                    "p", "t", "t-1", generation, worktree, "b" * 40, git_dir)
                first_path = first.socket_path
                first.close()
                dispatch._claude_brokers.pop(("p", "t", generation), None)
                task["claude_broker"]["owner_pid"] = 999999
                task["claude_broker"]["owner_pid_start"] = "dead"
                second = dispatch._ensure_claude_broker(
                    "p", "t", "t-1", generation, worktree, "b" * 40, git_dir)
                try:
                    self.assertEqual(second.token, "durable-secret")
                    self.assertEqual(second.socket_path, first_path)
                    self.assertTrue(second.socket_path.exists())
                finally:
                    second.close()
                    dispatch._claude_brokers.pop(("p", "t", generation), None)

    def test_aged_pending_dispatch_revokes_broker_before_claim_clear(self):
        generation = "c" * 24
        with tempfile.TemporaryDirectory(prefix="altitude-stale-claude-") as tmp:
            worktree = Path(tmp)
            task = {"slug": "t", "state": "approved", "dispatching": "old",
                    "pending_dispatch": {"dispatch_id": "t-1", "engine": "claude",
                                         "generation": generation, "started": "old",
                                         "worktree": str(worktree), "origin_sha": "d" * 40}}

            def save(_project, value):
                task.clear(); task.update(value)

            with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext()), \
                 mock.patch.object(dispatch.S, "load_task", side_effect=lambda _p, _s: dict(task)), \
                 mock.patch.object(dispatch.S, "save_task", side_effect=save), \
                 mock.patch.object(dispatch, "_seconds_since", return_value=601), \
                 mock.patch.object(dispatch.config, "project_path", return_value=worktree), \
                 mock.patch.object(dispatch, "_validate_task_worktree", return_value=Path("/git")), \
                 mock.patch.object(dispatch, "_ensure_claude_broker"), \
                 mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
                 mock.patch.object(dispatch, "_close_claude_broker") as close:
                self.assertIsNone(dispatch._adopt_pending_dispatch("p", task))
        close.assert_called_once_with("p", "t", generation)
        self.assertNotIn("pending_dispatch", task)


if __name__ == "__main__":
    unittest.main()
