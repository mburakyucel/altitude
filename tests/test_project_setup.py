"""#349/#348: real setup results, scoped repair and preserved project/task continuity."""
import json
import shutil
import threading
import urllib.error
import urllib.request
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, dispatch, engines, git_policy, l3, project_setup as setup, server, state as S, tasks as T


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

    def blocked_owner(self):
        task = T.new(self.project, "Continue saved work", "Preserve the owner", hold_merge="Review required")
        worktree = add_worktree(self.repo, task["slug"])
        task.update(state="running", attempt=1, agent_id="old-worker", session_id="saved-session",
                    worktree=str(worktree))
        S.save_task(self.project, task)
        T.block(self.project, task["slug"], "Review this approach", actor="l2", updates={"waiting_on": "burak"})
        self.patch(engines, "worker_live", return_value=False)
        return S.load_task(self.project, task["slug"]), worktree

    def test_contended_daemon_resume_keeps_request_messages_and_one_saved_owner(self):
        task, worktree = self.blocked_owner()
        slug = task["slug"]
        (worktree / "draft.txt").write_text("Keep unfinished work\n")
        first = T.message(self.project, slug, "burak", "Continue with this approach", by="burak")
        request = dispatch.request_task_operation(self.project, slug, "resume", "Continue authorized work", actor="l3")
        before = S.load_task(self.project, slug)
        with setup.operation_lock(self.project) as acquired:
            self.assertTrue(acquired)
            for _ in range(2):
                held = dispatch.run_task_operation(self.project, slug)
                self.assertTrue(held["pending"])
                self.assertIn("setup", held["held"])
                current = S.load_task(self.project, slug)
                for key in ("agent_id", "session_id", "attempt", "questions", "hold_merge", "block_id", "resume_request"):
                    self.assertEqual(current.get(key), before.get(key), key)
                self.assertEqual(current["daemon_request"]["id"], request["request"]["id"])
                self.assertEqual(current["daemon_request"]["status"], "executing")
                self.assertFalse(current.get("resume_claim"))
                self.assertFalse(current.get("dispatching"))
                self.assertFalse(current.get("fault"))
                self.assertFalse(current.get("resume_failed"))
                self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [first["id"]])
            self.assertEqual(dispatch.pending_task_operations(self.project), [slug])
            self.assertEqual(dispatch.resume_due(self.project), [])
            second = T.message(self.project, slug, "burak", "Also keep my draft", by="burak")
        self.assertFalse(config.INCIDENT_INDEX.exists())
        self.assertEqual(len([e for e in S.read_events(self.project, slug) if e["kind"] == "resume-held"]), 1)
        self.assertEqual(self.perform()["status"], "ready")

        def launch(_engine, _name, session_id, prompt, **_kwargs):
            self.assertEqual(session_id, "saved-session")
            self.assertLess(prompt.index(first["text"]), prompt.index(second["text"]))
            self.assertTrue(dispatch.run_task_operation(self.project, slug)["already_resuming"])
            return {"returncode": 0, "agent": {"id": "replacement", "sessionId": session_id, "input_delivered": True}}

        with mock.patch.object(engines, "resume_l2", side_effect=launch) as resume:
            result = dispatch.run_task_operation(self.project, slug)
            self.assertEqual(result["request"]["status"], "done")
            self.assertTrue(dispatch.run_task_operation(self.project, slug)["idempotent"])
            resume.assert_called_once()
        current = S.load_task(self.project, slug)
        self.assertEqual((current["state"], current["session_id"], current["attempt"]), ("running", "saved-session", 1))
        self.assertEqual(current["questions"], before["questions"])
        self.assertEqual(current["hold_merge"], "Review required")
        self.assertFalse(current.get("resume_claim"))
        self.assertEqual(T.pending(self.project, slug), [])
        self.assertEqual((worktree / "draft.txt").read_text(), "Keep unfinished work\n")
        self.assertFalse(config.INCIDENT_INDEX.exists())

    def test_contended_message_wake_restores_racing_message_then_obeys_stop(self):
        task, _ = self.blocked_owner()
        slug = task["slug"]
        first = T.message(self.project, slug, "burak", "Resume this work", by="burak")
        real_ensure = setup.ensure_guards
        arrived = []
        def preflight(*args, **kwargs):
            arrived.append(T.message(self.project, slug, "burak", "Later steering", by="burak"))
            return real_ensure(*args, **kwargs)
        with setup.operation_lock(self.project), mock.patch.object(setup, "ensure_guards", side_effect=preflight):
            self.assertTrue(dispatch.resume(self.project, slug)["held"])
        self.assertEqual(dispatch.resume_due(self.project), [slug])
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [first["id"], arrived[0]["id"]])
        self.assertEqual(S.load_task(self.project, slug)["resume_request"], arrived[0]["id"])
        with mock.patch.object(engines, "stop_l2_worker", return_value="Stopped"):
            dispatch.stop(self.project, slug)
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertEqual(dispatch.resume(self.project, slug), {"waiting": True})
        self.assertEqual(len(T.pending(self.project, slug)), 2)
        self.assertEqual(S.load_task(self.project, slug)["hold_merge"], "Review required")

    def test_contended_message_wake_continues_through_daemon_scheduler(self):
        task, _ = self.blocked_owner()
        slug = task["slug"]
        T.message(self.project, slug, "burak", "Continue saved session", by="burak")
        def spawn(_key, fn, *args):
            fn(*args)
            return True
        with mock.patch.object(server, "spawn", side_effect=spawn), mock.patch.object(
                engines, "resume_l2", return_value={"returncode": 0, "agent": {
                    "id": "replacement", "sessionId": "saved-session", "input_delivered": True}}) as launch:
            with setup.operation_lock(self.project):
                self.assertFalse(server.request_task_resume(self.project, slug))
                launch.assert_not_called()
            self.assertTrue(server.request_task_resume(self.project, slug))
            self.assertFalse(server.request_task_resume(self.project, slug))
            launch.assert_called_once()
        self.assertEqual(S.load_task(self.project, slug)["state"], "running")
        self.assertEqual(T.pending(self.project, slug), [])

    def test_new_question_during_contention_supersedes_resume_without_fault(self):
        task, _ = self.blocked_owner()
        slug = task["slug"]
        message = T.message(self.project, slug, "burak", "Continue", by="burak")
        real_ensure = setup.ensure_guards
        def preflight(*args, **kwargs):
            T.escalate(self.project, slug, "A newer decision is needed")
            return real_ensure(*args, **kwargs)
        with setup.operation_lock(self.project), mock.patch.object(setup, "ensure_guards", side_effect=preflight):
            with self.assertRaises(dispatch.ResumeFailure):
                dispatch.resume(self.project, slug)
        current = S.load_task(self.project, slug)
        self.assertEqual(current["blocked_reason"], "A newer decision is needed")
        self.assertNotEqual(current["block_id"], task["block_id"])
        self.assertFalse(current.get("resume_claim"))
        self.assertFalse(current.get("fault"))
        self.assertFalse(current.get("resume_after"))
        self.assertEqual(dispatch.resume_due(self.project), [])
        pending = T.pending(self.project, slug)
        self.assertEqual(pending[0]["id"], message["id"])
        self.assertEqual(pending[1]["text"], "A newer decision is needed")
        self.assertFalse(pending[1]["wake"])
        self.assertFalse(config.INCIDENT_INDEX.exists())

    def test_queued_launch_waits_for_setup_release_without_fault_or_attempt(self):
        task = T.new(self.project, "Queued launch", "Launch when setup is ready")
        with mock.patch.object(engines, "start_l2", return_value={"returncode": 0, "agent": {
                "id": "first-worker", "sessionId": "first-session"}}) as launch:
            with setup.operation_lock(self.project):
                server.dispatch_waiting(self.project)
                current = S.load_task(self.project, task["slug"])
                self.assertEqual((current["state"], current["attempt"]), ("queued", 0))
                self.assertFalse(current.get("fault"))
                launch.assert_not_called()
            server.dispatch_waiting(self.project)
            server.dispatch_waiting(self.project)
            launch.assert_called_once()
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        self.assertFalse(config.INCIDENT_INDEX.exists())

    def test_setup_and_provenance_failures_after_contention_still_fail_closed(self):
        task, worktree = self.blocked_owner()
        slug = task["slug"]
        first = T.message(self.project, slug, "burak", "Continue", by="burak")
        dispatch.request_task_operation(self.project, slug, "resume", "Authorized resume", actor="l3")
        with setup.operation_lock(self.project):
            self.assertTrue(dispatch.run_task_operation(self.project, slug)["held"])
        custom = self.repo / ".git/hooks/pre-commit"
        custom.write_text("#!/bin/sh\nexit 0\n")
        custom.chmod(0o755)
        with self.assertRaises(dispatch.ResumeFailure):
            dispatch.run_task_operation(self.project, slug)
        current = S.load_task(self.project, slug)
        self.assertEqual(current["fault"], "project-setup")
        self.assertEqual(current["daemon_request"]["status"], "failed")
        self.assertFalse(current.get("resume_claim"))
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [first["id"]])

        custom.unlink()
        setup.ensure_guards(self.project, slug=slug)
        git("switch", "-c", "wrong-owner", cwd=worktree)
        dispatch.request_task_operation(self.project, slug, "resume", "Guards repaired", actor="l3")
        with self.assertRaisesRegex(dispatch.ResumeFailure, "wrong-owner"):
            dispatch.run_task_operation(self.project, slug)
        current = S.load_task(self.project, slug)
        self.assertEqual(current["fault"], "task-git-provenance")
        self.assertEqual(current["daemon_request"]["status"], "failed")
        self.assertFalse(current.get("resume_claim"))
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertEqual([row["id"] for row in T.pending(self.project, slug)], [first["id"]])

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

    def test_observation_crossing_repair_completion_refreshes_operation_and_receipt(self):
        self.perform()
        git("init", "-q", "-b", "main", cwd=config.REPO)
        git("add", "hooks", cwd=config.REPO)
        git("commit", "-qm", "Fictional installation", cwd=config.REPO)
        sha = git("rev-parse", "HEAD", cwd=config.REPO).strip()
        old = config.REPO / ".altitude-source" / sha / "hooks"
        for module, boundary in ((setup, "_repository"), (git_policy, "inspect_hooks"), (setup, "_coordinator")):
            with self.subTest(boundary=boundary):
                git("config", "--local", "--unset-all", "core.hooksPath", cwd=self.repo)
                self.assertIn("Installed", self.step("guards", self.perform())["detail"])
                git("config", "--local", "core.hooksPath", str(old), cwd=self.repo)
                self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "stale")
                setup.request(self.project, "repair", actor="operator")
                operation = setup.read(self.project)["operation"]
                setup.save(self.project, operation={**operation, "state": "running", "step": "guards"})
                original = getattr(module, boundary)
                completed = False

                def finish_repair(*args, **kwargs):
                    nonlocal completed
                    result = original(*args, **kwargs)
                    if not completed:
                        completed = True
                        setup.run(self.project)
                    return result

                with mock.patch.object(module, boundary, side_effect=finish_repair):
                    raced = setup.observe(self.project)
                stored = setup.read(self.project)
                fresh = setup.observe(self.project)
                self.assertTrue(completed)
                self.assertEqual(stored["operation"]["id"], operation["id"])
                self.assertEqual(stored["operation"]["state"], "complete")
                self.assertEqual(stored["guards"]["action"], "updated")
                self.assertEqual(raced["operation"], stored["operation"])
                self.assertEqual(raced["steps"], fresh["steps"])
                self.assertEqual(raced["status"], "ready")
                self.assertIn("Updated", self.step("guards", raced)["detail"])

    def test_retry_accepted_during_observation_runs_when_the_read_releases_the_lock(self):
        self.perform()
        git("config", "--local", "--unset-all", "core.hooksPath", cwd=self.repo)
        setup.request(self.project, "repair", actor="operator")
        interrupted = setup.read(self.project)["operation"]
        setup.save(self.project, operation={**interrupted, "state": "running", "step": "guards"})
        inspected, accepted, locked, released = (threading.Event() for _ in range(4))
        for event in (accepted, released):
            self.addCleanup(event.set)
        original = setup._observe

        def paused(project, record, **kwargs):
            if threading.current_thread().name == "observer":
                if inspected.is_set():
                    locked.set()
                    self.assertTrue(released.wait(10))
                else:
                    inspected.set()
                    self.assertTrue(accepted.wait(10))
            return original(project, record, **kwargs)

        self.patch(setup, "_observe", paused)
        outcomes = {}
        observer = threading.Thread(target=lambda: outcomes.update(view=setup.observe(self.project)), name="observer")
        runner = threading.Thread(target=lambda: outcomes.update(run=setup.run(self.project)), name="runner")
        observer.start()
        self.assertTrue(inspected.wait(10))
        retry = setup.request(self.project, "repair", actor="operator")
        self.assertEqual(retry["operation"]["state"], "pending")
        self.assertNotEqual(retry["operation"]["id"], interrupted["id"])
        accepted.set()
        self.assertTrue(locked.wait(10))
        runner.start()
        released.set()
        observer.join(10)
        runner.join(10)
        self.assertFalse(observer.is_alive() or runner.is_alive())
        self.assertEqual(outcomes["view"]["operation"]["id"], retry["operation"]["id"])
        self.assertEqual(outcomes["view"]["status"], "checking")
        stored = setup.read(self.project)
        self.assertEqual(stored["operation"]["id"], retry["operation"]["id"])
        self.assertEqual(stored["operation"]["state"], "complete")
        self.assertEqual(stored["guards"]["action"], "installed")
        self.assertIn("Installed and verified", self.step("guards")["detail"])

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
