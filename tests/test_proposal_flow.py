"""Incident I-008: the proposal flow's auto-revision must never override a park by Burak.

The branches of server.run_proposal_flow, with the proposal/critic/L3 agents stubbed out:
  * Burak parks during the L3 turn   -> the park stands (no revision, no -vN history);
  * the L3 parks during the turn      -> the revision is queued as before;
  * the L3 parked in an *earlier* turn while we queued on l3.lock -> the park stands;
  * the task left `requested` before the turn -> the turn is skipped and proposal_started cleared;
  * it left `requested` by becoming `proposed` -> skipped, but still auto-approved as FYI-only.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Must happen before altitude.config is imported: config.py reads ALTITUDE_HOME into ROOT at import time.
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="alt-proposal-flow-")

from altitude import config  # noqa: E402

# Under `unittest discover` another test module may have imported altitude.config against a
# different ALTITUDE_HOME before this one ran; rebuild ROOT and its derived paths against ours.
importlib.reload(config)

from altitude import l3, propose, server, state as S, tasks as T  # noqa: E402

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

    def _chat_entry(self, project: str, prompt: str, offset: int) -> None:
        """Write the `user` chat entry l3.turn would have written, `offset` seconds from now.

        l3.turn logs the prompt *inside* the per-project L3 lock, so this entry — not the moment
        the server queued the turn — is when the turn really began (incident I-008, finding 1).
        """
        at = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=offset)).isoformat()
        path = config.project_dir(project) / "chat.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"at": at, "role": "user", "text": prompt, "trigger": "proposal-ready"}) + "\n")

    def _park_in_turn(self, slug: str, actor: str, calls: list):
        """Stub for l3.turn: the L3 (or Burak, racing it) parks the task while the turn runs."""
        def turn(project, prompt, **kw):
            calls.append(prompt)
            T.park(project, slug, f"parked by {actor}", actor=actor)
            return {"ok": True}
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

    def test_park_by_l3_in_an_earlier_turn_is_not_overridden(self):
        """Finding 1: we stamp the queue time, then block on l3.lock behind a chat turn in which the
        L3 parks the task for Burak (`alt task park` under ALTITUDE_ACTOR=l3, so `by=l3`). That park
        predates our turn and must stand — the guard reads the chat entry, not the queue time."""
        slug = self._task("l3 parked it in the chat turn we queued behind")
        calls: list = []

        def turn(project, prompt, **kw):
            calls.append(prompt)
            T.park(project, slug, "Burak asked me to park it", actor="l3")   # the earlier chat turn
            self._chat_entry(project, prompt, offset=60)                     # our turn only starts after it
            return {"ok": True}
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

    def test_fyi_only_proposal_made_during_the_flow_is_still_auto_approved(self):
        """Finding 2: the L3 proposes the task (no question) in a chat turn while our proposal agent
        runs. We skip the now-pointless L3 turn, but an FYI-only proposal raises no decision card, so
        the auto-approve branch at the end of the flow is the only thing that can ever dispatch it."""
        slug = self._task("l3 proposes it while the proposal agent runs", cls="M", on_disk=False)

        def run_proposal(project, task_slug):
            T.propose(project, task_slug, "# Proposal\nBody.\n", dict(PROPOSAL))  # no question: FYI-only
            return dict(PROPOSAL)
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
