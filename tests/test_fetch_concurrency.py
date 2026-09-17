"""Real Git transfers overlap across linked worktrees without touching operator state."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time
from unittest import mock

from tests.support import AltitudeCase, add_worktree, git, make_repo
from altitude import dispatch, git_policy, land, state as S, config
from altitude import tasks as T


class TestFetchConcurrency(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        self.register(self.project, self_deploy=True)
        self.worker = add_worktree(self.repo, "fetching")
        self.old = git_policy.capture_origin_sha(self.repo)
        clone = self.tmp / "upstream"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(clone), cwd=self.tmp)
        (clone / "altitude").mkdir()
        (clone / "altitude/change.py").write_text("# deployed change\n")
        git("add", "-A", cwd=clone)
        git("commit", "-qm", "advance remote", cwd=clone)
        git("push", "-q", "origin", "main", cwd=clone)
        self.new = git("rev-parse", "HEAD", cwd=clone).strip()
        self.ready, self.release = self.tmp / "ready", self.tmp / "release"
        wrapper = self.tmp / "upload-pack"
        # The first transfer pauses at its pack, after fetch has read the old local ref.
        # Further transfers use real upload-pack without pausing. No Git result is mocked.
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, subprocess, sys, time\n"
            f"ready, release = pathlib.Path({str(self.ready)!r}), pathlib.Path({str(self.release)!r})\n"
            "child = subprocess.Popen(['git-upload-pack', *sys.argv[1:]], stdout=subprocess.PIPE)\n"
            "while header := child.stdout.read(4):\n"
            "    chunk = header + child.stdout.read(max(0, int(header, 16) - 4))\n"
            "    if b'PACK' in chunk and not ready.exists():\n"
            "        ready.touch()\n"
            "        deadline = time.monotonic() + 20\n"
            "        while not release.exists():\n"
            "            if time.monotonic() > deadline: raise SystemExit('pack gate timed out')\n"
            "            time.sleep(.01)\n"
            "    sys.stdout.buffer.write(chunk)\n"
            "    sys.stdout.buffer.flush()\n"
            "raise SystemExit(child.wait())\n"
        )
        wrapper.chmod(0o755)
        git("config", "protocol.version", "0", cwd=self.repo)
        git("config", "remote.origin.uploadpack", str(wrapper), cwd=self.repo)

    def wait_for_pack(self):
        deadline = time.monotonic() + 10
        while not self.ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.ready.exists(), "first fetch did not reach the pack gate")

    def raw_fetch(self):
        return subprocess.run(["git", "-C", str(self.repo), "fetch", "--no-tags", "origin", "main"],
                              capture_output=True, text=True, timeout=30)

    def test_uncoordinated_fetch_reproduces_stale_old_ref_collision(self):
        with ThreadPoolExecutor() as pool:
            first = pool.submit(self.raw_fetch)
            try:
                self.wait_for_pack()
                self.assertEqual(land._fetch_rev(self.worker, "main"), self.new)
            finally:
                self.release.touch()
            failed = first.result(timeout=30)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(f"is at {self.new} but expected {self.old}", failed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), self.old)

    def deployment_race(self, competing_fetch):
        git_policy.install_hooks(self.repo)
        with mock.patch.object(git_policy, "_run", wraps=git_policy._run) as commands, ThreadPoolExecutor() as pool:
            deployment = pool.submit(dispatch.self_deploy_fast_forward, self.project)
            try:
                self.wait_for_pack()
                competing_fetch()
                self.assertEqual(git_policy.capture_origin_sha(self.worker), self.new)
            finally:
                self.release.touch()
            self.assertTrue(deployment.result(timeout=30)[0].startswith("self-deploy:"))
        self.assertEqual(sum(call.args[1] == "fetch" for call in commands.call_args_list), 2)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), self.new)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worker).strip(), self.old)
        pending = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pending["head"], self.new)
        self.assertIn("altitude/change.py", pending["files"])

    def test_deployment_progresses_after_competing_landing_fetch(self):
        self.deployment_race(lambda: self.assertEqual(land._fetch_rev(self.worker, "main"), self.new))

    def test_deployment_progresses_after_competing_direct_fetch(self):
        self.deployment_race(lambda: git("fetch", "origin", "main", cwd=self.worker))

    def test_dispatch_uses_fresh_base_and_preserves_dirty_deployment(self):
        self.quiet_engines()
        task = T.new(self.project, "Concurrent dispatch", "Use the current base")
        (self.repo / "README.md").write_text("operator edits\n")
        git("add", "README.md", cwd=self.repo)
        (self.repo / "untracked.txt").write_text("keep\n")
        before = git("status", "--porcelain", cwd=self.repo)
        launched = {"stdout": "started", "stderr": "", "returncode": 0,
                    "agent": {"id": "agent-1", "sessionId": "session-1"}}
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.engines, "start_l2", return_value=launched), ThreadPoolExecutor() as pool:
            first = pool.submit(dispatch.run, self.project, task["slug"])
            try:
                self.wait_for_pack()
                land._fetch_rev(self.worker, "main")
            finally:
                self.release.touch()
            first.result(timeout=30)
        current = S.load_task(self.project, task["slug"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(git("rev-parse", "HEAD", cwd=current["worktree"]).strip(), self.new)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), self.old)
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), before)
        self.assertEqual((self.repo / "README.md").read_text(), "operator edits\n")
        self.assertEqual((self.repo / "untracked.txt").read_text(), "keep\n")

    def test_fetch_errors_remain_failures(self):
        self.release.touch()
        for fetch in (git_policy.fetch_origin, land._fetch_rev):
            with self.subTest(fetch=fetch.__name__):
                with self.assertRaisesRegex((git_policy.GitPolicyError, land.LandError), "missing-branch"):
                    fetch(self.worker, "missing-branch")
                self.assertEqual(fetch(self.worker, "main"), self.new)
        git("remote", "set-url", "origin", str(self.tmp / "absent.git"), cwd=self.repo)
        with self.assertRaisesRegex(git_policy.GitPolicyError, "does not appear to be a git repository"):
            git_policy.fetch_origin(self.repo)

    def test_real_error_on_second_fetch_remains_a_failure(self):
        with ThreadPoolExecutor() as pool:
            first = pool.submit(git_policy.fetch_origin, self.repo)
            try:
                self.wait_for_pack()
                land._fetch_rev(self.worker, "main")
                git("remote", "set-url", "origin", str(self.tmp / "absent.git"), cwd=self.repo)
            finally:
                self.release.touch()
            with self.assertRaisesRegex(git_policy.GitPolicyError, "does not appear to be a git repository"):
                first.result(timeout=30)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), self.old)

    def test_real_ref_lock_failure_is_not_suppressed(self):
        self.release.touch()
        lock = self.repo / ".git/refs/remotes/origin/main.lock"
        lock.touch()
        with self.assertRaises(git_policy.GitPolicyError):
            git_policy.fetch_origin(self.repo)
        self.assertEqual(git_policy.capture_origin_sha(self.repo), self.old)
        lock.unlink()
        self.assertEqual(git_policy.fetch_origin(self.repo), self.new)

    def test_retry_is_narrow_and_bounded(self):
        collision = f"error: cannot lock ref 'refs/remotes/origin/main': is at {self.new} but expected {self.old}"
        cases = [
            (collision, 2),
            (collision + "\nfatal: cannot write FETCH_HEAD", 1),
            (collision + "\nerror: another ref failed", 1),
            (collision.replace("origin/main", "origin/other"), 1),
            ("fatal: remote unavailable", 1),
            (collision.replace(self.new, "1234"), 1),
        ]
        for error, attempts in cases:
            with self.subTest(error=error):
                failed = subprocess.CompletedProcess([], 1, "", error)
                with mock.patch.object(git_policy, "_run", return_value=failed) as run:
                    with self.assertRaises(git_policy.GitPolicyError):
                        git_policy.fetch_origin(self.repo)
                self.assertEqual(run.call_count, attempts)
