"""Dormant deployment baseline, contribution, and qualification authority.

This module records facts only.  It cannot build, install, stop, start, or activate a
service.  Eligibility is a pure projection over one exact first-parent chain supplied
by a later trusted activation runner.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import config, state as S

SCHEMA_V1 = "altitude.deployment/v1"
SCHEMA = "altitude.deployment/v2"
RUNTIME_MANIFEST_SCHEMA = "altitude.runtime-manifest/v2"
LEGACY_PENDING_BLOCKER = "legacy_restart_pending_unreconciled"
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SERVICE_ID = re.compile(r"[a-z0-9][a-z0-9_.@-]{0,99}\Z")
_UNIT = re.compile(r"[A-Za-z0-9_.@:-]{1,160}\Z")
_MANIFEST_FIELDS = {
    "schema_version", "role", "prior_kind", "build_version", "source_commit",
    "expected_commit", "identity_errors", "valid", "loaded_source", "executed_source",
    "installed_checkout", "installation_identity", "remote_main", "state", "service_unit",
    "web_bundle", "prior_install_manifest_sha256", "bootstrap_evidence", "manifest_sha256",
}
_RECORD_FIELDS = {
    "schema_version", "service", "repository", "activated_sha",
    "bootstrap_anchor", "legacy_pending", "legacy_reconciliation",
    "contributions", "qualifications", "operator_coverage", "satisfied_contributions",
    "record_sha256",
}
_V1_RECORD_FIELDS = {
    "schema_version", "service", "repository", "activated_sha",
    "bootstrap_anchor", "legacy_pending", "record_sha256",
}
_CONTRIBUTION_FIELDS = {
    "id", "publication_receipt_id", "publication_receipt_sha256", "merge_sha", "observed_at",
}
_QUALIFICATION_FIELDS = {
    "id", "contribution_id", "observed_at", "main_check", "open_findings",
    "merge_hold", "decision", "supersedes",
}
_COVERAGE_FIELDS = {
    "id", "start_exclusive", "end_inclusive", "commit_count", "commits_sha256",
    "observed_at", "reason", "authorization",
}
_LEGACY_RECONCILIATION_FIELDS = {
    "id", "evidence_sha256", "observed_at", "anchor_sha", "head_sha",
    "commit_count", "commits_sha256", "coverage_sha256",
}


class DeploymentError(ValueError):
    pass


class DeploymentConflict(DeploymentError):
    pass


def _exact(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise DeploymentError(f"{label} must have exactly {sorted(fields)}, got {got}")
    return value


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DeploymentError(f"{label} must be a nonempty string of at most {limit} characters")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise DeploymentError(f"{label} must be one full lowercase Git SHA")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise DeploymentError(f"{label} must be one lowercase sha256")
    return value


def _canonical_hash(value: dict, hash_field: str) -> str:
    unsigned = {key: item for key, item in value.items() if key != hash_field}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _identity(service_id: str, unit: str, repository_id: str,
              checkout: str) -> tuple[dict, dict]:
    if not isinstance(service_id, str) or not _SERVICE_ID.fullmatch(service_id):
        raise DeploymentError("service id is invalid")
    if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
        raise DeploymentError("service unit is invalid")
    _text(repository_id, "repository id", 200)
    if ".." in repository_id.split("/") or repository_id.startswith("/"):
        raise DeploymentError("repository id must be a stable logical identity, not a path")
    _text(checkout, "repository checkout", 4096)
    checkout_path = Path(checkout)
    if (not checkout_path.is_absolute()
            or str(checkout_path.resolve(strict=False)) != checkout):
        raise DeploymentError("repository checkout must be one normalized absolute path")
    return ({"id": service_id, "unit": unit},
            {"id": repository_id, "checkout": checkout})


def _manifest(value: object, *, unit: str, checkout: str) -> dict:
    manifest = _exact(value, _MANIFEST_FIELDS, "runtime manifest")
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise DeploymentError("runtime manifest schema is unsupported")
    if manifest.get("manifest_sha256") != _canonical_hash(manifest, "manifest_sha256"):
        raise DeploymentError("runtime manifest hash does not match its contents")
    if manifest.get("valid") is not True or manifest.get("identity_errors") != []:
        raise DeploymentError("runtime manifest does not prove an exact install identity")
    source = _sha(manifest.get("source_commit"), "runtime manifest source_commit")
    if manifest.get("expected_commit") != source:
        raise DeploymentError("runtime manifest expected/source commit differs")
    loaded = manifest.get("loaded_source")
    installation = manifest.get("installation_identity")
    installed = manifest.get("installed_checkout")
    service = manifest.get("service_unit")
    if not all(isinstance(row, dict) for row in (loaded, installation, installed, service)):
        raise DeploymentError("runtime manifest lacks deployment identity objects")
    if (loaded.get("checkout") != checkout or loaded.get("git_head") != source
            or loaded.get("dirty") is not False
            or (loaded.get("commit_comparison") or {}).get("exact") is not True
            or installation.get("checkout_path") != checkout
            or installation.get("checkout_head") != source
            or installation.get("source_commit") != source
            or installed.get("path") != checkout or installed.get("git_head") != source
            or installed.get("error") is not None or service.get("name") != unit):
        raise DeploymentError("runtime manifest install/repository identity is not exact")
    return manifest


def _active_anchor(manifest: dict) -> tuple[str, dict]:
    if manifest.get("role") != "running_install" or manifest.get("prior_kind") != "ordinary_running_install":
        raise DeploymentError("activated_sha requires an ordinary running_install manifest")
    source = manifest["source_commit"]
    executed = manifest.get("executed_source") or {}
    manager = (manifest.get("service_unit") or {}).get("manager") or {}
    cgroup, process = manager.get("cgroup") or {}, manager.get("process") or {}
    pid = manager.get("main_pid")
    if (executed.get("captured") is not True or executed.get("valid") is not True
            or manager.get("observed") is not True or manager.get("active_state") != "active"
            or manager.get("sub_state") != "running" or isinstance(pid, bool)
            or not isinstance(pid, int) or pid <= 0 or cgroup.get("empty") is not False
            or pid not in (cgroup.get("pids") or []) or process.get("pid") != pid
            or isinstance(process.get("start_ticks"), bool)
            or not isinstance(process.get("start_ticks"), int)
            or process["start_ticks"] <= 0 or process.get("error") is not None):
        raise DeploymentError("running manifest does not prove one stable active service process")
    return source, {"kind": "loaded_manifest", "sha": source,
                    "manifest_sha256": manifest["manifest_sha256"]}


def _authorization(value: object) -> dict:
    auth = _exact(value, {"id", "actor", "at", "reason"}, "operator authorization")
    limits = {"id": 100, "actor": 80, "at": 80, "reason": 500}
    out = {field: _text(auth.get(field), f"authorization {field}", limit)
           for field, limit in limits.items()}
    try:
        timestamp = datetime.fromisoformat(out["at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentError("authorization at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise DeploymentError("authorization at must include an offset")
    return out


def _timestamp(value: object, label: str) -> str:
    text = _text(value, label, 80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DeploymentError(f"{label} must include an offset")
    return text


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, 160)
    if any(character.isspace() for character in text):
        raise DeploymentError(f"{label} must not contain whitespace")
    return text


def _commit_list_digest(commits: list[str]) -> str:
    return hashlib.sha256(json.dumps(
        commits, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def _contribution(value: object) -> dict:
    row = _exact(value, _CONTRIBUTION_FIELDS, "deployment contribution")
    _digest(row.get("id"), "contribution id")
    _identifier(row.get("publication_receipt_id"), "publication receipt id")
    _digest(row.get("publication_receipt_sha256"), "publication receipt sha256")
    _sha(row.get("merge_sha"), "contribution merge sha")
    _timestamp(row.get("observed_at"), "contribution observed_at")
    expected = hashlib.sha256(S._canonical_json({  # noqa: SLF001
        "publication_receipt_id": row["publication_receipt_id"],
        "merge_sha": row["merge_sha"],
    })).hexdigest()
    if row["id"] != expected:
        raise DeploymentError("contribution id does not match its immutable facts")
    return row


def _qualification(value: object) -> dict:
    row = _exact(value, _QUALIFICATION_FIELDS, "deployment qualification")
    _digest(row.get("id"), "qualification id")
    _digest(row.get("contribution_id"), "qualification contribution id")
    _timestamp(row.get("observed_at"), "qualification observed_at")
    main = _exact(row.get("main_check"), {"status", "head_sha", "evidence_sha256"},
                  "qualification main_check")
    if main.get("status") not in ("passed", "failed", "unavailable"):
        raise DeploymentError("qualification main_check status is invalid")
    _sha(main.get("head_sha"), "qualification main_check head sha")
    _digest(main.get("evidence_sha256"), "qualification main_check evidence sha256")
    findings = _exact(row.get("open_findings"), {"count", "evidence_sha256"},
                      "qualification open_findings")
    count = findings.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10_000:
        raise DeploymentError("qualification open finding count is invalid")
    _digest(findings.get("evidence_sha256"), "qualification finding evidence sha256")
    hold = _exact(row.get("merge_hold"), {"held", "reason"}, "qualification merge_hold")
    if not isinstance(hold.get("held"), bool):
        raise DeploymentError("qualification merge_hold held must be boolean")
    reason = hold.get("reason")
    if hold["held"]:
        _text(reason, "qualification merge hold reason", 500)
    elif reason is not None:
        raise DeploymentError("an unheld qualification cannot carry a merge hold reason")
    decision = row.get("decision")
    expected_decision = ("eligible" if main["status"] == "passed" and count == 0
                         and hold["held"] is False else "blocked")
    if decision != expected_decision:
        raise DeploymentError("qualification decision contradicts its evidence")
    supersedes = row.get("supersedes")
    if (not isinstance(supersedes, list) or len(supersedes) > 128
            or supersedes != sorted(set(supersedes))):
        raise DeploymentError("qualification supersedes must be sorted unique ids")
    for item in supersedes:
        _digest(item, "superseded qualification id")
    if decision != "eligible" and supersedes:
        raise DeploymentError("only an eligible corrective qualification may supersede a blocker")
    expected = hashlib.sha256(S._canonical_json({  # noqa: SLF001
        key: row[key] for key in _QUALIFICATION_FIELDS - {"id"}
    })).hexdigest()
    if row["id"] != expected:
        raise DeploymentError("qualification id does not match its immutable facts")
    return row


def _coverage(value: object) -> dict:
    row = _exact(value, _COVERAGE_FIELDS, "operator coverage")
    _digest(row.get("id"), "operator coverage id")
    _sha(row.get("start_exclusive"), "operator coverage start")
    _sha(row.get("end_inclusive"), "operator coverage end")
    count = row.get("commit_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10_000:
        raise DeploymentError("operator coverage commit_count is invalid")
    _digest(row.get("commits_sha256"), "operator coverage commits sha256")
    _timestamp(row.get("observed_at"), "operator coverage observed_at")
    _text(row.get("reason"), "operator coverage reason", 500)
    _authorization(row.get("authorization"))
    expected = hashlib.sha256(S._canonical_json({  # noqa: SLF001
        key: row[key] for key in _COVERAGE_FIELDS - {"id"}
    })).hexdigest()
    if row["id"] != expected:
        raise DeploymentError("operator coverage id does not match its immutable facts")
    return row


def _legacy_reconciliation(value: object, pending: dict | None) -> dict | None:
    if value is None:
        return None
    row = _exact(value, _LEGACY_RECONCILIATION_FIELDS, "legacy reconciliation")
    _digest(row.get("id"), "legacy reconciliation id")
    _digest(row.get("evidence_sha256"), "legacy reconciliation evidence sha256")
    _timestamp(row.get("observed_at"), "legacy reconciliation observed_at")
    _sha(row.get("anchor_sha"), "legacy reconciliation anchor sha")
    _sha(row.get("head_sha"), "legacy reconciliation head sha")
    count = row.get("commit_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100_000:
        raise DeploymentError("legacy reconciliation commit_count is invalid")
    _digest(row.get("commits_sha256"), "legacy reconciliation commits sha256")
    _digest(row.get("coverage_sha256"), "legacy reconciliation coverage sha256")
    if (pending is None or pending.get("status") != "recognized"
            or row["evidence_sha256"] != pending.get("evidence_sha256")
            or row["head_sha"] != pending.get("head")):
        raise DeploymentError("legacy reconciliation does not bind the imported evidence")
    expected = hashlib.sha256(S._canonical_json({  # noqa: SLF001
        key: row[key] for key in _LEGACY_RECONCILIATION_FIELDS - {"id", "observed_at"}
    })).hexdigest()
    if row["id"] != expected:
        raise DeploymentError("legacy reconciliation id does not match its immutable facts")
    return row


def _operator_anchor(manifest: dict, declaration: object) -> tuple[None, dict]:
    declared = _exact(declaration, {"evidence_limitation", "authorization"},
                      "operator provenance")
    if manifest.get("role") != "stopped_install" or manifest.get("prior_kind") != "bootstrap_freeze":
        raise DeploymentError("operator provenance requires one bootstrap stopped_install manifest")
    manager = (manifest.get("service_unit") or {}).get("manager") or {}
    cgroup = manager.get("cgroup") or {}
    if (manager.get("observed") is not True or manager.get("active_state") != "inactive"
            or manager.get("sub_state") != "dead" or manager.get("main_pid") != 0
            or cgroup.get("empty") is not True or cgroup.get("error") is not None):
        raise DeploymentError("stopped manifest does not prove the service cgroup empty")
    evidence, state = manifest.get("bootstrap_evidence"), manifest.get("state")
    if not isinstance(evidence, dict) or not isinstance(state, dict):
        raise DeploymentError("stopped manifest lacks bootstrap/state evidence")
    source = manifest["source_commit"]
    state_sha = _digest(state.get("sha256"), "frozen state sha256")
    raw_sha = _digest(evidence.get("raw_evidence_sha256"), "raw freeze evidence sha256")
    if (evidence.get("source_commit") != source or evidence.get("state_sha256") != state_sha
            or evidence.get("process_empty") is not True):
        raise DeploymentError("bootstrap evidence differs from stopped install")
    authorization = _authorization(declared.get("authorization"))
    if evidence.get("operator_authorization") != authorization:
        raise DeploymentError("operator authorization differs from the frozen bootstrap evidence")
    return None, {
        "kind": "operator_provenance", "sha": source,
        "stopped_manifest_sha256": manifest["manifest_sha256"],
        "raw_freeze_evidence_sha256": raw_sha, "frozen_state_sha256": state_sha,
        "evidence_limitation": _text(declared.get("evidence_limitation"),
                                     "evidence limitation", 500),
        "authorization": authorization,
    }


def _strict_json_object(raw: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


def _pending_values(since: object, head: object, files: object) -> tuple[str, str, list[str]]:
    since = _text(since, "legacy pending since", 80)
    try:
        timestamp = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentError("legacy pending since must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise DeploymentError("legacy pending since must include an offset")
    head = _sha(head, "legacy pending head")
    if (not isinstance(files, list) or not files or len(files) > 128
            or any(not isinstance(path, str) for path in files)
            or files != sorted(set(files))):
        raise DeploymentError("legacy pending files must be 1..128 sorted unique paths")
    for path in files:
        if (not path or len(path) > 300 or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts):
            raise DeploymentError("legacy pending contains an unsafe path")
    return since, head, files


def _legacy_pending(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, bytes) or len(raw) > 65_536:
        raise DeploymentError("legacy restart-pending evidence must be at most 65536 bytes")
    digest = hashlib.sha256(raw).hexdigest()
    error = None
    try:
        value = _strict_json_object(raw)
        _exact(value, {"since", "head", "files"}, "legacy restart-pending")
        since, head, files = _pending_values(value.get("since"), value.get("head"),
                                             value.get("files"))
    except (DeploymentError, UnicodeDecodeError, ValueError, TypeError):
        error = "unrecognized_shape"
    if error:
        return {"status": "unrecognized", "evidence_sha256": digest,
                "error": error, "blocker": LEGACY_PENDING_BLOCKER}
    return {"status": "recognized", "evidence_sha256": digest, "head": head,
            "since": since, "files": files, "blocker": LEGACY_PENDING_BLOCKER}


def _anchor(value: object) -> dict:
    if not isinstance(value, dict):
        raise DeploymentError("bootstrap_anchor must be an object")
    kind = value.get("kind")
    fields = ({"kind", "sha", "manifest_sha256"} if kind == "loaded_manifest" else
              {"kind", "sha", "stopped_manifest_sha256", "raw_freeze_evidence_sha256",
               "frozen_state_sha256", "evidence_limitation", "authorization"}
              if kind == "operator_provenance" else set())
    if not fields:
        raise DeploymentError("bootstrap_anchor kind is invalid")
    anchor = _exact(value, fields, "bootstrap_anchor")
    _sha(anchor.get("sha"), "bootstrap anchor sha")
    for field in fields & {"manifest_sha256", "stopped_manifest_sha256",
                           "raw_freeze_evidence_sha256", "frozen_state_sha256"}:
        _digest(anchor.get(field), f"bootstrap anchor {field}")
    if kind == "operator_provenance":
        _text(anchor.get("evidence_limitation"), "evidence limitation", 500)
        _authorization(anchor.get("authorization"))
    return anchor


def _validate_baseline(value: object, *, schema: str, fields: set[str]) -> tuple[dict, dict | None]:
    record = _exact(value, fields, "DeploymentRecord")
    if record.get("schema_version") != schema:
        raise DeploymentError("DeploymentRecord schema is unsupported")
    service = _exact(record.get("service"), {"id", "unit"}, "service identity")
    repository = _exact(record.get("repository"), {"id", "checkout"}, "repository identity")
    _identity(service.get("id"), service.get("unit"), repository.get("id"),
              repository.get("checkout"))
    anchor = _anchor(record.get("bootstrap_anchor"))
    activated = record.get("activated_sha")
    if activated is not None:
        _sha(activated, "activated_sha")
    if ((anchor["kind"] == "loaded_manifest" and activated != anchor["sha"])
            or (anchor["kind"] == "operator_provenance" and activated is not None)):
        raise DeploymentError("activated_sha contradicts bootstrap_anchor")
    pending = record.get("legacy_pending")
    if pending is not None:
        fields = ({"status", "evidence_sha256", "head", "since", "files", "blocker"}
                  if isinstance(pending, dict) and pending.get("status") == "recognized"
                  else {"status", "evidence_sha256", "error", "blocker"})
        _exact(pending, fields, "legacy_pending")
        _digest(pending.get("evidence_sha256"), "legacy pending evidence_sha256")
        if pending.get("blocker") != LEGACY_PENDING_BLOCKER:
            raise DeploymentError("legacy_pending blocker is invalid")
        if pending.get("status") == "recognized":
            _pending_values(pending.get("since"), pending.get("head"), pending.get("files"))
        elif pending.get("status") != "unrecognized" or pending.get("error") != "unrecognized_shape":
            raise DeploymentError("legacy_pending status/error is invalid")
    if record.get("record_sha256") != _canonical_hash(record, "record_sha256"):
        raise DeploymentError("DeploymentRecord hash does not match its contents")
    return record, pending


def validate_v1_record(value: object) -> dict:
    """Validate the exact dormant Phase 2A shape; it is accepted only by the upgrader."""
    record, _pending = _validate_baseline(value, schema=SCHEMA_V1, fields=_V1_RECORD_FIELDS)
    return deepcopy(record)


def validate_record(value: object) -> dict:
    record, pending = _validate_baseline(value, schema=SCHEMA, fields=_RECORD_FIELDS)
    _legacy_reconciliation(record.get("legacy_reconciliation"), pending)
    contributions = record.get("contributions")
    qualifications = record.get("qualifications")
    coverage = record.get("operator_coverage")
    satisfied = record.get("satisfied_contributions")
    if not isinstance(contributions, list) or len(contributions) > 10_000:
        raise DeploymentError("DeploymentRecord contributions must be a bounded array")
    if not isinstance(qualifications, list) or len(qualifications) > 20_000:
        raise DeploymentError("DeploymentRecord qualifications must be a bounded array")
    if not isinstance(coverage, list) or len(coverage) > 10_000:
        raise DeploymentError("DeploymentRecord operator_coverage must be a bounded array")
    if satisfied != []:
        raise DeploymentError("Phase 2B DeploymentRecord satisfied_contributions must be empty")
    contribution_ids, merge_shas, receipt_ids = set(), set(), set()
    contribution_by_id = {}
    for item in contributions:
        row = _contribution(item)
        if row["id"] in contribution_ids or row["merge_sha"] in merge_shas:
            raise DeploymentError("DeploymentRecord contains a duplicate contribution or merge sha")
        receipt_id = row["publication_receipt_id"]
        if receipt_id in receipt_ids:
            raise DeploymentError("DeploymentRecord contains a reused publication receipt id")
        contribution_ids.add(row["id"]); merge_shas.add(row["merge_sha"]); receipt_ids.add(receipt_id)
        contribution_by_id[row["id"]] = row
    if any(item not in contribution_ids for item in satisfied):
        raise DeploymentError("satisfied contribution does not exist in this record")
    qualification_ids = set()
    prior_qualifications = {}
    for item in qualifications:
        row = _qualification(item)
        if row["id"] in qualification_ids or row["contribution_id"] not in contribution_ids:
            raise DeploymentError("qualification identity or contribution reference is invalid")
        if row["main_check"]["head_sha"] != contribution_by_id[row["contribution_id"]]["merge_sha"]:
            raise DeploymentError("qualification main check does not name its contribution merge sha")
        for replaced in row["supersedes"]:
            if (replaced not in prior_qualifications
                    or prior_qualifications[replaced]["decision"] != "blocked"
                    or prior_qualifications[replaced]["contribution_id"] == row["contribution_id"]):
                raise DeploymentError("qualification supersedes an unknown, later, or same-contribution receipt")
        qualification_ids.add(row["id"]); prior_qualifications[row["id"]] = row
    coverage_ids = set()
    for item in coverage:
        row = _coverage(item)
        if row["id"] in coverage_ids:
            raise DeploymentError("DeploymentRecord contains duplicate operator coverage")
        coverage_ids.add(row["id"])
    return deepcopy(record)


def record_path(service_id: str, *, root: Path | None = None) -> Path:
    if not isinstance(service_id, str) or not _SERVICE_ID.fullmatch(service_id):
        raise DeploymentError("service id is invalid")
    return Path(root or config.ROOT) / "deployments" / f"{service_id}.json"


def load(service_id: str, *, root: Path | None = None) -> dict | None:
    value = S.read_json(record_path(service_id, root=root), None)
    if value is None:
        return None
    record = validate_record(value)
    if record["service"]["id"] != service_id:
        raise DeploymentError("DeploymentRecord service identity differs from its path")
    return record


def upgrade_v1_for_migration(service_id: str, *, root: Path | None = None) -> dict:
    """Atomically extend the dormant Phase 2A record; normal reads never auto-migrate."""
    path = record_path(service_id, root=root)
    with S.ordered_file_lock(path.with_suffix(".lock"), S.LockLevel.ACTIVATION_MAINTENANCE):
        raw = S.read_json(path, None)
        if raw is None:
            raise DeploymentError(f"DeploymentRecord for {service_id} is not initialized")
        if isinstance(raw, dict) and raw.get("schema_version") == SCHEMA:
            current = validate_record(raw)
            if current["service"]["id"] != service_id:
                raise DeploymentError("DeploymentRecord service identity differs from its path")
            return current
        prior = validate_v1_record(raw)
        if prior["service"]["id"] != service_id:
            raise DeploymentError("DeploymentRecord service identity differs from its path")
        upgraded = {key: deepcopy(value) for key, value in prior.items() if key != "record_sha256"}
        upgraded.update({
            "schema_version": SCHEMA, "legacy_reconciliation": None,
            "contributions": [], "qualifications": [], "operator_coverage": [],
            "satisfied_contributions": [],
        })
        upgraded["record_sha256"] = _canonical_hash(upgraded, "record_sha256")
        validate_record(upgraded); S.write_json(path, upgraded)
        return deepcopy(upgraded)


def initialize_for_migration(
    *, service_id: str, unit: str, repository_id: str, checkout: str,
    runtime_manifest: dict, operator_provenance: dict | None = None,
    legacy_pending_bytes: bytes | None = None, root: Path | None = None,
) -> dict:
    """Persist the one baseline record; this API has no runtime caller in Phase 2A."""
    service, repository = _identity(service_id, unit, repository_id, checkout)
    manifest = _manifest(deepcopy(runtime_manifest), unit=unit, checkout=checkout)
    if manifest.get("role") == "running_install":
        if operator_provenance is not None:
            raise DeploymentError("a running manifest cannot use operator-provenance bootstrap")
        activated_sha, anchor = _active_anchor(manifest)
    else:
        if operator_provenance is None:
            raise DeploymentError("stopped/unverifiable baseline requires explicit operator provenance")
        activated_sha, anchor = _operator_anchor(manifest, deepcopy(operator_provenance))
    candidate = {
        "schema_version": SCHEMA, "service": service, "repository": repository,
        "activated_sha": activated_sha, "bootstrap_anchor": anchor,
        "legacy_pending": _legacy_pending(legacy_pending_bytes), "legacy_reconciliation": None,
        "contributions": [], "qualifications": [], "operator_coverage": [],
        "satisfied_contributions": [],
    }
    candidate["record_sha256"] = _canonical_hash(candidate, "record_sha256")
    validate_record(candidate)
    path = record_path(service_id, root=root)
    lock = path.with_suffix(".lock")
    with S.ordered_file_lock(lock, S.LockLevel.ACTIVATION_MAINTENANCE):
        existing = S.read_json(path, None)
        if existing is not None:
            current = validate_record(existing)
            if current != candidate:
                raise DeploymentConflict(f"conflicting DeploymentRecord for {service_id}")
            return current
        S.write_json(path, candidate)
    return deepcopy(candidate)


def _append(service_id: str, field: str, row: dict, *, root: Path | None = None) -> dict:
    """Append one immutable fact under the sole deployment lock."""
    path = record_path(service_id, root=root)
    with S.ordered_file_lock(path.with_suffix(".lock"), S.LockLevel.ACTIVATION_MAINTENANCE):
        record = load(service_id, root=root)
        if record is None:
            raise DeploymentError(f"DeploymentRecord for {service_id} is not initialized")
        existing = next((item for item in record[field] if item.get("id") == row["id"]), None)
        if existing is not None:
            same_contribution = (field == "contributions"
                                 and {key: value for key, value in existing.items() if key != "observed_at"}
                                 == {key: value for key, value in row.items() if key != "observed_at"})
            if existing != row and not same_contribution:
                raise DeploymentConflict(f"conflicting {field} fact {row['id']}")
            return record
        record[field].append(deepcopy(row))
        record["record_sha256"] = _canonical_hash(record, "record_sha256")
        validate_record(record)
        S.write_json(path, record)
        return deepcopy(record)


def append_contribution(
    service_id: str, *, publication_receipt_id: str, publication_receipt_sha256: str,
    merge_sha: str, observed_at: str, root: Path | None = None,
) -> dict:
    """Record that one immutable publication receipt merged; this does not qualify it."""
    facts = {
        "publication_receipt_id": _identifier(publication_receipt_id, "publication receipt id"),
        "publication_receipt_sha256": _digest(
            publication_receipt_sha256, "publication receipt sha256"),
        "merge_sha": _sha(merge_sha, "contribution merge sha"),
        "observed_at": _timestamp(observed_at, "contribution observed_at"),
    }
    facts["id"] = hashlib.sha256(S._canonical_json({  # noqa: SLF001
        "publication_receipt_id": facts["publication_receipt_id"],
        "merge_sha": facts["merge_sha"],
    })).hexdigest()
    return _append(service_id, "contributions", _contribution(facts), root=root)


def append_qualification(
    service_id: str, *, contribution_id: str, observed_at: str,
    main_check_status: str, main_check_head_sha: str, main_check_evidence_sha256: str,
    open_findings_count: int, open_findings_evidence_sha256: str,
    merge_hold: bool, merge_hold_reason: str | None = None,
    supersedes: list[str] | None = None, root: Path | None = None,
) -> dict:
    """Append one evidence-derived qualification or blocker; prior facts stay immutable."""
    facts = {
        "contribution_id": _digest(contribution_id, "qualification contribution id"),
        "observed_at": _timestamp(observed_at, "qualification observed_at"),
        "main_check": {
            "status": main_check_status,
            "head_sha": _sha(main_check_head_sha, "qualification main_check head sha"),
            "evidence_sha256": _digest(
                main_check_evidence_sha256, "qualification main_check evidence sha256"),
        },
        "open_findings": {
            "count": open_findings_count,
            "evidence_sha256": _digest(
                open_findings_evidence_sha256, "qualification finding evidence sha256"),
        },
        "merge_hold": {"held": merge_hold, "reason": merge_hold_reason},
        "decision": ("eligible" if main_check_status == "passed"
                     and open_findings_count == 0 and merge_hold is False else "blocked"),
        "supersedes": sorted(set(supersedes or [])),
    }
    facts["id"] = hashlib.sha256(S._canonical_json(facts)).hexdigest()  # noqa: SLF001
    return _append(service_id, "qualifications", _qualification(facts), root=root)


def append_operator_coverage(
    service_id: str, *, start_exclusive: str, commits: list[str], observed_at: str,
    reason: str, authorization: dict, root: Path | None = None,
) -> dict:
    """Cover one exact contiguous first-parent range without fabricating check evidence."""
    start = _sha(start_exclusive, "operator coverage start")
    if (not isinstance(commits, list) or not commits or len(commits) > 10_000
            or len(set(commits)) != len(commits)):
        raise DeploymentError("operator coverage commits must be 1..10000 unique ordered shas")
    exact_commits = [_sha(item, "operator coverage commit") for item in commits]
    facts = {
        "start_exclusive": start, "end_inclusive": exact_commits[-1],
        "commit_count": len(exact_commits), "commits_sha256": _commit_list_digest(exact_commits),
        "observed_at": _timestamp(observed_at, "operator coverage observed_at"),
        "reason": _text(reason, "operator coverage reason", 500),
        "authorization": _authorization(deepcopy(authorization)),
    }
    facts["id"] = hashlib.sha256(S._canonical_json(facts)).hexdigest()  # noqa: SLF001
    return _append(service_id, "operator_coverage", _coverage(facts), root=root)


def reconcile_legacy_pending(
    service_id: str, *, observed_at: str, first_parent_chain: list[str],
    root: Path | None = None,
) -> dict:
    """Resolve a recognized marker only through a fully covered anchor-to-head chain."""
    path = record_path(service_id, root=root)
    with S.ordered_file_lock(path.with_suffix(".lock"), S.LockLevel.ACTIVATION_MAINTENANCE):
        record = load(service_id, root=root)
        if record is None:
            raise DeploymentError(f"DeploymentRecord for {service_id} is not initialized")
        pending = record.get("legacy_pending")
        if pending is None:
            raise DeploymentError("there is no imported legacy pending evidence to reconcile")
        if pending.get("status") != "recognized":
            raise DeploymentError("unrecognized legacy pending evidence has no mechanical head to reconcile")
        chain = [_sha(item, "legacy reconciliation chain commit") for item in first_parent_chain]
        existing = record["legacy_reconciliation"]
        if existing is not None:
            if (not chain or chain[0] != existing["anchor_sha"]
                    or chain[-1] != existing["head_sha"]
                    or len(chain) - 1 != existing["commit_count"]
                    or _commit_list_digest(chain[1:]) != existing["commits_sha256"]):
                raise DeploymentConflict("legacy pending evidence already has different reconciled ancestry")
            return record
        result = _evaluate_record(record, pending["head"], first_parent_chain, include_legacy=False)
        if result["blockers"]:
            raise DeploymentError("legacy pending baseline is not fully covered: "
                                  + ", ".join(item["kind"] for item in result["blockers"]))
        facts = {
            "evidence_sha256": pending["evidence_sha256"],
            "observed_at": _timestamp(observed_at, "legacy reconciliation observed_at"),
            "anchor_sha": result["anchor_sha"], "head_sha": pending["head"],
            "commit_count": len(chain) - 1,
            "commits_sha256": _commit_list_digest(chain[1:]),
            "coverage_sha256": hashlib.sha256(S._canonical_json(result["covered"])).hexdigest(),  # noqa: SLF001
        }
        facts["id"] = hashlib.sha256(S._canonical_json({  # noqa: SLF001
            key: facts[key] for key in _LEGACY_RECONCILIATION_FIELDS - {"id", "observed_at"}
        })).hexdigest()
        row = _legacy_reconciliation(facts, pending)
        record["legacy_reconciliation"] = row
        record["record_sha256"] = _canonical_hash(record, "record_sha256")
        validate_record(record); S.write_json(path, record)
        return deepcopy(record)


def _evaluate_record(record: dict, candidate_sha: str, first_parent_chain: list[str],
                     *, include_legacy: bool) -> dict:
    candidate = _sha(candidate_sha, "eligibility candidate")
    if (not isinstance(first_parent_chain, list) or not first_parent_chain
            or len(first_parent_chain) > 100_000
            or len(set(first_parent_chain)) != len(first_parent_chain)):
        raise DeploymentError("first-parent chain must be a bounded unique ordered sha array")
    chain = [_sha(item, "first-parent chain commit") for item in first_parent_chain]
    anchor = record["activated_sha"] or record["bootstrap_anchor"]["sha"]
    if chain[0] != anchor or chain[-1] != candidate:
        raise DeploymentError("first-parent chain must run from the effective anchor through candidate")
    contributions = {item["merge_sha"]: item for item in record["contributions"]}
    contribution_by_id = {item["id"]: item for item in record["contributions"]}
    chain_index = {sha: index for index, sha in enumerate(chain)}
    included_ids = {item["id"] for sha, item in contributions.items() if sha in chain[1:]}
    included_qualifications = [
        item for item in record["qualifications"] if item["contribution_id"] in included_ids
    ]
    qualification_by_id = {item["id"]: item for item in included_qualifications}
    superseded_by_eligible = set()
    for item in included_qualifications:
        if item["decision"] != "eligible":
            continue
        correcting_sha = contribution_by_id[item["contribution_id"]]["merge_sha"]
        for replaced in item["supersedes"]:
            prior = qualification_by_id.get(replaced)
            if prior is None:
                continue
            blocked_sha = contribution_by_id[prior["contribution_id"]]["merge_sha"]
            if chain_index[correcting_sha] > chain_index[blocked_sha]:
                superseded_by_eligible.add(replaced)
    by_contribution: dict[str, list[dict]] = {}
    for item in included_qualifications:
        by_contribution.setdefault(item["contribution_id"], []).append(item)
    operator_by_sha: dict[str, str] = {}
    for receipt in record["operator_coverage"]:
        start_index = chain_index.get(receipt["start_exclusive"])
        end_index = chain_index.get(receipt["end_inclusive"])
        if start_index is None or end_index is None or end_index <= start_index:
            continue
        covered = chain[start_index + 1:end_index + 1]
        if (len(covered) == receipt["commit_count"]
                and _commit_list_digest(covered) == receipt["commits_sha256"]):
            for sha in covered:
                operator_by_sha.setdefault(sha, receipt["id"])
    blockers, covered_rows = [], []
    for contribution in record["contributions"]:
        if contribution["merge_sha"] not in chain:
            blockers.append({"kind": "contribution_not_in_candidate_ancestry",
                             "sha": contribution["merge_sha"],
                             "reference": contribution["id"]})
    for sha in chain[1:]:
        contribution = contributions.get(sha)
        if contribution is not None:
            qualifications = by_contribution.get(contribution["id"], [])
            eligible = [item for item in qualifications if item["decision"] == "eligible"]
            unresolved = [item for item in qualifications if item["decision"] == "blocked"
                          and item["id"] not in superseded_by_eligible]
            if eligible and not unresolved:
                covered_rows.append({"sha": sha, "kind": "qualified_contribution",
                                     "reference": eligible[-1]["id"]})
            elif qualifications and not unresolved and any(
                    item["id"] in superseded_by_eligible for item in qualifications):
                covered_rows.append({"sha": sha, "kind": "corrected_contribution",
                                     "reference": contribution["id"]})
            else:
                blockers.append({"kind": "unqualified_contribution", "sha": sha,
                                 "reference": contribution["id"]})
        elif sha in operator_by_sha:
            covered_rows.append({"sha": sha, "kind": "operator_provenance",
                                 "reference": operator_by_sha[sha]})
        else:
            blockers.append({"kind": "uncovered_commit", "sha": sha, "reference": None})
    if include_legacy and record["legacy_pending"] is not None:
        reconciliation = record["legacy_reconciliation"]
        valid_reconciliation = False
        if reconciliation is not None:
            end = chain_index.get(reconciliation["head_sha"], -1)
            prefix = chain[:end + 1] if end >= 0 else []
            valid_reconciliation = bool(
                prefix and prefix[0] == reconciliation["anchor_sha"]
                and len(prefix) - 1 == reconciliation["commit_count"]
                and _commit_list_digest(prefix[1:]) == reconciliation["commits_sha256"]
            )
            if valid_reconciliation:
                covered_shas = {item["sha"] for item in covered_rows}
                valid_reconciliation = all(sha in covered_shas for sha in prefix[1:])
        if not valid_reconciliation:
            blockers.append({"kind": LEGACY_PENDING_BLOCKER, "sha": None,
                             "reference": record["legacy_pending"]["evidence_sha256"]})
    return {
        "eligible": not blockers, "anchor_sha": anchor, "candidate_sha": candidate,
        "covered": covered_rows, "blockers": blockers,
    }


def evaluate_eligibility(
    service_id: str, *, candidate_sha: str, first_parent_chain: list[str],
    root: Path | None = None,
) -> dict:
    """Project eligibility from durable facts and one exact anchor-through-candidate chain."""
    record = load(service_id, root=root)
    if record is None:
        raise DeploymentError(f"DeploymentRecord for {service_id} is not initialized")
    return _evaluate_record(record, candidate_sha, first_parent_chain, include_legacy=True)
