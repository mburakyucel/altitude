"""One execution contract: Codex keeps its own sandbox, both engines use the `alt` door, a window switch is a fresh attempt."""
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase, add_worktree, make_repo
from altitude import config, dispatch, engines, platform, state as S, tasks as T

PROJECT = "door"


class _Stdin(io.BytesIO):
    def close(self):  # keep the prompt readable after codex_bg closes the pipe
        pass


class FakeProcess:
    """A Codex launch that announces its thread and stays alive until told otherwise."""

    def __init__(self, cmd, *, stdout, thread):
        self.cmd, self.pid, self.stdin, self.alive = cmd, 4242, _Stdin(), True
        stdout.write((json.dumps({"type": "thread.started", "thread_id": thread}) + "\n").encode())

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False

    def kill(self):
        self.alive = False


def launch_spec(proc) -> dict:
    """The one JSON line a job's driver reads: engine command, first input and launch settings. A coordinator turn
    writes it from a thread, so it may still be on its way."""
    deadline = time.monotonic() + 10
    while not proc.stdin.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    data = proc.stdin.getvalue()
    return json.loads(data.decode() if isinstance(data, bytes) else data)


class TestCodexAdapter(AltitudeCase):
    host = "linux"  # systemd fixtures
    github = True  # launches read the sign-in through the gh fixture

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.worktree = add_worktree(self.repo, "wt")
        self.job_root = self.tmp / "jobs"

    def test_sandbox_roots_are_the_worktree_both_git_dirs_and_altitude_home(self):
        # Codex leaves a linked worktree's own metadata read-only unless it is a root of its own, which blocked
        # `git fetch` for task give-the-chat-section-its-own-scrollbar on 2026-09-03.
        settings = engines.codex_sandbox(self.worktree, extra_roots=engines._git_dirs(self.worktree))
        parsed = tomllib.loads("\n".join(settings))
        self.assertEqual(parsed["default_permissions"], "altitude-task")
        profile = parsed["permissions"]["altitude-task"]
        roots = list(profile["workspace_roots"])
        self.assertEqual(roots, [str(self.worktree.resolve()), str((self.repo / ".git").resolve()),
                                 str((self.repo / ".git" / "worktrees" / "wt").resolve()),
                                 str(config.ROOT.resolve())])
        self.assertTrue(all(Path(r).is_dir() for r in roots), "Codex bind-mounts writable roots; they must exist")
        self.assertTrue(all(profile["workspace_roots"].values()))
        self.assertEqual(profile["extends"], ":workspace", "retain native protected paths and temporary roots")
        self.assertEqual(profile["filesystem"], {":root": "read",
                         **{str(path): "deny" for path in platform.job_control_paths()}})
        self.assertTrue(profile["network"]["enabled"])
        self.assertNotIn("sandbox_mode", parsed, "legacy selection must not override the named profile")
        self.assertNotIn("sandbox_workspace_write", parsed)
        self.assertIn('approval_policy="never"', settings)

    def test_repeated_workspace_roots_produce_a_valid_profile(self):
        settings = engines.codex_sandbox(config.ROOT, extra_roots=[config.ROOT])
        parsed = tomllib.loads("\n".join(settings))
        self.assertEqual(parsed["permissions"]["altitude-task"]["workspace_roots"],
                         {str(config.ROOT.resolve()): True})

    def test_control_socket_denials_cover_both_endpoints_without_two_file_masks(self):
        runtime = Path(f"/run/user/{os.getuid()}")
        endpoints = (runtime / "bus", runtime / "systemd/private")
        with mock.patch.object(config, "project_path", return_value=self.repo):
            profiles = [engines.codex_sandbox(self.worktree),
                        engines.codex_l3_permissions(self.worktree, project=PROJECT)]
        for settings in profiles:
            parsed = tomllib.loads("\n".join(settings))
            rules = parsed["permissions"][parsed["default_permissions"]]["filesystem"]
            denied = {Path(path) for path, access in rules.items() if access == "deny"}
            self.assertEqual(denied, {runtime / "bus", runtime / "systemd"})
            self.assertTrue(all(any(endpoint.is_relative_to(root) for root in denied) for endpoint in endpoints))
            self.assertFalse(any((runtime / "unrelated").is_relative_to(root) for root in denied))

    def _launch(self, *, thread="thr-1", actual_settings=False, **kw):
        procs, real_popen = [], subprocess.Popen

        def popen(cmd, **pkw):
            if cmd[0] == "gh":  # the launcher's own sign-in lookup
                return real_popen(cmd, **pkw)
            procs.append(FakeProcess(cmd, stdout=pkw["stdout"], thread=thread))
            return procs[-1]

        def job_command(unit, command, env, **kw):
            self.job_env = env
            return ["svc", unit, *command]

        settings = engines.codex_sandbox(self.worktree, extra_roots=[self.repo / ".git"]) if actual_settings else ["s1", "s2"]
        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(engines, "codex_sandbox", return_value=settings), \
             mock.patch.object(engines, "_git_dirs", return_value=[self.repo / ".git"]), \
             mock.patch.object(platform, "job_command", side_effect=job_command), \
             mock.patch.object(platform, "job_active", return_value=False):
            res = engines.codex_bg("door/t-1", "brief", cwd=self.worktree, job_root=self.job_root,
                                   extra_env={"ALTITUDE_TASK": "t", "ALTITUDE_ATTEMPT": "1"}, **kw)
        return res, procs

    def test_fresh_and_resumed_tasks_select_the_generated_profile(self):
        for resume in (None, "thr-1"):
            with self.subTest(resume=resume):
                _, procs = self._launch(actual_settings=True, resume=resume)
                command = launch_spec(procs[0])["command"]
                self.assertEqual(command[:3], [config.CODEX_BIN, "app-server", "--strict-config"])
                settings = [command[i + 1] for i, arg in enumerate(command) if arg == "-c"]
                parsed = tomllib.loads("\n".join(settings))
                self.assertEqual(parsed["default_permissions"], "altitude-task")
                self.assertNotIn("sandbox_mode", parsed)
                self.assertNotIn("--sandbox", command)
                self.assertNotIn("--ignore-user-config", command, "retain native user customization")

    def test_i_20261006_183126_a_worker_denied_the_session_bus_still_reaches_github_signed_in(self):
        # The GitHub CLI keeps its sign-in in the keyring on the session bus the worker profile denies, so the
        # launcher reads the token and hands it over in the job's launch input, never as a job setting.
        state = self.fake_gh()
        (state / "token.txt").write_text("fixture-token\n")
        self.setenv("GH_TOKEN", "ambient-fixture-token")
        signed = self.tmp / "gh-user"
        # The engine asks GitHub who it is before it serves the turn.
        engine = self.tmp / "codex-engine"
        engine.write_text(f'#!/bin/sh\ngh api user > {signed} 2>&1\n'
                          f'exec {sys.executable} {Path(__file__).resolve().parent / "fake_engine.py"} "$@"\n')
        engine.chmod(0o755)
        self.patch(config, "CODEX_BIN", str(engine))
        _, procs = self._launch(actual_settings=True)
        command, sent = procs[0].cmd, procs[0].stdin.getvalue()
        spec = json.loads(sent)
        self.assertEqual(self.gh_log(), [["auth", "token"]])
        self.assertFalse(any("fixture-token" in arg for arg in command), "job settings appear on its command line")
        self.assertFalse(any("fixture-token" in arg for arg in spec["command"]))
        self.assertFalse({"GH_TOKEN", "GITHUB_TOKEN", "DBUS_SESSION_BUS_ADDRESS"} & set(self.job_env))
        self.assertEqual(spec["github_token"], "fixture-token")
        settings = [spec["command"][i + 1] for i, arg in enumerate(spec["command"]) if arg == "-c"]
        rules = tomllib.loads("\n".join(settings))["permissions"]["altitude-task"]["filesystem"]
        self.assertEqual({path for path, access in rules.items() if access == "deny"},
                         {str(path) for path in platform.job_control_paths()})
        # In the job's environment, without the bus, GitHub accepts the engine only after the driver exports the
        # token; an ambient token never reaches it, and the engine reads its turn, not the token.
        driver = command[2:]
        self.assertEqual(driver, engines._driver_command())
        log = self.tmp / "engine.log"
        env = {**self.job_env, "PATH": os.environ["PATH"], "FAKE_GH_DIR": str(state), "GH_TOKEN": "ambient-fixture-token",
               "FAKE_ENGINE_LOG": str(log)}
        job = subprocess.run(driver, input=sent, capture_output=True, env=env, timeout=60)
        self.assertEqual(job.returncode, 0, job.stderr)
        self.assertEqual(signed.read_text().splitlines()[0], '{"login": "fixture-operator"}')
        environment, *read = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(environment["environment"], {"GH_TOKEN": "fixture-token"})
        self.assertNotIn("fixture-token", json.dumps(read))
        turn = next(row for row in read if row.get("method") == "turn/start")
        self.assertTrue(turn["params"]["input"][0]["text"].endswith("\n\nbrief"))
        log.unlink()
        unsigned = subprocess.run(driver, input=json.dumps({**spec, "github_token": ""}).encode() + b"\n",
                                  capture_output=True, env=env, timeout=60)
        self.assertEqual(unsigned.returncode, 0, unsigned.stderr)
        self.assertIn("HTTP 401: Requires authentication", signed.read_text())
        self.assertEqual(json.loads(log.read_text().splitlines()[0])["environment"], {"GH_TOKEN": None})
        # A launcher without a sign-in still starts the worker, without a token.
        (state / "token.txt").unlink()
        _, procs = self._launch(actual_settings=True)
        self.assertEqual(launch_spec(procs[0])["github_token"], "")

    def test_fresh_turn_runs_codex_app_server_in_the_worktree_with_the_persona_in_front(self):
        persona = self.tmp / "l2.md"
        persona.write_text("PERSONA")
        res, procs = self._launch(persona=persona, model="gpt-x")
        self.assertEqual(res["returncode"], 0)
        row = res["agent"]
        self.assertEqual(procs[0].cmd, ["svc", engines._codex_unit(row["id"]), *engines._driver_command()])
        spec = launch_spec(procs[0])
        self.assertEqual(spec["command"], [config.CODEX_BIN, "app-server", "--strict-config", "-c", "s1", "-c", "s2"])
        self.assertEqual((spec["engine"], spec["cwd"], spec["model"], spec["resume"], spec["github_token"]),
                         ("codex", str(self.worktree), "gpt-x", None, ""))
        self.assertEqual(spec["sends"], str(engines.worker_sends(row["id"], job_root=self.job_root)))
        self.assertEqual([part["type"] for part in spec["input"]], ["text"])
        sent = spec["input"][0]["text"]
        self.assertTrue(sent.startswith("PERSONA\n\n"))
        self.assertIn(engines.CODEX_PATCH_NOTE, sent)
        self.assertTrue(sent.endswith("\n\nbrief"))
        self.assertEqual((row["sessionId"], row["state"], row["status"], row["engine"], row["pid"], row["name"]),
                         ("thr-1", "working", "busy", "codex", 4242, "door/t-1"))
        self.assertEqual(json.loads((self.job_root / f"{row['id']}.json").read_text())["session_id"], "thr-1")

    def test_resume_continues_the_same_thread_and_refuses_another(self):
        res, procs = self._launch(resume="thr-1")
        spec = launch_spec(procs[0])
        self.assertEqual(spec["command"][:2], [config.CODEX_BIN, "app-server"])
        self.assertNotIn("resume", spec["command"])
        self.assertNotIn("-C", spec["command"])
        self.assertEqual(spec["resume"], "thr-1")
        prompt = spec["input"][0]["text"]
        self.assertTrue(prompt.endswith("\n\nbrief"))
        self.assertNotIn(engines.CODEX_PATCH_NOTE, prompt, "resume retains the initial thread instructions")
        self.assertIn(str(config.PERSONAS / "l1.md"), prompt)
        self.assertEqual((res["returncode"], res["agent"]["sessionId"]), (0, "thr-1"))

        with mock.patch.object(engines, "codex_stop", return_value="stopped") as stop:
            res, _ = self._launch(resume="thr-1", thread="thr-2")
        self.assertEqual(res["returncode"], 1)
        self.assertIn("different Codex thread", res["stderr"])
        stop.assert_called_once()

    def test_container_launch_and_resume_replace_host_patch_instruction(self):
        for resume in (None, 'thr-1'):
            with self.subTest(resume=resume), mock.patch.object(platform, 'containerized', return_value=True), \
                 mock.patch.object(platform, '_lifecycle_state', return_value={'reason': None}):
                _, procs = self._launch(resume=resume)
            sent = launch_spec(procs[0])["input"][0]["text"]
            self.assertIn(engines.CODEX_CONTAINER_PATCH_NOTE, sent)
            self.assertNotIn(engines.CODEX_PATCH_NOTE, sent)
            self.assertIn('python3', sent)
            self.assertTrue(sent.endswith('\n\nbrief'))

    def test_worker_row_states_follow_the_unit_and_the_events(self):
        wid = "w-states"
        paths = engines._codex_paths(self.job_root, wid)
        S.write_json(paths["record"], {"id": wid, "name": "n", "pid": 1, "unit": "u.service", "started_at": "t",
                                       "session_id": None, "stopped": None})
        paths["stdout"].write_text('{"type":"thread.started","thread_id":"thr-9"}\n')
        paths["stderr"].write_text("boom\n")
        with mock.patch.object(platform, "job_active", return_value=True):
            self.assertEqual(engines.codex_worker(wid, job_root=self.job_root)["state"], "working")
        with mock.patch.object(platform, "job_active", return_value=False):
            row = engines.codex_worker(wid, job_root=self.job_root)
            self.assertEqual((row["state"], row["status"], row["detail"], row["sessionId"]),
                             ("failed", "exited", "boom\n", "thr-9"))
            with paths["stdout"].open("a") as f:
                f.write('{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}\n')
            row = engines.codex_worker(wid, job_root=self.job_root)
            self.assertEqual((row["state"], row["usage"]["input_tokens"]), ("done", 5))
        self.assertIsNone(engines.codex_worker("missing", job_root=self.job_root))
        self.assertIsNone(engines.codex_worker(None, job_root=self.job_root))

    def test_stop_ends_the_unit_and_stamps_the_record(self):
        wid = "w-stop"
        paths = engines._codex_paths(self.job_root, wid)
        S.write_json(paths["record"], {"id": wid, "engine": "codex", "name": "n", "pid": 1, "unit": "altitude-codex-w-stop.service",
                                       "started_at": "t", "session_id": "thr-3", "stopped": None})
        paths["stdout"].write_text("")
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(engines.subprocess, "run", return_value=done) as run, \
             mock.patch.object(platform, "job_active", return_value=False):
            self.assertEqual(engines.codex_stop(wid, job_root=self.job_root), "Codex worker stopped")
            self.assertEqual(engines.codex_worker(wid, job_root=self.job_root)["state"], "stopped")
        self.assertEqual(run.call_args.args[0], [platform.SYSTEMCTL, "--user", "stop", "altitude-codex-w-stop.service"])
        with mock.patch.object(engines.subprocess, "run", return_value=done), \
             mock.patch.object(platform, "job_active", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                engines.codex_stop(wid, job_root=self.job_root)

    def test_a_timed_out_turn_stops_its_unit_with_the_inherited_environment(self):
        proc = mock.Mock(pid=4242)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("codex", 0), ("", "")]
        with mock.patch.object(engines.subprocess, "Popen", return_value=proc), \
             mock.patch.object(platform, "job_stop") as stop, self.assertRaises(subprocess.TimeoutExpired):
            engines.codex_exec("Prompt", cwd=self.worktree, timeout=0)
        stop.assert_called_once()
        self.assertEqual(stop.call_args.kwargs, {})
        self.assertEqual(len(stop.call_args.args), 1)
        self.assertTrue(stop.call_args.args[0].startswith(engines.CODEX_UNIT_PREFIX))
        proc.kill.assert_called_once()

    def test_service_launcher_is_synchronous_and_scrubs_the_user_bus_before_codex_starts(self):
        child_env = {"PATH": os.environ["PATH"], "ALTITUDE_PROJECT": "altitude", "ALTITUDE_TASK": "task"}
        script = ('import json, os; print(json.dumps({'
                  '"project": os.environ.get("ALTITUDE_PROJECT"), "task": os.environ.get("ALTITUDE_TASK"), '
                  '"secret": os.environ.get("MANAGER_FAKE_SECRET"), "bus": os.environ.get("DBUS_SESSION_BUS_ADDRESS")}))')
        command = platform.job_command("altitude-codex-test.service", [sys.executable, "-c", script], child_env)
        for flag in ("--wait", "--pipe", "--property=NoNewPrivileges=no", "--property=KillMode=control-group"):
            self.assertIn(flag, command)
        child = command[command.index("--") + 1:]
        self.assertEqual(child[:2], [platform.ENV_BIN, "-i"])
        result = subprocess.run(child, capture_output=True, text=True, check=True,
                                env={"PATH": os.environ["PATH"], "MANAGER_FAKE_SECRET": "must-not-cross",
                                     "DBUS_SESSION_BUS_ADDRESS": "unix:path=/manager/bus"})
        self.assertEqual(json.loads(result.stdout), {"project": "altitude", "task": "task", "secret": None, "bus": None})

    def test_coordinator_turn_sends_the_whole_prompt_and_reads_the_last_message(self):
        # The job starts reading a second late, and the prompt exceeds every pipe buffer: a coordinator turn on a
        # loaded Mac once waited its whole limit for the rest of its prompt (#617).
        engine = self.tmp / "codex"
        engine.write_text(f"#!{sys.executable}\n" + (
            "import hashlib, json, sys\n"
            "received = {'argv': sys.argv[1:]}\n"
            "def out(message):\n"
            "    print(json.dumps(message), flush=True)\n"
            "def note(text):\n"
            "    out({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'id': text[:8], 'text': text}}})\n"
            "for raw in sys.stdin:\n"
            "    message = json.loads(raw)\n"
            "    method, params = message.get('method'), message.get('params') or {}\n"
            "    if method == 'initialize':\n"
            "        out({'id': message['id'], 'result': {}})\n"
            "    elif method in ('thread/start', 'thread/resume'):\n"
            "        received['open'] = {'method': method, **params}\n"
            "        out({'id': message['id'], 'result': {'thread': {'id': params.get('threadId') or 'new'}}})\n"
            "    elif method == 'turn/start':\n"
            "        received['sha256'] = hashlib.sha256(params['input'][0]['text'].encode()).hexdigest()\n"
            "        out({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})\n"
            "        out({'method': 'turn/started', 'params': {'turn': {'id': 'turn-1'}}})\n"
            "        note('first')\n"
            "        note(json.dumps(received))\n"
            "        out({'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': {'inputTokens': 7}}}})\n"
            "        out({'method': 'turn/completed', 'params': {'turn': {'id': 'turn-1', 'status': 'completed'}}})\n"))
        engine.chmod(0o755)
        prompt = "Keep the public result stable. ✓\n" * 8192
        self.patch(config, "CODEX_BIN", str(engine))
        self.patch(platform, "job_command",
                   side_effect=lambda unit, command, env, **kw: ["/bin/sh", "-c", 'sleep 1; exec "$@"', "job", *command])
        out = engines.codex_turn(prompt, cwd=self.worktree, effort="high", resume="thr-l3", model="gpt-x",
                                 extra_env={"ALTITUDE_ACTOR": "l3"}, timeout=30)
        received = json.loads(out["text"])
        self.assertEqual(received["argv"][:2], ["app-server", "--strict-config"])
        self.assertIn('model_reasoning_effort="high"', received["argv"])
        self.assertEqual(received["open"], {"method": "thread/resume", "threadId": "thr-l3", "model": "gpt-x",
                                            "approvalPolicy": "never"})
        self.assertEqual(received["sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual((out["reported_session_id"], out["usage"], out["error"]), ("thr-l3", {"input_tokens": 7}, None))

    def _engine_that_never_reads(self, script: str) -> str:
        engine = self.tmp / "codex"
        engine.write_text(f"#!{sys.executable}\n" + script)
        engine.chmod(0o755)
        self.patch(config, "CODEX_BIN", str(engine))
        self.patch(platform, "job_command", side_effect=lambda unit, command, env, **kw: command)
        return "Keep the public result stable. ✓\n" * 8192

    def assert_prompt_writer_ends(self):
        deadline = time.monotonic() + 10
        while any(thread.name.endswith("(_feed)") for thread in threading.enumerate()) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([thread.name for thread in threading.enumerate() if thread.name.endswith("(_feed)")], [])

    def test_an_engine_that_exits_without_reading_its_prompt_reports_its_failure(self):
        prompt = self._engine_that_never_reads("import sys\nprint('codex: not signed in', file=sys.stderr)\nsys.exit(3)\n")
        out = engines.codex_exec(prompt, cwd=self.worktree, timeout=30)
        self.assertEqual((out["returncode"], out["error"]), (3, "codex: not signed in"))
        self.assert_prompt_writer_ends()

    def test_a_turn_whose_engine_never_reads_its_prompt_times_out_and_releases_the_writer(self):
        prompt = self._engine_that_never_reads("import time\ntime.sleep(60)\n")
        self.patch(platform, "job_stop")
        with self.assertRaises(subprocess.TimeoutExpired):
            engines.codex_exec(prompt, cwd=self.worktree, timeout=1)
        self.assert_prompt_writer_ends()

    def test_window_hold_belongs_to_claude_only(self):
        with mock.patch.object(engines, "usage_hold", return_value="2030-01-01T00:00:00+00:00"):
            self.assertEqual(engines.window_hold("claude"), "2030-01-01T00:00:00+00:00")
            self.assertIsNone(engines.window_hold("codex"))


class TestDoor(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(PROJECT, path=self.repo)

    def _alt(self, *args, env=None):
        return self.alt("--project", PROJECT, *args, env={"ALTITUDE_ACTOR": "l2", **(env or {})})

    def test_l2_has_only_the_worker_commands(self):
        for args in (("task", "new", "--title", "x", "y"), ("dispatch", "s"),
                     ("task", "resume", "s", "--reason", "L3 daemon handoff only"),
                     ("task", "stop", "s", "--reason", "L3 daemon handoff only"), ("chat", "hi")):
            with self.subTest(args=args):
                out = self._alt(*args)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn("not available to an L2 worker", out.stderr,
                              "the required reason is present, so the role door decides before any daemon handoff")
        out = self._alt("task", "list")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_l2_block_is_fenced_to_its_own_task_and_current_attempt(self):
        task = T.new(PROJECT, "Door block fixture", "Block me.", actor="burak")
        task.update({"state": "running", "attempt": 2})
        S.save_task(PROJECT, task)
        slug = task["slug"]
        out = self._alt("task", "block", slug, "--reason", "which key?")
        self.assertIn("ALTITUDE_TASK", out.stderr)
        stale = {"ALTITUDE_TASK": slug, "ALTITUDE_ATTEMPT": "1"}
        out = self._alt("task", "block", slug, "--reason", "which key?", env=stale)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("attempt 1 is no longer current", out.stderr)
        self.assertEqual(S.load_task(PROJECT, slug)["state"], "running")
        out = self._alt("task", "block", slug, "--reason", "which key?", env={**stale, "ALTITUDE_ATTEMPT": "2"})
        self.assertEqual(out.returncode, 0, out.stderr)
        blocked = S.load_task(PROJECT, slug)
        self.assertEqual((blocked["state"], blocked["blocked_reason"]), ("blocked", "which key?"))
        self.assertEqual(S.read_events(PROJECT, slug)[-1]["by"], "l2")


class TestFreshAttempt(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(PROJECT, path=self.repo)

    def test_requeue_with_a_worker_needs_clear_worker_and_pins_the_next_engine(self):
        task = T.new(PROJECT, "Requeue fixture", "Do it.", actor="burak")
        task.update({"state": "blocked", "agent_id": "a", "session_id": "s", "l2_engine": "codex",
                     "engine_model": "m", "routing": "why", "resume_after": "x"})
        S.save_task(PROJECT, task)
        with self.assertRaisesRegex(T.TransitionError, "resume it instead"):
            T.requeue(PROJECT, task["slug"])
        out = T.requeue(PROJECT, task["slug"], engine="claude", clear_worker=True)
        self.assertEqual((out["state"], out["l2_engine"], out["agent_id"], out["session_id"], out["engine_model"],
                          out["routing"]), ("queued", "claude", None, None, None, None))
        self.assertNotIn("resume_after", out)

    def test_brief_carries_the_previous_attempts_progress(self):
        task = T.new(PROJECT, "Progress fixture", "Do it.", actor="burak")
        self.assertNotIn("stopped before finishing", dispatch.build_brief(PROJECT, task["slug"]))
        task["attempt"] = 1
        S.save_task(PROJECT, task)
        (S.task_dir(PROJECT, task["slug"]) / "progress.md").write_text("# Progress\n\n- done: half of it\n")
        brief = dispatch.build_brief(PROJECT, task["slug"])
        self.assertIn("Attempt 1 stopped before finishing", brief)
        self.assertIn("- done: half of it", brief)

    def test_fresh_dispatch_does_not_turn_a_previous_selection_into_a_pin(self):
        task = T.new(PROJECT, "Switch fixture", "Do it.", actor="burak")
        task["l2_engine"] = "claude"
        S.save_task(PROJECT, task)
        fake = {"stdout": "", "stderr": "", "returncode": 0, "agent": {"id": "agent-1", "sessionId": "session-1"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_origin", return_value="a" * 40), \
             mock.patch.object(dispatch, "_task_worktree", return_value=config.ROOT), \
             mock.patch.object(dispatch.route, "pick_engine",
                               side_effect=lambda role, forced=None, **kwargs: {"engine": forced or "codex", "why": "t"}) as pick, \
             mock.patch.object(dispatch.engines, "start_l2", return_value=fake) as launch:
            dispatch.run(PROJECT, task["slug"])
        self.assertIsNone(pick.call_args.kwargs["forced"])
        self.assertEqual(launch.call_args.args[0], "codex")
        self.assertEqual(launch.call_args.kwargs["persona"], config.PERSONAS / "l2.md")
        self.assertEqual(S.load_task(PROJECT, task["slug"])["attempt"], 1)


if __name__ == "__main__":
    unittest.main()
