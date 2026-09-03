"""Trusted application of one inert action returned by a read-only, contained Codex L3 turn."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess

from . import config, dispatch, engines, incidents, recovery, state as S, tasks as T


class L3ActionError(RuntimeError):
    pass


_ACTION_ID = re.compile(r"^[0-9a-f]{64}$")
_VALID = {"new_task", "task_done", "task_block", "task_resume", "task_fyi", "task_hold_merge",
          "github_issue", "github_issue_approve", "incident_new", "incident_amend", "recovery_hold",
          "recovery_clear"}
_PUBLICATION_SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"\b[A-Za-z0-9_~+/=.-]{36,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[0-9a-fA-F]{48,}\b|"
    r"(?:api[_ -]?key|password|secret|credential|capability[_ -]?token|access[_ -]?token)"
    r"\s*(?::|=|\bis\b|\bwas\b))",
    re.I,
)


def _need(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise L3ActionError(f"L3 action requires {field}")
    return text


def _new_task_request(action: dict) -> str:
    """Accept the schema's inert text slot when Codex puts the L2 brief there."""
    request = str(action.get("request") or "").strip()
    return request or _need(action.get("text"), "request")


def _validate(structured: object) -> dict | None:
    if not isinstance(structured, dict) or not isinstance(structured.get("actions"), list):
        raise L3ActionError("Codex L3 ended without a schema-valid action envelope")
    actions = structured["actions"]
    if len(actions) > 1:
        raise L3ActionError("one L3 turn may request at most one trusted action")
    if not actions:
        return None
    action = actions[0]
    if not isinstance(action, dict) or action.get("type") not in _VALID:
        kind = action.get("type") if isinstance(action, dict) else None
        raise L3ActionError(f"unknown L3 action {kind!r}")
    kind = action["type"]
    if kind in ("task_done", "task_block", "task_resume", "task_fyi", "task_hold_merge"):
        _need(action.get("slug"), "slug")
    if kind in ("task_block", "recovery_hold", "recovery_clear"):
        _need(action.get("reason"), "reason")
    if kind == "new_task":
        _need(action.get("title"), "title")
        _new_task_request(action)
    if kind == "github_issue":
        _need(action.get("title"), "title")
        _need(action.get("text"), "text")
    if kind == "github_issue_approve":
        _need(action.get("digest"), "digest")
    return action


def _journal_path(project: str, action_id: str):
    return config.project_dir(project) / "l3-actions" / f"{action_id}.json"


def _claim(project: str, action_id: str, action: dict) -> list[dict] | None:
    if not _ACTION_ID.fullmatch(str(action_id or "")):
        raise L3ActionError("Codex L3 action has no durable turn identity")
    path = _journal_path(project, action_id)
    with S.project_lock(project):
        record = S.read_json(path, None)
        if isinstance(record, dict):
            if record.get("action") != action:
                raise L3ActionError("L3 action identity was reused with different content")
            if record.get("status") == "complete" and isinstance(record.get("result"), list):
                return record["result"]
            status = record.get("status") or "unknown"
            raise L3ActionError(f"L3 action was already claimed with {status} outcome; refusing a duplicate")
        S.write_json(path, {"id": action_id, "at": S.now(), "status": "applying", "action": action})
    return None


def _finish(project: str, action_id: str, action: dict, *, result: list[dict] | None = None,
            error: str | None = None) -> None:
    with S.project_lock(project):
        path = _journal_path(project, action_id)
        record = S.read_json(path, {}) or {}
        if record.get("action") != action or record.get("status") != "applying":
            raise L3ActionError("L3 action journal changed during application")
        record.update({"finished": S.now(), "status": "failed" if error else "complete"})
        if error:
            record["error"] = error[:500]
        else:
            record["result"] = result or []
        S.write_json(path, record)


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


def _resume_paths(project: str, task: dict, paths: object) -> tuple[list[str], list[str]]:
    """Validate and persist an L3 lease for the exact blocked task generation."""
    if not isinstance(paths, list):
        raise L3ActionError("task_resume paths must be an array")
    declared = []
    for value in paths:
        if not isinstance(value, str):
            raise L3ActionError("task_resume paths must contain only strings")
        path = value.strip().replace("\\", "/")
        if (not path or path.startswith("/") or "\x00" in path
                or any(part in ("", ".", "..") for part in path.split("/"))
                or posixpath.normpath(path) != path):
            raise L3ActionError(f"task_resume has invalid repo-relative path {value!r}")
        declared.append(path)
    if not declared:
        raise L3ActionError("task_resume paths must contain a non-empty path")

    declared = list(dict.fromkeys([*(str(path) for path in (task.get("paths") or [])), *declared]))
    candidate = {**task, "paths": declared}
    expanded = dispatch.task_paths(project, candidate)
    if not expanded:
        raise L3ActionError("task_resume paths do not declare a file lease")
    with S.project_lock(project):
        live = S.load_task(project, task["slug"])
        for label, expected, actual in (
            ("state", "blocked", live.get("state")),
            ("dispatch", task.get("dispatch_id"), live.get("dispatch_id")),
            ("session", task.get("session_id"), live.get("session_id")),
            ("agent", task.get("agent_id"), live.get("agent_id")),
        ):
            if expected != actual:
                raise L3ActionError(
                    f"{task['slug']}: {label} changed before paths were persisted "
                    f"({expected!r} → {actual!r})"
                )
        conflict = dispatch.hold_conflict(expanded, dispatch.leases(project, exclude=task["slug"]))
        if conflict:
            raise L3ActionError(f"{task['slug']}: {conflict}")
        previous = list(live.get("paths") or [])
        live["paths"] = declared
        S.save_task(project, live)
    return declared, previous


def _rollback_resume_paths(project: str, task: dict, declared: list[str], previous: list[str]) -> None:
    """Undo only our own lease write; never overwrite a newer task generation."""
    with S.project_lock(project):
        live = S.load_task(project, task["slug"])
        if (live.get("state") == "blocked" and live.get("dispatch_id") == task.get("dispatch_id")
                and live.get("session_id") == task.get("session_id")
                and live.get("agent_id") == task.get("agent_id")
                and live.get("paths") == declared):
            live["paths"] = previous
            S.save_task(project, live)


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


def _github_issue_approve(project: str, action: dict, source: str | None) -> dict:
    key = _need(action.get("digest"), "digest")
    if not re.fullmatch(r"[0-9a-f]{24}", key):
        raise L3ActionError("GitHub issue approval has an invalid draft id")
    expected = f"approve github issue publication {key}"
    if str(source or "").strip().lower() != expected:
        raise L3ActionError(f"GitHub issue publication requires the exact approval phrase: {expected}")
    path = _issue_draft_path(project, key)
    draft = S.read_json(path, None)
    if not isinstance(draft, dict):
        raise L3ActionError("GitHub issue draft does not exist")
    title, body = str(draft.get("title") or ""), str(draft.get("body") or "")
    labels = list(draft.get("labels") or [])
    if draft.get("status") == "published" and draft.get("url"):
        return {"type": "github_issue", "id": key, "url": draft["url"], "reused": True,
                "pending_review": False}
    if _PUBLICATION_SECRET.search(title + "\n" + body):
        raise L3ActionError("GitHub issue publication refused because the draft may contain a secret")
    marker = f"<!-- altitude-l3-issue:{key} -->"
    listed = subprocess.run(["gh", "issue", "list", "--state", "all", "--limit", "100", "--json", "url,body"],
                            cwd=str(config.project_path(project)), capture_output=True, text=True,
                            timeout=120, env=engines.clean_env())
    if listed.returncode != 0:
        raise L3ActionError(f"GitHub issue lookup failed: {(listed.stderr or listed.stdout).strip()[-300:]}")
    try:
        existing = next((row for row in json.loads(listed.stdout or "[]")
                         if marker in str(row.get("body") or "")), None)
    except (ValueError, TypeError) as exc:
        raise L3ActionError(f"GitHub issue lookup returned invalid JSON: {exc}") from exc
    if existing:
        url, reused = existing.get("url"), True
    else:
        cmd = ["gh", "issue", "create", "--title", title, "--body", f"{body.rstrip()}\n\n{marker}"]
        for label in labels:
            cmd += ["--label", label]
        run = subprocess.run(cmd, cwd=str(config.project_path(project)), capture_output=True, text=True,
                             timeout=120, env=engines.clean_env())
        if run.returncode != 0:
            raise L3ActionError(f"GitHub issue create failed: {(run.stderr or run.stdout).strip()[-300:]}")
        url, reused = (run.stdout or "").strip(), False
    with S.project_lock(project):
        current = S.read_json(path, None)
        if not isinstance(current, dict) or current.get("status") not in ("pending_review", "published"):
            raise L3ActionError("GitHub issue draft changed during publication")
        current.update({"status": "published", "published": S.now(), "url": url})
        S.write_json(path, current)
    return {"type": "github_issue", "id": key, "url": url, "reused": reused, "pending_review": False}


def _execute(project: str, action: dict, *, github_issue_source: str | None = None) -> dict:
    kind = action["type"]
    slug = str(action.get("slug") or "").strip()
    if kind == "new_task":
        title = _need(action.get("title"), "title")
        request = _new_task_request(action)
        task = T.new(project, title, request,
                     actor="l3", source=action.get("source") or "chat", engine=action.get("engine"),
                     model=action.get("model"), paths=action.get("paths") or None,
                     hold_merge=action.get("hold_merge"))
        return {"type": kind, "slug": task["slug"]}
    if kind == "task_done":
        task = S.load_task(project, _need(slug, "slug"))
        if task.get("state") != "reported" or _worker_live(project, task):
            raise L3ActionError(f"{slug}: L3 may complete only a reported task with no live L2 worker")
        done = T.done(project, slug, actor="l3", digest=str(action.get("digest") or ""),
                      expected_state="reported", expected_dispatch_id=task.get("dispatch_id"),
                      expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        return {"type": kind, "slug": slug, "state": done["state"]}
    if kind == "task_block":
        task = S.load_task(project, _need(slug, "slug"))
        if task.get("state") not in ("running", "reported") or _worker_live(project, task):
            raise L3ActionError(f"{slug}: L3 cannot block a live or non-running/non-reported task")
        blocked = T.block(project, slug, _need(action.get("reason"), "reason"), actor="l3",
                          expected_state=task.get("state"), expected_dispatch_id=task.get("dispatch_id"),
                          expected_session_id=task.get("session_id"), expected_agent_id=task.get("agent_id"))
        return {"type": kind, "slug": slug, "state": blocked["state"]}
    if kind == "task_resume":
        task = S.load_task(project, _need(slug, "slug"))
        if task.get("state") != "blocked" or not task.get("session_id") or not task.get("agent_id"):
            raise L3ActionError(f"{slug}: task has no exact blocked L2 session to resume")
        answer = str(action.get("answer") or action.get("reason") or "Continue from the durable task state.")
        paths = action.get("paths") or []
        persisted = _resume_paths(project, task, paths) if paths else None
        try:
            result = dispatch.resume_blocked(project, slug, answer, prefix="L3: ",
                                             expected_state="blocked",
                                             expected_dispatch_id=task.get("dispatch_id"),
                                             expected_session_id=task.get("session_id"),
                                             expected_agent_id=task.get("agent_id"))
        except Exception:
            if persisted is not None:
                declared_paths, previous_paths = persisted
                _rollback_resume_paths(project, task, declared_paths, previous_paths)
            raise
        return {"type": kind, "slug": slug, "deferred": bool(result.get("deferred"))}
    if kind == "task_fyi":
        T.fyi(project, _need(slug, "slug"), _need(action.get("text"), "text"), actor="l3")
        return {"type": kind, "slug": slug}
    if kind == "task_hold_merge":
        hold = bool(action.get("merge_hold"))
        T.set_hold_merge(project, _need(slug, "slug"),
                         _need(action.get("reason"), "reason") if hold else None, actor="l3")
        return {"type": kind, "slug": slug, "held": hold}
    if kind == "github_issue":
        return _github_issue_draft(project, action, github_issue_source)
    if kind == "github_issue_approve":
        return _github_issue_approve(project, action, github_issue_source)
    if kind == "incident_new":
        data = action.get("incident") if isinstance(action.get("incident"), dict) else {}
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
        incidents.amend_incident(project, iid, reason=_need(action.get("reason"), "reason"), actor="l3", **fields)
        return {"type": kind, "id": iid}
    if kind == "recovery_hold":
        recovery.hold(_need(action.get("reason"), "reason"), actor="l3")
        return {"type": kind}
    if kind == "recovery_clear":
        recovery.clear(_need(action.get("reason"), "reason"), actor="l3")
        return {"type": kind}
    raise L3ActionError(f"unknown L3 action {kind!r}")


def apply(project: str, structured: object, *, action_id: str,
          github_issue_source: str | None = None) -> list[dict]:
    action = _validate(structured)
    if action is None:
        return []
    prior = _claim(project, action_id, action)
    if prior is not None:
        return prior
    try:
        result = [_execute(project, action, github_issue_source=github_issue_source)]
    except L3ActionError as exc:
        _finish(project, action_id, action, error=str(exc))
        raise
    except (T.TransitionError, KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        _finish(project, action_id, action, error=str(exc))
        raise L3ActionError(str(exc)) from exc
    _finish(project, action_id, action, result=result)
    return result
