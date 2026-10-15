"""Committed installation inputs and dirty deployments, without providers or user services."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO, SUITE, AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, engines, git_policy, state as S, tasks as T


class LaunchSource(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.fixture_boot_identity()
        self.private_ledgers()
        self.quiet_engines()
        make_repo(self.repo)
        (self.repo / "AGENTS.md").write_text("- Never publish private material.\n")
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-qm", "Project rules", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.installation = self.tmp / "installation" / "repo"
        make_repo(self.installation)
        for directory in ("altitude", "bin", "hooks", "personas", "schemas", "templates", "scripts"):
            shutil.copytree(REPO / directory, self.installation / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.installation / ".gitignore").write_text(".altitude-source/\n.claude/\n__pycache__/\n")
        git("add", "-A", cwd=self.installation)
        git("commit", "-qm", "Installation fixture", cwd=self.installation)
        git("push", "-q", "origin", "main", cwd=self.installation)
        self.head = git("rev-parse", "HEAD", cwd=self.installation).strip()
        self.patch(config, "REPO", self.installation)
        self.patch(config, "SOURCE", self.installation)
        for key in ("PERSONAS", "SCHEMAS", "TEMPLATES", "HOOKS"):
            self.patch(config, key, self.installation / key.lower())
        self.patch(sys.modules["altitude"], "__path__", list(sys.modules["altitude"].__path__))
        for repository in (self.repo, self.installation):
            git_policy.install_hooks(repository)
        self.engine = FakeL2()
        self.engine.install(self)

    def snapshot(self, repository):
        return (git("rev-parse", "HEAD", cwd=repository),
                (repository / ".git/index").read_bytes(),
                git("diff", "--binary", "HEAD", cwd=repository),
                git("ls-files", "--others", "--exclude-standard", cwd=repository))

    def test_fixture_bootstrap_removes_the_source_service_branch_before_config_import(self):
        result = subprocess.run([sys.executable, "-c",
                                 "import tests.support; from altitude import config; "
                                 "import os; print(config.SOURCE_BRANCH); "
                                 "assert 'ALTITUDE_SOURCE_BRANCH' not in os.environ; "
                                 "assert 'NPM_CONFIG_CACHE' not in os.environ; "
                                 "assert 'NPM_CONFIG_STORE_DIR' not in os.environ; "
                                 "from pathlib import Path; "
                                 "assert all(Path(os.environ[k]).is_relative_to(tests.support.SUITE) "
                                 "for k in ('XDG_CACHE_HOME','COREPACK_HOME','npm_config_cache',"
                                 "'npm_config_store_dir','PIP_CACHE_DIR'))"],
                                env={**os.environ, "ALTITUDE_SOURCE_BRANCH": "service-only-branch",
                                     "NPM_CONFIG_CACHE": "/unwritable/npm", "NPM_CONFIG_STORE_DIR": "/unwritable/store",
                                     **{key: "/unwritable/service-cache" for key in
                                        ("XDG_CACHE_HOME", "COREPACK_HOME", "npm_config_cache",
                                         "npm_config_store_dir", "PIP_CACHE_DIR")}},
                                cwd=REPO, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "main")

    def test_committed_inputs_survive_deployment_edits_and_same_owner_resume(self):
        # Project rules are sourced from the task base, not the deployment's staged/working versions.
        git_policy.activate_source()
        source = config.SOURCE
        self.assertEqual(source, self.installation / ".altitude-source" / self.head)
        original = (source / "personas/l2.md").read_text()
        for path in ("personas/l2.md", "templates/brief.md", "hooks/inbox.py", "hooks/pre-commit", "bin/alt"):
            (self.installation / path).write_text("DEPLOYMENT EDIT MUST NOT EXECUTE\n")
        git("add", "personas/l2.md", cwd=self.installation)
        (self.installation / "personas/l2.md").write_text("WORKING VERSION\n")
        (self.installation / "private.txt").write_text("fictional private deployment content\n")
        (self.repo / "AGENTS.md").write_text("- Never use the committed rules.\n")
        git("add", "AGENTS.md", cwd=self.repo)
        (self.repo / "AGENTS.md").write_text("- Never use this working rule either.\n")
        (self.repo / "private.txt").write_text("fictional untracked project content\n")
        before = self.snapshot(self.installation), self.snapshot(self.repo)

        task = T.new(self.project, "Isolated owner", "Implement the assigned request.")
        dispatch.run(self.project, task["slug"])
        launch = self.engine.calls[-1]
        self.assertEqual(launch["persona"].read_text(), original)
        self.assertEqual(launch["persona"].parent.parent, source)
        self.assertIn(str(source / "schemas/report.json"), launch["prompt"])
        self.assertFalse((launch["cwd"] / "private.txt").exists())
        self.assertEqual(shutil.which("alt", path=engines.clean_env()["PATH"]), str(source / "bin/alt"))
        settings = S.read_json(launch["settings"])
        self.assertIn(str(source / "hooks/inbox.py"), json.dumps(settings))

        # The exported CLI and Git hooks import the same committed installation package.
        env = {**engines.clean_env(), **launch["extra_env"]}
        reply = subprocess.run([str(source / "bin/alt"), "task", "reply", "Trusted CLI works."],
                               cwd=launch["cwd"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(reply.returncode, 0, reply.stderr)
        # Stdin forms owners use: `-`, no argument, the task slug first as in the sibling verbs, and `--file -`.
        for number, argv in enumerate((["-"], [], [task["slug"], "-"], [task["slug"]], ["--file", "-"])):
            text = f"Costs $1.20 and 'quotes' stay literal.\nForm {number}.\n"
            reply = subprocess.run([str(source / "bin/alt"), "task", "reply", *argv], input=text,
                                   cwd=launch["cwd"], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(reply.returncode, 0, (argv, reply.stderr))
            self.assertEqual(T.task_messages(self.project, task["slug"])[-1]["text"], text.strip())
        extra = subprocess.run([str(source / "bin/alt"), "task", "reply", "other-task", "-"], input="Stray.\n",
                               cwd=launch["cwd"], env=env, capture_output=True, text=True, timeout=30)
        self.assertIn("optionally after this task's slug", extra.stderr)
        self.assertEqual(len(T.task_messages(self.project, task["slug"])), 6)
        work = launch["cwd"] / "owned.txt"
        work.write_text("Task-owned work\n")
        git("add", "owned.txt", cwd=launch["cwd"])
        committed = subprocess.run(["git", "commit", "-qm", "Owned change"], cwd=launch["cwd"],
                                   env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        work.write_text("Unfinished owned work\n")
        running = S.load_task(self.project, task["slug"])
        self.engine.stop_l2_worker(running["l2_engine"], running["agent_id"], job_root=dispatch.l2_job_root(self.project, task["slug"]))
        T.block(self.project, task["slug"], "Awaiting context", updates={"waiting_on": "l3"})
        T.message(self.project, task["slug"], "l3", "Continue this same owner.")
        with mock.patch.object(git_policy, "fetch_origin", side_effect=AssertionError("resume must not fetch")):
            dispatch.resume(self.project, task["slug"])
        resumed = self.engine.calls[-1]
        self.assertEqual(resumed["session_id"], running["session_id"])
        self.assertEqual(resumed["cwd"], launch["cwd"])
        self.assertEqual(resumed["persona"], source / "personas/l2.md")
        self.assertEqual(work.read_text(), "Unfinished owned work\n")
        self.assertEqual(before, (self.snapshot(self.installation), self.snapshot(self.repo)))
        self.assertEqual((self.installation / "private.txt").read_text(), "fictional private deployment content\n")

    def test_activation_retains_old_sources_and_moves_only_known_guard_installations(self):
        custom = self.tmp / "custom-hooks"
        custom.mkdir()
        git("config", "core.hooksPath", str(custom), cwd=self.repo)
        git_policy.activate_source()
        first = config.SOURCE
        git_policy.require_hooks_installed(self.installation)
        self.assertEqual(git("config", "--get", "core.hooksPath", cwd=self.repo).strip(), str(custom))
        with self.assertRaisesRegex(git_policy.GitPolicyError, "not installed"):
            git_policy.require_hooks_installed(self.repo)
        # A committed persona change activates at startup, retaining files referenced by old workers.
        persona = self.installation / "personas/l2.md"
        persona.write_text(persona.read_text() + "\nCommitted next persona.\n")
        git("add", "personas/l2.md", cwd=self.installation)
        tree = git("write-tree", cwd=self.installation).strip()
        head = git("commit-tree", tree, "-p", self.head, "-m", "Next installation fixture", cwd=self.installation).strip()
        git("push", "-q", "origin", head + ":refs/heads/reviewed-fixture", cwd=self.installation)
        git("update-ref", "refs/heads/main", head, cwd=self.installation.parent / "origin.git")
        git("fetch", "-q", "origin", "main", cwd=self.installation)
        git("update-ref", "refs/heads/main", head, cwd=self.installation)
        git_policy.deploy_source(self.installation, "main")   # Altitude's copy fetches the new commit
        git_policy.activate_source()
        self.assertNotEqual(config.SOURCE, first)
        self.assertTrue(first.is_dir())
        self.assertNotIn("Committed next persona", (first / "personas/l2.md").read_text())
        self.assertIn("Committed next persona", (config.SOURCE / "personas/l2.md").read_text())
        git_policy.require_hooks_installed(self.installation)
        for prefix in ("hooks/", "personas/", "schemas/", "templates/", "scripts/"):
            self.assertEqual(dispatch.activation_component(prefix + "changed"), "backend")

    def install_version(self, message):
        """Commit the installation's files as its next version, fetch it into Altitude's copy and activate it."""
        git("add", "-A", cwd=self.installation)
        tree = git("write-tree", cwd=self.installation).strip()
        head = git("commit-tree", tree, "-p", "HEAD", "-m", message, cwd=self.installation).strip()
        git("push", "-q", "origin", head + ":refs/heads/reviewed-" + head[:8], cwd=self.installation)
        git("update-ref", "refs/heads/main", head, cwd=self.installation.parent / "origin.git")
        git("fetch", "-q", "origin", "main", cwd=self.installation)
        git("update-ref", "refs/heads/main", head, cwd=self.installation)
        git_policy.deploy_source(self.installation, "main")
        git_policy.activate_source()
        return config.SOURCE

    def test_a_worker_that_outlives_activation_sends_what_the_activated_altd_reads(self):
        # I-20261015-172004: a worker launched before an activation kept its export's `alt`, which sent `--show` files
        # as paths after altd had come to read their content, and every such request was refused until L3 resumed it.
        # The worker here launches with an export whose `alt` and edit hook send what this altd no longer reads.
        alt, hook = self.installation / "bin/alt", self.installation / "hooks/edit_count.py"
        current_alt, current_hook = alt.read_text(), hook.read_text()
        alt.write_text(current_alt.replace('"show": _sent_show(args, "task review run")', '"show": args.show'))
        hook.write_text(current_hook.replace('{"path": ', '{"file": '))
        self.assertNotEqual((alt.read_text(), hook.read_text()), (current_alt, current_hook))
        older = self.install_version("Older worker client")
        task = T.new(self.project, "Show the spec", "Ask for a proposal review of the spec.")
        dispatch.run(self.project, task["slug"])
        launch = self.engine.calls[-1]
        env = {**engines.clean_env(), **launch["extra_env"]}   # what the worker keeps for its whole session
        command = shutil.which("alt", path=env["PATH"])
        self.assertEqual(command, str(older / "bin/alt"))
        (launch["cwd"] / "docs").mkdir(exist_ok=True)
        (launch["cwd"] / "docs/spec.md").write_text("# Spec\n")
        review = [command, "task", "review", "run", "--review-id", "review", "--show", "docs/spec.md"]
        edit = [sys.executable, "-B", str(older / "hooks/edit_count.py")]
        edited = json.dumps({"tool_input": {"file_path": "docs/spec.md"}})
        counts = S.counts_path(self.project, S.load_task(self.project, task["slug"]))

        def run(argv, stdin=""):
            return subprocess.run(argv, cwd=launch["cwd"], env=env, input=stdin, capture_output=True, text=True,
                                  timeout=60)

        with mock.patch("altitude.reviews.run", return_value={"id": "review", "state": "completed"}) as reviewed:
            refused = run(review)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Sent files are a map of paths to their content.", refused.stderr)
            self.assertIn("did not count this edit", run(edit, edited).stderr)
            self.assertFalse(counts.exists())

            alt.write_text(current_alt)
            hook.write_text(current_hook)
            activated = self.install_version("Activated altd and client")
            self.assertNotEqual(activated, older)
            self.assertEqual(shutil.which("alt", path=env["PATH"]), command)  # the worker's own PATH is unchanged
            accepted = run(review)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)["state"], "completed")
            self.assertEqual(reviewed.call_args.kwargs["show"], {"docs/spec.md": b"# Spec\n"})
            counted = run(edit, edited)
            self.assertEqual(counted.returncode, 0, counted.stderr)
            self.assertEqual(S.read_json(counts), {"edits": 1, "files": ["docs/spec.md"]})
        self.assertTrue(older.is_dir())
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

    def test_dirty_startup_cannot_activate_uncommitted_installation_inputs(self):
        (self.installation / "personas/l2.md").write_text("uncommitted persona\n")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "uncommitted"):
            git_policy.activate_source()
        self.assertFalse((self.installation / ".altitude-source").exists())

    def test_unavailable_project_does_not_block_activation_or_other_task_progress(self):
        self.register("missing-project", path=self.tmp / "missing")
        with mock.patch("altitude.incidents.system_fault") as fault:
            git_policy.activate_source()
        fault.assert_called_once()
        self.assertEqual(fault.call_args.kwargs, {"project": "missing-project"})
        git_policy.require_hooks_installed(self.installation)
        task = T.new(self.project, "Other project progresses", "Continue independently.")
        dispatch.run(self.project, task["slug"])
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

    def test_conversation_only_folder_has_no_guard_fault_or_repository_creation(self):
        folder = self.tmp / "conversation-only"
        folder.mkdir()
        self.register("conversation-only", path=folder)
        with mock.patch("altitude.incidents.system_fault") as fault:
            git_policy.activate_source()
        fault.assert_not_called()
        self.assertEqual(list(folder.iterdir()), [])

    def test_activation_preserves_daemon_owned_hook_consent_while_refreshing_wrappers(self):
        custom = self.tmp / "custom-hooks"
        custom.mkdir()
        hook = custom / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        git("config", "core.hooksPath", str(custom), cwd=self.repo)
        observed = git_policy.inspect_hooks(self.repo)
        initial = Path(git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])["hooks_path"])
        receipt = json.loads((initial / "original.json").read_text())
        git_policy.activate_source()
        current = git_policy.require_hooks_installed(self.repo)
        self.assertNotEqual(initial, current)
        refreshed = json.loads((current / "original.json").read_text())
        for key in ("approved_fingerprint", "owner", "original_contents", "path", "selection", "scope"):
            self.assertEqual(receipt[key], refreshed[key])
        self.assertEqual(refreshed["guards"], str(self.installation / ".altitude-source/current/hooks"))
        self.assertEqual(json.loads((initial / "original.json").read_text()), receipt)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(git_policy.repair_hooks(self.repo)["action"], "reused")

    def test_failed_hook_update_keeps_retry_available_and_affected_launch_refused(self):
        real_run = git_policy._run

        def fail_config(repository, *args, **kwargs):
            if repository == self.repo and args[:3] == ("config", "--local", "core.hooksPath"):
                raise git_policy.GitPolicyError("fixture project configuration unavailable")
            return real_run(repository, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=fail_config), \
                mock.patch("altitude.incidents.system_fault") as fault:
            git_policy.activate_source()
        self.assertEqual(fault.call_args.kwargs, {"project": self.project})
        task = T.new(self.project, "Affected project waits", "Use only trusted hooks.")
        with mock.patch.object(git_policy, "_run", side_effect=fail_config):
            with self.assertRaisesRegex(T.TransitionError, "configuration unavailable"):
                dispatch.run(self.project, task["slug"])
        self.assertEqual(self.engine.calls, [])
        dispatch.resume(self.project, task["slug"])
        dispatch.run(self.project, task["slug"])
        git_policy.require_hooks_installed(self.repo)
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")

    def test_interrupted_installation_hook_update_can_retry_after_source_switch(self):
        real_run = git_policy._run

        def fail_config(repository, *args, **kwargs):
            if repository == self.installation and args[:3] == ("config", "--local", "core.hooksPath"):
                raise git_policy.GitPolicyError("fixture installation configuration unavailable")
            return real_run(repository, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=fail_config):
            with self.assertRaisesRegex(git_policy.GitPolicyError, "configuration unavailable"):
                git_policy.activate_source()
        self.assertTrue((self.installation / ".altitude-source/current").is_dir())
        git_policy.activate_source()
        git_policy.require_hooks_installed(self.installation)

    def test_skipped_source_upgrade_repairs_old_owned_guards_without_touching_project_work(self):
        git_policy.activate_source()
        first = config.SOURCE
        git("config", "core.hooksPath", str(first / "hooks"), cwd=self.repo)
        task = T.new(self.project, "Keep task context", "Continue after guard maintenance.", hold_merge="Review required")
        dispatch.run(self.project, task["slug"])
        running = S.load_task(self.project, task["slug"])
        worktree = Path(running["worktree"])
        (worktree / "unfinished.txt").write_text("Preserve unfinished work.\n")
        self.engine.stop_l2_worker(running["l2_engine"], running["agent_id"],
                                  job_root=dispatch.l2_job_root(self.project, task["slug"]))
        T.block(self.project, task["slug"], "Wait for maintenance", updates={"waiting_on": "l3"})
        before = S.load_task(self.project, task["slug"])
        real = git_policy._run

        def miss_project(repository, *args, **kwargs):
            if repository == self.repo and args[:3] == ("config", "--local", "core.hooksPath"):
                raise git_policy.GitPolicyError("fixture missed guard upgrade")
            return real(repository, *args, **kwargs)

        for version in ("second", "third"):
            persona = self.installation / "personas/l2.md"
            persona.write_text(persona.read_text() + f"\n{version} installed version.\n")
            git("add", "personas/l2.md", cwd=self.installation)
            tree = git("write-tree", cwd=self.installation).strip()
            head = git("commit-tree", tree, "-p", "HEAD", "-m", version, cwd=self.installation).strip()
            git("push", "-q", "origin", head + ":refs/heads/reviewed-" + version, cwd=self.installation)
            git("update-ref", "refs/heads/main", head, cwd=self.installation.parent / "origin.git")
            git("fetch", "-q", "origin", "main", cwd=self.installation)
            git("update-ref", "refs/heads/main", head, cwd=self.installation)
            git_policy.deploy_source(self.installation, "main")
            if version == "second":
                with mock.patch.object(git_policy, "_run", side_effect=miss_project), \
                        mock.patch("altitude.incidents.system_fault") as fault:
                    git_policy.activate_source()
                self.assertEqual(fault.call_args.kwargs, {"project": self.project})
                self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "stale")
            else:
                git_policy.activate_source()
        self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "ready")
        self.assertEqual(git("config", "--get", "core.hooksPath", cwd=self.repo).strip(),
                         str(self.installation / ".altitude-source/current/hooks"))
        self.assertTrue(first.is_dir())
        after = S.load_task(self.project, task["slug"])
        for key in ("session_id", "worktree", "attempt", "hold_merge", "questions", "state"):
            self.assertEqual(before.get(key), after.get(key))
        self.assertEqual((worktree / "unfinished.txt").read_text(), "Preserve unfinished work.\n")
        git("config", "extensions.worktreeConfig", "true", cwd=self.repo)
        git("config", "--worktree", "core.hooksPath", str(first / "hooks"), cwd=worktree)
        self.assertEqual(git_policy.inspect_hooks(worktree)["status"], "stale")
        T.message(self.project, task["slug"], "l3", "The current Git guards are verified; continue.")
        dispatch.resume(self.project, task["slug"])
        self.assertEqual(self.engine.calls[-1]["session_id"], running["session_id"])
        git_policy.require_hooks_installed(worktree)
        self.assertEqual(git("config", "--worktree", "--get", "core.hooksPath", cwd=worktree).strip(),
                         str(self.installation / ".altitude-source/current/hooks"))


PROBE = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import door
door.follow_activation(__file__)
print(Path(__file__).resolve().parent.parent.name, sys.argv[1:], sys.stdin.read())
"""


class FollowActivation(unittest.TestCase):
    """A worker's `alt` and hooks run the activated version, in a source deployment's exports and in installed
    versions alike, and every other process runs its own."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="follow-", dir=SUITE))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def version(self, folder: Path, *, release: bool = False) -> Path:
        (folder / "hooks").mkdir(parents=True)
        (folder / "hooks/probe.py").write_text(PROBE)
        (folder / "altitude").symlink_to(REPO / "altitude", target_is_directory=True)
        if release:
            (folder / "release.json").write_text("{}")
        return folder / "hooks/probe.py"

    def probe(self, script: Path, **env: str) -> str:
        worker = {"PATH": os.environ["PATH"], "ALTITUDE_HOME": str(self.tmp / "home"), "ALTITUDE_ACTOR": "l2", **env}
        result = subprocess.run([sys.executable, "-B", str(script), "--flag"], input="checkpoint",
                                env={key: value for key, value in worker.items() if value is not None},
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_a_worker_command_runs_the_activated_version(self):
        for layout, root, release in (("source", ".altitude-source", False), ("installed", "versions", True)):
            with self.subTest(layout=layout):
                base = self.tmp / layout
                older = self.version(base / root / "older", release=release)
                self.version(base / root / "newer", release=release)
                current = (base / root if layout == "source" else base) / "current"

                def activate(name):
                    current.unlink(missing_ok=True)
                    current.symlink_to(Path(root, name) if release else name, target_is_directory=True)
                activate("older")
                self.assertEqual(self.probe(older), "older ['--flag'] checkpoint")   # nothing activated since
                activate("newer")
                self.assertEqual(self.probe(older), "newer ['--flag'] checkpoint")
                self.assertEqual(self.probe(older, ALTITUDE_ACTOR=None), "older ['--flag'] checkpoint")
                self.assertEqual(self.probe(older, ALTITUDE_ACTOR="l3"), "older ['--flag'] checkpoint")
                self.assertEqual(self.probe(older, ALTITUDE_DOOR="1"), "older ['--flag'] checkpoint")  # altd's own
                current.unlink()
                self.assertEqual(self.probe(older), "older ['--flag'] checkpoint")

    def test_a_development_checkout_runs_its_own_files(self):
        checkout = self.version(self.tmp / "checkout")
        (self.tmp / "current").symlink_to("checkout", target_is_directory=True)
        self.assertEqual(self.probe(checkout), "checkout ['--flag'] checkpoint")
