"""#961: a self-deploy fast-forward moves the branch through the guard before it writes any file, and only to a tip
the checkout's cached origin ref also names; real Git and the tracked guards, no providers or services."""
import subprocess
from unittest import mock

from tests.support import REPO, AltitudeCase, fyi_rows, git, make_repo
from altitude import config, dispatch, git_policy, server, state as S


class SelfDeployFastForward(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        git_policy.install_hooks(self.repo, REPO / "hooks")
        self.register("altitude", path=self.repo, self_deploy=True)
        self.patch(config, "REPO", self.repo)
        self.head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        self.assertEqual(git_policy.source_head(self.repo, "main"), self.head)   # seeds Altitude's copy
        self.addCleanup(server._failing_since.clear)
        self.addCleanup(server._refused.clear)

    def merged(self, path: str = "altitude/x.py") -> str:
        """A PR merged on the origin a moment ago; the checkout has not fetched it."""
        other = self.tmp / "other"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        (other / path).parent.mkdir(parents=True, exist_ok=True)
        (other / path).write_text("# merged\n")
        git("add", "-A", cwd=other)
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Merged", cwd=other)
        git("push", "-q", "origin", "HEAD:main", cwd=other)
        return git("rev-parse", "HEAD", cwd=other).strip()

    def snapshot(self):
        return (git("rev-parse", "HEAD", "refs/remotes/origin/main", cwd=self.repo),
                (self.repo / ".git" / "index").read_bytes(),
                git("status", "--porcelain", "--untracked-files=all", cwd=self.repo),
                git("diff", "--binary", cwd=self.repo), git("diff", "--cached", "--binary", cwd=self.repo))

    def assert_untouched(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        self.assertEqual(git_policy.source_head(self.repo, "main"), self.head)   # clean: the next tick can retry

    def test_a_refused_branch_move_leaves_the_checkout_untouched(self):
        tip = self.merged()
        # The merged commit's objects only: fetching by address leaves origin/main where it was.
        git("fetch", "-q", str(self.tmp / "origin.git"), "+refs/heads/main:refs/fixture/tip", cwd=self.repo)
        before = self.snapshot()
        # The guard still refuses a move to a commit that is not the cached origin ref.
        refused = subprocess.run(["git", "update-ref", "refs/heads/main", tip], cwd=self.repo, capture_output=True,
                                 text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refs/heads/main may move only to origin/main", refused.stderr)
        self.assert_untouched(before)
        # The two fetches seem to agree, but the cached ref is elsewhere by the time the branch moves.
        with mock.patch.object(git_policy, "capture_origin_sha", return_value=tip), \
             self.assertRaisesRegex(git_policy.GitPolicyError, "protected branch update blocked"):
            git_policy.deploy_source(self.repo, "main")
        self.assert_untouched(before)

    def test_a_lagging_origin_ref_is_fetched_level_before_the_fast_forward(self):
        tip = self.merged()
        self.assertEqual(git("rev-parse", "refs/remotes/origin/main", cwd=self.repo).strip(), self.head)
        self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, tip, ["altitude/x.py"]))
        self.assertEqual(git("rev-parse", "HEAD", "refs/remotes/origin/main", cwd=self.repo).split(), [tip, tip])
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_fetches_that_still_disagree_leave_the_step_to_the_next_tick(self):
        tip = self.merged()
        before = self.snapshot()
        # The replica the checkout's fetch reaches has not seen the merge yet; Altitude's copy has.
        with mock.patch.object(git_policy, "fetch_origin", return_value=self.head):
            with self.assertRaisesRegex(git_policy.FetchError, "disagree"):
                git_policy.deploy_source(self.repo, "main")
            self.assert_untouched(before)
            with mock.patch("altitude.incidents.system_fault") as fault, mock.patch.object(server, "log") as log:
                server.self_deploy("altitude")
            fault.assert_not_called()
            self.assertIn("[altitude] self-deploy failed; retrying next tick:", log.call_args.args[0])
            self.assert_untouched(before)
        with mock.patch("altitude.incidents.system_fault") as fault:
            server.self_deploy("altitude")   # the next tick
        fault.assert_not_called()
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_a_fast_forward_marks_activation_pending(self):
        tip = self.merged("altitude/x.py")
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.self_deploy_fast_forward("altitude")
        fault.assert_not_called()
        self.assertEqual(notes[0], f"self-deploy: main {self.head[:7]} → {tip[:7]} (1 files)")
        self.assertEqual(notes[1], "restart pending: activation of 1 backend file(s)")
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual((pend.get("head"), pend.get("files")), (tip, ["altitude/x.py"]))
        self.assertIn("the running Altitude backend is older than main", fyi_rows("altitude")[-1]["text"])
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)
