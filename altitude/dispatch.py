"""Dispatch an L2 as `claude --bg` in a worktree; poll `claude agents`; notice done (ARCHITECTURE §5)."""
from __future__ import annotations
import json
import os
import secrets
import signal
import shlex
import stat
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
import re
from pathlib import Path

from . import config, engines, git_policy, route, rules, state as S, tasks as T
from .alt_broker import AltBroker, BrokerDenied, l2_policy


def project_never_list(repo: Path) -> str:
    """Best effort: the 'Never' bullets from the repo's CLAUDE.md, else a generic line."""
    md = repo / "CLAUDE.md"
    if md.exists():
        lines = [l.strip("- ").strip() for l in md.read_text().splitlines() if re.match(r"^\s*-\s*\*\*?never", l, re.I) or "never" in l.lower()[:40]]
        if lines:
            return "; ".join(l[:160] for l in lines[:8])
    return "no changes outside the brief; no weakened guardrails; high-impact classes: open the PR and stop"


JOBS_DIR = Path(os.environ.get("ALTITUDE_JOBS_DIR") or (config.HOME / ".claude" / "jobs"))


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
        _validate_task_worktree(repo, project, slug, worktree, origin_sha, require_clean=True)
        return worktree

    _validate_task_worktree(repo, project, slug, worktree, origin_sha, require_clean=True)
    return worktree


def _git_text(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    value = (result.stdout or "").strip()
    if result.returncode != 0 or not value:
        detail = (result.stderr or result.stdout or "git returned no value").strip()[:300]
        raise T.TransitionError(f"Git identity check failed for {cwd}: {detail}")
    return value


def _linked_worktree_gitdir(repo: Path, worktree: Path, expected_branch: str) -> Path:
    """Prove the model-writable `.git` pointer still names this registered linked worktree."""
    pointer = worktree / ".git"
    try:
        info = pointer.lstat()
        raw = pointer.read_text()
    except OSError as exc:
        raise T.TransitionError(f"task worktree Git pointer is unavailable: {exc}") from exc
    lines = raw.splitlines()
    if (not stat.S_ISREG(info.st_mode) or pointer.is_symlink() or info.st_size > 4096
            or len(lines) != 1 or not lines[0].startswith("gitdir: ")):
        raise T.TransitionError("task worktree .git must be the exact bounded, non-symlink Git pointer")
    common_raw = _git_text(repo, "rev-parse", "--git-common-dir")
    common = (Path(common_raw) if Path(common_raw).is_absolute() else repo / common_raw).resolve(strict=True)
    target_raw = lines[0][len("gitdir: "):].strip()
    target = (Path(target_raw) if Path(target_raw).is_absolute() else worktree / target_raw).resolve(strict=True)
    if target.parent.resolve() != (common / "worktrees").resolve() or not target.is_dir():
        raise T.TransitionError("task worktree .git pointer escaped the authoritative common Git directory")
    backpointer = target / "gitdir"
    try:
        back_info = backpointer.lstat()
        back_raw = backpointer.read_text().strip()
        back = Path(back_raw).resolve(strict=True)
    except OSError as exc:
        raise T.TransitionError(f"authoritative worktree backpointer is unavailable: {exc}") from exc
    if not stat.S_ISREG(back_info.st_mode) or backpointer.is_symlink() or back != pointer.resolve(strict=True):
        raise T.TransitionError("authoritative worktree backpointer does not match the task checkout")
    registered = None
    for block in _git_text(repo, "worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        value = fields.get("worktree")
        if value and Path(value).resolve() == worktree.resolve():
            registered = fields
            break
    if registered is None or registered.get("branch") != f"refs/heads/{expected_branch}":
        raise T.TransitionError("task checkout is not the authoritative registered worktree/branch")
    expected_remote = _git_text(repo, "remote", "get-url", "origin")
    actual_remote = _git_text(worktree, "remote", "get-url", "origin")
    push_urls = _git_text(worktree, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if actual_remote != expected_remote or push_urls != [expected_remote]:
        raise T.TransitionError("task checkout origin fetch/push URL differs from the authoritative repository")
    return target


def _validate_task_worktree(repo: Path, project: str, slug: str, worktree: Path, origin_sha: str,
                            *, require_clean: bool) -> Path:
    """Validate an already-created L2 checkout before either a fresh launch or a resume."""
    import subprocess

    expected_path = (repo / ".claude" / "worktrees" / slug).resolve()
    if worktree.resolve() != expected_path or not worktree.is_dir():
        raise T.TransitionError(f"task worktree for {project}/{slug} must be {expected_path}, got {worktree}")
    expected_branch = f"worktree-{slug}"
    git_dir = _linked_worktree_gitdir(repo, worktree, expected_branch)
    actual = _git_branch(worktree)
    if actual != expected_branch:
        raise T.TransitionError(
            f"task worktree {worktree} is on {actual or 'detached HEAD'}, expected {expected_branch!r}"
        )
    ancestor = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", origin_sha, "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    if ancestor.returncode != 0:
        raise T.TransitionError(
            f"captured origin/main base {origin_sha[:12]} is not an ancestor of "
            f"{expected_branch!r}; the immutable dispatch provenance cannot be established"
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
    return git_dir


def build_brief(project: str, slug: str, l2_engine: str = "claude") -> str:
    task = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    proj = config.project(project)
    proposal = (d / "proposal.md").read_text() if (d / "proposal.md").exists() else (d / "request.md").read_text()
    env = task["envelope"]
    dec = task.get("decision") or {}
    approval_note = f" — Burak chose: {dec['chosen']}" if dec.get("chosen") else ""
    if dec.get("chosen") and dec.get("detail"):  # decision 46: the card is short; the conditions behind it travel with the brief
        approval_note += f"\n\nBehind the card (the L3's reasoning and conditions — binding where they say so):\n{dec['detail']}"
    if dec.get("chosen") and dec.get("note"):  # decision 50: Burak's own words with the answer are binding too
        approval_note += f"\n\nBurak's note with that answer (binding): {dec['note']}"
    policy = proj.get("approval", "default")
    if task.get("hold_merge"):  # decision 48: the hold is the exception, and it says why
        merge_policy = f"**Held for Burak** — open the PR, get it review-clean and CI-green, and stop; Burak merges it himself. Why: {task['hold_merge']}"
    else:
        merge_policy = {"default": "Merge when the review is addressed and CI is green — every class, L included (decision 48). Only a brief marked *held* stops at the open PR.",
                        "open-pr-only": "Open PRs and stop; never merge.", "merge-all": "Merge when the review is addressed and CI is green."}.get(policy, policy)
    checkpoint_guidance = (
        "**Codex broker boundary:** Altitude state and Git metadata are outside your writable sandbox. "
        "Create `.altitude-checkpoints/progress.md`, `report.md`, and `report.json` in this worktree, then publish "
        f"each with `alt task checkpoint {slug} --name <name> --source .altitude-checkpoints/<name>`. "
        "Use only brokered `alt` commands for status, L1 launches, verification, commits, pushes, PRs, and merges; "
        "direct state writes and network access are unavailable."
        if l2_engine == "codex" else
        f"**Checkpoint files:** write `progress.md`, `report.md`, and `report.json` directly in `{d}`."
    )
    effective_leases = []
    for lease in leases(project, exclude=slug):
        paths = narrow(lease["paths"])
        if paths:
            effective_leases.append({"slug": lease["slug"], "paths": paths})
    text = (config.TEMPLATES / "brief.md").read_text().format(
        slug=slug, cls=task["class"], project=project, title=task["title"], report_schema=config.SCHEMAS / "report.json",
        model=("Codex (configured model)" if l2_engine == "codex" else task.get("model") or config.MODELS["l2"]),
        engine_line=(f"every L1 engine is forced to **{task['engine']}** for this task." if task.get("engine") else "each L1 engine is Altitude's choice."),
        leases=("; ".join(f"`{l['slug']}` on {', '.join(l['paths'])}" for l in effective_leases) or "none"),
        paths=", ".join(task_paths(project, task)) or "(not declared — stay inside the proposal's file list)",
        task_dir=d, merge_policy=merge_policy, never_list=project_never_list(config.project_path(project)),
        l1_in_flight=env["l1_in_flight"], subagent_launches=env["subagent_launches"], max_turns=env["max_turns"],
        verification=env.get("verification", "reviewer"), approval_note=approval_note, repo=config.project_path(project),
        checkpoint_guidance=checkpoint_guidance,
        branch=worktree_branch(slug, config.project_path(project) / ".claude" / "worktrees" / slug),
        proposal=proposal, **{"class": task["class"]})
    stack = rules.compile_section(rules.stack_rules(proj.get("stacks", [])), "Stack rules")
    return text + ("\n" + stack if stack else "")


def session_settings(project: str, slug: str, session_key: str) -> Path:
    """Per-dispatch settings passed with --settings: hooks that enforce the envelope, nothing global."""
    hooks = config.HOOKS
    settings = {"hooks": {
        "PreToolUse": [{"matcher": "Agent|Task|Bash", "hooks": [{"type": "command", "command": f"python3 {hooks / 'subagent_cap.py'}", "timeout": 10}]},
                       {"matcher": "Bash", "hooks": [{"type": "command", "command": f"python3 {hooks / 'guard.py'}", "timeout": 10}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"python3 {hooks / 'edit_count.py'}", "timeout": 10}]}],
    }, "env": {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l2",
               "ALTITUDE_SESSION_KEY": session_key},
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW}  # decision 49: the 300k umbrella, also for resumed sessions
    p = S.task_dir(project, slug) / "settings.json"
    S.write_json(p, settings)
    return p


CODEX_RUN_FILE = "codex-l2.json"
CODEX_GATE_FD_ENV = "ALTITUDE_CODEX_L2_GATE_FD"
RESUME_STATUS_NOTE = (
    "[altitude] Resume status: run `alt task status` before continuing. Its current repository gate, `hold`, "
    "`other_leases`, and WIP fields are authoritative and override the point-in-time brief. Stop if the repository "
    "provenance gate is not healthy. Never commit, merge, reset, or push main/master directly. A bare top-level "
    "`tests/`, `docs/`, or similar staging path is not a hold lease; only the current narrowed hold is blocking."
)


def codex_run_path(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / CODEX_RUN_FILE


def codex_run(project: str, slug: str) -> dict:
    return S.read_json(codex_run_path(project, slug), {}) or {}


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def task_l2_engine(project: str, task: dict) -> str:
    """Return the persisted engine, adopting only an exact durable Codex generation."""
    engine = task.get("l2_engine")
    if engine in config.ENGINES:
        return engine
    record = codex_run(project, task.get("slug") or "")
    if record.get("dispatch_id") and record.get("dispatch_id") == task.get("dispatch_id"):
        return "codex"
    return "claude"


def _task_origin_sha(project: str, task: dict, current_origin_sha: str) -> str:
    """Keep the dispatch base immutable; adopt it only from the exact durable generation."""
    if task.get("origin_sha"):
        return str(task["origin_sha"])
    record = codex_run(project, task.get("slug") or "")
    if (record.get("dispatch_id") == task.get("dispatch_id") and record.get("origin_sha")):
        return str(record["origin_sha"])
    return current_origin_sha


def _l2_choice(task: dict, project_config: dict) -> dict:
    """Project L2 pins beat quota routing; task.engine is an L1-only override."""
    if project_config.get("l2_engine"):
        return route.pick_engine("l2", forced=project_config["l2_engine"])
    return route.pick_engine("l2")


def _codex_extra_config(worktree: Path, project: str, slug: str) -> list[str]:
    """No state/Git roots and no network: privileged operations go through the broker."""
    guard = f"python3 {config.HOOKS / 'codex_guard.py'}"
    cap = f"python3 {config.HOOKS / 'codex_l2_cap.py'}"
    hooks = (f'{{type="command",command={json.dumps(guard)},timeout=10}},'
             f'{{type="command",command={json.dumps(cap)},timeout=30}}')
    return [
        "sandbox_workspace_write.network_access=false",
        "features.hooks=true",
        f'hooks.PreToolUse=[{{matcher=".*",hooks=[{hooks}]}}]',
    ]


def _codex_agent(record: dict, name: str, *, active: bool | None = None) -> dict:
    if active is None:
        active = record.get("state") in ("starting", "running")
    return {"id": record.get("agent_id"), "sessionId": record.get("session_id"), "name": name,
            "state": "working" if active else record.get("state"),
            "status": "busy" if active else "exited", "pid": record.get("pid"), "engine": "codex"}


def _update_codex_record(project: str, slug: str, dispatch_id: str, generation: str, **fields) -> dict | None:
    """Update only the exact live generation; a stopped worker cannot resurrect itself."""
    with S.project_lock(project):
        current = codex_run(project, slug)
        if (current.get("dispatch_id") != dispatch_id or current.get("generation") != generation
                or current.get("state") in ("stopping", "stopped", "stop-failed")):
            return None
        current.update(fields)
        S.write_json(codex_run_path(project, slug), current)
        return current


def _proc_start_time(pid: int | None) -> str | None:
    try:
        raw = Path(f"/proc/{int(pid or 0)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, TypeError, ValueError, IndexError):
        return None


def _process_evidence(pid: int | None, expected_start: str | None) -> str:
    """absent/mismatch are safe; exact/unverified mean this generation may still execute."""
    if not pid:
        return "absent"
    observed = _proc_start_time(pid)
    if observed is None:
        return "absent"
    if expected_start is None:
        return "unverified"
    return "exact" if observed == expected_start else "mismatch"


def codex_processes_live(record: dict) -> bool:
    members = _codex_group_members(record)
    if members is None:
        return bool(record.get("pgid"))  # an unprovable recorded group fails closed
    return (bool(members)
            or any(_process_evidence(record.get(pid), record.get(start)) in ("exact", "unverified")
                   for pid, start in (("pid", "pid_start"), ("engine_pid", "engine_pid_start"))))


def _codex_group_members(record: dict) -> dict[int, str] | None:
    """Return the exact live members of the persisted wrapper group.

    ``None`` means ownership could not be proved (unreadable /proc or a reused
    numeric group leader). Callers must fail closed and must not signal such a
    group. A dead leader with remaining members is still the original process
    group and remains owned; this is the crash window PID-only checks miss.
    """
    try:
        pgid = int(record.get("pgid") or 0)
    except (TypeError, ValueError):
        return None if record.get("pgid") else {}
    if pgid <= 1:
        return None if record.get("pgid") else {}
    try:
        members = engines.process_group_members(pgid)
    except OSError:
        return None
    leader_start = members.get(pgid)
    expected_start = record.get("pid_start")
    if leader_start is not None and (not expected_start or leader_start != str(expected_start)):
        return None
    return members


def _kill_codex_group(record: dict, *, grace: float = 1.0) -> bool:
    """Stop and prove empty the exact persisted L2 process group."""
    identities = ((record.get("pid"), record.get("pid_start")),
                  (record.get("engine_pid"), record.get("engine_pid_start")))
    if any(pid and start is None and _proc_start_time(pid) is not None for pid, start in identities):
        return False
    members = _codex_group_members(record)
    if members is None:
        return False
    pgid = record.get("pgid")
    if members:
        try:
            pgid = int(pgid)
            if pgid == os.getpgrp():
                return False
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            members = _codex_group_members(record)
            if members != {}:
                return False
        else:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                members = _codex_group_members(record)
                if members == {}:
                    break
                if members is None:
                    return False
                time.sleep(0.025)
            if members:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    members = _codex_group_members(record)
                    if members == {}:
                        break
                    if members is None:
                        return False
                    time.sleep(0.025)
            if members != {}:
                return False
    # Compatibility for pre-PGID records or an engine historically launched
    # outside the wrapper group. Exact PID identity still fences it.
    for pid, started in identities:
        if not _kill_process_group(pid, started):
            return False
    return _codex_group_members(record) == {} and not any(
        _process_evidence(pid, started) in ("exact", "unverified") for pid, started in identities)


def _kill_process_group(pid: int | None, expected_start: str | None) -> bool:
    """Signal only the exact process identity; a live identity-less PID fails closed."""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return True
    if pid <= 1:
        return True
    current_start = _proc_start_time(pid)
    if current_start is None:
        return True
    if not expected_start or pid == os.getpid():
        return False
    if current_start != expected_start:
        return True
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        for _ in range(20):
            if _proc_start_time(pid) != expected_start:
                return True
            time.sleep(0.05)
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        for _ in range(20):
            if _proc_start_time(pid) != expected_start:
                return True
            time.sleep(0.05)
    except OSError:

        current_start = _proc_start_time(pid)
        return current_start is None or current_start != expected_start
    return False
def _cancel_new_child(child: subprocess.Popen, expected_start: str | None) -> bool:
    """Cancel a child this process just created, even before /proc identity is readable."""
    if expected_start and _kill_process_group(child.pid, expected_start):
        return True
    try:
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        child.wait(timeout=2)
        return True
    except ProcessLookupError:
        return True
    except (AttributeError, OSError, subprocess.SubprocessError):
        return False



def _mark_codex_launch_failed(project: str, slug: str, dispatch_id: str, generation: str, error: str) -> None:
    with S.project_lock(project):
        current = codex_run(project, slug)
        if (current.get("dispatch_id") == dispatch_id and current.get("generation") == generation
                and current.get("state") not in ("stopping", "stopped")):
            current.update({"state": "failed", "done": S.now(),
                            "result": {"error": error, "returncode": -1}})
            S.write_json(codex_run_path(project, slug), current)


def _codex_guard_env(worktree: Path, trusted_git_dir: Path) -> dict[str, str]:
    """The complete immutable Git identity consumed by the fail-closed guard."""
    return {"ALTITUDE_TASK_WORKTREE": str(worktree.resolve()),
            "ALTITUDE_REQUIRED_HOOKS": str(config.HOOKS.resolve()),
            "ALTITUDE_TRUSTED_GIT_DIR": str(trusted_git_dir.resolve())}


def _start_codex_worker(project: str, slug: str, dispatch_id: str, worktree: Path, prompt: str,
                        *, origin_sha: str, trusted_git_dir: Path, resume: str | None = None,
                        generation: str | None = None,
                        claim_field: str | None = None, resume_answer: str | None = None) -> dict:
    """Start a detached, pipe-gated wrapper and durably fence one generation."""
    with S.project_lock(project):
        if claim_field:
            task = S.load_task(project, slug)
            claim = task.get(claim_field) or {}
            expected_states = ("approved",) if claim_field == "pending_dispatch" else ("running", "blocked")
            if (task.get("state") not in expected_states or claim.get("dispatch_id") != dispatch_id
                    or claim.get("generation") != generation or claim.get("cancel_requested")):
                raise T.TransitionError("Codex L2 launch claim was cancelled before wrapper creation")
        previous = codex_run(project, slug)
        turn = int(previous.get("turn") or 0) + 1
        generation = generation or secrets.token_hex(12)
        prompt_path = S.task_dir(project, slug) / f"codex-l2-turn-{turn}-{generation}.md"
        S.atomic_write(prompt_path, prompt.rstrip() + "\n")
        record = {"project": project, "slug": slug, "dispatch_id": dispatch_id, "generation": generation,
                  "origin_sha": origin_sha, "worktree": str(worktree.resolve()),
                  "trusted_git_dir": str(trusted_git_dir.resolve()), "engine": "codex", "turn": turn,
                  "prompt": str(prompt_path), "resume": resume, "session_id": resume, "state": "starting",
                  "started": S.now(), "done": None, "pid": None, "pid_start": None, "pgid": None,
                  "engine_pid": None, "engine_pid_start": None, "agent_id": None, "result": None}
        S.write_json(codex_run_path(project, slug), record)
    log = open(S.task_dir(project, slug) / "codex-l2.log", "ab")
    with S.project_lock(project):
        current = codex_run(project, slug)
        if (current.get("dispatch_id") != dispatch_id or current.get("generation") != generation
                or current.get("state") != "starting"):
            log.close()
            raise T.TransitionError("Codex L2 generation was cancelled before wrapper launch")

    gate_read, gate_write = os.pipe()
    env = engines.clean_env()
    env.update(l2_env(project, {"slug": slug, "dispatch_id": dispatch_id}))
    env["ALTITUDE_CODEX_L2_GENERATION"] = generation
    env.update(_codex_guard_env(worktree, trusted_git_dir))
    env[CODEX_GATE_FD_ENV] = str(gate_read)
    try:
        child = subprocess.Popen(
            [sys.executable, str(config.REPO / "bin" / "alt"), "--project", project,
             "l2", "_exec", slug, dispatch_id],
            cwd=str(worktree), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env, pass_fds=(gate_read,),
        )
    except Exception as exc:
        os.close(gate_read); os.close(gate_write)
        _mark_codex_launch_failed(
            project, slug, dispatch_id, generation,
            f"Codex L2 wrapper could not start: {type(exc).__name__}: {exc}")
        raise
    finally:
        log.close()
    os.close(gate_read)
    child_start = _proc_start_time(child.pid)
    if not child_start:
        os.close(gate_write)
        stopped = _cancel_new_child(child, None)
        error = f"Codex L2 wrapper PID identity unavailable at launch (stopped={stopped})"
        _mark_codex_launch_failed(project, slug, dispatch_id, generation, error)
        raise T.TransitionError(error)
    cancelled = False
    activated_resume = False
    prior_blocked = {}
    with S.project_lock(project):
        current = codex_run(project, slug)
        claim_current = True
        if claim_field:
            task = S.load_task(project, slug)
            claim = task.get(claim_field) or {}
            expected_states = ("approved",) if claim_field == "pending_dispatch" else ("running", "blocked")
            claim_current = (task.get("state") in expected_states and claim.get("dispatch_id") == dispatch_id
                             and claim.get("generation") == generation and not claim.get("cancel_requested"))
        if (current.get("dispatch_id") != dispatch_id or current.get("generation") != generation
                or current.get("state") != "starting" or not claim_current):
            cancelled = True
        else:
            current.update({"pid": child.pid, "pid_start": child_start, "pgid": child.pid,
                            "agent_id": f"codex:{child.pid}", "state": "running"})
            S.write_json(codex_run_path(project, slug), current)
            if claim_field == "pending_resume" and task.get("state") == "blocked":
                prior_blocked = {"blocked_reason": task.get("blocked_reason"),
                                 "needs_user": task.get("needs_user")}
                task["blocked_reason"] = None
                task["needs_user"] = None
                T._move(project, task, "running", "altd", answer=resume_answer,
                        dispatch_id=dispatch_id, generation=generation)
                activated_resume = True
    if cancelled:
        os.close(gate_write)
        stopped = _cancel_new_child(child, child_start)
        raise T.TransitionError(
            f"Codex L2 generation was cancelled during wrapper launch (stopped={stopped})")
    try:
        if os.write(gate_write, b"1") != 1:
            raise OSError("short wrapper gate write")
    except OSError as exc:
        stopped = _cancel_new_child(child, child_start)
        if activated_resume:
            with S.project_lock(project):
                task = S.load_task(project, slug)
                claim = task.get("pending_resume") or {}
                if (task.get("state") == "running" and claim.get("dispatch_id") == dispatch_id
                        and claim.get("generation") == generation):
                    task.update(prior_blocked)
                    T._move(project, task, "blocked", "altd", reason="resume wrapper gate failed")
        error = f"Codex L2 wrapper gate release failed: {exc} (stopped={stopped})"
        _mark_codex_launch_failed(project, slug, dispatch_id, generation, error)
        raise T.TransitionError(error) from exc
    finally:
        os.close(gate_write)
    return {"returncode": 0, "stdout": f"Codex L2 wrapper pid {child.pid}", "stderr": "",
            "agent": _codex_agent(current, f"{project}/{dispatch_id}")}


def stop_codex_worker(project: str, slug: str, dispatch_id: str | None = None) -> bool:
    """Stop and verify the exact generation; process evidence outranks record labels."""
    with S.project_lock(project):
        current = codex_run(project, slug)
        if not current or (dispatch_id and current.get("dispatch_id") != dispatch_id):
            return False
        generation = current.get("generation")
        if not generation:
            return False
        wrapper_pid, engine_pid = current.get("pid"), current.get("engine_pid")
        wrapper_start, engine_start = current.get("pid_start"), current.get("engine_pid_start")
        if not codex_processes_live(current):
            current.update({"state": "stopped", "done": current.get("done") or S.now()})
            S.write_json(codex_run_path(project, slug), current)
            return True
        current.update({"state": "stopping", "stopped_by": os.getpid()})
        S.write_json(codex_run_path(project, slug), current)
    group_stopped = _kill_codex_group(current)
    with S.project_lock(project):
        latest = codex_run(project, slug)
        stopped = (latest.get("generation") == generation and group_stopped
                   and not codex_processes_live(latest))
        if latest.get("generation") == generation:
            latest.update({"state": "stopped" if stopped else "stop-failed", "done": S.now()})
            S.write_json(codex_run_path(project, slug), latest)
    S.append_event(project, slug, "l2-codex-stopped", dispatch_id=current.get("dispatch_id"),
                   generation=generation, stopped=stopped)
    return stopped


def _codex_generation_current(project: str, slug: str, dispatch_id: str, generation: str) -> bool:
    current = codex_run(project, slug)
    return (current.get("dispatch_id") == dispatch_id and current.get("generation") == generation
            and current.get("state") in ("starting", "running"))


def _codex_owned_cwd(project: str, slug: str, dispatch_id: str, generation: str,
                     worktree: Path, candidate: Path) -> bool:
    if not _codex_generation_current(project, slug, dispatch_id, generation):
        return False
    resolved = candidate.resolve()
    if resolved == worktree.resolve():
        return True
    from . import l1
    for run in l1.list_runs(project, slug):
        value = run.get("worktree")
        if not value:
            continue
        try:
            if Path(value).resolve(strict=True) != resolved:
                continue
            l1.validate_broker_worktree(project, slug, run, resolved, dispatch_id=dispatch_id)
            return True
        except (OSError, BrokerDenied, T.TransitionError):
            return False
    return False


def _codex_before_cli(project: str, slug: str, dispatch_id: str, generation: str,
                      worktree: Path, argv: list[str], candidate: Path) -> dict[str, str] | None:
    """Re-prove host Git authority immediately before a brokered mutating/network command."""
    with S.project_lock(project):
        if not _codex_generation_current(project, slug, dispatch_id, generation):
            raise BrokerDenied("worker generation changed before trusted CLI execution")
        task = S.load_task(project, slug)
        if task.get("dispatch_id") != dispatch_id or task.get("state") != "running":
            raise BrokerDenied("task dispatch changed before trusted CLI execution")
        record = codex_run(project, slug)
        origin_sha = str(record.get("origin_sha") or "")
        if not origin_sha:
            raise BrokerDenied("Codex L2 record has no immutable origin/main base")
        resolved = candidate.resolve()
        authority = resolved
        if argv[:2] == ["l1", "run"] and "--cwd" in argv:
            indexes = [index for index, value in enumerate(argv[:-1]) if value == "--cwd"]
            if len(indexes) != 1:
                raise BrokerDenied("brokered l1 run requires one unambiguous --cwd")
            selected = Path(argv[indexes[0] + 1])
            try:
                authority = (selected if selected.is_absolute() else resolved / selected).resolve(strict=True)
            except OSError as exc:
                raise BrokerDenied(f"brokered L1 parent cwd is unavailable: {exc}") from exc
        if authority == worktree.resolve():
            try:
                git_dir = _validate_task_worktree(
                    config.project_path(project), project, slug, worktree, origin_sha, require_clean=False)
                git_policy.require_hooks_installed(worktree, config.HOOKS)
            except (git_policy.GitPolicyError, T.TransitionError) as exc:
                raise BrokerDenied(f"task worktree provenance changed: {exc}") from exc
            if argv[:1] in (["land"], ["verify"]) or argv[:2] == ["l1", "run"]:
                return {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(authority)}
            return None
        from . import l1
        run = next((item for item in l1.list_runs(project, slug)
                    if item.get("worktree") and Path(item["worktree"]).resolve() == authority), None)
        validator = getattr(l1, "validate_broker_worktree", None)
        if run is None or not callable(validator):
            raise BrokerDenied("L1 worktree has no authoritative broker provenance validator")
        proof = validator(project, slug, run, authority, dispatch_id=dispatch_id)
        if not isinstance(proof, dict) or not proof.get("GIT_DIR") or not proof.get("GIT_WORK_TREE"):
            raise BrokerDenied("L1 worktree provenance validator returned no immutable Git proof")
        return {str(key): str(value) for key, value in proof.items()}


def _codex_checkpoint_tool(payload: dict, worktree: Path, slug: str) -> bool:
    """Checkpoint/report operations remain available after the hard tool-call cap."""
    tool = str(payload.get("tool_name") or "").lower()
    tool_input = payload.get("tool_input") or {}
    checkpoint_root = (worktree / ".altitude-checkpoints").resolve()
    allowed_names = {"progress.md", "report.md", "report.json"}

    def owned(value) -> bool:
        if not value:
            return False
        path = Path(str(value))
        path = path if path.is_absolute() else worktree / path
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved.parent == checkpoint_root and resolved.name in allowed_names

    if tool in ("edit", "write", "multiedit"):
        return owned(tool_input.get("file_path") or tool_input.get("path"))
    if tool == "apply_patch":
        patch = str(tool_input.get("patch") or tool_input.get("input") or "")
        targets = re.findall(r"^(?:\+\+\+|---)\s+(?:[ab]/)?([^\s]+)", patch, re.M)
        targets += re.findall(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", patch, re.M)
        targets += re.findall(r"^\*\*\* Move to:\s*(.+?)\s*$", patch, re.M)
        targets = [target for target in targets if target != "/dev/null"]
        return bool(targets) and all(owned(target) for target in targets)
    if tool not in ("exec_command", "shell", "bash"):
        return False
    command = str(tool_input.get("cmd") or tool_input.get("command") or "").strip()
    if not command or re.search(r"[\n;&|<>`$()]", command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if words[:3] == ["alt", "task", "status"]:
        return words[3:] in ([], [slug])
    if words[:3] == ["alt", "task", "block"]:
        return len(words) == 6 and words[3] == slug and words[4] == "--reason" and bool(words[5])
    if words[:3] == ["alt", "task", "checkpoint"]:
        return len(words) == 8 and words[3] == slug and set(words[4::2]) == {"--name", "--source"}
    return False


def _codex_envelope_hook(project: str, slug: str, dispatch_id: str, generation: str,
                         worktree: Path, payload: dict) -> dict:
    """Host-authoritative, serialized Codex L2 tool-call envelope."""
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    tool_id = payload.get("tool_use_id")
    if not tool or not isinstance(tool_input, dict) or not tool_id:
        return {"allowed": False, "message": "altitude: envelope hook input is malformed"}
    checkpoint = _codex_checkpoint_tool(payload, worktree, slug)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        record = codex_run(project, slug)
        if (task.get("state") != "running" or task.get("dispatch_id") != dispatch_id
                or record.get("dispatch_id") != dispatch_id or record.get("generation") != generation
                or record.get("state") not in ("starting", "running")):
            return {"allowed": False, "message": "altitude: worker generation changed; tool denied"}
        try:
            call_cap = int((task.get("envelope") or {})["max_turns"])
        except (KeyError, TypeError, ValueError):
            return {"allowed": False, "message": "altitude: tool-call envelope is missing or invalid"}
        counts_path = config.MONITOR_DIR / f"counts-{project}--{dispatch_id}.json"
        counts = S.read_json(counts_path, {}) or {}
        if not isinstance(counts, dict) or not isinstance(counts.get("codex_tool_seen", []), list):
            return {"allowed": False, "message": "altitude: tool-call counter is malformed"}
        seen = list(counts.get("codex_tool_seen") or [])
        dedupe = str(tool_id)
        stop_reason = counts.get("envelope_stop")
        if dedupe in seen:
            allowed = not stop_reason or checkpoint
            return {"allowed": allowed, "message": "" if allowed else
                    f"altitude: envelope checkpoint-only mode ({stop_reason})"}
        try:
            calls = int(counts.get("tool_calls", 0))
            edits = int(counts.get("edits", 0))
        except (TypeError, ValueError):
            return {"allowed": False, "message": "altitude: tool-call counter values are malformed"}
        if stop_reason or calls >= call_cap:
            counts["envelope_stop"] = stop_reason or "tool-call cap"
            if not checkpoint:
                counts["denied_tool_calls"] = int(counts.get("denied_tool_calls", 0)) + 1
            counts["codex_tool_seen"] = (seen + [dedupe])[-500:]
            S.write_json(counts_path, counts)
            return {"allowed": checkpoint, "message": "" if checkpoint else
                    f"altitude: Codex L2 tool-call envelope reached ({calls}/{call_cap}); checkpoint, report, and stop"}
        counts["tool_calls"] = calls + 1
        counts["edits"] = edits + (1 if tool.lower() in ("apply_patch", "edit", "write", "multiedit") else 0)
        counts["codex_tool_seen"] = (seen + [dedupe])[-500:]
        S.write_json(counts_path, counts)
        return {"allowed": True, "message": ""}


def _codex_checkpoint(project: str, slug: str, dispatch_id: str, generation: str,
                      name: str, data: bytes) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrokerDenied("checkpoint is not UTF-8 text") from exc
    if name == "report.json":
        try:
            report = json.loads(text)
        except ValueError as exc:
            raise BrokerDenied(f"report.json is invalid JSON: {exc}") from exc
        if not isinstance(report, dict):
            raise BrokerDenied("report.json must contain a JSON object")
    target = S.task_dir(project, slug) / name
    with S.project_lock(project):
        task = S.load_task(project, slug)
        record = codex_run(project, slug)
        if (task.get("state") != "running" or task.get("dispatch_id") != dispatch_id
                or record.get("dispatch_id") != dispatch_id or record.get("generation") != generation
                or record.get("state") not in ("starting", "running")):
            raise BrokerDenied("worker generation changed before checkpoint persistence")
        S.atomic_write(target, text)
    S.append_event(project, slug, "l2-checkpoint", dispatch_id=dispatch_id,
                   generation=generation, name=name, bytes=len(data))
    return {"checkpoint": name, "bytes": len(data), "path": str(target)}


def _codex_after_cli(project: str, slug: str, dispatch_id: str, argv: list[str], returncode: int) -> None:
    if returncode != 0 or argv[:2] != ["l1", "run"]:
        return
    from . import l1
    count_path = config.MONITOR_DIR / f"counts-{project}--{dispatch_id}.json"
    with S.project_lock(project):
        counts = S.read_json(count_path, {}) or {}
        counts["subagent_launches"] = l1.billed_count(project, slug, dispatch_id)
        S.write_json(count_path, counts)

def exec_codex_l2(project: str, slug: str, dispatch_id: str) -> dict:
    """Detached child body: validate provenance, execute Codex, then close its exact generation."""
    record = codex_run(project, slug)
    generation = os.environ.get("ALTITUDE_CODEX_L2_GENERATION")
    if (record.get("dispatch_id") != dispatch_id or not generation
            or record.get("generation") != generation):
        raise T.TransitionError("Codex L2 record does not match this wrapper generation")
    try:
        gate_fd = int(os.environ.pop(CODEX_GATE_FD_ENV))
        released = os.read(gate_fd, 1)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        released = b""
        gate_error = f"wrapper launch gate unavailable: {exc}"
    else:
        gate_error = "wrapper launch gate closed before durable PID persistence"
    finally:
        if "gate_fd" in locals():
            try:
                os.close(gate_fd)
            except OSError:
                pass
    if released != b"1":
        _mark_codex_launch_failed(project, slug, dispatch_id, generation, gate_error)
        return codex_run(project, slug)
    worktree = Path(record.get("worktree") or Path.cwd())
    origin_sha = str(record.get("origin_sha") or "")
    if not origin_sha:
        raise T.TransitionError("Codex L2 record has no immutable origin/main base")
    validated_git_dir = _validate_task_worktree(
        config.project_path(project), project, slug, worktree, origin_sha, require_clean=False)
    trusted_git_dir = Path(record.get("trusted_git_dir") or "")
    try:
        trusted_matches = (trusted_git_dir.resolve(strict=True)
                           == validated_git_dir.resolve(strict=True))
    except OSError:
        trusted_matches = False
    if not trusted_matches:
        raise T.TransitionError("Codex L2 trusted Git directory changed before engine launch")
    prompt_path = Path(record["prompt"])
    if prompt_path.resolve().parent != S.task_dir(project, slug).resolve():
        raise T.TransitionError(f"Codex L2 prompt is outside its task directory: {prompt_path}")
    prompt = prompt_path.read_text()

    def note_session(session_id: str) -> None:
        current = _update_codex_record(project, slug, dispatch_id, generation, session_id=session_id)
        if current is None:
            return
        with S.project_lock(project):
            live = S.load_task(project, slug)
            if live.get("dispatch_id") == dispatch_id:
                live["session_id"] = session_id
                S.save_task(project, live)

    def note_engine_start(pid: int) -> None:
        pid_start = _proc_start_time(pid)
        if not pid_start:
            raise T.TransitionError("Codex engine PID identity was unavailable at launch")
        with S.project_lock(project):
            current = codex_run(project, slug)
            if (current.get("dispatch_id") != dispatch_id or current.get("generation") != generation
                    or current.get("state") not in ("starting", "running")):
                raise T.TransitionError("Codex L2 generation was cancelled before engine PID persistence")
            current.update({"engine_pid": pid, "engine_pid_start": pid_start})
            S.write_json(codex_run_path(project, slug), current)

    broker_dir = Path(tempfile.mkdtemp(prefix=f"altitude-l2-broker-{generation[:8]}-"))

    try:
        broker = AltBroker(
            socket_path=broker_dir / "broker.sock", token=secrets.token_hex(32), project=project,
            slug=slug, generation=generation, worktree=worktree, trusted_alt=config.REPO / "bin" / "alt",
            policy=l2_policy,
            validate_generation=lambda: _codex_generation_current(project, slug, dispatch_id, generation),
            validate_cwd=lambda cwd: _codex_owned_cwd(
                project, slug, dispatch_id, generation, worktree, cwd),
            hook=lambda payload: _codex_envelope_hook(
                project, slug, dispatch_id, generation, worktree, payload),
            checkpoint=lambda name, data: _codex_checkpoint(
                project, slug, dispatch_id, generation, name, data),
            before_cli=lambda argv, cwd: _codex_before_cli(
                project, slug, dispatch_id, generation, worktree, argv, cwd),
            after_cli=lambda argv, returncode: _codex_after_cli(
                project, slug, dispatch_id, argv, returncode),
        )
        extra_env = l2_env(project, {"slug": slug, "dispatch_id": dispatch_id})
        extra_env.update(_codex_guard_env(worktree, trusted_git_dir))
        extra_env.update(broker.env())
        with broker:
            res = engines.codex_exec(
                prompt, cwd=worktree, sandbox="workspace-write", timeout=7200,
                model=config.project(project).get("l2_codex_model") or config.MODELS.get("l2_codex"),
                effort=config.CODEX_EFFORT.get("l2"), extra_config=_codex_extra_config(worktree, project, slug),
                extra_env=extra_env, resume=record.get("resume"), on_start=note_engine_start,
                on_session=note_session, bypass_hook_trust=True, start_new_session=False,
            )
    except Exception as exc:
        res = {"text": "", "usage": {}, "session_id": record.get("session_id"),
               "returncode": -1, "error": f"{type(exc).__name__}: {exc}"}
    if res.get("session_id"):
        note_session(str(res["session_id"]))
    from .monitor import codex_context_percent
    result = {"error": res.get("error"), "returncode": res.get("returncode"),
              "usage": res.get("usage") or {}, "text_tail": (res.get("text") or "")[-1500:]}
    pgid = os.getpgrp()
    while not engines.reap_process_group_members(pgid, exclude_pid=os.getpid()):
        with S.project_lock(project):
            current = codex_run(project, slug)
            if (current.get("dispatch_id") != dispatch_id or current.get("generation") != generation
                    or current.get("state") in ("stopping", "stopped")):
                return current
            current.update({"state": "stop-failed",
                            "result": {**result, "error": (result.get("error") or "")
                                       + " | owned process-group descendants remain"}})
            S.write_json(codex_run_path(project, slug), current)
        time.sleep(1)
    with S.project_lock(project):
        final = codex_run(project, slug)
        if (final.get("dispatch_id") != dispatch_id or final.get("generation") != generation
                or final.get("state") in ("stopping", "stopped")):
            return final
        final.update({"state": "done" if not res.get("error") else "failed", "done": S.now(),
                      "context_percent": codex_context_percent(res.get("session_id")), "result": result})
        S.write_json(codex_run_path(project, slug), final)
        return final


def _adopt_pending_dispatch(project: str, task: dict) -> dict | None:
    """Promote an exact worker launched before lifecycle persistence, or wait/clear safely."""
    pending = task.get("pending_dispatch") or {}
    if not pending:
        return None
    dispatch_id, engine = pending.get("dispatch_id"), pending.get("engine")
    agent = None
    if engine == "codex":
        record = codex_run(project, task["slug"])
        exact = (record.get("dispatch_id") == dispatch_id
                 and record.get("generation") == pending.get("generation"))
        active = exact and codex_processes_live(record)
        terminal = (exact and not active
                    and record.get("state") in ("done", "failed", "stop-failed", "stopped"))
        if active or terminal:
            agent = _codex_agent(record, f"{project}/{dispatch_id}", active=active)
    elif engine == "claude":
        rows = [row for row in engines.claude_agents() if row.get("name") == f"{project}/{dispatch_id}"]
        if rows:
            agent = max(rows, key=lambda row: row.get("startedAt") or 0)
    if agent:
        T.dispatch(
            project, task["slug"], dispatch_id=dispatch_id, session_id=agent.get("sessionId"),
            agent_id=agent.get("id"), worktree=pending.get("worktree"), branch=pending.get("branch"),
            l2_engine=engine, l2_engine_why=pending.get("why"), origin_sha=pending.get("origin_sha"),
            actor="altd-recovery",
        )
        S.append_event(project, task["slug"], "dispatch-adopted", dispatch_id=dispatch_id, engine=engine)
        return {"dispatch_id": dispatch_id, "agent": agent, "engine": engine,
                "why": "adopted durable pending dispatch after restart", "stdout": ""}
    if _seconds_since(pending.get("started") or "") < 600:
        return {"dispatch_id": dispatch_id, "agent": None, "engine": engine,
                "why": "pending worker generation is still within its launch claim", "stdout": "", "pending": True}
    if engine == "codex":
        stop_codex_worker(project, task["slug"], dispatch_id)
    with S.project_lock(project):
        live = S.load_task(project, task["slug"])
        if (live.get("pending_dispatch") or {}).get("dispatch_id") == dispatch_id:
            live.pop("pending_dispatch", None)
            live["dispatching"] = None
            S.save_task(project, live)
    return None


def run(project: str, slug: str, model: str | None = None) -> dict:
    pending = S.load_task(project, slug)
    if pending.get("state") == "approved" and pending.get("pending_dispatch"):
        if adopted := _adopt_pending_dispatch(project, pending):
            return adopted
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "approved":
            raise T.TransitionError(f"{slug} is {task['state']}, not approved")
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
    repo = config.project_path(project)
    try:
        origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
    except git_policy.GitPolicyError as exc:
        # system_fault may acquire state locks, so it deliberately lives outside project_lock.
        from . import improve
        improve.system_fault("main-unpushed", f"{project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"dispatch refused by Git provenance gate: {exc}") from exc
    try:
        worktree_path = _task_worktree(repo, project, slug, origin_sha)
        trusted_git_dir = _validate_task_worktree(
            repo, project, slug, worktree_path, origin_sha, require_clean=True)
    except (git_policy.GitPolicyError, T.TransitionError) as exc:
        from . import improve
        improve.system_fault("task-git-provenance", f"{project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"dispatch refused by task provenance gate: {exc}") from exc
    proj = config.project(project)
    choice = _l2_choice(task, proj)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] != "approved":
            raise T.TransitionError(f"{slug} is {task['state']}, not approved")
        if task.get("dispatching") and _seconds_since(task["dispatching"]) < 600:
            raise T.TransitionError(f"{slug} is already being dispatched")
        held = wip_hold(project, task)
        if held:
            raise T.TransitionError(held)
        task["dispatching"] = S.now()
        task["report_not_before"] = S.now()
        attempt = task.get("attempt", 0) + 1
        dispatch_id = f"{slug}-{attempt}"
        name = f"{project}/{dispatch_id}"
        generation = secrets.token_hex(12) if choice["engine"] == "codex" else None
        task["pending_dispatch"] = {
            "dispatch_id": dispatch_id, "engine": choice["engine"], "why": choice["why"],
            "origin_sha": origin_sha, "worktree": str(worktree_path),
            "branch": worktree_branch(slug, worktree_path), "generation": generation,
            "started": S.now(),
        }
        S.save_task(project, task)
    try:
        brief_md = build_brief(project, slug, choice["engine"])
        T.brief(project, slug, brief_md, actor="altd")
        env_file = config.MONITOR_DIR / f"envelope-{project}--{dispatch_id}.json"
        S.write_json(env_file, {"project": project, "slug": slug, "dispatch_id": dispatch_id, **task["envelope"]})
        persona = rules.compiled_persona("l2", project)
        if choice["engine"] == "codex":
            git_policy.require_hooks_installed(worktree_path, config.HOOKS)
            prompt = persona.read_text() + "\n\n" + engines.CODEX_PATCH_NOTE + "\n\n" + brief_md
        else:
            settings = session_settings(project, slug, f"{project}--{dispatch_id}")
    except Exception as exc:
        with S.project_lock(project):
            current = S.load_task(project, slug)
            if current.get("state") == "approved":
                current["dispatching"] = None
                current.pop("pending_dispatch", None)
                S.save_task(project, current)
        S.append_event(project, slug, "dispatch-failed", engine=choice["engine"], phase="prepare",
                       error=f"{type(exc).__name__}: {exc}"[:600])
        raise
    try:
        if choice["engine"] == "codex":
            res = _start_codex_worker(
                project, slug, dispatch_id, worktree_path, prompt,
                origin_sha=origin_sha, trusted_git_dir=trusted_git_dir,
                generation=generation, claim_field="pending_dispatch",
            )
        else:
            res = engines.claude_bg(
                name, brief_md, cwd=worktree_path, worktree=None, persona=persona,
                permission_mode="auto", max_turns=task["envelope"]["max_turns"],
                model=model or task.get("model") or proj.get("l2_model") or config.MODELS["l2"],
                settings=settings, extra_env=l2_env(project, {"slug": slug, "dispatch_id": dispatch_id}),
            )
    except Exception as exc:
        # A background launch may succeed and then fail during agent discovery.
        # Preserve the claim so restart adoption can settle the exact worker.
        S.append_event(project, slug, "dispatch-launch-indeterminate", engine=choice["engine"],
                       dispatch_id=dispatch_id, error=f"{type(exc).__name__}: {exc}"[:600])
        raise
    agent = res.get("agent") or {}
    if res["returncode"] != 0 and not agent:
        S.append_event(project, slug, "dispatch-launch-indeterminate", engine=choice["engine"],
                       dispatch_id=dispatch_id, stdout=res["stdout"][:300], stderr=res["stderr"][:300])
        raise RuntimeError(
            f"{choice['engine']} L2 launch could not be confirmed: "
            f"{res['stderr'][:300] or res['stdout'][:300]}"
        )
    worktree = str(worktree_path)
    try:
        T.dispatch(
            project, slug, dispatch_id=dispatch_id, session_id=agent.get("sessionId"),
            agent_id=agent.get("id"), worktree=worktree,
            branch=worktree_branch(slug, worktree, agent.get("id")),
            l2_engine=choice["engine"], l2_engine_why=choice["why"], origin_sha=origin_sha,
        )
    except Exception as exc:
        S.append_event(project, slug, "dispatch-transition-indeterminate", engine=choice["engine"],
                       dispatch_id=dispatch_id, error=f"{type(exc).__name__}: {exc}"[:600])
        raise
    S.append_event(project, slug, "l2-engine", engine=choice["engine"], why=choice["why"])
    if agent.get("sessionId"):
        S.write_json(config.MONITOR_DIR / f"session-{agent['sessionId']}.json",
                     {"project": project, "slug": slug, "dispatch_id": dispatch_id, "level": "l2"})
    return {"dispatch_id": dispatch_id, "agent": agent, "stdout": res["stdout"], **choice}


def l2_env(project: str, task: dict) -> dict:
    """The env every L2 session (fresh or resumed) needs: the hooks read the session key to find their envelope."""
    return {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": task["slug"],
            "ALTITUDE_ACTOR": "l2", "ALTITUDE_SESSION_KEY": f"{project}--{task['dispatch_id']}"}


def resume_session(project: str, slug: str, text: str, session_id: str | None = None) -> dict:
    """Re-attach the task's L2 transcript in a new --bg worker (in the task's worktree) and hand it `text`.

    A resumed session gets a new session id and agent id: the task is rebound to the live row, so `poll()` follows the
    new worker instead of re-reading the old one's `failed`/`done` row. No fallback to the main checkout: a missing
    worktree is a dispatch-again situation, not a place to run an L2 that thinks it is on its own branch."""
    task = S.load_task(project, slug)
    engine = task_l2_engine(project, task)
    if task.get("state") not in ("running", "blocked"):
        raise T.TransitionError(f"{slug}: L2 messages require running or blocked state, not {task.get('state')}")
    if not task.get("dispatch_id"):
        raise T.TransitionError(f"{slug}: L2 message has no current dispatch generation")
    sid = session_id or task.get("session_id")
    cwd = Path(task.get("worktree") or "")
    if not task.get("worktree") or not cwd.is_dir():
        raise T.TransitionError(f"worktree missing for {slug} ({task.get('worktree')}); dispatch again")
    repo = config.project_path(project)
    try:
        current_origin_sha = git_policy.fetch_and_require_exact_base(repo, "main")
        origin_sha = _task_origin_sha(project, task, current_origin_sha)
        # A resume is specifically how an agent continues uncommitted work, so dirt is allowed here; path,
        # branch, immutable dispatch base, and every committed ancestor remain strict.
        trusted_git_dir = _validate_task_worktree(
            repo, project, slug, cwd, origin_sha, require_clean=False)
    except (git_policy.GitPolicyError, T.TransitionError) as exc:
        from . import improve
        improve.system_fault("task-git-provenance", f"resume {project}/{slug}: {exc}", project=project, task=slug)
        raise T.TransitionError(f"resume refused by Git provenance gate: {exc}") from exc
    with S.project_lock(project):
        current = S.load_task(project, slug)
        if (current.get("dispatch_id") != task.get("dispatch_id")
                or Path(current.get("worktree") or "").resolve() != cwd.resolve()):
            raise T.TransitionError("task dispatch changed while its resume provenance was being validated")
        if current.get("origin_sha") and str(current["origin_sha"]) != origin_sha:
            raise T.TransitionError("task origin base changed while its resume provenance was being validated")
        current["origin_sha"] = origin_sha
        S.save_task(project, current)
        task = current
    # A replacement must never overlap the generation it replaces.
    if engine == "codex":
        record = codex_run(project, slug)
        if record.get("dispatch_id") == task.get("dispatch_id"):
            process_live = codex_processes_live(record)
            needs_stop_proof = process_live or record.get("state") == "stop-failed"
            if needs_stop_proof and not stop_codex_worker(project, slug, task.get("dispatch_id")):
                raise T.TransitionError(f"{slug}: previous Codex worker could not be stopped")
    elif task.get("agent_id"):
        old_id = task["agent_id"]
        rows = engines.claude_agents()
        old_live = any(a.get("id") == old_id and a.get("state") not in ("failed", "done", "stopped")
                       for a in rows)
        if old_live:
            engines.claude_stop(old_id)
            rows = engines.claude_agents()
            if any(a.get("id") == old_id and a.get("state") not in ("failed", "done", "stopped")
                   for a in rows):
                raise T.TransitionError(f"{slug}: previous Claude worker is still live after stop")
    # Only a report written after the previous worker is confirmed gone can
    # satisfy this replacement generation.
    with S.project_lock(project):
        live = S.load_task(project, slug)
        if (live.get("state") != task.get("state")
                or live.get("dispatch_id") != task.get("dispatch_id")):
            raise T.TransitionError("task changed while its previous worker was being stopped")
        live["report_not_before"] = S.now()
        S.save_task(project, live)
        task = live
    generation = secrets.token_hex(12)
    with S.project_lock(project):
        live = S.load_task(project, slug)
        if live.get("state") != task.get("state") or live.get("dispatch_id") != task.get("dispatch_id"):
            raise T.TransitionError("task changed while its resume generation was being claimed")
        live["pending_resume"] = {"dispatch_id": task.get("dispatch_id"), "engine": engine,
                                  "generation": generation, "started": S.now()}
        S.save_task(project, live)
    continuation = RESUME_STATUS_NOTE + "\n\n" + text
    task_dir = S.task_dir(project, slug)
    fresh_context = ""
    if not sid:
        original = ((task_dir / "brief.md").read_text()
                    if (task_dir / "brief.md").exists() else build_brief(project, slug, engine))
        progress = ((task_dir / "progress.md").read_text()
                    if (task_dir / "progress.md").exists() else "(no progress file was written)")
        fresh_context = (
            "## Original L2 brief\n" + original
            + "\n\n## Surviving progress\n" + progress
            + "\n\n## Continuation\n" + continuation
        )
    name = f"{project}/{task['dispatch_id']}"
    persona = rules.compiled_persona("l2", project)
    if engine == "codex":
        git_policy.require_hooks_installed(cwd, config.HOOKS)
        prompt = engines.CODEX_PATCH_NOTE + "\n\n" + (continuation if sid else
                 persona.read_text() + "\n\n" + fresh_context)
        res = _start_codex_worker(
            project, slug, task["dispatch_id"], cwd, prompt,
            origin_sha=origin_sha, trusted_git_dir=trusted_git_dir,
            resume=sid, generation=generation, claim_field="pending_resume",
            resume_answer=text,
        )
        new = res["agent"]
    else:
        common = {"cwd": cwd, "persona": persona, "max_turns": task["envelope"]["max_turns"],
                  "settings": task_dir / "settings.json", "extra_env": l2_env(project, task)}
        if sid:
            res = engines.claude_resume_bg(name, sid, continuation, **common)
        else:
            res = engines.claude_bg(name, fresh_context, worktree=None, permission_mode="auto", **common)
        live = [a for a in engines.claude_agents()
                if a.get("name") == name and a.get("state") not in ("failed", "done", "stopped")]
        if live:
            new = max(live, key=lambda a: a.get("startedAt") or 0)
        else:
            new = {}
    if engine == "claude" and not live:
        raise RuntimeError(f"resume of {name} produced no live worker: {res['stderr'][:200] or res['stdout'][:200]}")
    cancelled = False
    with S.project_lock(project):
        live_task = S.load_task(project, slug)
        claim = live_task.get("pending_resume") or {}
        expected_state = "running" if engine == "codex" and task.get("state") == "blocked" else task.get("state")
        if (live_task.get("state") == expected_state
                and live_task.get("dispatch_id") == task.get("dispatch_id")
                and claim.get("dispatch_id") == task.get("dispatch_id")
                and claim.get("generation") == generation and not claim.get("cancel_requested")):
            live_task["agent_id"] = new.get("id")
            live_task["session_id"] = new.get("sessionId") or sid
            live_task.pop("pending_resume", None)
            S.save_task(project, live_task)
        else:
            cancelled = True
    if cancelled:
        if engine == "codex":
            stop_codex_worker(project, slug, task.get("dispatch_id"))
        elif new.get("id"):
            engines.claude_stop(new["id"])
            if any(row.get("id") == new["id"] and row.get("state") not in ("failed", "done", "stopped")
                   for row in engines.claude_agents()):
                raise T.TransitionError(f"{slug}: cancelled Claude resume worker is still live")
        raise T.TransitionError("task changed while its replacement worker was being persisted")
    S.append_event(project, slug, "resumed", engine=engine, agent_id=new.get("id"),
                   session_id=new.get("sessionId") or sid, previous=sid)
    res["agent"] = new
    return res


def resume_blocked(project: str, slug: str, answer: str, prefix: str = "Burak's answer: ") -> dict:
    task = S.load_task(project, slug)
    if task["state"] == "blocked":
        hold = wip_hold(project, task)
        if hold:
            waiting = (f"waiting for lease: {hold.removeprefix('file lease: ')}"
                       if hold.startswith("file lease: ") else f"waiting: {hold}")
            with S.project_lock(project):
                task = S.load_task(project, slug)
                if "blocked_question" not in task:
                    task["blocked_question"] = task.get("blocked_reason")
                task["resume_answer"] = answer
                task["resume_prefix"] = prefix
                task["resume_after"] = S.now()
                task["blocked_reason"] = waiting
                S.save_task(project, task)
                S.append_event(project, slug, "resume-deferred", hold=hold, reason=waiting)
            return {"deferred": True, "hold": hold, "waiting": waiting}
    res = resume_session(project, slug, f"{prefix}{answer}\nContinue from your progress file; finish to *done* and rewrite the report.")
    if S.load_task(project, slug).get("state") == "blocked":
        T.resume(project, slug, answer=answer)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        task.pop("resume_after", None)
        task.pop("resume_answer", None)
        task.pop("resume_prefix", None)
        S.save_task(project, task)
    res["deferred"] = False
    return res


def resume_or_retry_blocked(project: str, slug: str, answer: str) -> dict:
    """Resume from a surviving worktree, or queue a fresh dispatch without phantom running state."""
    task = S.load_task(project, slug)
    if task.get("state") != "blocked":
        raise T.TransitionError(f"{slug} is {task.get('state')}, not blocked")
    if task.get("worktree"):
        return resume_blocked(project, slug, answer)
    T.retry(project, slug, actor="burak", reason="Resume requested without a durable worktree")
    return run(project, slug)


def resume_due(project: str) -> list[str]:
    """Tasks blocked by an exhausted window come back by themselves once it reopens — oldest first, WIP-throttled."""
    now, back = S.now(), []
    due = [t for t in S.list_tasks(project) if t["state"] == "blocked" and t.get("resume_after") and t["resume_after"] <= now]
    for t in sorted(due, key=_resume_order):
        if wip_hold(project, t):
            continue  # a lease or the improve-serialization rule holds this one; a younger unrelated task may still go
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


AUTO_RECOVERY_LIMIT = 2
AUTO_RECOVERY_CLAIM_SECONDS = 300


def _automatic_recovery(task: dict) -> tuple[str, str] | None:
    """Return a stable category and continuation for mechanical blockers."""
    low = str(task.get("blocked_reason") or "").strip().lower()
    if low.startswith(("lease:", "lease —", "lease -")):
        return "lease-clear", "The file lease is clear; re-check `alt task status`, then continue."
    if "l2 session died before reporting" in low:
        return "worker-died", "The previous worker died without a current report; continue from progress.md."
    if "l2 session ended without a report" in low or "report.json missing" in low:
        return "report-missing", "The previous worker ended without a current report; continue and finish the report contract."
    return None


def _recoverable_identity(project: str, task: dict) -> bool:
    """Either engine can restart fresh from progress when the validated task worktree survives."""
    return bool(task.get("worktree"))


def recovery_pending(project: str, task: dict) -> bool:
    """Whether deterministic recovery owns this blocker, so L3 does not race it."""
    automatic = _automatic_recovery(task)
    if not automatic or not _recoverable_identity(project, task):
        return False
    category, _ = automatic
    pending = task.get("pending_resume") or {}
    if (pending.get("generation") and pending.get("dispatch_id") == task.get("dispatch_id")
            and pending.get("engine") == task_l2_engine(project, task)):
        return True
    rec = task.get("auto_recovery") or {}
    attempts = int(rec.get("attempts") or 0) if rec.get("category") == category else 0
    if (rec.get("category") == category and rec.get("in_flight")
            and _seconds_since(rec.get("started") or "") < AUTO_RECOVERY_CLAIM_SECONDS):
        return True
    return attempts < AUTO_RECOVERY_LIMIT


def _claimed_worker_live(project: str, task: dict) -> bool:
    """Adopt an exact claimed worker, including a fast terminal worker with a current report."""
    pending = task.get("pending_resume") or {}
    engine = task_l2_engine(project, task)
    if (not pending.get("generation") or pending.get("dispatch_id") != task.get("dispatch_id")
            or pending.get("engine") != engine):
        return False
    current_report = bool(task.get("report_not_before")) and _current_report(project, task)
    terminal = False
    if engine == "codex":
        record = codex_run(project, task["slug"])
        if record.get("generation") != pending.get("generation"):
            return False
        if record.get("dispatch_id") != task.get("dispatch_id"):
            return False
        active = (record.get("state") in ("starting", "running")
                  and _pid_alive(record.get("pid"))
                  and _proc_start_time(record.get("pid")) == record.get("pid_start"))
        terminal = record.get("state") in ("done", "failed", "stop-failed") and current_report
        if not (active or terminal):
            return False
        agent = _codex_agent(record, f"{project}/{task.get('dispatch_id')}")
    else:
        name = f"{project}/{task.get('dispatch_id')}"
        rows = [a for a in engines.claude_agents() if a.get("name") == name]
        active_rows = [a for a in rows if a.get("state") not in ("failed", "done", "stopped")]
        if active_rows:
            agent = max(active_rows, key=lambda a: a.get("startedAt") or 0)
        elif current_report:
            terminal = True
            agent = max(rows, key=lambda a: a.get("startedAt") or 0) if rows else {}
        else:
            return False
    with S.project_lock(project):
        live = S.load_task(project, task["slug"])
        if live.get("state") != "blocked":
            return live.get("state") == "running"
        live_pending = live.get("pending_resume") or {}
        if (live_pending.get("dispatch_id") != pending.get("dispatch_id")
                or live_pending.get("generation") != pending.get("generation")
                or live_pending.get("engine") != pending.get("engine")):
            return False
        live["agent_id"] = agent.get("id")
        live["session_id"] = agent.get("sessionId") or live.get("session_id")
        live["blocked_reason"] = None
        live["needs_user"] = None
        live.pop("pending_resume", None)
        T._move(project, live, "running", "altd", recovered_worker=True, recovered_terminal=terminal)
    return True


def _settle_auto_recovery(project: str, slug: str, token: str | None, *, error: str | None = None,
                          deferred: bool | None = None) -> None:
    """Settle only the matching bounded recovery attempt without resetting its charge."""
    with S.project_lock(project):
        live = S.load_task(project, slug)
        rec = live.get("auto_recovery") or {}
        if not token or not rec or rec.get("token") != token:
            return
        rec["in_flight"] = False
        rec["finished"] = S.now()
        rec["error"] = error
        if deferred is None:
            rec.pop("deferred", None)
        else:
            rec["deferred"] = bool(deferred)
        live["auto_recovery"] = rec
        S.save_task(project, live)


def resume_recoverable(project: str) -> list[str]:
    """Boundedly and atomically resume routine blockers after their holds clear."""
    back = []
    tasks = [t for t in S.list_tasks(project)
             if t.get("state") == "blocked" and not t.get("resume_after")]
    for snapshot in sorted(tasks, key=_resume_order):
        if snapshot.get("pending_resume"):
            if _claimed_worker_live(project, snapshot):
                _settle_auto_recovery(
                    project, snapshot["slug"], (snapshot.get("auto_recovery") or {}).get("token"))
                back.append(snapshot["slug"])
                continue
            if _seconds_since((snapshot.get("pending_resume") or {}).get("started") or "") < AUTO_RECOVERY_CLAIM_SECONDS:
                continue
            with S.project_lock(project):
                live = S.load_task(project, snapshot["slug"])
                live_pending = live.get("pending_resume") or {}
                snapshot_pending = snapshot.get("pending_resume") or {}
                if live.get("state") != "blocked":
                    continue
                if any(live_pending.get(key) != snapshot_pending.get(key)
                       for key in ("dispatch_id", "generation", "engine")):
                    continue
                live.pop("pending_resume", None)
                S.save_task(project, live)
        automatic = _automatic_recovery(snapshot)
        if not automatic or not _recoverable_identity(project, snapshot):
            continue
        category, note = automatic
        rec = snapshot.get("auto_recovery") or {}
        if rec.get("category") == category and rec.get("in_flight"):
            if _seconds_since(rec.get("started") or "") < AUTO_RECOVERY_CLAIM_SECONDS:
                continue
        if wip_hold(project, snapshot):
            continue
        reason = str(snapshot.get("blocked_reason") or "")
        token = f"{os.getpid()}:{datetime.now(timezone.utc).timestamp()}"
        with S.project_lock(project):
            live = S.load_task(project, snapshot["slug"])
            if (live.get("state") != "blocked" or str(live.get("blocked_reason") or "") != reason
                    or live.get("pending_resume")):
                continue
            rec = live.get("auto_recovery") or {}
            attempts = int(rec.get("attempts") or 0) if rec.get("category") == category else 0
            if attempts >= AUTO_RECOVERY_LIMIT:
                continue
            if rec.get("in_flight") and _seconds_since(rec.get("started") or "") < AUTO_RECOVERY_CLAIM_SECONDS:
                continue
            live["auto_recovery"] = {"category": category, "attempts": attempts + 1,
                                     "token": token, "in_flight": True, "started": S.now()}
            S.save_task(project, live)
        try:
            result = resume_blocked(project, snapshot["slug"], note, prefix="Altitude automatic recovery: ")
        except Exception as exc:
            _settle_auto_recovery(
                project, snapshot["slug"], token,
                error=f"{type(exc).__name__}: {exc}"[:500])
            continue
        deferred = bool(result.get("deferred"))
        _settle_auto_recovery(project, snapshot["slug"], token, deferred=deferred)
        if not deferred:
            back.append(snapshot["slug"])
    return back


def _norm(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def paths_overlap(a: list[str], b: list[str]) -> list[str]:
    """Paths collide when equal or when one is a directory prefix of the other (decision 39)."""
    out = []
    for x in map(_norm, a):
        for y in map(_norm, b):
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                out.append(x if len(x) >= len(y) else y)
    return sorted(set(out))


BROAD_CLAIMS = ("tests", "docs", "altitude", "web", "hooks", "bin", "personas", "schemas", "templates", "src", "lib", "app")


def narrow(paths: list[str]) -> list[str]:
    """Decision 51 addendum: a claim on a whole top-level directory (`tests/`, `docs/`) is not a lease — it would hold every
    task in the project behind one (2026-08-30: 23 approved tasks waited on a single `tests/` claim). Files and deeper
    directories lease; top-level directory claims are dropped here, so briefs still show them but nothing waits on them."""
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
    """The paths a task has declared: `--paths` on the task, else the proposal's `files`. This is the *staging* lease
    (`alt land` refuses changes outside it); the *hold* lease is `narrow()` of it — see wip_hold."""
    entries = task.get("paths")
    if not entries:
        p = S.read_json(S.task_dir(project, task["slug"]) / "proposal.json", {}) or {}
        entries = p.get("files") or []
    return [path for entry in entries for path in _expand_entry(str(entry))]


def _lease_tasks(project: str, exclude: str | None = None) -> list[dict]:
    """Tasks that may own a worker hold leases, including crash-window claims."""
    return [t for t in S.list_tasks(project)
            if t["slug"] != exclude
            and (t["state"] == "running"
                 or (t["state"] == "blocked" and (t.get("resume_after") or t.get("pending_resume")))
                 or (t["state"] == "approved" and t.get("pending_dispatch")))]


def leases(project: str, exclude: str | None = None) -> list[dict]:
    """Running and pending-resume tasks and the paths they hold, for status and briefs."""
    out = []
    for task in _lease_tasks(project, exclude):
        lease = {"slug": task["slug"], "paths": task_paths(project, task)}
        if task["state"] == "blocked":
            lease["pending_resume"] = True
        elif task["state"] == "approved":
            lease["pending_dispatch"] = True
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


def rule_application(task: dict) -> bool:
    """A task that edits the rules ledger (docs/RULES.md, docs/incidents) — those serialize; every other improve task
    relies on leases like anyone else (decision 51: the broad "one improve task at a time" held 11 tasks for hours)."""
    paths = task.get("paths") or []
    return str(task.get("slug", "")).startswith("apply-r-") or any(str(p).strip().lstrip("./").startswith(("docs/RULES.md", "docs/incidents")) for p in paths)


PER_TASK_HOLDS = ("file lease", "one rule-application")  # holds that belong to one task; the rest of the queue is still dispatchable


def per_task_hold(hold: str | None) -> bool:
    return bool(hold) and str(hold).startswith(PER_TASK_HOLDS)


def wip_hold(project: str, task: dict | None = None) -> str | None:
    held = engines.usage_hold()
    if held and task is None:
        return f"usage limit: subscription window exhausted, resets {held}"
    active = [t for t in S.list_tasks(project)
              if t["state"] == "running"
              or (t["state"] == "approved" and t.get("pending_dispatch"))
              or (t["state"] == "blocked" and t.get("pending_resume"))]
    running = [t for t in active if t["state"] == "running"]
    proj = config.project(project)
    l2_engine = None
    if task:
        l2_engine = task.get("l2_engine") or (
            "claude" if task.get("session_id") else _l2_choice(task, proj)["engine"])
    if held and l2_engine != "codex":
        return f"usage limit: subscription window exhausted, resets {held}"
    if task and rule_application(task) and any(rule_application(t) for t in running):
        return "one rule-application task at a time (they edit the same ledger)"
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
    if l2_engine != "codex":
        live = [a for a in engines.claude_agents()
                if a.get("kind") == "background" and a.get("state") not in ("done", "failed", "stopped")]
        if len(live) >= config.SESSIONS_PER_MACHINE:
            return f"session ceiling: {len(live)} live Claude sessions on this machine (cap {config.SESSIONS_PER_MACHINE})"
    if len(active) >= int(proj.get("wip", config.WIP_PER_PROJECT)):
        return f"WIP limit: {len(active)} active or launch-claimed in {project}"
    total = sum(1 for p in config.load_projects() for t in S.list_tasks(p)
                if t["state"] == "running"
                or (t["state"] == "approved" and t.get("pending_dispatch"))
                or (t["state"] == "blocked" and t.get("pending_resume")))
    if total >= config.WIP_PER_MACHINE:
        return f"WIP limit: {total} running on this machine"
    from .monitor import quota_hold, quota
    if not (quota() or {}).get("known"):
        from . import improve  # decision 36: the reserve line cannot be enforced — say so, once a day
        improve.system_fault("quota-unknown", "no fresh statusline or OAuth quota reading: engine balancing cannot measure Claude headroom")
    if l2_engine != "codex":
        q = quota_hold()
        if q:
            return q
    return None


def _poll_codex_task(project: str, task: dict, has_report: bool, live_p: Path) -> dict | None:
    """Translate the exact durable Codex generation into the shared completion contract."""
    record = codex_run(project, task["slug"])
    same_run = record.get("dispatch_id") == task.get("dispatch_id")
    session_id = record.get("session_id") if same_run else None
    if session_id and session_id != task.get("session_id"):
        with S.project_lock(project):
            current = S.load_task(project, task["slug"])
            if current.get("dispatch_id") == task.get("dispatch_id"):
                current["session_id"] = session_id
                S.save_task(project, current)
                task = current
    starting_grace = (same_run and record.get("state") == "starting"
                      and not record.get("pid") and _seconds_since(record.get("started") or "") < 30)
    process_live = same_run and codex_processes_live(record)
    alive = bool(process_live or starting_grace)
    agent = (_codex_agent(record, f"{project}/{task.get('dispatch_id')}", active=alive)
             if same_run else None)
    S.write_json(live_p, {"at": S.now(), "agent": agent, "idle_since": None,
                          "record_state": record.get("state"), "process_live": process_live})
    if alive:
        return None
    if has_report:
        return {"task": task, "agent": agent}
    result = record.get("result") or {}
    error = result.get("error") or (
        f"Codex L2 wrapper pid {record.get('pid')} exited without a current "
        f"{S.task_dir(project, task['slug']) / 'report.json'}"
        if same_run else "Codex L2 has no durable record for the current dispatch"
    )
    return {"task": task, "agent": agent, "died": True, "error": error}


def _current_report(project: str, task: dict) -> bool:
    """A report before the current dispatch/resume generation is history."""
    report = S.task_dir(project, task["slug"]) / "report.json"
    if not report.exists():
        return False
    boundary = task.get("report_not_before")
    if not boundary:
        return True
    try:
        return report.stat().st_mtime >= datetime.fromisoformat(boundary).timestamp()
    except (OSError, TypeError, ValueError):
        return False


def poll(project: str) -> list[dict]:
    """Compare running tasks with their Claude or Codex worker."""
    tasks = S.list_tasks(project)
    needs_claude = any(t["state"] == "running" and task_l2_engine(project, t) == "claude"
                       for t in tasks)
    agents = {a.get("sessionId"): a for a in (engines.claude_agents() if needs_claude else [])}
    by_id = {a.get("id"): a for a in agents.values()}
    finished = []
    for t in tasks:
        has_report = _current_report(project, t)
        if t["state"] == "blocked" and has_report and "idle without a report" in (t.get("blocked_reason") or ""):
            finished.append({"task": t, "agent": None})  # report landed after the idle check: hand it to the verifier
            continue
        if t["state"] != "running":
            continue
        live_p = config.MONITOR_DIR / f"live-{project}--{t['slug']}.json"
        if task_l2_engine(project, t) == "codex":
            outcome = _poll_codex_task(project, t, has_report, live_p)
            if outcome:
                finished.append(outcome)
            continue
        a = agents.get(t.get("session_id")) or by_id.get(t.get("agent_id"))
        prev = S.read_json(live_p, {}) or {}
        live = {"status": a.get("status"), "state": a.get("state")} if a else None
        if a and a.get("state") == "blocked":
            if has_report:
                finished.append({"task": t, "agent": a})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None})
                continue
            detail, at = job_detail(a.get("id"))
            lim = engines.usage_limit_in(detail, now=at)
            if lim:
                finished.append({"task": t, "agent": a, "limited": lim})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None, "limited": lim})
            else:
                finished.append({"task": t, "agent": a, "needs_input": True})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None})
            continue
        idle_since = None
        if a and a.get("status") == "idle" and a.get("state") != "done" and not has_report:
            detail, at = job_detail(a.get("id"))
            lim = engines.usage_limit_in(detail, now=at)
            if lim:  # decision 44: the worker is waiting for the window, not for a human
                finished.append({"task": t, "agent": a, "limited": lim})
                S.write_json(live_p, {"at": S.now(), "agent": live, "idle_since": None, "limited": lim})
                continue
            idle_since = prev.get("idle_since") or S.now()
        died = a is not None and a.get("state") == "failed" and not has_report
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
    """Self-deploy (decision 43): when a project's checkout *is* the deployment — Altitude's own repo — fast-forward it to
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
        from . import improve
        improve.system_fault("self-deploy", f"{project}: {e}", project=project, task=task.get("slug"))
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
                                        f"`systemctl --user restart altitude` when convenient; L2 workers survive it (decision 42).")
        notes.append(f"restart pending ({len(code)} code files)")
    return notes


class CleanupResult(list):
    """List-compatible cleanup notes plus the completion evidence the daemon must persist."""

    def __init__(self, notes=(), *, complete: bool, pending: list[str] | None = None, branches=()):
        super().__init__(notes)
        self.complete = bool(complete)
        self.pending = list(dict.fromkeys(pending or []))
        self.branches = sorted(set(branches))

    def durable_status(self) -> dict:
        return {"complete": self.complete, "pending": self.pending, "branches": self.branches}


def cleanup_after_done(project: str, task: dict) -> CleanupResult:
    """After `done`, remove only this task's merged L2 and completed L1/reviewer worktrees.

    Ownership comes from the task's persisted L2 path and L1/reviewer records, before any session or git-state guard is
    applied. An unfinished owned record is an expected deferral: it is logged and returned in the notes even if git
    cannot list the worktree, and it never raises. `pull_after_done` is attempted after both normal cleanup and
    fail-closed early returns caused by Git fetch/list or Claude-session lookup failures.

    The return remains list-compatible for existing callers, while `complete` and `pending` tell the daemon whether it
    has durable evidence to stamp the task cleaned. Orphan reclamation remains outside this task-owned pass."""
    import subprocess
    from . import improve
    repo = config.project_path(project)
    slug = task.get("slug") or ""
    notes = []
    pending = []
    retry_branches = set((task.get("cleanup_retry") or {}).get("branches") or [])

    def wait_for(reason: str) -> None:
        if reason not in pending:
            pending.append(reason)

    def result() -> CleanupResult:
        return CleanupResult(notes, complete=not pending and not retry_branches,
                             pending=pending, branches=retry_branches)


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
                    candidate = own_candidates.setdefault(key, {"kind": kind, "unfinished": False})
                    if kind == "L2":
                        candidate["kind"] = "L2"
                    if not rec.get("done"):
                        candidate["unfinished"] = True

    # Deferral comes from persisted ownership, not from git's transient view. Record every unfinished L1/reviewer even
    # when its worktree is absent from (or cannot be read through) `git worktree list`.
    deferred_keys = set()
    for key, candidate in own_candidates.items():
        if candidate["unfinished"]:
            reason = "persisted L1 record has no done stamp"
            S.append_event(project, slug, "cleanup-worktree", action="deferred", worktree=key, reason=reason)
            notes.append(f"deferred worktree {Path(key).name}: {reason}")
            wait_for(reason)
            deferred_keys.add(key)

    def finish_after_failure(reason: str) -> CleanupResult:
        for key in own_candidates:
            if key not in deferred_keys:
                S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=key, reason=reason)
        notes.append(f"skipped worktree cleanup: {reason}")
        wait_for(reason)
        notes.extend(pull_after_done(project, task))
        return result()

    try:
        fetch = subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=str(repo), capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as e:
        improve.system_fault("cleanup-git", f"{project}: {e}", project=project, task=slug)
        return finish_after_failure(f"git fetch failed: {e}")
    if fetch.returncode != 0:
        error = (fetch.stderr or fetch.stdout).strip()[:120] or "git fetch failed"
        reason = f"could not refresh origin/main: {error}"
        improve.system_fault("cleanup-fetch", f"{project}: {reason}", project=project, task=slug)
        return finish_after_failure(reason)
    from . import l1
    isolated_notes, isolated_pending = l1.cleanup_isolated_clones(project, task)
    notes.extend(isolated_notes)
    for reason in isolated_pending:
        wait_for(reason)
    try:
        listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(repo), capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        improve.system_fault("cleanup-git", f"{project}: {e}", project=project, task=slug)
        return finish_after_failure(f"git worktree list failed: {e}")
    if listed.returncode != 0:
        error = (listed.stderr or listed.stdout).strip()[:120] or "git worktree list failed"
        improve.system_fault("cleanup-git", f"{project}: {error}", project=project, task=slug)
        return finish_after_failure(f"git worktree list failed: {error}")

    records = []
    all_listed_keys = set()
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
                all_listed_keys.add(key)
                if key in own_candidates:  # Ownership is the first candidate filter.
                    records.append((wt, branch, locked, key, own_candidates[key]))
            wt, branch, locked = None, None, False

    registered_keys = {record[3] for record in records}
    for key in set(own_candidates) - registered_keys - deferred_keys:
        if Path(key).exists():
            reason = "owned path exists but is absent from git worktree list"
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=key, reason=reason)
            notes.append(f"skipped worktree {Path(key).name}: {reason}")
            wait_for(reason)
        else:
            S.append_event(project, slug, "cleanup-worktree", action="absent", worktree=key,
                           reason="owned worktree is already absent")

    eligible = []
    for wt, branch, locked, key, candidate in records:
        if key in deferred_keys:
            continue
        if owners.get(key, set()) != {slug}:
            reason = "also owned by task(s): " + ", ".join(sorted(owners[key] - {slug}))
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            wait_for(reason)
        elif locked:
            reason = "git worktree is locked"
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            wait_for(reason)
        else:
            eligible.append((wt, branch, candidate))

    claude_rows = []
    live = []
    task_agent = None
    task_agent_live = False
    if task_l2_engine(project, task) == "claude":
        try:
            claude_rows = engines.claude_agents()
            live = [path_key(a["cwd"]) for a in claude_rows
                    if a.get("cwd") and a.get("state") not in ("failed", "done", "stopped")]
            task_agent = next((a for a in claude_rows if a.get("id") == task.get("agent_id")), None)
            task_agent_live = bool(task_agent and task_agent.get("state") not in ("failed", "done", "stopped"))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            reason = f"live Claude session list unavailable: {e}"
            improve.system_fault("cleanup-agents", f"{project}: cannot list live sessions, removing nothing: {e}", project=project, task=slug)
            for wt, _branch, _candidate in eligible:
                S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree cleanup: {e}")
            wait_for(reason)
            notes.extend(pull_after_done(project, task))
            return result()

    def has_live_claude_session(path: str) -> bool:
        key = path_key(path)
        return any(key == cwd or key.startswith(cwd + "/") or cwd.startswith(key + "/") for cwd in live)

    # A terminal Claude row can survive after its worktree has already disappeared. Remove that row only when both
    # Git and the filesystem prove its path absent; an exact live row remains a retryable deferral even without a cwd.
    l2_keys = [key for key, candidate in own_candidates.items() if candidate["kind"] == "L2"]
    agent_cwd = path_key(task_agent["cwd"]) if task_agent and task_agent.get("cwd") else None
    l2_absent = bool(l2_keys) and all(key not in all_listed_keys and not Path(key).exists() for key in l2_keys)
    agent_cwd_absent = bool(agent_cwd) and agent_cwd not in all_listed_keys and not Path(agent_cwd).exists()
    if task_agent_live and (l2_absent or agent_cwd_absent):
        reason = "live Claude session still owns the completed task"
        S.append_event(project, slug, "cleanup-agent", action="deferred", agent_id=task.get("agent_id"), reason=reason)
        notes.append(f"deferred Claude agent {task.get('agent_id')}: {reason}")
        wait_for(reason)
    elif task_agent and not task_agent_live and (l2_absent or (not l2_keys and agent_cwd_absent)):
        try:
            rm_note = engines.claude_rm(task["agent_id"])
        except (subprocess.SubprocessError, OSError, RuntimeError) as e:
            reason = f"claude rm failed: {e}"
            improve.system_fault("cleanup-claude-rm", f"{project}/{slug}: {reason}", project=project, task=slug)
            S.append_event(project, slug, "cleanup-agent", action="skipped", agent_id=task.get("agent_id"), reason=reason)
            wait_for(reason)
        else:
            S.append_event(project, slug, "cleanup-agent", action="removed", agent_id=task.get("agent_id"),
                           reason="terminal Claude row had no remaining worktree")
            notes.append(f"claude rm {task['agent_id']}: {(rm_note or 'completed')[:120]}")

    attempted_branches = set()
    for wt, branch, candidate in eligible:
        reason = None
        if has_live_claude_session(wt) or (candidate["kind"] == "L2" and task_agent_live):
            reason = "live Claude session is using the worktree"
        elif not branch:
            reason = "git worktree has no branch"
        else:
            try:
                ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=str(repo),
                                          capture_output=True, text=True, timeout=30)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"merge-base indeterminate: {e}"
                improve.system_fault("cleanup-merge-base", f"{project}/{slug} {branch}: {reason}", project=project, task=slug)
            if not reason and ancestry.returncode == 1:
                reason = "branch has commits not on origin/main"
            elif not reason and ancestry.returncode != 0:
                stderr = (ancestry.stderr or "").strip()[:120] or "(empty stderr)"
                reason = f"merge-base indeterminate (exit {ancestry.returncode}); stderr: {stderr}"
                improve.system_fault("cleanup-merge-base", f"{project}/{slug} {branch}: {reason}", project=project, task=slug)
        if reason:
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"skipped worktree {Path(wt).name}: {reason}")
            wait_for(reason)
            continue
        attempted_branches.add(branch)
        removal_reason = "task-owned branch is merged into origin/main"
        used_claude_rm = (candidate["kind"] == "L2" and bool(task.get("agent_id"))
                          and task_l2_engine(project, task) == "claude")
        if used_claude_rm:
            try:
                rm_note = engines.claude_rm(task["agent_id"])
            except (subprocess.SubprocessError, OSError, RuntimeError) as e:
                reason = f"claude rm failed: {e}"
                improve.system_fault("cleanup-claude-rm", f"{project}/{slug}: {reason}", project=project, task=slug)
            else:
                notes.append(f"claude rm {task['agent_id']}: {(rm_note or 'completed')[:120]}")
        if not reason:
            try:
                rm = subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=str(repo), capture_output=True,
                                    text=True, timeout=60)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"git worktree remove failed: {e}"
            else:
                if rm.returncode != 0:
                    error = (rm.stderr or rm.stdout).strip()[:120] or f"exit {rm.returncode}"
                    reason = f"git worktree remove failed: {error}"
            if reason:
                improve.system_fault("cleanup-worktree-remove", f"{project}/{slug} {wt}: {reason}",
                                     project=project, task=slug)
        if not reason:
            try:
                deleted = subprocess.run(["git", "branch", "-D", branch], cwd=str(repo), capture_output=True, text=True,
                                         timeout=30)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"git branch delete failed after worktree removal: {e}"
            else:
                if deleted.returncode != 0:
                    error = (deleted.stderr or deleted.stdout).strip()[:120] or f"exit {deleted.returncode}"
                    reason = f"git branch delete failed after worktree removal: {error}"
            if reason:
                improve.system_fault("cleanup-branch-delete", f"{project}/{slug} {branch}: {reason}",
                                     project=project, task=slug)
        if reason:
            S.append_event(project, slug, "cleanup-worktree", action="skipped", worktree=wt, reason=reason)
            notes.append(f"could not remove {Path(wt).name}: {reason}")
            wait_for(reason)
            retry_branches.add(branch)
            continue
        retry_branches.discard(branch)
        if used_claude_rm:
            removal_reason += "; L2 agent removed via claude rm"
        S.append_event(project, slug, "cleanup-worktree", action="removed", worktree=wt, reason=removal_reason)
        notes.append(f"removed merged worktree {Path(wt).name}")

    for branch in sorted(retry_branches - attempted_branches):
        reason = None
        try:
            ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=str(repo),
                                      capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as e:
            reason = f"merge-base indeterminate: {e}"
        else:
            if ancestry.returncode == 1:
                reason = "branch has commits not on origin/main"
            elif ancestry.returncode != 0:
                stderr = (ancestry.stderr or "").strip()[:120] or "(empty stderr)"
                reason = f"merge-base indeterminate (exit {ancestry.returncode}); stderr: {stderr}"
        if not reason:
            try:
                deleted = subprocess.run(["git", "branch", "-D", branch], cwd=str(repo), capture_output=True,
                                         text=True, timeout=30)
            except (subprocess.SubprocessError, OSError) as e:
                reason = f"git branch delete failed after worktree removal: {e}"
            else:
                if deleted.returncode != 0:
                    error = (deleted.stderr or deleted.stdout).strip()[:120] or f"exit {deleted.returncode}"
                    reason = f"git branch delete failed after worktree removal: {error}"
        if reason:
            wait_for(reason)
            notes.append(f"deferred branch {branch}: {reason}")
        else:
            retry_branches.discard(branch)
            notes.append(f"removed merged branch {branch}")
    notes.extend(pull_after_done(project, task))
    return result()
