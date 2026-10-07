"""Dispatch one task-owning L2 in its worktree, monitor it, and start its session again once it stopped."""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import subprocess
import shlex
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode

from . import config, engines, git_policy, images, platform, project_setup, route, state as S, tasks as T


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
    if isinstance(error, project_setup.SetupError):
        project_setup.block_task(project, slug, error, expected_block_id=claim.get("block_id", claim_id))
    else:
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
    return platform.process_identity_live(claim.get("owner_process"))


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


def l2_engine(task: dict) -> str:
    """Old task records predate provider identity and are necessarily Claude sessions."""
    return task.get("l2_engine") or "claude"


def l2_job_root(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / "l2-engine"


DAEMON_TASK_OPERATIONS = {
    "handoff": {"from": ("blocked",), "done": ("queued",)},
    "preserve-checkout": {"from": ("blocked",), "done": ()},
    "resume": {"from": ("blocked",), "done": ("running", "queued")},
    "stop": {"from": ("running",), "done": ("blocked",)},
    "reject": {"from": ("queued", "running", "blocked", "reported"), "done": ("rejected",)},
}
DAEMON_REQUEST_ACTORS = ("l3", config.OPERATOR_ACTOR)


def _ci_api(project: str, repository: str, suffix: str, *, method: str = "GET") -> dict:
    """Bounded GitHub IO for one project-selected workflow run; no caller-supplied commands."""
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    env.pop("GH_HOST", None)
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "--method", method,
         f"repos/{repository}/actions/{suffix}"], cwd=config.project_path(project), env=env,
        capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"GitHub {method} failed (exit {result.returncode}); inspect the selected run")
    if len(result.stdout) > 2_000_000:
        raise ValueError("CI evidence exceeds the bounded response size")
    return json.loads(result.stdout or "{}")


def _ci_run(project: str, repository: str, run_id: int) -> dict:
    run = _ci_api(project, repository, f"runs/{run_id}")
    if (run.get("id") != run_id or (run.get("repository") or {}).get("full_name", "").lower() != repository.lower()
            or not isinstance(run.get("run_attempt"), int) or run["run_attempt"] < 1
            or not isinstance(run.get("workflow_id"), int) or not run.get("status")):
        raise ValueError("CI run identity or attempt is unavailable")
    return run


def _ci_finish(project: str, task: dict, record: dict, evidence: dict, *, observation: dict | None = None) -> None:
    record.update(status="notifying", evidence=evidence, observation=observation, due_at=None, finished_at=S.now())
    S.save_task(project, task)
    S.append_event(project, task["slug"], "ci-recheck-result", request_id=record["id"],
                   status=record["status"], evidence=evidence)


def run_ci_recheck(project: str, slug: str) -> None:
    """One tick of the finite CI probe. A persisted submission intent is never submitted twice."""
    from . import l3, server
    with S.project_lock(project):
        task = S.load_task(project, slug)
        record = task.get("ci_recheck") or {}
        if record.get("status") not in ("pending", "probing", "notifying"):
            return
        if record["status"] != "notifying" and not T.ci_recheck_current(task, record):
            record.update(status="invalidated", due_at=None, error="task identity or lifecycle changed")
            S.save_task(project, task)
            return
        notifying = record["status"] == "notifying"
        now = datetime.fromisoformat(S.now())
        if not notifying:
            if record["reads"] >= 24 or now >= datetime.fromisoformat(record["deadline"]):
                _ci_finish(project, task, record, {"error": "CI probe exhausted its read/deadline bound; recovery unverified. "
                                                        "Next: register another finite recheck or message the owner."})
                notifying = True
            elif now < datetime.fromisoformat(record["due_at"]):
                return
            else:
                record.update(status="probing", reads=record["reads"] + 1,
                              due_at=(now + timedelta(minutes=5)).isoformat())
                S.save_task(project, task)  # a crash consumes this read round too
        request_id = record["id"]
    if notifying:
        l3.queue_ci_recheck(project, slug)
        return
    try:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=config.project_path(project),
                                capture_output=True, text=True, timeout=10)
        url = server.repository_url(origin.stdout) if origin.returncode == 0 else None
        if not url:
            raise ValueError("CI recheck requires a GitHub project origin")
        repository = url.removeprefix("https://github.com/")
        if record.get("repository") and record["repository"] != repository:
            raise ValueError("CI project repository changed")
        target = record.get("target") or record["run"]
        run = _ci_run(project, repository, target)
        baseline = {"conclusion": run.get("conclusion")
                    if datetime.fromisoformat(run["updated_at"]) < datetime.fromisoformat(record["at"]) else None,
                    "artifact_upload": "unverified"}
        if not record.get("target"):
            query = urlencode({"branch": run.get("head_branch") or "", "event": run.get("event") or "",
                               "per_page": 20})
            candidates = _ci_api(project, repository, f"workflows/{run['workflow_id']}/runs?{query}")
            relevant = [item for item in candidates.get("workflow_runs", [])[:20]
                        if all(item.get(key) == run.get(key) for key in ("workflow_id", "head_branch", "event"))
                        and [pr["number"] for pr in item.get("pull_requests", [])] == [pr["number"] for pr in run.get("pull_requests", [])]
                        and (item.get("head_repository") or {}).get("full_name") == (run.get("head_repository") or {}).get("full_name")
                        and (item.get("status") != "completed"
                             or datetime.fromisoformat(item["updated_at"]) >= datetime.fromisoformat(record["at"]))]
            if relevant:
                fresh_id = max(relevant, key=lambda item: item.get("id", 0))["id"]
                run = _ci_run(project, repository, fresh_id)
                target = fresh_id
        with S.project_lock(project):
            task = S.load_task(project, slug)
            current = task.get("ci_recheck") or {}
            if current.get("id") != request_id or not T.ci_recheck_current(task, current):
                return
            current["repository"] = repository
            if not current.get("target"):
                current["baseline"] = baseline
                current["target"] = target
                # A relevant live run or fresh completed execution supplies evidence without a write.
                fresh = (run["status"] != "completed"
                         or datetime.fromisoformat(run["updated_at"]) >= datetime.fromisoformat(current["at"]))
                if not fresh and not current.get("wait"):  # a wait observes; only a fault recovery reruns
                    observed_attempt = run["run_attempt"]
                    run = _ci_run(project, repository, current["run"])
                    current["target"] = target = current["run"]
                    if (run["status"] == "completed" and run["run_attempt"] == observed_attempt
                            and datetime.fromisoformat(run["updated_at"]) < datetime.fromisoformat(current["at"])):
                        current["submission"] = {"baseline_attempt": run["run_attempt"], "at": S.now(), "status": "intent"}
                        S.save_task(project, task)  # required before the only external write
                        try:
                            _ci_api(project, repository, f"runs/{target}/rerun", method="POST")
                            current["submission"]["status"] = "accepted"
                        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
                            current["submission"]["status"] = "uncertain"
                        S.save_task(project, task)
                        return
                S.save_task(project, task)
            submission = current.get("submission") or {}
            if submission and run["run_attempt"] <= submission["baseline_attempt"]:
                return  # read until bounded expiry; an uncertain write is never blindly repeated
            if submission:
                submission.update(status="observed", attempt=run["run_attempt"])
                S.save_task(project, task)
        if run["status"] != "completed":
            return
        attempt = _ci_api(project, repository, f"runs/{target}/attempts/{run['run_attempt']}")
        if attempt.get("run_attempt") != run["run_attempt"] or attempt.get("status") != "completed":
            raise ValueError("CI execution attempt evidence is unavailable")
        artifacts = _ci_api(project, repository, f"runs/{target}/artifacts?per_page=100")
        start = max(datetime.fromisoformat(record["at"]), datetime.fromisoformat(attempt["run_started_at"]))
        finished = datetime.fromisoformat(attempt["updated_at"])
        uploaded = [item["id"] for item in artifacts.get("artifacts", [])[:100]
                    if not item.get("expired") and item.get("size_in_bytes", 0) > 0
                    and start <= datetime.fromisoformat(item["created_at"]) <= finished]
        observation = {"conclusion": run.get("conclusion"), "artifact_upload": "available" if uploaded else "unverified"}
        evidence = {"url": f"{url}/actions/runs/{target}", "attempt": run["run_attempt"],
                    **observation, "artifact_ids": uploaded,
                    "note": "Artifacts prove this execution uploaded; tolerated step success alone proves no capacity recovery. "
                            "Reruns retain their original workflow; owners still need their own candidate checks and holds."}
        with S.project_lock(project):
            task = S.load_task(project, slug)
            current = task.get("ci_recheck") or {}
            if current.get("id") == request_id and T.ci_recheck_current(task, current):
                _ci_finish(project, task, current, evidence, observation=observation)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        with S.project_lock(project):
            task = S.load_task(project, slug)
            current = task.get("ci_recheck") or {}
            if current.get("id") == request_id and T.ci_recheck_current(task, current):
                current["error"] = str(exc)[:300]
                current["read_failures"] = current.get("read_failures", 0) + 1
                if current["read_failures"] >= 3:
                    _ci_finish(project, task, current, {"error": "CI evidence reads failed three times; recovery unverified. "
                                                               "Next: check the run by hand, then register another finite recheck or message the owner."})
                else:
                    S.save_task(project, task)
    l3.queue_ci_recheck(project, slug)


def _require_handoff(project: str, task: dict, engine: str, attempt: int) -> dict:
    # I-20260909-232345: recover an exited owner, never override a pin or a decision-only wait.
    if (task.get("state") != "blocked" or task.get("attempt") != attempt or not attempt
            or not task.get("agent_id") or not task.get("session_id")
            or (task.get("fault") != "l2-died" and not task.get("usage_limit"))
            or task.get("completion_requested") or task.get("resume_claim") or task.get("dispatching")):
        raise T.TransitionError("handoff requires the expected launched attempt blocked by worker exit or usage limit, with no active claim")
    if engine == l2_engine(task):
        raise T.TransitionError("handoff requires another engine; use ordinary resume for this engine")
    choice = route.pick_task(config.project(project), {**task, "next_engine": engine})
    if not choice.get("engine"):
        raise T.TransitionError(choice["why"])
    return choice


def handoff(project: str, slug: str, request: dict) -> dict:
    with config.restart_lock() as ready, S.project_lock(project):
        if not ready or config.restart_in_progress():
            raise T.TransitionError("Altitude is restarting; retry shortly")
        task = S.load_task(project, slug)
        fence = {"expected_daemon_request": request["id"], "expected_agent_id": request.get("agent_id"),
                 "expected_session_id": request.get("session_id"), "expected_block_id": request.get("block_id")}
        T._require_daemon_fence(task, slug, **fence)
        _require_handoff(project, task, request["engine"], request["attempt"])
        job_root = l2_job_root(project, slug)
        if engines.worker_live(l2_engine(task), task, job_root=job_root):
            raise T.TransitionError("handoff refuses a live worker; observe its exit before retrying")
        engines.remove_l2_worker(l2_engine(task), task["agent_id"], job_root=job_root)
    return T.requeue(project, slug, engine=request["engine"], clear_worker=True, **fence,
                     reason=request["reason"], actor=request["actor"],
                     previous_engine=l2_engine(task), previous_agent_id=task["agent_id"],
                     previous_session_id=task["session_id"], attempt=task["attempt"])


def request_task_operation(project: str, slug: str, operation: str, reason: str, *, actor: str,
                           engine: str | None = None, expected_attempt: int | None = None,
                           generation: object = T._UNSET, stop_id: object = T._UNSET,
                           deliver_reason: bool = True, send_now: str | None = None) -> dict:
    """Persist one L3/operator request for altd; this process never touches Git or a worker.

    A resume's authored reason reaches the resumed owner; the UI's buttons send fixed text and deliver none.

    I-20260904-062512: the request and its audit event land under the project lock before the daemon acts. The
    worker identity snapshot prevents a delayed resume, stop, or reject from applying to a replacement session.
    """
    reason = str(reason or "").strip()
    if operation not in DAEMON_TASK_OPERATIONS:
        raise T.TransitionError(f"unknown daemon task operation {operation!r}")
    if not reason:
        raise T.TransitionError(f"task {operation} requires a reason")
    if actor not in DAEMON_REQUEST_ACTORS:
        raise T.TransitionError(f"task {operation} is available only to L3 or the operator")
    contract = DAEMON_TASK_OPERATIONS[operation]
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if send_now:
            receipt = _send_now_admission(project, slug, task, send_now)
            if receipt is not None:
                return receipt
        continuing_send_now = operation == "stop" and task.get("state") == "blocked" and task.get("send_now")
        if operation == "stop" and generation is not T._UNSET and generation != task.get("agent_id"):
            raise T.TransitionError("The worker changed. Refresh before stopping it.")
        if operation == "resume" and stop_id is not T._UNSET:
            if stop_id != task.get("stop_id"):
                raise T.TransitionError("Refresh the stopped task before continuing this session.")
            if stop_id is not None:
                view = T.steering_view(task, S.read_events(project, slug), job_root=l2_job_root(project, slug))
                if view["state"] not in ("stopped", "resuming"):
                    raise T.TransitionError("Refresh the stopped task before continuing this session.")
        previous = task.get("daemon_request") or {}
        same = (previous.get("operation"), previous.get("reason"), previous.get("actor"),
                previous.get("engine"), previous.get("attempt") if operation == "handoff" else None,
                previous.get("deliver_reason") if operation == "resume" else None, previous.get("send_now")) == (
            operation, reason, actor, engine, expected_attempt, deliver_reason if operation == "resume" else None, send_now)
        if previous.get("status") in ("pending", "executing"):
            if same:
                return {"queued": True, "idempotent": True, "request": previous}
            raise T.TransitionError(
                f"{slug}: {previous.get('operation')} is already queued for altd as {previous.get('id')}"
            )
        if previous.get("status") in ("done", "refused", "failed") and same and not send_now:
            receipt = (previous.get("result_state"), previous.get("result_agent_id"),
                       previous.get("result_session_id"), previous.get("result_block_id"))
            current = (task.get("state"), task.get("agent_id"), task.get("session_id"), task.get("block_id"))
            # A retry is the same operation only while the task still matches its terminal receipt. A later
            # lifecycle may legitimately need the same human reason again, but gets a new request/id/event.
            if (previous.get("result_state") is None or receipt == current) and (
                    operation != "resume" or previous.get("block_id") == task.get("block_id")):
                return {"queued": False, "idempotent": True, "request": previous}
        if (task.get("state") not in contract["from"] and not continuing_send_now
                and not (operation == "resume" and task.get("state") == "reported")):
            raise T.TransitionError(
                f"{slug}: cannot {operation} from {task.get('state')}; expected {' or '.join(contract['from'])}"
            )
        if operation == "handoff":
            _require_handoff(project, task, engine, expected_attempt)
        if operation == "resume" and task.get("state") == "reported":
            task = T.continue_report(project, task, actor=actor, reason=reason)
        if continuing_send_now:
            T._supersede_resume(task)
        request = {"id": uuid.uuid4().hex, "at": S.now(), "operation": operation, "reason": reason,
                   "actor": actor, "status": "pending", "expected_state": task.get("state"),
                   "block_id": task.get("block_id"),
                   "resume_request": task.get("resume_request"),
                   "agent_id": task.get("agent_id"), "session_id": task.get("session_id")}
        if send_now:
            request["send_now"] = send_now
        if operation == "resume":
            request["deliver_reason"] = deliver_reason
        if operation == "handoff":
            request.update(engine=engine, attempt=expected_attempt)
        task["daemon_request"] = request
        if operation == "stop":
            task["stop_id"] = request["id"]
        if operation == "resume":
            task["resume_after"] = task.get("resume_after") or request["at"]
        S.save_task(project, task)
        S.append_event(project, slug, "daemon-request", task=slug, request_id=request["id"],
                       operation=operation, reason=reason, by=actor)
        S.regen_state_md(project)
        return {"queued": True, "idempotent": False, "request": request}


def send_now_unavailable(project: str, task: dict, *, own_request: str | None = None) -> str | None:
    """Project saved eligibility without acquiring transient launch or admission locks."""
    request = task.get("daemon_request") or {}
    if task.get("fault"):
        return "The owner is faulted; verified recovery must resume it first."
    if task.get("waiting_on"):
        return "The owner is waiting for an answer. Reply to its question first."
    if task.get("stop_id") and task.get("stop_id") != own_request:
        return "The owner is stopped or stopping. Continue its session first."
    if task.get("state") != "running":
        return "Send now needs a running owner."
    if not task.get("agent_id") or not task.get("session_id"):
        return "The owner has no saved session to continue."
    if task.get("resume_claim") or task.get("dispatching") or (request.get("status") in ("pending", "executing")
                                                              and request.get("id") != own_request):
        return "Another owner action is in progress."
    if config.restart_in_progress():
        return "Altitude is restarting; retry shortly."
    if hold := resume_engine_hold(task) or wip_hold(project, task):
        return hold
    return None


def _send_now_launch_hold(project: str, *, check_launch: bool = True) -> str | None:
    """Check transient admission only when requesting or executing an interruption."""
    with config.provider_admission() as held:
        if held:
            return held
    with project_setup.operation_lock(project) as ready:
        if not ready:
            return "Project setup is in progress; retry shortly."
    if check_launch:
        with launch_lock(wait=False) as ready:
            if not ready:
                return "Another session is starting; retry shortly."
    return None


def request_send_now(project: str, slug: str, message_id: str) -> dict:
    """Claim an existing operator row for Stop and saved-session continuation, without resending it."""
    if not message_id:
        raise T.TransitionError("Choose a queued operator message.")
    return request_task_operation(project, slug, "stop", "Deliver the selected queued message now",
                                  actor=T.OPERATOR_MESSAGE_ROLE, deliver_reason=False, send_now=message_id)


def _send_now_admission(project: str, slug: str, task: dict, message_id: str) -> dict | None:
    """Called under the operation's writer lock, shared with inbox pickup and Remove."""
    row = next((row for row in T.task_messages(project, slug) if row["id"] == message_id), None)
    if row is None or row.get("role") != row.get("by") or row.get("role") != T.OPERATOR_MESSAGE_ROLE:
        raise T.TransitionError("Only queued operator messages can be sent now.")
    receipt = (task.get("message_deliveries") or {}).get(message_id) or {}
    if receipt.get("state") == "removed":
        raise T.TransitionError("This message was removed.")
    request = task.get("daemon_request") or {}
    if (request.get("send_now") == message_id and request.get("status") in ("pending", "executing")
            or task.get("send_now") == message_id):
        return {"queued": True, "idempotent": True, "request": request}
    claimed = {row["id"] for row in (task.get("resume_claim") or {}).get("messages", [])}
    if (receipt or message_id in claimed
            or message_id not in {row["id"] for row in T.pending(project, slug)}):
        return {"queued": False, "idempotent": True}
    if message_id not in T.removable_messages(project, slug, task):
        raise T.TransitionError("This message is already owned by a decision or delivery.")
    if reason := send_now_unavailable(project, task) or _send_now_launch_hold(project):
        raise T.TransitionError(reason)
    return None


def _run_send_now(project: str, slug: str, *, admission_held: str | None = None) -> dict:
    """Stop and claim share machine capacity; held continuation uses the ordinary resume timer."""
    with launch_lock(), config.restart_lock() as ready:
        with S.project_lock(project):
            task = S.load_task(project, slug)
            request = task.get("daemon_request") or {}
            identity = request.get("id")
            if not request.get("send_now") or request.get("status") not in ("pending", "executing"):
                return {"idempotent": True, "request": request}
            changed = any(request.get(key) != task.get(key) for key in ("block_id", "agent_id", "session_id"))
            reason = "The owner changed before delivery; refresh its status." if changed else None
            if not reason and task.get("state") == "running":
                reason = (admission_held or ("Altitude is restarting; retry shortly." if not ready else None)
                          or send_now_unavailable(project, task, own_request=identity)
                          or _send_now_launch_hold(project, check_launch=False))
            elif not reason and (task.get("state") != "blocked" or task.get("send_now") != request["send_now"]):
                reason = "A newer owner wait superseded this delivery request."
            if reason:
                if task.get("stop_id") == identity:
                    task.pop("stop_id", None)
                    S.save_task(project, task)
                return _finish_task_operation_locked(project, task, identity, "refused", reason)
            request.update(status="executing", started_at=request.get("started_at") or S.now())
            S.save_task(project, task)
        try:
            stop(project, slug, by=request["actor"], reason=request["reason"],
                 daemon_request_id=identity, expected_agent_id=request.get("agent_id"),
                 expected_session_id=request.get("session_id"))
            with S.project_lock(project):
                task = S.load_task(project, slug)
                stop_block = (task.get("daemon_request") or {}).get("block_id")
                T._require_daemon_fence(task, slug, expected_daemon_request=identity,
                                        expected_agent_id=request.get("agent_id"),
                                        expected_session_id=request.get("session_id"),
                                        expected_block_id=stop_block)
                if task.get("send_now") != request["send_now"]:
                    raise T.TransitionError("A newer owner wait superseded this delivery request.")
                task.update(resume_after=S.now(), resume_request=request["send_now"])
                _finish_task_operation_locked(project, task, identity, "done", "Stopped for selected message delivery")
        except (T.TransitionError, git_policy.GitPolicyError) as exc:
            return _finish_task_operation(project, slug, identity, "refused", str(exc))
        except Exception as exc:
            _finish_task_operation(project, slug, identity, "failed", str(exc))
            raise
        # Stop is complete. Resume owns its claim, failures and selected input from this point.
        if admission_held or not ready or config.restart_in_progress():
            hold = admission_held or "Altitude is restarting; delivery continues after restart."
            T.mark_resume_held(project, slug, hold, expected_block_id=stop_block)
            return {"held": hold}
        return _resume(project, slug)


MACHINE_SETTINGS = ("wip", "voice", "projects_folder", "operator_name", "incident_repository", "terminal", "update_check")


def request_setting(project: str | None, setting: str, value, reason: str, *, actor: str) -> dict:
    """2026-09-07 WIP incident: persist an operational change without needing a free task slot."""
    reason = str(reason or "").strip()
    scope = "machine" if project is None else "project"
    if actor not in DAEMON_REQUEST_ACTORS or (project is None and actor == "l3") or not reason:
        authority = "the operator" if project is None else "L3 or the operator"
        raise T.TransitionError(f"{scope} set requires {authority} and a nonempty reason")
    if setting not in (MACHINE_SETTINGS if project is None else config.PROJECT_SETTINGS):
        raise T.TransitionError(f"unknown {scope} setting")
    try:
        unavailable = platform.container_setting_error(setting, value) if project is None else None
        if unavailable:
            raise ValueError(unavailable)
        if setting == "wip":
            config.validate_wip(value)
        elif setting == "voice":
            config.validate_voice(value)
        elif setting == "projects_folder":
            config.validate_projects_folder(value)
        elif setting == "operator_name":
            config.validate_operator_name(value)
        elif setting == "incident_repository":
            config.validate_incident_repository(value)
        elif setting in ("terminal", "update_check") and not isinstance(value, bool):
            raise ValueError(f"the {setting.replace('_', ' ')} setting is on or off")
        elif setting in config.DEFAULT_SETTINGS:
            config.validate_project_default(setting, value)
        elif setting == "l2_preference":
            config.validate_preference(value)
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
        if same and (previous.get("status") == "pending" or previous.get("status") == "done"
                     and previous.get(f"result_{setting}") == entry.get(setting)):
            return {"idempotent": True, "request": previous}
        if previous.get("status") == "pending":
            raise T.TransitionError(f"{scope} set already pending in altd")
        request = {"id": uuid.uuid4().hex, "at": S.now(), "operation": f"{scope}-set", "project": project,
                   "actor": actor, "reason": reason, setting: value, "status": "pending"}
        S.write_json(path, request)
        return {"idempotent": False, "request": request}


def run_settings(project: str | None = None) -> dict:
    settings = MACHINE_SETTINGS if project is None else config.PROJECT_SETTINGS
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
        unavailable = platform.container_setting_error(setting, request.get(setting)) if project is None else None
        if unavailable:
            request.update(status="refused", note=unavailable)
        if setting == "projects_folder":  # the folder can vanish or lose access before altd drains the CLI request
            try:
                config.validate_projects_folder(request[setting])
            except ValueError as exc:
                request.update(status="refused", note=str(exc))
        if entry is None:
            request.update(status="refused", note="project is not registered")
        elif request["status"] == "pending":
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


def forget_speech_service() -> None:
    """Delete a speech-service URL and key saved before that option was removed, once, at daemon start. The event
    names only the removal, never the URL or key."""
    with config.projects_lock():
        settings = config.machine_settings()
        receipt = config.ROOT / "voice-request.json"
        request = S.read_json(receipt, {})
        stale_setting = isinstance(settings.get("voice"), dict)
        stale_receipt = isinstance(request.get("voice"), dict) or isinstance(request.get("result_voice"), dict)
        if not stale_setting and not stale_receipt:
            return
        if stale_setting:
            del settings["voice"]
            S.write_json(config.ROOT / "settings.json", settings)
        receipt.unlink(missing_ok=True)
        events = config.ROOT / "events.jsonl"
        rows = events.read_text().splitlines() if events.exists() else []
        event = {"at": S.now(), "kind": "machine-set", "project": None, "actor": "altd",
                 "reason": "speech-service option removed", "voice": None, "status": "done"}
        S.atomic_write(events, "".join(row + "\n" for row in rows) + json.dumps(event) + "\n")


def pending_task_operations(project: str) -> list[str]:
    """Task-local daemon requests, including a reject archived just before its receipt was saved."""
    now = S.now()
    pending = [task for task in S.list_tasks(project, include_archive=True)
               if (task.get("daemon_request") or {}).get("status") in ("pending", "executing")
               and not ((task.get("daemon_request") or {}).get("operation") == "resume"
                        and (task.get("resume_after") or "") > now)]
    return [task["slug"] for task in sorted(
        pending, key=lambda task: ((task.get("daemon_request") or {}).get("at") or "", task["slug"]))]


def _handoff_requeued(task: dict, request: dict) -> bool:
    return (request.get("operation") == "handoff" and task.get("state") == "queued"
            and task.get("next_engine") == request.get("engine")
            and task.get("attempt") == request.get("attempt") and not task.get("agent_id")
            and not task.get("session_id") and task.get("block_id") == request.get("block_id"))


def _finish_task_operation(project: str, slug: str, request_id: str | None, status: str,
                           note: str = "") -> dict:
    with S.project_lock(project):
        return _finish_task_operation_locked(project, S.load_task(project, slug), request_id, status, note)


def _finish_task_operation_locked(project: str, task: dict, request_id: str | None, status: str,
                                  note: str = "") -> dict:
    request = task.get("daemon_request") or {}
    if request.get("id") != request_id:
        return {"stale": True}
    if request.get("operation") == "handoff" and request.get("status") == "done":
        return {"request": request, "state": task.get("state"), "idempotent": True}
    if _handoff_requeued(task, request):
        status, note = "done", "handoff requeued the observed attempt"
    request.update({"status": status, "completed_at": S.now(), "note": str(note or "")[:300],
                    "result_state": task.get("state"), "result_agent_id": task.get("agent_id"),
                    "result_block_id": task.get("block_id"), "result_session_id": task.get("session_id")})
    task["daemon_request"] = request
    if request.get("send_now") and status in ("failed", "refused"):
        if task.get("send_now") == request["send_now"]:
            task.pop("send_now", None)
        if task.get("block_id") != request.get("block_id") and task.get("stop_id") == request_id:
            task.pop("stop_id", None)
    if request.get("operation") == "resume" and status not in ("pending", "executing") and all(
            request.get(key) == task.get(key) for key in ("block_id", "resume_request")):
        task.pop("resume_after", None)
    S.save_task(project, task)
    return {"request": request, "state": task.get("state")}


def run_task_operation(project: str, slug: str) -> dict:
    with config.provider_admission() as held:
        return _run_task_operation(project, slug, admission_held=held)


def _run_task_operation(project: str, slug: str, *, admission_held: str | None = None) -> dict:
    """Execute one durable request in altd, once, against the worker identity the caller observed.

    I-20260904-062512: ``executing`` is a durable fence. Task transitions re-check its id, state,
    and worker identity while holding the same project lock, so a delayed operation cannot affect a replacement.
    """
    request = S.load_task(project, slug).get("daemon_request") or {}
    if request.get("send_now") and request.get("status") in ("pending", "executing"):
        return _run_send_now(project, slug, admission_held=admission_held)
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
            elif status == "executing" and _handoff_requeued(task, request):
                terminal = ("done", "handoff already requeued the observed attempt")
            elif operation == "preserve-checkout" and status == "executing":
                terminal = ("refused", f"preservation interrupted; inspect archive/checkout-{request_id}, "
                            f"task events and legacy git log -g refs/stash for {request_id} before retrying")
            elif identity_changed:
                terminal = ("refused", "worker identity changed")
            elif operation == "handoff" and request.get("attempt") != task.get("attempt"):
                terminal = ("refused", "attempt changed after handoff request")
            elif operation in ("resume", "handoff") and request.get("block_id") != task.get("block_id"):
                terminal = ("refused", "block changed after resume request")
            elif state in DAEMON_TASK_OPERATIONS[operation]["done"] and operation != "stop":
                terminal = ("done", "already in target state")
            elif state not in DAEMON_TASK_OPERATIONS[operation]["from"] and not (
                    operation == "stop" and state == "blocked"):
                terminal = ("refused", f"task changed to {state}")
            else:
                terminal = None
                if admission_held and operation in ("resume", "handoff") and not task.get("resume_claim"):
                    return {"pending": True, "request": request, "held": admission_held}
                if status == "pending":
                    request.update({"status": "executing", "started_at": S.now()})
                    task["daemon_request"] = request
                    S.save_task(project, task)
    if terminal:
        return _finish_task_operation(project, slug, request_id, *terminal)

    try:
        if operation == "handoff":
            result = handoff(project, slug, request)
        elif operation == "resume":
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
            from . import reviews
            reviews.cancel_attached(project, slug, "Owner rejected")
            result = T.reject(
                project, slug, request["reason"], actor=request["actor"],
                expected_state=request.get("expected_state"), expected_agent_id=request.get("agent_id"),
                expected_session_id=request.get("session_id"), expected_daemon_request=request_id)
    except ResumeFailure as exc:
        _finish_task_operation(project, slug, request_id, "failed", str(exc))
        raise
    except (T.TransitionError, git_policy.GitPolicyError) as exc:
        return _finish_task_operation(project, slug, request_id, "refused", str(exc))
    except Exception as exc:
        _finish_task_operation(project, slug, request_id, "failed", str(exc))
        raise
    return _finish_task_operation(project, slug, request_id, "done", str(result)[:300])


@contextmanager
def publication_settlement(project: str):
    """Serialize project fetch, deployment and explicit checkout preservation."""
    path = config.project_dir(project) / ".publication-settlement.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def preserve_checkout(project: str, slug: str, request: dict) -> str:
    """Issue #247: explicitly preserve a dirty main for an unlaunched recovery owner."""
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
    worktree = repo / config.WORKTREE_ROOT / slug

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

    task = S.load_task(project, slug)
    _validate_task_worktree(repo, project, slug, worktree, restore_branch=True,
                           require_clean=not bool(task.get("attempt") and task.get("worktree") == str(worktree)))
    return worktree


def _validate_task_worktree(repo: Path, project: str, slug: str, worktree: Path,
                            *, require_clean: bool, restore_branch: bool = False) -> None:
    """Validate an already-created L2 checkout before either a fresh launch or a resume.

    With `restore_branch`, a clean checkout left on another branch returns to its task branch first.
    """
    import subprocess

    expected_path = (repo / config.WORKTREE_ROOT / slug).resolve()
    if worktree.resolve() != expected_path or not worktree.is_dir():
        raise T.TransitionError(f"task worktree for {project}/{slug} must be {expected_path}, got {worktree}")
    expected_branch = f"worktree-{slug}"
    actual = _git_branch(worktree)
    if actual and actual != expected_branch and restore_branch:
        _restore_task_branch(project, slug, worktree, actual, expected_branch)
        actual = _git_branch(worktree)
    if actual != expected_branch:
        raise T.TransitionError(
            f"task worktree {worktree} is on {actual or 'detached HEAD'}, expected {expected_branch!r}"
            + ("" if actual else f"; in the worktree, branch any commits to keep, then `git switch {expected_branch}` and resume")
        )
    if config.RELEASE is not None or config.SOURCE != config.REPO:
        # #348: a worktree-specific custom hook selection must not evade project guard verification.
        git_policy.require_hooks_installed(worktree)
    task = S.read_json(S.status_path(project, slug), {}) or {}
    if adopted_head := (task.get("adopted_pr") or {}).get("head"):
        ancestry = subprocess.run(
            ["git", "-C", str(worktree), "merge-base", "--is-ancestor", adopted_head, "HEAD"],
            capture_output=True, text=True, timeout=60,
        )
        if ancestry.returncode != 0:
            raise T.TransitionError("adopted PR head is not an ancestor of HEAD; preserve its history")
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


def _restore_task_branch(project: str, slug: str, worktree: Path, actual: str, expected: str) -> None:
    """#524: switch a clean checkout back to its task branch; the branch it leaves keeps its commits."""
    import subprocess

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=60)

    where = f"task worktree {worktree} is on {actual}, expected {expected!r}"
    if git("show-ref", "--verify", "--quiet", f"refs/heads/{expected}").returncode != 0:
        raise T.TransitionError(f"{where}, and that branch is missing; in the worktree, recreate {expected} "
                                "at the task's latest commit and switch to it, then resume")
    dirty = git("status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0 or (dirty.stdout or "").strip():
        raise T.TransitionError(f"{where}, with uncommitted changes; in the worktree, commit or discard them on "
                                f"{actual}, then `git switch {expected}` and resume")
    # Ignored files are outside `status`, so refuse to overwrite one the task branch tracks.
    switched = git("switch", "--no-overwrite-ignore", expected)
    if switched.returncode != 0:
        raise T.TransitionError(f"{where}; switching failed: {(switched.stderr or switched.stdout).strip()[:200]}")
    S.append_event(project, slug, "task-branch-restored", left=actual, branch=expected)


def build_brief(project: str, slug: str) -> str:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    request = (d / "request.md").read_text()
    policy = proj.get("approval", "default")
    if task.get("hold_merge"):  # a recorded hold is the explicit exception to merge-by-default
        merge_policy = (f"**Held for operator review** ({task['hold_merge']}): open a reviewed, green PR; after the "
                        "operator approves it in this task's chat, merge with `alt land --merge --approval "
                        "<message-id>`.")
    else:
        merge_policy = {"default": "Merge when the applicable checks and any appropriate review are complete.",
                        "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when the review is addressed and CI is green."}.get(policy, policy)
    engine = task.get("l2_engine") or task.get("engine") or "pending quota route"
    other_leases = leases(project, exclude=slug)
    overlaps = [f"`{other['slug']}` on {', '.join(shared)}" for other in other_leases
                if (shared := shared_paths(task_paths(project, task), other["paths"]))]
    progress = d / "progress.md"
    if task.get("attempt") and progress.exists():
        request += (f"\n\n---\n\nAttempt {task['attempt']} stopped before finishing. Its worktree and branch are "
                    "kept; its `progress.md` follows.\n\n" + progress.read_text().rstrip() + "\n")
    worktree = config.project_path(project) / config.WORKTREE_ROOT / slug
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        engine=engine,
        model=task.get("engine_model") or task.get("model") or "provider default",
        leases=("; ".join(f"`{l['slug']}` on {', '.join(l['paths']) or '(undeclared paths)'}" for l in other_leases) or "none"),
        overlaps=(f" Shared with {'; '.join(overlaps)}: rebase onto main before landing and keep shared-doc "
                  "edits to your own sections." if overlaps else ""),
        paths=", ".join(task_paths(project, task)) or "(not declared — stay inside the request's scope)",
        task_dir=d, merge_policy=merge_policy,
        repo=config.project_path(project),
        branch=worktree_branch(slug, worktree), request=request)
    if questions := T.open_questions(task):
        text += "\n\n**Open questions from the earlier session:**" + questions + "\n"
    return text


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-attempt settings: edit telemetry, the inbox hook that hands the operator's queued messages to the
    worker after a tool call or when it is about to stop, and native permission for the task's own `alt task run`.

    That one narrow allow rule decides before the engine's own action classifier, so a command the operator granted
    is not judged again; altd refuses `alt task run` unless the task holds a current grant, so the rule adds nothing
    without one and a grant or revocation takes effect in the running session."""
    hooks = config.HOOKS
    inbox = [{"type": "command", "command": shlex.join([sys.executable, "-B", str(hooks / "inbox.py")]), "timeout": 10}]
    settings = {"hooks": {
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": shlex.join([sys.executable, "-B", str(hooks / "edit_count.py")]), "timeout": 10}]},
                        {"hooks": inbox}],
        "Stop": [{"hooks": inbox}],
    }, "permissions": {"allow": [f"Bash(alt task run {slug} *)"]},
        "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key},
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW}
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


def run(project: str, slug: str, model: str | None = None) -> dict:
    with config.provider_admission() as held, launch_lock(), config.restart_lock() as ready:
        if held:
            raise T.TransitionError(held)
        if not ready or config.restart_in_progress():
            raise T.TransitionError("Altitude is restarting; retry shortly")
        return _run(project, slug, model)


def _run(project: str, slug: str, model: str | None = None) -> dict:
    # New work starts at the fetched commit; deployment HEAD, index and working files are not launch inputs.
    task = T.release_dependency(project, slug)
    if task.get("planned_wait"):
        raise T.TransitionError(f"waits for {task['planned_wait']['reason']}")
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "queued":
            raise T.TransitionError(f"{slug} is {task['state']}, not queued")
        T._require_daemon_fence(task, slug)
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
    try:
        repo = config.project_path(project)
        with publication_settlement(project):
            if config.RELEASE is not None or config.SOURCE != config.REPO:
                project_setup.ensure_guards(project, slug=slug)
            origin_sha = git_policy.fetch_origin(repo, "main")
    except project_setup.SetupError as exc:
        project_setup.block_task(project, slug, exc)
        raise T.TransitionError(f"dispatch awaits project setup: {exc}") from exc
    except git_policy.GitPolicyError as exc:
        # system_fault may acquire state locks, so it deliberately lives outside project_lock.
        from . import incidents
        incidents.system_fault("task-git-provenance", f"{project}/{slug}: {exc}", project=project, task=slug)
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
        choice = route.pick_task(proj, task)
        if not choice.get("engine"):
            raise T.TransitionError(f"engine hold: {choice['why']}")
        engine = choice["engine"]
        selected_model = choice.get("model")
        selected_effort = choice.get("effort")
        messages = T.pending(project, slug)
        for row in messages:
            task.setdefault("message_deliveries", {})[row["id"]] = {"state": "unconfirmed", "at": None}
        task.update({"dispatching": S.now(), "worker_started_at": datetime.now(timezone.utc).isoformat(),
                     "l2_engine": engine, "engine_model": selected_model,
                     "launch_model": selected_model, "launch_effort": selected_effort, "engine_reasoning_effort": None,
                     "routing": choice["why"], "routing_pinned": choice.get("pinned", False)})
        S.save_task(project, task)
    attempt = task.get("attempt", 0) + 1
    agent = {}
    try:
        settings = session_settings(project, slug, S.session_key(project, slug, attempt))
        tried = []
        while True:
            brief_md = build_brief(project, slug)
            history = [row for row in task.get("image_messages", []) if row.get("delivered")] if task.get("attempt") else []
            if history:
                brief_md += "\n\nPreviously delivered image context; these are saved messages, not new requests:\n" + T.render_inbox(history)
            if messages:
                brief_md += "\n\nMessages received before launch; retain the original brief's source authority:\n" + T.render_inbox(messages)
            T.brief(project, slug, brief_md, actor="altd")
            with S.project_lock(project):
                refs = (task.get("images") or []) + [image for row in history + messages for image in row.get("images", [])]
                attached = images.resolve(project, list({ref["id"]: ref for ref in refs}.values()), task=slug)
            res = engines.start_l2(
                engine, worker_name(project, slug, attempt), brief_md, cwd=worktree_path, persona=config.PERSONAS / "l2.md",
                model=selected_model, effort=selected_effort, settings=settings, extra_env=l2_env(project, slug, attempt),
                job_root=l2_job_root(project, slug), images=attached)
            rejection = res.get("rejection")
            if not rejection:
                break
            route.note_rejection(choice, rejection)
            if attached or not res.get("safe_to_retry"):
                break
            tried.append(route.option_key(choice))
            choice = route.pick_task(proj, task, excluded=tried)
            if not choice.get("engine"):
                with S.project_lock(project):
                    current = S.load_task(project, slug)
                    current["dispatching"] = None
                    S.save_task(project, current)
                raise T.TransitionError(f"engine hold: {choice['why']}")
            engine, selected_model = choice["engine"], choice["model"]
            selected_effort = choice.get("effort")
            with S.project_lock(project):
                current = S.load_task(project, slug)
                current.update(l2_engine=engine, launch_model=selected_model, engine_model=selected_model,
                               launch_effort=selected_effort, routing=choice["why"])
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
                   l2_engine=engine, engine_model=agent.get("engine_model", selected_model), routing=choice["why"],
                   messages=messages, input_delivered=agent.get("input_delivered") is True)
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
    claim = S.load_task(project, slug).get("resume_claim") or {}
    if claim and _claim_owner_live(claim):
        return {"already_resuming": True}
    with config.provider_admission() as held, launch_lock(), config.restart_lock() as ready:
        if held and not S.load_task(project, slug).get("resume_claim"):
            return {"held": held}
        if not ready or config.restart_in_progress():
            return {"held": "Altitude is restarting; retry shortly"}
        try:
            return _resume(project, slug, daemon_request_id=daemon_request_id,
                           **({"admission_held": held} if held else {}))
        except T.TransitionError as exc:
            raise ResumeFailure(str(exc)) from exc  # lifecycle cancellation must not become workflow:resume


def _resume(project: str, slug: str, *, daemon_request_id: str | None = None,
            admission_held: str | None = None) -> dict:
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
    if admission_held:
        return {"held": admission_held}  # Reconcile the old claim without claiming or launching new work.
    task = S.load_task(project, slug)
    if task.get("stop_id") and not task.get("resume_after") and daemon_request_id is None:
        return {"waiting": True}
    if task.get("waiting_on") and not task.get("fault") and not task.get("resume_after") and daemon_request_id is None:
        return {"waiting": True}  # I-20260908-045037: an old inbox/wake is not an answer to a new question.
    if not task.get("agent_id") or not task.get("session_id"):
        T.requeue(project, slug, expected_daemon_request=daemon_request_id,
                  expected_block_id=task.get("block_id"), **daemon_fence)
        return {"requeued": True}
    window = engines.window_hold(l2_engine(task))
    hold = resume_engine_hold(task) or wip_hold(project, task)
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
        if config.RELEASE is not None or config.SOURCE != config.REPO:
            project_setup.ensure_guards(project, slug=slug)
    except project_setup.SetupBusy as exc:
        T.release_resume_claim(project, slug, claim["id"], consume_request=False)
        T.mark_resume_held(project, slug, str(exc), expected_daemon_request=daemon_request_id,
                           expected_block_id=task.get("block_id"), **daemon_fence)
        return {"held": str(exc)}
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
    try:
        # A resume continues owned work, including edits, without needing a fresh remote base. Branch
        # restoration waits for the stopped worker so the checkout never changes underneath it.
        _validate_task_worktree(repo, project, slug, cwd, require_clean=False, restore_branch=True)
    except (git_policy.GitPolicyError, T.TransitionError, subprocess.SubprocessError, OSError) as exc:
        raise record_resume_failure(project, slug, claim["id"], exc, kind="task-git-provenance") from exc
    except Exception as exc:  # noqa: BLE001 — no post-claim infrastructure fault may strand the durable fence
        raise record_resume_failure(project, slug, claim["id"], exc) from exc
    rows = claim["messages"]
    prompt = T.render_inbox(rows) or "Continue from your progress file."
    worker = {}
    try:
        with S.project_lock(project):
            attached = images.resolve(project, [image for row in rows for image in row.get("images") or []], task=slug)
        T.update_resume_claim(project, slug, claim["id"], phase="launching")
        with S.project_lock(project):
            current = S.load_task(project, slug)
            current["worker_started_at"] = datetime.now(timezone.utc).isoformat()
            current["engine_reasoning_effort"] = None
            T._save_claim_task(project, current)
        res = engines.resume_l2(
            engine, worker_name(project, slug, task["attempt"]), task["session_id"], prompt, cwd=cwd,
            persona=config.PERSONAS / "l2.md", model=task.get("launch_model", task.get("engine_model")),
            effort=task.get("launch_effort"),
            settings=session_settings(project, slug, S.session_key(project, slug, task["attempt"])),
            extra_env=l2_env(project, slug, task["attempt"]), job_root=job_root, images=attached)
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
                 expected_daemon_request=daemon_request_id, input_delivered=worker.get("input_delivered") is True,
                 **daemon_fence)
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


def stop(project: str, slug: str, *, by: str = config.OPERATOR_ACTOR, reason: str | None = None,
         daemon_request_id: str | None = None, expected_agent_id: object = T._UNSET,
         expected_session_id: object = T._UNSET) -> dict:
    """End this worker; an explicit continuation releases its held inbox into the saved session."""
    reason = str(reason or f"stopped by {by}").strip()
    task = S.load_task(project, slug)
    active_request = task.get("daemon_request") or {}
    if active_request.get("status") in ("pending", "executing") and (
            active_request.get("id") != daemon_request_id or active_request.get("operation") != "stop"):
        raise T.TransitionError(f"{slug}: daemon request {active_request.get('id')} owns this task")
    if task["state"] == "running":
        # Block first, so the poll does not read the exiting worker as a death.
        task = T.block(project, slug, reason, actor=by, expected_state=task["state"],
                updates={"stop_id": task.get("stop_id") or daemon_request_id or uuid.uuid4().hex},
                expected_agent_id=expected_agent_id, expected_session_id=expected_session_id,
                expected_daemon_request=daemon_request_id)
    elif task["state"] != "blocked":
        raise T.TransitionError(f"{slug} is {task['state']}; nothing to stop")
    elif not task.get("stop_id"):
        with S.project_lock(project):
            task = S.load_task(project, slug)
            T._require_daemon_fence(task, slug, expected_daemon_request=daemon_request_id,
                                    expected_agent_id=expected_agent_id, expected_session_id=expected_session_id)
            if task["state"] != "blocked":
                raise T.TransitionError("The task changed before Stop; refresh its status.")
            T._ensure_question(project, task)
            T._supersede_resume(task)
            task.update(stop_id=daemon_request_id or uuid.uuid4().hex, blocked_reason=reason, block_actor=by)
            S.save_task(project, task)
    from . import reviews
    reviews.cancel_attached(project, slug, "Owner stopped")
    if task.get("agent_id"):
        note = engines.stop_l2_worker(l2_engine(task), task["agent_id"], job_root=l2_job_root(project, slug))
    else:
        note = "No worker was attached."
    S.append_event(project, slug, "stopped", agent_id=task.get("agent_id"), stop_id=task.get("stop_id"),
                   by=by, reason=reason, note=str(note or "")[:200])
    return S.load_task(project, slug)


def resume_engine_hold(task: dict) -> str | None:
    """Check the saved session's engine/model without changing its routing."""
    engine = l2_engine(task)
    if window := engines.window_hold(engine):
        return f"usage limit: subscription window exhausted, resets {window}"
    hold = route.resume_hold(engine, task.get("launch_model", task.get("engine_model")))
    return f"engine hold: {hold}" if hold else None


def resume_ready(project: str, task: dict) -> bool:
    """A saved session has an authorized, due wake and an eligible provider."""
    if task["state"] != "blocked" or not task.get("agent_id") or not task.get("session_id"):
        return False
    request = task.get("daemon_request") or {}
    if request.get("status") in ("pending", "executing"):
        if request.get("operation") != "resume" or any(
                request.get(key) != task.get(key) for key in ("block_id", "agent_id", "session_id")):
            return False
    elif task.get("resume_claim"):
        return False  # Recovery owns stale claims; an active launch holds launch_lock.
    elif not task.get("resume_after"):
        if (task.get("stop_id") or task.get("waiting_on") or task.get("fault") or task.get("resume_failed")
                or not any(row.get("wake", True) for row in T.pending(project, task["slug"]))):
            return False
    if (task.get("resume_after") or "") > S.now() or resume_engine_hold(task):
        return False
    with project_setup.operation_lock(project) as ready:
        return ready


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
        if t.get("stop_id") and not after:
            continue
        inbox_due = (not after and not t.get("waiting_on") and not t.get("fault") and not t.get("resume_failed")
                     and any(row.get("wake", True) for row in T.pending(project, t["slug"])))
        if (not after and not inbox_due) or after > now:
            continue
        if not t.get("agent_id") or not t.get("session_id"):
            due.append(t["slug"])
            continue
        if not resume_ready(project, t) or wip_hold(project, t):
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
    """Shared planned paths for briefs and status; not a permission boundary."""
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
    """The task's advisory planned files, for coordination and inspection."""
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


@contextmanager
def launch_lock(*, wait: bool = True):
    """Serialize machine capacity admission through worker binding, across threads and processes."""
    with open(config.ROOT / ".launch.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def wip_hold(project: str, task: dict | None = None) -> str | None:
    from . import reviews
    if task and occupies_slot(task):
        return None
    tasks = [(p, t) for p in config.load_projects() for t in S.list_tasks(p)]
    total = sum(occupies_slot(t) for _, t in tasks) + reviews.active_count()
    if total >= config.machine_wip():
        return f"WIP limit: {total} running on this machine"
    if task and task["state"] == "queued" and any(resume_ready(p, t) for p, t in tasks):
        return "WIP limit: ready resumes take the next machine slot"
    return None


def occupies_slot(task: dict) -> bool:
    """A launched resume awaiting crash-recovery binding already has a worker on the machine."""
    worker = (task.get("resume_claim") or {}).get("worker") or {}
    return task["state"] == "running" or bool(worker.get("id") and worker.get("sessionId"))


def poll(project: str) -> list[dict]:
    """Return L2 turns that exited, using each task's persisted engine adapter."""
    from . import reviews, usage
    reviews.poll(project)
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
        # #308 continuation: a previous delivery's report cannot explain the current worker's exit.
        started = max(started or "", (t.get("delivery") or {}).get("at", ""), t.get("report_after") or "")
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
BACKEND_ACTIVATION_DIRS = ("altitude/", "bin/", "systemd/", "hooks/", "personas/", "schemas/", "templates/", "scripts/")
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
    if platform.containerized():
        return []  # A project merge cannot activate code in the immutable application image.
    # Activation: a finishing worker must not clear requested_at or change build inputs during activation.
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            return ["deferred self-deploy: activation in progress"]
        return _self_deploy_fast_forward(project, slug)


def _self_deploy_fast_forward(project: str, slug: str | None = None) -> list[str]:
    """Fast-forward a project's deployment to its origin branch: main, or `config.SOURCE_BRANCH` for the checkout
    Altitude runs from. Backend, launch inputs and tracked web build inputs need activation: announce those with
    an FYI and `monitor/restart-pending.json`, never restart from here.

    The one implementation the deployment tick and `pull_after_done` share. Returns notes, empty when the
    project does not deploy from its checkout or the checkout is already at its origin branch. Anything that is not a
    pure fast-forward — dirty, on another branch, ahead of origin, or diverged — raises a deployment failure."""
    proj = config.project(project)
    if not proj.get("self_deploy", project == "altitude"):
        return []
    repo = config.project_path(project)
    branch = config.SOURCE_BRANCH if repo.resolve() == config.REPO.resolve() else "main"
    origin_sha = git_policy.fetch_origin(repo, branch)
    state = git_policy.service_preflight(repo, branch)   # clean, on the branch, neither ahead nor diverged
    if not state.behind:
        return []
    head = state.head
    pull = subprocess.run(["git", "merge", "-q", "--ff-only", f"origin/{branch}"], cwd=str(repo), capture_output=True, text=True, timeout=120)
    if pull.returncode != 0:
        raise git_policy.GitPolicyError(
            f"fast-forward failed: {(pull.stderr or pull.stdout).strip()[:300] or f'exit {pull.returncode}'}"
        )
    files = subprocess.run(["git", "diff", "--name-only", head, origin_sha], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout.split()
    changed = [f for f in files if activation_component(f)]
    notes = [f"self-deploy: {branch} {head[:7]} → {origin_sha[:7]} ({len(files)} files)"]
    if changed and config.RELEASE is None:
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
    except git_policy.FetchError as e:
        return [f"self-deploy fetch failed; the tick retries: {str(e)[:160]}"]
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
        from . import incidents
        incidents.system_fault("self-deploy", f"{project}: {e}", project=project)
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
