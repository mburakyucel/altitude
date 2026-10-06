"""Native VM boundary contracts using filesystem fixtures; no hypervisor or host service runs."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from altitude import platform


class ValidationVMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        self.area = self.root / "run"
        self.area.mkdir()
        (self.area / "input").mkdir()
        self.tool = self.root / "macosvm"
        self.tool.write_bytes(b"fixture VM executable")
        self.vm = {"version": 1, "hardwareModel": "model", "machineId": "machine", "cpus": 4,
                   "ram": 8 * 1024**3, "networks": [], "audio": False,
                   "displays": [{"dpi": 120, "width": 1280, "height": 800}],
                   "storage": [{"type": "disk", "file": "disk.img", "readOnly": False},
                               {"type": "aux", "file": "aux.img", "readOnly": False}]}
        (self.template / "disk.img").write_bytes(b"guest disk")
        (self.template / "aux.img").write_bytes(b"guest auxiliary image")
        self.write_template()

    def write_template(self):
        (self.template / "vm.json").write_text(json.dumps(self.vm))
        self.manifest = {"version": 1, "files": {name: platform._validation_digest(self.template / name)
                                                for name in ("vm.json", "disk.img", "aux.img")},
                         "executable": str(self.tool), "executable_sha256": platform._validation_digest(self.tool)}
        (self.template / "manifest.json").write_text(json.dumps(self.manifest))

    def prepare(self):
        def native(argv, **kwargs):
            if argv[0] == "/bin/cp":
                shutil.copyfile(argv[-2], argv[-1])
            elif argv[1] == "create":
                Path(argv[-1]).touch()
            return subprocess.CompletedProcess(argv, 0)

        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform.host_platform, "machine", return_value="arm64"),
              mock.patch.object(platform.os, "geteuid", return_value=501),
              mock.patch.object(platform, "_validation_standard_account"),
              mock.patch.object(platform, "validation_trusted_file"),
              mock.patch.object(platform, "validation_host_identity", return_value={"os": "macos"}),
              mock.patch.object(platform.shutil, "disk_usage", return_value=mock.Mock(free=100 * 1024**3)),
              mock.patch.object(platform.subprocess, "run", side_effect=native) as calls):
            result = platform.validation_vm_prepare(self.template, self.area)
        return result, [call.args[0] for call in calls.call_args_list]

    def test_clone_has_only_bounded_disks_and_two_shares_without_network(self):
        result, calls = self.prepare()
        config = json.loads((self.area / "vm" / "vm.json").read_text())
        self.assertEqual(config["networks"], [])
        self.assertFalse(config["audio"])
        self.assertFalse(config["serial"])
        self.assertEqual(config["shares"], [
            {"path": str(self.area / "input"), "volume": "altitude-input", "readOnly": True},
            {"path": str(self.area / "results"), "volume": "altitude-results", "readOnly": False}])
        self.assertEqual(result["argv"], [str(self.tool), "--no-gui", "--no-audio", "--no-serial", str(self.area / "vm" / "vm.json")])
        self.assertEqual(result["template"], platform._validation_digest(self.template / "manifest.json"))
        self.assertIn(["/usr/bin/hdiutil", "create", "-size", "512m", "-fs", "APFS", "-type", "SPARSE",
                       "-volname", "AltitudeValidation", str(self.area / "results.sparseimage")], calls)
        (self.area / "vm" / "disk.img").write_bytes(b"candidate modification")
        self.assertEqual((self.template / "disk.img").read_bytes(), b"guest disk")

    def test_template_refuses_network_extra_storage_shares_flags_and_resource_overflow(self):
        baseline = json.loads(json.dumps(self.vm))
        for key, value in [("networks", [{"type": "nat"}]), ("shares", [{"path": "/"}]),
                           ("spice", True), ("cpus", 5), ("ram", 9 * 1024**3), ("audio", True),
                           ("storage", [{"type": "disk", "file": "/private-data", "readOnly": False}])]:
            with self.subTest(key=key):
                self.vm = {**baseline, key: value}
                self.write_template()
                with mock.patch.object(platform, "validation_trusted_file"), self.assertRaises(RuntimeError):
                    platform._validation_template(self.template)

    def test_changed_template_or_executable_refuses_before_launch(self):
        for file in (self.template / "disk.img", self.tool):
            with self.subTest(file=file.name):
                original = file.read_bytes()
                file.write_bytes(b"changed")
                with mock.patch.object(platform, "validation_trusted_file"), self.assertRaisesRegex(RuntimeError, "fingerprint mismatch"):
                    platform._validation_template(self.template)
                file.write_bytes(original)

    def test_worker_owned_or_symlinked_template_refuses(self):
        path = self.root / "unsafe"
        path.write_text("x")
        path.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, "root-owned"):
            platform.validation_trusted_file(path)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaisesRegex(RuntimeError, "root-owned"):
            platform.validation_trusted_file(link)

    def test_non_mac_executor_is_explicitly_unavailable(self):
        with mock.patch.object(platform, "_darwin", return_value=False), self.assertRaisesRegex(RuntimeError, "Apple-silicon"):
            platform.validation_vm_prepare(self.template, self.area)

    def test_cleanup_detaches_only_own_image_and_preserves_input_and_result_directory(self):
        (self.area / "results.sparseimage").touch()
        (self.area / "vm").mkdir()
        (self.area / "results").mkdir()
        data = {"images": [
            {"image-path": "/another-run/results.sparseimage", "system-entities": [{"dev-entry": "/dev/disk7"}]},
            {"image-path": str(self.area / "results.sparseimage"), "system-entities": [{"dev-entry": "/dev/disk8"}]}]}
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform.subprocess, "check_output", return_value=plistlib.dumps(data)),
              mock.patch.object(platform.subprocess, "run") as call):
            platform.validation_vm_cleanup(self.area)
        self.assertEqual(call.call_args.args[0], ["/usr/bin/hdiutil", "detach", "/dev/disk8"])
        self.assertFalse((self.area / "vm").exists())
        self.assertFalse((self.area / "results.sparseimage").exists())
        self.assertTrue((self.area / "input").exists())
        self.assertTrue((self.area / "results").exists())

    def test_failed_detach_keeps_image_and_clone_for_recovery(self):
        (self.area / "results.sparseimage").touch()
        (self.area / "vm").mkdir()
        data = {"images": [{"image-path": str(self.area / "results.sparseimage"),
                            "system-entities": [{"dev-entry": "/dev/disk8"}]}]}
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform.subprocess, "check_output", return_value=plistlib.dumps(data)),
              mock.patch.object(platform.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "detach")),
              self.assertRaises(subprocess.CalledProcessError)):
            platform.validation_vm_cleanup(self.area)
        self.assertTrue((self.area / "vm").exists())
        self.assertTrue((self.area / "results.sparseimage").exists())

    def test_continuous_clock_uses_native_timebase(self):
        library = mock.Mock()
        library.mach_continuous_time.return_value = 9_000_000_000

        def timebase(pointer):
            values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
            values[0], values[1] = 2, 3
            return 0

        library.mach_timebase_info.side_effect = timebase
        with mock.patch.object(platform, "_darwin", return_value=True), mock.patch.object(platform.ctypes, "CDLL", return_value=library):
            self.assertEqual(platform.validation_deadline_clock(), 6.0)

    def test_broker_job_uses_existing_supervision_without_preventing_sleep(self):
        ident = "a" * 32
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform, "_login_env", return_value={"HOME": str(self.root)}),
              mock.patch.object(platform.subprocess, "run") as native):
            platform.validation_broker_launch(["/trusted/python", "/trusted/broker.py", "work"], ident, self.area)
        args = native.call_args.args[0]
        spec = json.loads(args[-1])
        self.assertEqual(spec["mode"], "detached")
        self.assertEqual(spec["runtime_max"], 3600)
        self.assertTrue(spec["allow_idle_sleep"])
        self.assertEqual(spec["command"], ["/trusted/python", "/trusted/broker.py", "work", ident])
        self.assertEqual(spec["label"], "dev.altitude.job.altitude-validation-mac-" + ident)

    def test_broker_stop_requires_confirmed_termination(self):
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform, "job_active", side_effect=[True, True]),
              mock.patch.object(platform, "job_stop") as stop,
              self.assertRaisesRegex(RuntimeError, "unconfirmed")):
            platform.validation_broker_stop("a" * 32)
        stop.assert_called_once()
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform, "job_active", side_effect=RuntimeError("status unknown")),
              self.assertRaisesRegex(RuntimeError, "unknown")):
            platform.validation_broker_active("a" * 32)

    def test_guest_mounts_are_root_mapped_and_input_is_read_only(self):
        input_path, results_path = self.root / "guest-input", self.root / "guest-results"
        with (mock.patch.object(platform, "_darwin", return_value=True),
              mock.patch.object(platform.os, "geteuid", return_value=0),
              mock.patch.object(Path, "stat", return_value=mock.Mock(st_uid=0, st_mode=0o40755)),
              mock.patch.object(platform.subprocess, "run") as native):
            platform.validation_guest_mounts(input_path, results_path)
        commands = [call.args[0] for call in native.call_args_list]
        self.assertEqual(commands[0], ["/sbin/mount_virtiofs", "-r", "-u", "0", "-g", "0", "altitude-input", str(input_path)])
        self.assertEqual(commands[1], ["/sbin/mount_virtiofs", "-u", "0", "-g", "0", "altitude-results", str(results_path)])


if __name__ == "__main__":
    unittest.main()
