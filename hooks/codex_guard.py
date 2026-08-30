#!/usr/bin/env python3
"""Translate Codex hook payloads into the hardened Claude Bash guard contract."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


try:
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
except (OSError, TypeError, ValueError) as exc:
    print(f"altitude Codex guard: invalid hook input: {exc}", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(payload, dict):
    print("altitude Codex guard: hook input is not an object", file=sys.stderr)
    raise SystemExit(2)

def patch_targets(patch: str) -> list[str]:
    targets = re.findall(r"^(?:\+\+\+|---)\s+(?:[ab]/)?([^\s]+)", patch, re.M)
    targets += re.findall(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", patch, re.M)
    targets += re.findall(r"^\*\*\* Move to:\s*(.+?)\s*$", patch, re.M)
    return [path for path in targets if path != "/dev/null"]


def deny(message: str) -> None:
    print(f"altitude Codex guard: blocked — {message}", file=sys.stderr)
    raise SystemExit(2)


tool_input = payload.get("tool_input") or {}
if not isinstance(tool_input, dict):
    deny("malformed tool_input")
worktree = os.environ.get("ALTITUDE_TASK_WORKTREE")
required_hooks = os.environ.get("ALTITUDE_REQUIRED_HOOKS")
trusted_git_dir = os.environ.get("ALTITUDE_TRUSTED_GIT_DIR")
common_dir = None
if any((worktree, required_hooks, trusted_git_dir)) and not all((worktree, required_hooks, trusted_git_dir)):
    deny("the trusted task Git guard environment is incomplete")
if worktree and required_hooks and trusted_git_dir:
    absolute = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--absolute-git-dir"], capture_output=True, text=True)
    if (absolute.returncode != 0
            or Path(absolute.stdout.strip()).resolve() != Path(trusted_git_dir).resolve()):
        deny("the isolated checkout Git pointer changed")
    configured = subprocess.run(
        ["git", "-C", worktree, "config", "--path", "--get", "core.hooksPath"],
        capture_output=True, text=True,
    )
    if configured.returncode != 0:
        deny("the hardened Git hook path is missing")
    configured_path = Path(configured.stdout.strip())
    if not configured_path.is_absolute():
        configured_path = Path(worktree) / configured_path
    if configured_path.resolve() != Path(required_hooks).resolve():
        deny("the hardened Git hook path changed")
    common = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if common.returncode != 0:
        deny("the task Git common directory is indeterminate")
    common_dir = Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = Path(worktree) / common_dir
    common_dir = common_dir.resolve()
    os.environ["ALTITUDE_GIT_COMMON_DIR"] = str(common_dir)
    branch = subprocess.run(
        ["git", "-C", worktree, "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    if branch.returncode != 0 or branch.stdout.strip() in ("main", "master"):
        deny("the task checkout is detached or on a protected branch")
    values = []
    for ref in ("refs/heads/main", "refs/remotes/origin/main"):
        result = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--verify", ref],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            deny(f"required protected ref {ref} is missing")
        values.append(result.stdout.strip())
    if values[0] != values[1]:
        deny("the protected local main ref is not pinned to origin/main")


tool_name = str(payload.get("tool_name") or "").lower()
write_tool = tool_name in ("apply_patch", "edit", "write", "multiedit", "write_file", "create_file")
paths = [tool_input.get("file_path"), tool_input.get("path")]
if tool_name == "apply_patch":
    patch = str(tool_input.get("patch") or tool_input.get("input") or "")
    paths += patch_targets(patch)
if write_tool:
    base = Path(worktree).resolve() if worktree else Path.cwd()
    altitude_home = Path(os.environ["ALTITUDE_HOME"]).resolve() if os.environ.get("ALTITUDE_HOME") else None
    project, task = os.environ.get("ALTITUDE_PROJECT"), os.environ.get("ALTITUDE_TASK")
    task_dir = (altitude_home / project / "tasks" / task).resolve() if altitude_home and project and task else None
    checkpoints = ({task_dir / name for name in ("report.md", "report.json", "progress.md")}
                   if task_dir else set())
    for value in paths:
        target = Path(str(value)) if value else None
        if target is not None and not target.is_absolute():
            target = base / target
        target = target.resolve() if target is not None else None
        if target and common_dir and (target == common_dir or common_dir in target.parents):
            deny("non-shell tools may not write the shared Git common directory")
        if target and altitude_home and (target == altitude_home or altitude_home in target.parents) and target not in checkpoints:
            deny("non-shell tools may not write live Altitude state outside exact checkpoint files")
if tool_name in ("exec_command", "shell", "bash"):
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        print("altitude Codex guard: malformed tool_input", file=sys.stderr)
        raise SystemExit(2)
    payload = {**payload, "tool_name": "Bash",
               "tool_input": {**tool_input, "command": tool_input.get("command") or tool_input.get("cmd") or ""}}

guard = Path(__file__).with_name("guard.py")
try:
    result = subprocess.run([sys.executable, str(guard)], input=json.dumps(payload).encode(),
                            capture_output=True, env=os.environ.copy())
except OSError as exc:
    deny(f"the hardened shell guard could not run: {exc}")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
# Exit 2 is the explicit blocking contract; an unexpected guard failure must
# not be downgraded into a non-blocking hook error.
raise SystemExit(0 if result.returncode == 0 else 2)
