"""The guarded land-a-PR sequence as one deterministic command.

Commit the owner's staged changes, leaving working and untracked edits intact,
push with one force-with-lease retry against the branch tip recorded before committing (never two), open or
reuse the PR, wait for checks, merge only on green — or, for projects without CI, on a
full local suite that passed on the base-plus-head merge candidate — and only when asked. No
model call anywhere — the commit message arrives as an argument. Idempotent: nothing to commit is a skip, an
up-to-date push is a no-op, an open PR is reused.

Explicit adoption pins an existing external PR's original history. Its task branch publishes only
fast-forward updates to the original branch; merging preserves history and requests no branch deletion.

Precondition: a working, authenticated `gh` before alt land commits anything. The branch's PR is looked up
first — a merged or closed-unmerged PR starts another delivery on a fresh PR — so
a missing or logged-out `gh` ends the run with the worktree untouched, nothing staged and nothing committed."""
from __future__ import annotations
import contextlib
import fcntl
import functools
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, dispatch, github_intake, state as S, tasks as T

CHECK_POLL_SECONDS = 15
#: #480: GitHub's PR view lags the landing's own push briefly; the head is re-read within this bound.
PR_VIEW_SETTLE_SECONDS = 30
PR_VIEW_POLL_SECONDS = 2
LOCAL_TEST_TIMEOUT = 1800
DEFAULT_TEST_CMD = "make test"
#: Bounds both admission to the repository turn and, by default, the CI and owner-assessment wait inside it,
#: so a merging candidate keeps its turn through its fresh required check, including those of heads that
#: integrate a moved base; `--wait` only shortens the latter.
LAND_WAIT_TIMEOUT = 3600


class LandError(RuntimeError):
    """A refusal or a dead end the caller must see; bin/alt prints it on stderr and exits non-zero."""


class BaseMoved(LandError):
    """A fresh read proves that only the base moved: the PR, its head and both branch names still match the pin."""


def _run(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Every git/gh invocation funnels through here so tests can drive the whole pipeline offline."""
    try:
        env = config.subprocess_env()
        if args[0] == "gh":
            # #252: neither GH_REPO nor gh's preferred upstream may select a different adoption target.
            env.pop("GH_REPO", None)
            env.pop("GH_HOST", None)
            origin = _need(_git(cwd, "config", "--get", "remote.origin.url"), "origin URL")
            match = github_intake._REMOTE.fullmatch(origin)
            if match and args[1] != "api":
                repository = f"{match['owner']}/{match['repo']}"
                args = [*args, *([repository] if args[1:3] == ["repo", "view"] else ["--repo", repository])]
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


def _require_current_publisher(project: str, slug: str, task: dict, authority: dict | None = None) -> None:
    """Fence automated landing to the exact current L2 attempt.

    A hand-run command has no actor (or explicitly names the operator). Every automated
    caller must be the L2 that owns the task now: L1s and control-plane actors do
    not publish. The attempt number names the L2 that owns the task now: a
    replaced worker keeps it, a relaunch from the queue does not.
    """
    actor = authority.get("actor") if authority is not None else os.environ.get("ALTITUDE_ACTOR")
    if actor is None or actor == config.OPERATOR_ACTOR:
        return
    if actor != "l2":
        raise LandError(f"actor {actor!r} cannot land {project}/{slug}; only the current L2 or the operator may land")
    if task.get("state") != "running":
        raise LandError(f"current L2 cannot land {project}/{slug}: task is not running")
    attempt = authority.get("attempt") if authority is not None else os.environ.get("ALTITUDE_ATTEMPT")
    if not attempt or str(task.get("attempt")) != str(attempt):
        raise LandError(f"current L2 cannot land {project}/{slug}: attempt {attempt} is no longer current")


def _require_task_checkout(root: Path, project: str, slug: str, task: dict, branch: str) -> None:
    common = _need(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"), "task Git directory")
    repo = config.project_path(project).resolve()
    repo_common = _need(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"), "project Git directory")
    if (not task.get("worktree") or root.resolve() != Path(task["worktree"]).resolve()
            or root.resolve() == repo or common != repo_common
            or branch != f"worktree-{slug}" or task.get("branch") != branch):
        raise LandError("landing requires this task's registered isolated worktree and branch in its repository")


def _require_unowned_pr(project: str, slug: str, branch: str, number: int | None = None) -> None:
    """Inspect existing ownership records under the caller's project lock."""
    for other in S.list_tasks(project):
        adopted = other.get("adopted_pr") or {}
        if other["slug"] != slug and other["state"] in S.OPEN_STATES and (
                adopted.get("branch") == branch or other.get("branch") == branch
                or number is not None and (adopted.get("number") == number or number in other.get("prs", []))):
            raise LandError(f"PR or branch already belongs to task {other['slug']}")


def _require_delivery_owner(project: str, slug: str, task: dict, current: dict, authority: dict | None,
                            *, branch: str, number: int | None = None) -> None:
    """Refresh the publication boundary under the caller's project lock."""
    _require_current_publisher(project, slug, current, authority)
    _require_unowned_pr(project, slug, branch, number)
    if current.get("adopted_pr") != task.get("adopted_pr"):
        raise LandError("task adoption changed during landing")


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


def _push(root: Path, branch: str, recorded_tip: str | None,
          *, source_branch: str | None = None) -> list[str]:
    """Push normally; only ordinary task branches may retry against the recorded remote-tip lease."""
    refspec = f"refs/heads/{source_branch}:refs/heads/{branch}" if source_branch else branch
    p = _git(root, "push", "-u", "origin", refspec, timeout=300)
    if p.returncode == 0:
        return []
    err = (p.stderr or "") + (p.stdout or "")
    if source_branch:
        raise LandError(f"adopted PR push refused; only fast-forward updates are allowed: {err.strip()[-300:]}")
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
              "number,url,state,baseRefName,baseRefOid,headRefName,headRefOid,isCrossRepository,isDraft,reviewDecision,closingIssuesReferences,mergeCommit"], root)
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


def _require_closing_issues(root: Path, number: int, issues: list[int]) -> None:
    """#269: a mention is not GitHub's closing relationship; scope is the owner's explicit assertion."""
    if not issues:
        return
    pr = _pr_view(root, str(number)) or {}
    repository = str(pr.get("url") or "").rsplit("/pull/", 1)[0]
    linked = {str(issue.get("url") or "").lower() for issue in pr.get("closingIssuesReferences") or []}
    missing = [n for n in issues if f"{repository}/issues/{n}".lower() not in linked]
    if missing:
        if pr.get("state") == "MERGED":
            raise LandError(f"PR #{number} already merged without closing links for {missing}; send full-scope "
                            "evidence via `alt task reply` and report follow_ups to L3 for `alt issue close N --reason completed`")
        raise LandError(f"PR #{number} lacks GitHub closing links for {missing}; put `Closes #N` for each "
                        "fully resolved issue in --pr-body-file and target the repository's default branch. "
                        "Re-run if GitHub is still updating the links")
    # #269: manual closing references can also exist on a nondefault target, which will not close issues.
    info = _need(_run(["gh", "repo", "view", "--json", "defaultBranchRef"], root), "GitHub default branch")
    try:
        default_branch = json.loads(info)["defaultBranchRef"]["name"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LandError("cannot determine GitHub's default branch for issue closure") from exc
    if not default_branch or pr.get("baseRefName") != default_branch:
        raise LandError(f"PR #{number} must target GitHub's default branch {default_branch!r} to close issues")


def _adoption(root: Path, task: dict, base: str,
              number: int | None, expected_head: str | None, reason: str | None) -> tuple[dict | None, dict | None]:
    """Explicitly select one PR/head at a time in this project's isolated task checkout."""
    receipt = task.get("adopted_pr")
    if number is None and not receipt:
        return None, None
    origin = _need(_git(root, "config", "--get", "remote.origin.url"), "origin URL")
    match = github_intake._REMOTE.fullmatch(origin)
    if not match:
        raise LandError("adoption requires a GitHub origin")
    previous_merge = None
    delivered = task.get("delivery") or {}
    if not receipt and delivered.get("number") and delivered["number"] != number:
        previous = _pr_view(root, str(delivered["number"])) or {}
        previous_merge = (previous.get("mergeCommit") or {}).get("oid")
        if previous.get("state") != "MERGED" or not previous_merge:
            raise LandError("finish the current task PR before adopting another PR")
        _need(_git(root, "merge-base", "--is-ancestor", previous_merge, f"origin/{base}"),
              "previous delivery is not on current main")
    if receipt and number is not None and number != receipt["number"]:
        # #266: finish each assigned PR before selecting the next.
        if any(old["number"] == number for old in task.get("adoption_history", [])):
            raise LandError("cannot reactivate an earlier adoption receipt")
        previous = _pr_view(root, str(receipt["number"])) or {}
        previous_merge = (previous.get("mergeCommit") or {}).get("oid")
        if (receipt["origin"] != origin or receipt["base"] != base or previous.get("state") != "MERGED"
                or previous.get("number") != receipt["number"] or previous.get("url") != receipt["url"]
                or previous.get("headRefName") != receipt["branch"] or previous.get("baseRefName") != base
                or not previous_merge or not previous.get("headRefOid")):
            raise LandError("previous adoption must be verified merged before selecting another PR")
        _need(_git(root, "merge-base", "--is-ancestor", previous_merge, f"origin/{base}"),
              "previous adoption merge is not on current main")
        receipt = None
    if receipt and (receipt["base"] != base or receipt["origin"] != origin
                    or number is not None and (number != receipt["number"] or expected_head != receipt["head"])):
        raise LandError("adopted PR identity is immutable; cannot change its PR, base, origin, or original head")
    if receipt:
        _need(_git(root, "merge-base", "--is-ancestor", receipt["head"], "HEAD"),
              "adopted PR head is not an ancestor of HEAD; preserve its history")
    pr = _pr_view(root, str(receipt["number"] if receipt else number))
    url = f"https://github.com/{match['owner']}/{match['repo']}/pull/{receipt['number'] if receipt else number}"
    allowed_states = ("OPEN", "MERGED") if receipt else ("OPEN",)
    if (not pr or pr.get("state") not in allowed_states or pr.get("isCrossRepository") is not False
            or pr.get("baseRefName") != base or base != "main"
            or pr.get("number") != (receipt["number"] if receipt else number)
            or str(pr.get("url", "")).lower() != url.lower()):
        raise LandError("adoption requires an open, same-repository PR targeting main")
    remote_branch = pr.get("headRefName")
    if not remote_branch or remote_branch in ("main", "master") or remote_branch.startswith("worktree-"):
        raise LandError("cannot adopt a protected branch or an Altitude task branch")
    if receipt and (remote_branch != receipt["branch"] or pr.get("url") != receipt["url"]):
        raise LandError("adopted PR branch or URL changed")
    if pr.get("state") == "MERGED":
        return receipt, pr  # the existing merged-PR path refuses dirty work and publishes nothing
    remote_head = _fetch_remote_tip(root, remote_branch)
    if not remote_head or remote_head != pr.get("headRefOid") or not receipt and remote_head != expected_head:
        raise LandError("PR head changed or differs from --expected-head; inspect it before adopting")
    _need(_git(root, "merge-base", "--is-ancestor", remote_head, "HEAD"),
          "PR remote head is not an ancestor of HEAD; incorporate it without rewriting history")
    if not receipt:
        receipt = {"number": number, "url": pr["url"], "branch": remote_branch, "base": base,
                   "head": remote_head, "origin": origin, "reason": reason.strip()}
        if previous_merge:
            receipt["previous_merge"] = previous_merge
    return receipt, pr


def _record_adoption(project: str, slug: str, receipt: dict, authority: dict | None, *,
                     previous: dict | None, dry_run: bool) -> tuple[dict, str | None]:
    with S.project_lock(project):
        current = S.load_task(project, slug)
        _require_current_publisher(project, slug, current, authority)
        if current.get("adopted_pr") != previous:
            raise LandError("task adoption changed during landing")
        _require_unowned_pr(project, slug, receipt["branch"], receipt["number"])
        if dry_run or previous == receipt:
            return receipt, current.get("hold_merge")
        receipt = {**receipt, "actor": (authority or {}).get("actor") or os.environ.get("ALTITUDE_ACTOR", "operator"),
                   "attempt": current.get("attempt"), "at": S.now()}
        if previous:
            current.setdefault("adoption_history", []).append(previous)
        restore_hold = _restore_delivery_hold(current, previous["number"] if previous else
                                              (current.get("delivery") or {}).get("number"))
        current["adopted_pr"] = receipt
        current["prs"] = [n for n in current.get("prs", []) if n != receipt["number"]] + [receipt["number"]]
        S.save_task(project, current)
        if restore_hold:
            S.append_event(project, slug, "hold-merge", why=restore_hold, actor=receipt["actor"],
                           hold_id=current.get("hold_merge_id"))
        S.append_event(project, slug, "pr-adopted", **receipt)
        return receipt, current.get("hold_merge")


def _restore_delivery_hold(task: dict, previous: int | None) -> str | None:
    approval = task.get("merge_approval") or {}
    hold = (previous and not task.get("hold_merge") and approval.get("pr") == previous
            and approval.get("hold_id") == task.get("hold_merge_id") and approval.get("hold"))
    if hold:  # Each PR needs its own scope judgment, within the original review requirement.
        task["hold_merge"] = hold
    return hold or None


def _record_delivery(project: str, slug: str, task: dict, authority: dict | None, *,
                     branch: str, base: str, number: int | None = None, head: str | None = None,
                     previous: dict | None = None, reconciled: dict | None = None) -> dict:
    """Current publication plus immutable events; a pending publication cannot complete the task.
    `reconciled` names the recorded head and merge evidence a merged PR's newer head replaces."""
    receipt = dict(number=number, head=head, base=base, branch=branch)
    with S.project_lock(project):
        current = S.load_task(project, slug)
        _require_delivery_owner(project, slug, task, current, authority, branch=branch, number=number)
        old = current.get("delivery") or {}
        if all(old.get(k) == v for k, v in receipt.items()):
            return current
        prior_number = (current.get("prs") or [None])[-1]
        hold = _restore_delivery_hold(current, prior_number if number and number != prior_number else None)
        if number and current.get("adopted_pr") and current["adopted_pr"]["number"] != number:
            current.setdefault("adoption_history", []).append(current.pop("adopted_pr"))
        current["prs"] = list(dict.fromkeys([*current.get("prs", []),
                                            *([previous["number"]] if previous else []),
                                            *([number] if number else [])]))
        current["delivery"] = {**receipt, "at": datetime.now(timezone.utc).isoformat()}
        for key in ("verified", "l3_handled", "completion_requested"):
            current.pop(key, None)
        S.save_task(project, current)
        if hold:
            S.append_event(project, slug, "hold-merge", why=hold, actor="l2", hold_id=current.get("hold_merge_id"))
        S.append_event(project, slug, "delivery", **current["delivery"], previous=previous,
                       **({"reconciled": reconciled} if reconciled else {}))
        return current


def _continuation_base(root: Path, branch: str,
                       base: str, pr: dict, dirty: bool, remote_tip: str | None) -> str | None:
    """Separate the already merged head from follow-up, including a retry after reconciliation."""
    head = _need(_git(root, "rev-parse", "HEAD"), "local head")
    previous_head = pr.get("headRefOid")
    if not dirty and head == previous_head:
        return None
    merged = (pr.get("mergeCommit") or {}).get("oid")
    if (not previous_head or not merged or pr.get("baseRefName") != base
            or pr.get("headRefName") != branch or pr.get("isCrossRepository") is not False):
        raise LandError("cannot continue: previous PR identity, final head or merge is unavailable")
    _need(_git(root, "merge-base", "--is-ancestor", merged, f"origin/{base}"),
          "cannot continue: previous PR merge is not on current main")
    if _git(root, "merge-base", "--is-ancestor", merged, "HEAD").returncode == 0:
        cutoff = _need(_git(root, "merge-base", f"origin/{base}", "HEAD"), "continuation base")
    elif _git(root, "merge-base", "--is-ancestor", previous_head, "HEAD").returncode == 0:
        cutoff = previous_head
    else:
        raise LandError("cannot identify follow-up commits: retain the previous PR head or reconcile onto main")
    if not dirty and _git(root, "diff", "--quiet", cutoff, "HEAD").returncode == 0:
        return None
    if _need(_git(root, "rev-list", "--merges", f"{cutoff}..HEAD"), "follow-up merges"):
        raise LandError("follow-up contains merge commits; reconcile that work onto current main in this "
                        "task worktree before landing, preserving any merge-resolution edits")
    if remote_tip and remote_tip != previous_head:
        _need(_git(root, "merge-base", "--is-ancestor", remote_tip, "HEAD"),
              "continuation remote branch has unseen work; incorporate it before landing")
    return cutoff


def _merged_retry(root: Path, project: str, slug: str, task: dict, authority: dict | None,
                  branch: str, base: str, pr: dict, lease: list[str], issues: list[int]) -> dict:
    _require_closing_issues(root, pr["number"], issues)
    delivered = task.get("delivery") or {}
    reconciled = None
    if delivered.get("number") and ((delivered["number"], delivered.get("branch"), delivered.get("head"))
                                    != (pr["number"], pr.get("headRefName"), pr["headRefOid"])):
        reconciled = _merged_head_evidence(root, delivered, pr, base)
    if not delivered.get("number") or reconciled:
        task = _record_delivery(project, slug, task, authority, branch=pr["headRefName"],
                                base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"),
                                number=pr["number"], head=pr["headRefOid"], reconciled=reconciled)
    if reconciled:
        _note(f"PR #{pr['number']} merged at {pr['headRefOid'][:12]}, after its recorded head "
              f"{reconciled['head'][:12]} — current delivery reconciled")
    _note(f"PR #{pr['number']} already merged — nothing to push")
    return {"pr": pr["number"], "url": pr.get("url"), "checks": "merged",
            "merged": True, "branch": branch, "commit": None, "head": None,
            "lease": lease, "staged": [], "hold": task.get("hold_merge"), "replaced": [], "local_tests": None}


def _merged_head_evidence(root: Path, delivered: dict, pr: dict, base: str) -> dict:
    """#771: the recorded PR merged on GitHub after later pushes to its own branch. Adopt that newer head only
    when it extends the recorded one and its merge is on current main, so an unrelated merge never completes the task."""
    number, merge = pr["number"], (pr.get("mergeCommit") or {}).get("oid")
    if delivered["number"] != number or delivered.get("branch") != pr.get("headRefName"):
        raise LandError(f"PR #{number} from {pr.get('headRefName')!r} merged, but the current delivery records "
                        f"PR #{delivered['number']} from {delivered.get('branch')!r}; it is not reconciled")
    if not merge or not pr.get("headRefOid"):
        raise LandError(f"cannot reconcile PR #{number}: its merged head or merge commit is unavailable")
    _need(_git(root, "merge-base", "--is-ancestor", delivered["head"], pr["headRefOid"]),
          f"cannot reconcile PR #{number}: recorded head {delivered['head'][:12]} is not an ancestor of "
          f"its merged head {pr['headRefOid'][:12]}")
    _need(_git(root, "merge-base", "--is-ancestor", merge, f"origin/{base}"),
          f"cannot reconcile PR #{number}: its merge {merge[:12]} is not on current {base}")
    return {"head": delivered["head"], "merge": merge, "url": pr.get("url")}


def _ensure_pr(root: Path, branch: str, base: str, message: str, pr_title: str | None,
               pr_body_file: str | None, task_ref: str, pr: dict | None) -> dict:
    """Reuse the branch's PR when one exists (editing it only when asked); otherwise create it. `pr` is the
    caller's already-fetched view of the branch's PR (`None` = looked, there is none), so the happy path costs
    one `gh pr view` and the create path reads the PR back exactly once."""
    title = pr_title or message.splitlines()[0]
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
    # By its printed URL: the branch may also name an earlier closed or merged PR.
    pr = _pr_view(root, ((c.stdout or "").strip().splitlines() or [branch])[-1])
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
    # Skips need candidate requiredness; this inventory alone is no merge verdict.
    return "skipped" if "skipping" in buckets else "pass"


def _check_query(root: Path, query: str, **variables) -> dict:
    origin = _need(_git(root, "config", "--get", "remote.origin.url"), "origin URL")
    match = github_intake._REMOTE.fullmatch(origin)
    if not match:
        raise LandError("check evidence requires a GitHub origin")
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in {"owner": match["owner"], "repo": match["repo"], **variables}.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    try:
        result = json.loads(_need(_run(args, root), "GitHub check evidence"))
        if result.get("errors"):
            raise ValueError("GraphQL errors")
        return result["data"]["repository"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LandError("GitHub check evidence is incomplete or unreadable") from exc


def _complete_check_nodes(connection: dict) -> list[dict]:
    # #266: a truncated response must not hide a required, failing, or skipped check.
    try:
        nodes = connection["nodes"]
        if (not isinstance(nodes, list) or connection["pageInfo"]["hasNextPage"] is not False
                or connection["totalCount"] != len(nodes)):
            raise ValueError("incomplete connection")
        return nodes
    except (ValueError, KeyError, TypeError) as exc:
        raise LandError("GitHub check evidence is truncated or incomplete") from exc


def _checks_evidence(root: Path, pair: dict) -> str:
    """#266: prove the exact candidate and successful required checks."""
    query = """query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){
      pullRequest(number:$number){number state baseRefName headRefName headRefOid
        baseRef{target{oid} branchProtectionRule{requiredStatusChecks{context app{databaseId}}}
          rules(first:100){totalCount pageInfo{hasNextPage} nodes{type parameters{
            ... on RequiredStatusChecksParameters{requiredStatusChecks{context integrationId}}}}}}
        commits(last:1){nodes{commit{...CandidateChecks}}} potentialMergeCommit{...CandidateChecks}}}}
      fragment CandidateChecks on Commit{oid tree{oid} parents(first:3){totalCount nodes{oid}}
        statusCheckRollup{contexts(first:100){totalCount pageInfo{hasNextPage} nodes{__typename
          ... on StatusContext{context state isRequired(pullRequestNumber:$number) commit{oid}}
          ... on CheckRun{name status conclusion isRequired(pullRequestNumber:$number)
            checkSuite{commit{oid} app{databaseId} branch{name}
              matchingPullRequests(first:100){totalCount pageInfo{hasNextPage} nodes{number baseRefName headRefName}}
              workflowRun{event file{path}}}}}}}}"""
    try:
        repository = _check_query(root, query, number=pair["number"])
        pr = repository["pullRequest"]
        base = pr["baseRef"]
        identity = (pr["number"], pr["state"], pr["baseRefName"], pr["headRefName"],
                    base["target"]["oid"], pr["headRefOid"])
        if identity != (pair["number"], "OPEN", pair["base"], pair["branch"], pair["base_sha"], pair["head_sha"]):
            _assert_pair_current(root, pair)  # BaseMoved when only the base moved
            raise LandError("PR base or head moved while reading exact check evidence")
        required = {(item["context"], (item.get("app") or {}).get("databaseId"))
                    for item in (base["branchProtectionRule"] or {}).get("requiredStatusChecks") or []}
        for rule in _complete_check_nodes(base["rules"]):
            if rule["type"] == "REQUIRED_STATUS_CHECKS":
                required.update((item["context"], item.get("integrationId"))
                                for item in rule["parameters"]["requiredStatusChecks"])
            elif rule["type"] in {"WORKFLOWS", "REQUIRED_WORKFLOW_STATUS_CHECKS"}:
                raise LandError("required workflow evidence cannot be established from status checks")
        head = pr["commits"]["nodes"][0]["commit"]
        if head["oid"] != pair["head_sha"]:
            raise LandError("checks are associated with a different PR head")
        merge = pr["potentialMergeCommit"]
        if merge and (merge["parents"]["totalCount"] != 2 or
                      [p["oid"] for p in merge["parents"]["nodes"]] != [pair["base_sha"], pair["head_sha"]]):
            raise LandError("test merge checks do not belong to the pinned base/head candidate")
        candidate = head
        if merge and merge.get("statusCheckRollup") and _complete_check_nodes(merge["statusCheckRollup"]["contexts"]):
            candidate = merge
        rollup = candidate.get("statusCheckRollup")
        contexts = _complete_check_nodes(rollup["contexts"]) if rollup else []
        if candidate is head and contexts and _git(root, "merge-base", "--is-ancestor",
                                                  pair["base_sha"], pair["head_sha"]).returncode != 0:
            raise LandError("head checks do not include the pinned base; merge current main and rerun checks")
        named_gate = pair.get("required_pr_check", False)
        if named_gate:
            # #380: the workflow proves its tested merge tree equals this head tree.
            _need(_git(root, "merge-base", "--is-ancestor", pair["base_sha"], pair["head_sha"]),
                  "incorporate current main in the PR branch and wait for fresh CI")
            if not head["tree"]["oid"] or candidate["tree"]["oid"] != head["tree"]["oid"]:
                raise LandError("PR check candidate tree differs from the current head")
        gate_passed = gate_seen = False
        states, passed, seen, observed = set(), set(), set(), []
        for check in contexts:
            is_run = check["__typename"] == "CheckRun"
            if not is_run and check["__typename"] != "StatusContext":
                raise LandError("unknown check evidence type")
            suite = check["checkSuite"] if is_run else check
            if suite["commit"]["oid"] != candidate["oid"] or not isinstance(check["isRequired"], bool):
                raise LandError("check result is unrelated to the pinned candidate or lacks requiredness")
            run = suite.get("workflowRun")
            # GitHub-managed workflows (CodeQL default setup) run on the commit with event `dynamic` and no
            # PR: the commit binding above identifies them, they never qualify as the gate, and their
            # results count like any other check's.
            if run and run["event"] != "dynamic":
                related = _complete_check_nodes(suite["matchingPullRequests"])
                if (run["event"] not in {"push", "pull_request", "pull_request_target"}
                        or not any((p["number"], p["baseRefName"], p["headRefName"]) ==
                                   (pair["number"], pair["base"], pair["branch"]) for p in related)
                        or (run["event"] == "push" and (suite.get("branch") or {}).get("name") != pair["branch"])):
                    raise LandError("workflow run does not belong to this PR candidate")
            status = check["conclusion"] if is_run and check["status"] == "COMPLETED" else (
                "PENDING" if is_run else check["state"])
            is_gate = (named_gate and is_run and check["name"] == config.PR_CHECK_NAME
                       and (suite.get("app") or {}).get("databaseId") == 15368
                       and run and run["event"] == "pull_request"
                       and (run.get("file") or {}).get("path") == config.PR_CHECK_WORKFLOW)
            name, app = (check["name"] if is_run else check["context"]), (suite.get("app") or {}).get("databaseId")
            seen.add((name, app))
            gate_seen |= is_gate
            observed.append(f"{name} {(status or 'without conclusion').lower()}" + ("" if is_gate or check["isRequired"] else " (not required)"))
            if is_gate and status == "SUCCESS":
                gate_passed = True
            if is_run and status == "SKIPPED" and check["isRequired"] is False and not is_gate:
                continue
            states.add(status)
            if status == "SUCCESS":
                passed.add((name, app, check["isRequired"]))
        if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            return "fail"
        if states & {"PENDING", "EXPECTED"}:
            return "pending"
        registered = [(name, app) for name, app in sorted(required, key=str)
                      if any(name == seen_name and app in (None, seen_app) for seen_name, seen_app in seen)]
        if (gate_seen and not gate_passed) or any(
                not any(name == check_name and needed and app in (None, check_app)
                        for check_name, check_app, needed in passed) for name, app in registered):
            return "skipped"
        # GitHub registers a workflow's jobs one by one: a required check absent from the candidate
        # has not reported yet, which is a wait, not a skip of the checks that did register.
        unregistered = [name for name, app in sorted(required, key=str) if (name, app) not in registered]
        if named_gate and not gate_seen:
            unregistered.append(config.PR_CHECK_NAME)
        if unregistered:
            pair["unregistered"] = (f"required check {', '.join(dict.fromkeys(unregistered))} has not registered on "
                                    f"head {pair['head_sha']}; registered: {', '.join(observed) or 'none'}")
            return "missing"
        if states - {"SUCCESS"} or not states:
            return "skipped" if contexts or required else "none"
        if named_gate:
            pair["tree"] = head["tree"]["oid"]
        return "pass"
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise LandError("GitHub check evidence is incomplete or unreadable") from exc


def _fetch_rev(root: Path, ref: str) -> str:
    """Fetch and return the authoritative current tip of `origin/<ref>`."""
    fetched = _git(root, "fetch", "origin", ref, timeout=300)
    if fetched.returncode != 0:
        raise LandError(f"cannot refresh origin/{ref}: "
                        f"{((fetched.stderr or '') + (fetched.stdout or '')).strip()[-200:] or f'exit {fetched.returncode}'} — "
                        "refusing to judge checks or a merge against a ref that cannot be read")
    return _need(_git(root, "rev-parse", "FETCH_HEAD"), f"cannot resolve origin/{ref} after fetching it")


def _require_pr_target(pr: dict, branch: str, base: str) -> None:
    if pr.get("baseRefName") != base or pr.get("headRefName") != branch:
        raise LandError(f"PR #{pr.get('number')} targets {pr.get('baseRefName')!r} from {pr.get('headRefName')!r}, not "
                        f"the expected {base!r} from {branch!r}")


def _snapshot_pair(root: Path, branch: str, number: int, base: str, expected_head: str) -> dict:
    """Pin the exact GitHub PR base/head pair that check classification and local testing will judge.

    origin/<branch> is the authoritative head: anything but the revision this landing pushed is a
    foreign move and refuses. GitHub's PR view lags a push for a moment (#480), so a view that still
    names another head is re-read for a short bound until it names the pushed revision.
    """
    deadline, settling = time.monotonic() + PR_VIEW_SETTLE_SECONDS, False
    while True:
        pr = _pr_view(root, str(number)) or {}
        if pr.get("state") != "OPEN":
            raise LandError(f"PR #{number} is not open while its merge candidate is being pinned")
        _require_pr_target(pr, branch, base)
        # #266: baseRefOid is PR metadata and may lag the actual branch after main is incorporated.
        base_sha, fetched_head = _fetch_rev(root, base), _fetch_rev(root, branch)
        if fetched_head != expected_head:
            raise LandError(f"PR #{number} head moved from the pushed revision {expected_head} to {fetched_head} "
                            "before checks")
        head_sha = pr.get("headRefOid")
        if head_sha == expected_head:
            break
        if time.monotonic() >= deadline:
            raise LandError(f"PR #{number} still reports head {head_sha} instead of the pushed revision "
                            f"{expected_head} after {PR_VIEW_SETTLE_SECONDS} seconds; re-run alt land")
        if not settling:
            _note(f"PR #{number} view still reports {head_sha}; re-reading until it names the pushed head")
            settling = True
        time.sleep(PR_VIEW_POLL_SECONDS)
    return {"base": base, "branch": branch, "base_sha": base_sha, "head_sha": head_sha, "number": number,
            "required_pr_check": _required_pr_check(root, base_sha)}


def _assert_pair_current(root: Path, pair: dict) -> None:
    """Fail unless both origin refs and GitHub's PR still name the pinned pair; BaseMoved when only the base moved."""
    base_sha = _fetch_rev(root, pair["base"])
    head_sha = _fetch_rev(root, pair["branch"])
    pr = _pr_view(root, str(pair["number"])) or {}
    actual = (head_sha, pr.get("headRefOid"), pr.get("state"), pr.get("baseRefName"), pr.get("headRefName"))
    if actual != (pair["head_sha"], pair["head_sha"], "OPEN", pair["base"], pair["branch"]):
        raise LandError("the PR base or head moved after the merge candidate was pinned — "
                        "re-run alt land to classify, test and merge one current pair")
    if base_sha != pair["base_sha"]:
        raise BaseMoved(f"origin/{pair['base']} moved from {pair['base_sha']} to {base_sha} after the merge "
                        "candidate was pinned")


def _workflows(root: Path, pair: dict) -> dict[str, bool]:
    """Whether each exact side of the merge pair (a commit or tree id) contains GitHub Actions workflows."""
    sides = {}
    for label, sha in (("base", pair["base_sha"]), ("head", pair["head_sha"])):
        tree = _git(root, "ls-tree", "--name-only", sha, ".github/workflows")
        if tree.returncode != 0:
            raise LandError(f"cannot inspect .github/workflows on the {label} {sha[:12]}: "
                            f"{(tree.stderr or '').strip()[-200:] or f'exit {tree.returncode}'}")
        sides[label] = bool((tree.stdout or "").strip())
    return sides


def _has_ci(root: Path, pair: dict) -> bool:
    """Workflows on either side keep the hosted gate: a head that deletes them cannot select the local suite."""
    return any(_workflows(root, pair).values())


def _test_argv(test_cmd: str) -> list[str]:
    """`--test-cmd` is one command, split into argv without a shell; operators such as `&&` are literal arguments."""
    try:
        argv = shlex.split(test_cmd)
    except ValueError as exc:
        raise LandError(f"--test-cmd is not one shell-quoted command: {exc}") from exc
    if not argv:
        raise LandError("the local test command is empty")
    return argv


def _prospective(root: Path, base: str, changed: list[str], test_cmd: str) -> dict:
    """What a real landing of this worktree would pin and judge, as far as a dry run can tell without
    committing, pushing or reading the PR. Staged changes leave the head commit undetermined; its tree
    is the staged index. Continuation after a merged PR, base integration under --merge and check
    evidence already published on the PR are settled only by a real landing."""
    base_sha = _need(_git(root, "rev-parse", f"origin/{base}"), f"origin/{base}")
    head = _need(_git(root, "rev-parse", "HEAD"), "HEAD")
    tree = _need(_git(root, "write-tree"), "the staged tree")
    workflows = _workflows(root, {"base_sha": base_sha, "head_sha": tree})
    required = _required_pr_check(root, base_sha)
    gate = "github-actions" if required or any(workflows.values()) else "local-suite"
    undetermined = []
    if changed:
        undetermined.append(f"the head commit is created at landing from the {len(changed)} staged path(s); "
                            "its tree is the staged index")
    if _git(root, "merge-base", "--is-ancestor", base_sha, head).returncode != 0:
        undetermined.append(f"HEAD does not include current origin/{base}: --merge integrates it into a new head "
                            "first, and the required PR check needs a head that includes it")
    return {"base": base_sha, "head": None if changed else head, "tree": tree, "gate": gate,
            "required_pr_check": required, "workflows": workflows,
            "local_suite": _test_argv(test_cmd) if gate == "local-suite" else None,
            "undetermined": undetermined}


def _checks_value(root: Path, number: int, pair: dict) -> str:
    """Read checks only while GitHub and origin still name the pinned PR pair."""
    _assert_pair_current(root, pair)
    state = _checks_state(root, number)
    evidence = _checks_evidence(root, pair)
    if state != "none" and evidence == "none":
        raise LandError("reported checks have no evidence on the pinned candidate")
    state = evidence
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
    argv = _test_argv(test_cmd)
    _note(f"the merge candidate's local suite is the gate: {test_cmd}")
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
    """Yield the single-parent squash candidate GitHub's squash merge produces."""
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
                         "-c", "commit.gpgsign=false", "commit", "-m", "alt land synthetic merge candidate",
                         timeout=300)
        if committed.returncode != 0:
            raise LandError("cannot commit the synthetic merge candidate: "
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


def _merge(root: Path, branch: str, number: int, base: str, expected_head: str,
           *, delete_branch: bool = True) -> bool:
    """Merge, then believe GitHub about the result, not the exit code — `--delete-branch` can fail on the
    local half (a worktree holds the branch) after the merge itself succeeded. GitHub atomically refuses if the
    PR head changed after the candidate this invocation validated. An adopted PR keeps its original branch. The
    receipt names no main run: the merged commit's push-triggered run rarely exists yet, and `alt task status`
    resolves it by commit once it does."""
    method = ["--squash", "--delete-branch"] if delete_branch else ["--squash"]
    m = _run(["gh", "pr", "merge", str(number), *method,
              "--match-head-commit", expected_head], root, timeout=300)
    after = _pr_view(root, str(number)) or {}
    if after.get("state") != "MERGED":
        raise LandError(f"gh pr merge #{number}: "
                        f"{((m.stderr or '') + (m.stdout or '')).strip()[-300:] or 'PR is not merged'}")
    if m.returncode != 0:
        _note(f"merged, but gh pr merge exited {m.returncode}: {(m.stderr or '').strip()[-160:]}")
    f = _git(root, "fetch", "origin", base, timeout=120)
    if f.returncode != 0:
        _note(f"git fetch origin {base} failed: {(f.stderr or '').strip()[-160:]}")
    return True


def _merge_pair(root: Path, pair: dict, *, delete_branch: bool) -> bool:
    """Merge the pinned pair; a refusal caused only by a moved base (a strict up-to-date rule) is BaseMoved."""
    try:
        return _merge(root, pair["branch"], pair["number"], pair["base"], pair["head_sha"],
                      delete_branch=delete_branch)
    except LandError:
        _assert_pair_current(root, pair)
        raise


def _merge_on_local_suite(root: Path, pair: dict, test_cmd: str, *, before_merge=None,
                          delete_branch: bool = True) -> tuple[bool, dict]:
    """Test one exact base/head pair and merge only while both tips still match it."""
    base_sha, head_sha = pair["base_sha"], pair["head_sha"]
    identity = {"base": base_sha, "head": head_sha}

    try:
        _assert_pair_current(root, pair)
        with _candidate(root, base_sha, head_sha) as path:
            tests = _local_suite(path, test_cmd)
    except BaseMoved:
        raise
    except LandError as exc:
        _note(f"not merging: {exc}")
        return False, {"command": test_cmd, "passed": False, "returncode": None, "tests": None,
                       "skipped": None, "expected_failures": None, "error": str(exc), **identity}
    tests.update(identity)
    if not tests["passed"]:
        _note(f"not merging: the local suite ({test_cmd}) is not green on the merge candidate")
        return False, tests
    try:
        _assert_pair_current(root, pair)
    except BaseMoved:
        raise
    except LandError:
        tests["error"] = "the base or the head moved while the merge candidate was under test"
        _note(f"not merging: {tests['error']} — re-run alt land to test and merge the current pair")
        return False, tests
    after_checks = _checks_state(root, pair["number"])
    if after_checks == "none":
        after_checks = _checks_evidence(root, pair)
    try:
        _assert_pair_current(root, pair)
    except BaseMoved:
        raise
    except LandError:
        tests["error"] = "the base or the head moved while final checks were being read"
        _note(f"not merging: {tests['error']}")
        return False, tests
    if after_checks != "none":
        tests["error"] = f"PR checks changed from none to {after_checks} while the local suite ran"
        _note(f"not merging: {tests['error']} — re-run alt land under the current gate")
        return False, tests
    with before_merge() if before_merge else contextlib.nullcontext():
        merged = _merge_pair(root, pair, delete_branch=delete_branch)
    return merged, tests


def _required_pr_check(root: Path, base_sha: str) -> bool:
    """A base commit that ships the check workflow requires its PR `check`; a head cannot opt out by deleting it."""
    return _git(root, "cat-file", "-e", f"{base_sha}:{config.PR_CHECK_WORKFLOW}").returncode == 0


def _checks_outcome(checks: str, pair: dict) -> str:
    return f"checks are {checks!r}" + (f"; {pair['unregistered']}" if checks == "missing" else "")


class _SessionEnded(BaseException):
    """SIGTERM reached a waiting landing: the job holding its owner's session is stopping."""


def _ends_with_session(wait_for):
    """A waiting landing ends with the owner session that started it. A worker's end stops its whole job, which
    sends this process SIGTERM. The candidate stays published and unmerged, the repository turn is released with
    the process, and the owner's next turn is told which head is published."""
    @functools.wraps(wait_for)
    def run(root, project, slug, pair, *, merge, **kwargs):
        def ended(signum, frame):
            raise _SessionEnded

        attempt = S.load_task(project, slug).get("attempt")
        previous = signal.signal(signal.SIGTERM, ended)
        restore = signal.SIG_DFL if previous is None else previous
        try:
            return wait_for(root, project, slug, pair, merge=merge, **kwargs)
        except _SessionEnded:
            signal.signal(signal.SIGTERM, restore)
            T.notify(project, slug, f"Your `alt land` ended with your previous session while it waited on PR "
                     f"#{pair['number']}. Head {pair['head_sha']} remains published and unmerged, and the repository "
                     f"turn is free. Assess that head where a review asks for it, then re-run "
                     f"`alt land{' --merge' if merge else ''}` when ready.", by="landing", attempt=attempt)
            raise LandError(f"the owner's session ended while this landing waited; PR #{pair['number']} head "
                            f"{pair['head_sha']} remains published and unmerged — assess when ready and re-run "
                            "alt land") from None
        finally:
            signal.signal(signal.SIGTERM, restore)
    return run


@_ends_with_session
def _wait_for_candidate(root, project, slug, pair, *, merge, wait, authority, deadline):
    """Keep the repository turn while the owner assesses an integrated head and CI runs."""
    from . import reviews
    notified, announced = None, None
    actor = authority.get("actor") if authority is not None else os.environ.get("ALTITUDE_ACTOR")
    while True:
        stale = refused = None
        if merge:
            # Only local review reads share admission's lock; network reads and sleeps
            # leave review requests available, including during nonmerging publication.
            with reviews.merge_lock(project, slug):
                _require_current_publisher(project, slug, S.load_task(project, slug), authority)
                try:
                    reviews.require_merge(project, slug, pair)
                except reviews.AssessmentRequired as exc:
                    if actor != "l2" or wait <= 0:
                        raise LandError(str(exc)) from exc
                    stale = exc
                except T.TransitionError as exc:
                    refused = exc
        if refused:
            _assert_pair_current(root, pair)  # BaseMoved when the review candidate differs only by a moved base
            raise LandError(str(refused)) from refused
        checks = _checks_value(root, pair["number"], pair)
        notice = str(stale) if stale else None
        if stale and notice != notified:
            _note(f"waiting for owner assessment on head {pair['head_sha']} "
                  f"and base {pair['base_sha']}; keeping the repository turn with "
                  f"{max(0, round(deadline - time.monotonic()))}s remaining in --wait. "
                  "Keep this command running in a background/tool session and collect its result. "
                  f"Cancel landing if code needs edits.\n{stale}")
        notified = notice
        if checks in ("pending", "missing") and checks != announced:
            _note(f"{pair['unregistered'] if checks == 'missing' else 'PR checks pending on head ' + pair['head_sha']}; "
                  f"polling for up to {max(0, round(deadline - time.monotonic()))}s")
            announced = checks
        if checks not in ("pending", "missing", "pass", "none-configured"):
            return checks
        if stale is None and checks not in ("pending", "missing"):
            return checks
        if time.monotonic() >= deadline:
            if stale:
                raise LandError(f"owner assessment wait timed out with {_checks_outcome(checks, pair)}; candidate "
                                "remains published and unmerged — assess when ready and re-run alt land")
            return checks
        time.sleep(min(CHECK_POLL_SECONDS, max(deadline - time.monotonic(), 0)))


def _repository_turn(function):
    """#433: siblings must not advance the base while a merging landing validates its candidate, and a local
    suite runs one at a time on this machine (I-20260923-062538). Publication without --merge never waits.
    Other installations and external writers do not share this process-owned turn."""
    @functools.wraps(function)
    def run(message, **kwargs):
        root = Path(kwargs.get("cwd") or Path.cwd())
        if kwargs.get("dry_run") or not kwargs.get("merge"):
            return {**function(message, **kwargs), "waited": 0}
        common = _need(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"), "Git directory")
        with open(Path(common) / "altitude-land.lock", "a") as lock:
            started = time.monotonic()
            waiting = False
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not waiting:
                        _note(f"waiting for another landing in this repository (up to {LAND_WAIT_TIMEOUT} seconds)")
                        waiting = True
                    if time.monotonic() >= started + LAND_WAIT_TIMEOUT:
                        raise LandError("landing wait timed out; no candidate selected — re-run alt land when ready")
                    time.sleep(1)
            waited = round(time.monotonic() - started) if waiting else 0
            if waiting:
                _note(f"landing turn acquired after {waited} seconds; refreshing ownership, base and candidate checks")
            return {**function(message, **kwargs), "waited": waited}
    return run


@_repository_turn
def land(message: str, *, project: str | None = None, pr_title: str | None = None, pr_body_file: str | None = None,
         merge: bool = False, wait: int | None = None, base: str = "main",
         dry_run: bool = False, test_cmd: str = DEFAULT_TEST_CMD, cwd: Path | None = None,
         authority: dict | None = None, adopt_pr: int | None = None,
         expected_head: str | None = None, reason: str | None = None,
         closes_issues: list[int] | None = None, approval: str | None = None) -> dict:
    """Run the whole sequence from the current worktree; returns the JSON-ready result object.
    `approval` names the operator's task-chat message approving a held PR; it releases the hold just before merge."""
    if not message.strip():
        raise LandError("--message is empty")
    if approval and not merge:
        raise LandError("--approval applies only with --merge")
    closes_issues = list(dict.fromkeys(closes_issues or []))
    if any(type(n) is not int or n <= 0 for n in closes_issues):
        raise LandError("--closes-issue requires a positive issue number in this repository")
    if adopt_pr is not None:
        if adopt_pr <= 0 or not re.fullmatch(r"[0-9a-f]{40}", expected_head or "") or not (reason or "").strip():
            raise LandError("--adopt-pr requires a positive PR number, full --expected-head SHA, and --reason")
    elif expected_head is not None or reason is not None:
        raise LandError("--expected-head and --reason require --adopt-pr")
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
    if task is None or not project or not slug:
        raise LandError(
            f"cannot verify task ownership for branch {branch!r}: no task record resolved; "
            "pass `--project` or run from the dispatch environment"
        )
    hold_merge = task.get("hold_merge")
    if hold_merge:  # an explicit merge hold is the exception to merge-by-default
        if merge and not approval:
            raise LandError(f"task {project}/{slug} carries a merge hold: {hold_merge}; after the operator approves "
                            "the PR in the task chat, re-run with `--merge --approval <message-id>`; until then "
                            "re-run `alt land` without `--merge`")
        _note(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
              + ("the operator approval is applied before merge" if merge
                 else "a dry run opens nothing" if dry_run else "the PR will be opened but not merged"))
    # This is deliberately before fetch, committing, or any GitHub call. Put the
    # fence in the library rather than only in bin/alt so direct callers cannot
    # bypass current-publisher ownership.
    _require_current_publisher(project, slug, task, authority=authority)
    _require_task_checkout(root, project, slug, task, branch)
    with S.project_lock(project):
        _require_unowned_pr(project, slug, branch)
    fetched = _git(root, "fetch", "-q", "origin", base)
    if fetched.returncode != 0:
        raise LandError(f"git fetch origin {base}: {(fetched.stderr or fetched.stdout).strip()[-300:]}")
    # Record the branch tip before committing: a later force may replace only this exact
    # remote history, and a push from another worker after this point must make the lease fail.
    adoption, pr = _adoption(root, task, base, adopt_pr, expected_head, reason)
    publish_branch = adoption["branch"] if adoption else branch
    recorded_tip = None if adoption else _fetch_remote_tip(root, branch)
    task_ref = f"{project}/{slug}"
    lease = dispatch.task_paths(project, task)
    staged_diff = _git(root, "diff", "--cached", "--name-only", "--no-renames", "-z")
    _need(staged_diff, "staged changes")
    changed = [path for path in (staged_diff.stdout or "").split("\0") if path]
    if adoption:
        adoption, hold_merge = _record_adoption(project, slug, adoption, authority,
                                                previous=task.get("adopted_pr"), dry_run=dry_run)
        task = {**task, "adopted_pr": adoption}
    commit, staged = None, []
    if not changed:
        _note("nothing staged — no new commit")
    if dry_run:
        prospective = _prospective(root, base, changed, test_cmd)
        _note(f"dry run: nothing committed, pushed, opened, tested or merged; the candidate would be judged by "
              f"{prospective['gate']} on base {prospective['base'][:12]}"
              + (f", running one command without a shell: {prospective['local_suite']}"
                 if prospective["local_suite"] else ""))
        for reason in prospective["undetermined"]:
            _note(f"dry run: {reason}")
        return {"pr": None, "url": None, "checks": "dry-run", "merged": False, "branch": branch,
                "commit": None, "head": None, "lease": lease, "staged": changed, "hold": hold_merge,
                "replaced": [], "local_tests": None, "dry_run": True, "adopted_pr": adoption,
                "prospective": prospective}
    if not adoption:
        pr = _pr_view(root, branch)
        if pr:
            _require_pr_target(pr, branch, base)
        with S.project_lock(project):
            _require_unowned_pr(project, slug, branch, (pr or {}).get("number"))
    cutoff, continued_pr = None, None
    if pr is not None and pr.get("state") == "MERGED":
        cutoff = _continuation_base(root, publish_branch, base, pr, bool(changed), recorded_tip)
        if cutoff is None:
            return _merged_retry(root, project, slug, task, authority, branch, base, pr, lease, closes_issues)
        continued_pr = pr
        previous = {"number": pr["number"], "head": pr["headRefOid"],
                    "merge": pr["mergeCommit"]["oid"], "url": pr["url"]}
        task = _record_delivery(project, slug, task, authority, branch=branch,
                                base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"), previous=previous)
        hold_merge = task.get("hold_merge")
        # An adopted delivery retains its receipt in history; follow-up belongs to the task branch.
        if adoption:
            recorded_tip = _fetch_remote_tip(root, branch)
        adoption, pr, publish_branch = None, None, branch
        _note(f"continuing after PR #{previous['number']} in the same task")
    closed = None
    if pr is not None and pr.get("state") == "CLOSED":  # it stays closed; the next delivery is its own PR
        closed = {"number": pr["number"], "head": pr.get("headRefOid"), "url": pr.get("url"), "state": "CLOSED"}
        pr = None
        _note(f"PR #{closed['number']} closed without merging — opening a fresh PR from {branch!r}")
    if changed or not pr or (task.get("delivery") or {}).get("head") != _need(_git(root, "rev-parse", "HEAD"), "head"):
        task = _record_delivery(project, slug, task, authority, branch=publish_branch,
                                base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"), previous=closed)
    if changed:
        _need(_git(root, "commit", "-m", message), "git commit")
        commit = _need(_git(root, "rev-parse", "HEAD"), "git rev-parse HEAD")
        staged = changed
        _note(f"committed {commit[:7]} ({len(staged)} path(s))")
    if cutoff:
        try:
            _need(_git(root, "rebase", "--onto", f"origin/{base}", cutoff, timeout=300), "continuation rebase")
        except LandError as exc:
            if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
                try:
                    _need(_git(root, "rebase", "--abort"), "abort continuation")
                except LandError as abort_error:
                    raise LandError(f"{exc}; abort also failed: {abort_error}; local commits remain in reflog") from exc
            raise LandError(f"continuation could not reconcile with main; local work is committed and retained. "
                            f"Resolve with git rebase --onto origin/{base} {cutoff}, then re-run alt land: "
                            f"{exc}") from exc
        if commit:
            commit = _need(_git(root, "rev-parse", "HEAD"), "rebased commit")
        if _git(root, "diff", "--quiet", f"origin/{base}", "HEAD").returncode == 0:
            return _merged_retry(root, project, slug, task, authority, branch, base, continued_pr, lease, closes_issues)
    replaced, deadline = [], None
    while True:  # A base that moves while this landing holds its turn is integrated, published and checked again.
        try:
            with S.project_lock(project):
                _require_delivery_owner(project, slug, task, S.load_task(project, slug), authority,
                                        branch=publish_branch, number=(pr or {}).get("number"))
            publishing_base = _need(_git(root, "rev-parse", f"origin/{base}"), "publishing base")
            if merge and _git(root, "merge-base", "--is-ancestor", publishing_base, "HEAD").returncode != 0:
                _note(f"integrating current origin/{base} before publishing the candidate")
                try:
                    _need(_git(root, "merge", "--no-edit", publishing_base, timeout=300), "integrate current base")
                except LandError:
                    if (git_dir / "MERGE_HEAD").exists():
                        _need(_git(root, "merge", "--abort"), "abort base integration; resolve the worktree before retrying")
                    raise
            replaced += _push(root, publish_branch, recorded_tip,
                              source_branch=branch if adoption else None)
            pushed_head = _need(_git(root, "rev-parse", f"origin/{publish_branch}"), "cannot capture the pushed PR head")
            _note(f"pushed head {pushed_head}")
            pr = _ensure_pr(root, publish_branch, base, message, pr_title, pr_body_file, task_ref, pr=pr)
            number = pr.get("number")
            task = _record_delivery(project, slug, task, authority, branch=publish_branch, number=number,
                                    head=pushed_head, base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"))
            hold_merge = task.get("hold_merge")
            pair = _snapshot_pair(root, publish_branch, number, base, pushed_head)
            if deadline is None:  # one wait covers every head this landing publishes
                wait = LAND_WAIT_TIMEOUT if wait is None else min(max(wait, 0), LAND_WAIT_TIMEOUT)
                deadline = time.monotonic() + wait
            if merge and pair["base_sha"] != publishing_base:
                # An outside merge between integration and pinning must also get a fresh integrated head.
                _assert_pair_current(root, {**pair, "base_sha": publishing_base})
            checks = _wait_for_candidate(root, project, slug, pair, merge=merge, wait=wait,
                                         authority=authority, deadline=deadline)
            _require_closing_issues(root, number, closes_issues)
            merged, local_tests = pr.get("state") == "MERGED", None
            def check_before_merge():
                current_pr = _pr_view(root, str(number)) or {}
                if (current_pr.get("isDraft") is True
                        or current_pr.get("reviewDecision") not in (None, "", "APPROVED")):
                    raise LandError("PR is not review-ready or has outstanding required reviews/changes")
                if adoption:
                    if (current_pr.get("number") != adoption["number"] or current_pr.get("url") != adoption["url"]
                            or current_pr.get("isCrossRepository") is not False):
                        raise LandError("adopted PR identity changed before merge")
                    if (current_pr.get("isDraft") is not False
                            or current_pr.get("reviewDecision") not in ("", "APPROVED")):
                        raise LandError("adopted PR is not review-ready or has outstanding required reviews/changes")
                    _assert_pair_current(root, pair)
                with S.project_lock(project):
                    current = S.load_task(project, slug)
                    _require_delivery_owner(project, slug, task, current, authority,
                                            branch=publish_branch, number=number)
                _require_closing_issues(root, number, closes_issues)
                if current.get("hold_merge") and not approval:
                    raise LandError(f"task carries a merge hold: {current['hold_merge']}")
                return current, current_pr
            @contextlib.contextmanager
            def before_merge():
                from . import reviews
                while True:
                    with reviews.merge_lock(project, slug):
                        current, current_pr = check_before_merge()
                        _assert_pair_current(root, pair)
                        try:
                            reviews.require_merge(project, slug, pair)
                        except reviews.AssessmentRequired:
                            pass  # Release the assessment lock before waiting on the owner.
                        except T.TransitionError as exc:
                            raise LandError(str(exc)) from exc
                        else:
                            if current.get("hold_merge"):
                                try:
                                    T.apply_merge_approval(project, slug, approval, current_pr, head=pair["head_sha"], actor="l2",
                                                           reason="owner applied the operator's task-chat approval")
                                except T.TransitionError as exc:
                                    raise LandError(str(exc)) from exc
                            yield
                            with S.project_lock(project):
                                current = S.load_task(project, slug)
                                current["review_merged_head"] = pair["head_sha"]
                                for review in reviews._current_reviews(current):
                                    review["merged_head"] = pair["head_sha"]
                                S.save_task(project, current)
                            return
                    final_checks = _wait_for_candidate(root, project, slug, pair, merge=True, wait=wait,
                                                       authority=authority, deadline=deadline)
                    if final_checks != checks:
                        raise LandError(f"PR checks changed from {checks} to {final_checks} during owner assessment")

            if merge and not merged:
                if checks == "none-configured":
                    merged, local_tests = _merge_on_local_suite(
                        root, pair, test_cmd, before_merge=before_merge, delete_branch=not adoption)
                elif checks == "pass":
                    checks = _checks_value(root, number, pair)
                    if checks == "pass":
                        with before_merge():
                            merged = _merge_pair(root, pair, delete_branch=not adoption)
                        if pair["required_pr_check"]:
                            commit_sha = ((_pr_view(root, str(number)) or {}).get("mergeCommit") or {}).get("oid")
                            if not commit_sha or _need(_git(root, "rev-parse", f"{commit_sha}^{{tree}}"),
                                                       "merged tree") != pair["tree"]:
                                raise LandError("merged tree does not match the tested PR tree; report delivery for recovery")
                else:
                    _note(f"not merging: {_checks_outcome(checks, pair)}")
        except BaseMoved as exc:
            if not merge or deadline is None or time.monotonic() >= deadline:
                raise LandError(f"{exc}; the candidate remains published and unmerged — re-run alt land to "
                                "integrate it, test and merge one current pair") from exc
            _note(f"{exc}; integrating it and checking the new head within the same wait")
            recorded_tip = pushed_head
            continue
        break

    return {"pr": number, "url": pr.get("url"), "checks": checks, "merged": merged,
            "branch": branch, "commit": commit, "head": pushed_head, "lease": lease, "staged": staged,
            "hold": hold_merge, "replaced": replaced, "local_tests": local_tests, "adopted_pr": adoption}
