"""The guarded land-a-PR sequence as one deterministic command.

Commit the owner's staged changes with the Altitude trailer, leaving working and untracked edits intact,
push with one force-with-lease retry against the branch tip recorded before committing (never two), open or
reuse the PR, wait for checks, merge only on green — or, under the project's local-check policy, on a
full local suite that passed on the base-plus-head merge candidate — and only when asked. No
model call anywhere — the commit message arrives as an argument. Idempotent: nothing to commit is a skip, an
up-to-date push is a no-op, an open PR is reused.

Explicit adoption pins an existing external PR's original history. Its task branch publishes only
fast-forward updates to the original branch; merging preserves history and requests no branch deletion.

Precondition: a working, authenticated `gh` before alt land commits anything. The branch's PR is looked up
first — a merged PR can start another delivery; a closed PR is refused — so
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
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config, dispatch, git_policy, github_intake, state as S

CHECK_POLL_SECONDS = 15
LOCAL_TEST_TIMEOUT = 1800
DEFAULT_TEST_CMD = "make test"


class LandError(RuntimeError):
    """A refusal or a dead end the caller must see; bin/alt prints it on stderr and exits non-zero."""


def _run(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Every git/gh invocation funnels through here so tests can drive the whole pipeline offline."""
    try:
        env = dict(os.environ)
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

    A hand-run command has no actor (or explicitly names Burak). Every automated
    caller must be the L2 that owns the task now: L1s and control-plane actors do
    not publish. The attempt number names the L2 that owns the task now: a
    replaced worker keeps it, a relaunch from the queue does not.
    """
    actor = authority.get("actor") if authority is not None else os.environ.get("ALTITUDE_ACTOR")
    if actor is None or actor == "burak":
        return
    if actor != "l2":
        raise LandError(f"actor {actor!r} cannot land {project}/{slug}; only the current L2 or Burak may land")
    if task.get("state") != "running":
        raise LandError(f"current L2 cannot land {project}/{slug}: task is not running")
    attempt = authority.get("attempt") if authority is not None else os.environ.get("ALTITUDE_ATTEMPT")
    if not attempt or str(task.get("attempt")) != str(attempt):
        raise LandError(f"current L2 cannot land {project}/{slug}: attempt {attempt} is no longer current")


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


def _push(root: Path, branch: str, base: str, task_ref: str, recorded_tip: str | None,
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


def _adoption(root: Path, project: str, slug: str, task: dict, branch: str, base: str,
              number: int | None, expected_head: str | None, reason: str | None) -> tuple[dict | None, dict | None]:
    """Explicitly select one PR/head at a time in this project's isolated task checkout."""
    receipt = task.get("adopted_pr")
    if number is None and not receipt:
        return None, None
    expected_path = Path(task.get("worktree") or config.project_path(project))
    common = _need(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"), "task Git directory")
    repo_common = _need(_git(config.project_path(project), "rev-parse", "--path-format=absolute",
                            "--git-common-dir"), "project Git directory")
    if (root.resolve() != expected_path.resolve() or root.resolve() == config.project_path(project).resolve()
            or common != repo_common or branch != f"worktree-{slug}"):
        raise LandError("PR adoption requires this task's isolated worktree and branch in its registered repository")
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
        # #266: continuation requires completed, preserved history, never a wider provenance exception.
        if any(old["number"] == number for old in task.get("adoption_history", [])):
            raise LandError("cannot reactivate an earlier adoption receipt")
        previous = _pr_view(root, str(receipt["number"])) or {}
        previous_merge = (previous.get("mergeCommit") or {}).get("oid")
        if (receipt["origin"] != origin or receipt["base"] != base or previous.get("state") != "MERGED"
                or previous.get("number") != receipt["number"] or previous.get("url") != receipt["url"]
                or previous.get("headRefName") != receipt["branch"] or previous.get("baseRefName") != base
                or not previous_merge or not previous.get("headRefOid")):
            raise LandError("previous adoption must be verified merged before selecting another PR")
        for ancestor, descendant in ((receipt["head"], previous_merge),
                                     (previous["headRefOid"], previous_merge), (previous_merge, f"origin/{base}")):
            _need(_git(root, "merge-base", "--is-ancestor", ancestor, descendant),
                  "previous adoption is not preserved in the current base")
        receipt = None
    if receipt and (receipt["base"] != base or receipt["origin"] != origin
                    or number is not None and (number != receipt["number"] or expected_head != receipt["head"])):
        raise LandError("adopted PR identity is immutable; cannot change its PR, base, origin, or original head")
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
        for other in S.list_tasks(project):
            adopted = other.get("adopted_pr") or {}
            if other["slug"] != slug and other["state"] in S.OPEN_STATES and (
                    adopted.get("number") == receipt["number"] or adopted.get("branch") == receipt["branch"]
                    or other.get("branch") == receipt["branch"] or receipt["number"] in other.get("prs", [])):
                raise LandError(f"PR or branch already belongs to task {other['slug']}")
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
                           hold_id=current["hold_merge_id"])
        S.append_event(project, slug, "pr-adopted", **receipt)
        return receipt, current.get("hold_merge")


def _restore_delivery_hold(task: dict, previous: int | None) -> str | None:
    approval = task.get("merge_approval") or {}
    hold = (previous and not task.get("hold_merge") and approval.get("pr") == previous
            and approval.get("hold_id") == task.get("hold_merge_id") and approval.get("hold"))
    if hold:  # #308 continuation: a PR-specific release cannot approve a subsequent delivery.
        task.update(hold_merge=hold, hold_merge_id=uuid.uuid4().hex)
    return hold or None


def _record_delivery(project: str, slug: str, task: dict, authority: dict | None, *,
                     branch: str, base: str, number: int | None = None, head: str | None = None,
                     previous: dict | None = None) -> dict:
    """Current publication plus immutable events; a pending publication cannot complete the task."""
    receipt = dict(number=number, head=head, base=base, branch=branch)
    with S.project_lock(project):
        current = S.load_task(project, slug)
        _require_current_publisher(project, slug, current, authority)
        if current.get("adopted_pr") != task.get("adopted_pr"):
            raise LandError("task adoption changed during landing")
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
            S.append_event(project, slug, "hold-merge", why=hold, actor="l2", hold_id=current["hold_merge_id"])
        S.append_event(project, slug, "delivery", **current["delivery"], previous=previous)
        return current


def _continuation_base(root: Path, project: str, slug: str, task: dict, branch: str,
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
    common = _need(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"), "task Git directory")
    repo_common = _need(_git(config.project_path(project), "rev-parse", "--path-format=absolute",
                            "--git-common-dir"), "project Git directory")
    if (root.resolve() != Path(task.get("worktree") or "").resolve()
            or root.resolve() == config.project_path(project).resolve() or common != repo_common
            or task.get("branch") != f"worktree-{slug}"
            or _need(_git(root, "branch", "--show-current"), "task branch") != task.get("branch")):
        raise LandError("continuation requires this task's registered isolated worktree and branch")
    if remote_tip and remote_tip != previous_head:
        _need(_git(root, "merge-base", "--is-ancestor", remote_tip, "HEAD"),
              "continuation remote branch has unseen work; incorporate it before landing")
    return cutoff


def _merged_retry(root: Path, project: str, slug: str, task: dict, authority: dict | None,
                  branch: str, base: str, pr: dict, lease: list[str], issues: list[int]) -> dict:
    _require_closing_issues(root, pr["number"], issues)
    if not (task.get("delivery") or {}).get("number"):
        task = _record_delivery(project, slug, task, authority, branch=pr["headRefName"],
                                base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"),
                                number=pr["number"], head=pr["headRefOid"])
    _note(f"PR #{pr['number']} already merged — nothing to push")
    return {"pr": pr["number"], "url": pr.get("url"), "checks": "merged",
            "merged": True, "main_run": None, "branch": branch, "commit": None, "head": None,
            "lease": lease, "staged": [], "hold": task.get("hold_merge"), "replaced": [], "local_tests": None}


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


def _inapplicable_job(source: str, name: str, event: str) -> bool:
    """Recognize complete job conditions false for the PR event; unsupported YAML stays unknown."""
    lines = source.splitlines()
    if ("\t" in source or [line for line in lines if re.match(r"^jobs\s*:", line)] != ["jobs:"]
            or any(line and not line[0].isspace() and not line.startswith("#")
                   and not re.match(r"[A-Za-z_][\w-]*:", line) for line in lines)):
        return False
    for line in lines:
        # A quoted scalar spanning lines can contain an apparent jobs block (#266 review).
        plain = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|#.*", "", line)
        if "'" in plain or '"' in plain:
            return False
    active, job, field, seen, condition = False, None, None, set(), None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line == "jobs:":
            active = True
            continue
        if not active:
            continue
        if not line.startswith(" "):
            active = False
            continue
        if not line.startswith("    "):
            found = re.fullmatch(r"  ([A-Za-z_][\w-]*):(?:\s*#.*)?", line)
            if not found or ("job", found[1]) in seen:
                return False
            job, field = found[1], None
            seen.add(("job", job))
        elif not line.startswith("     "):
            found = re.fullmatch(r"    ([A-Za-z_][\w-]*):\s*(.*)", line)
            if not found or not job or (job, found[1]) in seen or found[1] == "name":
                return False
            field = found[1]
            seen.add((job, field))
            if job == name and field in {"strategy", "uses"}:
                return False
            if job == name and field == "if":
                condition = found[2].strip()
        elif field is None or job == name and field == "if":
            return False  # A continued plain scalar could change the condition's meaning.
    if condition is None:
        return False
    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()
    return (re.fullmatch(r"github\.event_name\s*==\s*'push'\s*&&\s*"
                         r"github\.ref\s*==\s*'refs/heads/main'", condition) is not None
            or event == "pull_request"
            and re.fullmatch(r"github\.event_name\s*!=\s*'pull_request'", condition) is not None)


def _inapplicable_check(root: Path, check: dict, pair: dict, merge_sha: str | None, repository: str) -> bool:
    suite = check.get("checkSuite") or {}
    run = suite.get("workflowRun") or {}
    file = run.get("file") or {}
    if (check.get("isRequired") is not False or (suite.get("app") or {}).get("slug") != "github-actions"
            or run.get("event") not in {"pull_request", "pull_request_target"}
            or file.get("repositoryName") != repository):
        return False
    match = re.fullmatch(r"https://github\.com/" + re.escape(repository)
                         + r"/blob/([0-9a-f]{40})/(\.github/workflows/[^/]+\.ya?ml)",
                         file.get("repositoryFileUrl") or "")
    allowed = {pair["base_sha"]} if run["event"] == "pull_request_target" else {merge_sha}
    if run["event"] == "pull_request" and _git(
            root, "merge-base", "--is-ancestor", pair["base_sha"], pair["head_sha"]).returncode == 0:
        allowed.add(pair["head_sha"])
    if not match or match[1] not in allowed or match[2] != file.get("path"):
        return False
    workflow = _check_query(root, """query($owner:String!,$repo:String!,$expression:String!){
      repository(owner:$owner,name:$repo){object(expression:$expression){... on Blob{isTruncated text}}}}""",
                            expression=f"{match[1]}:{match[2]}")
    blob = workflow.get("object") or {}
    return blob.get("isTruncated") is False and _inapplicable_job(
        blob.get("text") or "", check.get("name") or "", run["event"])


def _checks_evidence(root: Path, pair: dict) -> str:
    """#266: prove the exact candidate, mandatory contexts and any optional job exclusion."""
    query = """query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){nameWithOwner
      pullRequest(number:$number){number state baseRefName headRefName headRefOid
        baseRef{target{oid} branchProtectionRule{requiredStatusChecks{context app{databaseId}}}
          rules(first:100){totalCount pageInfo{hasNextPage} nodes{type parameters{
            ... on RequiredStatusChecksParameters{requiredStatusChecks{context integrationId}}}}}}
        commits(last:1){nodes{commit{...CandidateChecks}}} potentialMergeCommit{...CandidateChecks}}}}
      fragment CandidateChecks on Commit{oid parents(first:3){totalCount nodes{oid}}
        statusCheckRollup{contexts(first:100){totalCount pageInfo{hasNextPage} nodes{__typename
          ... on StatusContext{context state isRequired(pullRequestNumber:$number) commit{oid}}
          ... on CheckRun{name status conclusion isRequired(pullRequestNumber:$number)
            checkSuite{commit{oid} app{databaseId slug} branch{name}
              matchingPullRequests(first:100){totalCount pageInfo{hasNextPage} nodes{number baseRefName headRefName}}
              workflowRun{event file{path repositoryName repositoryFileUrl}}}}}}}}"""
    try:
        repository = _check_query(root, query, number=pair["number"])
        pr = repository["pullRequest"]
        base = pr["baseRef"]
        identity = (pr["number"], pr["state"], pr["baseRefName"], pr["headRefName"],
                    base["target"]["oid"], pr["headRefOid"])
        if identity != (pair["number"], "OPEN", pair["base"], pair["branch"], pair["base_sha"], pair["head_sha"]):
            raise LandError("PR base or head moved while reading exact check evidence")
        required = {(item["context"], (item.get("app") or {}).get("databaseId"))
                    for item in (base["branchProtectionRule"] or {}).get("requiredStatusChecks") or []}
        for rule in _complete_check_nodes(base["rules"]):
            if rule["type"] == "REQUIRED_STATUS_CHECKS":
                required.update((item["context"], item.get("integrationId"))
                                for item in rule["parameters"]["requiredStatusChecks"])
            elif rule["type"] in {"WORKFLOWS", "REQUIRED_WORKFLOW_STATUS_CHECKS"}:
                raise LandError("required workflow evidence cannot be established from status checks")
        if pair.get("local_checks"):
            if required:
                raise LandError("local-check policy requires the operator to remove hosted required checks first")
            return "local-required"
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
        states, passed = set(), set()
        for check in contexts:
            is_run = check["__typename"] == "CheckRun"
            if not is_run and check["__typename"] != "StatusContext":
                raise LandError("unknown check evidence type")
            suite = check["checkSuite"] if is_run else check
            if suite["commit"]["oid"] != candidate["oid"] or not isinstance(check["isRequired"], bool):
                raise LandError("check result is unrelated to the pinned candidate or lacks requiredness")
            run = suite.get("workflowRun")
            if run:
                related = _complete_check_nodes(suite["matchingPullRequests"])
                if (run["event"] not in {"push", "pull_request", "pull_request_target"}
                        or not any((p["number"], p["baseRefName"], p["headRefName"]) ==
                                   (pair["number"], pair["base"], pair["branch"]) for p in related)
                        or (run["event"] == "push" and (suite.get("branch") or {}).get("name") != pair["branch"])):
                    raise LandError("workflow run does not belong to this PR candidate")
            status = check["conclusion"] if is_run and check["status"] == "COMPLETED" else (
                "PENDING" if is_run else check["state"])
            if status == "SKIPPED" and _inapplicable_check(root, check, pair, merge["oid"] if merge else None,
                                                         repository["nameWithOwner"]):
                continue
            states.add(status)
            if status == "SUCCESS":
                passed.add((check["name"] if is_run else check["context"],
                            (suite.get("app") or {}).get("databaseId"), check["isRequired"]))
        if any(not any(name == check_name and needed and (app is None or app == check_app)
                       for check_name, check_app, needed in passed) for name, app in required):
            return "skipped"
        if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            return "fail"
        if states & {"PENDING", "EXPECTED"}:
            return "pending"
        if states - {"SUCCESS"} or not states:
            return "skipped" if contexts or required else "none"
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


def _snapshot_pair(root: Path, branch: str, number: int, base: str, expected_head: str) -> dict:
    """Pin the exact GitHub PR base/head pair that check classification and local testing will judge."""
    pr = _pr_view(root, str(number)) or {}
    if pr.get("state") != "OPEN":
        raise LandError(f"PR #{number} is not open while its merge candidate is being pinned")
    if pr.get("baseRefName") != base or pr.get("headRefName") != branch:
        raise LandError(f"PR #{number} targets {pr.get('baseRefName')!r} from {pr.get('headRefName')!r}, not "
                        f"the expected {base!r} from {branch!r}")
    # #266: baseRefOid is PR metadata and may lag the actual branch after main is incorporated.
    base_sha, fetched_head = _fetch_rev(root, base), _fetch_rev(root, branch)
    head_sha = pr.get("headRefOid")
    if head_sha != expected_head:
        raise LandError(f"PR #{number} head moved from the pushed revision {expected_head} to {head_sha} before checks")
    if fetched_head != head_sha:
        raise LandError(f"PR #{number} refs moved while the merge candidate was being pinned "
                        f"(GitHub head {head_sha}, origin head {fetched_head})")
    return {"base": base, "branch": branch, "base_sha": base_sha, "head_sha": head_sha, "number": number}


def _assert_pair_current(root: Path, pair: dict) -> None:
    """Fail unless both origin refs and GitHub's PR still name the pinned pair."""
    base_sha = _fetch_rev(root, pair["base"])
    head_sha = _fetch_rev(root, pair["branch"])
    pr = _pr_view(root, str(pair["number"])) or {}
    actual = (base_sha, head_sha, pr.get("headRefOid"), pr.get("state"),
              pr.get("baseRefName"), pr.get("headRefName"))
    expected = (pair["base_sha"], pair["head_sha"], pair["head_sha"], "OPEN",
                pair["base"], pair["branch"])
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
    if pair.get("local_checks"):
        value = _checks_evidence(root, pair)
        _assert_pair_current(root, pair)
        return value
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


def _local_suite(cwd: Path, test_cmd: str, *, log: Path | None = None) -> dict:
    """Run and count the full local suite in the synthetic merge candidate."""
    argv = shlex.split(test_cmd)
    if not argv:
        raise LandError("the local test command is empty")
    _note(f"the merge candidate's local suite is the gate: {test_cmd}")
    result = {"command": test_cmd, "passed": False, "returncode": None, "tests": None, "skipped": None,
              "expected_failures": None, "error": None}
    try:
        # Hosted checks forbade focused-only tests; the mandatory local gate retains that setting.
        run = _run(["env", "CI=true", *argv] if log else argv, cwd, timeout=LOCAL_TEST_TIMEOUT)
    except LandError as exc:
        _note(f"the local suite did not run to completion — not merging: {exc}")
        result["error"] = str(exc)
        if log:
            cause = exc.__cause__
            captured = (cause.stdout, cause.stderr) if isinstance(cause, subprocess.TimeoutExpired) else ()
            output = "\n".join(part.decode(errors="replace") if isinstance(part, bytes) else part or ""
                               for part in captured)
            log.write_text(output + "\n" + str(exc) + "\n")
        return result
    if log:
        log.write_text((run.stdout or "") + "\n" + (run.stderr or ""))
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
def _candidate(root: Path, base_sha: str, head_sha: str, *, preserve_history: bool = False):
    """Yield the squash or two-parent merge candidate used by the selected GitHub merge method."""
    tmp = Path(tempfile.mkdtemp(prefix="alt-land-candidate-"))
    path = tmp / "candidate"
    try:
        worktree = _git(root, "worktree", "add", "--detach", str(path), base_sha, timeout=300)
        if worktree.returncode != 0:
            raise LandError(f"cannot build the merge candidate worktree: "
                            f"{((worktree.stderr or '') + (worktree.stdout or '')).strip()[-200:]}")
        method = ["--no-ff", "--no-commit"] if preserve_history else ["--squash"]
        merged = _git(path, "merge", *method, head_sha, timeout=300)
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
           *, preserve_history: bool = False) -> tuple[bool, dict | None]:
    """Merge, then believe GitHub about the result, not the exit code — `--delete-branch` can fail on the
    local half (a worktree holds the branch) after the merge itself succeeded. GitHub atomically refuses if the
    PR head changed after the commit whose provenance and checks this invocation validated."""
    method = ["--merge"] if preserve_history else ["--squash", "--delete-branch"]
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


def _merge_on_local_suite(root: Path, pair: dict, test_cmd: str, *, before_merge=None,
                          preserve_history: bool = False, merge: bool = True,
                          evidence_dir: Path | None = None) -> tuple[bool, dict | None, dict]:
    """Test one exact base/head pair and merge only while both tips still match it."""
    base_sha, head_sha = pair["base_sha"], pair["head_sha"]
    identity = {"base": base_sha, "head": head_sha}

    def finish(merged, main_run, tests):
        if evidence_dir:
            S.write_json(evidence_dir / "result.json", tests)
        return merged, main_run, tests

    try:
        _assert_pair_current(root, pair)
        with _candidate(root, base_sha, head_sha, preserve_history=preserve_history) as path:
            if evidence_dir:
                candidate = _need(_git(path, "rev-parse", "HEAD"), "candidate SHA")
                tree = _need(_git(path, "rev-parse", "HEAD^{tree}"), "candidate tree")
                evidence_dir = evidence_dir / candidate
                evidence_dir.mkdir(parents=True, exist_ok=True)
                identity.update(candidate=candidate, tree=tree, evidence=str(evidence_dir))
                # Fresh candidate worktrees have no web dependencies. Install the locked inputs.
                install = _run(["pnpm", "--dir", "web", "install", "--frozen-lockfile",
                                "--store-dir", str(config.ROOT / "pnpm-store")], path, timeout=LOCAL_TEST_TIMEOUT)
                (evidence_dir / "install.log").write_text((install.stdout or "") + "\n" + (install.stderr or ""))
                _need(install, "candidate dependency installation")
                tests = _local_suite(path, test_cmd, log=evidence_dir / "check.log")
                artifacts = path / "web" / "ui-artifacts" / "report"
                if artifacts.exists():
                    shutil.copytree(artifacts, evidence_dir / "ui-artifacts" / "report", dirs_exist_ok=True)
            else:
                tests = _local_suite(path, test_cmd)
    except LandError as exc:
        _note(f"not merging: {exc}")
        return finish(False, None, {"command": test_cmd, "passed": False, "returncode": None, "tests": None,
                             "skipped": None, "expected_failures": None, "error": str(exc),
                             **identity})
    tests.update(identity)
    if evidence_dir:
        S.write_json(evidence_dir / "result.json", tests)
    if not tests["passed"]:
        _note(f"not merging: the local suite ({test_cmd}) is not green on the merge candidate")
        return finish(False, None, tests)
    try:
        _assert_pair_current(root, pair)
    except LandError:
        tests["error"] = "the base or the head moved while the merge candidate was under test"
        _note(f"not merging: {tests['error']} — re-run alt land to test and merge the current pair")
        return finish(False, None, tests)
    after_checks = (_checks_evidence(root, pair) if pair.get("local_checks")
                    else _checks_state(root, pair["number"]))
    if after_checks == "none":
        after_checks = _checks_evidence(root, pair)
    try:
        _assert_pair_current(root, pair)
    except LandError:
        tests["error"] = "the base or the head moved while final checks were being read"
        _note(f"not merging: {tests['error']}")
        return finish(False, None, tests)
    if after_checks != ("local-required" if pair.get("local_checks") else "none"):
        tests["error"] = f"PR checks changed from none to {after_checks} while the local suite ran"
        _note(f"not merging: {tests['error']} — re-run alt land under the current gate")
        return finish(False, None, tests)
    if evidence_dir:
        body = json.loads(_need(_run(["gh", "pr", "view", str(pair["number"]), "--json", "body"], root),
                                "PR test summary"))["body"] or ""
        body = re.sub(r"^Tests: make check passed locally \([0-9a-f]{40}\).*\n?", "", body, flags=re.M)
        summary = (f"Tests: make check passed locally ({tests['candidate']}); "
                   f"base {base_sha}, head {head_sha}.\n")
        body_path = evidence_dir / "pr-body.md"
        body_path.write_text(body.rstrip() + "\n" + summary)
        _need(_run(["gh", "pr", "edit", str(pair["number"]), "--body-file", str(body_path)], root),
              "publish local test summary")
        _assert_pair_current(root, pair)
    if not merge:
        return finish(False, None, tests)
    if before_merge:
        before_merge()
    merged, main_run = _merge(root, pair["branch"], pair["number"], pair["base"], head_sha,
                              preserve_history=preserve_history)
    return finish(merged, None if pair.get("local_checks") else main_run, tests)


def land(message: str, *, project: str | None = None, pr_title: str | None = None, pr_body_file: str | None = None,
         merge: bool = False, wait: int = 600, base: str = "main",
         dry_run: bool = False, test_cmd: str = DEFAULT_TEST_CMD, cwd: Path | None = None,
         authority: dict | None = None, adopt_pr: int | None = None,
         expected_head: str | None = None, reason: str | None = None,
         closes_issues: list[int] | None = None) -> dict:
    """Run the whole sequence from the current worktree; returns the JSON-ready result object."""
    if not message.strip():
        raise LandError("--message is empty")
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
            f"cannot verify commit provenance for branch {branch!r}: no task record resolved; "
            "pass `--project` or run from the dispatch environment"
        )
    hold_merge = task.get("hold_merge")
    if hold_merge:  # an explicit merge hold is the exception to merge-by-default
        if merge:
            raise LandError(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
                            f"Burak releases it with `alt task hold-merge {slug} --off`; "
                            "re-run `alt land` without `--merge` — open the PR, report ok with the PR number, stop")
        _note(f"task {project}/{slug} carries a merge hold: {hold_merge}; "
              "the PR will be opened but not merged")
    # This is deliberately before fetch, committing, or any GitHub call. Put the
    # fence in the library rather than only in bin/alt so direct callers cannot
    # bypass current-publisher ownership.
    _require_current_publisher(project, slug, task, authority=authority)
    fetched = _git(root, "fetch", "-q", "origin", base)
    if fetched.returncode != 0:
        raise LandError(f"git fetch origin {base}: {(fetched.stderr or fetched.stdout).strip()[-300:]}")
    # Record the branch tip before committing: a later force may replace only this exact
    # remote history, and a push from another worker after this point must make the lease fail.
    adoption, pr = _adoption(root, project, slug, task, branch, base, adopt_pr, expected_head, reason)
    publish_branch = adoption["branch"] if adoption else branch
    recorded_tip = None if adoption else _fetch_remote_tip(root, branch)
    task_ref = f"{project}/{slug}"
    try:
        missing = git_policy.commits_missing_task_trailer(
            root, base, task_ref, adopted_head=adoption["head"] if adoption else None)
    except git_policy.GitPolicyError as exc:
        raise LandError(f"cannot verify commit provenance: {exc}") from exc
    if missing:
        sample = ", ".join(sha[:12] for sha in missing[:5])
        raise LandError(
            f"branch has commit(s) without exact `Altitude-Task: {task_ref}` provenance: {sample}; "
            "refusing before committing or pushing" + ("" if adoption else ", or contacting GitHub")
        )
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
        return {"pr": None, "url": None, "checks": "dry-run", "merged": False, "main_run": None, "branch": branch,
                "commit": None, "head": None, "lease": lease, "staged": changed, "hold": hold_merge,
                "replaced": [], "local_tests": None, "dry_run": True, "adopted_pr": adoption}
    if not adoption:
        pr = _pr_view(root, branch)
    cutoff, continued_pr = None, None
    if pr is not None and pr.get("state") == "MERGED":
        cutoff = _continuation_base(root, project, slug, task, publish_branch, base, pr, bool(changed), recorded_tip)
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
    if pr is not None and pr.get("state") == "CLOSED":
        raise LandError(f"PR #{pr.get('number')} for {branch!r} is closed without being merged — refusing to "
                        f"commit or push onto a closed PR: reopen it (`gh pr reopen {pr.get('number')}`) "
                        f"and re-run alt land, or start a new task branch")
    if changed or not pr or (task.get("delivery") or {}).get("head") != _need(_git(root, "rev-parse", "HEAD"), "head"):
        task = _record_delivery(project, slug, task, authority, branch=publish_branch,
                                base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"))
    if changed:
        body = message.rstrip("\n") + f"\n\nAltitude-Task: {task_ref}"
        _need(_git(root, "commit", "-m", body), "git commit")
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
    replaced = _push(root, publish_branch, base, task_ref, recorded_tip,
                     source_branch=branch if adoption else None)
    pushed_head = _need(_git(root, "rev-parse", f"origin/{publish_branch}"), "cannot capture the pushed PR head")
    _note(f"pushed head {pushed_head}")
    pr = _ensure_pr(root, publish_branch, base, message, pr_title, pr_body_file, task_ref, pr=pr)
    number = pr.get("number")
    task = _record_delivery(project, slug, task, authority, branch=publish_branch, number=number,
                            head=pushed_head, base=_need(_git(root, "rev-parse", f"origin/{base}"), "base"))
    hold_merge = task.get("hold_merge")
    pair = _snapshot_pair(root, publish_branch, number, base, pushed_head)
    origin = _need(_git(root, "config", "--get", "remote.origin.url"), "origin URL")
    repository = github_intake._REMOTE.fullmatch(origin)
    pair["local_checks"] = bool(repository and
                                f"{repository['owner']}/{repository['repo']}".lower() == config.LOCAL_CHECK_REPOSITORY)
    if pair["local_checks"]:
        test_cmd = "make check"
    checks = _checks_value(root, number, pair)
    deadline = time.monotonic() + max(wait, 0)
    while checks == "pending" and time.monotonic() < deadline:
        time.sleep(min(CHECK_POLL_SECONDS, max(deadline - time.monotonic(), 1.0)))
        checks = _checks_value(root, number, pair)
    _require_closing_issues(root, number, closes_issues)
    merged, main_run, local_tests = pr.get("state") == "MERGED", None, None
    def before_merge():
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
        current = S.load_task(project, slug)
        _require_current_publisher(project, slug, current, authority)
        if current.get("adopted_pr") != adoption:
            raise LandError("task adoption changed before merge")
        if current.get("hold_merge"):
            raise LandError(f"task carries a merge hold: {current['hold_merge']}")
        _require_closing_issues(root, number, closes_issues)
    if checks == "local-required":
        merged, main_run, local_tests = _merge_on_local_suite(
            root, pair, test_cmd, before_merge=before_merge, preserve_history=bool(adoption), merge=merge,
            evidence_dir=S.task_dir(project, slug) / "local-checks")
        checks = "local-pass" if local_tests["passed"] and not local_tests["error"] else "local-fail"
    elif merge and not merged:
        if checks == "none-configured":
            merged, main_run, local_tests = _merge_on_local_suite(
                root, pair, test_cmd, before_merge=before_merge, preserve_history=bool(adoption))
        elif checks == "pass":
            _assert_pair_current(root, pair)
            before_merge()
            merged, main_run = _merge(root, publish_branch, number, base, pushed_head,
                                      preserve_history=bool(adoption))
        else:
            _note(f"not merging: checks are {checks!r}")
    return {"pr": number, "url": pr.get("url"), "checks": checks, "merged": merged, "main_run": main_run,
            "branch": branch, "commit": commit, "head": pushed_head, "lease": lease, "staged": staged,
            "hold": hold_merge, "replaced": replaced, "local_tests": local_tests, "adopted_pr": adoption}
