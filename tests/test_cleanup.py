"""cleanup_after_done removes only merged worktrees nobody owns: not a running task's, not a locked one (real git)."""
import os
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-cleanup-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch  # noqa: E402
from tests.physical_fixture import helper_record  # noqa: E402


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


class TestCleanupScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        cls.repo = Path(_TMP) / "repo"; cls.repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        os.environ.update(env)
        git("init", "-q", "-b", "main", cwd=cls.repo); (cls.repo / "f").write_text("1")
        (cls.repo / ".gitignore").write_text(".claude/\n")
        git("add", "-A", cwd=cls.repo); git("commit", "-qm", "init", cwd=cls.repo)
        bare = Path(_TMP) / "origin.git"; git("init", "-q", "--bare", str(bare), cwd=cls.repo)
        git("remote", "add", "origin", str(bare), cwd=cls.repo); git("push", "-q", "origin", "main", cwd=cls.repo)
        config.save_projects({"altitude": {"name": "altitude", "path": str(cls.repo)}})
        for name in ("running-one", "locked-one", "finished-one"):
            git("worktree", "add", "-q", "-b", f"worktree-{name}", f".claude/worktrees/{name}", "origin/main", cwd=cls.repo)
        git("worktree", "lock", ".claude/worktrees/locked-one", cwd=cls.repo)
        S.task_dir("altitude", "running-one").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "running-one", "title": "running-one", "created": S.now(), "updated": S.now(), "state": "running", "worktree": str(cls.repo / ".claude/worktrees/running-one")})

    def test_only_unowned_merged_worktrees_go(self):
        engines.claude_rm, engines.claude_agents = (lambda aid: "removed"), (lambda: [])
        done = {"slug": "finished-one", "state": "done", "agent_id": "x", "worktree": str(self.repo / ".claude/worktrees/finished-one")}
        notes = dispatch.cleanup_after_done("altitude", done)
        left = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertIn("running-one", left, notes)   # zero-commit branch is "merged" but a running task owns it
        self.assertIn("locked-one", left, notes)
        self.assertNotIn("finished-one", left, notes)
        self.assertIn("removed merged worktree finished-one", notes)

    def test_unreadable_session_list_removes_nothing(self):
        git("worktree", "add", "-q", "-b", "worktree-orphan", ".claude/worktrees/orphan", "origin/main", cwd=self.repo)
        def boom(): raise RuntimeError("claude agents down")
        engines.claude_rm, engines.claude_agents = (lambda aid: "removed"), boom
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.cleanup_after_done(
                "altitude",
                {"slug": "z", "title": "z", "state": "done", "agent_id": "x", "updated": S.now()},
            )
        self.assertIn("orphan", git("worktree", "list", "--porcelain", cwd=self.repo))
        self.assertTrue(any("skipped worktree cleanup" in n for n in notes), notes)
        fault.assert_called_once()


class TestSquashEquivalentCleanup(unittest.TestCase):
    def make_squashed_worktree(self, slug):
        root = Path(tempfile.mkdtemp(prefix="altitude-real-squash-cleanup-"))
        repo = root / "repo"; repo.mkdir()
        bare = root / "origin.git"
        project = f"squash-project-{slug}"
        git("init", "-q", "-b", "main", cwd=repo)
        (repo / ".gitignore").write_text(".claude/\n")
        (repo / "doc.md").write_text("before\n")
        git("add", "-A", cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "base", cwd=repo)
        git("init", "-q", "--bare", str(bare), cwd=repo)
        git("remote", "add", "origin", str(bare), cwd=repo)
        git("push", "-q", "origin", "main", cwd=repo)

        owned = repo / ".claude" / "worktrees" / slug
        unrelated = repo / ".claude" / "worktrees" / "unrelated"
        branch = f"worktree-{slug}"
        git("worktree", "add", "-q", "-b", branch, str(owned), "origin/main", cwd=repo)
        git("worktree", "add", "-q", "-b", "worktree-unrelated", str(unrelated), "origin/main", cwd=repo)
        (owned / "doc.md").write_text("after\n")
        git("add", "doc.md", cwd=owned)
        git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "task change", cwd=owned)
        self.assertNotEqual(git("rev-parse", branch, cwd=repo), git("rev-parse", "main", cwd=repo))

        git("merge", "--squash", branch, cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "squash task", cwd=repo)
        git("push", "-q", "origin", "main", cwd=repo)
        ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=repo)
        self.assertEqual(ancestry.returncode, 1)

        config.save_projects({project: {"name": project, "path": str(repo)}})
        return project, repo, owned, unrelated, branch

    def test_real_squash_removes_only_the_exact_owned_worktree(self):
        slug = "squash-task"
        project, repo, owned, unrelated, branch = self.make_squashed_worktree(slug)
        task = {"slug": slug, "state": "done", "l2_engine": "codex", "worktree": str(owned), "branch": branch,
                "verified": {"verdict": "ok", "prs": [123]}}
        with mock.patch.object(dispatch, "_merged_pr_receipt", return_value=(True, "verified merged PR #123")):
            notes = dispatch.cleanup_after_done(project, task)

        listed = git("worktree", "list", "--porcelain", cwd=repo)
        self.assertNotIn(str(owned), listed, notes)
        self.assertIn(str(unrelated), listed, notes)
        self.assertNotIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))
        self.assertTrue(any("removed merged worktree squash-task" in note for note in notes), notes)

    def test_real_dirty_tracked_and_untracked_squash_worktrees_are_preserved(self):
        for kind in ("tracked", "untracked"):
            with self.subTest(kind=kind):
                slug = f"dirty-{kind}"
                project, repo, owned, _unrelated, branch = self.make_squashed_worktree(slug)
                if kind == "tracked":
                    (owned / "doc.md").write_text("local edit after publication\n")
                    preserved = owned / "doc.md"
                else:
                    preserved = owned / "local-notes.txt"
                    preserved.write_text("keep me\n")
                task = {"slug": slug, "state": "done", "l2_engine": "codex", "worktree": str(owned), "branch": branch,
                        "verified": {"verdict": "ok", "prs": [123]}}

                with mock.patch.object(dispatch, "_merged_pr_receipt", return_value=(True, "verified merged PR #123")):
                    notes = dispatch.cleanup_after_done(project, task)

                self.assertIn(str(owned), git("worktree", "list", "--porcelain", cwd=repo), notes)
                self.assertIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))
                self.assertTrue(preserved.exists(), notes)
                self.assertTrue(any("tracked or untracked changes" in note for note in notes), notes)


class TestCapturedL1Cleanup(unittest.TestCase):
    def make_l1(self, slug):
        root = Path(tempfile.mkdtemp(prefix="altitude-real-l1-cleanup-"))
        repo = root / "repo"; repo.mkdir()
        bare = root / "origin.git"
        project = f"l1-cleanup-{slug}"
        git("init", "-q", "-b", "main", cwd=repo)
        (repo / ".gitignore").write_text("*.ignored\n__pycache__/\n.claude/\n")
        (repo / "doc.md").write_text("before\n")
        git("add", "-A", cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "base", cwd=repo)
        git("init", "-q", "--bare", str(bare), cwd=repo)
        git("remote", "add", "origin", str(bare), cwd=repo)
        git("push", "-q", "origin", "main", cwd=repo)
        config.save_projects({project: {"name": project, "path": str(repo)}})
        parent_sha = git("rev-parse", "HEAD", cwd=repo).strip()
        record = helper_record(project, slug, terminal="complete", request_seed="cleanup", parent_sha=parent_sha)
        worktree = Path(record["preparation"]["worktree"])
        branch = record["preparation"]["branch"]
        git("worktree", "add", "-q", "-b", branch, str(worktree), "origin/main", cwd=repo)
        task = {"slug": slug, "state": "done", "l2_engine": "codex"}
        S.task_dir(project, slug).mkdir(parents=True, exist_ok=True)
        S.save_task(project, {**task, "created": S.now(), "updated": S.now()})
        directory = S.task_dir(project, slug) / "l1"
        directory.mkdir(parents=True, exist_ok=True)
        S.write_json(directory / f"{record['name']}.json", record)
        return project, repo, worktree, branch, task

    def capture_record(self, project, slug, worktree, *, include_untracked=True):
        (worktree / "doc.md").write_text("captured\n")
        changed = ["doc.md"]
        if include_untracked:
            (worktree / "new.txt").write_text("captured new file\n")
            git("add", "-N", "--", "new.txt", cwd=worktree)
            changed.append("new.txt")
        patch_text = git("diff", "--binary", "--no-ext-diff", "HEAD", "--", *changed, cwd=worktree)
        if include_untracked:
            git("reset", "-q", "HEAD", "--", "new.txt", cwd=worktree)
        record_path = next((S.task_dir(project, slug) / "l1").glob("helper-*.json"))
        record = S.read_json(record_path)
        record["result"]["patch"] = patch_text
        record["physical"]["receipts"]["result_observed"]["sha256"] = hashlib.sha256(
            S._canonical_json(record["result"])).hexdigest()  # noqa: SLF001
        S.write_json(record_path, record)
        return patch_text

    def test_real_dirty_l1_is_removed_only_when_current_diff_exactly_matches_its_patch(self):
        slug = "captured-exact"
        project, repo, worktree, branch, task = self.make_l1(slug)
        self.capture_record(project, slug, worktree)

        notes = dispatch.cleanup_after_done(project, task)

        self.assertNotIn(str(worktree), git("worktree", "list", "--porcelain", cwd=repo), notes)
        self.assertNotIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))

    def test_real_l1_with_a_post_capture_edit_is_preserved(self):
        slug = "captured-then-edited"
        project, repo, worktree, branch, task = self.make_l1(slug)
        self.capture_record(project, slug, worktree)
        (worktree / "later.txt").write_text("not in captured patch\n")

        notes = dispatch.cleanup_after_done(project, task)

        self.assertIn(str(worktree), git("worktree", "list", "--porcelain", cwd=repo), notes)
        self.assertIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))
        self.assertTrue((worktree / "later.txt").exists())
        self.assertTrue(any("no longer matches its exact captured patch" in note for note in notes), notes)

    def test_real_ignored_file_is_preserved(self):
        slug = "ignored-data"
        project, repo, worktree, branch, task = self.make_l1(slug)
        (worktree / "important.ignored").write_text("keep\n")

        notes = dispatch.cleanup_after_done(project, task)

        self.assertIn(str(worktree), git("worktree", "list", "--porcelain", cwd=repo), notes)
        self.assertIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))
        self.assertTrue((worktree / "important.ignored").exists())
        self.assertTrue(any("worktree has ignored data" in note for note in notes), notes)

    def test_real_ignored_python_bytecode_is_explicitly_disposable(self):
        slug = "ignored-bytecode"
        project, repo, worktree, branch, task = self.make_l1(slug)
        bytecode = worktree / "pkg" / "__pycache__" / "module.cpython-312.pyc"
        bytecode.parent.mkdir(parents=True)
        bytecode.write_bytes(b"generated")

        notes = dispatch.cleanup_after_done(project, task)

        self.assertNotIn(str(worktree), git("worktree", "list", "--porcelain", cwd=repo), notes)
        self.assertNotIn(f"refs/heads/{branch}", git("show-ref", "--heads", cwd=repo))


class TestSelfDeploy(unittest.TestCase):
    def test_pull_after_done_fast_forwards_and_flags_code_changes(self):
        repo = TestCleanupScope.repo
        config.save_projects({"altitude": {"name": "altitude", "path": str(repo), "self_deploy": True}})
        other = Path(_TMP) / "other"; git("clone", "-q", "-b", "main", str(Path(_TMP) / "origin.git"), str(other), cwd=_TMP)
        (other / "altitude").mkdir(exist_ok=True); (other / "altitude" / "x.py").write_text("# new\n")
        (other / "hooks").mkdir(exist_ok=True); (other / "hooks" / "h.py").write_text("# hook\n")
        git("add", "-A", cwd=other); git("commit", "-qm", "code + hook", cwd=other); git("push", "-q", "origin", "main", cwd=other)
        S.task_dir("altitude", "landed").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "landed", "title": "landed", "state": "done",
                                      "l2_engine": "codex", "created": S.now(), "updated": S.now()})
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((repo / "hooks" / "h.py").exists(), notes)          # hooks deploy by the pull itself
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pend.get("files"), ["altitude/x.py"], notes)       # python needs a restart: flagged, not done
        self.assertTrue(any("restart pending" in n for n in notes), notes)
        self.assertEqual(dispatch.pull_after_done("altitude", {"slug": "landed"}), [])  # nothing new → silent

    def test_not_self_deploy_projects_are_untouched(self):
        config.save_projects({"other": {"name": "other", "path": str(Path(_TMP) / "nowhere")}})
        self.assertEqual(dispatch.pull_after_done("other", {"slug": "x"}), [])

    def test_self_deploy_refuses_an_ahead_main(self):
        repo = TestCleanupScope.repo
        config.save_projects({"altitude": {"name": "altitude", "path": str(repo), "self_deploy": True}})
        (repo / "direct.txt").write_text("must not deploy\n")
        git("add", "direct.txt", cwd=repo)
        git("commit", "-qm", "direct main commit", cwd=repo)
        head = git("rev-parse", "HEAD", cwd=repo).strip()

        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})

        self.assertEqual(git("rev-parse", "HEAD", cwd=repo).strip(), head)
        self.assertTrue(any("self-deploy refused" in note for note in notes), notes)
        fault.assert_called_once()


if __name__ == "__main__":
    unittest.main()
