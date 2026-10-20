"""The Weekly improvement review as a built-in agent of Altitude's own project: the operator's agreement to the
sheet's terms, the Weekly switch and its Monday run, skipped weeks, a change of terms pausing it, Run now, its row on
the Agents page and its own page. Projects are fictional records; the reviewer is scripted at the engine seam."""
import http.client
import math
import json
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

from tests.test_improvement_runs import NOW, RunCase, suggestion
from altitude import access, config, improve, l3, schedule, server, state as S, suggestions


class TestAgreement(RunCase):
    def test_nothing_starts_without_the_operators_agreement_to_the_current_terms(self):
        self.load(agree=False)
        version = improve.terms(self.project)["version"]
        with self.assertRaisesRegex(ValueError, "Agree to the Weekly improvement review's terms on the Agents page first"):
            improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
        with self.assertRaisesRegex(ValueError, "Agents page first"):
            improve.run_now(self.project, "a" * 32, now=NOW)
        with self.assertRaisesRegex(ValueError, "Agents page first"):
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

    def test_a_change_of_terms_pauses_it_says_so_once_and_needs_a_new_agreement(self):
        self.load()
        improve.set_weekly(self.project, True, now=NOW)
        improve.reconcile(self.project, now=NOW)
        self.assertEqual(self.chat(), [])
        with mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5"):
            improve.reconcile(self.project, now=NOW)
            improve.reconcile(self.project, now=NOW)
            view = improve.page(self.project, now=NOW)
            self.assertEqual((view["agreed"], view["status"]["line"], view["status"]["actions"]),
                             (False, "Paused: its terms changed", ["resume", "run"]))
            self.assertEqual([row["text"] for row in self.chat()],
                             ["Weekly improvement review paused: its terms changed."])
            with self.assertRaisesRegex(ValueError, "Agents page first"):
                improve.start(self.project, self.operator("Get improvement suggestions"), now=NOW)
            improve.set_weekly(self.project, True, agree=improve.terms(self.project)["version"], now=NOW)
            self.assertEqual(improve.page(self.project, now=NOW)["status"]["line"][:6], "Next: ")
        changes = improve._load(self.project)["changes"]
        self.assertEqual([c.get("weekly") for c in changes], [None, True, False, None, True])
        self.assertEqual(changes[2]["by"], "altitude")

    def test_only_the_altitude_project_has_the_review(self):
        with mock.patch.object(improve.github_intake, "project_repo", return_value=("someone", "orchard")):
            self.assertEqual((improve.agent(self.project), improve.page(self.project), improve.yours(self.project)),
                             (None, None, False))
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
            local = NOW.astimezone()
            day = local.date() + timedelta(days=days - local.weekday())
            self.now = datetime(day.year, day.month, day.day, hour, minute).astimezone()
        # The first Monday run after the switch is turned on at self.now, at most a week away, so the fixture's six
        # tasks stay inside its first window; and the run a week later.
        weekly = (improve.WEEKLY_DAYS, improve.WEEKLY_TIME)
        self.monday = schedule.upcoming(*weekly, self.now - timedelta(microseconds=1))
        self.following = schedule.upcoming(*weekly, self.monday)

    def line(self, now):
        return improve.page(self.project, now=now)["status"]["line"]

    def switched(self, at=None, count=6):
        self.load(count)
        improve.set_weekly(self.project, True, now=at or self.now)

    def test_monday_morning_claims_the_weeks_one_run(self):
        self.switched()
        self.assertIsNone(improve.weekly(self.project, now=self.monday - timedelta(minutes=1)))
        self.assertEqual(self.line(self.monday - timedelta(minutes=1)), f"Next: Monday, {improve._day(self.monday.isoformat())}")
        receipt = improve.weekly(self.project, now=self.monday + timedelta(minutes=1))
        self.assertEqual((receipt["state"], receipt["tasks"]), ("reading", 6))
        improve.execute(self.project, receipt["run"])
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(days=2)))
        record = improve._load(self.project)
        self.assertEqual(record["weeks"], {self.monday.date().isoformat(): {
            "at": (self.monday + timedelta(minutes=1)).replace(microsecond=0).isoformat(), "run": receipt["run"]}})
        self.assertEqual(record["runs"][-1]["message"], f"week {self.monday.date().isoformat()}")
        self.assertEqual(self.line(self.monday + timedelta(hours=1)),
                         f"Next: Monday, {improve._day(self.following.isoformat())}, if 5 more tasks finish")

    def test_a_week_altitude_was_down_runs_at_the_first_check_and_a_missed_week_is_not_caught_up(self):
        self.switched()
        improve.weekly(self.project, now=self.following + timedelta(days=3))
        self.assertEqual(list(improve._load(self.project)["weeks"]), [self.following.date().isoformat()])

    def test_turned_on_after_mondays_run_it_first_runs_the_next_monday(self):
        self.switched(at=self.monday + timedelta(hours=1))
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(hours=2)))
        self.assertEqual(improve._load(self.project).get("weeks", {}), {})
        self.assertRegex(self.line(self.monday + timedelta(hours=2)),
                         rf"^Next: Monday, {improve._day(self.following.isoformat())}")

    def test_a_week_that_fails_a_condition_is_skipped_recorded_and_said_only_in_recent_runs(self):
        self.switched(count=3)
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(minutes=1)))
        week = self.monday.date().isoformat()
        self.assertEqual(improve._load(self.project)["weeks"][week]["skipped"], "3 tasks had finished")
        self.assertIsNone(improve.weekly(self.project, now=self.monday + timedelta(hours=1)))
        later = self.monday + timedelta(hours=1)
        self.assertEqual(self.line(later), f"Next: Monday, {improve._day(self.following.isoformat())}, if 2 more tasks finish")
        self.assertEqual(improve.page(self.project, now=later)["runs"], [{
            "id": None, "state": "skipped", "outcome": "Skipped", "tone": "muted",
            "at": (self.monday + timedelta(minutes=1)).replace(microsecond=0).isoformat(), "minutes": None,
            "line": "3 tasks had finished; it needs 5."}])
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
        self.assertEqual(self.line(self.now), "Paused")

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


# Sunday night, the early Monday hours before that day's run, its very hour, Monday after it, and mid-week; in UTC
# and in a zone behind it.
for _zone in ("UTC", "America/Los_Angeles"):
    for _name, _hour in (("sunday_2330", (-1, 23, 30)), ("monday_0030", (0, 0, 30)), ("monday_0230", (0, 2, 30)),
                         ("monday_0730", (0, 7, 30)),
                         ("monday_0800", (0, 8, 0)), ("monday_0900", (0, 9, 0)), ("wednesday_1200", (2, 12, 0))):
        _case = f"TestWeekly_{_zone.split('/')[-1]}_{_name}"
        globals()[_case] = type(_case, (TestWeekly,), {"zone": _zone, "hour": _hour})
del _zone, _name, _hour, _case


class TestAgentsRow(RunCase):
    """The review on the Agents page: Built in until it is the operator's, then the first of their agents."""
    def news(self):
        row = improve.agent(self.project)
        self.assertIsNone(row["builtin"])
        return row["agent"]["news"], row["agent"]["cue"]

    def test_built_in_until_agreed_switched_or_run(self):
        self.load(agree=False)
        row = improve.agent(self.project)
        self.assertEqual((row["agent"], row["builtin"]), (None, {
            "key": "weekly", "name": "Weekly improvement review",
            "line": "Mondays at 08:00 · Suggests fixes for friction that keeps coming back in this project's tasks"}))
        self.assertEqual(row["terms"]["title"], f"Turn on the Weekly improvement review for {self.project}?")
        self.assertFalse(improve.yours(self.project))
        self.agree()
        self.assertEqual(improve.agent(self.project)["agent"]["triggers"], "Mondays at 08:00")
        self.assertTrue(improve.yours(self.project))
        self.assertEqual(self.news(), ("Paused", "paused"))

    def test_its_news_running_paused_then_the_latest_ended_run(self):
        self.load()
        improve.set_weekly(self.project, True, now=NOW)
        self.assertEqual(self.news(), ("Never run", None))
        receipt = improve.run_now(self.project, "a" * 32, now=NOW)
        self.assertEqual(self.news(), ("Reading 6 tasks", "running"))
        improve.execute(self.project, receipt["run"])
        row = improve.agent(self.project)["agent"]
        self.assertEqual((row["id"], row["state"], row["news"], row["cue"]), ("weekly", "on", "Nothing to suggest", None))
        self.assertEqual(row["news_at"], improve._load(self.project)["runs"][0]["ended"])
        improve.set_weekly(self.project, False, now=NOW)
        self.assertEqual(self.news(), ("Paused", "paused"))
        with mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5"):
            self.assertEqual(self.news(), ("Paused: its terms changed", "paused"))

    def test_its_monitor_row_once_yours_on_the_agents_terms(self):
        self.load(agree=False)
        self.assertIsNone(improve.monitor(self.project, now=NOW), "built in, not yet the operator's")
        self.agree()
        self.assertIsNone(improve.monitor(self.project, now=NOW), "paused, never run")
        improve.set_weekly(self.project, True, now=NOW)
        row = improve.monitor(self.project, now=NOW)
        monday = schedule.when(schedule.upcoming((0,), "08:00", NOW), NOW)
        self.assertEqual((row["id"], row["name"], row["next"], row["cue"], row["last"]),
                         ("weekly", "Weekly improvement review", f"Next {monday}", None, None))
        receipt = improve.run_now(self.project, "a" * 32, now=NOW)
        self.assertEqual((improve.monitor(self.project, now=NOW)["next"], improve.monitor(self.project, now=NOW)["cue"]),
                         ("Running", "running"))
        improve.execute(self.project, receipt["run"])
        run = improve._find(improve._load(self.project), receipt["run"])
        self.assertEqual(improve.monitor(self.project, now=NOW)["last"],
                         {"outcome": "Nothing to suggest", "at": run["ended"], "tokens": 900, "minutes": math.ceil(
                             (improve._date(run["ended"]) - improve._date(run["started"])).total_seconds() / 60)})
        improve.set_weekly(self.project, False, now=NOW)
        self.assertEqual(improve.monitor(self.project, now=NOW)["next"], "Paused", "paused, ran this week")
        with mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5"):
            self.assertEqual(improve.monitor(self.project, now=NOW)["next"], "Paused: its terms changed")
        self.assertIsNone(improve.monitor(self.project, now=NOW + timedelta(days=8)), "paused, no run this week")

    def test_the_agents_view_and_works_count_carry_it(self):
        from altitude import project_agents
        self.load(agree=False)
        self.assertEqual(project_agents.view(self.project)["weekly"]["builtin"]["key"], "weekly")
        self.assertEqual(project_agents.project_summary(self.project)["count"], 0)
        self.agree()
        self.assertEqual(project_agents.view(self.project)["weekly"]["agent"]["id"], "weekly")
        self.assertEqual(project_agents.project_summary(self.project)["count"], 1)


class TestPage(RunCase):
    def status(self, now=NOW):
        return improve.page(self.project, now=now)["status"]

    def test_terms_out_of_date_whatever_the_switch_says_until_agreed(self):
        changed = mock.patch.object(config, "IMPROVEMENT_MODEL", "claude-haiku-5-5")
        # Never switched: agreed through Run now, then the terms changed.
        self.load()
        with changed:
            self.assertEqual((self.status()["line"], self.status()["actions"], self.status()["paused"]),
                             ("Paused: its terms changed", ["resume", "run"], True))
        # Paused by the operator before the change.
        improve.set_weekly(self.project, True, now=NOW)
        improve.set_weekly(self.project, False, now=NOW)
        with changed:
            self.assertEqual(self.status()["line"], "Paused: its terms changed")
        # Turned off by the change itself.
        improve.set_weekly(self.project, True, now=NOW)
        with changed:
            improve.reconcile(self.project, now=NOW)
            self.assertEqual(improve._load(self.project)["weekly"]["off"], "terms")
            self.assertEqual(self.status()["line"], "Paused: its terms changed")
            improve.set_weekly(self.project, True, agree=improve.terms(self.project)["version"], now=NOW)
            self.assertEqual((self.status()["line"][:6], self.status()["actions"]), ("Next: ", ["run", "pause"]))

    def test_not_on_yet_and_run_nows_reason(self):
        self.load(count=3, agree=False)
        since = improve._day((NOW - improve.FIRST_WINDOW).isoformat())
        status = self.status()
        self.assertEqual((status["line"], status["actions"], status["run"]), ("Not on yet", ["run", "turn_on"], None))
        self.assertEqual(status["now"], {"line": None, "unavailable": f"Run now needs 5 finished tasks since {since}; 3 so far."})
        self.usage.return_value = {"claude": (95.0, 10.0), "codex": (None, None)}
        self.assertEqual(self.status()["now"]["unavailable"], "Claude's weekly allowance is inside Altitude's reserve.")

    def test_on_says_when_it_runs_next_and_running_offers_stop_only(self):
        self.load()
        since = improve._day((NOW - improve.FIRST_WINDOW).isoformat())
        improve.set_weekly(self.project, True, now=NOW)
        upcoming = schedule.upcoming(improve.WEEKLY_DAYS, improve.WEEKLY_TIME, NOW)
        self.assertEqual(self.status(), {
            "line": f"Next: Monday, {improve._day(upcoming.isoformat())}", "actions": ["run", "pause"], "run": None,
            "spinning": False, "paused": False,
            "now": {"line": f"Reads the 6 tasks finished since {since}.", "unavailable": None}})
        receipt = improve.run_now(self.project, "b" * 32, now=NOW)
        self.assertEqual(self.status(), {"line": "Reading 6 tasks", "actions": ["stop"], "run": receipt["run"],
                                         "spinning": True, "paused": False, "now": None})

    def test_recent_runs_newest_first_with_every_ending(self):
        self.patch(improve, "CUTOFF", NOW - timedelta(days=60))
        self.load()
        self.answer = {"suggestions": [suggestion(anchors=["CITE"]), suggestion(title="Docks list rebuilt by hand", anchors=["CITE"])]}
        posted = improve.run_now(self.project, "c" * 32, now=NOW)["run"]
        improve.execute(self.project, posted)
        improve.post(self.project, posted, [1, 2], [], now=NOW)
        with S.project_lock(self.project):
            record = improve._load(self.project)
            run = record["runs"][0]
            # Later fictional endings: one stopped while reading, one that failed.
            for n, (state, reason) in enumerate((("stopped", "stopped by you"), ("failed", "it was interrupted by a restart")), 1):
                at = (NOW + timedelta(days=n)).isoformat()
                record["runs"].append({**run, "id": f"{n:012x}", "state": state, "reason": reason, "started": at,
                                       "ended": (NOW + timedelta(days=n, minutes=3)).isoformat(), "suggestions": [], "post": None})
            S.write_json(improve._path(self.project), record)
        rows = improve.page(self.project, now=NOW + timedelta(days=3))["runs"]
        titles = "; ".join(s["title"] for s in run["suggestions"])
        self.assertEqual([(r["id"], r["outcome"], r["tone"], r["line"]) for r in rows], [
            (f"{2:012x}", "Couldn't finish", "danger", "It was interrupted by a restart."),
            (f"{1:012x}", "Stopped by you", "muted", "It was reading 6 tasks."),
            (posted, "2 suggestions", "accent", titles)])
        self.assertEqual(rows[0]["minutes"], 3)
        self.assertEqual(improve.page(self.project, now=NOW)["what"]["text"],
                         "Finds friction that keeps coming back in this project's tasks and suggests fixes, as cards in chat "
                         "you can make into tasks.")

    def test_no_runs_yet(self):
        self.load(agree=False)
        self.assertEqual(improve.page(self.project, now=NOW)["runs"], [])

    def test_its_page_and_presses_through_the_server(self):
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

        self.assertEqual(call("GET", f"/api/agents/{self.project}")["weekly"]["builtin"]["key"], "weekly")
        version = call("GET", f"/api/agents/{self.project}/weekly")["terms"]["version"]
        call("GET", "/api/agents/no-such-project/weekly", status=404)
        call("GET", f"/api/improve/{self.project}", status=404)
        on = {"action": "weekly", "on": True, "request_id": "c" * 32, "terms": version}
        self.assertIn("paired device", call("POST", "/api/improve", on, 403)["error"])
        self.patch(access, "device", return_value={"id": "fictional-phone", "name": "Phone"})
        self.patch(access, "renew", return_value=False)
        self.assertIn("Altitude's own page", call("POST", "/api/improve", on, 403, **{"Sec-Fetch-Site": "cross-site"})["error"])
        self.assertIn("Unsupported", call("POST", "/api/improve", {**on, "on": "yes"}, 409)["error"])
        self.assertIn("read them again", call("POST", "/api/improve", {**on, "terms": "0" * 12}, 409)["error"])
        self.assertEqual(call("POST", "/api/improve", on)["weekly"], True)
        self.assertEqual(call("GET", f"/api/agents/{self.project}")["weekly"]["agent"]["id"], "weekly")
        receipt = call("POST", "/api/improve", {"action": "run", "request_id": "d" * 32})
        self.assertEqual(receipt["state"], "reading")
        server.spawn.assert_called_once_with(f"improve:{self.project}", improve.execute, self.project, receipt["run"])
        self.assertTrue(call("POST", "/api/improve", {"action": "run", "request_id": "d" * 32})["replayed"])
        server.spawn.assert_called_once()
        self.assertEqual(call("GET", f"/api/agents/{self.project}/weekly")["status"]["line"], "Reading 6 tasks")
        self.assertEqual(improve._load(self.project)["agreement"]["by"], "operator")
        with mock.patch.object(improve.github_intake, "project_repo", return_value=("someone", "orchard")):
            call("GET", f"/api/agents/{self.project}/weekly", status=404)
