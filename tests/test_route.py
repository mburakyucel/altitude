"""Weekly-first provider routing is explicit, comparable, and can return unavailable."""
import unittest

from tests.support import AltitudeCase
from altitude import config, engines, monitor, route


def codex(weekly, short=0):
    return {"known": True, "primary_used": weekly, "primary_window_minutes": route.WEEK_MINUTES,
            "secondary_used": short, "secondary_window_minutes": route.SHORT_MINUTES}


class TestPickEngine(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.private_ledgers()
        self.claude, self.codex = {"known": False}, {"known": False}
        self.patch(monitor, "quota", side_effect=lambda: self.claude)
        self.patch(route, "quota_codex", side_effect=lambda: self.codex)
        self.patch(engines, "usage_hold", return_value=None)
        self.installation = self.patch(engines, "installation", return_value={"available": None, "why": "test installation"})

    def test_override_is_visible_and_unavailable_override_does_not_fallback(self):
        choice = route.pick_engine("l2", forced="claude")
        self.assertEqual(choice["engine"], "claude")
        self.claude = {"known": True, "five_hour": 100, "seven_day": 10}
        choice = route.pick_engine("l2", forced="claude")
        self.assertIsNone(choice["engine"]); self.assertIn("forced claude", choice["why"])
        with self.assertRaises(ValueError):
            route.pick_engine("l2", forced="gemini")

    def test_unknown_quotas_use_codex_default_out_loud(self):
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], config.PRIMARY_DEFAULT_ENGINE)
        self.assertIn("unknown", choice["why"]); self.assertIn("configured tie order", choice["why"])

    def test_weekly_headroom_wins_over_short_window_percentage(self):
        self.claude = {"known": True, "five_hour": 5, "seven_day": 80}
        self.codex = codex(20, short=90)
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], "codex"); self.assertIn("weekly headroom", choice["why"])
        self.claude = {"known": True, "five_hour": 90, "seven_day": 10}
        self.codex = codex(60, short=5)
        self.assertEqual(route.pick_engine("l2")["engine"], "claude")

    def test_short_exhaustion_only_rules_out_that_provider(self):
        self.claude = {"known": True, "five_hour": 100, "seven_day": 5}
        self.codex = codex(80, short=20)
        choice = route.pick_engine("l2")
        self.assertEqual(choice["engine"], "codex"); self.assertIn("claude:opus unavailable", choice["why"])

    def test_opus_only_with_unknown_quota_and_missing_second_engine(self):
        self.installation.side_effect = lambda engine: {"available": False if engine == "codex" else None,
                                                        "why": "executable missing"}
        for role in ("l2", "l3"):
            choice = route.pick_engine(role, project={"routing": config.parse_routing("claude:opus")})
            self.assertEqual((choice["engine"], choice["model"]), ("claude", "opus"))
            self.assertIn("access unverified", choice["why"])

    def test_preferences_outrank_headroom_and_tie_order_is_configurable(self):
        project = {"routing": config.parse_routing("claude:opus>codex")}
        self.claude = {"known": True, "seven_day": 99}
        self.codex = codex(0)
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "opus")
        project["routing"] = config.parse_routing("claude:fable,codex>claude:opus")
        self.claude = {"known": False}
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "fable")
        self.claude = {"known": True, "seven_day": 99}
        self.assertEqual(route.pick_engine("l2", project=project)["engine"], "codex")

    def test_l2_preference_outranks_headroom_falls_back_and_leaves_l3_and_pins_alone(self):
        self.claude = {"known": True, "seven_day": 90}
        self.codex = codex(0)
        project = {"l2_preference": "claude"}
        self.assertEqual(config.role_routing("l2", project),
                         [[{"engine": "claude", "model": None}], [{"engine": "claude", "model": "opus"}],
                          [{"engine": "codex", "model": None}]])
        choice = route.pick_engine("l2", project=project)
        self.assertEqual((choice["engine"], choice["model"], choice["pinned"]), ("claude", "opus", False))
        self.assertIn("Auto tier 1 (prefers Claude)", choice["why"])
        self.assertEqual(route.pick_engine("l3", project=project)["engine"], "codex", "the L2 preference never reaches L3")
        self.assertEqual(route.pick_engine("l2", forced="codex", project=project)["engine"], "codex")
        self.assertEqual(route.pick_engine("l2", project={**project, "l2_engine": "codex"})["engine"], "codex")
        self.claude = {"known": True, "five_hour": 100, "seven_day": 90}
        choice = route.pick_engine("l2", project=project)
        self.assertEqual(choice["engine"], "codex"); self.assertIn("claude:opus unavailable", choice["why"])
        self.claude = {"known": True, "seven_day": 0}
        self.codex = codex(90)
        self.assertEqual(route.pick_engine("l2", project={"l2_preference": "codex"})["engine"], "codex")
        self.assertEqual(route.pick_engine("l2")["engine"], "claude", "Auto keeps weekly headroom")

    def test_l2_preference_yields_to_rejection_missing_install_and_handoff(self):
        project = {"l2_preference": "claude"}
        route.note_rejection({"engine": "claude", "model": "opus"}, {"scope": "model", "why": "model not accessible"})
        self.assertEqual(route.pick_engine("l2", project=project)["engine"], "codex")
        self.installation.side_effect = lambda engine: {"available": False if engine == "claude" else None, "why": "executable missing"}
        self.assertEqual(route.pick_engine("l2", project={"l2_preference": "claude", "routing": config.parse_routing("claude:fable,codex")})["engine"], "codex")
        self.installation.side_effect = None
        handoff = route.pick_task({"l2_preference": "claude"}, {"next_engine": "codex"})
        self.assertEqual(handoff["engine"], "codex", "a recorded handoff target outranks the preference")

    def test_l2_preference_reorders_custom_routing_without_adding_options(self):
        routing = config.parse_routing("claude:fable,codex>claude:opus")
        self.assertEqual(config.role_routing("l2", {"routing": routing, "l2_preference": "codex"}),
                         [[{"engine": "codex", "model": None}],
                          [{"engine": "claude", "model": "fable"}], [{"engine": "claude", "model": "opus"}]])
        only = config.parse_routing("claude:opus")
        self.assertEqual(config.role_routing("l2", {"routing": only, "l2_preference": "codex"}), only)
        self.assertEqual(config.role_routing("l3", {"routing": routing, "l2_preference": "codex"}), routing)

    def test_model_rejection_excludes_only_that_model_and_expires(self):
        project = {"routing": config.parse_routing("claude:fable>claude:opus")}
        option = {"engine": "claude", "model": "fable"}
        route.note_rejection(option, {"scope": "model", "why": "model not accessible"})
        self.assertEqual(route.pick_engine("l3", project=project)["model"], "opus")
        self.assertIsNone(route.pick_engine("l3", model="fable", project=project)["engine"])
        self.assertIsNone(route.pick_engine("l2", project=project, excluded=[("claude", "opus")])["engine"])
        self.patch(route.S, "now", return_value="2000-01-01T00:00:00+00:00")
        route.note_rejection(option, {"scope": "model", "why": "old rejection"})
        self.assertEqual(route.pick_engine("l3", project=project)["model"], "fable")

    def test_account_rejection_and_quota_exhaustion_cover_all_seat_models(self):
        project = {"routing": config.parse_routing("claude:fable>claude:opus>codex")}
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "engine", "why": "sign in"})
        self.assertEqual(route.pick_engine("l2", project=project)["engine"], "codex")
        route.note_limit("codex", engines._usage_limit("2099-01-01T00:00:00+00:00"))
        choice = route.pick_engine("l2", project=project)
        self.assertIsNone(choice["engine"])
        self.assertIn("sign in", choice["why"])
        self.assertIn("resets", choice["why"])
        self.assertIn("--routing", choice["why"])

    def test_unresolved_native_default_rejection_is_scoped_to_its_role(self):
        project = {"routing": config.parse_routing("codex")}
        choice = route.pick_engine("l2", project=project)
        route.note_rejection(choice, {"scope": "model", "why": "default model not accessible"})
        self.assertIsNone(route.pick_engine("l2", project=project)["engine"])
        self.assertEqual(route.pick_engine("l3", project=project)["engine"], "codex")

    def test_exhausted_model_allowance_leaves_other_models_and_engines_eligible(self):
        project = {"routing": config.parse_routing("claude:fable>claude:opus>codex")}
        limit = engines.usage_limit_in("You've reached your Fable limit. Switch to another model.")
        engines.record_usage_limit("claude", limit)
        self.assertFalse(engines.usage_limit_path().exists())
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "opus")
        self.assertEqual(route.pick_engine("l2", project=project, excluded=(("claude", "opus"),))["engine"], "codex")
        self.assertIn("reset time unknown", route.pick_engine("l2", forced="claude", model="fable")["why"])

    def test_no_entitlement_or_model_allowance_is_inferred_from_plan_or_account_meter(self):
        project = {"routing": config.parse_routing("claude:fable>claude:opus")}
        self.claude = {"known": True, "plan": "cheap", "seven_day": 10}
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "fable")
        self.claude["seven_day"] = 100
        self.assertIsNone(route.pick_engine("l2", project=project)["engine"])

    def test_continuity_within_tier_never_overrides_a_higher_available_tier(self):
        project = {"routing": config.parse_routing("claude:fable,claude:opus>codex")}
        choice = route.pick_engine("l3", project=project, current="claude", current_model="opus")
        self.assertEqual(choice["model"], "opus")
        project["routing"] = config.parse_routing("claude:fable>claude:opus")
        self.assertEqual(route.pick_engine("l3", project=project, current="claude", current_model="opus")["model"], "fable")

    def test_missing_installation_falls_through_default_tiers_and_pins_never_do(self):
        self.installation.side_effect = lambda engine: {"available": False if engine == "codex" else None,
                                                        "why": "executable missing"}
        self.assertEqual(route.pick_engine("l2")["model"], "opus")
        self.assertIsNone(route.pick_engine("l2", forced="codex")["engine"])
        self.installation.side_effect = lambda engine: {"available": False, "why": "executable missing"}
        self.assertIsNone(route.pick_engine("l2")["engine"])

    def test_default_tiers_give_l2_opus_and_l3_fable_with_opus_below_fable_only(self):
        self.installation.side_effect = lambda engine: {"available": engine != "codex" and None, "why": "executable missing"}
        l2, l3 = route.pick_engine("l2"), route.pick_engine("l3")
        self.assertEqual(((l2["engine"], l2["model"], l2["pinned"]), (l3["engine"], l3["model"])), (("claude", "opus", False), ("claude", "fable")))
        self.assertEqual(l2["why"].count("claude:opus"), 1, "the lower Opus tier repeats L2's resolved default and is not re-explained")
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "model", "why": "fable rejected"})
        self.assertEqual(route.pick_engine("l3")["model"], "opus")
        self.assertEqual(route.pick_engine("l2")["model"], "opus")
        self.claude = {"known": True, "five_hour": 0, "seven_day": 100}
        self.assertIsNone(route.pick_engine("l2")["engine"])
        self.assertEqual(route.pick_engine("l2")["why"].count("claude:opus"), 1)

    def test_fable_l2_only_by_explicit_task_model_or_project_default(self):
        pinned = route.pick_engine("l2", model="fable")
        self.assertEqual((pinned["engine"], pinned["model"], pinned["pinned"]), ("claude", "fable", True))
        preferred = route.pick_engine("l2", project={"l2_model": "fable"}, excluded=(("codex", None),))
        self.assertEqual((preferred["engine"], preferred["model"], preferred["pinned"]), ("claude", "fable", False))
        self.assertEqual(route.pick_engine("l2", project={"l2_model": "fable"})["engine"], config.PRIMARY_DEFAULT_ENGINE,
                         "a project default model is a preference for its engine, never an engine pin")
        self.assertIsNone(config.pinned_option("l2", {"l2_model": "fable", "l2_codex_model": "chosen"}))

    def test_project_default_model_fills_only_unqualified_options_per_engine(self):
        project = {"l2_model": "fable", "l2_codex_model": "chosen-model"}
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "chosen-model")
        self.assertEqual(route.pick_engine("l2", project=project, excluded=(("codex", "chosen-model"),))["model"], "fable")
        self.assertIsNone(route.pick_engine("l3", project=project)["model"], "L2 defaults never reach L3 options")
        self.assertEqual(route.pick_engine("l3", project=project, excluded=(("codex", None),))["model"], "fable")
        project["routing"] = config.parse_routing("claude:opus>codex")
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "opus")
        self.assertEqual(route.pick_engine("l2", forced="codex", project=project)["model"], "chosen-model")
        self.assertEqual(config.pinned_option("l2", {**project, "l2_engine": "claude"}), {"engine": "claude", "model": "fable"})

    def test_l3_model_keys_are_preferences_too_and_only_the_engine_key_pins(self):
        for project in ({"l3_model": "opus"}, {"l3_codex_model": "chosen-model"}):
            with self.subTest(project=project):
                self.assertIsNone(config.pinned_option("l3", project))
                choice = route.pick_engine("l3", project=project)
                self.assertEqual((choice["engine"], choice["pinned"]), (config.PRIMARY_DEFAULT_ENGINE, False))
        pinned = route.pick_engine("l3", project={"l3_engine": "claude", "l3_model": "opus"})
        self.assertEqual((pinned["engine"], pinned["model"], pinned["pinned"]), ("claude", "opus", True))

    def test_both_exhausted_returns_no_engine(self):
        self.claude = {"known": True, "five_hour": 100, "seven_day": 5}
        self.codex = codex(100, short=20)
        self.assertIsNone(route.pick_engine("l2")["engine"])

    def test_a_session_stays_on_its_engine_under_the_switch_margin(self):
        # Two close quotas would otherwise alternate every turn, paying a cold cache and a handoff each time.
        self.claude = {"known": True, "five_hour": 10, "seven_day": 20}
        self.codex = codex(30)
        self.assertEqual(route.pick_engine("l3")["engine"], "claude")
        choice = route.pick_engine("l3", current="codex")
        self.assertEqual(choice["engine"], "codex")
        self.assertIn("staying on codex", choice["why"]); self.assertIn("10.0 points", choice["why"])
        self.claude = {"known": False}
        choice = route.pick_engine("l3", current="claude")
        self.assertEqual(choice["engine"], "claude"); self.assertIn("not comparable", choice["why"])

    def test_a_session_moves_for_a_clear_lead_or_an_unavailable_engine(self):
        self.claude = {"known": True, "five_hour": 10, "seven_day": 20}
        self.codex = codex(40)
        choice = route.pick_engine("l3", current="codex")
        self.assertEqual(choice["engine"], "claude"); self.assertIn("more weekly headroom", choice["why"])
        self.codex = codex(30, short=100)
        self.assertEqual(route.pick_engine("l3", current="codex")["engine"], "claude")
        self.assertEqual(route.pick_engine("l3", forced="claude", current="codex")["engine"], "claude")


    def test_new_tasks_is_tried_first_and_routing_takes_over_while_it_is_unavailable(self):
        self.patch(config, "machine_settings", return_value={"new_tasks": {"engine": "claude", "model": "fable", "effort": "max"}})
        project = {"routing": config.parse_routing("codex>claude:opus")}
        choice = route.pick_engine("l2", project=project)
        self.assertEqual((choice["engine"], choice["model"], choice["effort"]), ("claude", "fable", "max"))
        self.assertIn("chosen for new tasks", choice["why"])
        self.assertIsNone(route.choice_unavailable("l2", config.machine_settings()["new_tasks"]))
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "model", "why": "model not accessible"})
        fallback = route.pick_engine("l2", project=project)
        self.assertEqual((fallback["engine"], fallback["effort"]), ("codex", "high"))
        self.assertIn("Auto tier 1", fallback["why"]); self.assertIn("claude:fable unavailable", fallback["why"])
        self.assertIn("model not accessible", route.choice_unavailable("l2", config.machine_settings()["new_tasks"]))
        self.assertEqual(project, {"routing": config.parse_routing("codex>claude:opus")})

    def test_l3_choice_is_per_project_and_new_tasks_never_steers_l3(self):
        self.patch(config, "machine_settings", return_value={"new_tasks": {"engine": "claude", "model": "fable"}})
        self.assertEqual(route.pick_engine("l3", project={"routing": config.parse_routing("codex")})["engine"], "codex")
        project = {"routing": config.parse_routing("claude:opus>codex"), "l3_choice": {"engine": "claude", "model": "sonnet"}}
        self.assertEqual(route.pick_engine("l3", project=project)["model"], "sonnet")
        self.assertIn("chosen for L3", route.pick_engine("l3", project=project)["why"])
        self.assertEqual(route.pick_engine("l2", project=project)["model"], "fable")

    def test_explicit_task_or_turn_selection_outranks_the_choice(self):
        self.patch(config, "machine_settings", return_value={"new_tasks": {"engine": "claude", "model": "fable", "effort": "max"}})
        self.assertEqual(route.pick_engine("l2", forced="codex")["engine"], "codex")
        explicit = route.pick_engine("l2", model="opus")
        self.assertEqual((explicit["model"], explicit["effort"]), ("opus", None))
        self.assertEqual(route.pick_engine("l2", effort="low")["effort"], "low")
        task = route.pick_task({}, {"engine": None, "model": None, "effort": "medium"})
        self.assertEqual((task["model"], task["effort"]), ("fable", "medium"))

    def test_only_engine_keeps_its_engine_and_takes_a_choice_made_on_it(self):
        settings = {"new_tasks": {"engine": "claude", "model": "fable", "effort": "max"}}
        self.patch(config, "machine_settings", side_effect=lambda: settings)
        same = route.pick_engine("l2", project={"l2_engine": "claude"})
        self.assertEqual((same["engine"], same["model"], same["effort"], same["pinned"]), ("claude", "fable", "max", True))
        other = route.pick_engine("l2", project={"l2_engine": "codex", "l2_codex_effort": "low"})
        self.assertEqual((other["engine"], other["effort"]), ("codex", "low"))
        route.note_rejection({"engine": "claude", "model": "fable"}, {"scope": "model", "why": "model not accessible"})
        defaults = route.pick_engine("l2", project={"l2_engine": "claude"})
        self.assertEqual((defaults["engine"], defaults["model"], defaults["effort"]), ("claude", "opus", None))
        self.installation.side_effect = lambda engine: {"available": False if engine == "claude" else None, "why": "missing"}
        self.assertIsNone(route.pick_engine("l2", project={"l2_engine": "claude"})["engine"])
        settings["new_tasks"] = {"effort": "ultra"}
        self.installation.side_effect = None
        auto = route.pick_engine("l2", project={"l2_engine": "codex"})
        self.assertEqual((auto["engine"], auto["effort"]), ("codex", "ultra"))

    def test_an_effort_an_engine_rejects_falls_back_to_its_default_and_never_skips_it(self):
        self.patch(config, "machine_settings", return_value={"new_tasks": {"effort": "ultra"}})
        project = {"routing": config.parse_routing("claude:opus>codex")}
        choice = route.pick_engine("l2", project=project)
        self.assertEqual((choice["engine"], choice["effort"]), ("claude", None))
        self.claude = {"known": True, "seven_day": 100}
        self.assertEqual(route.pick_engine("l2", project=project)["effort"], "ultra")

    def test_reviewer_selection_and_explicit_handoff_ignore_new_tasks(self):
        self.patch(config, "machine_settings", return_value={"new_tasks": {"engine": "claude", "model": "fable"}})
        self.patch(engines, "review_capability", return_value={"available": True, "why": ""})
        review = route.pick_review({"l2_engine": "claude"}, {})
        self.assertEqual(review["engine"], "codex")
        handoff = route.pick_task({}, {"next_engine": "codex"})
        self.assertEqual(handoff["engine"], "codex")

    def test_choice_cli_spelling_names_engine_model_and_effort(self):
        self.assertEqual(config.parse_choice("fable@high"), {"engine": "claude", "model": "fable", "effort": "high"})
        self.assertEqual(config.parse_choice("codex@ultra"), {"engine": "codex", "effort": "ultra"})
        self.assertEqual(config.parse_choice("codex:gpt-test"), {"engine": "codex", "model": "gpt-test"})
        self.assertEqual(config.parse_choice("@max"), {"effort": "max"})
        for bad in ("", "gpt-test", "claude@ultra", "gemini:x", "claude:two words"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.parse_choice(bad)


if __name__ == "__main__":
    unittest.main()
