"""Administrator setup planning and transaction behavior in fictional directory trees."""
import base64
from contextlib import ExitStack
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

from scripts import setup_validation_mac as setup


class MacSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "install"
        self.ssh = self.root / "sshd_config"
        self.ssh.write_text("# existing unrelated access\nPasswordAuthentication yes\n")
        self.job = self.root / "guest.plist"
        self.template = self.root / "prepared-template"
        self.template.mkdir()
        for name in ("vm.json", "disk.img", "aux.img", "manifest.json"):
            (self.template / name).write_text("pinned " + name)
        self.key = self.root / "public-key"
        blob = struct.pack("!I", 11) + b"ssh-ed25519" + struct.pack("!I", 32) + b"a" * 32
        self.key.write_text("ssh-ed25519 " + base64.b64encode(blob).decode() + " ignored comment\n")
        self.account = {"name": "validationfixture", "uid": os.getuid(), "gid": os.getgid(), "home": str(self.root / "home")}
        self.template_identity = "b" * 64
        self.files = {name: ("fixture " + name).encode() for name in setup.CODE}
        self.value = {"version": 1, "mode": "host", "account": self.account["name"],
                      "python": str(Path(sys.executable).resolve()), "source_sha256": setup.source_digest(self.files),
                      "template": str(self.template), "template_sha256": self.template_identity,
                      "public_key": str(self.key), "source_network": "192.168.99.2/32"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("BASE", self.base), ("SSHD_CONFIG", self.ssh), ("GUEST_JOB", self.job),
                            ("GUEST_WORK", self.root / "guest-work")):
            self.stack.enter_context(mock.patch.object(setup, name, value))
        self.stack.enter_context(mock.patch.object(setup, "protected"))
        self.stack.enter_context(mock.patch.object(setup, "code_files", return_value=self.files))
        self.stack.enter_context(mock.patch.object(setup.platform, "validation_setup_python"))
        self.stack.enter_context(mock.patch.object(setup.platform, "validation_setup_account", return_value=self.account))
        self.stack.enter_context(mock.patch.object(setup.platform, "_validation_template", return_value=({}, {}, self.template_identity)))
        self.stack.enter_context(mock.patch.object(setup.shutil, "disk_usage", return_value=mock.Mock(free=100 * 1024**3)))
        self.native = self.stack.enter_context(mock.patch.object(setup.platform, "validation_setup_sshd", side_effect=self.ssh_check))
        self.clone = self.stack.enter_context(mock.patch.object(setup.platform, "validation_setup_clone", side_effect=shutil.copyfile))

    def ssh_check(self, configuration, account, address):
        command = shlex.join([self.value["python"], "-I", "-B", str(self.base / "code/scripts/validation_remote.py"), "serve"])
        return setup.account_rules(account, command)[1]

    def apply(self, plan):
        with mock.patch.object(setup.os, "geteuid", return_value=0):
            setup.apply_plan(plan)

    def guest(self):
        candidate = self.root / "candidate"
        (candidate / "web").mkdir(parents=True)
        for name in ("package.json", "pnpm-lock.yaml"):
            (candidate / "web" / name).write_text(name)
        store, browsers = self.root / "store", self.root / "browsers"
        store.mkdir()
        browsers.mkdir()
        return {key: self.value[key] for key in setup.COMMON} | {
            "mode": "guest", "candidate": str(candidate), "store": str(store), "browsers": str(browsers),
            "path": str(Path("/usr/bin").resolve()), "fingerprints": {"web/" + name: setup.platform._validation_digest(candidate / "web" / name)
                                                       for name in ("package.json", "pnpm-lock.yaml")}}

    def test_default_plan_checks_all_inputs_without_writing(self):
        original = self.ssh.read_bytes()
        plan = setup.build_plan(self.value)
        self.assertEqual(plan["mode"], "host")
        self.assertFalse(self.base.exists())
        self.assertFalse(self.job.exists())
        self.assertEqual(self.ssh.read_bytes(), original)
        self.clone.assert_not_called()
        self.native.assert_called_once()
        key = plan["writes"][self.base / "authorized_keys"][0].decode()
        self.assertIn('from="192.168.99.2/32",restrict ssh-ed25519 ', key)
        self.assertNotIn("ignored comment", key)
        rules = plan["writes"][self.base / "sshd.conf"][0].decode()
        for instruction in ("AuthenticationMethods publickey", "DisableForwarding yes", "PermitTTY no", "PermitUserRC no", "ForceCommand "):
            self.assertIn(instruction, rules)
        self.assertNotIn("PermitUserEnvironment", rules)  # global-only: checked, never changed

    def test_apply_installs_captured_source_config_template_and_closed_admission(self):
        original = self.ssh.read_bytes()
        plan = setup.build_plan(self.value)
        self.apply(plan)
        for name, data in self.files.items():
            self.assertEqual((self.base / "code" / name).read_bytes(), data)
        config = json.loads((self.base / "broker.json").read_text())
        self.assertEqual(config["runner_uid"], self.account["uid"])
        self.assertTrue((self.base / "state" / "off").is_file())
        self.assertEqual((self.base / "state").stat().st_mode & 0o777, 0o700)
        self.assertTrue(self.ssh.read_bytes().startswith(original))
        self.assertEqual((self.base / "template/disk.img").read_bytes(), (self.template / "disk.img").read_bytes())
        self.assertEqual(self.clone.call_count, 4)
        self.assertFalse(self.job.exists())

    def test_final_ssh_refusal_restores_original_and_removes_only_new_installation(self):
        plan = setup.build_plan(self.value)
        original = self.ssh.read_bytes()
        neighbor = self.root / "unrelated"
        neighbor.write_text("preserved")
        calls = 0

        def fail_after_apply(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("native effective configuration refused")
            return self.ssh_check(*args)

        self.native.side_effect = fail_after_apply
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual(self.ssh.read_bytes(), original)
        self.assertFalse(self.base.exists())
        self.assertEqual(neighbor.read_text(), "preserved")

    def test_missing_root_source_or_template_approval_causes_no_writes(self):
        for mutation in ({"source_sha256": "0" * 64}, {"template_sha256": "0" * 64}, {"unknown": "value"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                setup.build_plan(self.value | mutation)
            self.assertFalse(self.base.exists())
        plan = setup.build_plan(self.value)
        with mock.patch.object(setup.os, "geteuid", return_value=501), self.assertRaises(ValueError):
            setup.apply_plan(plan)
        self.assertFalse(self.base.exists())

    def test_source_address_cannot_widen_or_use_public_loopback_address(self):
        for source in ("192.168.99.0/24", "8.8.8.8", "127.0.0.1", "::1"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                setup.build_plan(self.value | {"source_network": source})
        self.assertEqual(setup.build_plan(self.value | {"source_network": "100.64.10.20/32"})["mode"], "host")

    def test_ssh_conflicting_rules_refuse_before_write(self):
        self.native.return_value = {}
        self.native.side_effect = None
        with self.assertRaisesRegex(ValueError, "conflict"):
            setup.build_plan(self.value)
        self.assertFalse(self.base.exists())

    def test_private_key_and_path_links_are_refused(self):
        self.key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nprivate fictional bytes\n")
        with self.assertRaisesRegex(ValueError, "public key"):
            setup.build_plan(self.value)
        link = self.root / "linked"
        link.symlink_to(self.template)
        with self.assertRaisesRegex(ValueError, "canonical"):
            setup.canonical(str(link))

    def test_existing_installation_and_staging_file_are_preserved(self):
        self.base.mkdir()
        protected_file = self.base / "preserve"
        protected_file.write_text("existing")
        with self.assertRaisesRegex(ValueError, "already installed"):
            setup.build_plan(self.value)
        self.assertEqual(protected_file.read_text(), "existing")
        staging = self.ssh.with_name(".altitude-validation-new")
        staging.write_text("existing staging")
        with self.assertRaises(FileExistsError):
            setup._replace_ssh(b"new", self.ssh.read_bytes(), 0o644)
        self.assertEqual(staging.read_text(), "existing staging")

    def test_guest_installs_immutable_supervisor_for_next_boot_without_host_ssh_edits(self):
        value = self.guest()
        original = self.ssh.read_bytes()
        plan = setup.build_plan(value)
        self.apply(plan)
        plist = plistlib.loads(self.job.read_bytes())
        self.assertEqual(plist["UserName"], "root")
        self.assertTrue(plist["RunAtLoad"])
        self.assertFalse(plist["KeepAlive"])
        self.assertEqual(plist["ProgramArguments"], [value["python"], "-I", "-B", str(self.base / "code/scripts/validation_guest.py")])
        config = json.loads((self.base / "guest.json").read_text())
        self.assertEqual(config["fingerprints"], value["fingerprints"])
        self.assertEqual(self.ssh.read_bytes(), original)
        self.native.assert_not_called()
        self.clone.assert_not_called()

    def test_guest_changed_dependencies_or_prior_execution_refuse(self):
        value = self.guest()
        changed = copy.deepcopy(value)
        changed["fingerprints"]["web/pnpm-lock.yaml"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "dependency identity"):
            setup.build_plan(changed)
        setup.GUEST_WORK.mkdir()
        with self.assertRaisesRegex(ValueError, "previous validation"):
            setup.build_plan(value)
        self.assertFalse(self.base.exists())

    def test_cli_plan_output_contains_no_private_inputs(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.value))
        path.chmod(0o600)
        output = io.StringIO()
        with mock.patch.object(setup.sys, "stdout", output):
            self.assertEqual(setup.main(["--plan", str(path)]), 0)
        self.assertNotIn("192.168.99.2", output.getvalue())
        self.assertNotIn(str(self.key), output.getvalue())
        self.assertNotIn(self.key.read_text().split()[1], output.getvalue())
        self.assertFalse(self.base.exists())

    def test_changed_inputs_after_preview_are_refused_before_apply(self):
        plan = setup.build_plan(self.value)
        self.files["scripts/validation_remote.py"] += b"changed"
        with self.assertRaisesRegex(ValueError, "source identity"):
            self.apply(plan)
        self.assertFalse(self.base.exists())

    def test_native_account_missing_gui_or_template_clone_failure_never_enables_admission(self):
        with mock.patch.object(setup.platform, "validation_setup_account", side_effect=RuntimeError("GUI unavailable")):
            with self.assertRaises(RuntimeError):
                setup.build_plan(self.value)
        self.assertFalse(self.base.exists())
        plan = setup.build_plan(self.value)
        original = self.ssh.read_bytes()
        self.clone.side_effect = RuntimeError("clone unavailable")
        with self.assertRaises(RuntimeError):
            self.apply(plan)
        self.assertFalse(self.base.exists())
        self.assertEqual(self.ssh.read_bytes(), original)


class MacSetupNativeSeamTests(unittest.TestCase):
    def test_ssh_configuration_is_validated_on_stdin_without_service_actions(self):
        with (mock.patch.object(setup.platform, "_darwin", return_value=True),
              mock.patch.object(setup.platform.subprocess, "run", return_value=mock.Mock(stdout="passwordauthentication no\n")) as run):
            actual = setup.platform.validation_setup_sshd("fictional config", "fixture", "192.168.99.2")
        self.assertEqual(actual, {"passwordauthentication": "no"})
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/sbin/sshd", "-f", "/dev/stdin", "-t"])
        self.assertEqual(run.call_args_list[1].args[0][:4], ["/usr/sbin/sshd", "-f", "/dev/stdin", "-T"])
        self.assertTrue(all(call.kwargs["input"] == "fictional config" for call in run.call_args_list))

    def test_template_clone_never_launches_vm(self):
        with (mock.patch.object(setup.platform, "_darwin", return_value=True),
              mock.patch.object(setup.platform.subprocess, "run") as run):
            setup.platform.validation_setup_clone(Path("/prepared/disk.img"), Path("/installed/disk.img"))
        self.assertEqual(run.call_args.args[0], ["/bin/cp", "-c", "/prepared/disk.img", "/installed/disk.img"])

    def test_account_setup_only_checks_existing_standard_account_gui(self):
        account = mock.Mock(pw_uid=501, pw_gid=20, pw_dir="/Users/fictional")
        with (mock.patch.object(setup.platform, "_darwin", return_value=True),
              mock.patch.object(setup.platform.host_platform, "machine", return_value="arm64"),
              mock.patch.object(setup.platform, "_validation_standard_account", return_value=account),
              mock.patch.object(setup.platform.subprocess, "run") as run):
            result = setup.platform.validation_setup_account("fictional")
        self.assertEqual(result["uid"], 501)
        self.assertEqual(run.call_args.args[0], ["/bin/launchctl", "print", "gui/501"])


if __name__ == "__main__":
    unittest.main()
