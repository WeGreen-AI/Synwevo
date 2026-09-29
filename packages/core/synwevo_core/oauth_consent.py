"""A person's consent to an integration acting for them, kept apart from signing in (API-4).

Signing in proves who someone is; letting a client call Synwevo's APIs *as* that person is a
separate decision, and an administrator registering the client does not make it for them
(RFC 9700 §2.4, OpenID Connect Core §3.1.2.4). So an authorization request that asks for anything
beyond the identity scopes (``openid``, ``profile``, ``email``) — API scopes, ``offline_access`` —
needs a stored consent covering those scopes for this person, this client and this workspace.
Without one, the request is parked server-side and the person sees a consent page showing the
client, the scopes in plain words and the workspace; only their approval issues the code.

* **Per workspace.** A delegated token acts in the workspace chosen at authorization; consenting
  for one workspace says nothing about another.
* **Union on re-consent.** Approving more scopes later widens the stored set; the token still gets
  only what that request asked for.
* **Withdrawal revokes.** Withdrawing a consent (the account's "connected apps") revokes every
  token of that client for that person in that workspace in the same transaction, which the API
  checks on every request — no cache to outlive it.
* **Legacy grants.** Delegations that existed before consent was asked for are recorded once as
  ``source='legacy'`` so they show up (and can be withdrawn) instead of silently continuing.

Parked requests live 10 minutes, belong to the central session that made them, and are decided
once. Nothing a client sends after the redirect can widen what the person approved.

Store alone executes ``SCHEMA_SQL`` and runs ``grandfather`` once.
"""
from __future__ import annotations

import json

from .errors import ServiceError
from .store import Store, new_id, now

IDENTITY_SCOPES = frozenset({"openid", "profile", "email"})
REQUEST_TTL_S = 600

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS oauth_consents (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, client_id TEXT NOT NULL, workspace_id TEXT NOT NULL DEFAULT '',
 scopes TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'consent',
 granted_at REAL NOT NULL, updated_at REAL NOT NULL, revoked_at REAL, revoked_reason TEXT,
 UNIQUE (user_id, client_id, workspace_id),
 CHECK (source IN ('consent', 'legacy'))
);
CREATE INDEX IF NOT EXISTS ix_oauth_consents_user ON oauth_consents(user_id, revoked_at);
CREATE TABLE IF NOT EXISTS oauth_consent_requests (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, central_session_id TEXT NOT NULL, client_id TEXT NOT NULL,
 redirect_uri TEXT NOT NULL, state TEXT, scope TEXT NOT NULL, nonce TEXT,
 code_challenge TEXT, code_challenge_method TEXT, workspace_id TEXT,
 auth_time REAL NOT NULL, amr TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 decided_at REAL, decision TEXT
);
"""

GRANDFATHER_KEY = "oauth_consent_legacy_recorded"


def needed(scope: str) -> list[str]:
    """The scopes of a request that need a person's consent (sorted), or [] for sign-in only."""
    return sorted(set((scope or "").split()) - IDENTITY_SCOPES)


def grandfather(c) -> None:
    """Once: record every live delegated grant that predates consent as a legacy consent."""
    if c.execute("SELECT 1 FROM meta WHERE key=?", (GRANDFATHER_KEY,)).fetchone():
        return
    t = now()
    rows = c.execute(
        "SELECT user_id, client_id, COALESCE(workspace_id, '') AS ws, scope, MIN(issued_at) AS first FROM oauth_tokens "
        "WHERE user_id IS NOT NULL AND revoked_at IS NULL AND (refresh_expires_at > ? OR access_expires_at > ?) "
        "GROUP BY user_id, client_id, COALESCE(workspace_id, ''), scope", (t, t)).fetchall()
    merged: dict[tuple, set] = {}
    first: dict[tuple, float] = {}
    for r in rows:
        want = needed(r["scope"])
        if not want:
            continue
        key = (r["user_id"], r["client_id"], r["ws"])
        merged.setdefault(key, set()).update(want)
        first[key] = min(first.get(key, r["first"]), r["first"])
    for (uid, cid, ws), scopes in merged.items():
        c.execute("INSERT OR IGNORE INTO oauth_consents (id, user_id, client_id, workspace_id, scopes, source, granted_at, "
                  "updated_at) VALUES (?,?,?,?,?, 'legacy', ?, ?)",
                  (new_id("cns"), uid, cid, ws, json.dumps(sorted(scopes)), first[(uid, cid, ws)], t))
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (GRANDFATHER_KEY, str(t)))


class Consents:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ stored consent
    def active(self, user_id: str, client_id: str, workspace_id: str | None) -> dict | None:
        row = self.store.one("SELECT * FROM oauth_consents WHERE user_id=? AND client_id=? AND workspace_id=? "
                             "AND revoked_at IS NULL", (user_id, client_id, workspace_id or ""))
        if row:
            row["scopes"] = json.loads(row["scopes"])
        return row

    def covers(self, user_id: str, client_id: str, workspace_id: str | None, scope: str) -> bool:
        want = set(needed(scope))
        if not want:
            return True
        row = self.active(user_id, client_id, workspace_id)
        return bool(row) and want <= set(row["scopes"])

    def grant(self, c, user_id: str, client_id: str, workspace_id: str | None, scopes: list[str]) -> dict:
        """Record (or widen) consent inside the caller's transaction."""
        ws, t = workspace_id or "", now()
        row = c.execute("SELECT * FROM oauth_consents WHERE user_id=? AND client_id=? AND workspace_id=?",
                        (user_id, client_id, ws)).fetchone()
        if row and row["revoked_at"] is None:
            union = sorted(set(json.loads(row["scopes"])) | set(scopes))
            c.execute("UPDATE oauth_consents SET scopes=?, source='consent', updated_at=? WHERE id=?",
                      (json.dumps(union), t, row["id"]))
            return {"id": row["id"], "scopes": union}
        if row:                                           # withdrawn earlier: a fresh consent, not a revival
            c.execute("UPDATE oauth_consents SET scopes=?, source='consent', granted_at=?, updated_at=?, revoked_at=NULL, "
                      "revoked_reason=NULL WHERE id=?", (json.dumps(sorted(scopes)), t, t, row["id"]))
            return {"id": row["id"], "scopes": sorted(scopes)}
        cid = new_id("cns")
        c.execute("INSERT INTO oauth_consents (id, user_id, client_id, workspace_id, scopes, source, granted_at, updated_at) "
                  "VALUES (?,?,?,?,?, 'consent', ?, ?)", (cid, user_id, client_id, ws, json.dumps(sorted(scopes)), t, t))
        return {"id": cid, "scopes": sorted(scopes)}

    def list_for_user(self, user_id: str) -> list[dict]:
        rows = self.store.all(
            "SELECT c.*, o.name AS client_name, o.app_id, w.name AS workspace_name, "
            "(SELECT MAX(issued_at) FROM oauth_tokens t WHERE t.user_id=c.user_id AND t.client_id=c.client_id "
            " AND COALESCE(t.workspace_id,'')=c.workspace_id) AS last_issued_at "
            "FROM oauth_consents c LEFT JOIN oauth_clients o ON o.client_id=c.client_id "
            "LEFT JOIN workspaces w ON w.id=c.workspace_id WHERE c.user_id=? AND c.revoked_at IS NULL "
            "ORDER BY c.updated_at DESC", (user_id,))
        for r in rows:
            r["scopes"] = json.loads(r["scopes"])
        return rows

    def withdraw(self, user_id: str, consent_id: str, reason: str = "user_withdrew") -> dict:
        """Withdraw one consent and revoke that client's tokens for this person in this workspace.

        Journaled first (write-ahead, like every withdrawal): a backup restored later re-applies it."""
        from . import journal
        row = self.store.one("SELECT * FROM oauth_consents WHERE id=? AND user_id=? AND revoked_at IS NULL",
                             (consent_id, user_id))
        if not row:
            raise ServiceError(404, "not_found", "该授权不存在或已撤回")
        journal.record(self.store, "consent_withdrawn", user_id=user_id, client_id=row["client_id"],
                       workspace_id=row["workspace_id"] or None)
        with self.store.tx() as c:
            row = c.execute("SELECT * FROM oauth_consents WHERE id=? AND user_id=? AND revoked_at IS NULL",
                            (consent_id, user_id)).fetchone()
            if not row:
                raise ServiceError(404, "not_found", "该授权不存在或已撤回")
            t = now()
            c.execute("UPDATE oauth_consents SET revoked_at=?, revoked_reason=?, updated_at=? WHERE id=?",
                      (t, reason, t, consent_id))
            n = c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? WHERE user_id=? AND client_id=? "
                          "AND COALESCE(workspace_id,'')=? AND revoked_at IS NULL",
                          (t, "consent_withdrawn", user_id, row["client_id"], row["workspace_id"])).rowcount
        return {"withdrawn": True, "tokens_revoked": n, "client_id": row["client_id"], "workspace_id": row["workspace_id"] or None}

    # ------------------------------------------------------------------ parked requests
    def park(self, *, user_id: str, central_session_id: str, client_id: str, redirect_uri: str, state: str | None,
             scope: str, nonce: str | None, code_challenge: str | None, code_challenge_method: str | None,
             workspace_id: str | None, auth_time: float, amr: str | None) -> str:
        rid, t = new_id("ocr"), now()
        self.store.insert("oauth_consent_requests", {
            "id": rid, "user_id": user_id, "central_session_id": central_session_id, "client_id": client_id,
            "redirect_uri": redirect_uri, "state": state, "scope": scope, "nonce": nonce,
            "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
            "workspace_id": workspace_id, "auth_time": auth_time, "amr": amr, "created_at": t,
            "expires_at": t + REQUEST_TTL_S})
        return rid

    def pending(self, rid: str, *, user_id: str, central_session_id: str, c=None) -> dict:
        """The parked request for this person and session, still undecided and unexpired."""
        q = ("SELECT * FROM oauth_consent_requests WHERE id=? AND user_id=? AND central_session_id=?")
        row = (c.execute(q, (rid, user_id, central_session_id)).fetchone() if c is not None
               else self.store.one(q, (rid, user_id, central_session_id)))
        if not row:
            raise ServiceError(404, "not_found", "授权请求不存在")
        row = dict(row)
        if row["decided_at"] is not None:
            raise ServiceError(409, "consent_decided", "这个授权请求已经处理过了")
        if row["expires_at"] <= now():
            raise ServiceError(410, "consent_expired", "授权请求已过期，请回到原应用重新发起")
        return row

    def decide(self, c, rid: str, decision: str) -> bool:
        """Mark decided exactly once (conditional update); False when someone else decided first."""
        return c.execute("UPDATE oauth_consent_requests SET decided_at=?, decision=? WHERE id=? AND decided_at IS NULL",
                         (now(), decision, rid)).rowcount == 1
