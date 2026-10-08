"""The coordinator's gh reads this project's repository or a public one and never writes."""
import subprocess
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import server

VISIBILITY = {"cli/cli": "public", "other/private": "private"}

ALLOWED = (
    (["pr", "view", "7", "--comments", "--json", "title,body"], set()),
    (["pr", "checks", "7", "--watch"], set()),
    (["pr", "diff", "7"], set()),
    (["pr", "list", "--search", "is:open review:required"], set()),
    (["issue", "status"], set()),
    (["repo", "view"], set()),
    (["repo", "view", "cli/cli"], {"cli/cli"}),
    (["release", "view", "v1.0", "--repo", "cli/cli"], {"cli/cli"}),
    (["release", "list", "-R", "Team/Project"], set()),
    (["run", "view", "123", "--log"], set()),
    (["run", "list", "-w", "checks.yml"], set()),
    (["workflow", "list"], set()),
    (["ruleset", "list"], set()),
    (["ruleset", "check", "main"], set()),
    (["label", "list"], set()),
    (["cache", "list"], set()),
    (["issue", "view", "https://github.com/cli/cli/issues/1"], {"cli/cli"}),
    (["pr", "view", "cli/cli#1"], {"cli/cli"}),
    (["search", "issues", "flaky", "--repo", "team/project"], set()),
    (["search", "prs", "repo:cli/cli", "is:open"], {"cli/cli"}),
    (["search", "code", "--repo=cli/cli", "token"], {"cli/cli"}),
)

REFUSED = (
    # Writes and side effects.
    ["pr", "create"], ["pr", "edit", "7"], ["pr", "close", "7"], ["pr", "merge", "7"], ["pr", "comment", "7"],
    ["pr", "checkout", "7"], ["issue", "create"], ["issue", "close", "7"], ["issue", "comment", "7"],
    ["issue", "delete", "7"], ["release", "create", "v1"], ["release", "delete", "v1"],
    ["release", "download", "v1"], ["run", "rerun", "1"], ["run", "cancel", "1"], ["run", "download", "1"],
    ["run", "delete", "1"], ["workflow", "run", "ci"], ["workflow", "disable", "ci"], ["cache", "delete", "x"],
    ["label", "create", "x"], ["repo", "edit"], ["repo", "clone", "cli/cli"], ["ruleset", "--help", "x"],
    ["auth", "status"], ["auth", "token"], ["secret", "list"], ["status", "--org"], ["browse", "7"],
    # Non-GET or bodied API calls, and endpoints outside one repository.
    ["api", "repos/team/project/issues", "-X", "POST"], ["api", "repos/team/project", "--method=PATCH"],
    ["api", "repos/team/project/issues", "-iXPOST"], ["api", "repos/team/project/issues", "-f", "title=x"],
    ["api", "repos/team/project/issues", "-F", "title=x"], ["api", "repos/team/project", "--raw-field=a=b"],
    ["api", "repos/team/project", "--input", "-"], ["api", "repos/team/project", "--hostname", "example.com"],
    ["api", "graphql", "-f", "query=x"], ["api", "user/repos"], ["api", "search/issues?q=x"],
    ["api", "https://api.github.com/repos/team/project"], ["api", "repos/team/project/../../search/issues"],
    ["api", "repos/team/project/%2e%2e/x"], ["api", "repos/other/private/contents/README.md"],
    ["api", "repos/{owner}/private"], ["api", "--method", "GET"],
    # Browser, owner-wide and other private repositories.
    ["pr", "view", "7", "--web"], ["repo", "view", "--web=true"], ["repo", "list"], ["repo", "list", "team"],
    ["ruleset", "list", "--org", "team"], ["ruleset", "view", "1", "-po", "team"],
    ["search", "issues", "secret"], ["search", "code", "--owner", "team", "token"],
    ["search", "issues", "--repo", "team/project", "org:other"], ["pr", "list", "--search", "user:other"],
    ["issue", "list", "--search", "x OR is:private"], ["pr", "list", "--search=repo:other/private"],
    ["pr", "view", "--repo", "other/private", "7"], ["pr", "view", "-Rother/private", "7"],
    ["pr", "view", "-R=other/private", "7"], ["pr", "view", "-cR", "other/private", "7"],
    ["pr", "view", "https://github.com/other/private/pull/7"], ["pr", "view", "other/private#7"],
    ["repo", "view", "other/private"], ["pr", "view", "https://example.com/team/project/pull/7"],
    ["pr", "view", "7", "--repo", "example.com/team/project"], ["pr", "view", "7", "--repo"],
    ["pr", "view", "7", "-R", "../.."], ["pr", "view", "7", "--repo=missing/repository"],
)


class TestCoordinatorGhReads(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.calls = []
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] != "gh":
                return real_run(args, **kwargs)
            self.calls.append((args, kwargs))
            if args[1:3] == ["api", "--method=GET"] and args[-1] == "--jq=.visibility":
                seen = VISIBILITY.get(args[3].removeprefix("repos/"))
                return subprocess.CompletedProcess(args, 0 if seen else 1, f"{seen}\n" if seen else "",
                                                   "" if seen else "HTTP 404: Not Found\n")
            return subprocess.CompletedProcess(args, 0, "read\n", "")

        self.enterContext(mock.patch.object(server.subprocess, "run", side_effect=run))

    def read(self, args):
        return server.l3_verb_request(self.project, {"kind": "gh", "args": args})

    def test_reads_of_this_project_and_public_repositories_run(self):
        for args, checked in ALLOWED:
            self.calls.clear()
            with self.subTest(args=args):
                self.assertEqual(self.read(args), {"returncode": 0, "stdout": "read\n", "stderr": ""})
                *checks, (command, kwargs) = self.calls
                self.assertEqual(command, ["gh", *args])
                self.assertEqual({check[0][3].removeprefix("repos/") for check in checks}, checked)
                self.assertEqual(kwargs["cwd"], str(self.repo))
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                self.assertNotIn("GH_REPO", kwargs["env"])
                self.assertEqual(kwargs["env"]["GH_BROWSER"], "false", "a -w cluster cannot open a browser")

    def test_writes_owner_wide_reads_and_other_private_repositories_are_refused(self):
        for args in REFUSED:
            self.calls.clear()
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "L3 gh read refused: .+ only reads"):
                self.read(args)
            self.assertTrue(all(call[0][-1] == "--jq=.visibility" for call in self.calls),
                            f"{args} ran more than a visibility check")

    def test_api_get_is_rebuilt_from_validated_options(self):
        self.read(["api", "repos/{owner}/{repo}/rulesets", "-X", "get", "-H", "Accept: application/json",
                   "--paginate", "-q", ".[].name"])
        self.assertEqual(self.calls[-1][0], ["gh", "api", "repos/{owner}/{repo}/rulesets", "--method=GET",
                                             "--header=Accept: application/json", "--jq=.[].name", "--paginate"])
        self.calls.clear()
        self.read(["api", "/repos/cli/cli/releases/latest", "--include"])
        self.assertEqual([call[0] for call in self.calls], [
            ["gh", "api", "--method=GET", "repos/cli/cli", "--jq=.visibility"],
            ["gh", "api", "/repos/cli/cli/releases/latest", "--method=GET", "--include"]])

    def test_argument_and_output_bounds_hold(self):
        for args in (["pr"], ["pr", "view", *["7"] * 63], ["pr", "view", "x" * 4097], ["pr", 7], "pr view"):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "invalid gh read arguments"):
                self.read(args)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.read(["pr", "view", *["7"] * 62])["stdout"], "read\n")
        with mock.patch.object(server, "L3_VERB_MAX_OUTPUT", 8):
            self.assertEqual(self.read(["pr", "view", "7"])["stdout"], "read\n")
            with mock.patch.object(server.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0, "x" * 20, "")):
                self.assertEqual(self.read(["pr", "view", "7"])["stdout"],
                                 "x" * 8 + "\n[output truncated by altd]\n")
