"""Installation-local HTTPS identity. Trust-store changes remain explicit operator actions."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import tempfile

from . import config

RENEW_SECONDS = 30 * 24 * 60 * 60
_MARKER = ".altitude-managed"


class TLSFailure(ValueError):
    """An HTTPS setup failure with an operator action; never a reason to serve HTTP."""


def _openssl(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["openssl", *map(str, args)], capture_output=True, text=True,
                                timeout=30, check=False)
    except FileNotFoundError as exc:
        raise TLSFailure("HTTPS requires OpenSSL on PATH. Install OpenSSL, then retry alt tls-init.") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TLSFailure(f"OpenSSL could not complete HTTPS validation: {exc}. Fix it and retry.") from exc
    if result.returncode and not allow_failure:
        raise TLSFailure(f"HTTPS certificate operation failed: {result.stderr.strip()[:600]}. "
                         "Check the configured TLS directory; existing certificates are retained.")
    return result


def _host(host: str | None) -> tuple[str, str]:
    value = host or config.HOST
    try:
        return "IP", str(ipaddress.ip_address(value))
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise TLSFailure("HTTPS host must be an IP address or DNS name, without a port or URL.")
        return "DNS", value.lower()


def _private(path: Path) -> None:
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise TLSFailure(f"Missing HTTPS identity file {path}. Restore it; do not replace a trusted CA implicitly.") from exc
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise TLSFailure(f"HTTPS private material {path} must belong to this user and exclude group/other access. "
                         f"Set {'directory mode 700' if path.is_dir() else 'key mode 600'} before retrying.")


def _safe_location(directory: Path) -> None:
    # A trusted signing key in a worker-writable tree lets project code replace the identity.
    roots = [config.ROOT, config.SOURCE, *config.PROJECT_ROOTS]
    if config.PROJECTS_FILE.exists():
        # Read the registry without load_projects(): inspecting TLS must not migrate runtime state.
        import json
        try:
            roots.extend(Path(p["path"]) for p in json.loads(config.PROJECTS_FILE.read_text()).values())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise TLSFailure("Cannot verify registered project paths. Repair the project registry before creating TLS keys.") from exc
    resolved = directory.resolve()
    if any(resolved.is_relative_to(Path(root).expanduser().resolve()) for root in roots):
        raise TLSFailure("Keep generated TLS signing keys outside Altitude runtime, source and project directories. "
                         "Set ALTITUDE_TLS_DIR to a private configuration directory, then retry alt tls-init.")


def _managed(directory: Path) -> bool:
    marker = directory / _MARKER
    if not marker.exists():
        return False
    if marker.read_text() != "1\n":
        raise TLSFailure("Unknown managed HTTPS identity version. Use the application version that created it.")
    _safe_location(directory)
    _private(directory)
    _private(directory / "ca.key")
    key = _openssl("pkey", "-in", directory / "ca.key", "-pubout").stdout
    certificate_key = _openssl("x509", "-in", directory / "ca.crt", "-pubkey", "-noout").stdout
    if key != certificate_key:
        raise TLSFailure("The managed CA certificate and signing key do not match. Restore the original pair; "
                         "replacing a trusted CA requires explicit trust changes on every device.")
    return True


def _verify(directory: Path, host: str | None, *, ignore_time: bool = False, verify_host: bool = True) -> None:
    kind, name = _host(host)
    ca = directory / "ca.crt"
    args = ["verify", "-purpose", "sslserver"]
    if verify_host:
        args.extend(["-verify_ip" if kind == "IP" else "-verify_hostname", name])
    if ca.exists():
        args.extend(["-CAfile", str(ca)])
    if ignore_time:
        args.append("-no_check_time")
    certificate = str(directory / "server.crt")
    _openssl(*args, "-untrusted", certificate, certificate)


def _load(directory: Path, context: ssl.SSLContext | None = None) -> ssl.SSLContext:
    _private(directory / "server.key")
    context = context or ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(directory / "server.crt", directory / "server.key")
    except (OSError, ssl.SSLError) as exc:
        raise TLSFailure(f"Cannot load HTTPS certificate and matching key from {directory}: {exc}. "
                         "Restore the pair or run alt tls-init for a new installation.") from exc
    return context


def _issue(directory: Path, staging: Path, host: str | None) -> Path:
    kind, name = _host(host)
    san = list(dict.fromkeys(["DNS:localhost", "IP:127.0.0.1", "IP:::1", f"{kind}:{name}"]))
    extension = staging / "server.ext"
    extension.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                         "extendedKeyUsage=serverAuth\nsubjectAltName=" + ",".join(san) + "\n")
    request, certificate = staging / "server.csr", staging / "server.crt"
    _openssl("req", "-new", "-key", directory / "server.key", "-subj", "/CN=Altitude", "-out", request)
    _openssl("x509", "-req", "-in", request, "-CA", directory / "ca.crt", "-CAkey", directory / "ca.key",
             "-set_serial", "0x" + secrets.token_hex(16), "-days", "365", "-sha256", "-out", certificate,
             "-extfile", extension)
    return certificate


def _renew(directory: Path, host: str | None) -> ssl.SSLContext:
    try:
        with tempfile.TemporaryDirectory(prefix=".renew-", dir=directory) as temporary:
            staging = Path(temporary)
            certificate = _issue(directory, staging, host)
            # Validate the replacement with the unchanged key before the sole atomic write.
            os.symlink(directory.resolve() / "server.key", staging / "server.key")
            os.symlink(directory.resolve() / "ca.crt", staging / "ca.crt")
            context = _load(staging)
            _verify(staging, host)
            os.replace(certificate, directory / "server.crt")
            return context
    except OSError as exc:
        raise TLSFailure(f"Cannot renew HTTPS in {directory}: {exc}. Existing certificate and key are retained.") from exc


def initialize(host: str | None = None) -> dict:
    """Create one CA and leaf atomically; an existing external identity is never rewritten."""
    directory = config.TLS_DIR
    _host(host)
    try:
        if directory.exists() and any(directory.iterdir()):
            if _managed(directory):
                _load(directory)
                _verify(directory, host, ignore_time=True, verify_host=False)
                try:
                    _verify(directory, host, ignore_time=True)
                except TLSFailure:
                    # An explicit tls-init may replace the leaf's host; the trust identity stays fixed.
                    _renew(directory, host)
            check(host)
            return info(host)
        _safe_location(directory)
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix=".altitude-tls-", dir=directory.parent) as temporary:
            staging = Path(temporary) / "identity"
            staging.mkdir(mode=0o700)
            for name in ("ca", "server"):
                _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", staging / f"{name}.key")
                (staging / f"{name}.key").chmod(0o600)
            _openssl("req", "-x509", "-new", "-key", staging / "ca.key", "-sha256", "-days", "3650",
                     "-out", staging / "ca.crt", "-subj", "/CN=Altitude local CA",
                     "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                     "-addext", "keyUsage=critical,keyCertSign,cRLSign")
            _issue(staging, staging, host)
            _load(staging)
            _verify(staging, host)
            (staging / "server.csr").unlink()
            (staging / "server.ext").unlink()
            (staging / _MARKER).write_text("1\n")
            os.rename(staging, directory)
        return info(host)
    except OSError as exc:
        raise TLSFailure(f"Cannot initialize HTTPS in {directory}: {exc}. Existing identity files are retained.") from exc


def check(host: str | None = None, *, renew: bool = True,
          context: ssl.SSLContext | None = None) -> ssl.SSLContext:
    """Validate HTTPS, renew a managed leaf when due, and load the serving context."""
    directory = config.TLS_DIR
    managed = _managed(directory)
    validated = _load(directory)
    _verify(directory, host, ignore_time=managed and renew)
    if managed and renew and _openssl("x509", "-checkend", str(RENEW_SECONDS), "-noout",
                                     "-in", directory / "server.crt", allow_failure=True).returncode:
        validated = _renew(directory, host)
    _verify(directory, host)
    return _load(directory, context) if context is not None else validated


def info(host: str | None = None) -> dict:
    """Return public identity evidence; local certificate validity does not prove device trust."""
    directory = config.TLS_DIR
    check(host, renew=False)
    ca = directory / "ca.crt"
    details = _openssl("x509", "-noout", "-dates", "-fingerprint", "-sha256",
                       "-in", directory / "server.crt").stdout.strip()
    return {"dir": str(directory), "host": _host(host)[1], "managed": _managed(directory),
            "server_cert": details, "ca_cert": str(ca) if ca.exists() else None,
            "ca_sha256": _openssl("x509", "-noout", "-fingerprint", "-sha256", "-in", ca).stdout.strip()
            if ca.exists() else None, "trust": "unknown",
            "trust_action": "Transfer only ca.crt to each device and explicitly trust it in OS/browser settings. "
                            "Never transfer ca.key or server.key; verify the CA SHA-256 fingerprint."}
