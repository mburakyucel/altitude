"""The L3 coordinator: one serialized turn, with a resumable session per provider."""
from __future__ import annotations
import json
import threading
from pathlib import Path

from . import config, engines, route, state as S

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

ALLOWED_TOOLS = ("Read,Grep,Glob,Bash(alt *),Bash(git log*),Bash(git diff --stat*),Bash(gh pr view*),"
                 "Bash(gh pr list*),Bash(gh issue *),Bash(gh run *)")


def lock(project: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())


def info_path(project: str) -> Path:
    return config.project_dir(project) / "l3.json"


def info(project: str) -> dict:
    return S.read_json(info_path(project), {}) or {}


def save_info(project: str, data: dict) -> None:
    S.write_json(info_path(project), data)


def chat_log(project: str, role: str, text: str, **meta) -> None:
    path = config.project_dir(project) / "chat.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as stream:
        stream.write(json.dumps({"at": S.now(), "role": role, "text": text, **meta}, sort_keys=True) + "\n")


def chat_history(project: str, limit: int = 60) -> list[dict]:
    path = config.project_dir(project) / "chat.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def busy(project: str) -> bool:
    return lock(project).locked()


def queue_path(project: str) -> Path:
    return config.project_dir(project) / "l3-queue.jsonl"


def queue_message(project: str, text: str, *, trigger: str) -> None:
    """Leave one message for the project's L3; the server delivers it as a turn once L3 is free."""
    path = queue_path(project)
    with S.project_lock(project), open(path, "a") as stream:
        stream.write(json.dumps({"at": S.now(), "trigger": trigger, "text": text}, sort_keys=True) + "\n")


def deliver_queued(project: str) -> dict | None:
    """Run the oldest queued message as one L3 turn. Nothing runs while L3 is busy or no engine is available."""
    path = queue_path(project)
    if not path.exists() or busy(project) or not _select(project).get("engine"):
        return None
    with S.project_lock(project):
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if rows[1:]:
            S.atomic_write(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows[1:]))
        else:
            path.unlink()
    if not rows:
        return None
    return turn(project, rows[0]["text"], trigger=rows[0].get("trigger") or "queued")


def _header(project: str, trigger: str, fresh: bool) -> str:
    directory = config.project_dir(project)
    lines = [f"[altitude] project={project} trigger={trigger} state_file={directory / 'STATE.md'} "
             f"tasks_dir={directory / 'tasks'} repo={config.project_path(project)}"]
    if fresh:
        lines.append("[altitude] Fresh provider session. Read the state file first; it is durable project memory.")
    return "\n".join(lines) + "\n\n"


def _handoff(history: list[dict], engine: str, since: str | None) -> str:
    """Only the chat missed while another provider owned L3, never a synthetic full transcript replay."""
    missed = [item for item in history if item.get("at") and (not since or item["at"] > since)
              and item.get("role") in ("user", "assistant") and item.get("engine") != engine]
    if not missed:
        return ""
    lines = [f"- {item['role']}: {str(item.get('text') or '')[:800]}" for item in missed[-20:]]
    return "[altitude] Cross-provider chat missed by this session (oldest first):\n" + "\n".join(lines) + "\n\n"


def _select(project: str, engine: str | None = None) -> dict:
    """The engine for one turn: Burak's choice for this turn, else the project pin, else the quota route."""
    proj = config.project(project)
    forced = engine or proj.get("l3_engine")
    held = engines.usage_hold()
    if held and not forced:
        choice = route.pick_engine("l3", forced="codex")
        if choice.get("engine"):
            choice["why"] = f"Claude short-window hold until {held}; " + choice["why"]
        return choice
    choice = route.pick_engine("l3", forced=forced)
    if engine and choice.get("engine"):
        choice["why"] = "chosen by Burak for this turn"
    return choice


def turn(project: str, prompt: str, *, trigger: str = "chat", engine: str | None = None,
         on_text=None, on_start=None, model: str | None = None) -> dict:
    """Run one L3 turn. `engine` pins this turn; otherwise the project pin or the weekly quota selects
    a provider. Each provider resumes only its own transcript."""
    requested = engine
    with lock(project):
        choice = _select(project, requested)
        if not choice.get("engine"):
            return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
                    "turns": 0, "structured": None, "error": f"engine hold: {choice['why']}", "tools": [],
                    "skipped": False, "completed": False, "_turn_started_at": None, "routing": choice}
        engine = choice["engine"]
        proj = config.project(project)
        S.regen_state_md(project)
        inf = info(project)
        sessions = inf.setdefault("sessions", {})
        session = sessions.setdefault(engine, {})
        sid = session.get("session_id")
        over = (session.get("context_percent") or 0) >= config.CONTEXT_LINES[engine][1] * 100
        fresh = not sid or session.get("rotate_next", False) or over
        if fresh and sid:
            S.project_log(project, "l3-rotate", engine=engine, old=sid,
                          reason=session.get("rotate_reason") or "context threshold")
            session.update({"session_id": None, "rotate_next": False, "rotate_reason": None,
                            "context_percent": 0, "rotated_from": sid, "rotated_at": S.now()})
            inf.update({"session_id": None, "rotate_next": False, "rotate_reason": None,
                        "context_percent": 0, "rotated_from": sid, "rotated_at": session["rotated_at"]})
            save_info(project, inf)
            sid = None
        history = chat_history(project, 60)
        handoff = _handoff(history, engine, session.get("last_turn"))
        turn_started_at = S.now()
        chat_log(project, "user", prompt, trigger=trigger, engine=engine, at=turn_started_at)
        if engine == "codex":
            res = _codex_turn(project, prompt, trigger, turn_started_at, choice, inf, session, fresh,
                              handoff, model=model, on_start=on_start)
        else:
            text = _header(project, trigger, fresh) + handoff + prompt
            res = engines.claude_print(
                text, cwd=config.project_path(project), resume=None if fresh else sid,
                persona=config.PERSONAS / "l3.md", allowed_tools=ALLOWED_TOOLS, permission_mode="auto",
                model=model or proj.get("l3_model") or config.MODELS["l3"], on_text=on_text, on_start=on_start,
                extra_env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project,
                           "ALTITUDE_HOME": str(config.ROOT)})
            res.update({"skipped": False, "_turn_started_at": turn_started_at, "engine": "claude",
                        "routing": choice})
            # A provider-limit result can follow tool side effects. Never replay such a turn automatically elsewhere.
            if (res.get("limited") and not res.get("text") and not res.get("tools")
                    and not requested and not proj.get("l3_engine")):
                fallback = route.pick_engine("l3", forced="codex")
                if fallback.get("engine"):
                    fallback["why"] = f"Claude window closed before producing output; {fallback['why']}"
                    codex_session = sessions.setdefault("codex", {})
                    codex_handoff = _handoff(history, "codex", codex_session.get("last_turn"))
                    return _codex_turn(project, prompt, trigger, turn_started_at, fallback, inf, codex_session,
                                       not codex_session.get("session_id"), codex_handoff, model=None,
                                       on_start=on_start)
            if res.get("error") and not res.get("session_id"):
                chat_log(project, "error", res["error"], trigger=trigger, engine="claude")
                return res
            pct = engines.context_percent(res.get("context_tokens", 0), "claude")
            _save_session(inf, session, "claude", res.get("session_id"), pct, fresh,
                          res.get("cost", 0.0), res.get("usage") or {}, choice)
            save_info(project, inf)
            chat_log(project, "assistant", res.get("text") or (res.get("error") or ""), trigger=trigger,
                     engine="claude", context_percent=pct, turns=res.get("turns"),
                     tools=(res.get("tools") or [])[:40])
            S.regen_state_md(project)
            res.update({"context_percent": pct, "completed": True})
        return res


def _save_session(inf: dict, session: dict, engine: str, sid: str | None, pct: float,
                  fresh: bool, cost: float, usage: dict, choice: dict) -> None:
    act = config.CONTEXT_LINES[engine][1] * 100
    session.update({"session_id": sid, "turns": (0 if fresh else int(session.get("turns") or 0)) + 1,
                    "context_percent": pct, "last_turn": S.now(), "last_cost": cost,
                    "started": session.get("started") if not fresh else S.now(),
                    "context_state": engines.context_state(pct, engine), "rotate_next": pct >= act,
                    "rotate_reason": f"context {pct}% ≥ act line {int(act)}%" if pct >= act else None,
                    "usage": usage})
    inf.update({"engine_last": engine, "session_id": sid, "context_percent": pct,
                "turns": session["turns"], "last_turn": session["last_turn"], "last_cost": cost,
                "routing": choice, "rotate_next": session["rotate_next"],
                "rotate_reason": session["rotate_reason"]})


def _codex_turn(project: str, prompt: str, trigger: str, turn_started_at: str, choice: dict,
                inf: dict, session: dict, fresh: bool, handoff: str, *, model: str | None, on_start=None) -> dict:
    """One Codex L3 turn from a disposable runtime directory: the same persona and `alt` door as Claude, inside
    Codex's own sandbox (writes only there and to the Altitude home; the checkout is readable)."""
    proj = config.project(project)
    sid = None if fresh else session.get("session_id")
    body = _header(project, trigger, fresh) + handoff + prompt
    if fresh:
        body = ((config.PERSONAS / "l3.md").read_text() + "\n\n"
                + f"[altitude] Engine: Codex — {choice['why']}.\n\n" + body)
    runtime = config.project_dir(project) / "l3-codex-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    S.project_log(project, "l3-codex", reason=choice["why"], trigger=trigger, resume=bool(sid))
    result = engines.codex_exec(
        body, cwd=runtime, timeout=config.L3_CODEX_TURN_TIMEOUT, model=model or proj.get("l3_codex_model"),
        effort=config.CODEX_EFFORT.get("l3"), resume=sid, on_start=on_start,
        extra_env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT)})
    reported_sid = result.get("reported_session_id") or result.get("session_id")
    identity_error = None
    if not reported_sid:
        identity_error = "Codex L3 turn did not report a thread identity"
    elif sid and reported_sid != sid:
        identity_error = f"Codex L3 resume returned a different thread than {sid}"
    usage = result.get("usage") or {}
    tokens = int(usage.get("input_tokens", 0) or 0)
    out = {"text": str(result.get("text") or ""), "session_id": reported_sid or sid or "",
           "usage": usage, "context_tokens": tokens, "cost": 0.0, "turns": 1, "structured": None,
           "error": identity_error or result.get("error"), "tools": [], "skipped": False, "completed": False,
           "_turn_started_at": turn_started_at, "engine": "codex", "routing": choice}
    pct = engines.context_percent(tokens, "codex") if tokens else 0.0
    if identity_error or (result.get("error") and not out["text"]):
        if reported_sid and not identity_error:
            _save_session(inf, session, "codex", reported_sid, pct, fresh, 0.0, usage, choice)
            save_info(project, inf)
        chat_log(project, "error", f"codex L3 turn failed: {out['error']}", trigger=trigger, engine="codex")
        return out
    _save_session(inf, session, "codex", out["session_id"], pct, fresh, 0.0, usage, choice)
    save_info(project, inf)
    chat_log(project, "assistant", out["text"], trigger=trigger, engine="codex",
             context_percent=pct, cache_tokens=usage.get("cached_input_tokens"))
    S.regen_state_md(project)
    out.update({"context_percent": pct, "completed": True})
    return out


def reset(project: str, reason: str = "manual") -> None:
    inf = info(project)
    sessions = inf.setdefault("sessions", {})
    engine = inf.get("engine_last") or "claude"
    sessions.setdefault(engine, {}).update({"rotate_next": True, "rotate_reason": reason})
    inf.update({"rotate_next": True, "rotate_reason": reason})
    save_info(project, inf)
