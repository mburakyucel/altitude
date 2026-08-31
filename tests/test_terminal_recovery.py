"""Terminal worker stops and the one-time operational-park recovery are fail-safe."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from altitude import config, dispatch, engines, l1, state as S, tasks as T


class RecoveryHome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="altitude-terminal-recovery-")
        root = Path(self.temp.name)
        self.saved = {name: getattr(config, name) for name in
                      ("ROOT", "PROJECTS_FILE", "MONITOR_DIR", "INCIDENT_INDEX", "DIGEST_FILE")}
        config.ROOT = root
        config.PROJECTS_FILE = root / "projects.json"
        config.MONITOR_DIR = root / "monitor"
        config.INCIDENT_INDEX = root / "incidents.jsonl"
        config.DIGEST_FILE = root / "DIGEST.md"
        config.ensure_root()
        config.save_projects({"demo": {"path": str(root), "stacks": ["python"]}})

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(config, name, value)
        self.temp.cleanup()

    def blocked(self, slug: str = "worker", engine: str = "claude") -> dict:
        task = T.new("demo", slug, "S", "request")
        T.auto_approve("demo", task["slug"], "test")
        T.dispatch("demo", task["slug"], dispatch_id=f"{task['slug']}-1",
                   session_id="sid", agent_id="aid", worktree="/tmp/wt", branch="worktree-worker",
                   l2_engine=engine, origin_sha="a" * 40)
        return T.block("demo", task["slug"], "waiting")


class TestTerminalWorkerStops(RecoveryHome):
    def test_terminal_claude_row_needs_no_stop(self):
        task = self.blocked()
        with mock.patch.object(engines, "claude_agents", return_value=[{"id": "aid", "state": "done"}]), \
             mock.patch.object(engines, "claude_stop") as stop:
            T.park("demo", task["slug"], "operator park")
            stop.assert_not_called()
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "parked")

    def test_missing_claude_row_needs_no_stop(self):
        task = self.blocked("missing")
        with mock.patch.object(engines, "claude_agents", return_value=[]), \
             mock.patch.object(engines, "claude_stop") as stop:
            T.park("demo", task["slug"], "operator park")
            stop.assert_not_called()

    def test_live_claude_is_stopped_and_verified_before_terminal_move(self):
        task = self.blocked()
        with mock.patch.object(engines, "claude_agents",
                               side_effect=[[{"id": "aid", "state": "working"}],
                                            [{"id": "aid", "state": "stopped"}]]), \
             mock.patch.object(engines, "claude_stop", return_value="stopped") as stop:
            T.reject("demo", task["slug"], "cancel")
        stop.assert_called_once_with("aid")
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "rejected")

    def test_failed_live_claude_stop_keeps_task_nonterminal(self):
        task = self.blocked()
        with mock.patch.object(engines, "claude_agents",
                               return_value=[{"id": "aid", "state": "working"}]), \
             mock.patch.object(engines, "claude_stop", side_effect=RuntimeError("stop failed")):
            with self.assertRaises(RuntimeError):
                T.park("demo", task["slug"], "cancel")
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "blocked")

    def test_active_l1_stop_failure_keeps_task_nonterminal(self):
        task = self.blocked("l1-active")
        with mock.patch.object(l1, "stop_all", return_value=False) as stop_all:
            with self.assertRaisesRegex(T.TransitionError, "L1 generation"):
                T.park("demo", task["slug"], "cancel")
        stop_all.assert_called_once_with("demo", task["slug"], task["dispatch_id"])
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "blocked")

    def test_active_codex_stop_failure_keeps_task_nonterminal(self):
        task = self.blocked(engine="codex")
        record = {"dispatch_id": task["dispatch_id"], "state": "running"}
        with mock.patch.object(dispatch, "codex_run", return_value=record), \
             mock.patch.object(dispatch, "codex_processes_live", return_value=True), \
             mock.patch.object(dispatch, "stop_codex_worker", return_value=False):
            with self.assertRaises(T.TransitionError):
                T.reject("demo", task["slug"], "cancel")
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "blocked")

    def test_approved_pending_codex_launch_is_cancelled_before_terminal_move(self):
        task = T.new("demo", "pending", "S", "request")
        T.auto_approve("demo", task["slug"], "test")
        with S.project_lock("demo"):
            live = S.load_task("demo", task["slug"])
            live["pending_dispatch"] = {
                "dispatch_id": f"{task['slug']}-1", "generation": "g", "engine": "codex"}
            S.save_task("demo", live)
        record = {"dispatch_id": f"{task['slug']}-1", "generation": "g", "state": "starting"}
        with mock.patch.object(l1, "stop_all", return_value=True), \
             mock.patch.object(dispatch, "codex_run", return_value=record), \
             mock.patch.object(dispatch, "codex_processes_live", return_value=False), \
             mock.patch.object(dispatch, "stop_codex_worker", return_value=True) as stop:
            T.park("demo", task["slug"], "cancel pending launch")
        stop.assert_called_once_with("demo", task["slug"], f"{task['slug']}-1")
        terminal = S.load_task("demo", task["slug"])
        self.assertEqual(terminal["state"], "parked")
        self.assertNotIn("pending_dispatch", terminal)


    def test_failed_record_with_live_process_still_blocks_terminal_move(self):
        task = self.blocked("failed-live", engine="codex")
        record = {"dispatch_id": task["dispatch_id"], "state": "failed",
                  "pid": 123, "pid_start": "exact"}
        with mock.patch.object(dispatch, "codex_run", return_value=record), \
             mock.patch.object(dispatch, "codex_processes_live", return_value=True), \
             mock.patch.object(dispatch, "stop_codex_worker", return_value=False):
            with self.assertRaises(T.TransitionError):
                T.park("demo", task["slug"], "cancel")
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "blocked")


class TestOperationalParkMigration(RecoveryHome):
    def forensic_park(self, slug: str, reason: str, *, trusted: bool = True) -> None:
        with S.project_lock("demo"):
            task = S.load_task("demo", slug)
            previous = task["state"]
            task["state"] = "parked"
            S.save_task("demo", task)
            S.append_event("demo", slug, "state", frm=previous, to="parked",
                           by="forensic-reconciler", reason=reason,
                           forensic_reconcile=trusted, audit_id="recovery-test")

    def test_only_l3_operational_waits_restore_once(self):
        queued = T.new("demo", "queued", "S", "request")
        T.park("demo", queued["slug"], "Queue hold, not a defect: unpark when PR #1 lands.", actor="l3")
        explicit = T.new("demo", "explicit", "S", "request")
        T.park("demo", explicit["slug"], "Burak's park stands: revise after review.", actor="l3")
        approved = T.new("demo", "approved queue", "S", "request")
        T.auto_approve("demo", approved["slug"], "test")
        T.park("demo", approved["slug"], "Queue order, not a problem with the task: unpark when the lease clears.", actor="l3")

        migrated = T.migrate_operational_parks("demo")
        self.assertEqual(set(migrated), {queued["slug"], approved["slug"]})
        self.assertEqual(S.load_task("demo", queued["slug"])["state"], "requested")
        self.assertEqual(S.load_task("demo", approved["slug"])["state"], "approved")
        self.assertEqual(S.load_task("demo", explicit["slug"])["state"], "parked")
        self.assertEqual(T.migrate_operational_parks("demo"), [])

    def test_later_operational_park_is_a_new_generation(self):
        task = T.new("demo", "reparked", "S", "request")
        reason = "Queue hold, not a defect: unpark when PR #1 lands."
        T.park("demo", task["slug"], reason, actor="l3")
        self.assertEqual(T.migrate_operational_parks("demo"), [task["slug"]])
        first_marker = S.read_json(
            config.MONITOR_DIR / f"{T.OPERATIONAL_PARK_MIGRATION}-demo.json")
        first_generation = first_marker["processed_events"][task["slug"]][0]

        T.park("demo", task["slug"], reason, actor="l3")
        self.assertEqual(T.migrate_operational_parks("demo"), [task["slug"]])
        marker = S.read_json(config.MONITOR_DIR / f"{T.OPERATIONAL_PARK_MIGRATION}-demo.json")
        generations = marker["processed_events"][task["slug"]]
        self.assertEqual(len(generations), 2)
        self.assertEqual(generations[0], first_generation)
        self.assertNotEqual(generations[0], generations[1])
        self.assertEqual(len(marker["history"]), 1)
        marker_text = (config.MONITOR_DIR / f"{T.OPERATIONAL_PARK_MIGRATION}-demo.json").read_text()
        self.assertEqual(T.migrate_operational_parks("demo"), [])
        self.assertEqual(
            (config.MONITOR_DIR / f"{T.OPERATIONAL_PARK_MIGRATION}-demo.json").read_text(),
            marker_text,
            "an already handled generation must not churn the marker",
        )

    def test_trusted_forensic_reconcile_park_is_operational(self):
        task = T.new("demo", "forensic", "S", "request")
        T.auto_approve("demo", task["slug"], "test")
        self.forensic_park(
            task["slug"],
            "Queue order, not a problem with the task: unpark when the lease clears.",
        )
        self.assertEqual(T.migrate_operational_parks("demo"), [task["slug"]])
        self.assertEqual(S.load_task("demo", task["slug"])["state"], "approved")

    def test_untrusted_forensic_and_burak_parks_stay_parked(self):
        untrusted = T.new("demo", "untrusted forensic", "S", "request")
        self.forensic_park(
            untrusted["slug"], "Queue hold: unpark when PR #1 lands.", trusted=False)
        manual = T.new("demo", "manual", "S", "request")
        T.park("demo", manual["slug"], "Queue hold: unpark when PR #1 lands.", actor="burak")

        self.assertEqual(T.migrate_operational_parks("demo"), [])
        self.assertEqual(S.load_task("demo", untrusted["slug"])["state"], "parked")
        self.assertEqual(S.load_task("demo", manual["slug"])["state"], "parked")

    def test_legacy_global_marker_does_not_hide_a_new_park(self):
        marker = config.MONITOR_DIR / f"{T.OPERATIONAL_PARK_MIGRATION}-demo.json"
        S.write_json(marker, {"at": "2026-01-01T00:00:00+00:00", "project": "demo",
                              "migrated": ["historical"],
                              "explicit_or_unclassified": ["manual"]})
        task = T.new("demo", "new after legacy marker", "S", "request")
        T.park("demo", task["slug"], "Waiting for the prerequisite; unpark when it lands.", actor="l3")

        self.assertEqual(T.migrate_operational_parks("demo"), [task["slug"]])
        saved = S.read_json(marker)
        self.assertEqual(saved["history"][0]["migrated"], ["historical"])
        self.assertEqual(saved["migrated"], [task["slug"]])


if __name__ == "__main__":
    unittest.main()
