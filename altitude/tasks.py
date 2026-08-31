"""Task lifecycle — five states, one writer. Every transition goes through here."""
from __future__ import annotations
from pathlib import Path

import hashlib
import json
import re

from . import config, state as S

# ---- decision 46: the card is executive — the dilemma in ≤ 2 plain sentences, options ≤ 8 words, the rest in detail ----
CARD_CONTEXT_MAX = 320
CARD_QUESTION_MAX = 280
CARD_OPTION_MAX = 80
CARD_JARGON = re.compile(r"\bI-\d{3}\b|\bR-\d{3}\b|\bdecisions?[- ]?#?\d+\b|\b[\w/.-]+\.(?:py|js|md|json|ts|yaml|yml|toml)\b|`", re.I)


def check_card(question: str, options: list[str] | None, context: str | None = None) -> list[str]:
    """What is wrong with a card, in the L3's terms (empty list = fine). Burak reads it on a phone with no ledger in his head."""
    probs = []
    if context and len(context) > CARD_CONTEXT_MAX:
        probs.append(f"context is {len(context)} chars (max {CARD_CONTEXT_MAX}): the situation in ≤ 2 plain sentences — what is wrong and what the proposal does about it")
    if context and CARD_JARGON.search(context):
        probs.append("context names an incident/decision/rule id, a file or code: plain words only; ids and files go in --detail")
    if len(question) > CARD_QUESTION_MAX:
        probs.append(f"question is {len(question)} chars (max {CARD_QUESTION_MAX}): the dilemma in ≤ 2 plain sentences; the rest goes in --detail")
    if CARD_JARGON.search(question):
        probs.append("question names an incident/decision/rule id, a file or code: plain words only; ids and files go in --detail")
    for o in options or []:
        if len(o) > CARD_OPTION_MAX:
            probs.append(f"option '{o[:32]}…' is {len(o)} chars (max {CARD_OPTION_MAX}): a label of ≤ 8 words; conditions go in --detail")
        elif CARD_JARGON.search(o):
            probs.append(f"option '{o[:32]}…' names an id, file or code: plain words only")
    return probs


def short_reason(reason: str, limit: int = 200) -> str:
    """The first sentence of a block reason, for the card; the whole reason stays in detail."""
    first = re.split(r"(?<=[.!?])\s|\s[—–-]\s|:\s`", reason.strip(), maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"

TRANSITIONS = {
    "requested": {"proposed", "rejected", "parked", "approved"},   # approved: S-class auto (decision 13)
    "proposed": {"approved", "rejected", "parked", "requested"},   # requested: "revise"
    "approved": {"running", "parked", "rejected"},
    "running": {"reported", "blocked", "parked"},
    "blocked": {"approved", "running", "parked", "rejected", "reported"},
    "reported": {"done", "running", "blocked"},                    # running: verifier says not done → resume
    "parked": {"requested", "approved", "blocked", "rejected"},     # approved/blocked: restore a stale operational wait
    "done": set(),
    "rejected": set(),
}

ENVELOPE = {  # decision 31 / ROLES.md table
    "S": {"l1_in_flight": 1, "subagent_launches": 3, "max_turns": 40, "verification": "reviewer"},
    "M": {"l1_in_flight": 3, "subagent_launches": 8, "max_turns": 120, "verification": "reviewer+critic-if-arch"},
    "L": {"l1_in_flight": 5, "subagent_launches": 20, "max_turns": 250, "verification": "critic+reviewer"},
}


class TransitionError(Exception):
    pass


def _move(project: str, task: dict, to: str, actor: str, **ev) -> dict:
    frm = task["state"]
    if to not in TRANSITIONS.get(frm, set()):
        raise TransitionError(f"{task['slug']}: {frm} → {to} is not allowed")
    task["state"] = to
    if to != "blocked":
        task["needs_user"] = None
    if to == "running":
        task["dispatching"] = None
    S.save_task(project, task)
    S.append_event(project, task["slug"], "state", frm=frm, to=to, by=actor, **ev)
    S.regen_state_md(project)
    return task


def _stop_terminal_worker(project: str, task: dict) -> None:
    """Stop only the worker generation owned by a task before it becomes terminal.

    A failed stop is a failed transition: keeping the task non-terminal lets the
    normal poll/recovery loop keep the orphan visible instead of silently parking
    a process that can still edit its worktree.
    """
    try:
        from . import dispatch, l1
        claim = task.get("pending_dispatch") or task.get("pending_resume") or {}
        dispatch_id = task.get("dispatch_id") or claim.get("dispatch_id")
        engine = claim.get("engine") or dispatch.task_l2_engine(project, task)
        if dispatch_id and not l1.stop_all(project, task["slug"], dispatch_id):
            raise TransitionError(f"{task['slug']}: an exact L1 generation could not be stopped safely")
        if engine == "codex" and dispatch_id:
            record = dispatch.codex_run(project, task["slug"])
            exact_active = (record.get("dispatch_id") == dispatch_id
                            and dispatch.codex_processes_live(record))
            stopped = dispatch.stop_codex_worker(project, task["slug"], dispatch_id)
            S.append_event(project, task["slug"], "session-stopped", engine="codex",
                           dispatch_id=dispatch_id, stopped=stopped)
            if exact_active and not stopped:
                raise TransitionError(f"{task['slug']}: Codex worker could not be stopped safely")
        elif claim and engine == "claude" and claim.get("launch_attempted_at"):
            # Claude has no parent-controlled launch gate.  The pre-launch
            # inventory is durably attached to the claim before ``claude --bg``
            # is entered, so cancellation can either stop the exact new row or
            # leave durable cleanup work for poll() if registration is late.
            excluded_ids = {
                str(value) for value in claim.get("excluded_agent_ids") or []
            }
            stopped = dispatch._cancel_claude_launch(
                project, task["slug"], str(dispatch_id),
                str(claim.get("generation") or ""),
                f"{project}/{dispatch_id}", excluded_ids,
                reason="terminal transition crossed a Claude launch attempt")
            S.append_event(project, task["slug"], "session-stop-deferred" if not stopped else "session-stopped",
                           engine="claude", dispatch_id=dispatch_id,
                           generation=claim.get("generation"), durable_cleanup=not stopped)
        elif task.get("agent_id") or (claim and engine == "claude"):
            from . import engines
            rows = engines.claude_agents()
            agent_id = task.get("agent_id") or next(
                (row.get("id") for row in rows if row.get("name") == f"{project}/{dispatch_id}"), None)
            active = any(row.get("id") == agent_id
                         and row.get("state") not in ("failed", "done", "stopped")
                         for row in rows)
            if active:
                note = engines.claude_stop(agent_id)
                still_live = any(row.get("id") == agent_id
                                 and row.get("state") not in ("failed", "done", "stopped")
                                 for row in engines.claude_agents())
                if still_live:
                    raise TransitionError(f"{task['slug']}: Claude worker is still live after stop")
                S.append_event(project, task["slug"], "session-stopped", engine="claude",
                               agent_id=agent_id, note=note[:200])
            else:
                S.append_event(project, task["slug"], "session-already-terminal",
                               engine="claude", agent_id=agent_id)
            # Revocation is part of stop proof. A terminal task must never
            # retain a live Unix-socket capability even if the agent row was
            # absent because launch and cancellation crossed one another.
            dispatch._close_claude_broker(
                project, task["slug"], claim.get("generation") or
                (task.get("claude_broker") or {}).get("generation"))
    except Exception as exc:
        S.append_event(project, task["slug"], "session-stop-failed",
                       error=f"{type(exc).__name__}: {exc}"[:500])
        raise


def _terminal_move(project: str, slug: str, to: str, actor: str, **ev) -> dict:
    """Stop the exact worker first, then atomically close the unchanged task."""
    with S.project_lock(project):
        snapshot = S.load_task(project, slug)
        if to not in TRANSITIONS.get(snapshot.get("state"), set()):
            raise TransitionError(f"{slug}: {snapshot.get('state')} → {to} is not allowed")
        claim_field = "pending_dispatch" if snapshot.get("pending_dispatch") else (
            "pending_resume" if snapshot.get("pending_resume") else None)
        if claim_field:
            claim = dict(snapshot[claim_field])
            claim["cancel_requested"] = S.now()
            snapshot[claim_field] = claim
            S.save_task(project, snapshot)
        identity = (snapshot.get("state"), snapshot.get("dispatch_id"), snapshot.get("agent_id"),
                    snapshot.get("pending_dispatch"), snapshot.get("pending_resume"))
        had_worker = snapshot.get("state") in ("running", "blocked") or bool(claim_field)
    if had_worker:
        _stop_terminal_worker(project, snapshot)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        current = (task.get("state"), task.get("dispatch_id"), task.get("agent_id"),
                   task.get("pending_dispatch"), task.get("pending_resume"))
        if current != identity:
            raise TransitionError(f"{slug}: task changed while its worker was being stopped")
        task.pop("pending_dispatch", None)
        task.pop("pending_resume", None)
        task.pop("dispatching", None)
        task["blocked_reason"] = None
        return _move(project, task, to, actor, **ev)


def new(project: str, title: str, cls: str, request: str, actor: str = "l3", source: str = "chat", model: str | None = None,
        paths: list[str] | None = None, engine: str | None = None, hold_merge: str | None = None) -> dict:
    if cls == "auto":  # decision 53: the intake sizer picks the class (Burak does not have to)
        cls = None
    elif cls not in S.CLASSES:
        raise TransitionError(f"class must be one of {S.CLASSES} or auto")
    if model and model not in config.MODEL_ALIASES:
        raise TransitionError(f"model must be one of {config.MODEL_ALIASES}")
    config.project(project)
    with S.project_lock(project):
        base = S.slugify(title)
        slug, n = base, 1
        while S.task_dir(project, slug).exists():
            n += 1
            slug = S.collision_slug(base, n)
        d = S.tasks_dir(project) / slug
        d.mkdir(parents=True)
        S.atomic_write(d / "request.md", request.rstrip() + "\n")
        task = {"slug": slug, "title": title, "class": cls, "state": "requested", "created": S.now(),
                "attempt": 0, "proposal_attempts": 0, "dispatch_id": None, "session_id": None, "agent_id": None,
                "worktree": None,
                "branch": None, "prs": [], "envelope": dict(ENVELOPE[cls]) if cls else {}, "estimate": {}, "spend": {},
                "decision": None, "blocked_reason": None, "source": source, "verified": None, "model": model, "paths": [p.strip() for p in (paths or []) if p.strip()],
                "engine": engine,  # decision 45: a forced engine for every L1 of this task (None = by quota)
                "hold_merge": (hold_merge or "").strip() or None}  # an additional landing blocker; R-014 currently blocks every task
        S.save_task(project, task)
        S.append_event(project, slug, "new", by=actor, cls=cls, title=title, source=source)
        S.regen_state_md(project)
        return task


def propose(project: str, slug: str, proposal_md: str, proposal: dict | None = None,
            question: str | None = None, options: list[str] | None = None, actor: str = "l3", detail: str | None = None,
            context: str | None = None) -> dict:
    """Attach a proposal. If it needs Burak, `context`/`question`/`options` create the Decision card; `detail` carries the reasoning."""
    if question:
        probs = check_card(question, options, context)
        if probs:
            raise TransitionError("card rejected (decision 46 — the card is executive): " + "; ".join(probs))
    with S.project_lock(project):
        task = S.load_task(project, slug)
        d = S.task_dir(project, slug)
        S.atomic_write(d / "proposal.md", proposal_md.rstrip() + "\n")
        if proposal is not None:
            S.write_json(d / "proposal.json", proposal)
            est = proposal.get("estimate") or {}
            if est:
                task["estimate"] = est
            env = proposal.get("envelope") or {}
            if env:
                task["envelope"].update({k: v for k, v in env.items() if k in task["envelope"] and v is not None})
        if question:
            task["decision"] = {"question": question, "options": options or ["Approve", "Revise", "Park"],
                                "asked": S.now(), "chosen": None, "detail": detail, "context": context}
        else:
            task["decision"] = None
        _clear_proposal_failure(task)
        return _move(project, task, "proposed", actor, needs_decision=bool(question))


def approve(project: str, slug: str, option: int | None = None, actor: str = "burak", note: str = "") -> dict:
    """Only Burak approves (decision 5): the button, or `alt task approve` run by a human."""
    if actor != "burak":
        raise TransitionError("approval must come from Burak (button or human-run CLI), not from an agent")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        dec = task.get("decision") or {}
        chosen = None
        if dec:
            opts = dec.get("options") or []
            if option is None or not (0 <= option < len(opts)):
                raise TransitionError(f"pick an option 0..{len(opts) - 1}: {opts}")
            chosen = opts[option]
            dec.update({"chosen": chosen, "chosen_index": option, "answered": S.now(), "note": note})
            task["decision"] = dec
            low = chosen.lower()
            if low.startswith("revise"):  # decision 50: a revision carries Burak's feedback, and the next proposal answers it
                if not (note or "").strip():
                    raise TransitionError("Revise needs feedback: what should change in the proposal")
                return _revise_locked(project, task, note.strip(), actor, question=dec.get("question"), answer=chosen)
            if low.startswith("park"):
                return _move(project, task, "parked", actor, question=dec.get("question"), answer=chosen, note=note)
            if low.startswith("reject"):
                return _move(project, task, "rejected", actor, question=dec.get("question"), answer=chosen, note=note)
        return _move(project, task, "approved", actor, question=dec.get("question"), answer=chosen, note=note)


def set_class(project: str, slug: str, cls: str, why: str, paths: list[str] | None = None, actor: str = "sizer") -> dict:
    """Decision 53: give an unsized (or wrongly sized) requested task its class; the envelope follows the class table and
    declared paths are kept (the sizer's are used only when the task has none)."""
    if cls not in S.CLASSES:
        raise TransitionError(f"class must be one of {S.CLASSES}")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "requested":
            raise TransitionError(f"{slug}: class can change only while requested (state {task['state']})")
        prev = task.get("class")
        task["class"] = cls
        task["envelope"] = dict(ENVELOPE[cls])
        if paths and not task.get("paths"):
            task["paths"] = [p.strip() for p in paths if p.strip()]
        task["sized"] = {"class": cls, "why": why, "by": actor, "at": S.now(), "previous": prev}
        task["size_error"] = None
        S.save_task(project, task)
    S.append_event(project, slug, "sized", actor=actor, cls=cls, why=why, previous=prev)
    return task


def auto_approve(project: str, slug: str, reason: str) -> dict:
    """S-class work that the class table lets L3 start without a card (decision 13). Always an FYI."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["class"] != "S":
            raise TransitionError("only S-class tasks can be auto-approved")
        task["decision"] = None
        fyi(project, slug, f"auto-approved S task: {reason}")
        return _move(project, task, "approved", "l3", auto=True, reason=reason)


def reject(project: str, slug: str, reason: str, actor: str = "burak") -> dict:
    return _terminal_move(project, slug, "rejected", actor, reason=reason)


def park(project: str, slug: str, reason: str, actor: str = "l3", trigger: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if actor == "l3" and trigger == "reconcile" and task.get("state") == "blocked":
            raise TransitionError(f"{slug}: reconcile cannot park blocked work; acknowledge the recovery batch instead")
    return _terminal_move(project, slug, "parked", actor, reason=reason)


def unpark(project: str, slug: str, actor: str = "l3") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _clear_proposal_failure(task)
        return _move(project, task, "requested", actor)


OPERATIONAL_PARK_MIGRATION = "operational-parks-v1"
_OPERATIONAL_PARK_WORDS = (
    "queue hold", "queue order", "unpark when", "unpark once", "unpark on merge",
    "waits for", "waiting for", "starts after pr", "registry points to", "critic call crashes",
    "exists only in the unpushed", "every new dispatch", "most new dispatches",
)
_EXPLICIT_PARK_WORDS = (
    "burak's park stands", "superseded", "nothing to resume", "revise ", "revision ",
)


def operational_park_target(project: str, task: dict) -> tuple[str, str] | None:
    """Classify only historical L3/altd queue waits from their durable state event."""
    if task.get("state") != "parked":
        return None
    events = [e for e in S.read_events(project, task["slug"])
              if e.get("kind") == "state" and e.get("to") == "parked"]
    if not events:
        return None
    event = events[-1]
    reason = str(event.get("reason") or "")
    low = reason.lower()
    actor = event.get("by")
    trusted_recovery = actor == "forensic-reconciler" and event.get("forensic_reconcile") is True
    if (actor not in ("l3", "altd") and not trusted_recovery) or any(
            word in low for word in _EXPLICIT_PARK_WORDS):
        return None
    if not any(word in low for word in _OPERATIONAL_PARK_WORDS):
        return None
    previous = event.get("frm")
    if previous == "approved":
        target = "approved"
    elif previous in ("running", "blocked"):
        target = "blocked" if task.get("worktree") else "approved"
    else:
        target = "requested"
    return target, reason


def _operational_park_generation(project: str, task: dict) -> str | None:
    """Stable identity for the exact latest parked-state event, including duplicate occurrences."""
    parks = [(index, event) for index, event in enumerate(S.read_events(project, task["slug"]))
             if event.get("kind") == "state" and event.get("to") == "parked"]
    if not parks:
        return None
    index, event = parks[-1]
    payload = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    return f"{index}:{hashlib.sha256(payload).hexdigest()}"


def migrate_operational_parks(project: str) -> list[str]:
    """Recover each durable operational park once; explicit/user parks stay parked.

    The original v1 marker was project-global.  Keep that record as history, but
    do not let its existence hide a later operational park of the same task.
    """
    marker = config.MONITOR_DIR / f"{OPERATIONAL_PARK_MIGRATION}-{project}.json"
    prior = S.read_json(marker, {}) if marker.exists() else {}
    prior = prior if isinstance(prior, dict) else {}
    processed = {
        str(slug): [str(value) for value in values]
        for slug, values in (prior.get("processed_events") or {}).items()
        if isinstance(values, list)
    }
    migrated, skipped = [], []
    for snapshot in S.list_tasks(project):
        classified = operational_park_target(project, snapshot)
        if not classified:
            if snapshot.get("state") == "parked":
                skipped.append(snapshot["slug"])
            continue
        generation = _operational_park_generation(project, snapshot)
        if generation is None or generation in processed.get(snapshot["slug"], []):
            continue
        target, reason = classified
        with S.project_lock(project):
            task = S.load_task(project, snapshot["slug"])
            if task.get("state") != "parked":
                continue
            current = operational_park_target(project, task)
            current_generation = _operational_park_generation(project, task)
            if current is None or current_generation != generation:
                continue
            target, reason = current
            task["blocked_reason"] = (
                f"Recovered stale operational park: {reason}" if target == "blocked" else None)
            _move(project, task, target, "altd-recovery",
                  migration=OPERATIONAL_PARK_MIGRATION, park_generation=generation,
                  parked_reason=reason)
        processed.setdefault(snapshot["slug"], []).append(generation)
        migrated.append(snapshot["slug"])
    if not marker.exists() or migrated:
        history = list(prior.get("history") or [])
        previous_run = {key: prior[key] for key in
                        ("at", "project", "migrated", "explicit_or_unclassified") if key in prior}
        if previous_run:
            history.append(previous_run)
        S.write_json(marker, {"at": S.now(), "project": project, "migrated": migrated,
                              "explicit_or_unclassified": skipped,
                              "processed_events": processed, "history": history})
    return migrated


def brief(project: str, slug: str, brief_md: str, actor: str = "l3") -> Path:
    with S.project_lock(project):
        d = S.task_dir(project, slug)
        S.atomic_write(d / "brief.md", brief_md.rstrip() + "\n")
        S.append_event(project, slug, "brief", by=actor, bytes=len(brief_md))
        return d / "brief.md"


def dispatch(project: str, slug: str, *, dispatch_id: str, session_id: str | None, agent_id: str | None,
             worktree: str | None, branch: str | None, l2_engine: str | None = None,
             l2_engine_why: str | None = None, origin_sha: str | None = None,
             actor: str = "altd", expected_pending_generation: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if expected_pending_generation is not None:
            claim = task.get("pending_dispatch") or {}
            if (task.get("state") != "approved"
                    or claim.get("dispatch_id") != dispatch_id
                    or claim.get("generation") != expected_pending_generation
                    or claim.get("engine") != l2_engine
                    or claim.get("cancel_requested")):
                raise TransitionError(
                    f"{slug}: pending dispatch generation changed before lifecycle persistence")
        task.update({"dispatch_id": dispatch_id, "session_id": session_id, "agent_id": agent_id,
                     "worktree": worktree, "branch": branch, "blocked_reason": None,
                     "dispatched": S.now(), "origin_sha": origin_sha or task.get("origin_sha")})
        task.pop("pending_dispatch", None)
        task["attempt"] = int(dispatch_id.rsplit("-", 1)[-1]) if dispatch_id.rsplit("-", 1)[-1].isdigit() else task["attempt"] + 1
        if l2_engine:
            task.update({"l2_engine": l2_engine, "l2_engine_why": l2_engine_why})
        return _move(project, task, "running", actor, dispatch_id=dispatch_id, session_id=session_id,
                     engine=l2_engine, engine_why=l2_engine_why)


def report(project: str, slug: str, verified: dict, actor: str = "altd", *,
           expected_state: str | None = None, expected_attempt: int | None = None,
           expected_block_from: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if expected_state is not None and task["state"] != expected_state:
            raise TransitionError(f"{slug}: expected {expected_state}, found {task['state']}")
        if expected_attempt is not None and task.get("attempt") != expected_attempt:
            raise TransitionError(f"{slug}: expected attempt {expected_attempt}, found {task.get('attempt')}")
        if expected_block_from is not None:
            last_block = next((ev for ev in reversed(S.read_events(project, slug))
                               if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
            if not last_block or last_block.get("frm") != expected_block_from:
                raise TransitionError(f"{slug}: latest block did not come from {expected_block_from}")
        verified = {**verified, "attempt": task["attempt"]}
        task["verified"] = verified
        task["blocked_reason"] = None
        if verified.get("prs"):
            task["prs"] = sorted(set(task.get("prs", []) + list(verified["prs"])))
        return _move(project, task, "reported", actor, verdict=verified.get("verdict"))


def block(project: str, slug: str, reason: str, actor: str = "altd", recovery_batch: str | None = None) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") == "running" and actor != "altd":
            # A worker cannot declare itself blocked while retaining its
            # worktree/broker authority. Persist a fail-closed revocation
            # claim first; dispatch.poll stops and proves the exact generation
            # before committing the visible running -> blocked transition.
            # Retrying the same command is idempotent when its response was
            # lost because the worker was stopped immediately afterward.
            pending = task.get("block_pending") or {}
            if pending:
                if (pending.get("dispatch_id") != task.get("dispatch_id")
                        or pending.get("reason") != reason.strip()):
                    raise TransitionError(f"{slug}: another worker block is already pending stop proof")
                return task
            task["block_pending"] = {
                "reason": reason.strip(), "by": actor, "requested": S.now(),
                "dispatch_id": task.get("dispatch_id"),
                "engine": task.get("l2_engine"), "stop_failures": 0,
            }
            task["needs_user"] = None
            task["l3_handled"] = None
            S.save_task(project, task)
            S.append_event(project, slug, "block-pending", by=actor,
                           reason=reason.strip(), dispatch_id=task.get("dispatch_id"),
                           engine=task.get("l2_engine"))
            S.regen_state_md(project)
            return task
        if task.get("state") == "blocked":
            if actor != "l3" or not recovery_batch:
                raise TransitionError(f"{slug}: blocked acknowledgement requires L3 and --recovery-batch")
            task["l3_handled"] = S.now()
            S.save_task(project, task)
            S.append_event(project, slug, "blocked-ack", by=actor, recovery_batch=recovery_batch,
                           note=reason.strip(), blocked_reason=task.get("blocked_reason"),
                           dispatch_id=task.get("dispatch_id"))
            S.regen_state_md(project)
            return task
        if task.get("blocked_reason") != reason:
            task["l3_handled"] = None
        task["needs_user"] = None
        task["blocked_reason"] = reason
        return _move(project, task, "blocked", actor, reason=reason)


def needs_user(project: str, slug: str, reason: str, actor: str = "l3") -> dict:
    """Expose a blocked task to Burak only after L3 identifies a genuine executive decision."""
    if actor != "l3":
        raise TransitionError(f"{slug}: needs-user is L3-only (actor {actor!r})")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") != "blocked":
            raise TransitionError(f"{slug}: needs-user requires a blocked task (state {task.get('state')})")
        task["needs_user"] = {"reason": reason.strip(), "asked": S.now(), "by": actor}
        task["l3_handled"] = S.now()
        S.save_task(project, task)
    S.append_event(project, slug, "needs-user", reason=reason.strip(), by=actor)
    S.regen_state_md(project)
    return task


def raise_envelope(project: str, slug: str, launches: int | None = None, turns: int | None = None, actor: str = "l3") -> dict:
    """Decision 52: L3 raises a blocked task's envelope itself. task.json and the envelope file the hooks read both change,
    so the re-attached L2's next launch is judged against the new cap; lowering is refused (a cap is a stop, not a dial)."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        env = task["envelope"]
        for key, val in (("subagent_launches", launches), ("max_turns", turns)):
            if val is None:
                continue
            if int(val) < int(env[key]):
                raise TransitionError(f"{slug}: {key} {env[key]} → {val} would lower the envelope; only raising is allowed")
            env[key] = int(val)
        S.save_task(project, task)
        if task.get("dispatch_id"):
            p = config.MONITOR_DIR / f"envelope-{project}--{task['dispatch_id']}.json"
            cur = S.read_json(p) if p.exists() else {"project": project, "slug": slug, "dispatch_id": task["dispatch_id"]}
            cur.update(env)
            S.write_json(p, cur)
    S.append_event(project, slug, "envelope-raised", actor=actor, launches=launches, turns=turns)
    return task


def resume(project: str, slug: str, actor: str = "altd", **ev) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = None
        task["needs_user"] = None
        task.pop("pending_resume", None)
        return _move(project, task, "running", actor, **ev)


def retry(project: str, slug: str, actor: str = "burak", **ev) -> dict:
    """Put a blocked task back in the dispatch queue without inventing a running worker."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = None
        task["needs_user"] = None
        task["dispatching"] = None
        return _move(project, task, "approved", actor, **ev)


def done(project: str, slug: str, actor: str = "l3", digest: str = "") -> dict:
    # R-014: no automated/model path currently possesses authoritative
    # base-attached landing evidence.  Keep Burak's explicit UI/CLI override,
    # but do not let L3, altd, or a forged model report bypass verification and
    # archive a reported task.  The future trusted-remote consumer must add a
    # distinct proof-bearing host transition; it must not loosen this guard.
    if actor != "burak":
        raise TransitionError(
            "task close requires Burak's explicit override while trusted remote landing integration is pending")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        d = S.task_dir(project, slug)
        task = _move(project, task, "done", actor)
        if digest:
            S.atomic_write(d / "digest.md", digest.rstrip() + "\n")
        _archive(project, slug)
        S.regen_state_md(project)
        return task


def _archive(project: str, slug: str) -> None:
    src = S.tasks_dir(project) / slug
    if src.is_dir():
        dst = S.archive_dir(project) / slug
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def set_spend(project: str, slug: str, **spend) -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.setdefault("spend", {}).update(spend)
        S.save_task(project, task)
        return task


# ---- Decisions and FYIs (the two outbound classes, decision 9) ---------------

def fyi(project: str, slug: str | None, text: str, actor: str = "l3") -> dict:
    """An FYI is a line in the project's inbox.jsonl; the page shows the tail."""
    item = {"at": S.now(), "kind": "fyi", "project": project, "slug": slug, "text": text.strip(), "by": actor, "seen": False}
    p = config.project_dir(project) / "inbox.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        import json
        f.write(json.dumps(item, sort_keys=True) + "\n")
    if slug:
        S.append_event(project, slug, "fyi", text=text.strip(), by=actor)
    return item


def inbox(project: str, limit: int = 50) -> list[dict]:
    import json
    p = config.project_dir(project) / "inbox.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def decisions(project: str) -> list[dict]:
    """Open Decision cards: proposal choices plus blocked tasks explicitly escalated by L3."""
    out = []
    for t in S.list_tasks(project):
        if t["state"] == "proposed" and t.get("decision") and not t["decision"].get("chosen"):
            out.append({"project": project, "slug": t["slug"], "class": t["class"], "title": t["title"],
                        "question": t["decision"]["question"], "options": t["decision"]["options"],
                        "asked": t["decision"].get("asked"), "kind": "decision", "detail": t["decision"].get("detail"),
                        "context": t["decision"].get("context")})
        elif t["state"] == "blocked" and not t.get("resume_after") and t.get("needs_user"):
            escalation = t.get("needs_user") or {}
            detail = escalation.get("reason") or t.get("blocked_reason") or "no reason recorded"
            out.append({"project": project, "slug": t["slug"], "class": t["class"], "title": t["title"],
                        "question": f"Stopped mid-task: {short_reason(detail)}",
                        "options": ["Resume", "Park", "Reject"], "asked": escalation.get("asked") or t.get("updated"),
                        "kind": "blocked", "detail": detail})
    return out


def set_hold_merge(project: str, slug: str, why: str | None, actor: str = "l3") -> dict:
    """Decision 48: PRs merge by default; a hold is the exception and must say why (critical, costly, always-list)."""
    why = (why or "").strip() or None
    with S.project_lock(project):
        t = S.load_task(project, slug)
        t["hold_merge"] = why
        S.save_task(project, t)
    S.append_event(project, slug, "hold-merge" if why else "release-merge", why=why, actor=actor)
    return t


def _clear_proposal_failure(task: dict) -> None:
    task.pop("proposal_error", None)
    task["proposal_attempts"] = 0


def clear_proposal_failure(project: str, slug: str) -> dict:
    """A completed proposal makes validation failures from earlier attempts obsolete."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _clear_proposal_failure(task)
        S.save_task(project, task)
        return task


def park_failed_proposal(project: str, slug: str, max_attempts: int, actor: str = "altd") -> dict | None:
    """Atomically park a requested task whose recorded proposal failures exhausted their cap."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        failure = task.get("proposal_error") or {}
        message = str(failure.get("message") or "").strip()
        attempts = int(task.get("proposal_attempts", 0))
        if task["state"] != "requested" or attempts < max_attempts or not message:
            return None
        reason = f"proposal failed after {attempts} attempts; recorded failure: {message!r}"
        task["proposal_started"] = None
        task["proposal_turn"] = None
        task = _move(project, task, "parked", actor, reason=reason)
        S.append_event(project, slug, "proposal-failed", by=actor, attempts=attempts, message=message)
    fyi(project, slug, f"{slug}: parked after {attempts} proposal attempts; recorded failure: {message!r}. "
                       "Read the message, fix the request or the docs it cites, then unpark the task.", actor=actor)
    return task


def archive_proposal(task_dir) -> int:
    """Move proposal.md / proposal.json / critique.json aside as -vN (N = next free number) so the next proposal run
    starts fresh and the reviser can still read the previous round. Returns N, or 0 when there was nothing to move."""
    n = len(list(task_dir.glob("proposal-v*.md"))) + 1
    moved = 0
    for old in ("proposal.md", "proposal.json", "critique.json"):
        src = task_dir / old
        if src.exists():
            src.rename(src.with_name(f"{src.stem}-v{n}{src.suffix}"))
            moved += 1
    return n if moved else 0


def _revise_locked(project: str, task: dict, feedback: str, actor: str, **ev) -> dict:
    d = S.task_dir(project, task["slug"])
    n = archive_proposal(d) or len(list(d.glob("proposal-v*.md"))) + 1
    with open(d / "request.md", "a") as f:  # the feedback is part of the request from now on — whoever proposes next reads it
        f.write(f"\n\n## Burak's feedback on proposal v{n} ({S.now()[:16]})\n{feedback}\n")
    task["proposal_started"] = None
    task["feedback_rounds"] = int(task.get("feedback_rounds", 0)) + 1
    _clear_proposal_failure(task)
    return _move(project, task, "requested", actor, answer=ev.pop("answer", "Revise"), note=feedback, **ev)


def revise(project: str, slug: str, feedback: str, actor: str = "burak") -> dict:
    """Decision 50: feedback on a proposal card. The task goes back to *requested*, the old proposal is archived as
    -vN, the feedback is appended to request.md, and the next proposal (agent or L3) must answer it point by point."""
    feedback = (feedback or "").strip()
    if not feedback:
        raise TransitionError("Revise needs feedback: what should change in the proposal")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "proposed":
            raise TransitionError(f"{slug}: revise applies to a proposed task, not {task['state']}")
        dec = task.get("decision") or {}
        if dec:
            dec.update({"chosen": "Revise", "answered": S.now(), "note": feedback})
            task["decision"] = dec
        return _revise_locked(project, task, feedback, actor, question=dec.get("question"))
