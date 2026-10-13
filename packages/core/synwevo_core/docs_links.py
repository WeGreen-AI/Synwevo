"""Read-side cards for links to Synwevo Docs objects, for the account service's messaging.

A message may contain a link to a Docs project or job. The card that goes with it (title,
workspace, job status) is resolved *per reader*, at read time, against the reader's own access:
the reader must be a member of the workspace the project lives in, and that workspace (and its
organization) must be available, otherwise there is no card — the same "nothing" a stranger gets
for a project that does not exist. Being in the same conversation as the sender is not access to
the sender's project.

Only names and a status are read from the Docs tables (`projects`, `jobs`); no file, mapping or
template content. This is a read-side capability of the shared package, like membership and
eligibility, so the account service never imports Docs code.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .apps import DOCS_APP_ID
from .identity import Identity
from .store import Store
from .workspace_availability import AVAILABLE, availability

_PATH = re.compile(r"^/p/(p_[a-z0-9]+)(?:/jobs/(j_[a-z0-9]+))?/?$")
JOB_STATES = ("queued", "running", "cancelling", "completed", "completed_with_errors", "failed", "cancelled")


def docs_origin(store: Store) -> str | None:
    """The registered Docs address (scheme + host), written by the process that serves Docs."""
    row = store.one("SELECT launch_url FROM apps WHERE id=?", (DOCS_APP_ID,))
    url = (row or {}).get("launch_url") or ""
    if not url.startswith(("http://", "https://")):
        return None
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def parse_docs_link(url: str, origin: str | None) -> dict | None:
    """`{"project_id", "job_id"}` when `url` points at a project or job on the Docs origin."""
    if not origin or not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    if f"{u.scheme}://{u.netloc}".lower() != origin:
        return None
    m = _PATH.match(u.path)
    if not m:
        return None
    return {"project_id": m.group(1), "job_id": m.group(2)}


class DocsLinkCards:
    def __init__(self, store: Store, ident: Identity):
        self.store, self.ident = store, ident

    def card(self, uid: str, url: str) -> dict | None:
        ref = parse_docs_link(url, docs_origin(self.store))
        if not ref:
            return None
        p = self.store.one("SELECT id, name, tenant, deleted, archived_at FROM projects WHERE id=?", (ref["project_id"],))
        if not p or p.get("deleted") or not p.get("tenant"):
            return None
        # The workspace *and its organization* must be available (`workspace_availability`, the
        # rule every live gate refuses by): a frozen organization, or one deleted inside its
        # recovery window, closes the card as it closes the project's discussion (A44-44c-3).
        if availability(self.store, p["tenant"]) != AVAILABLE:
            return None
        ws = self.ident.get_workspace(p["tenant"])
        if not ws:
            return None
        if not self.ident.membership(p["tenant"], uid):
            return None
        card = {"kind": "project", "project_id": p["id"], "title": p["name"], "workspace": ws.get("name"),
                "archived": bool(p.get("archived_at")), "path": f"/p/{p['id']}"}
        if ref["job_id"]:
            j = self.store.one("SELECT id, status, counts, created_at, finished_at FROM jobs WHERE id=? AND project_id=?", (ref["job_id"], p["id"]))
            if not j:
                return None
            counts = j.get("counts")
            if isinstance(counts, str):
                try:
                    counts = json.loads(counts)
                except ValueError:
                    counts = {}
            counts = counts if isinstance(counts, dict) else {}
            card.update(kind="job", job_id=j["id"], status=j["status"] if j["status"] in JOB_STATES else "queued",
                        done=int(counts.get("success") or 0), failed=int(counts.get("failed") or 0) + int(counts.get("skipped") or 0) + int(counts.get("cancelled") or 0),
                        path=f"/p/{p['id']}/jobs/{j['id']}")
        return card
