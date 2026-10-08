"""I-20260907-171446: each L2 foreground CLI belongs to its task's transient unit."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import config, dispatch, engines, platform, state as S, tasks as T


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
    host = "linux"  # systemd fixtures

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
                self.assertEqual(cmd[0], platform.SYSTEMD_RUN)
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
                     mock.patch.object(platform, "job_active", return_value=True), \
                     mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"])) as popen:
                    result = self.launch(resume=resume, model=model)
                    self.assertEqual(result["returncode"], 0)
                    row = result["agent"]
                    self.assertEqual((row["state"], row["sessionId"], row["engine_model"]),
                                     ("working", "session", "observed-model"))
                    cmd = popen.call_args.args[0]
                    self.assertEqual(cmd[0], platform.SYSTEMD_RUN)
                    for flag in ("--user", "--wait", "--pipe", f"--unit={row['unit']}",
                                 "--property=KillMode=control-group", "--property=SendSIGKILL=yes"):
                        self.assertIn(flag, cmd)
                    self.assertNotIn("--service-type=forking", cmd)
                    child = cmd[cmd.index("--") + 1:]
                    self.assertEqual(child[:2], [platform.ENV_BIN, "-i"])
                    self.assertIn("ALTITUDE_TASK=worker", child)
                    self.assertFalse(any(arg.startswith("DBUS_SESSION_BUS_ADDRESS=") for arg in child))
                    cli = child[child.index(config.CLAUDE_BIN):]
                    self.assertIn("-p", cli)
                    self.assertEqual(cli[cli.index("--output-format") + 1], "stream-json")
                    self.assertEqual(cli[cli.index("--permission-mode") + 1], "auto")
                    self.assertEqual(cli[cli.index("--allowedTools") + 1], "Bash(alt *)")
                    self.assertEqual(cli[cli.index("--settings") + 1], str(self.settings))
                    self.assertEqual(cli[cli.index("--append-system-prompt-file") + 1], str(self.persona))
                    self.assertNotIn("--bg", cli)
                    self.assertEqual("--resume" in cli, resume)
                    self.assertEqual("--model" in cli, model is not None)
                    if model:
                        self.assertEqual(cli[cli.index("--model") + 1], model)
                    self.assertIn("DBUS_SESSION_BUS_ADDRESS", popen.call_args.kwargs["env"])
                    self.assertEqual(popen.call_args.kwargs["cwd"], str(self.repo))
                    prompt = engines._codex_processes[row["id"]].stdin.getvalue().decode()
                    self.assertTrue(prompt.endswith("\n\ncontinue" if resume else "\n\nbrief"))
                    self.assertIn(str(config.PERSONAS / "l1.md"), prompt)

    def test_incident_612_native_allowance_admits_replies_without_granting_coordinator_authority(self):
        script = '''
import json
import subprocess
import sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "session"}), flush=True)
sys.stdin.read()
arguments = sys.argv[2:]
allowed = "--allowedTools" in arguments and arguments[arguments.index("--allowedTools") + 1] == "Bash(alt *)"
if allowed:
    reply = subprocess.run([sys.executable, sys.argv[1], "task", "reply", "-"],
                           input="Owner can report through Altitude.", text=True, capture_output=True)
    denied = subprocess.run([sys.executable, sys.argv[1], "task", "new", "--title", "Unowned task", "Unauthorized"],
                            input="", text=True, capture_output=True)
    error = reply.returncode != 0 or denied.returncode == 0
    detail = reply.stderr + denied.stderr
else:
    error, detail = True, "External System Writes: coordination has no native allowance"
print(json.dumps({"type": "result", "is_error": error, "result": detail}), flush=True)
'''
        real_popen = subprocess.Popen
        for host in ("linux", "darwin"):
            for resume in (False, True):
                with self.subTest(host=host, resume=resume):
                    task = T.new(self.project, f"Owner allowance {host} {resume}", "Report the authorized result.")
                    task.update(state="running", attempt=1, session_id="session", hold_merge="Operator security review")
                    S.save_task(self.project, task)
                    processes = []

                    def popen(command, **options):
                        if host == "darwin":
                            specification = json.loads(command[-1])
                            native, environment = specification["command"], specification["env"]
                            self.assertIn(str(self.repo), specification["writable"])
                            self.assertIn(str(config.ROOT), specification["writable"])
                        else:
                            child = command[command.index("--") + 1:]
                            executable_index = child.index(config.CLAUDE_BIN)
                            environment = dict(argument.split("=", 1) for argument in child[2:executable_index])
                            native = child[executable_index:]
                        self.assertEqual(native[native.index("--permission-mode") + 1], "auto")
                        self.assertNotIn("--dangerously-skip-permissions", native)
                        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", environment)
                        options["env"] = environment
                        process = real_popen([sys.executable, "-c", script, str(ALT), *native[1:]], **options)
                        processes.append(process)
                        return process

                    with mock.patch.object(platform, "_darwin", return_value=host == "darwin"), \
                         mock.patch.object(engines, "claude_agents", return_value=[]), \
                         mock.patch.object(platform, "job_active", side_effect=lambda *_: processes[-1].poll() is None), \
                         mock.patch.object(engines.subprocess, "Popen", side_effect=popen):
                        result = engines._start_worker("claude", "owner-fixture", "Report the result.",
                            cwd=self.repo, job_root=self.job_root, resume="session" if resume else None,
                            persona=self.persona, settings=self.settings,
                            extra_env=dispatch.l2_env(self.project, task["slug"], 1))
                        self.assertEqual(result["returncode"], 0, result["stderr"])
                        self.assertEqual(processes[-1].wait(timeout=10), 0)
                        worker = engines.worker("claude", {"agent_id": result["agent"]["id"]}, job_root=self.job_root)
                    self.assertEqual(worker["state"], "done", worker["detail"])
                    self.assertEqual(T.task_messages(self.project, task["slug"])[-1]["text"],
                                     "Owner can report through Altitude.")
                    self.assertEqual(S.load_task(self.project, task["slug"])["hold_merge"], "Operator security review")
                    self.assertFalse(any(row["title"] == "Unowned task" for row in S.list_tasks(self.project)))

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
                     mock.patch.object(platform, "job_active", side_effect=lambda _unit, _env: processes[-1].poll() is None):
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
             mock.patch.object(platform, "job_active", return_value=False), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"], "wrong")), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            result = self.launch(resume=True)
        self.assertEqual(result["returncode"], 1)
        self.assertIn("different", result["stderr"])
        self.assertEqual(run.call_args.args[0], [platform.SYSTEMCTL, "--user", "stop", result["agent"]["unit"]])

    def test_incident_171446_startup_exception_stops_the_unbound_unit(self):
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(platform, "job_active", return_value=False), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=lambda cmd, **kw: _Process(kw["stdout"])), \
             mock.patch.object(engines, "_worker_events", side_effect=OSError("output disk unavailable")), \
             mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            with self.assertRaisesRegex(OSError, "output disk unavailable"):
                self.launch()
        record = S.read_json(next(self.job_root.glob("*.json")))
        self.assertTrue(record["stopped"])
        self.assertEqual(run.call_args.args[0], [platform.SYSTEMCTL, "--user", "stop", record["unit"]])
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
                 mock.patch.object(platform, "job_active", return_value=True), \
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
        with mock.patch.object(platform, "job_active", return_value=False):
            row = engines.worker("claude", {"agent_id": "worker"}, job_root=self.job_root)
        self.assertEqual(engines.worker_detail("claude", row)[1].timestamp(), 100)

    def test_incident_171446_stop_uses_the_owned_unit_and_refuses_live_descendants(self):
        paths = engines._codex_paths(self.job_root, "worker")
        unit = engines._claude_unit("worker")
        S.write_json(paths["record"], {"engine": "claude", "id": "worker", "unit": unit})
        for alive in (True, False):
            with self.subTest(alive=alive), mock.patch.object(platform, "job_active", return_value=alive), \
                 mock.patch.object(engines.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                 mock.patch.object(engines, "claude_stop") as registry_stop:
                if alive:
                    with self.assertRaisesRegex(RuntimeError, "still running after stop"):
                        engines.stop_l2_worker("claude", "worker", job_root=self.job_root)
                    self.assertNotIn("stopped", S.read_json(paths["record"]))
                else:
                    engines.remove_l2_worker("claude", "worker", job_root=self.job_root)
                    self.assertTrue(S.read_json(paths["record"])["stopped"])
                self.assertEqual(run.call_args.args[0], [platform.SYSTEMCTL, "--user", "stop", unit])
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
             mock.patch.object(platform, "job_active", return_value=True) as active:
            self.assertFalse(engines.worker_live("claude", task, job_root=self.job_root))
            transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')
            self.assertTrue(engines.worker_live("claude", task, job_root=self.job_root))
            self.assertEqual(active.call_args.args[0], engines._claude_unit("project/old-1"))
            active.return_value = False
            self.assertFalse(engines.worker_live("claude", task, job_root=self.job_root))


class TestWorkerTokenInput(AltitudeCase):
    """A worker's GitHub token arrives on its job's first input line, which the engine never reads. On macOS the
    engine once read its input file from the beginning and sent the token to its model as prompt text."""

    TOKEN = "fixture-github-token"
    ENGINE = ("import os, sys\n"
              "try:  # a file, from its beginning, wherever the position\n"
              "    data = os.pread(0, 1 << 20, 0)\n"
              "except OSError:  # a pipe\n"
              "    data = sys.stdin.buffer.read()\n"
              "sys.stdout.buffer.write(data)\n"
              "sys.stderr.write('GH_TOKEN=' + os.environ.get('GH_TOKEN', '') + '\\n')\n")

    def setUp(self):
        super().setUp()
        self.patch(engines, "github_token", return_value=self.TOKEN)
        self.patch(engines, "_codex_processes", {})
        self.patch(engines, "claude_agents", return_value=[])
        self.patch(platform, "job_active", return_value=True)
        engine = self.tmp / "engine"
        engine.write_text(f"#!{sys.executable}\n{self.ENGINE}")
        engine.chmod(0o755)
        self.patch(config, "CLAUDE_BIN", str(engine))
        self.patch(config, "CODEX_BIN", str(engine))
        (self.tmp / "persona.md").write_text("Worker instructions")
        (self.tmp / "settings.json").write_text("{}")

    def launched(self, engine: str, host: str) -> tuple[list[str], bytes]:
        """The job command and input a worker launch hands its host's service manager."""
        self.patch(platform.sys, "platform", host)
        launches = []

        def popen(cmd, **kw):
            event = ({"type": "system", "subtype": "init", "session_id": "s"} if engine == "claude" else
                     {"type": "thread.started", "thread_id": "s"})
            kw["stdout"].write((json.dumps(event) + "\n").encode())
            launches.append((cmd, _Process(io.BytesIO())))
            return launches[-1][1]

        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(engines, "codex_sandbox", return_value=[]), \
             mock.patch.object(engines, "_git_dirs", return_value=[]):
            result = engines.start_l2(engine, "project/worker-1", "brief", cwd=self.repo, persona=self.tmp / "persona.md",
                                      model=None, settings=self.tmp / "settings.json", extra_env={"ALTITUDE_TASK": "worker"},
                                      job_root=self.tmp / "jobs")
        self.assertEqual(result["returncode"], 0, result)
        return launches[0][0], launches[0][1].stdin.getvalue()

    def assert_engine_read_only_the_prompt(self, output: bytes, errors: bytes) -> None:
        self.assertNotIn(self.TOKEN.encode(), output)
        self.assertTrue(output.endswith(b"brief"), output[-200:])
        self.assertIn(f"GH_TOKEN={self.TOKEN}".encode(), errors, "the reader exports the line it took")

    def test_linux_job_engine_reads_only_what_follows_the_token(self):
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                command, sent = self.launched(engine, "linux")
                self.assertTrue(sent.startswith(self.TOKEN.encode() + b"\n"))
                # systemd-run --pipe hands the job its launcher's own input pipe.
                job = subprocess.run(command[command.index("--") + 1:], input=sent, capture_output=True, timeout=60)
                self.assertEqual(job.returncode, 0, job.stderr)
                self.assert_engine_read_only_the_prompt(job.stdout, job.stderr)

    def test_macos_job_engine_reads_only_what_follows_the_token_and_keeps_no_copy(self):
        home = self.tmp / "home"
        home.mkdir()
        out, err = self.tmp / "out", self.tmp / "err"
        self.patch(platform.Path, "home", return_value=home)
        self.patch(platform, "_bsd", return_value=platform._BSDInfo(start_sec=1))
        self.patch(platform, "_coalition_of", return_value=44)
        self.patch(platform, "_stop_members", return_value=True)
        self.patch(platform, "_running", return_value=True)
        self.patch(platform, "CAFFEINATE", "/usr/bin/true")
        for engine in ("claude", "codex"):
            with self.subTest(engine=engine):
                command, sent = self.launched(engine, "darwin")
                spec = json.loads(command[-1])
                spec["writable"] = None  # Seatbelt replaces itself with the command; confinement has its own tests
                out.write_bytes(b""), err.write_bytes(b"")
                kept = []

                def launchd(argv, **kw):  # bootstrap runs the job's supervisor; afterwards launchd has forgotten it
                    if argv[1] != "bootstrap":
                        return subprocess.CompletedProcess(argv, platform.NOT_FOUND, "", "Could not find service")
                    job = Path(argv[-1]).parent
                    with mock.patch.object(platform.os, "execv", side_effect=SystemExit("removed")), \
                         self.assertRaisesRegex(SystemExit, "removed"):
                        platform._supervise(job)
                    kept.extend(path.name for path in job.iterdir())
                    return subprocess.CompletedProcess(argv, 0, "", "")

                streams = {0: None, 1: str(out), 2: str(err)}
                with mock.patch.object(platform, "_fd_path", side_effect=streams.get), \
                     mock.patch.object(platform.sys, "stdin", mock.Mock(buffer=io.BytesIO(sent))), \
                     mock.patch.object(platform.subprocess, "run", side_effect=launchd):
                    self.assertEqual(platform._launch(dict(spec)), 0, err.read_text())
                self.assert_engine_read_only_the_prompt(out.read_bytes(), err.read_bytes())
                self.assertIn("status", kept)
                self.assertNotIn("stdin", kept, "the launcher's copy of the token is removed once read")

    def test_the_reader_starts_no_engine_unless_its_input_is_a_pipe(self):
        command, sent = self.launched("claude", "linux")
        saved = self.tmp / "input"
        saved.write_bytes(sent)
        with saved.open("rb") as stream:
            job = subprocess.run(command[command.index("--") + 1:], stdin=stream, capture_output=True, timeout=60)
        self.assertEqual(job.returncode, 125)
        self.assertEqual(job.stdout, b"")
        self.assertNotIn(b"GH_TOKEN", job.stderr, "the engine never started")
        self.assertIn(b"input is not a pipe", job.stderr)
