"""Wallet top-ups through a payment provider (org-payments, 2026-10-05). Additive tables only.

* ``payment_settings_versions`` — immutable, versioned non-secret settings per billing
  environment (enabled, operator name, server-priced packages, amount bounds, velocity limits),
  with who changed them, why and when.
* ``payment_credentials`` — the provider secret key and webhook signing secret, sealed
  (``synwevo_core.sealed`` purpose ``platform-payment-key``); never selected into a response.
* ``payment_webhook_inbox`` — every signature-verified provider event, once (provider, event id):
  a whitelisted fact subset (no customer name, address or card data) and the SHA-256 of the raw
  payload. The ledger's payment and dispute verifiers read it inside their own transaction.
* ``payment_topups`` — a top-up order's lifecycle (creating → pending → paid | failed | expired).
  Money is never here: the immutable ``billing_orders`` row and the ledger journal are the facts.

Receipt times (review payments-3, 2026-10-05): ``payment_webhook_inbox.provider_created_at`` is
the signed event's own ``created`` time (when the provider recorded it), set once and then fixed.
A credit takes ``payment_topups.paid_at`` from it; ``credited_at`` stays the processing time.

Rollback: older code never reads these tables; credited payments stay in the ledger.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_settings_versions (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    spec_json TEXT NOT NULL,
    credential_revision INTEGER NOT NULL CHECK(credential_revision>=0),
    credential_configured INTEGER NOT NULL CHECK(credential_configured IN (0,1)),
    actor_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,revision)
);
CREATE TRIGGER IF NOT EXISTS payment_settings_no_update BEFORE UPDATE ON payment_settings_versions
BEGIN SELECT RAISE(ABORT,'immutable_payment_settings'); END;
CREATE TRIGGER IF NOT EXISTS payment_settings_no_delete BEFORE DELETE ON payment_settings_versions
BEGIN SELECT RAISE(ABORT,'immutable_payment_settings'); END;
CREATE TRIGGER IF NOT EXISTS payment_settings_no_replace BEFORE INSERT ON payment_settings_versions
WHEN EXISTS(SELECT 1 FROM payment_settings_versions
 WHERE (environment=NEW.environment AND revision=NEW.revision) OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_payment_settings'); END;
CREATE TABLE IF NOT EXISTS payment_credentials (
    environment TEXT PRIMARY KEY CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    secret_enc TEXT, webhook_secret_enc TEXT
);
CREATE TABLE IF NOT EXISTS payment_webhook_inbox (
    provider TEXT NOT NULL, event_id TEXT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    type TEXT NOT NULL, payload_sha256 TEXT NOT NULL, facts_json TEXT NOT NULL,
    signature_verified INTEGER NOT NULL CHECK(signature_verified=1),
    received_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('received','processed','ignored','rejected','failed')),
    result_code TEXT, processed_at INTEGER,
    PRIMARY KEY(provider,event_id)
);
CREATE INDEX IF NOT EXISTS payment_inbox_status ON payment_webhook_inbox(environment,status,received_at);
CREATE TRIGGER IF NOT EXISTS payment_inbox_facts_fixed BEFORE UPDATE ON payment_webhook_inbox
WHEN NEW.provider<>OLD.provider OR NEW.event_id<>OLD.event_id OR NEW.environment<>OLD.environment
 OR NEW.type<>OLD.type OR NEW.payload_sha256<>OLD.payload_sha256 OR NEW.facts_json<>OLD.facts_json
 OR NEW.signature_verified<>OLD.signature_verified OR NEW.received_at<>OLD.received_at
BEGIN SELECT RAISE(ABORT,'immutable_payment_evidence'); END;
CREATE TRIGGER IF NOT EXISTS payment_inbox_no_delete BEFORE DELETE ON payment_webhook_inbox
BEGIN SELECT RAISE(ABORT,'immutable_payment_evidence'); END;
CREATE TABLE IF NOT EXISTS payment_topups (
    order_id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    provider TEXT NOT NULL,
    payer_kind TEXT NOT NULL CHECK(payer_kind IN ('user','organization')), payer_id TEXT NOT NULL,
    initiator_id TEXT NOT NULL REFERENCES users(id), package_id TEXT,
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>0),
    currency TEXT NOT NULL, unit TEXT NOT NULL,
    credit_units INTEGER NOT NULL CHECK(typeof(credit_units)='integer' AND credit_units>0),
    settings_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('creating','pending','paid','failed','expired')),
    checkout_session_id TEXT UNIQUE, checkout_url TEXT, payment_id TEXT, method TEXT,
    created_at INTEGER NOT NULL, expires_at INTEGER, paid_at INTEGER, credited_at INTEGER,
    journal_id TEXT, failure_code TEXT
);
CREATE INDEX IF NOT EXISTS payment_topups_initiator ON payment_topups(initiator_id,created_at);
CREATE INDEX IF NOT EXISTS payment_topups_payer ON payment_topups(payer_kind,payer_id,created_at);
CREATE INDEX IF NOT EXISTS payment_topups_payment ON payment_topups(provider,payment_id);
CREATE INDEX IF NOT EXISTS payment_topups_time ON payment_topups(environment,created_at);
CREATE TRIGGER IF NOT EXISTS payment_topups_terms_fixed BEFORE UPDATE ON payment_topups
WHEN NEW.order_id<>OLD.order_id OR NEW.environment<>OLD.environment OR NEW.provider<>OLD.provider
 OR NEW.payer_kind<>OLD.payer_kind OR NEW.payer_id<>OLD.payer_id OR NEW.initiator_id<>OLD.initiator_id
 OR NEW.package_id IS NOT OLD.package_id OR NEW.amount_minor<>OLD.amount_minor OR NEW.currency<>OLD.currency
 OR NEW.unit<>OLD.unit OR NEW.credit_units<>OLD.credit_units OR NEW.settings_revision<>OLD.settings_revision
 OR NEW.created_at<>OLD.created_at
 OR (OLD.status='paid' AND (NEW.status<>'paid' OR NEW.journal_id IS NOT OLD.journal_id))
 OR (OLD.checkout_session_id IS NOT NULL AND NEW.checkout_session_id IS NOT OLD.checkout_session_id)
 OR (OLD.payment_id IS NOT NULL AND NEW.payment_id IS NOT OLD.payment_id)
BEGIN SELECT RAISE(ABORT,'immutable_payment_order'); END;
CREATE TRIGGER IF NOT EXISTS payment_topups_no_delete BEFORE DELETE ON payment_topups
BEGIN SELECT RAISE(ABORT,'immutable_payment_order'); END;
"""

#: Added column: older rows have no provider time (NULL) and may receive it once.
COLUMNS = (("payment_webhook_inbox", "provider_created_at", "INTEGER"),)


def migrate(c) -> None:
    """After COLUMNS (same transaction): once known, the provider's time is evidence too."""
    c.execute("CREATE TRIGGER IF NOT EXISTS payment_inbox_created_fixed BEFORE UPDATE OF provider_created_at "
              "ON payment_webhook_inbox WHEN OLD.provider_created_at IS NOT NULL "
              "AND NEW.provider_created_at IS NOT OLD.provider_created_at "
              "BEGIN SELECT RAISE(ABORT,'immutable_payment_evidence'); END")
