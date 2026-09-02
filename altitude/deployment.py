"""Dormant per-service deployment authority and one-time baseline import."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import config, state as S

SCHEMA = "altitude.deployment/v1"
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


def validate_record(value: object) -> dict:
    record = _exact(value, {"schema_version", "service", "repository", "activated_sha",
                            "bootstrap_anchor", "legacy_pending", "record_sha256"},
                    "DeploymentRecord")
    if record.get("schema_version") != SCHEMA:
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
        "legacy_pending": _legacy_pending(legacy_pending_bytes),
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
