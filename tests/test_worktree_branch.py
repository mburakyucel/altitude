"""I-20260907-171446: the task owns its checkout and Git supplies its branch."""
import unittest

from tests.support import AltitudeCase, git
from altitude import dispatch


class TestWorktreeBranch(AltitudeCase):
    def test_slug_derives_the_branch_before_checkout_exists(self):
        for kwargs in ({}, {"worktree": self.tmp / "gone"}):
            with self.subTest(case=str(kwargs)):
                self.assertEqual(dispatch.worktree_branch("demo-slug", **kwargs), "worktree-demo-slug")

    def test_git_confirms_the_branch_when_the_worktree_exists(self):
        wt = self.tmp / "wt"
        git("init", "-q", str(wt), cwd=self.tmp)
        git("symbolic-ref", "HEAD", "refs/heads/worktree-renamed", cwd=wt)
        git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x", cwd=wt)
        self.assertEqual(dispatch.worktree_branch("demo-slug", worktree=wt), "worktree-renamed")

    def test_never_falls_back_to_the_bare_slug(self):
        self.assertNotEqual(dispatch.worktree_branch("demo-slug"), "demo-slug")


if __name__ == "__main__":
    unittest.main()
