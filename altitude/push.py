"""Web push for decision alerts, so a closed phone still learns a decision is waiting (issue #221).

The push carries no payload: the push service is asked to wake the device and is told nothing else.
The service worker then reads the waiting decisions from Altitude itself and names the project and
task; a phone that cannot reach Altitude shows a decision is waiting and nothing more, by the
operator's 2026-09-17 decision. Signing the request needs only the OpenSSL that HTTPS already requires, so Altitude
carries no encryption library and no conversation text ever leaves the machine.

Push is a capability: without OpenSSL, without a subscribed device or without outbound reach it is
simply unavailable, and alerts in an open Altitude page are unaffected.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from . import config, digest, state as S

# Apple refuses a token whose contact has no real domain (403 BadJwtToken for altitude@localhost), so the
# default is a well-formed address at a reserved domain: it names no one, and a real one overrides it.
CONTACT = os.environ.get("ALTITUDE_PUSH_CONTACT") or "mailto:altitude@example.com"
KEY_DIR = config.ROOT / "push"
RECORD = config.ROOT / "push.json"
DEVICES = 5  # the operator's own devices; a bounded list keeps replaced phones from accumulating
TTL_SECONDS = 12 * 60 * 60  # a decision older than half a day is read in Altitude, not from a banner
TIMEOUT = 10
_LOCK = threading.Lock()  # the daemon alone writes the record: its timer thread and its request threads
_KEY_LOCK = threading.Lock()  # two first readers must not each generate a key and hand out the loser


class PushFailure(RuntimeError):
    """Push is unavailable; decision alerts in an open page continue unchanged."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _openssl(*args, stdin: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(["openssl", *map(str, args)], input=stdin, capture_output=True,
                                timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PushFailure(f"push signing needs OpenSSL on PATH: {exc}") from exc
    if result.returncode:
        raise PushFailure(f"push signing failed: {result.stderr.decode(errors='replace').strip()[:300]}")
    return result.stdout


def _key() -> Path:
    """The signing key stays on this machine; the push service only ever sees its public half."""
    path = KEY_DIR / "vapid.key"
    with _KEY_LOCK:
        # A browser binds the key it subscribed with for good, so a second key must never replace it.
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)  # the directory closes the window before the key file is chmodded
            staging = path.parent / f".{uuid.uuid4().hex}.key"
            _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", staging)
            staging.chmod(0o600)
            staging.replace(path)
    return path


def public_key() -> str:
    """The application server key the browser subscribes with: the uncompressed P-256 point."""
    der = _openssl("ec", "-in", _key(), "-pubout", "-outform", "DER")
    return _b64(der[-65:])


def _signature(der: bytes) -> bytes:
    """ES256 wants r and s as 32 bytes each; OpenSSL signs into the DER sequence of two integers."""
    body = der[2:] if der[1] < 0x80 else der[2 + (der[1] & 0x7F):]
    numbers = []
    while body:
        size = body[1]
        numbers.append(int.from_bytes(body[2:2 + size], "big"))
        body = body[2 + size:]
    if len(numbers) != 2:
        raise PushFailure("push signature is not an ECDSA pair")
    return b"".join(number.to_bytes(32, "big") for number in numbers)


def _token(endpoint: str) -> str:
    """A VAPID token proves the push came from this Altitude, and expires within the day."""
    target = urlparse(endpoint)
    claims = {"aud": f"{target.scheme}://{target.netloc}", "exp": int(time.time()) + TTL_SECONDS,
              "sub": CONTACT}
    header = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    signed = f"{header}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    return f"{signed}.{_b64(_signature(_openssl('dgst', '-sha256', '-sign', _key(), stdin=signed.encode())))}"


def _send(endpoint: str) -> tuple[int, str]:
    """One empty POST: no body, so the service carries no word of the decision. A refusal says why."""
    request = urllib.request.Request(endpoint, data=b"", method="POST", headers={
        "TTL": str(TTL_SECONDS), "Urgency": "high", "Content-Length": "0",
        "Authorization": f"vapid t={_token(endpoint)},k={public_key()}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, _reason(exc)


def _reason(refusal: urllib.error.HTTPError) -> str:
    """Apple and Mozilla answer {"reason": …}, Google a line of text. Only a short plain code is kept:
    the reason is logged and shown on the page, and a body echoing an endpoint must not carry it there."""
    try:
        body = refusal.read(2000).decode(errors="replace").strip()
    except OSError:
        return ""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        body = str(parsed.get("reason") or parsed.get("message") or parsed.get("error") or "")
    body = " ".join(body.split())
    return body if re.fullmatch(r"[\w .,'()-]{1,80}", body) else ""


def _record() -> dict:
    """`seen` holds the announced decision keys; `owed` the devices a wake has not yet reached."""
    stored = S.read_json(RECORD, {}) or {}
    subscriptions = list(stored.get("subscriptions") or [])
    return {"subscriptions": subscriptions, "seen": list(stored.get("seen") or []),
            "owed": [endpoint for endpoint in stored.get("owed") or [] if endpoint in subscriptions],
            "refused": {endpoint: why for endpoint, why in (stored.get("refused") or {}).items()
                        if endpoint in subscriptions}}


def _save(record: dict) -> None:
    S.atomic_write(RECORD, json.dumps(record, indent=2, sort_keys=True) + "\n")
    RECORD.chmod(0o600)  # an endpoint is a capability: whoever holds it can wake that device


def _waiting() -> tuple[list[str], list[str]]:
    """The key of every waiting operator question or held review, in the form the page and the worker both
    use, and the keys among them that may wake a device now: a decision whose task is still moving waits.

    Keyed on the decision, not its revision: a block and its escalation publish the same waiting
    decision twice, and the operator is woken for it once."""
    rows = [row for row in digest.queue() if row.get("id") or row["kind"] == "review"]  # not a fault or a stop
    keys = [f"{row['project']}:{row['slug']}:{row.get('group_id') or row.get('id') or 'review:%s' % row['pr']}"
            for row in rows]
    return keys, [key for key, row in zip(keys, rows) if not row.get("alert_held")]


def subscribe(endpoint: str) -> dict:
    """A device registers what waking it takes. Altitude stores the endpoint and nothing about the device."""
    endpoint = (endpoint or "").strip()
    target = urlparse(endpoint)
    # Altitude posts to this address unattended: only the https endpoint a push service issues qualifies.
    if target.scheme != "https" or not target.netloc:
        raise PushFailure("A push subscription needs the https endpoint the browser was given.")
    with _LOCK:
        record = _record()
        kept = [known for known in record["subscriptions"] if known != endpoint]
        # Already waiting decisions are not news to the first device; a later one must not silence
        # what the devices already subscribed are still owed.
        seen = _waiting()[0] if not record["subscriptions"] else record["seen"]
        kept = [*kept[-(DEVICES - 1):], endpoint]
        # A fresh subscription starts unrefused and owed nothing: the page subscribing it shows what waits,
        # and what its push service thinks shows on the next send.
        refused = {known: why for known, why in record["refused"].items() if known in kept and known != endpoint}
        owed = [known for known in record["owed"] if known in kept and known != endpoint]
        _save({"subscriptions": kept, "seen": seen, "owed": owed, "refused": refused})
    return {"push": True}


def forget(endpoint: str) -> dict:
    with _LOCK:
        record = _record()
        record["subscriptions"] = [known for known in record["subscriptions"] if known != endpoint]
        record["refused"].pop(endpoint, None)
        _save(record)
    return {"push": False}


def refused() -> list[dict]:
    """Devices whose push service refuses Altitude's alerts, and why, for the page to say so."""
    return [{"host": urlparse(endpoint).netloc, "reason": why} for endpoint, why in _record()["refused"].items()]


def notify(log=lambda message: None) -> None:
    """Called each tick: a decision that newly may alert, or an announced one that has left the queue,
    owes every subscribed device one wake, so it alerts once or closes the banner it no longer needs.

    A device stays owed until its push service takes a wake. One asleep, off its network or refused when
    the decision arrived therefore still wakes on the next tick that gets through, even when another device
    took it, and a fix on either side of a refusal reaches it without another step."""
    with _LOCK:
        record = _record()
        if not record["subscriptions"]:
            return
        keys, alerting = _waiting()
        fresh = [key for key in alerting if key not in record["seen"]]
        if fresh or any(key not in keys for key in record["seen"]):
            # Answered decisions drop out, so the record stays the size of the queue.
            record["seen"] = [key for key in keys if key in record["seen"] or key in fresh]
            record["owed"] = list(record["subscriptions"])
            _save(record)
        endpoints = list(record["owed"])
        before = dict(record["refused"])  # a subscription renewed while sending starts clean, not refused again
    outcomes = {}
    for endpoint in endpoints:  # sent outside the lock: a slow push service must not stall a subscription
        try:
            status, reason = _send(endpoint)
        except (PushFailure, OSError) as exc:  # unreachable service or no signing tool: again next tick
            log(f"push to {urlparse(endpoint).netloc} deferred: {exc}")
            continue
        if status in (404, 410):  # gone for good; the device subscribes again when it next alerts
            forget(endpoint)
        elif status >= 300:  # still owed, and recorded with its reason below
            outcomes[endpoint] = f"{status} {reason}".strip()
        else:
            outcomes[endpoint] = None
    if not outcomes:
        return
    with _LOCK:
        record = _record()
        record["owed"] = [endpoint for endpoint in record["owed"]
                          if endpoint not in outcomes or outcomes[endpoint] is not None]
        for endpoint, why in outcomes.items():
            now = record["refused"].get(endpoint)
            if endpoint not in record["subscriptions"] or now != before.get(endpoint) or now == why:
                continue
            # Once per change, with its reason: a refusal logged every tick without one hid its cause.
            host = urlparse(endpoint).netloc
            log(f"push to {host} refused with {why}" if why else f"push to {host} delivered again")
            if why:
                record["refused"][endpoint] = why
            else:
                record["refused"].pop(endpoint, None)
        _save(record)
