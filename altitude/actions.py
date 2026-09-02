"""Trusted post-turn actions for contained Codex L2 workers.

The model can edit ordinary files in its isolated worktree and return inert JSON. It cannot write Altitude state,
Git metadata, or the network. Only this control-plane module, after the worker's whole cgroup is empty and its
dispatch identity is still current, may turn its closed WorkerOutcome into state changes, helpers, or publication.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config, contracts, dispatch, engines, land, l1, recovery, state as S, tasks as T

MAX_HELPERS = 4
MAX_HELPER_RESULT_BYTES = engines.RAW_CAPTURE_CAP
MAX_OWNER_RESULT_BYTES = 64 * 1024
MAX_OUTCOME_BYTES = engines.RAW_CAPTURE_CAP
MAX_OUTCOME_LOG_BYTES = 8 * engines.RAW_CAPTURE_CAP
OWNER_RESULT_FIELDS = {
    "version", "provider", "project", "slug", "owner_generation", "transition_id", "generation",
    "process_unit_id", "message_id", "intent_digest", "result_id", "result_sha256", "terminal_stage",
    "empty_receipt_sha256", "recovery_episode_id", "recovery_permit_revision",
}
OUTCOME_STAGES = ("claimed", "message_posted", "effecting", "complete", "cancelled")


class ActionError(RuntimeError):
    """An untrusted or stale action was refused before privileged side effects."""


def _bounded_canonical(value: dict, limit: int, label: str) -> bytes:
    try:
        encoded = S._canonical_json(value)  # noqa: SLF001 - shared strict canonical wire encoding
    except (TypeError, ValueError) as exc:
        raise ActionError(f"{label} is not strict JSON: {exc}") from exc
    if len(encoded) > limit:
        raise ActionError(f"{label} exceeds {limit} bytes")
    return encoded


def _validate_owner_result(value: object, project: str, slug: str) -> dict:
    if type(value) is not dict or set(value) != OWNER_RESULT_FIELDS:
        raise ActionError("owner result snapshot has an invalid closed shape")
    result = dict(value)
    digests = ("intent_digest", "result_sha256", "empty_receipt_sha256")
    identifiers = ("transition_id", "generation", "process_unit_id", "message_id", "result_id")
    episode, permit = result["recovery_episode_id"], result["recovery_permit_revision"]
    if (result["version"] != 1 or result["provider"] != "codex"
            or result["project"] != project or result["slug"] != slug
            or isinstance(result["owner_generation"], bool)
            or not isinstance(result["owner_generation"], int) or result["owner_generation"] < 1
            or result["terminal_stage"] != "complete"
            or any(not isinstance(result[key], str) or not result[key] for key in identifiers)
            or any(not isinstance(result[key], str)
                   or re.fullmatch(r"[0-9a-f]{64}", result[key]) is None for key in digests)
            or (episode is None) != (permit is None)
            or episode is not None and (not isinstance(episode, str) or not episode
                                        or isinstance(permit, bool)
                                        or not isinstance(permit, int) or permit < 1)):
        raise ActionError("owner result snapshot is not exact terminal-empty Codex evidence")
    _bounded_canonical(result, MAX_OWNER_RESULT_BYTES, "owner result snapshot")
    return result


def _validate_shape(action: object) -> dict:
    if type(action) is not dict or set(action) != {"message", "outcome"}:
        raise ActionError("Codex L2 result must contain exactly message and outcome")
    message = action.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ActionError("Codex L2 result requires a human-readable task message")
    try:
        outcome = contracts.validate_worker_outcome(action.get("outcome"))
    except contracts.ContractError as exc:
        raise ActionError(f"Codex L2 returned an invalid WorkerOutcome: {exc}") from exc
    helpers = outcome.get("helper_requests") if outcome["kind"] == "continue" else []
    if len(helpers) > MAX_HELPERS:
        raise ActionError(f"continue permits at most {MAX_HELPERS} helper requests")
    if any(helper.get("provider") not in (None, "codex") for helper in helpers):
        raise ActionError("enabled helper requests accept only Codex")
    result = {"message": message.strip(), "outcome": outcome}
    _bounded_canonical(result, MAX_OUTCOME_BYTES, "WorkerOutcome envelope")
    return result


def _settled_owner_result(project: str, task: dict) -> tuple[dict, dict, dict]:
    """Read only the Phase 1B typed result; no dispatch/session/agent/action fallback is authority."""
    require = getattr(dispatch, "require_owner_result", None)
    if not callable(require):
        raise ActionError("Phase 1B exact owner-result snapshot is not integrated")
    try:
        settled = require(project, task)
    except T.TransitionError as exc:
        raise ActionError(str(exc)) from exc
    if (type(settled) is not dict
            or set(settled) != {"task", "agent", "result", "owner_result", "worker_result"}
            or type(settled.get("task")) is not dict or type(settled.get("agent")) is not dict):
        raise ActionError("Phase 1B returned a malformed settled owner result")
    live = settled["task"]
    if live.get("slug") != task.get("slug"):
        raise ActionError("settled owner result crossed task context")
    owner_result = _validate_owner_result(settled["owner_result"], project, live["slug"])
    marker = settled["result"]
    if (type(marker) is not dict
            or set(marker) != {"present", "process_unit_id", "intent_digest", "result_id", "sha256"}
            or marker.get("present") is not True
            or marker.get("process_unit_id") != owner_result["process_unit_id"]
            or marker.get("intent_digest") != owner_result["intent_digest"]
            or marker.get("result_id") != owner_result["result_id"]
            or marker.get("sha256") != owner_result["result_sha256"]):
        raise ActionError("settled durable result marker differs from owner result snapshot")
    _bounded_canonical(marker, MAX_OWNER_RESULT_BYTES, "durable result marker")
    action = _validate_shape(settled["worker_result"])
    return live, owner_result, action


def _outcome_path(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / "outcomes.jsonl"


def _outcome_id(owner_result: dict) -> str:
    return hashlib.sha256(b"worker-outcome\0" + _bounded_canonical(
        owner_result, MAX_OWNER_RESULT_BYTES, "owner result snapshot")).hexdigest()


def _reply_id(outcome_id: str) -> str:
    return hashlib.sha256(f"worker-outcome-reply\0{outcome_id}".encode()).hexdigest()


def _claim_row(owner_result: dict, action: dict) -> dict:
    outcome_id = _outcome_id(owner_result)
    return {
        "version": 1, "record_id": f"{outcome_id}:claimed", "outcome_id": outcome_id,
        "stage": "claimed", "owner_result": owner_result, "message_id": _reply_id(outcome_id),
        "message": action["message"], "outcome": action["outcome"],
    }


def _stage_row(outcome_id: str, stage: str) -> dict:
    if stage not in OUTCOME_STAGES[1:]:
        raise ActionError(f"invalid outcome stage {stage!r}")
    return {"version": 1, "record_id": f"{outcome_id}:{stage}",
            "outcome_id": outcome_id, "stage": stage}


def _read_outcomes(project: str, slug: str) -> list[dict]:
    path = _outcome_path(project, slug)
    try:
        if path.stat().st_size > MAX_OUTCOME_LOG_BYTES:
            raise ActionError(f"{slug}: outcome journal exceeds {MAX_OUTCOME_LOG_BYTES} bytes")
    except FileNotFoundError:
        return []
    rows = S.read_jsonl(path, key_field="record_id")
    for row in rows:
        stage = row.get("stage") if isinstance(row, dict) else None
        expected = ({"version", "record_id", "outcome_id", "stage", "owner_result",
                     "message_id", "message", "outcome"} if stage == "claimed"
                    else {"version", "record_id", "outcome_id", "stage"})
        if (type(row) is not dict or set(row) != expected or row.get("version") != 1
                or stage not in OUTCOME_STAGES
                or row.get("record_id") != f"{row.get('outcome_id')}:{stage}"):
            raise ActionError(f"{slug}: malformed canonical outcome record")
        if stage == "claimed":
            owner = _validate_owner_result(row["owner_result"], project, slug)
            action = _validate_shape({"message": row["message"], "outcome": row["outcome"]})
            if (row["outcome_id"] != _outcome_id(owner)
                    or row["message_id"] != _reply_id(row["outcome_id"])
                    or action["message"] != row["message"] or action["outcome"] != row["outcome"]):
                raise ActionError(f"{slug}: outcome claim identity changed")
        _bounded_canonical(row, MAX_OUTCOME_BYTES + MAX_OWNER_RESULT_BYTES,
                           "outcome journal row")
    return rows


def _find_claim(project: str, slug: str, outcome_id: str) -> dict:
    rows = _read_outcomes(project, slug)
    matches = [row for row in rows if row["outcome_id"] == outcome_id and row["stage"] == "claimed"]
    if len(matches) != 1:
        raise ActionError(f"{slug}: outcome claim artifact is missing or ambiguous")
    return matches[0]


def _claim(project: str, task: dict, action: dict, owner_result: dict) -> dict:
    """Persist one exact owner outcome before any conversation or process effect."""
    owner_result = _validate_owner_result(owner_result, project, task["slug"])
    row = _claim_row(owner_result, action)
    claimant = getattr(dispatch, "owner_operation_lock", None)
    current_snapshot = getattr(dispatch, "owner_result_snapshot", None)
    permit_fence = getattr(dispatch, "require_owner_permit_current", None)
    if not all(callable(fn) for fn in (claimant, current_snapshot, permit_fence)):
        raise ActionError("Phase 1B exact owner-result claimant is not integrated")
    with claimant(project, task["slug"]):
        permit_fence(project, S.load_task(project, task["slug"]))
        with S.project_lock(project):
            live = S.load_task(project, task["slug"])
            if live.get("state") not in ("running", "blocked"):
                raise ActionError(f"{task['slug']}: task is no longer outcome-settleable")
            if current_snapshot(project, live) != owner_result:
                raise ActionError(f"{task['slug']}: owner result changed before outcome claim")
            existing = live.get("outcome_ref")
            if existing is not None and (type(existing) is not dict
                                         or set(existing) != {"id", "stage"}
                                         or existing.get("id") != row["outcome_id"]
                                         or existing.get("stage") not in OUTCOME_STAGES):
                raise ActionError(f"{task['slug']}: a different outcome is already claimed")
            if existing is None:
                live["outcome_ref"] = {"id": row["outcome_id"], "stage": "claimed"}
                S.save_task(project, live)
            S.append_jsonl(_outcome_path(project, task["slug"]), row, key_field="record_id")
            if existing is None:
                S.append_event(project, task["slug"], "l2-outcome-claimed",
                               outcome_id=row["outcome_id"], outcome_kind=action["outcome"]["kind"])
    return row


def _advance(project: str, slug: str, outcome_id: str, stage: str) -> dict:
    """Task reference is authoritative; append its deterministic receipt before the next mutation."""
    with S.project_lock(project):
        live = S.load_task(project, slug)
        ref = live.get("outcome_ref")
        if type(ref) is not dict or ref.get("id") != outcome_id or ref.get("stage") not in OUTCOME_STAGES:
            raise ActionError(f"{slug}: outcome claim is no longer current")
        old = OUTCOME_STAGES.index(ref["stage"])
        new = OUTCOME_STAGES.index(stage)
        if new < old or (ref["stage"] in ("complete", "cancelled") and stage != ref["stage"]):
            raise ActionError(f"{slug}: invalid outcome stage {ref['stage']} -> {stage}")
        if stage != ref["stage"]:
            live["outcome_ref"] = {"id": outcome_id, "stage": stage}
            S.save_task(project, live)
        if stage != "claimed":
            S.append_jsonl(_outcome_path(project, slug), _stage_row(outcome_id, stage),
                           key_field="record_id")
        return live


def cancel_claimed_outcome(project: str, slug: str) -> None:
    """Cancel a claim that has not crossed the broker's effect-intent boundary."""
    task = S.load_task(project, slug)
    ref = task.get("outcome_ref")
    if not ref or ref.get("stage") in ("complete", "cancelled"):
        return
    if ref.get("stage") == "effecting":
        raise T.TransitionError(f"{slug}: claimed outcome effect must reconcile before terminal archive")
    try:
        _find_claim(project, slug, ref["id"])
    except ActionError:
        # State-first claim crash: reconstruct only from the exact still-current B3 durable result.
        live, owner_result, action = _settled_owner_result(project, task)
        if _outcome_id(owner_result) != ref["id"]:
            raise T.TransitionError(f"{slug}: cannot reconstruct claimed outcome from current owner result")
        _claim(project, live, action, owner_result)
    _advance(project, slug, ref["id"], "cancelled")


def _post_message(project: str, task: dict, record: dict) -> None:
    ref = S.load_task(project, task["slug"]).get("outcome_ref") or {}
    if ref.get("stage") in ("message_posted", "effecting", "complete"):
        return
    T.append_task_message(project, task["slug"], "l2", record["message"],
                          expected_dispatch_id=str(task.get("dispatch_id") or ""),
                          expected_l2_token=str(task.get("l2_token") or ""), actor="l2",
                          message_id=record["message_id"], expected_outcome_id=record["outcome_id"])
    _advance(project, task["slug"], record["outcome_id"], "message_posted")


def _defer_for_recovery(project: str, task: dict, reason: str) -> dict:
    live = S.load_task(project, task["slug"])
    if live.get("state") == "running":
        try:
            T.block(project, task["slug"], f"pending trusted action: {reason}",
                    expected_state="running", expected_dispatch_id=task.get("dispatch_id"),
                    expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        except T.TransitionError as exc:
            raise ActionError(str(exc)) from exc
    return {"kind": "pending", "reason": reason}


def _resume_same(project: str, task: dict, record: dict, prompt: str) -> dict:
    outcome_id = record["outcome_id"]
    _advance(project, task["slug"], outcome_id, "effecting")
    try:
        if task.get("state") == "blocked":
            result = dispatch.resume_blocked(
                project, task["slug"], prompt, prefix="Altitude control plane: ",
                expected_dispatch_id=task.get("dispatch_id"), expected_session_id=task.get("session_id"),
                expected_agent_id=task.get("agent_id"), expected_state="blocked",
                expected_outcome_id=outcome_id,
            )
        else:
            result = dispatch.resume_session(
                project, task["slug"], prompt,
                expected_dispatch_id=task.get("dispatch_id"), expected_session_id=task.get("session_id"),
                expected_agent_id=task.get("agent_id"), expected_state=task.get("state"),
                expected_outcome_id=outcome_id,
            )
    except T.TransitionError:
        # A recovery hold can race after the settled worker was stopped. Dispatch then durably stores this exact
        # prompt as a blocked pending resume; that durable payload, rather than the old action, owns the retry.
        live = S.load_task(project, task["slug"])
        if (live.get("state") == "blocked" and live.get("resume_exact_prompt") is True
                and live.get("resume_answer") == prompt):
            _advance(project, task["slug"], outcome_id, "complete")
            return {"kind": "pending", "reason": live.get("blocked_reason")}
        raise
    _advance(project, task["slug"], outcome_id, "complete")
    return {"kind": "pending" if result.get("deferred") else "resumed",
            "agent": result.get("agent"), "reason": result.get("waiting")}


def _report(action: dict, result: dict, task: dict) -> dict:
    outcome = action["outcome"]
    main_run = result.get("main_run") if isinstance(result.get("main_run"), dict) else None
    runs = []
    if main_run and main_run.get("databaseId"):
        runs.append({"id": str(main_run["databaseId"]),
                     "conclusion": str(main_run.get("conclusion") or main_run.get("status") or "pending")})
    local = result.get("local_tests") if isinstance(result.get("local_tests"), dict) else None
    if not runs and local and local.get("passed"):
        runs.append({"id": f"local:{str(result.get('head') or '')[:12]}", "conclusion": "success"})
    prs = []
    if result.get("pr"):
        prs.append({"number": int(result["pr"]), "title": outcome.get("pr_title") or outcome["commit_message"].splitlines()[0],
                    "merged": bool(result.get("merged")), "merge_sha": None})
    blocked = ""
    if result.get("hold"):
        blocked = f"merge held for Burak: {result['hold']}"
    elif outcome.get("request_merge") and not result.get("merged"):
        blocked = f"publication incomplete: checks are {result.get('checks')}"
    return {
        "landed": {"prs": prs, "main_runs": runs,
                   "deploy": "not-applicable"},
        "review": [],
        "blocked": blocked,
        "decisions": [], "fyi": [], "follow_ups": [], "deviations": [], "spend": {},
    }


def _publish(project: str, task: dict, record: dict) -> dict:
    action = {"message": record["message"], "outcome": record["outcome"]}
    outcome = action["outcome"]
    proj = config.project(project)
    test_cmd = str(proj.get("test_cmd") or land.DEFAULT_TEST_CMD)
    wants_merge = outcome["request_merge"]
    _advance(project, task["slug"], record["outcome_id"], "effecting")
    dispatch.require_owner_permit_current(project, S.load_task(project, task["slug"]))
    # Keep every strict dispatch/resume gate outside the short interval after GitHub accepts a merge but before the
    # self-deploy checkout reaches origin/main. Settlement also runs when land returns a retry-class error: GitHub may
    # have accepted the exact merge even when a later observation reports that the pinned pair moved.
    with dispatch.publication_settlement(project):
        try:
            result = land.land(
                str(outcome["commit_message"]), project=project,
                pr_title=str(outcome.get("pr_title") or "").strip() or None,
                merge=wants_merge, wait=int(proj.get("land_wait") or 600),
                base="main", test_cmd=test_cmd, cwd=Path(task["worktree"]),
                authority={"actor": "l2", "dispatch_id": task.get("dispatch_id"),
                           "l2_token": task.get("l2_token")},
            )
        finally:
            if wants_merge:
                dispatch.pull_after_done(project, task)
    report = _report(action, result, task)
    S.write_json(S.task_dir(project, task["slug"]) / "report.json", report)
    _advance(project, task["slug"], record["outcome_id"], "complete")
    return {"kind": "report", "land": result, "report": report}


def _helpers(project: str, task: dict, record: dict) -> dict:
    action = {"message": record["message"], "outcome": record["outcome"]}
    _advance(project, task["slug"], record["outcome_id"], "effecting")
    records = []
    task_dir = S.task_dir(project, task["slug"])
    for index, helper in enumerate(action["outcome"]["helper_requests"], 1):
        brief = task_dir / f"helper-request-{task.get('attempt', 0)}-{index}.md"
        S.atomic_write(brief, str(helper["brief"]).rstrip() + "\n")
        name = f"helper-{task.get('attempt', 0)}-{index}"
        existing = l1.load(project, task["slug"], name)
        dispatch.require_owner_permit_current(project, S.load_task(project, task["slug"]))
        records.append(existing or l1.start(
            project, task["slug"], brief, role=helper["role"], engine=helper.get("provider"),
            model=helper.get("model"),
            paths=(helper["scope"].get("paths") if helper["scope"]["kind"] == "paths" else None), name=name,
            expected_dispatch_id=str(task.get("dispatch_id") or ""),
            expected_l2_token=str(task.get("l2_token") or ""),
        ))
    waits = [l1.wait(project, task["slug"], record["name"], timeout=config.L1_TIMEOUT) for record in records]
    results = [wait.get("run") or {"name": record["name"], "error": "helper wait timed out"}
               for wait, record in zip(waits, records)]
    compact = []
    total = 0
    artifact_root = (task_dir / "l1").resolve()
    for result in results:
        item = {key: result.get(key) for key in ("name", "role", "engine", "findings", "summary", "error")}
        patch_path = result.get("patch")
        if patch_path:
            path = Path(str(patch_path)).resolve()
            if not path.is_relative_to(artifact_root) or path.suffix != ".patch":
                raise T.TransitionError(f"helper {result.get('name')} returned an invalid patch artifact")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_HELPER_RESULT_BYTES:
                raise T.TransitionError("helper patch artifacts exceed the bounded L2 handoff size")
            item["patch"] = data.decode("utf-8", errors="replace")
        else:
            item["patch"] = None
        compact.append(item)
    prompt = ("Altitude finished the optional helpers you requested. No patch was auto-applied. Inspect these "
              "bounded inline patches/findings, integrate only what you judge useful, test the combined work, and return "
              f"your next schema-valid action:\n{json.dumps(compact, sort_keys=True)}")
    if len(prompt.encode("utf-8")) > MAX_HELPER_RESULT_BYTES:
        raise T.TransitionError("helper results exceed the bounded L2 handoff size")
    return _resume_same(project, task, record, prompt)


def process_l2(project: str, item: dict) -> dict:
    """Consume one settled Codex outcome; return a small instruction to the server workflow."""
    requested = item["task"]
    try:
        T.require_owner_provider_capability(requested)
    except T.TransitionError as exc:
        raise ActionError(str(exc)) from exc
    if requested.get("l2_engine") != "codex":
        raise ActionError("normalized outcome broker accepts only enabled Codex owners")
    task, owner_result, action = _settled_owner_result(project, requested)
    record = _claim(project, task, action, owner_result)
    stage = (S.load_task(project, task["slug"]).get("outcome_ref") or {}).get("stage")
    if stage == "complete":
        return {"kind": "settled", "outcome_id": record["outcome_id"]}
    if stage == "cancelled":
        raise ActionError(f"{task['slug']}: outcome was cancelled")
    if stage == "effecting":
        raise ActionError(f"{task['slug']}: outcome effect requires exact reconciliation")
    hold = recovery.dispatch_hold(project, S.load_task(project, task["slug"]))
    if hold:
        return _defer_for_recovery(project, task, hold)
    try:
        live = S.load_task(project, task["slug"])
        if live.get("state") == "blocked":
            live = T.resume(project, task["slug"], actor="altd", trusted_action=True,
                            expected_state="blocked", expected_dispatch_id=task.get("dispatch_id"),
                            expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        task = live
        _post_message(project, task, record)
        outcome = record["outcome"]
        kind = outcome["kind"]
        if kind == "publish":
            return _publish(project, task, record)
        if kind == "complete_no_code":
            _advance(project, task["slug"], record["outcome_id"], "effecting")
            with S.project_lock(project):
                live = S.load_task(project, task["slug"])
                if (live.get("state") != "running"
                        or (live.get("outcome_ref") or {}).get("id") != record["outcome_id"]):
                    raise T.TransitionError(f"{task['slug']}: completion ownership changed")
                live["completion_requested"] = {
                    "at": S.now(), "digest": outcome["digest"],
                    "dispatch_id": live.get("dispatch_id"), "agent_id": live.get("agent_id"),
                    "session_id": live.get("session_id"), "outcome_id": record["outcome_id"],
                }
                S.save_task(project, live)
            _advance(project, task["slug"], record["outcome_id"], "complete")
            done = T.finalize_completion(project, task["slug"],
                                         expected_dispatch_id=str(task.get("dispatch_id") or ""),
                                         expected_agent_id=task.get("agent_id"),
                                         expected_session_id=task.get("session_id"))
            return {"kind": "done", "task": done}
        if kind == "block":
            _advance(project, task["slug"], record["outcome_id"], "effecting")
            reason = f"{outcome['reason_or_question']} Resume when: {outcome['resume_condition']}"
            blocked = T.block(project, task["slug"], reason, actor="l2",
                              expected_state="running", expected_dispatch_id=task.get("dispatch_id"),
                              expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
            _advance(project, task["slug"], record["outcome_id"], "complete")
            return {"kind": "blocked", "task": blocked}
        if outcome["helper_requests"]:
            return _helpers(project, task, record)
        return _resume_same(project, task, record, "Continue for this exact reason from your previous action: "
                           + str(outcome["reason"]))
    except (land.LandError, T.TransitionError, OSError, ValueError) as exc:
        kind = str((action.get("outcome") or {}).get("kind") or "unknown")
        S.append_event(project, task["slug"], "l2-action-refused", action=kind, reason=str(exc)[:300])
        raise ActionError(f"{kind} outcome refused; its exact claim remains for reconciliation: {exc}") from exc


def pending(project: str) -> list[dict]:
    return [task for task in S.list_tasks(project)
            if (task.get("outcome_ref") or {}).get("stage") not in (None, "complete", "cancelled")]
