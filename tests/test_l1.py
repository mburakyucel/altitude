"""Public managed-helper entrypoints stay inert until the B3 owner seam exists."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_IMPORT_ROOT = tempfile.mkdtemp(prefix="altitude-helper-dormant-import-")
os.environ["ALTITUDE_HOME"] = _IMPORT_ROOT

from altitude import actions, config, l1, state as S, tasks as T


class TestDormantHelperEntryPoints(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-helper-dormant-")
        self.root = Path(self.temp.name)
        self.root_patch = mock.patch.object(config, "ROOT", self.root / "state")
        self.root_patch.start()
        task_dir = S.tasks_dir("project") / "task"
        task_dir.mkdir(parents=True)
        S.write_json(task_dir / "status.json", {"slug": "task", "l2_engine": "codex"})
        self.brief = self.root / "brief.md"
        self.brief.write_text("bounded work\n")

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_public_start_and_exec_are_inert_without_b3(self):
        before = sorted(path.relative_to(config.ROOT) for path in config.ROOT.rglob("*"))
        with self.assertRaisesRegex(T.TransitionError, "await.*Phase 1B.3"):
            l1.start("project", "task", self.brief, expected_l2_token="model-controlled")
        with self.assertRaisesRegex(T.TransitionError, "direct helper execution is disabled"):
            l1.exec_run("project", "task", "helper-anything")
        after = sorted(path.relative_to(config.ROOT) for path in config.ROOT.rglob("*"))
        self.assertEqual(after, before)

    def test_action_broker_refuses_before_brief_or_helper_artifacts(self):
        task = {"slug": "task", "attempt": 3}
        action = {"helpers": [{"role": "implementer", "brief": "change x", "paths": ["x"]}]}
        with mock.patch.object(S, "atomic_write") as write, \
             self.assertRaisesRegex(actions.ActionError, "await.*Phase 1B.3"):
            actions._helpers("project", task, action)  # noqa: SLF001 - dormant boundary assertion
        write.assert_not_called()
        self.assertFalse((S.task_dir("project", "task") / "l1").exists())

    def test_no_direct_provider_or_secret_transport_exists(self):
        source = Path(l1.__file__).read_text()
        self.assertNotIn("ALTITUDE_L2_TOKEN", source)
        self.assertNotIn("ALTITUDE_L2_CAPABILITY", source)
        self.assertNotIn("spawn_managed_unit", source)
        self.assertNotIn("codex_exec(", source)
        self.assertNotIn("claude_print(", source)
        self.assertNotIn("subprocess.Popen", source)

    def test_closed_primitives_have_no_runtime_caller(self):
        names = ("_install_operation(", "_prepare_worktree(", "_prepare_prompt(",
                 "_spawn_and_record(", "_write_result_marker(", "_reconcile(")
        repository = Path(l1.__file__).resolve().parent.parent
        sources = [repository / "bin" / "alt", *(repository / "altitude").glob("*.py")]
        for path in sources:
            if path == Path(l1.__file__):
                continue
            text = path.read_text()
            self.assertFalse(any(name in text for name in names), f"dormant helper caller in {path}")


if __name__ == "__main__":
    unittest.main()
