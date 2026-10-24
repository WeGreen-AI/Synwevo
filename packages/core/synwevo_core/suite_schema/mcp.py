"""Model Context Protocol (MCP) on the workplace origin: the Agent's connections to external MCP
servers, what they offer, who may use which tool, and every call made through them. Owner:
`synwevo_suite.agent.mcp` (outbound) and `synwevo_suite.mcp_server` (inbound).

A connection belongs to one person (``owner_kind='user'``) or to one workspace
(``owner_kind='workspace'``; its owners and administrators manage it, its members may use it).
Credentials are sealed (`synwevo_core.sealed`, purpose ``mcp-credential``) and never leave the
server. Discovered tools are recorded with a hash of their schema: a grant names the schema it was
made for, so a server that changes a tool's shape does not keep the old permission (the grant
becomes stale and the tool is unavailable until someone looks at it again).

Everything is additive (``CREATE … IF NOT EXISTS``); older code ignores these tables.
"""

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
