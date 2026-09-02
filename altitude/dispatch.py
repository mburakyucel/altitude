"""Dispatch one task-owning L2 in its worktree, monitor it, and safely resume its session."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone
import re
from pathlib import Path

from . import config, engines, git_policy, github_intake, recovery, route, state as S, tasks as T


class DispatchFailure(T.TransitionError):
    """A launch fault already persisted and routed through the global recovery fuse."""


def _active_task(project: str, slug: str) -> dict:
    """Never follow the archive fallback while claiming or mutating physical ownership."""
    task = S.read_json(S.tasks_dir(project) / slug / "status.json")
    if not task:
        raise T.TransitionError(f"{slug}: active task no longer exists")
    return task


def _task_owner_update(project: str, slug: str, update) -> dict:
    """Route a B3 task projection update through the Task authority seam."""
    def decide(task):
        update(task)
        return task, None, None
    task, _ = T.owner_command(project, slug, decide,
                              lambda value: (_owner(value, project=project, required=True)
                                             if value.get("active_operation") is not None else None))
    return task


def record_dispatch_failure(project: str, slug: str, error: object) -> DispatchFailure:
    """Leave a failed launch queued, clear its transient claim, and trip recovery once."""
    reason = str(error)[:300]
    def clear_dispatching(task):
        if task.get("state") == "queued":
            task["dispatching"] = None
    _task_owner_update(project, slug, clear_dispatching)
    S.append_event(project, slug, "dispatch-failed", reason=reason)
    from . import incidents
    incidents.system_fault("dispatch-failed", f"{project}/{slug}: {reason}", project=project, task=slug)
    return DispatchFailure(f"dispatch failed: {reason}")


def record_resume_failure(project: str, slug: str, previous: str, error: object) -> RuntimeError:
    """Persist a failed replacement launch and hold further ordinary work for recovery."""
    reason = str(error)[:300]
    S.append_event(project, slug, "resume-failed", previous=previous, reason=reason)
    from . import incidents
    incidents.system_fault("l2-resume", f"{project}/{slug}: {reason}", project=project, task=slug)
    return RuntimeError(f"resume of {project}/{slug} failed: {reason}")


def project_never_list(repo: Path) -> str:
    """Best effort: the 'Never' bullets from the repo's CLAUDE.md, else a generic line."""
    md = repo / "CLAUDE.md"
    if md.exists():
        lines = [l.strip("- ").strip() for l in md.read_text().splitlines() if re.match(r"^\s*-\s*\*\*?never", l, re.I) or "never" in l.lower()[:40]]
        if lines:
            return "; ".join(l[:160] for l in lines[:8])
    return "no changes outside the brief; no weakened guardrails; honor any recorded merge hold"


def l2_engine(task: dict) -> str:
    """Old task records are inspectable legacy evidence, never launch authority."""
    return task.get("l2_engine") or "claude"


def provider_capability_hold(task: dict) -> str | None:
    """Return the closed provider hold without changing task or recovery state."""
    return T.owner_provider_capability_hold(task)


# Read-only legacy Claude evidence. Codex ownership never reads or writes this provider-private tree.
JOBS_DIR = config.HOME / ".claude" / "jobs"


_OWNER_KEYS = {"kind", "request", "preparation", "physical", "result", "event",
               "cancelled_before_effect", "stop", "successor", "recovery_epoch"}
_REQUEST_KEYS = {"message_id", "prompt", "prompt_sha256", "model", "routing", "issue_digest"}
_PREPARATION_KEYS = {"stage", "intent", "receipt", "prior_unit"}
_PREPARATION_INTENT_KEYS = {"repository", "worktree", "branch", "base_ref"}
_PREPARATION_RECEIPT_KEYS = {"repository", "worktree", "branch", "base_ref", "base_sha"}
_RESULT_KEYS = {"provider_session_id", "action", "usage", "error"}
_OWNER_HISTORY_KEYS = {"generation", "transition_id", "process_unit_id", "message_id",
                       "intent_digest", "event_id", "event_sha256", "result_id", "result_sha256",
                       "session_id"}
_OWNER_ERROR_CAP = 500
OWNER_GENERATION_HISTORY_CAP = 128


def _bounded_owner_error(error: object) -> str:
    value = str(error).strip() or "unknown Codex owner failure"
    encoded, suffix = value.encode("utf-8", "replace"), b" [truncated]"
    if len(encoded) <= _OWNER_ERROR_CAP:
        return value
    return (encoded[:_OWNER_ERROR_CAP - len(suffix)].decode("utf-8", "ignore").rstrip()
            + suffix.decode())


def _owner(task: dict, *, project: str | None = None, required: bool = False) -> dict | None:
    """Validate the sole TaskRecord-owned Codex generation; never consult legacy job rows."""
    op = task.get("active_operation")
    if op is None:
        if required:
            raise T.TransitionError(f"{task.get('slug')}: exact Codex owner operation required")
        return None
    if not isinstance(op, dict) or set(op) != _OWNER_KEYS or op.get("kind") != "codex_owner":
        raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner operation")
    request, prep = op.get("request"), op.get("preparation")
    if (not isinstance(request, dict) or set(request) != _REQUEST_KEYS
            or not isinstance(request.get("message_id"), str) or not request["message_id"]
            or not isinstance(request.get("prompt"), str) or not request["prompt"]
            or request.get("prompt_sha256") != hashlib.sha256(request["prompt"].encode()).hexdigest()
            or request.get("model") is not None and not isinstance(request["model"], str)
            or not isinstance(request.get("routing"), dict)
            or request.get("issue_digest") is not None and not isinstance(request["issue_digest"], str)):
        raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner request")
    intent, prep_receipt = (prep or {}).get("intent"), (prep or {}).get("receipt")
    if (not isinstance(prep, dict) or set(prep) != _PREPARATION_KEYS
            or prep.get("stage") not in ("planned", "applying", "prepared", "complete")
            or prep.get("prior_unit") is not None and not isinstance(prep["prior_unit"], str)
            or not isinstance(intent, dict) or set(intent) != _PREPARATION_INTENT_KEYS
            or not all(isinstance(intent.get(key), str) and intent[key]
                       for key in _PREPARATION_INTENT_KEYS)
            or intent.get("base_ref") != "origin/main"
            or prep["stage"] in ("planned", "applying") and prep_receipt is not None
            or prep["stage"] in ("prepared", "complete") and (not isinstance(prep_receipt, dict)
                or set(prep_receipt) != _PREPARATION_RECEIPT_KEYS
                or not all(isinstance(prep_receipt.get(key), str) and prep_receipt[key]
                           for key in _PREPARATION_RECEIPT_KEYS)
                or any(prep_receipt[key] != intent[key]
                       for key in _PREPARATION_INTENT_KEYS))):
        raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner preparation")
    expected_intent = _preparation_intent(project or intent["repository"], str(task.get("slug") or ""))
    if intent != expected_intent:
        raise T.TransitionError(f"{task.get('slug')}: Codex owner preparation path changed")
    physical = op.get("physical")
    if (isinstance(op.get("recovery_epoch"), bool) or not isinstance(op.get("recovery_epoch"), int)
            or op["recovery_epoch"] < 0):
        raise T.TransitionError(f"{task.get('slug')}: malformed owner recovery epoch")
    if prep["stage"] != "complete":
        if physical is not None:
            raise T.TransitionError(f"{task.get('slug')}: unbound owner preparation has physical authority")
    else:
        try:
            physical = engines.validate_physical_transition(physical)
        except engines.PhysicalTransitionError as exc:
            raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner transition: {exc}") from exc
        expected_project = project or str((physical or {}).get("subject_id", "")).partition("/")[0]
        identity = {
            "project": expected_project, "slug": task.get("slug"),
            "dispatch_id": task.get("dispatch_id"), "owner_generation": task.get("owner_generation"),
            "message_id": request["message_id"], "worktree": prep_receipt["worktree"],
            "branch": prep_receipt["branch"], "base_sha": prep_receipt["base_sha"], "model": request["model"],
            "routing": request["routing"], "prompt_sha256": request["prompt_sha256"],
            "capability_sha256": hashlib.sha256(str(task.get("l2_token") or "").encode()).hexdigest(),
        }
        transition_id = hashlib.sha256(b"codex-owner\0" + S._canonical_json(identity)).hexdigest()  # noqa: SLF001
        if (physical["subject_kind"] != "owner" or physical["provider"] != "codex"
                or physical["subject_id"] != f"{expected_project}/{task.get('slug')}"
                or physical["transition_id"] != transition_id or physical["generation"] != transition_id
                or physical["message_id"] != request["message_id"]
                or prep_receipt["worktree"] != task.get("worktree")
                or prep_receipt["branch"] != task.get("branch")
                or prep_receipt["base_sha"] != task.get("base_sha")):
            raise T.TransitionError(f"{task.get('slug')}: Codex owner context changed")
        provider_request = physical["provider_session_request"]
        bound = physical["receipts"].get("bound") or {}
        if (provider_request["kind"] == "resume" and bound.get("bound") is not True
                and provider_request["session_id"] != task.get("session_id")):
            raise T.TransitionError(f"{task.get('slug')}: Codex resume thread changed")
        if bound.get("bound") is True and (bound["provider_session_id"] != task.get("session_id")
                or bound["physical_worker_id"] != task.get("agent_id")):
            raise T.TransitionError(f"{task.get('slug')}: Codex bound ownership projection changed")
    result = op.get("result")
    if result is not None and (not isinstance(result, dict) or set(result) != _RESULT_KEYS
            or result.get("provider_session_id") is not None
                and (not isinstance(result["provider_session_id"], str)
                     or not result["provider_session_id"]
                     or len(result["provider_session_id"].encode()) > engines.CODEX_PROVIDER_SESSION_ID_CAP)
            or result.get("action") is not None and not isinstance(result["action"], dict)
            or not isinstance(result.get("usage"), dict)
            or result.get("error") is not None and (not isinstance(result["error"], str)
                or not result["error"] or len(result["error"].encode("utf-8", "replace")) > _OWNER_ERROR_CAP)):
        raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner result")
    cancelled = op.get("cancelled_before_effect")
    if cancelled is not None:
        stop = op.get("stop")
        error = (result or {}).get("error")
        if (not isinstance(cancelled, dict)
                or set(cancelled) != {"status", "message_id", "result_id", "sha256", "empty"}
                or cancelled.get("status") != "cancelled-before-effect"
                or cancelled.get("message_id") != request["message_id"]
                or cancelled.get("result_id") != "transition:error"
                or cancelled.get("empty") is not True
                or not isinstance(cancelled.get("sha256"), str)
                or not isinstance(stop, dict) or set(stop) != {"reason", "requested", "cancel"}
                or stop.get("cancel") is not True
                or not isinstance(stop.get("reason"), str) or not stop["reason"]
                or not isinstance(stop.get("requested"), str) or not stop["requested"]
                or prep["stage"] != "planned" or physical is not None
                or result != {"provider_session_id": None, "action": None,
                              "usage": {}, "error": error}
                or error != _bounded_owner_error(
                    f"owner cancelled before effect for message {request['message_id']}: {stop['reason']}")
                or cancelled["sha256"] != hashlib.sha256(error.encode()).hexdigest()):
            raise T.TransitionError(f"{task.get('slug')}: malformed cancelled-before-effect receipt")
    elif result is not None and physical is None:
        raise T.TransitionError(f"{task.get('slug')}: unprepared owner has a result")
    event = op.get("event")
    event_required = bool(physical and "result_observed" in physical["receipts"])
    if (event_required and (not isinstance(event, dict) or set(event) != {"event_id", "sha256"}
            or event.get("event_id") != f"l2-engine/{physical['generation']}/events.jsonl"
            or not isinstance(event.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", event["sha256"]) is None)
            or not event_required and event is not None):
        raise T.TransitionError(f"{task.get('slug')}: malformed Codex owner event receipt")
    for field in ("stop", "successor"):
        value = op.get(field)
        if value is not None and not isinstance(value, dict):
            raise T.TransitionError(f"{task.get('slug')}: malformed owner {field} intent")
    if op.get("successor") is not None:
        successor = op["successor"]
        if set(successor) != _REQUEST_KEYS:
            raise T.TransitionError(f"{task.get('slug')}: malformed owner successor")
        _owner({**task, "active_operation": {"kind": "codex_owner", "request": successor,
            "preparation": {"stage": "planned", "intent": intent, "receipt": None,
                            "prior_unit": None},
            "physical": None, "result": None, "event": None, "cancelled_before_effect": None,
            "stop": None, "successor": None,
            "recovery_epoch": op["recovery_epoch"]}}, project=project, required=True)
    history = task.get("owner_generations") or []
    if not isinstance(history, list) or len(history) > OWNER_GENERATION_HISTORY_CAP:
        raise T.TransitionError(f"{task.get('slug')}: malformed owner generation history")
    seen_generations = set()
    for row in history:
        if (not isinstance(row, dict) or set(row) != _OWNER_HISTORY_KEYS
                or not all(isinstance(row.get(key), str) and row[key]
                           for key in _OWNER_HISTORY_KEYS - {"session_id"})
                or not re.fullmatch(r"[0-9a-f]{64}", row["generation"])
                or row["transition_id"] != row["generation"]
                or row["process_unit_id"] != engines.deterministic_process_unit(
                    "owner", f"{project}/{task.get('slug')}", row["generation"])
                or len(row["message_id"].encode("utf-8", "replace")) >
                    engines.CODEX_PROVIDER_SESSION_ID_CAP
                or row.get("session_id") is not None and (
                    not isinstance(row["session_id"], str) or not row["session_id"]
                    or len(row["session_id"].encode("utf-8", "replace")) >
                        engines.CODEX_PROVIDER_SESSION_ID_CAP)
                or row["event_id"] != f"l2-engine/{row['generation']}/events.jsonl"
                or row["result_id"] not in (
                    "transition:error", f"l2-engine/{row['generation']}/result.json")
                or not re.fullmatch(r"[0-9a-f]{64}", row["intent_digest"])
                or not re.fullmatch(r"[0-9a-f]{64}", row["event_sha256"])
                or not re.fullmatch(r"[0-9a-f]{64}", row["result_sha256"])):
            raise T.TransitionError(f"{task.get('slug')}: malformed owner generation history")
        if row["generation"] in seen_generations:
            raise T.TransitionError(f"{task.get('slug')}: duplicate owner generation history")
        seen_generations.add(row["generation"])
    return {**op, "physical": physical}


def _request(message_id: str, prompt: str, model: str | None, routing: dict,
             issue_digest: str | None = None) -> dict:
    return {"message_id": message_id, "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "model": model,
            "routing": routing, "issue_digest": issue_digest}


def _preparation_intent(project: str, slug: str) -> dict:
    repository = config.project_path(project)
    worktree = _expected_task_worktree(repository, slug)
    return {"repository": str(repository),
            "worktree": str(worktree),
            "branch": f"worktree-{slug}", "base_ref": "origin/main"}


def _operation(project: str, slug: str, request: dict, prior_unit: str | None = None,
               recovery_epoch: int = 0) -> dict:
    return {"kind": "codex_owner", "request": request,
            "preparation": {"stage": "planned", "intent": _preparation_intent(project, slug),
                            "receipt": None, "prior_unit": prior_unit},
            "physical": None, "result": None, "event": None, "cancelled_before_effect": None,
            "stop": None, "successor": None,
            "recovery_epoch": recovery_epoch}


def _owner_terminal(op: dict | None) -> bool:
    return bool(op and (op.get("cancelled_before_effect") is not None
                or op.get("physical") and op["physical"]["stage"] in ("complete", "failed")))


def _retire_owner_generation(project: str, task: dict, op: dict) -> None:
    """Project one terminal generation before active_operation can be overwritten."""
    if not _owner_terminal(op):
        raise T.TransitionError(f"{task['slug']}: cannot retire a nonterminal owner generation")
    if op.get("cancelled_before_effect") is not None:
        raise T.TransitionError(f"{task['slug']}: cancelled-before-effect owner has no generation to retire")
    _terminal_owner_evidence(project, task, op)
    physical = op["physical"]
    result = physical["receipts"]["result_observed"]
    event = op["event"]
    row = {"generation": physical["generation"], "transition_id": physical["transition_id"],
           "process_unit_id": physical["process_unit_id"], "message_id": physical["message_id"],
           "intent_digest": physical["intent_digest"],
           "event_id": event["event_id"], "event_sha256": event["sha256"],
           "result_id": result["result_id"], "result_sha256": result["sha256"],
           "session_id": (op.get("result") or {}).get("provider_session_id")}
    history = task.setdefault("owner_generations", [])
    existing = next((entry for entry in history if entry.get("generation") == row["generation"]), None)
    if existing not in (None, row):
        raise T.TransitionError(f"{task['slug']}: owner generation history conflicts")
    if existing is None:
        if len(history) >= OWNER_GENERATION_HISTORY_CAP:
            raise T.TransitionError(
                f"{task['slug']}: owner generation history reached fixed cap "
                f"{OWNER_GENERATION_HISTORY_CAP}; PR 1C.5 owner-history compaction is required"
            )
        history.append(row)


def _save_owner(project: str, before: dict, replacement: dict, *, updates: dict | None = None) -> dict:
    """One TaskRecord CAS. Effects are forbidden while this short project lock is held."""
    def decide(task):
        current = _owner(task, project=project, required=True)
        if current != _owner(before, project=project, required=True):
            raise T.TransitionError(f"{before['slug']}: Codex owner changed concurrently")
        candidate = {**task, **(updates or {}), "active_operation": replacement}
        return candidate, None, None
    candidate, _ = T.owner_command(project, before["slug"], decide,
                                   lambda value: _owner(value, project=project, required=True))
    return candidate


def _recovery_observation(project: str, task: dict) -> dict:
    try:
        return recovery.observe_owner_state(project, task)
    except ValueError as exc:
        raise T.TransitionError(str(exc)) from exc


class OwnerRecoveryFenceError(T.TransitionError):
    """An unchanged prepared owner request could not bind its persisted recovery fence."""


def _owner_invalidation_error(project: str, task: dict, op: dict) -> str | None:
    """Return one immutable error for stop/recovery invalidation of this exact generation."""
    physical = op.get("physical")
    if physical is None:
        return None
    stop = op.get("stop")
    if stop is not None:
        if physical.get("error") is not None:
            return physical["error"]
        reason = str(stop.get("reason") or "stop requested")
        return _bounded_owner_error(
            f"owner cancelled for transition {physical['transition_id']}: {reason}")
    if not recovery.owner_state_is_current(project, task, physical, op["recovery_epoch"]):
        if physical.get("error") is not None:
            return physical["error"]
        episode = physical.get("recovery_episode_id") or "inactive"
        permit = physical.get("recovery_permit_revision") or "inactive"
        return _bounded_owner_error(
            f"owner recovery fence changed for transition {physical['transition_id']} "
            f"(epoch {op['recovery_epoch']}, episode {episode}, permit {permit})"
        )
    return None


def _new_physical(project: str, task: dict, op: dict, prep: dict) -> dict:
    generation = int(task.get("owner_generation") or 0)
    receipt = prep["receipt"]
    identity = {"project": project, "slug": task["slug"], "dispatch_id": task["dispatch_id"],
                "owner_generation": generation, "message_id": op["request"]["message_id"],
                "worktree": receipt["worktree"], "branch": receipt["branch"],
                "base_sha": receipt["base_sha"],
                "model": op["request"]["model"], "routing": op["request"]["routing"],
                "prompt_sha256": op["request"]["prompt_sha256"],
                "capability_sha256": hashlib.sha256(task["l2_token"].encode()).hexdigest()}
    transition_id = hashlib.sha256(b"codex-owner\0" + S._canonical_json(identity)).hexdigest()  # noqa: SLF001
    try:
        recovery_state = _recovery_observation(project, task)
    except T.TransitionError as exc:
        raise OwnerRecoveryFenceError(str(exc)) from exc
    if op["recovery_epoch"] != recovery_state["epoch"]:
        raise OwnerRecoveryFenceError(
            f"{task['slug']}: recovery epoch changed before owner preparation"
        )
    request = ({"kind": "resume", "session_id": task["session_id"]}
               if task.get("session_id") else {"kind": "fresh"})
    return engines.new_physical_transition(
        transition_id=transition_id, subject_kind="owner", subject_id=f"{project}/{task['slug']}",
        generation=transition_id, provider="codex", provider_session_request=request,
        message_id=op["request"]["message_id"],
        recovery_episode_id=recovery_state["episode_id"],
        recovery_permit_revision=recovery_state["permit_revision"])


def owner_projection(project: str, task: dict) -> dict | None:
    """Pure public/liveness projection. It never repairs TaskRecord state."""
    op = _owner(task, project=project)
    if op is None:
        return None
    if op.get("cancelled_before_effect") is not None:
        return {"provider": "codex", "stage": "cancelled-before-effect",
                "process_unit_id": None, "empty": True, "working": False,
                "terminal": True, "session_id": task.get("session_id")}
    physical = op.get("physical")
    observation = engines.observe_managed_unit(physical["process_unit_id"]) if physical else None
    return {"provider": "codex", "stage": physical["stage"] if physical else op["preparation"]["stage"],
            "process_unit_id": physical["process_unit_id"] if physical else None,
            "empty": observation.get("empty") if observation else True,
            "working": bool(observation and not observation.get("empty")),
            "terminal": _owner_terminal(op), "session_id": task.get("session_id")}


def _owner_paths(project: str, slug: str, physical: dict) -> dict[str, Path]:
    generation = physical["generation"]
    if not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise T.TransitionError(f"{slug}: invalid owner runtime generation")
    project_dir, tasks = config.project_dir(project), S.tasks_dir(project)
    task_dir, family = tasks / slug, tasks / slug / "l2-engine"
    root = family / generation
    chain = (project_dir, tasks, task_dir, family, root)
    if (any(path.is_symlink() or path.exists() and not path.is_dir() for path in chain)
            or tasks.resolve().parent != project_dir.resolve()
            or task_dir.resolve().parent != tasks.resolve()
            or family.resolve().parent != task_dir.resolve()
            or root.resolve().parent != family.resolve()):
        raise T.TransitionError(f"{slug}: owner runtime path escapes its generation")
    paths = {"root": root, "events": root / "events.jsonl", "answer": root / "answer.json",
             "result": root / "result.json"}
    for path in paths.values():
        if path == root:
            continue
        if path.is_symlink():
            raise T.TransitionError(f"{slug}: owner runtime evidence is not a private regular file")
        if not path.exists():
            continue
        mode = path.lstat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1:
            raise T.TransitionError(f"{slug}: owner runtime evidence is not a private regular file")
        if path.resolve().parent != root.resolve():
            raise T.TransitionError(f"{slug}: owner runtime evidence escapes its generation")
    return paths


def _owner_evidence_bytes(path: Path, cap: int, label: str) -> bytes:
    """Read one exact private evidence file; absence and replacement are never empty evidence."""
    try:
        mode = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1:
            raise T.TransitionError(f"{label} is not a private regular file")
        if mode.st_size > cap:
            raise T.TransitionError(f"{label} exceeds {cap} bytes")
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise T.TransitionError(f"{label} is unavailable") from exc
    except OSError as exc:
        raise T.TransitionError(f"{label} is unavailable: {exc}") from exc
    if len(data) > cap:
        raise T.TransitionError(f"{label} exceeds {cap} bytes")
    return data


def _owner_event_evidence(project: str, task: dict, identity: dict,
                          event_id: str, event_sha256: str, *, label: str) -> Path:
    """Validate one event spool against its bound identity and raw-byte digest."""
    expected_id = f"l2-engine/{identity['generation']}/events.jsonl"
    if event_id != expected_id:
        raise T.TransitionError(f"{task['slug']}: {label} identity changed")
    path = _owner_paths(project, task["slug"], identity)["events"]
    data = _owner_evidence_bytes(path, engines.CODEX_EVENT_CAP, label)
    if hashlib.sha256(data).hexdigest() != event_sha256:
        raise T.TransitionError(f"{task['slug']}: {label} changed")
    try:
        first = data.splitlines()[0]
        header = S._strict_json_loads(first.decode("utf-8"))  # noqa: SLF001
    except (IndexError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise T.TransitionError(f"{task['slug']}: {label} header is invalid") from exc
    expected_header = {"type": "altitude.owner.spool", "version": 1,
        "transition_id": identity["transition_id"], "generation": identity["generation"],
        "process_unit_id": identity["process_unit_id"], "message_id": identity["message_id"],
        "intent_digest": identity["intent_digest"],
        "answer_id": f"l2-engine/{identity['generation']}/answer.json"}
    if header != expected_header:
        raise T.TransitionError(f"{task['slug']}: {label} header changed")
    return path


def _bind_owner_event(project: str, task: dict, op: dict) -> dict:
    physical = op["physical"]
    path = _owner_paths(project, task["slug"], physical)["events"]
    data = _owner_evidence_bytes(path, engines.CODEX_EVENT_CAP, "Codex owner event spool")
    event = {"event_id": f"l2-engine/{physical['generation']}/events.jsonl",
             "sha256": hashlib.sha256(data).hexdigest()}
    _owner_event_evidence(project, task, physical, event["event_id"], event["sha256"],
                          label="Codex owner event spool")
    return event


def _retired_owner_event_path(project: str, task: dict, row: dict) -> Path:
    """Validate both immutable retired spools before exposing either to transcript readers."""
    paths = _owner_paths(project, task["slug"], {"generation": row["generation"]})
    _owner_event_evidence(project, task, row, row["event_id"], row["event_sha256"],
                          label="retired Codex owner event spool")
    result_data = _owner_evidence_bytes(paths["result"], 65536, "retired Codex owner result")
    try:
        marker = S._strict_json_loads(result_data.decode("utf-8"))  # noqa: SLF001
        result_sha256 = hashlib.sha256(S._canonical_json(marker)).hexdigest()  # noqa: SLF001
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise T.TransitionError(f"{task['slug']}: retired owner result is invalid") from exc
    if (not isinstance(marker, dict)
            or set(marker) != {"version", "transition_id", "process_unit_id", "intent_digest", "result"}
            or marker.get("version") != 1
            or marker.get("transition_id") != row["transition_id"]
            or marker.get("process_unit_id") != row["process_unit_id"]
            or marker.get("intent_digest") != row["intent_digest"]
            or not isinstance(marker.get("result"), dict)
            or set(marker["result"]) != _RESULT_KEYS):
        raise T.TransitionError(f"{task['slug']}: retired owner result changed")
    if row["result_id"] == "transition:error":
        error = marker["result"].get("error")
        if (marker["result"] != {"provider_session_id": None, "action": None,
                "usage": {}, "error": error}
                or not isinstance(error, str) or not error
                or len(error.encode("utf-8", "replace")) > _OWNER_ERROR_CAP
                or hashlib.sha256(error.encode()).hexdigest() != row["result_sha256"]):
            raise T.TransitionError(f"{task['slug']}: retired owner error result changed")
    elif result_sha256 != row["result_sha256"]:
        raise T.TransitionError(f"{task['slug']}: retired owner result changed")
    return paths["events"]


def _owner_spool_header(physical: dict) -> dict:
    return {"type": "altitude.owner.spool", "version": 1,
            "transition_id": physical["transition_id"], "generation": physical["generation"],
            "process_unit_id": physical["process_unit_id"], "message_id": physical["message_id"],
            "intent_digest": physical["intent_digest"],
            "answer_id": f"l2-engine/{physical['generation']}/answer.json"}


def owner_event_path(project: str, task: dict) -> Path | None:
    op = _owner(task, project=project)
    return _owner_paths(project, task["slug"], op["physical"])["events"] if op and op.get("physical") else None


def owner_generation_event_paths(project: str, task: dict) -> list[tuple[str, Path]]:
    """Enumerate only validated TaskRecord generation projections; never scan runtime files."""
    _owner(task, project=project)  # validates the compact history as well as the current generation
    values = [(row.get("session_id") or "", _retired_owner_event_path(project, task, row))
              for row in task.get("owner_generations") or []]
    current = _owner(task, project=project)
    if current and current.get("physical"):
        current_path = _owner_paths(project, task["slug"], current["physical"])["events"]
        if _owner_terminal(current):
            event = current["event"]
            current_path = _owner_event_evidence(
                project, task, current["physical"], event["event_id"], event["sha256"],
                label="terminal Codex owner event spool")
            values.append((task.get("session_id") or "", current_path))
        elif current_path.exists():
            values.append((task.get("session_id") or "", current_path))
    return values


def _sync_owner_transcript(project: str, slug: str) -> None:
    from . import transcript
    transcript.sync(project, slug)


def _owner_marker(project: str, task: dict, op: dict) -> tuple[dict | None, dict]:
    physical = op["physical"]
    path = _owner_paths(project, task["slug"], physical)["result"]
    if not path.exists():
        return None, {"present": False}
    try:
        marker = S._strict_json_loads(engines.read_bounded_codex_output(path, 65536, "Codex owner result"))  # noqa: SLF001
    except (RuntimeError, ValueError, RecursionError) as exc:
        raise T.TransitionError(f"{task['slug']}: invalid owner result marker: {exc}") from exc
    if (not isinstance(marker, dict) or set(marker) != {"version", "transition_id", "process_unit_id",
            "intent_digest", "result"} or marker.get("version") != 1
            or (marker.get("transition_id"), marker.get("process_unit_id"), marker.get("intent_digest")) !=
                (physical["transition_id"], physical["process_unit_id"], physical["intent_digest"])):
        raise T.TransitionError(f"{task['slug']}: owner result marker does not match its intent")
    result = marker.get("result")
    if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
        raise T.TransitionError(f"{task['slug']}: malformed owner result marker")
    probe = {**op, "result": result}
    _owner({**task, "active_operation": probe}, project=project, required=True)
    bound = (physical["receipts"].get("bound") or {})
    if (result.get("error") is None and bound.get("bound") is True
            and result.get("provider_session_id") != bound.get("provider_session_id")):
        raise T.TransitionError(f"{task['slug']}: owner result changed its bound provider thread")
    requested = physical["provider_session_request"]
    if (result.get("error") is None and requested["kind"] == "resume"
            and result.get("provider_session_id") != requested["session_id"]):
        raise T.TransitionError(f"{task['slug']}: owner result changed its resumed provider thread")
    if (physical.get("error") and result == {"provider_session_id": None, "action": None,
            "usage": {}, "error": physical["error"]}):
        return marker, {"present": True, "process_unit_id": physical["process_unit_id"],
                        "intent_digest": physical["intent_digest"], "result_id": "transition:error",
                        "sha256": hashlib.sha256(physical["error"].encode()).hexdigest()}
    return marker, {"present": True, "process_unit_id": physical["process_unit_id"],
                    "intent_digest": physical["intent_digest"],
                    "result_id": f"l2-engine/{physical['generation']}/result.json",
                    "sha256": hashlib.sha256(S._canonical_json(marker)).hexdigest()}  # noqa: SLF001


@contextmanager
def publication_settlement(project: str):
    """Fence provenance gates from the remote-merge/service-fast-forward interval."""
    path = config.project_dir(project) / ".publication-settlement.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
    """Return the exact Git branch when observable, otherwise the deterministic task branch."""
    derived = f"worktree-{slug}"
    return (_git_branch(worktree) or derived) if worktree and Path(worktree).exists() else derived


def _task_worktree(repo: Path, project: str, slug: str, origin_sha: str) -> Path:
    """Create or validate the L2 checkout without ever inheriting the deployment checkout's mutable HEAD."""
    import subprocess

    expected_branch = f"worktree-{slug}"
    worktree = _expected_task_worktree(repo, slug)

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

    _validate_task_worktree(repo, project, slug, worktree, origin_sha,
                            require_clean=True, allow_task_commits=False)
    return worktree


def _validate_task_worktree(repo: Path, project: str, slug: str, worktree: Path, origin_sha: str,
                            *, require_clean: bool, allow_task_commits: bool) -> None:
    """Validate an already-created L2 checkout before either a fresh launch or a resume."""
    import subprocess

    expected_path = _expected_task_worktree(repo, slug)
    if (worktree.absolute() != expected_path or worktree.is_symlink()
            or worktree.resolve() != expected_path or not worktree.is_dir()):
        raise T.TransitionError(f"task worktree for {project}/{slug} must be {expected_path}, got {worktree}")
    expected_branch = f"worktree-{slug}"
    actual = _git_branch(worktree)
    if actual != expected_branch:
        raise T.TransitionError(
            f"task worktree {worktree} is on {actual or 'detached HEAD'}, expected {expected_branch!r}"
        )
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True, text=True, timeout=60,
    )
    head_sha = (head.stdout or "").strip()
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", head_sha):
        raise T.TransitionError(f"cannot resolve existing task worktree HEAD for {project}/{slug}")
    ancestor = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", origin_sha, head_sha],
        capture_output=True, text=True, timeout=60,
    )
    if ancestor.returncode == 1:
        raise T.TransitionError(
            f"existing task worktree {worktree} does not descend from fetched origin/main {origin_sha}"
        )
    if ancestor.returncode != 0:
        detail = (ancestor.stderr or ancestor.stdout or "").strip()[:200]
        raise T.TransitionError(
            f"cannot prove fetched origin/main ancestry for {project}/{slug}"
            + (f": {detail}" if detail else "")
        )
    if not allow_task_commits and head_sha != origin_sha:
        raise T.TransitionError(
            f"initial task worktree {worktree} is not exactly fetched origin/main {origin_sha}"
        )
    task_ref = f"{project}/{slug}"
    missing = git_policy.commits_missing_task_trailer(
        worktree, "main", task_ref, origin_sha=origin_sha
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


def _expected_task_worktree(repo: Path, slug: str) -> Path:
    """Return one lexical canonical worktree path; symlinked parents/targets are never authority."""
    repository = repo.expanduser().absolute()
    claude, parent = repository / ".claude", repository / ".claude" / "worktrees"
    target = parent / slug
    if (repository.is_symlink() or claude.is_symlink() or parent.is_symlink() or target.is_symlink()
            or repository.resolve() != repository
            or claude.resolve().parent != repository
            or parent.resolve().parent != claude.resolve()
            or target.resolve().parent != parent.resolve()):
        raise T.TransitionError(f"task worktree path for {slug} escapes its registered repository")
    return target


def build_brief(project: str, slug: str, issue_snapshot: dict | None = None) -> str:
    task = _active_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    request = (d / "request.md").read_text()
    if issue_snapshot:
        request = request.rstrip() + "\n\n" + github_intake.render(issue_snapshot) + "\n"
    policy = proj.get("approval", "default")
    if task.get("hold_merge"):  # a recorded hold is the explicit exception to merge-by-default
        merge_policy = f"**Held for Burak** — open the PR, make it ready for any required review, get its checks green, and stop; Burak merges it himself. Why: {task['hold_merge']}"
    else:
        merge_policy = {"default": "Merge when the applicable checks and any appropriate review are complete. Only a brief marked *held* stops at the open PR.",
                        "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when the review is addressed and CI is green."}.get(policy, policy)
    engine = task.get("l2_engine") or task.get("engine") or "pending quota route"
    if engine == "codex":
        completion_contract = (
            "For code delivery, return the schema-valid `publish` action after testing; Altitude's trusted control "
            "plane commits, opens the PR, applies the persisted merge policy, and writes the verified report. For a "
            "research/proposal task with no repository changes, return `complete_no_code` with the durable result."
        )
        conversation_contract = (
            "Put a concise reply in the final action's `message`. If a decision is genuinely required, return "
            "`block` with the exact question; the same Codex thread is resumed with Burak's answer."
        )
        publication_contract = (
            "The Codex command sandbox can write only ordinary worktree files; Git metadata, Altitude state, and "
            "network access remain outside it. Return inert publication/helper intent through the final action "
            "schema—never run Git publication or Altitude mutation commands yourself."
        )
    else:
        completion_contract = (
            f"Code delivery writes a concise schema-valid `report.json` (`{config.SCHEMAS / 'report.json'}`) in "
            f"`{d}` so Altitude can verify it. A no-code task may use `alt task done` after sending its result; "
            "Altitude finalizes it only after this worker exits."
        )
        conversation_contract = (
            "Reply in plain language with `alt task reply \"<message>\"`. Ask directly only when the repository and "
            "brief cannot resolve the choice; checkpoint `progress.md`, send the question, then block."
        )
        publication_contract = (
            "Every code change uses the isolated branch and a PR. Land with `alt land --message \"<message>\"`; use "
            "`--merge` only when allowed. Read the live `hold_merge` value and never merge around it."
        )
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        engine=engine,
        model=task.get("engine_model") or task.get("model") or "provider default",
        leases=("; ".join(f"`{l['slug']}` on {', '.join(l['paths']) or '(undeclared paths)'}" for l in leases(project, exclude=slug)) or "none"),
        paths=", ".join(task_paths(project, task)) or "(not declared — stay inside the request's scope)",
        task_dir=d, merge_policy=merge_policy, never_list=project_never_list(config.project_path(project)),
        repo=config.project_path(project),
        branch=worktree_branch(slug, config.project_path(project) / ".claude" / "worktrees" / slug),
        completion_contract=completion_contract, conversation_contract=conversation_contract,
        publication_contract=publication_contract, request=request)
    return text


def _begin_owner_request(project: str, slug: str, request: dict, *, initial: dict | None = None) -> tuple[dict, str]:
    """Install one request or its durable successor. A different live request is never overwritten."""
    snapshot = _active_task(project, slug)
    snapshot_owner = _owner(snapshot, project=project)
    terminal_expected = None
    if _owner_terminal(snapshot_owner):
        _terminal_owner_evidence(project, snapshot, snapshot_owner)
        terminal_expected = snapshot_owner
    epoch = recovery.clearance_epoch()
    def decide(task):
        if task.get("state") not in ("queued", "running", "blocked"):
            raise T.TransitionError(f"{slug}: cannot install owner request in {task.get('state')}")
        current = _owner(task, project=project)
        if current and _owner_terminal(current) and current != terminal_expected:
            raise T.TransitionError(f"{slug}: terminal owner changed before successor installation")
        if current and _owner_terminal(current) and current["request"] == request:
            return task, "complete", None
        if current and not _owner_terminal(current):
            if current["request"] == request:
                return task, "recover", None
            if current.get("successor") not in (None, request):
                raise T.TransitionError(f"{slug}: a different owner successor is already pending")
            current["successor"] = request
            current["stop"] = current.get("stop") or {
                "reason": "superseded by persisted owner request", "requested": S.now()}
            task["active_operation"] = current
            return task, "stop", None
        prior = (current["physical"]["process_unit_id"] if current and current.get("physical")
                 else current["preparation"].get("prior_unit") if current else None)
        if current and current.get("physical"):
            _retire_owner_generation(project, task, current)
        task.update(initial or {})
        task["active_operation"] = _operation(project, slug, request, prior, epoch)
        return task, "prepare", ("owner-request-planned", {"message_id": request["message_id"]})
    task, disposition = T.owner_command(project, slug, decide,
                                        lambda value: _owner(value, project=project, required=True))
    if terminal_expected is not None and disposition == "prepare":
        _sync_owner_transcript(project, slug)
    return task, disposition


def _promote_successor(project: str, slug: str) -> dict:
    snapshot = _active_task(project, slug)
    expected = _owner(snapshot, project=project, required=True)
    _terminal_owner_evidence(project, snapshot, expected)
    epoch = recovery.clearance_epoch()
    def decide(task):
        current = _owner(task, project=project, required=True)
        if current != expected or not _owner_terminal(current) or current.get("successor") is None:
            raise T.TransitionError(f"{slug}: owner successor is not ready")
        prior = (current["physical"]["process_unit_id"] if current.get("physical")
                 else current["preparation"].get("prior_unit"))
        if current.get("physical"):
            _retire_owner_generation(project, task, current)
        task["active_operation"] = _operation(project, slug, current["successor"], prior, epoch)
        return task, None, None
    task, _ = T.owner_command(project, slug, decide,
                              lambda value: _owner(value, project=project, required=True))
    _sync_owner_transcript(project, slug)
    return task


def _claim_owner_preparation(project: str, task: dict) -> tuple[dict, bool]:
    """Elect the sole Git-effect claimant with one short planned→applying CAS."""
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] != "planned":
        return task, False
    claimed = {**op, "preparation": {**op["preparation"], "stage": "applying"}}
    try:
        return _save_owner(project, task, claimed), True
    except T.TransitionError:
        current = _active_task(project, task["slug"])
        _owner(current, project=project, required=True)
        return current, False


def _record_owner_prepared(project: str, task: dict, *, worktree: Path,
                           branch: str, base_sha: str) -> dict:
    """Persist the exact Git receipt after the elected claimant finishes its unlocked effect."""
    current = _active_task(project, task["slug"])
    op = _owner(current, project=project, required=True)
    expected = _owner(task, project=project, required=True)
    if (op["request"] != expected["request"]
            or op["preparation"]["intent"] != expected["preparation"]["intent"]
            or op["preparation"]["stage"] != "applying"):
        raise T.TransitionError(f"{task['slug']}: owner preparation claim changed after Git effect")
    intent = op["preparation"]["intent"]
    receipt = {**intent, "base_sha": base_sha}
    if (str(worktree), branch) != (intent["worktree"], intent["branch"]):
        raise T.TransitionError(f"{task['slug']}: Git preparation differs from persisted intent")
    prepared = {**op, "preparation": {"stage": "prepared", "intent": intent,
        "receipt": receipt, "prior_unit": op["preparation"]["prior_unit"]}}
    return _save_owner(project, current, prepared, updates={
        "worktree": str(worktree), "branch": branch, "base_sha": base_sha})


def _rebind_prepared_owner_epoch(project: str, task: dict) -> dict:
    """Rebind an unchanged prepared request only to the current inactive recovery epoch."""
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] != "prepared":
        return task
    try:
        observation = _recovery_observation(project, task)
    except T.TransitionError as exc:
        raise OwnerRecoveryFenceError(str(exc)) from exc
    if op["recovery_epoch"] == observation["epoch"]:
        return task
    if observation["state"] != "none":
        raise OwnerRecoveryFenceError(
            f"{task['slug']}: prepared owner cannot rebind during an active recovery episode"
        )
    replacement = {**op, "recovery_epoch": observation["epoch"]}
    try:
        return _save_owner(project, task, replacement)
    except T.TransitionError:
        current = _active_task(project, task["slug"])
        current_op = _owner(current, project=project, required=True)
        if (current_op["request"] == op["request"]
                and current_op["recovery_epoch"] == observation["epoch"]
                and current_op["preparation"]["stage"] in ("prepared", "complete")):
            return current
        raise


def _bind_prepared_owner(project: str, task: dict) -> dict:
    """Bind one prepared Git receipt to its deterministic physical intent; perform no effect."""
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] == "complete":
        return task
    if op["preparation"]["stage"] != "prepared":
        raise T.TransitionError(f"{task['slug']}: exact prepared Git receipt required before binding")
    task = _rebind_prepared_owner_epoch(project, task)
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] == "complete":
        return task
    if op["preparation"]["stage"] != "prepared":
        raise T.TransitionError(f"{task['slug']}: prepared owner changed before physical binding")
    generation = int(task.get("owner_generation") or 0) + 1
    candidate = {**task, "owner_generation": generation}
    physical = _new_physical(project, candidate, op, op["preparation"])
    if op.get("stop") is not None:
        reason = _bounded_owner_error(
            f"owner cancelled for transition {physical['transition_id']}: "
            f"{op['stop'].get('reason') or 'stop requested'}"
        )
        physical = engines.note_physical_transition_error(physical, reason)
    complete = {**op, "preparation": {**op["preparation"], "stage": "complete"},
                "physical": physical}
    try:
        return _save_owner(project, task, complete, updates={"owner_generation": generation})
    except T.TransitionError:
        current = _active_task(project, task["slug"])
        current_op = _owner(current, project=project, required=True)
        if (current_op["request"] == op["request"]
                and current_op["preparation"]["stage"] == "complete"
                and current_op["physical"] == physical):
            return current
        raise


def _prepare_owner(project: str, task: dict, *, worktree: Path, branch: str, base_sha: str) -> dict:
    """Record a claimed Git effect, then bind its inert physical intent in a second CAS."""
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] == "applying":
        task = _record_owner_prepared(
            project, task, worktree=worktree, branch=branch, base_sha=base_sha)
    return _bind_prepared_owner(project, task)


def _settle_owner_preparation(project: str, task: dict, *, existing_worktree: bool) -> dict:
    """Recover one applying Git preparation under the existing settlement lock.

    The TaskRecord CAS is always short. The settlement lock serializes the idempotent Git effect
    with its prepared-receipt CAS, so a replacement process can safely finish an abandoned
    ``applying`` operation and a concurrent observer re-reads ``prepared`` without repeating Git.
    """
    expected = _owner(task, project=project, required=True)
    if expected["preparation"]["stage"] == "planned":
        task, _winner = _claim_owner_preparation(project, task)
        expected = _owner(task, project=project, required=True)
    if _owner_terminal(expected) or expected["preparation"]["stage"] in ("prepared", "complete"):
        return task
    if expected["preparation"]["stage"] != "applying":
        raise T.TransitionError(f"{task['slug']}: unknown owner preparation stage")

    with publication_settlement(project):
        live = _active_task(project, task["slug"])
        op = _owner(live, project=project, required=True)
        if _owner_terminal(op) or op["preparation"]["stage"] in ("prepared", "complete"):
            return live
        if (op["preparation"]["stage"] != "applying"
                or op["request"] != expected["request"]
                or op["preparation"]["intent"] != expected["preparation"]["intent"]):
            raise T.TransitionError(f"{task['slug']}: applying Git intent changed before settlement")

        repo = config.project_path(project)
        intent = op["preparation"]["intent"]
        worktree = Path(intent["worktree"])
        origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
        if existing_worktree:
            _validate_task_worktree(repo, project, task["slug"], worktree,
                                    origin_sha, require_clean=False, allow_task_commits=True)
        else:
            worktree = _task_worktree(repo, project, task["slug"], origin_sha)

        # Cancellation and other task projections may race while Git is unlocked. Re-read and
        # preserve them, but accept only this exact request/intent at the applying boundary.
        for _ in range(4):
            live = _active_task(project, task["slug"])
            current = _owner(live, project=project, required=True)
            if current["preparation"]["stage"] in ("prepared", "complete"):
                return live
            if (current["preparation"]["stage"] != "applying"
                    or current["request"] != expected["request"]
                    or current["preparation"]["intent"] != intent):
                raise T.TransitionError(f"{task['slug']}: applying Git intent changed after effect")
            try:
                return _record_owner_prepared(
                    project, live, worktree=worktree, branch=intent["branch"], base_sha=origin_sha)
            except T.TransitionError as exc:
                if "changed concurrently" not in str(exc):
                    raise
        raise T.TransitionError(f"{task['slug']}: prepared receipt CAS did not settle")


def _step_owner(project: str, task: dict, stage: str, following: str, receipt: dict,
                *, result: dict | None = None, event: dict | None = None,
                updates: dict | None = None) -> dict:
    op = _owner(task, project=project, required=True)
    replacement = {**op, "physical": engines.advance_physical_transition(
        op["physical"], stage, following, receipt)}
    if result is not None:
        replacement["result"] = result
    if event is not None:
        replacement["event"] = event
    return _save_owner(project, task, replacement, updates=updates)


def _claim_owner_launch(project: str, task: dict) -> tuple[dict, bool]:
    """Only the CAS winner of planned→prior_stopped may cross the provider spawn boundary."""
    op = _owner(task, project=project, required=True)
    if op["physical"]["stage"] != "planned":
        return task, False
    prior = op["preparation"]["prior_unit"]
    if prior is None:
        receipt = {"previous_process_unit_id": None, "empty": True}
    else:
        observed = engines.observe_managed_unit(prior)
        if observed.get("empty") is not True:
            return task, False
        receipt = {"previous_process_unit_id": prior, "empty": True, "observation": observed}
    try:
        return _step_owner(project, task, "planned", "prior_stopped", receipt), True
    except T.TransitionError:
        current = _active_task(project, task["slug"])
        _owner(current, project=project, required=True)
        return current, False


def _write_owner_marker(project: str, task: dict, op: dict, result: dict) -> None:
    physical = op["physical"]
    paths = _owner_paths(project, task["slug"], physical)
    paths["root"].mkdir(parents=True, exist_ok=True)
    if not paths["events"].exists():
        S.atomic_write(paths["events"], S._canonical_json(  # noqa: SLF001
            _owner_spool_header(physical)).decode() + "\n")
    marker = {"version": 1, "transition_id": physical["transition_id"],
              "process_unit_id": physical["process_unit_id"],
              "intent_digest": physical["intent_digest"], "result": result}
    try:
        encoded = S._canonical_json(marker) + b"\n"  # noqa: SLF001
    except (TypeError, ValueError, RecursionError):
        encoded = b""
    if not encoded or len(encoded) > 65536:
        result = {"provider_session_id": None, "action": None, "usage": {},
                  "error": "Codex owner result exceeded 65536 bytes"}
        marker["result"] = result
        encoded = S._canonical_json(marker) + b"\n"  # noqa: SLF001
    S.atomic_write(paths["result"], encoded.decode())


def _parse_owner_spool(project: str, task: dict, op: dict) -> dict:
    paths, physical = _owner_paths(project, task["slug"], op["physical"]), op["physical"]
    events_text = engines.read_bounded_codex_output(paths["events"], engines.CODEX_EVENT_CAP,
                                                     "Codex owner event spool")
    answer_text = engines.read_bounded_codex_output(paths["answer"], engines.CODEX_ANSWER_CAP,
                                                     "Codex owner answer")
    if not events_text.endswith("\n"):
        raise T.TransitionError("Codex owner event spool has an incomplete tail")
    try:
        events = [S._strict_json_loads(line) for line in events_text.splitlines()]  # noqa: SLF001
        action = S._strict_json_loads(answer_text)  # noqa: SLF001
        for value in [*events, action]:
            S._canonical_json(value)  # noqa: SLF001
        from . import actions
        actions._validate_shape(action)  # noqa: SLF001
    except (ValueError, TypeError, RecursionError) as exc:
        raise T.TransitionError(f"invalid Codex owner spool: {exc}") from exc
    header = _owner_spool_header(physical)
    started = [(i, row) for i, row in enumerate(events[1:], 1) if row.get("type") == "thread.started"]
    terminals = [(i, row) for i, row in enumerate(events[1:], 1)
                 if row.get("type") in ("turn.completed", "turn.failed")]
    if (not events or events[0] != header or len(started) != 1 or len(terminals) != 1
            or terminals[0][1].get("type") != "turn.completed"
            or not started[0][0] < terminals[0][0] == len(events) - 1
            or not isinstance(started[0][1].get("thread_id"), str)
            or not started[0][1]["thread_id"]
            or len(started[0][1]["thread_id"].encode()) > engines.CODEX_PROVIDER_SESSION_ID_CAP
            or not isinstance(terminals[0][1].get("usage"), dict)):
        raise T.TransitionError("Codex owner spool has no single successful turn")
    return {"provider_session_id": started[0][1]["thread_id"], "action": action,
            "usage": terminals[0][1]["usage"], "error": None}


def _owner_thread(project: str, task: dict, op: dict) -> str | None:
    """Read only the bounded, unique thread-start prefix while the managed unit is live."""
    path = _owner_paths(project, task["slug"], op["physical"])["events"]
    if not path.exists():
        return None
    text = engines.read_bounded_codex_output(path, engines.CODEX_EVENT_CAP, "Codex owner event spool")
    rows = []
    for line in text.splitlines():
        try:
            row = S._strict_json_loads(line)  # noqa: SLF001
        except (ValueError, RecursionError):
            if line == text.splitlines()[-1] and not text.endswith("\n"):
                break
            raise T.TransitionError("Codex owner event spool contains corrupt evidence")
        if not isinstance(row, dict):
            raise T.TransitionError("Codex owner event spool row is not an object")
        rows.append(row)
    if not rows or rows[0] != _owner_spool_header(op["physical"]):
        raise T.TransitionError("Codex owner event spool header does not match its generation")
    starts = [row.get("thread_id") for row in rows[1:] if row.get("type") == "thread.started"]
    if len(starts) > 1:
        raise T.TransitionError("Codex owner event spool contains duplicate thread identity")
    if not starts:
        return None
    value = starts[0]
    if (not isinstance(value, str) or not value
            or len(value.encode()) > engines.CODEX_PROVIDER_SESSION_ID_CAP):
        raise T.TransitionError("Codex owner thread identity is invalid")
    return value


def _managed_owner(project: str, slug: str, transition_id: str) -> int:
    """Run one bounded Codex turn inside the predeclared outer managed unit."""
    task = _active_task(project, slug)
    op = _owner(task, project=project, required=True)
    physical = op["physical"]
    if physical["transition_id"] != transition_id or physical["stage"] not in ("prior_stopped", "spawned"):
        return 2
    paths = _owner_paths(project, slug, physical)
    paths["root"].mkdir(parents=True, exist_ok=True)
    header = _owner_spool_header(physical)
    if any(path.exists() for key, path in paths.items() if key != "root"):
        return 3
    S.atomic_write(paths["events"], S._canonical_json(header).decode() + "\n")  # noqa: SLF001
    S.atomic_write(paths["answer"], "")
    current = _active_task(project, slug)
    current_op = _owner(current, project=project, required=True)
    same = (current_op["request"] == op["request"] and current_op["preparation"] == op["preparation"]
            and all(current_op["physical"][key] == physical[key]
                    for key in ("transition_id", "generation", "process_unit_id", "message_id", "intent_digest")))
    invalidation = _owner_invalidation_error(project, current, current_op) if same else None
    if not same or invalidation is not None:
        _write_owner_marker(project, task, op, {"provider_session_id": None, "action": None,
                            "usage": {}, "error": invalidation or "owner changed before Codex launch"})
        return 4
    persona = (config.PERSONAS / "l2_codex.md").read_text()
    request = physical["provider_session_request"]
    result = engines.codex_exec(
        persona + "\n\n" + op["request"]["prompt"],
        cwd=Path(op["preparation"]["receipt"]["worktree"]),
        schema=config.SCHEMAS / "l2_action.json", sandbox="workspace-write",
        model=op["request"]["model"], timeout=config.L3_CODEX_TURN_TIMEOUT,
        extra_env=l2_env(project, task), resume=request.get("session_id"), contain=False,
        answer_path=paths["answer"], event_spool=paths["events"])
    try:
        if result.get("returncode") != 0:
            raise T.TransitionError(_bounded_owner_error(
                result.get("error") or "Codex owner process failed"))
        bounded = _parse_owner_spool(project, task, op)
        if request["kind"] == "resume" and bounded["provider_session_id"] != request["session_id"]:
            raise T.TransitionError("Codex owner resumed a different provider thread")
        current = _active_task(project, slug)
        current_op = _owner(current, project=project, required=True)
        same = (current_op["request"] == op["request"]
                and current_op["preparation"] == op["preparation"]
                and current_op["physical"]["intent_digest"] == physical["intent_digest"])
        if not same:
            raise T.TransitionError("owner changed before owner result")
        if invalidation := _owner_invalidation_error(project, current, current_op):
            raise T.TransitionError(invalidation)
    except T.TransitionError as exc:
        bounded = {"provider_session_id": None, "action": None, "usage": {},
                   "error": _bounded_owner_error(exc)}
    current = _active_task(project, slug)
    current_op = _owner(current, project=project, required=True)
    same = (current_op["request"] == op["request"] and current_op["preparation"] == op["preparation"]
            and current_op["physical"]["intent_digest"] == physical["intent_digest"])
    if not same:
        return 5
    if invalidation := _owner_invalidation_error(project, current, current_op):
        bounded = {"provider_session_id": None, "action": None, "usage": {},
                   "error": invalidation}
    _write_owner_marker(project, current, current_op, bounded)
    return 0 if bounded["error"] is None else 1


def _spawn_owner(project: str, task: dict) -> dict:
    task, winner = _claim_owner_launch(project, task)
    if not winner:
        return reconcile_owner(project, task["slug"])
    op = _owner(task, project=project, required=True)
    physical = op["physical"]
    child_env = engines.codex_env(l2_env(project, task)); child_env["PYTHONPATH"] = str(config.REPO)
    try:
        with recovery.owner_launch_permission(project, task, physical, op["recovery_epoch"]):
            current = _active_task(project, task["slug"])
            current_op = _owner(current, project=project, required=True)
            if current_op != op or current_op.get("stop") is not None:
                raise T.TransitionError("owner changed or was cancelled before provider launch")
            engines.spawn_managed_unit(
                physical, [sys.executable, "-m", "altitude.dispatch", "--managed-owner", project,
                           task["slug"], physical["transition_id"]], cwd=config.REPO,
                launcher_env=engines.codex_env(l2_env(project, task), retain_user_bus=True),
                child_env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
    except Exception as exc:
        replacement = {**op, "physical": engines.note_physical_transition_error(
            physical, _bounded_owner_error(exc))}
        return _save_owner(project, task, replacement)
    try:
        return _step_owner(project, task, "prior_stopped", "spawned",
                           {"process_unit_id": physical["process_unit_id"], "launched": True})
    except T.TransitionError:
        # A concurrent reconciler may have observed this exact unit and recorded the
        # same launch. It owns the later receipts; never stop a healthy elected unit.
        return reconcile_owner(project, task["slug"])


def reconcile_owner(project: str, slug: str) -> dict:
    """Forward-repair one physical stage per CAS loop. Reconciliation never launches."""
    for _ in range(12):
        task = _active_task(project, slug)
        op = _owner(task, project=project, required=True)
        if op["preparation"]["stage"] != "complete" or _owner_terminal(op):
            return task
        marker, durable = _owner_marker(project, task, op)
        physical, stage = op["physical"], op["physical"]["stage"]
        invalidation = _owner_invalidation_error(project, task, op)
        if invalidation is not None and stage not in ("complete", "failed"):
            if not engines.observe_managed_unit(physical["process_unit_id"])["empty"]:
                engines.stop_managed_unit(physical["process_unit_id"])
            changed = False
            if physical["error"] is None:
                task = _save_owner(project, task, {**op, "physical":
                    engines.note_physical_transition_error(
                        physical, invalidation)})
                op = _owner(task, project=project, required=True)
                physical = op["physical"]
                changed = True
            expected_result = {"provider_session_id": None, "action": None,
                               "usage": {}, "error": invalidation}
            if ("result_observed" not in physical["receipts"]
                    and (marker is None or marker.get("result") != expected_result)):
                _write_owner_marker(project, task, op, expected_result)
                changed = True
            if changed:
                continue
        try:
            decision = engines.reconcile_physical_transition(physical, durable)
        except engines.PhysicalTransitionError as exc:
            raise T.TransitionError(f"{slug}: ownership_uncertain: {exc}") from exc
        action, unit = decision["decision"], decision["process_unit"]
        if stage == "planned":
            return task
        if stage == "prior_stopped":
            if action not in ("record_spawned", "record_spawn_failure"):
                raise T.TransitionError(f"{slug}: unexpected launch reconciliation {action}")
            launched = action == "record_spawned"
            task = _step_owner(project, task, stage, "spawned", {
                "process_unit_id": physical["process_unit_id"], "launched": launched,
                **({} if launched else {"reason": physical["error"]})})
            continue
        if stage == "spawned":
            if marker is None and not unit["empty"]:
                bound = _owner_thread(project, task, op)
                if bound is None:
                    return task
                request = physical["provider_session_request"]
                if request["kind"] == "resume" and request["session_id"] != bound:
                    raise T.TransitionError(f"{slug}: Codex owner resumed a different thread")
                updates = {"session_id": bound, "agent_id": physical["process_unit_id"]}
                if task.get("state") == "queued":
                    updates.update({"state": "running", "dispatching": None})
                task = _step_owner(project, task, stage, "bound", {
                    "bound": True, "physical_worker_id": physical["process_unit_id"],
                    "provider_session_id": bound}, updates=updates)
                continue
            if marker is None:
                error = physical["error"] or "Codex owner exited without a bounded result"
                replacement = {**op, "physical": (physical if physical["error"] else
                    engines.note_physical_transition_error(physical, error))}
                task = _save_owner(project, task, replacement)
                op = _owner(task, project=project, required=True)
                _write_owner_marker(project, task, op, {"provider_session_id": None, "action": None,
                                    "usage": {}, "error": error})
                continue
            result = marker["result"]
            if result["error"] and physical["error"] is None:
                task = _save_owner(project, task, {**op, "physical":
                    engines.note_physical_transition_error(
                        physical, _bounded_owner_error(result["error"]))})
                continue
            bound = result.get("provider_session_id")
            receipt = ({"bound": True, "physical_worker_id": physical["process_unit_id"],
                        "provider_session_id": bound} if bound else
                       {"bound": False, "reason": physical["error"]})
            updates = ({"session_id": bound, "agent_id": physical["process_unit_id"],
                        **({"state": "running", "dispatching": None}
                           if task.get("state") == "queued" else {})} if bound else None)
            task = _step_owner(project, task, stage, "bound", receipt,
                               updates=updates)
            continue
        if stage == "bound":
            if marker is None:
                if not unit["empty"]:
                    return task
                error = physical["error"] or "Codex owner exited without a bounded result"
                task = _save_owner(project, task, {**op, "physical": (physical if physical["error"] else
                    engines.note_physical_transition_error(physical, error))})
                op = _owner(task, project=project, required=True)
                _write_owner_marker(project, task, op, {"provider_session_id": None, "action": None,
                                    "usage": {}, "error": error})
                continue
            event = _bind_owner_event(project, task, op)
            task = _step_owner(project, task, stage, "result_observed",
                {"result_id": durable["result_id"], "sha256": durable["sha256"]},
                result=marker["result"], event=event)
            continue
        if stage == "result_observed":
            if not unit["empty"]:
                return task
            task = _step_owner(project, task, stage, "empty", unit)
            continue
        if stage == "empty":
            terminal = "failed" if physical["error"] else "complete"
            task = _step_owner(project, task, stage, terminal, {"status": terminal})
            if terminal == "complete" and task.get("state") == "queued":
                # A completed turn may have exited before its bound receipt was projected; state still becomes owned.
                pass
            return task
    raise T.TransitionError(f"{slug}: owner reconciliation exceeded its closed stage count")


def stop_owner(project: str, slug: str, reason: str) -> dict:
    """Persist stop intent, perform idempotent stop, then reconcile exact terminal emptiness."""
    def decide(task):
        op = _owner(task, project=project, required=True)
        if _owner_terminal(op):
            return task, None, None
        if op.get("stop") is None:
            op["stop"] = {"reason": _bounded_owner_error(reason), "requested": S.now()}
            task["active_operation"] = op
        return task, None, None
    task, _ = T.owner_command(project, slug, decide,
                              lambda value: _owner(value, project=project, required=True))
    if _owner_terminal(_owner(task, project=project, required=True)):
        return task
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] == "planned":
        raise T.TransitionError(f"{slug}: planned owner must be cancelled before effect")
    if op["preparation"]["stage"] == "applying":
        # The elected Git claimant may still be outside the lock. The stop intent is durable,
        # but absence of its exact receipt is ambiguous and can never be archived as empty.
        return task
    if op["preparation"]["stage"] == "prepared":
        task = _bind_prepared_owner(project, task)
        op = _owner(task, project=project, required=True)
    physical = op["physical"]
    if physical["stage"] == "planned":
        invalidation = _owner_invalidation_error(project, task, op) or _bounded_owner_error(reason)
        task = _save_owner(project, task, {**op, "physical":
            engines.note_physical_transition_error(physical, invalidation)})
        task, _winner = _claim_owner_launch(project, task)
        op = _owner(task, project=project, required=True)
        physical = op["physical"]
    if physical["stage"] == "prior_stopped" and physical["error"] is None:
        raise T.TransitionError(f"{slug}: launch boundary is ambiguous; reconcile before replacement")
    if physical["stage"] not in ("complete", "failed") and physical["error"] is None:
        invalidation = _owner_invalidation_error(project, task, op) or _bounded_owner_error(reason)
        task = _save_owner(project, task, {**op, "physical":
            engines.note_physical_transition_error(physical, invalidation)})
        op = _owner(task, project=project, required=True)
        physical = op["physical"]
    unlaunched = (physical["stage"] == "prior_stopped" and physical["error"] is not None
                  or (physical["receipts"].get("spawned") or {}).get("launched") is False)
    if physical["stage"] not in ("complete", "failed") and not unlaunched:
        engines.stop_managed_unit(physical["process_unit_id"])
    task = reconcile_owner(project, slug)
    if not _owner_terminal(_owner(task, project=project, required=True)):
        task = reconcile_owner(project, slug)
    return task


def _cancel_planned_operation(op: dict, reason: str) -> dict:
    """Return the canonical terminal receipt for one still-planned request."""
    stop_reason = _bounded_owner_error(reason)
    error = _bounded_owner_error(
        f"owner cancelled before effect for message {op['request']['message_id']}: {stop_reason}")
    return {**op,
            "stop": {"reason": stop_reason, "requested": S.now(), "cancel": True},
            "result": {"provider_session_id": None, "action": None,
                       "usage": {}, "error": error},
            "cancelled_before_effect": {
                "status": "cancelled-before-effect", "message_id": op["request"]["message_id"],
                "result_id": "transition:error", "sha256": hashlib.sha256(error.encode()).hexdigest(),
                "empty": True}}


def _cancel_planned_before_effect(project: str, slug: str, reason: str) -> dict:
    """Close one still-planned request without Git, provider, or physical history."""
    def install(task):
        op = _owner(task, project=project, required=True)
        if op["preparation"]["stage"] != "planned" or op.get("physical") is not None:
            raise T.TransitionError(f"{slug}: owner crossed the pre-effect cancellation boundary")
        if op.get("cancelled_before_effect") is not None:
            return task, None, None
        task["active_operation"] = _cancel_planned_operation(op, reason)
        return task, None, None
    task, _ = T.owner_command(project, slug, install,
                              lambda value: _owner(value, project=project, required=True))
    return task


def cancel_owner(project: str, slug: str, reason: str) -> dict:
    """Install the rejection cancellation intent before the idempotent physical stop."""
    def install(task):
        current = _owner(task, project=project, required=True)
        if current.get("cancelled_before_effect") is not None:
            return task, None, None
        if current["preparation"]["stage"] == "planned":
            task["active_operation"] = _cancel_planned_operation(current, reason)
        elif not _owner_terminal(current):
            current["stop"] = {"reason": _bounded_owner_error(reason),
                               "requested": S.now(), "cancel": True}
            task["active_operation"] = current
        return task, None, None
    task, _ = T.owner_command(project, slug, install,
                              lambda value: _owner(value, project=project, required=True))
    op = _owner(task, project=project, required=True)
    if op.get("cancelled_before_effect") is not None:
        return task
    return stop_owner(project, slug, reason)


def owner_archive_ready(project: str, task: dict) -> bool:
    op = _owner(task, project=project)
    if op is not None and op.get("cancelled_before_effect") is not None:
        return op.get("successor") is None
    if (op is None or not _owner_terminal(op) or op.get("result") is None
            or op["physical"]["receipts"].get("empty", {}).get("empty") is not True):
        return False
    try:
        _terminal_owner_evidence(project, task, op)
    except T.TransitionError:
        return False
    return True


def _terminal_owner_evidence(project: str, task: dict, op: dict) -> tuple[dict, dict]:
    if not _owner_terminal(op):
        raise T.TransitionError(f"{task['slug']}: owner is not terminal")
    if op.get("cancelled_before_effect") is not None:
        receipt = op["cancelled_before_effect"]
        return {"result": op["result"]}, {"present": True,
            "result_id": receipt["result_id"], "sha256": receipt["sha256"]}
    marker, durable = _owner_marker(project, task, op)
    receipt = op["physical"]["receipts"]["result_observed"]
    observed = ({"result_id": durable.get("result_id"), "sha256": durable.get("sha256")}
                if durable.get("present") else None)
    if marker is None or observed != receipt or marker["result"] != op.get("result"):
        raise T.TransitionError(f"{task['slug']}: terminal owner result evidence changed")
    event = op["event"]
    _owner_event_evidence(project, task, op["physical"], event["event_id"], event["sha256"],
                          label="terminal Codex owner event spool")
    return marker, durable


def owner_terminal_snapshot(project: str, task: dict) -> dict:
    """Return one exact terminal-empty result, including a closed failure/cancellation."""
    task = reconcile_owner(project, task["slug"])
    op = _owner(task, project=project, required=True)
    cancelled = op.get("cancelled_before_effect")
    if (not _owner_terminal(op) or op.get("result") is None
            or cancelled is None
                and op["physical"]["receipts"].get("empty", {}).get("empty") is not True):
        raise T.TransitionError(f"{task['slug']}: exact terminal-empty owner result required")
    _terminal_owner_evidence(project, task, op)
    return {"task": task, "operation": op, "result": op["result"]}


def owner_result_snapshot(project: str, task: dict) -> dict:
    terminal = owner_terminal_snapshot(project, task)
    task, op = terminal["task"], terminal["operation"]
    if op.get("cancelled_before_effect") is not None or op["physical"]["stage"] != "complete":
        raise T.TransitionError(f"{task['slug']}: exact successful terminal-empty owner result required")
    if not recovery.owner_state_is_current(project, task, op["physical"], op["recovery_epoch"]):
        raise T.TransitionError(f"{task['slug']}: owner recovery fence changed")
    physical = op["physical"]
    return {"task": task, "result": op["result"], "transition_id": physical["transition_id"],
            "process_unit_id": physical["process_unit_id"], "intent_digest": physical["intent_digest"],
            "message_id": physical["message_id"], "generation": physical["generation"],
            "result_receipt": physical["receipts"]["result_observed"],
            "empty_receipt": physical["receipts"]["empty"],
            "recovery_episode_id": physical["recovery_episode_id"],
            "recovery_permit_revision": physical["recovery_permit_revision"],
            "recovery_epoch": op["recovery_epoch"]}


def require_owner_result(project: str, task: dict) -> dict:
    """Stable broker seam: return only the exact terminal-empty, recovery-current owner result."""
    snapshot = owner_result_snapshot(project, task)
    live, result = snapshot["task"], snapshot["result"]
    return {**snapshot, "agent": {"id": live.get("agent_id"), "sessionId": live.get("session_id"),
                                   "state": "done", "status": "exited", "engine": "codex",
                                   "action": result.get("action")},
            "worker_result": result.get("action"), "owner_result": {
                "version": 1, "provider": "codex", "project": project, "slug": live["slug"],
                "owner_generation": live.get("owner_generation"),
                "transition_id": snapshot["transition_id"],
                "generation": snapshot["generation"], "message_id": snapshot["message_id"],
                "process_unit_id": snapshot["process_unit_id"],
                "intent_digest": snapshot["intent_digest"],
                "result_receipt": snapshot["result_receipt"],
                "empty_receipt": snapshot["empty_receipt"],
                "recovery_episode_id": snapshot["recovery_episode_id"],
                "recovery_permit_revision": snapshot["recovery_permit_revision"],
                "recovery_epoch": snapshot["recovery_epoch"]}}


def require_owner_permit_current(project: str, task: dict) -> None:
    op = _owner(task, project=project, required=True)
    if not recovery.owner_state_is_current(project, task, op["physical"], op["recovery_epoch"]):
        raise T.TransitionError(f"{task['slug']}: owner recovery fence changed")


def _continue_prepared_owner(project: str, task: dict) -> dict:
    """Bind prepared intent, then either settle its stop or cross the provider launch boundary."""
    op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] == "prepared":
        task = _bind_prepared_owner(project, task)
        op = _owner(task, project=project, required=True)
    if op["preparation"]["stage"] != "complete":
        return task
    if op.get("stop") is not None:
        return stop_owner(project, task["slug"], op["stop"].get("reason") or "owner stopped")
    return (_spawn_owner(project, task) if op["physical"]["stage"] == "planned"
            else reconcile_owner(project, task["slug"]))


OWNER_CONTINUATION_TASK_CAP = 128
OWNER_CONTINUATION_STEP_CAP = 8
_BLOCKED_RESUME_FIELDS = ("resume_after", "resume_answer", "resume_prefix",
                          "resume_exact_prompt", "resume_message_id")
_BOUND_OWNER_IDENTITY_KEYS = {"message_id", "request_sha256", "transition_id", "generation",
                              "intent_digest", "process_unit_id", "provider_session_id"}
_RESUME_COMPLETION_KEYS = {"resume", "dispatch_id", "session_id", "agent_id", "owner"}


def _bound_owner_identity(op: dict) -> dict:
    """Return the non-secret exact identity of one positively bound owner generation."""
    physical = op.get("physical") or {}
    bound = physical.get("receipts", {}).get("bound") or {}
    if bound.get("bound") is not True:
        raise T.TransitionError("exact positive owner bound receipt required")
    request_sha = hashlib.sha256(S._canonical_json(op["request"])).hexdigest()  # noqa: SLF001
    return {"message_id": op["request"]["message_id"], "request_sha256": request_sha,
            "transition_id": physical["transition_id"], "generation": physical["generation"],
            "intent_digest": physical["intent_digest"],
            "process_unit_id": physical["process_unit_id"],
            "provider_session_id": bound["provider_session_id"]}


def _resume_completion_expectation(task: dict, owner: dict) -> dict:
    """Bind one persisted blocked input to the exact generation that satisfied its wait."""
    if (set(owner) != _BOUND_OWNER_IDENTITY_KEYS
            or any(key not in task for key in _BLOCKED_RESUME_FIELDS)):
        raise T.TransitionError(f"{task['slug']}: exact blocked resume expectation is unavailable")
    resume = {key: task[key] for key in _BLOCKED_RESUME_FIELDS}
    if (resume["resume_message_id"] != owner["message_id"]
            or task.get("session_id") != owner["provider_session_id"]):
        raise T.TransitionError(f"{task['slug']}: blocked resume expectation changed before binding")
    return {"resume": resume, "dispatch_id": task.get("dispatch_id"),
            "session_id": task.get("session_id"), "agent_id": owner["process_unit_id"],
            "owner": owner}


def _complete_blocked_resume(project: str, task: dict, expectation: dict | None = None) -> dict:
    """Atomically project one exact bound resume and consume only its matching retry intent."""
    op = _owner(task, project=project, required=True)
    owner = _bound_owner_identity(op)
    expectation = expectation or _resume_completion_expectation(task, owner)
    if (not isinstance(expectation, dict) or set(expectation) != _RESUME_COMPLETION_KEYS
            or not isinstance(expectation.get("resume"), dict)
            or set(expectation["resume"]) != set(_BLOCKED_RESUME_FIELDS)
            or not isinstance(expectation.get("owner"), dict)
            or set(expectation["owner"]) != _BOUND_OWNER_IDENTITY_KEYS):
        raise T.TransitionError(f"{task['slug']}: malformed blocked resume completion expectation")
    expected_resume = expectation["resume"]
    message_id, answer = expected_resume["resume_message_id"], expected_resume["resume_answer"]
    expected_identity = {key: expectation[key] for key in ("dispatch_id", "session_id", "agent_id")}
    if (task.get("state") not in ("blocked", "running")
            or not isinstance(message_id, str) or not message_id
            or not isinstance(answer, str) or expected_resume["resume_exact_prompt"] is not True
            or op.get("stop") is not None or owner != expectation["owner"]):
        raise T.TransitionError(f"{task['slug']}: exact bound blocked resume is required")

    def complete(live):
        current = _owner(live, project=project, required=True)
        for key, expected in expected_identity.items():
            if live.get(key) != expected:
                raise T.TransitionError(
                    f"{live['slug']}: blocked resume {key.removesuffix('_id')} changed before completion")
        if (_bound_owner_identity(current) != expectation["owner"]
                or current["request"]["message_id"] != message_id or current.get("stop") is not None):
            raise T.TransitionError(f"{live['slug']}: blocked resume owner changed before completion")
        if live.get("state") == "running":
            if any(key in live for key in _BLOCKED_RESUME_FIELDS):
                raise T.TransitionError(
                    f"{live['slug']}: running resume retains ambiguous retry intent")
            return live, False, None
        if live.get("state") != "blocked" or any(
                key not in live or live[key] != value for key, value in expected_resume.items()):
            raise T.TransitionError(f"{live['slug']}: blocked resume intent changed before completion")
        live.update({"state": "running", "dispatching": None, "blocked_reason": None})
        for key in _BLOCKED_RESUME_FIELDS:
            live.pop(key)
        return live, True, ("state", {"frm": "blocked", "to": "running", "by": "altd",
                                      "answer": answer})

    completed, changed = T.owner_command(
        project, task["slug"], complete,
        lambda value: _owner(value, project=project, required=True))
    if changed:
        S.regen_state_md(project)
    return completed


def _finish_background_resume(project: str, task: dict, op: dict) -> dict:
    """Project one exact durable blocked resume after its provider thread is bound."""
    message_id = task.get("resume_message_id")
    physical = op.get("physical")
    bound = (physical or {}).get("receipts", {}).get("bound") or {}
    if (task.get("state") != "blocked" or not isinstance(message_id, str)
            or op["request"]["message_id"] != message_id or bound.get("bound") is not True
            or op.get("stop") is not None or physical.get("stage") == "failed"):
        return task
    return _complete_blocked_resume(project, task)


def _settle_failed_background_resume(project: str, task: dict, op: dict) -> dict:
    """Consume one exact failed retry intent without projecting the blocked task to running."""
    if (task.get("state") != "blocked" or (op.get("physical") or {}).get("stage") != "failed"
            or any(key not in task for key in _BLOCKED_RESUME_FIELDS)
            or task.get("resume_message_id") != op["request"]["message_id"]):
        return task
    expected_resume = {key: task[key] for key in _BLOCKED_RESUME_FIELDS}
    expected_identity = {key: task.get(key) for key in ("dispatch_id", "session_id", "agent_id")}
    error = _bounded_owner_error((op.get("result") or {}).get("error")
                                 or op["physical"].get("error") or "owner resume failed")

    def settle(live):
        current = _owner(live, project=project, required=True)
        if (live.get("state") != "blocked" or current != op
                or (current.get("physical") or {}).get("stage") != "failed"
                or any(live.get(key) != value for key, value in expected_identity.items())
                or any(key not in live or live[key] != value
                       for key, value in expected_resume.items())):
            raise T.TransitionError(f"{live['slug']}: failed blocked resume changed before settlement")
        for key in _BLOCKED_RESUME_FIELDS:
            live.pop(key)
        live["blocked_reason"] = f"owner resume failed: {error}"
        return live, True, ("resume-failed", {
            "message_id": op["request"]["message_id"], "reason": error})

    settled, changed = T.owner_command(
        project, task["slug"], settle,
        lambda value: _owner(value, project=project, required=True))
    if changed:
        S.regen_state_md(project)
    return settled


def continue_owners(project: str) -> list[dict]:
    """Boundedly advance replay-safe Codex owner boundaries from the existing L2 worker.

    This is the sole background continuation routine. It adds no timer or authority: it uses
    TaskRecord CAS, the existing settlement/launch fences, and the non-launching physical
    reconciler. In particular, ``prior_stopped`` is observed by ``reconcile_owner`` only.
    """
    results = []
    candidates = [task for task in S.list_tasks(project)
                  if task.get("state") in ("queued", "running", "blocked")
                  and l2_engine(task) == "codex" and task.get("active_operation") is not None]
    for candidate in candidates[:OWNER_CONTINUATION_TASK_CAP]:
        slug = candidate["slug"]
        try:
            for _ in range(OWNER_CONTINUATION_STEP_CAP):
                task = _active_task(project, slug)
                if task.get("state") not in ("queued", "running", "blocked"):
                    break
                T.require_owner_provider_capability(task)
                op = _owner(task, project=project, required=True)
                physical = op.get("physical")
                if (op["preparation"]["stage"] == "complete" and physical is not None
                        and physical["stage"] != "planned" and not _owner_terminal(op)):
                    # A hold gates admission, not strict stop/reconciliation of a unit that already
                    # crossed launch. Settle only the existing generation; never launch here.
                    task = reconcile_owner(project, slug)
                    op = _owner(task, project=project, required=True)
                hold = wip_hold(project, task, existing_owner=True)
                if hold:
                    results.append({"slug": slug, "status": "held", "reason": hold})
                    break
                task = _settle_failed_background_resume(project, task, op)
                op = _owner(task, project=project, required=True)
                resumed = _finish_background_resume(project, task, op)
                if resumed != task:
                    task = resumed
                    op = _owner(task, project=project, required=True)
                before = op

                if _owner_terminal(op):
                    if op.get("successor") is None:
                        break
                    task = _promote_successor(project, slug)
                elif (op["preparation"]["stage"] == "planned"
                      and op.get("stop") is not None and op.get("successor") is not None):
                    task = _cancel_planned_before_effect(
                        project, slug, op["stop"].get("reason") or
                        "superseded by persisted owner request")
                elif op["preparation"]["stage"] in ("planned", "applying"):
                    existing = bool(task.get("session_id") or op["preparation"].get("prior_unit"))
                    task = _settle_owner_preparation(
                        project, task, existing_worktree=existing)
                    task = _continue_prepared_owner(project, task)
                elif op["preparation"]["stage"] in ("prepared", "complete"):
                    # `_continue_prepared_owner` launches only physical `planned`. Every later
                    # physical stage, including ambiguous `prior_stopped`, goes to reconciliation.
                    task = _continue_prepared_owner(project, task)
                else:  # schema validation should make this unreachable.
                    raise T.TransitionError(f"{slug}: unknown owner preparation stage")

                current = _owner(task, project=project, required=True)
                resumed = _finish_background_resume(project, task, current)
                if resumed != task:
                    task = resumed
                    current = _owner(task, project=project, required=True)
                if current == before:
                    break
            else:
                results.append({"slug": slug, "status": "bounded"})
                continue
            if not any(row.get("slug") == slug for row in results):
                results.append({"slug": slug, "status": "continued"})
        except (git_policy.GitPolicyError, recovery.LaunchHeld, T.TransitionError,
                engines.ManagedUnitError) as exc:
            results.append({"slug": slug, "status": "held", "reason": _bounded_owner_error(exc)})
    return results


def run(project: str, slug: str, model: str | None = None) -> dict:
    """Install a durable request before Git/provider effects, then start only as CAS launch winner."""
    with S.project_lock(project):
        task = _active_task(project, slug)
        if task["state"] != "queued":
            raise T.TransitionError(f"{slug} is {task['state']}, not queued")
        # Persisted provider ownership and explicit provider pins are checked
        # before intake, Git, worktree, or launch effects.
        T.require_owner_provider_capability(task)
        proj = config.project(project)
        forced_engine = task.get("engine") or proj.get("l2_engine")
        if model in config.MODEL_ALIASES and not forced_engine:
            forced_engine = "claude"
        if forced_engine in config.ENGINES:
            try:
                engines.require_autonomous_engine(forced_engine)
            except engines.EngineCapabilityError as exc:
                raise T.TransitionError(f"engine hold: {exc}") from exc
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
        if task.get("l2_engine") == "codex" and task.get("active_operation") is None:
            raise T.TransitionError(f"{slug}: unknown legacy Codex ownership cannot be relaunched")
        existing = _owner(task, project=project) if task.get("l2_engine") == "codex" else None
    if existing and not _owner_terminal(existing):
        preparation_stage = existing["preparation"]["stage"]
        if preparation_stage in ("planned", "applying"):
            request = existing["request"]
        elif preparation_stage in ("prepared", "complete"):
            started = _continue_prepared_owner(project, task)
            return _wait_owner_started(project, started)
    else:
        try:
            issue_snapshot = github_intake.ensure_snapshot(project, slug, expected_state="queued")
        except github_intake.IssueIntakeError as exc:
            raise T.TransitionError(f"GitHub issue intake held before launch: {exc}") from exc
        with S.project_lock(project):
            task = _active_task(project, slug)
            proj = config.project(project)
            forced = task.get("engine") or proj.get("l2_engine")
            choice = route.pick_engine("l2", forced=forced)
            if choice.get("engine") != "codex":
                raise T.TransitionError("Codex is the sole autonomous L2 provider")
            selected_model = model or task.get("model") or proj.get("l2_codex_model")
            attempt = int(task.get("attempt") or 0) + 1
            dispatch_id = f"{slug}-{attempt}"
        brief = build_brief(project, slug, issue_snapshot)
        message_id = hashlib.sha256(
            f"owner\0{project}\0{slug}\0{dispatch_id}\0initial".encode()).hexdigest()
        request = _request(message_id, brief, selected_model, choice,
                           (issue_snapshot or {}).get("content_sha256"))
        task, disposition = _begin_owner_request(project, slug, request, initial={
            "dispatching": S.now(), "dispatch_id": dispatch_id, "attempt": attempt,
            "l2_engine": "codex", "engine_model": selected_model, "routing": choice,
            "l2_token": secrets.token_urlsafe(24)})
        if disposition not in ("prepare", "recover"):
            raise T.TransitionError(f"{slug}: initial owner request unexpectedly requires stop")
        T.brief(project, slug, brief, actor="altd")

    try:
        task = _settle_owner_preparation(
            project, _active_task(project, slug), existing_worktree=False)
    except (git_policy.GitPolicyError, T.TransitionError) as exc:
        raise record_dispatch_failure(project, slug, exc) from exc
    op = _owner(task, project=project, required=True)
    if op["request"] != request:
        raise T.TransitionError(f"{slug}: owner request changed during Git preparation")
    task = _continue_prepared_owner(project, task)
    return _wait_owner_started(project, task)


def _wait_owner_started(project: str, task: dict, timeout: float = 15.0, *,
                        include_owner_identity: bool = False) -> dict:
    import time
    expected = _owner(task, project=project, required=True)
    expected_request = expected["request"]
    expected_physical = expected.get("physical") or {}
    expected_identity = tuple(expected_physical.get(key) for key in (
        "transition_id", "generation", "intent_digest"))
    if any(value is None for value in expected_identity):
        raise T.TransitionError(f"{task['slug']}: exact physical owner intent is required before waiting")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = reconcile_owner(project, task["slug"])
        op = _owner(task, project=project, required=True)
        physical = op.get("physical") or {}
        identity = tuple(physical.get(key) for key in (
            "transition_id", "generation", "intent_digest"))
        if op["request"] != expected_request or identity != expected_identity:
            raise T.TransitionError(f"{task['slug']}: physical owner changed before binding")
        bound = (op.get("physical") or {}).get("receipts", {}).get("bound") or {}
        if bound.get("bound") is True or _owner_terminal(op):
            break
        time.sleep(0.05)
    op = _owner(task, project=project, required=True)
    physical = op.get("physical") or {}
    identity = tuple(physical.get(key) for key in (
        "transition_id", "generation", "intent_digest"))
    if op["request"] != expected_request or identity != expected_identity:
        raise T.TransitionError(f"{task['slug']}: physical owner changed before binding")
    bound = (op.get("physical") or {}).get("receipts", {}).get("bound") or {}
    if bound.get("bound") is not True and not _owner_terminal(op):
        raise T.TransitionError(f"{task['slug']}: owner did not expose a stable thread before timeout")
    if op.get("cancelled_before_effect") is not None:
        raise T.TransitionError(f"{task['slug']}: owner was cancelled before effect")
    if op["physical"]["stage"] == "failed":
        raise T.TransitionError(f"{task['slug']}: owner failed before binding")
    projection = owner_projection(project, task) or {}
    result = {"dispatch_id": task["dispatch_id"], "engine": "codex",
              "routing": task.get("routing"), "agent": {
                "id": op["physical"]["process_unit_id"], "sessionId": task.get("session_id"),
                "state": "working" if projection.get("working") else "done", "engine": "codex"},
              "stdout": ""}
    if include_owner_identity:
        result["_owner_identity"] = _bound_owner_identity(op)
    return result



def l2_env(project: str, task: dict) -> dict:
    """The ownership identity every fresh or resumed L2 session needs."""
    return {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": task["slug"],
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_SESSION_KEY": f"{project}--{task['dispatch_id']}",
            "ALTITUDE_DISPATCH_ID": str(task["dispatch_id"]),
            "ALTITUDE_L2_TOKEN": str(task["l2_token"]),
            # Codex strips TOKEN-named values from model subprocesses. This alias
            # retains only the current task attempt's scoped capability.
            "ALTITUDE_L2_CAPABILITY": str(task["l2_token"])}


def _require_resume_snapshot(task: dict, slug: str, *, expected_dispatch_id: str | None = None,
                             expected_session_id: str | None = None,
                             expected_agent_id: str | None = None,
                             expected_state: str | None = None) -> None:
    if task.get("state") not in ("running", "blocked"):
        raise T.TransitionError(f"{slug}: owner can be resumed only while running or blocked")
    for label, expected, current in (
        ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
        ("session", expected_session_id, task.get("session_id")),
        ("agent", expected_agent_id, task.get("agent_id")),
        ("state", expected_state, task.get("state")),
    ):
        if expected is not None and current != expected:
            raise T.TransitionError(f"{slug}: owner {label} changed before resume")


def _issue_resume_prompt(project: str, task: dict, prompt: str) -> tuple[str, str | None]:
    try:
        snapshot = github_intake.ensure_snapshot(
            project, task["slug"], expected_state=task.get("state"),
            expected_dispatch_id=task.get("dispatch_id"), expected_session_id=task.get("session_id"),
            expected_agent_id=task.get("agent_id"))
    except github_intake.IssueIntakeError as exc:
        raise T.TransitionError(f"GitHub issue intake held before owner resume: {exc}") from exc
    if not snapshot or task.get("github_issue_context_delivered") == snapshot["content_sha256"]:
        return prompt, None
    rendered = github_intake.render(snapshot)
    return (prompt if github_intake.marker(snapshot) in prompt else rendered + "\n\n" + prompt,
            snapshot["content_sha256"])


def _defer_owner_request(project: str, slug: str, request: dict, hold: str) -> dict:
    """Atomically block one exact persisted resume without crossing its next effect boundary."""
    prompt = request["prompt"]
    def defer(live):
        current = _owner(live, project=project, required=True)
        exact = (current["request"] == request and not _owner_terminal(current))
        successor = (_owner_terminal(current) and current.get("successor") == request)
        if (not (exact or successor) or live.get("state") not in ("running", "blocked")):
            raise T.TransitionError(f"{slug}: owner request changed before admission deferral")
        previous = live["state"]
        expected_retry = {
            "resume_after": S.now(), "resume_answer": prompt, "resume_prefix": "",
            "resume_exact_prompt": True, "resume_message_id": request["message_id"],
        }
        live.update({"state": "blocked", "blocked_reason": hold, **expected_retry})
        event = (("state", {"frm": previous, "to": "blocked", "by": "altd", "reason": hold})
                 if previous != "blocked" else None)
        return live, None, event
    task, _ = T.owner_command(
        project, slug, defer,
        lambda value: _owner(value, project=project, required=True))
    S.regen_state_md(project)
    return task


def _resume_owner(project: str, slug: str, prompt: str, *, message_id: str, **expected) -> dict:
    task = _active_task(project, slug)
    _require_resume_snapshot(task, slug, **expected)
    T.require_owner_provider_capability(task)
    if l2_engine(task) != "codex" or _owner(task, project=project) is None:
        raise T.TransitionError("legacy non-Codex ownership cannot be resumed")
    if not task.get("session_id") or not task.get("worktree"):
        raise T.TransitionError(f"{slug}: exact provider thread and worktree are required")
    prompt, issue_digest = _issue_resume_prompt(project, task, prompt)
    request = _request(message_id, prompt, task.get("engine_model"), task.get("routing") or {}, issue_digest)
    task, disposition = _begin_owner_request(project, slug, request)
    if disposition == "stop":
        task = stop_owner(project, slug, "superseded by persisted owner request")
        if hold := wip_hold(project, task, existing_owner=True):
            _defer_owner_request(project, slug, request, hold)
            return {"deferred": True, "hold": hold, "waiting": hold}
        try:
            task = _promote_successor(project, slug)
        except T.TransitionError:
            task = _active_task(project, slug)
            if _owner(task, project=project, required=True)["request"] != request:
                raise
    elif disposition == "recover":
        current = _owner(task, project=project, required=True)
        if current["request"] != request:
            raise T.TransitionError(f"{slug}: owner request changed before recovery")

    op = _owner(task, project=project, required=True)
    try:
        if op["preparation"]["stage"] in ("planned", "applying"):
            if hold := wip_hold(project, task, existing_owner=True):
                _defer_owner_request(project, slug, request, hold)
                return {"deferred": True, "hold": hold, "waiting": hold}
            task = _settle_owner_preparation(project, task, existing_worktree=True)
        task = _continue_prepared_owner(project, task)
    except (git_policy.GitPolicyError, recovery.LaunchHeld, T.TransitionError) as exc:
        if isinstance(exc, (recovery.LaunchHeld, OwnerRecoveryFenceError)):
            hold = str(exc)
            _defer_owner_request(project, slug, request, hold)
            return {"deferred": True, "hold": hold, "waiting": hold}
        raise
    result = _wait_owner_started(project, task, include_owner_identity=True)
    live = _active_task(project, slug)
    if request.get("issue_digest"):
        live = _task_owner_update(project, slug, lambda value:
                                  value.update({"github_issue_context_delivered": request["issue_digest"]}))
    result["deferred"] = False
    return result


def resume_session(project: str, slug: str, text: str, session_id: str | None = None, *,
                   message_id: str | None = None, _include_owner_identity: bool = False,
                   **expected) -> dict:
    task = _active_task(project, slug)
    if session_id is not None and task.get("session_id") != session_id:
        raise T.TransitionError(f"{slug}: requested provider thread is stale")
    stable = message_id or hashlib.sha256(
        f"owner\0{project}\0{slug}\0{task.get('dispatch_id')}\0{secrets.token_hex(16)}".encode()).hexdigest()
    result = _resume_owner(project, slug, text, message_id=stable, **expected)
    if not _include_owner_identity:
        result.pop("_owner_identity", None)
    return result


def resume_blocked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ", *,
                   message_id: str | None = None, **expected) -> dict:
    task = _active_task(project, slug)
    _require_resume_snapshot(task, slug, **expected)
    if capability_hold := provider_capability_hold(task):
        return {"deferred": True, "hold": capability_hold, "waiting": capability_hold}
    hold = wip_hold(project, task) if task["state"] == "blocked" else None
    if hold:
        waiting = (f"waiting for lease: {hold.removeprefix('file lease: ')}"
                   if hold.startswith("file lease: ") else f"waiting: {hold}")
        def defer(live):
            live.setdefault("blocked_question", live.get("blocked_reason"))
            live.update({"resume_answer": answer, "resume_prefix": prefix,
                         "resume_after": S.now(), "blocked_reason": waiting})
        _task_owner_update(project, slug, defer)
        return {"deferred": True, "hold": hold, "waiting": waiting}
    prompt = (answer if task.get("resume_exact_prompt") and answer == task.get("resume_answer") else
              f"{prefix}{answer}\nContinue from your progress file; finish to *done* and rewrite the report.")
    stable = message_id or hashlib.sha256(
        f"owner\0{project}\0{slug}\0{task.get('dispatch_id')}\0{secrets.token_hex(16)}".encode()
    ).hexdigest()
    def persist_resume(live):
        if live.get("state") != "blocked":
            raise T.TransitionError(f"{slug}: blocked resume changed before durable intent")
        live.update({"resume_after": S.now(), "resume_answer": prompt, "resume_prefix": "",
                     "resume_exact_prompt": True, "resume_message_id": stable})
    pending = _task_owner_update(project, slug, persist_resume)
    result = resume_session(project, slug, prompt, message_id=stable,
                            _include_owner_identity=True, **expected)
    if result.get("deferred"):
        return result
    owner_identity = result.pop("_owner_identity", None)
    if not isinstance(owner_identity, dict):
        raise T.TransitionError(f"{slug}: bound resume completion identity is unavailable")
    completion = _resume_completion_expectation(pending, owner_identity)
    live = _active_task(project, slug)
    _complete_blocked_resume(project, live, completion)
    result["deferred"] = False
    return result


def message_l2(project: str, slug: str, text: str, *, expected_dispatch_id: str | None = None,
               expected_session_id: str | None = None, expected_engine: str | None = None) -> dict:
    """Persist a stable human message, then install that exact message as the sole successor request."""
    text = str(text or "").strip()
    if not text:
        raise T.TransitionError("task message is empty")
    task = _active_task(project, slug)
    _require_resume_snapshot(task, slug)
    for label, expected, actual in (
        ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
        ("session", expected_session_id, task.get("session_id")),
        ("engine", expected_engine, l2_engine(task)),
    ):
        if expected is not None and str(expected) != str(actual or ""):
            raise T.TransitionError(f"{slug}: {label} changed; refresh before steering")
    if not task.get("dispatch_id") or not task.get("session_id"):
        raise T.TransitionError(f"{slug}: no current L2 dispatch ownership")
    if capability_hold := provider_capability_hold(task):
        raise T.TransitionError(capability_hold)
    message = T.append_task_message(
        project, slug, "burak", text, actor="burak",
        expected_dispatch_id=task["dispatch_id"], expected_session_id=task["session_id"],
        expected_state=task["state"])
    expected = {"expected_dispatch_id": task["dispatch_id"], "expected_session_id": task["session_id"],
                "expected_agent_id": task.get("agent_id"), "expected_state": task["state"]}
    if task["state"] == "blocked":
        result = resume_blocked(project, slug, text, message_id=message["id"], **expected)
    else:
        result = resume_session(project, slug, text, message_id=message["id"], **expected)
    return {**result, "message": message}


def resume_due(project: str) -> list[str]:
    """Tasks blocked by an exhausted window come back by themselves once it reopens — oldest first, WIP-throttled."""
    now, back = S.now(), []
    due = [t for t in S.list_tasks(project) if t["state"] == "blocked" and t.get("resume_after") and t["resume_after"] <= now]
    for t in sorted(due, key=_resume_order):
        if provider_capability_hold(t):
            continue
        if l2_engine(t) == "claude" and engines.usage_hold():
            continue
        if wip_hold(project, t):
            continue  # a lease holds this one; a younger unrelated task may still go
        if "resume_answer" in t:
            answer = t["resume_answer"]
            prefix = t.get("resume_prefix", "")
        else:
            answer = "The usage window has reopened; Altitude held you, nothing is wrong with the task."
            prefix = ""
        res = resume_blocked(project, t["slug"], answer, prefix=prefix,
                             message_id=t.get("resume_message_id"))
        if res and res.get("deferred"):
            continue
        def clear(t2):
            t2.pop("resume_after", None)
            t2.pop("resume_answer", None)
            t2.pop("resume_prefix", None)
            t2.pop("resume_exact_prompt", None)
            t2.pop("resume_message_id", None)
        _task_owner_update(project, t["slug"], clear)
        back.append(t["slug"])
    return back


def _resume_order(task: dict) -> tuple[str, str]:
    """The deterministic oldest-first order shared by due resumes and pending-resume leases."""
    return (task.get("created") or "", task.get("slug") or "")


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


def paths_overlap(a: list[str], b: list[str]) -> list[str]:
    """Paths collide when equal or when one is a directory prefix of the other."""
    out = []
    for x in map(_norm, a):
        for y in map(_norm, b):
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                out.append(x if len(x) >= len(y) else y)
    return sorted(set(out))


BROAD_CLAIMS = ("tests", "docs", "altitude", "web", "hooks", "bin", "personas", "schemas", "templates", "src", "lib", "app")


def narrow(paths: list[str]) -> list[str]:
    """Drop whole top-level directory claims because they are too broad to be useful leases.

    Files and deeper directories still lease, and briefs still show the original declared scope.
    """
    return [p for p in paths if p.strip("/").split("/")[0] != p.strip("/") or p.strip("/") not in BROAD_CLAIMS]


def hold_conflict(mine: list[str], others: list[dict]) -> str | None:
    """Return the first narrowed file-lease conflict with ``others``, if any."""
    mine = narrow(mine)
    for other in others:
        hit = paths_overlap(mine, narrow(other.get("paths", [])))
        if hit:
            activity = other.get("activity") or (
                "blocked with a pending resume" if other.get("pending_resume") else "running")
            return f"file lease: `{other['slug']}` is {activity} on {', '.join(hit[:4])}"
    return None


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
    """Tasks that currently hold file leases, including blocked tasks queued to resume."""
    return [t for t in S.list_tasks(project)
            if t["slug"] != exclude
            and (t["state"] == "running" or (t["state"] == "blocked" and t.get("resume_after")))]


def leases(project: str, exclude: str | None = None) -> list[dict]:
    """Running and pending-resume tasks and the paths they hold, for status and briefs."""
    out = []
    for task in _lease_tasks(project, exclude):
        lease = {"slug": task["slug"], "paths": task_paths(project, task)}
        if task["state"] == "blocked":
            lease["pending_resume"] = True
        out.append(lease)
    return out


def job_detail(agent_id: str | None) -> tuple[str, datetime | None]:
    """What the worker last said about itself (`~/.claude/jobs/<id>/state.json` detail) and when — the limit message
    lands here, and "resets 8pm" only means something relative to the moment it was written."""
    if not agent_id:
        return "", None
    p = JOBS_DIR / str(agent_id) / "state.json"
    try:
        st = json.loads(p.read_text())
        return (str(st.get("detail") or "") if isinstance(st, dict) else ""), datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    except (OSError, ValueError):
        return "", None


PER_TASK_HOLDS = ("file lease", "recovery hold")  # skip held ordinary work so the claimed repair can be reached


def per_task_hold(hold: str | None) -> bool:
    return bool(hold) and str(hold).startswith(PER_TASK_HOLDS)


def _disabled_owner_scope_uncertain(task: dict, paths: list[str]) -> bool:
    """A disabled writer without one valid narrow lease remains repository-uncertain."""
    if not provider_capability_hold(task):
        return False
    declared = task.get("paths")
    return (not isinstance(declared, list) or not declared
            or any(not isinstance(item, str) or not item.strip() for item in declared)
            or not narrow(paths))


def wip_hold(project: str, task: dict | None = None, *, existing_owner: bool = False) -> str | None:
    held = recovery.dispatch_hold(project, task)
    if held:
        return held
    project_tasks = S.list_tasks(project)
    # Disabled-provider legacy rows remain protected by their repository lease,
    # but do not consume the scarce runnable-provider WIP capacity.
    running = [t for t in project_tasks
               if t["state"] == "running" and not provider_capability_hold(t)
               and not (existing_owner and task and t["slug"] == task["slug"])]
    proj = config.project(project)
    if task:
        mine = task_paths(project, task)
        mine_pending = task.get("state") == "blocked" and bool(task.get("resume_after"))
        holders = []
        for other in _lease_tasks(project, exclude=task["slug"]):
            pending_resume = other["state"] == "blocked"
            other_paths = task_paths(project, other)
            disabled_owner = provider_capability_hold(other)
            if disabled_owner and (
                not narrow(mine) or _disabled_owner_scope_uncertain(other, other_paths)
            ):
                return (f"file lease: `{other['slug']}` has disabled-provider ownership with "
                        "repository-uncertain path scope")
            if (not disabled_owner and pending_resume and mine_pending
                    and _resume_order(other) >= _resume_order(task)):
                continue  # enabled overlapping resumes proceed oldest-first; disabled owners never resume
            holders.append({"slug": other["slug"], "paths": narrow(other_paths),
                            "pending_resume": pending_resume})
        held = hold_conflict(mine, holders)
        if held:
            return held
    if len(running) >= int(proj.get("wip", config.WIP_PER_PROJECT)):
        return f"WIP limit: {len(running)} running in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p)
                if t["state"] == "running" and not provider_capability_hold(t)
                and not (existing_owner and task and p == project and t["slug"] == task["slug"]))
    if total >= config.WIP_PER_MACHINE:
        return f"WIP limit: {total} running on this machine"
    return None


def poll(project: str) -> list[dict]:
    """Return L2 turns that exited, using each task's persisted engine adapter."""
    task_rows = S.list_tasks(project)
    needs_claude = any(t["state"] == "running" and l2_engine(t) == "claude" for t in task_rows)
    claude_rows = engines.claude_agents() if needs_claude else []
    agents = {a.get("sessionId"): a for a in claude_rows}
    by_id = {a.get("id"): a for a in claude_rows}
    finished = []
    for t in task_rows:
        has_report = (S.task_dir(project, t["slug"]) / "report.json").exists()
        if t["state"] == "blocked" and has_report and "idle without a report" in (t.get("blocked_reason") or ""):
            finished.append({"task": t, "agent": None})  # report landed after the idle check: hand it to the verifier
            continue
        if t["state"] != "running":
            continue
        engine = l2_engine(t)
        if engine == "claude":
            a = agents.get(t.get("session_id")) or by_id.get(t.get("agent_id"))
        else:
            t = reconcile_owner(project, t["slug"])
            projection = owner_projection(project, t)
            result = (_owner(t, project=project, required=True).get("result") or {})
            a = ({"id": t.get("agent_id"), "sessionId": t.get("session_id"),
                  "status": "exited" if projection["terminal"] else "busy",
                  "state": "done" if projection["terminal"] else "working",
                  "usage": result.get("usage") or {}, "detail": result.get("error") or "",
                  "action": result.get("action")} if projection else None)
        live_p = config.MONITOR_DIR / f"live-{project}--{t['slug']}.json"
        prev = S.read_json(live_p, {}) or {}
        live = ({"status": a.get("status"), "state": a.get("state"), "engine": engine,
                 "pid": a.get("pid"), "usage": a.get("usage")} if a else None)
        idle_since = None
        detail, at = ((job_detail(a.get("id")) if engine == "claude"
                       else (str(a.get("detail") or ""), datetime.now(timezone.utc)))
                      if a else ("", None))
        settled = a and (a.get("state") in ("blocked", "done", "failed", "stopped")
                         or a.get("status") in ("idle", "exited"))
        if settled and not has_report:
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
        died = (a is None or a.get("state") == "failed") and not has_report
        if died:  # worker gone before a report: raised as a system fault by the server, never read as "still running"
            finished.append({"task": t, "agent": a, "died": True})
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
DEPLOY_DIRS = ("altitude/", "bin/", "systemd/")   # code the running altd loaded at start; everything else is read per use


def _pin_ref(repo: Path, ref: str) -> tuple[str | None, str | None]:
    try:
        resolved = subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                                  cwd=str(repo), capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
        return None, str(exc)
    sha = (resolved.stdout or "").strip()
    if resolved.returncode != 0 or not sha:
        return None, (resolved.stderr or "").strip()[:120] or f"exit {resolved.returncode}"
    return sha, None


def _squash_equivalent(repo: Path, branch_sha: str, main_sha: str) -> tuple[bool | None, str | None]:
    """Prove every nonempty net path changed by a pinned branch has identical content on pinned main."""
    try:
        base = subprocess.run(["git", "merge-base", branch_sha, main_sha], cwd=str(repo),
                              capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
        return None, f"cannot find merge base: {exc}"
    base_sha = (base.stdout or "").strip()
    if base.returncode != 0 or not base_sha:
        detail = (base.stderr or "").strip()[:120] or f"exit {base.returncode}"
        return None, f"cannot find merge base: {detail}"

    def changed(left: str, right: str) -> tuple[set[str] | None, str | None]:
        try:
            diff = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", left, right],
                                  cwd=str(repo), capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
            return None, str(exc)
        if diff.returncode != 0:
            return None, (diff.stderr or "").strip()[:120] or f"exit {diff.returncode}"
        raw = diff.stdout or ""
        if raw and not raw.endswith("\0"):
            return None, "git diff returned malformed NUL-delimited paths"
        return {path for path in raw.split("\0") if path}, None

    touched, error = changed(base_sha, branch_sha)
    if error:
        return None, f"cannot read branch paths: {error}"
    if not touched:
        return False, "branch has no net changed paths"
    different, error = changed(branch_sha, main_sha)
    if error:
        return None, f"cannot compare branch with main: {error}"
    return not bool(touched & different), None


def _ref_still_at(repo: Path, ref: str, expected: str) -> bool:
    current, error = _pin_ref(repo, ref)
    return error is None and current == expected


def _merged_pr_receipt(repo: Path, task: dict, branch: str, branch_sha: str) -> tuple[bool | None, str]:
    """Confirm GitHub merged this exact task branch tip; tree equality alone is not publication evidence."""
    verified = task.get("verified") if isinstance(task.get("verified"), dict) else {}
    raw_numbers = verified.get("prs") if verified.get("verdict") == "ok" else []
    if not isinstance(raw_numbers, list):
        return False, "task has no well-formed verified merged-PR receipt"
    numbers = sorted({int(value) for value in raw_numbers
                      if not isinstance(value, bool) and isinstance(value, (int, str)) and str(value).isdigit()})
    if not numbers:
        return False, "task has no verified merged-PR receipt"
    errors = []
    for number in numbers:
        try:
            viewed = subprocess.run(
                ["gh", "pr", "view", str(number), "--json", "number,state,baseRefName,headRefName,headRefOid"],
                cwd=str(repo), capture_output=True, text=True, timeout=60, env=engines.clean_env())
        except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
            errors.append(f"PR #{number}: {exc}")
            continue
        if viewed.returncode != 0:
            detail = (viewed.stderr or viewed.stdout or "").strip()[:120] or f"exit {viewed.returncode}"
            errors.append(f"PR #{number}: {detail}")
            continue
        try:
            info = json.loads(viewed.stdout or "{}")
        except (TypeError, ValueError) as exc:
            errors.append(f"PR #{number}: invalid response ({exc})")
            continue
        if not isinstance(info, dict):
            errors.append(f"PR #{number}: invalid response shape")
            continue
        if (info.get("number") == number and info.get("state") == "MERGED" and info.get("baseRefName") == "main"
                and info.get("headRefName") == branch
                and info.get("headRefOid") == branch_sha):
            return True, f"verified merged PR #{number}"
    if errors:
        return None, "; ".join(errors)[:240]
    return False, "no verified merged PR matches the task branch and pinned tip"


def _l1_patch_matches_current(worktree: Path, patch_path: Path) -> tuple[bool | None, str | None]:
    """Recreate L1's binary patch and require it to equal the durable artifact byte-for-byte."""
    from . import land
    try:
        groups = land._changes(worktree)
        changed = sorted({path for _xy, group in groups for path in group})
        if not changed:
            return False, "dirty status had no reproducible changed paths"
        untracked = sorted({path for xy, group in groups if xy == "??" for path in group})
        if untracked:
            added = subprocess.run(["git", "add", "-N", "--", *untracked], cwd=str(worktree),
                                   capture_output=True, text=True, timeout=30)
            if added.returncode != 0:
                detail = (added.stderr or added.stdout or "").strip()[:120] or f"exit {added.returncode}"
                return None, f"cannot prepare current L1 diff: {detail}"
        try:
            current = subprocess.run(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", *changed],
                                     cwd=str(worktree), capture_output=True, text=True, timeout=60)
        finally:
            if untracked:
                subprocess.run(["git", "reset", "-q", "HEAD", "--", *untracked], cwd=str(worktree),
                               capture_output=True, text=True, timeout=30)
        if current.returncode != 0:
            detail = (current.stderr or current.stdout or "").strip()[:120] or f"exit {current.returncode}"
            return None, f"cannot capture current L1 diff: {detail}"
        return current.stdout == patch_path.read_text(), None
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, RuntimeError) as exc:
        return None, str(exc)


def pull_after_done(project: str, task: dict) -> list[str]:
    """When a project's checkout is its deployment, fast-forward it to
    origin/main after a task lands, so merged hooks, personas and templates are what the next session runs. Python
    changes need a restart: those are announced with an FYI and `monitor/restart-pending.json`, never restarted from here."""
    import subprocess
    proj = config.project(project)
    if not proj.get("self_deploy", project == "altitude"):
        return []
    repo = config.project_path(project)
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        if br != "main":
            return [f"self-deploy skipped: checkout on {br!r}, not main"]
        git_policy.fetch_origin(repo, "main")
        git_policy.service_preflight(repo, "main")
        pull = subprocess.run(["git", "merge", "-q", "--ff-only", "origin/main"], cwd=str(repo), capture_output=True, text=True, timeout=120)
        if pull.returncode != 0:
            raise git_policy.GitPolicyError(
                f"fast-forward failed: {(pull.stderr or pull.stdout).strip()[:300] or f'exit {pull.returncode}'}"
            )
        new = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, timeout=15).stdout.strip()
        if new == head:
            return []
        files = subprocess.run(["git", "diff", "--name-only", head, new], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout.split()
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
        from . import incidents
        incidents.system_fault("self-deploy", f"{project}: {e}", project=project, task=task.get("slug"))
        T.fyi(project, task.get("slug"), f"self-deploy refused in {repo}: {str(e)[:300]}")
        return [f"self-deploy refused: {str(e)[:160]}"]
    code = [f for f in files if f.startswith(DEPLOY_DIRS)]
    notes = [f"self-deploy: main {head[:7]} → {new[:7]} ({len(files)} files)"]
    if code:
        pend_p = config.MONITOR_DIR / RESTART_PENDING
        pend = S.read_json(pend_p, {}) or {}
        pend = {"since": pend.get("since") or S.now(), "head": new, "files": sorted(set(pend.get("files", [])) | set(code))}
        S.write_json(pend_p, pend)
        T.fyi(project, task.get("slug"), f"restart pending: altd runs code older than main ({len(pend['files'])} file(s) under "
                                        f"{'/'.join(d.rstrip('/') for d in DEPLOY_DIRS)} changed since {pend['since'][:16]}Z) — "
                                        "an authorized service restart after verification.")
        notes.append(f"restart pending ({len(code)} code files)")
    return notes


def cleanup_after_done(project: str, task: dict) -> list[str]:
    """After `done`, remove only this task's published L2 and completed L1/reviewer worktrees.

    Ownership comes from the task's persisted L2 path and L1/reviewer records, before any session or git-state guard is
    applied. An unfinished owned record is an expected deferral: it is logged and returned in the notes even if git
    cannot list the worktree, and it never raises. `pull_after_done` is attempted after both normal cleanup and
    fail-closed early returns caused by Git fetch/list or Claude-session lookup failures.

    The current `server.tick` caller assigns this return to `notes`, unconditionally stamps the task cleaned, and only
    then logs the notes. Deferred and skipped trees therefore are not retried by that caller. `claude rm` also deletes
    its worktree rather than offering a session-only removal, so it is called only after that exact L2 candidate passes
    every guard; a background session can remain when its tree is ineligible. Orphan reclamation is intentionally
    outside this done-time pass and belongs to the accepted caller/prune follow-up."""
    import subprocess
    from . import incidents
    repo = config.project_path(project)
    slug = task.get("slug") or ""
    notes = []

    def path_key(path: str | Path) -> str:
        return str(Path(path).resolve())

    worktrees_root = (repo / ".claude" / "worktrees").resolve()

    def l1_records(owner_slug: str) -> list[dict]:
        directory = S.task_dir(project, owner_slug) / "l1"
        if not directory.is_dir():
            return []
        return [rec for p in sorted(directory.glob("*.json"))
                if (rec := S.read_json(p, {})) and rec.get("name") and rec.get("role") in ("implementer", "reviewer")]

    def record_paths(owner_slug: str, owner_task: dict, rec: dict) -> list[tuple[str, str]]:
        """Return convention-derived and validated persisted paths, with their cleanup kind."""
        paths = [(path_key(worktrees_root / f"{owner_slug[:30]}-{rec['name']}"), "L1")]
        persisted = rec.get("worktree")
        if not persisted:
            return paths
        persisted_key = path_key(persisted)
        l2_keys = {path_key(worktrees_root / owner_slug)}
        if owner_task.get("worktree"):
            l2_keys.add(path_key(owner_task["worktree"]))
        persisted_path = Path(persisted_key)
        in_l1_namespace = (persisted_path.parent == worktrees_root
                           and persisted_path.name.startswith(f"{owner_slug[:30]}-"))
        if persisted_key in l2_keys or in_l1_namespace:
            persisted_kind = "L2" if persisted_key in l2_keys else "L1"
            if (persisted_key, persisted_kind) not in paths:
                paths.append((persisted_key, persisted_kind))
        return paths

    def captured_patch(owner_slug: str, rec: dict, candidate_path: str) -> dict | None:
        """Bind one completed L1 record to its exact worktree, parent, and conventional durable patch."""
        if rec.get("role") != "implementer" or not rec.get("done"):
            return None
        result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
        if result.get("error") or not isinstance(result.get("patch"), str) or not rec.get("parent_sha"):
            return None
        if not rec.get("worktree") or path_key(rec["worktree"]) != candidate_path:
            return None
        artifact_root = (S.task_dir(project, owner_slug) / "l1").resolve()
        patch = Path(result["patch"]).resolve()
        expected = (artifact_root / f"{rec['name']}.patch").resolve()
        if patch != expected or not patch.is_file():
            return None
        return {"patch": str(patch), "name": rec["name"], "parent_sha": rec["parent_sha"],
                "worktree": candidate_path}

    # Include archived records: `done` archives the task before the server reaches this function. The passed record is
    # also included because direct callers and old state may not have a status file on disk.
    task_records = {t.get("slug"): t for t in S.list_tasks(project, include_archive=True) if t.get("slug")}
    task_records[slug] = task
    owners: dict[str, set[str]] = {}
    own_candidates: dict[str, dict] = {}
    for owner_slug, owner_task in task_records.items():
        # A cleaned archived task has already relinquished reusable L1-prefix paths. Active and not-yet-cleaned tasks
        # retain ownership; the task currently being cleaned remains an owner even for defensive direct callers.
        retains_ownership = owner_slug == slug or not owner_task.get("cleaned")
        if retains_ownership and owner_task.get("worktree"):
            key = path_key(owner_task["worktree"])
            owners.setdefault(key, set()).add(owner_slug)
            if owner_slug == slug:
                own_candidates[key] = {"kind": "L2", "unfinished": False}
        for rec in l1_records(owner_slug):
            if not retains_ownership:
                continue
            for key, kind in record_paths(owner_slug, owner_task, rec):
                owners.setdefault(key, set()).add(owner_slug)
                if owner_slug == slug:
                    candidate = own_candidates.setdefault(key, {"kind": kind, "unfinished": False,
                                                                 "l1_patch_proofs": []})
                    if kind == "L2":
                        candidate["kind"] = "L2"
                    if not rec.get("done"):
                        candidate["unfinished"] = True
                    if proof := captured_patch(owner_slug, rec, key):
                        candidate.setdefault("l1_patch_proofs", []).append(proof)

    # Deferral comes from persisted ownership, not from git's transient view. Record every unfinished L1/reviewer even
    # when its worktree is absent from (or cannot be read through) `git worktree list`.
    deferred_keys = set()
    for key, candidate in own_candidates.items():
        if candidate["unfinished"]:
            reason = "persisted L1 record has no done stamp"
            S.append_event(project, slug, "cleanup-worktree", action="deferred", worktree=key, reason=reason)
            notes.append(f"deferred worktree {Path(key).name}: {reason}")
            deferred_keys.add(key)

    def finish_after_failure(reason: str) -> list[str]:
        for key in own_candidates:
            if key not in deferred_keys:
                S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=key, reason=reason)
        notes.append(f"skipped worktree cleanup: {reason}")
        notes.extend(pull_after_done(project, task))
        return notes

    try:
        fetch = subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=str(repo), capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as e:
        incidents.system_fault("cleanup-git", f"{project}: {e}", project=project, task=slug)
        return finish_after_failure(f"git fetch failed: {e}")
    if fetch.returncode != 0:
        error = (fetch.stderr or fetch.stdout).strip()[:120] or "git fetch failed"
        reason = f"could not refresh origin/main: {error}"
        incidents.system_fault("cleanup-fetch", f"{project}: {reason}", project=project, task=slug)
        return finish_after_failure(reason)
    try:
        listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(repo), capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        incidents.system_fault("cleanup-git", f"{project}: {e}", project=project, task=slug)
        return finish_after_failure(f"git worktree list failed: {e}")
    if listed.returncode != 0:
        error = (listed.stderr or listed.stdout).strip()[:120] or "git worktree list failed"
        incidents.system_fault("cleanup-git", f"{project}: {error}", project=project, task=slug)
        return finish_after_failure(f"git worktree list failed: {error}")

    records = []
    wt, branch, locked = None, None, False
    for line in listed.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            wt = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            branch = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif line == "locked" or line.startswith("locked "):
            locked = True
        elif line == "":
            if wt:
                key = path_key(wt)
                if key in own_candidates:  # Ownership is the first candidate filter.
                    records.append((wt, branch, locked, key, own_candidates[key]))
            wt, branch, locked = None, None, False

    eligible = []
    for wt, branch, locked, key, candidate in records:
        if key in deferred_keys:
            continue
        if owners.get(key, set()) != {slug}:
            reason = "also owned by task(s): " + ", ".join(sorted(owners[key] - {slug}))
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
        elif locked:
            reason = "git worktree is locked"
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
        else:
            eligible.append((wt, branch, candidate))

    live = []
    if l2_engine(task) == "claude":
        try:
            live = [path_key(a["cwd"]) for a in engines.claude_agents()
                    if a.get("cwd") and a.get("state") not in ("failed", "done", "stopped")]
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            reason = f"live Claude session list unavailable: {e}"
            incidents.system_fault("cleanup-agents", f"{project}: cannot list live sessions, removing nothing: {e}",
                                   project=project, task=slug)
            for wt, _branch, _candidate in eligible:
                S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree cleanup: {e}")
            notes.extend(pull_after_done(project, task))
            return notes

    if l2_engine(task) == "codex" and task.get("agent_id") and task.get("worktree"):
        codex_row = owner_projection(project, task)
        if codex_row and codex_row.get("working"):
            live.append(path_key(task["worktree"]))

    def has_live_worker(path: str) -> bool:
        key = path_key(path)
        return any(key == cwd or key.startswith(cwd + "/") or cwd.startswith(key + "/") for cwd in live)

    for wt, branch, candidate in eligible:
        reason = None
        squash_equivalent = False
        pinned_branch = None
        pinned_main = None
        if has_live_worker(wt):
            reason = "live L2 worker is using the worktree"
        elif not branch:
            reason = "git worktree has no branch"
        else:
            pinned_branch, branch_error = _pin_ref(repo, f"refs/heads/{branch}")
            pinned_main, main_error = _pin_ref(repo, "refs/remotes/origin/main")
            if branch_error or main_error:
                reason = f"cannot pin cleanup refs: {branch_error or main_error}"
                incidents.system_fault("cleanup-ref", f"{project}/{slug} {branch}: {reason}",
                                       project=project, task=slug)
            try:
                ancestry = (subprocess.run(["git", "merge-base", "--is-ancestor", pinned_branch, pinned_main],
                                           cwd=str(repo), capture_output=True, text=True, timeout=30)
                            if not reason else None)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"merge-base indeterminate: {e}"
                incidents.system_fault("cleanup-merge-base", f"{project}/{slug} {branch}: {reason}", project=project, task=slug)
            if not reason and ancestry.returncode == 1:
                expected_l2_path = path_key(task.get("worktree")) if task.get("worktree") else None
                expected_branch = f"worktree-{slug}"
                if (candidate["kind"] != "L2" or path_key(wt) != expected_l2_path
                        or branch != task.get("branch") or branch != expected_branch):
                    reason = "squash cleanup is limited to the task's exact persisted L2 branch and worktree"
                else:
                    squash_equivalent, equivalent_error = _squash_equivalent(repo, pinned_branch, pinned_main)
                    if squash_equivalent is None:
                        reason = f"squash equivalence indeterminate: {equivalent_error}"
                        incidents.system_fault("cleanup-equivalence", f"{project}/{slug} {branch}: {reason}",
                                               project=project, task=slug)
                    elif not squash_equivalent:
                        reason = equivalent_error or "branch has commits not on origin/main"
                    else:
                        receipt, receipt_detail = _merged_pr_receipt(repo, task, branch, pinned_branch)
                        if receipt is None:
                            reason = f"merged-PR receipt indeterminate: {receipt_detail}"
                            incidents.system_fault("cleanup-publication-receipt", f"{project}/{slug} {branch}: {reason}",
                                                   project=project, task=slug)
                        elif not receipt:
                            reason = receipt_detail
            elif not reason and ancestry.returncode != 0:
                stderr = (ancestry.stderr or "").strip()[:120] or "(empty stderr)"
                reason = f"merge-base indeterminate (exit {ancestry.returncode}); stderr: {stderr}"
                incidents.system_fault("cleanup-merge-base", f"{project}/{slug} {branch}: {reason}", project=project, task=slug)
        if reason:
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            continue
        if (not _ref_still_at(repo, f"refs/heads/{branch}", pinned_branch)
                or not _ref_still_at(repo, "refs/remotes/origin/main", pinned_main)):
            reason = "branch or origin/main moved after cleanup proof"
            incidents.system_fault("cleanup-branch-race", f"{project}/{slug} {branch}: {reason}",
                                   project=project, task=slug)
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            continue
        dirty_l1_with_patch = False
        try:
            status = subprocess.run(["git", "-C", wt, "status", "--porcelain", "-z",
                                     "--untracked-files=all", "--ignored=traditional"],
                                    cwd=str(repo), capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError, UnicodeError) as e:
            reason = f"cannot inspect worktree changes: {e}"
        else:
            if status.returncode != 0:
                detail = (status.stderr or status.stdout or "").strip()[:120] or f"exit {status.returncode}"
                reason = f"cannot inspect worktree changes: {detail}"
            else:
                raw_status = status.stdout or ""
                if raw_status and not raw_status.endswith("\0"):
                    reason = "cannot inspect worktree changes: malformed NUL-delimited status"
                records = [record for record in raw_status.split("\0") if record] if not reason else []
                ignored = [record[3:] for record in records if record.startswith("!! ")]
                disposable_ignored = [path for path in ignored
                                      if "__pycache__" in Path(path).parts and Path(path).suffix in (".pyc", ".pyo")]
                protected_ignored = sorted(set(ignored) - set(disposable_ignored))
                dirty = [record for record in records if not record.startswith("!! ")]
                if protected_ignored:
                    reason = f"worktree has ignored data: {', '.join(protected_ignored)[:160]}"
                elif dirty:
                    proofs = candidate.get("l1_patch_proofs") or []
                    proof = proofs[0] if candidate["kind"] == "L1" and len(proofs) == 1 else None
                    if proof and proof.get("worktree") == path_key(wt) and proof.get("parent_sha") == pinned_branch:
                        matches, match_error = _l1_patch_matches_current(Path(wt), Path(proof["patch"]))
                        if matches:
                            dirty_l1_with_patch = True
                        elif matches is None:
                            reason = f"cannot validate current L1 diff against captured patch: {match_error}"
                        else:
                            reason = "dirty L1 worktree no longer matches its exact captured patch"
                    else:
                        reason = "worktree has tracked or untracked changes without one exact captured L1 patch"
        if reason:
            if reason.startswith("cannot inspect"):
                incidents.system_fault("cleanup-worktree-status", f"{project}/{slug} {wt}: {reason}",
                                       project=project, task=slug)
            elif reason.startswith("cannot validate current L1 diff"):
                incidents.system_fault("cleanup-l1-patch", f"{project}/{slug} {wt}: {reason}",
                                       project=project, task=slug)
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            continue
        removal_reason = ("task branch content is present in origin/main (squash-equivalent)"
                          if squash_equivalent else "task-owned branch is merged into origin/main")
        removed_l2_worker = (candidate["kind"] == "L2" and bool(task.get("agent_id"))
                             and l2_engine(task) == "claude")
        if removed_l2_worker:
            try:
                engine = l2_engine(task)
                rm_note = engines.claude_rm(task["agent_id"])
            except (subprocess.SubprocessError, OSError, RuntimeError) as e:
                reason = f"{engine} worker cleanup failed: {e}"
                incidents.system_fault("cleanup-worker", f"{project}/{slug}: {reason}", project=project, task=slug)
            else:
                notes.append(f"{engine} worker {task['agent_id']}: {(rm_note or 'completed')[:120]}")
        if not reason:
            try:
                remove_cmd = (["git", "worktree", "remove", "--force", wt]
                              if dirty_l1_with_patch else ["git", "worktree", "remove", wt])
                rm = subprocess.run(remove_cmd, cwd=str(repo), capture_output=True,
                                    text=True, timeout=60)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"git worktree remove failed: {e}"
            else:
                if rm.returncode != 0:
                    error = (rm.stderr or rm.stdout).strip()[:120] or f"exit {rm.returncode}"
                    reason = f"git worktree remove failed: {error}"
            if reason:
                incidents.system_fault("cleanup-worktree-remove", f"{project}/{slug} {wt}: {reason}",
                                     project=project, task=slug)
        if not reason:
            try:
                deleted = subprocess.run(["git", "update-ref", "-d", f"refs/heads/{branch}", pinned_branch],
                                         cwd=str(repo), capture_output=True, text=True, timeout=30)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"git branch compare-and-delete failed after worktree removal: {e}"
            else:
                if deleted.returncode != 0:
                    error = (deleted.stderr or deleted.stdout).strip()[:120] or f"exit {deleted.returncode}"
                    reason = f"git branch compare-and-delete failed after worktree removal: {error}"
            if reason:
                incidents.system_fault("cleanup-branch-delete", f"{project}/{slug} {branch}: {reason}",
                                     project=project, task=slug)
        if reason:
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"could not remove {Path(wt).name}: {reason}")
            continue
        if removed_l2_worker:
            removal_reason += "; L2 worker removed"
        elif dirty_l1_with_patch:
            removal_reason += "; dirty L1 worktree removed after validating its captured patch"
        S.append_event(project, slug, "cleanup-worktree", action="removed", worktree=wt, reason=removal_reason)
        notes.append(f"removed merged worktree {Path(wt).name}")
    notes.extend(pull_after_done(project, task))
    return notes


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--managed-owner":
        raise SystemExit(_managed_owner(sys.argv[2], sys.argv[3], sys.argv[4]))
    raise SystemExit("dispatch is not a public command entrypoint")
