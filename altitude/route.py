"""Small, auditable engine selection shared by L2 and L3.

Routing is weekly-first because the weekly allowance is the scarce resource. A
short window is only an availability signal: it can rule an engine out, but it
never makes an engine with less weekly headroom look preferable. The decision
persists on the task as one reason string.
"""
from __future__ import annotations
from datetime import datetime, timezone

from . import config, state as S

QUOTA_CODEX = "quota-codex.json"
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


def _usage() -> dict[str, tuple[float | None, float | None]]:
    """Per engine: (weekly % used, short-window % used); None when unknown."""
    from . import engines
    from .monitor import quota
    claude, codex = quota() or {}, quota_codex()
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


def pick_engine(role: str, *, forced: str | None = None, current: str | None = None) -> dict:
    """Return ``{engine, why}``; ``engine`` is None when nothing is available.

    ``current`` is the engine that ran the previous turn of a long-lived session (L3). Staying keeps that
    transcript and its prompt cache warm, so the route moves only when ``current`` is unavailable or the other
    engine has SWITCH_MARGIN more weekly headroom; without it two close quotas would alternate every turn.
    """
    if forced and forced not in config.ENGINES:
        raise ValueError(f"engine must be one of {config.ENGINES}, not {forced!r}")
    usage = _usage()
    unavailable = {engine: _unavailable(*usage[engine]) for engine in config.ENGINES}
    if forced:
        if unavailable[forced]:
            return {"engine": None, "why": f"forced {forced} is unavailable: {unavailable[forced]}"}
        return {"engine": forced, "why": "forced by task or project policy"}

    available = [engine for engine in config.ENGINES if unavailable[engine] is None]
    if not available:
        why = "; ".join(f"{engine}: {unavailable[engine]}" for engine in config.ENGINES)
        return {"engine": None, "why": f"no engine available ({why})"}
    if len(available) == 1:
        engine = available[0]
        other = next(item for item in config.ENGINES if item != engine)
        return {"engine": engine, "why": f"{other} unavailable: {unavailable[other]}"}

    weekly = {engine: usage[engine][0] for engine in available}
    comparable = [engine for engine in available if weekly[engine] is not None]
    default = config.PRIMARY_DEFAULT_ENGINE
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
    if current in available and engine != current:
        lead = weekly[current] - weekly[engine] if current in comparable and engine in comparable else None
        if lead is None or lead < SWITCH_MARGIN:
            reason = (f"{engine} has {lead:.1f} points more weekly headroom, under the {SWITCH_MARGIN:.0f}-point switch margin"
                      if lead is not None else "weekly quotas are not comparable")
            engine, why = current, f"staying on {current}: {reason}"
    return {"engine": engine, "why": why}
