"""The coordinator's gh reads any repository its login can see and never writes or opens a browser."""
import subprocess
from unittest import mock

from tests.support import AltitudeCase, git, make_repo
from altitude import l3, server

ALLOWED = (
    ["pr", "view", "7", "--comments", "--json", "title,body"], ["pr", "checks", "7", "--watch"],
    ["pr", "diff", "7"], ["pr", "list", "--search", "is:open org:other OR label:bug"], ["issue", "status"],
    ["repo", "view"], ["repo", "view", "other/private"], ["repo", "list", "other"],
    ["release", "view", "v1.0", "--repo", "other/private"], ["release", "list", "-R", "cli/cli"],
    ["run", "view", "123", "--log"], ["run", "list", "-w", "checks.yml"], ["workflow", "list"],
    ["ruleset", "list", "--org", "other"], ["ruleset", "check", "main"], ["label", "list"], ["cache", "list"],
    ["issue", "view", "https://github.com/other/private/issues/1"], ["pr", "view", "other/private#1"],
    ["search", "issues", "secret"], ["search", "code", "--owner", "other", "token"], ["search", "repos", "topic:x"],
)

REFUSED = (
    # Writes and side effects.
    ["pr", "create"], ["pr", "edit", "7"], ["pr", "close", "7"], ["pr", "merge", "7"], ["pr", "comment", "7"],
    ["pr", "checkout", "7"], ["issue", "create"], ["issue", "close", "7"], ["issue", "comment", "7"],
    ["issue", "delete", "7"], ["release", "create", "v1"], ["release", "delete", "v1"],
    ["release", "download", "v1"], ["run", "rerun", "1"], ["run", "cancel", "1"], ["run", "download", "1"],
    ["run", "delete", "1"], ["workflow", "run", "ci"], ["workflow", "disable", "ci"], ["cache", "delete", "x"],
    ["label", "create", "x"], ["repo", "edit"], ["repo", "clone", "cli/cli"], ["ruleset", "--help", "x"],
    ["auth", "status"], ["auth", "token"], ["secret", "set", "x"], ["browse", "7"], ["extension", "install", "x"],
    # Non-GET, bodied or off-host API calls.
    ["api", "repos/team/project/issues", "-X", "POST"], ["api", "repos/team/project", "--method=PATCH"],
    ["api", "repos/team/project/issues", "-iXPOST"], ["api", "repos/team/project/issues", "-f", "title=x"],
    ["api", "repos/team/project/issues", "-F", "title=x"], ["api", "repos/team/project", "--raw-field=a=b"],
    ["api", "repos/team/project", "--input", "-"], ["api", "repos/team/project", "--hostname", "example.com"],
    ["api", "graphql", "-f", "query=x"], ["api", "https://example.com/repos/team/project"],
    ["api", "--method", "GET"],
    # Browser.
    ["pr", "view", "7", "--web"], ["repo", "view", "--web=true"], ["pr", "view", "7", "-w"],
    ["pr", "view", "7", "-cw"], ["search", "issues", "x", "-w"],
)


class TestCoordinatorGhReads(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        git("remote", "set-url", "origin", "git@github.com:team/project.git", cwd=self.repo)
        self.calls, self.output = [], "read\n"
        real_run = subprocess.run

        def run(args, **kwargs):
            if args[0] != "gh":
                return real_run(args, **kwargs)
            self.calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, self.output, "")

        self.enterContext(mock.patch.object(server.subprocess, "run", side_effect=run))

    def read(self, args):
        return server.l3_verb_request(self.project, {"kind": "gh", "args": args})

    def test_reads_of_any_repository_run_unchanged(self):
        self.setenv("GH_REPO", "other/unrelated")
        for args in ALLOWED:
            self.calls.clear()
            with self.subTest(args=args):
                self.assertEqual(self.read(args), {"returncode": 0, "stdout": "read\n", "stderr": ""})
                [(command, kwargs)] = self.calls
                self.assertEqual(command, ["gh", *args])
                self.assertEqual(kwargs["cwd"], str(self.repo))
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                self.assertNotIn("GH_REPO", kwargs["env"], "a read naming no repository uses this checkout")
                self.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")

    def test_writes_browser_and_bodied_api_calls_are_refused_before_gh(self):
        for args in REFUSED:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "L3 gh read refused: .+ only reads"):
                self.read(args)
        self.assertEqual(self.calls, [])

    def test_api_get_is_rebuilt_from_validated_options(self):
        self.read(["api", "repos/{owner}/{repo}/rulesets", "-X", "get", "-H", "Accept: application/json",
                   "--paginate", "-q", ".[].name"])
        self.read(["api", "/repos/other/private/contents/README.md", "--include"])
        self.assertEqual([call[0] for call in self.calls], [
            ["gh", "api", "repos/{owner}/{repo}/rulesets", "--method=GET", "--header=Accept: application/json",
             "--jq=.[].name", "--paginate"],
            ["gh", "api", "/repos/other/private/contents/README.md", "--method=GET", "--include"]])

    def test_argument_and_output_bounds_hold(self):
        for args in (["pr"], ["pr", "view", *["7"] * 63], ["pr", "view", "x" * 4097], ["pr", 7], "pr view"):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "invalid gh read arguments"):
                self.read(args)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.read(["pr", "view", *["7"] * 62])["stdout"], "read\n")
        with mock.patch.object(server, "L3_VERB_MAX_OUTPUT", 8):
            self.assertEqual(self.read(["pr", "view", "7"])["stdout"], "read\n")
            self.output = "x" * 20
            self.assertEqual(self.read(["pr", "view", "7"])["stdout"], "x" * 8 + "\n[output truncated by altd]\n")

    def test_runtime_shim_reaches_the_real_project_broker(self):
        broker = server.start_l3_verb_broker(self.project)
        self.addCleanup(server.stop_l3_verb_broker, broker)
        runtime = l3._l3_runtime(self.project, "claude")
        self.addCleanup(l3._remove_runtime, runtime)
        shim = str(runtime / "bin" / "gh")
        read = subprocess.run([shim, "repo", "view", "other/private"], input="", capture_output=True, text=True,
                              timeout=30)
        self.assertEqual((read.returncode, read.stdout), (0, "read\n"), read.stderr)
        merge = subprocess.run([shim, "pr", "merge", "7"], input="", capture_output=True, text=True, timeout=30)
        self.assertNotEqual(merge.returncode, 0)
        self.assertIn("L3 gh read refused", merge.stderr)
        self.assertEqual([call[0] for call in self.calls], [["gh", "repo", "view", "other/private"]])
