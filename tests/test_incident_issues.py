"""Every incident gets one sanitized GitHub issue: created when it is filed, found again after a failed
attempt, attached to a matching issue by L3, and closed with the incident. Fixture `gh` only."""
import json
import subprocess
from unittest import mock

from tests.support import AltitudeCase, fyi_rows, git, make_repo
from altitude import config, engines, incidents, l3, platform, server, state as S, tasks as T

PROJECT = "atlas"
LOCAL_NAMES = platform.local_names   # the real reader; every case sees fixture names
TARGET = "https://github.com/product-fixture/altitude"


class IncidentIssueCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(PROJECT)
        self.gh = self.fake_gh()
        self.setenv("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY", "product-fixture/altitude")
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "product-fixture/altitude")
        self.patch(platform, "local_names", return_value={"host": {"ada-workstation.lan", "ada-workstation"},
                                                          "user": {"adafixture"}})

    def issues(self) -> list[dict]:
        path = self.gh / "issues.json"
        return json.loads(path.read_text()) if path.exists() else []

    def calls(self) -> list[list[str]]:
        path = self.gh / "log.jsonl"
        return [json.loads(line)[:2] for line in path.read_text().splitlines()] if path.exists() else []

    def record(self, incident: str) -> str:
        return (config.project_dir(PROJECT) / "incidents" / f"{incident}.md").read_text()

    def row(self, incident: str) -> dict:
        return next(r for r in incidents.index(PROJECT) if r["id"] == incident)

    def file(self, what="the worker used stale repository state", cause="the worktree base was not refreshed",
             title="the task used stale repository state", task=None):
        filed = incidents.new_incident(PROJECT, title=title, task=task, what=what, cause=cause,
                                       evidence="events.log 00:15:07 running->blocked", tags=["worker"])
        return filed["id"], incidents.publish_issue(PROJECT, filed["id"])


class TestPublication(IncidentIssueCase):
    def test_filing_creates_one_labelled_sanitized_issue_and_links_the_record(self):
        home = str(config.HOME)
        with mock.patch.object(config, "OPERATOR", "Ada Fixture"):
            incident, result = self.file(
                what=f"Ada Fixture's checkout at {home}/Projects/atlas/.claude/worktrees/fix was dirty; session "
                     "787c6ab0-8e6c-4c93-a8fc-b47b76e39e1e, agent 3e710860bbb944b29ff1e2068d33f5e1, "
                     "token=abcdefgh12345678, mail ada@example.test, see ~/.altitude/atlas/incidents/I-20260101-000000.md")
        [issue] = self.issues()
        self.assertEqual(result, {"id": incident, "issue": issue["url"], "created": True})
        self.assertEqual(issue["labels"], [incidents.ISSUE_LABEL])
        self.assertEqual(issue["title"], "the task used stale repository state")
        body = issue["body"]
        for private in (home, "Projects/atlas", "787c6ab0", "3e710860", "abcdefgh12345678", "ada@example.test",
                        "I-20260101-000000.md", "events.log", "Ada Fixture"):
            self.assertNotIn(private, body)
        for public in ("## Expected", "## Actual", "the operator's checkout at [path] was dirty; session [id], agent [id], "
                       "[REDACTED] mail [email], see [path]", "## Cause\nthe worktree base was not refreshed",
                       "## Reproduction\nPending triage", "## System\n- platform: ",
                       f"- altitude: {incidents.version()}\n", incidents.marker(PROJECT, incident)):
            self.assertIn(public, body)
        self.assertIn(f"- issue: {issue['url']}\n", self.record(incident))
        self.assertEqual(self.row(incident)["issue"], issue["url"])
        self.assertIn(f"issue {issue['url']}", S.regen_state_md(PROJECT))
        self.assertEqual([e["kind"] for e in S.read_project_log(PROJECT, limit=0) if e["kind"] == "incident-issue"],
                         ["incident-issue"])
        self.assertEqual(self.calls(), [["issue", "list"], ["issue", "create"]])

    def test_sanitizer_covers_short_secrets_key_blocks_encoded_names_and_project_names(self):
        self.register("tutor")
        with mock.patch.object(config, "OPERATOR", "Ada Fixture"):
            text = incidents.sanitize(
                "password=hunter2 and token=abcdefgh!supersecret; Ada%20Fixture saw session 3E710860BBBB44B29FF1E2068D33F5E1 in "
                "tutor/write-the-lesson-plan and altitude/land-the-fix for tutor\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\n"
                "-----END RSA PRIVATE KEY-----\nat %2Fhome%2Fada%2Fx")
            for private in ("hunter2", "supersecret", "Ada", "3E710860", "write-the-lesson", "land-the-fix", "tutor", "MIIE", "/home"):
                self.assertNotIn(private, text, text)
            self.assertEqual(text, "[REDACTED] and [REDACTED] the operator saw session [id] in [task] and [task] "
                                   "for [project]\n[REDACTED]\nat [path]")
            with self.assertRaisesRegex(ValueError, "operator's name"):
                incidents.check_public("Ada Fixture")

    def test_a_fault_detail_with_its_own_bullet_line_stays_one_field(self):
        incident, result = self.file(what="output was\n- issue: private log line\n- status: closed")
        self.assertTrue(result["issue"], result)
        self.assertEqual(incidents.index(PROJECT)[-1]["issue"], result["issue"])
        self.assertIn("- what happened: output was\n  - issue: private log line\n  - status: closed\n", self.record(incident))
        self.assertIn("- status: watch\n", self.record(incident))

    def test_system_fault_publishes_outside_the_fault_lock_and_names_the_issue(self):
        task = T.new(PROJECT, "Fixture victim", "Toy request")
        with mock.patch.object(incidents, "_fault_lock") as lock:
            lock.return_value.__enter__.return_value = None
            fault = incidents.system_fault("checkout", "Local cause", project=PROJECT, task=task["slug"])
        [issue] = self.issues()
        self.assertEqual(fault["issue"], issue["url"])
        self.assertEqual(issue["title"], "system fault: checkout")
        self.assertIn(issue["url"], fyi_rows(PROJECT)[-1]["text"])
        self.assertIn(issue["url"], l3.queued(PROJECT)[-1]["text"])
        repeat = incidents.system_fault("checkout", "Local cause", project=PROJECT, task=task["slug"])
        self.assertIsNone(repeat)
        self.assertEqual(len(self.issues()), 1)

    def test_failed_publication_stays_on_the_record_and_publish_retries_without_a_duplicate(self):
        (self.gh / "issue_create_error.txt").write_text("could not add label: 'incident' not found")
        incident, result = self.file()
        self.assertEqual(result["issue"], None)
        self.assertIn("could not add label", result["pending"])
        self.assertIn(f"- issue: {incidents.PENDING}GitHub refused: could not add label", self.record(incident))
        self.assertIn("issue pending — GitHub refused: could not add label", S.regen_state_md(PROJECT))
        self.assertEqual(self.issues(), [])
        (self.gh / "issue_create_error.txt").unlink()
        retried = incidents.publish_issue(PROJECT, incident)
        [issue] = self.issues()
        self.assertEqual(retried, {"id": incident, "issue": issue["url"], "created": True})
        self.assertIn(f"- issue: {issue['url']}\n", self.record(incident))
        self.assertNotIn(incidents.PENDING, self.record(incident))
        self.assertEqual(incidents.publish_issue(PROJECT, incident), {"id": incident, "issue": issue["url"], "created": False})
        self.assertEqual(len(self.issues()), 1)

    def test_retry_after_an_interrupted_create_finds_the_issue_by_its_incident_id(self):
        (self.gh / "issue_create_error.txt").write_text("timed out")
        incident, _ = self.file()
        (self.gh / "issue_create_error.txt").unlink()
        (self.gh / "issues.json").write_text(json.dumps([
            {"number": 7, "url": f"{TARGET}/issues/7", "state": "OPEN", "title": "earlier attempt", "labels": ["incident"],
             "body": f"## Actual\nsomething\n\n{incidents.marker(PROJECT, incident)}\n", "comments": [], "closed_reason": None},
            {"number": 8, "url": f"{TARGET}/issues/8", "state": "OPEN", "title": "other", "labels": ["incident"],
             "body": f"{incidents.marker('other', incident)}\n", "comments": [], "closed_reason": None}]))
        self.assertEqual(incidents.publish_issue(PROJECT, incident), {"id": incident, "issue": f"{TARGET}/issues/7", "created": False})
        self.assertEqual(len(self.issues()), 2)

    def test_unavailable_github_and_a_sanitizer_miss_keep_the_report_local(self):
        with mock.patch.object(incidents.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            incident, result = self.file()
        self.assertIn("GitHub request unavailable or timed out", result["pending"])
        with mock.patch.object(incidents, "sanitize", lambda text: text):
            second, result = self.file(what=f"private evidence at {config.HOME}/.altitude/atlas/incidents/I-1.md")
        self.assertIn("Private incident evidence boundary", result["pending"])
        self.assertEqual(self.issues(), [])
        self.assertNotIn(["issue", "create"], self.calls())

    def test_invalid_target_is_an_actionable_pending_reason(self):
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", "not a repository")
        incident, result = self.file()
        self.assertIn("incident repository must name a GitHub owner/repository", result["pending"])
        self.assertEqual(self.calls(), [])

    def test_fresh_installation_keeps_incidents_local_and_says_why(self):
        """Issue #470: an unset target files nothing anywhere; the record and `alt incident list` carry the reason."""
        self.patch(config, "UPSTREAM_ISSUE_REPOSITORY", None)
        incident, result = self.file()
        self.assertIsNone(result["issue"])
        self.assertIn("stay on this machine until incident reports are turned on in Settings", result["pending"])
        self.assertEqual(self.calls(), [])
        self.assertIn("pending — incidents stay on this machine", self.record(incident))
        self.assertIn("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY", self.row(incident)["issue"])
        listed = self.alt("--project", PROJECT, "incident", "list")
        self.assertIn("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY", listed.stdout)


class TestAttachAndClose(IncidentIssueCase):
    def kept(self, number=42, state="OPEN"):
        issues = self.issues()
        issues.append({"number": number, "url": f"{TARGET}/issues/{number}", "state": state, "title": "tracked cause",
                       "labels": ["bug"], "body": "hand-written report", "comments": [], "closed_reason": None})
        (self.gh / "issues.json").write_text(json.dumps(issues))
        return f"{TARGET}/issues/{number}"

    def test_attaching_a_matching_issue_notes_the_occurrence_and_closes_the_created_one_as_duplicate(self):
        incident, result = self.file()
        own = result["issue"]
        kept = self.kept()
        amended = incidents.amend_incident(PROJECT, incident, issue=kept, reason="Same cause as the tracked report")
        self.assertEqual(amended["amended"], ["issue"])
        self.assertIn(f"- issue: {kept}\n", self.record(incident))
        self.assertIn(f"- was issue: {own}", self.record(incident))
        by_url = {i["url"]: i for i in self.issues()}
        self.assertEqual(by_url[kept]["comments"], [f"Occurrence: incident {incident} on Altitude version {incidents.version()}.\n"])
        self.assertEqual((by_url[own]["state"], by_url[own]["closed_reason"]), ("CLOSED", "duplicate"))
        self.assertEqual(by_url[own]["comments"], [f"Duplicate of {kept}; tracking continues there.\n"])
        self.assertEqual(incidents.amend_incident(PROJECT, incident, issue=kept.upper().replace("HTTPS://GITHUB.COM", "https://github.com"),
                                                  reason="Repeat")["amended"], ["issue"])
        self.assertEqual(len(by_url[kept]["comments"]), 1)

    def test_a_record_filed_before_issues_existed_gets_its_issue_line(self):
        path = config.project_dir(PROJECT) / "incidents" / "I-20260101-000000.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# I-20260101-000000 — old record\n\n- date: 2026-01-01\n- task: -\n- project: atlas\n"
                        "- what happened: old failure\n- evidence: old evidence\n- root cause: old cause\n- status: watch\n")
        with open(config.project_dir(PROJECT) / "incidents.jsonl", "a") as f:
            f.write(json.dumps({"at": "2026-01-01T00:00:00+00:00", "project": PROJECT, "id": "I-20260101-000000",
                                "title": "old record", "task": None, "tags": [], "cause": "old cause"}) + "\n")
        self.assertIsNone(self.row("I-20260101-000000")["issue"])
        kept = self.kept()
        incidents.amend_incident(PROJECT, "I-20260101-000000", issue=kept, reason="Tracked here")
        self.assertIn(f"- status: watch\n- issue: {kept}\n\namended:", self.record("I-20260101-000000"))
        self.assertEqual(self.row("I-20260101-000000")["issue"], kept)
        # a hand-written issue that was attached is never closed as a duplicate of the next attachment
        other = self.kept(43)
        incidents.amend_incident(PROJECT, "I-20260101-000000", issue=other, reason="Better match")
        self.assertEqual({i["url"]: i["state"] for i in self.issues()}, {kept: "OPEN", other: "OPEN"})

    def test_unverifiable_or_foreign_issue_urls_are_refused_and_write_nothing(self):
        incident, _ = self.file()
        before = self.record(incident)
        for url in ("https://github.com/other/repo/issues/42", "not a url", f"{TARGET}/pull/42"):
            with self.assertRaisesRegex(ValueError, "issue URL in"):
                incidents.amend_incident(PROJECT, incident, issue=url, reason="Attach")
        with self.assertRaisesRegex(ValueError, "GitHub refused"):
            incidents.amend_incident(PROJECT, incident, issue=f"{TARGET}/issues/999", reason="Attach")
        self.assertEqual(self.record(incident), before)

    def test_closing_the_incident_comments_the_reason_and_closes_an_open_issue(self):
        incident, result = self.file()
        incidents.amend_incident(PROJECT, incident, status="closed", evidence="Recovered; prevention merged.",
                                 reason=f"Prevention delivered in PR #43 by {config.HOME}/me")
        [issue] = self.issues()
        self.assertEqual((issue["state"], issue["closed_reason"]), ("CLOSED", "completed"))
        self.assertEqual(issue["comments"], ["Incident closed: Prevention delivered in PR #43 by [path]\n"])
        self.assertIn("- status: closed", self.record(incident))
        self.assertNotIn(incident, S.regen_state_md(PROJECT))

    def test_closing_an_attached_incident_comments_but_leaves_the_shared_issue_open(self):
        incident, result = self.file()
        kept = self.kept()
        incidents.amend_incident(PROJECT, incident, issue=kept, reason="Same cause")
        incidents.amend_incident(PROJECT, incident, status="closed", reason="This occurrence is recovered")
        by_url = {i["url"]: i for i in self.issues()}
        self.assertEqual(by_url[kept]["state"], "OPEN")
        self.assertEqual(by_url[kept]["comments"][-1], "Incident closed: This occurrence is recovered\n")
        self.assertIn("- status: closed", self.record(incident))
        # attach and close in one amendment comment the kept issue, never the duplicate
        second, _ = self.file()
        incidents.amend_incident(PROJECT, second, issue=kept, status="closed", reason="Same cause, recovered")
        by_url = {i["url"]: i for i in self.issues()}
        self.assertEqual(by_url[kept]["state"], "OPEN")
        self.assertEqual(by_url[kept]["comments"][-1], "Incident closed: Same cause, recovered\n")
        self.assertEqual(by_url[result["issue"]]["comments"], [f"Duplicate of {kept}; tracking continues there.\n"])

    def test_closing_with_an_already_closed_issue_only_comments_and_a_pending_issue_needs_no_github(self):
        incident, result = self.file()
        issues = self.issues()
        issues[0].update(state="CLOSED", closed_reason="completed")
        (self.gh / "issues.json").write_text(json.dumps(issues))
        incidents.amend_incident(PROJECT, incident, status="closed", reason="Fixed upstream")
        self.assertEqual(self.calls()[-2:], [["issue", "view"], ["issue", "comment"]])
        (self.gh / "issue_create_error.txt").write_text("offline")
        second, _ = self.file()
        (self.gh / "log.jsonl").unlink()
        incidents.amend_incident(PROJECT, second, status="closed", reason="Stale; no live defect")
        self.assertEqual(self.calls(), [])
        self.assertIn("- status: closed", self.record(second))

    def test_a_refused_amendment_touches_no_issue(self):
        incident, _ = self.file()
        kept = self.kept()
        path = config.project_dir(PROJECT) / "incidents" / f"{incident}.md"
        path.write_text(path.read_text().replace("- evidence: ", "- proof: "))
        with self.assertRaisesRegex(ValueError, "no `- evidence:` line"):
            incidents.amend_incident(PROJECT, incident, issue=kept, evidence="x", status="closed", reason="Same cause")
        self.assertEqual(self.calls(), [["issue", "list"], ["issue", "create"]])
        self.assertNotIn("amended:", self.record(incident))

    def test_github_failure_on_closure_leaves_the_incident_open(self):
        incident, _ = self.file()
        (self.gh / "fail.txt").write_text("down")
        with self.assertRaisesRegex(ValueError, "GitHub refused"):
            incidents.amend_incident(PROJECT, incident, status="closed", reason="Fixed")
        self.assertIn("- status: watch", self.record(incident))
        self.assertNotIn("amended:", self.record(incident))


class TestNotificationAndAuthority(IncidentIssueCase):
    def development_project(self, origin="git@github.com:Product-Fixture/Altitude.git"):
        checkout = self.tmp / "development" / "repo"
        make_repo(checkout)
        git("remote", "set-url", "origin", origin, cwd=checkout)
        self.register("altitude", path=checkout)
        return checkout

    def test_another_projects_incident_notifies_the_matching_altitude_coordinator_once(self):
        self.development_project()
        incident, result = self.file()
        [notice] = l3.queued("altitude")
        self.assertEqual(notice["trigger"], "upstream-issue")
        self.assertIn(result["issue"], notice["text"])
        self.assertNotIn(incident, notice["text"])
        self.assertEqual(S.list_tasks("altitude"), [])
        incidents.publish_issue(PROJECT, incident)
        self.assertEqual(len(l3.queued("altitude")), 1)

    def test_a_link_recovered_by_marker_notifies_like_a_created_one(self):
        self.development_project()
        (self.gh / "issue_create_error.txt").write_text("timed out")
        incident, _ = self.file()
        self.assertEqual(l3.queued("altitude"), [])
        (self.gh / "issue_create_error.txt").unlink()
        (self.gh / "issues.json").write_text(json.dumps([
            {"number": 7, "url": f"{TARGET}/issues/7", "state": "OPEN", "title": "earlier attempt", "labels": ["incident"],
             "body": f"{incidents.marker(PROJECT, incident)}\n", "comments": [], "closed_reason": None}]))
        self.assertEqual(incidents.publish_issue(PROJECT, incident)["created"], False)
        self.assertEqual([n["trigger"] for n in l3.queued("altitude")], ["upstream-issue"])

    def test_altitude_itself_and_a_nonmatching_origin_get_no_notification(self):
        checkout = self.development_project("git@github.com:someone/else.git")
        incident, _ = self.file()
        self.assertEqual(l3.queued("altitude"), [])
        filed = incidents.new_incident("altitude", title="own fault", task=None, what="x", evidence="y", cause="z", tags=[])
        own = incidents.publish_issue("altitude", filed["id"])
        self.assertEqual(own["created"], True, own)
        self.assertEqual(l3.queued("altitude"), [])
        self.assertEqual(len(self.issues()), 2)

    def test_l3_verbs_publish_and_attach_but_the_old_report_verb_is_gone(self):
        incident, _ = self.file()
        for args in (["incident", "publish", "../evil"], ["issue", "upstream", "--title", "x", "-"]):
            with self.assertRaises(ValueError):
                server.l3_verb_request(PROJECT, {"kind": "alt", "args": args})
        reply = server.l3_verb_request(PROJECT, {"kind": "alt", "args": ["incident", "publish", incident]})
        self.assertEqual(reply["returncode"], 0, reply["stderr"])
        self.assertEqual(json.loads(reply["stdout"])["created"], False)
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            server.issue_write  # the HTTP surface below is the only caller that accepted incident/url fields
            raise ValueError("alt issue: unsupported fields")

    def test_operator_cli_files_publishes_amends_and_retries(self):
        env = {"ALTITUDE_ACTOR": "burak"}
        filed = self.alt("--project", PROJECT, "incident", "new", "--title", "cli incident", "--what", "w",
                         "--evidence", "e", "--cause", "c", env=env)
        self.assertEqual(filed.returncode, 0, filed.stderr)
        record = json.loads(filed.stdout)
        self.assertEqual(record["issue"], f"{TARGET}/issues/101")
        kept = f"{TARGET}/issues/101"
        self.assertEqual(self.alt("--project", PROJECT, "incident", "publish", record["id"], env=env).returncode, 0)
        refused = self.alt("--project", PROJECT, "incident", "amend", record["id"], "--issue", "https://example.invalid/1",
                           "--reason", "x", env=env)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("issue URL in", refused.stderr)
        self.assertEqual(self.alt("--project", PROJECT, "incident", "amend", record["id"], "--issue", kept,
                                  "--reason", "same", env=env).returncode, 0)
        self.assertEqual(len(self.issues()), 1)

    def test_project_local_issue_writes_keep_the_public_boundary_and_no_incident_fields(self):
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:fictional/atlas.git", cwd=self.repo)
        with self.assertRaisesRegex(ValueError, "Private incident evidence boundary"):
            server.issue_write(PROJECT, "new", f"see {config.HOME}/notes", actor="l3", title="leak")
        with self.assertRaisesRegex(ValueError, "Private credential boundary"):
            server.issue_write(PROJECT, "new", "token=abcdefghijklmnop", actor="l3", title="leak")
        with self.assertRaisesRegex(ValueError, "not available to an L2"):
            server.issue_write(PROJECT, "new", "x", actor="l2", title="t")
        with self.assertRaisesRegex(ValueError, "only new, comment, and close"):
            server.issue_write(PROJECT, "upstream", "x", actor="l3", title="t")
        url = server.issue_write(PROJECT, "new", "plain report", actor="l3", title="local", labels=["bug"])
        self.assertEqual(url, "https://github.com/fictional/atlas/issues/101")


UUID = "4c4c4544-0042-3510-8051-b4c04f565431"
# Identifiers within reach of collection or in the failing worker's output; none may reach an issue.
PRIVATE = ("ada-workstation", "adafixture", "10.20.30.40", "fe80::1c2d:3e4f", "a4:83:e7:12:34:56",
           "PF4SERIAL9", "L1HF3SERIAL", "C02XK1ZZJGH5", UUID, "thinking_tokens", "elta", '{"')
STREAM = ('elta":100,"session_id":"787c6ab0-8e6c-4c93-a8fc-b47b76e39e1e"}\n'
          '{"type":"system","subtype":"thinking_tokens","estimated_tokens":300,"estimated_tokens_delta":150}\n'
          '{"type":"system","subtype":"thinking_tokens","estimated_tokens":350,"estimated_tokens_delta":50}')


class TestSystemAndSummary(IncidentIssueCase):
    """Issues #613 and #658: an incident says which machine, build and engine failed, and what the failure was."""

    def setUp(self):
        super().setUp()
        self.patch(platform, "containerized", return_value=False)
        self.patch(platform.host_platform, "node", return_value="ada-workstation")
        self.patch(engines, "cli_version", return_value="2.1.300")
        self.task = T.new(PROJECT, "Fixture victim", "Toy request")["slug"]

    def linux_host(self):
        self.patch(platform.sys, "platform", "linux")
        self.patch(platform.host_platform, "release", return_value="6.8.0-45-generic")
        self.patch(platform.host_platform, "machine", return_value="x86_64")
        self.patch(platform.host_platform, "freedesktop_os_release", return_value={"PRETTY_NAME": "Ubuntu 24.04.1 LTS"})
        dmi = self.tmp / "dmi"
        dmi.mkdir()
        for name, value in {"sys_vendor": "LENOVO", "product_family": "ThinkPad X1 Carbon Gen 11",
                            "product_name": "21HMCTO1WW", "product_serial": "PF4SERIAL9", "board_serial": "L1HF3SERIAL",
                            "product_uuid": UUID}.items():
            (dmi / name).write_text(value + "\n")
        self.patch(platform, "DMI", dmi)

    def mac_host(self) -> list:
        self.patch(platform.sys, "platform", "darwin")
        self.patch(platform.host_platform, "mac_ver", return_value=("15.4.1", ("", "", ""), ""))
        self.patch(platform.host_platform, "release", return_value="24.4.0")
        self.patch(platform.host_platform, "machine", return_value="arm64")
        self.patch(config, "RELEASE", {"version": "v0.4.0", "commit": "884b6464abcdef0123456789abcdef0123456789"})
        self.patch(config, "INSTALL_PREFIX", self.tmp / "install")
        asked, real = [], subprocess.run

        def run(argv, *args, **kwargs):
            if argv[0] != "/usr/sbin/sysctl":
                return real(argv, *args, **kwargs)   # the fixture gh
            asked.append(argv)
            return subprocess.CompletedProcess(argv, 0, "Mac15,3\n", "")
        self.patch(platform.subprocess, "run", side_effect=run)
        return asked

    def fault(self, detail: str, step: str) -> tuple[str, str]:
        fault = incidents.system_fault("l2-died", detail, project=PROJECT, task=self.task, step=step)
        [issue] = self.issues()
        self.assertEqual(fault["issue"], issue["url"])
        return fault["incident"], issue["body"]

    def section(self, body: str, title: str) -> str:
        return body.split(f"## {title}\n", 1)[1].split("\n\n", 1)[0]

    def assert_private_absent(self, body: str):
        for private in (*PRIVATE, str(config.HOME), "3e710860"):
            self.assertNotIn(private, body)

    def test_linux_issue_names_the_machine_build_and_engine_and_the_failure_without_stream_lines(self):
        self.linux_host()
        incident, body = self.fault(
            "L2 worker 3e710860bbb944b29ff1e2068d33f5e1 (attempt 1) ended without a fresh report: worker state=failed "
            f"on ada-workstation as adafixture via 10.20.30.40 and fe80::1c2d:3e4f (a4:83:e7:12:34:56) in "
            f"{config.HOME}/Projects/atlas; " + STREAM, "the L2 worker run (attempt 1)")
        self.assertEqual(self.section(body, "System").splitlines(), [
            "- platform: Linux", "- os: Ubuntu 24.04.1 LTS", "- kernel: Linux 6.8.0-45-generic",
            "- architecture: x86_64", "- model: LENOVO ThinkPad X1 Carbon Gen 11",
            f"- altitude: {incidents.version()}", "- deployment: source checkout", "- engine: claude 2.1.300",
            "- confinement: systemd user unit, Claude permission rules"])
        self.assertEqual(self.section(body, "Actual"),
                         "Fault l2-died during the L2 worker run (attempt 1). Last error: L2 worker [id] (attempt 1) "
                         "ended without a fresh report: worker state=failed on [host] as [user] via [address] and "
                         "[address] ([address]) in [path]")
        self.assert_private_absent(body)
        record = self.record(incident)
        self.assertIn("thinking_tokens", record)   # the raw evidence stays private on the record
        self.assertIn("- system: platform: Linux; os: Ubuntu 24.04.1 LTS;", record)
        row = self.row(incident)
        self.assertIn("Last error: L2 worker", row["summary"])
        self.assertIn("engine: claude 2.1.300; confinement: systemd user unit", row["system"])

    def test_macos_issue_names_the_mac_model_and_release_and_the_error_inside_the_worker_output(self):
        asked = self.mac_host()
        _, body = self.fault("L2 worker 3e710860bbb944b29ff1e2068d33f5e1 (attempt 2) ended without a fresh report: "
                             "worker state=failed; {'message': 'workspace routing discovery unauthorized (401)'}",
                             "the L2 worker run (attempt 2)")
        self.assertEqual(asked, [["/usr/sbin/sysctl", "-n", "hw.model"]])   # never the serial or the platform UUID
        self.assertEqual(self.section(body, "System").splitlines(), [
            "- platform: macOS", "- os: macOS 15.4.1", "- kernel: Darwin 24.4.0", "- architecture: arm64",
            "- model: Mac15,3", "- altitude: v0.4.0 (884b6464abcd)", "- deployment: installed release",
            "- engine: claude 2.1.300", "- confinement: launchd job with Altitude's Seatbelt profile, Claude permission rules"])
        self.assertEqual(self.section(body, "Actual"), "Fault l2-died during the L2 worker run (attempt 2). "
                                                       "Last error: workspace routing discovery unauthorized (401)")
        self.assert_private_absent(body)

    def test_an_incident_l3_files_reports_its_own_words_and_the_machine_without_an_engine(self):
        self.linux_host()
        incident, _ = self.file(what="the worker used stale state on ada-workstation")
        body = self.issues()[0]["body"]
        self.assertEqual(self.section(body, "Actual"), "the worker used stale state on [host]")
        self.assertNotIn("- engine:", body)
        self.assertIn("- summary: \n", self.record(incident))

    def test_an_unreadable_machine_fact_still_files_the_incident(self):
        self.patch(platform, "host_facts", side_effect=OSError("denied"))
        incident, body = self.fault("worker exited", "the L2 worker run (attempt 1)")
        self.assertIn("- system: unavailable (OSError)", self.record(incident))
        self.assertEqual(self.section(body, "System"), "- unavailable (OSError)")
        self.assertEqual(self.section(body, "Actual"),
                         "Fault l2-died during the L2 worker run (attempt 1). Last error: worker exited")


class TestFailureLine(IncidentIssueCase):
    def test_the_last_error_wins_and_stream_events_without_one_never_count(self):
        cases = {
            "first\nError: disk full\n" + STREAM.split("\n", 1)[1]: "Error: disk full",
            'started\n{"type":"error","error":{"message":"rate limited"}}\n{"type":"system","subtype":"x"}': "rate limited",
            '{"type":"result","is_error":true,"result":"API Error: 500"}': "API Error: 500",
            '{"type":"result","is_error":false,"result":"done"}': "",
            STREAM.split("\n", 1)[1]: "",
            "  \n": "",
        }
        for text, line in cases.items():
            self.assertEqual(incidents.failure_line(text), line, text)
        self.assertEqual(incidents.failure_summary("tick", None, STREAM), "Fault tick. No error line was recorded.")
        self.assertEqual(len(incidents.failure_line("x" * 900)), 300)


class TestSanitizerFields(IncidentIssueCase):
    def test_network_addresses_and_this_machines_names_are_redacted_but_versions_times_and_loopback_stay(self):
        text = incidents.sanitize("adafixture@ada-workstation reached 10.20.30.40, 2001:db8:0:0:0:0:0:1, fe80::1 and "
                                  "a4:83:e7:12:34:56 at 10:15:07 with CLI 2.1.300 on Ubuntu; serving 127.0.0.1:8890 and ::1; "
                                  "Ada-Workstation stays")
        self.assertEqual(text, "[user]@[host] reached [address], [address], [address] and [address] at 10:15:07 with "
                               "CLI 2.1.300 on Ubuntu; serving 127.0.0.1:8890 and ::1; Ada-Workstation stays")
        with self.assertRaisesRegex(ValueError, "network addresses"):
            incidents.check_public("reached 10.20.30.40")
        incidents.check_public("serving 127.0.0.1:8890")


class TestLocalNames(AltitudeCase):
    def test_a_container_has_no_machine_names_and_localhost_is_none(self):
        self.patch(platform, "containerized", return_value=True)
        self.assertEqual(LOCAL_NAMES(), {"host": set(), "user": set()})
        self.patch(platform, "containerized", return_value=False)
        self.patch(platform.socket, "gethostname", return_value="ada-workstation.lan")
        self.patch(platform.pwd, "getpwuid", return_value=mock.Mock(pw_name="adafixture"))
        self.assertEqual(LOCAL_NAMES(), {"host": {"ada-workstation.lan", "ada-workstation"}, "user": {"adafixture"}})
        self.patch(platform.socket, "gethostname", return_value="localhost")
        self.assertEqual(LOCAL_NAMES()["host"], set())
