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

import json

from synwevo_core.context import tenant as current_tenant
from synwevo_core.store import Store, now

# Every column that holds a blob id together with the join that gives it a workspace. Kept as
# data so the read check and any future sweeper cannot drift apart.
# `:q` is the JSON array of digests whose quarantine verdict stands (synwevo_core.quarantine) —
# usually empty. A verdict counts before the rows are marked: another application's rescan may
# have found the bytes first, or a restored backup may predate the verdict (CI-90).
_HELD = "(SELECT value FROM json_each(:q))"
_REACHABLE = (
    # source files, via their project. Only live ones: removing a file uploaded by mistake has
    # to stop its bytes being served, and every source row hands its blob id to the browser.
    # Only accepted ones too: a file still being checked, or rejected — including one a rescan
    # rejected after it had been parsed, whose converted copy and parsed workbook remain (CI-90) —
    # or refused by the gate as encrypted (§8.7; `malware.encrypted_refusal`: a rescan's record,
    # `rescan: true`, leaves a stored file's access as it was).
    "SELECT 1 FROM source_files s JOIN projects p ON p.id=s.project_id "
    "WHERE (s.blob=:b OR s.parsed_blob=:b OR s.working_blob=:b) AND s.removed=0 "
    "AND COALESCE(s.status,'') NOT IN ('rejected','scanning','scan_failed') "
    "AND NOT (COALESCE(json_extract(s.scan,'$.status'),'')='encrypted' AND COALESCE(json_extract(s.scan,'$.rescan'),0)=0) "
    f"AND s.blob NOT IN {_HELD} AND COALESCE(s.working_blob,'') NOT IN {_HELD} "
    "AND COALESCE(p.deleted,0)=0 AND COALESCE(p.tenant,'default')=:t",
    # uploaded template originals, unless a rescan found the template infected or any of its
    # contents is under a verdict
    "SELECT 1 FROM templates WHERE original_blob=:b AND COALESCE(tenant,'default')=:t AND deleted=0 "
    "AND COALESCE(json_extract(scan,'$.status'),'')<>'infected' "
    f"AND NOT EXISTS (SELECT 1 FROM template_versions v9 WHERE v9.template_id=templates.id AND v9.working_blob IN {_HELD})",
    # template working copies, via their (live, not rejected) template
    "SELECT 1 FROM template_versions v JOIN templates t2 ON t2.id=v.template_id "
    "WHERE v.working_blob=:b AND COALESCE(t2.deleted,0)=0 AND COALESCE(t2.tenant,'default')=:t "
    "AND COALESCE(json_extract(t2.scan,'$.status'),'')<>'infected' "
    f"AND COALESCE(t2.original_blob,'') NOT IN {_HELD} "
    f"AND NOT EXISTS (SELECT 1 FROM template_versions v9 WHERE v9.template_id=t2.id AND v9.working_blob IN {_HELD})",
    # generated artifacts, via their job; a deleted artifact is no longer served, nor one made
    # from a template or source that was rejected later (the same rule as jobs.withdrawn_input)
    "SELECT 1 FROM artifacts a JOIN jobs j ON j.id=a.job_id JOIN projects pa ON pa.id=j.project_id "
    "WHERE a.blob=:b AND a.deleted_at IS NULL AND COALESCE(j.tenant,'default')=:t AND COALESCE(pa.deleted,0)=0 "
    "AND NOT EXISTS (SELECT 1 FROM templates t3 WHERE t3.id=json_extract(j.snapshot,'$.template.id') "
    "AND COALESCE(json_extract(t3.scan,'$.status'),'')='infected') "
    "AND NOT EXISTS (SELECT 1 FROM json_each(j.snapshot,'$.sources') js JOIN source_files s3 "
    "ON s3.id=json_extract(js.value,'$.id') WHERE s3.status='rejected') "
    "AND NOT EXISTS (SELECT 1 FROM json_each(:q) hq WHERE instr(j.snapshot, hq.value) > 0)",
    # previews, layout PDFs and desensitized copies, which no row owns. A grant made since CI-90
    # names what it was made from (`preview:<template>`, `layout:<template>`,
    # `desensitized:<source>`): one made from a rejected template or source is no longer served.
    "SELECT 1 FROM blob_grants WHERE blob=:b AND tenant=:t "
    "AND NOT EXISTS (SELECT 1 FROM templates t4 WHERE instr(blob_grants.kind, ':')>0 "
    "AND t4.id=substr(blob_grants.kind, instr(blob_grants.kind, ':')+1) "
    f"AND (COALESCE(json_extract(t4.scan,'$.status'),'')='infected' OR COALESCE(t4.original_blob,'') IN {_HELD} "
    f"OR EXISTS (SELECT 1 FROM template_versions v8 WHERE v8.template_id=t4.id AND v8.working_blob IN {_HELD}))) "
    "AND NOT EXISTS (SELECT 1 FROM source_files s4 WHERE instr(blob_grants.kind, ':')>0 "
    "AND s4.id=substr(blob_grants.kind, instr(blob_grants.kind, ':')+1) "
    f"AND (COALESCE(s4.status,'')='rejected' OR s4.blob IN {_HELD}))",
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
    from synwevo_core import quarantine
    held = quarantine.standing(store)
    if blob in held:
        return False
    t = tenant or current_tenant()
    args = {"b": blob, "t": t, "q": json.dumps(sorted(held))}
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
        "WHERE s.id=? AND s.removed=0 AND COALESCE(p.deleted,0)=0 AND COALESCE(p.tenant,'default')=?",
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


# --------------------------------------------------------------------- role policy (browser API)
#: What each browser route needs from the member's workspace role, keyed exactly as the route is
#: registered in `api/app.py`. ``None`` = any member may call it (reading on the platform),
#: ``"write"`` = editor and above, ``"export"`` = takes files off the platform, so it needs the
#: membership's ``can_export`` or editor and above (permissions.md §4.2: reading ≠ exporting).
#:
#: An explicit table rather than "GET is read, the rest is write": several GETs hand over the
#: original bytes (downloads, ZIPs, the CSV report) and several POSTs only compute a preview.
#: `tests/integration/test_gate_roles.py` fails when a route is registered without an entry
#: here, so a new route cannot quietly default to "any member".
ROUTE_POLICY: dict[tuple[str, str], str | None] = {
    # regression cases & desensitization
    ("GET", "/api/regression-cases"): None,
    ("POST", "/api/projects/{pid}/regression-cases"): "write",
    ("POST", "/api/regression-cases/{cid}/run"): "write",
    ("DELETE", "/api/regression-cases/{cid}"): "write",
    ("GET", "/api/regression-cases/{cid}/download"): "export",      # a ZIP of the case's inputs
    # Creates copies (and, with add_to_project, new sources). Writing, whatever it is used for.
    ("POST", "/api/projects/{pid}/desensitize"): "write",
    ("GET", "/api/desensitized/{blob}"): "export",
    # about the software, not the workspace
    ("GET", "/api/about"): None,
    ("GET", "/api/source-code"): None,
    ("GET", "/api/capabilities"): None,
    # copying a personal project into an organization workspace (approval lives on the account service)
    ("GET", "/api/copy-targets"): None,
    ("GET", "/api/projects/{pid}/copy-manifest"): None,
    ("GET", "/api/projects/{pid}/copy-requests"): None,
    ("POST", "/api/projects/{pid}/copy-requests"): "write",
    ("POST", "/api/copy-requests/{cid}/withdraw"): "write",
    # Comments: reading and writing a comment is part of reading the project (a viewer may ask a
    # question); who may edit, delete or resolve is decided per comment in the service.
    ("GET", "/api/projects/{pid}/comments"): None,
    ("GET", "/api/projects/{pid}/comment-members"): None,
    ("POST", "/api/projects/{pid}/comments"): None,
    ("PATCH", "/api/comments/{cid}"): None,
    ("DELETE", "/api/comments/{cid}"): None,
    ("POST", "/api/comments/{cid}/resolve"): None,
    ("POST", "/api/comments/{cid}/reopen"): None,
    # projects
    ("GET", "/api/projects"): None,
    ("POST", "/api/projects"): "write",
    ("GET", "/api/projects/{pid}"): None,
    ("PATCH", "/api/projects/{pid}"): "write",
    ("DELETE", "/api/projects/{pid}"): "write",
    # sources
    ("POST", "/api/projects/{pid}/sources"): "write",
    ("GET", "/api/projects/{pid}/sources"): None,
    ("PUT", "/api/projects/{pid}/sources/order"): "write",
    ("DELETE", "/api/sources/{sid}"): "write",
    ("POST", "/api/sources/{sid}/representative"): "write",
    ("PUT", "/api/sources/{sid}/options"): "write",
    ("GET", "/api/sources/{sid}/view"): None,
    ("GET", "/api/sources/{sid}/blocks"): None,
    ("GET", "/api/sources/{sid}/search"): None,
    ("GET", "/api/sources/{sid}/page/{page}.png"): None,
    ("GET", "/api/sources/{sid}/words/{page}"): None,
    ("POST", "/api/sources/{sid}/ocr"): "write",                    # stores recognised text
    ("PUT", "/api/sources/{sid}/corrections/{mid}"): "write",
    # Computes a locator from a selection and stores nothing; only the mapping save persists it.
    ("POST", "/api/sources/{sid}/locator"): None,
    ("GET", "/api/projects/{pid}/locate"): None,
    ("GET", "/api/sources/{sid}/download"): "export",
    # templates
    ("GET", "/api/templates"): None,
    ("POST", "/api/templates/upload"): "write",
    ("POST", "/api/templates/builtin"): "write",
    ("PUT", "/api/templates/{tid}/builtin"): "write",
    ("GET", "/api/templates/{tid}"): None,
    ("PATCH", "/api/templates/{tid}"): "write",
    ("DELETE", "/api/templates/{tid}"): "write",
    ("POST", "/api/templates/{tid}/duplicate"): "write",
    ("GET", "/api/templates/{tid}/view"): None,
    ("GET", "/api/templates/{tid}/page/{page}.png"): None,
    ("POST", "/api/templates/{tid}/slots"): "write",
    ("POST", "/api/templates/{tid}/pdf-repeat-detect"): None,       # detection only, stores nothing
    ("PATCH", "/api/templates/{tid}/slots/{slot_id}"): "write",
    ("DELETE", "/api/templates/{tid}/slots/{slot_id}"): "write",
    ("GET", "/api/templates/{tid}/usage"): None,
    # Live co-editing: joining the room is reading (a viewer follows along and sees who is there);
    # whether a connection may change the document is decided per message from the live role.
    ("GET", "/api/collab/templates/{tid}"): None,
    ("GET", "/api/templates/{tid}/collab/changes"): None,
    # a live table room's state becomes a version (docs-sheet-live.md §3): a write like any save
    ("POST", "/api/templates/{tid}/collab/save"): "write",
    ("GET", "/api/collab/projects/{pid}"): None,
    ("GET", "/api/projects/{pid}/activity"): None,
    ("POST", "/api/templates/{tid}/restore"): "write",
    ("POST", "/api/templates/{tid}/undo"): "write",
    ("POST", "/api/templates/{tid}/redo"): "write",
    ("GET", "/api/templates/{tid}/download"): "export",
    # organization-shared templates: any member may browse; publishing, maintaining and copying
    # one into this workspace are writes (the service checks the organization side)
    ("GET", "/api/organization-templates"): None,
    ("GET", "/api/organization-templates/{pid}"): None,
    ("POST", "/api/templates/{tid}/publish"): "write",
    ("POST", "/api/organization-templates/{pid}/update"): "write",
    ("POST", "/api/organization-templates/{pid}/deprecate"): "write",
    ("POST", "/api/organization-templates/{pid}/use"): "write",
    # records / rules
    ("GET", "/api/projects/{pid}/records"): None,
    ("POST", "/api/projects/{pid}/rule-preview"): None,              # counts for a candidate rule, not saved
    ("GET", "/api/projects/{pid}/validate"): None,
    # mappings
    ("GET", "/api/projects/{pid}/mappings"): None,
    ("PUT", "/api/projects/{pid}/mappings"): "write",
    ("POST", "/api/projects/{pid}/mappings/undo"): "write",
    ("POST", "/api/projects/{pid}/mappings/redo"): "write",
    # preview: rendered on the platform is reading; the raw file is an export
    ("GET", "/api/projects/{pid}/preview"): None,
    ("GET", "/api/preview-files/{blob}"): "export",
    ("GET", "/api/projects/{pid}/preview-layout"): None,
    ("GET", "/api/preview-files/{blob}/view"): None,
    ("GET", "/api/artifacts/{aid}/view"): None,
    ("GET", "/api/preview-files/{blob}/page/{page}.png"): None,
    # jobs
    ("GET", "/api/projects/{pid}/generation-summary"): None,
    ("GET", "/api/projects/{pid}/generation-issues"): None,          # the preflight's issues, paged
    ("POST", "/api/projects/{pid}/jobs"): "write",
    ("GET", "/api/projects/{pid}/jobs"): None,
    ("GET", "/api/jobs/{jid}"): None,
    ("GET", "/api/jobs/{jid}/items"): None,
    ("POST", "/api/jobs/{jid}/cancel"): "write",
    ("POST", "/api/jobs/{jid}/retry"): "write",
    ("GET", "/api/jobs/{jid}/runs"): None,
    ("GET", "/api/jobs/{jid}/events"): None,
    ("GET", "/api/jobs/{jid}/zip"): "export",
    ("GET", "/api/jobs/{jid}/report.csv"): "export",
    # `inline=1` still hands the whole file to the browser, which can save it
    ("GET", "/api/artifacts/{aid}"): "export",
    ("GET", "/api/artifacts/{aid}/page/{page}.png"): None,
}

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _compile(template: str):
    import re
    parts = re.split(r"(\{[^}/]+\})", template)
    return re.compile("^" + "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts if p) + "$")


_COMPILED = [(method, _compile(template), policy, template)
             for (method, template), policy in ROUTE_POLICY.items()]


def route_policy(method: str, path: str) -> str | None:
    """What a browser request to Synwevo Docs needs from the member's workspace role.

    Returns ``None`` (readable by any member), ``"write"`` (editor or above) or ``"export"``
    (takes files off the platform: needs ``can_export`` or editor and above). The platform gate
    (`synwevo_core.http`) consults it through ``api.state.route_policy`` for every Synwevo Docs
    ``/api/`` request made with a browser session.

    Literal segments win over parameters, the way the router resolves them, so
    ``/api/templates/upload`` is never mistaken for ``/api/templates/{tid}``. A path in no
    entry falls back by method — anything that is not a safe method needs ``write`` — so an
    unclassified route fails closed for viewers rather than open.
    """
    m = (method or "GET").upper()
    if m == "HEAD":
        m = "GET"
    best = None
    for method_, rx, policy, template in _COMPILED:
        if method_ == m and rx.match(path):
            literal = template.count("/") - template.count("{")
            if best is None or literal > best[0]:
                best = (literal, policy)
    if best is not None:
        return best[1]
    return None if m in SAFE_METHODS else "write"


def classified(method: str, template: str) -> bool:
    """Is this registered route in the table? Used by the coverage test."""
    return (method.upper(), template) in ROUTE_POLICY
