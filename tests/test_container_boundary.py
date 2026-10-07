"""The image boundary refuses authority-bearing routes regardless of client/socket classification."""
import http.client
import json
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, installation, l3, platform, server, source_tls, speech, state as S, tasks as T, terminal, tls


class TestContainerIdentity(AltitudeCase):
    def identity(self, uid=0):
        marker = mock.MagicMock()
        marker.lstat.return_value = SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | 0o644)
        parent = mock.Mock()
        parent.lstat.return_value = SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR | 0o755)
        marker.parents = [parent, Path('/')]
        marker.open.return_value.__enter__.return_value.read.return_value = b"altitude-container-v1\n"
        self.patch(platform, 'CONTAINER_MARKER', marker)
        self.patch(Path, 'lstat', return_value=SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR | 0o755))
        return marker, parent

    def test_absent_marker_is_native_even_with_environment_claim(self):
        marker = self.tmp / "absent"
        with mock.patch.object(platform, "CONTAINER_MARKER", marker), mock.patch.dict(os.environ, {"container": "podman"}):
            self.assertFalse(platform.containerized())

    def test_image_marker_requires_root_owned_regular_file_and_parents(self):
        marker, parent = self.identity()
        with mock.patch.object(platform, "CONTAINER_MARKER", marker):
            self.assertTrue(platform.containerized())
            for mode, uid in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o666, 0), (stat.S_IFREG | 0o644, 1000)):
                marker.lstat.return_value = SimpleNamespace(st_uid=uid, st_mode=mode)
                with self.assertRaises(RuntimeError):
                    platform.containerized()
            marker.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
            parent.lstat.return_value = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o755)
            with self.assertRaises(RuntimeError):
                platform.containerized()

    def test_single_user_namespace_recognizes_protected_image_and_refuses_native_authority(self):
        self.identity(65534)
        proc = self.tmp / 'proc'
        (proc / 'self').mkdir(parents=True)
        (proc / 'sys/kernel').mkdir(parents=True)
        (proc / 'self/uid_map').write_text('1000 0 1\n')
        (proc / 'sys/kernel/overflowuid').write_text('65534\n')
        self.patch(platform, 'PROC', proc)
        self.patch(os, 'geteuid', return_value=1000)
        self.patch(os, 'access', return_value=False)
        self.assertTrue(platform.containerized())
        self.patch(platform, 'CONTAINER_INSTANCE', platform.CONTAINER_MARKER)
        platform.CONTAINER_MARKER.open.return_value.__enter__.return_value.read.return_value = b'a' * 32 + b'\n'
        self.assertEqual(platform._container_instance(), 'a' * 32)
        platform.CONTAINER_MARKER.open.return_value.__enter__.return_value.read.return_value = b'altitude-container-v1\n'
        with self.assertRaisesRegex(RuntimeError, 'image-managed'):
            platform.require_native_application()

    def test_namespace_identity_rejects_writable_paths_and_invalid_mapping(self):
        marker, parent = self.identity(65534)
        proc = self.tmp / 'proc'
        (proc / 'self').mkdir(parents=True)
        (proc / 'sys/kernel').mkdir(parents=True)
        mapping = proc / 'self/uid_map'
        mapping.write_text('1000 0 1\n')
        (proc / 'sys/kernel/overflowuid').write_text('65534\n')
        self.patch(platform, 'PROC', proc)
        self.patch(os, 'geteuid', return_value=1000)
        access = self.patch(os, 'access', return_value=False)
        for target in (marker, parent, Path('/')):
            access.side_effect = lambda path, *a, **kw: path == target
            with self.subTest(writable=target), self.assertRaises(RuntimeError):
                platform.containerized()
        access.side_effect = None
        for entry in (marker, parent):
            original = entry.lstat.return_value
            for uid, mode in ((1000, original.st_mode), (65533, original.st_mode),
                              (65534, original.st_mode | 0o020), (65534, stat.S_IFLNK | 0o755)):
                entry.lstat.return_value = SimpleNamespace(st_uid=uid, st_mode=mode)
                with self.subTest(uid=uid, mode=mode), self.assertRaises(RuntimeError):
                    platform.containerized()
            entry.lstat.return_value = original
        for value in ('', 'bad', '0 1000 1\n', '1000 0 2\n', '1000 0 1\n2000 2 1\n'):
            mapping.write_text(value)
            with self.subTest(mapping=value), self.assertRaises(RuntimeError):
                platform.containerized()
        mapping.unlink()
        with self.assertRaises(RuntimeError):
            platform.containerized()

    def test_invalid_contents_fail_closed_and_instance_uses_same_ownership_contract(self):
        marker, _ = self.identity()
        marker.open.return_value.__enter__.return_value.read.return_value = b'not-an-image\n'
        with self.assertRaises(RuntimeError):
            platform.containerized()
        self.patch(platform, 'CONTAINER_INSTANCE', marker)
        marker.open.return_value.__enter__.return_value.read.return_value = b'a' * 32 + b'\n'
        self.assertEqual(platform._container_instance(), 'a' * 32)
        for value in (b'bad', b'a' * 32 + b' ' * 32 + b'x', b'\xff'):
            marker.open.return_value.__enter__.return_value.read.return_value = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform._container_instance()
        marker.lstat.return_value.st_uid = 1000
        with self.assertRaises(ValueError):
            platform._container_instance()

    def test_native_macos_never_reads_linux_namespace_metadata(self):
        marker, _ = self.identity()
        self.patch(platform, '_darwin', return_value=True)
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('Linux metadata read')):
            self.assertTrue(platform.containerized())
            marker.lstat.side_effect = FileNotFoundError
            self.assertFalse(platform.containerized())
            self.identity(65534)
            with self.assertRaises(RuntimeError):
                platform.containerized()


class TestContainerBoundary(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.patch(platform, "containerized", return_value=True)
        self.patch(server, "log")

    def test_all_terminal_update_and_host_voice_routes_refuse_before_socket_inference(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        routes = [("GET", f"/api/terminal/{self.project}"),
                  ("GET", f"/api/terminal/{self.project}/stream?id=x")]
        routes += [("POST", f"/api/terminal/{self.project}/{action}")
                   for action in ("open", "input", "command", "resize", "close")]
        routes += [("POST", path) for path in ("/api/terminal-access", "/api/update", "/api/update-check",
                   "/api/voice/host", "/api/voice/live", "/api/voice/live/x/audio", "/api/voice/live/x/cancel")]
        routes += [("POST", f"/api/devices/{action}") for action in ("share", "share-close")]
        with mock.patch.object(terminal, "agent_connection", side_effect=AssertionError("must not infer origin")):
            for forwarded in (False, True):
                for method, path in routes:
                    with self.subTest(method=method, path=path, forwarded=forwarded):
                        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
                        try:
                            headers = {"Content-Type": "application/json"}
                            if forwarded:
                                headers.update({"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=192.0.2.1"})
                            connection.request(method, path, "{}" if method == "POST" else None, headers)
                            response = connection.getresponse()
                            body = json.loads(response.read())
                            self.assertEqual(response.status, 403, body)
                            self.assertRegex(body["error"], "container|image-managed")
                            if path.startswith("/api/devices/share"):
                                self.assertIn("host container command's certificate action", body["error"])
                        finally:
                            connection.close()

    def test_task_terminal_output_is_unavailable_not_empty(self):
        with mock.patch.object(S, "load_task", side_effect=AssertionError("must refuse before task lookup")):
            with self.assertRaisesRegex(PermissionError, "unavailable"):
                server.owner_terminal_output(self.project, "fictional", 1, (), ())
        self.assertFalse(terminal.enabled())
        self.assertEqual(speech.status()["state"], "unavailable")

    def test_container_https_identity_is_the_advertised_host_not_wildcard_binding(self):
        from tests.test_tls import handshake
        self.patch(config, "HOST", new="0.0.0.0")
        self.patch(config, "PUBLIC_HOST", new="fixture.local")
        self.patch(config, "TLS_DIR", new=self.tmp / "certificates")
        self.patch(config, "PROJECT_ROOTS", new=[self.tmp / "projects"])
        identity = tls.initialize()
        self.assertEqual(identity["host"], "fixture.local")
        self.assertEqual(tls.url(), f"https://fixture.local:{config.PORT}")
        self.assertIn("host container command", tls.info()["trust_steps"][0])
        self.assertNotIn("tls-share", tls.info()["trust_steps"][0])
        context = tls.check()
        self.assertEqual(handshake(context, config.TLS_DIR / "ca.crt", "fixture.local"), b"typed conversation")
        self.assertEqual(handshake(context, config.TLS_DIR / "ca.crt", "localhost"), b"typed conversation")
        with self.assertRaises(Exception):
            handshake(context, config.TLS_DIR / "ca.crt", "wrong.local")

    def test_cli_setting_requests_and_stale_pending_requests_cannot_enable_unavailable_actions(self):
        for setting, value in (("terminal", True), ("update_check", True), ("voice", "host")):
            with self.subTest(setting=setting), self.assertRaisesRegex(T.TransitionError, "container|image-managed"):
                dispatch.request_setting(None, setting, value, "fixture", actor=config.OPERATOR_ACTOR)
            with mock.patch.object(platform, "containerized", return_value=False):
                dispatch.request_setting(None, setting, value, "fixture", actor=config.OPERATOR_ACTOR)
            result = dispatch._run_setting(None, setting)
            self.assertEqual(result["status"], "refused")
            self.assertNotEqual(config.machine_settings().get(setting), value)

    def test_stale_activation_neither_restarts_nor_holds_admission(self):
        self.private_ledgers()
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        S.write_json(flag, {"requested_at": "2020-01-01T00:00:00+00:00", "unit": "fictional"})
        before = flag.read_bytes()
        with mock.patch.object(server, "_request_restart_unit", side_effect=AssertionError("must not launch")), \
             mock.patch.object(dispatch, "_self_deploy_fast_forward", side_effect=AssertionError("must not activate")):
            self.assertFalse(config.restart_in_progress())
            self.assertIsNone(server.restart_status())
            server.auto_restart()
            self.assertEqual(dispatch.self_deploy_fast_forward(self.project), [])
            with self.assertRaisesRegex(RuntimeError, "image-managed"):
                server.restart_service()
        self.assertEqual(flag.read_bytes(), before)

    def test_image_restart_does_not_queue_native_activation_notice_or_lose_task_state(self):
        self.private_ledgers()
        task = T.new(self.project, "Retained task", "Keep the existing review hold", hold_merge="Review required")
        task["state"] = "blocked"
        S.save_task(self.project, task)
        message = T.message(self.project, task["slug"], config.OPERATOR_ACTOR, "Saved context")
        before = S.load_task(self.project, task["slug"])
        server.restart_notice()
        self.assertEqual(l3.queued(self.project), [])
        self.assertEqual(S.load_task(self.project, task["slug"]), before)
        self.assertIn(message["id"], [row["id"] for row in T.pending(self.project, task["slug"])])
        with mock.patch.object(platform, "containerized", return_value=False):
            server.restart_notice()
        self.assertEqual(len(l3.queued(self.project)), 1)

    def test_image_lifecycle_refuses_before_download_record_or_service_mutation(self):
        with mock.patch.object(installation, "_changing_update_record", side_effect=AssertionError("no update record")), \
             mock.patch.object(installation, "latest_release", side_effect=AssertionError("no download")), \
             mock.patch.object(platform, "control", side_effect=AssertionError("no native control")):
            installation.check_for_update()
            self.assertIsNone(installation.update_notice())
            self.assertEqual(installation.update_status()["managed"], "image")
            for operation in (lambda: installation.request_update("v0.1.0"),
                              lambda: installation.update("v0.1.0"), installation.recover, installation.uninstall,
                              lambda: installation.install(self.tmp / "absent", "0" * 64),
                              lambda: source_tls.prepare(self.tmp / "absent", apply=True),
                              lambda: installation.service("restart")):
                with self.subTest(operation=operation), self.assertRaisesRegex((RuntimeError, ValueError), "image-managed"):
                    operation()
