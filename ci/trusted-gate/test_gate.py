from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "trusted-remote-tests.yml"
CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
UPLOAD_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
BASE = "a" * 40
CANDIDATE = "b" * 40
NONCE = "c" * 64


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RESOLVER = load_module("trusted_gate_resolver", HERE / "resolve_run_context.py")
VERIFIER = load_module("trusted_gate_verifier", HERE / "verify_manifest.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pr_json() -> dict[str, object]:
    return {
        "id": 9900, "number": 99,
        "base": {"ref": "main", "sha": BASE,
                 "repo": {"full_name": "owner/base", "id": 10}},
        "head": {"sha": CANDIDATE,
                 "repo": {"full_name": "fork/base", "id": 20}},
    }


def jobs_json() -> dict[str, object]:
    return {"jobs": [{
        "id": 400, "run_id": 200, "run_attempt": 3,
        "name": "trusted-remote / evidence", "head_sha": BASE,
        "check_run_url": "https://api.github.com/repos/owner/base/check-runs/400",
    }]}


def resolve(event_name: str, event: dict[str, object], pull: dict[str, object] | None = None):
    return RESOLVER.resolve(
        event_name, event, pull, jobs_json(), base_repository="owner/base",
        base_repository_id=10, base_sha=BASE, workflow_sha=BASE,
        run_id=200, run_attempt=3,
    )


class TrustedGateStaticTests(unittest.TestCase):
    def test_workflow_uses_base_trust_and_immutable_evidence(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("pull_request_target:", workflow)
        self.assertIn("actions: read\n  contents: read\n  pull-requests: read", workflow)
        self.assertEqual(workflow.count(f"actions/checkout@{CHECKOUT_SHA}"), 2)
        self.assertEqual(workflow.count(f"actions/upload-artifact@{UPLOAD_SHA}"), 1)
        self.assertNotRegex(workflow, r"uses:\s+[^\n]+@(v\d+|main|master)\s*$")
        self.assertEqual(workflow.count("persist-credentials: false"), 2)
        self.assertEqual(workflow.count("set-safe-directory: false"), 2)
        self.assertEqual(workflow.count("allow-unsafe-pr-checkout: true"), 1)
        trusted_checkout, candidate_and_after = workflow.split(
            "- name: Checkout candidate PR ref as inert data", 1
        )
        candidate_checkout = candidate_and_after.split("- name: Package inert candidate", 1)[0]
        self.assertNotIn("allow-unsafe-pr-checkout", trusted_checkout)
        self.assertIn("allow-unsafe-pr-checkout: true", candidate_checkout)
        self.assertIn("request_nonce:", workflow)
        self.assertIn("expected_base_sha:", workflow)
        self.assertIn("refs/pull/{pr_number}/head", (HERE / "resolve_run_context.py").read_text())
        self.assertIn("sudo -n /usr/bin/env -i", workflow)
        self.assertNotIn("candidate/Makefile", workflow)

        lines = workflow.splitlines()
        for index, line in enumerate(lines):
            if not line.lstrip().startswith("run: |"):
                continue
            run_indent = len(line) - len(line.lstrip())
            script = []
            for script_line in lines[index + 1:]:
                if script_line.strip() and len(script_line) - len(script_line.lstrip()) <= run_indent:
                    break
                script.append(script_line)
            self.assertNotIn("${{ inputs.", "\n".join(script))

    def test_launcher_and_pid1_declare_every_required_boundary(self):
        launcher = (HERE / "launch.sh").read_text()
        pid1 = (HERE / "pid1.c").read_text()
        boundary = (HERE / "boundary_selftest.py").read_text()
        for required in (
            "set -euo pipefail", "--mount --pid --fork", "--net --ipc --uts --cgroup",
            "cgroup.kill", "kill_empty_remove_cgroup", "altitude-gate-usr", "hidepid=2",
            "mount_exactly", "flock --exclusive --nonblock", "<>/usr/bin/python3",
        ):
            self.assertIn(required, launcher)
        for required in (
            "__X32_SYSCALL_BIT", "BPF_JSET", "SECCOMP_RET_KILL_PROCESS", "AF_UNIX",
            "AF_INET", "AF_INET6", "__NR_socketcall", "__NR_io_uring_setup",
            "F_SETLK", "F_WRLCK", "PID1_GLOBAL_SECONDS", "_Static_assert",
        ):
            self.assertIn(required, pid1)
        for required in ("AF_VSOCK", "io_uring_setup", "signal.SIGSYS", "fcntl.lockf"):
            self.assertIn(required, boundary)

    def test_phase_budgets_fit_the_global_wall(self):
        self.assertEqual(sum(VERIFIER.PHASE_SECONDS.values()), 1100)
        self.assertLessEqual(
            sum(VERIFIER.PHASE_SECONDS.values()) + VERIFIER.LIMITS["teardown_reserve_seconds"],
            VERIFIER.LIMITS["wall_seconds"],
        )


class ResolverTests(unittest.TestCase):
    def test_pull_request_and_dispatch_bind_exact_current_pair(self):
        event = {"action": "synchronize", "pull_request": pr_json()}
        automatic = resolve("pull_request_target", event)
        self.assertEqual(automatic["candidate_fetch_ref"], "refs/pull/99/head")
        self.assertEqual(automatic["pull_request_id"], "9900")
        self.assertRegex(automatic["request_nonce"], r"[0-9a-f]{64}")
        dispatch_event = {
            "ref": "refs/heads/main",
            "inputs": {
                "pull_request_number": "99", "candidate_repository": "fork/base",
                "candidate_sha": CANDIDATE, "expected_base_sha": BASE,
                "request_nonce": NONCE,
            },
        }
        manual = resolve("workflow_dispatch", dispatch_event, pr_json())
        self.assertEqual(manual["request_nonce"], NONCE)
        self.assertEqual(manual["pull_request_id"], "9900")
        self.assertEqual(manual["check_run_id"], "400")
        self.assertEqual(
            manual["artifact_name"], f"trusted-remote-evidence-200-3-{NONCE}"
        )
        push = resolve("push", {"ref": "refs/heads/main", "after": BASE})
        self.assertEqual(push["pull_request_id"], "0")
        self.assertEqual(push["pull_request_number"], "0")

    def test_resolver_rejects_every_stale_dispatch_identity(self):
        base_event = {
            "ref": "refs/heads/main",
            "inputs": {
                "pull_request_number": "99", "candidate_repository": "fork/base",
                "candidate_sha": CANDIDATE, "expected_base_sha": BASE,
                "request_nonce": NONCE,
            },
        }
        mutations = {
            "pull_request_number": "98",
            "candidate_repository": "other/base",
            "candidate_sha": "d" * 40,
            "expected_base_sha": "e" * 40,
            "request_nonce": "f" * 63,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                event = copy.deepcopy(base_event)
                event["inputs"][key] = value  # type: ignore[index]
                with self.assertRaises(RESOLVER.ContextError):
                    resolve("workflow_dispatch", event, pr_json())

    def test_resolver_rejects_wrong_base_and_check_run(self):
        event = {"action": "synchronize", "pull_request": pr_json()}
        bad_pr = pr_json()
        bad_pr["base"]["sha"] = "d" * 40  # type: ignore[index]
        with self.assertRaises(RESOLVER.ContextError):
            resolve("pull_request_target", {"action": "synchronize", "pull_request": bad_pr})
        bad_id = pr_json()
        bad_id["id"] = 0
        with self.assertRaises(RESOLVER.ContextError):
            resolve("pull_request_target", {"action": "synchronize", "pull_request": bad_id})
        bad_jobs = jobs_json()
        bad_jobs["jobs"][0]["check_run_url"] = (  # type: ignore[index]
            "https://api.github.com/repos/owner/base/check-runs/401"
        )
        with self.assertRaises(RESOLVER.ContextError):
            RESOLVER.resolve(
                "pull_request_target", event, None, bad_jobs,
                base_repository="owner/base", base_repository_id=10,
                base_sha=BASE, workflow_sha=BASE, run_id=200, run_attempt=3,
            )


class RunnerTests(unittest.TestCase):
    def run_fixture(self, body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="altitude-trusted-runner-") as raw:
            root = Path(raw) / "suite"
            tests = root / "tests"
            tests.mkdir(parents=True)
            (tests / "test_case.py").write_text(body)
            return subprocess.run(
                [sys.executable, "-I", str(HERE / "trusted_runner.py"), "--label", "base",
                 "--root", str(root), "--tests", str(tests)],
                capture_output=True, text=True, check=False,
            )

    def test_runner_reports_nonempty_execution(self):
        result = self.run_fixture(textwrap.dedent("""
            import unittest
            class TestOK(unittest.TestCase):
                def test_ok(self): self.assertEqual(2 + 2, 4)
        """))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ALTITUDE_TRUSTED_RESULT label=base tests=1", result.stdout)

    def test_runner_rejects_all_skipped(self):
        result = self.run_fixture(textwrap.dedent("""
            import unittest
            class TestSkipped(unittest.TestCase):
                @unittest.skip("adversarial")
                def test_skip(self): pass
        """))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful=0", result.stdout)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="altitude-manifest-")
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "output.log"
        self.mounts = self.directory / "mounts.txt"
        self.pid1 = self.directory / "pid1"
        self.candidate_archive = self.directory / "candidate.tar"
        self.base_archive = self.directory / "base-tests.tar"
        self.manifest_path = self.directory / "manifest.json"
        self.output.write_bytes(b"bounded trusted output\n")
        self.mounts.write_text(
            "schema=1\n"
            "root\ttmpfs\tnodev,nosuid,rw\n"
            "usr\toverlay\tlowerdir=/usr,nodev,nosuid,ro\n"
            "dev\ttmpfs\tnodev,noexec,nosuid,rw\n"
            "dev-shm\ttmpfs\tnodev,noexec,nosuid,rw\n"
            "proc\tproc\thidepid=2,nodev,noexec,nosuid,rw\n"
            "gate\tbind\tnodev,noexec,nosuid,rw\n"
        )
        self.pid1.write_bytes(b"trusted pid1 binary")
        self.candidate_archive.write_bytes(b"candidate archive")
        self.base_archive.write_bytes(b"base tests archive")
        harness_sha, components = VERIFIER.harness_records(ROOT, self.pid1)
        identity = {
            "base_repository": "owner/base", "base_repository_id": 10,
            "candidate_repository": "fork/base", "candidate_repository_id": 20,
            "event_name": "workflow_dispatch", "event_action": "retest",
            "pull_request_id": 9900, "pull_request_number": 99,
            "run_id": 200, "run_attempt": 3,
            "check_run_id": 400, "workflow_path": VERIFIER.WORKFLOW_PATH,
            "workflow_sha": BASE, "request_nonce": NONCE,
            "artifact_name": f"trusted-remote-evidence-200-3-{NONCE}",
            "base_sha": BASE, "candidate_sha": CANDIDATE,
        }
        result = {
            "status": 0, "tests": 4, "failures": 0, "errors": 0, "skipped": 1,
            "expected_failures": 0, "unexpected_successes": 0, "successful": 1,
        }
        self.manifest = {
            "schema": 2, "identity": identity,
            "harness": {"sha256": harness_sha, "components": components},
            "inputs": {
                "candidate_archive": VERIFIER.file_record(self.candidate_archive, 536870912),
                "base_tests_archive": VERIFIER.file_record(self.base_archive, 134217728),
                "pid1": VERIFIER.file_record(self.pid1, 4194304),
            },
            "actions": {"checkout": CHECKOUT_SHA, "upload_artifact": UPLOAD_SHA},
            "limits": VERIFIER.LIMITS, "commands": VERIFIER.COMMANDS,
            "boundary_status": 0, "base": copy.deepcopy(result),
            "candidate": copy.deepcopy(result), "namespace_status": 0,
            "log_truncated": 0, "gate_ok": 1,
            "output": {"sha256": sha(self.output.read_bytes()), "bytes": self.output.stat().st_size},
            "mounts": {"sha256": sha(self.mounts.read_bytes()), "bytes": self.mounts.stat().st_size},
            "status": 0,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, **overrides: str) -> list[str]:
        values = {
            "base-repository": "owner/base", "base-repository-id": "10",
            "candidate-repository": "fork/base", "candidate-repository-id": "20",
            "event-name": "workflow_dispatch", "event-action": "retest",
            "pull-request-id": "9900", "pull-request-number": "99",
            "run-id": "200", "run-attempt": "3",
            "check-run-id": "400", "workflow-path": VERIFIER.WORKFLOW_PATH,
            "workflow-sha": BASE, "request-nonce": NONCE,
            "artifact-name": f"trusted-remote-evidence-200-3-{NONCE}",
            "base": BASE, "candidate": CANDIDATE,
        }
        values.update(overrides)
        command = [
            sys.executable, str(HERE / "verify_manifest.py"),
            "--manifest", str(self.manifest_path), "--output", str(self.output),
            "--mounts", str(self.mounts), "--trusted-dir", str(ROOT),
            "--pid1", str(self.pid1), "--candidate-archive", str(self.candidate_archive),
            "--base-tests-archive", str(self.base_archive),
        ]
        for key, value in values.items():
            command.extend((f"--{key}", value))
        return command

    def run_verify(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        self.manifest_path.write_text(json.dumps(self.manifest))
        return subprocess.run(self.command(**overrides), capture_output=True, text=True, check=False)

    def test_valid_manifest_recomputes_every_digest(self):
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in (self.output, self.mounts, self.pid1,
                     self.candidate_archive, self.base_archive):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"tamper")
                failed = self.run_verify()
                self.assertNotEqual(failed.returncode, 0)
                path.write_bytes(original)

    def test_every_identity_mutation_is_rejected(self):
        mutations = {
            "base_repository": "other/base", "base_repository_id": 11,
            "candidate_repository": "other/fork", "candidate_repository_id": 21,
            "event_name": "push", "event_action": "push", "pull_request_id": 9901,
            "pull_request_number": 98,
            "run_id": 201, "run_attempt": 4, "check_run_id": 401,
            "workflow_path": ".github/workflows/other.yml", "workflow_sha": "d" * 40,
            "request_nonce": "e" * 64, "artifact_name": "trusted-remote-evidence-bad",
            "base_sha": "f" * 40, "candidate_sha": "1" * 40,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                original = self.manifest["identity"][key]
                self.manifest["identity"][key] = value
                self.assertNotEqual(self.run_verify().returncode, 0)
                self.manifest["identity"][key] = original

    def test_wrong_expected_identity_and_all_skipped_are_rejected(self):
        self.assertNotEqual(self.run_verify(**{"candidate": "d" * 40}).returncode, 0)
        for label in ("base", "candidate"):
            with self.subTest(label=label):
                self.manifest[label]["tests"] = 2
                self.manifest[label]["skipped"] = 2
                self.assertNotEqual(self.run_verify().returncode, 0)
                self.manifest[label]["tests"] = 4
                self.manifest[label]["skipped"] = 1

    def test_duplicate_schema_and_mount_tamper_are_rejected(self):
        duplicate = json.dumps(self.manifest).replace('{"schema": 2,', '{"schema": 2, "schema": 2,', 1)
        self.manifest_path.write_text(duplicate)
        self.assertNotEqual(
            subprocess.run(self.command(), capture_output=True, text=True, check=False).returncode, 0
        )
        original = self.mounts.read_text()
        self.mounts.write_text(original.replace("nodev,noexec,nosuid,rw", "noexec,nosuid,rw", 1))
        self.assertNotEqual(self.run_verify().returncode, 0)

    def test_harness_digest_is_path_independent(self):
        first, _ = VERIFIER.harness_records(ROOT, self.pid1)
        with tempfile.TemporaryDirectory(prefix="altitude-harness-copy-") as raw:
            copied = Path(raw)
            for relative in (
                VERIFIER.WORKFLOW_PATH, "ci/trusted-gate/launch.sh",
                "ci/trusted-gate/trusted_runner.py", "ci/trusted-gate/boundary_selftest.py",
                "ci/trusted-gate/verify_manifest.py", "ci/trusted-gate/resolve_run_context.py",
            ):
                target = copied / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            second, _ = VERIFIER.harness_records(copied, self.pid1)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
