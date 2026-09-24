"""The history audit finds planted material across refs, keeps matches out of its console summary and rescans only the delta."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.support import AltitudeCase, REPO, git, make_repo

SCRIPT = REPO / "scripts" / "audit_history.py"


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAuditHistory(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.audit = load()
        self.out = self.tmp / "audit"

    def run_audit(self, name, *extra):
        path = self.out / name
        console = io.StringIO()
        with redirect_stdout(console):
            self.audit.main(["--repo", str(self.repo), "--findings", str(path), *extra])
        return json.loads(console.getvalue()), json.loads(path.read_text())

    def commit(self, files, message, branch=None):
        if branch:
            git("checkout", "-q", "-b", branch, cwd=self.repo)
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", message, cwd=self.repo)

    def test_finds_planted_material_in_published_and_local_refs_and_keeps_matches_private(self):
        key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfixture\n-----END RSA PRIVATE KEY-----\n"
        self.commit({"deploy/id_rsa": key, "notes.md": "see /home/somebody/altitude and mail somebody@corp.example\n"}, "keys")
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.commit({"scratch.txt": "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij0123\n"}, "local only", branch="wip")
        words = self.tmp / "words.txt"
        words.write_text("otherproject\n")
        self.commit({"docs/plan.md": "OtherProject shares the runner\n"}, "names", branch="names")
        summary, private = self.run_audit("full.json", "--words", str(words))

        found = summary["findings"]
        self.assertEqual(found["private-key"]["published"], 1)
        self.assertEqual(found["key-or-credential-file"]["published"], 1)
        self.assertEqual(found["home-path"]["published"], 1)
        self.assertEqual(found["email-address"]["published"], 1)
        self.assertEqual((found["github-token"]["total"], found["github-token"]["published"]), (1, 0))
        self.assertEqual((found["private-word"]["total"], found["private-word"]["published"]), (1, 0))
        self.assertEqual(summary["cutoff"]["origin/main"], git("rev-parse", "origin/main", cwd=self.repo).strip())
        self.assertEqual(summary["refs_by_namespace"]["refs/heads/"], 3)
        console = json.dumps(summary)
        for secret in ("ghp_", "somebody", "PRIVATE KEY", "otherproject"):
            self.assertNotIn(secret, console)
        token = next(f for f in private["findings"] if f["category"] == "github-token")
        self.assertEqual((token["paths"], token["refs"], token["line"]), (["scratch.txt"], ["refs/heads/"], 1))
        self.assertTrue(token["match"].startswith("ghp_") and token["redacted"].startswith("ghp_…"))
        self.assertEqual(oct((self.out / "full.json").stat().st_mode)[-3:], "600")

    def test_delta_scans_only_objects_and_commits_new_since_the_previous_run(self):
        self.commit({"old.txt": "AKIAABCDEFGHIJKLMNOP old\n"}, "old")
        _, _ = self.run_audit("full.json")
        self.commit({"new.txt": "AKIAQRSTUVWXYZABCDEF new\n"}, "new: mail later@corp.example")
        summary, private = self.run_audit("delta.json", "--since", str(self.out / "full.json"))
        self.assertEqual((summary["commits"], summary["blobs"]), (1, 1))
        self.assertEqual({f["kind"] for f in private["findings"]}, {"content", "commit"})
        self.assertEqual(summary["findings"]["cloud-access-key"]["total"], 1)
        self.assertEqual(summary["findings"]["email-address"]["total"], 1)
        self.assertEqual(summary["since"], str(self.out / "full.json"))

    def test_fixture_placeholders_and_binaries_do_not_count_as_secrets(self):
        (self.repo / "big.bin").write_bytes(b"\0" * (300 << 10))
        self.commit({"conf.py": 'password = "example-placeholder-value-1234"\nmail = "ada@example.com"\nhome = "/home/you"\n'}, "fixtures")
        summary, _ = self.run_audit("full.json")
        self.assertNotIn("assigned-secret", summary["findings"])
        self.assertNotIn("email-address", summary["findings"])
        self.assertNotIn("home-path", summary["findings"])
        self.assertEqual(summary["findings"]["large-blob"]["total"], 1)

    def test_findings_inside_the_repository_are_refused(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.audit.main(["--repo", str(self.repo), "--findings", str(self.repo / "findings.json")])
        self.assertFalse((self.repo / "findings.json").exists())


if __name__ == "__main__":
    unittest.main()
