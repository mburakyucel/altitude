"""Task-owned adversarial review of captured proposals and changes."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import io
import json
import re
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from . import config, engines, state as S, tasks as T

_inflight: set[tuple[str, str, str]] = set()
_inflight_lock = threading.Lock()


def _owner(task, actor, expected_attempt=None, *, required=False):
    if actor not in ("l2", T.OPERATOR_MESSAGE_ROLE) or required and actor != "l2":
        raise T.TransitionError("Only the task owner or operator can manage its review.")
    if actor == "l2" and (expected_attempt is None or task.get("attempt") != expected_attempt):
        raise T.TransitionError("The review command does not name the current owner attempt.")


def _eligible(task, subject="changes"):
    if task.get("fault") or task.get("stop_id") or task.get("planned_wait"):
        return "Continue or settle the task before requesting review."
    # The held PR's merge question stays open through its review; any other answer can still change the code.
    if subject == "changes" and any(q["status"] == "open" and not T.asks_merge(task, q) for q in task.get("questions", [])):
        return "Settle the open question before requesting changes review; only the held PR's merge question can stay open."
    if task.get("state") == "reported":
        report = S.read_json(S.task_dir(task["project"], task["slug"]) / "report.json")
        if not T.reported_continuable(task, report):
            return "This task has no open delivery to review."
    elif task.get("state") != "running" and not (task.get("state") == "blocked" and T.open_questions(task)):
        return "Review is available when the task owner is running."
    if not task.get("worktree") or not task.get("l2_engine"):
        return "The owner's worktree and engine must be known before review."
    if subject == "changes" and task.get("review_merged_head") and task["review_merged_head"] == _git(Path(task["worktree"]), "rev-parse", "HEAD"):
        return "This delivery already merged. Prepare the next delivery's checkpoint before requesting review."
    return None


def _find(task, identity):
    review = next((r for r in task.get("reviews", []) if r["id"] == identity), None)
    if review is None:
        raise T.TransitionError("Review not found on this task.")
    return review


@contextmanager
def merge_lock(project, slug, *, wait=True):
    """Request admission and the actual merge share a task-local lock, not a second hold field."""
    with open(S.task_dir(project, slug) / ".review-merge.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise T.TransitionError("A merge is being settled. Refresh the task before requesting review.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def active_count():
    return sum(r["state"] == "running" for project in config.load_projects()
               for task in S.list_tasks(project, include_archive=True) for r in task.get("reviews", []))


def busy():
    """A daemon-bound review must return its result before planned activation."""
    with _inflight_lock:
        return bool(_inflight)


def _capacity(task):
    from . import dispatch
    if active_count():
        return "Another cross-engine review is running on this machine."
    owners = sum(dispatch.occupies_slot(t) for p in config.load_projects() for t in S.list_tasks(p))
    # A reported owner must also resume before it can prepare/run the review.
    if owners + (task.get("state") in ("reported", "blocked")) + 1 > config.machine_wip():
        return "No machine capacity for an additional reviewer. Try again when a slot is free."
    return None


def _git(root, *args, binary=False, stdin=None, conflict_ok=False):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", *args],
                            cwd=root, input=stdin, capture_output=True, timeout=120, env=config.subprocess_env())
    if result.returncode and not (conflict_ok and result.returncode == 1):
        reason = (result.stderr.strip() or result.stdout.strip()).decode(errors="replace")[-300:].strip()
        raise T.TransitionError(f"Review checkpoint unavailable: git {args[0]} failed: "
                                + (reason or f"exit status {result.returncode}"))
    output = result.stdout if binary else result.stdout.decode().strip()
    return (output, result.returncode == 1) if conflict_ok else output


def _candidate_tree(root, base, head):
    output, conflicted = _git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", base, head, conflict_ok=True)
    lines = output.splitlines()
    if conflicted:
        paths = ", ".join(dict.fromkeys(lines[1:])) or "unnamed paths"
        raise T.TransitionError(f"The task branch conflicts with current main in {paths}. "
                                "Reconcile the branch with origin/main and commit, then retry.")
    return lines[0]


def _context(project, task):
    rows = [{k: row.get(k) for k in ("id", "at", "role", "text", "images")}
            for row in T.task_messages(project, task["slug"])
            if row.get("role") in (T.OPERATOR_MESSAGE_ROLE, "l3", "l2") and not row.get("removed_at")]
    folder = S.task_dir(project, task["slug"])
    return {"request": (folder / "request.md").read_text(),
            "brief": (folder / "brief.md").read_text() if (folder / "brief.md").exists() else "",
            "messages": rows, "decisions": [q.get("resolution") for q in task.get("questions", []) if q.get("resolution")]}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _identity(project, task, *, fetch=False, candidate=True, proposal_id=None):
    root = Path(task["worktree"])
    from . import dispatch
    dispatch._validate_task_worktree(config.project_path(project), project, task["slug"], root, require_clean=False)
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise T.TransitionError("Commit the selected task changes before capturing or assessing review.")
    if fetch:
        _git(root, "fetch", "--no-tags", "origin", "main")
    head = _git(root, "rev-parse", "HEAD")
    base = _git(root, "rev-parse", "origin/main")
    tree = (_git(root, "rev-parse", head + "^{tree}") if proposal_id else
            _candidate_tree(root, base, head)) if candidate else None
    context = _context(project, task)
    identity = {"head": head, "base": base, "tree": tree}
    if proposal_id:
        proposal = next((r for r in context["messages"] if r["id"] == proposal_id and r["role"] == "l2" and r.get("text")), None)
        if not proposal:
            raise T.TransitionError("Name an original L2 proposal message with --proposal-message before review.")
        context["proposal"] = proposal
        identity.update(proposal_id=proposal_id, proposal_hash=_hash(proposal))
    identity["context_hash"] = _hash(context)
    return identity, context


def _same(left, right):
    return all(left.get(k) == right.get(k) for k in ("head", "base", "tree", "context_hash", "proposal_id", "proposal_hash"))


def _current_reviews(task):
    replaced = {r.get("previous") for r in task.get("reviews", [])}
    return [r for r in task.get("reviews", []) if r["id"] not in replaced and not r.get("merged_head")]


def _request_wait(task, previous=None):
    for row in _current_reviews(task):
        if row["id"] == previous or row["state"] == "withdrawn":
            continue
        if row["state"] != "completed" or not row.get("reconciled"):
            return "Address the existing review request before requesting another review."
    return None


def _project_review(review, task, identity):
    row = {k: copy.deepcopy(v) for k, v in review.items() if k not in ("message", "delivered", "worker", "owner")}
    snapshot = review.get("snapshot") or {}
    assessed = review.get("reconciled") or {}
    matches = lambda saved: all(saved.get(k) == identity.get(k) for k in ("head", "base", "context_hash", "proposal_id", "proposal_hash"))
    coverage = ("unknown" if identity is None else "current" if snapshot and matches(snapshot)
                else "assessed" if assessed and matches(assessed) else "earlier")
    mutable = task.get("state") in ("running", "blocked", "reported") and review in _current_reviews(task)
    latest = next((r for r in reversed(task.get("reviews", [])) if r.get("subject", "changes") == review.get("subject", "changes")), None)
    rerunnable = task.get("state") in ("running", "blocked", "reported") and review == latest
    row.update(coverage=coverage, can_withdraw=mutable and review["state"] not in ("withdrawn", "running"),
               can_cancel=mutable and review["state"] == "running" and not review.get("cancel_requested"),
               can_retry=mutable and review["state"] in ("failed", "cancelled"),
               can_review_latest=rerunnable and review["state"] == "completed" and bool(assessed) and coverage != "current",
               can_review_again=rerunnable and review["state"] == "completed" and bool(assessed) and coverage == "current")
    return row


def view(project, slug):
    from . import route
    task = S.load_task(project, slug)
    task["project"] = project
    settings = config.load_projects().get(project)
    choice = route.pick_review(task, settings) if settings else {"why": "Project is not managed."}
    common = (None if choice.get("engine") else choice.get("why") or "No reviewer is available.") or _capacity(task)
    subjects, history, identities = {}, [], {}
    for review in task.get("reviews", []):
        proposal_id = ((review.get("reconciled") or review.get("snapshot") or {}).get("proposal_id")
                       if review.get("subject", "changes") == "proposal" else None)
        if proposal_id not in identities:
            try:
                identities[proposal_id], _ = _identity(project, task, candidate=False, proposal_id=proposal_id)
            except (T.TransitionError, OSError, subprocess.SubprocessError, KeyError):
                identities[proposal_id] = None
        row = _project_review(review, task, identities[proposal_id])
        why = _eligible(task, review.get("subject", "changes")) or common or _request_wait(task, review["id"])
        for key in ("can_retry", "can_review_latest", "can_review_again"):
            row[key] = row[key] and not why
        history.append(row)
    for subject in ("proposal", "changes"):
        latest = next((r for r in reversed(history) if r.get("subject", "changes") == subject), None)
        why = _eligible(task, subject) or common or _request_wait(task)
        subjects[subject] = {"available": not bool(why), "why": why or "", "latest": latest}
    return {**{k: subjects["changes"][k] for k in ("available", "why")}, "subjects": subjects,
            "engine_label": choice.get("label"), "model": choice.get("model"),
            "same_engine": bool(choice.get("same_engine")), "fallback_reason": choice.get("fallback_reason", ""),
            "allowance_known": bool(choice.get("allowance_known")),
            "latest": history[-1] if history else None, "history": history}


def request(project, slug, *, actor, request_id, focus="", source_id=None, previous=None, expected_attempt=None, subject=None,
            engine=None, model=None):
    from . import dispatch, route
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        raise T.TransitionError("A stable review request identity is required.")
    if not isinstance(focus, str) or len(focus) > 4000:
        raise T.TransitionError("Review focus must be text of at most 4000 characters.")
    if any(value is not None and not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,127}", value))
           for value in (engine, model)):
        raise T.TransitionError("A selected review engine or model must be a plain name.")
    with merge_lock(project, slug, wait=False), dispatch.launch_lock(), S.project_lock(project):
        task = S.load_task(project, slug)
        task["project"] = project
        _owner(task, actor, expected_attempt)
        rows = task.setdefault("reviews", [])
        prior = _find(task, previous) if previous else None
        subject = subject if subject is not None else (prior.get("subject", "changes") if prior else "changes")
        # A retry, rerun or replacement keeps the prior focus; an operator's focus stays and owner text only adds to it.
        kept = prior.get("focus", "") if prior else ""
        operator_scope = bool(prior and prior.get("requested_by") == T.OPERATOR_MESSAGE_ROLE
                              and (prior["state"] != "withdrawn" or prior.get("replaced_by")))
        if operator_scope and kept and focus != kept and not focus.startswith(kept + "\n"):
            focus = kept + ("\n" + focus if focus else "")
        focus = focus or kept
        if len(focus) > 4000:
            raise T.TransitionError("Review focus must be text of at most 4000 characters.")
        # A selection belongs to one request; a retry without one routes automatically and shows that reviewer.
        selection = {"engine": engine, "model": model} if engine or model else None
        if subject not in ("proposal", "changes") or prior and prior.get("subject", "changes") != subject:
            raise T.TransitionError("A review must name the same proposal or changes subject as its prior request.")
        repeated = next((r for r in rows if r["id"] == request_id or source_id and r.get("source_id") == source_id and r.get("subject", "changes") == subject), None)
        if repeated:
            if (repeated.get("focus", "") != focus or repeated.get("source_id") != source_id
                    or repeated.get("previous") != previous or repeated.get("subject", "changes") != subject
                    or repeated.get("selection") != selection):
                raise T.TransitionError("That review request identity already has a different focus or selection.")
            return _project_review(repeated, task, None)
        if why := _eligible(task, subject):
            raise T.TransitionError(why)
        latest = next((r for r in reversed(rows) if r.get("subject", "changes") == subject), None)
        # Naming a waiting request with a different explicit selection replaces it; nothing else re-selects.
        reselect = bool(latest and previous == latest["id"] and latest["state"] == "requested"
                        and (engine or model) and selection != latest.get("selection"))
        if latest and not reselect:
            if previous != latest["id"] or latest["state"] in ("requested", "running"):
                if (engine or model) and latest.get("selection") != selection:
                    raise T.TransitionError("Another review request is open. Name it with --previous to select a different reviewer.")
                return _project_review(latest, task, None)
            if latest["state"] == "completed" and not latest.get("reconciled"):
                raise T.TransitionError("Assess the completed review before requesting another.")
        if why := _request_wait(task, previous):
            raise T.TransitionError(why)
        if why := _capacity(task):
            raise T.TransitionError(why)
        if subject == "changes":
            root = Path(task["worktree"])
            _candidate_tree(root, _git(root, "rev-parse", "origin/main"), _git(root, "rev-parse", "HEAD"))
        choice = route.pick_review(task, config.project(project), **(selection or {}))
        if not choice.get("engine"):
            raise T.TransitionError(choice.get("why") or "No second engine is available.")
        requester = actor
        if source_id:
            source = next((r for r in T.task_messages(project, slug) if r["id"] == source_id and not r.get("removed_at")), None)
            if not source or source.get("role") not in ("l2", T.OPERATOR_MESSAGE_ROLE):
                raise T.TransitionError("The review source must be an original owner or operator message.")
            requester = source["role"]
        # An owner retry cannot turn an operator requirement into an owner-waivable request.
        if latest and latest.get("requested_by") == T.OPERATOR_MESSAGE_ROLE and latest["state"] != "withdrawn":
            requester = T.OPERATOR_MESSAGE_ROLE
        at = T._conversation_time()
        row = {"id": request_id, "requested_at": at, "requested_by": requester, "source_id": source_id,
               "focus": focus, "subject": subject, "state": "requested", "selection": selection,
               "engine": choice["engine"], "model": choice.get("model"),
               "engine_label": choice.get("label"), "same_engine": bool(choice.get("same_engine")),
               "fallback_reason": choice.get("fallback_reason", ""), "allowance_known": bool(choice.get("allowance_known")),
               "owner": {k: task.get(k) for k in ("attempt", "l2_engine")},
               "delivered": actor == "l2", "previous": previous,
               "message": {"id": request_id, "at": at, "role": "system", "by": requester,
                           "review_id": request_id, "text": f"Adversarial {subject} review requested. Prepare a committed source checkpoint, "
                           f"then run alt task review run --review-id {request_id}"
                           + (" --proposal-message <original-L2-proposal-id>" if subject == "proposal" else "")
                           + ". Preserve open approval questions; this request authorizes only review and assessment, not implementation. "
                           + (f"Selected reviewer: {route.option_label(choice)}. " if selection else "") + focus}}
        rows.append(row)
        if reselect:
            latest.update(state="withdrawn", withdrawn_by=actor, finished_at=S.now(), replaced_by=request_id,
                          withdrawal_reason=f"Replaced by review {request_id} with reviewer {route.option_label(choice)}.")
        if task["state"] == "reported":
            task = T.continue_report(project, task, actor=actor, reason="Cross-engine review requested")
        if task["state"] == "blocked":
            task.update(resume_request=request_id, resume_after=S.now())
        S.save_task(project, task)
        S.append_event(project, slug, "review-requested", review_id=request_id, by=requester)
        return _project_review(row, task, None)


def _capture(project, task, review, context_ids, proposal_id=None):
    proposal = review.get("subject", "changes") == "proposal"
    if proposal and not proposal_id or not proposal and proposal_id:
        raise T.TransitionError("Proposal review requires --proposal-message; changes review does not accept it.")
    identity, context = _identity(project, task, fetch=True, proposal_id=proposal_id)
    if not proposal and identity["head"] == identity["base"]:
        raise T.TransitionError("No task changes are ready for review. Prepare a committed checkpoint first.")
    if context_ids is not None:
        available = {r["id"] for r in context["messages"]}
        if not set(context_ids) <= available:
            raise T.TransitionError("Selected review context includes an unavailable message.")
        # Selection narrows owner evidence, never original authority or later corrections.
        context["messages"] = [r for r in context["messages"] if r["role"] != "l2" or r["id"] in context_ids or r["id"] == proposal_id]
    if any(row.get("images") for row in context["messages"]):
        if context_ids is None or not any(r["role"] == "l2" and r.get("text") for r in context["messages"]):
            raise T.TransitionError("Review context includes images. Supply an L2 textual account and select it with --context-message; image bytes are not reviewed.")
        context["limitations"] = ["Original image bytes are not reviewed. Selected L2 evidence supplies their textual account; report any missing visual evidence."]
    entries, omitted = [], {}
    # Deleted/replaced base content also enters changes.patch, so bound both inputs before diffing.
    # Files over 2 MiB stay out of source and patch; the capture names them instead (issue #533).
    for tree in ((identity["tree"],) if proposal else (identity["base"], identity["tree"])):
        total = count = 0
        for raw in _git(Path(task["worktree"]), "ls-tree", "-rlz", tree, binary=True).split(b"\0"):
            if not raw:
                continue
            metadata, name = raw.split(b"\t", 1)
            mode, kind, oid, size = metadata.decode().split()
            path = PurePosixPath(name.decode())
            if path.is_absolute() or ".." in path.parts or mode not in ("100644", "100755") or kind != "blob":
                raise T.TransitionError("Review snapshots require ordinary tracked files; links and special entries are unavailable.")
            count += 1
            if int(size) > 2 * 1024 * 1024:
                omitted[str(path)] = int(size)
                continue
            total += int(size)
            if total > 64 * 1024 * 1024 or count > 10000:
                raise T.TransitionError("Review snapshot exceeds its bounds: 64 MiB per tree, 10000 files.")
            if tree == identity["tree"]:
                entries.append((path, oid))
    # A path over the bound in either tree is omitted from both, so source and patch agree.
    entries = [(path, oid) for path, oid in entries if str(path) not in omitted]
    if omitted:
        context["limitations"] = context.get("limitations", []) + [
            "Paths over 2 MiB in the base or candidate are not captured in source or changes.patch: "
            + ", ".join(f"{path} ({size} bytes)" for path, size in sorted(omitted.items())) + "."]
    text = json.dumps(context, ensure_ascii=False, indent=2)
    if len(text.encode()) > 65536:
        raise T.TransitionError("Review context exceeds 64 KiB. Select relevant L2 evidence with --context-message; authority messages remain included.")
    folder = S.task_dir(project, task["slug"]) / "reviews" / review["id"]
    snapshot = folder / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=False)
    source = snapshot / "source"
    source.mkdir()
    # Blob reads preserve exact Git content even when export-ignore/export-subst attributes exist.
    content = io.BytesIO(_git(Path(task["worktree"]), "cat-file", "--batch", binary=True,
                             stdin="".join(oid + "\n" for _, oid in entries).encode()))
    for path, oid in entries:
        header = content.readline().decode().split()
        if len(header) != 3 or header[:2] != [oid, "blob"]:
            raise T.TransitionError("The captured Git blob is unavailable.")
        data = content.read(int(header[2]))
        if content.read(1) != b"\n":
            raise T.TransitionError("The captured Git blob is incomplete.")
        dest = source.joinpath(*path.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    excluded = ("--", ".", *(f":(exclude,literal){path}" for path in sorted(omitted))) if omitted else ()
    patch = b"" if proposal else _git(Path(task["worktree"]), "diff", "--no-ext-diff", "--no-textconv",
                                      identity["base"], identity["tree"], *excluded, binary=True)
    (snapshot / "changes.patch").write_bytes(patch)
    (snapshot / "context.json").write_text(text)
    (snapshot / "l1.md").write_text((config.PERSONAS / "l1.md").read_text())
    identity.update(context_ids=[r["id"] for r in context["messages"]], captured_at=S.now(),
                    captured_context_hash=_hash(context), selected_owner_evidence=context_ids is not None,
                    limitations=context.get("limitations", []),
                    omitted=[{"path": path, "size": size} for path, size in sorted(omitted.items())],
                    input_hash=_hash({"tree": identity["tree"], "context": text, "patch": hashlib.sha256(patch).hexdigest()}))
    if proposal:
        identity["proposal"] = {k: context["proposal"].get(k) for k in ("id", "at", "text")}
    runtime = folder / "runtime"
    runtime.mkdir()
    return identity, snapshot, runtime


def review_prompt(snapshot, focus, subject="changes"):
    rules = engines.repository_rules(snapshot / "source")
    return ("You are an L1 reviewer. Read l1.md and " + (str(rules.relative_to(snapshot)) if rules else "the supplied task context") + ", "
              "then context.json and changes.patch. Give a relatively quick, focused independent adversarial review. "
              + ("Review the exact proposal in context.json against captured source and authority. Challenge assumptions, design risks and missing acceptance. A proposal review is not implementation review. " if subject == "proposal" else "Review the captured changes against their acceptance. ") +
              "Start with the brief, decisions, diff and requested focus. Check the main correctness, regression, "
              "security and acceptance risks; follow affected callers and tests when needed to substantiate a finding. "
              "Avoid unrelated exploration, cosmetic suggestions and repeated passes without new evidence. "
              "Once those risks are checked, return concise, actionable findings with evidence, what you examined "
              "and anything left uncovered. If the scope is too large for a focused review, identify the remaining "
              "areas and recommend targeted follow-up rather than silently claiming full coverage. "
              "Read/search only these inputs. Do not run tests, tools with external effects, helpers, publication or task commands. "
              "Identify concrete material findings with source/evidence references. State missing evidence and limitations. "
              "No findings is not merge approval. Focus: " + focus)


def run(project, slug, review_id, *, actor, expected_attempt, context_ids=None, proposal_id=None, on_wait=None):
    from . import dispatch, route
    if context_ids is not None and (not isinstance(context_ids, list) or any(not isinstance(item, str) for item in context_ids)):
        raise T.TransitionError("Selected review context must be a list of original message IDs.")
    key = (project, slug, review_id)
    with config.restart_lock() as quiet, dispatch.launch_lock(), S.project_lock(project):
        if not quiet or config.restart_in_progress():
            raise T.TransitionError("Altitude is activating an update. Run the accepted review after activation.")
        task = S.load_task(project, slug)
        task["project"] = project
        _owner(task, actor, expected_attempt, required=True)
        review = _find(task, review_id)
        if review["state"] != "requested":
            return _project_review(review, task, None)
        if task.get("state") != "running":
            raise T.TransitionError("Resume the current owner before running the requested review.")
        if why := _eligible(task, review.get("subject", "changes")):
            raise T.TransitionError(why)
        if review["owner"] != {k: task.get(k) for k in ("attempt", "l2_engine")}:
            raise T.TransitionError("The owner changed. Withdraw or explicitly retry this review on the current attempt.")
        if why := _capacity(task):
            raise T.TransitionError(why)
        choice = route.pick_review(task, config.project(project), **(review.get("selection") or {}))
        why = (None if choice.get("engine") == review["engine"] and choice.get("model") == review["model"]
                                  else "The selected reviewer is no longer available. Explicitly retry to select another.")
        if why:
            review.update(state="failed", error=why, finished_at=S.now())
            S.save_task(project, task)
            return _project_review(review, task, None)
        # Reserve capacity before export/launch. Cancellation retains this reservation until termination is known.
        review.update(state="running", started_at=S.now(), generation=task.get("agent_id"))
        S.save_task(project, task)
        with _inflight_lock:
            _inflight.add(key)
    result = None
    invoked = False
    try:
        identity, snapshot, runtime = _capture(project, task, review, context_ids, proposal_id)
        with S.project_lock(project):
            current = S.load_task(project, slug)
            live = _find(current, review_id)
            live["snapshot"] = identity
            S.save_task(project, current)

        def started(worker):
            with S.project_lock(project):
                current = S.load_task(project, slug)
                live = _find(current, review_id)
                live["worker"] = worker
                S.save_task(project, current)
                return (live["state"] == "running" and not live.get("cancel_requested")
                        and current.get("state") == "running" and current.get("attempt") == expected_attempt
                        and current.get("agent_id") == review.get("generation"))

        def waiting():
            connected = on_wait is None or on_wait()
            with S.project_lock(project):
                current = S.load_task(project, slug)
                live = _find(current, review_id)
                if not connected:
                    live.update(cancel_requested=True, cancel_reason="Review caller disconnected")
                    S.save_task(project, current)
                return (connected and live["state"] == "running" and not live.get("cancel_requested")
                        and current.get("state") == "running" and current.get("attempt") == expected_attempt
                        and current.get("agent_id") == review.get("generation"))

        prompt = review_prompt(snapshot, review["focus"], review.get("subject", "changes"))
        invoked = True
        result = engines.review(prompt, engine=review["engine"], snapshot=snapshot, runtime=runtime,
                                model=review["model"], on_start=started, on_wait=waiting)
    except (OSError, ValueError, T.TransitionError, subprocess.SubprocessError) as exc:
        saved = _find(S.load_task(project, slug), review_id)
        worker = saved.get("worker")
        result = {"error": str(exc), "termination_confirmed": engines.review_active(worker) is False if worker else not invoked}
    except (KeyboardInterrupt, SystemExit):
        saved = _find(S.load_task(project, slug), review_id)
        worker = saved.get("worker")
        result = {"error": "Review execution interrupted.",
                  "termination_confirmed": engines.review_active(worker) is False if worker else not invoked}
        raise
    finally:
        with S.project_lock(project):
            current = S.load_task(project, slug)
            live = _find(current, review_id)
            result = result or {"error": "Review execution interrupted.", "termination_confirmed": False}
            confirmed = result.get("termination_confirmed", False)
            if not confirmed:
                live.update(error="Reviewer termination is unconfirmed; capacity remains reserved.", cancel_requested=True)
            elif live["state"] == "running":
                stopped = (live.get("cancel_requested") or current.get("state") != "running"
                           or current.get("agent_id") != review.get("generation") or current.get("attempt") != expected_attempt)
                live.update(state="cancelled" if stopped else "failed" if result.get("error") else "completed", finished_at=S.now())
                if result.get("error"):
                    live["error"] = result["error"]
                if not stopped and (not result.get("error") or result.get("text")):
                    live["result"] = {k: result.get(k) for k in ("text", "findings", "limitations")}
                live["usage"] = result.get("usage")
            S.save_task(project, current)
        with _inflight_lock:
            _inflight.discard(key)
        if not confirmed:
            _termination_fault(project, current, review_id)
    return view(project, slug)["latest"] if current["reviews"][-1]["id"] == review_id else _project_review(live, current, None)


def assess(project, slug, review_id, *, actor, expected_attempt, dispositions, reason, proposal_id=None):
    with merge_lock(project, slug), S.project_lock(project):
        task = S.load_task(project, slug)
        _owner(task, actor, expected_attempt, required=True)
        review = _find(task, review_id)
        if task.get("state") != "running" or review["state"] != "completed" or not isinstance(reason, str) or not reason.strip():
            raise T.TransitionError("The current owner assesses a completed review with an evidence-bearing reason.")
        findings = {f["id"] for f in review["result"]["findings"]}
        if (not isinstance(dispositions, list) or any(not isinstance(d, dict) for d in dispositions)
                or {d.get("finding_id") for d in dispositions} != findings or len(dispositions) != len(findings)
                or any(d.get("disposition") not in ("fixed", "dismissed") or not isinstance(d.get("reason"), str)
                       or not d["reason"].strip() for d in dispositions)):
            raise T.TransitionError("Give each finding one fixed/dismissed disposition with evidence.")
        if review.get("subject", "changes") == "proposal":
            proposal_id = proposal_id or review["snapshot"]["proposal_id"]
        elif proposal_id:
            raise T.TransitionError("Changes assessment does not accept a proposal message.")
        identity, _ = _identity(project, task, fetch=True, proposal_id=proposal_id)
        review.update(dispositions=dispositions, reconciled={**identity, "reason": reason.strip(), "at": S.now()})
        S.save_task(project, task)
        return _project_review(review, task, identity)


def cancel(project, slug, review_id, *, actor, reason="", expected_attempt=None):
    with S.project_lock(project):
        task = S.load_task(project, slug)
        _owner(task, actor, expected_attempt)
        review = _find(task, review_id)
        if review["state"] != "running":
            return _project_review(review, task, None)
        review.update(cancel_requested=True, cancel_reason=reason or "Review cancelled", cancel_by=actor)
        worker = review.get("worker")
        S.save_task(project, task)
    if worker and engines.review_stop(worker):
        with S.project_lock(project):
            task = S.load_task(project, slug)
            review = _find(task, review_id)
            if review["state"] == "running":
                review.update(state="cancelled", finished_at=S.now())
                S.save_task(project, task)
    return _project_review(review, task, None)


def withdraw(project, slug, review_id, *, actor, reason="", expected_attempt=None):
    with merge_lock(project, slug), S.project_lock(project):
        task = S.load_task(project, slug)
        _owner(task, actor, expected_attempt)
        review = _find(task, review_id)
        if task.get("state") not in ("running", "blocked", "reported"):
            raise T.TransitionError("Finished review history is read-only.")
        if review["state"] == "running":
            raise T.TransitionError("Cancel the running review and confirm it has stopped before withdrawing it.")
        if actor == "l2" and review["requested_by"] != "l2":
            raise T.TransitionError("Only the operator can skip an operator-requested review.")
        if not isinstance(reason, str) or not reason.strip():
            raise T.TransitionError("Record why the review is withdrawn.")
        review.update(state="withdrawn", withdrawn_by=actor, withdrawal_reason=reason, finished_at=S.now())
        S.save_task(project, task)
        return _project_review(review, task, None)


def require_merge(project, slug, pair):
    task = S.load_task(project, slug)
    for review in _current_reviews(task):
        if review["state"] == "withdrawn":
            continue
        if review["state"] != "completed" or not review.get("reconciled"):
            raise T.TransitionError("Adversarial review must finish and receive the owner's dispositions before merging.")
        identity, _ = _identity(project, task, proposal_id=review["reconciled"].get("proposal_id"))
        if (not _same(review["reconciled"], identity) or identity["base"] != pair["base_sha"]
                or identity["head"] != pair["head_sha"]):
            raise T.TransitionError("Code, base, proposal or context changed after review assessment. Assess the current candidate before merging.")


def cancel_attached(project, slug, reason):
    for review in S.load_task(project, slug).get("reviews", []):
        if review["state"] == "running":
            cancel(project, slug, review["id"], actor=T.OPERATOR_MESSAGE_ROLE, reason=reason)


def _termination_fault(project, task, review_id):
    from . import incidents
    if task.get("fault") != "review-termination":
        incidents.system_fault("review-termination", f"Reviewer {review_id} termination is unknown; capacity stays reserved. "
                               "Verify its recorded unit has stopped before resuming the owner; do not retry the provider.",
                               project=project, task=task["slug"], expected_task=task)


def poll(project):
    """Reconcile orphaned runs without restarting a model or claiming an unknown termination."""
    for task in S.list_tasks(project, include_archive=True):
        for review in task.get("reviews", []):
            if review["state"] != "running":
                continue
            key = (project, task["slug"], review["id"])
            with _inflight_lock:
                if key in _inflight:
                    continue
            worker = review.get("worker")
            active = engines.review_active(worker) if worker else None
            if active is False:
                with S.project_lock(project):
                    current = S.load_task(project, task["slug"])
                    row = _find(current, review["id"])
                    if row["state"] == "running":
                        row.update(state="failed", error="Review interrupted; no complete result was saved. Retry explicitly.", finished_at=S.now())
                        S.save_task(project, current)
            elif active is None:
                _termination_fault(project, task, review["id"])
            else:
                cancel(project, task["slug"], review["id"], actor=T.OPERATOR_MESSAGE_ROLE,
                       reason="Review invocation interrupted; retry explicitly")
