"""Copying a personal project into an organization workspace: the manifest the approver sees,
the digest that pins what was approved, and the executor that clones rows into the target
tenant once everything still holds (state machine: synwevo_core.project_copies).

What is copied: the template (its pinned version, or the head, as a new template with one
version), the project's rule/output/step, the current mapping revision, and — only when the
requester chose it — the ready source files, with or without their OCR text and corrections.
Blobs are content-addressed and shared; new rows in the target tenant make them reachable there
(authz.blob_readable) without moving bytes. Never copied: jobs, artifacts, uploads, grants,
webhooks, API keys, provenance of the original, or anything from another project.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading

from synwevo_core.context import as_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.notifications import Notifier
from synwevo_core.project_copies import ProjectCopies
from synwevo_core.store import new_id, now
from synwevo_core import workers

log = logging.getLogger("synwevo.copies")


def announce_request(store, ident, copies: ProjectCopies, notifier, r: dict, requester_id: str, project_name: str) -> int:
    """Tell the people who may decide a new copy request that it is waiting for them (inbox,
    actionable). The requester is not told about their own request. Shared by the workbench and
    the API, so both ask the same people the same way. Returns how many were told."""
    me = ident.get_user(requester_id) or {}
    ws = store.one("SELECT name FROM workspaces WHERE id=?", (r["target_workspace_id"],)) or {}
    told = 0
    for who in copies.deciders(r["target_organization_id"], r["target_workspace_id"]):
        if who == requester_id:
            continue
        try:
            notifier.emit(who, "copy.requested", params={"requester": me.get("display_name") or me.get("email") or "—", "project": project_name,
                                                         "workspace": ws.get("name") or "—"},
                          organization_id=r["target_organization_id"], workspace_id=r["target_workspace_id"], object_kind="project_copy",
                          object_id=r["id"], dedupe_key=f"copy:{r['id']}:requested:{who}", actionable=True,
                          target={"origin": "accounts", "path": f"/organizations/{r['target_organization_id']}?approval={r['id']}"})
            told += 1
        except Exception as e:  # noqa: BLE001 - the request stands even if one inbox item does not
            log.warning("copy request notification skipped: %s", type(e).__name__)
    return told


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_manifest(app, pid: str, scope: dict) -> tuple[dict, str]:
    """Inside the source tenant. Names, sizes, hashes and counts — never file bodies."""
    p = app.get_project(pid)
    tpl = ver = None
    if p.get("template_id"):
        tpl = app.get_template(p["template_id"])
        ver = app.get_version(tpl["id"], p.get("template_version") or tpl["head"])
    mappings = (app.get_mappings(pid) or {}).get("mappings") or []
    # The public source view deliberately hides the blob hash; the manifest needs it (the approver
    # sees what will be copied, the executor re-checks it), so read the rows after get_project's
    # tenant check — the same check list_sources relies on.
    sources = [s for s in app.store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,))
               if s.get("status") == "ready"]
    src_rows = [{"id": s["id"], "filename": s["filename"], "size": s.get("size") or 0, "format": s.get("format"), "family": s.get("family"),
                 "sha256": s.get("blob"), "version": s.get("version") or 1, "scan": ((s.get("scan") or {}).get("status") if isinstance(s.get("scan"), dict) else None),
                 "has_ocr": bool(s.get("ocr")), "has_corrections": bool(s.get("corrections"))} for s in sources]
    manifest = {
        "project": {"id": p["id"], "name": p["name"], "step": p.get("step"), "rev": p.get("rev"), "record_rule": p.get("record_rule") or {}, "output": p.get("output") or {}},
        "template": ({"id": tpl["id"], "name": tpl["name"], "kind": tpl.get("kind"), "format": tpl.get("format"), "version_no": ver["version_no"],
                      "pinned": bool(p.get("template_version")), "working_sha256": ver.get("working_blob"), "slots": len(ver.get("slots") or []),
                      "scan": ((tpl.get("scan") or {}).get("status") if isinstance(tpl.get("scan"), dict) else None)} if tpl else None),
        "mappings": {"rev": p.get("mapping_rev") or 0, "count": len(mappings), "enabled": sum(1 for m in mappings if m.get("enabled", True))},
        "sources": {"included": bool(scope.get("sources")), "count": len(src_rows), "total_bytes": sum(s["size"] for s in src_rows),
                    "items": src_rows if scope.get("sources") else [{k: s[k] for k in ("filename", "size", "format")} for s in src_rows]},
        "corrections": {"included": bool(scope.get("sources")) and bool(scope.get("corrections"))},
        "excluded": ["jobs", "artifacts", "uploads", "blob_grants", "webhooks", "api_keys", "provenance"],
    }
    digest = hashlib.sha256(_canonical({
        "project": [p["id"], p.get("rev"), p.get("template_id"), p.get("template_version"), p.get("mapping_rev"), p.get("record_rule"), p.get("output")],
        "template": [tpl["id"], ver["version_no"], ver.get("working_blob"), _canonical(ver.get("slots") or [])] if tpl else None,
        "sources": sorted([s["id"], s["sha256"], s["version"]] for s in src_rows) if scope.get("sources") else None,
    }).encode()).hexdigest()
    return manifest, digest


_COPY_REASONS = {
    "source_changed": {"zh-Hans": "源项目在批准后有改动，批准范围不再有效。", "zh-Hant": "來源項目在批准後有改動，批准範圍不再有效。",
                       "en": "The source project changed after approval, so the approved scope no longer applies."},
    "approval_expired": {"zh-Hans": "批准已过期。", "zh-Hant": "批准已過期。", "en": "The approval has expired."},
    "source_authority_lost": {"zh-Hans": "申请人不再拥有源项目。", "zh-Hant": "申請人不再擁有來源項目。",
                              "en": "The requester no longer owns the source project."},
    "decider_authority_lost": {"zh-Hans": "审批者不再具有审批权限。", "zh-Hant": "審批者不再具有審批權限。",
                               "en": "The approver no longer has approval rights."},
    "organization_unavailable": {"zh-Hans": "目标组织当前不可用。", "zh-Hant": "目標組織目前不可用。",
                                 "en": "The target organization is currently unavailable."},
    "target": {"zh-Hans": "申请人已不能把项目复制到目标工作空间。", "zh-Hant": "申請人已不能把項目複製到目標工作空間。",
               "en": "The requester can no longer copy the project into the target workspace."},
    "delegation": {"zh-Hans": "发起申请的应用委托已失效，请重新授权并申请。", "zh-Hant": "發起申請的應用委託已失效，請重新授權並申請。",
                   "en": "The integration's delegation is no longer valid. Authorize it again before making a new request."},
    "source": {"zh-Hans": "源项目已不可用。", "zh-Hant": "來源項目已不可用。", "en": "The source project is no longer available."},
    "transfer": {"zh-Hans": "当前数据流政策或导出权限不允许复制到目标空间，请核对后重新申请。",
                 "zh-Hant": "目前資料流政策或匯出權限不允許複製到目標空間，請核對後重新申請。",
                 "en": "The current data transfer policy or export rights do not allow this copy. Check them before requesting it again."},
    "failed": {"zh-Hans": "复制过程出错，没有创建任何内容。", "zh-Hant": "複製過程出錯，沒有建立任何內容。",
               "en": "Copying failed and nothing was created."},
    "copy_recovery_ambiguous": {"zh-Hans": "发现无法唯一确认的既有副本，已保留全部内容，需要管理员核对。",
                                "zh-Hant": "發現無法唯一確認的既有副本，已保留全部內容，需要管理員核對。",
                                "en": "Existing copies could not be uniquely reconciled. All content was preserved for administrator review."},
}


def copy_failure_reason(reason: str | None, locale: str) -> str:
    """The sentence for why a copy did not run, in the recipient's language. The reason stored on
    the request is a code (a precondition, `target:<code>`, `source:<code>`, or the failing
    exception's class name); the notification used to carry it — or a Chinese sentence in every
    language — as its body (CI-86)."""
    key = reason or ""
    if key not in _COPY_REASONS:
        key = key.split(":", 1)[0] if key.split(":", 1)[0] in ("target", "source", "delegation", "transfer") else "failed"
    text = _COPY_REASONS[key]
    return text.get(locale, text["en"])


class CopyExecutor:
    """Runs one approved copy. All authority and the digest are re-checked here, not trusted
    from the approval; the copy is one transaction and the original is never written to."""

    def __init__(self, app, copies: ProjectCopies, notifier: Notifier | None = None):
        self.app, self.copies, self.notifier = app, copies, notifier

    def run(self, cid: str) -> dict:
        r = self.copies.get(cid)
        if not r:
            raise ServiceError(404, "no_copy_request", "找不到该复制申请")
        if r["status"] != "approved":
            # Listed as approved, but by now another runner claimed or finished it (a self-approval
            # wakes the runner thread while another tick has listed the same request), or it was
            # withdrawn or expired meanwhile. Not ours to judge: this used to mark it stale — over
            # a copy whose project already existed in the target — and tell both people it failed.
            return r
        why = self.copies.preconditions(r)
        if why:
            return self._stale(r, why, why)
        with as_tenant(r["source_workspace_id"]):
            try:
                manifest, digest = build_manifest(self.app, r["source_project_id"], r["scope"] or {})
            except ServiceError as e:
                return self._stale(r, f"source:{e.code}", e.code)
        if digest != r["digest"]:
            return self._stale(r, "source_changed", "source_changed")
        claimed = self.copies.claim_approved(cid)
        if not claimed:
            return self.copies.get(cid)
        try:
            # Claiming stays visible to other runners, but publication and its terminal
            # record are one commit. A restart can only recover an unpublished claim; it
            # cannot replay a copy whose project committed before its `done` record.
            with self.app.store.tx():
                current = self.copies.get(cid)
                if current["status"] != "running" or current["version"] != claimed["version"]:
                    return current
                # The earlier checks are advisory. Under the write lock, repeat both
                # authority and snapshot checks before copying the rows they describe.
                why = self.copies.preconditions({**current, "status": "approved"})
                if why:
                    return self._stale(current, why, why, claimed_version=claimed["version"])
                with as_tenant(current["source_workspace_id"]):
                    try:
                        manifest, digest = build_manifest(self.app, current["source_project_id"], current["scope"] or {})
                    except ServiceError as e:
                        return self._stale(current, f"source:{e.code}", e.code, claimed_version=claimed["version"])
                if digest != current["digest"]:
                    return self._stale(current, "source_changed", "source_changed", claimed_version=claimed["version"])
                result = self._copy(current, manifest)
                out = self.copies.finish(cid, "done", result=result, expected_version=claimed["version"])
                if out is None:
                    raise RuntimeError("Copy claim changed before publication")
        except Exception as e:  # noqa: BLE001 - whatever failed, the request records it and nothing half-made stays visible
            log.warning("project copy %s failed: %s", cid, type(e).__name__)
            out = self.copies.finish(cid, "failed", error=f"{type(e).__name__}", expected_version=claimed["version"])
            if out is None:
                return self.copies.get(cid)
            self._tell(out, "copy.failed", outcome_key="failed", reason=type(e).__name__)
            return out
        self._tell(out, "copy.done")
        return out

    def _stale(self, r: dict, error: str, reason: str, *, claimed_version: int | None = None) -> dict:
        """approved → stale, told to both people — only when this runner made that transition; a
        request someone else took or withdrew between the checks and now is left as it is."""
        out = self.copies.reset_stale(r["id"], error=error, claimed_version=claimed_version)
        if out is None:
            return self.copies.get(r["id"])
        self._tell(out, "copy.failed", outcome_key="stale", reason=reason)
        return out

    def _copy(self, r: dict, manifest: dict) -> dict:
        src_ws, dst_ws, pid = r["source_workspace_id"], r["target_workspace_id"], r["source_project_id"]
        scope = r["scope"] or {}
        store = self.app.store
        with as_tenant(src_ws):
            p = self.app.get_project(pid)
            tpl = self.app.get_template(p["template_id"]) if p.get("template_id") else None
            ver = self.app.get_version(tpl["id"], p.get("template_version") or tpl["head"]) if tpl else None
            mappings = (self.app.get_mappings(pid) or {}).get("mappings") or []
            sources = ([s for s in store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,)) if s.get("status") == "ready"]
                       if scope.get("sources") else [])
            full_sources = {s["id"]: s for s in sources}
        ts = now()
        new_pid = new_id("p")
        warnings: list[str] = []
        with store.tx():
            new_tid = None
            if tpl:
                new_tid = new_id("t")
                store.insert("templates", {"id": new_tid, "tenant": dst_ws, "name": tpl["name"][:100], "kind": tpl.get("kind"), "format": tpl.get("format"),
                                           "original_blob": tpl.get("original_blob"), "original_name": tpl.get("original_name"), "head": 1,
                                           "created_at": ts, "updated_at": ts, "info": tpl.get("info"), "scan": tpl.get("scan")})
                store.insert("template_versions", {"template_id": new_tid, "version_no": 1, "parent_no": None, "working_blob": ver.get("working_blob"),
                                                   "slots": ver.get("slots"), "editor": self.app.editor_of(tpl["id"], ver, tpl.get("kind")),
                                                   "note": f"复制自个人项目模板「{tpl['name']}」v{ver['version_no']}（申请 {r['id']}）", "created_at": ts})
            store.insert("projects", {"id": new_pid, "name": r["name"][:100], "created_at": ts, "updated_at": ts, "tenant": dst_ws,
                                      "origin": f"copy:{r['id']}"[:80], "record_rule": p.get("record_rule") or {}, "output": p.get("output") or {},
                                      "template_id": new_tid, "template_version": (1 if (tpl and p.get("template_version")) else None),
                                      "mapping_rev": 1, "rev": 1, "step": p.get("step") or "sources"})
            id_map: dict[str, str] = {}
            for s in sources:
                row = full_sources[s["id"]]
                nid = new_id("s")
                id_map[s["id"]] = nid
                store.insert("source_files", {
                    "id": nid, "project_id": new_pid, "filename": row["filename"], "ord": row.get("ord"), "blob": row.get("blob"), "size": row.get("size"),
                    "format": row.get("format"), "family": row.get("family"), "status": "ready", "error": None, "notes": row.get("notes"),
                    "structure": row.get("structure"), "signature": row.get("signature"), "parsed_blob": row.get("parsed_blob"), "working_blob": row.get("working_blob"),
                    "options": row.get("options"), "version": row.get("version") or 1, "representative": row.get("representative") or 0,
                    "ocr": row.get("ocr") if scope.get("corrections") else None, "corrections": row.get("corrections") if scope.get("corrections") else None,
                    "scan": row.get("scan"), "created_at": ts, "updated_at": ts, "removed": 0})
            data = []
            for m in mappings:
                m = json.loads(json.dumps(m))
                if m.get("source_file"):
                    m["source_file"] = id_map.get(m["source_file"]) if id_map else None
                    if m["source_file"] is None:
                        m.pop("source_file", None)
                if isinstance(m.get("overrides"), dict):
                    if id_map:
                        remapped = {}
                        for k, v in m["overrides"].items():
                            head, sep, rest = k.partition("~")
                            if head in id_map:
                                remapped[id_map[head] + sep + rest] = v
                        m["overrides"] = remapped
                    else:
                        m.pop("overrides", None)
                data.append(m)
            if not sources and any(m.get("source_file") or m.get("overrides") for m in mappings):
                warnings.append("mappings_source_refs_dropped")
            store.insert("mapping_revs", {"project_id": new_pid, "rev": 0, "parent_rev": None, "data": [], "note": "初始", "created_at": ts})
            store.insert("mapping_revs", {"project_id": new_pid, "rev": 1, "parent_rev": 0, "data": data, "note": f"复制自申请 {r['id']}", "created_at": ts})
        return {"project_id": new_pid, "template_id": new_tid, "sources": len(sources), "mappings": len(data), "warnings": warnings,
                "manifest_digest": r["digest"]}

    def _tell(self, r: dict, kind: str, *, outcome_key: str | None = None, reason: str | None = None) -> None:
        if not self.notifier:
            return
        m = r.get("manifest") or {}
        ws = self.app.store.one("SELECT name FROM workspaces WHERE id=?", (r["target_workspace_id"],)) or {}
        base = {"project": (m.get("project") or {}).get("name") or r["name"], "workspace": ws.get("name") or "—"}
        for who, locale_user in ((r["requester_id"], r["requester_id"]), (r.get("decided_by"), r.get("decided_by"))):
            if not who or (who == r["requester_id"] and locale_user != r["requester_id"]):
                continue
            params = dict(base)
            if kind == "copy.failed":
                u = self.app.store.one("SELECT locale FROM users WHERE id=?", (who,)) or {}
                loc = u.get("locale") or "zh-Hans"
                outcome = {"stale": {"zh-Hans": "需要重新申请", "zh-Hant": "需要重新申請", "en": "needs a new request"},
                           "failed": {"zh-Hans": "失败", "zh-Hant": "失敗", "en": "failed"}}[outcome_key or "failed"]
                params["outcome"] = outcome.get(loc, outcome["en"])
                params["reason"] = copy_failure_reason(reason, loc)
            path = f"/p/{(r.get('result') or {}).get('project_id')}" if kind == "copy.done" and who == r["requester_id"] else None
            try:
                self.notifier.emit(who, kind, params=params, organization_id=r["target_organization_id"], workspace_id=r["target_workspace_id"],
                                   object_kind="project_copy", object_id=r["id"], dedupe_key=f"copy:{r['id']}:{kind}:{who}",
                                   target=({"origin": "docs", "path": path} if path else {"origin": "accounts", "path": f"/organizations/{r['target_organization_id']}?approval={r['id']}"}))
            except Exception as e:  # noqa: BLE001
                log.warning("copy notification skipped: %s", type(e).__name__)


class CopyRunner:
    """Executes approved copies in the background of the Docs process (10 s tick)."""

    def __init__(self, executor: CopyExecutor, copies: ProjectCopies, *, start: bool = True):
        self.executor, self.copies = executor, copies
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._recover_running()
        if start:
            self._thread = threading.Thread(target=self._run, name="project-copies", daemon=True)
            self._thread.start()
            workers.register(self, self._thread)

    def _recover_running(self) -> None:
        """Reconcile old split commits before retrying. Older releases could commit the
        cloned project and crash before recording `done`; its durable origin identifies
        the already published result. Ambiguous publications are never executed again."""
        store, recovered = self.copies.store, []
        with store.tx():
            for r in store.all("SELECT * FROM project_copies WHERE status='running'"):
                published = store.all("SELECT id, tenant, template_id FROM projects WHERE origin=?", (f"copy:{r['id']}",))
                if not published:
                    store.execute("UPDATE project_copies SET status='approved', updated_at=?, version=version+1 WHERE id=?",
                                  (now(), r["id"]))
                    continue
                if len(published) != 1 or published[0]["tenant"] != r["target_workspace_id"]:
                    out = self.copies.finish(r["id"], "failed", error="copy_recovery_ambiguous", expected_version=r["version"])
                    recovered.append((out, "copy.failed"))
                    continue
                p = published[0]
                mappings = store.one("SELECT data FROM mapping_revs WHERE project_id=? AND rev=1", (p["id"],)) or {}
                sources = store.one("SELECT COUNT(*) AS n FROM source_files WHERE project_id=?", (p["id"],))["n"]
                result = {"project_id": p["id"], "template_id": p["template_id"], "sources": sources,
                          "mappings": len(mappings.get("data") or []), "warnings": ["recovered_previous_publication"],
                          "manifest_digest": r["digest"]}
                out = self.copies.finish(r["id"], "done", result=result, expected_version=r["version"])
                recovered.append((out, "copy.done"))
        for out, kind in recovered:
            if out:
                self.executor._tell(out, kind, reason=out.get("error"))

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        """A copy in progress finishes its transaction; nothing half-made is ever visible."""
        self._stop.set()
        self._wake.set()

    def alive(self) -> bool:
        return self._thread is None or self._thread.is_alive()

    def run_once(self) -> int:
        self.copies.sweep()
        n = 0
        for r in self.copies.next_approved():
            self.executor.run(r["id"]); n += 1
        return n

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001
                log.warning("copy runner error: %s", type(e).__name__)
            self._wake.wait(10.0)
            self._wake.clear()
