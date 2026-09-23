"""Object-level authorization: can the current workspace reach this object?

The tenant check used to be written by hand at four call sites, and an audit that exercised the
running app found eleven UI endpoints returning another workspace's document content — source
listings, the document body, raw downloads, full-text search, page images, per-word transcription,
job items, report.csv and /locate. Their siblings refused correctly, which is the tell: a check
that must be remembered separately in every handler will be forgotten in half of them.

So the checks live here, they are named after what they protect, and the thing they answer is
always the same question: is this object reachable from the workspace this request is acting in?

The blob layer is the part that decides whether any of this holds. Blob ids travel to the
browser in ordinary responses — every source row and every artifact row carries its sha256 —
and four routes accept one directly. Scoping project queries while leaving those alone would be
bypassable by construction, so `blob_readable` is the gate for all of them.
"""
from __future__ import annotations

from platform_core.context import tenant as current_tenant
from platform_core.store import Store, now

# Every column that holds a blob id together with the join that gives it a workspace. Kept as
# data so the read check and any future sweeper cannot drift apart.
_REACHABLE = (
    # source files, via their project
    "SELECT 1 FROM source_files s JOIN projects p ON p.id=s.project_id "
    "WHERE (s.blob=:b OR s.parsed_blob=:b OR s.working_blob=:b) AND COALESCE(p.tenant,'default')=:t",
    # uploaded template originals
    "SELECT 1 FROM templates WHERE original_blob=:b AND COALESCE(tenant,'default')=:t AND deleted=0",
    # template working copies, via their template
    "SELECT 1 FROM template_versions v JOIN templates t2 ON t2.id=v.template_id "
    "WHERE v.working_blob=:b AND COALESCE(t2.tenant,'default')=:t",
    # generated artifacts, via their job
    "SELECT 1 FROM artifacts a JOIN jobs j ON j.id=a.job_id "
    "WHERE a.blob=:b AND COALESCE(j.tenant,'default')=:t",
    # previews and layout PDFs, which no row owns
    "SELECT 1 FROM blob_grants WHERE blob=:b AND tenant=:t",
)


def blob_readable(store: Store, blob: str, tenant: str | None = None) -> bool:
    """May the current workspace read these bytes?

    Deliberately a reachability question rather than an ownership one: content-addressed storage
    deduplicates, so the same blob genuinely belongs to several workspaces at once and "who owns
    it" has no single answer. What matters is whether the caller can get to it through something
    they are allowed to see.

    Unreachable means refused. A blob that exists only inside a job snapshot — possible after its
    source row is hard-deleted — is not reachable here and will be denied; that is the safe
    direction, and no route serves those bytes to a browser.
    """
    if not blob:
        return False
    t = tenant or current_tenant()
    args = {"b": blob, "t": t}
    for sql in _REACHABLE:
        if store.one(sql + " LIMIT 1", args):
            return True
    return False


def grant_blob(store: Store, blob: str, kind: str, tenant: str | None = None) -> None:
    """Record that this workspace may read a derived blob it just produced.

    Called where previews and layout PDFs are created. Without it the reachability check would
    refuse the very bytes the caller is waiting for.
    """
    if not blob:
        return
    store.execute("INSERT INTO blob_grants (blob,tenant,kind,created_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(blob,tenant) DO NOTHING",
                  (blob, tenant or current_tenant(), kind, now()))


def source_in_scope(store: Store, sid: str, tenant: str | None = None) -> dict | None:
    """The source file row, if the current workspace owns the project it belongs to.

    source_files has no tenant column of its own — it inherits one through project_id — which is
    exactly why every handler that looked a source up by id alone crossed workspaces.
    """
    return store.one(
        "SELECT s.* FROM source_files s JOIN projects p ON p.id=s.project_id "
        "WHERE s.id=? AND s.removed=0 AND COALESCE(p.tenant,'default')=?",
        (sid, tenant or current_tenant()))


def job_in_scope(store: Store, jid: str, tenant: str | None = None) -> dict | None:
    return store.one("SELECT * FROM jobs WHERE id=? AND COALESCE(tenant,'default')=?",
                     (jid, tenant or current_tenant()))


def same_project(source_row: dict | None, pid: str) -> bool:
    """A source id and a project id arriving in one request must actually belong together.

    /locate checked the project and then looked the source up by id alone, so a caller could run
    their own mappings against someone else's document and read the resolved values back.
    """
    return bool(source_row) and source_row.get("project_id") == pid
