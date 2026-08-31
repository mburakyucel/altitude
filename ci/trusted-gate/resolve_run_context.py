#!/usr/bin/python3
"""Resolve immutable, base-trusted identity for one remote test run."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
CHECK_RUN_URL = re.compile(r"https://api\.github\.com/repos/[^/]+/[^/]+/check-runs/([1-9][0-9]*)")
WORKFLOW_PATH = ".github/workflows/trusted-remote-tests.yml"
JOB_NAME = "trusted-remote / evidence"
PR_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


class ContextError(ValueError):
    pass


def exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX40.fullmatch(value):
        raise ContextError(f"{label} is not an exact lowercase commit SHA")
    return value


def exact_nonce(value: Any) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ContextError("request_nonce is not 64 lowercase hexadecimal characters")
    return value


def positive_integer(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ContextError(f"{label} is not an integer")
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal():
            raise ContextError(f"{label} is not an integer")
        value = int(value)
    if not isinstance(value, int) or value < (0 if allow_zero else 1):
        raise ContextError(f"{label} is outside its allowed range")
    return value


def repository(value: Any, label: str) -> str:
    if not isinstance(value, str) or not REPOSITORY.fullmatch(value):
        raise ContextError(f"{label} is not a canonical owner/repository name")
    return value


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContextError(f"{label} is not an object")
    return value


def pr_identity(pr: dict[str, Any], base_repository: str, base_repository_id: int,
                base_sha: str) -> tuple[int, int, str, int, str]:
    pull_request_id = positive_integer(pr.get("id"), "pull request id")
    number = positive_integer(pr.get("number"), "pull request number")
    base = require_mapping(pr.get("base"), "pull request base")
    base_repo = require_mapping(base.get("repo"), "pull request base repository")
    if (repository(base_repo.get("full_name"), "pull request base repository") != base_repository or
            positive_integer(base_repo.get("id"), "pull request base repository id") != base_repository_id or
            base.get("ref") != "main" or exact_sha(base.get("sha"), "pull request base SHA") != base_sha):
        raise ContextError("pull request is not based on the exact trusted main commit")
    head = require_mapping(pr.get("head"), "pull request head")
    head_repo = require_mapping(head.get("repo"), "pull request head repository")
    candidate_repository = repository(head_repo.get("full_name"), "candidate repository")
    candidate_repository_id = positive_integer(head_repo.get("id"), "candidate repository id")
    candidate_sha = exact_sha(head.get("sha"), "candidate SHA")
    return pull_request_id, number, candidate_repository, candidate_repository_id, candidate_sha


def resolve(event_name: str, event: dict[str, Any], pull: dict[str, Any] | None,
            jobs: dict[str, Any], *, base_repository: str, base_repository_id: int,
            base_sha: str, workflow_sha: str, run_id: int,
            run_attempt: int) -> dict[str, str]:
    base_repository = repository(base_repository, "base repository")
    base_repository_id = positive_integer(base_repository_id, "base repository id")
    base_sha = exact_sha(base_sha, "base SHA")
    workflow_sha = exact_sha(workflow_sha, "workflow SHA")
    run_id = positive_integer(run_id, "run id")
    run_attempt = positive_integer(run_attempt, "run attempt")
    if base_sha != workflow_sha:
        raise ContextError("workflow SHA is not the exact tested base SHA")

    if event_name == "pull_request_target":
        action = event.get("action")
        if action not in PR_ACTIONS:
            raise ContextError("pull_request_target action is not allowed")
        pr = require_mapping(event.get("pull_request"), "pull request event")
        pull_request_id, pr_number, candidate_repository, candidate_repository_id, candidate_sha = pr_identity(
            pr, base_repository, base_repository_id, base_sha
        )
        request_nonce = hashlib.sha256(
            f"{base_repository}\0{pull_request_id}\0{pr_number}\0{candidate_repository}\0{candidate_sha}\0"
            f"{run_id}\0{run_attempt}".encode()
        ).hexdigest()
        event_action = str(action)
    elif event_name == "workflow_dispatch":
        if event.get("ref") != "refs/heads/main" or pull is None:
            raise ContextError("dispatch did not execute from trusted main with PR evidence")
        inputs = require_mapping(event.get("inputs"), "workflow dispatch inputs")
        pull_request_id, pr_number, candidate_repository, candidate_repository_id, candidate_sha = pr_identity(
            pull, base_repository, base_repository_id, base_sha
        )
        if positive_integer(inputs.get("pull_request_number"), "requested pull request") != pr_number:
            raise ContextError("dispatch pull request does not match API evidence")
        if repository(inputs.get("candidate_repository"), "requested candidate repository") != candidate_repository:
            raise ContextError("dispatch candidate repository does not match current PR head")
        if exact_sha(inputs.get("candidate_sha"), "requested candidate SHA") != candidate_sha:
            raise ContextError("dispatch candidate SHA does not match current PR head")
        if exact_sha(inputs.get("expected_base_sha"), "expected base SHA") != base_sha:
            raise ContextError("dispatch expected base is not current trusted main")
        request_nonce = exact_nonce(inputs.get("request_nonce"))
        event_action = "retest"
    elif event_name == "push":
        if event.get("ref") != "refs/heads/main" or exact_sha(event.get("after"), "push SHA") != base_sha:
            raise ContextError("push is not the exact trusted main commit")
        pull_request_id = 0
        pr_number = 0
        candidate_repository = base_repository
        candidate_repository_id = base_repository_id
        candidate_sha = base_sha
        request_nonce = hashlib.sha256(
            f"{base_repository}\0push\0{base_sha}\0{run_id}\0{run_attempt}".encode()
        ).hexdigest()
        event_action = "push"
    else:
        raise ContextError("unsupported workflow event")

    job_entries = jobs.get("jobs") if isinstance(jobs, dict) else None
    if not isinstance(job_entries, list):
        raise ContextError("Actions jobs response is malformed")
    matches: list[dict[str, Any]] = []
    for raw_job in job_entries:
        if not isinstance(raw_job, dict) or raw_job.get("name") != JOB_NAME:
            continue
        if (positive_integer(raw_job.get("run_id"), "job run id") == run_id and
                positive_integer(raw_job.get("run_attempt"), "job run attempt") == run_attempt and
                exact_sha(raw_job.get("head_sha"), "job head SHA") == base_sha):
            matches.append(raw_job)
    if len(matches) != 1:
        raise ContextError("could not resolve exactly one current trusted Actions job")
    job = matches[0]
    job_id = positive_integer(job.get("id"), "Actions job id")
    match = CHECK_RUN_URL.fullmatch(str(job.get("check_run_url", "")))
    if match is None or positive_integer(match.group(1), "check run id") != job_id:
        raise ContextError("Actions job is not bound to the same check run id")

    artifact_name = f"trusted-remote-evidence-{run_id}-{run_attempt}-{request_nonce}"
    candidate_fetch_ref = candidate_sha if pr_number == 0 else f"refs/pull/{pr_number}/head"
    return {
        "artifact_name": artifact_name,
        "base_repository": base_repository,
        "base_repository_id": str(base_repository_id),
        "base_sha": base_sha,
        "candidate_fetch_ref": candidate_fetch_ref,
        "candidate_repository": candidate_repository,
        "candidate_repository_id": str(candidate_repository_id),
        "candidate_sha": candidate_sha,
        "check_run_id": str(job_id),
        "event_action": event_action,
        "event_name": event_name,
        "pull_request_id": str(pull_request_id),
        "pull_request_number": str(pr_number),
        "request_nonce": request_nonce,
        "run_attempt": str(run_attempt),
        "run_id": str(run_id),
        "workflow_path": WORKFLOW_PATH,
        "workflow_sha": workflow_sha,
    }


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ContextError(f"{path} does not contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-file", required=True, type=Path)
    parser.add_argument("--pull-file", type=Path)
    parser.add_argument("--jobs-file", required=True, type=Path)
    parser.add_argument("--base-repository", required=True)
    parser.add_argument("--base-repository-id", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args()
    resolved = resolve(
        args.event_name, read_json(args.event_file),
        read_json(args.pull_file) if args.pull_file else None, read_json(args.jobs_file),
        base_repository=args.base_repository,
        base_repository_id=positive_integer(args.base_repository_id, "base repository id"),
        base_sha=args.base_sha, workflow_sha=args.workflow_sha,
        run_id=positive_integer(args.run_id, "run id"),
        run_attempt=positive_integer(args.run_attempt, "run attempt"),
    )
    with args.github_output.open("a", encoding="utf-8") as output:
        for key in sorted(resolved):
            output.write(f"{key}={resolved[key]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
