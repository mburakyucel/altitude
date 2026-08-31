"""The L3 session — headless, resumable, driven by the server, one turn at a time (decision 16)."""
from __future__ import annotations
import json
import hashlib
import os
import signal
import threading
import secrets
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, engines, route, rules, state as S

from .alt_broker import AltBroker, l3_policy
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# L3 may inspect the checkout and use the generation-fenced ``alt`` socket. It
# has no Agent/Task, GitHub, socket, credential, or direct state-file surface.
ALLOWED_TOOLS = "Read,Grep,Glob,Bash"


def lock(project: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())


def info_path(project: str) -> Path:
    return config.project_dir(project) / "l3.json"


def info(project: str) -> dict:
    return S.read_json(info_path(project), {}) or {}


def save_info(project: str, d: dict) -> None:
    S.write_json(info_path(project), d)


def lease_path(project: str) -> Path:
    return config.project_dir(project) / "l3-turn.json"


def _proc_start_time(pid: int | None) -> str | None:
    """Linux process-start identity for PID-reuse-safe L3 ownership."""
    if not pid:
        return None
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, ValueError, IndexError):
        return None


def process_live(pid: int | None, expected_start: str | None) -> bool:
    """True only for the exact process recorded in a durable claim."""
    return bool(pid and expected_start and _proc_start_time(pid) == str(expected_start))


def _deadline_passed(rec: dict) -> bool:
    try:
        return datetime.fromisoformat(str(rec.get("deadline"))) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _lease_live(rec: dict) -> bool:
    if not rec.get("generation") or _deadline_passed(rec):
        return False
    return (process_live(rec.get("owner_pid"), rec.get("owner_pid_start"))
            or _engine_group_live(rec))


def _engine_group_live(rec: dict) -> bool:
    if rec.get("engine_isolation") == "bwrap-pid-v1":
        return process_live(rec.get("engine_pid"), rec.get("engine_pid_start"))
    if process_live(rec.get("engine_pid"), rec.get("engine_pid_start")):
        return True
    try:
        return bool(rec.get("engine_pgid") is not None
                    and engines.process_group_members(int(rec["engine_pgid"])))
    except (TypeError, ValueError, OSError):
        return False


def _terminate_exact_engine(rec: dict, grace: float = 0.5) -> bool:
    """Terminate only the recorded engine identity; a recycled PID is never signalled."""
    pid, started = rec.get("engine_pid"), rec.get("engine_pid_start")
    leader_live = process_live(pid, started)
    if rec.get("engine_isolation") == "bwrap-pid-v1":
        if not pid or not started:
            return False
        if not leader_live:
            # Losing PID 1 destroys the private namespace, including every
            # setsid/double-fork descendant. A reused numeric host PID is not
            # signalled and cannot imply a survivor of the old generation.
            return True
        try:
            if os.getpgid(int(pid)) != int(pid):
                return False
        except (OSError, TypeError, ValueError):
            return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(int(pid), sig)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if not process_live(pid, started):
                    return True
                time.sleep(0.02)
        return not process_live(pid, started)
    pgid = rec.get("engine_pgid")
    if pgid is not None:
        try:
            pgid = int(pgid)
        except (TypeError, ValueError):
            return False
        if not leader_live:
            # A surviving private group keeps its numeric PGID reserved. Refuse if the leader
            # PID has already been reused; otherwise reap only exact member identities.
            if _proc_start_time(pid) is not None:
                return False
            return engines.reap_process_group_members(pgid)
        try:
            candidate = os.getpgid(int(pid))
            if int(pgid) != int(pid) or candidate != int(pgid):
                return False
            pgid = int(pgid)
        except (OSError, TypeError, ValueError):
            return False
    elif rec.get("engine") in ("claude", "codex"):
        # New L3 engines always have a private group. A legacy record without proof cannot
        # safely use killpg (it may share altd's group), nor may it silently orphan tools.
        return False
    elif not leader_live:
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not process_live(pid, started):
            break
        try:
            if pgid is not None:
                # codex_exec creates a new session. The exact-live leader anchors this group,
                # so descendants do not survive an altd/broker restart.
                os.killpg(pgid, sig)
            else:
                os.kill(int(pid), sig)
        except ProcessLookupError:
            break
        except (OSError, TypeError, ValueError):
            return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not process_live(pid, started):
                if pgid is None:
                    return True
                break  # leader exited; exact group survivors still require TERM→KILL proof below
            time.sleep(0.02)
    if process_live(pid, started):
        return False
    return (engines.reap_process_group_members(pgid) if pgid is not None else True)


def lease_info(project: str, *, recover: bool = True) -> dict:
    """Return an exact-live durable lease, clearing dead claims and reaping expired engines.

    The engine can outlive altd. In that case the new daemon adopts the claim for observation
    without changing its owner identity, and suppresses every trigger until that exact engine exits.
    """
    stale = None
    orphaned = False
    with S.project_lock(project):
        rec = S.read_json(lease_path(project), {}) or {}
        owner_live = process_live(rec.get("owner_pid"), rec.get("owner_pid_start"))
        engine_live = _engine_group_live(rec)
        # Both model engines depend on a generation socket broker and output collector owned by
        # the launching altd. Once that exact owner dies, a surviving CLI can no longer publish
        # a durable result; fence its PID namespace immediately and let state evidence requeue it.
        # Legacy engine-less records retain the old observation/adoption behavior.
        if (rec.get("generation") and rec.get("engine") in ("claude", "codex")
                and not owner_live and engine_live):
            stale = dict(rec); orphaned = True
            if not recover or not _terminate_exact_engine(rec):
                return rec
            current = S.read_json(lease_path(project), {}) or {}
            if current.get("generation") == rec.get("generation"):
                S.write_json(lease_path(project), {})
        elif _lease_live(rec):
            if (not owner_live and engine_live
                    and not process_live(rec.get("adopted_by_pid"), rec.get("adopted_by_pid_start"))):
                rec["adopted_by_pid"] = os.getpid()
                rec["adopted_by_pid_start"] = _proc_start_time(os.getpid())
                rec["adopted_at"] = S.now()
                S.write_json(lease_path(project), rec)
            return rec
        elif not rec.get("generation"):
            return {}
        else:
            stale = dict(rec)
            orphaned = bool(rec.get("owner_pid") and not owner_live)
            if not recover:
                return {}
            if _deadline_passed(rec) and engine_live:
                # Fence the old generation before another claimant can make this exact PID appear
                # to belong to a newer turn.
                if not _terminate_exact_engine(rec):
                    return rec  # fail closed: an unkillable exact engine remains visibly busy
            if _deadline_passed(rec) and owner_live and not rec.get("timed_out_at"):
                # The engine is fenced, but its owning turn still has to unwind callbacks and
                # generation-checked state writes. Give that exact owner one short grace lease;
                # its normal finally clears immediately, while a wedged thread is bounded again.
                rec.update({"engine_pid": None, "engine_pid_start": None, "timed_out_at": S.now(),
                            "deadline": (datetime.now(timezone.utc)
                                         + timedelta(seconds=30)).replace(microsecond=0).isoformat()})
                S.write_json(lease_path(project), rec)
                return rec
            current = S.read_json(lease_path(project), {}) or {}
            if current.get("generation") == rec.get("generation"):
                S.write_json(lease_path(project), {})
    if stale:
        S.project_log(project, "l3-lease-recovered", generation=stale.get("generation"),
                      trigger=stale.get("trigger"), timed_out=_deadline_passed(stale), orphaned=orphaned,
                      engine=stale.get("engine"))
        evidence = stale.get("evidence") or {}
        state_driven = any(key in evidence for key in ("proposal_slug", "report_slug", "recovery_batch"))
        if orphaned and not state_driven:
            detail = (f"orphaned {stale.get('engine') or 'unknown'} L3 {stale.get('trigger')} turn "
                      f"{stale.get('generation')} lost its owner; no blind replay was attempted")
            chat_log(project, "error", detail, trigger=stale.get("trigger"), generation=stale.get("generation"))
            from . import improve
            improve.system_fault("l3-orphan", detail, project=project)
    return {}


def _claim_lease(project: str, trigger: str, prompt: str, evidence: dict | None) -> dict | None:
    # Recovery is deliberately outside the claim transaction so stale exact engines are fenced first.
    if lease_info(project):
        return None
    now = datetime.now(timezone.utc).replace(microsecond=0)
    generation = secrets.token_hex(16)
    rec = {
        "version": 1, "generation": generation, "trigger": trigger,
        "owner_pid": os.getpid(), "owner_pid_start": _proc_start_time(os.getpid()),
        "engine_pid": None, "engine_pid_start": None,
        "started": now.isoformat(),
        "deadline": (now + timedelta(seconds=config.L3_TURN_TIMEOUT)).isoformat(),
        "evidence": {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), **(evidence or {})},
    }
    if not rec["owner_pid_start"]:
        raise RuntimeError("cannot establish exact process identity for L3 lease owner")
    with S.project_lock(project):
        current = S.read_json(lease_path(project), {}) or {}
        if _lease_live(current):
            return None
        # A dead claim discovered between recovery and this lock is safe to replace. An expired
        # exact-live engine is not: leave it for lease_info to fence on the next attempt.
        if (current.get("generation") and _engine_group_live(current)
                and _deadline_passed(current)):
            return None
        S.write_json(lease_path(project), rec)
    S.project_log(project, "l3-lease-claimed", generation=generation, trigger=trigger,
                  deadline=rec["deadline"], evidence=rec["evidence"])
    return rec


def _note_engine_start(project: str, generation: str, pid: int) -> None:
    started = _proc_start_time(pid)
    if not started:
        raise RuntimeError(f"cannot establish exact process identity for L3 engine pid {pid}")
    try:
        pgid = os.getpgid(int(pid))
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot establish private process group for L3 engine pid {pid}") from exc
    if pgid != int(pid):
        raise RuntimeError(f"L3 engine pid {pid} is not leader of its private process group ({pgid})")
    with S.project_lock(project):
        rec = S.read_json(lease_path(project), {}) or {}
        if rec.get("generation") != generation:
            # Both engine wrappers kill their child when on_start raises. This is the launch
            # cancellation handshake for a turn whose lease was timed out/replaced concurrently.
            raise RuntimeError("L3 lease generation changed while the engine was starting")
        rec.update({"engine_pid": int(pid), "engine_pid_start": started,
                    "engine_pgid": pgid, "engine_isolation": "bwrap-pid-v1",
                    "engine_started": S.now()})
        S.write_json(lease_path(project), rec)


def _note_engine_choice(project: str, generation: str, engine: str) -> None:
    with S.project_lock(project):
        rec = S.read_json(lease_path(project), {}) or {}
        if rec.get("generation") != generation:
            raise RuntimeError("L3 lease generation changed before engine selection")
        rec["engine"] = engine
        S.write_json(lease_path(project), rec)


def _clear_lease(project: str, generation: str) -> None:
    with S.project_lock(project):
        rec = S.read_json(lease_path(project), {}) or {}
        if rec.get("generation") != generation:
            return
        # Engine wrappers normally prove their private group empty before returning or raising.
        # If an OS-level termination failure defeats that contract, retain this generation as
        # fail-closed authority rather than allowing a new L3 to overlap surviving tools.
        if _engine_group_live(rec):
            rec["release_waiting_for_engine"] = S.now()
            S.write_json(lease_path(project), rec)
            return
        S.write_json(lease_path(project), {})
    S.project_log(project, "l3-lease-cleared", generation=generation)


def public_info(project: str) -> dict:
    """Engine-normalized L3 status for APIs: never pair a Codex row with Claude's retained transcript."""
    inf = info(project)
    engine = inf.get("engine_last") or "claude"
    out = {**inf, "engine": engine}
    lease = lease_info(project)
    out["busy"] = bool(lease)
    out["active_turn"] = ({key: lease.get(key) for key in
                           ("generation", "trigger", "started", "deadline", "engine_pid", "evidence")}
                          if lease else None)
    if engine == "codex":
        from .monitor import codex_context_percent
        out["session_id"] = inf.get("codex_session_id")
        out["context_percent"] = codex_context_percent(inf.get("codex_session_id"))
        out["context_state"] = engines.context_state(out["context_percent"], "codex")
        out["rotate_next"] = False  # Codex compacts natively; Claude's retained rotation flag is not this engine's state
        out["turns"] = inf.get("codex_turns")
    out.pop("codex_session_id", None)
    out.pop("codex_context_percent", None)  # legacy values were cumulative billing, not live context
    return out


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
    return bool(lease_info(project))


def _header(project: str, trigger: str, fresh: bool) -> str:
    d = config.project_dir(project)
    lines = [f"[altitude] project={project} trigger={trigger} state_file={d / 'STATE.md'} "
             f"tasks_dir={d / 'tasks'} repo={config.project_path(project)}"]
    if fresh:
        lines.append("[altitude] This is a fresh session (start or rotation). Read the state file first; it is your memory. "
                     "Do not re-ask what it already answers.")
    return "\n".join(lines) + "\n\n"


def turn(project: str, prompt: str, *, trigger: str = "chat", on_text=None, on_start=None,
         model: str | None = None, precheck=None, evidence: dict | None = None) -> dict:
    """Run one project-serialized L3 turn under a restart-stable durable lease.

    Every trigger enters here.  A live prior owner/engine is adopted and reported as busy rather
    than waited on or duplicated.  ``on_start`` is combined with the lease callback, so callers
    retain their task-specific evidence without becoming the concurrency authority.
    """
    empty = {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
             "turns": 0, "structured": None, "error": None, "tools": [], "skipped": True,
             "_turn_started_at": None}
    if precheck is not None and not precheck():
        return empty
    claim = _claim_lease(project, trigger, prompt, evidence)
    if claim is None:
        current = lease_info(project)
        return {**empty, "busy": True, "lease_generation": current.get("generation"),
                "busy_trigger": current.get("trigger")}
    generation = claim["generation"]

    def combined_start(pid: int) -> None:
        _note_engine_start(project, generation, pid)
        if on_start is not None:
            on_start(pid)

    try:
        # The check is repeated after the atomic claim.  It runs without the project lock, so
        # task prechecks may safely use state helpers and a changed task never consumes a turn.
        if precheck is not None and not precheck():
            return empty
        return _run_turn(project, prompt, trigger=trigger, on_text=on_text,
                         on_start=combined_start, model=model, precheck=precheck,
                         lease_generation=generation)
    finally:
        _clear_lease(project, generation)


def _run_turn(project: str, prompt: str, *, trigger: str = "chat", on_text=None, on_start=None,
              model: str | None = None, precheck=None, lease_generation: str) -> dict:
    """In-process turn body.  ``turn`` owns the durable serialization contract."""
    with lock(project):
        if precheck is not None and not precheck():
            return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
                    "turns": 0, "structured": None, "error": None, "tools": [], "skipped": True,
                    "_turn_started_at": None}
        proj = config.project(project)
        S.regen_state_md(project)
        inf = info(project)
        sid = inf.get("session_id")
        choice = route.pick_l3_engine(forced=proj.get("l3_engine"), previous=inf.get("engine_last"))
        _note_engine_choice(project, lease_generation, choice["engine"])
        over = (inf.get("context_percent") or 0) >= config.CONTEXT_ACT * 100  # decision 12: checked at turn start, not only after
        handoff = choice["engine"] == "claude" and inf.get("engine_last") == "codex" and bool(sid)
        if handoff:
            inf["rotate_reason"] = "fresh Claude handoff after Codex L3 turns"
        elif over and sid and not inf.get("rotate_next"):
            inf["rotate_reason"] = f"context {inf.get('context_percent')}% ≥ act line {int(config.CONTEXT_ACT * 100)}% at turn start"
        fresh = not sid or inf.get("rotate_next", False) or over or handoff
        if fresh and sid:
            S.project_log(project, "l3-rotate", old=sid, reason=inf.get("rotate_reason", "requested"))
            # the rotation is a decision, so it is persisted *before* the turn runs: it used to be saved only
            # when the turn returned, so an altd that restarted mid-turn read the old session id back and
            # rotated the very same session again, once per restart (I-011: three `l3-rotate old=e9aa9612`)
            inf.update({"session_id": None, "rotate_next": False, "rotate_reason": None, "context_percent": 0,
                        "rotated_from": sid, "rotated_at": S.now()})
            save_info(project, inf)
        persona = rules.compiled_persona("l3", project)
        turn_started_at = S.now()
        chat_log(project, "user", prompt, trigger=trigger, at=turn_started_at)
        if choice["engine"] == "codex":
            return _codex_turn(project, prompt, trigger, persona, turn_started_at,
                               reason=choice["why"], on_text=on_text, on_start=on_start)
        worktree = config.project_path(project)
        state_snapshot = (config.project_dir(project) / "STATE.md").read_text()
        claude_prompt = (_header(project, trigger, fresh)
                         + "## Current state snapshot\n" + state_snapshot + "\n\n" + prompt)
        broker_dir = Path(tempfile.mkdtemp(prefix=f"altitude-l3-claude-{lease_generation[:8]}-"))
        broker = AltBroker(
            socket_path=broker_dir / "broker.fifo", token=secrets.token_hex(32),
            project=project, slug="__l3__", generation=lease_generation,
            worktree=worktree, trusted_alt=config.REPO / "bin" / "alt", policy=l3_policy,
            validate_generation=lambda: (S.read_json(lease_path(project), {}) or {}).get("generation")
            == lease_generation,
            actor="l3", host_env={"ALTITUDE_TRIGGER": trigger},
        )
        git_paths: list[Path] = []
        for args in (("rev-parse", "--absolute-git-dir"), ("rev-parse", "--git-common-dir")):
            import subprocess
            proof = subprocess.run(["git", *args], cwd=str(worktree), capture_output=True,
                                   text=True, timeout=30)
            if proof.returncode == 0 and (proof.stdout or "").strip():
                candidate = Path(proof.stdout.strip())
                git_paths.append(candidate if candidate.is_absolute() else worktree / candidate)
        settings = engines.claude_worker_settings(
            config.project_dir(project) / f"l3-claude-settings-{lease_generation}.json",
            cwd=worktree, writable=False, broker_dir=broker_dir,
            git_read_paths=tuple(git_paths),
        )
        extra_env = {"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project,
                     "ALTITUDE_TRIGGER": trigger, **broker.env()}
        try:
            with broker:
                res = engines.claude_print(
                    claude_prompt, cwd=worktree, resume=None if fresh else sid,
                    persona=persona, tools=ALLOWED_TOOLS, restricted=True,
                    permission_mode="auto", model=model or proj.get("l3_model") or config.MODELS["l3"],
                    on_text=on_text, on_start=on_start, settings=settings,
                    extra_env=extra_env, start_new_session=True, generation_isolation=True)
        finally:
            settings.unlink(missing_ok=True)
        res["skipped"] = False
        res["_turn_started_at"] = turn_started_at
        if res.get("limited"):  # the window closed under this very turn: run it again on Codex, now
            _note_engine_choice(project, lease_generation, "codex")
            return _codex_turn(project, prompt, trigger, persona, turn_started_at,
                               reason=f"Claude window exhausted until {res['limited']}", on_text=on_text, on_start=on_start)
        if res["error"] and not res["session_id"]:
            chat_log(project, "error", res["error"], trigger=trigger)  # a failed turn is not a turn: nothing saved
            return res
        pct = engines.context_percent(res["context_tokens"])
        inf.update({"session_id": res["session_id"], "turns": (0 if fresh else inf.get("turns", 0)) + 1,
                    "context_percent": pct, "last_turn": S.now(), "last_cost": res["cost"], "engine_last": "claude",
                    "started": inf.get("started") if not fresh else S.now(),
                    "last_audit": inf.get("last_audit") or S.now(),   # first weekly audit a week after the first turn
                    "context_state": engines.context_state(pct),
                    "rotate_next": pct >= config.CONTEXT_ACT * 100,
                    "rotate_reason": f"context {pct}% ≥ act line {int(config.CONTEXT_ACT * 100)}% (decision 12)" if pct >= config.CONTEXT_ACT * 100 else None})
        save_info(project, inf)
        chat_log(project, "assistant", res["text"] or (res["error"] or ""), trigger=trigger, context_percent=pct,
                 turns=res["turns"], tools=res["tools"][:40])
        S.regen_state_md(project)
        res["context_percent"] = pct
        return res


def _codex_turn(project: str, prompt: str, trigger: str, persona: Path, turn_started_at: str, *,
                reason: str, on_text=None, on_start=None) -> dict:
    """One L3 turn on Codex (decision 56). No transcript: the state file and the recent chat are its memory, exactly as a
    fresh Claude session. The Claude session id is kept for when the window reopens; the turn is labelled `codex` in the
    chat log, the project log and the result — a degraded state that is visible, never a silent substitution (decision 36)."""
    inf = info(project)
    recent = chat_history(project, 20)
    history = "\n".join(f"- {m.get('role')}: {str(m.get('text') or '')[:600]}" for m in recent if m.get("role") in ("user", "assistant"))
    state_text = (config.project_dir(project) / "STATE.md").read_text()
    text = (persona.read_text() + "\n\n" + _header(project, trigger, True)
            + f"[altitude] Engine: Codex — {reason}. You have no transcript and no writable state root. "
              "The state snapshot and recent chat below are your memory; use only brokered `alt …` commands to mutate state.\n\n"
            + "## Current state snapshot\n" + state_text + "\n\n"
            + ("## Recent chat (oldest first)\n" + history + "\n\n" if history else "") + prompt)

    S.project_log(project, "l3-codex", reason=reason, trigger=trigger)
    guard = f"python3 {config.HOOKS / 'codex_l3_guard.py'}"
    hook_config = ("hooks.PreToolUse=[{matcher=\".*\",hooks=[{type=\"command\",command="
                   + json.dumps(guard) + ",timeout=10}]}]")
    generation = secrets.token_hex(12)
    broker_dir = Path(tempfile.mkdtemp(prefix=f"altitude-l3-broker-{generation[:8]}-"))
    active = {"value": True}
    broker = AltBroker(
        socket_path=broker_dir / "broker.fifo", token=secrets.token_hex(32), project=project,
        slug="__l3__", generation=generation, worktree=config.project_path(project),
        trusted_alt=config.REPO / "bin" / "alt", policy=l3_policy,
        validate_generation=lambda: active["value"], actor="l3", host_env={"ALTITUDE_TRIGGER": trigger},
    )
    extra_env = {"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT),
                 "ALTITUDE_TRIGGER": trigger}
    try:
        with broker:
            broker_env, broker_fds = broker.codex_capability()
            extra_env.update(broker_env)
            res = engines.codex_exec(
                text, cwd=config.project_path(project), sandbox="read-only", timeout=1200,
                effort=config.CODEX_EFFORT.get("l3"),
                extra_config=["features.hooks=true", hook_config],
                extra_env=extra_env, on_text=on_text, on_start=on_start,
                fault_context={"project": project}, permission_role="l3", permission_id=project,
                permission_read_paths=(config.HOOKS,), bypass_hook_trust=True,
                generation_isolation=True,
                broker_fds=broker_fds,
            )
    finally:
        active["value"] = False
    usage = res.get("usage") or {}
    from .monitor import codex_context_percent
    context = codex_context_percent(res.get("session_id"))
    out = {"text": res.get("text") or "", "session_id": res.get("session_id") or "", "codex_session_id": res.get("session_id"),
           "usage": usage, "context_tokens": None, "context_percent": context, "cost": 0.0, "turns": 1,
           "structured": None, "error": res.get("error"), "tools": [], "skipped": False,
           "_turn_started_at": turn_started_at, "engine": "codex", "engine_reason": reason}
    if "exhausted" in reason.lower():
        out["degraded"] = reason
    if res.get("error") and not out["text"]:
        chat_log(project, "error", f"codex L3 turn failed: {res['error']}", trigger=trigger, engine="codex")
        return out
    now = S.now()
    inf.update({"last_turn": now, "last_cost": 0.0, "engine_last": "codex",
                "codex_turns": int(inf.get("codex_turns") or 0) + 1, "codex_session_id": res.get("session_id"),
                "codex_context_percent": context, "started": inf.get("started") or now,
                "last_audit": inf.get("last_audit") or now})
    save_info(project, inf)
    chat_log(project, "assistant", out["text"], trigger=trigger, engine="codex", engine_reason=reason,
             degraded=out.get("degraded"))
    S.regen_state_md(project)
    return out


def reset(project: str, reason: str = "manual") -> None:
    inf = info(project)
    inf.update({"rotate_next": True, "rotate_reason": reason})
    save_info(project, inf)
