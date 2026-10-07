"""Passive task accounting. Provider records and their meaning stay behind the engine seam."""
from __future__ import annotations

from datetime import datetime

from . import engines, state as S


COUNTERS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "requests")


def remember(task: dict) -> None:
    """Keep concrete task identities before recovery or resume replaces them (caller holds the lock)."""
    engine, session = task.get("l2_engine"), task.get("session_id")
    if not engine or not session:
        return
    identities = task.setdefault("token_usage_sessions", [])
    identity = next((row for row in identities if row["engine"] == engine and row["session_id"] == session), None)
    if identity is None:
        identity = {"engine": engine, "session_id": session, "attempt": task.get("attempt", 0)}
        identities.append(identity)
    attempts = identity.setdefault("attempts", [identity["attempt"]])
    if task.get("attempt", 0) not in attempts:
        attempts.append(task.get("attempt", 0))


def _sum(rows: list[dict], key: str) -> int | None:
    values = [row[key] for row in rows if isinstance(row.get(key), int)]
    return sum(values) if values else None


def capture(project: str, task: dict, *, final: bool = False) -> None:
    """Refresh the task in place under its existing project lock; telemetry never gates delivery.

    Cursors contain counters/identities only, are private to collection, and move with the task.
    The public snapshot is on status.json, independent of the worker-authored report's spend fields.
    """
    remember(task)
    previous = task.get("token_usage") or {}
    try:
        path = S.task_dir(project, task["slug"]) / "token-usage.json"
        cursors = S.read_json(path, {}) or {}
        identities = task.get("token_usage_sessions", [])
        rows, notes, statuses, helpers, helper_statuses = [], [], [], [], []
        for engine in dict.fromkeys(row["engine"] for row in identities):
            roots = [row for row in identities if row["engine"] == engine]
            result = {}
            for root in roots:
                result = engines.observe_token_usage(engine, root["session_id"], cursors.get(engine),
                                                     job_root=path.parent / "l2-engine")
                cursors[engine] = result["cursor"]
            # Completion drains a bounded backlog, never a full-log scan loop that can hold delivery.
            for _ in range(7 if final else 0):
                if not result.get("pending"):
                    break
                result = engines.observe_token_usage(engine, roots[-1]["session_id"], cursors[engine],
                                                     job_root=path.parent / "l2-engine")
                cursors[engine] = result["cursor"]
            sessions = result.get("sessions", [])
            attempts = {root["session_id"]: root["attempt"] for root in roots}
            parents = {row["session_id"]: row.get("parent_session_id") for row in sessions}
            for row in sessions:
                ancestor, seen = row["session_id"], set()
                while ancestor not in attempts and ancestor in parents and ancestor not in seen:
                    seen.add(ancestor)
                    ancestor = parents[ancestor]
                rows.append({**row, "engine": engine, "attempt": attempts.get(ancestor)})
            for helper in result.get("helpers", []):
                owner = next((root for root in roots if root["session_id"] == helper.get("owner_session_id")), {})
                helpers.append({**helper, "engine": engine, "attempt": owner.get("attempt"),
                                "attempts": owner.get("attempts", [owner["attempt"]] if owner else [])})
            helper_statuses.append(result.get("helper_status", "unknown"))
            notes.extend(result.get("notes", []))
            statuses.append(result.get("status", "partial"))
            if result.get("pending"):
                notes.append("Collection is catching up; some local records have not been read.")
                statuses.append("partial")
        attempts = {attempt for row in identities for attempt in row.get("attempts", [row["attempt"]])}
        if any(n not in attempts for n in range(1, task.get("attempt", 0) + 1)):
            notes.append("Earlier attempt identities or usage are unavailable.")
            statuses.append("partial")
        if not rows:
            notes.append("No local token counters have been observed.")
        counters = {key: _sum(rows, key) for key in COUNTERS}
        known = [counters[key] for key in ("input_tokens", "output_tokens") if counters[key] is not None]
        total = sum(known) if known else None
        status = ("unknown" if total is None else "partial" if "partial" in statuses
                  or any(row.get("status") != "observed" for row in rows) else "observed")
        helper_known = bool(helpers) or "partial" in helper_statuses
        unclassified = sum(row.get("depth") is None for row in helpers)
        direct = sum(row.get("depth") == 1 for row in helpers)
        descendants = sum((row.get("depth") or 0) > 1 for row in helpers)
        helper_summary = {"status": "partial" if helper_known else "unknown",
                          "observed_count": len(helpers) if helper_known else None,
                          "direct_count": direct if helper_known and (direct or not unclassified) else None,
                          "descendant_count": descendants if helper_known and (descendants or not unclassified) else None,
                          "unclassified_count": unclassified if helper_known else None,
                          "total_tokens": _sum(helpers, "total_tokens"), "sessions": helpers}
        # Only the current owner session's own newest request is current context; earlier owners are history.
        owner = next((row for row in rows if row["engine"] == task.get("l2_engine")
                      and row["session_id"] == task.get("session_id")), {})
        context = {**owner["context"], "engine": owner["engine"], "session_id": owner["session_id"]} if owner.get("context") else None
        at = S.now()
        snapshot = {"status": status, **counters, "total_tokens": total, "context": context,
                    "checked_at": at, "observed_at": max((row["observed_at"] for row in [*rows, *helpers]
                                                           if row.get("observed_at")), default=None),
                    "finalized_at": at if final else None, "sessions": rows, "helpers": helper_summary,
                    "notes": list(dict.fromkeys(notes))}
        S.write_json(path, cursors)
        task["token_usage"] = snapshot
    except Exception:
        # Missing/malformed provider records or unavailable telemetry storage must not block a task.
        task["token_usage"] = {**previous, "status": "partial" if previous.get("total_tokens") is not None else "unknown",
                               "finalized_at": S.now() if final else None,
                               "notes": list(dict.fromkeys([*previous.get("notes", []),
                                                            "Usage collection unavailable; prior observations retained."]))}


def refresh(project: str, slug: str) -> dict:
    """The daemon calls this, never a browser poll. A restart resumes the persisted byte cursors."""
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if task["state"] in ("done", "rejected"):
            return task
        checked = (task.get("token_usage") or {}).get("checked_at")
        try:
            if checked and (datetime.fromisoformat(S.now()) - datetime.fromisoformat(checked)).total_seconds() < 10:
                return task
        except (TypeError, ValueError):
            pass  # Invalid telemetry freshness is due for recollection, not a worker fault.
        capture(project, task)
        try:
            # Passive observations must not reset task/decision ages or reorder work cards.
            S.write_json(S.status_path(project, slug), task)
        except OSError:
            pass  # The last durable snapshot still exposes its age; telemetry cannot stop polling.
        return task
