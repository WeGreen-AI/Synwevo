"""Is the party that asked for this work still allowed to have it?

A request is authorised when it arrives. Work outlives the request: a job queues, runs for an
hour, gets retried next week; a webhook is retried for days; a download link is handed to
someone else. Each of those later moments has to ask the question again, about the *present*,
because the answer changes — a key is revoked because it leaked, a person leaves a workspace, a
workspace is frozen. Freezing the answer into the job at submission time is exactly how a
revoked credential keeps delivering results.

So the thing that is stored with deferred work is a **descriptor** of who asked — small,
JSON-serialisable, containing identifiers only — and `check()` re-derives authority from live
rows every time it is consulted.

What each kind of principal means, and what ends it:

* ``api_key`` — a legacy ``dw_`` key. Its authority belongs to the key's **lineage**, not to one
  key: rotating a key (``apikey rotate``) keeps the lineage, so the automation's queued jobs are
  unaffected; revoking it (a security action) ends the lineage and with it the jobs.
* ``service_account`` — a platform client acting as itself in one workspace. Rotating its secret
  changes nothing here; disabling the client or the workspace ends it. A person signing out of a
  browser never does.
* ``delegated`` — a client acting for a person. It needs the person's *current* membership and
  role, the application entitlement, and a grant that has not been revoked (a refresh rotation is
  not a revocation).
* ``user`` — a person working in the browser. Their current membership, role and entitlement.
  Signing out does not cancel work they queued; losing access does.
* ``operator`` — the pre-identity browser modes (``none``/``basic``/``proxy``), where the
  operator's own front door is the only authority there is. Always allowed, as before.

Jobs created before this module existed carry no descriptor and are treated as ``operator``: they
were authorised under the old rules and rejecting them on upgrade would strand work in flight.
"""
from __future__ import annotations

import json

from .store import Store, now

WRITE_ROLES = frozenset({"owner", "admin", "editor"})
READ_ROLES = WRITE_ROLES | {"viewer"}
LEGACY_WRITE = {"write", "admin"}

# Workspace states in which nothing may run. `pending_claim` is the unclaimed legacy workspace:
# nobody can reach it through membership, but operator-issued legacy keys and the operator's own
# browser modes predate the platform and keep working there.
BLOCKED_WORKSPACE_STATES = frozenset({"frozen", "deleted"})


# --------------------------------------------------------------------- descriptors
def describe(principal: dict | None = None, *, user: dict | None = None, tenant: str | None = None) -> dict:
    """The descriptor to store with deferred work.

    `principal` is what `MachineAuth.verify` returned for an /api/v1 call; `user` is the
    browser session's user. Neither means one of the operator modes.
    """
    if principal:
        kind = principal.get("kind") or "api_key"
        t = principal.get("tenant") or tenant
        if kind == "api_key":
            return {"kind": "api_key", "key_id": principal.get("key_id"),
                    "lineage": principal.get("lineage") or principal.get("key_id"), "tenant": t}
        if kind == "service_account":
            return {"kind": "service_account", "client_id": principal.get("client_id"), "tenant": t}
        if kind == "delegated":
            return {"kind": "delegated", "client_id": principal.get("client_id"),
                    "user_id": principal.get("user_id"), "family_id": principal.get("grant_family"),
                    "tenant": t}
        return {"kind": kind, "tenant": t}
    if user:
        return {"kind": "user", "user_id": user.get("id"), "tenant": tenant}
    return {"kind": "operator", "tenant": tenant}


def describe_request(request) -> dict:
    """Descriptor for whoever made this request, as resolved by the platform gate."""
    from .context import tenant as current_tenant
    state = request.state
    principal = getattr(state, "principal", None)
    if principal:
        return describe(principal, tenant=current_tenant())
    return describe(user=getattr(state, "user", None), tenant=current_tenant())


def dumps(desc: dict | None) -> str | None:
    return json.dumps(desc, ensure_ascii=False, sort_keys=True) if desc is not None else None


def loads(raw) -> dict | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {"kind": "invalid"}
    except (TypeError, ValueError):
        # Only an absent pre-migration descriptor means operator. Corrupt persisted data
        # must not erase the submitting identity and acquire that compatibility exception.
        return {"kind": "invalid"}


def label(desc: dict | None) -> str:
    """A short, secret-free label for logs, audit and job records (e.g. ``api:ab12cd34ef56``)."""
    d = desc or {}
    k = d.get("kind")
    if k == "api_key":
        return f"api:{d.get('key_id')}"
    if k == "service_account":
        return f"service:{d.get('client_id')}"
    if k == "delegated":
        return f"delegated:{d.get('client_id')}:{d.get('user_id')}"
    if k == "user":
        return f"user:{d.get('user_id')}"
    return "operator"


# --------------------------------------------------------------------- the check
def check(store: Store, desc: dict | None, *, need: str = "write", app_id: str = "docweave",
          tenant: str | None = None) -> tuple[bool, str | None]:
    """(allowed, reason). `need` is ``read``, ``write`` or ``export``.

    `tenant` is the workspace the work belongs to; when given it must match the descriptor's,
    so a descriptor can never be replayed against another workspace's objects.
    """
    if need not in ("read", "write", "export"):
        return False, "scope_withdrawn"
    d = {"kind": "operator"} if desc is None else desc
    if not isinstance(d, dict):
        return False, "unknown_principal"
    kind = d.get("kind")
    t = d.get("tenant")
    if tenant is not None and t is not None and t != tenant:
        return False, "workspace_mismatch"
    t = t if t is not None else tenant

    if kind == "operator":
        return True, None

    ws = store.one("SELECT id, status FROM workspaces WHERE id=?", (t,)) if t else None
    if ws and ws["status"] in BLOCKED_WORKSPACE_STATES:
        return False, f"workspace_{ws['status']}"
    from .organizations import workspace_access_failure
    why = workspace_access_failure(store, t, d.get("user_id") if kind in ("user", "delegated") else None)
    if why:
        return False, why
    if kind in ("api_key", "service_account"):
        from .api_platform import approval_failure
        why = approval_failure(store, d, need=need)
        if why:
            return False, why

    if kind == "api_key":
        return _check_api_key(store, d, need, ws, app_id)
    if kind == "service_account":
        return _check_service(store, d, need, ws, app_id)
    if kind in ("delegated", "user"):
        return _check_person(store, d, need, ws, app_id)
    return False, "unknown_principal"


def _app_live(store: Store, app_id: str, workspace_id: str) -> str | None:
    app = store.one("SELECT status FROM apps WHERE id=?", (app_id,))
    if app and app["status"] != "active":
        return "app_disabled"
    if not store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?", (workspace_id, app_id)):
        return "app_not_enabled_for_workspace"
    return None


def _check_api_key(store, d, need, ws, app_id):
    lineage = d.get("lineage") or d.get("key_id")
    t = now()
    rows = store.all("SELECT id, tenant, scopes, expires_at, revoked_at FROM api_keys "
                     "WHERE id=? OR lineage=?", (lineage, lineage))
    live = [r for r in rows if not r["revoked_at"] and not (r["expires_at"] and r["expires_at"] <= t)]
    if not live:
        return False, "credential_revoked"
    if d.get("tenant") and all(r["tenant"] != d["tenant"] for r in live):
        return False, "workspace_mismatch"
    live = [r for r in live if r["tenant"] == d.get("tenant")]
    required = LEGACY_WRITE if need == "write" else {"read", "admin"}
    if not any(required & set((r["scopes"] or "").split(",")) for r in live):
        return False, "scope_withdrawn"
    # The application being switched off platform-wide stops every caller, legacy keys included;
    # the per-workspace enablement is only meaningful for workspaces that are live.
    app = store.one("SELECT status FROM apps WHERE id=?", (app_id,))
    if app and app["status"] != "active":
        return False, "app_disabled"
    if ws and ws["status"] == "active":
        why = _app_live(store, app_id, ws["id"])
        if why:
            return False, why
    return True, None


def _check_service(store, d, need, ws, app_id):
    c = store.one("SELECT client_id, kind, workspace_id, api_scopes, disabled_at, app_id FROM oauth_clients "
                  "WHERE client_id=?", (d.get("client_id"),))
    if not c or c["disabled_at"]:
        return False, "credential_revoked"
    if c["app_id"] != app_id:
        return False, "wrong_audience"
    if c["kind"] != "service" or c["workspace_id"] != d.get("tenant"):
        return False, "workspace_mismatch"
    if not ws or ws["status"] != "active":
        return False, "workspace_unavailable"
    why = _app_live(store, app_id, ws["id"])
    if why:
        return False, why
    required = "docweave.write" if need == "write" else "docweave.read"
    if required not in (c["api_scopes"] or "").split():
        return False, "scope_withdrawn"
    return True, None


def _check_person(store, d, need, ws, app_id):
    uid = d.get("user_id")
    user = store.one("SELECT status FROM users WHERE id=?", (uid,))
    if not user or user["status"] != "active":
        return False, "account_unavailable"
    if not ws or ws["status"] != "active":
        return False, "workspace_unavailable"
    m = store.one("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?", (ws["id"], uid))
    if not m:
        return False, "not_a_member"
    if m["role"] not in (WRITE_ROLES if need == "write" else READ_ROLES):
        return False, "role_insufficient"
    if need == "export" and m["role"] not in WRITE_ROLES and not m["can_export"]:
        return False, "export_not_allowed"
    why = _app_live(store, app_id, ws["id"])
    if why:
        return False, why
    from .apps import entitlement_state
    ent = entitlement_state(store, ws["id"], app_id, uid)
    if ent == "revoked":
        return False, "entitlement_revoked"
    app = store.one("SELECT access_policy FROM apps WHERE id=?", (app_id,))
    if ent != "granted" and (app or {}).get("access_policy") != "members":
        return False, "no_entitlement"
    if d.get("kind") == "delegated":
        from .oidc import Clients
        c = store.one("SELECT disabled_at, app_id, scopes, api_scopes FROM oauth_clients WHERE client_id=?",
                      (d.get("client_id"),))
        if not c or c["disabled_at"]:
            return False, "credential_revoked"
        if c["app_id"] != app_id:
            # A delegation minted for another application is not authority over this one.
            return False, "wrong_audience"
        required = "docweave.write" if need == "write" else "docweave.read"
        if required not in Clients.allowed_scopes(c):
            return False, "scope_withdrawn"
        fam = d.get("family_id")
        if not fam:
            return False, "grant_revoked"
        last = store.one("SELECT revoked_at, revoked_reason, scope FROM oauth_tokens "
                         "WHERE (family_id=? OR id=?) AND client_id=? AND user_id=? AND workspace_id=? "
                         "ORDER BY issued_at DESC, (revoked_at IS NULL) DESC LIMIT 1",
                         (fam, fam, d.get("client_id"), uid, ws["id"]))
        if not last or (last["revoked_at"] and (last["revoked_reason"] or "") != "rotated"):
            return False, "grant_revoked"
        if required not in (last["scope"] or "").split():
            return False, "scope_withdrawn"
    return True, None
