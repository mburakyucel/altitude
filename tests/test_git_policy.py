"""Repository policy tests use real repositories, refs, hooks, and pushes."""
import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.support import git, make_repo
from altitude import config, git_policy


class TestGitPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="alt-git-policy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        installation = mock.patch.object(config, "REPO", self.tmp / "installation")
        installation.start()
        self.addCleanup(installation.stop)
        self.repo = make_repo(self.tmp / "repo")
        self.remote = self.tmp / "origin.git"
        self.configure(self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.remote)   # clones start on main

    def git(self, *args, check=True):
        """Unlike `support.git`, keeps a refusal: the policy hooks are under test here."""
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, f"git {' '.join(args)}: {result.stderr or result.stdout}")
        return result

    def configure(self, repo):
        git("config", "user.email", "test@example.invalid", cwd=repo)
        git("config", "user.name", "Test User", cwd=repo)
        git("config", "commit.gpgsign", "false", cwd=repo)

    def commit_file(self, name, content, message, *, no_verify=False):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git("add", name)
        args = ["commit", "-q"]
        if no_verify:
            args.append("--no-verify")
        args.extend(["-m", message])
        self.git(*args)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def advance_remote(self):
        other = self.tmp / f"other-{len(list(self.tmp.glob('other-*')))}"
        git("clone", "-q", str(self.remote), str(other), cwd=self.tmp)
        self.configure(other)
        marker = other / "remote.txt"
        marker.write_text(marker.read_text() + "next\n" if marker.exists() else "next\n")
        git("add", "remote.txt", cwd=other)
        git("commit", "-q", "-m", "remote advance", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        return git("rev-parse", "HEAD", cwd=other).strip()

    def trace_reference_transactions(self):
        """Record real Git input, then run the unchanged tracked hook executable."""
        source = Path(__file__).resolve().parent.parent / "hooks"
        hooks = self.tmp / "hooks"
        hooks.mkdir()
        trace = self.tmp / "transactions.jsonl"
        trace.touch()
        for name in git_policy.REQUIRED_HOOKS:
            if name != "reference-transaction":
                (hooks / name).symlink_to(source / name)
        hook = hooks / "reference-transaction"
        hook.write_text(
            "#!/usr/bin/env python3\nimport json, subprocess, sys\n"
            "data = sys.stdin.read()\n"
            f"with open({str(trace)!r}, 'a') as f:\n"
            "    f.write(json.dumps([sys.argv[1], data]) + '\\n')\n"
            f"raise SystemExit(subprocess.run([{str(source / hook.name)!r}, *sys.argv[1:]], "
            "input=data, text=True).returncode)\n"
        )
        hook.chmod(0o755)
        git_policy.install_hooks(self.repo, hooks)
        return trace

    def assert_packing_preserves_lagging_main(self, *command, prune=True):
        trace = self.trace_reference_transactions()
        ref = "refs/heads/main"
        for _ in range(2):  # First pack, then replace the stale packed entry after a fast-forward.
            tip = self.git("rev-parse", ref).stdout.strip()
            remote = self.advance_remote()
            self.git("fetch", "-q", "origin", "main")
            trace.write_text("")
            self.git(*command)
            self.assertEqual(self.git("rev-parse", ref).stdout.strip(), tip)
            self.assertIn(f"{tip} {ref}\n", (self.repo / ".git/packed-refs").read_text())
            self.assertEqual((self.repo / ".git" / ref).exists(), not prune)
            rows = [(phase, *line.split()) for phase, data in
                    map(json.loads, trace.read_text().splitlines()) for line in data.splitlines()]
            zero = "0" * len(tip)
            self.assertIn(("prepared", zero, tip, ref), rows)
            if prune:
                self.assertIn(("prepared", tip, zero, ref), rows)
                self.assertIn(("committed", tip, zero, ref), rows)
            self.git("merge", "--ff-only", "origin/main")
            self.assertEqual(self.git("rev-parse", ref).stdout.strip(), remote)

    def test_pack_refs_preserves_lagging_main_and_prunes_loose_copy(self):
        self.assert_packing_preserves_lagging_main("pack-refs", "--all")

    def test_pack_refs_no_prune_preserves_lagging_main(self):
        self.assert_packing_preserves_lagging_main("pack-refs", "--all", "--no-prune", prune=False)

    def test_gc_preserves_lagging_main(self):
        self.assert_packing_preserves_lagging_main("gc")

    def test_fetch_automatic_gc_packs_and_prunes_lagging_main(self):
        trace = self.trace_reference_transactions()
        tip = self.git("rev-parse", "main").stdout.strip()
        # repack supplies revision input itself; pack-objects --all waits for inherited stdin.
        self.git("repack", "-a")
        remote = self.advance_remote()
        self.git("fetch", "-q", "origin", "main")
        self.git("repack", "-a")
        self.assertEqual(len(list((self.repo / ".git/objects/pack").glob("*.pack"))), 2)
        # Exceed the loose-ref threshold used by newer Git's GC pack-refs --auto.
        # One setup transaction avoids per-branch hook startup competing with parallel suites.
        subprocess.run(["git", "-C", str(self.repo), "update-ref", "--stdin"],
                       input="".join(f"create refs/heads/packing-{index} {tip}\n" for index in range(32)),
                       text=True, capture_output=True, check=True)
        # Select GC explicitly: newer Git defaults automatic maintenance to geometric repacking.
        self.git("config", "maintenance.strategy", "gc")
        self.git("config", "gc.autoPackLimit", "1")
        self.git("config", "gc.autoDetach", "false")
        trace.write_text("")
        fetched = self.git("fetch", "origin", "main")
        self.assertNotIn("failed", fetched.stderr)
        rows = [line for phase, data in map(json.loads, trace.read_text().splitlines())
                if phase == "prepared" for line in data.splitlines()]
        self.assertIn(f"{'0' * len(tip)} {tip} refs/heads/main", rows)
        self.assertIn(f"{tip} {'0' * len(tip)} refs/heads/main", rows)
        self.assertFalse((self.repo / ".git/refs/heads/main").exists())
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), tip)
        self.git("merge", "--ff-only", "origin/main")
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), remote)

    def test_packing_from_linked_worktree_uses_common_packed_refs(self):
        git_policy.install_hooks(self.repo)
        worktree = self.tmp / "worktree"
        self.git("worktree", "add", "-q", "-b", "topic", str(worktree))
        tip = self.git("rev-parse", "main").stdout.strip()
        remote = self.advance_remote()
        self.git("fetch", "-q", "origin", "main")
        git("pack-refs", "--all", cwd=worktree)
        self.assertFalse((self.repo / ".git/refs/heads/main").exists())
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), tip)
        self.git("merge", "--ff-only", "origin/main")
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), remote)

    def test_protected_ref_deletions_and_moves_remain_blocked_in_each_storage_form(self):
        git_policy.install_hooks(self.repo)
        self.git("checkout", "-q", "-b", "topic")
        unauthorized = self.commit_file("topic.txt", "topic\n", "topic")
        tip = self.git("rev-parse", "main").stdout.strip()
        for storage in ("loose", "both", "packed", "stale-packed"):
            if storage == "both":
                self.git("pack-refs", "--all", "--no-prune")
            elif storage == "packed":
                self.git("pack-refs", "--all")
            elif storage == "stale-packed":
                tip = self.advance_remote()
                self.git("fetch", "-q", "origin", "main")
                self.git("update-ref", "refs/heads/main", tip)
            for command in (("update-ref", "-d", "refs/heads/main"),
                            ("update-ref", "-d", "refs/heads/main", tip),
                            ("update-ref", "--no-deref", "-d", "refs/heads/main", tip),
                            ("branch", "-D", "main"),
                            ("update-ref", "refs/heads/main", unauthorized),
                            ("branch", "-f", "main", unauthorized)):
                with self.subTest(storage=storage, command=command):
                    refused = self.git(*command, check=False)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn("protected branch update blocked", refused.stderr)
                    self.assertEqual(self.git("rev-parse", "main").stdout.strip(), tip)
            with self.subTest(storage=storage, command="transactional delete"):
                refused = subprocess.run(
                    ["git", "-C", str(self.repo), "update-ref", "--stdin"], text=True,
                    input=f"start\ndelete refs/heads/main {tip}\nprepare\ncommit\n", capture_output=True,
                )
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("protected branch update blocked", refused.stderr)
                self.assertEqual(self.git("rev-parse", "main").stdout.strip(), tip)

    def test_inspection_does_not_fetch_and_reports_local_commits_oldest_first(self):
        initial = self.git("rev-parse", "HEAD").stdout.strip()
        self.advance_remote()
        stale = git_policy.inspect_repository(self.repo)
        self.assertTrue(stale.determinate)
        self.assertEqual(stale.head, initial)
        self.assertEqual(stale.origin_sha, initial)
        self.assertEqual((stale.ahead, stale.behind), (0, 0))

        self.git("reset", "--hard", "-q", "HEAD")
        first = self.commit_file("one.txt", "one\n", "local one")
        second = self.commit_file("two.txt", "two\n", "local two")
        state = git_policy.inspect_repository(self.repo)
        self.assertEqual((state.ahead, state.behind), (2, 0))
        self.assertEqual(state.local_only_shas, (first, second))
        self.assertEqual(state.oldest_local_sha, first)
        self.assertEqual(state.as_dict()["local_only_shas"], (first, second))

    def test_missing_origin_is_indeterminate_and_capture_refuses(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        state = git_policy.inspect_repository(self.repo)
        self.assertFalse(state.determinate)
        self.assertIn("missing origin/main", state.error)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "missing origin/main"):
            git_policy.capture_origin_sha(self.repo)

    def test_fetch_exact_base_accepts_equal_and_refuses_behind_dirty_and_ahead(self):
        expected = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(git_policy.fetch_and_require_exact_base(self.repo), expected)

        self.advance_remote()
        with self.assertRaisesRegex(git_policy.GitPolicyError, "behind"):
            git_policy.fetch_and_require_exact_base(self.repo)
        self.git("merge", "--ff-only", "origin/main")

        (self.repo / "dirty.txt").write_text("dirty\n")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "uncommitted"):
            git_policy.fetch_and_require_exact_base(self.repo)
        (self.repo / "dirty.txt").unlink()

        self.commit_file("ahead.txt", "ahead\n", "local ahead")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "ahead"):
            git_policy.fetch_and_require_exact_base(self.repo)

    def test_service_preflight_allows_equal_or_behind_and_refuses_other_states(self):
        equal = git_policy.service_preflight(self.repo)
        self.assertEqual((equal.ahead, equal.behind), (0, 0))

        self.advance_remote()
        self.git("fetch", "-q", "origin", "main")
        behind = git_policy.service_preflight(self.repo)
        self.assertEqual((behind.ahead, behind.behind), (0, 1))

        self.commit_file("local.txt", "local\n", "local divergence")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "diverged"):
            git_policy.service_preflight(self.repo)

    def test_install_hooks_is_idempotent_and_refuses_an_existing_different_path(self):
        expected = Path(__file__).resolve().parent.parent / "hooks"
        with self.assertRaisesRegex(git_policy.GitPolicyError, "not installed"):
            git_policy.require_hooks_installed(self.repo)
        first = git_policy.install_hooks(self.repo)
        second = git_policy.install_hooks(self.repo)
        self.assertEqual(first, expected.resolve())
        self.assertEqual(second, first)
        self.assertEqual(git_policy.require_hooks_installed(self.repo), first)
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(), str(first))

        self.git("config", "--local", "core.hooksPath", ".git/other-hooks")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "refusing to overwrite"):
            git_policy.install_hooks(self.repo)

    def custom_hooks(self, directory=None):
        directory = directory or self.tmp / "custom-hooks"
        directory.mkdir(exist_ok=True)
        hook = directory / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        return directory

    def test_default_and_inherited_hooks_are_preserved_until_explicit_combination(self):
        default = self.custom_hooks(self.repo / ".git/hooks")
        before = (default / "pre-commit").read_bytes()
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        self.assertTrue(observed["can_combine"])
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "ownership changed"):
            git_policy.repair_hooks(self.repo, combine=True, expected="old observation")
        result = git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual((result["status"], result["action"]), ("ready", "combined"))
        self.assertEqual((default / "pre-commit").read_bytes(), before)
        self.assertEqual(git_policy.repair_hooks(self.repo)["action"], "reused")
        git_policy.require_hooks_installed(self.repo)
        self.git("config", "--unset", "core.hooksPath")
        global_file = self.tmp / "global.gitconfig"
        custom = self.custom_hooks()
        global_file.write_text(f'[core]\n\thooksPath = {custom}\n')
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_file)}):
            observed = git_policy.inspect_hooks(self.repo)
            self.assertEqual(observed["status"], "conflict")
            self.assertEqual(observed["hooks_path"], str(custom))
            with self.assertRaises(git_policy.GitPolicyError):
                git_policy.repair_hooks(self.repo)
        self.assertEqual(global_file.read_text(), f'[core]\n\thooksPath = {custom}\n')

    def test_worktree_hook_override_is_inspected_and_not_replaced_implicitly(self):
        git_policy.install_hooks(self.repo)
        self.git("config", "extensions.worktreeConfig", "true")
        custom = self.custom_hooks()
        self.git("config", "--worktree", "core.hooksPath", str(custom))
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(self.repo)
        result = git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(result["action"], "combined")
        self.assertEqual(self.git("config", "--worktree", "--get", "core.hooksPath").stdout.strip(), result["hooks_path"])
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(), str(git_policy._active_hooks()))

    def test_combination_preserves_arguments_input_environment_and_both_rejections(self):
        custom = self.custom_hooks()
        trace = self.tmp / "hooks.jsonl"
        for name in ("pre-push", "reference-transaction", "post-checkout"):
            hook = custom / name
            hook.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                            f"with open({str(trace)!r}, 'a') as output:\n"
                            f"    output.write(json.dumps([{name!r}, sys.argv[1:], "
                            "sys.stdin.read(), os.getcwd(), os.environ.get('HOOK_FIXTURE')]) + '\\n')\n"
                            "raise SystemExit(int(os.environ.get('HOOK_REJECT', '0')))\n")
            hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(custom))
        observed = git_policy.inspect_hooks(self.repo)
        path = Path(git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])["hooks_path"])
        env = {**os.environ, "HOOK_FIXTURE": "kept"}
        tip = self.git("rev-parse", "HEAD").stdout.strip()
        for name, args, data in (("pre-push", ["origin", "fixture"], f"refs/heads/topic {tip} refs/heads/topic {tip}\n"),
                                 ("reference-transaction", ["prepared"], f"{tip} {tip} refs/heads/topic\n"),
                                 ("post-checkout", [tip, tip, "1"], "other event input\n")):
            with self.subTest(name=name):
                result = subprocess.run([str(path / name), *args], input=data, text=True,
                                        cwd=self.repo, env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(trace.read_text().splitlines()[-1]),
                                 [name, args, data, str(self.repo), "kept"])
        denied = subprocess.run([str(path / "pre-push"), "origin", "fixture"],
                                input=f"refs/heads/topic {tip} refs/heads/main {tip}\n", text=True,
                                cwd=self.repo, env=env, capture_output=True)
        self.assertNotEqual(denied.returncode, 0)
        rejected = subprocess.run([str(path / "pre-push"), "origin", "fixture"],
                                  input=f"refs/heads/topic {tip} refs/heads/topic {tip}\n", text=True,
                                  cwd=self.repo, env={**env, "HOOK_REJECT": "17"}, capture_output=True)
        self.assertEqual(rejected.returncode, 17)
        # Actual Git still invokes the protected guard, while other original events remain active.
        (self.repo / "blocked.txt").write_text("blocked\n")
        self.git("add", "blocked.txt")
        self.assertIn("pre-commit", self.git("commit", "-m", "blocked", check=False).stderr)
        (path / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "conflict")
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(self.repo)

    def test_combine_refuses_changed_original_and_retries_failed_config_write(self):
        custom = self.custom_hooks()
        self.git("config", "core.hooksPath", str(custom))
        observed = git_policy.inspect_hooks(self.repo)
        (custom / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "ownership changed"):
            git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        observed = git_policy.inspect_hooks(self.repo)
        real = git_policy._run

        def denied(repo, *args, **kwargs):
            if args[:3] == ("config", "--local", "core.hooksPath"):
                raise git_policy.GitPolicyError("configuration permission denied")
            return real(repo, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=denied):
            with self.assertRaisesRegex(git_policy.GitPolicyError, "permission denied"):
                git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(self.git("config", "--get", "core.hooksPath").stdout.strip(), str(custom))
        result = git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(result["status"], "ready")

    def test_forged_git_metadata_does_not_supply_operator_consent(self):
        custom = self.custom_hooks()
        saved = {"path": str(custom), "selection": str(custom), "scope": "local",
                 "owner": str(self.repo / ".git"), "guards": str(git_policy._active_hooks()),
                 "approved_fingerprint": "forged", "original_contents": git_policy._hook_contents(custom)}
        digest = hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest()
        forged = self.repo / ".git/altitude-hooks" / digest
        forged.mkdir(parents=True)
        for name in git_policy.HOOK_EVENTS:
            hook = forged / name
            hook.write_text(git_policy._composed_hook(name, custom, git_policy._active_hooks(), saved["original_contents"]))
            hook.chmod(0o755)
        (forged / "original.json").write_text(json.dumps(saved))
        self.git("config", "core.hooksPath", str(forged))
        self.assertEqual(git_policy.inspect_hooks(self.repo)["status"], "conflict")
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(self.repo)
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        self.assertFalse(git_policy._composition_path(self.repo).exists())

    def test_changed_original_requires_fresh_consent_without_recursive_composition(self):
        custom = self.custom_hooks()
        self.git("config", "core.hooksPath", str(custom))
        observed = git_policy.inspect_hooks(self.repo)
        first = Path(git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])["hooks_path"])
        self.assertTrue(first.is_relative_to(config.REPO / ".altitude-source"))
        (custom / "pre-commit").write_text("#!/bin/sh\nexit 7\n")
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        self.assertTrue(observed["can_combine"])
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo)
        # The installed wrapper also refuses a changed original before launching it.
        attempt = subprocess.run([str(first / "pre-commit")], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(attempt.returncode, 1)
        self.assertIn("original hooks changed", attempt.stderr)
        second = Path(git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])["hooks_path"])
        self.assertNotEqual(first, second)
        saved = json.loads((second / "original.json").read_text())
        self.assertEqual(saved["path"], str(custom))
        self.assertEqual(saved["original_contents"], git_policy._hook_contents(custom))
        git_policy.require_hooks_installed(self.repo)
        saved["path"] = str(self.tmp / "forged-original")
        (second / "original.json").write_text(json.dumps(saved))
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(self.repo)

    def test_worktree_consent_cannot_be_reused_by_a_sibling_worktree(self):
        git_policy.install_hooks(self.repo)
        self.git("config", "extensions.worktreeConfig", "true")
        sibling = self.tmp / "sibling"
        self.git("worktree", "add", "-q", "-b", "sibling", str(sibling))
        custom = self.custom_hooks()
        self.git("config", "--worktree", "core.hooksPath", str(custom))
        git("config", "--worktree", "core.hooksPath", str(custom), cwd=sibling)
        own = git_policy.inspect_hooks(self.repo)
        other = git_policy.inspect_hooks(sibling)
        self.assertNotEqual(own["fingerprint"], other["fingerprint"])
        approved = git_policy.repair_hooks(self.repo, combine=True, expected=own["fingerprint"])
        git("config", "--worktree", "core.hooksPath", approved["hooks_path"], cwd=sibling)
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.require_hooks_installed(sibling)
        git_policy.require_hooks_installed(self.repo)

    def test_unsupported_hook_manager_stays_actionable_and_unchanged(self):
        custom = self.custom_hooks()
        (custom / "pre-commit").write_text('#!/bin/sh\ngit config --get core.hooksPath\n')
        self.git("config", "core.hooksPath", str(custom))
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        self.assertFalse(observed["can_combine"])
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(self.git("config", "--get", "core.hooksPath").stdout.strip(), str(custom))

    def test_relative_custom_selection_is_not_redirected_to_one_worktrees_directory(self):
        custom = self.custom_hooks(self.repo / "custom-hooks")
        self.git("config", "core.hooksPath", "custom-hooks")
        observed = git_policy.inspect_hooks(self.repo)
        self.assertEqual(observed["status"], "conflict")
        self.assertFalse(observed["can_combine"])
        self.assertIn("Relative hook paths", observed["detail"])
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.repair_hooks(self.repo, combine=True, expected=observed["fingerprint"])
        self.assertEqual(self.git("config", "--get", "core.hooksPath").stdout.strip(), "custom-hooks")
        self.assertEqual((custom / "pre-commit").read_text(), "#!/bin/sh\nexit 0\n")

    def test_pre_commit_and_pre_merge_hooks_block_main_and_master_but_allow_topic(self):
        initial = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/master", initial)
        self.git("branch", "master", "origin/master")
        git_policy.install_hooks(self.repo)
        (self.repo / "blocked.txt").write_text("blocked\n")
        self.git("add", "blocked.txt")
        blocked = self.git("commit", "-m", "blocked on main", check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("pre-commit on main is blocked", blocked.stderr)

        self.git("checkout", "-q", "-b", "topic")
        allowed = self.git("commit", "-m", "allowed on topic", check=False)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

        self.git("checkout", "-q", "main")
        merge_blocked = self.git("merge", "--no-ff", "topic", "-m", "merge topic", check=False)
        self.assertNotEqual(merge_blocked.returncode, 0)
        self.assertIn("pre-merge-commit on main is blocked", merge_blocked.stderr)
        self.git("merge", "--abort")

        self.git("checkout", "-q", "master")
        (self.repo / "master.txt").write_text("master\n")
        self.git("add", "master.txt")
        master_commit = self.git("commit", "-m", "blocked on master", check=False)
        self.assertNotEqual(master_commit.returncode, 0)
        self.assertIn("pre-commit on master is blocked", master_commit.stderr)
        self.git("reset", "--hard", "-q", "HEAD")
        master_merge = self.git("merge", "--no-ff", "topic", "-m", "merge topic", check=False)
        self.assertNotEqual(master_merge.returncode, 0)
        self.assertIn("pre-merge-commit on master is blocked", master_merge.stderr)
        self.git("merge", "--abort")

        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "integration")
        merge_allowed = self.git("merge", "--no-ff", "topic", "-m", "merge topic", check=False)
        self.assertEqual(merge_allowed.returncode, 0, merge_allowed.stderr)

    def test_pre_push_blocks_main_and_master_and_allows_topic(self):
        git_policy.install_hooks(self.repo)
        self.git("checkout", "-q", "-b", "topic")
        self.commit_file("topic.txt", "topic\n", "topic commit")
        blocked = self.git("push", "origin", "topic:main", check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("pushing refs/heads/main directly is blocked", blocked.stderr)

        master_blocked = self.git("push", "origin", "topic:master", check=False)
        self.assertNotEqual(master_blocked.returncode, 0)
        self.assertIn("pushing refs/heads/master directly is blocked", master_blocked.stderr)

        allowed = self.git("push", "-u", "origin", "topic", check=False)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        remote_topic = git("rev-parse", "refs/heads/topic", cwd=self.remote).strip()
        self.assertEqual(remote_topic, self.git("rev-parse", "topic").stdout.strip())

    def test_reference_transaction_rejects_local_base_moves_but_allows_fetched_remote_head(self):
        git_policy.install_hooks(self.repo)
        self.git("checkout", "-q", "-b", "topic")
        topic = self.commit_file("topic.txt", "topic\n", "topic commit")

        forced = self.git("branch", "-f", "main", "topic", check=False)
        self.assertNotEqual(forced.returncode, 0)
        self.assertIn("protected branch update blocked", forced.stderr)
        self.git("checkout", "-q", "main")
        fast_forward = self.git("merge", "--ff-only", "topic", check=False)
        self.assertNotEqual(fast_forward.returncode, 0)
        self.assertIn("protected branch update blocked", fast_forward.stderr)

        self.git("push", "-q", "origin", "topic")
        git("update-ref", "refs/heads/main", topic, cwd=self.remote)
        self.git("fetch", "-q", "origin", "main")
        allowed = self.git("merge", "--ff-only", "origin/main", check=False)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), topic)


if __name__ == "__main__":
    unittest.main()
