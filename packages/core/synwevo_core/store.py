"""Persistence: SQLite metadata + content-addressed blob storage on local disk.

Blobs are immutable and named by SHA-256, so source files, template versions and
outputs are never overwritten in place.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import env
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL, updated_at REAL,
  template_id TEXT, record_rule TEXT, output TEXT, mapping_rev INTEGER DEFAULT 0,
  rev INTEGER DEFAULT 0, step TEXT, deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS source_files (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL, ord REAL, blob TEXT, size INTEGER,
  format TEXT, family TEXT, status TEXT, error TEXT, notes TEXT, structure TEXT, signature TEXT,
  parsed_blob TEXT, working_blob TEXT, options TEXT, version INTEGER DEFAULT 1, representative INTEGER DEFAULT 0,
  ocr TEXT, corrections TEXT, created_at REAL, updated_at REAL, removed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_src_project ON source_files(project_id);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT, format TEXT, original_blob TEXT, original_name TEXT,
  head INTEGER DEFAULT 1, created_at REAL, updated_at REAL, deleted INTEGER DEFAULT 0, info TEXT
);
CREATE TABLE IF NOT EXISTS template_versions (
  template_id TEXT NOT NULL, version_no INTEGER NOT NULL, parent_no INTEGER, working_blob TEXT, slots TEXT,
  editor TEXT, note TEXT, created_at REAL, PRIMARY KEY (template_id, version_no)
);
CREATE TABLE IF NOT EXISTS mapping_revs (
  project_id TEXT NOT NULL, rev INTEGER NOT NULL, parent_rev INTEGER, data TEXT, note TEXT, created_at REAL,
  PRIMARY KEY (project_id, rev)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, project_id TEXT, status TEXT, created_at REAL, started_at REAL, finished_at REAL,
  snapshot TEXT, options TEXT, counts TEXT, request_id TEXT UNIQUE, error TEXT, cancel_requested INTEGER DEFAULT 0,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON jobs(project_id);
CREATE TABLE IF NOT EXISTS job_items (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, seq INTEGER, record_key TEXT, record TEXT, status TEXT,
  issues TEXT, attempts INTEGER DEFAULT 0, started_at REAL, finished_at REAL, file_base TEXT, label TEXT
);
CREATE INDEX IF NOT EXISTS ix_items_job ON job_items(job_id, seq);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, item_id TEXT, format TEXT, filename TEXT, blob TEXT, size INTEGER,
  trace TEXT, verification TEXT, created_at REAL, attempt INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_art_job ON artifacts(job_id);
"""

# v2: tenancy, job leases / runs / events, persistent idempotency, API keys, webhook deliveries
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS idempotency (
  tenant TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
  job_id TEXT, created_at REAL NOT NULL, PRIMARY KEY (tenant, scope, key)
);
CREATE TABLE IF NOT EXISTS job_runs (
  job_id TEXT NOT NULL, run_no INTEGER NOT NULL, trigger TEXT NOT NULL, requested_by TEXT, items INTEGER,
  created_at REAL, started_at REAL, finished_at REAL, status TEXT, PRIMARY KEY (job_id, run_no)
);
CREATE TABLE IF NOT EXISTS job_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, job_id TEXT NOT NULL, tenant TEXT,
  type TEXT NOT NULL, version INTEGER NOT NULL, occurred_at REAL NOT NULL, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_job ON job_events(job_id, seq);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, tenant TEXT NOT NULL, scopes TEXT NOT NULL, secret_hash TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL, last_used_at REAL, revoked_at REAL
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id TEXT PRIMARY KEY, event_id TEXT NOT NULL, event_seq INTEGER, endpoint TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER DEFAULT 0, next_attempt_at REAL, last_code INTEGER, last_error TEXT, created_at REAL, delivered_at REAL,
  UNIQUE (event_id, endpoint)
);
CREATE INDEX IF NOT EXISTS ix_wh_due ON webhook_deliveries(status, next_attempt_at);
"""

# v4: identity — accounts, workspaces, memberships, sessions, credentials, invitations, audit.
#
# The one decision that shapes everything here: **a workspace id IS the tenant value**. The
# `tenant` column already exists on projects/templates/jobs and already threads through
# api_keys, idempotency, job_events and the request ContextVar, so reusing it turns machinery
# that is already in place into real isolation instead of adding a second parallel concept.
#
# It also settles the migration. Every pre-v4 row carries tenant='default', so the legacy data
# becomes workspace 'default' with **zero row rewrites** — and because that workspace is created
# with no members, it is invisible to every account by construction. Nobody inherits the old
# files by registering first; a platform admin has to assign them deliberately. That is a
# property of the data model, not a check that some future code path could forget.
#
# Status columns carry CHECK constraints on purpose: DW-049/050/051 were all silent enum
# fallbacks, where an unexpected value quietly became the permissive default. Here the database
# refuses the write instead.
SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  -- email_key is the normalised (casefolded, trimmed) form and carries uniqueness; email keeps
  -- what the person typed, for display. Comparing on the raw form would let Alice@x and
  -- alice@x become two accounts that receive the same mail.
  email TEXT NOT NULL, email_key TEXT NOT NULL UNIQUE,
  email_verified_at REAL,
  display_name TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','active','suspended','pending_delete','deleted')),
  -- platform_role is NOT a superset of workspace access: a platform admin manages accounts and
  -- service state and still has no read access to any workspace's documents (docs/security/permissions.md §4.1).
  platform_role TEXT NOT NULL DEFAULT 'none'
    CHECK (platform_role IN ('none','operator','admin')),
  created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted_at REAL
);

CREATE TABLE IF NOT EXISTS workspaces (
  -- id doubles as the tenant value on projects/templates/jobs/api_keys.
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'personal' CHECK (kind IN ('personal','org','legacy')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','frozen','pending_claim','deleted')),
  created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT, deleted_at REAL
);

CREATE TABLE IF NOT EXISTS memberships (
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('viewer','editor','admin','owner')),
  -- viewers may or may not be allowed to take files off the platform; reading and exporting are
  -- separate permissions (permissions.md §4.2), so this is not implied by the role.
  can_export INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_member_user ON memberships(user_id);

CREATE TABLE IF NOT EXISTS sessions (
  -- id is the SHA-256 of the cookie value; the cookie itself is never stored, so a database
  -- read cannot be replayed as a login.
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  workspace_id TEXT,                      -- the workspace currently selected in this session
  created_at REAL NOT NULL, last_seen_at REAL NOT NULL,
  idle_expires_at REAL NOT NULL, absolute_expires_at REAL NOT NULL,
  -- reauth_at records a *server-side* recent re-authentication. Sensitive actions check this;
  -- a front-end flag would be worthless (permissions.md §4.2).
  reauth_at REAL, reauth_scope TEXT,
  -- amr = which methods were actually used ("password", "password+totp", "passkey_uv").
  -- Needed because "the user has a passkey" is not the same as "this login was phishing-resistant".
  amr TEXT NOT NULL DEFAULT '',
  user_agent TEXT, ip TEXT,
  revoked_at REAL, revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_sess_user ON sessions(user_id, revoked_at);

CREATE TABLE IF NOT EXISTS user_passwords (
  user_id TEXT PRIMARY KEY,
  -- A full PHC string: algorithm and parameters travel with the hash, so parameters can be
  -- raised later and old hashes upgraded on next successful login without a password export.
  phc TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  credential_id TEXT NOT NULL UNIQUE,     -- base64url of the raw credential id
  public_key TEXT NOT NULL, sign_count INTEGER NOT NULL DEFAULT 0,
  transports TEXT, aaguid TEXT, name TEXT,
  -- uv/backup flags decide whether this authenticator satisfies a multi-factor requirement on
  -- its own; they are recorded from the ceremony rather than assumed.
  uv INTEGER NOT NULL DEFAULT 0, backup_eligible INTEGER NOT NULL DEFAULT 0, backup_state INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, last_used_at REAL
);
CREATE INDEX IF NOT EXISTS ix_wa_user ON webauthn_credentials(user_id);

CREATE TABLE IF NOT EXISTS totp_secrets (
  user_id TEXT PRIMARY KEY,
  secret_enc TEXT NOT NULL,               -- encrypted at rest; never returned by any API
  confirmed_at REAL,                      -- NULL until one real code has been verified
  last_step INTEGER,                      -- highest accepted time step, so a code cannot be replayed
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recovery_codes (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  code_hash TEXT NOT NULL,                -- irreversible; nobody, including an admin, can read these back
  created_at REAL NOT NULL, used_at REAL
);
CREATE INDEX IF NOT EXISTS ix_rc_user ON recovery_codes(user_id, used_at);

CREATE TABLE IF NOT EXISTS invitations (
  id TEXT PRIMARY KEY, email_key TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('viewer','editor','admin','owner')),
  can_export INTEGER NOT NULL DEFAULT 0,
  token_hash TEXT NOT NULL UNIQUE,
  invited_by TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL,
  accepted_at REAL, accepted_by TEXT, revoked_at REAL
);
CREATE INDEX IF NOT EXISTS ix_inv_ws ON invitations(workspace_id, accepted_at, revoked_at);

CREATE TABLE IF NOT EXISTS email_tokens (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('verify','reset','email_change')),
  token_hash TEXT NOT NULL UNIQUE,
  new_email TEXT, new_email_key TEXT,     -- only for email_change
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL
);
CREATE INDEX IF NOT EXISTS ix_etok_user ON email_tokens(user_id, kind, used_at);

CREATE TABLE IF NOT EXISTS bootstrap_tokens (
  -- One-shot creation of the first platform admin, from the CLI. Short-lived and single-use, so
  -- it cannot sit around as a permanent master key; no default password exists anywhere.
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL, used_by TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
  at REAL NOT NULL,
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user','machine','system','anonymous')),
  actor_id TEXT, workspace_id TEXT,
  action TEXT NOT NULL, object_kind TEXT, object_id TEXT,
  result TEXT NOT NULL CHECK (result IN ('ok','denied','error')),
  request_id TEXT, ip TEXT,
  -- metadata only: never passwords, tokens, TOTP seeds, recovery codes or document content.
  meta TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_events(at);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_events(actor_id, at);
CREATE INDEX IF NOT EXISTS ix_audit_ws ON audit_events(workspace_id, at);

CREATE TABLE IF NOT EXISTS apps (
  -- A business application on the platform. Right now there is exactly one real one
  -- (DocWeave); the table exists so adding a second needs configuration, not a schema change.
  --
  -- app_id is NOT a client_id and NOT an audience. One app may have several OIDC clients (web,
  -- later a desktop build) that share the app but have their own redirect URIs and secrets.
  id TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT,
  launch_url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  -- 'members' = any member of a workspace that enabled the app; 'explicit' = per-user grant.
  access_policy TEXT NOT NULL DEFAULT 'members' CHECK (access_policy IN ('members','explicit')),
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_apps (
  -- Which apps a workspace has turned on. Separate from membership: being in a workspace does
  -- not by itself mean every app is available in it.
  workspace_id TEXT NOT NULL, app_id TEXT NOT NULL,
  enabled_at REAL NOT NULL, enabled_by TEXT,
  PRIMARY KEY (workspace_id, app_id)
);

CREATE TABLE IF NOT EXISTS app_entitlements (
  -- A person's right to use one app inside one workspace.
  --
  -- Deliberately its own table rather than being inferred from membership: a future second app
  -- must not inherit DocWeave's access just because someone is a workspace admin. Migration
  -- writes these explicitly for existing members so nobody is locked out, and grants nothing
  -- beyond the app they already used.
  workspace_id TEXT NOT NULL, app_id TEXT NOT NULL, user_id TEXT NOT NULL,
  granted_at REAL NOT NULL, granted_by TEXT, revoked_at REAL,
  PRIMARY KEY (workspace_id, app_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_ent_user ON app_entitlements(user_id, revoked_at);

CREATE TABLE IF NOT EXISTS oauth_clients (
  -- OIDC client registration. Managed by CLI and configuration only — there is deliberately no
  -- product UI for this, per the standing constraint on automation surfaces.
  client_id TEXT PRIMARY KEY, app_id TEXT NOT NULL, name TEXT NOT NULL,
  -- NULL for public clients (PKCE only). Confidential clients store a hash, never the secret.
  secret_hash TEXT,
  redirect_uris TEXT NOT NULL,            -- JSON array; matched exactly, never by wildcard
  post_logout_redirect_uris TEXT,         -- JSON array
  backchannel_logout_uri TEXT,
  scopes TEXT NOT NULL DEFAULT 'openid profile email',
  audiences TEXT,                         -- JSON array of APIs this client may be issued tokens for
  token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none'
    CHECK (token_endpoint_auth_method IN ('none','client_secret_basic','client_secret_post')),
  require_pkce INTEGER NOT NULL DEFAULT 1,
  -- 'user': a client that acts on behalf of a person who signed in.
  -- 'service': a machine identity that acts as itself, bound to one workspace, with its own
  -- lifecycle. A person signing out of a browser must not stop a service account's work, and
  -- a service account must not inherit anyone's permissions.
  kind TEXT NOT NULL DEFAULT 'user' CHECK (kind IN ('user','service')),
  workspace_id TEXT,                      -- service accounts only; the tenant they act in
  api_scopes TEXT NOT NULL DEFAULT '',    -- the most this client may ever be granted
  created_at REAL NOT NULL, updated_at REAL NOT NULL, disabled_at REAL
);
CREATE INDEX IF NOT EXISTS ix_client_app ON oauth_clients(app_id);

CREATE TABLE IF NOT EXISTS oauth_codes (
  -- Authorization codes. Short-lived and single-use; `used_at` is set by a conditional UPDATE so
  -- a replayed code cannot be exchanged twice.
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL, scope TEXT NOT NULL, nonce TEXT,
  code_challenge TEXT, code_challenge_method TEXT,
  auth_time REAL NOT NULL, amr TEXT,        -- carried into the id_token: how and when they authenticated
  central_session_id TEXT,                  -- ties the grant to the central session, for logout
  workspace_id TEXT,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  -- Issued access/refresh tokens, stored as hashes so the table cannot be replayed.
  -- Rows persist after revocation so introspection can answer "revoked" rather than "unknown".
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id TEXT,
  access_hash TEXT UNIQUE, refresh_hash TEXT UNIQUE,
  scope TEXT NOT NULL, audience TEXT,
  central_session_id TEXT, workspace_id TEXT,
  issued_at REAL NOT NULL, access_expires_at REAL, refresh_expires_at REAL,
  revoked_at REAL, revoked_reason TEXT,
  -- Refresh rotation lineage. `family_id` is the first token of a grant; presenting a spent
  -- refresh token revokes the whole family, which is what makes replay detectable at all.
  family_id TEXT, parent_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_tok_session ON oauth_tokens(central_session_id, revoked_at);
CREATE INDEX IF NOT EXISTS ix_tok_user ON oauth_tokens(user_id, revoked_at);

CREATE TABLE IF NOT EXISTS oidc_keys (
  -- Signing keys, persisted. Generating a fresh key at startup would invalidate every issued
  -- token on restart, which looks exactly like a mass logout bug.
  kid TEXT PRIMARY KEY, alg TEXT NOT NULL, private_jwk TEXT NOT NULL, public_jwk TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retiring','retired')),
  created_at REAL NOT NULL, retired_at REAL
);

CREATE TABLE IF NOT EXISTS passkeys (
  -- A registered WebAuthn credential. Only public material is stored; the private key never
  -- leaves the authenticator, which is the whole point of the mechanism.
  --
  -- `rp_id` is recorded per credential because a credential is bound to the domain it was
  -- registered under. Changing the account service's domain does not migrate these — they have
  -- to be registered again — and storing the id makes that a visible fact rather than a
  -- surprise at sign-in.
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  credential_id TEXT NOT NULL UNIQUE,     -- base64url, as the browser reports it
  public_key TEXT NOT NULL,               -- base64url COSE key
  sign_count INTEGER NOT NULL DEFAULT 0,  -- monotonic where the authenticator provides it
  transports TEXT,                        -- JSON array, a hint for the next sign-in
  rp_id TEXT NOT NULL,
  backed_up INTEGER NOT NULL DEFAULT 0,   -- a synced credential exists on more than one device
  uv_capable INTEGER NOT NULL DEFAULT 0,  -- user verification was performed at registration
  name TEXT,
  created_at REAL NOT NULL, last_used_at REAL, deleted_at REAL
);

CREATE TABLE IF NOT EXISTS webauthn_challenges (
  -- A challenge in flight. Server-side and single use, because a challenge the client could
  -- choose or replay is not a challenge.
  --
  -- Registration and authentication challenges are kept apart by `purpose` and never satisfy
  -- each other: a registration ceremony signed with an existing credential must not be
  -- accepted as a sign-in, and vice versa.
  id TEXT PRIMARY KEY, user_id TEXT,      -- NULL for a discoverable-credential sign-in
  purpose TEXT NOT NULL CHECK (purpose IN ('register','authenticate')),
  challenge TEXT NOT NULL,                -- base64url
  rp_id TEXT NOT NULL, origin TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL
);

CREATE TABLE IF NOT EXISTS rp_auth_requests (
  -- DocWeave's side of a sign-in in progress. state, nonce and the PKCE verifier live here,
  -- server-side; the browser only carries an opaque id, so none of the three can be supplied by
  -- whoever sent the person to the link.
  id TEXT PRIMARY KEY, state TEXT NOT NULL, nonce TEXT NOT NULL, code_verifier TEXT NOT NULL,
  next_path TEXT,                          -- already reduced to a path on this origin
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL
);

CREATE TABLE IF NOT EXISTS rp_logout_seen (
  -- jti of back-channel logout tokens already applied. The provider retries on failure, so a
  -- repeat must be a no-op success rather than a second (harmless but confusing) revocation.
  jti TEXT PRIMARY KEY, at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS client_sessions (
  -- Links a central session to the per-client sessions it produced, so a platform-wide logout
  -- can reach them. Without this, "sign out everywhere" only clears the portal's own cookie.
  id TEXT PRIMARY KEY, central_session_id TEXT NOT NULL, client_id TEXT NOT NULL,
  user_id TEXT NOT NULL, sid TEXT NOT NULL,        -- the `sid` claim given to the client
  created_at REAL NOT NULL, logged_out_at REAL, logout_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_csess_central ON client_sessions(central_session_id, logged_out_at);

CREATE TABLE IF NOT EXISTS mfa_tickets (
  -- Proof that the password step succeeded, held between the two halves of a sign-in.
  --
  -- Deliberately not a session row with a flag: anything in `sessions` is one bug away from
  -- being loadable as an authenticated session, and a half-finished login must never be.
  -- Short-lived, single-use, and counted — five wrong codes burn it, so a six-digit TOTP cannot
  -- be ground down inside its window.
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL,
  user_agent TEXT, ip TEXT
);

CREATE TABLE IF NOT EXISTS mail_outbox (
  -- Mail is queued here first and sent by a worker, so an invitation is never reported as
  -- "sent" because an API call returned 200. A handler that sends inline has to either block on
  -- a slow SMTP server or lie about the outcome; both are worse than saying "queued" and
  -- showing the real state afterwards.
  id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  kind TEXT, ref_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','dropped')),
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL,
  last_error TEXT, created_at REAL NOT NULL, sent_at REAL
);
CREATE INDEX IF NOT EXISTS ix_mail_due ON mail_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS blob_grants (
  -- Read permission for a blob that no row owns.
  --
  -- Most blobs are reachable from something with a workspace: a source file, a template
  -- version, an artifact. Preview and layout outputs are not — views.py puts them in the store
  -- and hands the id straight to the browser, so there is nothing to check them against. That
  -- is why every /api/preview-files/{blob} route was readable across workspaces: existence on
  -- disk was the entire test.
  --
  -- A grant is recorded when such a blob is produced, for the workspace it was produced in.
  -- Per (blob, tenant) rather than per blob, because content-addressed storage deduplicates:
  -- two workspaces that generate identical bytes share one file and must each hold their own
  -- grant, and revoking one must not revoke the other.
  blob TEXT NOT NULL, tenant TEXT NOT NULL, kind TEXT, created_at REAL NOT NULL,
  PRIMARY KEY (blob, tenant)
);
CREATE INDEX IF NOT EXISTS ix_grant_tenant ON blob_grants(tenant, created_at);

CREATE TABLE IF NOT EXISTS auth_attempts (
  -- Rate limiting and backoff have to survive a restart and be shared across workers, so they
  -- live here rather than in a per-process dict.
  bucket TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0,
  first_at REAL NOT NULL, last_at REAL NOT NULL, blocked_until REAL
);
"""

# columns added to v1 tables (name, table, definition)
V3_COLUMNS = [
    # Provenance: which run created this project.  Without it a cleanup tool cannot prove a
    # row is test data, and deleting by name prefix is not evidence.  Pre-v3 rows keep NULL,
    # which means "unknown origin" and is deliberately NOT treated as test data.
    ("projects", "origin", "TEXT"),
    # Archive is separate from `deleted`: deleted is the soft delete (the project 404s), an
    # archived project is still fully usable, just out of the default list.
    ("projects", "archived_at", "REAL"),
]

# v4's tables are created unconditionally at the end of _migrate (they are pure additions, so
# they need no version bump). Columns added to those tables afterwards therefore need the same
# unconditional treatment: a database already stamped v4 never re-enters the `ver <` branch, so
# putting them in a version-gated list would silently skip every existing install.
V4_COLUMNS = [
    ("oauth_tokens", "family_id", "TEXT"),
    ("oauth_tokens", "parent_id", "TEXT"),
    # A service account is a client that acts as itself, with no user behind it. It is bound to
    # exactly one workspace, because "which tenant is this machine acting in" cannot be answered
    # by a person's membership when there is no person.
    ("oauth_clients", "kind", "TEXT NOT NULL DEFAULT 'user'"),
    ("oauth_clients", "workspace_id", "TEXT"),
    ("oauth_clients", "api_scopes", "TEXT NOT NULL DEFAULT ''"),
    # A DocWeave session created by single sign-on remembers which grant produced it, so a
    # back-channel logout can end exactly that sign-in and no one else's. NULL means the session
    # was created locally (bootstrap, or the legacy direct login), which is why revocation by
    # `sub` is scoped to rows where this is set.
    ("sessions", "oidc_sid", "TEXT"),
    ("sessions", "central_session_id", "TEXT"),
]

V2_COLUMNS = [
    ("projects", "tenant", "TEXT DEFAULT 'default'"),
    ("projects", "template_version", "INTEGER"),
    ("templates", "tenant", "TEXT DEFAULT 'default'"),
    ("jobs", "tenant", "TEXT DEFAULT 'default'"),
    ("jobs", "lease_owner", "TEXT"),
    ("jobs", "lease_until", "REAL"),
    ("jobs", "run_no", "INTEGER DEFAULT 1"),
    ("jobs", "cancel_requested_at", "REAL"),
    ("jobs", "origin", "TEXT DEFAULT 'ui'"),
    ("job_items", "run_no", "INTEGER DEFAULT 1"),
    ("artifacts", "run_no", "INTEGER DEFAULT 1"),
    ("artifacts", "deleted_at", "REAL"),
]

JSON_COLS = {"record_rule", "output", "notes", "structure", "options", "ocr", "corrections", "slots", "editor", "data",
             "snapshot", "counts", "record", "issues", "trace", "verification", "info", "summary"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now() -> float:
    return time.time()


class Store:
    def __init__(self, root: str | Path, migrate: bool = True):
        """Open the store, migrating the schema forward unless told not to.

        ``migrate=False`` is for looking at a data directory without changing it — checking a
        backup's schema version, or reading counts before an upgrade. Opening used to migrate
        unconditionally, which made `manage.py info` (the command the upgrade docs tell you to
        run to *check* the version) silently rewrite a v2 database to v3 in place. Pointed at a
        backup, an inspection command that mutates its subject defeats the backup.
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        self.db_path = self.root / "docweave.sqlite3"
        self._local = threading.local()
        self._write_lock = threading.RLock()     # serialises writers inside this process; SQLite locks across processes
        c = self._c()
        if not migrate:
            return
        c.executescript(SCHEMA)
        self._migrate(c)
        self._stamp_instance_kind(c)

    # ---------------------------------------------------------------- instance identity
    def _stamp_instance_kind(self, c: sqlite3.Connection) -> None:
        """Record once, at creation, whether this data directory belongs to a test run.

        Destructive test tooling refuses to touch an instance that is not marked ``test``.
        The mark is written only while the directory is still empty, so setting
        SYNWEVO_TEST_INSTANCE against a directory that already holds real projects can
        never re-label it - it stays ``normal`` and stays protected."""
        row = c.execute("SELECT value FROM meta WHERE key='instance_kind'").fetchone()
        if row:
            return
        empty = c.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0
        want_test = (env.get("TEST_INSTANCE") or "").strip().lower() in ("1", "true", "yes", "on")
        kind = "test" if (empty and want_test) else "normal"
        c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('instance_kind', ?)", (kind,))

    def instance_kind(self) -> str:
        row = self.one("SELECT value FROM meta WHERE key='instance_kind'")
        return (row or {}).get("value") or "normal"

    # ---------------------------------------------------------------- schema migration
    def _migrate(self, c: sqlite3.Connection) -> None:
        """Forward-only, idempotent migrations.  Existing rows stay readable: new columns have
        defaults, and pre-v2 rows belong to the default tenant."""
        c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1')")
        ver = int(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"])
        if ver < SCHEMA_VERSION:
            # Every migration so far is additive: new tables (idempotent CREATE IF NOT EXISTS)
            # and new columns (guarded by PRAGMA).  Running the whole set on any older version
            # is therefore safe AND necessary — gating on `ver < 2` meant a v2 database never
            # received v3's columns and the app then failed with "no such column".
            c.executescript(SCHEMA_V2)
            c.executescript(SCHEMA_V4)
            with self.tx() as t:
                for table, col, decl in V2_COLUMNS + V3_COLUMNS:
                    have = {r["name"] for r in t.execute(f"PRAGMA table_info({table})").fetchall()}
                    if col not in have:
                        t.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                self._seed_legacy_workspace(t)
                t.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        c.executescript(SCHEMA_V2)                   # new tables of the current version (idempotent)
        c.executescript(SCHEMA_V4)
        with self.tx() as t:
            for table, col, decl in V4_COLUMNS:
                have = {r["name"] for r in t.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in have:
                    t.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    @staticmethod
    def _seed_legacy_workspace(t: sqlite3.Connection) -> None:
        """Give the pre-identity data a workspace to belong to — and no members.

        Every row written before v4 carries tenant='default', and a workspace id *is* a tenant
        value, so one row here adopts all of it without rewriting a single project, template or
        job. The workspace is created with **zero memberships**, which is the whole point:
        access is granted by membership, so until a platform admin deliberately assigns this
        workspace, nobody can reach the historical files — not the first person to register, not
        anyone who guesses a project id. The rule "old data is not handed to whoever signs up
        first" is enforced by the absence of a row rather than by a check.

        status='pending_claim' marks it as awaiting that decision. Skipped when there is nothing
        to adopt, so a fresh install does not get a phantom workspace.
        """
        if t.execute("SELECT 1 FROM workspaces WHERE id='default'").fetchone():
            return
        has_data = any(t.execute(f"SELECT 1 FROM {tbl} LIMIT 1").fetchone() for tbl in ("projects", "templates"))
        if not has_data:
            return
        ts = now()
        t.execute("INSERT INTO workspaces (id, name, kind, status, created_at, updated_at, created_by) "
                  "VALUES ('default', ?, 'legacy', 'pending_claim', ?, ?, NULL)",
                  ("历史数据（待认领）", ts, ts))

    def schema_version(self) -> int:
        """0 means "nothing here yet" — only reachable via ``Store(..., migrate=False)``, which
        may be pointed at an empty or not-yet-initialised directory."""
        try:
            row = self.one("SELECT value FROM meta WHERE key='schema_version'")
        except sqlite3.OperationalError:
            return 0
        return int(row["value"]) if row else 0

    # ---------------------------------------------------------------- connection
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False, isolation_level=None)  # explicit transactions
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _c(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._connect()
            self._local.c = c
            self._local.depth = 0
        return c

    @contextmanager
    def tx(self):
        """A write transaction (``BEGIN IMMEDIATE``): all statements inside commit together or
        not at all.  Re-entrant within a thread; the outermost block commits."""
        c = self._c()
        if self._local.depth:
            self._local.depth += 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        with self._write_lock:
            c.execute("BEGIN IMMEDIATE")
            self._local.depth = 1
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                self._local.depth = 0

    @contextmanager
    def conn(self):
        """Single statements: autocommit (atomic on their own); inside ``tx()`` they join it."""
        yield self._c()

    # ---------------------------------------------------------------- rows
    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in list(d):
            if k in JSON_COLS and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    pass
        return d

    @staticmethod
    def _encode(v):
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return v

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.conn() as c:
            return self._decode(c.execute(sql, args).fetchone())

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn() as c:
            return [self._decode(r) for r in c.execute(sql, args).fetchall()]

    def insert(self, table: str, row: dict) -> None:
        cols = list(row)
        with self.tx() as c:
            c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      tuple(self._encode(row[k]) for k in cols))

    def update(self, table: str, key: dict, values: dict) -> None:
        if not values:
            return
        sets = ",".join(f"{k}=?" for k in values)
        where = " AND ".join(f"{k}=?" for k in key)
        with self.tx() as c:
            c.execute(f"UPDATE {table} SET {sets} WHERE {where}",
                      tuple(self._encode(v) for v in values.values()) + tuple(key.values()))

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, args).rowcount

    # ---------------------------------------------------------------- blobs
    def put_blob(self, data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        p = self.blob_path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp" + secrets.token_hex(3))
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def put_json_blob(self, obj) -> str:
        return self.put_blob(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def blob_path(self, h: str) -> Path:
        if not h or not all(ch in "0123456789abcdef" for ch in h) or len(h) != 64:
            raise ValueError("invalid blob id")
        return self.root / "blobs" / h[:2] / h

    def get_blob(self, h: str) -> bytes:
        return self.blob_path(h).read_bytes()

    def get_json_blob(self, h: str):
        return json.loads(self.get_blob(h).decode("utf-8"))

    def has_blob(self, h: str | None) -> bool:
        return bool(h) and self.blob_path(h).exists()

    # ---------------------------------------------------------------- project rev
    def bump_project(self, pid: str) -> int:
        with self.tx() as c:
            c.execute("UPDATE projects SET rev = rev + 1, updated_at = ? WHERE id = ?", (now(), pid))
            r = c.execute("SELECT rev FROM projects WHERE id = ?", (pid,)).fetchone()
            return r["rev"] if r else 0
