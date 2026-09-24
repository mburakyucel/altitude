"""The project default L2 model per engine: CLI persistence beside effort, and routing that treats it as a preference."""
import json

from tests.support import AltitudeCase
from altitude import config, dispatch, route, tasks as T


MODEL_SETTINGS = tuple(config.model_setting("l2", engine) for engine in config.ENGINES)


class TestProjectModel(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.quiet_engines()

    def test_cli_sets_lists_and_unsets_each_engine_default_without_touching_effort(self):
        self.register(self.project, l2_effort="high")
        for engine in config.ENGINES:
            flag = config.model_setting("l2", engine).replace("_", "-")
            result = self.alt("project", "set", self.project, f"--{flag}", "chosen-model", "--reason", "Set default model")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["request"]["status"], "pending")
            self.assertNotIn(config.model_setting("l2", engine), config.project(self.project))
            self.assertEqual(dispatch.run_settings(self.project)[config.model_setting("l2", engine)]["status"], "done")
            self.assertEqual(config.project(self.project)[config.model_setting("l2", engine)], "chosen-model")
            choice = route.pick_engine("l2", forced=engine, project=config.project(self.project))
            self.assertEqual((choice["engine"], choice["model"], choice["effort"]), (engine, "chosen-model", "high"))
        listing = self.alt("project", "list")
        self.assertEqual({key: json.loads(listing.stdout)[self.project][key] for key in MODEL_SETTINGS},
                         dict.fromkeys(MODEL_SETTINGS, "chosen-model"))
        self.assertFalse(route.pick_engine("l2", project=config.project(self.project))["pinned"])
        for engine in config.ENGINES:
            flag = config.model_setting("l2", engine).replace("_", "-")
            result = self.alt("project", "set", self.project, f"--unset-{flag}", "--reason", "Restore the role default")
            self.assertEqual(result.returncode, 0, result.stderr)
            dispatch.run_settings(self.project)
            self.assertNotIn(config.model_setting("l2", engine), config.project(self.project))
            self.assertEqual(route.pick_engine("l2", forced=engine, project=config.project(self.project))["model"],
                             config.default_model("l2", engine))
        self.assertEqual(config.project(self.project)["l2_effort"], "high")

    def test_invalid_values_are_refused_before_anything_is_queued(self):
        self.register(self.project)
        original = config.project(self.project)
        for value in ("", "two words", " opus", 7):
            with self.subTest(value=value), self.assertRaisesRegex(T.TransitionError, "one alias or model id"):
                dispatch.request_setting(self.project, "l2_model", value, "Set default model", actor="burak")
        with self.assertRaisesRegex(T.TransitionError, "unknown project setting"):
            dispatch.request_setting(self.project, "l1_model", "opus", "Set default model", actor="burak")
        self.assertEqual(dispatch.run_settings(self.project)["l2_model"], {})
        self.assertEqual(config.project(self.project), original)
