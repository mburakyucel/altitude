"""L3 preserves one resumable conversation per provider and never replays a partial turn."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-l3-sessions-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, route  # noqa: E402


class TestL3Sessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root(); (_TMP / "repo").mkdir()
        config.save_projects({"k": {"name": "k", "path": str(_TMP / "repo")}})

    def setUp(self):
        for path in (l3.info_path("k"), config.project_dir("k") / "chat.jsonl"):
            path.unlink(missing_ok=True)

    @staticmethod
    def choice(engine):
        return {"engine": engine, "why": f"test chose {engine}", "quota": {}}

    def test_codex_second_turn_resumes_the_same_thread(self):
        calls = []

        def fake_codex(prompt, **kwargs):
            calls.append((prompt, kwargs.get("resume")))
            sid = kwargs.get("resume") or "cx-1"
            return {"text": "codex answer", "session_id": sid, "reported_session_id": sid,
                    "error": None, "usage": {"input_tokens": 1200, "cached_input_tokens": 900},
                    "structured": {"message": "codex answer", "actions": []}, "returncode": 0,
                    "containment_empty": True}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            first = l3.turn("k", "first")
            second = l3.turn("k", "second")

        self.assertEqual((first["session_id"], second["session_id"]), ("cx-1", "cx-1"))
        self.assertIsNone(calls[0][1]); self.assertEqual(calls[1][1], "cx-1")
        self.assertIn("Engine: Codex", calls[0][0])
        self.assertNotIn("# You are the L3", calls[1][0], "persona is not replayed into a resumed transcript")
        self.assertEqual(l3.info("k")["sessions"]["codex"]["usage"]["cached_input_tokens"], 900)

    def test_codex_l3_gets_project_context_read_only(self):
        seen = {}

        def fake_codex(_prompt, **kwargs):
            seen.update(kwargs)
            return {"text": "coordinated", "session_id": "cx-state", "reported_session_id": "cx-state",
                    "error": None, "usage": {"input_tokens": 100},
                    "structured": {"message": "coordinated", "actions": []}, "returncode": 0,
                    "containment_empty": True}

        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn("k", "coordinate this")

        self.assertEqual(Path(seen["cwd"]), config.project_dir("k") / "l3-codex-runtime")
        self.assertEqual(seen["sandbox"], "workspace-write")
        self.assertEqual([Path(path) for path in seen["readable_roots"]],
                         [config.ROOT, config.project_path("k")])

    def test_github_issue_action_returns_a_human_approval_phrase(self):
        source = "Please preserve this sidecar idea as a GitHub issue"
        structured = {"message": "I prepared it.", "actions": [{
            "type": "github_issue", "title": "sidecar idea", "text": source, "labels": [],
        }]}
        result = {"text": "", "session_id": "cx-draft", "reported_session_id": "cx-draft",
                  "error": None, "usage": {"input_tokens": 100}, "structured": structured,
                  "returncode": 0, "containment_empty": True}
        pending = [{"type": "github_issue", "id": "1234567890abcdef12345678", "pending_review": True}]
        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", return_value=result), \
             mock.patch("altitude.l3_actions.apply", return_value=pending) as apply:
            out = l3.turn("k", source)
        self.assertIn("approve GitHub issue publication 1234567890abcdef12345678", out["text"])
        self.assertEqual(apply.call_args.kwargs["github_issue_source"], source)

    def test_alternating_providers_preserves_both_session_ids(self):
        choices = [self.choice("claude"), self.choice("codex"), self.choice("claude")]
        claude_resumes = []

        def fake_claude(prompt, **kwargs):
            claude_resumes.append(kwargs.get("resume"))
            return {"text": "claude answer", "session_id": kwargs.get("resume") or "cl-1", "usage": {},
                    "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None, "error": None,
                    "tools": [], "limited": None}

        def fake_codex(_prompt, **kwargs):
            sid = kwargs.get("resume") or "cx-1"
            return {"text": "codex answer", "session_id": sid, "reported_session_id": sid, "error": None,
                    "usage": {"input_tokens": 100},
                    "structured": {"message": "codex answer", "actions": []}, "returncode": 0,
                    "containment_empty": True}

        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex):
            l3.turn("k", "one"); l3.turn("k", "two"); l3.turn("k", "three")

        self.assertEqual(claude_resumes, [None, "cl-1"])
        sessions = l3.info("k")["sessions"]
        self.assertEqual(sessions["claude"]["session_id"], "cl-1")
        self.assertEqual(sessions["codex"]["session_id"], "cx-1")

    def test_codex_first_does_not_migrate_its_thread_into_claude(self):
        choices = [self.choice("codex"), self.choice("claude")]

        def fake_codex(_prompt, **_kwargs):
            return {"text": "codex answer", "session_id": "cx-1", "reported_session_id": "cx-1",
                    "error": None, "usage": {"input_tokens": 100},
                    "structured": {"message": "codex answer", "actions": []}, "returncode": 0,
                    "containment_empty": True}

        def fake_claude(_prompt, **kwargs):
            self.assertIsNone(kwargs.get("resume"))
            return {"text": "claude answer", "session_id": "cl-1", "usage": {},
                    "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None, "error": None,
                    "tools": [], "limited": None}

        with mock.patch.object(l3, "_select", side_effect=choices), \
             mock.patch.object(engines, "codex_exec", side_effect=fake_codex), \
             mock.patch.object(engines, "claude_print", side_effect=fake_claude):
            l3.turn("k", "one"); l3.turn("k", "two")

        sessions = l3.info("k")["sessions"]
        self.assertEqual(sessions["codex"]["session_id"], "cx-1")
        self.assertEqual(sessions["claude"]["session_id"], "cl-1")

    def test_codex_resume_rejects_a_different_thread(self):
        l3.save_info("k", {"sessions": {"codex": {"session_id": "cx-1"}}, "engine_last": "codex"})
        result = {"text": "wrong thread answer", "session_id": "cx-2", "reported_session_id": "cx-2",
                  "error": None, "usage": {"input_tokens": 100},
                  "structured": {"message": "wrong thread answer", "actions": []}, "returncode": 0,
                  "containment_empty": True}
        with mock.patch.object(l3, "_select", return_value=self.choice("codex")), \
             mock.patch.object(engines, "codex_exec", return_value=result):
            out = l3.turn("k", "continue")
        self.assertFalse(out["completed"])
        self.assertIn("different thread", out["error"])
        self.assertEqual(l3.info("k")["sessions"]["codex"]["session_id"], "cx-1")

    def test_partial_limited_claude_turn_is_not_replayed_on_codex(self):
        result = {"text": "I already changed state", "session_id": "cl-1", "usage": {},
                  "context_tokens": 100, "cost": 0.0, "turns": 1, "structured": None,
                  "error": "usage limit", "tools": ["Bash"], "limited": "2099-01-01T00:00:00+00:00"}
        with mock.patch.object(l3, "_select", return_value=self.choice("claude")), \
             mock.patch.object(engines, "claude_print", return_value=result), \
             mock.patch.object(engines, "codex_exec") as codex:
            out = l3.turn("k", "do one thing")
        codex.assert_not_called()
        self.assertEqual(out["engine"], "claude")


if __name__ == "__main__":
    unittest.main()
