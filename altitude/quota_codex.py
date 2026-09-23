"""Read the Codex seat's account-wide rate limit for quota-aware routing."""
from __future__ import annotations

import json
import math
import os
import selectors
import subprocess
import time
from datetime import datetime, timezone

from . import config, state

_REQUESTS = (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"clientInfo": {"name": "altitude", "title": "altitude", "version": "0"}}},
    {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
)
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def _spawn():
    return subprocess.Popen(
        [config.CODEX_BIN, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _talk(timeout: float) -> list[str]:
    """Send the two requests and return stdout through response 2, ignoring notifications."""
    proc = _spawn()
    lines: list[str] = []
    try:
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("app-server pipes unavailable")
        wire = b"".join((json.dumps(request, separators=(",", ":")) + "\n").encode() for request in _REQUESTS)
        proc.stdin.write(wire)
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        buffered = b""
        total_bytes = 0
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Codex rate-limit read timed out")
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    if buffered:
                        lines.append(buffered.decode(errors="replace"))
                    code = proc.poll()
                    suffix = f" with status {code}" if code is not None else ""
                    raise RuntimeError(f"app-server exited before response{suffix}")
                total_bytes += len(chunk)
                if total_bytes > _MAX_OUTPUT_BYTES:
                    raise RuntimeError(
                        f"Codex app-server output exceeded {_MAX_OUTPUT_BYTES} bytes"
                    )
                buffered += chunk
                while b"\n" in buffered:
                    raw, buffered = buffered.split(b"\n", 1)
                    line = raw.decode(errors="replace")
                    lines.append(line)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(message, dict) and message.get("id") == 2:
                        return lines
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass


def _unknown(why: str) -> dict:
    return {"known": False, "why": why[:300]}


def _epoch_iso(value) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0).isoformat()


def read(timeout: float = 20.0) -> dict:
    """Return the account-wide Codex quota, or an explicit unknown result on every probe failure."""
    try:
        lines = _talk(timeout)
    except FileNotFoundError:
        return _unknown("Codex binary not found")
    except (TimeoutError, subprocess.TimeoutExpired):
        return _unknown("Codex rate-limit read timed out")
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"Codex app-server failed: {exc}")

    response = None
    for line in lines:
        try:
            message = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            response = message
            break
    if response is None:
        return _unknown("Codex rate-limit response missing")
    if response.get("error") is not None:
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return _unknown(f"Codex JSON-RPC error: {message or error}")

    result = response.get("result")
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    primary = limits.get("primary") if isinstance(limits, dict) else None
    used = primary.get("usedPercent") if isinstance(primary, dict) else None
    if used is None:
        return _unknown("Codex response missing rateLimits.primary.usedPercent")
    secondary = limits.get("secondary")
    try:
        primary_used = float(used)
    except (TypeError, ValueError, OverflowError):
        return _unknown(f"Codex response has invalid primary usedPercent: {used!r}")
    if not math.isfinite(primary_used) or not 0 <= primary_used <= 100:
        return _unknown(f"Codex response has invalid primary usedPercent: {used!r}")

    resets_at = primary.get("resetsAt")
    if resets_at is None:
        return _unknown("Codex response missing rateLimits.primary.resetsAt")
    try:
        primary_resets = _epoch_iso(resets_at)
    except (TypeError, ValueError, OverflowError, OSError):
        return _unknown(f"Codex response has invalid primary resetsAt: {resets_at!r}")

    secondary_used = None
    secondary_resets = None
    secondary_window = None
    if isinstance(secondary, dict):
        secondary_value = secondary.get("usedPercent")
        try:
            candidate_used = float(secondary_value)
            if math.isfinite(candidate_used) and 0 <= candidate_used <= 100:
                candidate_resets = _epoch_iso(secondary.get("resetsAt"))
                secondary_used = candidate_used
                secondary_resets = candidate_resets
                secondary_window = secondary.get("windowDurationMins")
        except (TypeError, ValueError, OverflowError, OSError):
            pass

    return {
        "known": True,
        "primary_used": primary_used,
        "primary_resets": primary_resets,
        "primary_window_minutes": primary.get("windowDurationMins"),
        "secondary_used": secondary_used,
        "secondary_resets": secondary_resets,
        "secondary_window_minutes": secondary_window,
        "plan_type": limits.get("planType"),
        "read_at": state.now(),
    }
