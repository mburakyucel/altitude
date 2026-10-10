"""A fixed number of places on this machine that requests take in arrival order.

A request that finds every place taken waits its turn, says what it waits for, and leaves the line when its client
goes away or a newer request replaces it. The validation runner has one place (`validation.LINE`) and `alt task run` a
few (`server.MACHINE_LINE`).
See docs/DEVELOPMENT.md#validation-runner.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import threading
import time

CLIENT_GONE = "its client stopped or lost its connection"


class Line:
    def __init__(self, places: int, name: str):
        self.places, self.name = places, name
        self.lock = threading.Condition()   # guards the queue and the holders
        self._queue: list = []              # the waiting requests, in arrival order
        self.holders: list[dict] = []       # each admitted request: what it is and when its limit ends, if it has one

    def _waiting_for(self, ahead: int) -> str:
        """What a request in line waits for, with `lock` held."""
        held = ", ".join(h["what"] + (f" until its limit at {h['ends']:%Y-%m-%d %H:%M:%S} UTC" if h.get("ends") else "")
                         for h in self.holders) or "the requests ahead of it are being admitted"
        return f"waiting for {self.name}: {held}" + (f"; {ahead} request(s) ahead of this one" if ahead else "")

    def ends(self, place: dict, seconds: int) -> None:
        """The admitted request's limit now ends `seconds` from now."""
        with self.lock:
            place["ends"] = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    @contextmanager
    def turn(self, what: str, limit: int | None, *, command: str, wait: int = 0, watch: float = 2,
             waiting=lambda text: None, gone=lambda: False, force: bool = False):
        """Hold a place for `what` (as the line describes it), whose limit ends `limit` seconds from admission.
        A request that finds every place taken waits in arrival order for up to `wait` seconds, telling
        `waiting(text)` what it waits for when that changes and at least every minute, and checking every `watch`
        seconds that `gone()` does not say its client stopped (True) or give another reason it leaves. `force` takes a
        place at once even beyond the line's places, for work already running. Yields the place, whose limit `ends`
        can move."""
        ticket, deadline, told, last = object(), time.monotonic() + wait, None, 0.0
        with self.lock:
            self._queue.append(ticket)
        try:
            while True:
                with self.lock:
                    if force or self._queue[0] is ticket and len(self.holders) < self.places:
                        place = {"what": what, "ends": limit and datetime.now(timezone.utc) + timedelta(seconds=limit)}
                        self.holders.append(place)
                        break
                    text = self._waiting_for(self._queue.index(ticket))
                why = gone()
                if why:
                    raise ValueError(f"{command}: {CLIENT_GONE if why is True else why} while it waited")
                if time.monotonic() >= deadline:
                    raise ValueError(f"{command}: not admitted within {wait // 60} minutes, {text}; try again")
                if text != told or time.monotonic() - last >= 60:
                    waiting(text)
                    told, last = text, time.monotonic()
                with self.lock:
                    self.lock.wait(watch)
        finally:
            with self.lock:
                self._queue.remove(ticket)
                self.lock.notify_all()
        try:
            yield place
        finally:
            with self.lock:
                self.holders = [h for h in self.holders if h is not place]
                self.lock.notify_all()
