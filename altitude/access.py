"""Who may use Altitude: browsers paired with a one-time code, and this machine's own `alt` CLI.

Everything lives in one private folder beside the TLS material (`~/.config/altitude/access`), outside every
runtime, source and project root, and every sandbox Altitude applies refuses to read it. `machine.key` is the CLI's
credential: only the operator's account can read it, and none of Altitude's sandboxed agents, so being able to read
it is the proof of being the operator on this machine. `devices.json` holds each
paired browser's name, times and the SHA-256 of its random key (never the key itself), plus at most one
pairing code, also hashed. `alt pair` writes a code here directly, so pairing and lockout recovery need no
running browser, no particular bind address and nothing platform-specific. `<engine>.token` is the sign-in
Altitude's runs of that engine use instead of the operator's own; `alt sign-in` stores it.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, state as S

DIR = config.HOME / ".config/altitude/access"
#: Raised when every earlier machine key must be taken as known to an agent: the first start replaces it.
#: 2: Altitude's sandboxes stopped being able to read this folder.
GENERATION = 2
COOKIE = "altitude_device"
KEY_HEADER = "X-Altitude-Key"
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O or 1/I/L to misread
CODE_LENGTH = 8
CODE_MINUTES = 10
CODE_TRIES = 5
# A device's key lives 400 days (the longest cookie browsers keep) and renews at most daily when used.
COOKIE_SECONDS = 400 * 86400
RENEW = timedelta(days=1)


class AccessError(ValueError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@contextmanager
def _locked():
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    with open(DIR / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read() -> dict:
    try:
        record = json.loads((DIR / "devices.json").read_text())
    except FileNotFoundError:
        return {"devices": [], "code": None}
    if not isinstance(record, dict) or not isinstance(record.get("devices"), list):
        raise ValueError("Pairing data is invalid; the file is retained.")
    return record


def _write(record: dict) -> None:
    S.atomic_write(DIR / "devices.json", json.dumps(record, indent=1, sort_keys=True) + "\n")  # mkstemp: 0600


def machine_key() -> str | None:
    """The CLI's credential; altd creates it when it starts, and a confined agent may be unable to read it."""
    try:
        return (DIR / "machine.key").read_text().strip() or None
    except OSError:
        return None


def engine_token(engine: str) -> str | None:
    """The sign-in of Altitude's own runs on an engine that takes one from the operator (`alt sign-in`)."""
    try:
        return (DIR / f"{engine}.token").read_text().strip() or None
    except OSError:
        return None


def engine_token_saved(engine: str) -> float | None:
    """When that sign-in was saved, from which its year runs."""
    try:
        return (DIR / f"{engine}.token").stat().st_mtime
    except OSError:
        return None


def save_engine_token(engine: str, token: str) -> None:
    with _locked():
        S.atomic_write(DIR / f"{engine}.token", token + "\n")  # mkstemp: 0600


def prepare() -> None:
    """Create the private folder and the machine key when altd starts. A key of an earlier generation is replaced,
    with any pairing code in flight, and the jobs running now are noted: they started under sandboxes that could read
    this folder, so `settle` replaces the key once more when they have all ended."""
    with _locked():
        if machine_key() is None:
            _replace_key([])
        elif _generation().get("generation") != GENERATION:
            _replace_key(_jobs())
            record = _read()
            if record.get("code"):
                _write({**record, "code": None})


def settle() -> None:
    """Replace the key once every job noted by the last replacement has ended."""
    if not _generation().get("after"):
        return
    with _locked():
        after = _generation().get("after")
        if after and not set(after) & set(_jobs()):
            _replace_key([])


def _generation() -> dict:
    try:
        record = json.loads((DIR / "key.json").read_text())
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _replace_key(after: list[str]) -> None:
    S.atomic_write(DIR / "machine.key", secrets.token_urlsafe(32) + "\n")
    S.atomic_write(DIR / "key.json", json.dumps({"generation": GENERATION, "after": after}) + "\n")


def _jobs() -> list[str]:
    """The agent jobs running now: task workers, coordinator turns and reviews, whose sandboxes are what changed.
    Long-lived jobs that run no agent (terminal shells, the container's volume holder) do not hold the key back."""
    from . import engines, platform
    return sorted({part for _, groups in platform.process_table().values() for part in groups
                   if engines.agent_unit(part)})


def is_machine(presented: str | None) -> bool:
    key = machine_key()
    return bool(presented and key) and hmac.compare_digest(presented.encode(), key.encode())


def design_pass(project: str, device_id: str, day: int | None = None) -> str:
    """One paired device's read pass for one project's wireframe boards, valid the day it is made and the next
    while that device stays paired. The boards run sandboxed in an opaque origin, so a browser sends no SameSite
    cookie with their own files; the pass rides in the path instead, where the boards' relative imports keep it."""
    if machine_key() is None:
        prepare()
    day = int(time.time() // 86400) if day is None else day
    mac = hmac.new(machine_key().encode(), f"design\0{project}\0{device_id}\0{day}".encode(), "sha256")
    return f"p-{device_id}-{mac.hexdigest()[:32]}"


def voice_owner(device_id: str) -> str:
    """An opaque name for the paired device that opened a host voice recording. A page replaying its recording
    after the host forgot it presents this name, so the replay stays with that device even when the browser
    was paired again meanwhile."""
    if machine_key() is None:
        prepare()
    return hmac.new(machine_key().encode(), f"voice\0{device_id}".encode(), "sha256").hexdigest()[:32]


def design_pass_valid(project: str, presented: str) -> bool:
    device_id = presented[2:].rpartition("-")[0]
    today = int(time.time() // 86400)
    return (presented.startswith("p-") and bool(device_id)
            and any(hmac.compare_digest(presented, design_pass(project, device_id, day)) for day in (today, today - 1))
            and known(device_id))


def issue_code(*, first_browser: bool = False) -> dict | None:
    """A fresh one-time code; manual issuance replaces any earlier one.

    First-install issuance leaves existing devices and an unexpired code intact. Its eligibility
    and write share the redemption lock, so manual issuance or redemption cannot cross them.
    """
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    expires = _now() + timedelta(minutes=CODE_MINUTES)
    with _locked():
        record = _read()
        active = record.get("code")
        if first_browser:
            if record["devices"]:
                return None
            if active is not None:
                if (not isinstance(active, dict) or not isinstance(active.get("hash"), str)
                        or not isinstance(active.get("expires"), str) or not isinstance(active.get("wrong"), int)):
                    raise ValueError("Pairing data is invalid; the file is retained.")
                deadline = datetime.fromisoformat(active["expires"])
                if deadline.tzinfo is None:
                    raise ValueError("Pairing data is invalid; the file is retained.")
                if deadline > _now():
                    return None
        record["code"] = {"hash": _hash(raw), "expires": expires.isoformat(), "wrong": 0}
        _write(record)
    return {"code": f"{raw[:4]}-{raw[4:]}", "expires": expires.isoformat(), "minutes": CODE_MINUTES}


def redeem(code: str, name: str) -> tuple[str, dict]:
    """Pair a device with the active code: its new key and record. Every wrong code counts against the one
    active code, whoever sends it, and the fifth cancels it."""
    typed = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    with _locked():
        record = _read()
        active = record.get("code")
        if not active or datetime.fromisoformat(active["expires"]) <= _now():
            record["code"] = None
            _write(record)
            raise AccessError("This code has expired or was already used. Make a new one.", 410)
        if not hmac.compare_digest(_hash(typed), active["hash"]):
            active["wrong"] += 1
            left = CODE_TRIES - active["wrong"]
            record["code"] = active if left > 0 else None
            _write(record)
            raise AccessError(f"That code is not right. {left} {'try' if left == 1 else 'tries'} left." if left > 0
                              else "Too many wrong codes, so this one is cancelled. Make a new one.", 403)
        key = secrets.token_urlsafe(32)
        stamp = _now().isoformat()
        device = {"id": uuid.uuid4().hex[:12], "name": name, "hash": _hash(key), "paired": stamp, "used": stamp}
        record["code"] = None
        record["devices"].append(device)
        _write(record)
    return key, device


def device(key: str | None) -> dict | None:
    if not key:
        return None
    digest = _hash(key)
    return next((row for row in _read()["devices"] if hmac.compare_digest(row["hash"], digest)), None)


def known(device_id: str) -> bool:
    return any(row["id"] == device_id for row in _read()["devices"])


def renew(device: dict) -> bool:
    """Record use at most daily; True when the browser's cookie should be renewed with it."""
    if _now() - datetime.fromisoformat(device["used"]) < RENEW:
        return False
    device_id = device["id"]
    with _locked():
        record = _read()
        row = next((row for row in record["devices"] if row["id"] == device_id), None)
        if row is None or _now() - datetime.fromisoformat(row["used"]) < RENEW:
            return False
        row["used"] = _now().isoformat()
        _write(record)
    return True


def devices() -> list[dict]:
    return [{key: row[key] for key in ("id", "name", "paired", "used")} for row in _read()["devices"]]


def code_expires() -> str | None:
    """When the one unused pairing code stops working, or None when there is none: `alt doctor` points at
    the installer's private link while it still works, since `alt pair` would replace it."""
    active = _read().get("code")
    if not isinstance(active, dict) or not isinstance(active.get("expires"), str):
        return None
    return active["expires"] if datetime.fromisoformat(active["expires"]) > _now() else None


def revoke(device_id: str) -> None:
    with _locked():
        record = _read()
        kept = [row for row in record["devices"] if row["id"] != device_id]
        if len(kept) == len(record["devices"]):
            raise AccessError("That device is not paired.", 404)
        record["devices"] = kept
        _write(record)


def device_name(user_agent: str, standalone: bool) -> str:
    """What Settings calls a device: its browser and system, read from the browser's own description."""
    ua = user_agent or ""
    system = next((label for token, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                              ("Mac OS X", "Mac"), ("Windows", "Windows"), ("CrOS", "ChromeOS"),
                                              ("Linux", "Linux")) if token in ua), "Unknown system")
    if standalone and system in ("iPhone", "iPad"):
        return f"Home Screen app on {system}"
    browser = next((label for token, label in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("CriOS/", "Chrome"),
                                               ("Chrome/", "Chrome"), ("Safari/", "Safari")) if token in ua), "Browser")
    return f"{browser} on {system}"
