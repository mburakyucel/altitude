"""Small, auditable engine selection shared by L2 and L3.

Preference tiers choose eligible engine/model options; named weekly allowance
chooses within a tied tier. Short windows only rule exhausted seats out.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

from . import config, state as S

QUOTA_CODEX = "quota-codex.json"
QUOTA_CLAUDE = "quota-claude.json"
WEEK_MINUTES = 7 * 24 * 60
SHORT_MINUTES = 5 * 60
# A quota snapshot older than this is stale: the router refuses to route on it and the pages label it.
FRESH_SECONDS = 1800


def quota_file(engine: str) -> str:
    """The persisted seat reading the unattended refresh writes for this engine."""
    return {"claude": QUOTA_CLAUDE, "codex": QUOTA_CODEX}[engine]


def quota_codex() -> dict:
    """The persisted Codex seat reading. ``known`` requires a snapshot no older than 30 minutes; an
    older one keeps its figures and is marked ``stale`` so a page can show an old number as old."""
    p = config.MONITOR_DIR / QUOTA_CODEX
    if not p.exists():
        return {"known": False}
    data = S.read_json(p, {}) or {}
    fresh = False
    try:
        observed = datetime.fromisoformat(str(data.get("read_at")))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        fresh = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= FRESH_SECONDS
    except (TypeError, ValueError):
        pass
    return {**data, "known": bool(data.get("known")) and fresh,
            **({"stale": True, "why": "Codex quota snapshot is stale or undated"}
               if data.get("known") and not fresh else {})}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= 100 else None


def _codex_window(data: dict, minutes: int) -> float | None:
    """Percent used of an explicitly identified window; never guess primary/secondary semantics."""
    for prefix in ("primary", "secondary"):
        try:
            duration = int(data.get(f"{prefix}_window_minutes"))
        except (TypeError, ValueError, OverflowError):
            continue
        used = _number(data.get(f"{prefix}_used"))
        if duration == minutes and used is not None:
            return used
    return None


def _readings() -> dict[str, dict]:
    """Each configured engine's seat reading, whole. This mapping of engine to reading is the seam:
    it is written here once and nowhere else, so adding or dropping an engine edits this module."""
    from .monitor import quota
    return {"claude": quota() or {}, "codex": quota_codex()}


def _routed_models(engine: str) -> list[str]:
    """Named models any project's L2/L3 routing can launch on this engine, in first-seen order."""
    names = []
    for project in config.load_projects().values():
        for role in ("l3", "l2"):
            choice = config.role_choice(role, project) or {}
            if choice.get("engine") == engine:
                names.append(choice.get("model") or config.default_model(role, engine, project))
            for tier in config.role_routing(role, project):
                for option in tier:
                    if option["engine"] == engine:
                        names.append(option.get("model") or config.default_model(role, engine, project))
            if role == "l2" and project.get("l2_engine") == engine:
                names.append(config.default_model(role, engine, project))
    return list(dict.fromkeys(name for name in names if name))


def _models(engine: str, reading: dict) -> list[dict]:
    """Each model with its own evidence: the reading's model-specific row, if any, and an active
    rejection. Shared account windows never stand in for a model that has no row of its own."""
    rows = {row["model"] or row["label"]: {**row, "rejected": None} for row in reading.get("models") or []}
    for name in _routed_models(engine):
        key = config.model_family(name) or name
        row = rows.setdefault(key, {"model": key, "label": None, "seven_day": None, "seven_day_resets": None})
        row["rejected"] = row.get("rejected") or _rejected({"engine": engine, "model": name})
    return list(rows.values())


def seats() -> list[dict]:
    """One seat per configured engine, in the seam's order: ``{engine, label, quota, models}`` with the
    seat's reading passed through as the seat reports it and each routed model's own evidence. The
    Monitor renders these rows without knowing which reading belongs to which provider."""
    readings = _readings()
    return [{"engine": engine, "label": config.ENGINE_LABELS[engine], "quota": readings[engine],
             "models": _models(engine, readings[engine])}
            for engine in config.ENGINES]


def engine_readouts() -> list[dict]:
    """One row per configured engine for the shell's engine readout: display name, weekly percent used,
    whether the reading is current, and when it was taken (ISO). ``week`` is None with no reading at all;
    ``stale`` keeps an old figure and says so. The rail renders these rows without knowing which is which."""
    readings = _readings()
    claude, codex = readings["claude"], readings["codex"]
    at = claude.get("at")
    weeks = {
        "claude": (_number(claude.get("seven_day")),
                   datetime.fromtimestamp(at, timezone.utc).isoformat() if isinstance(at, (int, float)) else None,
                   claude),
        "codex": (_codex_window(codex, WEEK_MINUTES), codex.get("read_at"), codex),
    }
    rows = []
    for engine in config.ENGINES:
        week, observed, data = weeks[engine]
        rows.append({"engine": engine, "label": config.ENGINE_LABELS[engine], "week": week,
                     "known": bool(data.get("known")), "stale": bool(data.get("stale")), "at": observed})
    return rows


def _usage(readings: dict[str, dict] | None = None) -> dict[str, tuple[float | None, float | None]]:
    """Per engine: (weekly % used, short-window % used); None when unknown."""
    from . import engines
    readings = readings or _readings()
    claude, codex = readings["claude"], readings["codex"]
    claude_week = _number(claude.get("seven_day")) if claude.get("known") else None
    claude_short = 100.0 if engines.usage_hold() else (_number(claude.get("five_hour")) if claude.get("known") else None)
    codex_week = _codex_window(codex, WEEK_MINUTES) if codex.get("known") else None
    codex_short = _codex_window(codex, SHORT_MINUTES) if codex.get("known") else None
    return {"claude": (claude_week, claude_short), "codex": (codex_week, codex_short)}


def improvement_reserve() -> str | None:
    """Why an improvement review may not start: its engine's allowance is unknown or inside Altitude's reserve."""
    weekly, short = _usage()["claude"]
    if weekly is None or short is None:
        return "Claude's allowance is not known, so Altitude cannot keep its reserve."
    if weekly >= config.IMPROVEMENT_RESERVE:
        return "Claude's weekly allowance is inside Altitude's reserve."
    if short >= config.IMPROVEMENT_RESERVE:
        return "Claude's short-term allowance is inside Altitude's reserve."
    return None


def _unavailable(weekly: float | None, short: float | None) -> str | None:
    if weekly is not None and weekly >= 100:
        return "weekly window exhausted"
    if short is not None and short >= 100:
        return "short window exhausted"
    return None


def _epoch(value) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds") if isinstance(value, (int, float)) else None


def _resets(readings: dict[str, dict]) -> dict[str, tuple[str | None, str | None]]:
    """Per engine: (weekly reset, short-window reset) as ISO instants; None when not reported."""
    from . import engines
    claude, codex = readings["claude"], readings["codex"]

    def codex_reset(minutes: int) -> str | None:
        return next((codex.get(f"{prefix}_resets") for prefix in ("primary", "secondary")
                     if str(codex.get(f"{prefix}_window_minutes")) == str(minutes)), None)
    return {"claude": (_epoch(claude.get("seven_day_resets")), engines.usage_hold() or _epoch(claude.get("five_hour_resets"))),
            "codex": (codex_reset(WEEK_MINUTES), codex_reset(SHORT_MINUTES))}


def model_reading(reading: dict, model: str | None) -> dict | None:
    """The seat reading's own row for this model's family; None when the provider reported none."""
    family = config.model_family(model)
    return next((row for row in reading.get("models") or [] if family and row.get("model") == family), None)


def _model_exhausted(option: dict, readings: dict[str, dict]) -> tuple[str, str | None] | None:
    """A current model-specific reading at its limit, with its reset; the shared account windows say nothing about it."""
    reading = readings[option["engine"]]
    row = model_reading(reading, option.get("model")) if reading.get("known") else None
    used = _number(row.get("seven_day")) if row else None
    reset = row.get("seven_day_resets") if row else None
    if used is None or used < 100 or isinstance(reset, (int, float)) and reset <= time.time():
        return None
    until = _epoch(reset)
    return f"{row['label']} weekly allowance exhausted; " + (f"resets {until}" if until else "reset time unknown"), until


def _hold(option: dict, readings: dict[str, dict], *, retry_sign_in: bool = False) -> dict | None:
    """Why this option cannot start now: ``why``, the reported ``until`` and whether a usage ``limit`` holds it."""
    rejection = _rejection(option, retry_sign_in=retry_sign_in)
    if rejection:
        return {"why": rejection["why"] + ("" if rejection.get("until") else
                                           "; availability observation expires after 30 minutes"),
                "until": rejection.get("until"), "limit": bool(rejection.get("until") or "exhausted" in rejection["why"])}
    weekly, short = _usage(readings)[option["engine"]]
    window = _unavailable(weekly, short)
    if window:
        resets = _resets(readings)[option["engine"]]
        return {"why": window, "until": resets[0] if window.startswith("weekly") else resets[1], "limit": True}
    exhausted = _model_exhausted(option, readings)
    return {"why": exhausted[0], "until": exhausted[1], "limit": True} if exhausted else None


SWITCH_MARGIN = 15.0  # weekly points of extra headroom the other engine needs before a session moves


def option_key(option: dict) -> tuple:
    return option["engine"], option.get("model")


def option_label(option: dict) -> str:
    return option["engine"] + (":" + option["model"] if option.get("model") else ":default")


def _rejection_path(option: dict, scope: str, model: str | None = None):
    from hashlib import sha256
    model = (model or option.get("model")) if scope == "model" else None
    name = sha256(repr((option["engine"], model, scope)).encode()).hexdigest()[:24]
    return config.MONITOR_DIR / f"route-unavailable-{name}.json"


def note_rejection(option: dict, rejection: dict) -> None:
    """A confirmed provider denial is a temporary observation, never a subscription inference. A sign-in
    rejection keeps its own record, so withdrawing it never touches a usage limit on the same engine. That record
    is the engine's sign-in lapse and remembers who was told: it lasts until an engine call succeeds again, and
    evidence older than the observation window starts a new lapse."""
    scope = "sign-in" if rejection.get("sign_in") else rejection["scope"]
    path = _rejection_path(option, scope)
    previous = S.read_json(path, {}) or {} if scope == "sign-in" else {}
    told = previous.get("told", []) if _current(previous) else []
    S.write_json(path, {**rejection, "at": S.now(), **({"told": told} if told else {})})


def _current(data: dict) -> bool:
    now = datetime.now(timezone.utc)
    return bool(data) and (now < datetime.fromisoformat(data["until"]) if data.get("until") else
                           (now - datetime.fromisoformat(data["at"])).total_seconds() < FRESH_SECONDS)


def _active(data: dict) -> bool:
    return _current(data) and not data.get("withdrawn")


def _rejection(option: dict, *, retry_sign_in: bool = False) -> dict | None:
    # A usage limit names the model family ("fable"); a configured model id shares its exclusion.
    family = config.model_family(option.get("model"))
    paths = [*([] if retry_sign_in else [_rejection_path(option, "sign-in")]),
             _rejection_path(option, "engine"), _rejection_path(option, "model"),
             *([_rejection_path(option, "model", family)] if family and family != option.get("model") else [])]
    return next((data for data in map(lambda path: S.read_json(path, {}), paths) if _active(data)), None)


def _rejected(option: dict) -> str | None:
    data = _rejection(option)
    return data and data["why"] + ("" if data.get("until") else "; availability observation expires after 30 minutes")


def signed_out(engine: str) -> bool:
    """Whether the engine's sign-in rejection currently excludes it."""
    return _active(S.read_json(_rejection_path({"engine": engine}, "sign-in"), {}))


def retry_sign_in(engine: str) -> bool:
    """Withdraw the engine's sign-in rejection for an explicit operator Retry or Resume about to launch on it.

    Signing in cures it at once, unlike a usage window. Automatic retries still honor it; a credential that
    is still invalid records it again on the attempt, within the same lapse. True when a rejection was withdrawn."""
    path = _rejection_path({"engine": engine}, "sign-in")
    data = S.read_json(path, {})
    if not data or data.get("withdrawn"):
        return False
    S.write_json(path, {**data, "withdrawn": True})
    return True


def signed_in(engine: str) -> None:
    """A successful call on the engine ends its sign-in lapse."""
    _rejection_path({"engine": engine}, "sign-in").unlink(missing_ok=True)


def tell_sign_in(engine: str, project: str) -> bool:
    """True once per lapse and project: the first time this project's conversation learns of the lapse."""
    path = _rejection_path({"engine": engine}, "sign-in")
    data = S.read_json(path, {})
    if not data or project in data.get("told", []):
        return False
    S.write_json(path, {**data, "told": [*data.get("told", []), project]})
    return True


def note_limit(engine: str, limit: dict) -> None:
    note_rejection({"engine": engine, "model": limit.get("model")}, limit)


def choice_unavailable(role: str, choice: dict | None, project: dict | None = None) -> str | None:
    """Why a chosen engine/model cannot start now, so its control can say "Auto meanwhile"; None when it can."""
    from . import engines
    if not choice or not choice.get("engine"):
        return None
    option = {"engine": choice["engine"], "model": choice.get("model") or config.default_model(role, choice["engine"], project)}
    installed = engines.installation(option["engine"])
    if installed["available"] is False:
        return installed["why"]
    return (_hold(option, _readings()) or {}).get("why")


def resume_hold(engine: str, model: str | None) -> str | None:
    """Availability of a saved session, independent of fresh-task routing preferences."""
    from . import engines
    if engine not in config.ENGINES:
        return f"saved engine {engine} is not configured"
    installed = engines.installation(engine)
    if installed["available"] is False:
        return installed["why"]
    option = {"engine": engine, "model": model}
    return (_hold(option, _readings()) or {}).get("why")


def pick_task(project: dict, task: dict, *, excluded: tuple = ()) -> dict:
    """Fresh dispatch and its queue explanation share the same one-attempt target and pin policy."""
    if task.get("next_engine"):
        try:
            if task.get("routing_pinned") or config.pinned_option("l2", project, engine=task.get("engine"), model=task.get("model")):
                raise ValueError("handoff refuses an explicit task or project engine/model pin")
            tiers = [[option for option in tier if option["engine"] == task["next_engine"]]
                     for tier in config.role_routing("l2", project)]
            if not any(tiers):
                raise ValueError("handoff target is not in the project's configured routing options")
            project = {**project, "routing": [tier for tier in tiers if tier]}
            selection = task.get("next_selection")
            if selection is not None:
                if (not isinstance(selection, dict) or set(selection) != {"model", "effort"}
                        or not selection["model"]):
                    raise ValueError("handoff requires a resolved model and effort")
                config.validate_choice({"engine": task["next_engine"], **selection})
                choice = pick_engine("l2", forced=task["next_engine"], model=selection["model"],
                                     effort=selection["effort"] or "native", project=project,
                                     excluded=excluded, steer=False)
                # A confirmed attempt selection is not a permanent task/project policy pin.
                return {**choice, "pinned": False}
        except ValueError as exc:
            return {"engine": None, "model": None, "why": str(exc)}
    return pick_engine("l2", forced=task.get("engine"), model=task.get("model"),
                       project=project, excluded=excluded, effort=task.get("effort"), steer=not task.get("next_engine"))


def pick_review(task: dict, project: dict, *, engine: str | None = None, model: str | None = None) -> dict:
    """Prefer an eligible alternate seat, then a separate invocation on the owner's seat.

    An explicit ``engine``/``model`` selection is the only candidate: the same eligibility, quota and
    rejection checks apply, and an unavailable selection is refused rather than substituted.
    """
    from . import engines
    owner = task.get("l2_engine")
    empty = {"engine": None, "model": None, "label": None, "allowance_known": False,
             "same_engine": False, "fallback_reason": ""}
    if owner not in config.ENGINES:
        return {**empty, "why": "The owner's actual engine is unavailable."}
    try:
        if engine or model:
            # The project's L2 engine pin never completes a review selection; a bare model must name its engine.
            selected = config.pinned_option("l2", {k: v for k, v in project.items() if k != "l2_engine"},
                                            engine=engine, model=model)
            choice = pick_engine("l2", forced=selected["engine"], model=selected["model"], project=project, effort="native",
                                 steer=False)
            capability = engines.review_capability(choice["engine"]) if choice.get("engine") else {}
            if not capability.get("available"):
                return {**empty, "why": "The selected reviewer is unavailable: " + (capability.get("why") or choice["why"])}
            same_engine = choice["engine"] == owner
            choice.update(_review_effort(choice["engine"]))
            return {**choice, "label": config.ENGINE_LABELS[choice["engine"]],
                    "allowance_known": all(value is not None for value in _usage()[choice["engine"]]),
                    "same_engine": same_engine, "fallback_reason": "Selected for this review." if same_engine else ""}
        pin = config.pinned_option("l2", project)
        tiers = [[pin]] if pin else config.role_routing("l2", project)
        options = [(o["engine"], o.get("model") or config.default_model("l2", o["engine"], project))
                   for tier in tiers for o in tier]
        fallback_reason = ""
        for same_engine in (False, True):
            excluded = tuple(option for option in options if (option[0] == owner) != same_engine)
            failures = []
            while True:
                choice = pick_engine("l2", project=project, excluded=excluded, effort="native", steer=False)
                if not choice.get("engine"):
                    why = " ".join([*failures, choice["why"]])
                    break
                capability = engines.review_capability(choice["engine"])
                if capability["available"]:
                    choice.update(_review_effort(choice["engine"]))
                    return {**choice, "label": config.ENGINE_LABELS[choice["engine"]],
                            "allowance_known": all(value is not None for value in _usage()[choice["engine"]]),
                            "same_engine": same_engine, "fallback_reason": fallback_reason}
                failures.append(f"{config.ENGINE_LABELS[choice['engine']]}: {capability['why']}")
                excluded += tuple(option for option in options if option[0] == choice["engine"])
            if not same_engine:
                fallback_reason = "No alternate engine is eligible. " + why
        return {**empty, "why": "No reviewer is available. " + why, "fallback_reason": fallback_reason}
    except ValueError as exc:
        return {**empty, "why": str(exc)}


def _review_effort(engine: str) -> dict:
    requested = config.model_default_pair(engine)["effort"]
    # Reviews keep their native default; owner/project task effort never leaks into the independent invocation.
    return {"requested_effort": requested, "effort": config.task_effort(engine, requested, role="l3")}


def pick_engine(role: str, *, forced: str | None = None, model: str | None = None,
                project: dict | None = None, current: str | None = None,
                current_model: str | None = None, excluded: tuple = (), effort: str | None = None,
                steer: bool = True, retry_sign_in: bool = False) -> dict:
    """One policy for fresh L2, L3 and explanations. Never used to change an L2 resume.

    An explicit task or turn engine/model wins outright. Otherwise the role's choice (New tasks for L2, the
    project's L3 choice) is tried first, ahead of the routing tiers; a project's Only engine for tasks keeps its engine and
    takes the choice's model and effort when they are on it. An explicit launch ``effort`` wins over the choice's,
    which wins over each option's per-role, per-engine project default; an engine that rejects the chosen effort
    uses its default. ``steer=False`` ignores the choice (reviewer selection, explicit engine handoff).
    ``retry_sign_in`` looks past sign-in rejections for an operator's explicit retry; ``signed_out`` lists the
    engines a sign-in rejection excluded, and ``held`` every skipped option in priority order with its reported
    reset (``until``) and whether a usage ``limit`` holds it.
    Unknown access/quota is eligible. Tiers outrank headroom; a tied tier compares only
    known named weekly windows. Continuity retains the current option inside that tier.
    """
    from . import engines
    project = project or {}
    choice = config.role_choice(role, project) if steer and not (forced or model) else None
    pin = config.pinned_option(role, project, engine=forced, model=model)
    if pin and choice and choice.get("engine") not in (None, pin["engine"]):
        choice = None
    chosen_tier = bool(choice and choice.get("engine"))
    first = [[{"engine": choice["engine"], "model": choice.get("model")}]] if chosen_tier else []
    tiers = first + ([[pin]] if pin else config.role_routing(role, project))
    chosen_effort = (choice or {}).get("effort")
    prefer = project.get(f"{role}_preference")
    readings = _readings()
    usage = _usage(readings)
    skipped, considered, lapsed, held = [], set(), [], []
    for priority, tier in enumerate(tiers, 1):
        available = []
        for configured in tier:
            option = {"engine": configured["engine"],
                      "model": configured.get("model") or config.default_model(role, configured["engine"], project)}
            if option_key(option) in considered:
                continue  # a lower tier never repeats an option a higher tier already resolved
            considered.add(option_key(option))
            engine = option["engine"]
            steered = chosen_effort in config.ENGINE_EFFORTS[engine] and (priority == 1 or not chosen_tier)
            try:
                requested = (effort if effort is not None else chosen_effort if steered
                             else config.default_effort(role, engine, project))
                option["effort"] = config.task_effort(engine, requested, role=role)
                option["requested_effort"] = requested
            except ValueError as exc:
                skipped.append(f"{option_label(option)} unavailable: {exc}")
                continue
            installed = engines.installation(engine)
            hold = ({"why": installed["why"]} if installed["available"] is False else
                    _hold(option, readings, retry_sign_in=retry_sign_in)
                    or ({"why": "already tried in this dispatch/turn"} if option_key(option) in excluded else None))
            if hold:
                skipped.append(f"{option_label(option)} unavailable: {hold['why']}")
                held.append({"engine": engine, "model": option["model"], "until": hold.get("until"),
                             "limit": bool(hold.get("limit"))})
                if not retry_sign_in and engine not in lapsed and signed_out(engine):
                    lapsed.append(engine)
            else:
                available.append(option)
        if not available:
            continue
        # Shared account readings stay shared: multiple models on a seat do not get fabricated allowances.
        weekly = {o["engine"]: usage[o["engine"]][0] for o in available}
        comparable = all(value is not None for value in weekly.values())
        chosen = min(available, key=lambda o: weekly[o["engine"]]) if comparable else available[0]
        why = (("more weekly headroom" if len(set(weekly.values())) > 1 else "equal/shared weekly headroom; configured tie order")
               + " (" + ", ".join(f"{e} 7d {w:.1f}% used" for e, w in weekly.items()) + ")"
               if comparable else "weekly quotas unknown or not comparable; configured tie order")
        previous = next((o for o in available if o["engine"] == current and
                         (current_model is None or o["model"] == current_model)), None)
        if previous and previous != chosen:
            lead = weekly[current] - weekly[chosen["engine"]] if comparable else None
            if lead is None or lead < SWITCH_MARGIN:
                why = (f"staying on {option_label(previous)}: " +
                       (f"{lead:.1f} points extra headroom is under the {SWITCH_MARGIN:.0f}-point switch margin"
                        if lead is not None else "weekly quotas are not comparable"))
                chosen = previous
        prefix = (("chosen for new tasks" if role == "l2" else "chosen for L3") if chosen_tier and priority == 1 else
                  "forced by task or project policy" if pin else
                  f"Auto tier {priority - chosen_tier}"
                  + (f" (prefers {config.ENGINE_LABELS[prefer]})" if prefer in config.ENGINES else ""))
        return {**chosen, "pinned": bool(pin), "signed_out": lapsed, "held": held, "why": f"{prefix}: {option_label(chosen)}; {why}; "
                + "installation found; model access unverified" + ("; skipped " + "; ".join(skipped) if skipped else "")}
    prefix = f"forced {option_label(pin)} is unavailable" if pin else "no configured option available"
    return {"engine": None, "model": None, "pinned": bool(pin), "signed_out": lapsed, "held": held, "why": prefix + ": " + "; ".join(skipped)
            + ". Install/authenticate an engine, wait for the reported quota reset, or change alt project set --routing with --reason."}
