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

from altitude import config, dispatch, engines, recovery, server, state as S, tasks as T, transcript  # noqa: E402


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
        task, winner = dispatch._claim_owner_preparation(self.project, task)  # noqa: SLF001
        self.assertTrue(winner)
        return dispatch._prepare_owner(self.project, task, worktree=Path(intent["worktree"]),  # noqa: SLF001
                                       branch=intent["branch"], base_sha="a" * 40)

    def completed(self, prompt: str = "exact prompt", thread: str = "thread-1") -> dict:
        task = self.prepared(prompt)
        return self.finish_prepared(task, thread=thread)

    def bound(self, prompt: str = "exact prompt", thread: str = "thread-bound") -> dict:
        task = self.prepared(prompt)
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=unit_observation(unit, empty=True)):
            task, winner = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        self.assertTrue(winner)
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        return dispatch._step_owner(  # noqa: SLF001
            self.project, task, "spawned", "bound",
            {"bound": True, "physical_worker_id": unit, "provider_session_id": thread},
            updates={"state": "running", "session_id": thread, "agent_id": unit,
                     "dispatching": None})

    def finish_prepared(self, task: dict, *, thread: str = "thread-1") -> dict:
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda observed: unit_observation(observed, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        action = {"action": "continue", "continue_reason": "done", "message": "", "helpers": []}
        dispatch._write_owner_marker(self.project, task,  # noqa: SLF001
            dispatch._owner(task, project=self.project, required=True),  # noqa: SLF001
            {"provider_session_id": thread, "action": action, "usage": {}, "error": None})
        with mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda observed: unit_observation(observed, empty=True)):
            return dispatch.reconcile_owner(self.project, task["slug"])

    def test_recovery_change_after_provider_uses_one_canonical_error_and_receipt(self):
        task = self.prepared()
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        unit = physical["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=unit_observation(unit, empty=True)):
            task, _ = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        task = dispatch._step_owner(self.project, task, "prior_stopped", "spawned",  # noqa: SLF001
            {"process_unit_id": unit, "launched": True})
        action = {"action": "continue", "continue_reason": "hostile race", "message": "", "helpers": []}

        def provider(*_args, answer_path, event_spool, **_kwargs):
            with open(event_spool, "a") as stream:
                stream.write(json.dumps({"type": "thread.started", "thread_id": "thread-race"}) + "\n")
                stream.write(json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
            S.atomic_write(Path(answer_path), json.dumps(action))
            recovery.hold("changed after provider", kind="test")
            return {"returncode": 0, "error": None}

        try:
            with mock.patch.object(engines, "codex_exec", side_effect=provider):
                self.assertEqual(dispatch._managed_owner(  # noqa: SLF001
                    self.project, task["slug"], physical["transition_id"]), 1)
            with mock.patch.object(engines, "observe_managed_unit",
                                   return_value=unit_observation(unit, empty=True)):
                terminal = dispatch.reconcile_owner(self.project, task["slug"])
            operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
            error = operation["physical"]["error"]
            self.assertEqual(operation["physical"]["stage"], "failed")
            self.assertEqual(operation["result"]["error"], error)
            self.assertEqual(operation["physical"]["receipts"]["result_observed"], {
                "result_id": "transition:error", "sha256": __import__("hashlib").sha256(error.encode()).hexdigest()})
        finally:
            recovery.clear("race test complete", actor="burak", project=self.project)

    def test_request_is_durable_before_git_or_physical_effect(self):
        request = dispatch._request("m1", "private exact prompt", None, {"engine": "codex"})  # noqa: SLF001
        task, disposition = dispatch._begin_owner_request(self.project, self.task["slug"], request, initial={  # noqa: SLF001
            "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
            "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "cap"})
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual((disposition, operation["preparation"]["stage"], operation["physical"]),
                         ("prepare", "planned", None))
        self.assertEqual(operation["request"]["prompt"], "private exact prompt")

    def test_preparation_stages_are_closed_and_binding_replays_exactly_once(self):
        request = dispatch._request("prepare-stages", "closed preparation", None,  # noqa: SLF001
                                    {"engine": "codex"})
        planned, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        self.assertEqual(disposition, "prepare")

        applying, winner = dispatch._claim_owner_preparation(self.project, planned)  # noqa: SLF001
        stale, stale_winner = dispatch._claim_owner_preparation(self.project, planned)  # noqa: SLF001
        self.assertTrue(winner)
        self.assertFalse(stale_winner)
        self.assertEqual(stale, applying)
        operation = dispatch._owner(applying, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual((operation["preparation"]["stage"], operation["preparation"]["receipt"],
                          operation["physical"]), ("applying", None, None))

        intent = operation["preparation"]["intent"]
        prepared = dispatch._record_owner_prepared(  # noqa: SLF001
            self.project, applying, worktree=Path(intent["worktree"]),
            branch=intent["branch"], base_sha="d" * 40)
        operation = dispatch._owner(prepared, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["preparation"]["stage"], "prepared")
        self.assertIsNotNone(operation["preparation"]["receipt"])
        self.assertIsNone(operation["physical"])

        bound = dispatch._bind_prepared_owner(self.project, prepared)  # noqa: SLF001
        replay = dispatch._bind_prepared_owner(self.project, bound)  # noqa: SLF001
        self.assertEqual(replay, bound)
        self.assertEqual(bound["owner_generation"], 1)
        self.assertEqual(bound["active_operation"]["preparation"]["stage"], "complete")
        self.assertIsNotNone(bound["active_operation"]["physical"])

        malformed = []
        for stage, receipt, physical in (
                ("unknown", None, None),
                ("applying", operation["preparation"]["receipt"], None),
                ("prepared", None, None),
                ("prepared", operation["preparation"]["receipt"], bound["active_operation"]["physical"]),
                ("complete", operation["preparation"]["receipt"], None)):
            hostile = json.loads(json.dumps(prepared))
            hostile["active_operation"]["preparation"].update({"stage": stage, "receipt": receipt})
            hostile["active_operation"]["physical"] = physical
            malformed.append(hostile)
        for hostile in malformed:
            with self.subTest(stage=hostile["active_operation"]["preparation"]["stage"]):
                with self.assertRaisesRegex(T.TransitionError, "preparation|physical"):
                    dispatch._owner(hostile, project=self.project, required=True)  # noqa: SLF001

    def test_cancel_racing_git_claim_cannot_claim_cancelled_before_effect(self):
        request = dispatch._request("cancel-git-race", "race Git deterministically", None,  # noqa: SLF001
                                    {"engine": "codex"})
        planned, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        claimed, release = threading.Event(), threading.Event()
        finished, failures = [], []

        def git_claimant():
            try:
                applying, winner = dispatch._claim_owner_preparation(self.project, planned)  # noqa: SLF001
                if not winner:
                    raise AssertionError("Git claimant lost its deterministic election")
                claimed.set()
                release.wait(5)
                intent = applying["active_operation"]["preparation"]["intent"]
                finished.append(dispatch._prepare_owner(  # noqa: SLF001
                    self.project, applying, worktree=Path(intent["worktree"]),
                    branch=intent["branch"], base_sha="e" * 40))
            except Exception as exc:  # pragma: no cover - asserted in the parent thread
                failures.append(exc)

        worker = threading.Thread(target=git_claimant)
        worker.start()
        self.assertTrue(claimed.wait(5))
        cancelled = dispatch.cancel_owner(self.project, planned["slug"], "operator raced Git")
        in_flight = dispatch._owner(cancelled, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(in_flight["preparation"]["stage"], "applying")
        self.assertIsNone(in_flight["cancelled_before_effect"])
        self.assertIsNotNone(in_flight["stop"])
        self.assertFalse(dispatch.owner_archive_ready(self.project, cancelled))

        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        prepared = dispatch._owner(finished[0], project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(prepared["preparation"]["stage"], "complete")
        self.assertIsNotNone(prepared["physical"]["error"])
        self.assertIsNone(prepared["cancelled_before_effect"])
        with mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda unit: unit_observation(unit, empty=True)), \
             mock.patch.object(engines, "spawn_managed_unit") as launch:
            terminal = dispatch.stop_owner(self.project, planned["slug"], "operator raced Git")
        launch.assert_not_called()
        operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["physical"]["stage"], "failed")
        self.assertTrue(dispatch.owner_archive_ready(self.project, terminal))

    def test_abandoned_applying_retries_fetch_worktree_and_receipt_boundaries(self):
        boundaries = ("fetch", "worktree", "before-prepared")
        for index, boundary in enumerate(boundaries):
            with self.subTest(boundary=boundary):
                task = self.task if index == 0 else T.new(
                    self.project, f"retry {boundary}", "retry exact Git intent", actor="burak",
                    engine="codex")
                request = dispatch._request(f"retry-{boundary}", "retry exact Git intent", None,  # noqa: SLF001
                                            {"engine": "codex"})
                task, _ = dispatch._begin_owner_request(  # noqa: SLF001
                    self.project, task["slug"], request, initial={
                        "dispatch_id": task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                        "engine_model": None, "routing": {"engine": "codex"},
                        "l2_token": "capability"})
                intent = task["active_operation"]["preparation"]["intent"]
                worktree = Path(intent["worktree"])
                real_record = dispatch._record_owner_prepared  # noqa: SLF001
                fetch_effect = ([T.TransitionError("fetch interrupted"), "f" * 40]
                                if boundary == "fetch" else ["f" * 40, "f" * 40])
                worktree_effect = ([T.TransitionError("worktree interrupted"), worktree]
                                   if boundary == "worktree" else [worktree, worktree])
                record_effect = ([T.TransitionError("killed before prepared receipt"), real_record]
                                 if boundary == "before-prepared" else [real_record])

                def record(*args, **kwargs):
                    effect = record_effect.pop(0)
                    if isinstance(effect, Exception):
                        raise effect
                    return effect(*args, **kwargs)

                with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                                       side_effect=fetch_effect) as fetch, \
                     mock.patch.object(dispatch, "_task_worktree",
                                       side_effect=worktree_effect) as create_or_validate, \
                     mock.patch.object(dispatch, "_record_owner_prepared", side_effect=record):
                    with self.assertRaisesRegex(T.TransitionError, "interrupted|killed"):
                        dispatch._settle_owner_preparation(  # noqa: SLF001
                            self.project, task, existing_worktree=False)
                    applying = S.load_task(self.project, task["slug"])
                    self.assertEqual(applying["active_operation"]["preparation"]["stage"], "applying")
                    prepared = dispatch._settle_owner_preparation(  # noqa: SLF001
                        self.project, applying, existing_worktree=False)
                operation = dispatch._owner(prepared, project=self.project, required=True)  # noqa: SLF001
                self.assertEqual(operation["preparation"]["stage"], "prepared")
                self.assertIsNone(operation["physical"])
                self.assertEqual(operation["preparation"]["receipt"]["base_sha"], "f" * 40)
                self.assertLessEqual(fetch.call_count, 2)
                self.assertLessEqual(create_or_validate.call_count, 2)

    def test_concurrent_applying_reconcilers_serialize_one_git_effect_and_receipt(self):
        request = dispatch._request("concurrent-prepare", "one serialized effect", None,  # noqa: SLF001
                                    {"engine": "codex"})
        task, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        task, winner = dispatch._claim_owner_preparation(self.project, task)  # noqa: SLF001
        self.assertTrue(winner)
        intent = task["active_operation"]["preparation"]["intent"]
        entered, release = threading.Event(), threading.Event()
        results, failures, active, maximum = [], [], 0, 0
        counter_lock = threading.Lock()

        def fetch(*_args):
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            entered.set()
            release.wait(5)
            with counter_lock:
                active -= 1
            return "a" * 40

        def reconcile():
            try:
                results.append(dispatch._settle_owner_preparation(  # noqa: SLF001
                    self.project, task, existing_worktree=False))
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                               side_effect=fetch) as fetched, \
             mock.patch.object(dispatch, "_task_worktree",
                               return_value=Path(intent["worktree"])) as worktree, \
             mock.patch.object(dispatch, "_record_owner_prepared",
                               wraps=dispatch._record_owner_prepared) as receipt:  # noqa: SLF001
            first = threading.Thread(target=reconcile)
            second = threading.Thread(target=reconcile)
            first.start()
            self.assertTrue(entered.wait(5))
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        self.assertEqual((fetched.call_count, worktree.call_count, receipt.call_count, maximum),
                         (1, 1, 1, 1))
        self.assertEqual(results[0]["active_operation"]["preparation"],
                         results[1]["active_operation"]["preparation"])

    def test_concurrent_stale_epoch_binders_install_one_physical_generation(self):
        request = dispatch._request("binder-race", "bind exactly once", None,  # noqa: SLF001
                                    {"engine": "codex"})
        task, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        task, winner = dispatch._claim_owner_preparation(self.project, task)  # noqa: SLF001
        self.assertTrue(winner)
        intent = task["active_operation"]["preparation"]["intent"]
        prepared = dispatch._record_owner_prepared(  # noqa: SLF001
            self.project, task, worktree=Path(intent["worktree"]),
            branch=intent["branch"], base_sha="9" * 40)
        recovery.hold("advance binder epoch", kind="test")
        recovery.clear("binder epoch advanced", actor="burak", project=self.project)

        real_save = dispatch._save_owner  # noqa: SLF001
        stale_save_entered, allow_stale_save = threading.Event(), threading.Event()
        results, failures = [], []

        def controlled_save(project, before, replacement, **kwargs):
            if (threading.current_thread().name == "stale-binder"
                    and replacement.get("recovery_epoch") == recovery.clearance_epoch()
                    and replacement["preparation"]["stage"] == "prepared"):
                stale_save_entered.set()
                allow_stale_save.wait(5)
            return real_save(project, before, replacement, **kwargs)

        def bind():
            try:
                results.append(dispatch._bind_prepared_owner(self.project, prepared))  # noqa: SLF001
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        with mock.patch.object(dispatch, "_save_owner", side_effect=controlled_save):
            stale = threading.Thread(target=bind, name="stale-binder")
            winner_thread = threading.Thread(target=bind, name="winning-binder")
            stale.start()
            self.assertTrue(stale_save_entered.wait(5))
            winner_thread.start()
            winner_thread.join(5)
            self.assertFalse(winner_thread.is_alive())
            allow_stale_save.set()
            stale.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        live = S.load_task(self.project, prepared["slug"])
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(live["owner_generation"], 1)
        self.assertEqual(operation["preparation"]["stage"], "complete")
        self.assertEqual(results[0]["active_operation"]["physical"], operation["physical"])
        self.assertEqual(results[1]["active_operation"]["physical"], operation["physical"])
        self.assertEqual(live.get("owner_generations") or [], [])

    def test_dispatch_waiting_recovers_an_initial_applying_preparation(self):
        worktree = Path(dispatch._preparation_intent(self.project, self.task["slug"])["worktree"])  # noqa: SLF001
        common = (
            mock.patch.object(dispatch, "wip_hold", return_value=None),
            mock.patch.object(dispatch.github_intake, "ensure_snapshot", return_value=None),
            mock.patch.object(dispatch, "build_brief", return_value="exact brief"),
            mock.patch.object(dispatch.route, "pick_engine", return_value={"engine": "codex"}),
            mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="b" * 40),
            mock.patch.object(dispatch, "_task_worktree",
                              side_effect=[T.TransitionError("worktree claimant died"), worktree]),
            mock.patch.object(dispatch, "record_dispatch_failure",
                              side_effect=lambda _p, _s, error: dispatch.DispatchFailure(str(error))),
            mock.patch.object(dispatch, "_spawn_owner", side_effect=lambda _p, value: value),
            mock.patch.object(dispatch, "_wait_owner_started", side_effect=lambda _p, value: {
                "dispatch_id": value["dispatch_id"], "agent": {"id": "test-unit"}}),
            mock.patch.object(server.S, "list_tasks",
                              side_effect=lambda project: [S.load_task(project, self.task["slug"])]),
        )
        with common[0], common[1], common[2], common[3], common[4], common[5], \
             common[6], common[7], common[8], common[9]:
            with self.assertRaisesRegex(dispatch.DispatchFailure, "claimant died"):
                dispatch.run(self.project, self.task["slug"])
            applying = S.load_task(self.project, self.task["slug"])
            self.assertEqual(applying["active_operation"]["preparation"]["stage"], "applying")
            server.dispatch_waiting(self.project)
        recovered = dispatch._owner(  # noqa: SLF001
            S.load_task(self.project, self.task["slug"]), project=self.project, required=True)
        self.assertEqual(recovered["preparation"]["stage"], "complete")
        self.assertIsNotNone(recovered["physical"])

    def test_full_run_cancellation_during_applying_settles_without_provider_launch(self):
        intent = dispatch._preparation_intent(self.project, self.task["slug"])  # noqa: SLF001
        entered, release, failures = threading.Event(), threading.Event(), []

        def worktree(*_args):
            entered.set()
            release.wait(5)
            return Path(intent["worktree"])

        def run_owner():
            try:
                dispatch.run(self.project, self.task["slug"])
            except Exception as exc:  # expected failed terminal after cancellation
                failures.append(exc)

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.github_intake, "ensure_snapshot", return_value=None), \
             mock.patch.object(dispatch, "build_brief", return_value="exact brief"), \
             mock.patch.object(dispatch.route, "pick_engine", return_value={"engine": "codex"}), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="c" * 40), \
             mock.patch.object(dispatch, "_task_worktree", side_effect=worktree), \
             mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda unit: unit_observation(unit, empty=True)), \
             mock.patch.object(engines, "spawn_managed_unit") as launch:
            worker = threading.Thread(target=run_owner)
            worker.start()
            self.assertTrue(entered.wait(5))
            stopped = dispatch.cancel_owner(self.project, self.task["slug"], "cancel full run")
            self.assertEqual(stopped["active_operation"]["preparation"]["stage"], "applying")
            self.assertFalse(dispatch.owner_archive_ready(self.project, stopped))
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        launch.assert_not_called()
        self.assertEqual(len(failures), 1)
        terminal = S.load_task(self.project, self.task["slug"])
        operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["physical"]["stage"], "failed")
        self.assertEqual(operation["result"]["error"], operation["physical"]["error"])
        self.assertTrue(dispatch.owner_archive_ready(self.project, terminal))

    def test_background_continuation_replays_every_prelaunch_boundary_once(self):
        for index, stage in enumerate(("planned", "applying", "prepared", "complete")):
            with self.subTest(stage=stage):
                task = self.task if index == 0 else T.new(
                    self.project, f"continue {stage}", "continue exact owner", actor="burak",
                    engine="codex")
                request = dispatch._request(f"continue-{stage}", "continue exact owner", None,  # noqa: SLF001
                                            {"engine": "codex"})
                task, _ = dispatch._begin_owner_request(  # noqa: SLF001
                    self.project, task["slug"], request, initial={
                        "dispatch_id": task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                        "engine_model": None, "routing": {"engine": "codex"},
                        "l2_token": "capability"})
                intent = task["active_operation"]["preparation"]["intent"]
                if stage != "planned":
                    task, winner = dispatch._claim_owner_preparation(self.project, task)  # noqa: SLF001
                    self.assertTrue(winner)
                if stage in ("prepared", "complete"):
                    task = dispatch._record_owner_prepared(  # noqa: SLF001
                        self.project, task, worktree=Path(intent["worktree"]),
                        branch=intent["branch"], base_sha="7" * 40)
                if stage == "complete":
                    task = dispatch._bind_prepared_owner(self.project, task)  # noqa: SLF001

                with mock.patch.object(dispatch.S, "list_tasks",
                                       side_effect=lambda _project, slug=task["slug"]:
                                       [S.load_task(self.project, slug)]), \
                     mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                                       return_value="7" * 40), \
                     mock.patch.object(dispatch, "_task_worktree",
                                       return_value=Path(intent["worktree"])), \
                     mock.patch.object(engines, "observe_managed_unit",
                                       side_effect=lambda unit: unit_observation(unit, empty=False)), \
                     mock.patch.object(recovery, "owner_launch_permission", return_value=nullcontext()), \
                     mock.patch.object(engines, "spawn_managed_unit") as spawn:
                    result = dispatch.continue_owners(self.project)
                self.assertEqual(result, [{"slug": task["slug"], "status": "continued"}])
                spawn.assert_called_once()
                live = S.load_task(self.project, task["slug"])
                operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
                self.assertEqual(operation["request"], request)
                self.assertEqual(operation["preparation"]["stage"], "complete")
                self.assertEqual(operation["physical"]["stage"], "spawned")
                self.assertEqual(live["owner_generation"], 1)

    def test_background_continuation_never_relaunches_prior_stopped(self):
        task = self.prepared("ambiguous launch boundary")
        physical = dispatch._owner(task, project=self.project, required=True)["physical"]  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=unit_observation(physical["process_unit_id"], empty=True)):
            task, winner = dispatch._claim_owner_launch(self.project, task)  # noqa: SLF001
        self.assertTrue(winner)
        with mock.patch.object(dispatch.S, "list_tasks", return_value=[task]), \
             mock.patch.object(engines, "observe_managed_unit",
                               return_value=unit_observation(physical["process_unit_id"], empty=True)), \
             mock.patch.object(engines, "spawn_managed_unit") as spawn:
            result = dispatch.continue_owners(self.project)
        spawn.assert_not_called()
        self.assertEqual(result[0]["status"], "held")
        live = S.load_task(self.project, task["slug"])
        self.assertEqual(live["active_operation"]["physical"]["stage"], "prior_stopped")

    def test_background_continuation_finishes_running_direct_steer_at_wip_one(self):
        current = self.bound("first turn", thread="thread-steer")
        old = dispatch._owner(current, project=self.project, required=True)["physical"]  # noqa: SLF001
        successor = dispatch._request("steer-successor", "exact steering", None,  # noqa: SLF001
                                      {"engine": "codex"})
        current, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, current["slug"], successor)
        self.assertEqual(disposition, "stop")
        stopped = {old["process_unit_id"]: False}

        def observe(unit):
            return unit_observation(unit, empty=stopped.get(unit, False))

        def stop(unit):
            stopped[unit] = True
            return unit_observation(unit, empty=True)

        def spawn(physical, *_args, **_kwargs):
            paths = dispatch._owner_paths(self.project, current["slug"], physical)  # noqa: SLF001
            paths["root"].mkdir(parents=True, exist_ok=True)
            S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                           json.dumps({"type": "thread.started", "thread_id": "thread-steer"}) + "\n")

        projects = config.load_projects()
        original_wip = projects[self.project].get("wip")
        projects[self.project]["wip"] = 1
        config.save_projects(projects)
        try:
            with mock.patch.object(dispatch.S, "list_tasks",
                                   side_effect=lambda _project: [S.load_task(self.project, current["slug"])]), \
                 mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                                   return_value=current["base_sha"]), \
                 mock.patch.object(dispatch, "_validate_task_worktree"), \
                 mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
                 mock.patch.object(engines, "stop_managed_unit", side_effect=stop), \
                 mock.patch.object(recovery, "owner_launch_permission", return_value=nullcontext()), \
                 mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn) as launched, \
                 mock.patch.object(server.dispatch, "resume_due", return_value=[]), \
                 mock.patch.object(server.dispatch, "poll", return_value=[]):
                server.reconcile_l2(self.project)
        finally:
            projects = config.load_projects()
            if original_wip is None:
                projects[self.project].pop("wip", None)
            else:
                projects[self.project]["wip"] = original_wip
            config.save_projects(projects)
        launched.assert_called_once()
        live = S.load_task(self.project, current["slug"])
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["request"], successor)
        self.assertEqual(operation["physical"]["stage"], "bound")
        self.assertEqual(len(live["owner_generations"]), 1)
        self.assertEqual(live["owner_generation"], 2)
        self.assertEqual(live["state"], "running")

    def test_planned_successor_cancels_before_git_then_promotes_exactly_once(self):
        first = dispatch._request("planned-first", "never prepare this", None,  # noqa: SLF001
                                  {"engine": "codex"})
        planned, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], first, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        successor = dispatch._request("planned-next", "exact successor", None,  # noqa: SLF001
                                      {"engine": "codex"})
        planned, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, planned["slug"], successor)
        self.assertEqual(disposition, "stop")

        with mock.patch.object(dispatch.S, "list_tasks",
                               side_effect=lambda _project:
                               [S.load_task(self.project, planned["slug"])]), \
             mock.patch.object(dispatch, "OWNER_CONTINUATION_STEP_CAP", 2), \
             mock.patch.object(dispatch, "_promote_successor",
                               wraps=dispatch._promote_successor) as promote, \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch, \
             mock.patch.object(dispatch, "_task_worktree") as worktree, \
             mock.patch.object(engines, "spawn_managed_unit") as spawn:
            result = dispatch.continue_owners(self.project)
        self.assertEqual(result, [{"slug": planned["slug"], "status": "bounded"}])
        promote.assert_called_once_with(self.project, planned["slug"])
        fetch.assert_not_called()
        worktree.assert_not_called()
        spawn.assert_not_called()
        live = S.load_task(self.project, planned["slug"])
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["request"], successor)
        self.assertEqual(operation["preparation"]["stage"], "planned")
        self.assertEqual(live.get("owner_generations") or [], [])
        self.assertEqual(live.get("owner_generation") or 0, 0)

    def test_server_reconcile_l2_continues_before_due_resume_and_poll(self):
        order = []
        item = {"task": {"slug": "ordinary-finished"}, "agent": {}, "action": {}}
        with mock.patch.object(server.dispatch, "continue_owners",
                               side_effect=lambda _project: order.append("continue") or []), \
             mock.patch.object(server.dispatch, "resume_due",
                               side_effect=lambda _project: order.append("resume") or []), \
             mock.patch.object(server.dispatch, "poll",
                               side_effect=lambda _project: order.append("poll") or [item]), \
             mock.patch.object(server, "spawn") as spawned:
            server.reconcile_l2(self.project)
        self.assertEqual(order, ["continue", "resume", "poll"])
        spawned.assert_called_once_with(
            "finished:owner-v2:ordinary-finished", server.on_l2_finished, self.project, item)

    def test_background_continuation_finishes_blocked_resume_and_clears_retry(self):
        terminal = self.completed("blocked old turn", thread="thread-blocked")
        terminal = T.block(self.project, terminal["slug"], "waiting for answer",
                           expected_dispatch_id=terminal["dispatch_id"],
                           expected_session_id=terminal["session_id"],
                           expected_agent_id=terminal["agent_id"])
        message_id, prompt = "blocked-resume-message", "exact blocked answer"
        request = dispatch._request(message_id, prompt, None, {"engine": "codex"})  # noqa: SLF001
        terminal, _ = dispatch._begin_owner_request(self.project, terminal["slug"], request)  # noqa: SLF001
        def pending(live):
            live.update({"resume_after": S.now(), "resume_answer": prompt, "resume_prefix": "",
                         "resume_exact_prompt": True, "resume_message_id": message_id})
        terminal = dispatch._task_owner_update(self.project, terminal["slug"], pending)  # noqa: SLF001
        old_unit = terminal["active_operation"]["preparation"]["prior_unit"]

        def observe(unit):
            return unit_observation(unit, empty=unit == old_unit)

        def spawn(physical, *_args, **_kwargs):
            paths = dispatch._owner_paths(self.project, terminal["slug"], physical)  # noqa: SLF001
            paths["root"].mkdir(parents=True, exist_ok=True)
            S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                           json.dumps({"type": "thread.started", "thread_id": "thread-blocked"}) + "\n")

        with mock.patch.object(dispatch.S, "list_tasks",
                               side_effect=lambda _project: [S.load_task(self.project, terminal["slug"])]), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                               return_value=terminal["base_sha"]), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(recovery, "owner_launch_permission", return_value=nullcontext()), \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn) as launched:
            result = dispatch.continue_owners(self.project)
        self.assertEqual(result[0]["status"], "continued")
        launched.assert_called_once()
        live = S.load_task(self.project, terminal["slug"])
        self.assertEqual(live["state"], "running")
        self.assertNotIn("resume_message_id", live)
        self.assertEqual(live["active_operation"]["request"], request)
        self.assertEqual(live["active_operation"]["physical"]["stage"], "bound")
        resumed = [event for event in S.read_events(self.project, live["slug"])
                   if event.get("kind") == "state" and event.get("to") == "running"][-1]
        self.assertEqual(resumed.get("answer"), prompt)
        self.assertNotEqual(resumed.get("answer"), "durable owner resume continued")

    def test_wait_owner_started_accepts_exact_bound_blocked_resume_but_not_unbound(self):
        bound = self.bound("bound while blocked", thread="thread-bound-wait")
        bound["state"] = "blocked"
        bound["blocked_reason"] = "projection pending"
        S.save_task(self.project, bound)
        unit = bound["active_operation"]["physical"]["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=unit_observation(unit, empty=False)):
            result = dispatch._wait_owner_started(self.project, bound, timeout=.06)  # noqa: SLF001
        self.assertEqual(result["agent"]["sessionId"], "thread-bound-wait")
        self.assertEqual(S.load_task(self.project, bound["slug"])["state"], "blocked")

        unbound = T.new(self.project, "unbound blocked wait", "must not look ready",
                        actor="burak", engine="codex")
        request = dispatch._request("unbound-wait", "must not look ready", None,  # noqa: SLF001
                                    {"engine": "codex"})
        unbound, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, unbound["slug"], request, initial={
                "dispatch_id": unbound["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        unbound["state"] = "blocked"
        S.save_task(self.project, unbound)
        with self.assertRaisesRegex(T.TransitionError, "exact physical owner intent"):
            dispatch._wait_owner_started(self.project, unbound, timeout=.01)  # noqa: SLF001

    def test_wait_owner_started_rejects_same_request_on_replacement_physical_generation(self):
        original = self.bound("same request replacement", thread="thread-same-request")
        original_op = dispatch._owner(original, project=self.project, required=True)  # noqa: SLF001
        old_unit = original_op["physical"]["process_unit_id"]
        replacement = {**original, "owner_generation": original["owner_generation"] + 1}
        physical = dispatch._new_physical(  # noqa: SLF001
            self.project, replacement, original_op, original_op["preparation"])
        physical = engines.advance_physical_transition(
            physical, "planned", "prior_stopped", {
                "previous_process_unit_id": old_unit, "empty": True,
                "observation": unit_observation(old_unit, empty=True)})
        physical = engines.advance_physical_transition(
            physical, "prior_stopped", "spawned", {
                "process_unit_id": physical["process_unit_id"], "launched": True})
        physical = engines.advance_physical_transition(
            physical, "spawned", "bound", {
                "bound": True, "physical_worker_id": physical["process_unit_id"],
                "provider_session_id": "thread-same-request"})
        replacement["active_operation"] = {**original_op, "physical": physical}
        replacement["agent_id"] = physical["process_unit_id"]
        dispatch._owner(replacement, project=self.project, required=True)  # noqa: SLF001

        with mock.patch.object(dispatch, "reconcile_owner", return_value=replacement):
            with self.assertRaisesRegex(T.TransitionError, "physical owner changed before binding"):
                dispatch._wait_owner_started(self.project, original, timeout=.01)  # noqa: SLF001

    def test_real_blocked_resume_returns_after_exact_bind_and_projects_atomically(self):
        terminal = self.completed("old completed turn", thread="thread-real-resume")
        terminal = T.block(self.project, terminal["slug"], "waiting for exact answer",
                           expected_dispatch_id=terminal["dispatch_id"],
                           expected_session_id=terminal["session_id"],
                           expected_agent_id=terminal["agent_id"])
        old_unit = terminal["agent_id"]
        answer = "Use the exact direct path."
        prompt = ("Burak's answer: Use the exact direct path.\n"
                  "Continue from your progress file; finish to *done* and rewrite the report.")

        def observe(unit):
            return unit_observation(unit, empty=unit == old_unit)

        def spawn(physical, *_args, **_kwargs):
            paths = dispatch._owner_paths(self.project, terminal["slug"], physical)  # noqa: SLF001
            paths["root"].mkdir(parents=True, exist_ok=True)
            S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                           json.dumps({"type": "thread.started",
                                       "thread_id": "thread-real-resume"}) + "\n")

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch.github_intake, "ensure_snapshot", return_value=None), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                               return_value=terminal["base_sha"]), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(recovery, "owner_launch_permission", return_value=nullcontext()), \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn) as launched:
            result = dispatch.resume_blocked(
                self.project, terminal["slug"], answer, message_id="real-resume-message",
                expected_state="blocked", expected_dispatch_id=terminal["dispatch_id"],
                expected_session_id=terminal["session_id"], expected_agent_id=terminal["agent_id"])
        launched.assert_called_once()
        self.assertFalse(result["deferred"])
        live = S.load_task(self.project, terminal["slug"])
        self.assertEqual(live["state"], "running")
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(operation["physical"]["stage"], "bound")
        self.assertEqual(operation["request"]["prompt"], prompt)
        state_events = [event for event in S.read_events(self.project, live["slug"])
                        if event.get("kind") == "state" and event.get("to") == "running"]
        self.assertEqual(state_events[-1].get("answer"), prompt)

    def test_blocked_resume_completion_is_one_idempotent_cas_under_concurrency(self):
        task = self.bound("atomic resume", thread="thread-atomic-resume")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        prompt = "exact persisted atomic answer"
        task.update({"state": "blocked", "blocked_reason": "waiting",
                     "resume_after": S.now(), "resume_answer": prompt, "resume_prefix": "",
                     "resume_exact_prompt": True,
                     "resume_message_id": operation["request"]["message_id"]})
        S.save_task(self.project, task)
        snapshot = S.load_task(self.project, task["slug"])
        results, failures = [], []

        def complete():
            try:
                results.append(dispatch._complete_blocked_resume(self.project, snapshot))  # noqa: SLF001
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        workers = [threading.Thread(target=complete) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        live = S.load_task(self.project, task["slug"])
        self.assertEqual(live["state"], "running")
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001
        matching = [event for event in S.read_events(self.project, task["slug"])
                    if event.get("kind") == "state" and event.get("answer") == prompt]
        self.assertEqual(len(matching), 1)

    def test_background_completion_winning_before_normal_reload_is_idempotent(self):
        prompt = "exact persisted racing answer"
        task = self.bound(prompt, thread="thread-background-wins")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        message_id = operation["request"]["message_id"]
        task.update({"state": "blocked", "blocked_reason": "waiting",
                     "resume_answer": prompt, "resume_exact_prompt": True})
        S.save_task(self.project, task)
        captured = []

        def background_wins(project, slug, text, **_kwargs):
            pending = S.load_task(project, slug)
            current = dispatch._owner(pending, project=project, required=True)  # noqa: SLF001
            owner = dispatch._bound_owner_identity(current)  # noqa: SLF001
            captured.append(dispatch._resume_completion_expectation(pending, owner))  # noqa: SLF001
            completed = dispatch._finish_background_resume(project, pending, current)  # noqa: SLF001
            self.assertEqual(completed["state"], "running")
            return {"deferred": False, "agent": {"id": owner["process_unit_id"],
                    "sessionId": owner["provider_session_id"]}, "stdout": "",
                    "_owner_identity": owner}

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch, "resume_session", side_effect=background_wins):
            result = dispatch.resume_blocked(
                self.project, task["slug"], prompt, message_id=message_id,
                expected_state="blocked", expected_dispatch_id=task["dispatch_id"],
                expected_session_id=task["session_id"], expected_agent_id=task["agent_id"])
        self.assertFalse(result["deferred"])
        live = S.load_task(self.project, task["slug"])
        self.assertEqual(live["state"], "running")
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001
        matching = [event for event in S.read_events(self.project, task["slug"])
                    if event.get("kind") == "state" and event.get("answer") == prompt]
        self.assertEqual(len(matching), 1)

        live["resume_answer"] = "partial stale residue"
        S.save_task(self.project, live)
        with self.assertRaisesRegex(T.TransitionError, "ambiguous retry intent"):
            dispatch._complete_blocked_resume(self.project, live, captured[0])  # noqa: SLF001

    def test_running_competing_physical_cannot_satisfy_old_resume_completion(self):
        task = self.bound("old completion", thread="thread-competing-running")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        prompt = "old exact retry"
        task.update({"state": "blocked", "blocked_reason": "waiting", "resume_after": S.now(),
                     "resume_answer": prompt, "resume_prefix": "", "resume_exact_prompt": True,
                     "resume_message_id": operation["request"]["message_id"]})
        S.save_task(self.project, task)
        expectation = dispatch._resume_completion_expectation(  # noqa: SLF001
            task, dispatch._bound_owner_identity(operation))  # noqa: SLF001
        task = dispatch._complete_blocked_resume(self.project, task, expectation)  # noqa: SLF001

        old_unit = operation["physical"]["process_unit_id"]
        replacement = {**task, "owner_generation": task["owner_generation"] + 1}
        physical = dispatch._new_physical(  # noqa: SLF001
            self.project, replacement, operation, operation["preparation"])
        physical = engines.advance_physical_transition(
            physical, "planned", "prior_stopped", {"previous_process_unit_id": old_unit,
                "empty": True, "observation": unit_observation(old_unit, empty=True)})
        physical = engines.advance_physical_transition(
            physical, "prior_stopped", "spawned", {
                "process_unit_id": physical["process_unit_id"], "launched": True})
        physical = engines.advance_physical_transition(
            physical, "spawned", "bound", {"bound": True,
                "physical_worker_id": physical["process_unit_id"],
                "provider_session_id": "thread-competing-running"})
        replacement["active_operation"] = {**operation, "physical": physical}
        replacement["agent_id"] = physical["process_unit_id"]
        S.save_task(self.project, replacement)
        with self.assertRaisesRegex(T.TransitionError, "exact bound blocked resume"):
            dispatch._complete_blocked_resume(  # noqa: SLF001
                self.project, S.load_task(self.project, task["slug"]), expectation)

    def test_completion_failure_then_reblock_cannot_reuse_old_resume_prompt(self):
        task = self.bound("crash-safe resume", thread="thread-crash-resume")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        old_prompt = "old prompt must disappear"
        task.update({"state": "blocked", "blocked_reason": "waiting",
                     "resume_after": S.now(), "resume_answer": old_prompt, "resume_prefix": "",
                     "resume_exact_prompt": True,
                     "resume_message_id": operation["request"]["message_id"]})
        S.save_task(self.project, task)
        snapshot = S.load_task(self.project, task["slug"])
        with mock.patch.object(S, "regen_state_md", side_effect=RuntimeError("crash after CAS")):
            with self.assertRaisesRegex(RuntimeError, "crash after CAS"):
                dispatch._complete_blocked_resume(self.project, snapshot)  # noqa: SLF001
        live = S.load_task(self.project, task["slug"])
        self.assertEqual(live["state"], "running")
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001
        reblocked = T.block(self.project, live["slug"], "a new question",
                            expected_dispatch_id=live["dispatch_id"],
                            expected_session_id=live["session_id"], expected_agent_id=live["agent_id"])
        self.assertEqual(reblocked["state"], "blocked")
        with mock.patch.object(dispatch, "resume_blocked") as resumed:
            self.assertEqual(dispatch.resume_due(self.project), [])
        resumed.assert_not_called()

    def test_background_continuation_obeys_recovery_and_pending_resume_lease_holds(self):
        request = dispatch._request("held-continuation", "do not cross hold", None,  # noqa: SLF001
                                    {"engine": "codex"})
        planned, _ = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        recovery.hold("continuation hold", kind="test")
        try:
            with mock.patch.object(dispatch.S, "list_tasks", return_value=[planned]), \
                 mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch, \
                 mock.patch.object(engines, "spawn_managed_unit") as spawn:
                result = dispatch.continue_owners(self.project)
            self.assertEqual(result[0]["status"], "held")
            self.assertIn("recovery hold", result[0]["reason"])
            fetch.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual(S.load_task(self.project, planned["slug"])["active_operation"]
                             ["preparation"]["stage"], "planned")
        finally:
            recovery.clear("continuation hold test", actor="burak", project=self.project)

        planned = S.load_task(self.project, planned["slug"])
        planned.update({"state": "blocked", "paths": ["src/shared.py"],
                        "resume_after": "9999-01-01T00:00:00+00:00",
                        "resume_answer": "exact held resume", "resume_prefix": "",
                        "resume_exact_prompt": True, "resume_message_id": "held-continuation",
                        "created": "9999-01-01T00:00:00+00:00"})
        S.save_task(self.project, planned)
        older = T.new(self.project, "older pending resume", "older exact request", actor="burak",
                      engine="codex", paths=["src/shared.py"])
        older.update({"state": "blocked", "l2_engine": "codex", "l2_token": "capability",
                      "dispatch_id": older["slug"] + "-1", "resume_after": S.now(),
                      "created": "0001-01-01T00:00:00+00:00"})
        S.save_task(self.project, older)
        with mock.patch.object(dispatch.S, "list_tasks",
                               side_effect=lambda _project: [
                                   S.load_task(self.project, planned["slug"]),
                                   S.load_task(self.project, older["slug"])]), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch, \
             mock.patch.object(engines, "spawn_managed_unit") as spawn:
            result = dispatch.continue_owners(self.project)
        self.assertEqual(result[0]["status"], "held")
        self.assertIn("older-pending-resume", result[0]["reason"])
        fetch.assert_not_called()
        spawn.assert_not_called()

    def test_recovery_hold_reconciles_bound_blocked_resume_before_admission(self):
        task = self.bound("stale bound resume", thread="thread-stale-bound")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        physical, message_id = operation["physical"], operation["request"]["message_id"]
        paths = dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
        paths["root"].mkdir(parents=True, exist_ok=True)
        S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                       json.dumps({"type": "thread.started", "thread_id": "thread-stale-bound"}) + "\n")
        task.update({"state": "blocked", "blocked_reason": "atomic projection interrupted",
                     "resume_after": S.now(), "resume_answer": "exact stale retry",
                     "resume_prefix": "", "resume_exact_prompt": True,
                     "resume_message_id": message_id})
        S.save_task(self.project, task)
        alive = {physical["process_unit_id"]: True}

        def observe(unit):
            return unit_observation(unit, empty=not alive.get(unit, False))

        def stop(unit):
            alive[unit] = False
            return unit_observation(unit, empty=True)

        recovery.hold("stale bound owner", kind="test")
        try:
            with mock.patch.object(dispatch.S, "list_tasks",
                                   side_effect=lambda _project:
                                   [S.load_task(self.project, task["slug"])]), \
                 mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
                 mock.patch.object(engines, "stop_managed_unit", side_effect=stop) as stopped, \
                 mock.patch.object(engines, "spawn_managed_unit") as spawn, \
                 mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch:
                held = dispatch.continue_owners(self.project)
            self.assertEqual(held[0]["status"], "held")
            self.assertIn("recovery hold", held[0]["reason"])
            stopped.assert_called_once_with(physical["process_unit_id"])
            spawn.assert_not_called()
            fetch.assert_not_called()
            stale = S.load_task(self.project, task["slug"])
            stale_op = dispatch._owner(stale, project=self.project, required=True)  # noqa: SLF001
            self.assertEqual((stale["state"], stale_op["physical"]["stage"]),
                             ("blocked", "failed"))
            self.assertIn("resume_message_id", stale)
        finally:
            recovery.clear("stale bound owner reconciled", actor="burak", project=self.project)

        with mock.patch.object(dispatch.S, "list_tasks",
                               side_effect=lambda _project:
                               [S.load_task(self.project, task["slug"])]), \
             mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(engines, "spawn_managed_unit") as spawn:
            surfaced = dispatch.continue_owners(self.project)
        self.assertEqual(surfaced[0]["status"], "continued")
        spawn.assert_not_called()
        live = S.load_task(self.project, task["slug"])
        self.assertEqual(live["state"], "blocked")
        self.assertIn("owner resume failed", live["blocked_reason"])
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001
        with mock.patch.object(dispatch, "resume_blocked") as resumed:
            self.assertEqual(dispatch.resume_due(self.project), [])
        resumed.assert_not_called()

    def test_running_steer_stops_but_defers_successor_effects_under_recovery_hold(self):
        original_project = self.project
        self.project = f"owner-steer-hold-{self.task['slug']}"
        self.addCleanup(setattr, self, "project", original_project)
        projects = config.load_projects()
        projects[self.project] = {"name": self.project, "path": str(self.repo)}
        config.save_projects(projects)
        self.task = T.new(self.project, "running steer under hold", "exact initial request",
                          actor="burak", engine="codex")
        task = self.bound("initial running work", thread="thread-steer-hold")
        operation = dispatch._owner(task, project=self.project, required=True)  # noqa: SLF001
        physical, old_unit = operation["physical"], operation["physical"]["process_unit_id"]
        paths = dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
        paths["root"].mkdir(parents=True, exist_ok=True)
        S.atomic_write(paths["events"], json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                       json.dumps({"type": "thread.started", "thread_id": "thread-steer-hold"}) + "\n")
        alive = {old_unit: True}

        def observe(unit):
            return unit_observation(unit, empty=not alive.get(unit, False))

        def stop(unit):
            alive[unit] = False
            return unit_observation(unit, empty=True)

        recovery.hold("preexisting running steer hold", kind="test")
        try:
            with mock.patch.object(dispatch.github_intake, "ensure_snapshot", return_value=None), \
                 mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
                 mock.patch.object(engines, "stop_managed_unit", side_effect=stop) as stopped, \
                 mock.patch.object(dispatch, "_promote_successor",
                                   wraps=dispatch._promote_successor) as promote, \
                 mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base") as fetch, \
                 mock.patch.object(dispatch, "_validate_task_worktree") as validate, \
                 mock.patch.object(dispatch, "_bind_prepared_owner",
                                   wraps=dispatch._bind_prepared_owner) as bind, \
                 mock.patch.object(engines, "spawn_managed_unit") as spawn:
                result = dispatch.message_l2(
                    self.project, task["slug"], "exact steering after clearance",
                    expected_dispatch_id=task["dispatch_id"],
                    expected_session_id=task["session_id"], expected_engine="codex")
            self.assertTrue(result["deferred"])
            self.assertIn("recovery hold", result["hold"])
            stopped.assert_called_once_with(old_unit)
            promote.assert_not_called()
            fetch.assert_not_called()
            validate.assert_not_called()
            bind.assert_not_called()
            spawn.assert_not_called()
            held = S.load_task(self.project, task["slug"])
            held_op = dispatch._owner(held, project=self.project, required=True)  # noqa: SLF001
            successor = held_op["successor"]
            self.assertEqual((held["state"], held_op["physical"]["stage"]),
                             ("blocked", "failed"))
            self.assertEqual(successor["message_id"], result["message"]["id"])
            self.assertEqual(successor["prompt"], "exact steering after clearance")
            self.assertEqual(held["resume_message_id"], successor["message_id"])
            self.assertEqual(held["resume_answer"], successor["prompt"])
        finally:
            recovery.clear("running steer may continue", actor="burak", project=self.project)

        def spawn(physical, *_args, **_kwargs):
            alive[physical["process_unit_id"]] = True
            spawned_paths = dispatch._owner_paths(self.project, task["slug"], physical)  # noqa: SLF001
            spawned_paths["root"].mkdir(parents=True, exist_ok=True)
            S.atomic_write(
                spawned_paths["events"],
                json.dumps(dispatch._owner_spool_header(physical)) + "\n" +  # noqa: SLF001
                json.dumps({"type": "thread.started", "thread_id": "thread-steer-hold"}) + "\n")

        isolated_projects = {self.project: {"name": self.project, "path": str(self.repo)}}
        with mock.patch.object(config, "load_projects", return_value=isolated_projects), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                               return_value=task["base_sha"]) as fetch, \
             mock.patch.object(dispatch, "_validate_task_worktree") as validate, \
             mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(engines, "stop_managed_unit", side_effect=stop) as stopped, \
             mock.patch.object(dispatch, "_promote_successor",
                               wraps=dispatch._promote_successor) as promote, \
             mock.patch.object(dispatch, "_bind_prepared_owner",
                               wraps=dispatch._bind_prepared_owner) as bind, \
             mock.patch.object(engines, "spawn_managed_unit", side_effect=spawn) as launched:
            continued = dispatch.continue_owners(self.project)
        self.assertEqual(continued[0]["status"], "continued")
        stopped.assert_not_called()
        promote.assert_called_once_with(self.project, task["slug"])
        fetch.assert_called_once()
        validate.assert_called_once()
        bind.assert_called_once()
        launched.assert_called_once()
        live = S.load_task(self.project, task["slug"])
        live_op = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(live["state"], "running")
        self.assertEqual(live_op["request"], successor)
        self.assertEqual(live_op["physical"]["stage"], "bound")
        self.assertTrue(all(key not in live for key in dispatch._BLOCKED_RESUME_FIELDS))  # noqa: SLF001

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

    def test_codex_terminalization_rejects_a_missing_owner_operation(self):
        task = S.load_task(self.project, self.task["slug"])
        task.update({"state": "reported", "l2_engine": "codex", "dispatch_id": "dispatch",
                     "session_id": "thread", "agent_id": "unit", "active_operation": None})
        S.save_task(self.project, task)
        with self.assertRaisesRegex(T.TransitionError, "exact Codex owner operation required"):
            T.done(self.project, task["slug"])
        with self.assertRaisesRegex(T.TransitionError, "exact Codex owner operation required"):
            T.finalize_completion(self.project, task["slug"], expected_dispatch_id="dispatch",
                                  expected_session_id="thread", expected_agent_id="unit")
        with self.assertRaisesRegex(T.TransitionError, "exact terminal-empty Codex owner result"):
            T._archive(self.project, task["slug"])  # noqa: SLF001
        self.assertEqual(S.load_task(self.project, task["slug"])["state"], "reported")

    def test_codex_terminalization_accepts_exact_terminal_empty_owner_evidence(self):
        task = self.completed()
        task["state"] = "reported"
        S.save_task(self.project, task)

        closed = T.done(self.project, task["slug"], digest="verified")

        self.assertEqual(closed["state"], "done")
        self.assertFalse((S.tasks_dir(self.project) / task["slug"]).exists())
        self.assertTrue((S.archive_dir(self.project) / task["slug"]).is_dir())

    def test_reject_planned_owner_records_exact_cancelled_before_effect_receipt_and_archives(self):
        request = dispatch._request("planned-cancel", "never cross an effect", None,  # noqa: SLF001
                                    {"engine": "codex"})
        planned, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, self.task["slug"], request, initial={
                "dispatch_id": self.task["slug"] + "-1", "attempt": 1, "l2_engine": "codex",
                "engine_model": None, "routing": {"engine": "codex"}, "l2_token": "capability"})
        self.assertEqual(disposition, "prepare")
        self.assertEqual(planned["active_operation"]["preparation"]["stage"], "planned")
        cancelled = dispatch.cancel_owner(self.project, planned["slug"], "operator rejected")
        operation = dispatch._owner(cancelled, project=self.project, required=True)  # noqa: SLF001
        receipt = operation["cancelled_before_effect"]
        self.assertEqual((operation["physical"], receipt["status"], receipt["empty"]),
                         (None, "cancelled-before-effect", True))
        self.assertEqual(receipt["sha256"], __import__("hashlib").sha256(
            operation["result"]["error"].encode()).hexdigest())
        self.assertEqual(dispatch.owner_projection(self.project, cancelled)["stage"],
                         "cancelled-before-effect")
        self.assertTrue(dispatch.owner_archive_ready(self.project, cancelled))

        hostile = json.loads(json.dumps(cancelled))
        hostile["active_operation"]["cancelled_before_effect"]["empty"] = False
        with self.assertRaisesRegex(T.TransitionError, "malformed cancelled-before-effect receipt"):
            dispatch._owner(hostile, project=self.project, required=True)  # noqa: SLF001

        rejected = T.reject(self.project, cancelled["slug"], "not needed")
        self.assertEqual(rejected["state"], "rejected")
        self.assertFalse((S.tasks_dir(self.project) / cancelled["slug"]).exists())
        self.assertTrue((S.archive_dir(self.project) / cancelled["slug"]).is_dir())

    def test_owner_generation_history_cap_fails_closed_without_eviction(self):
        terminal = self.completed()
        rows = []
        for index in range(dispatch.OWNER_GENERATION_HISTORY_CAP):
            generation = f"{index + 1:064x}"
            rows.append({"generation": generation, "transition_id": generation,
                         "process_unit_id": engines.deterministic_process_unit(
                             "owner", f"{self.project}/{terminal['slug']}", generation),
                         "message_id": f"message-{index}",
                         "intent_digest": f"{index + 129:064x}",
                         "event_id": f"l2-engine/{generation}/events.jsonl",
                         "event_sha256": "a" * 64,
                         "result_id": f"l2-engine/{generation}/result.json",
                         "result_sha256": "b" * 64, "session_id": None})
        terminal["owner_generations"] = rows
        S.save_task(self.project, terminal)
        request = dispatch._request("beyond-cap", "must not evict", None, {"engine": "codex"})  # noqa: SLF001
        with self.assertRaisesRegex(
                T.TransitionError, "fixed cap 128; PR 1C.5 owner-history compaction"):
            dispatch._begin_owner_request(self.project, terminal["slug"], request)  # noqa: SLF001
        self.assertEqual(S.load_task(self.project, terminal["slug"])["owner_generations"], rows)

    def test_retired_generation_evidence_missing_nonregular_and_tampered_fails_closed(self):
        terminal = self.completed(thread="thread-history")
        request = dispatch._request("history-next", "next generation", None, {"engine": "codex"})  # noqa: SLF001
        live, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, terminal["slug"], request)
        self.assertEqual(disposition, "prepare")
        row = live["owner_generations"][0]
        root = S.task_dir(self.project, live["slug"])
        event_path, result_path = root / row["event_id"], root / row["result_id"]
        event_text, result_text = event_path.read_text(), result_path.read_text()
        self.assertEqual(dispatch.owner_generation_event_paths(self.project, live)[0][1], event_path)

        event_path.unlink()
        with self.assertRaisesRegex(T.TransitionError, "event spool is unavailable"):
            dispatch.owner_generation_event_paths(self.project, live)
        event_path.mkdir()
        with self.assertRaisesRegex(T.TransitionError, "not a private regular file"):
            dispatch.owner_generation_event_paths(self.project, live)
        event_path.rmdir()
        S.atomic_write(event_path, event_text + "{}\n")
        with self.assertRaisesRegex(T.TransitionError, "event spool changed"):
            dispatch.owner_generation_event_paths(self.project, live)
        S.atomic_write(event_path, event_text)

        result = S.read_json(result_path)
        result["intent_digest"] = "0" * 64
        S.write_json(result_path, result)
        with self.assertRaisesRegex(T.TransitionError, "retired owner result changed"):
            dispatch.owner_generation_event_paths(self.project, live)
        S.atomic_write(result_path, result_text)
        self.assertEqual(dispatch.owner_generation_event_paths(self.project, live)[0][1], event_path)

    def test_owner_generation_history_rejects_unbounded_or_noncanonical_rows(self):
        terminal = self.completed(thread="thread-history-shape")
        request = dispatch._request("shape-next", "next generation", None, {"engine": "codex"})  # noqa: SLF001
        live, _ = dispatch._begin_owner_request(self.project, terminal["slug"], request)  # noqa: SLF001
        row = live["owner_generations"][0]
        mutations = {
            "transition": {"transition_id": "f" * 64},
            "process unit": {"process_unit_id": "foreign.service"},
            "event identity": {"event_id": "l2-engine/foreign/events.jsonl"},
            "result identity": {"result_id": "foreign/result.json"},
            "message bytes": {"message_id": "é" * 513},
            "session bytes": {"session_id": "é" * 513},
        }
        for label, changes in mutations.items():
            with self.subTest(label=label):
                hostile = json.loads(json.dumps(live))
                hostile["owner_generations"][0] = {**row, **changes}
                with self.assertRaisesRegex(T.TransitionError, "malformed owner generation history"):
                    dispatch._owner(hostile, project=self.project, required=True)  # noqa: SLF001

    def test_retired_cancelled_generation_validates_exact_error_receipt(self):
        prepared = self.prepared("cancel this generation")
        with mock.patch.object(engines, "observe_managed_unit",
                               side_effect=lambda unit: unit_observation(unit, empty=True)):
            failed = dispatch.cancel_owner(self.project, prepared["slug"], "operator cancelled")
        failed_op = dispatch._owner(failed, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual(failed_op["physical"]["stage"], "failed")
        request = dispatch._request("after-cancel", "replacement", None, {"engine": "codex"})  # noqa: SLF001
        successor, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, failed["slug"], request)
        self.assertEqual(disposition, "prepare")
        retired = successor["owner_generations"][0]
        error = failed_op["result"]["error"]
        self.assertEqual(retired["result_id"], "transition:error")
        self.assertEqual(retired["result_sha256"],
                         __import__("hashlib").sha256(error.encode()).hexdigest())
        paths = dispatch.owner_generation_event_paths(self.project, successor)
        self.assertEqual(len(paths), 1)
        root = transcript.sync(self.project, successor["slug"])
        self.assertTrue(transcript.validate_bundle(root)["valid"])

    def test_active_terminal_event_spool_is_required_and_bound_before_archive_or_retirement(self):
        terminal = self.completed(thread="thread-bound-event")
        operation = dispatch._owner(terminal, project=self.project, required=True)  # noqa: SLF001
        event_path = dispatch._owner_paths(  # noqa: SLF001
            self.project, terminal["slug"], operation["physical"])["events"]
        original = event_path.read_text()
        request = dispatch._request("after-evidence", "must preserve evidence", None,  # noqa: SLF001
                                    {"engine": "codex"})

        event_path.unlink()
        self.assertFalse(dispatch.owner_archive_ready(self.project, terminal))
        with self.assertRaisesRegex(T.TransitionError, "exact terminal-empty Codex owner result"):
            T._archive(self.project, terminal["slug"])  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "event spool is unavailable"):
            dispatch._begin_owner_request(self.project, terminal["slug"], request)  # noqa: SLF001

        S.atomic_write(event_path, original + "{}\n")
        self.assertFalse(dispatch.owner_archive_ready(self.project, terminal))
        with self.assertRaisesRegex(T.TransitionError, "exact terminal-empty Codex owner result"):
            T._archive(self.project, terminal["slug"])  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "event spool changed"):
            dispatch._begin_owner_request(self.project, terminal["slug"], request)  # noqa: SLF001

    def test_two_retired_generations_same_session_have_distinct_native_evidence(self):
        first = self.completed(thread="thread-shared")
        request = dispatch._request("shared-next", "same provider thread", None,  # noqa: SLF001
                                    {"engine": "codex"})
        second, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, first["slug"], request)
        self.assertEqual(disposition, "prepare")
        intent = second["active_operation"]["preparation"]["intent"]
        second, winner = dispatch._claim_owner_preparation(self.project, second)  # noqa: SLF001
        self.assertTrue(winner)
        second = dispatch._prepare_owner(  # noqa: SLF001
            self.project, second, worktree=Path(intent["worktree"]),
            branch=intent["branch"], base_sha="b" * 40)
        second = self.finish_prepared(second, thread="thread-shared")
        third_request = dispatch._request("shared-third", "retire both generations", None,  # noqa: SLF001
                                          {"engine": "codex"})
        third, disposition = dispatch._begin_owner_request(  # noqa: SLF001
            self.project, second["slug"], third_request)
        self.assertEqual(disposition, "prepare")
        self.assertEqual(len(third["owner_generations"]), 2)
        root = transcript.sync(self.project, third["slug"])
        self.assertIsNotNone(root)
        manifest = S.read_json(root / "manifest.json")
        native = manifest["native"]
        self.assertEqual([entry["session_id"] for entry in native],
                         ["thread-shared", "thread-shared"])
        self.assertEqual(len({entry["source_id"] for entry in native}), 2)
        self.assertEqual(len({entry["path"] for entry in native}), 2)
        self.assertTrue(transcript.validate_bundle(root)["valid"])

    def test_resume_recovery_fence_failure_defers_exact_request_after_hold_clears(self):
        terminal = self.completed(thread="thread-resume")
        prompt = "exact unchanged retry"
        real_bind = dispatch._bind_prepared_owner  # noqa: SLF001

        def bind_while_fence_changes(*args, **kwargs):
            recovery.hold("preparation race", kind="test")
            try:
                return real_bind(*args, **kwargs)
            except dispatch.OwnerRecoveryFenceError:
                recovery.clear("hold cleared before handler", actor="burak", project=self.project)
                raise

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch, "_issue_resume_prompt", return_value=(prompt, None)), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="c" * 40), \
             mock.patch.object(dispatch, "_validate_task_worktree"), \
             mock.patch.object(dispatch, "_bind_prepared_owner", side_effect=bind_while_fence_changes), \
             mock.patch.object(recovery, "dispatch_hold", return_value=None) as current_hold:
            result = dispatch._resume_owner(  # noqa: SLF001
                self.project, terminal["slug"], prompt, message_id="resume-message",
                expected_state="running", expected_session_id="thread-resume")
        current_hold.assert_called_once()
        self.assertTrue(result["deferred"])
        live = S.load_task(self.project, terminal["slug"])
        operation = dispatch._owner(live, project=self.project, required=True)  # noqa: SLF001
        self.assertEqual((live["state"], live["resume_answer"], live["resume_message_id"]),
                         ("blocked", prompt, "resume-message"))
        self.assertEqual(operation["preparation"]["stage"], "prepared")
        self.assertEqual(operation["request"],
                         dispatch._request("resume-message", prompt, None, {"engine": "codex"}))  # noqa: SLF001

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch, "_issue_resume_prompt", return_value=(prompt, None)), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base",
                               side_effect=AssertionError("prepared retry reran Git")) as fetch, \
             mock.patch.object(dispatch, "_spawn_owner", side_effect=lambda _project, value: value), \
             mock.patch.object(dispatch, "_wait_owner_started", return_value={"deferred": False}):
            retried = dispatch._resume_owner(  # noqa: SLF001
                self.project, terminal["slug"], prompt, message_id="resume-message",
                expected_state="blocked", expected_session_id="thread-resume")
        fetch.assert_not_called()
        self.assertFalse(retried["deferred"])
        rebound = dispatch._owner(  # noqa: SLF001
            S.load_task(self.project, terminal["slug"]), project=self.project, required=True)
        self.assertEqual(rebound["preparation"]["stage"], "complete")
        self.assertIsNotNone(rebound["physical"])
        self.assertEqual(rebound["recovery_epoch"], recovery.clearance_epoch())

    def test_resume_retry_recovers_applying_without_original_claimant(self):
        terminal = self.completed(thread="thread-applying-retry")
        prompt = "same resume after claimant exit"
        validations = [T.TransitionError("resume claimant exited"), None]

        def validate(*_args, **_kwargs):
            failure = validations.pop(0)
            if failure:
                raise failure

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
             mock.patch.object(dispatch, "_issue_resume_prompt", return_value=(prompt, None)), \
             mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="d" * 40) as fetch, \
             mock.patch.object(dispatch, "_validate_task_worktree", side_effect=validate) as worktree, \
             mock.patch.object(dispatch, "_spawn_owner", side_effect=lambda _project, value: value), \
             mock.patch.object(dispatch, "_wait_owner_started", return_value={"deferred": False}):
            with self.assertRaisesRegex(T.TransitionError, "resume claimant exited"):
                dispatch._resume_owner(  # noqa: SLF001
                    self.project, terminal["slug"], prompt, message_id="resume-applying",
                    expected_state="running", expected_session_id="thread-applying-retry")
            applying = S.load_task(self.project, terminal["slug"])
            self.assertEqual(applying["active_operation"]["preparation"]["stage"], "applying")
            retried = dispatch._resume_owner(  # noqa: SLF001
                self.project, terminal["slug"], prompt, message_id="resume-applying",
                expected_state="running", expected_session_id="thread-applying-retry")
        self.assertFalse(retried["deferred"])
        self.assertEqual((fetch.call_count, worktree.call_count), (2, 2))
        recovered = dispatch._owner(  # noqa: SLF001
            S.load_task(self.project, terminal["slug"]), project=self.project, required=True)
        self.assertEqual(recovered["preparation"]["stage"], "complete")
        self.assertIsNotNone(recovered["physical"])

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
