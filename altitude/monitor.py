"""Context and quota per session."""
from __future__ import annotations
import time

from . import engines, config, route, state as S


def sessions() -> list[dict]:
    """The sessions Altitude runs: each project's L3 and its live task owners (L2)."""
    out = []
    for name in config.load_projects():
        inf = S.read_json(config.project_dir(name) / "l3.json", {}) or {}
        if inf:
            out.append({"kind": "l3", "project": name, "session_id": inf.get("session_id"), "context_percent": inf.get("context_percent"),
                    "model": inf.get("engine_model"), "engine_reasoning_effort": inf.get("engine_reasoning_effort"),
                    "engine": inf.get("engine_last") or "claude", "context_state": engines.context_state(
                        inf.get("context_percent"), inf.get("engine_last") or "claude"),
                        "at": inf.get("last_turn"), "turns": inf.get("turns"), "rotate_next": inf.get("rotate_next")})
        for t in S.list_tasks(name):
            if t["state"] in ("running", "blocked", "reported"):
                counts_p = S.counts_path(name, t)
                counts = (S.read_json(counts_p, {}) if counts_p else {}) or {}
                live = S.read_json(config.MONITOR_DIR / f"live-{name}--{t['slug']}.json", {}) or {}
                engine = t.get("l2_engine") or "claude"
                cp = ((t.get("token_usage") or {}).get("context") or {}).get("percent")
                out.append({"kind": "l2", "project": name, "slug": t["slug"], "session_id": t.get("session_id"),
                            "attempt": t.get("attempt"), "state": t["state"], "agent": live.get("agent"),
                            "token_usage": t.get("token_usage"),
                            "at": live.get("at"), "edits": counts.get("edits", 0),
                            "context_percent": cp, "engine": engine,
                            "model": t.get("engine_model"), "engine_reasoning_effort": t.get("engine_reasoning_effort"),
                            "context_state": engines.context_state(cp, engine)})
    for row in out:
        if not row.get("model"):
            row.pop("model", None)
    return out


def quota() -> dict:
    """The unattended seat reading; expired observations retain their figures as stale."""
    reading = S.read_json(config.MONITOR_DIR / route.QUOTA_CLAUDE, {}) or {}
    if not reading.get("known"):
        return reading or {"known": False, "why": "Waiting for the daemon's quota refresh"}
    fresh = 0 <= time.time() - reading.get("at", 0) <= route.FRESH_SECONDS
    return {**reading, "known": fresh,
            **({} if fresh else {"stale": True})}


def routing() -> list[dict]:
    """Which engine each role would get for a turn started now, with the router's own reason.

    Display only: one row per project and role, respecting each project's pins and tiers.
    ``engine`` is None when the router
    would find nothing available. Fresh-L2 reasons prefix the shared decision with the project name.
    """
    rows = []
    for name, project in config.load_projects().items():
        info = S.read_json(config.project_dir(name) / "l3.json", {}) or {}
        pin = config.project(name).get("l3_engine")
        pin = pin if pin in config.ENGINES else None
        current = info.get("engine_last")
        rows.append({"role": "l3", "project": name, "pin": pin, "current": current,
                     **route.pick_engine("l3", project=project, current=current,
                                         current_model=((info.get("sessions") or {}).get(current) or {}).get("launch_model"))})
        choice = route.pick_engine("l2", project=project)
        rows.append({"role": "l2", "project": name, "pin": project.get("l2_engine"), "current": None,
                     **choice, "why": f"Project {name}: {choice['why']}"})
    return rows
