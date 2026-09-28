"""Rescan the Synwevo Docs files stored before malware scanning was switched on (CI-90).

    python -m synwevo_docs.cli rescan [--apply] [--limit N] [--workspace ID] [--only sources|templates|cases]
    python -m synwevo_docs.cli quarantine list | release <sha256> | purge <sha256> --yes

``rescan`` alone is a preview (counts, and whether the scanner answers); ``--apply`` scans and
writes. What is looked at: source files in use (not removed, project not deleted), every stored
content of templates in use — the working file, the uploaded image of an image template and the
working file of every version — and every organization template publication (its current release
and the working file of every earlier release, which outlive a deleted source template), whose
scan record is not ``clean`` (see `synwevo_core.rescan` for the selection and verdict rules shared
with Synwevo Message). A template or publication is one unit: it is clean only when all of its
contents are. Files of captured regression cases that no clean row vouches for (a source removed
since the capture) are scanned from the case's own copy on every run.

When bytes are infected every Docs row that points at them is rejected — sources become
``rejected`` (the upload gate's own sentence and ``scan.status=infected``), templates and
publications carry ``scan.status=infected`` (a publication in its ``info.scan``) and every read
path refuses them with 410 ``rejected_malware`` — and the bytes, with any regression case that
holds a copy, move to the quarantine instead of being deleted: a historical file may have no other
copy, and a false positive has to come back (docs/security/file-acceptance.md §8).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from synwevo_core import malware as M
from synwevo_core import quarantine as Q
from synwevo_core import rescan as RS
from synwevo_core.auth import audit_event
from synwevo_core.store import Store, now

from .services import template_blobs  # noqa: F401  (re-exported: the rescan and the read gates agree on a template's contents)

KIND_SOURCE = "来源"
KIND_TEMPLATE = "模板"
KIND_PUBLICATION = "组织共享模板"
KIND_CASE = "回归用例"
#: The quarantine rows this application restores on release.
DOCS_KINDS = frozenset({"source_files", "templates", "organization_templates", "regression_case"})
NOT_ACCEPTED = ("rejected", "scanning", "scan_failed")
#: The upload gate's own sentence (services._quarantine_blob), so a rescan rejection reads the same.
REJECT_ERROR = "安全检查未通过：文件被判定为恶意内容（{sig}），已拒绝并隔离"

# Every template / publication holding these bytes — including ones already rejected for other
# bytes, so each verdict's entry lists them and releasing one verdict leaves the other in force.
_TEMPLATES_WITH = ("SELECT id, tenant, scan FROM templates WHERE original_blob=? OR json_extract(info,'$.image.original_blob')=? "
                   "OR id IN (SELECT template_id FROM template_versions WHERE working_blob=?)")
_PUBS_WITH = ("SELECT id, organization_id, source_tenant, info FROM organization_templates WHERE working_blob=? "
              "OR json_extract(info,'$.image.original_blob')=? "
              "OR id IN (SELECT publication_id FROM organization_template_releases WHERE working_blob=?)")


def publication_blobs(store: Store, p: dict) -> list[str]:
    """Every stored content of a publication: the current release's working file, an image
    template's upload, and the working file of every earlier release."""
    out: list[str] = []
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    img = (info or {}).get("image") or {}
    for b in [p.get("working_blob"), img.get("original_blob")] + [
            r["working_blob"] for r in store.all("SELECT working_blob FROM organization_template_releases WHERE publication_id=? ORDER BY release_no", (p["id"],))]:
        if b and b not in out:
            out.append(b)
    return out


def _pub_scan(p: dict):
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    return (info or {}).get("scan")


def _is_infected(rec) -> bool:
    return isinstance(rec, dict) and rec.get("status") == "infected"


class DocsRescan:
    def __init__(self, app, report: RS.Report, by: str):
        self.app = app
        self.store: Store = app.store
        self.report = report
        self.by = by
        self._rejected: set[str] = set()          # digests already handled in this run

    # ------------------------------------------------------------------ selection
    def source_groups(self, workspace: str | None = None) -> list[dict]:
        sql = ("SELECT s.id, s.blob, s.size, s.status, s.scan, s.created_at, p.tenant FROM source_files s "
               "JOIN projects p ON p.id=s.project_id WHERE s.removed=0 AND COALESCE(p.deleted,0)=0 AND s.blob IS NOT NULL "
               "AND COALESCE(s.status,'')<>'rejected'")
        args: tuple = ()
        if workspace:
            sql += " AND COALESCE(p.tenant,'default')=?"
            args = (workspace,)
        held = Q.standing(self.store)
        groups: dict[str, list[dict]] = {}
        for r in self.store.all(sql, args):
            # Under a standing verdict: marked whatever the record says (a clean record from before
            # the other application's rescan, or a row still being checked). Otherwise only rows
            # the application is not handling itself and whose record is not clean.
            if r["blob"] in held or ((r.get("status") or "") not in ("scanning", "scan_failed") and RS.selectable(r.get("scan"))):
                groups.setdefault(r["blob"], []).append(r)
        return [{"sha256": b, "rows": rows,
                 "key": RS.UNDER_VERDICT if b in held else min(RS.order_key(r.get("scan"), r.get("created_at")) for r in rows)}
                for b, rows in groups.items()]

    def template_candidates(self, workspace: str | None = None) -> list[dict]:
        sql = "SELECT id, tenant, original_blob, info, scan, created_at FROM templates WHERE deleted=0"
        args: tuple = ()
        if workspace:
            sql += " AND COALESCE(tenant,'default')=?"
            args = (workspace,)
        held = Q.standing(self.store)
        out = []
        for r in self.store.all(sql, args):
            if _is_infected(r.get("scan")):
                continue
            r["blobs"] = template_blobs(self.store, r)
            under = bool(held.intersection(r["blobs"]))
            if under or RS.selectable(r.get("scan")):
                r["key"] = RS.UNDER_VERDICT if under else RS.order_key(r.get("scan"), r.get("created_at"))
                out.append(r)
        return out

    def publication_candidates(self, workspace: str | None = None) -> list[dict]:
        sql = "SELECT id, organization_id, source_tenant, working_blob, info, published_at FROM organization_templates"
        args: tuple = ()
        if workspace:
            sql += " WHERE source_tenant=?"
            args = (workspace,)
        held = Q.standing(self.store)
        out = []
        for r in self.store.all(sql, args):
            if _is_infected(_pub_scan(r)):
                continue
            r["blobs"] = publication_blobs(self.store, r)
            under = bool(held.intersection(r["blobs"]))
            if under or RS.selectable(_pub_scan(r)):
                r["key"] = RS.UNDER_VERDICT if under else RS.order_key(_pub_scan(r), r.get("published_at"))
                out.append(r)
        return out

    def case_groups(self, workspace: str | None = None) -> list[dict]:
        """Files of captured regression cases (regression.py) — verbatim copies of a project's
        sources and template, downloadable by the workspace — whose bytes no row vouches for:
        the source may have been removed since, or the case captured a working copy. A digest
        with a row whose clean record is bound to it is not scanned again. Cases carry no scan
        record of their own, so these are looked at on every run (they are few and small)."""
        from .regression import _tenant_dirname, cases_root
        root = cases_root(self.store.root)
        if not root.is_dir():
            return []
        want = _tenant_dirname(workspace) if workspace else None
        held = Q.standing(self.store)
        groups: dict[str, dict] = {}
        unreadable: list[dict] = []
        for ws in sorted(p for p in root.iterdir() if p.is_dir() and (want is None or p.name == want)):
            for case in sorted(p for p in ws.iterdir() if p.is_dir() and (p / "case.json").is_file()):
                ident = f"{ws.name}/{case.name}"
                try:
                    files = sorted(case.rglob("*"))
                    for f in files:
                        if f.is_symlink() or not f.is_file() or f.name == "case.json":
                            continue
                        sha = _digest(f)
                        g = groups.setdefault(sha, {"sha256": sha, "cases": [], "path": f, "size": f.stat().st_size})
                        if ident not in g["cases"]:
                            g["cases"].append(ident)
                except OSError as e:          # one unreadable case is reported, not fatal to the run
                    unreadable.append({"sha256": None, "cases": [ident], "path": None, "size": None,
                                       "error": f"{type(e).__name__} errno={getattr(e, 'errno', None)}", "key": (1, 0, 0)})
        out = []
        for g in groups.values():
            if g["sha256"] in held:
                g["key"] = RS.UNDER_VERDICT
            elif self._vouched(g["sha256"]) or RS.scan_status(self._case_record(g["sha256"])) == "clean":
                continue
            else:
                rec = self._case_record(g["sha256"])
                g["key"] = RS.order_key(rec, 0)
            out.append(g)
        return out + unreadable

    # A regression case has no row to hold a scan record, so the verdict on a case file's bytes
    # is kept in `meta` under its digest — bound to the content, like every record here.
    def _case_record(self, sha: str) -> dict | None:
        row = self.store.one("SELECT value FROM meta WHERE key=?", (f"rescan-case:{sha}",))
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return None

    def _record_case(self, sha: str, rec: dict) -> None:
        self.store.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (f"rescan-case:{sha}", json.dumps(rec)))

    def _vouched(self, sha: str) -> bool:
        """A row in use whose clean scan record is bound to exactly these bytes."""
        store = self.store
        if store.one("SELECT 1 AS x FROM source_files WHERE blob=? AND removed=0 AND json_extract(scan,'$.status')='clean' "
                     "AND json_extract(scan,'$.sha256')=? LIMIT 1", (sha, sha)):
            return True
        for r in store.all(_TEMPLATES_WITH, (sha, sha, sha)):
            rec = r.get("scan")
            if isinstance(rec, dict) and rec.get("status") == "clean":
                return True
        return False

    def units(self, *, workspace: str | None, only: str | None) -> list[tuple[str, dict]]:
        """Every unit of every kind, in one order (`synwevo_core.rescan.order_key`): under a
        standing verdict first, then never tried, earlier errors, and files over the limit last."""
        units: list[tuple[str, dict]] = []
        if only in (None, "sources"):
            units += [("source", g) for g in self.source_groups(workspace)]
        if only in (None, "templates"):
            units += [("template", t) for t in self.template_candidates(workspace)]
            units += [("publication", p) for p in self.publication_candidates(workspace)]
        if only in (None, "cases"):
            units += [("case", g) for g in self.case_groups(workspace)]
        rank = {"source": 0, "template": 1, "publication": 2, "case": 3}
        return sorted(units, key=lambda ku: (ku[1]["key"], rank[ku[0]]))

    # ------------------------------------------------------------------ the run
    def run(self, ex: RS.Examiner, *, workspace: str | None, only: str | None, limit: int | None) -> None:
        units = self.units(workspace=workspace, only=only)
        self.report.total = len(units)
        for n, (kind, u) in enumerate(units):
            if limit and ex.attempts >= limit:
                self.report.deferred = len(units) - n          # --limit counts scans; the rest waits
                break
            try:
                if kind == "source":
                    self._source_unit(ex, u)
                elif kind == "template":
                    self._template_unit(ex, u)
                elif kind == "publication":
                    self._publication_unit(ex, u)
                else:
                    self._case_unit(ex, u)
            except RS.ScannerLost:
                raise
            except Exception as e:  # noqa: BLE001 - one broken unit must not take the summary with it
                label = {"source": KIND_SOURCE, "template": KIND_TEMPLATE, "case": KIND_CASE}.get(kind, KIND_PUBLICATION)
                ident = (",".join(r["id"] for r in u["rows"][:3]) if kind == "source"
                         else ",".join(u["cases"][:3]) if kind == "case" else u["id"])
                self.report.failed(label, ident, u.get("sha256"), e)

    def _source_unit(self, ex: RS.Examiner, g: dict) -> None:
        sha, rows = g["sha256"], g["rows"]
        ident = ",".join(r["id"] for r in rows[:3]) + ("…" if len(rows) > 3 else "")
        standing = Q.verdict(self.store, sha)
        if standing:
            # The verdict stands until a release withdraws it: never scanned again here, never
            # recorded clean — even if a copy of the bytes is in the store and today's engine
            # would call it clean (that is what `quarantine release` is for).
            self._reject(sha, standing.get("signature"), standing.get("engine"), None)
            self.report.unit(KIND_SOURCE, ident, sha, "applied", len(rows), standing.get("signature") or "")
            return
        v = ex.examine(sha)
        if v.outcome == "infected":
            self._reject(sha, v.signature, v.engine, v.data)
            self.report.unit(KIND_SOURCE, ident, sha, "infected", len(rows), v.signature or "")
            return
        if v.outcome == "missing":
            standing = Q.verdict(self.store, sha)
            if standing:
                self._reject(sha, standing.get("signature"), standing.get("engine"), None)
                self.report.unit(KIND_SOURCE, ident, sha, "applied", len(rows), standing.get("signature") or "")
                return
        if v.outcome in ("missing", "mismatch"):
            self.report.unit(KIND_SOURCE, ident, sha, v.outcome, len(rows))
            return
        if v.outcome == "clean":
            made = [self._publish_source(r, RS.clean_record(v.result, sha, r.get("scan"))) for r in rows]
            self.report.unit(KIND_SOURCE, ident, sha, "clean" if any(made) else "changed", len(rows))
            return
        # error / too_large: the attempt is recorded, the row stays as it was
        limit = ex.scanner.max_bytes
        for r in rows:
            prev = r.get("scan") or {}
            if v.outcome == "too_large" and RS.scan_status(prev) == "too_large" and prev.get("limit") == limit:
                continue                      # same limit, same answer: nothing new to write
            extra = {"limit": limit} if v.outcome == "too_large" else {}
            self._publish_source(r, RS.attempt_record(v.outcome, v.reason or "", sha, prev, **extra))
        detail = f"size={rows[0].get('size')} limit={limit}" if v.outcome == "too_large" else (v.reason or "")
        self.report.unit(KIND_SOURCE, ident, sha, v.outcome, len(rows), detail)

    def _case_unit(self, ex: RS.Examiner, g: dict) -> None:
        """Infected (or under a standing verdict): every case holding the bytes moves aside with
        them (`_reject`), as it does when a row's bytes are rejected. Nothing else is written."""
        sha = g["sha256"]
        ident = ",".join(g["cases"][:3]) + ("…" if len(g["cases"]) > 3 else "")
        if g.get("error"):
            self.report.unit(KIND_CASE, ident, None, "error", len(g["cases"]), f"用例文件读不出：{g['error']}")
            return
        if sha in self._rejected:                   # moved aside with a row's bytes earlier in this run
            self.report.unit(KIND_CASE, ident, sha, "applied", len(g["cases"]), "已随同一字节的行移入隔离区")
            return
        standing = Q.verdict(self.store, sha)
        if standing:
            self._reject(sha, standing.get("signature"), standing.get("engine"), None)
            self.report.unit(KIND_CASE, ident, sha, "applied", len(g["cases"]), standing.get("signature") or "")
            return
        try:
            data = Path(g["path"]).read_bytes()
        except FileNotFoundError:
            self.report.unit(KIND_CASE, ident, sha, "changed", len(g["cases"]))
            return
        v = ex.examine_bytes(sha, data)
        if v.outcome == "infected":
            self._reject(sha, v.signature, v.engine, v.data)
            self.report.unit(KIND_CASE, ident, sha, "infected", len(g["cases"]), v.signature or "")
            return
        prev = self._case_record(sha)
        if v.outcome == "clean":
            self._record_case(sha, RS.clean_record(v.result, sha, prev))
        elif v.outcome in ("error", "too_large"):
            limit = ex.scanner.max_bytes
            if not (v.outcome == "too_large" and RS.scan_status(prev) == "too_large" and (prev or {}).get("limit") == limit):
                self._record_case(sha, RS.attempt_record(v.outcome, v.reason or "", sha, prev,
                                                         **({"limit": limit} if v.outcome == "too_large" else {})))
        detail = f"size={g['size']} limit={ex.scanner.max_bytes}" if v.outcome == "too_large" else (v.reason or "")
        self.report.unit(KIND_CASE, ident, sha, v.outcome, len(g["cases"]), detail)

    def _publish_source(self, row: dict, rec: dict) -> bool:
        """Write only the scan record, and only if nobody changed the row's bytes, acceptance or
        record since it was selected (the `_publish_scan` rule: re-read inside the transaction)."""
        with self.store.tx():
            cur = self.store.one("SELECT blob, status, scan FROM source_files WHERE id=? AND removed=0", (row["id"],))
            if (not cur or cur["blob"] != row["blob"] or (cur.get("status") or "") in NOT_ACCEPTED
                    or cur.get("scan") != row.get("scan")):
                return False
            self.store.update("source_files", {"id": row["id"]}, {"scan": rec})
        return True

    # ------------------------------------------------------------------ multi-content units
    def _template_unit(self, ex: RS.Examiner, t: dict) -> None:
        cur = self.store.one("SELECT scan, deleted FROM templates WHERE id=?", (t["id"],))
        if not cur or cur["deleted"] or cur.get("scan") != t.get("scan"):
            # Handled earlier in this run (bytes it shares were infected) or changed by someone else.
            outcome = "applied" if cur and _is_infected(cur.get("scan")) else "changed"
            self.report.unit(KIND_TEMPLATE, t["id"], ((cur or {}).get("scan") or {}).get("sha256"), outcome)
            return
        self._multi_unit(ex, KIND_TEMPLATE, t, t.get("scan"), self._publish_template)

    def _publication_unit(self, ex: RS.Examiner, p: dict) -> None:
        cur = self.store.one("SELECT info FROM organization_templates WHERE id=?", (p["id"],))
        if not cur or _pub_scan(cur) != _pub_scan(p):
            outcome = "applied" if cur and _is_infected(_pub_scan(cur)) else "changed"
            self.report.unit(KIND_PUBLICATION, p["id"], (_pub_scan(cur or {}) or {}).get("sha256"), outcome)
            return
        self._multi_unit(ex, KIND_PUBLICATION, p, _pub_scan(p), self._publish_publication)

    def _multi_unit(self, ex: RS.Examiner, label: str, row: dict, prev_rec, publish) -> None:
        blobs = row["blobs"]
        if not blobs:
            self.report.unit(label, row["id"], None, "missing", detail="没有任何已存内容")
            return
        held = [(b, Q.verdict(self.store, b)) for b in blobs]
        held = [(b, e) for b, e in held if e]
        if held:                                    # a standing verdict wins; nothing is scanned
            for b, e in held:
                self._reject(b, e.get("signature"), e.get("engine"), None)
            self.report.unit(label, row["id"], held[0][0], "applied", detail=held[0][1].get("signature") or "")
            return
        verdicts = [ex.examine(b) for b in blobs]
        infected = [v for v in verdicts if v.outcome == "infected"]
        for v in infected:
            self._reject(v.sha256, v.signature, v.engine, v.data)
        standing = [(v, Q.verdict(self.store, v.sha256)) for v in verdicts if v.outcome == "missing"]
        standing = [(v, e) for v, e in standing if e]
        for v, e in standing:
            self._reject(v.sha256, e.get("signature"), e.get("engine"), None)
        if infected:
            self.report.unit(label, row["id"], infected[0].sha256, "infected", detail=infected[0].signature or "")
            return
        if standing:
            self.report.unit(label, row["id"], standing[0][0].sha256, "applied", detail=standing[0][1].get("signature") or "")
            return
        for outcome in ("mismatch", "missing"):
            bad = [v.sha256 for v in verdicts if v.outcome == outcome]
            if bad:
                self.report.unit(label, row["id"], bad[0], outcome, detail=f"{len(bad)}/{len(blobs)} 份内容")
                return
        errors = [v for v in verdicts if v.outcome == "error"]
        big = [v for v in verdicts if v.outcome == "too_large"]
        prev = prev_rec or {}
        if errors:
            publish(row, RS.attempt_record("error", errors[0].reason or "", errors[0].sha256, prev, blobs=len(blobs)))
            self.report.unit(label, row["id"], errors[0].sha256, "error", detail=errors[0].reason or "")
            return
        if big:
            limit = ex.scanner.max_bytes
            if not (RS.scan_status(prev) == "too_large" and prev.get("limit") == limit):
                publish(row, RS.attempt_record("too_large", big[0].reason or "", big[0].sha256, prev, blobs=len(blobs), limit=limit))
            self.report.unit(label, row["id"], big[0].sha256, "too_large", detail=f"limit={limit}")
            return
        first = next(v for v in verdicts if v.result is not None)
        made = publish(row, RS.clean_record(first.result, blobs[0], prev, blobs=len(blobs)))
        self.report.unit(label, row["id"], blobs[0], "clean" if made else "changed",
                         detail=f"{len(blobs)} 份内容" if len(blobs) > 1 else "")

    def _publish_template(self, t: dict, rec: dict) -> bool:
        """A template verdict covers the contents that were scanned: a version added meanwhile
        (new bytes) or another writer's record means nothing is written and the next run looks again."""
        with self.store.tx():
            cur = self.store.one("SELECT id, original_blob, info, scan FROM templates WHERE id=? AND deleted=0", (t["id"],))
            if not cur or cur.get("scan") != t.get("scan") or template_blobs(self.store, cur) != t["blobs"]:
                return False
            self.store.update("templates", {"id": t["id"]}, {"scan": rec})
        return True

    def _publish_publication(self, p: dict, rec: dict) -> bool:
        with self.store.tx():
            cur = self.store.one("SELECT id, working_blob, info FROM organization_templates WHERE id=?", (p["id"],))
            if not cur or _pub_scan(cur) != _pub_scan(p) or publication_blobs(self.store, cur) != p["blobs"]:
                return False
            info = dict(cur.get("info") or {})
            info["scan"] = rec
            self.store.update("organization_templates", {"id": p["id"]}, {"info": info})
        return True

    # ------------------------------------------------------------------ infected bytes
    def _reject(self, sha: str, signature: str | None, engine: str | None, data: bytes | None) -> None:
        """Every Docs row that points at these bytes is rejected; the bytes (and any regression
        case holding a copy) move to the quarantine. `data` is None when another application
        already moved them there (a standing verdict applied).

        Order, so an interruption at any point is finished by the next run (`quarantine.settle`
        and the "applied" path): the quarantine copy and index first, then regression copies, then
        the rows, then the blob store copy."""
        if sha in self._rejected:
            return                                  # every row on these bytes was marked the first time
        self._rejected.add(sha)
        store = self.store
        srcs = store.all("SELECT s.id, s.project_id, s.status, s.scan, s.created_by, s.filename, s.removed, "
                         "p.tenant, p.deleted AS project_deleted FROM source_files s LEFT JOIN projects p ON p.id=s.project_id "
                         "WHERE s.blob=? AND COALESCE(s.status,'')<>'rejected'", (sha,))
        tpls = store.all(_TEMPLATES_WITH, (sha, sha, sha))
        pubs = store.all(_PUBS_WITH, (sha, sha, sha))
        rows = ([{"kind": "source_files", "id": r["id"], "workspace": r.get("tenant"),
                  "previous": {"status": r.get("status"), "scan": r.get("scan")}} for r in srcs]
                + [{"kind": "templates", "id": r["id"], "workspace": r.get("tenant"), "previous": {"scan": r.get("scan")}} for r in tpls]
                + [{"kind": "organization_templates", "id": r["id"], "workspace": r.get("source_tenant"),
                    "previous": {"scan": _pub_scan(r)}} for r in pubs])
        Q.hold(store, sha, data, signature=signature, engine=engine, rows=rows, by=self.by)
        cases, case_errors = self._move_regression_copies(sha)
        if cases:
            Q.hold(store, sha, None, signature=signature, engine=engine, rows=cases, by=self.by)
        rec = RS.infected_record(signature, engine, sha)
        err = REJECT_ERROR.format(sig=signature or "—")
        ts = now()
        late: list[dict] = []
        known = {(r["kind"], r["id"]) for r in rows}
        marked_tpls: list[dict] = []
        with store.tx():
            for r in store.all("SELECT s.id, s.status, s.scan, p.tenant FROM source_files s LEFT JOIN projects p ON p.id=s.project_id "
                               "WHERE s.blob=? AND COALESCE(s.status,'')<>'rejected'", (sha,)):
                store.update("source_files", {"id": r["id"]}, {"status": "rejected", "scan": rec, "error": err, "updated_at": ts})
                if ("source_files", r["id"]) not in known:
                    late.append({"kind": "source_files", "id": r["id"], "workspace": r.get("tenant"),
                                 "previous": {"status": r.get("status"), "scan": r.get("scan")}})
            for r in store.all(_TEMPLATES_WITH, (sha, sha, sha)):
                if not _is_infected(r.get("scan")):      # already rejected for other bytes: that record stays
                    store.update("templates", {"id": r["id"]}, {"scan": rec, "updated_at": ts})
                    marked_tpls.append(r)
                if ("templates", r["id"]) not in known:
                    late.append({"kind": "templates", "id": r["id"], "workspace": r.get("tenant"), "previous": {"scan": r.get("scan")}})
            for r in store.all(_PUBS_WITH, (sha, sha, sha)):
                if not _is_infected(_pub_scan(r)):
                    info = dict(r.get("info") or {})
                    info["scan"] = rec
                    store.update("organization_templates", {"id": r["id"]}, {"info": info})
                if ("organization_templates", r["id"]) not in known:
                    late.append({"kind": "organization_templates", "id": r["id"], "workspace": r.get("source_tenant"),
                                 "previous": {"scan": _pub_scan(r)}})
            # Previews filled from a rejected template are not served any more: grants made since
            # CI-90 name the template (and the read gate refuses them); older unnamed grants in the
            # template's workspace are dropped — a preview is recomputed on demand.
            for r in tpls:
                store.execute("DELETE FROM blob_grants WHERE kind IN (?, ?) OR (tenant=? AND kind IN ('preview','layout'))",
                              (f"preview:{r['id']}", f"layout:{r['id']}", r.get("tenant") or "default"))
            # A preview or layout PDF is filled with the sources' values and its grant does not say
            # which source: in the workspace of a rejected source those grants go (they are
            # recomputed on demand, and the rejected source is not read any more). A preview is
            # often byte-identical to a generated file of the same sample, so without this the
            # output a job made from the source stayed readable by its blob id. A desensitized copy
            # is a rewrite of one source's own package: its grant names the source since CI-90 and
            # goes with it; a grant from before that names nothing and goes too, in that workspace
            # only — it cannot be told apart from a copy of the rejected file.
            for ws in sorted({r.get("tenant") or "default" for r in srcs}):
                store.execute("DELETE FROM blob_grants WHERE tenant=? AND (kind IN ('preview','layout','desensitized') "
                              "OR kind LIKE 'preview:%' OR kind LIKE 'layout:%')", (ws,))
            for r in srcs:
                store.execute("DELETE FROM blob_grants WHERE kind=?", (f"desensitized:{r['id']}",))
        if late:
            Q.hold(store, sha, None, signature=signature, engine=engine, rows=late, by=self.by)
        if Q.bytes_path(store, sha).is_file():
            Q.remove_from_store(store, sha)          # verifies the quarantine copy first
        # who is told: the uploader of each live source (inbox + mail per the catalogue's docs.file_rejected)
        told, nobody = 0, 0
        for r in srcs:
            if r.get("removed") or r.get("project_deleted"):
                continue
            if r.get("created_by"):
                self.app._notify_rejected(r)
                told += 1
            else:
                nobody += 1
        for r in srcs:
            audit_event(store, "source.rescan_rejected", actor_kind="system", actor_id=self.by, workspace_id=r.get("tenant"),
                        object_kind="source", object_id=r["id"], meta={"signature": signature, "engine": engine})
        for r in tpls:
            audit_event(store, "template.rescan_rejected", actor_kind="system", actor_id=self.by, workspace_id=r.get("tenant"),
                        object_kind="template", object_id=r["id"], meta={"signature": signature, "engine": engine})
        for r in pubs:
            audit_event(store, "organization_template.rescan_rejected", actor_kind="system", actor_id=self.by,
                        workspace_id=r.get("source_tenant"), object_kind="organization_template", object_id=r["id"],
                        meta={"signature": signature, "engine": engine, "organization_id": r.get("organization_id")})
        lines = []
        if srcs:
            lines.append(f"来源 {len(srcs)} 行：{', '.join(r['id'] for r in srcs[:20])}" + ("…" if len(srcs) > 20 else ""))
        if tpls:
            lines.append(f"模板 {len(tpls)} 个：{', '.join(r['id'] for r in tpls[:20])}" + ("…" if len(tpls) > 20 else "")
                         + "（模板没有记录上传者，无人可通知）")
        if pubs:
            lines.append(f"组织共享模板 {len(pubs)} 个：{', '.join(r['id'] for r in pubs[:20])}" + ("…" if len(pubs) > 20 else ""))
        if cases:
            lines.append(f"回归用例 {len(cases)} 个整体移入隔离区：{', '.join(c['id'] for c in cases)}")
        for e in case_errors:
            lines.append(f"回归用例 {e['id']} 没能移入隔离区（{e['error']}），它仍含这些字节：请人工处理后再运行 rescan --apply")
        if srcs:
            lines.append(f"已通知上传者 {told} 行" + (f"；{nobody} 行没有上传者记录（机器接口或早期上传），无人可通知" if nobody else ""))
        others = Q.unmarked_references(store, sha).get("messages") or 0
        if others:
            lines.append(f"Synwevo Message 仍有 {others} 个附件引用这些字节：请在 messages 容器运行 python -m synwevo_messages.cli rescan --apply")
        jobs = [j["id"] for j in store.all("SELECT id FROM jobs WHERE instr(snapshot, ?) > 0 ORDER BY created_at DESC LIMIT 21", (sha,))]
        if jobs:
            lines.append(f"用过这些字节的生成任务：{', '.join(jobs[:20])}" + ("…" if len(jobs) > 20 else "")
                         + "（它们的产物、打包下载与链接一律拒绝）")
        self.report.quarantined.append({"sha256": sha, "signature": signature, "lines": lines})

    def _move_regression_copies(self, sha: str) -> tuple[list[dict], list[dict]]:
        """A captured regression case holds verbatim copies of a project's sources and template
        (regression.py). One that holds these bytes moves aside whole — the case would not replay
        without them anyway — and comes back on release. A case that cannot be moved is reported,
        not fatal: the rest of the verdict (rows, quarantine) still happens."""
        from .regression import cases_root
        root = cases_root(self.store.root)
        if not root.is_dir():
            return [], []
        moved, errors = [], []
        for ws in sorted(p for p in root.iterdir() if p.is_dir()):
            for case in sorted(p for p in ws.iterdir() if p.is_dir() and (p / "case.json").is_file()):
                ident = f"{ws.name}/{case.name}"
                try:
                    if not any(_digest(f) == sha for f in case.rglob("*") if f.is_file() and f.name != "case.json"):
                        continue
                    dest = Q.aside_dir(self.store, sha, create=True) / "regression" / ws.name / case.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(case), str(dest))
                except OSError as e:
                    errors.append({"id": ident, "error": f"{type(e).__name__} errno={getattr(e, 'errno', None)}"})
                    continue
                moved.append({"kind": "regression_case", "id": ident, "workspace": ws.name, "previous": {"dir": ident}})
        return moved, errors


def _other_held_digest(store: Store, case_dir: Path, released: str) -> str | None:
    """A case moved aside may also hold other bytes whose verdict still stands: it stays aside."""
    held = Q.standing(store) - {released}
    if not held or not case_dir.is_dir():
        return None
    for f in case_dir.rglob("*"):
        if f.is_file() and not f.is_symlink() and f.name != "case.json":
            d = _digest(f)
            if d in held:
                return d
    return None


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------- release (reviewed false positive)
def _rescan_all(runner: DocsRescan, ex: RS.Examiner, blobs: list[str], released: str, prev_rec, *, released_res,
                ref: dict) -> tuple[dict, str | None]:
    """The record a multi-content row (template, publication) gets when one verdict on it is
    withdrawn: every other content is looked at again. Another standing verdict, or a fresh
    detection, keeps it rejected under that verdict; all clean makes it clean; anything else leaves
    a non-clean record, so it is usable as it was before the rescan and the next rescan looks again.
    Returns (record, the digest that still holds it or None). The row is added to the verdict
    that still holds it, so releasing that one later reaches it too (``ref``: its index row)."""
    for b in blobs:
        if b != released:
            e = Q.verdict(runner.store, b)
            if e:
                Q.hold(runner.store, b, None, signature=e.get("signature"), engine=e.get("engine"), rows=[ref], by=runner.by)
                return RS.infected_record(e.get("signature"), e.get("engine"), b), b
    worst = None
    for b in blobs:
        if b == released:
            continue
        try:
            v = ex.examine(b)
        except RS.ScannerLost as lost:
            v = RS.Verdict("error", b, reason=str(lost)[:200])
        if v.outcome == "infected":
            runner._reject(b, v.signature, v.engine, v.data)
            return RS.infected_record(v.signature, v.engine, b), b
        if v.outcome != "clean" and worst is None:
            worst = v
    if worst is not None:
        status = "too_large" if worst.outcome == "too_large" else "error"
        reason = worst.reason or {"missing": "content missing", "mismatch": "content does not match its digest"}.get(worst.outcome, "")
        return RS.attempt_record(status, reason, worst.sha256, prev_rec, blobs=len(blobs), released=True), None
    return RS.clean_record(released_res, blobs[0] if blobs else released, prev_rec, blobs=len(blobs), released=True), None


def release(app, sha: str, by: str, out=None) -> int:
    """Put the bytes back and restore this application's rows — only when the current engine says
    the bytes are clean, i.e. after the false positive was withdrawn from the signature database.
    Takes the Docs run lock, so it never interleaves with a rescan of the same rows."""
    out = out or sys.stdout
    store: Store = app.store
    try:
        with RS.run_lock(store, "docs"):
            return _release(app, sha, by, out)
    except RS.RescanBusy as e:
        print(f"{e}；等它结束后再恢复。", file=sys.stderr)
        return RS.EXIT_USAGE


def _release(app, sha: str, by: str, out) -> int:
    store: Store = app.store
    try:
        entry = Q.load(store, sha)
    except Q.QuarantineError as e:
        print(str(e), file=sys.stderr)
        return RS.EXIT_USAGE
    if not entry:
        print(f"{sha} 没有隔离记录", file=sys.stderr)
        return RS.EXIT_USAGE
    if entry.get("state") == "purged":
        print(f"{sha} 已清除：不能恢复", file=sys.stderr)
        return RS.EXIT_USAGE
    mine = Q.pending(entry, set(DOCS_KINDS))
    if not mine:
        print(f"{sha} 没有待恢复的 Synwevo Docs 行（其余行由各自的应用恢复）", file=out)
        return RS.EXIT_OK
    try:
        scanner, engine = RS.open_scanner()
    except RS.ScannerUnavailable as e:
        print(str(e), file=sys.stderr)
        return RS.EXIT_SCANNER
    try:
        data = Q.read_any(store, sha)
    except Q.QuarantineError as e:
        print(str(e), file=sys.stderr)
        return RS.EXIT_FINDINGS
    try:
        res = scanner.scan(data, sha256=sha)
    except M.ScanUnavailable as e:
        print(f"扫描器不可达：{str(e)[:200]}", file=sys.stderr)
        return RS.EXIT_SCANNER
    if res.status != "clean":
        print(f"当前引擎（{res.engine}）对 sha256={sha} 的结论仍是 {res.status}"
              + (f"（{res.signature}）" if res.signature else "") + "：签名撤回之前不能恢复。", file=out)
        return RS.EXIT_FINDINGS
    Q.put_back(store, sha, data)                 # a damaged store copy is replaced, never kept
    runner = DocsRescan(app, RS.Report("Synwevo Docs"), by)
    ex = RS.Examiner(store, scanner)
    ex.cache[sha] = RS.Verdict("clean", sha, engine=res.engine, result=res)
    from .regression import cases_root
    restored = {"source_files": 0, "templates": 0, "organization_templates": 0, "regression_case": 0}
    done: set[tuple[str, str]] = set()
    kept: list[str] = []
    still: list[str] = []
    reparse: list[str] = []
    ts = now()
    for r in mine:
        prev = r.get("previous") or {}
        key = (r["kind"], r["id"])
        if r["kind"] == "source_files":
            with store.tx():
                cur = store.one("SELECT status, scan FROM source_files WHERE id=?", (r["id"],))
                if not cur or cur.get("status") != "rejected":
                    done.add(key)                # gone, or no longer rejected: nothing of it is held here
                    continue
                if (cur.get("scan") or {}).get("sha256") != sha:
                    kept.append(r["id"])
                    continue
                # A usable row goes back as it was (its parsed results, OCR and corrections are still
                # there). Any other row is parsed again: parsing is a function of the bytes and the
                # options, so its status and the parser's own sentence come back exactly, and the
                # index never had to keep error text (which can quote the file).
                back = prev.get("status") if prev.get("status") in ("ready", "needs_ocr") else "parsing"
                store.update("source_files", {"id": r["id"]}, {"status": back, "error": None, "updated_at": ts,
                                                               "scan": RS.clean_record(res, sha, prev.get("scan"), released=True)})
            if back == "parsing":
                reparse.append(r["id"])
            restored["source_files"] += 1
            done.add(key)
        elif r["kind"] in ("templates", "organization_templates"):
            is_tpl = r["kind"] == "templates"
            cur = (store.one("SELECT id, original_blob, info, scan FROM templates WHERE id=?", (r["id"],)) if is_tpl
                   else store.one("SELECT id, working_blob, info FROM organization_templates WHERE id=?", (r["id"],)))
            rec_now = (cur or {}).get("scan") if is_tpl else _pub_scan(cur or {})
            if not cur or not _is_infected(rec_now):
                done.add(key)
                continue
            if rec_now.get("sha256") != sha:
                done.add(key)                    # held by another verdict, which lists it too
                still.append(r["id"])
                continue
            blobs = template_blobs(store, cur) if is_tpl else publication_blobs(store, cur)
            rec, holder = _rescan_all(runner, ex, blobs, sha, prev.get("scan"), released_res=res,
                                      ref={"kind": r["kind"], "id": r["id"], "workspace": r.get("workspace"),
                                           "previous": {"scan": prev.get("scan")}})
            with store.tx():
                if is_tpl:
                    store.update("templates", {"id": r["id"]}, {"scan": rec, "updated_at": ts})
                else:
                    info = dict(cur.get("info") or {})
                    info["scan"] = rec
                    store.update("organization_templates", {"id": r["id"]}, {"info": info})
            done.add(key)
            if holder:
                still.append(r["id"])
            else:
                restored[r["kind"]] += 1
        elif r["kind"] == "regression_case":
            src = Q.aside_dir(store, sha) / "regression" / r["id"]
            dest = cases_root(store.root) / r["id"]
            other = _other_held_digest(store, src, sha)
            if other:
                kept.append(f"{r['id']}（还含另一份隔离中的内容 {other[:16]}）")
                continue
            if src.is_dir() and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                restored["regression_case"] += 1
                done.add(key)
            else:
                kept.append(r["id"])
    Q.release(store, sha, done, by=by, engine=res.engine)
    for sid in reparse:
        app._parse_source(sid)
    audit_event(store, "quarantine.released", actor_kind="system", actor_id=by, object_kind="quarantine", object_id=sha,
                meta={"engine": res.engine, "app": "docs", **restored, "kept": len(kept)})
    print(f"已恢复 sha256={sha}（当前引擎 {res.engine} 判定干净）：来源 {restored['source_files']} 行，模板 "
          f"{restored['templates']} 个，组织共享模板 {restored['organization_templates']} 个，回归用例 {restored['regression_case']} 个。", file=out)
    if still:
        print(f"仍被其他隔离结论拒绝（那份内容另行复核）：{', '.join(still)}", file=out)
    for q in runner.report.quarantined:
        print(f"恢复时重扫模板的其他内容发现感染，已隔离 sha256={q['sha256']} 签名={q.get('signature') or '-'}", file=out)
    if kept:
        print(f"没有改动、仍在隔离中（状态已被他人改动，或回归用例的原位置已被占用）：{', '.join(kept)}", file=out)
    left = Q.pending(Q.load(store, sha) or {})
    if left:
        print(f"仍有 {len(left)} 行待各自的应用恢复（Synwevo Message 用 python -m synwevo_messages.cli quarantine release {sha}）", file=out)
    # Nothing came back although rows were waiting: someone has to look (exit 1), as for a rescan.
    return RS.EXIT_FINDINGS if (kept or still) and not any(restored.values()) else RS.EXIT_OK


# ---------------------------------------------------------------------- command entry points
def _open(data: str):
    from .services import App
    store = Store(data)
    try:
        return App(store), None
    except ValueError as e:                     # contradictory scan configuration
        return None, str(e)


def main_rescan(args, data: str) -> int:
    app, problem = _open(data)
    if problem:
        print(f"扫描配置无效：{problem}", file=sys.stderr)
        return RS.EXIT_USAGE
    if args.limit is not None and args.limit < 1:
        print("--limit 必须是正整数", file=sys.stderr)
        return RS.EXIT_USAGE
    store: Store = app.store
    ws = getattr(args, "workspace", None)
    if ws and not (store.one("SELECT 1 AS x FROM workspaces WHERE id=?", (ws,))
                   or store.one("SELECT 1 AS x FROM projects WHERE COALESCE(tenant,'default')=? LIMIT 1", (ws,))
                   or store.one("SELECT 1 AS x FROM templates WHERE COALESCE(tenant,'default')=? LIMIT 1", (ws,))):
        print(f"没有找到工作空间 {ws!r}", file=sys.stderr)
        return RS.EXIT_USAGE
    runner = DocsRescan(app, RS.Report("Synwevo Docs"), RS.cli_actor("docs"))
    only = getattr(args, "only", None)
    if not getattr(args, "apply", False):
        return _dry_run(runner, ws, only, args.limit)
    try:
        scanner, engine = RS.open_scanner()
    except RS.ScannerUnavailable as e:
        print(f"{e}。没有读取或改动任何内容。", file=sys.stderr)
        return RS.EXIT_SCANNER
    try:
        with RS.run_lock(store, "docs"):
            rep = runner.report
            rep.say(f"补扫 Synwevo Docs：扫描引擎 {engine}，扫描上限 {scanner.max_bytes} 字节"
                    + (f"，工作空间 {ws}" if ws else "") + (f"，本次最多 {args.limit} 份" if args.limit else ""))
            for s in Q.settle(store):
                rep.say(f"上次中断留下的存储副本：sha256={s['sha256']} "
                        + {"removed": "已从存储移走", "moved": "已移入隔离区", "store_copy_damaged": "与摘要不符，未动（请人工核对）"}[s["action"]])
            ex = RS.Examiner(store, scanner)
            try:
                runner.run(ex, workspace=ws, only=only, limit=args.limit)
            except RS.ScannerLost as e:
                rep.stopped = str(e)
            audit_event(store, "rescan.run", actor_kind="system", actor_id=runner.by, object_kind="rescan", object_id="docs",
                        meta={"engine": engine, "workspace": ws, "limit": args.limit, "scans": ex.scans, "stopped": bool(rep.stopped),
                              "counts": {k: dict(c) for k, c in rep.counts.items()}})
            return rep.summary(engine)
    except RS.RescanBusy as e:
        print(f"{e}；等它结束后再运行。", file=sys.stderr)
        return RS.EXIT_USAGE


def _dry_run(runner: DocsRescan, ws: str | None, only: str | None, limit: int | None) -> int:
    rep = runner.report
    try:
        max_bytes = M.policy().max_bytes
    except ValueError:
        max_bytes = M.DEFAULT_MAX_BYTES
    rep.say("补扫预览（Synwevo Docs，只读：不扫描、不写入；加 --apply 才会扫描并写入）" + (f"：工作空间 {ws}" if ws else ""))
    units = runner.units(workspace=ws, only=only)
    srcs = [u for k, u in units if k == "source"]
    tpls = [u for k, u in units if k == "template"]
    pubs = [u for k, u in units if k == "publication"]

    def by_status(recs) -> str:
        c: dict[str, int] = {}
        for rec in recs:
            st = RS.scan_status(rec) or "unscanned"
            c[st] = c.get(st, 0) + 1
        return "，".join(f"{k} {n}" for k, n in sorted(c.items())) or "无"

    if only in (None, "sources"):
        big = sum(1 for g in srcs if max(r.get("size") or 0 for r in g["rows"]) > max_bytes)
        rep.say(f"来源：{len(srcs)} 份内容（{sum(len(g['rows']) for g in srcs)} 行）；按原记录："
                + by_status(r.get("scan") for g in srcs for r in g["rows"]) + f"；超过扫描上限（{max_bytes} 字节）{big} 份")
    if only in (None, "templates"):
        rep.say(f"模板：{len(tpls)} 个（{len({b for t in tpls for b in t['blobs']})} 份不同内容）；按原记录："
                + by_status(t.get("scan") for t in tpls))
        rep.say(f"组织共享模板：{len(pubs)} 个（{len({b for p in pubs for b in p['blobs']})} 份不同内容）；按原记录："
                + by_status(_pub_scan(p) for p in pubs))
    if only in (None, "cases"):
        cases = [u for k, u in units if k == "case"]
        rep.say(f"回归用例：{len({c for g in cases for c in g['cases']})} 个用例里 {len(cases)} 份没有干净记录担保的内容（每次运行都会检查）")
    held = [e for e in Q.entries(runner.store) if e.get("state") in Q.STANDING and runner.store.has_blob(e["sha256"])]
    if held:
        rep.say(f"已有隔离结论、存储里却还有副本的内容 {len(held)} 份：--apply 会先把它们移走")
    if limit:
        rep.say(f"--limit {limit}：一次运行最多扫描 {limit} 份（共 {len(units)} 份待看；已有结论、缺失、超限的不计入）")
    state = RS.scanner_state()
    rep.say(f"扫描器：{state}")
    return RS.EXIT_OK if state.startswith("可用") else RS.EXIT_SCANNER


def main_quarantine(args, data: str) -> int:
    by = RS.cli_actor("docs")
    if args.action == "list":
        return RS.print_entries(Store(data))
    if not args.sha256:
        print(f"quarantine {args.action} 需要完整的 SHA-256", file=sys.stderr)
        return RS.EXIT_USAGE
    if args.action == "purge":
        return RS.purge(Store(data), args.sha256.strip().lower(), yes=args.yes, by=by, app="docs")
    app, problem = _open(data)
    if problem:
        print(f"扫描配置无效：{problem}", file=sys.stderr)
        return RS.EXIT_USAGE
    return release(app, args.sha256.strip().lower(), by)

