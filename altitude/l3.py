"""The L3 session — headless, resumable, driven by the server, one turn at a time (decision 16)."""
from __future__ import annotations
import json
import threading
from pathlib import Path

from . import config, engines, state as S

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

ALLOWED_TOOLS = ("Read,Grep,Glob,Bash(alt *),Bash(git log*),Bash(git diff --stat*),Bash(gh pr view*),"
                 "Bash(gh pr list*),Bash(gh issue *),Bash(gh run *),Agent")


def lock(project: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())


def info_path(project: str) -> Path:
    return config.project_dir(project) / "l3.json"


def info(project: str) -> dict:
    return S.read_json(info_path(project), {}) or {}


def save_info(project: str, d: dict) -> None:
    S.write_json(info_path(project), d)


def chat_log(project: str, role: str, text: str, **meta) -> None:
    p = config.project_dir(project) / "chat.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"at": S.now(), "role": role, "text": text, **meta}, sort_keys=True) + "\n")


def chat_history(project: str, limit: int = 60) -> list[dict]:
    p = config.project_dir(project) / "chat.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def busy(project: str) -> bool:
    return lock(project).locked()


def _header(project: str, trigger: str, fresh: bool) -> str:
    d = config.project_dir(project)
    lines = [f"[altitude] project={project} trigger={trigger} state_file={d / 'STATE.md'} "
             f"tasks_dir={d / 'tasks'} repo={config.project_path(project)}"]
    if fresh:
        lines.append("[altitude] This is a fresh session (start or rotation). Read the state file first; it is your memory. "
                     "Do not re-ask what it already answers.")
    return "\n".join(lines) + "\n\n"


def turn(project: str, prompt: str, *, trigger: str = "chat", on_text=None, on_start=None,
         model: str | None = None, precheck=None) -> dict:
    """Run one L3 turn. Serialized per project. Handles start, resume, and rotation.

    `on_start(pid)` receives the turn subprocess's pid: it outlives an altd restart, so the caller can record
    it and tell an in-flight turn from a dead one afterwards (incident I-011)."""
    with lock(project):
        if precheck is not None and not precheck():
            return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
                    "turns": 0, "structured": None, "error": None, "tools": [], "skipped": True,
                    "_turn_started_at": None}
        proj = config.project(project)
        S.regen_state_md(project)
        inf = info(project)
        sid = inf.get("session_id")
        over = (inf.get("context_percent") or 0) >= config.CONTEXT_ACT * 100  # decision 12: checked at turn start, not only after
        if over and sid and not inf.get("rotate_next"):
            inf["rotate_reason"] = f"context {inf.get('context_percent')}% ≥ act line {int(config.CONTEXT_ACT * 100)}% at turn start"
        fresh = not sid or inf.get("rotate_next", False) or over
        if fresh and sid:
            S.project_log(project, "l3-rotate", old=sid, reason=inf.get("rotate_reason", "requested"))
            # the rotation is a decision, so it is persisted *before* the turn runs: it used to be saved only
            # when the turn returned, so an altd that restarted mid-turn read the old session id back and
            # rotated the very same session again, once per restart (I-011: three `l3-rotate old=e9aa9612`)
            inf.update({"session_id": None, "rotate_next": False, "rotate_reason": None, "context_percent": 0,
                        "rotated_from": sid, "rotated_at": S.now()})
            save_info(project, inf)
        persona = config.PERSONAS / "l3.md"
        turn_started_at = S.now()
        chat_log(project, "user", prompt, trigger=trigger, at=turn_started_at)
        held = engines.usage_hold()
        if proj.get("l3_engine") == "codex" or held:  # decision 56: the L3 does not stop when the Claude window is out
            return _codex_turn(project, prompt, trigger, persona, turn_started_at,
                               reason=f"Claude window exhausted until {held}" if held else "project pins l3_engine=codex")
        res = engines.claude_print(
            _header(project, trigger, fresh) + prompt, cwd=config.project_path(project),
            resume=None if fresh else sid, persona=persona, allowed_tools=ALLOWED_TOOLS,
            permission_mode="auto", model=model or proj.get("l3_model") or config.MODELS["l3"], on_text=on_text,
            on_start=on_start,
            extra_env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT)})
        res["skipped"] = False
        res["_turn_started_at"] = turn_started_at
        if res.get("limited"):  # the window closed under this very turn: run it again on Codex, now
            return _codex_turn(project, prompt, trigger, persona, turn_started_at, reason=f"Claude window exhausted until {res['limited']}")
        if res["error"] and not res["session_id"]:
            chat_log(project, "error", res["error"], trigger=trigger)  # a failed turn is not a turn: nothing saved
            return res
        pct = engines.context_percent(res["context_tokens"])
        inf.update({"session_id": res["session_id"], "turns": (0 if fresh else inf.get("turns", 0)) + 1,
                    "context_percent": pct, "last_turn": S.now(), "last_cost": res["cost"],
                    "started": inf.get("started") if not fresh else S.now(),
                    "context_state": engines.context_state(pct),
                    "rotate_next": pct >= config.CONTEXT_ACT * 100,
                    "rotate_reason": f"context {pct}% ≥ act line {int(config.CONTEXT_ACT * 100)}% (decision 12)" if pct >= config.CONTEXT_ACT * 100 else None})
        save_info(project, inf)
        chat_log(project, "assistant", res["text"] or (res["error"] or ""), trigger=trigger, context_percent=pct,
                 turns=res["turns"], tools=res["tools"][:40])
        S.regen_state_md(project)
        res["context_percent"] = pct
        return res


def _codex_turn(project: str, prompt: str, trigger: str, persona: Path, turn_started_at: str, *, reason: str) -> dict:
    """One L3 turn on Codex (decision 56). No transcript: the state file and the recent chat are its memory, exactly as a
    fresh Claude session. The Claude session id is kept for when the window reopens; the turn is labelled `codex` in the
    chat log, the project log and the result — a degraded state that is visible, never a silent substitution (decision 36)."""
    inf = info(project)
    recent = chat_history(project, 20)
    history = "\n".join(f"- {m.get('role')}: {str(m.get('text') or '')[:600]}" for m in recent if m.get("role") in ("user", "assistant"))
    text = (persona.read_text() + "\n\n" + _header(project, trigger, True)
            + f"[altitude] Engine: Codex — {reason}. You have no transcript: the state file and the recent chat below are your memory. "
              "Same persona, same commands (`alt …`); the state and task files live under ALTITUDE_HOME.\n\n"
            + ("## Recent chat (oldest first)\n" + history + "\n\n" if history else "") + prompt)
    S.project_log(project, "l3-codex", reason=reason, trigger=trigger)
    res = engines.codex_exec(text, cwd=config.ROOT, sandbox="workspace-write", timeout=1200, effort=config.CODEX_EFFORT.get("l3"),
                             extra_config=[f'sandbox_workspace_write.writable_roots=["{config.ROOT}"]', "sandbox_workspace_write.network_access=true"],
                             extra_env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT)},
                             fault_context={"project": project})
    usage = res.get("usage") or {}
    tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
    out = {"text": res.get("text") or "", "session_id": inf.get("session_id") or "", "usage": usage, "context_tokens": tokens,
           "cost": 0.0, "turns": 1, "structured": None, "error": res.get("error"), "tools": [], "skipped": False,
           "_turn_started_at": turn_started_at, "engine": "codex", "degraded": reason}
    if res.get("error") and not out["text"]:
        chat_log(project, "error", f"codex L3 turn failed: {res['error']}", trigger=trigger, engine="codex")
        return out
    inf.update({"last_turn": S.now(), "last_cost": 0.0, "engine_last": "codex", "codex_turns": int(inf.get("codex_turns") or 0) + 1,
                "codex_context_percent": engines.context_percent(tokens, "codex")})
    save_info(project, inf)
    chat_log(project, "assistant", out["text"], trigger=trigger, engine="codex", degraded=reason)
    S.regen_state_md(project)
    return out


def reset(project: str, reason: str = "manual") -> None:
    inf = info(project)
    inf.update({"rotate_next": True, "rotate_reason": reason})
    save_info(project, inf)
