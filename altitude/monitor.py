"""Context and quota per session."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import dispatch, engines, config, state as S


def sessions() -> list[dict]:
    """Everything the monitor dir knows: statusline snapshots (interactive sessions), L2 live rows, L3 infos."""
    out = []
    for p in sorted(config.MONITOR_DIR.glob("statusline-*.json")):
        d = S.read_json(p, {}) or {}
        out.append({"kind": "statusline", "session_id": p.stem.split("-", 1)[1], "at": d.get("at"),
                    "context_percent": ((d.get("context_window") or {}).get("used_percentage")),
                    "five_hour": ((d.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage"),
                    "seven_day": ((d.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage"),
                    "cwd": d.get("cwd") or (d.get("workspace") or {}).get("current_dir"), "model": (d.get("model") or {}).get("display_name")})
    for name in config.load_projects():
        inf = S.read_json(config.project_dir(name) / "l3.json", {}) or {}
        if inf:
            out.append({"kind": "l3", "project": name, "session_id": inf.get("session_id"), "context_percent": inf.get("context_percent"),
                    "engine": inf.get("engine_last") or "claude", "context_state": engines.context_state(
                        inf.get("context_percent"), inf.get("engine_last") or "claude"),
                        "at": inf.get("last_turn"), "turns": inf.get("turns"), "rotate_next": inf.get("rotate_next")})
        for t in S.list_tasks(name):
            if t["state"] in ("running", "blocked", "reported"):
                dispatch_id = t.get("dispatch_id")
                counts_p = config.MONITOR_DIR / f"counts-{name}--{dispatch_id}.json" if dispatch_id else None
                counts = S.read_json(counts_p, {}) if counts_p else {}
                counts = counts or {}
                live = S.read_json(config.MONITOR_DIR / f"live-{name}--{t['slug']}.json", {}) or {}
                l1_dir = S.task_dir(name, t["slug"]) / "l1"
                l1_runs = len(list(l1_dir.glob("*.json"))) if l1_dir.is_dir() else 0
                engine = t.get("l2_engine") or "claude"
                projection = None
                if engine == "codex":
                    projection = dispatch.owner_projection(name, t)
                    usage = ((t.get("active_operation") or {}).get("result") or {}).get("usage") or {}
                    tokens = int(usage.get("input_tokens", 0) or 0)
                    cp = engines.context_percent(tokens, "codex") if tokens else None
                else:
                    cp = transcript_context_percent(t.get("session_id"), config.project_path(name))
                out.append({"kind": "l2", "project": name, "slug": t["slug"], "session_id": t.get("session_id"),
                            "dispatch_id": t.get("dispatch_id"), "state": t["state"],
                            "agent": projection if engine == "codex" else live.get("agent"),
                            "l1_runs": l1_runs, "edits": counts.get("edits", 0),
                            "context_percent": cp, "engine": engine,
                            "context_state": engines.context_state(cp, engine)})
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
    """Latest 5h/7d numbers from any statusline snapshot younger than 30 minutes."""
    best = None
    for p in config.MONITOR_DIR.glob("statusline-*.json"):
        d = S.read_json(p, {}) or {}
        rl = d.get("rate_limits") or {}
        if not rl:
            continue
        ts = d.get("_at", 0)
        if best is None or ts > best[0]:
            best = (ts, rl)
    if not best or time.time() - best[0] > 1800:
        return {"known": False}
    rl = best[1]
    five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    return {"known": True, "five_hour": five.get("used_percentage"),
            "seven_day": seven.get("used_percentage"), "five_hour_resets": five.get("resets_at"),
            "seven_day_resets": seven.get("resets_at"), "at": best[0]}
