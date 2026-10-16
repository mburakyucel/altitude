"""Address changes keep real certificates and receipts; native service effects use a throwaway home."""
import ipaddress
import json
import plistlib
import os
import io
from pathlib import Path
import sys
from unittest import mock

from tests.test_installation import InstallationCase
from altitude import config, installation, platform, tls

TLS_INFO, TLS_INITIALIZE = tls.info, tls.initialize


class AddressConfiguration(InstallationCase):
    def setUp(self):
        super().setUp()
        for target, name, replacement in ((tls, "initialize", TLS_INITIALIZE), (tls, "info", TLS_INFO),
                                         (platform, "service_address", lambda host: str(ipaddress.ip_address(host)))):
            patcher = mock.patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.install()
        self.actions.clear()
        self.original = {path: path.read_bytes() for path in (self.settings, self.unit, *self.certificates.iterdir()) if path.is_file()}

    def assert_original(self):
        for path, contents in self.original.items():
            self.assertEqual(path.read_bytes(), contents, path.name)

    def test_preview_is_read_only_and_apply_preserves_ca_keys_port_and_stopped_state(self):
        preview = installation.configure_address("10.20.30.40")
        self.assertFalse(preview["applied"])
        self.assert_original()
        self.assertEqual(self.actions, [])
        self.assertEqual(preview["url"], "https://10.20.30.40:19443")
        self.assertIn("--apply", preview["apply"])
        installation.service("stop")
        self.actions.clear()
        result = installation.configure_address("10.20.30.40", apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["service"], "stopped")
        self.assertFalse(result["verified"])
        self.assertEqual(self.actions, ["stop", "reload"])
        for name in ("ca.crt", "ca.key", "server.key"):
            path = self.certificates / name
            self.assertEqual(path.read_bytes(), self.original[path])
        tls._verify(self.certificates, "10.20.30.40")
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved["environment"]["ALTITUDE_PORT"], "19443")
        self.assertEqual(saved["environment"]["ALTITUDE_HOST"], "10.20.30.40")
        self.assertFalse((self.prefix / "pending.json").exists())

    def test_ipv6_preview_and_stopped_apply_keep_a_certified_bracketed_url(self):
        preview = installation.configure_address("::1")
        self.assertEqual(preview["url"], "https://[::1]:19443")
        self.assert_original()
        installation.service("stop")
        self.actions.clear()
        result = installation.configure_address("::1", apply=True)
        self.assertEqual(result["service"], "stopped")
        self.assertEqual(self.actions, ["stop", "reload"])
        self.assertEqual(json.loads(self.settings.read_text())["environment"]["ALTITUDE_HOST"], "::1")
        tls._verify(self.certificates, "::1")

    def test_running_change_uses_explicit_new_network_and_rolls_back_failed_health(self):
        self.probes = [RuntimeError("candidate HTTPS failed"), None]
        with self.assertRaisesRegex(RuntimeError, "previous address restored"):
            installation.configure_address("10.20.30.40", apply=True)
        self.assert_original()
        self.assertEqual(self.active, "active")
        self.assertFalse((self.prefix / "pending.json").exists())
        with mock.patch.object(installation, "_probe", wraps=self.probe) as probe:
            result = installation.configure_address("10.20.30.40", apply=True)
        self.assertTrue(result["verified"])
        self.assertEqual(probe.call_args.kwargs["network"]["host"], "10.20.30.40")

    def test_transitional_and_unknown_states_refuse_before_any_mutation(self):
        for state in ("activating", "deactivating", "reloading", "unknown"):
            with self.subTest(state=state):
                self.active = state
                with self.assertRaisesRegex(RuntimeError, "settled service status"):
                    installation.configure_address("10.20.30.40", apply=True)
                self.assert_original()
                self.assertEqual(self.actions, [])
                self.assertFalse((self.prefix / "pending.json").exists())

    def test_interruption_after_each_write_and_native_boundary_restores_original(self):
        atomic, control = installation.atomic, platform.control
        for boundary in ("receipt", "stop", "leaf", "settings", "unit", "reload", "start", "probe"):
            with self.subTest(boundary=boundary):
                fired = False
                def write(path, *args, **kwargs):
                    nonlocal fired
                    atomic(path, *args, **kwargs)
                    kind = {self.prefix / "pending.json": "receipt", self.certificates / "server.crt": "leaf",
                            self.settings: "settings", self.unit: "unit"}.get(path)
                    if not fired and kind == boundary:
                        fired = True
                        raise KeyboardInterrupt("simulated process loss")
                def native(operation):
                    nonlocal fired
                    result = control(operation)
                    if not fired and operation == boundary:
                        fired = True
                        raise KeyboardInterrupt("simulated process loss")
                    return result
                def health(*args, **kwargs):
                    if boundary == "probe":
                        raise KeyboardInterrupt("simulated process loss")
                    self.probe(*args, **kwargs)
                with mock.patch.object(installation, "atomic", side_effect=write), \
                     mock.patch.object(platform, "control", side_effect=native), \
                     mock.patch.object(installation, "_probe", side_effect=health):
                    with self.assertRaises(KeyboardInterrupt):
                        installation.configure_address("10.20.30.40", apply=True)
                self.assertTrue((self.prefix / "pending.json").exists())
                # A fresh CLI loads whichever saved configuration the interrupted process reached.
                host = json.loads(self.settings.read_text())["environment"]["ALTITUDE_HOST"]
                with mock.patch.object(config, "HOST", host):
                    result = installation.recover()
                self.assertTrue(result["recovered"])
                self.assert_original()
                self.assertEqual(self.active, "active")

    def test_changed_ownership_retains_receipt_and_files_for_inspection(self):
        with mock.patch.object(installation, "_probe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                installation.configure_address("10.20.30.40", apply=True)
        self.unit.write_text("foreign service\n")
        with self.assertRaisesRegex(RuntimeError, "ownership changed"):
            installation._recover(self.prefix)
        self.assertEqual(self.unit.read_text(), "foreign service\n")
        self.assertTrue((self.prefix / "pending.json").exists())

    def test_external_identity_is_retained_and_must_already_cover_new_address(self):
        (self.certificates / tls._MARKER).unlink()
        (self.certificates / "ca.key").unlink()
        before = (self.certificates / "server.crt").read_bytes()
        with self.assertRaises(tls.TLSFailure):
            installation.configure_address("10.20.30.40")
        self.assertEqual((self.certificates / "server.crt").read_bytes(), before)
        self.assertEqual(self.actions, [])


class MacAddressConfiguration(AddressConfiguration):
    HOST = "darwin"

    def test_inactive_or_failed_bootstrapped_job_is_unloaded_before_mutation_and_later_start_reads_new_definition(self):
        for state in ("inactive", "failed"):
            with self.subTest(state=state):
                self.active = state
                loaded = {"coalition": None, "environment": {"ALTITUDE_HOST": config.HOST}}
                native_commands = []
                def run(*args):
                    nonlocal loaded
                    native_commands.append(args[1])
                    if args[1] == "bootout":
                        loaded = None
                    elif args[1] == "bootstrap":
                        loaded = {"coalition": None, "environment": plistlib.loads(self.unit.read_bytes())["EnvironmentVariables"]}
                    return ""
                with mock.patch.object(platform, "control", side_effect=platform._launchd_control), \
                     mock.patch.object(platform, "_print", side_effect=lambda *args, **kwargs: loaded), \
                     mock.patch.object(platform, "run", side_effect=run):
                    result = installation.configure_address("10.20.30.40", apply=True)
                    self.assertEqual(result["service"], "stopped")
                    self.assertEqual(native_commands, ["bootout"])
                    self.assertIsNone(loaded)
                    platform.control("start")
                    self.assertEqual(native_commands, ["bootout", "bootstrap"])
                    self.assertEqual(loaded["environment"]["ALTITUDE_HOST"], "10.20.30.40")
                # Restore the fixture for the other original native state, through the same operator action.
                with mock.patch.object(config, "HOST", "10.20.30.40"):
                    installation.configure_address("127.0.0.1", apply=True)


class CacheRecovery(InstallationCase):
    """The standalone installer's repair for an installation on v0.8.0 or older, whose own update code refuses
    generated caches; this release's own checks accept them."""

    def setUp(self):
        super().setUp()
        self.install()
        self.actions.clear()
        self.root = self.prefix / "current"
        self.cache = self.root / f"altitude/__pycache__/config.{sys.implementation.cache_tag}.pyc"
        self.cache.parent.mkdir()
        self.cache.write_bytes(b"generated bytecode")

    def test_explicit_repair_quarantines_only_caches_and_keeps_strict_validation_and_state(self):
        original = self.retained_data()
        with self.assertRaisesRegex(ValueError, "manifest"):
            installation.metadata(self.root.resolve(), archive=True)  # as an installation on v0.8.0 or older checks it
        result = installation.recover(generated_bytecode=True)
        self.assertTrue(result["repaired"])
        self.assertFalse(self.cache.exists())
        self.assertEqual((Path(result["backup"]) / result["quarantined"][0]).read_bytes(), b"generated bytecode")
        self.assertEqual(installation.metadata(self.root.resolve())["version"], "v0.1.0")
        self.assertEqual(self.actions, [])
        self.assertEqual({path: path.read_bytes() for path in original}, original)
        self.assertIn("manual repair", result["next"])
        # A review or coordinator turn that starts before the retry re-creates the caches (issue 912).
        self.assertIn("Run alt update now", result["next"])

    def test_changed_source_non_cache_and_foreign_unit_refuse_without_moving_anything(self):
        for fault in ("resource", "extra", "unit"):
            with self.subTest(fault=fault):
                path = {"resource": self.root / "altitude/config.py", "extra": self.root / "unknown.txt", "unit": self.unit}[fault]
                before = path.read_bytes() if path.exists() else None
                path.write_text("foreign contents\n")
                with self.assertRaises((ValueError, RuntimeError)):
                    installation.recover(generated_bytecode=True)
                self.assertTrue(self.cache.exists())
                self.assertFalse((self.prefix / "recovery").exists())
                if before is None:
                    path.unlink()
                else:
                    path.write_bytes(before)

    def test_interrupted_quarantine_retains_backup_and_explicit_retry_finishes_without_activation(self):
        original = self.retained_data()
        other = self.cache.with_name(f"__init__.{sys.implementation.cache_tag}.pyc")
        other.write_bytes(b"second generated cache")
        moving = os.replace
        cache_source = self.cache.resolve()
        def interrupted(source, destination):
            if Path(source) == cache_source:
                raise OSError("fictional interrupted second cache move")
            return moving(source, destination)
        with mock.patch.object(os, "replace", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                installation.recover(generated_bytecode=True)
        backup = next((self.prefix / "recovery").iterdir())
        receipt = json.loads((backup / "recovery.json").read_text())
        self.assertFalse(receipt["complete"])
        self.assertEqual((backup / other.relative_to(self.root)).read_bytes(), b"second generated cache")
        self.assertTrue(self.cache.exists())
        with self.assertRaisesRegex(ValueError, "manifest"):
            installation.metadata(self.root.resolve(), archive=True)  # as an installation on v0.8.0 or older checks it
        recovered = installation.recover(generated_bytecode=True)
        self.assertTrue(recovered["repaired"])
        self.assertEqual(recovered["quarantined"], [str(self.cache.relative_to(self.root))])
        self.assertTrue(json.loads((Path(recovered["backup"]) / "recovery.json").read_text())["complete"])
        self.assertEqual(installation.metadata(self.root.resolve())["version"], "v0.1.0")
        self.assertEqual({path: path.read_bytes() for path in original}, original)
        self.assertEqual(self.actions, [])
        self.assertFalse((self.prefix / "pending.json").exists())
        self.assertFalse(installation.recover(generated_bytecode=True)["repaired"])

    def test_archive_repair_cli_verifies_checksum_before_quarantine_and_never_activates_archive(self):
        archive, checksum = self.archive("v0.2.0")
        retained = self.retained_data()
        for trusted in ("0" * 64, checksum):
            with mock.patch.object(sys, "argv", ["install.py", "--archive", str(archive), "--sha256", trusted,
                                               "--prefix", str(self.prefix), "--repair-generated-bytecode"]), \
                    mock.patch.object(sys, "stdout", new_callable=io.StringIO) as output, \
                    mock.patch.object(sys, "stderr", new_callable=io.StringIO) as error:
                if trusted != checksum:
                    with self.assertRaises(SystemExit) as exited:
                        installation.main()
                    self.assertEqual(exited.exception.code, 1)
                    self.assertIn("checksum mismatch", error.getvalue())
                    self.assertTrue(self.cache.exists())
                    self.assertFalse((self.prefix / "recovery").exists())
                else:
                    installation.main()
                    repaired = json.loads(output.getvalue())
                    self.assertTrue(repaired["repaired"])
                    self.assertEqual(repaired["version"], "v0.1.0")
        self.assertFalse((self.prefix / "versions/v0.2.0").exists())
        self.assertEqual(installation.metadata(self.root.resolve())["version"], "v0.1.0")
        self.assertEqual(self.actions, [])
        self.assertEqual({path: path.read_bytes() for path in retained}, retained)
