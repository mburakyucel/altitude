"""Task lifecycle — five states, one writer. Every transition goes through here."""
from __future__ import annotations
from pathlib import Path

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
    "blocked": {"running", "parked", "rejected", "reported"},
    "reported": {"done", "running", "blocked"},                    # running: verifier says not done → resume
    "parked": {"requested", "rejected"},
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
    if to == "running":
        task["dispatching"] = None
    S.save_task(project, task)
    S.append_event(project, task["slug"], "state", frm=frm, to=to, by=actor, **ev)
    if to in ("parked", "rejected") and frm in ("running", "blocked") and task.get("agent_id"):
        from . import engines
        note = engines.claude_rm(task["agent_id"])
        S.append_event(project, task["slug"], "session-stopped", agent_id=task["agent_id"], note=note[:200])
    S.regen_state_md(project)
    return task


def new(project: str, title: str, cls: str, request: str, actor: str = "l3", source: str = "chat", model: str | None = None,
        paths: list[str] | None = None, engine: str | None = None, hold_merge: str | None = None) -> dict:
    if source == "recovery" and cls in ("auto", None):
        raise TransitionError("recovery delegation requires an explicit S, M, or L class; it cannot wait for automatic sizing")
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
            slug = f"{base}-{n}"
        recovery_claimed = False
        if source == "recovery":
            from . import recovery
            try:
                recovery.claim_repair(project, slug, actor=actor)
            except ValueError as exc:
                raise TransitionError(str(exc)) from exc
            recovery_claimed = True
        d = S.tasks_dir(project) / slug
        try:
            d.mkdir(parents=True)
            S.atomic_write(d / "request.md", request.rstrip() + "\n")
        except Exception:
            if recovery_claimed:
                recovery.release_failed_claim(project, slug)
            raise
        task = {"slug": slug, "title": title, "class": cls, "state": "requested", "created": S.now(),
                "attempt": 0, "proposal_attempts": 0, "dispatch_id": None, "session_id": None, "agent_id": None,
                "worktree": None,
                "branch": None, "prs": [], "envelope": dict(ENVELOPE[cls]) if cls else {}, "estimate": {}, "spend": {},
                "decision": None, "blocked_reason": None, "source": source, "verified": None, "model": model, "paths": [p.strip() for p in (paths or []) if p.strip()],
                "engine": engine,  # decision 45: a forced engine for every L1 of this task (None = by quota)
                "hold_merge": (hold_merge or "").strip() or None}  # decision 48: why Burak merges this one himself (None = the L2 merges)
        if source == "recovery":
            task["state"] = "approved"
        try:
            S.save_task(project, task)
        except Exception:
            if recovery_claimed:
                recovery.release_failed_claim(project, slug)
            raise
        S.append_event(project, slug, "new", by=actor, cls=cls, title=title, source=source,
                       recovery_delegated=source == "recovery")
        if source == "recovery":
            S.append_event(project, slug, "state", frm="requested", to="approved", by=actor,
                           recovery_delegated=True)
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
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = None
        return _move(project, task, "rejected", actor, reason=reason)


def park(project: str, slug: str, reason: str, actor: str = "l3") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        return _move(project, task, "parked", actor, reason=reason)


def unpark(project: str, slug: str, actor: str = "l3") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _clear_proposal_failure(task)
        return _move(project, task, "requested", actor)


def brief(project: str, slug: str, brief_md: str, actor: str = "l3") -> Path:
    with S.project_lock(project):
        d = S.task_dir(project, slug)
        S.atomic_write(d / "brief.md", brief_md.rstrip() + "\n")
        S.append_event(project, slug, "brief", by=actor, bytes=len(brief_md))
        return d / "brief.md"


def dispatch(project: str, slug: str, *, dispatch_id: str, session_id: str | None, agent_id: str | None,
             worktree: str | None, branch: str | None, actor: str = "altd") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.update({"dispatch_id": dispatch_id, "session_id": session_id, "agent_id": agent_id,
                     "worktree": worktree, "branch": branch, "blocked_reason": None,
                     "dispatched": S.now()})
        task["attempt"] = int(dispatch_id.rsplit("-", 1)[-1]) if dispatch_id.rsplit("-", 1)[-1].isdigit() else task["attempt"] + 1
        return _move(project, task, "running", actor, dispatch_id=dispatch_id, session_id=session_id)


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


def block(project: str, slug: str, reason: str, actor: str = "altd") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task["blocked_reason"] = reason
        return _move(project, task, "blocked", actor, reason=reason)


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
        return _move(project, task, "running", actor, **ev)


def done(project: str, slug: str, actor: str = "l3", digest: str = "") -> dict:
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
    """Open Decision cards: proposed tasks with an unanswered question, plus blocked tasks."""
    out = []
    for t in S.list_tasks(project):
        if t["state"] == "proposed" and t.get("decision") and not t["decision"].get("chosen"):
            out.append({"project": project, "slug": t["slug"], "class": t["class"], "title": t["title"],
                        "question": t["decision"]["question"], "options": t["decision"]["options"],
                        "asked": t["decision"].get("asked"), "kind": "decision", "detail": t["decision"].get("detail"),
                        "context": t["decision"].get("context")})
        elif t["state"] == "blocked" and not t.get("resume_after"):  # held by Altitude (decision 44) is not a decision
            out.append({"project": project, "slug": t["slug"], "class": t["class"], "title": t["title"],
                        "question": f"Stopped mid-task: {short_reason(t.get('blocked_reason') or 'no reason recorded')}",
                        "options": ["Resume", "Park", "Reject"], "asked": t.get("updated"), "kind": "blocked",
                        "detail": t.get("blocked_reason")})
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
