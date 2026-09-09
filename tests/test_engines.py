"""I-20260907-171446: each L2 foreground CLI belongs to its task's transient unit."""
import io
import json
import os
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, state as S


class _Input(io.BytesIO):
    def close(self):
        pass


class _Process:
    def __init__(self, stdout, session="session"):
        self.pid, self.stdin = 4242, _Input()
        stdout.write((json.dumps({"type": "system", "subtype": "init", "session_id": session,
                                 "model": "observed-model"}) + "\n").encode())

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class TestForegroundUnits(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.job_root = self.tmp / "jobs"
        self.settings = self.tmp / "settings.json"
        self.settings.write_text('{"modelSettings":{"fable":{"maxTokens":1000}}}')
        self.persona = self.tmp / "persona.md"
        self.persona.write_text("Worker instructions")
        self.patch(engines, "_codex_processes", {})

    def launch(self, *, resume=False, model=None):
        kw = dict(cwd=self.repo, persona=self.persona, model=model, settings=self.settings,
                  extra_env={"ALTITUDE_TASK": "worker"}, job_root=self.job_root)
        if resume:
            return engines.resume_l2("claude", "project/worker-1", "session", "continue", **kw)
        return engines.start_l2("claude", "project/worker-1", "brief", **kw)

    def test_ci_coordinator_timeout_survives_daemon_exit_on_both_engines(self):
        for engine, execute in (("claude", engines.claude_print), ("codex", engines.codex_exec)):
            with self.subTest(engine=engine), mock.patch.object(engines.subprocess, "Popen",
                    side_effect=RuntimeError("fixture: execution intercepted")) as popen:
                with self.assertRaisesRegex(RuntimeError, "intercepted"):
                    execute("Probe evidence", cwd=self.repo, timeout=37, durable_timeout=True)
                cmd = popen.call_args.args[0]
                self.assertEqual(cmd[0], engines.SYSTEMD_RUN_BIN)
                for flag in ("--property=RuntimeMaxSec=37", "--property=TimeoutStopSec=5",
                             "--property=KillMode=control-group", "--property=SendSIGKILL=yes"):
                    self.assertIn(flag, cmd)
                self.assertIn("DBUS_SESSION_BUS_ADDRESS", popen.call_args.kwargs["env"])
                child = cmd[cmd.index("--") + 1:]
                self.assertFalse(any(arg.startswith("DBUS_SESSION_BUS_ADDRESS=") for arg in child))

    def test_incident_171446_launch_and_resume_share_foreground_unit_for_both_models(self):
        for model in (None, "fable"):
            for resume in (False, True):
                with self.subTest(model=model, resume=resume), \
                     mock.patch.object(engines, "claude_agents", return_value=[]), \
                     mock.patch.object(engines, "_unit_active", return_value=True), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"])) as popen:
                    result = self.launch(resume=resume, model=model)
                    self.assertEqual(result["returncode"], 0)
                    row = result["agent"]
                    self.assertEqual((row["state"], row["sessionId"], row["engine_model"]),
                                     ("working", "session", "observed-model"))
                    cmd = popen.call_args.args[0]
                    self.assertEqual(cmd[0], engines.SYSTEMD_RUN_BIN)
                    for flag in ("--user", "--wait", "--pipe", f"--unit={row['unit']}",
                                 "--property=KillMode=control-group", "--property=SendSIGKILL=yes"):
                        self.assertIn(flag, cmd)
                    self.assertNotIn("--service-type=forking", cmd)
                    child = cmd[cmd.index("--") + 1:]
                    self.assertEqual(child[:2], [engines.ENV_BIN, "-i"])
                    self.assertIn("ALTITUDE_TASK=worker", child)
                    self.assertFalse(any(arg.startswith("DBUS_SESSION_BUS_ADDRESS=") for arg in child))
                    cli = child[child.index(config.CLAUDE_BIN):]
                    self.assertIn("-p", cli)
                    self.assertEqual(cli[cli.index("--output-format") + 1], "stream-json")
                    self.assertEqual(cli[cli.index("--settings") + 1], str(self.settings))
                    self.assertEqual(cli[cli.index("--append-system-prompt-file") + 1], str(self.persona))
                    self.assertNotIn("--bg", cli)
                    self.assertEqual("--resume" in cli, resume)
                    self.assertEqual("--model" in cli, model is not None)
                    if model:
                        self.assertEqual(cli[cli.index("--model") + 1], model)
                    self.assertIn("DBUS_SESSION_BUS_ADDRESS", popen.call_args.kwargs["env"])
                    self.assertEqual(popen.call_args.kwargs["cwd"], str(self.repo))
                    self.assertEqual(engines._codex_processes[row["id"]].stdin.getvalue(),
                                     b"continue" if resume else b"brief")

    def test_incident_171446_provider_stream_from_real_process_preserves_result_and_error(self):
        real_popen = subprocess.Popen
        for error in (False, True):
            with self.subTest(error=error):
                events = [{"type": "system", "subtype": "init", "session_id": "session", "model": "fable"},
                          {"type": "result", "is_error": error, "result": "provider rejected this turn" if error else "done",
                           "usage": {"input_tokens": 9}}]
                script = "import sys\nsys.stdin.read()\n" + "\n".join(f"print({json.dumps(json.dumps(event))}, flush=True)" for event in events)
                processes = []
                def popen(cmd, **kw):
                    proc = real_popen([sys.executable, "-c", script], **kw)
                    processes.append(proc)
                    return proc
                with mock.patch.object(engines, "claude_agents", return_value=[]), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
                     mock.patch.object(engines, "_unit_active", side_effect=lambda _unit: processes[-1].poll() is None):
                    result = self.launch(model="fable")
                    processes[-1].wait(timeout=5)
                    row = engines.worker("claude", {"agent_id": result["agent"]["id"]}, job_root=self.job_root)
                self.assertEqual(result["returncode"], 0)
                self.assertEqual((row["state"], row["sessionId"]), ("failed" if error else "done", "session"))
                if error:
                    self.assertIn("provider rejected this turn", engines.worker_detail("claude", row)[0])
                else:
                    self.assertEqual(row["usage"]["input_tokens"], 9)

    def test_incident_171446_resume_rejects_another_session_and_stops_its_unit(self):
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(engines, "_unit_active", return_value=False), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"], "wrong")), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            result = self.launch(resume=True)
        self.assertEqual(result["returncode"], 1)
        self.assertIn("different", result["stderr"])
        self.assertEqual(run.call_args.args[0], [engines.SYSTEMCTL_BIN, "--user", "stop", result["agent"]["unit"]])

    def test_incident_171446_startup_exception_stops_the_unbound_unit(self):
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(engines, "_unit_active", return_value=False), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"])), \
             mock.patch.object(engines, "_worker_events", side_effect=OSError("output disk unavailable")), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            with self.assertRaisesRegex(OSError, "output disk unavailable"):
                self.launch()
        record = S.read_json(next(self.job_root.glob("*.json")))
        self.assertTrue(record["stopped"])
        self.assertEqual(run.call_args.args[0], [engines.SYSTEMCTL_BIN, "--user", "stop", record["unit"]])
        self.assertNotIn(record["id"], engines._codex_processes)

    def test_incident_171446_launch_and_resume_stop_only_the_named_orphan_first(self):
        rows = [{"id": "orphan", "name": "project/worker-1", "state": "working"},
                {"id": "other", "name": "project/other-1", "state": "working"},
                {"id": "done", "name": "project/worker-1", "state": "done"}]
        for resume in (False, True):
            order = []
            def popen(cmd, **kw):
                order.append("launch")
                return _Process(kw["stdout"])
            with self.subTest(resume=resume), \
                 mock.patch.object(engines, "claude_agents", return_value=rows), \
                 mock.patch.object(engines, "claude_stop", side_effect=lambda worker: order.append(worker)), \
                 mock.patch.object(engines, "_unit_active", return_value=True), \
                 mock.patch.object(engines.subprocess, "Popen", side_effect=popen):
                self.launch(resume=resume)
            self.assertEqual(order, ["orphan", "launch"])

    def test_incident_171446_failed_orphan_stop_prevents_a_duplicate_launch(self):
        with mock.patch.object(engines, "claude_agents", return_value=[
                {"id": "orphan", "name": "project/worker-1", "state": "working"}]), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "stop refused")), \
             mock.patch.object(engines.subprocess, "Popen") as popen:
            for resume in (False, True):
                with self.subTest(resume=resume), self.assertRaisesRegex(RuntimeError, "stop refused"):
                    self.launch(resume=resume)
            popen.assert_not_called()

    def test_incident_171446_orphan_stop_waits_for_terminal_registry_state(self):
        for terminal in (True, False):
            rows = [[{"id": "old", "state": "working"}], [{"id": "old", "state": "stopped"}]]
            with self.subTest(terminal=terminal), \
                 mock.patch.object(engines, "claude_agents", side_effect=rows if terminal else None,
                                   return_value=rows[0]), \
                 mock.patch.object(engines.time, "monotonic", side_effect=[0, 1, 6]), \
                 mock.patch.object(engines.time, "sleep"), \
                 mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "stopped", "")):
                if terminal:
                    self.assertEqual(engines.claude_stop("old"), "stopped")
                else:
                    with self.assertRaisesRegex(RuntimeError, "still running after stop"):
                        engines.claude_stop("old")

    def test_incident_171446_failure_time_survives_later_record_writes(self):
        paths = engines._codex_paths(self.job_root, "worker")
        S.write_json(paths["record"], {"engine": "claude", "unit": "owned.service"})
        paths["stdout"].write_text('{"type":"result","is_error":true,"result":"resets 8pm"}\n')
        paths["stderr"].write_text("")
        for key in ("stdout", "stderr"):
            os.utime(paths[key], (100, 100))
        with mock.patch.object(engines, "_unit_active", return_value=False):
            row = engines.worker("claude", {"agent_id": "worker"}, job_root=self.job_root)
        self.assertEqual(engines.worker_detail("claude", row)[1].timestamp(), 100)

    def test_incident_171446_stop_uses_the_owned_unit_and_refuses_live_descendants(self):
        paths = engines._codex_paths(self.job_root, "worker")
        S.write_json(paths["record"], {"engine": "claude", "id": "worker", "unit": "owned.service"})
        for alive in (True, False):
            with self.subTest(alive=alive), mock.patch.object(engines, "_unit_active", return_value=alive), \
                 mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                 mock.patch.object(engines, "claude_stop") as registry_stop:
                if alive:
                    with self.assertRaisesRegex(RuntimeError, "still running after stop"):
                        engines.stop_l2_worker("claude", "worker", job_root=self.job_root)
                    self.assertNotIn("stopped", S.read_json(paths["record"]))
                else:
                    engines.remove_l2_worker("claude", "worker", job_root=self.job_root)
                    self.assertTrue(S.read_json(paths["record"])["stopped"])
                self.assertEqual(run.call_args.args[0], [engines.SYSTEMCTL_BIN, "--user", "stop", "owned.service"])
                registry_stop.assert_not_called()

    def test_incident_171446_legacy_adoption_requires_unit_and_session_transcript(self):
        self.patch(engines, "JOBS_DIR", self.tmp / "legacy-jobs")
        self.patch(config, "HOME", self.tmp / "legacy-home")
        S.write_json(engines.JOBS_DIR / "legacy" / "state.json",
                     {"name": "project/old-1", "state": "working"})
        task = {"agent_id": "legacy", "session_id": "legacy-session"}
        transcript = config.HOME / ".claude/projects/project/legacy-session.jsonl"
        transcript.parent.mkdir(parents=True)
        with mock.patch.object(engines, "claude_agents", side_effect=AssertionError("poll must not read registry")), \
             mock.patch.object(engines, "_unit_active", return_value=True) as active:
            self.assertFalse(engines.worker_live("claude", task, job_root=self.job_root))
            transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')
            self.assertTrue(engines.worker_live("claude", task, job_root=self.job_root))
            active.assert_called_with(engines._claude_unit("project/old-1"))
            active.return_value = False
            self.assertFalse(engines.worker_live("claude", task, job_root=self.job_root))
