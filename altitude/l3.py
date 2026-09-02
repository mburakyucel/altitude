"""L3 coordination through one durable, Codex-only physical turn owner."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import config, engines, l3_actions, recovery, route, state as S
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_OP_KEYS = {"version", "request", "physical", "instance_claim", "recovery_observation",
            "delivery", "delivery_claim", "error"}
_REQ_KEYS = {"created_at", "trigger", "prompt", "body", "model", "routing", "context_sha256"}
_CLAIM_KEYS = {"instance_id", "revision", "replacement"}
_REPLACEMENT_KEYS = {"from_instance_id", "to_instance_id", "prior_revision", "at"}
_DELIVERY_CLAIM_KEYS = {"instance_id", "revision", "replacement", "claimed_at"}
_DELIVERY_REPLACEMENT_KEYS = {"from_instance_id", "to_instance_id", "prior_revision", "at", "manager"}
_MANAGER_REPLACEMENT_KEYS = {"unit", "invocation_id", "main_pid", "active_state", "sub_state",
                             "control_group", "population"}
_RESULT_KEYS = {"text", "structured", "returncode", "usage", "provider_session_id", "error"}
_PUBLIC_KEYS = {"engine_last", "session_id", "context_percent", "turns", "last_turn", "last_cost", "rotate_next"}
_PROVIDER_SESSION_CAP = engines.CODEX_PROVIDER_SESSION_ID_CAP  # UTF-8 bytes
_RESULT_ERROR_CAP = 500
class L3OwnershipError(RuntimeError): pass
def lock(project: str) -> threading.Lock:
    """A latency optimization only; the project lock plus l3.json are authoritative."""
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())
def info_path(project: str) -> Path: return config.project_dir(project) / "l3.json"
def info(project: str) -> dict: return S.read_json(info_path(project), {}) or {}
def public_info(project: str) -> dict:
    """Return display telemetry only, never the persisted request or ownership authority."""
    return {k: v for k, v in info(project).items() if k in _PUBLIC_KEYS and (v is None or type(v) in (str, int, float, bool))}
def _valid_provider_session_id(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try: return len(value.encode("utf-8")) <= _PROVIDER_SESSION_CAP
    except UnicodeEncodeError: return False
def save_info(project: str, data: dict) -> None: S.write_json(info_path(project), data)
def _runtime(project: str) -> Path: return config.project_dir(project) / "l3-codex-runtime"
def _runtime_path(project: str, family: str, name: str | None = None) -> Path:
    """Confine every trusted/model runtime path to one non-symlink disposable family."""
    project_dir, runtime = config.project_dir(project), _runtime(project)
    parent = runtime / family; path = parent / name if name else parent
    if (project_dir.is_symlink() or runtime.is_symlink() or parent.is_symlink() or path.is_symlink()
            or runtime.resolve().parent != project_dir.resolve()
            or parent.resolve().parent != runtime.resolve()
            or name is not None and path.resolve().parent != parent.resolve()):
        raise L3OwnershipError("L3 disposable runtime path escapes its expected family")
    return path
def _result_path(project: str, op: dict | None = None) -> Path:
    op = op or _owned_info(project)["current_turn"]
    return _runtime_path(project, "results", f"{op['physical']['generation']}.json")
def _spool_paths(project: str, op: dict) -> dict[str, Path]:
    generation = op["physical"]["generation"]
    return {kind: _runtime_path(project, "spool", f"{generation}.{kind}")
            for kind in ("answer", "events")}
def _chat_rows(project: str) -> list[dict]:
    """Read only post-import keyed chat; Phase 3 owns the stopped legacy importer."""
    return S.read_jsonl(config.project_dir(project) / "chat.jsonl", key_field="id")
def _context_path(project: str, generation: str) -> Path:
    return _runtime_path(project, "context", f"{generation}.json")
def _safe_context_text(value: object, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())[:limit]
    return __import__("re").sub(
        r"(?i)(?:gh[pousr]_[a-z0-9]{20,}|sk-[a-z0-9_-]{20,}|xox[baprs]-[a-z0-9-]{20,}|"
        r"(?:token|secret|password|credential|private[_ -]?key)\s*[:=]\s*\S+)", "[REDACTED]", text)
def _write_context(project: str, generation: str, observation: dict) -> str:
    keys = ("slug", "title", "state", "source", "engine", "model", "updated", "prs", "hold_merge",
            "blocked_reason", "question", "answer", "report")
    rows = []
    for task in S.list_tasks(project)[-100:]:
        row = {key: task.get(key) for key in keys if task.get(key) is not None}
        rows.append({key: (_safe_context_text(value) if isinstance(value, str)
                           else [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
                           if key == "prs" and isinstance(value, list) else value)
                     for key, value in row.items()
                     if isinstance(value, (str, int, float, bool)) or key == "prs" and isinstance(value, list)})
    chats = [{"at": _safe_context_text(row.get("at"), 40),
              "role": row.get("role"), "text": _safe_context_text(row.get("text"), 500)}
             for row in _chat_rows(project)[-30:]
             if row.get("role") in ("user", "assistant", "error")]
    value = {"version": 1, "project": _safe_context_text(project, 100), "generated_at": S.now(),
             "recovery": {key: observation[key] for key in (
                 "state", "episode_id", "permit_revision", "epoch")},
             "tasks": rows, "recent_chat": chats}
    data = S._canonical_json(value)  # noqa: SLF001
    if len(data) > 65536:
        raise L3OwnershipError("redacted L3 context projection exceeds 65536 bytes")
    path = _context_path(project, generation)
    S.atomic_write(path, data.decode() + "\n")
    return hashlib.sha256(data + b"\n").hexdigest()
def _verified_context(project: str, op: dict) -> Path:
    path = _context_path(project, op["physical"]["generation"])
    if path.is_symlink() or not path.is_file():
        raise L3OwnershipError("L3 context projection is absent or not a regular file")
    try:
        data = engines.read_bounded_codex_output(path, 65536, "L3 context projection").encode()
    except RuntimeError as exc:
        raise L3OwnershipError(str(exc)) from exc
    if hashlib.sha256(data).hexdigest() != op["request"]["context_sha256"]:
        raise L3OwnershipError("L3 context projection changed after turn installation")
    return path
def chat_log(project: str, role: str, text: str, *, message_id: str, **meta) -> None:
    with S.project_lock(project):
        record = {"id": message_id, "at": meta.pop("at", S.now()), "role": role, "text": text, **meta}
        S.append_jsonl(config.project_dir(project) / "chat.jsonl", record, key_field="id")
def chat_history(project: str, limit: int = 60) -> list[dict]: return _chat_rows(project)[-limit:]
def _append_user(project: str, op: dict) -> None:
    chat_log(project, "user", op["request"]["prompt"], message_id=op["physical"]["message_id"],
             trigger=op["request"]["trigger"], engine="codex", at=op["request"]["created_at"])
def _operation(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != _OP_KEYS:
        raise L3OwnershipError("unknown or malformed L3 current-turn ownership")
    value = json.loads(S._canonical_json(raw)); request = value.get("request")  # noqa: SLF001
    if value.get("version") != 1:
        raise L3OwnershipError("invalid L3 current-turn version")
    if (not isinstance(request, dict) or set(request) != _REQ_KEYS or any(
            not isinstance(request.get(key), str) or not request[key] for key in ("created_at", "trigger", "prompt", "body"))
            or (request.get("model") is not None and not isinstance(request["model"], str))
            or not isinstance(request.get("routing"), dict)):
        raise L3OwnershipError("invalid L3 current-turn request")
    owner = value.get("instance_claim")
    if (not isinstance(owner, dict) or set(owner) != _CLAIM_KEYS
            or not isinstance(owner.get("instance_id"), str) or not owner["instance_id"]
            or len(owner["instance_id"]) > 200 or isinstance(owner.get("revision"), bool)
            or not isinstance(owner.get("revision"), int) or owner["revision"] < 0):
        raise L3OwnershipError("invalid L3 daemon instance claim")
    replacement = owner.get("replacement")
    if owner["revision"] == 0:
        if replacement is not None:
            raise L3OwnershipError("original L3 daemon claim cannot contain a replacement receipt")
    elif (not isinstance(replacement, dict) or set(replacement) != _REPLACEMENT_KEYS
          or replacement.get("to_instance_id") != owner["instance_id"]
          or not isinstance(replacement.get("from_instance_id"), str)
          or not replacement["from_instance_id"] or replacement["from_instance_id"] == owner["instance_id"]
          or replacement.get("prior_revision") != owner["revision"] - 1
          or not isinstance(replacement.get("at"), str) or not replacement["at"]):
        raise L3OwnershipError("invalid L3 daemon replacement receipt")
    physical = engines.validate_physical_transition(value.get("physical"))
    if (physical["subject_kind"], physical["provider"], physical["generation"]) != (
            "l3", "codex", physical["transition_id"]):
        raise L3OwnershipError("L3 request and physical ownership disagree")
    observation = value.get("recovery_observation")
    if (not isinstance(observation, dict) or set(observation) != {
            "state", "episode_id", "permit_revision", "claim", "epoch"}
            or observation.get("state") not in ("none", "active")
            or isinstance(observation.get("epoch"), bool)
            or not isinstance(observation.get("epoch"), int) or observation["epoch"] < 0):
        raise L3OwnershipError("invalid L3 recovery observation")
    claim = observation.get("claim")
    if physical["message_id"] != hashlib.sha256(
            f"{physical['transition_id']}\n{S._canonical_json(request)}\n{S._canonical_json(observation).decode()}".encode()).hexdigest():  # noqa: SLF001
        raise L3OwnershipError("L3 message identity does not bind its request")
    active = observation["state"] == "active"
    if ((not active and any(observation.get(key) is not None for key in (
            "episode_id", "permit_revision", "claim")))
            or active and ((physical["recovery_episode_id"], physical["recovery_permit_revision"]) !=
                           (observation.get("episode_id"), observation.get("permit_revision"))
                           or not isinstance(claim, str) or not claim)
            or (physical["recovery_episode_id"] is not None) != active):
        raise L3OwnershipError("L3 recovery claim and physical permit disagree")
    delivery = value.get("delivery")
    delivery_claim = value.get("delivery_claim")
    if (delivery not in ("pending", "applying", "complete", "failed")
            or (value.get("error") is not None and (not isinstance(value["error"], str) or not value["error"]))
            or (delivery in ("applying", "complete") and physical["stage"] != "complete")
            or (delivery == "pending" and delivery_claim is not None)
            or (delivery in ("applying", "complete") and (not isinstance(delivery_claim, dict)
                or set(delivery_claim) != _DELIVERY_CLAIM_KEYS
                or not isinstance(delivery_claim.get("instance_id"), str) or not delivery_claim["instance_id"]
                or isinstance(delivery_claim.get("revision"), bool)
                or not isinstance(delivery_claim.get("revision"), int) or delivery_claim["revision"] < 0
                or not isinstance(delivery_claim.get("claimed_at"), str) or not delivery_claim["claimed_at"]))):
        raise L3OwnershipError("invalid L3 delivery stage or evidence")
    if isinstance(delivery_claim, dict):
        replacement = delivery_claim.get("replacement")
        manager = replacement.get("manager") if isinstance(replacement, dict) else None
        current_parts = delivery_claim["instance_id"].split(":", 2)
        if ((delivery_claim["revision"] == 0 and replacement is not None)
                or (delivery_claim["revision"] > 0 and (not isinstance(replacement, dict)
                    or set(replacement) != _DELIVERY_REPLACEMENT_KEYS
                    or not isinstance(replacement.get("from_instance_id"), str)
                    or not replacement["from_instance_id"]
                    or replacement["from_instance_id"] == delivery_claim["instance_id"]
                    or replacement.get("to_instance_id") != delivery_claim["instance_id"]
                    or replacement.get("prior_revision") != delivery_claim["revision"] - 1
                    or not isinstance(replacement.get("at"), str) or not replacement["at"]
                    or not isinstance(manager, dict) or set(manager) != _MANAGER_REPLACEMENT_KEYS
                    or manager.get("unit") != "altitude.service"
                    or manager.get("active_state") != "active" or manager.get("population") != "populated"
                    or len(current_parts) != 3 or current_parts[0] != "altd"
                    or not current_parts[1].isdigit()
                    or manager.get("main_pid") != int(current_parts[1])
                    or manager.get("invocation_id") != current_parts[2]
                    or not isinstance(manager.get("sub_state"), str) or not manager["sub_state"]
                    or not isinstance(manager.get("control_group"), str) or not manager["control_group"]))):
            raise L3OwnershipError("invalid L3 delivery replacement receipt")
    return value
def _usage(value: object) -> dict:
    keys = {"input_tokens", "cached_input_tokens", "output_tokens"}
    if (not isinstance(value, dict) or not set(value) <= keys
            or any(isinstance(item, bool) or not isinstance(item, int)
                   or item < 0 or item > 9_007_199_254_740_991 for item in value.values())):
        raise L3OwnershipError("Codex L3 usage is not one closed bounded token record")
    return dict(value)
def _marker_cap() -> int: return 2 * engines.CODEX_ANSWER_CAP + 65536
def _owned_info(project: str, *, new: bool = False) -> dict:
    value = info(project)
    if any(key in value for key in ("pid", "l3_pid", "process_pid", "turn_pid", "active_turn")):
        raise L3OwnershipError("unknown legacy L3 ownership; stopped migration evidence is required")
    if value.get("l3_ownership_version") != 1 and not (new and not value and not info_path(project).exists()):
        raise L3OwnershipError("unknown legacy L3 ownership; stopped migration evidence is required")
    value.setdefault("l3_ownership_version", 1)
    if "current_turn" in value:
        value["current_turn"] = _operation(value["current_turn"])
        if value["current_turn"]["physical"]["subject_id"] != project:
            raise L3OwnershipError("L3 physical ownership belongs to a different project")
    sessions = value.get("sessions")
    session = (sessions or {}).get("codex") if isinstance(sessions, dict) else None
    if isinstance(session, dict) and session.get("session_id") and not session.get("process_unit_id"):
        raise L3OwnershipError("Codex L3 session has no prior physical unit evidence")
    return value
def _replace(project: str, old: dict, new: dict, update=None) -> dict:
    new = _operation(new)
    with S.project_lock(project):
        inf = _owned_info(project)
        if inf.get("current_turn") != old:
            raise L3OwnershipError("L3 current turn changed during reconciliation")
        inf["current_turn"] = new
        if update: update(inf)
        save_info(project, inf)
    return new
def busy(project: str) -> bool:
    if lock(project).locked(): return True
    try:
        current = _owned_info(project, new=True).get("current_turn")
        return bool(current and current["delivery"] not in ("complete", "failed"))
    except L3OwnershipError:
        return True
def _select(project: str) -> dict: return route.pick_engine("l3", forced=config.project(project).get("l3_engine"))
def _recovery(op: dict) -> tuple[str, int, str] | None:
    observation = op["recovery_observation"]
    return ((observation["episode_id"], observation["permit_revision"], observation["claim"])
            if observation["state"] == "active" else None)
def _check_recovery(project: str, op: dict, check=None) -> None:
    if not recovery.l3_state_is_current(project, op["recovery_observation"]):
        raise L3OwnershipError("recovery episode or launch permit changed during L3 turn")
    if check is not None and not check():
        raise L3OwnershipError("recovery episode or launch permit changed during L3 turn")
def _instance(value: str | None) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise L3OwnershipError("L3 mutation requires an exact service instance identity")
    return value
def _same_instance(project: str, op: dict, instance_id: str) -> dict:
    """Re-read the aggregate at a provider boundary and prove its exact claimant."""
    current = _owned_info(project).get("current_turn")
    same_operation = bool(current and current["request"] == op["request"]
        and current["recovery_observation"] == op["recovery_observation"]
        and current["instance_claim"] == op["instance_claim"]
        and all(current["physical"][key] == op["physical"][key]
                for key in ("transition_id", "intent_digest", "process_unit_id")))
    if not same_operation or current["instance_claim"]["instance_id"] != _instance(instance_id):
        raise L3OwnershipError("L3 daemon instance claim changed before the provider boundary")
    return current
def _replace_planned_claim(project: str, op: dict, instance_id: str) -> dict:
    """Persist the only allowed cross-instance handoff before a planned launch."""
    instance_id = _instance(instance_id)
    claim = op["instance_claim"]
    if claim["instance_id"] == instance_id:
        return op
    if op["physical"]["stage"] != "planned":
        raise L3OwnershipError("only a safely planned L3 operation can change launch claimant")
    new = _operation(op)
    new["instance_claim"] = {
        "instance_id": instance_id,
        "revision": claim["revision"] + 1,
        "replacement": {"from_instance_id": claim["instance_id"], "to_instance_id": instance_id,
                        "prior_revision": claim["revision"], "at": S.now()},
    }
    return _replace(project, op, new)
def _new_operation(project: str, prompt: str, trigger: str, choice: dict, inf: dict,
                   model: str | None, recovery_identity: dict | None, instance_id: str,
                   recovery_observation: dict | None = None) -> tuple[dict, dict]:
    session = inf.setdefault("sessions", {}).setdefault("codex", {}); sid = session.get("session_id")
    fresh = (not sid or session.get("rotate_next", False)
             or (session.get("context_percent") or 0) >= config.CONTEXT_LINES["codex"][1] * 100)
    if fresh and sid:
        session.update({"session_id": None, "rotate_next": False, "rotate_reason": None,
                        "context_percent": 0, "rotated_from": sid, "rotated_at": S.now()})
        sid = None
    if recovery_observation is None:
        try:
            observation = recovery.observe_l3_state(project, recovery_identity)
        except ValueError as exc:
            raise L3OwnershipError(str(exc)) from exc
    else:
        observation = recovery_observation
    episode, permit = observation["episode_id"], observation["permit_revision"]
    turn_id = secrets.token_hex(20)
    context_path = _context_path(project, turn_id)
    context_sha = _write_context(project, turn_id, observation)
    header = (f"[altitude] project={project} trigger={trigger} context_file={context_path} "
              f"repo={config.project_path(project)}\n\n")
    body = header + prompt
    if fresh:
        body = ((config.PERSONAS / "l3_codex.md").read_text() + "\n\n"
                + f"[altitude] Engine: Codex — {choice['why']}. Fresh session; read only the context projection first.\n\n" + body)
    request = {"created_at": S.now(), "trigger": trigger, "prompt": prompt, "body": body,
               "model": model or config.project(project).get("l3_codex_model"),
               "routing": choice, "context_sha256": context_sha}
    message_id = hashlib.sha256(
        f"{turn_id}\n{S._canonical_json(request)}\n{S._canonical_json(observation).decode()}".encode()).hexdigest()  # noqa: SLF001
    physical = engines.new_physical_transition(
        transition_id=turn_id, subject_kind="l3", subject_id=project, generation=turn_id,
        provider="codex", provider_session_request=({"kind": "fresh"} if fresh else {
            "kind": "resume", "session_id": sid}), message_id=message_id,
        recovery_episode_id=episode, recovery_permit_revision=permit,
    )
    return _operation({"version": 1, "request": request,
                       "instance_claim": {"instance_id": _instance(instance_id), "revision": 0,
                                          "replacement": None},
                       "physical": physical, "recovery_observation": observation, "delivery": "pending",
                       "delivery_claim": None, "error": None}), session
def _marker(project: str, op: dict, *, required: bool = False) -> tuple[dict | None, dict | None]:
    path = _result_path(project, op)
    if path.is_symlink():
        raise L3OwnershipError("durable L3 result marker is not a bounded regular file")
    if not path.exists():
        if required:
            raise L3OwnershipError("managed L3 unit exited without a durable result marker")
        return None, None
    if not path.is_file():
        raise L3OwnershipError("durable L3 result marker is not a bounded regular file")
    try:
        value = S._strict_json_loads(engines.read_bounded_codex_output(  # noqa: SLF001
            path, _marker_cap(), "durable L3 result marker"))
    except (RuntimeError, ValueError, RecursionError) as exc:
        raise L3OwnershipError(str(exc)) from exc
    p = op["physical"]
    if (not isinstance(value, dict) or set(value) != {"version", "transition_id", "process_unit_id",
            "intent_digest", "result"} or value.get("version") != 1
            or (value.get("transition_id"), value.get("process_unit_id"), value.get("intent_digest")) !=
            (p["transition_id"], p["process_unit_id"], p["intent_digest"])):
        raise L3OwnershipError("durable L3 result marker does not match current ownership")
    result = value.get("result")
    if (not isinstance(result, dict) or set(result) != _RESULT_KEYS or not isinstance(result.get("text"), str)
            or (result.get("structured") is not None and not isinstance(result["structured"], dict))
            or isinstance(result.get("returncode"), bool) or not isinstance(result.get("returncode"), int)
            or any(result.get(key) is not None and not isinstance(result[key], str)
                   for key in ("provider_session_id", "error"))
            or result.get("provider_session_id") is not None
                and not _valid_provider_session_id(result["provider_session_id"])
            or result.get("error") is not None and (not result["error"]
                or len(result["error"]) > _RESULT_ERROR_CAP)):
        raise L3OwnershipError("durable L3 result marker has an invalid result")
    result["usage"] = _usage(result.get("usage"))
    if (p["error"] and (p["receipts"].get("bound") or {}).get("bound") is not True
            and result == {"text": "", "structured": None, "returncode": 1, "usage": {},
                           "provider_session_id": None, "error": p["error"]}):
        return value, _observed(p, None)
    try:
        sha = hashlib.sha256(S._canonical_json(value)).hexdigest()  # noqa: SLF001
    except (TypeError, ValueError, RecursionError) as exc:
        raise L3OwnershipError(f"durable L3 result marker is invalid: {exc}") from exc
    return value, {"present": True, "process_unit_id": p["process_unit_id"],
                   "intent_digest": p["intent_digest"],
                   "result_id": f"l3-codex-runtime/results/{p['generation']}.json",
                   "sha256": sha}
def _observed(p: dict, value: dict | None) -> dict | None:
    return ({"present": True, "process_unit_id": p["process_unit_id"], "intent_digest": p["intent_digest"],
             "result_id": "transition:error", "sha256": hashlib.sha256(p["error"].encode()).hexdigest()}
            if p["error"] and (p["receipts"].get("bound") or {}).get("bound") is False else value)
def _bounded_error(error: object) -> str:
    value = str(error).strip() or "unknown L3 failure"
    encoded = value.encode("utf-8", "replace")
    suffix = b" [truncated]"
    if len(encoded) <= _RESULT_ERROR_CAP:
        return value
    return (encoded[:_RESULT_ERROR_CAP - len(suffix)].decode("utf-8", "ignore").rstrip()
            + suffix.decode())
def _error_marker(project: str, op: dict, error: str) -> None:
    error = op["physical"].get("error") or _bounded_error(error)
    _write_marker(project, op, {"text": "", "structured": None, "returncode": 1, "usage": {},
                                "provider_session_id": None, "error": error})
def _write_marker(project: str, op: dict, result: dict) -> None:
    p = op["physical"]
    failure = {"text": "", "structured": None, "returncode": 1, "usage": {},
               "provider_session_id": None,
               "error": "Codex L3 result exceeded the durable marker boundary"}
    try:
        value = dict(result)
        if isinstance(value.get("structured"), dict):
            value["text"] = ""  # the closed structured envelope already carries the human message
        value["usage"] = _usage(value.get("usage"))
        sid, error = value.get("provider_session_id"), value.get("error")
        if (set(value) != _RESULT_KEYS or not isinstance(value.get("text"), str)
                or value.get("structured") is not None and not isinstance(value["structured"], dict)
                or isinstance(value.get("returncode"), bool) or not isinstance(value.get("returncode"), int)
                or sid is not None and not _valid_provider_session_id(sid)
                or error is not None and (not isinstance(error, str) or not error
                    or len(error) > _RESULT_ERROR_CAP)):
            raise L3OwnershipError("invalid bounded L3 result")
        marker = {"version": 1, "transition_id": p["transition_id"],
                  "process_unit_id": p["process_unit_id"], "intent_digest": p["intent_digest"],
                  "result": value}
        encoded = S._canonical_json(marker) + b"\n"  # noqa: SLF001
        if len(encoded) > _marker_cap():
            raise L3OwnershipError("oversized durable L3 result")
    except (L3OwnershipError, TypeError, ValueError, RecursionError):
        marker = {"version": 1, "transition_id": p["transition_id"],
                  "process_unit_id": p["process_unit_id"], "intent_digest": p["intent_digest"],
                  "result": failure}
        encoded = S._canonical_json(marker) + b"\n"  # noqa: SLF001
    S.atomic_write(_result_path(project, op), encoded.decode("utf-8"))
def _prepare_spool(project: str, op: dict) -> dict[str, Path]:
    paths, p = _spool_paths(project, op), op["physical"]
    if any(path.exists() for path in paths.values()):
        raise L3OwnershipError("managed L3 preterminal spool already exists")
    answer_id = f"l3-codex-runtime/spool/{p['generation']}.answer"
    header = {"type": "altitude.l3.spool", "version": 1, "transition_id": p["transition_id"],
              "process_unit_id": p["process_unit_id"], "intent_digest": p["intent_digest"],
              "answer_id": answer_id}
    S.atomic_write(paths["answer"], "")
    S.atomic_write(paths["events"], S._canonical_json(header).decode() + "\n")  # noqa: SLF001
    return paths
def _spooled_result(project: str, op: dict) -> dict | None:
    paths, p = _spool_paths(project, op), op["physical"]
    if not any(path.exists() for path in paths.values()): return None
    if not all(path.is_file() for path in paths.values()):
        raise L3OwnershipError("managed L3 preterminal spool is incomplete")
    try:
        event_text = engines.read_bounded_codex_output(
            paths["events"], engines.CODEX_EVENT_CAP, "managed L3 event spool")
        answer_text = engines.read_bounded_codex_output(
            paths["answer"], engines.CODEX_ANSWER_CAP, "managed L3 answer spool")
    except RuntimeError as exc:
        raise L3OwnershipError(str(exc)) from exc
    if not event_text.endswith("\n"):
        raise L3OwnershipError("managed L3 event spool has an incomplete tail")
    try:
        events = [S._strict_json_loads(line) for line in event_text.splitlines()]  # noqa: SLF001
        if not all(isinstance(event, dict) for event in events):
            raise ValueError("event spool rows must be objects")
        for event in events: S._canonical_json(event)  # noqa: SLF001
        structured = S._strict_json_loads(answer_text)  # noqa: SLF001
        S._canonical_json(structured)  # noqa: SLF001
        l3_actions._validate(structured)  # noqa: SLF001 - same closed trusted-ingestion contract as live delivery
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, l3_actions.L3ActionError) as exc:
        raise L3OwnershipError(f"managed L3 preterminal spool is invalid: {exc}") from exc
    expected = {"type": "altitude.l3.spool", "version": 1, "transition_id": p["transition_id"],
                "process_unit_id": p["process_unit_id"], "intent_digest": p["intent_digest"],
                "answer_id": f"l3-codex-runtime/spool/{p['generation']}.answer"}
    started = [(index, row) for index, row in enumerate(events[1:], 1)
               if row.get("type") == "thread.started"]
    terminals = [(index, row) for index, row in enumerate(events[1:], 1)
                 if row.get("type") in ("turn.completed", "turn.failed")]
    if (not events or events[0] != expected or len(started) != 1 or len(terminals) != 1
            or terminals[0][1].get("type") != "turn.completed"
            or not started[0][0] < terminals[0][0] == len(events) - 1
            or not _valid_provider_session_id(started[0][1].get("thread_id"))
            or not isinstance(terminals[0][1].get("usage"), dict) or not isinstance(structured, dict)):
        raise L3OwnershipError("managed L3 preterminal spool has no single successful turn")
    return {"text": "", "structured": structured, "returncode": 0,
            "usage": _usage(terminals[0][1]["usage"]),
            "provider_session_id": started[0][1]["thread_id"], "error": None}
def _recover_marker(project: str, op: dict, check=None) -> tuple[dict, dict | None, dict | None]:
    try:
        result = _spooled_result(project, op)
        if result is None: return op, None, None
        _check_recovery(project, op, check)
    except L3OwnershipError as exc:
        error = str(exc)
        if op["physical"]["error"] is None: op = _fail(project, op, error)
        _error_marker(project, op, op["physical"]["error"])
        marker, observed = _marker(project, op, required=True)
        return op, marker, observed
    _write_marker(project, op, result)
    marker, observed = _marker(project, op, required=True)
    return op, marker, observed
def _step(project: str, op: dict, expected: str, following: str, receipt: dict) -> dict:
    new = _operation(op); new["physical"] = engines.advance_physical_transition(
        new["physical"], expected, following, receipt)
    return _replace(project, op, new)
def _fail(project: str, op: dict, error: str) -> dict:
    error = _bounded_error(error)
    new = _operation(op); new["physical"] = engines.note_physical_transition_error(new["physical"], error)
    new["error"] = error
    return _replace(project, op, new)
def _prior_stopped(project: str, op: dict, session: dict) -> dict:
    decision = engines.reconcile_physical_transition(op["physical"], None)
    if decision["decision"] != "prove_prior_stopped":
        raise L3OwnershipError("new L3 unit is not proven empty")
    previous = session.get("process_unit_id"); receipt = {"previous_process_unit_id": previous, "empty": True}
    if previous:
        observed = engines.observe_managed_unit(previous)
        if observed.get("empty") is not True:
            raise L3OwnershipError("prior L3 process unit is not proven empty")
        receipt["observation"] = observed
    return _step(project, op, "planned", "prior_stopped", receipt)
def _spawn(project: str, op: dict, instance_id: str, on_start=None) -> tuple[dict, subprocess.Popen | None]:
    _same_instance(project, op, instance_id)
    _check_recovery(project, op); _runtime_path(project, "scratch", op["physical"]["generation"]).mkdir(parents=True)
    extra = {"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_ACTOR": "l3"}
    child_env = engines.codex_env(extra); child_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    try:
        with recovery.l3_launch_permission(project, op["recovery_observation"]):
            _check_recovery(project, op)
            proc = engines.spawn_managed_unit(
                op["physical"], [sys.executable, "-m", "altitude.l3", "--managed-codex", project,
                                 op["physical"]["transition_id"], instance_id], cwd=_runtime(project),
                launcher_env=engines.codex_env(extra, retain_user_bus=True), child_env=child_env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, engines.ManagedUnitError, recovery.LaunchHeld) as exc:
        error = f"managed L3 launch failed before crossing spawn: {exc}"
        op = _fail(project, op, error); _error_marker(project, op, error)
        return op, None
    if on_start:
        on_start(proc.pid)
    return _step(project, op, "prior_stopped", "spawned", {
        "process_unit_id": op["physical"]["process_unit_id"], "launched": True}), proc
def _prepare_launch(project: str, op: dict, instance_id: str, check=None) -> tuple[dict, bool]:
    """CAS planned→prior_stopped; only the winner receives launch authority."""
    if op["physical"]["stage"] != "planned":
        return op, False
    op = _replace_planned_claim(project, op, instance_id)
    session = (_owned_info(project).get("sessions") or {}).get("codex") or {}
    try:
        _check_recovery(project, op, check)
    except L3OwnershipError as exc:
        op = _fail(project, _prior_stopped(project, op, session), str(exc))
        _error_marker(project, op, str(exc))
        return op, False
    return _prior_stopped(project, op, session), True
def _start_planned(project: str, op: dict, instance_id: str, on_start=None, check=None) -> dict:
    op, launch_winner = _prepare_launch(project, op, instance_id, check)
    if not launch_winner:
        return op
    op, proc = _spawn(project, op, instance_id, on_start)
    if proc:
        try:
            proc.wait(timeout=config.L3_CODEX_TURN_TIMEOUT + 60)
        except subprocess.TimeoutExpired:
            engines.stop_managed_unit(op["physical"]["process_unit_id"]); proc.wait(timeout=10)
        else:
            marker, _observed_result = _marker(project, _owned_info(project)["current_turn"])
            if marker and marker["result"].get("error"):
                engines.stop_managed_unit(op["physical"]["process_unit_id"])
    return _owned_info(project)["current_turn"]
def _reconcile_physical(project: str, op: dict, check=None) -> dict:
    """Record physical truth only; never launch from crash reconciliation."""
    while op["physical"]["stage"] not in ("complete", "failed"):
        marker, observed = _marker(project, op); p, stage = op["physical"], op["physical"]["stage"]
        if stage == "prior_stopped" and marker is None and any(
                path.exists() for path in _spool_paths(project, op).values()):
            try:
                empty = engines.observe_managed_unit(p["process_unit_id"]).get("empty") is True
            except engines.ManagedUnitError as exc:
                raise L3OwnershipError(f"ownership_uncertain: {exc}") from exc
            if empty:
                op, marker, observed = _recover_marker(project, op, check)
                p = op["physical"]
        observation = _observed(p, observed)
        try:
            decision = engines.reconcile_physical_transition(p, observation)
        except engines.PhysicalTransitionError as exc:
            raise L3OwnershipError(str(exc)) from exc
        action, unit = decision["decision"], decision["process_unit"]
        if action == "prove_prior_stopped":
            raise L3OwnershipError("planned L3 operation was not continued under its turn lock")
        if stage == "prior_stopped":
            launched = action == "record_spawned"
            if action not in ("record_spawned", "record_spawn_failure"):
                raise L3OwnershipError(f"unexpected prior-stop reconciliation {action}")
            op = _step(project, op, stage, "spawned", {"process_unit_id": p["process_unit_id"],
                "launched": launched, **({} if launched else {"reason": p["error"]})}); continue
        if stage == "spawned":
            if marker is None and not unit["empty"]:
                return op
            if marker is None:
                marker, observed = _marker(project, op)
            if marker is None:
                op, marker, observed = _recover_marker(project, op, check); p = op["physical"]
            if marker is None:
                error = p["error"] or "managed L3 unit exited without a durable result marker"
                op = _fail(project, op, error) if not p["error"] else op
                _error_marker(project, op, error); marker, observed = _marker(project, op, required=True)
                p = op["physical"]
            result = marker["result"]
            sid = result.get("provider_session_id")
            requested = p["provider_session_request"]
            error = result.get("error") or (f"Codex exited {result['returncode']}" if result["returncode"] else None)
            if requested["kind"] == "resume" and sid != requested["session_id"]:
                error = f"Codex L3 resume returned a different thread than {requested['session_id']}"
            if not sid:
                error = error or "Codex L3 turn did not report a thread identity"
            if error and p["error"] is None:
                op = _fail(project, op, error); p = op["physical"]
            if not sid:
                _error_marker(project, op, error)
            receipt = ({"bound": True, "physical_worker_id": p["process_unit_id"],
                        "provider_session_id": sid} if sid else {"bound": False, "reason": error})
            op = _step(project, op, stage, "bound", receipt); continue
        if stage == "bound":
            if marker is None and unit["empty"]:
                marker, observed = _marker(project, op)
                if marker is None:
                    op, marker, observed = _recover_marker(project, op, check); p = op["physical"]
            if marker is None:
                if not unit["empty"]:
                    return op
                error = p["error"] or "managed L3 unit exited without a durable result marker"
                op = _fail(project, op, error) if not p["error"] else op
                _error_marker(project, op, error); marker, observed = _marker(project, op, required=True)
                p = op["physical"]
            observed = _observed(p, observed)
            op = _step(project, op, stage, "result_observed", {
                "result_id": observed["result_id"], "sha256": observed["sha256"]}); continue
        if stage == "result_observed":
            if action == "wait_for_empty":
                return op
            op = _step(project, op, stage, "empty", unit); continue
        if stage == "empty":
            terminal = "failed" if p["error"] else "complete"
            op = _step(project, op, stage, terminal, {"status": terminal}); continue
        raise L3OwnershipError(f"unexpected L3 physical reconciliation {stage}/{action}")
    marker, observed = _marker(project, op, required=True)
    p = op["physical"]
    engines.reconcile_physical_transition(p, _observed(p, observed))
    return op
def _output(project: str, op: dict, *, actions=None) -> dict:
    marker, observed = _marker(project, op, required=True)
    if op["physical"]["receipts"]["result_observed"] != {
            "result_id": observed["result_id"], "sha256": observed["sha256"]}:
        raise L3OwnershipError("settled L3 result changed")
    result, usage = marker["result"], marker["result"].get("usage") or {}; tokens = int(usage.get("input_tokens", 0) or 0)
    text = str((result.get("structured") or {}).get("message") or result.get("text") or "")
    return {"text": text, "session_id": result.get("provider_session_id") or "",
            "usage": usage, "context_tokens": tokens,
            "context_percent": engines.context_percent(tokens, "codex") if tokens else 0.0,
            "cost": 0.0, "turns": 1, "structured": None, "error": op.get("error"), "tools": [],
            "skipped": False, "completed": op["delivery"] == "complete", "actions": actions or [],
            "_turn_started_at": op["request"]["created_at"], "engine": "codex",
            "routing": op["request"]["routing"]}
def _project_provider_session(inf: dict, op: dict, result: dict) -> None:
    """Preserve exact provider continuity independently of local delivery success."""
    session = inf.setdefault("sessions", {}).setdefault("codex", {})
    generation, unit = op["physical"]["generation"], op["physical"]["process_unit_id"]
    session["process_unit_id"] = unit
    if session.get("physical_generation") == generation:
        return
    sid = result.get("provider_session_id")
    if not sid:
        return
    usage = _usage(result.get("usage")); tokens = usage.get("input_tokens", 0)
    pct = engines.context_percent(tokens, "codex") if tokens else 0.0
    fresh = op["physical"]["provider_session_request"]["kind"] == "fresh"
    session.update({"session_id": sid, "turns": (0 if fresh else int(session.get("turns") or 0)) + 1,
                    "context_percent": pct, "last_turn": S.now(), "last_cost": 0.0, "usage": usage,
                    "started": S.now() if fresh else session.get("started"), "rotate_reason": None,
                    "context_state": engines.context_state(pct, "codex"), "physical_generation": generation,
                    "rotate_next": pct >= config.CONTEXT_LINES["codex"][1] * 100})
    inf.update({"engine_last": "codex", "session_id": sid, "context_percent": pct,
                "turns": session["turns"], "last_turn": session["last_turn"], "last_cost": 0.0,
                "routing": op["request"]["routing"], "rotate_next": session["rotate_next"],
                "rotate_reason": session["rotate_reason"]})
def _deliver(project: str, op: dict, instance_id: str, check=None) -> dict:
    instance_id = _instance(instance_id)
    try:
        _check_recovery(project, op, check)
    except L3OwnershipError as exc:
        try:
            op = _reconcile_physical(project, op)
        except L3OwnershipError:
            return _empty(str(exc), routing=op["request"]["routing"])
        if op["physical"]["stage"] in ("complete", "failed") and op["delivery"] not in ("complete", "failed"):
            new = _operation(op); new.update({"delivery": "failed", "error": str(exc)})
            _replace(project, op, new)
        return _empty(str(exc), routing=op["request"]["routing"])
    op = _reconcile_physical(project, op, check)
    if op["physical"]["stage"] not in ("complete", "failed"):
        return _empty("L3 turn is still physically owned", routing=op["request"]["routing"]) | {
            "engine": "codex", "_turn_started_at": op["request"]["created_at"]}
    if op["delivery"] in ("complete", "failed"):
        return _output(project, op)
    marker, observed = _marker(project, op, required=True)
    try:
        _check_recovery(project, op, check)
    except L3OwnershipError as exc:
        new = _operation(op); new.update({"delivery": "failed", "error": str(exc)})
        op = _replace(project, op, new)
        return _empty(str(exc), routing=op["request"]["routing"])
    result = marker["result"]
    if op["physical"]["stage"] == "failed":
        new = _operation(op); new.update({"delivery": "failed",
            "error": op.get("error") or result.get("error") or "Codex L3 physical turn failed"})
        return _output(project, _replace(project, op, new))
    action_id = hashlib.sha256(f"{op['physical']['intent_digest']}\n{observed['sha256']}".encode()).hexdigest()
    if op["delivery"] == "pending":
        new = _operation(op); new.update({"delivery": "applying",
            "delivery_claim": {"instance_id": instance_id, "revision": 0,
                               "replacement": None, "claimed_at": S.now()}})
        op = _replace(project, op, new)
    elif op["delivery"] == "applying":
        owner = op["delivery_claim"]
        # Same-instance replay is serialized by the process-local guard; exact journal/chat evidence decides it.
        if owner["instance_id"] != instance_id:
            try:
                manager = engines.prove_altitude_service_replacement(owner["instance_id"], instance_id)
            except engines.ManagedUnitError as exc:
                return _empty(f"reconciliation_required: {exc}", routing=op["request"]["routing"])
            new = _operation(op); new["delivery_claim"] = {
                "instance_id": instance_id, "revision": owner["revision"] + 1, "claimed_at": S.now(),
                "replacement": {"from_instance_id": owner["instance_id"], "to_instance_id": instance_id,
                                "prior_revision": owner["revision"], "at": S.now(), "manager": manager}}
            op = _replace(project, op, new)
    try:
        _check_recovery(project, op, check)
        applied = l3_actions.apply(project, result.get("structured"), action_id=action_id,
            github_issue_source=(op["request"]["prompt"] if op["request"]["trigger"] == "chat" else None),
            effect_precheck=lambda: _check_recovery(project, op, check),
            recovery_observation=op["recovery_observation"])
        text = str((result.get("structured") or {}).get("message") or result.get("text") or "")
        pending = next((item for item in applied if item.get("pending_review")), None)
        if pending:
            text = (text.rstrip() + "\n\n" if text.strip() else "") + (
                f"I saved GitHub issue draft `{pending['id']}` privately. Publication remains disabled until "
                "Phase 1C.4 adds exact-repository replay and a trusted approval command.")
        _check_recovery(project, op, check)
        chat_log(project, "assistant", text, message_id=hashlib.sha256(
            (op["physical"]["message_id"] + ":assistant").encode()).hexdigest(),
            trigger=op["request"]["trigger"], engine="codex")
    except l3_actions.L3ActionError as exc:
        if str(exc).startswith("reconciliation_required:"):
            new = _operation(op); new.update({"delivery": "applying", "error": str(exc)})
            if new != op:
                op = _replace(project, op, new)
            return _empty(str(exc), routing=op["request"]["routing"])
        new = _operation(op); new.update({"delivery": "failed", "error": str(exc)})
        return _output(project, _replace(project, op, new))
    except L3OwnershipError as exc:
        new = _operation(op); new.update({"delivery": "failed", "error": str(exc)})
        _replace(project, op, new)
        return _empty(str(exc), routing=op["request"]["routing"])
    new = _operation(op); new.update({"delivery": "complete", "error": None})
    _check_recovery(project, op, check)
    def update(inf):
        _project_provider_session(inf, op, result)
    new = _replace(project, op, new, update); S.regen_state_md(project)
    out = _output(project, new, actions=applied); out["text"] = text
    return out
def _empty(error=None, *, skipped=False, routing=None) -> dict:
    return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
            "turns": 0, "structured": None, "error": error, "tools": [], "skipped": skipped, "completed": False,
            "_turn_started_at": None, **({"routing": routing} if routing else {})}
def _retire(project: str, current: dict) -> None:
    marker, observed_result = _marker(project, current, required=True)
    expected = current["physical"]["receipts"].get("result_observed")
    actual = ({"result_id": observed_result["result_id"], "sha256": observed_result["sha256"]}
              if observed_result else None)
    if expected != actual:
        raise L3OwnershipError("settled L3 result changed before retirement")
    with S.project_lock(project):
        inf = _owned_info(project)
        if inf.get("current_turn") != current:
            raise L3OwnershipError("L3 turn changed before retirement")
        if current["physical"]["stage"] not in ("complete", "failed"):
            raise L3OwnershipError("cannot retire L3 ownership before its unit is proven empty")
        _project_provider_session(inf, current, marker["result"])
        inf.pop("current_turn"); save_info(project, inf)
    _result_path(project, current).unlink(missing_ok=True)
    for path in _spool_paths(project, current).values(): path.unlink(missing_ok=True)
    _context_path(project, current["physical"]["generation"]).unlink(missing_ok=True)
    shutil.rmtree(_runtime_path(project, "scratch", current["physical"]["generation"]), ignore_errors=True)
def turn(project: str, prompt: str, *, trigger: str = "chat", on_text=None, on_start=None,
         model: str | None = None, precheck=None, recovery_identity: dict | None = None,
         instance_id: str | None = None) -> dict:
    """Accept or reconcile one serialized Codex turn; only exact planned recovery may launch."""
    del on_text
    choice = _select(project)
    if choice.get("engine") != "codex":
        return _empty(f"engine hold: only Codex has adopted L3 ownership ({choice.get('why') or 'no route'})",
                      routing=choice)
    instance_id = _instance(instance_id)
    guard = lock(project)
    if not guard.acquire(blocking=False):
        return _empty("L3 is busy with another turn") | {"engine": "codex"}
    try:
        try:
            existing = _owned_info(project, new=True).get("current_turn")
            if existing:
                if (existing["physical"]["stage"] not in ("complete", "failed")
                        or existing["delivery"] not in ("complete", "failed")):
                    return _empty("L3 has an unrelated durable turn pending") | {"engine": "codex"}
                _retire(project, existing)
            if precheck is not None and not precheck():
                return _empty(skipped=True)
            S.regen_state_md(project)
            try:
                observation = recovery.observe_l3_state(project, recovery_identity)
            except ValueError as exc:
                raise L3OwnershipError(str(exc)) from exc
            with S.project_lock(project):
                inf = _owned_info(project, new=True)
                if inf.get("current_turn"):
                    raise L3OwnershipError("another process created an L3 turn")
                op, _session = _new_operation(
                    project, prompt, trigger, choice, inf, model, recovery_identity, instance_id, observation)
                inf["current_turn"] = op; save_info(project, inf)
            _append_user(project, op)
            return _deliver(project, _start_planned(project, op, instance_id, on_start, precheck),
                            instance_id, precheck)
        except (engines.ManagedUnitError, engines.PhysicalTransitionError, L3OwnershipError) as exc:
            return _empty(str(exc)) | {"engine": "codex"}
    finally:
        guard.release()
def reconcile(project: str, *, instance_id: str | None = None, nonblocking: bool = False) -> dict | None:
    instance_id = _instance(instance_id)
    guard = lock(project)
    if not guard.acquire(blocking=False if nonblocking else True):
        return None
    try:
        try:
            op = _owned_info(project, new=True).get("current_turn")
            if not op: return None
            _append_user(project, op)
            return _deliver(project, _start_planned(project, op, instance_id), instance_id)
        except (engines.ManagedUnitError, engines.PhysicalTransitionError, L3OwnershipError) as exc:
            return _empty(str(exc)) | {"engine": "codex"}
    finally:
        guard.release()
def reset(project: str, reason: str = "manual", *, instance_id: str | None = None) -> None:
    _instance(instance_id)
    with S.project_lock(project):
        inf = _owned_info(project, new=True)
        if inf.get("current_turn") and inf["current_turn"]["delivery"] not in ("complete", "failed"):
            raise L3OwnershipError("cannot rotate an unsettled L3 turn")
        inf.setdefault("sessions", {}).setdefault("codex", {}).update({"rotate_next": True, "rotate_reason": reason}); inf.update({"rotate_next": True, "rotate_reason": reason}); save_info(project, inf)
def _managed_codex(project: str, transition_id: str, instance_id: str) -> int:
    """Trusted wrapper spools output above the model-writable scratch directory."""
    op = _owned_info(project).get("current_turn")
    if not op or op["physical"]["transition_id"] != transition_id:
        raise L3OwnershipError("managed L3 child does not match the persisted current turn")
    op = _same_instance(project, op, instance_id)
    if op["physical"]["stage"] not in ("prior_stopped", "spawned"):
        raise L3OwnershipError("managed L3 child started from an invalid physical stage")
    request = op["physical"]["provider_session_request"]
    scratch = _runtime_path(project, "scratch", op["physical"]["generation"])
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        spool = _prepare_spool(project, op)
        op = _same_instance(project, op, instance_id)
        _check_recovery(project, op)
        context = _verified_context(project, op)
        raw = engines.codex_exec(op["request"]["body"], cwd=scratch, sandbox="workspace-write",
            schema=config.SCHEMAS / "l3_action.json", contain=False, timeout=config.L3_CODEX_TURN_TIMEOUT,
            model=op["request"]["model"], effort=config.CODEX_EFFORT.get("l3"),
            resume=request.get("session_id") if request["kind"] == "resume" else None,
            readable_roots=[context, config.project_path(project)],
            extra_env={"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project,
                       }, fault_context={"project": project},
            answer_path=spool["answer"], event_spool=spool["events"])
        code = raw.get("returncode") if isinstance(raw, dict) else None
        if isinstance(code, bool) or not isinstance(code, int) or code != 0:
            detail = raw.get("error") if isinstance(raw, dict) else None
            raise L3OwnershipError(detail if isinstance(detail, str) and detail
                                   else f"Codex process exited with status {code!r}")
        op = _same_instance(project, op, instance_id)
        _check_recovery(project, op)
        result = _spooled_result(project, op)
        if result is None:
            raise L3OwnershipError("Codex process exited without durable preterminal spool evidence")
    except Exception as exc:  # noqa: BLE001 - adapter failure becomes inert, bound result evidence
        result = {"text": "", "structured": None, "returncode": 1, "usage": {},
                  "provider_session_id": None, "error": str(exc)[:500]}
    op = _same_instance(project, op, instance_id)
    _write_marker(project, op, result)
    return 0
if __name__ == "__main__":
    raise SystemExit(_managed_codex(sys.argv[2], sys.argv[3], sys.argv[4]) if len(sys.argv) == 5
                     and sys.argv[1] == "--managed-codex" else "l3 is an internal managed worker module")
