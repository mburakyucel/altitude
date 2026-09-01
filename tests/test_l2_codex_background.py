"""A Codex L2 has a stable thread and replaceable, stoppable OS workers."""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-codex-bg-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines  # noqa: E402

REAL_STOP_CODEX_UNIT = engines._stop_codex_unit


FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
thread = os.environ.get("FAKE_THREAD", "11111111-1111-1111-1111-111111111111")
if os.environ.get("FAKE_ARGV"):
    open(os.environ["FAKE_ARGV"], "w").write(json.dumps(args))
if os.environ.get("FAKE_STDIN"):
    open(os.environ["FAKE_STDIN"], "w").write(sys.stdin.read())
print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
if os.environ.get("FAKE_MODE") == "complete":
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "cached_input_tokens": 80, "output_tokens": 5}}), flush=True)
else:
    time.sleep(30)
'''

FAKE_SYSTEMD_RUN = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get("FAKE_SYSTEMD_ARGV"):
    open(os.environ["FAKE_SYSTEMD_ARGV"], "w").write(json.dumps(args))
at = args.index("--")
command = args[at + 1:]
os.execvpe(command[0], command, os.environ)
'''


class TestCodexBackground(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = _ROOT / "repo"; cls.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cls.repo, check=True)
        cls.fake = _ROOT / "codex"; cls.fake.write_text(FAKE)
        cls.fake.chmod(cls.fake.stat().st_mode | stat.S_IXUSR)
        cls.fake_systemd_run = _ROOT / "systemd-run"; cls.fake_systemd_run.write_text(FAKE_SYSTEMD_RUN)
        cls.fake_systemd_run.chmod(cls.fake_systemd_run.stat().st_mode | stat.S_IXUSR)

    def setUp(self):
        self.jobs = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=_ROOT))
        self.systemd_run = mock.patch.object(engines, "SYSTEMD_RUN_BIN", str(self.fake_systemd_run))
        self.unit_state = mock.patch.object(engines, "_systemd_unit_properties", side_effect=self._unit_properties)
        self.unit_stop = mock.patch.object(engines, "_stop_codex_unit", side_effect=self._stop_fake_unit)
        self.systemd_run.start(); self.unit_state.start(); self.unit_stop.start()

    def tearDown(self):
        for worker_id, proc in list(engines._codex_processes.items()):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
            engines._codex_processes.pop(worker_id, None)
        self.unit_stop.stop(); self.unit_state.stop(); self.systemd_run.stop()

    def _record_for_unit(self, unit):
        for path in self.jobs.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("unit") == unit:
                return record
        return None

    def _unit_properties(self, unit):
        record = self._record_for_unit(unit)
        if not record:
            return {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                    "ControlGroup": "", "MainPID": "0"}
        pid = int(record["pid"])
        active = engines._pid_start(pid) == str(record.get("pid_start"))
        return {"LoadState": "loaded", "ActiveState": "active" if active else "inactive",
                "SubState": "running" if active else "dead", "ControlGroup": "", "MainPID": str(pid if active else 0)}

    def _stop_fake_unit(self, unit, timeout=5.0):
        record = self._record_for_unit(unit)
        if not record:
            return
        proc = engines._codex_processes.get(record["id"])
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            proc.wait(timeout=timeout)

    def launch(self, **kwargs):
        env = {"FAKE_THREAD": kwargs.pop("thread", "11111111-1111-1111-1111-111111111111"),
               **kwargs.pop("env", {})}
        with mock.patch.object(config, "CODEX_BIN", str(self.fake)), \
             mock.patch.object(engines, "_CODEX_SAFE_ENV", engines._CODEX_SAFE_ENV | set(env)), \
             mock.patch.object(engines, "codex_sandbox_preflight"):
            return engines.codex_bg("p/task-1", "do it", cwd=self.repo, job_root=self.jobs,
                                    extra_env=env, **kwargs)

    def test_fresh_and_resume_keep_thread_but_replace_worker(self):
        stdin_path = self.jobs / "stdin.txt"
        first_argv_path = self.jobs / "first-argv.json"
        systemd_argv_path = self.jobs / "systemd-argv.json"
        first = self.launch(env={"FAKE_STDIN": str(stdin_path), "FAKE_ARGV": str(first_argv_path),
                                 "FAKE_SYSTEMD_ARGV": str(systemd_argv_path)})
        self.assertEqual(first["returncode"], 0)
        self.assertIn(engines.CODEX_PATCH_NOTE, stdin_path.read_text())
        first_argv = json.loads(first_argv_path.read_text())
        joined = " ".join(first_argv)
        self.assertIn("--strict-config", first_argv)
        self.assertNotIn("--approve-for-me", first_argv)
        self.assertNotIn("--dangerously-bypass-hook-trust", first_argv)
        self.assertIn('approval_policy="never"', joined)
        self.assertIn('web_search="disabled"', joined)
        self.assertIn("features.apps=false", joined)
        self.assertIn("features.multi_agent=false", joined)
        self.assertIn("features.goals=false", joined)
        self.assertIn("shell_environment_policy.ignore_default_excludes=false", joined)
        systemd_argv = json.loads(systemd_argv_path.read_text())
        self.assertIn("--scope", systemd_argv)
        self.assertIn("--collect", systemd_argv)
        self.assertIn("--property=KillMode=control-group", systemd_argv)
        self.assertIn("--property=SendSIGKILL=yes", systemd_argv)
        self.assertTrue(any(arg.startswith("--unit=altitude-codex-") for arg in systemd_argv))
        one = first["agent"]
        self.assertEqual(one["unit"], self._record_for_unit(one["unit"])["unit"])
        self.assertEqual(one["sessionId"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(one["state"], "working")
        engines.codex_stop(one["id"], job_root=self.jobs)

        argv_path = self.jobs / "resume-argv.json"
        second = self.launch(resume=one["sessionId"], env={"FAKE_ARGV": str(argv_path)})
        two = second["agent"]
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual(two["sessionId"], one["sessionId"])
        argv = json.loads(argv_path.read_text())
        self.assertEqual(argv[:2], ["exec", "resume"])
        self.assertIn("--strict-config", argv)
        self.assertNotIn("--approve-for-me", argv)
        self.assertIn(one["sessionId"], argv)
        engines.codex_stop(two["id"], job_root=self.jobs)

    def test_resume_refuses_a_different_thread_identity(self):
        result = self.launch(resume="11111111-1111-1111-1111-111111111111",
                             thread="22222222-2222-2222-2222-222222222222")
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("different Codex thread", result["stderr"])

    def test_completed_turn_exposes_latest_cache_usage(self):
        result = self.launch(env={"FAKE_MODE": "complete"})
        row = engines.codex_worker(result["agent"]["id"], job_root=self.jobs)
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["usage"]["cached_input_tokens"], 80)

    def test_initial_unit_record_failure_never_starts_an_unowned_process(self):
        children = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if args[0][0] == str(self.fake_systemd_run):
                children.append(proc)
            return proc

        with mock.patch.object(config, "CODEX_BIN", str(self.fake)), \
             mock.patch.object(engines, "codex_sandbox_preflight"), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(engines.S, "write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                engines.codex_bg("p/task-1", "do it", cwd=self.repo, job_root=self.jobs)
        self.assertEqual(children, [])

    def test_pid_record_failure_stops_the_preowned_unit(self):
        children = []
        real_popen = subprocess.Popen
        real_write = engines.S.write_json
        writes = 0

        def capture(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if args[0][0] == str(self.fake_systemd_run):
                children.append(proc)
            return proc

        def fail_second(path, value):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("disk full")
            return real_write(path, value)

        with mock.patch.object(config, "CODEX_BIN", str(self.fake)), \
             mock.patch.object(engines, "codex_sandbox_preflight"), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(engines.S, "write_json", side_effect=fail_second), \
             mock.patch.object(engines, "_stop_codex_unit",
                               side_effect=lambda _unit: children[-1].terminate() if children else None):
            with self.assertRaisesRegex(OSError, "disk full"):
                engines.codex_bg("p/task-1", "do it", cwd=self.repo, job_root=self.jobs)

        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        records = [json.loads(path.read_text()) for path in self.jobs.glob("*.json")]
        self.assertTrue(records[0]["unit"].startswith("altitude-codex-"))

    def test_missing_process_identity_stops_the_unowned_process(self):
        children = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if args[0][0] == str(self.fake_systemd_run):
                children.append(proc)
            return proc

        with mock.patch.object(config, "CODEX_BIN", str(self.fake)), \
             mock.patch.object(engines, "codex_sandbox_preflight"), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(engines, "_pid_start", return_value=None), \
             mock.patch.object(engines, "_stop_codex_unit",
                               side_effect=lambda _unit: children[-1].terminate() if children else None):
            with self.assertRaisesRegex(RuntimeError, "stable process identity"):
                engines.codex_bg("p/task-1", "do it", cwd=self.repo, job_root=self.jobs)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_containment_helper_requires_a_durable_inactive_unit(self):
        worker_id = "worker"
        paths = engines._codex_paths(self.jobs, worker_id)
        engines.S.write_json(paths["record"], {"id": worker_id, "unit": "altitude-codex-worker.scope"})
        with mock.patch.object(engines, "_systemd_unit_properties", return_value={
            "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
            "ControlGroup": "", "MainPID": "0",
        }):
            self.assertTrue(engines.codex_containment_empty(worker_id, job_root=self.jobs))
        self.assertFalse(engines.codex_containment_empty("missing", job_root=self.jobs))

    def test_cgroup_wide_sigkill_is_used_when_stop_does_not_empty_scope(self):
        calls = []

        def run(cmd, **_kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(engines.subprocess, "run", side_effect=run), \
             mock.patch.object(engines, "_wait_codex_unit_empty", side_effect=[False, True]), \
             mock.patch.object(engines, "_codex_unit_empty", return_value=False):
            REAL_STOP_CODEX_UNIT("altitude-codex-worker.scope")

        self.assertEqual(calls[0][2], "stop")
        self.assertEqual(calls[1][2:5], ["kill", "--kill-who=all", "--signal=SIGKILL"])

    def test_containment_state_fails_closed_when_user_manager_is_unavailable(self):
        worker_id = "worker"
        paths = engines._codex_paths(self.jobs, worker_id)
        engines.S.write_json(paths["record"], {"id": worker_id, "unit": "altitude-codex-worker.scope"})
        with mock.patch.object(engines, "_systemd_unit_properties",
                               side_effect=engines.CodexContainmentError("Failed to connect to bus")):
            with self.assertLogs(engines.logger.name, level="ERROR"):
                self.assertFalse(engines.codex_containment_empty(worker_id, job_root=self.jobs))


if __name__ == "__main__":
    unittest.main()
