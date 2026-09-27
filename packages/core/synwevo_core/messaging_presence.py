"""Presence, kept in memory and defined in three words (CI-74).

Nothing here is stored. A signed-in browser tab reports a heartbeat while the page is visible
(`POST /api/messaging/presence`); this registry remembers, per person, when the last one arrived.
The state anyone else sees is a function of that age and nothing more:

* **在线 (online)** — a heartbeat in the last ``ONLINE_S`` seconds (a Synwevo page is visible).
* **离开 (away)** — the last heartbeat is ``ONLINE_S``–``AWAY_S`` old (the page was hidden, or the
  person stepped away; the tab stops reporting when it is not visible).
* **离线 (offline)** — older than ``AWAY_S``, or never reported since this process started.

No "last seen at" is exposed, no history is kept, and a restart forgets everything — after which
everyone is offline until their next heartbeat, which is the truth of what the server knows.
The state is per **process**: the deployment runs one messages worker, and a second worker would
need a shared registry (recorded next to the event fan-out's condition for scaling out).

Visibility is the service layer's decision (`Messaging.presence_of`): a person who hides their
presence (`messaging_settings.show_presence = 0`) is not shown to anyone and sees nobody's —
the rule is mutual, so hiding never buys a one-way window.
"""
from __future__ import annotations

import threading
import time

ONLINE_S = 60
AWAY_S = 300
STATES = ("online", "away", "offline")


class Presence:
    def __init__(self, *, online_s: int = ONLINE_S, away_s: int = AWAY_S, clock=time.time):
        self.online_s, self.away_s, self._clock = online_s, away_s, clock
        self._last: dict[str, float] = {}
        self._state: dict[str, str] = {}
        self._lock = threading.Lock()
        self._swept_at = 0.0

    def state_at(self, last: float | None, ts: float | None = None) -> str:
        ts = self._clock() if ts is None else ts
        if last is None:
            return "offline"
        age = ts - last
        if age <= self.online_s:
            return "online"
        if age <= self.away_s:
            return "away"
        return "offline"

    def heartbeat(self, user_id: str) -> tuple[str, str]:
        """Record a heartbeat; returns `(previous_state, 'online')` so the caller can publish a
        transition when there was one."""
        ts = self._clock()
        with self._lock:
            before = self.state_at(self._last.get(user_id), ts)
            self._last[user_id] = ts
            self._state[user_id] = "online"
        return before, "online"

    def state(self, user_id: str) -> str:
        with self._lock:
            return self.state_at(self._last.get(user_id))

    def states(self, user_ids) -> dict[str, str]:
        ts = self._clock()
        with self._lock:
            return {u: self.state_at(self._last.get(u), ts) for u in user_ids}

    def forget(self, user_id: str) -> str:
        """Drop a person (they hid their presence or signed out); returns the state they had."""
        with self._lock:
            before = self.state_at(self._last.pop(user_id, None))
            self._state.pop(user_id, None)
        return before

    def sweep(self, *, min_interval_s: float = 10.0) -> list[tuple[str, str]]:
        """People whose state aged since they were last looked at: `[(user_id, new_state), …]`.
        Rate-limited so a hundred open streams ticking at once do not each walk the table;
        entries that reached `offline` are dropped, so the registry cannot grow past the people
        seen in the last `away_s` seconds."""
        ts = self._clock()
        with self._lock:
            if ts - self._swept_at < min_interval_s:
                return []
            self._swept_at = ts
            changed: list[tuple[str, str]] = []
            for uid in list(self._last):
                new = self.state_at(self._last[uid], ts)
                if new != self._state.get(uid):
                    self._state[uid] = new
                    changed.append((uid, new))
                if new == "offline":
                    self._last.pop(uid, None)
                    self._state.pop(uid, None)
            return changed

    def tracked(self) -> int:
        with self._lock:
            return len(self._last)
