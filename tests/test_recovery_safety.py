"""Recovery faults stop ordinary dispatch without recursively creating work."""
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

_BOOT = Path(tempfile.mkdtemp(prefix="altitude-recovery-safety-bootstrap-"))
os.environ["ALTITUDE_HOME"] = str(_BOOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, improve, monitor, recovery, server, state as S, tasks as T  # noqa: E402


class TestRecoveryFuse(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="altitude-recovery-safety-"))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.patches = ExitStack()
        for obj, name, value in (
            (config, "ROOT", self.root),
            (config, "MONITOR_DIR", self.root / "monitor"),
            (config, "PROJECTS_FILE", self.root / "projects.json"),
            (config, "INCIDENT_INDEX", self.root / "incidents.jsonl"),
            (improve, "FAULTS", self.root / "monitor" / "faults.json"),
        ):
            self.patches.enter_context(mock.patch.object(obj, name, value))
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": str(self.repo), "stacks": []}})

    def tearDown(self):
        self.patches.close()

    def test_system_fault_holds_dispatch_and_creates_no_task(self):
        result = improve.system_fault("test-health", "engine supervision failed", project="altitude")

        self.assertIsNotNone(result)
        self.assertEqual(S.list_tasks("altitude"), [], "fault evidence must not recursively create repair work")
        self.assertEqual(recovery.status()["faults"][-1]["kind"], "test-health")

        ordinary = T.new("altitude", "ordinary work", "S", "request", actor="l3", source="chat")
        self.assertIn("recovery hold", dispatch.wip_hold("altitude", ordinary))

    def test_one_explicit_repair_is_allowed_until_l3_clears(self):
        recovery.hold("runtime ownership is uncertain", kind="ownership", actor="l3")
        repair = T.new("altitude", "repair ownership", "S", "diagnose and repair", actor="l3", source="recovery")

        self.assertEqual(repair["state"], "approved", "recovery delegation bypasses proposal and user approval")
        self.assertIsNone(recovery.dispatch_hold("altitude", repair))
        with self.assertRaisesRegex(T.TransitionError, "already has active repair task"):
            T.new("altitude", "second repair", "S", "another repair", actor="l3", source="recovery")
        with self.assertRaisesRegex(ValueError, "only L3 or Burak"):
            recovery.clear("looks fine", actor="altd")

        cleared = recovery.clear("ownership reconciled\nand the repair PR verified", actor="l3")
        self.assertTrue(cleared["cleared"])
        self.assertIsNone(recovery.status())
        history_text = recovery.clearance_history_path().read_text()
        history = [json.loads(line) for line in history_text.splitlines()]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reason"], "ownership reconciled and the repair PR verified")
        self.assertEqual(history[0]["repair"]["slug"], repair["slug"])
        self.assertEqual(history[0]["faults"], [
            {"count": 1, "incident": None, "kind": "ownership"}
        ])
        self.assertNotIn("runtime ownership is uncertain", history_text,
                         "clearance history keeps evidence references, not raw fault details")
        self.assertEqual(recovery.clearance_history_path().stat().st_mode & 0o777, 0o600)
        ordinary = T.new("altitude", "ordinary after recovery", "S", "request", actor="l3", source="chat")
        self.assertIsNone(recovery.dispatch_hold("altitude", ordinary))

    def test_concurrent_same_kind_faults_coalesce_incident_fyi_and_hold(self):
        barrier = threading.Barrier(2)

        def record(number):
            barrier.wait()
            return improve.system_fault("parallel-health", f"failure {number}", project="altitude")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(record, (1, 2)))

        self.assertEqual(sum(result is not None for result in results), 1)
        fault = S.read_json(improve.FAULTS)["parallel-health"]
        self.assertEqual(fault["count"], 2)
        self.assertIsNotNone(fault["incident"])
        incidents = [row for row in improve.index() if row.get("title") == "system fault: parallel-health"]
        self.assertEqual(len(incidents), 1)
        inbox = [json.loads(line) for line in
                 (config.project_dir("altitude") / "inbox.jsonl").read_text().splitlines()]
        notices = [row for row in inbox if "SYSTEM FAULT [parallel-health]" in row.get("text", "")]
        self.assertEqual(len(notices), 1)
        held = [row for row in recovery.status()["faults"] if row.get("kind") == "parallel-health"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["count"], 2)
        self.assertEqual(held[0]["incident"], fault["incident"])

    def test_hold_publishes_before_a_prequeued_launch_can_cross_the_boundary(self):
        """Adversarial FIFO order: A owns barrier, B queues, then hold queues; B must still refuse."""
        ordinary = {"slug": "ordinary-b", "source": "chat"}
        condition = threading.Condition()
        owner = None
        queue = []
        a_inside = threading.Event()
        release_a = threading.Event()
        b_queued = threading.Event()
        b_spawned = threading.Event()
        b_held = threading.Event()
        published = threading.Event()
        hold_done = threading.Event()
        errors = []

        @contextmanager
        def fifo_launch_lock():
            nonlocal owner
            name = threading.current_thread().name
            with condition:
                queue.append(name)
                if name == "launch-b":
                    b_queued.set()
                while owner is not None or queue[0] != name:
                    condition.wait()
                queue.pop(0)
                owner = name
            try:
                yield
            finally:
                with condition:
                    owner = None
                    condition.notify_all()

        real_write_json = S.write_json

        def observed_write(path, value):
            real_write_json(path, value)
            if path == recovery.hold_path() and value.get("active"):
                published.set()

        def launch_a():
            try:
                with recovery.launch_permission("altitude", ordinary):
                    a_inside.set()
                    release_a.wait(5)
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        def launch_b():
            try:
                with recovery.launch_permission("altitude", ordinary):
                    b_spawned.set()
            except recovery.LaunchHeld:
                b_held.set()
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        def trip_hold():
            try:
                recovery.hold("launch ownership changed", kind="launch-race")
                hold_done.set()
            except BaseException as exc:  # pragma: no cover - reported by assertion below
                errors.append(exc)

        with mock.patch.object(recovery, "_launch_lock", fifo_launch_lock), \
                mock.patch.object(S, "write_json", side_effect=observed_write):
            a = threading.Thread(target=launch_a, name="launch-a")
            b = threading.Thread(target=launch_b, name="launch-b")
            hold = threading.Thread(target=trip_hold, name="fault-hold")
            a.start()
            self.assertTrue(a_inside.wait(2))
            b.start()
            self.assertTrue(b_queued.wait(2), "B must already be queued behind A")
            hold.start()
            self.assertTrue(published.wait(2), "the hold must publish while A still owns the barrier")
            self.assertFalse(hold_done.is_set(), "hold still settles A after publishing")
            self.assertFalse(b_spawned.is_set())
            release_a.set()
            for thread in (a, b, hold):
                thread.join(2)

        self.assertEqual(errors, [])
        self.assertTrue(b_held.is_set())
        self.assertFalse(b_spawned.is_set())
        self.assertTrue(hold_done.is_set())

    def test_engine_launch_guard_covers_popen_but_not_cli_wait(self):
        guard_active = False

        @contextmanager
        def spawn_guard():
            nonlocal guard_active
            guard_active = True
            try:
                yield
            finally:
                guard_active = False

        class FakeProcess:
            returncode = 0

            def communicate(self, timeout):
                self.assert_guard_released()
                return "started", ""

            def assert_guard_released(self):
                if guard_active:
                    raise AssertionError("spawn guard remained held while waiting for the CLI")

        def popen(*args, **kwargs):
            self.assertTrue(guard_active, "Popen is the guarded irreversible boundary")
            return FakeProcess()

        with mock.patch.object(engines.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(engines, "find_agent", return_value=None):
            result = engines.claude_bg("altitude/test", "prompt", cwd=self.repo,
                                       settings=self.root / "settings.json", spawn_guard=spawn_guard())
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "started")

    def test_recovery_task_requires_an_active_hold_and_explicit_actor(self):
        with self.assertRaisesRegex(T.TransitionError, "requires an active recovery hold"):
            T.new("altitude", "unheld repair", "S", "request", actor="l3", source="recovery")
        recovery.hold("manual hold", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "explicitly delegated"):
            T.new("altitude", "agent invented repair", "S", "request", actor="altd", source="recovery")

    def test_recovery_task_rejects_automatic_sizing_before_claim(self):
        recovery.hold("manual hold", actor="l3")
        for cls in ("auto", None):
            with self.subTest(cls=cls), self.assertRaisesRegex(T.TransitionError, "explicit S, M, or L class"):
                T.new("altitude", "unsized repair", cls, "request", actor="l3", source="recovery")
        self.assertIsNone(recovery.status()["repair"])
        self.assertFalse(S.task_dir("altitude", "unsized-repair").exists())

    def test_dispatch_skips_ordinary_work_and_reaches_claimed_repair(self):
        recovery.hold("runtime ownership is uncertain", kind="ownership", actor="l3")
        ordinary = T.new("altitude", "ordinary first", "S", "request", actor="l3", source="chat")
        ordinary["state"] = "approved"
        S.save_task("altitude", ordinary)
        repair = T.new("altitude", "repair second", "S", "request", actor="l3", source="recovery")
        started = []
        with mock.patch.object(dispatch, "run", side_effect=lambda project, slug: started.append(slug) or {
            "dispatch_id": slug, "agent": None
        }), mock.patch.object(engines, "claude_agents", return_value=[]), \
                mock.patch.object(monitor, "quota", return_value={"known": True}), \
                mock.patch.object(monitor, "quota_hold", return_value=None):
            server.dispatch_waiting("altitude")
        self.assertEqual(started, [repair["slug"]])
        project_hold = S.read_json(config.project_dir("altitude") / "hold.json")
        self.assertTrue(project_hold["reason"].startswith("recovery hold"))

    def test_quota_fault_holds_the_same_ordinary_dispatch_attempt(self):
        ordinary = T.new("altitude", "ordinary quota work", "S", "request", actor="l3", source="chat")
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
                mock.patch.object(monitor, "quota", return_value={"known": False}), \
                mock.patch.object(monitor, "quota_hold", return_value=None):
            held = dispatch.wip_hold("altitude", ordinary)
        self.assertIn("recovery hold: quota-unknown", held)

    def test_failed_task_write_rolls_back_the_repair_claim(self):
        recovery.hold("manual hold", actor="l3")
        with mock.patch.object(S, "save_task", side_effect=OSError("state store unavailable")):
            with self.assertRaisesRegex(OSError, "state store unavailable"):
                T.new("altitude", "unwritten repair", "S", "request", actor="l3", source="recovery")
        self.assertIsNone(recovery.status()["repair"])

    def test_fault_during_git_prep_prevents_fresh_worker_launch(self):
        ordinary = T.new("altitude", "racy ordinary launch", "S", "request", actor="l3", source="chat")
        ordinary["state"] = "approved"
        S.save_task("altitude", ordinary)

        def fault_then_return(*args, **kwargs):
            improve.system_fault("fetch-race", "ownership changed during fetch", project="altitude")
            return "a" * 40

        crossed_spawn_boundary = mock.Mock()

        def guarded_launch(*args, spawn_guard, **kwargs):
            with spawn_guard:
                crossed_spawn_boundary()
            return {"stdout": "", "stderr": "", "returncode": 0, "agent": None}

        with mock.patch.object(dispatch, "wip_hold", return_value=None), \
                mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", side_effect=fault_then_return), \
                mock.patch.object(dispatch, "_task_worktree", return_value=self.repo), \
                mock.patch.object(engines, "claude_bg", side_effect=guarded_launch) as launch:
            with self.assertRaisesRegex(T.TransitionError, "recovery hold: fetch-race"):
                dispatch.run("altitude", ordinary["slug"])

        launch.assert_called_once()
        crossed_spawn_boundary.assert_not_called()
        current = S.load_task("altitude", ordinary["slug"])
        self.assertEqual(current["state"], "approved")
        self.assertIsNone(current.get("dispatching"))
        self.assertEqual(recovery.status()["faults"][-1]["kind"], "fetch-race")

    def test_resume_launch_obeys_hold_but_claimed_repair_can_resume(self):
        ordinary = T.new("altitude", "ordinary resume", "S", "request", actor="l3", source="chat")
        ordinary.update({"state": "running", "worktree": str(self.repo), "dispatch_id": "ordinary-resume-1",
                         "session_id": "ordinary-session"})
        S.save_task("altitude", ordinary)
        recovery.hold("manual hold", kind="manual", actor="l3")

        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "claude_resume_bg") as resume:
            with self.assertRaisesRegex(T.TransitionError, "recovery hold: manual"):
                dispatch.resume_session("altitude", ordinary["slug"], "steer")
        resume.assert_not_called()

        repair = T.new("altitude", "claimed repair resume", "S", "request", actor="l3", source="recovery")
        repair.update({"state": "blocked", "worktree": str(self.repo), "dispatch_id": "claimed-repair-resume-1",
                       "session_id": "repair-session"})
        S.save_task("altitude", repair)
        resumed = {"stdout": "", "stderr": "", "returncode": 0}
        live = [{"name": "altitude/claimed-repair-resume-1", "id": "agent-2", "sessionId": "session-2",
                 "state": "working", "startedAt": 2}]
        with mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"), \
                mock.patch.object(engines, "claude_resume_bg", return_value=resumed) as resume, \
                mock.patch.object(engines, "claude_agents", return_value=live):
            result = dispatch.resume_session("altitude", repair["slug"], "continue repair")
        resume.assert_called_once()
        self.assertEqual(result["agent"]["id"], "agent-2")


if __name__ == "__main__":
    unittest.main()
