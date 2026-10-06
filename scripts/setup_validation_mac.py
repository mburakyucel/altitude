#!/usr/bin/env python3
"""Plan or explicitly install the reviewed Mac validation endpoint or its prepared guest.

No default action writes files. --apply is a separate administrator action under a scoped grant.
Accounts, a private SSH route, Apple OS setup and the pinned offline template already exist.
Host apply makes the account restrictions effective for fresh SSH connections; its off marker
refuses submissions until explicit scoped enablement, while status/result/cancel remain reachable.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import struct
import sys

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))
from altitude import platform

BASE = Path("/Library/Application Support/AltitudeValidation")
SSHD_CONFIG = Path("/private/etc/ssh/sshd_config")
GUEST_JOB = Path("/Library/LaunchDaemons/dev.altitude.validation-guest.plist")
GUEST_WORK = Path("/private/var/altitude-validation")
CODE = ("altitude/__init__.py", "altitude/platform.py", "altitude/validation_payload.py",
        "altitude/validation_remote.py", "scripts/validation_remote.py", "scripts/validation_guest.py")
COMMON = {"version", "mode", "account", "python", "source_sha256"}
HOST = {"template", "template_sha256", "public_key", "source_network"}
GUEST = {"candidate", "fingerprints", "store", "browsers", "path"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_files() -> dict[str, bytes]:
    files = {}
    for name in CODE:
        path = SOURCE / name
        if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Source must contain ordinary unlinked files")
        data = path.read_bytes()
        if len(data) > 4 * 1024**2:
            raise ValueError("Source file exceeds setup limit")
        files[name] = data
    return files


def source_digest(files: dict[str, bytes]) -> str:
    return digest(json.dumps({name: digest(data) for name, data in files.items()}, sort_keys=True).encode())


def canonical(value) -> Path:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError("Setup paths must be canonical")
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Setup paths must be canonical")
    return path


def protected(path: Path, *, directory: bool = False) -> None:
    canonical(str(path))
    for entry in (path, *path.parents):
        info = entry.lstat()
        expected = stat.S_ISDIR if directory or entry != path else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Setup dependencies and destination parents need protected administrator ownership")


def public_key(path: Path) -> str:
    if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Public key must be an ordinary file")
    with path.open("rb") as source:
        data = source.read(4097)
    if len(data) > 4096:
        raise ValueError("Public key exceeds setup limit")
    return parse_public_key(data.decode("ascii").strip())


def parse_public_key(value: str) -> str:
    words = value.split()
    if len(words) < 2 or words[0] != "ssh-ed25519":
        raise ValueError("Supply the dedicated Ed25519 public key only")
    raw = base64.b64decode(words[1], validate=True)
    expected = struct.pack("!I", 11) + b"ssh-ed25519" + struct.pack("!I", 32)
    if len(raw) != len(expected) + 32 or not raw.startswith(expected):
        raise ValueError("Invalid Ed25519 public key")
    return "ssh-ed25519 " + words[1]


def private_source(value: str):
    network = ipaddress.ip_network(value, strict=True)
    shared = network.version == 4 and network.subnet_of(ipaddress.ip_network("100.64.0.0/10"))
    if (network.num_addresses != 1 or not (network.is_private or shared)
            or network.is_loopback or network.is_multicast or network.is_unspecified):
        raise ValueError("Use only the existing private route's single source address")
    return network


def key_state(uid: int) -> Path:
    state = canonical(str(BASE / "state"))
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Validation state ownership changed")
    return state


def key_admission_closed(uid: int) -> None:
    state = key_state(uid)
    off = state / "off"
    info = off.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise ValueError("Key changes require the existing private admission-off marker")
    active = state / "active.json"
    if active.exists() or active.is_symlink():
        raise ValueError("Reconcile the active validation run before changing keys")


def build_key_plan(value: dict) -> dict:
    action = value.get("action")
    fields = {"version", "mode", "action"}
    if action in {"stage", "retire"}:
        fields.add("public_key")
    if action == "retire":
        fields.add("replacement_public_key")
    if (action not in {"stage", "retire", "revoke"} or set(value) != fields
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Invalid public-key lifecycle plan")
    for path in (BASE / "broker.json", BASE / "sshd.conf", BASE / "authorized_keys", SSHD_CONFIG):
        protected(path)
    config = json.loads((BASE / "broker.json").read_text())
    if (set(config) != {"version", "runner_uid", "state", "template", "python"}
            or type(config["version"]) is not int or config["version"] != 1
            or type(config["runner_uid"]) is not int or config["runner_uid"] < 500
            or config["state"] != str(BASE / "state") or config["template"] != str(BASE / "template")):
        raise ValueError("Installed validation configuration changed")
    python = canonical(config["python"])
    protected(python)
    if action == "revoke":
        key_state(config["runner_uid"])
    else:
        key_admission_closed(config["runner_uid"])
    rules = (BASE / "sshd.conf").read_text()
    match = re.match(r"Match User ([a-z_][a-z0-9_-]{0,31})\n", rules)
    if not match:
        raise ValueError("Installed validation account restriction changed")
    command = shlex.join([str(python), "-I", "-B", str(BASE / "code/scripts/validation_remote.py"), "serve"])
    expected_rules, expected = account_rules(match[1], command)
    if rules != expected_rules:
        raise ValueError("Installed validation account restriction changed")
    original = (BASE / "authorized_keys").read_bytes()
    if len(original) > 4096:
        raise ValueError("Installed public-key file exceeds its limit")
    rows = original.decode("ascii").splitlines(keepends=True)
    if not (0 if action == "revoke" else 1) <= len(rows) <= 2:
        raise ValueError("Expected only the current key and optional staged replacement")
    keys, source = [], None
    for row in rows:
        item = re.fullmatch(r'from="([^"]+)",restrict (ssh-ed25519 [A-Za-z0-9+/=]+)\n', row)
        if not item:
            raise ValueError("Public-key file contains unrelated or changed restrictions")
        network = private_source(item[1])
        if str(network) != item[1] or (source is not None and source != item[1]):
            raise ValueError("Public-key source restrictions differ")
        source = item[1]
        key = parse_public_key(item[2])
        if key in keys:
            raise ValueError("Duplicate installed public key")
        keys.append(key)
    if action != "revoke":
        check_rules(SSHD_CONFIG.read_text(), match[1], str(private_source(source).network_address), expected)
    if action == "stage":
        key = public_key(canonical(value["public_key"]))
        if key in keys or len(keys) != 1:
            raise ValueError("Stage requires one current key and a distinct replacement")
        keys.append(key)
    elif action == "retire":
        old = public_key(canonical(value["public_key"]))
        replacement = public_key(canonical(value["replacement_public_key"]))
        if old == replacement or set(keys) != {old, replacement}:
            raise ValueError("Retirement requires the exact old key and its staged replacement")
        keys.remove(old)
    else:
        keys = []
    updated = "".join(f'from="{source}",restrict {key}\n' for key in keys).encode()
    return {"mode": "host-key", "value": value, "original": original, "updated": updated,
            "runner_uid": config["runner_uid"], "config": config}


def close_key_admission(uid: int) -> None:
    off = key_state(uid) / "off"
    fd = os.open(off, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, uid) or info.st_nlink != 1:
            raise ValueError("Invalid admission-off marker")
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, -1)
        os.fsync(fd)
    finally:
        os.close(fd)


def revocation_receipt(value: dict) -> None:
    path, temporary = BASE / "revocation.json", BASE / ".revocation.new"
    if path.exists() or path.is_symlink():
        protected(path)
    created = False
    try:
        _write(temporary, json.dumps(value, sort_keys=True).encode(), 0o600)
        created = True
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def apply_key_plan(plan: dict) -> None:
    path, temporary = BASE / "authorized_keys", BASE / ".authorized_keys.new"
    created = False
    revoke = plan["value"]["action"] == "revoke"
    if revoke:
        close_key_admission(plan["runner_uid"])
    try:
        _write(temporary, plan["updated"], 0o644)
        created = True
        # The trusted runner keeps admission closed through this administrator action.
        # Recheck immediately before replacing only this installation's key file.
        if revoke:
            close_key_admission(plan["runner_uid"])
        else:
            key_admission_closed(plan["runner_uid"])
        protected(path)
        if path.read_bytes() != plan["original"]:
            raise ValueError("Installed public keys changed during the transaction")
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    if revoke:
        # Access is already removed. Neither a stop failure nor loss of this command
        # restores keys; the private receipt makes incomplete cleanup recoverable.
        receipt = {"keys_revoked": True, "cleanup": False, "error": "Local cleanup not yet confirmed"}
        revocation_receipt(receipt)
        try:
            receipt = {**platform.validation_setup_revoke(plan["config"]), "keys_revoked": True}
        except Exception:
            revocation_receipt(receipt)
            raise RuntimeError("Keys revoked; local cleanup needs recovery") from None
        revocation_receipt(receipt)
        if not receipt.get("cleanup"):
            raise RuntimeError("Keys revoked; local cleanup needs recovery")


def account_rules(account: str, command: str) -> tuple[str, dict]:
    values = {"AuthenticationMethods": "publickey", "PubkeyAuthentication": "yes",
              "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
              "AuthorizedKeysFile": '"' + str(BASE / "authorized_keys") + '"',
              "AuthorizedKeysCommand": "none", "TrustedUserCAKeys": "none",
              "ForceCommand": command, "DisableForwarding": "yes", "PermitTTY": "no",
              "PermitUserRC": "no"}
    text = "Match User " + account + "\n" + "".join(f"    {key} {value}\n" for key, value in values.items()) + "Match all\n"
    expected = {key.lower(): value.strip('"') for key, value in values.items()}
    # This is a global-only SSH setting: require the safe existing setting, never change
    # unrelated accounts by attempting to set it inside this account's Match section.
    expected["permituserenvironment"] = "no"
    return text, expected


def check_rules(configuration: str, account: str, address: str, expected: dict) -> None:
    actual = platform.validation_setup_sshd(configuration, account, address)
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("Existing SSH rules conflict with the validation account restrictions")


def build_plan(value: dict) -> dict:
    mode = value.get("mode") if isinstance(value, dict) else None
    if mode == "host-key":
        return build_key_plan(value)
    fields = COMMON | (HOST if mode == "host" else GUEST if mode == "guest" else set())
    if mode not in {"host", "guest"} or set(value) != fields or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Invalid Mac setup plan")
    if not isinstance(value["account"], str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value["account"]):
        raise ValueError("Invalid dedicated account")
    if BASE.exists() or BASE.is_symlink():
        raise ValueError("Validation is already installed; this installer never replaces an existing installation")
    protected(BASE.parent, directory=True)
    python = canonical(value["python"])
    protected(python)
    if not os.access(python, os.X_OK):
        raise ValueError("The prepared Python interpreter is not executable")
    platform.validation_setup_python(python)
    files = code_files()
    if source_digest(files) != value["source_sha256"]:
        raise ValueError("Reviewed source identity changed")
    account = platform.validation_setup_account(value["account"], guest=mode == "guest")
    writes = {BASE / "code" / name: (data, 0o644) for name, data in files.items()}
    plan = {"mode": mode, "account": account, "writes": writes, "copies": [], "value": value,
            "source_sha256": value["source_sha256"]}
    if mode == "host":
        template = canonical(value["template"])
        manifest, _, template_digest = platform._validation_template(template)
        if template_digest != value["template_sha256"]:
            raise ValueError("Reviewed template identity changed")
        if shutil.disk_usage(BASE.parent).free < (template / "disk.img").stat().st_size + 2 * 1024**3:
            raise ValueError("Insufficient storage for the validation template and scratch budget")
        for name in ("vm.json", "disk.img", "aux.img", "manifest.json"):
            plan["copies"].append((template / name, BASE / "template" / name))
        network = private_source(value["source_network"])
        address = str(network.network_address)
        command = shlex.join([str(python), "-I", "-B", str(BASE / "code/scripts/validation_remote.py"), "serve"])
        key = public_key(canonical(value["public_key"]))
        writes[BASE / "authorized_keys"] = (f'from="{network}",restrict {key}\n'.encode(), 0o644)
        rules, expected = account_rules(account["name"], command)
        writes[BASE / "sshd.conf"] = (rules.encode(), 0o600)
        protected(SSHD_CONFIG)
        original = SSHD_CONFIG.read_bytes()
        if len(original) > 1024**2 or str(BASE) in original.decode():
            raise ValueError("SSH configuration is oversized or already contains validation setup")
        # Append at EOF so a Match section cannot change following global directives.
        effective = original.decode() + "\nMatch all\n" + rules
        check_rules(effective, account["name"], address, expected)
        updated = original + f'\nMatch all\nInclude "{BASE / "sshd.conf"}"\n'.encode()
        plan.update(ssh_original=original, ssh_updated=updated, ssh_mode=stat.S_IMODE(SSHD_CONFIG.stat().st_mode),
                    ssh_expected=expected, source_address=address, template_digest=template_digest)
        config = {"version": 1, "runner_uid": account["uid"], "state": str(BASE / "state"),
                  "template": str(BASE / "template"), "python": str(python)}
        writes[BASE / "broker.json"] = (json.dumps(config, sort_keys=True).encode(), 0o644)
    else:
        if GUEST_WORK.exists() or GUEST_WORK.is_symlink():
            raise ValueError("The prepared guest contains previous validation run state")
        if GUEST_JOB.exists() or GUEST_JOB.is_symlink():
            raise ValueError("The guest supervisor job already exists")
        protected(GUEST_JOB.parent, directory=True)
        candidate = canonical(value["candidate"])
        fingerprints = value["fingerprints"]
        if not isinstance(fingerprints, dict) or not {"web/package.json", "web/pnpm-lock.yaml"} <= set(fingerprints):
            raise ValueError("Guest setup requires exact dependency fingerprints")
        for name, expected in fingerprints.items():
            path = canonical(str(candidate / name))
            if (not path.is_relative_to(candidate) or not path.is_file() or path.is_symlink()
                    or not isinstance(expected, str) or platform._validation_digest(path) != expected):
                raise ValueError("Reviewed guest dependency identity changed")
        for key in ("store", "browsers"):
            protected(canonical(value[key]), directory=True)
        if not isinstance(value["path"], str) or not value["path"]:
            raise ValueError("Guest setup requires its prepared executable path")
        for folder in value["path"].split(":"):
            protected(canonical(folder), directory=True)
        config = {key: value[key] for key in ("version", "fingerprints", "store", "browsers", "path")}
        config["user"] = account["name"]
        writes[BASE / "guest.json"] = (json.dumps(config, sort_keys=True).encode(), 0o644)
        job = {"Label": "dev.altitude.validation-guest", "RunAtLoad": True, "KeepAlive": False,
               "UserName": "root", "ProcessType": "Background", "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
               "ProgramArguments": [str(python), "-I", "-B", str(BASE / "code/scripts/validation_guest.py")],
               "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}}
        writes[GUEST_JOB] = (plistlib.dumps(job), 0o644)
    return plan


def _write(path: Path, data: bytes, mode: int) -> None:
    output = path.open("xb")
    try:
        with output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(mode)
    except BaseException:
        path.unlink()
        raise


def _replace_ssh(data: bytes, expected: bytes, mode: int) -> None:
    protected(SSHD_CONFIG)
    if SSHD_CONFIG.read_bytes() != expected:
        raise ValueError("SSH configuration changed during setup")
    temporary = SSHD_CONFIG.with_name(".altitude-validation-new")
    created = False
    try:
        _write(temporary, data, mode)
        created = True
        temporary.replace(SSHD_CONFIG)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def apply_plan(plan: dict) -> None:
    if os.geteuid() != 0:
        raise ValueError("Applying Mac validation setup requires its approved administrator action")
    # Repeat the complete preflight immediately before any writes. Captured source bytes and
    # root-owned template inputs then form the installation; no candidate command executes.
    fresh = build_plan(plan["value"])
    if fresh != plan:
        raise ValueError("Setup inputs changed after validation")
    if plan["mode"] == "host-key":
        apply_key_plan(plan)
        return
    created_job = False
    created_base = False
    changed_ssh = False
    previous_umask = os.umask(0o022)
    try:
        BASE.mkdir(mode=0o755)
        created_base = True
        for path, (data, mode) in plan["writes"].items():
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            _write(path, data, mode)
            if path == GUEST_JOB:
                created_job = True
        for source, destination in plan["copies"]:
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            platform.validation_setup_clone(source, destination)
            destination.chmod(0o644)
        if plan["mode"] == "host":
            if platform._validation_template(BASE / "template")[2] != plan["template_digest"]:
                raise ValueError("Installed template differs from the reviewed identity")
            state = BASE / "state"
            state.mkdir(mode=0o700)
            os.chown(state, plan["account"]["uid"], plan["account"]["gid"])
            # Admission starts disabled. A separately authorized native preflight precedes enabling.
            _write(state / "off", b"native preflight required\n", 0o600)
            os.chown(state / "off", plan["account"]["uid"], plan["account"]["gid"])
            _replace_ssh(plan["ssh_updated"], plan["ssh_original"], plan["ssh_mode"])
            changed_ssh = True
            check_rules(SSHD_CONFIG.read_text(), plan["account"]["name"], plan["source_address"], plan["ssh_expected"])
        # Installing a guest plist never starts a job; it becomes eligible at the next guest boot.
    except BaseException:
        if changed_ssh:
            _replace_ssh(plan["ssh_original"], plan["ssh_updated"], plan["ssh_mode"])
        if created_job:
            GUEST_JOB.unlink()
        if created_base:
            shutil.rmtree(BASE)
        raise
    finally:
        os.umask(previous_umask)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--plan", type=Path, help="private JSON setup plan; default validates without writing")
    selection.add_argument("--source-digest", action="store_true", help="print this installer code set's review identity")
    parser.add_argument("--apply", action="store_true", help="apply the complete validated plan as administrator")
    args = parser.parse_args(argv)
    try:
        if args.source_digest:
            if args.apply:
                raise ValueError("Apply requires a setup plan")
            print(source_digest(code_files()))
            return 0
        plan_path = canonical(str(args.plan))
        if not stat.S_ISREG(plan_path.lstat().st_mode):
            raise ValueError("Setup plan must be an ordinary private file")
        info = plan_path.stat()
        owners = {os.getuid(), 0}
        if os.geteuid() == 0 and os.environ.get("SUDO_UID", "").isdigit():
            owners.add(int(os.environ["SUDO_UID"]))
        if info.st_mode & 0o077 or info.st_uid not in owners:
            raise ValueError("Setup plan must be private to its preparing account")
        with plan_path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("Setup plan exceeds its limit")
        plan = build_plan(json.loads(raw))
        if args.apply:
            apply_plan(plan)
            if plan["mode"] == "host-key":
                print("Validation public-key action completed; admission remains disabled.")
            elif plan["mode"] == "host":
                print("Validation setup installed. Account restrictions apply to fresh SSH connections; submissions remain disabled until scoped native preflight and explicit enablement.")
            else:
                print("Guest supervisor installed for the next guest boot; native acceptance remains pending.")
        else:
            print("Validation setup plan checked. No files or services changed; --apply requires scoped administrator authorization.")
        return 0
    except Exception:
        print("Validation setup refused or failed. Check private prerequisites and installed-state recovery before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
