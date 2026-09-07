"""Regression coverage for the Altitude service ownership boundary."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests.support import REPO, AltitudeCase, add_worktree, make_repo
from altitude import config, dispatch, engines, server, state as S, tasks as T


class TestServiceLifecycle(unittest.TestCase):
    def test_stop_owns_the_entire_service_cgroup(self):
        unit = (REPO / "systemd" / "altitude.service").read_text()
        settings = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in unit.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }

        self.assertEqual(settings.get("KillMode"), "control-group")
        self.assertNotIn("KillMode=process", unit)


class TestDecision11WorkerContinuity(AltitudeCase):
    """Decision 11: running workers do not hold activation (2026-09-07 queue starvation)."""

    def task(self, engine):
        task = T.new(self.project, f"Running {engine} worker", "Produce a proposal.")
        task.update({"state": "running", "attempt": 1, "l2_engine": engine,
                     "agent_id": f"{engine}-worker", "session_id": f"{engine}-session"})
        S.save_task(self.project, task)
        return task

    def test_decision_11_running_jobs_are_adopted_after_restart_and_finish_normally(self):
        make_repo(self.repo)
        self.private_ledgers()
        tasks = [self.task(engine) for engine in ("claude", "codex")]
        for task in tasks:
            worktree = add_worktree(self.repo, task["slug"])
            task.update({"worktree": str(worktree), "branch": f"worktree-{task['slug']}"})
            S.save_task(self.project, task)
        claude, codex = tasks
        registry = [{"id": claude["agent_id"], "sessionId": claude["session_id"],
                     "state": "working", "status": "busy", "pid": 1001}]
        paths = engines._codex_paths(dispatch.l2_job_root(self.project, codex["slug"]), codex["agent_id"])
        S.write_json(paths["record"], {"id": codex["agent_id"], "session_id": codex["session_id"],
                                      "name": codex["slug"], "pid": 1002, "unit": "test-worker.service",
                                      "started_at": S.now(), "engine_model": "test-model"})
        paths["stdout"].write_text(json.dumps({"type": "thread.started", "thread_id": codex["session_id"]}) + "\n")
        paths["stderr"].write_text("")
        # A replacement daemon has no process handles. The external registry and unit still own the workers.
        with mock.patch.dict(engines._codex_processes, {}, clear=True), \
             mock.patch.object(engines, "claude_agents", return_value=registry), \
             mock.patch.object(engines, "_unit_active", return_value=True) as unit_active:
            self.assertEqual(dispatch.poll(self.project), [])
            unit_active.assert_called_once_with("test-worker.service")
            for task in tasks:
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual((saved["state"], saved["agent_id"], saved["session_id"]),
                                 ("running", task["agent_id"], task["session_id"]))
                live = S.read_json(config.MONITOR_DIR / f"live-{self.project}--{task['slug']}.json")
                self.assertEqual(live["agent"]["state"], "working")
                T.done(self.project, task["slug"], actor="l2", digest="Proposal complete.", expected_attempt=1)
            registry[0].update({"state": "done", "status": "exited"})
            with paths["stdout"].open("a") as out:
                out.write('{"type":"turn.completed"}\n')
            unit_active.return_value = False
            finished = dispatch.poll(self.project)
            self.assertEqual({item["task"]["slug"] for item in finished}, {task["slug"] for task in tasks})
            for item in finished:
                self.assertEqual(item["agent"]["sessionId"], item["task"]["session_id"])
                server.on_l2_finished(self.project, item)
                self.assertEqual(S.load_task(self.project, item["task"]["slug"])["state"], "done")

    def test_decision_11_worker_cli_and_inbox_work_without_a_daemon(self):
        # L2 commands and hooks use locked files: a restart must not invent a daemon dependency or lose a message.
        task = self.task("claude")
        env = dispatch.l2_env(self.project, task["slug"], 1)
        result = self.alt("task", "reply", "Still working during activation.", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(T.task_messages(self.project, task["slug"])[-1]["text"],
                         "Still working during activation.")
        T.message(self.project, task["slug"], "burak", "Keep the same session.")
        hook = subprocess.run([sys.executable, str(REPO / "hooks" / "inbox.py")],
                              input=json.dumps({"hook_event_name": "PostToolUse"}),
                              text=True, capture_output=True, env={**os.environ, **env}, timeout=30)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertIn("Keep the same session.", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(T.pending(self.project, task["slug"]), [])

    def test_decision_11_worker_path_resolves_the_deployment_cli(self):
        for env in (engines.clean_env(), engines.codex_env()):
            self.assertEqual(shutil.which("alt", path=env["PATH"]), str(config.REPO / "bin" / "alt"))


if __name__ == "__main__":
    unittest.main()
