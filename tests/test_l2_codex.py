"""I-030: workspace-write Codex turns prove the host sandbox can write every promised root before spending."""
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from altitude import engines


class TestCodexSandboxPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-codex-preflight-")
        self.cwd = Path(self.temp.name) / "worktree"
        self.cwd.mkdir()

    def tearDown(self):
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
            return self._probe_success(cmd) if cmd[0] == "/usr/bin/bwrap" else self._codex_success(cmd)

        extra = [f'sandbox_workspace_write.writable_roots=["{second}"]']
        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write", extra_config=extra)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(calls), 2)
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
                    with mock.patch("altitude.improve.system_fault") as fault:
                        with self.assertRaises(engines.CodexSandboxPreflightError) as raised:
                            engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertIn(str(self.cwd), raised.exception.detail)
        self.assertLessEqual(len(raised.exception.detail), 500)
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "codex-sandbox")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/usr/bin/bwrap")

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
                    with mock.patch("altitude.improve.system_fault") as fault:
                        with self.assertRaises(engines.CodexSandboxPreflightError) as raised:
                            engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write", extra_config=extra)

        self.assertIn(str(second), raised.exception.detail)
        self.assertIn(str(second), fault.call_args.args[1])
        fault.assert_called_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.cwd.glob(".altitude-codex-write-probe-*")), [])

    def test_read_only_run_has_no_preflight_or_extra_subprocess(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._codex_success(cmd)

        with mock.patch.object(engines.shutil, "which") as which:
            with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                with mock.patch("altitude.improve.system_fault") as fault:
                    result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only")

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "exec")
        which.assert_not_called()
        fault.assert_not_called()

    def test_preflight_subprocess_strictly_precedes_codex(self):
        order = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "/usr/bin/bwrap":
                order.append("preflight")
                return self._probe_success(cmd)
            order.append("codex")
            return self._codex_success(cmd)

        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertEqual(order, ["preflight", "codex"])

    def test_probe_roots_parses_dedupes_and_ignores_malformed_overrides(self):
        second = str(Path(self.temp.name) / "second")
        third = str(Path(self.temp.name) / "third")
        extra = [
            f' sandbox_workspace_write.writable_roots = ["{second}", "{self.cwd}"]',
            'sandbox_workspace_write.writable_roots=["unterminated"',
            f"sandbox_workspace_write.writable_roots=['{third}', '{second}']",
            "sandbox_workspace_write.network_access=true",
        ]
        self.assertEqual(engines.codex_probe_roots(self.cwd, extra), [str(self.cwd), second, third])

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
