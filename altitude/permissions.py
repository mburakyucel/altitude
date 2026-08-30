"""The permission allowlist every Claude session Altitude launches carries (I-064) — one table, two renderings — and the
one definition of the passive permission-prompt hook that rides beside it.

Consumers: the `permissions.allow` block of a settings file (`engines.claude_settings(repo)` for every launch without a
per-dispatch file — L1s, proposals, critic, sizer, intake, improve, verify — and `dispatch.session_settings` for fresh
and resumed L2s) and the `--allowedTools` string the L3 turn passes (`l3.allowed_tools(project)`). Nothing is
hand-copied: a rule exists in this table or it does not exist. Every consumer still takes the checkout it renders for
(`repo`/`cwd`, one settings file per repository), but no rule currently varies by checkout, so the rendered allowlist is
identical for every checkout; the threading stays because it is the shape any future repository-specific rule needs.
The hook wiring (`prompt_fault_hooks`) is shared the same way: both settings files take it from here, so neither can
drift from the other.

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

Option sweep (2026-08-30, the review of PR #89). A wildcard tail carries every option of its verb, so every rule was
checked for options that name a program to run or inject configuration — `--upload-pack`, `--receive-pack`, `--exec`,
`--extcmd`, `--ext-diff` and `-c <key>=<value>` against each git rule, and the gh analogues:
- `git fetch` is exact forms only (`_GIT_FETCH`): `git fetch --upload-pack=<program>` runs that program, and a remote
  argument (`git fetch <url>`, or `git fetch <helper>::<x>`, which runs `git-remote-<helper>` from PATH — and PATH
  holds this checkout's `bin/` in every session) fetches from anywhere. `--receive-pack` and `--exec` belong to
  `git push`, and there is no push rule.
- `git rebase origin/main` and `git reset --hard origin/main` are exact, so `--exec`/`-x` cannot ride on the rebase.
- `git -c <key>=<value>`, `--exec-path`, `--git-dir` and `-C` are global options written *before* the verb; every
  rule starts `git <verb>`, so none admits them, and an environment prefix (`GIT_EXTERNAL_DIFF=… git diff`) matches no
  rule either. After the verb, `-c` means something else on every listed verb (`log`/`diff`/`show`: combined diff;
  `commit`: reuse a message; `branch`: copy a branch) and no listed verb takes `<key>=<value>`.
- `--ext-diff` and `--textconv` (`log`, `diff`, `show`) only enable a program named by `diff.external` or a textconv
  driver in git config, which nothing in this table can set (`git config` is not here); `--extcmd` is `git difftool`'s,
  which the ` *` spacing already excludes. Editors, pagers and gpg (`branch --edit-description`, `commit -e`,
  `commit -S`, `add -e`) run the program named by config or environment, never one named on the command line.
- `git status`, `git rev-parse`, `git add`, `git commit`, `git branch`: no option names a program.
- gh: no listed verb has an option that names a program; `--web` and `--editor` open the browser or editor named by
  `BROWSER`/`GH_EDITOR`/gh config, `gh pr checkout` (which runs git) is not in the table, and gh has no `-c`
  configuration injection. `gh issue` is read verbs only (`view`, `list`): a write verb with a ` *` tail would also
  admit `--repo other/x` (and `delete`, `transfer`), and no role writes issues — `alt backlog` reads them.
- `gh api`: **no rule at all** (the second review of PR #89 removed the merge-route rules this PR first carried).
  I-064's evidence shows the pulls API route was only ever an L2's *fallback* after `gh pr merge` was denied;
  `Bash(gh pr merge *)` is in the table and covers that need directly, so the API route buys nothing. And no
  prefix/wildcard rule shape can bound an API route safely: `*` matches anything including spaces, so even the
  exact-looking `gh api repos/<o>/<r>/pulls/*/merge` matched `gh api repos/o/r/pulls/1 -X PATCH -f state=closed
  -f body=/merge` — the route reconstructed inside a later argument — and closed a PR; and the owner/repo the rule
  interpolated came from an unanchored, unvalidated remote URL (`evil-github.com/a/b` parsed as a GitHub remote, and
  a permission metacharacter in owner/repo would have landed in the rule verbatim). With those rules gone there is
  no repository-specific rule in the table at all.

Residual risk (accepted, 2026-08-30): `Bash(gh pr merge *)` also matches `gh pr merge 1 --admin --repo other/project`.
Claude's Bash rules are prefix/wildcard shapes and cannot express "not `--admin`" or "this repository only", and
`gh pr merge` is the exact command I-064 requires. The allow rule removes the prompt; it does not authorise the act:
`--admin` and cross-repository merges stay forbidden by the never-list and R-013 (a merge under a hold is a breach),
and narrowing them belongs in `hooks/guard.py` (deny runs before allow), not in this table. One more of the same
shape, recorded so the next reader does not re-derive it: `--output=<file>` on `git log`/`git diff`/`git show` writes
the output to a file, and `--body-file <file>` on the gh verbs reads a local file and publishes it — neither runs a
program. A residual prompt is not silent either: `hooks/permission_prompt_fault.py` counts it and the tick raises it
as a system fault (decision 36).
"""
from __future__ import annotations
from pathlib import Path
from typing import Sequence

from . import config

# gh: the verbs the roles use, one rule each, never a bare `gh *`. `gh pr diff` (fresh review) and `gh pr comment`
# (review disposition) are role needs with no production call site today — intentional. `gh issue` is `view` and `list`
# only (module docstring: the write verbs have no call site and a wildcard tail would carry `--repo`). There is no
# `gh api` rule at all (module docstring: no rule shape bounds an API route; `gh pr merge` covers I-064's need).
_GH = ("gh pr view", "gh pr list", "gh pr diff", "gh pr checks", "gh pr create", "gh pr merge", "gh pr comment",
       "gh pr ready", "gh run view", "gh run list", "gh run watch", "gh issue view", "gh issue list")

# git fetch: exact forms, no wildcard — a tail would carry `--upload-pack=<program>` (module docstring, option sweep).
# These are the forms a worktree session needs before `git rebase origin/main`, `git reset --hard origin/main` and
# `git diff --stat origin/main <head>`; `alt land` and the server fetch on their own.
_GIT_FETCH = ("git fetch", "git fetch origin", "git fetch origin main", "git fetch --all")

# git: bounded read plus the write forms a worktree session legitimately needs, every verb followed by ` *` (module
# docstring: the space keeps `git diff` from admitting `git difftool`, `git log` from `git logtool`, `git show` from
# `git show-branch`, `git commit` from `git commit-tree`). There is deliberately no push rule: the matcher turns
# `git push origin *` into `^git push origin( .*)?$`, which admits `git push origin --force main`, and no rule shape
# says "a push that is neither forced nor to main". Pushes stay with the classifier and the never-list
# (`hooks/guard.py`); the landing push runs inside `alt land`, which the `alt` rules cover.
_GIT = ("git status *", "git log *", "git diff *", "git show *", *_GIT_FETCH, "git branch *", "git rev-parse *",
        "git add *", "git commit *", "git rebase origin/main", "git reset --hard origin/main")


def allow_rules(repo: Path | None = None) -> list[str]:
    """The `Bash(...)` rule strings for a session running in `repo` (default: this checkout), in table order: alt (both
    invocation forms), gh, git. No rule currently varies by checkout, so the result is the same for every `repo`; the
    parameter stays because it is the shape any future repository-specific rule needs (module docstring)."""
    rules = ["alt *", f"{config.REPO / 'bin' / 'alt'} *"]   # `alt` is on PATH in every nested launch (engines.clean_env)
    rules += [f"{verb} *" for verb in _GH]
    rules += _GIT
    return [f"Bash({r})" for r in rules]


def permissions_block(repo: Path | None = None) -> dict:
    """The `permissions` object merged into a settings file: allow only (module docstring)."""
    return {"allow": allow_rules(repo)}


def allowed_tools(extra: Sequence[str] = (), repo: Path | None = None) -> str:
    """The comma-joined `--allowedTools` value: the non-Bash tools a role needs, then every rule from `allow_rules(repo)`."""
    return ",".join([*extra, *allow_rules(repo)])


def prompt_fault_hooks() -> dict:
    """The passive permission-prompt hook (`hooks/permission_prompt_fault.py`, I-064) as the settings `hooks` entries
    every Claude launch carries — one definition for `engines.claude_settings` and `dispatch.session_settings`, so the
    two files cannot drift: a `PreToolUse(Bash)` capture, a `PostToolUse(Bash)` release, and the `Notification` for a
    prompt nobody can answer (Claude Code 2.1.251 matches it on `notification_type`). Capture and count only, never a
    decision; the envelope hooks (`subagent_cap.py`, `guard.py`, `edit_count.py`) stay the L2's alone."""
    hook = {"type": "command", "command": f"python3 {config.HOOKS / 'permission_prompt_fault.py'}", "timeout": 10}
    return {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [hook]}],
            "Notification": [{"matcher": "permission_prompt|worker_permission_prompt", "hooks": [hook]}]}
