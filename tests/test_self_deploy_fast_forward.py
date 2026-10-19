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
        git("add", path, cwd=other)
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
        # The two fetches agreed, but the cached ref is elsewhere by the time the branch moves.
        with mock.patch.object(git_policy, "capture_origin_sha", return_value=tip), \
             self.assertRaisesRegex(git_policy.GitPolicyError, "protected branch update blocked") as refusal:
            git_policy.deploy_source(self.repo, "main")
        self.assertNotIsInstance(refusal.exception, git_policy.FetchError)
        self.assert_untouched(before)
        # A fetch elsewhere moved the cached ref on in the meantime: the next tick fetches again.
        agreed = iter([tip])
        with mock.patch.object(git_policy, "capture_origin_sha", side_effect=lambda *_: next(agreed, self.head)), \
             self.assertRaisesRegex(git_policy.FetchError, "moved past"):
            git_policy.deploy_source(self.repo, "main")
        self.assert_untouched(before)

    def test_a_lagging_origin_ref_is_fetched_level_before_the_fast_forward(self):
        tip = self.merged()
        self.assertEqual(git("rev-parse", "refs/remotes/origin/main", cwd=self.repo).strip(), self.head)
        # An entry a worker staged in the checkout's index is not carried into the deployment.
        blob = git("rev-parse", "HEAD:README.md", cwd=self.repo).strip()
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},staged-by-a-worker.txt", cwd=self.repo)
        self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, tip, ["altitude/x.py"]))
        self.assertEqual(git("rev-parse", "HEAD", "refs/remotes/origin/main", cwd=self.repo).split(), [tip, tip])
        self.assertEqual(git("ls-files", cwd=self.repo).split(), git("ls-tree", "-r", "--name-only", tip,
                                                                      cwd=self.repo).split())
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_a_file_appearing_after_the_check_does_not_strand_a_moved_branch(self):
        tip = self.merged()
        run = git_policy._run

        def racing(repo, *args, **kwargs):
            if args[0] == "update-ref":   # an untracked file lands where the merged commit tracks one
                (self.repo / "altitude").mkdir(exist_ok=True)
                (self.repo / "altitude" / "x.py").write_text("# a worker's file\n")
            return run(repo, *args, **kwargs)

        with mock.patch.object(git_policy, "_run", side_effect=racing):
            self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, tip, ["altitude/x.py"]))
        self.assertEqual((self.repo / "altitude" / "x.py").read_text(), "# merged\n")
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_the_deployment_reads_altitudes_copy_never_the_checkouts_object_store(self):
        tip = self.merged()
        git("fetch", "-q", "origin", cwd=self.repo)
        store = self.repo / ".git" / "objects"
        self.assertEqual(list((store / "pack").glob("*.pack")), [])
        # A worker writes the checkout's store: it removes every tree and file the two commits hold and plants a
        # replacement for the merged file.
        merged = git("rev-parse", f"{tip}:altitude/x.py", cwd=self.repo).strip()
        for loose in store.glob("??/*"):
            if git("cat-file", "-t", loose.parent.name + loose.name, cwd=self.repo).strip() != "commit":
                loose.unlink()
        planted = subprocess.run(["git", "hash-object", "-w", "--stdin"], input="# a worker's\n", cwd=self.repo,
                                 capture_output=True, text=True, check=True).stdout.strip()
        git("update-ref", f"refs/replace/{merged}", planted, cwd=self.repo)
        self.assertEqual(git_policy.deploy_source(self.repo, "main"), (self.head, tip, ["altitude/x.py"]))
        self.assertEqual((self.repo / "altitude" / "x.py").read_text(), "# merged\n")
        self.assertEqual((self.repo / "README.md").read_text(), "readme\n")
        self.assertEqual(git_policy.source_head(self.repo, "main"), tip)

    def test_a_held_commit_is_checked_without_reaching_the_origin(self):
        (self.tmp / "origin.git").rename(self.tmp / "unreachable.git")
        self.assertEqual(git_policy.source_head(self.repo, "main"), self.head)
        with self.assertRaises(git_policy.FetchError):
            git_policy.deploy_source(self.repo, "main")

    def test_a_commit_no_longer_on_the_branch_is_refused_and_never_deployed_backwards(self):
        tip = self.merged()
        self.assertEqual(git_policy.deploy_source(self.repo, "main")[1], tip)
        git("update-ref", "refs/heads/main", self.head, cwd=self.tmp / "origin.git")   # the origin's main rewound
        with self.assertRaisesRegex(git_policy.GitPolicyError, "commit is not on main"):
            git_policy.deploy_source(self.repo, "main")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), tip)
        self.assertEqual((self.repo / "altitude" / "x.py").read_text(), "# merged\n")

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
