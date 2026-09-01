"""The guarded land-a-PR sequence as one deterministic command.

Stage only the task's lease (refuse if anything outside it changed), commit with the Altitude trailer,
push with one force-with-lease retry against the branch tip recorded before committing (never two), open or
reuse the PR, wait for checks, merge only on green — or, where the repository configures no CI at all, on a
full local suite that passed on the base-plus-head merge candidate — and only when asked. No
model call anywhere — the commit message arrives as an argument. Idempotent: nothing to commit is a skip, an
up-to-date push is a no-op, an open PR is reused.

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
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, dispatch, git_policy, state as S

CHECK_POLL_SECONDS = 15
LOCAL_TEST_TIMEOUT = 1800
DEFAULT_TEST_CMD = "make test"
UNDECLARED = "(undeclared — all changes staged)"
EMPTY_LEASE_MESSAGE = "lease is empty: pass --paths or set the task paths"
#: `_ensure_pr(pr=...)` default: no lookup has happened yet. `None` means the caller already looked and the
#: branch has no PR, so the create path must not look a second time.
NOT_PREFETCHED = object()


class LandError(RuntimeError):
    """A refusal or a dead end the caller must see; bin/alt prints it on stderr and exits non-zero."""


class EmptyLeaseError(LandError):
    """The task resolved correctly, but it grants no files for this landing."""


def _run(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Every git/gh invocation funnels through here so tests can drive the whole pipeline offline."""
    try:
        return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
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


def _require_current_publisher(project: str, slug: str, task: dict, authority: dict | None = None) -> None:
    """Fence automated landing to the exact current L2 attempt.

    A hand-run command has no actor (or explicitly names Burak). Every automated
    caller must be the L2 that owns the task now: L1s and control-plane actors do
    not publish. Physical worker replacement first proves the old worker stopped;
    the capability token remains stable only within this dispatch attempt.
    """
    actor = authority.get("actor") if authority is not None else os.environ.get("ALTITUDE_ACTOR")
    if actor is None or actor == "burak":
        return
    if actor != "l2":
        raise LandError(f"actor {actor!r} cannot land {project}/{slug}; only the current L2 or Burak may land")
    if task.get("state") != "running":
        raise LandError(f"current L2 cannot land {project}/{slug}: task is not running")
    dispatch_id = authority.get("dispatch_id") if authority is not None else os.environ.get("ALTITUDE_DISPATCH_ID")
    if not dispatch_id or task.get("dispatch_id") != dispatch_id:
        raise LandError(f"current L2 cannot land {project}/{slug}: dispatch ownership changed")
    l2_token = (authority.get("l2_token") if authority is not None
                else os.environ.get("ALTITUDE_L2_TOKEN") or os.environ.get("ALTITUDE_L2_CAPABILITY"))
    if not l2_token or task.get("l2_token") != l2_token:
        raise LandError(f"current L2 cannot land {project}/{slug}: ownership capability changed")


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
    """A directory lease covers everything beneath it. Entries are
    repo-root-relative; a malformed absolute entry like `/src` is read as `src` rather than matching nothing."""
    return dispatch.inside_lease(path, lease)


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
            return None  # legitimately missing, not a tooling failure
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
    caller, which knows whether the repository configures CI, can say what it means."""
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
                        "refusing to read them as a pass; this gates --merge")
    if not buckets:
        return "none"
    if buckets & {"fail", "cancel"}:
        return "fail"
    if "pending" in buckets:
        return "pending"
    # A rollup that mixes passes with skips is not a pass: the skipped check is a configured gate that did
    # not run; a configured gate is never satisfied by its absence.
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


def _local_suite(cwd: Path, test_cmd: str) -> dict:
    """Run and count the full local suite in the synthetic merge candidate."""
    argv = shlex.split(test_cmd)
    if not argv:
        raise LandError("the local test command is empty")
    _note(f"no CI configured — the merge candidate's local suite is the gate: {test_cmd}")
    result = {"command": test_cmd, "passed": False, "returncode": None, "tests": None, "skipped": None,
              "expected_failures": None, "error": None}
    try:
        run = _run(argv, cwd, timeout=LOCAL_TEST_TIMEOUT)
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
                           + " — the local gate is not satisfied")
        _note(f"not merging: {result['error']}")
    else:
        _note(f"local suite exited {run.returncode}"
              + (f" ({tests} passing, {skipped} skipped, {expected} expected failures)"
                 if tests is not None else " (count unreadable)"))
    return result


@contextlib.contextmanager
def _candidate(root: Path, base_sha: str, head_sha: str):
    """Yield a clean temporary worktree with the single-parent squash history GitHub will create."""
    tmp = Path(tempfile.mkdtemp(prefix="alt-land-candidate-"))
    path = tmp / "candidate"
    try:
        worktree = _git(root, "worktree", "add", "--detach", str(path), base_sha, timeout=300)
        if worktree.returncode != 0:
            raise LandError(f"cannot build the merge candidate worktree: "
                            f"{((worktree.stderr or '') + (worktree.stdout or '')).strip()[-200:]}")
        merged = _git(path, "merge", "--squash", head_sha, timeout=300)
        if merged.returncode != 0:
            raise LandError("the base-plus-head merge candidate does not merge cleanly — GitHub would refuse "
                            f"this merge too: {((merged.stderr or '') + (merged.stdout or '')).strip()[-200:]}")
        committed = _git(path, "-c", "user.name=alt land", "-c", "user.email=alt-land@localhost",
                         "-c", "commit.gpgsign=false", "commit", "-m", "alt land synthetic squash candidate",
                         timeout=300)
        if committed.returncode != 0:
            raise LandError("cannot commit the synthetic squash candidate: "
                            f"{((committed.stderr or '') + (committed.stdout or '')).strip()[-200:]}")
        yield path
    finally:
        active_error = sys.exc_info()[1]
        cleanup_errors = []
        remove_failed = False
        try:
            removed = _git(root, "worktree", "remove", "--force", str(path), timeout=300)
            if removed.returncode != 0:
                remove_failed = True
                cleanup_errors.append(((removed.stderr or "") + (removed.stdout or "")).strip())
        except LandError as exc:
            remove_failed = True
            cleanup_errors.append(str(exc))
        try:
            shutil.rmtree(tmp)
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"remove {tmp}: {exc}")
        if remove_failed:
            try:
                pruned = _git(root, "worktree", "prune")
                if pruned.returncode != 0:
                    cleanup_errors.append(((pruned.stderr or "") + (pruned.stdout or "")).strip())
            except LandError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            detail = "; ".join(error for error in cleanup_errors if error) or "unknown cleanup error"
            if active_error is not None:
                _note(f"candidate cleanup also failed (preserving the original error): {detail}")
            else:
                raise LandError(f"candidate cleanup failed: {detail}")


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
         dry_run: bool = False, test_cmd: str = DEFAULT_TEST_CMD, cwd: Path | None = None,
         authority: dict | None = None) -> dict:
    """Run the whole sequence from the current worktree; returns the JSON-ready result object."""
    if not message.strip():
        raise LandError("--message is empty")
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
    if hold_merge:  # an explicit merge hold is the exception to merge-by-default
        if merge:
            raise LandError(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
                            f"the L3 releases it with `alt task hold-merge {slug} --off`; "
                            "re-run `alt land` without `--merge` — open the PR, report ok with the PR number, stop")
        _note(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
              "the PR will be opened but not merged")
    if task is None or not project or not slug:
        raise LandError(
            f"cannot verify commit provenance for branch {branch!r}: no task record resolved; "
            "pass `--project` or run from the dispatch environment"
        )
    # This is deliberately before fetch, staging, or any GitHub call.  Put the
    # fence in the library rather than only in bin/alt so direct callers cannot
    # bypass current-publisher ownership.
    _require_current_publisher(project, slug, task, authority=authority)
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
        # never reported as a pass.
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
            trailer = [f"Altitude-Task: {project}/{slug}"] if project and slug else []
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
