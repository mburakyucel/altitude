"""Dispatch one task-owning L2 in its worktree, monitor it, and safely resume its session."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import json
import secrets
import subprocess
from datetime import datetime, timezone
import re
from pathlib import Path

from . import config, engines, git_policy, route, state as S, tasks as T


class DispatchFailure(T.TransitionError):
    """A launch fault that is already recorded as a system fault."""


def record_dispatch_failure(project: str, slug: str, error: object) -> DispatchFailure:
    """Clear the failed launch's transient claim and record the fault; the fault blocks the task."""
    reason = str(error)[:300]
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task.get("state") == "queued":
            task["dispatching"] = None
            S.save_task(project, task)
    S.append_event(project, slug, "dispatch-failed", reason=reason)
    from . import incidents
    incidents.system_fault("dispatch-failed", f"{project}/{slug}: {reason}", project=project, task=slug)
    return DispatchFailure(f"dispatch failed: {reason}")


def record_resume_failure(project: str, slug: str, previous: str, error: object) -> RuntimeError:
    """Record a failed replacement launch as a system fault; the fault blocks the task."""
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


JOBS_DIR = config.HOME / ".claude" / "jobs"   # the harness's background-job state, keyed by agent id


def l2_engine(task: dict) -> str:
    """Old task records predate provider identity and are necessarily Claude sessions."""
    return task.get("l2_engine") or "claude"


def l2_job_root(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / "l2-engine"


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


def worktree_branch(slug: str, worktree: str | Path | None = None, agent_id: str | None = None) -> str:
    """The branch `claude --bg -w <slug>` really checks out — `worktree-<slug>`, never the bare slug.

    The harness records the real name in `~/.claude/jobs/<agent_id>/state.json` (`worktreeBranch`); before an agent id
    exists the name is derived and confirmed against the worktree itself. Every failure degrades to the derived name:
    a wrong branch in the brief is bad, a dispatch that dies reading a state file is worse."""
    derived = f"worktree-{slug}"
    if agent_id:
        try:
            st = json.loads((JOBS_DIR / str(agent_id) / "state.json").read_text())
            b = (st.get("worktreeBranch") or "").strip() if isinstance(st, dict) else ""
            if b:
                return b
        except (OSError, ValueError, AttributeError):
            pass
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


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-dispatch settings: repository guardrails and passive edit telemetry."""
    hooks = config.HOOKS
    settings = {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"python3 {hooks / 'guard.py'}", "timeout": 10}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"python3 {hooks / 'edit_count.py'}", "timeout": 10}]}],
    }, "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key},
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW}
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


def run(project: str, slug: str, model: str | None = None) -> dict:
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
    repo = config.project_path(project)
    try:
        with publication_settlement(project):
            origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
    except git_policy.GitPolicyError as exc:
        # system_fault may acquire state locks, so it deliberately lives outside project_lock.
        from . import incidents
        incidents.system_fault("main-unpushed", f"{project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"dispatch refused by Git provenance gate: {exc}") from exc
    try:
        worktree_path = _task_worktree(repo, project, slug, origin_sha)
    except (git_policy.GitPolicyError, T.TransitionError) as exc:
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
        forced_engine = task.get("engine") or proj.get("l2_engine")
        if model in config.MODEL_ALIASES and not forced_engine:
            forced_engine = "claude"
        choice = route.pick_engine("l2", forced=forced_engine)
        if not choice.get("engine"):
            raise T.TransitionError(f"engine hold: {choice['why']}")
        engine = choice["engine"]
        selected_model = (model or task.get("model") or proj.get("l2_model") or config.MODELS["l2"]
                          if engine == "claude" else model or task.get("model") or proj.get("l2_codex_model"))
        task.update({"dispatching": S.now(), "l2_engine": engine, "engine_model": selected_model,
                     "routing": choice["why"]})
        S.save_task(project, task)
    attempt = task.get("attempt", 0) + 1
    dispatch_id = f"{slug}-{attempt}"
    name = f"{project}/{dispatch_id}"
    l2_token = secrets.token_urlsafe(24)
    agent = {}
    try:
        brief_md = build_brief(project, slug)
        T.brief(project, slug, brief_md, actor="altd")
        settings = session_settings(project, slug, f"{project}--{dispatch_id}")
        persona = config.PERSONAS / ("l2_codex.md" if engine == "codex" else "l2.md")
        res = engines.start_l2(
            engine, name, brief_md, cwd=worktree_path, persona=persona, model=selected_model, settings=settings,
            extra_env=l2_env(project, {"slug": slug, "dispatch_id": dispatch_id, "l2_token": l2_token}),
            job_root=l2_job_root(project, slug))
    except Exception as exc:
        raise record_dispatch_failure(project, slug, exc) from exc
    agent = res.get("agent") or {}
    try:
        if res.get("returncode") != 0:
            raise RuntimeError(f"{engine} L2 launch failed: {res.get('stderr', '')[:300] or res.get('stdout', '')[:300]}")
        if not agent.get("id") or not agent.get("sessionId"):
            raise RuntimeError(f"{engine} L2 returned without a concrete worker id and session id")
        worktree = str(worktree_path)
        T.dispatch(project, slug, dispatch_id=dispatch_id, session_id=agent["sessionId"], agent_id=agent["id"],
                   worktree=worktree, branch=worktree_branch(slug, worktree, agent["id"]), l2_token=l2_token,
                   l2_engine=engine, engine_model=selected_model, routing=choice["why"])
    except T.TransitionError as exc:
        if agent.get("id"):
            try:
                engines.stop_l2_worker(engine, agent["id"], job_root=l2_job_root(project, slug))
            except Exception:  # noqa: BLE001 — preserve the launch fault; the incident records any orphaned worker
                pass
        try:
            current_state = S.load_task(project, slug).get("state")
        except (KeyError, OSError, ValueError):
            current_state = None
        if current_state != "queued":
            S.append_event(project, slug, "dispatch-cancelled", reason=str(exc)[:300])
            raise
        raise record_dispatch_failure(project, slug, exc) from exc
    except Exception as exc:
        if agent.get("id"):
            try:
                engines.stop_l2_worker(engine, agent["id"], job_root=l2_job_root(project, slug))
            except Exception:  # noqa: BLE001 — preserve the launch fault; the incident records any orphaned worker
                pass
        raise record_dispatch_failure(project, slug, exc) from exc
    return {"dispatch_id": dispatch_id, "engine": engine, "routing": choice["why"],
            "agent": agent, "stdout": res.get("stdout", "")}


def l2_env(project: str, task: dict) -> dict:
    """The ownership identity every fresh or resumed L2 session needs."""
    return {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": task["slug"],
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_SESSION_KEY": f"{project}--{task['dispatch_id']}",
            "ALTITUDE_DISPATCH_ID": str(task["dispatch_id"]),
            "ALTITUDE_L2_TOKEN": str(task["l2_token"]),
            # Codex strips TOKEN-named values from model subprocesses. This alias
            # retains only the current task attempt's scoped capability.
            "ALTITUDE_L2_CAPABILITY": str(task["l2_token"])}


@contextmanager
def _resume_lock(project: str, slug: str):
    """Serialize replacements of one L2 across the server and human-run CLI processes."""
    path = S.task_dir(project, slug) / ".resume.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _require_resume_snapshot(task: dict, slug: str, *, expected_dispatch_id: str | None = None,
                             expected_session_id: str | None = None,
                             expected_agent_id: str | None = None,
                             expected_state: str | None = None) -> None:
    if task.get("state") not in ("running", "blocked"):
        raise T.TransitionError(f"{slug}: L2 can be resumed only while running or blocked (state {task.get('state')})")
    checks = (
        ("dispatch", expected_dispatch_id, task.get("dispatch_id")),
        ("session", expected_session_id, task.get("session_id")),
        ("agent", expected_agent_id, task.get("agent_id")),
        ("state", expected_state, task.get("state")),
    )
    for label, expected, current in checks:
        if expected is not None and current != expected:
            raise T.TransitionError(f"{slug}: L2 {label} changed before resume ({expected!r} → {current!r})")


def _resume_session_locked(project: str, slug: str, text: str, session_id: str | None = None, *,
                           expected_dispatch_id: str | None = None,
                           expected_session_id: str | None = None,
                           expected_agent_id: str | None = None,
                           expected_state: str | None = None) -> dict:
    """Stop the current physical worker, then resume its provider conversation with ``text``.

    The task dispatch and L2 capability token are logical ownership and stay stable. The physical worker changes on
    every turn. Claude may also return a replacement session id; Codex keeps its thread id. We never overlap two
    writers in one worktree, and a running task never silently crosses providers.
    """
    task = S.load_task(project, slug)
    _require_resume_snapshot(task, slug, expected_dispatch_id=expected_dispatch_id,
                             expected_session_id=expected_session_id,
                             expected_agent_id=expected_agent_id, expected_state=expected_state)
    if not task.get("l2_token"):
        # Compatibility for sessions dispatched before the worker capability existed. It becomes stable now.
        with S.project_lock(project):
            current = S.load_task(project, slug)
            _require_resume_snapshot(current, slug, expected_dispatch_id=task.get("dispatch_id"),
                                     expected_session_id=task.get("session_id"),
                                     expected_agent_id=task.get("agent_id"), expected_state=task.get("state"))
            current["l2_token"] = secrets.token_urlsafe(24)
            S.save_task(project, current)
            task = current
    sid = session_id or task.get("session_id")
    if not sid:
        raise T.TransitionError("no session to resume; dispatch again")
    if session_id is not None and task.get("session_id") != session_id:
        raise T.TransitionError(f"{slug}: requested session is no longer the current L2 session")
    cwd = Path(task.get("worktree") or "")
    if not task.get("worktree") or not cwd.is_dir():
        raise T.TransitionError(f"worktree missing for {slug} ({task.get('worktree')}); dispatch again")
    repo = config.project_path(project)
    try:
        with publication_settlement(project):
            origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
            # A resume is specifically how an agent continues uncommitted work, so dirt is allowed here; path,
            # branch, and every committed ancestor remain strict.
            _validate_task_worktree(repo, project, slug, cwd, origin_sha, require_clean=False)
    except (git_policy.GitPolicyError, T.TransitionError) as exc:
        from . import incidents
        incidents.system_fault("task-git-provenance", f"resume {project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"resume refused by Git provenance gate: {exc}") from exc
    # Provenance checks may take a network round trip. Re-read before spending an engine launch.
    current = S.load_task(project, slug)
    _require_resume_snapshot(current, slug, expected_dispatch_id=task.get("dispatch_id"),
                             expected_session_id=sid, expected_agent_id=task.get("agent_id"),
                             expected_state=task.get("state"))
    task = current
    name = f"{project}/{task['dispatch_id']}"
    engine = l2_engine(task)
    job_root = l2_job_root(project, slug)
    old_worker = task.get("agent_id")
    if old_worker:
        try:
            engines.stop_l2_worker(engine, old_worker, job_root=job_root)
            if engine == "claude":
                old = next((row for row in engines.claude_agents() if row.get("id") == old_worker), None)
                if old and old.get("state") not in ("failed", "done", "stopped") and old.get("status") != "exited":
                    raise RuntimeError(f"Claude worker {old_worker} is still live after stop")
        except Exception as exc:
            raise record_resume_failure(project, slug, sid, f"old worker could not be stopped: {exc}") from exc
    try:
        res = engines.resume_l2(
            engine, name, sid, text, cwd=cwd, persona=config.PERSONAS / "l2.md",
            model=task.get("engine_model"), settings=S.task_dir(project, slug) / "settings.json",
            extra_env=l2_env(project, task), job_root=job_root)
    except Exception as exc:
        raise record_resume_failure(project, slug, sid, exc) from exc
    try:
        if engine == "claude":
            live = [a for a in engines.claude_agents()
                    if a.get("name") == name and a.get("state") not in ("failed", "done", "stopped")
                    and a.get("sessionId") and a.get("id") and a.get("id") != old_worker]
        else:
            row = res.get("agent") or {}
            live = [row] if row.get("id") and row.get("sessionId") and row.get("state") == "working" else []
    except Exception as exc:
        raise record_resume_failure(project, slug, sid, exc) from exc
    if res.get("returncode") != 0 or not live:
        for row in live:
            try:
                engines.stop_l2_worker(engine, row["id"], job_root=job_root)
            except Exception:  # noqa: BLE001 — the launch failure is recorded below
                pass
        note = res.get("stderr", "")[:200] or res.get("stdout", "")[:200]
        if res.get("returncode") != 0:
            detail = note or f"resume launcher exited {res.get('returncode')}"
        else:
            detail = "no concrete live worker"
            if note:
                detail += f" ({note})"
        raise record_resume_failure(project, slug, sid, detail)
    new = max(live, key=lambda a: a.get("startedAt") or 0)
    changed = None
    try:
        with S.project_lock(project):
            t = S.load_task(project, slug)
            try:
                _require_resume_snapshot(t, slug, expected_dispatch_id=task.get("dispatch_id"),
                                         expected_session_id=sid, expected_agent_id=task.get("agent_id"),
                                         expected_state=task.get("state"))
            except T.TransitionError as exc:
                changed = exc
            else:
                t["agent_id"], t["session_id"] = new["id"], new["sessionId"]
                t.pop("completion_requested", None)
                S.save_task(project, t)
    except Exception as exc:  # noqa: BLE001 — a launched worker without a durable owner must be stopped and held
        try:
            engines.stop_l2_worker(engine, new["id"], job_root=job_root)
        except Exception:  # noqa: BLE001 — the incident records any worker the stop could not reach
            pass
        raise record_resume_failure(project, slug, sid, f"could not bind replacement worker: {exc}") from exc
    if changed is not None:
        engines.stop_l2_worker(engine, new["id"], job_root=job_root)
        S.append_event(project, slug, "resume-cancelled", agent_id=new["id"], session_id=new["sessionId"],
                       reason=str(changed))
        raise T.TransitionError(f"{slug}: task generation changed during resume; replacement worker stopped") from changed
    S.append_event(project, slug, "resumed", engine=engine, agent_id=new.get("id"),
                   session_id=new.get("sessionId"), previous_session=sid, previous_worker=old_worker)
    res["agent"] = new
    return res


def resume_session(project: str, slug: str, text: str, session_id: str | None = None, **expected) -> dict:
    with _resume_lock(project, slug):
        return _resume_session_locked(project, slug, text, session_id, **expected)


def _resume_blocked_locked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ", *,
                           expected_dispatch_id: str | None = None,
                           expected_session_id: str | None = None,
                           expected_agent_id: str | None = None,
                           expected_state: str | None = None,
                           locked_resume: bool = True) -> dict:
    task = S.load_task(project, slug)
    _require_resume_snapshot(task, slug, expected_dispatch_id=expected_dispatch_id,
                             expected_session_id=expected_session_id,
                             expected_agent_id=expected_agent_id, expected_state=expected_state)
    if task["state"] == "blocked" and not task.get("agent_id"):
        T.requeue(project, slug, answer=answer)  # blocked before any launch: dispatch retries it from scratch
        return {"deferred": False, "requeued": True}
    if task["state"] == "blocked":
        provider_hold = engines.usage_hold() if l2_engine(task) == "claude" else None
        hold = (f"usage limit: subscription window exhausted, resets {provider_hold}"
                if provider_hold else wip_hold(project, task))
        if hold:
            waiting = (f"waiting for lease: {hold.removeprefix('file lease: ')}"
                       if hold.startswith("file lease: ") else f"waiting: {hold}")
            with S.project_lock(project):
                task = S.load_task(project, slug)
                _require_resume_snapshot(task, slug, expected_dispatch_id=expected_dispatch_id,
                                         expected_session_id=expected_session_id,
                                         expected_agent_id=expected_agent_id, expected_state=expected_state)
                if "blocked_question" not in task:
                    task["blocked_question"] = task.get("blocked_reason")
                task["resume_answer"] = answer
                task["resume_prefix"] = prefix
                task["resume_after"] = S.now()
                task["blocked_reason"] = waiting
                S.save_task(project, task)
                S.append_event(project, slug, "resume-deferred", hold=hold, reason=waiting)
            return {"deferred": True, "hold": hold, "waiting": waiting}
    prompt = f"{prefix}{answer}\nContinue from your progress file; finish to *done* and rewrite the report."
    expected = {key: value for key, value in {
        "expected_dispatch_id": expected_dispatch_id,
        "expected_session_id": expected_session_id,
        "expected_agent_id": expected_agent_id,
        "expected_state": expected_state,
    }.items() if value is not None}
    if locked_resume:
        res = _resume_session_locked(project, slug, prompt, **expected)
    else:
        # Keep the public resume seam used by callers and tests; it owns its own cross-process lock.
        res = resume_session(project, slug, prompt, **expected)
    new_agent = res.get("agent") or {}
    T.resume(project, slug, answer=answer, expected_state="blocked",
             expected_dispatch_id=task.get("dispatch_id"),
             expected_session_id=new_agent.get("sessionId") or task.get("session_id"),
             expected_agent_id=new_agent.get("id") or task.get("agent_id"))
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.pop("resume_after", None)
        task.pop("resume_answer", None)
        task.pop("resume_prefix", None)
        S.save_task(project, task)
    res["deferred"] = False
    return res


def resume_blocked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ", **expected) -> dict:
    return _resume_blocked_locked(project, slug, answer, prefix, locked_resume=False, **expected)


def message_l2(project: str, slug: str, text: str, *, expected_dispatch_id: str | None = None,
               expected_session_id: str | None = None, expected_engine: str | None = None) -> dict:
    """Persist Burak's message and deliver it only to the L2 attempt snapshot he addressed."""
    text = str(text or "").strip()
    if not text:
        raise T.TransitionError("task message is empty")
    with _resume_lock(project, slug):
        task = S.load_task(project, slug)
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
        expected = {
            "expected_dispatch_id": task["dispatch_id"],
            "expected_session_id": task["session_id"],
            "expected_agent_id": task.get("agent_id"),
            "expected_state": task["state"],
        }
        message = T.append_task_message(
            project, slug, "burak", text, actor="burak",
            expected_dispatch_id=task["dispatch_id"], expected_session_id=task["session_id"],
            expected_state=task["state"],
        )
        if task["state"] == "blocked":
            result = _resume_blocked_locked(project, slug, text, **expected)
        else:
            result = _resume_session_locked(project, slug, text, **expected)
        return {**result, "message": message}


def resume_due(project: str) -> list[str]:
    """Tasks blocked by an exhausted window come back by themselves once it reopens — oldest first, WIP-throttled."""
    now, back = S.now(), []
    due = [t for t in S.list_tasks(project) if t["state"] == "blocked" and t.get("resume_after") and t["resume_after"] <= now]
    for t in sorted(due, key=_resume_order):
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
        res = resume_blocked(project, t["slug"], answer, prefix=prefix)
        if res and res.get("deferred"):
            continue
        with S.project_lock(project):
            t2 = S.load_task(project, t["slug"])
            t2.pop("resume_after", None)
            t2.pop("resume_answer", None)
            t2.pop("resume_prefix", None)
            S.save_task(project, t2)
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


PER_TASK_HOLDS = ("file lease",)  # a lease holds one task; the queue behind it keeps moving


def per_task_hold(hold: str | None) -> bool:
    return bool(hold) and str(hold).startswith(PER_TASK_HOLDS)


def wip_hold(project: str, task: dict | None = None) -> str | None:
    running = [t for t in S.list_tasks(project) if t["state"] == "running"]
    proj = config.project(project)
    if task:
        mine = task_paths(project, task)
        mine_pending = task.get("state") == "blocked" and bool(task.get("resume_after"))
        holders = []
        for other in _lease_tasks(project, exclude=task["slug"]):
            pending_resume = other["state"] == "blocked"
            if pending_resume and mine_pending and _resume_order(other) >= _resume_order(task):
                continue  # among overlapping queued resumes, the deterministic oldest task proceeds first
            holders.append({"slug": other["slug"], "paths": task_paths(project, other),
                            "pending_resume": pending_resume})
        held = hold_conflict(mine, holders)
        if held:
            return held
    if len(running) >= int(proj.get("wip", config.WIP_PER_PROJECT)):
        return f"WIP limit: {len(running)} running in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p) if t["state"] == "running")
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
            a = engines.codex_worker(t.get("agent_id"), job_root=l2_job_root(project, t["slug"]))
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


def _l2_worker_live(project: str, task: dict) -> bool:
    engine, wt = l2_engine(task), task.get("worktree")
    if engine == "claude":
        return any(a.get("cwd") == wt and a.get("state") not in ("failed", "done", "stopped")
                   for a in engines.claude_agents())
    if engine == "codex" and task.get("agent_id"):
        row = engines.codex_worker(task["agent_id"], job_root=l2_job_root(project, task.get("slug") or ""))
        return bool(row and row.get("state") == "working")
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
        if _l2_worker_live(project, task):
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
