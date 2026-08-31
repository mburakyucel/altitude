"""Choose an engine for an optional L1 or reviewer from an override and available quota.

Never silent: every choice carries its reason, and it lands in the run record and the events log."""
from __future__ import annotations

from . import config, state as S

QUOTA_CODEX = "quota-codex.json"


def quota_codex() -> dict:
    """The Codex seat's window, when a reader has written `monitor/quota-codex.json` ({known, primary_used, ...}); else unknown."""
    p = config.MONITOR_DIR / QUOTA_CODEX
    if not p.exists():
        return {"known": False}
    d = S.read_json(p, {}) or {}
    return {**d, "known": bool(d.get("known")) and d.get("primary_used") is not None}


def other(engine: str) -> str:
    return "claude" if engine == "codex" else "codex"


def pick_engine(role: str, *, forced: str | None = None, other_than: str | None = None) -> dict:
    """Return ``{engine, why}`` for an optional L1 or reviewer.

    A run-specific override wins. Otherwise quota headroom and the configured L1 default decide,
    with the reason recorded in the run.
    """
    if forced:
        if forced not in config.ENGINES:
            raise ValueError(f"engine must be one of {config.ENGINES}, not {forced!r}")
        return {"engine": forced, "why": "forced on the command line"}
    from .monitor import quota
    cl, cx = quota() or {}, quota_codex()
    cl_used = float(cl["five_hour"]) if cl.get("known") and cl.get("five_hour") is not None else None
    cx_used = float(cx["primary_used"]) if cx.get("known") else None
    reserve = config.QUOTA_RESERVE * 100
    if cl_used is not None and cx_used is not None:
        eng = "codex" if cx_used <= cl_used else "claude"
        why = f"more headroom: claude 5h {cl_used:.0f}% used vs codex {cx_used:.0f}%"
    elif cl_used is not None:
        eng = "codex" if cl_used >= reserve else config.L1_DEFAULT_ENGINE
        why = f"claude 5h {cl_used:.0f}% used ({'past' if cl_used >= reserve else 'under'} the {reserve:.0f}% reserve), codex unknown"
        if cl_used < reserve:
            why += f" → default policy {eng}"
    elif cx_used is not None:
        eng = "claude" if cx_used >= reserve else config.L1_DEFAULT_ENGINE
        why = f"codex {cx_used:.0f}% used ({'past' if cx_used >= reserve else 'under'} the {reserve:.0f}% reserve), claude unknown"
        if cx_used < reserve:
            why += f" → default policy {eng}"
    else:
        eng = config.L1_DEFAULT_ENGINE
        why = f"both quotas unknown → default policy {eng} (Burak 2026-08-30: Claude is the constrained seat)"
    if other_than and eng == other_than:  # a reviewer should not share the author's engine when the other one has room
        alt = other(eng)
        alt_used = {"claude": cl_used, "codex": cx_used}[alt]
        if alt_used is None or alt_used < reserve:
            eng, why = alt, f"reviewer takes the other engine from the author ({other_than}); " + why
    return {"engine": eng, "why": why}
