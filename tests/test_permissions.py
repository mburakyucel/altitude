"""I-064: every Claude session Altitude launches carries one permission allowlist, rendered by `altitude.permissions`
for both consumers — the settings `permissions.allow` block (`engines.claude_settings(repo)`, `dispatch.session_settings`)
and the L3's `--allowedTools` string — for the checkout the session runs in. The assertions run against the settings
files actually produced, against two throwaway checkouts with distinct GitHub origins, and against a sentinel table that
must surface in every consumer (the structural proof that nothing is hand-copied).

`rule_matches` is a port of Claude Code 2.1.251's Bash rule matcher (read from the binary on 2026-08-30): a rule ending
in `:*` is a legacy prefix; a rule with a bare `*` is a wildcard where `*` matches anything (spaces included) and a lone
trailing ` *` also admits the bare command; anything else is an exact match. It is what makes the negative cases below
evidence rather than string trivia — in particular it shows why the table carries no push rule, and why every verb rule
ends in ` *` with the space (`git diff*` admitted `git difftool --extcmd=…`)."""
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
from altitude import config, dispatch, engines, l3, permissions, tasks as T  # noqa: E402

REPO = str(config.REPO)
OWNER_REPO = permissions.github_repo()   # this checkout's origin; the route tests need a GitHub remote
OWN_ROUTE = f"Bash(gh api repos/{OWNER_REPO}/pulls*)"


def make_checkout(name: str, origin: str) -> Path:
    """A throwaway git checkout with its own GitHub origin — what a managed project looks like to the renderer."""
    d = _TMP / "checkouts" / name
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin", origin], check=True)
    return d


WIDGETS = make_checkout("widgets", "git@github.com:acme/widgets.git")
GIZMOS = make_checkout("gizmos", "https://github.com/beta/gizmos.git")
WIDGETS_ROUTE = "Bash(gh api repos/acme/widgets/pulls*)"
GIZMOS_ROUTE = "Bash(gh api repos/beta/gizmos/pulls*)"


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
            "gh pr ready", "gh run view", "gh run list", "gh run watch", "gh issue")
GH_RULES = [f"Bash({v} *)" for v in GH_VERBS]
GIT_RULES = [f"Bash(git {v} *)" for v in ("status", "log", "diff", "show", "fetch", "branch", "rev-parse", "add", "commit")]
GIT_RULES += ["Bash(git rebase origin/main)", "Bash(git reset --hard origin/main)"]

# ---- the exact commands I-064 recorded as denied, plus the everyday role forms -------------------------------------
COVERED_BASE = [
    "alt task hold-merge apply-r-010-i-030 --off",
    "alt incident amend I-055",
    f"{REPO}/bin/alt task status foo",
    "gh pr merge 56 --squash --delete-branch",
    "git reset --hard origin/main",
    "alt", "alt land --message \"x\"",
    "gh pr view", "gh pr view 56 --json state,mergeable", "gh pr checks 56 --watch", "gh pr create --fill",
    "gh run watch 12345 --exit-status", "gh run list --limit 5", "gh issue list",
    "git status", "git status --short", "git log", "git log --oneline -5", "git diff", "git diff --stat origin/main HEAD",
    "git show HEAD --stat", "git fetch origin", "git branch --show-current", "git rev-parse --abbrev-ref HEAD",
    "git add altitude/permissions.py", "git commit -m msg", "git rebase origin/main",
]


def covered(owner_repo: str) -> list[str]:
    return COVERED_BASE + [f"gh api repos/{owner_repo}/pulls/56/merge", f"gh api repos/{owner_repo}/pulls"]


COVERED = covered(OWNER_REPO)

# ---- never-list and out-of-table commands: no rule may admit any of these -------------------------------------------
PRIVILEGE_ESCALATION = ["sudo systemctl restart altitude", "sudo -i", "sudo rm -rf /tmp/x", "doas sh", "su -"]
SERVICE_UNITS = ["systemctl --user restart altitude", "systemctl stop tutor", "systemctl --user daemon-reload"]
RECURSIVE_REMOVE = ["rm -rf /tmp/x", f"rm -r {REPO}", "rm -rf ~/.altitude"]
FORCE_PUSH = ["git push --force origin feature", "git push origin --force feature", "git push -f origin feature",
              "git push --force-with-lease origin feature", "git push -u origin feature --force-with-lease",
              "git push origin feature --force"]
PUSH_TO_MAIN = ["git push origin main", "git push -u origin main", "git push origin HEAD:main", "git push origin feature:main"]
OTHER_REPO_ROUTE = ["gh api repos/someone-else/other/pulls/1", "gh api repos/someone-else/other/pulls/1/merge",
                    "gh api -X PUT repos/someone-else/other/pulls/1/merge"]
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


class TestTable(unittest.TestCase):
    def test_every_listed_form_is_a_rule_in_table_order(self):
        self.assertIsNotNone(OWNER_REPO, "this checkout's origin must be a GitHub remote for the route tests")
        expected = ALT_RULES + GH_RULES + [OWN_ROUTE] + GIT_RULES
        self.assertEqual(permissions.allow_rules(), expected)

    def test_i064_commands_are_covered(self):
        rules = permissions.allow_rules()
        for cmd in COVERED:
            self.assertTrue(matching(rules, cmd), f"{cmd!r} is covered by no rule")

    def test_never_list_and_out_of_table_commands_match_no_rule(self):
        rules = permissions.allow_rules()
        for group in (PRIVILEGE_ESCALATION, SERVICE_UNITS, RECURSIVE_REMOVE, FORCE_PUSH, PUSH_TO_MAIN,
                      OTHER_REPO_ROUTE, OUT_OF_TABLE, PLAIN_PUSH, NEIGHBOURS):
            for cmd in group:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

    def test_neighbouring_subcommands_match_no_rule(self):
        """A2 on its own: `git difftool --extcmd=…`, `git logtool`, `git show-branch`, `git commit-tree`, `gh pr viewer`, …"""
        for repo in (None, WIDGETS):
            rules = permissions.allow_rules(repo)
            for cmd in NEIGHBOURS:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

    def test_every_verb_rule_has_the_space_before_its_wildcard(self):
        """The whole table, not just `_GIT`: a wildcard verb rule is `<verb> *` — the one documented exception is the pulls
        route, whose `pulls*` continues with `/56/merge` and has no neighbouring route to bleed into."""
        for rule in permissions.allow_rules(WIDGETS):
            c = rule[len("Bash("):-1]
            if "*" not in c:
                continue
            if c.startswith("gh api repos/"):
                self.assertEqual(c, "gh api repos/acme/widgets/pulls*")
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
        self.assertEqual(len([c for c in contents if c.startswith("gh api")]), 1)

    def test_gh_api_route_is_this_repository_only(self):
        with mock.patch.object(permissions, "_remote_url", return_value="git@github.com:acme/widgets.git"):
            rules = permissions.allow_rules()
        api = [r for r in rules if "gh api" in r]
        self.assertEqual(api, [WIDGETS_ROUTE])
        self.assertTrue(rule_matches(api[0], "gh api repos/acme/widgets/pulls"))
        self.assertTrue(rule_matches(api[0], "gh api repos/acme/widgets/pulls/56/merge"))
        for cmd in OTHER_REPO_ROUTE + ["gh api repos/acme/widgets/git/refs/heads/main", "gh api repos/acme/widgets-2/pulls/1"]:
            self.assertFalse(rule_matches(api[0], cmd), cmd)

    def test_remote_forms_parse_and_an_unreadable_remote_omits_the_route(self):
        for url in ("git@github.com:acme/widgets.git", "https://github.com/acme/widgets", "https://github.com/acme/widgets.git",
                    "ssh://git@github.com/acme/widgets.git", "https://github.com/acme/widgets/"):
            with mock.patch.object(permissions, "_remote_url", return_value=url):
                self.assertEqual(permissions.github_repo(), "acme/widgets", url)
        for url in (None, "", "https://gitlab.com/acme/widgets.git", "not a url"):
            with mock.patch.object(permissions, "_remote_url", return_value=url):
                self.assertIsNone(permissions.github_repo(), url)
                rules = permissions.allow_rules()
                self.assertEqual([r for r in rules if "gh api" in r], [], url)   # omitted, never widened
                self.assertEqual(rules, ALT_RULES + GH_RULES + GIT_RULES)
        self.assertRegex(permissions.github_repo(), r"^[^/\s]+/[^/\s]+$")   # the live remote of this checkout parses

    def test_no_absolute_home_path_in_the_module(self):
        src = Path(permissions.__file__).read_text()
        self.assertNotIn("/home/", src)
        self.assertIn(f"Bash({config.REPO}/bin/alt *)", permissions.allow_rules())

    def test_residual_risk_is_recorded_not_fixed(self):
        """A4: `gh pr merge *` admits `--admin` and `--repo other/x`; the module says so and leaves it to guard.py."""
        rules = permissions.allow_rules()
        self.assertTrue(matching(rules, "gh pr merge 1 --admin --repo other/project"))
        self.assertIn("Residual risk", Path(permissions.__file__).read_text())
        self.assertEqual(set(permissions.permissions_block()), {"allow"})

    def test_block_and_allowed_tools_share_one_source(self):
        grown = permissions.allow_rules() + ["Bash(echo grown *)"]
        with mock.patch.object(permissions, "allow_rules", return_value=grown):
            self.assertEqual(permissions.permissions_block(), {"allow": grown})
            self.assertEqual(permissions.allowed_tools(extra=("Read", "Agent")).split(","), ["Read", "Agent", *grown])
        self.assertEqual(permissions.allowed_tools().split(","), permissions.allow_rules())


class TestPerCheckout(unittest.TestCase):
    """A1: the pulls route follows the checkout the session runs in — never Altitude's own."""

    def test_each_checkout_renders_only_its_own_route(self):
        w, g = permissions.allow_rules(WIDGETS), permissions.allow_rules(GIZMOS)
        self.assertEqual([r for r in w if "gh api" in r], [WIDGETS_ROUTE])
        self.assertEqual([r for r in g if "gh api" in r], [GIZMOS_ROUTE])
        self.assertNotIn(OWN_ROUTE, w)
        self.assertNotIn(OWN_ROUTE, g)
        self.assertIn(OWN_ROUTE, permissions.allow_rules())                     # None: this checkout, the default
        self.assertIn(OWN_ROUTE, permissions.allow_rules(config.REPO))          # and by path
        self.assertEqual([r for r in w if "gh api" not in r], [r for r in g if "gh api" not in r])   # the rest is one table
        self.assertEqual(permissions.github_repo(WIDGETS), "acme/widgets")
        self.assertEqual(permissions.github_repo(GIZMOS), "beta/gizmos")
        self.assertIsNone(permissions.github_repo(_TMP))                        # not a checkout: omitted, never widened
        for cmd in covered("acme/widgets"):
            self.assertTrue(matching(w, cmd), f"{cmd!r} is covered by no widgets rule")
        for cmd in covered("acme/widgets")[-2:] + OTHER_REPO_ROUTE:
            self.assertEqual(matching(g, cmd), [], f"{cmd!r} is admitted in gizmos")

    def test_settings_file_is_per_repository(self):
        pw, pg, pd = engines.claude_settings(WIDGETS), engines.claude_settings(GIZMOS), engines.claude_settings()
        self.assertEqual(len({pw, pg, pd}), 3)
        self.assertEqual(pd.name, "claude-settings.json")
        self.assertEqual(engines.claude_settings(config.REPO), pd)              # this checkout by path is the default file
        self.assertEqual(engines.claude_settings(Path(str(WIDGETS) + "/")), pw)  # a spelling of the same path is the same file
        for p, route, other in ((pw, WIDGETS_ROUTE, GIZMOS_ROUTE), (pg, GIZMOS_ROUTE, WIDGETS_ROUTE)):
            st = json.loads(p.read_text())
            self.assertIn(route, st["permissions"]["allow"])
            self.assertNotIn(other, st["permissions"]["allow"])
            self.assertNotIn(OWN_ROUTE, st["permissions"]["allow"])
            self.assertEqual(st["autoCompactWindow"], 300_000)
            self.assertEqual(set(st), {"autoCompactWindow", "permissions"})
        self.assertIn(OWN_ROUTE, json.loads(pd.read_text())["permissions"]["allow"])
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
        self.assertIn(WIDGETS_ROUTE, json.loads(want[0].read_text())["permissions"]["allow"])
        self.assertIn(GIZMOS_ROUTE, json.loads(want[1].read_text())["permissions"]["allow"])


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
        self.assertEqual(set(self.global_settings), {"autoCompactWindow", "permissions"})

    def test_l2_file_carries_its_own_projects_route(self):
        self.assertIn(WIDGETS_ROUTE, self.l2_settings["permissions"]["allow"])
        self.assertNotIn(GIZMOS_ROUTE, self.l2_settings["permissions"]["allow"])
        self.assertIn(GIZMOS_ROUTE, self.l2_gizmos["permissions"]["allow"])
        self.assertNotIn(WIDGETS_ROUTE, self.l2_gizmos["permissions"]["allow"])
        for st in (self.l2_settings, self.l2_gizmos):
            self.assertNotIn(OWN_ROUTE, st["permissions"]["allow"])    # never Altitude's own route in another project

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
            for cmd in covered("acme/widgets"):
                self.assertTrue(matching(st["permissions"]["allow"], cmd), f"{cmd!r} is covered by no rule")
            for cmd in FORCE_PUSH + PUSH_TO_MAIN + PRIVILEGE_ESCALATION + SERVICE_UNITS + RECURSIVE_REMOVE + OTHER_REPO_ROUTE + NEIGHBOURS:
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
        self.assertEqual(json.loads(p.read_text()), {"autoCompactWindow": 300_000, "permissions": permissions.permissions_block()})


class TestOneSource(unittest.TestCase):
    """A3: a sentinel table must surface in every consumer — this fails the moment any one of them stops going through
    `permissions.allow_rules()`: the default settings file, a per-repo settings file, the L2's per-dispatch file, and the
    `allowed_tools=` value the L3 turn actually passes to `claude_print`."""
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
        self.assertIn(WIDGETS_ROUTE, l3.allowed_tools("widgets"))


if __name__ == "__main__":
    unittest.main()
