"""`claude --bg -w <slug>` checks out `worktree-<slug>`, not `<slug>`: dispatch must record the real branch."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-wb-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import dispatch  # noqa: E402


class TestWorktreeBranch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="altitude-jobs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.jobs, old = self.tmp / "jobs", dispatch.JOBS_DIR
        dispatch.JOBS_DIR = self.jobs
        self.addCleanup(setattr, dispatch, "JOBS_DIR", old)

    def write_state(self, agent_id: str, body) -> None:
        d = self.jobs / agent_id
        d.mkdir(parents=True)
        (d / "state.json").write_text(body if isinstance(body, str) else json.dumps(body))

    def test_state_json_wins(self):
        self.write_state("3642c4f1", {"worktreePath": "/w/demo", "worktreeBranch": "worktree-demo-slug"})
        self.assertEqual(dispatch.worktree_branch("demo-slug", agent_id="3642c4f1"), "worktree-demo-slug")

    def test_derives_when_state_json_lacks_the_branch(self):
        self.write_state("nobranch", {"worktreePath": "/w/demo"})
        self.assertEqual(dispatch.worktree_branch("demo-slug", agent_id="nobranch"), "worktree-demo-slug")
        self.write_state("empty", {"worktreeBranch": ""})
        self.assertEqual(dispatch.worktree_branch("demo-slug", agent_id="empty"), "worktree-demo-slug")

    def test_degrades_on_missing_or_malformed_state(self):
        self.write_state("junk", "{not json")
        for kwargs in ({}, {"agent_id": "junk"}, {"agent_id": "never-launched"}, {"worktree": self.tmp / "gone"}):
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
        # state.json still wins over git
        self.write_state("a1", {"worktreeBranch": "worktree-from-state"})
        self.assertEqual(dispatch.worktree_branch("demo-slug", worktree=wt, agent_id="a1"), "worktree-from-state")

    def test_never_falls_back_to_the_bare_slug(self):
        self.assertNotEqual(dispatch.worktree_branch("demo-slug"), "demo-slug")


if __name__ == "__main__":
    unittest.main()
