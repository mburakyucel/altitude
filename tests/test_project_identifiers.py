"""Project identifiers keep temporary project storage within its named folder."""
import http.client
import json
import threading
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, l3, server, state as S, tasks as T


class ProjectIdentifierCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.runtime = self.tmp / "runtime"
        self.patch(config, "ROOT", self.runtime)
        self.patch(config, "RUNTIME_ROOT", self.runtime.resolve())
        self.patch(config, "PROJECTS_FILE", self.runtime / "projects.json")
        self.patch(config, "MONITOR_DIR", self.runtime / "monitor")
        self.patch(config, "PROJECT_ROOTS", [self.tmp / "repositories"])
        self.sentinel = self.tmp / "sentinel"
        self.sentinel.mkdir()
        (self.sentinel / ".lock").write_bytes(b"preserved lock\n")
        (self.sentinel / "status.json").write_bytes(b'{"preserved":true}\n')

    def invalid_names(self):
        return ("", ".", "..", "../sentinel", str(self.sentinel), "nested/project", "project/",
                r"nested\project", r"C:\project", "name\x00suffix", None, 123, ["project"], {"name": "project"})

    def snapshot(self):
        return {str(p.relative_to(self.tmp)): p.read_bytes() if p.is_file() else None
                for p in self.tmp.rglob("*")}


class TestProjectIdentifiers(ProjectIdentifierCase):
    def test_invalid_names_refuse_storage_and_settings_without_filesystem_effects(self):
        before = self.snapshot()
        for name in self.invalid_names():
            operations = (
                lambda: config.project_dir(name),
                lambda: config.project(name),
                lambda: S.list_tasks(name, include_archive=True),
                lambda: S.load_task(name, "saved-task"),
                lambda: S.counts_path(name, {"slug": "saved-task", "attempt": 1}),
                lambda: dispatch.request_setting(name, "l2_preference", None, "Fixture setting",
                                                 actor=config.OPERATOR_ACTOR),
            )
            for operation in operations:
                # None is the explicit machine-settings scope, not a project identifier.
                if name is None and operation is operations[-1]:
                    continue
                with self.subTest(name=name, operation=operations.index(operation)):
                    with self.assertRaisesRegex(ValueError, "invalid project name"):
                        operation()
                    self.assertEqual(self.snapshot(), before)
            with self.subTest(name=name, operation="lock"):
                with self.assertRaisesRegex(ValueError, "invalid project name"):
                    with S.project_lock(name):
                        self.fail("invalid project lock admitted")
                self.assertEqual(self.snapshot(), before)
            with self.subTest(name=name, operation="activity"):
                with self.assertRaisesRegex(ValueError, "invalid project name"):
                    with config.project_activity(name):
                        self.fail("invalid project activity admitted")
                self.assertEqual(self.snapshot(), before)

    def test_invalid_registration_precedes_repository_inspection(self):
        before = self.snapshot()
        with mock.patch.object(config, "project_roots") as roots:
            for name in self.invalid_names():
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid project name"):
                    with config.add_project(name):
                        self.fail("invalid project registered")
            roots.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_valid_names_initialize_apply_settings_and_restore_archives(self):
        for name in ("a", "Atlas_1.2", "Project With Spaces", "naïve-工作", ".hidden", "three...dots"):
            with self.subTest(name=name):
                self.assertEqual(config.require_project_name(name), name)
                self.assertEqual(config.project_dir(name), self.runtime / name)
                self.assertFalse(config.project_dir(name).exists())
                with config.add_project(name, path=self.repo):
                    self.assertTrue((config.project_dir(name) / ".lock").is_file())
                request = dispatch.request_setting(name, "l2_preference", None, "Fixture setting",
                                                   actor=config.OPERATOR_ACTOR)
                self.assertEqual(request["request"]["project"], name)
                self.assertEqual(dispatch._run_setting(name, "l2_preference")["status"], "done")
                task = T.new(name, "Saved task", "Fictional task.")
                T.reject(name, task["slug"], "Fixture complete")
                self.assertTrue((S.archive_dir(name) / task["slug"] / "status.json").is_file())
                config.remove_project(name)
                self.assertEqual(S.load_task(name, task["slug"])["state"], "rejected")
                with config.add_project(name, path=self.repo):
                    pass
                self.assertEqual(S.list_tasks(name, include_archive=True)[0]["slug"], task["slug"])

    def test_machine_settings_keep_the_explicit_none_scope(self):
        request = dispatch.request_setting(None, "terminal", False, "Fixture setting", actor=config.OPERATOR_ACTOR)
        self.assertIsNone(request["request"]["project"])
        self.assertEqual(dispatch._run_setting(None, "terminal")["status"], "done")
        self.assertFalse(config.machine_settings()["terminal"])

    def test_ordinary_file_occupying_task_slug_is_a_collision(self):
        with config.add_project("fictional", path=self.repo):
            pass
        tasks = S.tasks_dir("fictional")
        tasks.mkdir(exist_ok=True)
        (tasks / "saved-task").write_text("ordinary collision")
        with mock.patch.object(S, "regen_state_md"):
            task = T.new("fictional", "Saved task", "Fictional request")
        self.assertEqual(task["slug"], "saved-task-2")
        self.assertEqual((tasks / "saved-task").read_text(), "ordinary collision")
        with self.assertRaises(NotADirectoryError):
            S.list_tasks("fictional")


class TestProjectIdentifierHTTP(ProjectIdentifierCase):
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

    def test_defaults_reject_invalid_project_without_storage_changes(self):
        before = self.snapshot()
        for name in self.invalid_names():
            with self.subTest(name=name):
                status, response = self.post("/api/defaults", project=name, setting="l2_preference", value=None)
                self.assertEqual(status, 400)
                self.assertIn("unknown machine setting" if name is None else "invalid project name", response["error"])
                self.assertEqual(self.snapshot(), before)

    def test_registration_rejects_invalid_names_before_history_lookup(self):
        before = self.snapshot()
        with mock.patch.object(l3, "chat_history") as history:
            for name in self.invalid_names():
                with self.subTest(name=name):
                    status, response = self.post("/api/project/add", name=name, path=str(self.repo))
                    self.assertEqual(status, 400)
                    self.assertIn("invalid project name", response["error"])
                    self.assertEqual(self.snapshot(), before)
            history.assert_not_called()

    def test_valid_api_registration_and_defaults(self):
        name = "Atlas_1.2"
        status, response = self.post("/api/project/add", name=name, path=str(self.repo))
        self.assertEqual(status, 200, response)
        self.assertFalse(response["restored"])
        status, response = self.post("/api/defaults", project=name, setting="l2_preference", value=None)
        self.assertEqual(status, 200, response)
        self.assertEqual(S.read_json(config.project_dir(name) / "l2_preference-request.json")["status"], "done")
