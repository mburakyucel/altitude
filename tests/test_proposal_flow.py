"""Incident I-008: the proposal flow's auto-revision must never override a park by Burak.

The branches of server.run_proposal_flow, with the proposal/critic/L3 agents stubbed out:
  * Burak parks during the L3 turn   -> the park stands (no revision, no -vN history);
  * the L3 parks during the turn      -> the revision is queued as before;
  * the L3 parked in an *earlier* turn while we queued on l3.lock -> the park stands;
  * the turn cannot be dated at all  -> the park stands (the guard fails closed, decision 36);
  * the task left `requested` before the turn -> the turn is skipped and proposal_started cleared;
  * it leaves `requested` while waiting on l3.lock -> the engine turn is skipped without a trace;
  * it becomes FYI-only `proposed` while waiting on l3.lock -> skipped, then auto-approved;
  * it leaves `requested` after the proposal -> the critic and L3 turn are both skipped;
  * it becomes always-list `proposed` after the proposal -> the critic is skipped and merge held;
  * it left `requested` by becoming `proposed` -> skipped, but still auto-approved as FYI-only.
"""
import importlib
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Must happen before altitude.config is imported: config.py reads ALTITUDE_HOME into ROOT at import time.
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="alt-proposal-flow-")

from altitude import config  # noqa: E402

# Under `unittest discover` another test module may have imported altitude.config against a
# different ALTITUDE_HOME before this one ran; rebuild ROOT and its derived paths against ours.
importlib.reload(config)

from altitude import engines, l3, propose, server, state as S, tasks as T  # noqa: E402

PROJECT = "propflow"
PROPOSAL = {"summary": "a summary", "decision_needed": True, "always_list_hits": [], "estimate": {"turns": 40}}


class TestProposalFlowPark(unittest.TestCase):

    def setUp(self):
        config.ensure_root()
        P = config.load_projects()
        P[PROJECT] = {"name": PROJECT, "path": config.ROOT.as_posix(), "stacks": ["python"]}
        config.save_projects(P)
        self._orig = (propose.run_proposal, propose.run_critic, l3.turn)
        # any real agent call is a bug in the test setup, not a run to be made
        propose.run_proposal = lambda *a, **k: self.fail("the proposal agent must not run")
        propose.run_critic = lambda *a, **k: self.fail("the critic must not run")
        l3.turn = lambda *a, **k: self.fail("l3.turn must not run")

    def tearDown(self):
        propose.run_proposal, propose.run_critic, l3.turn = self._orig

    def _task(self, title: str, cls: str = "L", on_disk: bool = True) -> str:
        """A task with a proposal and a `revise` critique already on disk, so the flow reuses both."""
        t = T.new(PROJECT, title, cls, "Do the thing.", actor="burak")
        if on_disk:
            d = S.task_dir(PROJECT, t["slug"])
            S.atomic_write(d / "proposal.md", "# Proposal\nBody.\n")
            S.write_json(d / "proposal.json", dict(PROPOSAL))
            S.write_json(d / "critique.json", {"verdict": "revise", "issues": ["one issue"]})
        return t["slug"]

    def _park_in_turn(self, slug: str, actor: str, calls: list):
        """Stub for l3.turn: the L3 (or Burak, racing it) parks the task while the turn runs."""
        def turn(project, prompt, **kw):
            calls.append(prompt)
            turn_started_at = S.now()
            T.park(project, slug, f"parked by {actor}", actor=actor)
            return {"ok": True, "_turn_started_at": turn_started_at}
        return turn

    def _events(self, slug: str, kind: str) -> list[dict]:
        return [e for e in S.read_events(PROJECT, slug) if e.get("kind") == kind]

    def _state_events(self, slug: str) -> list[dict]:
        return [e for e in S.read_events(PROJECT, slug) if e.get("kind") == "state"]

    def test_park_by_burak_is_not_overridden(self):
        slug = self._task("burak parks during the turn")
        calls: list = []
        l3.turn = self._park_in_turn(slug, "burak", calls)

        server.run_proposal_flow(PROJECT, slug)

        self.assertEqual(len(calls), 1, "the L3 turn should still have run")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked", "Burak's park must stand")
        self.assertFalse(t.get("revisions"), "no revision may be counted against a park by Burak")
        d = S.task_dir(PROJECT, slug)
        for name in ("proposal.md", "proposal.json", "critique.json"):
            self.assertTrue((d / name).exists(), f"{name} must not be renamed away")
        self.assertEqual([p.name for p in sorted(d.glob("*-v*"))], [], "no -vN history for a park by Burak")
        last = self._state_events(slug)[-1]
        self.assertEqual((last["to"], last["by"]), ("parked", "burak"), "altd must not have unparked it")

    def test_park_by_l3_queues_one_revision(self):
        slug = self._task("l3 parks with a revision brief")
        calls: list = []
        l3.turn = self._park_in_turn(slug, "l3", calls)

        server.run_proposal_flow(PROJECT, slug)

        self.assertEqual(len(calls), 1)
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "requested", "a revision puts the task back in requested")
        self.assertEqual(int(t.get("revisions", 0)), 1)
        self.assertIsNone(t.get("proposal_started"), "the flow must be re-runnable for the revision")
        d = S.task_dir(PROJECT, slug)
        self.assertEqual([p.name for p in sorted(d.glob("*-v*"))],
                         ["critique-v1.json", "proposal-v1.json", "proposal-v1.md"])
        for name in ("proposal.md", "proposal.json", "critique.json"):
            self.assertFalse((d / name).exists(), f"{name} should have been rotated to -v1")
        last = self._state_events(slug)[-1]
        self.assertEqual((last["frm"], last["to"], last["by"]), ("parked", "requested", "altd"))

    def test_turn_is_skipped_when_the_task_left_requested(self):
        slug = self._task("burak parks before the turn")
        T.park(PROJECT, slug, "not now", actor="burak")  # while the proposal/critic were running

        server.run_proposal_flow(PROJECT, slug)  # l3.turn stub fails the test if it is called

        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked")
        self.assertIsNone(t.get("proposal_started"),
                          "cleared so tick() re-runs the flow if Burak unparks the task later")
        self.assertFalse(t.get("revisions"))
        d = S.task_dir(PROJECT, slug)
        self.assertTrue((d / "proposal.json").exists(), "the proposal stays on disk to be reused")
        self.assertEqual([p.name for p in sorted(d.glob("*-v*"))], [])

    def test_park_while_waiting_on_l3_lock_skips_the_turn(self):
        slug = self._task("burak parks while proposal turn waits on l3 lock")
        sent: list = []
        logs: list[str] = []
        errors: list[BaseException] = []
        entered = threading.Event()
        chat_before = l3.chat_history(PROJECT)
        project_l3_lock = l3.lock(PROJECT)
        real_turn = self._orig[2]
        real_engine = engines.claude_print
        real_codex = engines.codex_exec
        real_log = server.log

        def waiting_turn(project, prompt, **kwargs):
            entered.set()
            return real_turn(project, prompt, **kwargs)

        def fake_engine(*args, **kwargs):
            sent.append(args[0])
            return {"text": "", "session_id": "test-session", "usage": {}, "context_tokens": 0,
                    "cost": 0.0, "turns": 1, "structured": None, "error": None, "tools": []}

        def run_flow():
            try:
                server.run_proposal_flow(PROJECT, slug)
            except BaseException as exc:  # preserve worker failures for the test thread
                errors.append(exc)

        l3.turn = waiting_turn
        engines.claude_print = fake_engine
        engines.codex_exec = fake_engine
        server.log = logs.append
        project_l3_lock.acquire()
        worker = threading.Thread(target=run_flow)
        try:
            worker.start()
            reached_lock = entered.wait(5)
            if reached_lock:
                T.park(PROJECT, slug, "not now", actor="burak")
        finally:
            project_l3_lock.release()
            worker.join(5)
            engines.claude_print = real_engine
            engines.codex_exec = real_codex
            server.log = real_log

        self.assertTrue(reached_lock, "the proposal-ready turn should reach the held L3 lock")
        self.assertFalse(worker.is_alive(), "the proposal flow should finish after the lock is released")
        self.assertEqual(errors, [])
        self.assertEqual(sent, [], "a task parked while queued must not be sent to the engine")
        self.assertEqual(l3.chat_history(PROJECT), chat_before, "a skipped turn must not reach chat.jsonl")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked")
        self.assertIsNone(t.get("proposal_started"))
        self.assertFalse(t.get("revisions"), "no revision may be queued for the skipped turn")
        self.assertEqual(len([line for line in logs if "skipping the proposal-ready L3 turn" in line]), 1)

    def test_fyi_only_proposal_while_waiting_on_l3_lock_is_auto_approved(self):
        slug = self._task("l3 proposes while proposal turn waits on l3 lock", cls="M")
        sent: list = []
        errors: list[BaseException] = []
        entered = threading.Event()
        project_l3_lock = l3.lock(PROJECT)
        real_turn = self._orig[2]
        real_engine = engines.claude_print
        real_codex = engines.codex_exec

        def waiting_turn(project, prompt, **kwargs):
            entered.set()
            return real_turn(project, prompt, **kwargs)

        def fake_engine(*args, **kwargs):
            sent.append(args[0])
            return {"text": "", "session_id": "test-session", "usage": {}, "context_tokens": 0,
                    "cost": 0.0, "turns": 1, "structured": None, "error": None, "tools": []}

        def run_flow():
            try:
                server.run_proposal_flow(PROJECT, slug)
            except BaseException as exc:
                errors.append(exc)

        l3.turn = waiting_turn
        engines.claude_print = fake_engine
        engines.codex_exec = fake_engine
        project_l3_lock.acquire()
        worker = threading.Thread(target=run_flow)
        try:
            worker.start()
            reached_lock = entered.wait(5)
            if reached_lock:
                T.propose(PROJECT, slug, "# Proposal\nBody.\n", dict(PROPOSAL, decision_needed=False))
        finally:
            project_l3_lock.release()
            worker.join(5)
            engines.claude_print = real_engine
            engines.codex_exec = real_codex

        self.assertTrue(reached_lock, "the proposal-ready turn should reach the held L3 lock")
        self.assertFalse(worker.is_alive(), "the proposal flow should finish after the lock is released")
        self.assertEqual(errors, [])
        self.assertEqual(sent, [], "a task proposed while queued must not be sent to the engine")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "approved", "an FYI-only proposal must not be stranded in `proposed`")
        self.assertIsNone(t.get("proposal_started"))
        last = self._state_events(slug)[-1]
        self.assertEqual((last["frm"], last["to"], last["by"]), ("proposed", "approved", "burak"))
        self.assertIn("auto", last.get("note", ""), "recorded as an automatic approval (decision 13)")
        self.assertTrue(self._events(slug, "fyi"), "Burak is told it is dispatching")

    def test_park_between_proposal_and_critic_skips_the_critic(self):
        slug = self._task("burak parks between proposal and critic", on_disk=False)
        critic_calls: list[str] = []
        logs: list[str] = []

        def run_proposal(project, task_slug):
            d = S.task_dir(project, task_slug)
            S.atomic_write(d / "proposal.md", "# Proposal\nBody.\n")
            S.write_json(d / "proposal.json", dict(PROPOSAL))
            T.park(project, task_slug, "not now", actor="burak")
            return dict(PROPOSAL)

        def run_critic(project, task_slug):
            critic_calls.append(task_slug)
            return {"verdict": "revise", "issues": ["one issue"]}

        propose.run_proposal = run_proposal
        propose.run_critic = run_critic
        real_log = server.log
        server.log = logs.append

        try:
            server.run_proposal_flow(PROJECT, slug)  # l3.turn stub fails the test if it is called
        finally:
            server.log = real_log

        self.assertEqual(critic_calls, [], "a park after the proposal must prevent the critic run")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked")
        self.assertIsNone(t.get("proposal_started"))
        self.assertFalse(t.get("revisions"))
        self.assertEqual(len([line for line in logs if "skipping the proposal-ready L3 turn" in line]), 1)

    def test_always_list_proposal_between_proposal_and_critic_holds_merge(self):
        slug = self._task("l3 proposes always-list task before critic", cls="M", on_disk=False)
        critic_calls: list[str] = []
        proposal = dict(PROPOSAL, always_list_hits=["production auth"])

        def run_proposal(project, task_slug):
            T.propose(project, task_slug, "# Proposal\nBody.\n", proposal)
            return proposal

        def run_critic(project, task_slug):
            critic_calls.append(task_slug)
            return {"verdict": "approve", "issues": []}

        propose.run_proposal = run_proposal
        propose.run_critic = run_critic

        server.run_proposal_flow(PROJECT, slug)  # l3.turn stub fails the test if it is called

        self.assertEqual(critic_calls, [], "a proposal after the proposal run must prevent the critic run")
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "requested")
        self.assertIsNone(t.get("proposal_started"))
        self.assertEqual(t.get("hold_merge"), "always-list: production auth")
        self.assertEqual(t.get("proposal_reconcile_failures"), 1)
        self.assertIn("always-list hits", t.get("proposal_reconcile_reason", ""))

    def test_decision_required_without_a_card_is_requeued_and_faulted_if_repeated(self):
        slug = self._task("decision-required proposal omitted its card", cls="M")
        proposal = dict(PROPOSAL, decision_needed=True, always_list_hits=[])
        T.propose(PROJECT, slug, "# Proposal\nBody.\n", proposal)

        server._finish_proposal(PROJECT, slug, proposal, S.load_task(PROJECT, slug))
        first = S.load_task(PROJECT, slug)
        self.assertEqual(first["state"], "requested")
        self.assertEqual(first.get("proposal_reconcile_failures"), 1)
        self.assertNotIn("approved", [event.get("to") for event in self._state_events(slug)])

        T.propose(PROJECT, slug, "# Proposal\nBody.\n", proposal)
        with mock.patch.object(server.improve, "system_fault") as fault:
            server._finish_proposal(PROJECT, slug, proposal, S.load_task(PROJECT, slug))
        second = S.load_task(PROJECT, slug)
        self.assertEqual(second["state"], "requested")
        self.assertEqual(second.get("proposal_reconcile_failures"), 2)
        self.assertTrue(second.get("proposal_reconcile_faulted"))
        fault.assert_called_once()

    def test_park_by_l3_in_an_earlier_turn_is_not_overridden(self):
        """Finding 1: we stamp the queue time, then block on l3.lock behind a chat turn in which the
        L3 parks the task for Burak (`alt task park` under ALTITUDE_ACTOR=l3, so `by=l3`). That park
        predates our turn and must stand — the guard reads the result timestamp, not queue time."""
        slug = self._task("l3 parked it in the chat turn we queued behind")
        calls: list = []

        def turn(project, prompt, **kw):
            calls.append(prompt)
            T.park(project, slug, "Burak asked me to park it", actor="l3")   # the earlier chat turn
            turn_started_at = (datetime.now(timezone.utc).replace(microsecond=0)
                               + timedelta(seconds=60)).isoformat()           # our turn only starts after it
            return {"ok": True, "_turn_started_at": turn_started_at}
        l3.turn = turn

        server.run_proposal_flow(PROJECT, slug)

        self.assertEqual(len(calls), 1)
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked", "a park made before our turn began must stand")
        self.assertFalse(t.get("revisions"), "no revision for a park that predates the turn")
        d = S.task_dir(PROJECT, slug)
        self.assertEqual([q.name for q in sorted(d.glob("*-v*"))], [])
        last = self._state_events(slug)[-1]
        self.assertEqual((last["to"], last["by"]), ("parked", "l3"), "altd must not have unparked it")

    def test_an_undatable_turn_leaves_the_park_standing(self):
        """The guard fails closed (decision 36): if l3.turn returns no start timestamp, we cannot
        tell when the turn began, so even an L3 park made during it must stand —
        the permissive reading is exactly the I-008 override. Unreachable in a healthy system."""
        slug = self._task("the turn start is missing")
        calls: list = []

        def turn(project, prompt, **kw):
            calls.append(prompt)
            T.park(project, slug, "revise: the estimate is wrong", actor="l3")  # in-turn, but undatable
            return {"ok": True}
        l3.turn = turn

        server.run_proposal_flow(PROJECT, slug)

        self.assertEqual(len(calls), 1)
        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "parked", "an undatable turn must not override the park")
        self.assertFalse(t.get("revisions"), "no revision may be queued off a turn we cannot date")
        d = S.task_dir(PROJECT, slug)
        self.assertEqual([q.name for q in sorted(d.glob("*-v*"))], [], "no -vN history either")
        last = self._state_events(slug)[-1]
        self.assertEqual((last["to"], last["by"]), ("parked", "l3"), "altd must not have unparked it")

    def test_fyi_only_proposal_made_during_the_flow_is_still_auto_approved(self):
        """Finding 2: the L3 proposes the task (no question) in a chat turn while our proposal agent
        runs. We skip the now-pointless L3 turn, but an FYI-only proposal raises no decision card, so
        the auto-approve branch at the end of the flow is the only thing that can ever dispatch it."""
        slug = self._task("l3 proposes it while the proposal agent runs", cls="M", on_disk=False)

        def run_proposal(project, task_slug):
            T.propose(project, task_slug, "# Proposal\nBody.\n", dict(PROPOSAL, decision_needed=False))  # no question: FYI-only
            return dict(PROPOSAL, decision_needed=False)
        propose.run_proposal = run_proposal

        server.run_proposal_flow(PROJECT, slug)   # the l3.turn stub fails the test if it is called

        t = S.load_task(PROJECT, slug)
        self.assertEqual(t["state"], "approved", "an FYI-only proposal must not be stranded in `proposed`")
        self.assertIsNone(t.get("proposal_started"))
        last = self._state_events(slug)[-1]
        self.assertEqual((last["frm"], last["to"], last["by"]), ("proposed", "approved", "burak"))
        self.assertIn("auto", last.get("note", ""), "recorded as an automatic approval (decision 13)")
        self.assertTrue(self._events(slug, "fyi"), "Burak is told it is dispatching")


if __name__ == "__main__":
    unittest.main()
