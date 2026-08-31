"""Which engine runs a piece of work (decision 45): forced > role rule > remaining quota > default policy.

Never silent: every choice carries its reason, and it lands in the run record and the events log."""
from __future__ import annotations
import time
from datetime import datetime

from . import config, state as S

ROLE_ENGINES = {"l3": "claude", "proposal": "codex", "sizer": "codex"}
# L2 is Claude while it has room, then Codex; proposals and sizing are Codex; a critic is the other proposal engine.
QUOTA_CODEX = "quota-codex.json"


def quota_codex() -> dict:
    """The Codex seat's window, when a reader has written `monitor/quota-codex.json` ({known, primary_used, ...}); else unknown."""
    p = config.MONITOR_DIR / QUOTA_CODEX
    if not p.exists():
        return {"known": False}
    d = S.read_json(p, {}) or {}
    try:
        age = time.time() - datetime.fromisoformat(str(d.get("read_at"))).timestamp()
        fresh = -300 <= age <= 1800
    except (TypeError, ValueError):
        fresh = False
    known = bool(d.get("known")) and d.get("primary_used") is not None and fresh
    out = {**d, "known": known}
    if not known and not out.get("why"):
        out["why"] = "Codex quota reading is stale"
    return out


def other(engine: str) -> str:
    return "claude" if engine == "codex" else "codex"


def pick_l3_engine(*, forced: str | None = None, previous: str | None = None) -> dict:
    """Route L3 by weekly remaining-percentage ratio, with hysteresis to avoid ping-pong."""
    from . import engines
    held = engines.usage_hold()
    if held:
        return {"engine": "codex", "why": f"Claude window exhausted until {held}"}
    if forced:
        if forced not in config.ENGINES:
            raise ValueError(f"engine must be one of {config.ENGINES}, not {forced!r}")
        return {"engine": forced, "why": f"project pins l3_engine={forced}"}
    from .monitor import quota, quota_hold
    reserve = quota_hold()
    if reserve:
        return {"engine": "codex", "why": f"{reserve} → preserve Claude headroom with Codex L3"}
    claude, codex = quota() or {}, quota_codex()
    cl_used = claude.get("seven_day") if claude.get("known") else None
    cx_used = None
    if codex.get("known"):
        for prefix in ("primary", "secondary"):
            if codex.get(f"{prefix}_window_minutes") == 10080 and codex.get(f"{prefix}_used") is not None:
                cx_used = codex[f"{prefix}_used"]
                break
    if cl_used is None or cx_used is None:
        engine = previous if previous in config.ENGINES else "claude"
        return {"engine": engine, "why": f"weekly quota ratio unavailable → keep {engine} to avoid engine switching"}
    cl_remaining, cx_remaining = max(0.0, 100.0 - float(cl_used)), max(0.0, 100.0 - float(cx_used))
    ratio = float("inf") if cl_remaining == 0 and cx_remaining > 0 else (cx_remaining / cl_remaining if cl_remaining else 1.0)
    threshold = config.L3_CODEX_EXIT_RATIO if previous == "codex" else config.L3_CODEX_ENTER_RATIO
    engine = "codex" if ratio >= threshold else "claude"
    why = (f"weekly remaining capacity: codex {cx_remaining:.0f}% / claude {cl_remaining:.0f}% = {ratio:.2f}x; "
           f"{'stay on' if previous == engine else 'route to'} {engine} at {threshold:.2f}x threshold")
    return {"engine": engine, "why": why}


def pick_engine(role: str, *, forced: str | None = None, task: dict | None = None, other_than: str | None = None) -> dict:
    """{engine, why}. `forced` (command line) beats the task's `engine`, which beats the role rule; L1s and reviewers are
    then split by remaining quota, and when neither seat is readable the default policy decides — out loud."""
    if forced:
        if forced not in config.ENGINES:
            raise ValueError(f"engine must be one of {config.ENGINES}, not {forced!r}")
        return {"engine": forced, "why": "forced on the command line"}
    if task and task.get("engine"):
        return {"engine": task["engine"], "why": f"forced on the task ({task.get('slug')})"}
    if role == "l2":
        from . import engines
        held = engines.usage_hold()
        if held:
            return {"engine": "codex", "why": f"Claude window exhausted until {held} → Codex L2 (decision 56)"}
        from .monitor import quota_hold
        reserve = quota_hold()
        if reserve:
            return {"engine": "codex", "why": f"{reserve} → preserve Claude headroom with a Codex L2 (decision 56)"}
        return {"engine": "claude", "why": "Claude has room; L2 stays on Claude by design (decision 56)"}
    if role in ROLE_ENGINES:
        return {"engine": ROLE_ENGINES[role], "why": f"{role} is always {ROLE_ENGINES[role]} by design"}
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
