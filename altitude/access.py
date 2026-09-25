"""This machine's key: the credential Altitude's own callers present to altd.

It lives in one private folder beside the TLS material (`~/.config/altitude/access`), outside every
runtime, source and project root. Only the operator's account can read `machine.key`, so being able to
read it is the proof of being on this machine as the operator. altd creates it when it starts; the `alt`
CLI and the restart script send it with their requests.
"""
from __future__ import annotations

import fcntl
import os
import secrets
from contextlib import contextmanager

from . import config, state as S

DIR = config.HOME / ".config/altitude/access"
KEY_HEADER = "X-Altitude-Key"


@contextmanager
def _locked():
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    with open(DIR / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def machine_key() -> str | None:
    """The CLI's credential; altd creates it when it starts, and a confined agent may be unable to read it."""
    try:
        return (DIR / "machine.key").read_text().strip() or None
    except OSError:
        return None


def prepare() -> None:
    """Create the private folder and the machine key when altd starts."""
    with _locked():
        if machine_key() is None:
            S.atomic_write(DIR / "machine.key", secrets.token_urlsafe(32) + "\n")
