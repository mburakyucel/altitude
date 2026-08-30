"""Context and quota per session; the reserve line (decision 31)."""
from __future__ import annotations
import json
import time
from datetime import datetime
from pathlib import Path

from . import engines, config, state as S
from .quota_claude import QUOTA_CLAUDE

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def _percent(value) -> float | None:
    try:
        return min(100.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _l2_lifecycle(task: dict, agent: dict | None) -> str:
    """Normalize task/worker evidence so the UI never guesses who owns the next action."""
    state = task.get("state")
    if state == "reported":
        return "awaiting_closeout"
    if state == "blocked":
        if task.get("needs_user"):
            return "needs_user"
        if task.get("pending_resume"):
            return "resume_in_progress"
        if task.get("resume_after"):
            return "resume_scheduled"
        return "awaiting_l3_recovery"
    if state == "running":
        agent = agent if isinstance(agent, dict) else {}
        worker_state = str(agent.get("state") or "")
        worker_status = str(agent.get("status") or "")
        if not agent or worker_status == "exited" or worker_state in ("done", "failed", "stopped"):
            return "recovery_pending"
        return "active"
    return "unknown"


def sessions() -> list[dict]:
    """Everything the monitor dir knows: statusline snapshots (interactive sessions), L2 live rows, L3 infos."""
    out = []
    for p in sorted(config.MONITOR_DIR.glob("statusline-*.json")):
        d = S.read_json(p, {}) or {}
        try:
            snapshot_at = float(d.get("_at"))
        except (TypeError, ValueError):
            continue
        if not -300 <= time.time() - snapshot_at <= 1800:
            continue
        out.append({"kind": "statusline", "session_id": p.stem.split("-", 1)[1], "at": d.get("at") or snapshot_at,
                    "context_percent": _percent((d.get("context_window") or {}).get("used_percentage")),
                    "five_hour": _percent(((d.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage")),
                    "seven_day": _percent(((d.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage")),
                    "cwd": d.get("cwd") or (d.get("workspace") or {}).get("current_dir"), "model": (d.get("model") or {}).get("display_name")})
    for name in config.load_projects():
        inf = S.read_json(config.project_dir(name) / "l3.json", {}) or {}
        if inf:
            engine = inf.get("engine_last") or "claude"
            cp = codex_context_percent(inf.get("codex_session_id")) if engine == "codex" else _percent(inf.get("context_percent"))
            session_id = inf.get("codex_session_id") if engine == "codex" else inf.get("session_id")
            turns = inf.get("codex_turns") if engine == "codex" else inf.get("turns")
            out.append({"kind": "l3", "project": name, "session_id": session_id, "context_percent": cp,
                        "engine": engine, "context_state": engines.context_state(cp, engine),
                        "at": inf.get("last_turn"), "turns": turns,
                        "rotate_next": False if engine == "codex" else inf.get("rotate_next")})
        for t in S.list_tasks(name):
            if t["state"] in ("running", "blocked", "reported"):
                dispatch_id = t.get("dispatch_id")
                counts_p = config.MONITOR_DIR / f"counts-{name}--{dispatch_id}.json" if dispatch_id else None
                if not counts_p or not counts_p.exists():
                    counts_p = config.MONITOR_DIR / f"counts-{t.get('session_id')}.json"
                counts = S.read_json(counts_p, {}) or {}
                live = S.read_json(config.MONITOR_DIR / f"live-{name}--{t['slug']}.json", {}) or {}
                from . import dispatch
                engine = dispatch.task_l2_engine(name, t)
                if engine == "codex":
                    cp = codex_context_percent(t.get("session_id"))
                    # No fallback to saved `context_percent`: legacy records used cumulative billing
                    # and are wrong even when the resulting percentage happens to be below 100.
                else:
                    cp = transcript_context_percent(t.get("session_id"), config.project_path(name))
                agent = live.get("agent")
                out.append({"kind": "l2", "project": name, "slug": t["slug"], "session_id": t.get("session_id"),
                            "dispatch_id": t.get("dispatch_id"), "state": t["state"], "agent": agent,
                            "lifecycle": _l2_lifecycle(t, agent), "needs_user": bool(t.get("needs_user")),
                            "resume_after": t.get("resume_after"), "pending_resume": bool(t.get("pending_resume")),
                            "at": t.get("updated"),
                            "subagent_launches": counts.get("subagent_launches", 0), "edits": counts.get("edits", 0),
                            "cap": (t.get("envelope") or {}).get("subagent_launches"),
                            "context_percent": cp, "engine": engine, "context_state": engines.context_state(cp, engine)})
    return out


def codex_context_percent(session_id: str | None) -> float | None:
    """Current Codex context from the rollout's last token-count event, not lifetime usage."""
    if not session_id:
        return None
    cands = list(CODEX_SESSIONS.glob(f"**/*-{session_id}.jsonl"))
    if not cands:
        return None
    try:
        newest = max(cands, key=lambda p: (p.stat().st_mtime_ns, str(p)))
        with open(newest, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 1_000_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload") or {}
        info = payload.get("info") or {}
        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        usage = info.get("last_token_usage") or {}
        try:
            window = int(info.get("model_context_window") or 0)
            tokens = int(usage.get("total_tokens") or 0)
            if not tokens:
                tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            continue
        if tokens <= 0 or window <= 0:
            continue
        return min(100.0, max(0.0, round(100.0 * tokens / window, 1)))
    return None


def transcript_context_percent(session_id: str | None, cwd: Path | None) -> float | None:
    """Read the last assistant usage from the session transcript (~/.claude/projects/<slug>/<sid>.jsonl)."""
    if not session_id:
        return None
    base = Path.home() / ".claude" / "projects"
    cands = list(base.glob(f"*/{session_id}.jsonl"))
    if not cands:
        return None
    try:
        newest = max(cands, key=lambda p: (p.stat().st_mtime_ns, str(p)))
        with open(newest, "rb") as f:
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
        u = ((o.get("message") or {}).get("usage")) if o.get("type") == "assistant" else None
        if u:
            try:
                tokens = (int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))
                          + int(u.get("cache_creation_input_tokens", 0)))
            except (TypeError, ValueError):
                continue
            return min(100.0, max(0.0, round(100.0 * tokens / config.CONTEXT_WINDOW, 1)))
    return None


def quota() -> dict:
    """Latest statusline quota (<30 min), falling back to the persistent OAuth reader."""
    now = time.time()
    best = None
    for p in config.MONITOR_DIR.glob("statusline-*.json"):
        d = S.read_json(p, {}) or {}
        rl = d.get("rate_limits") or {}
        if not rl:
            continue
        try:
            ts = float(d.get("_at"))
        except (TypeError, ValueError):
            continue
        if best is None or ts > best[0]:
            best = (ts, rl)

    status_age = now - best[0] if best else None
    status_at = best[0] if best and -300 <= status_age <= 1800 else 0
    status = {}
    if status_at:
        rl = best[1]
        status = {
            "five_hour": _percent((rl.get("five_hour") or {}).get("used_percentage")),
            "seven_day": _percent((rl.get("seven_day") or {}).get("used_percentage")),
        }
    reader = S.read_json(config.MONITOR_DIR / QUOTA_CLAUDE, {}) or {}
    try:
        read_at = datetime.fromisoformat(str(reader.get("read_at"))).timestamp()
    except (TypeError, ValueError):
        read_at = 0
    reader_age = now - read_at if read_at else None
    reader_fresh = bool(reader.get("known") and read_at and -300 <= reader_age <= 1800)
    reader_values = {
        "five_hour": _percent(reader.get("five_hour")),
        "seven_day": _percent(reader.get("seven_day")),
    } if reader_fresh else {}

    five = status.get("five_hour") if status.get("five_hour") is not None else reader_values.get("five_hour")
    seven = status.get("seven_day") if status.get("seven_day") is not None else reader_values.get("seven_day")
    if five is not None or seven is not None:
        used_status = any(value is not None for value in status.values())
        used_reader = ((status.get("five_hour") is None and reader_values.get("five_hour") is not None)
                       or (status.get("seven_day") is None and reader_values.get("seven_day") is not None))
        sources = (["statusline"] if used_status else []) + (["oauth-reader"] if used_reader else [])
        out = {"known": True, "five_hour": five, "seven_day": seven,
               "at": max(status_at, read_at if used_reader else 0), "source": "+".join(sources)}
        if reader_fresh:
            for key in ("resets_at", "read_at"):
                if reader.get(key) is not None:
                    out[key] = reader[key]
        return out
    return {"known": False, "why": reader.get("why") or "no fresh Claude quota reading"}


def quota_hold() -> str | None:
    q = quota()
    if q.get("known") and q.get("five_hour") is not None and float(q["five_hour"]) >= config.QUOTA_RESERVE * 100:
        return f"quota reserve: 5h window at {q['five_hour']}% ≥ {int(config.QUOTA_RESERVE * 100)}%"
    return None
