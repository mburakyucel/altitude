"""L3 rotation and physical intent are durable before the managed effect."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="altitude-restart-turn-")
os.environ["ALTITUDE_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, engines, l3, state as S  # noqa: E402

PROJECT = "restartdup"
INSTANCE = "restart-test-instance"


class TestRotationIsPersistedBeforeTheTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        projects = config.load_projects()
        projects[PROJECT] = {"name": PROJECT, "path": config.ROOT.as_posix(), "l3_engine": "codex"}
        config.save_projects(projects)

    def test_rotation_and_new_generation_exist_before_spawn(self):
        l3.save_info(PROJECT, {
            "l3_ownership_version": 1, "engine_last": "codex", "session_id": "old-thread",
            "rotate_next": True, "context_percent": 100.0, "turns": 12,
            "sessions": {"codex": {"session_id": "old-thread", "rotate_next": True,
                                    "context_percent": 100.0, "turns": 12,
                                    "process_unit_id": engines.deterministic_process_unit(
                                        "l3", PROJECT, "old")}},
        })
        seen = {}

        def empty(unit):
            return {"process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
                    "sub_state": "dead", "control_group": "", "population": "empty", "empty": True}

        def spawn(transition, command, **_kwargs):
            seen["transition"] = transition
            seen["on_disk"] = l3.info(PROJECT)
            l3._managed_codex(command[-3], command[-2], command[-1])  # noqa: SLF001
            return mock.Mock(pid=7, wait=mock.Mock(return_value=0))

        codex_result = {
            "text": "hello", "session_id": "new-thread", "reported_session_id": "new-thread",
            "usage": {"input_tokens": 1000}, "structured": {"message": "hello", "actions": []},
            "error": None, "returncode": 0, "containment_empty": True,
        }

        def codex(*_args, **kwargs):
            S.atomic_write(kwargs["answer_path"], json.dumps(codex_result["structured"]))
            with kwargs["event_spool"].open("a") as stream:
                stream.write('{"type":"thread.started","thread_id":"new-thread"}\n')
                stream.write('{"type":"turn.completed","usage":{"input_tokens":1000}}\n')
            return codex_result

        with mock.patch.object(l3, "_select", return_value={"engine": "codex", "why": "test", "quota": {}}), \
             mock.patch.object(engines, "observe_managed_unit", side_effect=empty), \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn), \
             mock.patch.object(engines, "codex_exec", side_effect=codex):
            out = l3.turn(PROJECT, "turn after rotation", instance_id=INSTANCE)

        self.assertTrue(out["completed"])
        self.assertEqual(seen["transition"]["provider_session_request"], {"kind": "fresh"})
        self.assertIsNone(seen["on_disk"]["sessions"]["codex"]["session_id"])
        self.assertEqual(seen["on_disk"]["sessions"]["codex"]["rotated_from"], "old-thread")
        self.assertEqual(seen["on_disk"]["current_turn"]["physical"]["stage"], "prior_stopped")
        self.assertEqual(l3.info(PROJECT)["session_id"], "new-thread")
        self.assertEqual(l3.info(PROJECT)["turns"], 1)


if __name__ == "__main__":
    unittest.main()
