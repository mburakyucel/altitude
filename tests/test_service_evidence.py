"""Read-only TLS recovery evidence uses fictional native-service snapshots (#362)."""
import json
import socket
import subprocess
from types import SimpleNamespace
from unittest import mock

from tests.support import AltitudeCase
from altitude import engines, server


class TestServiceEvidence(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.register(self.project, path=self.repo)
        self.owned = self.tmp / ".config/systemd/user/altitude.service.d/90-altitude-source-tls.conf"
        self.patch(engines.Path, "home", return_value=self.tmp)
        self.native = {
            "ActiveState": "active", "SubState": "running", "MainPID": "99",
            "ActiveEnterTimestamp": "Tue 2026-09-15 10:00:00 UTC", "LoadState": "loaded",
            "InvocationID": "a" * 32, "ExecMainStartTimestampMonotonic": "123456789",
            "Environment": 'ALTITUDE_TLS=1 "ALTITUDE_TLS_DIR=/fictional/TLS identity=one" TOKEN=SECRET',
            "PassEnvironment": "", "UnsetEnvironment": "",
            "DropInPaths": "", "NeedDaemonReload": "no",
        }

    def read(self, values=None, **result):
        native = self.native if values is None else values
        completed = SimpleNamespace(returncode=0, stderr="", stdout="".join(
            f"{key}={value}\n" for key, value in native.items()))
        completed.__dict__.update(result)
        with mock.patch.object(engines.subprocess, "run", return_value=completed) as run:
            record = engines.service_status()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:4], [engines.SYSTEMCTL_BIN, "--user", "show", "altitude.service"])
        return record

    def test_restored_loaded_selection_and_process_continuity_are_separate_evidence(self):
        restored = self.read()
        self.assertIsNone(restored["error"])
        self.assertEqual(restored["loaded_tls_environment"], {
            "ALTITUDE_TLS": "1", "ALTITUDE_TLS_DIR": "/fictional/TLS identity=one"})
        self.assertIs(restored["indirect_environment"], False)
        self.assertIs(restored["owned_tls_drop_in_present"], False)
        self.assertIs(restored["owned_tls_drop_in_loaded"], False)
        self.assertIs(restored["need_daemon_reload"], False)
        self.assertEqual((restored["pid"], restored["invocation_id"], restored["started_monotonic"]),
                         (99, "a" * 32, "123456789"))

        self.owned.parent.mkdir(parents=True)
        self.owned.write_text("SECRET: contents must never be returned")
        changed = self.native | {"DropInPaths": str(self.owned),
                                  "Environment": "ALTITUDE_TLS=1 ALTITUDE_TLS_DIR=/fictional/changed"}
        loaded = self.read(changed)
        self.assertIs(loaded["owned_tls_drop_in_present"], True)
        self.assertIs(loaded["owned_tls_drop_in_loaded"], True)
        self.assertNotEqual(loaded["loaded_tls_environment"], restored["loaded_tls_environment"])

        self.owned.unlink()
        stale = self.read(changed | {"NeedDaemonReload": "yes"})
        self.assertIs(stale["owned_tls_drop_in_present"], False)
        self.assertIs(stale["owned_tls_drop_in_loaded"], True)
        self.assertIs(stale["need_daemon_reload"], True)
        self.assertEqual(stale["loaded_tls_environment"], loaded["loaded_tls_environment"])
        for key in ("pid", "last_restart", "invocation_id", "started_monotonic"):
            self.assertEqual(stale[key], restored[key], "process continuity does not prove configuration restoration")
        self.assertNotIn("SECRET", json.dumps([restored, loaded, stale]))

    def test_unset_empty_disabled_and_indirect_environment_have_distinct_meanings(self):
        self.assertEqual(self.read(self.native | {"Environment": ""})["loaded_tls_environment"], {})
        self.assertEqual(self.read(self.native | {"Environment": "ALTITUDE_TLS=0 ALTITUDE_TLS_DIR="})[
            "loaded_tls_environment"], {"ALTITUDE_TLS": "0", "ALTITUDE_TLS_DIR": ""})
        for key, value in {"EnvironmentFiles": "/fictional/SECRET.env (ignore_errors=no)",
                           "PassEnvironment": "SECRET", "UnsetEnvironment": "SECRET"}.items():
            with self.subTest(key=key):
                record = self.read(self.native | {key: value})
                self.assertIs(record["indirect_environment"], True)
                self.assertNotIn("SECRET", json.dumps(record))

    def test_missing_native_properties_are_unknown_not_empty(self):
        for native, field in (("Environment", "loaded_tls_environment"),
                              ("PassEnvironment", "indirect_environment"),
                              ("UnsetEnvironment", "indirect_environment"),
                              ("DropInPaths", "owned_tls_drop_in_loaded"),
                              ("NeedDaemonReload", "need_daemon_reload"),
                              ("LoadState", "loaded_tls_environment")):
            with self.subTest(native=native):
                values = self.native.copy()
                del values[native]
                record = self.read(values)
                self.assertIsNone(record[field])
                self.assertTrue(record["error"])
                self.assertEqual(record["pid"], 99)
        for native, field in (("InvocationID", "invocation_id"),
                              ("ExecMainStartTimestampMonotonic", "started_monotonic")):
            record = self.read(self.native | {native: "SECRET"})
            self.assertIsNone(record[field])
            self.assertNotIn("SECRET", json.dumps(record))

    def test_native_empty_environment_files_omission_preserves_tls_evidence(self):
        # systemctl-show's struct-array formatter emits no line for an empty EnvironmentFiles.
        self.assertNotIn("EnvironmentFiles", self.native)
        omitted = self.read()
        explicit = self.read(self.native | {"EnvironmentFiles": ""})
        self.assertEqual(omitted, explicit)
        self.assertIsNone(omitted["error"])
        self.assertIs(omitted["indirect_environment"], False)
        self.assertEqual(omitted["loaded_tls_environment"]["ALTITUDE_TLS_DIR"], "/fictional/TLS identity=one")
        failed = self.read(returncode=1)
        self.assertIsNone(failed["indirect_environment"])
        self.assertIsNone(failed["loaded_tls_environment"])

    def test_ambiguous_or_escaped_environment_never_becomes_a_guessed_selection(self):
        for raw in ('"ALTITUDE_TLS_DIR=/fictional/unterminated SECRET',
                    'ALTITUDE_TLS_DIR=/one ALTITUDE_TLS_DIR=/two TOKEN=SECRET',
                    r'"ALTITUDE_TLS_DIR=/fictional/escaped\x20name" TOKEN=SECRET',
                    'ALTITUDE_TLS=1 SECRET', 'ALTITUDE_TLS_DIR=/fictional/\tcontrol TOKEN=SECRET',
                    'ALTITUDE_TLS_DIR=/' + 'x' * 4096 + ' TOKEN=SECRET'):
            with self.subTest(raw=raw[:50]):
                record = self.read(self.native | {"Environment": raw})
                self.assertIsNone(record["loaded_tls_environment"])
                self.assertTrue(record["error"])
                self.assertNotIn("SECRET", json.dumps(record))
        for property_, value, field in (("NeedDaemonReload", "unknown", "need_daemon_reload"),
                                         ("DropInPaths", '"SECRET', "owned_tls_drop_in_loaded"),
                                         ("DropInPaths", r"/SECRET\x20name", "owned_tls_drop_in_loaded"),
                                         ("LoadState", "not-found", "loaded_tls_environment")):
            record = self.read(self.native | {property_: value})
            self.assertIsNone(record[field])
            self.assertTrue(record["error"])
            self.assertNotIn("SECRET", json.dumps(record))

    def test_owned_drop_in_requires_exact_path_and_unreadable_disk_is_unknown(self):
        record = self.read(self.native | {"DropInPaths": "/other/90-altitude-source-tls.conf"})
        self.assertIs(record["owned_tls_drop_in_loaded"], False)
        with mock.patch.object(engines.Path, "lstat", side_effect=PermissionError("SECRET")):
            record = self.read()
        self.assertIsNone(record["owned_tls_drop_in_present"])
        self.assertTrue(record["error"])
        self.assertNotIn("SECRET", json.dumps(record))

    def test_native_failures_do_not_disclose_diagnostics_or_infer_absence(self):
        record = self.read(returncode=1, stdout="Environment=TOKEN=SECRET", stderr="SECRET")
        self.assertIsNone(record["pid"])
        self.assertIsNone(record["owned_tls_drop_in_present"])
        self.assertIsNone(record["loaded_tls_environment"])
        self.assertNotIn("SECRET", json.dumps(record))
        for error in (OSError("SECRET"), subprocess.TimeoutExpired("SECRET", 15, output="SECRET")):
            with mock.patch.object(engines.subprocess, "run", side_effect=error):
                record = engines.service_status()
            self.assertTrue(record["error"])
            self.assertNotIn("SECRET", json.dumps(record))

    def test_real_coordinator_broker_returns_filtered_evidence_with_fixed_authority(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        result = SimpleNamespace(returncode=0, stderr="", stdout="".join(
            f"{key}={value}\n" for key, value in self.native.items()))

        def request(payload):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(str(broker.server_address))
                client.sendall((json.dumps(payload) + "\n").encode())
                client.shutdown(socket.SHUT_WR)
                return json.loads(client.makefile().read())

        with mock.patch.object(engines.subprocess, "run", return_value=result) as run:
            record = request({"kind": "service", "unit": "altitude.service", "properties": ["Environment"],
                              "args": ["restart", "foreign.service"], "path": "/SECRET"})
            command = run.call_args.args[0]
            self.assertEqual(command[:4], [engines.SYSTEMCTL_BIN, "--user", "show", "altitude.service"])
            self.assertNotIn("restart", command)
            self.assertEqual(record["loaded_tls_environment"]["ALTITUDE_TLS_DIR"], "/fictional/TLS identity=one")
            self.assertNotIn("TOKEN", json.dumps(record))
            self.assertNotIn("SECRET", json.dumps(record))
            self.assertIsNone(record["error"])
            for unit in ("foreign.service", "restart altitude", "../altitude.service"):
                self.assertTrue(request({"kind": "service", "unit": unit})["error"])
            self.assertEqual(run.call_count, 1)
            worker = request({"kind": "service", "unit": "altitude-worker-fixture.service"})
            self.assertNotIn("loaded_tls_environment", worker)
            self.assertNotIn("--property=Environment", run.call_args.args[0])
            alias = request({"kind": "service", "unit": "altitude"})
            self.assertEqual(alias["loaded_tls_environment"], record["loaded_tls_environment"])
