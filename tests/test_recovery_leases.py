"""Recovered work does not need another permission round trip for unpredicted files."""
from pathlib import Path

from tests.support import AltitudeCase, git, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, l3, land, state as S, tasks as T


class TestRecoveryLeases(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.project = "demo"
        make_repo(self.repo)
        git("config", f"url.{self.tmp / 'origin.git'}.insteadOf", "https://github.com/team/demo.git", cwd=self.repo)
        git("remote", "set-url", "origin", "https://github.com/team/demo.git", cwd=self.repo)
        self.register(self.project)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)

    def recover(self, initial_paths, engine):
        task = T.new(self.project, "Recover demo", "Review preserved docs/archive/ changes.",
                     source="recovery", paths=initial_paths, engine=engine,
                     hold_merge="Review recovered content")
        slug = task["slug"]
        dispatch.run(self.project, slug)
        task = S.load_task(self.project, slug)
        other = T.new(self.project, "Other owner", "Unrelated work", paths=["src/"])
        self.fake_gh()
        for key, value in dispatch.l2_env(self.project, slug, task["attempt"]).items():
            self.setenv(key, value)
        worktree = Path(task["worktree"])
        archive = worktree / "docs/archive/recovered.md"
        archive.parent.mkdir(parents=True)
        archive.write_text("Reviewed fictional preserved documentation.\n")
        git("add", "docs/archive/recovered.md", cwd=worktree)
        selected = git("write-tree", cwd=worktree)
        private = worktree / "private.txt"
        private.write_text("Unselected notes\n")
        preview = land.land("docs: recover reviewed content", cwd=worktree, dry_run=True)
        self.assertEqual(preview["staged"], ["docs/archive/recovered.md"])
        self.assertEqual(git("write-tree", cwd=worktree), selected)
        result = land.land("docs: recover reviewed content", cwd=worktree, wait=0)
        self.assertEqual((result["pr"], result["merged"]), (101, False))
        self.assertEqual(git("show", "HEAD:docs/archive/recovered.md", cwd=worktree), archive.read_text())
        self.assertNotIn("private.txt", git("ls-tree", "-r", "--name-only", "HEAD", cwd=worktree))
        self.assertEqual(private.read_text(), "Unselected notes\n")
        current = S.load_task(self.project, slug)
        for key in ("state", "attempt", "session_id", "l2_engine", "worktree", "branch", "hold_merge", "paths"):
            self.assertEqual(current[key], task[key], key)
        self.assertEqual(len(self.engine.calls), 1, "no permission block or resume is needed")
        self.assertEqual(l3.queued(self.project), [])
        self.assertEqual(S.load_task(self.project, other["slug"])["paths"], ["src/"])

    def test_recovery_without_predicted_files_keeps_the_same_session(self):
        self.recover([], config.ENGINES[0])

    def test_recovery_keeps_advisory_files_on_the_other_engine(self):
        self.recover(["README.md"], config.ENGINES[-1])
