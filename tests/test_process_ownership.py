"""Dormant physical-transition and managed-unit foundation."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(tempfile.mkdtemp(prefix="altitude-process-ownership-"))
os.environ["ALTITUDE_HOME"] = str(_ROOT / "state")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from altitude import engines  # noqa: E402


def json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def transition(*, recovery: bool = False) -> dict:
    return engines.new_physical_transition(
        transition_id="transition-1", subject_kind="owner", subject_id="project/task",
        generation="attempt-2/physical-3", provider="codex",
        provider_session_request={"kind": "resume", "session_id": "provider-session"},
        message_id="message-1",
        recovery_episode_id="episode-4" if recovery else None,
        recovery_permit_revision=7 if recovery else None,
    )


def receipt(record: dict, stage: str) -> dict:
    unit = record["process_unit_id"]
    return {
        "prior_stopped": {"previous_process_unit_id": None, "empty": True},
        "spawned": {"process_unit_id": unit, "launched": True},
        "bound": {"bound": True, "physical_worker_id": "worker-3",
                  "provider_session_id": "provider-session"},
        "result_observed": {"result_id": "spool/result.json", "sha256": "a" * 64},
        "empty": {"process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
                  "sub_state": "dead", "control_group": "", "population": "empty", "empty": True},
        "complete": {"status": "complete"},
        "failed": {"status": "failed"},
    }[stage]


def advance_to(record: dict, target: str) -> dict:
    for current, following in zip(engines.PHYSICAL_TRANSITION_STAGES, engines.PHYSICAL_TRANSITION_STAGES[1:]):
        if following == "failed":
            break
        record = engines.advance_physical_transition(record, current, following, receipt(record, following))
        if following == target:
            return record
    raise AssertionError(f"cannot advance to {target}")


class TestPhysicalTransition(unittest.TestCase):
    def test_intent_is_closed_deterministic_and_detached(self):
        request = {"kind": "resume", "session_id": "provider-session"}
        record = transition(recovery=True)
        request["session_id"] = "changed"

        self.assertEqual(record["stage"], "planned")
        self.assertEqual(record["provider_session_request"]["session_id"], "provider-session")
        self.assertEqual(record["recovery_episode_id"], "episode-4")
        self.assertEqual(record["recovery_permit_revision"], 7)
        self.assertEqual(record["process_unit_id"], engines.deterministic_process_unit(
            "owner", "project/task", "attempt-2/physical-3"))
        self.assertRegex(record["intent_digest"], r"^[0-9a-f]{64}$")
        self.assertLessEqual(len(record["process_unit_id"]), 255)
        self.assertEqual(engines.validate_physical_transition(record), record)

    def test_unit_names_are_stable_safe_and_generation_specific(self):
        one = engines.deterministic_process_unit("helper", "project/task helper !", "generation/1")
        self.assertEqual(one, engines.deterministic_process_unit("helper", "project/task helper !", "generation/1"))
        self.assertNotEqual(one, engines.deterministic_process_unit("helper", "project/task helper !", "generation/2"))
        self.assertRegex(one, r"^altitude-worker-helper-[A-Za-z0-9_.-]+\.service$")

    def test_intent_rejects_unknown_shapes_and_half_recovery_identity(self):
        cases = [
            {"provider": "other"},
            {"provider_session_request": {"kind": "fresh", "session_id": "extra"}},
            {"provider_session_request": {"kind": "resume", "session_id": ""}},
            {"recovery_episode_id": "episode", "recovery_permit_revision": None},
            {"recovery_episode_id": "episode", "recovery_permit_revision": True},
        ]
        base = dict(
            transition_id="t", subject_kind="l3", subject_id="p", generation="g", provider="codex",
            provider_session_request={"kind": "fresh"}, message_id="m",
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(engines.PhysicalTransitionError):
                engines.new_physical_transition(**(base | changes))

    def test_record_rejects_unknown_fields_nondeterministic_unit_and_non_json(self):
        for mutate in (
            lambda value: value.update({"extra": True}),
            lambda value: value.update({"process_unit_id": "altitude-worker-owner-wrong.service"}),
            lambda value: value["receipts"].update({"bad": {}}),
            lambda value: value.update({"revision": True}),
            lambda value: value["provider_session_request"].update({1: "bad"}),
            lambda value: value["provider_session_request"].update({"number": math.inf}),
            lambda value: value.update({"message_id": "different"}),
            lambda value: value.update({"intent_digest": "0" * 64}),
        ):
            value = transition()
            mutate(value)
            with self.assertRaises(engines.PhysicalTransitionError):
                engines.validate_physical_transition(value)

    def test_revision_and_every_receipt_shape_are_exact(self):
        record = advance_to(transition(), "empty")
        self.assertEqual(record["revision"], len(record["receipts"]))
        for stage, extra in (
            ("prior_stopped", {"extra": True}),
            ("spawned", {"launcher_pid": 1}),
            ("bound", {"extra": True}),
            ("result_observed", {"extra": True}),
            ("empty", {"extra": True}),
        ):
            corrupted = dict(record)
            corrupted["receipts"] = {name: dict(value) for name, value in record["receipts"].items()}
            corrupted["receipts"][stage].update(extra)
            with self.subTest(stage=stage), self.assertRaises(engines.PhysicalTransitionError):
                engines.validate_physical_transition(corrupted)
        record["revision"] += 1
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "exactly derived"):
            engines.validate_physical_transition(record)

    def test_exact_sequence_and_identical_retry(self):
        record = transition()
        prior = receipt(record, "prior_stopped")
        moved = engines.advance_physical_transition(record, "planned", "prior_stopped", prior)
        self.assertEqual(moved["stage"], "prior_stopped")
        self.assertEqual(record["stage"], "planned", "advance must not mutate the caller's aggregate")
        self.assertEqual(
            engines.advance_physical_transition(moved, "planned", "prior_stopped", prior), moved,
        )
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "conflicting replay"):
            engines.advance_physical_transition(
                moved, "planned", "prior_stopped", {**prior, "previous_process_unit_id": "other"}
            )

    def test_skips_reverse_moves_and_false_empty_receipts_are_refused(self):
        record = transition()
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "illegal"):
            engines.advance_physical_transition(record, "planned", "spawned", {})
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "proven empty"):
            engines.advance_physical_transition(
                record, "planned", "prior_stopped", {"previous_process_unit_id": None, "empty": False}
            )
        record = advance_to(record, "result_observed")
        bad = receipt(record, "empty") | {"process_unit_id": "altitude-worker-owner-other.service"}
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "different process unit"):
            engines.advance_physical_transition(record, "result_observed", "empty", bad)

    def test_failed_is_possible_only_after_empty_and_a_stable_error(self):
        record = transition()
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "illegal"):
            engines.advance_physical_transition(record, "planned", "failed", receipt(record, "failed"))
        empty = advance_to(record, "empty")
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "recorded"):
            engines.advance_physical_transition(empty, "empty", "failed", receipt(empty, "failed"))
        failed = engines.note_physical_transition_error(empty, "provider exited")
        self.assertEqual(engines.note_physical_transition_error(failed, "provider exited"), failed)
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "different error"):
            engines.note_physical_transition_error(failed, "another error")
        failed = engines.advance_physical_transition(failed, "empty", "failed", receipt(failed, "failed"))
        self.assertEqual(failed["stage"], "failed")
        self.assertTrue(failed["receipts"]["empty"]["empty"])

    def test_error_cannot_complete_and_recovery_identity_never_changes(self):
        record = transition(recovery=True)
        identity = record["recovery_episode_id"], record["recovery_permit_revision"]
        record = advance_to(record, "empty")
        record = engines.note_physical_transition_error(record, "fault")
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "cannot complete"):
            engines.advance_physical_transition(record, "empty", "complete", receipt(record, "complete"))
        self.assertEqual((record["recovery_episode_id"], record["recovery_permit_revision"]), identity)

    def test_clean_empty_transition_can_complete(self):
        record = advance_to(transition(), "empty")
        record = engines.advance_physical_transition(record, "empty", "complete", receipt(record, "complete"))
        self.assertEqual(record["stage"], "complete")
        self.assertIsNone(record["error"])

    def test_pre_spawn_failure_reconciles_without_fabricating_a_launch(self):
        record = transition()
        record = engines.advance_physical_transition(
            record, "planned", "prior_stopped", receipt(record, "prior_stopped")
        )
        record = engines.note_physical_transition_error(record, "systemd-run was not executable")
        unit = record["process_unit_id"]
        record = engines.advance_physical_transition(
            record, "prior_stopped", "spawned",
            {"process_unit_id": unit, "launched": False, "reason": "popen failed"},
        )
        record = engines.advance_physical_transition(
            record, "spawned", "bound", {"bound": False, "reason": "not launched"}
        )
        error = record["error"].encode()
        record = engines.advance_physical_transition(
            record, "bound", "result_observed",
            {"result_id": "transition:error", "sha256": hashlib.sha256(error).hexdigest()},
        )
        record = engines.advance_physical_transition(
            record, "result_observed", "empty", receipt(record, "empty")
        )
        record = engines.advance_physical_transition(
            record, "empty", "failed", receipt(record, "failed")
        )
        self.assertFalse(record["receipts"]["spawned"]["launched"])
        self.assertFalse(record["receipts"]["bound"]["bound"])
        self.assertTrue(record["receipts"]["empty"]["empty"])
        self.assertEqual(record["stage"], "failed")

    def test_negative_spawn_or_bind_requires_reason_error_and_exact_error_result(self):
        prior = engines.advance_physical_transition(
            transition(), "planned", "prior_stopped", receipt(transition(), "prior_stopped")
        )
        unit = prior["process_unit_id"]
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "durable error"):
            engines.advance_physical_transition(
                prior, "prior_stopped", "spawned",
                {"process_unit_id": unit, "launched": False, "reason": "failed"},
            )
        failed = engines.note_physical_transition_error(prior, "failed")
        failed = engines.advance_physical_transition(
            failed, "prior_stopped", "spawned",
            {"process_unit_id": unit, "launched": False, "reason": "failed"},
        )
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "closed reason"):
            engines.advance_physical_transition(failed, "spawned", "bound", {"bound": False, "reason": ""})
        failed = engines.advance_physical_transition(
            failed, "spawned", "bound", {"bound": False, "reason": "not launched"}
        )
        with self.assertRaisesRegex(engines.PhysicalTransitionError, "exact durable error"):
            engines.advance_physical_transition(
                failed, "bound", "result_observed",
                {"result_id": "other", "sha256": "a" * 64},
            )


class TestPhysicalReconciliation(unittest.TestCase):
    @staticmethod
    def unit(record: dict, *, empty: bool = True, unknown: bool = False) -> dict:
        return {
            "process_unit_id": record["process_unit_id"], "load_state": "loaded",
            "active_state": "inactive" if empty else "active",
            "sub_state": "dead" if empty else "running", "control_group": "/unit",
            "population": "unknown" if unknown else ("empty" if empty else "populated"),
            "empty": empty,
        }

    @staticmethod
    def durable(record: dict) -> dict:
        result = record["receipts"].get("result_observed") or receipt(record, "result_observed")
        return {
            "present": True, "process_unit_id": record["process_unit_id"],
            "intent_digest": record["intent_digest"], **result,
        }

    def reconcile(self, record: dict, *, empty: bool, durable: dict | None = None) -> dict:
        with mock.patch.object(engines, "observe_managed_unit", return_value=self.unit(record, empty=empty)):
            return engines.reconcile_physical_transition(record, durable)

    def test_every_crash_stage_has_a_closed_nonmutating_decision(self):
        planned = transition()
        prior = advance_to(transition(), "prior_stopped")
        spawned = advance_to(transition(), "spawned")
        bound = advance_to(transition(), "bound")
        result = advance_to(transition(), "result_observed")
        empty = advance_to(transition(), "empty")
        complete = engines.advance_physical_transition(
            empty, "empty", "complete", receipt(empty, "complete")
        )
        cases = (
            (planned, True, None, "prove_prior_stopped"),
            (prior, False, None, "record_spawned"),
            (prior, True, self.durable(prior), "record_spawned"),
            (spawned, False, None, "record_bound"),
            (spawned, True, None, "process_missing"),
            (bound, False, None, "running"),
            (bound, True, None, "process_missing"),
            (bound, True, self.durable(bound), "record_result"),
            (result, False, self.durable(result), "wait_for_empty"),
            (result, True, self.durable(result), "record_empty"),
            (empty, True, self.durable(empty), "record_complete"),
            (complete, True, self.durable(complete), "settled"),
        )
        for record, is_empty, durable, decision in cases:
            before = json_bytes(record)
            with self.subTest(stage=record["stage"], decision=decision):
                observed = self.reconcile(record, empty=is_empty, durable=durable)
                self.assertEqual(observed["decision"], decision)
                self.assertEqual(json_bytes(record), before)

    def test_prior_stopped_empty_without_result_is_an_ambiguous_launch_window(self):
        record = advance_to(transition(), "prior_stopped")
        with mock.patch.object(
            engines, "observe_managed_unit", return_value=self.unit(record, empty=True)
        ), self.assertRaisesRegex(
            engines.PhysicalTransitionError, "ownership_uncertain: launch may have run"
        ):
            engines.reconcile_physical_transition(record, None)

    def test_prior_stopped_error_reconciles_as_a_negative_spawn_without_provider_effect(self):
        record = advance_to(transition(), "prior_stopped")
        record = engines.note_physical_transition_error(record, "systemd-run was not executable")
        before = json_bytes(record)
        with mock.patch.object(
            engines, "observe_managed_unit", return_value=self.unit(record, empty=True)
        ), mock.patch.object(engines, "spawn_managed_unit") as spawn:
            observed = engines.reconcile_physical_transition(record, None)
        self.assertEqual(observed["decision"], "record_spawn_failure")
        self.assertEqual(json_bytes(record), before)
        spawn.assert_not_called()

        error_result = {
            "present": True,
            "process_unit_id": record["process_unit_id"],
            "intent_digest": record["intent_digest"],
            "result_id": "transition:error",
            "sha256": hashlib.sha256(record["error"].encode()).hexdigest(),
        }
        self.assertEqual(
            self.reconcile(record, empty=True, durable=error_result)["decision"],
            "record_spawn_failure",
        )

        record = engines.advance_physical_transition(
            record, "prior_stopped", "spawned", {
                "process_unit_id": record["process_unit_id"],
                "launched": False,
                "reason": "pre-spawn error",
            },
        )
        self.assertEqual(self.reconcile(record, empty=True)["decision"], "record_bound_failure")

    def test_prior_stopped_error_refuses_populated_or_conflicting_result_evidence(self):
        record = advance_to(transition(), "prior_stopped")
        record = engines.note_physical_transition_error(record, "launch failed")
        conflicting = self.durable(record)
        for empty, durable in ((False, None), (True, conflicting)):
            with self.subTest(empty=empty, durable=durable), self.assertRaisesRegex(
                engines.PhysicalTransitionError, "ownership_uncertain"
            ):
                self.reconcile(record, empty=empty, durable=durable)

    def test_negative_path_reconciles_only_toward_failed(self):
        record = advance_to(transition(), "prior_stopped")
        record = engines.note_physical_transition_error(record, "launch failed")
        record = engines.advance_physical_transition(
            record, "prior_stopped", "spawned",
            {"process_unit_id": record["process_unit_id"], "launched": False, "reason": "exec failed"},
        )
        self.assertEqual(self.reconcile(record, empty=True)["decision"], "record_bound_failure")
        record = engines.advance_physical_transition(
            record, "spawned", "bound", {"bound": False, "reason": "not launched"}
        )
        self.assertEqual(self.reconcile(record, empty=True)["decision"], "record_error_result")
        error_receipt = {
            "result_id": "transition:error", "sha256": hashlib.sha256(b"launch failed").hexdigest()
        }
        record = engines.advance_physical_transition(record, "bound", "result_observed", error_receipt)
        record = engines.advance_physical_transition(record, "result_observed", "empty", receipt(record, "empty"))
        self.assertEqual(self.reconcile(record, empty=True, durable=self.durable(record))["decision"], "record_failed")
        record = engines.advance_physical_transition(record, "empty", "failed", receipt(record, "failed"))
        self.assertEqual(self.reconcile(record, empty=True, durable=self.durable(record))["decision"], "settled")

    def test_unknown_wrong_or_changed_ownership_refuses_explicitly(self):
        record = transition()
        wrong = self.durable(record) | {"intent_digest": "0" * 64}
        for observed, durable in (
            (self.unit(record, unknown=True), None),
            (self.unit(record), wrong),
            (self.unit(record, empty=False), None),
        ):
            with self.subTest(observed=observed, durable=durable), \
                    mock.patch.object(engines, "observe_managed_unit", return_value=observed), \
                    self.assertRaisesRegex(engines.PhysicalTransitionError, "ownership_uncertain"):
                engines.reconcile_physical_transition(record, durable)

    def test_recorded_result_must_still_exist_with_identical_identity_and_hash(self):
        record = advance_to(transition(), "result_observed")
        for durable in (None, self.durable(record) | {"sha256": "b" * 64}):
            with self.subTest(durable=durable), \
                    mock.patch.object(engines, "observe_managed_unit", return_value=self.unit(record)), \
                    self.assertRaisesRegex(engines.PhysicalTransitionError, "ownership_uncertain"):
                engines.reconcile_physical_transition(record, durable)


class TestManagedUnit(unittest.TestCase):
    def test_observation_preserves_exact_manager_and_population_state(self):
        unit = engines.deterministic_process_unit("owner", "p/t", "g")
        props = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                 "ControlGroup": "/user.slice/unit"}
        with mock.patch.object(engines, "_systemd_unit_properties", return_value=props), \
             mock.patch.object(engines, "_cgroup_population", return_value="populated"):
            observed = engines.observe_managed_unit(unit)
        self.assertEqual(observed, {
            "process_unit_id": unit, "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "control_group": "/user.slice/unit",
            "population": "populated", "empty": False,
        })

    def test_collected_unit_is_exactly_empty(self):
        unit = engines.deterministic_process_unit("l3", "p", "g")
        props = {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                 "ControlGroup": ""}
        with mock.patch.object(engines, "_systemd_unit_properties", return_value=props):
            self.assertTrue(engines.observe_managed_unit(unit)["empty"])

    def test_unknown_population_never_looks_empty(self):
        unit = engines.deterministic_process_unit("owner", "p/t", "g")
        props = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
                 "ControlGroup": "/unknown"}
        with mock.patch.object(engines, "_systemd_unit_properties", return_value=props), \
             mock.patch.object(engines, "_cgroup_population", return_value="unknown"):
            observed = engines.observe_managed_unit(unit)
        self.assertEqual(observed["population"], "unknown")
        self.assertFalse(observed["empty"])

    def test_incomplete_or_unavailable_manager_state_fails_closed(self):
        unit = engines.deterministic_process_unit("owner", "p/t", "g")
        with mock.patch.object(engines, "_systemd_unit_properties", return_value={
            "LoadState": "loaded", "ActiveState": "inactive", "ControlGroup": ""
        }), self.assertRaisesRegex(engines.ManagedUnitError, "incomplete"):
            engines.observe_managed_unit(unit)
        with mock.patch.object(
            engines, "_systemd_unit_properties",
            side_effect=engines.CodexContainmentError("cannot inspect Codex containment unit: bus failed"),
        ), self.assertRaisesRegex(engines.ManagedUnitError, "bus failed"):
            engines.observe_managed_unit(unit)

    def test_service_command_scrubs_and_sorts_the_child_environment(self):
        unit = engines.deterministic_process_unit("helper", "p/t/h", "g")
        command = engines.managed_service_command(unit, ["/bin/worker", "arg"], {"Z": "2", "A": "1"})
        split = command.index("--")
        self.assertEqual(command[split + 1:split + 5], [engines.ENV_BIN, "-i", "A=1", "Z=2"])
        self.assertEqual(command[-2:], ["/bin/worker", "arg"])
        self.assertIn("--property=KillMode=control-group", command)

    def test_spawn_requires_prior_empty_and_uses_the_recorded_unit(self):
        record = transition()
        popen = mock.Mock(pid=123)
        with mock.patch.object(engines.subprocess, "Popen", return_value=popen) as launch:
            with self.assertRaisesRegex(engines.PhysicalTransitionError, "prior_stopped"):
                engines.spawn_managed_unit(record, ["/bin/worker"], cwd=_ROOT,
                                           launcher_env={}, child_env={})
            launch.assert_not_called()

        record = engines.advance_physical_transition(
            record, "planned", "prior_stopped", receipt(record, "prior_stopped")
        )
        with mock.patch.object(engines, "observe_managed_unit", return_value={"empty": True}), \
             mock.patch.object(engines.subprocess, "Popen", return_value=popen) as launch:
            result = engines.spawn_managed_unit(
                record, ["/bin/worker"], cwd=_ROOT, launcher_env={"PATH": "/bin"}, child_env={"A": "1"}
            )
        self.assertIs(result, popen)
        argv = launch.call_args.args[0]
        self.assertIn(f"--unit={record['process_unit_id']}", argv)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_spawn_refuses_a_still_populated_deterministic_unit(self):
        record = transition()
        record = engines.advance_physical_transition(
            record, "planned", "prior_stopped", receipt(record, "prior_stopped")
        )
        with mock.patch.object(engines, "observe_managed_unit", return_value={"empty": False}), \
             mock.patch.object(engines.subprocess, "Popen") as launch, \
             self.assertRaisesRegex(engines.ManagedUnitError, "not empty"):
            engines.spawn_managed_unit(record, ["/bin/worker"], cwd=_ROOT,
                                       launcher_env={}, child_env={})
        launch.assert_not_called()

    def test_stop_returns_only_a_proven_empty_observation(self):
        unit = engines.deterministic_process_unit("owner", "p/t", "g")
        empty = {"process_unit_id": unit, "empty": True}
        with mock.patch.object(engines, "_stop_codex_unit") as stop, \
             mock.patch.object(engines, "observe_managed_unit", return_value=empty):
            self.assertEqual(engines.stop_managed_unit(unit), empty)
        stop.assert_called_once_with(unit, 5.0)

        with mock.patch.object(engines, "_stop_codex_unit"), \
             mock.patch.object(engines, "observe_managed_unit", return_value={"empty": False}), \
             self.assertRaisesRegex(engines.ManagedUnitError, "remained populated"):
            engines.stop_managed_unit(unit)

    def test_stop_translates_legacy_containment_error_without_suppressing_it(self):
        unit = engines.deterministic_process_unit("owner", "p/t", "g")
        with mock.patch.object(
            engines, "_stop_codex_unit", side_effect=engines.CodexContainmentError("unit unknown")
        ), self.assertRaisesRegex(engines.ManagedUnitError, "unit unknown"):
            engines.stop_managed_unit(unit)


class TestDormantBoundary(unittest.TestCase):
    def test_new_physical_api_has_no_runtime_consumer(self):
        names = {
            "new_physical_transition", "advance_physical_transition", "note_physical_transition_error",
            "validate_physical_transition", "reconcile_physical_transition",
            "deterministic_process_unit", "managed_service_command",
            "observe_managed_unit", "spawn_managed_unit", "stop_managed_unit",
        }
        production = Path(__file__).resolve().parent.parent / "altitude"
        uses = []
        for path in sorted(production.rglob("*.py")):
            if path.name == "engines.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in names:
                    uses.append(f"{path.relative_to(production.parent)}:{node.lineno}:{node.id}")
                elif isinstance(node, ast.Attribute) and node.attr in names:
                    uses.append(f"{path.relative_to(production.parent)}:{node.lineno}:{node.attr}")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
                    uses.append(f"{path.relative_to(production.parent)}:{node.lineno}:{node.value}")
        self.assertEqual(uses, [], "Phase 1B.1 must remain dormant: " + ", ".join(uses))


if __name__ == "__main__":
    unittest.main()
