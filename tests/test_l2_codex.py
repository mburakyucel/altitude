"""Workspace-write Codex turns prove the host sandbox can write every promised root before spending."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from altitude import engines

REAL_SUBPROCESS_RUN = subprocess.run


class TestCodexPermissionProfile(unittest.TestCase):
    def test_profile_reopens_only_runtime_binary_and_explicit_read_roots(self):
        cwd = Path("/tmp/altitude-worker")
        source = Path("/tmp/altitude-read-source")
        with mock.patch.object(engines.shutil, "which", return_value="/opt/codex/bin/codex"):
            config = engines.codex_isolation_config(cwd, writable=True, readable_roots=[source])
        filesystem = next(item for item in config if item.startswith("permissions.altitude_worker.filesystem="))
        self.assertIn('":root"="deny"', filesystem)
        self.assertIn('":minimal"="read"', filesystem)
        self.assertIn('"."="write"', filesystem)
        self.assertIn('"/opt/codex/bin/codex"="read"', filesystem)
        self.assertIn('"/tmp/altitude-read-source"="read"', filesystem)

    def test_strict_config_omits_the_unsupported_dynamic_project_map(self):
        config = engines.codex_isolation_config(Path("/tmp/altitude-worker"), writable=True)
        self.assertFalse(any(item.startswith("projects.") for item in config))

    def test_codex_environment_scrubs_ambient_credentials_and_control_channels(self):
        inherited = {
            "PATH": "/usr/bin", "HOME": "/tmp/home", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/private/bus",
            "XDG_RUNTIME_DIR": "/private/runtime", "GITHUB_TOKEN": "not-a-real-token",
            "ALTITUDE_L2_CAPABILITY": "not-a-real-capability", "SAFE_SETTING": "kept",
        }
        with mock.patch.object(engines, "clean_env", side_effect=lambda: dict(inherited)):
            direct = engines.codex_env({"OPENAI_API_KEY": "not-a-real-key"})
            launcher = engines.codex_env(retain_user_bus=True)
        self.assertNotIn("SAFE_SETTING", direct)
        for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "GITHUB_TOKEN",
                    "ALTITUDE_L2_CAPABILITY", "OPENAI_API_KEY"):
            self.assertNotIn(key, direct)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", launcher)
        self.assertNotIn("GITHUB_TOKEN", launcher)

    def test_launcher_synthesizes_user_bus_for_a_system_service(self):
        inherited = {"PATH": "/usr/bin", "HOME": "/tmp/home"}
        with mock.patch.object(engines, "clean_env", side_effect=lambda: dict(inherited)), \
             mock.patch.object(engines.os, "getuid", return_value=1234):
            launcher = engines.codex_env(retain_user_bus=True)

        self.assertEqual(launcher["XDG_RUNTIME_DIR"], "/run/user/1234")
        self.assertEqual(launcher["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1234/bus")

    def test_service_launcher_is_synchronous_and_scrubs_user_bus_before_codex_starts(self):
        child_env = {"PATH": os.environ["PATH"], "ALTITUDE_PROJECT": "altitude", "ALTITUDE_TASK": "task"}
        script = ('import json, os; print(json.dumps({'
                  '"project": os.environ.get("ALTITUDE_PROJECT"), '
                  '"task": os.environ.get("ALTITUDE_TASK"), '
                  '"secret": os.environ.get("MANAGER_FAKE_SECRET"), '
                  '"bus": os.environ.get("DBUS_SESSION_BUS_ADDRESS")}))')
        command = engines._codex_service_command(
            "altitude-codex-test.service", [sys.executable, "-c", script], child_env,
        )
        self.assertIn("--wait", command)
        self.assertIn("--pipe", command)
        self.assertIn("--property=NoNewPrivileges=no", command)
        self.assertNotIn("--scope", command)
        separator = command.index("--")
        child = command[separator + 1:]
        self.assertEqual(child[0], engines.ENV_BIN)
        self.assertEqual(child[1], "-i")
        result = REAL_SUBPROCESS_RUN(
            child, capture_output=True, text=True, check=True,
            env={"PATH": os.environ["PATH"], "MANAGER_FAKE_SECRET": "must-not-cross",
                 "DBUS_SESSION_BUS_ADDRESS": "unix:path=/manager/bus"},
        )
        observed = json.loads(result.stdout)
        self.assertEqual(observed, {"project": "altitude", "task": "task", "secret": None, "bus": None})


class TestCodexSandboxPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-codex-preflight-")
        self.cwd = Path(self.temp.name) / "worktree"
        self.cwd.mkdir()
        self.wait_empty = mock.patch.object(engines, "_wait_codex_unit_empty", return_value=True)
        self.unit_empty = mock.patch.object(engines, "_codex_unit_empty", return_value=True)
        self.wait_empty.start(); self.unit_empty.start()

    def tearDown(self):
        self.unit_empty.stop(); self.wait_empty.stop()
        self.temp.cleanup()

    @staticmethod
    def _probe_parts(cmd):
        script_at = cmd.index("-c")
        return cmd[script_at + 3], cmd[script_at + 4:]

    def _probe_success(self, cmd):
        sentinel, roots = self._probe_parts(cmd)
        for root in roots:
            path = Path(root) / sentinel
            with path.open("x") as probe:
                probe.write("altitude-codex-write-probe\n")
                probe.flush()
                os.fsync(probe.fileno())
            path.unlink()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    @staticmethod
    def _run_real_probe_script(cmd, **kwargs):
        shell_at = cmd.index("/bin/sh")
        return REAL_SUBPROCESS_RUN(cmd[shell_at:], **kwargs)

    @staticmethod
    def _codex_success(cmd):
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text('{"answer": "ok"}\n')
        stdout = '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}\n'
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    def test_healthy_host_proceeds_without_leaving_sentinels(self):
        second = Path(self.temp.name) / "git-common"
        second.mkdir()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._probe_success(cmd) if "/usr/bin/bwrap" in cmd else self._codex_success(cmd)

        extra = [f'sandbox_workspace_write.writable_roots=["{second}"]']
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write", extra_config=extra)

        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["containment_empty"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], engines.SYSTEMD_RUN_BIN)
        self.assertIn("--property=NoNewPrivileges=no", calls[0])
        self.assertEqual(calls[1][0], engines.SYSTEMD_RUN_BIN)
        self.assertIn("--wait", calls[1])
        self.assertIn("--pipe", calls[1])
        self.assertIn("--property=NoNewPrivileges=no", calls[1])
        self.assertIn("--property=KillMode=control-group", calls[1])
        self.assertIn("--property=SendSIGKILL=yes", calls[1])
        self.assertTrue(any(part.startswith("--unit=altitude-codex-sync-") for part in calls[1]))
        self.assertIn("--strict-config", calls[1])
        self.assertNotIn("--approve-for-me", calls[1])
        joined = " ".join(calls[1])
        for override in ('approval_policy="never"', 'web_search="disabled"', "features.apps=false",
                         "features.multi_agent=false", "features.goals=false"):
            self.assertIn(override, joined)
        self.assertEqual(list(self.cwd.glob(".altitude-codex-write-probe-*")), [])
        self.assertEqual(list(second.glob(".altitude-codex-write-probe-*")), [])

    def test_probe_nonzero_faults_once_without_codex_spend(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"read-only root: {self.cwd}")

        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    with mock.patch("altitude.incidents.system_fault") as fault:
                        result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write",
                                                    fault_context={"project": "project", "task": "task"})

        self.assertEqual(result["text"], "")
        self.assertIsNone(result["structured"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertIs(result["engine_started"], False)
        self.assertEqual(result["fault_recorded"], "codex-sandbox")
        self.assertEqual(result["usage"], {})
        self.assertIn(str(self.cwd), result["error"])
        self.assertIn("read-only root", result["error"])
        self.assertLessEqual(len(result["error"]), 500)
        self.assertEqual(result["raw_stdout"], "")
        self.assertEqual(result["raw_stderr"], "")
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "codex-sandbox")
        self.assertEqual(fault.call_args.kwargs, {"project": "project", "task": "task"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], engines.SYSTEMD_RUN_BIN)
        self.assertIn("/usr/bin/bwrap", calls[0])

    def test_second_writable_root_failure_names_root_and_never_spends(self):
        second = Path(self.temp.name) / "task-folder"
        second.mkdir()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            sentinel, roots = self._probe_parts(cmd)
            (Path(roots[0]) / sentinel).write_text("partial\n")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"failed for root: {second}")

        extra = [f'sandbox_workspace_write.writable_roots=["{second}"]']
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    with mock.patch("altitude.incidents.system_fault") as fault:
                        result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write",
                                                    extra_config=extra)

        self.assertNotEqual(result["returncode"], 0)
        self.assertIn(str(second), result["error"])
        self.assertIn(str(second), fault.call_args.args[1])
        fault.assert_called_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.cwd.glob(".altitude-codex-write-probe-*")), [])

    def test_read_only_run_has_no_preflight_or_extra_subprocess(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._codex_success(cmd)

        with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/codex") as which:
            with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                with mock.patch("altitude.incidents.system_fault") as fault:
                    result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only")

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "exec")
        which.assert_called_once_with(engines.config.CODEX_BIN)
        fault.assert_not_called()

    def test_read_only_coordinator_can_require_whole_turn_containment(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._codex_success(cmd)

        with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only", contain=True)

        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["containment_empty"])
        self.assertTrue(result["unit"].startswith("altitude-codex-sync-"))
        self.assertEqual(calls[0][0], engines.SYSTEMD_RUN_BIN)
        self.assertIn("--wait", calls[0])
        self.assertIn("--pipe", calls[0])
        self.assertIn("--property=NoNewPrivileges=no", calls[0])

    def test_contained_timeout_stops_the_whole_transient_unit(self):
        stopped = []

        def stop(unit):
            stopped.append(unit)

        with mock.patch.object(engines.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["systemd-run"], 1)), \
             mock.patch.object(engines, "_stop_codex_unit", side_effect=stop):
            with self.assertRaises(subprocess.TimeoutExpired):
                engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only", contain=True, timeout=1)

        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0].startswith("altitude-codex-sync-"))

    def test_contained_on_start_path_wraps_the_l3_process(self):
        launched = []
        started = []

        class FakeProcess:
            pid = 4242
            returncode = 0

            def __init__(self, cmd):
                launched.append(cmd)
                Path(cmd[cmd.index("-o") + 1]).write_text('{"message":"ok"}\n')

            def communicate(self, timeout=None):
                return ('{"type":"thread.started","thread_id":"thread"}\n', "")

        with mock.patch.object(engines.subprocess, "Popen",
                               side_effect=lambda cmd, **_kwargs: FakeProcess(cmd)):
            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only", contain=True,
                                        on_start=started.append)

        self.assertEqual(started, [4242])
        self.assertEqual(launched[0][0], engines.SYSTEMD_RUN_BIN)
        self.assertIn("--property=KillMode=control-group", launched[0])
        self.assertTrue(result["containment_empty"])
        self.assertEqual(result["reported_session_id"], "thread")

    def test_preflight_subprocess_strictly_precedes_codex(self):
        order = []

        def fake_run(cmd, **kwargs):
            if "/usr/bin/bwrap" in cmd:
                order.append("preflight")
                return self._probe_success(cmd)
            order.append("codex")
            return self._codex_success(cmd)

        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertEqual(order, ["preflight", "codex"])

    def test_success_is_suppressed_when_service_cannot_be_proven_empty(self):
        def fake_run(cmd, **kwargs):
            return self._probe_success(cmd) if "/usr/bin/bwrap" in cmd else self._codex_success(cmd)

        with mock.patch.object(engines.sys, "platform", "linux"), \
             mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"), \
             mock.patch.object(engines.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(engines, "_wait_codex_unit_empty", side_effect=[True, False]), \
             mock.patch.object(engines, "_stop_codex_unit",
                               side_effect=engines.CodexContainmentError("unit remained populated")):
            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertEqual(result["text"], "")
        self.assertIsNone(result["structured"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertFalse(result["containment_empty"])
        self.assertIn("unit remained populated", result["error"])

    def test_probe_roots_parses_dedupes_and_ignores_malformed_overrides(self):
        second = str(Path(self.temp.name) / "second")
        third = str(Path(self.temp.name) / "third")
        relative = "relative-root"
        extra = [
            f' sandbox_workspace_write.writable_roots = ["{second}", "{self.cwd}"]',
            'sandbox_workspace_write.writable_roots=["unterminated"',
            f"sandbox_workspace_write.writable_roots=['{third}', '{second}', '{relative}']",
            "sandbox_workspace_write.network_access=true",
        ]
        with self.assertLogs(engines.logger.name, level="WARNING") as logs:
            roots = engines.codex_probe_roots(self.cwd, extra)

        self.assertEqual(roots, [str(self.cwd), second, third, str((self.cwd / relative).resolve())])
        self.assertIn(extra[1], "\n".join(logs.output))

    def test_real_probe_script_succeeds_cleans_roots_and_omits_unused_network_flag(self):
        second = Path(self.temp.name) / "git-common"
        second.mkdir()
        probes = []

        def run_probe(cmd, **kwargs):
            probe = self._run_real_probe_script(cmd, **kwargs)
            probes.append((cmd, probe))
            return probe

        extra = [f'sandbox_workspace_write.writable_roots=["{second}"]',
                 "sandbox_workspace_write.network_access=true"]
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=run_probe):
                    engines.codex_sandbox_preflight(self.cwd, extra)

        cmd, probe = probes[0]
        sentinel, roots = self._probe_parts(cmd)
        self.assertEqual(probe.returncode, 0)
        self.assertEqual(probe.stderr, "")
        self.assertIn("--unshare-user", cmd)
        self.assertNotIn("--unshare-net", cmd)
        self.assertEqual(roots, [str(self.cwd), str(second)])
        self.assertFalse(any((Path(root) / sentinel).exists() for root in roots))

    def test_real_probe_script_failure_names_root_cleans_earlier_roots_and_keeps_network_flag(self):
        missing = Path(self.temp.name) / "missing" / "root"
        probes = []

        def run_probe(cmd, **kwargs):
            probe = self._run_real_probe_script(cmd, **kwargs)
            probes.append((cmd, probe))
            return probe

        extra = [f'sandbox_workspace_write.writable_roots=["{missing}"]']
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=run_probe):
                    with self.assertRaises(engines.CodexSandboxPreflightError) as raised:
                        engines.codex_sandbox_preflight(self.cwd, extra)

        cmd, probe = probes[0]
        sentinel, roots = self._probe_parts(cmd)
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn(str(missing), probe.stderr)
        self.assertIn(str(missing), raised.exception.detail)
        self.assertIn("--unshare-user", cmd)
        self.assertIn("--unshare-net", cmd)
        self.assertEqual(roots, [str(self.cwd), str(missing)])
        self.assertFalse(any((Path(root) / sentinel).exists() for root in roots))

    def test_fault_recording_failure_is_logged_and_still_returns_failure(self):
        failure = subprocess.CompletedProcess([], 1, stdout="", stderr="namespace unavailable")
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", return_value=failure):
                    with mock.patch("altitude.incidents.system_fault", side_effect=OSError("fault store closed")):
                        with self.assertLogs(engines.logger.name, level="ERROR") as logs:
                            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("namespace unavailable", result["error"])
        self.assertIsNone(result["fault_recorded"])
        self.assertIn("Failed to record Codex sandbox preflight system fault", "\n".join(logs.output))

    def test_preflight_containment_inspection_failure_faults_without_codex_spend(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._probe_success(cmd)

        with mock.patch.object(engines.sys, "platform", "linux"), \
             mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"), \
             mock.patch.object(engines.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(engines, "_wait_codex_unit_empty",
                               side_effect=engines.CodexContainmentError("manager inspection failed")), \
             mock.patch.object(engines, "_stop_codex_unit") as stop, \
             mock.patch("altitude.incidents.system_fault") as fault:
            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertEqual(len(calls), 1)
        self.assertIs(result["engine_started"], False)
        self.assertIn("manager inspection failed", result["error"])
        stop.assert_called_once()
        fault.assert_called_once()

    def test_concurrent_preflights_use_distinct_sentinels(self):
        barriers = (threading.Barrier(2), threading.Barrier(2))
        names, simultaneous_counts = [], []

        def fake_run(cmd, **kwargs):
            sentinel, roots = self._probe_parts(cmd)
            path = Path(roots[0]) / sentinel
            path.open("x").close()
            names.append(sentinel)
            barriers[0].wait(timeout=5)
            simultaneous_counts.append(len(list(self.cwd.glob(".altitude-codex-write-probe-*"))))
            barriers[1].wait(timeout=5)
            path.unlink()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(engines.codex_sandbox_preflight, self.cwd) for _ in range(2)]
                        for future in futures:
                            future.result(timeout=10)

        self.assertEqual(len(set(names)), 2)
        self.assertEqual(simultaneous_counts, [2, 2])
        self.assertEqual(list(self.cwd.glob(".altitude-codex-write-probe-*")), [])


if __name__ == "__main__":
    unittest.main()
