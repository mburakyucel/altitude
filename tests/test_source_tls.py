"""Source TLS preparation with real isolated HTTPS and disposable native/process evidence."""
import json
import fcntl
import os
from pathlib import Path
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, platform, source_tls, tls


class TestSourceTLS(AltitudeCase):
    host = "linux"  # systemd fixtures: the source service is a systemd unit

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.setenv("ALTITUDE_ACTOR", None)
        self.patch(config, "HOST", "127.0.0.1")
        self.directory = self.tmp / "existing-certificates"
        self.patch(config, "TLS_DIR", self.directory)
        tls.initialize()
        # Existing source certificates predate managed certificate rotation.
        (self.directory / tls._MARKER).unlink()
        self.patch(config, "RELEASE", {"repository": "example/altitude"})
        self.unit = self.tmp / "user-units/altitude.service"
        self.unit.parent.mkdir()
        self.unit.parent.chmod(0o700)
        self.unit.write_text("[Service]\nWorkingDirectory=" + str(self.repo) + "\n")
        self.unit.chmod(0o644)
        self.override = self.unit.parent / "altitude.service.d" / source_tls._NAME
        self.patch(platform, "service_path", return_value=self.unit)
        self.patch(platform, "require_supported")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.httpd.socket = tls.check().wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_port
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.httpd.shutdown)
        self.environment = {"ALTITUDE_HOST": "127.0.0.1", "ALTITUDE_PORT": str(self.port), "ALTITUDE_SERVICE": "1"}
        self.native = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "Type": "simple",
                       "MainPID": "1234", "FragmentPath": str(self.unit), "WorkingDirectory": str(self.repo),
                       "EnvironmentFiles": "", "PassEnvironment": "", "UnsetEnvironment": "", "DropInPaths": "",
                       "ExecStart": f"{{ path={self.repo}/bin/alt ; argv[]={self.repo}/bin/alt serve ; ignore_errors=no ; }}",
                       "InvocationID": "source-fixture", "ExecMainStartTimestampMonotonic": "1000",
                       "NeedDaemonReload": "no", "RootDirectory": "", "RootImage": ""}
        self._loaded_environment()
        self.controls = []
        self.fail_reload = 0
        self.patch(platform, "run", side_effect=self.run_native)
        self.patch(platform, "control", side_effect=self.control)
        self.proc = self.tmp / "proc"
        self.process = self.proc / "1234"
        (self.process / "fd").mkdir(parents=True)
        (self.proc / "net").mkdir()
        (self.process / "cwd").symlink_to(self.repo, target_is_directory=True)
        (self.process / "cmdline").write_bytes(f"python3\0{self.repo}/bin/alt\0serve\0".encode())
        (self.process / "environ").write_bytes(
            ("\0".join(f"{key}={value}" for key, value in self.environment.items())
             + "\0UNRELATED_SECRET=never-return-this-value\0").encode())
        (self.process / "fd/7").symlink_to("socket:[4242]")
        (self.proc / "net/tcp").write_text(
            "header\n" + f"0: 0100007F:{self.port:04X} 00000000:0000 0A 0:0 00:0 0 1000 0 4242\n")
        self.patch(source_tls, "_PROC", self.proc)

    def _loaded_environment(self):
        self.native["Environment"] = " ".join(shlex.quote(f"{key}={value}") for key, value in self.environment.items())

    def run_native(self, *args):
        if args[:4] == ("systemctl", "--user", "show", "altitude.service"):
            return "\n".join(f"{key}={value}" for key, value in self.native.items())
        if args[0] == "git" and "ls-files" in args:
            return "bin/alt\naltitude/server.py\naltitude/config.py\n"
        if args[0] == "git" and args[-3:] == ("remote", "get-url", "origin"):
            return "https://github.com/example/altitude\n"
        raise AssertionError(f"Unexpected native operation: {args[0]}")

    def control(self, action):
        self.assertEqual(action, "reload", "Preparation cannot perform a service lifecycle operation")
        self.controls.append(action)
        if self.fail_reload:
            self.fail_reload -= 1
            raise RuntimeError("fixture daemon-reload failure")
        if self.override.exists():
            value = json.loads(self.override.read_text()[len(source_tls._HEADER):]).replace("%%", "%")
            self.environment["ALTITUDE_TLS_DIR"] = value.split("=", 1)[1]
            self.native["DropInPaths"] = shlex.quote(str(self.override))
        else:
            self.environment.pop("ALTITUDE_TLS_DIR", None)
            self.native["DropInPaths"] = ""
        self._loaded_environment()

    def snapshot(self):
        return {str(path.relative_to(self.tmp)): path.read_bytes()
                for path in self.tmp.rglob("*") if path.is_file() and not path.is_symlink()}

    def test_check_only_binds_process_listener_and_leaf_without_writes_or_secret_output(self):
        before = self.snapshot()
        result = source_tls.prepare(self.directory)
        self.assertTrue(result["verified"])
        self.assertFalse(result["applied"])
        self.assertEqual((result["host"], result["port"], result["pid"]), ("127.0.0.1", self.port, 1234))
        self.assertIn('Environment="ALTITUDE_TLS_DIR=', result["override"])
        self.assertNotIn("UNRELATED_SECRET", json.dumps(result))
        self.assertNotIn("never-return-this-value", json.dumps(result))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controls, [])

    def test_apply_changes_only_owned_override_and_reloads_without_restarting(self):
        before = self.snapshot()
        result = source_tls.prepare(self.directory, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["loaded_tls_dir"], str(self.directory))
        self.assertEqual(self.override.read_bytes(), source_tls._override(self.directory))
        after = self.snapshot()
        after.pop(str(self.override.relative_to(self.tmp)))
        self.assertEqual(after, before)
        self.assertEqual(self.controls, ["reload"])
        self.assertEqual(source_tls.prepare(self.directory, apply=True)["pid"], 1234)
        self.assertEqual(self.controls, ["reload"], "Already verified preparation needs no second reload")

    def test_python_exec_start_shape_is_supported(self):
        self.native["ExecStart"] = (f"{{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 {self.repo}/bin/alt serve ; "
                                    "ignore_errors=no ; }")
        self.assertTrue(source_tls.prepare(self.directory)["verified"])

    def test_reload_resets_command_history_without_changing_process_identity(self):
        command = self.native["ExecStart"].removesuffix(" }")
        self.native["ExecStart"] = command + " start_time=[Mon 2026-09-14 12:00:00 UTC] ; stop_time=[n/a] ; pid=1234 ; code=(null) ; status=0/0 }"
        control = self.control

        def reload(action):
            control(action)
            # systemd/systemd#12375: command history can reset on daemon-reload.
            self.native["ExecStart"] = command + " start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"

        with mock.patch.object(platform, "control", side_effect=reload):
            result = source_tls.prepare(self.directory, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(self.controls, ["reload"])

    def test_rollback_accepts_reset_command_history_but_preserves_original_failure(self):
        command = self.native["ExecStart"].removesuffix(" }")
        self.native["ExecStart"] = command + " start_time=[Mon 2026-09-14 12:00:00 UTC] ; stop_time=[n/a] ; pid=1234 ; code=(null) ; status=0/0 }"
        control = self.control

        def reload(action):
            control(action)
            self.native["ExecStart"] = command + " start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
            if len(self.controls) == 1:
                raise RuntimeError("simulated reload acknowledgement failure")

        with mock.patch.object(platform, "control", side_effect=reload):
            with self.assertRaisesRegex(RuntimeError, "owned override restored and reload verified: simulated reload"):
                source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, ["reload", "reload"])

    def test_actual_identity_and_command_policy_changes_remain_uncertain(self):
        for key, value in (("InvocationID", "replacement-invocation"),
                           ("ExecMainStartTimestampMonotonic", "2000"),
                           ("ExecStart", self.native["ExecStart"].replace("ignore_errors=no", "ignore_errors=yes"))):
            with self.subTest(key=key):
                previous = self.native[key]
                self.controls.clear()
                control = self.control

                def reload(action):
                    control(action)
                    self.native[key] = value

                with mock.patch.object(platform, "control", side_effect=reload):
                    with self.assertRaisesRegex(RuntimeError, "restoration/reload is uncertain"):
                        source_tls.prepare(self.directory, apply=True)
                self.assertFalse(self.override.exists())
                self.assertEqual(self.controls, ["reload", "reload"])
                self.native[key] = previous

    def test_command_change_after_reload_is_not_hidden_by_metadata_normalization(self):
        control = self.control

        def reload(action):
            control(action)
            self.native["ExecStart"] = (f"{{ path=/usr/bin/python3.12 ; argv[]=/usr/bin/python3.12 {self.repo}/bin/alt serve ; "
                                        "ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }")

        with mock.patch.object(platform, "control", side_effect=reload):
            with self.assertRaisesRegex(RuntimeError, "restoration/reload is uncertain:.*ExecStart"):
                source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, ["reload", "reload"])

    def test_unknown_command_metadata_refuses_before_writing(self):
        self.native["ExecStart"] = self.native["ExecStart"].replace(" ; }", " ; unknown_flag=yes ; }")
        with self.assertRaisesRegex(RuntimeError, "Unsupported source command metadata"):
            source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, [])

    def test_mismatch_diagnostics_include_both_failures_without_environment_values(self):
        control = self.control

        def reload(action):
            control(action)
            self.environment["TOKEN_SECRET"] = "fictional-sensitive-value"
            self._loaded_environment()

        with mock.patch.object(platform, "control", side_effect=reload):
            with self.assertRaises(RuntimeError) as raised:
                source_tls.prepare(self.directory, apply=True)
        message = str(raised.exception)
        self.assertIn("Source verification changed: other loaded environment", message)
        self.assertIn("Restored source settings differ: other loaded environment", message)
        self.assertNotIn("TOKEN_SECRET", message)
        self.assertNotIn("fictional-sensitive-value", message)
        self.assertFalse(self.override.exists())

    def test_reload_failure_restores_owned_override_and_reports_verified_rollback(self):
        self.fail_reload = 1
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "owned override restored and reload verified"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, ["reload", "reload"])
        self.assertTrue(source_tls.prepare(self.directory, apply=True)["applied"])

    def test_failed_restoration_reload_is_reported_as_uncertain(self):
        self.fail_reload = 2
        with self.assertRaisesRegex(RuntimeError, "restoration/reload is uncertain"):
            source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, ["reload", "reload"])

    def test_foreign_override_and_foreign_dropin_refuse_without_changes(self):
        self.override.parent.mkdir()
        self.override.parent.chmod(0o700)
        self.override.write_text("[Service]\nEnvironment=ALTITUDE_TLS_DIR=/foreign\n")
        self.override.chmod(0o600)
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "foreign or customized"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.override.unlink()
        other = self.override.parent / "other.conf"
        other.write_text("[Service]\nEnvironment=ALTITUDE_PORT=9999\n")
        with self.assertRaisesRegex(RuntimeError, "Foreign source-unit drop-ins"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.controls, [])

    def test_different_selected_certificate_refuses_even_when_same_ca_is_trusted(self):
        with mock.patch.object(tls, "RENEW_SECONDS", 366 * 86400):
            # Force only an on-disk leaf replacement; the fixture listener retains the old leaf.
            tls._renew(self.directory, "127.0.0.1")
        with self.assertRaisesRegex(RuntimeError, "does not match.*served HTTPS identity"):
            source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, [])

    def test_changed_pid_after_reload_restores_file_but_reports_process_uncertainty(self):
        control = self.control

        def changed(action):
            control(action)
            self.native["MainPID"] = "4321"

        with mock.patch.object(platform, "control", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "restoration/reload is uncertain"):
                source_tls.prepare(self.directory, apply=True)
        self.assertFalse(self.override.exists())
        self.assertEqual(self.controls, ["reload", "reload"])

    def test_concurrent_foreign_override_is_never_removed_by_rollback(self):
        def changed(action):
            self.assertEqual(action, "reload")
            self.override.write_text("[Service]\nEnvironment=FOREIGN=preserve\n")
            raise RuntimeError("reload failed with concurrent edit")

        with mock.patch.object(platform, "control", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "restoration/reload is uncertain"):
                source_tls.prepare(self.directory, apply=True)
        self.assertIn("FOREIGN=preserve", self.override.read_text())

    def test_missing_process_visibility_or_wrong_listener_owner_refuses(self):
        (self.process / "fd/7").unlink()
        with self.assertRaisesRegex(RuntimeError, "does not own"):
            source_tls.prepare(self.directory)
        (self.process / "cmdline").unlink()
        with self.assertRaisesRegex(RuntimeError, "process visibility is required"):
            source_tls.prepare(self.directory)
        self.assertEqual(self.controls, [])

    def test_effective_binding_drift_and_ambiguous_loaded_settings_refuse(self):
        original = (self.process / "environ").read_bytes()
        (self.process / "environ").write_bytes(original.replace(b"ALTITUDE_HOST=127.0.0.1", b"ALTITUDE_HOST=127.0.0.2"))
        with self.assertRaisesRegex(RuntimeError, "Running binding differs"):
            source_tls.prepare(self.directory)
        (self.process / "environ").write_bytes(original)
        for key, value in (("EnvironmentFiles", "/other/file (ignore_errors=no)"), ("NeedDaemonReload", "yes")):
            with self.subTest(key=key), mock.patch.dict(self.native, {key: value}):
                with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    source_tls.prepare(self.directory)

    def test_worker_roles_are_denied_before_inspection(self):
        for actor in ("l2", "l3"):
            with self.subTest(actor=actor), mock.patch.dict(os.environ, {"ALTITUDE_ACTOR": actor}), \
                 mock.patch.object(source_tls, "_native") as inspect:
                with self.assertRaisesRegex(RuntimeError, "operator operation"):
                    source_tls.prepare(self.directory, apply=True)
                inspect.assert_not_called()

    def test_simultaneous_apply_refuses_while_check_only_stays_read_only(self):
        before = self.snapshot()
        with self.unit.open("rb") as other_preparation:
            fcntl.flock(other_preparation, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another source TLS preparation"):
                source_tls.prepare(self.directory, apply=True)
            self.assertTrue(source_tls.prepare(self.directory)["verified"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controls, [])

    def test_managed_identity_is_explicitly_outside_external_source_preparation(self):
        (self.directory / tls._MARKER).write_text("1\n")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "existing external certificates, not a managed"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controls, [])

    def test_interrupted_override_before_reload_refuses_with_explicit_recovery_guidance(self):
        self.override.parent.mkdir(mode=0o700)
        self.override.write_bytes(source_tls._override(self.directory))
        self.override.chmod(0o600)
        self.native["NeedDaemonReload"] = "yes"
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Review pending unit edits.*daemon-reload.*check-only"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controls, [])
        # The operator reviews the interrupted write and explicitly reloads definitions.
        self.native["NeedDaemonReload"] = "no"
        self.control("reload")
        self.assertTrue(source_tls.prepare(self.directory, apply=True)["applied"])
        self.assertEqual(self.controls, ["reload"])

    def test_shared_writable_unit_directory_and_symlink_unit_are_not_owned(self):
        self.unit.parent.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, "unit directories"):
            source_tls.prepare(self.directory, apply=True)
        self.unit.parent.chmod(0o700)
        target = self.unit.with_name("actual.service")
        self.unit.rename(target)
        self.unit.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "regular files owned"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.controls, [])

    def test_kernel_listener_at_another_address_refuses_even_with_matching_environment(self):
        table = self.proc / "net/tcp"
        table.write_text(table.read_text().replace("0100007F", "0200007F"))
        with self.assertRaisesRegex(RuntimeError, "does not own the configured listening address"):
            source_tls.prepare(self.directory, apply=True)
        self.assertEqual(self.controls, [])

    def test_symlink_or_shared_writable_override_directory_refuses_without_writes(self):
        target = self.tmp / "foreign-overrides"
        target.mkdir(mode=0o700)
        for kind in ("symlink", "shared-write"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.override.parent.symlink_to(target, target_is_directory=True)
                else:
                    self.override.parent.mkdir(mode=0o700)
                    self.override.parent.chmod(0o777)
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, "unit directories"):
                    source_tls.prepare(self.directory, apply=True)
                self.assertEqual(self.snapshot(), before)
                if kind == "symlink":
                    self.assertEqual(self.override.parent.readlink(), target)
                    self.override.parent.unlink()
                else:
                    self.override.parent.rmdir()
        self.assertEqual(self.controls, [])
