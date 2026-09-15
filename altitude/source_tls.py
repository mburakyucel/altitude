"""Preserve an active source service's existing HTTPS identity without restarting it."""
from __future__ import annotations

import hashlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import stat

from . import config, platform, tls
from .installation import atomic

_NAME = "90-altitude-source-tls.conf"
_PROC = Path("/proc")
_HEADER = "# Managed by Altitude source TLS preparation.\n[Service]\nEnvironment="
_PROCESS_ENV = ("ALTITUDE_HOST", "ALTITUDE_PORT", "ALTITUDE_HOME", "ALTITUDE_TLS", "ALTITUDE_TLS_DIR")
_PROPERTIES = ("LoadState", "ActiveState", "SubState", "Type", "MainPID", "FragmentPath", "WorkingDirectory",
               "Environment", "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment", "DropInPaths",
               "ExecStart", "InvocationID", "ExecMainStartTimestampMonotonic", "NeedDaemonReload",
               "RootDirectory", "RootImage")


def _native() -> dict:
    text = platform.run("systemctl", "--user", "show", platform.SERVICE,
                        "--property=" + ",".join(_PROPERTIES))
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _environment(raw: str) -> dict:
    entries = shlex.split(raw)
    result = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not separator or key in result or any(ord(c) < 32 for c in entry):
            raise RuntimeError("Loaded service environment is ambiguous; inspect its unit before preparation")
        result[key] = value
    return result


def _private_file(path: Path) -> bytes:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o022:
        raise RuntimeError("Source unit and override must be regular files owned by this user without shared write access")
    return path.read_bytes()


def _private_directory(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o022:
        raise RuntimeError("Source unit directories must be owned by this user, without symlinks or shared write access")


def _override(directory: Path) -> bytes:
    value = "ALTITUDE_TLS_DIR=" + str(directory)
    if not directory.is_absolute() or any(ord(c) < 32 for c in value):
        raise ValueError("Select an absolute certificate directory without control characters")
    return (_HEADER + json.dumps(value.replace("%", "%%"), ensure_ascii=False) + "\n").encode()


def _existing_override(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    content = _private_file(path)
    try:
        text = content.decode()
        if not text.startswith(_HEADER):
            raise ValueError
        value = json.loads(text[len(_HEADER):]).replace("%%", "%")
        if not value.startswith("ALTITUDE_TLS_DIR=") or _override(Path(value.split("=", 1)[1])) != content:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("The fixed TLS override is foreign or customized; leave it unchanged and inspect it") from None
    return content


def _command(command: list[str], checkout: Path) -> bool:
    expected = [str(checkout / "bin/alt"), "serve"]
    return command == expected or (len(command) == 3 and command[1:] == expected
                                  and re.fullmatch(r"python3(?:\.\d+)?", Path(command[0]).name) is not None)


def _source(native: dict) -> tuple[Path, dict, Path, bytes, bytes | None]:
    unit = platform.service_path()
    if (native.get("LoadState"), native.get("ActiveState"), native.get("SubState"), native.get("Type")) != (
            "loaded", "active", "running", "simple"):
        raise RuntimeError("Preparation requires the active source Altitude user service")
    if native.get("NeedDaemonReload") != "no" or any(native.get(key) for key in (
            "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment", "RootDirectory", "RootImage")):
        raise RuntimeError("Unloaded changes, environment files or service overrides make source settings ambiguous. "
                           "Review pending unit edits; if intended, run systemctl --user daemon-reload, then repeat check-only preparation.")
    if Path(native.get("FragmentPath", "")).absolute() != unit.absolute():
        raise RuntimeError("The active service is not the owned Altitude user-unit file")
    _private_directory(unit.parent)
    unit_bytes = _private_file(unit)
    checkout = Path(native.get("WorkingDirectory", ""))
    if not checkout.is_absolute() or checkout.resolve() != checkout or (checkout / "release.json").exists():
        raise RuntimeError("Preparation requires a source checkout, not an archive installation or indirect working directory")
    match = re.fullmatch(r"\{ path=(.+?) ; argv\[\]=(.+?) ; .* \}", native.get("ExecStart", ""))
    if not match or not _command(shlex.split(match[2]), checkout):
        raise RuntimeError("The source service must directly run its bin/alt serve, optionally with Python 3")
    command = shlex.split(match[2])
    if match[1] != command[0]:
        raise RuntimeError("Loaded source executable and arguments disagree")
    # Inspect tracked source and repository identity; never import executable configuration from that checkout.
    platform.run("git", "-C", str(checkout), "ls-files", "--error-unmatch",
                 "bin/alt", "altitude/server.py", "altitude/config.py")
    from .server import repository_url
    expected = (config.RELEASE or {}).get("repository")
    if expected is None:
        expected = platform.run("git", "-C", str(config.REPO), "remote", "get-url", "origin").strip()
    if expected and "/" in expected and ":" not in expected:
        expected = "https://github.com/" + expected
    actual = platform.run("git", "-C", str(checkout), "remote", "get-url", "origin").strip()
    if not repository_url(expected or "") or repository_url(expected) != repository_url(actual):
        raise RuntimeError("The running source checkout does not identify this Altitude release's repository")
    environment = _environment(native.get("Environment", ""))
    if environment.get("ALTITUDE_SERVICE") != "1" or environment.get("ALTITUDE_TLS", "1") != "1":
        raise RuntimeError("The source service must already run HTTPS with ALTITUDE_SERVICE=1")
    try:
        address = ipaddress.ip_address(environment["ALTITUDE_HOST"])
        port = int(environment["ALTITUDE_PORT"])
    except (KeyError, ValueError):
        raise RuntimeError("Source preparation requires an explicit IP binding and port in the loaded unit") from None
    if address.is_unspecified or not 1 <= port <= 65535:
        raise RuntimeError("Source preparation requires a concrete IP binding and valid port; wildcard bindings are ambiguous")
    path = unit.parent / (unit.name + ".d") / _NAME
    if path.parent.exists() or path.parent.is_symlink():
        _private_directory(path.parent)
    if any(p != path for p in path.parent.glob("*.conf")) or any(
            Path(p) != path for p in shlex.split(native.get("DropInPaths", ""))):
        raise RuntimeError("Foreign source-unit drop-ins require review before TLS preparation")
    previous = _existing_override(path)
    return checkout, environment, path, unit_bytes, previous


def _process(pid: int, checkout: Path, host: str, port: int) -> dict:
    process = _PROC / str(pid)
    try:
        command = [part.decode() for part in (process / "cmdline").read_bytes().split(b"\0") if part]
        if not _command(command, checkout) or (process / "cwd").resolve() != checkout:
            raise RuntimeError("The service PID does not run the selected source checkout")
        environment = {}
        for entry in (process / "environ").read_bytes().split(b"\0"):
            key, _, value = entry.partition(b"=")
            if key in [name.encode() for name in _PROCESS_ENV]:
                environment[key.decode()] = value.decode()
        if environment.get("ALTITUDE_HOST") != host or environment.get("ALTITUDE_PORT") != str(port):
            raise RuntimeError("Running binding differs from loaded settings; inspect source service state before preparation")
        if environment.get("ALTITUDE_TLS", "1") != "1":
            raise RuntimeError("The running source process explicitly disables HTTPS")
        owned = {os.readlink(fd) for fd in (process / "fd").iterdir()}
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        table = _PROC / "net" / ("tcp6" if family == socket.AF_INET6 else "tcp")
        for row in table.read_text().splitlines()[1:]:
            fields = row.split()
            encoded, bound_port = fields[1].split(":")
            raw = bytes.fromhex(encoded)
            raw = b"".join(raw[n:n + 4][::-1] for n in range(0, len(raw), 4))
            address = socket.inet_ntop(family, raw)
            if (fields[3] == "0A" and ipaddress.ip_address(address) == ipaddress.ip_address(host)
                    and int(bound_port, 16) == port and f"socket:[{fields[9]}]" in owned):
                return environment
    except (OSError, ValueError, UnicodeError, IndexError):
        raise RuntimeError("Cannot inspect the source PID and listening socket; process visibility is required") from None
    raise RuntimeError("The active source PID does not own the configured listening address and port")


def _certificate(directory: Path, host: str, port: int) -> str:
    if (directory / tls._MARKER).exists():
        raise RuntimeError("Source TLS preparation accepts existing external certificates, not a managed Altitude CA directory")
    tls._load(directory)
    tls._verify(directory, host)
    # Only the first PEM certificate is the leaf when an external directory includes a chain.
    pem = (directory / "server.crt").read_text().split("-----END CERTIFICATE-----", 1)[0] + "-----END CERTIFICATE-----\n"
    expected = ssl.PEM_cert_to_DER_cert(pem)
    ca = directory / "ca.crt"
    context = ssl.create_default_context(cafile=str(ca) if ca.exists() else None)
    with socket.create_connection((host, port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=host) as secured:
            actual = secured.getpeercert(binary_form=True)
    if actual != expected:
        raise RuntimeError("The selected certificate does not match the source service's served HTTPS identity")
    return hashlib.sha256(actual).hexdigest()


def _observe(directory: Path) -> dict:
    native = _native()
    checkout, environment, path, unit, previous = _source(native)
    try:
        pid = int(native.get("MainPID", "0"))
    except ValueError:
        pid = 0
    if pid <= 1 or not native.get("InvocationID") or not native.get("ExecMainStartTimestampMonotonic"):
        raise RuntimeError("The active source process identity is unavailable")
    host, port = environment["ALTITUDE_HOST"], int(environment["ALTITUDE_PORT"])
    process = _process(pid, checkout, host, port)
    fingerprint = _certificate(directory, host, port)
    if _native() != native:
        raise RuntimeError("Source service changed during inspection; repeat check-only preparation")
    unit_stat = platform.service_path().stat()
    return {"native": native, "environment": environment, "process": process, "path": path,
            "unit_identity": (unit_stat.st_dev, unit_stat.st_ino),
            "unit": unit, "previous": previous, "fingerprint": fingerprint, "pid": pid, "host": host, "port": port}


def _unchanged(before: dict, after: dict) -> bool:
    return (before["unit"] == after["unit"] and before["unit_identity"] == after["unit_identity"]
            and before["process"] == after["process"]
            and before["fingerprint"] == after["fingerprint"]
            and {k: v for k, v in before["native"].items() if k not in ("Environment", "DropInPaths")}
            == {k: v for k, v in after["native"].items() if k not in ("Environment", "DropInPaths")}
            and {k: v for k, v in before["environment"].items() if k != "ALTITUDE_TLS_DIR"}
            == {k: v for k, v in after["environment"].items() if k != "ALTITUDE_TLS_DIR"})


def prepare(directory: Path, *, apply: bool = False) -> dict:
    """Check by default; explicit apply writes one TLS-only drop-in and reloads definitions."""
    if os.environ.get("ALTITUDE_ACTOR") in ("l2", "l3"):
        raise RuntimeError("Source TLS preparation is an operator operation")
    platform.require_supported()
    directory = directory.expanduser().resolve(strict=True)
    before = _observe(directory)
    content = _override(directory)
    path = before["path"]
    result = {"applied": False, "override_path": str(path), "override": content.decode(), "host": before["host"],
              "port": before["port"], "pid": before["pid"], "certificate_sha256": before["fingerprint"],
              "loaded_tls_dir": before["environment"].get("ALTITUDE_TLS_DIR"), "verified": True}
    if not apply:
        return result
    # Serialize this operator operation on the existing unit inode: check-only creates no lock artifact.
    with platform.service_path().open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another source TLS preparation is running; retry after it finishes") from None
        details = os.fstat(lock.fileno())
        if (details.st_dev, details.st_ino) != before["unit_identity"]:
            raise RuntimeError("Source unit changed before apply; repeat check-only preparation")
        return _apply(directory, before, content, result)


def _apply(directory: Path, before: dict, content: bytes, result: dict) -> dict:
    path = before["path"]
    if _observe(directory) != before:
        raise RuntimeError("Source service or override changed before apply; repeat check-only preparation")
    if before["previous"] == content and result["loaded_tls_dir"] == str(directory):
        return {**result, "applied": True}
    path.parent.mkdir(mode=0o700, exist_ok=True)
    atomic(path, content.decode())
    try:
        platform.control("reload")
        after = _observe(directory)
        if not _unchanged(before, after) or after["environment"].get("ALTITUDE_TLS_DIR") != str(directory):
            raise RuntimeError("Source process, binding or loaded TLS selection changed unexpectedly")
    except Exception as exc:
        try:
            if _existing_override(path) != content:
                raise RuntimeError("Override changed concurrently; it was left untouched")
            if before["previous"] is None:
                path.unlink()
            else:
                atomic(path, before["previous"].decode())
            platform.control("reload")
            restored = _observe(directory)
            if not _unchanged(before, restored) or restored["environment"] != before["environment"]:
                raise RuntimeError("Restored source settings could not be verified")
        except Exception as recovery:
            raise RuntimeError(f"Source TLS preparation failed; restoration/reload is uncertain: {recovery}") from exc
        raise RuntimeError(f"Source TLS preparation failed; owned override restored and reload verified: {exc}") from exc
    return {**result, "applied": True, "loaded_tls_dir": str(directory)}
