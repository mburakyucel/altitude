"""Crash, contention, context, and result tests for dormant Phase 1B.4 primitives."""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import signal
import subprocess
import tempfile
import time
import unittest
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

_IMPORT_ROOT = tempfile.mkdtemp(prefix="altitude-helper-physical-import-")
os.environ["ALTITUDE_HOME"] = _IMPORT_ROOT

from altitude import config, engines, l1, state as S, tasks as T


def _empty(unit: str) -> dict:
    return {"process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
            "sub_state": "dead", "control_group": "", "population": "empty", "empty": True}


def _populated(unit: str) -> dict:
    return {"process_unit_id": unit, "load_state": "loaded", "active_state": "active",
            "sub_state": "running", "control_group": f"/{unit}", "population": "populated", "empty": False}


def _result(summary: str = "finished") -> dict:
    return {"provider_session_id": "codex-thread-1", "status": "complete", "summary": summary,
            "error": None, "patch": None, "findings": [], "usage": {"input_tokens": 3}}


def _install_child(root: str, repo: str, parent: dict, request_id: str, brief: str, queue) -> None:
    config.ROOT = Path(root)
    try:
        with mock.patch.object(config, "project_path", return_value=Path(repo)):
            rec = l1._install_operation(  # noqa: SLF001 - explicit dormant typed seam
                "project", "task", request_id=request_id, parent=parent,
                role="reviewer", brief=brief)
        queue.put(("ok", rec["physical"]["generation"]))
    except Exception as exc:  # noqa: BLE001 - transmit exact contention disposition
        queue.put(("error", str(exc)))


def _marker_then_kill(root: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    l1._write_result_marker("project", "task", name, parent, _result())  # noqa: SLF001
    os.kill(os.getpid(), signal.SIGKILL)


def _marker_child(root: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    S._held_locks.stack = []  # noqa: SLF001 - emulate the fresh lock state of the exec'd unit
    l1._write_result_marker("project", "task", name, parent, _result())  # noqa: SLF001


def _launch_parent_then_kill(root: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    def launch(_record):
        child = multiprocessing.get_context("spawn").Process(target=_marker_child, args=(root, parent, name))
        child.start()
        os.kill(os.getpid(), signal.SIGKILL)
    with mock.patch.object(engines, "observe_managed_unit",
                           side_effect=lambda unit: _empty(unit)):
        l1._spawn_and_record("project", "task", name, parent, launch)  # noqa: SLF001


def _spawn_child(root: str, repo: str, parent: dict, name: str, effects: str, queue) -> None:
    config.ROOT = Path(root)
    try:
        def launch(_record):
            (Path(effects) / str(os.getpid())).mkdir()
            return {"process_unit_id": _record["physical"]["process_unit_id"], "launched": True}
        with mock.patch.object(config, "project_path", return_value=Path(repo)), \
             mock.patch.object(engines, "observe_managed_unit", side_effect=lambda unit: _empty(unit)):
            record = l1._spawn_and_record("project", "task", name, parent, launch)  # noqa: SLF001
        queue.put(("ok", record["physical"]["stage"]))
    except Exception as exc:  # noqa: BLE001 - transmit exact contention disposition
        queue.put(("error", str(exc)))


def _spawn_then_kill(root: str, repo: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    def die(_record):
        os.kill(os.getpid(), signal.SIGKILL)
    with mock.patch.object(config, "project_path", return_value=Path(repo)), \
         mock.patch.object(engines, "observe_managed_unit", side_effect=lambda unit: _empty(unit)):
        l1._spawn_and_record("project", "task", name, parent, die)  # noqa: SLF001


def _worktree_then_kill(root: str, repo: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    real_save = l1._save  # noqa: SLF001

    def kill_before_receipt(project, slug, candidate):
        if candidate["preparation"]["stage"] == "worktree_ready":
            os.kill(os.getpid(), signal.SIGKILL)
        return real_save(project, slug, candidate)

    with mock.patch.object(config, "project_path", return_value=Path(repo)), \
         mock.patch.object(l1, "_save", side_effect=kill_before_receipt):
        l1._prepare_worktree("project", "task", name, parent)  # noqa: SLF001


def _prompt_then_kill(root: str, parent: dict, name: str) -> None:
    config.ROOT = Path(root)
    real_save = l1._save  # noqa: SLF001

    def kill_before_receipt(project, slug, candidate):
        if candidate["preparation"]["stage"] == "ready":
            os.kill(os.getpid(), signal.SIGKILL)
        return real_save(project, slug, candidate)

    with mock.patch.object(l1, "_save", side_effect=kill_before_receipt):
        l1._prepare_prompt("project", "task", name, parent)  # noqa: SLF001


class TestManagedHelperFoundation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-helper-physical-")
        self.root = Path(self.temp.name)
        self.state_root = self.root / "state"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        (self.repo / "base.txt").write_text("base\n")
        subprocess.run(["git", "add", "base.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@t",
                        "commit", "-q", "-m", "base"], cwd=self.repo, check=True)
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                                  capture_output=True, text=True).stdout.strip()
        self.root_patch = mock.patch.object(config, "ROOT", self.state_root)
        self.project_patch = mock.patch.object(config, "project_path", return_value=self.repo)
        self.root_patch.start()
        self.project_patch.start()
        self.parent = self._parent()

    def tearDown(self):
        self.project_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def _parent(self, **changes) -> dict:
        owner_result = {
            "version": 1, "provider": "codex", "project": "project", "slug": "task",
            "owner_generation": 17, "transition_id": "owner-transition-17",
            "generation": "owner-physical-generation-17",
            "process_unit_id": "altitude-owner-17.service", "message_id": "owner-message-17",
            "intent_digest": "a" * 64, "result_id": "owner-result-17", "result_sha256": "b" * 64,
            "terminal_stage": "complete", "empty_receipt_sha256": "c" * 64,
            "recovery_episode_id": "episode-8", "recovery_permit_revision": 4,
        }
        owner_result.update(changes.pop("owner_result", {}))
        parent = {
            "version": 1, "owner_result": owner_result, "task_scope": ["altitude", "tests"],
            "worktree": str(self.repo), "branch": "main", "base_sha": self.sha,
        }
        parent.update(changes)
        return parent

    def _install(self, request: str = "1" * 64, *, role="reviewer", brief="review it", paths=None,
                 parent=None) -> dict:
        return l1._install_operation(  # noqa: SLF001 - explicit dormant typed seam
            "project", "task", request_id=request, parent=parent or self.parent,
            role=role, brief=brief, paths=paths)

    def _ready(self, request: str = "1" * 64, *, role="reviewer", paths=None) -> dict:
        record = self._install(request, role=role, paths=paths)
        record = l1._prepare_worktree("project", "task", record["name"], self.parent)  # noqa: SLF001
        return l1._prepare_prompt("project", "task", record["name"], self.parent)  # noqa: SLF001

    def _prior_stopped(self, request: str = "1" * 64, *, role="reviewer", paths=None) -> dict:
        record = self._ready(request, role=role, paths=paths)
        with l1._locks("project", "task"):  # noqa: SLF001 - install exact crash-stage fixture
            return l1._advance("project", "task", record, "planned", "prior_stopped",  # noqa: SLF001
                               {"previous_process_unit_id": None, "empty": True})

    def _spawned(self, request: str = "1" * 64) -> dict:
        record = self._ready(request)
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            return l1._spawn_and_record(  # noqa: SLF001
                "project", "task", record["name"], self.parent,
                lambda current: {"process_unit_id": current["physical"]["process_unit_id"],
                                 "launched": True})

    def test_intent_is_durable_before_worktree_and_prompt_effects(self):
        record = self._install(role="implementer", paths=["altitude/l1.py"])
        observed = []
        real_git = l1._git

        def inspect_git(cwd, *args):
            observed.append(l1.load("project", "task", record["name"])["preparation"]["stage"])
            return real_git(cwd, *args)

        with mock.patch.object(l1, "_git", side_effect=inspect_git):
            worktree_ready = l1._prepare_worktree("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(observed[0], "planned")
        self.assertEqual(worktree_ready["preparation"]["stage"], "worktree_ready")
        prompt_path = Path(worktree_ready["preparation"]["prompt_path"])
        self.assertFalse(prompt_path.exists())
        ready = l1._prepare_prompt("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(ready["preparation"]["stage"], "ready")
        self.assertEqual(S.read_json(l1._record_path("project", "task", record["name"]))["preparation"]["stage"],
                         "ready")  # noqa: SLF001
        self.assertIn("Your sublease is: ['altitude/l1.py']", prompt_path.read_text())

    def test_worktree_effect_crash_reconciles_without_branch_recreation(self):
        record = self._install(request="2" * 64, role="implementer", paths=["tests"])
        process = multiprocessing.get_context("fork").Process(
            target=_worktree_then_kill,
            args=(str(self.state_root), str(self.repo), self.parent, record["name"]))
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        self.assertTrue(Path(record["preparation"]["worktree"]).is_dir())
        self.assertEqual(l1.load("project", "task", record["name"])["preparation"]["stage"], "planned")
        repaired = l1._prepare_worktree("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(repaired["preparation"]["stage"], "worktree_ready")
        branches = subprocess.run(["git", "branch", "--list", record["preparation"]["branch"]], cwd=self.repo,
                                  check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(len(branches), 1)

    def test_parent_is_rechecked_before_each_preparation_effect(self):
        record = self._install(request="3" * 64, role="implementer", paths=["tests"])
        stale = self._parent(owner_result={"owner_generation": 18})
        with mock.patch.object(l1, "_git") as git, \
             self.assertRaisesRegex(T.TransitionError, "no longer current"):
            l1._prepare_worktree("project", "task", record["name"], stale)  # noqa: SLF001
        git.assert_not_called()

        ready = {**record, "preparation": {**record["preparation"], "stage": "worktree_ready",
                                            "worktree_status_sha256": hashlib.sha256(b"").hexdigest()}}
        with l1._locks("project", "task"):  # noqa: SLF001
            l1._save("project", "task", ready)  # noqa: SLF001
        with mock.patch.object(S, "atomic_write") as write, \
             self.assertRaisesRegex(T.TransitionError, "no longer current"):
            l1._prepare_prompt("project", "task", record["name"], stale)  # noqa: SLF001
        write.assert_not_called()

    def test_prompt_effect_sigkill_replays_the_same_derived_bytes(self):
        record = self._install(request="c" * 64)
        record = l1._prepare_worktree("project", "task", record["name"], self.parent)  # noqa: SLF001
        process = multiprocessing.get_context("fork").Process(
            target=_prompt_then_kill, args=(str(self.state_root), self.parent, record["name"]))
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        prompt = Path(record["preparation"]["prompt_path"])
        crashed_bytes = prompt.read_bytes()
        self.assertEqual(l1.load("project", "task", record["name"])["preparation"]["stage"], "worktree_ready")
        repaired = l1._prepare_prompt("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(repaired["preparation"]["stage"], "ready")
        self.assertEqual(prompt.read_bytes(), crashed_bytes)

    def test_stable_request_is_idempotent_and_conflicts_never_reuse_old_result(self):
        first = self._install()
        self.assertEqual(self._install(), first)
        with self.assertRaisesRegex(T.TransitionError, "conflicting helper request id"):
            self._install(brief="different request")
        other = self._install(request="4" * 64)
        self.assertNotEqual(first["physical"]["generation"], other["physical"]["generation"])
        self.assertNotIn("attempt", first["name"])

    def test_full_parent_context_and_subscope_are_closed(self):
        bad = (
            self._parent(owner_result={"project": "other"}),
            self._parent(owner_result={"slug": "other"}),
            self._parent(owner_result={"provider": "claude"}),
            self._parent(owner_result={"owner_generation": True}),
            self._parent(owner_result={"terminal_stage": "failed"}),
            self._parent(owner_result={"recovery_episode_id": None, "recovery_permit_revision": 5}),
            self._parent(owner_result={"transition_id": ""}),
            self._parent(owner_result={"intent_digest": "x"}),
            self._parent(owner_result={"result_id": ""}),
            self._parent(owner_result={"result_sha256": "x"}),
            self._parent(owner_result={"empty_receipt_sha256": "x"}),
        )
        for index, parent in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(T.TransitionError):
                self._install(request=f"{index + 10:064x}", parent=parent)
        extra = deepcopy(self.parent)
        extra["owner_result"]["attempt_id"] = "legacy-parallel-generation"
        legacy = {"version": 1, "project": "project", "slug": "task", "state": "settling",
                  "task_scope": ["tests"], "generation": {}}
        for parent in (extra, legacy, self._parent(worktree="relative")):
            with self.assertRaises(T.TransitionError):
                self._install(request=hashlib.sha256(repr(parent).encode()).hexdigest(), parent=parent)
        with self.assertRaisesRegex(T.TransitionError, "outside its parent"):
            self._install(request="5" * 64, role="implementer", paths=["web"])
        empty_parent = self._parent(task_scope=[])
        self.assertEqual(self._install(request="5" * 63 + "a", parent=empty_parent)["paths"], [])
        with self.assertRaisesRegex(T.TransitionError, "parent task scope must not be empty"):
            self._install(request="5" * 63 + "b", role="implementer", paths=["tests"],
                          parent=empty_parent)

        hostile = (["/tests"], ["../tests"], ["tests/../altitude"], ["./tests"],
                   ["tests//unit"], ["tests\\unit"], ["tests\0unit"], ["tests/"], [""],
                   ["tests/line\nbreak"], ["tests/\x1fcontrol"], ["-tests/unit"],
                   ["tests/-unit"], ["C:/tests"], [".git/config"], [".GIT/config"],
                   ["vendor/.git/config"])
        for index, scope in enumerate(hostile):
            with self.subTest(parent_scope=scope), self.assertRaises(T.TransitionError):
                self._install(request=f"{index + 30:064x}", parent=self._parent(task_scope=scope))
            with self.subTest(helper_scope=scope), self.assertRaises(T.TransitionError):
                self._install(request=f"{index + 50:064x}", role="implementer", paths=scope)

        record = self._install(request="d" * 64)
        escaped = deepcopy(record)
        escaped["preparation"]["prompt_path"] = "/tmp/escaped.prompt.md"
        with self.assertRaisesRegex(T.TransitionError, "task/worktree"):
            l1._validate_record(escaped, "project", "task")  # noqa: SLF001
        transplanted = deepcopy(record)
        transplanted["physical"] = self._install(request="e" * 64)["physical"]
        with self.assertRaises(T.TransitionError):
            l1._validate_record(transplanted, "project", "task")  # noqa: SLF001

    def test_real_multiprocess_same_request_dedupes_and_conflict_refuses(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        request = "6" * 64
        workers = [context.Process(target=_install_child, args=(str(self.state_root), str(self.repo), self.parent,
                                                                request, "same", queue)) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        results = [queue.get(timeout=2) for _ in workers]
        self.assertEqual({kind for kind, _ in results}, {"ok"})
        self.assertEqual(len({value for _, value in results}), 1)
        self.assertEqual(len(l1.list_runs("project", "task")), 1)

        queue = context.Queue()
        conflict = "7" * 64
        workers = [context.Process(target=_install_child, args=(str(self.state_root), str(self.repo), self.parent,
                                                                conflict, brief, queue))
                   for brief in ("first", "second")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        dispositions = sorted(queue.get(timeout=2)[0] for _ in workers)
        self.assertEqual(dispositions, ["error", "ok"])

    def test_real_multiprocess_spawn_claim_performs_exactly_one_effect(self):
        record = self._ready(request="7" * 63 + "a")
        effects = self.root / "spawn-effects"
        effects.mkdir()
        context, queue = multiprocessing.get_context("fork"), multiprocessing.get_context("fork").Queue()
        workers = [context.Process(
            target=_spawn_child,
            args=(str(self.state_root), str(self.repo), self.parent, record["name"], str(effects), queue),
        ) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual([queue.get(timeout=2)[0] for _ in workers], ["ok"] * 4)
        self.assertEqual(len(list(effects.iterdir())), 1)
        self.assertEqual(l1.load("project", "task", record["name"])["physical"]["stage"], "spawned")

    def test_sigkill_after_prior_stop_claim_refuses_relaunch(self):
        record = self._ready(request="7" * 63 + "b")
        process = multiprocessing.get_context("fork").Process(
            target=_spawn_then_kill,
            args=(str(self.state_root), str(self.repo), self.parent, record["name"]),
        )
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        self.assertEqual(l1.load("project", "task", record["name"])["physical"]["stage"],
                         "prior_stopped")
        launch = mock.Mock(return_value={
            "process_unit_id": record["physical"]["process_unit_id"], "launched": True,
        })
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])), \
             self.assertRaisesRegex(engines.PhysicalTransitionError, "launch may have run"):
            l1._spawn_and_record(  # noqa: SLF001
                "project", "task", record["name"], self.parent, launch)
        launch.assert_not_called()

    def test_spawn_claim_rejects_a_non_exact_unit_receipt_without_relaunching(self):
        record = self._ready(request="7" * 63 + "c")
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])), \
             self.assertRaisesRegex(T.TransitionError, "exact process-unit receipt"):
            l1._spawn_and_record(  # noqa: SLF001
                "project", "task", record["name"], self.parent,
                lambda _record: {"process_unit_id": "wrong.service", "launched": True})
        self.assertEqual(l1.load("project", "task", record["name"])["physical"]["stage"],
                         "prior_stopped")

    def test_sigkill_after_marker_before_aggregate_is_forward_repaired_without_provider_repeat(self):
        record = self._spawned(request="8" * 64)
        process = multiprocessing.get_context("fork").Process(
            target=_marker_then_kill, args=(str(self.state_root), self.parent, record["name"]))
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        persisted = l1.load("project", "task", record["name"])
        self.assertEqual(persisted["physical"]["stage"], "spawned")
        self.assertIsNone(persisted["result"])
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            repaired = l1._reconcile("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(repaired["physical"]["stage"], "complete")
        self.assertEqual(repaired["result"]["summary"], "finished")

    def test_parent_sigkill_before_spawn_receipt_repairs_exact_child_marker_without_relaunch(self):
        record = self._ready(request="f" * 64)
        process = multiprocessing.get_context("fork").Process(
            target=_launch_parent_then_kill, args=(str(self.state_root), self.parent, record["name"]))
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        marker_path = l1._marker_path("project", "task", record)  # noqa: SLF001
        for _ in range(200):
            if marker_path.exists():
                break
            time.sleep(.01)
        self.assertTrue(marker_path.exists(), "detached helper result did not survive its killed parent")
        self.assertEqual(l1.load("project", "task", record["name"])["physical"]["stage"], "prior_stopped")
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            repaired = l1._reconcile("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(repaired["physical"]["stage"], "complete")
        self.assertEqual(repaired["result"]["summary"], "finished")

    def test_prior_stopped_populated_unit_records_spawned_but_empty_without_marker_is_uncertain(self):
        populated = self._prior_stopped(request="0" * 64)
        unit = populated["physical"]["process_unit_id"]
        with mock.patch.object(engines, "observe_managed_unit", return_value=_populated(unit)):
            repaired = l1._reconcile("project", "task", populated["name"], self.parent)  # noqa: SLF001
        self.assertEqual(repaired["physical"]["stage"], "spawned")
        self.assertTrue(repaired["physical"]["receipts"]["spawned"]["launched"])

        ambiguous = self._prior_stopped(request="2" * 64)
        before = deepcopy(ambiguous)
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(ambiguous["physical"]["process_unit_id"])), \
             self.assertRaisesRegex(engines.PhysicalTransitionError, "launch may have run"):
            l1._reconcile("project", "task", ambiguous["name"], self.parent)  # noqa: SLF001
        self.assertEqual(l1.load("project", "task", ambiguous["name"]), before)

    def test_final_spawn_operation_rechecks_prompt_git_context_scope_and_parent(self):
        record = self._ready(request="3" * 64, role="implementer", paths=["tests"])
        launch = mock.Mock(return_value={
            "process_unit_id": record["physical"]["process_unit_id"], "launched": True,
        })
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            spawned = l1._spawn_and_record(  # noqa: SLF001
                "project", "task", record["name"], self.parent, launch)
            l1._spawn_and_record("project", "task", record["name"], self.parent, launch)  # noqa: SLF001
        self.assertEqual(spawned["physical"]["stage"], "spawned")
        launch.assert_called_once_with(mock.ANY)

        record = self._ready(request="3" * 63 + "a", role="implementer", paths=["tests"])
        prompt = Path(record["preparation"]["prompt_path"])
        prompt.write_text(prompt.read_text() + "tampered")
        with mock.patch.object(engines, "observe_managed_unit") as observe, \
             self.assertRaisesRegex(T.TransitionError, "prompt bytes changed"):
            l1._spawn_and_record("project", "task", record["name"], self.parent, launch)  # noqa: SLF001
        observe.assert_not_called()

        record = self._ready(request="3" * 63 + "b", role="implementer", paths=["tests"])
        helper_worktree = Path(record["preparation"]["worktree"])
        (helper_worktree / "uncommitted.txt").write_text("not ready\n")
        with self.assertRaisesRegex(T.TransitionError, "worktree changed"):
            l1._spawn_and_record("project", "task", record["name"], self.parent, launch)  # noqa: SLF001

        record = self._ready(request="3" * 63 + "c")
        stale = self._parent(owner_result={"owner_generation": 18})
        with self.assertRaisesRegex(T.TransitionError, "no longer current"):
            l1._spawn_and_record("project", "task", record["name"], stale, launch)  # noqa: SLF001

        reviewer = self._ready(request="4" * 64)
        (self.repo / "review-race.txt").write_text("changed after ready\n")
        with self.assertRaisesRegex(T.TransitionError, "worktree changed"):
            l1._spawn_and_record("project", "task", reviewer["name"], self.parent, launch)  # noqa: SLF001

    def test_result_marker_is_generation_keyed_idempotent_and_conflict_refuses(self):
        record = self._spawned(request="9" * 64)
        marker = l1._write_result_marker("project", "task", record["name"], self.parent, _result())  # noqa: SLF001
        self.assertEqual(l1._write_result_marker(  # noqa: SLF001
            "project", "task", record["name"], self.parent, _result()), marker)
        self.assertIn(record["physical"]["generation"],
                      l1._marker_path("project", "task", record).name)  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "conflicting helper result marker"):
            l1._write_result_marker("project", "task", record["name"], self.parent, _result("changed"))  # noqa: SLF001

    def test_oversized_persisted_marker_fails_before_unbounded_decode(self):
        record = self._spawned(request="5" * 64)
        path = l1._marker_path("project", "task", record)  # noqa: SLF001
        path.write_bytes(b"{" + b" " * l1._MARKER_CAP)  # noqa: SLF001
        with self.assertRaisesRegex(T.TransitionError, "bounded evidence"):
            l1._reconcile("project", "task", record["name"], self.parent)  # noqa: SLF001

    def test_all_record_reads_are_bounded_and_corruption_is_never_overwritten(self):
        name, path = f"helper-{'6' * 64}", l1._record_path("project", "task", f"helper-{'6' * 64}")  # noqa: SLF001
        self.assertIsNone(l1.load("project", "task", name))
        path.parent.mkdir(parents=True, exist_ok=True)
        cases = ((b"", "empty"), (b"{broken", "invalid JSON"),
                 (b"{" + b" " * l1._RECORD_CAP, "bounded evidence"))  # noqa: SLF001
        for raw, message in cases:
            with self.subTest(message=message):
                path.write_bytes(raw)
                before = path.read_bytes()
                with self.assertRaisesRegex(T.TransitionError, message):
                    l1.load("project", "task", name)
                with self.assertRaisesRegex(T.TransitionError, message):
                    l1.list_runs("project", "task")
                with self.assertRaisesRegex(T.TransitionError, message):
                    l1.require_helpers_terminal("project", "task")
                self.assertEqual(path.read_bytes(), before)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(T.TransitionError, "unreadable"):
            l1.load("project", "task", name)
        self.assertNotIn("S.read_json(", Path(l1.__file__).read_text())

    def test_terminal_result_is_exactly_bound_and_cleanup_rejects_mutation_or_absence(self):
        record = self._spawned(request="6" * 63 + "a")
        l1._write_result_marker("project", "task", record["name"], self.parent, _result())  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            terminal = l1._reconcile("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(terminal["physical"]["stage"], "complete")
        path = l1._record_path("project", "task", record["name"])  # noqa: SLF001
        mutations = []
        changed_patch = deepcopy(terminal)
        changed_patch["result"]["patch"] = "mutated after receipt"
        mutations.append((changed_patch, "exact physical receipt"))
        missing = deepcopy(terminal)
        missing["result"] = None
        mutations.append((missing, "missing for its physical receipt"))
        mismatch = deepcopy(terminal)
        mismatch["physical"]["receipts"]["result_observed"]["sha256"] = "d" * 64
        mutations.append((mismatch, "exact physical receipt"))
        wrong_status = deepcopy(terminal)
        wrong_status["result"]["status"] = "failed"
        wrong_status["result"]["error"] = "not the physical error"
        wrong_status["physical"]["receipts"]["result_observed"]["sha256"] = \
            hashlib.sha256(S._canonical_json(wrong_status["result"])).hexdigest()  # noqa: SLF001
        mutations.append((wrong_status, "status/error"))
        for value, message in mutations:
            with self.subTest(message=message):
                S.write_json(path, value)
                with self.assertRaisesRegex(T.TransitionError, message):
                    l1.load("project", "task", record["name"])
                with self.assertRaisesRegex(T.TransitionError, message):
                    l1.cleanup_evidence("project", "task")
        S.write_json(path, terminal)

    def test_stale_parent_stops_and_discards_without_accepting_outcome(self):
        record = self._spawned(request="a" * 64)
        stale = self._parent(owner_result={"owner_generation": 18, "recovery_permit_revision": 5})
        marker = l1._write_result_marker("project", "task", record["name"], stale, _result("must not leak"))  # noqa: SLF001
        self.assertEqual(marker["disposition"], "discarded")
        stopped = [False]
        def observe(unit):
            return _empty(unit) if stopped[0] else _populated(unit)
        def stop_unit(unit):
            stopped[0] = True
            return _empty(unit)
        with mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(engines, "stop_managed_unit", side_effect=stop_unit) as stop:
            failed = l1._reconcile("project", "task", record["name"], stale)  # noqa: SLF001
        stop.assert_called_once_with(record["physical"]["process_unit_id"])
        self.assertEqual(failed["physical"]["stage"], "failed")
        self.assertTrue(failed["result"]["error"].startswith("parent OwnerGeneration changed"))
        self.assertNotIn("must not leak", str(failed["result"]))

    def test_stale_prior_stopped_exact_marker_proves_launch_before_discard(self):
        record = self._prior_stopped(request="1" * 63 + "9")
        stale = self._parent(owner_result={"owner_generation": 18})
        marker = l1._write_result_marker(  # noqa: SLF001
            "project", "task", record["name"], stale, _result("stale prior-stop result"))
        self.assertEqual(marker["disposition"], "discarded")
        unit, stopped = record["physical"]["process_unit_id"], [False]
        def observe(current):
            return _empty(current) if stopped[0] else _populated(current)
        def stop_unit(current):
            stopped[0] = True
            return _empty(current)
        with mock.patch.object(engines, "observe_managed_unit", side_effect=observe), \
             mock.patch.object(engines, "stop_managed_unit", side_effect=stop_unit) as stop:
            failed = l1._reconcile("project", "task", record["name"], stale)  # noqa: SLF001
        stop.assert_called_once_with(unit)
        self.assertEqual(failed["physical"]["stage"], "failed")
        self.assertTrue(failed["physical"]["receipts"]["spawned"]["launched"])
        self.assertTrue(failed["physical"]["receipts"]["bound"]["bound"])
        self.assertNotIn("stale prior-stop result", str(failed["result"]))

    def test_stale_prior_stopped_empty_without_marker_remains_ownership_uncertain(self):
        record = self._prior_stopped(request="1" * 63 + "a")
        stale = self._parent(owner_result={"owner_generation": 18})
        before = deepcopy(record)
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])), \
             mock.patch.object(engines, "stop_managed_unit") as stop, \
             self.assertRaisesRegex(engines.PhysicalTransitionError, "launch may have run"):
            l1._reconcile("project", "task", record["name"], stale)  # noqa: SLF001
        stop.assert_not_called()
        self.assertEqual(l1.load("project", "task", record["name"]), before)

    def test_stale_spawned_empty_without_marker_remains_ownership_uncertain(self):
        record = self._spawned(request="1" * 63 + "b")
        stale = self._parent(owner_result={"owner_generation": 18})
        before = deepcopy(record)
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])), \
             mock.patch.object(engines, "stop_managed_unit") as stop, \
             self.assertRaisesRegex(engines.PhysicalTransitionError, "stale helper spawned"):
            l1._reconcile("project", "task", record["name"], stale)  # noqa: SLF001
        stop.assert_not_called()
        self.assertEqual(l1.load("project", "task", record["name"]), before)

    def test_lock_order_is_project_then_task_then_operation(self):
        order = []

        def project_lock(_project):
            order.append(S.LockLevel.PROJECT)
            return nullcontext()

        def ordered(_path, level):
            order.append(level)
            return nullcontext()

        with mock.patch.object(S, "project_lock", side_effect=project_lock), \
             mock.patch.object(S, "ordered_file_lock", side_effect=ordered):
            self._install(request="b" * 64)
        self.assertEqual(order, [S.LockLevel.PROJECT, S.LockLevel.TASK, S.LockLevel.OPERATION])

    def test_task_terminal_and_archive_refuse_until_helper_is_terminal_empty(self):
        record = self._spawned(request="4" * 64)
        task = {
            "slug": "task", "title": "task", "state": "running", "created": S.now(), "updated": S.now(),
            "l2_engine": "codex", "dispatch_id": "dispatch-1", "session_id": "session-1",
            "agent_id": "owner-1", "l2_token": "private",
        }
        S.save_task("project", task)
        for terminal in (
            lambda: T.reject("project", "task", "cancel"),
            lambda: T.done("project", "task", actor="l3"),
            lambda: T._archive("project", "task"),  # noqa: SLF001 - direct archive gate
        ):
            with self.subTest(terminal=terminal), self.assertRaisesRegex(T.TransitionError, "not terminal"):
                terminal()
            self.assertEqual(S.load_task("project", "task")["state"], "running")

        l1._write_result_marker("project", "task", record["name"], self.parent, _result())  # noqa: SLF001
        with mock.patch.object(engines, "observe_managed_unit",
                               return_value=_empty(record["physical"]["process_unit_id"])):
            terminal_record = l1._reconcile("project", "task", record["name"], self.parent)  # noqa: SLF001
        self.assertEqual(terminal_record["physical"]["stage"], "complete")
        rejected = T.reject("project", "task", "cancel")
        self.assertEqual(rejected["state"], "rejected")
        self.assertTrue((S.archive_dir("project") / "task").is_dir())


if __name__ == "__main__":
    unittest.main()
