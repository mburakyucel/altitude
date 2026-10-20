"""Shared callable engine interface; native implementations live beside it."""
from __future__ import annotations

from pathlib import Path

from .. import config
from . import common, claude, codex, token_setup
from .invocation import InvocationRefused, replaced_after_projection

from .common import (
    refresh_quotas,
    repository_rules,
    repository_rule_prompt,
    RAW_CAPTURE_CAP,
    installation,
    interactive,
    repair_session,
    install_command,
    sign_in,
    session_timeout,
    ImageInputError,
    image_capability,
    image_read_instructions,
    AUTH_CODES,
    rejection,
    cap_raw,
    LIMIT_TEXT,
    RESETS,
    TEMPORARY_CAPACITY_TEXT,
    temporary_capacity_in,
    usage_limit_in,
    record_usage_limit,
    usage_limit_path,
    note_usage_limit,
    usage_hold,
    clean_env,
    GITHUB_TOKEN_VARIABLES,
    SEND_POLL,
    SEND_ECHO_WAIT,
    coordinator_native_send,
    send_into_turn,
    recover_send,
    send_into_turn_once,
    engine_driver,
    github_token,
    service_status,
    launch_env,
    task_tool_env,
    coordinator_unit,
    agent_unit,
    machine_unit,
    machine_files,
    machine_command,
    machine_stop,
    machine_outcome,
    machine_output,
    transcript_engine,
    transcript_sources,
    transcript_rows,
    activity_source,
    session_context_source,
    delivered_context,
    public_message_identity,
    background_work,
    observe_token_usage,
    owned_worker,
    worker_sends,
    worker_unit,
    stop_owned_worker,
    start_l2,
    resume_l2,
    start_project_agent_run,
    worker_answer,
    usage_tokens,
    stop_l2_worker,
    worker_termination,
    remove_l2_worker,
    window_hold,
    worker,
    worker_detail,
    cli_version,
    worker_confinement,
    worker_sign_in,
    review_capability,
    review_mcp,
    review_active,
    review_stop,
    review,
    context_percent,
    context_rotation_due,
    context_state,
)

from .claude import (
    L3_ALLOWED_TOOLS,
    IMPROVEMENT_INPUT_TOKENS,
    IMPROVEMENT_OUTPUT_TOKENS,
    IMPROVEMENT_SENT_BYTES,
    improvement_budget,
    improvement_review,
    claude_stop,
    claude_settings,
    claude_print,
    claude_agents,
    notes_folder,
    JOBS_DIR,
    claude_job_detail,
    CLAUDE_RESULT_FACTS,
)

from .codex import (
    CODEX_UNIT_PREFIX,
    codex_turns,
    codex_l3_permissions,
    codex_l3_mcp,
    codex_bg,
    codex_exec,
)

from .common import _safe_output, _shell_command

INSTALL = {engine: common.implementation(engine).INSTALL_COMMAND for engine in config.ENGINES}
SIGN_IN = {engine: common.implementation(engine).SIGN_IN for engine in config.ENGINES}
TOKEN_SETUP = [engine for engine in config.ENGINES if common.implementation(engine).TOKEN_SETUP]


def worker_live(engine: str, task: dict, *, job_root: Path) -> bool:
    """Whether anything of the worker still runs, including commands its ended engine left in the job."""
    row = worker(engine, task, job_root=job_root)
    return bool(row and (row.get("state") == "working" or row.get("job_active")))
