"""Real private archives and lifecycle state; native service effects stay in fixtures."""
import hashlib
import http.client
import http.server
import io
import json
import os
import plistlib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.fakes import FakeL2
from tests.support import REPO, SUITE, make_repo
from altitude import config, dispatch, engines, git_policy, installation, monitor, platform, state as S, tasks as T, tls


class InstallationCase(unittest.TestCase):
    """A throwaway home and prefix; native service, TLS and health probes are fixtures. HOST selects the platform's
    service definition and location; the supported-host gate is tested in Platform."""
    HOST = "linux"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-", dir=SUITE))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.prefix = self.home / ".local/share/altitude"
        self.settings = self.home / ".config/altitude/install.json"
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
            (platform.sys, "platform", self.HOST),
            (platform, "require_supported", lambda: None),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.unit = platform.service_path()
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


class Installation(InstallationCase):
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
        if self.HOST == "darwin":
            environment.update(plistlib.loads(self.unit.read_bytes())["EnvironmentVariables"])
        for line in self.unit.read_text().splitlines() if self.HOST == "linux" else ():
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


class InstallationDarwin(Installation):
    """The same lifecycle with the macOS LaunchAgent definition in ~/Library/LaunchAgents."""
    HOST = "darwin"


class Platform(unittest.TestCase):
    """The systemd definition and status; the macOS ones are in test_platform_darwin."""

    def setUp(self):
        for patcher in (mock.patch.object(platform.sys, "platform", "linux"),
                        mock.patch.object(platform.host_platform, "machine", return_value="x86_64")):
            patcher.start()
            self.addCleanup(patcher.stop)

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
        for host, machine, release, supported in (
                ("linux", "x86_64", "", True), ("darwin", "arm64", "15.0", True), ("darwin", "arm64", "26.6.2", True),
                ("linux", "aarch64", "", False), ("darwin", "x86_64", "15.0", False), ("darwin", "arm64", "14.7", False),
                ("win32", "AMD64", "", False)):
            with self.subTest(host=host, machine=machine, release=release), \
                    mock.patch.object(platform.sys, "platform", host), \
                    mock.patch.object(platform.host_platform, "machine", return_value=machine), \
                    mock.patch.object(platform.host_platform, "mac_ver", return_value=(release, ("", "", ""), "")):
                if supported:
                    platform.require_supported()
                else:
                    with self.assertRaisesRegex(RuntimeError, "macOS 15 or newer on Apple silicon"):
                        platform.require_supported()
        with mock.patch.object(platform, "require_supported"), \
                self.assertRaisesRegex(ValueError, "Unknown application service operation"):
            platform.control("mask")

    def test_detached_update_keeps_dollar_expressions_in_paths_literal(self):
        with mock.patch.object(platform, "require_supported"), mock.patch.object(platform, "run", return_value="") as run:
            platform.detach("altitude-update-v0.2.0", ["/tmp/${HOME}/python", "-B", "/tmp/${HOME}/current/bin/alt"], {"PATH": "/usr/bin"})
        self.assertIn("--expand-environment=no", run.call_args.args)
        self.assertEqual(run.call_args.args[-3:], ("/tmp/${HOME}/python", "-B", "/tmp/${HOME}/current/bin/alt"))

    def test_execstart_disables_environment_expansion_in_literal_paths(self):
        unit = platform.definition(Path("/tmp/${UNDEFINED}/application"), Path("/usr/bin/python3"),
                                   Path("/tmp/install.json"), {"PATH": "/usr/bin:/bin"})
        # systemd's ':' command prefix keeps ${...} literal; quotes alone do not.
        self.assertIn('ExecStart=:"/usr/bin/python3" -B "/tmp/${UNDEFINED}/application/current/bin/alt" serve', unit)


class PublishedReleaseCase(InstallationCase):
    """v0.1.0 installed; GitHub's release lookup and downloads are fixtures."""
    RELEASES = "https://github.com/example/altitude/releases"

    def setUp(self):
        super().setUp()
        self.install()
        self.requests = []
        self.published = {}
        patcher = mock.patch.object(config, "RELEASE", {"version": "v0.1.0", "repository": "https://github.com/example/altitude"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.download = installation._get
        patcher = mock.patch.object(installation, "_get", side_effect=self.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, url, limit):
        self.requests.append(url)
        if url not in self.published:
            raise OSError("fixture: not published")
        return self.published[url]

    def publish(self, version, *, latest=None, checksum=None, edited=True):
        archive, digest = self.archive(version, edited=edited)
        download = f"{self.RELEASES}/download/{version}/altitude-{version}.tar.gz"
        self.published[download] = archive.read_bytes()
        self.published[download + ".sha256"] = f"{checksum or digest}\n".encode()
        if latest is not None:
            self.published["https://api.github.com/repos/example/altitude/releases/latest"] = json.dumps(latest).encode()
        return download


class PublishedUpdate(PublishedReleaseCase):
    """`alt update` without an archive."""

    def test_every_download_redirect_hop_must_stay_on_https(self):
        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/next")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        # The first hop of a real download is always HTTPS; a plain-HTTP hop after it is refused before it is requested.
        with self.assertRaisesRegex(ValueError, "redirected away from HTTPS"):
            self.download(f"http://127.0.0.1:{server.server_port}/first", 1024)

    def test_the_archive_must_be_the_requested_release_and_newer_under_the_lock(self):
        mislabelled, digest = self.archive("v0.0.9")
        download = f"{self.RELEASES}/download/v0.1.1/altitude-v0.1.1.tar.gz"
        self.published.update({download: mislabelled.read_bytes(), download + ".sha256": digest.encode()})
        with self.assertRaisesRegex(ValueError, "The v0.1.1 release contains v0.0.9"):
            installation.update("v0.1.1")
        # Another update activated v0.2.0 while this one was downloading v0.1.1.
        newer, digest = self.archive("v0.2.0", edited=True)
        installation.install(newer, digest, self.prefix)
        self.publish("v0.1.1")
        with self.assertRaisesRegex(ValueError, "Altitude v0.2.0 is already installed; v0.1.1 is not newer"):
            installation.update("v0.1.1")
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.2.0")
        self.assertFalse((self.prefix / "versions/v0.1.1").exists())

    def test_latest_release_is_downloaded_verified_and_activated(self):
        download = self.publish("v0.1.1", latest={"tag_name": "v0.1.1", "prerelease": False, "draft": False})
        result = installation.update()
        self.assertEqual((result["version"], result["updated"]), ("v0.1.1", True))
        self.assertEqual(result["notes"], f"{self.RELEASES}/tag/v0.1.1")
        self.assertEqual(set(result), {"version", "updated", "service", "url", "notes"})
        self.assertNotIn(str(self.home), json.dumps(result))
        self.assertEqual(self.requests, ["https://api.github.com/repos/example/altitude/releases/latest",
                                         download, download + ".sha256"])
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.1")
        self.assertEqual(installation.metadata(self.prefix / "versions/v0.1.0")["version"], "v0.1.0")

    def test_current_or_older_latest_changes_nothing(self):
        for latest in ("v0.1.0", "v0.1.0-rc.2", "v0.0.9"):
            with self.subTest(latest=latest):
                self.published = {"https://api.github.com/repos/example/altitude/releases/latest":
                                  json.dumps({"tag_name": latest}).encode()}
                self.requests.clear()
                self.actions.clear()
                result = installation.update()
                self.assertEqual(result, {"version": "v0.1.0", "updated": False, "detail": "Altitude v0.1.0 is up to date"})
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.actions, [])

    def test_named_version_installs_that_release_without_a_lookup(self):
        download = self.publish("v0.2.0-rc.1")
        self.assertEqual(installation.update("v0.2.0-rc.1")["version"], "v0.2.0-rc.1")
        self.assertEqual(self.requests, [download, download + ".sha256"])
        self.requests.clear()
        with self.assertRaisesRegex(ValueError, "published v0.MINOR.PATCH"):
            installation.update("latest; rm -rf ~")
        for older in ("v0.0.9", "v0.1.0-rc.1"):
            with self.subTest(older=older), self.assertRaisesRegex(ValueError, "older than the installed v0.1.0"):
                installation.update(older)
        self.assertEqual(self.requests, [])

    def test_mismatched_published_checksum_keeps_the_installed_version(self):
        self.publish("v0.1.1", latest={"tag_name": "v0.1.1"}, checksum="0" * 64)
        self.actions.clear()
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            installation.update()
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.0")
        self.assertFalse((self.prefix / "versions/v0.1.1").exists())
        self.assertEqual(self.actions, [])

    def test_unusable_lookup_or_unnamed_repository_refuses_before_downloading(self):
        for latest in ({"tag_name": "v0.2.0", "prerelease": True}, {"tag_name": "main"}, ["v0.2.0"]):
            with self.subTest(latest=latest):
                self.published = {"https://api.github.com/repos/example/altitude/releases/latest":
                                  json.dumps(latest).encode()}
                self.requests.clear()
                with self.assertRaisesRegex(ValueError, "no valid version"):
                    installation.update()
                self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        with mock.patch.object(config, "RELEASE", {"version": "v0.1.0", "repository": "example/altitude"}):
            with self.assertRaisesRegex(RuntimeError, "update with --archive and --sha256"):
                installation.update()
        self.assertEqual(self.requests, [])
        with mock.patch.object(installation, "_get", side_effect=OSError("fixture: offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                installation.update()
        self.assertEqual((self.prefix / "current").resolve(), self.prefix / "versions/v0.1.0")


class UpdatedProjectGuards(PublishedReleaseCase):
    """#348: after `alt update`, a registered project's next task dispatches with current guards."""

    def serve(self, version):
        """The daemon after activation runs `current`, as the restarted service does."""
        source = (self.prefix / "current").resolve()
        self.assertEqual(source.name, version)
        release = json.loads((source / "release.json").read_text())
        for name, value in (("SOURCE", source), ("REPO", source), ("RELEASE", {**release, "repository": "https://github.com/example/altitude"}),
                            ("INSTALL_CONFIG", self.settings), ("PERSONAS", source / "personas"),
                            ("SCHEMAS", source / "schemas"), ("TEMPLATES", source / "templates"), ("HOOKS", source / "hooks")):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_next_dispatch_after_update_uses_current_guards_and_version(self):
        self.patch = lambda target, name, *new, **kwargs: self.enterContext(mock.patch.object(target, name, *new, **kwargs))
        for name, value in (("claude_agents", []), ("usage_hold", None),
                            ("installation", {"available": None, "why": "fixture engines"})):
            self.patch(engines, name, return_value=value)
        self.patch(monitor, "quota", return_value={"known": True})
        engine = FakeL2()
        engine.install(self)
        repo = make_repo(self.home / "Projects/demo")
        config.save_projects({"demo": {"name": "demo", "path": str(repo)}})

        self.serve("v0.1.0")
        first = T.new("demo", "Before the update", "Fictional work on the first version.")
        dispatch.run("demo", first["slug"])
        self.assertEqual(S.load_task("demo", first["slug"])["state"], "running")
        self.assertEqual(git_policy.require_hooks_installed(repo), self.prefix / "hooks")

        self.publish("v0.1.1", latest={"tag_name": "v0.1.1"})
        self.assertEqual(installation.update()["version"], "v0.1.1")
        self.serve("v0.1.1")
        second = T.new("demo", "After the update", "Fictional work on the updated version.")
        dispatch.run("demo", second["slug"])
        self.assertEqual(S.load_task("demo", second["slug"])["state"], "running")
        self.assertEqual(engine.calls[-1]["persona"], self.prefix / "versions/v0.1.1/personas/l2.md")
        self.assertEqual(git_policy.require_hooks_installed(repo), self.prefix / "hooks")
        # The installed guard runs the updated version and still protects main.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("ALTITUDE_")}
        refused = subprocess.run(["git", "commit", "--allow-empty", "-m", "Direct to main"], cwd=repo,
                                 env=environment, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("task branch", refused.stderr)
        self.assertIn("current/hooks/pre-commit", (self.prefix / "hooks/pre-commit").read_text())
        self.assertEqual((self.prefix / "current/hooks/pre-commit").resolve(), self.prefix / "versions/v0.1.1/hooks/pre-commit")


class NoticeCase(PublishedReleaseCase):
    """v0.1.0 installed, its release lookup a fixture, and the detached update recorded instead of run."""
    LATEST = "https://api.github.com/repos/example/altitude/releases/latest"

    def setUp(self):
        super().setUp()
        self.detached = []
        patcher = mock.patch.object(platform, "detach", side_effect=lambda *a: self.detached.append(a) or "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def latest(self, version):
        self.published[self.LATEST] = json.dumps({"tag_name": version}).encode()


class NewVersionNotice(NoticeCase):
    """The daemon's release check, what the app and terminal show, and the app's Update button."""

    def test_check_runs_every_twelve_hours_and_retries_an_hour_after_going_offline(self):
        installation.check_for_update(now=1000)
        self.assertEqual(self.requests, [self.LATEST])
        self.assertIsNone(installation.update_status()["available"])
        installation.check_for_update(now=1000 + 3599)
        self.assertEqual(len(self.requests), 1)
        self.latest("v0.2.0")
        installation.check_for_update(now=1000 + 3600)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(installation.update_status()["available"],
                         {"version": "v0.2.0", "notes": f"{self.RELEASES}/tag/v0.2.0"})
        installation.check_for_update(now=1000 + 3600 + 12 * 3600 - 1)
        self.assertEqual(len(self.requests), 2)
        installation.check_for_update(now=1000 + 3600 + 12 * 3600)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(installation.update_status()["available"]["version"], "v0.2.0")

    def test_only_a_newer_stable_release_is_offered(self):
        for version, offered in (("v0.1.0", False), ("v0.0.9", False), ("v0.1.1", True)):
            with self.subTest(version=version):
                (config.ROOT / "update.json").unlink(missing_ok=True)
                self.latest(version)
                installation.check_for_update()
                self.assertEqual(bool(installation.update_status()["available"]), offered)
        self.published[self.LATEST] = json.dumps({"tag_name": "v0.3.0-rc.1", "prerelease": True}).encode()
        (config.ROOT / "update.json").unlink()
        installation.check_for_update()
        self.assertIsNone(installation.update_status()["available"])

    def test_the_switch_stops_the_check_and_hides_the_notice(self):
        self.latest("v0.2.0")
        installation.check_for_update()
        S.write_json(config.ROOT / "settings.json", {"update_check": False})
        self.requests.clear()
        (config.ROOT / "update.json").unlink()
        installation.check_for_update()
        self.assertEqual(self.requests, [])
        status = installation.update_status()
        self.assertEqual((status["check"], status["available"]), (False, None))

    def test_a_source_deployment_neither_checks_nor_reports(self):
        with mock.patch.object(config, "RELEASE", None):
            installation.check_for_update()
            self.assertIsNone(installation.update_status())
        self.assertEqual(self.requests, [])

    def test_the_terminal_line_appears_once_a_day_from_the_saved_check(self):
        self.assertIsNone(installation.update_notice())
        self.latest("v0.2.0")
        installation.check_for_update()
        self.requests.clear()
        self.assertEqual(installation.update_notice(), "Altitude v0.2.0 is available: run alt update "
                         "(notes: https://github.com/example/altitude/releases/tag/v0.2.0)")
        self.assertIsNone(installation.update_notice())
        day_ago = (config.ROOT / "update-notice").stat().st_mtime - 86400
        os.utime(config.ROOT / "update-notice", (day_ago, day_ago))
        self.assertIsNotNone(installation.update_notice())
        self.assertEqual(self.requests, [])

    def test_doctor_reports_the_available_release(self):
        self.latest("v0.2.0")
        installation.check_for_update()
        with mock.patch.object(installation, "_gh_signed_in", return_value=False), \
                mock.patch.object(tls, "info", side_effect=OSError("fixture: no certificate")):
            self.assertEqual(installation.doctor()["update"]["available"]["version"], "v0.2.0")

    def test_the_button_runs_the_verified_update_for_exactly_the_shown_version(self):
        for version in ("v0.2.0", "v0.1.0", "v0.0.9"):
            with self.subTest(before_check=version), self.assertRaisesRegex(ValueError, "Only the newer release"):
                installation.request_update(version)
        self.latest("v0.2.0")
        installation.check_for_update()
        for version in ("v0.1.1", "v0.3.0", "v0.1.0", "v0.2.0; rm -rf ~"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "Only the newer release"):
                installation.request_update(version)
        self.assertEqual(self.detached, [])
        status = installation.request_update("v0.2.0")
        self.assertEqual(status["attempt"]["state"], "running")
        saved = json.loads(self.settings.read_text())
        [(name, argv, environment)] = self.detached
        self.assertEqual(name, "altitude-update-v0.2.0")
        self.assertEqual(argv, [saved["python"], "-B", str(self.prefix / "current/bin/alt"), "update", "--version", "v0.2.0"])
        self.assertEqual(environment["ALTITUDE_CONFIG"], str(self.settings))
        self.assertNotIn("ALTITUDE_ACTOR", environment)
        installation.request_update("v0.2.0")
        self.assertEqual(len(self.detached), 1)

    def test_a_failed_or_stalled_update_is_reported_and_can_be_retried(self):
        self.latest("v0.2.0")
        installation.check_for_update()
        installation.request_update("v0.2.0")
        with self.assertRaises(OSError):
            installation.update("v0.2.0")
        status = installation.update_status()
        self.assertEqual((status["current"], status["attempt"]["state"]), ("v0.1.0", "failed"))
        # The page gets a fixed sentence; the cause stays in the terminal or the update unit's log.
        self.assertEqual(status["attempt"]["error"], "Run alt update in a terminal to see why.")
        installation.request_update("v0.2.0")
        self.assertEqual(len(self.detached), 2)
        with mock.patch.object(installation.time, "time", return_value=time.time() + 1801):
            self.assertEqual(installation.update_status()["attempt"], {**installation._update_record()[1]["attempt"],
                             "state": "failed", "error": "Run alt update in a terminal to see why."})

    def test_an_update_that_cannot_start_is_marked_failed(self):
        self.latest("v0.2.0")
        installation.check_for_update()
        with mock.patch.object(platform, "detach", side_effect=RuntimeError("fixture: systemd-run failed")):
            with self.assertRaises(RuntimeError):
                installation.request_update("v0.2.0")
        self.assertEqual(installation.update_status()["attempt"]["state"], "failed")

    def test_a_check_finishing_during_an_update_keeps_its_attempt(self):
        self.latest("v0.2.0")
        installation.check_for_update()
        lookup = installation.latest_release

        def slow_lookup(repository):
            installation.request_update("v0.2.0")  # the Update button while the daemon's lookup is in flight
            return lookup(repository)

        with mock.patch.object(installation, "latest_release", side_effect=slow_lookup), \
                mock.patch.object(installation.time, "time", return_value=time.time() + 13 * 3600):
            installation.check_for_update()
        self.assertEqual(installation.update_status()["attempt"]["state"], "running")

    def test_a_finished_update_clears_the_notice(self):
        self.publish("v0.1.1", latest={"tag_name": "v0.1.1"})
        installation.check_for_update()
        installation.request_update("v0.1.1")
        installation.update("v0.1.1")
        with mock.patch.object(config, "RELEASE", {"version": "v0.1.1", "repository": "https://github.com/example/altitude"}):
            status = installation.update_status()
        self.assertEqual((status["current"], status["available"], status["attempt"]), ("v0.1.1", None, None))


class UpdateRequests(NoticeCase):
    """The Update button and the check switch through the real HTTP handler, behind the terminal's checks."""

    def setUp(self):
        super().setUp()
        from altitude import access, server, terminal
        self.agent = mock.patch.object(terminal, "agent_connection", return_value=False).start()
        mock.patch.object(access, "is_machine", return_value=True).start()  # past the pairing gate, as AltitudeCase
        self.addCleanup(mock.patch.stopall)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.latest("v0.2.0")
        installation.check_for_update()

    def post(self, path, body, *, status=200, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=30)
        try:
            connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            connection.close()

    def test_cross_site_pages_and_agents_cannot_start_an_update(self):
        # Every action refuses another site and a rebound host before routing; the update also needs JSON.
        for headers, error in (({"Origin": "https://elsewhere.example"}, "Requests must come from Altitude's own page."),
                               ({"Sec-Fetch-Site": "cross-site"}, "Requests must come from Altitude's own page."),
                               ({"Host": "rebound.example"}, "Over plain HTTP, open Altitude at its address or localhost."),
                               ({"Content-Type": "text/plain"}, "Update requests must come from Altitude's own page.")):
            with self.subTest(headers=headers):
                self.assertEqual(self.post("/api/update", {"version": "v0.2.0"}, status=403, headers=headers)["error"], error)
        self.agent.return_value = True
        self.assertEqual(self.post("/api/update", {"version": "v0.2.0"}, status=403)["error"],
                         "Update requests from Altitude's own agents are refused.")
        self.post("/api/update-check", {"enabled": False}, status=403)
        self.assertEqual(self.detached, [])
        self.assertTrue(installation.update_status()["check"])

    def test_launch_and_record_failures_reach_the_page_without_private_paths(self):
        public = "Altitude could not complete the update request. Run alt update in a terminal to see why."
        with mock.patch.object(platform, "detach", side_effect=RuntimeError(f"systemd-run failed in {self.home}")):
            self.assertEqual(self.post("/api/update", {"version": "v0.2.0"}, status=503)["error"], public)
        self.assertEqual(installation.update_status()["attempt"]["state"], "failed")
        with mock.patch("altitude.server._save_machine", side_effect=ValueError(f"Invalid JSON in {self.home}/settings.json")):
            self.assertEqual(self.post("/api/update-check", {"enabled": False}, status=503)["error"], public)
        installation._update_record()[0].write_text("{not json")
        for path, body in (("/api/update", {"version": "v0.2.0"}), ("/api/update-check", {"enabled": False})):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, body, status=503)["error"], public)

    def test_the_page_starts_only_the_shown_version_and_switches_the_check(self):
        self.post("/api/update", {"version": "v0.3.0"}, status=409)
        self.post("/api/update", {"version": ["v0.2.0"]}, status=400)
        self.post("/api/update", {"version": "v0.2.0", "force": True}, status=400)
        self.assertEqual(self.detached, [])
        self.assertEqual(self.post("/api/update", {"version": "v0.2.0"})["update"]["attempt"]["state"], "running")
        self.assertEqual(len(self.detached), 1)
        off = self.post("/api/update-check", {"enabled": False})
        self.assertEqual((off["update_check"], off["update"]["available"]), (False, None))
        self.post("/api/update-check", {"enabled": "no"}, status=400)
        self.assertTrue(self.post("/api/update-check", {"enabled": True})["update_check"])
