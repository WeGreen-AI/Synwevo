"""Synwevo Agent: a person's own conversations, turns, tool calls, event stream, settings and
memories, plus each workspace's policy on sending its content to an AI provider. Owner:
`synwevo_suite.agent`.

Everything here except ``agent_workspace_policies`` belongs to one user (``user_id``) and is never
a workspace's record: business results the Agent produces live in the application that made them.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    head_id TEXT,
    scope_json TEXT,
    sealed_reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_conversations_user ON agent_conversations(user_id, status, updated_at);

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    parent_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    status TEXT NOT NULL CHECK (status IN ('done','streaming','waiting','failed','cancelled')),
    body TEXT NOT NULL DEFAULT '',
    api_json TEXT,
    sources_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_messages_conversation ON agent_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS agent_messages_parent ON agent_messages(parent_id);

CREATE TABLE IF NOT EXISTS agent_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    assistant_message_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','waiting_confirmation','succeeded','failed','cancelled')),
    provider TEXT,
    credential TEXT,
    model TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_turns_conversation ON agent_turns(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS agent_turns_status ON agent_turns(status, updated_at);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    tool_use_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    operation TEXT,
    workspace_id TEXT,
    input_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_confirmation','declined','running','succeeded','failed','unknown')),
    needs_confirmation INTEGER NOT NULL DEFAULT 0,
    run_id TEXT,
    result_json TEXT,
    for_model_json TEXT,
    error_code TEXT,
    error_message TEXT,
    decided_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_tool_calls_turn ON agent_tool_calls(turn_id, step);
CREATE INDEX IF NOT EXISTS agent_tool_calls_status ON agent_tool_calls(user_id, status);

CREATE TABLE IF NOT EXISTS agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    turn_id TEXT,
    type TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_events_conversation ON agent_events(conversation_id, seq);

CREATE TABLE IF NOT EXISTS agent_settings (
    user_id TEXT PRIMARY KEY,
    effort TEXT NOT NULL DEFAULT 'medium',
    prefer_personal_key INTEGER NOT NULL DEFAULT 0,
    personal_key_sealed TEXT,
    personal_key_hint TEXT,
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_memories_user ON agent_memories(user_id, updated_at);

CREATE TABLE IF NOT EXISTS agent_workspace_policies (
    workspace_id TEXT PRIMARY KEY,
    processing TEXT NOT NULL CHECK (processing IN ('none','platform','any')),
    updated_by TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def migrate(c):
    """Remove credentials from pre-release operation snapshots, preserving replay hashes.

    Runs inside Store's migration transaction. No secret is logged, and the sealed key used
    by the provider is untouched. Rerunning this migration is a no-op after redaction.
    """
    import hashlib
    import json

    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    cursor = c.execute("SELECT id,input FROM op_runs WHERE op='agent.settings.update'")
    while rows := cursor.fetchmany(100):
        for row in rows:
            try:
                inp = json.loads(row[1])
            except (ValueError, TypeError):
                continue
            key = inp.get("personal_key") if isinstance(inp, dict) else None
            if not isinstance(key, str):
                continue
            inp["personal_key"] = {"sha256": hashlib.sha256(encoded(key).encode()).hexdigest()}
            clean = encoded(inp)
            c.execute("UPDATE op_runs SET input=?,input_hash=?,error_message=NULL,error_detail=NULL WHERE id=?",
                      (clean, hashlib.sha256(clean.encode()).hexdigest(), row[0]))
