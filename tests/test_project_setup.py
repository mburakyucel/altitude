"""#349/#348: real setup results, scoped repair and preserved project/task continuity."""
import json
import shutil
import threading
import urllib.error
import urllib.request
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, engines, git_policy, l3, project_setup as setup, server, state as S, tasks as T


class ProjectSetup(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        installation = self.tmp / "installation"
        shutil.copytree(config.HOOKS, installation / "hooks")
        self.patch(config, "REPO", installation)
        self.addCleanup(server.stop_l3_verb_brokers)

    def perform(self, action="repair", **kwargs):
        setup.request(self.project, action, actor="operator", **kwargs)
        setup.run(self.project)
        return setup.observe(self.project)

    def step(self, name, view=None):
        return next(s for s in (view or setup.observe(self.project))["steps"] if s["id"] == name)

    def test_fresh_setup_records_real_pending_running_and_verified_installation(self):
        requested = setup.request(self.project, "repair", actor="operator")
        self.assertEqual(requested["operation"]["state"], "pending")
        repeated = setup.request(self.project, "repair", actor="operator")
        self.assertEqual(repeated["operation"]["id"], requested["operation"]["id"])
        real_repair = git_policy.repair_hooks
        seen = []
        def repair(*args, **kwargs):
            seen.append(setup.observe(self.project))
            return real_repair(*args, **kwargs)
        with mock.patch.object(git_policy, "repair_hooks", side_effect=repair):
            setup.run(self.project)
        self.assertEqual(seen[0]["operation"]["state"], "running")
        self.assertEqual(self.step("guards", seen[0])["status"], "running")
        self.assertTrue(l3.verb_socket_path(self.project).is_socket())
        final = setup.observe(self.project)
        self.assertEqual(final["status"], "ready")
        self.assertEqual(self.step("guards", final)["status"], "complete")
        self.assertIn("Installed", self.step("guards", final)["detail"])
        git_policy.require_hooks_installed(self.repo)
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertEqual(final["operation"]["state"], "complete")

    def test_existing_project_gains_missing_check_without_repeating_healthy_work(self):
        git_policy.install_hooks(self.repo)
        server.ensure_l3_verb_broker(self.project)
        l3.save_info(self.project, {"session_id": "saved", "turns": 4})
        l3.chat_log(self.project, "assistant", "Existing conversation")
        task = T.new(self.project, "Existing task", "Preserve this work", hold_merge="Operator review")
        before = S.load_task(self.project, task["slug"])
        history = l3.chat_history(self.project)
        self.assertEqual(setup.observe(self.project)["status"], "ready")
        self.assertEqual(self.step("guards")["status"], "reused")
        git("config", "--unset", "core.hooksPath", cwd=self.repo)
        self.assertEqual(self.step("guards")["status"], "input_needed")
        with mock.patch.object(server, "start_l3", side_effect=AssertionError("healthy L3 must not restart")):
            setup.maintain(self.project)
        self.assertEqual(setup.observe(self.project)["status"], "ready")
        self.assertEqual(l3.chat_history(self.project), history)
        self.assertEqual(l3.info(self.project)["session_id"], "saved")
        self.assertEqual(S.load_task(self.project, task["slug"]), before)

    def test_failure_keeps_coordinator_reachable_and_retry_reuses_completed_steps(self):
        with mock.patch.object(git_policy, "repair_hooks", side_effect=git_policy.GitPolicyError("Cannot write Git configuration")):
            failed = self.perform()
        self.assertEqual(failed["operation"]["state"], "failed")
        self.assertEqual(self.step("guards", failed)["status"], "failed")
        self.assertTrue(l3.verb_socket_path(self.project).is_socket())
        self.assertTrue(l3.queued(self.project))
        with mock.patch.object(git_policy, "repair_hooks") as repair:
            setup.maintain(self.project)
            repair.assert_not_called()
        self.assertEqual(setup.observe(self.project)["operation"], failed["operation"])
        final = self.perform()
        self.assertEqual(final["status"], "ready")
        self.assertNotIn("error", final["operation"])

    def test_interruption_after_write_rechecks_reality_without_repeating_install(self):
        real_repair = git_policy.repair_hooks
        def interrupted(*args, **kwargs):
            real_repair(*args, **kwargs)
            raise SystemExit("runner interrupted after mutation")
        setup.request(self.project, "repair", actor="operator")
        with mock.patch.object(git_policy, "repair_hooks", side_effect=interrupted), self.assertRaises(SystemExit):
            setup.run(self.project)
        observed = setup.observe(self.project)
        self.assertEqual(observed["operation"]["state"], "interrupted")
        self.assertEqual(self.step("guards", observed)["status"], "reused")
        config_bytes = (self.repo / ".git/config").read_bytes()
        self.assertEqual(self.perform("check")["operation"]["state"], "complete")
        self.assertEqual((self.repo / ".git/config").read_bytes(), config_bytes)

    def test_custom_hooks_require_current_operator_choice(self):
        custom = self.repo / ".git/hooks/pre-commit"
        custom.write_text("#!/bin/sh\nexit 0\n")
        custom.chmod(0o755)
        original = custom.read_bytes()
        view = self.perform()
        guards = self.step("guards", view)
        self.assertEqual(guards["action"], "combine")
        self.assertEqual(custom.read_bytes(), original)
        with self.assertRaises(PermissionError):
            setup.request(self.project, "combine", actor="l3", expected=guards["fingerprint"])
        with self.assertRaises(ValueError):
            setup.request(self.project, "combine", actor="operator")
        final = self.perform("combine", expected=guards["fingerprint"])
        self.assertEqual(final["status"], "ready")
        self.assertIn("Both hook sets", self.step("guards", final)["detail"])
        self.assertEqual(custom.read_bytes(), original)
        git_policy.require_hooks_installed(self.repo)

    def test_non_git_folder_is_conversation_ready_and_instructions_are_never_created(self):
        folder = self.tmp / "notes"
        folder.mkdir()
        self.register("notes", path=folder)
        setup.request("notes", "repair", actor="operator")
        setup.run("notes")
        view = setup.observe("notes")
        self.assertEqual(view["status"], "conversation_ready")
        self.assertEqual(next(s for s in view["steps"] if s["id"] == "guards")["status"], "not_applicable")
        self.assertEqual(list(folder.iterdir()), [])

    def test_five_blocked_tasks_share_one_project_fault_and_keep_their_records(self):
        custom = self.repo / ".git/hooks/pre-commit"
        custom.write_text("#!/bin/sh\nexit 0\n")
        custom.chmod(0o755)
        tasks = [T.new(self.project, f"Task {i}", "Keep task", hold_merge="Review") for i in range(5)]
        for task in tasks:
            with self.assertRaises(setup.SetupError) as failure:
                setup.ensure_guards(self.project)
            setup.block_task(self.project, task["slug"], failure.exception)
        incident_messages = [row for row in l3.queued(self.project) if row.get("trigger") == "incident"]
        self.assertEqual(len(incident_messages), 1)
        self.assertEqual(set(setup.observe(self.project)["affected_tasks"]), {t["slug"] for t in tasks})
        for task in tasks:
            current = S.load_task(self.project, task["slug"])
            self.assertEqual(current["state"], "blocked")
            self.assertEqual(current["hold_merge"], "Review")
            self.assertEqual(current["attempt"], task["attempt"])

    def test_saved_worktree_override_is_visible_and_repaired_without_changing_its_task(self):
        self.perform()
        task = T.new(self.project, "Saved work", "Preserve session and review", hold_merge="Review")
        worktree = self.repo / ".claude/worktrees" / task["slug"]
        git("worktree", "add", "-b", f"worktree-{task['slug']}", str(worktree), cwd=self.repo)
        git("config", "extensions.worktreeConfig", "true", cwd=self.repo)
        git("config", "--worktree", "core.hooksPath", str(config.REPO / "hooks"), cwd=worktree)
        S.save_task(self.project, {**task, "worktree": str(worktree), "session_id": "saved-session"})
        before = S.load_task(self.project, task["slug"])
        self.assertEqual(self.step(f"guards:{task['slug']}")["status"], "input_needed")
        setup.maintain(self.project)
        self.assertEqual(self.step(f"guards:{task['slug']}")["status"], "complete")
        git_policy.require_hooks_installed(worktree)
        self.assertEqual(S.load_task(self.project, task["slug"]), before)

    def test_scoped_coordinator_transport_repairs_without_worker_or_extra_authority(self):
        def spawn(_key, fn, *args):
            fn(*args)
            return True
        with mock.patch.object(server, "spawn", side_effect=spawn):
            result = server.l3_verb_request(self.project, {"kind": "alt", "args":
                ["project", "setup", self.project, "--repair", "--reason", "Repair missing guards"]})
        self.assertEqual(result["returncode"], 0)
        git_policy.require_hooks_installed(self.repo)
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": ["project", "setup", self.project]})
        self.assertEqual(json.loads(result["stdout"])["status"], "ready")
        for args in (["project", "setup", "another-project"], ["project", "setup", self.project, "--combine"],
                     ["project", "setup", self.project, "--repair"], ["project", "setup", self.project, "--path", "/tmp"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                server.l3_verb_request(self.project, {"kind": "alt", "args": args})
        with self.assertRaises(PermissionError):
            setup.request(self.project, "repair", actor="l2")

    def test_initial_turn_failure_is_not_hidden_by_partial_session_and_retry_is_observed(self):
        self.perform()
        l3.save_info(self.project, {"session_id": "partial", "turns": 1})
        setup.save(self.project, start_requested=True, intro={"state": "failed", "error": "Authentication rejected"})
        self.assertEqual(self.step("coordinator")["status"], "failed")
        setup.save(self.project, intro={"state": "complete"})
        self.assertEqual(self.step("coordinator")["status"], "complete")

    def test_failed_intro_waits_for_explicit_retry_even_when_guards_need_maintenance(self):
        self.perform()
        setup.save(self.project, start_requested=True, intro={"state": "failed", "error": "Authentication rejected"})
        git("config", "--unset", "core.hooksPath", cwd=self.repo)
        with mock.patch.object(server, "spawn", return_value=True) as spawn:
            for _ in range(3):
                setup.maintain(self.project)
            spawn.assert_not_called()
            self.assertEqual(self.step("coordinator")["status"], "failed")
            git_policy.require_hooks_installed(self.repo)
            self.perform()
            spawn.assert_called_once_with(f"start:{self.project}", server.start_l3, self.project)

    def test_actual_intro_failure_then_explicit_retry_uses_the_engine_seam(self):
        setup.save(self.project, start_requested=True)
        def spawn(_key, fn, *args):
            fn(*args)
            return True
        with mock.patch.object(server, "spawn", side_effect=spawn), \
             mock.patch.object(engines, "installation", side_effect=lambda engine: {"available": engine == "claude", "why": "fixture"}), \
             mock.patch.object(engines, "claude_print", side_effect=[
                 {"error": "Fixture authentication refused"},
                 {"text": "Ready", "session_id": "repaired-session"}]) as call:
            self.perform()
            self.assertEqual(self.step("coordinator")["status"], "failed")
            for _ in range(3):
                setup.maintain(self.project)
            self.assertEqual(call.call_count, 1)
            self.perform()
            self.assertEqual(call.call_count, 2)
            self.assertEqual(self.step("coordinator")["status"], "complete")
            self.assertEqual(l3.info(self.project)["session_id"], "repaired-session")

    def test_http_registration_keeps_failed_setup_visible_and_retry_completes_it(self):
        name = "new-folder"
        folder = self.tmp / name
        folder.mkdir()
        self.addCleanup(self._forget, name)
        server.Handler._seen_clients.clear()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        def post(route, payload):
            request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/{route}",
                                             data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        with mock.patch.object(server, "spawn", return_value=True):
            self.assertTrue(post("project/add", {"name": name, "path": str(folder)})["ok"])
        with mock.patch.object(server, "ensure_l3_verb_broker", side_effect=PermissionError("Command connection denied")):
            setup.run(name)
        self.assertTrue(config.is_managed(name))
        self.assertEqual(setup.observe(name)["operation"]["state"], "failed")
        self.assertIn("denied", next(s for s in setup.observe(name)["steps"] if s["id"] == "coordinator")["detail"])
        with mock.patch.object(server, "spawn", return_value=True):
            post("project/setup", {"project": name, "action": "repair"})
            setup.run(name)
        self.assertEqual(setup.observe(name)["operation"]["state"], "complete")
        self.assertTrue(l3.verb_socket_path(name).is_socket())
