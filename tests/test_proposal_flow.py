"""Incident I-008: the proposal flow's auto-revision must never override a park by Burak.

Three branches of server.run_proposal_flow, with the proposal/critic/L3 agents stubbed out:
  * Burak parks during the L3 turn  -> the park stands (no revision, no -vN history);
  * the L3 parks during the turn    -> the revision is queued as before;
  * the task left `requested` before the turn -> the turn is skipped and proposal_started cleared.
"""
import importlib
import os
import sys
import tempfile
import unittest
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

    def _task(self, title: str) -> str:
        """An L task with a proposal and a `revise` critique already on disk, so the flow reuses both."""
        t = T.new(PROJECT, title, "L", "Do the thing.", actor="burak")
        d = S.task_dir(PROJECT, t["slug"])
        S.atomic_write(d / "proposal.md", "# Proposal\nBody.\n")
        S.write_json(d / "proposal.json", {"summary": "a summary", "decision_needed": True,
                                           "always_list_hits": [], "estimate": {"turns": 40}})
        S.write_json(d / "critique.json", {"verdict": "revise", "issues": ["one issue"]})
        return t["slug"]

    def _park_in_turn(self, slug: str, actor: str, calls: list):
        """Stub for l3.turn: the L3 (or Burak, racing it) parks the task while the turn runs."""
        def turn(project, prompt, **kw):
            calls.append(prompt)
            T.park(project, slug, f"parked by {actor}", actor=actor)
            return {"ok": True}
        return turn

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


if __name__ == "__main__":
    unittest.main()
