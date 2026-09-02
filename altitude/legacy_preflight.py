"""TEMPORARY Phase 0B inventory for simplification migration preflight.

This module deliberately does not import the state layer: importing it and calling either public
function must not create ``ALTITUDE_HOME`` or normalize any state.  The inventory is evidence for
later migrations, not another state authority.  It remains shipped through the first successful
real production ActivationReceipt.  Only the separately reviewed PR 10B may delete this module and
its ``alt preflight`` entry point after the frozen-state and cutover receipts exist.
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, manifest as runtime_identity
from .manifest import LEGACY_STATE_VERSION, SUPPORTED_STATE_VERSIONS


PREFLIGHT_SCHEMA = "altitude.offline-preflight/v2"
TASK_STATES = frozenset(("queued", "running", "reported", "done", "rejected", "blocked"))
ACTIVE_TASK_STATES = frozenset(("queued", "running", "reported", "blocked"))
TERMINAL_TASK_STATES = frozenset(("done", "rejected"))
ENGINES = frozenset(("claude", "codex"))


def _safe_component(value: object) -> bool:
    """Match the current direct-child path contract without inventing a new naming policy."""
    return (isinstance(value, str) and bool(value) and value not in (".", "..")
            and "/" not in value and "\\" not in value and "\0" not in value)


@dataclass(frozen=True)
class ShapeIssue:
    scope: str
    path: str
    reason: str


@dataclass(frozen=True)
class ArtifactFamily:
    name: str
    pattern: str
    scope: str
    consumers: tuple[str, ...]
    writers: tuple[str, ...]


# This is the proposal's baseline-family grouping verbatim: bundles and mirrors that share one
# lifecycle stay one family. Consumers and writers are declared review evidence, never mislabeled
# as mechanically observed counts.
_ARTIFACT_FAMILIES = (
    ArtifactFamily("projects", "projects.json", "global", ("config", "server", "dispatch", "l3", "monitor"), ("config", "bin/alt", "server")),
    ArtifactFamily("project-publication-resume-locks", "<project>/.lock; .publication-settlement.lock; <task>/.resume.lock", "synchronization", ("state", "dispatch", "land"), ("state", "dispatch")),
    ArtifactFamily("recovery-hold", "monitor/recovery-hold.json + locks", "global", ("recovery", "server", "dispatch", "l1", "l3_actions"), ("recovery",)),
    ArtifactFamily("recovery-clearances", "monitor/recovery-clearances.jsonl", "global", ("recovery",), ("recovery",)),
    ArtifactFamily("restart-pending", "monitor/restart-pending.json", "global", ("dispatch", "server"), ("dispatch", "server")),
    ArtifactFamily("global-incidents", "incidents.jsonl", "global", ("incidents", "server"), ("incidents",)),
    ArtifactFamily("monitor-faults", "monitor/faults.json + faults.lock", "global", ("incidents",), ("incidents",)),
    ArtifactFamily("quota-statusline-usage-observations", "monitor/quota-codex.json; monitor/usage-limit.json; statusline-*.json", "global", ("monitor", "route"), ("quota_codex", "hooks/statusline-monitor.sh")),
    ArtifactFamily("hook-fault-drain", "monitor/hook-faults.log", "global", ("server",), ("hooks",)),
    ArtifactFamily("edit-counts", "monitor/counts-*.json + locks", "global", ("status", "verify"), ("hooks/edit_count.py",)),
    ArtifactFamily("live-worker-cache", "monitor/live-*.json", "global", ("monitor", "dispatch"), ("dispatch",)),
    ArtifactFamily("project-state", "<project>/STATE.md", "project", ("l3", "server"), ("state",)),
    ArtifactFamily("project-events", "<project>/events.log", "project", ("state", "server"), ("state",)),
    ArtifactFamily("project-inbox", "<project>/inbox.jsonl", "project", ("tasks", "digest", "server"), ("tasks",)),
    ArtifactFamily("project-hold", "<project>/hold.json", "project", ("status", "server"), ("server", "recovery")),
    ArtifactFamily("project-incidents-markdown", "<project>/incidents.jsonl + incidents/I-NNN.md", "project", ("incidents", "server"), ("incidents",)),
    ArtifactFamily("claude-settings", "~/.claude/settings.json + ALTITUDE_HOME/claude-settings.json", "external/global", ("engines", "server"), ("server", "engines")),
    ArtifactFamily("l3-sessions", "<project>/l3.json + provider session", "project/external", ("l3", "monitor"), ("l3", "provider")),
    ArtifactFamily("l3-chat", "<project>/chat.jsonl", "project", ("l3", "server"), ("l3",)),
    ArtifactFamily("l3-actions", "<project>/l3-actions/<id>.json", "project", ("l3_actions",), ("l3_actions",)),
    ArtifactFamily("issue-drafts", "<project>/github-issue-drafts/<id>.json", "project", ("l3_actions",), ("l3_actions",)),
    ArtifactFamily("task-status", "<project>/{tasks,archive}/<slug>/status.json", "task", ("state", "tasks", "dispatch", "server", "status", "verify"), ("tasks", "dispatch", "server", "actions", "l3_actions")),
    ArtifactFamily("request", "<task>/request.md", "task", ("dispatch", "server"), ("tasks",)),
    ArtifactFamily("brief", "<task>/brief.md", "task", ("dispatch", "server"), ("dispatch", "tasks")),
    ArtifactFamily("conversation", "<task>/conversation.jsonl", "task", ("tasks", "transcript", "server"), ("tasks",)),
    ArtifactFamily("task-events", "<task>/events.log", "task", ("state", "transcript", "server"), ("state",)),
    ArtifactFamily("issue-snapshot", "<task>/github-issue.json", "task", ("github_intake", "dispatch"), ("github_intake",)),
    ArtifactFamily("report", "<task>/report.json", "task", ("verify", "server", "status", "transcript"), ("actions", "Claude L2")),
    ArtifactFamily("task-digest", "<task>/digest.md", "task", ("server", "transcript"), ("tasks",)),
    ArtifactFamily("progress", "<task>/progress.md", "task", ("server", "L2"), ("L2",)),
    ArtifactFamily("task-claude-settings", "<task>/settings.json", "task", ("dispatch", "engines"), ("dispatch",)),
    ArtifactFamily("helper-bundle", "<task>/l1/* + helper-request-*.md + helper worktree", "task/repository", ("l1", "dispatch", "status", "monitor", "L2"), ("l1", "actions", "provider")),
    ArtifactFamily("codex-worker-bundle", "<task>/l2-engine/*", "task", ("engines", "dispatch", "transcript", "actions"), ("engines", "Codex")),
    ArtifactFamily("transcript-bundle", "<task>/transcripts/<attempt>/*", "task", ("transcript",), ("transcript",)),
    ArtifactFamily("worktree-ref-provenance", "task worktree/branch/ref/PR/CI/provenance hooks", "repository/external", ("dispatch", "land", "status", "verify", "git_policy"), ("dispatch", "l1", "land", "Git", "GitHub Actions")),
    ArtifactFamily("report-md-compatibility", "<worktree>/report.md", "repository", ("guard", "L2"), ("L2",)),
    ArtifactFamily("global-digest-audio", "DIGEST.md + digest.wav", "global", ("digest", "server"), ("digest",)),
    ArtifactFamily("runtime-hooks-directory", "ALTITUDE_HOME/hooks", "global", ("engines", "server"), ("server",)),
    ArtifactFamily("service-log", "altd.log", "global", ("operator", "incidents"), ("server.log",)),
    ArtifactFamily("tls-material", "ALTITUDE_TLS_DIR", "global/external", ("server",), ("tls-init", "operator")),
    ArtifactFamily("web-bundle", "source checkout web/dist/", "repository/deploy", ("server", "manifest"), ("make web", "restart")),
    ArtifactFamily("service-manager", "systemd user manager state/cgroup", "external", ("manifest", "restart", "operator"), ("systemd", "restart")),
    ArtifactFamily("task-namespaces", "<project>/{tasks,archive}/", "project", ("state", "tasks", "server"), ("tasks",)),
    ArtifactFamily("l3-codex-scratch", "<project>/l3-codex-runtime/", "project/scratch", ("l3", "Codex"), ("l3", "Codex")),
    ArtifactFamily("claude-job-state", "~/.claude/jobs/<agent>/state.json", "provider/external", ("engines", "dispatch", "monitor"), ("Claude",)),
    ArtifactFamily("claude-session-records", "~/.claude/projects/*/<session>.jsonl", "provider/external", ("engines", "transcript"), ("Claude",)),
)


_MUTATING_CALLS = frozenset({
    "append_event", "append_task_message", "atomic_write", "chmod", "copy", "copy2", "copyfile",
    "copytree", "fdopen", "link_to", "makedirs", "mkdir", "mkdtemp", "mkstemp", "move",
    "NamedTemporaryFile", "open", "remove", "rename", "replace", "rmtree", "save_projects",
    "save_task", "symlink_to", "TemporaryDirectory", "touch", "unlink", "write", "write_bytes",
    "write_json", "write_text", "writelines",
})
_TIMER_CALLS = frozenset({"spawn", "sleep", "Thread", "Timer"})

_ROOT_FILES = ("projects.json", "incidents.jsonl", "DIGEST.md", "digest.wav", "altd.log",
               "claude-settings.json")
_MONITOR_FILES = (
    "recovery-hold.json", "recovery-hold.lock", "recovery-launch.lock",
    "recovery-clearances.jsonl", "restart-pending.json", "faults.json", "faults.lock",
    "quota-codex.json", "usage-limit.json", "hook-faults.log", "statusline-*.json",
    "live-*.json", "counts-*.json", "counts-*.json.lock",
)
_PROJECT_FILES = (
    ".lock", ".publication-settlement.lock", "STATE.md", "l3.json", "chat.jsonl",
    "events.log", "inbox.jsonl", "hold.json", "incidents.jsonl", "incidents/.alloc.lock",
    "incidents/I-*.md", "l3-actions/*.json", "github-issue-drafts/*.json",
)
_PROJECT_DIRS = ("tasks", "archive", "incidents", "l3-actions", "github-issue-drafts",
                 "l3-codex-runtime")
_TASK_FILES = (
    ".resume.lock", "status.json", "request.md", "brief.md", "github-issue.json",
    "settings.json", "events.log", "conversation.jsonl", "report.json", "digest.md",
    "progress.md", "helper-request-*.md", "l1/.lock", "l1/*.json", "l1/*.prompt.md",
    "l1/*.log", "l1/*.stdout", "l1/*.stderr", "l1/*.patch", "l2-engine/*.json",
    "l2-engine/*.stdout.jsonl", "l2-engine/*.stderr.log", "l2-engine/*.answer.md",
    "transcripts/*/manifest.json", "transcripts/*/events.jsonl",
    "transcripts/*/native-*.jsonl", "transcripts/*/report.json", "transcripts/*/digest.md",
)
_TASK_DIRS = ("l1", "l1/*.codex-runtime", "l2-engine", "transcripts", "transcripts/*")

# The PR 0B candidate roster applies the exact §07 counting categories.  New paths in one of the
# executable source namespaces are unclassified until this closed roster is deliberately amended.
PERMANENT_BACKEND_ROSTER = (
    "altitude/__init__.py", "altitude/actions.py", "altitude/config.py", "altitude/digest.py",
    "altitude/dispatch.py", "altitude/engines.py", "altitude/git_policy.py",
    "altitude/github_intake.py", "altitude/incidents.py", "altitude/l1.py", "altitude/l3.py",
    "altitude/l3_actions.py", "altitude/land.py", "altitude/manifest.py", "altitude/monitor.py",
    "altitude/quota_codex.py", "altitude/recovery.py", "altitude/route.py", "altitude/server.py",
    "altitude/state.py", "altitude/status.py", "altitude/tasks.py", "altitude/transcript.py",
    "altitude/verify.py", "bin/alt", "hooks/edit_count.py", "hooks/guard.py", "hooks/pre-commit",
    "hooks/pre-merge-commit", "hooks/pre-push", "hooks/reference-transaction",
    "hooks/statusline-monitor.sh", "scripts/restart_altitude.py",
)
WEB_SOURCE_ROSTER = (
    "web/src/data/Toast.tsx", "web/src/data/api.ts", "web/src/data/useOptimisticMutation.ts",
    "web/src/main.tsx", "web/src/routes.tsx", "web/src/routes/Chat.tsx",
    "web/src/routes/Inbox.tsx", "web/src/routes/LiveSession.tsx", "web/src/routes/Monitor.tsx",
    "web/src/routes/Project.tsx", "web/src/routes/Projects.tsx", "web/src/routes/Task.tsx",
    "web/src/shell/AppShell.tsx", "web/src/shell/theme.tsx", "web/src/styles.css",
)
WEB_BUILD_ROSTER = ("web/design/tokens.css", "web/index.html", "web/package.json",
                    "web/tsconfig.json", "web/vite.config.ts")
PERSONA_SCHEMA_TEMPLATE_ROSTER = (
    "personas/l1.md", "personas/l2.md", "personas/l2_codex.md", "personas/l3.md",
    "personas/l3_codex.md", "personas/reviewer.md", "schemas/l2_action.json",
    "schemas/l3_action.json", "schemas/report.json", "schemas/review.json",
    "templates/brief.md", "templates/incident.md", "templates/pr.md",
)
SUPPORT_ROSTER = ("systemd/altitude.service", ".github/workflows/remote-tests.yml", "Makefile")
TEMPORARY_ROSTER = ("altitude/legacy_preflight.py",)
BASELINE_COUNTS = {
    "permanent_backend": {"files": 32, "lines": 10695},
    "web_source": {"files": 15, "lines": 2683},
    "web_build": {"files": 5, "lines": 195},
    "permanent_total": {"files": 52, "lines": 13573},
    "persona_schema_template": {"files": 13, "lines": 496},
    "support": {"files": 3, "lines": 134},
    "python_tests": {"files": 54, "lines": 10258},
    "web_tests": {"files": 10, "lines": 1097},
}
PROJECT_FIELDS = frozenset(("name", "path", "approval", "wip", "self_deploy", "test_cmd",
                            "land_wait", "l2_engine", "l2_model", "l2_codex_model",
                            "l3_engine", "l3_model", "l3_codex_model"))
TASK_FIELDS = frozenset((
    "slug", "title", "state", "created", "updated", "attempt", "dispatch_id",
    "previous_dispatch_id", "session_id", "agent_id", "agent", "l2_token", "worktree",
    "branch", "prs", "spend", "blocked_reason", "source", "verified", "model", "engine",
    "l2_engine", "engine_model", "routing", "paths", "hold_merge", "dispatching", "dispatched",
    "capacity_retries", "resume_after", "resume_prefix", "resume_exact_prompt", "resume_answer",
    "pending_action", "completion_requested", "l3_handled", "cleaned", "blocked_question",
    "github_issue_context_delivered",
))
L3_FIELDS = frozenset(("sessions", "engine_last", "session_id", "context_percent", "turns",
                       "last_turn", "last_cost", "routing", "rotate_next", "rotate_reason",
                       "rotated_from", "rotated_at"))
L3_SESSION_FIELDS = frozenset(("session_id", "context_percent", "turns", "last_turn", "last_cost",
                               "started", "context_state", "rotate_next", "rotate_reason", "usage",
                               "rotated_from", "rotated_at"))
L1_FIELDS = frozenset(("n", "name", "role", "engine", "why", "model", "worktree", "branch",
                       "brief", "started", "pid", "done", "result", "paths", "parent_sha"))
CODEX_WORKER_FIELDS = frozenset(("id", "name", "pid", "pid_start", "unit", "started_at",
                                 "session_id", "cwd", "resume", "stopped"))
ACTION_JOURNAL_FIELDS = frozenset(("id", "at", "status", "action", "finished", "error", "result"))
ISSUE_DRAFT_FIELDS = frozenset(("id", "created", "status", "title", "body", "labels",
                                "published", "url"))
L3_ACTION_TYPES = frozenset(("new_task", "task_done", "task_block", "task_resume", "task_fyi",
                             "task_hold_merge", "github_issue", "github_issue_approve", "incident_new",
                             "incident_amend", "recovery_hold", "recovery_clear"))
IDENTITY_FIELDS = frozenset(("dispatch_id", "session_id", "agent_id"))
PENDING_ACTION_FIELDS = frozenset(("action", "identity", "claimed", "message_posted"))
COMPLETION_FIELDS = frozenset(("at", "digest", *IDENTITY_FIELDS))
ROUTING_FIELDS = frozenset(("engine", "why", "quota", "requested_engine"))
ROUTING_OBSERVATION_FIELDS = frozenset((
    "weekly_used", "short_used", "weekly_resets", "short_resets", "observed_at", "raw",
))
CLAUDE_QUOTA_FIELDS = frozenset((
    "known", "why", "five_hour", "seven_day", "five_hour_resets", "seven_day_resets", "at",
))
CODEX_QUOTA_FIELDS = frozenset((
    "known", "why", "primary_used", "primary_resets", "primary_window_minutes",
    "secondary_used", "secondary_resets", "secondary_window_minutes", "plan_type", "read_at",
))
STATUSLINE_RATE_LIMIT_FIELDS = frozenset(("five_hour", "seven_day"))
STATUSLINE_WINDOW_FIELDS = frozenset(("used_percentage", "resets_at"))
STATUSLINE_FIELDS = frozenset((
    "_at", "hook_event_name", "session_id", "transcript_path", "cwd", "model", "workspace",
    "version", "output_style", "cost", "context_window", "exceeds_200k_tokens", "rate_limits",
))
STATUSLINE_NESTED_FIELDS = {
    "model": frozenset(("id", "display_name")),
    "workspace": frozenset(("current_dir", "project_dir", "added_dirs")),
    "output_style": frozenset(("name",)),
    "cost": frozenset(("total_cost_usd", "total_duration_ms", "total_api_duration_ms",
                       "total_lines_added", "total_lines_removed")),
    "context_window": frozenset(("total_input_tokens", "total_output_tokens",
                                 "context_window_size", "used_percentage",
                                 "remaining_percentage")),
}
LIVE_AGENT_FIELDS = frozenset(("status", "state", "engine", "pid", "usage"))
LIVE_USAGE_FIELDS = frozenset((
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    "cached_input_tokens", "total_tokens",
))
L2_ACTION_FIELDS = frozenset((
    "message", "action", "commit_message", "pr_title", "merge", "digest", "blocked_reason",
    "continue_reason", "helpers", "outcome",
))
HELPER_REQUEST_FIELDS = frozenset(("role", "brief", "engine", "model", "paths"))
OUTCOME_FIELDS = frozenset(("deploy", "review", "decisions", "fyi", "follow_ups", "deviations", "spend"))
REVIEW_FIELDS = frozenset(("summary", "severity", "disposition", "reason"))
DECISION_FIELDS = frozenset(("question", "answer", "options"))
DEVIATION_FIELDS = frozenset(("description", "reason"))
SPEND_FIELDS = frozenset(("turns", "subagent_launches", "retries", "reverts"))
REPAIR_FIELDS = frozenset(("project", "slug", "claimed", "by"))
ATTENTION_FIELDS = frozenset((
    "episode", "project", "requested", "updated", "revision", "handled_revision", "attempts",
    "next_attempt", "faults", "claim", "claimed", "last_error", "last_attempt", "handled",
))
ATTENTION_FAULT_FIELDS = frozenset(("kind", "incident"))
CLEARANCE_REPAIR_FIELDS = frozenset(("project", "slug"))
CLEARANCE_ATTENTION_FIELDS = frozenset(("project", "revision", "handled_revision"))
CLEARANCE_FAULT_FIELDS = frozenset(("kind", "count", "incident"))
CLAUDE_WORKER_FIELDS = frozenset((
    "id", "name", "state", "status", "sessionId", "pid", "startedAt", "worktreePath",
    "worktreeBranch", "detail", "model", "permissionMode", "createdAt", "updatedAt", "cwd",
))


def _json(path: Path, issues: list[ShapeIssue], scope: str, *, required: bool = False):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if required:
            issues.append(ShapeIssue(scope, str(path), "required file is missing"))
        return None
    except OSError as exc:
        issues.append(ShapeIssue(scope, str(path), f"cannot read: {exc}"))
        return None
    try:
        return json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        issues.append(ShapeIssue(scope, str(path), f"invalid JSON: {exc}"))
        return None


def _jsonl(path: Path, issues: list[ShapeIssue], scope: str) -> list[dict]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        issues.append(ShapeIssue(scope, str(path), f"cannot read: {exc}"))
        return []
    if data and not data.endswith(b"\n"):
        issues.append(ShapeIssue(scope, str(path), "JSONL final record has no newline"))
    rows = []
    for number, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            issues.append(ShapeIssue(scope, f"{path}:{number}", f"invalid JSONL record: {exc}"))
            continue
        if not isinstance(value, dict):
            issues.append(ShapeIssue(scope, f"{path}:{number}", "JSONL record is not an object"))
            continue
        if "schema_version" in value:
            issues.append(ShapeIssue(
                scope, f"{path}:{number}",
                f"unsupported JSONL record schema_version {value.get('schema_version')!r}"))
        rows.append(value)
    return rows


def _version(value: dict, family: str, path: Path, issues: list[ShapeIssue], scope: str) -> None:
    actual = value.get("schema_version", LEGACY_STATE_VERSION)
    if actual not in SUPPORTED_STATE_VERSIONS[family]:
        issues.append(ShapeIssue(scope, str(path), f"unsupported {family} schema_version {actual!r}"))


def _unversioned(value: object, family: str, path: Path,
                 issues: list[ShapeIssue], scope: str) -> None:
    """Fail closed if a currently unversioned envelope announces a future decoder contract."""
    if isinstance(value, dict) and "schema_version" in value:
        issues.append(ShapeIssue(
            scope, str(path), f"unsupported {family} schema_version {value.get('schema_version')!r}"))


def _closed_fields(value: object, allowed: frozenset[str], family: str, path: Path,
                   issues: list[ShapeIssue], scope: str) -> None:
    if not isinstance(value, dict):
        return
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        issues.append(ShapeIssue(scope, str(path),
                                 f"unknown {family} fields: {', '.join(unknown[:10])}"))


def _object(value: object, label: str, path: Path,
            issues: list[ShapeIssue], scope: str) -> dict | None:
    if not isinstance(value, dict):
        issues.append(ShapeIssue(scope, str(path), f"{label} is not an object"))
        return None
    return value


def _identity_shape(value: object, label: str, path: Path,
                    issues: list[ShapeIssue], scope: str) -> None:
    value = _object(value, label, path, issues, scope)
    if value is None:
        return
    _closed_fields(value, IDENTITY_FIELDS, label, path, issues, scope)
    if set(value) != IDENTITY_FIELDS or any(not isinstance(value.get(field), str) or not value[field]
                                             for field in IDENTITY_FIELDS):
        issues.append(ShapeIssue(scope, str(path), f"{label} lacks one exact worker identity"))


def _routing_shape(value: object, path: Path, issues: list[ShapeIssue], scope: str) -> None:
    if value is None:
        return
    value = _object(value, "routing decision", path, issues, scope)
    if value is None:
        return
    _closed_fields(value, ROUTING_FIELDS, "routing decision", path, issues, scope)
    if value.get("engine") not in (None, *ENGINES) or not isinstance(value.get("why"), str):
        issues.append(ShapeIssue(scope, str(path), "routing decision lacks a closed engine/reason"))
    quota = _object(value.get("quota"), "routing quota", path, issues, scope)
    if quota is None:
        return
    _closed_fields(quota, ENGINES, "routing quota", path, issues, scope)
    if set(quota) != ENGINES:
        issues.append(ShapeIssue(scope, str(path), "routing quota lacks both provider observations"))
    for provider in ENGINES:
        observation = _object(quota.get(provider), f"{provider} routing observation", path, issues, scope)
        if observation is None:
            continue
        _closed_fields(observation, ROUTING_OBSERVATION_FIELDS,
                       f"{provider} routing observation", path, issues, scope)
        raw = _object(observation.get("raw"), f"{provider} raw quota", path, issues, scope)
        if raw is not None:
            _closed_fields(raw, CLAUDE_QUOTA_FIELDS if provider == "claude" else CODEX_QUOTA_FIELDS,
                           f"{provider} raw quota", path, issues, scope)


def _action_shape(value: object, path: Path, issues: list[ShapeIssue], scope: str) -> None:
    value = _object(value, "pending action payload", path, issues, scope)
    if value is None:
        return
    _closed_fields(value, L2_ACTION_FIELDS, "pending action payload", path, issues, scope)
    if set(value) != L2_ACTION_FIELDS or value.get("action") not in (
            "publish", "complete_no_code", "block", "request_helpers", "continue"):
        issues.append(ShapeIssue(scope, str(path), "pending action payload is incomplete or unknown"))
    helpers = value.get("helpers")
    if not isinstance(helpers, list):
        issues.append(ShapeIssue(scope, str(path), "pending action helpers is not an array"))
    else:
        for helper in helpers:
            row = _object(helper, "pending helper request", path, issues, scope)
            if row is not None:
                _closed_fields(row, HELPER_REQUEST_FIELDS, "pending helper request", path, issues, scope)
                if set(row) != HELPER_REQUEST_FIELDS:
                    issues.append(ShapeIssue(scope, str(path), "pending helper request is incomplete"))
    outcome = _object(value.get("outcome"), "pending action outcome", path, issues, scope)
    if outcome is None:
        return
    _closed_fields(outcome, OUTCOME_FIELDS, "pending action outcome", path, issues, scope)
    for field, allowed in (("review", REVIEW_FIELDS), ("decisions", DECISION_FIELDS),
                           ("deviations", DEVIATION_FIELDS)):
        rows = outcome.get(field)
        if not isinstance(rows, list):
            issues.append(ShapeIssue(scope, str(path), f"pending outcome {field} is not an array"))
            continue
        for row in rows:
            _closed_fields(row, allowed, f"pending outcome {field} row", path, issues, scope)
    _closed_fields(outcome.get("spend"), SPEND_FIELDS, "pending outcome spend", path, issues, scope)


def _task_nested_shapes(value: dict, path: Path, issues: list[ShapeIssue], scope: str) -> None:
    _routing_shape(value.get("routing"), path, issues, scope)
    completion = value.get("completion_requested")
    if completion is not None:
        row = _object(completion, "completion request", path, issues, scope)
        if row is not None:
            _closed_fields(row, COMPLETION_FIELDS, "completion request", path, issues, scope)
            if set(row) != COMPLETION_FIELDS:
                issues.append(ShapeIssue(scope, str(path), "completion request is incomplete"))
    pending = value.get("pending_action")
    if pending is not None:
        row = _object(pending, "pending action", path, issues, scope)
        if row is not None:
            _closed_fields(row, PENDING_ACTION_FIELDS, "pending action", path, issues, scope)
            if set(row) != PENDING_ACTION_FIELDS:
                issues.append(ShapeIssue(scope, str(path), "pending action is incomplete"))
            _identity_shape(row.get("identity"), "pending action identity", path, issues, scope)
            _action_shape(row.get("action"), path, issues, scope)


def _recovery_nested_shapes(value: dict, path: Path,
                            issues: list[ShapeIssue], scope: str) -> None:
    repair = value.get("repair")
    if repair is not None:
        row = _object(repair, "recovery repair", path, issues, scope)
        if row is not None:
            _closed_fields(row, REPAIR_FIELDS, "recovery repair", path, issues, scope)
            if set(row) != REPAIR_FIELDS:
                issues.append(ShapeIssue(scope, str(path), "recovery repair is incomplete"))
    attention = value.get("l3_attention")
    if attention is not None:
        row = _object(attention, "recovery L3 attention", path, issues, scope)
        if row is not None:
            _closed_fields(row, ATTENTION_FIELDS, "recovery L3 attention", path, issues, scope)
            required = {"episode", "project", "requested", "updated", "revision",
                        "handled_revision", "attempts", "next_attempt", "faults"}
            if not required.issubset(row):
                issues.append(ShapeIssue(scope, str(path), "recovery L3 attention is incomplete"))
            faults = row.get("faults")
            if not isinstance(faults, list):
                issues.append(ShapeIssue(scope, str(path), "recovery L3 attention faults is not an array"))
            else:
                for fault in faults:
                    item = _object(fault, "recovery L3 attention fault", path, issues, scope)
                    if item is not None:
                        _closed_fields(item, ATTENTION_FAULT_FIELDS,
                                       "recovery L3 attention fault", path, issues, scope)
                        if set(item) != ATTENTION_FAULT_FIELDS:
                            issues.append(ShapeIssue(
                                scope, str(path), "recovery L3 attention fault is incomplete"))


def _clearance_nested_shapes(value: dict, path: Path,
                             issues: list[ShapeIssue], scope: str) -> None:
    for field, allowed in (("repair", CLEARANCE_REPAIR_FIELDS),
                           ("l3_attention", CLEARANCE_ATTENTION_FIELDS)):
        if value.get(field) is None:
            continue
        row = _object(value[field], f"clearance {field}", path, issues, scope)
        if row is not None:
            _closed_fields(row, allowed, f"clearance {field}", path, issues, scope)
    faults = value.get("faults")
    if not isinstance(faults, list):
        issues.append(ShapeIssue(scope, str(path), "clearance faults is not an array"))
        return
    for fault in faults:
        row = _object(fault, "clearance fault", path, issues, scope)
        if row is not None:
            _closed_fields(row, CLEARANCE_FAULT_FIELDS, "clearance fault", path, issues, scope)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(Path(path).match(pattern) for pattern in patterns)


def _enumerate_namespace(root: Path, *, file_patterns: tuple[str, ...],
                         dir_patterns: tuple[str, ...] = (), opaque_dirs: tuple[str, ...] = (),
                         issues: list[ShapeIssue], scope: str, label: str) -> None:
    """Report every unrecognized entry; this inventory never follows state symlinks."""
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        issues.append(ShapeIssue(scope, str(root), f"{label} namespace is not a real directory"))
        return
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as exc:
        issues.append(ShapeIssue(scope, str(root), f"cannot enumerate {label} namespace: {exc}"))
        return
    for entry in entries:
        rel = entry.relative_to(root).as_posix()
        parents = [parent.as_posix() for parent in Path(rel).parents if parent.as_posix() != "."]
        is_opaque = _matches(rel, opaque_dirs)
        below_opaque = any(_matches(parent, opaque_dirs) for parent in parents)
        if is_opaque or below_opaque:
            if is_opaque and entry.is_symlink():
                issues.append(ShapeIssue(scope, str(entry), f"{label} entry is a symlink"))
            continue
        if entry.is_symlink():
            issues.append(ShapeIssue(scope, str(entry), f"{label} entry is a symlink"))
            continue
        if entry.is_dir():
            known = _matches(rel, dir_patterns)
        elif entry.is_file():
            known = _matches(rel, file_patterns)
        else:
            known = False
        if not known:
            kind = "directory" if entry.is_dir() else "file" if entry.is_file() else "entry"
            issues.append(ShapeIssue(scope, str(entry), f"unrecognized {label} {kind}"))


def _task_identity(path: Path, location: str, issues: list[ShapeIssue],
                   provider_home: Path) -> dict | None:
    status_path = path / "status.json"
    value = _json(status_path, issues, location, required=True)
    if not isinstance(value, dict):
        if value is not None:
            issues.append(ShapeIssue(location, str(status_path), "task status is not an object"))
        return None
    _version(value, "task_status", status_path, issues, location)
    _closed_fields(value, TASK_FIELDS, "task status", status_path, issues, location)
    _task_nested_shapes(value, status_path, issues, location)
    slug, state = value.get("slug"), value.get("state")
    if slug != path.name:
        issues.append(ShapeIssue(location, str(status_path), f"task slug {slug!r} does not match directory {path.name!r}"))
    if state not in TASK_STATES:
        issues.append(ShapeIssue(location, str(status_path), f"unknown task state {state!r}"))
    if location == "active" and state not in ACTIVE_TASK_STATES:
        issues.append(ShapeIssue(location, str(status_path), f"terminal task {state!r} is in the active namespace"))
    if location == "archive" and state not in TERMINAL_TASK_STATES:
        issues.append(ShapeIssue(location, str(status_path), f"nonterminal task {state!r} is in the archive namespace"))
    # Records created before the provider field existed were Claude-owned.  Preserve that explicit
    # physical identity in the audit instead of reporting an invented "unknown" provider.
    has_generation = any(value.get(key) for key in ("dispatch_id", "session_id", "agent_id"))
    engine = value.get("l2_engine") or ("claude" if has_generation else value.get("engine"))
    if engine is not None and engine not in ENGINES:
        issues.append(ShapeIssue(location, str(status_path), f"unknown provider {engine!r}"))
    if state in ("running", "reported", "blocked"):
        for field in ("dispatch_id", "session_id", "agent_id"):
            if not isinstance(value.get(field), str) or not value.get(field):
                issues.append(ShapeIssue(location, str(status_path), f"{state} task lacks physical {field}"))
        if engine not in ENGINES:
            issues.append(ShapeIssue(location, str(status_path), f"{state} task lacks a known physical provider"))
        if not isinstance(value.get("attempt"), int) or isinstance(value.get("attempt"), bool):
            issues.append(ShapeIssue(location, str(status_path), f"{state} task lacks an integral attempt"))
        if not isinstance(value.get("worktree"), str) or not isinstance(value.get("branch"), str):
            issues.append(ShapeIssue(location, str(status_path), f"{state} task lacks worktree/branch ownership"))
    artifacts = _task_artifacts(path, state, issues, location)
    return {
        "slug": slug,
        "state": state,
        "provider": engine,
        "dispatch_id": value.get("dispatch_id"),
        "session_id": value.get("session_id"),
        "worker_id": value.get("agent_id"),
        "physical_worker": _physical_worker(path, value, engine, issues, location, provider_home),
        "helpers": _helpers(path, issues, location),
        "transcripts": artifacts["transcripts"],
    }


def _task_artifacts(task_dir: Path, state: object, issues: list[ShapeIssue], scope: str) -> dict:
    for name in ("github-issue.json", "settings.json"):
        path = task_dir / name
        value = _json(path, issues, scope)
        if value is not None and not isinstance(value, dict):
            issues.append(ShapeIssue(scope, str(path), f"{name} is not an object"))
        else:
            _unversioned(value, name, path, issues, scope)
    report_path = task_dir / "report.json"
    report = _json(report_path, issues, scope, required=state == "reported")
    if report is not None and not isinstance(report, dict):
        issues.append(ShapeIssue(scope, str(report_path), "task report is not an object"))
    else:
        _unversioned(report, "task report", report_path, issues, scope)
    _jsonl(task_dir / "events.log", issues, scope)
    messages = _jsonl(task_dir / "conversation.jsonl", issues, scope)
    for number, message in enumerate(messages, 1):
        if message.get("role") not in ("burak", "l2") or not isinstance(message.get("text"), str):
            issues.append(ShapeIssue(
                scope, f"{task_dir / 'conversation.jsonl'}:{number}", "task message has an unknown shape"))
    transcripts = []
    root = task_dir / "transcripts"
    if root.is_dir():
        for attempt in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name):
            manifest_path = attempt / "manifest.json"
            value = _json(manifest_path, issues, scope, required=True)
            if not isinstance(value, dict):
                if value is not None:
                    issues.append(ShapeIssue(scope, str(manifest_path), "transcript manifest is not an object"))
                continue
            _version(value, "transcript", manifest_path, issues, scope)
            transcripts.append({
                "attempt": attempt.name, "schema_version": value.get("schema_version"),
                "engine": value.get("engine"), "worker_id": value.get("worker_id"),
            })
    return {"transcripts": transcripts}


def _claude_worker(path: Path, worker_id: str, task: dict | None,
                   issues: list[ShapeIssue], scope: str) -> dict | None:
    value = _json(path, issues, scope, required=True)
    if not isinstance(value, dict):
        if value is not None:
            issues.append(ShapeIssue(scope, str(path), "Claude worker record is not an object"))
        return None
    _unversioned(value, "Claude worker", path, issues, scope)
    _closed_fields(value, CLAUDE_WORKER_FIELDS, "Claude worker", path, issues, scope)
    if value.get("id") != worker_id:
        issues.append(ShapeIssue(scope, str(path), "Claude worker identity does not match job owner"))
    if task is not None:
        if value.get("sessionId") != task.get("session_id"):
            issues.append(ShapeIssue(scope, str(path), "Claude worker session differs from task owner"))
        if value.get("worktreePath") not in (None, task.get("worktree")):
            issues.append(ShapeIssue(scope, str(path), "Claude worker worktree differs from task owner"))
        if (task.get("state") == "running" and
                (value.get("state") != "working" or not isinstance(value.get("pid"), int)
                 or isinstance(value.get("pid"), bool) or value.get("pid", 0) <= 0)):
            issues.append(ShapeIssue(scope, str(path), "running Claude worker lacks live state/PID evidence"))
    return value


def _physical_worker(task_dir: Path, task: dict, provider: str | None,
                     issues: list[ShapeIssue], scope: str, provider_home: Path) -> dict | None:
    worker_id = task.get("agent_id")
    if not worker_id:
        return None
    record, active = None, None
    if provider == "codex":
        path = task_dir / "l2-engine" / f"{worker_id}.json"
        value = _json(path, issues, scope, required=True)
        if isinstance(value, dict):
            _version(value, "codex_worker", path, issues, scope)
            _closed_fields(value, CODEX_WORKER_FIELDS, "Codex worker", path, issues, scope)
            if value.get("id") != worker_id or not isinstance(value.get("unit"), str):
                issues.append(ShapeIssue(scope, str(path), "Codex worker identity/unit does not match task owner"))
            if (value.get("session_id") not in (None, task.get("session_id"))
                    or value.get("cwd") not in (None, task.get("worktree"))):
                issues.append(ShapeIssue(scope, str(path), "Codex worker session/worktree differs from task owner"))
            if task.get("state") == "running" and (not isinstance(value.get("pid"), int)
                                                    or not value.get("pid_start")):
                issues.append(ShapeIssue(scope, str(path), "running Codex worker lacks PID/start identity"))
            record = str(path)
            active = value.get("stopped") is not True
        elif value is not None:
            issues.append(ShapeIssue(scope, str(path), "Codex worker record is not an object"))
    elif provider == "claude":
        path = provider_home / ".claude" / "jobs" / str(worker_id) / "state.json"
        value = _claude_worker(path, str(worker_id), task, issues, scope)
        if value is not None:
            record = str(path)
            active = value.get("state") not in ("done", "failed", "stopped")
    return {"provider": provider, "worker_id": worker_id, "record": record, "active": active}


def _helpers(task_dir: Path, issues: list[ShapeIssue], scope: str) -> list[dict]:
    directory = task_dir / "l1"
    if not directory.is_dir():
        return []
    helpers = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name):
        value = _json(path, issues, scope)
        if not isinstance(value, dict):
            if value is not None:
                issues.append(ShapeIssue(scope, str(path), "L1 run is not an object"))
            continue
        _version(value, "l1_run", path, issues, scope)
        _closed_fields(value, L1_FIELDS, "L1 run", path, issues, scope)
        engine = value.get("engine")
        if engine not in ENGINES:
            issues.append(ShapeIssue(scope, str(path), f"unknown helper provider {engine!r}"))
        if value.get("name") not in (None, path.stem):
            issues.append(ShapeIssue(scope, str(path), "helper name does not match its record filename"))
        if value.get("role") not in ("implementer", "reviewer"):
            issues.append(ShapeIssue(scope, str(path), f"unknown helper role {value.get('role')!r}"))
        if not isinstance(value.get("name"), str):
            issues.append(ShapeIssue(scope, str(path), "helper lacks a physical name"))
        if value.get("done") is None:
            if not isinstance(value.get("pid"), int) or value.get("pid", 0) <= 0:
                issues.append(ShapeIssue(scope, str(path), "active helper lacks a positive PID"))
            if not isinstance(value.get("worktree"), str) or not value.get("worktree"):
                issues.append(ShapeIssue(scope, str(path), "active helper lacks a worktree identity"))
        elif not isinstance(value.get("done"), str) or not isinstance(value.get("result"), dict):
            issues.append(ShapeIssue(scope, str(path), "completed helper lacks done/result evidence"))
        helpers.append({key: value.get(key) for key in
                        ("name", "role", "engine", "model", "pid", "done", "worktree")})
    return helpers


def _incident_identities(home: Path, project_names: list[str], issues: list[ShapeIssue]) -> list[dict]:
    rows = _jsonl(home / "incidents.jsonl", issues, "active")
    for project in project_names:
        rows.extend(_jsonl(home / project / "incidents.jsonl", issues, "active"))
    identities = {}
    for row in rows:
        project, incident = row.get("project"), row.get("id")
        if not isinstance(project, str) or not isinstance(incident, str) or not re.fullmatch(r"I-\d+", incident):
            issues.append(ShapeIssue("active", "incident-index", f"invalid incident identity {project!r}/{incident!r}"))
            continue
        identity = {
            "project": project, "id": incident, "task": row.get("task"), "title": row.get("title")
        }
        key = (project, incident)
        if key in identities and identities[key] != identity:
            issues.append(ShapeIssue("active", "incident-index",
                                     f"conflicting incident identity {project!r}/{incident!r}"))
        identities[key] = identity
    return [identities[key] for key in sorted(identities)]


def _project_artifacts(project_dir: Path, issues: list[ShapeIssue], scope: str = "active") -> None:
    for name in ("chat.jsonl", "events.log", "inbox.jsonl"):
        _jsonl(project_dir / name, issues, scope)
    for directory_name in ("l3-actions", "github-issue-drafts"):
        directory = project_dir / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
            value = _json(path, issues, scope)
            if value is not None and not isinstance(value, dict):
                issues.append(ShapeIssue(scope, str(path), f"{directory_name} record is not an object"))
            else:
                _unversioned(value, f"{directory_name} record", path, issues, scope)
                allowed = ACTION_JOURNAL_FIELDS if directory_name == "l3-actions" else ISSUE_DRAFT_FIELDS
                _closed_fields(value, allowed, f"{directory_name} record", path, issues, scope)
                if isinstance(value, dict) and directory_name == "l3-actions":
                    action = value.get("action")
                    if (value.get("id") != path.stem or value.get("status") not in
                            ("applying", "complete", "failed") or not isinstance(action, dict)
                            or action.get("type") not in L3_ACTION_TYPES):
                        issues.append(ShapeIssue(scope, str(path), "L3 action journal has unknown ownership/stage"))
                    if value.get("status") == "applying":
                        issues.append(ShapeIssue(scope, str(path),
                                                 "active v1 action effect must settle before cutover"))
                if isinstance(value, dict) and directory_name == "github-issue-drafts":
                    if (value.get("id") != path.stem or value.get("status") not in
                            ("pending_review", "published") or not isinstance(value.get("title"), str)
                            or not isinstance(value.get("body"), str) or not isinstance(value.get("labels"), list)):
                        issues.append(ShapeIssue(scope, str(path), "issue draft has unknown ownership/stage"))
                    if value.get("status") == "pending_review":
                        issues.append(ShapeIssue(scope, str(path),
                                                 "active v1 issue effect must settle before cutover"))


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _qualified_call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return f"{target.value.id}.{target.attr}"
    return _call_name(node)


def _open_mode(node: ast.Call, position: int) -> object:
    mode = node.args[position] if len(node.args) > position else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return mode.value if isinstance(mode, ast.Constant) else mode


def _is_mutating_open(node: ast.Call) -> bool:
    name = _qualified_call_name(node)
    if name == "os.open":
        # O_RDONLY is literal zero. Any symbolic/nonzero flag may create, append, or truncate.
        flags = node.args[1] if len(node.args) > 1 else None
        return not (isinstance(flags, ast.Constant) and flags.value == 0)
    if _call_name(node) not in ("open", "fdopen"):
        return False
    modes = [_open_mode(node, 1)]
    if isinstance(node.func, ast.Attribute) and _call_name(node) == "open":
        modes.append(_open_mode(node, 0))  # Path.open(mode); tarfile.open(file, mode) uses the first row
    explicit = [mode for mode in modes if isinstance(mode, str)]
    if explicit:
        return any(any(flag in mode for flag in "wax+") for mode in explicit)
    # A dynamic method-open mode is not provably read-only; classify it instead of silently omitting it.
    return isinstance(node.func, ast.Attribute)


def _is_mutating_call(node: ast.Call) -> bool:
    name = _call_name(node)
    if name in ("open", "fdopen"):
        return _is_mutating_open(node)
    return name in _MUTATING_CALLS


def _source_inventory(repo: Path) -> dict:
    categories = {
        "permanent_backend": PERMANENT_BACKEND_ROSTER,
        "web_source": WEB_SOURCE_ROSTER,
        "web_build": WEB_BUILD_ROSTER,
        "temporary_real_state_importers": TEMPORARY_ROSTER,
        "persona_schema_template": PERSONA_SCHEMA_TEMPLATE_ROSTER,
        "support": SUPPORT_ROSTER,
        "python_tests": tuple(sorted(path.relative_to(repo).as_posix()
                                     for path in (repo / "tests").glob("*.py"))),
        "web_tests": tuple(sorted(path.relative_to(repo).as_posix()
                                  for path in (repo / "web" / "src").rglob("*")
                                  if path.is_file() and (".test." in path.name or
                                  path.relative_to(repo).as_posix() in
                                  ("web/src/test/render.tsx", "web/src/vitest.setup.ts")))),
    }

    def rows(roster: tuple[str, ...]) -> list[dict]:
        result = []
        for relative in roster:
            path = repo / relative
            if path.is_file():
                data = path.read_bytes()
                result.append({"path": relative, "lines": data.count(b"\n"), "bytes": len(data),
                               "sha256": runtime_identity._sha256(data)})
        return result

    category_rows = {name: rows(roster) for name, roster in categories.items()}
    permanent_names = ("permanent_backend", "web_source", "web_build")
    permanent = [row for name in permanent_names for row in category_rows[name]]
    classified = {relative for roster in categories.values() for relative in roster}
    candidates = set()
    candidates.update(path.relative_to(repo).as_posix() for path in (repo / "altitude").rglob("*.py"))
    for root in (repo / "bin", repo / "scripts", repo / "hooks"):
        if root.is_dir():
            candidates.update(path.relative_to(repo).as_posix() for path in root.rglob("*")
                              if path.is_file() and "__pycache__" not in path.parts
                              and path.suffix not in (".pyc", ".pyo"))
    web_src = repo / "web" / "src"
    if web_src.is_dir():
        candidates.update(path.relative_to(repo).as_posix() for path in web_src.rglob("*")
                          if path.is_file() and path.suffix in (".ts", ".tsx", ".css"))
    for relative in (*WEB_BUILD_ROSTER, *SUPPORT_ROSTER, *PERSONA_SCHEMA_TEMPLATE_ROSTER):
        if (repo / relative).is_file():
            candidates.add(relative)
    missing = {name: sorted(set(roster) - {row["path"] for row in category_rows[name]})
               for name, roster in categories.items() if name not in ("python_tests", "web_tests")}
    missing = {name: value for name, value in missing.items() if value}
    unclassified = sorted(candidates - classified)

    writers, timers, dependencies, analysis = [], [], {}, {}
    analyzed = (permanent + category_rows["temporary_real_state_importers"]
                + category_rows["persona_schema_template"] + category_rows["support"])
    for row in analyzed:
        rel, path = row["path"], repo / row["path"]
        data = path.read_text(errors="replace")
        python = path.suffix == ".py" or data.startswith("#!/usr/bin/env python")
        if python:
            try:
                tree = ast.parse(data, filename=rel)
            except SyntaxError as exc:
                analysis[rel], dependencies[rel] = f"closed exception: parse failed at {exc.lineno}", []
                continue
            deps = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    deps.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    deps.add(("." * node.level) + (node.module or ""))
                elif isinstance(node, ast.Call):
                    name = _call_name(node)
                    if (_is_mutating_call(node)
                            or name in ("Popen", "run", "system", "_git")):
                        writers.append({"path": rel, "line": node.lineno,
                                        "call": _qualified_call_name(node),
                                        "kind": "local write or external effect"})
                    if name in _TIMER_CALLS or name == "setitimer":
                        timers.append({"path": rel, "line": node.lineno, "call": name})
            dependencies[rel], analysis[rel] = sorted(deps), "complete Python AST call/import inventory"
        elif path.suffix in (".ts", ".tsx"):
            dependencies[rel] = sorted(set(re.findall(
                r"(?:from\s+|import\s*\()[\"']([^\"']+)[\"']", data)))
            for number, line in enumerate(data.splitlines(), 1):
                if re.search(r"\b(?:setTimeout|setInterval)\s*\(", line):
                    timers.append({"path": rel, "line": number, "call": "web timer"})
                if re.search(r"\b(?:fetch|request|mutate|post|del)\s*\(", line, re.I):
                    writers.append({"path": rel, "line": number, "call": "web request",
                                    "kind": "API effect"})
            analysis[rel] = "closed TypeScript import/API-effect/timer scan; no local state authority"
        elif data.startswith("#!") and re.search(r"\b(?:ba)?sh\b", data.splitlines()[0]):
            dependencies[rel] = sorted(set(re.findall(
                r"(?:^|[;&|]\s*|\bexec\s+)([A-Za-z0-9_.-]+)", data, re.M)))
            for number, line in enumerate(data.splitlines(), 1):
                if re.search(r"(?:^|\s)(?:exec|mkdir|mv|rm|cp|git|systemctl)\s|(?:>|>>)\s*", line):
                    writers.append({"path": rel, "line": number, "call": "shell write",
                                    "kind": "local write or external effect"})
                if re.search(r"(?:^|\s)sleep\s", line):
                    timers.append({"path": rel, "line": number, "call": "shell sleep"})
            analysis[rel] = "closed shell command/redirection scan"
        elif rel in SUPPORT_ROSTER:
            dependencies[rel] = []
            analysis[rel] = "closed declarative service/CI/build support; recipes reviewed as effects"
            for number, line in enumerate(data.splitlines(), 1):
                if re.search(r"^\s*(?:ExecStart=|run:|uses:|\t)", line):
                    writers.append({"path": rel, "line": number, "call": "declared command",
                                    "kind": "external effect"})
                if re.search(r"(?:RestartSec|timeout-minutes|\bsleep\b)", line):
                    timers.append({"path": rel, "line": number, "call": "declared timer"})
        elif rel in PERSONA_SCHEMA_TEMPLATE_ROSTER:
            dependencies[rel] = []
            analysis[rel] = "closed non-code runtime input; exact bytes and consuming tree are inventoried"
        else:
            dependencies[rel] = []
            analysis[rel] = "closed static CSS/build input; no executable state authority"
    endpoint_specs = (
        ("POST", "/api/project/add", 'parts[2] == "add"'),
        ("POST", "/api/project/remove", 'parts[2] == "remove"'),
        ("POST", "/api/decide", 'api == "decide"'),
        ("POST", "/api/task/action", 'parts[2] == "action"'),
        ("POST", "/api/l2/message", 'parts[2] == "message"'),
        ("POST", "/api/l3/reset", 'parts[2] == "reset"'),
        ("POST", "/api/chat", 'api == "chat"'),
        ("POST", "/api/digest/speak", 'parts[2] == "speak"'),
        ("POST", "/api/install-statusline", 'api == "install-statusline"'),
    )
    server_lines = (repo / "altitude" / "server.py").read_text().splitlines()
    endpoints = []
    for method, route, needle in endpoint_specs:
        line = next((number for number, text in enumerate(server_lines, 1) if needle in text), None)
        endpoints.append({"method": method, "route": route, "path": "altitude/server.py", "line": line})
    cli_mutations = (
        "project add/remove", "task new/reject/block/resume/paths/hold-merge/done/brief/reply",
        "fyi", "dispatch", "poll", "land", "chat", "l3-reset", "incident new/amend",
        "recovery hold/clear", "install-statusline", "install-git-guards", "tls-init",
    )
    return {
        "baseline_commit": "97e11979bdc0814ad5067eab717f999d1c251437",
        "baseline_counts": BASELINE_COUNTS,
        "categories": {name: {"files": value, "file_count": len(value),
                               "lines": sum(row["lines"] for row in value)}
                       for name, value in category_rows.items()},
        "permanent_runnable": {"files": permanent, "file_count": len(permanent),
                               "lines": sum(row["lines"] for row in permanent)},
        "unclassified_files": unclassified, "missing_roster_files": missing,
        "mechanically_observed_writer_call_sites": sorted(
            writers, key=lambda row: (row["path"], row["line"], row["call"])),
        "mechanically_observed_timer_paths": sorted(
            timers, key=lambda row: (row["path"], row["line"], row["call"])),
        "mutation_endpoints": endpoints,
        "mutation_cli_families": list(cli_mutations),
        "dependencies": dependencies,
        "file_analysis_contract": analysis,
        "closed_observation_exceptions": sorted(
            {path: reason for path, reason in analysis.items()
             if reason.startswith("closed exception")}.items()),
    }


def _consumer_owner_paths(repo: Path, owner: str) -> tuple[Path, ...]:
    """Resolve only inspectable source owners; providers/operators remain explicit declarations."""
    if owner in {path.stem for path in (repo / "altitude").glob("*.py")}:
        return (repo / "altitude" / f"{owner}.py",)
    direct = {
        "bin/alt": (repo / "bin" / "alt",), "guard": (repo / "hooks" / "guard.py",),
        "server.log": (repo / "altitude" / "server.py",),
        "restart": (repo / "scripts" / "restart_altitude.py",),
        "make web": (repo / "Makefile",),
        "tls-init": (repo / "bin" / "alt",),
    }
    if owner == "hooks":
        return tuple(path for path in (repo / "hooks").glob("*") if path.is_file())
    if owner.startswith("hooks/"):
        return (repo / owner,)
    return direct.get(owner, ())


def _artifact_tokens(pattern: str) -> tuple[str, ...]:
    tokens = re.findall(r"[A-Za-z0-9_.-]+", pattern)
    exact_directories = {"archive", "hooks", "incidents", "tasks", "transcripts"}
    ignored = {".json", "json", "global", "task", "project", "external", "repository",
               "provider", "source", "current", "runtime", "owned", "per", "and", "plus"}
    selected = {token for token in tokens
                if len(token) >= 4 and token.lower() not in ignored
                and ("." in token or "-" in token or token in exact_directories)
                and not token.startswith(("<", "{"))}
    return tuple(sorted(selected, key=len, reverse=True))


def _active_consumer_evidence(repo: Path, family: ArtifactFamily) -> list[dict]:
    """Count source consumers only when the owner contains both a read seam and family token."""
    semantic_tokens = {
        "helper-bundle": ("helper-request-", '"l1"'),
        "tls-material": ("TLS_DIR",),
        "web-bundle": ("WEB_DIST", "web/dist"),
        "service-manager": ("systemctl", "altitude.service"),
        "worktree-ref-provenance": ("worktree", "branch", "refs/"),
    }
    tokens = (*_artifact_tokens(family.pattern), *semantic_tokens.get(family.name, ()))
    evidence = []
    read_seam = re.compile(r"\b(?:read_json|read_text|read_bytes|load_|glob|rglob|exists|is_file|open|systemctl)\b")
    for owner in family.consumers:
        for path in _consumer_owner_paths(repo, owner):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            if not any(read_seam.search(line) for line in lines):
                continue
            match = next(((number, token) for number, line in enumerate(lines, 1)
                          for token in tokens if token in line), None)
            if match:
                evidence.append({"owner": owner, "path": path.relative_to(repo).as_posix(),
                                 "line": match[0], "token": match[1]})
                break
    return sorted(evidence, key=lambda row: (row["path"], row["line"], row["owner"]))


def _artifact_inventory(repo: Path | None = None) -> list[dict]:
    repo = Path(repo or config.REPO).resolve()
    rows = []
    for family in _ARTIFACT_FAMILIES:
        row = asdict(family)
        row["declared_consumers"] = list(row.pop("consumers"))
        row["declared_writers"] = list(row.pop("writers"))
        row["declared_consumer_count"] = len(row["declared_consumers"])
        row["declared_writer_count"] = len(row["declared_writers"])
        row["active_consumers"] = _active_consumer_evidence(repo, family)
        row["active_consumer_count"] = len(row["active_consumers"])
        row["evidence_kind"] = ("mechanically observed source owner + read seam + artifact token; "
                                "external/provider declarations remain separate")
        rows.append(row)
    return rows


def _archive_only_project(path: Path) -> bool:
    """An unregistered project is archival only when archive/ is its entire real namespace."""
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    return (len(entries) == 1 and entries[0].name == "archive"
            and entries[0].is_dir() and not entries[0].is_symlink())


def _monitor_project_references(home: Path) -> set[str]:
    """Current monitor ownership references that prevent archive-only classification."""
    references: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            project = value.get("project")
            if _safe_component(project):
                references.add(project)
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    monitor = home / "monitor"
    for path in monitor.glob("*.json") if monitor.is_dir() else ():
        try:
            collect(json.loads(path.read_bytes()))
        except (OSError, UnicodeDecodeError, ValueError):
            pass  # the ordinary shape audit below records an unreadable active monitor file
    clearance_path = monitor / "recovery-clearances.jsonl"
    try:
        for raw in clearance_path.read_bytes().splitlines():
            if raw.strip():
                collect(json.loads(raw))
    except (OSError, UnicodeDecodeError, ValueError):
        pass  # the ordinary shape audit below records an unreadable active monitor file
    if monitor.is_dir():
        for path in (*monitor.glob("live-*.json"), *monitor.glob("counts-*.json")):
            stem = path.name.removeprefix("live-").removeprefix("counts-").split("--", 1)[0]
            if _safe_component(stem):
                references.add(stem)
    return references


def offline_preflight(home: Path, *, repo: Path | None = None, inventory_repo: Path | None = None,
                      manifest_role: str,
                      expected_commit: str | None = None, prior_receipt: dict | None = None,
                      bootstrap_evidence: dict | None = None,
                      bootstrap_raw_evidence: bytes | None = None,
                      startup_source: dict | None = None, loaded_modules: dict | None = None,
                      provider_home: Path | None = None,
                      observe_manager: bool = True) -> dict:
    """Inventory a copied state home without locks, normalization, network, or writes."""
    requested_home = Path(home).expanduser()
    home_is_symlink = requested_home.is_symlink()
    home = requested_home.resolve()
    inspected_repo = Path(repo).resolve() if repo is not None else None
    inventory_repo = Path(inventory_repo or repo or config.REPO).resolve()
    provider_home = Path(provider_home or config.HOME).expanduser().resolve()
    initial_state = runtime_identity._tree_identity(home)
    active_issues: list[ShapeIssue] = []
    archive_issues: list[ShapeIssue] = []
    if not home.is_dir():
        active_issues.append(ShapeIssue("active", str(home), "copied ALTITUDE_HOME is not a directory"))
    if home_is_symlink:
        active_issues.append(ShapeIssue("active", str(requested_home),
                                        "copied ALTITUDE_HOME is a symlink"))

    projects_path = home / "projects.json"
    projects = _json(projects_path, active_issues, "active", required=True) if home.is_dir() else None
    if not isinstance(projects, dict):
        if projects is not None:
            active_issues.append(ShapeIssue("active", str(projects_path), "project registry is not an object"))
        projects = {}
    else:
        _version(projects, "project_registry", projects_path, active_issues, "active")
        # projects.json is a map rather than a version envelope today.  A literal schema_version
        # entry is therefore not a project and is removed after the version check.
        projects.pop("schema_version", None)

    project_names = []
    for name, value in sorted(projects.items()):
        if not _safe_component(name):
            active_issues.append(ShapeIssue("active", str(projects_path), f"unsafe project identity {name!r}"))
            continue
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            active_issues.append(ShapeIssue("active", str(projects_path), f"project {name!r} has an unknown config shape"))
            continue
        _closed_fields(value, PROJECT_FIELDS, "project config", projects_path, active_issues, "active")
        if value.get("name") not in (None, name):
            active_issues.append(ShapeIssue("active", str(projects_path),
                                            f"project {name!r} has a mismatched name identity"))
        if any(value.get(field) not in (None, *ENGINES) for field in ("l2_engine", "l3_engine")):
            active_issues.append(ShapeIssue("active", str(projects_path),
                                            f"project {name!r} has an unknown provider"))
        project_names.append(name)

    registered_names = set(project_names)
    monitor_references = _monitor_project_references(home)
    # A damaged registry must not hide physical project state.  Archive-only is a positive proof:
    # anything except one real archive/ directory is active or unknown and blocks cutover.
    reserved = {"monitor", "tls", "hooks"}
    if home.is_dir():
        for path in sorted(home.iterdir(), key=lambda item: item.name):
            if path.name in reserved or path.name.startswith(".") or not path.is_dir():
                continue
            markers = ("tasks", "archive", "l3.json", "hold.json", "chat.jsonl", "inbox.jsonl",
                       "events.log", "incidents.jsonl", "l3-actions", "github-issue-drafts")
            if not any((path / marker).exists() for marker in markers):
                continue
            if path.is_symlink():
                active_issues.append(ShapeIssue("active", str(path), "project state directory is a symlink"))
                continue
            if not _safe_component(path.name):
                active_issues.append(ShapeIssue("active", str(path), "unsafe physical project identity"))
                continue
            if path.name not in project_names:
                archive_only = _archive_only_project(path) and path.name not in monitor_references
                target = archive_issues if archive_only else active_issues
                scope = "archive" if archive_only else "active"
                target.append(ShapeIssue(scope, str(path), "physical project state is absent from projects.json"))
                project_names.append(path.name)

        for project in sorted(monitor_references - registered_names):
            active_issues.append(ShapeIssue(
                "active", str(home / "monitor"),
                f"active monitor state references unregistered project {project!r}"))

        opaque = ("monitor", "hooks", "tls", *project_names)
        _enumerate_namespace(
            home, file_patterns=_ROOT_FILES, dir_patterns=opaque, opaque_dirs=opaque,
            issues=active_issues, scope="active", label="state-root")

    tasks, archived = [], []
    for project in project_names:
        project_dir = home / project
        if not project_dir.is_dir() or project_dir.is_symlink():
            active_issues.append(ShapeIssue(
                "active", str(project_dir), "registered project namespace is absent or not a real directory"))
            continue
        _enumerate_namespace(
            project_dir, file_patterns=_PROJECT_FILES, dir_patterns=_PROJECT_DIRS,
            opaque_dirs=("tasks", "archive", "l3-codex-runtime"), issues=active_issues,
            scope="active", label="project")
        _project_artifacts(project_dir, active_issues)
        l3_path = project_dir / "l3.json"
        l3 = _json(l3_path, active_issues, "active")
        if l3 is not None:
            if isinstance(l3, dict):
                _version(l3, "l3_session", l3_path, active_issues, "active")
                _closed_fields(l3, L3_FIELDS, "L3 state", l3_path, active_issues, "active")
                _routing_shape(l3.get("routing"), l3_path, active_issues, "active")
                sessions = l3.get("sessions", {})
                if not isinstance(sessions, dict):
                    active_issues.append(ShapeIssue("active", str(l3_path), "L3 sessions is not an object"))
                else:
                    for provider, session in sessions.items():
                        if provider not in ENGINES or not isinstance(session, dict):
                            active_issues.append(ShapeIssue(
                                "active", str(l3_path), f"unknown L3 provider/session {provider!r}"))
                            continue
                        _closed_fields(session, L3_SESSION_FIELDS, f"{provider} L3 session",
                                       l3_path, active_issues, "active")
                        if session.get("session_id") is not None and not isinstance(session.get("session_id"), str):
                            active_issues.append(ShapeIssue(
                                "active", str(l3_path), f"{provider} L3 session identity is not a string"))
                        elif session.get("session_id"):
                            active_issues.append(ShapeIssue(
                                "active", str(l3_path),
                                f"active v1 {provider} L3 session must settle before cutover"))
            else:
                active_issues.append(ShapeIssue("active", str(l3_path), "L3 session state is not an object"))
        hold_path = project_dir / "hold.json"
        hold = _json(hold_path, active_issues, "active")
        if hold is not None and not isinstance(hold, dict):
            active_issues.append(ShapeIssue("active", str(hold_path), "project hold is not an object"))
        else:
            _unversioned(hold, "project hold", hold_path, active_issues, "active")
            _closed_fields(hold, frozenset(("at", "reason")), "project hold",
                           hold_path, active_issues, "active")
            if isinstance(hold, dict) and (not isinstance(hold.get("at"), str)
                                           or not isinstance(hold.get("reason"), str)):
                active_issues.append(ShapeIssue("active", str(hold_path),
                                                "project hold lacks at/reason evidence"))
            elif isinstance(hold, dict):
                active_issues.append(ShapeIssue(
                    "active", str(hold_path), "active v1 project hold must settle before cutover"))
        for namespace, target, issue_list in (("tasks", tasks, active_issues), ("archive", archived, archive_issues)):
            directory = project_dir / namespace
            if not directory.is_dir():
                continue
            scope = "active" if namespace == "tasks" else "archive"
            for task_dir in sorted(directory.iterdir(), key=lambda p: p.name):
                if task_dir.is_symlink() or not task_dir.is_dir():
                    issue_list.append(ShapeIssue(scope, str(task_dir),
                                                 "unrecognized task-namespace entry"))
                    continue
                if not _safe_component(task_dir.name):
                    issue_list.append(ShapeIssue(scope, str(task_dir), "unsafe task directory identity"))
                    continue
                _enumerate_namespace(
                    task_dir, file_patterns=_TASK_FILES, dir_patterns=_TASK_DIRS,
                    opaque_dirs=("l1/*.codex-runtime",),
                    issues=issue_list, scope=scope, label="task")
                row = _task_identity(task_dir, scope, issue_list, provider_home)
                if row is not None:
                    target.append({"project": project, **row})

    for task in tasks:
        active_issues.append(ShapeIssue(
            "active", f"{task['project']}/{task['slug']}",
            f"active v1 task {task.get('state')!r} must be settled before cutover"))
    for task in (*tasks, *archived):
        worker = task.get("physical_worker") or {}
        if worker.get("active"):
            active_issues.append(ShapeIssue(
                "active", f"{task['project']}/{task['slug']}",
                "active v1 physical worker must stop before cutover"))
        for helper in task.get("helpers") or ():
            if helper.get("done") is None:
                active_issues.append(ShapeIssue(
                    "active", f"{task['project']}/{task['slug']}/l1/{helper.get('name')}",
                    "active v1 helper must be settled before cutover"))

    recovery_path = home / "monitor" / "recovery-hold.json"
    recovery = _json(recovery_path, active_issues, "active")
    if recovery is not None:
        if not isinstance(recovery, dict):
            active_issues.append(ShapeIssue("active", str(recovery_path), "recovery hold is not an object"))
            recovery = None
        else:
            _version(recovery, "recovery_hold", recovery_path, active_issues, "active")
            _closed_fields(recovery, frozenset(("active", "since", "episode", "faults", "repair",
                                                "updated", "l3_attention")),
                           "recovery hold", recovery_path, active_issues, "active")
            _recovery_nested_shapes(recovery, recovery_path, active_issues, "active")
            if recovery.get("active") is not True or not isinstance(recovery.get("episode"), str):
                active_issues.append(ShapeIssue("active", str(recovery_path), "recovery hold lacks active=true and episode identity"))
            if not isinstance(recovery.get("faults"), list):
                active_issues.append(ShapeIssue("active", str(recovery_path), "recovery faults are not a list"))
            else:
                for number, fault in enumerate(recovery["faults"]):
                    if not isinstance(fault, dict) or not isinstance(fault.get("kind"), str):
                        active_issues.append(ShapeIssue(
                            "active", f"{recovery_path}:faults[{number}]", "recovery fault lacks a kind identity"))
                    else:
                        _closed_fields(fault, frozenset(("first", "last", "count", "kind", "reason",
                                                        "incident", "by")), "recovery fault",
                                       recovery_path, active_issues, "active")
            active_issues.append(ShapeIssue(
                "active", str(recovery_path),
                "active v1 recovery episode must settle before cutover"))
            for field in ("repair", "l3_attention"):
                if recovery.get(field) is not None and not isinstance(recovery.get(field), dict):
                    active_issues.append(ShapeIssue(
                        "active", str(recovery_path), f"recovery {field} is not an object or null"))

    faults_path = home / "monitor" / "faults.json"
    fault_state = _json(faults_path, active_issues, "active")
    system_faults = []
    if fault_state is not None:
        if not isinstance(fault_state, dict):
            active_issues.append(ShapeIssue("active", str(faults_path), "fault index is not an object"))
        else:
            for kind, value in sorted(fault_state.items()):
                if not isinstance(kind, str) or not isinstance(value, dict):
                    active_issues.append(ShapeIssue(
                        "active", str(faults_path), f"fault {kind!r} has an unknown shape"))
                    continue
                _closed_fields(value, frozenset(("first", "last", "count", "incident", "detail",
                                                 "project", "task")),
                               "system fault", faults_path, active_issues, "active")
                system_faults.append({
                    "kind": kind, "incident": value.get("incident"), "project": value.get("project"),
                    "task": value.get("task"), "count": value.get("count"),
                })

    monitor_dir = home / "monitor"
    _enumerate_namespace(
        monitor_dir, file_patterns=_MONITOR_FILES, issues=active_issues,
        scope="active", label="monitor")
    clearance_path = monitor_dir / "recovery-clearances.jsonl"
    for clearance in _jsonl(clearance_path, active_issues, "active"):
        _closed_fields(clearance, frozenset(("at", "by", "reason", "since", "repair", "faults",
                                             "l3_attention")),
                       "recovery clearance", clearance_path, active_issues, "active")
        _clearance_nested_shapes(clearance, clearance_path, active_issues, "active")
    if monitor_dir.is_dir():
        observed = [monitor_dir / "quota-codex.json", monitor_dir / "usage-limit.json",
                    monitor_dir / "restart-pending.json"]
        observed.extend(monitor_dir.glob("statusline-*.json"))
        observed.extend(monitor_dir.glob("live-*.json"))
        observed.extend(monitor_dir.glob("counts-*.json"))
        for path in sorted(set(observed), key=lambda item: item.name):
            value = _json(path, active_issues, "active")
            if value is not None and not isinstance(value, dict):
                active_issues.append(ShapeIssue("active", str(path), "monitor record is not an object"))
            else:
                _unversioned(value, "monitor record", path, active_issues, "active")
                if isinstance(value, dict):
                    if path.name == "quota-codex.json":
                        allowed = frozenset(("known", "why", "primary_used", "primary_resets",
                                             "primary_window_minutes", "secondary_used", "secondary_resets",
                                             "secondary_window_minutes", "plan_type", "read_at"))
                    elif path.name == "usage-limit.json":
                        allowed = frozenset(("until", "seen", "detail"))
                    elif path.name == "restart-pending.json":
                        allowed = frozenset(("since", "head", "files"))
                        active_issues.append(ShapeIssue(
                            "active", str(path),
                            "active v1 restart effect must settle before cutover"))
                    elif path.name.startswith("live-"):
                        allowed = frozenset(("at", "agent", "idle_since", "capacity", "limited"))
                    elif path.name.startswith("counts-"):
                        allowed = frozenset(("edits", "files"))
                    else:
                        allowed = STATUSLINE_FIELDS
                    _closed_fields(value, allowed, "monitor record", path, active_issues, "active")
                    if path.name.startswith("live-") and value.get("agent") is not None:
                        agent = _object(value.get("agent"), "live-cache agent", path,
                                        active_issues, "active")
                        if agent is not None:
                            _closed_fields(agent, LIVE_AGENT_FIELDS, "live-cache agent",
                                           path, active_issues, "active")
                            usage = agent.get("usage")
                            if usage is not None:
                                usage = _object(usage, "live-cache agent usage", path,
                                                active_issues, "active")
                                _closed_fields(usage, LIVE_USAGE_FIELDS, "live-cache agent usage",
                                               path, active_issues, "active")
                    if path.name.startswith("statusline-") and value.get("rate_limits") is not None:
                        rate_limits = _object(value.get("rate_limits"), "statusline rate_limits",
                                              path, active_issues, "active")
                        if rate_limits is not None:
                            _closed_fields(rate_limits, STATUSLINE_RATE_LIMIT_FIELDS,
                                           "statusline rate_limits", path, active_issues, "active")
                            for window_name, window in rate_limits.items():
                                window = _object(window, f"statusline {window_name} window", path,
                                                 active_issues, "active")
                                _closed_fields(window, STATUSLINE_WINDOW_FIELDS,
                                               f"statusline {window_name} window", path,
                                               active_issues, "active")
                    if path.name.startswith("statusline-"):
                        for field, fields in STATUSLINE_NESTED_FIELDS.items():
                            if value.get(field) is None:
                                continue
                            nested = _object(value.get(field), f"statusline {field}", path,
                                             active_issues, "active")
                            _closed_fields(nested, fields, f"statusline {field}", path,
                                           active_issues, "active")

    jobs_dir = provider_home / ".claude" / "jobs"
    if jobs_dir.exists():
        if jobs_dir.is_symlink() or not jobs_dir.is_dir():
            active_issues.append(ShapeIssue(
                "active", str(jobs_dir), "Claude provider jobs roster is not a real directory"))
        else:
            for job_dir in sorted(jobs_dir.iterdir(), key=lambda item: item.name):
                if job_dir.is_symlink() or not job_dir.is_dir() or not _safe_component(job_dir.name):
                    active_issues.append(ShapeIssue(
                        "active", str(job_dir), "unrecognized Claude provider job entry"))
                    continue
                path = job_dir / "state.json"
                value = _claude_worker(path, job_dir.name, None, active_issues, "active")
                if value is None:
                    continue
                if (value.get("state") not in ("done", "failed", "stopped")
                        and value.get("status") != "exited"):
                    active_issues.append(ShapeIssue(
                        "active", str(path), "active v1 Claude provider job must settle before cutover"))

    incidents = _incident_identities(home, project_names, active_issues)
    task_counts = Counter(str(task.get("state")) for task in tasks)
    manifest = runtime_identity.runtime_manifest(
        role=manifest_role, repo=inspected_repo, state_home=home,
        entrypoint=str((inspected_repo or Path(config.REPO).resolve()) / "bin" / "alt"),
        expected_commit=expected_commit, prior_receipt=prior_receipt,
        bootstrap_evidence=bootstrap_evidence, bootstrap_raw_evidence=bootstrap_raw_evidence,
        startup_source=startup_source, loaded_modules=loaded_modules,
        observe_manager=observe_manager)
    if initial_state.get("sha256") != (manifest.get("state") or {}).get("sha256"):
        active_issues.append(ShapeIssue(
            "active", str(home), "copied state changed while the offline preflight was reading it"))
    for error in manifest["identity_errors"]:
        active_issues.append(ShapeIssue("active", str(inspected_repo or config.REPO),
                                        f"runtime identity unknown: {error}"))
    try:
        source_inventory = _source_inventory(inventory_repo)
        if source_inventory["unclassified_files"]:
            active_issues.append(ShapeIssue(
                "active", str(inventory_repo), "unclassified production paths: "
                + ", ".join(source_inventory["unclassified_files"][:20])))
        if source_inventory["missing_roster_files"]:
            active_issues.append(ShapeIssue(
                "active", str(inventory_repo), "required §07 roster paths are missing: "
                + ", ".join(f"{category}={','.join(paths[:5])}"
                            for category, paths in source_inventory["missing_roster_files"].items())))
    except (OSError, ValueError) as exc:
        active_issues.append(ShapeIssue(
            "active", str(inventory_repo),
            f"production source inventory unavailable: {type(exc).__name__}: {exc}"))
        source_inventory = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "cutover_eligible": not active_issues,
        "cutover_blocked": bool(active_issues),
        "copied_state_home": str(home),
        "manifest": manifest,
        "tasks": {
            "active_by_state": {state: task_counts.get(state, 0) for state in sorted(ACTIVE_TASK_STATES)},
            "active": sorted(tasks, key=lambda row: (row["project"], str(row.get("slug")))),
            "archive": sorted(archived, key=lambda row: (row["project"], str(row.get("slug")))),
        },
        "recovery": {
            "active": bool(recovery and recovery.get("active")),
            "episode": recovery.get("episode") if isinstance(recovery, dict) else None,
            "repair": recovery.get("repair") if isinstance(recovery, dict) else None,
            "l3_attention": recovery.get("l3_attention") if isinstance(recovery, dict) else None,
            "faults": recovery.get("faults", []) if isinstance(recovery, dict) else [],
            "system_faults": system_faults,
            "project_holds": [
                {"project": project, "hold": value}
                for project in project_names
                if isinstance((value := _json(home / project / "hold.json", [], "active")), dict)
            ],
        },
        "incident_identities": incidents,
        "legacy_artifact_families": _artifact_inventory(inventory_repo),
        "source_inventory": source_inventory,
        "unknown_active_shapes": [asdict(issue) for issue in sorted(active_issues, key=lambda row: (row.path, row.reason))],
        "unknown_archived_shapes": [asdict(issue) for issue in sorted(archive_issues, key=lambda row: (row.path, row.reason))],
        "archive_policy": "unknown archived state is reported for an isolated read-only decoder and is never imported into active flows",
    }
