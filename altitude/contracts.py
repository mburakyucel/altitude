"""Dormant, closed v2 boundary contracts; no runtime path imports this module yet."""
from __future__ import annotations

import math
from typing import Any, Literal, TypeAlias, TypedDict, cast


class ContractError(ValueError):
    """A JSON value is outside a closed boundary contract."""


Provider: TypeAlias = Literal["claude", "codex"]
class PathsPublicationScope(TypedDict):
    version: Literal[1]
    kind: Literal["paths"]
    paths: list[str]


class PolicyDerivedPublicationScope(TypedDict):
    version: Literal[1]
    kind: Literal["policy_derived"]

PublicationScope: TypeAlias = PathsPublicationScope | PolicyDerivedPublicationScope
class HelperRequest(TypedDict):
    role: Literal["implementer", "reviewer"]
    brief: str
    provider: Provider | None
    model: str | None
    scope: PublicationScope


class PublishOutcome(TypedDict):
    version: Literal[1]
    kind: Literal["publish"]
    commit_message: str
    pr_title: str | None
    request_merge: bool
    evidence: list[str]


class CompleteNoCodeOutcome(TypedDict):
    version: Literal[1]
    kind: Literal["complete_no_code"]
    digest: str
    evidence: list[str]


class BlockOutcome(TypedDict):
    version: Literal[1]
    kind: Literal["block"]
    reason_or_question: str
    resume_condition: str


class ContinueOutcome(TypedDict):
    version: Literal[1]
    kind: Literal["continue"]
    reason: str
    helper_requests: list[HelperRequest]


WorkerOutcome: TypeAlias = PublishOutcome | CompleteNoCodeOutcome | BlockOutcome | ContinueOutcome
class ProviderQuotaObservation(TypedDict):
    version: Literal[1]
    provider: Provider
    freshness: Literal["fresh", "stale", "unknown"]
    observed_at: str | None
    weekly_remaining_percent: float | None
    short_remaining_percent: float | None
    availability: Literal["available", "unknown", "quota_limited", "capacity_limited"]
    retry_at: str | None


CommandDomain: TypeAlias = Literal["project", "task", "worker", "condition", "recovery", "deployment"]
class AppliedCommandResult(TypedDict):
    version: Literal[1]
    kind: Literal["applied"]
    domain: CommandDomain
    command_id: str
    transition_id: str
    revision: int


class RefusedCommandResult(TypedDict):
    version: Literal[1]
    kind: Literal["refused"]
    domain: CommandDomain
    command_id: str
    code: Literal["invalid_request", "unauthorized", "not_found", "invalid_state", "stale_authority", "conflict", "held"]
    message: str
    retryable: bool


ApplicationCommandResult: TypeAlias = AppliedCommandResult | RefusedCommandResult
TaskMessage = TypedDict("TaskMessage", {"at": str, "role": Literal["user", "l2"], "text": str})
PublicationSummary = TypedDict("PublicationSummary", {"receipt_id": str, "status": Literal["published", "merged", "held", "failed"], "pr_number": int | None})
WorkerSummary = TypedDict("WorkerSummary", {"provider": Provider, "status": Literal["starting", "live", "stopping", "stopped", "unknown"]})
BlockedSummary = TypedDict("BlockedSummary", {"kind": Literal["question", "operational_hold", "preempted_by_episode", "provider_unavailable", "other"], "summary": str, "resume_at": str | None})
SessionSummary = TypedDict("SessionSummary", {"provider": Provider, "role": Literal["l3", "l2", "helper"], "status": Literal["live", "idle", "stopped", "unknown"], "task": str | None, "context_percent": float | None})
ServiceSummary = TypedDict("ServiceSummary", {"name": str, "state": Literal["healthy", "degraded", "stopped", "unknown"], "source_sha": str | None})
RecoverySummary = TypedDict("RecoverySummary", {"state": Literal["clear", "open", "waiting_operator", "held"], "episode_id": str | None, "reason": str | None})


class TaskProjection(TypedDict):
    wire_version: Literal[1]
    kind: Literal["task"]
    project: str
    slug: str
    title: str
    state: Literal["queued", "running", "settling", "blocked", "done", "rejected"]
    publication_scope: PublicationScope
    conversation: list[TaskMessage]
    publication: PublicationSummary | None
    current_worker: WorkerSummary | None
    blocked: BlockedSummary | None
    updated_at: str


class OperationalProjection(TypedDict):
    wire_version: Literal[1]
    kind: Literal["operational"]
    generated_at: str
    providers: list[ProviderQuotaObservation]
    sessions: list[SessionSummary]
    services: list[ServiceSummary]
    recovery: RecoverySummary


def _object(value: object, fields: tuple[str, ...], at: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ContractError(f"{at} must be an object")
    actual, expected = set(value), set(fields)
    if actual != expected:
        raise ContractError(f"{at} fields differ: missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}")
    return dict(value)


def _string(value: object, at: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value.strip():
        raise ContractError(f"{at} must be a non-empty string" + (" or null" if nullable else ""))
    return value


def _enum(value: object, choices: tuple[str, ...], at: str) -> str:
    if type(value) is not str or value not in choices:
        raise ContractError(f"{at} must be one of {choices}")
    return value


def _array(value: object, at: str) -> list[Any]:
    if type(value) is not list:
        raise ContractError(f"{at} must be an array")
    return value


def _integer(value: object, at: str, *, minimum: int | None = None) -> int:
    if type(value) is int:
        number = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        number = int(value)
    else:
        raise ContractError(f"{at} must be a finite integer")
    if abs(number) > 2**53 - 1:
        raise ContractError(f"{at} must be a JavaScript-safe integer")
    if minimum is not None and number < minimum:
        raise ContractError(f"{at} must be an integer >= {minimum}")
    return number


def _version(value: object, at: str) -> Literal[1]:
    if _integer(value, at) != 1:
        raise ContractError(f"{at} must be version 1")
    return 1


def _percent(value: object, at: str) -> None:
    if value is not None and (type(value) not in (int, float) or not 0 <= value <= 100):
        raise ContractError(f"{at} must be a number from 0 through 100 or null")


def _repo_path(value: object, at: str) -> str:
    path = _string(value, at)
    assert path is not None
    if (path.startswith("/") or path.endswith("/") or "\\" in path or "\0" in path
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ContractError(f"{at} must be a normalized repo-relative POSIX path")
    return path


def validate_publication_scope(value: object, at: str = "publication_scope") -> PublicationScope:
    if type(value) is not dict:
        raise ContractError(f"{at} must be an object")
    version = _version(value.get("version"), f"{at}.version")
    kind = _enum(value.get("kind"), ("paths", "policy_derived"), f"{at}.kind")
    if kind == "policy_derived":
        record = _object(value, ("version", "kind"), at); record["version"] = version
        return cast(PolicyDerivedPublicationScope, record)
    record = _object(value, ("version", "kind", "paths"), at)
    record["version"] = version
    paths = [_repo_path(item, f"{at}.paths[{index}]") for index, item in enumerate(_array(record["paths"], f"{at}.paths"))]
    if not paths or len(paths) != len(set(paths)):
        raise ContractError(f"{at}.paths must be non-empty and unique")
    return cast(PathsPublicationScope, record)


def _helper(value: object, at: str) -> HelperRequest:
    record = _object(value, ("role", "brief", "provider", "model", "scope"), at)
    _enum(record["role"], ("implementer", "reviewer"), f"{at}.role")
    _string(record["brief"], f"{at}.brief")
    if record["provider"] is not None:
        _enum(record["provider"], ("claude", "codex"), f"{at}.provider")
    _string(record["model"], f"{at}.model", nullable=True)
    record["scope"] = validate_publication_scope(record["scope"], f"{at}.scope")
    return cast(HelperRequest, record)


def _evidence(value: object, at: str) -> None:
    for index, item in enumerate(_array(value, at)):
        _string(item, f"{at}[{index}]")


def validate_worker_outcome(value: object, at: str = "worker_outcome") -> WorkerOutcome:
    if type(value) is not dict:
        raise ContractError(f"{at} must be an object")
    version = _version(value.get("version"), f"{at}.version")
    kind = _enum(value.get("kind"), ("publish", "complete_no_code", "block", "continue"), f"{at}.kind")
    fields = {
        "publish": ("version", "kind", "commit_message", "pr_title", "request_merge", "evidence"),
        "complete_no_code": ("version", "kind", "digest", "evidence"),
        "block": ("version", "kind", "reason_or_question", "resume_condition"),
        "continue": ("version", "kind", "reason", "helper_requests"),
    }
    record = _object(value, fields[kind], at)
    record["version"] = version
    if kind == "publish":
        _string(record["commit_message"], f"{at}.commit_message")
        _string(record["pr_title"], f"{at}.pr_title", nullable=True)
        if type(record["request_merge"]) is not bool:
            raise ContractError(f"{at}.request_merge must be a boolean")
        _evidence(record["evidence"], f"{at}.evidence")
    elif kind == "complete_no_code":
        _string(record["digest"], f"{at}.digest"); _evidence(record["evidence"], f"{at}.evidence")
    elif kind == "block":
        _string(record["reason_or_question"], f"{at}.reason_or_question"); _string(record["resume_condition"], f"{at}.resume_condition")
    else:
        _string(record["reason"], f"{at}.reason")
        record["helper_requests"] = [
            _helper(helper, f"{at}.helper_requests[{index}]")
            for index, helper in enumerate(_array(record["helper_requests"], f"{at}.helper_requests"))
        ]
    return cast(WorkerOutcome, record)


def validate_provider_quota_observation(value: object, at: str = "provider_quota_observation") -> ProviderQuotaObservation:
    record = _object(value, ("version", "provider", "freshness", "observed_at", "weekly_remaining_percent",
                             "short_remaining_percent", "availability", "retry_at"), at)
    record["version"] = _version(record["version"], f"{at}.version")
    _enum(record["provider"], ("claude", "codex"), f"{at}.provider")
    freshness = _enum(record["freshness"], ("fresh", "stale", "unknown"), f"{at}.freshness")
    observed = _string(record["observed_at"], f"{at}.observed_at", nullable=True)
    _percent(record["weekly_remaining_percent"], f"{at}.weekly_remaining_percent")
    _percent(record["short_remaining_percent"], f"{at}.short_remaining_percent")
    availability = _enum(record["availability"], ("available", "unknown", "quota_limited", "capacity_limited"), f"{at}.availability")
    retry = _string(record["retry_at"], f"{at}.retry_at", nullable=True)
    measurements = (record["weekly_remaining_percent"], record["short_remaining_percent"])
    if freshness == "unknown" and (observed is not None or any(item is not None for item in measurements)):
        raise ContractError(f"{at}: unknown freshness cannot carry observations")
    if (freshness == "unknown") != (availability == "unknown") or (freshness != "unknown" and observed is None):
        raise ContractError(f"{at}: unknown observations remain eligible uncertainty; known ones require observed_at")
    if (availability in ("quota_limited", "capacity_limited")) != (retry is not None):
        raise ContractError(f"{at}: only an observed quota/capacity failure carries retry_at")
    return cast(ProviderQuotaObservation, record)


def validate_application_command_result(value: object, at: str = "application_command_result") -> ApplicationCommandResult:
    if type(value) is not dict:
        raise ContractError(f"{at} must be an object")
    version = _version(value.get("version"), f"{at}.version")
    kind = _enum(value.get("kind"), ("applied", "refused"), f"{at}.kind")
    fields = (("version", "kind", "domain", "command_id", "transition_id", "revision") if kind == "applied" else
              ("version", "kind", "domain", "command_id", "code", "message", "retryable"))
    record = _object(value, fields, at)
    record["version"] = version
    _enum(record["domain"], ("project", "task", "worker", "condition", "recovery", "deployment"), f"{at}.domain")
    _string(record["command_id"], f"{at}.command_id")
    if kind == "applied":
        _string(record["transition_id"], f"{at}.transition_id")
        record["revision"] = _integer(record["revision"], f"{at}.revision", minimum=1)
    else:
        _enum(record["code"], ("invalid_request", "unauthorized", "not_found", "invalid_state", "stale_authority", "conflict", "held"), f"{at}.code")
        _string(record["message"], f"{at}.message")
        if type(record["retryable"]) is not bool:
            raise ContractError(f"{at}.retryable must be a boolean")
    return cast(ApplicationCommandResult, record)


def validate_task_projection(value: object, at: str = "task_projection") -> TaskProjection:
    record = _object(value, ("wire_version", "kind", "project", "slug", "title", "state", "publication_scope",
                             "conversation", "publication", "current_worker", "blocked", "updated_at"), at)
    record["wire_version"] = _version(record["wire_version"], f"{at}.wire_version"); _enum(record["kind"], ("task",), f"{at}.kind")
    for field in ("project", "slug", "title", "updated_at"):
        _string(record[field], f"{at}.{field}")
    _enum(record["state"], ("queued", "running", "settling", "blocked", "done", "rejected"), f"{at}.state")
    record["publication_scope"] = validate_publication_scope(record["publication_scope"], f"{at}.publication_scope")
    for index, item in enumerate(_array(record["conversation"], f"{at}.conversation")):
        message = _object(item, ("at", "role", "text"), f"{at}.conversation[{index}]")
        _string(message["at"], f"{at}.conversation[{index}].at"); _enum(message["role"], ("user", "l2"), f"{at}.conversation[{index}].role"); _string(message["text"], f"{at}.conversation[{index}].text")
    if record["publication"] is not None:
        publication = _object(record["publication"], ("receipt_id", "status", "pr_number"), f"{at}.publication")
        _string(publication["receipt_id"], f"{at}.publication.receipt_id"); _enum(publication["status"], ("published", "merged", "held", "failed"), f"{at}.publication.status")
        if publication["pr_number"] is not None:
            publication["pr_number"] = _integer(publication["pr_number"], f"{at}.publication.pr_number", minimum=1)
        record["publication"] = publication
    if record["current_worker"] is not None:
        worker = _object(record["current_worker"], ("provider", "status"), f"{at}.current_worker")
        _enum(worker["provider"], ("claude", "codex"), f"{at}.current_worker.provider"); _enum(worker["status"], ("starting", "live", "stopping", "stopped", "unknown"), f"{at}.current_worker.status")
    if record["blocked"] is not None:
        blocked = _object(record["blocked"], ("kind", "summary", "resume_at"), f"{at}.blocked")
        _enum(blocked["kind"], ("question", "operational_hold", "preempted_by_episode", "provider_unavailable", "other"), f"{at}.blocked.kind")
        _string(blocked["summary"], f"{at}.blocked.summary"); _string(blocked["resume_at"], f"{at}.blocked.resume_at", nullable=True)
    return cast(TaskProjection, record)


def validate_operational_projection(value: object, at: str = "operational_projection") -> OperationalProjection:
    record = _object(value, ("wire_version", "kind", "generated_at", "providers", "sessions", "services", "recovery"), at)
    record["wire_version"] = _version(record["wire_version"], f"{at}.wire_version"); _enum(record["kind"], ("operational",), f"{at}.kind"); _string(record["generated_at"], f"{at}.generated_at")
    providers = _array(record["providers"], f"{at}.providers")
    validated = [validate_provider_quota_observation(item, f"{at}.providers[{index}]") for index, item in enumerate(providers)]
    if len({item["provider"] for item in validated}) != len(validated):
        raise ContractError(f"{at}.providers must be unique")
    record["providers"] = validated
    for index, item in enumerate(_array(record["sessions"], f"{at}.sessions")):
        session = _object(item, ("provider", "role", "status", "task", "context_percent"), f"{at}.sessions[{index}]")
        _enum(session["provider"], ("claude", "codex"), f"{at}.sessions[{index}].provider"); _enum(session["role"], ("l3", "l2", "helper"), f"{at}.sessions[{index}].role"); _enum(session["status"], ("live", "idle", "stopped", "unknown"), f"{at}.sessions[{index}].status")
        _string(session["task"], f"{at}.sessions[{index}].task", nullable=True); _percent(session["context_percent"], f"{at}.sessions[{index}].context_percent")
    names: list[str] = []
    for index, item in enumerate(_array(record["services"], f"{at}.services")):
        service = _object(item, ("name", "state", "source_sha"), f"{at}.services[{index}]")
        names.append(_string(service["name"], f"{at}.services[{index}].name") or "")
        _enum(service["state"], ("healthy", "degraded", "stopped", "unknown"), f"{at}.services[{index}].state"); _string(service["source_sha"], f"{at}.services[{index}].source_sha", nullable=True)
    if len(names) != len(set(names)):
        raise ContractError(f"{at}.services must be unique")
    recovery = _object(record["recovery"], ("state", "episode_id", "reason"), f"{at}.recovery")
    state = _enum(recovery["state"], ("clear", "open", "waiting_operator", "held"), f"{at}.recovery.state")
    episode = _string(recovery["episode_id"], f"{at}.recovery.episode_id", nullable=True); reason = _string(recovery["reason"], f"{at}.recovery.reason", nullable=True)
    if ((state == "clear" and (episode is not None or reason is not None))
            or (state != "clear" and (episode is None or reason is None))):
        raise ContractError(f"{at}.recovery: clear has no episode; active recovery requires one")
    return cast(OperationalProjection, record)
