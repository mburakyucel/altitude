"""Read-only, role-bound identity for an Altitude install or detached candidate."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from . import config

MANIFEST_SCHEMA = "altitude.runtime-manifest/v2"
BOOTSTRAP_EVIDENCE_SCHEMA = "altitude.bootstrap-freeze-evidence/v1"
RAW_FREEZE_EVIDENCE_SCHEMA = "altitude.raw-freeze-evidence/v1"
RUNNING_INSTALL = "running_install"
STOPPED_INSTALL = "stopped_install"
DETACHED_CANDIDATE = "detached_candidate"
MANIFEST_ROLES = (RUNNING_INSTALL, STOPPED_INSTALL, DETACHED_CANDIDATE)
LEGACY_STATE_VERSION = "legacy-unversioned"
SUPPORTED_STATE_VERSIONS = {
    "project_registry": (LEGACY_STATE_VERSION,), "task_status": (LEGACY_STATE_VERSION,),
    "recovery_hold": (LEGACY_STATE_VERSION,), "incident_index": (LEGACY_STATE_VERSION,),
    "l3_session": (LEGACY_STATE_VERSION,), "l1_run": (LEGACY_STATE_VERSION,),
    "codex_worker": (LEGACY_STATE_VERSION,), "transcript": ("altitude.transcript/v1",),
}
_SOURCE_DIRS = ("personas", "schemas", "templates", "hooks")
_BUILD_INPUTS = (
    "web/design/tokens.css", "web/index.html", "web/package.json", "web/tsconfig.json",
    "web/vite.config.ts", "systemd/altitude.service", "Makefile",
)
_FULL_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_BOOTSTRAP_UNAVAILABLE = frozenset(("ordinary_running_manifest", "live_main_pid",
                                    "live_cgroup_membership", "live_process_start"))
_BOOTSTRAP_FIELDS = frozenset((
    "schema_version", "raw_evidence_sha256", "state_sha256", "source_commit", "source_sha256",
    "entry_sha256", "repository_unit_sha256", "web_bundle_sha256",
    "manager_configuration_sha256", "installed_fragment", "installed_drop_ins",
    "last_observed_pid", "process_empty", "unavailable_running_facts", "operator_authorization",
))
_RAW_FREEZE_FIELDS = frozenset((
    "schema_version", "observed_at", "loaded_source", "service", "state",
    "provider_units", "worktrees", "refs", "processes",
))
_RAW_LOADED_SOURCE_FIELDS = frozenset(("commit", "source_sha256", "entry_sha256"))
_RAW_SERVICE_FIELDS = frozenset((
    "unit", "fragment_sha256", "configuration_sha256", "last_main_pid",
    "last_start_ticks", "control_group", "final_pids", "empty",
))
_RAW_STATE_FIELDS = frozenset(("home", "sha256"))
_RAW_PROVIDER_FIELDS = frozenset((
    "provider", "unit", "active_state", "sub_state", "control_group", "final_pids", "empty",
))
_RAW_WORKTREE_FIELDS = frozenset(("project", "slug", "path", "branch", "head", "dirty"))
_RAW_REF_FIELDS = frozenset(("repository", "name", "oid"))
_RAW_PROCESS_FIELDS = frozenset((
    "kind", "provider", "pid", "start_ticks", "unit", "worker_id", "session_id", "stopped",
))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: dict) -> str:
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return _sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True).encode())


def _file_identity(path: Path) -> dict:
    path = Path(path)
    row = {"path": str(path.resolve(strict=False)), "present": None, "bytes": None,
           "sha256": None, "error": None}
    try:
        if not path.is_file():
            row["present"] = False
            return row
        data = path.read_bytes()
        row.update({"present": True, "bytes": len(data), "sha256": _sha256(data)})
        if path.is_symlink():
            row["symlink"] = os.readlink(path)
    except OSError as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _tree_identity(root: Path) -> dict:
    root = Path(root)
    if not root.is_dir():
        return {"path": str(root.resolve(strict=False)), "present": False, "files": 0,
                "sha256": None, "errors": []}
    digest, count, errors = hashlib.sha256(), 0, []
    try:
        paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    except OSError as exc:
        return {"path": str(root.resolve(strict=False)), "present": None, "files": 0,
                "sha256": None, "errors": [f"cannot enumerate: {type(exc).__name__}: {exc}"]}
    for path in paths:
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix().encode()
        try:
            link = os.readlink(path).encode() if path.is_symlink() else b""
            # Hash a symlink as a symlink.  Never follow a copied-state link into mutable host data.
            data = b"" if path.is_symlink() else path.read_bytes()
            mode = path.lstat().st_mode & 0o777
        except OSError as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {type(exc).__name__}: {exc}")
            continue
        for item in (rel, link, str(mode).encode(), data):
            digest.update(len(item).to_bytes(8, "big") + item)
        count += 1
    return {"path": str(root.resolve(strict=False)), "present": True, "files": count,
            "sha256": None if errors else digest.hexdigest(), "errors": errors}


def _git_run(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-C", str(repo), *args]
    return subprocess.run(command, capture_output=True, text=text, check=False, timeout=10, env=env)


def _git(repo: Path, *args: str) -> dict:
    try:
        result = _git_run(repo, *args)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"value": None,
                "error": f"{' '.join(args)} could not run: {type(exc).__name__}: {exc}"}
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout or "").split())[:300]
        return {"value": None,
                "error": f"{' '.join(args)} exited {result.returncode}: {detail or 'no diagnostic'}"}
    return {"value": result.stdout.strip(), "error": None}


def _is_runtime_source_path(relative: str) -> bool:
    parts = Path(relative).parts
    if not parts or "__pycache__" in parts:
        return False
    if parts[0] == "altitude" and len(parts) > 1 and relative.endswith(".py"):
        return True
    if relative in ("bin/alt", "scripts/restart_altitude.py", *_BUILD_INPUTS):
        return True
    if parts[0] in _SOURCE_DIRS and len(parts) > 1:
        return True
    if len(parts) > 2 and parts[:2] == ("web", "src"):
        return (relative.endswith((".ts", ".tsx", ".css")) and ".test." not in relative
                and "/test/" not in relative and relative != "web/src/vitest.setup.ts")
    return False


def _runtime_source_files(repo: Path) -> list[Path]:
    files = list((repo / "altitude").rglob("*.py"))
    for relative in ("bin/alt", "scripts/restart_altitude.py", *_BUILD_INPUTS):
        path = repo / relative
        if path.is_file() or path.is_symlink():
            files.append(path)
    for directory in _SOURCE_DIRS:
        root = repo / directory
        if root.is_dir():
            files.extend(path for path in root.rglob("*")
                         if (path.is_file() or path.is_symlink()) and "__pycache__" not in path.parts)
    web = repo / "web" / "src"
    if web.is_dir():
        files.extend(path for path in web.rglob("*") if (path.is_file() or path.is_symlink())
                     and _is_runtime_source_path(path.relative_to(repo).as_posix()))
    return sorted(set(files), key=lambda path: path.relative_to(repo).as_posix())


def _selected_tree_identity(repo: Path, files: Iterable[Path]) -> dict:
    digest, rows, errors = hashlib.sha256(), [], []
    for path in files:
        rel = path.relative_to(repo).as_posix()
        try:
            link = os.readlink(path) if path.is_symlink() else None
            data = os.fsencode(link) if link is not None else path.read_bytes()
            mode = "120000" if link is not None else "100755" if path.stat().st_mode & 0o111 else "100644"
        except OSError as exc:
            errors.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        header = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
        stat = path.lstat()
        row = {"path": rel, "kind": "symlink" if link is not None else "file", "mode": mode,
               "bytes": len(data), "sha256": _sha256(data),
               "stat": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
               "git_blob_sha1": hashlib.sha1(header).hexdigest(),
               "git_blob_sha256": hashlib.sha256(header).hexdigest()}
        if link is not None:
            row["symlink"] = link
            if rel.startswith("altitude/") and rel.endswith(".py"):
                errors.append(f"{rel}: executable Python runtime source is a symlink")
        rows.append(row)
        digest.update(rel.encode() + b"\0" + mode.encode() + b"\0" + row["sha256"].encode() + b"\n")
    return {"files": rows, "file_count": len(rows),
            "sha256": None if errors else digest.hexdigest(), "errors": errors}


def loaded_module_origins() -> dict[str, str | None]:
    """Capture origins after imports; byte/stat identity remains owned by the pre-import snapshot."""
    return {
        name: (str(Path(origin).resolve(strict=False)) if origin else None)
        for name, module in sorted(sys.modules.items())
        if (name == "altitude" or name.startswith("altitude."))
        for origin in (getattr(module, "__file__", None),)
    }


def _startup_source_errors(snapshot: dict | None, modules: dict | None,
                           repo: Path, source: dict) -> list[str]:
    """Revalidate the pre-import capture supplied by ``bin/alt`` and loaded module origins."""
    if not isinstance(snapshot, dict):
        return ["pre-import runtime-source capture is unavailable"]
    if set(snapshot) != {"root", "files", "sha256"} or snapshot.get("root") != str(repo):
        return ["pre-import runtime-source capture has an unknown shape or checkout"]
    rows = snapshot.get("files")
    if not isinstance(rows, list) or not rows:
        return ["pre-import runtime-source capture has no files"]
    expected = {row.get("path"): row for row in source.get("files") or []}
    captured = {}
    errors = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "mode", "sha256", "stat"}
                or not isinstance(row.get("path"), str) or not isinstance(row.get("stat"), list)):
            errors.append("pre-import runtime-source capture contains an invalid row")
            continue
        captured[row["path"]] = row
    if set(captured) != set(expected):
        errors.append("pre-import runtime-source file roster differs from the inspected runtime tree")
    for relative in sorted(set(captured) & set(expected)):
        before, after = captured[relative], expected[relative]
        if any(before.get(key) != after.get(key) for key in ("mode", "sha256", "stat")):
            errors.append(f"runtime source changed after pre-import capture: {relative}")
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if snapshot.get("sha256") != _sha256(canonical):
        errors.append("pre-import runtime-source capture digest is invalid")
    captured_paths = {(repo / relative).resolve(strict=False) for relative in captured}
    if not isinstance(modules, dict) or not {"altitude", "altitude.config", "altitude.manifest"}.issubset(modules):
        errors.append("loaded Altitude module-origin capture is unavailable or incomplete")
        modules = {}
    for name, origin in sorted(modules.items()):
        if not origin:
            errors.append(f"loaded module {name} has no source origin")
            continue
        path = Path(origin)
        if path.suffix in (".pyc", ".pyo"):
            path = Path(str(path)[:-1])
        if path.resolve(strict=False) not in captured_paths:
            errors.append(f"loaded module {name} is outside the pre-import runtime capture")
    return errors


def _commit_comparison(repo: Path, files: Iterable[dict], commit: str | None) -> dict:
    if not commit:
        return {"exact": None, "mismatches": [], "error": "expected commit is unknown"}
    try:
        result = _git_run(repo, "ls-tree", "-r", "-z", "--full-tree", commit, text=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"exact": None, "mismatches": [],
                "error": f"commit tree could not run: {type(exc).__name__}: {exc}"}
    if result.returncode:
        detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip()[:300]
        return {"exact": None, "mismatches": [],
                "error": f"commit tree exited {result.returncode}: {detail or 'no diagnostic'}"}
    expected = {}
    try:
        stdout = result.stdout.encode() if isinstance(result.stdout, str) else result.stdout
        for raw in stdout.split(b"\0"):
            if not raw:
                continue
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split()
            relative = os.fsdecode(raw_path)
            if _is_runtime_source_path(relative):
                expected[relative] = (mode, object_type, oid)
    except (UnicodeDecodeError, ValueError) as exc:
        return {"exact": None, "mismatches": [], "error": f"commit tree output is invalid: {exc}"}
    actual = {row["path"]: row for row in files}
    mismatches = sorted(set(expected) ^ set(actual))
    for relative in sorted(set(expected) & set(actual)):
        row = actual[relative]
        expected_mode, object_type, expected_oid = expected[relative]
        actual_oid = row["git_blob_sha1"] if len(expected_oid) == 40 else row["git_blob_sha256"]
        if object_type != "blob" or row["mode"] != expected_mode or actual_oid != expected_oid:
            mismatches.append(relative)
    return {"exact": not mismatches, "mismatches": sorted(set(mismatches)), "error": None}


def _checkout_build_version(repo: Path) -> tuple[str | None, str | None]:
    path = repo / "altitude" / "__init__.py"
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return None, f"cannot inspect build version: {type(exc).__name__}: {exc}"
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if (any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return node.value.value, None
    return None, "inspected checkout has no literal __version__"


def _cgroup_identity(control_group: str | None) -> dict:
    if control_group == "":
        return {"control_group": "", "present": False, "pids": [], "empty": True, "error": None}
    if control_group is None or not control_group.startswith("/"):
        return {"control_group": control_group, "present": None, "pids": [], "empty": None,
                "error": "manager ControlGroup is absent or invalid"}
    path = Path("/sys/fs/cgroup") / control_group.lstrip("/") / "cgroup.procs"
    try:
        pids = [int(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        return {"control_group": control_group, "present": None, "pids": [], "empty": None,
                "error": f"cannot inspect cgroup membership: {type(exc).__name__}: {exc}"}
    return {"control_group": control_group, "present": True, "pids": sorted(set(pids)),
            "empty": not pids, "error": None}


def _process_identity(pid: int | None) -> dict:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return {"pid": pid, "start_ticks": None, "error": None}
    try:
        # Field 22 follows 19 fields after the parenthesized comm value, which may itself contain ')'.
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])
    except (OSError, IndexError, ValueError) as exc:
        return {"pid": pid, "start_ticks": None,
                "error": f"cannot inspect process start: {type(exc).__name__}: {exc}"}
    return {"pid": pid, "start_ticks": start_ticks, "error": None}


def _empty_manager(error: str) -> dict:
    return {"observed": False, "sha256": None, "configuration_sha256": None,
            "fragment_path": None, "drop_in_paths": [], "exec_start": None,
            "load_state": None, "active_state": None, "sub_state": None,
            "unit_file_state": None, "need_daemon_reload": None, "main_pid": None,
            "control_group": None, "cgroup": _cgroup_identity(None),
            "process": _process_identity(None), "error": error}


def _manager_unit_identity(*, observe: bool) -> dict:
    if not observe:
        return _empty_manager("manager observation disabled")
    properties = ("FragmentPath", "DropInPaths", "ExecStart", "LoadState", "ActiveState",
                  "SubState", "UnitFileState", "NeedDaemonReload", "MainPID", "ControlGroup")
    command = ["systemctl", "--user", "show", "altitude.service", "--no-pager",
               *(f"--property={item}" for item in properties)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return _empty_manager(f"manager query failed: {type(exc).__name__}: {exc}")
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout or "").split())[:300]
        return _empty_manager(f"manager query exited {result.returncode}: {detail or 'no diagnostic'}")
    values = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    required_nonempty = ("FragmentPath", "ExecStart", "LoadState", "ActiveState", "SubState",
                         "UnitFileState", "NeedDaemonReload")
    missing = [item for item in properties if item not in values]
    missing.extend(item for item in required_nonempty if item in values and not values[item])
    try:
        main_pid = int(values.get("MainPID", ""))
        if main_pid < 0:
            raise ValueError("negative PID")
    except ValueError:
        main_pid = None
        if "MainPID" not in missing:
            missing.append("MainPID")
    identity = {
        "fragment_path": values.get("FragmentPath") or None,
        "drop_in_paths": (values.get("DropInPaths") or "").split(),
        "exec_start": values.get("ExecStart") or None,
        "load_state": values.get("LoadState") or None,
        "active_state": values.get("ActiveState") or None,
        "sub_state": values.get("SubState") or None,
        "unit_file_state": values.get("UnitFileState") or None,
        "need_daemon_reload": values.get("NeedDaemonReload") or None,
        "main_pid": main_pid,
        "control_group": values.get("ControlGroup") if "ControlGroup" in values else None,
    }
    error = f"manager returned no/invalid {', '.join(missing)}" if missing else None
    configuration = {key: identity[key] for key in
                     ("fragment_path", "drop_in_paths", "exec_start", "load_state",
                      "unit_file_state", "need_daemon_reload")}
    cgroup = _cgroup_identity(identity["control_group"])
    process = _process_identity(main_pid)
    return {"observed": error is None,
            "sha256": _sha256(json.dumps(identity | {"cgroup": cgroup, "process": process},
                                          sort_keys=True).encode())
                      if error is None else None,
            "configuration_sha256": _sha256(json.dumps(configuration, sort_keys=True).encode())
                                    if error is None else None,
            **identity, "cgroup": cgroup, "process": process, "error": error}


def _manager_provider_units(*, observe: bool) -> dict:
    """Observe every Altitude-owned transient provider unit; ambiguity is not an empty roster."""
    if not observe:
        return {"units": None, "error": "manager observation disabled"}
    command = ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend",
               "--no-pager", "altitude-codex-*.scope"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"units": None, "error": f"provider-unit query failed: {type(exc).__name__}: {exc}"}
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout or "").split())[:300]
        return {"units": None,
                "error": f"provider-unit query exited {result.returncode}: {detail or 'no diagnostic'}"}
    return {"units": sorted({line.split()[0] for line in result.stdout.splitlines() if line.split()}),
            "error": None}


def _decode_systemd(value: str) -> str:
    return re.sub(r"\\x([0-9A-Fa-f]{2})", lambda match: chr(int(match.group(1), 16)), value)


def _manager_command(exec_start: str | None) -> tuple[Path | None, list[str], str | None]:
    records = re.findall(r"\{([^{}]*)\}", exec_start or "")
    if len(records) != 1:
        return None, [], "manager ExecStart is not one exact command"
    fields = [field.strip() for field in records[0].split(";") if field.strip()]
    path_field = next((field for field in fields if field.startswith("path=")), fields[0] if fields else "")
    raw_path = path_field.removeprefix("path=")
    argv_field = next((field for field in fields if field.startswith("argv[]=")), "")
    encoded_argv = argv_field.removeprefix("argv[]=").split() if argv_field else []
    path = _decode_systemd(raw_path)
    argv = [_decode_systemd(item) for item in encoded_argv]
    if not path.startswith("/") or argv != [path, "serve"]:
        return None, argv, "manager ExecStart must be exactly one absolute '<checkout>/bin/alt serve' command"
    return Path(path).resolve(strict=False), argv, None


def _prior_receipt_error(receipt: dict | None) -> str | None:
    if not isinstance(receipt, dict):
        return "immutable prior-install receipt is absent"
    if receipt.get("schema_version") != MANIFEST_SCHEMA:
        return "prior receipt has an unsupported manifest schema"
    kind = receipt.get("prior_kind")
    if not (receipt.get("role") == RUNNING_INSTALL and kind == "ordinary_running_install"
            or receipt.get("role") == STOPPED_INSTALL and kind == "bootstrap_freeze"):
        return "prior receipt is neither an ordinary running install nor a bootstrap stopped install"
    if receipt.get("valid") is not True or receipt.get("identity_errors"):
        return "prior-install receipt is not valid"
    if receipt.get("manifest_sha256") != _canonical_hash(receipt):
        return "prior-install receipt hash does not match its bytes"
    if not isinstance(receipt.get("installation_identity"), dict):
        return "prior-install receipt lacks installation identity"
    if kind == "bootstrap_freeze":
        problems = _bootstrap_errors(receipt.get("bootstrap_evidence"),
                                     receipt.get("installation_identity"))
        if problems:
            return "bootstrap prior evidence is invalid: " + problems[0]
    return None


def _exact_fields(value: object, fields: frozenset[str], label: str, errors: list[str]) -> dict:
    if not isinstance(value, dict):
        errors.append(f"raw evidence {label} is not an object")
        return {}
    unknown, missing = sorted(set(value) - fields), sorted(fields - set(value))
    if unknown:
        errors.append(f"raw evidence {label} has unknown fields: {', '.join(unknown)}")
    if missing:
        errors.append(f"raw evidence {label} lacks fields: {', '.join(missing)}")
    return value


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bootstrap_state_errors(state_home: str | None) -> list[str]:
    """Prove the copied v1 state has no task, worker, helper, or pending effect to inherit."""
    if not isinstance(state_home, str):
        return ["copied frozen-state path is absent"]
    home, errors = Path(state_home), []
    try:
        projects = json.loads((home / "projects.json").read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [f"copied project registry is unavailable: {type(exc).__name__}: {exc}"]
    if not isinstance(projects, dict):
        return ["copied project registry is not an object"]
    project_names = [key for key in projects if key != "schema_version"]
    if any(not isinstance(key, str) or not key or "/" in key or "\\" in key
           for key in project_names):
        return ["copied project registry contains an unsafe project identity"]
    for project in sorted(project_names):
        root = home / project
        try:
            l3 = json.loads((root / "l3.json").read_bytes())
        except FileNotFoundError:
            l3 = None
        except (OSError, UnicodeDecodeError, ValueError):
            errors.append(f"v1 L3 evidence is unreadable: {project}/l3.json")
            l3 = None
        if isinstance(l3, dict):
            sessions = l3.get("sessions") or {}
            if not isinstance(sessions, dict):
                errors.append(f"v1 L3 session roster is not an object: {project}")
            elif any(isinstance(row, dict) and row.get("session_id")
                     for row in sessions.values()):
                errors.append(f"active v1 L3 session remains: {project}")
        for namespace in ("tasks", "archive"):
            directory = root / namespace
            if not directory.is_dir():
                continue
            for task_dir in sorted((path for path in directory.iterdir() if path.is_dir()),
                                   key=lambda path: path.name):
                try:
                    task = json.loads((task_dir / "status.json").read_bytes())
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    errors.append(f"{project}/{namespace}/{task_dir.name} status is unavailable: {type(exc).__name__}")
                    continue
                if namespace == "tasks":
                    errors.append(f"active v1 task remains: {project}/{task_dir.name}")
                if isinstance(task, dict) and (task.get("pending_action") is not None
                                               or task.get("completion_requested") is not None):
                    errors.append(f"pending v1 task effect remains: {project}/{task_dir.name}")
                for helper in (task_dir / "l1").glob("*.json"):
                    try:
                        row = json.loads(helper.read_bytes())
                    except (OSError, UnicodeDecodeError, ValueError):
                        errors.append(f"v1 helper evidence is unreadable: {project}/{task_dir.name}/{helper.name}")
                        continue
                    if not isinstance(row, dict) or row.get("done") is None:
                        errors.append(f"active v1 helper remains: {project}/{task_dir.name}/{helper.name}")
                for worker in (task_dir / "l2-engine").glob("*.json"):
                    try:
                        row = json.loads(worker.read_bytes())
                    except (OSError, UnicodeDecodeError, ValueError):
                        errors.append(f"v1 worker evidence is unreadable: {project}/{task_dir.name}/{worker.name}")
                        continue
                    if not isinstance(row, dict) or row.get("stopped") is not True:
                        errors.append(f"active v1 worker remains: {project}/{task_dir.name}/{worker.name}")
        for path in (root / "l3-actions").glob("*.json"):
            try:
                row = json.loads(path.read_bytes())
            except (OSError, UnicodeDecodeError, ValueError):
                errors.append(f"v1 action evidence is unreadable: {project}/{path.name}")
                continue
            if not isinstance(row, dict) or row.get("status") == "applying":
                errors.append(f"pending v1 action remains: {project}/{path.name}")
        for path in (root / "github-issue-drafts").glob("*.json"):
            try:
                row = json.loads(path.read_bytes())
            except (OSError, UnicodeDecodeError, ValueError):
                errors.append(f"v1 issue evidence is unreadable: {project}/{path.name}")
                continue
            if not isinstance(row, dict) or row.get("status") == "pending_review":
                errors.append(f"pending v1 issue effect remains: {project}/{path.name}")
    monitor = home / "monitor"
    for name in ("restart-pending.json", "recovery-hold.json"):
        path = monitor / name
        if not path.exists():
            continue
        try:
            row = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, ValueError):
            errors.append(f"v1 operational evidence is unreadable: monitor/{name}")
            continue
        if name == "restart-pending.json" or not isinstance(row, dict) or row.get("active") is True \
                or row.get("repair") is not None or row.get("l3_attention") is not None:
            errors.append(f"pending v1 operational effect remains: monitor/{name}")
    return errors


def _bootstrap_git_errors(installation: dict | None) -> list[str]:
    """Refuse orphaned v1 task branches/worktrees that the copied state cannot own."""
    checkout = (installation or {}).get("checkout_path")
    if not isinstance(checkout, str):
        return ["stopped installation checkout is unavailable for Git ownership proof"]
    repo, errors = Path(checkout), []
    refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)",
                "refs/heads/worktree-*")
    if refs["error"]:
        errors.append(f"cannot inspect v1 task refs: {refs['error']}")
    elif refs["value"]:
        errors.append("orphaned v1 task refs remain without active copied-state owners")
    worktrees = _git(repo, "worktree", "list", "--porcelain")
    if worktrees["error"]:
        errors.append(f"cannot inspect v1 task worktrees: {worktrees['error']}")
    elif any(line.startswith("branch refs/heads/worktree-")
             for line in worktrees["value"].splitlines()):
        errors.append("orphaned v1 task worktrees remain without active copied-state owners")
    return errors


def _raw_freeze_errors(raw: bytes | None, *, expected_sha: object,
                       state_sha: str | None, state_home: str | None,
                       installation: dict | None, last_observed_pid: object = None,
                       provider_units_observation: dict | None = None) -> list[str]:
    """Validate the exact prerequisite bytes and their closed process/ref/worktree roster."""
    if not isinstance(raw, bytes):
        return ["raw prerequisite evidence bytes are absent"]
    errors = []
    if _sha256(raw) != expected_sha:
        errors.append("raw prerequisite evidence bytes do not match raw_evidence_sha256")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        return [*errors, f"raw prerequisite evidence is invalid JSON: {exc}"]
    value = _exact_fields(value, _RAW_FREEZE_FIELDS, "root", errors)
    if value.get("schema_version") != RAW_FREEZE_EVIDENCE_SCHEMA:
        errors.append("raw prerequisite evidence has an unsupported schema_version")
    if not isinstance(value.get("observed_at"), str) or not value.get("observed_at"):
        errors.append("raw prerequisite evidence lacks observed_at")

    loaded = _exact_fields(value.get("loaded_source"), _RAW_LOADED_SOURCE_FIELDS,
                           "loaded_source", errors)
    service = _exact_fields(value.get("service"), _RAW_SERVICE_FIELDS, "service", errors)
    state = _exact_fields(value.get("state"), _RAW_STATE_FIELDS, "state", errors)
    if state.get("sha256") != state_sha:
        errors.append("raw prerequisite state digest differs from the copied frozen state")
    if state.get("home") != state_home:
        errors.append("raw prerequisite state path differs from the copied frozen state")
    if installation:
        comparisons = {
            "commit": installation.get("source_commit"),
            "source_sha256": installation.get("source_sha256"),
            "entry_sha256": installation.get("entry_sha256"),
        }
        for field, expected in comparisons.items():
            if loaded.get(field) != expected:
                errors.append(f"raw prerequisite loaded_source differs from stopped install at {field}")
        fragment = installation.get("installed_fragment") or {}
        if service.get("fragment_sha256") != fragment.get("sha256"):
            errors.append("raw prerequisite service fragment differs from stopped install")
        if service.get("configuration_sha256") != installation.get("manager_configuration_sha256"):
            errors.append("raw prerequisite manager configuration differs from stopped install")
    if (service.get("unit") != "altitude.service" or not _positive_int(service.get("last_main_pid"))
            or not _positive_int(service.get("last_start_ticks"))
            or not isinstance(service.get("control_group"), str)
            or service.get("empty") is not True or service.get("final_pids") != []):
        errors.append("raw prerequisite service/process-empty evidence is incomplete")
    if service.get("last_main_pid") != last_observed_pid:
        errors.append("raw prerequisite service PID differs from bootstrap declaration")

    roster_specs = (
        ("provider_units", _RAW_PROVIDER_FIELDS), ("worktrees", _RAW_WORKTREE_FIELDS),
        ("refs", _RAW_REF_FIELDS), ("processes", _RAW_PROCESS_FIELDS),
    )
    rosters = {}
    for label, fields in roster_specs:
        rows = value.get(label)
        if not isinstance(rows, list):
            errors.append(f"raw prerequisite {label} roster is not an array")
            rows = []
        rosters[label] = [_exact_fields(row, fields, f"{label}[{index}]", errors)
                          for index, row in enumerate(rows)]
    for row in rosters["provider_units"]:
        if (row.get("provider") not in ("claude", "codex") or not isinstance(row.get("unit"), str)
                or not row.get("unit") or row.get("active_state") != "inactive"
                or row.get("sub_state") != "dead" or not isinstance(row.get("control_group"), str)
                or row.get("empty") is not True or row.get("final_pids") != []):
            errors.append("raw prerequisite provider-unit row is not proven inactive/empty")
    for row in rosters["worktrees"]:
        if (not all(isinstance(row.get(field), str) and row.get(field)
                    for field in ("project", "slug", "path", "branch"))
                or not isinstance(row.get("head"), str) or not _FULL_SHA.fullmatch(row["head"])
                or not isinstance(row.get("dirty"), bool)):
            errors.append("raw prerequisite worktree row is incomplete")
    for row in rosters["refs"]:
        if (not all(isinstance(row.get(field), str) and row.get(field)
                    for field in ("repository", "name"))
                or not isinstance(row.get("oid"), str) or not _FULL_SHA.fullmatch(row["oid"])):
            errors.append("raw prerequisite ref row is incomplete")
    for row in rosters["processes"]:
        if (row.get("provider") not in (None, "claude", "codex")
                or not _positive_int(row.get("pid")) or not _positive_int(row.get("start_ticks"))
                or not isinstance(row.get("kind"), str) or not row.get("kind")
                or not isinstance(row.get("unit"), str) or row.get("stopped") is not True):
            errors.append("raw prerequisite process row is incomplete or not proven stopped")
    expected_process = {
        "kind": "service", "provider": None, "pid": service.get("last_main_pid"),
        "start_ticks": service.get("last_start_ticks"), "unit": "altitude.service",
        "worker_id": None, "session_id": None, "stopped": True,
    }
    if rosters["processes"] != [expected_process]:
        errors.append("raw prerequisite process roster does not equal the stopped manager process")
    for label in ("provider_units", "worktrees", "refs"):
        if rosters[label]:
            errors.append(f"raw prerequisite {label} roster is not derivable from empty active v1 state")
    observation = provider_units_observation or {}
    if observation.get("error") or not isinstance(observation.get("units"), list):
        errors.append("manager provider-unit roster is unavailable")
    elif observation["units"]:
        errors.append("manager still reports Altitude-owned provider units: "
                      + ", ".join(observation["units"][:10]))
    errors.extend(_bootstrap_state_errors(state_home))
    errors.extend(_bootstrap_git_errors(installation))
    return errors


def _bootstrap_errors(evidence: dict | None, installation: dict | None = None, *,
                      raw_evidence: bytes | None = None, state_sha: str | None = None,
                      state_home: str | None = None, verify_raw: bool = False,
                      provider_units_observation: dict | None = None) -> list[str]:
    if not isinstance(evidence, dict):
        return ["bootstrap evidence is absent"]
    errors = []
    unknown = sorted(str(key) for key in evidence if key not in _BOOTSTRAP_FIELDS)
    missing = sorted(_BOOTSTRAP_FIELDS - set(evidence))
    if unknown:
        errors.append("unknown fields: " + ", ".join(unknown))
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if evidence.get("schema_version") != BOOTSTRAP_EVIDENCE_SCHEMA:
        errors.append("unsupported schema_version")
    for field in ("raw_evidence_sha256", "state_sha256", "source_sha256", "entry_sha256",
                  "repository_unit_sha256", "web_bundle_sha256", "manager_configuration_sha256"):
        if not isinstance(evidence.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", evidence[field]):
            errors.append(f"{field} is not one SHA-256")
    if not isinstance(evidence.get("source_commit"), str) or not _FULL_SHA.fullmatch(evidence["source_commit"]):
        errors.append("source_commit is not one full commit SHA")
    if (not isinstance(evidence.get("last_observed_pid"), int)
            or isinstance(evidence.get("last_observed_pid"), bool)
            or evidence.get("last_observed_pid", -1) < 0):
        errors.append("last_observed_pid is not a non-negative integer")
    if evidence.get("process_empty") is not True:
        errors.append("process_empty is not true")
    if set(evidence.get("unavailable_running_facts") or ()) != _BOOTSTRAP_UNAVAILABLE:
        errors.append("unavailable_running_facts is not the closed bootstrap set")
    authorization = evidence.get("operator_authorization")
    if (not isinstance(authorization, dict) or set(authorization) != {"id", "actor", "at", "reason"}
            or any(not isinstance(authorization.get(field), str) or not authorization[field].strip()
                   for field in ("id", "actor", "at", "reason"))):
        errors.append("operator_authorization is not one bounded exact receipt")
    elif any(len(authorization[field]) > limit for field, limit in
             (("id", 100), ("actor", 80), ("at", 80), ("reason", 500))):
        errors.append("operator_authorization exceeds bounded fields")
    if installation:
        comparisons = {
            "source_commit": installation.get("source_commit"),
            "source_sha256": installation.get("source_sha256"),
            "entry_sha256": installation.get("entry_sha256"),
            "repository_unit_sha256": installation.get("repository_unit_sha256"),
            "web_bundle_sha256": installation.get("web_bundle_sha256"),
            "manager_configuration_sha256": installation.get("manager_configuration_sha256"),
            "installed_fragment": installation.get("installed_fragment"),
            "installed_drop_ins": installation.get("installed_drop_ins"),
        }
        for field, current in comparisons.items():
            if evidence.get(field) != current:
                errors.append(f"frozen evidence differs from stopped install at {field}")
    if state_sha is not None and evidence.get("state_sha256") != state_sha:
        errors.append("frozen evidence differs from copied state at state_sha256")
    if verify_raw:
        errors.extend(_raw_freeze_errors(
            raw_evidence, expected_sha=evidence.get("raw_evidence_sha256"),
            state_sha=state_sha, state_home=state_home, installation=installation,
            last_observed_pid=evidence.get("last_observed_pid"),
            provider_units_observation=provider_units_observation))
    return errors


def runtime_manifest(*, role: str, repo: Path | None = None, state_home: Path | None = None,
                     entrypoint: str | None = None, expected_commit: str | None = None,
                     prior_receipt: dict | None = None, bootstrap_evidence: dict | None = None,
                     bootstrap_raw_evidence: bytes | None = None,
                     startup_source: dict | None = None,
                     loaded_modules: dict | None = None, observe_manager: bool = True) -> dict:
    """Inspect one named role without writing, importing, or executing inspected code."""
    if role not in MANIFEST_ROLES:
        raise ValueError(f"manifest role must be one of {MANIFEST_ROLES}")
    requested_repo = Path(repo).resolve() if repo is not None else None
    requested_state_home = Path(state_home or config.ROOT).expanduser()
    state_home_is_symlink = requested_state_home.is_symlink()
    state_home = requested_state_home.resolve()
    state_identity = _tree_identity(state_home)
    errors: list[str] = []
    manager = (_empty_manager("not applicable to detached_candidate") if role == DETACHED_CANDIDATE
               else _manager_unit_identity(observe=observe_manager))
    manager_entry, manager_argv, command_error = _manager_command(manager.get("exec_start"))
    manager_checkout = (manager_entry.parent.parent if manager_entry and manager_entry.name == "alt"
                        and manager_entry.parent.name == "bin" else None)
    if role == STOPPED_INSTALL:
        if prior_receipt is not None:
            receipt_problem = _prior_receipt_error(prior_receipt)
            if receipt_problem:
                errors.append(f"prior_install_receipt: {receipt_problem}")
        elif bootstrap_evidence is None:
            errors.append("prior_install_receipt: prior receipt or one-time bootstrap evidence is required")
        repo = manager_checkout or requested_repo or Path(config.REPO).resolve()
        if requested_repo and manager_checkout and requested_repo != manager_checkout:
            errors.append("installed_checkout: requested B does not equal manager ExecStart checkout")
    else:
        repo = requested_repo or Path(config.REPO).resolve()

    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    remote = _git(repo, "rev-parse", "refs/remotes/origin/main")
    if role == DETACHED_CANDIDATE:
        if not isinstance(expected_commit, str) or not _FULL_SHA.fullmatch(expected_commit):
            errors.append("expected_commit: detached_candidate requires one full pinned commit SHA")
            expected = None
        else:
            expected = expected_commit
    elif role == STOPPED_INSTALL:
        if isinstance(prior_receipt, dict):
            expected = prior_receipt.get("source_commit")
        else:
            expected = (bootstrap_evidence or {}).get("source_commit")
    else:
        expected = head["value"] or None
    if expected and head["value"] != expected:
        errors.append("source_commit: inspected checkout HEAD does not equal the role's expected commit")

    source = _selected_tree_identity(repo, _runtime_source_files(repo))
    comparison = _commit_comparison(repo, source["files"], expected)
    for label, observation in (("git_head", head), ("git_status", status)):
        if observation["error"]:
            errors.append(f"{label}: {observation['error']}")
    errors.extend(f"loaded_source: {error}" for error in source["errors"])
    if not source["files"]:
        errors.append("loaded_source: no runtime source inputs were found")
    if comparison["error"]:
        errors.append(f"loaded_source: {comparison['error']}")
    elif comparison["mismatches"]:
        errors.append("loaded_source: inputs differ from expected commit: "
                      + ", ".join(comparison["mismatches"][:10]))
    source_commit = (expected if expected and head["value"] == expected and comparison["exact"] is True
                     and not source["errors"] else None)
    startup_errors = (_startup_source_errors(startup_source, loaded_modules, repo, source)
                      if role == RUNNING_INSTALL else [])
    if startup_errors:
        source_commit = None
    errors.extend(f"executed_source: {error}" for error in startup_errors)

    inspected_entry = repo / "bin" / "alt"
    if role == RUNNING_INSTALL:
        actual_entry = Path(entrypoint or sys.argv[0]).resolve(strict=False)
    elif role == STOPPED_INSTALL:
        actual_entry = manager_entry or inspected_entry
    else:
        actual_entry = Path(entrypoint).resolve(strict=False) if entrypoint else inspected_entry.resolve()
        if actual_entry != inspected_entry.resolve():
            errors.append("candidate_entry: detached entry is not <candidate>/bin/alt")
    entry_identity = _file_identity(actual_entry)
    repository_unit = _file_identity(repo / "systemd" / "altitude.service")
    web_bundle = _tree_identity(repo / "web" / "dist")
    build_version, build_error = _checkout_build_version(repo)
    if build_error:
        errors.append(f"build_version: {build_error}")
    for label, row in (("runtime_entry", entry_identity), ("repository_unit_template", repository_unit)):
        if row["present"] is not True or row["error"]:
            errors.append(f"{label}: {row['error'] or 'required file is absent'}")
    if web_bundle["present"] is not True or not web_bundle["files"]:
        errors.append("web_bundle: required served bundle is absent or empty")
    errors.extend(f"web_bundle: {error}" for error in web_bundle["errors"])

    if manager.get("fragment_path"):
        installed_unit = _file_identity(Path(manager["fragment_path"]))
    else:
        installed_unit = {"path": None, "present": None, "bytes": None, "sha256": None,
                          "error": "installed FragmentPath is unknown"}
    drop_ins = [_file_identity(Path(path)) for path in manager.get("drop_in_paths", [])]
    installed_head = _git(manager_checkout, "rev-parse", "HEAD") if manager_checkout else {
        "value": None, "error": "manager runtime entry is not <checkout>/bin/alt"}
    installed_checkout = {"path": str(manager_checkout) if manager_checkout else None,
                          "git_head": installed_head["value"] or None, "error": installed_head["error"]}

    if role != DETACHED_CANDIDATE:
        if manager["error"]:
            errors.append(f"manager_unit: {manager['error']}")
        if manager["load_state"] not in (None, "loaded"):
            errors.append(f"manager_unit: LoadState is {manager['load_state']!r}, not 'loaded'")
        if manager["need_daemon_reload"] not in (None, "no"):
            errors.append("manager_unit: NeedDaemonReload is not 'no'")
        if manager["unit_file_state"] not in (None, "enabled", "enabled-runtime"):
            errors.append(
                f"manager_unit: UnitFileState {manager['unit_file_state']!r} is not enabled")
        if command_error:
            errors.append(f"manager_runtime_entry: {command_error}")
        if installed_unit["present"] is not True or installed_unit["error"]:
            errors.append(f"installed_unit: {installed_unit['error'] or 'manager fragment is absent'}")
        for row in drop_ins:
            if row["present"] is not True or row["error"]:
                errors.append(f"installed_drop_in: {row['error'] or 'manager drop-in is absent'}")
        if installed_head["error"]:
            errors.append(f"installed_checkout: {installed_head['error']}")
        if manager_checkout and manager_checkout != repo:
            errors.append("installed_checkout: manager checkout does not equal inspected checkout")
        if manager_entry and manager_entry != inspected_entry.resolve():
            errors.append("manager_runtime_entry: ExecStart is not the inspected checkout entry")
        if installed_unit.get("sha256") != repository_unit.get("sha256"):
            errors.append("installed_unit: fragment bytes differ from the checkout unit template")
        if role == RUNNING_INSTALL:
            if actual_entry != inspected_entry.resolve():
                errors.append("loaded_entry: process entry does not equal inspected checkout entry")
            if manager["active_state"] not in ("active", "activating"):
                errors.append("running_process: manager is not active/activating")
            accepted_substates = ({"running"} if manager["active_state"] == "active"
                                  else {"start-pre", "start", "start-post"})
            if manager["sub_state"] not in accepted_substates:
                errors.append(
                    f"running_process: manager SubState {manager['sub_state']!r} is not a stable start/run state")
            if not isinstance(manager["main_pid"], int) or manager["main_pid"] <= 0:
                errors.append("running_process: manager MainPID is not positive")
            cgroup = manager["cgroup"]
            if cgroup["error"] or cgroup["empty"] is not False or manager["main_pid"] not in cgroup["pids"]:
                errors.append("running_process: MainPID is not proven live in the manager cgroup")
            process = manager.get("process") or {}
            if (process.get("pid") != manager["main_pid"] or process.get("error")
                    or not isinstance(process.get("start_ticks"), int)
                    or isinstance(process.get("start_ticks"), bool)
                    or process["start_ticks"] <= 0):
                errors.append("running_process: PID/start identity is not proven")
        else:
            if manager["active_state"] != "inactive" or manager["sub_state"] != "dead":
                errors.append("stopped_process: manager is not exactly inactive/dead")
            if manager["main_pid"] != 0:
                errors.append("stopped_process: manager MainPID is not zero")
            if manager["cgroup"]["error"] or manager["cgroup"]["empty"] is not True:
                errors.append("stopped_process: service cgroup is not proven empty")

    installation_identity = {
        "source_commit": source_commit, "source_sha256": source["sha256"],
        "build_version": build_version, "entry_sha256": entry_identity["sha256"],
        "repository_unit_sha256": repository_unit["sha256"],
        "web_bundle_sha256": web_bundle["sha256"], "checkout_path": str(repo),
        "checkout_head": head["value"], "manager_configuration_sha256": manager.get("configuration_sha256"),
        "installed_fragment": {"path": installed_unit.get("path"), "sha256": installed_unit.get("sha256")},
        "installed_drop_ins": [{"path": row.get("path"), "sha256": row.get("sha256")} for row in drop_ins],
    }
    if role == DETACHED_CANDIDATE:
        installation_identity.update({"manager_configuration_sha256": None,
                                      "installed_fragment": None, "installed_drop_ins": []})
    elif role == STOPPED_INSTALL and prior_receipt is not None and _prior_receipt_error(prior_receipt) is None:
        prior = prior_receipt["installation_identity"]
        for key in sorted(set(prior) | set(installation_identity)):
            if prior.get(key) != installation_identity.get(key):
                errors.append(f"prior_install_receipt: installed identity drifted at {key}")
    if role == STOPPED_INSTALL and prior_receipt is None:
        errors.extend(f"bootstrap_evidence: {error}"
                      for error in _bootstrap_errors(
                          bootstrap_evidence, installation_identity,
                          raw_evidence=bootstrap_raw_evidence, state_sha=state_identity.get("sha256"),
                          state_home=str(state_home), verify_raw=True,
                          provider_units_observation=_manager_provider_units(observe=observe_manager)))

    if role != DETACHED_CANDIDATE:
        if state_home_is_symlink:
            errors.append("state_identity: state home is a symlink")
        if state_identity.get("present") is not True or state_identity.get("errors"):
            errors.append("state_identity: copied/runtime state home cannot be hashed exactly")

    prior_kind = ("ordinary_running_install" if role == RUNNING_INSTALL else
                  "bootstrap_freeze" if role == STOPPED_INSTALL and prior_receipt is None else
                  "revalidated_stopped_install" if role == STOPPED_INSTALL else None)

    result = {
        "schema_version": MANIFEST_SCHEMA, "role": role, "prior_kind": prior_kind,
        "build_version": build_version,
        "source_commit": source_commit, "expected_commit": expected,
        "identity_errors": errors, "valid": not errors,
        "loaded_source": {"checkout": str(repo), "entrypoint": str(actual_entry),
                          "git_head": head["value"],
                          "dirty": None if comparison["exact"] is None else not comparison["exact"],
                          "repository_dirty": None if status["error"] else bool(status["value"]),
                          "commit_comparison": comparison, **source},
        "executed_source": {
            "captured": isinstance(startup_source, dict),
            "sha256": startup_source.get("sha256") if isinstance(startup_source, dict) else None,
            "file_count": len(startup_source.get("files") or []) if isinstance(startup_source, dict) else 0,
            "valid": not startup_errors if role == RUNNING_INSTALL else None,
        },
        "installed_checkout": installed_checkout, "installation_identity": installation_identity,
        "remote_main": {"sha": remote["value"] or None, "error": remote["error"],
                        "observation": "local refs/remotes/origin/main; no fetch"},
        "state": {"home": str(state_home), "present": state_identity.get("present"),
                  "files": state_identity.get("files"), "sha256": state_identity.get("sha256"),
                  "errors": state_identity.get("errors"),
                  "supported_versions": {key: list(value) for key, value in SUPPORTED_STATE_VERSIONS.items()}},
        "service_unit": {"name": "altitude.service", "repository_template": repository_unit,
                         "installed_fragment": installed_unit, "drop_ins": drop_ins,
                         "runtime_entry": entry_identity, "manager": manager, "exec_argv": manager_argv},
        "web_bundle": web_bundle,
        "prior_install_manifest_sha256": ((prior_receipt or {}).get("manifest_sha256")
                                           if role == STOPPED_INSTALL else None),
        "bootstrap_evidence": bootstrap_evidence if prior_kind == "bootstrap_freeze" else None,
    }
    result["manifest_sha256"] = _canonical_hash(result)
    return result


def public_manifest(value: dict) -> dict:
    """Return the API projection without host paths, commands, PIDs, or raw diagnostics."""
    loaded = value.get("loaded_source") or {}
    service = value.get("service_unit") or {}
    manager = service.get("manager") or {}

    def public_file(row: dict) -> dict:
        return {"present": row.get("present"), "bytes": row.get("bytes"),
                "sha256": row.get("sha256"), "error": bool(row.get("error"))}

    return {
        "schema_version": value.get("schema_version"), "role": value.get("role"),
        "prior_kind": value.get("prior_kind"),
        "manifest_sha256": value.get("manifest_sha256"), "build_version": value.get("build_version"),
        "source_commit": value.get("source_commit"), "identity_known": value.get("valid") is True,
        "identity_error_fields": [str(error).split(":", 1)[0]
                                  for error in value.get("identity_errors") or []],
        "loaded_source": {key: loaded.get(key) for key in ("git_head", "file_count", "sha256")},
        "executed_source": {key: (value.get("executed_source") or {}).get(key)
                            for key in ("captured", "file_count", "sha256", "valid")},
        "installed_checkout": {"git_head": (value.get("installed_checkout") or {}).get("git_head"),
                               "known": not bool((value.get("installed_checkout") or {}).get("error"))},
        "remote_main": {"sha": (value.get("remote_main") or {}).get("sha"),
                        "known": not bool((value.get("remote_main") or {}).get("error"))},
        "state": {key: (value.get("state") or {}).get(key)
                  for key in ("present", "files", "sha256", "supported_versions")}
                 | {"error_count": len((value.get("state") or {}).get("errors") or [])},
        "service_unit": {
            "name": service.get("name"),
            "repository_template": public_file(service.get("repository_template") or {}),
            "installed_fragment": public_file(service.get("installed_fragment") or {}),
            "drop_in_count": len(service.get("drop_ins") or []),
            "runtime_entry": public_file(service.get("runtime_entry") or {}),
            "manager": {key: manager.get(key) for key in
                        ("observed", "load_state", "active_state", "sub_state",
                         "unit_file_state", "need_daemon_reload")}
                       | {"cgroup_empty": (manager.get("cgroup") or {}).get("empty"),
                          "error": bool(manager.get("error"))},
        },
        "web_bundle": {key: (value.get("web_bundle") or {}).get(key)
                       for key in ("present", "files", "sha256")}
                      | {"error_count": len((value.get("web_bundle") or {}).get("errors") or [])},
    }
