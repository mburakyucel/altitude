#!/usr/bin/python3
"""Recompute and verify one base-attached exact-pair evidence manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
UPLOAD_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
WORKFLOW_PATH = ".github/workflows/trusted-remote-tests.yml"
PHASE_SECONDS = {
    "extract_candidate": 60,
    "extract_base_copy": 60,
    "remove_candidate_tests": 20,
    "overlay_base_tests": 40,
    "boundary": 50,
    "base": 435,
    "candidate": 435,
}
LIMITS = {
    "pids": 256,
    "memory_bytes": 2147483648,
    "cpu_quota": "200000 100000",
    "wall_seconds": 1200,
    "teardown_reserve_seconds": 100,
    "log_bytes": 16777216,
    "phase_seconds": PHASE_SECONDS,
}
COMPONENTS = (
    "workflow.yml", "launch.sh", "pid1", "trusted_runner.py",
    "boundary_selftest.py", "verify_manifest.py", "resolve_run_context.py",
)
COMMANDS = [
    "/usr/bin/python3 -I /opt/gate/boundary_selftest.py",
    "/usr/bin/python3 -I /opt/gate/trusted_runner.py --label base --root "
    "/work/base-suite/source --tests /work/base-suite/source/tests",
    "/usr/bin/python3 -I /opt/gate/trusted_runner.py --label candidate --root "
    "/work/candidate-suite/source --tests /work/candidate-suite/source/tests",
]


def fail(message: str) -> None:
    raise SystemExit(message)


def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def regular(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        fail(f"trusted evidence input is not a regular file: {path}")
    stat = path.stat()
    if stat.st_nlink != 1 or stat.st_size < 0 or stat.st_size > maximum:
        fail(f"trusted evidence input has an unsafe size/link count: {path}")
    return path.read_bytes()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        fail(f"{label} is not an integer in range")
    return value


def parse_cli_integer(value: str, label: str, *, minimum: int = 0) -> int:
    if not value.isascii() or not value.isdecimal():
        fail(f"{label} is not an unsigned integer")
    parsed = int(value)
    if parsed < minimum:
        fail(f"{label} is outside its allowed range")
    return parsed


def exact_sha(value: str, label: str) -> str:
    if not HEX40.fullmatch(value):
        fail(f"{label} is not an exact lowercase commit SHA")
    return value


def exact_repository(value: str, label: str) -> str:
    if not REPOSITORY.fullmatch(value):
        fail(f"{label} is not owner/repository")
    return value


def file_record(path: Path, maximum: int) -> dict[str, object]:
    data = regular(path, maximum)
    if not data:
        fail(f"trusted evidence input is empty: {path}")
    return {"sha256": digest(data), "bytes": len(data)}


def harness_records(trusted: Path, pid1: Path) -> tuple[str, dict[str, str]]:
    paths = {
        "workflow.yml": trusted / WORKFLOW_PATH,
        "launch.sh": trusted / "ci/trusted-gate/launch.sh",
        "pid1": pid1,
        "trusted_runner.py": trusted / "ci/trusted-gate/trusted_runner.py",
        "boundary_selftest.py": trusted / "ci/trusted-gate/boundary_selftest.py",
        "verify_manifest.py": trusted / "ci/trusted-gate/verify_manifest.py",
        "resolve_run_context.py": trusted / "ci/trusted-gate/resolve_run_context.py",
    }
    components = {name: digest(regular(paths[name], 4 * 1024 * 1024)) for name in COMPONENTS}
    canonical = "".join(f"{components[name]}  {name}\n" for name in COMPONENTS).encode()
    return digest(canonical), components


def require_file_digest(record: Any, actual: dict[str, object], label: str) -> None:
    if record != actual:
        fail(f"{label} digest/size does not match evidence bytes")


def validate_mounts(data: bytes) -> None:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        fail(f"mount attestation is not UTF-8: {exc}")
    if not lines or lines[0] != "schema=1" or len(lines) != 7:
        fail("mount attestation schema/line count changed")
    expected = {
        "root": ("tmpfs", {"rw", "nosuid", "nodev"}, {"noexec"}),
        "usr": ("overlay", {"ro", "nosuid", "nodev", "lowerdir=/usr"}, {"rw"}),
        "dev": ("tmpfs", {"rw", "nosuid", "nodev", "noexec"}, set()),
        "dev-shm": ("tmpfs", {"rw", "nosuid", "nodev", "noexec"}, set()),
        "proc": ("proc", {"rw", "nosuid", "nodev", "noexec", "hidepid=2"}, set()),
        "gate": ("bind", {"rw", "nosuid", "nodev", "noexec"}, set()),
    }
    if [line.split("\t", 1)[0] for line in lines[1:]] != list(expected):
        fail("mount attestation order changed")
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 3:
            fail("mount attestation field count changed")
        name, fstype, raw_options = fields
        wanted_fstype, required, forbidden = expected[name]
        options = raw_options.split(",")
        if fstype != wanted_fstype or options != sorted(options) or len(options) != len(set(options)):
            fail(f"mount attestation is not canonical for {name}")
        option_set = set(options)
        if not required <= option_set or forbidden & option_set:
            fail(f"mount attestation options are unsafe for {name}")


def validate_suite(result: Any, label: str) -> None:
    fields = {
        "status", "tests", "failures", "errors", "skipped", "expected_failures",
        "unexpected_successes", "successful",
    }
    if not isinstance(result, dict) or set(result) != fields:
        fail(f"unexpected {label} result shape")
    for key in fields:
        exact_int(result[key], f"{label}.{key}")
    if result["tests"] > 1_000_000 or result["tests"] <= result["skipped"]:
        fail(f"{label} suite is empty or all skipped")
    counted = sum(result[key] for key in
                  ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    if counted > result["tests"]:
        fail(f"{label} result counts are inconsistent")
    if (result["status"] != 0 or result["successful"] != 1 or result["failures"] != 0 or
            result["errors"] != 0 or result["unexpected_successes"] != 0):
        fail(f"{label} suite did not produce a passing result")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mounts", required=True, type=Path)
    parser.add_argument("--trusted-dir", required=True, type=Path)
    parser.add_argument("--pid1", required=True, type=Path)
    parser.add_argument("--candidate-archive", required=True, type=Path)
    parser.add_argument("--base-tests-archive", required=True, type=Path)
    for name in ("base-repository", "base-repository-id", "candidate-repository",
                 "candidate-repository-id", "event-name", "event-action",
                 "pull-request-id", "pull-request-number", "run-id", "run-attempt", "check-run-id",
                 "workflow-path", "workflow-sha", "request-nonce", "artifact-name",
                 "base", "candidate"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()

    manifest_bytes = regular(args.manifest, 1024 * 1024)
    try:
        manifest = json.loads(manifest_bytes, object_pairs_hook=strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"invalid trusted manifest: {exc}")
    required = {
        "schema", "identity", "harness", "inputs", "actions", "limits", "commands",
        "boundary_status", "base", "candidate", "namespace_status", "log_truncated",
        "gate_ok", "output", "mounts", "status",
    }
    if not isinstance(manifest, dict) or set(manifest) != required or manifest["schema"] != 2:
        fail("manifest schema fields are missing or unexpected")

    expected_identity = {
        "base_repository": exact_repository(args.base_repository, "base repository"),
        "base_repository_id": parse_cli_integer(args.base_repository_id, "base repository id", minimum=1),
        "candidate_repository": exact_repository(args.candidate_repository, "candidate repository"),
        "candidate_repository_id": parse_cli_integer(args.candidate_repository_id, "candidate repository id", minimum=1),
        "event_name": args.event_name,
        "event_action": args.event_action,
        "pull_request_id": parse_cli_integer(args.pull_request_id, "pull request id"),
        "pull_request_number": parse_cli_integer(args.pull_request_number, "pull request number"),
        "run_id": parse_cli_integer(args.run_id, "run id", minimum=1),
        "run_attempt": parse_cli_integer(args.run_attempt, "run attempt", minimum=1),
        "check_run_id": parse_cli_integer(args.check_run_id, "check run id", minimum=1),
        "workflow_path": args.workflow_path,
        "workflow_sha": exact_sha(args.workflow_sha, "workflow SHA"),
        "request_nonce": args.request_nonce,
        "artifact_name": args.artifact_name,
        "base_sha": exact_sha(args.base, "base SHA"),
        "candidate_sha": exact_sha(args.candidate, "candidate SHA"),
    }
    if not HEX64.fullmatch(args.request_nonce) or args.workflow_path != WORKFLOW_PATH:
        fail("workflow path or request nonce is invalid")
    expected_artifact = f"trusted-remote-evidence-{args.run_id}-{args.run_attempt}-{args.request_nonce}"
    if args.artifact_name != expected_artifact:
        fail("artifact name is not derived from the exact run tuple")
    if manifest["identity"] != expected_identity:
        fail("manifest identity does not match the requested exact run/pair")
    event_pair = (args.event_name, args.event_action)
    if event_pair in {("pull_request_target", action) for action in
                      ("opened", "synchronize", "reopened", "ready_for_review")} or \
            event_pair == ("workflow_dispatch", "retest"):
        if expected_identity["pull_request_id"] <= 0 or expected_identity["pull_request_number"] <= 0:
            fail("PR evidence lacks a PR id or number")
    elif event_pair == ("push", "push"):
        if (expected_identity["pull_request_id"] != 0 or expected_identity["pull_request_number"] != 0 or
                args.base_repository != args.candidate_repository or
                args.base_repository_id != args.candidate_repository_id or args.base != args.candidate):
            fail("push evidence identity is inconsistent")
    else:
        fail("event/action pair is not allowed")
    if args.workflow_sha != args.base:
        fail("workflow SHA is not the tested base SHA")

    harness_sha, components = harness_records(args.trusted_dir, args.pid1)
    if manifest["harness"] != {"sha256": harness_sha, "components": components}:
        fail("harness digest is not the deterministic recomputation")
    expected_inputs = {
        "candidate_archive": file_record(args.candidate_archive, 536870912),
        "base_tests_archive": file_record(args.base_tests_archive, 134217728),
        "pid1": file_record(args.pid1, 4194304),
    }
    if manifest["inputs"] != expected_inputs:
        fail("input digest/size evidence does not match original inputs")
    if manifest["actions"] != {"checkout": CHECKOUT_SHA, "upload_artifact": UPLOAD_SHA}:
        fail("manifest action pins do not match the trusted workflow")
    if manifest["limits"] != LIMITS or sum(PHASE_SECONDS.values()) + 100 > LIMITS["wall_seconds"]:
        fail("trusted resource limits or phase budget changed")
    if manifest["commands"] != COMMANDS:
        fail("trusted command selection changed")
    for key in ("status", "namespace_status", "boundary_status", "log_truncated", "gate_ok"):
        exact_int(manifest[key], key)
    if (manifest["status"] != 0 or manifest["namespace_status"] != 0 or
            manifest["boundary_status"] != 0 or manifest["log_truncated"] != 0 or
            manifest["gate_ok"] != 1):
        fail("trusted boundary did not pass")
    validate_suite(manifest["base"], "base")
    validate_suite(manifest["candidate"], "candidate")

    output = regular(args.output, LIMITS["log_bytes"])
    mounts = regular(args.mounts, 65536)
    validate_mounts(mounts)
    require_file_digest(manifest["output"], {"sha256": digest(output), "bytes": len(output)}, "output")
    require_file_digest(manifest["mounts"], {"sha256": digest(mounts), "bytes": len(mounts)}, "mounts")
    print(
        "trusted exact-pair evidence: "
        f"run={args.run_id}/{args.run_attempt} check={args.check_run_id} "
        f"base={args.base} candidate={args.candidate}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
