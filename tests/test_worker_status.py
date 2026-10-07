"""#676: unavailable unit identity cannot conclude a worker or abort project reconciliation."""
import hashlib
import json
import subprocess
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, incidents, l3, platform, server, state as S, tasks as T


class TestWorkerStatus(AltitudeCase):
    host = "linux"

    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "_codex_processes", {})
        self.real_publish = incidents.publish_issue
        self.publish = self.patch(incidents, "publish_issue", return_value={"pending": "fictional publisher"})
        self.patch(dispatch, "resume_engine_hold", return_value=None)
        self.patch(config, "machine_wip", return_value=20)

    def worker(self, slug, engine="codex", *, completed=False):
        task = T.new(self.project, slug, "Fictional worker reconciliation")
        task.update(state="running", attempt=3, l2_engine=engine, agent_id=slug,
                    session_id=f"session-{slug}", hold_merge="Retain review hold")
        S.save_task(self.project, task)
        paths = engines._codex_paths(dispatch.l2_job_root(self.project, slug), slug)
        unit = engines._codex_unit(slug) if engine == "codex" else engines._claude_unit(slug)
        record = {"id": slug, "engine": engine, "unit": unit, "session_id": task["session_id"],
                  "engine_model": "fictional-model", "started_at": S.now(), "input_delivered": True}
        S.write_json(paths["record"], record)
        events = ([{"type": "turn.completed"}] if engine == "codex" else
                  [{"type": "result", "is_error": False}]) if completed else []
        paths["stdout"].write_text("".join(json.dumps(row) + "\n" for row in events))
        paths["stderr"].write_text("")
        return task, paths, record

    def test_missing_unit_repeats_without_finishing_or_losing_reservation(self):
        for engine in ("claude", "codex"):
            for unit in (None, "", "absent"):
                for exited in (False, True):
                    with self.subTest(engine=engine, unit=unit, exited=exited):
                        label = "null" if unit is None else unit or "empty"
                        slug = f"worker-{engine}-{label}-{exited}".lower()
                        # A completed result and fresh report still do not prove that descendants stopped.
                        task, paths, record = self.worker(slug, engine, completed=True)
                        if unit == "absent":
                            record.pop("unit")
                        else:
                            record["unit"] = unit
                        S.write_json(paths["record"], record)
                        (S.task_dir(self.project, slug) / "report.json").write_text("{}")
                        T.message(self.project, slug, config.OPERATOR_ACTOR, "Keep this steering")
                        if exited:
                            engines._codex_processes[slug] = mock.Mock(poll=mock.Mock(return_value=0))
                        before = S.load_task(self.project, slug)
                        inbox = T.pending(self.project, slug)
                        with mock.patch.object(platform, "job_stop") as stop, \
                             mock.patch.object(engines, "resume_l2") as launch:
                            self.assertEqual(dispatch.poll(self.project), [])
                            self.assertEqual(dispatch.poll(self.project), [])
                            self.assertEqual(dispatch.resume(self.project, slug), {"already_running": True})
                            self.assertIsNone(engines.worker_termination(task, job_root=paths["record"].parent))
                            with self.assertRaisesRegex(RuntimeError, "identity is unavailable"):
                                engines.worker_live(engine, task, job_root=paths["record"].parent)
                            with self.assertRaisesRegex(RuntimeError, "ownership record is unavailable"):
                                engines.stop_l2_worker(engine, slug, job_root=paths["record"].parent)
                        stop.assert_not_called()
                        launch.assert_not_called()
                        after = S.load_task(self.project, slug)
                        for key, value in before.items():
                            if key not in ("updated", "token_usage"):
                                self.assertEqual(after.get(key), value, key)
                        self.assertEqual(T.pending(self.project, slug), inbox)
                        self.assertEqual(S.read_json(paths["record"]), record)
                        self.assertTrue(dispatch.occupies_slot(after))
                        self.assertNotIn(slug, dispatch.resume_due(self.project))
                        live = S.read_json(config.MONITOR_DIR / f"live-{self.project}--{slug}.json")
                        self.assertEqual(live["agent"]["status"], "unknown")
                        self.assertIsNone(live["idle_since"])
                        faults = S.read_json(incidents.FAULTS)
                        identity = hashlib.sha256(slug.encode()).hexdigest()[:12]
                        self.assertIsNone(faults[json.dumps([self.project, f"worker-status:{identity}"])]["task"])
                        # Keep each variation isolated without hiding repeat-fault behavior.
                        S.save_task(self.project, {**after, "state": "blocked", "waiting_on": "l3"})
        evidence = incidents.index(self.project)
        self.assertEqual(len(evidence), 12)
        self.assertEqual(self.publish.call_count, 12)
        queue = l3.queue_path(self.project).read_text()
        self.assertNotIn("Its task is blocked", queue)

    def test_uncertain_worker_keeps_last_slot_and_public_fault_omits_private_task_name(self):
        # Exercise the real publication sanitizer through a fictional GitHub executable.
        gh = self.fake_gh()
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "product-fixture/altitude")
        self.publish.side_effect = self.real_publish
        task, paths, record = self.worker("private-task-title")
        queued = T.new(self.project, "Queued work", "Wait for actual capacity")
        S.write_json(paths["record"], {**record, "unit": None})
        for host in ("linux", "darwin"):
            with self.subTest(host=host), mock.patch.object(platform.sys, "platform", host), \
                 mock.patch.object(config, "machine_wip", return_value=1):
                self.assertEqual(dispatch.poll(self.project), [])
                self.assertEqual(dispatch.wip_hold(self.project, queued), "WIP limit: 1 running on this machine")
        issues = json.loads((gh / "issues.json").read_text())
        self.assertEqual(len(issues), 1)
        self.assertNotIn(task["slug"], json.dumps(issues))
        self.assertNotIn(self.project, json.dumps(issues))

    def test_tick_continues_other_finishes_resumes_and_dispatch_after_unknown_worker(self):
        unknown, paths, record = self.worker("a-unknown", completed=True)
        record.pop("unit")
        S.write_json(paths["record"], record)
        done, _, _ = self.worker("b-reported", completed=True)
        (S.task_dir(self.project, done["slug"]) / "report.json").write_text("{}")
        failed, _, _ = self.worker("c-failed")
        waiting, _, _ = self.worker("d-waiting")
        waiting.update(state="blocked", resume_after=S.now(), resume_claim=None)
        S.save_task(self.project, waiting)
        blocked, _, _ = self.worker("e-claimed")
        blocked.update(state="blocked", waiting_on="operator", questions=[{"id": "keep", "status": "open",
                       "audience": "operator", "revision": 1, "question": "Keep this question"}],
                       resume_claim={"id": "keep-claim", "worker": {"id": "claimed-worker", "sessionId": "saved"}})
        S.save_task(self.project, blocked)
        self.patch(dispatch, "_claim_owner_live", return_value=True)
        self.patch(server.project_setup, "maintain")
        self.patch(server, "self_deploy")
        self.patch(server.images, "collect")
        self.patch(dispatch, "run_settings")
        self.patch(server, "request_l3_drain")
        spawn = self.patch(server, "spawn")
        reports = self.patch(server, "resume_stranded_reports")
        resume = self.patch(server, "request_task_resume")
        waiting_dispatch = self.patch(server, "dispatch_waiting")
        launch = self.patch(engines, "start_l2")
        # Real platform identity validation; only the external service-manager query is fictional.
        with mock.patch.object(platform.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, "inactive\n", "")):
            server.tick_project(self.project)
        finished = [call.args[3] for call in spawn.call_args_list if call.args[1] is server.on_l2_finished]
        self.assertEqual({item["task"]["slug"] for item in finished}, {done["slug"], failed["slug"]})
        self.assertFalse(next(item for item in finished if item["task"]["slug"] == done["slug"]).get("died"))
        self.assertTrue(next(item for item in finished if item["task"]["slug"] == failed["slug"])["died"])
        reports.assert_called_once_with(self.project)
        resume.assert_called_once_with(self.project, waiting["slug"])
        waiting_dispatch.assert_called_once_with(self.project)
        launch.assert_not_called()
        self.assertEqual(S.load_task(self.project, unknown["slug"])["state"], "running")
        claimed = S.load_task(self.project, blocked["slug"])
        for key in ("resume_claim", "questions", "hold_merge", "session_id", "attempt", "waiting_on"):
            self.assertEqual(claimed[key], blocked[key])
        self.assertTrue(dispatch.occupies_slot(claimed))
        self.assertNotIn(json.dumps([self.project, "tick"]), S.read_json(incidents.FAULTS))

    def test_known_unit_observation_recovers_without_relaunch(self):
        task, paths, record = self.worker("recover", completed=True)
        S.write_json(paths["record"], {**record, "unit": None})
        self.assertEqual(dispatch.poll(self.project), [])
        S.write_json(paths["record"], record)
        with mock.patch.object(platform, "job_active", return_value=True):
            self.assertEqual(dispatch.poll(self.project), [])
            self.assertTrue(engines.worker_live(task["l2_engine"], task, job_root=paths["record"].parent))
        with mock.patch.object(platform, "job_active", return_value=False):
            item, = dispatch.poll(self.project)
            self.assertEqual(item["task"]["agent_id"], task["agent_id"])
            self.assertTrue(item["died"])  # No report: normal exit fault handling is retained.
            self.assertTrue(engines.worker_termination(task, job_root=paths["record"].parent))

    def test_unavailable_unit_status_does_not_become_worker_death(self):
        task, _, _ = self.worker("unreadable")
        for failure in (RuntimeError("Worker unit status is unavailable"),
                        OSError("fictional status read failure"), subprocess.TimeoutExpired("fixture", 1)):
            with self.subTest(failure=failure), mock.patch.object(platform, "job_active", side_effect=failure):
                self.assertEqual(dispatch.poll(self.project), [])
                self.assertEqual(S.load_task(self.project, task["slug"])["state"], "running")
