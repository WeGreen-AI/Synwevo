"""Request rate, token rate, per-payer in-flight calls and bounded fair waiting.

Model calls through the platform account ask for capacity before any money is reserved and
are admitted in the same transaction as Ledger's one-shot send fence (docs/platform/model-traffic.md).
Policy comes from the versioned cost policy; a policy without `traffic` keeps only the
platform in-flight and cost limits. Unreadable policy or state refuses new calls.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
import time

from ..store import new_id
from .models import BillingError, identifier, integer

WINDOW_S = 60
GRANT_TTL_S = 30
POLL_S = 0.25
_MAX_WAITING = 10_000
# Ended rows are queue state, not money facts (usage and cost live in the ledger). Keep a day:
# far beyond the one-minute windows and the one-hour fairness memory.
RETAIN_S = 86_400
_PRUNE_BATCH = 500


@dataclass(frozen=True)
class TrafficLimits:
    platform_rpm: int
    platform_tpm: int
    payer_rpm: int
    payer_tpm: int
    payer_inflight: int
    wait_seconds: int
    max_waiting: int
    max_waiting_per_payer: int

    def __post_init__(self):
        for value in (self.platform_rpm, self.platform_tpm, self.payer_rpm, self.payer_tpm,
                      self.payer_inflight, self.max_waiting, self.max_waiting_per_payer):
            integer(value, positive=True)
        integer(self.wait_seconds, maximum=900)
        if (self.payer_rpm > self.platform_rpm or self.payer_tpm > self.platform_tpm
                or self.max_waiting > _MAX_WAITING or self.max_waiting_per_payer > self.max_waiting):
            raise BillingError("invalid_traffic_policy")


class TrafficCancelled(Exception):
    """The caller stopped while waiting; nothing was reserved or sent."""


class ModelTraffic:
    def __init__(self, ledger, *, current_policy, sleep=time.sleep):
        self.ledger, self.store, self.environment = ledger, ledger.store, ledger.environment
        self.current_policy, self.sleep = current_policy, sleep

    # ------------------------------------------------------------------ policy and counters
    def _limits(self):
        policy = self.current_policy()
        return policy, getattr(policy, "traffic", None)

    def _scope(self, policy):
        return (self.environment, policy.account_ref)

    def _usage(self, t, policy, now, payer=None):
        """Requests and tokens admitted in the last minute plus live grants, and calls in flight."""
        scope, where, args = self._scope(policy), "", ()
        if payer is not None:
            where, args = " AND payer_user_id=?", (payer,)
        recent = t.execute(
            "SELECT COUNT(*), COALESCE(SUM(COALESCE(actual_tokens,est_tokens)),0) FROM model_traffic_claims"
            " WHERE environment=? AND account_ref=? AND admitted_at>?" + where,
            (*scope, now - WINDOW_S, *args)).fetchone()
        granted = t.execute(
            "SELECT COUNT(*), COALESCE(SUM(est_tokens),0) FROM model_traffic_claims"
            " WHERE environment=? AND account_ref=? AND state='granted'" + where, (*scope, *args)).fetchone()
        inflight = t.execute(
            "SELECT COUNT(*) FROM model_traffic_claims WHERE environment=? AND account_ref=?"
            " AND state IN ('admitted','unknown') AND inflight_until>?" + where, (*scope, now, *args)).fetchone()[0]
        return {"requests": recent[0] + granted[0], "tokens": recent[1] + granted[1],
                "inflight": inflight + granted[0]}

    def _expire(self, t, policy, now):
        scope = self._scope(policy)
        # Strictly before: a call with no waiting allowance still gets one chance at its own second.
        t.execute("UPDATE model_traffic_claims SET state='expired' WHERE environment=? AND account_ref=?"
                  " AND state='waiting' AND wait_until<?", (*scope, now))
        t.execute("UPDATE model_traffic_claims SET state='expired' WHERE environment=? AND account_ref=?"
                  " AND state='granted' AND grant_expires_at<=?", (*scope, now))
        t.execute("DELETE FROM model_traffic_claims WHERE rowid IN (SELECT rowid FROM model_traffic_claims"
                  " WHERE environment=? AND account_ref=? AND state IN ('finished','unknown','expired','abandoned')"
                  " AND enqueued_at<? AND (state<>'unknown' OR COALESCE(inflight_until,0)<?) LIMIT ?)",
                  (*scope, now - RETAIN_S, now, _PRUNE_BATCH))

    def _grant(self, t, policy, limits, now):
        """Grant waiting calls in fair order: payers by oldest last grant, each payer first-come."""
        self._expire(t, policy, now)
        scope = self._scope(policy)
        rows = t.execute("SELECT id,payer_user_id,est_tokens FROM model_traffic_claims WHERE environment=?"
                         " AND account_ref=? AND state='waiting' ORDER BY enqueued_at,id LIMIT ?",
                         (*scope, limits.max_waiting)).fetchall()
        if not rows:
            return
        queues: dict[str, deque] = {}
        for row in rows:
            queues.setdefault(row["payer_user_id"], deque()).append(row)
        arrival = {payer: index for index, payer in enumerate(queues)}
        last = {row[0]: row[1] for row in t.execute(
            "SELECT payer_user_id, MAX(granted_at) FROM model_traffic_claims WHERE environment=? AND account_ref=?"
            " AND granted_at>? GROUP BY payer_user_id", (*scope, now - 3600)).fetchall()}
        platform = self._usage(t, policy, now)
        payers = {payer: self._usage(t, policy, now, payer) for payer in queues}
        active = deque(sorted(queues, key=lambda payer: (last.get(payer, -1), arrival[payer])))
        while active:
            payer = active.popleft()
            claim, own = queues[payer][0], payers[payer]
            tokens = claim["est_tokens"]
            if (platform["requests"] + 1 > limits.platform_rpm or platform["tokens"] + tokens > limits.platform_tpm
                    or platform["inflight"] + 1 > policy.max_inflight):
                return  # Platform capacity is exhausted: keep the order for the next round.
            if (own["requests"] + 1 > limits.payer_rpm or own["tokens"] + tokens > limits.payer_tpm
                    or own["inflight"] + 1 > limits.payer_inflight):
                continue  # This payer's own limit; the others still proceed.
            t.execute("UPDATE model_traffic_claims SET state='granted',granted_at=?,grant_expires_at=? WHERE id=?",
                      (now, now + GRANT_TTL_S, claim["id"]))
            for usage in (platform, own):
                usage["requests"] += 1
                usage["tokens"] += tokens
                usage["inflight"] += 1
            queues[payer].popleft()
            if queues[payer]:
                active.append(payer)

    # ------------------------------------------------------------------ caller API
    def acquire(self, payer_user_id: str, *, est_tokens: int, deadline: int, should_stop) -> str | None:
        """Wait for capacity; return the granted claim, or None when traffic limits are not configured."""
        identifier(payer_user_id)
        integer(est_tokens, positive=True)
        integer(deadline)
        with self.store.tx() as t:
            policy, limits = self._limits()
            if limits is None:
                return None
            if est_tokens > limits.payer_tpm:
                raise BillingError("traffic_request_too_large")
            now = self.ledger._now()
            scope = self._scope(policy)
            self._expire(t, policy, now)
            waiting = t.execute("SELECT COUNT(*), COALESCE(SUM(payer_user_id=?),0) FROM model_traffic_claims"
                                " WHERE environment=? AND account_ref=? AND state='waiting'",
                                (payer_user_id, *scope)).fetchone()
            if waiting[0] >= limits.max_waiting:
                raise BillingError("traffic_queue_full")
            if waiting[1] >= limits.max_waiting_per_payer:
                raise BillingError("traffic_payer_queue_full")
            claim = new_id("traffic")
            t.execute("INSERT INTO model_traffic_claims(id,environment,account_ref,payer_user_id,est_tokens,state,"
                      "enqueued_at,wait_until) VALUES (?,?,?,?,?,'waiting',?,?)",
                      (claim, *scope, payer_user_id, est_tokens, now, min(now + limits.wait_seconds, deadline)))
        granted = False
        try:
            while True:
                with self.store.tx() as t:
                    policy, limits = self._limits()
                    if limits is None:
                        raise BillingError("traffic_policy_changed")
                    now = self.ledger._now()
                    self._grant(t, policy, limits, now)
                    row = t.execute("SELECT state,wait_until FROM model_traffic_claims WHERE id=?", (claim,)).fetchone()
                if row["state"] == "granted":
                    granted = True
                    return claim
                if row["state"] != "waiting" or now >= row["wait_until"]:
                    raise BillingError("traffic_wait_timeout")
                if should_stop():
                    raise TrafficCancelled()
                self.sleep(POLL_S * random.uniform(0.8, 1.2))
        finally:
            if not granted:
                self.abandon(claim)

    def admit(self, transaction, claim: str, *, attempt_key: str, inflight_until: int) -> None:
        """Inside Ledger.dispatch: the grant must still be live; refusal sends nothing."""
        identifier(claim)
        identifier(attempt_key)
        now = self.ledger._now()
        row = transaction.execute("SELECT state,grant_expires_at,environment FROM model_traffic_claims WHERE id=?",
                                  (claim,)).fetchone()
        if not row or row["environment"] != self.environment or row["state"] != "granted" or row["grant_expires_at"] <= now:
            raise BillingError("traffic_grant_expired")
        transaction.execute("UPDATE model_traffic_claims SET state='admitted',admitted_at=?,inflight_until=?,attempt_key=?"
                            " WHERE id=?", (now, integer(inflight_until), attempt_key, claim))

    def finish(self, claim: str, *, tokens: int | None) -> None:
        """Known usage replaces the estimate; unknown usage keeps the estimate and its in-flight slot."""
        if tokens is not None:
            integer(tokens)
        with self.store.tx() as t:
            t.execute("UPDATE model_traffic_claims SET state=?,finished_at=?,actual_tokens=? WHERE id=? AND state='admitted'",
                      ("finished" if tokens is not None else "unknown", self.ledger._now(), tokens, claim))
        self.abandon(claim)  # Granted but never admitted (refused before sending): free it.

    def abandon(self, claim: str) -> None:
        with self.store.tx() as t:
            t.execute("UPDATE model_traffic_claims SET state='abandoned' WHERE id=? AND state IN ('waiting','granted')",
                      (claim,))

    def queue_state(self, payer_user_id: str) -> dict | None:
        """Backpressure before accepting new work: is there room to wait at all?"""
        with self.store.tx() as t:
            policy, limits = self._limits()
            if limits is None:
                return None
            now = self.ledger._now()
            self._expire(t, policy, now)
            waiting = t.execute("SELECT COUNT(*), COALESCE(SUM(payer_user_id=?),0) FROM model_traffic_claims"
                                " WHERE environment=? AND account_ref=? AND state='waiting'",
                                (payer_user_id, *self._scope(policy))).fetchone()
        return {"waiting": waiting[0], "max_waiting": limits.max_waiting, "payer_waiting": waiting[1],
                "max_waiting_per_payer": limits.max_waiting_per_payer,
                "full": waiting[0] >= limits.max_waiting or waiting[1] >= limits.max_waiting_per_payer,
                "retry_after": max(1, min(limits.wait_seconds, WINDOW_S))}

    def snapshot(self) -> dict:
        """Current platform counters for Account governance; no payer identities."""
        with self.store.tx() as t:
            policy, limits = self._limits()
            now = self.ledger._now()
            usage = self._usage(t, policy, now)
            waiting = t.execute("SELECT COUNT(*), MIN(enqueued_at) FROM model_traffic_claims WHERE environment=?"
                                " AND account_ref=? AND state='waiting' AND wait_until>?",
                                (*self._scope(policy), now)).fetchone()
        return {"configured": limits is not None, "as_of": now, "window_s": WINDOW_S,
                "requests": usage["requests"], "tokens": usage["tokens"], "inflight": usage["inflight"],
                "waiting": waiting[0], "oldest_wait_s": (now - waiting[1]) if waiting[1] is not None else 0}
