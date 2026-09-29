"""Removing a project detaches L3 without abandoning work or erasing saved history."""
import http.client
import json
import os
import select
import subprocess
import sys
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, engines, l3, server, state as S, tasks as T


class TestProjectLifecycle(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.setenv("ALTITUDE_ACTOR", None)

    def test_unfinished_work_refuses_removal_without_changing_tasks(self):
        task = T.new(self.project, "Keep my work", "Do the work.")
        for state in S.OPEN_STATES:
            task["state"] = state
            S.save_task(self.project, task)
            with self.subTest(state=state):
                with self.assertRaisesRegex(config.ProjectBusy, "Finish or reject.*keep-my-work"):
                    config.remove_project(self.project)
                self.assertTrue(config.is_managed(self.project))
                self.assertEqual(S.load_task(self.project, task["slug"]), task)
        result = self.alt("project", "remove", self.project)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Finish or reject", result.stderr)

    def test_rejected_worker_and_unsettled_claims_still_prevent_removal(self):
        task = T.new(self.project, "Ending worker", "Do the work.")
        task = T.reject(self.project, task["slug"], "cancel")
        for fields in ({"agent_id": "still-live", "l2_engine": config.ENGINES[0]},
                       {"dispatching": S.now()}, {"resume_claim": {"id": "in-flight"}},
                       {"daemon_request": {"status": "executing"}}):
            with self.subTest(fields=fields), mock.patch.object(engines, "worker_live", return_value=True):
                S.save_task(self.project, {**task, **fields})
                with self.assertRaisesRegex(config.ProjectBusy, "still finishing"):
                    config.remove_project(self.project)
        S.save_task(self.project, task)
        config.remove_project(self.project)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")

    def test_separate_process_turn_blocks_cli_removal_but_not_another_project(self):
        script = """
import sys
from altitude import l3
with l3._turn_scope(sys.argv[1], 'chat') as turn:
    assert turn
    print('admitted', flush=True)
    sys.stdin.readline()
"""
        process = subprocess.Popen([sys.executable, "-c", script, self.project], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], "turn did not enter")
            self.assertEqual(process.stdout.readline().strip(), "admitted")
            self.assertFalse(l3.busy(self.project), "local busy cannot see the other process")
            refused = self.alt("project", "remove", self.project)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Project activity is still finishing", refused.stderr)
            self.register("another-idle-project")
            config.remove_project("another-idle-project")
        finally:
            process.communicate("finish\n", timeout=5)
        self.assertEqual(process.returncode, 0)
        config.remove_project(self.project)

    def test_task_creation_rechecks_registration_after_issue_intake(self):
        with mock.patch.object(T.github_intake, "inline", side_effect=lambda *args: config.remove_project(self.project)):
            with self.assertRaises(KeyError):
                T.new(self.project, "Racing task", "Do the work.")
        self.assertEqual(S.list_tasks(self.project), [])

    def test_removal_preserves_disk_sessions_and_queue_and_registration_restores_them(self):
        self.patch(config, "PROJECT_ROOTS", [self.repo.parent])
        sentinel = self.repo / "keep.txt"
        sentinel.write_text("Repository remains on disk\n")
        l3.chat_log(self.project, "assistant", "Saved answer", trigger="chat")
        l3.save_info(self.project, {"session_id": "saved-session", "sessions": {"configured": {"session_id": "saved-session"}}})
        queued = l3.queue_message(self.project, "Saved request", trigger="chat", role="burak")
        task = T.new(self.project, "Old task", "Done already.")
        T.reject(self.project, task["slug"], "no longer needed")
        original = {p: p.read_bytes() for p in (l3.info_path(self.project), l3.queue_path(self.project))}
        config.remove_project(self.project)
        self.assertNotIn(self.project, config.load_projects())
        self.assertFalse(next(p for p in config.discover_projects() if p["path"] == str(self.repo))["managed"])
        self.assertEqual(sentinel.read_text(), "Repository remains on disk\n")
        with mock.patch.object(l3, "_select") as route:
            self.assertIsNone(l3.deliver_queued(self.project))
            self.assertFalse(l3.turn(self.project, "Do not run")["completed"])
            route.assert_not_called()
        self.assertFalse(server.request_l3_drain(self.project))
        with self.assertRaisesRegex(ValueError, "not managed"):
            server.l3_verb_request(self.project, {"kind": "alt", "args": ["state"]})
        for path, contents in original.items():
            self.assertEqual(path.read_bytes(), contents)
        with config.add_project(self.project, path=self.repo):
            pass
        self.assertEqual(l3.chat_history(self.project)[0]["text"], "Saved answer")
        self.assertEqual(l3.queued(self.project)[0]["id"], queued["id"])
        with mock.patch.object(l3, "_select", return_value={"engine": config.ENGINES[0]}), \
             mock.patch.object(l3, "turn", return_value={"completed": True}) as turn:
            l3.deliver_queued(self.project)
        self.assertEqual(turn.call_args.args[1], queued["text"])
        self.assertEqual(l3.info_path(self.project).read_bytes(), original[l3.info_path(self.project)])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "rejected")

    def test_report_after_archive_still_holds_removal(self):
        def reporting(*args):
            with self.assertRaises(config.ProjectBusy):
                config.remove_project(self.project)
        with mock.patch.object(server, "_report_turn", side_effect=reporting):
            server.report_turn(self.project, {}, {})
        config.remove_project(self.project)
        with mock.patch.object(server, "_report_turn") as report:
            server.report_turn(self.project, {}, {})
            report.assert_not_called()

    def test_delayed_broker_cleanup_preserves_a_reattached_project(self):
        self.addCleanup(server.stop_l3_verb_brokers)
        broker = server.ensure_l3_verb_broker(self.project)
        config.remove_project(self.project)
        with config.add_project(self.project, path=self.repo):
            self.assertIs(server.ensure_l3_verb_broker(self.project), broker)
        server.remove_l3_verb_broker(self.project)
        self.assertTrue(broker.socket_path.is_socket())
        self.assertIs(server.ensure_l3_verb_broker(self.project), broker)
        config.remove_project(self.project)
        server.remove_l3_verb_broker(self.project)
        self.assertFalse(broker.socket_path.exists())
        self.assertNotIn(self.project, server._l3_verb_brokers)


    def test_a_name_differing_only_by_case_is_refused_on_every_host(self):
        # A case-insensitive disk (the macOS default) gives both names one runtime folder.
        before = config.load_projects()
        for name in (self.project.upper(), self.project.capitalize()):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "must differ by more than case"):
                with config.add_project(name, path=self.repo):
                    pass
        self.assertEqual(config.load_projects(), before)
        with config.add_project(self.project, path=self.repo):  # re-registering the same name still works
            pass


class TestProjectLifecycleHTTP(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(server, "spawn", return_value=True)
        self.patch(server, "log")
        self.patch(server, "ensure_l3_verb_broker")
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, endpoint, **body):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request("POST", endpoint, json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_removal_denial_then_restoration_with_preserved_error_history(self):
        task = T.new(self.project, "Unfinished work", "Do the work.")
        status, response = self.post("/api/project/remove", name=self.project)
        self.assertEqual(status, 409)
        self.assertIn(task["slug"], response["error"])
        T.reject(self.project, task["slug"], "cancel")
        l3.chat_log(self.project, "error", "An old start failed", trigger="start")
        self.assertEqual(self.post("/api/project/remove", name=self.project)[0], 200)
        self.assertEqual(self.post("/api/chat", project=self.project, text="Do not dispatch")[0], 409)
        self.assertFalse(l3.queued(self.project))
        status, response = self.post("/api/project/add", name=self.project, path=str(self.repo))
        self.assertEqual(status, 200)
        self.assertTrue(response["restored"])
        self.assertEqual(l3.chat_history(self.project)[0]["text"], "An old start failed")

    def test_removal_wins_race_with_chat_queue_admission(self):
        def removed_during_admission():
            config.remove_project(self.project)
            return True
        with mock.patch.object(config, "restart_in_progress", side_effect=removed_during_admission):
            status, response = self.post("/api/chat", project=self.project, text="Do not accept after removal")
        self.assertEqual(status, 409)
        self.assertIn("not managed", response["error"])
        self.assertFalse(l3.queued(self.project))
