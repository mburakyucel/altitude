"""I-055: Codex L1s know the host patch constraint and surface sandbox setup failures."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from altitude import engines, improve, l1, route, state as S


class TestL1PatchNote(unittest.TestCase):
    def test_persisted_prompt_has_patch_note_only_for_codex(self):
        cases = (
            ("implementer", "l1", "codex"),
            ("reviewer", "reviewer", "codex"),
            ("implementer", "l1", "claude"),
            ("reviewer", "reviewer", "claude"),
        )
        for role, route_role, engine in cases:
            with self.subTest(role=role, engine=engine), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                run_dir = root / "runs"
                run_dir.mkdir()
                brief = root / "brief.md"
                brief.write_text("change one thing\n")
                picked = []

                def pick_engine(selected_role, **_kwargs):
                    picked.append(selected_role)
                    return {"engine": engine, "why": "test"}

                with ExitStack() as stack:
                    stack.enter_context(mock.patch.object(route, "pick_engine", pick_engine))
                    stack.enter_context(mock.patch.object(
                        S, "load_task", return_value={"envelope": {"l1_in_flight": 1}, "worktree": str(root)}
                    ))
                    stack.enter_context(mock.patch.object(S, "append_event", return_value=None))
                    stack.enter_context(mock.patch.object(l1, "list_runs", return_value=[]))
                    stack.enter_context(mock.patch.object(l1, "runs_dir", return_value=run_dir))
                    stack.enter_context(mock.patch.object(l1, "save", return_value=None))
                    stack.enter_context(mock.patch.object(
                        l1, "_git", return_value=SimpleNamespace(stdout="test-branch\n", stderr="", returncode=0)
                    ))
                    stack.enter_context(mock.patch.object(l1.git_policy, "capture_origin_sha", return_value="a" * 40))
                    stack.enter_context(mock.patch.object(l1.git_policy, "commits_missing_task_trailer", return_value=[]))
                    stack.enter_context(mock.patch.object(l1.subprocess, "Popen", return_value=SimpleNamespace(pid=123)))
                    rec = l1.start(
                        "project", "task", brief, role=role, engine=engine,
                        cwd=str(root) if role == "reviewer" else None,
                    )

                prompt = (run_dir / f"{rec['name']}.prompt.md").read_text()
                self.assertEqual(picked, [route_role])
                if engine == "codex":
                    self.assertEqual(prompt.count(l1.CODEX_PATCH_NOTE), 1)
                    self.assertTrue(prompt.endswith(l1.FOOTER))
                    self.assertLess(prompt.index(l1.CODEX_PATCH_NOTE), prompt.index(l1.FOOTER))
                else:
                    persona = l1.config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
                    expected = persona.read_text() + "\n\n# Sub-brief\n\n" + brief.read_text() + l1.FOOTER
                    self.assertEqual(prompt, expected)
                    self.assertNotIn(l1.CODEX_PATCH_NOTE, prompt)
        self.assertIsNone(l1._codex_sandbox_denial(l1.CODEX_PATCH_NOTE))
        self.assertIsNone(l1._codex_sandbox_stop(l1.CODEX_PATCH_NOTE))

    def _run_codex_result(self, root: Path, response: dict, engine: str = "codex"):
        run_dir = root / "runs"
        run_dir.mkdir()
        (run_dir / "implementer-1.prompt.md").write_text("test prompt")
        rec = {
            "name": "implementer-1",
            "role": "implementer",
            "engine": engine,
            "model": "test-model",
            "worktree": str(root),
        }
        faults = []

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(l1, "load", return_value=rec))
            stack.enter_context(mock.patch.object(l1, "runs_dir", return_value=run_dir))
            stack.enter_context(mock.patch.object(l1, "save", return_value=None))
            stack.enter_context(mock.patch.object(l1, "_git", return_value=SimpleNamespace(stdout="")))
            stack.enter_context(mock.patch.object(S, "append_event", return_value=None))
            stack.enter_context(mock.patch.object(engines, "codex_exec", return_value=response))
            stack.enter_context(mock.patch.object(engines, "claude_print", return_value=response))
            stack.enter_context(mock.patch.object(improve, "system_fault", side_effect=lambda **kw: faults.append(kw)))
            result = l1.exec_run("project", "task", "implementer-1")
        return result, faults

    def test_bwrap_denial_becomes_codex_sandbox_engine_fault(self):
        response = {
            "text": "RESULT: no PR — engine stopped",
            "error": "setup failed\nbwrap: setting up uid map: Permission denied\n",
            "usage": {},
            "structured": None,
            "returncode": 1,
        }
        with tempfile.TemporaryDirectory() as td:
            rec, faults = self._run_codex_result(Path(td), response)

        self.assertEqual(rec["result"]["summary"], "engine fault: codex-sandbox")
        self.assertIsNone(rec["result"]["pr"])
        self.assertEqual(faults, [{
            "kind": "codex-sandbox",
            "detail": "bwrap: setting up uid map: Permission denied",
            "project": "project",
            "task": "task",
        }])

    # The record I-055 left behind (tasks/apply-r-011-i-031/l1/implementer-2.json) carried no raw bwrap
    # line at all: error None, returncode 0, and the denial only in the worker's own final sentence.
    STOPPED = ("RESULT: no PR — Stopped because bubblewrap sandbox setup denied both mandated file writes "
               "and Git/Altitude commands before any change could be made.")

    def test_sandbox_stop_without_a_raw_bwrap_line_is_an_engine_fault(self):
        response = {"text": self.STOPPED, "error": None, "usage": {}, "structured": None, "returncode": 0}
        with tempfile.TemporaryDirectory() as td:
            rec, faults = self._run_codex_result(Path(td), response)

        self.assertEqual(rec["result"]["summary"], "engine fault: codex-sandbox")
        self.assertIsNone(rec["result"]["pr"])
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["kind"], "codex-sandbox")
        self.assertIn("bubblewrap sandbox setup denied", faults[0]["detail"])

    def test_same_stop_on_claude_is_not_a_codex_fault(self):
        response = {"text": self.STOPPED, "error": None, "usage": {}, "structured": None, "returncode": 0}
        with tempfile.TemporaryDirectory() as td:
            rec, faults = self._run_codex_result(Path(td), response, engine="claude")

        self.assertEqual(faults, [])
        self.assertNotEqual(rec["result"]["summary"], "engine fault: codex-sandbox")

    def test_a_landed_pr_that_merely_discusses_the_sandbox_is_not_reclassified(self):
        response = {
            "text": "RESULT: #61 — documented why bwrap namespace setup is denied under AppArmor",
            "error": None, "usage": {}, "structured": None, "returncode": 0,
        }
        with tempfile.TemporaryDirectory() as td:
            rec, faults = self._run_codex_result(Path(td), response)

        self.assertEqual(rec["result"]["pr"], 61)
        self.assertEqual(faults, [])

    def test_normal_codex_result_is_unchanged(self):
        response = {
            "text": "RESULT: #42 — landed normally",
            "error": None,
            "usage": {"input_tokens": 10},
            "structured": None,
            "returncode": 0,
        }
        with tempfile.TemporaryDirectory() as td:
            rec, faults = self._run_codex_result(Path(td), response)

        self.assertEqual(rec["result"]["summary"], "#42 — landed normally")
        self.assertEqual(rec["result"]["pr"], 42)
        self.assertEqual(faults, [])


if __name__ == "__main__":
    unittest.main()
