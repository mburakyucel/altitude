"""Turning improvement suggestions on: the operator's agreement to the sheet's terms, the Weekly switch and its
Monday run, skipped weeks, a change of terms turning Weekly off, Get suggestions now, and the settings section's
lines. Projects are fictional records; the reviewer is scripted at the engine seam."""
import http.client
import json
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

from tests.test_improvement_runs import NOW, RunCase
from altitude import access, config, improve, l3, server, state as S


class TestAgreement(RunCase):
    def test_nothing_starts_without_the_operators_agreement_to_the_current_terms(self):
        self.load(agree=False)
        version = improve.terms(self.project)["version"]
        with self.assertRaisesRegex(ValueError, "Turn on improvement suggestions in this project's settings first"):
            improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        with self.assertRaisesRegex(ValueError, "settings first"):
            improve.run_now(self.project, "a" * 32, now=NOW)
        with self.assertRaisesRegex(ValueError, "settings first"):
            improve.set_weekly(self.project, True, now=NOW)
        with self.assertRaisesRegex(ValueError, "terms changed while the sheet was open"):
            improve.run_now(self.project, "a" * 32, agree="0" * 12, now=NOW)
        self.assertEqual(improve._load(self.project), {"runs": []})
        receipt = improve.run_now(self.project, "a" * 32, agree=version, now=NOW)
        self.assertEqual((receipt["state"], receipt["tasks"]), ("reading", 6))
        self.assertEqual(improve.run_now(self.project, "a" * 32, now=NOW), {**receipt, "replayed": True})
        record = improve._load(self.project)
        self.assertEqual(record["agreement"], {"terms": version, "by": "operator", "at": NOW.isoformat()})
        self.assertEqual(record["changes"], [{"at": NOW.isoformat(), "by": "operator", "agreed": version}])

    def test_the_terms_version_covers_the_model_and_the_limits(self):
        version = improve.terms(self.project)["version"]
        with mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5"):
            self.assertNotEqual(improve.terms(self.project)["version"], version)
            self.assertIn("Claude Haiku 5.5 reads counts", improve.terms(self.project)["body"])
        with mock.patch.object(improve.engines, "IMPROVEMENT_OUTPUT_TOKENS", 20_000):
            self.assertNotEqual(improve.terms(self.project)["version"], version)
        with mock.patch.object(improve, "QUOTE", 600):
            self.assertNotEqual(improve.terms(self.project)["version"], version)
        for name, value in (("SIGNALS_VERSION", improve.SIGNALS_VERSION + 1), ("OLDEST", timedelta(days=60)), ("FIRST_WINDOW", timedelta(days=28))):
            with mock.patch.object(improve, name, value):
                self.assertNotEqual(improve.terms(self.project)["version"], version, name)
        self.assertEqual(improve.terms(self.project)["version"], version)

    def test_a_change_of_terms_turns_weekly_off_says_so_once_and_needs_a_new_agreement(self):
        self.load()
        improve.set_weekly(self.project, True, now=NOW)
        improve.reconcile(self.project, now=NOW)
        self.assertEqual(self.chat(), [])
        with mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5"):
            improve.reconcile(self.project, now=NOW)
            improve.reconcile(self.project, now=NOW)
            view = improve.settings(self.project, now=NOW)
            self.assertEqual((view["weekly"], view["agreed"], view["line"]),
                             (False, False, "Off: the terms changed. Turn on to see them."))
            self.assertEqual([row["text"] for row in self.chat()],
                             ["Improvement suggestions are off until you agree to the new terms in settings."])
            with self.assertRaisesRegex(ValueError, "settings first"):
                improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
            improve.set_weekly(self.project, True, agree=improve.terms(self.project)["version"], now=NOW)
            self.assertEqual(improve.settings(self.project, now=NOW)["line"][:6], "Next: ")
        changes = improve._load(self.project)["changes"]
        self.assertEqual([c.get("weekly") for c in changes], [None, True, False, None, True])
        self.assertEqual(changes[2]["by"], "altitude")

    def test_only_the_altitude_project_has_the_section(self):
        with mock.patch.object(improve.github_intake, "project_repo", return_value=("someone", "orchard")):
            self.assertEqual(improve.settings(self.project), {"available": False})
            with self.assertRaisesRegex(ValueError, "only the Altitude project"):
                improve.set_weekly(self.project, True, agree=improve.terms(self.project)["version"])


class TestWeekly(RunCase):
    """The Weekly scenarios at the clock's time; the variants below repeat them from fixed local hours around Monday's
    run and in two time zones."""
    zone = None
    hour = None  # (days after this week's Monday, hour, minute) in local time, or None for the clock

    def setUp(self):
        if self.zone:
            self.addCleanup(time.tzset)
            self.setenv("TZ", self.zone)
            time.tzset()
        super().setUp()
        if self.hour:
            days, hour, minute = self.hour
            day = improve._monday(NOW).date() + timedelta(days=days)
            self.now = datetime(day.year, day.month, day.day, hour, minute).astimezone()
        # The first Monday run after the switch is turned on at self.now, at most a week away, so the fixture's six
        # tasks stay inside its first window; and the run a week later.
        this = improve._monday(self.now)
        self.monday = this if self.now < this else improve._monday(self.now, 1)
        self.following = improve._monday(self.monday, 1)

    def switched(self, at=None, count=6):
        self.load(count)
        improve.set_weekly(self.project, True, now=at or self.now)

    def test_monday_morning_claims_the_weeks_one_run(self):
        self.switched()
        self.assertIsNone(improve.weekly(self.project, now=self.monday - timedelta(minutes=1)))
        self.assertEqual(improve.settings(self.project, now=self.monday - timedelta(minutes=1))["line"],
                         f"Next: Monday, {improve._day(self.monday.isoformat())}.")
        receipt = improve.weekly(self.project, now=self.monday + timedelta(minutes=1))
        self.assertEqual((receipt["state"], receipt["tasks"]), ("reading", 6))
        improve.execute(self.project, receipt["run"])
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(days=2)))
        record = improve._load(self.project)
        self.assertEqual(record["weeks"], {self.monday.date().isoformat(): {
            "at": (self.monday + timedelta(minutes=1)).replace(microsecond=0).isoformat(), "run": receipt["run"]}})
        self.assertEqual(record["runs"][-1]["message"], f"week {self.monday.date().isoformat()}")
        self.assertEqual(improve.settings(self.project, now=self.monday + timedelta(hours=1))["line"],
                         f"Next: Monday, {improve._day(self.following.isoformat())}, if 5 more tasks finish.")

    def test_a_week_altitude_was_down_runs_at_the_first_check_and_a_missed_week_is_not_caught_up(self):
        self.switched()
        improve.weekly(self.project, now=self.following + timedelta(days=3))
        self.assertEqual(list(improve._load(self.project)["weeks"]), [self.following.date().isoformat()])

    def test_turned_on_after_mondays_run_it_first_runs_the_next_monday(self):
        self.switched(at=self.monday + timedelta(hours=1))
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(hours=2)))
        self.assertEqual(improve._load(self.project).get("weeks", {}), {})
        self.assertRegex(improve.settings(self.project, now=self.monday + timedelta(hours=2))["line"],
                         rf"^Next: Monday, {improve._day(self.following.isoformat())}")

    def test_a_week_that_fails_a_condition_is_skipped_recorded_and_said_only_in_settings(self):
        self.switched(count=3)
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=1)))
        week = self.monday.date().isoformat()
        self.assertEqual(improve._load(self.project)["weeks"][week]["skipped"], "3 tasks had finished")
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(hours=1)))
        self.assertEqual(improve.settings(self.project, now=self.monday + timedelta(hours=1))["line"],
                         f"Skipped {improve._day(self.monday.isoformat())}: 3 tasks had finished. "
                         f"Next: Monday, {improve._day(self.following.isoformat())}, if 2 more tasks finish.")
        self.assertEqual(self.chat(), [])
        self.engine.assert_not_called()

    def test_reserve_and_a_running_review_skip_the_week_and_a_pending_restart_waits(self):
        self.switched()
        with mock.patch.object(config, "activation_pending", return_value=True):
            self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=1)))
        self.assertEqual(improve._load(self.project).get("weeks", {}), {})
        self.usage.return_value = {"claude": (95.0, 10.0), "codex": (None, None)}
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=2)))
        self.assertEqual(improve._load(self.project)["weeks"][self.monday.date().isoformat()]["skipped"],
                         "Claude's weekly allowance is inside Altitude's reserve")
        self.usage.return_value = {"claude": (40.0, 10.0), "codex": (None, None)}
        improve.start(self.project, self.operator("Get improvement suggestions"), now=self.now)
        self.assertIsNone(improve.weekly(self.project, now=self.following + timedelta(minutes=1)))
        self.assertEqual(improve._load(self.project)["weeks"][self.following.date().isoformat()]["skipped"],
                         "another review was running")

    def test_weekly_turned_off_while_the_tick_reads_starts_nothing(self):
        self.switched()
        finished = improve.finished

        def turned_off(*args):
            improve.set_weekly(self.project, False, now=self.monday)
            return finished(*args)
        with mock.patch.object(improve, "finished", side_effect=turned_off):
            self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=1)))
        record = improve._load(self.project)
        self.assertEqual((record["runs"], record.get("weeks", {})), ([], {}))

    def test_weekly_off_runs_nothing(self):
        self.switched()
        improve.set_weekly(self.project, False, now=self.now)
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=1)))
        self.assertEqual(improve.settings(self.project, now=self.now)["line"],
                         "Every Monday morning, when at least 5 tasks have finished.")

    def test_the_tick_starts_the_monday_run_on_its_own_thread(self):
        self.switched()
        for name in ("self_deploy", "request_l3_drain", "resume_stranded_reports", "dispatch_waiting"):
            self.patch(server, name)
        self.patch(server.dispatch, "run_settings")
        self.patch(server.dispatch, "poll", return_value=[])
        spawn = self.patch(server, "spawn")
        weekly = improve.weekly
        with mock.patch.object(improve, "weekly", side_effect=lambda p: weekly(p, now=self.monday + timedelta(minutes=1))):
            server.tick_project(self.project)
        run = improve._load(self.project)["runs"][-1]
        spawn.assert_called_once_with(f"improve:{self.project}", improve.execute, self.project, run["id"])


# Sunday night, the early Monday hours before that day's run, Monday after it, and mid-week; in UTC and in a zone
# behind it.
for _zone in ("UTC", "America/Los_Angeles"):
    for _name, _hour in (("sunday_2330", (-1, 23, 30)), ("monday_0030", (0, 0, 30)), ("monday_0230", (0, 2, 30)),
                         ("monday_0900", (0, 9, 0)), ("wednesday_1200", (2, 12, 0))):
        _case = f"TestWeekly_{_zone.split('/')[-1]}_{_name}"
        globals()[_case] = type(_case, (TestWeekly,), {"zone": _zone, "hour": _hour})
del _zone, _name, _hour, _case


class TestSection(RunCase):
    def test_get_suggestions_now_says_what_it_reads_why_it_cannot_or_shows_the_running_review(self):
        self.load(count=3, agree=False)
        view = improve.settings(self.project, now=NOW)
        since = improve._day((NOW - improve.FIRST_WINDOW).isoformat())
        self.assertEqual((view["agreed"], view["weekly"], view["terms"]["title"]),
                         (False, False, f"Turn on improvement suggestions for {self.project}?"))
        self.assertEqual(view["terms"]["sent"],
                         "Counts and short chat quotes · Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check")
        self.assertEqual(view["now"], {"line": f"Reads the 3 tasks finished since {since}.",
                                       "unavailable": f"Needs 5 finished tasks since {since}; 3 so far."})
        self.usage.return_value = {"claude": (95.0, 10.0), "codex": (None, None)}
        self.assertEqual(improve.settings(self.project, now=NOW)["now"]["unavailable"],
                         "Claude's weekly allowance is inside Altitude's reserve.")

    def test_a_running_review_replaces_the_button(self):
        self.load()
        improve.run_now(self.project, "b" * 32, now=NOW)
        view = improve.settings(self.project, now=NOW)
        self.assertNotIn("now", view)
        self.assertEqual(view["running"]["line"], "Reading 6 tasks")

    def test_switch_and_run_come_only_from_a_paired_devices_own_page(self):
        self.load(agree=False)
        self.patch(server, "spawn")
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def call(method, path, body=None, status=200, **headers):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
            connection.request(method, path, body=body and json.dumps({"project": self.project, **body}),
                               headers={"Content-Type": "application/json", **headers})
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, status, payload)
            return payload

        version = call("GET", f"/api/improve/{self.project}")["terms"]["version"]
        call("GET", "/api/improve/no-such-project", status=404)
        on = {"action": "weekly", "on": True, "request_id": "c" * 32, "terms": version}
        self.assertIn("paired device", call("POST", "/api/improve", on, 403)["error"])
        self.patch(access, "device", return_value={"id": "fictional-phone", "name": "Phone"})
        self.patch(access, "renew", return_value=False)
        self.assertIn("Altitude's own page", call("POST", "/api/improve", on, 403, **{"Sec-Fetch-Site": "cross-site"})["error"])
        self.assertIn("Unsupported", call("POST", "/api/improve", {**on, "on": "yes"}, 409)["error"])
        self.assertEqual(call("POST", "/api/improve", on)["weekly"], True)
        receipt = call("POST", "/api/improve", {"action": "run", "request_id": "d" * 32})
        self.assertEqual(receipt["state"], "reading")
        server.spawn.assert_called_once_with(f"improve:{self.project}", improve.execute, self.project, receipt["run"])
        self.assertTrue(call("POST", "/api/improve", {"action": "run", "request_id": "d" * 32})["replayed"])
        server.spawn.assert_called_once()
        self.assertEqual(call("GET", f"/api/improve/{self.project}")["running"]["line"], "Reading 6 tasks")
        self.assertEqual(improve._load(self.project)["agreement"]["by"], "operator")
