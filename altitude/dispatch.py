"""Dispatch one task-owning L2 in its worktree, monitor it, and start its session again once it stopped."""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import json
import os
import subprocess
import re
import uuid
from pathlib import Path

from . import config, engines, git_policy, route, state as S, tasks as T


class DispatchFailure(T.TransitionError):
    """A launch fault that is already recorded as a system fault."""


class ResumeFailure(RuntimeError, T.TransitionError):
    """A session-resume fault that is already recorded as a system fault."""


def record_dispatch_failure(project: str, slug: str, error: object, *, launch: dict | None = None) -> DispatchFailure:
    """Clear the failed launch's transient claim and record the fault; the fault blocks the task."""
    reason = str(error)[:300]
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if (task.get("dispatching") == launch.get("dispatching") if launch else task.get("state") == "queued"):
            task["dispatching"] = None
            S.save_task(project, task)
    S.append_event(project, slug, "dispatch-failed", reason=reason)
    from . import incidents
    incidents.system_fault("dispatch-failed", f"{project}/{slug}: {reason}", project=project, task=slug,
                           expected_block_id=(launch or task).get("block_id"))
    return DispatchFailure(f"dispatch failed: {reason}")


def record_resume_failure(project: str, slug: str, claim_id: str, error: object, *,
                          suppress_retry: bool = False, kind: str = "l2-resume") -> ResumeFailure:
    """Record a failed session relaunch as a system fault; the task stays blocked with the incident."""
    reason = str(error)[:300]
    task = S.load_task(project, slug)
    claim = task.get("resume_claim") or {}
    claim = claim if claim.get("id") == claim_id else {}
    current = claim.get("id") == claim_id and claim.get("block_id") == task.get("block_id")
    T.release_resume_claim(project, slug, claim_id, consume_request=current, suppress_retry=suppress_retry and current)
    if not current and isinstance(error, T.TransitionError):
        return ResumeFailure(reason)
    S.append_event(project, slug, "resume-failed", reason=reason)
    from . import incidents
    incidents.system_fault(kind, f"{project}/{slug}: {reason}", project=project, task=slug,
                           expected_block_id=claim.get("block_id", claim_id))
    return ResumeFailure(f"resume of {project}/{slug} failed: {reason}")


def _stop_replacement(engine: str, worker_id: str, job_root: Path) -> str | None:
    """Best-effort stop which never prevents the durable resume claim from being finalized."""
    try:
        engines.stop_l2_worker(engine, worker_id, job_root=job_root)
        return None
    except Exception as exc:  # noqa: BLE001 — the caller records this alongside the primary bind failure
        return str(exc)[:200]


def _claim_owner_live(claim: dict) -> bool:
    """Whether the daemon process which owns a durable resume claim still exists."""
    try:
        os.kill(int(claim.get("owner_pid")), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _recover_resume_claim(project: str, slug: str, task: dict, *, daemon_request_id: str | None = None,
                          daemon_fence: dict | None = None) -> dict | None:
    """Adopt a launched worker after daemon restart, or safely release a claim made before launch."""
    claim = task.get("resume_claim") or {}
    if not claim or _claim_owner_live(claim):
        return {"already_resuming": True} if claim else None
    worker = claim.get("worker") or {}
    if worker.get("id") and worker.get("sessionId"):
        return {**_bind_resume_worker(project, slug, task, claim, worker,
                                     daemon_request_id, daemon_fence or {}), "recovered": True}
    if claim.get("phase") == "claimed":
        T.release_resume_claim(project, slug, claim["id"], consume_request=False)
        return {"superseded": True} if claim.get("block_id") != task.get("block_id") else None
    # The provider call crossed a daemon crash without returning a worker identity. Retrying could deliver the
    # same inbox batch twice, so preserve it and fail closed for an explicit operator decision.
    reason = "daemon exited while provider resume was launching; worker ownership cannot be proven"
    T.release_resume_claim(project, slug, claim["id"], consume_request=True,
                           suppress_retry=claim.get("block_id") == task.get("block_id"))
    S.append_event(project, slug, "resume-failed", reason=reason)
    from . import incidents
    incidents.system_fault("l2-resume-recovery", f"{project}/{slug}: {reason}", project=project, task=slug,
                           expected_block_id=claim.get("block_id"))
    raise ResumeFailure(f"resume of {project}/{slug} failed: {reason}")


def project_never_list(repo: Path) -> str:
    """Best effort: the 'Never' bullets from the project's rules, else a generic line."""
    md = engines.repository_rules(repo)
    if md:
        lines = [l.strip("- ").strip() for l in md.read_text().splitlines() if re.match(r"^\s*-\s*\*\*?never", l, re.I) or "never" in l.lower()[:40]]
        if lines:
            return "; ".join(l[:160] for l in lines[:8])
    return "no changes outside the brief; no weakened guardrails; honor any recorded merge hold"


def l2_engine(task: dict) -> str:
    """Old task records predate provider identity and are necessarily Claude sessions."""
    return task.get("l2_engine") or "claude"


def l2_job_root(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / "l2-engine"


DAEMON_TASK_OPERATIONS = {
    "preserve-checkout": {"from": ("blocked",), "done": ()},
    "resume": {"from": ("blocked",), "done": ("running", "queued")},
    "stop": {"from": ("running",), "done": ("blocked",)},
    "reject": {"from": ("queued", "running", "blocked", "reported"), "done": ("rejected",)},
}
DAEMON_REQUEST_ACTORS = ("l3", "burak")


def request_task_operation(project: str, slug: str, operation: str, reason: str, *, actor: str) -> dict:
    """Persist one L3/operator request for altd; this process never touches Git or a worker.

    I-20260904-062512: the request and its audit event land under the project lock before the daemon acts. The
    worker identity snapshot prevents a delayed resume, stop, or reject from applying to a replacement session.
    """
    reason = str(reason or "").strip()
    if operation not in DAEMON_TASK_OPERATIONS:
        raise T.TransitionError(f"unknown daemon task operation {operation!r}")
    if not reason:
        raise T.TransitionError(f"task {operation} requires a reason")
    if actor not in DAEMON_REQUEST_ACTORS:
        raise T.TransitionError(f"task {operation} is available only to L3 or Burak")
    contract = DAEMON_TASK_OPERATIONS[operation]
    with S.project_lock(project):
        task = S.load_task(project, slug)
        previous = task.get("daemon_request") or {}
        same = (previous.get("operation"), previous.get("reason"), previous.get("actor")) == (
            operation, reason, actor)
        if previous.get("status") in ("pending", "executing"):
            if same:
                return {"queued": True, "idempotent": True, "request": previous}
            raise T.TransitionError(
                f"{slug}: {previous.get('operation')} is already queued for altd as {previous.get('id')}"
            )
        if previous.get("status") in ("done", "refused", "failed") and same:
            receipt = (previous.get("result_state"), previous.get("result_agent_id"),
                       previous.get("result_session_id"), previous.get("result_block_id"))
            current = (task.get("state"), task.get("agent_id"), task.get("session_id"), task.get("block_id"))
            # A retry is the same operation only while the task still matches its terminal receipt. A later
            # lifecycle may legitimately need the same human reason again, but gets a new request/id/event.
            if (previous.get("result_state") is None or receipt == current) and (
                    operation != "resume" or previous.get("block_id") == task.get("block_id")):
                return {"queued": False, "idempotent": True, "request": previous}
        if task.get("state") not in contract["from"]:
            raise T.TransitionError(
                f"{slug}: cannot {operation} from {task.get('state')}; expected {' or '.join(contract['from'])}"
            )
        request = {"id": uuid.uuid4().hex, "at": S.now(), "operation": operation, "reason": reason,
                   "actor": actor, "status": "pending", "expected_state": task.get("state"),
                   "block_id": task.get("block_id"),
                   "resume_request": task.get("resume_request"),
                   "agent_id": task.get("agent_id"), "session_id": task.get("session_id")}
        task["daemon_request"] = request
        if operation == "resume":
            task["resume_after"] = task.get("resume_after") or request["at"]
        S.save_task(project, task)
        S.append_event(project, slug, "daemon-request", task=slug, request_id=request["id"],
                       operation=operation, reason=reason, by=actor)
        S.regen_state_md(project)
        return {"queued": True, "idempotent": False, "request": request}


def request_setting(project: str | None, setting: str, value, reason: str, *, actor: str) -> dict:
    """2026-09-07 WIP incident: persist an operational change without needing a free task slot."""
    reason = str(reason or "").strip()
    scope = "machine" if project is None else "project"
    if actor not in DAEMON_REQUEST_ACTORS or (project is None and actor == "l3") or not reason:
        authority = "the operator" if project is None else "L3 or the operator"
        raise T.TransitionError(f"{scope} set requires {authority} and a nonempty reason")
    if setting not in (("wip",) if project is None else ("wip", "routing")):
        raise T.TransitionError(f"unknown {scope} setting")
    if setting == "wip":
        try:
            config.validate_wip(value)
        except ValueError as exc:
            raise T.TransitionError(str(exc)) from exc
    if setting == "routing" and value is not None:
        value = config.parse_routing(value)
    with config.projects_lock() if project is None else S.project_lock(project):
        entry = config.machine_settings() if project is None else config.project(project)
        directory = config.ROOT if project is None else config.project_dir(project)
        path = directory / f"{setting}-request.json"
        previous = S.read_json(path, {})
        same = (previous.get(setting), previous.get("reason"), previous.get("actor")) == (value, reason, actor)
        if same and (previous.get("status") == "pending" or previous.get(f"result_{setting}") == entry.get(setting)):
            return {"idempotent": True, "request": previous}
        if setting == "wip" and project is not None:
            try:
                config.validate_wip(value, project=True)
            except ValueError as exc:
                raise T.TransitionError(str(exc)) from exc
        if previous.get("status") == "pending":
            raise T.TransitionError(f"{scope} set already pending in altd")
        request = {"id": uuid.uuid4().hex, "at": S.now(), "operation": f"{scope}-set", "project": project,
                   "actor": actor, "reason": reason, setting: value, "status": "pending"}
        S.write_json(path, request)
        return {"idempotent": False, "request": request}


def run_settings(project: str | None = None) -> dict:
    settings = ("wip",) if project is None else ("wip", "routing")
    return {setting: _run_setting(project, setting) for setting in settings}


def _run_setting(project: str | None, setting: str) -> dict:
    """Altd drains this before task requests, independent of WIP and restart dispatch holds.

    D7/I-20260904-062512: preserve the receipt and deduplicate the project event across a crash.
    Re-registration is deliberate: the last registry write wins, with no registration identity fence.
    """
    with (nullcontext() if project is None else S.project_lock(project)), config.projects_lock():
        directory = config.ROOT if project is None else config.project_dir(project)
        path = directory / f"{setting}-request.json"
        request = S.read_json(path, {})
        if request.get("status") != "pending":
            return request
        projects = config._load_projects() if project is not None else None
        entry = projects.get(project) if projects is not None else config.machine_settings()
        if entry is None:
            request.update(status="refused", note="project is not registered")
        else:
            if request[setting] is None:
                entry.pop(setting, None)
            else:
                entry[setting] = request[setting]
            if projects is None:
                S.write_json(config.ROOT / "settings.json", entry)
            else:
                config.save_projects(projects)
            request["status"] = "done"
        events = directory / "events.jsonl"
        rows = events.read_text().splitlines() if events.exists() else []
        if not any(json.loads(row).get("request_id") == request["id"] for row in rows):
            event = {"at": S.now(), "kind": request["operation"], "project": project, "request_id": request["id"],
                     "actor": request["actor"], "reason": request["reason"], setting: request[setting],
                     "status": request["status"], "note": request.get("note")}
            S.atomic_write(events, "".join(row + "\n" for row in rows) + json.dumps(event) + "\n")
        request.update({f"result_{setting}": entry.get(setting) if entry else None, "completed_at": S.now()})
        S.write_json(path, request)
        return request


def pending_task_operations(project: str) -> list[str]:
    """Task-local daemon requests, including a reject archived just before its receipt was saved."""
    now = S.now()
    pending = [task for task in S.list_tasks(project, include_archive=True)
               if (task.get("daemon_request") or {}).get("status") in ("pending", "executing")
               and not ((task.get("daemon_request") or {}).get("operation") == "resume"
                        and (task.get("resume_after") or "") > now)]
    return [task["slug"] for task in sorted(
        pending, key=lambda task: ((task.get("daemon_request") or {}).get("at") or "", task["slug"]))]


def _finish_task_operation(project: str, slug: str, request_id: str | None, status: str,
                           note: str = "") -> dict:
    with S.project_lock(project):
        task = S.load_task(project, slug)
        request = task.get("daemon_request") or {}
        if request.get("id") != request_id:
            return {"stale": True}
        request.update({"status": status, "completed_at": S.now(), "note": str(note or "")[:300],
                        "result_state": task.get("state"), "result_agent_id": task.get("agent_id"),
                        "result_block_id": task.get("block_id"),
                        "result_session_id": task.get("session_id")})
        task["daemon_request"] = request
        if request.get("operation") == "resume" and status not in ("pending", "executing") and all(
                request.get(key) == task.get(key) for key in ("block_id", "resume_request")):
            task.pop("resume_after", None)
        S.save_task(project, task)
        return {"request": request, "state": task.get("state")}


def run_task_operation(project: str, slug: str) -> dict:
    """Execute one durable request in altd, once, against the worker identity the caller observed.

    I-20260904-062512: ``executing`` is a durable fence. Task transitions re-check its id, state,
    and worker identity while holding the same project lock, so a delayed operation cannot affect a replacement.
    """
    with S.project_lock(project):
        task = S.load_task(project, slug)
        request = dict(task.get("daemon_request") or {})
        status = request.get("status")
        if status not in ("pending", "executing"):
            return {"idempotent": True, "request": request, "state": task.get("state")}
        operation, request_id = request.get("operation"), request.get("id")
        if operation not in DAEMON_TASK_OPERATIONS or not request_id:
            terminal = ("refused", "invalid request")
        else:
            state = task.get("state")
            identity_changed = (
                task.get("agent_id") != request.get("agent_id")
                or task.get("session_id") != request.get("session_id"))
            # I-20260904-062512: resume installs a replacement identity before the final receipt. If altd exits
            # in that narrow window, the durable target state proves this executing request already succeeded.
            if operation == "resume" and status == "executing" and state in DAEMON_TASK_OPERATIONS[operation]["done"]:
                terminal = ("done", "resume already reached its target state")
            elif operation == "preserve-checkout" and status == "executing":
                terminal = ("refused", f"preservation interrupted; inspect archive/checkout-{request_id}, "
                            f"task events and legacy git log -g refs/stash for {request_id} before retrying")
            elif identity_changed:
                terminal = ("refused", "worker identity changed")
            elif operation == "resume" and request.get("block_id") != task.get("block_id"):
                terminal = ("refused", "block changed after resume request")
            elif state in DAEMON_TASK_OPERATIONS[operation]["done"] and operation != "stop":
                terminal = ("done", "already in target state")
            elif state not in DAEMON_TASK_OPERATIONS[operation]["from"] and not (
                    operation == "stop" and state == "blocked"):
                terminal = ("refused", f"task changed to {state}")
            else:
                terminal = None
                if status == "pending":
                    request.update({"status": "executing", "started_at": S.now()})
                    task["daemon_request"] = request
                    S.save_task(project, task)
    if terminal:
        return _finish_task_operation(project, slug, request_id, *terminal)

    try:
        if operation == "resume":
            result = resume(project, slug, daemon_request_id=request_id)
            if result.get("held") or result.get("already_resuming"):
                return {"pending": True, "request": request, **result}
        elif operation == "preserve-checkout":
            result = preserve_checkout(project, slug, request)
        elif operation == "stop":
            result = stop(project, slug, by=request["actor"], reason=request["reason"],
                          daemon_request_id=request_id, expected_agent_id=request.get("agent_id"),
                          expected_session_id=request.get("session_id"))
        else:
            result = T.reject(
                project, slug, request["reason"], actor=request["actor"],
                expected_state=request.get("expected_state"), expected_agent_id=request.get("agent_id"),
                expected_session_id=request.get("session_id"), expected_daemon_request=request_id)
    except ResumeFailure as exc:
        _finish_task_operation(project, slug, request_id, "failed", str(exc))
        raise
    except (T.TransitionError, git_policy.GitPolicyError) as exc:
        return _finish_task_operation(project, slug, request_id, "refused", str(exc))
    return _finish_task_operation(project, slug, request_id, "done", str(result)[:300])


@contextmanager
def publication_settlement(project: str):
    """Serialize the deployment checkout: one gate at a time fast-forwards it and reads its provenance."""
    path = config.project_dir(project) / ".publication-settlement.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def preserve_checkout(project: str, slug: str, request: dict) -> str:
    """Issue #247: preserve a dirty main through altd, before any worker can launch."""
    with publication_settlement(project), S.project_lock(project):
        task = S.load_task(project, slug)
        T._require_daemon_fence(task, slug, expected_daemon_request=request["id"],
                                expected_agent_id=None, expected_session_id=None)
        if task["state"] != "blocked" or task.get("attempt"):
            raise T.TransitionError("preserve-checkout requires a blocked task that has never launched")
        repo = config.project_path(project)
        origin = git_policy.fetch_origin(repo)
        state = git_policy.inspect_repository(repo)
        if not state.determinate or state.branch != "main" or state.head != origin or not state.dirty:
            raise T.TransitionError("preserve-checkout requires dirty main at origin/main; inspect alt repo")
        label = f"Altitude {project}/{slug} preserve-checkout {request['id']}"
        branch = f"archive/checkout-{request['id']}"
        with git_policy.archive_checkout(repo, origin, branch, label) as (sha, index_env):
            task["checkout_archive"] = {"branch": branch, "sha": sha}
            S.save_task(project, task)
            S.append_event(project, slug, "checkout-preserved", branch=branch, sha=sha, request_id=request["id"],
                           reason=request["reason"], by=request["actor"])
            git_policy.clean_archived_checkout(repo, sha, origin, index_env)
        git_policy.fetch_and_require_exact_base(repo)
        return (f"Preserved {branch} at {sha}; inspect the archive; apply its binary diff SHA~2..SHA "
                "with git apply --index in the task worktree, review and deliver a PR; retain until operator removal")


def settle_deploy_checkout(project: str, slug: str) -> None:
    """Do the fast-forward the provenance gate would otherwise only demand.

    I-20260903-075410: an L2 merges its PR while its task is still running, so the deployment checkout stays behind
    origin/main until that task's report lands, and every dispatch in the window refused. A checkout that is clean,
    on main and strictly behind moves to exactly origin/main here; anything else stays untouched for
    `fetch_and_require_exact_base` to refuse with its precise reason."""
    try:
        notes = self_deploy_fast_forward(project, slug)
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError, KeyError):
        return   # not a fast-forward, or no registered checkout to move: the strict gate decides
    if notes:
        S.append_event(project, slug, "self-deploy", note="; ".join(notes))


def _git_branch(worktree: str | Path) -> str | None:
    """The branch git reports for `worktree`, or None if it is absent, detached or git failed."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError):
        return None
    b = (r.stdout or "").strip()
    return b if r.returncode == 0 and b and b != "HEAD" else None


def worktree_branch(slug: str, worktree: str | Path | None = None) -> str:
    """The task owns its checkout; Git supplies its branch, or the task name derives it."""
    derived = f"worktree-{slug}"
    return (_git_branch(worktree) or derived) if worktree else derived


def _task_worktree(repo: Path, project: str, slug: str, origin_sha: str) -> Path:
    """Create or validate the L2 checkout without ever inheriting the deployment checkout's mutable HEAD."""
    import subprocess

    expected_branch = f"worktree-{slug}"
    worktree = repo / ".claude" / "worktrees" / slug

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=120)

    branch_exists = git("show-ref", "--verify", "--quiet", f"refs/heads/{expected_branch}").returncode == 0
    if not worktree.exists():
        if branch_exists:
            raise T.TransitionError(
                f"task branch {expected_branch!r} exists without its registered worktree {worktree}; "
                "quarantine or remove the orphan branch before dispatch"
            )
        worktree.parent.mkdir(parents=True, exist_ok=True)
        made = git("worktree", "add", "-b", expected_branch, str(worktree), origin_sha)
        if made.returncode != 0:
            raise T.TransitionError(f"git worktree add failed: {(made.stderr or made.stdout).strip()[:300]}")
        return worktree

    _validate_task_worktree(repo, project, slug, worktree, origin_sha, require_clean=True)
    return worktree


def _validate_task_worktree(repo: Path, project: str, slug: str, worktree: Path, origin_sha: str,
                            *, require_clean: bool) -> None:
    """Validate an already-created L2 checkout before either a fresh launch or a resume."""
    import subprocess

    expected_path = (repo / ".claude" / "worktrees" / slug).resolve()
    if worktree.resolve() != expected_path or not worktree.is_dir():
        raise T.TransitionError(f"task worktree for {project}/{slug} must be {expected_path}, got {worktree}")
    expected_branch = f"worktree-{slug}"
    actual = _git_branch(worktree)
    if actual != expected_branch:
        raise T.TransitionError(
            f"task worktree {worktree} is on {actual or 'detached HEAD'}, expected {expected_branch!r}"
        )
    task_ref = f"{project}/{slug}"
    task = S.read_json(S.status_path(project, slug), {}) or {}
    missing = git_policy.commits_missing_task_trailer(
        worktree, "main", task_ref, origin_sha=origin_sha,
        adopted_head=(task.get("adopted_pr") or {}).get("head"),
    )
    if missing:
        sample = ", ".join(sha[:12] for sha in missing[:5])
        raise T.TransitionError(
            f"existing task branch {expected_branch!r} has commit(s) without exact "
            f"`Altitude-Task: {task_ref}` provenance: {sample}"
        )
    if require_clean:
        dirty = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=60,
        )
        if dirty.returncode != 0 or (dirty.stdout or "").strip():
            detail = (dirty.stderr or "").strip()[:200]
            raise T.TransitionError(
                f"existing task worktree {worktree} is dirty"
                + (f": {detail}" if detail else " — preserve or clean it before dispatch")
            )


def build_brief(project: str, slug: str) -> str:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    request = (d / "request.md").read_text()
    policy = proj.get("approval", "default")
    if task.get("hold_merge"):  # a recorded hold is the explicit exception to merge-by-default
        merge_policy = f"**Held for Burak** — open the PR, make it ready for any required review, get its checks green, and stop; Burak merges it himself. Why: {task['hold_merge']}"
    else:
        merge_policy = {"default": "Merge when the applicable checks and any appropriate review are complete. Only a brief marked *held* stops at the open PR.",
                        "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when the review is addressed and CI is green."}.get(policy, policy)
    engine = task.get("l2_engine") or task.get("engine") or "pending quota route"
    completion_contract = (
        f"Code delivery writes a concise schema-valid `report.json` (`{config.SCHEMAS / 'report.json'}`) in "
        f"`{d}` so Altitude can verify it. A no-code task may use `alt task done` after sending its result; "
        "Altitude finalizes it only after this worker exits."
    )
    conversation_contract = (
        "They reach you at your next checkpoint: after a tool call or when you are about to stop in a Claude "
        "session, or when this turn ends and Altitude resumes your thread on Codex. Reply in plain language with "
        "`alt task reply \"<message>\"`. Ask directly only when the repository and brief cannot resolve the "
        "choice: checkpoint `progress.md`, reply with the question, then `alt task block \"$ALTITUDE_TASK\" "
        "--reason \"<question>\"` and stop; the answer resumes this session."
        " Supply --recommendation '<approach>' --label '<accept action>' --why '<short rationale>' when "
        "there is a concrete recommended approach. A conversational follow-up wakes you to discuss while "
        "the pending question remains open: waking is not approval to implement the disputed approach. "
        "Use the question identity/revision and original message id supplied in your inbox with "
        "`alt task resolve` to record a clear decision before continuing. Clarify real ambiguity in chat, "
        "without requiring an approval phrase or redundant confirmation. Close obsolete questions with "
        "a cited superseded disposition; retain only still-relevant unanswered parts using --remaining."
        " Use a plain question when no quick choice is useful, or one question with a recommended action "
        "or alternatives. Ask dependent questions sequentially after their prerequisites are settled. "
        "Ask up to three independent questions together using `alt task block \"$ALTITUDE_TASK\" "
        "--questions-file <JSON-file>`. JSON is {\"questions\":[{\"question\":\"...\",\"options\": "
        "[{\"key\":\"a\",\"label\":\"Short action\",\"text\":\"Chosen approach\"}],\"recommended_key\":\"a\",\"why\":\"...\"}]}. "
        "A plain question omits options and recommended_key. Supply up to three options with one explicit "
        "recommended_key. No default or follow-up counts as an answer. Revise existing members by adding "
        "their id; omitted members stay open. Resolve each answered or obsolete member against the same "
        "source message where appropriate; unrelated unanswered members remain open."
    )
    publication_contract = (
        "Every code change uses the isolated branch and a PR. Land with `alt land --message \"<message>\"`; use "
        "`--merge` only when allowed. Read the live `hold_merge` value and never merge around it."
    )
    other_leases = leases(project, exclude=slug)
    overlaps = []
    for other in other_leases:
        shared = shared_paths(task_paths(project, task), other["paths"])
        if shared:
            overlaps.append(f"`{other['slug']}` on {', '.join(shared)}")
    if overlaps:
        publication_contract += (
            f" Shared paths with {'; '.join(overlaps)}: expect to rebase onto main before landing "
            "and keep edits in shared docs to your own sections."
        )
    progress = d / "progress.md"
    if task.get("attempt") and progress.exists():
        request += (f"\n\n---\n\nAttempt {task['attempt']} stopped before finishing. Its worktree and branch are "
                    "kept; its `progress.md` follows.\n\n" + progress.read_text().rstrip() + "\n")
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        engine=engine,
        model=task.get("engine_model") or task.get("model") or "provider default",
        leases=("; ".join(f"`{l['slug']}` on {', '.join(l['paths']) or '(undeclared paths)'}" for l in other_leases) or "none"),
        paths=", ".join(task_paths(project, task)) or "(not declared — stay inside the request's scope)",
        task_dir=d, merge_policy=merge_policy, never_list=project_never_list(config.project_path(project)),
        repo=config.project_path(project),
        branch=worktree_branch(slug, config.project_path(project) / ".claude" / "worktrees" / slug),
        completion_contract=completion_contract, conversation_contract=conversation_contract,
        publication_contract=publication_contract, request=request)
    if task.get("questions"):
        text += "\n\n" + T.group_context(task) + "\n"
    return text


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-attempt settings: edit telemetry and the inbox hook that hands Burak's queued messages to the
    worker after a tool call or when it is about to stop."""
    hooks = config.HOOKS
    inbox = [{"type": "command", "command": f"python3 {hooks / 'inbox.py'}", "timeout": 10}]
    settings = {"hooks": {
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"python3 {hooks / 'edit_count.py'}", "timeout": 10}]},
                        {"hooks": inbox}],
        "Stop": [{"hooks": inbox}],
    }, "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key},
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW}
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


def run(project: str, slug: str, model: str | None = None) -> dict:
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            raise T.TransitionError("Altitude is restarting; retry shortly")
        return _run(project, slug, model)


def _run(project: str, slug: str, model: str | None = None) -> dict:
    # Read task eligibility first, but do not mark or write anything until the deployment checkout has passed
    # its remote-backed gate and this task's worktree has a provenance-safe base.
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "queued":
            raise T.TransitionError(f"{slug} is {task['state']}, not queued")
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
    try:
        repo = config.project_path(project)
        with publication_settlement(project):
            settle_deploy_checkout(project, slug)
            origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
    except git_policy.GitPolicyError as exc:
        # system_fault may acquire state locks, so it deliberately lives outside project_lock.
        from . import incidents
        incidents.system_fault("main-unpushed", f"{project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"dispatch refused by Git provenance gate: {exc}") from exc
    try:
        worktree_path = _task_worktree(repo, project, slug, origin_sha)
    except (git_policy.GitPolicyError, T.TransitionError, subprocess.SubprocessError, OSError) as exc:
        from . import incidents
        incidents.system_fault("task-git-provenance", f"{project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"dispatch refused by task provenance gate: {exc}") from exc
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "queued":
            raise T.TransitionError(f"{slug} is {task['state']}, not queued")
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        proj = config.project(project)
        if model:
            pin = config.pinned_option("l2", proj, engine=task.get("engine"), model=model)
            task.update(model=model, engine=pin["engine"])
            S.save_task(project, task)
        choice = route.pick_engine("l2", forced=task.get("engine"), model=model or task.get("model"), project=proj)
        if not choice.get("engine"):
            raise T.TransitionError(f"engine hold: {choice['why']}")
        engine = choice["engine"]
        selected_model = choice.get("model")
        task.update({"dispatching": S.now(), "worker_started_at": datetime.now(timezone.utc).isoformat(),
                     "l2_engine": engine, "engine_model": selected_model,
                     "launch_model": selected_model, "engine_reasoning_effort": None,
                     "routing": choice["why"], "routing_pinned": choice.get("pinned", False)})
        S.save_task(project, task)
    attempt = task.get("attempt", 0) + 1
    agent = {}
    try:
        settings = session_settings(project, slug, S.session_key(project, slug, attempt))
        tried = []
        while True:
            brief_md = build_brief(project, slug)
            T.brief(project, slug, brief_md, actor="altd")
            res = engines.start_l2(
                engine, worker_name(project, slug, attempt), brief_md, cwd=worktree_path, persona=config.PERSONAS / "l2.md",
                model=selected_model, settings=settings, extra_env=l2_env(project, slug, attempt),
                job_root=l2_job_root(project, slug))
            rejection = res.get("rejection")
            if not rejection:
                break
            route.note_rejection(choice, rejection)
            if not res.get("safe_to_retry"):
                break
            tried.append(route.option_key(choice))
            choice = route.pick_engine("l2", project=proj, forced=task.get("engine"),
                                       model=model or task.get("model"), excluded=tried)
            if not choice.get("engine"):
                with S.project_lock(project):
                    current = S.load_task(project, slug)
                    current["dispatching"] = None
                    S.save_task(project, current)
                raise T.TransitionError(f"engine hold: {choice['why']}")
            engine, selected_model = choice["engine"], choice["model"]
            with S.project_lock(project):
                current = S.load_task(project, slug)
                current.update(l2_engine=engine, launch_model=selected_model, engine_model=selected_model,
                               routing=choice["why"])
                S.save_task(project, current)
    except T.TransitionError:
        raise
    except Exception as exc:
        raise record_dispatch_failure(project, slug, exc, launch=task) from exc
    agent = res.get("agent") or {}
    try:
        if res.get("returncode") != 0:
            raise RuntimeError(f"{engine} L2 launch failed: {res.get('stderr', '')[:300] or res.get('stdout', '')[:300]}")
        if not agent.get("id") or not agent.get("sessionId"):
            raise RuntimeError(f"{engine} L2 returned without a concrete worker id and session id")
        worktree = str(worktree_path)
        T.dispatch(project, slug, attempt=attempt, session_id=agent["sessionId"], agent_id=agent["id"],
                   worktree=worktree, branch=worktree_branch(slug, worktree),
                   l2_engine=engine, engine_model=agent.get("engine_model", selected_model), routing=choice["why"])
    except T.TransitionError as exc:
        if agent.get("id"):
            _stop_replacement(engine, agent["id"], l2_job_root(project, slug))
        try:
            with S.project_lock(project):
                current = S.load_task(project, slug)
                current_state = current.get("state")
                if current.get("dispatching") == task.get("dispatching"):
                    current["dispatching"] = None
                    S.save_task(project, current)
        except (KeyError, OSError, ValueError):
            current_state = None
        if current_state != "queued":
            S.append_event(project, slug, "dispatch-cancelled", reason=str(exc)[:300])
            raise DispatchFailure(str(exc)) from exc
        raise record_dispatch_failure(project, slug, exc, launch=task) from exc
    except Exception as exc:
        if agent.get("id"):
            _stop_replacement(engine, agent["id"], l2_job_root(project, slug))
        raise record_dispatch_failure(project, slug, exc, launch=task) from exc
    return {"attempt": attempt, "engine": engine, "routing": choice["why"],
            "agent": agent, "stdout": res.get("stdout", "")}


def worker_name(project: str, slug: str, attempt: int) -> str:
    return f"{project}/{slug}-{attempt}"


def l2_env(project: str, slug: str, attempt: int) -> dict:
    """What every L2 process needs to name its task and attempt to `alt`."""
    return {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug,
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_ATTEMPT": str(attempt),
            "ALTITUDE_SESSION_KEY": S.session_key(project, slug, attempt)}


def resume(project: str, slug: str, *, daemon_request_id: str | None = None) -> dict:
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            return {"held": "Altitude is restarting; retry shortly"}
        try:
            return _resume(project, slug, daemon_request_id=daemon_request_id)
        except T.TransitionError as exc:
            raise ResumeFailure(str(exc)) from exc  # lifecycle cancellation must not become workflow:resume


def _resume(project: str, slug: str, *, daemon_request_id: str | None = None) -> dict:
    """Start a blocked task's provider session again in its worktree, with whatever waits in its inbox.

    This is the only way a session is launched again, and nothing running is ever replaced: a task blocks when its
    worker exited or sits idle without a report (that worker is stopped first). A task blocked before any launch goes
    back to the queue. A WIP cap or an exhausted usage window keeps the task blocked with `resume_after` set, and
    the next tick tries again."""
    task = S.load_task(project, slug)
    active_request = task.get("daemon_request") or {}
    if daemon_request_id is not None:
        if (active_request.get("id") != daemon_request_id
                or active_request.get("operation") != "resume"
                or active_request.get("status") != "executing"):
            raise T.TransitionError(f"{slug}: daemon resume request {daemon_request_id} is no longer executing")
        daemon_fence = {"expected_agent_id": active_request.get("agent_id"),
                        "expected_session_id": active_request.get("session_id")}
    else:
        daemon_fence = {}
        if active_request.get("status") in ("pending", "executing"):
            raise T.TransitionError(f"{slug}: daemon request {active_request.get('id')} owns this task")
    if task["state"] == "running":
        return {"already_running": True}
    if task["state"] != "blocked":
        raise T.TransitionError(f"{slug} is {task['state']}, not blocked")
    recovered = _recover_resume_claim(project, slug, task, daemon_request_id=daemon_request_id,
                                      daemon_fence=daemon_fence)
    if recovered is not None:
        return recovered
    task = S.load_task(project, slug)
    if task.get("waiting_on") and not task.get("fault") and not task.get("resume_after") and daemon_request_id is None:
        return {"waiting": True}  # I-20260908-045037: an old inbox/wake is not an answer to a new question.
    if not task.get("agent_id") or not task.get("session_id"):
        if task.get("fault") == "main-unpushed":
            try:
                with publication_settlement(project):
                    settle_deploy_checkout(project, slug)
                    git_policy.fetch_and_require_exact_base(config.project_path(project))
            except git_policy.GitPolicyError as exc:
                with S.project_lock(project):
                    current = S.load_task(project, slug)
                    if all(current.get(k) == task.get(k) for k in ("state", "resume_request", "resume_after")):
                        current.pop("resume_after", None)
                        current.pop("resume_request", None)
                        S.save_task(project, current)
                raise ResumeFailure(f"checkout fault remains unresolved: {exc}") from exc
        T.requeue(project, slug, expected_daemon_request=daemon_request_id,
                  expected_block_id=task.get("block_id"), **daemon_fence)
        return {"requeued": True}
    window = engines.window_hold(l2_engine(task))
    hold = f"usage limit: subscription window exhausted, resets {window}" if window else wip_hold(project, task)
    if hold:
        T.mark_resume_held(project, slug, hold, retry_at=window,
                           expected_daemon_request=daemon_request_id,
                           expected_block_id=task.get("block_id"), **daemon_fence)
        return {"held": hold}
    claim = T.claim_resume(project, slug, expected_daemon_request=daemon_request_id,
                           expected_block_id=task.get("block_id"), **daemon_fence)
    if claim is None:
        live = S.load_task(project, slug)
        if live.get("state") == "running":
            return {"already_running": True}
        if live.get("resume_claim"):
            return {"already_resuming": True}
        raise T.TransitionError(f"{slug}: could not claim blocked task for resume")
    task = S.load_task(project, slug)
    cwd = Path(task.get("worktree") or "")
    if not task.get("worktree") or not cwd.is_dir():
        error = T.TransitionError(f"worktree missing for {slug} ({task.get('worktree')}); dispatch again")
        raise record_resume_failure(project, slug, claim["id"], error) from error
    try:
        repo = config.project_path(project)
        with publication_settlement(project):
            settle_deploy_checkout(project, slug)
            origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
            # Uncommitted work is exactly what a resumed session continues; path, branch, and ancestry stay strict.
            _validate_task_worktree(repo, project, slug, cwd, origin_sha, require_clean=False)
    except (git_policy.GitPolicyError, T.TransitionError, subprocess.SubprocessError, OSError) as exc:
        raise record_resume_failure(project, slug, claim["id"], exc, kind="task-git-provenance") from exc
    except Exception as exc:  # noqa: BLE001 — no post-claim infrastructure fault may strand the durable fence
        raise record_resume_failure(project, slug, claim["id"], exc) from exc
    engine, job_root = l2_engine(task), l2_job_root(project, slug)
    try:
        if engines.worker_live(engine, task, job_root=job_root):
            engines.stop_l2_worker(engine, task["agent_id"], job_root=job_root)
            if engines.worker_live(engine, task, job_root=job_root):
                raise T.TransitionError(f"{slug}: worker {task['agent_id']} is still live after stop; try again")
    except Exception as exc:
        raise record_resume_failure(project, slug, claim["id"], exc) from exc
    rows = claim["messages"]
    prompt = T.render_inbox(rows) or "Continue from your progress file."
    if task.get("questions"):
        prompt += "\n\n" + T.group_context(task)
    worker = {}
    try:
        T.update_resume_claim(project, slug, claim["id"], phase="launching")
        with S.project_lock(project):
            current = S.load_task(project, slug)
            current["worker_started_at"] = datetime.now(timezone.utc).isoformat()
            T._save_claim_task(project, current)
        res = engines.resume_l2(
            engine, worker_name(project, slug, task["attempt"]), task["session_id"], prompt, cwd=cwd,
            persona=config.PERSONAS / "l2.md", model=task.get("launch_model", task.get("engine_model")),
            settings=S.task_dir(project, slug) / "settings.json",
            extra_env=l2_env(project, slug, task["attempt"]), job_root=job_root)
        worker = res.get("agent") or {}
        if res.get("returncode") != 0:
            raise RuntimeError(res.get("stderr") or res.get("stdout") or f"exit {res.get('returncode')}")
        if not worker.get("id") or not worker.get("sessionId"):
            raise RuntimeError("no concrete live worker")
    except Exception as exc:
        raise record_resume_failure(project, slug, claim["id"], exc) from exc
    return _bind_resume_worker(project, slug, task, claim, worker, daemon_request_id, daemon_fence)


def _bind_resume_worker(project: str, slug: str, task: dict, claim: dict, worker: dict,
                        daemon_request_id: str | None, daemon_fence: dict) -> dict:
    """Normal completion and restart adoption share binding and unowned-worker cleanup."""
    engine, job_root = l2_engine(task), l2_job_root(project, slug)
    try:
        T.update_resume_claim(project, slug, claim["id"], phase="launched", worker=worker)
        T.resume(project, slug, agent_id=worker["id"], session_id=worker["sessionId"],
                 previous_worker=task["agent_id"], expected_claim=claim["id"],
                 expected_daemon_request=daemon_request_id, **daemon_fence)
    except Exception as exc:  # the task moved on, or its state could not be written: nothing may own the new worker
        try:
            live = S.load_task(project, slug)
        except (KeyError, OSError, ValueError):
            live = {}
        committed = (live.get("state") == "running" and live.get("agent_id") == worker["id"]
                     and live.get("session_id") == worker["sessionId"] and not live.get("resume_claim"))
        if committed:
            # save_task is the commit point. A later event/STATE.md write must not turn the now-owned worker into
            # an orphan; report that ancillary persistence fault without blocking the active task.
            from . import incidents
            incidents.system_fault("l2-resume-bookkeeping", f"{project}/{slug}: {exc}", project=project)
            return {"agent": worker, "bookkeeping_error": str(exc)[:300]}
        stop_error = _stop_replacement(engine, worker["id"], job_root)
        if isinstance(exc, T.TransitionError):
            T.release_resume_claim(project, slug, claim["id"], consume_request=False)
            if stop_error:
                from . import incidents
                incidents.system_fault("l2-resume", f"{project}/{slug}: replacement stop failed: {stop_error}",
                                       project=project)
            raise
        detail = f"{exc}; replacement stop also failed: {stop_error}" if stop_error else exc
        raise record_resume_failure(project, slug, claim["id"], detail, suppress_retry=True) from exc
    return {"agent": worker}


def stop(project: str, slug: str, *, by: str = "burak", reason: str | None = None,
         daemon_request_id: str | None = None, expected_agent_id: object = T._UNSET,
         expected_session_id: object = T._UNSET) -> dict:
    """Abort the task's worker. The task blocks; a message or Resume starts the same session again, Reject ends it."""
    reason = str(reason or f"stopped by {by}").strip()
    task = S.load_task(project, slug)
    active_request = task.get("daemon_request") or {}
    if active_request.get("status") in ("pending", "executing") and (
            active_request.get("id") != daemon_request_id or active_request.get("operation") != "stop"):
        raise T.TransitionError(f"{slug}: daemon request {active_request.get('id')} owns this task")
    if task["state"] == "running":  # block first, so the poll does not read the exiting worker as a death
        T.block(project, slug, reason, actor=by, expected_state="running",
                expected_agent_id=expected_agent_id, expected_session_id=expected_session_id,
                expected_daemon_request=daemon_request_id)
    elif task["state"] != "blocked":
        raise T.TransitionError(f"{slug} is {task['state']}; nothing to stop")
    if task.get("agent_id"):
        note = engines.stop_l2_worker(l2_engine(task), task["agent_id"], job_root=l2_job_root(project, slug))
        S.append_event(project, slug, "stopped", agent_id=task["agent_id"], by=by, reason=reason,
                       note=str(note or "")[:200])
    return S.load_task(project, slug)


def resume_due(project: str) -> list[str]:
    """Blocked tasks with a due daemon request or a turn-boundary inbox.

    A request survives a coordinator exit or daemon restart. An exhausted usage window or a WIP cap keeps the
    task waiting; a task blocked before its first launch is included so the daemon can requeue it.
    """
    now, due = S.now(), []
    for t in S.list_tasks(project):
        if t["state"] != "blocked":
            continue
        if (t.get("daemon_request") or {}).get("status") in ("pending", "executing"):
            continue  # explicit resume/stop/reject is owned by the daemon-operation runner
        claim = t.get("resume_claim") or {}
        if claim:
            if not _claim_owner_live(claim):
                due.append(t["slug"])
            continue  # a stale claim is recovered before ordinary due times, WIP or usage holds
        after = t.get("resume_after") or ""
        inbox_due = (not after and not t.get("waiting_on") and not t.get("fault") and not t.get("resume_failed")
                     and any(row.get("wake", True) for row in T.pending(project, t["slug"])))
        if (not after and not inbox_due) or after > now:
            continue
        if not t.get("agent_id") or not t.get("session_id"):
            due.append(t["slug"])
            continue
        if engines.window_hold(l2_engine(t)):
            continue
        if wip_hold(project, t):
            continue
        due.append(t["slug"])
    return due


def _norm(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def inside_lease(path: str, lease: list[str]) -> bool:
    """Whether a repo-relative file is exactly in, or below, one declared lease entry."""
    normalized = _norm(path).lstrip("/")
    return any(normalized == item or normalized.startswith(item + "/")
               for item in (_norm(entry).lstrip("/") for entry in lease))


def shared_paths(a: list[str], b: list[str]) -> list[str]:
    """Informational shared scope for briefs and status, using the staging lease semantics."""
    return sorted({p for p in map(_norm, a + b) if inside_lease(p, a) and inside_lease(p, b)})


def _split_top_level(value: str) -> list[str]:
    """Split commas outside brace groups and parenthesized annotations."""
    parts, start, brace_depth, paren_depth = [], 0, 0, 0
    for i, char in enumerate(value):
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "," and not brace_depth and not paren_depth:
            parts.append(value[start:i])
            start = i + 1
    parts.append(value[start:])
    return parts


def _annotation_start(value: str) -> int | None:
    """The start of a trailing parenthesized annotation, not parentheses within a filename."""
    start = value.find(" (")
    while start >= 0:
        depth = 0
        for i in range(start + 1, len(value)):
            if value[i] == "(":
                depth += 1
            elif value[i] == ")":
                depth -= 1
                if depth == 0:
                    if not value[i + 1:].strip():
                        return start
                    break
        start = value.find(" (", start + 2)
    return None


def _expand_braces(path: str) -> list[str]:
    """Expand balanced brace groups, including subsequent and nested groups."""
    candidates = [path]
    while any("{" in candidate for candidate in candidates):
        expanded = []
        for candidate in candidates:
            start = candidate.find("{")
            if start < 0:
                expanded.append(candidate)
                continue
            depth, end = 0, None
            for i in range(start, len(candidate)):
                if candidate[i] == "{":
                    depth += 1
                elif candidate[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end is None:
                continue
            members = _split_top_level(candidate[start + 1:end])
            expanded.extend(candidate[:start] + member.strip() + candidate[end + 1:]
                            for member in members if member.strip())
        candidates = expanded
    return [candidate for candidate in candidates if "{" not in candidate and "}" not in candidate]


def _expand_entry(entry: str) -> list[str]:
    """Expand one declared file entry into unannotated paths."""
    entry = entry.strip()
    if not entry or entry.startswith("("):
        return []
    if "{" not in entry and "}" not in entry and " (" not in entry:
        return [entry]
    out = []
    for part in _split_top_level(entry):
        path = part.strip()
        annotation = _annotation_start(path)
        if annotation is not None:
            path = path[:annotation].strip()
        if not path or path.startswith("(") or ("/" not in path and "." not in path):
            continue
        out.extend(_expand_braces(path))
    return out


def task_paths(project: str, task: dict) -> list[str]:
    """The task's declared staging lease; `alt land` refuses changes outside it."""
    entries = task.get("paths") or []
    return [path for entry in entries for path in _expand_entry(str(entry))]


def _lease_tasks(project: str, exclude: str | None = None) -> list[dict]:
    """Tasks whose declared scope is shown, including blocked tasks queued to resume."""
    return [t for t in S.list_tasks(project)
            if t["slug"] != exclude
            and (t["state"] == "running"
                 or (t["state"] == "blocked" and (t.get("resume_after") or t.get("resume_claim")
                     or (not t.get("waiting_on") and not t.get("fault") and not t.get("resume_failed")
                         and T.pending(project, t["slug"])))))]


def leases(project: str, exclude: str | None = None) -> list[dict]:
    """Running and pending-resume tasks and their declared scope, for status and briefs."""
    out = []
    for task in _lease_tasks(project, exclude):
        lease = {"slug": task["slug"], "paths": task_paths(project, task)}
        if task["state"] == "blocked":
            lease["pending_resume"] = True
        out.append(lease)
    return out


def wip_hold(project: str, task: dict | None = None) -> str | None:
    running = [t for t in S.list_tasks(project) if t["state"] == "running"]
    if len(running) >= config.project_wip(project):
        return f"WIP limit: {len(running)} running in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p) if t["state"] == "running")
    if total >= config.machine_wip():
        return f"WIP limit: {total} running on this machine"
    return None


def poll(project: str) -> list[dict]:
    """Return L2 turns that exited, using each task's persisted engine adapter."""
    from . import usage
    task_rows = S.list_tasks(project)
    finished = []
    for t in task_rows:
        if t["state"] in ("running", "blocked", "reported"):
            t = usage.refresh(project, t["slug"])
        report = S.task_dir(project, t["slug"]) / "report.json"
        # I-20260907-165145: an earlier attempt/resume's report cannot account for a vanished worker.
        started = t.get("worker_started_at")
        if not started:
            started = next((e["at"] for e in reversed(S.read_events(project, t["slug"]))
                            if e.get("kind") == "state" and e.get("to") == "running"), t.get("dispatched"))
        try:
            has_report = report.stat().st_mtime >= (datetime.fromisoformat(started).timestamp() if started else 0)
        except FileNotFoundError:
            has_report = False
        if t["state"] == "blocked" and has_report and "idle without a report" in (t.get("blocked_reason") or ""):
            finished.append({"task": t, "agent": None})  # report landed after the idle check: hand it to the verifier
            continue
        if t["state"] != "running":
            continue
        engine = l2_engine(t)
        a = engines.worker(engine, t, job_root=l2_job_root(project, t["slug"]))
        metadata = {key: a[key] for key in ("engine_model", "engine_reasoning_effort") if a and key in a}
        if metadata and any(t.get(key) != value for key, value in metadata.items()):
            with S.project_lock(project):
                current = S.load_task(project, t["slug"])
                if current.get("agent_id") == a.get("id"):
                    current.setdefault("launch_model", current.get("engine_model"))
                    current.update(metadata)
                    S.save_task(project, current)
                    t.update(metadata)
        live_p = config.MONITOR_DIR / f"live-{project}--{t['slug']}.json"
        prev = S.read_json(live_p, {}) or {}
        live = ({"status": a.get("status"), "state": a.get("state"), "engine": engine,
                 "pid": a.get("pid"), "usage": a.get("usage"), **metadata} if a else None)
        idle_since = None
        detail, at = engines.worker_detail(engine, a)
        settled = a and (a.get("state") in ("blocked", "done", "failed", "stopped")
                         or a.get("status") in ("idle", "exited"))
        if settled and not has_report:
            if a.get("rejection"):
                finished.append({"task": t, "agent": a, "rejection": a["rejection"],
                                 "safe_to_retry": a.get("safe_to_retry", False)})
                continue
            if engines.temporary_capacity_in(detail):
                finished.append({"task": t, "agent": a, "capacity": True})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None, "capacity": True})
                continue
            lim = engines.usage_limit_in(detail, now=at)
            if lim:
                finished.append({"task": t, "agent": a, "limited": lim})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None, "limited": lim})
                continue
        if a and a.get("status") == "idle" and a.get("state") != "done" and not has_report:
            idle_since = prev.get("idle_since") or S.now()
        died = (a is None or a.get("state") in ("done", "failed", "stopped") or a.get("status") == "exited") and not has_report
        if died:  # worker gone before a report: raised as a system fault by the server, never read as "still running"
            finished.append({"task": t, "agent": a, "died": True, "detail": detail})
        elif a is None or a.get("state") in ("done", "failed") or a.get("status") == "exited" or (has_report and a.get("status") == "idle"):
            finished.append({"task": t, "agent": a})
        elif idle_since and _seconds_since(idle_since) > IDLE_NEEDS_INPUT_SECONDS:
            finished.append({"task": t, "agent": a, "needs_input": True})
            idle_since = None
        S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": idle_since})
    return finished


IDLE_NEEDS_INPUT_SECONDS = 150


def _seconds_since(iso: str) -> float:
    from datetime import datetime
    import time
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


RESTART_PENDING = "restart-pending.json"
BACKEND_ACTIVATION_DIRS = ("altitude/", "bin/", "systemd/")
WEB_BUILD_INPUTS = (
    "web/src/",
    "web/design/tokens.css",
    "web/index.html",
    "web/package.json",
    "web/pnpm-lock.yaml",
    "web/tsconfig.json",
    "web/vite.config.ts",
)


def activation_component(path: str) -> str | None:
    """The deployed component made older than main by this tracked path, if any."""
    if path.startswith(BACKEND_ACTIVATION_DIRS):
        return "backend"
    if any(path.startswith(item) if item.endswith("/") else path == item for item in WEB_BUILD_INPUTS):
        return "web"
    return None


def self_deploy_fast_forward(project: str, slug: str | None = None) -> list[str]:
    # Activation: a finishing worker must not clear requested_at or change build inputs during activation.
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            return ["deferred self-deploy: activation in progress"]
        return _self_deploy_fast_forward(project, slug)


def _self_deploy_fast_forward(project: str, slug: str | None = None) -> list[str]:
    """When a project's checkout is its deployment, fast-forward it to origin/main, so merged hooks, personas and
    templates are what the next session runs. Loaded backend changes and tracked web build inputs need activation:
    those are announced with an FYI and `monitor/restart-pending.json`, never restarted from here.

    The one implementation `pull_after_done` and the dispatch/resume gate share. Returns notes, empty when the
    project does not deploy from its checkout or the checkout is already at origin/main. Anything that is not a
    pure fast-forward — dirty, on another branch, ahead of origin, or diverged — raises, and every caller keeps
    refusing exactly as before."""
    proj = config.project(project)
    if not proj.get("self_deploy", project == "altitude"):
        return []
    repo = config.project_path(project)
    origin_sha = git_policy.fetch_origin(repo, "main")
    state = git_policy.service_preflight(repo, "main")   # clean, on main, neither ahead nor diverged
    if not state.behind:
        return []
    head = state.head
    pull = subprocess.run(["git", "merge", "-q", "--ff-only", "origin/main"], cwd=str(repo), capture_output=True, text=True, timeout=120)
    if pull.returncode != 0:
        raise git_policy.GitPolicyError(
            f"fast-forward failed: {(pull.stderr or pull.stdout).strip()[:300] or f'exit {pull.returncode}'}"
        )
    files = subprocess.run(["git", "diff", "--name-only", head, origin_sha], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout.split()
    changed = [f for f in files if activation_component(f)]
    notes = [f"self-deploy: main {head[:7]} → {origin_sha[:7]} ({len(files)} files)"]
    if changed:
        pend_p = config.MONITOR_DIR / RESTART_PENDING
        pend = S.read_json(pend_p, {}) or {}
        pending_files = sorted(set(pend.get("files", [])) | set(changed))
        components = sorted({activation_component(path) for path in pending_files} - {None})
        pend = {"since": pend.get("since") or S.now(), "head": origin_sha, "files": pending_files}
        S.write_json(pend_p, pend)
        subject = ("the running Altitude backend and deployed web bundle are" if len(components) == 2 else
                   "the running Altitude backend is" if components == ["backend"] else
                   "the deployed web bundle is")
        T.fyi(project, slug, f"activation pending: {subject} older than main ({len(pending_files)} relevant file(s) "
                             f"changed since {pend['since'][:16]}Z) — Altitude will build, restart safely, and verify "
                             "the API and UI automatically at the next quiet point.")
        notes.append(f"restart pending: activation of {len(pending_files)} {' and '.join(components)} file(s)")
    return notes


def pull_after_done(project: str, task: dict) -> list[str]:
    """Fast-forward the deployment checkout after a task lands; a checkout it may not move is a fault, not a silent skip."""
    try:
        return self_deploy_fast_forward(project, task.get("slug"))
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
        from . import incidents
        incidents.system_fault("self-deploy", f"{project}: {e}", project=project, task=task.get("slug"))
        T.fyi(project, task.get("slug"), f"self-deploy refused in {config.project_path(project)}: {str(e)[:300]}")
        return [f"self-deploy refused: {str(e)[:160]}"]


def _pr_merged_at(repo: Path, task: dict, branch_sha: str) -> bool:
    """True when a verified PR for this task is merged on GitHub at exactly this branch tip (squash merges)."""
    from . import verify
    verified = task.get("verified") if isinstance(task.get("verified"), dict) else {}
    numbers = verified.get("prs") if verified.get("verdict") == "ok" and isinstance(verified.get("prs"), list) else []
    for number in numbers:
        try:
            info = verify.gh(["pr", "view", str(number), "--json", "state,headRefOid"], repo)
        except verify.VerifierFault:
            continue
        if isinstance(info, dict) and info.get("state") == "MERGED" and info.get("headRefOid") == branch_sha:
            return True
    return False


def cleanup_after_done(project: str, task: dict) -> list[str]:
    """After archive, remove the task's worktree and branch once its work is on origin/main and nothing uses it.

    A refusal is a note, never a fault: a tree that is unmerged, dirty, or still in use simply stays for a later
    pass or a manual `git worktree prune`. `pull_after_done` runs afterwards in every case."""
    repo = config.project_path(project)
    slug, wt, branch = task.get("slug") or "", task.get("worktree"), task.get("branch")
    notes: list[str] = []

    def git(*args: str, cwd: Path = repo) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)

    def keep(reason: str) -> list[str]:
        S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
        notes.append(f"kept worktree {Path(wt).name}: {reason}")
        return notes + pull_after_done(project, task)

    if not wt or not branch or not Path(wt).is_dir():
        return pull_after_done(project, task)
    try:
        fetch = git("fetch", "-q", "origin", "main")
        if fetch.returncode != 0:
            return keep(f"could not refresh origin/main: {(fetch.stderr or fetch.stdout).strip()[:120]}")
        tip = git("rev-parse", "--verify", "-q", f"refs/heads/{branch}").stdout.strip()
        merged = bool(tip) and git("merge-base", "--is-ancestor", tip, "refs/remotes/origin/main").returncode == 0
        if not merged and not (tip and _pr_merged_at(repo, task, tip)):
            return keep("branch is not on origin/main")
        status = git("status", "--porcelain", "--untracked-files=all", cwd=Path(wt))
        if status.returncode != 0 or status.stdout.strip():
            return keep("worktree has uncommitted changes")
        if engines.worker_live(l2_engine(task), task, job_root=l2_job_root(project, slug)):
            return keep("L2 worker is still running")
        if task.get("agent_id"):
            note = engines.remove_l2_worker(l2_engine(task), task["agent_id"], job_root=l2_job_root(project, slug))
            notes.append(f"{l2_engine(task)} worker {task['agent_id']}: {(note or 'completed')[:120]}")
        removed = git("worktree", "remove", wt)
        if removed.returncode != 0:
            return keep(f"git worktree remove failed: {(removed.stderr or removed.stdout).strip()[:120]}")
        git("branch", "-D", branch)
    except (subprocess.SubprocessError, OSError, RuntimeError) as e:
        return keep(f"cleanup error: {e}")
    S.append_event(project, slug, "cleanup-worktree", action="removed", worktree=wt, reason="merged into origin/main")
    notes.append(f"removed merged worktree {Path(wt).name}")
    return notes + pull_after_done(project, task)
