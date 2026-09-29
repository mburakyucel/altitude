"""Git boundary checks shared by dispatch, landing, and the service checkout.

The central rule is deliberately small: ``origin/<base>`` is the authority for
new work and agents never write the base branch directly.  Inspection is local
and side-effect free; callers that need a current answer must explicitly use
``fetch_origin`` first.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import os
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tarfile
from typing import Any, Sequence


DEFAULT_BASE = "main"
REQUIRED_HOOKS = ("pre-commit", "pre-merge-commit", "pre-push", "reference-transaction")
# Git's hook events, including events owned only by an existing integration.
HOOK_EVENTS = (*REQUIRED_HOOKS, "applypatch-msg", "pre-applypatch", "post-applypatch",
               "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout",
               "post-merge", "pre-receive", "update", "proc-receive", "post-receive", "post-update",
               "push-to-checkout", "pre-auto-gc", "post-rewrite", "sendemail-validate",
               "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist",
               "p4-pre-submit", "post-index-change")


class GitPolicyError(RuntimeError):
    """A repository state or requested Git operation violates policy."""


class FetchError(GitPolicyError):
    """Fetching from the remote failed: transport, authentication or the remote itself."""


@dataclass(frozen=True)
class RepositoryState:
    """One non-fetching observation of a checkout relative to its remote base."""

    branch: str | None
    dirty: bool | None
    head: str | None
    origin_sha: str | None
    ahead: int | None
    behind: int | None
    local_only_shas: tuple[str, ...]
    oldest_local_sha: str | None
    determinate: bool
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(repo: Path, *args: str, timeout: int = 120, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitPolicyError(f"git {' '.join(args[:3])}: {exc}") from exc


def _output(result: subprocess.CompletedProcess[str], what: str) -> str:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise GitPolicyError(f"{what}: {detail[-300:] or f'exit {result.returncode}'}")
    return (result.stdout or "").strip()


def _git_dir(repo: Path) -> Path:
    raw = _output(_run(repo, "rev-parse", "--absolute-git-dir"), "cannot resolve git directory")
    return Path(raw)


def inspect_repository(
    repo: str | Path,
    base: str = DEFAULT_BASE,
) -> RepositoryState:
    """Inspect ``HEAD`` against the locally cached ``origin/<base>`` without fetching.

    An indeterminate result is returned rather than raised so status surfaces can
    report the fault.  Mutating paths should reject it through one of the strict
    helpers below.
    """
    root = Path(repo).resolve()
    branch: str | None = None
    dirty: bool | None = None
    head_sha: str | None = None
    origin_sha: str | None = None
    ahead: int | None = None
    behind: int | None = None
    local_only: tuple[str, ...] = ()

    try:
        _git_dir(root)
        branch_result = _run(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch_result.returncode == 0:
            branch = (branch_result.stdout or "").strip() or None

        status = _run(root, "status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            raise GitPolicyError(
                f"git status: {(status.stderr or status.stdout or '').strip()[-300:] or f'exit {status.returncode}'}"
            )
        dirty = bool(status.stdout)

        head_sha = _output(
            _run(root, "rev-parse", "--verify", "HEAD^{commit}"),
            "cannot resolve HEAD",
        )
        origin_ref = f"refs/remotes/origin/{base}"
        resolved_origin = _run(root, "rev-parse", "--verify", f"{origin_ref}^{{commit}}")
        if resolved_origin.returncode != 0:
            raise GitPolicyError(f"missing origin/{base}; fetch it before using this checkout")
        origin_sha = (resolved_origin.stdout or "").strip()

        counts = _output(
            _run(root, "rev-list", "--left-right", "--count", f"{origin_sha}...{head_sha}"),
            f"cannot compare HEAD with origin/{base}",
        ).split()
        if len(counts) != 2:
            raise GitPolicyError(f"cannot compare HEAD with origin/{base}: unexpected rev-list output")
        behind, ahead = int(counts[0]), int(counts[1])
        if ahead:
            rows = _output(
                _run(root, "rev-list", "--reverse", f"{origin_sha}..{head_sha}"),
                f"cannot list commits ahead of origin/{base}",
            )
            local_only = tuple(row for row in rows.splitlines() if row)
        return RepositoryState(
            branch=branch,
            dirty=dirty,
            head=head_sha,
            origin_sha=origin_sha,
            ahead=ahead,
            behind=behind,
            local_only_shas=local_only,
            oldest_local_sha=local_only[0] if local_only else None,
            determinate=True,
            error=None,
        )
    except (GitPolicyError, OSError, ValueError) as exc:
        return RepositoryState(
            branch=branch,
            dirty=dirty,
            head=head_sha,
            origin_sha=origin_sha,
            ahead=ahead,
            behind=behind,
            local_only_shas=local_only,
            oldest_local_sha=local_only[0] if local_only else None,
            determinate=False,
            error=str(exc),
        )


def capture_origin_sha(repo: str | Path, base: str = DEFAULT_BASE) -> str:
    """Resolve the currently cached remote base to an immutable commit id."""
    root = Path(repo).resolve()
    result = _run(root, "rev-parse", "--verify", f"refs/remotes/origin/{base}^{{commit}}")
    if result.returncode != 0:
        raise GitPolicyError(f"missing origin/{base}; fetch it before dispatch")
    return (result.stdout or "").strip()


def fetch_origin(repo: str | Path, base: str = DEFAULT_BASE) -> str:
    """Fetch one remote base and return its new immutable commit id."""
    root = Path(repo).resolve()
    env = dict(os.environ, LC_ALL="C")
    try:
        result = _run(root, "fetch", "--no-tags", "origin", base, timeout=300, env=env)
        # #404: another worktree's fetch can win the remote ref's compare-and-swap.
        # Require a fresh successful fetch, never infer success from the cached ref.
        diagnostics = [line for line in (result.stderr or "").splitlines()
                       if re.search(r"\b(?:error|fatal):", line)]
        oid = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
        collision = rf"error: cannot lock ref '{re.escape(f'refs/remotes/origin/{base}')}': is at {oid} but expected {oid}"
        if result.returncode and len(diagnostics) == 1 and re.fullmatch(collision, diagnostics[0]):
            result = _run(root, "fetch", "--no-tags", "origin", base, timeout=300, env=env)
        _output(result, f"git fetch origin {base}")
    except GitPolicyError as exc:
        raise FetchError(str(exc)) from exc
    return capture_origin_sha(root, base)


def _state_error(state: RepositoryState, base: str, *, exact: bool) -> str | None:
    if not state.determinate:
        return state.error or f"cannot determine repository state against origin/{base}"
    if state.branch != base:
        return f"checkout is on {state.branch or 'detached HEAD'}, expected {base}"
    if state.dirty:
        return "checkout has uncommitted changes"
    if state.ahead and state.behind:
        return (
            f"{base} has diverged from origin/{base} "
            f"({state.ahead} ahead, {state.behind} behind; oldest local commit {state.oldest_local_sha})"
        )
    if state.ahead:
        return (
            f"{base} is {state.ahead} commit(s) ahead of origin/{base}; "
            f"oldest local commit {state.oldest_local_sha} must be moved to a PR branch"
        )
    if exact and state.behind:
        return f"{base} is {state.behind} commit(s) behind origin/{base}; fast-forward it before activation or recovery"
    return None


def fetch_and_require_exact_base(repo: str | Path, base: str = DEFAULT_BASE) -> str:
    """Fetch, then require a clean checkout exactly at ``origin/<base>``.

    Used for explicit checkout recovery and activation, never isolated task admission.
    """
    origin_sha = fetch_origin(repo, base)
    state = inspect_repository(repo, base)
    refusal = _state_error(state, base, exact=True)
    if refusal:
        raise GitPolicyError(f"dispatch refused: {refusal}")
    if state.head != origin_sha:
        raise GitPolicyError(f"dispatch refused: {base} is not exactly origin/{base}")
    return origin_sha


def activate_source() -> None:
    """Pin service launch inputs to committed source outside every worker's writable roots."""
    from . import config, platform

    if platform.containerized() or config.RELEASE is not None:
        # Versioned installs already pin code and resources outside project worktrees.
        return
    repo = config.REPO
    head = service_preflight(repo).head
    repair_hooks(repo)
    root = repo / ".altitude-source"
    root.mkdir(exist_ok=True)
    source = root / head
    if not source.exists():
        with tempfile.TemporaryDirectory(dir=root) as staging:
            archive = Path(staging) / "source.tar"
            _output(_run(repo, "archive", "--format=tar", f"--output={archive}", head), "cannot export launch source")
            tree = Path(staging) / "tree"
            tree.mkdir()
            with tarfile.open(archive) as contents:
                contents.extractall(tree, filter="data")
            tree.rename(source)
    current = root / "current"
    link = root / "next"
    link.unlink(missing_ok=True)
    link.symlink_to(head, target_is_directory=True)
    link.replace(current)
    config.SOURCE = source
    config.PERSONAS, config.SCHEMAS = source / "personas", source / "schemas"
    config.TEMPLATES, config.HOOKS = source / "templates", source / "hooks"
    # Later imports use the same committed code as the modules loaded at clean startup.
    sys.modules[__package__].__path__ = [str(source / "altitude")]
    repair_hooks(repo)
    for project in config.load_projects():
        repository = config.project_path(project)
        if repository == repo:
            continue
        try:
            probe = _run(repository, "rev-parse", "--git-dir", env={**os.environ, "LC_ALL": "C"})
            if repository.is_dir() and "not a git repository" in probe.stderr:
                continue  # Conversation-only folders have no applicable Git guards.
            repair_hooks(repository)
        except GitPolicyError as exc:
            from . import incidents
            incidents.system_fault("launch-source", f"{project}: {exc}", project=project)


@contextmanager
def archive_checkout(repo: Path, base_sha: str, branch: str, label: str):
    """Preserve index and working content on a local branch before touching the checkout.

    Issue #247, operator archive decision: two ordinary commits retain staged-only versions in
    the snapshot's parent; the diff from its grandparent is the complete working change.
    """
    with tempfile.TemporaryDirectory(prefix="alt-checkout-", dir=_git_dir(repo)) as temp:
        index = Path(temp) / "index"
        shutil.copyfile(_git_dir(repo) / "index", index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}

        def git(*args: str) -> str:
            return _output(_run(repo, *args, env=env), label)

        staged_tree = git("write-tree")
        git("add", "--all", "--", ".")
        # #247: a gitlink cannot preserve the files inside a dirty submodule or nested repo.
        changes = git("diff", "--raw", "--ignore-submodules=none", base_sha)
        changes += "\n" + git("diff", "--cached", "--raw", "--ignore-submodules=none", base_sha)
        if any(row.startswith(":160000 ") or " 160000 " in row[:15] for row in changes.splitlines()):
            raise GitPolicyError("preserve-checkout refuses changed submodules or nested repositories")
        tree = git("write-tree")
        staged = git("commit-tree", staged_tree, "-p", base_sha, "-m", f"{label}: staged content")
        sha = git("commit-tree", tree, "-p", staged, "-m", f"{label}: working snapshot")
        # Empty old value means create only: even a colliding request must never overwrite an archive.
        git("update-ref", f"refs/heads/{branch}", sha, "")
        yield sha, env


def clean_archived_checkout(repo: Path, sha: str, base_sha: str, env: dict) -> None:
    """Clean only after the branch and task receipt are durable; never recurse into gitlinks."""
    def paths(*args: str) -> set[Path]:
        result = _run(repo, *args)
        _output(result, "inspect restoration paths")
        return {Path(p) for p in result.stdout.split("\0") if p}

    ignored = paths("ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    tracked = paths("ls-tree", "-r", "--name-only", "-z", base_sha)
    # #247 archive review: restore can silently replace an ignored file/directory obstructing HEAD.
    if (ignored & tracked or ignored.intersection(p for f in tracked for p in f.parents)
            or tracked.intersection(p for f in ignored for p in f.parents)):
        raise GitPolicyError("archive retained; ignored files obstruct checkout restoration; inspect before retrying")
    _output(_run(repo, "diff", "--cached", "--quiet", f"{sha}^", "--"),
            "index changed since archive capture; archive retained")
    # Use the captured index's stat data: a later edit refuses the merge instead of being overwritten.
    _output(_run(repo, "read-tree", "-m", "-u", "--no-recurse-submodules", sha, base_sha, env=env),
            "clean preserved checkout")
    _output(_run(repo, "read-tree", base_sha), "restore base index")


def service_preflight(repo: str | Path, base: str = DEFAULT_BASE) -> RepositoryState:
    """Allow a clean service checkout that is equal to or behind its remote base."""
    state = inspect_repository(repo, base)
    refusal = _state_error(state, base, exact=False)
    if refusal:
        raise GitPolicyError(f"service preflight refused: {refusal}")
    return state


def _configured_hooks_path(repo: Path) -> str | None:
    result = _run(repo, "config", "--get", "core.hooksPath")
    if result.returncode == 1:
        return None
    return _output(result, "cannot read core.hooksPath") or None


def _resolve_hooks_path(repo: Path, raw: str) -> Path:
    path = Path(os.path.expanduser(raw))
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _active_hooks() -> Path:
    from . import config, platform
    image = platform.container_git_guards()
    if image is not None:
        return image[0]
    if config.INSTALL_PREFIX is not None:
        return config.INSTALL_PREFIX / "hooks"
    current = config.REPO / ".altitude-source/current/hooks"
    return current if current.is_dir() else config.HOOKS


def _verify_hook_files(desired: Path) -> None:
    missing = [name for name in REQUIRED_HOOKS if not (desired / name).is_file()]
    if missing:
        raise GitPolicyError(f"hook directory {desired} is missing: {', '.join(missing)}")
    not_executable = [name for name in REQUIRED_HOOKS if not os.access(desired / name, os.X_OK)]
    if not_executable:
        raise GitPolicyError(f"hook directory {desired} has non-executable hooks: {', '.join(not_executable)}")


def _owned_hooks(path: Path) -> bool:
    """Recognize only this installation's guard namespace."""
    from . import config, platform
    image = platform.container_git_guards()
    if image is not None:
        return path == image[0].resolve()
    if config.INSTALL_PREFIX is not None:
        return path == (config.INSTALL_PREFIX / "hooks").resolve()
    if path == (config.REPO / "hooks").resolve():
        return True
    source = config.REPO / ".altitude-source"
    try:
        relative = path.relative_to(source.resolve())
    except ValueError:
        return False
    if len(relative.parts) != 2 or relative.parts[1] != "hooks":
        return False
    sha = relative.parts[0]
    return (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
            and _run(config.REPO, "cat-file", "-e", f"{sha}:hooks/pre-commit").returncode == 0)


def _common_git_dir(repo: Path) -> Path:
    common = _output(_run(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"),
                     "cannot resolve shared Git directory")
    return Path(common).resolve()


def _composition_path(repo: Path) -> Path:
    from . import config, platform
    identity = hashlib.sha256(str(_common_git_dir(repo)).encode()).hexdigest()
    image = platform.container_git_guards()
    if image is not None:
        return image[1] / identity
    # #348 review: workers can write .git and runtime state, but cannot grant integration consent.
    # The wrapper generation and its original-selection receipt share the trusted installation boundary.
    root = config.INSTALL_PREFIX if config.INSTALL_PREFIX is not None else config.REPO / ".altitude-source"
    return root / "git-guards" / identity


def _hook_contents(path: Path) -> list[list[str]]:
    return [[p.name, hashlib.sha256(p.read_bytes()).hexdigest()] for p in sorted(path.iterdir())
            if p.name in HOOK_EVENTS and p.is_file() and os.access(p, os.X_OK)] if path.is_dir() else []


def _integration_support(path: Path, contents: list[list[str]], selection: str | None) -> tuple[bool, str]:
    if not path.is_dir():
        return False, "The original hook directory is unavailable; L3 needs to inspect it."
    if selection is not None and not Path(os.path.expanduser(selection)).is_absolute():
        return False, "Relative hook paths can select different hooks across worktrees and events; review integration with L3."
    for name, _ in contents:
        source = (path / name).read_bytes()
        if b"core.hooksPath" in source or b"husky" in source.lower():
            return False, f"The {name} hook uses a hook manager or the hook selection itself; review integration with L3."
    return True, "Existing hooks need your integration choice."


def _composed_hook(name: str, original: Path, guards: Path, contents: list[list[str]]) -> str:
    from . import config, platform
    image = platform.container_git_guards()
    if image is not None:
        code = (f"import sys; sys.path.insert(0, {str(image[0].parent)!r}); "
                "from altitude.git_policy import combined_hook; "
                f"raise SystemExit(combined_hook({name!r}, {str(original)!r}, {str(guards)!r}, {dict(contents).get(name)!r}))")
        return "#!/bin/sh\nexec " + shlex.join([image[2], "-B", "-c", code]) + ' "$@"\n'
    if config.INSTALL_PREFIX is not None:
        python = json.loads(config.INSTALL_CONFIG.read_text())["python"]
        code = (f"import sys; sys.path.insert(0, {str(config.INSTALL_PREFIX / 'current')!r}); "
                "from altitude.git_policy import combined_hook; "
                f"raise SystemExit(combined_hook({name!r}, {str(original)!r}, {str(guards)!r}, {dict(contents).get(name)!r}))")
        return (f"#!/bin/sh\nexport ALTITUDE_CONFIG={shlex.quote(str(config.INSTALL_CONFIG))}\n"
                + "exec " + shlex.join([python, "-B", "-c", code]) + ' "$@"\n')
    return ("#!/usr/bin/env python3\nimport sys\n"
            f"sys.path.insert(0, {str(guards.parent)!r})\n"
            "from altitude.git_policy import combined_hook\n"
            f"raise SystemExit(combined_hook({name!r}, {str(original)!r}, {str(guards)!r}, {dict(contents).get(name)!r}))\n")


def _composition(repo: Path, path: Path) -> dict | None:
    if path.parent != _composition_path(repo):
        return None
    try:
        saved = json.loads((path / "original.json").read_text())
        if path.name != hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest():
            return None
        original, guards = Path(saved["path"]), Path(saved["guards"])
        owner = _git_dir(repo) if saved["scope"] == "worktree" else _common_git_dir(repo)
        if saved["owner"] != str(owner):
            return None
        if original == path or not original.is_absolute() or not guards.is_absolute():
            return None
        if not _owned_hooks(guards.resolve()) and guards.resolve() != _active_hooks().resolve():
            return None
        if any((path / name).is_symlink() or not os.access(path / name, os.X_OK)
               or (path / name).read_text() != _composed_hook(name, original, guards, saved["original_contents"])
               for name in HOOK_EVENTS):
            return None
        return saved
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _selection(repo: Path) -> tuple[str | None, str, Path]:
    _git_dir(repo)
    selected = _run(repo, "config", "--show-scope", "--get", "core.hooksPath")
    if selected.returncode != 1:
        _output(selected, "cannot read core.hooksPath")
        scope, raw = selected.stdout.removesuffix("\n").split("\t", 1)
        return raw, scope, _resolve_hooks_path(repo, raw)
    default = _output(_run(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"),
                      "cannot resolve default hooks")
    return None, "local", Path(default).resolve()


def inspect_hooks(repo: str | Path) -> dict:
    """Observe actual hook ownership; no registration or repair is implied by this read."""
    root = Path(repo).resolve()
    try:
        raw, scope, path = _selection(root)
        desired = _active_hooks()
        _verify_hook_files(desired)
        contents = _hook_contents(path)
        owner = _git_dir(root) if scope == "worktree" else _common_git_dir(root)
        fingerprint = hashlib.sha256(json.dumps([str(owner), raw, scope, str(path), contents]).encode()).hexdigest()
        result = {"hooks_path": str(path), "fingerprint": fingerprint, "can_combine": False,
                  "selection": raw, "scope": scope, "owner": str(owner), "original_contents": contents}
        saved = _composition(root, path)
        if saved:
            original = Path(saved["path"])
            actual = _hook_contents(original)
            result.update(original_hooks=str(original), original_contents=actual,
                          fingerprint=hashlib.sha256(json.dumps([fingerprint, actual]).encode()).hexdigest())
            supported, reason = _integration_support(original, actual, saved["selection"])
            if actual != saved["original_contents"] or not supported:
                return {**result, "status": "conflict", "can_combine": supported,
                        "detail": "The original hooks changed; review the updated integration." if supported else reason}
            return {**result, "status": "ready" if Path(saved["guards"]).resolve() == desired.resolve() else "stale",
                    "detail": "Both hook sets are configured.", "original_hooks": saved["path"]}
        if raw is not None and path == desired.resolve():
            return {**result, "status": "ready", "detail": "Git guards are already configured."}
        if raw is not None and _owned_hooks(path):
            return {**result, "status": "stale", "detail": "Git guards need an update."}
        if raw is None and not contents:
            return {**result, "status": "missing", "detail": "Git guards are not installed."}
        # #348: foreign hooks cannot be disabled as a side effect of repairing stale guards.
        # Managers that redirect through another hooksPath need their own integration review.
        supported, reason = _integration_support(path, contents, raw)
        if path.parent == _composition_path(root) or path == _composition_path(root):
            supported, reason = False, "The managed hook files changed; L3 needs to inspect them."
        return {**result, "status": "conflict", "can_combine": supported,
                "original_hooks": str(path), "detail": ("Existing hooks need your integration choice." if supported else
                reason)}
    except (GitPolicyError, OSError, ValueError) as exc:
        return {"status": "error", "detail": str(exc), "can_combine": False}


def repair_hooks(repo: str | Path, *, combine: bool = False, expected: str | None = None) -> dict:
    """Install/refresh owned guards; combining a foreign owner requires its observed identity."""
    try:
        return _repair_hooks(repo, combine=combine, expected=expected)
    except OSError as exc:
        raise GitPolicyError(f"Cannot update Git guards: {exc}") from exc


def _repair_hooks(repo: str | Path, *, combine: bool, expected: str | None) -> dict:
    root = Path(repo).resolve()
    observed = inspect_hooks(root)
    if observed["status"] == "ready":
        return {**observed, "action": "reused"}
    if observed["status"] == "error":
        raise GitPolicyError(observed["detail"])
    desired = _active_hooks().absolute()
    raw, scope, path = observed["selection"], observed["scope"], Path(observed["hooks_path"])
    saved = _composition(root, path)
    action = "updated" if observed["status"] == "stale" else "installed"
    if observed["status"] == "conflict":
        if not combine or not observed["can_combine"]:
            raise GitPolicyError(f"{observed['detail']} refusing to overwrite existing hooks; review project Setup.")
        if expected != observed["fingerprint"]:
            raise GitPolicyError("Hook ownership changed; check project Setup and review the current integration.")
        saved = {**(saved or {"path": str(path), "selection": raw, "scope": scope, "owner": observed["owner"]}),
                 "guards": str(desired), "approved_fingerprint": expected,
                 "original_contents": observed["original_contents"]}
        action = "combined"
    if saved:
        saved["guards"] = str(desired)
        directory = _composition_path(root)
        if directory.is_symlink():
            raise GitPolicyError("The integration directory is a symlink; inspect it with L3 before retrying.")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest()
        previous = _composition(root, target) if target.exists() else None
        if target.exists() and (previous is None or previous["path"] != saved["path"]):
            raise GitPolicyError("The integration destination already exists; inspect it with L3 before retrying.")
        # #348: a config-write failure retains a complete, verifiable composition for retry.
        with tempfile.TemporaryDirectory(dir=target.parent, prefix=".altitude-hooks-") as temporary:
            staged = Path(temporary) / "hooks"
            staged.mkdir()
            for name in HOOK_EVENTS:
                wrapper = staged / name
                wrapper.write_text(_composed_hook(name, Path(saved["path"]), desired, saved["original_contents"]))
                wrapper.chmod(0o755)
            (staged / "original.json").write_text(json.dumps(saved) + "\n")
            if not target.exists():
                staged.rename(target)
        desired = target
    if inspect_hooks(root).get("fingerprint") != observed["fingerprint"]:
        raise GitPolicyError("Hook ownership changed during repair; check project Setup and retry.")
    _output(_run(root, "config", "--worktree" if scope == "worktree" else "--local", "core.hooksPath", str(desired)),
            "cannot install Git policy hooks")
    final = inspect_hooks(root)
    if final["status"] != "ready":
        raise GitPolicyError(final["detail"])
    return {**final, "action": action}


def install_hooks(repo: str | Path, hooks_dir: str | Path | None = None) -> Path:
    if hooks_dir is None:
        return Path(repair_hooks(repo)["hooks_path"])
    # Explicit directories are used by isolated hook-policy fixtures and installation tooling.
    root, desired = Path(repo).resolve(), Path(hooks_dir).absolute()
    _verify_hook_files(desired)
    raw, scope, path = _selection(root)
    if raw is not None and path != desired.resolve():
        raise GitPolicyError(f"core.hooksPath is already {raw!r}; refusing to overwrite it")
    _output(_run(root, "config", "--worktree" if scope == "worktree" else "--local", "core.hooksPath", str(desired)),
            "cannot install Git policy hooks")
    return desired


def require_hooks_installed(repo: str | Path, hooks_dir: str | Path | None = None) -> Path:
    """Verify the complete tracked guard set without changing repository configuration."""
    if hooks_dir is None:
        observed = inspect_hooks(repo)
        if observed["status"] != "ready":
            raise GitPolicyError("Git guards are not installed or current; " + observed["detail"]
                                 + " L3 can run project setup repair; review custom hooks in Setup.")
        return Path(observed["hooks_path"])
    root = Path(repo).resolve()
    _git_dir(root)
    desired = Path(hooks_dir or _active_hooks()).resolve()
    current = _configured_hooks_path(root)
    if current is None or _resolve_hooks_path(root, current) != desired:
        raise GitPolicyError(f"Git guards are not installed from {desired}; run `alt install-git-guards`")
    missing = [name for name in REQUIRED_HOOKS if not (desired / name).is_file()]
    not_executable = [name for name in REQUIRED_HOOKS if (desired / name).is_file() and not os.access(desired / name, os.X_OK)]
    if missing or not_executable:
        detail = ([f"missing {', '.join(missing)}"] if missing else []) + (
            [f"non-executable {', '.join(not_executable)}"] if not_executable else []
        )
        raise GitPolicyError(f"Git guard installation is incomplete: {'; '.join(detail)}")
    return desired


def combined_hook(name: str, original: str, guards: str, expected: str | None) -> int:
    """Preserve Git's invocation, and give both guarded hooks their complete input."""
    foreign = Path(original) / name
    actual = hashlib.sha256(foreign.read_bytes()).hexdigest() if foreign.is_file() and os.access(foreign, os.X_OK) else None
    if actual != expected:
        print("Altitude Git policy: original hooks changed; review the integration in project Setup.", file=sys.stderr)
        return 1
    if name not in REQUIRED_HOOKS:
        if foreign.is_file() and os.access(foreign, os.X_OK):
            os.execv(str(foreign), [str(foreign), *sys.argv[1:]])
        return 0
    data = sys.stdin.buffer.read() if name in ("pre-push", "reference-transaction") else None
    for hook in (Path(guards) / name, foreign):
        if hook == foreign and not (hook.is_file() and os.access(hook, os.X_OK)):
            continue
        result = subprocess.run([str(hook), *sys.argv[1:]], input=data)
        if result.returncode:
            return result.returncode
    return 0


def _current_branch(repo: Path) -> str | None:
    result = _run(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _protected_branches(base: str) -> frozenset[str]:
    return frozenset((base, "main", "master"))


def _hook_commit(kind: str, repo: Path, base: str) -> int:
    branch = _current_branch(repo)
    if branch in _protected_branches(base):
        print(
            f"Altitude Git policy: {kind} on {branch} is blocked; commit on a task branch and merge its PR.",
            file=sys.stderr,
        )
        return 1
    return 0


def _hook_pre_push(base: str) -> int:
    protected = {f"refs/heads/{branch}" for branch in _protected_branches(base)}
    offending: list[str] = []
    for line in sys.stdin:
        fields = line.split()
        if len(fields) == 4 and fields[2] in protected:
            offending.append(fields[2])
    if offending:
        print(
            f"Altitude Git policy: pushing {', '.join(sorted(set(offending)))} directly is blocked; "
            "push a task branch and merge its PR.",
            file=sys.stderr,
        )
        return 1
    return 0


def _hook_reference_transaction(repo: Path, base: str, phase: str) -> int:
    """Allow unchanged protected tips and moves to their cached remote refs.

    This covers fast-forward merge, reset, and branch-force paths that do not invoke pre-commit or
    pre-merge-commit. The service's supported self-deploy fast-forward remains allowed because its target is
    exactly ``origin/<branch>``.
    """
    if phase != "prepared":
        return 0
    protected = {f"refs/heads/{branch}": branch for branch in _protected_branches(base)}
    refusals: list[str] = []
    for line in sys.stdin:
        fields = line.split()
        if len(fields) != 3 or fields[2] not in protected:
            continue
        old, new, ref = fields
        current = _run(repo, "rev-parse", "--verify", ref)
        if current.returncode == 0 and new == current.stdout.strip():
            # #291: pack-refs writes zero -> current, even over an older packed tip.
            continue
        if new == "0" * len(new) and old != "0" * len(old):
            # #291: pruning removes only the loose copy after packing the same tip.
            # Real deletion first prepares a zero -> zero packed removal, which
            # must still be refused, even when the packed entry matches the loose one.
            packed = _run(repo, "rev-parse", "--path-format=absolute", "--git-path", "packed-refs")
            if packed.returncode == 0:
                try:
                    rows = Path(packed.stdout.strip()).read_text().splitlines()
                except OSError:
                    rows = []
                if current.returncode == 0 and current.stdout.strip() == old and f"{old} {ref}" in rows:
                    continue
        branch = protected[ref]
        remote = _run(repo, "rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}")
        expected = (remote.stdout or "").strip() if remote.returncode == 0 else None
        if not expected or new != expected:
            refusals.append(f"{ref} may move only to origin/{branch} ({expected[:12] if expected else 'missing'})")
    if refusals:
        print(
            "Altitude Git policy: protected branch update blocked: " + "; ".join(refusals)
            + ". Merge the PR remotely, fetch, then fast-forward to the cached origin ref.",
            file=sys.stderr,
        )
        return 1
    return 0


def hook_main(kind: str, *, repo: str | Path = ".", base: str | None = None,
              phase: str | None = None) -> int:
    """Entry point used by the tracked hook executables and their tests."""
    selected_base = base or os.environ.get("ALTITUDE_BASE_BRANCH") or DEFAULT_BASE
    root = Path(repo).resolve()
    if kind in ("pre-commit", "pre-merge-commit"):
        return _hook_commit(kind, root, selected_base)
    if kind == "pre-push":
        return _hook_pre_push(selected_base)
    if kind == "reference-transaction" and phase:
        return _hook_reference_transaction(root, selected_base, phase)
    print(f"Altitude Git policy: unknown hook {kind!r}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) >= 2 and args[0] == "hook":
        return hook_main(args[1], phase=args[2] if len(args) == 3 else None)
    print("usage: python3 -m altitude.git_policy hook {pre-commit|pre-merge-commit|pre-push|reference-transaction [phase]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
