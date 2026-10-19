"""Lifecycle self-test: a queued task runs directly through one L2 to done."""
import unittest

from tests.support import AltitudeCase
from altitude import config, state as S, tasks as T


class TestLifecycle(AltitudeCase):
    """Single linear scenario. Kept as one test method: unittest does not guarantee an
    ordering across methods that would keep this state-mutating sequence valid."""

    def test_lifecycle_sequence(self):
        t = T.new(self.project, "Add beta stage with alarm rollback", "Add a beta pipeline stage…")
        self.assertEqual(t["state"], "queued")
        self.assertEqual(T.decisions(self.project), [])
        T.brief(self.project, t["slug"], "# Brief\n…")
        T.dispatch(self.project, t["slug"], attempt=1, session_id="sid", agent_id="aid", worktree="/wt", branch="b")
        self.assertEqual(S.load_task(self.project, t["slug"])["attempt"], 1)

        parked = T.block(self.project, t["slug"], "Landing window pending")
        self.assertEqual(T.decisions(self.project), [], "a daemon park has no operator question")
        self.assertEqual(T.block_status(self.project, parked), ("paused", "paused"))

        T.resume(self.project, t["slug"])
        T.report(self.project, t["slug"], {"verdict": "ok", "prs": [140]})
        T.fyi(self.project, t["slug"], "landed")
        T.done(self.project, t["slug"], digest="Done.")

        self.assertEqual(S.task_dir(self.project, t["slug"]).parent.name, "archive")
        self.assertGreaterEqual(len(S.read_events(self.project, t["slug"])), 8)

        t2 = T.new(self.project, "Add beta stage with alarm rollback", "again")
        self.assertTrue(t2["slug"].endswith("-2"))
        self.assertEqual(t2["state"], "queued")
        self.assertNotIn("Recently finished", (config.project_dir(self.project) / "STATE.md").read_text())
        T.reject(self.project, t2["slug"], "tracked in a GitHub issue")
        self.assertEqual(S.task_dir(self.project, t2["slug"]).parent.name, "archive")
        self.assertNotIn(t2["slug"], (config.project_dir(self.project) / "STATE.md").read_text())

        self.assertFalse((config.project_dir(self.project) / "inbox.jsonl").exists(),
                         "FYIs are chat rows; the project inbox file is gone (SPEC.md §5.2 note 3)")


class TestStateSummary(AltitudeCase):
    """STATE.md is the task state L3 reads at the start of every turn: each task sits under its own state, and
    each waiting task under the audience it waits for."""

    def task(self, title, state, **fields):
        task = T.new(self.project, title, "A fixture task.")
        task.update(state=state, **fields)
        S.save_task(self.project, task)
        return task["slug"]

    def placement(self, text):
        """Each listed slug with the section and subsection headings it sits under."""
        found, section, subsection = {}, None, None
        for line in text.splitlines():
            if line.startswith("## "):
                section, subsection = line[3:], None
            elif line.startswith("### "):
                subsection = line[4:]
            elif line.startswith("- **"):
                found.setdefault(line[4:].split("**", 1)[0], []).append((section, subsection))
        return found

    def test_tasks_sit_under_their_state_and_waiting_audience(self):
        empty = S.regen_state_md(self.project)
        self.assertEqual(empty.split("## Needs user input\n\n", 1)[1].split("\n", 1)[0], "- none")
        self.assertEqual(empty.split("## Needs L3 input\n\n", 1)[1].split("\n", 1)[0], "- none")
        self.assertNotIn("###", empty)

        queued = self.task("Queued work", "queued")
        running = self.task("Running work", "running", attempt=2, prs=[7, 9], spend={"turns": 3})
        reported = self.task("Reported work", "reported", attempt=1)
        for_l3 = self.task("Waits for the coordinator", "blocked", waiting_on="l3", blocked_reason="Lease question")
        for_operator = self.task("Waits for the operator", "blocked", blocked_reason="Merge go")
        planned = self.task("Resumes by itself", "blocked", resume_after="2999-01-01T00:00:00+00:00",
                            blocked_reason="CI wait")
        asked = self.task("Asks the operator", "blocked", questions=[
            {"id": "q1", "revision": 2, "status": "open", "audience": "operator", "question": "Ship it?",
             "recommendation": {"text": "Yes"}},
            {"id": "q0", "revision": 1, "status": "answered", "audience": "l3", "question": "Settled already?"}])
        self.task("Finished work", "done")

        text = S.regen_state_md(self.project)
        self.assertEqual(text, (config.project_dir(self.project) / "STATE.md").read_text())
        needs_user, needs_l3 = ("Needs user input", None), ("Needs L3 input", None)
        self.assertEqual(self.placement(text), {
            queued: [("Tasks", "queued (1)")],
            running: [("Tasks", "running (1)")],
            reported: [("Tasks", "reported (1)")],
            for_l3: [needs_l3, ("Tasks", "blocked (4)")],
            for_operator: [needs_user, ("Tasks", "blocked (4)")],
            planned: [("Tasks", "blocked (4)")],
            asked: [needs_user, ("Tasks", "blocked (4)")],
        })
        self.assertIn(f"- **{asked}** (0m; question q1 revision 2): Ship it? Recommended: Yes\n", text)
        self.assertNotIn("Settled already?", text)
        self.assertIn(f"- **{for_l3}** (0m): Lease question\n", text)
        self.assertIn(f"- **{running}** Running work — 0m — attempt 2; PRs 7, 9; turns 3\n", text)
        self.assertIn(f"- **{for_l3}** Waits for the coordinator — 0m — blocked: Lease question\n", text)
        self.assertIn(f"- **{queued}** Queued work — 0m\n", text)
        self.assertEqual([line for line in text.splitlines() if line.startswith("### ")],
                         ["### blocked (4)", "### running (1)", "### reported (1)", "### queued (1)"])
        # A state with no tasks leaves out its own heading only.
        finished = S.load_task(self.project, running)
        finished["state"] = "done"
        S.save_task(self.project, finished)
        later = self.placement(S.regen_state_md(self.project))
        self.assertNotIn(running, later)
        self.assertEqual((later[reported], later[queued]), ([("Tasks", "reported (1)")], [("Tasks", "queued (1)")]))


if __name__ == "__main__":
    unittest.main()
