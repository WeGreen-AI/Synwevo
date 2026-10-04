"""Synwevo Forms: form definitions, their published versions, fill-in requests, drafts,
submissions, attachments and review notes. Owner: `synwevo_suite.forms`.

A definition is edited as a draft (``forms.draft`` with an edit revision) and frozen into an
immutable version when published (``form_versions``). A request pins one version for its whole
life; a submission records the version it answered. Answers are keyed by stable field ids, so a
renamed field keeps every mapping that points at it.

Column names avoid the ones `Store` decodes as JSON by name (`store.JSON_COLS`) except where that
decoding is wanted (``scan``).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS forms (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    draft_json TEXT NOT NULL,
    draft_rev INTEGER NOT NULL DEFAULT 1,
    published_version INTEGER,
    public_token_sealed TEXT,
    public_token_hash TEXT UNIQUE,
    public_version INTEGER,
    public_request_id TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX IF NOT EXISTS forms_workspace ON forms(workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS form_versions (
    form_id TEXT NOT NULL,
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
    PRIMARY KEY (form_id, version)
);

CREATE TABLE IF NOT EXISTS form_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    form_id TEXT NOT NULL,
    form_version INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('personal','public')),
    title TEXT NOT NULL DEFAULT '',
    recipient_email TEXT,
    recipient_user_id TEXT,
    recipient_name TEXT,
    require_sign_in INTEGER NOT NULL DEFAULT 0,
    token_hash TEXT UNIQUE,
    token_sealed TEXT,
    prefill_json TEXT,
    submission_policy TEXT NOT NULL CHECK (submission_policy IN ('once','multiple')),
    due_at REAL,
    status TEXT NOT NULL CHECK (status IN ('open','submitted','changes_requested','accepted','closed','revoked','expired')),
    correlation_id TEXT,
    source_json TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    closed_at REAL,
    closed_reason TEXT
);
CREATE INDEX IF NOT EXISTS form_requests_form ON form_requests(form_id, created_at);
CREATE INDEX IF NOT EXISTS form_requests_workspace ON form_requests(workspace_id, status, created_at);
CREATE INDEX IF NOT EXISTS form_requests_due ON form_requests(status, due_at);

CREATE TABLE IF NOT EXISTS form_drafts (
    request_id TEXT NOT NULL,
    filler_key TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    PRIMARY KEY (request_id, filler_key)
);

CREATE TABLE IF NOT EXISTS form_submissions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    form_id TEXT NOT NULL,
    form_version INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    previous_id TEXT,
    answers_json TEXT NOT NULL,
    answers_hash TEXT NOT NULL,
    submitted_by TEXT,
    submitter_label TEXT,
    filler_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('submitted','changes_requested','accepted','superseded')),
    review_note TEXT,
    reviewed_by TEXT,
    reviewed_at REAL,
    idempotency_key TEXT,
    created_at REAL NOT NULL,
    UNIQUE (request_id, filler_key, idempotency_key)
);
CREATE INDEX IF NOT EXISTS form_submissions_form ON form_submissions(form_id, created_at);
CREATE INDEX IF NOT EXISTS form_submissions_request ON form_submissions(request_id, created_at);

CREATE TABLE IF NOT EXISTS form_files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    filler_key TEXT NOT NULL,
    blob TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT,
    size INTEGER NOT NULL,
    scan TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('staged','attached','discarded')),
    submission_id TEXT,
    uploaded_by TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS form_files_request ON form_files(request_id, filler_key, state);
CREATE INDEX IF NOT EXISTS form_files_submission ON form_files(submission_id);

CREATE TABLE IF NOT EXISTS form_review_items (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    field_id TEXT,
    note TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS form_review_items_submission ON form_review_items(submission_id);
"""

#: A version freezes what link holders are shown, not only the fields: the fill page used to read
#: the description from the live form row, so a draft save changed a published page.
COLUMNS = (("form_versions", "description", "TEXT"),)


def migrate(c) -> None:
    # Versions published before the column existed (or by older code during a rolling start)
    # keep the description link holders already saw: the live one at upgrade time.
    c.execute("UPDATE form_versions SET description=COALESCE((SELECT description FROM forms "
              "WHERE forms.id=form_versions.form_id), '') WHERE description IS NULL")
