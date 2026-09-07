"""A system fault blocks its task, files one incident per kind, and leaves one message for L3."""
import json
import unittest
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, state as S, incidents, l3, verify, engines, dispatch, tasks as T

#: system_fault files against the project that owns Altitude's code whenever it is registered.
PROJECT = "altitude"


class TestSystemFault(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)

    def inbox_texts(self) -> list[str]:
        p = config.project_dir(PROJECT) / "inbox.jsonl"
        return [json.loads(line)["text"] for line in p.read_text().splitlines()] if p.exists() else []

    def queued(self) -> list[dict]:
        p = l3.queue_path(PROJECT)
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
        again = incidents.system_fault("test-kind", "something broke again", project=PROJECT)
        self.assertIsNone(again, "same kind within 24h must not file a second incident")
        faults = S.read_json(incidents.FAULTS)
        self.assertEqual(faults["test-kind"]["count"], 2)
        self.assertEqual(faults["test-kind"]["incident"], first["incident"])
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
        self.assertEqual(S.read_json(incidents.FAULTS)["test-kind"]["incident"], first["incident"])
        # the same fault on the task that is already blocked by it adds nothing
        self.assertIsNone(incidents.system_fault("test-kind", "main behind origin", project=PROJECT, task=second_task["slug"]))
        self.assertEqual(len(self.queued()), 2)

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
        self.assertIn("verifier", S.read_json(incidents.FAULTS))
        self.assertEqual(S.load_task(PROJECT, task["slug"])["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
