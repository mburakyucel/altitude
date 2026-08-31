"""Recovery faults stop ordinary dispatch without recursively creating work."""
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
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

        cleared = recovery.clear("ownership reconciled and the repair PR verified", actor="l3")
        self.assertTrue(cleared["cleared"])
        self.assertIsNone(recovery.status())
        ordinary = T.new("altitude", "ordinary after recovery", "S", "request", actor="l3", source="chat")
        self.assertIsNone(recovery.dispatch_hold("altitude", ordinary))

    def test_recovery_task_requires_an_active_hold_and_explicit_actor(self):
        with self.assertRaisesRegex(T.TransitionError, "requires an active recovery hold"):
            T.new("altitude", "unheld repair", "S", "request", actor="l3", source="recovery")
        recovery.hold("manual hold", actor="l3")
        with self.assertRaisesRegex(T.TransitionError, "explicitly delegated"):
            T.new("altitude", "agent invented repair", "S", "request", actor="altd", source="recovery")

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


if __name__ == "__main__":
    unittest.main()
