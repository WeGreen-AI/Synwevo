"""Content safety and the separate model-provider consent of the personal Agent.

* ``agent_provider_consents`` holds, per person and named model provider, the consent version the
  person accepted and whether they withdrew it; ``agent_provider_consent_log`` keeps every
  acceptance and withdrawal (append-only), which the account export returns.
* ``agent_safety_rule_versions`` are the administrators' governed keyword/pattern lists:
  immutable published versions, the newest is in force.
* ``agent_safety_events`` record that a rule (or the provider's own filter) stopped a message:
  which rule, which direction and how long the text was — never the text itself.
* ``agent_message_reports`` are people's reports on an answer; the excerpt is stored only when the
  reporter chose to include it.
* ``agent_access_suspensions`` are administrators' suspensions of one person's Agent access.

All additive; older code ignores these tables.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_provider_consents (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    version TEXT NOT NULL,
    accepted_at REAL NOT NULL,
    withdrawn_at REAL,
    PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS agent_provider_consent_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    version TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('accepted','withdrawn')),
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_provider_consent_log_user ON agent_provider_consent_log(user_id, at);

CREATE TABLE IF NOT EXISTS agent_safety_rule_versions (
    version INTEGER PRIMARY KEY CHECK (typeof(version)='integer' AND version>0),
    rules_json TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS agent_safety_rules_no_update BEFORE UPDATE ON agent_safety_rule_versions
BEGIN SELECT RAISE(ABORT,'immutable_safety_rules'); END;
CREATE TRIGGER IF NOT EXISTS agent_safety_rules_no_delete BEFORE DELETE ON agent_safety_rule_versions
BEGIN SELECT RAISE(ABORT,'immutable_safety_rules'); END;

CREATE TABLE IF NOT EXISTS agent_safety_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    conversation_id TEXT,
    turn_id TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('input','output','provider')),
    rule_version INTEGER,
    rule_id TEXT,
    category TEXT,
    characters INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_safety_events_user ON agent_safety_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS agent_message_reports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT,
    excerpt TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
    created_at REAL NOT NULL,
    resolved_by TEXT,
    resolved_at REAL,
    resolution TEXT,
    UNIQUE (user_id, message_id)
);
CREATE INDEX IF NOT EXISTS agent_message_reports_status ON agent_message_reports(status, created_at);

CREATE TABLE IF NOT EXISTS agent_access_suspensions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    report_id TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    lifted_at REAL,
    lifted_by TEXT,
    lift_reason TEXT
);
CREATE INDEX IF NOT EXISTS agent_access_suspensions_user ON agent_access_suspensions(user_id, lifted_at);
"""
