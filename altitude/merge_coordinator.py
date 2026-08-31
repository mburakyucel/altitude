"""Daemon-owned, generation-bound PR merge coordination.

Models may ask for a merge by project, task slug, and PR number.  They do not
choose a ref, SHA, repository path, test command, or merge command.  This
module derives those facts from durable task/L1 records and GitHub, pins them
in a request, and gives the daemon the only function that invokes
``gh pr merge``.

The CLI and model broker expose only :func:`request`.  In particular, a
brokered model command cannot call :func:`process`; only the host tick does.

Merge requests preserve durable intent, but R-014 deliberately disconnects
the landing path until the coordinator can consume the immutable,
base-attached ``trusted-remote / evidence`` artifact.  Generic check-rollups
and local candidate suites are never landing authority.
"""
from __future__ import annotations

import os
import json
import re
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import config, dispatch, engines, git_policy, l1, state as S, verify


BASE = "main"
CLAIM_TIMEOUT_SECONDS = 900
MAX_ATTEMPTS = 3
RETRY_SECONDS = (60, 300, 900)
_ACTIVE_L1 = {"reserved", "preparing", "launching", "starting", "running", "stopping", "stop-failed"}
_FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
_NONPASS = {"NEUTRAL", "SKIPPED"}
_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TRUSTED_REMOTE_PENDING = (
    "trusted remote landing integration is pending: the coordinator requires an immutable "
    "base-attached trusted-remote / evidence artifact; generic checks and local suites do not authorize landing"
)


class MergeCoordinatorError(RuntimeError):
    """A fail-closed coordinator refusal."""


class MergePending(MergeCoordinatorError):
    """The pinned request remains valid, but an external gate is not ready."""


def _record_path(project: str, slug: str, pr: int) -> Path:
    return S.task_dir(project, slug) / f"merge-request-{pr}.json"


def _history_path(project: str, slug: str, pr: int) -> Path:
    return S.task_dir(project, slug) / f"merge-request-{pr}-history.jsonl"


def _same_request(rec: object, *, project: str, slug: str, pr: int, dispatch_id: str,
                  task_attempt: object, branch: str, base_sha: str, head_sha: str,
                  candidate_tree: str, authority: dict, reviewer: dict) -> bool:
    """Exact immutable-input equality; a same head on a moved main is not the same request."""
    return bool(isinstance(rec, dict)
                and rec.get("project") == project and rec.get("slug") == slug and rec.get("pr") == pr
                and isinstance(rec.get("generation"), str) and rec.get("generation")
                and rec.get("dispatch_id") == dispatch_id and rec.get("task_attempt") == task_attempt
                and rec.get("branch") == branch and rec.get("base") == BASE
                and rec.get("base_sha") == base_sha and rec.get("head_sha") == head_sha
                and rec.get("candidate_tree") == candidate_tree
                and rec.get("authority") == authority and rec.get("reviewer") == reviewer)


def _pr_number(value: object) -> int:
    if type(value) is not int or value <= 0:  # bool is deliberately not an int here
        raise MergeCoordinatorError("the PR number must be a positive integer")
    return value


def _pid_start(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        return int(Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _claim_liveness(claim: object) -> bool | None:
    """Return exact owner liveness; indeterminate evidence stays fail-closed."""
    if not isinstance(claim, dict):
        return False
    pid, expected = claim.get("owner_pid"), claim.get("owner_pid_start")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(expected, int):
        return None
    try:
        current = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, IndexError):
        return None
    return current == expected


def status(project: str, slug: str, pr: int) -> dict | None:
    """Return a durable request without changing or refreshing it."""
    return S.read_json(_record_path(project, slug, _pr_number(pr)), None)


def _task_current(task: dict, dispatch_id: str) -> bool:
    return bool(task.get("state") == "running" and dispatch_id
                and task.get("dispatch_id") == dispatch_id
                and not task.get("block_pending"))


def _load_current_task(project: str, slug: str, dispatch_id: str | None = None) -> dict:
    task = S.load_task(project, slug)
    current = str(task.get("dispatch_id") or "")
    if task.get("block_pending"):
        raise MergePending("task worker block is pending exact L1/L2 stop proof")
    if not _task_current(task, str(dispatch_id or current)):
        raise MergeCoordinatorError("merge requests require the task's current running dispatch generation")
    if task.get("hold_merge"):
        raise MergePending(f"task carries a merge hold: {task['hold_merge']}")
    return task


def _load_claim_task(project: str, slug: str, dispatch_id: str) -> dict:
    """Load the exact request generation, including post-worker closeout.

    Pre-merge authorization remains running-only.  A blocked/reported task may
    acquire a coordinator claim solely so an already-merged PR can be adopted
    after an operator resolves an unsupported automatic-merge hold.  Exact
    dispatch/attempt, stopped workers and every pinned proof are revalidated
    in :func:`process` before that adoption becomes task-owned evidence.
    """
    task = S.load_task(project, slug)
    if task.get("block_pending"):
        raise MergePending("task worker block is pending exact L1/L2 stop proof")
    if (not dispatch_id or task.get("dispatch_id") != dispatch_id
            or task.get("state") not in {"running", "blocked", "reported"}):
        raise MergeCoordinatorError("merge request no longer belongs to a current task closeout generation")
    if task.get("state") == "running" and task.get("hold_merge"):
        raise MergePending(f"task carries a merge hold: {task['hold_merge']}")
    return task


def _pr_view(project: str, pr: int) -> dict:
    fields = ("number,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,mergeable,"
              "mergeStateStatus,reviewDecision,mergeCommit,statusCheckRollup")
    try:
        value = verify.gh(["pr", "view", str(pr), "--json", fields], config.project_path(project))
    except verify.VerifierFault as exc:
        raise MergePending(f"PR #{pr} evidence unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise MergePending(f"PR #{pr} is unavailable")
    return value


def _validate_pr_shape(pr: int, info: dict, *, branch: str | None = None,
                       base_sha: str | None = None, head_sha: str | None = None) -> tuple[str, str, str]:
    if info.get("number") not in (None, pr):
        raise MergeCoordinatorError(f"GitHub returned the wrong PR for #{pr}")
    if info.get("state") != "OPEN":
        raise MergeCoordinatorError(f"PR #{pr} is not open")
    if info.get("isDraft") is not False:
        raise MergeCoordinatorError(f"PR #{pr} is draft or its draft state is indeterminate")
    if info.get("baseRefName") != BASE:
        raise MergeCoordinatorError(f"PR #{pr} does not target {BASE}")
    actual_branch = str(info.get("headRefName") or "")
    actual_base = str(info.get("baseRefOid") or "")
    actual_head = str(info.get("headRefOid") or "")
    if not actual_branch or not _SHA.fullmatch(actual_base) or not _SHA.fullmatch(actual_head):
        raise MergeCoordinatorError(f"PR #{pr} does not expose one immutable base/head pair")
    if branch is not None and actual_branch != branch:
        raise MergeCoordinatorError(f"PR #{pr} head branch changed")
    if base_sha is not None and actual_base != base_sha:
        raise MergeCoordinatorError(f"PR #{pr} base moved")
    if head_sha is not None and actual_head != head_sha:
        raise MergeCoordinatorError(f"PR #{pr} head moved")
    if str(info.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        raise MergeCoordinatorError(f"PR #{pr} has a changes-requested review")
    return actual_branch, actual_base, actual_head


def _validate_merged_shape(project: str, pr: int, info: dict, *, branch: str, base_sha: str,
                           head_sha: str, candidate_tree: str) -> tuple[str, dict]:
    """Prove an adopted/post-command merge is the exact request, not merely the same PR number."""
    if (info.get("state") != "MERGED" or info.get("baseRefName") != BASE
            or info.get("headRefName") != branch or info.get("headRefOid") != head_sha):
        raise MergeCoordinatorError(f"PR #{pr} is not merged at the pinned base/head pair")
    # GitHub's baseRefOid follows the base branch and commonly advances to the
    # squash commit after a successful merge.  The pinned base is therefore
    # authorized immediately before the match-head operation, while the stable
    # postcondition is exact PR/head/base-name plus an immutable merge commit.
    if not _SHA.fullmatch(base_sha):
        raise MergeCoordinatorError(f"PR #{pr} request has no pinned base")
    commit = info.get("mergeCommit") if isinstance(info.get("mergeCommit"), dict) else {}
    merge_sha = str(commit.get("oid") or "")
    if not _SHA.fullmatch(merge_sha):
        raise MergePending(f"PR #{pr} merge commit is not available yet")
    proof = _post_merge_proof(project, merge_sha, base_sha, candidate_tree)
    return merge_sha, proof


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    run_env = engines.clean_env()
    run_env.pop("GIT_DIR", None)
    run_env.pop("GIT_WORK_TREE", None)
    if env:
        run_env.update(env)
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                            timeout=120, env=run_env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-300:]
        raise MergeCoordinatorError(f"git {' '.join(args[:3])}: {detail or f'exit {result.returncode}'}")
    return (result.stdout or "").strip()


def _fetch_object(project: str, sha: str, *, fallback_ref: str) -> None:
    """Fetch one exact reachable object without updating a protected local ref."""
    if not _SHA.fullmatch(sha):
        raise MergeCoordinatorError("cannot fetch a non-SHA merge candidate object")
    repo = config.project_path(project)
    try:
        if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}") == "":
            return
    except MergeCoordinatorError:
        pass
    errors = []
    for source in (sha, fallback_ref):
        try:
            _git(repo, "fetch", "--no-tags", "--quiet", "origin", source)
            _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
            return
        except MergeCoordinatorError as exc:
            errors.append(str(exc))
    raise MergePending(f"cannot fetch exact Git object {sha[:12]}: {'; '.join(errors)[-500:]}")


def _candidate_tree(project: str, pr: int, base_sha: str, head_sha: str) -> str:
    """Compute the tree GitHub's squash commit must have for this exact pair."""
    _fetch_object(project, base_sha, fallback_ref=BASE)
    _fetch_object(project, head_sha, fallback_ref=f"pull/{pr}/head")
    # Candidate derivation must not read the project/global Git config:
    # versioned attributes can otherwise select a host custom merge driver.
    from . import land
    try:
        return land.sanitized_candidate_tree(config.project_path(project), base_sha, head_sha)
    except land.LandError as exc:
        raise MergeCoordinatorError(f"cannot derive an exact clean merge tree for the pinned PR pair: {exc}") from exc


def _post_merge_proof(project: str, merge_sha: str, base_sha: str, candidate_tree: str) -> dict:
    """Prove GitHub created the exact pinned synthetic squash, not merely a same-head merge."""
    _fetch_object(project, merge_sha, fallback_ref=BASE)
    repo = config.project_path(project)
    row = _git(repo, "rev-list", "--parents", "-n", "1", merge_sha).split()
    if len(row) != 2 or row[0] != merge_sha or row[1] != base_sha:
        raise MergeCoordinatorError(
            "merged PR is not a one-parent squash directly on the pinned base")
    actual_tree = _git(repo, "show", "-s", "--format=%T", merge_sha)
    if actual_tree != candidate_tree:
        raise MergeCoordinatorError(
            "merged PR tree differs from the exact pinned base/head squash candidate")
    return {"merge_sha": merge_sha, "parent_sha": base_sha,
            "tree_sha": actual_tree, "candidate_tree": candidate_tree}


def _inside(path: str, lease: list[str]) -> bool:
    path = dispatch._norm(path).lstrip("/")
    return any(path == dispatch._norm(item).lstrip("/")
               or path.startswith(dispatch._norm(item).lstrip("/") + "/") for item in lease)


def _latest_exact_run(runs: list[dict], *, evidence: str) -> dict:
    """Select the latest durable L1 generation by its monotonic sequence.

    A later exact run supersedes earlier evidence for the same dispatch/PR
    pair. Falling back to an older clean reviewer or authority after a later
    attempt is unsafe. Missing or duplicate sequence identity is ambiguous and
    therefore refused.
    """
    by_sequence: dict[int, dict] = {}
    for run in runs:
        sequence = run.get("n")
        if (type(sequence) is not int or sequence < 1 or not run.get("name")
                or not run.get("generation")):
            raise MergeCoordinatorError(f"{evidence} has incomplete L1 generation identity")
        if sequence in by_sequence:
            raise MergeCoordinatorError(f"{evidence} has ambiguous L1 sequence {sequence}")
        by_sequence[sequence] = run
    if not by_sequence:
        raise MergeCoordinatorError(f"{evidence} has no exact L1 generation")
    return by_sequence[max(by_sequence)]


def _missing_trailers(cwd: Path, env: dict[str, str], origin_sha: str,
                      head_sha: str, task_ref: str) -> list[str]:
    rows = _git(cwd, "rev-list", "--reverse", f"{origin_sha}..{head_sha}", env=env)
    missing = []
    fmt = f"%(trailers:key={git_policy.TASK_TRAILER},valueonly,separator=%x00)"
    for sha in rows.splitlines():
        values = {value.strip() for value in
                  _git(cwd, "show", "--quiet", f"--format={fmt}", sha, env=env).split("\0")
                  if value.strip()}
        if values != {task_ref}:
            missing.append(sha)
    return missing


def _require_pinned_hooks(cwd: Path, env: dict[str, str]) -> None:
    raw = _git(cwd, "config", "--path", "--get", "core.hooksPath", env=env)
    path = Path(raw)
    actual = path.resolve() if path.is_absolute() else (cwd / path).resolve()
    expected = config.HOOKS.resolve()
    if actual != expected:
        raise MergeCoordinatorError("task-owned Git authority changed its hardened hook path")
    missing = [name for name in git_policy.REQUIRED_HOOKS
               if not (expected / name).is_file() or not os.access(expected / name, os.X_OK)]
    if missing:
        raise MergeCoordinatorError(f"hardened Git hook set is incomplete: {', '.join(missing)}")


def _authority(project: str, slug: str, pr: int, task: dict, head_sha: str, branch: str) -> dict:
    """Prove that the PR head belongs to this exact task generation and lease."""
    dispatch_id = str(task.get("dispatch_id") or "")
    runs = l1.list_runs(project, slug)
    owners = [run for run in runs
              if run.get("dispatch_id") == dispatch_id and run.get("role") == "implementer"
              and run.get("state") == "done" and run.get("done")
              and (run.get("result") or {}).get("pr") == pr
              and run.get("head_sha") == head_sha and run.get("branch") == branch]
    if owners:
        owner = _latest_exact_run(owners, evidence=f"PR #{pr} task-owned authority")
        proof = l1.validate_broker_worktree(project, slug, owner, Path(str(owner.get("worktree") or "")),
                                            dispatch_id=dispatch_id)
        cwd = Path(proof["GIT_WORK_TREE"])
        git_env = {"GIT_DIR": proof["GIT_DIR"], "GIT_WORK_TREE": proof["GIT_WORK_TREE"]}
        kind = "l1"
    else:
        cwd = Path(str(task.get("worktree") or ""))
        if not cwd.is_dir():
            raise MergeCoordinatorError(f"PR #{pr} has no exact task-owned worktree authority")
        try:
            git_dir = dispatch._validate_task_worktree(config.project_path(project), project, slug, cwd,
                                                       str(task.get("origin_sha") or ""), require_clean=True)
        except Exception as exc:
            raise MergeCoordinatorError(f"task worktree authority failed: {exc}") from exc
        git_env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(cwd)}
        kind = "l2"
    _require_pinned_hooks(cwd, git_env)
    project_remote = _git(config.project_path(project), "remote", "get-url", "origin")
    authority_remote = _git(cwd, "remote", "get-url", "origin", env=git_env)
    if l1._canonical_remote_url(cwd, authority_remote) != l1._canonical_remote_url(
            config.project_path(project), project_remote):
        raise MergeCoordinatorError("task-owned Git authority changed its canonical origin")
    if _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD", env=git_env) != branch:
        raise MergeCoordinatorError("task-owned branch changed")
    if _git(cwd, "rev-parse", "--verify", "HEAD", env=git_env) != head_sha:
        raise MergeCoordinatorError("task-owned head differs from the pinned PR head")
    if _git(cwd, "status", "--porcelain", "--untracked-files=all", env=git_env):
        raise MergeCoordinatorError("task-owned worktree is dirty")
    origin_sha = str(task.get("origin_sha") or "")
    if not _SHA.fullmatch(origin_sha):
        raise MergeCoordinatorError("task dispatch has no immutable origin SHA")
    missing = _missing_trailers(cwd, git_env, origin_sha, head_sha, f"{project}/{slug}")
    if missing:
        raise MergeCoordinatorError("PR head contains commit(s) without exact task provenance")
    changed = [line for line in _git(cwd, "diff", "--name-only", f"{origin_sha}..{head_sha}", env=git_env).splitlines()
               if line]
    lease = dispatch.task_paths(project, task)
    if not lease:
        raise MergeCoordinatorError("task has no file lease")
    outside = [path for path in changed if not _inside(path, lease)]
    if outside:
        raise MergeCoordinatorError(f"PR changes path(s) outside the task lease: {', '.join(outside[:10])}")
    owner_proof = ({"name": owner.get("name"), "generation": owner.get("generation"), "n": owner.get("n")}
                   if owners else {"dispatch_id": dispatch_id,
                                   "task_attempt": task.get("attempt")})
    return {"kind": kind, "worktree": str(cwd), "branch": branch, "head_sha": head_sha,
            "origin_sha": origin_sha, "files": changed, "lease": lease,
            "owner": owner_proof}


def _active_runs(project: str, slug: str, dispatch_id: str) -> list[str]:
    return [str(run.get("name")) for run in l1.list_runs(project, slug)
            if run.get("dispatch_id") == dispatch_id and run.get("state") in _ACTIVE_L1]


def _clean_reviewer(project: str, slug: str, pr: int, dispatch_id: str, head_sha: str,
                    base_sha: str, candidate_tree: str) -> dict:
    attempts = []
    for run in l1.list_runs(project, slug):
        if (run.get("dispatch_id") == dispatch_id and run.get("role") == "reviewer"
                and run.get("state") in {"done", "stopped"} and run.get("done")
                and run.get("review_pr") == pr
                and run.get("review_head_at_start") == head_sha
                and run.get("review_base_at_start") == base_sha):
            attempts.append(run)
    if not attempts:
        raise MergeCoordinatorError(
            f"PR #{pr} requires a terminal clean reviewer bound to this PR/base/head")
    run = _latest_exact_run(attempts, evidence=f"PR #{pr} reviewer evidence")
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    structured = result.get("structured") if isinstance(result.get("structured"), dict) else None
    findings = structured.get("findings") if structured else None
    if (result.get("review_pr") != pr or result.get("reviewed_head_sha") != head_sha
            or result.get("reviewed_base_sha") != base_sha or result.get("error") or findings != []):
        raise MergeCoordinatorError(
            f"PR #{pr} latest exact reviewer generation is not a clean base/head-bound review")
    return {"name": run.get("name"), "generation": run.get("generation"), "n": run.get("n"),
            "head_sha": head_sha, "base_sha": base_sha,
            "candidate_tree": candidate_tree, "reviewed_pr": pr}


def _checks(info: dict) -> str:
    rows = info.get("statusCheckRollup")
    if not isinstance(rows, list):
        raise MergePending("PR check rollup is indeterminate")
    if not rows:
        return "none"
    states = {str((row if isinstance(row, dict) else {}).get("conclusion")
                  or (row if isinstance(row, dict) else {}).get("state") or "").upper() for row in rows}
    if states & _FAILED:
        return "fail"
    # GitHub has several terminal conclusions which are not evidence that the
    # configured check actually ran and passed (for example NEUTRAL and
    # SKIPPED).  Fail closed: every configured row must literally be SUCCESS.
    if states == {"SUCCESS"}:
        return "pass"
    if states & _NONPASS:
        return "reject"
    return "pending"


def _candidate_evidence(value: object, base_sha: str, head_sha: str) -> dict:
    evidence = value if isinstance(value, dict) else {}
    if (evidence.get("sandboxed") is not True or evidence.get("passed") is not True
            or evidence.get("base_sha") != base_sha or evidence.get("head_sha") != head_sha
            or type(evidence.get("tests")) is not int or evidence["tests"] <= 0):
        raise MergeCoordinatorError("sandbox candidate gate returned incomplete or mismatched evidence")
    return {key: evidence.get(key) for key in
            ("sandboxed", "passed", "base_sha", "head_sha", "tests", "skipped", "expected_failures")}


def _server_stale_base_guard(project: str, info: dict) -> dict:
    """Prove GitHub will atomically refuse a merge on a newly advanced base.

    ``--match-head-commit`` protects only the PR head. The supported server
    mechanism here is classic branch protection with strict, nonempty required
    status checks: GitHub re-evaluates the latest base at the atomic REST merge
    operation. A local suite, green optional checks, an unreadable endpoint,
    plan-level 403, or merely observing the base twice is not such a guard.
    """
    if (_checks(info) != "pass" or not isinstance(info.get("statusCheckRollup"), list)
            or not info["statusCheckRollup"]):
        raise MergePending("automatic merge requires nonempty green GitHub checks under a server stale-base guard")
    cwd = config.project_path(project)
    try:
        identity = verify.gh(["repo", "view", "--json", "nameWithOwner"], cwd)
    except verify.VerifierFault as exc:
        raise MergePending(f"server stale-base guard repository identity is unavailable: {exc}") from exc
    repo = str((identity if isinstance(identity, dict) else {}).get("nameWithOwner") or "")
    if not _REPO.fullmatch(repo):
        raise MergePending("server stale-base guard repository identity is unavailable")
    try:
        protection = verify.gh(["api", f"repos/{repo}/branches/{BASE}/protection"], cwd)
    except verify.VerifierFault as exc:
        # Private repositories on plans without branch-protection API support
        # return 403 here. That is absence of proof, never permission to merge.
        raise MergePending(f"server stale-base guard is unavailable: {exc}") from exc
    required_status = (protection.get("required_status_checks")
                       if isinstance(protection, dict) else None)
    enforce_admins = protection.get("enforce_admins") if isinstance(protection, dict) else None
    if not isinstance(required_status, dict) or required_status.get("strict") is not True:
        raise MergePending("server stale-base guard is unavailable: required status checks are not strict")
    if not isinstance(enforce_admins, dict) or enforce_admins.get("enabled") is not True:
        raise MergePending("server stale-base guard is unavailable: administrators can bypass branch protection")
    required = set()
    contexts = required_status.get("contexts")
    if isinstance(contexts, list):
        required.update(value for value in contexts if isinstance(value, str) and value.strip())
    checks = required_status.get("checks")
    if isinstance(checks, list):
        required.update(str(value.get("context")) for value in checks
                        if isinstance(value, dict) and isinstance(value.get("context"), str)
                        and value["context"].strip())
    if not required:
        raise MergePending("server stale-base guard is unavailable: no required status checks are configured")
    return {"kind": "strict-required-status-checks", "repository": repo, "base": BASE,
            "strict": True, "enforce_admins": True, "required_checks": sorted(required)}


def _readiness(project: str, slug: str, pr: int, task: dict, info: dict,
               candidate_gate: Callable[..., dict] | None = None, *, require_gate: bool = True,
               allow_external_pending: bool = False) -> dict:
    branch, base_sha, head_sha = _validate_pr_shape(pr, info)
    dispatch_id = str(task.get("dispatch_id") or "")
    active = _active_runs(project, slug, dispatch_id)
    if active:
        raise MergePending(f"task still has active L1 generation(s): {', '.join(active)}")
    authority = _authority(project, slug, pr, task, head_sha, branch)
    candidate_tree = _candidate_tree(project, pr, base_sha, head_sha)
    reviewer = _clean_reviewer(project, slug, pr, dispatch_id, head_sha, base_sha, candidate_tree)
    mergeable = str(info.get("mergeable") or "").upper()
    if mergeable == "CONFLICTING":
        raise MergeCoordinatorError(f"PR #{pr} conflicts with {BASE}")
    if mergeable != "MERGEABLE" and not allow_external_pending:
        raise MergePending(f"PR #{pr} mergeability is indeterminate")
    check_state = _checks(info)
    gate = None
    if check_state == "fail":
        raise MergeCoordinatorError(f"PR #{pr} checks failed")
    if check_state == "reject":
        raise MergeCoordinatorError(f"PR #{pr} checks did not literally succeed")
    if check_state == "pending" and not allow_external_pending:
        raise MergePending(f"PR #{pr} checks are pending")
    if check_state == "none":
        if candidate_gate is None and require_gate:
            raise MergePending("PR has no green CI and no trusted sandbox candidate gate is installed")
        if candidate_gate is not None:
            gate = _candidate_evidence(candidate_gate(project=project, slug=slug, pr=pr,
                                                       base_sha=base_sha, head_sha=head_sha,
                                                       authority=authority), base_sha, head_sha)
    return {"branch": branch, "base_sha": base_sha, "head_sha": head_sha,
            "authority": authority, "reviewer": reviewer, "checks": check_state,
            "candidate_tree": candidate_tree, "candidate_gate": gate}


def request(project: str, slug: str, pr: int) -> dict:
    """Pin a model's non-authoritative merge request to current host evidence."""
    pr = _pr_number(pr)
    superseded = None
    with S.project_lock(project):
        task = _load_current_task(project, slug)
        dispatch_id = str(task["dispatch_id"])
    info = _pr_view(project, pr)
    branch, base_sha, head_sha = _validate_pr_shape(pr, info)
    # Request-time checks reject invented/cross-task PR numbers.  Process repeats
    # every proof and is the only authority that can merge.
    # Pin a fully proven task/PR authority even while CI is still running.  The
    # daemon owns the durable retry and repeats every proof before merging.
    ready = _readiness(project, slug, pr, task, info, require_gate=False,
                       allow_external_pending=True)
    path = _record_path(project, slug, pr)
    with S.project_lock(project):
        current_task = _load_current_task(project, slug, dispatch_id)
        current_info = _pr_view(project, pr)
        _validate_pr_shape(pr, current_info, branch=branch, base_sha=base_sha, head_sha=head_sha)
        current_authority = _authority(project, slug, pr, current_task, head_sha, branch)
        current_reviewer = _clean_reviewer(project, slug, pr, dispatch_id, head_sha, base_sha,
                                           ready["candidate_tree"])
        if current_authority != ready["authority"] or current_reviewer != ready["reviewer"]:
            raise MergeCoordinatorError("merge authority changed while the request was being pinned")
        existing = S.read_json(path, None)
        same = _same_request(existing, project=project, slug=slug, pr=pr,
                             dispatch_id=dispatch_id, task_attempt=current_task.get("attempt"),
                             branch=branch, base_sha=base_sha, head_sha=head_sha,
                             candidate_tree=ready["candidate_tree"], authority=current_authority,
                             reviewer=current_reviewer)
        if same and existing.get("state") in {"requested", "processing", "waiting", "failed", "merged"}:
            return existing
        if isinstance(existing, dict) and existing.get("state") == "processing":
            # Never replace an exact live/indeterminate owner.  A later request
            # may supersede it only after process identity proves it dead.
            if _claim_liveness(existing.get("claim")) is not False:
                return existing
        if isinstance(existing, dict):
            superseded = {**existing, "superseded_at": S.now(),
                          "superseded_by_inputs": {"dispatch_id": dispatch_id,
                                                   "task_attempt": current_task.get("attempt"),
                                                   "branch": branch, "base_sha": base_sha,
                                                   "head_sha": head_sha,
                                                   "candidate_tree": ready["candidate_tree"],
                                                   "authority": current_authority,
                                                   "reviewer": current_reviewer}}
            history = _history_path(project, slug, pr)
            history.parent.mkdir(parents=True, exist_ok=True)
            with history.open("a") as stream:
                stream.write(json.dumps(superseded, sort_keys=True) + "\n")
        rec = {"version": 2, "project": project, "slug": slug, "pr": pr,
               "generation": secrets.token_hex(16), "dispatch_id": dispatch_id,
               "task_attempt": current_task.get("attempt"), "branch": branch,
               "base": BASE, "base_sha": base_sha, "head_sha": head_sha,
               "candidate_tree": ready["candidate_tree"],
               "authority": current_authority, "reviewer": current_reviewer,
               "gate_mode": None, "candidate_gate": None,
               "state": "requested", "requested_at": S.now(), "updated_at": S.now(),
               # `attempts` is an operational poll count and is deliberately
               # unbounded.  Only invariant/security refusals consume the
               # bounded terminal budget; ordinary external pending states do
               # not permanently strand an otherwise-current request.
               "attempts": 0, "terminal_attempts": 0,
               "retry_after": None, "last_error": None,
               "claim": None, "result": None}
        S.write_json(path, rec)
    S.append_event(project, slug, "merge-requested", pr=pr, head_sha=head_sha,
                   dispatch_id=dispatch_id, generation=rec["generation"])
    if superseded:
        S.append_event(project, slug, "merge-request-superseded", pr=pr,
                       old_generation=superseded.get("generation"), generation=rec["generation"],
                       old_base_sha=superseded.get("base_sha"), base_sha=base_sha)
    return rec


def _claim(project: str, slug: str, pr: int) -> tuple[dict | None, str | None]:
    path = _record_path(project, slug, pr)
    with S.project_lock(project):
        rec = S.read_json(path, None)
        if not isinstance(rec, dict):
            raise MergeCoordinatorError(f"no merge request for PR #{pr}")
        if rec.get("state") in {"merged", "failed"}:
            return rec, None
        claim = rec.get("claim")
        # A deadline is diagnostic, not authority to overlap a still-live merge.
        # Reclaim only after exact PID/start evidence proves the old owner gone;
        # unreadable or malformed identity remains fail-closed.
        if isinstance(claim, dict) and rec.get("state") == "processing":
            owner_live = _claim_liveness(claim)
            if owner_live is not False:
                return rec, None
        if int(rec.get("terminal_attempts") or 0) >= MAX_ATTEMPTS:
            rec.update({"state": "failed", "claim": None, "updated_at": S.now()})
            S.write_json(path, rec)
            return rec, None
        if rec.get("retry_after") and str(rec["retry_after"]) > S.now():
            return rec, None
        try:
            task = _load_claim_task(project, slug, str(rec.get("dispatch_id") or ""))
        except MergePending as exc:
            # An unresolved worker stop is an operational hold, not a merge
            # attempt and not a terminal invariant failure. Keep the durable
            # request retryable without acquiring merge authority.
            rec.update({"state": "waiting", "claim": None, "updated_at": S.now(),
                        "retry_after": None, "last_error": str(exc)})
            S.write_json(path, rec)
            return rec, None
        if task.get("attempt") != rec.get("task_attempt"):
            raise MergeCoordinatorError("task attempt changed after the merge request was pinned")
        token = secrets.token_hex(16)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        owner_start = _pid_start(os.getpid())
        if owner_start is None:
            raise MergeCoordinatorError("cannot establish exact daemon process identity for merge claim")
        rec["attempts"] = int(rec.get("attempts") or 0) + 1
        rec.update({"state": "processing", "updated_at": S.now(), "retry_after": None,
                    "claim": {"generation": token, "owner_pid": os.getpid(),
                              "owner_pid_start": owner_start, "started": now.isoformat(),
                              "deadline": (now + timedelta(seconds=CLAIM_TIMEOUT_SECONDS)).isoformat()}})
        S.write_json(path, rec)
        return rec, token


def _settle(project: str, slug: str, pr: int, token: str, *, state: str,
            error: str | None = None, result: dict | None = None,
            terminal_failure: bool = False) -> dict:
    path = _record_path(project, slug, pr)
    with S.project_lock(project):
        rec = S.read_json(path, None)
        claim = rec.get("claim") if isinstance(rec, dict) else None
        if not token or not isinstance(claim, dict) or claim.get("generation") != token:
            raise MergeCoordinatorError("merge claim generation changed before settlement")
        if terminal_failure:
            rec["terminal_attempts"] = int(rec.get("terminal_attempts") or 0) + 1
            if rec["terminal_attempts"] >= MAX_ATTEMPTS:
                state = "failed"
        rec.update({"state": state, "claim": None, "updated_at": S.now(), "last_error": error})
        if result is not None:
            rec["result"] = result
        if state == "waiting":
            delay = RETRY_SECONDS[min(int(rec.get("attempts") or 1) - 1, len(RETRY_SECONDS) - 1)]
            rec["retry_after"] = (datetime.now(timezone.utc)
                                  + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
        elif state == "failed":
            rec["retry_after"] = None
        S.write_json(path, rec)
    S.append_event(project, slug, f"merge-{state}", pr=pr, head_sha=rec.get("head_sha"),
                   attempts=rec.get("attempts"), error=error)
    return rec


def _merge_pr(project: str, pr: int, head_sha: str, branch: str, base_sha: str,
              candidate_tree: str, stale_base_guard: dict) -> dict:
    cwd = config.project_path(project)
    repo = str((stale_base_guard if isinstance(stale_base_guard, dict) else {}).get("repository") or "")
    if (not isinstance(stale_base_guard, dict)
            or stale_base_guard.get("kind") != "strict-required-status-checks"
            or stale_base_guard.get("strict") is not True or not _REPO.fullmatch(repo)
            or stale_base_guard.get("enforce_admins") is not True
            or not stale_base_guard.get("required_checks")):
        raise MergePending("automatic merge has no proven server stale-base guard")
    # Use the one-shot REST merge operation, not `gh pr merge`: the latter can
    # enable auto-merge/queue behavior instead of returning a synchronous
    # refusal. GitHub atomically applies strict up-to-date protection here;
    # `sha` independently pins the head.
    result = subprocess.run(["gh", "api", "--method", "PUT", f"repos/{repo}/pulls/{pr}/merge",
                             "-f", "merge_method=squash", "-f", f"sha={head_sha}"],
                            cwd=str(cwd), capture_output=True,
                            text=True, timeout=300, env=engines.clean_env())
    after = _pr_view(project, pr)
    if after.get("state") != "MERGED":
        detail = (result.stderr or result.stdout or "").strip()[-300:]
        try:
            _validate_pr_shape(pr, after, branch=branch, base_sha=base_sha, head_sha=head_sha)
        except MergeCoordinatorError as exc:
            raise MergePending(f"atomic merge was refused after the PR pair moved: {exc}") from exc
        raise MergePending(f"GitHub's atomic stale-base guard refused PR #{pr}"
                           + (f": {detail}" if detail else ""))
    try:
        merge_sha, proof = _validate_merged_shape(
            project, pr, after, branch=branch, base_sha=base_sha,
            head_sha=head_sha, candidate_tree=candidate_tree)
    except MergeCoordinatorError as exc:
        detail = (result.stderr or result.stdout or "").strip()[-300:]
        raise MergeCoordinatorError(
            f"gh pr merge #{pr} postcondition failed: {exc}"
            + (f"; command output: {detail}" if detail else "")) from exc
    return {"merged": True, "merge_sha": merge_sha, "head_sha": head_sha,
            "base_sha": base_sha, "post_merge_proof": proof,
            "stale_base_guard": stale_base_guard,
            "gh_returncode": result.returncode}


def _main_run(project: str, merge_sha: str | None) -> dict | None:
    if not merge_sha:
        return None
    try:
        rows = verify.gh(["run", "list", "--branch", BASE, "--limit", "100", "--json",
                          "databaseId,headSha,status,conclusion,workflowName"], config.project_path(project))
    except verify.VerifierFault:
        return None
    if not isinstance(rows, list):
        return None
    row = next((item for item in rows if isinstance(item, dict) and item.get("headSha") == merge_sha), None)
    return ({"id": row.get("databaseId"), "head_sha": merge_sha, "status": row.get("status"),
             "conclusion": row.get("conclusion"), "workflow": row.get("workflowName")}
            if row else None)


def process(project: str, slug: str, pr: int, *,
            candidate_gate: Callable[..., dict] | None = None) -> dict:
    """Durably wait for R-014 integration; never inspect a generic/local gate or merge."""
    pr = _pr_number(pr)
    rec, token = _claim(project, slug, pr)
    if token is None:
        return rec or {}
    try:
        # This must precede task/PR/check reads, local candidate execution,
        # merged-PR adoption and the privileged merge call.  ``candidate_gate``
        # remains in the signature only for compatibility with old durable
        # callers and is intentionally dead-disconnected.
        raise MergePending(TRUSTED_REMOTE_PENDING)
        task = _load_claim_task(project, slug, str(rec.get("dispatch_id") or ""))
        if task.get("attempt") != rec.get("task_attempt"):
            raise MergeCoordinatorError("task attempt changed after the merge request was pinned")
        info = _pr_view(project, pr)
        if info.get("state") == "MERGED":
            active = _active_runs(project, slug, str(rec.get("dispatch_id") or ""))
            if active:
                raise MergePending(f"task still has active L1 generation(s): {', '.join(active)}")
            candidate_tree = _candidate_tree(project, pr, rec["base_sha"], rec["head_sha"])
            if candidate_tree != rec.get("candidate_tree"):
                raise MergeCoordinatorError("merged PR candidate tree differs from the durable request")
            authority = _authority(project, slug, pr, task, rec["head_sha"], rec["branch"])
            reviewer = _clean_reviewer(project, slug, pr, rec["dispatch_id"], rec["head_sha"],
                                       rec["base_sha"], rec["candidate_tree"])
            if authority != rec.get("authority") or reviewer != rec.get("reviewer"):
                raise MergeCoordinatorError(
                    "current reviewer/authority differs from the durable merged request")
            merge_sha, proof = _validate_merged_shape(
                project, pr, info, branch=rec["branch"], base_sha=rec["base_sha"],
                head_sha=rec["head_sha"], candidate_tree=rec["candidate_tree"])
            check_state = _checks(info)
            gate = None
            if check_state == "pass":
                gate_mode = "github-actions"
            elif check_state == "none":
                if candidate_gate is None:
                    raise MergePending(
                        "merged no-CI PR requires exact trusted sandbox candidate evidence for closeout")
                gate = _candidate_evidence(
                    candidate_gate(project=project, slug=slug, pr=pr,
                                   base_sha=rec["base_sha"], head_sha=rec["head_sha"],
                                   authority=authority), rec["base_sha"], rec["head_sha"])
                gate_mode = "local-suite"
            elif check_state == "pending":
                raise MergePending("merged PR checks are still pending")
            else:
                raise MergeCoordinatorError(
                    f"merged PR checks are terminal non-success state {check_state}")
            result = {"merged": True, "adopted": True, "head_sha": rec.get("head_sha"),
                      "base_sha": rec.get("base_sha"), "merge_sha": merge_sha,
                      "post_merge_proof": proof, "gate_mode": gate_mode,
                      "candidate_gate": gate,
                      "stale_base_guard": rec.get("stale_base_guard")}
            result["main_run"] = _main_run(project, result.get("merge_sha"))
            with S.project_lock(project):
                latest = S.read_json(_record_path(project, slug, pr), {}) or {}
                claim = latest.get("claim") if isinstance(latest, dict) else None
                if not isinstance(claim, dict) or claim.get("generation") != token:
                    raise MergeCoordinatorError("merge request generation changed before adoption")
                latest_task = _load_claim_task(project, slug, rec["dispatch_id"])
                if latest_task.get("attempt") != rec.get("task_attempt"):
                    raise MergeCoordinatorError("task attempt changed before merged-PR adoption")
                if _active_runs(project, slug, rec["dispatch_id"]):
                    raise MergePending("an L1 generation became active before merged-PR adoption")
                final_authority = _authority(
                    project, slug, pr, latest_task, rec["head_sha"], rec["branch"])
                final_reviewer = _clean_reviewer(
                    project, slug, pr, rec["dispatch_id"], rec["head_sha"],
                    rec["base_sha"], rec["candidate_tree"])
                if final_authority != authority or final_reviewer != reviewer:
                    raise MergeCoordinatorError(
                        "task authority or reviewer changed before merged-PR adoption")
                latest["gate_mode"] = gate_mode
                latest["candidate_gate"] = gate
                latest["updated_at"] = S.now()
                S.write_json(_record_path(project, slug, pr), latest)
            return _settle(project, slug, pr, token, state="merged", result=result)
        if not _task_current(task, str(rec.get("dispatch_id") or "")):
            raise MergePending(
                "task is in stopped closeout; waiting for an exact operator merge before adoption")
        # Prove that GitHub has an atomic stale-base mechanism before doing
        # any expensive local candidate work. In particular, a no-CI PR can
        # never satisfy the currently supported strict-required-checks guard;
        # repeatedly rebuilding and running its sandbox candidate would only
        # burn resources while ending at the same durable pending state.
        preflight_stale_base_guard = _server_stale_base_guard(project, info)
        ready = _readiness(project, slug, pr, task, info, candidate_gate=candidate_gate)
        if (ready["branch"], ready["base_sha"], ready["head_sha"], ready["candidate_tree"]) != (
                rec.get("branch"), rec.get("base_sha"), rec.get("head_sha"), rec.get("candidate_tree")):
            raise MergeCoordinatorError("current PR pair differs from the durable request")
        if ready["authority"] != rec.get("authority") or ready["reviewer"] != rec.get("reviewer"):
            raise MergeCoordinatorError(
                "current reviewer/authority differs from the durable request; submit a fresh request")
        # The project lock is deliberately held across the final evidence read
        # and GitHub's match-head merge.  This prevents a local hold/task/review
        # generation from changing between authorization and the privileged call.
        with S.project_lock(project):
            latest = S.read_json(_record_path(project, slug, pr), None)
            claim = latest.get("claim") if isinstance(latest, dict) else None
            if not isinstance(claim, dict) or claim.get("generation") != token:
                raise MergeCoordinatorError("merge request generation changed before final authorization")
            latest_task = _load_current_task(project, slug, str(rec.get("dispatch_id") or ""))
            if latest_task.get("attempt") != rec.get("task_attempt"):
                raise MergeCoordinatorError("task attempt changed before merge")
            final_info = _pr_view(project, pr)
            # The PR read can block on the network.  Re-read the task after it
            # so a hold/terminal transition that waited on the project lock is
            # observed before the privileged call.
            latest_task = _load_current_task(project, slug, str(rec.get("dispatch_id") or ""))
            if latest_task.get("attempt") != rec.get("task_attempt"):
                raise MergeCoordinatorError("task attempt changed before merge")
            _validate_pr_shape(pr, final_info, branch=rec["branch"],
                               base_sha=rec["base_sha"], head_sha=rec["head_sha"])
            if _active_runs(project, slug, rec["dispatch_id"]):
                raise MergePending("an L1 generation became active before merge")
            final_authority = _authority(project, slug, pr, latest_task, rec["head_sha"], rec["branch"])
            if final_authority != ready["authority"]:
                raise MergeCoordinatorError("task-owned PR authority changed before merge")
            final_reviewer = _clean_reviewer(project, slug, pr, rec["dispatch_id"], rec["head_sha"],
                                             rec["base_sha"], rec["candidate_tree"])
            if final_reviewer != ready["reviewer"]:
                raise MergeCoordinatorError("clean reviewer generation changed before merge")
            final_checks = _checks(final_info)
            if final_checks in {"fail", "reject"}:
                raise MergeCoordinatorError(
                    f"PR checks changed to terminal non-success state {final_checks} before merge")
            if ready.get("candidate_gate"):
                if final_checks != "none":
                    raise MergePending(f"PR check state changed to {final_checks} after the sandbox gate")
                _candidate_evidence(ready["candidate_gate"], rec["base_sha"], rec["head_sha"])
            elif final_checks != "pass":
                raise MergePending(f"PR checks changed to {final_checks} before merge")
            stale_base_guard = _server_stale_base_guard(project, final_info)
            if stale_base_guard != preflight_stale_base_guard:
                raise MergePending("server stale-base guard changed during final authorization")
            # Persist the exact authorization mode before the privileged call.
            # If altd dies after GitHub merges but before settlement, adoption
            # can still prove whether this pair used CI or the trusted local
            # candidate suite; report closeout never has to infer it.
            gate_mode = "local-suite" if ready.get("candidate_gate") else "github-actions"
            latest["gate_mode"] = gate_mode
            latest["candidate_gate"] = ready.get("candidate_gate")
            latest["stale_base_guard"] = stale_base_guard
            latest["updated_at"] = S.now()
            S.write_json(_record_path(project, slug, pr), latest)
            result = _merge_pr(project, pr, rec["head_sha"], rec["branch"], rec["base_sha"],
                               rec["candidate_tree"], stale_base_guard)
            result["gate_mode"] = gate_mode
            result["candidate_gate"] = ready.get("candidate_gate")
            result["stale_base_guard"] = stale_base_guard
            # Persist the authoritative merge result before releasing the lock.
            latest.update({"state": "merged", "claim": None, "updated_at": S.now(),
                           "last_error": None, "retry_after": None, "result": result})
            S.write_json(_record_path(project, slug, pr), latest)
        result["main_run"] = _main_run(project, result.get("merge_sha"))
        with S.project_lock(project):
            latest = S.read_json(_record_path(project, slug, pr), {}) or {}
            if (latest.get("generation") == rec.get("generation") and latest.get("state") == "merged"
                    and (latest.get("result") or {}).get("head_sha") == rec.get("head_sha")):
                latest["result"] = result
                latest["updated_at"] = S.now()
                S.write_json(_record_path(project, slug, pr), latest)
        S.append_event(project, slug, "merge-merged", pr=pr, head_sha=rec.get("head_sha"),
                       merge_sha=result.get("merge_sha"), dispatch_id=rec.get("dispatch_id"))
        return latest
    except MergePending as exc:
        return _settle(project, slug, pr, token, state="waiting", error=str(exc))
    except MergeCoordinatorError as exc:
        return _settle(project, slug, pr, token, state="waiting", error=str(exc),
                       terminal_failure=True)
    except Exception as exc:
        # Unknown transport/runtime failures are retried.  They are not proof
        # that the pinned authority is invalid and therefore cannot consume a
        # terminal invariant budget.
        return _settle(project, slug, pr, token, state="waiting",
                       error=f"{type(exc).__name__}: {exc}")


def pending(project: str) -> list[tuple[str, int]]:
    """List durable requests still owned by the current closeout generation.

    Terminal tasks and superseded dispatches keep their forensic request files,
    but the server must not retry them on every tick and manufacture a workflow
    fault forever.
    """
    out: list[tuple[str, int]] = []
    for task in S.list_tasks(project):
        slug = str(task.get("slug") or "")
        if not slug:
            continue
        for path in S.task_dir(project, slug).glob("merge-request-*.json"):
            rec = S.read_json(path, None)
            if (isinstance(rec, dict) and rec.get("state") in {"requested", "processing", "waiting"}
                    and task.get("state") in {"running", "blocked", "reported"}
                    and rec.get("dispatch_id") == task.get("dispatch_id")
                    and type(rec.get("pr")) is int):
                out.append((slug, rec["pr"]))
    return sorted(set(out))
