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
# A new CA vouches only for loopback, private-network addresses and private names, so trusting it on a
# device can never let its key impersonate a public website.
_PRIVATE = ("DNS:localhost", "DNS:local", "DNS:internal", "DNS:home.arpa",
            "IP:127.0.0.0/255.0.0.0", "IP:10.0.0.0/255.0.0.0", "IP:172.16.0.0/255.240.0.0",
            "IP:192.168.0.0/255.255.0.0", "IP:100.64.0.0/255.192.0.0",
            "IP:::1/ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "IP:fc00::/fe00::")
TRUST_STEPS = (
    "Any channel may carry ca.crt, and `alt tls-share` offers it to a phone on this network for ten minutes. "
    "Before installing, check that the file holds only this certificate (ca_name) and that its SHA-256 matches "
    "ca_sha256; otherwise delete it. Never transfer ca.key or server.key.",
    "Linux Chrome/Chromium: chrome://certificate-manager, import ca.crt as a trusted website authority. "
    "Firefox: Settings > Privacy & Security > View Certificates > Authorities > Import, trust for websites.",
    "Mac: open ca.crt in Keychain Access, then set Trust > When using this certificate > Always Trust.",
    "iPhone/iPad: open ca.crt, then in Settings > Profile Downloaded check that it contains only a Certificate "
    "named ca_name and that More Details shows its SHA-256 before tapping Install. Then turn it on under "
    "General > About > Certificate Trust Settings.",
    "Android: Settings > Security > Encryption & credentials > Install a certificate > CA certificate. "
    "Firefox for Android also needs its third-party CA certificate setting.",
    "Open the HTTPS URL in a new private window on each device; trust is confirmed only when it loads without "
    "a warning. Pair the device after that.",
)


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
    if value in ("0.0.0.0", "::"):
        # A wildcard bind is not a name a device can use; devices reach it through localhost.
        return "DNS", "localhost"
    try:
        return "IP", str(ipaddress.ip_address(value))
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise TLSFailure("HTTPS host must be an IP address or DNS name, without a port or URL.")
        return "DNS", value.lower()


def url(host: str | None = None) -> str:
    """The HTTPS address a device opens: the certified name, with an IPv6 literal bracketed."""
    kind, name = _host(host)
    return f"https://{f'[{name}]' if kind == 'IP' and ':' in name else name}:{config.PORT}"


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
    roots = [config.ROOT, config.SOURCE, *config.project_roots()]
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
    try:
        _openssl(*args, "-untrusted", certificate, certificate)
    except TLSFailure as exc:
        if "permitted subtree violation" in str(exc):
            raise TLSFailure(f"This installation's CA covers only loopback, private-network addresses and private "
                             f"names; {name} is outside it. Serve Altitude on a private address or name.") from exc
        raise


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
            kind, name = _host(host)
            permitted = [*_PRIVATE, *([f"DNS:{name}"] if kind == "DNS" else [])]
            _openssl("req", "-x509", "-new", "-key", staging / "ca.key", "-sha256", "-days", "3650",
                     "-out", staging / "ca.crt", "-subj", "/CN=Altitude local CA",
                     "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                     "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                     "-addext", "nameConstraints=critical," + ",".join(f"permitted;{n}" for n in dict.fromkeys(permitted)))
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
    """Validate HTTPS, reissue a managed leaf when due or for a new host, and load the serving context."""
    directory = config.TLS_DIR
    managed = _managed(directory)
    validated = _load(directory)
    if managed and renew:
        _verify(directory, host, ignore_time=True, verify_host=False)
        try:
            _verify(directory, host, ignore_time=True)
            due = _openssl("x509", "-checkend", str(RENEW_SECONDS), "-noout",
                           "-in", directory / "server.crt", allow_failure=True).returncode
        except TLSFailure:
            due = True  # The configured host changed; the CA and every device's trust stay fixed.
        if due:
            validated = _renew(directory, host)
    _verify(directory, host)
    return _load(directory, context) if context is not None else validated


def _subtree(entry: str) -> str:
    """One name-constraint entry, typed, with an address range written with its prefix length."""
    kind, _, value = entry.partition(":")
    if kind == "IP" and "/" in value:
        address, mask = value.lower().split("/", 1)
        try:
            prefix = bin(int(ipaddress.ip_address(mask))).count("1")
            return f"IP:{ipaddress.ip_network(f'{address}/{prefix}', strict=False)}"
        except ValueError:
            pass
    return entry


def _rfc2253(value: str) -> str:
    """An RFC 2253 attribute value as written: `\\,` is the character, `\\C3\\A9` its UTF-8 bytes."""
    data = re.sub(rb"\\([0-9A-Fa-f]{2})|\\(.)", lambda m: bytes.fromhex(m[1].decode()) if m[1] else m[2],
                  value.encode(), flags=re.S)
    return data.decode("utf-8", "replace")


def identity(certificate: Path) -> dict:
    """What a device shows and trusts: the CA's display name, expiry, SHA-256 and typed name constraints,
    read from the certificate itself. `scope` is None when the CA has no constraints."""
    text = _openssl("x509", "-noout", "-subject", "-nameopt", "RFC2253", "-enddate", "-fingerprint", "-sha256",
                    "-text", "-in", certificate).stdout
    subject = re.search(r"^subject=\s*(.*)$", text, re.M).group(1).strip()
    # Attributes end at an unescaped comma or, within a multi-valued name, an unescaped plus.
    common = next((value for key, _, value in (part.partition("=") for part in
                                               re.findall(r"(?:[^,+\\]|\\.)+", subject))
                   if key.strip().upper() == "CN"), None)
    scope, block, indent = None, None, 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("X509v3 Name Constraints"):
            scope, indent = {"permitted": [], "excluded": []}, len(line) - len(line.lstrip())
        elif scope is not None and block != "done":
            if not stripped or len(line) - len(line.lstrip()) <= indent:
                block = "done"
            elif stripped in ("Permitted:", "Excluded:"):
                block = stripped[:-1].lower()
            elif block in scope:
                scope[block].append(_subtree(stripped))
    return {"name": _rfc2253(subject if common is None else common),
            "expires": re.search(r"^notAfter=(.*)$", text, re.M).group(1).strip(),
            "sha256": re.search(r"Fingerprint=([0-9A-F:]+)", text).group(1), "scope": scope}


NO_LIMITS = ("No limits: this CA can vouch for any website, so whoever holds its key could impersonate "
             "any site to a device that trusts it.")


def describe_scope(scope: dict | None) -> str:
    """The authority a device grants by trusting the CA. Constraints limit each name type separately, so a
    type the CA does not restrict is stated as unrestricted."""
    if scope is None:
        return NO_LIMITS
    parts = []
    for kind, limited, anything in (("DNS", "Names under {} and their subdomains", "Any website name"),
                                    ("IP", "addresses in {}", "any IP address")):
        allowed, barred = ([entry.split(":", 1)[1] for entry in scope[block] if entry.startswith(f"{kind}:")]
                           for block in ("permitted", "excluded"))
        parts.append((limited.format(", ".join(allowed)) if allowed else anything)
                     + (f" except {', '.join(barred)}" if barred else ""))
    if parts == ["Any website name", "any IP address"]:
        return NO_LIMITS
    return f"{parts[0]}; {parts[1]}."


def info(host: str | None = None) -> dict:
    """Return public identity evidence; local certificate validity does not prove device trust."""
    directory = config.TLS_DIR
    check(host, renew=False)
    ca = directory / "ca.crt"
    details = _openssl("x509", "-noout", "-dates", "-fingerprint", "-sha256",
                       "-in", directory / "server.crt").stdout.strip()
    authority = identity(ca) if ca.exists() else None
    return {"dir": str(directory), "host": _host(host)[1], "managed": _managed(directory),
            "server_cert": details, "ca_cert": str(ca) if ca.exists() else None,
            "ca_sha256": f"sha256 Fingerprint={authority['sha256']}" if authority else None,
            "ca_name": authority and authority["name"], "ca_expires": authority and authority["expires"],
            "ca_scope": authority and describe_scope(authority["scope"]),
            "trust": "unknown", "trust_steps": list(TRUST_STEPS)}


SHARE_MINUTES = 10


def service() -> dict:
    """Where the running Altitude service listens and which certificate folder it serves from, as the service
    itself started, so every shell reaches the same service. A shell setting that disagrees is refused."""
    from . import platform
    try:
        pid, environment = platform.service_settings()
        found = config.network(environment)
    except (RuntimeError, ValueError) as exc:
        raise TLSFailure(f"Cannot find the running Altitude service: {exc}") from exc
    shell = config.network(os.environ)
    differing = [key for key, name in (("ALTITUDE_HOST", "host"), ("ALTITUDE_PORT", "port"),
                                       ("ALTITUDE_TLS", "tls"), ("ALTITUDE_TLS_DIR", "tls_dir"))
                 if key in os.environ and shell[name] != found[name]]
    if differing:
        raise TLSFailure(f"This shell sets {', '.join(differing)} differently from the running Altitude service. "
                         "Unset them in this shell, then retry.")
    kind, name = _host(found["host"])
    address = f"[{name}]" if kind == "IP" and ":" in name else name
    return {**found, "pid": pid, "kind": kind, "name": name,
            "url": f"{'https' if found['tls'] else 'http'}://{address}:{found['port']}"}


def _phone_address(found: dict) -> None:
    """Refuse a service a phone cannot open over HTTPS."""
    if not found["tls"]:
        raise TLSFailure("The Altitude service serves plain HTTP, so it has no certificate for a phone to trust.")
    if found["name"] == "localhost" or found["kind"] == "IP" and ipaddress.ip_address(found["name"]).is_loopback:
        raise TLSFailure(f"The Altitude service is configured for {found['host']}, which only this computer can "
                         "open. Set the service's ALTITUDE_HOST to the private-network address the phone opens, "
                         "restart the service, then retry.")


def _proven(found: dict) -> bytes:
    """The CA certificate the service proves it serves under: its health, fetched over HTTPS trusting only
    that certificate for the service's name, comes from the service's own process."""
    import json
    import urllib.request

    ca = found["tls_dir"] / "ca.crt"
    try:
        body = ca.read_bytes()
        context = ssl.create_default_context(cadata=body.decode())
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise TLSFailure(f"Cannot read the Altitude service's CA certificate {ca}: {exc}. Run alt doctor.") from exc
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(found["url"] + "/api/health", timeout=10) as response:
            health = json.load(response)
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise TLSFailure(f"The Altitude service at {found['url']} does not prove its identity with the CA "
                             f"certificate {ca}: {reason.verify_message}. Run alt doctor.") from exc
        raise TLSFailure(f"The Altitude service does not answer at {found['url']}: {reason}.") from exc
    if not isinstance(health, dict) or health.get("pid") != found["pid"]:
        raise TLSFailure(f"Another process answers at {found['url']}, not the running Altitude service.")
    return body


def share(minutes: int = SHARE_MINUTES, out=print) -> None:
    """Offer the public CA certificate to a phone on this network over plain HTTP for a few minutes. The
    channel is unauthenticated: the printed steps have the phone check the file's contents and SHA-256
    before installing it, and nothing else is served."""
    import http.server
    import socket
    import threading
    import time

    found = service()
    _phone_address(found)
    body = _proven(found)
    kind, name = found["kind"], found["name"]
    authority = identity(found["tls_dir"] / "ca.crt")
    deadline = time.monotonic() + minutes * 60
    connections: set[socket.socket] = set()

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 10

        def setup(self) -> None:
            super().setup()
            connections.add(self.connection)

        def finish(self) -> None:
            connections.discard(self.connection)
            super().finish()

        def do_GET(self) -> None:
            if self.path != "/ca.crt" or time.monotonic() >= deadline:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(body)
                out(f"Sent the certificate to {self.client_address[0]}.")

        do_HEAD = do_GET

        def log_message(self, *args) -> None:
            pass

    class Server(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6 if kind == "IP" and ":" in name else socket.AF_INET
        block_on_close = False  # expiry closes open connections below instead of waiting for them

    try:
        server = Server((name, 0), Handler)
    except OSError as exc:
        raise TLSFailure(f"Cannot listen on {name} for the phone: {exc}.") from exc
    with server:
        host = f"[{name}]" if server.address_family == socket.AF_INET6 else name
        pairs = authority["sha256"].split(":")
        out(f"For the next {minutes} minutes, on the phone open this link in Safari and tap Allow:\n"
            f"  http://{host}:{server.server_address[1]}/ca.crt\n"
            "Then Settings > Profile Downloaded. Before tapping Install, check that:\n"
            f"  - it contains only a Certificate, named {authority['name']}\n"
            "  - More Details > that certificate shows SHA-256:\n"
            + "".join(f"      {' '.join(pairs[start:start + 8])}\n" for start in range(0, len(pairs), 8)) +
            "If anything differs, tap Remove and stop: someone else answered the link.\n"
            f"Trusting it allows: {describe_scope(authority['scope'])}\n"
            f"It expires {authority['expires']}.\n"
            f"After Install: Settings > General > About > Certificate Trust Settings > turn on {authority['name']}.\n"
            f"Then open {found['url']} in a new Private tab. It must load with no warning; only then run alt pair.\n"
            "Android: install the file under Settings > Security > Encryption & credentials > "
            "Install a certificate > CA certificate.\n"
            "Ctrl-C closes the link sooner.")
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        serving.start()
        try:
            threading.Event().wait(max(0.0, deadline - time.monotonic()))
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            for connection in list(connections):  # a slow or stalled client ends with the link
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    out("The link is closed.")
