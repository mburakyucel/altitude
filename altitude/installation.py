"""Versioned private application archives and recoverable per-user installation.

This file is also the standalone installer distributed alongside the release archive.
"""
from __future__ import annotations

import argparse
import contextlib
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


VERSION = re.compile(r"v0\.[0-9]+\.[0-9]+(?:-rc\.[1-9][0-9]*)?\Z")
ARCHIVE_LIMIT = 256 * 1024 * 1024
REQUIRED = ("bin/alt", "altitude/__init__.py", "altitude/config.py", "altitude/server.py", "web/dist/index.html",
            "personas/l2.md", "personas/l3.md", "schemas/report.json")


def atomic(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".altitude-")
    try:
        with os.fdopen(fd, "w") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(filename, mode)
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def metadata(root: Path) -> dict:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("Release resources must not be symbolic links")
    data = json.loads((root / "release.json").read_text())
    if not isinstance(data, dict) or not VERSION.fullmatch(data.get("version", "")):
        raise ValueError("Release has no valid private-preview version")
    if not re.fullmatch(r"[0-9a-f]{40}", data.get("commit", "")):
        raise ValueError("Release has no source commit identity")
    files = data.get("files")
    if not isinstance(files, dict) or any(name not in files for name in REQUIRED):
        raise ValueError("Release is missing required application resources")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != {*files, "release.json"}:
        raise ValueError("Release files do not match its manifest")
    for name, digest in files.items():
        parts = PurePosixPath(name)
        if parts.is_absolute() or ".." in parts.parts or "\\" in name:
            raise ValueError("Unsafe release path")
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Release verification failed: {name}")
    return data


def extract(archive: Path, checksum: str, destination: Path) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise ValueError("Supply the release's exact SHA-256 checksum")
    if archive.stat().st_size > ARCHIVE_LIMIT:
        raise ValueError("Application archive exceeds 256 MiB")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum.lower():
        raise ValueError("Archive checksum mismatch; the installed application is unchanged")
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or member.name in names or member.mode & 0o7000):
                raise ValueError("Archive contains an unsafe, duplicate or non-file entry")
            names.add(member.name)
        if sum(member.size for member in members) > ARCHIVE_LIMIT:
            raise ValueError("Application archive exceeds 256 MiB")
        bundle.extractall(destination, filter="data")
    return metadata(destination)


def version_key(version: str) -> tuple:
    """Release order: minor, patch, then any rc before the final release."""
    match = re.fullmatch(r"v0\.([0-9]+)\.([0-9]+)(?:-rc\.([0-9]+))?", version)
    if not match:
        raise ValueError(f"Not a release version: {version}")
    return int(match[1]), int(match[2]), int(match[3]) if match[3] else float("inf")


class _HTTPSRedirects(HTTPRedirectHandler):
    """Every hop of a release download stays on HTTPS, so no plain-HTTP hop can redirect it elsewhere."""

    def redirect_request(self, request, response, code, message, headers, url):
        if not url.startswith("https://"):
            raise ValueError("A release download was redirected away from HTTPS")
        return super().redirect_request(request, response, code, message, headers, url)


def _get(url: str, limit: int) -> bytes:
    """One HTTPS GET with no identifying headers beyond a generic User-Agent."""
    request = Request(url, headers={"User-Agent": "altitude", "Accept": "application/vnd.github+json"})
    with build_opener(_HTTPSRedirects).open(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release download exceeds its size limit")
    return data


def release_repository() -> str:
    """owner/name of the GitHub repository this installed release was built from."""
    from . import config
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", (config.RELEASE or {}).get("repository", ""))
    if not match:
        raise RuntimeError("This installation does not name its GitHub release repository; update with --archive and --sha256")
    return match[1]


def latest_release(repository: str, current: str) -> dict | None:
    """The newest published release this installation follows, with its release notes page; None when there is none.

    A stable installation follows stable releases: GitHub's latest release, never a draft or prerelease. One installed
    from a release candidate also follows newer candidates, read from the thirty newest releases."""
    follows_candidates = "-rc." in current
    path = "releases?per_page=30" if follows_candidates else "releases/latest"
    data = json.loads(_get(f"https://api.github.com/repos/{repository}/{path}", 4 * 1024 * 1024))
    releases = data if follows_candidates else [data]
    if not isinstance(releases, list) or not all(isinstance(release, dict) for release in releases):
        raise ValueError("GitHub's release lookup returned no releases")
    versions = [release["tag_name"] for release in releases
                if isinstance(release.get("tag_name"), str) and VERSION.fullmatch(release["tag_name"])
                and not release.get("draft") and (follows_candidates or "-rc." not in release["tag_name"])]
    if not versions:
        return None
    version = max(versions, key=version_key)
    return {"version": version, "notes": f"https://github.com/{repository}/releases/tag/{version}"}


UPDATE_CHECK_SECONDS = 12 * 3600
UPDATE_RETRY_SECONDS = 3600
UPDATE_STALE_SECONDS = 1800
# The page shows only this next step; the cause stays in the terminal or the update unit's log.
UPDATE_FAILED = "Run alt update in a terminal to see why."


def _update_record() -> tuple[Path, dict]:
    from . import config, state as S
    path = config.ROOT / "update.json"
    return path, S.read_json(path, {})


@contextmanager
def _changing_update_record():
    """The daemon, the app's request and the detached update each change update.json under one lock."""
    from . import state as S
    path, _ = _update_record()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = S.read_json(path, {})
        before = json.dumps(record, sort_keys=True)
        yield record
        if json.dumps(record, sort_keys=True) != before:
            S.write_json(path, record)


def check_for_update(now: float | None = None) -> None:
    """The daemon's release lookup: at startup, then every 12 hours; offline retries after an hour."""
    from . import config, platform
    if platform.containerized() or config.RELEASE is None or config.machine_settings().get("update_check") is False:
        return
    now = time.time() if now is None else now
    _, record = _update_record()
    if now < record.get("next", 0):
        return
    try:
        found = {"latest": latest_release(release_repository(), config.RELEASE["version"]), "checked": now,
                 "next": now + UPDATE_CHECK_SECONDS}
    except (OSError, ValueError, RuntimeError):
        found = {"next": now + UPDATE_RETRY_SECONDS}
    with _changing_update_record() as record:
        record.update(found)


def update_status() -> dict | None:
    """What the app, `alt doctor` and the CLI notice show; None for a source deployment.

    `automatic` is the switch that installs an offered release at a quiet point; `installed` names the release an
    automatic update installed into this running version, so the page can say Altitude updated itself."""
    from . import config, platform
    if platform.containerized():
        return {"current": (config.RELEASE or {}).get("version"), "available": None, "check": False,
                "automatic": False, "automatic_pending": False, "command": None, "checked": None, "attempt": None, "installed": None,
                "managed": "image", "reason": platform.IMAGE_MANAGED}
    if config.RELEASE is None:
        return None
    current = config.RELEASE["version"]
    _, record = _update_record()
    settings = config.machine_settings()
    check = settings.get("update_check") is not False
    latest = record.get("latest") if check else None
    newer = bool(latest) and VERSION.fullmatch(latest.get("version", "")) and version_key(latest["version"]) > version_key(current)
    last = record.get("attempt") or {}
    automatic = check and settings.get("update_automatic") is not False
    attempt = last if last.get("state") in ("running", "failed") and last.get("version") != current else None
    installed = ({"version": current, "notes": f"{config.RELEASE['repository']}/releases/tag/{current}"}
                 if last.get("automatic") and last.get("version") == current and last.get("state") == "succeeded" else None)
    return {"current": current, "available": latest if newer else None, "check": check,
            "automatic": automatic, "automatic_pending": bool(automatic and newer and last.get("state") != "running"
                and latest["version"] not in record.get("automatic_attempts", [])), "command": "alt update",
            "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record["checked"])) if record.get("checked") else None,
            "attempt": attempt, "installed": installed}


def update_running() -> bool:
    """The durable request fences admission until its owner finishes or a lock-protected reconciliation ends it."""
    from . import config, platform
    if platform.containerized() or config.RELEASE is None:
        return False
    return (_update_record()[1].get("attempt") or {}).get("state") == "running"


def _reconcile_update_locked(prefix: Path) -> None:
    """Caller owns install.lock: no live updater can be downloading, activating or recovering here."""
    if (prefix / "pending.json").exists():
        return
    with _changing_update_record() as record:
        attempt = record.get("attempt") or {}
        if attempt.get("state") != "running":
            return
        current = prefix / "current"
        if current.is_symlink() and attempt.get("version") == metadata(_version_path(prefix, os.readlink(current)))["version"]:
            record["attempt"] = {**attempt, "state": "succeeded"}
        elif time.time() - attempt["started"] >= UPDATE_STALE_SECONDS:
            record["attempt"] = {**attempt, "state": "failed", "error": UPDATE_FAILED}


def reconcile_update() -> None:
    from . import config, platform
    if platform.containerized() or config.RELEASE is None:
        return
    if not update_running():
        return
    try:
        with _lock(_prefix()):
            _reconcile_update_locked(_prefix())
    except InstallationBusy:
        pass  # The real transaction, not its age in the UI, owns the fence.


def _require_no_update(prefix: Path) -> None:
    """A detached request owns the interval before its child acquires install.lock."""
    from . import config
    if config.RELEASE is None:
        return
    _reconcile_update_locked(prefix)
    if update_running():
        raise UpdateRefused("Another update or recovery is already running")


def automatic_update() -> str | None:
    """The offered release the daemon installs at its next quiet point; each version is tried once."""
    reconcile_update()
    status = update_status()
    if not status or not status["automatic_pending"]:
        return None
    return status["available"]["version"]


def update_notice() -> str | None:
    """The terminal's once-a-day line about a newer release, read from the daemon's last check; no network."""
    from . import config
    status = update_status()
    marker = config.ROOT / "update-notice"
    if not status or not status["available"] or marker.exists() and time.time() - marker.stat().st_mtime < 86400:
        return None
    try:
        marker.touch()
    except OSError:
        return None
    version = status["available"]["version"]
    when = "installs when Altitude is next idle; alt update installs it now" if status["automatic_pending"] else "run alt update"
    return f"Altitude {version} is available: {when} (notes: {config.RELEASE['repository']}/releases/tag/{version})"


class UpdateRefused(ValueError):
    """A request the page may explain: its message names no path or internal state."""


def request_update(version: str, *, automatic: bool = False) -> dict:
    """The app's Update button, or the daemon's automatic update: the exact newer release on offer, run as
    `alt update --version` in its own unit."""
    from . import platform
    if platform.containerized():
        raise UpdateRefused(platform.IMAGE_MANAGED)
    from . import config
    if config.RELEASE is None:
        raise UpdateRefused("Only an installed release can update from the app")
    ident = uuid.uuid4().hex
    try:
        with _lock(_prefix()):
            _reconcile_update_locked(_prefix())
            with _changing_update_record() as record:
                status = update_status()
                if not status["available"] or status["available"]["version"] != version:
                    raise UpdateRefused("Only the newer release Altitude is showing can be installed from the app")
                last = record.get("attempt") or {}
                if last.get("state") == "running":
                    if last["version"] == version and not automatic:
                        return status
                    raise UpdateRefused("Another update or recovery is already running")
                if (_prefix() / "pending.json").exists():
                    raise UpdateRefused("Another update or recovery is already running")
                if automatic:
                    if not status["automatic"] or version in record.get("automatic_attempts", []):
                        raise UpdateRefused("This release is not waiting for an automatic update")
                    record["automatic_attempts"] = [*record.get("automatic_attempts", []), version]
                record["attempt"] = {"id": ident, "version": version, "state": "running", "started": time.time(),
                                     "automatic": automatic}
        # The durable reservation owns this handoff; release the lock so an immediately
        # scheduled child can acquire it before detach returns.
        saved = json.loads(_settings().read_text())
        platform.detach(f"altitude-update-{ident}",
                        [saved["python"], "-B", str(_prefix() / "current/bin/alt"), "update", "--version", version],
                        {**saved["environment"], "ALTITUDE_CONFIG": str(_settings()), "PYTHONDONTWRITEBYTECODE": "1",
                         "ALTITUDE_UPDATE_ATTEMPT": ident})
    except InstallationBusy as exc:
        raise UpdateRefused("Another update or recovery is already running") from exc
    except (OSError, ValueError, RuntimeError):
        with contextlib.suppress(OSError, ValueError):
            _finish_attempt(ident, "failed")
        raise
    return update_status()


def _finish_attempt(ident: str, state: str) -> None:
    """Only this transaction changes its receipt; details stay in its terminal or job log."""
    with _changing_update_record() as record:
        attempt = record.get("attempt") or {}
        if attempt.get("state") == "running" and attempt.get("id") == ident:
            record["attempt"] = {**attempt, "state": state, **({"error": UPDATE_FAILED} if state == "failed" else {})}


def update(version: str | None = None) -> dict:
    """Install the named release, or the newest one this installation follows, through the same verification and activation."""
    from . import platform
    platform.require_native_application()
    ident = os.environ.get("ALTITUDE_UPDATE_ATTEMPT")
    with _lock(_prefix(), wait=bool(ident)):
        _reconcile_update_locked(_prefix())
        last = _update_record()[1].get("attempt") or {}
        if ident:
            if last.get("id") != ident or last.get("state") != "running" or last.get("version") != version:
                raise UpdateRefused("This update request has expired or already ended")
        else:
            _require_no_update(_prefix())
            ident = uuid.uuid4().hex
        try:
            return _update(version, ident)
        except (OSError, ValueError, RuntimeError):
            _finish_attempt(ident, "failed")
            raise


def _update(version: str | None, ident: str) -> dict:
    from . import config
    repository = release_repository()
    current = config.RELEASE["version"]
    if version is None:
        found = latest_release(repository, current)
        version = found["version"] if found else current
        if version_key(version) <= version_key(current):
            return {"version": current, "updated": False, "detail": f"Altitude {current} is up to date"}
    elif not VERSION.fullmatch(version):
        raise ValueError("Use a published v0.MINOR.PATCH or v0.MINOR.PATCH-rc.N version")
    elif version == current:
        return {"version": current, "updated": False, "detail": f"Altitude {current} is already installed"}
    elif version_key(version) < version_key(current):
        raise ValueError(f"{version} is older than the installed {current}; alt recover restores the previous version")
    if (_prefix() / "pending.json").exists():
        raise RuntimeError("Interrupted activation exists; run alt recover before updating")
    with _changing_update_record() as record:
        if (record.get("attempt") or {}).get("id") != ident:
            record["attempt"] = {"id": ident, "version": version, "state": "running", "started": time.time(),
                                 "automatic": False}
    base = f"https://github.com/{repository}/releases/download/{version}/altitude-{version}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="altitude-update-") as folder:
        archive = Path(folder) / f"altitude-{version}.tar.gz"
        archive.write_bytes(_get(base, ARCHIVE_LIMIT))
        checksum = _get(base + ".sha256", 1024).decode(errors="replace").split()[:1]
        with _prepare_install(archive, checksum[0] if checksum else "", _prefix(), newer=version) as prepared:
            result = _activate(*prepared, newer=version)
    _finish_attempt(ident, "succeeded")
    # Devices already trust this installation, so the summary leaves out its paths and trust steps.
    return {"version": version, "updated": True, "service": result["service"], "url": result["url"],
            "notes": f"https://github.com/{repository}/releases/tag/{version}"}


def _settings() -> Path:
    return Path(os.environ.get("ALTITUDE_CONFIG", Path.home() / ".config/altitude/install.json")).expanduser()


def _prefix() -> Path:
    from . import config
    if config.INSTALL_PREFIX is None:
        raise RuntimeError("This is a source deployment. Install a private release separately; migration is explicit.")
    return config.INSTALL_PREFIX


def _require_saved_environment(saved: dict) -> None:
    from . import config
    current = config.installation_environment()
    critical = ("ALTITUDE_HOME", "ALTITUDE_HOST", "ALTITUDE_PORT", "ALTITUDE_TLS_DIR", "ALTITUDE_ROOTS")
    changed = [key for key in critical if current[key] != saved["environment"][key]]
    if not config.TLS:
        changed.append("ALTITUDE_TLS")
    if changed:
        raise RuntimeError("Lifecycle settings differ from the owned service: " + ", ".join(changed)
                           + ". Remove these shell overrides and retry; saved configuration is retained.")


def _link(prefix: Path, target: str | None) -> None:
    pending = prefix / ".current-next"
    pending.unlink(missing_ok=True)
    if target is None:
        (prefix / "current").unlink(missing_ok=True)
        return
    pending.symlink_to(target, target_is_directory=True)
    pending.replace(prefix / "current")


def _launcher(prefix: Path, saved: dict, settings: Path, entry: str = "current/bin/alt") -> str:
    return (f"#!/bin/sh\nexport ALTITUDE_CONFIG={shlex.quote(str(settings))}\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            f"exec {shlex.quote(saved['python'])} -B {shlex.quote(str(prefix / entry))} \"$@\"\n")


class InstallationBusy(RuntimeError):
    """Another lifecycle process owns the installation transaction."""


@contextmanager
def _lock(prefix: Path, *, wait: bool = False):
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (prefix / "install.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise InstallationBusy("Another installation operation is running") from exc
        yield


def _probe(version: str, *, prefix: Path, timeout: float = 30) -> None:
    from . import config, platform
    expected = json.loads((prefix / "versions" / version / "release.json").read_text())
    ca = config.TLS_DIR / "ca.crt"
    context = ssl.create_default_context(cafile=str(ca) if ca.exists() else None)
    host = config.HOST if config.HOST not in ("0.0.0.0", "::") else "localhost"
    authority = f"[{host}]" if ":" in host else host
    base = f"https://{authority}:{config.PORT}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(base + "/api/health", context=context, timeout=2) as response:
                health = json.load(response)
            with urlopen(base + "/", context=context, timeout=2) as response:
                html = response.read().decode()
            native = platform.status()
            if (health.get("version") == version and health.get("commit") == expected["commit"]
                    and str(health.get("pid")) == native.get("MainPID")
                    and native.get("ActiveState") == "active" and "/assets/" in html):
                return
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("The selected version's HTTPS API and built UI did not become healthy")
        time.sleep(0.25)


def _gh_signed_in() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gh_lacks() -> list[str] | None:
    """The pull request fields `alt land` reads that this GitHub CLI does not offer, or None when it lists none, as a
    failing gh does. Given `--json` alone, its parser lists the fields it offers, with no repository, sign-in or network
    request; distribution packages such as Ubuntu 24.04's gh 2.45 can sign in and open pull requests but lack
    baseRefOid, so landing fails (#350)."""
    from .land import PR_FIELDS
    try:
        listed = subprocess.run(["gh", "pr", "view", "--json"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    output = listed.stdout + listed.stderr
    if "comma-separated fields" not in output:
        return None
    return [field for field in PR_FIELDS.split(",") if field not in output.split()]


#: Why a GitHub CLI lacking `_gh_lacks()` fields cannot serve tasks.
_GH_TOO_OLD = "Agents land pull requests with GitHub CLI 2.72 or newer; this one lacks {}."


def prerequisites() -> list[dict]:
    """What agents need before the first task, in the order First run shows it: each item is met, unmet or optional,
    with the command the operator runs in their own terminal. Nothing here takes a password or token."""
    from . import config, engines, platform
    gh = shutil.which("gh")
    lacks = _gh_lacks() if gh else []
    install = None if platform.containerized() else platform.install_command("gh")
    if not gh:
        github = {"label": "GitHub CLI not installed", "state": "unmet", "command": install,
                  "detail": "Agents push branches and open pull requests through the GitHub CLI. Install it, then sign "
                  "in with gh auth login."}
    elif lacks:
        github = {"label": "GitHub CLI too old", "state": "unmet", "command": install,
                  "detail": _GH_TOO_OLD.format(", ".join(lacks)) + " Install the current release, then check again."}
    else:
        signed = _gh_signed_in()
        github = {"label": "GitHub CLI signed in", "state": "met" if signed else "unmet", "command": "gh auth login",
                  "detail": "Agents push branches and open pull requests through the GitHub CLI."}
    if platform.containerized() and (lacks or not gh):
        github["detail"] = "GitHub CLI is bundled in the image. Replace this incomplete image from the host."
    items = [{"key": "github", **github}]
    agents = []
    for engine in config.ENGINES:
        label = config.ENGINE_LABELS[engine]
        if engines.installation(engine)["available"] is False:
            agents.append({"key": engine, "label": f"{label} not installed", "state": "unmet",
                           "detail": f"Install {label} (or set its binary in the service environment), then sign in.",
                           "command": engines.install_command(engine)})
            continue
        signed = engines.sign_in(engine)
        agents.append({"key": engine, "label": f"{label} signed in" if signed["signed_in"] else f"{label} installed",
                       "state": "met" if signed["signed_in"] else "unmet",
                       "detail": None if signed["signed_in"] else f"{label} is installed but not signed in.",
                       "command": None if signed["signed_in"] else signed["command"]})
    if any(agent["state"] == "met" for agent in agents):
        for agent in agents:
            if agent["state"] == "unmet":
                agent.update(state="optional", detail=f"Optional: Altitude works with any one coding agent. {agent['detail']}")
    items += agents
    git = shutil.which("git")
    items.append({"key": "git", "label": "Git installed" if git else "Git not installed", "state": "met" if git else "unmet",
                  "detail": None if git else "Agents work in Git checkouts.", "command": None if git else platform.install_command("git")})
    if not git and platform.containerized():
        items[-1].update(detail="Git is bundled in the image. Replace this incomplete image from the host.", command=None)
    return items


def doctor() -> dict:
    from . import config, engines, platform, tls
    checks = [{"name": "Python", "state": "tested" if sys.version_info >= (3, 12) else "unavailable",
               "detail": sys.version.split()[0]}]
    for name in ("git", "gh", "openssl"):
        binary = shutil.which(name)
        checks.append({"name": name, "state": "configured" if binary else "unavailable",
                       "detail": binary or f"Install {name} and put it on the service PATH"})
    try:
        native = platform.status()
        checks.append({"name": "user service", "state": "tested", "detail": native})
    except RuntimeError as exc:
        checks.append({"name": "user service", "state": "unavailable", "detail": str(exc)})
    if shutil.which("gh"):
        lacks = _gh_lacks()
        checks.append({"name": "GitHub CLI pull request fields",
                       "state": "unknown" if lacks is None else "unavailable" if lacks else "tested",
                       "detail": "gh pr view --json listed no fields; run it to see why." if lacks is None
                       else f"{_GH_TOO_OLD.format(', '.join(lacks))} Install the current release: "
                       f"{platform.install_command('gh')}" if lacks else "Offers every field alt land reads."})
    authenticated = _gh_signed_in()
    checks.append({"name": "GitHub authentication", "state": "tested" if authenticated else "unknown",
                   "detail": "Authentication check passed; repository permissions are checked during project setup."
                   if authenticated else "Run gh auth login, then gh auth status; repository access remains unverified."})
    try:
        identity = tls.info()
        trust = {"state": "unknown", "url": tls.url(),
                 **{key: identity[key] for key in ("ca_cert", "ca_name", "ca_sha256", "ca_expires", "ca_scope",
                                                    "trust_steps")}}
    except (tls.TLSFailure, OSError) as exc:
        trust = {"state": "unavailable", "detail": str(exc)}
    seats = [{"name": config.ENGINE_LABELS[engine], **engines.installation(engine)} for engine in config.ENGINES]
    return {"version": (config.RELEASE or {}).get("version"), "update": update_status(), "checks": checks, "engines": seats,
            "engine_access": "unknown; no provider requests are made", "certificate_trust": trust,
            "optional": "Voice, GPU and telemetry do not gate typing or task delivery."}


def recover(prefix: Path | None = None) -> dict:
    from . import config, platform
    platform.require_native_application()
    prefix = prefix or _prefix()
    _require_saved_environment(json.loads(_settings().read_text()))
    with _lock(prefix), config.restart_lock(exclusive=True) as quiet:
        if not quiet:
            raise RuntimeError("Recovery waits for dispatch, L3 or report verification; retry shortly")
        if not (prefix / "pending.json").exists():
            _require_no_update(prefix)
        result = _recover(prefix)
        if result.get("recovered"):
            attempt = _update_record()[1].get("attempt") or {}
            _finish_attempt(attempt.get("id"), "failed")
        return result


def _recover(prefix: Path) -> dict:
    from . import platform
    receipt = prefix / "pending.json"
    if not receipt.exists():
        return {"recovered": False, "detail": "No interrupted activation"}
    saved = json.loads(receipt.read_text())
    for target in (saved["previous"], saved["candidate"]):
        if target is not None:
            _version_path(prefix, target)
    unit = platform.service_path()
    native = _require_owned_unit()
    if unit.exists() and unit.read_text() not in (saved["service"], saved["candidate_service"]):
        raise RuntimeError("Service ownership changed during activation; inspect it before recovery")
    current = prefix / "current"
    if current.is_symlink() and os.readlink(current) not in (saved["previous"], saved["candidate"]):
        raise RuntimeError("Active version changed during activation; inspect it before recovery")
    if native["LoadState"] != "not-found":
        platform.control("stop")
        if platform.status().get("ActiveState") not in ("inactive", "failed"):
            raise RuntimeError("Service stop is unconfirmed; activation recovery remains pending")
    _link(prefix, saved["previous"])
    if saved["service"] is None:
        if unit.exists():
            platform.control("disable")
        unit.unlink(missing_ok=True)
    else:
        atomic(unit, saved["service"], 0o644)
    platform.control("reload")
    if saved["active"] and saved["previous"]:
        platform.control("start")
        _probe(saved["previous_version"], prefix=prefix)
    if not saved["previous"]:
        launcher = Path.home() / ".local/bin/alt"
        if launcher.exists() and launcher.read_text() == saved["candidate_launcher"]:
            launcher.unlink()
    receipt.unlink()
    return {"recovered": True, "version": saved["previous_version"], "retained": "configuration, TLS and all user data"}


def _require_owned_unit() -> dict:
    from . import platform
    status = platform.status()
    unit = platform.service_path()
    if unit.is_symlink() or (status["LoadState"] == "loaded"
                            and Path(status.get("FragmentPath", "")).absolute() != unit.absolute()):
        raise RuntimeError("The loaded Altitude service belongs to another location; migration must be explicit")
    return status


def _version_path(prefix: Path, target: str) -> Path:
    if (PurePosixPath(target).parts != ("versions", Path(target).name)
            or not VERSION.fullmatch(Path(target).name)
            or (prefix / "versions").is_symlink() or (prefix / target).is_symlink()):
        raise RuntimeError("The application link does not name an owned installed version")
    return prefix / target


def require_service_owner() -> Path:
    from . import platform
    prefix = _prefix()
    settings = _settings()
    saved = json.loads(settings.read_text())
    _require_saved_environment(saved)
    expected = platform.definition(prefix, Path(saved["python"]), settings, saved["environment"])
    if Path(saved["prefix"]).resolve() != prefix or platform.service_path().read_text() != expected:
        raise RuntimeError("Service ownership does not match this installed application")
    _require_owned_unit()
    return prefix


def service(operation: str) -> dict | str:
    from . import config, platform
    if operation in ("status", "logs"):
        return platform.status() if operation == "status" else platform.logs()
    platform.require_native_application()
    prefix = _prefix()
    require_service_owner()
    with _lock(prefix), config.restart_lock(exclusive=True) as quiet:
        _require_no_update(prefix)
        if not quiet or (prefix / "pending.json").exists():
            raise RuntimeError("Service lifecycle waits for active work or installation recovery")
        platform.control(operation)
        native = platform.status()
        if operation == "start":
            _probe(metadata(_version_path(prefix, os.readlink(prefix / "current")))["version"], prefix=prefix)
        elif native.get("ActiveState") not in ("inactive", "failed"):
            raise RuntimeError("Service stop is unconfirmed; inspect alt service status")
        return native


@contextmanager
def _prepare_install(archive: Path, checksum: str, prefix: Path | None = None, *, newer: str | None = None):
    """Verify and stage an archive. `newer` names the published release an update expects:
    the verified archive must be that version, and newer than the one installed when the lock is held."""
    global __package__
    if __package__ not in (None, ""):
        from . import platform
        platform.require_native_application()
    prefix = (prefix or Path.home() / ".local/share/altitude").expanduser().resolve()
    if sys.version_info < (3, 12):
        raise RuntimeError("Install Python 3.12 or newer before installing Altitude")
    with tempfile.TemporaryDirectory(prefix="altitude-stage-") as folder:
        stage = Path(folder)
        release = extract(archive, checksum, stage)
        if newer is not None and release["version"] != newer:
            raise ValueError(f"The {newer} release contains {release['version']}; the installed application is unchanged")
        # The standalone installer imports only the verified application tree.
        if __package__ in (None, ""):
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(stage))
            __package__ = "altitude"
        from altitude import config, platform
        platform.require_native_application()
        platform.require_supported()
        registered = json.loads(config.PROJECTS_FILE.read_text()) if config.PROJECTS_FILE.exists() else {}
        settings = _settings().resolve()
        if settings.is_relative_to(prefix):
            raise RuntimeError("Installation configuration must be outside the removable application prefix")
        if settings.exists():
            _require_saved_environment(json.loads(settings.read_text()))
        elif prefix.exists() and any(path.name != "install.lock" for path in prefix.iterdir()):
            raise RuntimeError("Initial installation needs an empty application prefix; existing files are retained")
        for protected in (config.ROOT, config.TLS_DIR, *config.project_roots(),
                          *(Path(project["path"]).expanduser() for project in registered.values())):
            if prefix.is_relative_to(protected.resolve()) or protected.resolve().is_relative_to(prefix):
                raise RuntimeError("Installation prefix must be separate from runtime, TLS and project writable roots")
            if settings.is_relative_to(protected.resolve()):
                raise RuntimeError("Installation configuration must be outside runtime, TLS and project writable roots")
        yield stage, release, prefix, settings


def install(archive: Path, checksum: str, prefix: Path | None = None, *, newer: str | None = None) -> dict:
    """Verify an archive before acquiring the shared lifecycle transaction and activating it."""
    with _prepare_install(archive, checksum, prefix, newer=newer) as prepared:
        with _lock(prepared[2]):
            _require_no_update(prepared[2])
            return _activate(*prepared, newer=newer)


def _activate(stage: Path, release: dict, prefix: Path, settings: Path, *, newer: str | None) -> dict:
    """Activate verified contents while the caller owns install.lock, including download for an update."""
    from . import config, platform, tls
    if (prefix / "pending.json").exists():
        raise RuntimeError("Interrupted activation exists; run alt recover before updating")
    current = prefix / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError("The current application path is not an owned version link")
    previous = os.readlink(current) if current.is_symlink() else None
    if previous:
        installed = metadata(_version_path(prefix, previous))["version"]
        if newer is not None and version_key(newer) <= version_key(installed):
            raise ValueError(f"Altitude {installed} is already installed; {newer} is not newer")
    service = platform.service_path()
    previous_service = service.read_text() if service.exists() else None
    native = _require_owned_unit()
    if native["LoadState"] == "loaded" and previous_service is None:
        raise RuntimeError("An existing Altitude service is loaded from another location")
    launcher = Path.home() / ".local/bin/alt"
    if settings.exists():
        saved = json.loads(settings.read_text())
        if Path(saved["prefix"]).resolve() != prefix:
            raise RuntimeError("The configuration belongs to another installation")
    else:
        saved = {"prefix": str(prefix), "python": str(Path(sys.executable).resolve()),
                 "environment": config.installation_environment()}
    unit_text = platform.definition(prefix, Path(saved["python"]), settings, saved["environment"])
    command = _launcher(prefix, saved, settings)
    if previous_service is not None and previous_service != unit_text:
        raise RuntimeError("Existing Altitude service belongs to another installation or was customized; migration must be explicit")
    if launcher.exists() and (launcher.is_symlink() or launcher.read_text() != command):
        raise RuntimeError("Existing alt command belongs to another installation; leave it in place")
    wrappers = {prefix / "launchers" / release["version"] / "alt":
                _launcher(prefix, saved, settings, f"versions/{release['version']}/bin/alt")}
    wrappers.update({prefix / "hooks" / hook: _launcher(prefix, saved, settings, f"current/hooks/{hook}")
                     for hook in ("pre-commit", "pre-push", "pre-merge-commit", "reference-transaction")})
    for path, content in wrappers.items():
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise RuntimeError("An installation wrapper was customized; existing files are retained")
    if not settings.exists():
        atomic(settings, json.dumps(saved, indent=2) + "\n")
    destination = _version_path(prefix, f"versions/{release['version']}")
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        if metadata(destination) != release:
            raise RuntimeError("A version is immutable; this version already has different contents")
    else:
        shutil.copytree(stage, destination)
    for path, content in wrappers.items():
        atomic(path, content, 0o755)
    tls.initialize()
    # This is the same narrow gate used by dispatch, resume, L3 and report verification.
    deadline = time.monotonic() + 60
    while True:
        with config.restart_lock(exclusive=True) as quiet:
            if quiet:
                receipt = {"previous": previous, "candidate": str(destination.relative_to(prefix)),
                           "previous_version": json.loads((current / "release.json").read_text())["version"] if previous else None,
                           "service": previous_service, "candidate_service": unit_text,
                           "candidate_launcher": command, "active": native.get("ActiveState") == "active"}
                atomic(prefix / "pending.json", json.dumps(receipt) + "\n")
                try:
                    _link(prefix, str(destination.relative_to(prefix)))
                    atomic(service, unit_text, 0o644)
                    atomic(launcher, command, 0o755)
                    platform.control("reload")
                    if previous_service is None:
                        platform.control("enable")
                    if previous_service is None or receipt["active"]:
                        platform.control("restart")
                        _probe(release["version"], prefix=prefix)
                except Exception as exc:
                    try:
                        _recover(prefix)
                    except Exception as rollback:
                        raise RuntimeError(f"Activation failed ({exc}); recovery is incomplete ({rollback}). Run alt recover.") from exc
                    raise RuntimeError(f"Activation failed; previous installation restored: {exc}") from exc
                (prefix / "pending.json").unlink()
                break
        if time.monotonic() >= deadline:
            raise RuntimeError("Update staged; activation waits for dispatch, L3 or report verification. Retry this version.")
        time.sleep(0.25)
    return {"version": release["version"], "prefix": str(prefix), "url": tls.url(),
            "service": "running" if previous_service is None or receipt["active"] else "stopped",
            "trust": tls.info(), "retained": "previous versions and all user data", "next": "Follow trust.trust_steps in each desktop or phone browser. Use the public ca_cert file locally, or alt tls-share on a configured private network. Compare the CA name and full fingerprint before trusting it, then verify the exact HTTPS URL without a warning and pair with the code `alt pair` prints."}


def uninstall() -> dict:
    from . import config, platform, state as S
    platform.require_native_application()
    prefix = _prefix()
    _require_saved_environment(json.loads(_settings().read_text()))
    with _lock(prefix), config.restart_lock(exclusive=True) as quiet:
        _require_no_update(prefix)
        if not quiet:
            raise RuntimeError("Uninstall waits for dispatch, L3 or report verification")
        # A stopped daemon does not stop its independent workers. Retain their pinned inputs.
        active = [f"{project}/{task['slug']}" for project in config.load_projects() for task in S.list_tasks(project)
                  if task.get("agent_id") and task.get("state") not in ("done", "rejected")]
        if active:
            raise RuntimeError("Retain the application while tasks still own worker inputs: " + ", ".join(active)
                               + ". Finish or reject them through their task controls before uninstalling.")
        unit = platform.service_path()
        saved = json.loads(_settings().read_text())
        expected = platform.definition(prefix, Path(saved["python"]), _settings(), saved["environment"])
        if unit.exists() and unit.read_text() != expected:
            raise RuntimeError("Service ownership changed; refusing removal")
        _require_owned_unit()
        platform.control("stop")
        if platform.status().get("ActiveState") not in ("inactive", "failed"):
            raise RuntimeError("Service stop is unconfirmed; application files retained")
        platform.control("disable")
        unit.unlink(missing_ok=True)
        platform.control("reload")
        launcher = Path.home() / ".local/bin/alt"
        if launcher.exists() and launcher.read_text() == _launcher(prefix, saved, _settings()):
            launcher.unlink()
        # Registered repositories can still reference hooks in these versions. Never strand them.
        retained = bool(config.load_projects())
        if not retained:
            shutil.rmtree(prefix / "versions")
            shutil.rmtree(prefix / "launchers")
            shutil.rmtree(prefix / "hooks")
            (prefix / "current").unlink()
        return {"uninstalled": True, "application_retained_for_project_hooks": retained,
                "retained": "configuration, certificate trust, histories, provider sessions and project worktrees"}


def main() -> None:
    global __package__
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archive", type=Path)
    mode.add_argument("--recover", action="store_true")
    parser.add_argument("--sha256")
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--prepare-source-tls", type=Path, metavar="DIRECTORY",
                        help="verify the active source deployment's existing TLS; do not install")
    parser.add_argument("--apply", action="store_true", help="apply the verified source TLS service override")
    args = parser.parse_args()
    if os.environ.get("ALTITUDE_ACTOR") in ("l2", "l3"):
        parser.error("Application installation is an operator operation")
    if (args.prepare_source_tls and (args.recover or args.prefix)) or (args.apply and not args.prepare_source_tls):
        parser.error("--prepare-source-tls uses --archive/--sha256 without --prefix; --apply requires it")
    try:
        if args.recover:
            prefix = (args.prefix or Path.home() / ".local/share/altitude").expanduser().resolve()
            pending = json.loads((prefix / "pending.json").read_text())
            root = _version_path(prefix, pending["previous"] or pending["candidate"])
            metadata(root)
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(root))
            __package__ = "altitude"
            result = recover(prefix)
        else:
            if not args.sha256:
                parser.error("--archive requires --sha256 from the release")
            if args.prepare_source_tls:
                with tempfile.TemporaryDirectory(prefix="altitude-tls-review-") as folder:
                    stage = Path(folder)
                    extract(args.archive, args.sha256, stage)
                    if not (stage / "altitude/source_tls.py").is_file():
                        raise ValueError("This archive has no source TLS preparation; obtain a newer reviewed archive")
                    sys.dont_write_bytecode = True
                    sys.path.insert(0, str(stage))
                    from altitude import source_tls
                    result = source_tls.prepare(args.prepare_source_tls, apply=args.apply)
            else:
                result = install(args.archive, args.sha256, args.prefix)
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")


if __name__ == "__main__":
    main()
