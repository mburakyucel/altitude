"""alt land — publish a task branch as one plain command (decision 47, tier 0).

The measured why: status → add → commit → push → `gh pr create` → `gh pr view` → merge → fetch → run list cost
~100 turns across L2/L1 sessions, and every compound form of it trips the Safety Net (R-003). Here it is one
command: stage only the task's lease (refuse if anything outside it changed), commit with the Altitude trailer,
push with one force-with-lease retry against the branch tip recorded before committing (never two), and open
or reuse the PR. Models stop there: they bind an independent reviewer to the exact PR head and submit a durable
merge request. Only the daemon merge coordinator receives merge credentials; it revalidates provenance,
review and CI, and requires a proven server-side atomic stale-base guard. A no-CI suite on the exact candidate
inside credential-free Bubblewrap remains candidate evidence, but cannot by itself authorize an automatic
GitHub merge; without strict required server checks the durable request waits. The legacy merge mode is
operator-only and structurally denied by model hooks and brokers. No model call occurs
inside this module — the commit message arrives as an argument. Idempotent: nothing to commit is a skip, an
up-to-date push is a no-op, and an open PR is reused.

Precondition: a working, authenticated `gh` before alt land commits anything. The branch's PR is looked up
first — that lookup is what decides whether committing is safe at all (a merged or closed PR is refused) — so
a missing or logged-out `gh` ends the run with the worktree untouched, nothing staged and nothing committed."""
from __future__ import annotations
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from . import config, dispatch, engines, git_policy, state as S

CHECK_POLL_SECONDS = 15
LOCAL_TEST_TIMEOUT = 1800
DEFAULT_TEST_CMD = "make test"
TRUSTED_REMOTE_PENDING = (
    "trusted remote landing integration is pending: publish the clean reviewed PR without --merge; "
    "local tests and generic GitHub checks cannot authorize landing"
)
BWRAP = Path("/usr/bin/bwrap")
PRLIMIT = Path("/usr/bin/prlimit")
SANDBOX_HOME = "/home/altitude"
SANDBOX_WORKSPACE = "/workspace"
SANDBOX_RUN_ROOT = "/workspace/source"
SANDBOX_SOURCE = "/source"
SANDBOX_WORKSPACE_BYTES = 512 * 1024 * 1024
SANDBOX_MAX_SOURCE_FILES = 100_000
SANDBOX_MAX_SOURCE_BYTES = 256 * 1024 * 1024
SANDBOX_MAX_TREE_METADATA_BYTES = 64 * 1024 * 1024
SANDBOX_MAX_GIT_METADATA_BYTES = (2 * SANDBOX_MAX_SOURCE_BYTES
                                  + 2 * SANDBOX_MAX_TREE_METADATA_BYTES + 8 * 1024 * 1024)
SANDBOX_FILE_BURST = 16_384
SANDBOX_OUTPUT_LIMIT = 4 * 1024 * 1024
SANDBOX_RLIMIT_AS = 1024 * 1024 * 1024
# RLIMIT_NPROC is charged to the caller's real UID, not to a process group or
# candidate.  Give the suite a bounded burst above the already-live same-UID
# thread count; the exact generation monitor below still enforces the much
# smaller SANDBOX_MAX_PROCESSES limit.  A fixed absolute value makes unrelated
# Altitude workers consume the candidate's allowance and can turn a green gate
# into an unreadable fork failure.
SANDBOX_RLIMIT_NPROC_BURST = 128
SANDBOX_RLIMIT_CPU = 600
SANDBOX_RLIMIT_FSIZE = 256 * 1024 * 1024
SANDBOX_RLIMIT_NOFILE = 256
SANDBOX_MAX_PROCESSES = 64
SANDBOX_MAX_RSS_BYTES = 2 * 1024 * 1024 * 1024
SANDBOX_MAX_CPU_SECONDS = 900.0
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
UNDECLARED = "(undeclared — all changes staged)"
EMPTY_LEASE_MESSAGE = "lease is empty: pass --paths or set the task paths"
#: `_ensure_pr(pr=...)` default: no lookup has happened yet. `None` means the caller already looked and the
#: branch has no PR, so the create path must not look a second time.
NOT_PREFETCHED = object()


class LandError(RuntimeError):
    """A refusal or a dead end the caller must see; bin/alt prints it on stderr and exits non-zero."""


class EmptyLeaseError(LandError):
    """The task resolved correctly, but it grants no files for this landing."""


def _land_git_env() -> dict[str, str]:
    """Fixed host-Git environment for publication and evidence reads.

    Repository attributes remain versioned input, but they cannot select a
    caller global/system clean/process filter. The only hook path Git can use
    is Altitude's tracked protected-branch hook set.
    """
    missing = [name for name in git_policy.REQUIRED_HOOKS
               if not (config.HOOKS / name).is_file() or not os.access(config.HOOKS / name, os.X_OK)]
    if missing:
        raise LandError(f"trusted Git hook set is unavailable: {', '.join(missing)}")
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(config.HOME),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "/usr/bin/ssh -oBatchMode=yes",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(config.HOOKS.resolve()),
        "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "protocol.ext.allow", "GIT_CONFIG_VALUE_2": "never",
    }
    socket = os.environ.get("SSH_AUTH_SOCK")
    if socket and Path(socket).is_absolute():
        env["SSH_AUTH_SOCK"] = socket
    return env


def _run(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Every git/gh invocation funnels through here so tests can drive the whole pipeline offline."""
    try:
        env = _land_git_env() if args and args[0] == "git" else None
        return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
    except (subprocess.SubprocessError, OSError) as e:
        raise LandError(f"{' '.join(args[:3])}: {e}") from e


def _git(root: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return _run(["git", *args], root, timeout=timeout)


def _need(p: subprocess.CompletedProcess, what: str) -> str:
    if p.returncode != 0:
        raise LandError(f"{what}: {(p.stderr or p.stdout or '').strip()[-300:] or f'exit {p.returncode}'}")
    return (p.stdout or "").strip()


def _note(msg: str) -> None:
    print(f"alt land: {msg}", file=sys.stderr)


def _resolve(branch: str, project: str | None) -> tuple[str | None, str | None, dict | None]:
    """The task this worktree lands: ALTITUDE_PROJECT/ALTITUDE_TASK from the dispatch env, slug derived from the
    `worktree-<slug>` branch when the env is gone (a resumed or hand-run session)."""
    project = project or os.environ.get("ALTITUDE_PROJECT") or None
    slug = os.environ.get("ALTITUDE_TASK") or None
    if not slug and branch.startswith("worktree-"):
        slug = branch[len("worktree-"):]
    task = None
    if project and slug:
        try:
            task = S.load_task(project, slug)
        except KeyError:
            task = None
    return project, slug, task


def _changes(root: Path) -> list[tuple[str, list[str]]]:
    """Working-tree changes as (XY, paths) groups — a rename is one group carrying both ends. `-z` so
    spaced and quoted paths never bite; untracked files listed one by one, never as a directory."""
    p = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if p.returncode != 0:
        raise LandError(f"git status: {(p.stderr or '').strip()[-200:]}")
    items = (p.stdout or "").split("\0")
    groups, i = [], 0
    while i < len(items):
        it = items[i]
        i += 1
        if not it:
            continue
        xy, grp = it[:2], [it[3:]]
        if "R" in xy or "C" in xy:  # the origin path follows as its own NUL-separated item
            grp.append(items[i])
            i += 1
        groups.append((xy, grp))
    return groups


def _inside(path: str, lease: list[str]) -> bool:
    """A lease entry that is a directory covers everything beneath it (decision 39 semantics). Entries are
    repo-root-relative; a malformed absolute entry like `/src` is read as `src` rather than matching nothing."""
    def norm(x: str) -> str:
        return dispatch._norm(x).lstrip("/")
    p = norm(path)
    return any(p == l or p.startswith(l + "/") for l in map(norm, lease))


def _fetch_remote_tip(root: Path, branch: str) -> str | None:
    """Refresh and return origin/<branch>; a branch that does not exist yet has no tip to lease."""
    ref = f"refs/remotes/origin/{branch}"
    fetched = _git(root, "fetch", "-q", "origin", f"+refs/heads/{branch}:{ref}")
    if fetched.returncode != 0:
        err = ((fetched.stderr or "") + (fetched.stdout or "")).strip()
        absent = _git(root, "ls-remote", "--exit-code", "--heads", "origin", branch)
        if absent.returncode == 2:
            return None
        raise LandError(f"git fetch origin {branch}: {err[-300:] or f'exit {fetched.returncode}'}")
    tip = _git(root, "rev-parse", "--verify", "-q", ref)
    if tip.returncode != 0 or not (tip.stdout or "").strip():
        raise LandError(f"cannot record remote tip for origin/{branch}")
    return tip.stdout.strip()


def _push(root: Path, branch: str, base: str, task_ref: str, recorded_tip: str | None) -> list[str]:
    """Push once normally; retry a non-fast-forward once against the pre-commit remote-tip lease."""
    p = _git(root, "push", "-u", "origin", branch, timeout=300)
    if p.returncode == 0:
        return []
    err = (p.stderr or "") + (p.stdout or "")
    if not any(s in err for s in ("non-fast-forward", "fetch first", "[rejected]")):
        raise LandError(f"git push: {err.strip()[-300:]}")
    if recorded_tip is None:
        raise LandError(f"push rejected, but origin/{branch} had no tip when alt land began — refusing to force "
                        "without a recorded lease; fetch the branch, inspect it, and re-run alt land")
    # A rebase adds commits outside the recorded tip's history; a zero count means force can only rewind it.
    local_additions = _need(
        _git(root, "rev-list", "--count", "HEAD", f"^{recorded_tip}"),
        f"cannot determine whether force would only rewind origin/{branch}",
    )
    if local_additions == "0":
        try:
            current_tip = _fetch_remote_tip(root, branch)
            current = current_tip or "(no remote branch)"
        except LandError as exc:
            current = f"(unavailable: {exc})"
        raise LandError(
            f"force-with-lease push refused — recorded remote tip: {recorded_tip}; current remote tip: "
            f"{current}. Run `git fetch origin {branch}`, look at the foreign commits, rebase by hand, then "
            "re-run `alt land`. Local HEAD adds no commits outside the recorded remote history"
        )
    try:
        missing = git_policy.commits_missing_task_trailer(root, base, task_ref)
    except git_policy.GitPolicyError as exc:
        raise LandError(f"cannot revalidate provenance after rebase: {exc}") from exc
    if missing:
        sample = ", ".join(sha[:12] for sha in missing[:5])
        raise LandError(
            f"rebased branch has commit(s) without exact `Altitude-Task: {task_ref}` provenance: {sample}; "
            "stopping before the second push"
        )
    audit = _need(
        _git(root, "rev-list", "--oneline", "--max-count=10", recorded_tip, "^HEAD"),
        f"cannot audit commits replaced on origin/{branch}",
    )
    replaced = audit.splitlines()
    _note(f"push rejected (non-fast-forward) — retrying once with a lease on {recorded_tip}; "
          f"recorded remote-only commits (up to 10): {' | '.join(replaced) or '(none)'}")
    lease = f"--force-with-lease={branch}:{recorded_tip}"
    p2 = _git(root, "push", "-u", lease, "origin", branch, timeout=300)
    if p2.returncode != 0:
        try:
            current_tip = _fetch_remote_tip(root, branch)
            current = current_tip or "(no remote branch)"
        except LandError as exc:
            current = f"(unavailable: {exc})"
        push_err = ((p2.stderr or "") + (p2.stdout or "")).strip()[-300:]
        raise LandError(
            f"force-with-lease push refused — recorded remote tip: {recorded_tip}; current remote tip: "
            f"{current}. Run `git fetch origin {branch}`, look at the foreign commits, rebase by hand, then "
            f"re-run `alt land`. Push error: {push_err or f'exit {p2.returncode}'}"
        )
    return replaced


def _pr_view(root: Path, target: str) -> dict | None:
    p = _run(["gh", "pr", "view", target, "--json",
              "number,url,state,baseRefName,baseRefOid,headRefName,headRefOid"], root)
    if p.returncode != 0:
        err = ((p.stderr or "") + (p.stdout or "")).strip()
        if "no pull requests found" in err.lower():
            return None  # legitimately missing, not a tooling failure (decision 36)
        raise LandError(f"gh pr view {target}: {err[-200:]}")
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise LandError(f"gh pr view {target}: unparseable output") from e


def _pr_files(root: Path, base: str) -> list[str]:
    p = _git(root, "diff", "--name-only", f"origin/{base}...HEAD")
    return sorted(x for x in (p.stdout or "").splitlines() if x) if p.returncode == 0 else []


def _ensure_pr(root: Path, branch: str, base: str, message: str, pr_title: str | None,
               pr_body_file: str | None, task_ref: str, pr: dict | None | object = NOT_PREFETCHED) -> dict:
    """Reuse the branch's PR when one exists (editing it only when asked); otherwise create it. `pr` is the
    caller's already-fetched view of the branch's PR, so the happy path costs one `gh pr view`, not two —
    and a prefetched `None` ("looked, there is no PR") is distinct from `NOT_PREFETCHED` ("nobody looked"),
    so the create path reads the PR back exactly once instead of viewing it before and after creating it."""
    title = pr_title or message.splitlines()[0]
    if pr is NOT_PREFETCHED:
        pr = _pr_view(root, branch)
    if pr is not None:
        _note(f"PR #{pr.get('number')} exists — reusing it")
        if pr_title or pr_body_file:
            args = ["gh", "pr", "edit", str(pr.get("number"))]
            args += ["--title", pr_title] if pr_title else []
            args += ["--body-file", pr_body_file] if pr_body_file else []
            e = _run(args, root)
            if e.returncode != 0:
                raise LandError(f"gh pr edit: {(e.stderr or '').strip()[-200:]}")
        return pr
    body_path, tmp = pr_body_file, None
    try:
        if not body_path:
            files = "\n".join(f"- `{f}`" for f in _pr_files(root, base)) or "- (no files changed against the base)"
            body = (config.TEMPLATES / "pr.md").read_text().format(
                title=title, task=task_ref, branch=branch, base=base, message=message, files=files)
            fd, tmp = tempfile.mkstemp(prefix="alt-land-", suffix=".md")
            os.close(fd)
            Path(tmp).write_text(body)
            body_path = tmp
        c = _run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title,
                  "--body-file", body_path], root, timeout=120)
        if c.returncode != 0:
            raise LandError(f"gh pr create: {((c.stderr or '') + (c.stdout or '')).strip()[-300:]}")
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    pr = _pr_view(root, branch)
    if pr is None:
        raise LandError("gh pr create succeeded but the PR cannot be read back")
    _note(f"PR #{pr.get('number')} created")
    return pr


def _checks_state(root: Path, number: int) -> str:
    """One reading of the PR's checks: pass / fail / pending / skipped / none. `gh pr checks` exits non-zero
    for pending or failing checks, so the JSON body is the verdict, not the exit code. `none` is GitHub
    reporting no checks at all, which is a different fact from checks that ran and were skipped: only the
    caller, which knows whether the repository configures CI, can say what it means (R-006)."""
    p = _run(["gh", "pr", "checks", str(number), "--json", "bucket"], root)
    body = (p.stdout or "").strip()
    if not body:
        if "no checks" in ((p.stderr or "") + (p.stdout or "")).lower():
            return "none"
        raise LandError(f"gh pr checks #{number}: {(p.stderr or '').strip()[-200:] or f'exit {p.returncode}'}")
    try:
        buckets = {c.get("bucket") for c in json.loads(body)}
    except (ValueError, TypeError, AttributeError) as e:
        raise LandError(f"gh pr checks #{number}: unparseable output") from e
    unknown = buckets - {"pass", "fail", "pending", "skipping", "cancel"}
    if unknown:
        raise LandError(f"gh pr checks #{number}: unrecognised bucket(s) {', '.join(sorted(map(str, unknown)))} — "
                        f"refusing to read them as a pass (decision 36); this gates --merge")
    if not buckets:
        return "none"
    if buckets & {"fail", "cancel"}:
        return "fail"
    if "pending" in buckets:
        return "pending"
    # A rollup that mixes passes with skips is not a pass: the skipped check is a configured gate that did
    # not run, and R-006 never lets a gate be satisfied by its absence.
    return "skipped" if "skipping" in buckets else "pass"


def _fetch_rev(root: Path, ref: str) -> str:
    """Fetch and return the authoritative current tip of `origin/<ref>`."""
    fetched = _git(root, "fetch", "origin", ref, timeout=300)
    if fetched.returncode != 0:
        raise LandError(f"cannot refresh origin/{ref}: "
                        f"{((fetched.stderr or '') + (fetched.stdout or '')).strip()[-200:] or f'exit {fetched.returncode}'} — "
                        "refusing to judge checks or a merge against a ref that cannot be read")
    return _need(_git(root, "rev-parse", "FETCH_HEAD"), f"cannot resolve origin/{ref} after fetching it")


def _snapshot_pair(root: Path, branch: str, number: int, base: str, expected_head: str) -> dict:
    """Pin the exact GitHub PR base/head pair that check classification and local testing will judge."""
    pr = _pr_view(root, str(number)) or {}
    if pr.get("state") != "OPEN":
        raise LandError(f"PR #{number} is not open while its merge candidate is being pinned")
    if pr.get("baseRefName") != base or pr.get("headRefName") != branch:
        raise LandError(f"PR #{number} targets {pr.get('baseRefName')!r} from {pr.get('headRefName')!r}, not "
                        f"the expected {base!r} from {branch!r}")
    base_sha, head_sha = pr.get("baseRefOid"), pr.get("headRefOid")
    if not base_sha or not head_sha:
        raise LandError(f"PR #{number} did not report both baseRefOid and headRefOid — refusing an unpinned gate")
    fetched_base, fetched_head = _fetch_rev(root, base), _fetch_rev(root, branch)
    if head_sha != expected_head:
        raise LandError(f"PR #{number} head moved from the pushed revision {expected_head} to {head_sha} before checks")
    if fetched_base != base_sha or fetched_head != head_sha:
        raise LandError(f"PR #{number} refs moved while the merge candidate was being pinned "
                        f"(GitHub {base_sha[:12]}/{head_sha[:12]}, origin {fetched_base[:12]}/{fetched_head[:12]})")
    return {"base": base, "branch": branch, "base_sha": base_sha, "head_sha": head_sha, "number": number}


def _assert_pair_current(root: Path, pair: dict) -> None:
    """Fail unless both origin refs and GitHub's PR still name the pinned pair."""
    base_sha = _fetch_rev(root, pair["base"])
    head_sha = _fetch_rev(root, pair["branch"])
    pr = _pr_view(root, str(pair["number"])) or {}
    actual = (base_sha, head_sha, pr.get("baseRefOid"), pr.get("headRefOid"), pr.get("state"))
    expected = (pair["base_sha"], pair["head_sha"], pair["base_sha"], pair["head_sha"], "OPEN")
    if actual != expected:
        raise LandError("the PR base or head moved after the merge candidate was pinned — "
                        "re-run alt land to classify, test and merge one current pair")


def _has_ci(root: Path, pair: dict) -> bool:
    """Whether either exact side of the pinned merge pair contains GitHub Actions workflows."""
    for label, sha in (("base", pair["base_sha"]), ("head", pair["head_sha"])):
        tree = _git(root, "ls-tree", "--name-only", sha, ".github/workflows")
        if tree.returncode != 0:
            raise LandError(f"cannot inspect .github/workflows on the pinned {label} {sha[:12]}: "
                            f"{(tree.stderr or '').strip()[-200:] or f'exit {tree.returncode}'}")
        if (tree.stdout or "").strip():
            return True
    return False


def _checks_value(root: Path, number: int, pair: dict) -> str:
    """Read checks only while GitHub and origin still name the pinned PR pair."""
    _assert_pair_current(root, pair)
    state = _checks_state(root, number)
    _assert_pair_current(root, pair)
    if state != "none":
        return state
    value = "none-configured" if not _has_ci(root, pair) else "skipped"
    _assert_pair_current(root, pair)
    return value


def _test_counts(output: str) -> tuple[int | None, int | None, int | None]:
    """Return (passing, skipped, expected-failure) counts, or an unreadable triple."""
    ran = re.findall(r"^Ran (\d+) tests?\b", output, re.M)
    if ran:
        found = re.findall(r"\bskipped=(\d+)", output)
        skipped = int(found[-1]) if found else 0
        found = re.findall(r"\bexpected failures=(\d+)", output)
        expected = int(found[-1]) if found else 0
        return max(int(ran[-1]) - skipped - expected, 0), skipped, expected
    passed = re.findall(r"\b(\d+) passed\b", output)
    if passed:
        found = re.findall(r"\b(\d+) skipped\b", output)
        skipped = int(found[-1]) if found else 0
        found = re.findall(r"\b(\d+) xfailed\b", output)
        expected = int(found[-1]) if found else 0
        return int(passed[-1]), skipped, expected
    return None, None, None


def _uid_task_count() -> int:
    """Count live threads charged to our real UID for RLIMIT_NPROC.

    Linux accounts threads (not merely thread-group leaders) against this
    limit.  Vanishing processes are normal during the snapshot; inability to
    inspect a still-present same-UID process is not, because undercounting
    would recreate the unrelated-load failure this baseline prevents.
    """
    uid = os.getuid()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError as exc:
        raise LandError(f"local-suite cannot establish the same-UID process baseline: {exc}") from exc
    total = 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            tasks = [task for task in (entry / "task").iterdir() if task.name.isdigit()]
        except FileNotFoundError:
            continue
        except OSError as exc:
            # If the identity vanished, this is an ordinary /proc race.  A
            # still-live same-UID identity must remain countable.
            try:
                live = entry.stat().st_uid == uid
            except OSError:
                continue
            if live:
                raise LandError(f"local-suite cannot count same-UID threads for PID {entry.name}: {exc}") from exc
            continue
        total += len(tasks)
    return total


def _sandbox_argv(cwd: Path, argv: list[str]) -> list[str]:
    """Build the fixed, credentialless Bubblewrap boundary for untrusted repository tests."""
    for executable, label in ((BWRAP, "sandbox"), (PRLIMIT, "resource limiter")):
        try:
            info = executable.stat()
        except OSError as exc:
            raise LandError(f"local-suite {label} is unavailable at {executable}: {exc}") from exc
        if not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK):
            raise LandError(f"local-suite {label} is not an executable regular file: {executable}")
    nproc_limit = _uid_task_count() + SANDBOX_RLIMIT_NPROC_BURST
    command = [
        str(PRLIMIT),
        f"--as={SANDBOX_RLIMIT_AS}:{SANDBOX_RLIMIT_AS}",
        f"--nproc={nproc_limit}:{nproc_limit}",
        f"--cpu={SANDBOX_RLIMIT_CPU}:{SANDBOX_RLIMIT_CPU}",
        f"--fsize={SANDBOX_RLIMIT_FSIZE}:{SANDBOX_RLIMIT_FSIZE}",
        f"--nofile={SANDBOX_RLIMIT_NOFILE}:{SANDBOX_RLIMIT_NOFILE}",
        "--core=0:0", "--",
        str(BWRAP),
        # Popen creates the one private session/process group that the host can fence. Do not ask the sandbox child
        # to setsid again: that would put descendants outside the host's persisted group anchor.
        "--die-with-parent", "--unshare-all", "--unshare-user", "--unshare-net", "--disable-userns",
        "--cap-drop", "ALL", "--clearenv",
        # The suite gets the system toolchain, not the host's home, credential stores, service state, or runtime dirs.
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/sbin", "/sbin",
        "--proc", "/proc", "--dev", "/dev", "--remount-ro", "/dev",
        "--dir", "/home",
        # The synthetic candidate is exposed read-only. Tests run from a copy
        # in a kernel-sized tmpfs, so an adversarial many-file workload cannot
        # fill the host filesystem (RLIMIT_FSIZE only bounds one file).
        "--ro-bind", str(cwd.resolve()), SANDBOX_SOURCE,
        "--size", str(SANDBOX_WORKSPACE_BYTES), "--tmpfs", SANDBOX_WORKSPACE,
        # --chdir is resolved by Bubblewrap before the trusted launcher runs,
        # so every workspace subdirectory must be created as part of mount
        # setup rather than by that launcher.
        "--dir", SANDBOX_RUN_ROOT,
        "--dir", "/workspace/tmp",
        "--dir", "/workspace/home",
    ]
    # A synthetic merge candidate carries a bounded, credential-free object
    # database.  Do not copy it into writable tmpfs: expose only that metadata
    # as a read-only child mount so repository tests can inspect the exact
    # single-parent candidate history without reaching host Git state.
    metadata = cwd / ".git"
    if metadata.is_dir():
        command += ["--ro-bind", str(metadata.resolve()), f"{SANDBOX_RUN_ROOT}/.git"]
    command += [
        # HOME and both conventional temporary paths resolve inside the same
        # byte-sized, inode-monitored workspace.  There is no second tmpfs
        # with a host-derived byte/inode allowance.  The synthetic root is
        # then read-only; only this child mount remains writable.
        "--symlink", "workspace/tmp", "/tmp",
        "--symlink", "../workspace/home", SANDBOX_HOME,
        "--remount-ro", "/",
        "--chdir", SANDBOX_RUN_ROOT,
        "--setenv", "HOME", SANDBOX_HOME,
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
        "--", "/bin/sh", "-c",
        ("/usr/bin/mkdir -p /workspace/source /workspace/tmp /workspace/home && "
         "for item in /source/* /source/.[!.]* /source/..?*; do "
         "if [ ! -e \"$item\" ] && [ ! -L \"$item\" ]; then continue; fi; "
         "if [ \"$item\" = /source/.git ]; then continue; fi; "
         "/usr/bin/cp -a -- \"$item\" /workspace/source/ || "
         "{ echo 'altitude-sandbox: workspace copy failed' >&2; exit 125; }; "
         "done; "
         "cd /workspace/source && exec \"$@\""),
        "altitude-local-suite", *argv,
    ]
    return command


class _SandboxOutput:
    """Drain both pipes continuously while retaining at most one combined bounded payload."""

    def __init__(self, limit: int):
        self.limit = limit
        self.total = 0
        self.exceeded = threading.Event()
        self._lock = threading.Lock()
        self._parts = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(self, name: str, stream) -> None:
        try:
            while chunk := stream.read(65536):
                with self._lock:
                    room = max(0, self.limit - self.total)
                    if room:
                        kept = chunk[:room]
                        self._parts[name].extend(kept)
                        self.total += len(kept)
                    if len(chunk) > room:
                        self.exceeded.set()
        except (OSError, ValueError):
            self.exceeded.set()

    def text(self, name: str) -> str:
        with self._lock:
            return bytes(self._parts[name]).decode("utf-8", errors="replace")


def _proc_record(entry: Path, *, usage: bool) -> dict | None:
    try:
        fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        record = {"pid": int(entry.name), "state": fields[0], "ppid": int(fields[1]),
                  "start": fields[19], "user_ticks": int(fields[11]), "system_ticks": int(fields[12])}
        if usage:
            record["resident"] = int((entry / "statm").read_text().split()[1])
        return record
    except (OSError, ValueError, IndexError):
        return None


def _pid_namespace(pid: int, name: str = "pid") -> tuple[int, int] | None:
    try:
        info = os.stat(f"/proc/{pid}/ns/{name}")
        return info.st_dev, info.st_ino
    except OSError:
        return None


def _sandbox_owner(pid: int) -> dict:
    record = _proc_record(Path(f"/proc/{pid}"), usage=False)
    namespace = _pid_namespace(pid)
    if record is None or namespace is None:
        raise LandError("local-suite sandbox process identity could not be established")
    return {"leader": pid, "leader_start": record["start"], "host_namespace": namespace,
            "namespaces": set(), "known": {pid: record["start"]},
            "workspace_seen": False, "workspace_identity": None, "workspace_files": 0}


def _tree_entry_count(root: Path, *, stop_after: int) -> int:
    """Count filesystem objects without following candidate-controlled links."""
    count, pending = 0, [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > stop_after:
                        return count
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
        except FileNotFoundError:
            # Workspaces can vanish only after their exact process namespace
            # exits; callers decide whether that lifecycle is safe.
            raise
        except OSError as exc:
            raise LandError(f"local-suite workspace file count is unreadable at {directory}: {exc}") from exc
    return count


def _workspace_file_count(owner: dict, members: list[dict]) -> int | None:
    """Count inodes in the exact private workspace, or fail closed.

    Bubblewrap's byte-sized tmpfs does not bound ``nr_inodes``.  A host-side
    monitor therefore opens the workspace through an exact owned process's
    mount namespace and bounds all directory entries.  The marker fallback is
    solely for the unit fake, which has no mount namespace.
    """
    roots: list[Path] = []
    private_live = False
    for record in members:
        namespace = _pid_namespace(record["pid"])
        if namespace is not None and namespace != owner["host_namespace"]:
            private_live = True
        namespaced = Path(f"/proc/{record['pid']}/root{SANDBOX_WORKSPACE}")
        try:
            if namespaced.is_dir():
                roots.append(namespaced)
                continue
        except OSError:
            current = _proc_record(Path(f"/proc/{record['pid']}"), usage=False)
            if current is not None and current["state"] != "Z" and current["start"] == record["start"]:
                return None
            continue
        # The deterministic fake-bwrap launcher cannot mount /workspace.  Its
        # exact child cwd contains this marker and otherwise follows the same
        # file-count path as production.
        cwd = Path(f"/proc/{record['pid']}/cwd")
        try:
            # The fake runs from workspace/source, while the marker belongs
            # to the monitored parent that also contains fake HOME and TMP.
            resolved_cwd = cwd.resolve(strict=True)
            for candidate in (resolved_cwd, resolved_cwd.parent, resolved_cwd.parent.parent):
                if (candidate / ".altitude-sandbox-workspace").is_file():
                    roots.append(candidate)
                    break
        except OSError:
            current = _proc_record(Path(f"/proc/{record['pid']}"), usage=False)
            if current is not None and current["state"] != "Z" and current["start"] == record["start"]:
                return None
            continue
    if not roots:
        # Before Bubblewrap forks the inner namespace, or after that namespace
        # has exited, only its outer monitor exists and cannot create files.
        # Missing access while a private member is live is unverifiable.
        return None if private_live else int(owner.get("workspace_files") or 0)
    for root in roots:
        try:
            info = root.stat()
        except FileNotFoundError:
            continue  # exact member exited after its /proc root was captured
        except OSError:
            return None
        identity = (info.st_dev, info.st_ino)
        if owner["workspace_identity"] not in (None, identity):
            return None
        try:
            count = _tree_entry_count(root, stop_after=int(owner["file_limit"]))
        except FileNotFoundError:
            continue  # namespace/workspace vanished during the bounded walk
        except LandError:
            return None
        owner["workspace_identity"] = identity
        owner["workspace_seen"] = True
        owner["workspace_files"] = count
        return count
    # Every captured root vanished. That is safe only when the corresponding
    # exact private-namespace producers have also vanished; the outer bwrap
    # monitor (or the unit fake during trusted cleanup) cannot execute the
    # candidate workload after that point.
    for record in members:
        current = _proc_record(Path(f"/proc/{record['pid']}"), usage=False)
        if current is None or current["state"] == "Z" or current["start"] != record["start"]:
            continue
        namespace = _pid_namespace(record["pid"])
        if namespace is None:
            return None
        if namespace != owner["host_namespace"]:
            return None
    return int(owner.get("workspace_files") or 0)


def _sandbox_members(owner: dict, *, usage: bool = False) -> tuple[list[dict], bool] | None:
    """Return exact suite-owned processes regardless of setsid/setpgid.

    Ownership starts at the immutable leader PID/start identity. Descendants
    are followed by PPID, then pinned by PID/start time; Bubblewrap's child PID
    namespace (including reparented/double-forked children) is sticky for the
    generation. We deliberately never add the host PID namespace.
    """
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    records: dict[int, dict] = {}
    for entry in entries:
        if entry.name.isdigit():
            record = _proc_record(entry, usage=usage)
            if record is not None and record["state"] != "Z":
                records[record["pid"]] = record
    leader = records.get(owner["leader"])
    if leader is not None and leader["start"] != owner["leader_start"]:
        return None  # PID reuse makes the ownership proof invalid.

    owned = {pid for pid, start in owner["known"].items()
             if pid in records and records[pid]["start"] == start}
    if leader is not None:
        owned.add(owner["leader"])
    changed = True
    while changed:
        before = len(owned)
        owned.update(pid for pid, record in records.items() if record["ppid"] in owned)
        changed = len(owned) != before

    # Bubblewrap's outer monitor remains in the host PID namespace. Its
    # pid_for_children switches to the private namespace before the inner init
    # is forked, so observe both that anchor and every discovered descendant.
    if leader is not None:
        child_namespace = _pid_namespace(owner["leader"], "pid_for_children")
        if child_namespace is None:
            current = _proc_record(Path(f"/proc/{owner['leader']}"), usage=False)
            if (current is not None and current["state"] != "Z"
                    and current["start"] == owner["leader_start"]):
                return None
            leader = None  # it exited between the /proc snapshot and namespace read
        elif child_namespace != owner["host_namespace"]:
            owner["namespaces"].add(child_namespace)
    for pid in list(owned):
        namespace = _pid_namespace(pid)
        if namespace is None:
            # A process can exit between /proc scans; a still-live exact
            # identity with an unreadable namespace is an enforcement failure.
            current = _proc_record(Path(f"/proc/{pid}"), usage=False)
            if current is not None and current["start"] == records[pid]["start"]:
                return None
            continue
        if namespace != owner["host_namespace"]:
            owner["namespaces"].add(namespace)
    if owner["namespaces"]:
        for pid, record in records.items():
            namespace = _pid_namespace(pid)
            if namespace in owner["namespaces"]:
                owned.add(pid)

    for pid in owned:
        owner["known"][pid] = records[pid]["start"]
    live = [records[pid] for pid in sorted(owned)]
    return live, leader is not None


def _sandbox_usage(owner: dict) -> dict | None:
    sampled = _sandbox_members(owner, usage=True)
    if sampled is None:
        return None
    members, _leader_live = sampled
    files = _workspace_file_count(owner, members)
    if files is None:
        return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        clock_ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None
    return {"processes": len(members),
            "files": files,
            "rss_bytes": sum(record.get("resident", 0) for record in members) * page_size,
            "cpu_seconds": sum(record["user_ticks"] + record["system_ticks"]
                               for record in members) / clock_ticks}


def _signal_sandbox_members(owner: dict, members: list[dict], sig: int) -> bool:
    for record in members:
        current = _proc_record(Path(f"/proc/{record['pid']}"), usage=False)
        if current is None or current["state"] == "Z":
            continue
        if current["start"] != record["start"]:
            return False
        try:
            os.kill(record["pid"], sig)
        except ProcessLookupError:
            pass
        except OSError:
            return False
    return True


def _freeze_sandbox(owner: dict) -> list[dict] | None:
    """Stop producers, then rescan until no child can race the reap snapshot."""
    previous: set[tuple[int, str]] = set()
    for _attempt in range(8):
        sampled = _sandbox_members(owner)
        if sampled is None:
            return None
        members, _leader_live = sampled
        identities = {(record["pid"], record["start"]) for record in members}
        # Parents first narrows the spawn window; SIGSTOP cannot be caught or
        # ignored. A second scan captures anything forked between the snapshot
        # and its parent's stop (important for fake-bwrap tests without pidns).
        by_pid = {record["pid"]: record for record in members}
        def depth(record: dict) -> int:
            value, seen = 0, set()
            parent = record["ppid"]
            while parent in by_pid and parent not in seen:
                seen.add(parent); value += 1; parent = by_pid[parent]["ppid"]
            return value
        # Freeze the inner producer before its children and the outer
        # Bubblewrap monitor last; then rescan for the final fork race.
        ordered = sorted(members, key=lambda record: (record["pid"] == owner["leader"], depth(record)))
        if not _signal_sandbox_members(owner, ordered, signal.SIGSTOP):
            return None
        if identities == previous:
            return members
        previous = identities
    return None


def _reap_adopted_sandbox_children(owner: dict) -> None:
    """Collect exact generation zombies adopted by this process.

    Another Altitude lifecycle test (and altd in production) can make the
    long-lived parent a child subreaper.  Detached suite descendants are then
    reparented here when the sandbox leaders die.  Killing them is not enough:
    an unreaped zombie keeps its PID indefinitely and made timeout cleanup
    order-dependent.  Never wait for the Popen leader itself, and pin every
    other wait to the start-time identity discovered while it was live.
    """
    for pid, start in list(owner["known"].items()):
        if pid == owner["leader"]:
            continue
        current = _proc_record(Path(f"/proc/{pid}"), usage=False)
        if current is None or current["start"] != start or current["state"] != "Z":
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass  # Its still-live sandbox parent, or init, owns the wait.
        except OSError:
            pass


def _reap_sandbox(owner: dict, *, terminate: bool) -> bool:
    """Reap all exact generation members, including detached session leaders."""
    sampled = _sandbox_members(owner)
    if sampled is None:
        return False
    members, _leader_live = sampled
    if not members:
        _reap_adopted_sandbox_children(owner)
        return True
    if terminate:
        members = _freeze_sandbox(owner)
        if members is None:
            return False
        if not _signal_sandbox_members(owner, members, signal.SIGTERM):
            return False
        # Deliver pending SIGTERM to stopped processes; ignore races with
        # processes that honored TERM and disappeared before CONT.
        _signal_sandbox_members(owner, members, signal.SIGCONT)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        sampled = _sandbox_members(owner)
        if sampled is None:
            return False
        members, _leader_live = sampled
        if not members:
            _reap_adopted_sandbox_children(owner)
            return True
        time.sleep(0.05)
    if terminate and not _signal_sandbox_members(owner, members, signal.SIGKILL):
        return False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        sampled = _sandbox_members(owner)
        if sampled is None:
            return False
        members, _leader_live = sampled
        if not members:
            _reap_adopted_sandbox_children(owner)
            return True
        time.sleep(0.05)
    sampled = _sandbox_members(owner)
    if sampled is not None and sampled[0] == []:
        _reap_adopted_sandbox_children(owner)
        return True
    return False


def _run_local_sandbox(cwd: Path, argv: list[str]) -> subprocess.CompletedProcess:
    command = _sandbox_argv(cwd, argv)
    try:
        source_files = _tree_entry_count(cwd, stop_after=SANDBOX_MAX_SOURCE_FILES)
    except FileNotFoundError as exc:
        raise LandError(f"local-suite candidate disappeared before sandbox launch: {exc}") from exc
    if source_files > SANDBOX_MAX_SOURCE_FILES:
        raise LandError(f"local-suite candidate contains more than {SANDBOX_MAX_SOURCE_FILES} files")
    # Bubblewrap itself receives no host secrets either. All child variables are set explicitly after --clearenv.
    launcher_env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    try:
        proc = subprocess.Popen(
            command, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=launcher_env, start_new_session=True,
        )
    except OSError as exc:
        raise LandError(f"local-suite sandbox could not start: {exc}") from exc
    try:
        owner = _sandbox_owner(proc.pid)
        owner["file_limit"] = source_files + SANDBOX_FILE_BURST
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        raise
    output = _SandboxOutput(SANDBOX_OUTPUT_LIMIT)
    drains = [threading.Thread(target=output.drain, args=(name, stream), daemon=True)
              for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for thread in drains:
        thread.start()
    violation = None
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if output.exceeded.is_set():
                violation = f"output exceeded {SANDBOX_OUTPUT_LIMIT} bytes"
                break
            usage = _sandbox_usage(owner)
            if usage is None:
                violation = "resource usage could not be verified"
                break
            if usage["processes"] > SANDBOX_MAX_PROCESSES:
                violation = f"process count exceeded {SANDBOX_MAX_PROCESSES}"
                break
            if usage["files"] > owner["file_limit"]:
                violation = f"file count exceeded {owner['file_limit']}"
                break
            if usage["rss_bytes"] > SANDBOX_MAX_RSS_BYTES:
                violation = f"resident memory exceeded {SANDBOX_MAX_RSS_BYTES} bytes"
                break
            if usage["cpu_seconds"] > SANDBOX_MAX_CPU_SECONDS:
                violation = f"aggregate CPU exceeded {SANDBOX_MAX_CPU_SECONDS:g} seconds"
                break
            if time.monotonic() - started > LOCAL_TEST_TIMEOUT:
                violation = f"timed out after {LOCAL_TEST_TIMEOUT} seconds"
                break
            time.sleep(0.01)
        if violation and not _reap_sandbox(owner, terminate=True):
            raise LandError(f"local-suite sandbox {violation} and its process namespace could not be reaped")
        if not _reap_sandbox(owner, terminate=True):
            raise LandError("local-suite sandbox left an unverifiable process-namespace descendant")
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise LandError("local-suite sandbox leader could not be reaped") from exc
        for thread in drains:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in drains):
            raise LandError("local-suite sandbox output pipes did not close after process cleanup")
        for stream in (proc.stdout, proc.stderr):
            stream.close()
        # The leader can exit between the last loop predicate and a drainer observing the final chunk.  Enforce the
        # bound after EOF as well so a short, fast flood cannot race the polling loop and be accepted.
        if violation is None and output.exceeded.is_set():
            violation = f"output exceeded {SANDBOX_OUTPUT_LIMIT} bytes"
        stdout, stderr = output.text("stdout"), output.text("stderr")
        if violation:
            raise LandError(f"local-suite sandbox {violation}")
        if proc.returncode != 0 and (stderr or "").lstrip().startswith("bwrap:"):
            raise LandError(f"local-suite sandbox refused execution: {(stderr or '').strip()[-300:]}")
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    except BaseException:
        _reap_sandbox(owner, terminate=True)
        if proc.poll() is None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.kill()
                except OSError:
                    pass
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for thread in drains:
            thread.join(timeout=2)
        raise


def _local_suite(cwd: Path, test_cmd: str) -> dict:
    """Run and count the full local suite in the synthetic merge candidate."""
    argv = shlex.split(test_cmd)
    if not argv:
        raise LandError("the local test command is empty")
    _note(f"no CI configured — the merge candidate's local suite is the gate: {test_cmd}")
    result = {"command": test_cmd, "passed": False, "returncode": None, "tests": None, "skipped": None,
              "expected_failures": None, "error": None}
    try:
        run = _run_local_sandbox(cwd, argv)
    except LandError as exc:
        _note(f"the local suite did not run to completion — not merging: {exc}")
        result["error"] = str(exc)
        return result
    tests, skipped, expected = _test_counts((run.stdout or "") + "\n" + (run.stderr or ""))
    result.update(returncode=run.returncode, tests=tests, skipped=skipped, expected_failures=expected,
                  passed=run.returncode == 0 and tests is not None and tests > 0)
    if run.returncode == 0 and (tests is None or tests <= 0):
        result["error"] = (("the suite exited 0 but no passing-test count could be read from its output"
                            if tests is None else "the suite exited 0 but reported no passing tests")
                           + " — the local gate is not satisfied (R-006)")
        _note(f"not merging: {result['error']}")
    else:
        _note(f"local suite exited {run.returncode}"
              + (f" ({tests} passing, {skipped} skipped, {expected} expected failures)"
                 if tests is not None else " (count unreadable)"))
    return result


def _source_object_directory(root: Path) -> Path:
    """Resolve a worktree's common object directory without running repository-configured Git."""
    marker = root.resolve() / ".git"
    try:
        if marker.is_dir():
            git_dir = marker.resolve()
        else:
            line = marker.read_text().strip()
            if not line.startswith("gitdir: "):
                raise LandError("candidate source has no readable Git directory")
            value = Path(line[len("gitdir: "):])
            git_dir = (value if value.is_absolute() else marker.parent / value).resolve()
        common_marker = git_dir / "commondir"
        if common_marker.is_file():
            value = Path(common_marker.read_text().strip())
            git_dir = (value if value.is_absolute() else git_dir / value).resolve()
        objects = git_dir / "objects"
        if not objects.is_dir():
            raise LandError("candidate source Git object directory is unavailable")
        return objects
    except OSError as exc:
        raise LandError(f"candidate source Git metadata is unreadable: {exc}") from exc


def _safe_git_environment(git_dir: Path | None, objects: Path | None, private_home: Path) -> dict[str, str]:
    """A config-independent Git environment for untrusted candidate trees.

    The synthetic repository has no source/global/system configuration,
    templates, hooks, fsmonitor, credential helpers, custom merge drivers or
    filters. Object data is shared read-only through alternates, never through
    a source worktree checkout or a configured remote helper.
    """
    env = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "HOME": str(private_home), "XDG_CONFIG_HOME": str(private_home),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "core.autocrlf", "GIT_CONFIG_VALUE_2": "false",
        "GIT_CONFIG_KEY_3": "protocol.ext.allow", "GIT_CONFIG_VALUE_3": "never",
    }
    if git_dir is not None:
        env["GIT_DIR"] = str(git_dir)
    if objects is not None:
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(objects)
    return env


def _safe_git(args: list[str], *, cwd: Path, env: dict[str, str], what: str,
              timeout: int = 300) -> str:
    try:
        run = subprocess.run(["git", *args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LandError(f"{what}: {exc}") from exc
    if run.returncode != 0:
        detail = ((run.stderr or "") + (run.stdout or "")).strip()[-300:]
        raise LandError(f"{what}: {detail or f'exit {run.returncode}'}")
    return (run.stdout or "").strip()


def _safe_repository(root: Path, tmp: Path) -> tuple[Path, dict[str, str]]:
    git_dir, private_home = tmp / "git", tmp / "home"
    private_home.mkdir()
    init_env = _safe_git_environment(None, None, private_home)
    _safe_git(["init", "--bare", "--template=", str(git_dir)], cwd=tmp, env=init_env,
              what="cannot initialize isolated candidate object store")
    objects = _source_object_directory(root)
    # Persist the read-only alternate for candidate-local Git introspection
    # (the fake boundary's history regression); the real boundary does not
    # expose this sibling path. Git object IDs authenticate every object read.
    (git_dir / "objects" / "info" / "alternates").write_text(str(objects) + "\n")
    return git_dir, _safe_git_environment(git_dir, objects, private_home)


def _tree_metrics(tree: str, *, cwd: Path, env: dict[str, str], label: str) -> tuple[int, int]:
    """Stream exact tree entries and blob sizes, aborting before output can grow without bound."""
    try:
        proc = subprocess.Popen(
            ["git", "ls-tree", "--full-tree", "-r", "-t", "-l", "-z", tree],
            cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise LandError(f"cannot preflight {label}: {exc}") from exc
    entries = total = metadata_bytes = 0
    buffer = bytearray()
    violation = None
    try:
        assert proc.stdout is not None
        while chunk := proc.stdout.read(65536):
            buffer.extend(chunk)
            while b"\0" in buffer:
                raw, _, rest = buffer.partition(b"\0")
                buffer = bytearray(rest)
                metadata_bytes += len(raw) + 1
                if metadata_bytes > SANDBOX_MAX_TREE_METADATA_BYTES:
                    violation = f"{label} tree metadata exceeds {SANDBOX_MAX_TREE_METADATA_BYTES} bytes"
                    break
                try:
                    metadata, raw_path = raw.split(b"\t", 1)
                    _mode, kind, _oid, size = metadata.split(b" ", 3)
                except ValueError:
                    violation = f"{label} has unreadable tree metadata"
                    break
                try:
                    _validate_candidate_path(os.fsdecode(bytes(raw_path)))
                except LandError as exc:
                    violation = f"{label} {exc}"
                    break
                entries += 1
                if entries > SANDBOX_MAX_SOURCE_FILES:
                    violation = f"{label} contains more than {SANDBOX_MAX_SOURCE_FILES} filesystem entries"
                    break
                if kind == b"blob":
                    try:
                        total += int(size)
                    except ValueError:
                        violation = f"{label} has unreadable blob size metadata"
                        break
                    if total > SANDBOX_MAX_SOURCE_BYTES:
                        violation = f"{label} contains more than {SANDBOX_MAX_SOURCE_BYTES} blob bytes"
                        break
                elif kind != b"tree":
                    violation = f"{label} contains unsupported {kind.decode(errors='replace')} entries"
                    break
            if violation:
                proc.kill()
                break
        if buffer and not violation:
            violation = f"{label} tree listing ended mid-record"
        try:
            _stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); _stdout, stderr = proc.communicate()
            violation = violation or f"{label} tree preflight did not terminate"
        if violation:
            raise LandError(violation)
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-300:]
            raise LandError(f"cannot preflight {label}: {detail or f'exit {proc.returncode}'}")
        return entries, total
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def _validate_candidate_path(relative: str) -> None:
    """Reject paths that can alias Git's own metadata on supported platforms."""
    value = Path(relative)
    if value.is_absolute() or not value.parts or any(part in ("", ".", "..") for part in value.parts):
        raise LandError("contains a path outside its materialization root")
    for component in value.parts:
        # Approximate Git's HFS/NTFS dotgit guards as a cross-platform
        # invariant even though the coordinator currently runs on Linux.
        normalized = unicodedata.normalize("NFKC", component).casefold()
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cf")
        windows = normalized.rstrip(" .")
        if (windows == ".git" or windows.startswith(".git:")
                or re.fullmatch(r"\.?git~\d+", windows)):
            raise LandError("contains an unsafe Git metadata path")


def _tree_files(tree: str, *, cwd: Path, env: dict[str, str]) -> list[tuple[bytes, bytes, str]]:
    """Return bounded raw leaf metadata after :func:`_tree_metrics` accepted the tree."""
    # Use bytes so arbitrary Git path bytes survive exactly. The metadata
    # bound was already proven by the streaming preflight.
    try:
        run = subprocess.run(
            ["git", "ls-tree", "--full-tree", "-r", "-z", tree], cwd=str(cwd), env=env,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LandError(f"cannot enumerate the bounded merge candidate: {exc}") from exc
    if run.returncode != 0 or len(run.stdout) > SANDBOX_MAX_TREE_METADATA_BYTES:
        detail = (run.stderr or b"").decode("utf-8", errors="replace").strip()[-300:]
        raise LandError(f"cannot enumerate the bounded merge candidate: {detail or 'output bound exceeded'}")
    files = []
    for raw in run.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, path = raw.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ", 2)
        except ValueError as exc:
            raise LandError("merge candidate has unreadable file metadata") from exc
        if kind != b"blob" or mode not in (b"100644", b"100755", b"120000"):
            raise LandError("merge candidate contains an unsupported file mode")
        relative = os.fsdecode(path)
        _validate_candidate_path(relative)
        files.append((mode, oid, relative))
    return files


def _materialize_tree(tree: str, path: Path, *, cwd: Path, env: dict[str, str]) -> None:
    """Write raw authenticated blobs without checkout attributes or filters."""
    files = _tree_files(tree, cwd=cwd, env=env)
    path.mkdir()
    for mode, oid, relative in files:
        target = path / relative
        try:
            resolved_parent = target.parent.resolve()
            if path.resolve() != resolved_parent and path.resolve() not in resolved_parent.parents:
                raise LandError("merge candidate tree path escapes its materialization root")
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == b"120000":
                try:
                    blob = subprocess.Popen(
                        ["git", "cat-file", "blob", oid.decode("ascii")], cwd=str(cwd), env=env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                except OSError as exc:
                    raise LandError(f"cannot read merge candidate symlink: {exc}") from exc
                assert blob.stdout is not None and blob.stderr is not None
                link = blob.stdout.read(4096)
                oversized = bool(blob.stdout.read(1))
                if oversized:
                    blob.kill()
                try:
                    blob.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    blob.kill(); blob.wait()
                    oversized = True
                detail = blob.stderr.read(4096).decode("utf-8", errors="replace").strip()[-300:]
                blob.stdout.close(); blob.stderr.close()
                if blob.returncode != 0 or oversized or b"\0" in link:
                    raise LandError(f"cannot read merge candidate symlink: {detail or 'invalid link target'}")
                os.symlink(os.fsdecode(link), target)
                continue
            with target.open("xb") as handle:
                run = subprocess.run(
                    ["git", "cat-file", "blob", oid.decode("ascii")], cwd=str(cwd), env=env,
                    stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.PIPE, timeout=300,
                )
            if run.returncode != 0:
                target.unlink(missing_ok=True)
                detail = (run.stderr or b"").decode("utf-8", errors="replace").strip()[-300:]
                raise LandError(f"cannot read merge candidate blob: {detail or f'exit {run.returncode}'}")
            target.chmod(0o755 if mode == b"100755" else 0o644)
        except LandError:
            raise
        except OSError as exc:
            raise LandError(f"cannot materialize bounded merge candidate path {relative!r}: {exc}") from exc


def _bundle_candidate_git(tree: str, commit: str, base_sha: str, path: Path, *,
                          cwd: Path, env: dict[str, str]) -> None:
    """Build bounded self-contained, credential-free Git metadata for tests.

    The synthetic commit, its shallow parent, and every object reachable from
    the candidate tree are packed locally. The real sandbox mounts this
    directory read-only, so a test can inspect exact history without reaching
    the source repository, host refs/config, credentials, or host ``/tmp``.
    """
    base_tree = _safe_git(["show", "-s", "--format=%T", base_sha], cwd=cwd, env=env,
                          what="cannot resolve candidate base tree")
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_tree):
        raise LandError("cannot resolve candidate base tree")
    objects = {tree.encode("ascii"), commit.encode("ascii"), base_sha.encode("ascii"),
               base_tree.encode("ascii")}
    for label, object_tree in (("candidate", tree), ("base", base_tree)):
        listing = subprocess.run(
            ["git", "ls-tree", "--full-tree", "-r", "-t", "-z", object_tree],
            cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=300,
        )
        if listing.returncode != 0 or len(listing.stdout) > SANDBOX_MAX_TREE_METADATA_BYTES:
            detail = (listing.stderr or b"").decode("utf-8", errors="replace").strip()[-300:]
            raise LandError(f"cannot enumerate {label} Git objects: "
                            f"{detail or 'metadata bound exceeded'}")
        for raw in listing.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                object_metadata, _relative = raw.split(b"\t", 1)
                _mode, _kind, oid = object_metadata.split(b" ", 2)
            except ValueError as exc:
                raise LandError(f"candidate {label} Git object listing is unreadable") from exc
            if not re.fullmatch(rb"[0-9a-f]{40,64}", oid):
                raise LandError(f"candidate {label} Git object listing contains an invalid object ID")
            objects.add(oid)
    metadata = path / ".git"
    (metadata / "objects" / "pack").mkdir(parents=True)
    (metadata / "refs" / "heads").mkdir(parents=True)
    (metadata / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfilemode = true\n"
        "\thooksPath = /dev/null\n\tfsmonitor = false\n"
        "[protocol \"ext\"]\n\tallow = never\n"
    )
    (metadata / "HEAD").write_text("ref: refs/heads/candidate\n")
    (metadata / "refs" / "heads" / "candidate").write_text(commit + "\n")
    (metadata / "shallow").write_text(base_sha + "\n")
    pack_path = metadata / "objects" / "pack" / "candidate.pack"
    object_input = b"\n".join(sorted(objects)) + b"\n"
    with pack_path.open("wb") as output:
        packed = subprocess.run(
            ["git", "pack-objects", "--stdout", "--no-reuse-delta", "--no-reuse-object", "--window=0"],
            cwd=str(cwd), env=env, input=object_input, stdout=output, stderr=subprocess.PIPE, timeout=300,
        )
    if packed.returncode != 0:
        detail = (packed.stderr or b"").decode("utf-8", errors="replace").strip()[-300:]
        raise LandError(f"cannot pack candidate Git metadata: {detail or f'exit {packed.returncode}'}")
    if pack_path.stat().st_size > SANDBOX_MAX_GIT_METADATA_BYTES:
        raise LandError(f"candidate Git metadata exceeds {SANDBOX_MAX_GIT_METADATA_BYTES} bytes")
    embedded_env = dict(env)
    embedded_env["GIT_DIR"] = str(metadata)
    embedded_env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    indexed = subprocess.run(
        ["git", "index-pack", str(pack_path)], cwd=str(path), env=embedded_env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300,
    )
    if indexed.returncode != 0:
        detail = ((indexed.stderr or "") + (indexed.stdout or "")).strip()[-300:]
        raise LandError(f"cannot index candidate Git metadata: {detail or f'exit {indexed.returncode}'}")
    pack_hash = (indexed.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", pack_hash):
        raise LandError("candidate Git pack index returned an invalid object ID")
    index_path = pack_path.with_suffix(".idx")
    final_pack = pack_path.with_name(f"pack-{pack_hash}.pack")
    final_index = pack_path.with_name(f"pack-{pack_hash}.idx")
    try:
        pack_path.rename(final_pack)
        index_path.rename(final_index)
    except OSError as exc:
        raise LandError(f"cannot finalize candidate Git pack: {exc}") from exc
    index_env = dict(embedded_env)
    index_env["GIT_WORK_TREE"] = str(path)
    _safe_git(["read-tree", commit], cwd=path, env=index_env,
              what="cannot build candidate Git index")


def _derive_candidate_tree(root: Path, base_sha: str, head_sha: str, tmp: Path) -> tuple[str, Path, dict[str, str]]:
    """Derive a bounded exact merge tree in a config-independent object store."""
    git_dir, env = _safe_repository(root, tmp)
    trees = []
    for label, commit in (("base tree", base_sha), ("head tree", head_sha)):
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit or ""):
            raise LandError(f"{label} does not name an immutable commit")
        tree = _safe_git(["show", "-s", "--format=%T", commit], cwd=tmp, env=env,
                         what=f"cannot resolve {label}")
        if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise LandError(f"cannot resolve {label}")
        _tree_metrics(tree, cwd=tmp, env=env, label=label)
        trees.append(tree)
    output = _safe_git(["merge-tree", "--write-tree", base_sha, head_sha], cwd=tmp, env=env,
                       what="the base-plus-head merge candidate does not merge cleanly")
    candidate_tree = output.splitlines()[0].strip() if output else ""
    if not re.fullmatch(r"[0-9a-f]{40,64}", candidate_tree):
        raise LandError("cannot derive the exact base-plus-head merge candidate tree")
    _tree_metrics(candidate_tree, cwd=tmp, env=env, label="merge candidate")
    return candidate_tree, git_dir, env


def sanitized_candidate_tree(root: Path, base_sha: str, head_sha: str) -> str:
    """Public tree-only form used by the daemon merge proof."""
    tmp = Path(tempfile.mkdtemp(prefix="alt-land-tree-"))
    try:
        return _derive_candidate_tree(root, base_sha, head_sha, tmp)[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _candidate(root: Path, base_sha: str, head_sha: str):
    """Yield a bounded, config-independent synthetic squash candidate.

    Both input trees and the exact merged tree are preflighted before a single
    candidate path is checked out.  Materialization therefore cannot outrun
    the sandbox's byte/inode envelope, and repository attributes cannot select
    host-configured hooks, filters, fsmonitor or custom merge drivers.
    """
    tmp = Path(tempfile.mkdtemp(prefix="alt-land-candidate-"))
    path = tmp / "candidate"
    try:
        tree, git_dir, env = _derive_candidate_tree(root, base_sha, head_sha, tmp)
        commit_env = dict(env)
        commit_env.update({
            "GIT_AUTHOR_NAME": "alt land", "GIT_AUTHOR_EMAIL": "alt-land@localhost",
            "GIT_COMMITTER_NAME": "alt land", "GIT_COMMITTER_EMAIL": "alt-land@localhost",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        })
        commit = _safe_git(["commit-tree", tree, "-p", base_sha, "-m",
                            "alt land synthetic squash candidate"], cwd=tmp, env=commit_env,
                           what="cannot create the synthetic squash candidate")
        _safe_git(["update-ref", "refs/heads/candidate", commit], cwd=tmp, env=env,
                  what="cannot pin the synthetic squash candidate")
        _safe_git(["symbolic-ref", "HEAD", "refs/heads/candidate"], cwd=tmp, env=env,
                  what="cannot pin the synthetic squash candidate HEAD")
        _materialize_tree(tree, path, cwd=tmp, env=env)
        _bundle_candidate_git(tree, commit, base_sha, path, cwd=tmp, env=env)
        yield path
    finally:
        active_error = sys.exc_info()[1]
        try:
            shutil.rmtree(tmp)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if active_error is not None:
                _note(f"candidate cleanup also failed (preserving the original error): remove {tmp}: {exc}")
            else:
                raise LandError(f"candidate cleanup failed: remove {tmp}: {exc}")


def daemon_candidate_gate(project: str, slug: str, pr: int, base_sha: str, head_sha: str,
                          authority: dict) -> dict:
    """Run the no-CI gate for the daemon's exact, already-authorized PR pair.

    This callback deliberately has no merge authority.  It builds a disposable
    base-plus-head candidate from the task authority's worktree, runs the fixed
    default suite inside :func:`_local_suite`'s Bubblewrap boundary, and returns
    only pair-bound evidence for ``merge_coordinator``.  Every malformed input,
    candidate failure, sandbox refusal, and non-green suite is a closed gate.
    """
    evidence = {
        "sandboxed": True,
        "passed": False,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "tests": None,
        "skipped": None,
        "expected_failures": None,
    }
    try:
        if not project or not slug or type(pr) is not int or pr <= 0:
            raise LandError("candidate gate received an invalid task or PR identity")
        if not (re.fullmatch(r"[0-9a-f]{40}", base_sha or "")
                and re.fullmatch(r"[0-9a-f]{40}", head_sha or "")):
            raise LandError("candidate gate received an invalid base/head pair")
        if not isinstance(authority, dict) or authority.get("head_sha") != head_sha:
            raise LandError("candidate gate authority does not match the pinned PR head")
        raw_worktree = authority.get("worktree")
        if not isinstance(raw_worktree, str) or not raw_worktree or not Path(raw_worktree).is_absolute():
            raise LandError("candidate gate authority has no absolute worktree")
        worktree = Path(raw_worktree).resolve(strict=True)
        top = Path(_need(_git(worktree, "rev-parse", "--show-toplevel"),
                         "candidate gate authority is not a Git worktree")).resolve(strict=True)
        if top != worktree:
            raise LandError("candidate gate authority does not name the worktree root")
        current_head = _need(_git(worktree, "rev-parse", "--verify", "HEAD"),
                             "candidate gate cannot read the authority head")
        if current_head != head_sha:
            raise LandError("candidate gate authority moved from the pinned PR head")
        with _candidate(worktree, base_sha, head_sha) as candidate:
            suite = _local_suite(candidate, DEFAULT_TEST_CMD)
        evidence.update({key: suite.get(key) for key in
                         ("passed", "tests", "skipped", "expected_failures")})
        if suite.get("error"):
            evidence["error"] = suite["error"]
    except Exception as exc:
        evidence["error"] = str(exc) or type(exc).__name__
        _note(f"daemon candidate gate failed closed: {evidence['error']}")
    return evidence


def _merge(root: Path, branch: str, number: int, base: str, expected_head: str) -> tuple[bool, dict | None]:
    """Squash-merge, then believe GitHub about the result, not the exit code — `--delete-branch` can fail on the
    local half (a worktree holds the branch) after the merge itself succeeded. GitHub atomically refuses if the
    PR head changed after the commit whose provenance and checks this invocation validated."""
    m = _run(["gh", "pr", "merge", str(number), "--squash", "--delete-branch",
              "--match-head-commit", expected_head], root, timeout=300)
    after = _pr_view(root, branch) or {}
    if after.get("state") != "MERGED":
        raise LandError(f"gh pr merge #{number}: "
                        f"{((m.stderr or '') + (m.stdout or '')).strip()[-300:] or 'PR is not merged'}")
    if m.returncode != 0:
        _note(f"merged, but gh pr merge exited {m.returncode}: {(m.stderr or '').strip()[-160:]}")
    f = _git(root, "fetch", "origin", base, timeout=120)
    if f.returncode != 0:
        _note(f"git fetch origin {base} failed: {(f.stderr or '').strip()[-160:]}")
    rl = _run(["gh", "run", "list", "--branch", base, "--limit", "1", "--json", "databaseId,status,conclusion"], root)
    if rl.returncode != 0 or not (rl.stdout or "").strip():
        _note(f"gh run list --branch {base} gave nothing: {(rl.stderr or '').strip()[-160:] or 'no output'}")
        return True, None
    try:
        rows = json.loads(rl.stdout)
    except ValueError:
        _note(f"gh run list --branch {base}: unparseable output")
        return True, None
    return True, (rows[0] if rows else None)


def _merge_on_local_suite(root: Path, pair: dict, test_cmd: str) -> tuple[bool, dict | None, dict]:
    """Test one exact base/head pair and merge only while both tips still match it."""
    base_sha, head_sha = pair["base_sha"], pair["head_sha"]
    try:
        _assert_pair_current(root, pair)
        with _candidate(root, base_sha, head_sha) as path:
            tests = _local_suite(path, test_cmd)
    except LandError as exc:
        _note(f"not merging: {exc}")
        return False, None, {"command": test_cmd, "passed": False, "returncode": None, "tests": None,
                             "skipped": None, "expected_failures": None, "error": str(exc),
                             "base": base_sha, "head": head_sha}
    tests.update(base=base_sha, head=head_sha)
    if not tests["passed"]:
        _note(f"not merging: the local suite ({test_cmd}) is not green on the merge candidate")
        return False, None, tests
    try:
        _assert_pair_current(root, pair)
    except LandError:
        tests["error"] = "the base or the head moved while the merge candidate was under test"
        _note(f"not merging: {tests['error']} — re-run alt land to test and merge the current pair")
        return False, None, tests
    after_checks = _checks_state(root, pair["number"])
    try:
        _assert_pair_current(root, pair)
    except LandError:
        tests["error"] = "the base or the head moved while final checks were being read"
        _note(f"not merging: {tests['error']}")
        return False, None, tests
    if after_checks != "none":
        tests["error"] = f"PR checks changed from none to {after_checks} while the local suite ran"
        _note(f"not merging: {tests['error']} — re-run alt land under the current gate")
        return False, None, tests
    merged, main_run = _merge(root, pair["branch"], pair["number"], pair["base"], head_sha)
    return merged, main_run, tests


def land(message: str, *, project: str | None = None, pr_title: str | None = None, pr_body_file: str | None = None,
         merge: bool = False, wait: int = 600, paths: str | None = None, base: str = "main",
         dry_run: bool = False, test_cmd: str = DEFAULT_TEST_CMD, cwd: Path | None = None) -> dict:
    """Run the whole sequence from the current worktree; returns the JSON-ready result object."""
    if not message.strip():
        raise LandError("--message is empty")
    # R-014: until the base-attached trusted-remote artifact is consumed by
    # the landing path, no actor may turn this publishing command into a
    # local-suite or candidate-head-check merge path.  Reject before even
    # resolving the repository so the prohibition is actor-independent and
    # cannot run a test/check callback as a side effect.
    if merge:
        raise LandError(TRUSTED_REMOTE_PENDING)
    root = Path(_need(_git(Path(cwd or Path.cwd()), "rev-parse", "--show-toplevel"), "not a git repository"))
    git_dir = Path(_need(_git(root, "rev-parse", "--git-dir"), "cannot resolve the git dir"))
    if not git_dir.is_absolute():
        git_dir = root / git_dir  # in a worktree `.git` is a file, so resolve the real dir, never assume .git/
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        raise LandError("a rebase is in progress in this worktree — finish it (resolve conflicts, then "
                        "`git rebase --continue`) or back out (`git rebase --abort`) before running alt land")
    ref = _git(root, "symbolic-ref", "-q", "HEAD")
    if ref.returncode != 0:
        sha = (_git(root, "rev-parse", "--short", "HEAD").stdout or "").strip() or "unknown"
        raise LandError(f"detached HEAD at {sha} — alt land runs from a task worktree branch; "
                        f"check out your branch first")
    branch = (ref.stdout or "").strip()
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    if branch in ("main", "master") or branch == base:
        raise LandError(f"on {branch!r} (base {base!r}) — alt land runs from a task worktree branch, "
                        f"never the base branch itself")
    project, slug, task = _resolve(branch, project)
    hold_merge = task.get("hold_merge") if task is not None else None
    if merge and task is None:
        raise LandError(f"cannot verify a merge hold for branch {branch!r}: no task record resolved; "
                        "pass `--project` or run from the dispatch environment")
    if hold_merge:  # A task hold remains an additional restriction under R-014.
        if merge:
            raise LandError(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
                            f"the L3 releases it with `alt task hold-merge {slug} --off`; "
                            "publish the PR without claiming it landed, preserve the hold reason, and stop")
        _note(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
              "the PR will be opened but not merged")
    if task is None or not project or not slug:
        raise LandError(
            f"cannot verify commit provenance for branch {branch!r}: no task record resolved; "
            "pass `--project` or run from the dispatch environment"
        )
    fetched = _git(root, "fetch", "-q", "origin", base)
    if fetched.returncode != 0:
        raise LandError(f"git fetch origin {base}: {(fetched.stderr or fetched.stdout).strip()[-300:]}")
    # Record the branch tip before anything is staged or committed: a later force may replace only this exact
    # remote history, and a push from another worker after this point must make the lease fail.
    recorded_tip = _fetch_remote_tip(root, branch)
    task_ref = f"{project}/{slug}"
    try:
        missing = git_policy.commits_missing_task_trailer(root, base, task_ref)
    except git_policy.GitPolicyError as exc:
        raise LandError(f"cannot verify commit provenance: {exc}") from exc
    if missing:
        sample = ", ".join(sha[:12] for sha in missing[:5])
        raise LandError(
            f"branch has commit(s) without exact `Altitude-Task: {task_ref}` provenance: {sample}; "
            "refusing before staging, pushing, or contacting GitHub"
        )
    if paths is not None:
        lease = [p.strip() for p in paths.split(",") if p.strip()]
        if not lease:
            raise LandError("--paths was given but names no paths")
        lease_src = "--paths"
    elif task is not None:
        lease = dispatch.task_paths(project, task)
        if not lease:
            raise EmptyLeaseError(f"alt land: task {project}/{slug} {EMPTY_LEASE_MESSAGE}")
        lease_src = f"task {project}/{slug}"
    else:
        lease, lease_src = [], None
    groups = _changes(root)
    changed = sorted({p for _, grp in groups for p in grp})
    if lease:
        outside = sorted({p for _, grp in groups for p in grp if not _inside(p, lease)})
        if outside:
            raise LandError(f"changes outside the lease ({', '.join(lease)}, from {lease_src}) — "
                            f"staging nothing: {', '.join(outside)}")
    elif changed:
        _note(f"no task resolved for branch {branch!r} and no --paths given — "
              f"staging all {len(changed)} changed path(s)")
    lease_repr: list[str] | str = lease if lease else UNDECLARED
    commit, staged = None, []
    if not groups:
        _note("working tree clean — nothing to commit")
    if dry_run:
        return {"pr": None, "url": None, "checks": "dry-run", "merged": False, "main_run": None, "branch": branch,
                "commit": None, "head": None, "lease": lease_repr, "staged": changed, "hold": hold_merge,
                "replaced": [], "local_tests": None, "dry_run": True}
    pr = _pr_view(root, branch)
    if pr is not None and pr.get("state") == "MERGED":
        if groups:
            raise LandError(f"PR #{pr.get('number')} for {branch!r} is already merged — this branch has landed; "
                            f"refusing to commit new changes onto it, start a new task branch")
        _note(f"PR #{pr.get('number')} already merged — nothing to push, not resurrecting the branch")
        ahead = _git(root, "rev-list", "--count", f"origin/{branch}..HEAD")
        if ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0"):
            _note(f"warning: {ahead.stdout.strip()} local commit(s) are not on origin/{branch} and will not be "
                  f"pushed onto a merged branch — cherry-pick them onto a new task branch")
        # Nothing was pushed and nothing can be: the PR's checks are history, and asking for them costs a
        # `gh pr checks` round trip whose answer cannot change this run. `merged` is its own checks value,
        # never reported as a pass (decision 36).
        return {"pr": pr.get("number"), "url": pr.get("url"), "checks": "merged",
                "merged": True, "main_run": None, "branch": branch, "commit": None, "head": None,
                "lease": lease_repr, "staged": [], "hold": hold_merge, "replaced": [], "local_tests": None}
    if pr is not None and pr.get("state") == "CLOSED":
        raise LandError(f"PR #{pr.get('number')} for {branch!r} is closed without being merged — refusing to "
                        f"stage, commit or push onto a closed PR: reopen it (`gh pr reopen {pr.get('number')}`) "
                        f"and re-run alt land, or start a new task branch")
    if groups:
        fd, spec = tempfile.mkstemp(prefix="alt-land-pathspec-")
        try:  # NUL-separated :(literal) pathspecs: a path like `a[1].py` is a filename, never a glob
            with os.fdopen(fd, "w") as fh:
                fh.write("\0".join(f":(literal){p}" for p in changed))
            _need(_git(root, "add", "-A", f"--pathspec-from-file={spec}", "--pathspec-file-nul"), "git add")
        finally:
            Path(spec).unlink(missing_ok=True)
        if _git(root, "diff", "--cached", "--quiet").returncode != 0:
            trailer = ([f"Altitude-Task: {project}/{slug}"] if project and slug else []) + [TRAILER]
            _need(_git(root, "commit", "-m", message.rstrip("\n") + "\n\n" + "\n".join(trailer)), "git commit")
            commit = _need(_git(root, "rev-parse", "HEAD"), "git rev-parse HEAD")
            staged = changed
            _note(f"committed {commit[:7]} ({len(staged)} path(s))")
        else:
            _note("staged changes match HEAD — nothing to commit")
    replaced = _push(root, branch, base, task_ref, recorded_tip)
    pushed_head = _need(_git(root, "rev-parse", f"origin/{branch}"), "cannot capture the pushed PR head")
    _note(f"pushed head {pushed_head}")
    pr = _ensure_pr(root, branch, base, message, pr_title, pr_body_file, task_ref, pr=pr)
    number = pr.get("number")
    pair = _snapshot_pair(root, branch, number, base, pushed_head)
    checks = _checks_value(root, number, pair)
    deadline = time.monotonic() + max(wait, 0)
    while checks == "pending" and time.monotonic() < deadline:
        time.sleep(min(CHECK_POLL_SECONDS, max(deadline - time.monotonic(), 1.0)))
        checks = _checks_value(root, number, pair)
    merged, main_run, local_tests = pr.get("state") == "MERGED", None, None
    if merge and not merged:
        if checks == "none-configured":
            merged, main_run, local_tests = _merge_on_local_suite(root, pair, test_cmd)
        elif checks == "pass":
            _assert_pair_current(root, pair)
            merged, main_run = _merge(root, branch, number, base, pushed_head)
        else:
            _note(f"not merging: checks are {checks!r}")
    return {"pr": number, "url": pr.get("url"), "checks": checks, "merged": merged, "main_run": main_run,
            "branch": branch, "commit": commit, "head": pushed_head, "lease": lease_repr, "staged": staged,
            "hold": hold_merge, "replaced": replaced, "local_tests": local_tests}
