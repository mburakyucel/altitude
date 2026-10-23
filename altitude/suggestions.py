"""Suggested tasks the operator decides: the Weekly improvement review's (`improve`) and a project agent run's
(`project_agents`). Each owner keeps its runs in its own record; a run holds `suggestions`, L3's check (`post`, what
it kept and set aside) and the operator's `decisions`. This module is the one decision model both read: the states,
the card's buttons, the lapse, the presses, typed answers and the one task each accepted suggestion makes.

A suggestion is named by its ref: "<run>/<n>" for a review and "agent:<agent>/<run>/<n>" for an agent run. See
docs/ARCHITECTURE.md#suggested-tasks.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import l3, state as S, tasks as T

LAPSE = timedelta(days=7)
REASON = 200
REVIEW = re.compile(r"([0-9a-f]{12})/([1-9])")
AGENT = re.compile(r"agent:([0-9a-f]{12})/([1-9][0-9]*)/([1-9])")
REVIEW_RUN = re.compile(r"[0-9a-f]{12}")
AGENT_RUN = re.compile(r"agent:([0-9a-f]{12})/([1-9][0-9]*)")
ALLOWED = {("undecided", "accepted"), ("undecided", "dismissed"), ("dismissed", "accepted"),
           ("not decided", "accepted")}
#: The card's buttons: Make a task and Dismiss on an undecided suggestion, Undo on a dismissed one until it lapses.
BUTTONS = {"accept": ("undecided", "accepted"), "dismiss": ("undecided", "dismissed"),
           "undo": ("dismissed", "undecided")}


def _owner(kind: str):
    from . import improve, project_agents
    return improve if kind == "review" else project_agents


def run_ref(text: str):
    """The owner and run key a run ref names: a review id, or "agent:<agent>/<run>"."""
    text = str(text or "")
    if REVIEW_RUN.fullmatch(text):
        return _owner("review"), text
    if match := AGENT_RUN.fullmatch(text):
        return _owner("agent"), (match.group(1), int(match.group(2)))
    raise ValueError("a run is named by its review id, or agent:<agent>/<run> for a routine run")


def parse(ref: str):
    """The owner, run key and number suggestion `ref` names."""
    ref = str(ref or "")
    if match := REVIEW.fullmatch(ref):
        return _owner("review"), match.group(1), int(match.group(2))
    if match := AGENT.fullmatch(ref):
        return _owner("agent"), (match.group(1), int(match.group(2))), int(match.group(3))
    raise ValueError("a suggestion is named <run>/<number>, or agent:<agent>/<run>/<number>, as its status shows it")


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _at(now: datetime) -> str:
    return now.replace(microsecond=0).isoformat()


def state(run: dict, n: int, now: datetime) -> str:
    """Undecided, accepted, dismissed, or not decided once a week passed without a decision (or after an Undo)."""
    decided = [d for d in run["decisions"] if d["n"] == n]
    if decided and decided[-1]["to"] != "undecided":
        return decided[-1]["to"]
    return "not decided" if lapsed(run, now) else "undecided"


def lapsed(run: dict, now: datetime) -> bool:
    return now - _date(run["post"]["at"]) >= LAPSE


def shown(run: dict, n: int) -> bool:
    return bool(run.get("post")) and n in run["post"]["shown"]


def checking(run: dict) -> bool:
    """Whether L3's check of the run's suggestions is queued or running: nothing is shown before it posts."""
    return bool(run.get("check")) and not run.get("post")


def choose(run: dict, keep: list[int], drop: list[tuple[int, str]]) -> tuple[list[int], dict[str, str]]:
    """L3's post, validated: every suggestion kept or set aside exactly once, each set aside with its reason."""
    named = [*keep, *(n for n, _ in drop)]
    numbers = {s["n"] for s in run["suggestions"]}
    if len(named) != len(set(named)) or set(named) != numbers:
        raise ValueError(f"Keep or drop each of {', '.join(map(str, sorted(numbers)))} exactly once.")
    drops = {str(n): " ".join(why.split()) for n, why in drop}
    if any(not why or len(why) > REASON for why in drops.values()):
        raise ValueError(f"Each drop needs a reason of 1–{REASON} characters.")
    return sorted(keep), drops


def outcome(project: str, run: dict, n: int, now: datetime) -> str:
    """What became of a suggestion, as the next run that may repeat it reads it."""
    post = run.get("post") or {}
    if post.get("unfinished"):
        return "set aside: L3 did not finish checking it"
    if str(n) in post.get("drops", {}):
        return f"set aside by L3: {post['drops'][str(n)]}"
    if not post:
        return "L3 is checking it"
    current = state(run, n, now)
    if current == "accepted":
        made = task(project, ref_of(run, n))
        return f"made a task ({S.load_task(project, made)['state']})" if made else "accepted, task not yet made"
    return {"undecided": "waiting for the operator"}.get(current, current)


def ref_of(run: dict, n: int) -> str:
    return f"agent:{run['agent']}/{run['n']}/{n}" if "agent" in run else f"{run['id']}/{n}"


def _pressed(record: dict, request_id: str, action: str, ref: str) -> bool:
    """Whether web press `request_id` was already saved: the receipts are the record's, so an id made one press on
    one suggestion, and reusing it for any other is refused."""
    done = record.setdefault("presses", {}).get(request_id)
    if done and done != [action, ref]:
        raise ValueError("this press was already saved for another change")
    return bool(done)


def _locked(project: str, ref: str):
    """The owner, its record and the run `ref` names, read under the caller's project lock, and the number."""
    owner, key, n = parse(ref)
    record = owner._load(project)
    return owner, record, owner.suggested_run(record, key), n


def decision(project: str, run: dict, n: int, now: datetime) -> dict:
    ref = ref_of(run, n)
    title = next(s["title"] for s in run["suggestions"] if s["n"] == n)
    return {"suggestion": ref, "title": title, "state": state(run, n, now), "task": task(project, ref)}


def decide(project: str, ref: str, action: str, request_id: str, *, now: datetime | None = None) -> dict:
    """The operator's press on suggestion `ref`: Make a task, Dismiss or Undo. Make a task queues the one L3 turn
    that opens its task. The same request again returns its receipt; a press the card no longer allows is refused."""
    now = now or datetime.now(timezone.utc)
    if action not in BUTTONS:
        raise ValueError("the action is accept, dismiss or undo")
    start, to = BUTTONS[action]
    with S.project_lock(project):
        owner, record, run, n = _locked(project, ref)
        if not shown(run, n):
            raise ValueError(f"suggestion {ref} was not shown to the operator")
        if _pressed(record, request_id, action, ref):
            return decision(project, run, n, now)
        current = state(run, n, now)
        if current != start or action == "undo" and lapsed(run, now):
            raise ValueError(f"This suggestion is {current} now.")
        made = {"n": n, "from": current, "to": to, "at": _at(now), "by": "operator"}
        if to == "accepted":
            made["turn"] = l3.queue_locked(project, owner.task_turn(run, n), trigger="improvement-task")["id"]
        run["decisions"].append(made)
        record["presses"][request_id] = [action, ref]
        owner._save(project, record)
    return decision(project, run, n, now)


def retry(project: str, ref: str, request_id: str, *, now: datetime | None = None) -> dict:
    """Try again on an accepted suggestion whose task turn ended without a task: queue that same single turn.
    One task per suggestion still holds in `alt task new`, so no retry makes a second."""
    now = now or datetime.now(timezone.utc)
    owner, key, n = parse(ref)
    accepted = next((d for d in owner.suggested_run(owner._load(project), key)["decisions"]
                     if d["n"] == n and d["to"] == "accepted"), None)
    # Read before the lock: a turn leaves the queue under L3's own guards, which are never taken inside it. Under the
    # lock the turn must still be the one read, or another Try again has queued a newer one.
    seen = accepted and accepted.get("turn")
    opening = bool(seen and l3.pending(project, seen))
    with S.project_lock(project):
        owner, record, run, n = _locked(project, ref)
        if _pressed(record, request_id, "retry", ref):
            return decision(project, run, n, now)
        accepted = next((d for d in run["decisions"] if d["n"] == n and d["to"] == "accepted"), None)
        if not accepted:
            raise ValueError(f"suggestion {ref} is not accepted")
        if task(project, ref):
            raise ValueError(f"suggestion {ref} already has its task")
        if opening or accepted.get("turn") != seen:
            raise ValueError("L3 is still opening its task.")
        accepted["turn"] = l3.queue_locked(project, owner.task_turn(run, n), trigger="improvement-task")["id"]
        accepted["retried"] = _at(now)
        record["presses"][request_id] = ["retry", ref]
        owner._save(project, record)
    return decision(project, run, n, now)


def resolve(project: str, ref: str, outcome_: str, message_id: str, reason: str, *,
            now: datetime | None = None) -> dict:
    """Record the operator's typed answer on suggestion `ref`, citing their message."""
    now = now or datetime.now(timezone.utc)
    reason = " ".join(str(reason or "").split())
    if outcome_ not in ("accepted", "dismissed"):
        raise ValueError("the outcome is accepted or dismissed")
    if not reason or len(reason) > REASON:
        raise ValueError(f"give the scope you read from the operator's message, 1–{REASON} characters")
    said = next((m for m in T._decision_messages(project, "", "project") if m["id"] == message_id), None)
    with S.project_lock(project):
        owner, record, run, n = _locked(project, ref)
        if not shown(run, n):
            raise ValueError(f"suggestion {ref} was not shown to the operator")
        if not said or _date(said["at"]) <= _date(run["post"]["at"]):
            raise ValueError("the answer must be the operator's own project-chat message after the suggestions")
        last = next((d for d in reversed(run["decisions"]) if d["n"] == n and d.get("message")), None)
        if last and (last["message"], last["to"]) == (message_id, outcome_):
            return decision(project, run, n, now)
        if last and _date(said["at"]) <= _date(last["said"]):
            raise ValueError(f"suggestion {ref} was decided on that message or a later one; a change needs the "
                             "operator's newer message")
        current = state(run, n, now)
        if (current, outcome_) not in ALLOWED:
            raise ValueError(f"suggestion {ref} is {current}; it cannot become {outcome_}")
        # A typed acceptance is carried out in the turn answering it: that turn is the one opening its task.
        run["decisions"].append({"n": n, "from": current, "to": outcome_, "at": _at(now), "by": "l3",
                                 "message": message_id, "said": said["at"], "reason": reason,
                                 **({"turn": message_id} if outcome_ == "accepted" else {})})
        owner._save(project, record)
    return decision(project, run, n, now)


def post(project: str, run: str, keep: list[int], drop: list[tuple[int, str]], *,
         now: datetime | None = None) -> dict:
    """L3's check of run `run` (a run ref), posted by its owner."""
    owner, key = run_ref(run)
    return owner.post(project, key, keep, drop, now=now)


def task(project: str, ref: str) -> str | None:
    """The task made for suggestion `ref`, active or archived."""
    return next((t["slug"] for t in S.list_tasks(project, include_archive=True) if t.get("improvement") == ref), None)


def made(project: str) -> dict[str, str]:
    """Every suggestion's task by its ref, for a view drawing many."""
    return {t["improvement"]: t["slug"] for t in S.list_tasks(project, include_archive=True) if t.get("improvement")}


def require_accepted_locked(project: str, ref: str) -> None:
    """`alt task new --improvement`, under the caller's project lock: one task per accepted suggestion."""
    _, _, run, n = _locked(project, ref)
    if not shown(run, n) or state(run, n, datetime.now(timezone.utc)) != "accepted":
        raise ValueError(f"suggestion {ref} is not accepted")
    if existing := task(project, ref):
        raise ValueError(f"suggestion {ref} already has task {existing}")


def item(project: str, run: dict, s: dict, made_: dict[str, str], now: datetime) -> dict:
    """One shown suggestion as its card draws it: its state, its task, Undo, whether L3 is opening its task and whether
    it waits in Needs you: undecided, or accepted without its task for LAPSE after the last press, Make a task or Try
    again."""
    ref, current = ref_of(run, s["n"]), state(run, s["n"], now)
    accepted = next((d for d in run["decisions"] if d["n"] == s["n"] and d["to"] == "accepted"), None)
    untasked = current == "accepted" and ref not in made_ and accepted
    return {"ref": ref, "n": s["n"], "title": s["title"], "explanation": s["explanation"], "state": current,
            "task": made_.get(ref), "undo": current == "dismissed" and not lapsed(run, now),
            "opening": bool(untasked and accepted.get("turn") and l3.pending(project, accepted["turn"])),
            "waits": current == "undecided" or bool(
                untasked and now - _date(accepted.get("retried") or accepted["at"]) < LAPSE)}


def origin(project: str, ref: str | None) -> str | None:
    """Where a task made from a suggestion came from: its agent's name, or the Weekly improvement review."""
    if not ref:
        return None
    try:
        owner, key, _ = parse(ref)
    except ValueError:
        return None
    return owner.origin(project, key)
