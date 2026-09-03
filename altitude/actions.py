"""Trusted post-turn actions for contained Codex L2 workers.

The model can edit ordinary files in its isolated worktree and return inert JSON. It cannot write Altitude state,
Git metadata, or the network. Only this control-plane module, after the worker's whole cgroup is empty, may turn
that JSON into state changes, helper launches, or PR publication. Codex works in turns: between them the task is
held for a moment and the next prompt waits in the task inbox, which the next tick's resume folds in.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, dispatch, engines, land, l1, state as S, tasks as T

MAX_HELPERS = 4
MAX_HELPER_RESULT_BYTES = engines.RAW_CAPTURE_CAP


class ActionError(RuntimeError):
    """An untrusted or stale action was refused before privileged side effects."""


def _validate_shape(action: object) -> dict:
    if not isinstance(action, dict):
        raise ActionError("Codex L2 ended without a schema-valid action")
    kind = action.get("action")
    if kind not in ("publish", "complete_no_code", "block", "request_helpers", "continue"):
        raise ActionError(f"unknown Codex L2 action {kind!r}")
    if kind == "publish" and not str(action.get("commit_message") or "").strip():
        raise ActionError("publish requires a commit message")
    if kind == "complete_no_code" and not str(action.get("digest") or "").strip():
        raise ActionError("complete_no_code requires a digest")
    if kind == "block" and not str(action.get("blocked_reason") or "").strip():
        raise ActionError("block requires an exact reason")
    if kind == "continue" and not str(action.get("continue_reason") or "").strip():
        raise ActionError("continue requires a reason")
    helpers = action.get("helpers") or []
    if kind == "request_helpers":
        if not isinstance(helpers, list) or not helpers or len(helpers) > MAX_HELPERS:
            raise ActionError(f"request_helpers requires 1-{MAX_HELPERS} helpers")
        for helper in helpers:
            if (not isinstance(helper, dict) or helper.get("role") not in ("implementer", "reviewer")
                    or not str(helper.get("brief") or "").strip()):
                raise ActionError("each helper requires a role and bounded brief")
            if helper.get("engine") not in (None, "claude", "codex"):
                raise ActionError(f"invalid helper engine {helper.get('engine')!r}")
            if not isinstance(helper.get("paths"), list):
                raise ActionError("helper paths must be an array")
    return action


def _require_contained_exit(project: str, task: dict) -> None:
    job_root = dispatch.l2_job_root(project, task["slug"])
    if not engines.codex_containment_empty(str(task.get("agent_id") or ""), job_root=job_root):
        raise ActionError(f"{task['slug']}: Codex worker containment is not empty")


def next_turn(project: str, task: dict, prompt: str) -> dict:
    """Hold the task between two Codex turns and leave the next prompt in its inbox for the tick to resume with."""
    T.block(project, task["slug"], "between Codex turns", actor="altd", expected_state="running",
            updates={"resume_after": S.now()})
    T.enqueue(project, task["slug"], prompt)
    return {"kind": "pending"}


def _report(action: dict, result: dict, task: dict) -> dict:
    outcome = action.get("outcome") if isinstance(action.get("outcome"), dict) else {}
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
        prs.append({"number": int(result["pr"]), "title": action.get("pr_title") or action["commit_message"].splitlines()[0],
                    "merged": bool(result.get("merged")), "merge_sha": None})
    blocked = ""
    if result.get("hold"):
        blocked = f"merge held for Burak: {result['hold']}"
    elif action.get("merge") and not result.get("merged"):
        blocked = f"publication incomplete: checks are {result.get('checks')}"
    return {
        "landed": {"prs": prs, "main_runs": runs,
                   "deploy": str(outcome.get("deploy") or "not-applicable")},
        "review": outcome.get("review") if isinstance(outcome.get("review"), list) else [],
        "blocked": blocked,
        "decisions": outcome.get("decisions") if isinstance(outcome.get("decisions"), list) else [],
        "fyi": outcome.get("fyi") if isinstance(outcome.get("fyi"), list) else [],
        "follow_ups": outcome.get("follow_ups") if isinstance(outcome.get("follow_ups"), list) else [],
        "deviations": outcome.get("deviations") if isinstance(outcome.get("deviations"), list) else [],
        "spend": outcome.get("spend") if isinstance(outcome.get("spend"), dict) else {},
    }


def _publish(project: str, task: dict, action: dict) -> dict:
    proj = config.project(project)
    test_cmd = str(proj.get("test_cmd") or land.DEFAULT_TEST_CMD)
    wants_merge = bool(action.get("merge"))
    # Keep every strict dispatch/resume gate outside the short interval after GitHub accepts a merge but before the
    # self-deploy checkout reaches origin/main. Settlement also runs when land returns a retry-class error: GitHub may
    # have accepted the exact merge even when a later observation reports that the pinned pair moved.
    with dispatch.publication_settlement(project):
        try:
            result = land.land(
                str(action["commit_message"]), project=project,
                pr_title=str(action.get("pr_title") or "").strip() or None,
                merge=wants_merge, wait=int(proj.get("land_wait") or 600),
                base="main", test_cmd=test_cmd, cwd=Path(task["worktree"]),
                authority={"actor": "l2", "attempt": task.get("attempt")},
            )
        finally:
            if wants_merge:
                dispatch.pull_after_done(project, task)
    report = _report(action, result, task)
    S.write_json(S.task_dir(project, task["slug"]) / "report.json", report)
    return {"kind": "report", "land": result, "report": report}


def _helpers(project: str, task: dict, action: dict) -> dict:
    records = []
    task_dir = S.task_dir(project, task["slug"])
    for index, helper in enumerate(action.get("helpers") or [], 1):
        brief = task_dir / f"helper-request-{task.get('attempt', 0)}-{index}.md"
        S.atomic_write(brief, str(helper["brief"]).rstrip() + "\n")
        name = f"helper-{task.get('attempt', 0)}-{index}"
        existing = l1.load(project, task["slug"], name)
        records.append(existing or l1.start(
            project, task["slug"], brief, role=helper["role"], engine=helper.get("engine"),
            model=helper.get("model"), paths=helper.get("paths") or None, name=name,
            expected_attempt=task.get("attempt"),
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
    return next_turn(project, task, prompt)


def process_l2(project: str, item: dict) -> dict:
    """Consume one settled Codex action; returns a small instruction to the server workflow."""
    task = item["task"]
    if (task.get("l2_engine") or "claude") != "codex":
        raise ActionError("trusted action broker accepts only contained Codex L2 workers")
    action = _validate_shape(item.get("action") or (item.get("agent") or {}).get("action"))
    _require_contained_exit(project, task)
    if task.get("state") != "running":
        raise ActionError(f"{task['slug']}: Codex action arrived for a {task.get('state')} task")
    text = str(action.get("message") or "").strip()
    if text:
        T.message(project, task["slug"], "l2", text, by="l2")
    try:
        kind = action["action"]
        if kind == "publish":
            return _publish(project, task, action)
        if kind == "complete_no_code":
            with S.project_lock(project):
                live = S.load_task(project, task["slug"])
                if live.get("state") != "running" or live.get("agent_id") != task.get("agent_id"):
                    raise T.TransitionError(f"{task['slug']}: completion ownership changed")
                live["completion_requested"] = {"at": S.now(), "digest": action["digest"]}
                S.save_task(project, live)
            return {"kind": "done", "task": T.finalize_completion(project, task["slug"])}
        if kind == "block":
            blocked = T.block(project, task["slug"], str(action["blocked_reason"]), actor="l2", expected_state="running")
            return {"kind": "blocked", "task": blocked}
        if kind == "request_helpers":
            return _helpers(project, task, action)
        return next_turn(project, task, "Continue for this exact reason from your previous action: "
                         + str(action["continue_reason"]))
    except (land.LandError, T.TransitionError, OSError, ValueError) as exc:
        kind = str(action.get("action") or "unknown")
        S.append_event(project, task["slug"], "l2-action-refused", action=kind, reason=str(exc)[:300])
        try:
            return next_turn(project, S.load_task(project, task["slug"]),
                             f"Trusted {kind} action was refused without changing providers: {exc}")
        except T.TransitionError as hold_exc:
            raise ActionError(f"{kind} action and same-thread correction were fenced: {hold_exc}") from hold_exc
