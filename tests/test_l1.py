"""Decision 45: `alt l1 run` — detached run on either engine, own worktree, cap enforced, record closed with the result."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l1-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
os.environ["ALTITUDE_CODEX_RUNTIME_ROOT"] = str(_TMP / "codex-runtime")
if shutil.which("codex"):
    os.environ.setdefault("ALTITUDE_TEST_REAL_CODEX", str(Path(shutil.which("codex")).resolve()))
FAKE = _TMP / "bin"; FAKE.mkdir()
(FAKE / "codex").write_text('''#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
out = args[args.index("-o") + 1]
open(out, "w").write("done.\\nRESULT: no PR — fake codex finished " + ("review" if "--output-schema" in args else "build"))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 7}}))
open(sys.argv[-1] + ".unused", "a") if False else None
''')
(FAKE / "claude").write_text('''#!/usr/bin/env python3
import sys, json
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "RESULT: #42 — fake claude opened PR"}], "usage": {"input_tokens": 5, "output_tokens": 3}}}))
print(json.dumps({"type": "result", "subtype": "success", "result": "RESULT: #42 — fake claude opened PR", "session_id": "fake-sid", "is_error": False}))
''')
# Engine binaries are copied into a private immutable runtime before launch;
# group/other-writable fakes must fail the same trust check as production.
for f in ("codex", "claude"):
    (FAKE / f).chmod(0o500)
os.environ["CODEX_BIN"] = str(FAKE / "codex")
os.environ["CLAUDE_BIN"] = str(FAKE / "claude")
_REAL_PATH = os.environ.get("PATH", "")
os.environ["PATH"] = f"{FAKE}:{_REAL_PATH}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, l1, route, monitor  # noqa: E402

REPO = _TMP / "repo"


def _wait_done(project, slug, name, secs=30):
    for _ in range(secs * 4):
        r = l1.load(project, slug, name)
        if r and r.get("done"):
            return r
        time.sleep(0.25)
    raise AssertionError(f"run {name} did not finish: {l1.load(project, slug, name)}")


class TestL1Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        REPO.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=REPO, check=True)
        (REPO / ".gitignore").write_text(".claude/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=REPO, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=REPO, check=True)
        remote = _TMP / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], cwd=REPO, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=REPO, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=REPO, check=True)
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO), "stacks": ["python"]}})
        cls._real_monitor_quota = monitor.quota
        cls._real_route_quota_codex = route.quota_codex
        monitor.quota = lambda: {"known": False}
        route.quota_codex = lambda: {"known": False}
        cls._codex_runtime = mock.patch.object(l1.engines, "CODEX_RUNTIME_ROOT", _TMP / "codex-runtime")
        cls._codex_runtime.start()
        # The managed test sandbox remaps host root to nobody. Production keeps
        # the root-owned /usr/bin/bwrap trust gate; these lifecycle tests keep
        # exercising the wrapper below a mocked namespace-launch boundary.
        cls._pid_isolation = mock.patch.object(
            l1.engines, "generation_isolation_command", side_effect=lambda argv: list(argv))
        cls._pid_isolation.start()

    @classmethod
    def tearDownClass(cls):
        cls._pid_isolation.stop()
        cls._codex_runtime.stop()
        monitor.quota = cls._real_monitor_quota
        route.quota_codex = cls._real_route_quota_codex
        os.environ["PATH"] = _REAL_PATH

    def _task(self, slug, cls="S", engine=None):
        T.new("altitude", slug, cls, "req", actor="l3", engine=engine)
        worktree = REPO / ".claude" / "worktrees" / slug
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", f"worktree-{slug}", str(worktree), "origin/main"],
            cwd=REPO, check=True,
        )
        with S.project_lock("altitude"):
            t = S.load_task("altitude", slug)
            origin_sha = subprocess.run(["git", "rev-parse", "origin/main"], cwd=REPO, check=True,
                                        capture_output=True, text=True).stdout.strip()
            t.update({"worktree": str(worktree), "branch": f"worktree-{slug}",
                      "state": "running", "dispatch_id": f"{slug}-1", "origin_sha": origin_sha})
            S.save_task("altitude", t)
        brief = S.task_dir("altitude", slug) / "sub-1.md"; brief.write_text("# sub-brief\nchange one thing\n")
        return slug, brief

    def test_codex_by_default_policy_in_its_own_worktree(self):
        slug, brief = self._task("l1-codex")
        rec = l1.start("altitude", slug, brief)
        self.assertEqual(rec["engine"], "codex"); self.assertIn("default policy", rec["why"])
        self.assertTrue(rec["branch"].startswith("l1/")); self.assertTrue(Path(rec["worktree"]).is_dir())
        self.assertNotEqual(Path(rec["worktree"]).resolve(), REPO.resolve(), "an implementer never works in the L2's checkout")
        done = _wait_done("altitude", slug, rec["name"])
        self.assertIsNone(done["result"]["error"]); self.assertIsNone(done["result"]["pr"])
        self.assertIn("fake codex finished build", done["result"]["summary"])
        self.assertEqual(done["result"]["usage"]["input_tokens"], 50)
        w = l1.wait("altitude", slug, rec["name"], timeout=5)
        self.assertFalse(w["waiting"]); self.assertEqual(w["run"]["name"], rec["name"])
        kinds = [e["kind"] for e in S.read_events("altitude", slug)] if hasattr(S, "read_events") else ["l1-started", "l1-finished"]
        self.assertIn("l1-started", kinds); self.assertIn("l1-finished", kinds)

    def test_task_engine_forces_claude_and_pr_is_parsed(self):
        slug, brief = self._task("l1-claude", engine="claude")
        rec = l1.start("altitude", slug, brief)
        self.assertEqual(rec["engine"], "claude"); self.assertIn("forced on the task", rec["why"])
        done = _wait_done("altitude", slug, rec["name"])
        self.assertEqual(done["result"]["pr"], 42)

    def test_in_flight_cap_and_reviewer_on_the_other_engine(self):
        slug, brief = self._task("l1-cap")  # S: one implementer in flight
        first = l1.start("altitude", slug, brief)
        # a second implementer while the first is (possibly) still running must be refused by the cap
        with S.project_lock("altitude"):
            pass
        rec = l1.load("altitude", slug, first["name"])
        if not rec.get("done"):
            with self.assertRaises(T.TransitionError):
                l1.start("altitude", slug, brief)
        _wait_done("altitude", slug, first["name"])
        rev = l1.start("altitude", slug, brief, role="reviewer")
        self.assertEqual(rev["engine"], "claude", "author was codex → reviewer takes claude")
        self.assertEqual(
            Path(rev["worktree"]).resolve(), (REPO / ".claude" / "worktrees" / slug).resolve(),
            "a reviewer reads in the L2 checkout, without another worktree",
        )
        done = _wait_done("altitude", slug, rev["name"])
        self.assertIsNone(done["result"]["error"])
        st = l1.status("altitude", slug)
        self.assertEqual([r["role"] for r in st], ["implementer", "reviewer"])

    def test_reviewer_git_reads_require_the_registered_task_pointer(self):
        slug, _brief = self._task("reviewer-git-authority")
        worktree = REPO / ".claude" / "worktrees" / slug
        git_dir, common = l1._reviewer_git_read_paths("altitude", slug, worktree)
        self.assertEqual(git_dir.parent, common / "worktrees")
        pointer = worktree / ".git"
        original = pointer.read_text()
        pointer.write_text("gitdir: /tmp/attacker-reviewer-git\n")
        try:
            with self.assertRaises(T.TransitionError):
                l1._reviewer_git_read_paths("altitude", slug, worktree)
        finally:
            pointer.write_text(original)

    def test_cli_counts_as_a_launch_for_the_cap_hook(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("cap", Path(__file__).resolve().parent.parent / "hooks" / "subagent_cap.py")
        src = (Path(__file__).resolve().parent.parent / "hooks" / "subagent_cap.py").read_text()
        ns = {}
        exec(src.split("\ninp = json.load")[0], ns)  # the regex + is_launch, without the hook's stdin main
        self.assertTrue(ns["is_launch"]("bin/alt l1 run --brief x.md"))
        self.assertTrue(ns["is_launch"]("alt l1 run --role reviewer --brief r.md"))
        self.assertFalse(ns["is_launch"]("alt l1 wait implementer-1"))
        self.assertFalse(ns["is_launch"]("alt l1 status"))

    def test_immutable_parent_sha_is_used_if_l2_head_moves_after_validation(self):
        slug, brief = self._task("l1-parent-race")
        parent = REPO / ".claude" / "worktrees" / slug
        captured = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=parent, check=True, capture_output=True, text=True,
        ).stdout.strip()
        real_check = l1.git_policy.commits_missing_task_trailer

        def advance_after_check(*args, **kwargs):
            missing = real_check(*args, **kwargs)
            (parent / "raced.txt").write_text("later\n")
            subprocess.run(["git", "add", "raced.txt"], cwd=parent, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "later",
                 "-m", f"Altitude-Task: altitude/{slug}"],
                cwd=parent, check=True,
            )
            return missing

        with mock.patch.object(l1.git_policy, "commits_missing_task_trailer", side_effect=advance_after_check):
            rec = l1.start("altitude", slug, brief)

        nested_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=rec["worktree"], check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(nested_head, captured)
        self.assertFalse((Path(rec["worktree"]) / "raced.txt").exists())
        _wait_done("altitude", slug, rec["name"])

    def test_z_implementer_refuses_parent_commit_from_another_provenance(self):
        slug, brief = self._task("l1-bad-parent")
        parent = REPO / ".claude" / "worktrees" / slug
        (parent / "direct.txt").write_text("direct\n")
        subprocess.run(["git", "add", "direct.txt"], cwd=parent, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "direct commit"],
            cwd=parent, check=True,
        )

        with self.assertRaisesRegex(T.TransitionError, "without exact.*provenance"):
            l1.start("altitude", slug, brief)
        failed = l1.list_runs("altitude", slug)
        self.assertEqual(len(failed), 1); self.assertEqual(failed[0]["state"], "stopped")

    def test_z_explicit_main_cwd_is_refused(self):
        slug, brief = self._task("l1-explicit-main")
        with self.assertRaisesRegex(T.TransitionError, "registered L2 or completed L1 worktree"):
            l1.start("altitude", slug, brief, cwd=str(REPO))
        failed = l1.list_runs("altitude", slug)
        self.assertEqual(len(failed), 1); self.assertEqual(failed[0]["state"], "stopped")

    def test_registered_task_worktree_is_allowed_for_review_fix_round(self):
        slug, brief = self._task("l1-review-fix")
        parent = REPO / ".claude" / "worktrees" / slug
        rec = l1.start("altitude", slug, brief, cwd=str(parent))
        self.assertNotEqual(Path(rec["worktree"]).resolve(), parent.resolve())
        self.assertTrue(rec["isolated_clone"]); self.assertTrue(rec["branch"].startswith("l1/"))
        _wait_done("altitude", slug, rec["name"])


    def test_non_running_or_stale_dispatch_refuses_launch(self):
        slug, brief = self._task("l1-stale-launch")
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug); task["state"] = "parked"; S.save_task("altitude", task)
        with self.assertRaisesRegex(T.TransitionError, "current running dispatch"):
            l1.start("altitude", slug, brief)
        self.assertEqual(l1.list_runs("altitude", slug), [])

    def test_reservation_serializes_cap_and_duplicate_name(self):
        slug, brief = self._task("l1-reservation-race")
        entered, release, errors = threading.Event(), threading.Event(), []
        real_prepare = l1._prepare_isolated_clone

        def held(*args, **kwargs):
            result = real_prepare(*args, **kwargs)
            entered.set(); release.wait(10)
            return result

        def launch():
            try:
                l1.start("altitude", slug, brief, name="only")
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(l1, "_prepare_isolated_clone", side_effect=held):
            thread = threading.Thread(target=launch); thread.start()
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(T.TransitionError, "L1 cap"):
                l1.start("altitude", slug, brief, name="second")
            with self.assertRaisesRegex(T.TransitionError, "already exists"):
                l1.start("altitude", slug, brief, role="reviewer", name="only")
            release.set(); thread.join(15)
        self.assertEqual(errors, [])
        _wait_done("altitude", slug, "only")

    def test_cancel_during_preparation_removes_clone_and_never_launches(self):
        slug, brief = self._task("l1-cancel-prepare")
        prepared = []
        real_prepare = l1._prepare_isolated_clone

        def cancel_after_prepare(*args, **kwargs):
            result = real_prepare(*args, **kwargs); prepared.append(result[0])
            with S.project_lock("altitude"):
                task = S.load_task("altitude", slug); task["state"] = "parked"; S.save_task("altitude", task)
            return result

        with mock.patch.object(l1, "_prepare_isolated_clone", side_effect=cancel_after_prepare), \
             mock.patch.object(l1, "_launch_wrapper") as launch:
            with self.assertRaisesRegex(T.TransitionError, "changed during L1 preparation"):
                l1.start("altitude", slug, brief)
        launch.assert_not_called(); self.assertFalse(prepared[0].exists())
        self.assertEqual(l1.list_runs("altitude", slug)[0]["state"], "stopped")

    def test_cancel_after_wrapper_start_kills_child_and_removes_clone(self):
        slug, brief = self._task("l1-cancel-after-popen")
        clone = []
        real_prepare = l1._prepare_isolated_clone

        def remember(*args, **kwargs):
            result = real_prepare(*args, **kwargs); clone.append(result[0]); return result

        child = mock.Mock(pid=987654); child.poll.return_value = None

        def started(*_args, **_kwargs):
            with S.project_lock("altitude"):
                task = S.load_task("altitude", slug); task["state"] = "parked"; S.save_task("altitude", task)
            return child

        with mock.patch.object(l1, "_prepare_isolated_clone", side_effect=remember), \
             mock.patch.object(l1, "_launch_wrapper", side_effect=started), \
             mock.patch.object(l1, "_proc_start_time", return_value=11), \
             mock.patch.object(l1, "_kill_unpersisted") as killed:
            with self.assertRaisesRegex(T.TransitionError, "changed while the L1 wrapper started"):
                l1.start("altitude", slug, brief)
        killed.assert_called_once_with(child); self.assertFalse(clone[0].exists())

    def test_codex_implementer_has_no_network_shared_git_root_or_raw_state(self):
        slug, brief = self._task("l1-isolation-config")
        child = mock.Mock(pid=987655); child.poll.return_value = None
        captured = {}

        def fake_exec(_prompt, **kwargs):
            captured.update(kwargs)
            return {"text": "RESULT: no PR — checked", "error": None, "usage": {}, "structured": None,
                    "returncode": 0, "raw_stdout": "", "raw_stderr": ""}

        with mock.patch.object(l1, "_launch_wrapper", return_value=child), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            rec = l1.start("altitude", slug, brief)
        with mock.patch.object(l1.engines, "codex_exec", side_effect=fake_exec):
            done = l1.exec_run("altitude", slug, rec["name"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(captured["sandbox"], "workspace-write")
        self.assertFalse(any("sandbox_" in value for value in captured["extra_config"]))
        self.assertEqual(captured["permission_role"], "implementer")
        self.assertTrue(captured["bypass_hook_trust"])
        self.assertNotIn("ALTITUDE_ALT_BROKER_SOCKET", captured["extra_env"])
        self.assertIn("ALTITUDE_ALT_BROKER_FD", captured["extra_env"])
        self.assertEqual(tuple(map(int, (captured["extra_env"]["ALTITUDE_ALT_BROKER_FD"],
                                         captured["extra_env"]["ALTITUDE_ALT_BROKER_LOCK_FD"]))),
                         captured["broker_fds"])
        self.assertIn(Path(rec["git_dir"]), captured["permission_read_paths"])
        self.assertIn("ALTITUDE_HOME", captured["unset_env"])
        self.assertNotIn("ALTITUDE_HOME", captured["extra_env"])
        self.assertFalse(captured["start_new_session"])
        self.assertEqual(Path(captured["extra_env"]["ALTITUDE_TASK_WORKTREE"]).resolve(), Path(rec["worktree"]).resolve())
        self.assertEqual(Path(captured["extra_env"]["ALTITUDE_TRUSTED_GIT_DIR"]).resolve(), Path(rec["git_dir"]).resolve())
        self.assertEqual(Path(captured["extra_env"]["ALTITUDE_REQUIRED_HOOKS"]).resolve(), config.HOOKS.resolve())
        common = Path(subprocess.run(["git", "-C", rec["worktree"], "rev-parse", "--absolute-git-dir"],
                                     check=True, capture_output=True, text=True).stdout.strip())
        self.assertEqual(common.resolve(), Path(rec["git_dir"]).resolve())
        self.assertNotEqual(common.parent, Path(rec["worktree"]).resolve())
        notes, pending = l1.cleanup_isolated_clones("altitude", S.load_task("altitude", slug))
        self.assertEqual(pending, []); self.assertTrue(any("removed merged isolated clone" in note for note in notes))
        self.assertFalse(Path(rec["worktree"]).exists())

    def test_stop_failed_generation_is_retryable(self):
        slug, _brief = self._task("l1-stop-retry")
        rec = {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "running", "lifecycle": "running",
               "wrapper_pid": 11, "wrapper_pid_start": 1, "engine_pid": 22, "engine_pid_start": 2,
               "done": None, "result": None}
        l1.save("altitude", slug, rec)
        with mock.patch.object(l1, "_kill_run_group", return_value=False):
            self.assertFalse(l1.stop("altitude", slug, rec["name"], "g1"))
        self.assertEqual(l1.load("altitude", slug, rec["name"])["state"], "stop-failed")
        with mock.patch.object(l1, "_kill_run_group", return_value=True):
            self.assertTrue(l1.stop("altitude", slug, rec["name"], "g1"))
        self.assertEqual(l1.load("altitude", slug, rec["name"])["state"], "stopped")


    def test_l1_policy_structurally_rejects_merge(self):
        with self.assertRaisesRegex(l1.BrokerDenied, "may not merge"):
            l1._l1_policy(["land", "--merge"], project="altitude", slug="task", worktree=REPO)

    def test_preflight_failures_are_not_billed_and_do_not_exhaust_launch_cap(self):
        slug, brief = self._task("l1-preflight-billing")
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug)
            task["envelope"] = {**(task.get("envelope") or {}), "subagent_launches": 2}
            S.save_task("altitude", task)
        for index in range(4):
            with mock.patch.object(l1, "_prepare_isolated_clone", side_effect=T.TransitionError("preflight")):
                with self.assertRaisesRegex(T.TransitionError, "preflight"):
                    l1.start("altitude", slug, brief, name=f"failed-{index}")
        failed = l1.list_runs("altitude", slug)
        self.assertEqual(len(failed), 4)
        self.assertTrue(all(run["state"] == "stopped" and not run["billed"] for run in failed))
        rec = l1.start("altitude", slug, brief, name="real")
        done = _wait_done("altitude", slug, rec["name"])
        self.assertTrue(done["billed"])
        self.assertEqual(l1.billed_count("altitude", slug, f"{slug}-1"), 1)

    def test_wrapper_without_pid_identity_is_killed_unbilled_and_clone_removed(self):
        slug, brief = self._task("l1-wrapper-identity")
        child = mock.Mock(pid=987601); child.poll.return_value = None
        clones = []
        real_prepare = l1._prepare_isolated_clone

        def remember(*args, **kwargs):
            result = real_prepare(*args, **kwargs); clones.append((result[0], result[1])); return result

        with mock.patch.object(l1, "_prepare_isolated_clone", side_effect=remember), \
             mock.patch.object(l1, "_launch_wrapper", return_value=child), \
             mock.patch.object(l1, "_proc_start_time", return_value=None), \
             mock.patch.object(l1, "_kill_unpersisted") as killed:
            with self.assertRaisesRegex(T.TransitionError, "PID identity"):
                l1.start("altitude", slug, brief)
        killed.assert_called_once_with(child)
        self.assertFalse(clones[0][0].exists()); self.assertFalse(clones[0][1].exists())
        rec = l1.list_runs("altitude", slug)[0]
        self.assertEqual(rec["state"], "stopped"); self.assertFalse(rec["billed"])

    def test_engine_without_pid_identity_is_never_billed(self):
        slug, _brief = self._task("l1-engine-identity")
        rec = {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "starting",
               "lifecycle": "starting", "billed": False}
        l1.save("altitude", slug, rec)
        with mock.patch.object(l1, "_proc_start_time", return_value=None):
            with self.assertRaisesRegex(T.TransitionError, "PID identity"):
                l1._engine_started("altitude", slug, rec["name"], "g1", f"{slug}-1", 22)
        self.assertFalse(l1.load("altitude", slug, rec["name"])["billed"])

    def test_status_keeps_exact_live_engine_when_wrapper_has_exited(self):
        slug, _brief = self._task("l1-engine-outlives-wrapper")
        rec = {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "running",
               "lifecycle": "running", "wrapper_pid": 11, "wrapper_pid_start": 1,
               "engine_pid": 22, "engine_pid_start": 2, "done": None, "result": None}
        l1.save("altitude", slug, rec)
        with mock.patch.object(l1, "_exact_alive", side_effect=lambda pid, started: pid == 22 and started == 2):
            status = l1.status("altitude", slug)
        self.assertEqual(status[0]["state"], "running")
        self.assertIsNone(status[0]["done"])

    def test_broker_rejects_git_pointer_remote_and_hook_mutation(self):
        slug, brief = self._task("l1-broker-authority")
        child = mock.Mock(pid=987602); child.poll.return_value = None
        with mock.patch.object(l1, "_launch_wrapper", return_value=child), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            rec = l1.start("altitude", slug, brief)
        worktree, git_dir = Path(rec["worktree"]), Path(rec["git_dir"])
        self.assertNotIn(git_dir.resolve(), [worktree.resolve(), *worktree.resolve().parents])
        proof = l1.validate_broker_worktree("altitude", slug, rec, worktree, dispatch_id=f"{slug}-1")
        self.assertEqual(Path(proof["GIT_DIR"]).resolve(), git_dir.resolve())
        pointer = worktree / ".git"; original_pointer = pointer.read_text()
        pointer.write_text("gitdir: /tmp/attacker-git-dir\n")
        with self.assertRaisesRegex(l1.BrokerDenied, "pointer changed"):
            l1.validate_broker_worktree("altitude", slug, rec, worktree, dispatch_id=f"{slug}-1")
        pointer.write_text(original_pointer)
        self.assertEqual(l1._trusted_git(rec, "remote", "set-url", "origin", "https://evil.invalid/x").returncode, 0)
        with self.assertRaisesRegex(l1.BrokerDenied, "origin URL changed"):
            l1.validate_broker_worktree("altitude", slug, rec, worktree, dispatch_id=f"{slug}-1")
        self.assertEqual(l1._trusted_git(rec, "remote", "set-url", "origin", rec["origin_url"]).returncode, 0)
        self.assertEqual(l1._trusted_git(rec, "config", "core.hooksPath", "/tmp/evil-hooks").returncode, 0)
        with self.assertRaisesRegex(l1.BrokerDenied, "hook path changed"):
            l1.validate_broker_worktree("altitude", slug, rec, worktree, dispatch_id=f"{slug}-1")
        l1._trusted_git(rec, "config", "core.hooksPath", rec["hooks_path"])
        l1._remove_owned_clone("altitude", slug, worktree, git_dir)

    def test_squash_merged_exact_pr_head_allows_cleanup(self):
        slug, brief = self._task("l1-squash-cleanup")
        child = mock.Mock(pid=987603); child.poll.return_value = None
        with mock.patch.object(l1, "_launch_wrapper", return_value=child), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            rec = l1.start("altitude", slug, brief)
        worktree = Path(rec["worktree"])
        (worktree / "squashed.txt").write_text("squashed\n")
        self.assertEqual(l1._trusted_git(rec, "add", "squashed.txt").returncode, 0)
        committed = l1._trusted_git(rec, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                                    "-m", "task change", "-m", f"Altitude-Task: altitude/{slug}")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        head_sha = l1._trusted_git(rec, "rev-parse", "HEAD").stdout.strip()
        with S.project_lock("altitude"):
            current = l1.load("altitude", slug, rec["name"])
            current.update({"state": "done", "lifecycle": "done", "done": S.now(), "head_sha": head_sha,
                            "result": {"pr": 77, "error": None, "summary": "#77 squash"}})
            l1.save("altitude", slug, current)
        remote = _TMP / "origin.git"
        old_main = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
                                  check=True, capture_output=True, text=True).stdout.strip()
        integration = Path(tempfile.mkdtemp(prefix="altitude-l1-squash-integrate-"))
        try:
            subprocess.run(["git", "clone", "-q", str(remote), str(integration)], check=True)
            subprocess.run(["git", "checkout", "-q", "-b", "main", "origin/main"], cwd=integration, check=True)
            (integration / "squashed.txt").write_text("squashed\n")
            subprocess.run(["git", "add", "squashed.txt"], cwd=integration, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                            "-m", "squash integration"], cwd=integration, check=True)
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=integration, check=True)
            proof = {"number": 77, "state": "MERGED", "mergedAt": "2026-08-30T12:00:00Z",
                     "headRefName": rec["branch"], "headRefOid": head_sha, "baseRefName": "main"}
            with mock.patch.object(l1, "_gh_pr_view", return_value=proof):
                notes, pending = l1.cleanup_isolated_clones("altitude", S.load_task("altitude", slug))
            self.assertEqual(pending, []); self.assertTrue(any("removed merged" in note for note in notes))
            self.assertFalse(worktree.exists()); self.assertFalse(Path(rec["git_dir"]).exists())
        finally:
            subprocess.run(["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", old_main], check=True)
            import shutil
            shutil.rmtree(integration, ignore_errors=True)


    def test_broker_git_authority_is_consumed_for_parent_but_not_inherited_by_clone_or_wrapper(self):
        slug, brief = self._task("l1-broker-parent-env")
        parent = REPO / ".claude" / "worktrees" / slug
        linked_git_dir = subprocess.run(["git", "-C", str(parent), "rev-parse", "--absolute-git-dir"],
                                        check=True, capture_output=True, text=True).stdout.strip()
        child = mock.Mock(pid=987604); child.poll.return_value = None
        captured = {}

        def launch(*args, **kwargs):
            captured.update(kwargs["env"]); return child

        with mock.patch.dict(os.environ, {"GIT_DIR": linked_git_dir, "GIT_WORK_TREE": str(parent)}), \
             mock.patch.object(l1, "_launch_wrapper", side_effect=launch), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            rec = l1.start("altitude", slug, brief)
        self.assertNotIn("GIT_DIR", captured); self.assertNotIn("GIT_WORK_TREE", captured)
        self.assertTrue(Path(rec["worktree"]).is_dir()); self.assertTrue(Path(rec["git_dir"]).is_dir())
        l1._remove_owned_clone("altitude", slug, Path(rec["worktree"]), Path(rec["git_dir"]))

    def test_isolated_clone_checkout_ignores_host_smudge_filter(self):
        slug, _brief = self._task("l1-host-filter")
        parent = REPO / ".claude" / "worktrees" / slug
        sentinel = _TMP / "l1-host-filter-ran"
        sentinel.unlink(missing_ok=True)
        global_config = _TMP / "hostile-l1-gitconfig"
        global_config.write_text(
            '[filter "hostile"]\n'
            f'\tsmudge = sh -c "echo ran > {sentinel}; cat"\n'
        )
        (parent / ".gitattributes").write_text("victim filter=hostile\n")
        (parent / "victim").write_text("raw\n")
        subprocess.run(["git", "add", ".gitattributes", "victim"], cwd=parent, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
             "-m", "hostile attributes", "-m", f"Altitude-Task: altitude/{slug}"],
            cwd=parent, check=True,
        )
        clone = git_dir = None
        try:
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
                clone, git_dir, *_rest = l1._prepare_isolated_clone(
                    "altitude", slug, "implementer-1", "generation-hostile", parent, [], explicit=False)
            self.assertEqual((clone / "victim").read_text(), "raw\n")
            self.assertFalse(sentinel.exists())
        finally:
            if clone is not None:
                l1._remove_owned_clone("altitude", slug, clone, git_dir)

    def test_task_immutable_origin_must_be_real_ancestor_of_parent(self):
        slug, brief = self._task("l1-disconnected-origin")
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug); task["origin_sha"] = "f" * 40; S.save_task("altitude", task)
        with self.assertRaisesRegex(T.TransitionError, "not descended"):
            l1.start("altitude", slug, brief)
        self.assertFalse(l1.list_runs("altitude", slug)[0]["billed"])


    def test_child_handshake_recovers_parent_death_before_wrapper_pid_persistence(self):
        slug, _brief = self._task("l1-parent-crash-window")
        rec = {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "launching",
               "lifecycle": "launching", "wrapper_pid": None, "wrapper_pid_start": None,
               "engine_pid": None, "engine_pid_start": None, "billed": False}
        l1.save("altitude", slug, rec)
        starts = {11: 101, 22: 202}
        with mock.patch.object(l1.os, "getpid", return_value=11), \
             mock.patch.object(l1.os, "getpgid", return_value=11), \
             mock.patch.object(l1, "_proc_start_time", side_effect=lambda pid: starts.get(pid)):
            l1._engine_started("altitude", slug, rec["name"], "g1", f"{slug}-1", 22)
        persisted = l1.load("altitude", slug, rec["name"])
        self.assertEqual((persisted["wrapper_pid"], persisted["wrapper_pid_start"]), (11, 101))
        self.assertEqual((persisted["engine_pid"], persisted["engine_pid_start"]), (22, 202))
        self.assertTrue(persisted["billed"])
        live = {11, 22}; signals = []

        def killpg(pgid, sig):
            signals.append((pgid, sig)); live.clear()

        def members(_pgid):
            return ([{"pid": 11, "pid_start": 101}, {"pid": 22, "pid_start": 202}] if live else [])

        with mock.patch.object(l1, "_exact_alive", side_effect=lambda pid, started: pid in live), \
             mock.patch.object(l1, "_group_members", side_effect=members), \
             mock.patch.object(l1.os, "killpg", side_effect=killpg):
            self.assertTrue(l1._kill_run_group(persisted, grace=0))
        self.assertEqual(signals[0][0], 11)


    def test_launcher_reaps_wrapper_process_object_without_blocking(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        child = mock.Mock(pid=987610)

        def wait():
            entered.set()
            release.wait(2)
            finished.set()

        child.wait.side_effect = wait
        l1._reap_wrapper_async(child)
        self.assertTrue(entered.wait(1))
        self.assertFalse(finished.is_set())
        release.set()
        self.assertTrue(finished.wait(1))


    def test_wrapper_completion_reaps_generation_descendants_not_only_its_process_group(self):
        with mock.patch.object(l1.os, "getpid", return_value=11), \
             mock.patch.object(l1, "_proc_start_time", return_value=101), \
             mock.patch.object(l1.engines, "reap_generation_descendants", return_value={}) as reap:
            self.assertEqual(l1._reap_wrapper_descendants(grace=0), [])
        reap.assert_called_once_with(11, 101, grace=0)

    def test_wrapper_launch_is_always_pid_namespace_isolated(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        child = mock.Mock(pid=123)
        with mock.patch.object(l1.engines, "generation_isolation_command",
                               return_value=["trusted-isolator", "--", "worker"]) as isolate, \
             mock.patch.object(l1.subprocess, "Popen", return_value=child) as popen:
            result = l1._launch_wrapper(["worker"], cwd=Path.cwd(), log=subprocess.DEVNULL,
                                        env={"ALTITUDE_L1_START_FD": str(read_fd)})
        os.close(read_fd)
        isolate.assert_called_once_with(["worker"])
        self.assertEqual(popen.call_args.args[0], ["trusted-isolator", "--", "worker"])
        self.assertTrue(result.altitude_pid_isolation)


    def test_fix_round_clones_exact_completed_l1_parent_not_l2_head(self):
        slug, brief = self._task("l1-fix-round-parent")
        children = [mock.Mock(pid=987611), mock.Mock(pid=987612)]
        for child in children:
            child.poll.return_value = None
        with mock.patch.object(l1, "_launch_wrapper", side_effect=children), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            first = l1.start("altitude", slug, brief, name="first")
            prior = Path(first["worktree"])
            (prior / "prior-fix.txt").write_text("prior L1 head\n")
            self.assertEqual(l1._trusted_git(first, "add", "prior-fix.txt").returncode, 0)
            commit = l1._trusted_git(first, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                                     "-m", "prior fix", "-m", f"Altitude-Task: altitude/{slug}")
            self.assertEqual(commit.returncode, 0, commit.stderr)
            prior_head = l1._trusted_git(first, "rev-parse", "HEAD").stdout.strip()
            l2_head = subprocess.run(["git", "-C", str(REPO / ".claude" / "worktrees" / slug),
                                      "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertNotEqual(prior_head, l2_head)
            with S.project_lock("altitude"):
                current = l1.load("altitude", slug, "first")
                current.update({"state": "done", "lifecycle": "done", "done": S.now(), "head_sha": prior_head,
                                "result": {"error": None, "pr": 12, "summary": "first round"}})
                l1.save("altitude", slug, current)
                first = current
            l2_worktree = REPO / ".claude" / "worktrees" / slug
            l2_git_dir = subprocess.run(["git", "-C", str(l2_worktree), "rev-parse", "--absolute-git-dir"],
                                        check=True, capture_output=True, text=True).stdout.strip()
            with mock.patch.dict(os.environ, {"GIT_DIR": l2_git_dir, "GIT_WORK_TREE": str(l2_worktree)}):
                with self.assertRaisesRegex(T.TransitionError, "does not match the explicit --cwd"):
                    l1._validate_parent("altitude", slug, prior, l1.list_runs("altitude", slug), explicit=True)
            with mock.patch.dict(os.environ, {"GIT_DIR": first["git_dir"], "GIT_WORK_TREE": first["worktree"]}):
                second = l1.start("altitude", slug, brief, name="fix", cwd=first["worktree"])
        second_head = l1._trusted_git(second, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(second_head, prior_head)
        self.assertTrue((Path(second["worktree"]) / "prior-fix.txt").is_file())
        l1._remove_owned_clone("altitude", slug, Path(second["worktree"]), Path(second["git_dir"]))
        l1._remove_owned_clone("altitude", slug, Path(first["worktree"]), Path(first["git_dir"]))

    def test_broker_rejects_running_task_dispatch_replacement_after_git_validation(self):
        slug, brief = self._task("l1-broker-dispatch-race")
        child = mock.Mock(pid=987613); child.poll.return_value = None
        with mock.patch.object(l1, "_launch_wrapper", return_value=child), \
             mock.patch.object(l1, "_proc_start_time", return_value=12):
            rec = l1.start("altitude", slug, brief)
        real_check = l1._trusted_missing_task_trailers

        def replace_dispatch(*args, **kwargs):
            answer = real_check(*args, **kwargs)
            task = S.load_task("altitude", slug)
            task["dispatch_id"] = "replacement-dispatch"
            S.save_task("altitude", task)
            return answer

        with mock.patch.object(l1, "_trusted_missing_task_trailers", side_effect=replace_dispatch):
            with self.assertRaisesRegex(l1.BrokerDenied, "generation changed"):
                l1.validate_broker_worktree("altitude", slug, rec, Path(rec["worktree"]),
                                            dispatch_id=f"{slug}-1")
        l1._remove_owned_clone("altitude", slug, Path(rec["worktree"]), Path(rec["git_dir"]))

    def test_kill_group_reaps_unrecorded_survivor_after_known_identities_exit(self):
        rec = {"wrapper_pid": 11, "wrapper_pid_start": 101,
               "engine_pid": 22, "engine_pid_start": 202, "descendants": []}
        members = [{"pid": 33, "pid_start": 303}]
        signals = []

        def killpg(pgid, sig):
            signals.append((pgid, sig)); members.clear()

        with mock.patch.object(l1, "_exact_alive", return_value=False), \
             mock.patch.object(l1, "_group_members", side_effect=lambda _pgid: list(members)), \
             mock.patch.object(l1.os, "killpg", side_effect=killpg):
            self.assertTrue(l1._kill_run_group(rec, grace=0))
        self.assertEqual(signals, [(11, l1.signal.SIGTERM)])

    def test_status_reaps_engine_spawned_before_on_start_persistence(self):
        slug, _brief = self._task("l1-pre-on-start-orphan")
        rec = {"n": 1, "name": "implementer-1", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "starting",
               "lifecycle": "starting", "wrapper_pid": 11, "wrapper_pid_start": 101,
               "engine_pid": None, "engine_pid_start": None, "done": None, "result": None}
        l1.save("altitude", slug, rec)
        members = [{"pid": 33, "pid_start": 303}]
        with mock.patch.object(l1, "_exact_alive", return_value=False), \
             mock.patch.object(l1, "_group_members", side_effect=lambda _pgid: list(members)), \
             mock.patch.object(l1.os, "killpg", side_effect=lambda _pgid, _sig: members.clear()):
            rows = l1.status("altitude", slug)
        self.assertEqual(rows[0]["state"], "done")
        self.assertIn("died before finishing", rows[0]["error"])

    def test_status_recovers_stale_reserve_prepare_and_pre_popen_claims(self):
        for phase in ("reserved", "preparing", "launching"):
            slug, _brief = self._task(f"l1-stale-{phase}")
            generation = f"{phase}-generation"
            clone = l1._clone_root("altitude", slug) / f"worker-{generation[:12]}"
            git_dir = l1._gitdir_root("altitude", slug) / f"worker-{generation[:12]}.git"
            clone.mkdir(parents=True); git_dir.mkdir(parents=True)
            rec = {"n": 1, "name": "worker", "role": "implementer", "engine": "codex",
                   "dispatch_id": f"{slug}-1", "generation": generation, "state": phase,
                   "lifecycle": phase, "started": "2000-01-01T00:00:00+00:00",
                   "launcher_pid": 987654, "launcher_pid_start": 123,
                   "wrapper_pid": None, "wrapper_pid_start": None, "billed": False,
                   "done": None, "result": None}
            l1.save("altitude", slug, rec)
            with mock.patch.object(l1, "_exact_alive", return_value=False), \
                 mock.patch.object(l1, "_alive", return_value=False):
                row = l1.status("altitude", slug)[0]
            self.assertEqual(row["state"], "stopped")
            self.assertFalse(row["done"] is None)
            self.assertFalse(clone.exists()); self.assertFalse(git_dir.exists())

    def test_stale_pid_reuse_releases_exact_reservation_and_unblocks_launch(self):
        slug, brief = self._task("l1-stale-reservation-pid-reuse")
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug)
            task["envelope"] = {**(task.get("envelope") or {}), "subagent_launches": 1}
            S.save_task("altitude", task)
        with l1.LC.launch_lock(config.ROOT, "altitude", slug):
            token = l1.LC.reserve(config.ROOT, "altitude", slug, name="worker", role="reviewer", pid=os.getpid())
        rec = {"n": 1, "name": "worker", "role": "reviewer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "reused-pid-generation", "state": "reserved",
               "lifecycle": "reserved", "started": "2000-01-01T00:00:00+00:00",
               "launcher_pid": os.getpid(), "launcher_pid_start": "definitely-not-this-process",
               "wrapper_pid": None, "wrapper_pid_start": None, "billed": False,
               "reservation": token, "done": None, "result": None}
        l1.save("altitude", slug, rec)

        row = l1.status("altitude", slug)[0]

        self.assertEqual(row["state"], "stopped")
        self.assertIsNone(l1.load("altitude", slug, "worker")["reservation"])
        self.assertEqual(l1.LC.reservations(config.ROOT, "altitude", slug), [])
        with mock.patch.object(l1, "_spawn", side_effect=RuntimeError("new launch reached spawn")):
            with self.assertRaisesRegex(RuntimeError, "new launch reached spawn"):
                l1.start("altitude", slug, brief, role="reviewer", name="replacement")
        self.assertEqual(l1.LC.reservations(config.ROOT, "altitude", slug), [])

    def test_status_retries_stale_stopping_generation(self):
        slug, _brief = self._task("l1-stale-stopping")
        rec = {"n": 1, "name": "worker", "role": "implementer", "engine": "codex",
               "dispatch_id": f"{slug}-1", "generation": "g1", "state": "stopping",
               "lifecycle": "stopping", "started": S.now(), "wrapper_pid": None,
               "wrapper_pid_start": None, "engine_pid": None, "engine_pid_start": None,
               "done": None, "result": None}
        l1.save("altitude", slug, rec)
        row = l1.status("altitude", slug)[0]
        self.assertEqual(row["state"], "stopped")
        self.assertIsNotNone(row["done"])

    def test_historical_billed_run_does_not_consume_new_dispatch_cap(self):
        slug, brief = self._task("l1-dispatch-scoped-cap")
        old = {"n": 1, "name": "old", "role": "implementer", "engine": "codex",
               "dispatch_id": "old-dispatch", "generation": "old-generation", "state": "done",
               "lifecycle": "done", "started": S.now(), "done": S.now(), "billed": True,
               "result": {"error": None, "pr": 1, "summary": "old"}}
        l1.save("altitude", slug, old)
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug)
            task["envelope"] = {**(task.get("envelope") or {}), "subagent_launches": 1}
            S.save_task("altitude", task)
        rec = l1.start("altitude", slug, brief, name="current")
        done = _wait_done("altitude", slug, rec["name"])
        self.assertTrue(done["billed"])
        self.assertEqual(l1.billed_count("altitude", slug, f"{slug}-1"), 1)



if __name__ == "__main__":
    unittest.main()
