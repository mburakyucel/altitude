"""Decision 50: feedback on a proposal card archives the old proposal, lands in the request, and the next proposal must answer it."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="altitude-revise-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, state as S, tasks as T, dispatch  # noqa: E402

CARD = dict(question="Build it around the existing hook?", options=["Approve", "Revise", "Reject"], context="The hook already sees every launch. The proposal adds a second daemon.")


class TestRevise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        (_TMP / "repo").mkdir()
        config.save_projects({"altitude": {"name": "altitude", "path": str(_TMP / "repo"), "stacks": ["python"]}})

    def _proposed(self, slug, md):
        d = S.task_dir("altitude", slug)
        S.write_json(d / "proposal.json", {"summary": md}); S.write_json(d / "critique.json", {"verdict": "ok", "issues": []})
        T.propose("altitude", slug, md, **CARD)
        return d

    def test_feedback_archives_and_lands_in_the_request(self):
        T.new("altitude", "rev-m", "M", "the request")
        d = self._proposed("rev-m", "# proposal one")
        with self.assertRaises(T.TransitionError):
            T.revise("altitude", "rev-m", "   ")
        t = T.revise("altitude", "rev-m", "Use the existing hook instead of a new daemon")
        self.assertEqual(t["state"], "requested"); self.assertIsNone(t["proposal_started"]); self.assertEqual(t["feedback_rounds"], 1)
        self.assertTrue((d / "proposal-v1.md").exists()); self.assertTrue((d / "critique-v1.json").exists())
        self.assertFalse((d / "proposal.md").exists(), "a fresh proposal run must not reuse the old file")
        req = (d / "request.md").read_text()
        self.assertIn("## Burak's feedback on proposal v1", req); self.assertIn("existing hook", req)
        # second round through the card's own Revise option: the note is the feedback and is required
        self._proposed("rev-m", "# proposal two")
        with self.assertRaises(T.TransitionError):
            T.approve("altitude", "rev-m", 1, actor="burak", note="")
        t = T.approve("altitude", "rev-m", 1, actor="burak", note="shorter, and keep the tests")
        self.assertEqual(t["state"], "requested"); self.assertEqual(t["feedback_rounds"], 2)
        self.assertTrue((d / "proposal-v2.md").exists())
        self.assertIn("## Burak's feedback on proposal v2", (d / "request.md").read_text())
        with self.assertRaises(T.TransitionError):  # only a proposed task can be revised
            T.revise("altitude", "rev-m", "again")

    def test_a_note_with_approve_travels_in_the_brief(self):
        T.new("altitude", "rev-note", "M", "the request")
        self._proposed("rev-note", "# proposal")
        T.approve("altitude", "rev-note", 0, actor="burak", note="keep the tests green before merging")
        b = dispatch.build_brief("altitude", "rev-note")
        self.assertIn("Burak's note with that answer (binding): keep the tests green before merging", b)


if __name__ == "__main__":
    unittest.main()
