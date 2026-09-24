"""A model's own allowance, not the shared account windows, decides whether that model is available."""
import json
import subprocess
import time
from datetime import datetime, timezone
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, dispatch, engines, l3, route, state as S, tasks as T

RESET = "2099-01-01T00:00:00+00:00"


def row(kind, percent, model=None, reset=RESET, surface=None):
    scope = {"model": {"display_name": model}} if model else {"surface": {"display_name": surface}} if surface else None
    return {"kind": kind, "group": "weekly" if kind != "session" else "session", "percent": percent,
            "resets_at": reset, "scope": scope, "severity": "normal", "is_active": kind == "session"}


def usage(*rows):
    return "\n".join(json.dumps(event) for event in (
        {"type": "assistant", "usage_report": {"rate_limits": {"limits": list(rows)}}},
        {"type": "result", "is_error": False, "num_turns": 0}))


def write_quota(fable=None, *, at=None, reset=4070908800.0, shared=55):
    """A persisted reading: the shared account windows with headroom and an optional Fable row."""
    models = [] if fable is None else [{"model": "fable", "label": "Fable", "seven_day": fable,
                                        "seven_day_resets": reset}]
    S.write_json(config.MONITOR_DIR / route.QUOTA_CLAUDE, {
        "known": True, "at": time.time() if at is None else at, "five_hour": shared, "seven_day": shared,
        "five_hour_resets": 4070908800.0, "seven_day_resets": 4070908800.0,
        **({"models": models} if models else {})})


class TestNativeModelRows(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.output = ""
        self.patch(engines.subprocess, "run", side_effect=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "2.1.280 (Claude Code)" if command[1:] == ["--version"] else self.output, ""))

    def test_model_rows_are_kept_beside_shared_windows_with_zero_and_unknown_reset(self):
        self.output = usage(row("session", 55), row("weekly_all", 55), row("weekly_scoped", 100, "Fable", None),
                            row("weekly_scoped", 0, "Opus"), row("weekly_scoped", 40, surface="Design"))
        reading = engines._claude_quota()
        self.assertTrue(reading["known"])
        self.assertEqual((reading["five_hour"], reading["seven_day"]), (55, 55))
        self.assertEqual(reading["models"], [
            {"model": "fable", "label": "Fable", "seven_day": 100, "seven_day_resets": None},
            {"model": "opus", "label": "Opus", "seven_day": 0,
             "seven_day_resets": datetime.fromisoformat(RESET).timestamp()}])

    def test_a_model_row_alone_is_a_reading_without_account_windows(self):
        self.output = usage(row("weekly_scoped", 20, "Fable"))
        reading = engines._claude_quota()
        self.assertTrue(reading["known"])
        self.assertNotIn("seven_day", reading)
        self.assertEqual(reading["models"][0]["seven_day"], 20)

    def test_a_malformed_or_duplicate_model_row_makes_the_whole_reading_unknown(self):
        for rows in ((row("weekly_all", 5), row("weekly_scoped", "bad", "Fable")),
                     (row("weekly_all", 5), row("weekly_scoped", 10, "Fable"), row("weekly_scoped", 20, "Fable")),
                     (row("weekly_all", 5), {k: v for k, v in row("weekly_scoped", 10, "Fable").items()
                                              if k != "severity"})):
            with self.subTest(rows=rows):
                self.output = usage(*rows)
                self.assertFalse(engines._claude_quota()["known"])

    def test_an_unrecognized_model_label_is_shown_but_matches_no_configured_model(self):
        self.output = usage(row("weekly_all", 5), row("weekly_scoped", 100, "Future Model"))
        reading = engines._claude_quota()
        self.assertEqual(reading["models"], [{"model": None, "label": "Future Model", "seven_day": 100,
                                              "seven_day_resets": datetime.fromisoformat(RESET).timestamp()}])
        self.assertIsNone(route.model_reading(reading, "fable"))


class TestModelRouting(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.patch(engines, "installation", return_value={"available": None, "why": "installed; access unknown"})
        self.patch(engines, "usage_hold", return_value=None)
        self.patch(route, "quota_codex", return_value={"known": False})

    def policy(self, value="claude:fable > claude:opus", **pins):
        self.register(self.project, routing=config.parse_routing(value), **pins)
        return config.project(self.project)

    def test_shared_headroom_with_exhausted_fable_routes_to_opus_and_names_the_reset(self):
        write_quota(fable=100)
        choice = route.pick_engine("l3", project=self.policy())
        self.assertEqual((choice["engine"], choice["model"]), ("claude", "opus"))
        self.assertIn("claude:fable unavailable: Fable weekly allowance exhausted; resets 2099-01-01T00:00:00+00:00",
                      choice["why"])
        self.assertEqual(route.pick_engine("l2", project=self.policy("claude:opus"))["model"], "opus")

    def test_unknown_reset_is_not_invented(self):
        write_quota(fable=100, reset=None)
        self.assertIn("Fable weekly allowance exhausted; reset time unknown",
                      route.pick_engine("l3", project=self.policy())["why"])

    def test_a_passed_reported_reset_ends_the_exclusion(self):
        write_quota(fable=100, reset=time.time() - 60)
        self.assertEqual(route.pick_engine("l3", project=self.policy())["model"], "fable")

    def test_zero_missing_and_stale_model_readings_do_not_exclude_the_model(self):
        for fable, at in ((0, None), (None, None), (100, time.time() - route.FRESH_SECONDS - 60)):
            with self.subTest(fable=fable, stale=at is not None):
                write_quota(fable=fable, at=at)
                self.assertEqual(route.pick_engine("l3", project=self.policy())["model"], "fable")

    def test_explicit_pin_on_an_exhausted_model_stays_strict(self):
        write_quota(fable=100)
        project = self.policy(l3_engine="claude")
        choice = route.pick_engine("l3", project=project)
        self.assertIsNone(choice["engine"])
        self.assertIn("claude:fable unavailable: Fable weekly allowance exhausted", choice["why"])
        self.assertIsNone(route.pick_engine("l2", model="fable", project=project)["engine"])

    def test_a_family_limit_excludes_a_configured_model_id_of_that_family_only(self):
        self.policy()
        route.note_limit("claude", engines.usage_limit_in("You've reached your Fable limit."))
        self.assertIn("fable allowance exhausted", route._rejected({"engine": "claude", "model": "claude-fable-5-1"}))
        self.assertIsNone(route._rejected({"engine": "claude", "model": "claude-opus-5-5"}))

    def test_resume_of_a_saved_fable_session_holds_while_its_own_allowance_is_exhausted(self):
        write_quota(fable=100)
        self.assertIn("Fable weekly allowance exhausted", route.resume_hold("claude", "fable"))
        self.assertIsNone(route.resume_hold("claude", "opus"))

    def test_an_explicit_fable_task_stays_queued_and_unlaunched(self):
        write_quota(fable=100)
        self.policy()
        self.patch(dispatch.git_policy, "fetch_origin", return_value="a" * 40)
        self.patch(dispatch, "_task_worktree", return_value=self.repo)
        self.patch(dispatch, "_validate_task_worktree")
        task = T.new(self.project, "Pinned work", "Make the change.", paths=["README.md"])
        with mock.patch.object(engines, "start_l2") as launch:
            with self.assertRaisesRegex(T.TransitionError, "forced claude:fable is unavailable"):
                dispatch.run(self.project, task["slug"], model="fable")
        launch.assert_not_called()
        saved = S.load_task(self.project, task["slug"])
        self.assertEqual((saved["state"], saved["model"]), ("queued", "fable"))

    def test_a_pinned_l3_keeps_its_system_event_queued_instead_of_running_a_failing_turn(self):
        write_quota(fable=100)
        self.policy(l3_engine="claude")
        queued = l3.queue_message(self.project, "Report landed for fictional-task", trigger="report-landed")
        with mock.patch.object(engines, "claude_print") as execute:
            self.assertIsNone(l3.deliver_queued(self.project))
        execute.assert_not_called()
        self.assertEqual([item["id"] for item in l3.queued(self.project)], [queued["id"]])
        write_quota(fable=10)
        with mock.patch.object(engines, "claude_print", return_value={
                "session_id": "conversation", "text": "Handled", "tools": [], "error": None,
                "usage": {}, "context_tokens": 1, "cost": 0, "turns": 1}) as execute:
            self.assertTrue(l3.deliver_queued(self.project)["completed"])
        self.assertEqual(execute.call_args.kwargs["model"], "fable")
        self.assertEqual(l3.queued(self.project), [])


class TestSeatModels(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.register(self.project, routing=config.parse_routing("claude:fable > claude:opus"))

    def claude(self):
        return next(seat for seat in route.seats() if seat["engine"] == "claude")

    def test_each_routed_model_shows_its_own_reading_or_says_it_has_none(self):
        write_quota(fable=100)
        models = {item["model"]: item for item in self.claude()["models"]}
        self.assertEqual((models["fable"]["seven_day"], models["fable"]["label"]), (100, "Fable"))
        self.assertIsNone(models["opus"]["seven_day"])  # no row of its own; the shared 55% is not borrowed
        self.assertIsNone(models["opus"]["rejected"])

    def test_an_active_rejection_is_the_models_unavailable_state(self):
        write_quota()
        route.note_limit("claude", engines.usage_limit_in("You've reached your Fable limit."))
        models = {item["model"]: item for item in self.claude()["models"]}
        self.assertIsNone(models["fable"]["seven_day"])
        self.assertIn("fable allowance exhausted; reset time unknown", models["fable"]["rejected"])

    def test_the_other_seat_lists_no_models_without_named_ones(self):
        write_quota()
        codex = next(seat for seat in route.seats() if seat["engine"] == "codex")
        self.assertEqual(codex["models"], [])

    def test_the_reading_time_is_the_models_freshness(self):
        write_quota(fable=100, at=time.time() - route.FRESH_SECONDS - 60)
        seat = self.claude()
        self.assertTrue(seat["quota"]["stale"])
        self.assertEqual(next(m for m in seat["models"] if m["model"] == "fable")["seven_day"], 100)
        self.assertEqual(datetime.fromtimestamp(seat["quota"]["at"], timezone.utc).year,
                         datetime.now(timezone.utc).year)
