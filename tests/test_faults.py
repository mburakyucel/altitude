"""Fault evidence and notifications stay with the owning project, including repeated kinds."""
import json
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.support import AltitudeCase, fyi_rows, git, make_repo
from altitude import config, state as S, incidents, l3, verify, engines, dispatch, tasks as T

PROJECT = "altitude"


class TestSystemFault(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)

    def inbox_texts(self, project=PROJECT) -> list[str]:
        return [row["text"] for row in fyi_rows(project)]

    def queued(self, project=PROJECT) -> list[dict]:
        p = l3.queue_path(project)
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def test_fault_blocks_its_task_files_one_incident_and_queues_one_l3_message(self):
        task = T.new(PROJECT, "fault probe", "request", actor="burak")
        first = incidents.system_fault("test-kind", "something broke", project=PROJECT, task=task["slug"])
        self.assertTrue(first["incident"].startswith("I-"))
        blocked = S.load_task(PROJECT, task["slug"])
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("system fault [test-kind]", blocked["blocked_reason"])
        self.assertEqual([row["trigger"] for row in self.queued()], ["incident"])
        self.assertIn(first["incident"], self.queued()[0]["text"])
        again = incidents.system_fault("test-kind", "something broke", project=PROJECT)
        self.assertIsNone(again, "same kind within 24h must not file a second incident")
        faults = S.read_json(incidents.FAULTS)
        self.assertEqual(faults[json.dumps([PROJECT, "test-kind"])]["count"], 2)
        self.assertEqual(faults[json.dumps([PROJECT, "test-kind"])]["incident"], first["incident"])
        self.assertEqual(sum("SYSTEM FAULT [test-kind]" in text for text in self.inbox_texts()), 1)
        self.assertEqual(len(self.queued()), 1, "a repeated kind must not queue a second L3 message")
        other = incidents.system_fault("other-kind", "different mechanism")
        self.assertNotEqual(other["incident"], first["incident"])
        self.assertEqual(len(self.queued()), 2)

    def test_a_repeat_that_blocks_another_task_still_reaches_l3(self):
        first_task = T.new(PROJECT, "first victim", "request", actor="burak")
        first = incidents.system_fault("test-kind", "main behind origin", project=PROJECT, task=first_task["slug"])
        second_task = T.new(PROJECT, "second victim", "request", actor="burak")
        again = incidents.system_fault("test-kind", "main behind origin", project=PROJECT, task=second_task["slug"])
        self.assertEqual((again["incident"], again["repeat"]), (first["incident"], True))
        self.assertEqual(S.load_task(PROJECT, second_task["slug"])["fault"], "test-kind")
        rows = self.queued()
        self.assertEqual([row["trigger"] for row in rows], ["incident", "incident"])
        self.assertIn(second_task["slug"], rows[1]["text"]); self.assertIn(first["incident"], rows[1]["text"])
        self.assertEqual(S.read_json(incidents.FAULTS)[json.dumps([PROJECT, "test-kind"])]["incident"], first["incident"])
        # the same fault on the task that is already blocked by it adds nothing
        self.assertIsNone(incidents.system_fault("test-kind", "main behind origin", project=PROJECT, task=second_task["slug"]))
        self.assertEqual(len(self.queued()), 2)

    def test_saved_blockers_stay_quiet_across_restarts_and_incident_windows(self):
        from altitude import server
        task = T.new(PROJECT, "persistent fault", "request")
        first = incidents.system_fault("checkout", "local cause still present", project=PROJECT, task=task["slug"])
        saved = S.load_task(PROJECT, task["slug"])
        for day in (1, 3, 6):
            with self.subTest(day=day), mock.patch.object(S, "now", return_value=f"2030-01-{day:02d}T00:00:00+00:00"), \
                 mock.patch.object(dispatch, "_seconds_since", return_value=day * 86400), \
                 mock.patch.object(server, "log"):
                server.restart_notice()
                self.assertIsNone(incidents.system_fault("checkout", "local cause still present",
                                                        project=PROJECT, task=task["slug"]))
                self.assertEqual(S.load_task(PROJECT, task["slug"]), saved)
                self.assertEqual(dispatch.resume_due(PROJECT), [])
        self.assertEqual(len(incidents.index(PROJECT)), 1)
        self.assertEqual([row["trigger"] for row in self.queued()], ["incident", "restart", "restart", "restart"])
        self.assertEqual(len(self.inbox_texts()), 1)
        self.assertEqual(S.read_json(incidents.FAULTS)[json.dumps([PROJECT, "checkout"])]["incident"], first["incident"])

    def test_changed_full_details_and_new_blockers_remain_actionable_per_task(self):
        tasks = [T.new(PROJECT, title, "request") for title in ("first affected", "second affected")]
        details = ["shared diagnostic prefix " + "x" * 900 + suffix for suffix in ("cause A", "cause B")]
        first = incidents.system_fault("checkout", details[0], project=PROJECT, task=tasks[0]["slug"])
        second = incidents.system_fault("checkout", details[1], project=PROJECT, task=tasks[1]["slug"])
        self.assertEqual(first["incident"], second["incident"])
        for task, detail in zip(tasks * 2, details * 2):
            self.assertIsNone(incidents.system_fault("checkout", detail, project=PROJECT, task=task["slug"]))
        self.assertEqual(len(self.queued()), 2, "alternating task observations do not generate new nudges")
        original = S.load_task(PROJECT, tasks[0]["slug"])
        changed = incidents.system_fault("checkout", details[1], project=PROJECT, task=tasks[0]["slug"])
        self.assertEqual(changed["incident"], first["incident"])
        saved = S.load_task(PROJECT, tasks[0]["slug"])
        self.assertEqual(saved["blocked_reason"], f"system fault [checkout]: {details[1]}")
        self.assertNotEqual(saved["block_id"], original["block_id"])
        self.assertEqual(len(self.queued()), 3)
        self.assertIn(tasks[0]["slug"], self.queued()[-1]["text"])
        self.assertIsNone(incidents.system_fault("checkout", details[1], project=PROJECT, task=tasks[0]["slug"]))
        other = incidents.system_fault("different-kind", "a different blocker", project=PROJECT, task=tasks[0]["slug"])
        self.assertNotEqual(other["incident"], first["incident"])
        self.assertEqual(len(self.queued()), 4)

    def test_changed_project_observation_notifies_without_duplicate_incident(self):
        first = incidents.system_fault("tick", "x" * 900 + "cause A", project=PROJECT)
        changed = incidents.system_fault("tick", "x" * 900 + "cause B", project=PROJECT)
        self.assertEqual(changed["incident"], first["incident"])
        self.assertEqual(len(self.queued()), 2)
        self.assertIsNone(incidents.system_fault("tick", "x" * 900 + "cause B", project=PROJECT))
        self.assertEqual(S.read_json(incidents.FAULTS)[json.dumps([PROJECT, "tick"])]["detail"], "x" * 900 + "cause B")

    def test_unchanged_fault_preserves_its_escalated_operator_question(self):
        task = T.new(PROJECT, "fault awaiting operator", "request")
        incidents.system_fault("environment", "Original failed operation", project=PROJECT, task=task["slug"])
        T.escalate(PROJECT, task["slug"], "Approve using an alternative environment?")
        saved = S.load_task(PROJECT, task["slug"])
        self.assertEqual(saved["blocked_reason"], "system fault [environment]: Original failed operation")
        self.assertEqual(saved["waiting_on"], T.OPERATOR_MESSAGE_ROLE)
        self.assertEqual(T.question_views(PROJECT, task["slug"])[0]["question"], "Approve using an alternative environment?")
        self.assertIsNone(incidents.system_fault("environment", "Original failed operation",
                                                project=PROJECT, task=task["slug"]))
        self.assertEqual(S.load_task(PROJECT, task["slug"]), saved)
        self.assertEqual(len(self.queued()), 1)
        self.assertEqual(dispatch.resume_due(PROJECT), [])

    def test_reporting_receipt_closure_and_unrelated_restart_do_not_repair_originating_task(self):
        from altitude import server
        source = self.project
        checkout = self.tmp / "upstream" / "development"
        # Each fixture needs its own origin.git even when initial commits have different timestamps.
        for second, repo in enumerate((self.repo, checkout)):
            stamp = f"2026-01-01T00:00:0{second}+00:00"
            with mock.patch.dict("os.environ", {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}):
                make_repo(repo)
        git("remote", "set-url", "origin", "https://github.com/fictional/altitude.git", cwd=checkout)
        self.register(PROJECT, path=checkout)
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "fictional/altitude")
        task = T.new(source, "affected task", "Keep its original session", hold_merge="Operator review")
        task.update(state="running", attempt=2, session_id="original-session", agent_id="original-worker",
                    l2_engine=config.ENGINES[0], launch_model="original-model")
        S.save_task(source, task)
        incident = incidents.system_fault("checkout", "Local cause still present", project=source, task=task["slug"])
        saved = S.load_task(source, task["slug"])
        url = "https://github.com/fictional/altitude/issues/42"
        github = []
        run = subprocess.run

        def github_result(args, **kwargs):
            if args[0] != "gh":
                return run(args, **kwargs)
            github.append(args[1:3])
            return subprocess.CompletedProcess(args, 0, url + "\n", "")

        with mock.patch.object(server.subprocess, "run", side_effect=github_result):
            server.issue_write(source, "upstream", json.dumps({
                "expected": "The toy task resumes after repair", "actual": "The toy task waits",
                "reproduction": "Block a fictional task and inspect its status"}), actor="l3",
                incident=incident["incident"], title="Fictional recovery defect")
            self.assertEqual(S.load_task(source, task["slug"]), saved)
            self.assertEqual(len(self.queued()), 1)
            with mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "fixture"}), \
                 mock.patch.object(engines, "claude_print", return_value={
                     "text": "Issue received; no work assigned.", "session_id": "receiving-session", "usage": {}}):
                self.assertTrue(l3.deliver_queued(PROJECT)["completed"])
            server.issue_write(source, "upstream", "", actor="l3", incident=incident["incident"])
            outcome = incidents.upstream_delivery(source, incident["incident"])
            self.assertEqual((outcome["status"], outcome["notification"]["status"]), ("confirmed", "received"))
            server.issue_write(PROJECT, "close", "", actor="l3", number=42, reason="completed")
        self.assertEqual(github, [["issue", "create"], ["issue", "close"]])
        with mock.patch.object(server, "log"):
            server.restart_notice()
        self.assertIsNone(incidents.system_fault("checkout", "Local cause still present", project=source, task=task["slug"]))
        T.message(source, task["slug"], "l3", "The upstream issue is closed; local verification still fails.")
        self.assertEqual(S.load_task(source, task["slug"]), saved)
        self.assertEqual(dispatch.resume_due(source), [])
        self.assertEqual(dispatch.pending_task_operations(source), [])
        self.assertEqual(S.list_tasks(PROJECT), [])
        self.assertEqual([r["trigger"] for r in self.queued(source)], ["incident", "restart"])

    def test_late_rejected_fault_retains_evidence_without_retagging_newer_question(self):
        task = T.new(PROJECT, "changed question", "request")
        first = incidents.system_fault("checkout", "first fault", project=PROJECT, task=task["slug"])
        old_id = S.load_task(PROJECT, task["slug"])["block_id"]
        T.resume(PROJECT, task["slug"])
        T.block(PROJECT, task["slug"], "operator decision still needed", actor="l2")
        saved = S.load_task(PROJECT, task["slug"])
        with mock.patch.object(dispatch, "_seconds_since", return_value=3 * 86400):
            late = incidents.system_fault("checkout", "late failure evidence", project=PROJECT,
                                          task=task["slug"], expected_block_id=old_id)
        self.assertIsNotNone(late)
        self.assertNotEqual(late["incident"], first["incident"])
        self.assertEqual(S.load_task(PROJECT, task["slug"]), saved)

    def test_identical_faults_and_task_slugs_stay_in_each_project(self):
        self.register("demo")
        first = {}
        # IDs are reserved per project: even equal incident IDs must refer to the right evidence.
        with mock.patch.object(incidents, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 7, 20, 51, 28, tzinfo=timezone.utc)
            for project in ("demo", PROJECT):
                task = T.new(project, "first victim", "request")
                untouched = T.new(project, "unaffected task", "request")
                first[project] = incidents.system_fault("main-unpushed", f"{project} checkout refused",
                                                        project=project, task=task["slug"])
                blocked = S.load_task(project, task["slug"])
                self.assertEqual((blocked["state"], blocked["waiting_on"], blocked["fault"]),
                                 ("blocked", "l3", "main-unpushed"))
                self.assertEqual(S.load_task(project, untouched["slug"])["state"], "queued")
                self.assertEqual(T.decisions(project), [])
                if project == "demo":
                    self.assertEqual(self.queued(), [])
                    self.assertEqual(self.inbox_texts(), [])
                    self.assertFalse(S.task_dir(PROJECT, task["slug"]).exists())
        self.assertEqual(first["demo"]["incident"], first[PROJECT]["incident"])
        for project in (PROJECT, "demo"):
            with self.subTest(project=project):
                other = "demo" if project == PROJECT else PROJECT
                iid = first[project]["incident"]
                task = T.new(project, "second victim", "request")
                repeat = incidents.system_fault("main-unpushed", f"{project} still refused",
                                                project=project, task=task["slug"])
                self.assertEqual((repeat["repeat"], repeat["incident"]), (True, iid))
                self.assertIsNone(incidents.system_fault("main-unpushed", f"{project} still refused",
                                                        project=project, task=task["slug"]))
                self.assertIsNone(incidents.system_fault("main-unpushed", f"{project} still refused", project=project))
                rows = self.queued(project)
                self.assertEqual([r["trigger"] for r in rows], ["incident", "incident"])
                for text in [r["text"] for r in rows] + self.inbox_texts(project):
                    self.assertIn(f"{project}/{iid}", text)
                    self.assertNotIn(f"{other}/{iid}", text)
                self.assertEqual(len(self.inbox_texts(project)), 2)
                evidence = (config.project_dir(project) / "incidents" / f"{iid}.md").read_text()
                self.assertIn(f"{project} checkout refused", evidence)
                self.assertNotIn(f"{other} checkout refused", evidence)
                key = json.dumps([project, "main-unpushed"])
                self.assertIn(f"monitor/faults.json key {key}", evidence)
                ledger = S.read_json(incidents.FAULTS)[key]
                self.assertEqual((ledger["project"], ledger["count"], ledger["incident"]), (project, 4, iid))
        self.assertCountEqual([r["project"] for r in incidents.index()], [PROJECT, "demo"])

    def test_project_and_kind_delimiters_cannot_share_a_fault_record(self):
        self.register("demo")
        self.register("demo/child")
        for project, kind in (("demo", "child/tick"), ("demo/child", "tick")):
            filed = incidents.system_fault(kind, "checkout refused", project=project)
            self.assertIsNotNone(filed)
            self.assertEqual(len(self.queued(project)), 1)
            self.assertIsNone(incidents.system_fault(kind, "checkout refused", project=project))
        self.assertEqual(len(S.read_json(incidents.FAULTS)), 2)

    def test_unscoped_misrouted_record_never_supplies_another_projects_evidence(self):
        self.register("demo")
        old = incidents.new_incident(PROJECT, title="old misrouted fault", task=None, what="demo failed",
                                     evidence="original evidence", cause="unknown", tags=["system-fault"])
        old_path = config.project_dir(PROJECT) / "incidents" / f"{old['id']}.md"
        evidence = old_path.read_text()
        legacy = {"first": S.now(), "last": S.now(), "count": 7, "project": "demo", "incident": old["id"]}
        S.write_json(incidents.FAULTS, {"main-unpushed": legacy})
        for project in ("demo", PROJECT):
            filed = incidents.system_fault("main-unpushed", f"{project} failed", project=project)
            self.assertIsNotNone(filed)
            self.assertNotIn("repeat", filed)
            self.assertEqual(len(self.queued(project)), 1)
            self.assertTrue((config.project_dir(project) / "incidents" / f"{filed['incident']}.md").exists())
        self.assertEqual(S.read_json(incidents.FAULTS)["main-unpushed"], legacy)
        self.assertEqual(old_path.read_text(), evidence)

    def test_projectless_fault_uses_registered_altitude_without_sharing_project_dedupe(self):
        self.register("demo")
        machine = incidents.system_fault("tick", "machine fault")
        project = incidents.system_fault("tick", "project fault", project=PROJECT)
        self.assertNotEqual(machine["incident"], project["incident"])
        self.assertIsNone(incidents.system_fault("tick", "machine fault"))
        self.assertIsNone(incidents.system_fault("tick", "project fault", project=PROJECT))
        self.assertEqual(len(self.queued()), 2)
        self.assertEqual(self.queued("demo"), [])
        faults = S.read_json(incidents.FAULTS)
        for source in (None, PROJECT):
            self.assertEqual((faults[json.dumps([source, "tick"])]["project"],
                              faults[json.dumps([source, "tick"])]["count"]), (source, 2))

    def test_projectless_fault_without_altitude_only_records_machine_ledger(self):
        self._forget(PROJECT)
        self.register("demo")
        self.assertIsNone(incidents.system_fault("tick", "machine fault"))
        self.assertIsNone(incidents.system_fault("tick", "machine fault again"))
        rec = S.read_json(incidents.FAULTS)[json.dumps([None, "tick"])]
        self.assertEqual((rec["count"], rec["incident"], rec["project"]), (2, None, None))
        self.assertEqual(incidents.index(), [])
        self.assertEqual(self.queued("demo"), [])
        self.assertEqual(self.inbox_texts("demo"), [])

    def test_repair_faults_in_another_project_never_wake_either_l3(self):
        self.register("demo")
        for title in ("first repair", "second repair"):
            task = T.new("demo", title, "request", source="recovery")
            incidents.system_fault("repair-kind", title, project="demo", task=task["slug"])
            self.assertIsNone(incidents.system_fault("repair-kind", "same repair", project="demo", task=task["slug"]))
            blocked = S.load_task("demo", task["slug"])
            self.assertEqual((blocked["state"], blocked["waiting_on"], blocked["fault"]),
                             ("blocked", "l3", "repair-kind"))
            self.assertFalse(S.task_dir(PROJECT, task["slug"]).exists())
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.queued("demo"), [])
        self.assertEqual(self.inbox_texts(), [])
        self.assertEqual(len(self.inbox_texts("demo")), 1)
        self.assertIn("not woken", self.inbox_texts("demo")[0])
        self.assertEqual([r["project"] for r in incidents.index()], ["demo"])
        # A real project task sharing the repair's kind still gets its own blocked-task notification.
        ordinary = T.new("demo", "ordinary task", "request")
        again = incidents.system_fault("repair-kind", "ordinary failed", project="demo", task=ordinary["slug"])
        self.assertTrue(again["repeat"])
        self.assertEqual(len(self.queued("demo")), 1)
        self.assertEqual(self.queued(), [])

    def test_l2_reports_an_environment_fault_through_its_block_door(self):
        # The first Codex task after the rebuild blocked on a read-only worktree gitdir with a plain block, so the
        # cause sat in the Inbox as a question for Burak and L3 never saw it (2026-09-03).
        task = T.new(PROJECT, "fault door", "request", actor="burak")
        task.update({"state": "running", "attempt": 2}); S.save_task(PROJECT, task)
        slug = task["slug"]
        worker = {"ALTITUDE_ACTOR": "l2", "ALTITUDE_TASK": slug, "ALTITUDE_ATTEMPT": "1"}
        out = self.alt("--project", PROJECT, "task", "block", slug, "--reason", "gitdir is read-only", "--fault", env=worker)
        self.assertNotEqual(out.returncode, 0); self.assertIn("no longer current", out.stderr)
        self.assertEqual(S.load_task(PROJECT, slug)["state"], "running")
        worker["ALTITUDE_ATTEMPT"] = "2"
        out = self.alt("--project", PROJECT, "task", "block", slug, "--reason", "gitdir is read-only", "--fault", env=worker)
        self.assertEqual(out.returncode, 0, out.stderr)
        filed = json.loads(out.stdout)
        self.assertEqual(filed["kind"], f"worker:{slug}"); self.assertTrue(filed["incident"].startswith("I-"))
        blocked = S.load_task(PROJECT, slug)
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn(f"system fault [worker:{slug}]: gitdir is read-only", blocked["blocked_reason"])
        self.assertEqual((blocked["waiting_on"], blocked["fault"]), ("l3", f"worker:{slug}"))
        self.assertEqual(T.decisions(PROJECT), [], "a fault is L3's, not a card for Burak")
        self.assertEqual([row["trigger"] for row in self.queued()], ["incident"])
        self.assertIn(slug, self.queued()[0]["text"])

    def test_a_fault_tags_a_task_that_had_already_blocked_itself(self):
        task = T.new(PROJECT, "already blocked", "request", actor="burak")
        task.update({"state": "blocked", "blocked_reason": "gitdir is read-only"}); S.save_task(PROJECT, task)
        incidents.system_fault("worker:" + task["slug"], "gitdir is read-only", project=PROJECT, task=task["slug"])
        tagged = S.load_task(PROJECT, task["slug"])
        self.assertEqual((tagged["state"], tagged["waiting_on"], tagged["fault"]), ("blocked", "l3", "worker:" + task["slug"]))

    def test_restart_notice_hands_the_active_tasks_to_l3(self):
        from altitude import server
        faulty = T.new(PROJECT, "faulty", "request", actor="burak")
        incidents.system_fault("worker:" + faulty["slug"], "gitdir is read-only", project=PROJECT, task=faulty["slug"])
        running = T.new(PROJECT, "running fine", "request", actor="burak")
        running.update({"state": "running"}); S.save_task(PROJECT, running)
        done = T.new(PROJECT, "finished", "request", actor="burak")
        done.update({"state": "done"}); S.save_task(PROJECT, done)
        with mock.patch.object(server, "log"):
            server.restart_notice()
        notice = [row for row in self.queued() if row["trigger"] == "restart"]
        self.assertEqual(len(notice), 1)
        text = notice[0]["text"]
        self.assertIn(f"{faulty['slug']}: blocked (fault worker:{faulty['slug']})", text)
        self.assertIn(f"{running['slug']}: running (running)", text)
        self.assertNotIn(done["slug"], text)
        self.assertIn("alt task resume", text)

    def test_repair_task_fault_reaches_the_inbox_without_waking_l3(self):
        task = T.new(PROJECT, "repair probe", "request", actor="burak", source="recovery")
        incidents.system_fault("repair-kind", "repair broke", project=PROJECT, task=task["slug"])
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "blocked")
        self.assertEqual(self.queued(), [])
        self.assertTrue(any("[repair-kind]" in text and "not woken" in text for text in self.inbox_texts()))

    def test_task_blocked_before_launch_is_queued_again_on_resume(self):
        task = T.new(PROJECT, "requeue probe", "request", actor="burak")
        incidents.system_fault("launch-kind", "launch broke", project=PROJECT, task=task["slug"])
        res = dispatch.resume(PROJECT, task["slug"])
        self.assertTrue(res["requeued"])
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "queued")

    def test_queued_message_is_delivered_once_as_one_l3_turn(self):
        l3.queue_message(PROJECT, "hello L3", trigger="incident")
        with mock.patch.object(l3, "turn", return_value={"completed": True}) as turn, \
             mock.patch.object(l3, "_select", return_value={"engine": "claude", "why": "test"}):
            self.assertEqual(l3.deliver_queued(PROJECT), {"completed": True})
            self.assertIsNone(l3.deliver_queued(PROJECT))
        turn.assert_called_once_with(PROJECT, "hello L3", trigger="incident")
        self.assertFalse(l3.queue_path(PROJECT).exists())

    def test_queued_message_waits_for_an_engine(self):
        l3.queue_message(PROJECT, "hello L3", trigger="incident")
        with mock.patch.object(l3, "turn") as turn, \
             mock.patch.object(l3, "_select", return_value={"engine": None, "why": "both windows exhausted"}):
            self.assertIsNone(l3.deliver_queued(PROJECT))
        turn.assert_not_called()
        self.assertEqual(len(self.queued()), 1)

    def test_corrupt_json_raises_missing_defaults(self):
        p = self.tmp / "corrupt.json"
        p.write_text("{not json")
        with self.assertRaises(ValueError):
            S.read_json(p, {})
        self.assertEqual(S.read_json(self.tmp / "absent.json", {"d": 1}), {"d": 1})

    def test_claude_agents_failure_raises_instead_of_empty_list(self):
        self.patch(config, "CLAUDE_BIN", "/nonexistent/claude")
        with self.assertRaises(RuntimeError):
            engines.claude_agents()
        S.task_dir(PROJECT, "poll-probe").mkdir(parents=True, exist_ok=True)
        S.save_task(PROJECT, {"slug": "poll-probe", "title": "poll-probe", "state": "running",
                              "l2_engine": "claude", "created": S.now(), "updated": S.now()})
        # I-20260907-171446: polling reads owned units, while launch still fails closed on registry errors.
        with mock.patch.object(engines, "worker", side_effect=RuntimeError("unit inspection failed")):
            with self.assertRaisesRegex(RuntimeError, "unit inspection failed"):
                dispatch.poll(PROJECT)

    def test_verifier_tooling_failure_is_a_fault_verdict(self):
        self.patch(verify, "gh", new=lambda *a, **k: (_ for _ in ()).throw(verify.VerifierFault("gh: network down")))
        task = T.new(PROJECT, "verifier fault test", "request", actor="burak")
        task["state"] = "running"; S.save_task(PROJECT, task)
        d = S.task_dir(PROJECT, task["slug"])
        S.write_json(d / "report.json", {"landed": {"prs": [{"number": 1, "merged": True}], "main_runs": [], "deploy": "not-applicable"},
                                         "review": [], "deviations": [], "decisions": [], "fyi": [], "blocked": "", "follow_ups": [],
                                         "spend": {"turns": 1, "subagent_launches": 0, "retries": 0, "reverts": 0}})
        v = verify.verify(PROJECT, task["slug"])
        self.assertEqual(v["verdict"], "fault")
        self.assertIn("verifier fault", v["problems"][0])
        self.assertIn(json.dumps([PROJECT, "verifier"]), S.read_json(incidents.FAULTS))
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
