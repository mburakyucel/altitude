"""A held PR closed without merging stops asking for merge review (#575).

The incident this prevents: after the operator redirected delivery and PR #571 was closed unmerged, its task kept
showing "Review PR #571 before merge" because the held-review card read only the recorded delivery and merge hold.
"""
from __future__ import annotations

import json
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, server, state as S, tasks as T


class TestClosedPrReview(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.gh = self.fake_gh()
        task = T.new(self.project, "Runtime port", "Port it.", actor="burak", hold_merge="Operator review before merge")
        self.slug, self.name = task["slug"], config.operator_label()
        task.update(state="running", attempt=1, prs=[41, 42],
                    delivery={"number": 42, "head": "a" * 40, "base": "b" * 40, "branch": "worktree-x", "at": S.now()})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "delivery", **task["delivery"])
        self.pr(42, "OPEN")

    def pr(self, number: int, state: str) -> None:
        prs = json.loads((self.gh / "prs.json").read_text()) if (self.gh / "prs.json").exists() else {}
        S.write_json(self.gh / "prs.json", {**prs, str(number): {"number": number, "state": state}})

    def owner_block(self, *args: str):
        out = self.alt("--project", self.project, "task", "block", self.slug, *args,
                       env={"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": self.slug, "ALTITUDE_ATTEMPT": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def kinds(self) -> list[str]:
        return [row["kind"] for row in T.decisions(self.project)]

    def label(self) -> str | None:
        return T.wait_label(self.project, S.load_task(self.project, self.slug))

    def closures(self) -> list[dict]:
        return [e for e in S.read_events(self.project, self.slug) if e["kind"] == "pr-closed"]

    def test_owner_stop_after_closure_retires_the_card_and_keeps_history_hold_and_questions(self):
        self.owner_block("--reason", "Waiting on the Mac agent's push.")
        self.assertEqual(self.kinds(), ["review"], "an open held PR still asks for review")
        self.assertEqual(self.label(), f"{self.name}'s turn · review PR #42")

        T.resume(self.project, self.slug)
        self.pr(42, "CLOSED")
        self.owner_block("--reason", "Which region should the installer default to?", "--for-operator")
        self.assertEqual(self.kinds(), ["asks"], "the independent question stays; the closed PR asks nothing")
        self.assertEqual(self.label(), f"{self.name}'s turn · 1 question")
        with mock.patch.object(server.monitor, "sessions", return_value=[]):
            self.assertEqual([row["kind"] for row in server.project_view(self.project)["decisions"]], ["asks"])
        task = S.load_task(self.project, self.slug)
        self.assertEqual((task["hold_merge"], task["prs"], task["delivery"]["number"]),
                         ("Operator review before merge", [41, 42], 42), "history and the hold stay recorded")
        self.assertEqual([(e["number"], e["by"]) for e in self.closures()], [(42, "l2")])
        self.assertIsNone(T.approved_pr(self.project, task))

        question = task["questions"][-1]
        T.resolve_question(self.project, self.slug, question["id"], question["revision"], None,
                           disposition="withdrawn", reason="Settled in chat.", expected_attempt=1)
        T.resume(self.project, self.slug)
        self.owner_block("--reason", "Waiting on the Mac agent's push.")
        self.assertEqual((self.kinds(), self.label()), ([], "waiting on L3"))
        self.assertEqual(len(self.closures()), 1, "one observation per closure")

        # A replacement delivery under the same hold asks for its own review again.
        T.resume(self.project, self.slug)
        task = S.load_task(self.project, self.slug)
        task.update(prs=[41, 42, 43], delivery={**task["delivery"], "number": 43, "at": S.now()})
        S.save_task(self.project, task)
        S.append_event(self.project, self.slug, "delivery", **task["delivery"])
        self.pr(43, "OPEN")
        self.owner_block("--reason", "Waiting on the Mac agent's push.")
        self.assertEqual([row.get("pr") for row in T.decisions(self.project)], [43])

    def test_a_reopened_or_relanded_pr_asks_again(self):
        for reopen in ("observed open", "landed again"):
            with self.subTest(reopen=reopen):
                self.pr(42, "CLOSED")
                self.owner_block("--reason", "Waiting on the Mac agent's push.")
                self.assertEqual(self.kinds(), [])
                T.resume(self.project, self.slug)
                self.pr(42, "OPEN")
                if reopen == "landed again":
                    S.append_event(self.project, self.slug, "delivery", **S.load_task(self.project, self.slug)["delivery"])
                    self.assertEqual(self.kinds(), [], "a running owner is not waiting for review")
                self.owner_block("--reason", "Waiting on the Mac agent's push.")
                self.assertEqual(self.kinds(), ["review"])
                T.resume(self.project, self.slug)
        self.assertEqual([e["kind"] for e in S.read_events(self.project, self.slug) if e["kind"].startswith("pr-")],
                         ["pr-closed", "pr-reopened", "pr-closed"])

    def test_an_unreadable_pr_state_keeps_the_recorded_review_and_says_so(self):
        (self.gh / "view_error.txt").write_text("gh: authentication required")
        out = self.owner_block("--reason", "Waiting on the Mac agent's push.")
        self.assertIn("PR #42 state unavailable", out.stderr)
        self.assertEqual(S.load_task(self.project, self.slug)["state"], "blocked", "the block itself still lands")
        self.assertEqual((self.kinds(), self.closures()), (["review"], []), "no closure is invented")
