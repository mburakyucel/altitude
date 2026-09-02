"""TaskRecord is the sole Codex L2 process owner and every effect is replay-safe."""
from __future__ import annotations
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

os.environ.setdefault("ALTITUDE_HOME", tempfile.mkdtemp(prefix="altitude-owner-v2-"))

from altitude import config, dispatch, engines, recovery, state as S, tasks as T  # noqa: E402


def unit_observation(unit: str, *, empty: bool) -> dict:
    return {"process_unit_id": unit, "load_state": "not-found" if empty else "loaded",
            "active_state": "inactive" if empty else "active", "sub_state": "dead" if empty else "running",
            "control_group": "", "population": "empty" if empty else "populated", "empty": empty}


def claim_in_process(project: str, slug: str, output) -> None:
    task = S.load_task(project, slug)
    unit = dispatch._owner(task, project=project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
    with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
        _task, winner = dispatch._claim_owner_launch(project, task)  # noqa: SLF001
    output.put(winner)


class TestL2OwnerOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        cls.repo = Path(tempfile.mkdtemp(prefix="altitude-owner-repo-"))
        cls.project = "owner-v2"
        projects = config.load_projects()
        projects[cls.project] = {"name": cls.project, "path": str(cls.repo)}
        config.save_projects(projects)

    def setUp(self):
        self.task = T.new(self.project, self._testMethodName, "Implement the exact request.", actor="burak",
                          engine="codex")

    def prepared(self, prompt: str = "exact prompt") -> dict:
        slug = self.task["slug"]
        request = dispatch._request("message-" + slug, prompt, None, {"engine": "codex"})  # noqa: SLF001
        task, disposition = dispatch._begin_owner_request(self.project, slug, request, initial={  # noqa: SLF001
            "dispatch_id": f"{slug}-1", "attempt": 1, "l2_engine": "codex", "engine_model": None,
            "routing": {"engine": "codex"}, "l2_token": "capability"})
        self.assertEqual(disposition, "prepare")
        intent = task["active_operation"]["preparation"]["intent"]
        return dispatch._prepare_owner(self.project, task, worktree=Path(intent["worktree"]),  # noqa: SLF001
                                       branch=intent["branch"], base_sha="a" * 40)

    def completed(self, prompt: str = "exact prompt", thread: str = "thread-1") -> dict:
        task = self.prepared(prompt)
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        action = {"action": "continue", "continue_reason": "done", "message": "", "helpers": []}
        dispatch._write_owner_marker(self.project, task,  # noqa: SLF001
            dispatch._owner(task, project=self.project, required=True),  # noqa: SLF001
            {"provider_session_id": thread, "action": action, "usage": {}, "error": None})
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            return dispatch.reconcile_owner(self.project, task["slug"])

    def test_request_is_durable_before_git_or_physical_effect(self):
        request = dispatch._request("m1", "private exact prompt", None, {"engine": "codex"})  # noqa: SLF001
        task, disposition = dispatch._begin_owner_request(self.project, self.task["slug"], request, initial={  # noqa: SLF001
            "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
            "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "cap"})
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual((disposition, operation["preparation"]["stage"], operation["physical"]),
                         ("prepare", "planned", None))
        self.assertEqual(operation["request"]["prompt"], "private exact prompt")

    def test_unknown_legacy_codex_ownership_cannot_be_relaunched(self):
        task = S.load_task(self.project, self.task["slug"])
        task["l2_engine"] = "codex"
        S.save_task(self.project, task)
        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(engines, "spawn_managed_unit") as spawned:
            with self.assertRaisesRegex(T.TransitionError, "unknown legacy Codex ownership"):
                dispatch.run(self.project, task["slug"])
        spawned.assert_not_called()

    def test_only_planned_to_prior_stopped_cas_winner_may_spawn(self):
        task = self.prepared()
        unit = dispatch._owner(task, project=self.project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            won, first = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
            observed, second = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(dispatch._owner(won, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "prior_stopped")
        self.assertEqual(dispatch._owner(observed, project=self.project, required=True)["physical"],  # noqa: SLF001
                         dispatch._owner(won, project=self.project, required=True)["physical"])  # noqa: SLF001

    def test_cross_process_launch_election_has_exactly_one_winner(self):
        task = self.prepared()
        context = multiprocessing.get_context("fork")
        output = context.Queue()
        processes = [context.Process(target=claim_in_process, args=(self.project, task["slug"], output))
                     for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(output.get(timeout=1) for _ in processes), [False, True])

    def test_spawn_crosses_effect_only_after_winning_receipt(self):
        task = self.prepared()
        unit = dispatch._owner(task, project=self.project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
        seen = []
        def spawn(physical, *_args, **_kwargs):
            seen.append(S.load_task(self.project, task["slug"])["active_operation"]["physical"]["stage"])
            return mock.Mock()
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)), \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn) as launched, \
             mock.patch("altitude.recovery.owner_launch_permission", return_value=nullcontext()):
            moved = dispatch._spawn_owner(self.project, task)  # noqa: SLF001
        launched.assert_called_once()
        self.assertEqual(seen, ["prior_stopped"])
        self.assertEqual(dispatch._owner(moved, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "spawned")

    def test_concurrent_spawn_receipt_never_makes_launcher_stop_the_elected_unit(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]

        def spawn(_physical, *_args, **_kwargs):
            live = S.load_task(self.project, task["slug"])
            dispatch._step_owner(self.project, live, "prior_stopped", "spawned",  # noqa: SLF001
                {"process_unit_id": unit, "launched": True})
            return mock.Mock()

        with mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda _unit: unit_observation(unit, empty=False)), \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn), \
             mock.patch.object(engines, "stop_managed_unit") as stopped, \
             mock.patch("altitude.recovery.owner_launch_permission", return_value=nullcontext()):
            moved = dispatch._spawn_owner(self.project, task)  # noqa: SLF001
        stopped.assert_not_called()
        self.assertEqual(dispatch._owner(moved, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "spawned")

    def test_result_written_before_spawn_receipt_forward_repairs_without_relaunch(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, won = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        self.assertTrue(won)
        action = {"action": "continue", "continue_reason": "recovered", "message": "", "helpers": []}
        dispatch._write_owner_marker(self.project, task,  # noqa: SLF001
            dispatch._owner(task, project=self.project, required=True),  # noqa: SLF001
            {"provider_session_id": "thread-early", "action": action, "usage": {}, "error": None})
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)), \
             mock.patch.object(engines, "spawn_managed_unit") as spawned:
            terminal = dispatch.reconcile_owner(self.project, task["slug"])
        spawned.assert_not_called()
        self.assertEqual(dispatch._owner(terminal, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "complete")
        self.assertEqual(terminal["state"], "running")
        proof = dispatch.require_owner_result(self.project, terminal)["owner_result"]
        self.assertEqual(proof["result_receipt"]["result_id"],
                         f"l2-engine/{physical['generation']}/result.json")
        self.assertTrue(proof["empty_receipt"]["empty"])

    def test_cancel_before_spawn_records_failure_and_never_launches(self):
        task = self.prepared()
        unit = dispatch._owner(task, project=self.project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
        seen = []

        def observe(_unit):
            live = S.load_task(self.project, task["slug"])
            seen.append(bool(live["active_operation"].get("stop")))
            return unit_observation(unit, empty=True)

        with mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(engines, "spawn_managed_unit") as spawned:
            terminal = dispatch.cancel_owner(self.project, task["slug"], "rejected")
        spawned.assert_not_called()
        self.assertTrue(all(seen), "the stop intent must precede every physical observation")
        self.assertEqual(dispatch._owner(terminal, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "failed")

    def test_strict_bounded_spool_binds_then_terminalizes_exact_result(self):
        task = self.prepared()
        unit = dispatch._owner(task, project=self.project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        op = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        paths = dispatch._owner_paths(self.project, task["slug"], op["physical"])  # noqa: SLF001
        paths["root"].mkdir(parents=True, exist_ok=True)
        header = dispatch._owner_spool_header(op["physical"])  # noqa: SLF001
        S.atomic_write(paths["events"], "\n".join(json.dumps(row, sort_keys=True) for row in (
            header, {"type": "thread.started", "thread_id": "thread-1"})) + "\n")
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=False)):
            bound = dispatch.reconcile_owner(self.project, task["slug"])
        self.assertEqual((bound["state"], bound["session_id"], bound["agent_id"]),
                         ("running", "thread-1", unit))
        action = {"action": "continue", "continue_reason": "more", "message": "", "helpers": []}
        dispatch._write_owner_marker(self.project, bound,  # noqa: SLF001
            dispatch._owner(bound, project=self.project, required=True),  # noqa: SLF001
            {"provider_session_id": "thread-1", "action": action, "usage": {}, "error": None})
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            terminal = dispatch.reconcile_owner(self.project, task["slug"])
        operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["physical"]["stage"], "complete")
        self.assertEqual(dispatch.require_owner_result(self.project, terminal)["worker_result"], action)

    def test_context_tamper_and_public_projection_fail_closed(self):
        task = self.prepared("secret prompt")
        task["active_operation"]["request"]["prompt"] = "changed"
        with self.assertRaisesRegex(T.TransitionError, "malformed Codex owner request"):
            dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001

    def test_managed_child_observes_cancel_before_provider_and_settles_forward(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        successor = dispatch._request("cancel-next", "later", None, {"engine": "codex"})  # noqa: SLF001
        task, disposition = dispatch._begin_owner_request(self.project, task["slug"], successor)  # noqa: SLF001
        self.assertEqual(disposition, "stop")
        with mock.patch.object(engines, "codex_exec") as provider, \
             mock.patch.object(recovery, "owner_state_is_current", return_value=True):
            self.assertEqual(dispatch._managed_owner(  # noqa: SLF001
                self.project, task["slug"], physical["transition_id"]), 4)
        provider.assert_not_called()
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            terminal = dispatch.reconcile_owner(self.project, task["slug"])
        self.assertEqual(dispatch._owner(terminal, project=self.project, required=True)["physical"]["stage"],  # noqa: SLF001
                         "failed")

    def test_forged_header_cannot_bind_and_bound_thread_cannot_change_at_result(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        op = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        paths = dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
        paths["root"].mkdir(parents=True, exist_ok=True)
        forged = {**dispatch._owner_spool_header(physical), "message_id": "foreign"}  # noqa: SLF001
        S.atomic_write(paths["events"], json.dumps(forged) + "\n" +
                       json.dumps({"type": "thread.started", "thread_id": "thread-a"}) + "\n")
        with self.assertRaisesRegex(T.TransitionError, "header does not match"):
            dispatch._owner_thread(self.project, task, op)  # noqa: SLF001
        S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                       json.dumps({"type": "thread.started", "thread_id": "thread-a"}) + "\n")
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=False)):
            bound = dispatch.reconcile_owner(self.project, task["slug"])
        dispatch._write_owner_marker(self.project, bound,  # noqa: SLF001
            dispatch._owner(bound, project=self.project, required=True),  # noqa: SLF001
            {"provider_session_id": "thread-b", "action": {"action": "continue",
             "continue_reason": "forged", "message": "", "helpers": []}, "usage": {}, "error": None})
        with self.assertRaisesRegex(T.TransitionError, "bound provider thread"):
            dispatch._owner_marker(self.project, bound,  # noqa: SLF001
                                   dispatch._owner(bound, project=self.project, required=True))  # noqa: SLF001

    def test_runtime_symlink_deep_marker_and_terminal_tamper_fail_closed(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        family = S.tasks_dir(self.project) / task["slug"] / "l2-engine"
        outside = Path(tempfile.mkdtemp(prefix="owner-outside-"))
        family.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(T.TransitionError, "runtime path escapes"):
            dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
        family.unlink()
        paths = dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
        paths["root"].mkdir(parents=True)
        paths["result"].write_text("[" * 2000 + "]" * 2000)
        with self.assertRaisesRegex(T.TransitionError, "invalid owner result marker"):
            dispatch._owner_marker(self.project, task,  # noqa: SLF001
                                   dispatch._owner(task, project=self.project, required=True))  # noqa: SLF001
        paths["result"].unlink()
        self.task = T.new(self.project, self._testMethodName + " terminal",
                          "Implement the exact request.", actor="burak", engine="codex")
        terminal = self.completed("tamper terminal", "thread-t")
        terminal_op = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
        terminal_paths = dispatch._owner_paths(self.project, terminal["slug"], terminal_op["physical"])  # noqa: SLF001
        marker = S.read_json(terminal_paths["result"])
        marker["result"]["action"]["continue_reason"] = "changed"
        S.write_json(terminal_paths["result"], marker)
        with self.assertRaisesRegex(T.TransitionError, "evidence changed"):
            dispatch.owner_result_snapshot(self.project, terminal)

    def test_owner_error_is_bounded_by_utf8_bytes(self):
        error = dispatch._bounded_owner_error("é" * 400)  # noqa: SLF001
        self.assertLessEqual(len(error.encode("utf-8")), 500)
        self.assertTrue(error.endswith("[truncated]"))

    def test_archive_rename_and_concurrent_claim_never_create_a_ghost_active_task(self):
        slug = self.task["slug"]
        request = dispatch._request("late", "must not run", None, {"engine": "codex"})  # noqa: SLF001
        entered, release, outcome = threading.Event(), threading.Event(), []

        def slow_sync(*_args):
            entered.set()
            release.wait(2)

        def reject():
            T.reject(self.project, slug, "finished")

        def claim():
            try:
                dispatch._begin_owner_request(self.project, slug, request)  # noqa: SLF001
            except Exception as exc:  # exact exception is asserted below
                outcome.append(exc)

        with mock.patch("altitude.transcript.sync", side_effect=slow_sync):
            archiver = threading.Thread(target=reject)
            archiver.start()
            self.assertTrue(entered.wait(1))
            claimant = threading.Thread(target=claim)
            claimant.start()
            time.sleep(.03)
            self.assertTrue(claimant.is_alive(), "claim did not serialize behind archive")
            release.set()
            archiver.join(2); claimant.join(2)
        self.assertEqual(len(outcome), 1)
        self.assertRegex(str(outcome[0]), "active task no longer exists")
        self.assertFalse((S.tasks_dir(self.project) / slug).exists())
        self.assertTrue((S.archive_dir(self.project) / slug).is_dir())

    def test_successor_is_persisted_before_stop_and_cannot_be_replaced(self):
        task = self.prepared("first")
        request = dispatch._request("next", "exact successor", None, {"engine": "codex"})  # noqa: SLF001
        live, disposition = dispatch._begin_owner_request(self.project, task["slug"], request)  # noqa: SLF001
        self.assertEqual(disposition, "stop")
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["successor"], request)
        self.assertIsNotNone(operation["stop"])
        other = dispatch._request("other", "different", None, {"engine": "codex"})  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "different owner successor"):
            dispatch._begin_owner_request(self.project, task["slug"], other)  # noqa: SLF001

    def test_recovery_revision_change_stops_and_discards_stale_generation(self):
        task = self.prepared()
        unit = dispatch._owner(task, project=self.project, required=True)["physical"]["process_unit_id"]  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        state = {"empty": False}
        def observe(_unit):
            return unit_observation(unit, empty=state["empty"])
        def stop(_unit):
            state["empty"] = True
            return unit_observation(unit, empty=True)
        recovery.hold("test stale owner", kind="test")
        try:
            with mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
                 mock.patch.object(engines, "stop_managed_unit", side_effect=stop) as stopped:
                terminal = dispatch.reconcile_owner(self.project, task["slug"])
            stopped.assert_called_once_with(unit)
            operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
            self.assertEqual(operation["physical"]["stage"], "failed")
            with self.assertRaisesRegex(T.TransitionError, "terminal-empty owner result|required"):
                dispatch.owner_result_snapshot(self.project, terminal)
        finally:
            recovery.clear("owner test complete", actor="burak", project=self.project)


if __name__ == "__main__":
    unittest.main()
