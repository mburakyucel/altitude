"""Confirmed public issues notify a matching local coordinator without transferring recovery work."""
import json
import os
import subprocess
import threading
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import config, engines, incidents, l3, server, state as S, tasks as T


REPORT = {"expected": "The fictional Atlas task resumes once.",
          "actual": "The task remains blocked.",
          "reproduction": "Create a toy project, block its task, then request resume.",
          "version": "example-build-123"}
ARGS = ["issue", "upstream", "--title", "Fictional resume defect", "-"]
TARGET = "https://github.com/product-fixture/altitude"


class TestUpstreamIssues(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:fictional/atlas.git", cwd=self.repo)
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "product-fixture/altitude")
        self.writes = []
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] == "gh":
                self.writes.append((args, kwargs))
                repository = args[args.index("--repo") + 1]
                if args[1:3] == ["issue", "view"]:
                    return subprocess.CompletedProcess(args, 0, json.dumps({"url": repository + "/issues/42"}), "")
                return subprocess.CompletedProcess(args, 0, repository + "/issues/42\n", "")
            return real_run(args, **kwargs)

        self.run = self.patch(server.subprocess, "run", side_effect=run)

    def request(self, args=None, report=None, **extra):
        return server.l3_verb_request(self.project, {
            "kind": "alt", "args": ARGS if args is None else args,
            "stdin": json.dumps(REPORT if report is None else report), **extra})

    def fault(self, kind, project=None):
        project = project or self.project
        task = T.new(project, f"Fictional {kind} victim", "Toy request")
        return incidents.system_fault(kind, "Private fictional diagnostic", project=project, task=task["slug"])["incident"]

    def tracked(self, incident):
        return self.request(args=[*ARGS, "--incident", incident])

    def delivery(self, incident):
        return next(row for row in incidents.index(self.project) if row["id"] == incident)["upstream"]

    def link(self, incident, url=TARGET + "/issues/42"):
        return server.l3_verb_request(self.project, {"kind": "alt", "args": [
            "issue", "upstream", "--incident", incident, "--url", url]})

    def development_project(self):
        checkout = self.tmp / "development" / "repo"
        make_repo(checkout)
        git("remote", "set-url", "origin", "git@github.com:Product-Fixture/Altitude.git", cwd=checkout)
        self.register("altitude", path=checkout)
        return checkout

    def test_confirmed_creation_and_link_send_fixed_notification_without_transferring_tasks_or_evidence(self):
        self.development_project()
        incident = self.fault("source-private-kind")
        receiving = T.new("altitude", "Private receiving task", "Private instructions")
        receiving.update(state="blocked", attempt=3, session_id="existing-session", hold_merge="Operator hold")
        S.save_task("altitude", receiving)
        before = {p: p.read_bytes() for project in (self.project, "altitude")
                  for p in S.tasks_dir(project).rglob("*") if p.is_file()}
        self.tracked(incident)
        self.link(incident)
        self.request()  # Successful reports without an incident also use the same deduplication.
        rows = l3.queued("altitude")
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"id", "at", "role", "trigger", "upstream_url", "text"})
        self.assertEqual(rows[0]["upstream_url"], TARGET + "/issues/42")
        self.assertIn("assigns no work", rows[0]["text"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(len(S.list_tasks("altitude")), 1)
        self.assertEqual(self.delivery(incident)["status"], "confirmed")
        self.assertEqual(self.delivery(incident)["notification"]["status"], "queued")
        for private in (self.project, incident, "source-private-kind", "Private", REPORT["reproduction"], receiving["slug"]):
            self.assertNotIn(private, json.dumps(rows))

    def test_absent_removed_and_wrong_repository_targets_remain_issue_only(self):
        incident = self.fault("target")
        self.tracked(incident)
        self.assertEqual(self.delivery(incident)["notification"]["status"], "unavailable")
        self.assertFalse(config.project_dir("altitude").exists())
        checkout = self.development_project()
        git("remote", "set-url", "origin", "git@github.com:unrelated/altitude.git", cwd=checkout)
        self.tracked(incident)
        self.assertIn("does not match", self.delivery(incident)["notification"]["reason"])
        self.assertEqual(l3.queued("altitude"), [])
        config.remove_project("altitude")
        self.tracked(incident)
        self.assertEqual(self.delivery(incident)["notification"]["status"], "unavailable")
        self.assertEqual(l3.queued("altitude"), [])
        self.assertEqual(len(self.writes), 1)

    def test_notification_origin_check_and_enqueue_fence_removal(self):
        self.development_project()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        queue = l3.queue_upstream_issue
        def pause(*args, **kwargs):
            started.set()
            release.wait(10)
            return queue(*args, **kwargs)
        results = []
        with mock.patch.object(l3, "queue_upstream_issue", side_effect=pause):
            worker = threading.Thread(target=lambda: results.append(self.request()), daemon=True)
            worker.start()
            self.assertTrue(started.wait(5))
            with self.assertRaises(config.ProjectBusy):
                config.remove_project("altitude")
            release.set()
            worker.join(5)
        self.assertEqual(results[0]["stdout"], TARGET + "/issues/42\n")
        with config.project_activity("altitude", exclusive=True):
            self.request()
        self.assertEqual(S.read_project_log(self.project)[-1]["status"], "failed")
        self.assertEqual(len(l3.queued("altitude")), 1)

    def test_registration_replacement_after_origin_check_cannot_receive_notification(self):
        self.development_project()
        queue = l3.queue_upstream_issue
        def replace(*args, **kwargs):
            self.register("altitude", path=self.repo)
            return queue(*args, **kwargs)
        with mock.patch.object(l3, "queue_upstream_issue", side_effect=replace):
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
        self.assertEqual(l3.queued("altitude"), [])
        self.assertIn("Local notification failed", l3.chat_history(self.project)[-1]["text"])

    def test_failed_and_uncertain_publication_and_unverified_link_never_notify(self):
        self.development_project()
        incident = self.fault("publication")
        with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "invalid"):
            with self.assertRaises(ValueError):
                self.tracked(incident)
        with mock.patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 120)):
            with self.assertRaises(ValueError):
                self.tracked(incident)
            with self.assertRaises(ValueError):
                self.link(incident)
        self.assertEqual(self.delivery(incident)["status"], "uncertain")
        self.assertEqual(l3.queued("altitude"), [])
        self.link(incident)
        self.assertEqual(len(l3.queued("altitude")), 1)
        self.assertTrue(all(args[1:3] == ["issue", "view"] for args, _ in self.writes))

    def test_cross_project_concurrent_links_and_restart_keep_one_notification_per_full_url(self):
        self.development_project()
        self.register("other-source")
        sources = [(self.project, self.fault("first")), ("other-source", self.fault("second", "other-source"))]
        barrier = threading.Barrier(2)
        errors = []
        def link(project, incident):
            try:
                barrier.wait(5)
                server.issue_write(project, "upstream", "", actor="l3", incident=incident, url=TARGET + "/issues/42")
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=link, args=source, daemon=True) for source in sources]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(l3.queued("altitude")), 1)
        with mock.patch.object(l3, "_select", return_value={"engine": config.ENGINES[0]}), \
                mock.patch.object(l3, "turn", return_value={"completed": True}) as turn:
            l3.deliver_queued("altitude")
        turn.assert_called_once()
        for i in range(205):
            S.project_log("altitude", "unrelated", index=i)
        # Fresh interpreter sees the consumed receipt, even outside the normal event display window.
        script = ("from altitude import config, l3; import json; print(json.dumps(l3.queue_upstream_issue('altitude', "
                  + repr(TARGET + "/issues/42") + ", checkout=config.project_path('altitude'))))")
        result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "received")
        self.assertEqual(l3.queued("altitude"), [])
        # A different repository's issue with the same number has its own full identity.
        git("remote", "set-url", "origin", "git@github.com:other-product/altitude.git", cwd=config.project_path("altitude"))
        with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "other-product/altitude"):
            self.request()
        self.assertEqual(l3.queued("altitude")[0]["upstream_url"], "https://github.com/other-product/altitude/issues/42")

    def test_queue_failure_stays_separate_and_retry_does_not_republish(self):
        self.development_project()
        incident = self.fault("queue")
        with mock.patch.object(l3, "_write_queue", side_effect=OSError("Private queue diagnostic")):
            self.assertEqual(self.tracked(incident)["stdout"], TARGET + "/issues/42\n")
        outcome = self.delivery(incident)
        self.assertEqual(outcome["status"], "confirmed")
        self.assertEqual(outcome["notification"]["status"], "failed")
        self.assertNotIn("Private queue diagnostic", json.dumps(outcome))
        self.tracked(incident)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(l3.queued("altitude")), 1)
        with mock.patch.object(l3, "queue_upstream_issue", side_effect=OSError("Private queue diagnostic")):
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
        self.assertIn("Do not repost", l3.chat_history(self.project)[-1]["text"])

    def test_failed_notification_bookkeeping_keeps_successful_publication(self):
        self.development_project()
        project_log = S.project_log
        def fail(project, kind, **fields):
            if kind == "upstream-notification":
                raise OSError("Private bookkeeping error")
            return project_log(project, kind, **fields)
        with mock.patch.object(S, "project_log", side_effect=fail), mock.patch.object(server, "log") as log:
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
        self.assertNotIn("Private", log.call_args.args[0])
        self.assertEqual(len(l3.queued("altitude")), 1)

    def test_two_faults_one_report_remains_visible_and_reuses_link_after_restart_and_new_window(self):
        first, second = self.fault("resume"), self.fault("dispatch")
        self.assertEqual([row["upstream"]["status"] for row in incidents.index(self.project)], ["missing", "missing"])
        victim = next(row["task"] for row in incidents.index(self.project) if row["id"] == first)
        T.resume(self.project, victim)
        self.assertNotEqual(S.load_task(self.project, victim)["state"], "blocked")
        self.tracked(first)
        prevention = "Recovered: original session continues. Prevention: issue 42; owner role-correction; delivery pending."
        incidents.amend_incident(self.project, first, evidence=prevention, status="watch",
                                 reason="Local recovery does not complete prevention")
        self.assertEqual(self.delivery(first)["url"], TARGET + "/issues/42")
        self.assertEqual(self.delivery(second)["status"], "missing")
        self.assertIn("Check existing upstream issues", self.delivery(second)["reason"])
        summary = S.regen_state_md(self.project)
        self.assertIn(f"- {first}: watch — system fault: resume; report {TARGET}/issues/42", summary)
        self.assertIn(f"- {second}: watch — system fault: dispatch; no report linked", summary)
        self.assertIn(prevention, summary, "an open incident keeps its current follow-through visible")
        with mock.patch.object(server, "log"):
            server.restart_notice()
        restarted = [row for row in l3.queued(self.project) if row["trigger"] == "restart"][-1]["text"]
        self.assertNotIn(second, restarted, "incident history is not replayed into restart context")
        # A new Python process sees the persisted receipt without a live daemon/session cache.
        result = subprocess.run(["python3", "-c", "from pathlib import Path; import json, sys; "
            "from altitude import incidents; incidents.FAULTS=Path(sys.argv[1]); "
            "print(json.dumps(incidents.index(sys.argv[2])))", str(incidents.FAULTS), self.project],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["upstream"], self.delivery(first))
        self.assertEqual(json.loads(result.stdout)[0]["evidence"], prevention)
        self.assertEqual(json.loads(result.stdout)[0]["status"], "watch")

        self.tracked(first)
        repeat = self.fault("resume")
        self.assertEqual(repeat, first)
        self.assertNotIn(TARGET, l3.queued(self.project)[-1]["text"], "the fault message points at the incident only")
        self.assertIn(f"- {first}: watch — system fault: resume; report {TARGET}/issues/42", S.regen_state_md(self.project))
        faults = S.read_json(incidents.FAULTS)
        faults[json.dumps([self.project, "resume"])]["last"] = "2000-01-01T00:00:00+00:00"
        S.write_json(incidents.FAULTS, faults)
        later = self.fault("resume")
        self.assertNotEqual(first, later)
        self.assertEqual(self.delivery(later)["status"], "missing")
        self.link(later)  # L3 establishes a matching cause; the failure label cannot decide it.
        self.assertEqual(self.delivery(later)["url"], self.delivery(first)["url"])
        self.assertEqual(self.delivery(later)["incident"], later)
        self.tracked(later)
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(self.writes[-1][0][1:3], ["issue", "view"])
        self.assertEqual(self.delivery(second)["status"], "missing")
        self.assertNotIn("Private fictional diagnostic", self.writes[0][1]["input"])
        self.assertNotIn(first, self.writes[0][1]["input"])
        incidents.amend_incident(self.project, second, evidence="No defect; no report needed.", status="closed",
                                 reason="Closed without a report")
        self.assertNotIn(second, S.regen_state_md(self.project), "a closed incident is not advertised as missing")
        (config.project_dir(self.project) / "incidents" / f"{first}.md").unlink()
        self.assertIn(f"- {first}: evidence unavailable, inspect the local record", S.regen_state_md(self.project))
        self.assertIn(second, [row["id"] for row in incidents.index(self.project)], "history stays on demand")
        self.assertNotIn(prevention, self.writes[0][1]["input"])

    def test_prepublication_failure_is_actionable_and_can_be_explicitly_retried(self):
        incident = self.fault("configuration")
        with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "invalid"):
            with self.assertRaisesRegex(ValueError, "configure ALTITUDE_UPSTREAM"):
                self.tracked(incident)
        self.assertEqual(self.delivery(incident)["status"], "failed")
        self.assertIn("configure ALTITUDE_UPSTREAM", self.delivery(incident)["reason"])
        self.assertEqual(self.writes, [])
        self.tracked(incident)
        self.assertEqual(self.delivery(incident)["status"], "confirmed")
        incident = self.fault("executable")
        with mock.patch.object(server.subprocess, "run", side_effect=FileNotFoundError("private executable path")):
            with self.assertRaisesRegex(ValueError, "gh installation"):
                self.tracked(incident)
        self.assertEqual(self.delivery(incident)["status"], "failed")
        self.assertNotIn("private executable path", self.delivery(incident)["reason"])

    def test_existing_receipts_keep_their_incident_when_a_later_cause_publishes(self):
        for status in ("confirmed", "uncertain"):
            with self.subTest(status=status):
                kind = "worker-exit-" + status
                first = self.fault(kind)
                key = json.dumps([self.project, kind])
                receipt = {"status": status, "url": TARGET + "/issues/42" if status == "confirmed" else None,
                           "incident": first, "at": S.now(), "actor": "l3",
                           "reason": "Fictional publication receipt; check existing issues before retrying."}
                faults = S.read_json(incidents.FAULTS)
                faults[key].update(upstream=receipt, last="2000-01-01T00:00:00+00:00")
                S.write_json(incidents.FAULTS, faults)
                self.assertEqual(self.delivery(first), receipt)

                later = self.fault(kind)
                self.assertNotEqual(first, later)
                self.assertEqual(self.delivery(later)["status"], "missing")
                with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, TARGET + "/issues/43\n", "")) as publish:
                    self.assertEqual(self.tracked(later)["stdout"], TARGET + "/issues/43\n")
                    self.tracked(later)
                publish.assert_called_once()
                self.assertEqual(publish.call_args.args[0][1:3], ["issue", "create"])
                self.assertEqual(self.delivery(later)["incident"], later)
                self.assertEqual(self.delivery(first), receipt)
                self.assertEqual(S.read_json(incidents.FAULTS)[key]["upstream"], receipt)
                if status == "uncertain":
                    with self.assertRaisesRegex(ValueError, "before retrying"):
                        self.tracked(first)
                    self.assertEqual(self.delivery(first), receipt)
                else:
                    self.assertEqual(self.tracked(first)["stdout"], TARGET + "/issues/42\n")
                self.assertEqual(self.writes, [])

    def test_projectless_and_project_receipts_with_same_incident_id_stay_separate(self):
        self.register("altitude", path=self.repo)
        with mock.patch.object(incidents, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            machine = incidents.system_fault("machine-exit", "Fictional machine failure")["incident"]
            local = self.fault("worker-exit")
        self.assertEqual(machine, local)
        faults = S.read_json(incidents.FAULTS)
        faults["unscoped"] = {"upstream": {"status": "confirmed", "incident": machine,
                                            "url": TARGET + "/issues/99"}}
        S.write_json(incidents.FAULTS, faults)
        self.assertEqual(incidents.upstream_delivery("altitude", machine)["status"], "missing")
        result = server.l3_verb_request("altitude", {"kind": "alt", "args": [
            "issue", "upstream", "--incident", machine, "--url", TARGET + "/issues/42"]})
        self.assertEqual(result["stdout"], TARGET + "/issues/42\n")
        self.assertEqual(self.delivery(local)["status"], "missing")
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, TARGET + "/issues/43\n", "")):
            self.tracked(local)
        self.assertEqual(self.delivery(local)["url"], TARGET + "/issues/43")
        self.assertEqual(incidents.upstream_delivery("altitude", machine)["url"], TARGET + "/issues/42")
        self.assertEqual(S.read_json(incidents.FAULTS)["unscoped"], faults["unscoped"])

    def test_potentially_delivered_failures_latch_uncertainty_and_never_retry_creation(self):
        failures = [OSError("private pipe failure after launch"), subprocess.TimeoutExpired("gh", 120),
                    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid output after publication"),
                    subprocess.CompletedProcess([], 1, "", "private stderr"),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "https://github.com/other/repo/issues/42", "")]
        for i, failure in enumerate(failures):
            incident = self.fault(f"uncertain-{i}")
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with mock.patch.object(server.subprocess, "run", **kwargs):
                with self.assertRaisesRegex(ValueError, "before retrying"):
                    self.tracked(incident)
            outcome = self.delivery(incident)
            self.assertEqual(outcome["status"], "uncertain")
            self.assertNotIn("private stderr", outcome["reason"])
            with self.assertRaisesRegex(ValueError, "before retrying"):
                self.tracked(incident)
            self.assertEqual(self.delivery(incident), outcome)
        self.assertEqual(self.writes, [])
        self.assertEqual(S.regen_state_md(self.project).count("; report uncertain\n"), 6)

    def test_interrupted_request_and_concurrent_repeat_retain_uncertainty(self):
        incident = self.fault("interrupted")

        def interrupted(*args, **kwargs):
            # The prepublication receipt is durable and its lock is released during external IO.
            self.assertEqual(self.delivery(incident)["status"], "uncertain")
            with self.assertRaisesRegex(ValueError, "Creation is blocked"):
                self.tracked(incident)
            self.fault("independent")
            raise SystemExit("fictional daemon exit")

        with mock.patch.object(server.subprocess, "run", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.tracked(incident)
        with mock.patch.object(server, "log"):
            server.restart_notice()
        self.assertEqual(self.delivery(incident)["status"], "uncertain")
        with self.assertRaisesRegex(ValueError, "Check existing upstream issues"):
            self.tracked(incident)
        self.assertEqual(self.writes, [])

    def test_successful_creation_with_failed_local_event_cannot_be_replayed(self):
        incident = self.fault("local-recording")
        project_log = S.project_log

        def fail_receipt(project, kind, **fields):
            if kind == "issue-upstream":
                raise OSError("private bookkeeping path")
            return project_log(project, kind, **fields)

        with mock.patch.object(S, "project_log", side_effect=fail_receipt):
            with self.assertRaisesRegex(ValueError, "bookkeeping failed"):
                self.tracked(incident)
        self.assertEqual(self.delivery(incident)["status"], "uncertain")
        self.assertNotIn("private bookkeeping path", self.delivery(incident)["reason"])
        with self.assertRaisesRegex(ValueError, "before retrying"):
            self.tracked(incident)
        self.assertEqual(len(self.writes), 1)

    def test_verified_link_resolves_uncertainty_and_can_share_known_match_across_kinds(self):
        first, second = self.fault("first"), self.fault("second")
        with mock.patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 120)):
            with self.assertRaises(ValueError):
                self.tracked(first)
        with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"url": TARGET + "/issues/42"}), "")) as run:
            self.link(first)
            self.link(second)
            self.assertTrue(all(call.args[0][1:3] == ["issue", "view"] for call in run.call_args_list))
        self.assertEqual(self.delivery(first)["url"], self.delivery(second)["url"])
        self.tracked(first); self.tracked(second)
        with self.assertRaisesRegex(ValueError, "different confirmed URL"):
            self.link(first, TARGET + "/issues/43")
        self.assertEqual(self.writes, [])

    def test_failed_link_and_late_creation_failure_cannot_erase_a_verified_link(self):
        incident = self.fault("race")

        def late_failure(*args, **kwargs):
            with mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps({"url": TARGET + "/issues/42"}), "")):
                self.link(incident)
            raise subprocess.TimeoutExpired("gh", 120)

        with mock.patch.object(server.subprocess, "run", side_effect=late_failure):
            with self.assertRaisesRegex(ValueError, "delivery changed"):
                self.tracked(incident)
        self.assertEqual(self.delivery(incident)["status"], "confirmed")
        other = self.fault("failed-link")
        with mock.patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 120)):
            with self.assertRaises(ValueError):
                self.tracked(other)
            previous = self.delivery(other)
            with self.assertRaisesRegex(ValueError, "Previous delivery status is retained"):
                self.link(other)
        self.assertEqual(self.delivery(other), previous)

    def test_incident_linking_keeps_project_authority_and_sanitization(self):
        self.check_incident_authority((0, 0, 0))

    def test_incident_linking_keeps_project_authority_across_second_boundary(self):
        self.check_incident_authority((0, 1, 1))

    def check_incident_authority(self, seconds):
        self.register("other")
        with mock.patch.object(incidents, "datetime") as clock:
            clock.now.side_effect = [datetime(2026, 1, 1, 0, 0, second, tzinfo=timezone.utc) for second in seconds]
            foreign_ids = [self.fault("foreign", "other"), self.fault("foreign-two", "other")]
            own = self.fault("own")
        # IDs are project-local: either foreign incident can share the local ID.
        self.assertIn(own, foreign_ids)
        foreign = next(incident for incident in foreign_ids if incident != own)
        for incident in (foreign, "../private", "I-20000101-000000"):
            with self.assertRaises(ValueError):
                self.tracked(incident)
        for url in ("https://github.com/other/repo/issues/42", TARGET + "/pull/42", TARGET + "/issues/42?x=1"):
            with self.assertRaisesRegex(ValueError, "configured upstream repository"):
                self.link(own, url)
        with self.assertRaisesRegex(ValueError, "L2"):
            server.issue_write(self.project, "upstream", "", actor="l2", incident=own, url=TARGET + "/issues/42")
        with self.assertRaisesRegex(ValueError, "Private"):
            self.request(args=[*ARGS, "--incident", own], report=REPORT | {"actual": "Read incidents/private.md"})
        self.assertEqual(self.delivery(own)["status"], "failed")
        self.assertEqual(incidents.index("other")[0]["upstream"]["status"], "missing")
        self.assertNotIn(foreign, S.regen_state_md(self.project))
        self.assertEqual(self.writes, [])

    def test_historical_incidents_are_visible_without_inferred_linkage_or_publication(self):
        incident = incidents.new_incident(self.project, title="system fault: historical", task=None,
            what="Private historical fault", cause="unknown", evidence=TARGET + "/issues/42", tags=["system-fault"])["id"]
        self.assertEqual(self.delivery(incident)["status"], "missing")
        with self.assertRaisesRegex(ValueError, "historical backfill requires separate authorization"):
            self.tracked(incident)
        self.assertEqual(self.writes, [])

    def test_report_only_uses_product_target_and_leaves_all_private_state_in_place(self):
        self.private_ledgers()
        self.register("altitude", path=self.tmp / "installation")
        for project in (self.project, "altitude"):
            directory = config.project_dir(project)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "incidents").mkdir()
            (directory / "incidents" / "private.md").write_text("Private fixture evidence")
            (directory / "conversation.jsonl").write_text("Private fixture conversation")
        before = {path: path.read_bytes() for project in (self.project, "altitude")
                  for path in config.project_dir(project).rglob("*") if path.is_file()}
        self.setenv("GH_REPO", "unrelated/repository")
        with mock.patch.object(T, "new") as task, mock.patch.object(l3, "queue_message") as wake, \
                mock.patch.object(incidents, "new_incident") as incident, \
                mock.patch.object(server, "spawn") as spawn:
            result = self.request(project="altitude", actor="operator")
        self.assertEqual(result["stdout"], TARGET + "/issues/42\n")
        task.assert_not_called(); wake.assert_not_called(); incident.assert_not_called(); spawn.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(S.list_tasks(self.project), [])
        self.assertEqual(S.list_tasks("altitude"), [])
        self.assertEqual(S.read_project_log("altitude"), [])
        self.assertFalse(incidents.FAULTS.exists())
        args, kwargs = self.writes[0]
        self.assertEqual(args, ["gh", "issue", "create", "--title=Fictional resume defect",
                                "--repo", TARGET, "--body-file", "-"])
        self.assertNotIn("GH_REPO", kwargs["env"])
        self.assertEqual(kwargs["cwd"], self.repo)
        self.assertIn("## Expected behavior\n" + REPORT["expected"], kwargs["input"])
        self.assertNotIn("Private fixture", kwargs["input"])
        events = S.read_project_log(self.project)
        self.assertEqual([(e["kind"], e["actor"], e["url"]) for e in events if e["kind"] == "issue-upstream"],
                         [("issue-upstream", "l3", TARGET + "/issues/42")])
        self.assertNotIn(REPORT["reproduction"], json.dumps(events))
        for args in (["issue", "new", "--title", "Local backlog", "-"],
                     ["issue", "comment", "42", "-"],
                     ["issue", "close", "42", "--reason", "completed"]):
            server.l3_verb_request(self.project, {"kind": "alt", "args": args, "stdin": ""})
            self.assertIn("https://github.com/fictional/atlas", self.writes[-1][0])

    def test_unset_seam_uses_installation_origin_even_without_a_managed_altitude_project(self):
        install = self.tmp / "product" / "checkout"
        make_repo(install)
        git("remote", "set-url", "origin", "git@github.com:product-fixture/altitude.git", cwd=install)
        with mock.patch.object(config, "REPO", install), \
                mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", None):
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
            git("remote", "set-url", "origin", "/tmp/non-github.git", cwd=install)
            with self.assertRaisesRegex(ValueError, "configure ALTITUDE_UPSTREAM_ISSUE_REPOSITORY"):
                self.request()
        self.assertEqual(len(self.writes), 1)

    def test_invalid_configuration_never_falls_back_to_local_origin(self):
        for target in ("", "https://other.test/team/repo", "owner/repo/issues/1", "--repo=other/repo"):
            with self.subTest(target=target), mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", target):
                with self.assertRaisesRegex(ValueError, "configure ALTITUDE_UPSTREAM_ISSUE_REPOSITORY"):
                    self.request()
        self.assertEqual(self.writes, [])

    def test_canonical_github_url_confirms_a_mixed_case_configured_target(self):
        with mock.patch.object(config, "UPSTREAM_ISSUE_REPOSITORY", "Product-Fixture/ALTITUDE"), \
                mock.patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, TARGET + "/issues/42\n", "")) as run:
            self.assertEqual(self.request()["stdout"], TARGET + "/issues/42\n")
        self.assertIn("https://github.com/Product-Fixture/ALTITUDE", run.call_args.args[0])

    def test_report_shape_and_private_content_are_rejected_before_external_calls(self):
        for report in ({}, [], "raw incident dump", REPORT | {"evidence": "attachment"},
                       REPORT | {"actual": " "}, REPORT | {"version": 123}):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, "fictional/redacted JSON"):
                self.request(report=report)
        private = ["/home/example/private.txt", "/Users/example/private.txt", "~/secret", "$HOME/secret",
                   r"C:\Users\example\secret", "incidents/private.md", "I-20260907-123456.md",
                   "conversation.jsonl", "monitor/faults.json", "inbox.jsonl", "chat.jsonl",
                   "%2Fhome%2Fexample%2Fsecret", "ghp_" + "x" * 36, "github_pat_" + "x" * 50,
                   "sk-" + "x" * 30, "-----BEGIN OPENSSH PRIVATE KEY-----",
                   "Authorization: Bearer abcdef123456", "api_key=abcdef123456", '"password": "abcdef123456"',
                   "https://example:password@example.test"]
        for value in private:
            for field in REPORT:
                with self.subTest(value=value, field=field), self.assertRaisesRegex(ValueError, "Private"):
                    self.request(report=REPORT | {field: value})
            with self.subTest(title=value), self.assertRaisesRegex(ValueError, "Private"):
                self.request(args=["issue", "upstream", "--title", value, "-"])
        self.run.assert_not_called()
        self.assertEqual(S.read_project_log(self.project), [])
        report = {key: value for key, value in REPORT.items() if key != "version"}
        self.request(report=report | {"reproduction": "Set api_key=[REDACTED] in a toy project."})
        self.assertIn("## Altitude version\nunknown", self.writes[0][1]["input"])

    def test_unsupported_targets_actions_and_l2_are_denied(self):
        for suffix in (["--repo", "other/repo"], ["--target", "other"], ["--project", "altitude"],
                       ["--label", "bug"], ["--file", "incident.md"], ["--reason", "completed"],
                       ["--number", "42"], ["comment", "42"], ["close", "42"]):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.request(args=[*ARGS, *suffix])
        for operation in ("create", "comment", "close", "edit", "transfer"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                server.l3_verb_request(self.project, {"kind": "gh", "args": ["issue", operation, "42"]})
        for fields in ({"labels": []}, {"number": 42}, {"reason": "completed"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                server.issue_write(self.project, "upstream", json.dumps(REPORT), actor="l3", title="Defect", **fields)
        with self.assertRaisesRegex(ValueError, "L2"):
            server.issue_write(self.project, "upstream", json.dumps(REPORT), actor="l2", title="Defect")
        result = self.alt(*ARGS, env={"ALTITUDE_ACTOR": "l2"})
        self.assertIn("not available to an L2", result.stderr)
        result = self.alt(*ARGS, env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": self.project})
        self.assertIn("L3 must use its project-bound runtime alt socket", result.stderr)
        self.assertEqual(self.writes, [])

    def test_failures_are_actionable_and_do_not_echo_private_external_output(self):
        for failure in (OSError("/home/private/path"), subprocess.TimeoutExpired("gh", 120),
                        subprocess.CompletedProcess([], 1, "", "secret-token /home/private/path"),
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.CompletedProcess([], 0, "https://github.com/other/repo/issues/42", "")):
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with self.subTest(failure=failure), mock.patch.object(server.subprocess, "run", **kwargs):
                with self.assertRaisesRegex(ValueError, "alt issue upstream:") as error:
                    self.request()
                self.assertIn("before retrying", str(error.exception))
                self.assertNotIn("/home/private", str(error.exception))
                self.assertNotIn("secret-token", str(error.exception))
        self.assertEqual(S.read_project_log(self.project), [])

    def test_runtime_shim_and_mcp_admit_reporting_and_refuse_extra_authority(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "codex")
        self.addCleanup(l3._remove_runtime, runtime)
        bindir = runtime / "bin"
        adapter = tomllib.loads("\n".join(engines.codex_l3_permissions(runtime, project=self.project)))["mcp_servers"]["altitude"]
        tracked, linked = self.fault("runtime-create"), self.fault("runtime-link")
        cases = [(ARGS, REPORT, False),
                 ([*ARGS, "--incident", tracked], REPORT, False),
                 (["issue", "upstream", "--incident", linked, "--url", TARGET + "/issues/42"], None, False),
                 ([*ARGS, "--repo", "other/repo"], REPORT, True),
                 (["issue", "close", "42", "--reason", "completed", "--target", "upstream"], REPORT, True),
                 (ARGS, REPORT | {"actual": "Read incidents/private.md"}, True)]
        for args, report, denied in cases:
            stdin = json.dumps(report) if report is not None else ""
            result = subprocess.run([str(bindir / "alt"), *args], input=stdin, cwd=runtime,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(bool(result.returncode), denied, result)
            wire = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "coordinator", "arguments": {"kind": "alt", "args": args, "stdin": stdin}}}
            result = subprocess.run([adapter["command"], *adapter["args"]], input=json.dumps(wire) + "\n",
                                    cwd=runtime, env=os.environ | l3._l3_env(self.project, runtime),
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)["result"]
            self.assertEqual(reply["isError"], denied, reply)
            if not denied:
                self.assertEqual(json.loads(reply["content"][0]["text"])["stdout"], TARGET + "/issues/42\n")
        self.assertEqual(len(self.writes), 4)
        self.assertEqual(self.delivery(tracked)["status"], "confirmed")
        self.assertEqual(self.delivery(linked)["status"], "confirmed")

    def test_operator_cli_http_uses_the_same_create_only_handler(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.setenv("ALTITUDE_PORT", str(http.server_port))
        self.setenv("ALTITUDE_HOST", "127.0.0.1")
        self.setenv("ALTITUDE_TLS", "0")
        result = subprocess.run([str(config.REPO / "bin" / "alt"), *ARGS], input=json.dumps(REPORT),
                                env=os.environ | {"ALTITUDE_ACTOR": "burak", "ALTITUDE_PROJECT": self.project},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, TARGET + "/issues/42\n")
        self.assertEqual(S.read_project_log(self.project)[0]["actor"], "operator")
        for extra in ({"repo": "other/repo"}, {"target": "other"}, {"labels": ["bug"]}, {"number": 42}):
            request = urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/issue", headers={
                "Content-Type": "application/json"}, data=json.dumps({"project": self.project, "operation": "upstream",
                "title": "Defect", "body": json.dumps(REPORT), **extra}).encode())
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.writes), 1)
        tracked, linked = self.fault("http-create"), self.fault("http-link")
        for args, body in (([*ARGS, "--incident", tracked], json.dumps(REPORT)),
                           (["issue", "upstream", "--incident", linked, "--url", TARGET + "/issues/42"], "")):
            result = subprocess.run([str(config.REPO / "bin" / "alt"), *args], input=body,
                env=os.environ | {"ALTITUDE_ACTOR": "burak", "ALTITUDE_PROJECT": self.project},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, TARGET + "/issues/42\n")
        self.assertEqual(self.delivery(tracked)["actor"], "operator")
        self.assertEqual(self.delivery(linked)["actor"], "operator")
        self.assertEqual(len(self.writes), 3)
