"""Synwevo Approvals: templates and their published versions, requests with their revisions, the
stages a revision went through, the tasks of each stage, the decisions and the timeline.
Owner: `synwevo_suite.approvals`.

A template is edited as a draft (``approval_templates.draft_json`` with an edit revision) and
frozen into an immutable version when published (``approval_template_versions``). A request pins
one version for its whole life. What was submitted for approval — the field values and the basis
snapshot with its canonical hash — is a revision (``approval_revisions``); changing either makes
a new revision that starts again from the first stage, and decisions are tied to the stage run of
the revision they were made on, so a decision on old content never counts for new content.

``approval_requests.state_version`` goes up by one on every change of a request's state; every
change is a compare-and-swap on it inside one write transaction, so concurrent last votes,
approve-versus-withdraw races and double clicks end in one consistent state.

Column names avoid the ones `Store` decodes as JSON by name (`store.JSON_COLS`).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_templates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    draft_json TEXT NOT NULL,
    draft_rev INTEGER NOT NULL DEFAULT 1,
    published_version INTEGER,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX IF NOT EXISTS approval_templates_workspace ON approval_templates(workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS approval_template_versions (
    template_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('published','retired')),
    note TEXT NOT NULL DEFAULT '',
    published_by TEXT NOT NULL,
    published_at REAL NOT NULL,
    retired_at REAL,
    PRIMARY KEY (template_id, version)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_review','approved','rejected','returned','withdrawn','stalled')),
    state_version INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    stage_index INTEGER,
    stage_run_id TEXT,
    correlation_id TEXT,
    source_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS approval_requests_workspace ON approval_requests(workspace_id, status, updated_at);
CREATE INDEX IF NOT EXISTS approval_requests_requester ON approval_requests(workspace_id, requester_id, updated_at);
CREATE INDEX IF NOT EXISTS approval_requests_template ON approval_requests(template_id);

CREATE TABLE IF NOT EXISTS approval_revisions (
    request_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    fields_hash TEXT NOT NULL,
    basis_ref_json TEXT,
    basis_json TEXT,
    basis_hash TEXT,
    note TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL,
    submitted_at REAL NOT NULL,
    PRIMARY KEY (request_id, revision)
);

CREATE TABLE IF NOT EXISTS approval_stage_runs (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    stage_index INTEGER NOT NULL,
    stage_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('all','any','n_of_m')),
    required INTEGER,
    status TEXT NOT NULL CHECK (status IN ('active','stalled','approved','rejected','returned','skipped','cancelled')),
    stall_reason TEXT,
    skip_json TEXT,
    opened_at REAL NOT NULL,
    due_at REAL,
    remind INTEGER NOT NULL DEFAULT 0,
    reminded_at REAL,
    escalate_at REAL,
    escalated_at REAL,
    closed_at REAL
);
CREATE INDEX IF NOT EXISTS approval_stage_runs_request ON approval_stage_runs(request_id, revision, stage_index);
CREATE INDEX IF NOT EXISTS approval_stage_runs_due ON approval_stage_runs(status, due_at);
CREATE INDEX IF NOT EXISTS approval_stage_runs_escalate ON approval_stage_runs(status, escalate_at);

CREATE TABLE IF NOT EXISTS approval_tasks (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    stage_run_id TEXT NOT NULL,
    assignee_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','returned','transferred','revoked','cancelled')),
    source TEXT NOT NULL CHECK (source IN ('explicit','role','escalation','transfer','reassign','reresolve')),
    transferred_from TEXT,
    close_reason TEXT,
    created_at REAL NOT NULL,
    closed_at REAL
);
CREATE INDEX IF NOT EXISTS approval_tasks_stage ON approval_tasks(stage_run_id, status);
CREATE INDEX IF NOT EXISTS approval_tasks_assignee ON approval_tasks(workspace_id, assignee_id, status);
CREATE INDEX IF NOT EXISTS approval_tasks_request ON approval_tasks(request_id);
-- One live task per person per stage run: a person is never counted twice in one stage.
CREATE UNIQUE INDEX IF NOT EXISTS approval_tasks_live ON approval_tasks(stage_run_id, assignee_id)
    WHERE status IN ('pending','approved','rejected','returned');

CREATE TABLE IF NOT EXISTS approval_decisions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    stage_run_id TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    decider_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve','reject','return')),
    comment TEXT NOT NULL DEFAULT '',
    state_version INTEGER NOT NULL,
    via TEXT,
    run_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS approval_decisions_request ON approval_decisions(request_id, created_at);
CREATE INDEX IF NOT EXISTS approval_decisions_decider ON approval_decisions(workspace_id, decider_id, created_at);

CREATE TABLE IF NOT EXISTS approval_timeline (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    at REAL NOT NULL,
    actor_id TEXT,
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL,
    stage_index INTEGER,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS approval_timeline_request ON approval_timeline(request_id, seq);
"""

# Waits are durable subscriptions, not threads or model sessions. Every subscription names the
# exact revision it waits for; revision changes cancel it instead of approving changed content.
SCHEMA += """
CREATE TABLE IF NOT EXISTS approval_waits (
    run_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    deadline REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS approval_waits_request ON approval_waits(request_id, status);
CREATE INDEX IF NOT EXISTS approval_waits_deadline ON approval_waits(status, deadline);
CREATE TABLE IF NOT EXISTS approval_alerts (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    stage_run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(stage_run_id, user_id, kind)
);
CREATE INDEX IF NOT EXISTS approval_alerts_user ON approval_alerts(workspace_id, user_id, created_at);
"""
