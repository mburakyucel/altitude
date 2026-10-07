"""Machine admission with real task transitions, saved sessions and Git worktrees."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, digest, dispatch, engines, project_setup, route, server, state as S, status, tasks as T


class TestResumePriority(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.patch(config, "WIP_PER_MACHINE", 1)
        self.fake = FakeL2()
        self.fake.install(self)
        self.register("other", wip=1)

    def new(self, title, project=None):
        return T.new(project or self.project, title, "Implement the fictional request")

    def launch(self, title, project=None):
        project = project or self.project
        task = self.new(title, project)
        dispatch.run(project, task["slug"])
        return S.load_task(project, task["slug"])

    def pause(self, task, project=None, **updates):
        project = project or self.project
        self.fake.stop_l2_worker(task["l2_engine"], task["agent_id"], job_root=dispatch.l2_job_root(project, task["slug"]))
        return T.block(project, task["slug"], "Waiting for direction", updates=updates)

    def test_paused_owner_gets_next_slot_before_new_work_across_projects(self):
        for fresh_project in (self.project, "other"):
            with self.subTest(fresh_project=fresh_project):
                original = self.launch(f"Original {fresh_project}")
                original["hold_merge"] = "Operator review still required"
                S.save_task(self.project, original)
                self.pause(original)
                replacement = self.launch(f"Replacement {fresh_project}", fresh_project)
                fresh = self.new(f"Fresh {fresh_project}", fresh_project)
                message = T.message(self.project, original["slug"], "burak", "Continue the saved work")
                self.assertEqual(digest.wip()["machine"], 1)
                self.assertEqual(dispatch.resume_due(self.project), [])
                self.pause(replacement, fresh_project)

                # Even visiting the fresh project's queue first cannot steal the released slot.
                server.dispatch_waiting(fresh_project)
                self.assertEqual(S.load_task(fresh_project, fresh["slug"])["state"], "queued")
                self.assertTrue(server.request_task_resume(self.project, original["slug"]))
                server._bg[f"resume:{self.project}:{original['slug']}"].join(5)
                resumed = S.load_task(self.project, original["slug"])
                self.assertEqual(resumed["state"], "running")
                for key in ("session_id", "attempt", "worktree", "branch", "hold_merge"):
                    self.assertEqual(resumed[key], original[key])
                self.assertIn(message["text"], self.fake.calls[-1]["prompt"])
                self.assertEqual(digest.wip()["machine"], 1)
                self.pause(resumed)
                server.dispatch_waiting(fresh_project)
                fresh = S.load_task(fresh_project, fresh["slug"])
                self.assertEqual(fresh["state"], "running")
                self.pause(fresh, fresh_project)

    def test_explicit_resume_request_has_priority_before_its_background_runner(self):
        original = self.launch("Explicit owner")
        self.pause(original)
        dispatch.request_task_operation(self.project, original["slug"], "resume", "Recovery verified", actor="l3")
        fresh = self.new("Other project", "other")
        server.dispatch_waiting("other")
        self.assertEqual(S.load_task("other", fresh["slug"])["state"], "queued")
        self.assertEqual(dispatch.run_task_operation(self.project, original["slug"])["state"], "running")
        self.assertEqual(digest.wip()["machine"], 1)

    def test_running_status_excludes_admission_holds_during_concurrent_queue_reads(self):
        self.fake_gh()
        original = self.launch("Owner reads status")
        original["hold_merge"] = "Operator review required"
        S.save_task(self.project, original)
        pending = self.new("Pending launch")
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                if resumed:
                    self.pause(original)
                    replacement = self.launch("Temporary slot owner", "other")
                    T.message(self.project, original["slug"], "burak", "Continue")
                    self.assertIn("1 running on this machine", dispatch.resume(self.project, original["slug"])["held"])
                    waiting_resume = status.status(self.project, original["slug"])
                    self.assertEqual(waiting_resume["state"], "blocked")
                    self.assertEqual(waiting_resume["wip_hold"], "WIP limit: 1 running on this machine")
                    self.pause(replacement, "other")
                    self.assertIn("agent", dispatch.resume(self.project, original["slug"]))
                # Queue admission writes a project hold after the subject is running.
                # Synchronize each read with another admission pass in a separate thread.
                server.dispatch_waiting(self.project)
                barrier = threading.Barrier(2)

                def admission():
                    for _ in range(3):
                        barrier.wait(5)
                        server.dispatch_waiting(self.project)
                        barrier.wait(5)

                with ThreadPoolExecutor() as pool:
                    worker = pool.submit(admission)
                    for _ in range(3):
                        barrier.wait(5)
                        try:
                            running = status.status(self.project, original["slug"])
                            waiting = status.status(self.project, pending["slug"])
                            self.assertEqual(running["state"], "running")
                            self.assertIsNone(running["hold"])
                            self.assertIsNone(running["wip_hold"])
                            self.assertEqual(running["hold_merge"], "Operator review required")
                            self.assertEqual(running["session_id"], original["session_id"])
                            self.assertEqual(waiting["state"], "queued")
                            self.assertEqual(waiting["wip_hold"], "WIP limit: 1 running on this machine")
                            self.assertEqual(S.read_json(config.project_dir(self.project) / "hold.json")["reason"],
                                             waiting["wip_hold"])
                            self.assertEqual(digest.wip()["machine"], 1)
                        finally:
                            barrier.wait(5)
                    worker.result(5)

    def test_task_status_preserves_real_waits_without_inheriting_project_queue_hold(self):
        self.fake_gh()
        for updates in ({"waiting_on": "burak", "hold_merge": "Operator review"},
                        {"fault": "fixture-unrecovered", "hold_merge": "Operator review"}):
            with self.subTest(updates=updates):
                original = self.launch(f"Blocked owner {len(self.fake.calls)}")
                blocked = self.pause(original, **updates)
                S.write_json(config.project_dir(self.project) / "hold.json",
                             {"at": S.now(), "reason": "WIP limit: old project queue observation"})
                result = status.status(self.project, original["slug"])
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["blocked_reason"], blocked["blocked_reason"])
                self.assertIsNone(result["hold"])
                for key, value in updates.items():
                    self.assertEqual(result[key], value)

    def test_ineligible_resumes_and_planned_work_do_not_reserve_capacity(self):
        for reason, updates in (("operator", {"waiting_on": "burak"}),
                                ("fault", {"fault": "fixture-unrecovered"}),
                                ("future", {"resume_after": "2099-01-01T00:00:00+00:00"}),
                                ("stopped", {"stop_id": "fixture-stop"})):
            original = self.launch(reason)
            self.pause(original, **updates)
        planned = T.new(self.project, "Planned dependency", "Wait", wait="Approval of later work")
        self.assertEqual(digest.wip()["machine"], 0)
        self.assertEqual(dispatch.resume_due(self.project), [])
        fresh = self.launch("Eligible other project", "other")
        self.assertEqual(S.load_task("other", fresh["slug"])["state"], "running")
        self.assertEqual(S.load_task(self.project, planned["slug"])["state"], "queued")
        self.assertEqual(len(self.fake.calls), 5)

    def test_unavailable_saved_provider_does_not_freeze_an_eligible_provider(self):
        original = self.launch("Provider wait")
        self.pause(original)
        T.message(self.project, original["slug"], "burak", "Resume when available")
        saved_engine = original["l2_engine"]
        available_engine = next(engine for engine in config.ENGINES if engine != saved_engine)
        self.register("other", routing=[[{"engine": available_engine}]])
        with mock.patch.object(engines, "installation", side_effect=lambda engine: {
                "available": engine != saved_engine, "why": "fixture engine unavailable"}):
            self.assertEqual(dispatch.resume_due(self.project), [])
            result = dispatch.resume(self.project, original["slug"])
            self.assertIn("engine hold", result["held"])
            self.launch("Available provider", "other")
        self.assertEqual(len(self.fake.calls), 2)

    def test_simultaneous_fresh_launches_cannot_exceed_the_machine_cap(self):
        first, second = self.new("First fresh"), self.new("Second fresh", "other")
        entered, release = threading.Event(), threading.Event()
        start = self.fake.start_l2

        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return start(*args, **kwargs)

        with mock.patch.object(engines, "start_l2", side_effect=delayed), ThreadPoolExecutor() as pool:
            a = pool.submit(dispatch.run, self.project, first["slug"])
            try:
                self.assertTrue(entered.wait(5))
                b = pool.submit(dispatch.run, "other", second["slug"])
            finally:
                release.set()
            a.result(5)
            with self.assertRaisesRegex(T.TransitionError, "1 running on this machine"):
                b.result(5)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(digest.wip()["machine"], 1)

    def test_removed_saved_provider_does_not_freeze_an_eligible_project(self):
        original = self.launch("Removed provider")
        self.pause(original)
        T.message(self.project, original["slug"], "burak", "Continue when available")
        available = next(engine for engine in config.ENGINES if engine != original["l2_engine"])
        self.register("other", routing=[[{"engine": available}]])
        with mock.patch.object(config, "ENGINES", (available,)):
            self.assertEqual(dispatch.resume_due(self.project), [])
            self.assertIn("not configured", dispatch.resume(self.project, original["slug"])["held"])
            self.launch("Still available", "other")
        self.assertEqual(digest.wip()["machine"], 1)

    def test_native_default_model_denial_does_not_reserve_resume_capacity(self):
        engine = next(engine for engine in config.ENGINES if config.default_model("l2", engine) is None)
        self.register(self.project, routing=[[{"engine": engine}]])
        original = self.launch("Native default model")
        self.pause(original)
        T.message(self.project, original["slug"], "burak", "Continue when allowance returns")
        route.note_rejection({"engine": engine, "model": None, "role": "l2"},
                             {"scope": "model", "why": "Native default allowance exhausted"})
        self.assertEqual(dispatch.resume_due(self.project), [])
        self.assertIn("allowance exhausted", dispatch.resume(self.project, original["slug"])["held"])
        self.register("other", routing=[[{"engine": engine, "model": "available-model"}]])
        self.launch("Available model", "other")
        self.assertEqual(digest.wip()["machine"], 1)

    def test_setup_contention_does_not_reserve_capacity_in_any_project_tick_order(self):
        original = self.launch("Setup held owner")
        original["hold_merge"] = "Keep review hold"
        S.save_task(self.project, original)
        self.pause(original)
        T.message(self.project, original["slug"], "burak", "Retain this message through setup")
        dispatch.request_task_operation(self.project, original["slug"], "resume", "Continue", actor="l3")
        with project_setup.operation_lock(self.project):
            # The fresh project's tick happens first, with no resume attempt or retry timer.
            for index in range(2):
                fresh = self.new(f"Other project during setup {index}", "other")
                server.dispatch_waiting("other")
                fresh = S.load_task("other", fresh["slug"])
                self.assertEqual(fresh["state"], "running")
                self.pause(fresh, "other")
            self.assertFalse(S.load_task(self.project, original["slug"]).get("resume_claim"))
        fresh = self.new("Other project after setup", "other")
        server.dispatch_waiting("other")
        self.assertEqual(S.load_task("other", fresh["slug"])["state"], "queued")
        self.assertEqual(dispatch.run_task_operation(self.project, original["slug"])["state"], "running")
        resumed = S.load_task(self.project, original["slug"])
        self.assertEqual((resumed["session_id"], resumed["hold_merge"]),
                         (original["session_id"], original["hold_merge"]))
        self.assertIn("Retain this message through setup", self.fake.calls[-1]["prompt"])
        self.assertEqual(digest.wip()["machine"], 1)

    def test_simultaneous_resumes_and_fresh_launch_stay_within_machine_cap(self):
        first = self.launch("First resume")
        self.pause(first)
        second = self.launch("Second resume", "other")
        self.pause(second, "other")
        for project, task in ((self.project, first), ("other", second)):
            T.message(project, task["slug"], "burak", "Continue")
        fresh = self.new("Fresh contender", "other")
        entered, release = threading.Event(), threading.Event()

        def delayed():
            entered.set()
            self.assertTrue(release.wait(5))

        self.fake.on_resume = delayed
        with ThreadPoolExecutor() as pool:
            a = pool.submit(dispatch.resume, self.project, first["slug"])
            try:
                self.assertTrue(entered.wait(5))
                b = pool.submit(dispatch.resume, "other", second["slug"])
                c = pool.submit(dispatch.run, "other", fresh["slug"])
            finally:
                release.set()
            self.assertIn("agent", a.result(5))
            self.assertIn("1 running on this machine", b.result(5)["held"])
            with self.assertRaisesRegex(T.TransitionError, "1 running on this machine"):
                c.result(5)
        self.assertEqual(len(self.fake.calls), 3)
        self.assertEqual(digest.wip()["machine"], 1)

    def test_launched_resume_awaiting_crash_recovery_already_occupies_its_slot(self):
        original = self.launch("Recover launched owner")
        self.pause(original)
        T.message(self.project, original["slug"], "burak", "Continue")
        claim = T.claim_resume(self.project, original["slug"])
        worker = self.fake.resume_l2(original["l2_engine"], "recovered-worker", original["session_id"], "Continue")["agent"]
        T.update_resume_claim(self.project, original["slug"], claim["id"], phase="launched", worker=worker)
        task = S.load_task(self.project, original["slug"])
        task["resume_claim"]["owner_process"]["pid"] = 999999999
        S.save_task(self.project, task)
        fresh = self.new("Fresh after daemon crash", "other")

        self.assertEqual(digest.wip()["machine"], 1)
        server.dispatch_waiting("other")
        self.assertEqual(S.load_task("other", fresh["slug"])["state"], "queued")
        self.assertTrue(dispatch.resume(self.project, original["slug"])["recovered"])
        self.assertEqual(digest.wip()["machine"], 1)
        self.assertEqual(len(self.fake.calls), 2, "recovery binds the existing worker without another launch")
