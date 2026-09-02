"""Headless Claude Code and Codex command builders and runners."""
from __future__ import annotations
import ast
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, state as S

logger = logging.getLogger(__name__)
CODEX_SYSTEMD_PREFIX = "altitude-codex-"
MANAGED_SYSTEMD_PREFIX = "altitude-worker-"
SYSTEMD_RUN_BIN = shutil.which("systemd-run") or "systemd-run"
SYSTEMCTL_BIN = shutil.which("systemctl") or "systemctl"
ENV_BIN = shutil.which("env") or "/usr/bin/env"

_CODEX_SECRET_ENV = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|ACCESS_KEY|SESSION_KEY|CREDENTIAL)(?:$|_)",
    re.I,
)
_CODEX_CONTROL_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
_CODEX_SENSITIVE_ENV = {
    "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_ASKPASS",
    "GIT_SSH_COMMAND", "ALTITUDE_L2_TOKEN", "ALTITUDE_L2_CAPABILITY",
}
_CODEX_SAFE_ENV = {
    "HOME", "PATH", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "COLORTERM", "TZ",
    "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NO_COLOR",
    "ALTITUDE_HOME", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_ACTOR", "ALTITUDE_SESSION_KEY",
    "ALTITUDE_DISPATCH_ID",
}

# Claude's stream-json can be much larger than its final answer. Keep raw capture bounded while preserving evidence
# from both ends; L1 applies the same default cap to the artifacts it exposes.
RAW_CAPTURE_CAP = 2 * 1024 * 1024
CODEX_ANSWER_CAP = 2 * 1024 * 1024
CODEX_EVENT_CAP = 8 * 1024 * 1024
CODEX_PROVIDER_SESSION_ID_CAP = 1024  # UTF-8 bytes
CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: Do not call the custom `apply_patch` tool, because its filesystem verifier "
    "cannot create its bwrap namespace under this host's AppArmor policy. For every edit, call the shell command "
    "`apply_patch` through the exec tool and pass the patch on stdin; this stays inside the Codex workspace-write "
    "sandbox and its configured writable roots."
)


PHYSICAL_TRANSITION_STAGES = (
    "planned", "prior_stopped", "spawned", "bound", "result_observed", "empty",
    "complete", "failed",
)
_PHYSICAL_NEXT = {
    "planned": {"prior_stopped"},
    "prior_stopped": {"spawned"},
    "spawned": {"bound"},
    "bound": {"result_observed"},
    "result_observed": {"empty"},
    "empty": {"complete", "failed"},
    "complete": set(),
    "failed": set(),
}
_PHYSICAL_KEYS = {
    "version", "transition_id", "subject_kind", "subject_id", "generation", "provider",
    "process_unit_id", "provider_session_request", "message_id", "recovery_episode_id",
    "recovery_permit_revision", "intent_digest", "stage", "receipts", "error", "revision",
}
_PHYSICAL_INTENT_KEYS = (
    "version", "transition_id", "subject_kind", "subject_id", "generation", "provider",
    "process_unit_id", "provider_session_request", "message_id", "recovery_episode_id",
    "recovery_permit_revision",
)


class PhysicalTransitionError(RuntimeError):
    """A physical owner/helper transition is malformed or attempted an unsafe move."""


class ManagedUnitError(RuntimeError):
    """A managed process unit could not be observed or made provably empty."""


def read_bounded_codex_output(path: Path, limit: int, label: str) -> str:
    """Read completed Codex output without permitting an unbounded allocation."""
    source = Path(path)
    try:
        if source.stat().st_size > limit:
            raise RuntimeError(f"{label} exceeds {limit} bytes")
        with source.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise RuntimeError(f"cannot read {label}: {exc}") from exc
    if len(raw) > limit:
        raise RuntimeError(f"{label} exceeds {limit} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} is not valid UTF-8") from exc


def _run_codex_to_bounded_spools(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int,
                                 event_spool: Path, answer_path: Path, on_start=None,
                                 on_abort=None) -> subprocess.CompletedProcess:
    """Drain Codex incrementally and stop it as soon as either declared output crosses its cap."""
    initial = event_spool.stat().st_size if event_spool.exists() else 0
    if initial > CODEX_EVENT_CAP:
        raise RuntimeError(f"Codex event spool exceeds {CODEX_EVENT_CAP} bytes")
    proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    if on_start:
        on_start(proc.pid)
    overflow = threading.Event()
    errors: list[BaseException] = []
    stderr = _BoundedRawCapture()

    def drain_events() -> None:
        written = initial
        try:
            with event_spool.open("ab", buffering=0) as stream:
                while chunk := proc.stdout.read(65536):
                    if written + len(chunk) > CODEX_EVENT_CAP:
                        overflow.set()
                        continue
                    stream.write(chunk); written += len(chunk)
                os.fsync(stream.fileno())
        except BaseException as exc:  # noqa: BLE001 - relay the exact drain failure to the owner thread
            errors.append(exc); overflow.set()

    def drain_stderr() -> None:
        try:
            while chunk := proc.stderr.read(65536):
                stderr.add(chunk.decode("utf-8", errors="replace"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc); overflow.set()

    readers = [threading.Thread(target=drain_events), threading.Thread(target=drain_stderr)]
    for reader in readers:
        reader.start()
    deadline, exceeded, timed_out = time.monotonic() + timeout, None, False
    while proc.poll() is None:
        try:
            if answer_path.exists() and answer_path.stat().st_size > CODEX_ANSWER_CAP:
                exceeded = f"Codex answer exceeds {CODEX_ANSWER_CAP} bytes"
            elif overflow.is_set():
                exceeded = f"Codex event spool exceeds {CODEX_EVENT_CAP} bytes"
        except OSError as exc:
            errors.append(exc); exceeded = f"cannot inspect Codex bounded output: {exc}"
        if exceeded or time.monotonic() >= deadline:
            timed_out = not exceeded
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
            if on_abort:
                on_abort()
            break
        time.sleep(0.01)
    for reader in readers:
        reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        proc.kill(); proc.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=5)
        proc.stdout.close(); proc.stderr.close()
        raise RuntimeError("Codex output drain did not stop with its process")
    proc.stdout.close(); proc.stderr.close()
    if errors:
        raise RuntimeError(f"Codex output drain failed: {errors[0]}")
    if exceeded or overflow.is_set():
        raise RuntimeError(exceeded or f"Codex event spool exceeds {CODEX_EVENT_CAP} bytes")
    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout)
    if answer_path.exists() and answer_path.stat().st_size > CODEX_ANSWER_CAP:
        raise RuntimeError(f"Codex answer exceeds {CODEX_ANSWER_CAP} bytes")
    S._fsync_directory(event_spool.parent)  # noqa: SLF001 - shared durability primitive
    stderr_text, stderr_truncated = stderr.render()
    result = subprocess.CompletedProcess(argv, proc.returncode, None, stderr_text)
    result.stderr_truncated = stderr_truncated
    return result


def _physical_json(value):
    """Return a detached strict-JSON copy suitable for stable receipt comparison."""
    if not isinstance(value, dict):
        raise PhysicalTransitionError("physical transition value must be an object")
    try:
        return json.loads(S._canonical_json(value))  # noqa: SLF001 - share Phase 0C's strict JSON contract
    except (TypeError, ValueError) as exc:
        raise PhysicalTransitionError(f"physical transition contains invalid JSON: {exc}") from exc


def deterministic_process_unit(subject_kind: str, subject_id: str, generation: str) -> str:
    """Derive one bounded systemd unit name from the exact physical subject generation."""
    if subject_kind not in ("l3", "owner", "helper"):
        raise PhysicalTransitionError(f"unknown physical subject kind {subject_kind!r}")
    if not isinstance(subject_id, str) or not subject_id.strip():
        raise PhysicalTransitionError("physical subject id must be a nonempty string")
    if not isinstance(generation, str) or not generation.strip():
        raise PhysicalTransitionError("physical generation must be a nonempty string")
    identity = f"{subject_kind}\0{subject_id}\0{generation}"
    label = re.sub(r"[^A-Za-z0-9_.-]", "-", subject_id).strip(".-")[:48] or "subject"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{MANAGED_SYSTEMD_PREFIX}{subject_kind}-{label}-{digest}.service"


def _provider_session_request(value) -> dict:
    request = _physical_json(value)
    if not isinstance(request, dict) or request.get("kind") not in ("fresh", "resume"):
        raise PhysicalTransitionError("provider session request must be fresh or resume")
    expected = {"kind"} if request["kind"] == "fresh" else {"kind", "session_id"}
    if set(request) != expected:
        raise PhysicalTransitionError("provider session request has unknown or missing fields")
    if request["kind"] == "resume" and (
        not isinstance(request["session_id"], str) or not request["session_id"]
    ):
        raise PhysicalTransitionError("resume request requires a nonempty provider session id")
    return request


def _physical_intent_digest(record: dict) -> str:
    intent = {key: record.get(key) for key in _PHYSICAL_INTENT_KEYS}
    return hashlib.sha256(S._canonical_json(intent)).hexdigest()  # noqa: SLF001


def new_physical_transition(*, transition_id: str, subject_kind: str, subject_id: str,
                            generation: str, provider: str, provider_session_request: dict,
                            message_id: str, recovery_episode_id: str | None = None,
                            recovery_permit_revision: int | None = None) -> dict:
    """Create an inert launch intent for embedding in its domain's authoritative record."""
    for label, value in (("transition id", transition_id), ("message id", message_id)):
        if not isinstance(value, str) or not value:
            raise PhysicalTransitionError(f"{label} must be a nonempty string")
    if provider not in ("claude", "codex"):
        raise PhysicalTransitionError(f"unknown provider {provider!r}")
    if (recovery_episode_id is None) != (recovery_permit_revision is None):
        raise PhysicalTransitionError("recovery episode and permit revision must be present together")
    if recovery_episode_id is not None and (
        not isinstance(recovery_episode_id, str) or not recovery_episode_id
        or isinstance(recovery_permit_revision, bool)
        or not isinstance(recovery_permit_revision, int) or recovery_permit_revision < 1
    ):
        raise PhysicalTransitionError("invalid recovery episode or permit revision")
    record = {
        "version": 1,
        "transition_id": transition_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "generation": generation,
        "provider": provider,
        "process_unit_id": deterministic_process_unit(subject_kind, subject_id, generation),
        "provider_session_request": _provider_session_request(provider_session_request),
        "message_id": message_id,
        "recovery_episode_id": recovery_episode_id,
        "recovery_permit_revision": recovery_permit_revision,
        "intent_digest": "",
        "stage": "planned",
        "receipts": {},
        "error": None,
        "revision": 0,
    }
    record["intent_digest"] = _physical_intent_digest(record)
    return validate_physical_transition(record)


def _required_receipts(stage: str) -> tuple[str, ...]:
    ordered = PHYSICAL_TRANSITION_STAGES[1:6]
    if stage == "planned":
        return ()
    if stage in ("complete", "failed"):
        return (*ordered, stage)
    return ordered[:ordered.index(stage) + 1]


def _validate_empty_observation(observation: object, unit: str) -> None:
    keys = {"process_unit_id", "load_state", "active_state", "sub_state", "control_group", "population", "empty"}
    if (not isinstance(observation, dict) or set(observation) != keys
            or observation.get("process_unit_id") != unit or observation.get("population") != "empty"
            or observation.get("empty") is not True
            or observation.get("active_state") not in ("inactive", "failed")
            or not all(isinstance(observation.get(key), str)
                       for key in ("load_state", "active_state", "sub_state", "control_group"))):
        raise PhysicalTransitionError(f"{unit} does not have an exact empty-unit observation")


def _validate_unit_observation(observation: object, unit: str) -> dict:
    keys = {"process_unit_id", "load_state", "active_state", "sub_state", "control_group", "population", "empty"}
    if (not isinstance(observation, dict) or set(observation) != keys
            or observation.get("process_unit_id") != unit
            or observation.get("population") not in ("empty", "populated", "unknown")
            or not isinstance(observation.get("empty"), bool)
            or not all(isinstance(observation.get(key), str)
                       for key in ("load_state", "active_state", "sub_state", "control_group"))):
        raise PhysicalTransitionError("ownership_uncertain: managed-unit observation is not exact")
    should_be_empty = (observation["load_state"] == "not-found"
                       or (observation["active_state"] in ("inactive", "failed")
                           and observation["population"] == "empty"))
    if observation["empty"] is not should_be_empty or observation["population"] == "unknown":
        raise PhysicalTransitionError("ownership_uncertain: managed-unit population is not proven")
    return observation


def _validate_durable_result(observation: object, record: dict) -> dict:
    if observation is None:
        return {"present": False}
    value = _physical_json(observation)
    if value == {"present": False}:
        return value
    keys = {"present", "process_unit_id", "intent_digest", "result_id", "sha256"}
    if (set(value) != keys or value.get("present") is not True
            or value.get("process_unit_id") != record["process_unit_id"]
            or value.get("intent_digest") != record["intent_digest"]
            or not isinstance(value.get("result_id"), str) or not value["result_id"]
            or not isinstance(value.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
        raise PhysicalTransitionError("ownership_uncertain: durable result does not match the physical intent")
    return value


def validate_physical_transition(record: dict) -> dict:
    """Validate and detach one closed physical transition without reading external state."""
    value = _physical_json(record)
    if not isinstance(value, dict) or set(value) != _PHYSICAL_KEYS:
        raise PhysicalTransitionError("physical transition has unknown or missing fields")
    if value.get("version") != 1 or value.get("stage") not in PHYSICAL_TRANSITION_STAGES:
        raise PhysicalTransitionError("physical transition version or stage is invalid")
    for key in ("transition_id", "subject_id", "generation", "message_id"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise PhysicalTransitionError(f"physical transition {key} must be a nonempty string")
    if value.get("subject_kind") not in ("l3", "owner", "helper"):
        raise PhysicalTransitionError("physical transition subject kind is invalid")
    if value.get("provider") not in ("claude", "codex"):
        raise PhysicalTransitionError("physical transition provider is invalid")
    expected_unit = deterministic_process_unit(value["subject_kind"], value["subject_id"], value["generation"])
    if value.get("process_unit_id") != expected_unit:
        raise PhysicalTransitionError("physical transition process unit is not deterministic")
    value["provider_session_request"] = _provider_session_request(value.get("provider_session_request"))
    episode, permit = value.get("recovery_episode_id"), value.get("recovery_permit_revision")
    if (episode is None) != (permit is None) or (
        episode is not None and (
            not isinstance(episode, str) or not episode or isinstance(permit, bool)
            or not isinstance(permit, int) or permit < 1
        )
    ):
        raise PhysicalTransitionError("physical transition recovery identity is invalid")
    digest = value.get("intent_digest")
    if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or digest != _physical_intent_digest(value)):
        raise PhysicalTransitionError("physical transition intent digest is invalid")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PhysicalTransitionError("physical transition revision is invalid")
    receipts = value.get("receipts")
    required = _required_receipts(value["stage"])
    if not isinstance(receipts, dict) or set(receipts) != set(required):
        raise PhysicalTransitionError("physical transition receipts do not match its stage")
    expected_revision = len(receipts) + (1 if value.get("error") is not None else 0)
    if revision != expected_revision:
        raise PhysicalTransitionError("physical transition revision is not exactly derived from receipts and error")
    for name, receipt in receipts.items():
        if not isinstance(receipt, dict):
            raise PhysicalTransitionError(f"physical transition {name} receipt must be an object")
    if "prior_stopped" in receipts:
        prior = receipts["prior_stopped"]
        previous = prior.get("previous_process_unit_id")
        expected_keys = ({"previous_process_unit_id", "empty"} if previous is None
                         else {"previous_process_unit_id", "empty", "observation"})
        if set(prior) != expected_keys or prior.get("empty") is not True:
            raise PhysicalTransitionError("prior_stopped requires proven empty prior ownership")
        if previous is not None:
            if not isinstance(previous, str) or not previous:
                raise PhysicalTransitionError("prior process unit id is invalid")
            _validate_empty_observation(prior.get("observation"), previous)
    if "spawned" in receipts:
        spawned = receipts["spawned"]
        if spawned.get("process_unit_id") != expected_unit:
            raise PhysicalTransitionError("spawned receipt names a different process unit")
        if spawned.get("launched") is True:
            if set(spawned) != {"process_unit_id", "launched"}:
                raise PhysicalTransitionError("positive spawned receipt has an invalid shape")
        elif spawned.get("launched") is False:
            if (set(spawned) != {"process_unit_id", "launched", "reason"}
                    or not isinstance(spawned.get("reason"), str) or not spawned["reason"].strip()
                    or value.get("error") is None):
                raise PhysicalTransitionError("negative spawned receipt requires a closed reason and durable error")
        else:
            raise PhysicalTransitionError("spawned receipt must say whether launch occurred")
    if "bound" in receipts:
        bound = receipts["bound"]
        if bound.get("bound") is True:
            if (set(bound) != {"bound", "physical_worker_id", "provider_session_id"}
                    or not isinstance(bound.get("physical_worker_id"), str) or not bound["physical_worker_id"]
                    or not isinstance(bound.get("provider_session_id"), str) or not bound["provider_session_id"]):
                raise PhysicalTransitionError("positive bound receipt has an invalid shape")
            if receipts["spawned"].get("launched") is not True:
                raise PhysicalTransitionError("an unlaunched process cannot bind a provider identity")
        elif bound.get("bound") is False:
            if (set(bound) != {"bound", "reason"}
                    or not isinstance(bound.get("reason"), str) or not bound["reason"].strip()
                    or value.get("error") is None):
                raise PhysicalTransitionError("negative bound receipt requires a closed reason and durable error")
        else:
            raise PhysicalTransitionError("bound receipt must say whether provider identity was bound")
        if receipts["spawned"].get("launched") is False and bound.get("bound") is not False:
            raise PhysicalTransitionError("an unlaunched process must remain unbound")
    if "result_observed" in receipts:
        result = receipts["result_observed"]
        if (set(result) != {"result_id", "sha256"}
                or not isinstance(result.get("result_id"), str) or not result["result_id"]
                or not isinstance(result.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", result["sha256"]) is None):
            raise PhysicalTransitionError("result_observed receipt requires a stable result id and SHA-256")
    if "empty" in receipts:
        if set(receipts["empty"]) != {
            "process_unit_id", "load_state", "active_state", "sub_state", "control_group", "population", "empty"
        }:
            raise PhysicalTransitionError("empty receipt has an invalid shape")
        if receipts["empty"].get("process_unit_id") != expected_unit:
            raise PhysicalTransitionError("empty receipt names a different process unit")
        _validate_empty_observation(receipts["empty"], expected_unit)
    error = value.get("error")
    if error is not None and (not isinstance(error, str) or not error):
        raise PhysicalTransitionError("physical transition error must be null or nonempty text")
    if value["stage"] == "complete" and error is not None:
        raise PhysicalTransitionError("a completed physical transition cannot carry an error")
    if value["stage"] == "failed" and error is None:
        raise PhysicalTransitionError("a failed physical transition requires an error")
    negative = ((receipts.get("spawned") or {}).get("launched") is False
                or (receipts.get("bound") or {}).get("bound") is False)
    if negative and "result_observed" in receipts:
        expected_error_hash = hashlib.sha256(error.encode("utf-8")).hexdigest()
        if receipts["result_observed"] != {"result_id": "transition:error", "sha256": expected_error_hash}:
            raise PhysicalTransitionError("negative launch/bind requires the exact durable error receipt")
    if "complete" in receipts and (set(receipts["complete"]) != {"status"}
                                    or receipts["complete"].get("status") != "complete"):
        raise PhysicalTransitionError("complete receipt has an invalid shape")
    if "failed" in receipts and (set(receipts["failed"]) != {"status"}
                                  or receipts["failed"].get("status") != "failed"):
        raise PhysicalTransitionError("failed receipt has an invalid shape")
    return value


def note_physical_transition_error(record: dict, error: str) -> dict:
    """Persist the first inert error while reconciliation continues toward proven emptiness."""
    value = validate_physical_transition(record)
    if value["stage"] in ("complete", "failed"):
        raise PhysicalTransitionError("terminal physical transition cannot accept another error")
    if not isinstance(error, str) or not error.strip():
        raise PhysicalTransitionError("physical transition error must be nonempty text")
    error = error.strip()
    if value["error"] is not None:
        if value["error"] != error:
            raise PhysicalTransitionError("physical transition already records a different error")
        return value
    value["error"] = error
    value["revision"] += 1
    return validate_physical_transition(value)


def advance_physical_transition(record: dict, expected_stage: str, next_stage: str,
                                receipt: dict) -> dict:
    """Advance exactly one durable stage; an identical receipt replay is idempotent."""
    value = validate_physical_transition(record)
    if next_stage not in _PHYSICAL_NEXT.get(expected_stage, set()):
        raise PhysicalTransitionError(f"illegal physical transition {expected_stage} -> {next_stage}")
    receipt = _physical_json(receipt)
    if not isinstance(receipt, dict):
        raise PhysicalTransitionError("physical transition receipt must be an object")
    if value["stage"] == next_stage:
        if value["receipts"].get(next_stage) == receipt:
            return value
        raise PhysicalTransitionError(f"conflicting replay of physical stage {next_stage}")
    if value["stage"] != expected_stage:
        raise PhysicalTransitionError(
            f"physical transition is {value['stage']}, expected {expected_stage}"
        )
    if next_stage == "failed" and value["error"] is None:
        raise PhysicalTransitionError("physical failure must be recorded before terminalization")
    if next_stage == "complete" and value["error"] is not None:
        raise PhysicalTransitionError("physical transition with an error cannot complete")
    value["receipts"][next_stage] = receipt
    value["stage"] = next_stage
    value["revision"] += 1
    return validate_physical_transition(value)


def reconcile_physical_transition(record: dict, durable_result_observation: dict | None) -> dict:
    """Inspect the exact unit and result marker without launching, stopping, or persisting.

    The returned decision is advisory to a later owner-family adapter. Ambiguous observations refuse
    with ``ownership_uncertain``; the helper never guesses a receipt or mutates the caller's record.
    """
    value = validate_physical_transition(record)
    unit = value["process_unit_id"]
    try:
        unit_observation = _validate_unit_observation(observe_managed_unit(unit), unit)
    except ManagedUnitError as exc:
        raise PhysicalTransitionError(f"ownership_uncertain: {exc}") from exc
    result = _validate_durable_result(durable_result_observation, value)
    recorded_result = value["receipts"].get("result_observed")
    if recorded_result is not None:
        if not result["present"] or {
            "result_id": result["result_id"], "sha256": result["sha256"]
        } != recorded_result:
            raise PhysicalTransitionError("ownership_uncertain: recorded result is absent or changed")

    stage = value["stage"]
    empty, result_present = unit_observation["empty"], result["present"]
    if stage == "planned":
        if not empty or result_present:
            raise PhysicalTransitionError("ownership_uncertain: physical effect exists before prior-stop receipt")
        decision = "prove_prior_stopped"
    elif stage == "prior_stopped":
        if value["error"] is not None:
            if not empty:
                raise PhysicalTransitionError(
                    "ownership_uncertain: a pre-spawn failure has a populated unit"
                )
            expected = {
                "result_id": "transition:error",
                "sha256": hashlib.sha256(value["error"].encode("utf-8")).hexdigest(),
            }
            if result_present and {
                "result_id": result["result_id"], "sha256": result["sha256"]
            } != expected:
                raise PhysicalTransitionError(
                    "ownership_uncertain: pre-spawn durable result differs from the recorded error"
                )
            decision = "record_spawn_failure"
        elif empty and not result_present:
            raise PhysicalTransitionError(
                "ownership_uncertain: launch may have run and its unit been collected"
            )
        else:
            decision = "record_spawned"
    elif stage == "spawned":
        if value["receipts"]["spawned"]["launched"] is False:
            if not empty:
                raise PhysicalTransitionError("ownership_uncertain: an unlaunched transition has a populated unit")
            decision = "record_bound_failure"
        elif result_present:
            decision = "forward_repair"
        elif empty:
            decision = "process_missing"
        else:
            decision = "record_bound"
    elif stage == "bound":
        if value["receipts"]["bound"]["bound"] is False:
            if not empty:
                raise PhysicalTransitionError("ownership_uncertain: an unbound transition has a populated unit")
            decision = "record_result" if result_present else "record_error_result"
        elif result_present:
            decision = "record_result"
        elif empty:
            decision = "process_missing"
        else:
            decision = "running"
    elif stage == "result_observed":
        decision = "record_empty" if empty else "wait_for_empty"
    elif stage == "empty":
        if not empty:
            raise PhysicalTransitionError("ownership_uncertain: empty receipt contradicts the managed unit")
        decision = "record_failed" if value["error"] else "record_complete"
    else:
        if not empty:
            raise PhysicalTransitionError("ownership_uncertain: terminal transition still owns processes")
        decision = "settled"
    return {
        "transition_id": value["transition_id"], "stage": stage, "decision": decision,
        "process_unit": unit_observation, "durable_result": result,
    }


class EngineCapabilityError(RuntimeError):
    """A provider lacks the closed capability required for an autonomous launch."""


def require_autonomous_engine(engine: str) -> None:
    if engine not in config.AUTONOMOUS_ENGINES:
        raise EngineCapabilityError(
            f"{engine} autonomous/mutating launch disabled: supervised foreground ownership is unproved"
        )


def codex_isolation_config(cwd: Path, *, writable: bool = True,
                           readable_roots: list[Path] | None = None) -> list[str]:
    """Suppress mutable user/project/plugin sources; managed host policy remains authoritative.

    User config and rules are ignored by command-line flags, and plugins are disabled. Do not synthesize a dynamic
    ``projects.<path>.trust_level`` override: the Codex strict schema rejects that mutable map before it can report a
    thread identity. Hooks are disabled for these headless turns; the Codex permission profile and
    whole-turn containment, not shell-text automation, are the write and process boundaries. Host-managed
    requirements remain authoritative.
    """
    profile = "altitude_worker" if writable else "altitude_reader"
    access = "write" if writable else "read"
    # Start from Codex's maintained workspace baseline so its own executable/runtime remain available, then deny the
    # broad root and reopen only minimal runtime paths plus the effective workspace roots. Coordinators narrow those
    # roots to read; every worker keeps Git/Codex metadata read-only.
    parent = ":workspace"
    codex_binary = Path(shutil.which(config.CODEX_BIN) or config.CODEX_BIN).resolve()
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}").resolve()
    extra_reads = "".join(f", {json.dumps(str(Path(root).resolve()))}=\"read\""
                          for root in [codex_binary, *(readable_roots or [])])
    filesystem = ('{ ":root"="deny", ":minimal"="read", '
                  f'":workspace_roots"={{ "."="{access}", ".git"="read", ".codex"="read" }}'
                  f', {json.dumps(str(runtime_dir))}="deny"{extra_reads} }}')
    return ["features.hooks=false", "features.plugins=false",
            "features.remote_plugin=false", "features.apps=false", "features.multi_agent=false",
            "features.goals=false", 'web_search="disabled"', 'approval_policy="never"',
            "shell_environment_policy.ignore_default_excludes=false",
            f"default_permissions=\"{profile}\"", f"permissions.{profile}.extends=\"{parent}\"",
            f"permissions.{profile}.filesystem={filesystem}"]


def cap_raw(data: bytes, cap: int, *, total: int | None = None) -> tuple[bytes, bool]:
    """Cap raw bytes, retaining the head and tail with an exact drop notice."""
    total = len(data) if total is None else total
    if total <= cap:
        return data, False
    dropped = total - cap
    while True:
        notice = f"\n\n[altitude: raw output truncated; {dropped} bytes dropped]\n\n".encode()
        kept = max(0, cap - len(notice))
        exact = total - kept
        if exact == dropped:
            break
        dropped = exact
    head = kept // 2
    tail = kept - head
    return data[:head] + notice + (data[-tail:] if tail else b""), True


class _BoundedRawCapture:
    """Collect at most ``cap`` bytes, retaining the head and tail with an exact drop notice."""

    def __init__(self, cap: int | None = None):
        self.cap = RAW_CAPTURE_CAP if cap is None else cap
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()
        self.head_limit = self.cap // 2
        self.tail_limit = self.cap - self.head_limit

    def add(self, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        self.total += len(data)
        room = self.head_limit - len(self.head)
        if room > 0:
            self.head.extend(data[:room])
            data = data[room:]
        if data:
            self.tail.extend(data)
            if len(self.tail) > self.tail_limit:
                del self.tail[:len(self.tail) - self.tail_limit]

    def render(self) -> tuple[str, bool]:
        data, truncated = cap_raw(bytes(self.head + self.tail), self.cap, total=self.total)
        return data.decode("utf-8", errors="replace"), truncated

# ---- usage limit: the subscription window closing is a timed hold, not a failure ----------
LIMIT_TEXT = re.compile(r"hit your (?:session|usage) limit|usage limit reached|out of (?:extra )?usage|rate limit reached", re.I)
RESETS = re.compile(r"resets?\s+(?:(?:at|in)\s+)?(?:([A-Za-z]{3,9}\s+\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", re.I)
TEMPORARY_CAPACITY_TEXT = "Selected model is at capacity. Please try a different model."


def temporary_capacity_in(text: str | None) -> bool:
    """Recognize the provider's exact temporary-capacity warning, not a quota exhaustion."""
    return bool(text and TEMPORARY_CAPACITY_TEXT in text)


def usage_limit_in(text: str | None, quota: dict | None = None, now: datetime | None = None) -> str | None:
    """The UTC ISO time the window reopens if `text`/`quota` say it is exhausted, else None.

    Claude Code says it two ways: a synthetic assistant message ("You've hit your session limit · resets 8pm
    (America/Los_Angeles)") and, on the same record, `quotaLimits: {status: rejected, resetsAt: <epoch>}`."""
    now = now or datetime.now(timezone.utc)
    if quota and quota.get("status") == "rejected" and quota.get("resetsAt"):
        return datetime.fromtimestamp(int(quota["resetsAt"]), timezone.utc).isoformat(timespec="seconds")
    if not text or not LIMIT_TEXT.search(text):
        return None
    m = RESETS.search(text)
    if not m:
        return (now + timedelta(hours=1)).isoformat(timespec="seconds")  # no time given: hold an hour, then look again
    day, hour, minute, ampm, tz = m.groups()
    try:
        zone = ZoneInfo(tz) if tz else (datetime.now().astimezone().tzinfo or timezone.utc)
    except Exception:  # noqa: BLE001 — unknown zone name
        zone = timezone.utc
    local = now.astimezone(zone)
    when = local.replace(hour=int(hour) % 12 + (12 if ampm.lower() == "pm" else 0), minute=int(minute or 0), second=0, microsecond=0)
    if day:
        for fmt in ("%b %d", "%B %d"):
            try:
                d = datetime.strptime(day, fmt); when = when.replace(month=d.month, day=d.day); break
            except ValueError:
                continue
    if when <= local:
        when += timedelta(days=1)
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def usage_limit_path() -> Path:
    return config.MONITOR_DIR / "usage-limit.json"


def note_usage_limit(until: str, detail: str = "") -> bool:
    """Record an exhausted window. True when the reset time is news, so callers post one FYI per window."""
    p = usage_limit_path(); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        cur = {}
    if cur.get("until") == until:
        return False
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"until": until, "seen": datetime.now(timezone.utc).isoformat(timespec="seconds"), "detail": detail[:200]}))
    os.replace(tmp, p)
    return True


def usage_hold() -> str | None:
    """The reset time while the window is exhausted, else None. Dispatch and L3 turns check this first."""
    p = usage_limit_path()
    try:
        until = json.loads(p.read_text()).get("until") if p.exists() else None
    except (ValueError, OSError):
        return None
    if until and datetime.fromisoformat(until) > datetime.now(timezone.utc):
        return until
    return None


def claude_stop(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "stop", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    return (p.stdout or p.stderr).strip()


def clean_env() -> dict:
    """Nested launches need CLAUDE* unset (verified); keep PATH sane for systemd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = str(config.REPO / "bin") + ":" + env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")
    return env


def codex_env(extra_env: dict | None = None, *, retain_user_bus: bool = False) -> dict:
    """Build the host environment for Codex without passing control capabilities or ambient credentials.

    A contained launch retains the user bus only in the outer ``systemd-run`` client. System services do not
    necessarily inherit the interactive session's bus variables, so the trusted launcher synthesizes their canonical
    per-user values when absent. The command executed inside the transient service gets both variables explicitly
    unset by :func:`_codex_service_command` before Codex starts.
    """
    source = clean_env()
    source.update(extra_env or {})
    env = {key: value for key, value in source.items()
           if key in _CODEX_SAFE_ENV or key.startswith("LC_")}
    if retain_user_bus:
        runtime_dir = source.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env["DBUS_SESSION_BUS_ADDRESS"] = (source.get("DBUS_SESSION_BUS_ADDRESS")
                                           or f"unix:path={runtime_dir}/bus")
    env["TMPDIR"] = "/tmp"
    for key in list(env):
        if key in _CODEX_SENSITIVE_ENV or _CODEX_SECRET_ENV.search(key):
            env.pop(key, None)
    if not retain_user_bus:
        for key in _CODEX_CONTROL_ENV:
            env.pop(key, None)
    return env


def claude_settings() -> Path:
    """The settings every Claude launch without a per-dispatch file gets: auto-compact at the configured window,
    stated explicitly rather than inherited from ~/.claude/settings.json. Rewritten when the number changes."""
    p = config.ROOT / "claude-settings.json"
    want = {"autoCompactWindow": config.AUTOCOMPACT_WINDOW}
    try:
        cur = json.loads(p.read_text())
    except (OSError, ValueError):
        cur = None
    if cur != want:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(want, indent=2) + "\n")
    return p


def claude_print(prompt: str, *, cwd: Path, resume: str | None = None, persona: Path | None = None,
                 allowed_tools: str | None = None, tools: str | None = None, permission_mode: str = "auto",
                 schema: Path | None = None, model: str | None = None, max_turns: int | None = None,
                 settings: Path | None = None, extra_env: dict | None = None, on_text=None, on_start=None,
                 timeout: int = config.L3_TURN_TIMEOUT) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error, and bounded
    raw_stdout/raw_stderr; `limited` (a reset time) when the subscription window is exhausted — the call is not even
    made while a hold is in force.

    `on_start(pid)` is called the moment the child exists. The turn outlives altd, so its pid lets a
    restarted server distinguish an in-flight turn from a dead one."""
    require_autonomous_engine("claude")
    held = usage_hold()
    if held:
        return {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0, "turns": 0,
                "structured": None, "error": f"usage limit: window exhausted until {held}", "tools": [], "limited": held,
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False, "raw_stderr_truncated": False}
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
           "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if tools is not None:
        cmd += ["--tools", tools]
    if schema:  # the flag takes the JSON text itself, not a path
        cmd += ["--json-schema", Path(schema).read_text() if Path(schema).exists() else str(schema)]
    if model:
        cmd += ["--model", model]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd += ["--settings", str(settings or claude_settings())]
    if resume:
        cmd += ["--resume", resume]
    env = clean_env()
    env.update(extra_env or {})
    # prompt goes through stdin: --allowedTools is variadic and would swallow a positional prompt
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    if on_start:
        on_start(proc.pid)
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    stdout_capture, stderr_capture = _BoundedRawCapture(), _BoundedRawCapture()

    def drain_stderr() -> None:
        while chunk := proc.stderr.read(65536):
            stderr_capture.add(chunk)

    drain = threading.Thread(target=drain_stderr, daemon=True)
    drain.start()
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    out = {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0,
           "turns": 0, "structured": None, "error": None, "tools": []}
    parts: list[str] = []
    failure = None
    try:
        for line in proc.stdout:
            stdout_capture.add(line)
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("session_id"):
                out["session_id"] = o["session_id"]
            typ = o.get("type")
            if typ == "stream_event":
                delta = (o.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    if on_text:
                        on_text(delta["text"])
            elif typ == "assistant":
                out["turns"] += 1
                msg = o.get("message") or {}
                u = msg.get("usage") or {}
                if u:
                    out["context_tokens"] = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
                for c in msg.get("content") or []:
                    if c.get("type") == "tool_use":
                        out["tools"].append(c.get("name"))
                    elif c.get("type") == "text" and msg.get("model") == "<synthetic>":
                        out["synthetic"] = (out.get("synthetic") or "") + str(c.get("text") or "")
                q = o.get("quotaLimits") or msg.get("quotaLimits")
                if isinstance(q, dict):
                    out["quota"] = q
            elif typ == "result":
                out["usage"] = o.get("usage") or {}
                out["cost"] = float(o.get("total_cost_usd") or 0)
                if o.get("structured_output") is not None:
                    out["structured"] = o["structured_output"]
                if o.get("is_error"):
                    out["error"] = str(o.get("result") or o.get("error") or "")[:500]
                elif not parts and o.get("result"):
                    parts.append(str(o["result"]))
        proc.wait()
    except BaseException as exc:
        failure = exc
        raise
    finally:
        killer.cancel()
        proc.stdout.close()
        drain.join(timeout=2)
        proc.stderr.close()
        drain.join(timeout=2)
        if failure is not None:
            failure.raw_stdout, failure.raw_stdout_truncated = stdout_capture.render()
            failure.raw_stderr, failure.raw_stderr_truncated = stderr_capture.render()
    raw_stdout, raw_stdout_truncated = stdout_capture.render()
    raw_stderr, raw_stderr_truncated = stderr_capture.render()
    out.update({"raw_stdout": raw_stdout, "raw_stderr": raw_stderr,
                "raw_stdout_truncated": raw_stdout_truncated, "raw_stderr_truncated": raw_stderr_truncated})
    out["text"] = "".join(parts).strip()
    lim = usage_limit_in(out.get("synthetic") or out["text"] or raw_stderr, out.get("quota"))
    if lim:
        note_usage_limit(lim, (out.get("synthetic") or out["text"])[:200])
        out["limited"] = lim
        out["error"] = f"usage limit: window exhausted until {lim}"
    if proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {raw_stderr.strip()[:500]}"
    if schema and out["structured"] is None and out["text"]:
        try:
            out["structured"] = json.loads(out["text"])
        except ValueError:
            pass
    return out


def _guarded_spawn(cmd: list[str], *, cwd: Path, env: dict, guard=None) -> subprocess.CompletedProcess:
    """Serialize only the irreversible Popen boundary; do not hold the guard while the CLI waits."""
    with guard if guard is not None else nullcontext():
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env)
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        exc.stdout, exc.stderr = stdout, stderr
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def claude_bg(name: str, prompt: str, *, cwd: Path, worktree: str | None = None, persona: Path | None = None,
              permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
              settings: Path | None = None, extra_env: dict | None = None, spawn_guard=None) -> dict:
    """Start a background session (disabled unless Claude has the closed autonomous capability)."""
    require_autonomous_engine("claude")
    before = {row.get("id") for row in claude_agents() if row.get("name") == name and row.get("id")}
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--permission-mode", permission_mode]
    if worktree:
        cmd += ["-w", worktree]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    cmd += ["--settings", str(settings or claude_settings())]
    env = clean_env()
    env.update(extra_env or {})
    p = _guarded_spawn(cmd + [prompt], cwd=cwd, env=env, guard=spawn_guard)
    row = _new_claude_agent(name, before)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": row}


def claude_agents() -> list[dict]:
    try:
        p = subprocess.run([config.CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True,
                           timeout=60, env=clean_env())
        data = json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        # An empty fallback would read as "every L2 vanished"; fail loudly instead.
        raise RuntimeError(f"claude agents --json failed: {e}") from e
    if p.returncode != 0:
        raise RuntimeError(f"claude agents --json exit {p.returncode}: {(p.stderr or '')[-300:]}")
    if isinstance(data, dict):
        data = data.get("agents") or data.get("sessions") or []
    return data if isinstance(data, list) else []


def find_agent(name: str | None = None, agent_id: str | None = None, session_id: str | None = None) -> dict | None:
    for a in claude_agents():
        if agent_id and a.get("id") == agent_id:
            return a
        if session_id and a.get("sessionId") == session_id:
            return a
        if name and a.get("name") == name:
            return a
    return None


def _new_claude_agent(name: str, before: set[str]) -> dict | None:
    """Resolve only the concrete worker created after this launch, never an old same-name job."""
    rows = [row for row in claude_agents()
            if row.get("name") == name and row.get("id") and row.get("id") not in before]
    live = [row for row in rows
            if row.get("state") not in ("failed", "done", "stopped") and row.get("status") != "exited"]
    candidates = live or rows
    return max(candidates, key=lambda row: str(row.get("startedAt") or ""), default=None)


def claude_resume_bg(name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path | None = None,
                     permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
                     settings: Path | None = None,
                     extra_env: dict | None = None, spawn_guard=None) -> dict:
    require_autonomous_engine("claude")
    before = {row.get("id") for row in claude_agents() if row.get("name") == name and row.get("id")}
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--resume", session_id, "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    cmd += ["--settings", str(settings or claude_settings())]
    env = clean_env()
    env.update(extra_env or {})
    p = _guarded_spawn(cmd + [prompt], cwd=cwd, env=env, guard=spawn_guard)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode,
            "agent": _new_claude_agent(name, before)}


def claude_rm(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "rm", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    return (p.stdout + p.stderr).strip()


def _removed_l2_transport(engine: str) -> None:
    """Keep legacy adapter names fail-closed without restoring a second L2 owner path."""
    require_autonomous_engine(engine)
    raise EngineCapabilityError(
        f"{engine} detached L2 transport removed: use the TaskRecord-owned transition"
    )


def start_l2(engine: str, *_args, **_kwargs) -> dict:
    _removed_l2_transport(engine)


def resume_l2(engine: str, *_args, **_kwargs) -> dict:
    _removed_l2_transport(engine)


def stop_l2_worker(engine: str, *_args, **_kwargs) -> str:
    _removed_l2_transport(engine)


def remove_l2_worker(engine: str, *_args, **_kwargs) -> str:
    _removed_l2_transport(engine)


def codex_worker(_worker_id: str | None, *, job_root: Path) -> None:
    """Legacy job-root evidence is never authoritative for the durable Codex owner."""
    del job_root
    return None


class CodexSandboxPreflightError(RuntimeError):
    """A workspace-write turn cannot start because its host sandbox cannot safely write every promised root."""

    def __init__(self, roots: list[str], detail: str):
        self.roots = list(roots)
        detail = str(detail).strip() or "unknown bwrap failure"
        self.detail = detail if len(detail) <= 500 else detail[:245] + " ... " + detail[-250:]
        super().__init__(f"Codex sandbox preflight failed for {', '.join(self.roots)}: {self.detail}")


class CodexContainmentError(RuntimeError):
    """A Codex turn cannot start or finish without a proven-empty transient cgroup."""


def _codex_unit(worker_id: str) -> str:
    """A systemd-safe, collision-resistant transient service name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(worker_id))
    return f"{CODEX_SYSTEMD_PREFIX}{safe}.service"


def _codex_service_command(unit: str, command: list[str], child_env: dict[str, str]) -> list[str]:
    """Run a turn in a user-manager-created transient service with its own cgroup.

    ``--wait --pipe`` keeps the launch synchronous while the user manager, rather than the hardened Altitude parent,
    creates the child. This lets nested bwrap initialize without weakening altd's ``NoNewPrivileges=yes`` boundary.
    Unlike a process group, the service cgroup retains descendants that call ``setsid`` or double-fork. The inner
    Codex sandbox supplies the PID namespace; keeping syscall filters off the outer service preserves nested bwrap.
    """
    # A transient service inherits the user manager's environment, not the launching client's. Clear it completely
    # and reconstruct only the already-sanitized child environment so task identity survives without ambient manager
    # credentials or control sockets crossing the boundary.
    scrub = [ENV_BIN, "-i", *(f"{key}={child_env[key]}" for key in sorted(child_env))]
    return [SYSTEMD_RUN_BIN, "--user", "--wait", "--pipe", f"--unit={unit}", "--quiet", "--collect",
            "--same-dir", "--expand-environment=no", "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=NoNewPrivileges=no", "--", *scrub, *command]


def managed_service_command(unit: str, command: list[str], child_env: dict[str, str]) -> list[str]:
    """Build the common foreground managed-unit command without changing current Codex callers."""
    if not isinstance(unit, str) or not unit.startswith(MANAGED_SYSTEMD_PREFIX) or not unit.endswith(".service"):
        raise ManagedUnitError(f"invalid Altitude managed process unit {unit!r}")
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ManagedUnitError("managed process command must contain nonempty string arguments")
    if not isinstance(child_env, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) for key, value in child_env.items()
    ):
        raise ManagedUnitError("managed process environment must contain only string names and values")
    return _codex_service_command(unit, command, child_env)


def _systemd_unit_properties(unit: str) -> dict[str, str]:
    """Read the security-relevant state of one transient user unit, failing closed with no user bus."""
    cmd = [SYSTEMCTL_BIN, "--user", "show", unit, "--property=LoadState", "--property=ActiveState",
           "--property=SubState", "--property=ControlGroup"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                env=codex_env(retain_user_bus=True))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexContainmentError(f"cannot inspect Codex containment unit {unit}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl show failed").strip()
        # A collected transient unit is expected to disappear. This response proves the user manager was reached;
        # connection/permission failures remain hard failures.
        if "could not be found" in detail.lower() or "not found" in detail.lower():
            return {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                    "ControlGroup": ""}
        raise CodexContainmentError(f"cannot inspect Codex containment unit {unit}: {detail[:500]}")
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            props[key] = value
    required = {"LoadState", "ActiveState", "ControlGroup"}
    if not required.issubset(props):
        raise CodexContainmentError(f"incomplete systemd state for Codex containment unit {unit}")
    return props


def _cgroup_population(control_group: str) -> str:
    """Observe a v2 cgroup as empty, populated, or unknown without guessing on I/O failure."""
    if not control_group:
        return "empty"
    relative = Path(control_group.lstrip("/"))
    if ".." in relative.parts:
        return "unknown"
    events = Path("/sys/fs/cgroup") / relative / "cgroup.events"
    try:
        values = dict(line.split(None, 1) for line in events.read_text().splitlines() if len(line.split(None, 1)) == 2)
    except FileNotFoundError:
        return "empty"
    except OSError:
        return "unknown"
    return {"0": "empty", "1": "populated"}.get(values.get("populated"), "unknown")


def _cgroup_unpopulated(control_group: str) -> bool:
    """Preserve the Codex fail-closed boolean over the exact population observation."""
    return _cgroup_population(control_group) == "empty"


def _codex_unit_empty(unit: str) -> bool:
    props = _systemd_unit_properties(unit)
    if props.get("LoadState") == "not-found":
        return True
    if props.get("ActiveState") not in ("inactive", "failed"):
        return False
    return _cgroup_unpopulated(props.get("ControlGroup") or "")


def observe_managed_unit(unit: str) -> dict:
    """Return an exact provider-neutral manager/cgroup observation for one named unit."""
    if not isinstance(unit, str) or not unit.startswith(MANAGED_SYSTEMD_PREFIX) or not unit.endswith(".service"):
        raise ManagedUnitError(f"invalid Altitude managed process unit {unit!r}")
    try:
        props = _systemd_unit_properties(unit)
    except CodexContainmentError as exc:
        raise ManagedUnitError(str(exc)) from exc
    required = {"LoadState", "ActiveState", "SubState", "ControlGroup"}
    if not required.issubset(props):
        raise ManagedUnitError(f"incomplete systemd state for managed process unit {unit}")
    population = ("empty" if props["LoadState"] == "not-found"
                  else _cgroup_population(props.get("ControlGroup") or ""))
    empty = (props["LoadState"] == "not-found"
             or (props["ActiveState"] in ("inactive", "failed") and population == "empty"))
    return {
        "process_unit_id": unit,
        "load_state": props["LoadState"],
        "active_state": props["ActiveState"],
        "sub_state": props["SubState"],
        "control_group": props["ControlGroup"],
        "population": population,
        "empty": empty,
    }


def prove_altitude_service_replacement(prior_instance: str, current_instance: str) -> dict:
    """Prove systemd replaced one altd invocation before it may resume a claimed delivery."""
    def identity(value: str) -> tuple[int, str]:
        parts = str(value or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "altd" or not parts[1].isdigit() or not parts[2]:
            raise ManagedUnitError("L3 service instance is not bound to pid and systemd invocation")
        return int(parts[1]), parts[2]
    _prior_pid, prior_invocation = identity(prior_instance)
    current_pid, current_invocation = identity(current_instance)
    if prior_instance == current_instance or prior_invocation == current_invocation:
        raise ManagedUnitError("L3 delivery replacement requires a different service invocation")
    cmd = [SYSTEMCTL_BIN, "--user", "show", "altitude.service", "--property=LoadState",
           "--property=ActiveState", "--property=SubState", "--property=ControlGroup",
           "--property=MainPID", "--property=InvocationID"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                env=codex_env(retain_user_bus=True))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedUnitError(f"cannot inspect altitude.service replacement: {exc}") from exc
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    required = {"LoadState", "ActiveState", "SubState", "ControlGroup", "MainPID", "InvocationID"}
    population = _cgroup_population(props.get("ControlGroup") or "") if result.returncode == 0 else "unknown"
    if (result.returncode != 0 or set(props) < required or props["LoadState"] != "loaded"
            or props["ActiveState"] != "active" or props["MainPID"] != str(current_pid)
            or props["InvocationID"] != current_invocation or population != "populated"):
        raise ManagedUnitError("cannot prove the exact replacement altitude.service generation active")
    return {"unit": "altitude.service", "invocation_id": current_invocation, "main_pid": current_pid,
            "active_state": props["ActiveState"], "sub_state": props["SubState"],
            "control_group": props["ControlGroup"], "population": population}


def spawn_managed_unit(transition: dict, command: list[str], *, cwd: Path,
                       launcher_env: dict[str, str], child_env: dict[str, str],
                       stdin=None, stdout=None, stderr=None) -> subprocess.Popen:
    """Cross the spawn boundary only for a prior-stopped, caller-persisted launch intent.

    Persistence deliberately remains with the domain aggregate. The caller must durably embed the
    returned transition before invoking this function, then record the spawn receipt afterward.
    """
    record = validate_physical_transition(transition)
    if record["stage"] != "prior_stopped" or record["receipts"]["prior_stopped"].get("empty") is not True:
        raise PhysicalTransitionError("managed spawn requires a prior_stopped transition")
    before = observe_managed_unit(record["process_unit_id"])
    if before["empty"] is not True:
        raise ManagedUnitError(f"managed process unit {record['process_unit_id']} is not empty before spawn")
    argv = managed_service_command(record["process_unit_id"], command, child_env)
    return subprocess.Popen(
        argv, cwd=str(Path(cwd)), stdin=stdin, stdout=stdout, stderr=stderr,
        env=dict(launcher_env), start_new_session=True,
    )


def stop_managed_unit(unit: str, timeout: float = 5.0) -> dict:
    """Stop every descendant and return an exact empty receipt, or fail closed."""
    if not isinstance(unit, str) or not unit.startswith(MANAGED_SYSTEMD_PREFIX) or not unit.endswith(".service"):
        raise ManagedUnitError(f"invalid Altitude managed process unit {unit!r}")
    try:
        _stop_codex_unit(unit, timeout)
    except CodexContainmentError as exc:
        raise ManagedUnitError(str(exc)) from exc
    observation = observe_managed_unit(unit)
    if observation["empty"] is not True:
        raise ManagedUnitError(f"managed process unit {unit} remained populated after stop")
    return observation


def _wait_codex_unit_empty(unit: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _codex_unit_empty(unit):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _stop_codex_unit(unit: str, timeout: float = 5.0) -> None:
    """Stop every process in the transient service, escalating to cgroup-wide SIGKILL if needed."""
    try:
        stopped = subprocess.run([SYSTEMCTL_BIN, "--user", "stop", "--no-block", unit],
                                 capture_output=True, text=True,
                                 timeout=timeout, env=codex_env(retain_user_bus=True))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexContainmentError(f"cannot stop Codex containment unit {unit}: {exc}") from exc
    if stopped.returncode != 0 and not _codex_unit_empty(unit):
        detail = (stopped.stderr or stopped.stdout or "systemctl stop failed").strip()
        raise CodexContainmentError(f"cannot stop Codex containment unit {unit}: {detail[:500]}")
    if _wait_codex_unit_empty(unit, timeout):
        return
    if _codex_unit_empty(unit):
        return
    killed = subprocess.run([SYSTEMCTL_BIN, "--user", "kill", "--kill-who=all", "--signal=SIGKILL", unit],
                            capture_output=True, text=True, timeout=timeout,
                            env=codex_env(retain_user_bus=True))
    if killed.returncode != 0 and _codex_unit_empty(unit):
        return
    if killed.returncode != 0 or not _wait_codex_unit_empty(unit, timeout):
        detail = (killed.stderr or killed.stdout or "unit remained populated").strip()
        raise CodexContainmentError(f"Codex containment unit {unit} did not empty: {detail[:500]}")


def codex_probe_roots(cwd: Path, extra_config: list[str] | None) -> list[str]:
    """Return every root a workspace-write override promises; malformed overrides are not promises.

    Non-existent roots stay in the list so the in-sandbox write fails closed instead of silently narrowing access.
    """
    base = Path(cwd).resolve()
    roots = [str(base)]
    for override in extra_config or []:
        key, separator, value = override.partition("=")
        if not separator or key.strip() != "sandbox_workspace_write.writable_roots":
            continue
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            logger.warning("Codex sandbox preflight ignored unparseable writable-roots override: %r", override)
            continue
        if isinstance(parsed, (list, tuple)):
            roots.extend(str((base / root).resolve()) for root in parsed if isinstance(root, str))
    return list(dict.fromkeys(roots))


def _codex_network_access(extra_config: list[str] | None) -> bool:
    """Return the effective workspace-write network setting from Codex's TOML-style overrides."""
    network_access = False
    for override in extra_config or []:
        key, separator, value = override.partition("=")
        if not separator or key.strip() != "sandbox_workspace_write.network_access":
            continue
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            network_access = normalized == "true"
    return network_access


def codex_sandbox_preflight(cwd: Path, extra_config: list[str] | None = None, timeout: int = 15) -> None:
    """Prove the requested Linux sandbox can create, sync, and remove a sentinel in every writable root.

    `codex sandbox` cannot express these inline roots reliably, so probe the capability Codex depends on directly.
    Other platforms and hosts without bwrap stay available with one warning.
    """
    if not sys.platform.startswith("linux"):
        logger.warning("Codex sandbox preflight skipped: platform is not Linux")
        return
    bwrap = shutil.which("bwrap")
    if not bwrap:
        logger.warning("Codex sandbox preflight skipped: bwrap is not resolvable")
        return
    roots = codex_probe_roots(cwd, extra_config)
    sentinel = f".altitude-codex-write-probe-{uuid.uuid4().hex}"
    script = """set -eu
name=$1
shift
for root do
    probe=$root/$name
    { printf '%s\\n' altitude-codex-write-probe > "$probe" && sync "$probe" && rm -f "$probe"; } || {
        status=$?
        printf 'codex sandbox preflight failed for root: %s\\n' "$root" >&2
        exit "$status"
    }
done
"""
    cmd = [bwrap, "--dev-bind", "/", "/", "--unshare-user"]
    if not _codex_network_access(extra_config):
        cmd.append("--unshare-net")
    cmd += ["--die-with-parent", "/bin/sh", "-c", script, "altitude-codex-write-probe", sentinel, *roots]
    unit = _codex_unit(f"preflight-{uuid.uuid4().hex}")
    launcher_env = codex_env(retain_user_bus=True)
    contained_cmd = _codex_service_command(unit, cmd, codex_env())
    detail = ""
    try:
        probe = subprocess.run(contained_cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                               env=launcher_env)
        try:
            empty = _wait_codex_unit_empty(unit)
        except CodexContainmentError as exc:
            empty = False
            detail = str(exc)
        if probe.returncode == 0 and empty:
            return
        if not empty:
            try:
                _stop_codex_unit(unit)
            except CodexContainmentError as exc:
                detail = str(exc)
            detail = detail or f"Codex sandbox preflight service {unit} remained populated"
        else:
            detail = probe.stderr or f"bwrap exited {probe.returncode} without stderr"
    except subprocess.TimeoutExpired as exc:
        try:
            _stop_codex_unit(unit)
        except CodexContainmentError as stop_exc:
            detail = str(stop_exc)
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        detail = detail or stderr or f"bwrap timed out after {timeout} seconds"
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"
    cleanup_errors = []
    for root in roots:
        try:
            (Path(root) / sentinel).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"{root}: {type(exc).__name__}: {exc}")
    if cleanup_errors:
        detail = f"{detail.rstrip()}; cleanup failed: {'; '.join(cleanup_errors)}"
    raise CodexSandboxPreflightError(roots, detail)


def _codex_session_id(value: object) -> str | None:
    """Accept only a nonempty Codex thread id bounded by encoded wire bytes."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return value if len(value.encode("utf-8")) <= CODEX_PROVIDER_SESSION_ID_CAP else None
    except UnicodeEncodeError:
        return None


def codex_exec(prompt: str, *, cwd: Path, schema: Path | None = None, sandbox: str = "read-only",
               model: str | None = None, timeout: int = 900, extra_config: list[str] | None = None,
               effort: str | None = None, extra_env: dict | None = None,
               fault_context: dict[str, str] | None = None, resume: str | None = None,
               on_start=None, contain: bool | None = None,
               readable_roots: list[Path] | None = None, answer_path: Path | None = None,
               event_spool: Path | None = None) -> dict:
    """Codex headless (optional L1 implementers/reviewers) — verified: needs stdin closed, -o for
    the answer. `extra_config` are `-c key=value` overrides (sandbox network, writable roots). Token usage comes from
    the `turn.completed` events on stdout. Workspace-write turns are contained by default; ``contain=True`` also
    places a read-only coordinator turn in a transient cgroup before it may return trusted actions."""
    try:
        effective_config = [*(extra_config or []), *codex_isolation_config(
            cwd, writable=sandbox == "workspace-write", readable_roots=readable_roots)]
    except RuntimeError as exc:
        return {"text": "", "structured": None, "returncode": 1, "engine_started": False,
                "fault_recorded": None, "usage": {}, "error": str(exc),
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                "raw_stderr_truncated": False}
    if sandbox == "workspace-write":
        try:
            codex_sandbox_preflight(cwd, effective_config)
        except CodexSandboxPreflightError as exc:
            from . import incidents  # local import avoids the incident/engine module cycle
            fault_recorded = None
            try:
                incidents.system_fault("codex-sandbox", f"roots={exc.roots!r}; {exc.detail}",
                                     **(fault_context or {}))
                fault_recorded = "codex-sandbox"
            except Exception:  # noqa: BLE001 — fault persistence must not replace the deterministic gate failure
                logger.exception("Failed to record Codex sandbox preflight system fault")
            error = str(exc)
            if len(error) > 500:
                error = error[:245] + " ... " + error[-250:]
            # Callers already persist and stamp ordinary failures; returning that contract avoids duplicate faults
            # and stranded L3 turns while still guaranteeing Codex was never invoked.
            return {"text": "", "structured": None, "returncode": 1, "engine_started": False,
                    "fault_recorded": fault_recorded,
                    "usage": {}, "error": error,
                    "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                    "raw_stderr_truncated": False}
    temporary_output = answer_path is None
    if temporary_output:
        with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as outf:
            out_path = Path(outf.name)
    else:
        out_path = Path(answer_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    if resume:
        cmd = [config.CODEX_BIN, "exec", "resume", "--json", "--strict-config", "-o", str(out_path), "--skip-git-repo-check",
               "--ignore-user-config", "--ignore-rules"]
    else:
        cmd = [config.CODEX_BIN, "exec", "--json", "--strict-config", "-o", str(out_path), "-C", str(cwd),
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
    if schema:
        cmd += ["--output-schema", str(schema)]
    if model:
        cmd += ["-m", model]
    for kv in effective_config:
        cmd += ["-c", kv]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    contained = sandbox == "workspace-write" if contain is None else contain
    unit = _codex_unit(f"sync-{uuid.uuid4().hex}") if contained else None
    containment_error = None
    if event_spool:
        Path(event_spool).parent.mkdir(parents=True, exist_ok=True)
    try:
        env = codex_env(extra_env, retain_user_bus=bool(unit))
        child_env = codex_env(extra_env)
        argv = cmd + ([resume, prompt] if resume else [prompt])
        contained_argv = _codex_service_command(unit, argv, child_env) if unit else argv
        if event_spool:
            def abort_bounded_unit() -> None:
                if not unit:
                    return
                _stop_codex_unit(unit)
                if not _codex_unit_empty(unit):
                    raise CodexContainmentError(
                        f"{unit} remained populated after bounded producer failure")
            try:
                p = _run_codex_to_bounded_spools(
                    contained_argv, cwd=cwd, env=env, timeout=timeout, event_spool=Path(event_spool),
                    answer_path=out_path, on_start=on_start, on_abort=abort_bounded_unit)
            except BaseException as exc:
                if unit:
                    try:
                        abort_bounded_unit()
                    except CodexContainmentError as stop_exc:
                        raise CodexContainmentError(
                            f"bounded Codex producer failed and {unit} could not be proven empty: {stop_exc}"
                        ) from exc
                raise
        elif on_start:
            proc = subprocess.Popen(contained_argv, cwd=str(cwd), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, env=env,
                                    start_new_session=True)
            on_start(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    if unit:
                        _stop_codex_unit(unit)
                finally:
                    proc.kill(); stdout, stderr = proc.communicate()
                raise
            p = subprocess.CompletedProcess(contained_argv, proc.returncode, stdout, stderr)
        else:
            try:
                p = subprocess.run(contained_argv, cwd=str(cwd), text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL, env=env, capture_output=True)
            except subprocess.TimeoutExpired:
                if unit:
                    _stop_codex_unit(unit)
                raise
        if unit:
            try:
                if not _wait_codex_unit_empty(unit):
                    _stop_codex_unit(unit)
                if not _codex_unit_empty(unit):
                    raise CodexContainmentError(f"Codex containment unit {unit} remained populated after the turn")
            except CodexContainmentError as exc:
                containment_error = str(exc)
        if event_spool:
            p = subprocess.CompletedProcess(
                p.args, p.returncode,
                read_bounded_codex_output(Path(event_spool), CODEX_EVENT_CAP, "Codex event spool"),
                p.stderr,
            )
        if out_path.exists():
            with open(out_path, "rb") as answer:
                os.fsync(answer.fileno())
            S._fsync_directory(out_path.parent)  # noqa: SLF001 - shared durability primitive
        text = (read_bounded_codex_output(out_path, CODEX_ANSWER_CAP, "Codex answer")
                if out_path.exists() else "")
    finally:
        if temporary_output:
            try:
                os.unlink(out_path)
            except OSError:
                pass
    structured = None
    try:
        structured = S._strict_json_loads(text) if schema else json.loads(text)  # noqa: SLF001
    except ValueError:
        pass
    usage, messages, session_id, reported_session_id = {}, [], resume, None
    for line in (p.stdout or "").splitlines():
        try:
            ev = S._strict_json_loads(line)  # noqa: SLF001
        except ValueError:
            continue
        if ev.get("type") == "thread.started":
            reported_session_id = _codex_session_id(ev.get("thread_id"))
            if reported_session_id is not None:
                session_id = reported_session_id
        elif ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
            usage = dict(ev["usage"])
        elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            messages.append(str((ev["item"] or {}).get("text") or ""))
    if not text.strip() and messages:  # no -o file (or empty): the last agent message is the answer
        text = messages[-1]
    if containment_error:
        # Never expose an actionable answer while a descendant could still be running. Raw streams remain local
        # diagnostic evidence, while L1/L3 see a deterministic engine failure.
        text, structured = "", None
    return {"text": text.strip(), "structured": structured,
            "returncode": 1 if containment_error else p.returncode, "usage": usage,
            "session_id": session_id, "reported_session_id": reported_session_id,
            "unit": unit, "containment_empty": (not containment_error) if unit else None,
            "error": containment_error or (None if p.returncode == 0 else p.stderr.strip()[:500]),
            "raw_stdout": p.stdout or "", "raw_stderr": p.stderr or "",
            "raw_stdout_truncated": False,
            "raw_stderr_truncated": bool(getattr(p, "stderr_truncated", False))}


def context_percent(context_tokens: int, engine: str = "claude") -> float:
    return round(100.0 * context_tokens / config.CONTEXT_LINES[engine][2], 1)


def context_state(pct: float | None, engine: str = "claude") -> str:
    """Return ``ok``, ``warn``, or ``act`` against the engine's configured lines."""
    if pct is None:
        return "unknown"
    warn, act, _ = config.CONTEXT_LINES[engine]
    return "act" if pct >= act * 100 else "warn" if pct >= warn * 100 else "ok"
