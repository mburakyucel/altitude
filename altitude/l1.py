"""Dormant, crash-safe records for bounded Codex helpers and reviewers.

Phase 1B.4 deliberately exposes no launch path until Phase 1B.3 supplies the
single trusted ``require_owner_result`` result.  The private functions in
this module accept that closed snapshot explicitly so their record, preparation,
result-marker, and reconciliation behavior can be tested without inventing a
second owner authority.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

from . import config, dispatch, engines, state as S, tasks as T

CODEX_PATCH_NOTE = engines.CODEX_PATCH_NOTE
IMPLEMENTER_FOOTER = (
    "\n\n---\nWhen finished, leave the bounded change uncommitted and print exactly one final line "
    "`RESULT: patch ready — <tests and one-sentence handoff>`. Do not commit, open a PR, or merge; "
    "Altitude captures a patch and the L2 decides what to integrate."
)
REVIEWER_FOOTER = (
    "\n\n---\nWhen finished, print exactly one final line "
    "`RESULT: no commit — <one-sentence review verdict>`. Do not edit, open a PR, or merge."
)

_OWNER_RESULT_KEYS = {
    "version", "provider", "project", "slug", "owner_generation", "transition_id", "generation",
    "process_unit_id", "message_id", "intent_digest", "result_id", "result_sha256", "terminal_stage",
    "empty_receipt_sha256", "recovery_episode_id", "recovery_permit_revision",
}
_PARENT_KEYS = {"version", "owner_result", "task_scope", "worktree", "branch", "base_sha"}
_RECORD_KEYS = {"version", "name", "request_id", "role", "model", "parent", "parent_digest",
                "brief", "paths", "preparation", "physical", "result"}
_RESULT_KEYS = {"provider_session_id", "status", "summary", "error", "patch", "findings", "usage"}
_MARKER_KEYS = {"version", "transition_id", "generation", "process_unit_id", "intent_digest", "parent_digest",
                "result_id", "sha256", "disposition", "reason", "outcome"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40,64}")
_MARKER_CAP = engines.RAW_CAPTURE_CAP + 4096
_RECORD_CAP = 2 * engines.RAW_CAPTURE_CAP + 65536


def _digest(value: dict) -> str:
    return hashlib.sha256(S._canonical_json(value)).hexdigest()  # noqa: SLF001 - shared strict JSON


def _closed(value, keys: set[str], label: str, *, limit: int | None = None) -> dict:
    try:
        encoded = S._canonical_json(value)  # noqa: SLF001
        if limit is not None and len(encoded) > limit:
            raise T.TransitionError(f"{label} exceeds the bounded evidence size")
        copied = S._strict_json_loads(encoded.decode())  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise T.TransitionError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(copied, dict) or set(copied) != keys:
        raise T.TransitionError(f"{label} has unknown or missing fields")
    return copied


def _read_bounded_json(path: Path, limit: int, label: str):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise T.TransitionError(f"{label} is unreadable: {exc}") from exc
    if len(raw) > limit:
        raise T.TransitionError(f"{label} exceeds the bounded evidence size")
    if not raw.strip():
        raise T.TransitionError(f"{label} is empty")
    try:
        return S._strict_json_loads(raw.decode())  # noqa: SLF001
    except (UnicodeDecodeError, ValueError) as exc:
        raise T.TransitionError(f"{label} is invalid JSON: {exc}") from exc


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise T.TransitionError(f"helper {label} must be nonempty text")
    return value


def _sha(value, label: str, pattern=_SHA256) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise T.TransitionError(f"helper {label} is invalid")
    return value


def _repo_scope(value, label: str, *, allow_empty: bool) -> list[str]:
    """Validate one exact repository-relative scope without normalization or coercion."""
    if not isinstance(value, list):
        raise T.TransitionError(f"helper {label} must be an array")
    paths = []
    for path in value:
        if (not isinstance(path, str) or not path or path.startswith("/")
                or "\\" in path or not path.isprintable() or re.match(r"^[A-Za-z]:", path)):
            raise T.TransitionError(f"helper {label} contains an invalid repository-relative path")
        parts = path.split("/")
        if any(not part or part in (".", "..") or part.casefold() == ".git" or part.startswith("-")
               or part != part.strip() for part in parts):
            raise T.TransitionError(f"helper {label} contains an ambiguous repository-relative path")
        paths.append(path)
    if not allow_empty and not paths:
        raise T.TransitionError(f"helper {label} must not be empty")
    if len(set(paths)) != len(paths):
        raise T.TransitionError(f"helper {label} contains duplicate paths")
    return paths


def _owner_result_snapshot(value: dict, project: str, slug: str) -> dict:
    """Validate B3's exact immutable ``dispatch.owner_result_snapshot`` projection."""
    result = _closed(value, _OWNER_RESULT_KEYS, "helper owner-result snapshot")
    if (result["version"] != 1 or result["provider"] != "codex"
            or result["project"] != project or result["slug"] != slug
            or result["terminal_stage"] != "complete"):
        raise T.TransitionError("helper owner-result snapshot has the wrong completed owner context")
    if (isinstance(result["owner_generation"], bool)
            or not isinstance(result["owner_generation"], int) or result["owner_generation"] < 1):
        raise T.TransitionError("helper owner-result generation is invalid")
    for key in ("transition_id", "generation", "process_unit_id", "message_id", "result_id"):
        _text(result[key], f"owner-result {key}")
    for key in ("intent_digest", "result_sha256", "empty_receipt_sha256"):
        _sha(result[key], f"owner-result {key}")
    episode, permit = result["recovery_episode_id"], result["recovery_permit_revision"]
    if (episode is None) != (permit is None) or (episode is not None and (
            not isinstance(episode, str) or not episode or isinstance(permit, bool)
            or not isinstance(permit, int) or permit < 1)):
        raise T.TransitionError("helper owner-result recovery fence is invalid")
    return result


def _parent_snapshot(value: dict, project: str, slug: str, *, allow_empty_scope: bool = False) -> dict:
    """Validate the broker-bound B3 result and its exact task/repository context.

    B4 has no production builder: the B3 rebase must construct this wrapper from the
    value returned by ``require_owner_result`` while holding its claimant.  This
    validator deliberately has no legacy-generation or caller-selected fallback.
    """
    snapshot = _closed(value, _PARENT_KEYS, "helper parent snapshot")
    if snapshot["version"] != 1:
        raise T.TransitionError("helper parent snapshot version is invalid")
    snapshot["owner_result"] = _owner_result_snapshot(snapshot["owner_result"], project, slug)
    snapshot["task_scope"] = _repo_scope(
        snapshot["task_scope"], "parent task scope", allow_empty=allow_empty_scope
    )
    _text(snapshot["worktree"], "parent worktree")
    _text(snapshot["branch"], "parent branch")
    if not Path(snapshot["worktree"]).is_absolute():
        raise T.TransitionError("helper parent worktree must be absolute")
    _sha(snapshot["base_sha"], "parent base SHA", _GIT_SHA)
    return snapshot


def _result(value: dict) -> dict:
    result = _closed(value, _RESULT_KEYS, "helper result", limit=engines.RAW_CAPTURE_CAP)
    _text(result["provider_session_id"], "provider session id")
    if result["status"] not in ("complete", "failed"):
        raise T.TransitionError("helper result status is invalid")
    if (result["status"] == "complete" and result["error"] is not None) or (
            result["status"] == "failed" and (
                not isinstance(result["error"], str) or not result["error"].strip()
            )):
        raise T.TransitionError("helper result status and error disagree")
    for key in ("summary", "patch"):
        if result[key] is not None and not isinstance(result[key], str):
            raise T.TransitionError(f"helper result {key} must be text or null")
    if result["findings"] is not None and not isinstance(result["findings"], list):
        raise T.TransitionError("helper findings must be a list or null")
    if not isinstance(result["usage"], dict):
        raise T.TransitionError("helper usage must be an object")
    return result


def runs_dir(project: str, slug: str) -> Path:
    return S.task_dir(project, slug) / "l1"


def _record_path(project: str, slug: str, name: str) -> Path:
    return runs_dir(project, slug) / f"{name}.json"


def _marker_path(project: str, slug: str, record: dict) -> Path:
    return runs_dir(project, slug) / f".{record['name']}.{_physical(record)['generation']}.result.json"


@contextmanager
def _locks(project: str, slug: str):
    """All helper writers take project -> task -> operation in one declared order."""
    with S.project_lock(project):
        with S.ordered_file_lock(S.task_dir(project, slug) / ".lock", S.LockLevel.TASK):
            with S.ordered_file_lock(runs_dir(project, slug) / ".lock", S.LockLevel.OPERATION):
                yield


def _physical(record: dict) -> dict:
    return engines.validate_physical_transition(record.get("physical"))


def _preparation(project: str, slug: str, request_id: str, role: str, parent: dict,
                 brief: str, paths: list[str]) -> dict:
    short = re.sub(r"[^A-Za-z0-9_.-]", "-", slug)[:30]
    if role == "implementer":
        worktree = config.project_path(project).resolve() / ".claude" / "worktrees" / f"{short}-h-{request_id}"
        branch, create = f"l1/{short}-h-{request_id}", True
    else:
        worktree = Path(parent["worktree"]).resolve()
        branch, create = parent["branch"], False
    name = f"helper-{request_id}"
    prompt = _prompt(role, brief, paths).encode()
    if len(prompt) > engines.RAW_CAPTURE_CAP:
        raise T.TransitionError("helper prompt exceeds the bounded evidence size")
    return {"stage": "planned", "create_worktree": create, "worktree": str(worktree), "branch": branch,
            "parent_sha": parent["base_sha"],
            "prompt_path": str(runs_dir(project, slug) / f"{name}.prompt.md"),
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(), "worktree_status_sha256": None}


def _validate_record(value: dict, project: str, slug: str) -> dict:
    record = _closed(value, _RECORD_KEYS, "helper record", limit=_RECORD_CAP)
    if record["version"] != 1:
        raise T.TransitionError("helper record version is invalid")
    _sha(record["request_id"], "request id")
    if record["name"] != f"helper-{record['request_id']}":
        raise T.TransitionError("helper name is not derived from its stable request id")
    if record["role"] not in ("implementer", "reviewer"):
        raise T.TransitionError("helper role is invalid")
    if record["model"] is not None and not isinstance(record["model"], str):
        raise T.TransitionError("helper model must be text or null")
    parent = _parent_snapshot(
        record["parent"], project, slug, allow_empty_scope=record["role"] == "reviewer"
    )
    if record["parent_digest"] != _digest(parent):
        raise T.TransitionError("helper parent digest is invalid")
    _text(record["brief"], "brief")
    paths = _repo_scope(record["paths"], "sub-scope", allow_empty=record["role"] == "reviewer")
    record["paths"] = paths
    if record["role"] == "implementer":
        if any(not dispatch.inside_lease(path, parent["task_scope"]) for path in paths):
            raise T.TransitionError("helper sub-scope is outside its parent task")
    elif paths:
        raise T.TransitionError("reviewer helper cannot hold a write sub-scope")
    prep = record["preparation"]
    if not isinstance(prep, dict) or prep.get("stage") not in ("planned", "worktree_ready", "ready"):
        raise T.TransitionError("helper preparation stage is invalid")
    expected_prep = _preparation(project, slug, record["request_id"], record["role"], parent,
                                 record["brief"], paths)
    status_sha = prep.get("worktree_status_sha256")
    if (prep["stage"] == "planned" and status_sha is not None) or (
            prep["stage"] != "planned" and not isinstance(status_sha, str)):
        raise T.TransitionError("helper worktree status receipt is invalid")
    if status_sha is not None:
        _sha(status_sha, "worktree status SHA")
    if {**prep, "stage": "planned", "worktree_status_sha256": None} != expected_prep:
        raise T.TransitionError("helper preparation is outside its exact task/worktree/branch context")
    physical = _physical(record)
    expected_generation = hashlib.sha256(
        f"helper-generation\0{record['parent_digest']}\0{record['request_id']}".encode()
    ).hexdigest()
    if (physical["subject_id"] != f"{project}/{slug}/{record['name']}"
            or physical["provider"] != "codex"
            or physical["generation"] != expected_generation
            or physical["transition_id"] != f"helper:{project}/{slug}/{record['name']}:{expected_generation}"
            or physical["message_id"] != f"helper-message:{record['request_id']}"
            or physical["provider_session_request"] != {"kind": "fresh"}
            or physical["recovery_episode_id"] != parent["owner_result"]["recovery_episode_id"]
            or physical["recovery_permit_revision"] != parent["owner_result"]["recovery_permit_revision"]):
        raise T.TransitionError("helper physical transition is not bound to its parent context")
    result = None if record["result"] is None else _result(record["result"])
    receipt = physical["receipts"].get("result_observed")
    if result is not None:
        expected = {"result_id": f"helper-result:{record['request_id']}", "sha256": _digest(result)}
        if receipt != expected:
            raise T.TransitionError("helper result does not match its exact physical receipt")
        if ((result["status"] == "failed") != (physical["error"] is not None)
                or result["status"] == "failed" and result["error"] != physical["error"]):
            raise T.TransitionError("helper result status/error disagrees with its physical transition")
    elif receipt is not None:
        expected = ({"result_id": "transition:error",
                     "sha256": hashlib.sha256(physical["error"].encode()).hexdigest()}
                    if physical["error"] is not None else None)
        if receipt != expected:
            raise T.TransitionError("helper result is missing for its physical receipt")
    if physical["stage"] == "complete" and (result is None or result["status"] != "complete"):
        raise T.TransitionError("completed helper requires its exact successful result")
    if physical["stage"] == "failed" and result is not None and result["status"] != "failed":
        raise T.TransitionError("failed helper cannot contain a successful result")
    record["result"] = result
    return record


def load(project: str, slug: str, name: str) -> dict | None:
    value = _read_bounded_json(_record_path(project, slug, name), _RECORD_CAP, "helper record")
    return None if value is None else _validate_record(value, project, slug)


def _save(project: str, slug: str, record: dict) -> dict:
    record = _validate_record(record, project, slug)
    S.atomic_write(_record_path(project, slug, record["name"]), S._canonical_json(record).decode())  # noqa: SLF001
    return record


def list_runs(project: str, slug: str) -> list[dict]:
    directory = runs_dir(project, slug)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("helper-*.json")):
        value = _read_bounded_json(path, _RECORD_CAP, f"helper record {path.name}")
        if value is None:
            raise T.TransitionError(f"helper record {path.name} disappeared during enumeration")
        records.append(_validate_record(value, project, slug))
    return records


def _same_parent(record: dict, current: dict, project: str, slug: str) -> dict:
    snapshot = _parent_snapshot(
        current, project, slug, allow_empty_scope=record["role"] == "reviewer"
    )
    if _digest(snapshot) != record["parent_digest"]:
        raise T.TransitionError("helper parent OwnerGeneration is no longer current")
    return snapshot


def _prompt(role: str, brief: str, paths: list[str]) -> str:
    persona = config.PERSONAS / ("reviewer.md" if role == "reviewer" else "l1.md")
    prompt = persona.read_text() + "\n\n# Sub-brief\n\n" + brief.rstrip() + "\n\n" + CODEX_PATCH_NOTE
    if role == "implementer":
        return prompt + IMPLEMENTER_FOOTER + f" Your sublease is: {paths}."
    return prompt + REVIEWER_FOOTER


def _install_operation(project: str, slug: str, *, request_id: str, parent: dict, role: str,
                       brief: str, paths: list[str] | None = None, model: str | None = None) -> dict:
    """Install inert intent before any worktree, prompt, process, or provider effect.

    Only the future Phase 1B.3 broker may call this after deriving ``request_id`` and obtaining
    its exact current-generation snapshot.  There is intentionally no production caller in 1B.4.
    """
    request_id = _sha(request_id, "request id")
    if role not in ("implementer", "reviewer"):
        raise T.TransitionError("helper role is invalid")
    parent = _parent_snapshot(parent, project, slug, allow_empty_scope=role == "reviewer")
    brief = _text(brief, "brief")
    normalized = _repo_scope(paths if paths is not None else [], "sub-scope", allow_empty=role == "reviewer")
    if role == "reviewer" and normalized:
        raise T.TransitionError("reviewer helper cannot hold a write sub-scope")
    name = f"helper-{request_id}"
    parent_digest = _digest(parent)
    generation = hashlib.sha256(f"helper-generation\0{parent_digest}\0{request_id}".encode()).hexdigest()
    physical = engines.new_physical_transition(
        transition_id=f"helper:{project}/{slug}/{name}:{generation}", subject_kind="helper",
        subject_id=f"{project}/{slug}/{name}", generation=generation, provider="codex",
        provider_session_request={"kind": "fresh"}, message_id=f"helper-message:{request_id}",
        recovery_episode_id=parent["owner_result"]["recovery_episode_id"],
        recovery_permit_revision=parent["owner_result"]["recovery_permit_revision"],
    )
    record = {
        "version": 1, "name": name, "request_id": request_id, "role": role, "model": model, "parent": parent,
        "parent_digest": parent_digest, "brief": brief, "paths": normalized,
        "preparation": _preparation(project, slug, request_id, role, parent, brief, normalized),
        "physical": physical, "result": None,
    }
    record = _validate_record(record, project, slug)
    with _locks(project, slug):
        existing = load(project, slug, name)
        if existing is not None:
            if existing != record:
                raise T.TransitionError("conflicting helper request id")
            return existing
        return _save(project, slug, record)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _prepare_worktree(project: str, slug: str, name: str, current_parent: dict) -> dict:
    """Reconcile exactly one recorded worktree effect after a fresh parent-fence check."""
    with _locks(project, slug):
        record = load(project, slug, name)
        if record is None:
            raise T.TransitionError(f"no helper {name!r}")
        _same_parent(record, current_parent, project, slug)
        prep = record["preparation"]
        if prep["stage"] != "planned":
            return record
        target, parent_sha = Path(prep["worktree"]), prep["parent_sha"]
        repo = config.project_path(project).resolve()
        if prep["create_worktree"]:
            if target.exists():
                pass
            else:
                branch_exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{prep['branch']}").returncode == 0
                args = (["worktree", "add", str(target), prep["branch"]] if branch_exists
                        else ["worktree", "add", "-b", prep["branch"], str(target), parent_sha])
                created = _git(repo, *args)
                if created.returncode != 0:
                    raise T.TransitionError(f"helper worktree preparation failed: {(created.stderr or created.stdout)[:300]}")
        else:
            if target.resolve() != Path(record["parent"]["worktree"]).resolve():
                raise T.TransitionError("review helper escaped its parent worktree")
        head = (_git(target, "rev-parse", "HEAD").stdout or "").strip()
        branch = (_git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()
        target_common = (_git(target, "rev-parse", "--git-common-dir").stdout or "").strip()
        repo_common = (_git(repo, "rev-parse", "--git-common-dir").stdout or "").strip()
        target_common = (target / target_common).resolve() if target_common else None
        repo_common = (repo / repo_common).resolve() if repo_common else None
        if head != parent_sha or branch != prep["branch"] or target_common is None or target_common != repo_common:
            raise T.TransitionError("helper worktree conflicts with its exact parent repository/commit/branch")
        status = _git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status.returncode != 0 or record["role"] == "implementer" and status.stdout:
            raise T.TransitionError("helper worktree is not in its allowed preparation state")
        record["preparation"]["worktree_status_sha256"] = hashlib.sha256(status.stdout.encode()).hexdigest()
        record["preparation"]["stage"] = "worktree_ready"
        return _save(project, slug, record)


def _prepare_prompt(project: str, slug: str, name: str, current_parent: dict) -> dict:
    """Persist the derived prompt only after worktree preparation and another fence check."""
    with _locks(project, slug):
        record = load(project, slug, name)
        if record is None:
            raise T.TransitionError(f"no helper {name!r}")
        _same_parent(record, current_parent, project, slug)
        prep = record["preparation"]
        if prep["stage"] == "ready":
            return record
        if prep["stage"] != "worktree_ready":
            raise T.TransitionError("helper worktree is not prepared")
        prompt = _prompt(record["role"], record["brief"], record["paths"])
        if hashlib.sha256(prompt.encode()).hexdigest() != prep["prompt_sha256"]:
            raise T.TransitionError("helper prompt inputs changed after intent was installed")
        S.atomic_write(Path(prep["prompt_path"]), prompt)
        record["preparation"]["stage"] = "ready"
        return _save(project, slug, record)


def _git_value(cwd: Path, *args: str) -> str:
    observed = _git(cwd, *args)
    if observed.returncode != 0:
        raise T.TransitionError(
            f"helper worktree observation failed: {(observed.stderr or observed.stdout)[:300]}"
        )
    return (observed.stdout or "").strip()


def _require_launch_ready_locked(project: str, slug: str, record: dict, current_parent: dict,
                                 expected_stage: str) -> dict:
    """Revalidate every immutable input at the final, still-dormant launch boundary."""
    _same_parent(record, current_parent, project, slug)
    prep, physical = record["preparation"], _physical(record)
    if prep["stage"] != "ready" or physical["stage"] != expected_stage:
        raise T.TransitionError("helper is not at its final launch boundary")
    prompt_path = Path(prep["prompt_path"])
    try:
        with prompt_path.open("rb") as stream:
            prompt = stream.read(engines.RAW_CAPTURE_CAP + 1)
    except OSError as exc:
        raise T.TransitionError(f"helper prompt is unavailable: {exc}") from exc
    if len(prompt) > engines.RAW_CAPTURE_CAP:
        raise T.TransitionError("helper prompt exceeds the bounded evidence size")
    if hashlib.sha256(prompt).hexdigest() != prep["prompt_sha256"]:
        raise T.TransitionError("helper prompt bytes changed after preparation")

    target, repo = Path(prep["worktree"]), config.project_path(project).resolve()
    head = _git_value(target, "rev-parse", "HEAD")
    branch = _git_value(target, "rev-parse", "--abbrev-ref", "HEAD")
    target_common = (target / _git_value(target, "rev-parse", "--git-common-dir")).resolve()
    repo_common = (repo / _git_value(repo, "rev-parse", "--git-common-dir")).resolve()
    status = _git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status.returncode != 0:
        raise T.TransitionError(
            f"helper worktree status failed: {(status.stderr or status.stdout)[:300]}"
        )
    status_sha = hashlib.sha256(status.stdout.encode()).hexdigest()
    if (head != prep["parent_sha"] or branch != prep["branch"] or target_common != repo_common
            or status_sha != prep["worktree_status_sha256"]):
        raise T.TransitionError("helper worktree changed after preparation")
    return record


def _advance(project: str, slug: str, record: dict, expected: str, stage: str, receipt: dict) -> dict:
    record["physical"] = engines.advance_physical_transition(_physical(record), expected, stage, receipt)
    return _save(project, slug, record)


def _note_failure(project: str, slug: str, record: dict, error: str) -> dict:
    if _physical(record)["error"] is None:
        record["physical"] = engines.note_physical_transition_error(_physical(record), error)
        return _save(project, slug, record)
    return record


def _spawn_and_record(project: str, slug: str, name: str, current_parent: dict, launch) -> dict:
    """Elect, launch, and receipt one helper without releasing its canonical claim.

    Only a ``planned`` operation may invoke ``launch``.  Finding ``prior_stopped`` means a previous
    claimant may have crashed at the effect boundary; that state is reconciled or refused and is
    never relaunched.  The callable is a dormant typed seam for the future B3 broker, not a runtime
    adapter or alternate process authority.
    """
    with _locks(project, slug):
        record = load(project, slug, name)
        if record is None:
            raise T.TransitionError(f"no helper {name!r}")
        _same_parent(record, current_parent, project, slug)
        physical = _physical(record)
        if physical["stage"] != "planned":
            if physical["stage"] == "prior_stopped":
                marker, observation = _marker_observation(project, slug, record)
                decision = engines.reconcile_physical_transition(physical, observation)
                if decision["decision"] != "record_spawned":
                    raise engines.PhysicalTransitionError(
                        "ownership_uncertain: prior helper launch claim cannot be replayed"
                    )
                return _advance(project, slug, record, "prior_stopped", "spawned", {
                    "process_unit_id": physical["process_unit_id"], "launched": True,
                })
            return record
        _require_launch_ready_locked(project, slug, record, current_parent, "planned")
        observation = engines.observe_managed_unit(physical["process_unit_id"])
        if not observation.get("empty"):
            raise T.TransitionError("prior helper generation is not proven empty")
        record = _advance(project, slug, record, "planned", "prior_stopped",
                          {"previous_process_unit_id": None, "empty": True})
        _require_launch_ready_locked(project, slug, record, current_parent, "prior_stopped")
        receipt = launch(record)
        physical = _physical(record)
        expected = {"process_unit_id": physical["process_unit_id"], "launched": True}
        if receipt != expected:
            raise T.TransitionError("helper launcher did not return the exact process-unit receipt")
        return _advance(project, slug, record, "prior_stopped", "spawned", expected)


def _validate_marker(value: dict, record: dict) -> dict:
    marker = _closed(value, _MARKER_KEYS, "helper result marker", limit=_MARKER_CAP)
    physical = _physical(record)
    if (marker["version"] != 1 or marker["transition_id"] != physical["transition_id"]
            or marker["generation"] != physical["generation"]
            or marker["process_unit_id"] != physical["process_unit_id"]
            or marker["intent_digest"] != physical["intent_digest"]
            or marker["parent_digest"] != record["parent_digest"]):
        raise T.TransitionError("helper result marker is not bound to the exact operation")
    outcome = _result(marker["outcome"])
    if marker["result_id"] != f"helper-result:{record['request_id']}" or marker["sha256"] != _digest(outcome):
        raise T.TransitionError("helper result marker identity is invalid")
    if marker["disposition"] not in ("candidate", "discarded"):
        raise T.TransitionError("helper result marker disposition is invalid")
    if (marker["disposition"] == "candidate" and marker["reason"] is not None) or (
            marker["disposition"] == "discarded" and (
                not isinstance(marker["reason"], str) or not marker["reason"].strip()
            )):
        raise T.TransitionError("helper result marker disposition reason is invalid")
    marker["outcome"] = outcome
    return marker


def _write_result_marker(project: str, slug: str, name: str, current_parent: dict, outcome: dict) -> dict:
    """Persist generation-keyed provider evidence before the foreground wrapper exits."""
    outcome = _result(outcome)
    with _locks(project, slug):
        record = load(project, slug, name)
        if record is None:
            raise T.TransitionError(f"no helper {name!r}")
        current = _parent_snapshot(
            current_parent, project, slug, allow_empty_scope=record["role"] == "reviewer"
        )
        physical = _physical(record)
        if physical["stage"] not in ("prior_stopped", "spawned", "bound"):
            raise T.TransitionError("helper result marker requires a launch-capable operation")
        same = _digest(current) == record["parent_digest"]
        marker = {
            "version": 1, "transition_id": physical["transition_id"], "generation": physical["generation"],
            "process_unit_id": physical["process_unit_id"], "intent_digest": physical["intent_digest"],
            "parent_digest": record["parent_digest"], "result_id": f"helper-result:{record['request_id']}",
            "sha256": _digest(outcome), "disposition": "candidate" if same else "discarded",
            "reason": None if same else "parent OwnerGeneration changed before result persistence",
            "outcome": outcome,
        }
        marker = _validate_marker(marker, record)
        path = _marker_path(project, slug, record)
        existing = _read_bounded_json(path, _MARKER_CAP, "helper result marker")
        if existing is not None:
            existing = _validate_marker(existing, record)
            if existing != marker:
                raise T.TransitionError("conflicting helper result marker")
            return existing
        S.atomic_write(path, S._canonical_json(marker).decode())  # noqa: SLF001
        return marker


def _marker_observation(project: str, slug: str, record: dict) -> tuple[dict | None, dict]:
    value = _read_bounded_json(_marker_path(project, slug, record), _MARKER_CAP,
                               "helper result marker")
    if value is None:
        return None, {"present": False}
    marker = _validate_marker(value, record)
    return marker, {
        "present": True, "process_unit_id": marker["process_unit_id"],
        "intent_digest": marker["intent_digest"], "result_id": marker["result_id"],
        "sha256": marker["sha256"],
    }


def _discard_stale(project: str, slug: str, record: dict) -> dict:
    """Stop stale ownership, but advance only facts the shared reconciler can prove."""
    error = "parent OwnerGeneration changed; helper outcome discarded"
    marker, observation = _marker_observation(project, slug, record)
    recorded_result = _physical(record)["receipts"].get("result_observed")
    if recorded_result is not None and record["result"] is not None:
        observation = {
            "present": True, "process_unit_id": _physical(record)["process_unit_id"],
            "intent_digest": _physical(record)["intent_digest"], **recorded_result,
        }
    while True:
        physical = _physical(record)
        stage, unit = physical["stage"], physical["process_unit_id"]
        if stage in ("complete", "failed"):
            return record

        # At planned there is mechanically no effect, so the stale error can be installed before
        # proving prior-stop and can truthfully drive the negative launch/bind receipts.  At every
        # later stage, first preserve any positive launch/result fact; never turn empty ambiguity
        # into a fabricated negative receipt merely because stop made the unit empty.
        if stage == "planned" and physical["error"] is None:
            record = _note_failure(project, slug, record, error)
            physical = _physical(record)
        decision = engines.reconcile_physical_transition(physical, observation)
        action = decision["decision"]
        if action == "prove_prior_stopped":
            record = _advance(project, slug, record, stage, "prior_stopped",
                              {"previous_process_unit_id": None, "empty": True})
            continue
        if action == "record_spawned":
            record = _advance(project, slug, record, "prior_stopped", "spawned", {
                "process_unit_id": unit, "launched": True,
            })
            continue
        if action == "record_spawn_failure":
            record = _advance(project, slug, record, "prior_stopped", "spawned", {
                "process_unit_id": unit, "launched": False, "reason": error,
            })
            continue
        if action == "record_bound_failure":
            record = _advance(project, slug, record, "spawned", "bound", {
                "bound": False, "reason": error,
            })
            continue
        if action == "forward_repair":
            if marker is None:
                raise T.TransitionError("stale helper result evidence disappeared")
            record = _advance(project, slug, record, "spawned", "bound", {
                "bound": True, "physical_worker_id": unit,
                "provider_session_id": marker["outcome"]["provider_session_id"],
            })
            continue

        if action in ("record_bound", "running", "wait_for_empty"):
            observation = engines.stop_managed_unit(unit)
            if not observation.get("empty"):
                raise T.TransitionError("stale helper unit is not proven empty")
            # Re-observe through the shared physical reconciler.  Spawned/bound with no marker now
            # becomes process_missing and remains nonterminal instead of inventing launch truth.
            result_receipt = _physical(record)["receipts"].get("result_observed")
            observation = ({
                "present": True, "process_unit_id": unit,
                "intent_digest": _physical(record)["intent_digest"], **result_receipt,
            } if result_receipt and (record["result"] is not None
                                     or result_receipt["result_id"] == "transition:error")
                else _marker_observation(project, slug, record)[1])
            continue
        if action == "process_missing":
            raise engines.PhysicalTransitionError(
                f"ownership_uncertain: stale helper {stage} unit is empty without exact result evidence"
            )
        if action in ("record_result", "record_error_result"):
            if action == "record_result" and marker is None:
                raise T.TransitionError("stale helper result evidence disappeared")
            record = _note_failure(project, slug, record, error)
            if marker is not None:
                record["result"] = {
                    "provider_session_id": "discarded", "status": "failed", "summary": None,
                    "error": error, "patch": None, "findings": None, "usage": {},
                }
                error_receipt = {
                    "result_id": f"helper-result:{record['request_id']}",
                    "sha256": _digest(record["result"]),
                }
                record["physical"] = engines.advance_physical_transition(
                    _physical(record), "bound", "result_observed", error_receipt
                )
                record = _save(project, slug, record)
            else:
                error_receipt = {
                    "result_id": "transition:error",
                    "sha256": hashlib.sha256(error.encode()).hexdigest(),
                }
                record = _advance(project, slug, record, "bound", "result_observed", error_receipt)
            observation = {
                "present": True, "process_unit_id": unit,
                "intent_digest": _physical(record)["intent_digest"], **error_receipt,
            }
            continue
        if action == "record_empty":
            record = _note_failure(project, slug, record, error)
            record = _advance(project, slug, record, "result_observed", "empty",
                              decision["process_unit"])
            continue
        if action == "record_failed":
            return _advance(project, slug, record, "empty", "failed", {"status": "failed"})
        if action == "record_complete":
            record = _note_failure(project, slug, record, error)
            continue
        if action == "settled":
            return record
        raise engines.PhysicalTransitionError(
            f"ownership_uncertain: stale helper reconciliation cannot apply {action}"
        )


def _reconcile(project: str, slug: str, name: str, current_parent: dict) -> dict:
    """Advance receipts only; never launch or repeat a provider effect."""
    with _locks(project, slug):
        record = load(project, slug, name)
        if record is None:
            raise T.TransitionError(f"no helper {name!r}")
        current = _parent_snapshot(
            current_parent, project, slug, allow_empty_scope=record["role"] == "reviewer"
        )
        if _digest(current) != record["parent_digest"]:
            return _discard_stale(project, slug, record)
        marker, observation = _marker_observation(project, slug, record)
        while True:
            physical = _physical(record)
            decision = engines.reconcile_physical_transition(physical, observation)
            stage, unit = physical["stage"], physical["process_unit_id"]
            if decision["decision"] == "prove_prior_stopped":
                record = _advance(project, slug, record, stage, "prior_stopped",
                                  {"previous_process_unit_id": None, "empty": True})
            elif decision["decision"] == "record_spawned":
                record = _advance(project, slug, record, "prior_stopped", "spawned", {
                    "process_unit_id": unit, "launched": True,
                })
            elif decision["decision"] == "forward_repair":
                if marker is None:
                    raise T.TransitionError("helper result evidence disappeared")
                record = _advance(project, slug, record, stage, "bound", {
                    "bound": True, "physical_worker_id": unit,
                    "provider_session_id": marker["outcome"]["provider_session_id"],
                })
            elif decision["decision"] == "record_result":
                if marker is None or marker["disposition"] != "candidate":
                    raise T.TransitionError("helper result is not current-authorized evidence")
                result = marker["outcome"]
                if result["error"] and physical["error"] is None:
                    record["physical"] = engines.note_physical_transition_error(physical, result["error"])
                record["result"] = result
                record["physical"] = engines.advance_physical_transition(
                    _physical(record), "bound", "result_observed", {
                    "result_id": marker["result_id"], "sha256": marker["sha256"],
                })
                record = _save(project, slug, record)
            elif decision["decision"] == "record_empty":
                record = _advance(project, slug, record, stage, "empty", decision["process_unit"])
            elif decision["decision"] == "record_complete":
                return _advance(project, slug, record, stage, "complete", {"status": "complete"})
            elif decision["decision"] == "record_failed":
                return _advance(project, slug, record, stage, "failed", {"status": "failed"})
            elif decision["decision"] in ("running", "record_bound", "wait_for_empty", "settled"):
                return record
            else:
                raise engines.PhysicalTransitionError(
                    f"ownership_uncertain: helper reconciliation cannot apply {decision['decision']}"
                )


def _records_locked(project: str, slug: str) -> list[dict]:
    directory = runs_dir(project, slug)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith(".") and path.name.endswith(".result.json"):
            continue
        value = _read_bounded_json(path, _RECORD_CAP, f"helper record {path.name}")
        if value is None:
            raise T.TransitionError(f"helper record {path.name} is unreadable")
        record = _validate_record(value, project, slug)
        if path.name != f"{record['name']}.json":
            raise T.TransitionError(f"helper record {path.name} has an unexpected identity")
        records.append(record)
    return records


def require_helpers_terminal(project: str, slug: str) -> list[dict]:
    """Serialize terminal task changes with every strict helper and prove its exact unit empty."""
    with S.project_lock(project):
        if not runs_dir(project, slug).is_dir():
            return []
        with S.ordered_file_lock(S.task_dir(project, slug) / ".lock", S.LockLevel.TASK):
            with S.ordered_file_lock(runs_dir(project, slug) / ".lock", S.LockLevel.OPERATION):
                records = _records_locked(project, slug)
                for record in records:
                    physical = _physical(record)
                    empty = physical["receipts"].get("empty") or {}
                    if physical["stage"] not in ("complete", "failed") or empty.get("empty") is not True:
                        raise T.TransitionError(
                            f"{slug}: helper {record['name']} is not terminal and proven empty"
                        )
                return records


def cleanup_evidence(project: str, slug: str) -> list[dict]:
    """Project strict terminal helper records into conservative cleanup evidence."""
    records = require_helpers_terminal(project, slug)
    evidence = []
    for record in records:
        prep, physical = record["preparation"], _physical(record)
        result = record.get("result") or {}
        evidence.append({
            "name": record["name"], "role": record["role"], "stage": physical["stage"],
            "worktree": prep["worktree"], "branch": prep["branch"], "parent_sha": prep["parent_sha"],
            "patch": result.get("patch") if record["role"] == "implementer" and physical["stage"] == "complete"
            else None,
        })
    return evidence


def start(project: str, slug: str, brief: Path, **_kwargs) -> dict:
    """Fail closed until the Phase 1B.3 owner broker supplies the typed seam."""
    # Preserve the narrower Phase 1A denial for legacy Claude-owned rows, without accepting its token as authority.
    T.require_owner_provider_capability(S.load_task(project, slug))
    raise T.TransitionError("engine hold: managed helpers await the Phase 1B.3 current-generation broker")


def exec_run(project: str, slug: str, name: str) -> dict:
    raise T.TransitionError("engine hold: direct helper execution is disabled; no brokered generation is available")


def _compact(record: dict) -> dict:
    physical = _physical(record)
    result = record.get("result") or {}
    compact = {key: record.get(key) for key in ("name", "role", "model")} | {"engine": physical["provider"],
        "branch": record["preparation"]["branch"], "worktree": record["preparation"]["worktree"],
        "physical_stage": physical["stage"], "done": physical["stage"] in ("complete", "failed"),
        "patch": result.get("patch"), "summary": result.get("summary"), "error": result.get("error"),
        "usage": result.get("usage"),
    }
    if record["role"] == "reviewer":
        compact["findings"] = result.get("findings") if isinstance(result.get("findings"), list) else None
    return compact


def status(project: str, slug: str) -> list[dict]:
    return [_compact(record) for record in list_runs(project, slug)]


def wait(project: str, slug: str, name: str | None = None, timeout: int = 540) -> dict:
    raise T.TransitionError("engine hold: helper waiting awaits the Phase 1B.3 current-generation broker")
