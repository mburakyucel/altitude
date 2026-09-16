"""Packaged code has no Git checkout; project ownership and guards remain real."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from unittest import mock

from tests.support import REPO, AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, git_policy, project_setup, server, state as S, tasks as T


class InstalledRuntime(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        self.prefix = self.tmp / "application"
        self.source = self.prefix / "versions/trial-1"
        self.source.mkdir(parents=True)
        for directory in ("altitude", "bin", "hooks", "personas", "schemas", "templates"):
            shutil.copytree(REPO / directory, self.source / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.release = {"version": "trial-1", "commit": "a" * 40, "repository": "example/altitude", "files": {}}
        (self.source / "release.json").write_text(json.dumps(self.release))
        (self.prefix / "current").symlink_to(self.source, target_is_directory=True)
        launchers = self.prefix / "launchers/trial-1"
        launchers.mkdir(parents=True)
        (launchers / "alt").write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-B", str(self.source / "bin/alt")]) + ' "$@"\n')
        (launchers / "alt").chmod(0o755)
        hooks = self.prefix / "hooks"
        hooks.mkdir()
        for name in git_policy.REQUIRED_HOOKS:
            (hooks / name).write_text("#!/bin/sh\nexec " + shlex.join([
                sys.executable, "-B", str(self.prefix / "current/hooks" / name)]) + ' "$@"\n')
            (hooks / name).chmod(0o755)
        self.patch(config, "SOURCE", self.source)
        self.patch(config, "REPO", self.source)
        self.patch(config, "RELEASE", self.release)
        self.patch(config, "INSTALL_PREFIX", self.prefix)
        settings = self.tmp / "installed settings.json"
        settings.write_text(json.dumps({"python": sys.executable, "environment": {}}))
        self.patch(config, "INSTALL_CONFIG", settings)
        for name in ("PERSONAS", "SCHEMAS", "TEMPLATES", "HOOKS"):
            self.patch(config, name, self.source / name.lower())
        self.engine = FakeL2()
        self.engine.install(self)

    def read_config(self, settings=None, **overrides):
        settings_file = self.tmp / "install.json"
        settings_file.write_text(json.dumps({"environment": settings or {}}))
        env = {key: value for key, value in os.environ.items()
               if key not in ("ALTITUDE_HOST", "ALTITUDE_PORT", "ALTITUDE_TLS_DIR", "ALTITUDE_TLS")}
        env.update({"PYTHONPATH": str(self.source), "ALTITUDE_CONFIG": str(settings_file), **overrides})
        code = ("import json; from altitude import config as c; "
                "print(json.dumps({k: str(getattr(c,k)) for k in "
                "('SOURCE','REPO','INSTALL_PREFIX','WEB_DIST','HOST','PORT','TLS','TLS_DIR','ROOT')}))")
        result = subprocess.run([sys.executable, "-c", code], cwd=self.tmp, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_package_reads_saved_settings_with_explicit_overrides_and_no_checkout(self):
        observed = self.read_config({"ALTITUDE_HOST": "192.0.2.1", "ALTITUDE_PORT": "9443",
                                     "ALTITUDE_TLS_DIR": str(self.tmp / "own-tls")}, ALTITUDE_PORT="9553")
        self.assertEqual(observed["SOURCE"], str(self.source))
        self.assertEqual(observed["REPO"], str(self.source))
        self.assertEqual(observed["INSTALL_PREFIX"], str(self.prefix))
        self.assertEqual(observed["WEB_DIST"], str(self.source / "web/dist"))
        self.assertEqual((observed["HOST"], observed["PORT"]), ("192.0.2.1", "9553"))
        self.assertEqual(observed["TLS_DIR"], str(self.tmp / "own-tls"))
        self.assertFalse((self.source / ".git").exists())

    def test_fresh_defaults_and_source_checkout_ignore_install_settings(self):
        defaults = self.read_config()
        self.assertEqual((defaults["HOST"], defaults["TLS"]), ("127.0.0.1", "True"))
        self.assertEqual(defaults["TLS_DIR"], str(config.HOME / ".config/altitude/tls"))
        (self.source / "release.json").unlink()
        source = self.read_config({"ALTITUDE_HOST": "192.0.2.2", "ALTITUDE_PORT": "9443"})
        self.assertEqual((source["HOST"], source["PORT"]), ("127.0.0.1", "8890"))
        self.assertEqual(source["INSTALL_PREFIX"], "None")

    def test_activation_keeps_packaged_inputs_and_custom_project_hooks(self):
        custom = self.tmp / "custom-hooks"
        custom.mkdir()
        git("config", "core.hooksPath", str(custom), cwd=self.repo)
        with mock.patch.object(git_policy, "service_preflight", side_effect=AssertionError("package is not a checkout")):
            git_policy.activate_source()
        self.assertEqual(config.SOURCE, self.source)
        self.assertFalse((self.source / ".altitude-source").exists())
        self.assertEqual(git("config", "--get", "core.hooksPath", cwd=self.repo).strip(), str(custom))
        with self.assertRaisesRegex(git_policy.GitPolicyError, "refusing to overwrite"):
            git_policy.install_hooks(self.repo)

    def test_task_dispatch_and_resume_keep_owner_hold_and_packaged_resources(self):
        expected = self.prefix / "hooks"
        self.assertEqual(git_policy.install_hooks(self.repo), expected)
        task = T.new(self.project, "Installed task", "Use the packaged installation.", hold_merge="Operator review")
        dispatch.run(self.project, task["slug"])
        launch = self.engine.calls[-1]
        self.assertEqual(launch["persona"], self.source / "personas/l2.md")
        self.assertIn(str(self.source / "schemas/report.json"), launch["prompt"])
        running = S.load_task(self.project, task["slug"])
        work = launch["cwd"] / "draft.txt"
        work.write_text("Unfinished work stays here.\n")
        self.engine.stop_l2_worker(running["l2_engine"], running["agent_id"], job_root=None)
        T.block(self.project, task["slug"], "Awaiting context", updates={"waiting_on": "l3"})
        T.message(self.project, task["slug"], "l3", "Continue the same task.")
        dispatch.resume(self.project, task["slug"])
        resumed = S.load_task(self.project, task["slug"])
        self.assertEqual(resumed["session_id"], running["session_id"])
        self.assertEqual(resumed["worktree"], running["worktree"])
        self.assertEqual(resumed["hold_merge"], running["hold_merge"])
        self.assertEqual(work.read_text(), "Unfinished work stays here.\n")

    def test_missing_guard_resources_refuse_dispatch_before_launch(self):
        (self.prefix / "hooks/pre-commit").unlink()
        task = T.new(self.project, "Guard required", "Never launch without project guards.")
        with mock.patch("altitude.incidents.system_fault"):
            with self.assertRaisesRegex(T.TransitionError, "missing: pre-commit"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "blocked")

    def test_removed_guards_refuse_resume_without_losing_session_or_message(self):
        git_policy.install_hooks(self.repo)
        task = T.new(self.project, "Resume guards", "Keep the owner when guards disappear.")
        dispatch.run(self.project, task["slug"])
        running = S.load_task(self.project, task["slug"])
        self.engine.stop_l2_worker(running["l2_engine"], running["agent_id"], job_root=None)
        T.block(self.project, task["slug"], "Awaiting context", updates={"waiting_on": "l3"})
        message = T.message(self.project, task["slug"], "l3", "Continue after repair.")
        (self.prefix / "hooks/pre-commit").unlink()
        with mock.patch("altitude.incidents.system_fault"):
            with self.assertRaisesRegex(T.TransitionError, "missing: pre-commit"):
                dispatch.resume(self.project, task["slug"])
        blocked = S.load_task(self.project, task["slug"])
        self.assertEqual((blocked["state"], blocked["session_id"]), ("blocked", running["session_id"]))
        self.assertEqual(len(self.engine.calls), 1)
        self.assertIn(message["id"], [row["id"] for row in T.pending(self.project, task["slug"])])

    def test_installed_dispatch_repairs_missing_project_guard_selection(self):
        task = T.new(self.project, "Routine setup", "Install the required guards before dispatch.")
        dispatch.run(self.project, task["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        self.assertEqual(git_policy.require_hooks_installed(self.repo), self.prefix / "hooks")
        self.assertEqual(project_setup.read(self.project)["guards"]["action"], "installed")

    def test_unrecorded_existing_task_worktree_cannot_bypass_custom_hook_consent(self):
        git_policy.install_hooks(self.repo)
        git("config", "extensions.worktreeConfig", "true", cwd=self.repo)
        task = T.new(self.project, "Interrupted first dispatch", "Recheck guards before adopting the existing checkout.")
        worktree = self.repo / config.WORKTREE_ROOT / task["slug"]
        git("worktree", "add", "-q", "-b", f"worktree-{task['slug']}", str(worktree), "origin/main", cwd=self.repo)
        custom = self.tmp / "worktree-specific-hooks"
        custom.mkdir()
        (custom / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        (custom / "pre-commit").chmod(0o755)
        git("config", "--worktree", "core.hooksPath", str(custom), cwd=worktree)
        self.assertFalse(S.load_task(self.project, task["slug"]).get("worktree"))
        with mock.patch("altitude.incidents.system_fault"):
            with self.assertRaisesRegex(T.TransitionError, "Git guards are not installed or current"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(git("config", "--worktree", "--get", "core.hooksPath", cwd=worktree).strip(), str(custom))

    def test_managed_application_clone_cannot_replace_installed_application(self):
        self.register(self.project, self_deploy=True)
        other = self.tmp / "other"
        git("clone", "-q", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        git("switch", "main", cwd=other)
        (other / "altitude").mkdir()
        (other / "altitude/changed.py").write_text("# Reviewed project change\n")
        git("add", ".", cwd=other)
        git("commit", "-qm", "Reviewed change", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        installed = (self.source / "altitude/config.py").read_bytes()
        notes = dispatch.self_deploy_fast_forward(self.project)
        self.assertTrue(notes[0].startswith("self-deploy:"))
        self.assertTrue((self.repo / "altitude/changed.py").is_file())
        self.assertEqual((self.source / "altitude/config.py").read_bytes(), installed)
        self.assertEqual((self.prefix / "current").resolve(), self.source)
        self.assertFalse((config.MONITOR_DIR / dispatch.RESTART_PENDING).exists())

    def test_worker_cli_stays_pinned_after_update_without_changing_project_python(self):
        later = self.prefix / "versions/trial-2"
        shutil.copytree(self.source, later)
        (later / "bin/alt").write_text("#!/bin/sh\necho wrong version >&2\nexit 87\n")
        (self.prefix / "current").unlink()
        (self.prefix / "current").symlink_to(later, target_is_directory=True)
        tools = self.tmp / "project-tools"
        tools.mkdir()
        python = tools / "python3"
        python.write_text("#!/bin/sh\necho project Python is not the application interpreter >&2\nexit 86\n")
        python.chmod(0o755)
        self.setenv("PATH", str(tools) + ":" + os.environ["PATH"])
        environment = engines.clean_env()
        self.assertEqual(shutil.which("alt", path=environment["PATH"]), str(self.prefix / "launchers/trial-1/alt"))
        self.assertEqual(shutil.which("python3", path=environment["PATH"]), str(python))
        result = subprocess.run(["alt", "--help"], cwd=self.repo, env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(any(self.source.rglob("__pycache__")))
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")

    def test_git_guard_uses_installed_interpreter_outside_worker_environment(self):
        git_policy.install_hooks(self.repo)
        tools = self.tmp / "operator-tools"
        tools.mkdir()
        python = tools / "python3"
        python.write_text("#!/bin/sh\necho wrong interpreter >&2\nexit 86\n")
        python.chmod(0o755)
        environment = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"]}
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run(["git", "commit", "--allow-empty", "-m", "Must refuse direct commit"],
                                cwd=self.repo, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("commit on a task branch", result.stderr)
        self.assertNotIn("wrong interpreter", result.stderr)
        self.assertFalse(any(self.source.rglob("__pycache__")))

    def test_session_hook_command_quotes_paths_and_pins_the_daemon_interpreter(self):
        hooks = self.tmp / "hook directory with spaces"
        hooks.mkdir()
        (hooks / "inbox.py").write_text("import json, sys; print(json.dumps([sys.executable, sys.dont_write_bytecode]))\n")
        self.patch(config, "HOOKS", hooks)
        settings = S.read_json(dispatch.session_settings(self.project, "quoted-hooks", "fixture-key"))
        command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command), [sys.executable, "-B", str(hooks / "inbox.py")])
        result = subprocess.run(command, shell=True, cwd=self.repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [sys.executable, True])

    def test_l3_broker_inspection_uses_daemon_interpreter_with_hostile_python_on_path(self):
        task = T.new(self.project, "Broker inspection", "Inspect an installed task without launching it.")
        tools = self.tmp / "hostile-tools"
        tools.mkdir()
        python = tools / "python3"
        python.write_text("#!/bin/sh\necho wrong broker interpreter >&2\nexit 86\n")
        python.chmod(0o755)
        self.setenv("PATH", str(tools) + ":" + os.environ["PATH"])
        result = server.l3_verb_request(self.project, {"kind": "alt", "args": ["task", "list"]})
        self.assertEqual(result["returncode"], 0, result["stderr"])
        rows = json.loads(result["stdout"])
        self.assertIn(task["slug"], [row["slug"] for row in rows])
        self.assertEqual(self.engine.calls, [])
        self.assertFalse(any(self.source.rglob("__pycache__")))

    def test_installed_runtime_preserves_but_ignores_pending_source_activation(self):
        marker = config.MONITOR_DIR / dispatch.RESTART_PENDING
        S.write_json(marker, {"requested_at": S.now(), "head": "a" * 40, "files": ["altitude/server.py"],
                              "unit": "fictional-source-restart"})
        before = marker.read_bytes()
        self.assertIsNone(server.restart_status())
        server.auto_restart()
        with self.assertRaisesRegex(RuntimeError, "Installed releases use alt update"):
            server.restart_service()
        self.assertEqual(marker.read_bytes(), before)
        git_policy.install_hooks(self.repo)
        task = T.new(self.project, "Installed marker isolation", "Retained source state does not block package work.")
        pending = self.prefix / "pending.json"
        pending.write_text('{"candidate": "versions/trial-2"}\n')
        with self.assertRaisesRegex(T.TransitionError, "Altitude is restarting"):
            dispatch.run(self.project, task["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "queued")
        self.assertEqual(self.engine.calls, [])
        pending.unlink()  # The installation transaction clears only its own receipt after verification.
        dispatch.run(self.project, task["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
        self.assertEqual(marker.read_bytes(), before)

    def test_application_lifecycle_commands_are_denied_to_both_worker_roles(self):
        task = T.new(self.project, "Preserved authorization", "Keep role boundaries intact.", hold_merge="Review")
        status = S.status_path(self.project, task["slug"])
        before = status.read_bytes()
        commands = [
            ["install", "--archive", str(self.tmp / "does-not-exist.tar"), "--sha256", "0" * 64],
            ["update", "--archive", str(self.tmp / "does-not-exist.tar"), "--sha256", "0" * 64],
            ["uninstall"], ["recover"], ["service", "start"], ["service", "stop"],
            ["service", "prepare-tls", "--directory", str(self.tmp / "tls"), "--apply"],
        ]
        for actor in ("l2", "l3"):
            for args in commands:
                with self.subTest(actor=actor, command=args[0:2]):
                    result = self.alt(*args, env={"ALTITUDE_ACTOR": actor, "ALTITUDE_PROJECT": self.project})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"not available to an {actor.upper()}", result.stderr)
                    self.assertNotIn("offline tests", result.stderr)
        self.assertEqual(status.read_bytes(), before)
        self.assertEqual((self.prefix / "current").resolve(), self.source)
        self.assertEqual(self.engine.calls, [])

    def custom_hooks(self):
        custom = self.tmp / "custom hooks"
        custom.mkdir()
        for name in ("pre-commit", "pre-push", "post-checkout"):
            hook = custom / name
            hook.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(name)
                            + " >> " + shlex.quote(str(self.tmp / "custom-events"))
                            + '\nexit "${CUSTOM_REJECTION:-0}"\n')
            hook.chmod(0o755)
        git("config", "core.hooksPath", str(custom), cwd=self.repo)
        return custom

    @staticmethod
    def package_snapshot(source):
        return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.rglob("*") if path.is_file()}

    def test_custom_hook_consent_survives_archive_update_without_mutating_releases(self):
        python = self.tmp / "saved Python"
        python.write_text("#!/bin/sh\nprintf 'used\\n' >> " + shlex.quote(str(self.tmp / "interpreter-calls"))
                          + "\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        config.INSTALL_CONFIG.write_text(json.dumps({"python": str(python), "environment": {}}))
        custom = self.custom_hooks()
        original = (custom / "pre-commit").read_bytes()
        before = self.package_snapshot(self.source)
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        with self.assertRaisesRegex(PermissionError, "Only the operator"):
            project_setup.request(self.project, "combine", actor="l3", expected=observed["fingerprint"])
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        approved = git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        combined = Path(approved["hooks_path"])
        self.assertTrue(combined.is_relative_to(self.prefix / "git-guards"))
        self.assertFalse(combined.is_relative_to(config.ROOT))
        self.assertEqual(self.package_snapshot(self.source), before)
        self.assertEqual((custom / "pre-commit").read_bytes(), original)

        tools = self.tmp / "unrelated-python"
        tools.mkdir()
        (tools / "python3").write_text("#!/bin/sh\necho wrong interpreter >&2\nexit 86\n")
        (tools / "python3").chmod(0o755)
        environment = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"]}
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        refused = subprocess.run(["git", "commit", "--allow-empty", "-m", "Protected main"],
                                 cwd=self.repo, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertIn("commit on a task branch", refused.stderr)
        self.assertTrue((self.tmp / "interpreter-calls").is_file())
        self.assertFalse((self.tmp / "custom-events").exists())
        git("switch", "-c", "composed-topic", cwd=self.repo)
        accepted = subprocess.run(["git", "commit", "--allow-empty", "-m", "Composed topic"],
                                  cwd=self.repo, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("pre-commit", (self.tmp / "custom-events").read_text())
        rejected = subprocess.run([str(combined / "pre-commit")], cwd=self.repo,
                                  env={**environment, "CUSTOM_REJECTION": "17"},
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(rejected.returncode, 17, rejected.stderr)

        later = self.prefix / "versions/trial-2"
        shutil.copytree(self.source, later)
        (later / "release.json").write_text(json.dumps({**self.release, "version": "trial-2"}))
        later_before = self.package_snapshot(later)
        (self.prefix / "current").unlink()
        (self.prefix / "current").symlink_to(later, target_is_directory=True)
        with mock.patch.object(config, "SOURCE", later), mock.patch.object(config, "REPO", later):
            self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "ready")
            self.assertEqual(git_policy.repair_hooks(self.repo)["action"], "reused")
            self.assertEqual(git_policy.require_hooks_installed(self.repo), combined)
            accepted = subprocess.run([str(combined / "pre-commit")], cwd=self.repo, env=environment,
                                      capture_output=True, text=True, timeout=30)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.package_snapshot(self.source), before)
        self.assertEqual(self.package_snapshot(later), later_before)

    def test_installed_composition_retries_failed_write_and_rejects_stale_or_forged_consent(self):
        custom = self.custom_hooks()
        observed = git_policy.inspect_hooks(self.repo)
        run = git_policy._run

        def denied(repository, *args, **kwargs):
            if args[:3] == ("config", "--local", "core.hooksPath"):
                raise git_policy.GitPolicyError("fixture configuration permission denied")
            return run(repository, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=denied):
            with self.assertRaisesRegex(git_policy.GitPolicyError, "permission denied"):
                git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(git("config", "--get", "core.hooksPath", cwd=self.repo).strip(), str(custom))
        approved = git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        combined = Path(approved["hooks_path"])
        saved = (combined / "original.json").read_bytes()
        (custom / "pre-commit").write_text("#!/bin/sh\nexit 7\n")
        changed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(changed["status"], "conflict")
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "ownership changed"):
            git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        invoked = subprocess.run([str(combined / "pre-commit")], cwd=self.repo,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(invoked.returncode, 1)
        self.assertIn("original hooks changed", invoked.stderr)
        self.assertEqual((combined / "original.json").read_bytes(), saved)
        refreshed = Path(git_policy.repair_hooks(self.repo, combine=True, expected=changed["fingerprint"])["hooks_path"])
        receipt = json.loads((refreshed / "original.json").read_text())
        self.assertEqual(receipt["path"], str(custom))
        forged = config.project_dir(self.project) / "forged-hooks" / refreshed.name
        shutil.copytree(refreshed, forged)
        git("config", "core.hooksPath", str(forged), cwd=self.repo)
        self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "conflict")
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(self.repo)
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        self.assertEqual(json.loads((refreshed / "original.json").read_text()), receipt)
