"""Organization payers: an organization pays for a member's Agent calls, within its limits.

Owner decision §6.1: an organization may pay for its members. The member chooses the payer for
a run; the organization decides who may charge it and how much:

* **The organization's grant to the member** (``billing_org_member_grants``): enabled or not, and a
  monthly limit in wallet units (UTC calendar month). Set by someone who currently holds
  ``org.billing.manage`` (owners hold it; others by a custom role). No grant = no organization payer.
* **An optional monthly limit per workspace** (``billing_org_workspace_limits``) for the calls
  attributed to that workspace.
* **Checked where money is reserved**: ``Ledger.reserve_call`` and ``Ledger.dispatch`` call
  ``check`` under their own writer lock, so a withdrawn grant, a member who left, a frozen or
  deleted organization or an exhausted limit refuses the next reservation and the next send.
  Spent (settled sales) plus active holds plus the new hold may not exceed a limit.
* The organization's wallet (``org_wallet``) never goes negative and is frozen by a chargeback
  like a person's.

This module never decides who may *manage* billing; the routes check ``org.billing.manage``
inside the same transaction as the change and write the audit row.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import BillingError, Unit, identifier, integer


def month_start(at: int) -> int:
    day = datetime.fromtimestamp(at, timezone.utc)
    return int(day.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


class OrgPayers:
    def __init__(self, ledger):
        self.ledger, self.store, self.environment = ledger, ledger.store, ledger.environment

    # ------------------------------------------------------------------ reading
    def _grant(self, t, oid, uid):
        return t.execute("SELECT * FROM billing_org_member_grants WHERE environment=? AND organization_id=? AND user_id=? "
                         "ORDER BY revision DESC LIMIT 1", (self.environment, oid, uid)).fetchone()

    def _workspace_limit(self, t, oid, wid):
        return t.execute("SELECT * FROM billing_org_workspace_limits WHERE environment=? AND organization_id=? "
                         "AND workspace_id=? ORDER BY revision DESC LIMIT 1", (self.environment, oid, wid)).fetchone()

    def _exposure(self, t, account, at, *, user_id=None, workspace_id=None) -> tuple[int, int]:
        """(spent this month, held now) of the organization's wallet for one member or workspace."""
        clause, args = ("a.user_id=?", (user_id,)) if user_id is not None else ("a.workspace_id=?", (workspace_id,))
        rows = t.execute(
            "SELECT c.status,c.hold_units,c.created_at,j.amount FROM billing_org_admissions a "
            "JOIN billing_calls c ON c.id=a.call_key LEFT JOIN billing_settlements s ON s.call_key=c.id "
            "LEFT JOIN billing_journal j ON j.id=s.journal_id WHERE c.account_id=? AND " + clause +
            " AND (c.status='reserved' OR (c.status='settled' AND c.created_at>=?))",
            (account, *args, month_start(at))).fetchall()
        held = sum(r["hold_units"] for r in rows if r["status"] == "reserved")
        spent = sum(r["amount"] or 0 for r in rows if r["status"] == "settled")
        return spent, held

    def _member(self, t, oid, uid) -> bool:
        return t.execute("SELECT 1 FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                         "WHERE m.organization_id=? AND m.user_id=? AND u.status='active' AND u.deleted_at IS NULL",
                         (oid, uid)).fetchone() is not None

    def _org_active(self, t, oid) -> bool:
        return t.execute("SELECT 1 FROM organizations WHERE id=? AND status='active' AND deleted_at IS NULL",
                         (oid,)).fetchone() is not None

    # ------------------------------------------------------------------ ledger policy
    def check(self, t, account, context, *, amount, at):
        """Called by the ledger under its writer lock; returns the policy recorded with the call."""
        integer(amount)
        integer(at)
        oid, uid = context.organization_id, context.actor_user_id
        row = t.execute("SELECT kind,owner,unit FROM billing_accounts WHERE id=? AND environment=?",
                        (account, self.environment)).fetchone()
        if not row or row["kind"] != "org_wallet" or row["owner"] != oid:
            raise BillingError("org_payer_inconsistent")
        if not self._org_active(t, oid) or not self._member(t, oid, uid):
            raise BillingError("org_payer_not_member")
        grant = self._grant(t, oid, uid)
        if not grant or not grant["enabled"] or grant["unit"] != row["unit"]:
            raise BillingError("org_payer_not_granted")
        spent, held = self._exposure(t, account, at, user_id=uid)
        if spent + held + amount > grant["monthly_limit"]:
            raise BillingError("org_member_monthly_limit_exceeded")
        workspace_revision = None
        if context.workspace_id is not None:
            limit = self._workspace_limit(t, oid, context.workspace_id)
            if limit is not None:
                workspace_revision = limit["revision"]
                if limit["monthly_limit"] is not None:
                    spent, held = self._exposure(t, account, at, workspace_id=context.workspace_id)
                    if spent + held + amount > limit["monthly_limit"]:
                        raise BillingError("org_workspace_monthly_limit_exceeded")
        return {"organization_id": oid, "user_id": uid, "workspace_id": context.workspace_id,
                "grant_revision": grant["revision"], "workspace_revision": workspace_revision}

    def record(self, t, call_key, policy):
        t.execute("INSERT INTO billing_org_admissions(call_key,environment,organization_id,user_id,workspace_id,"
                  "grant_revision,workspace_revision) VALUES (?,?,?,?,?,?,?)",
                  (call_key, self.environment, policy["organization_id"], policy["user_id"], policy["workspace_id"],
                   policy["grant_revision"], policy["workspace_revision"]))

    # ------------------------------------------------------------------ management (caller authorizes)
    def _transaction(self):
        with self.store.conn() as t:
            if not t.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("organization payer changes require an existing Store.tx()")
            return t

    def set_member(self, oid: str, uid: str, *, unit: Unit, enabled: bool, monthly_limit: int,
                   expected_revision: int, actor_id: str) -> dict:
        t = self._transaction()
        identifier(oid), identifier(uid), identifier(actor_id)
        integer(monthly_limit)
        integer(expected_revision)
        if type(enabled) is not bool:
            raise BillingError("invalid_org_payer")
        if not self._org_active(t, oid):
            raise BillingError("organization_unavailable")
        if enabled and not self._member(t, oid, uid):
            raise BillingError("org_payer_not_member")
        old = self._grant(t, oid, uid)
        if (old["revision"] if old else 0) != expected_revision:
            raise BillingError("org_payer_conflict")
        t.execute("INSERT INTO billing_org_member_grants(environment,organization_id,user_id,revision,enabled,unit,"
                  "monthly_limit,actor_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (self.environment, oid, uid, expected_revision + 1, int(enabled), unit.code, monthly_limit,
                   actor_id, self.ledger._now()))
        return dict(self._grant(t, oid, uid))

    def set_workspace(self, oid: str, wid: str, *, unit: Unit, monthly_limit: int | None,
                      expected_revision: int, actor_id: str) -> dict:
        t = self._transaction()
        identifier(oid), identifier(wid), identifier(actor_id)
        integer(expected_revision)
        if monthly_limit is not None:
            integer(monthly_limit)
        if not self._org_active(t, oid):
            raise BillingError("organization_unavailable")
        if not t.execute("SELECT 1 FROM workspaces WHERE id=? AND organization_id=?", (wid, oid)).fetchone():
            raise BillingError("org_workspace_unknown")
        old = self._workspace_limit(t, oid, wid)
        if (old["revision"] if old else 0) != expected_revision:
            raise BillingError("org_payer_conflict")
        t.execute("INSERT INTO billing_org_workspace_limits(environment,organization_id,workspace_id,revision,unit,"
                  "monthly_limit,actor_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (self.environment, oid, wid, expected_revision + 1, unit.code, monthly_limit, actor_id, self.ledger._now()))
        return dict(self._workspace_limit(t, oid, wid))

    # ------------------------------------------------------------------ projections
    def _account(self, t, oid, unit):
        row = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind='org_wallet'",
                        (self.environment, unit.code, oid)).fetchone()
        return row["id"] if row else None

    def payers_for(self, uid: str, unit: Unit) -> list[dict]:
        """The organizations this person may charge now, each with what is left this month."""
        now = self.ledger._now()
        out = []
        with self.store.tx() as t:
            rows = t.execute("SELECT DISTINCT g.organization_id FROM billing_org_member_grants g WHERE g.environment=? "
                             "AND g.user_id=? AND g.unit=?", (self.environment, uid, unit.code)).fetchall()
            for row in rows:
                oid = row["organization_id"]
                grant = self._grant(t, oid, uid)
                if not grant or not grant["enabled"] or not self._org_active(t, oid) or not self._member(t, oid, uid):
                    continue
                name = t.execute("SELECT name FROM organizations WHERE id=?", (oid,)).fetchone()["name"]
                account = self._account(t, oid, unit)
                booked = held_all = 0
                spent = held = 0
                frozen = False
                if account:
                    booked, held_all = self.ledger._balance(t, account)
                    spent, held = self._exposure(t, account, now, user_id=uid)
                    frozen = self.ledger._frozen(t, account)
                out.append({"organization_id": oid, "name": name, "grant_revision": grant["revision"],
                            "monthly_limit": grant["monthly_limit"], "spent_this_month": spent, "held": held,
                            "remaining_this_month": max(0, grant["monthly_limit"] - spent - held),
                            "wallet_available": booked - held_all, "frozen": frozen})
        return out

    def summary(self, oid: str, unit: Unit) -> dict:
        """For a billing manager: the wallet, every member grant and workspace limit, this month's use."""
        now = self.ledger._now()
        with self.store.tx() as t:
            account = self._account(t, oid, unit)
            booked = held_all = 0
            frozen = False
            if account:
                booked, held_all = self.ledger._balance(t, account)
                frozen = self.ledger._frozen(t, account)
            members = []
            for row in t.execute("SELECT m.user_id,m.role,u.email,u.display_name FROM organization_memberships m "
                                 "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.deleted_at IS NULL "
                                 "ORDER BY u.email", (oid,)).fetchall():
                grant = self._grant(t, oid, row["user_id"])
                spent, held = self._exposure(t, account, now, user_id=row["user_id"]) if account else (0, 0)
                members.append({"user_id": row["user_id"], "email": row["email"], "display_name": row["display_name"],
                                "role": row["role"], "revision": grant["revision"] if grant else 0,
                                "enabled": bool(grant and grant["enabled"]),
                                "monthly_limit": grant["monthly_limit"] if grant else 0,
                                "spent_this_month": spent, "held": held})
            workspaces = []
            for row in t.execute("SELECT id,name FROM workspaces WHERE organization_id=? ORDER BY name", (oid,)).fetchall():
                limit = self._workspace_limit(t, oid, row["id"])
                spent, held = self._exposure(t, account, now, workspace_id=row["id"]) if account else (0, 0)
                workspaces.append({"workspace_id": row["id"], "name": row["name"],
                                   "revision": limit["revision"] if limit else 0,
                                   "monthly_limit": limit["monthly_limit"] if limit else None,
                                   "spent_this_month": spent, "held": held})
            return {"organization_id": oid, "unit": {"code": unit.code, "scale": unit.scale},
                    "wallet": {"booked": booked, "held": held_all, "available": booked - held_all, "frozen": frozen},
                    "month_start": month_start(now), "members": members, "workspaces": workspaces}
