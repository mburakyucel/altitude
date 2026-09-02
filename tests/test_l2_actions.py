"""The normalized outcome broker trusts only an exact settled owner generation."""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-l2-outcomes-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from altitude import actions, config, contracts, dispatch, state as S, tasks as T  # noqa: E402


class TestL2Outcomes(unittest.TestCase):
    def setUp(self):
        self.repo = _ROOT / "repo"; self.repo.mkdir(exist_ok=True)
        config.ensure_root(); config.save_projects({"p": {"name": "p", "path": str(self.repo)}})
        for directory in (S.tasks_dir("p"), S.archive_dir("p")):
            if directory.exists():
                for path in sorted(directory.rglob("*"), reverse=True):
                    path.unlink() if path.is_file() else path.rmdir()

    def task(self, title="Codex outcome"):
        task = T.new("p", title, "request")
        task.update({"state": "running", "dispatch_id": f"{task['slug']}-1", "session_id": "thread-1",
                     "agent_id": "worker-1", "l2_engine": "codex", "l2_token": "token-1",
                     "attempt": 1, "worktree": str(self.repo), "owner_generation": 3})
        S.save_task("p", task); return task

    @staticmethod
    def action(kind="continue", *, message="Task update", **fields):
        observations = {"findings": [], "decisions": [], "fyis": [], "follow_up_proposals": [],
                        "deviations": [], "usage": {"input_tokens": None, "cached_input_tokens": None,
                        "output_tokens": None, "cost_usd": None}, "spend": {"turns": None,
                        "subagent_launches": None, "retries": None, "reverts": None}, "merge_hold": None}
        defaults = {"reason": "continue exactly", "helper_requests": []} if kind == "continue" else {}
        return {"message": message, "outcome": {"version": 1, "kind": kind,
                "observations": observations, **defaults, **fields}}

    @staticmethod
    def owner(task, *, result_id="codex:result"):
        return {"version": 1, "provider": "codex", "project": "p", "slug": task["slug"],
                "owner_generation": task["owner_generation"], "transition_id": "transition-3",
                "generation": "physical-3", "process_unit_id": "unit-3", "message_id": "message-3",
                "intent_digest": "1" * 64, "result_id": result_id, "result_sha256": "2" * 64,
                "terminal_stage": "complete", "empty_receipt_sha256": "3" * 64,
                "recovery_episode_id": None, "recovery_permit_revision": None}

    def patches(self, task, action, *, owner=None):
        owner = owner or self.owner(task)
        marker = {"present": True, "process_unit_id": owner["process_unit_id"],
                  "intent_digest": owner["intent_digest"], "result_id": owner["result_id"],
                  "sha256": owner["result_sha256"]}
        settled = {"task": S.load_task("p", task["slug"]), "agent": {"action": action},
                   "result": marker, "owner_result": owner, "worker_result": action}
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(dispatch, "require_owner_result", return_value=settled, create=True))
        stack.enter_context(mock.patch.object(dispatch, "owner_operation_lock",
                                              side_effect=lambda *_: contextlib.nullcontext(), create=True))
        stack.enter_context(mock.patch.object(dispatch, "owner_result_snapshot", return_value=owner, create=True))
        stack.enter_context(mock.patch.object(dispatch, "require_owner_permit_current", create=True))
        return stack

    def test_closed_envelope_rejects_parallel_legacy_action_fields(self):
        with self.assertRaisesRegex(actions.ActionError, "exactly message and outcome"):
            actions._validate_shape({"message": "done", "action": "complete_no_code"})  # noqa: SLF001

    def test_helper_policy_is_codex_or_null_and_at_most_four_in_python(self):
        helper = {"role": "reviewer", "brief": "review", "provider": "codex", "model": None,
                  "scope": {"version": 1, "kind": "policy_derived"}}
        contracts.validate_worker_outcome(self.action(helper_requests=[helper] * 4)["outcome"])
        for helpers in ([{**helper, "provider": "claude"}], [helper] * 5):
            with self.subTest(helpers=len(helpers)), self.assertRaises(contracts.ContractError):
                contracts.validate_worker_outcome(self.action(helper_requests=helpers)["outcome"])

    def test_owner_snapshot_requires_exact_context_terminal_empty_and_permit_pair(self):
        task = self.task()
        for change in ({"project": "other"}, {"terminal_stage": "failed"},
                       {"empty_receipt_sha256": "short"}, {"recovery_episode_id": "R-1"}):
            with self.subTest(change=change), self.assertRaises(actions.ActionError):
                actions._validate_owner_result({**self.owner(task), **change}, "p", task["slug"])  # noqa: SLF001

    def test_claim_stores_only_reference_and_one_canonical_immutable_row(self):
        task, action = self.task(), self.action()
        with self.patches(task, action):
            row = actions._claim("p", task, action, self.owner(task))  # noqa: SLF001
            again = actions._claim("p", task, action, self.owner(task))  # noqa: SLF001
        live = S.load_task("p", task["slug"])
        self.assertEqual(live["outcome_ref"], {"id": row["outcome_id"], "stage": "claimed"})
        self.assertNotIn("pending_action", live); self.assertEqual(row, again)
        self.assertEqual(S.read_jsonl(S.task_dir("p", task["slug"]) / "outcomes.jsonl",
                                      key_field="record_id"), [row])

    def test_same_owner_result_cannot_be_reused_for_different_outcome(self):
        task, first, second = self.task(), self.action(reason="first"), self.action(reason="second")
        with self.patches(task, first): actions._claim("p", task, first, self.owner(task))  # noqa: SLF001
        with self.patches(task, second), self.assertRaisesRegex(ValueError, "conflicting JSONL record"):
            actions._claim("p", task, second, self.owner(task))  # noqa: SLF001

    def test_reply_key_is_stable_and_replay_deduplicates_conversation(self):
        task, action = self.task(), self.action(message="Stable reply")
        with self.patches(task, action):
            row = actions._claim("p", task, action, self.owner(task))  # noqa: SLF001
            actions._post_message("p", task, row); actions._post_message("p", task, row)  # noqa: SLF001
        self.assertEqual([(m["id"], m["text"]) for m in T.task_messages("p", task["slug"])],
                         [(row["message_id"], "Stable reply")])
        self.assertEqual(S.load_task("p", task["slug"])["outcome_ref"]["stage"], "message_posted")

    def test_claim_fences_unrelated_message_and_resume_before_effect(self):
        task, action = self.task(), self.action()
        with self.patches(task, action): actions._claim("p", task, action, self.owner(task))  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "claimed worker outcome"):
            T.append_task_message("p", task["slug"], "burak", "race", expected_dispatch_id=task["dispatch_id"])
        with self.assertRaisesRegex(T.TransitionError, "claimed worker outcome"):
            dispatch._require_resume_snapshot(S.load_task("p", task["slug"]), task["slug"])  # noqa: SLF001

    @staticmethod
    def _capture(errors, function, *args):
        try: function(*args)
        except Exception as exc: errors.append(exc)  # noqa: BLE001

    def test_state_first_claim_crash_is_rebuilt_before_racing_reject_archives(self):
        task, action = self.task("claim crash"), self.action()
        original, entered, release, failed = S.append_jsonl, threading.Event(), threading.Event(), [False]
        def fail_claim(path, row, **kwargs):
            if str(path).endswith("outcomes.jsonl") and row.get("stage") == "claimed" and not failed[0]:
                failed[0] = True; entered.set(); release.wait(2); raise OSError("kill after state")
            return original(path, row, **kwargs)
        errors = []
        with self.patches(task, action), mock.patch.object(S, "append_jsonl", side_effect=fail_claim):
            claiming = threading.Thread(target=lambda: self._capture(
                errors, actions._claim, "p", task, action, self.owner(task)))
            claiming.start(); self.assertTrue(entered.wait(2))
            rejecting = threading.Thread(target=lambda: self._capture(
                errors, T.reject, "p", task["slug"], "operator cancel"))
            rejecting.start(); time.sleep(.05); self.assertTrue(rejecting.is_alive())
            release.set(); claiming.join(2); rejecting.join(2)
        self.assertTrue(any(isinstance(exc, OSError) for exc in errors))
        archived = S.archive_dir("p") / task["slug"]
        rows = S.read_jsonl(archived / "outcomes.jsonl", key_field="record_id")
        self.assertEqual([row["stage"] for row in rows], ["claimed", "cancelled"])
        self.assertEqual(S.read_json(archived / "status.json")["outcome_ref"]["stage"], "cancelled")

    def test_effecting_claim_refuses_reject_and_defensive_archive(self):
        task, action = self.task(), self.action()
        with self.patches(task, action):
            row = actions._claim("p", task, action, self.owner(task))  # noqa: SLF001
            actions._advance("p", task["slug"], row["outcome_id"], "effecting")  # noqa: SLF001
            with self.assertRaisesRegex(T.TransitionError, "must reconcile"):
                T.reject("p", task["slug"], "too late")
        with S.project_lock("p"), self.assertRaisesRegex(T.TransitionError, "must reconcile or cancel"):
            T._archive("p", task["slug"])  # noqa: SLF001

    def test_process_uses_only_settled_agent_outcome_and_blocks(self):
        task = self.task()
        exact = self.action("block", message="Need input", reason_or_question="Choose",
                            resume_condition="operator answers")
        with self.patches(task, exact), mock.patch.object(actions.recovery, "dispatch_hold", return_value=None):
            result = actions.process_l2("p", {"task": task, "action": self.action(reason="ignore")})
        self.assertEqual(result["kind"], "blocked")
        live = S.load_task("p", task["slug"])
        self.assertEqual(live["blocked_reason"], "Choose Resume when: operator answers")
        self.assertEqual(live["outcome_ref"]["stage"], "complete")

    def test_outcome_reader_fails_closed_on_oversize_artifact(self):
        task = self.task(); path = S.task_dir("p", task["slug"]) / "outcomes.jsonl"
        path.write_bytes(b"x" * (actions.MAX_OUTCOME_LOG_BYTES + 1))
        with self.assertRaisesRegex(actions.ActionError, "exceeds"):
            actions._read_outcomes("p", task["slug"])  # noqa: SLF001


if __name__ == "__main__": unittest.main()
