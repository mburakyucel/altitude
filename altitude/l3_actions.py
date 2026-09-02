"""Trusted application of one inert action returned by a read-only, contained Codex L3 turn."""
from __future__ import annotations

import hashlib
import json
import re

from . import config, dispatch, engines, github_intake, incidents, recovery, state as S, tasks as T


class L3ActionError(RuntimeError):
    pass


_ACTION_ID = re.compile(r"^[0-9a-f]{64}$")
_VALID = {"new_task", "task_done", "task_block", "task_fyi", "task_hold_merge",
          "github_issue", "incident_new", "incident_amend"}
_RECONCILABLE = {"github_issue"}
_ACTION_KEYS = {"type", "slug", "title", "text", "reason", "request", "digest", "answer", "source",
                "engine", "model", "paths", "hold_merge", "merge_hold", "incident", "labels"}
_INCIDENT_KEYS = {"id", "title", "task", "what", "evidence", "cause", "tags", "status"}
_JOURNAL_CAP = 65536
_JOURNAL_TERMINAL_HEADROOM = 8192
_JOURNAL_APPLYING_CAP = _JOURNAL_CAP - _JOURNAL_TERMINAL_HEADROOM
_JOURNAL_RESULT_CAP = 4096


def _need(value, field: str) -> str:
    if not isinstance(value, str):
        raise L3ActionError(f"L3 action requires string {field}")
    text = value.strip()
    if not text:
        raise L3ActionError(f"L3 action requires {field}")
    return text


def _effect_ok(check) -> None:
    if check:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - close the callback at the broker boundary
            raise L3ActionError(str(exc)) from exc


def _new_task_request(action: dict) -> str:
    """Accept the schema's inert text slot when Codex puts the L2 brief there."""
    request = action.get("request")
    request = request.strip() if isinstance(request, str) else ""
    return request or _need(action.get("text"), "request")


def _validate(structured: object) -> dict | None:
    if (not isinstance(structured, dict) or set(structured) != {"message", "actions"}
            or not isinstance(structured.get("message"), str)
            or not isinstance(structured.get("actions"), list)):
        raise L3ActionError("Codex L3 ended without a schema-valid action envelope")
    actions = structured["actions"]
    if len(actions) > 1:
        raise L3ActionError("one L3 turn may request at most one trusted action")
    if not actions:
        return None
    action = actions[0]
    incident = action.get("incident") if isinstance(action, dict) else None
    nullable_strings = ("slug", "title", "text", "reason", "request", "digest", "answer",
                        "source", "engine", "model", "hold_merge")
    incident_strings = ("id", "title", "task", "what", "evidence", "cause", "status")
    if (not isinstance(action, dict) or set(action) != _ACTION_KEYS or action.get("type") not in _VALID
            or any(action.get(key) is not None and not isinstance(action[key], str)
                   for key in nullable_strings)
            or action.get("engine") not in (None, "codex")
            or action.get("source") not in (None, "chat", "recovery")
            or action.get("merge_hold") is not None and not isinstance(action["merge_hold"], bool)
            or not isinstance(action.get("paths"), list)
            or not all(isinstance(path, str) for path in action["paths"])
            or not isinstance(action.get("labels"), list)
            or not all(isinstance(label, str) for label in action["labels"])
            or incident is not None and (not isinstance(incident, dict) or set(incident) != _INCIDENT_KEYS
                or any(incident.get(key) is not None and not isinstance(incident[key], str)
                       for key in incident_strings)
                or not isinstance(incident.get("tags"), list)
                or not all(isinstance(tag, str) for tag in incident["tags"]))):
        kind = action.get("type") if isinstance(action, dict) else None
        raise L3ActionError(f"unknown L3 action {kind!r}")
    kind = action["type"]
    if kind == "new_task" and action.get("source") == "recovery":
        raise L3ActionError("recovery repair delegation is dormant until the Phase 3 episode command")
    if kind in ("task_done", "task_block", "task_fyi", "task_hold_merge"):
        _need(action.get("slug"), "slug")
    if kind == "task_block":
        _need(action.get("reason"), "reason")
    if kind == "new_task":
        _need(action.get("title"), "title")
        _new_task_request(action)
    if kind == "github_issue":
        _need(action.get("title"), "title")
        _need(action.get("text"), "text")
    return action


def _journal_path(project: str, action_id: str):
    return config.project_dir(project) / "l3-actions" / f"{action_id}.json"


def _journal(path):
    if path.is_symlink():
        raise L3ActionError("L3 action journal is not one bounded regular record")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _JOURNAL_CAP:
        raise L3ActionError("L3 action journal is not one bounded regular record")
    record = S.read_json(path, None)
    if not isinstance(record, dict):
        raise L3ActionError("L3 action journal is corrupt")
    return record


def _journal_text(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def _write_journal(path, record: dict) -> None:
    """Write exactly the deterministic bytes whose cap every journal reader enforces."""
    text = _journal_text(record)
    if len(text.encode("utf-8")) > _JOURNAL_CAP:
        raise L3ActionError(f"L3 action journal exceeds {_JOURNAL_CAP} bytes")
    S.atomic_write(path, text)


def _bounded_result(result: list[dict] | None) -> list[dict]:
    value = result or []
    if not isinstance(value, list) or len(value) > 1 or any(not isinstance(row, dict) for row in value):
        raise L3ActionError("L3 action result is not one bounded JSON record")
    try:
        encoded = S._canonical_json({"result": value})  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise L3ActionError("L3 action result is not one bounded JSON record") from exc
    if len(encoded) > _JOURNAL_RESULT_CAP:
        raise L3ActionError(f"L3 action result exceeds {_JOURNAL_RESULT_CAP} bytes")
    return json.loads(encoded)["result"]


def _claim(project: str, action_id: str, action: dict) -> list[dict] | None:
    if not _ACTION_ID.fullmatch(str(action_id or "")):
        raise L3ActionError("Codex L3 action has no durable turn identity")
    path = _journal_path(project, action_id)
    with S.project_lock(project):
        record = _journal(path)
        if record is not None:
            if record.get("action") != action:
                raise L3ActionError("L3 action identity was reused with different content")
            if record.get("status") == "complete" and isinstance(record.get("result"), list):
                return record["result"]
            status = record.get("status") or "unknown"
            if status == "applying":
                if action.get("type") in _RECONCILABLE:
                    return None  # deterministic draft/remote marker reconciles the same effect below
                raise L3ActionError(
                    f"reconciliation_required: interrupted {action.get('type')} has no domain-native effect marker")
            raise L3ActionError(f"L3 action was already claimed with {status} outcome; refusing a duplicate")
        candidate = {"id": action_id, "at": S.now(), "status": "applying", "action": action}
        if len(_journal_text(candidate).encode("utf-8")) > _JOURNAL_APPLYING_CAP:
            raise L3ActionError(
                f"L3 action journal exceeds {_JOURNAL_APPLYING_CAP} bytes before reserved terminal headroom")
        _write_journal(path, candidate)
    return None


def _finish(project: str, action_id: str, action: dict, *, result: list[dict] | None = None,
            error: str | None = None) -> None:
    with S.project_lock(project):
        path = _journal_path(project, action_id)
        record = _journal(path)
        if record is None:
            raise L3ActionError("L3 action journal disappeared during application")
        if record.get("action") != action or record.get("status") != "applying":
            raise L3ActionError("L3 action journal changed during application")
        record.update({"finished": S.now(), "status": "failed" if error else "complete"})
        if error:
            record["error"] = error[:500]
        else:
            record["result"] = _bounded_result(result)
        _write_journal(path, record)


def _note_interrupted(project: str, action_id: str, action: dict, error: BaseException) -> None:
    """Keep a post-claim failure as the same durable mutation fence."""
    with S.project_lock(project):
        path = _journal_path(project, action_id)
        record = _journal(path)
        if record is None:
            raise L3ActionError("L3 action journal disappeared after an ambiguous application failure")
        if record.get("action") != action or record.get("status") != "applying":
            raise L3ActionError("L3 action journal changed after an ambiguous application failure")
        record.update({"last_error_at": S.now(), "last_error": str(error)[:500]})
        _write_journal(path, record)


def _worker_live(project: str, task: dict) -> bool:
    worker_id = task.get("agent_id")
    if not worker_id:
        return False
    engine = task.get("l2_engine") or "claude"
    try:
        if engine == "codex":
            row = engines.codex_worker(worker_id, job_root=dispatch.l2_job_root(project, task["slug"]))
        else:
            row = next((item for item in engines.claude_agents() if item.get("id") == worker_id), None)
    except Exception as exc:  # noqa: BLE001 -- inability to prove exit must fail closed
        raise L3ActionError(f"cannot prove L2 worker {worker_id} stopped: {exc}") from exc
    if not row:
        if engine == "codex":
            raise L3ActionError(f"cannot prove Codex L2 worker {worker_id} stopped: worker record is missing")
        return False
    return row.get("state") not in ("failed", "done", "stopped") and row.get("status") != "exited"


def _issue_draft_path(project: str, key: str):
    return config.project_dir(project) / "github-issue-drafts" / f"{key}.json"


def _github_issue_draft(project: str, action: dict, source: str | None) -> dict:
    title = _need(action.get("title"), "title")
    body = _need(action.get("text"), "text")
    exact_source = str(source or "").strip()
    if not exact_source or body.strip() != exact_source:
        raise L3ActionError("GitHub issue publication requires the exact current user message")
    if len(title) > 120 or "\n" in title or title.lower() not in exact_source.lower():
        raise L3ActionError("GitHub issue title must be a short exact phrase from the current user message")
    if len(body) > 6000:
        raise L3ActionError("GitHub issue draft is too large")
    labels = sorted(str(label) for label in (action.get("labels") or []))
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", label) for label in labels):
        raise L3ActionError("GitHub issue labels must use short lowercase slugs")
    key = hashlib.sha256(json.dumps([title, body, labels], separators=(",", ":")).encode()).hexdigest()[:24]
    draft = {"id": key, "created": S.now(), "status": "pending_review", "title": title, "body": body,
             "labels": labels}
    with S.project_lock(project):
        path = _issue_draft_path(project, key)
        existing = S.read_json(path, None)
        if isinstance(existing, dict):
            if any(existing.get(field) != draft.get(field) for field in ("title", "body", "labels")):
                raise L3ActionError("GitHub issue draft identity collision")
            draft = existing
        else:
            S.write_json(path, draft)
    return {"type": "github_issue", "id": key, "pending_review": draft.get("status") != "published",
            "url": draft.get("url")}


def _execute(project: str, action: dict, *, github_issue_source: str | None = None,
             effect_precheck=None, recovery_observation: dict | None = None) -> dict:
    _effect_ok(effect_precheck)
    kind = action["type"]
    slug = str(action.get("slug") or "").strip()
    if kind == "new_task":
        title = _need(action.get("title"), "title")
        request = _new_task_request(action)
        try:
            reference = github_intake.task_reference(title, request)
        except github_intake.IssueIntakeError as exc:
            raise L3ActionError(str(exc)) from exc
        if reference:
            authorized = github_intake.source_authorizes(github_issue_source, reference)
            if (not authorized and reference[0] is None
                    and github_intake.source_has_absolute_issue(github_issue_source, reference[2])):
                try:
                    repository = github_intake.project_repo(project)
                except github_intake.IssueIntakeError as exc:
                    raise L3ActionError(str(exc)) from exc
                authorized = github_intake.source_authorizes(
                    github_issue_source, reference, repository=repository)
            if not authorized:
                raise L3ActionError("GitHub issue task intake was not authorized by the current user message")
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            task = T.new(project, title, request,
                         actor="l3", source=action.get("source") or "chat", engine=action.get("engine"),
                         model=action.get("model"), paths=action.get("paths") or None,
                         hold_merge=action.get("hold_merge"))
        return {"type": kind, "slug": task["slug"]}
    if kind == "task_done":
        task = S.load_task(project, _need(slug, "slug"))
        if task.get("state") != "reported" or _worker_live(project, task):
            raise L3ActionError(f"{slug}: L3 may complete only a reported task with no live L2 worker")
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            done = T.done(project, slug, actor="l3", digest=action.get("digest") or "",
                          expected_state="reported", expected_dispatch_id=task.get("dispatch_id"),
                          expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        return {"type": kind, "slug": slug, "state": done["state"]}
    if kind == "task_block":
        task = S.load_task(project, _need(slug, "slug"))
        if task.get("state") not in ("running", "reported") or _worker_live(project, task):
            raise L3ActionError(f"{slug}: L3 cannot block a live or non-running/non-reported task")
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            blocked = T.block(project, slug, _need(action.get("reason"), "reason"), actor="l3",
                              expected_state=task.get("state"), expected_dispatch_id=task.get("dispatch_id"),
                              expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        return {"type": kind, "slug": slug, "state": blocked["state"]}
    if kind == "task_fyi":
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            T.fyi(project, _need(slug, "slug"), _need(action.get("text"), "text"), actor="l3")
        return {"type": kind, "slug": slug}
    if kind == "task_hold_merge":
        hold = bool(action.get("merge_hold"))
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            T.set_hold_merge(project, _need(slug, "slug"),
                             _need(action.get("reason"), "reason") if hold else None, actor="l3")
        return {"type": kind, "slug": slug, "held": hold}
    if kind == "github_issue":
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            return _github_issue_draft(project, action, github_issue_source)
    if kind == "incident_new":
        data = action.get("incident") if isinstance(action.get("incident"), dict) else {}
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            incident = incidents.new_incident(
                project, title=_need(data.get("title") or action.get("title"), "incident title"),
                task=data.get("task") or slug or None, what=_need(data.get("what"), "incident what"),
                evidence=_need(data.get("evidence"), "incident evidence"),
                cause=_need(data.get("cause"), "incident cause"), tags=data.get("tags") or [], actor="l3")
        return {"type": kind, "id": incident.get("id")}
    if kind == "incident_amend":
        data = action.get("incident") if isinstance(action.get("incident"), dict) else {}
        iid = _need(data.get("id"), "incident id")
        fields = {key: data[key] for key in ("what", "evidence", "cause", "status") if data.get(key) is not None}
        with recovery.l3_effect_permission(project, recovery_observation):
            _effect_ok(effect_precheck)
            incidents.amend_incident(project, iid, reason=_need(action.get("reason"), "reason"), actor="l3", **fields)
        return {"type": kind, "id": iid}
    raise L3ActionError(f"unknown L3 action {kind!r}")


def _apply(project: str, structured: object, *, action_id: str,
           github_issue_source: str | None = None, effect_precheck=None,
           recovery_observation: dict | None = None) -> list[dict]:
    action = _validate(structured)
    if action is None:
        return []
    if (action["type"] == "new_task" and isinstance(recovery_observation, dict)
            and recovery_observation.get("state") == "active"):
        raise L3ActionError("recovery repair delegation is dormant until the Phase 3 episode command")
    _effect_ok(effect_precheck)
    prior = _claim(project, action_id, action)
    if prior is not None:
        return prior
    try:
        result = [_execute(project, action, github_issue_source=github_issue_source,
                           effect_precheck=effect_precheck,
                           recovery_observation=recovery_observation)]
    except recovery.LaunchHeld as exc:
        _finish(project, action_id, action, error=str(exc))
        raise L3ActionError(str(exc)) from exc
    except (L3ActionError, T.TransitionError, KeyError, ValueError, OSError) as exc:
        _note_interrupted(project, action_id, action, exc)
        raise L3ActionError(
            f"reconciliation_required: {action.get('type')} may have crossed its effect boundary: {exc}"
        ) from exc
    _finish(project, action_id, action, result=result)
    return result


def apply(project: str, structured: object, *, action_id: str,
          github_issue_source: str | None = None, effect_precheck=None,
          recovery_observation: dict | None = None) -> list[dict]:
    return _apply(project, structured, action_id=action_id, github_issue_source=github_issue_source,
                  effect_precheck=effect_precheck, recovery_observation=recovery_observation)
