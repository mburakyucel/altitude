"""Regression coverage for the Altitude service ownership boundary."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests.support import REPO, AltitudeCase, add_worktree, make_repo
from altitude import config, dispatch, engines, platform, server, state as S, tasks as T


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
    """Running workers do not hold activation (2026-09-07 queue starvation)."""

    def task(self, engine):
        task = T.new(self.project, f"Running {engine} worker", "Produce a proposal.")
        task.update({"state": "running", "attempt": 1, "l2_engine": engine,
                     "agent_id": f"{engine}-worker", "session_id": f"{engine}-session"})
        S.save_task(self.project, task)
        return task

    def test_incident_171446_decision_11_owned_jobs_are_adopted_after_restart_and_finish_normally(self):
        make_repo(self.repo)
        self.private_ledgers()
        tasks = [self.task(engine) for engine in ("claude", "codex")]
        for task in tasks:
            worktree = add_worktree(self.repo, task["slug"])
            task.update({"worktree": str(worktree), "branch": f"worktree-{task['slug']}"})
            S.save_task(self.project, task)
        outputs = {}
        for task in tasks:
            engine = task["l2_engine"]
            paths = engines._codex_paths(dispatch.l2_job_root(self.project, task["slug"]), task["agent_id"])
            S.write_json(paths["record"], {"id": task["agent_id"], "session_id": task["session_id"],
                                          "engine": engine, "name": task["slug"], "pid": 1002,
                                          "unit": f"test-{engine}.service", "started_at": S.now(),
                                          "engine_model": "test-model"})
            initialized = ({"type": "system", "subtype": "init", "session_id": task["session_id"]}
                           if engine == "claude" else {"type": "thread.started", "thread_id": task["session_id"]})
            paths["stdout"].write_text(json.dumps(initialized) + "\n")
            paths["stderr"].write_text("")
            outputs[engine] = paths["stdout"]
        # I-20260907-171446: replacement altd adopts both engines from records and units, without registry polling.
        with mock.patch.dict(engines._codex_processes, {}, clear=True), \
             mock.patch.object(engines, "claude_agents", side_effect=AssertionError("unexpected registry polling")), \
             mock.patch.object(platform, "job_active", return_value=True) as unit_active:
            self.assertEqual(dispatch.poll(self.project), [])
            self.assertCountEqual([call.args[0] for call in unit_active.call_args_list],
                                  ["test-claude.service", "test-codex.service"])
            for task in tasks:
                saved = S.load_task(self.project, task["slug"])
                self.assertEqual((saved["state"], saved["agent_id"], saved["session_id"]),
                                 ("running", task["agent_id"], task["session_id"]))
                live = S.read_json(config.MONITOR_DIR / f"live-{self.project}--{task['slug']}.json")
                self.assertEqual(live["agent"]["state"], "working")
                T.done(self.project, task["slug"], actor="l2", digest="Proposal complete.", expected_attempt=1)
            for engine, output in outputs.items():
                with output.open("a") as out:
                    out.write(json.dumps({"type": "result", "is_error": False} if engine == "claude"
                                         else {"type": "turn.completed"}) + "\n")
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
