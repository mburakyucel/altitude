"""Read the Claude subscription windows without requiring an interactive statusline session."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from urllib import error, request

from . import config, state as S

QUOTA_CLAUDE = "quota-claude.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
_last_refresh_at: float | None = None


def _unknown(why: str) -> dict:
    return {"known": False, "why": why[:300], "read_at": S.now()}


def _access_token(credentials: Path | None = None) -> str:
    path = credentials or (config.HOME / ".claude" / ".credentials.json")
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"Claude credentials not found at {path}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Claude credentials unreadable at {path}: {exc}") from exc
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Claude credentials have no claudeAiOauth.accessToken")
    return token


def _percent(window: object, name: str) -> float | None:
    """Normalize the OAuth API's/statusline's 0..100 percentage fields."""
    if window is None:
        return None
    if not isinstance(window, dict):
        raise RuntimeError(f"Claude usage response has invalid {name} window")
    raw = window.get("used_percentage")
    if raw is None:
        raw = window.get("utilization")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Claude usage response has invalid {name} utilization: {raw!r}") from exc
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise RuntimeError(f"Claude usage response has invalid {name} utilization: {raw!r}")
    return value


def read(*, timeout: float = 20.0, credentials: Path | None = None, url: str = USAGE_URL,
         opener=request.urlopen) -> dict:
    """Return account-wide Claude quota, or an explicit unknown result on every probe failure."""
    try:
        token = _access_token(credentials)
        req = request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "Accept": "application/json",
            "User-Agent": "altitude-quota-reader/1",
        })
        with opener(req, timeout=timeout) as response:
            body = response.read(1024 * 1024)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError("Claude usage response is not an object")
        five = _percent(data.get("five_hour"), "five_hour")
        seven = _percent(data.get("seven_day"), "seven_day")
        if five is None:
            raise RuntimeError("Claude usage response is missing five_hour utilization")
        five_window = data.get("five_hour") or {}
        return {
            "known": True,
            "five_hour": five,
            "seven_day": seven,
            "resets_at": five_window.get("resets_at") if isinstance(five_window, dict) else None,
            "read_at": S.now(),
        }
    except error.HTTPError as exc:
        return _unknown(f"Claude usage endpoint returned HTTP {exc.code}")
    except error.URLError as exc:
        return _unknown(f"Claude usage endpoint failed: {exc.reason}")
    except (OSError, ValueError, RuntimeError) as exc:
        return _unknown(str(exc))


def refresh() -> dict:
    """Read and atomically persist Claude quota; a failed read is a visible system fault."""
    result = read()
    S.write_json(config.MONITOR_DIR / QUOTA_CLAUDE, result)
    if not result.get("known"):
        from . import improve
        improve.system_fault("quota-claude", result.get("why") or "Claude quota reader failed")
    return result


def refresh_if_due(min_interval: float = 300) -> dict | None:
    """Refresh no more often than ``min_interval`` seconds."""
    global _last_refresh_at
    now = time.monotonic()
    if _last_refresh_at is not None and now - _last_refresh_at < min_interval:
        return None
    _last_refresh_at = now
    return refresh()
