"""Context and quota per session."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import engines, config, route, state as S


def sessions() -> list[dict]:
    """Everything the monitor dir knows: statusline snapshots (interactive sessions), L2 live rows, L3 infos."""
    out = []
    for p in sorted(config.MONITOR_DIR.glob("statusline-*.json")):
        d = S.read_json(p, {}) or {}
        out.append({"kind": "statusline", "session_id": p.stem.split("-", 1)[1], "at": d.get("_at"),
                    "context_percent": ((d.get("context_window") or {}).get("used_percentage")),
                    "five_hour": ((d.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage"),
                    "seven_day": ((d.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage"),
                    "cwd": d.get("cwd") or (d.get("workspace") or {}).get("current_dir"), "model": (d.get("model") or {}).get("display_name")})
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
                if engine == "codex":
                    cp = None  # Completed-turn consumption is not context-window occupancy.
                else:
                    cp = transcript_context_percent(t.get("session_id"), config.project_path(name))
                out.append({"kind": "l2", "project": name, "slug": t["slug"], "session_id": t.get("session_id"),
                            "attempt": t.get("attempt"), "state": t["state"], "agent": live.get("agent"),
                            "at": live.get("at"), "edits": counts.get("edits", 0),
                            "context_percent": cp, "engine": engine,
                            "model": t.get("engine_model"), "engine_reasoning_effort": t.get("engine_reasoning_effort"),
                            "context_state": engines.context_state(cp, engine)})
    for row in out:
        if not row.get("model"):
            row.pop("model", None)
    return out


def transcript_context_percent(session_id: str | None, cwd: Path | None) -> float | None:
    """Read the last assistant usage from the session transcript (~/.claude/projects/<slug>/<sid>.jsonl)."""
    if not session_id:
        return None
    base = Path.home() / ".claude" / "projects"
    cands = list(base.glob(f"*/{session_id}.jsonl"))
    if not cands:
        return None
    try:
        with open(cands[0], "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        message = o.get("message") or {}
        u = message.get("usage") if o.get("type") == "assistant" and message.get("model") != "<synthetic>" else None
        if u:
            tokens = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
            if tokens:
                return round(100.0 * tokens / config.CONTEXT_WINDOW, 1)
    return None


def quota() -> dict:
    """Latest 5h/7d numbers, reset times, and the epoch second the snapshot was written.

    ``known`` is the routing contract and stays exactly as strict: true only while the newest
    statusline snapshot is younger than route.FRESH_SECONDS. An older snapshot is not nothing, so it
    still returns its figures marked ``stale`` and the reader decides; no snapshot at all is the
    only unknown, and it carries ``why`` so a page can say what produces a reading.
    """
    best = None
    for p in config.MONITOR_DIR.glob("statusline-*.json"):
        d = S.read_json(p, {}) or {}
        rl = d.get("rate_limits") or {}
        if not rl:
            continue
        ts = d.get("_at", 0)
        if best is None or ts > best[0]:
            best = (ts, rl)
    if not best:
        return {"known": False,
                "why": "needs the statusline wrapper (alt install-statusline) and one interactive session"}
    at, rl = best
    five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    fresh = time.time() - at <= route.FRESH_SECONDS
    return {"known": fresh, "five_hour": five.get("used_percentage"),
            "seven_day": seven.get("used_percentage"), "five_hour_resets": five.get("resets_at"),
            "seven_day_resets": seven.get("resets_at"), "at": at,
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
