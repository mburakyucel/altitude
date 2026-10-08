"""Browser file reads use registered task identities and bounded, descriptor-relative storage."""
import http.client
import json
import os
import threading
from urllib.parse import urlencode
from unittest import mock

from tests.support import AltitudeCase
from tests.test_design_viewer import _RecordingHTTPServer
from altitude import config, server, state as S, tasks as T


class TestTaskFiles(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        self.slug = T.new(self.project, "Read operator guide", "Read a document")["slug"]
        self.directory = S.task_dir(self.project, self.slug)
        self.document = self.directory / "operator guide.md"
        self.sentinel = self.tmp / "must-not-execute"
        self.document.write_text(f"# Guide\n\n```sh\ntouch {self.sentinel}\n```\n", encoding="utf-8")
        self.logs = []
        self.patch(server, "log", new=self.logs.append)
        self.httpd = _RecordingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.assertEqual(self.httpd.errors, [])
        self.assertNotIn("Traceback", "\n".join(self.logs))

    def request(self, reference=None, *, project=None, method="GET", url=None):
        path = url or f"/api/files/{project or self.project}?" + urlencode({"path": str(reference or self.document)})
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if raw else None
        finally:
            connection.close()

    def assert_refused(self, reference=None, status=None, **kwargs):
        code, headers, data = self.request(reference, **kwargs)
        self.assertEqual(code, status) if status else self.assertIn(code, (400, 403, 404, 415))
        self.assertEqual(set(data), {"error"})
        self.assertNotIn(str(config.ROOT), data["error"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_absolute_path_and_encoded_file_uri_read_exact_text_without_execution(self):
        for reference in (str(self.document), self.document.as_uri()):
            with self.subTest(reference=reference):
                code, headers, data = self.request(reference)
                self.assertEqual(code, 200)
                self.assertEqual(data, {"name": self.document.name, "path": str(self.document),
                                       "current_path": str(self.document), "text": self.document.read_text(),
                                       "markdown": True})
                self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertFalse(self.sentinel.exists())

    def test_plain_empty_and_limit_sized_files_need_no_chat_publication(self):
        document = self.directory / ".unmentioned.txt"
        for text in ("", "é" * (T.TASK_FILE_LIMIT // 2)):
            with self.subTest(length=len(text)):
                document.write_text(text, encoding="utf-8")
                code, _, data = self.request(document)
                self.assertEqual(code, 200)
                self.assertEqual(data["text"], text)
                self.assertFalse(data["markdown"])

    def test_ancestor_lookup_needs_search_access_without_directory_contents(self):
        """#625: a permitted document beneath an ancestor whose directory contents are hidden."""
        search = getattr(os, "O_SEARCH", getattr(os, "O_PATH", 0))
        self.assertNotEqual(search, 0)
        original = os.open
        hidden = config.project_dir(self.project).parts[1]

        def opened(path, flags, *args, **kwargs):
            if os.fspath(path) == hidden and flags & search != search:
                raise PermissionError("fictional ancestor's directory contents are hidden")
            return original(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=opened):
            # Read-only directory traversal reproduces the endpoint's unavailable result.
            with mock.patch.object(T.platform, "directory_search_access", return_value=os.O_RDONLY):
                self.assert_refused(status=404)
            # Search-only descriptors still bind every component and final file to the real filesystem.
            with mock.patch.object(T.platform, "directory_search_access", return_value=search):
                code, _, data = self.request()
                self.assertEqual(code, 200)
                self.assertEqual(data["text"], self.document.read_text())

    def test_original_reference_survives_archive_and_reports_current_location(self):
        original = str(self.document)
        archived = S.archive_dir(self.project) / self.slug
        T.reject(self.project, self.slug, "Archive fixture after document review")
        for reference in (original, str(archived / self.document.name)):
            code, _, data = self.request(reference)
            self.assertEqual(code, 200)
            self.assertEqual(data["path"], reference)
            self.assertEqual(data["current_path"], str(archived / self.document.name))

    def test_wrong_project_unregistered_task_and_other_locations_are_unavailable(self):
        self.register("other-project", path=self.tmp / "other-repo")
        self.assert_refused(project="other-project", status=403)
        self.assert_refused(project="unregistered", status=403)
        orphan = S.tasks_dir(self.project) / "orphan"
        orphan.mkdir()
        (orphan / "guide.md").write_text("not a registered task")
        self.assert_refused(orphan / "guide.md", status=404)
        for path in (self.tmp / "private.md", self.directory / "nested" / "guide.md",
                     config.project_dir(self.project) / "private.md"):
            with self.subTest(path=path):
                self.assert_refused(path, status=403)

    def test_missing_active_document_does_not_read_an_older_archived_copy(self):
        archived = S.archive_dir(self.project) / self.slug
        archived.mkdir(parents=True)
        (archived / "status.json").write_text((self.directory / "status.json").read_text())
        (archived / self.document.name).write_text("stale copy")
        self.document.unlink()
        self.assert_refused(status=404)

    def test_uri_hosts_queries_fragments_traversal_and_relative_paths_are_refused(self):
        for path in ("file://localhost" + str(self.document), "file://remote" + str(self.document),
                     "file:" + str(self.document), self.document.as_uri() + "?raw=1",
                     self.document.as_uri() + "#heading", "guide.md", "../guide.md",
                     str(self.directory) + "/../" + self.slug + "/operator guide.md",
                     str(self.directory) + "//operator guide.md", str(self.directory / "bad\\name.md"),
                     self.document.as_uri().replace("/tasks/", "/tasks/%2e%2e/tasks/"),
                     self.document.as_uri() + "%00", "file:///[bad%ff.md",
                     self.document.as_uri().replace("guide", "gui\nde")):
            with self.subTest(path=path):
                self.assert_refused(path, status=403)

    def test_unsupported_encoding_type_size_directory_and_fifo(self):
        for name, data in (("guide.html", b"<script>bad()</script>"),
                           ("binary.md", b"\xff\xfe"), ("large.md", b"a" * (T.TASK_FILE_LIMIT + 1))):
            with self.subTest(name=name):
                path = self.directory / name
                path.write_bytes(data)
                self.assert_refused(path, status=415)
        (self.directory / "folder.md").mkdir()
        os.mkfifo(self.directory / "pipe.md")
        self.assert_refused(self.directory / "folder.md", status=415)
        self.assert_refused(self.directory / "pipe.md", status=415)

    def test_missing_and_inaccessible_document_are_honest_errors(self):
        self.assert_refused(self.directory / "missing.md", status=404)
        self.document.chmod(0)
        self.addCleanup(self.document.chmod, 0o600)
        self.assert_refused(status=404)

    def test_document_and_status_symlinks_do_not_read_their_targets(self):
        outside = self.tmp / "outside.md"
        outside.write_text("private outside target")
        self.document.unlink()
        self.document.symlink_to(outside)
        self.assert_refused(status=404)
        self.document.unlink()
        self.document.write_text("allowed text")
        status = self.directory / "status.json"
        outside_status = self.tmp / "status.json"
        status.rename(outside_status)
        status.symlink_to(outside_status)
        self.assert_refused(status=404)

    def test_task_project_and_runtime_directory_symlinks_are_refused(self):
        for directory in (self.directory, config.project_dir(self.project), config.ROOT):
            with self.subTest(directory=directory):
                moved = directory.with_name(directory.name + "-moved")
                directory.rename(moved)
                directory.symlink_to(moved, target_is_directory=True)
                try:
                    self.assert_refused(status=404)
                finally:
                    directory.unlink()
                    moved.rename(directory)

    def test_invalid_task_record_cannot_authorize_a_document(self):
        for value in ({"slug": "different", "state": "running"}, {"slug": self.slug},
                      {"slug": self.slug, "state": []}, [], "broken"):
            with self.subTest(value=value):
                (self.directory / "status.json").write_text(json.dumps(value))
                self.assert_refused(status=404)

    def test_task_directory_replacement_after_open_cannot_redirect_the_document(self):
        original_open = os.open
        outside = self.tmp / "other-task"
        outside.mkdir()
        (outside / self.document.name).write_text("outside secret")
        moved = self.directory.with_name(self.slug + "-held")

        def replace_after_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == self.slug:
                self.directory.rename(moved)
                self.directory.symlink_to(outside, target_is_directory=True)
            return fd

        with mock.patch.object(T.os, "open", side_effect=replace_after_open):
            code, _, data = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(data["text"], (moved / self.document.name).read_text())
        self.assertNotIn("outside secret", data["text"])

    def test_racing_document_symlink_is_refused(self):
        original_open = os.open
        outside = self.tmp / "private.md"
        outside.write_text("outside secret")

        def replace_before_open(path, flags, *args, **kwargs):
            if path == self.document.name:
                self.document.unlink()
                self.document.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(T.os, "open", side_effect=replace_before_open):
            self.assert_refused(status=404)

    def test_query_shape_and_read_only_method_boundary(self):
        base = f"/api/files/{self.project}"
        query = urlencode({"path": str(self.document)})
        for path in (base, base + "?path=", base + "?" + query + "&" + query,
                     base + "?" + query + "&other=1", base + "/extra?" + query,
                     base + "/?" + query, "/api//files/" + self.project + "?" + query,
                     base + "?path=%ff", base + "?path"):
            with self.subTest(path=path):
                self.assert_refused(url=path, status=400)
        self.assert_refused(method="POST", status=405)
        code, _, body = self.request(method="HEAD")
        self.assertEqual((code, body), (405, None))
