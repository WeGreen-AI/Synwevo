"""Personal uploaded files; additive schema, no workspace ownership or model credentials."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_files (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 filename TEXT NOT NULL, size INTEGER NOT NULL, content_hash TEXT NOT NULL, content BLOB,
 idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('receiving','queued','parsing','ready','failed','cancelled','deleted')),
 scan_json TEXT, parsed_json TEXT, snapshot_hash TEXT, error_code TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
 UNIQUE(user_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_files_owner ON agent_files(user_id,created_at);
CREATE INDEX IF NOT EXISTS agent_files_queue ON agent_files(status,lease_until);
-- Older account-service code does not know this new table. Keep final erasure atomic even
-- during a rolling deployment or code rollback; pending_delete remains recoverable.
CREATE TRIGGER IF NOT EXISTS agent_files_user_erased
AFTER UPDATE OF status ON users WHEN NEW.status='deleted'
BEGIN DELETE FROM agent_files WHERE user_id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS agent_files_user_removed
AFTER DELETE ON users
BEGIN DELETE FROM agent_files WHERE user_id=OLD.id; END;
"""


def migrate(c):
    # A restored snapshot can predate the trigger while retaining terminal account deletions.
    c.execute("DELETE FROM agent_files WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id=agent_files.user_id AND u.status!='deleted')")
