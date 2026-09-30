"""#543: replacement admission preserves ownership and queues, without using a host runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import audit, config, dispatch, engines, l3, platform, project_setup, reviews, server, state as S, tasks as T, tls
from scripts import container


class ContainerAdmission(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.patch(config, "HOME", self.tmp / "image-home")
        self.patch(platform, "containerized", return_value=True)
        self.identity = self.patch(platform, "_container_instance", return_value="a" * 32)
        self.patch(platform, "status", return_value={"ActiveState": "active"})
        self.patch(platform, "container_ready")
        platform._lifecycle_write("a" * 32, False)

    def change(self, action):
        return platform.change_container_lifecycle(action, self.identity.return_value)

    def test_same_instance_restart_preserves_admission_replacement_and_bad_receipts_close_it(self):
        self.assertTrue(platform.container_lifecycle()["ready"])
        self.assertFalse(self.change("pause")["ready"])
        self.assertFalse(platform.container_lifecycle()["ready"])
        self.assertTrue(self.change("continue")["ready"])
        self.identity.return_value = "b" * 32
        self.assertFalse(platform.container_lifecycle()["ready"])
        with self.assertRaisesRegex(ValueError, "container changed"):
            platform.change_container_lifecycle("continue", "a" * 32)
        self.assertTrue(self.change("continue")["ready"])
        receipt = platform._lifecycle_directory() / "lifecycle.json"
        for bad in ('{', '[]', '{"paused":"false"}', '{}'):
            receipt.write_text(bad)
            self.assertFalse(platform.container_lifecycle()["ready"])
            with config.provider_admission() as held:
                self.assertTrue(held)
        receipt.unlink()
        self.assertFalse(platform.container_lifecycle()["ready"])
        self.assertTrue(self.change("continue")["ready"])

    def test_identity_or_state_access_failure_refuses_and_unready_service_cannot_continue(self):
        for error in (PermissionError("unreadable"), ValueError("invalid")):
            with mock.patch.object(platform, "_container_instance", side_effect=error):
                self.assertFalse(platform.container_lifecycle()["ready"])
                with config.provider_admission() as held:
                    self.assertTrue(held)
        with mock.patch.object(platform, "container_ready", side_effect=RuntimeError("not ready")):
            with self.assertRaisesRegex(RuntimeError, "not ready"):
                self.change("continue")

    def test_same_container_restore_is_not_replacement_and_is_explicitly_unsupported(self):
        receipt = platform._lifecycle_directory() / "lifecycle.json"
        backup = receipt.read_bytes()
        self.change("pause")
        receipt.write_bytes(backup)
        self.assertTrue(platform.container_lifecycle()["ready"])
        self.identity.return_value = "b" * 32  # Supported restore creates a new container.
        self.assertFalse(platform.container_lifecycle()["ready"])

    def test_native_report_restart_keeps_its_existing_queue_behavior(self):
        with mock.patch.object(platform, "containerized", return_value=False), \
             mock.patch.object(config, "restart_in_progress", return_value=True):
            result = l3.turn(self.project, "Native report", trigger="report-landed")
        self.assertIn("queued", result)
        self.assertNotIn("held", result)
        self.assertEqual(len(l3.queued(self.project)), 1)

    def test_coordinator_start_waits_without_failed_intro_or_duplicate_queued_turn(self):
        self.change("pause")
        with mock.patch.object(server, "server_l3_turn", side_effect=AssertionError("premature intro")):
            server.start_l3(self.project)
            server.start_l3(self.project)
        record = project_setup.read(self.project)
        self.assertTrue(record["start_requested"])
        self.assertFalse(record.get("intro"))
        self.assertEqual(l3.queued(self.project), [])
        self.change("continue")
        with mock.patch.object(server, "server_l3_turn", return_value={"completed": True}) as intro:
            server.start_l3(self.project)
        intro.assert_called_once()
        self.assertEqual(project_setup.read(self.project)["intro"]["state"], "complete")

    def test_native_does_not_read_receipts_or_instance_identity(self):
        with mock.patch.object(platform, "containerized", return_value=False), \
             mock.patch.object(platform, "_lifecycle_directory", side_effect=AssertionError("native receipt read")), \
             mock.patch.object(platform, "_container_instance", side_effect=AssertionError("native identity read")):
            self.assertIsNone(platform.container_lifecycle())
            with config.provider_admission() as held:
                self.assertIsNone(held)

    def test_agent_roles_and_task_credentials_cannot_use_operator_continuation(self):
        for actor in ("l1", "l2", "l3", "altd"):
            with mock.patch.dict(os.environ, ALTITUDE_ACTOR=actor), self.assertRaises(PermissionError):
                self.change("continue")
        with mock.patch.dict(os.environ, ALTITUDE_ACTOR=config.OPERATOR_ACTOR, ALTITUDE_TASK="fixture"), \
             self.assertRaises(PermissionError):
            self.change("pause")

    def test_pause_serializes_with_admission_without_cancelling_the_already_admitted_call(self):
        entered, release = threading.Event(), threading.Event()
        observed, errors = [], []
        @config.admitted_provider
        def provider():
            observed.append("invoked")
        def launch():
            try:
                with config.provider_admission() as held:
                    self.assertIsNone(held)
                    entered.set()
                    self.assertTrue(release.wait(5))
                    provider()  # Its caller was admitted before pause, not an untracked late launch.
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=launch)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            state = self.change("pause")
            self.assertFalse(state["ready"])
            self.assertTrue(state["admitted_calls_active"])
            with self.assertRaises(config.AdmissionPaused):
                provider()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(observed, ["invoked"])
        self.assertFalse(platform.container_lifecycle()["admitted_calls_active"])

    def test_every_provider_primitive_refuses_before_launch_or_capability_side_effects(self):
        self.change("pause")
        invocations = [
            lambda: engines.claude_print("fixture", cwd=self.tmp),
            lambda: engines.codex_exec("fixture", cwd=self.tmp),
            lambda: engines._start_worker("claude", "fixture", "prompt", cwd=self.tmp, job_root=self.tmp),
            lambda: engines._start_worker("codex", "fixture", "prompt", cwd=self.tmp, job_root=self.tmp),
            lambda: engines.conversation_review(self.project, "fixture", engine="claude", model="fixture"),
            lambda: engines.review("fixture", engine="codex", snapshot=self.tmp, runtime=self.tmp),
        ]
        with mock.patch.object(engines.subprocess, "Popen", side_effect=AssertionError("provider launch")), \
             mock.patch.object(engines, "review_capability", side_effect=AssertionError("capability side effect")):
            for call in invocations:
                with self.subTest(call=call), self.assertRaises(config.AdmissionPaused):
                    call()

    def test_an_exited_admission_process_releases_its_lease(self):
        code = """
import sys
from pathlib import Path
from altitude import config, platform
config.HOME = Path(sys.argv[1])
platform.containerized = lambda: True
platform._container_instance = lambda: 'a' * 32
with config.provider_admission() as held:
    assert held is None, held
    print('admitted', flush=True)
    sys.stdin.read()
"""
        child = subprocess.Popen([sys.executable, "-c", code, str(config.HOME)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import select
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            self.assertEqual(child.stdout.readline().strip(), "admitted")
            self.assertTrue(self.change("pause")["admitted_calls_active"])
            child.kill()
            child.communicate(timeout=5)
            self.assertFalse(platform.container_lifecycle()["admitted_calls_active"])
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_dispatch_and_resume_hold_before_mutating_task_or_claiming_messages(self):
        queued = T.new(self.project, "Queued", "Do not start while paused", hold_merge="Operator review")
        blocked = T.new(self.project, "Blocked", "Keep the owned session", hold_merge="Operator review")
        blocked.update(state="blocked", attempt=1, agent_id="fixture-worker", session_id="saved-session")
        S.save_task(self.project, blocked)
        message = T.message(self.project, blocked["slug"], config.OPERATOR_ACTOR, "Resume this work")
        before = {t["slug"]: S.load_task(self.project, t["slug"]) for t in (queued, blocked)}
        self.change("pause")
        with self.assertRaisesRegex(T.TransitionError, "paused"):
            dispatch.run(self.project, queued["slug"])
        self.assertIn("paused", dispatch.resume(self.project, blocked["slug"])["held"])
        for slug, state in before.items():
            self.assertEqual(S.load_task(self.project, slug), state)
        self.assertEqual([row["id"] for row in T.pending(self.project, blocked["slug"])], [message["id"]])
        self.change("continue")
        with mock.patch.object(dispatch, "_resume", return_value={"fixture": True}) as resume:
            self.assertEqual(dispatch.resume(self.project, blocked["slug"]), {"fixture": True})
            resume.assert_called_once()

    def test_prelaunch_claim_is_reconciled_while_paused_without_launch_or_losing_inbox(self):
        task = T.new(self.project, "Interrupted claim", "Preserve input")
        task.update(state="blocked", attempt=1, agent_id="fixture-worker", session_id="saved-session")
        S.save_task(self.project, task)
        message = T.message(self.project, task["slug"], config.OPERATOR_ACTOR, "Keep this input")
        T.claim_resume(self.project, task["slug"])
        task = S.load_task(self.project, task["slug"])
        task["resume_claim"]["owner_process"]["pid"] = 999999999
        S.save_task(self.project, task)
        self.change("pause")
        with mock.patch.object(engines, "resume_l2", side_effect=AssertionError("must not launch")):
            self.assertIn("paused", dispatch.resume(self.project, task["slug"])["held"])
        self.assertFalse(S.load_task(self.project, task["slug"]).get("resume_claim"))
        self.assertIn(message["id"], [row["id"] for row in T.pending(self.project, task["slug"])])

    def test_browser_continue_stays_pending_until_global_continuation(self):
        task = T.new(self.project, "Queued Continue", "Do not claim the pending operation")
        task.update(state="blocked", attempt=1, agent_id="fixture-worker", session_id="saved-session")
        S.save_task(self.project, task)
        dispatch.request_task_operation(self.project, task["slug"], "resume", "Continue my task", actor=config.OPERATOR_ACTOR)
        before = S.load_task(self.project, task["slug"])
        self.change("pause")
        result = dispatch.run_task_operation(self.project, task["slug"])
        self.assertTrue(result["pending"])
        self.assertIn("paused", result["held"])
        self.assertEqual(S.load_task(self.project, task["slug"]), before)

    def test_chat_report_audit_review_and_setup_wait_without_consuming_their_work(self):
        self.change("pause")
        first = l3.turn(self.project, "Keep my message")
        self.assertIn("paused", first["error"])
        queue = l3.queued(self.project)
        self.assertEqual(len(queue), 1)
        report = l3.turn(self.project, "Report still needs attention", trigger="report-landed")
        self.assertTrue(report["held"])
        self.assertEqual(l3.queued(self.project), queue)
        l3.deliver_queued(self.project)
        self.assertEqual(l3.queued(self.project), queue)
        with mock.patch.object(reviews, "_run", side_effect=AssertionError("claimed review")):
            with self.assertRaisesRegex(T.TransitionError, "paused"):
                reviews.run(self.project, "fixture", "review", actor="l2", expected_attempt=1)
        with mock.patch.object(audit, "_run", side_effect=AssertionError("claimed audit")):
            audit.run(self.project)
        project_setup.request(self.project, "check", actor=config.OPERATOR_ACTOR)
        before = project_setup.read(self.project)
        project_setup.run(self.project)
        self.assertEqual(project_setup.read(self.project), before)

    def test_host_launcher_fences_the_instance_seen_before_the_mutation(self):
        calls = []
        self.patch(container,'owned',return_value={'Id':'fictional','Config':{'Labels':{'io.altitude.lineage':'1'*32}},'Mounts':[]})
        self.patch(platform,'container_user_environment',return_value={'XDG_RUNTIME_DIR':str(self.tmp)})
        self.patch(platform,'container_copy_available')
        def execute(instance, command):
            calls.append(command)
            return json.dumps(platform.change_container_lifecycle(command[-2], command[-1]))
        with mock.patch.object(container, "execute", side_effect=execute):
            observed = platform.container_lifecycle()["instance"]
            self.assertIn("--instance " + observed, platform.container_lifecycle()["continue_command"])
            self.identity.return_value = "b" * 32
            with self.assertRaisesRegex(ValueError, "container changed"):
                container.lifecycle("fixture", "continue", expected=observed)
            self.assertFalse(platform.container_lifecycle()["ready"])
            with self.assertRaisesRegex(ValueError, "Pass --instance"):
                container.lifecycle("fixture", "continue")
            self.assertTrue(container.lifecycle("fixture", "continue", expected="b" * 32)["ready"])
        self.assertEqual(calls[0][-2:], ["continue", "a" * 32])


class FreshImageInitialization(AltitudeCase):
    def test_readiness_proves_local_service_https_not_just_active_unit(self):
        found = {"pid": 123, "tls": True, "port": 19443, "tls_dir": self.tmp,
                 "url": "https://fixture.invalid:19443"}
        with mock.patch.object(tls, "service", return_value=found), mock.patch.object(tls, "_proven") as proven:
            platform.container_ready()
            proven.assert_called_once_with({**found, "url": "https://localhost:19443"})
            proven.side_effect = tls.TLSFailure("HTTPS not ready")
            with self.assertRaisesRegex(tls.TLSFailure, "not ready"):
                platform.container_ready()

    def test_only_empty_home_and_projects_initialize_without_an_operator(self):
        home, projects = self.tmp / "home", self.tmp / "home/Projects"
        projects.mkdir(parents=True)
        instance = self.tmp / "instance"
        self.patch(platform, "CONTAINER_INSTANCE", instance)
        self.patch(platform, "_container_instance", return_value="a" * 32)
        with mock.patch.object(platform.subprocess, "run") as initialize:
            platform._initialize_container_lifecycle(home, projects)
            first = instance.read_bytes()
            initialize.assert_called_once()
            self.assertEqual(initialize.call_args.kwargs["user"], 1000)
            self.assertEqual(initialize.call_args.kwargs["extra_groups"], ())
            (home / ".config").mkdir()
            platform._initialize_container_lifecycle(home, projects)
            self.assertEqual(initialize.call_count, 1)
            self.assertEqual(instance.read_bytes(), first)
            (home / ".config").rmdir()
            (projects / "saved-project").mkdir()
            platform._initialize_container_lifecycle(home, projects)
            self.assertEqual(initialize.call_count, 1)
