"""A `claude --bg` worker that died (`claude agents` state=failed) is a finished-with-fault L2, never "still running"."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-liveness-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, engines, dispatch, l1, tasks as T  # noqa: E402


class TestDeadWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": _TMP, "stacks": ["python"]}})

    def _poll(self, rows, tasks):
        orig_agents, orig_list = engines.claude_agents, S.list_tasks
        engines.claude_agents = lambda: rows
        S.list_tasks = lambda project: tasks
        try:
            # Worker liveness is tested independently of the structural
            # Claude capability adoption gate.
            with mock.patch.object(dispatch, "_adopt_claude_broker_for_task"):
                return dispatch.poll("altitude")
        finally:
            engines.claude_agents, S.list_tasks = orig_agents, orig_list

    def test_failed_worker_without_report_is_died(self):
        task = {"slug": "dead-one", "state": "running", "session_id": "sid-1", "agent_id": "a1"}
        out = self._poll([{"id": "a1", "sessionId": "sid-1", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].get("died"))

    def test_failed_worker_with_report_is_a_normal_finish(self):
        d = S.task_dir("altitude", "reported-then-died"); d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text("blocked report\n")
        (d / "report.json").write_text(json.dumps({"landed": {}}))
        task = {"slug": "reported-then-died", "state": "running", "session_id": "sid-2", "agent_id": "a2"}
        out = self._poll([{"id": "a2", "sessionId": "sid-2", "state": "failed"}], [task])
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].get("died"))

    def test_working_worker_is_not_finished(self):
        task = {"slug": "alive", "state": "running", "session_id": "sid-3", "agent_id": "a3"}
        out = self._poll([{"id": "a3", "sessionId": "sid-3", "state": "working", "status": "busy", "pid": 1}], [task])
        self.assertEqual(out, [])


class TestResumeRebinds(unittest.TestCase):
    def test_resume_binds_task_to_the_new_worker_in_its_worktree(self):
        wt = Path(_TMP) / "wt-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "resume-me").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "resume-me", "title": "resume-me", "created": S.now(), "updated": S.now(), "state": "blocked", "session_id": "old-sid", "agent_id": "old",
                                 "dispatch_id": "resume-me-1", "envelope": {"max_turns": 5}, "worktree": str(wt), "class": "S"})
        seen = {}
        def fake_resume(name, sid, prompt, *, cwd, **kw):
            seen.update(name=name, sid=sid, cwd=str(cwd), env=kw.get("extra_env") or {}); return {"stdout": "", "stderr": "", "returncode": 0}
        rows = [{"id": "old", "name": "altitude/resume-me-1", "sessionId": "old-sid", "state": "failed", "startedAt": 1},
                {"id": "new", "name": "altitude/resume-me-1", "sessionId": "new-sid", "state": "working", "startedAt": 2}]
        with mock.patch.object(engines, "claude_resume_bg", fake_resume), \
             mock.patch.object(engines, "claude_agents", side_effect=[rows[:1], rows[:1], rows]), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree") as validate:
            res = dispatch.resume_session("altitude", "resume-me", "go")
        validate.assert_called_once()
        self.assertEqual(seen["cwd"], str(wt))
        self.assertEqual(seen["env"].get("ALTITUDE_SESSION_KEY"), "altitude--resume-me-1")
        t = S.load_task("altitude", "resume-me")
        self.assertEqual((t["agent_id"], t["session_id"]), ("new", "new-sid"))
        self.assertEqual(res["agent"]["id"], "new")

    def test_terminal_move_during_running_claude_resume_stops_the_new_worker(self):
        wt = Path(_TMP) / "wt-running-resume"; wt.mkdir(exist_ok=True)
        slug = "running-resume"
        S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {
            "slug": slug, "title": slug, "created": S.now(), "updated": S.now(), "state": "running",
            "session_id": "old-sid", "agent_id": "old", "dispatch_id": f"{slug}-1",
            "envelope": {"max_turns": 5}, "worktree": str(wt), "class": "S",
        })

        def terminal_resume(*_args, **_kwargs):
            T.park("altitude", slug, "cancel during resume")
            return {"stdout": "", "stderr": "", "returncode": 0}

        old = [{"id": "old", "name": f"altitude/{slug}-1", "sessionId": "old-sid", "state": "failed"}]
        new = [{"id": "new", "name": f"altitude/{slug}-1", "sessionId": "new-sid", "state": "working"}]
        stopped = [{**new[0], "state": "stopped"}]
        with mock.patch.object(engines, "claude_resume_bg", side_effect=terminal_resume), \
             mock.patch.object(engines, "claude_agents",
                               side_effect=[old, old, old, old + new, stopped]), \
             mock.patch.object(engines, "claude_stop", return_value="stopped") as stop, \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"):
            with self.assertRaisesRegex(T.TransitionError, "generation changed"):
                dispatch.resume_session("altitude", slug, "go")
        stop.assert_called_once_with("new")
        task = S.load_task("altitude", slug)
        self.assertEqual(task["state"], "parked")
        self.assertNotIn("pending_resume", task)

    def test_resume_without_worktree_is_a_dispatch_again(self):
        S.task_dir("altitude", "no-wt").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": "no-wt", "title": "no-wt", "created": S.now(), "updated": S.now(), "state": "blocked", "session_id": "s", "agent_id": "a", "dispatch_id": "no-wt-1",
                                 "envelope": {"max_turns": 5}, "worktree": str(Path(_TMP) / "gone"), "class": "S"})
        with self.assertRaises(T.TransitionError):
            dispatch.resume_session("altitude", "no-wt", "go")

    def test_resume_provenance_failure_never_launches_the_engine(self):
        wt = Path(_TMP) / "wt-refused-resume"; wt.mkdir(exist_ok=True)
        S.task_dir("altitude", "refused-resume").mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {
            "slug": "refused-resume", "title": "refused-resume", "created": S.now(), "updated": S.now(),
            "state": "blocked", "session_id": "old", "agent_id": "old-agent",
            "dispatch_id": "refused-resume-1", "envelope": {"max_turns": 5},
            "worktree": str(wt), "class": "S",
        })

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
             mock.patch.object(
                 dispatch, "_validate_task_worktree", side_effect=T.TransitionError("foreign commit")
             ), \
             mock.patch("altitude.improve.system_fault") as fault, \
             mock.patch.object(engines, "claude_resume_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "foreign commit"):
                dispatch.resume_session("altitude", "refused-resume", "go")

        fault.assert_called_once()
        launch.assert_not_called()
        self.assertNotIn("report_not_before", S.load_task("altitude", "refused-resume"))

    def test_terminal_task_message_is_rejected_before_engine_or_qa_side_effects(self):
        wt = Path(_TMP) / "wt-terminal-message"; wt.mkdir(exist_ok=True)
        slug = "terminal-message"
        S.task_dir("altitude", slug).mkdir(parents=True, exist_ok=True)
        S.save_task("altitude", {"slug": slug, "title": slug, "created": S.now(), "updated": S.now(),
                                 "state": "done", "dispatch_id": f"{slug}-1", "session_id": "old",
                                 "agent_id": "old-agent", "worktree": str(wt), "class": "S"})

        with mock.patch.object(engines, "claude_resume_bg") as launch:
            with self.assertRaisesRegex(T.TransitionError, "running or blocked"):
                dispatch.resume_session("altitude", slug, "stale message")

        launch.assert_not_called()
        self.assertFalse((S.task_dir("altitude", slug) / "qa.md").exists())


class TestInitialClaudeDispatchCancellation(unittest.TestCase):
    _number = 0

    def setUp(self):
        config.ensure_root()
        config.save_projects({"altitude": {
            "name": "altitude", "path": _TMP, "stacks": ["python"], "wip": 20,
        }})

    def _task(self) -> tuple[str, Path, Path]:
        type(self)._number += 1
        slug = f"claude-launch-race-{self._number}"
        worktree = Path(_TMP) / f"wt-{slug}"
        worktree.mkdir(exist_ok=True)
        task = T.new("altitude", slug, "S", "request")
        T.auto_approve("altitude", task["slug"], "test")
        persona = Path(_TMP) / f"persona-{slug}.md"
        persona.write_text("persona\n")
        return task["slug"], worktree, persona

    def _stack(self, slug: str, worktree: Path, persona: Path):
        # contextlib.ExitStack is intentionally imported lazily to keep this
        # test's production-like patch set readable.
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(dispatch, "_l2_choice", return_value={
            "engine": "claude", "why": "test"}))
        stack.enter_context(mock.patch.object(dispatch, "wip_hold", return_value=None))
        stack.enter_context(mock.patch.object(
            dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40))
        stack.enter_context(mock.patch.object(dispatch, "_task_worktree", return_value=worktree))
        stack.enter_context(mock.patch.object(
            dispatch, "_validate_task_worktree", return_value=Path(_TMP) / "trusted-git"))
        stack.enter_context(mock.patch.object(dispatch.git_policy, "require_hooks_installed"))
        stack.enter_context(mock.patch.object(dispatch, "build_brief", return_value="brief"))
        stack.enter_context(mock.patch.object(dispatch.rules, "compiled_persona", return_value=persona))
        stack.enter_context(mock.patch.object(dispatch, "session_settings", return_value=Path(_TMP) / "settings"))
        stack.enter_context(mock.patch.object(dispatch, "worktree_branch", return_value=f"worktree-{slug}"))
        stack.enter_context(mock.patch.object(dispatch, "_ensure_claude_broker", return_value=SimpleNamespace(
            env=lambda: {"ALTITUDE_BROKER_TOKEN": "token"},
            socket_path=Path(_TMP) / "broker" / "broker.fifo")))
        stack.enter_context(mock.patch.object(l1, "stop_all", return_value=True))
        return stack

    def test_park_during_initial_launch_stops_exact_new_worker_and_revokes_broker(self):
        slug, worktree, persona = self._task()
        name = f"altitude/{slug}-1"
        new = {"id": "new-agent", "sessionId": "new-session", "name": name,
               "state": "working", "status": "busy"}
        stopped = {**new, "state": "stopped", "status": "exited"}

        def launch(*_args, **_kwargs):
            T.park("altitude", slug, "cancel during initial Claude launch")
            return {"stdout": "", "stderr": "", "returncode": 0, "agent": new}

        with self._stack(slug, worktree, persona), \
             mock.patch.object(dispatch.engines, "claude_bg", side_effect=launch), \
             mock.patch.object(dispatch.engines, "claude_agents",
                               side_effect=[[], [], [new], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop, \
             mock.patch.object(dispatch, "_close_claude_broker") as close:
            with self.assertRaisesRegex(T.TransitionError, "pending dispatch generation changed"):
                dispatch.run("altitude", slug)

        stop.assert_called_once_with("new-agent")
        self.assertTrue(any(call.args == ("altitude", slug, mock.ANY) for call in close.call_args_list))
        task = S.load_task("altitude", slug)
        self.assertEqual(task["state"], "parked")
        self.assertNotIn("pending_dispatch", task)
        self.assertNotIn("claude_broker", task)
        self.assertNotIn("claude_cleanup_pending", task)

    def test_reject_before_initial_launch_precheck_never_calls_engine(self):
        slug, worktree, persona = self._task()
        original = dispatch._require_claude_claim

        def reject_then_check(project, task_slug, claim_field, dispatch_id, generation):
            T.reject(project, task_slug, "cancel immediately before Claude launch")
            return original(project, task_slug, claim_field, dispatch_id, generation)

        with self._stack(slug, worktree, persona), \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch, "_require_claude_claim", side_effect=reject_then_check), \
             mock.patch.object(dispatch.engines, "claude_bg") as launch, \
             mock.patch.object(dispatch, "_close_claude_broker"):
            with self.assertRaisesRegex(T.TransitionError, "cancelled or replaced"):
                dispatch.run("altitude", slug)

        launch.assert_not_called()
        self.assertEqual(S.load_task("altitude", slug)["state"], "rejected")

    def test_cancelled_launch_waits_for_late_inventory_then_stops_it(self):
        slug, _worktree, _persona = self._task()
        dispatch_id = f"{slug}-1"
        name = f"altitude/{dispatch_id}"
        new = {"id": "late-agent", "sessionId": "late-session", "name": name,
               "state": "working", "status": "busy"}
        stopped = {**new, "state": "stopped", "status": "exited"}

        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents",
                               side_effect=[[], [], [new], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop, \
             mock.patch.object(dispatch.time, "sleep"):
            proved = dispatch._cancel_claude_launch(
                "altitude", slug, dispatch_id, "g" * 24, name, set(),
                reason="deterministic late registration")

        self.assertTrue(proved)
        stop.assert_called_once_with("late-agent")
        self.assertNotIn("claude_cleanup_pending", S.load_task("altitude", slug))

    def test_terminal_task_keeps_unseen_launch_visible_until_poll_stops_it(self):
        slug, _worktree, _persona = self._task()
        dispatch_id = f"{slug}-1"
        generation = "f" * 24
        name = f"altitude/{dispatch_id}"
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch.time, "sleep"):
            self.assertFalse(dispatch._cancel_claude_launch(
                "altitude", slug, dispatch_id, generation, name, set(),
                reason="launch returned before registry publication"))
        task = S.load_task("altitude", slug)
        self.assertIn("claude_cleanup_pending", task)
        task["state"] = "parked"
        S.save_task("altitude", task)

        late = {"id": "poll-late-agent", "sessionId": "poll-late-session", "name": name,
                "state": "working", "status": "busy"}
        stopped = {**late, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.S, "list_tasks", return_value=[S.load_task("altitude", slug)]), \
             mock.patch.object(dispatch.engines, "claude_agents",
                               side_effect=[[late], [late], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop:
            self.assertEqual(dispatch.poll("altitude"), [])

        stop.assert_called_once_with("poll-late-agent")
        final = S.load_task("altitude", slug)
        self.assertEqual(final["state"], "parked")
        self.assertNotIn("claude_cleanup_pending", final)

    def test_terminal_move_durably_tracks_worker_that_registers_after_launcher_dies(self):
        slug, _worktree, _persona = self._task()
        dispatch_id = f"{slug}-1"
        generation = "d" * 24
        name = f"altitude/{dispatch_id}"
        with S.project_lock("altitude"):
            task = S.load_task("altitude", slug)
            task["pending_dispatch"] = {
                "dispatch_id": dispatch_id, "engine": "claude",
                "generation": generation, "started": S.now(),
                "excluded_agent_ids": ["old-same-name"],
                "launch_attempted_at": S.now(),
            }
            S.save_task("altitude", task)

        with mock.patch.object(l1, "stop_all", return_value=True), \
             mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch.time, "sleep"):
            T.park("altitude", slug, "operator cancelled while launcher disappeared")

        terminal = S.load_task("altitude", slug)
        self.assertEqual(terminal["state"], "parked")
        self.assertNotIn("pending_dispatch", terminal)
        cleanup = terminal["claude_cleanup_pending"]
        self.assertEqual(cleanup["dispatch_id"], dispatch_id)
        self.assertEqual(cleanup["excluded_agent_ids"], ["old-same-name"])

        late = {"id": "late-after-crash", "sessionId": "late-session", "name": name,
                "state": "working", "status": "busy"}
        stopped = {**late, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.S, "list_tasks", return_value=[terminal]), \
             mock.patch.object(dispatch.engines, "claude_agents",
                               side_effect=[[late], [late], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop:
            self.assertEqual(dispatch.poll("altitude"), [])

        stop.assert_called_once_with("late-after-crash")
        final = S.load_task("altitude", slug)
        self.assertEqual(final["state"], "parked")
        self.assertNotIn("claude_cleanup_pending", final)

    def test_failed_direct_hint_stop_cannot_turn_empty_inventory_into_proof(self):
        slug, _worktree, _persona = self._task()
        dispatch_id = f"{slug}-1"
        generation = "e" * 24
        name = f"altitude/{dispatch_id}"
        hinted = {"id": "hinted-late", "sessionId": "hinted-session", "name": name,
                  "state": "working", "status": "busy"}
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch.engines, "claude_stop", side_effect=RuntimeError("not visible yet")), \
             mock.patch.object(dispatch.time, "sleep"):
            self.assertFalse(dispatch._cancel_claude_launch(
                "altitude", slug, dispatch_id, generation, name, set(), hinted=hinted,
                reason="hint returned before stop and inventory publication"))
        task = S.load_task("altitude", slug)
        self.assertEqual(task["claude_cleanup_pending"]["agent_ids"], ["hinted-late"])
        task["state"] = "parked"
        S.save_task("altitude", task)

        stopped = {**hinted, "state": "stopped", "status": "exited"}
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.S, "list_tasks", return_value=[S.load_task("altitude", slug)]), \
             mock.patch.object(dispatch.engines, "claude_agents", side_effect=[[stopped], [stopped]]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="stopped") as stop:
            self.assertEqual(dispatch.poll("altitude"), [])
        stop.assert_called_once_with("hinted-late")
        self.assertNotIn("claude_cleanup_pending", S.load_task("altitude", slug))

    def test_successful_direct_hint_stop_without_inventory_remains_cleanup_work(self):
        slug, _worktree, _persona = self._task()
        dispatch_id = f"{slug}-1"
        generation = "c" * 24
        name = f"altitude/{dispatch_id}"
        hinted = {"id": "acknowledged-but-unpublished", "sessionId": "late-session",
                  "name": name, "state": "working", "status": "busy"}
        with mock.patch.object(dispatch, "_close_claude_broker"), \
             mock.patch.object(dispatch.engines, "claude_agents", return_value=[]), \
             mock.patch.object(dispatch.engines, "claude_stop", return_value="accepted") as stop, \
             mock.patch.object(dispatch.time, "sleep"):
            self.assertFalse(dispatch._cancel_claude_launch(
                "altitude", slug, dispatch_id, generation, name, set(), hinted=hinted,
                reason="stop acknowledged before registry publication"))

        stop.assert_called_once_with("acknowledged-but-unpublished")
        cleanup = S.load_task("altitude", slug)["claude_cleanup_pending"]
        self.assertEqual(cleanup["agent_ids"], ["acknowledged-but-unpublished"])


if __name__ == "__main__":
    unittest.main()
