"""A worker ends with its engine's failed turn, even while a command it started keeps its job running.

Real fixture engine processes, worker records, polling and finished-worker handling; only the service manager is
a process-group fixture, so a job stays active exactly while any process it started still runs."""
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, platform, server, state as S

LIMIT = "You've hit your session limit · resets 8pm (America/Los_Angeles)"
ENGINE = r'''
import json, os, subprocess, sys
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "session_id": "session"}), flush=True)
command = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open(os.environ["FIXTURE_COMMAND"], "w").write(str(command.pid))
result = os.environ["FIXTURE_RESULT"]
print(json.dumps({"type": "result", "subtype": "success", "is_error": result != "success",
                  "result": result, "api_error": "usage_limit_reached"}), flush=True)
if os.environ["FIXTURE_ENGINE"] == "recovers":  # The command's notice starts another turn, which works.
    print(json.dumps({"type": "system", "subtype": "task_notification", "status": "completed"}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"content": []}}), flush=True)
if os.environ["FIXTURE_ENGINE"] != "exits":
    command.wait()  # As Claude does: a running background command would start the next turn.
'''


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # A zombie awaiting its reaper no longer runs.
        return "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2]
    except OSError:
        return True


class TestWorkerEnd(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "claude_agents", return_value=[])
        engine = self.tmp / "engine"
        engine.write_text(f"#!{sys.executable}\n{ENGINE}")
        engine.chmod(0o755)
        self.patch(config, "CLAUDE_BIN", str(engine))
        self.jobs = self.tmp / "units"
        self.jobs.mkdir()
        self.patch(platform, "job_command", side_effect=self.job_command)
        self.patch(platform, "job_active", side_effect=self.job_active)
        self.patch(platform, "job_stop", side_effect=self.job_stop)
        self.command = self.tmp / "command.pid"
        self.addCleanup(self.cleanup)
        (self.tmp / "persona.md").write_text("Owner")
        (self.tmp / "settings.json").write_text("{}")

    # A job is a process group whose leader records its id; it stays active while the engine or its command runs.
    def job_command(self, unit, argv, env, **_):
        return ["/usr/bin/env", *(f"{key}={value}" for key, value in env.items()),
                "/bin/sh", "-c", 'echo $$ > "$0"; exec "$@"', str(self.jobs / unit), *argv]

    def job_members(self, unit):
        if not (self.jobs / unit).exists():
            return []
        group = int((self.jobs / unit).read_text())
        pids = [group] + ([int(self.command.read_text())] if self.command.exists() else [])
        members = []
        for pid in pids:
            try:
                if os.getpgid(pid) == group and _running(pid):
                    members.append(pid)
            except ProcessLookupError:
                pass
        return members

    def job_active(self, unit, env=None):
        return bool(self.job_members(unit))

    def job_stop(self, unit, env=None, *, timeout=120):
        for pid in self.job_members(unit):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while self.job_active(unit) and time.monotonic() < deadline:
            time.sleep(.05)

    def cleanup(self):
        for unit in self.jobs.iterdir():
            for pid in self.job_members(unit.name):
                os.kill(pid, signal.SIGKILL)
        for engine in engines._codex_processes.values():
            engine.wait(timeout=10)

    def launch(self, slug, *, engine_ends: bool, result: str, recovers: bool = False):
        env = {"FIXTURE_COMMAND": str(self.command), "FIXTURE_RESULT": result, "ALTITUDE_TASK": slug,
               "FIXTURE_ENGINE": "exits" if engine_ends else "recovers" if recovers else "waits"}
        job_root = dispatch.l2_job_root(self.project, slug)
        launched = engines.start_l2("claude", f"{self.project}/{slug}-1", "brief", cwd=self.repo,
                                    persona=self.tmp / "persona.md", model=None, settings=self.tmp / "settings.json",
                                    extra_env=env, job_root=job_root)
        self.assertEqual(launched["returncode"], 0, launched)
        S.save_task(self.project, {"slug": slug, "title": slug, "state": "running", "attempt": 1,
                                   "l2_engine": "claude", "routing_pinned": True, "agent_id": launched["agent"]["id"],
                                   "session_id": "session", "worker_started_at": datetime.now(timezone.utc).isoformat()})
        deadline = time.monotonic() + 30
        stdout = job_root / f"{launched['agent']['id']}.stdout.jsonl"
        while ('"assistant"' if recovers else '"result"') not in stdout.read_text() or not self.command.exists():
            self.assertLess(time.monotonic(), deadline, "fixture engine never reported its turn")
            time.sleep(.05)
        if engine_ends and (engine := engines._codex_processes.get(launched["agent"]["id"])):
            engine.wait(timeout=30)
        return S.load_task(self.project, slug), job_root

    def reconcile(self):
        """One daemon tick's worker reconciliation."""
        for item in dispatch.poll(self.project):
            server.on_l2_finished(self.project, item)

    def test_usage_limit_ends_the_worker_while_its_command_keeps_the_job_running(self):
        for engine_ends in (False, True):
            with self.subTest(engine_ends=engine_ends):
                slug = "limited-" + ("exited" if engine_ends else "waiting")
                task, job_root = self.launch(slug, engine_ends=engine_ends, result=LIMIT)
                command = int(self.command.read_text())
                row = engines.worker("claude", task, job_root=job_root)
                self.assertEqual((row["state"], row["job_active"]), ("failed", True))
                self.assertTrue(engines.worker_live("claude", task, job_root=job_root),
                                "a resume must still stop what the ended worker left running")
                self.reconcile()
                saved = S.load_task(self.project, slug)
                self.assertEqual(saved["state"], "blocked")
                self.assertTrue(saved["blocked_reason"].startswith("usage limit:"), saved["blocked_reason"])
                self.assertEqual(saved["resume_after"], saved["usage_limit"]["until"])
                self.assertIsNone(saved.get("fault"))
                self.assertFalse(_running(command), "the command the worker left running ends with it")
                self.assertFalse(engines.worker_live("claude", saved, job_root=job_root))
                self.command.unlink()

    def keeps_running(self, slug, result, *, recovers=False):
        task, job_root = self.launch(slug, engine_ends=False, result=result, recovers=recovers)
        self.assertEqual(engines.worker("claude", task, job_root=job_root)["state"], "working")
        self.reconcile()
        self.assertEqual(S.load_task(self.project, slug)["state"], "running")
        self.assertTrue(_running(int(self.command.read_text())))
        live = S.read_json(config.MONITOR_DIR / f"live-{self.project}--{slug}.json")
        self.assertEqual(live["agent"]["state"], "working")

    def test_successful_turn_waiting_on_its_command_keeps_running(self):
        self.keeps_running("waiting", "success")

    def test_activity_after_a_failed_turn_keeps_the_worker_running(self):
        self.keeps_running("recovered", LIMIT, recovers=True)
