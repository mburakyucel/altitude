"""Small strict physical records for tests that persist helper aggregates."""
import hashlib

from altitude import config, engines, state as S


def helper_physical(identity: str, *, terminal: str | None = "complete") -> dict:
    record = engines.new_physical_transition(
        transition_id=f"helper:{identity}:generation-1", subject_kind="helper", subject_id=identity,
        generation="generation-1", provider="codex", provider_session_request={"kind": "fresh"},
        message_id=f"message:{identity}")
    unit = record["process_unit_id"]
    record = engines.advance_physical_transition(
        record, "planned", "prior_stopped", {"previous_process_unit_id": None, "empty": True})
    record = engines.advance_physical_transition(
        record, "prior_stopped", "spawned", {"process_unit_id": unit, "launched": True})
    if terminal is None:
        return record
    if terminal == "failed":
        record = engines.note_physical_transition_error(record, "fixture failure")
    record = engines.advance_physical_transition(
        record, "spawned", "bound",
        {"bound": True, "physical_worker_id": unit, "provider_session_id": "fixture-session"})
    record = engines.advance_physical_transition(
        record, "bound", "result_observed", {"result_id": "fixture-result", "sha256": "a" * 64})
    record = engines.advance_physical_transition(record, "result_observed", "empty", {
        "process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
        "sub_state": "dead", "control_group": "", "population": "empty", "empty": True})
    return engines.advance_physical_transition(record, "empty", terminal, {"status": terminal})


def helper_record(project: str, slug: str, *, terminal: str | None = None, role: str = "implementer",
                  patch: str | None = None, error: str | None = None, request_seed: str = "fixture",
                  parent_sha: str = "1" * 40, parent_worktree: str | None = None) -> dict:
    from altitude import l1

    request_id = hashlib.sha256(f"{request_seed}:{project}/{slug}".encode()).hexdigest()
    name = f"helper-{request_id}"
    parent = {
        "version": 1,
        "owner_result": {
            "version": 1, "provider": "codex", "project": project, "slug": slug,
            "owner_generation": 1, "transition_id": "fixture-owner-transition",
            "generation": "fixture-owner-generation", "process_unit_id": "fixture-parent.service",
            "message_id": "fixture-owner-message", "intent_digest": "2" * 64,
            "result_id": "fixture-owner-result", "result_sha256": "3" * 64,
            "terminal_stage": "complete", "empty_receipt_sha256": "4" * 64,
            "recovery_episode_id": None, "recovery_permit_revision": None,
        },
        "task_scope": ["tests"],
        "worktree": parent_worktree or str(config.project_path(project).resolve()),
        "branch": "main", "base_sha": parent_sha,
    }
    parent_digest = hashlib.sha256(S._canonical_json(parent)).hexdigest()  # noqa: SLF001
    generation = hashlib.sha256(
        f"helper-generation\0{parent_digest}\0{request_id}".encode()
    ).hexdigest()
    physical = engines.new_physical_transition(
        transition_id=f"helper:{project}/{slug}/{name}:{generation}", subject_kind="helper",
        subject_id=f"{project}/{slug}/{name}", generation=generation, provider="codex",
        provider_session_request={"kind": "fresh"}, message_id=f"helper-message:{request_id}")
    if terminal is not None:
        unit = physical["process_unit_id"]
        physical = engines.advance_physical_transition(
            physical, "planned", "prior_stopped", {"previous_process_unit_id": None, "empty": True})
        physical = engines.advance_physical_transition(
            physical, "prior_stopped", "spawned", {"process_unit_id": unit, "launched": True})
        if terminal in ("complete", "failed"):
            failure = error or "fixture failure"
            if terminal == "failed":
                physical = engines.note_physical_transition_error(physical, failure)
            physical = engines.advance_physical_transition(
                physical, "spawned", "bound",
                {"bound": True, "physical_worker_id": unit, "provider_session_id": "fixture-session"})
            result = {
                "provider_session_id": "fixture-session", "status": terminal,
                "summary": None if terminal == "failed" else "fixture complete",
                "error": failure if terminal == "failed" else None,
                "patch": patch if terminal == "complete" else None,
                "findings": [] if role == "reviewer" else None, "usage": {},
            }
            physical = engines.advance_physical_transition(
                physical, "bound", "result_observed",
                {"result_id": f"helper-result:{request_id}",
                 "sha256": hashlib.sha256(S._canonical_json(result)).hexdigest()})  # noqa: SLF001
            physical = engines.advance_physical_transition(physical, "result_observed", "empty", {
                "process_unit_id": unit, "load_state": "not-found", "active_state": "inactive",
                "sub_state": "dead", "control_group": "", "population": "empty", "empty": True})
            physical = engines.advance_physical_transition(physical, "empty", terminal, {"status": terminal})
        else:
            result = None
    else:
        result = None
    return {
        "version": 1, "name": name, "request_id": request_id, "role": role, "model": None,
        "parent": parent, "parent_digest": parent_digest,
        "brief": "fixture", "paths": ["tests"] if role == "implementer" else [],
        "preparation": {**l1._preparation(  # noqa: SLF001
            project, slug, request_id, role, parent, "fixture", ["tests"] if role == "implementer" else []),
            "stage": "ready", "worktree_status_sha256": hashlib.sha256(b"").hexdigest()},
        "physical": physical, "result": result,
    }
