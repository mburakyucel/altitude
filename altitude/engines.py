"""Subscription CLIs driven headlessly (decision 1). Claude Code and Codex command builders + runners."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from . import config


def clean_env() -> dict:
    """Nested launches need CLAUDE* unset (verified); keep PATH sane for systemd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")
    return env


def claude_print(prompt: str, *, cwd: Path, resume: str | None = None, persona: Path | None = None,
                 allowed_tools: str | None = None, tools: str | None = None, permission_mode: str = "auto",
                 schema: Path | None = None, model: str | None = None, max_turns: int | None = None,
                 settings: Path | None = None, extra_env: dict | None = None, on_text=None,
                 timeout: int = config.L3_TURN_TIMEOUT) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error."""
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
           "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if tools is not None:
        cmd += ["--tools", tools]
    if schema:
        cmd += ["--json-schema", str(schema)]
    if model:
        cmd += ["--model", model]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if settings:
        cmd += ["--settings", str(settings)]
    if resume:
        cmd += ["--resume", resume]
    env = clean_env()
    env.update(extra_env or {})
    # prompt goes through stdin: --allowedTools is variadic and would swallow a positional prompt
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    stderr: list[str] = []
    drain = threading.Thread(target=lambda: stderr.append(proc.stderr.read() or ""), daemon=True)
    drain.start()
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    out = {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0,
           "turns": 0, "structured": None, "error": None, "tools": []}
    parts: list[str] = []
    try:
        for line in proc.stdout:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("session_id"):
                out["session_id"] = o["session_id"]
            typ = o.get("type")
            if typ == "stream_event":
                delta = (o.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    if on_text:
                        on_text(delta["text"])
            elif typ == "assistant":
                out["turns"] += 1
                msg = o.get("message") or {}
                u = msg.get("usage") or {}
                if u:
                    out["context_tokens"] = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
                for c in msg.get("content") or []:
                    if c.get("type") == "tool_use":
                        out["tools"].append(c.get("name"))
            elif typ == "result":
                out["usage"] = o.get("usage") or {}
                out["cost"] = float(o.get("total_cost_usd") or 0)
                if o.get("structured_output") is not None:
                    out["structured"] = o["structured_output"]
                if o.get("is_error"):
                    out["error"] = str(o.get("result") or o.get("error") or "")[:500]
                elif not parts and o.get("result"):
                    parts.append(str(o["result"]))
        proc.wait()
    finally:
        killer.cancel()
        proc.stdout.close()
        drain.join(timeout=2)
        proc.stderr.close()
    out["text"] = "".join(parts).strip()
    if proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {(stderr[0] if stderr else '').strip()[:500]}"
    if schema and out["structured"] is None and out["text"]:
        try:
            out["structured"] = json.loads(out["text"])
        except ValueError:
            pass
    return out


def claude_bg(name: str, prompt: str, *, cwd: Path, worktree: str | None = None, persona: Path | None = None,
              permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
              settings: Path | None = None, extra_env: dict | None = None) -> dict:
    """Start a background session (verified shape). Returns what `claude --bg` printed + the agent row."""
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--permission-mode", permission_mode]
    if worktree:
        cmd += ["-w", worktree]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    if settings:
        cmd += ["--settings", str(settings)]
    env = clean_env()
    env.update(extra_env or {})
    p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=120, env=env)
    row = find_agent(name)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": row}


def claude_agents() -> list[dict]:
    try:
        p = subprocess.run([config.CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True,
                           timeout=60, env=clean_env())
        data = json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError):
        return []
    if isinstance(data, dict):
        data = data.get("agents") or data.get("sessions") or []
    return data if isinstance(data, list) else []


def find_agent(name: str | None = None, agent_id: str | None = None, session_id: str | None = None) -> dict | None:
    for a in claude_agents():
        if agent_id and a.get("id") == agent_id:
            return a
        if session_id and a.get("sessionId") == session_id:
            return a
        if name and a.get("name") == name:
            return a
    return None


def claude_resume_bg(name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path | None = None,
                     permission_mode: str = "auto", max_turns: int | None = None, settings: Path | None = None,
                     extra_env: dict | None = None) -> dict:
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--resume", session_id, "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if settings:
        cmd += ["--settings", str(settings)]
    env = clean_env()
    env.update(extra_env or {})
    p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=120, env=env)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": find_agent(name)}


def claude_rm(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "rm", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    return (p.stdout + p.stderr).strip()


def codex_exec(prompt: str, *, cwd: Path, schema: Path | None = None, sandbox: str = "read-only",
               model: str | None = None, timeout: int = 900) -> dict:
    """Codex as validator (critic/reviewer) — verified: needs stdin closed, -o for the answer."""
    with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as outf:
        out_path = outf.name
    cmd = [config.CODEX_BIN, "exec", "--json", "-o", out_path, "-s", sandbox, "-C", str(cwd), "--skip-git-repo-check"]
    if schema:
        cmd += ["--output-schema", str(schema)]
    if model:
        cmd += ["-m", model]
    try:
        p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=clean_env())
        text = Path(out_path).read_text() if Path(out_path).exists() else ""
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    structured = None
    try:
        structured = json.loads(text)
    except ValueError:
        pass
    return {"text": text.strip(), "structured": structured, "returncode": p.returncode,
            "error": None if p.returncode == 0 else p.stderr.strip()[:500]}


def context_percent(context_tokens: int) -> float:
    return round(100.0 * context_tokens / config.CONTEXT_WINDOW, 1)
