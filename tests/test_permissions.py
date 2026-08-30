"""I-064: every Claude session Altitude launches carries one permission allowlist, rendered by `altitude.permissions`
for both consumers — the settings `permissions.allow` block (`engines.claude_settings(repo)`, `dispatch.session_settings`)
and the L3's `--allowedTools` string — for the checkout the session runs in, plus the passive permission-prompt hook
from the same module. The assertions run against the settings files actually produced, against two throwaway checkouts
with distinct GitHub origins, and against sentinels that must surface in every consumer (the structural proof that
nothing is hand-copied).

`rule_matches` is a port of Claude Code 2.1.251's Bash rule matcher (read from the binary on 2026-08-30): a rule ending
in `:*` is a legacy prefix; a rule with a bare `*` is a wildcard where `*` matches anything (spaces included) and a lone
trailing ` *` also admits the bare command; anything else is an exact match. It is what makes the negative cases below
evidence rather than string trivia — in particular it shows why the table carries no push rule, why every verb rule
ends in ` *` with the space (`git diff*` admitted `git difftool --extcmd=…`), why `git fetch` is exact forms
(`git fetch *` admitted `--upload-pack=<program>`), and why there is no `gh api` rule at all (a middle `*` spans
spaces, so the "exact" merge route matched a PR-closing PATCH that reconstructed the route in a later argument)."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix="altitude-perm-"))
os.environ["ALTITUDE_HOME"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from altitude import config, dispatch, engines, l3, permissions, state as S, tasks as T  # noqa: E402

REPO = str(config.REPO)
DOCS = config.REPO / "docs"


def make_checkout(name: str, origin: str) -> Path:
    """A throwaway git checkout with its own GitHub origin — what a managed project looks like to the renderer."""
    d = _TMP / "checkouts" / name
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin", origin], check=True)
    return d


WIDGETS = make_checkout("widgets", "git@github.com:acme/widgets.git")
GIZMOS = make_checkout("gizmos", "https://github.com/beta/gizmos.git")


def register_projects() -> None:
    config.ensure_root()
    config.save_projects({"widgets": {"name": "widgets", "path": str(WIDGETS), "stacks": ["python"]},
                          "gizmos": {"name": "gizmos", "path": str(GIZMOS), "stacks": ["python"]}})


_SPECIAL = re.compile(r"[.+?^${}()|\[\]\\'\"]")


def rule_matches(rule: str, command: str) -> bool:
    """Claude Code 2.1.251's matcher for one `Bash(...)` rule against one command."""
    assert rule.startswith("Bash(") and rule.endswith(")"), rule
    content = rule[5:-1]
    if content.endswith(":*"):
        return command.startswith(content[:-2])
    if "*" not in content:
        return command == content
    pattern = _SPECIAL.sub(lambda m: "\\" + m.group(0), content).replace("*", ".*")
    if pattern.endswith(" .*") and content.count("*") == 1:
        pattern = pattern[:-3] + "( .*)?"
    return re.fullmatch(pattern, command, re.S) is not None


def matching(rules, command: str) -> list[str]:
    return [r for r in rules if rule_matches(r, command)]


# ---- the table, as the brief lists it ------------------------------------------------------------------------------
ALT_RULES = ["Bash(alt *)", f"Bash({REPO}/bin/alt *)"]
GH_VERBS = ("gh pr view", "gh pr list", "gh pr diff", "gh pr checks", "gh pr create", "gh pr merge", "gh pr comment",
            "gh pr ready", "gh run view", "gh run list", "gh run watch", "gh issue view", "gh issue list")
GH_RULES = [f"Bash({v} *)" for v in GH_VERBS]
FETCH_FORMS = ("git fetch", "git fetch origin", "git fetch origin main", "git fetch --all")   # F1: exact, never a wildcard
GIT_RULES = [f"Bash(git {v} *)" for v in ("status", "log", "diff", "show")]
GIT_RULES += [f"Bash({f})" for f in FETCH_FORMS]
GIT_RULES += [f"Bash(git {v} *)" for v in ("branch", "rev-parse", "add", "commit")]
GIT_RULES += ["Bash(git rebase origin/main)", "Bash(git reset --hard origin/main)"]

# ---- the exact commands I-064 recorded as denied, plus the everyday role forms -------------------------------------
# (I-064's `gh api …/pulls/…/merge` denial is deliberately NOT here: it was only the L2's fallback after `gh pr merge`
# was denied, and `gh pr merge` below covers that need — C1/C2, the second review of PR #89.)
COVERED = [
    "alt task hold-merge apply-r-010-i-030 --off",
    "alt incident amend I-055",
    f"{REPO}/bin/alt task status foo",
    "gh pr merge 56 --squash --delete-branch",
    "git reset --hard origin/main",
    "alt", "alt land --message \"x\"",
    "gh pr view", "gh pr view 56 --json state,mergeable", "gh pr checks 56 --watch", "gh pr create --fill",
    "gh run watch 12345 --exit-status", "gh run list --limit 5", "gh issue list", "gh issue view 12", "gh issue view 12 --comments",
    "git status", "git status --short", "git log", "git log --oneline -5", "git diff", "git diff --stat origin/main HEAD",
    "git show HEAD --stat", "git fetch", "git fetch origin", "git fetch origin main", "git fetch --all",
    "git branch --show-current", "git rev-parse --abbrev-ref HEAD",
    "git add altitude/permissions.py", "git commit -m msg", "git rebase origin/main",
]

# ---- never-list and out-of-table commands: no rule may admit any of these -------------------------------------------
PRIVILEGE_ESCALATION = ["sudo systemctl restart altitude", "sudo -i", "sudo rm -rf /tmp/x", "doas sh", "su -"]
SERVICE_UNITS = ["systemctl --user restart altitude", "systemctl stop tutor", "systemctl --user daemon-reload"]
RECURSIVE_REMOVE = ["rm -rf /tmp/x", f"rm -r {REPO}", "rm -rf ~/.altitude"]
FORCE_PUSH = ["git push --force origin feature", "git push origin --force feature", "git push -f origin feature",
              "git push --force-with-lease origin feature", "git push -u origin feature --force-with-lease",
              "git push origin feature --force"]
PUSH_TO_MAIN = ["git push origin main", "git push -u origin main", "git push origin HEAD:main", "git push origin feature:main"]
# C1/C2 (the second review of PR #89): no `gh api` rule exists, so nothing under `gh api` may match — not the merge
# route this PR first carried, not any other route, of any repository. `gh pr merge` is the covered form.
GH_API = ["gh api repos/acme/widgets/pulls/56/merge", "gh api repos/acme/widgets/pulls/56/merge -X PUT -f merge_method=squash",
          "gh api repos/acme/widgets/pulls/56/merge --method PUT", "gh api repos/acme/widgets/pulls",
          "gh api repos/acme/widgets/pulls/1 --method PATCH -f title=x", "gh api repos/acme/widgets/pulls/comments/5 -X DELETE",
          "gh api repos/acme/widgets/pulls/1/reviews/2 -X DELETE", "gh api repos/acme/widgets/pulls/1/merge/x",
          "gh api repos/someone-else/other/pulls/1", "gh api repos/someone-else/other/pulls/1/merge",
          "gh api -X PUT repos/someone-else/other/pulls/1/merge", "gh api user", "gh api graphql -f query=x"]
# the C1 construction: the route rule's middle `*` spanned spaces, so a PR-closing PATCH whose *argument* ends in
# `/merge` matched the "exact" route `gh api repos/o/r/pulls/*/merge` — with or without further arguments after it
MERGE_IN_ARGUMENT = ["gh api repos/acme/widgets/pulls/1 -X PATCH -f state=closed -f body=/merge",
                     "gh api repos/acme/widgets/pulls/1 -X PATCH -f state=closed -f body=/merge -f x=y",
                     "gh api repos/someone-else/other/pulls/1 -X PATCH -f state=closed -f body=/merge"]
OUT_OF_TABLE = ["gh repo delete x", "gh auth logout", "gh api repos/x/y/git/refs/heads/main", "git reset --hard HEAD~3",
                "git rebase -i origin/main", "git checkout main", "git clean -fdx", "altd", "kill -9 1", "echo alt task status"]
# plain pushes are not in the table either: the landing push runs inside `alt land`, the rest stays with the classifier
PLAIN_PUSH = ["git push -u origin feature", "git push origin feature"]
# A2: neighbouring subcommands — a verb rule must never bleed into them. `git difftool --extcmd` runs an arbitrary command;
# the rest are real git/gh subcommands (plumbing or otherwise) whose names extend a listed verb.
NEIGHBOURS = [
    "git difftool --extcmd=touch /tmp/pwned", "git difftool", "git difftool -y -x 'sh -c id' HEAD~1",
    "git logtool", "git log-x",
    "git show-branch", "git show-ref", "git show-index",
    "git status-x", "git statusx",
    "git fetch-pack origin", "git fetchx",
    "git branch-x", "git branchx",
    "git rev-parse-x",
    "git addremove", "git add-x",
    "git commit-tree HEAD^{tree}", "git commit-graph write", "git commitx",
    "git diff-index HEAD", "git diff-tree HEAD", "git diff-files",
    "git rebase origin/main2", "git rebase origin/maintenance", "git reset --hard origin/main2", "git reset --hard origin/mainline",
    "gh pr viewer 1", "gh pr listing", "gh pr merged 1", "gh pr diffs", "gh pr checksum", "gh pr created", "gh pr readyx",
    "gh pr commentary", "gh run viewer", "gh run listing", "gh run watcher", "gh issues list",
    "altx", "alt-x", "altitude", f"{REPO}/bin/altx", f"{REPO}/bin/alt-x",
]
# F1: options that name a program to run (or inject configuration), and the forms that reach one through a listed verb —
# the option sweep in the module docstring. `git fetch --upload-pack=<program>` is the one the old `git fetch *` admitted.
PROGRAM_OPTIONS = [
    "git fetch --upload-pack=/tmp/pwned", "git fetch --upload-pack /tmp/pwned origin", "git fetch origin --upload-pack=touch",
    "git fetch origin main --upload-pack=/tmp/pwned", "git fetch -u /tmp/pwned origin",
    "git fetch --receive-pack=/tmp/pwned origin", "git fetch --exec=/tmp/pwned origin",
    "git fetch ext::sh -c id", "git fetch evil::x", "git fetch https://evil.example/x.git", "git fetch origin main:main",
    "git push --receive-pack=/tmp/pwned origin feature", "git push --exec=/tmp/pwned origin feature", "git pull --upload-pack=/tmp/pwned origin",
    "git rebase --exec=touch origin/main", "git rebase -x touch origin/main", "git rebase origin/main --exec touch",
    "git -c core.pager=touch status", "git -c diff.external=touch diff --ext-diff", "git -c core.editor=touch commit",
    "git --exec-path=/tmp/pwned status", "git --git-dir=/tmp/x log", "GIT_EXTERNAL_DIFF=touch git diff --ext-diff",
    "git difftool --extcmd=touch", "git difftool -x touch", "git config diff.external touch",
]
# F3: `gh issue` writes — destructive, cross-repository, or simply not a role's — stay with the classifier
ISSUE_WRITES = [
    "gh issue delete 123 --yes", "gh issue transfer 123 other/repo",
    "gh issue edit 123 --repo other/repo --title x", "gh issue close 123 --repo other/repo",
    "gh issue comment 123 --repo other/repo --body x", "gh issue create --repo other/repo --title x --body y",
    "gh issue edit 123 --title x", "gh issue close 123", "gh issue comment 123 --body x", "gh issue create --title x",
    "gh issue reopen 123", "gh issue pin 123", "gh issue lock 123", "gh issue develop 123", "gh issue viewer 1", "gh issue listing",
]


class TestMatcherPort(unittest.TestCase):
    def test_semantics_read_from_the_binary(self):
        self.assertTrue(rule_matches("Bash(alt *)", "alt"))
        self.assertTrue(rule_matches("Bash(alt *)", "alt task status x"))
        self.assertFalse(rule_matches("Bash(alt *)", "altd"))
        self.assertTrue(rule_matches("Bash(git log *)", "git log --oneline"))
        self.assertTrue(rule_matches("Bash(git log *)", "git log"))
        self.assertFalse(rule_matches("Bash(git log *)", "git logtool"))
        self.assertFalse(rule_matches("Bash(git rebase origin/main)", "git rebase -i origin/main"))
        self.assertTrue(rule_matches("Bash(npm run:*)", "npm run test"))
        # the reason the table has no push rule: a trailing wildcard spans flags and refspecs alike
        self.assertTrue(rule_matches("Bash(git push origin *)", "git push origin --force main"))
        self.assertTrue(rule_matches("Bash(git push -u origin *)", "git push -u origin HEAD:main"))
        # A2: the space-less form bled into a neighbouring subcommand that runs arbitrary commands; the spaced form does not
        self.assertTrue(rule_matches("Bash(git diff*)", "git difftool --extcmd=touch /tmp/pwned"))
        self.assertFalse(rule_matches("Bash(git diff *)", "git difftool --extcmd=touch /tmp/pwned"))
        self.assertTrue(rule_matches("Bash(git diff *)", "git diff"))
        self.assertTrue(rule_matches("Bash(git diff *)", "git diff --stat"))
        # F1/F5: a wildcard tail carries every option of the verb, and a wildcard in the middle matches spaces too
        self.assertTrue(rule_matches("Bash(git fetch *)", "git fetch --upload-pack=/tmp/pwned origin"))
        self.assertTrue(rule_matches("Bash(gh api repos/o/r/pulls*)", "gh api repos/o/r/pulls/1 -X PATCH -f state=closed"))
        # C1: the middle `*` spans spaces, so even the "exact" merge route this PR first carried matched a PR-closing
        # PATCH that reconstructs the route inside a later argument — why the table now has no `gh api` rule at all
        self.assertTrue(rule_matches("Bash(gh api repos/o/r/pulls/*/merge)",
                                     "gh api repos/o/r/pulls/1 -X PATCH -f state=closed -f body=/merge"))
        self.assertTrue(rule_matches("Bash(gh api repos/o/r/pulls/*/merge *)",
                                     "gh api repos/o/r/pulls/1 -X PATCH -f state=closed -f body=/merge -f x=y"))


class TestTable(unittest.TestCase):
    def test_every_listed_form_is_a_rule_in_table_order(self):
        expected = ALT_RULES + GH_RULES + GIT_RULES
        self.assertEqual(permissions.allow_rules(), expected)

    def test_i064_commands_are_covered(self):
        rules = permissions.allow_rules()
        for cmd in COVERED:
            self.assertTrue(matching(rules, cmd), f"{cmd!r} is covered by no rule")

    def test_never_list_and_out_of_table_commands_match_no_rule(self):
        rules = permissions.allow_rules()
        for group in (PRIVILEGE_ESCALATION, SERVICE_UNITS, RECURSIVE_REMOVE, FORCE_PUSH, PUSH_TO_MAIN,
                      GH_API, MERGE_IN_ARGUMENT, OUT_OF_TABLE, PLAIN_PUSH, NEIGHBOURS, PROGRAM_OPTIONS, ISSUE_WRITES):
            for cmd in group:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

    def test_neighbouring_subcommands_match_no_rule(self):
        """A2 on its own: `git difftool --extcmd=…`, `git logtool`, `git show-branch`, `git commit-tree`, `gh pr viewer`, …"""
        for repo in (None, WIDGETS):
            rules = permissions.allow_rules(repo)
            for cmd in NEIGHBOURS:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

    def test_program_naming_options_match_no_rule(self):
        """F1: `git fetch --upload-pack=<program>` ran that program through the old `git fetch *` — the same hole the ` *`
        spacing closed for `git difftool`, reopened through an option. `git fetch` is exact forms; the sweep of
        `--upload-pack`, `--receive-pack`, `--exec`, `--extcmd`, `--ext-diff` and `-c <key>=<value>` over every rule is
        recorded in the module docstring."""
        for repo in (None, WIDGETS):
            rules = permissions.allow_rules(repo)
            for cmd in PROGRAM_OPTIONS:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")
            for cmd in FETCH_FORMS:
                self.assertEqual(matching(rules, cmd), [f"Bash({cmd})"], cmd)       # each exact form is its own rule
            self.assertEqual([r for r in rules if "git fetch" in r], [f"Bash({f})" for f in FETCH_FORMS])
        src = Path(permissions.__file__).read_text()
        self.assertIn("Option sweep", src)
        for opt in ("--upload-pack", "--receive-pack", "--exec", "--extcmd", "--ext-diff", "-c <key>=<value>", "--body-file"):
            self.assertIn(opt, src, f"the option sweep does not record {opt}")

    def test_gh_issue_is_read_verbs_only(self):
        """F3: `gh issue *` admitted `gh issue delete 123 --yes` and `gh issue transfer 123 other/repo`; a write verb with a
        ` *` tail would still admit `--repo other/repo`, and no role writes issues (`alt backlog` reads them)."""
        self.assertTrue(rule_matches("Bash(gh issue *)", "gh issue delete 123 --yes"))          # the old shape's hole
        self.assertTrue(rule_matches("Bash(gh issue edit *)", "gh issue edit 123 --repo other/repo --title x"))
        for repo in (None, WIDGETS):
            rules = permissions.allow_rules(repo)
            for cmd in ISSUE_WRITES:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")
            for cmd in ("gh issue list", "gh issue list --state open --limit 60 --json number,title", "gh issue view 12",
                        "gh issue view 12 --comments"):
                self.assertTrue(matching(rules, cmd), cmd)
            self.assertEqual([r for r in rules if r.startswith("Bash(gh issue")], ["Bash(gh issue view *)", "Bash(gh issue list *)"])

    def test_no_rule_matches_gh_api_at_all(self):
        """C1/C2 (the second review of PR #89): the merge-route rules and the remote parsing behind them are gone. The
        old `pulls*` shape covered the whole namespace whenever the method flag came after the route, and even the
        "exact" `pulls/*/merge` matched a PR-closing PATCH (the middle `*` spans spaces, so the route can be
        reconstructed inside a later argument); the unanchored remote regex behind it took `evil-github.com/a/b` for a
        GitHub remote. No rule shape bounds an API route, so no `gh api` command may match any rule, ever."""
        old = "Bash(gh api repos/acme/widgets/pulls*)"
        self.assertTrue(rule_matches(old, "gh api repos/acme/widgets/pulls/1 -X PATCH -f state=closed"))
        self.assertTrue(rule_matches(old, "gh api repos/acme/widgets/pulls/comments/5 -X DELETE"))
        for repo in (None, WIDGETS, GIZMOS, config.REPO):
            rules = permissions.allow_rules(repo)
            self.assertEqual([r for r in rules if "gh api" in r], [], repo)
            for cmd in GH_API:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")
        for name in ("github_repo", "merge_route_rules", "_remote_url", "_GITHUB_REMOTE"):
            self.assertFalse(hasattr(permissions, name), f"{name} must be gone with the rules it served")

    def test_merge_in_a_post_route_argument_matches_nothing(self):
        """C1's construction on the live table: a command that spells `/merge` inside a post-route argument (the shape
        that matched the removed route rules and closed a PR) matches no rule."""
        for repo in (None, WIDGETS):
            rules = permissions.allow_rules(repo)
            for cmd in MERGE_IN_ARGUMENT:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

    def test_every_verb_rule_has_the_space_before_its_wildcard(self):
        """The whole table: a wildcard verb rule is `<verb> *` — with the merge route gone there is no exception left."""
        for rule in permissions.allow_rules(WIDGETS):
            c = rule[len("Bash("):-1]
            if "*" not in c:
                continue
            self.assertTrue(c.endswith(" *") and c.count("*") == 1, c)

    def test_table_shape(self):
        contents = [r[len("Bash("):-1] for r in permissions.allow_rules()]
        for c in contents:
            self.assertNotRegex(c, r"^(sudo|doas|su|systemctl|rm|kill)\b", c)
            self.assertFalse(c.startswith("git push"), c)
            self.assertNotIn("--force", c)
            self.assertNotIn(":*", c)
        self.assertNotIn("gh *", contents)
        self.assertNotIn("git *", contents)
        self.assertNotIn("gh issue *", contents)
        self.assertNotIn("git fetch *", contents)
        self.assertEqual([c for c in contents if c.startswith("gh api")], [])

    def test_no_absolute_home_path_in_the_module(self):
        src = Path(permissions.__file__).read_text()
        self.assertNotIn("/home/", src)
        self.assertIn(f"Bash({config.REPO}/bin/alt *)", permissions.allow_rules())

    def test_residual_risk_is_recorded_not_fixed(self):
        """A4: `gh pr merge *` admits `--admin` and `--repo other/x`; the module says so and leaves it to guard.py. The
        same for the file-writing and file-publishing options: recorded, not silently admitted. The removal of the
        `gh api` rules is recorded too, so the next reader does not re-add "just the merge route"."""
        rules = permissions.allow_rules()
        self.assertTrue(matching(rules, "gh pr merge 1 --admin --repo other/project"))
        src = Path(permissions.__file__).read_text()
        self.assertIn("Residual risk", src)
        self.assertIn("--output=<file>", src)
        self.assertIn("fallback", src)                                   # the gh api rationale: gh pr merge covers I-064
        self.assertIn("no repository-specific rule in the table", src)
        self.assertEqual(set(permissions.permissions_block()), {"allow"})

    def test_block_and_allowed_tools_share_one_source(self):
        grown = permissions.allow_rules() + ["Bash(echo grown *)"]
        with mock.patch.object(permissions, "allow_rules", return_value=grown):
            self.assertEqual(permissions.permissions_block(), {"allow": grown})
            self.assertEqual(permissions.allowed_tools(extra=("Read", "Agent")).split(","), ["Read", "Agent", *grown])
        self.assertEqual(permissions.allowed_tools().split(","), permissions.allow_rules())


class TestPerCheckout(unittest.TestCase):
    """A1, amended by C1/C2: the renderer still takes the checkout (the per-repo settings files and the threading are
    the shape a future repository-specific rule needs), but with the merge route gone no rule varies by checkout — the
    rendered allowlist is identical for every checkout, GitHub remote or none."""

    def test_every_checkout_renders_the_identical_table(self):
        w, g = permissions.allow_rules(WIDGETS), permissions.allow_rules(GIZMOS)
        self.assertEqual(w, g)
        self.assertEqual(w, permissions.allow_rules())                      # None: this checkout, the default
        self.assertEqual(w, permissions.allow_rules(config.REPO))           # and by path
        self.assertEqual(w, permissions.allow_rules(_TMP))                  # not even a checkout: same table
        for cmd in COVERED:
            self.assertTrue(matching(w, cmd), f"{cmd!r} is covered by no widgets rule")
        for cmd in GH_API + MERGE_IN_ARGUMENT:
            self.assertEqual(matching(g, cmd), [], f"{cmd!r} is admitted in gizmos")

    def test_settings_file_is_per_repository(self):
        pw, pg, pd = engines.claude_settings(WIDGETS), engines.claude_settings(GIZMOS), engines.claude_settings()
        self.assertEqual(len({pw, pg, pd}), 3)
        self.assertEqual(pd.name, "claude-settings.json")
        self.assertEqual(engines.claude_settings(config.REPO), pd)              # this checkout by path is the default file
        self.assertEqual(engines.claude_settings(Path(str(WIDGETS) + "/")), pw)  # a spelling of the same path is the same file
        for p in (pw, pg, pd):
            st = json.loads(p.read_text())
            self.assertEqual(st["permissions"]["allow"], permissions.allow_rules())   # one identical table per file (C1/C2)
            self.assertEqual(st["autoCompactWindow"], 300_000)
            self.assertEqual(set(st), {"autoCompactWindow", "permissions", "hooks", "env"})
        before, g_before = pw.stat().st_mtime_ns, pg.read_text()
        engines.claude_settings(WIDGETS)                                        # unchanged content: not rewritten
        self.assertEqual(pw.stat().st_mtime_ns, before)
        self.assertEqual(pg.read_text(), g_before)                              # and the other project's file is untouched

    def test_launchers_render_for_their_cwd(self):
        """`claude_print`, `claude_bg`, `claude_resume_bg` with no `settings` pass the file rendered for their `cwd` —
        which is how every L1, proposal, critic, sizer and L3 turn gets its own project's route without a caller change."""
        seen, real_run, real_popen = [], subprocess.run, subprocess.Popen

        class FakeProc:
            def __init__(self, cmd, **kw):
                seen.append(cmd); self.pid = 4242; self.returncode = 0
                self.stdin, self.stderr = io.StringIO(), io.StringIO("")
                self.stdout = io.StringIO('{"type":"result","result":"ok","session_id":"s"}\n')

            def wait(self): return 0

            def kill(self): pass

        def fake_popen(cmd, **kw):                                              # only `claude` is stubbed; git stays real,
            return FakeProc(cmd, **kw) if cmd and cmd[0] == config.CLAUDE_BIN else real_popen(cmd, **kw)   # the route must render

        def fake_run(cmd, **kw):
            if cmd and cmd[0] == config.CLAUDE_BIN:
                seen.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return real_run(cmd, **kw)

        with mock.patch.object(engines.subprocess, "Popen", fake_popen), mock.patch.object(engines.subprocess, "run", fake_run), \
                mock.patch.object(engines, "find_agent", return_value=None):
            engines.claude_print("hi", cwd=WIDGETS)
            engines.claude_bg("n", "hi", cwd=GIZMOS)
            engines.claude_resume_bg("n", "sid", "hi", cwd=WIDGETS)
            engines.claude_print("hi", cwd=WIDGETS, settings=Path("/tmp/explicit.json"))
        self.assertEqual(len(seen), 4)
        want = [engines.claude_settings(WIDGETS), engines.claude_settings(GIZMOS), engines.claude_settings(WIDGETS), Path("/tmp/explicit.json")]
        for cmd, p in zip(seen, want):
            self.assertEqual(Path(cmd[cmd.index("--settings") + 1]), p)
        for p in want[:2]:
            self.assertEqual(json.loads(p.read_text())["permissions"]["allow"], permissions.allow_rules())


class TestConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_projects()
        T.new("widgets", "perm-task", "S", "req")
        T.new("gizmos", "perm-task", "S", "req")
        cls.global_settings = json.loads(engines.claude_settings().read_text())
        cls.widgets_settings = json.loads(engines.claude_settings(WIDGETS).read_text())
        cls.l2_settings = json.loads(dispatch.session_settings("widgets", "perm-task", "key").read_text())
        cls.l2_gizmos = json.loads(dispatch.session_settings("gizmos", "perm-task", "key").read_text())

    def test_both_settings_files_carry_the_same_allowlist(self):
        rules = permissions.allow_rules(WIDGETS)
        self.assertEqual(self.widgets_settings["permissions"], {"allow": rules})
        self.assertEqual(self.l2_settings["permissions"], {"allow": rules})
        self.assertEqual(self.global_settings["permissions"], {"allow": permissions.allow_rules()})
        self.assertEqual(self.widgets_settings["autoCompactWindow"], 300_000)
        self.assertEqual(self.l2_settings["autoCompactWindow"], 300_000)
        self.assertIn("hooks", self.l2_settings)                       # the L2 file keeps its hooks and env untouched
        self.assertEqual(self.l2_settings["env"]["ALTITUDE_ACTOR"], "l2")
        self.assertEqual(set(self.global_settings), {"autoCompactWindow", "permissions", "hooks", "env"})

    def test_l2_files_carry_the_identical_table_and_no_gh_api(self):
        self.assertEqual(self.l2_settings["permissions"]["allow"], self.l2_gizmos["permissions"]["allow"])   # C1/C2
        for st in (self.l2_settings, self.l2_gizmos):
            self.assertEqual([r for r in st["permissions"]["allow"] if "gh api" in r], [])

    def test_l2_file_wires_the_prompt_fault_hook_beside_the_existing_ones(self):
        hooks = self.l2_settings["hooks"]
        notif = hooks["Notification"]
        self.assertEqual(len(notif), 1)
        self.assertIn("permission_prompt", notif[0]["matcher"])
        self.assertTrue(notif[0]["hooks"][0]["command"].endswith("permission_prompt_fault.py"))
        bash_cmds = [h["command"] for e in hooks["PreToolUse"] if e["matcher"] == "Bash" for h in e["hooks"]]
        self.assertTrue(any(c.endswith("guard.py") for c in bash_cmds))                      # the guard is still there
        self.assertTrue(any(c.endswith("permission_prompt_fault.py") for c in bash_cmds))    # the capture rides beside it
        self.assertTrue(any(e["matcher"] == "Agent|Task|Bash" and e["hooks"][0]["command"].endswith("subagent_cap.py")
                            for e in hooks["PreToolUse"]))                                   # and the cap
        self.assertTrue(hooks["PostToolUse"][0]["hooks"][0]["command"].endswith("edit_count.py"))
        post_cmds = [h["command"] for e in hooks["PostToolUse"] if e["matcher"] == "Bash" for h in e["hooks"]]
        self.assertTrue(any(c.endswith("permission_prompt_fault.py") for c in post_cmds))    # and the release (F6)

    def test_global_file_wires_only_the_passive_hook(self):
        """F2: every launch without a per-dispatch file — the L3 (where I-064 was observed), L1s, proposals, critic, sizer —
        gets the same passive hook from the same definition as the L2 file, with the `ALTITUDE_HOME` it needs to land its
        line where altd drains; never the envelope hooks."""
        pf = permissions.prompt_fault_hooks()
        self.assertEqual(set(pf), {"PreToolUse", "PostToolUse", "Notification"})
        self.assertEqual([e["matcher"] for e in pf["PreToolUse"] + pf["PostToolUse"]], ["Bash", "Bash"])
        self.assertEqual(pf["Notification"][0]["matcher"], "permission_prompt|worker_permission_prompt")
        for st in (self.global_settings, self.widgets_settings):
            self.assertEqual(st["hooks"], pf)
            self.assertEqual(st["env"], {"ALTITUDE_HOME": str(config.ROOT)})
            cmds = [h["command"] for entries in st["hooks"].values() for e in entries for h in e["hooks"]]
            self.assertEqual(len(cmds), 3)
            self.assertTrue(all(c.endswith("permission_prompt_fault.py") for c in cmds), cmds)
            for forbidden in ("subagent_cap.py", "guard.py", "edit_count.py"):
                self.assertFalse(any(forbidden in c for c in cmds), forbidden)
        l2 = self.l2_settings["hooks"]                                # the L2 file carries exactly the same entries, beside its own
        self.assertEqual(l2["Notification"], pf["Notification"])
        for event in ("PreToolUse", "PostToolUse"):
            for entry in pf[event]:
                self.assertIn(entry, l2[event])

    def test_allow_only_no_deny_block(self):
        for st in (self.global_settings, self.widgets_settings, self.l2_settings, self.l2_gizmos):
            self.assertEqual(set(st["permissions"]), {"allow"})

    def test_l3_allowed_tools_is_the_same_table_for_the_project(self):
        parts = l3.allowed_tools("widgets").split(",")
        self.assertEqual(parts[:4], ["Read", "Grep", "Glob", "Agent"])
        self.assertEqual(parts[4:], self.l2_settings["permissions"]["allow"])
        self.assertEqual(l3.allowed_tools("gizmos").split(",")[4:], self.l2_gizmos["permissions"]["allow"])
        self.assertFalse(hasattr(l3, "ALLOWED_TOOLS"))                 # never an import-time constant again (A1)

    def test_i064_commands_are_covered_by_both_files(self):
        for st in (self.widgets_settings, self.l2_settings):
            for cmd in COVERED:
                self.assertTrue(matching(st["permissions"]["allow"], cmd), f"{cmd!r} is covered by no rule")
            for cmd in (FORCE_PUSH + PUSH_TO_MAIN + PRIVILEGE_ESCALATION + SERVICE_UNITS + RECURSIVE_REMOVE
                        + NEIGHBOURS + PROGRAM_OPTIONS + ISSUE_WRITES + GH_API + MERGE_IN_ARGUMENT):
                self.assertEqual(matching(st["permissions"]["allow"], cmd), [], f"{cmd!r} is admitted")

    def test_global_settings_rewritten_when_the_block_changes(self):
        p = engines.claude_settings()
        p.write_text(json.dumps({"autoCompactWindow": 300_000}) + "\n")   # a pre-I-064 file on disk
        engines.claude_settings()
        self.assertEqual(json.loads(p.read_text())["permissions"], permissions.permissions_block())
        grown = permissions.allow_rules() + ["Bash(echo grown *)"]
        with mock.patch.object(permissions, "allow_rules", return_value=grown):
            engines.claude_settings()
        self.assertEqual(json.loads(p.read_text())["permissions"]["allow"], grown)
        engines.claude_settings()
        self.assertEqual(json.loads(p.read_text()), {"autoCompactWindow": 300_000, "permissions": permissions.permissions_block(),
                                                     "hooks": permissions.prompt_fault_hooks(), "env": {"ALTITUDE_HOME": str(config.ROOT)}})

    def test_resume_re_renders_the_settings_file(self):
        """F4: a task dispatched before I-064 carried a per-dispatch file with no allowlist and no prompt hook; a resume
        must render the file again from current code — under the key the fresh dispatch used — not reuse it by path."""
        t = T.new("widgets", "stale resume", "S", "req")
        slug = t["slug"]
        wt = _TMP / "worktrees" / slug
        wt.mkdir(parents=True, exist_ok=True)
        t.update({"state": "blocked", "dispatch_id": f"{slug}-1", "session_id": "old-sid", "agent_id": "a1", "worktree": str(wt)})
        S.save_task("widgets", t)
        p = S.task_dir("widgets", slug) / "settings.json"
        S.write_json(p, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 guard.py", "timeout": 10}]}]},
                         "env": {"ALTITUDE_SESSION_KEY": f"widgets--{slug}-1"}, "autoCompactWindow": 300_000})   # the pre-I-064 file
        seen = {}

        def fake_resume(name, sid, text, **kw):
            seen.update(kw, name=name, sid=sid, on_disk=json.loads(Path(kw["settings"]).read_text()))   # as the launch reads it
            return {"returncode": 0, "stdout": "", "stderr": ""}

        live = [{"name": f"widgets/{slug}-1", "id": "a2", "sessionId": "new-sid", "state": "running", "startedAt": 2}]
        with mock.patch.object(engines, "claude_resume_bg", side_effect=fake_resume), \
                mock.patch.object(engines, "claude_agents", return_value=live), \
                mock.patch.object(dispatch.git_policy, "fetch_and_require_exact_base", return_value="a" * 40), \
                mock.patch.object(dispatch, "_validate_task_worktree"):
            dispatch.resume_session("widgets", slug, "carry on")
        self.assertEqual(Path(seen["settings"]), p)
        st = seen["on_disk"]
        self.assertEqual(st["permissions"], {"allow": permissions.allow_rules(WIDGETS)})
        self.assertEqual(st["hooks"]["Notification"], permissions.prompt_fault_hooks()["Notification"])
        self.assertTrue(any(h["command"].endswith("guard.py") for e in st["hooks"]["PreToolUse"] for h in e["hooks"]))
        self.assertEqual(st["env"]["ALTITUDE_SESSION_KEY"], f"widgets--{slug}-1")
        self.assertEqual(st["env"]["ALTITUDE_SESSION_KEY"], seen["extra_env"]["ALTITUDE_SESSION_KEY"])   # the fresh dispatch's key
        self.assertEqual((st["env"]["ALTITUDE_TASK"], st["env"]["ALTITUDE_ACTOR"]), (slug, "l2"))
        self.assertEqual(S.load_task("widgets", slug)["session_id"], "new-sid")


class TestOneSource(unittest.TestCase):
    """A3: a sentinel table must surface in every consumer — this fails the moment any one of them stops going through
    `permissions.allow_rules()`: the default settings file, a per-repo settings file, the L2's per-dispatch file, and the
    `allowed_tools=` value the L3 turn actually passes to `claude_print`. The hook wiring gets the same proof (F2)."""
    SENTINEL = ["Bash(sentinel-one *)", "Bash(sentinel-two *)"]

    @classmethod
    def setUpClass(cls):
        register_projects()
        T.new("widgets", "sentinel-task", "S", "req")

    def test_sentinel_reaches_every_consumer(self):
        calls = []

        def fake_print(prompt, **kw):
            calls.append(kw)
            return {"text": "ok", "session_id": "sid-1", "usage": {}, "context_tokens": 10, "cost": 0.0, "turns": 1,
                    "structured": None, "error": None, "tools": []}

        with mock.patch.object(permissions, "allow_rules", return_value=list(self.SENTINEL)) as ar, \
                mock.patch.object(engines, "claude_print", side_effect=fake_print):
            self.assertEqual(json.loads(engines.claude_settings().read_text())["permissions"]["allow"], self.SENTINEL)
            self.assertEqual(json.loads(engines.claude_settings(WIDGETS).read_text())["permissions"]["allow"], self.SENTINEL)
            l2 = json.loads(dispatch.session_settings("widgets", "sentinel-task", "key").read_text())
            self.assertEqual(l2["permissions"]["allow"], self.SENTINEL)
            res = l3.turn("widgets", "hello", trigger="chat")
        self.assertTrue(ar.called)
        self.assertFalse(res.get("skipped")); self.assertIsNone(res.get("error"))
        self.assertEqual(len(calls), 1)
        parts = calls[0]["allowed_tools"].split(",")
        self.assertEqual(parts[:4], ["Read", "Grep", "Glob", "Agent"])
        self.assertEqual(parts[4:], self.SENTINEL)
        self.assertEqual(calls[0]["cwd"], WIDGETS)                        # the turn runs where the rules were rendered for
        # the patch gone, the real table is back everywhere and the files on disk are re-rendered
        for st in (json.loads(engines.claude_settings().read_text()), json.loads(engines.claude_settings(WIDGETS).read_text()),
                   json.loads(dispatch.session_settings("widgets", "sentinel-task", "key").read_text())):
            self.assertNotIn(self.SENTINEL[0], st["permissions"]["allow"])
        self.assertNotIn(self.SENTINEL[0], l3.allowed_tools("widgets"))
        self.assertIn("Bash(gh pr merge *)", l3.allowed_tools("widgets"))

    def test_hook_sentinel_reaches_both_settings_files(self):
        """F2: the hook wiring comes from `permissions.prompt_fault_hooks()` in both files, so the two cannot drift."""
        hook = {"type": "command", "command": "python3 /sentinel/permission_prompt_fault.py", "timeout": 10}
        sentinel = {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}], "PostToolUse": [{"matcher": "Bash", "hooks": [hook]}],
                    "Notification": [{"matcher": "permission_prompt|worker_permission_prompt", "hooks": [hook]}]}
        with mock.patch.object(permissions, "prompt_fault_hooks", return_value=sentinel) as pf:
            g = json.loads(engines.claude_settings().read_text())
            w = json.loads(engines.claude_settings(WIDGETS).read_text())
            l2 = json.loads(dispatch.session_settings("widgets", "sentinel-task", "key").read_text())
        self.assertTrue(pf.called)
        self.assertEqual(g["hooks"], sentinel)
        self.assertEqual(w["hooks"], sentinel)
        self.assertEqual(l2["hooks"]["Notification"], sentinel["Notification"])
        self.assertIn(sentinel["PreToolUse"][0], l2["hooks"]["PreToolUse"])
        self.assertIn(sentinel["PostToolUse"][0], l2["hooks"]["PostToolUse"])
        self.assertTrue(any(h["command"].endswith("guard.py") for e in l2["hooks"]["PreToolUse"] for h in e["hooks"]))
        # the patch gone, the real hook is back in both files
        self.assertEqual(json.loads(engines.claude_settings().read_text())["hooks"], permissions.prompt_fault_hooks())
        self.assertNotIn(hook, [h for e in json.loads(dispatch.session_settings("widgets", "sentinel-task", "key").read_text())["hooks"]["Notification"]
                                for h in e["hooks"]])


class TestDocs(unittest.TestCase):
    """F8/F9: the docs this PR promotes state only what the host shows, and carry no home path."""

    def test_building_blocks_names_the_codex_controls_0_151_0_has(self):
        text = (DOCS / "BUILDING-BLOCKS.md").read_text()
        self.assertNotIn("--full-auto", text)                                    # not a `codex exec` flag on this host
        for flag in ("--approve-for-me", "--dangerously-bypass-approvals-and-sandbox", "-s <", "--ignore-rules", "`.rules`",
                     "-c <key>=<value>"):
            self.assertIn(flag, text, flag)
        self.assertIn("no per-launch rules file", text)
        self.assertIn("deliberately unchanged", text)

    def test_promoted_incident_carries_no_home_path(self):
        text = (DOCS / "incidents" / "I-064.md").read_text()
        self.assertNotIn("/home/", text)
        self.assertIn("<repo>/bin/alt", text)
        self.assertEqual(text.count("amended: 2026-08-30 by"), 4)                # the amendment history is intact


if __name__ == "__main__":
    unittest.main()
