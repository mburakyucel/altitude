"""The tidy rule: when Altitude itself sets aside a worktree or deletes a branch that a routine run asked about, and how
it puts one back (docs/ARCHITECTURE.md#routines-that-act).

A run only names a target. Altitude matches the name exactly against what it lists itself (`git worktree list`, the
repository's branches), checks the rule from Git and its own task records at the moment it acts, and passes only its
own values to Git. Every limit that protects the operator's work is here, none in a routine's brief: a worktree is set
aside only when nothing in it would be lost, nothing uses it and nothing changed in it for `QUIET`; a branch is deleted
only when main or a merged pull request has exactly its tip and it has not moved for `QUIET`. A set-aside worktree
moves, on the same disk, into the worktree root's `.set-aside` folder for `ASIDE`, so Undo moves it back exactly, ignored files
included; the tick then removes it for good. A deleted branch's commit is kept under `refs/altitude/undo/` and Undo
recreates the branch at the commit Altitude recorded, refusing when that ref no longer matches, since task workers can
write refs.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, git_policy, platform, state as S

QUIET = timedelta(days=7)
ASIDE = timedelta(days=14)
SET_ASIDE = ".set-aside"
#: Folders under the worktree root that are never a target: routine runs' and agent chats' own copies.
RUN_COPY = re.compile(r"project-agent-|chat-[0-9a-f]{12}$")
UNDO = "refs/altitude/undo"
#: Lines the run's inventory lists at most, of worktrees and of branches each.
LISTED = 100


class Kept(Exception):
    """Why the rule keeps a target, as the run's answer and the run page say it after "Kept: "."""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # No optional locks: a status that refreshed the index would read as a change to the worktree.
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120,
                          env={**git_policy.git_env(cwd), "GIT_OPTIONAL_LOCKS": "0"})


def _said(result: subprocess.CompletedProcess) -> str:
    text = " ".join((result.stderr or result.stdout or "").split())
    return (text[:200] or f"git exited with {result.returncode}").removeprefix("fatal: ").removeprefix("error: ")


def root(project: str) -> Path:
    return config.project_path(project) / config.WORKTREE_ROOT


def worktrees(project: str) -> list[dict]:
    """Every worktree the project's repository registers, as `git worktree list --porcelain` gives it."""
    repo = config.project_path(project)
    listed = _git(repo, "worktree", "list", "--porcelain")
    if listed.returncode:
        raise RuntimeError(_said(listed))
    out, entry = [], None
    for line in listed.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "worktree":
            entry = {"path": value, "head": None, "branch": None, "locked": False}
            out.append(entry)
        elif entry is not None and key == "HEAD":
            entry["head"] = value
        elif entry is not None and key == "branch":
            entry["branch"] = value.removeprefix("refs/heads/")
        elif entry is not None and key == "locked":
            entry["locked"] = True
    return out


def _candidates(project: str) -> dict[str, dict]:
    """The worktrees a run may name: registered, a plain folder directly under the worktree root, not a routine run's or
    agent chat's copy."""
    base = os.path.realpath(root(project))
    out = {}
    for entry in worktrees(project):
        path = Path(entry["path"])
        if (os.path.realpath(path.parent) != base or path.name.startswith(".") or RUN_COPY.match(path.name) or path.is_symlink()
                or not path.is_dir()):
            continue
        out[path.name] = {**entry, "name": path.name, "path": path}
    return out


def _branches(project: str) -> dict[str, str]:
    repo = config.project_path(project)
    listed = _git(repo, "for-each-ref", "--format=%(refname:strip=2) %(objectname)", "refs/heads")
    if listed.returncode:
        raise RuntimeError(_said(listed))
    return dict(line.split(" ", 1) for line in listed.stdout.splitlines() if " " in line)


def _tasks(project: str) -> list[dict]:
    return S.list_tasks(project, include_archive=True)


def _title(task: dict) -> str:
    return task.get("title") or task["slug"]


def _ago(at: float, now: datetime) -> str:
    days = int((now.timestamp() - at) // 86400)
    return "today" if days < 1 else "yesterday" if days == 1 else f"{days} days ago"


def unsaved(worktree: Path) -> str | None:
    """Why removing `worktree` would lose work: "uncommitted" changes (untracked files included, ignored ones not) or
    "loose" commits found on no branch or remote; None when nothing would be lost. Task cleanup shares it."""
    status = _git(worktree, "status", "--porcelain", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        return "uncommitted"
    loose = _git(worktree, "rev-list", "-n1", "HEAD", "--not", "--branches", "--remotes")
    if loose.returncode or loose.stdout.strip():
        return "loose"
    return None


def _newest(path: Path, after: float) -> float:
    """The newest change time of `path` and everything under it, ignored files included, without following links;
    it stops at the first change after `after`."""
    newest = os.lstat(path).st_mtime
    stack = [path]
    while stack and newest <= after:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    if newest > after:
                        break
        except FileNotFoundError:
            continue
    return newest


def check_worktree(project: str, target: str, *, put_back: dict, now: datetime) -> dict:
    """The worktree `target` names, when the rule lets Altitude set it aside now; raises Kept otherwise."""
    found = _candidates(project).get(target) or next(
        (w for w in _candidates(project).values() if str(w["path"]) == target), None)
    if not found:
        raise Kept("It is not one of this project's worktrees Altitude may set aside.")
    name, path = found["name"], found["path"]
    if ("worktree", name) in put_back:
        raise Kept(f"You put it back {put_back[('worktree', name)]}.")
    if found["locked"]:
        raise Kept("It is locked.")
    try:
        _, _, record = git_policy._task_layout(path)
        made = os.stat(record / "commondir").st_mtime  # Git writes it once, when it makes the worktree
    except (git_policy.GitPolicyError, OSError):
        raise Kept("Its Git record is not in order.") from None
    tasks = _tasks(project)
    # A task's own worktree is task cleanup's, which removes it once the task ends and nothing in it would be lost;
    # one set aside here would stop the operator's reopening of the task from checking out its branch again.
    own = next((t for t in tasks if t["slug"] == name), None)
    if own:
        raise Kept(f"It is task “{_title(own)}”’s worktree.")
    # A helper copy of a task that is still open stays until that task ends.
    older = next((t for t in tasks if t["state"] in S.OPEN_STATES
                  and datetime.fromisoformat(t["created"]).timestamp() <= made), None)
    if older:
        raise Kept(f"Task “{_title(older)}” was open when it was made and still is.")
    cutoff = (now - QUIET).timestamp()
    changed = max(_newest(record, cutoff), _newest(path, cutoff))
    if changed > cutoff:
        raise Kept(f"It changed {_ago(changed, now)}.")
    try:
        in_use = platform.folder_in_use(path)
    except (OSError, subprocess.SubprocessError):
        raise Kept("Altitude could not tell whether a program has it open.") from None
    if in_use:
        raise Kept("A program has it open.")
    lost = unsaved(path)
    if lost:
        raise Kept("It has uncommitted changes." if lost == "uncommitted" else "Its commits are on no branch.")
    return found


def _merged(project: str, name: str, sha: str) -> str | None:
    """How a branch's tip reached main: "Merged into main.", "Merged by #214." or None."""
    from . import verify
    repo = config.project_path(project)
    if _git(repo, "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main").returncode == 0:
        return "Merged into main."
    try:
        found = verify.gh(["pr", "list", "--head", name, "--base", "main", "--state", "merged", "-L", "20",
                           "--json", "number,headRefOid,isCrossRepository"], repo) or []
    except verify.VerifierFault:
        return None
    number = next((p["number"] for p in found if isinstance(p, dict) and p.get("headRefOid") == sha
                   and p.get("isCrossRepository") is False), None)
    return f"Merged by #{number}." if number else None


def _moved(project: str, name: str, sha: str) -> float:
    """When the branch last moved: its newest reflog entry, else its tip's commit time."""
    repo = config.project_path(project)
    logged = _git(repo, "log", "-g", "-n1", "--date=unix", "--format=%gd", f"refs/heads/{name}")
    stamp = logged.stdout.strip().rpartition("@{")[2].rstrip("}")
    if logged.returncode == 0 and stamp.isdigit():
        return float(stamp)
    return float(_git(repo, "log", "-1", "--format=%ct", sha).stdout.strip() or 0)


def merged_tip(project: str, target: str) -> tuple[str, str | None] | None:
    """The branch's tip and how it reached main, looked up before the project lock, as task cleanup does: GitHub can
    take a minute to answer."""
    sha = _branches(project).get(target)
    return (sha, _merged(project, target, sha)) if sha else None


def check_branch(project: str, target: str, *, put_back: dict, now: datetime,
                 looked: tuple[str, str | None] | None = None) -> dict:
    """The local branch `target` names, with its tip and how it merged, when the rule lets Altitude delete it now;
    raises Kept otherwise. `looked` is `merged_tip`'s answer, used only while the tip is still the one it names."""
    sha = _branches(project).get(target)
    if not sha:
        raise Kept("It is not one of this project's local branches.")
    if ("branch", target) in put_back:
        raise Kept(f"You put it back {put_back[('branch', target)]}.")
    repo = config.project_path(project)
    default = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip().removeprefix("origin/")
    if target in ("main", default):
        raise Kept("It is the main branch.")
    holder = next((w for w in worktrees(project) if w["branch"] == target), None)
    if holder:
        raise Kept(f"It is checked out in {Path(holder['path']).name}.")
    task = next((t for t in _tasks(project) if t.get("branch") == target and t["state"] in S.OPEN_STATES), None)
    if task:
        raise Kept(f"Task “{_title(task)}” is still open.")
    moved = _moved(project, target, sha)
    if moved > (now - QUIET).timestamp():
        raise Kept(f"It moved {_ago(moved, now)}.")
    merged = looked[1] if looked and looked[0] == sha else _merged(project, target, sha)
    if not merged:
        ahead = _git(repo, "rev-list", "--count", sha, "--not", "refs/remotes/origin/main").stdout.strip()
        raise Kept(f"It has {ahead or 'some'} commit{'' if ahead == '1' else 's'} found nowhere else.")
    return {"name": target, "sha": sha, "merged": merged}


def set_aside(project: str, found: dict, key: str, now: datetime) -> dict:
    """Move a checked worktree under the set-aside folder and keep its commit for Undo; what the change records.
    `key` ("<agent>/<run>/<change>") names its undo ref and its folder."""
    repo = config.project_path(project)
    undo_ref, aside = f"{UNDO}/{key}", root(project) / SET_ASIDE / key.replace("/", "-") / found["name"]
    aside.parent.mkdir(parents=True, exist_ok=True)
    if found["head"]:
        kept = _git(repo, "update-ref", undo_ref, found["head"])
        if kept.returncode:
            raise RuntimeError(_said(kept))
    moved = _git(repo, "worktree", "move", str(found["path"]), str(aside))
    if moved.returncode:
        _git(repo, "update-ref", "-d", undo_ref)
        raise RuntimeError(_said(moved))
    return {"path": str(found["path"]), "aside": str(aside), "head": found["head"], "branch": found["branch"],
            "ref": undo_ref, "until": (now + ASIDE).replace(microsecond=0).isoformat()}


def delete_branch(project: str, found: dict, key: str) -> dict:
    """Delete a checked branch only while it is still at the tip Altitude checked, keeping that commit for Undo."""
    repo, undo_ref = config.project_path(project), f"{UNDO}/{key}"
    kept = _git(repo, "update-ref", undo_ref, found["sha"])
    if kept.returncode:
        raise RuntimeError(_said(kept))
    deleted = _git(repo, "update-ref", "-d", f"refs/heads/{found['name']}", found["sha"])
    if deleted.returncode:
        _git(repo, "update-ref", "-d", undo_ref)
        raise RuntimeError(_said(deleted))
    return {"sha": found["sha"], "merged": found["merged"], "ref": undo_ref}


def _kept_commit(repo: Path, change: dict, sha: str | None) -> None:
    if not sha:
        return
    held = _git(repo, "rev-parse", "--verify", "-q", change["ref"]).stdout.strip()
    if held != sha:
        raise Kept("its saved commit no longer matches Altitude's record.")


def undo(project: str, change: dict) -> None:
    """Put back what `change` did; raises Kept with why not."""
    repo = config.project_path(project)
    if change["kind"] == "branch":
        name = change["target"]
        if _branches(project).get(name):
            raise Kept("a branch by that name exists again.")
        _kept_commit(repo, change, change["sha"])
        made = _git(repo, "update-ref", f"refs/heads/{name}", change["sha"], "")
        if made.returncode:
            raise Kept(_said(made))
    else:
        path = Path(change["path"])
        if os.path.lexists(path):
            raise Kept("a worktree is already at that path.")
        if not change.get("purged"):
            moved = _git(repo, "worktree", "move", change["aside"], str(path))
            if moved.returncode:
                raise Kept(_said(moved))
        else:
            _kept_commit(repo, change, change["head"])
            free = change["branch"] and _branches(project).get(change["branch"]) and not any(
                w["branch"] == change["branch"] for w in worktrees(project))
            added = _git(repo, "worktree", "add", *((str(path), change["branch"]) if free
                                                    else ("--detach", str(path), change["head"])))
            if added.returncode:
                raise Kept(_said(added))
    _git(repo, "update-ref", "-d", change["ref"])


def purge(project: str, change: dict) -> str | None:
    """Remove a set-aside worktree for good once its time is up; why not, when Git refuses."""
    repo = config.project_path(project)
    if not Path(change["aside"]).is_dir():
        return None
    removed = _git(repo, "worktree", "remove", change["aside"])
    return None if removed.returncode == 0 else _said(removed)


def forget(project: str, prefix: str) -> None:
    """Drop the undo refs under `prefix`, as a pruned run takes its Undo with it."""
    repo = config.project_path(project)
    listed = _git(repo, "for-each-ref", "--format=%(refname)", prefix)
    for ref in listed.stdout.split():
        _git(repo, "update-ref", "-d", ref)


def inventory(project: str, now: datetime | None = None) -> str:
    """The run's list of the project's worktrees and branches with what Git and Altitude's records say about each: a
    starting point for its judgment, never the rule, which Altitude checks again when it is asked."""
    now = now or datetime.now(timezone.utc)
    repo = config.project_path(project)
    tasks = {t["slug"]: t for t in _tasks(project)}
    by_branch = {t.get("branch"): t for t in tasks.values() if t.get("branch")}
    lines = [f"Worktrees under {config.WORKTREE_ROOT}:"]
    found = sorted(_candidates(project).values(), key=lambda w: w["name"])
    for w in found[:LISTED]:
        task = tasks.get(w["name"])
        said = [f"task “{_title(task)}” is {task['state']}" if task else "no task names it",
                f"on branch {w['branch']}" if w["branch"] else "detached"]
        try:
            _, _, record = git_policy._task_layout(w["path"])
            said.append(f"its Git record changed {_ago(_newest(record, now.timestamp()), now)}")
            lost = unsaved(w["path"])
            said.append({"uncommitted": "has uncommitted changes", "loose": "has commits on no branch"}.get(
                lost, "nothing uncommitted"))
        except (git_policy.GitPolicyError, OSError):
            said.append("its Git record is not in order")
        lines.append(f"- {w['name']}: " + "; ".join(said))
    if not found:
        lines.append("- none")
    elif len(found) > LISTED:
        lines.append(f"- and {len(found) - LISTED} more")
    held = {w["branch"]: Path(w["path"]).name for w in worktrees(project) if w["branch"]}
    lines.append("\nLocal branches:")
    branches = sorted(_branches(project).items())
    for name, sha in branches[:LISTED]:
        on_main = _git(repo, "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main").returncode == 0
        task = by_branch.get(name)
        said = ["on main" if on_main else "not on main", f"moved {_ago(_moved(project, name, sha), now)}"]
        if name in held:
            said.append(f"checked out in {held[name]}")
        if task:
            said.append(f"task “{_title(task)}” is {task['state']}")
        lines.append(f"- {name}: " + "; ".join(said))
    if len(branches) > LISTED:
        lines.append(f"- and {len(branches) - LISTED} more")
    return "\n".join(lines)
