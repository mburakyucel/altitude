"""I-20260903-075410/I-20260904-062512: L3 reads checkouts; altd owns privileged effects."""
import io
import json
import socket
import subprocess
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, l3, server, state as S, tasks as T


def _claude_result(session="claude-l3"):
    return {"text": "read it", "session_id": session, "usage": {}, "context_tokens": 1, "cost": 0,
            "turns": 1, "structured": None, "error": None, "limited": None, "tools": []}


class TestL3CheckoutConfinement(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)

    @staticmethod
    def choice(engine):
        return {"engine": engine, "why": f"test chose {engine}", "quota": {}}

    def checkout_snapshot(self):
        return (git("rev-parse", "HEAD", cwd=self.repo),
                git("status", "--porcelain", "--untracked-files=all", cwd=self.repo),
                (self.repo / "README.md").read_text())

    def assert_read_git_shim_denies_fetch(self, runtime: Path, before):
        read = subprocess.run([str(runtime / "bin" / "git"), "log", "-1", "--oneline"],
                              capture_output=True, text=True)
        denied = subprocess.run([str(runtime / "bin" / "git"), "fetch", "origin"],
                                capture_output=True, text=True)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("init", read.stdout)
        self.assertEqual(denied.returncode, 77)
        self.assertIn("read-only", denied.stderr)
        self.assertEqual(self.checkout_snapshot(), before, "the denied Git write leaves checkout and Git metadata untouched")

    def test_i_20260903_075410_claude_l3_denies_checkout_write_and_reads(self):
        before, seen = self.checkout_snapshot(), {}
        l3.save_info(self.project, {"sessions": {"claude": {"session_id": "old-auto-session"}},
                                    "engine_last": "claude"})

        def fake_claude(_prompt, **kwargs):
            seen.update(kwargs)
            runtime = Path(kwargs["cwd"])
            self.assertEqual(runtime.parent, config.project_dir(self.project))
            self.assertTrue(runtime.name.startswith("l3-claude-"))
            self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
            self.assert_read_git_shim_denies_fetch(runtime, before)
            return _claude_result()

        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude):
            l3.turn(self.project, "Read the checkout, then try git fetch.")

        runtime = Path(seen["cwd"])
        self.assertFalse(runtime.exists(), "the per-turn Claude runtime is disposed after the engine exits")
        self.assertIsNone(seen["resume"], "the checkout-cwd auto-permission session is rotated once")
        self.assertEqual((seen["permission_mode"], seen["permission_prompts"], seen["restricted"]),
                         ("dontAsk", "none", True))
        self.assertEqual(seen["tools"], "Read,Grep,Glob,Bash")
        self.assertEqual(seen["add_dirs"], (self.repo, config.ROOT),
                         "restricted Claude retains read access to the Altitude home")
        self.assertNotIn("Edit", seen["tools"]); self.assertNotIn("Write", seen["tools"])
        self.assertNotIn("git fetch", seen["allowed_tools"])
        for read in ("gh pr diff", "gh pr checks", "git show --stat", "journalctl --user -u altitude",
                     "systemctl --user status altitude"):
            self.assertIn(read, seen["allowed_tools"])
        self.assertEqual(l3.info(self.project)["sessions"]["claude"]["confinement_version"],
                         l3.L3_CONFINEMENT_VERSION)

    def test_i_20260903_075410_codex_l3_denies_checkout_write_and_reads(self):
        before, seen = self.checkout_snapshot(), {}

        def fake_codex(_prompt, **kwargs):
            seen.update(kwargs)
            runtime = Path(kwargs["cwd"])
            self.assertEqual(runtime.parent, config.project_dir(self.project))
            self.assertTrue(runtime.name.startswith("l3-codex-"))
            self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
            self.assert_read_git_shim_denies_fetch(runtime, before)
            return {"text": "read it", "session_id": "codex-l3", "reported_session_id": "codex-l3",
                    "usage": {"input_tokens": 1}, "error": None, "returncode": 0, "tools": []}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn(self.project, "Read the checkout, then try git fetch.")

        runtime = Path(seen["cwd"])
        settings = seen["sandbox_settings"]
        self.assertFalse(runtime.exists(), "the per-turn Codex runtime is disposed after the engine exits")
        self.assertTrue(seen["ignore_user_config"], "ambient sandbox settings cannot replace the L3 profile")
        self.assertIn('default_permissions="altitude-l3"', settings)
        self.assertFalse(any(value.startswith("sandbox_mode=") for value in settings))
        self.assertIn("features.network_proxy=true", settings)
        root_write = f'{json.dumps(str(config.ROOT.resolve()))}="write"'
        self.assertFalse(any(root_write in value for value in settings),
                         "Altitude state is writable only through the daemon verb socket")
        bus = f"/run/user/{l3.os.getuid()}/bus"
        self.assertTrue(any(bus in value and '="deny"' in value for value in settings),
                        "Codex cannot reconstruct the user bus and control the service")
        self.assertTrue(any(str(self.repo.resolve()) in value and '="read"' in value
                            for value in settings), "the deployment checkout is explicitly read-only")
        self.assertTrue(any(str(l3.verb_socket_path(self.project).resolve()) in value and '="allow"' in value
                            for value in settings), "only altd's role-fenced capability socket is exposed")
        self.assertFalse(any("network.domains" in value and '="allow"' in value for value in settings),
                         "direct GitHub and altd HTTP access stay blocked")

        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        service = {"unit": "altitude.service", "state": "active", "substate": "running", "pid": 123,
                   "last_restart": "today", "error": None}
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        def read(request):
            return service if request["kind"] == "service" else {
                "returncode": 0, "stdout": "checks are green\n", "stderr": ""}

        with mock.patch.object(server, "l3_verb_request", side_effect=lambda _project, request: read(request)):
            status = subprocess.run([str(runtime / "bin" / "systemctl"), "--user", "status",
                                     "altitude.service", "--no-pager"], capture_output=True, text=True)
            gh_read = subprocess.run([str(runtime / "bin" / "gh"), "pr", "checks", "7"],
                                     capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout.strip(), "altitude.service: active/running PID 123")
        self.assertEqual((gh_read.returncode, gh_read.stdout.strip()), (0, "checks are green"))
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", (runtime / "bin" / "systemctl").read_text())

    def test_i_20260903_075410_runtime_path_cannot_be_retargeted_to_the_checkout(self):
        before = self.checkout_snapshot()
        first = l3._l3_runtime(self.project, "codex")
        l3._remove_runtime(first)
        first.symlink_to(self.repo, target_is_directory=True)
        self.addCleanup(l3._remove_runtime, first)
        second = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, second)
        self.assertNotEqual(first, second)
        self.assertEqual(second.parent, config.project_dir(self.project))
        self.assertFalse(second.is_symlink())
        self.assertEqual(self.checkout_snapshot(), before,
                         "a symlink left by one turn cannot redirect the next turn's trusted setup")

    def test_i_20260903_075410_read_broker_rejects_github_writes(self):
        with mock.patch.object(server.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "documented gh read"):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["pr", "merge", "7"]})
        run.assert_not_called()

    def test_sept7_l3_sets_wip_through_broker_but_cannot_register_or_cross_projects(self):
        self.assertIn("Bash(alt project set *)", engines.L3_ALLOWED_TOOLS)
        self.assertNotIn("Bash(alt project add *)", engines.L3_ALLOWED_TOOLS)
        for options, expected in ((["--wip", "5"], 5), (["--unset-wip"], None)):
            result = server.l3_verb_request(self.project, {
                "kind": "alt", "args": ["project", "set", self.project, *options, "--reason", "test"]})
            self.assertEqual(result["returncode"], 0, result["stderr"])
            dispatch.run_project_wip(self.project)
            self.assertEqual(config.project(self.project).get("wip"), expected)
        events = [json.loads(line) for line in (config.project_dir(self.project) / "events.jsonl").read_text().splitlines()]
        self.assertEqual([(e["actor"], e["reason"]) for e in events], [("l3", "test")] * 2)
        for args in (["project", "add", "forbidden"], ["project", "remove", self.project],
                     ["project", "set", "other", "--wip", "5", "--reason", "test"]):
            result = server.l3_verb_request(self.project, {"kind": "alt", "args": args})
            self.assertNotEqual(result["returncode"], 0)
        self.assertNotIn("forbidden", config.load_projects())

    def test_i_20260903_075410_project_socket_ignores_a_forged_project(self):
        other = f"{self.project}-other"
        other_repo = self.tmp / "other-repo"; other_repo.mkdir()
        self.register(other, path=other_repo)
        local = T.new(self.project, "Shared", "local", actor="burak")
        private = T.new(other, "Shared", "private", actor="burak")
        private["title"] = "OTHER PROJECT PRIVATE TITLE"; S.save_task(other, private)
        self.assertNotEqual(l3.verb_socket_path(self.project), l3.verb_socket_path(other))

        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        def raw(request):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(l3.verb_socket_path(self.project)))
                client.sendall((json.dumps(request) + "\n").encode()); client.shutdown(socket.SHUT_WR)
                return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))

        response = raw({"kind": "alt", "project": other,
                        "args": ["task", "status", local["slug"], "--json"], "stdin": ""})
        self.assertEqual(response["returncode"], 0, response.get("stderr"))
        self.assertIn('"title": "Shared"', response["stdout"])
        self.assertNotIn("OTHER PROJECT PRIVATE TITLE", response["stdout"],
                         "attacker-controlled JSON cannot select a sibling project's daemon authority")
        abbreviated = raw({"kind": "alt",
                           "args": ["--pro", other, "task", "status", local["slug"], "--json"], "stdin": ""})
        self.assertIn("fixes the project", abbreviated["error"])
        self.assertNotIn("OTHER PROJECT PRIVATE TITLE", json.dumps(abbreviated),
                         "argparse abbreviations cannot override the project-bound socket")

    def test_i_20260903_075410_task_identifiers_cannot_traverse_projects(self):
        other = f"{self.project}-other"
        other_repo = self.tmp / "other-repo"; other_repo.mkdir()
        self.register(other, path=other_repo)
        victim = T.new(other, "Private victim", "private", actor="burak")
        traversal = f"../../{other}/tasks/{victim['slug']}"
        with self.assertRaisesRegex(ValueError, "invalid task slug"):
            S.load_task(self.project, traversal)
        with mock.patch.object(server.subprocess, "run") as run, \
             self.assertRaisesRegex(ValueError, "invalid task slug"):
            server.l3_verb_request(self.project, {
                "kind": "alt", "args": ["task", "message", traversal], "stdin": "overwrite"})
        run.assert_not_called()
        self.assertFalse((S.task_dir(other, victim["slug"]) / "inbox.jsonl").exists(),
                         "a traversal write is rejected before the daemon invokes alt")

    def test_i_20260903_075410_broker_rejects_daemon_file_reads(self):
        with mock.patch.object(server.subprocess, "run") as run:
            for option in ("--file", "--file=/etc/passwd", "--fil", "--fil=/etc/passwd",
                           "--fi", "--fi=/etc/passwd", "--f", "--f=/etc/passwd"):
                with self.subTest(option=option), self.assertRaisesRegex(ValueError, "only on stdin"):
                    args = ["task", "message", "safe-task", option]
                    if "=" not in option:
                        args.append("/etc/passwd")
                    server.l3_verb_request(self.project, {"kind": "alt", "args": args, "stdin": ""})
        run.assert_not_called()

        abbreviated = self.alt("--pro", self.project, "state", env={"ALTITUDE_ACTOR": "burak"})
        self.assertNotEqual(abbreviated.returncode, 0)
        self.assertIn("invalid choice", abbreviated.stderr,
                      "the ordinary CLI parser must not recreate broker-rejected option aliases")

    def test_i_20260903_075410_github_reads_cannot_select_another_repo(self):
        redirected = (
            ["pr", "view", "--repo", "other/private", "7"],
            ["pr", "view", "-Rother/private", "7"],
            ["pr", "view", "https://github.com/other/private/pull/7"],
            ["pr", "view", "other/private#7"],
        )
        with mock.patch.object(server.subprocess, "run") as run:
            for args in redirected:
                with self.subTest(args=args), self.assertRaisesRegex(ValueError, "documented gh read"):
                    server.l3_verb_request(self.project, {"kind": "gh", "args": args})
        run.assert_not_called()

        self.setenv("GH_REPO", "other/private")
        completed = subprocess.CompletedProcess([], 0, "green\n", "")
        with mock.patch.object(server.subprocess, "run", return_value=completed) as run:
            result = server.l3_verb_request(self.project, {"kind": "gh", "args": ["pr", "checks", "7"]})
        self.assertEqual(result["stdout"], "green\n")
        self.assertNotIn("GH_REPO", run.call_args.kwargs["env"])

    def test_i_20260903_075410_engine_adapters_receive_the_fail_closed_cli_flags(self):
        class ClaudeProcess:
            pid, returncode = 1, 0
            stdin = io.StringIO()
            stdout = io.StringIO(json.dumps({"type": "result", "result": "ok", "session_id": "sid",
                                               "is_error": False}) + "\n")
            stderr = io.StringIO()
            def wait(self): return self.returncode
            def kill(self): self.returncode = -9

        runtime = l3._l3_runtime(self.project, "claude")
        self.addCleanup(l3._remove_runtime, runtime)
        seen = {}
        def claude_popen(command, **kwargs):
            seen["claude"] = (command, kwargs)
            return ClaudeProcess()
        with mock.patch.object(engines, "usage_hold", return_value=None), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=claude_popen):
            engines.claude_print("prompt", cwd=runtime, permission_mode="dontAsk", permission_prompts="none",
                                 restricted=True, tools=l3.L3_TOOLS, allowed_tools=l3.ALLOWED_TOOLS,
                                 add_dirs=(self.repo, config.ROOT), settings=self.tmp / "settings.json")
        command = seen["claude"][0]
        self.assertIn("--restricted", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Bash")
        self.assertNotIn("Edit", command[command.index("--tools") + 1])

        settings = engines.codex_l3_permissions(runtime, project=self.project)
        codex_seen = {}
        codex_events = json.dumps({"type": "thread.started", "thread_id": "sid"}) + "\n"
        codex_process = mock.Mock(pid=2, returncode=0)
        codex_process.communicate.return_value = (codex_events, "")
        def codex_popen(command, **kwargs):
            codex_seen["command"] = command
            return codex_process
        with mock.patch.object(engines.subprocess, "Popen", side_effect=codex_popen):
            engines.codex_exec("prompt", cwd=runtime, sandbox_settings=settings, ignore_user_config=True)
        command = codex_seen["command"]
        self.assertIn("--ignore-user-config", command)
        for setting in settings:
            self.assertIn(setting, command)

    def test_i_20260903_075410_project_add_establishes_the_broker_before_l3_starts(self):
        name = f"{self.project}-added"
        repo = self.tmp / "added-repo"; repo.mkdir()
        self.addCleanup(self._forget, name)
        self.addCleanup(server.remove_l3_verb_broker, name)
        server.Handler._seen_clients.clear()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = server.threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)

        def started(_key, _fn, project):
            self.assertEqual(project, name)
            self.assertTrue(l3.verb_socket_path(name).is_socket(),
                            "a newly registered project gets its capability boundary before its first L3 turn")
            return True

        body = json.dumps({"name": name, "path": str(repo)}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/project/add", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with mock.patch.object(server, "spawn", side_effect=started):
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["ok"])

    def test_i_20260903_075410_cli_added_project_gets_a_broker_before_server_turn(self):
        name = f"{self.project}-cli-added"
        repo = self.tmp / "cli-added-repo"; repo.mkdir()
        self.addCleanup(self._forget, name)
        self.addCleanup(server.remove_l3_verb_broker, name)
        added = self.alt("project", "add", name, "--path", str(repo), env={"ALTITUDE_ACTOR": "burak"})
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertFalse(l3.verb_socket_path(name).exists())

        def turn(project, _prompt, **_kwargs):
            self.assertEqual(project, name)
            self.assertTrue(l3.verb_socket_path(name).is_socket(),
                            "every server-owned L3 turn reconciles a project added by another CLI process")
            return {"text": "ready"}

        with mock.patch.object(l3, "turn", side_effect=turn), \
             mock.patch.object(server, "request_l3_drain"):
            self.assertEqual(server.server_l3_turn(name, "hello")["text"], "ready")


class TestL3DaemonOperations(AltitudeCase):
    def task(self, state="running", title="Daemon operation"):
        task = T.new(self.project, title, "Do it.", actor="burak")
        task.update({"state": state, "attempt": 1, "agent_id": "agent-old", "session_id": "session-old",
                     "l2_engine": "claude"})
        S.save_task(self.project, task)
        return task

    def cli(self, *args, actor="l3"):
        return self.alt("--project", self.project, *args, env={"ALTITUDE_ACTOR": actor})

    def test_i_20260904_062512_l3_alt_crosses_the_daemon_socket(self):
        task = self.task(title="Socket stop")
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        result = subprocess.run([str(runtime / "bin" / "alt"), "task", "stop", task["slug"],
                                 "--reason", "Stop through altd"], input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)["request"]
        self.assertEqual((request["operation"], request["actor"], request["reason"]),
                         ("stop", "l3", "Stop through altd"))
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the L3 wrapper leaves the worker effect to the daemon operation runner")

    def test_i_20260904_062512_stop_requires_reason_records_once_and_runs_only_in_altd(self):
        task = self.task()
        missing = self.cli("task", "stop", task["slug"])
        self.assertNotEqual(missing.returncode, 0); self.assertIn("--reason", missing.stderr)

        first = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertEqual(first.returncode, 0, first.stderr)
        queued = json.loads(first.stdout)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the CLI process cannot stop the worker")
        again = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertTrue(json.loads(again.stdout)["idempotent"])
        events = [event for event in S.read_events(self.project, task["slug"])
                  if event["kind"] == "daemon-request"]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["task"], events[0]["operation"], events[0]["reason"], events[0]["by"]),
                         (task["slug"], "stop", "Abort the stale implementation", "l3"))
        self.assertEqual(events[0]["request_id"], queued["request"]["id"])

        with mock.patch.object(engines, "stop_l2_worker", return_value="stopped") as stop:
            completed = dispatch.run_task_operation(self.project, task["slug"])
            retried = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_called_once_with("claude", "agent-old", job_root=dispatch.l2_job_root(self.project, task["slug"]))
        self.assertEqual((completed["request"]["status"], completed["state"]), ("done", "blocked"))
        self.assertTrue(retried["idempotent"])
        self.assertEqual(S.load_task(self.project, task["slug"])["blocked_reason"],
                         "Abort the stale implementation")

        replacement = S.load_task(self.project, task["slug"])
        replacement.update({"state": "running", "agent_id": "agent-new", "session_id": "session-new"})
        S.save_task(self.project, replacement)
        old_retry = self.cli("task", "stop", task["slug"], "--reason", "Abort the stale implementation")
        self.assertFalse(json.loads(old_retry.stdout)["idempotent"],
                         "an intervening worker lifecycle gets a new identity-fenced daemon handoff")
        self.assertEqual(len([event for event in S.read_events(self.project, task["slug"])
                              if event["kind"] == "daemon-request"]), 2,
                         "the new daemon handoff records its own reason-bearing operation event")

    def test_i_20260904_062512_each_daemon_verb_requires_reason_and_records_one_request(self):
        for operation, state in (("resume", "blocked"), ("stop", "running"), ("reject", "running")):
            with self.subTest(operation=operation):
                task = self.task(state=state, title=f"{operation} contract")
                missing = self.cli("task", operation, task["slug"])
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("--reason", missing.stderr,
                              f"{operation} must be rejected before any daemon handoff without a reason")
                reason = f"Reason for {operation}"
                first = self.cli("task", operation, task["slug"], "--reason", reason)
                second = self.cli("task", operation, task["slug"], "--reason", reason)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertTrue(json.loads(second.stdout)["idempotent"],
                                f"the same {operation} handoff is idempotent")
                events = [event for event in S.read_events(self.project, task["slug"])
                          if event["kind"] == "daemon-request"]
                self.assertEqual(len(events), 1)
                self.assertEqual((events[0]["operation"], events[0]["reason"], events[0]["by"], events[0]["task"]),
                                 (operation, reason, "l3", task["slug"]))

    def test_i_20260904_062512_each_daemon_verb_refuses_a_changed_target(self):
        for operation, state in (("resume", "blocked"), ("stop", "running"), ("reject", "running")):
            with self.subTest(operation=operation):
                task = self.task(state=state, title=f"stale {operation}")
                dispatch.request_task_operation(self.project, task["slug"], operation,
                                                f"Refuse stale {operation}", actor="burak")
                changed = S.load_task(self.project, task["slug"])
                changed["state"] = "reported"
                S.save_task(self.project, changed)
                with mock.patch.object(engines, "stop_l2_worker") as stop, \
                     mock.patch.object(engines, "remove_l2_worker") as remove, \
                     mock.patch.object(engines, "resume_l2") as resume:
                    result = dispatch.run_task_operation(self.project, task["slug"])
                stop.assert_not_called(); remove.assert_not_called(); resume.assert_not_called()
                self.assertEqual(result["request"]["status"], "refused")
                retry = dispatch.request_task_operation(self.project, task["slug"], operation,
                                                        f"Refuse stale {operation}", actor="burak")
                self.assertTrue(retry["idempotent"],
                                f"retrying refused {operation} cannot retarget a replacement")
                self.assertFalse(retry["queued"])

    def test_stale_target_is_refused_without_touching_the_replacement_worker(self):
        task = self.task(title="Stale stop")
        self.assertEqual(self.cli("task", "stop", task["slug"], "--reason", "Stop old worker").returncode, 0)
        changed = S.load_task(self.project, task["slug"]); changed["agent_id"] = "agent-new"; S.save_task(self.project, changed)
        with mock.patch.object(engines, "stop_l2_worker") as stop:
            result = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_not_called()
        self.assertEqual((result["request"]["status"], result["request"]["note"]),
                         ("refused", "worker identity changed"))
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

        refused = self.cli("task", "resume", task["slug"], "--reason", "Wrong state")
        self.assertNotEqual(refused.returncode, 0); self.assertIn("cannot resume from running", refused.stderr)

    def test_i_20260904_062512_identity_is_rechecked_at_the_stop_transition(self):
        task = self.task(title="Stop transition race")
        dispatch.request_task_operation(self.project, task["slug"], "stop", "Stop the observed worker",
                                        actor="burak")
        real_stop = dispatch.stop

        def replace_after_daemon_precheck(project, slug, **kwargs):
            replacement = S.load_task(project, slug)
            replacement.update({"agent_id": "agent-new", "session_id": "session-new"})
            S.save_task(project, replacement)
            return real_stop(project, slug, **kwargs)

        with mock.patch.object(dispatch, "stop", side_effect=replace_after_daemon_precheck), \
             mock.patch.object(engines, "stop_l2_worker") as stop:
            result = dispatch.run_task_operation(self.project, task["slug"])
        stop.assert_not_called()
        self.assertEqual((result["request"]["status"], result["request"]["note"]),
                         ("refused", f"{task['slug']}: worker identity changed"))
        live = S.load_task(self.project, task["slug"])
        self.assertEqual((live["state"], live["agent_id"], live["session_id"]),
                         ("running", "agent-new", "session-new"),
                         "the daemon's under-lock check leaves a replacement worker untouched")

    def test_explicit_resume_and_blocked_message_leave_only_durable_daemon_work(self):
        task = self.task(state="blocked", title="Daemon resume")
        requested = self.cli("task", "resume", task["slug"], "--reason", "Fault is fixed")
        self.assertEqual(requested.returncode, 0, requested.stderr)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["state"], "blocked"); self.assertTrue(current["resume_after"])
        self.assertEqual(dispatch.resume_due(self.project), [], "the explicit request belongs to one daemon runner")

        def resume_in_daemon(project, slug, **_kwargs):
            live = S.load_task(project, slug); live["state"] = "running"; S.save_task(project, live)
            return {"agent": {"id": "agent-new"}}

        with mock.patch.object(dispatch, "resume", side_effect=resume_in_daemon) as resume:
            result = dispatch.run_task_operation(self.project, task["slug"])
        resume.assert_called_once_with(self.project, task["slug"],
                                       daemon_request_id=current["daemon_request"]["id"])
        self.assertEqual((result["request"]["status"], result["state"]), ("done", "running"))

        other = self.task(state="blocked", title="Message resume")
        message = self.cli("task", "message", other["slug"], "Use the recorded decision.", actor="burak")
        self.assertEqual(message.returncode, 0, message.stderr)
        live = S.load_task(self.project, other["slug"])
        self.assertEqual(live["state"], "blocked", "the Burak CLI no longer launches a worker")
        self.assertTrue(live["resume_after"]); self.assertNotIn("daemon_request", live)

    def test_i_20260904_062512_resume_receipt_survives_a_crash_after_transition(self):
        task = self.task(state="blocked", title="Resume receipt")
        queued = dispatch.request_task_operation(
            self.project, task["slug"], "resume", "Fault is fixed", actor="burak")
        live = S.load_task(self.project, task["slug"])
        live["daemon_request"]["status"] = "executing"
        live.update({"state": "running", "agent_id": "agent-replacement", "session_id": "session-replacement"})
        S.save_task(self.project, live)

        with mock.patch.object(dispatch, "resume") as resume:
            result = dispatch.run_task_operation(self.project, task["slug"])
        resume.assert_not_called()
        self.assertEqual((result["request"]["id"], result["request"]["status"], result["state"]),
                         (queued["request"]["id"], "done", "running"),
                         "an executing resume that reached its target gets a successful crash-recovery receipt")

    def test_i_20260904_062512_same_reason_after_an_intervening_lifecycle_is_new_work(self):
        task = self.task(title="Repeated stop")
        reason = "Pause for the same maintenance window"
        first = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        with mock.patch.object(engines, "stop_l2_worker", return_value="stopped"):
            finished = dispatch.run_task_operation(self.project, task["slug"])
        immediate = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        self.assertTrue(immediate["idempotent"])
        self.assertEqual(immediate["request"]["id"], first["request"]["id"])
        self.assertEqual((finished["request"]["result_state"], finished["request"]["result_agent_id"]),
                         ("blocked", "agent-old"))

        T.resume(self.project, task["slug"], agent_id="agent-new", session_id="session-new")
        second = dispatch.request_task_operation(self.project, task["slug"], "stop", reason, actor="burak")
        self.assertTrue(second["queued"]); self.assertFalse(second["idempotent"])
        self.assertNotEqual(second["request"]["id"], first["request"]["id"])
        self.assertEqual((second["request"]["agent_id"], second["request"]["session_id"]),
                         ("agent-new", "session-new"),
                         "an intervening resume gives the repeated reason a new identity-fenced request")
        self.assertEqual(len([event for event in S.read_events(self.project, task["slug"])
                              if event["kind"] == "daemon-request"]), 2)

    def test_l3_cannot_orphan_a_worker_or_release_buraks_merge_hold(self):
        task = self.task(title="Protected running task")
        blocked = self.cli("task", "block", task["slug"], "--reason", "Pause it")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not available to an L3", blocked.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

        held = S.load_task(self.project, task["slug"]); held["hold_merge"] = "security review"; S.save_task(self.project, held)
        released = self.cli("task", "hold-merge", task["slug"], "--off")
        self.assertNotEqual(released.returncode, 0)
        self.assertIn("only Burak", released.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["hold_merge"], "security review")

    def test_reject_worker_cleanup_runs_in_altd_and_the_l3_door_refuses_admin_commands(self):
        task = self.task(title="Daemon reject")
        requested = self.cli("task", "reject", task["slug"], "--reason", "No longer wanted")
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        with mock.patch.object(engines, "remove_l2_worker", return_value="removed") as remove:
            result = dispatch.run_task_operation(self.project, task["slug"])
            retry = dispatch.run_task_operation(self.project, task["slug"])
        remove.assert_called_once()
        self.assertEqual((result["request"]["status"], result["state"]), ("done", "rejected"))
        self.assertTrue(retry["idempotent"])

        denied = self.cli("dispatch", task["slug"])
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("not available to an L3", denied.stderr)

    def test_i_20260904_062512_altd_tick_schedules_the_durable_operation(self):
        task = self.task(title="Daemon tick")
        dispatch.request_task_operation(self.project, task["slug"], "stop", "Operator requested stop",
                                        actor="burak")
        with mock.patch.object(server.quota_codex, "refresh_if_due"), \
             mock.patch.object(server, "drain_hook_faults"), \
             mock.patch.object(dispatch, "poll", return_value=[]), \
             mock.patch.object(server, "resume_stranded_reports"), \
             mock.patch.object(dispatch, "resume_due", return_value=[]), \
             mock.patch.object(server, "dispatch_waiting"), \
             mock.patch.object(server, "auto_restart"), \
             mock.patch.object(server, "morning_digest"), \
             mock.patch.object(server, "spawn") as spawn:
            server.tick()
        spawn.assert_any_call(f"task-operation:{self.project}:{task['slug']}",
                              dispatch.run_task_operation, self.project, task["slug"])

    def test_i_20260904_062512_browser_controls_persist_before_the_daemon_runner(self):
        task = self.task(title="Browser stop")
        with mock.patch.object(server, "spawn", return_value=True) as spawn:
            result = server.request_daemon_task_operation(
                self.project, task["slug"], "stop", "Stopped from the task page", actor="burak")
        self.assertTrue(result["queued"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running",
                         "the HTTP request leaves the privileged worker effect to altd")
        spawn.assert_called_once_with(
            f"task-operation:{self.project}:{task['slug']}", dispatch.run_task_operation,
            self.project, task["slug"])

        blocked = self.task(state="blocked", title="Browser resume note")
        server.Handler._seen_clients.clear()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = server.threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        self.addCleanup(httpd.server_close); self.addCleanup(httpd.shutdown)

        def scheduled(*_args):
            self.assertEqual([row["text"] for row in T.pending(self.project, blocked["slug"])],
                             ["Use the approved value."],
                             "Burak's note exists before the daemon resume runner can start")
            return True

        body = json.dumps({"project": self.project, "slug": blocked["slug"], "option": 0,
                           "note": "Use the approved value."}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/decide", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with mock.patch.object(server, "spawn", side_effect=scheduled):
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["queued"])

    def test_coordinator_done_refuses_to_orphan_a_running_worker(self):
        task = self.task(title="Live completion")
        with self.assertRaisesRegex(T.TransitionError, "cannot complete a running worker"):
            T.done(self.project, task["slug"], actor="l3", digest="not actually done")
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")


if __name__ == "__main__":
    unittest.main()
