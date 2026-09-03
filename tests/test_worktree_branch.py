"""`claude --bg -w <slug>` checks out `worktree-<slug>`, not `<slug>`: dispatch must record the real branch."""
import json
import unittest

from tests.support import AltitudeCase, git
from altitude import dispatch, engines


class TestWorktreeBranch(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.jobs = self.patch(engines, "JOBS_DIR", new=self.tmp / "jobs")

    def write_state(self, agent_id: str, body) -> None:
        d = self.jobs / agent_id
        d.mkdir(parents=True)
        (d / "state.json").write_text(body if isinstance(body, str) else json.dumps(body))

    def test_state_json_names_the_branch_and_the_slug_derives_it_otherwise(self):
        self.write_state("named", {"worktreePath": "/w/demo", "worktreeBranch": "worktree-demo-slug"})
        self.write_state("nobranch", {"worktreePath": "/w/demo"})      # state.json without the branch
        self.write_state("empty", {"worktreeBranch": ""})
        self.write_state("junk", "{not json")
        for kwargs in ({"agent_id": "named"}, {"agent_id": "nobranch"}, {"agent_id": "empty"},
                       {"agent_id": "junk"}, {"agent_id": "never-launched"}, {},
                       {"worktree": self.tmp / "gone"}):
            with self.subTest(case=str(kwargs)):
                self.assertEqual(dispatch.worktree_branch("demo-slug", **kwargs), "worktree-demo-slug")

    def test_git_confirms_the_branch_when_the_worktree_exists(self):
        wt = self.tmp / "wt"
        git("init", "-q", str(wt), cwd=self.tmp)
        git("symbolic-ref", "HEAD", "refs/heads/worktree-renamed", cwd=wt)
        git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x", cwd=wt)
        self.assertEqual(dispatch.worktree_branch("demo-slug", worktree=wt), "worktree-renamed")
        # state.json still wins over git
        self.write_state("a1", {"worktreeBranch": "worktree-from-state"})
        self.assertEqual(dispatch.worktree_branch("demo-slug", worktree=wt, agent_id="a1"), "worktree-from-state")

    def test_never_falls_back_to_the_bare_slug(self):
        self.assertNotEqual(dispatch.worktree_branch("demo-slug"), "demo-slug")


if __name__ == "__main__":
    unittest.main()
