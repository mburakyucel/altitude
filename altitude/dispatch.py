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

from . import config, engines, git_policy, github_intake, recovery, route, state as S, tasks as T


class DispatchFailure(T.TransitionError):
    """A launch fault already persisted and routed through the global recovery fuse."""


def record_dispatch_failure(project: str, slug: str, error: object) -> DispatchFailure:
    """Leave a failed launch queued, clear its transient claim, and trip recovery once."""
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


JOBS_DIR = config.HOME / ".claude" / "jobs"   # the harness's background-job state, keyed by agent id


def l2_engine(task: dict) -> str:
    """Old task records predate provider identity and are necessarily Claude sessions."""
    return task.get("l2_engine") or "claude"


def provider_capability_hold(task: dict) -> str | None:
    """Return the closed provider hold without changing task or recovery state."""
    return T.owner_provider_capability_hold(task)


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


def build_brief(project: str, slug: str, issue_snapshot: dict | None = None) -> str:
    task = S.load_task(project, slug)
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
        # A provider already persisted on a queued legacy row is ownership
        # evidence, not a routing suggestion. Never rewrite it to an enabled
        # provider or perform intake/Git/worktree effects first.
        T.require_owner_provider_capability(task)
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
        proj = config.project(project)
        forced_engine = task.get("engine") or proj.get("l2_engine")
        if model in config.MODEL_ALIASES and not forced_engine:
            forced_engine = "claude"
        if forced_engine in config.ENGINES:
            try:
                engines.require_autonomous_engine(forced_engine)
            except engines.EngineCapabilityError as exc:
                raise T.TransitionError(f"engine hold: {exc}") from exc
    try:
        issue_snapshot = github_intake.ensure_snapshot(project, slug, expected_state="queued")
    except github_intake.IssueIntakeError as exc:
        raise T.TransitionError(f"GitHub issue intake held before launch: {exc}") from exc
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
        T.require_owner_provider_capability(task)
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
        try:
            engines.require_autonomous_engine(engine)
        except engines.EngineCapabilityError as exc:
            raise T.TransitionError(f"engine hold: {exc}") from exc
        selected_model = (model or task.get("model") or proj.get("l2_model") or config.MODELS["l2"]
                          if engine == "claude" else model or task.get("model") or proj.get("l2_codex_model"))
        task.update({"dispatching": S.now(), "l2_engine": engine, "engine_model": selected_model,
                     "routing": choice})
        S.save_task(project, task)
    attempt = task.get("attempt", 0) + 1
    dispatch_id = f"{slug}-{attempt}"
    name = f"{project}/{dispatch_id}"
    l2_token = secrets.token_urlsafe(24)
    agent = {}
    try:
        brief_md = build_brief(project, slug, issue_snapshot)
        T.brief(project, slug, brief_md, actor="altd")
        settings = session_settings(project, slug, f"{project}--{dispatch_id}")
        persona = config.PERSONAS / ("l2_codex.md" if engine == "codex" else "l2.md")
        res = engines.start_l2(
            engine, name, brief_md, cwd=worktree_path, persona=persona, model=selected_model, settings=settings,
            extra_env=l2_env(project, {"slug": slug, "dispatch_id": dispatch_id, "l2_token": l2_token}),
            job_root=l2_job_root(project, slug), spawn_guard=recovery.launch_permission(project, task))
    except recovery.LaunchHeld as exc:
        with S.project_lock(project):
            held_task = S.load_task(project, slug)
            held_task["dispatching"] = None
            S.save_task(project, held_task)
        S.append_event(project, slug, "dispatch-held", reason=str(exc))
        raise T.TransitionError(str(exc)) from exc
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
                   l2_engine=engine, engine_model=selected_model, routing=choice)
    except T.TransitionError as exc:
        if agent.get("id"):
            try:
                engines.stop_l2_worker(engine, agent["id"], job_root=l2_job_root(project, slug))
            except Exception:  # noqa: BLE001 — preserve the launch fault; recovery owns any orphaned worker
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
            except Exception:  # noqa: BLE001 — preserve the launch fault; recovery owns any orphaned worker
                pass
        raise record_dispatch_failure(project, slug, exc) from exc
    return {"dispatch_id": dispatch_id, "engine": engine, "routing": choice,
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
                             expected_state: str | None = None,
                             expected_outcome_id: str | None = None) -> None:
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
    outcome_ref = task.get("outcome_ref")
    if outcome_ref and (expected_outcome_id is None or outcome_ref.get("id") != expected_outcome_id
                        or outcome_ref.get("stage") not in ("message_posted", "effecting")):
        raise T.TransitionError(f"{slug}: a claimed worker outcome fences resume/steering effects")
    if expected_outcome_id is not None and not outcome_ref:
        raise T.TransitionError(f"{slug}: worker outcome claim disappeared before resume")


def _issue_resume_prompt(project: str, task: dict, prompt: str) -> tuple[str, str | None]:
    """Add missing legacy issue context once, before any current worker is stopped."""
    try:
        snapshot = github_intake.ensure_snapshot(
            project, task["slug"], expected_state=task.get("state"),
            expected_dispatch_id=task.get("dispatch_id"), expected_session_id=task.get("session_id"),
            expected_agent_id=task.get("agent_id"),
        )
    except github_intake.IssueIntakeError as exc:
        raise T.TransitionError(f"GitHub issue intake held before L2 resume: {exc}") from exc
    if not snapshot:
        return prompt, None
    digest = snapshot["content_sha256"]
    brief_path = S.task_dir(project, task["slug"]) / "brief.md"
    brief_has_context = brief_path.exists() and github_intake.marker(snapshot) in brief_path.read_text()
    already_delivered = task.get("github_issue_context_delivered") == digest
    if brief_has_context or already_delivered:
        return prompt, None
    if github_intake.marker(snapshot) not in prompt:
        prompt = github_intake.render(snapshot) + "\n\n" + prompt
    return prompt, digest


def _resume_session_locked(project: str, slug: str, text: str, session_id: str | None = None, *,
                           expected_dispatch_id: str | None = None,
                           expected_session_id: str | None = None,
                           expected_agent_id: str | None = None,
                           expected_state: str | None = None,
                           expected_outcome_id: str | None = None) -> dict:
    """Stop the current physical worker, then resume its provider conversation with ``text``.

    The task dispatch and L2 capability token are logical ownership and stay stable. The physical worker changes on
    every enabled Codex turn. Disabled legacy Claude work is held before provenance or stop. We never overlap two
    writers in one worktree, and a running task never silently crosses providers.
    """
    task = S.load_task(project, slug)
    _require_resume_snapshot(task, slug, expected_dispatch_id=expected_dispatch_id,
                             expected_session_id=expected_session_id,
                             expected_agent_id=expected_agent_id, expected_state=expected_state,
                             expected_outcome_id=expected_outcome_id)
    engine = l2_engine(task)
    if capability_hold := provider_capability_hold(task):
        raise T.TransitionError(capability_hold)
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
                             expected_state=task.get("state"), expected_outcome_id=expected_outcome_id)
    # A recovery fuse that was already active must leave the current worker attached. The launch guard below still
    # closes a later race at the spawn boundary, but checking after the potentially slow provenance work and before
    # stop prevents a known hold from needlessly stranding a healthy conversation.
    held = recovery.dispatch_hold(project, current)
    if held:
        S.append_event(project, slug, "resume-held", reason=held, previous=sid)
        raise T.TransitionError(held)
    task = current
    text, delivered_issue_digest = _issue_resume_prompt(project, task, text)
    name = f"{project}/{task['dispatch_id']}"
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
        held = recovery.dispatch_hold(project, task)
        if held:
            raise recovery.LaunchHeld(held)
        res = engines.resume_l2(
            engine, name, sid, text, cwd=cwd, persona=config.PERSONAS / "l2.md",
            model=task.get("engine_model"), settings=S.task_dir(project, slug) / "settings.json",
            extra_env=l2_env(project, task), job_root=job_root,
            spawn_guard=recovery.launch_permission(project, task))
    except recovery.LaunchHeld as exc:
        try:
            _defer_stopped_resume(project, task, text, str(exc), previous=sid,
                                  expected_outcome_id=expected_outcome_id)
        except Exception as defer_exc:
            raise record_resume_failure(
                project, slug, sid, f"recovery hold appeared after worker stop and pending resume could not persist: {defer_exc}"
            ) from defer_exc
        raise T.TransitionError(str(exc)) from exc
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
            except Exception:  # noqa: BLE001 — the recovery fuse records the launch failure below
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
                                         expected_state=task.get("state"),
                                         expected_outcome_id=expected_outcome_id)
            except T.TransitionError as exc:
                changed = exc
            else:
                t["agent_id"], t["session_id"] = new["id"], new["sessionId"]
                if delivered_issue_digest:
                    t["github_issue_context_delivered"] = delivered_issue_digest
                t.pop("completion_requested", None)
                S.save_task(project, t)
    except Exception as exc:  # noqa: BLE001 — a launched worker without a durable owner must be stopped and held
        try:
            engines.stop_l2_worker(engine, new["id"], job_root=job_root)
        except Exception:  # noqa: BLE001 — recovery owns any worker the stop command could not reach
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


def _defer_stopped_resume(project: str, snapshot: dict, prompt: str, hold: str, *, previous: str,
                          expected_outcome_id: str | None = None) -> None:
    """Atomically turn a post-stop recovery race into an exact pending resume."""
    reason = f"waiting: {hold}"
    with S.project_lock(project):
        task = S.load_task(project, snapshot["slug"])
        _require_resume_snapshot(
            task, snapshot["slug"], expected_dispatch_id=snapshot.get("dispatch_id"),
            expected_session_id=snapshot.get("session_id"), expected_agent_id=snapshot.get("agent_id"),
            expected_state=snapshot.get("state"), expected_outcome_id=expected_outcome_id,
        )
        task["resume_after"] = S.now()
        task["resume_answer"] = prompt
        task["resume_prefix"] = ""
        task["resume_exact_prompt"] = True
        task["blocked_reason"] = reason
        if task["state"] == "running":
            T._move(project, task, "blocked", "altd", reason=reason)  # noqa: SLF001 — atomic state + resume payload
        else:
            S.save_task(project, task)
            S.regen_state_md(project)
        S.append_event(project, task["slug"], "resume-held", reason=hold, previous=previous,
                       pending_resume=True)


def resume_session(project: str, slug: str, text: str, session_id: str | None = None, **expected) -> dict:
    with _resume_lock(project, slug):
        return _resume_session_locked(project, slug, text, session_id, **expected)


def _resume_blocked_locked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ", *,
                           expected_dispatch_id: str | None = None,
                           expected_session_id: str | None = None,
                           expected_agent_id: str | None = None,
                           expected_state: str | None = None, expected_outcome_id: str | None = None,
                           locked_resume: bool = True) -> dict:
    task = S.load_task(project, slug)
    _require_resume_snapshot(task, slug, expected_dispatch_id=expected_dispatch_id,
                             expected_session_id=expected_session_id,
                             expected_agent_id=expected_agent_id, expected_state=expected_state,
                             expected_outcome_id=expected_outcome_id)
    if capability_hold := provider_capability_hold(task):
        return {"deferred": True, "hold": capability_hold, "waiting": capability_hold}
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
                                         expected_agent_id=expected_agent_id, expected_state=expected_state,
                                         expected_outcome_id=expected_outcome_id)
                if "blocked_question" not in task:
                    task["blocked_question"] = task.get("blocked_reason")
                task["resume_answer"] = answer
                task["resume_prefix"] = prefix
                task["resume_after"] = S.now()
                task["blocked_reason"] = waiting
                S.save_task(project, task)
                S.append_event(project, slug, "resume-deferred", hold=hold, reason=waiting)
            return {"deferred": True, "hold": hold, "waiting": waiting}
    prompt = (answer if task.get("resume_exact_prompt")
              else f"{prefix}{answer}\nContinue from your progress file; finish to *done* and rewrite the report.")
    expected = {key: value for key, value in {
        "expected_dispatch_id": expected_dispatch_id,
        "expected_session_id": expected_session_id,
        "expected_agent_id": expected_agent_id,
        "expected_state": expected_state,
        "expected_outcome_id": expected_outcome_id,
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
        task.pop("resume_exact_prompt", None)
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
        if capability_hold := provider_capability_hold(task):
            raise T.TransitionError(capability_hold)
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
        res = resume_blocked(project, t["slug"], answer, prefix=prefix)
        if res and res.get("deferred"):
            continue
        with S.project_lock(project):
            t2 = S.load_task(project, t["slug"])
            t2.pop("resume_after", None)
            t2.pop("resume_answer", None)
            t2.pop("resume_prefix", None)
            t2.pop("resume_exact_prompt", None)
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


def wip_hold(project: str, task: dict | None = None) -> str | None:
    held = recovery.dispatch_hold(project, task)
    if held:
        return held
    project_tasks = S.list_tasks(project)
    # Disabled-provider legacy rows remain protected by their repository lease,
    # but do not consume the scarce runnable-provider WIP capacity.
    running = [t for t in project_tasks
               if t["state"] == "running" and not provider_capability_hold(t)]
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
                if t["state"] == "running" and not provider_capability_hold(t))
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
        codex_row = engines.codex_worker(task["agent_id"], job_root=l2_job_root(project, slug))
        if codex_row and codex_row.get("state") == "working":
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
        removed_l2_worker = candidate["kind"] == "L2" and bool(task.get("agent_id"))
        if removed_l2_worker:
            try:
                engine = l2_engine(task)
                rm_note = engines.remove_l2_worker(
                    engine, task["agent_id"], job_root=l2_job_root(project, slug))
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
