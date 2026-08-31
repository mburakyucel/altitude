"""Decision 46: the card is executive — plain dilemma, short options, reasoning and ids behind it, ledger one tap away."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-cards-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, refs  # noqa: E402

REPO = Path(_TMP) / "repo"
JARGON = ("Decision-31 reserve line has no headless source (I-007). v3: harvest rate_limit_event from every claude -p stream, "
          "600 s freshness gate at each dispatch decision, cooldown-gated haiku probe as fallback. Conditions: (a) drop web/app.js …")


class TestCardContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (REPO / "docs" / "incidents").mkdir(parents=True, exist_ok=True)
        config.save_projects({"altitude": {"name": "altitude", "path": str(REPO), "stacks": ["python"]}})
        (REPO / "docs" / "DECISIONS.md").write_text("# Decisions\n\n| # | Status | Decision | Why | Cost |\n|---|---|---|---|---|\n"
                                                    "| 31 | settled | **Reserve line.** Hold at 80%. | quota | some |\n| 32 | proposed | other | w | c |\n")
        (REPO / "docs" / "RULES.md").write_text("# Rules\n\n## R-002 — reserve a launch\nbody two\n\n## R-003 — One plain command per Bash call\nbody three\nmore\n")
        (REPO / "docs" / "incidents" / "I-007.md").write_text("# I-007 — quota reserve line has no data source\n\nwhat happened\n")

    def _task(self, slug):
        return T.new("altitude", slug, "M", "request text", actor="l3")["slug"]

    def test_plain_card_is_accepted_with_detail(self):
        slug = self._task("plain-card")
        t = T.propose("altitude", slug, "# proposal", None, question="Altitude cannot see how much of the window is left. When no fresh reading can be had, stop or carry on?",
                      options=["Stop dispatching until a reading returns (recommended)", "Carry on and raise an alarm", "Park"], detail=JARGON,
                      context="Altitude only learns the window usage from interactive sessions, so it flies blind. The proposal adds a headless reader.")
        self.assertEqual(t["state"], "proposed")
        card = next(d for d in T.decisions("altitude") if d["slug"] == slug)
        self.assertEqual(card["detail"], JARGON)
        self.assertTrue(card["context"].startswith("Altitude only learns"))
        self.assertLessEqual(len(card["question"]), T.CARD_QUESTION_MAX)

    def test_jargon_or_length_is_rejected_with_the_fix(self):
        slug = self._task("jargon-card")
        with self.assertRaises(T.TransitionError) as cm:
            T.propose("altitude", slug, "# proposal", None, question=JARGON, options=["Fail closed (recommended): hold with reason quota unknown, retry after the 600 s probe cooldown (10 min max). Conditions: (a)…"])
        msg = str(cm.exception)
        self.assertIn("decision 46", msg); self.assertIn("--detail", msg); self.assertIn("option", msg)
        self.assertEqual(S.load_task("altitude", slug)["state"], "requested", "a rejected card changes nothing")
        with self.assertRaises(T.TransitionError):
            T.propose("altitude", slug, "# p", None, question="Should we change server.py now?", options=["Yes", "No"])
        with self.assertRaises(T.TransitionError):
            T.propose("altitude", slug, "# p", None, question="Stop or carry on?", options=["Stop", "Go"], context="x " * 200)

    def test_blocked_card_is_one_sentence_with_the_reason_behind_it(self):
        slug = self._task("blocked-card")
        with S.project_lock("altitude"):
            t = S.load_task("altitude", slug); t["state"] = "blocked"; t["blocked_reason"] = "L2 is idle without a report — probably waiting for a permission or a question. Attach: `claude attach abc`; or answer via message L2."; S.save_task("altitude", t)
        card = next(d for d in T.decisions("altitude") if d["slug"] == slug)
        self.assertEqual(card["question"], "Stopped mid-task: L2 is idle without a report")
        self.assertIn("claude attach abc", card["detail"])
        self.assertEqual(card["options"], ["Resume", "Park", "Reject"])


class TestRefs(unittest.TestCase):
    def test_decision_rule_and_incident_resolve(self):
        d = refs.resolve("altitude", "decision 31")
        self.assertEqual(d["kind"], "decision"); self.assertIn("Reserve line", d["text"]); self.assertIn("Why: quota", d["text"])
        self.assertEqual(refs.resolve("altitude", "31")["title"], "Decision 31")
        r = refs.resolve("altitude", "r-003")
        self.assertEqual(r["kind"], "rule"); self.assertTrue(r["title"].startswith("R-003")); self.assertIn("more", r["text"]); self.assertNotIn("body two", r["text"])
        i = refs.resolve("altitude", "I-007")
        self.assertEqual(i["kind"], "incident"); self.assertIn("what happened", i["text"])
        for bad in ("decision 99", "R-999", "I-999", "nonsense"):
            with self.assertRaises(KeyError):
                refs.resolve("altitude", bad)


if __name__ == "__main__":
    unittest.main()
