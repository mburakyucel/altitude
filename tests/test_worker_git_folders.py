"""Altitude's own Git commands take every task repository path from Altitude's layout, task workers keep the
repository's Git settings, hooks and worktree records read-only, and Altitude's own code comes through its copy of
its repository."""
import os
import subprocess
from pathlib import Path

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import config, dispatch, git_policy, platform, reviews, state as S, tasks as T, validation, verify


class WorkerGitFolders(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.slug = "fix-x"
        self.worktree = add_worktree(self.repo, self.slug)
        self.task = {"slug": self.slug, "title": "Fictional fix", "state": "running",
                     "attempt": 1, "branch": f"worktree-{self.slug}", "worktree": str(self.worktree)}
        S.save_task(self.project, self.task)
        self.ran = self.tmp / "outside-program-ran"
        # A fictional repository outside the layout whose settings run a program.
        self.outside = self.tmp / "outside"
        git("init", "-q", str(self.outside), cwd=self.tmp)
        program = self.tmp / "program"
        program.write_text(f"#!/bin/sh\ntouch {self.ran}\n")
        program.chmod(0o755)
        git("config", "core.fsmonitor", str(program), cwd=self.outside)

    def redirect(self):
        """The worktree's `.git` file names a repository outside the layout."""
        (self.worktree / ".git").write_text(f"gitdir: {self.outside / '.git'}\n")

    def test_the_layout_names_the_task_git_folders_and_refuses_a_redirection(self):
        env = git_policy.task_git(self.worktree)
        common = self.repo / ".git"
        self.assertEqual((env["GIT_DIR"], env["GIT_COMMON_DIR"], env["GIT_WORK_TREE"]),
                         (str(common / "worktrees" / self.slug), str(common), str(self.worktree)))
        self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "not a task worktree"):
            git_policy.task_git(self.repo)
        self.redirect()
        with self.assertRaisesRegex(git_policy.GitPolicyError, "does not name its own record"):
            git_policy.task_git(self.worktree)
        (self.worktree / ".git").write_text(f"gitdir: {common / 'worktrees' / self.slug}\n")
        record = common / "worktrees" / self.slug
        record.rename(self.tmp / "moved-record")
        record.symlink_to(self.tmp / "moved-record")
        with self.assertRaisesRegex(git_policy.GitPolicyError, "not plain folders"):
            git_policy.task_git(self.worktree)

    def test_a_git_file_naming_another_repository_is_never_followed(self):
        self.redirect()
        subprocess.run(["git", "status"], cwd=self.worktree, capture_output=True)
        self.assertTrue(self.ran.exists(), "the fixture's program runs when the file is followed")
        self.ran.unlink()
        with self.assertRaises(git_policy.GitPolicyError):
            dispatch._validate_task_worktree(self.project, self.slug, require_clean=True)
        with self.assertRaises(T.TransitionError):
            reviews._git(self.worktree, "status", "--porcelain")
        with self.assertRaises(T.TransitionError):
            T._require_no_code_change(self.project, self.task)
        with self.assertRaises(git_policy.GitPolicyError):
            validation._clone(self.worktree, self.tmp / "copy", self.project)
        with self.assertRaises(verify.VerifierFault):
            verify._worktree_problems(self.project, self.task, {"head": "0" * 40}, None)
        self.assertEqual(dispatch._git_branch(self.worktree), None)
        self.assertFalse(self.ran.exists())

    def test_a_redirected_task_record_is_never_followed(self):
        outside_worktree = self.tmp / "outside-worktree"
        git("-C", str(self.outside), "commit", "-q", "--allow-empty", "-m", "fictional", cwd=self.tmp)
        git("-C", str(self.outside), "worktree", "add", "-q", str(outside_worktree), cwd=self.tmp)
        self.ran.unlink(missing_ok=True)  # the outside repository's own commands ran it
        task = {**self.task, "worktree": str(outside_worktree)}
        S.save_task(self.project, task)
        git("fetch", "-q", "origin", cwd=self.worktree)
        T._require_no_code_change(self.project, task)  # the layout's clean, unchanged worktree
        head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        self.assertEqual(verify._worktree_problems(self.project, task, {"head": head}, None), [])
        dispatch._validate_task_worktree(self.project, self.slug, require_clean=True)
        self.assertFalse(self.ran.exists())

    def test_a_commondir_in_the_shared_folder_is_read_only_and_not_followed(self):
        # The shared folder's commondir and gitdir are read-only to workers, so a worker cannot redirect the
        # repository-level Git Altitude runs there; altd also names the folder explicitly rather than discovering it.
        writable, read_only = git_policy.worker_git_access(self.worktree)
        self.assertIn(self.repo / ".git" / "commondir", read_only)
        (self.repo / ".git" / "commondir").write_text(f"{self.outside / '.git'}\n")
        subprocess.run(["git", "status"], cwd=self.repo, capture_output=True)
        self.assertTrue(self.ran.exists(), "the fixture's program runs when the file is followed")
        self.ran.unlink()
        self.assertEqual(git_policy.inspect_repository(self.repo).branch, "main")
        env = git_policy.git_env(self.repo)
        self.assertEqual((env["GIT_DIR"], env["GIT_COMMON_DIR"]), (str(self.repo / ".git"), str(self.repo / ".git")))
        self.assertFalse(self.ran.exists())

    def test_validation_copies_the_task_commit_from_the_projects_own_git_folder(self):
        (self.worktree / "change.txt").write_text("fictional\n")
        git("add", "change.txt", cwd=self.worktree)
        git("commit", "-qm", "Fictional change", cwd=self.worktree)
        git("checkout", "-q", "--detach", cwd=self.worktree)
        head = git("rev-parse", "HEAD", cwd=self.worktree).strip()
        commit, _ = validation._clone(self.worktree, self.tmp / "copy", self.project)
        self.assertEqual(commit, head)
        self.assertEqual((self.tmp / "copy" / "change.txt").read_text(), "fictional\n")

    def test_workers_write_objects_branches_and_their_own_state_but_not_settings_or_records(self):
        writable, read_only = git_policy.worker_git_access(self.worktree)
        common, record = self.repo / ".git", self.repo / ".git" / "worktrees" / self.slug
        self.assertEqual(writable, (common, record))
        self.assertEqual(set(read_only), {common / "config", common / "config.worktree", common / "hooks",
                                          common / "info", common / "modules", common / "worktrees",
                                          common / "commondir", common / "gitdir",
                                          record / "commondir", record / "gitdir", record / "config.worktree"})

    def test_seatbelt_applies_the_most_specific_git_rule(self):
        self.patch(platform, "_user_temp", return_value="/private/var/folders/ab/cd/T/")
        writable, read_only = git_policy.worker_git_access(self.worktree)
        text = platform.seatbelt_profile([str(self.worktree), *map(str, writable)], [str(path) for path in read_only])
        common = os.path.realpath(self.repo / ".git")
        order = [text.index(rule) for rule in (
            f'(allow file-write* (subpath "{common}"))', f'(deny file-write* (subpath "{common}/worktrees"))',
            f'(allow file-write* (subpath "{common}/worktrees/{self.slug}"))',
            f'(deny file-write* (subpath "{common}/worktrees/{self.slug}/gitdir"))')]
        self.assertEqual(order, sorted(order), "Seatbelt's last matching rule decides")
        self.assertTrue(text.endswith(platform.private_rule() + platform.SERVICE_ESCAPES))


class SourceCopy(AltitudeCase):
    """Altitude's own code comes through its copy of its repository, never the checkout's shared Git store."""

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.head = git("rev-parse", "HEAD", cwd=self.repo).strip()

    def test_a_forged_main_in_the_shared_store_is_never_the_source(self):
        self.assertEqual(git_policy.source_head(self.repo, "main"), self.head)
        # A commit only the checkout's shared store holds, named by its branch and origin ref.
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo).strip()
        forged = git("commit-tree", tree, "-p", self.head, "-m", "Forged", cwd=self.repo).strip()
        for ref in ("refs/heads/main", "refs/remotes/origin/main"):
            git("update-ref", ref, forged, cwd=self.repo)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "not on main in Altitude's copy"):
            git_policy.source_head(self.repo, "main")
        git("update-ref", "refs/heads/main", self.head, cwd=self.repo)
        # The fast-forward target comes from the origin, fetched into the copy; the forged origin ref is ignored.
        self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, self.head, []))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), self.head)

    def push_next(self) -> str:
        """A later commit on the origin's main; returns it."""
        other = self.tmp / "other"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        (other / "next.txt").write_text("next\n")
        git("add", "next.txt", cwd=other)
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Next", cwd=other)
        git("push", "-q", "origin", "HEAD:main", cwd=other)
        return git("rev-parse", "HEAD", cwd=other).strip()

    def test_the_checkout_advances_to_its_origin_through_the_copy(self):
        git_policy.source_head(self.repo, "main")
        tip = self.push_next()
        self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, tip, ["next.txt"]))
        self.assertEqual((self.repo / "next.txt").read_text(), "next\n")
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_uncommitted_or_hidden_changes_in_the_checkout_are_refused(self):
        git_policy.source_head(self.repo, "main")
        readme = self.repo / "README.md"
        readme.write_text("changed\n")
        # The files are compared by content, not by the checkout's index.
        git("update-index", "--assume-unchanged", "README.md", cwd=self.repo)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "uncommitted changes"):
            git_policy.source_head(self.repo, "main")

    def test_a_pull_by_hand_is_fetched_into_the_copy(self):
        git_policy.source_head(self.repo, "main")
        tip = self.push_next()
        git("pull", "-q", "--ff-only", "origin", "main", cwd=self.repo)
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)
