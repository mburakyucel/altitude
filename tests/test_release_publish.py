"""The one-command install script and the tag-triggered release job, run on fictional releases."""
import hashlib
import copy
import io
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import threading
import textwrap
import unittest

from tests.support import REPO, AltitudeCase, add_worktree, git, make_repo
from altitude import config, dispatch, engines, installation, releases, server, terminal, state as S, tasks as T
from scripts import build_release

REPOSITORY = "https://github.com/example/altitude"
FINGERPRINT = "sha256 Fingerprint=AA:BB:CC"


class InstallScript(unittest.TestCase):
    """install.sh with fixture commands on PATH: curl serves a local release, systemctl answers as told."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.release = self.tmp / "release"
        self.release.mkdir()
        (self.release / "altitude-v0.1.0.tar.gz").write_bytes(b"fictional archive")
        (self.release / "install.py").write_text(textwrap.dedent(f"""\
            import json, pathlib, sys
            pathlib.Path({str(self.tmp / 'installer-args.json')!r}).write_text(json.dumps(sys.argv[1:]))
            print(json.dumps({{"version": "v0.1.0", "service": "running", "url": "https://localhost:8443",
                              "trust": {{"ca_cert": {str(self.tmp / 'home/.altitude/tls/ca.crt')!r}, "ca_sha256": {FINGERPRINT!r}}}}}))
            """))
        digest = {name: hashlib.sha256((self.release / name).read_bytes()).hexdigest()
                  for name in ("altitude-v0.1.0.tar.gz", "install.py")}
        self.script = self.tmp / "install.sh"
        self.script.write_text(build_release.install_script((REPO / "scripts/install.sh").read_text(), "v0.1.0",
                                                            REPOSITORY, digest["altitude-v0.1.0.tar.gz"], digest["install.py"]))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("sh", "cat", "cp", "cut", "sha256sum", "mktemp", "rm", "openssl"):
            (self.bin / tool).symlink_to(subprocess.check_output(["sh", "-c", f"command -v {tool}"], text=True).strip())
        (self.bin / "python3").symlink_to(sys.executable)
        self.fixture("uname", 'case "$1" in -s) echo "${FIXTURE_SYSTEM:-Linux}" ;; -m) echo "${FIXTURE_MACHINE:-x86_64}" ;; esac')
        self.fixture("id", 'echo "${FIXTURE_UID:-1000}"')
        self.fixture("systemctl", 'exit "${FIXTURE_SYSTEMD:-0}"')
        self.fixture("sw_vers", 'echo 15.5')
        self.fixture("xcode-select", 'exit 1')
        self.fixture("curl", f'echo "$@" >> {self.tmp}/downloads\n'
                             'for last; do :; done\n'
                             'while [ "$1" != -o ]; do shift; done\n'
                             f'cp "{self.release}/${{last##*/}}" "$2"')
        self.env = {"PATH": str(self.bin), "HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp)}

    def fixture(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, **env):
        # The documented form: the script arrives on standard input.
        return subprocess.run(["sh"], input=self.script.read_text(), env={**self.env, **env},
                              capture_output=True, text=True)

    def downloads(self):
        path = self.tmp / "downloads"
        return path.read_text().splitlines() if path.exists() else []

    def test_installs_the_release_it_was_built_with_and_prints_next_steps(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[-1] for line in self.downloads()],
                         [f"{REPOSITORY}/releases/download/v0.1.0/altitude-v0.1.0.tar.gz",
                          f"{REPOSITORY}/releases/download/v0.1.0/install.py"])
        self.assertTrue(all("--proto =https" in line for line in self.downloads()))
        arguments = json.loads((self.tmp / "installer-args.json").read_text())
        self.assertEqual(arguments[0::2], ["--archive", "--sha256"])
        self.assertEqual(arguments[3], hashlib.sha256(b"fictional archive").hexdigest())
        self.assertIn("Altitude v0.1.0 is installed and its service is running.", result.stdout)
        self.assertIn("Address: https://localhost:8443", result.stdout)
        self.assertIn("Its fingerprint: AA:BB:CC", result.stdout)
        self.assertIn("Certificate authority: ~/.altitude/tls/ca.crt", result.stdout)
        self.assertNotIn(str(self.tmp / "home"), result.stdout)
        self.assertIn('1. Put alt on your PATH', result.stdout)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', result.stdout)
        self.assertIn("2. Run: alt doctor", result.stdout)
        self.assertIn(f"{REPOSITORY}/blob/v0.1.0/docs/SETUP.md#trust-https-on-each-device", result.stdout)
        self.assertEqual(list(self.tmp.glob("tmp.*")), [], "the download folder is removed")
        on_path = self.run_script(PATH=f"{self.bin}:{self.tmp}/home/.local/bin")
        self.assertNotIn("Put alt on your PATH", on_path.stdout)
        self.assertIn("1. Run: alt doctor", on_path.stdout)

    def test_mismatched_download_runs_nothing(self):
        (self.release / "altitude-v0.1.0.tar.gz").write_bytes(b"substituted archive")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("altitude-v0.1.0.tar.gz does not match the checksum this v0.1.0 installer was built with",
                      result.stderr)
        self.assertFalse((self.tmp / "installer-args.json").exists())

    def test_failed_installer_stops_without_claiming_success(self):
        (self.release / "install.py").write_text("import sys\nsys.exit('Installation failed: fixture refusal')\n")
        digest = hashlib.sha256((self.release / "install.py").read_bytes()).hexdigest()
        self.script.write_text(build_release.install_script(
            (REPO / "scripts/install.sh").read_text(), "v0.1.0", REPOSITORY,
            hashlib.sha256(b"fictional archive").hexdigest(), digest))
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Installation failed: fixture refusal", result.stderr)
        self.assertIn("the installer stopped with the message above", result.stderr)
        self.assertNotIn("is installed", result.stdout)

    def test_a_mac_stops_with_what_it_found_before_downloading(self):
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64")
        self.assertEqual(result.returncode, 1)
        version = ".".join(map(str, sys.version_info[:3]))
        self.assertIn("Altitude cannot be installed on this Mac yet. Nothing was installed or changed.", result.stderr)
        self.assertIn(f"Detected: macOS 15.5 on arm64; Python {version} at {self.bin}/python3", result.stderr)
        home_python = self.tmp / "home/.pyenv/bin"
        home_python.mkdir(parents=True)
        (home_python / "python3").symlink_to(sys.executable)
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64", PATH=f"{home_python}:{self.bin}")
        self.assertIn(f"Python {version} at ~/.pyenv/bin/python3", result.stderr)
        self.assertNotIn(str(self.tmp / "home"), result.stderr)
        self.assertIn("native macOS runtime", result.stderr)
        self.assertIn(f"Follow macOS support: {REPOSITORY}/issues/225", result.stderr)
        self.assertEqual(self.downloads(), [])
        (self.bin / "python3").unlink()
        self.fixture("python3", 'case "$*" in *exit*) exit 1 ;; *) echo 3.9.6 ;; esac')
        result = self.run_script(FIXTURE_SYSTEM="Darwin", FIXTURE_MACHINE="arm64")
        self.assertIn("Python 3.12 or newer not found (3.9.6)", result.stderr)

    def test_unsupported_machines_and_missing_prerequisites_stop_with_the_fix(self):
        cases = [({"FIXTURE_MACHINE": "aarch64"}, "runs on Linux x86_64; this machine is Linux aarch64"),
                 ({"FIXTURE_SYSTEM": "FreeBSD"}, "this machine is FreeBSD x86_64"),
                 ({"FIXTURE_UID": "0"}, "not as root"),
                 ({"FIXTURE_SYSTEMD": "1"}, "no systemd user manager is reachable")]
        for env, message in cases:
            with self.subTest(message):
                result = self.run_script(**env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.downloads(), [])
        (self.bin / "python3").unlink()
        self.fixture("python3", 'case "$*" in *exit*) exit 1 ;; *) echo 3.10.12 ;; esac')
        result = self.run_script()
        self.assertIn("Python 3.12 or newer was not found (3.10.12)", result.stderr)
        self.assertIn("sudo apt install python3", result.stderr)

    def test_the_unfilled_template_refuses(self):
        result = subprocess.run(["sh", str(REPO / "scripts/install.sh")], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("this is the release template", result.stderr)


class ReleaseNotes(unittest.TestCase):
    def test_notes_are_the_versions_dated_changelog_section(self):
        changelog = ("# Changelog\n\n## Unreleased\n\n- Next.\n\n## v0.1.1 — 2026-10-02\n\n- Fixed a fictional thing.\n\n"
                     "## v0.1.0 — 2026-10-01\n\n- First.\n")
        self.assertEqual(build_release.notes("v0.1.1", changelog), "- Fixed a fictional thing.\n")
        self.assertEqual(build_release.notes("v0.1.0", changelog), "- First.\n")
        for version in ("v0.1.2", "v0.1"):
            with self.assertRaisesRegex(ValueError, "no dated section"):
                build_release.notes(version, changelog)
        with self.assertRaisesRegex(ValueError, "no dated section"):
            build_release.notes("v0.2.0", "## v0.2.0\n\n- Undated.\n")


class ReleaseWorkflow(unittest.TestCase):
    """The job's gate: the tag's commit is on main and has a successful push `check` run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = make_repo(self.tmp / "repo")
        self.main = git("rev-parse", "HEAD", cwd=self.repo).strip()
        git("checkout", "-qb", "side", cwd=self.repo)
        (self.repo / "side").write_text("unmerged\n")
        git("add", "side", cwd=self.repo)
        git("commit", "-qm", "side", cwd=self.repo)
        self.side = git("rev-parse", "HEAD", cwd=self.repo).strip()
        git("fetch", "-q", "origin", cwd=self.repo)
        self.workflow = (REPO / ".github/workflows/release.yml").read_text()
        step = self.workflow.split("- name: Verify the approved commit")[1].split("- name:")[0]
        self.gate = textwrap.dedent(step.split("run: |\n")[1])
        commands = self.tmp / "commands"
        commands.mkdir()
        # gh answers as its --jq filter would: matching run ids, then that run's successful check job ids.
        (commands / "gh").write_text(f'#!/bin/sh\necho "$@" >> {self.tmp}/gh-args\n'
                                     'case "$2" in */jobs) printf "%s" "$FIXTURE_JOBS" ;; *) printf "%s" "$FIXTURE_RUNS" ;; esac\n')
        (commands / "gh").chmod(0o755)
        self.path = f"{commands}{os.pathsep}{os.environ['PATH']}"

    def verify(self, commit, runs="41\n", jobs="7\n", version="v0.1.0"):
        git("tag", "-f", version, commit, cwd=self.repo)
        env = {**os.environ, "PATH": self.path, "GITHUB_SHA": commit, "VERSION": version,
               "GITHUB_REPOSITORY": "example/altitude", "FIXTURE_RUNS": runs, "FIXTURE_JOBS": jobs}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", self.gate], cwd=self.repo, env=env,
                              capture_output=True, text=True)

    def test_publishes_only_a_checked_commit_on_main(self):
        result = self.verify(self.main)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.tmp / "gh-args").read_text()
        self.assertIn(f"head_sha={self.main}&event=push&branch=main&status=success", calls)
        self.assertIn('select(.head_sha == $ENV.GITHUB_SHA)', calls)
        self.assertIn("repos/example/altitude/actions/runs/41/jobs", calls)
        self.assertIn('select(.name == "check" and .conclusion == "success")', calls)
        for runs, jobs in (("", "7\n"), ("41\n", "")):
            with self.subTest(runs=runs, jobs=jobs):
                unchecked = self.verify(self.main, runs=runs, jobs=jobs)
                self.assertNotEqual(unchecked.returncode, 0)
                self.assertIn("has no successful check job on main", unchecked.stdout)
        unmerged = self.verify(self.side)
        self.assertNotEqual(unmerged.returncode, 0)
        self.assertIn("does not point at a commit on main", unmerged.stdout)

    def test_job_holds_only_the_permissions_it_uses_and_uploads_every_release_file(self):
        self.assertIn("tags: ['v0.*']", self.workflow)
        self.assertIn("permissions: {}", self.workflow)
        job = self.workflow.split("jobs:")[1]
        self.assertIn("contents: write\n      actions: read\n      id-token: write\n      attestations: write", job)
        self.assertNotIn("secrets.", self.workflow)
        self.assertIn('"altitude-$VERSION.tar.gz" "altitude-$VERSION.tar.gz.sha256" install.py install.sh SHA256SUMS', job)
        self.assertIn("--notes-file", job)


class ApprovedPublication(AltitudeCase):
    """Real approvals, source/asset verification and audit; only GitHub is a stateful fixture."""

    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.version = "v0.1.0-rc.2"
        self.public_notes = ("- Fictional release.\n\nManual publication from an operator-approved owner build. "
                             "Checksums verify integrity; this release has no hosted build attestation.\n")
        self.repository = f"example/{self.project}"
        git("remote", "set-url", "origin", f"https://github.com/{self.repository}.git", cwd=self.repo)
        (self.repo / "CHANGELOG.md").write_text(f"# Changelog\n\n## {self.version} — 2026-10-06\n\n- Fictional release.\n")
        git("add", "CHANGELOG.md", cwd=self.repo)
        git("commit", "-qm", "Release notes", cwd=self.repo)
        self.sha = git("rev-parse", "HEAD", cwd=self.repo).strip()
        self.slug = T.new(self.project, "Publish the reviewed release", "Publish only the approved release.")["slug"]
        self.worktree = add_worktree(self.repo, self.slug)
        T.dispatch(self.project, self.slug, attempt=1, session_id="session", agent_id="agent",
                   worktree=str(self.worktree), branch=f"worktree-{self.slug}")
        self.directory = S.task_dir(self.project, self.slug) / "release"
        self.directory.mkdir()
        self.assets()
        self.remote_release = None
        self.remote_assets = {}
        self.remote_tag = None
        self.calls = []
        self.interrupt = None
        self.create_failure = None
        self.before_publish_read = None
        self.compare_status = "ahead"
        self.check_job = {"name": "check", "conclusion": "success"}
        self.check_run = {"id": 11, "head_sha": self.sha, "event": "push",
                          "head_branch": "main", "conclusion": "success"}
        self.patch(releases, "_api", side_effect=self.api)

    def assets(self, *, version=None, sha=None, repository=None):
        """A complete tiny application with the actual installer's manifest/archive rules."""
        contents = {name: b"fixture application resource\n" for name in installation.REQUIRED}
        contents["altitude/installation.py"] = b"# fictional standalone installer\n"
        metadata = {"version": version or self.version, "commit": sha or self.sha,
                    "repository": f"https://github.com/{repository or self.repository}",
                    "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
        contents["release.json"] = json.dumps(metadata).encode()
        archive = self.directory / f"altitude-{self.version}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name, data in contents.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
        (self.directory / "install.py").write_bytes(contents["altitude/installation.py"])
        archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        installer_hash = hashlib.sha256(contents["altitude/installation.py"]).hexdigest()
        (self.directory / "install.sh").write_text(build_release.install_script(
            (REPO / "scripts/install.sh").read_text(), self.version, f"https://github.com/{self.repository}",
            archive_hash, installer_hash))
        (self.directory / (archive.name + ".sha256")).write_text(archive_hash + "\n")
        self.checksums()

    def checksums(self):
        names = sorted((f"altitude-{self.version}.tar.gz", "install.py", "install.sh"))
        (self.directory / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((self.directory / name).read_bytes()).hexdigest()}  {name}\n" for name in names))

    def approval(self, text=None, **kwargs):
        return T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE,
                         text or f"Publish {self.version} from {self.sha}.", by=T.OPERATOR_MESSAGE_ROLE, **kwargs)

    def grant(self, approval=None, **kwargs):
        arguments = dict(version=self.version, sha=self.sha, files=str(self.directory), reason="Original approval agrees.",
                         actor="l2", expected_attempt=1)
        arguments.update(kwargs)
        return releases.grant(self.project, self.slug, (approval or self.approval())["id"], **arguments)

    def run_publish(self, **kwargs):
        return releases.run(self.project, self.slug, kwargs.pop("attempt", 1),
                            owner=kwargs.pop("owner", lambda task: task["agent_id"] == "agent"), **kwargs)

    def events(self, kind):
        return [row for row in S.read_events(self.project, self.slug) if row["kind"] == kind]

    def api(self, repository, path, *, method="GET", data=None, upload=None, missing=False):
        self.assertEqual(repository, self.repository)
        self.calls.append((method, path, copy.deepcopy(data)))
        route = path.split("?", 1)[0]
        if method == "GET":
            if route.startswith("compare/"):
                return {"status": self.compare_status, "merge_base_commit": {"sha": self.sha}}
            if route == "actions/workflows/self-hosted-checks.yml/runs":
                return {"workflow_runs": [self.check_run]}
            if route == "actions/runs/11/jobs":
                return {"jobs": [self.check_job]}
            if route == f"git/ref/tags/{self.version}":
                return copy.deepcopy(self.remote_tag)
            if route == "releases":
                return [copy.deepcopy(self.remote_release)] if self.remote_release else []
            if route == "releases/21/assets":
                return copy.deepcopy(list(self.remote_assets.values()))
            if route == "releases/21":
                if self.before_publish_read:
                    callback, self.before_publish_read = self.before_publish_read, None
                    callback()
                return copy.deepcopy(self.remote_release)
        if method == "POST" and route == "releases":
            if self.create_failure:
                failure, self.create_failure = self.create_failure, None
                raise failure
            self.assertIsNone(self.remote_release, "retry must never blindly create another draft")
            self.assertIsNone(self.remote_tag, "the manual path must not create a tag before uploading")
            self.assertTrue(data["draft"])
            self.remote_release = {"id": 21, **data}
            result, phase = self.remote_release, "create"
        elif method == "POST" and route == "releases/21/assets":
            self.assertTrue(self.remote_release["draft"])
            self.assertIsNone(self.remote_tag)
            name = upload.name
            self.assertNotIn(name, self.remote_assets, "retry must never replace or duplicate an upload")
            result = {"name": name, "state": "uploaded", "digest": "sha256:" + hashlib.sha256(upload.read_bytes()).hexdigest()}
            self.remote_assets[name] = result
            phase = "upload"
        elif method == "PATCH" and route == "releases/21":
            self.assertEqual(set(self.remote_assets), set(path.name for path in self.directory.iterdir()))
            self.assertEqual(data, {"draft": False, "make_latest": "false", "body": self.public_notes})
            self.remote_release.update(data)
            self.remote_tag = {"object": {"type": "commit", "sha": self.sha}}
            result, phase = self.remote_release, "publish"
        else:
            self.fail(f"unexpected GitHub operation: {method} {path}")
        if self.interrupt == phase:
            self.interrupt = None
            raise RuntimeError("fixture connection lost after remote write")
        return copy.deepcopy(result)

    def test_owner_records_operator_scope_and_publishes_draft_assets_then_tag(self):
        approval = self.approval()
        grant = self.grant(approval)
        self.assertEqual((grant["approval"], grant["repository"], grant["sha"], grant["attempt"]),
                         (approval["id"], self.repository, self.sha, 1))
        self.assertEqual(grant["notes"], self.public_notes)
        self.assertEqual(self.run_publish(check=True)["status"], "ready")
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))
        result = self.run_publish()
        self.assertEqual(result["status"], "published")
        self.assertFalse(result["already_published"])
        writes = [(method, path) for method, path, _ in self.calls if method != "GET"]
        self.assertEqual(writes[0], ("POST", "releases"))
        self.assertEqual(writes[-1], ("PATCH", "releases/21"))
        self.assertEqual(len(writes), 7)
        self.assertEqual(len(self.events("release-attempt")), 2)
        self.assertEqual(len(self.events("release-write")), 7)
        self.assertEqual(len(self.events("release-write-finished")), 7)
        self.assertEqual(len(self.events("release-completed")), 1)
        self.assertIsNone(releases.active_grant(S.load_task(self.project, self.slug)))
        self.assertTrue(self.run_publish()["already_published"])
        self.assertEqual(sum(method != "GET" for method, _, _ in self.calls), 7)
        with self.assertRaisesRegex(T.TransitionError, "consumed"):
            self.grant(approval)

    def test_commit_prefix_and_exact_historical_question_preserve_the_scope_seen(self):
        T.block(self.project, self.slug, f"Publish {self.version} at {self.sha[:9]}?", actor="l2",
                expected_state="running", expected_attempt=1, updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE})
        original = S.load_task(self.project, self.slug)["questions"][-1]
        answer = self.approval("Yes, publish.", question_id=original["id"], revision=original["revision"])
        T.resume(self.project, self.slug)
        T.block(self.project, self.slug, "Revised question", actor="l2", expected_state="running", expected_attempt=1,
                updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE}, questions={"questions": [
                    {"id": original["id"], "question": "May I publish v0.9.9 instead?"}]})
        changed = S.load_task(self.project, self.slug)["questions"][-1]
        T.resume(self.project, self.slug)
        with self.assertRaisesRegex(T.TransitionError, "different question revision"):
            self.grant(answer, question=changed["id"], revision=changed["revision"])
        grant = self.grant(answer, sha=self.sha[:9], question=original["id"], revision=original["revision"])
        self.assertEqual(grant["sha"], self.sha)
        self.assertEqual(grant["revision"], original["revision"])

    def test_project_approval_requires_original_identity_task_scope_and_coordinator(self):
        row = {"turn_id": "original-project-answer", "role": "user", "by": T.OPERATOR_MESSAGE_ROLE,
               "trigger": "chat", "at": S.now(), "text": f"Publish {self.version} at {self.sha}."}
        path = config.project_dir(self.project) / "chat.jsonl"
        path.write_text(json.dumps(row) + "\n")
        approval = {"id": row["turn_id"]}
        with self.assertRaisesRegex(T.TransitionError, "current owner records its task-chat"):
            self.grant(approval, source="project")
        with self.assertRaisesRegex(T.TransitionError, "identify this task"):
            self.grant(approval, source="project", actor="l3")
        row["text"] += f" For task {self.slug}."
        path.write_text(json.dumps(row) + "\n")
        grant = self.grant(approval, source="project", actor="l3")
        self.assertEqual((grant["approval"], grant["source"]), (row["turn_id"], "project"))

    def test_grant_rejects_wrong_scope_relay_missing_identity_and_wrong_attempt(self):
        valid = self.approval()
        relay = T.message(self.project, self.slug, "l3", valid["text"], by="l3")
        for approval, kwargs in ((relay, {}), ({"id": "absent"}, {}),
                                 (self.approval(f"Publish v0.9.9 at {self.sha}."), {}),
                                 (self.approval(f"Publish {self.version} at {'1' * 40}."), {}),
                                 (valid, {"expected_attempt": 2})):
            with self.subTest(approval=approval["id"], kwargs=kwargs), self.assertRaises(T.TransitionError):
                self.grant(approval, **kwargs)
        self.assertEqual(len(self.events("release-grant-refused")), 5)
        self.assertIsNone(S.load_task(self.project, self.slug).get("release_grant"))

    def test_project_contextual_yes_must_bind_this_tasks_exact_question(self):
        T.block(self.project, self.slug, f"Publish {self.version} at {self.sha}?", actor="l2",
                expected_state="running", expected_attempt=1, updates={"waiting_on": T.OPERATOR_MESSAGE_ROLE})
        question = S.load_task(self.project, self.slug)["questions"][-1]
        row = {"turn_id": "contextual-project-answer", "role": "user", "by": T.OPERATOR_MESSAGE_ROLE,
               "trigger": "chat", "at": S.now(), "text": "Yes, proceed with the other task."}
        path = config.project_dir(self.project) / "chat.jsonl"
        path.write_text(json.dumps(row) + "\n")
        args = dict(source="project", actor="l3", question=question["id"], revision=question["revision"])
        approval = {"id": row["turn_id"]}
        with self.assertRaisesRegex(T.TransitionError, "identify this task"):
            self.grant(approval, **args)
        row["text"] = "Yes."
        row["question_refs"] = [{"id": question["id"], "revision": question["revision"] + 1}]
        path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(T.TransitionError, "identify this task"):
            self.grant(approval, **args)
        row["question_refs"] = [{"id": question["id"], "revision": question["revision"]}]
        path.write_text(json.dumps(row) + "\n")
        result = self.grant(approval, **args)
        self.assertEqual((result["source"], result["question"], result["revision"]),
                         ("project", question["id"], question["revision"]))

    def test_wrong_owner_or_attempt_cannot_even_read_remote_state_and_is_audited(self):
        self.grant()
        for kwargs in ({"owner": lambda task: False}, {"attempt": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionError):
                self.run_publish(**kwargs)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.events("release-refused")), 2)

    def test_revocation_refuses_publish_and_reuse_of_original_approval(self):
        approval = self.approval()
        self.grant(approval)
        releases.revoke(self.project, self.slug, "Operator withdrew publication.", actor="l3")
        with self.assertRaises(PermissionError):
            self.run_publish()
        with self.assertRaisesRegex(T.TransitionError, "revoked"):
            self.grant(approval)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.events("release-revoked")), 1)

    def test_expiry_new_attempt_and_finished_task_invalidate_grant(self):
        self.grant(expires="2100-01-01T00:00:00+00:00")
        original = S.load_task(self.project, self.slug)
        for changes in ({"attempt": 2}, {"state": "done"},
                        {"release_grant": {**original["release_grant"], "expires_at": "2000-01-01T00:00:00+00:00"}}):
            S.save_task(self.project, {**original, **changes})
            with self.subTest(changes=changes), self.assertRaises(PermissionError):
                self.run_publish(attempt=changes.get("attempt", 1))
        self.assertEqual(self.calls, [])

    def test_asset_changes_after_approval_refuse_before_remote_effects(self):
        self.grant()
        (self.directory / "install.py").write_text("substituted installer")
        with self.assertRaisesRegex(ValueError, "files changed"):
            self.run_publish()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.events("release-failed")), 1)

    def test_archive_identity_must_match_approved_repository_version_and_sha(self):
        approval = self.approval()
        for changes in ({"version": "v0.9.9"}, {"sha": "1" * 40}, {"repository": "elsewhere/altitude"}):
            self.assets(**changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(T.TransitionError, "archive does not match"):
                self.grant(approval)
        self.assertEqual(self.calls, [])

    def test_exact_asset_set_checksum_and_symlink_requirements(self):
        approval = self.approval()
        extra = self.directory / "private.txt"
        extra.write_text("fictional unrelated file")
        with self.assertRaisesRegex(T.TransitionError, "exactly the five"):
            self.grant(approval)
        extra.unlink()
        sums = self.directory / "SHA256SUMS"
        original = sums.read_bytes()
        sums.write_text("incorrect checksum")
        with self.assertRaisesRegex(T.TransitionError, "checksums"):
            self.grant(approval)
        sums.write_bytes(original)
        target = self.tmp / "sums"
        sums.rename(target)
        sums.symlink_to(target)
        with self.assertRaises(T.TransitionError):
            self.grant(approval)
        sums.unlink()
        target.rename(sums)
        linked = self.directory.parent / "linked-release"
        linked.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(T.TransitionError):
            self.grant(approval, files=str(linked))
        with self.assertRaisesRegex(T.TransitionError, "subdirectory"):
            self.grant(approval, files=str(self.tmp))

    def test_malformed_archive_is_audited_as_refused_grant(self):
        archive = self.directory / f"altitude-{self.version}.tar.gz"
        archive.write_bytes(b"invalid archive despite internally consistent checksums")
        (self.directory / (archive.name + ".sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")
        self.checksums()
        with self.assertRaises(T.TransitionError):
            self.grant()
        self.assertEqual(len(self.events("release-grant-refused")), 1)
        self.assertIsNone(S.load_task(self.project, self.slug).get("release_grant"))

    def test_interrupted_upload_reconciles_without_replacing_any_asset(self):
        self.grant()
        self.interrupt = "upload"
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            self.run_publish()
        self.assertIsNone(self.remote_tag)
        self.assertEqual(len(self.remote_assets), 1)
        self.assertEqual(self.run_publish()["status"], "published")
        self.assertEqual(sum(method == "POST" and path == "releases" for method, path, _ in self.calls), 1)
        self.assertEqual(len(self.events("release-attempt")), 2)
        self.assertEqual(len(self.events("release-failed")), 1)
        self.assertEqual(len(self.events("release-write")), 7)
        self.assertEqual(len(self.events("release-write-finished")), 6)

    def test_interrupted_publication_reads_back_success_without_repeating_write(self):
        self.grant()
        self.interrupt = "publish"
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            self.run_publish()
        self.assertFalse(self.remote_release["draft"])
        self.assertTrue(self.run_publish()["already_published"])
        self.assertEqual(sum(method == "PATCH" for method, _, _ in self.calls), 1)
        self.assertEqual(len(self.events("release-completed")), 1)

    def test_uncertain_draft_creation_reconciles_only_its_persisted_receipt(self):
        self.grant()
        self.interrupt = "create"
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            self.run_publish()
        original = self.remote_release["body"]
        self.remote_release["body"] = self.public_notes
        with self.assertRaisesRegex(ValueError, "notes differ"):
            self.run_publish()
        self.assertEqual(sum(method != "GET" for method, _, _ in self.calls), 1)
        self.assertEqual(self.remote_assets, {})
        self.assertIsNone(self.remote_tag)
        self.remote_release["body"] = original
        self.assertEqual(self.run_publish()["status"], "published")
        self.assertEqual(self.remote_release["body"], self.public_notes)
        self.assertEqual(sum(method == "POST" and path == "releases" for method, path, _ in self.calls), 1)
        self.assertEqual(len(self.events("release-reconciled")), 1)

    def test_definite_create_refusal_is_audited_and_can_retry(self):
        self.grant()
        self.create_failure = releases.APIError("fixture HTTP 403", refused=True)
        with self.assertRaises(releases.APIError):
            self.run_publish()
        self.assertIsNone(self.remote_release)
        self.assertIsNone(self.remote_tag)
        self.assertEqual(self.events("release-write-refused")[0]["phase"], "create")
        self.assertEqual(self.run_publish()["status"], "published")
        self.assertEqual(sum(method == "POST" and path == "releases" for method, path, _ in self.calls), 2)

    def test_uncertain_create_without_remote_receipt_never_blindly_retries(self):
        self.grant()
        self.create_failure = releases.APIError("fixture outcome unknown")
        with self.assertRaises(releases.APIError):
            self.run_publish()
        with self.assertRaisesRegex(ValueError, "creation outcome is uncertain"):
            self.run_publish()
        self.assertEqual(sum(method == "POST" and path == "releases" for method, path, _ in self.calls), 1)
        self.assertIsNone(self.remote_tag)

    def test_revocation_after_upload_stops_before_publication(self):
        self.grant()
        self.before_publish_read = lambda: releases.revoke(self.project, self.slug, "Stop now.", actor="l3")
        with self.assertRaises(PermissionError):
            self.run_publish()
        self.assertTrue(self.remote_release["draft"])
        self.assertEqual(len(self.remote_assets), 5)
        self.assertIsNone(self.remote_tag)
        self.assertEqual(len(self.events("release-refused")), 1)

    def test_remote_asset_conflict_or_foreign_draft_never_gets_replaced(self):
        self.grant()
        self.interrupt = "upload"
        with self.assertRaises(RuntimeError):
            self.run_publish()
        first = next(iter(self.remote_assets.values()))
        first["digest"] = "sha256:" + "0" * 64
        writes = sum(method != "GET" for method, _, _ in self.calls)
        with self.assertRaisesRegex(ValueError, "remote asset differs"):
            self.run_publish()
        self.assertEqual(sum(method != "GET" for method, _, _ in self.calls), writes)
        self.remote_release["id"] = 22
        with self.assertRaisesRegex(ValueError, "recorded draft"):
            self.run_publish()

    def test_tag_conflict_or_unclaimed_tag_refuses_without_modification(self):
        self.grant()
        for sha, error in (("1" * 40, "approved commit"), (self.sha, "another publisher")):
            self.remote_tag = {"object": {"type": "commit", "sha": sha}}
            with self.subTest(sha=sha), self.assertRaisesRegex(ValueError, error):
                self.run_publish()
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))

    def test_removed_task_message_is_not_approval(self):
        approval = self.approval()
        T.remove_message(self.project, self.slug, approval["id"])
        with self.assertRaisesRegex(T.TransitionError, "original message"):
            self.grant(approval)
        self.assertEqual(len(self.events("release-grant-refused")), 1)

    def test_deadline_must_be_future_and_timezone_aware(self):
        approval = self.approval()
        for deadline in ("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00"):
            with self.subTest(deadline=deadline), self.assertRaisesRegex(T.TransitionError, "future timestamp"):
                self.grant(approval, expires=deadline)

    def test_second_task_cannot_reuse_message_or_adopt_first_tasks_draft(self):
        first_slug, first_dir = self.slug, self.directory
        approval = self.approval()
        self.grant(approval)
        self.interrupt = "upload"
        with self.assertRaises(RuntimeError):
            self.run_publish()
        self.slug = T.new(self.project, "Another release task", "A distinct owner.")["slug"]
        worktree = add_worktree(self.repo, self.slug)
        T.dispatch(self.project, self.slug, attempt=1, session_id="other-session", agent_id="other-agent",
                   worktree=str(worktree), branch=f"worktree-{self.slug}")
        self.directory = S.task_dir(self.project, self.slug) / "release"
        shutil.copytree(first_dir, self.directory)
        with self.assertRaisesRegex(T.TransitionError, "original message"):
            self.grant(approval)
        self.grant()
        before = len(self.calls)
        with self.assertRaisesRegex(ValueError, "belongs to another task"):
            self.run_publish(owner=lambda task: task["agent_id"] == "other-agent")
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(self.remote_assets), 1)
        self.assertIsNone(self.remote_tag)
        self.assertIsNotNone(S.load_task(self.project, first_slug)["release_grant"])

    def test_new_attempt_can_regrant_identical_unrevoked_approval_and_resume_draft(self):
        approval = self.approval()
        grant = self.grant(approval)
        self.interrupt = "upload"
        with self.assertRaises(RuntimeError):
            self.run_publish()
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "attempt": 2})
        with self.assertRaises(PermissionError):
            self.run_publish(attempt=2)
        replacement = self.grant(approval, expected_attempt=2)
        self.assertNotEqual(replacement["id"], grant["id"])
        self.assertEqual(replacement["hashes"], grant["hashes"])
        self.assertEqual(self.run_publish(attempt=2)["status"], "published")
        self.assertEqual(sum(method == "POST" and path == "releases" for method, path, _ in self.calls), 1)

    def test_unregistered_or_path_project_cannot_create_files_through_release_verbs(self):
        outside = self.tmp / "escaped-release-project"
        unknown = f"unregistered-{self.project}"
        cases = ((unknown, config.project_dir(unknown)), (str(outside), outside),
                 (f"../{self.tmp.name}/escaped-release-project", outside))
        for project, directory in cases:
            operations = {
                "grant": lambda: releases.grant(project, self.slug, "untrusted-answer", version=self.version,
                                                sha=self.sha, files=str(self.directory), reason="Reject unknown project.",
                                                actor="l3"),
                "revoke": lambda: releases.revoke(project, self.slug, "Reject unknown project.", actor="l3"),
                "run": lambda: releases.run(project, self.slug, 1, owner=lambda task: True),
            }
            for name, operation in operations.items():
                with self.subTest(project=project, operation=name):
                    self.assertFalse(directory.exists())
                    with self.assertRaisesRegex(KeyError, "unknown project"):
                        operation()
                    self.assertFalse(directory.exists(), "rejected identity must not create lock, audit or task files")
        self.assertEqual(self.calls, [])

    def test_original_approval_keeps_deadline_and_assets_after_intervening_grant(self):
        approval_a = self.approval()
        deadline = "2100-01-01T00:00:00+00:00"
        grant_a = self.grant(approval_a, expires=deadline)
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "attempt": 2})
        approval_b = self.approval("I separately approve " + self.version + " from " + self.sha + ".")
        grant_b = self.grant(approval_b, expected_attempt=2)
        self.assertNotEqual(grant_a["approval"], grant_b["approval"])
        task = S.load_task(self.project, self.slug)
        S.save_task(self.project, {**task, "attempt": 3})
        for expires in (None, "2101-01-01T00:00:00+00:00"):
            with self.subTest(expires=expires), self.assertRaisesRegex(T.TransitionError, "retain.*deadline"):
                self.grant(approval_a, expected_attempt=3, expires=expires)
        script = self.directory / "install.sh"
        original_script = script.read_bytes()
        script.write_bytes(original_script + b"\n# changed owner build\n")
        self.checksums()
        with self.assertRaisesRegex(T.TransitionError, "retain.*release"):
            self.grant(approval_a, expected_attempt=3, expires=deadline)
        script.write_bytes(original_script)
        self.checksums()
        restored = self.grant(approval_a, expected_attempt=3, expires=deadline)
        self.assertEqual((restored["expires_at"], restored["hashes"], restored["sha"]),
                         (grant_a["expires_at"], grant_a["hashes"], grant_a["sha"]))
        self.assertEqual(len(self.events("release-granted")), 3)
        self.assertEqual(len(self.events("release-grant-refused")), 3)

    def test_main_gate_requires_exact_commit_push_run_and_successful_check_job(self):
        self.grant()
        self.compare_status = "diverged"
        with self.assertRaisesRegex(ValueError, "not on GitHub main"):
            self.run_publish()
        self.compare_status = "ahead"
        original = self.check_run.copy()
        for change in ({"head_sha": "1" * 40}, {"event": "pull_request"}, {"head_branch": "other"},
                       {"conclusion": "failure"}):
            self.check_run = {**original, **change}
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "no successful main"):
                self.run_publish()
        self.check_run = original
        for job in ({"name": "different", "conclusion": "success"}, {"name": "check", "conclusion": "failure"}):
            self.check_job = job
            with self.subTest(job=job), self.assertRaisesRegex(ValueError, "no successful main"):
                self.run_publish()
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))

    def test_http_admits_only_current_worker_and_fixed_fields(self):
        self.grant()
        root = dispatch.l2_job_root(self.project, self.slug)
        root.mkdir(parents=True, exist_ok=True)
        engine = config.PRIMARY_DEFAULT_ENGINE
        unit = getattr(engines, f"_{engine}_unit")("agent")
        S.write_json(root / "agent.json", {"id": "agent", "engine": engine, "unit": unit})
        native_owner = self.patch(terminal, "owner_connection", return_value=False)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.shutdown)

        def request(**changes):
            body = {"project": self.project, "slug": self.slug, "attempt": 1, "check": True, **changes}
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
            try:
                connection.request("POST", "/api/task/publish", body=json.dumps(body),
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        status, result = request()
        self.assertEqual(status, 403, result)
        self.assertEqual(self.calls, [])
        self.assertTrue(native_owner.called, "HTTP must use the actual worker-connection authority seam")
        self.assertEqual(native_owner.call_args.args[2], unit)
        native_owner.return_value = True
        self.assertEqual(request(attempt=2)[0], 403)
        for changes in ({"command": "gh release create other"}, {"version": "v0.9.9"}, {"check": "false"}):
            with self.subTest(changes=changes):
                self.assertEqual(request(**changes)[0], 400)
        status, result = request()
        self.assertEqual((status, result["status"]), (200, "ready"))
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))
        status, result = request(check=False)
        self.assertEqual((status, result["status"]), (200, "published"))


if __name__ == "__main__":
    unittest.main()
