"""When a schedule comes due, in this computer's local time, and how it reads.

A schedule is days of the week (0 is Monday) and a time ("07:00"). The project's tick claims the latest due time not
yet claimed (`latest`), never one before the schedule was turned on, so a time missed while Altitude was off runs
once when it comes back and older ones are not caught up. Project agents and the Weekly improvement review share it.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
PLURAL = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
NAMED = {(0, 1, 2, 3, 4): "Weekdays", (5, 6): "Weekends", (0, 1, 2, 3, 4, 5, 6): "Every day"}


def _at(day: date, time: str) -> datetime:
    hour, minute = map(int, time.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def latest(days, time: str, now: datetime) -> datetime | None:
    """The schedule's latest local time at or before `now`, within the last week; None without days."""
    local = now.astimezone()
    for back in range(8):
        day = local.date() - timedelta(days=back)
        if day.weekday() in days and (at := _at(day, time)) <= now:
            return at
    return None


def upcoming(days, time: str, now: datetime) -> datetime | None:
    """The schedule's first local time after `now`; None without days."""
    local = now.astimezone()
    for ahead in range(8):
        day = local.date() + timedelta(days=ahead)
        if day.weekday() in days and (at := _at(day, time)) > now:
            return at
    return None


def words(days, time: str) -> str:
    """"Weekdays at 07:00", "Mondays at 08:00", "Mon, Wed, Fri at 07:00"."""
    days = tuple(sorted(set(days)))
    named = NAMED.get(days) or (PLURAL[days[0]] if len(days) == 1 else ", ".join(DAYS[d] for d in days))
    return f"{named} at {time}"


def when(at: datetime, now: datetime) -> str:
    """A coming time as a status line says it: "today at 17:00", "tomorrow at 07:00", "Monday at 08:00"."""
    local, today = at.astimezone(), now.astimezone().date()
    day = ("today" if local.date() == today else "tomorrow" if local.date() == today + timedelta(days=1)
           else f"{local:%A}" if local.date() < today + timedelta(days=7) else f"{local:%b} {local.day}")
    return f"{day} at {local:%H:%M}"


def zone() -> dict:
    """This computer's zone as a schedule names it ("Istanbul") and its offset from UTC in minutes now, so a page in
    another zone can say whose 07:00 it means."""
    now = datetime.now(timezone.utc).astimezone()
    name = os.environ.get("TZ", "").lstrip(":")
    if not name and os.path.islink("/etc/localtime"):
        name = os.path.realpath("/etc/localtime").partition("zoneinfo/")[2]
    label = name.rsplit("/", 1)[-1].replace("_", " ") if "/" in name else now.tzname() or "UTC"
    return {"zone": label, "offset": int(now.utcoffset().total_seconds() // 60)}
