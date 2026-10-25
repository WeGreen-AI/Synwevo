"""Model Context Protocol (MCP) on the workplace origin: the Agent's connections to external MCP
servers, what they offer, who may use which tool, and every call made through them. Owner:
`synwevo_suite.agent.mcp` (outbound) and `synwevo_suite.mcp_server` (inbound).

A connection belongs to one person (``owner_kind='user'``) or to one workspace
(``owner_kind='workspace'``; its owners and administrators manage it, its members may use it).
Credentials are sealed (`synwevo_core.sealed`, purpose ``mcp-credential``) and never leave the
server. Discovered tools are recorded with a hash of their schema: a grant names the schema it was
made for, so a server that changes a tool's shape does not keep the old permission (the grant
becomes stale and the tool is unavailable until someone looks at it again).

Everything is additive (``CREATE … IF NOT EXISTS``, guarded ``ADD COLUMN``); older code ignores
these tables and columns.

`settle_interrupted_calls` is here, beside the tables, because two readers of the call log must
agree on when a 'running' record can no longer be in flight: the connection service
(`synwevo_suite.agent.mcp`) and the platform administration's masked view
(`synwevo_identity.admin_mcp`), which cannot import an application.
"""
from __future__ import annotations

import time

#: A call record still 'running' this long after it started cannot still be in flight: no exchange
#: outlives the longest deadline the policy allows (``call_timeout_s`` ≤ 600 s, plus the runtime's
#: 5 s of slack and a token refresh before it, ≤ 40 s, and the wait for another caller's refresh).
#: Whatever process wrote it stopped before it could write the end (a crash, a restart).
STALE_RUNNING_S = 900
INTERRUPTED_CODE = "mcp_call_interrupted"
INTERRUPTED_MESSAGE = "调用进行中时服务停止或重启，结果未知"


def settle_interrupted_calls(store, *, connection_id: str | None = None, at: float | None = None) -> int:
    """Mark the call records left 'running' by a stopped process as 'unknown' — never as success or
    failure, since nobody knows whether the external server acted. `store` is a `Store` (or anything
    with its ``execute(sql, args)``); returns how many records were settled."""
    t = time.time() if at is None else at
    sql = ("UPDATE mcp_calls SET status='unknown', error_code=?, error_message=?, finished_at=? "
           "WHERE status='running' AND started_at<?")
    args: tuple = (INTERRUPTED_CODE, INTERRUPTED_MESSAGE, t, t - STALE_RUNNING_S)
    if connection_id is not None:
        sql += " AND connection_id=?"
        args += (connection_id,)
    n = store.execute(sql, args)
    return n if isinstance(n, int) else getattr(n, "rowcount", 0)


SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_connections (
    id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('user','workspace')),
    owner_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    auth_kind TEXT NOT NULL CHECK (auth_kind IN ('none','bearer','oauth')),
    credential_sealed TEXT,
    credential_hint TEXT,
    oauth_client_json TEXT,
    oauth_client_secret_sealed TEXT,
    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
    health TEXT NOT NULL DEFAULT 'unknown' CHECK (health IN ('unknown','ok','failed','auth_required')),
    health_checked_at REAL,
    last_ok_at REAL,
    last_error_code TEXT,
    last_error_message TEXT,
    failure_streak INTEGER NOT NULL DEFAULT 0,
    open_until REAL,
    protocol_version TEXT,
    server_info_json TEXT,
    capabilities_json TEXT,
    instructions TEXT,
    discovered_at REAL,
    discovery_hash TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS mcp_connections_owner_slug
    ON mcp_connections(owner_kind, owner_id, slug) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS mcp_connections_owner ON mcp_connections(owner_kind, owner_id, status);

CREATE TABLE IF NOT EXISTS mcp_connection_tools (
    connection_id TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT,
    input_schema_json TEXT NOT NULL,
    output_schema_json TEXT,
    annotations_json TEXT,
    schema_hash TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    removed_at REAL,
    PRIMARY KEY (connection_id, name)
);

CREATE TABLE IF NOT EXISTS mcp_connection_resources (
    connection_id TEXT NOT NULL,
    uri TEXT NOT NULL,
    name TEXT,
    title TEXT,
    description TEXT,
    mime_type TEXT,
    size INTEGER,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    removed_at REAL,
    PRIMARY KEY (connection_id, uri)
);

CREATE TABLE IF NOT EXISTS mcp_connection_prompts (
    connection_id TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT,
    arguments_json TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    removed_at REAL,
    PRIMARY KEY (connection_id, name)
);

-- Who may let the Agent use a tool, and how: 'off' never, 'confirm' only after the person
-- confirms the exact call, 'auto' without asking (allowed only for a tool the server marks
-- read-only). schema_hash names the schema the grant was made for.
CREATE TABLE IF NOT EXISTS mcp_tool_grants (
    connection_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('off','confirm','auto')),
    schema_hash TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (connection_id, tool)
);

-- A person's choice to let their Agent use a connection, under an alias unique to them; per-tool
-- modes here may only narrow the connection's grants, never widen them.
CREATE TABLE IF NOT EXISTS mcp_agent_bindings (
    user_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    resources_enabled INTEGER NOT NULL DEFAULT 0,
    tool_modes_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, connection_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS mcp_agent_bindings_alias ON mcp_agent_bindings(user_id, alias);

-- Every call made through a connection: who, through which Agent turn, which tool, how long, how
-- it ended. Inputs and results are kept as bounded previews; secrets never enter them.
CREATE TABLE IF NOT EXISTS mcp_calls (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    user_id TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('agent','test','discover','page')),
    kind TEXT NOT NULL CHECK (kind IN ('tools/call','resources/read','prompts/get','discover')),
    name TEXT,
    conversation_id TEXT,
    turn_id TEXT,
    tool_call_id TEXT,
    request_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled','timeout','unknown')),
    input_hash TEXT,
    input_preview TEXT,
    result_preview TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS mcp_calls_connection ON mcp_calls(connection_id, started_at);
CREATE INDEX IF NOT EXISTS mcp_calls_user ON mcp_calls(user_id, started_at);
CREATE INDEX IF NOT EXISTS mcp_calls_tool_call ON mcp_calls(tool_call_id);
-- The records still in flight, for settle_interrupted_calls.
CREATE INDEX IF NOT EXISTS mcp_calls_running ON mcp_calls(started_at) WHERE status='running';

-- An authorization-code flow in progress with an external MCP server's authorization server.
CREATE TABLE IF NOT EXISTS mcp_oauth_flows (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    code_verifier_sealed TEXT NOT NULL,
    issuer TEXT NOT NULL,
    authorization_endpoint TEXT NOT NULL,
    token_endpoint TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scope TEXT,
    resource TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    iss_supported INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    completed_at REAL,
    outcome TEXT
);

-- The platform's MCP policy, one complete snapshot per revision (synwevo_core.mcp_policy).
CREATE TABLE IF NOT EXISTS mcp_policy_versions (
    revision INTEGER PRIMARY KEY,
    spec_json TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    operation_id TEXT,
    created_at REAL NOT NULL
);
"""


COLUMNS = (
    # A platform administrator's stop (synwevo_identity.admin_mcp): set, the connection is unusable
    # and its owner cannot resume it; only the platform administration lifts it.
    ("mcp_connections", "platform_disabled_at", "REAL"),
    ("mcp_connections", "platform_disabled_by", "TEXT"),
    # What the circuit breaker decides on (JSON: {"since": the streak at the last opening, "by": {user id:
    # failures since}}): one member alone cannot open a shared connection's circuit for everyone, nor
    # re-open it each time the window ends (Connections._health).
    ("mcp_connections", "failure_users_json", "TEXT"),
    # A lease on refreshing the connection's OAuth token, so that concurrent callers in any process
    # do not spend the same refresh token twice (Connections.credential); a change that voids the
    # authorization clears it, which fences off the result of a refresh under way (store_tokens).
    ("mcp_connections", "refresh_claim_until", "REAL"),
    # Why a discovered tool cannot be granted (a stable code, e.g. 'mcp_schema_too_large'), NULL when
    # it can: its schema was too large to keep or to give a model, or the connection's offer had no
    # more room for it ('mcp_offer_too_large'), so the row holds a placeholder in its place and the
    # tool reaches no Agent (Connections._record_discovery).
    ("mcp_connection_tools", "unusable_code", "TEXT"),
)
