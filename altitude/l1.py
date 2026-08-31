"""L1 runs (decisions 45, 47): `alt l1 run` starts an implementer or a reviewer on either engine, detached, in its own
worktree; `alt l1 wait` collects the result. The L2 no longer spawns L1s through Claude Code's Agent tool, so the
engine is Altitude's choice (by quota), the in-flight cap is enforced here, and every run leaves a record. Raw stream
artifacts are local diagnostic evidence: cite their paths, never paste their contents into a PR, issue, or report."""
from __future__ import annotations
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, engines, git_policy, improve, route, state as S, tasks as T
from hooks import launch_counter as LC
from .alt_broker import AltBroker, BrokerDenied, l2_policy

RESULT_RE = re.compile(r"^RESULT:\s*(.+)$", re.M)
PR_RE = re.compile(r"(?:pull/|#)(\d+)")
# Compatibility name for existing callers and tests; engines owns the dependency-neutral source of truth.
CODEX_PATCH_NOTE = engines.CODEX_PATCH_NOTE
FOOTER = ("\n\n---\nWhen you are finished, print exactly one final line `RESULT: <PR number or URL, or 'no PR'> — <one sentence on what "
          "landed or why you stopped>`. Do not merge. Do not spawn agents or subagents.")
POLL = 5
# Raw engine artifacts are capped at 2 MiB per stream. Truncated files retain both ends and state the exact byte drop.
RAW_OUTPUT_CAP = engines.RAW_CAPTURE_CAP
# A launch is billed only once the engine process itself ran: an exec that never happened raises, and a wrapper that
# started and then failed to find the engine leaves 127 behind. Neither is a session, so neither is charged.
_EXIT_127 = re.compile(r"\bexit(?:\s+code)?\s+127\b")
_CODEX_SANDBOX_MARKERS = ("uid map", "loopback", "RTM_NEWADDR", "Operation not permitted")
_SANDBOX_WORDS = ("bwrap", "bubblewrap", "sandbox", "landlock", "seccomp")
_DENIAL_WORDS = ("denied", "not permitted", "permission", "blocked", "refused", "could not create", "cannot create")
_ACTIVE_STATES = {"reserved", "preparing", "launching", "starting", "running", "stopping", "stop-failed"}
_LAUNCH_STATES = {"reserved", "preparing", "launching"}
_LAUNCH_CLAIM_TIMEOUT = 600  # clone/fetch is capped at 300s; reclaim only after any child must have finished


def _cap_raw_output(output: str | bytes | None) -> tuple[bytes, bool]:
    data = output if isinstance(output, bytes) else (output or "").encode("utf-8", errors="replace")
    return engines.cap_raw(data, RAW_OUTPUT_CAP)


def _codex_sandbox_denial(output: str | None) -> str | None:
    """The raw kernel line, on the paths where the engine surfaces one."""
    for line in (output or "").splitlines():
        if "bwrap:" in line and any(marker in line for marker in _CODEX_SANDBOX_MARKERS):
            return line.strip()[:300]
    return None


def _codex_sandbox_stop(text: str | None) -> str | None:
    """The shape I-055 actually left behind. Codex does not surface the bwrap line to Altitude: on the real
    incident `error` was None and the returncode 0, and the only evidence was the worker's own account of why
    it stopped. Only consulted for a run that produced no PR, and a verbatim echo of CODEX_PATCH_NOTE is
    stripped first, so neither a landed run that merely discusses the sandbox nor the note itself can trigger it."""
    hay = (text or "").replace(CODEX_PATCH_NOTE, " ")
    low = hay.lower()
    if not any(w in low for w in _SANDBOX_WORDS):
        return None
    if not any(w in low for w in _DENIAL_WORDS):
        return None
    return hay.strip()[:300]


def runs_dir(project: str, slug: str) -> Path:
    d = S.task_dir(project, slug) / "l1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(project: str, slug: str, name: str) -> dict | None:
    return S.read_json(runs_dir(project, slug) / f"{name}.json", None)


def save(project: str, slug: str, rec: dict) -> None:
    S.write_json(runs_dir(project, slug) / f"{rec['name']}.json", rec)


def list_runs(project: str, slug: str) -> list[dict]:
    recs = [S.read_json(p, {}) or {} for p in runs_dir(project, slug).glob("*.json")]
    return sorted((r for r in recs if r.get("name")), key=lambda r: r.get("n", 0))


def _git(cwd: Path, *args: str, honor_authority: bool = False) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if not honor_authority:
        env.pop("GIT_DIR", None); env.pop("GIT_WORK_TREE", None)
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60, env=env)


def _isolated_git_env(private_home: Path) -> dict[str, str]:
    """Host-Git boundary used while materializing an untrusted L1 parent.

    Neither the project nor the caller's global/system configuration may
    supply hooks, fsmonitor, credential helpers, filters or custom protocols
    during clone/checkout.  Versioned attributes remain data, but named
    filters have no configured command to execute on the host.
    """
    env = engines.clean_env()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env.update({
        "HOME": str(private_home), "XDG_CONFIG_HOME": str(private_home),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "protocol.ext.allow", "GIT_CONFIG_VALUE_2": "never",
    })
    return env


def _isolated_git(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=300, env=env, stdin=subprocess.DEVNULL)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proc_start_time(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        tail = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


def _group_members(pgid: int, *, exclude: int | None = None) -> list[dict]:
    members = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return [{"pid": pgid, "pid_start": _proc_start_time(pgid), "indeterminate": True}]
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == exclude:
            continue
        try:
            tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            state, process_group, started = tail[0], int(tail[2]), int(tail[19])
        except (OSError, ValueError, IndexError):
            continue
        if process_group == pgid and state != "Z":
            members.append({"pid": pid, "pid_start": started})
    return members


def _reap_wrapper_descendants(*, grace: float = 0.5) -> list[dict]:
    """Prove the subreaper-owned generation has no descendants, even after ``setsid``."""
    wrapper_pid = os.getpid()
    started = _proc_start_time(wrapper_pid)
    if started is None:
        return [{"pid": wrapper_pid, "pid_start": None, "indeterminate": True}]
    members = engines.reap_generation_descendants(wrapper_pid, started, grace=grace)
    if members is None:
        return [{"pid": wrapper_pid, "pid_start": started, "indeterminate": True}]
    return [{"pid": pid, "pid_start": int(value)} for pid, value in sorted(members.items())]


def _exact_alive(pid: int | None, started: int | None) -> bool:
    observed = _proc_start_time(pid)
    if not pid or started is None or observed != started:
        return False
    try:
        state = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _kill_exact(pid: int | None, started: int | None, *, grace: float = 2.0) -> bool:
    """Stop only the persisted process generation; PID reuse is treated as already gone."""
    if not _exact_alive(pid, started):
        return True
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _exact_alive(pid, started):
            return True
        time.sleep(0.05)
    try:
        os.killpg(int(pid), signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _exact_alive(pid, started):
            return True
        time.sleep(0.05)
    return not _exact_alive(pid, started)


def _kill_run_group(rec: dict, *, grace: float = 2.0) -> bool:
    """Kill and prove empty the persisted wrapper PGID, including unrecorded surviving descendants."""
    isolation_pid = rec.get("isolation_pid")
    isolation_start = rec.get("isolation_pid_start")
    if isolation_pid:
        # The exact Bubblewrap leader anchors a private PID namespace. Killing
        # it is a kernel guarantee that no setsid/double-fork descendant of this
        # generation remains executable.
        return bool(isolation_start) and _kill_exact(isolation_pid, isolation_start, grace=grace)
    wrapper_pid = rec.get("wrapper_pid") or rec.get("pid")
    identities = [(wrapper_pid, rec.get("wrapper_pid_start")),
                  (rec.get("engine_pid"), rec.get("engine_pid_start"))]
    identities.extend((item.get("pid"), item.get("pid_start"))
                      for item in (rec.get("descendants") or []) if isinstance(item, dict))
    if any(pid and started is None and _alive(pid) for pid, started in identities):
        return False
    known_live = [(int(pid), int(started)) for pid, started in identities if _exact_alive(pid, started)]
    if not wrapper_pid:
        return not known_live

    def owned_members() -> list[dict] | None:
        members = _group_members(int(wrapper_pid))
        if any(member.get("indeterminate") for member in members):
            return None
        leader = next((member for member in members if member.get("pid") == int(wrapper_pid)), None)
        if leader is not None and leader.get("pid_start") != rec.get("wrapper_pid_start"):
            return None  # the old group is gone and its numeric leader PID was reused
        return members

    members = owned_members()
    if members is None:
        return False
    if not members:
        return not known_live
    try:
        os.killpg(int(wrapper_pid), signal.SIGTERM)
    except ProcessLookupError:
        members = owned_members()
        return members == []
    except OSError:
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        members = owned_members()
        if members == []:
            return True
        if members is None:
            return False
        time.sleep(0.05)
    members = owned_members()
    if members == []:
        return True
    if members is None:
        return False
    try:
        os.killpg(int(wrapper_pid), signal.SIGKILL)
    except ProcessLookupError:
        members = owned_members()
        return members == []
    except OSError:
        return False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        members = owned_members()
        if members == []:
            return True
        if members is None:
            return False
        time.sleep(0.05)
    return owned_members() == []


def _task_generation_current(project: str, slug: str, dispatch_id: str, name: str, generation: str) -> bool:
    task = S.load_task(project, slug)
    rec = load(project, slug, name) or {}
    return (rec.get("generation") == generation and rec.get("dispatch_id") == dispatch_id
            and rec.get("state") in _ACTIVE_STATES
            and not task.get("block_pending")
            and ((task.get("state") == "running" and task.get("dispatch_id") == dispatch_id)
                 or (rec.get("standalone_review") is True and rec.get("role") == "reviewer"
                     and task.get("state") == "requested" and not task.get("dispatch_id"))))


def _l1_policy(argv: list[str], **kwargs):
    """An L1 receives exactly one trusted capability: landing its own task branch."""
    if not argv or argv[0] != "land":
        raise BrokerDenied("an L1 capability permits only `alt land`")
    if "--merge" in argv:
        raise BrokerDenied("an L1 may open or update its PR but may not merge it")
    return l2_policy(argv, **kwargs)


def _clone_root(project: str, slug: str) -> Path:
    return config.project_dir(project) / "l1-clones" / slug


def _gitdir_root(project: str, slug: str) -> Path:
    return _clone_root(project, slug) / ".gitdirs"


def _canonical_remote_url(repo: Path, value: str) -> str:
    value = value.strip()
    if "://" not in value and not re.match(r"^[^/]+@[^:]+:", value) and not Path(value).is_absolute():
        return str((repo / value).resolve())
    return str(Path(value).resolve()) if Path(value).is_absolute() else value


def _trusted_git(rec: dict, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    git_dir = rec.get("git_dir")
    worktree = rec.get("worktree")
    if not git_dir or not worktree:
        return subprocess.CompletedProcess(["git", *args], 2, "", "missing trusted L1 Git authority")
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(config.HOME),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "/usr/bin/ssh -oBatchMode=yes",
        "GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(worktree),
        # The hooks path deliberately remains the host-owned repository-local
        # value. Broker validation reads and compares that value before every
        # action; overriding it here would hide a corrupted Git directory from
        # the authority proof. Global/system config is still absent, so only
        # the already-installed trusted local hook set can run.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "protocol.ext.allow", "GIT_CONFIG_VALUE_1": "never",
    }
    socket = os.environ.get("SSH_AUTH_SOCK")
    if socket and Path(socket).is_absolute():
        env["SSH_AUTH_SOCK"] = socket
    run_cwd = Path(str(worktree))
    if not run_cwd.exists():
        run_cwd = Path(str(git_dir)).parent
    return subprocess.run(["git", *args], cwd=str(run_cwd), capture_output=True, text=True, timeout=timeout, env=env)


def _trusted_missing_task_trailers(rec: dict, project: str, slug: str, *, head: str = "HEAD") -> list[str]:
    origin_sha = str(rec.get("origin_sha") or "")
    rows = _trusted_git(rec, "rev-list", "--reverse", f"{origin_sha}..{head}")
    if rows.returncode != 0:
        raise BrokerDenied(f"cannot list trusted L1 commits: {(rows.stderr or rows.stdout).strip()[-300:]}")
    missing = []
    fmt = f"%(trailers:key={git_policy.TASK_TRAILER},valueonly,separator=%x00)"
    for sha in (rows.stdout or "").splitlines():
        trailers = _trusted_git(rec, "show", "--quiet", f"--format={fmt}", sha)
        if trailers.returncode != 0:
            raise BrokerDenied(f"cannot read trusted L1 commit provenance for {sha[:12]}")
        values = {value.strip() for value in (trailers.stdout or "").split("\0") if value.strip()}
        if values != {f"{project}/{slug}"}:
            missing.append(sha)
    return missing


def _remove_owned_clone(project: str, slug: str, path: Path, git_dir: Path | None = None) -> None:
    root = _clone_root(project, slug).resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents or resolved.parent.name == ".gitdirs":
        raise T.TransitionError(f"refusing to remove non-owned L1 clone {path}")
    if resolved.exists():
        shutil.rmtree(resolved)
    if git_dir is not None:
        git_root = _gitdir_root(project, slug).resolve()
        resolved_git = git_dir.resolve()
        if resolved_git == git_root or git_root not in resolved_git.parents:
            raise T.TransitionError(f"refusing to remove non-owned L1 Git directory {git_dir}")
        if resolved_git.exists():
            shutil.rmtree(resolved_git)


def _registered_parent_run(task: dict, base: Path, runs: list[dict]) -> dict | None:
    """Return the exact completed L1 generation registered for an explicit fix-round parent."""
    resolved = base.resolve()
    matches = [run for run in runs if run.get("worktree") and Path(str(run["worktree"])).resolve() == resolved]
    if not matches:
        return None
    if len(matches) != 1:
        raise T.TransitionError(f"--cwd {base} has ambiguous L1 ownership records")
    run = matches[0]
    if (not run.get("isolated_clone") or run.get("state") != "done"
            or run.get("dispatch_id") != task.get("dispatch_id") or not run.get("git_dir")):
        raise T.TransitionError("--cwd L1 parent must be a completed isolated clone from the current dispatch generation")
    return run


def _validate_parent(project: str, slug: str, base: Path, runs: list[dict], *, explicit: bool) -> tuple[str, str, str]:
    task = S.load_task(project, slug)
    origin_sha = str(task.get("origin_sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", origin_sha):
        raise T.TransitionError("cannot launch a write-capable L1 without the dispatch generation's immutable origin_sha")
    resolved = base.resolve()
    task_worktree = Path(str(task.get("worktree") or "")).resolve()
    parent_run = _registered_parent_run(task, base, runs) if explicit and resolved != task_worktree else None
    if explicit and resolved != task_worktree and parent_run is None:
        raise T.TransitionError(f"--cwd {base} is not this task's registered L2 or completed L1 worktree")
    if parent_run:
        authority = dict(parent_run)
        expected_git_dir = Path(str(parent_run["git_dir"])).resolve()
        git_root = _gitdir_root(project, slug).resolve()
        if expected_git_dir == git_root or git_root not in expected_git_dir.parents:
            raise T.TransitionError("registered L1 parent Git authority is outside its owned root")
        pointer = _git(base, "rev-parse", "--absolute-git-dir")
        if pointer.returncode != 0 or Path((pointer.stdout or "").strip()).resolve() != expected_git_dir:
            raise T.TransitionError("registered L1 parent checkout Git pointer changed")
    else:
        pointer = _git(base, "rev-parse", "--absolute-git-dir")
        if pointer.returncode != 0 or not (pointer.stdout or "").strip():
            raise T.TransitionError(f"cannot resolve trusted parent Git authority for {base}")
        expected_git_dir = Path((pointer.stdout or "").strip()).resolve()
        authority = {"git_dir": str(expected_git_dir), "worktree": str(resolved), "origin_sha": origin_sha}
    inherited_dir, inherited_tree = os.environ.get("GIT_DIR"), os.environ.get("GIT_WORK_TREE")
    if bool(inherited_dir) != bool(inherited_tree):
        raise T.TransitionError("broker parent authority must provide both GIT_DIR and GIT_WORK_TREE")
    if inherited_dir and (Path(inherited_dir).resolve() != expected_git_dir
                          or Path(str(inherited_tree)).resolve() != resolved):
        raise T.TransitionError("broker parent Git authority does not match the explicit --cwd worktree")

    branch = (_trusted_git(authority, "rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()
    if not branch or branch == "HEAD":
        raise T.TransitionError(f"cannot launch a write-capable L1 from detached or unreadable HEAD in {base}")
    if branch in ("main", "master"):
        raise T.TransitionError(f"cannot launch a write-capable L1 directly on protected branch {branch!r}; use a task worktree")
    dirty = _trusted_git(authority, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0 or (dirty.stdout or "").strip():
        raise T.TransitionError(f"cannot launch a write-capable L1 from a dirty or unreadable parent in {base}")
    project_repo = config.project_path(project)
    if not parent_run:
        base_common = (_trusted_git(authority, "rev-parse", "--git-common-dir").stdout or "").strip()
        repo_common = (_git(project_repo, "rev-parse", "--git-common-dir").stdout or "").strip()
        if not base_common or not repo_common or (base / base_common).resolve() != (project_repo / repo_common).resolve():
            raise T.TransitionError(f"{base} is not a worktree of the {project!r} repository")
    parent_sha = (_trusted_git(authority, "rev-parse", "HEAD").stdout or "").strip()
    exists = _trusted_git(authority, "cat-file", "-e", f"{origin_sha}^{{commit}}")
    ancestry = _trusted_git(authority, "merge-base", "--is-ancestor", origin_sha, parent_sha) if parent_sha else None
    if exists.returncode != 0 or ancestry is None or ancestry.returncode != 0:
        raise T.TransitionError("L1 parent is not descended from the task's immutable origin_sha")
    try:
        missing = _trusted_missing_task_trailers(authority, project, slug, head=parent_sha)
    except BrokerDenied as exc:
        raise T.TransitionError(f"cannot validate L1 parent provenance: {exc}") from exc
    if missing:
        sample = ", ".join(sha[:12] for sha in missing[:5])
        raise T.TransitionError(f"L1 parent has commit(s) without exact `Altitude-Task: {project}/{slug}` provenance: {sample}")
    remote = _git(project_repo, "remote", "get-url", "origin")
    remote_url = _canonical_remote_url(project_repo, (remote.stdout or ""))
    if remote.returncode != 0 or not remote_url:
        raise T.TransitionError(f"cannot resolve authoritative origin URL: {(remote.stderr or remote.stdout).strip()[:300]}")
    if parent_run and _canonical_remote_url(base, str(parent_run.get("origin_url") or "")) != remote_url:
        raise T.TransitionError("registered L1 parent authoritative origin does not match the project origin")
    return parent_sha, origin_sha, remote_url


def _prepare_isolated_clone(project: str, slug: str, name: str, generation: str, base: Path,
                            runs: list[dict], *, explicit: bool) -> tuple[Path, Path, str, str, str, str]:
    parent_sha, origin_sha, remote_url = _validate_parent(project, slug, base, runs, explicit=explicit)
    task = S.load_task(project, slug)
    parent_run = (_registered_parent_run(task, base, runs)
                  if explicit and base.resolve() != Path(str(task.get("worktree") or "")).resolve() else None)
    clone_source = Path(str(parent_run["git_dir"])) if parent_run else base
    root = _clone_root(project, slug)
    git_root = _gitdir_root(project, slug)
    root.mkdir(parents=True, exist_ok=True)
    git_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    clone = root / f"{name}-{generation[:12]}"
    git_dir = git_root / f"{name}-{generation[:12]}.git"
    branch = f"l1/{slug[:30]}-{name}-{generation[:8]}"
    git_env = _isolated_git_env(git_root)
    made = subprocess.run(
        ["git", "clone", "--no-hardlinks", "--no-checkout", "--separate-git-dir", str(git_dir),
         str(clone_source), str(clone)], capture_output=True, text=True, timeout=300,
        env=git_env, stdin=subprocess.DEVNULL)
    if made.returncode != 0:
        raise T.TransitionError(f"isolated L1 clone failed: {(made.stderr or made.stdout).strip()[:300]}")
    try:
        for args, label in (
            (("remote", "set-url", "origin", remote_url), "reset authoritative origin"),
            (("checkout", "-q", "-b", branch, parent_sha), "checkout immutable parent"),
            (("branch", "-f", "main", origin_sha), "pin protected main"),
            (("update-ref", "refs/remotes/origin/main", origin_sha), "pin protected remote main"),
        ):
            result = _isolated_git(clone, git_env, *args)
            if result.returncode != 0:
                raise T.TransitionError(f"cannot {label}: {(result.stderr or result.stdout).strip()[:300]}")
        installed = git_policy.install_hooks(clone)
        git_policy.require_hooks_installed(clone, installed)
    except Exception:
        _remove_owned_clone(project, slug, clone, git_dir)
        raise
    return clone, git_dir, branch, parent_sha, origin_sha, remote_url


def validate_broker_worktree(project: str, slug: str, run: dict | str, cwd: Path, *,
                               dispatch_id: str | None = None) -> dict[str, str]:
    """Revalidate one immutable L1 Git authority immediately before a host broker action."""
    snapshot = dict(run) if isinstance(run, dict) else {}
    name = str(snapshot.get("name") or run)
    rec = load(project, slug, name) or {}
    generation = str(rec.get("generation") or "")
    if snapshot and snapshot.get("generation") != generation:
        raise BrokerDenied("L1 broker record generation changed")
    task = S.load_task(project, slug)
    if (task.get("state") != "running" or task.get("dispatch_id") != rec.get("dispatch_id")
            or (dispatch_id is not None and rec.get("dispatch_id") != dispatch_id)
            or rec.get("generation") != generation
            or rec.get("state") not in (_ACTIVE_STATES | {"done"})):
        raise BrokerDenied("L1 generation is no longer current and running")
    expected_worktree = Path(str(rec.get("worktree") or "")).resolve()
    if cwd.resolve() != expected_worktree:
        raise BrokerDenied("L1 broker cwd is not the exact persisted isolated clone")
    git_dir = Path(str(rec.get("git_dir") or "")).resolve()
    git_root = _gitdir_root(project, slug).resolve()
    if git_dir == git_root or git_root not in git_dir.parents or not git_dir.is_dir():
        raise BrokerDenied("L1 trusted Git directory is missing or outside its owned root")
    pointer = _git(cwd, "rev-parse", "--absolute-git-dir")
    if pointer.returncode != 0 or Path((pointer.stdout or "").strip()).resolve() != git_dir:
        raise BrokerDenied("L1 checkout Git pointer changed")
    expected_origin = str(rec.get("origin_url") or "")
    expected_hooks = Path(str(rec.get("hooks_path") or "")).resolve()
    remote = _trusted_git(rec, "config", "--get", "remote.origin.url")
    hooks = _trusted_git(rec, "config", "--path", "--get", "core.hooksPath")
    branch = _trusted_git(rec, "symbolic-ref", "--quiet", "--short", "HEAD")
    if remote.returncode != 0 or _canonical_remote_url(cwd, remote.stdout or "") != expected_origin:
        raise BrokerDenied("L1 authoritative origin URL changed")
    configured_hooks = Path((hooks.stdout or "").strip())
    if not configured_hooks.is_absolute():
        configured_hooks = cwd / configured_hooks
    if hooks.returncode != 0 or configured_hooks.resolve() != expected_hooks:
        raise BrokerDenied("L1 hardened hook path changed")
    if branch.returncode != 0 or (branch.stdout or "").strip() != rec.get("branch"):
        raise BrokerDenied("L1 task branch changed")
    origin_sha = str(rec.get("origin_sha") or "")
    if task.get("origin_sha") != origin_sha:
        raise BrokerDenied("L1 immutable origin does not match the task dispatch")
    values = []
    for ref in ("refs/heads/main", "refs/remotes/origin/main"):
        result = _trusted_git(rec, "rev-parse", "--verify", ref)
        if result.returncode != 0:
            raise BrokerDenied(f"L1 protected ref {ref} is missing")
        values.append((result.stdout or "").strip())
    if values != [origin_sha, origin_sha]:
        raise BrokerDenied("L1 protected refs moved from the immutable dispatch origin")
    parent_sha = str(rec.get("parent_sha") or "")
    if (_trusted_git(rec, "merge-base", "--is-ancestor", origin_sha, parent_sha).returncode != 0
            or _trusted_git(rec, "merge-base", "--is-ancestor", parent_sha, "HEAD").returncode != 0):
        raise BrokerDenied("L1 branch is disconnected from its immutable parent provenance")
    missing = _trusted_missing_task_trailers(rec, project, slug)
    if missing:
        raise BrokerDenied("L1 branch contains commits without exact task provenance")
    latest = load(project, slug, name) or {}
    latest_task = S.load_task(project, slug)
    if (latest_task.get("state") != "running"
            or latest_task.get("dispatch_id") != rec.get("dispatch_id")
            or (dispatch_id is not None and latest_task.get("dispatch_id") != dispatch_id)
            or latest.get("generation") != generation or latest.get("dispatch_id") != rec.get("dispatch_id")
            or latest.get("state") not in (_ACTIVE_STATES | {"done"})):
        raise BrokerDenied("L1 generation changed during broker provenance validation")
    return {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(expected_worktree),
            "ALTITUDE_TRUSTED_REMOTE_URL": expected_origin}


def _validate_l1_broker(project: str, slug: str, run: dict, cwd: Path, dispatch_id: str) -> dict[str, str]:
    """Hold the task lock across the final L1 capability provenance and generation check."""
    with S.project_lock(project):
        return validate_broker_worktree(project, slug, run, cwd, dispatch_id=dispatch_id)

def _finish_reserved_failure(project: str, slug: str, name: str, generation: str, error: str) -> dict:
    token = None
    with S.project_lock(project):
        rec = load(project, slug, name) or {}
        if rec.get("generation") != generation:
            return rec
        token = rec.get("reservation")
        rec.update({"state": "stopped", "lifecycle": "stopped", "done": S.now(),
                    "billed": False,
                    "result": {"error": error, "pr": None, "summary": None}})
        save(project, slug, rec)
    LC.release(config.ROOT, project, slug, token)
    return rec


def _kill_unpersisted(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _launch_wrapper(argv: list[str], *, cwd: Path, log, env: dict) -> subprocess.Popen:
    start_fd = int(env["ALTITUDE_L1_START_FD"])
    child = subprocess.Popen(engines.generation_isolation_command(argv), cwd=str(cwd), stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=env, pass_fds=(start_fd,))
    child.altitude_pid_isolation = True
    return child


def _reap_wrapper_async(child: subprocess.Popen) -> None:
    """Hold and reap a durable fire-and-forget wrapper without blocking its launcher.

    The wrapper's PID/start identity remains the durable authority. The thread only collects
    its eventual exit status so Python does not warn or retain a zombie after ``start`` returns.
    """
    def reap() -> None:
        try:
            child.wait()
        except (ChildProcessError, OSError):
            pass
    threading.Thread(target=reap, name=f"l1-reap-{child.pid}", daemon=True).start()


def _await_parent_start() -> None:
    raw = os.environ.pop("ALTITUDE_L1_START_FD", None)
    if raw is None:
        return
    descriptor = int(raw)
    try:
        os.read(descriptor, 1)  # one byte = persisted; EOF = parent died, child handshake takes ownership
    finally:
        os.close(descriptor)


def _reviewer_git_read_paths(project: str, slug: str, worktree: Path) -> tuple[Path, Path]:
    """Return the exact linked-worktree and common Git metadata roots.

    Reviewers do not receive an isolated-clone ``git_dir`` field. Both engines
    still need read-only access to the registered worktree's Git metadata, so
    derive and validate it from Git rather than indexing implementer-only
    provenance.
    """
    repo = config.project_path(project).resolve(strict=True)
    common = _git(repo, "rev-parse", "--git-common-dir", honor_authority=True)
    if common.returncode != 0:
        raise T.TransitionError("reviewer Git read authority is unavailable")
    common_text = (common.stdout or "").strip()
    if not common_text:
        raise T.TransitionError("reviewer Git read authority returned no metadata path")
    common_path = Path(common_text)
    common_path = common_path if common_path.is_absolute() else repo / common_path
    try:
        common_path = common_path.resolve(strict=True)
    except OSError as exc:
        raise T.TransitionError(f"reviewer Git read authority is unavailable: {exc}") from exc
    resolved = worktree.resolve(strict=True)
    if resolved == repo:
        absolute = _git(repo, "rev-parse", "--absolute-git-dir", honor_authority=True)
        absolute_text = (absolute.stdout or "").strip()
        if absolute.returncode != 0 or not absolute_text:
            raise T.TransitionError("reviewer Git read authority is unavailable")
        absolute_path = Path(absolute_text)
        absolute_path = absolute_path if absolute_path.is_absolute() else repo / absolute_path
        if absolute_path.resolve(strict=True) != common_path:
            raise T.TransitionError("main checkout Git metadata differs from the authoritative common directory")
        return common_path, common_path
    task_worktree_raw = S.load_task(project, slug).get("worktree")
    if not task_worktree_raw or Path(str(task_worktree_raw)).resolve(strict=True) != resolved:
        raise T.TransitionError("reviewer checkout is not the current task worktree")
    # Reuse dispatch's hardened pointer/backpointer, registration, branch and
    # remote proof. A model-writable `.git` pointer must never mint arbitrary
    # host read capability for the reviewer sandbox.
    from . import dispatch
    try:
        linked = dispatch._linked_worktree_gitdir(repo, resolved, f"worktree-{slug}")
    except OSError as exc:
        raise T.TransitionError(f"reviewer Git pointer is unavailable: {exc}") from exc
    return linked.resolve(strict=True), common_path


def start(project: str, slug: str, brief: Path, *, role: str = "implementer", engine: str | None = None,
          model: str | None = None, name: str | None = None, cwd: str | None = None,
          review_pr: int | None = None) -> dict:
    """Atomically reserve and launch one generation-bound L1."""
    if role not in ("implementer", "reviewer"):
        raise T.TransitionError("role must be implementer or reviewer")
    if role == "reviewer" and review_pr is not None and (not isinstance(review_pr, int) or review_pr < 1):
        raise T.TransitionError("reviewer --pr must be a positive PR number")
    if role != "reviewer" and review_pr is not None:
        raise T.TransitionError("--pr is available only for reviewer launches")
    brief = Path(brief)
    if not brief.exists():
        raise T.TransitionError(f"brief not found: {brief}")
    task = S.load_task(project, slug)
    launch_cap = int((task.get("envelope") or {}).get("subagent_launches") or 0)
    # The event ledger is the authoritative bill. A reservation occupies the
    # same slot before routing/preparation and is handed to the durable wrapper.
    with LC.launch_lock(config.ROOT, project, slug):
        ledger = LC.launch_ledger(config.ROOT, project, slug)
        if ledger is None:
            raise T.TransitionError(
                f"L1 launch count for {project}/{slug} cannot be read — refusing an unknown envelope")
        launches, started_names = ledger
        held = LC.reservations(config.ROOT, project, slug)
        if held is None:
            raise T.TransitionError(
                f"L1 launch reservations for {project}/{slug} cannot be read — refusing an unknown envelope")
        committed = launches + len(held)
        if committed >= launch_cap:
            raise T.TransitionError(
                f"L1 launch cap: {committed} run(s) already started (cap {launch_cap}) — "
                "raise the envelope or report blocked")
        runs = list_runs(project, slug)
        held_names = {str(item.get("name")) for item in held if item.get("name")}
        counted = started_names | held_names
        if role == "implementer":
            cap = int((task.get("envelope") or {}).get("l1_in_flight") or 1)
            live = {run["name"] for run in runs
                    if run.get("name") in counted and run.get("role") == "implementer" and not run.get("done")}
            live.update(str(item.get("name")) for item in held
                        if item.get("name") and (item.get("role") == "implementer"
                                                 or str(item.get("name")).startswith("implementer-")))
            if len(live) >= cap:
                raise T.TransitionError(
                    f"L1 cap: {len(live)} implementer(s) in flight (cap {cap}) — "
                    "`alt l1 wait` for one before starting another")
        author = next((run.get("engine") for run in reversed(runs)
                       if run.get("name") in counted and run.get("role") == "implementer"), None)
        used_names = {str(run.get("name")) for run in runs if run.get("name")} | held_names
        sequences = [int(run.get("n") or 0) for run in runs]
        sequences.extend(int(match.group(1)) for used in used_names
                         if (match := re.search(r"-(\d+)$", used)))
        n = max(sequences, default=0) + 1
        run_name = name or f"{role}-{n}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_name):
            raise T.TransitionError("L1 run name must be a simple 1-80 character identifier")
        if run_name in used_names:
            raise T.TransitionError(f"run {run_name!r} already exists")
        token = LC.reserve(config.ROOT, project, slug, name=run_name, role=role)
    try:
        return _spawn(project, slug, brief, task, role=role, engine=engine, model=model,
                      name=run_name, cwd=cwd, n=n, author=author, token=token, runs=runs,
                      review_pr=review_pr)
    except BaseException:
        LC.release(config.ROOT, project, slug, token)
        raise


def _spawn(project: str, slug: str, brief: Path, task: dict, *, role: str, engine: str | None,
           model: str | None, name: str, cwd: str | None, n: int, author: str | None,
           token: str | None, runs: list[dict], review_pr: int | None = None) -> dict:
    """Prepare an isolated authority and start one generation-bound wrapper."""
    with S.project_lock(project):
        current_task = S.load_task(project, slug)
        dispatch_id = current_task.get("dispatch_id")
        standalone_review = (role == "reviewer" and current_task.get("state") == "requested"
                             and not dispatch_id)
        if standalone_review:
            dispatch_id = f"review-{slug}"
        elif current_task.get("state") != "running" or not dispatch_id:
            raise T.TransitionError(f"{slug}: L1 launch requires the current running dispatch generation")
        if (task.get("dispatch_id") and task.get("dispatch_id") != dispatch_id):
            raise T.TransitionError(f"{slug}: task dispatch changed before L1 reservation")
        task = current_task
        choice = route.pick_engine("reviewer" if role == "reviewer" else "l1", forced=engine, task=task,
                                   other_than=author if role == "reviewer" else None)
        generation = secrets.token_hex(16)
        key = ("reviewer" if role == "reviewer" else "l1") + ("_codex" if choice["engine"] == "codex" else "")
        selected_model = model or config.MODELS.get(key)
        rec = {"n": n, "name": name, "role": role, "engine": choice["engine"], "why": choice["why"],
               "model": selected_model, "brief": str(brief), "dispatch_id": dispatch_id,
               "review_pr": review_pr,
               "standalone_review": standalone_review, "generation": generation, "state": "reserved", "lifecycle": "reserved", "started": S.now(),
               "launcher_pid": os.getpid(), "launcher_pid_start": _proc_start_time(os.getpid()),
               "wrapper_pid": None, "wrapper_pid_start": None, "isolation_pid": None,
               "isolation_pid_start": None, "engine_pid": None, "engine_pid_start": None,
               "pid": None, "billed": False, "reservation": token, "done": None, "result": None}
        save(project, slug, rec)


    run_name = name
    base = Path(cwd) if cwd else Path(task.get("worktree") or config.project_path(project))
    clone = None
    git_dir = None
    try:
        with S.project_lock(project):
            current = load(project, slug, run_name) or {}
            if current.get("generation") != generation:
                raise T.TransitionError("L1 reservation was replaced")
            current.update({"state": "preparing", "lifecycle": "preparing"})
            save(project, slug, current)
        if role == "implementer":
            clone, git_dir, branch, parent_sha, origin_sha, origin_url = _prepare_isolated_clone(
                project, slug, run_name, generation, base, runs, explicit=bool(cwd))
            workdir = clone
            prepared = {"isolated_clone": True, "parent_sha": parent_sha, "origin_sha": origin_sha,
                        "origin_url": origin_url, "git_dir": str(git_dir),
                        "hooks_path": str(config.HOOKS.resolve()), "base": "main"}
        else:
            workdir = base
            if cwd and workdir.resolve() != Path(task.get("worktree") or "").resolve():
                raise T.TransitionError("reviewer --cwd must be the exact registered task worktree")
            branch = (_git(base, "rev-parse", "--abbrev-ref", "HEAD", honor_authority=True).stdout or "").strip()
            if not branch or branch == "HEAD":
                raise T.TransitionError(f"reviewer worktree is detached or unreadable: {base}")
            prepared = {"isolated_clone": False}
        persona = config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
        prompt = persona.read_text() + "\n\n# Sub-brief\n\n" + brief.read_text()
        if role == "reviewer" and review_pr is not None:
            proof = _gh_pr_view({"worktree": str(workdir), "project": project}, int(review_pr))
            review_head = str(proof.get("headRefOid") or "")
            review_base = str(proof.get("baseRefOid") or "")
            if (proof.get("state") != "OPEN" or proof.get("baseRefName") != "main"
                    or not re.fullmatch(r"[0-9a-fA-F]{40,64}", review_head)
                    or not re.fullmatch(r"[0-9a-fA-F]{40,64}", review_base)):
                raise T.TransitionError(
                    f"PR #{review_pr} is not an open PR to main with one immutable base/head pair")
            rec["review_head_at_start"] = review_head
            rec["review_base_at_start"] = review_base
            rec["review_branch"] = str(proof.get("headRefName") or "")
            prompt += (f"\n\n[altitude] Review authority: PR #{review_pr}, exact base/head "
                       f"{review_base}/{review_head}. Findings apply only to that pair; if either moves, stop.")
        if choice["engine"] == "codex":
            prompt += "\n\n" + CODEX_PATCH_NOTE
        prompt += FOOTER
        (runs_dir(project, slug) / f"{run_name}.prompt.md").write_text(prompt)
        with S.project_lock(project):
            if not _task_generation_current(project, slug, dispatch_id, run_name, generation):
                raise T.TransitionError("task dispatch changed during L1 preparation")
            current = load(project, slug, run_name) or {}
            current.update({"worktree": str(workdir), "branch": branch,
                            "review_head_at_start": rec.get("review_head_at_start"),
                            "review_base_at_start": rec.get("review_base_at_start"),
                            "review_branch": rec.get("review_branch"), **prepared,
                            "state": "launching", "lifecycle": "launching"})
            save(project, slug, current)
    except Exception as exc:
        if clone is not None:
            try:
                _remove_owned_clone(project, slug, clone, git_dir)
            except Exception:
                pass
        _finish_reserved_failure(project, slug, run_name, generation, f"{type(exc).__name__}: {exc}")
        raise

    log = open(runs_dir(project, slug) / f"{run_name}.log", "ab")
    start_read, start_write = os.pipe()
    env = {key: value for key, value in os.environ.items() if key not in ("GIT_DIR", "GIT_WORK_TREE")}
    env.update({"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project,
                "ALTITUDE_TASK": slug, "ALTITUDE_ACTOR": "l1",
                "ALTITUDE_L1_START_FD": str(start_read)})
    try:
        child = _launch_wrapper(
            [sys.executable, str(config.REPO / "bin" / "alt"), "l1", "_exec", slug, run_name],
            cwd=workdir, log=log, env=env)
    except Exception as exc:
        os.close(start_read); os.close(start_write)
        if prepared.get("isolated_clone"):
            _remove_owned_clone(project, slug, workdir, Path(prepared["git_dir"]))
        detail = f"wrapper start failed: {type(exc).__name__}: {exc}"
        _finish_reserved_failure(project, slug, run_name, generation, detail)
        raise T.TransitionError(f"failed to start L1 wrapper: {type(exc).__name__}: {exc}") from exc
    finally:
        log.close()
    os.close(start_read)
    wrapper_start = _proc_start_time(child.pid)
    if wrapper_start is None:
        _kill_unpersisted(child)
        os.close(start_write)
        if prepared.get("isolated_clone"):
            _remove_owned_clone(project, slug, workdir, Path(prepared["git_dir"]))
        _finish_reserved_failure(project, slug, run_name, generation,
                                 "wrapper PID identity was unavailable at launch")
        raise T.TransitionError("wrapper PID identity was unavailable at launch")
    with S.project_lock(project):
        current = load(project, slug, run_name) or {}
        exact = current.get("generation") == generation and current.get("dispatch_id") == dispatch_id
        if exact and current.get("state") == "done":
            stale = False  # a very fast engine completed before its parent persisted wrapper identity
        elif not _task_generation_current(project, slug, dispatch_id, run_name, generation):
            stale = True
        else:
            stale = False
            lifecycle = "running" if current.get("state") == "running" else "starting"
            if getattr(child, "altitude_pid_isolation", False):
                current.update({"isolation_pid": child.pid, "isolation_pid_start": wrapper_start,
                                "pid": child.pid, "state": lifecycle, "lifecycle": lifecycle})
            else:  # compatibility for tests and pre-namespace launch records
                current.update({"wrapper_pid": child.pid, "wrapper_pid_start": wrapper_start,
                                "pid": child.pid, "state": lifecycle, "lifecycle": lifecycle})
            save(project, slug, current)
    if stale:
        _kill_unpersisted(child)
        os.close(start_write)
        if prepared.get("isolated_clone"):
            try:
                _remove_owned_clone(project, slug, workdir, Path(prepared["git_dir"]) if prepared.get("git_dir") else None)
            except Exception:
                pass
        _finish_reserved_failure(project, slug, run_name, generation,
                                 "task dispatch changed while the L1 wrapper started")
        raise T.TransitionError("task dispatch changed while the L1 wrapper started")
    LC.hand_over(config.ROOT, project, slug, token, child.pid)
    try:
        os.write(start_write, b"1")
    except BrokenPipeError:
        pass
    finally:
        os.close(start_write)
    _reap_wrapper_async(child)
    return load(project, slug, run_name) or current


def billed_count(project: str, slug: str, dispatch_id: str | None = None) -> int:
    return sum(1 for run in list_runs(project, slug)
               if run.get("billed") and (dispatch_id is None or run.get("dispatch_id") == dispatch_id))


def _sync_billed_count(project: str, slug: str, dispatch_id: str) -> None:
    count_path = config.MONITOR_DIR / f"counts-{project}--{dispatch_id}.json"
    counts = S.read_json(count_path, {}) or {}
    counts["subagent_launches"] = billed_count(project, slug, dispatch_id)
    S.write_json(count_path, counts)


def _engine_started(project: str, slug: str, name: str, generation: str, dispatch_id: str, pid: int) -> None:
    started = _proc_start_time(pid)
    wrapper_pid = os.getpid()
    wrapper_started = _proc_start_time(wrapper_pid)
    if started is None or wrapper_started is None:
        raise T.TransitionError("L1 engine PID identity was unavailable at launch")
    with S.project_lock(project):
        if not _task_generation_current(project, slug, dispatch_id, name, generation):
            raise T.TransitionError("L1 engine started after its task generation was cancelled")
        rec = load(project, slug, name) or {}
        isolated = bool(rec.get("isolation_pid") and rec.get("isolation_pid_start"))
        if not isolated:
            try:
                process_group = os.getpgid(pid)
            except OSError as exc:
                raise T.TransitionError("L1 engine process-group identity was unavailable at launch") from exc
            if process_group != wrapper_pid:
                raise T.TransitionError("L1 engine did not inherit its exact wrapper process group")
        if (rec.get("wrapper_pid") not in (None, wrapper_pid)
                or rec.get("wrapper_pid_start") not in (None, wrapper_started)):
            raise T.TransitionError("L1 wrapper identity changed before engine persistence")
        if isolated:
            # These identities are in the private PID namespace and must never
            # be compared with or signalled from the daemon's host namespace.
            # The durable host authority is isolation_pid/isolation_pid_start.
            rec.update({"namespace_wrapper_pid": wrapper_pid,
                        "namespace_wrapper_pid_start": wrapper_started,
                        "namespace_engine_pid": pid, "namespace_engine_pid_start": started})
        else:
            rec.update({"wrapper_pid": wrapper_pid, "wrapper_pid_start": wrapper_started,
                        "pid": wrapper_pid, "engine_pid": pid, "engine_pid_start": started})
        rec.update({"billed": True, "state": "running", "lifecycle": "running"})
        save(project, slug, rec)

def engine_started(rec: dict, res: dict, exc: BaseException | None, pid: int | None) -> bool:
    """Whether the engine process itself ran — the handshake a launch has to pass before it is billed.

    Claude hands back its child's pid the moment `Popen` returns, so there the evidence is that the callback fired
    at all: a closed subscription window short-circuits before it, and a failed exec raises instead. Codex exposes
    no such callback, so the evidence is the exit status it came back with. Common to both: an exec that never
    happened raises `FileNotFoundError`/`PermissionError`, and a wrapper that could not find the engine exits 127."""
    if res.get("limited"):
        return False  # a confirmed quota/engine refusal is not a started L1, even when the CLI emitted diagnostics
    if res.get("engine_started") is False:
        return False  # a caller-side preflight failed before the engine process existed
    if rec["engine"] == "claude":
        return pid is not None and not _EXIT_127.search(str(res.get("error") or exc or ""))
    if exc is not None:
        # TimeoutExpired is raised only after subprocess.run successfully spawned the engine. Every other
        # exception defaults to not-started: ENOEXEC and future pre-spawn OSErrors carry no positive handshake.
        return isinstance(exc, subprocess.TimeoutExpired)
    returncode = res.get("returncode")
    if not isinstance(returncode, int) or returncode == 127:
        return False
    return not _EXIT_127.search(str(res.get("error") or exc or ""))


def _settle_launch(project: str, slug: str, rec: dict, started: bool) -> None:
    """Turn the reservation into the authoritative bill, or give it back.

    The `l1-started` event is the bill and the run name is its identity, so this is idempotent: a re-run of the
    same name appends nothing and settles the same number. Nothing else writes `subagent_launches` for a task."""
    token = rec.get("reservation")
    if not started:
        with S.project_lock(project):
            current = load(project, slug, rec["name"]) or {}
            if (current.get("generation") == rec.get("generation")
                    and current.get("dispatch_id") == rec.get("dispatch_id")):
                current["billed"] = False
                save(project, slug, current)
        LC.release(config.ROOT, project, slug, token)
        if rec.get("dispatch_id"):
            _sync_billed_count(project, slug, rec["dispatch_id"])
        return
    cap = int((S.load_task(project, slug).get("envelope") or {}).get("subagent_launches", 0))
    with LC.launch_lock(config.ROOT, project, slug):
        ledger = LC.launch_ledger(config.ROOT, project, slug)
        if ledger is None:
            LC.note_fault(config.ROOT, f"{project}/{slug}: cannot read l1-started events, {rec['name']} not settled")
            return
        if rec["name"] not in ledger[1]:
            S.append_event(project, slug, "l1-started", name=rec["name"], role=rec["role"], engine=rec["engine"],
                           why=rec.get("why"), model=rec.get("model"), dispatch_id=rec.get("dispatch_id"),
                           generation=rec.get("generation"), actor="l2")
        LC.release(config.ROOT, project, slug, token, locked=True)
        key = os.environ.get("ALTITUDE_SESSION_KEY")
        if key:
            LC.settle_counts(config.ROOT, LC.counts_path(config.ROOT, key), project, slug, cap)
    with S.project_lock(project):
        current = load(project, slug, rec["name"]) or {}
        if (current.get("generation") == rec.get("generation")
                and current.get("dispatch_id") == rec.get("dispatch_id")):
            current["billed"] = True
            save(project, slug, current)
    if rec.get("dispatch_id"):
        _sync_billed_count(project, slug, rec["dispatch_id"])


def exec_run(project: str, slug: str, name: str) -> dict:
    """The detached child: run the engine to completion and close the record — always, whatever broke."""
    _await_parent_start()
    rec = load(project, slug, name)
    if not rec:
        raise T.TransitionError(f"no run {name!r}")
    generation, dispatch_id = rec.get("generation"), rec.get("dispatch_id")
    if not generation or not dispatch_id or not _task_generation_current(project, slug, dispatch_id, name, generation):
        return _finish_reserved_failure(project, slug, name, generation or "", "L1 generation is no longer current")
    prompt = (runs_dir(project, slug) / f"{name}.prompt.md").read_text()
    wt = Path(rec["worktree"])
    schema = config.SCHEMAS / "review.json" if rec["role"] == "reviewer" else None
    on_start = lambda pid: _engine_started(project, slug, name, generation, dispatch_id, pid)
    res: dict = {}
    raw_stdout: str | bytes | None = ""
    raw_stderr: str | bytes | None = ""
    raw_stdout_truncated = raw_stderr_truncated = False
    engine_pid: list[int] = []  # claude_print's `on_start`: the child's pid, the moment it exists
    failure: BaseException | None = None
    try:
        engines.enable_generation_subreaper()
        if rec["engine"] == "codex":
            extra = []
            guard = f"python3 {config.HOOKS / 'codex_guard.py'}"
            extra += ["features.hooks=true",
                      f'hooks.PreToolUse=[{{matcher=".*",hooks=[{{type="command",command={json.dumps(guard)},timeout=10}}]}}]']
            kwargs = dict(cwd=wt, sandbox="read-only" if rec["role"] == "reviewer" else "workspace-write",
                          model=rec["model"], timeout=config.L1_TIMEOUT, extra_config=extra, schema=schema,
                          effort=config.CODEX_EFFORT.get(rec["role"]), on_start=on_start,
                          unset_env=("ALTITUDE_HOME",), fault_context={"project": project, "task": slug},
                          bypass_hook_trust=True)
            git_read_paths = ((Path(rec["git_dir"]),) if rec["role"] == "implementer" else
                              _reviewer_git_read_paths(project, slug, wt))
            kwargs.update(permission_role=rec["role"], permission_id=f"{project}/{slug}/{rec['name']}",
                          permission_read_paths=(config.HOOKS, *git_read_paths))
            if rec["role"] == "implementer":
                git_policy.require_hooks_installed(wt, Path(rec["hooks_path"]))
                broker_dir = Path(tempfile.mkdtemp(prefix="alt-l1-broker-"))
                broker = AltBroker(socket_path=broker_dir / "broker.sock", token=secrets.token_urlsafe(32),
                                   project=project, slug=slug, generation=generation, worktree=wt,
                                   trusted_alt=config.REPO / "bin" / "alt", policy=_l1_policy,
                                   validate_generation=lambda: _task_generation_current(
                                       project, slug, dispatch_id, name, generation),
                                   before_cli=lambda argv, cwd: _validate_l1_broker(
                                       project, slug, rec, cwd, dispatch_id),
                                   host_env={"GIT_DIR": str(rec["git_dir"]), "GIT_WORK_TREE": str(wt)}, actor="l1")
                with broker:
                    broker_env, broker_fds = broker.codex_capability()
                    guard_env = {**broker_env, "ALTITUDE_TASK_WORKTREE": str(wt),
                                 "ALTITUDE_REQUIRED_HOOKS": str(rec["hooks_path"]),
                                 "ALTITUDE_TRUSTED_GIT_DIR": str(rec["git_dir"])}
                    res = engines.codex_exec(prompt, extra_env=guard_env, broker_fds=broker_fds,
                                             start_new_session=False, **kwargs)
            else:
                res = engines.codex_exec(prompt, start_new_session=False, **kwargs)
        else:
            def note_claude_start(pid: int) -> None:
                engine_pid.append(pid)
                on_start(pid)
            writable = rec["role"] == "implementer"
            broker = None
            broker_dir = None
            extra_env = {"ALTITUDE_ACTOR": "l1", "ALTITUDE_PROJECT": project,
                         "ALTITUDE_TASK": slug}
            git_paths: tuple[Path, ...]
            if writable:
                git_policy.require_hooks_installed(wt, Path(rec["hooks_path"]))
                broker_dir = Path(tempfile.mkdtemp(prefix="alt-l1-claude-broker-"))
                broker = AltBroker(
                    socket_path=broker_dir / "broker.fifo", token=secrets.token_urlsafe(32),
                    project=project, slug=slug, generation=generation, worktree=wt,
                    trusted_alt=config.REPO / "bin" / "alt", policy=_l1_policy,
                    validate_generation=lambda: _task_generation_current(
                        project, slug, dispatch_id, name, generation),
                    before_cli=lambda argv, cwd: _validate_l1_broker(
                        project, slug, rec, cwd, dispatch_id),
                    host_env={"GIT_DIR": str(rec["git_dir"]), "GIT_WORK_TREE": str(wt)},
                    actor="l1",
                )
                extra_env.update(broker.env())
                git_paths = (Path(rec["git_dir"]),)
            else:
                git_paths = _reviewer_git_read_paths(project, slug, wt)
            settings = engines.claude_worker_settings(
                runs_dir(project, slug) / f"{name}.claude-settings.json",
                cwd=wt, writable=writable, broker_dir=broker_dir,
                git_read_paths=git_paths,
            )

            def invoke_claude() -> dict:
                return engines.claude_print(
                    prompt, cwd=wt, model=rec["model"],
                    permission_mode="plan" if rec["role"] == "reviewer" else "auto",
                    max_turns=config.L1_MAX_TURNS, timeout=config.L1_TIMEOUT, schema=schema,
                    settings=settings, extra_env=extra_env, restricted=True,
                    tools=("Read,Grep,Glob,Bash" if not writable else
                           "Read,Grep,Glob,Bash,Edit,Write,MultiEdit"),
                    on_start=note_claude_start,
                )

            if broker is not None:
                with broker:
                    res = invoke_claude()
            else:
                res = invoke_claude()
        text, err = res.get("text") or "", res.get("error")
        raw_stdout, raw_stderr = res.get("raw_stdout") or "", res.get("raw_stderr") or ""
        raw_stdout_truncated = bool(res.get("raw_stdout_truncated"))
        raw_stderr_truncated = bool(res.get("raw_stderr_truncated"))
    except Exception as e:  # noqa: BLE001 — the record must close with the reason (decision 36)
        failure = e
        text, err = "", f"{type(e).__name__}: {e}"
        raw_stdout = getattr(e, "raw_stdout", getattr(e, "stdout", "")) or ""
        raw_stderr = getattr(e, "raw_stderr", getattr(e, "stderr", "")) or ""
        raw_stdout_truncated = bool(getattr(e, "raw_stdout_truncated", False))
        raw_stderr_truncated = bool(getattr(e, "raw_stderr_truncated", False))
    # The handshake: only now is it known whether an engine ran, so only now is the launch billed or given back.
    try:
        _settle_launch(project, slug, rec, engine_started(rec, res, failure, engine_pid[0] if engine_pid else None))
    except Exception as e:  # noqa: BLE001 — a settlement that breaks must not also lose the run's result
        LC.note_fault(config.ROOT, f"{project}/{slug}: settling {name} failed: {type(e).__name__}: {e}")
    stdout_data, stdout_capped = _cap_raw_output(raw_stdout)
    stderr_data, stderr_capped = _cap_raw_output(raw_stderr)
    run_dir = runs_dir(project, slug)
    raw_paths: dict[str, str | None] = {"stdout": None, "stderr": None}
    for stream, data in (("stdout", stdout_data), ("stderr", stderr_data)):
        path = run_dir / f"{name}.{stream}"
        try:
            path.write_bytes(data)
            raw_paths[stream] = str(path)
        except OSError as e:
            err = f"{err or ''}\nraw {stream} persistence failed: {type(e).__name__}: {e}".strip()
    # `raw` publishes local diagnostic evidence paths only: cite them, never paste their contents into external reports.
    raw_info = ({**raw_paths, "truncated": raw_stdout_truncated or raw_stderr_truncated or stdout_capped or stderr_capped}
                if any(raw_paths.values()) else None)
    persisted_stdout = stdout_data.decode("utf-8", errors="replace")
    persisted_stderr = stderr_data.decode("utf-8", errors="replace")
    m = RESULT_RE.search(text)
    summary = m.group(1).strip() if m else None
    denial = None
    if rec["engine"] == "codex":
        scan_raw_evidence = summary is None or "no pr" in summary.lower() or res.get("returncode") not in (None, 0)
        if scan_raw_evidence:
            denial = _codex_sandbox_denial(persisted_stderr) or _codex_sandbox_denial(persisted_stdout)
            if not denial:
                denial = _codex_sandbox_stop(summary or text[-1500:])
        if not denial:
            denial = _codex_sandbox_denial(err) or _codex_sandbox_denial(text)
        if not denial and res.get("engine_started") is False:
            denial = str(err or "Codex sandbox preflight failed")[:300]
    if denial:
        if res.get("fault_recorded") not in ("codex-sandbox", "codex-permissions"):
            try:
                improve.system_fault(kind="codex-sandbox", detail=denial, project=project, task=slug)
            except Exception as e:  # noqa: BLE001 — a fault raised about a broken run must not break the record too
                err = f"{err or ''}\nsystem_fault failed: {type(e).__name__}: {e}".strip()
        summary = "engine fault: codex-sandbox"
    pr = None
    if summary and "no pr" not in summary.lower():
        pm = PR_RE.search(summary)
        pr = int(pm.group(1)) if pm else None
    head_sha = None
    if rec.get("isolated_clone"):
        head = _trusted_git(rec, "rev-parse", "--verify", "HEAD")
        head_sha = (head.stdout or "").strip() if head.returncode == 0 else None
    reviewed_head_sha = None
    reviewed_base_sha = None
    if rec.get("role") == "reviewer" and rec.get("review_pr") is not None:
        try:
            proof = _gh_pr_view({**rec, "project": project}, int(rec.get("review_pr") or 0))
            current_head = str(proof.get("headRefOid") or "")
            current_base = str(proof.get("baseRefOid") or "")
            if (proof.get("state") != "OPEN" or proof.get("baseRefName") != "main"
                    or current_head != str(rec.get("review_head_at_start") or "")
                    or current_base != str(rec.get("review_base_at_start") or "")):
                raise T.TransitionError(
                    f"PR #{rec.get('review_pr')} base/head moved or closed during review; a fresh reviewer is required"
                )
            reviewed_head_sha = current_head
            reviewed_base_sha = current_base
        except Exception as exc:
            err = f"{err or ''}\nreview head binding failed: {type(exc).__name__}: {exc}".strip()
    descendants = _reap_wrapper_descendants()
    if descendants:
        err = f"{err or ''}\nL1 wrapper descendants survived engine completion".strip()
    result = {"error": err, "pr": pr, "summary": summary, "usage": res.get("usage"),
              "structured": res.get("structured"), "returncode": res.get("returncode"),
              "text_tail": text[-1500:], "raw": raw_info,
              "review_pr": rec.get("review_pr"), "reviewed_head_sha": reviewed_head_sha,
              "reviewed_base_sha": reviewed_base_sha}
    with S.project_lock(project):
        current = load(project, slug, name) or {}
        if (current.get("generation") != generation or current.get("dispatch_id") != dispatch_id
                or current.get("state") in ("stopping", "stopped", "stop-failed")):
            return current
        if descendants:
            current.update({"done": None, "result": result, "head_sha": head_sha or current.get("head_sha"),
                            "state": "stop-failed", "lifecycle": "stop-failed",
                            "descendants": descendants})
        else:
            current.update({"done": S.now(), "result": result, "head_sha": head_sha or current.get("head_sha"),
                            "state": "done", "lifecycle": "done", "engine_pid": None,
                            "engine_pid_start": None, "isolation_pid": None,
                            "isolation_pid_start": None, "namespace_wrapper_pid": None,
                            "namespace_wrapper_pid_start": None, "namespace_engine_pid": None,
                            "namespace_engine_pid_start": None, "descendants": []})
        save(project, slug, current)
    S.append_event(project, slug, "l1-stop-failed" if descendants else "l1-finished", name=name,
                   engine=current["engine"], pr=pr, error=(err or "")[:200],
                   dispatch_id=dispatch_id, generation=generation, actor="l1")
    return current



def stop(project: str, slug: str, name: str, generation: str | None = None) -> bool:
    """Stop one exact wrapper-owned process group, retrying a prior stop-failed record safely."""
    with S.project_lock(project):
        rec = load(project, slug, name) or {}
        if not rec or (generation and rec.get("generation") != generation):
            return True
        if rec.get("state") not in _ACTIVE_STATES:
            return True
        exact_generation = rec.get("generation")
        rec.update({"state": "stopping", "lifecycle": "stopping"})
        save(project, slug, rec)
    stopped = _kill_run_group(rec)
    with S.project_lock(project):
        current = load(project, slug, name) or {}
        if current.get("generation") != exact_generation:
            return True
        if stopped:
            result = current.get("result") or {"error": "L1 stopped before completion", "pr": None, "summary": None}
            current.update({"state": "stopped", "lifecycle": "stopped", "done": current.get("done") or S.now(),
                            "result": result, "wrapper_pid": None, "wrapper_pid_start": None,
                            "isolation_pid": None, "isolation_pid_start": None,
                            "engine_pid": None, "engine_pid_start": None,
                            "namespace_wrapper_pid": None, "namespace_wrapper_pid_start": None,
                            "namespace_engine_pid": None, "namespace_engine_pid_start": None,
                            "descendants": [], "pid": None})
        else:
            current.update({"state": "stop-failed", "lifecycle": "stop-failed"})
        save(project, slug, current)
    S.append_event(project, slug, "l1-stopped" if stopped else "l1-stop-failed", name=name,
                   generation=exact_generation, actor="altd")
    return stopped


def stop_all(project: str, slug: str, dispatch_id: str | None) -> bool:
    """Stop every active exact L1 owned by one task dispatch generation."""
    all_runs = list_runs(project, slug)
    # Pre-generation records cannot be killed safely because they did not persist a PID start identity. Keep the task
    # visible/non-terminal until the legacy process exits; status() then closes it as historical evidence.
    if any(not rec.get("done") and not rec.get("generation") and _alive(rec.get("pid")) for rec in all_runs):
        return False
    runs = [rec for rec in all_runs
            if rec.get("dispatch_id") == dispatch_id and rec.get("state") in _ACTIVE_STATES]
    return all(stop(project, slug, rec["name"], rec.get("generation")) for rec in runs)


def _gh_pr_view(rec: dict, pr: int) -> dict:
    fields = "number,state,mergedAt,headRefName,headRefOid,baseRefName,baseRefOid"
    env = {**os.environ, "GIT_DIR": str(rec.get("git_dir") or ""),
           "GIT_WORK_TREE": str(rec.get("worktree") or "")}
    cwd = Path(str(rec.get("worktree") or ""))
    if not cwd.exists():
        cwd = config.project_path(str(rec.get("project") or "")) if rec.get("project") else Path.cwd()
    result = subprocess.run(["gh", "pr", "view", str(pr), "--json", fields], cwd=str(cwd),
                            capture_output=True, text=True, timeout=60, env=env)
    if result.returncode != 0:
        raise T.TransitionError(f"PR #{pr} evidence unavailable: {(result.stderr or result.stdout).strip()[:300]}")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise T.TransitionError(f"PR #{pr} evidence is malformed") from exc
    if not isinstance(value, dict):
        raise T.TransitionError(f"PR #{pr} evidence is not an object")
    return value


def _squash_merge_proven(rec: dict, project: str, slug: str, head_sha: str) -> bool:
    result = rec.get("result") or {}
    pr = result.get("pr")
    if not isinstance(pr, int):
        return False
    proof_rec = {**rec, "project": project}
    proof = _gh_pr_view(proof_rec, pr)
    return bool(proof.get("state") == "MERGED" and proof.get("mergedAt")
                and proof.get("headRefName") == rec.get("branch")
                and proof.get("headRefOid") == head_sha
                and proof.get("baseRefName") == "main")


def cleanup_isolated_clones(project: str, task: dict) -> tuple[list[str], list[str]]:
    """Remove terminal owned clones only after exact ancestry or exact squash-PR evidence."""
    slug = task.get("slug") or ""
    root = _clone_root(project, slug).resolve()
    git_root = _gitdir_root(project, slug).resolve()
    notes, pending = [], []
    for rec in list_runs(project, slug):
        if not rec.get("isolated_clone"):
            continue
        path = Path(rec.get("worktree") or "")
        git_dir = Path(rec.get("git_dir") or "")
        try:
            resolved, resolved_git = path.resolve(), git_dir.resolve()
        except OSError as exc:
            pending.append(f"isolated clone path unreadable: {exc}")
            continue
        if resolved == root or root not in resolved.parents or resolved_git == git_root or git_root not in resolved_git.parents:
            pending.append(f"isolated clone escaped owned roots: {path}")
            continue
        if not path.exists() and not git_dir.exists():
            notes.append(f"isolated clone already absent: {path.name}")
            continue
        if rec.get("state") in _ACTIVE_STATES or not rec.get("done"):
            pending.append(f"isolated clone {path.name} still has an active L1 generation")
            continue
        if not git_dir.is_dir():
            pending.append(f"isolated clone {path.name} trusted Git directory is missing")
            continue
        remote = _trusted_git(rec, "config", "--get", "remote.origin.url")
        if (remote.returncode != 0
                or _canonical_remote_url(path, remote.stdout or "") != str(rec.get("origin_url") or "")):
            pending.append(f"isolated clone {path.name} authoritative origin changed")
            continue
        fetched = _trusted_git(rec, "fetch", "-q", "origin", "main", timeout=300)
        if fetched.returncode != 0:
            pending.append(f"isolated clone {path.name} origin fetch failed")
            continue
        head_sha = str(rec.get("head_sha") or "")
        if not head_sha:
            head = _trusted_git(rec, "rev-parse", "--verify", f"{rec.get('branch')}^{{commit}}")
            head_sha = (head.stdout or "").strip() if head.returncode == 0 else ""
        if not head_sha:
            pending.append(f"isolated clone {path.name} has no immutable completed head")
            continue
        try:
            missing = _trusted_missing_task_trailers(rec, project, slug, head=head_sha)
        except BrokerDenied as exc:
            pending.append(f"isolated clone {path.name} provenance failed: {exc}")
            continue
        if missing:
            pending.append(f"isolated clone {path.name} completed head lacks exact task provenance")
            continue
        ancestry = _trusted_git(rec, "merge-base", "--is-ancestor", head_sha, "origin/main")
        merged_reason = "task-owned head is merged into origin/main"
        if ancestry.returncode != 0:
            try:
                squash = _squash_merge_proven(rec, project, slug, head_sha)
            except T.TransitionError as exc:
                pending.append(f"isolated clone {path.name} squash evidence failed: {exc}")
                continue
            if not squash:
                pending.append(f"isolated clone {path.name} head is not proven merged into origin/main")
                continue
            merged_reason = "exact task PR/head is proven squash-merged into main"
        try:
            _remove_owned_clone(project, slug, path, git_dir)
        except Exception as exc:
            pending.append(f"isolated clone {path.name} removal failed: {exc}")
            continue
        notes.append(f"removed merged isolated clone {path.name}")
        S.append_event(project, slug, "cleanup-isolated-clone", action="removed", worktree=str(path),
                       branch=rec.get("branch"), reason=merged_reason)
    return notes, pending

def _compact(r: dict) -> dict:
    """Return the L2 view; ``raw`` paths point to local-only diagnostic evidence."""
    res = r.get("result") or {}
    compact = {k: r.get(k) for k in ("name", "role", "engine", "why", "model", "branch", "worktree",
                                            "dispatch_id", "generation", "state", "started", "done")} | {
        "pr": res.get("pr"), "summary": res.get("summary"), "error": res.get("error"), "usage": res.get("usage"),
        "raw": res.get("raw"), "review_pr": res.get("review_pr"),
        "reviewed_head_sha": res.get("reviewed_head_sha"),
        "reviewed_base_sha": res.get("reviewed_base_sha")}
    if r.get("role") != "reviewer":
        return compact
    structured = res.get("structured")
    findings = structured.get("findings") if isinstance(structured, dict) else None
    if not isinstance(findings, list):
        findings = None
    compact["findings"] = findings
    if findings is not None:
        if not findings:
            compact["summary"] = "no findings"
        else:
            counts = {severity: 0 for severity in ("blocking", "major", "minor")}
            other = 0
            for finding in findings:
                severity = finding.get("severity") if isinstance(finding, dict) else None
                if isinstance(severity, str) and severity in counts:
                    counts[severity] += 1
                else:
                    other += 1
            buckets = [f"{counts[severity]} {severity}" for severity in ("blocking", "major", "minor") if counts[severity]]
            if other:
                buckets.append(f"{other} other")
            compact["summary"] = f"{len(findings)} findings: {', '.join(buckets)}"
    elif r.get("done") and not (isinstance(compact["summary"], str) and compact["summary"].strip()) and compact["error"] is None:
        compact["error"] = "reviewer returned no findings and no summary"
    return compact


def _launch_claim_expired(rec: dict) -> bool:
    launcher_pid, launcher_start = rec.get("launcher_pid"), rec.get("launcher_pid_start")
    if launcher_pid and launcher_start is None and _alive(launcher_pid):
        return False  # identity is indeterminate: fail closed rather than reclaim a live launcher
    if _exact_alive(launcher_pid, launcher_start):
        return False
    try:
        started = datetime.fromisoformat(str(rec.get("started") or ""))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() >= _LAUNCH_CLAIM_TIMEOUT


def _stale_launch_cleanup(project: str, slug: str, rec: dict) -> bool:
    if rec.get("role") != "implementer" or not rec.get("generation"):
        return True
    clone = Path(str(rec.get("worktree") or
                     (_clone_root(project, slug) / f"{rec['name']}-{str(rec['generation'])[:12]}")))
    git_dir = Path(str(rec.get("git_dir") or
                       (_gitdir_root(project, slug) / f"{rec['name']}-{str(rec['generation'])[:12]}.git")))
    try:
        _remove_owned_clone(project, slug, clone, git_dir)
        return True
    except Exception:
        return False


def _reconcile_stale_launch(project: str, slug: str, rec: dict) -> dict:
    if not _launch_claim_expired(rec):
        return rec
    with S.project_lock(project):
        current = load(project, slug, rec["name"]) or rec
        if (current.get("generation") != rec.get("generation")
                or current.get("state") not in _LAUNCH_STATES or not _launch_claim_expired(current)):
            return current
        token = current.get("reservation")
        # Expiry proved the exact launcher identity is gone (including PID reuse via start-time mismatch).
        # Release before persisting `stopped`: if the daemon dies between the two, the still-launching record is
        # safely reconciled again; the inverse order could strand a live-PID reservation forever.
        LC.release(config.ROOT, project, slug, token)
        current.update({"state": "stopped", "lifecycle": "stopped", "done": S.now(),
                        "result": {"error": f"L1 launcher died during {current.get('state')} before wrapper persistence",
                                   "pr": None, "summary": None},
                        "reservation": None,
                        "preflight_cleanup_pending": current.get("role") == "implementer"})
        save(project, slug, current)
    cleaned = _stale_launch_cleanup(project, slug, current)
    with S.project_lock(project):
        latest = load(project, slug, current["name"]) or current
        if latest.get("generation") == current.get("generation"):
            latest["preflight_cleanup_pending"] = not cleaned
            save(project, slug, latest)
            current = latest
    S.append_event(project, slug, "l1-launch-recovered", name=current["name"],
                   generation=current.get("generation"), cleanup_pending=not cleaned, actor="altd")
    return current


def status(project: str, slug: str) -> list[dict]:
    out = []
    try:
        current_dispatch = S.load_task(project, slug).get("dispatch_id")
    except KeyError:  # compatibility/read-only views may render historical run records after task archival
        current_dispatch = None
    for r in list_runs(project, slug):
        if (r.get("generation") and r.get("state") in _ACTIVE_STATES
                and r.get("dispatch_id") != current_dispatch):
            stop(project, slug, r["name"], r.get("generation"))
            r = load(project, slug, r["name"]) or r
        if not r.get("state") and not r.get("generation"):
            if r.get("done"):
                r["state"] = "done"
            elif _alive(r.get("pid")):
                r["state"] = "legacy-unfenced"
            else:
                r.update({"state": "done", "done": S.now(),
                          "result": {"error": "legacy L1 process died before finishing (no result)",
                                     "pr": None, "summary": None}})
                save(project, slug, r)
        if r.get("preflight_cleanup_pending"):
            cleaned = _stale_launch_cleanup(project, slug, r)
            if cleaned:
                with S.project_lock(project):
                    current = load(project, slug, r["name"]) or r
                    if current.get("generation") == r.get("generation"):
                        current["preflight_cleanup_pending"] = False
                        save(project, slug, current)
                        r = current
        if r.get("state") in ("stopping", "stop-failed"):
            stop(project, slug, r["name"], r.get("generation"))
            r = load(project, slug, r["name"]) or r
        elif r.get("state") in _LAUNCH_STATES:
            r = _reconcile_stale_launch(project, slug, r)
        elif r.get("state") in ("starting", "running"):
            wrapper_live = (_exact_alive(r.get("isolation_pid"), r.get("isolation_pid_start"))
                            or _exact_alive(r.get("wrapper_pid") or r.get("pid"),
                                            r.get("wrapper_pid_start")))
            engine_live = _exact_alive(r.get("engine_pid"), r.get("engine_pid_start"))
            descendant_live = any(_exact_alive(item.get("pid"), item.get("pid_start"))
                                  for item in (r.get("descendants") or []) if isinstance(item, dict))
            if wrapper_live or engine_live or descendant_live:
                out.append(_compact(r))
                continue
            # A dead wrapper can still have an engine spawned before on_start persisted it. Inspect and reap the
            # exact persisted PGID before closing the record; an indeterminate/non-empty group remains retryable.
            group_empty = _kill_run_group(r)
            with S.project_lock(project):
                current = load(project, slug, r["name"]) or r
                if current.get("generation") == r.get("generation") and current.get("state") in ("starting", "running"):
                    if group_empty:
                        current.update({"state": "done", "lifecycle": "done", "done": S.now(),
                                        "result": {"error": "L1 process died before finishing (no result)",
                                                   "pr": None, "summary": None},
                                        "wrapper_pid": None, "wrapper_pid_start": None, "pid": None,
                                        "isolation_pid": None, "isolation_pid_start": None,
                                        "engine_pid": None, "engine_pid_start": None, "descendants": []})
                    else:
                        current.update({"state": "stop-failed", "lifecycle": "stop-failed"})
                    save(project, slug, current)
                    r = current
            S.append_event(project, slug, "l1-finished" if group_empty else "l1-stop-failed",
                           name=r["name"], engine=r["engine"], pr=None,
                           error="process died" if group_empty else "process group could not be proven empty", actor="altd")
        out.append(_compact(r))
    return out


def wait(project: str, slug: str, name: str | None = None, timeout: int = 540) -> dict:
    """Block until `name` (or any unfinished run) finishes, or `timeout` — then return {waiting: true} so the caller
    calls again (an L2's Bash call is capped at 10 minutes)."""
    deadline = time.time() + timeout
    while True:
        runs = status(project, slug)
        pending = [r for r in runs if not r["done"] and (name is None or r["name"] == name)]
        finished = [r for r in runs if r["done"] and (name is None or r["name"] == name)]
        if name and not pending and not finished:
            raise T.TransitionError(f"no run {name!r}")
        if name and finished:
            return {"waiting": False, "run": finished[0], "runs": runs}
        if name is None and not pending:
            return {"waiting": False, "run": finished[-1] if finished else None, "runs": runs}
        if time.time() >= deadline:
            return {"waiting": True, "pending": [r["name"] for r in pending], "runs": runs}
        time.sleep(POLL)
