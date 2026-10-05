"""Read-only usage projections of one payer's model calls: per call, per period, as CSV.

Every number comes from immutable money and metering facts: ``billing_calls`` (the hold and the
payer's wallet account), ``billing_attempts`` (requested model, dispatch time, pinned time band),
``billing_usage`` (trusted final counters), ``billing_settlements``/``billing_journal`` (the amount
actually sold) and ``agent_model_observations`` (finish reason, completion time). Engine counters
such as ``agent_turns.input_tokens`` are never read, so a figure shown here is the ledger's figure,
not a client or engine estimate.

Never projected: platform cost prices or amounts, provider request ids, evidence references, the
call's authorization context, other payers' wallets.

Payer authority is decided here, from current state, on every read:
* ``user`` — only that user (a platform or workspace administrator is not the payer);
* ``organization`` — a current billing manager of an active organization: its owner, or a member
  whose base role or custom roles carry ``org.billing.manage`` (the permission the org-payments
  package uses for top-ups and organization orders, so reading and paying follow one rule). An
  organization admin without that permission, an ordinary member, a platform administrator and
  anyone outside the organization get the same refusal, whether or not the organization exists.

An organization's billing managers see which member made each call (they already see their
members' names and addresses) but never the member's conversations: no conversation id, no
title, and no grouping by conversation — a member's Agent conversation is the member's own.

No method writes. A reserved call whose deadline has passed is shown as not charged, matching
the ledger's rule that an expired hold is final and never charged later.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import io
import json
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..org_roles import BASE as ORG_BASE_ROLES
from ..store import Store
from .models import MAX_INTEGER, BillingError, identifier, integer

PAYER_KINDS = ("user", "organization")
#: ``billing_accounts.kind`` of each payer's wallet. A personal wallet is the ledger's ``wallet``
#: owned by the user id; an organization wallet is ``org_wallet`` owned by the organization id (the
#: org-payments package widens the account-kind CHECK to admit it; until then an organization has
#: no wallet, and every projection of it is honestly empty).
WALLET_KINDS = {"user": "wallet", "organization": "org_wallet"}
#: Who may read an organization's money (owners hold every organization permission).
BILLING_PERMISSION = "org.billing.manage"
STATES = ("settled", "pending", "held", "not_charged")
GROUPS = {"user": ("day", "model", "conversation"), "organization": ("day", "model", "member")}
#: The ledger namespace whose run ids are Agent turn ids (apps/suite agent configuration).
AGENT_NAMESPACE = "agent"
MAX_SPAN_DAYS = 366
AGGREGATE_CAP = 20_000
CSV_CAP = 10_000
PAGE_MAX = 100
MAX_ORGANIZATIONS = 50
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Leading characters a spreadsheet may evaluate as a formula or a DDE command (OWASP "CSV
#: injection"), plus their full-width forms, which some spreadsheet locales also accept.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n", "|", "%", "＝", "＋", "－", "＠")


@dataclass(frozen=True)
class Payer:
    kind: str
    id: str

    def __post_init__(self):
        if self.kind not in PAYER_KINDS:
            raise BillingError("invalid_payer")
        identifier(self.id)


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    zone: str
    start: int  # inclusive epoch seconds
    end: int  # exclusive epoch seconds

    def as_dict(self) -> dict:
        return {"from": self.first.isoformat(), "to": self.last.isoformat(), "timezone": self.zone}


def zone(name: str | None) -> ZoneInfo:
    if not isinstance(name, str) or not name or len(name) > 64:
        raise BillingError("invalid_timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise BillingError("invalid_timezone") from None


def period(first: str | None, last: str | None, zone_name: str, *, today: date | None = None) -> Period:
    """Inclusive local calendar days in ``zone_name``; default the last 30 days."""
    tz = zone(zone_name)
    today = today or datetime.now(tz).date()

    def parse(value):
        if not isinstance(value, str) or not _DATE.match(value):
            raise BillingError("invalid_period")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise BillingError("invalid_period") from None

    end_day = parse(last) if last else today
    start_day = parse(first) if first else end_day - timedelta(days=29)
    if start_day > end_day or (end_day - start_day).days + 1 > MAX_SPAN_DAYS:
        raise BillingError("invalid_period")
    start = datetime.combine(start_day, datetime.min.time(), tz)
    end = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tz)
    return Period(start_day, end_day, tz.key, int(start.timestamp()), int(end.timestamp()))


def neutralize(value) -> str:
    """One CSV cell that a spreadsheet shows as text and never evaluates (formula injection).

    The test is on the first non-blank character: some programs trim leading spaces before
    deciding whether a cell is a formula."""
    text = "" if value is None else str(value)
    if text.lstrip(" ").startswith(_FORMULA_LEAD):
        return "'" + text
    return text


def decimal_text(units: int, scale: int) -> str:
    """Integer smallest units as a plain decimal string; no float on the way."""
    sign = "-" if units < 0 else ""
    digits = str(abs(units)).rjust(scale + 1, "0")
    whole, fraction = (digits[:-scale], digits[-scale:]) if scale else (digits, "")
    return sign + whole + ("." + fraction if fraction else "")


def wallet_account(t, environment: str, payer: Payer, unit: str):
    """The payer's wallet account id for ``unit`` in ``environment``, or None."""
    row = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind=?",
                    (environment, unit, payer.id, WALLET_KINDS[payer.kind])).fetchone()
    return row[0] if row else None


def manages_billing(t, organization_id: str, user_id: str) -> bool:
    """Whether ``user_id`` is a current billing manager of an active organization (see module)."""
    row = t.execute("SELECT m.role FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id "
                    "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND m.user_id=? "
                    "AND o.status='active' AND o.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL",
                    (organization_id, user_id)).fetchone()
    if not row:
        return False
    if row[0] == "owner" or BILLING_PERMISSION in ORG_BASE_ROLES.get(row[0], ()):
        return True
    for (permissions,) in t.execute("SELECT r.permissions FROM organization_role_assignments a "
                                    "JOIN organization_roles r ON r.id=a.role_id "
                                    "WHERE a.organization_id=? AND a.user_id=?", (organization_id, user_id)):
        try:
            if BILLING_PERMISSION in json.loads(permissions or "[]"):
                return True
        except (TypeError, ValueError):
            continue
    return False


class PayerUsage:
    """Read model bound to one ledger environment. ``clock`` is injectable for tests."""

    def __init__(self, store: Store, *, environment: str, clock=None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    # ------------------------------------------------------------------ authority
    def _authorize(self, t, requester: str, payer: Payer) -> None:
        identifier(requester)
        if not isinstance(payer, Payer):
            raise BillingError("invalid_payer")
        if not t.execute("SELECT 1 FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                         (requester,)).fetchone():
            raise BillingError("account_unavailable")
        if payer.kind == "user":
            if payer.id != requester:
                raise BillingError("wallet_forbidden")
            return
        if not manages_billing(t, payer.id, requester):
            raise BillingError("wallet_forbidden")

    def authorize(self, requester: str, payer: Payer) -> None:
        with self.store.tx() as t:
            self._authorize(t, requester, payer)

    def payers(self, requester: str) -> list[dict]:
        """The payers ``requester`` may read: themself first, then organizations by name."""
        with self.store.tx() as t:
            self._authorize(t, requester, Payer("user", requester))
            out = [{"kind": "user", "id": requester, "name": None}]
            rows = t.execute("SELECT o.id,o.name FROM organization_memberships m JOIN organizations o "
                             "ON o.id=m.organization_id WHERE m.user_id=? AND o.status='active' "
                             "AND o.deleted_at IS NULL ORDER BY o.name,o.id LIMIT ?",
                             (requester, MAX_ORGANIZATIONS)).fetchall()
            out += [{"kind": "organization", "id": row[0], "name": row[1]} for row in rows
                    if manages_billing(t, row[0], requester)]
            return out

    # ------------------------------------------------------------------ balance and entries
    def overview(self, requester: str, payer: Payer, units) -> dict:
        """The ledger's personal projection (booked/used/held/available), for any payer the
        requester may read. Reads only: an expired hold is simply not counted as held."""
        if (not isinstance(units, (tuple, list)) or not 1 <= len(units) <= 16
                or len({unit.code for unit in units}) != len(units)):
            raise BillingError("invalid_unit")
        now = self.clock()
        with self.store.tx() as t:
            self._authorize(t, requester, payer)
            through, balances = 0, []
            for unit in units:
                known = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
                if not known or known[0] != unit.scale:
                    raise BillingError("unit_scale_conflict")
                account = wallet_account(t, self.environment, payer, unit.code)
                booked = held = used = 0
                if account:
                    booked = t.execute("SELECT amount FROM billing_balances WHERE account_id=?",
                                       (account,)).fetchone()[0]
                    held = t.execute("SELECT COALESCE(SUM(hold_units),0) FROM billing_calls WHERE account_id=? "
                                     "AND status='reserved' AND deadline>?", (account, now)).fetchone()[0]
                    used = t.execute("SELECT COALESCE(SUM(amount),0) FROM billing_journal WHERE environment=? "
                                     "AND debit_account=? AND kind='sale'",
                                     (self.environment, account)).fetchone()[0]
                    through = max(through, t.execute(
                        "SELECT COALESCE(MAX(rowid),0) FROM billing_journal WHERE environment=? "
                        "AND (debit_account=? OR credit_account=?)",
                        (self.environment, account, account)).fetchone()[0])
                balances.append({"unit": unit.code, "scale": unit.scale, "booked": booked, "used": used,
                                 "held": held, "available": booked - held})
            return {"balances": balances, "used_through_seq": through}

    def statement(self, requester: str, payer: Payer, *, after_seq: int = 0, before_seq: int | None = None,
                  limit: int = 100) -> list[dict]:
        """Posted entries of the payer's wallets. ``after_seq`` pages oldest first; ``before_seq``
        (exclusive) pages newest first, for a "recent activity" list."""
        integer(after_seq)
        integer(limit, positive=True, maximum=500)
        if before_seq is not None:
            integer(before_seq, positive=True)
        order, bound, cursor = ("DESC", "<", before_seq) if before_seq is not None else ("ASC", ">", after_seq)
        with self.store.tx() as t:
            self._authorize(t, requester, payer)
            rows = t.execute("SELECT j.rowid AS seq,j.id,j.kind,j.unit,u.scale,j.created_at,"
                             "CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END AS delta "
                             "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account,j.credit_account) "
                             "JOIN billing_units u ON u.code=j.unit WHERE a.kind=? AND a.owner=? "
                             f"AND a.environment=? AND j.environment=? AND j.rowid{bound}? ORDER BY j.rowid {order} LIMIT ?",
                             (WALLET_KINDS[payer.kind], payer.id, self.environment, self.environment,
                              cursor, limit)).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ calls
    def _select(self, t, requester: str, payer: Payer, window: Period, *, model, state, before, limit):
        columns = {row[1] for row in t.execute("PRAGMA table_info(billing_attempts)").fetchall()}
        # The pricing package pins the time band (and records a completion time) on the attempt.
        # Until those columns exist the band is honestly unknown, never guessed; the completion
        # time falls back to the gateway's observation of the finished response.
        band = "a.band" if "band" in columns else "NULL"
        finished = "COALESCE(" + ("a.completed_at," if "completed_at" in columns else "") + "o.created_at,u.created_at)"
        personal = payer.kind == "user"
        conversation = "tn.conversation_id" if personal else "NULL"
        title = "CASE WHEN cv.user_id=:requester THEN cv.title END" if personal else "NULL"
        actor = "NULL" if personal else "json_extract(c.context,'$.actor_user_id')"
        actor_name = "NULL" if personal else (
            "(SELECT COALESCE(NULLIF(p.display_name,''),p.email) FROM users p "
            "WHERE p.id=json_extract(c.context,'$.actor_user_id'))")
        state_sql = ("CASE WHEN s.call_key IS NOT NULL THEN 'settled' "
                     "WHEN c.status='reserved' AND c.deadline>:now AND a.status='usage_pending' THEN 'pending' "
                     "WHEN c.status='reserved' AND c.deadline>:now THEN 'held' ELSE 'not_charged' END")
        sql = (
            "SELECT * FROM (SELECT c.rowid AS seq,c.id,c.created_at,c.price_id,c.hold_units,"
            f"{state_sql} AS state,acc.unit,un.scale,a.requested_model AS model,{band} AS band,"
            f"a.dispatched_at,CASE WHEN u.attempt_key IS NOT NULL OR o.attempt_key IS NOT NULL THEN {finished} END"
            " AS completed_at,"
            "json_extract(u.facts,'$.usage.input_cached') AS input_cached,"
            "json_extract(u.facts,'$.usage.input_uncached') AS input_uncached,"
            "json_extract(u.facts,'$.usage.output') AS output,"
            "json_extract(u.facts,'$.usage.reasoning') AS reasoning,"
            "j.amount AS charged,json_extract(o.evidence,'$.finish_reason') AS finish_reason,"
            f"{conversation} AS conversation_id,{title} AS conversation_title,"
            f"{actor} AS actor_user_id,{actor_name} AS actor_name "
            "FROM billing_calls c JOIN billing_accounts acc ON acc.id=c.account_id "
            "JOIN billing_units un ON un.code=acc.unit JOIN billing_runs r ON r.id=c.run_key "
            "LEFT JOIN billing_attempts a ON a.id=(SELECT x.id FROM billing_attempts x WHERE x.call_key=c.id "
            "ORDER BY x.rowid DESC LIMIT 1) "
            "LEFT JOIN billing_usage u ON u.attempt_key=a.id "
            "LEFT JOIN billing_settlements s ON s.call_key=c.id LEFT JOIN billing_journal j ON j.id=s.journal_id "
            "LEFT JOIN agent_model_observations o ON o.attempt_key=a.id "
            "LEFT JOIN agent_turns tn ON r.namespace=:agent AND tn.id=r.run_id "
            "LEFT JOIN agent_conversations cv ON cv.id=tn.conversation_id "
            "WHERE acc.environment=:environment AND acc.owner=:owner AND acc.kind=:kind "
            "AND c.created_at>=:start AND c.created_at<:end "
            # A call released before anything was sent never touched the payer's money.
            "AND (c.status<>'released' OR EXISTS(SELECT 1 FROM billing_attempts d WHERE d.call_key=c.id "
            "AND d.dispatched_at IS NOT NULL)) "
            + ("AND c.rowid<:before " if before is not None else "")
            + ") WHERE 1=1 "
            + ("AND model=:model " if model is not None else "")
            + ("AND state=:state " if state is not None else "")
            + "ORDER BY seq DESC LIMIT :limit")
        return t.execute(sql, {
            "requester": requester, "environment": self.environment, "owner": payer.id,
            "kind": WALLET_KINDS[payer.kind], "agent": AGENT_NAMESPACE,
            "start": window.start, "end": window.end, "now": self.clock(), "before": before,
            "model": model, "state": state, "limit": limit}).fetchall()

    @staticmethod
    def _row(row, payer: Payer) -> dict:
        has_usage = row["input_uncached"] is not None
        state = row["state"]
        out = {
            "id": row["id"], "created_at": row["created_at"], "model": row["model"], "band": row["band"],
            "price_version": row["price_id"], "state": state, "unit": row["unit"], "scale": row["scale"],
            "charged_minor": str(row["charged"] if state == "settled" else 0),
            "held_minor": str(row["hold_units"] if state in ("held", "pending") else 0),
            "tokens": {key: row[key] for key in ("input_cached", "input_uncached", "output", "reasoning")}
            if has_usage else None,
            "finish_reason": row["finish_reason"],
            "dispatched_at": row["dispatched_at"], "completed_at": row["completed_at"],
            "duration_s": (row["completed_at"] - row["dispatched_at"])
            if row["completed_at"] is not None and row["dispatched_at"] is not None else None,
            "conversation_id": row["conversation_id"],
        }
        if payer.kind == "user":
            out["conversation_title"] = row["conversation_title"]
        else:
            out["member_user_id"], out["member_name"] = row["actor_user_id"], row["actor_name"]
        return out

    @staticmethod
    def _filters(model, state):
        if model is not None:
            identifier(model)
        if state is not None and state not in STATES:
            raise BillingError("invalid_state")

    def calls(self, requester: str, payer: Payer, window: Period, *, model: str | None = None,
              state: str | None = None, before: int | None = None, limit: int = 50) -> dict:
        """Newest first; ``before`` is the previous page's ``next_cursor`` (exclusive)."""
        self._filters(model, state)
        integer(limit, positive=True, maximum=PAGE_MAX)
        if before is not None:
            integer(before, positive=True, maximum=MAX_INTEGER)
        with self.store.tx() as t:
            self._authorize(t, requester, payer)
            rows = self._select(t, requester, payer, window, model=model, state=state, before=before,
                                limit=limit + 1)
            models = [r[0] for r in t.execute(
                "SELECT DISTINCT a.requested_model FROM billing_calls c JOIN billing_accounts acc ON acc.id=c.account_id "
                "JOIN billing_attempts a ON a.call_key=c.id WHERE acc.environment=? AND acc.owner=? AND acc.kind=? "
                "AND c.created_at>=? AND c.created_at<? ORDER BY 1 LIMIT 50",
                (self.environment, payer.id, WALLET_KINDS[payer.kind], window.start, window.end)).fetchall()]
        items = [self._row(row, payer) for row in rows[:limit]]
        return {"items": items, "models": models,
                "next_cursor": str(rows[limit - 1]["seq"]) if len(rows) > limit else None}

    def _all(self, requester, payer, window, *, cap, model=None, state=None):
        self._filters(model, state)
        with self.store.tx() as t:
            self._authorize(t, requester, payer)
            rows = self._select(t, requester, payer, window, model=model, state=state, before=None, limit=cap + 1)
        if len(rows) > cap:
            raise BillingError("usage_period_too_large")
        return [self._row(row, payer) for row in rows]

    def summary(self, requester: str, payer: Payer, window: Period, *, group: str) -> dict:
        """Totals per bucket and per unit. Buckets with no calls are absent: a day without a
        bucket had no calls, and the reader may draw it as zero."""
        if not isinstance(payer, Payer) or group not in GROUPS[payer.kind]:
            raise BillingError("invalid_group")
        tz = ZoneInfo(window.zone)
        rows = self._all(requester, payer, window, cap=AGGREGATE_CAP)
        buckets: dict[tuple, dict] = {}
        totals: dict[str, dict] = {}

        def add(target, row):
            target["calls"] += 1
            target["charged"] += int(row["charged_minor"])
            target["held"] += int(row["held_minor"])
            if row["tokens"]:
                for key, value in row["tokens"].items():
                    target["tokens"][key] += value or 0

        def blank(unit, scale, **extra):
            return {"unit": unit, "scale": scale, "calls": 0, "charged": 0, "held": 0,
                    "tokens": {"input_cached": 0, "input_uncached": 0, "output": 0, "reasoning": 0}, **extra}

        for row in rows:
            if group == "day":
                extra = {"key": datetime.fromtimestamp(row["created_at"], timezone.utc).astimezone(tz).date().isoformat()}
            elif group == "model":
                extra = {"key": row["model"] or ""}
            elif group == "conversation":
                extra = {"key": row["conversation_id"] or "", "title": row["conversation_title"]}
            else:
                extra = {"key": row["member_user_id"] or "", "title": row["member_name"]}
            bucket = buckets.setdefault((row["unit"], extra["key"]), blank(row["unit"], row["scale"], **extra))
            if "title" in extra and not bucket.get("title") and extra["title"]:
                bucket["title"] = extra["title"]
            add(bucket, row)
            add(totals.setdefault(row["unit"], blank(row["unit"], row["scale"])), row)

        def public(item):
            out = {k: v for k, v in item.items() if k not in ("charged", "held")}
            out["charged_minor"], out["held_minor"] = str(item["charged"]), str(item["held"])
            return out

        ordered = sorted(buckets.values(), key=(lambda b: (b["unit"], b["key"])) if group == "day"
                         else (lambda b: (b["unit"], -b["charged"], -b["calls"], b["key"])))
        return {"group": group, "period": window.as_dict(), "buckets": [public(b) for b in ordered],
                "totals": [public(totals[unit]) for unit in sorted(totals)]}

    def csv_text(self, requester: str, payer: Payer, window: Period, *, model: str | None = None,
                 state: str | None = None) -> str:
        rows = self._all(requester, payer, window, cap=CSV_CAP, model=model, state=state)
        tz = ZoneInfo(window.zone)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        personal = payer.kind == "user"
        header = ["time", "call_id"]
        header += ["conversation_id", "conversation_title"] if personal else ["member_user_id", "member"]
        header += ["model", "band", "price_version", "state", "input_cached_tokens", "input_uncached_tokens",
                   "output_tokens", "reasoning_tokens", "charged", "held", "unit", "finish_reason", "duration_s"]
        writer.writerow(header)
        for row in rows:
            tokens = row["tokens"] or {}
            local = datetime.fromtimestamp(row["created_at"], timezone.utc).astimezone(tz).isoformat()
            who = ([row["conversation_id"], row["conversation_title"]] if personal
                   else [row["member_user_id"], row["member_name"]])
            # Every cell goes through neutralize(): the numbers generated here are safe either way,
            # and titles, names, model names and finish reasons come from outside this code.
            writer.writerow([neutralize(value) for value in (
                local, row["id"], *who, row["model"], row["band"], row["price_version"], row["state"],
                tokens.get("input_cached"), tokens.get("input_uncached"), tokens.get("output"),
                tokens.get("reasoning"), decimal_text(int(row["charged_minor"]), row["scale"]),
                decimal_text(int(row["held_minor"]), row["scale"]), row["unit"], row["finish_reason"],
                row["duration_s"])])
        return out.getvalue()
