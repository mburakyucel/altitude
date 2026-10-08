"""cleanup_task frees a finished task's worktree and branch once nothing needs them, and
prune_source_exports removes launch exports nothing names (real git, disposable repositories)."""
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, dispatch, engines, server, state as S


class TestCleanup(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        (self.repo / "doc.md").write_text("before\n")
        git("add", "doc.md", cwd=self.repo)
        git("commit", "-qm", "doc", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.quiet_engines()
        self.patch(engines, "remove_l2_worker", return_value="removed")

    def task(self, slug="task-one", **extra) -> dict:
        wt, branch = self.repo / ".claude" / "worktrees" / slug, f"worktree-{slug}"
        git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=self.repo)
        (wt / "doc.md").write_text(f"after {slug}\n")
        git("commit", "-qam", "task change", cwd=wt)
        S.task_dir(self.project, slug).mkdir(parents=True, exist_ok=True)
        task = {"slug": slug, "title": slug, "state": "done", "agent_id": "a1", "worktree": str(wt), "branch": branch,
                "created": S.now(), "updated": S.now(), **extra}
        S.save_task(self.project, task)
        return task

    def merge(self, branch, *, squash=False):
        if squash:
            git("merge", "--squash", branch, cwd=self.repo)
            git("commit", "-qm", "squash", cwd=self.repo)
        else:
            git("merge", "--no-ff", "-qm", "merge", branch, cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)

    def listed(self, task) -> bool:
        return task["worktree"] in git("worktree", "list", "--porcelain", cwd=self.repo)

    def branch_exists(self, task) -> bool:
        return f"refs/heads/{task['branch']}" in git("show-ref", "--heads", cwd=self.repo)

    def last_event(self, slug):
        return [e for e in S.read_events(self.project, slug) if e.get("kind") == "cleanup-worktree"][-1]

    def test_merged_branch_is_removed_with_its_worker(self):
        task = self.task()
        self.merge(task["branch"])
        notes = dispatch.cleanup_task(self.project, task)
        self.assertFalse(self.listed(task), notes)
        self.assertFalse(self.branch_exists(task))
        engines.remove_l2_worker.assert_called_once()
        self.assertEqual(self.last_event(task["slug"])["action"], "removed")
        self.assertIn("removed merged worktree task-one", notes)

    def test_squash_merge_needs_a_verified_merged_pr(self):
        unverified = self.task("unverified")
        self.merge(unverified["branch"], squash=True)
        with mock.patch.object(dispatch, "_pr_merged_at", return_value=False):
            notes = dispatch.cleanup_task(self.project, unverified)
        self.assertFalse(self.listed(unverified), notes)
        self.assertTrue(self.branch_exists(unverified))
        self.assertIn("not on origin/main", self.last_event("unverified")["reason"])
        task = self.task(verified={"verdict": "ok", "prs": [7]})
        self.merge(task["branch"], squash=True)
        tip = git("rev-parse", task["branch"], cwd=self.repo).strip()
        with mock.patch.object(dispatch, "_pr_merged_at", return_value=True) as receipt:
            dispatch.cleanup_task(self.project, task)
        self.assertFalse(self.listed(task) or self.branch_exists(task))
        self.assertEqual(receipt.call_args.args[2], tip)  # asked about the branch tip, not main

    def test_dirty_and_live_worktrees_are_kept(self):
        dirty = self.task("dirty")
        self.merge(dirty["branch"])
        (Path(dirty["worktree"]) / "notes.txt").write_text("mine\n")
        live = self.task("live")
        self.merge(live["branch"])
        notes = dispatch.cleanup_task(self.project, dirty)
        self.assertTrue(self.listed(dirty) and self.branch_exists(dirty), notes)
        self.assertEqual(self.last_event("dirty")["action"], "skipped")
        self.assertIn("kept worktree dirty: worktree has uncommitted changes", notes)
        with mock.patch.object(engines, "worker_live", return_value=True):
            notes = dispatch.cleanup_task(self.project, live)
        self.assertTrue(self.listed(live), notes)
        self.assertEqual(self.last_event("live")["action"], "deferred")
        self.assertIn("deferred worktree live: L2 worker is still running", notes)
        engines.remove_l2_worker.assert_not_called()
        self.assertTrue((Path(dirty["worktree"]) / "notes.txt").exists())

    def test_a_git_timeout_retries(self):
        task = self.task("slow", state="rejected")
        self.merge(task["branch"])
        run = dispatch.subprocess.run

        def fetch_times_out(args, **kwargs):
            if args[:2] == ["git", "fetch"]:
                raise dispatch.subprocess.TimeoutExpired(args, 120)
            return run(args, **kwargs)
        with mock.patch.object(dispatch.subprocess, "run", side_effect=fetch_times_out):
            notes = dispatch.cleanup_task(self.project, task)
        self.assertTrue(self.listed(task), notes)
        self.assertTrue(notes[0].startswith("deferred worktree slow: git timed out:"), notes)
        self.assertEqual(self.last_event("slow")["action"], "deferred")

    def test_rejected_task_with_merged_branch_is_removed_without_self_deploy(self):
        task = self.task("rejected", state="rejected")
        self.merge(task["branch"])
        with mock.patch.object(dispatch, "pull_after_done") as pull:
            notes = dispatch.cleanup_task(self.project, task)
        self.assertFalse(self.listed(task) or self.branch_exists(task), notes)
        self.assertEqual(notes, ["claude worker a1: removed", "removed merged worktree rejected"])
        pull.assert_not_called()

    def test_local_only_commits_keep_their_branch(self):
        task = self.task("abandoned", state="rejected")
        tip = git("rev-parse", task["branch"], cwd=self.repo).strip()
        notes = dispatch.cleanup_task(self.project, task)
        self.assertFalse(self.listed(task), notes)
        self.assertFalse(Path(task["worktree"]).exists())
        self.assertEqual(git("rev-parse", task["branch"], cwd=self.repo).strip(), tip)
        event = self.last_event("abandoned")
        self.assertEqual((event["action"], event["branch"]), ("removed", "kept"))
        self.assertIn("kept branch worktree-abandoned: 1 commit(s) not on origin/main", notes[-1])

    def test_commits_on_no_branch_keep_the_worktree(self):
        task = self.task("detached", state="rejected")
        self.merge(task["branch"])
        wt = Path(task["worktree"])
        git("switch", "-q", "--detach", cwd=wt)
        (wt / "doc.md").write_text("detached work\n")
        git("commit", "-qam", "detached change", cwd=wt)
        notes = dispatch.cleanup_task(self.project, task)
        self.assertTrue(self.listed(task) and self.branch_exists(task), notes)
        self.assertIn("kept worktree detached: worktree has commits on no branch", notes)

    def test_maintenance_sweeps_terminal_tasks_and_retries_a_live_worker(self):
        rejected = self.task("rejected", state="rejected")
        self.merge(rejected["branch"])
        running = self.task("running", state="running")
        self.merge(running["branch"])
        for name in ("project_setup", "images"):
            self.patch(getattr(server, name), "maintain" if name == "project_setup" else "collect")
        for name in ("self_deploy", "request_l3_drain", "resume_stranded_reports", "dispatch_waiting", "spawn"):
            self.patch(server, name)
        self.patch(dispatch, "run_settings")
        self.patch(dispatch, "poll", return_value=[])
        with mock.patch.object(engines, "worker_live", return_value=True):
            server.tick_project(self.project)
        self.assertTrue(self.listed(rejected))
        self.assertIsNone(S.load_task(self.project, "rejected").get("cleaned"))
        server.tick_project(self.project)
        self.assertFalse(self.listed(rejected))
        self.assertTrue(S.load_task(self.project, "rejected").get("cleaned"))
        self.assertTrue(self.listed(running))
        self.assertIsNone(S.load_task(self.project, "running").get("cleaned"))

    def test_no_worktree_means_nothing_to_clean_once_the_worker_ends(self):
        self.assertEqual(dispatch.cleanup_task(self.project, {"slug": "x", "state": "done"}), [])
        task = {"slug": "y", "state": "rejected", "agent_id": "a2"}
        with mock.patch.object(engines, "worker_live", return_value=True):
            self.assertEqual(dispatch.cleanup_task(self.project, task), ["deferred worktree y: L2 worker is still running"])
        self.assertEqual(dispatch.cleanup_task(self.project, task), [])


class TestSourceExports(AltitudeCase):
    RUNNING, CURRENT, BRIEF, SETTINGS, ENDED, STALE = (c * 40 for c in "abcdef")

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "deploy" / ".altitude-source"
        for sha in (self.RUNNING, self.CURRENT, self.BRIEF, self.SETTINGS, self.ENDED, self.STALE):
            (self.root / sha / "bin").mkdir(parents=True)
        (self.root / "git-guards").mkdir()
        (self.root / "current").symlink_to(self.CURRENT, target_is_directory=True)
        self.activated(hours_ago=2)
        self.patch(config, "RELEASE", None)
        self.patch(config, "SOURCE", self.root / self.RUNNING)
        self.save("open-task", "blocked", brief=self.BRIEF)
        self.save("ending-task", "rejected", settings=self.SETTINGS)
        self.save("ended-task", "done", settings=self.ENDED, cleaned=S.now())

    def activated(self, *, hours_ago):
        at = time.time() - hours_ago * 3600
        os.utime(self.root / "current", (at, at), follow_symlinks=False)

    def save(self, slug, state, *, brief=None, settings=None, **extra):
        folder = S.task_dir(self.project, slug)
        folder.mkdir(parents=True, exist_ok=True)
        if brief:
            (folder / "brief.md").write_text(f"Schema: `{self.root / brief}/schemas/report.json`\n")
        if settings:
            (folder / "settings.json").write_text(f'{{"hooks": "{self.root / settings}/hooks/inbox.py"}}\n')
        S.save_task(self.project, {"slug": slug, "title": slug, "state": state, "created": S.now(), "updated": S.now(),
                                   **extra})

    def test_unreferenced_exports_are_removed_and_named_ones_kept(self):
        notes = dispatch.prune_source_exports()
        self.assertEqual(sorted(notes), [f"removed source export {self.ENDED}", f"removed source export {self.STALE}"])
        left = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(left, sorted([self.RUNNING, self.CURRENT, self.BRIEF, self.SETTINGS, "current", "git-guards"]))
        self.assertEqual(os.readlink(self.root / "current"), self.CURRENT)
        self.assertEqual(dispatch.prune_source_exports(), [])

    def test_an_unreadable_reference_defers_pruning(self):
        settings = S.task_dir(self.project, "ending-task") / "settings.json"
        settings.chmod(0)
        self.addCleanup(settings.chmod, 0o600)
        notes = dispatch.prune_source_exports()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("deferred source export pruning: cannot read "), notes)
        self.assertTrue(all((self.root / sha).is_dir() for sha in (self.SETTINGS, self.ENDED, self.STALE)))

    def test_a_recent_activation_defers_pruning(self):
        self.activated(hours_ago=0.5)
        self.assertEqual(dispatch.prune_source_exports(), [])
        self.assertTrue((self.root / self.STALE).is_dir())

    def test_versioned_and_checkout_runs_have_no_exports_to_prune(self):
        self.patch(config, "SOURCE", self.repo)
        self.assertEqual(dispatch.prune_source_exports(), [])
        self.patch(config, "SOURCE", self.root / self.RUNNING)
        self.patch(config, "RELEASE", {"version": "1.0.0"})
        self.assertEqual(dispatch.prune_source_exports(), [])
        self.assertTrue((self.root / self.STALE).is_dir())


class TestSelfDeploy(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        self.register("altitude", path=self.repo, self_deploy=True)
        S.task_dir("altitude", "landed").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "landed", "title": "landed", "state": "done",
                                 "created": S.now(), "updated": S.now()})

    def test_pull_after_done_fast_forwards_and_flags_code_changes(self):
        other = self.tmp / "other"
        git("clone", "-q", "-b", "main", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        (other / "altitude").mkdir()
        (other / "altitude" / "x.py").write_text("# new\n")
        (other / "hooks").mkdir()
        (other / "hooks" / "h.py").write_text("# hook\n")
        git("add", "-A", cwd=other)
        git("commit", "-qm", "code + hook", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((self.repo / "hooks" / "h.py").exists(), notes)
        pend = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, {})
        self.assertEqual(pend.get("files"), ["altitude/x.py", "hooks/h.py"], notes)
        self.assertTrue(any("restart pending" in n for n in notes), notes)
        self.assertEqual(dispatch.pull_after_done("altitude", {"slug": "landed"}), [])  # nothing new → silent

    def test_the_running_checkout_follows_the_branch_the_operator_chose(self):
        git("checkout", "-qb", "trial", cwd=self.repo)
        git("push", "-q", "origin", "trial", cwd=self.repo)
        other = self.tmp / "other"
        git("clone", "-q", "-b", "trial", str(self.tmp / "origin.git"), str(other), cwd=self.tmp)
        (other / "trial.txt").write_text("on trial\n")
        git("add", "-A", cwd=other)
        git("commit", "-qm", "trial change", cwd=other)
        git("push", "-q", "origin", "trial", cwd=other)
        with mock.patch("altitude.incidents.system_fault"):  # main is the default: another branch is refused
            self.assertTrue(any("self-deploy refused" in note
                                for note in dispatch.pull_after_done("altitude", {"slug": "landed"})))
        self.patch(config, "REPO", self.repo)
        self.patch(config, "SOURCE_BRANCH", "trial")
        notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertTrue((self.repo / "trial.txt").exists(), notes)
        self.assertTrue(any(note.startswith("self-deploy: trial ") for note in notes), notes)

    def test_not_self_deploy_projects_are_untouched(self):
        self.register("other", path=self.tmp / "nowhere")
        self.assertEqual(dispatch.pull_after_done("other", {"slug": "x"}), [])

    def test_self_deploy_refuses_an_ahead_main(self):
        (self.repo / "direct.txt").write_text("must not deploy\n")
        git("add", "direct.txt", cwd=self.repo)
        git("commit", "-qm", "direct main commit", cwd=self.repo)
        head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).strip(), head)
        self.assertTrue(any("self-deploy refused" in note for note in notes), notes)
        fault.assert_called_once()

    def unreachable_origin(self):
        origin = self.tmp / "origin.git"
        origin.rename(self.tmp / "moved.git")
        self.addCleanup(lambda: origin.exists() or (self.tmp / "moved.git").rename(origin))
        return lambda: (self.tmp / "moved.git").rename(origin)

    def tick_self_deploy(self, now: float) -> tuple[mock.Mock, mock.Mock]:
        with mock.patch("altitude.incidents.system_fault") as fault, mock.patch.object(server, "log") as log, \
             mock.patch.object(server.time, "monotonic", return_value=now):
            server.self_deploy("altitude")
        return fault, log

    def test_tick_logs_a_failed_fetch_that_recovers_without_a_fault(self):
        self.addCleanup(server._fetch_failing_since.pop, "altitude", None)
        restore = self.unreachable_origin()
        fault, log = self.tick_self_deploy(1000.0)
        fault.assert_not_called()
        self.assertIn("self-deploy fetch failed; retrying next tick: git fetch origin main:", log.call_args.args[0])
        restore()
        fault, _ = self.tick_self_deploy(1030.0)
        fault.assert_not_called()
        self.assertNotIn("altitude", server._fetch_failing_since)

    def test_tick_faults_once_fetches_keep_failing_past_the_grace_period(self):
        self.addCleanup(server._fetch_failing_since.pop, "altitude", None)
        self.unreachable_origin()
        grace = server.SELF_DEPLOY_FETCH_GRACE_SECONDS
        for now in (1000.0, 1000.0 + grace - 1):
            fault, _ = self.tick_self_deploy(now)
            fault.assert_not_called()
        fault, _ = self.tick_self_deploy(1000.0 + grace)
        fault.assert_called_once()
        self.assertEqual(fault.call_args.args[0], "self-deploy")
        self.assertIn("altitude: git fetch origin main:", fault.call_args.args[1])

    def test_tick_faults_a_policy_refusal_immediately(self):
        self.addCleanup(server._fetch_failing_since.pop, "altitude", None)
        server._fetch_failing_since["altitude"] = 1000.0  # an earlier fetch failure does not delay a refusal
        (self.repo / "README.md").write_text("dirty deployment\n")
        fault, _ = self.tick_self_deploy(1001.0)
        fault.assert_called_once()
        self.assertIn("uncommitted changes", fault.call_args.args[1])
        self.assertNotIn("altitude", server._fetch_failing_since)

    def test_pull_after_done_leaves_a_failed_fetch_to_the_tick(self):
        self.unreachable_origin()
        with mock.patch("altitude.incidents.system_fault") as fault:
            notes = dispatch.pull_after_done("altitude", {"slug": "landed"})
        fault.assert_not_called()
        self.assertTrue(notes[0].startswith("self-deploy fetch failed; the tick retries: git fetch origin main:"), notes)


if __name__ == "__main__":
    unittest.main()
