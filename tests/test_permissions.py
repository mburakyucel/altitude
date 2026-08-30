"""I-064: every Claude session Altitude launches carries one permission allowlist, rendered by `altitude.permissions`
for both consumers — the settings `permissions.allow` block (`engines.claude_settings`, `dispatch.session_settings`)
and the L3's `--allowedTools` string. The assertions run against the settings files actually produced.

`rule_matches` is a port of Claude Code 2.1.251's Bash rule matcher (read from the binary on 2026-08-30): a rule ending
in `:*` is a legacy prefix; a rule with a bare `*` is a wildcard where `*` matches anything (spaces included) and a lone
trailing ` *` also admits the bare command; anything else is an exact match. It is what makes the negative cases below
evidence rather than string trivia — in particular it shows why the table carries no push rule."""
import json
import os
import re
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
GIT_RULES = [f"Bash(git {v}*)" for v in ("status", "log", "diff", "show", "fetch", "branch", "rev-parse", "add", "commit")]
GIT_RULES += ["Bash(git rebase origin/main)", "Bash(git reset --hard origin/main)"]

# ---- the exact commands I-064 recorded as denied, plus the everyday role forms -------------------------------------
COVERED = [
    "alt task hold-merge apply-r-010-i-030 --off",
    "alt incident amend I-055",
    f"{REPO}/bin/alt task status foo",
    "gh pr merge 56 --squash --delete-branch",
    f"gh api repos/{OWNER_REPO}/pulls/56/merge",
    "git reset --hard origin/main",
    "alt", "alt land --message \"x\"",
    "gh pr view", "gh pr view 56 --json state,mergeable", "gh pr checks 56 --watch", "gh pr create --fill",
    "gh run watch 12345 --exit-status", "gh run list --limit 5", "gh issue list",
    "git status --short", "git log --oneline -5", "git diff --stat origin/main HEAD", "git show HEAD --stat",
    "git fetch origin", "git branch --show-current", "git rev-parse --abbrev-ref HEAD",
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
OTHER_REPO_ROUTE = ["gh api repos/someone-else/other/pulls/1", "gh api repos/someone-else/other/pulls/1/merge",
                    "gh api -X PUT repos/someone-else/other/pulls/1/merge"]
OUT_OF_TABLE = ["gh repo delete x", "gh auth logout", "gh api repos/x/y/git/refs/heads/main", "git reset --hard HEAD~3",
                "git rebase -i origin/main", "git checkout main", "git clean -fdx", "altd", "kill -9 1", "echo alt task status"]
# plain pushes are not in the table either: the landing push runs inside `alt land`, the rest stays with the classifier
PLAIN_PUSH = ["git push -u origin feature", "git push origin feature"]


class TestMatcherPort(unittest.TestCase):
    def test_semantics_read_from_the_binary(self):
        self.assertTrue(rule_matches("Bash(alt *)", "alt"))
        self.assertTrue(rule_matches("Bash(alt *)", "alt task status x"))
        self.assertFalse(rule_matches("Bash(alt *)", "altd"))
        self.assertTrue(rule_matches("Bash(git log*)", "git log --oneline"))
        self.assertTrue(rule_matches("Bash(git log*)", "git log"))
        self.assertFalse(rule_matches("Bash(git rebase origin/main)", "git rebase -i origin/main"))
        self.assertTrue(rule_matches("Bash(npm run:*)", "npm run test"))
        # the reason the table has no push rule: a trailing wildcard spans flags and refspecs alike
        self.assertTrue(rule_matches("Bash(git push origin *)", "git push origin --force main"))
        self.assertTrue(rule_matches("Bash(git push -u origin *)", "git push -u origin HEAD:main"))


class TestTable(unittest.TestCase):
    def test_every_listed_form_is_a_rule_in_table_order(self):
        self.assertIsNotNone(OWNER_REPO, "this checkout's origin must be a GitHub remote for the route tests")
        expected = ALT_RULES + GH_RULES + [f"Bash(gh api repos/{OWNER_REPO}/pulls*)"] + GIT_RULES
        self.assertEqual(permissions.allow_rules(), expected)

    def test_i064_commands_are_covered(self):
        rules = permissions.allow_rules()
        for cmd in COVERED:
            self.assertTrue(matching(rules, cmd), f"{cmd!r} is covered by no rule")

    def test_never_list_and_out_of_table_commands_match_no_rule(self):
        rules = permissions.allow_rules()
        for group in (PRIVILEGE_ESCALATION, SERVICE_UNITS, RECURSIVE_REMOVE, FORCE_PUSH, PUSH_TO_MAIN,
                      OTHER_REPO_ROUTE, OUT_OF_TABLE, PLAIN_PUSH):
            for cmd in group:
                self.assertEqual(matching(rules, cmd), [], f"{cmd!r} is admitted")

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
        self.assertEqual(api, ["Bash(gh api repos/acme/widgets/pulls*)"])
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

    def test_block_and_allowed_tools_share_one_source(self):
        grown = permissions.allow_rules() + ["Bash(echo grown *)"]
        with mock.patch.object(permissions, "allow_rules", return_value=grown):
            self.assertEqual(permissions.permissions_block(), {"allow": grown})
            self.assertEqual(permissions.allowed_tools(extra=("Read", "Agent")).split(","), ["Read", "Agent", *grown])
        self.assertEqual(permissions.allowed_tools().split(","), permissions.allow_rules())


class TestConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_root()
        config.save_projects({"altitude": {"name": "altitude", "path": str(_TMP), "stacks": ["python"]}})
        T.new("altitude", "perm-task", "S", "req")
        cls.global_settings = json.loads(engines.claude_settings().read_text())
        cls.l2_settings = json.loads(dispatch.session_settings("altitude", "perm-task", "key").read_text())

    def test_both_settings_files_carry_the_same_allowlist(self):
        rules = permissions.allow_rules()
        self.assertEqual(self.global_settings["permissions"], {"allow": rules})
        self.assertEqual(self.l2_settings["permissions"], {"allow": rules})
        self.assertEqual(self.global_settings["autoCompactWindow"], 300_000)
        self.assertEqual(self.l2_settings["autoCompactWindow"], 300_000)
        self.assertIn("hooks", self.l2_settings)                       # the L2 file keeps its hooks and env untouched
        self.assertEqual(self.l2_settings["env"]["ALTITUDE_ACTOR"], "l2")
        self.assertEqual(set(self.global_settings), {"autoCompactWindow", "permissions"})

    def test_allow_only_no_deny_block(self):
        for st in (self.global_settings, self.l2_settings):
            self.assertEqual(set(st["permissions"]), {"allow"})

    def test_l3_allowed_tools_is_the_same_table(self):
        parts = l3.ALLOWED_TOOLS.split(",")
        self.assertEqual(parts[:4], ["Read", "Grep", "Glob", "Agent"])
        self.assertEqual(parts[4:], self.global_settings["permissions"]["allow"])
        self.assertEqual(parts[4:], self.l2_settings["permissions"]["allow"])

    def test_i064_commands_are_covered_by_both_files(self):
        for st in (self.global_settings, self.l2_settings):
            for cmd in COVERED:
                self.assertTrue(matching(st["permissions"]["allow"], cmd), f"{cmd!r} is covered by no rule")
            for cmd in FORCE_PUSH + PUSH_TO_MAIN + PRIVILEGE_ESCALATION + SERVICE_UNITS + RECURSIVE_REMOVE + OTHER_REPO_ROUTE:
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


if __name__ == "__main__":
    unittest.main()
