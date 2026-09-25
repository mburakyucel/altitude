"""Real private archives and lifecycle state; native service effects stay in fixtures."""
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import REPO, SUITE
from altitude import config, installation, platform, state as S, tls


class Installation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-", dir=SUITE))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.prefix = self.home / ".local/share/altitude"
        self.settings = self.home / ".config/altitude/install.json"
        self.unit = self.home / ".config/systemd/user/altitude.service"
        self.launcher = self.home / ".local/bin/alt"
        self.runtime = self.home / ".altitude"
        self.certificates = self.home / ".config/altitude/tls"
        self.actions = []
        self.active = "inactive"
        self.enabled = "disabled"
        self.failures = {}
        self.probes = []
        for target, name, value in (
            (Path, "home", mock.Mock(return_value=self.home)),
            (config, "ROOT", self.runtime),
            (config, "PROJECTS_FILE", self.runtime / "projects.json"),
            (config, "MONITOR_DIR", self.runtime / "monitor"),
            (config, "PROJECT_ROOTS", [self.home / "Projects"]),
            (config, "TLS_DIR", self.certificates),
            (config, "HOST", "127.0.0.1"),
            (config, "PORT", 19443),
            (config, "INSTALL_PREFIX", self.prefix),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {"ALTITUDE_CONFIG": str(self.settings)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, name, value in (
            (platform, "control", self.control), (platform, "status", self.status),
            (tls, "initialize", self.initialize_tls), (tls, "info", lambda: {"trust": "fixture"}),
            (installation, "_probe", self.probe),
        ):
            patcher = mock.patch.object(target, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.ensure_root()

    def status(self):
        return {"LoadState": "loaded" if self.unit.exists() else "not-found", "ActiveState": self.active,
                "SubState": "running" if self.active == "active" else "dead",
                "UnitFileState": self.enabled, "FragmentPath": str(self.unit) if self.unit.exists() else "",
                "MainPID": "1234" if self.active == "active" else "0"}

    def control(self, action):
        self.actions.append(action)
        if self.failures.get(action):
            self.failures[action] -= 1
            raise RuntimeError(f"fixture {action} failure")
        if action in ("start", "restart"):
            self.active = "active"
        elif action == "stop":
            self.active = "inactive"
        elif action == "enable":
            self.enabled = "enabled"
        elif action == "disable":
            self.enabled = "disabled"
        return ""

    def initialize_tls(self):
        self.certificates.mkdir(parents=True, exist_ok=True)
        for filename in ("ca.crt", "ca.key", "server.crt", "server.key"):
            path = self.certificates / filename
            if not path.exists():
                path.write_text("retained fixture certificate material\n")
        return {"trust": "fixture"}

    def probe(self, version, **kwargs):
        if self.probes:
            result = self.probes.pop(0)
            if isinstance(result, Exception):
                raise result
        self.assertEqual(self.active, "active")
        self.assertEqual(json.loads((self.prefix / "current/release.json").read_text())["version"], version)

    def archive(self, version="v0.1.0", *, edited=False):
        folder = self.tmp / f"bundle-{len(list(self.tmp.glob('bundle-*')))}"
        folder.mkdir()
        for directory in ("altitude", "bin", "hooks", "personas", "schemas", "templates"):
            shutil.copytree(REPO / directory, folder / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (folder / "web/dist/assets").mkdir(parents=True)
        (folder / "web/dist/index.html").write_text('<html><script src="/assets/app.js"></script></html>')
        (folder / "web/dist/assets/app.js").write_text("console.log('fixture');\n")
        if edited:
            (folder / "personas/l2.md").write_text("Reviewed next application version\n")
        release = {"version": version, "commit": ("b" if edited else "a") * 40,
                   "repository": "example/altitude", "files": {
                       str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in folder.rglob("*") if path.is_file()}}
        (folder / "release.json").write_text(json.dumps(release))
        archive = folder.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(folder)), recursive=False)
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def install(self, version="v0.1.0", *, edited=False):
        archive, checksum = self.archive(version, edited=edited)
        return installation.install(archive, checksum, self.prefix)

    def retained_data(self):
        paths = [self.runtime / "history.jsonl", self.home / "Projects/demo/.claude/worktrees/task/draft.txt",
                 self.home / ".provider/sessions/session.json"]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fictional retained owner data\n")
        return {path: path.read_bytes() for path in paths}

    def test_install_update_and_uninstall_keep_settings_trust_and_user_data(self):
        retained = self.retained_data()
        first = self.install()
        self.assertEqual(first["version"], "v0.1.0")
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.0")
        self.assertFalse((self.prefix / "current/.git").exists())
        self.assertTrue(os.access(self.launcher, os.X_OK))
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.enabled, "enabled")
        settings = self.settings.read_bytes()
        certificate = (self.certificates / "ca.key").read_bytes()
        updated = self.install("v0.1.1", edited=True)
        self.assertEqual(updated["version"], "v0.1.1")
        for version in ("v0.1.0", "v0.1.1"):
            self.assertEqual(installation.metadata(self.prefix / "versions" / version)["version"], version)
        self.assertEqual(self.settings.read_bytes(), settings)
        self.assertEqual((self.certificates / "ca.key").read_bytes(), certificate)
        self.assertFalse((self.prefix / "pending.json").exists())
        result = installation.uninstall()
        self.assertTrue(result["uninstalled"])
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.launcher.exists())
        self.assertEqual(self.settings.read_bytes(), settings)
        self.assertEqual((self.certificates / "ca.key").read_bytes(), certificate)
        for path, data in retained.items():
            self.assertEqual(path.read_bytes(), data)

    def test_install_persists_application_choices_without_worker_authority(self):
        with mock.patch.dict(os.environ, {"ALTITUDE_OPERATOR": "Trial user", "ALTITUDE_TASK": "unrelated-task"}):
            self.install()
        saved = json.loads(self.settings.read_text())["environment"]
        self.assertEqual(saved["ALTITUDE_OPERATOR"], "Trial user")
        self.assertEqual(saved["ALTITUDE_ROOTS"], str(self.home / "Projects"))
        self.assertNotIn("ALTITUDE_TASK", saved)

    def test_install_preserves_discovered_custom_nvm_tools_in_clean_service_environment(self):
        from tests.test_toolchain import nvm_fixture

        with mock.patch.dict(os.environ, {"ALTITUDE_TASK": "transient-task", "ALTITUDE_SESSION_KEY": "transient-session"}):
            node_bin = nvm_fixture(SimpleNamespace(tmp=self.tmp, setenv=os.environ.__setitem__))
            engine = node_bin / "fixture-engine"
            engine.write_text("#!/bin/sh\necho provider invocation forbidden >&2\nexit 86\n")
            engine.chmod(0o755)
            os.environ["CODEX_BIN"] = "fixture-engine"
            profile = self.tmp / "profile-must-not-run"
            profile.write_text("exit 99\n")
            os.environ["BASH_ENV"] = str(profile)
            before = dict(os.environ)
            self.assertEqual(shutil.which("node", path=config.subprocess_env()["PATH"]), str(node_bin / "node"))
            self.install()
            self.assertEqual(dict(os.environ), before)

        saved = json.loads(self.settings.read_text())["environment"]
        self.assertEqual(saved["PATH"], str(node_bin) + os.pathsep + before["PATH"])
        for name in ("NVM_DIR", "BASH_ENV", "ALTITUDE_TASK", "ALTITUDE_SESSION_KEY"):
            self.assertNotIn(name, saved)
        settings_before, unit_before = self.settings.read_bytes(), self.unit.read_bytes()
        next_bin = node_bin.parents[1] / "v24.22.0/bin"
        shutil.copytree(node_bin, next_bin)
        (next_bin / "node").write_text("#!/bin/sh\nprintf 'v24.22.0\\n'\n")
        manager = node_bin.parents[3] / "nvm.sh"
        manager.write_text(manager.read_text().replace("v24.21.0", "v24.22.0"))
        with mock.patch.dict(os.environ, before, clear=True):
            self.assertEqual(shutil.which("node", path=config.subprocess_env()["PATH"]), str(next_bin / "node"))
            self.install("v0.1.1", edited=True)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(self.unit.read_bytes(), unit_before)
        # Reproduce the generated service's environment without a login shell or manager root.
        environment = {"HOME": str(self.home), "PATH": before["PATH"]}
        for line in self.unit.read_text().splitlines():
            if line.startswith("Environment="):
                key, value = shlex.split(line.partition("=")[2])[0].split("=", 1)
                environment[key] = value
        self.assertEqual(environment["PATH"], saved["PATH"])
        self.assertNotIn("NVM_DIR", environment)
        for command, expected in (("node", "v24.21.0"), ("pnpm", "10.34.5")):
            self.assertEqual(subprocess.check_output([command, "--version"], env=environment, text=True).strip(), expected)
        checked = subprocess.run([str(self.launcher), "doctor"], cwd=self.tmp, env=environment,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        reported = json.loads(checked.stdout)
        fixture = next(row for row in reported["engines"] if row["name"] == config.ENGINE_LABELS["codex"])
        self.assertIsNone(fixture["available"], "discovered engine stays installed; account access remains unknown")
        trust = reported["certificate_trust"]  # the fixture identity is not a real key pair
        self.assertEqual(trust["state"], "unavailable")
        self.assertIn("key mode 600", trust["detail"])
        self.assertNotIn("provider invocation forbidden", checked.stderr)

    def test_installed_lifecycle_rejects_conflicting_shell_runtime_before_native_effects(self):
        self.install()
        other = self.tmp / "wrong-runtime"
        before = (self.prefix / "current").resolve()
        environment = {**os.environ, "HOME": str(self.home), "ALTITUDE_HOME": str(other),
                       "ALTITUDE_CONFIG": str(self.settings)}
        for key in ("ALTITUDE_ACTOR", "ALTITUDE_TASK", "ALTITUDE_PROJECT", "PYTHONPATH"):
            environment.pop(key, None)
        for operation in (["recover"], ["uninstall"], ["service", "stop"], ["update"]):
            if operation[0] == "update":
                archive, checksum = self.archive("v0.1.1")
                operation = ["update", "--archive", str(archive), "--sha256", checksum]
            result = subprocess.run([str(self.launcher), *operation], cwd=self.tmp, env=environment,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Lifecycle settings differ", result.stderr)
            self.assertNotIn("offline tests", result.stderr)
        self.assertFalse(other.exists())
        self.assertEqual((self.prefix / "current").resolve(), before)
        self.assertEqual(self.active, "active")

    def test_failed_update_restores_previous_unit_version_and_active_service(self):
        self.install()
        unit = self.unit.read_bytes()
        settings = self.settings.read_bytes()
        self.probes = [RuntimeError("candidate API unavailable"), None]
        with self.assertRaisesRegex(RuntimeError, "previous installation restored"):
            self.install("v0.1.1", edited=True)
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.0")
        self.assertEqual(self.unit.read_bytes(), unit)
        self.assertEqual(self.settings.read_bytes(), settings)
        self.assertEqual(self.active, "active")
        self.assertFalse((self.prefix / "pending.json").exists())
        self.assertTrue((self.prefix / "versions/v0.1.1").is_dir())

    def test_failed_first_install_recovers_without_removing_config_or_trust(self):
        self.probes = [RuntimeError("candidate API unavailable")]
        with self.assertRaisesRegex(RuntimeError, "previous installation restored"):
            self.install()
        self.assertFalse((self.prefix / "current").exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.launcher.exists())
        self.assertEqual(self.active, "inactive")
        self.assertEqual(self.enabled, "disabled")
        self.assertTrue(self.settings.is_file())
        self.assertTrue((self.certificates / "ca.crt").is_file())
        self.assertFalse((self.prefix / "pending.json").exists())

    def test_failed_recovery_keeps_receipt_until_explicit_retry_succeeds(self):
        self.install()
        self.failures["stop"] = 1
        self.probes = [RuntimeError("candidate API unavailable")]
        with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            self.install("v0.1.1", edited=True)
        receipt = self.prefix / "pending.json"
        self.assertTrue(receipt.exists())
        with self.assertRaisesRegex(RuntimeError, "Interrupted activation"):
            self.install("v0.1.2", edited=True)
        recovered = installation.recover(self.prefix)
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["version"], "v0.1.0")
        self.assertFalse(receipt.exists())
        self.assertEqual(self.active, "active")

    def test_failed_update_preserves_preexisting_disabled_service(self):
        self.install()
        self.active = "inactive"
        self.enabled = "disabled"
        self.failures["reload"] = 1
        with self.assertRaisesRegex(RuntimeError, "previous installation restored"):
            self.install("v0.1.1", edited=True)
        self.assertEqual((self.active, self.enabled), ("inactive", "disabled"))

    def test_successful_update_does_not_start_or_enable_a_stopped_installation(self):
        self.install()
        self.active = "inactive"
        self.enabled = "disabled"
        self.actions.clear()
        self.install("v0.1.1", edited=True)
        self.assertEqual((self.active, self.enabled), ("inactive", "disabled"))
        self.assertNotIn("restart", self.actions)
        self.assertNotIn("enable", self.actions)

    def test_service_controls_verify_state_and_refuse_pending_recovery(self):
        self.install()
        self.assertEqual(installation.service("stop")["ActiveState"], "inactive")
        self.assertEqual(installation.service("start")["ActiveState"], "active")
        (self.prefix / "pending.json").write_text('{}\n')
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "installation recovery"):
            installation.service("stop")
        self.assertEqual(self.actions, [])

    def test_version_contents_are_immutable(self):
        self.install()
        before = (self.prefix / "current/personas/l2.md").read_bytes()
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            self.install(edited=True)
        self.assertEqual((self.prefix / "current/personas/l2.md").read_bytes(), before)
        self.assertEqual(self.actions, [])

    def test_existing_custom_service_and_launcher_are_preserved_before_native_mutation(self):
        self.unit.parent.mkdir(parents=True)
        custom = "[Service]\nExecStart=/usr/bin/other-service\n"
        self.unit.write_text(custom)
        with self.assertRaisesRegex(RuntimeError, "another installation"):
            self.install()
        self.assertEqual(self.unit.read_text(), custom)
        self.assertEqual(self.actions, [])
        self.unit.unlink()
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\nexec other-command\n")
        with self.assertRaisesRegex(RuntimeError, "another installation"):
            self.install()
        self.assertEqual(self.actions, [])
        self.assertIn("other-command", self.launcher.read_text())

    def test_initial_prefix_and_customized_installed_hooks_are_not_overwritten(self):
        self.prefix.mkdir(parents=True)
        existing = self.prefix / "unrelated.txt"
        existing.write_text("unrelated data")
        with self.assertRaisesRegex(RuntimeError, "empty application prefix"):
            self.install()
        self.assertEqual(existing.read_text(), "unrelated data")
        self.assertEqual(self.actions, [])
        existing.unlink()
        self.install()
        hook = self.prefix / "hooks/pre-commit"
        hook.write_text("custom integration")
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "wrapper was customized"):
            self.install("v0.1.1")
        self.assertEqual(hook.read_text(), "custom integration")
        self.assertEqual(self.actions, [])

    def test_merely_mentioning_installation_prefix_does_not_claim_custom_resources(self):
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text(f"# Example path: {self.prefix}/current/bin/alt\n[Service]\nExecStart=/usr/bin/other\n")
        with self.assertRaisesRegex(RuntimeError, "another installation"):
            self.install()
        self.assertEqual(self.actions, [])
        self.unit.unlink()
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text(f"#!/bin/sh\n# Other app data: {self.prefix}\nexec other-command\n")
        with self.assertRaisesRegex(RuntimeError, "another installation"):
            self.install()
        self.assertEqual(self.actions, [])

    def test_recovery_refuses_changed_unit_before_stopping_it(self):
        self.install()
        self.failures["stop"] = 1
        self.probes = [RuntimeError("candidate API unavailable")]
        with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            self.install("v0.1.1", edited=True)
        self.unit.write_text("[Service]\nExecStart=/usr/bin/other-service\n")
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "ownership changed"):
            installation.recover(self.prefix)
        self.assertEqual(self.actions, [])
        self.assertTrue((self.prefix / "pending.json").exists())

    def test_recovery_waits_for_confirmed_stop_before_restoring_previous_version(self):
        self.install()
        self.failures["stop"] = 1
        self.probes = [RuntimeError("candidate API unavailable")]
        with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            self.install("v0.1.1", edited=True)
        current = (self.prefix / "current").resolve()
        with mock.patch.object(platform, "control", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "stop.*unconfirmed|still.*active"):
                installation.recover(self.prefix)
        self.assertEqual((self.prefix / "current").resolve(), current)
        self.assertTrue((self.prefix / "pending.json").exists())

    def test_uninstall_refuses_changed_service_before_stopping_it(self):
        self.install()
        self.unit.write_text("[Service]\nExecStart=/usr/bin/other-service\n")
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "ownership|another installation"):
            installation.uninstall()
        self.assertEqual(self.actions, [])
        self.assertEqual(self.active, "active")

    def test_missing_local_unit_does_not_authorize_stopping_another_loaded_service(self):
        self.install()
        self.unit.unlink()
        self.actions.clear()
        native = {**self.status(), "LoadState": "loaded", "FragmentPath": "/other/altitude.service"}
        with mock.patch.object(platform, "status", return_value=native):
            with self.assertRaisesRegex(RuntimeError, "another|ownership"):
                installation.uninstall()
        self.assertEqual(self.actions, [])

    def test_uninstall_refuses_unconfirmed_stop_and_retains_application(self):
        self.install()
        actual = self.control

        def ineffective_stop(action):
            if action == "stop":
                self.actions.append(action)
                return ""
            return actual(action)

        with mock.patch.object(platform, "control", side_effect=ineffective_stop):
            with self.assertRaisesRegex(RuntimeError, "stop is unconfirmed"):
                installation.uninstall()
        self.assertTrue(self.unit.exists())
        self.assertTrue(self.launcher.exists())
        self.assertTrue((self.prefix / "current").exists())

    def test_registered_project_keeps_hook_resources_and_blocked_owner_prevents_uninstall(self):
        self.install()
        project = self.home / "Projects/demo"
        project.mkdir(parents=True)
        config.save_projects({"demo": {"path": str(project)}})
        S.save_task("demo", {"slug": "owned-task", "state": "blocked", "agent_id": "fixture-worker",
                             "session_id": "fixture-session", "hold_merge": "Operator review"})
        self.actions.clear()
        with self.assertRaisesRegex(RuntimeError, "tasks still own worker inputs"):
            installation.uninstall()
        self.assertEqual(self.actions, [])
        task = S.load_task("demo", "owned-task")
        task["state"] = "done"
        S.save_task("demo", task)
        result = installation.uninstall()
        self.assertTrue(result["application_retained_for_project_hooks"])
        self.assertTrue((self.prefix / "current/hooks/pre-commit").is_file())
        self.assertEqual(S.load_task("demo", "owned-task")["session_id"], "fixture-session")

    def test_overlapping_runtime_prefix_refuses_before_service_mutation(self):
        archive, checksum = self.archive()
        with self.assertRaisesRegex(RuntimeError, "separate from runtime"):
            installation.install(archive, checksum, self.runtime / "application")
        self.assertEqual(self.actions, [])

    def test_checksum_failure_preserves_installed_current(self):
        self.install()
        archive, _ = self.archive("v0.1.1")
        self.actions.clear()
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            installation.install(archive, "0" * 64, self.prefix)
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.0")
        self.assertEqual(self.actions, [])

    def test_standalone_installer_imports_verified_package_before_native_unavailability(self):
        archive, checksum = self.archive()
        installer = self.tmp / "install.py"
        shutil.copyfile(REPO / "altitude/installation.py", installer)
        environment = {**os.environ, "HOME": str(self.home), "ALTITUDE_HOME": str(self.runtime),
                       "ALTITUDE_ROOTS": str(self.home / "Projects"), "ALTITUDE_CONFIG": str(self.settings),
                       "ALTITUDE_TLS_DIR": str(self.certificates), "ALTITUDE_HOST": "127.0.0.1",
                       "ALTITUDE_PORT": "19443"}
        for key in ("ALTITUDE_ACTOR", "ALTITUDE_TASK", "ALTITUDE_PROJECT", "PYTHONPATH"):
            environment.pop(key, None)
        result = subprocess.run([sys.executable, "-B", str(installer), "--archive", str(archive),
                                 "--sha256", checksum, "--prefix", str(self.prefix)],
                                cwd=self.tmp, env=environment, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Native user service failed", result.stderr)
        self.assertNotIn("ImportError", result.stderr)
        root = self.prefix / "versions/v0.1.0"
        self.assertFalse(root.exists(), "unavailable native service must reject before installing application code")
        self.assertFalse((self.prefix / "current").exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.settings.exists())

    def test_tampered_manifest_resource_and_extra_file_are_rejected(self):
        archive, checksum = self.archive()
        target = self.tmp / "extracted"
        target.mkdir()
        installation.extract(archive, checksum, target)
        resource = target / "personas/l2.md"
        original = resource.read_bytes()
        resource.write_text("modified without manifest update")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            installation.metadata(target)
        resource.write_bytes(original)
        (target / "unexpected.py").write_text("unlisted code")
        with self.assertRaisesRegex(ValueError, "do not match"):
            installation.metadata(target)

    def test_archive_traversal_symlinks_hardlinks_and_duplicate_entries_are_rejected(self):
        for name, kind, duplicate in (("../escape", tarfile.REGTYPE, False),
                                      ("/absolute", tarfile.REGTYPE, False),
                                      ("symlink", tarfile.SYMTYPE, False),
                                      ("hardlink", tarfile.LNKTYPE, False),
                                      ("duplicate", tarfile.REGTYPE, True)):
            with self.subTest(name=name):
                archive = self.tmp / "unsafe.tar"
                with tarfile.open(archive, "w") as bundle:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = "../escape" if kind != tarfile.REGTYPE else ""
                    entry.size = 1 if kind == tarfile.REGTYPE else 0
                    bundle.addfile(entry, io.BytesIO(b"x") if entry.size else None)
                    if duplicate:
                        bundle.addfile(entry, io.BytesIO(b"x"))
                checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
                target = self.tmp / "unsafe-output"
                with self.assertRaisesRegex(ValueError, "unsafe|duplicate|non-file"):
                    installation.extract(archive, checksum, target)
                self.assertFalse(target.exists())


class Platform(unittest.TestCase):
    def test_unit_quotes_spaces_percent_backslashes_and_quotes(self):
        unit = platform.definition(Path('/tmp/A 100% "trial"'), Path("/usr/bin/python3"),
                                   Path('/tmp/A 100% "trial"/install.json'), {"PATH": "/bin:/tmp/back\\slash"})
        self.assertIn('WorkingDirectory="/tmp/A 100%% \\"trial\\""', unit)
        self.assertIn('Environment="PATH=/bin:/tmp/back\\\\slash"', unit)
        self.assertIn('KillMode=control-group\n', unit)
        self.assertIn('NoNewPrivileges=yes\n', unit)

    def test_control_characters_cannot_inject_unit_directives(self):
        for value in ("/tmp/app\nExecStart=/usr/bin/other", "/tmp/app\r", "/tmp/app\x00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.definition(Path(value), Path("/usr/bin/python3"), Path("/tmp/install.json"), {"PATH": "/bin"})

    def test_indeterminate_and_unavailable_native_service_are_explicit(self):
        with mock.patch.object(platform, "run", return_value="LoadState=error\nActiveState=active\n"):
            with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                platform.status()
        with mock.patch.object(platform, "run", return_value="LoadState=loaded\n"):
            with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                platform.status()
        with mock.patch.object(platform.subprocess, "run", side_effect=OSError("fixture unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Native user service unavailable"):
                platform.status()
        with mock.patch.object(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "denied")):
            with self.assertRaisesRegex(RuntimeError, "Native user service failed: denied"):
                platform.status()

    def test_unsupported_platform_and_control_operation_are_refused(self):
        with mock.patch.object(platform.sys, "platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "macOS validation is pending"):
                platform.require_supported()
        with self.assertRaisesRegex(ValueError, "Unknown application service operation"):
            platform.control("mask")

    def test_execstart_disables_environment_expansion_in_literal_paths(self):
        unit = platform.definition(Path("/tmp/${UNDEFINED}/application"), Path("/usr/bin/python3"),
                                   Path("/tmp/install.json"), {"PATH": "/usr/bin:/bin"})
        # systemd's ':' command prefix keeps ${...} literal; quotes alone do not.
        self.assertIn('ExecStart=:"/usr/bin/python3" -B "/tmp/${UNDEFINED}/application/current/bin/alt" serve', unit)
