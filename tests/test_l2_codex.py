"""Codex L2 uses the hardened task worktree and a generation-safe durable wrapper."""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from altitude import config, dispatch, engines

REAL_SUBPROCESS_RUN = subprocess.run
REAL_SUBPROCESS_POPEN = subprocess.Popen


class TestL2ChoiceAndDispatch(unittest.TestCase):
    def test_task_engine_is_l1_only_and_project_pin_controls_l2(self):
        choice = dispatch._l2_choice({"slug": "pinned", "engine": "codex"}, {"l2_engine": "claude"})
        self.assertEqual(choice["engine"], "claude")
        self.assertIn("forced", choice["why"])

    def test_codex_launch_uses_exact_origin_base_and_shared_task_worktree(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-dispatch-") as tmp:
            root = Path(tmp)
            repo, worktree, task_dir = root / "repo", root / "worktree", root / "task"
            repo.mkdir(); worktree.mkdir(); task_dir.mkdir()
            persona = root / "persona.md"; persona.write_text("persona")
            task = {"slug": "safe", "title": "safe", "class": "S", "state": "approved",
                    "dispatching": None, "attempt": 0, "engine": "codex",
                    "envelope": {"max_turns": 5, "subagent_launches": 1, "l1_in_flight": 1}}
            saved, order = [], []

            def save(_project, value):
                task.update(value)
                saved.append(dict(value))
                if value.get("report_not_before"):
                    order.append("marker")

            def base(_repo, _branch):
                order.append("base")
                return "a" * 40

            def make_worktree(_repo, project, slug, origin_sha):
                self.assertEqual((_repo, project, slug, origin_sha), (repo, "demo", "safe", "a" * 40))
                order.append("worktree")
                return worktree

            def start(project, slug, dispatch_id, cwd, prompt, *, origin_sha, trusted_git_dir,
                      resume=None, generation=None, claim_field=None):
                self.assertEqual((project, slug, cwd, origin_sha, resume),
                                 ("demo", "safe", worktree, "a" * 40, None))
                self.assertTrue(generation)
                self.assertEqual(trusted_git_dir, root / "trusted-git")
                self.assertEqual(claim_field, "pending_dispatch")
                self.assertIn("patch note", prompt)
                order.append("start")
                return {"returncode": 0, "stdout": "started", "stderr": "",
                        "agent": {"id": "codex:321", "sessionId": None}}

            with mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext()), \
                 mock.patch.object(dispatch.S, "load_task", side_effect=lambda _p, _s: dict(task)), \
                 mock.patch.object(dispatch.S, "save_task", side_effect=save), \
                 mock.patch.object(dispatch.S, "task_dir", return_value=task_dir), \
                 mock.patch.object(dispatch.S, "write_json"), \
                 mock.patch.object(dispatch.S, "append_event"), \
                 mock.patch.object(dispatch.T, "brief"), \
                 mock.patch.object(dispatch.T, "dispatch"), \
                 mock.patch.object(dispatch, "wip_hold", return_value=None) as wip, \
                 mock.patch.multiple(
                     dispatch.config, project_path=mock.Mock(return_value=repo),
                     project=mock.Mock(return_value={"l2_engine": "codex"})), \
                 mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=base), \
                 mock.patch.object(dispatch.git_policy, "require_hooks_installed"), \
                 mock.patch.multiple(dispatch, _task_worktree=mock.Mock(side_effect=make_worktree),
                                     _validate_task_worktree=mock.Mock(return_value=root / "trusted-git")), \
                 mock.patch.object(dispatch, "build_brief", return_value="brief"), \
                 mock.patch.object(dispatch.rules, "compiled_persona", return_value=persona), \
                 mock.patch.object(dispatch, "_start_codex_worker", side_effect=start), \
                 mock.patch.object(dispatch, "worktree_branch", return_value="worktree-safe"), \
                 mock.patch.object(engines, "CODEX_PATCH_NOTE", "patch note", create=True), \
                 mock.patch.object(engines, "claude_bg") as claude:
                result = dispatch.run("demo", "safe")

        self.assertEqual(result["engine"], "codex")
        self.assertEqual(order[:4], ["base", "worktree", "marker", "start"])
        self.assertTrue(any(row.get("report_not_before") for row in saved))
        claude.assert_not_called()
        self.assertEqual(wip.call_count, 2)


class TestDurableGeneration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-codex-record-")
        self.root = Path(self.temp.name)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.lock = mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext())
        self.dir_patch = mock.patch.object(dispatch.S, "task_dir", return_value=self.task_dir)
        self.lock.start(); self.dir_patch.start()
        self.addCleanup(self.lock.stop); self.addCleanup(self.dir_patch.stop); self.addCleanup(self.temp.cleanup)

    def test_terminal_record_during_wrapper_start_cancels_without_pid_resurrection(self):
        def popen(*_args, **_kwargs):
            current = dispatch.codex_run("demo", "task")
            current.update({"state": "done", "session_id": "thread-1", "engine_pid": 444,
                            "result": {"error": None, "returncode": 0}})
            dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), current)
            return SimpleNamespace(pid=321)

        with mock.patch.object(dispatch.engines, "clean_env", return_value={}), \
             mock.patch.object(dispatch.config, "REPO", self.root), \
             mock.patch.object(dispatch, "_proc_start_time", return_value="wrapper"), \
             mock.patch.object(dispatch.subprocess, "Popen", side_effect=popen):
            with self.assertRaisesRegex(Exception, "cancelled during wrapper launch"):
                dispatch._start_codex_worker(
                    "demo", "task", "task-1", self.root, "prompt", origin_sha="a" * 40,
                    trusted_git_dir=self.root,
                )
        record = dispatch.codex_run("demo", "task")
        self.assertEqual((record["state"], record["session_id"], record["engine_pid"]),
                         ("done", "thread-1", 444))
        self.assertEqual((record["pid"], record["agent_id"]), (None, None))

    def test_nested_codex_engine_inherits_wrapper_process_group(self):
        prompt = self.task_dir / "prompt.md"
        prompt.write_text("work")
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "running",
            "worktree": str(self.root), "origin_sha": "a" * 40, "prompt": str(prompt),
            "trusted_git_dir": str(self.root),
        })
        observed = {}
        def codex_exec(_prompt, **kwargs):
            observed.update(kwargs)
            return {"text": "done", "usage": {}, "session_id": None,
                    "returncode": 0, "error": None}
        gate_read, gate_write = os.pipe()
        os.write(gate_write, b"1"); os.close(gate_write)
        with mock.patch.dict(os.environ, {"ALTITUDE_CODEX_L2_GENERATION": "g",
                                          dispatch.CODEX_GATE_FD_ENV: str(gate_read)}), \
             mock.patch.object(dispatch.config, "project_path", return_value=self.root), \
             mock.patch.object(dispatch.config, "project", return_value={}), \
             mock.patch.object(dispatch, "_validate_task_worktree", return_value=self.root), \
             mock.patch.object(dispatch.engines, "codex_exec", side_effect=codex_exec), \
             mock.patch.object(dispatch.engines, "reap_process_group_members", return_value=True), \
             mock.patch.object(dispatch.S, "append_event"):
            dispatch.exec_codex_l2("demo", "task", "task-1")
        self.assertIs(observed.get("start_new_session"), False)

    def test_missing_wrapper_identity_cancels_direct_child_and_fails_launch(self):
        child = SimpleNamespace(pid=321)
        with mock.patch.object(dispatch.engines, "clean_env", return_value={}), \
             mock.patch.object(dispatch.config, "REPO", self.root), \
             mock.patch.object(dispatch.subprocess, "Popen", return_value=child), \
             mock.patch.object(dispatch, "_proc_start_time", return_value=None), \
             mock.patch.object(dispatch, "_cancel_new_child", return_value=True) as cancel:
            with self.assertRaisesRegex(Exception, "identity unavailable"):
                dispatch._start_codex_worker(
                    "demo", "task", "task-1", self.root, "prompt", origin_sha="a" * 40,
                trusted_git_dir=self.root)
        cancel.assert_called_once_with(child, None)
        self.assertEqual(dispatch.codex_run("demo", "task")["state"], "failed")

    def test_gate_release_occurs_only_after_wrapper_identity_is_durable(self):
        child = SimpleNamespace(pid=321)
        observed = []
        def release(_fd, data):
            record = dispatch.codex_run("demo", "task")
            observed.append((record.get("state"), record.get("pid"), record.get("pid_start"), data))
            return 1
        captured = {}
        def popen(*args, **kwargs):
            captured.update(kwargs)
            return child
        with mock.patch.object(dispatch.engines, "clean_env", return_value={}), \
             mock.patch.object(dispatch.config, "REPO", self.root), \
             mock.patch.object(dispatch.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(dispatch, "_proc_start_time", return_value="wrapper"), \
             mock.patch.object(dispatch.os, "write", side_effect=release):
            dispatch._start_codex_worker(
                "demo", "task", "task-1", self.root, "prompt", origin_sha="a" * 40,
                trusted_git_dir=self.root)
        self.assertEqual(observed, [("running", 321, "wrapper", b"1")])
        guard_env = captured["env"]
        self.assertEqual(guard_env["ALTITUDE_TASK_WORKTREE"], str(self.root.resolve()))
        self.assertEqual(guard_env["ALTITUDE_TRUSTED_GIT_DIR"], str(self.root.resolve()))
        self.assertEqual(guard_env["ALTITUDE_REQUIRED_HOOKS"], str(dispatch.config.HOOKS.resolve()))

    def test_blocked_resume_is_running_before_child_gate_release(self):
        child = SimpleNamespace(pid=321)
        task = {"slug": "task", "state": "blocked", "dispatch_id": "task-1",
                "blocked_reason": "waiting", "needs_user": None,
                "pending_resume": {"dispatch_id": "task-1", "generation": "g", "engine": "codex"}}
        observed = []

        def load_task(*_args):
            return dict(task)

        def save_task(_project, value):
            task.clear(); task.update(value)

        def release(_fd, data):
            observed.append((task["state"], task.get("pending_resume", {}).get("generation"), data))
            return 1

        with mock.patch.object(dispatch.engines, "clean_env", return_value={}), \
             mock.patch.object(dispatch.config, "REPO", self.root), \
             mock.patch.object(dispatch.subprocess, "Popen", return_value=child), \
             mock.patch.object(dispatch, "_proc_start_time", return_value="wrapper"), \
             mock.patch.object(dispatch.S, "load_task", side_effect=load_task), \
             mock.patch.object(dispatch.S, "save_task", side_effect=save_task), \
             mock.patch.object(dispatch.S, "append_event"), \
             mock.patch.object(dispatch.S, "regen_state_md"), \
             mock.patch.object(dispatch.os, "write", side_effect=release):
            dispatch._start_codex_worker(
                "demo", "task", "task-1", self.root, "prompt", origin_sha="a" * 40,
                trusted_git_dir=self.root, generation="g", claim_field="pending_resume",
                resume_answer="continue")

        self.assertEqual(observed, [("running", "g", b"1")])
        self.assertEqual(task["state"], "running")
        self.assertEqual(task["pending_resume"]["generation"], "g")

    def test_terminal_claim_after_popen_cancels_wrapper_before_gate_release(self):
        child = SimpleNamespace(pid=321)
        task = {"state": "approved", "pending_dispatch": {
            "dispatch_id": "task-1", "generation": "g", "engine": "codex"}}

        def popen(*_args, **_kwargs):
            task["pending_dispatch"]["cancel_requested"] = "now"
            return child

        with mock.patch.object(dispatch.engines, "clean_env", return_value={}), \
             mock.patch.object(dispatch.config, "REPO", self.root), \
             mock.patch.object(dispatch.S, "load_task", side_effect=lambda *_a: dict(task)), \
             mock.patch.object(dispatch.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(dispatch, "_proc_start_time", return_value="wrapper"), \
             mock.patch.object(dispatch, "_cancel_new_child", return_value=True) as cancel, \
             mock.patch.object(dispatch.os, "write") as release:
            with self.assertRaisesRegex(Exception, "cancelled during wrapper launch"):
                dispatch._start_codex_worker(
                    "demo", "task", "task-1", self.root, "prompt", origin_sha="a" * 40,
                    trusted_git_dir=self.root,
                    generation="g", claim_field="pending_dispatch")
        cancel.assert_called_once_with(child, "wrapper")
        release.assert_not_called()

    def test_closed_wrapper_gate_never_starts_engine(self):
        prompt = self.task_dir / "prompt.md"; prompt.write_text("work")
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "running",
            "worktree": str(self.root), "origin_sha": "a" * 40, "prompt": str(prompt),
            "trusted_git_dir": str(self.root),
        })
        gate_read, gate_write = os.pipe(); os.close(gate_write)
        with mock.patch.dict(os.environ, {"ALTITUDE_CODEX_L2_GENERATION": "g",
                                          dispatch.CODEX_GATE_FD_ENV: str(gate_read)}), \
             mock.patch.object(dispatch.engines, "codex_exec") as engine:
            record = dispatch.exec_codex_l2("demo", "task", "task-1")
        engine.assert_not_called()
        self.assertEqual(record["state"], "failed")
        self.assertIn("gate closed", record["result"]["error"])

    def test_wrapper_reaps_descendants_before_recording_terminal_failure(self):
        prompt = self.task_dir / "prompt.md"; prompt.write_text("work")
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "running",
            "worktree": str(self.root), "origin_sha": "a" * 40, "prompt": str(prompt),
            "trusted_git_dir": str(self.root),
        })
        gate_read, gate_write = os.pipe(); os.write(gate_write, b"1"); os.close(gate_write)
        result = {"text": "", "usage": {}, "session_id": None, "returncode": 1, "error": "timeout"}
        with mock.patch.dict(os.environ, {"ALTITUDE_CODEX_L2_GENERATION": "g",
                                          dispatch.CODEX_GATE_FD_ENV: str(gate_read)}), \
             mock.patch.object(dispatch.config, "project_path", return_value=self.root), \
             mock.patch.object(dispatch.config, "project", return_value={}), \
             mock.patch.object(dispatch, "_validate_task_worktree", return_value=self.root), \
             mock.patch.object(dispatch.engines, "codex_exec", return_value=result), \
             mock.patch.object(dispatch.engines, "reap_process_group_members", side_effect=[False, True]) as reap, \
             mock.patch.object(dispatch.time, "sleep"), \
             mock.patch.object(dispatch.S, "append_event"):
            record = dispatch.exec_codex_l2("demo", "task", "task-1")
        self.assertEqual((reap.call_count, record["state"]), (2, "failed"))
        self.assertEqual(record["result"]["error"], "timeout")

    def test_stopped_or_replaced_generation_cannot_be_resurrected(self):
        path = dispatch.codex_run_path("demo", "task")
        dispatch.S.write_json(path, {"dispatch_id": "task-1", "generation": "new", "state": "stopped"})
        self.assertIsNone(dispatch._update_codex_record("demo", "task", "task-1", "new", state="done"))
        self.assertEqual(dispatch.codex_run("demo", "task")["state"], "stopped")
        self.assertIsNone(dispatch._update_codex_record("demo", "task", "task-1", "old", state="running"))

    def test_stop_failed_generation_can_be_reaped_on_retry(self):
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "stop-failed",
            "pid": 123, "pid_start": "wrapper", "engine_pid": 456, "engine_pid_start": "engine",
        })
        with mock.patch.object(dispatch, "codex_processes_live", side_effect=[True, False]), \
             mock.patch.object(dispatch, "_kill_process_group", side_effect=[True, True]) as kill:
            self.assertTrue(dispatch.stop_codex_worker("demo", "task", "task-1"))
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(dispatch.codex_run("demo", "task")["state"], "stopped")

    def test_stop_failure_stays_visible_while_process_evidence_remains(self):
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "failed",
            "pid": 123, "pid_start": "wrapper", "engine_pid": 456, "engine_pid_start": "engine",
        })
        with mock.patch.object(dispatch, "codex_processes_live", side_effect=[True, True]), \
             mock.patch.object(dispatch, "_kill_process_group", side_effect=[True, True]):
            self.assertFalse(dispatch.stop_codex_worker("demo", "task", "task-1"))
        self.assertEqual(dispatch.codex_run("demo", "task")["state"], "stop-failed")



    @staticmethod
    def _wait_pid_file(path: Path) -> int:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists() and path.read_text().strip():
                return int(path.read_text().strip())
            time.sleep(0.02)
        raise AssertionError(f"process did not publish child pid to {path}")

    def test_stop_proves_group_empty_after_leader_exits_but_child_ignores_term(self):
        child_pid_file = self.root / "term-child.pid"
        child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        leader_code = (
            "import pathlib,signal,subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); time.sleep(60)"
        )
        leader = subprocess.Popen([sys.executable, "-c", leader_code], start_new_session=True)
        child_pid = self._wait_pid_file(child_pid_file)
        started = dispatch._proc_start_time(leader.pid)
        self.assertIsNotNone(started)
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "failed",
            "pid": leader.pid, "pid_start": started, "pgid": leader.pid,
            "engine_pid": None, "engine_pid_start": None,
        })
        try:
            self.assertTrue(dispatch.stop_codex_worker("demo", "task", "task-1"))
            leader.wait(timeout=3)
            self.assertIsNone(dispatch._proc_start_time(child_pid))
            self.assertEqual(dispatch.codex_run("demo", "task")["state"], "stopped")
        finally:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                leader.wait(timeout=1)
            except subprocess.TimeoutExpired:
                leader.kill(); leader.wait(timeout=1)

    def test_stop_reaps_unrecorded_engine_after_wrapper_dies_before_callback(self):
        child_pid_file = self.root / "pre-callback-child.pid"
        child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        wrapper_code = (
            "import pathlib,subprocess,sys; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid))"
        )
        wrapper = subprocess.Popen([sys.executable, "-c", wrapper_code], start_new_session=True)
        started = dispatch._proc_start_time(wrapper.pid)
        self.assertIsNotNone(started)
        child_pid = self._wait_pid_file(child_pid_file)
        wrapper.wait(timeout=3)
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "failed",
            "pid": wrapper.pid, "pid_start": started, "pgid": wrapper.pid,
            "engine_pid": None, "engine_pid_start": None,
        })
        try:
            self.assertTrue(dispatch.codex_processes_live(dispatch.codex_run("demo", "task")))
            self.assertTrue(dispatch.stop_codex_worker("demo", "task", "task-1"))
            self.assertIsNone(dispatch._proc_start_time(child_pid))
        finally:
            try:
                os.killpg(wrapper.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_reused_group_leader_fails_closed_without_signalling(self):
        record = {"pid": 123, "pid_start": "original", "pgid": 123}
        with mock.patch.object(dispatch.engines, "process_group_members", return_value={123: "reused"}), \
             mock.patch.object(dispatch.os, "killpg") as kill:
            self.assertTrue(dispatch.codex_processes_live(record))
            self.assertFalse(dispatch._kill_codex_group(record))
        kill.assert_not_called()

    def test_failed_live_generation_must_stop_before_resume(self):
        task = {"slug": "task", "state": "running", "dispatch_id": "task-1",
                "worktree": str(self.root), "origin_sha": "a" * 40,
                "session_id": "thread", "envelope": {"max_turns": 5}}
        dispatch.S.save_task("demo", task)
        dispatch.S.write_json(dispatch.codex_run_path("demo", "task"), {
            "dispatch_id": "task-1", "generation": "g", "state": "failed",
            "pid": 123, "pid_start": "wrapper", "pgid": 123,
        })
        with mock.patch.object(dispatch, "task_l2_engine", return_value="codex"), \
             mock.patch.object(dispatch.config, "project_path", return_value=self.root), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree", return_value=self.root), \
             mock.patch.object(dispatch, "codex_processes_live", return_value=True), \
             mock.patch.object(dispatch, "stop_codex_worker", return_value=False) as stop, \
             mock.patch.object(dispatch, "_start_codex_worker") as launch:
            with self.assertRaisesRegex(Exception, "could not be stopped"):
                dispatch.resume_session("demo", "task", "continue")
        stop.assert_called_once_with("demo", "task", "task-1")
        launch.assert_not_called()

    def test_after_cli_count_merge_is_project_locked(self):
        entered = []
        @contextlib.contextmanager
        def locked(_project):
            entered.append(True)
            yield
        with mock.patch.object(dispatch.S, "project_lock", side_effect=locked), \
             mock.patch("altitude.l1.billed_count", return_value=2), \
             mock.patch.object(dispatch.S, "read_json", return_value={"tool_calls": 4}), \
             mock.patch.object(dispatch.S, "write_json") as write:
            dispatch._codex_after_cli("demo", "task", "task-1", ["l1", "run"], 0)
        self.assertEqual(entered, [True])
        self.assertEqual(write.call_args.args[1], {"tool_calls": 4, "subagent_launches": 2})


class TestCodexPolling(unittest.TestCase):
    def test_poll_uses_current_codex_record_without_querying_claude(self):
        task = {"slug": "codex", "state": "running", "dispatch_id": "codex-1", "l2_engine": "codex"}
        record = {"dispatch_id": "codex-1", "generation": "g", "state": "failed", "pid": 999999,
                  "result": {"error": "engine failed"}}
        with tempfile.TemporaryDirectory(prefix="altitude-codex-poll-") as tmp, \
             mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "task_dir", return_value=Path(tmp)), \
             mock.patch.object(dispatch.config, "MONITOR_DIR", Path(tmp)), \
             mock.patch.object(dispatch, "codex_run", return_value=record), \
             mock.patch.object(engines, "claude_agents", side_effect=AssertionError("must not query Claude")):
            outcome = dispatch.poll("demo")
        self.assertEqual(len(outcome), 1)
        self.assertTrue(outcome[0]["died"])
        self.assertEqual(outcome[0]["error"], "engine failed")

    def test_failed_record_with_exact_live_engine_remains_busy(self):
        task = {"slug": "codex", "state": "running", "dispatch_id": "codex-1", "l2_engine": "codex"}
        record = {"dispatch_id": "codex-1", "generation": "g", "state": "failed",
                  "pid": 999999, "pid_start": "gone", "engine_pid": 456, "engine_pid_start": "engine"}
        with tempfile.TemporaryDirectory(prefix="altitude-codex-poll-") as tmp, \
             mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(dispatch.S, "task_dir", return_value=Path(tmp)), \
             mock.patch.object(dispatch.config, "MONITOR_DIR", Path(tmp)), \
             mock.patch.object(dispatch, "codex_run", return_value=record), \
             mock.patch.object(dispatch, "_proc_start_time", side_effect=lambda pid: "engine" if pid == 456 else None), \
             mock.patch.object(engines, "claude_agents", side_effect=AssertionError("must not query Claude")):
            outcome = dispatch.poll("demo")
            live = json.loads((Path(tmp) / "live-demo--codex.json").read_text())
        self.assertEqual(outcome, [])
        self.assertTrue(live["process_live"])
        self.assertEqual((live["agent"]["state"], live["agent"]["status"]), ("working", "busy"))

    def test_report_generation_rejects_a_stale_report(self):
        with tempfile.TemporaryDirectory(prefix="altitude-report-generation-") as tmp:
            task_dir = Path(tmp)
            report = task_dir / "report.json"
            report.write_text("{}")
            os.utime(report, (1, 1))
            task = {"slug": "stale", "report_not_before":
                    (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}
            with mock.patch.object(dispatch.S, "task_dir", return_value=task_dir):
                self.assertFalse(dispatch._current_report("demo", task))
                task.pop("report_not_before")
                self.assertTrue(dispatch._current_report("demo", task))


class TestCodexEnvelopeHook(unittest.TestCase):
    def call(self, root: Path, key: str, payload: dict) -> subprocess.CompletedProcess:
        env = {**os.environ, "ALTITUDE_HOME": str(root), "ALTITUDE_SESSION_KEY": key,
               "ALTITUDE_PROJECT": "p", "ALTITUDE_TASK": "t", "ALTITUDE_L2_CAP_TEST_LOCAL": "1"}
        return subprocess.run(
            [str(config.HOOKS / "codex_l2_cap.py")], input=json.dumps(payload), text=True,
            capture_output=True, env=env,
        )

    def test_missing_or_corrupt_envelope_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); (root / "monitor").mkdir()
            payload = {"tool_use_id": "one", "tool_name": "exec_command", "tool_input": {"cmd": "true"}}
            missing = self.call(root, "p--t", payload)
            (root / "monitor" / "envelope-p--t.json").write_text("not json")
            corrupt = self.call(root, "p--t", payload)
        self.assertEqual((missing.returncode, corrupt.returncode), (2, 2))
        self.assertIn("failed closed", missing.stderr)

    def test_deduplicates_and_blocks_launch_past_cap(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); monitor = root / "monitor"; monitor.mkdir()
            key = "p--t"
            (monitor / f"envelope-{key}.json").write_text(json.dumps({"subagent_launches": 1, "max_turns": 20}))
            def payload(call_id):
                return {"tool_use_id": call_id, "tool_name": "exec_command",
                        "tool_input": {"cmd": "alt l1 run --brief sub.md"}}
            first = self.call(root, key, payload("one"))
            duplicate = self.call(root, key, payload("one"))
            second = self.call(root, key, payload("two"))
            counts = json.loads((monitor / f"counts-{key}.json").read_text())
        self.assertEqual((first.returncode, duplicate.returncode), (0, 0))
        self.assertEqual(second.returncode, 2)
        self.assertEqual(counts["subagent_launches"], 1)
        self.assertEqual(counts["denied_launches"], 1)

    def test_bills_python_wrappers_and_shell_source_but_not_data_or_prose(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); monitor = root / "monitor"; monitor.mkdir()
            key = "p--t"
            (monitor / f"envelope-{key}.json").write_text(json.dumps({"subagent_launches": 20, "max_turns": 20}))
            commands = [
                "python3 bin/alt l1 run --brief one.md",
                'env -u TOKEN bash -c "python3 bin/alt l1 run --brief two.md"',
                'timeout --signal KILL 5 bash -c "alt l1 run --brief three.md"',
                "bash <<'EOF'\npython3 bin/alt l1 run --brief four.md\nEOF\n",
                "cat <<'EOF'\npython3 bin/alt l1 run --brief data.md\nEOF\n",
                'echo "python3 bin/alt l1 run --brief prose.md"',
                "python3 bin/alt l1 run --help",
            ]
            results = []
            for index, command in enumerate(commands):
                results.append(self.call(root, key, {
                    "tool_use_id": f"call-{index}", "tool_name": "exec_command",
                    "tool_input": {"cmd": command},
                }))
            counts = json.loads((monitor / f"counts-{key}.json").read_text())
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(counts["subagent_launches"], 4)

    def test_checkpoint_commands_are_bound_to_current_task_slug(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); monitor = root / "monitor"; monitor.mkdir()
            (root / "p" / "tasks" / "t").mkdir(parents=True)
            key = "p--t"
            (monitor / f"envelope-{key}.json").write_text(json.dumps({"subagent_launches": 1, "max_turns": 1}))
            (monitor / f"counts-{key}.json").write_text(json.dumps({"tool_calls": 1, "subagent_launches": 0, "edits": 0}))

            def command(call_id, text):
                return self.call(root, key, {"tool_use_id": call_id, "tool_name": "exec_command",
                                             "tool_input": {"cmd": text}})

            own_status = command("status", "alt task status t")
            replay = command("status", "alt task status t")
            implicit_status = command("implicit", "alt task status")
            wrong_status = command("wrong-status", "alt task status another")
            own_block = command("block", 'alt task block t --reason "cap reached"')
            wrong_block = command("wrong-block", 'alt task block another --reason "cap reached"')
        self.assertEqual((own_status.returncode, replay.returncode, implicit_status.returncode, own_block.returncode), (0, 0, 0, 0))
        self.assertEqual((wrong_status.returncode, wrong_block.returncode), (2, 2))

    def test_checkpoint_custom_patch_targets_stay_in_owned_task_dir(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); monitor = root / "monitor"; monitor.mkdir()
            task_dir = root / "p" / "tasks" / "t"; task_dir.mkdir(parents=True)
            key = "p--t"
            (monitor / f"envelope-{key}.json").write_text(json.dumps({"subagent_launches": 1, "max_turns": 1}))
            (monitor / f"counts-{key}.json").write_text(json.dumps({"tool_calls": 1, "subagent_launches": 0, "edits": 0}))

            def patch(call_id, marker, target):
                body = f"*** Begin Patch\n*** {marker} File: {target}\n*** End Patch\n"
                return self.call(root, key, {"tool_use_id": call_id, "tool_name": "apply_patch",
                                             "tool_input": {"patch": body}})

            allowed = [patch(f"own-{marker}", marker, task_dir / "report.md")
                       for marker in ("Update", "Add", "Delete")]
            outside = patch("outside", "Update", root / "report.md")
            wrong_name = patch("wrong-name", "Add", task_dir / "notes.md")
            mixed_body = (f"*** Begin Patch\n*** Update File: {task_dir / 'progress.md'}\n"
                          f"*** Add File: {root / 'report.json'}\n*** End Patch\n")
            mixed = self.call(root, key, {"tool_use_id": "mixed", "tool_name": "apply_patch", "tool_input": {"patch": mixed_body}})
            moved_body = (f"*** Begin Patch\n*** Update File: {task_dir / 'report.md'}\n"
                          f"*** Move to: {root / 'report.md'}\n*** End Patch\n")
            moved = self.call(root, key, {"tool_use_id": "moved", "tool_name": "apply_patch", "tool_input": {"patch": moved_body}})
        self.assertTrue(all(result.returncode == 0 for result in allowed))
        self.assertEqual((outside.returncode, wrong_name.returncode, mixed.returncode, moved.returncode), (2, 2, 2, 2))

    def test_missing_tool_id_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="altitude-codex-hook-") as tmp:
            root = Path(tmp); monitor = root / "monitor"; monitor.mkdir()
            (monitor / "envelope-p--t.json").write_text(json.dumps({"subagent_launches": 1, "max_turns": 1}))
            result = self.call(root, "p--t", {"tool_name": "exec_command", "tool_input": {"cmd": "true"}})
        self.assertEqual(result.returncode, 2)
        self.assertIn("tool_use_id", result.stderr)


class TestHostCodexEnvelope(unittest.TestCase):
    def test_host_counter_dedupes_hard_caps_and_keeps_checkpoint_open(self):
        with tempfile.TemporaryDirectory(prefix="altitude-host-envelope-") as tmp:
            root = Path(tmp); worktree = root / "worktree"; worktree.mkdir()
            task = {"slug": "t", "state": "running", "dispatch_id": "t-1",
                    "envelope": {"max_turns": 1, "subagent_launches": 3}}
            record = {"dispatch_id": "t-1", "generation": "g", "state": "running"}
            payload = {"tool_use_id": "one", "tool_name": "apply_patch",
                       "tool_input": {"patch": "*** Begin Patch\n*** Update File: code.py\n*** End Patch\n"}}
            with mock.patch.object(dispatch.config, "MONITOR_DIR", root / "monitor"), \
                 mock.patch.object(dispatch.S, "project_lock", side_effect=lambda _p: contextlib.nullcontext()), \
                 mock.patch.object(dispatch.S, "load_task", return_value=task), \
                 mock.patch.object(dispatch, "codex_run", return_value=record):
                first = dispatch._codex_envelope_hook("p", "t", "t-1", "g", worktree, payload)
                duplicate = dispatch._codex_envelope_hook("p", "t", "t-1", "g", worktree, payload)
                denied = dispatch._codex_envelope_hook("p", "t", "t-1", "g", worktree, {
                    "tool_use_id": "two", "tool_name": "Read", "tool_input": {"file_path": "code.py"}})
                checkpoint = dispatch._codex_envelope_hook("p", "t", "t-1", "g", worktree, {
                    "tool_use_id": "three", "tool_name": "Write",
                    "tool_input": {"file_path": str(worktree / ".altitude-checkpoints" / "progress.md")}})
            counts = json.loads((root / "monitor" / "counts-p--t-1.json").read_text())
        self.assertEqual((first["allowed"], duplicate["allowed"], denied["allowed"], checkpoint["allowed"]),
                         (True, True, False, True))
        self.assertEqual((counts["tool_calls"], counts["edits"], counts["denied_tool_calls"]), (1, 1, 1))
        self.assertNotIn("subagent_launches", counts, "L1 launch billing remains authoritative in l1.start")

    def test_checkpoint_generation_validation_and_write_share_project_lock(self):
        with tempfile.TemporaryDirectory(prefix="altitude-checkpoint-lock-") as tmp:
            root = Path(tmp); held = []

            @contextlib.contextmanager
            def lock(_project):
                held.append(True)
                try:
                    yield
                finally:
                    held.pop()

            task = {"state": "running", "dispatch_id": "t-1"}
            record = {"state": "running", "dispatch_id": "t-1", "generation": "g"}
            def atomic_write(path, text):
                self.assertTrue(held, "checkpoint write escaped the generation-validation lock")
                Path(path).write_text(text)

            with mock.patch.object(dispatch.S, "project_lock", side_effect=lock), \
                 mock.patch.object(dispatch.S, "load_task", return_value=task), \
                 mock.patch.object(dispatch, "codex_run", return_value=record), \
                 mock.patch.object(dispatch.S, "task_dir", return_value=root), \
                 mock.patch.object(dispatch.S, "atomic_write", side_effect=atomic_write), \
                 mock.patch.object(dispatch.S, "append_event"):
                dispatch._codex_checkpoint("p", "t", "t-1", "g", "progress.md", b"safe")
            self.assertEqual((root / "progress.md").read_text(), "safe")



class TestCodexSandboxPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-codex-preflight-")
        self.cwd = Path(self.temp.name) / "worktree"
        self.cwd.mkdir()
        self.codex_calls = []
        self.popen_patch = mock.patch.object(
            engines.subprocess, "Popen",
            side_effect=lambda cmd, **_kwargs: self._codex_success(cmd),
        )
        self.popen_patch.start()

    def tearDown(self):
        self.popen_patch.stop()
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
        with mock.patch.object(subprocess, "Popen", REAL_SUBPROCESS_POPEN):
            return REAL_SUBPROCESS_RUN(cmd[shell_at:], **kwargs)

    def _codex_success(self, cmd):
        self.codex_calls.append(cmd)
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text('{"answer": "ok"}\n')

        class Process:
            pid = 456
            returncode = 0

            def __init__(self):
                self.stdout = io.StringIO(
                    '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}\n')
                self.stderr = io.StringIO("")

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        return Process()

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
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.codex_calls), 1)
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

        with mock.patch.object(engines.shutil, "which") as which:
            with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                with mock.patch("altitude.improve.system_fault") as fault:
                    result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="read-only")

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.codex_calls), 1)
        which.assert_not_called()
        fault.assert_not_called()

    def test_preflight_subprocess_strictly_precedes_codex(self):
        order = []

        def fake_run(cmd, **kwargs):
            order.append("preflight")
            return self._probe_success(cmd)

        def fake_popen(cmd, **kwargs):
            order.append("codex")
            return self._codex_success(cmd)

        with mock.patch.object(engines.sys, "platform", "linux"):
            with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/bwrap"):
                with mock.patch.object(engines.subprocess, "run", side_effect=fake_run):
                    with mock.patch.object(engines.subprocess, "Popen", side_effect=fake_popen):
                        engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertEqual(order, ["preflight", "codex"])

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
                    with mock.patch("altitude.improve.system_fault", side_effect=OSError("fault store closed")):
                        with self.assertLogs(engines.logger.name, level="ERROR") as logs:
                            result = engines.codex_exec("prompt", cwd=self.cwd, sandbox="workspace-write")

        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("namespace unavailable", result["error"])
        self.assertIsNone(result["fault_recorded"])
        self.assertIn("Failed to record Codex sandbox preflight system fault", "\n".join(logs.output))

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
