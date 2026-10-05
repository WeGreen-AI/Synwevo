"""Organization payers (org-payments, 2026-10-05). Additive tables only.

* ``billing_org_member_grants`` — the organization's grant to a member to charge its wallet, with
  a monthly limit (wallet units, UTC calendar month). Immutable revisions; the latest revision of
  (organization, member) is in force; ``enabled=0`` withdraws it.
* ``billing_org_workspace_limits`` — an optional monthly limit per organization workspace (the
  Agent call's attributed workspace). ``monthly_limit`` NULL means "no workspace limit".
* ``billing_org_admissions`` — which member/workspace/grant revision admitted each organization-paid
  call, written in the reservation transaction; the monthly exposure is read from it.

Rollback: older code never reads these tables and refuses every organization payer.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_org_member_grants (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    organization_id TEXT NOT NULL REFERENCES organizations(id), user_id TEXT NOT NULL REFERENCES users(id),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), unit TEXT NOT NULL,
    monthly_limit INTEGER NOT NULL CHECK(typeof(monthly_limit)='integer' AND monthly_limit>=0),
    actor_id TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,organization_id,user_id,revision)
);
CREATE INDEX IF NOT EXISTS billing_org_grants_member ON billing_org_member_grants(environment,user_id,organization_id);
CREATE TABLE IF NOT EXISTS billing_org_workspace_limits (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    organization_id TEXT NOT NULL REFERENCES organizations(id), workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0), unit TEXT NOT NULL,
    monthly_limit INTEGER CHECK(monthly_limit IS NULL OR (typeof(monthly_limit)='integer' AND monthly_limit>=0)),
    actor_id TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,organization_id,workspace_id,revision)
);
CREATE TABLE IF NOT EXISTS billing_org_admissions (
    call_key TEXT PRIMARY KEY REFERENCES billing_calls(id), environment TEXT NOT NULL,
    organization_id TEXT NOT NULL, user_id TEXT NOT NULL, workspace_id TEXT,
    grant_revision INTEGER NOT NULL, workspace_revision INTEGER
);
CREATE INDEX IF NOT EXISTS billing_org_admissions_member ON billing_org_admissions(organization_id,user_id);
CREATE INDEX IF NOT EXISTS billing_org_admissions_workspace ON billing_org_admissions(organization_id,workspace_id);
"""

for _table in ("billing_org_member_grants", "billing_org_workspace_limits", "billing_org_admissions"):
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_table}_no_update BEFORE UPDATE ON {_table}
BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
CREATE TRIGGER IF NOT EXISTS {_table}_no_delete BEFORE DELETE ON {_table}
BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
"""
