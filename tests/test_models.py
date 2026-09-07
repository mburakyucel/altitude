"""Role model defaults are explicit; task-level overrides are persisted and validated."""
import json
import sys
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, state as S, tasks as T


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


class TestSessionModel(AltitudeCase):
    """Prevent the unknown-model incident and stale attribution after a CLI default changes."""

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.home = self.tmp / "provider-home"
        self.rollout = self.home / "sessions/2026/09/07/rollout-date-thread-1.jsonl"
        self.rollout.parent.mkdir(parents=True)
        self.started = "2026-09-07T08:00:00+00:00"

    def context(self, model="actual-model", effort="high", at="2026-09-07T08:00:01Z"):
        return json.dumps({"type": "turn_context", "timestamp": at,
                           "payload": {"model": model, "effort": effort}}) + "\n"

    def test_resumed_turn_uses_its_context_not_the_old_model_or_requested_config(self):
        self.rollout.write_text(self.context("old", at="2026-09-07T07:59:59Z") + self.context())
        self.assertEqual(engines._codex_session_model("thread-1", self.started, self.home),
                         {"engine_model": "actual-model", "engine_reasoning_effort": "high"})

    def test_missing_partial_and_model_less_rollout_is_unknown_then_retried(self):
        read = lambda: engines._codex_session_model("thread-1", self.started, self.home)
        self.assertEqual(read(), {})
        self.rollout.write_text(self.context(None) + '{"type":"turn_context"')
        self.assertEqual(read(), {})
        self.rollout.write_text(self.context(effort=None))
        self.assertEqual(read(), {"engine_model": "actual-model", "engine_reasoning_effort": None})
        self.assertEqual(engines._codex_session_model("*", self.started, self.home), {})

    def test_worker_caches_observation_and_poll_persists_task_and_live_snapshot(self):
        task = T.new(self.project, "Observed model", "request")
        task.update(state="running", attempt=1, l2_engine="codex", agent_id="worker-1", session_id="thread-1",
                    engine_model=None)
        S.save_task(self.project, task)
        paths = engines._codex_paths(dispatch.l2_job_root(self.project, task["slug"]), "worker-1")
        S.write_json(paths["record"], {"started_at": self.started, "codex_home": str(self.home)})
        paths["stdout"].write_text('{"type":"thread.started","thread_id":"thread-1"}\n')
        self.rollout.write_text(self.context())
        with mock.patch.object(engines, "_unit_active", return_value=True):
            self.assertEqual(dispatch.poll(self.project), [])
            self.rollout.unlink()
            row = engines.codex_worker("worker-1", job_root=paths["record"].parent)
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual(saved["engine_model"], "actual-model")
        self.assertEqual(saved["engine_reasoning_effort"], "high")
        self.assertIn("launch_model", saved, "tasks predating observation preserve their original launch choice")
        self.assertIsNone(saved["launch_model"], "recording a CLI default must not turn it into an override")
        self.assertEqual(row["engine_model"], "actual-model", "observation survives rollout removal")
        live = S.read_json(config.MONITOR_DIR / f"live-{self.project}--{task['slug']}.json")
        self.assertEqual(live["agent"]["engine_model"], "actual-model")

    def test_synchronous_runner_observes_model_before_process_exit(self):
        # Real pipes exercise communicate's timeout/resume behavior: input is sent exactly once.
        script = '''import json, pathlib, sys, time
from datetime import datetime, timezone
assert sys.stdin.read() == "one prompt"
print(json.dumps({"type":"thread.started", "thread_id":"thread-1"}), flush=True)
p = pathlib.Path(sys.argv[1])
p.write_text(json.dumps({"type":"turn_context", "timestamp":datetime.now(timezone.utc).isoformat(),
                        "payload":{"model":"actual-model", "effort":"high"}}) + "\\n")
time.sleep(1.2)
assert p.with_suffix(".observed").exists(), "metadata callback must run while this turn is live"
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":7}}), flush=True)
'''
        observations = []
        def observed(metadata):
            observations.append(metadata)
            self.rollout.with_suffix(".observed").touch()

        with mock.patch.object(engines, "_codex_service_command",
                               return_value=[sys.executable, "-c", script, str(self.rollout)]):
            result = engines.codex_exec("one prompt", cwd=self.tmp, timeout=5,
                                       extra_env={"CODEX_HOME": str(self.home)},
                                       on_session=observed)
        self.assertEqual(observations, [{"session_id": "thread-1", "engine_model": "actual-model",
                                         "engine_reasoning_effort": "high"}])
        self.assertEqual(result["engine_model"], "actual-model")
        self.assertEqual(result["returncode"], 0, result["error"])
        self.assertEqual(result["usage"], {"input_tokens": 7})

if __name__ == "__main__":
    unittest.main()
