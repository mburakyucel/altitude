"""Small, auditable engine selection shared by L2 and L3.

Preference tiers choose eligible engine/model options; named weekly allowance
chooses within a tied tier. Short windows only rule exhausted seats out.
"""
from __future__ import annotations
from datetime import datetime, timezone

from . import config, state as S

QUOTA_CODEX = "quota-codex.json"
QUOTA_CLAUDE = "quota-claude.json"
WEEK_MINUTES = 7 * 24 * 60
SHORT_MINUTES = 5 * 60
# A quota snapshot older than this is stale: the router refuses to route on it and the pages label it.
FRESH_SECONDS = 1800


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


def seats() -> list[dict]:
    """One seat per configured engine, in the seam's order: ``{engine, label, quota}`` with the seat's
    reading passed through as the seat reports it. The Monitor renders these rows without knowing
    which reading belongs to which provider."""
    readings = _readings()
    return [{"engine": engine, "label": config.ENGINE_LABELS[engine], "quota": readings[engine]}
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


def _usage() -> dict[str, tuple[float | None, float | None]]:
    """Per engine: (weekly % used, short-window % used); None when unknown."""
    from . import engines
    readings = _readings()
    claude, codex = readings["claude"], readings["codex"]
    claude_week = _number(claude.get("seven_day")) if claude.get("known") else None
    claude_short = 100.0 if engines.usage_hold() else (_number(claude.get("five_hour")) if claude.get("known") else None)
    codex_week = _codex_window(codex, WEEK_MINUTES) if codex.get("known") else None
    codex_short = _codex_window(codex, SHORT_MINUTES) if codex.get("known") else None
    return {"claude": (claude_week, claude_short), "codex": (codex_week, codex_short)}


def _unavailable(weekly: float | None, short: float | None) -> str | None:
    if weekly is not None and weekly >= 100:
        return "weekly window exhausted"
    if short is not None and short >= 100:
        return "short window exhausted"
    return None


SWITCH_MARGIN = 15.0  # weekly points of extra headroom the other engine needs before a session moves


def option_key(option: dict) -> tuple:
    return option["engine"], option.get("model")


def option_label(option: dict) -> str:
    return option["engine"] + (":" + option["model"] if option.get("model") else ":default")


def _rejection_path(option: dict, scope: str):
    from hashlib import sha256
    model = option.get("model") if scope == "model" else None
    # Native defaults can differ by role (one launcher ignores user model configuration).
    role = option.get("role") if scope == "model" and model is None else None
    name = sha256(repr((option["engine"], model, role, scope)).encode()).hexdigest()[:24]
    return config.MONITOR_DIR / f"route-unavailable-{name}.json"


def note_rejection(option: dict, rejection: dict) -> None:
    """A confirmed provider denial is a temporary observation, never a subscription inference."""
    S.write_json(_rejection_path(option, rejection["scope"]), {**rejection, "at": S.now()})


def _rejected(option: dict) -> str | None:
    for scope in ("engine", "model"):
        data = S.read_json(_rejection_path(option, scope), {})
        if data:
            now = datetime.now(timezone.utc)
            active = (now < datetime.fromisoformat(data["until"]) if data.get("until") else
                      (now - datetime.fromisoformat(data["at"])).total_seconds() < FRESH_SECONDS)
            if active:
                return data["why"] + ("" if data.get("until") else "; availability observation expires after 30 minutes")
    return None


def note_limit(engine: str, limit: dict) -> None:
    note_rejection({"engine": engine, "model": limit.get("model")}, limit)


def resume_hold(engine: str, model: str | None) -> str | None:
    """Availability of a saved session, independent of fresh-task routing preferences."""
    from . import engines
    if engine not in config.ENGINES:
        return f"saved engine {engine} is not configured"
    installed = engines.installation(engine)
    if installed["available"] is False:
        return installed["why"]
    option = {"engine": engine, "model": model or config.default_model("l2", engine), "role": "l2"}
    return _rejected(option) or _unavailable(*_usage()[engine])


def pick_task(project: dict, task: dict, *, excluded: tuple = ()) -> dict:
    """Fresh dispatch and its queue explanation share the same one-attempt target and pin policy."""
    if task.get("next_engine"):
        try:
            if task.get("routing_pinned") or config.pinned_option("l2", project, engine=task.get("engine"), model=task.get("model")):
                raise ValueError("handoff refuses an explicit task or project engine/model pin")
            tiers = [[option for option in tier if option["engine"] == task["next_engine"]]
                     for tier in project.get("routing", config.AUTO_ROUTING)]
            if not any(tiers):
                raise ValueError("handoff target is not in the project's configured routing options")
            project = {**project, "routing": [tier for tier in tiers if tier]}
        except ValueError as exc:
            return {"engine": None, "model": None, "why": str(exc)}
    return pick_engine("l2", forced=task.get("engine"), model=task.get("model"),
                       project=project, excluded=excluded, effort=task.get("effort"))


def pick_engine(role: str, *, forced: str | None = None, model: str | None = None,
                project: dict | None = None, current: str | None = None,
                current_model: str | None = None, excluded: tuple = (), effort: str | None = None) -> dict:
    """One policy for fresh L2, L3 and explanations. Never used to change an L2 resume.

    Unknown access/quota is eligible. Tiers outrank headroom; a tied tier compares only
    known named weekly windows. Continuity retains the current option inside that tier.
    """
    from . import engines
    project = project or {}
    pin = config.pinned_option(role, project, engine=forced, model=model)
    tiers = [[pin]] if pin else project.get("routing", config.AUTO_ROUTING)
    usage = _usage()
    skipped = []
    for priority, tier in enumerate(tiers, 1):
        available = []
        for configured in tier:
            option = {"engine": configured["engine"],
                      "model": configured.get("model") or config.default_model(role, configured["engine"]), "role": role}
            engine = option["engine"]
            try:
                config.task_effort(engine, effort)
            except ValueError as exc:
                skipped.append(f"{option_label(option)} unavailable: {exc}")
                continue
            installed = engines.installation(engine)
            unavailable = ("already tried in this dispatch/turn" if option_key(option) in excluded else
                           installed["why"] if installed["available"] is False else
                           _rejected(option) or _unavailable(*usage[engine]))
            if unavailable:
                skipped.append(f"{option_label(option)} unavailable: {unavailable}")
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
        prefix = "forced by task or project policy" if pin else f"Auto tier {priority}"
        return {**chosen, "pinned": bool(pin), "why": f"{prefix}: {option_label(chosen)}; {why}; "
                + "installation found; model access unverified" + ("; skipped " + "; ".join(skipped) if skipped else "")}
    prefix = f"forced {option_label(pin)} is unavailable" if pin else "no configured option available"
    return {"engine": None, "model": None, "pinned": bool(pin), "why": prefix + ": " + "; ".join(skipped)
            + ". Install/authenticate an engine, wait for the reported quota reset, or change alt project set --routing with --reason."}
