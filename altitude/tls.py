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
    "On the computer running Altitude, use the public ca_cert file reported here, including for localhost. "
    "Any channel may carry ca.crt. Settings > Devices > Set up a device or `alt tls-share` offers it to a "
    "desktop or phone on the configured private network for ten minutes through a setup link and QR code. "
    "Before installing, check that the file holds only this certificate (ca_name) and that its SHA-256 matches "
    "ca_sha256; otherwise delete it. Never transfer ca.key or server.key.",
    "Before trusting a desktop download, inspect ca.crt with a certificate viewer or "
    "openssl x509 -in ca.crt -noout -subject -fingerprint -sha256; compare the full fingerprint with this "
    "trusted terminal or Settings, not just the download page.",
    "Linux Chrome/Chromium: chrome://certificate-manager > Local certificates > Custom > Installed by you > "
    "Trusted Certificates > Import; "
    "older versions use chrome://settings/certificates > Authorities, with website trust. "
    "Firefox: Settings > Privacy & Security > View Certificates > Authorities > Import, trust for websites.",
    "macOS Safari/Chrome: in Keychain Access, select the login keychain and import ca.crt. Open the "
    "certificate, expand Trust and set Secure Sockets Layer (SSL) to Always Trust; close and authenticate "
    "if asked. Firefox can use its separate Authorities import. Restart the browser before verifying.",
    "iPhone/iPad: open the share link in Safari, tap Download the profile and Allow. Then open Settings > "
    "Profile Downloaded; this entry appears after download, and an uninstalled profile expires after eight minutes. "
    "Also check General > VPN & Device Management for profiles. Check that it contains only a Certificate "
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
    from . import platform
    value = host or (config.PUBLIC_HOST if platform.containerized() else config.HOST)
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
    from . import platform
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
            "trust": "unknown", "trust_steps": ([
                "Export only ca.crt with the host container command's certificate action. Before trusting it, "
                "check that the file holds only this certificate (ca_name) and that its SHA-256 matches ca_sha256; "
                "otherwise delete it. Never transfer ca.key or server.key.", *TRUST_STEPS[1:]]
                if platform.containerized() else list(TRUST_STEPS))}


SHARE_MINUTES = 10


def record() -> Path:
    """Where the running service records how to reach it: beside the machine key rather than in the runtime home
    task folders share, since it decides where `alt` sends that key and which CA it trusts."""
    from . import access
    return access.DIR / "service.json"


def publish(found: dict) -> None:
    """The service records, as it starts serving, where it listens and which certificate folder it serves from.
    A later restart with other settings replaces the record, so clients never depend on their own launch
    environment."""
    import json

    from . import state
    state.atomic_write(record(), json.dumps({"pid": os.getpid(), "host": found["host"], "port": found["port"],
                                             "tls": found["tls"], "tls_dir": str(found["tls_dir"]),
                                             "public_host": found.get("public_host", found["host"])}) + "\n")


def service() -> dict:
    """Where the running Altitude service listens and which certificate folder it serves from, as the service
    itself recorded when it started, so every shell and agent reaches the same service whatever its own
    environment says."""
    import json
    from . import platform

    path = record()
    try:
        saved = json.loads(path.read_text())
        found = {"host": str(saved["host"]), "port": int(saved["port"]), "tls": saved["tls"] is True,
                 "tls_dir": Path(saved["tls_dir"]), "pid": int(saved["pid"]),
                 "public_host": str(saved["public_host"] if platform.containerized() else saved["host"])}
    except FileNotFoundError as exc:
        raise TLSFailure(f"The Altitude service has not recorded where it listens ({path}). Start the service, "
                         "then retry.") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TLSFailure(f"Cannot read the Altitude service's record {path}: {exc}.") from exc
    return located(found)


def located(found: dict) -> dict:
    """Service network settings with the name a device opens and the service's URL."""
    from . import platform
    kind, name = _host(found.get("public_host", config.PUBLIC_HOST) if platform.containerized() else found["host"])
    address = f"[{name}]" if kind == "IP" and ":" in name else name
    return {**found, "kind": kind, "name": name,
            "url": f"{'https' if found['tls'] else 'http'}://{address}:{found['port']}"}


def phone_address(found: dict) -> None:
    """Refuse a service a phone cannot open over HTTPS."""
    if not found["tls"]:
        raise TLSFailure("The Altitude service serves plain HTTP, so it has no certificate for a device to trust.")
    if found["name"] == "localhost" or found["kind"] == "IP" and ipaddress.ip_address(found["name"]).is_loopback:
        raise TLSFailure(f"The Altitude service is configured for {found['host']}, which only this computer can "
                         "open. For this computer, run alt doctor and import only its public ca_cert file "
                         "using your browser's certificate settings. Sharing with another device needs the "
                         "service's ALTITUDE_HOST set to its reachable private-network address and a service restart.")


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


def profile(body: bytes, authority: dict) -> bytes:
    """An iPhone/iPad configuration profile whose only payload is the public CA certificate, so the phone
    shows a named install screen. Its identifiers follow the certificate, so installing it again replaces
    the earlier copy."""
    import plistlib
    import uuid

    digest = authority["sha256"].replace(":", "").lower()

    def payload(kind: str) -> dict:
        return {"PayloadIdentifier": f"local.altitude.ca.{digest[:16]}{kind}", "PayloadVersion": 1,
                "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, f"altitude-ca:{digest}{kind}")).upper()}

    certificate = {**payload(".certificate"), "PayloadType": "com.apple.security.root",
                   "PayloadDisplayName": authority["name"], "PayloadCertificateFileName": "ca.crt",
                   "PayloadContent": ssl.PEM_cert_to_DER_cert(body.decode())}
    return plistlib.dumps({**payload(""), "PayloadType": "Configuration", "PayloadDisplayName": authority["name"],
                           "PayloadOrganization": "Altitude", "PayloadContent": [certificate],
                           "PayloadDescription": "Lets this device check that it is talking to your Altitude on "
                                                 "your own network. It holds only Altitude's public certificate."})


def fingerprint_rows(sha256: str) -> list[str]:
    """A SHA-256 fingerprint as four rows of eight byte pairs, the way devices show it."""
    pairs = sha256.split(":")
    return [" ".join(pairs[start:start + 8]) for start in range(0, len(pairs), 8)]


def _guide(authority: dict, service_url: str, minutes: float) -> bytes:
    """Device setup from the share link: identity, public downloads and deliberate trust steps."""
    from html import escape

    name = escape(authority["name"])
    rows = "<br>".join(fingerprint_rows(authority["sha256"]))
    address = escape(service_url)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up this device for Altitude</title>
<style>
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:34rem;padding:1.25rem;color:#1d1d1f;background:#fff}}
h1{{font-size:1.5rem;margin:.25rem 0 .75rem}}h2{{font-size:1.1rem;margin:1.75rem 0 .5rem}}
.check{{border:1px solid #d2d2d7;border-radius:12px;padding:.75rem 1rem;background:#f5f5f7}}
.sha{{font:14px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.02em}}
a.button{{display:block;text-align:center;padding:.8rem 1rem;border-radius:12px;background:#0a66d8;color:#fff;
font-weight:600;text-decoration:none;margin:.5rem 0}}a.button.secondary{{background:#e8e8ed;color:#1d1d1f}}
ol{{padding-left:1.25rem}}li{{margin:.4rem 0}}small{{color:#6e6e73}}a{{color:#0a66d8}}
nav{{display:flex;flex-wrap:wrap;gap:.5rem}}nav a{{padding:.5rem .75rem;border:1px solid #86868b;border-radius:8px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}}h2{{scroll-margin-top:1rem}}
@media (prefers-color-scheme:dark){{body{{background:#000;color:#f5f5f7}}.check{{background:#1c1c1e;border-color:#3a3a3c}}
a{{color:#4da3ff}}a.button.secondary{{background:#2c2c2e;color:#f5f5f7}}small{{color:#98989d}}}}
</style></head><body>
<h1>Set up this device for Altitude</h1>
<p>Trust the public certificate on your computer or phone, then pair its browser. You approve the trust change yourself.</p>
<div class="check"><strong>{name}</strong><br><small>SHA-256</small><div class="sha">{rows}</div>
<small>It must match the full SHA-256 in the trusted Altitude Settings page or terminal that opened sharing.
This HTTP download page alone cannot prove the certificate's identity. If it differs, stop here.</small></div>
<p>Choose your device:</p>
<nav aria-label="Device instructions"><a href="#linux">Linux</a><a href="#macos">macOS</a>
<a href="#ios">iPhone or iPad</a><a href="#android">Android</a></nav>
<h2 id="desktop">Download and check on desktop</h2>
<a class="button secondary" href="/ca.crt">Download the certificate</a>
<p>Save <strong>ca.crt</strong>. Before importing it, open it in a certificate viewer and check that it holds only
one certificate named <strong>{name}</strong>. Compare its full SHA-256 with the trusted Settings page or terminal.
On Linux or macOS, you can read its name and fingerprint in a terminal in the download folder:</p>
<pre>openssl x509 -in ca.crt -noout -subject -fingerprint -sha256</pre>
<p>Also inspect the file in a text editor: it must contain exactly one <strong>BEGIN CERTIFICATE</strong> /
<strong>END CERTIFICATE</strong> block and no other payload. A file checksum is not the certificate fingerprint.</p>
<p>Compare every hexadecimal pair; spaces, colons and letter case do not matter. If it differs or you cannot
inspect it, stop before trusting it. Never transfer <strong>ca.key</strong> or <strong>server.key</strong>.</p>
<h2 id="linux">Linux</h2>
<ol>
<li><a href="#desktop">Download and check the certificate</a>, including on the computer hosting Altitude.</li>
<li><strong>Chrome or Chromium:</strong> open <strong>chrome://certificate-manager</strong> and choose
<strong>Local certificates › Custom › Installed by you › Trusted Certificates › Import</strong>. Select <strong>ca.crt</strong>.
Older versions use <strong>chrome://settings/certificates › Authorities › Import</strong>; enable trust for websites.</li>
<li><strong>Firefox:</strong> open <strong>Settings › Privacy &amp; Security › Certificates › View Certificates › Authorities › Import</strong>.
Select <strong>ca.crt</strong> and enable <strong>Trust this CA to identify websites</strong>.
Firefox on Linux may need its own import even if another browser already trusts the certificate.</li>
<li>Restart the browser, then <a href="#verify">verify HTTPS before pairing</a>.</li>
</ol>
<h2 id="macos">macOS</h2>
<ol>
<li><a href="#desktop">Download and check ca.crt</a>. Use the certificate file, not the iPhone profile.</li>
<li>For <strong>Safari or Chrome</strong>, open <strong>Keychain Access</strong> (search for it with Spotlight),
select the <strong>login</strong> keychain and drag the certificate file into it.</li>
<li>Open the imported certificate, expand <strong>Trust</strong> and set <strong>Secure Sockets Layer (SSL)</strong>
to <strong>Always Trust</strong>. Leave other uses at their defaults. Close the window and authenticate if macOS asks.</li>
<li>For <strong>Firefox</strong>, use the Authorities import described under Linux if it does not use your macOS trust.</li>
<li>Restart the browser, then <a href="#verify">verify HTTPS before pairing</a>.</li>
</ol>
<h2 id="ios">iPhone or iPad</h2>
<p>Open this page in <strong>Safari</strong>, then tap <strong>Download the profile</strong>.</p>
<a class="button" href="/altitude.mobileconfig">Download the profile</a>
<ol>
<li>Tap <strong>Allow</strong>, then <strong>Close</strong>.</li>
<li>Open <strong>Settings › Profile Downloaded</strong> after the download completes. Check that it contains only a certificate named
<strong>{name}</strong> and that <strong>More Details</strong> shows the SHA-256 above. Then tap
<strong>Install</strong> and enter your passcode. If details are unavailable, stop before installing.
If anything differs, tap <strong>Remove</strong>.</li>
<li>Open <strong>Settings › General › About › Certificate Trust Settings</strong> and turn on <strong>{name}</strong>.</li>
<li>Open <a href="{address}">{address}</a> in a new Private tab. It must load with no warning; then pair this phone.</li>
</ol>
<p><strong>No Profile Downloaded?</strong> This shortcut appears only after a profile download.
Check <strong>Settings › General › VPN &amp; Device Management</strong> for profiles, too.
iOS deletes an uninstalled profile after eight minutes. If none is there, return to this page in Safari
and download again. If there is no Allow prompt or the download fails, stop and report what Safari shows.</p>
<h2 id="android">Android</h2>
<a class="button secondary" href="/ca.crt">Download the Android certificate</a>
<ol>
<li>Open <strong>Settings › Security › Encryption &amp; credentials › Install a certificate › CA certificate</strong>
and choose the downloaded file (names vary by device). Compare its name and full SHA-256 with your trusted
Settings page or terminal before trusting it; stop if you cannot check them.
Firefox for Android also needs its third-party CA certificate setting.</li>
<li>Open <a href="{address}">{address}</a> in a new private tab. It must load with no warning; then pair this device.</li>
</ol>
<h2 id="verify">Verify HTTPS before pairing</h2>
<p>Open the exact Altitude address <a href="{address}">{address}</a> in a new private window or tab.
It must load with no certificate warning. Do not bypass a warning: check the address, certificate identity
and trust setting first. Only then run <strong>alt pair</strong> on the computer hosting Altitude and pair this browser.</p>
<p>For everyday use, verify the regular window is warning-free too and pair there; private-window pairing ends when you close it.</p>
<p>A second computer or phone needs the configured private-network address; localhost refers to that device itself.
Keep the original Settings page or terminal open until the download finishes.</p>
<p><small>This page works for {minutes:g} minutes after the QR code appeared, or until it is closed there.</small></p>
</body></html>
""".encode()


class Share:
    """A bounded window that offers the service's public CA to a phone over plain HTTP on its private address:
    the guided page at /, the iPhone profile and the plain certificate; nothing else. Callers first check
    phone_address(found), so the window never opens on a loopback-only service. The channel is
    unauthenticated, so every screen has the phone compare the certificate's SHA-256 with a trusted one
    before installing it. The listener closes at its deadline or on close()."""

    def __init__(self, found: dict, body: bytes, authority: dict, minutes: float = SHARE_MINUTES, sent=None):
        import http.server
        import socket
        import threading
        import time

        self.authority, self.minutes = authority, minutes
        self.closed, self._lock = threading.Event(), threading.Lock()
        self.deadline = time.monotonic() + minutes * 60
        files = {"/": (_guide(authority, found["url"], minutes), "text/html; charset=utf-8", None),
                 "/altitude.mobileconfig": (profile(body, authority), "application/x-apple-aspen-config", "profile"),
                 "/ca.crt": (body, "application/x-x509-ca-cert", "certificate")}
        connections: set[socket.socket] = set()
        self._connections = connections
        share = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 10

            def setup(self) -> None:
                super().setup()
                connections.add(self.connection)

            def finish(self) -> None:
                connections.discard(self.connection)
                super().finish()

            def do_GET(self) -> None:
                found = files.get(self.path)
                if found is None or share.remaining() <= 0:
                    self.send_error(404)
                    return
                content, kind, what = found
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                if self.command == "GET":
                    self.wfile.write(content)
                    if what and sent:
                        sent(f"Sent the {what} to {self.client_address[0]}.")

            do_HEAD = do_GET

            def log_message(self, *args) -> None:
                pass

        class Server(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6 if found["kind"] == "IP" and ":" in found["name"] else socket.AF_INET
            block_on_close = False  # closing ends open connections below instead of waiting for them

        try:
            self._server = Server((found["name"], 0), Handler)
        except OSError as exc:
            raise TLSFailure(f"Cannot listen on {found['name']} for device setup: {exc}.") from exc
        host = f"[{found['name']}]" if self._server.address_family == socket.AF_INET6 else found["name"]
        self.link = f"http://{host}:{self._server.server_address[1]}/"
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        self._timer = threading.Timer(max(0.0, self.deadline - time.monotonic()), self.close)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self) -> float:
        import time
        return 0.0 if self.closed.is_set() else max(0.0, self.deadline - time.monotonic())

    def close(self) -> None:
        """Stop answering and cut any open connection; `closed` is set once the link is gone."""
        import socket

        with self._lock:
            if self.closed.is_set():
                return
            self._timer.cancel()
            self._server.shutdown()
            for connection in list(self._connections):  # a slow or stalled client ends with the link
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._server.server_close()
            self.closed.set()


def share(minutes: float = SHARE_MINUTES, out=print) -> None:
    """`alt tls-share`: open a share window for the running service's proven CA certificate and print its
    QR code and the checks, until it closes on time or on Ctrl-C."""
    from . import platform, qr
    if platform.containerized():
        raise TLSFailure("Export the public CA with the host container command's certificate action; "
                         "this container does not publish a second certificate-sharing port.")

    found = service()
    phone_address(found)
    body = _proven(found)
    authority = identity(found["tls_dir"] / "ca.crt")
    window = Share(found, body, authority, minutes, sent=out)
    try:
        out(f"For the next {minutes:g} minutes, open this link on your desktop or scan it with a phone's camera:\n"
            f"{qr.terminal(window.link)}\n"
            f"  {window.link}\n"
            "The page has Linux, macOS, iPhone/iPad and Android steps. Before installing, check that:\n"
            f"  - it contains only a Certificate, named {authority['name']}\n"
            "  - the downloaded certificate's full SHA-256 matches this trusted terminal:\n"
            + "".join(f"      {row}\n" for row in fingerprint_rows(authority["sha256"])) +
            "If anything differs or cannot be inspected, stop before trusting it. Never transfer private keys.\n"
            f"Trusting it allows: {describe_scope(authority['scope'])}\n"
            f"It expires {authority['expires']}.\n"
            f"iPhone/iPad after Install: Settings > General > About > Certificate Trust Settings > turn on {authority['name']}.\n"
            f"Then open {found['url']} in a new Private tab. It must load with no warning; only then run alt pair.\n"
            "Ctrl-C closes the link sooner.")
        window.closed.wait()
    except KeyboardInterrupt:
        pass
    finally:
        window.close()
    out("The link is closed.")
