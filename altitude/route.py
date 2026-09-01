"""Small, auditable engine selection shared by L2, L3, and optional helpers.

Routing is weekly-first because the weekly allowance is the scarce resource. A
short window is only an availability signal: it can rule an engine out, but it
never makes an engine with less weekly headroom look preferable. The raw quota
evidence travels with every decision so a task never silently changes providers
between brief creation and launch.
"""
from __future__ import annotations
from datetime import datetime, timezone

from . import config, state as S

QUOTA_CODEX = "quota-codex.json"
WEEK_MINUTES = 7 * 24 * 60
SHORT_MINUTES = 5 * 60


def quota_codex() -> dict:
    p = config.MONITOR_DIR / QUOTA_CODEX
    if not p.exists():
        return {"known": False}
    data = S.read_json(p, {}) or {}
    fresh = False
    try:
        observed = datetime.fromisoformat(str(data.get("read_at")))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        fresh = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= 1800
    except (TypeError, ValueError):
        pass
    return {**data, "known": bool(data.get("known")) and fresh,
            **({"why": "Codex quota snapshot is stale or undated"} if data.get("known") and not fresh else {})}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= 100 else None


def _codex_window(data: dict, minutes: int) -> dict | None:
    """Return only an explicitly identified duration; never guess primary/secondary semantics."""
    for prefix in ("primary", "secondary"):
        try:
            duration = int(data.get(f"{prefix}_window_minutes"))
        except (TypeError, ValueError, OverflowError):
            continue
        used = _number(data.get(f"{prefix}_used"))
        if duration == minutes and used is not None:
            return {"used": used, "resets": data.get(f"{prefix}_resets"), "minutes": duration}
    return None


def quota_snapshot() -> dict:
    """Normalize comparable windows while retaining the provider's raw observation."""
    from . import engines
    from .monitor import quota

    claude_raw = quota() or {"known": False}
    codex_raw = quota_codex() or {"known": False}
    claude_weekly = _number(claude_raw.get("seven_day")) if claude_raw.get("known") else None
    claude_short = _number(claude_raw.get("five_hour")) if claude_raw.get("known") else None
    claude_hold = engines.usage_hold()
    if claude_hold:
        claude_short = 100.0
    codex_week = _codex_window(codex_raw, WEEK_MINUTES) if codex_raw.get("known") else None
    codex_short = _codex_window(codex_raw, SHORT_MINUTES) if codex_raw.get("known") else None
    return {
        "claude": {
            "weekly_used": claude_weekly,
            "short_used": claude_short,
            "weekly_resets": claude_raw.get("seven_day_resets"),
            "short_resets": claude_hold or claude_raw.get("five_hour_resets"),
            "observed_at": claude_raw.get("at"),
            "raw": claude_raw,
        },
        "codex": {
            "weekly_used": codex_week.get("used") if codex_week else None,
            "short_used": codex_short.get("used") if codex_short else None,
            "weekly_resets": codex_week.get("resets") if codex_week else None,
            "short_resets": codex_short.get("resets") if codex_short else None,
            "observed_at": codex_raw.get("read_at"),
            "raw": codex_raw,
        },
    }


def _unavailable(observation: dict) -> str | None:
    weekly, short = observation.get("weekly_used"), observation.get("short_used")
    if weekly is not None and weekly >= 100:
        return "weekly window exhausted"
    if short is not None and short >= 100:
        return "short window exhausted"
    return None


def _default(role: str) -> str:
    return config.PRIMARY_DEFAULT_ENGINE if role in ("l2", "l3") else config.L1_DEFAULT_ENGINE


def pick_engine(role: str, *, forced: str | None = None, other_than: str | None = None) -> dict:
    """Return a persisted-ready ``{engine, why, quota}`` decision.

    ``other_than`` remains for call compatibility but never overrides quota:
    independent review is useful, but not worth draining the scarcer weekly seat.
    """
    if forced and forced not in config.ENGINES:
        raise ValueError(f"engine must be one of {config.ENGINES}, not {forced!r}")
    quota = quota_snapshot()
    unavailable = {engine: _unavailable(quota[engine]) for engine in config.ENGINES}
    if forced:
        if unavailable[forced]:
            return {"engine": None, "why": f"forced {forced} is unavailable: {unavailable[forced]}",
                    "quota": quota, "requested_engine": forced}
        return {"engine": forced, "why": "forced by task or project policy", "quota": quota}

    available = [engine for engine in config.ENGINES if unavailable[engine] is None]
    if not available:
        why = "; ".join(f"{engine}: {unavailable[engine]}" for engine in config.ENGINES)
        return {"engine": None, "why": f"no engine available ({why})", "quota": quota}
    if len(available) == 1:
        engine = available[0]
        other = next(item for item in config.ENGINES if item != engine)
        return {"engine": engine, "why": f"{other} unavailable: {unavailable[other]}", "quota": quota}

    weekly = {engine: quota[engine].get("weekly_used") for engine in available}
    comparable = [engine for engine in available if weekly[engine] is not None]
    default = _default(role)
    if len(comparable) == 2:
        engine = min(comparable, key=lambda item: (weekly[item], item != default))
        why = (f"more weekly headroom: claude 7d {weekly['claude']:.1f}% used vs "
               f"codex 7d {weekly['codex']:.1f}% used")
    elif len(comparable) == 1:
        known = comparable[0]
        engine = default if default in available else known
        why = (f"weekly quota is not comparable ({known} {weekly[known]:.1f}% used; "
               f"other unknown) → default policy {engine}")
    else:
        engine = default if default in available else available[0]
        why = f"weekly quotas unknown or incomparable → default policy {engine}"
    if other_than and engine == other_than:
        why += "; reviewer stayed on quota-selected engine instead of forcing provider diversity"
    return {"engine": engine, "why": why, "quota": quota}
