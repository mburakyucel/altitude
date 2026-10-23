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

from . import access, config, digest, state as S

# Apple refuses a token whose contact has no real domain (403 BadJwtToken for altitude@localhost), so the
# default is a well-formed address at a reserved domain: it names no one, and a real one overrides it.
CONTACT = os.environ.get("ALTITUDE_PUSH_CONTACT") or "mailto:altitude@example.com"
# Beside the device keys in the folder task agents can neither read nor write, so no task can sign a wake.
KEY = access.DIR / "vapid.key"
RECORD = config.ROOT / "push.json"
DEVICES = 5  # the operator's own devices; a bounded list keeps replaced phones from accumulating
TTL_SECONDS = 12 * 60 * 60  # a decision older than half a day is read in Altitude, not from a banner
PACES = (0, 15 * 60, 60 * 60)  # the device's choice of the least time between two of its wakes (Settings)
TIMEOUT = 10
_LOCK = threading.Lock()  # the daemon alone writes the record: its timer thread and its request threads
_KEY_LOCK = threading.Lock()  # two first readers must not each generate a key and hand out the loser


class PushFailure(RuntimeError):
    """Push is unavailable; decision alerts in an open page continue unchanged."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _openssl(*args, stdin: bytes | None = None) -> bytes:
    from . import platform
    try:
        result = subprocess.run([platform.openssl(), *map(str, args)], input=stdin, capture_output=True,
                                timeout=30, check=False)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise PushFailure(f"push signing needs OpenSSL: {exc}") from exc
    if result.returncode:
        raise PushFailure(f"push signing failed: {result.stderr.decode(errors='replace').strip()[:300]}")
    return result.stdout


def _key() -> Path:
    """The signing key stays on this machine; the push service only ever sees its public half."""
    path = KEY
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


def remove_legacy_key(log) -> None:
    """Delete the signing key earlier releases kept in `push/` in Altitude's home, with any key a crash left half made
    beside it. Nothing reads them: a device subscribed with that key subscribes again with the current one the next
    time Altitude opens there. The folder is held open and never followed through a link, so only those files go and
    never the current key, and the folder goes once they leave it empty."""
    removed = False
    try:
        home = os.open(config.ROOT, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        folder = os.open("push", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
        try:
            for name in os.listdir(folder):
                if name == "vapid.key" or re.fullmatch(r"\.[0-9a-f]{32}\.key", name):
                    try:
                        os.unlink(name, dir_fd=folder)
                        removed = True
                    except OSError:  # an entry that will not go keeps none of the others back
                        pass
        finally:
            os.close(folder)
        if removed:
            os.rmdir("push", dir_fd=home)  # fails while anything else shares it
    except OSError:
        pass
    finally:
        os.close(home)
    if removed:
        log("push: removed the signing key an earlier release left in Altitude's home")


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
    """`seen` holds the announced decision keys; `owes` each device's new decision keys no wake has yet reached
    it with; `every` each device's chosen pace in seconds and `woken` when its push service last took a wake."""
    stored = S.read_json(RECORD, {}) or {}
    subscriptions = list(stored.get("subscriptions") or [])

    def kept(field: str) -> dict:
        return {endpoint: value for endpoint, value in (stored.get(field) or {}).items() if endpoint in subscriptions}
    return {"subscriptions": subscriptions, "seen": list(stored.get("seen") or []),
            "owes": kept("owes"), "refused": kept("refused"), "every": kept("every"), "woken": kept("woken")}


def _save(record: dict) -> None:
    S.atomic_write(RECORD, json.dumps(record, indent=2, sort_keys=True) + "\n")
    RECORD.chmod(0o600)  # an endpoint is a capability: whoever holds it can wake that device


def _waiting() -> tuple[list[str], list[str]]:
    """The `alert_key` of every waiting operator question, which the page and the worker use too, and the keys
    among them that may wake a device now: a decision whose task is still moving waits. Reviews, faults and
    stops stay in Needs you without waking anyone."""
    rows = [row for row in digest.queue() if row.get("alert_key")]
    return [row["alert_key"] for row in rows], [row["alert_key"] for row in rows if not row.get("alert_held")]


def _pace(every) -> int:
    if every not in PACES or isinstance(every, bool):
        raise PushFailure(f"How often to alert is one of {', '.join(map(str, PACES))} seconds.")
    return every


def subscribe(endpoint: str, every=0) -> dict:
    """A device registers what waking it takes and how often it may be woken, each time Altitude opens there, and
    learns whether Altitude already held it, which a renewing device does not when its push service ended it, and
    whether its push service refuses Altitude's alerts. Altitude stores the endpoint and nothing else about the
    device."""
    every = _pace(every)
    endpoint = (endpoint or "").strip()
    target = urlparse(endpoint)
    # Altitude posts to this address unattended: only the https endpoint a push service issues qualifies.
    if target.scheme != "https" or not target.netloc:
        raise PushFailure("A push subscription needs the https endpoint the browser was given.")
    with _LOCK:
        record = _record()
        if endpoint in record["subscriptions"]:
            # The same subscription again: what it is owed and what its service last answered still hold.
            others = [known for known in record["subscriptions"] if known != endpoint]
            again = {**record, "subscriptions": [*others, endpoint],
                     "every": {known: pace for known, pace in record["every"].items() if known != endpoint}}
            if every:
                again["every"][endpoint] = every
            if again != record:
                _save(again)
            return {"push": True, "known": True, "refused": record["refused"].get(endpoint)}
        # Already waiting decisions are not news to the first device; a later one must not silence
        # what the devices already subscribed are still owed.
        seen = _waiting()[0] if not record["subscriptions"] else record["seen"]
        kept = [*record["subscriptions"][-(DEVICES - 1):], endpoint]
        # A new subscription starts unrefused and owed nothing: the page subscribing it shows what waits,
        # and what its push service thinks shows on the next send.
        refused = {known: why for known, why in record["refused"].items() if known in kept}
        owes = {known: keys for known, keys in record["owes"].items() if known in kept}
        paces = {known: pace for known, pace in record["every"].items() if known in kept}
        woken = {known: at for known, at in record["woken"].items() if known in kept}
        _save({"subscriptions": kept, "seen": seen, "owes": owes, "refused": refused,
               "every": {**paces, endpoint: every} if every else paces, "woken": woken})
    return {"push": True, "known": False, "refused": None}


def pace(endpoint: str, every) -> dict:
    """A subscribed device changes how often it may be woken; what it is owed stays owed."""
    every = _pace(every)
    with _LOCK:
        record = _record()
        if endpoint not in record["subscriptions"]:
            raise PushFailure("This device is not subscribed to alerts. Turn alerts off and on to subscribe it again.")
        record["every"].pop(endpoint, None)
        if every:
            record["every"][endpoint] = every
        _save(record)
    return {"every": every}


def _drop(record: dict, endpoint: str) -> None:
    record["subscriptions"] = [known for known in record["subscriptions"] if known != endpoint]
    for field in ("owes", "refused", "every", "woken"):
        record[field].pop(endpoint, None)


def forget(endpoint: str) -> dict:
    with _LOCK:
        record = _record()
        _drop(record, endpoint)
        _save(record)
    return {"push": False}


def _ended(status: int, reason: str) -> bool:
    """404 and 410 end a subscription, and so does Apple's 400 VapidPkHashMismatch: a subscription made with
    another key can never be woken with the one Altitude signs with. Its device subscribes again with the
    current key the next time Altitude opens there (web/src/data/alerts.tsx)."""
    return status in (404, 410) or reason == "VapidPkHashMismatch"


def notify(log=lambda message: None) -> None:
    """Called each tick: a decision that newly may alert is owed to every subscribed device. Nothing else
    wakes a device: Safari shows something for every push, and WebKit keeps a banner it is asked to close
    within 30 seconds, so a wake that only cleared a banner left a bare one behind. An answered banner closes
    when the page next reads the queue or the next alert wakes the device.

    A device is owed a decision until its push service takes a wake or the decision is settled. One asleep,
    off its network or refused when the decision arrived therefore still wakes on the next tick that gets
    through, even when another device took it, and a fix on either side of a refusal reaches it without
    another step; one whose owed decisions were all settled meanwhile is not woken to announce nothing. A
    device that chose a pace waits until that long has passed since its last wake, so the decisions in between
    arrive in one."""
    with _LOCK:
        record = _record()
        if not record["subscriptions"]:
            return
        keys, alerting = _waiting()
        fresh = [key for key in alerting if key not in record["seen"]]
        # Answered decisions drop out, so the record stays the size of the queue and one that returns alerts again.
        seen = [key for key in keys if key in record["seen"] or key in fresh]
        # A decision held again stays owed and wakes no one until its task rests; a settled one is owed no more.
        owes = {endpoint: owed for endpoint in record["subscriptions"]
                if (owed := [key for key in record["owes"].get(endpoint, []) if key in seen] + fresh)}
        if fresh:
            log(f"push wakes {len(record['subscriptions'])} device(s) for {len(fresh)} new decision(s)")
        if seen != record["seen"] or owes != record["owes"]:
            record["seen"], record["owes"] = seen, owes
            _save(record)
        started = time.time()
        # A wake carries the decisions that may alert now; one held again stays owed until its task rests.
        waking = {endpoint: due for endpoint, owed in owes.items()
                  if (due := [key for key in owed if key in alerting])
                  and started - record["woken"].get(endpoint, 0) >= record["every"].get(endpoint, 0)}
    outcomes = {}
    for endpoint in waking:  # sent outside the lock: a slow push service must not stall a subscription
        try:
            status, reason = _send(endpoint)
        except (PushFailure, OSError) as exc:  # unreachable service or no signing tool: again next tick
            log(f"push to {urlparse(endpoint).netloc} deferred: {exc}")
            continue
        # An ended subscription is dropped below; any other refusal stays owed and is recorded with its reason.
        why = f"{status} {reason}".strip() if status >= 300 else None
        outcomes[endpoint] = (why, _ended(status, reason))
    if not outcomes:
        return
    with _LOCK:
        record = _record()
        for endpoint, (why, ended) in outcomes.items():
            # Turning alerts off and on while the wake was sent clears what this device owed: the outcome
            # belongs to the subscription it was sent for, so the new one starts clean.
            if endpoint not in record["owes"]:
                continue
            host = urlparse(endpoint).netloc
            if ended:
                _drop(record, endpoint)
                log(f"push to {host} ended with {why}: dropped")
                continue
            if why is None:
                record["owes"][endpoint] = [key for key in record["owes"][endpoint] if key not in waking[endpoint]]
                record["woken"][endpoint] = started
            if record["refused"].get(endpoint) == why:
                continue
            # Once per change, with its reason: a refusal logged every tick without one hid its cause.
            log(f"push to {host} refused with {why}" if why else f"push to {host} delivered again")
            if why:
                record["refused"][endpoint] = why
            else:
                record["refused"].pop(endpoint, None)
        record["owes"] = {endpoint: owed for endpoint, owed in record["owes"].items() if owed}
        _save(record)
