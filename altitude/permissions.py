"""The permission allowlist every Claude session Altitude launches carries (I-064) — one table, two renderings.

Consumers: the `permissions.allow` block of a settings file (`engines.claude_settings(repo)` for every launch without a
per-dispatch file — L1s, proposals, critic, sizer, intake, improve, verify — and `dispatch.session_settings` for fresh
and resumed L2s) and the `--allowedTools` string the L3 turn passes (`l3.allowed_tools(project)`). Nothing is
hand-copied: a rule exists in this table or it does not exist. The table is rendered **per checkout**: the one rule that
names a repository (the `gh api` pulls route) is read from the remote of the checkout the session runs in, so a session
serving project X carries X's route and never Altitude's own.

Why an allowlist and not the classifier: in auto mode the wording-based classifier decides case by case, and when it
denies, a non-interactive session parks on an approval prompt nobody can answer (I-064: the L3 could not run
`alt task hold-merge … --off` or `alt incident amend`, an L2 was denied `gh pr merge` and another `git reset --hard
origin/main` in its own worktree). Allow rules are decided before the classifier is asked.

This is a positive allowlist only. Claude evaluates deny before allow, so `hooks/guard.py` and the never-list keep
working unchanged; there is no `deny` block here and nothing here escalates — no privilege escalation, no service-unit
management, no `rm`, and no push rule at all (see `_GIT`).

Rule semantics (Claude Code 2.1.251, read from its Bash rule matcher on 2026-08-30): a rule ending in `:*` is a legacy
prefix rule; a rule with a bare `*` is a wildcard where `*` matches anything, spaces included, and a lone trailing ` *`
also admits the bare command (`Bash(alt *)` admits `alt` and `alt task status x`); any other rule is an exact match.
Every verb rule here therefore ends in ` *` — space first — so a verb never bleeds into a neighbouring subcommand:
`git diff *` admits `git diff` and `git diff --stat`, while the space-less `git diff*` also admitted
`git difftool --extcmd=<anything>`, which runs an arbitrary command. `tests/test_permissions.py` ports that matcher and
proves the table against I-064's commands, the never-list and the neighbouring subcommands.

Residual risk (accepted, 2026-08-30): `Bash(gh pr merge *)` also matches `gh pr merge 1 --admin --repo other/project`.
Claude's Bash rules are prefix/wildcard shapes and cannot express "not `--admin`" or "this repository only", and
`gh pr merge` is the exact command I-064 requires. The allow rule removes the prompt; it does not authorise the act:
`--admin` and cross-repository merges stay forbidden by the never-list and R-013 (a merge under a hold is a breach),
and narrowing them belongs in `hooks/guard.py` (deny runs before allow), not in this table. A residual prompt is not
silent either: `hooks/permission_prompt_fault.py` counts it and the tick raises it as a system fault (decision 36).
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from typing import Sequence

from . import config

# gh: the verbs the roles use, one rule each, never a bare `gh *`. `gh pr diff` (fresh review) and `gh pr comment`
# (review disposition) are role needs with no production call site today — intentional. The `gh api` pulls route is
# rendered per checkout from the git remote (`github_repo`), never as a wildcard over repositories.
_GH = ("gh pr view", "gh pr list", "gh pr diff", "gh pr checks", "gh pr create", "gh pr merge", "gh pr comment",
       "gh pr ready", "gh run view", "gh run list", "gh run watch", "gh issue")

# git: bounded read plus the write forms a worktree session legitimately needs, every verb followed by ` *` (module
# docstring: the space keeps `git diff` from admitting `git difftool`, `git log` from `git logtool`, `git show` from
# `git show-branch`, `git commit` from `git commit-tree`). There is deliberately no push rule: the matcher turns
# `git push origin *` into `^git push origin( .*)?$`, which admits `git push origin --force main`, and no rule shape
# says "a push that is neither forced nor to main". Pushes stay with the classifier and the never-list
# (`hooks/guard.py`); the landing push runs inside `alt land`, which the `alt` rules cover.
_GIT = ("git status *", "git log *", "git diff *", "git show *", "git fetch *", "git branch *", "git rev-parse *",
        "git add *", "git commit *", "git rebase origin/main", "git reset --hard origin/main")

_GITHUB_REMOTE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def _remote_url(repo: Path | None = None) -> str | None:
    """`remote.origin.url` of `repo` (default: this checkout), or None when git or the remote is not there."""
    try:
        p = subprocess.run(["git", "-C", str(repo or config.REPO), "config", "--get", "remote.origin.url"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def github_repo(repo: Path | None = None) -> str | None:
    """`owner/repo` of the GitHub origin of `repo` (default: this checkout), read at render time; None when the remote
    cannot be read or is not GitHub — the pulls route is then omitted rather than widened."""
    url = _remote_url(repo)
    m = _GITHUB_REMOTE.search(url) if url else None
    return f"{m.group(1)}/{m.group(2)}" if m else None


def allow_rules(repo: Path | None = None) -> list[str]:
    """The `Bash(...)` rule strings for a session running in `repo` (default: this checkout), in table order: alt (both
    invocation forms), gh, the exact pulls route of that checkout, git."""
    rules = ["alt *", f"{config.REPO / 'bin' / 'alt'} *"]   # `alt` is on PATH in every nested launch (engines.clean_env)
    rules += [f"{verb} *" for verb in _GH]
    owner_repo = github_repo(repo)
    if owner_repo:
        # `pulls*` (no space) on purpose: the route continues with `/56/merge`; nothing else under `repos/<x>/pulls…` exists
        rules.append(f"gh api repos/{owner_repo}/pulls*")
    rules += _GIT
    return [f"Bash({r})" for r in rules]


def permissions_block(repo: Path | None = None) -> dict:
    """The `permissions` object merged into a settings file: allow only (module docstring)."""
    return {"allow": allow_rules(repo)}


def allowed_tools(extra: Sequence[str] = (), repo: Path | None = None) -> str:
    """The comma-joined `--allowedTools` value: the non-Bash tools a role needs, then every rule from `allow_rules(repo)`."""
    return ",".join([*extra, *allow_rules(repo)])
