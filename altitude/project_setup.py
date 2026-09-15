"""Current project setup observations and bounded, daemon-owned repairs."""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config, engines, git_policy, l3, state as S, tasks as T


class SetupError(git_policy.GitPolicyError):
    pass


def path(project: str):
    return config.project_dir(project) / "setup.json"


def read(project: str) -> dict:
    return S.read_json(path(project), {}) or {}


def save(project: str, **updates) -> dict:
    with S.project_lock(project):
        record = {**read(project), **updates}
        S.write_json(path(project), record)
        return record


@contextmanager
def operation_lock(project: str):
    """A dead runner releases its lock; saved progress alone never proves it is running."""
    directory = config.project_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".setup.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def registered(project: str, *, start: bool = False) -> None:
    record = read(project)
    save(project, registered_at=record.get("registered_at") or S.now(),
         start_requested=record.get("start_requested", False) or start)
    request(project, "repair", actor="altd")


def parser() -> argparse.ArgumentParser:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(f"alt project setup: {message}")
    result = Parser(prog="alt project setup", allow_abbrev=False, add_help=False)
    result.add_argument("name")
    result.add_argument("--repair", action="store_true")
    result.add_argument("--reason")
    return result


def request(project: str, action: str, *, actor: str, expected: str | None = None,
            reason: str = "Project setup") -> dict:
    if actor not in (T.OPERATOR_MESSAGE_ROLE, "operator", "l3", "altd"):
        raise PermissionError("Project setup repair belongs to the operator or this project's L3.")
    if action not in ("check", "repair", "combine"):
        raise ValueError("Unknown project setup action")
    if action == "combine" and actor not in (T.OPERATOR_MESSAGE_ROLE, "operator"):
        raise PermissionError("Only the operator can choose how to integrate custom hooks.")
    if action == "combine" and (not isinstance(expected, str) or not expected):
        raise ValueError("Inspect the current hook integration before choosing it.")
    if action != "combine" and expected is not None:
        raise ValueError("A hook selection applies only to integration.")
    with config.project_activity(project) as attached, S.project_lock(project):
        if not attached or not config.is_managed(project):
            raise ValueError("Project is not managed.")
        record = read(project)
        operation = record.get("operation") or {}
        if operation.get("state") == "running":
            with operation_lock(project) as free:
                if free:
                    operation["state"] = "interrupted"
        if operation.get("state") in ("pending", "running"):
            if operation.get("action") != action or operation.get("expected") != expected:
                raise ValueError("Setup is already in progress. Check its result before another action.")
        else:
            record["operation"] = {"id": uuid.uuid4().hex, "action": action, "actor": actor,
                                   "reason": reason, "expected": expected, "state": "pending", "at": S.now()}
            S.write_json(path(project), record)
    return observe(project)


def _step(identity: str, label: str, status: str, detail: str, **fields) -> dict:
    return {"id": identity, "label": label, "status": status, "detail": detail, **fields}


def _repository(project: str) -> tuple[dict, bool]:
    repo = config.project_path(project)
    if not repo.is_dir():
        return _step("repository", "Git repository", "failed", "The registered folder is unavailable.", action="discuss"), False
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=10, env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        if "not a git repository" in result.stderr.lower():
            return _step("repository", "Git repository", "not_applicable",
                         "No Git repository. Conversation is available; Git tasks are unavailable."), False
        return _step("repository", "Git repository", "failed", result.stderr.strip()[:400], action="discuss"), False
    state = git_policy.inspect_repository(repo)
    if not state.determinate:
        return _step("repository", "Git repository", "input_needed", state.error or "Repository needs attention.", action="discuss"), True
    return _step("repository", "Git repository", "reused", "Detected an existing repository and origin/main. No repository was created."), True


def _coordinator(project: str, record: dict) -> dict:
    from . import server
    broker = server._l3_verb_brokers.get(project)
    connected = broker is not None and l3.verb_socket_path(project).exists()
    if not connected:
        return _step("coordinator", "Coordinator", "input_needed", "The coordinator command connection needs setup.", action="repair")
    inf = l3.info(project)
    intro = record.get("intro") or {}
    if l3.active(project):
        if intro.get("state") == "running" or not inf.get("turns"):
            return _step("coordinator", "Coordinator", "running", "The coordinator is preparing its first reply.")
    if intro.get("state") == "failed":
        return _step("coordinator", "Coordinator", "failed", intro.get("error") or "The first conversation failed.", action="repair")
    if intro.get("state") == "running":
        return _step("coordinator", "Coordinator", "unknown", "The first conversation was interrupted. Recheck and retry.", action="repair")
    if inf.get("turns") or any(r.get("role") == "assistant" for r in l3.chat_history(project)):
        return _step("coordinator", "Coordinator", "complete" if intro.get("state") == "complete" else "reused",
                     "First conversation ready." if intro.get("state") == "complete" else "Using the existing conversation and command connection.")
    if record.get("start_requested"):
        return _step("coordinator", "Coordinator", "pending", "Command connection ready; the first conversation is pending.")
    return _step("coordinator", "Coordinator", "complete", "Command connection ready. Open the conversation to speak with L3.")


def _guard_paths(project: str, slug: str | None = None) -> list[tuple[str, str, Path]]:
    """Only recorded task checkouts of this repository belong to project repair."""
    repo = config.project_path(project).resolve()
    paths = [("guards", "Git guards", repo)]
    common = git_policy._output(git_policy._run(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"),
                                "Cannot inspect the project's shared Git directory")
    for task in S.list_tasks(project):
        if slug is not None and task["slug"] != slug:
            continue
        if not task.get("worktree"):
            continue
        worktree = Path(task["worktree"])
        if not worktree.exists():
            continue  # Removed checkouts have no hook configuration to maintain.
        expected = repo / config.WORKTREE_ROOT / task["slug"]
        actual = git_policy._run(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if worktree.resolve() != expected.resolve() or actual.returncode or actual.stdout.strip() != common:
            raise SetupError(f"Task {task['slug']} has a checkout outside its registered repository; L3 needs to inspect it.")
        # Inherited healthy configuration is already represented by the project row.
        if git_policy._selection(worktree)[1] == "worktree" or git_policy.inspect_hooks(worktree)["status"] != "ready":
            paths.append((f"guards:{task['slug']}", f"Git guards · {task['slug']}", worktree))
    return paths


def observe(project: str) -> dict:
    config.project(project)
    record = read(project)
    repo = config.project_path(project)
    repository, has_git = _repository(project)
    rules = engines.repository_rules(repo)
    steps = [_step("folder", "Project folder", "complete" if record.get("registered_at") else "reused",
                   "Registered the selected folder." if record.get("registered_at") else "Using the registered folder."), repository,
             _step("instructions", "Project instructions", "reused" if rules else "not_applicable",
                   f"Using {rules.name}; its contents are unchanged." if rules else
                   "No project instructions. No file is created; discuss conventions with L3.", **({} if rules else {"action": "discuss"}))]
    try:
        guard_paths = _guard_paths(project) if has_git else []
    except git_policy.GitPolicyError as exc:
        guard_paths = []
        steps.append(_step("guards", "Git guards", "failed", str(exc), action="discuss"))
    for identity, label, checkout in guard_paths:
        guard = git_policy.inspect_hooks(checkout)
        status = {"ready": "reused", "missing": "input_needed", "stale": "input_needed", "conflict": "input_needed", "error": "failed"}[guard["status"]]
        receipt = (record.get("guards") if identity == "guards" else (record.get("worktree_guards") or {}).get(identity)) or {}
        detail = guard["detail"]
        if guard["status"] == "ready" and receipt.get("fingerprint") == guard.get("fingerprint"):
            status = "complete" if receipt.get("action") != "reused" else "reused"
            detail = {"installed": "Installed and verified Altitude's Git guards.", "updated": "Updated and verified Altitude's Git guards.",
                      "combined": "Both hook sets are configured and verified."}.get(receipt.get("action"), detail)
        action = ("combine" if guard.get("can_combine") else "discuss") if guard["status"] == "conflict" else "repair"
        steps.append(_step(identity, label, status, detail, fingerprint=guard.get("fingerprint"),
                           **({"custom_hooks": {"path": guard["original_hooks"],
                                                 "events": [name for name, _ in guard.get("original_contents", [])]}}
                              if guard.get("original_hooks") and guard["status"] == "conflict" else {}),
                           **({"action": action} if guard["status"] != "ready" else {})))
    if not has_git:
        steps.append(_step("guards", "Git guards", "not_applicable" if repository["status"] == "not_applicable" else "unknown",
                           "Requires a Git repository."))
    steps.append(_coordinator(project, record))
    operation = dict(record.get("operation") or {})
    if operation.get("state") == "running":
        with operation_lock(project) as free:
            if free:
                operation["state"] = "interrupted"
    if operation.get("state") in ("running", "pending", "interrupted", "failed"):
        target = next((s for s in steps if s["id"] == operation.get("step", "guards")), None)
        if target and operation.get("state") == "running":
            target.update(status="running", detail="Checking configuration…" if operation["action"] == "check" else "Applying setup and verifying the result…")
        elif target and operation.get("state") in ("failed", "interrupted") and target["status"] not in ("complete", "reused", "not_applicable"):
            target.update(status="failed" if operation["state"] == "failed" else "unknown",
                          detail=operation.get("error") or "Setup was interrupted. Recheck the observed result before retrying.", action="repair")
    statuses = {s["status"] for s in steps}
    status = ("checking" if operation.get("state") in ("pending", "running") or statuses & {"running", "pending"}
              else "attention" if statuses & {"input_needed", "failed", "unknown"}
              else "conversation_ready" if not has_git else "ready")
    return {"project": project, "status": status, "checked_at": S.now(), "steps": steps,
            "operation": operation or None, "source": str(config.SOURCE.name),
            "affected_tasks": [t["slug"] for t in S.list_tasks(project) if t.get("fault") == "project-setup"]}


def _fault(project: str, detail: str) -> None:
    from . import incidents
    incidents.system_fault("project-setup", f"{detail}\nL3 can inspect with `alt project setup {project}` and retry "
                           f"with `alt project setup {project} --repair --reason 'Repair project setup'`. "
                           "Custom-hook integration requires the operator's choice. Verify the result before resuming affected tasks.", project=project)


def run(project: str) -> None:
    from . import server
    with config.project_activity(project) as attached, config.restart_lock() as active, operation_lock(project) as acquired:
        if not attached or not active or not acquired or config.restart_in_progress() or not config.is_managed(project):
            return
        operation = read(project).get("operation") or {}
        if operation.get("state") not in ("pending", "running"):
            return
        operation.update(state="running", step="coordinator")
        save(project, operation=operation)
        try:
            server.ensure_l3_verb_broker(project)
            operation["step"] = "guards"
            save(project, operation=operation)
            _, has_git = _repository(project)
            if has_git and operation["action"] != "check":
                paths = _guard_paths(project)
                selected = next((p for _, _, p in paths if git_policy.inspect_hooks(p).get("fingerprint") == operation.get("expected")), None)
                if operation["action"] == "combine" and selected is None:
                    raise SetupError("Hook ownership changed; check Setup and review the current integration.")
                for identity, _, checkout in paths:
                    guard = git_policy.inspect_hooks(checkout)
                    if guard["status"] == "conflict" and checkout != selected:
                        continue
                    operation["step"] = identity
                    save(project, operation=operation)
                    # A completed write followed by interruption is verified and reused on retry.
                    result = git_policy.repair_hooks(checkout, combine=checkout == selected,
                                                    expected=operation.get("expected") if checkout == selected else None)
                    _save_guard(project, identity, result)
            operation.update(state="complete", finished_at=S.now())
            save(project, operation=operation)
            view = observe(project)
            problems = [s["detail"] for s in view["steps"] if s["status"] in ("failed", "input_needed") and s["id"] != "coordinator"]
            if problems:
                _fault(project, "; ".join(problems))
            if (read(project).get("start_requested") and (not l3.info(project).get("turns") or
                    (read(project).get("intro") or {}).get("state") in ("failed", "running"))
                    and not l3.active(project) and operation["action"] != "check"
                    and (operation["actor"] != "altd" or not read(project).get("intro"))):
                server.spawn(f"start:{project}", server.start_l3, project)
        except (git_policy.GitPolicyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            operation.update(state="failed", error=str(exc)[:500], finished_at=S.now())
            save(project, operation=operation)
            _fault(project, str(exc)[:500])
        S.project_log(project, "project-setup", actor=operation["actor"], action=operation["action"],
                      operation=operation["id"], outcome=operation["state"], reason=operation.get("reason"))


def maintain(project: str) -> None:
    """Recheck current requirements; retry failed writes only on an explicit repair request."""
    record = read(project)
    if (record.get("operation") or {}).get("state") in ("pending", "running"):
        run(project)
        return
    if (record.get("operation") or {}).get("state") == "failed":
        return
    view = observe(project)
    if any(s.get("action") == "repair" and (s["id"].startswith("guards") or
           s["id"] == "coordinator" and not record.get("intro")) for s in view["steps"]):
        request(project, "repair", actor="altd")
        run(project)


def _save_guard(project: str, identity: str, result: dict) -> None:
    if identity == "guards":
        save(project, guards=result)
    else:
        save(project, worktree_guards={**(read(project).get("worktree_guards") or {}), identity: result})


def ensure_guards(project: str, *, slug: str | None = None) -> None:
    """Launch preflight uses the same safe repair, independent of worker availability."""
    try:
        with operation_lock(project) as acquired:
            if not acquired:
                raise T.TransitionError("Project setup is in progress; launch waits for its result.")
            for identity, _, checkout in _guard_paths(project, slug):
                result = git_policy.repair_hooks(checkout)
                _save_guard(project, identity, result)
                git_policy.require_hooks_installed(checkout)
    except (git_policy.GitPolicyError, OSError) as exc:
        _fault(project, str(exc))
        raise SetupError(str(exc)) from exc


def block_task(project: str, slug: str, error: object, *, expected_block_id=None) -> None:
    from . import incidents, tasks as T
    incidents._block_faulting_task(project, slug, f"Project setup needs repair: {error}", "project-setup",
                                  T._UNSET if expected_block_id is None else expected_block_id)
