"""Role model defaults are explicit; task-level overrides are persisted and validated."""
import unittest

from tests.support import AltitudeCase
from altitude import config, tasks as T


class TestModels(AltitudeCase):
    def test_tiers(self):
        self.assertEqual(config.MODELS["l3"], "fable")            # judgement at the top
        self.assertEqual(config.MODELS["l2"], "opus")             # coding at least Opus
        self.assertEqual(set(config.MODELS), {"l3", "l2"})

    def test_task_carries_explicit_model_only(self):
        t = T.new(self.project, "plain", "r", actor="burak")
        self.assertIsNone(t.get("model"))
        t2 = T.new(self.project, "hard", "r", actor="burak", model="fable")
        self.assertEqual(t2["model"], "fable")
        with self.assertRaises(T.TransitionError):
            T.new(self.project, "bad", "r", actor="burak", model="gpt")


if __name__ == "__main__":
    unittest.main()
