"""Incident I-011: an altd restart must not start a second L3 turn on a task that already has one running.

The `claude -p` turn a previous altd started outlives it (systemd KillMode=process, ce856bb), so tick()'s
"no thread in this process" test was judging in-flight flows stale and re-running the proposal-ready turn —
one wasted Fable turn per restart per task, and a `parked -> parked` TransitionError when the loser of the
race finished second. The turn's pid is now recorded on the task, and only a dead pid makes the flow
resumable. The rotation such a turn was about to make is persisted before it runs, so a restart no longer
rotates the very same session again (`l3-rotate old=e9aa9612`, once per restart).
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-restart-turn-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, l3, server, state as S  # noqa: E402

PROJECT = "restartdup"


def register() -> None:
    """Register the project in whichever ROOT is live under `unittest discover`."""
    config.ensure_root()
    P = config.load_projects()
    P[PROJECT] = {"name": PROJECT, "path": config.ROOT.as_posix(), "stacks": ["python"]}
    config.save_projects(P)


def reaped_pid() -> int:
    """A pid that is certainly not running: a child we started and waited for."""
    p = subprocess.Popen(["/bin/true"])
    p.wait()
    return p.pid


def ago(seconds: int) -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=seconds)).isoformat()


class TestRestartDoesNotDuplicateTheTurn(unittest.TestCase):
    """tick()'s resume decision, with the rest of the tick stubbed out."""

    @classmethod
    def setUpClass(cls):
        register()

    def _task(self, slug: str, turn_rec: dict | None) -> str:
        """A requested M task whose proposal is on disk and whose flow was claimed by a previous altd —
        i.e. exactly the task the old stale test resumed on sight."""
        d = S.task_dir(PROJECT, slug)
        d.mkdir(parents=True, exist_ok=True)
        S.write_json(d / "proposal.json", {"summary": "s", "decision_needed": True, "always_list_hits": [],
                                           "estimate": {"turns": 1}})
        S.save_task(PROJECT, {"slug": slug, "title": slug, "class": "M", "state": "requested", "created": ago(7200),
                              "updated": ago(7200), "proposal_started": ago(3600), "proposal_turn": turn_rec})
        return slug

    def _tick(self, slug: str, calls: list, gate: threading.Event | None = None,
              entered: threading.Event | None = None) -> list:
        """One tick over this test's task alone, with the rest of the tick stubbed out. Returns its log lines."""
        logs: list = []

        def flow(project, slug):
            calls.append(slug)
            with S.project_lock(project):                 # the real flow claims the task the same way
                t = S.load_task(project, slug)
                t["proposal_started"] = S.now()
                S.save_task(project, t)
            if entered is not None:
                entered.set()
            if gate is not None:
                gate.wait(10)                            # hold the thread open across the next tick

        orig = (config.load_projects, S.list_tasks, engines.usage_hold, dispatch.poll, dispatch.resume_due,
                server.log, server.drain_hook_faults, server.resume_stranded_reports, server.dispatch_waiting,
                server.weekly_audit, server.morning_digest, server.run_proposal_flow)
        config.load_projects = lambda: {PROJECT: {"name": PROJECT, "path": config.ROOT.as_posix(),
                                                  "stacks": ["python"]}}
        S.list_tasks = lambda project, **kw: [S.load_task(project, slug)]   # this test's task, not the module's
        engines.usage_hold = lambda: None
        dispatch.poll = lambda project: []
        dispatch.resume_due = lambda project: []
        server.log = lambda msg: logs.append(msg)
        server.drain_hook_faults = lambda: None
        server.resume_stranded_reports = lambda project: None
        server.dispatch_waiting = lambda project: None
        server.weekly_audit = lambda project: None
        server.morning_digest = lambda: None
        server.run_proposal_flow = flow
        try:
            server.tick()
        finally:
            (config.load_projects, S.list_tasks, engines.usage_hold, dispatch.poll, dispatch.resume_due,
             server.log, server.drain_hook_faults, server.resume_stranded_reports, server.dispatch_waiting,
             server.weekly_audit, server.morning_digest, server.run_proposal_flow) = orig
        self.assertFalse([m for m in logs if "tick failed" in m], f"the tick itself must not fault: {logs}")
        return logs

    def _join(self, slug: str) -> None:
        t = server._bg.pop(f"propose:{PROJECT}:{slug}", None)
        if t:
            t.join(10)

    def test_a_live_turn_pid_suppresses_the_resume(self):
        """This process stands in for the orphaned turn: its pid is alive, so nothing may be resumed."""
        slug = self._task("live-turn", {"pid": os.getpid(), "started": ago(60)})
        calls: list = []

        self._tick(slug, calls)

        self.assertEqual(calls, [], "a turn that is still running must not be joined by a second one")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["proposal_turn"]["pid"], os.getpid(), "the record of a live turn must be left alone")
        self.assertIsNotNone(t["proposal_started"], "the flow is still claimed by the turn that is running")

    def test_a_dead_turn_pid_resumes_the_flow_exactly_once(self):
        slug = self._task("dead-turn", {"pid": reaped_pid(), "started": ago(60)})
        calls: list = []
        gate, entered = threading.Event(), threading.Event()
        try:
            logs = self._tick(slug, calls, gate=gate, entered=entered)
            self.assertTrue(entered.wait(10), "the resumed flow should have started")
            self.assertIsNone(S.load_task(PROJECT, slug)["proposal_turn"],
                              "the dead turn's record must be cleared by the resume")
            self._tick(slug, calls)                            # the next tick, while the resumed flow still runs
        finally:
            gate.set()
            self._join(slug)
        self.assertEqual(calls, [slug], "exactly one resume: the running flow owns the task from here")
        self.assertTrue([m for m in logs if "proposal flow resumed" in m])

    def test_a_pid_recorded_longer_ago_than_a_turn_may_run_is_not_trusted(self):
        """A recycled pid may delay a resume, never strand the task: the record expires with the engine's
        own turn timeout, which is all that ever bounded an orphaned turn (its kill timer died with altd)."""
        slug = self._task("expired-record", {"pid": os.getpid(), "started": ago(config.L3_TURN_TIMEOUT + 60)})
        calls: list = []
        gate = threading.Event()
        try:
            self._tick(slug, calls, gate=gate)
        finally:
            gate.set()
            self._join(slug)
        self.assertEqual(calls, [slug])

    def test_a_flow_with_no_record_at_all_still_resumes(self):
        """The behaviour for a genuinely dead flow (nothing recorded) is unchanged."""
        slug = self._task("no-record", None)
        calls: list = []
        gate = threading.Event()
        try:
            self._tick(slug, calls, gate=gate)
        finally:
            gate.set()
            self._join(slug)
        self.assertEqual(calls, [slug])


class TestRotationIsPersistedBeforeTheTurn(unittest.TestCase):
    """l3.turn used to save the rotation only once the turn returned, so a restart mid-turn read the old
    session id back and rotated the same session again."""

    @classmethod
    def setUpClass(cls):
        register()

    def test_a_restart_mid_turn_does_not_rotate_the_same_session_again(self):
        l3.save_info(PROJECT, {"session_id": "e9aa9612", "rotate_next": True, "context_percent": 91.0, "turns": 12})
        seen: dict = {}
        orig = engines.claude_print

        def crash(prompt, **kw):
            seen["resume"] = kw.get("resume")
            seen["on_disk"] = l3.info(PROJECT)           # exactly what the next altd reads if we die here
            raise RuntimeError("altd restarted mid-turn")

        def ok(prompt, **kw):
            seen["resume_after_restart"] = kw.get("resume")
            return {"text": "hello", "session_id": "f00d1234", "usage": {}, "context_tokens": 1000, "cost": 0.1,
                    "turns": 1, "structured": None, "error": None, "tools": [], "limited": None}

        try:
            engines.claude_print = crash
            with self.assertRaises(RuntimeError):
                l3.turn(PROJECT, "the turn the restart cut short")
            engines.claude_print = ok
            l3.turn(PROJECT, "the first turn after the restart")
        finally:
            engines.claude_print = orig

        self.assertIsNone(seen["resume"], "a rotating turn starts fresh; it does not resume the old session")
        self.assertIsNone(seen["on_disk"]["session_id"], "the rotated-away session must not still be on disk")
        self.assertFalse(seen["on_disk"]["rotate_next"])
        self.assertEqual(seen["on_disk"]["rotated_from"], "e9aa9612")
        self.assertIsNone(seen["resume_after_restart"], "the next altd must not resume the rotated-away session")
        rotations = [e for e in S.read_project_log(PROJECT)
                     if e.get("kind") == "l3-rotate" and e.get("old") == "e9aa9612"]
        self.assertEqual(len(rotations), 1, "one rotation per session, not one per restart")
        self.assertEqual(l3.info(PROJECT)["session_id"], "f00d1234")
        self.assertEqual(l3.info(PROJECT)["turns"], 1, "a rotated session counts turns from zero")


class TestServerTurnPromptsAreConversation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register()

    def test_server_prompts_put_details_in_records_without_reply_budgets(self):
        prompts: dict[str, str] = {}
        originals = (l3.turn, server.spawn, l3.info, l3.save_info,
                     server.improve.index, server.improve.audit_input)

        def capture(project, prompt, trigger=None, **kwargs):
            prompts[trigger] = prompt
            return {"_turn_started_at": S.now()}

        def immediately(key, fn, *args):
            fn(*args)
            return True

        proposal_slug = "r007-proposal-prompt"
        proposal_dir = S.task_dir(PROJECT, proposal_slug)
        proposal_dir.mkdir(parents=True, exist_ok=True)
        S.write_json(proposal_dir / "proposal.json", {"summary": "s", "decision_needed": True,
                                                       "always_list_hits": [], "estimate": {"turns": 1}})
        S.save_task(PROJECT, {"slug": proposal_slug, "title": proposal_slug, "class": "M", "state": "requested",
                              "created": ago(7200), "updated": ago(7200), "proposal_started": None,
                              "proposal_turn": None})
        report_slug = "r007-report-prompt"
        report_task = {"slug": report_slug, "title": report_slug, "class": "L", "state": "reported",
                       "created": ago(7200), "updated": ago(60), "l3_handled": None}
        S.save_task(PROJECT, report_task)

        l3.turn = capture
        server.spawn = immediately
        l3.info = lambda project: {"session_id": "audit-session"}
        l3.save_info = lambda project, info: None
        server.improve.index = lambda: []
        server.improve.audit_input = lambda project: {}
        try:
            server.start_l3(PROJECT)
            server.run_proposal_flow(PROJECT, proposal_slug)
            server.report_turn(PROJECT, report_task, {"verdict": "blocked", "problems": [], "signals": [],
                                                       "spend": {}, "prs": [], "report": {}})
            server.weekly_audit(PROJECT)
        finally:
            (l3.turn, server.spawn, l3.info, l3.save_info,
             server.improve.index, server.improve.audit_input) = originals

        self.assertEqual(set(prompts), {"start", "proposal-ready", "report-landed", "audit"})
        reply_budget = re.compile(r"≤\s*\d+\s+(?:plain\s+)?(?:sentences|lines)")
        for trigger, prompt in prompts.items():
            matches = reply_budget.findall(prompt)
            self.assertEqual(matches, ["≤ 2 plain sentences"] if trigger == "proposal-ready" else [], trigger)
            self.assertIn("ids, slugs, decision or rule numbers, file names, code, and spend figures", prompt)
            self.assertIn("the card `--detail`, the digest, the FYI, or the task folder", prompt)
            self.assertIn("not in the reply text", prompt)
        self.assertIn("a few plain sentences", prompts["start"])
        for trigger in ("proposal-ready", "report-landed", "audit"):
            self.assertIn("at most two plain sentences", prompts[trigger])


if __name__ == "__main__":
    unittest.main()
