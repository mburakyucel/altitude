"""Role model defaults are explicit; task-level overrides are persisted and validated."""
import os, sys, tempfile, unittest
from pathlib import Path
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-models-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, tasks as T  # noqa: E402


class TestModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": config.ROOT.as_posix()}})

    def test_tiers(self):
        self.assertEqual(config.MODELS["l3"], "fable")            # judgement at the top
        self.assertEqual(config.MODELS["l1"], "opus")             # coding at least Opus
        self.assertEqual(set(config.MODELS), {"l3", "l2", "l1", "reviewer"})

    def test_task_carries_only_an_enabled_provider_model(self):
        t = T.new("p", "plain", "r", actor="burak")
        self.assertIsNone(t.get("model"))
        t2 = T.new("p", "hard", "r", actor="burak", engine="codex", model="gpt-test")
        self.assertEqual((t2["engine"], t2["model"]), ("codex", "gpt-test"))
        with self.assertRaises(T.TransitionError):
            T.new("p", "disabled", "r", actor="burak", model="fable")


if __name__ == "__main__":
    unittest.main()
