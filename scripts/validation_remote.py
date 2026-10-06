#!/usr/bin/env python3
"""Setup-installed SSH forced command for one offline Mac validation executor.

The installed config, Python and source are administrator-owned. No SSH request chooses a
host command, configuration path, worker executable, template or state directory.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import stat
import struct
import sys

CONFIG = Path("/Library/Application Support/AltitudeValidation/broker.json")
SOURCE = Path(__file__).absolute().parent.parent
FIELDS = {"version", "runner_uid", "state", "template", "python"}
ERROR = "Mac validation configuration, request or executor is unavailable"


def protected_path(path: Path, *, directory: bool = False) -> None:
    """Validate before importing installed application code, not after executing it."""
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Noncanonical setup path")
    for entry in (path, *path.parents):
        info = entry.lstat()
        expected = stat.S_ISDIR if directory or entry != path else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Replaceable setup path")


def load_config(path: Path = CONFIG) -> dict:
    protected_path(path)
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Setup configuration exceeds its limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Invalid setup configuration")
    uid = value["runner_uid"]
    if type(uid) is not int or uid < 500 or os.getuid() != uid or os.geteuid() != uid:
        raise ValueError("Unexpected runner account")
    for key in ("state", "template", "python"):
        if not isinstance(value[key], str) or "\0" in value[key]:
            raise ValueError("Invalid setup path")
        item = Path(value[key])
        if not item.is_absolute() or item.resolve() != item:
            raise ValueError("Noncanonical setup path")
    python = Path(value["python"])
    protected_path(python)
    if Path(sys.executable).resolve() != python:
        raise ValueError("Unexpected interpreter")
    protected_path(Path(value["template"]), directory=True)
    state = Path(value["state"])
    protected_path(state.parent, directory=True)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Validation state must be a private precreated runner directory")
    return value


def runtime(config: dict):
    if not sys.flags.isolated:
        raise ValueError("Isolated interpreter required")
    # These are the complete application imports in the broker executor. Their ancestor
    # checks also protect the source root; the submitted checkout is never on sys.path.
    for relative in ("scripts/validation_remote.py", "altitude/__init__.py", "altitude/platform.py",
                     "altitude/validation_payload.py", "altitude/validation_remote.py"):
        protected_path(SOURCE / relative)
    # -B suppresses bytecode writes, not reads. Existing cached code must be equally immutable.
    cache = SOURCE / "altitude" / "__pycache__"
    if cache.exists():
        protected_path(cache, directory=True)
        for path in cache.iterdir():
            protected_path(path)
    sys.path.insert(0, str(SOURCE))
    platform = importlib.import_module("altitude.platform")
    broker = importlib.import_module("altitude.validation_remote")
    platform._validation_standard_account()
    if platform.validation_host_identity().get("os") != "macos":
        raise ValueError("Native Mac executor required")
    worker = [config["python"], "-I", "-B", str(SOURCE / "scripts" / "validation_remote.py"), "work"]
    return platform, broker.Broker(Path(config["state"]), Path(config["template"]), worker)


def unavailable(*, dispatched: bool) -> dict:
    value = {"status": "unavailable", "error": ERROR}
    if not dispatched:
        value["accepted"] = False
    return value


def main(argv: list[str] | None = None, *, source=None, output=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    source = sys.stdin.buffer if source is None else source
    output = sys.stdout.buffer if output is None else output
    external = argv == ["serve"]
    revoke = argv == ["revoke"]
    dispatched = False
    platform = None
    response = None
    try:
        if external:
            if os.environ.get("SSH_ORIGINAL_COMMAND") != "altitude-validation-v1":
                raise ValueError("Unsupported forced command")
        elif ("SSH_ORIGINAL_COMMAND" in os.environ or not revoke and
              (len(argv) != 2 or argv[0] != "work" or len(argv[1]) != 32
               or any(c not in "0123456789abcdef" for c in argv[1]))):
            raise ValueError("Unsupported internal command")
        config = load_config()
        platform, broker = runtime(config)
        if revoke:
            response = broker.revoke()
        elif not external:
            broker.work(argv[1])
            return 0
        else:
            request = platform.validation_frame_read(source)
            if source.read(1):
                raise ValueError("Trailing protocol data")
            # An exception after dispatch may follow admission; it cannot claim no work started.
            dispatched = True
            response = broker.dispatch(request)
    except Exception:
        # Never serialize native errors: they can contain private paths or endpoint details.
        response = unavailable(dispatched=dispatched)
        if not external:
            return 1
    try:
        if platform is None:
            # Refusal before trusted imports still speaks the same fixed framing format.
            raw = json.dumps(response, separators=(",", ":")).encode()
            frame = struct.pack("!Q", len(raw)) + raw
        else:
            frame = platform.validation_frame_encode(response)
        output.write(frame)
        output.flush()
        return 0
    except (OSError, ValueError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
