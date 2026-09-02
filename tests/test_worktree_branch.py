"""`claude --bg -w <slug>` checks out `worktree-<slug>`, not `<slug>`: dispatch must record the real branch."""
import os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-wb-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import dispatch  # noqa: E402


class TestWorktreeBranch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-jobs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
    def test_derives_without_a_real_worktree(self):
        for kwargs in ({}, {"worktree": self.tmp / "gone"}):
            self.assertEqual(dispatch.worktree_branch("demo-slug", **kwargs), "worktree-demo-slug", kwargs)

    def test_git_confirms_the_branch_when_the_worktree_exists(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        wt = self.tmp / "wt"
        wt.mkdir()
        subprocess.run(["git", "init", "-q", str(wt)], capture_output=True)
        subprocess.run(["git", "-C", str(wt), "symbolic-ref", "HEAD", "refs/heads/worktree-renamed"], capture_output=True)
        commit = subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                                 "commit", "-q", "--allow-empty", "-m", "x"], capture_output=True, text=True)
        if commit.returncode != 0:  # an unborn HEAD is not a branch git can name; nothing to confirm against
            self.skipTest(f"git commit unavailable: {(commit.stderr or commit.stdout).strip()[:120]}")
        self.assertEqual(dispatch.worktree_branch("demo-slug", worktree=wt), "worktree-renamed")

    def test_never_falls_back_to_the_bare_slug(self):
        self.assertNotEqual(dispatch.worktree_branch("demo-slug"), "demo-slug")


if __name__ == "__main__":
    unittest.main()
