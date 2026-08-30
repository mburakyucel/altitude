"""Decision 38: model tiers are explicit; Fable only by an explicit task override."""
import os, sys, tempfile, unittest
from pathlib import Path
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-models-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, tasks as T  # noqa: E402


class TestModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"p": {"name": "p", "path": config.ROOT.as_posix(), "stacks": []}})

    def test_defaults_never_fable(self):
        self.assertNotIn("fable", config.MODELS.values())
        self.assertEqual(config.MODELS["l3"], "opus")
        self.assertEqual(config.MODELS["l1"], "sonnet")

    def test_task_carries_explicit_model_only(self):
        t = T.new("p", "plain", "S", "r", actor="burak")
        self.assertIsNone(t.get("model"))
        t2 = T.new("p", "hard", "L", "r", actor="burak", model="fable")
        self.assertEqual(t2["model"], "fable")
        with self.assertRaises(T.TransitionError):
            T.new("p", "bad", "S", "r", actor="burak", model="gpt")


if __name__ == "__main__":
    unittest.main()
