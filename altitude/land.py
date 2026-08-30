"""alt land — the whole land-a-PR sequence as one plain command (decision 47, tier 0).

The measured why: status → add → commit → push → `gh pr create` → `gh pr view` → merge → fetch → run list cost
~100 turns across L2/L1 sessions, and every compound form of it trips the Safety Net (R-003). Here it is one
command: stage only the task's lease (refuse if anything outside it changed), commit with the Altitude trailer,
push with one rebase retry on a non-fast-forward (never two), open or reuse the PR, wait for checks, merge only
on green and only when asked. No model call anywhere — the commit message arrives as an argument. Idempotent:
nothing to commit is a skip, an up-to-date push is a no-op, an open PR is reused."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, dispatch, state as S

CHECK_POLL_SECONDS = 15
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
UNDECLARED = "(undeclared — all changes staged)"


class LandError(RuntimeError):
    """A refusal or a dead end the caller must see; bin/alt prints it on stderr and exits non-zero."""


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


def _push(root: Path, branch: str) -> None:
    """`git push -u origin <branch>`; on a non-fast-forward, exactly one `git pull --rebase` and one re-push."""
    p = _git(root, "push", "-u", "origin", branch, timeout=300)
    if p.returncode == 0:
        return
    err = (p.stderr or "") + (p.stdout or "")
    if not any(s in err for s in ("non-fast-forward", "fetch first", "[rejected]")):
        raise LandError(f"git push: {err.strip()[-300:]}")
    _note("push rejected (non-fast-forward) — one `git pull --rebase`, one re-push")
    r = _git(root, "pull", "--rebase", "origin", branch, timeout=300)
    if r.returncode != 0:
        raise LandError("git pull --rebase failed — if it stopped on conflicts the worktree is now mid-rebase: "
                        "resolve and `git rebase --continue`, or `git rebase --abort`, then re-run alt land: "
                        f"{((r.stderr or '') + (r.stdout or '')).strip()[-300:]}")
    p2 = _git(root, "push", "-u", "origin", branch, timeout=300)
    if p2.returncode != 0:
        raise LandError(f"push failed again after one rebase — stopping, not retrying: "
                        f"{((p2.stderr or '') + (p2.stdout or '')).strip()[-300:]}")


def _pr_view(root: Path, branch: str) -> dict | None:
    p = _run(["gh", "pr", "view", branch, "--json", "number,url,state"], root)
    if p.returncode != 0:
        err = ((p.stderr or "") + (p.stdout or "")).strip()
        if "no pull requests found" in err.lower() or "not found" in err.lower():
            return None  # legitimately missing, not a tooling failure (decision 36)
        raise LandError(f"gh pr view {branch}: {err[-200:]}")
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise LandError(f"gh pr view {branch}: unparseable output") from e


def _pr_files(root: Path, base: str) -> list[str]:
    p = _git(root, "diff", "--name-only", f"origin/{base}...HEAD")
    return sorted(x for x in (p.stdout or "").splitlines() if x) if p.returncode == 0 else []


def _ensure_pr(root: Path, branch: str, base: str, message: str, pr_title: str | None,
               pr_body_file: str | None, task_ref: str, pr: dict | None = None) -> dict:
    """Reuse the branch's PR when one exists (editing it only when asked); otherwise create it. `pr` is the
    caller's already-fetched view of the branch's PR, so the happy path costs one `gh pr view`, not two."""
    title = pr_title or message.splitlines()[0]
    if pr is None:
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
    """One reading of the PR's checks: pass / fail / pending / skipped. `gh pr checks` exits non-zero for
    pending or failing checks, so the JSON body is the verdict, not the exit code."""
    p = _run(["gh", "pr", "checks", str(number), "--json", "bucket"], root)
    body = (p.stdout or "").strip()
    if not body:
        if "no checks" in ((p.stderr or "") + (p.stdout or "")).lower():
            return "skipped"
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
        return "skipped"
    if buckets & {"fail", "cancel"}:
        return "fail"
    if "pending" in buckets:
        return "pending"
    return "skipped" if buckets <= {"skipping"} else "pass"


def _merge(root: Path, branch: str, number: int, base: str) -> tuple[bool, dict | None]:
    """Squash-merge, then believe GitHub about the result, not the exit code — `--delete-branch` can fail on the
    local half (a worktree holds the branch) after the merge itself succeeded."""
    m = _run(["gh", "pr", "merge", str(number), "--squash", "--delete-branch"], root, timeout=300)
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


def land(message: str, *, project: str | None = None, pr_title: str | None = None, pr_body_file: str | None = None,
         merge: bool = False, wait: int = 600, paths: str | None = None, base: str = "main",
         dry_run: bool = False, cwd: Path | None = None) -> dict:
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
    if paths is not None:
        lease = [p.strip() for p in paths.split(",") if p.strip()]
        if not lease:
            raise LandError("--paths was given but names no paths")
        lease_src = "--paths"
    elif task is not None:
        lease = dispatch.task_paths(project, task)
        if not lease:
            raise LandError(f"task {project}/{slug} resolved but its lease is empty — refusing to stage "
                            f"anything; declare paths on the task or pass --paths")
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
                "commit": None, "lease": lease_repr, "staged": changed, "dry_run": True}
    pr = _pr_view(root, branch)
    if pr is not None and pr.get("state") == "MERGED":
        if groups:
            raise LandError(f"PR #{pr.get('number')} for {branch!r} is already merged — this branch has landed; "
                            f"refusing to commit new changes onto it, start a new task branch")
        _note(f"PR #{pr.get('number')} already merged — nothing to push, not resurrecting the branch")
        return {"pr": pr.get("number"), "url": pr.get("url"), "checks": _checks_state(root, pr.get("number")),
                "merged": True, "main_run": None, "branch": branch, "commit": None, "lease": lease_repr,
                "staged": []}
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
    _push(root, branch)
    task_ref = f"{project}/{slug}" if project and slug else "(unresolved)"
    pr = _ensure_pr(root, branch, base, message, pr_title, pr_body_file, task_ref, pr=pr)
    number = pr.get("number")
    checks = _checks_state(root, number)
    deadline = time.monotonic() + max(wait, 0)
    while checks == "pending" and time.monotonic() < deadline:
        time.sleep(min(CHECK_POLL_SECONDS, max(deadline - time.monotonic(), 1.0)))
        checks = _checks_state(root, number)
    merged, main_run = pr.get("state") == "MERGED", None
    if merge and not merged:
        if checks == "pass":
            merged, main_run = _merge(root, branch, number, base)
        else:
            _note(f"not merging: checks are {checks!r}")
    return {"pr": number, "url": pr.get("url"), "checks": checks, "merged": merged, "main_run": main_run,
            "branch": branch, "commit": commit, "lease": lease_repr, "staged": staged}
