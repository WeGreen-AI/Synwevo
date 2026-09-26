"""Bulk member import HTTP ceremonies and the runner that executes a confirmed import.

Flow (docs/platform/member-import.md): upload → scanned → read by the document engine →
mapping + preview (per-row plan) → confirm (recent `admin` re-authentication) → rows run in the
background → per-row results, a report and one inbox item. The importer's authority is checked
at every step, and once more per row when it runs; organization roles on new people are applied
when *they* accept, as the importer and under the rules that hold then.

Mounted by the identity app: build_router(store, ident, sessions, audit, invites, mailer, ingest=…).
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from synwevo_core.errors import ServiceError
from synwevo_core.member_import import (IMPORTABLE_WORKSPACE_ROLES, MAX_FILE_BYTES, MAX_ROWS, MemberImports,
                                        ROW_ACTIONS)
from synwevo_core.notifications import Notifier
from synwevo_core.org_roles import OrgRoles
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import Sessions
from synwevo_core.store import now

from . import mail as mail_mod
from .invites import INVITE_TTL_S, ROLE_LABEL
from .withdrawals import record as journal

log = logging.getLogger("synwevo.member_import")
REAUTH_SCOPE = "admin"


class MemberImportRunner:
    """One background thread per account process: claims rows of running imports one at a
    time, so a crash or restart leaves at most one row `running` (re-queued on resume)."""

    def __init__(self, store, ident, invites, mailer, audit, imports: MemberImports, notifier: Notifier, *, start: bool = True):
        self.store, self.ident, self.invites, self.mailer, self.audit = store, ident, invites, mailer, audit
        self.imports, self.notifier = imports, notifier
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        # rows left `running` by a previous process go back to the queue
        self.store.execute("UPDATE member_import_rows SET status='pending', updated_at=? WHERE status='running'", (now(),))
        if start:
            self._thread = threading.Thread(target=self._run, name="member-import", daemon=True)
            self._thread.start()

    def alive(self) -> bool:
        return self._thread is None or self._thread.is_alive()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception as e:  # noqa: BLE001 - a worker thread must not die
                log.warning("member import runner error: %s", type(e).__name__)
                busy = 0
            self._wake.wait(1.0 if busy else 10.0)
            self._wake.clear()

    def run_once(self, limit: int = 50) -> int:
        """Process up to `limit` rows across running imports; returns the number processed."""
        done = 0
        for imp in self.store.all("SELECT id FROM member_imports WHERE status='running' ORDER BY confirmed_at LIMIT 20"):
            done += self._process(imp["id"], limit - done)
            if done >= limit:
                break
        return done

    def _process(self, iid: str, budget: int) -> int:
        processed = 0
        while processed < budget:
            imp = self.imports.get(iid)
            if not imp or imp["status"] != "running":
                return processed
            if imp["cancel_requested"]:
                self._finish(imp, cancelled=True)
                return processed
            row = self.imports.claim_row(iid)
            if row is None:
                self._finish(imp)
                return processed
            processed += 1
            try:
                result, inv_id = self._execute(imp, row)
                self.imports.finish_row(row["id"], "done", result, invitation_id=inv_id)
            except ServiceError as e:
                self.imports.finish_row(row["id"], "failed", {"error": e.code, "message": e.message[:200]})
            except Exception as e:  # noqa: BLE001
                log.warning("member import row failed: %s", type(e).__name__)
                self.imports.finish_row(row["id"], "failed", {"error": "internal_error", "message": type(e).__name__})
        return processed

    def _finish(self, imp: dict, *, cancelled: bool = False) -> None:
        out = self.imports.finish(imp["id"], cancelled=cancelled)
        o = (out["summary"] or {}).get("outcome") or {}
        self.audit(None, "organization.member_import.finished", actor_id=imp["created_by"], object_kind="member_import",
                   object_id=imp["id"], result="error" if out["status"] == "failed" else "ok",
                   meta={"organization_id": imp["organization_id"], "status": out["status"], **o})
        outcome = {"finished": {"zh-Hans": "已完成", "zh-Hant": "已完成", "en": "finished"},
                   "partial": {"zh-Hans": "部分完成", "zh-Hant": "部分完成", "en": "partly finished"},
                   "failed": {"zh-Hans": "失败", "zh-Hant": "失敗", "en": "failed"},
                   "cancelled": {"zh-Hans": "已取消", "zh-Hant": "已取消", "en": "was cancelled"}}[out["status"]]
        user = self.ident.get_user(imp["created_by"]) or {}
        try:
            self.notifier.emit(imp["created_by"], "member_import.finished",
                               params={"filename": imp["filename"], "outcome": outcome.get(user.get("locale") or "zh-Hans", outcome["en"]),
                                       "invited": o.get("invited", 0), "updated": o.get("updated", 0), "skipped": o.get("skipped", 0), "failed": o.get("failed", 0)},
                               organization_id=imp["organization_id"], object_kind="member_import", object_id=imp["id"],
                               dedupe_key=f"member_import:{imp['id']}:{out['status']}",
                               target={"origin": "accounts", "path": f"/organizations/{imp['organization_id']}?import={imp['id']}"})
        except Exception as e:  # noqa: BLE001
            log.warning("member import notification skipped: %s", type(e).__name__)
        _delete_upload(self.store, imp["id"])

    # ------------------------------------------------------------------ one row
    def _execute(self, imp: dict, row: dict) -> tuple[dict, str | None]:
        actor, oid, wid = imp["created_by"], imp["organization_id"], imp["workspace_id"]
        f = row["fields"] or {}
        # the importer's authority, as it is now — not as it was when the file was uploaded
        Organizations(self.store, self.ident).require(oid, actor, permission="org.members.import", active=True)
        ws = self.ident.get_workspace(wid)
        if not ws or ws["status"] != "active":
            raise ServiceError(409, "workspace_frozen" if ws and ws["status"] == "frozen" else "workspace_unavailable", "该工作空间当前不能增加成员")
        me = self.ident.get_user(actor) or {}
        if row["action"] == "invite":
            self.invites.may_invite(actor, wid, f["workspace_role"])
            inv, raw = self.invites.create(workspace_id=wid, email=f["email"], role=f["workspace_role"], actor_id=actor,
                                           can_export=False, ttl_s=INVITE_TTL_S)
            for old in inv.get("superseded_ids") or []:
                journal(self.store, "invitation_revoked", invitation_id=old)
            recipient = self.ident.by_email(inv["email_key"])
            locale = (recipient or {}).get("locale") or me.get("locale") or "zh-Hans"
            self.store.execute("UPDATE invitations SET locale=? WHERE id=?", (locale, inv["id"]))
            subject, text, html = mail_mod.invitation_mail(workspace=ws["name"], inviter=me.get("display_name") or me.get("email") or "—",
                                                           role_label=ROLE_LABEL.get(inv["role"], inv["role"]), token=raw,
                                                           expires_h=INVITE_TTL_S // 3600, locale=locale)
            try:
                self.mailer.enqueue(inv["email_key"], subject, text, kind="invite", ref_id=inv["id"], html=html)
            except ServiceError:
                self.invites.revoke(inv["id"], actor)
                raise
            self.audit(None, "invite.create", actor_id=actor, workspace_id=wid, object_kind="invitation", object_id=inv["id"],
                       result="ok", meta={"role": inv["role"], "import_id": imp["id"]})
            result: dict = {"invitation": inv["id"], "mail": "queued"}
            if recipient:
                try:
                    self.notifier.emit(recipient["id"], "invitation.received",
                                       params={"workspace": ws["name"], "inviter": me.get("display_name") or me.get("email") or "—", "role": inv["role"]},
                                       workspace_id=wid, object_kind="invitation", object_id=inv["id"], dedupe_key=f"invite:{inv['id']}",
                                       actionable=True, target={"origin": "accounts", "path": "/onboarding"})
                except Exception as e:  # noqa: BLE001
                    log.warning("inbox item skipped: %s", type(e).__name__)
                # already an organization member: the roles do not have to wait for the accept
                if f.get("org_role_ids") and (f.get("existing") or {}).get("org_member"):
                    have = [r["id"] for r in OrgRoles(self.store).assigned(oid, recipient["id"])]
                    out = OrgRoles(self.store).assign(oid, actor, recipient["id"], sorted(set(have) | set(f["org_role_ids"])))
                    result["roles"] = {"assigned": out["added"]}
            return result, inv["id"]
        if row["action"] == "update":
            user = self.ident.by_email(f["email"])
            if not user:
                raise ServiceError(409, "account_unavailable", "该账户不存在")
            diff = row.get("diff") or {}
            result = {}
            if diff.get("workspace_role"):
                to = diff["workspace_role"]["to"]
                self.invites.may_invite(actor, wid, to)          # may grant that role here?
                out = self.ident.change_role(wid, user["id"], to, actor_id=actor)
                self.audit(None, "member.role", actor_id=actor, workspace_id=wid, object_kind="membership", object_id=user["id"],
                           result="ok", meta={"before": out.get("before"), "after": {"role": to}, "import_id": imp["id"]})
                result["workspace_role"] = {"from": (out.get("before") or {}).get("role"), "to": to}
            if diff.get("org_roles"):
                have = [r["id"] for r in OrgRoles(self.store).assigned(oid, user["id"])]
                out = OrgRoles(self.store).assign(oid, actor, user["id"], sorted(set(have) | set(diff["org_roles"].get("add_ids") or [])))
                self.audit(None, "organization.role.assign", actor_id=actor, object_kind="organization_membership", object_id=user["id"],
                           result="ok", meta={"organization_id": oid, "added": out["added"], "removed": out["removed"], "import_id": imp["id"]})
                result["roles"] = {"assigned": out["added"]}
                if out["added"]:
                    org = self.store.one("SELECT name FROM organizations WHERE id=?", (oid,)) or {}
                    try:
                        self.notifier.emit(user["id"], "organization.roles_assigned",
                                           params={"organization": org.get("name") or "—", "roles": "、".join(r["name"] for r in out["roles"]) or "—",
                                                   "actor": me.get("display_name") or me.get("email") or "—"},
                                           organization_id=oid, object_kind="organization_membership", object_id=user["id"],
                                           target={"origin": "accounts", "path": f"/organizations/{oid}"})
                    except Exception as e:  # noqa: BLE001
                        log.warning("inbox item skipped: %s", type(e).__name__)
            return result, None
        raise ServiceError(409, "bad_action", f"不能执行 {row['action']} 行")


# ---------------------------------------------------------------------- the uploaded bytes
def _upload_dir(store) -> Path:
    d = Path(store.root) / "member_imports"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _save_upload(store, iid: str, data: bytes) -> None:
    p = _upload_dir(store) / iid
    p.write_bytes(data)
    p.chmod(0o600)


def _read_upload(store, iid: str) -> bytes | None:
    p = _upload_dir(store) / iid
    return p.read_bytes() if p.is_file() else None


def _delete_upload(store, iid: str) -> None:
    p = _upload_dir(store) / iid
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def build_router(store, ident, sessions, audit, invites, mailer, *, ingest, start_jobs: bool = True) -> APIRouter:
    r = APIRouter(prefix="/api/auth/organizations", tags=["organization-governance"])
    orgs = Organizations(store, ident)
    imports = MemberImports(store, ident)
    notifier = Notifier(store)
    runner = MemberImportRunner(store, ident, invites, mailer, audit, imports, notifier, start=start_jobs)
    r.runner = runner
    r.imports = imports

    def session(request: Request) -> dict:
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if (not current or current["revoked_at"] is not None or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current

    def actor(request: Request, oid: str, *, sensitive: bool = False) -> str:
        s = session(request)
        if sensitive and not Sessions.reauth_fresh(s, REAUTH_SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": REAUTH_SCOPE})
        orgs.require(oid, s["user_id"], permission="org.members.import", active=True)
        return s["user_id"]

    def load(oid: str, iid: str) -> dict:
        imp = imports.get(iid)
        if not imp or imp["organization_id"] != oid:
            raise ServiceError(404, "no_import", "找不到该导入")
        return imp

    def invite_check_for(uid: str, wid: str):
        def check(role: str) -> str | None:
            try:
                invites.may_invite(uid, wid, role)
                return None
            except ServiceError as e:
                return e.code
        return check

    @r.get("/{oid}/imports/capabilities")
    def capabilities(request: Request, oid: str):
        actor(request, oid)
        return {"ingest": ingest.describe(), "limits": {"rows": MAX_ROWS, "file_bytes": MAX_FILE_BYTES},
                "workspace_roles": list(IMPORTABLE_WORKSPACE_ROLES), "actions": list(ROW_ACTIONS)}

    @r.get("/{oid}/imports")
    def list_imports(request: Request, oid: str, limit: int = Query(20, ge=1, le=100)):
        actor(request, oid)
        return {"items": imports.list(oid, limit=limit)}

    @r.post("/{oid}/imports", status_code=201)
    async def upload(request: Request, oid: str, file: UploadFile = File(...), workspace_id: str = Form(""), ocr: str = Form("")):
        uid = actor(request, oid)
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ServiceError(413, "file_too_large", f"成员文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限")
        if not data:
            raise ServiceError(400, "empty_file", "文件是空的")
        filename = Path(file.filename or "members").name[:200] or "members"
        scan = ingest.accept(filename, data)                       # 400 / 503 / 413 on refusal
        extraction = ingest.extract(data, filename, ocr=ocr in ("1", "true", "on"))
        imp = imports.create(oid, uid, filename=filename, sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                             extraction=extraction, scan=scan, workspace_id=workspace_id.strip() or None)
        if extraction.get("status") in ("ready", "needs_ocr"):
            _save_upload(store, imp["id"], data)                    # kept only for OCR-on-request; deleted when done
        audit(request, "organization.member_import.upload", actor_id=uid, object_kind="member_import", object_id=imp["id"], result="ok",
              meta={"organization_id": oid, "format": extraction.get("format"), "status": extraction.get("status"),
                    "tables": len(extraction.get("tables") or []), "lines": len(extraction.get("lines") or []), "scan": (scan or {}).get("status")})
        return imports.public(imp)

    @r.get("/{oid}/imports/{iid}")
    def get_import(request: Request, oid: str, iid: str, full: int = Query(0, ge=0, le=1)):
        actor(request, oid)
        return imports.public(load(oid, iid), with_extraction=bool(full))

    @r.post("/{oid}/imports/{iid}/ocr")
    def run_ocr(request: Request, oid: str, iid: str):
        """Read a scan or image with OCR, on request: it is slow and the person should ask for it."""
        uid = actor(request, oid)
        imp = load(oid, iid)
        if imp["status"] not in ("extracted", "planned"):
            raise ServiceError(409, "import_not_editable", "这个导入已经开始执行")
        data = _read_upload(store, iid)
        if data is None:
            raise ServiceError(410, "upload_gone", "原文件已不再保留，请重新上传")
        extraction = ingest.extract(data, imp["filename"], ocr=True)
        out = imports.update_extraction(iid, extraction)
        audit(request, "organization.member_import.ocr", actor_id=uid, object_kind="member_import", object_id=iid,
              result="ok" if extraction.get("status") in ("ready", "needs_ocr") else "error",
              meta={"organization_id": oid, "status": extraction.get("status"), "lines": len(extraction.get("lines") or []),
                    "engine": (extraction.get("ocr") or {}).get("engine")})
        return imports.public(out)

    @r.put("/{oid}/imports/{iid}/mapping")
    def set_mapping(request: Request, oid: str, iid: str, body: dict = Body(...)):
        """`{"workspace_id", "mapping": {...}}` → the plan: every row's action, reason and diff."""
        uid = actor(request, oid)
        load(oid, iid)
        wid = str(body.get("workspace_id") or "").strip()
        if not wid:
            raise ServiceError(400, "bad_workspace", "请选择目标工作空间", {"field": "workspace_id"})
        out = imports.plan(iid, body.get("mapping") or {}, actor_id=uid, workspace_id=wid, invite_check=invite_check_for(uid, wid))
        audit(request, "organization.member_import.plan", actor_id=uid, object_kind="member_import", object_id=iid, result="ok",
              meta={"organization_id": oid, "workspace_id": wid, **((out["summary"] or {}).get("counts") or {})})
        return imports.public(out)

    @r.get("/{oid}/imports/{iid}/rows")
    def rows(request: Request, oid: str, iid: str, action: str = Query("", pattern="^(|invite|update|unchanged|skip|blocked)$"),
             limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
        actor(request, oid)
        load(oid, iid)
        return {"items": imports.rows(iid, action=action or None, limit=limit, offset=offset)}

    @r.post("/{oid}/imports/{iid}/confirm")
    def confirm(request: Request, oid: str, iid: str, body: dict = Body(default={})):
        """Start executing the plan. Recent `admin` re-authentication, like every organization
        write that grants something; `only_valid` accepts a plan that has blocked rows."""
        uid = actor(request, oid, sensitive=True)
        load(oid, iid)
        out = imports.confirm(iid, version=body.get("version"), only_valid=bool(body.get("only_valid")))
        audit(request, "organization.member_import.confirm", actor_id=uid, object_kind="member_import", object_id=iid, result="ok",
              meta={"organization_id": oid, "only_valid": bool(body.get("only_valid")), "reason": str(body.get("reason") or "")[:200]})
        runner.wake()
        return imports.public(out)

    @r.post("/{oid}/imports/{iid}/cancel")
    def cancel(request: Request, oid: str, iid: str):
        uid = actor(request, oid)
        load(oid, iid)
        out = imports.request_cancel(iid)
        audit(request, "organization.member_import.cancel", actor_id=uid, object_kind="member_import", object_id=iid, result="ok",
              meta={"organization_id": oid})
        runner.wake()
        return imports.public(out)

    @r.post("/{oid}/imports/{iid}/retry")
    def retry(request: Request, oid: str, iid: str):
        uid = actor(request, oid, sensitive=True)
        load(oid, iid)
        out = imports.retry_failed(iid)
        audit(request, "organization.member_import.retry", actor_id=uid, object_kind="member_import", object_id=iid, result="ok",
              meta={"organization_id": oid})
        runner.wake()
        return imports.public(out)

    @r.get("/{oid}/imports/{iid}/report.csv")
    def report(request: Request, oid: str, iid: str):
        actor(request, oid)
        load(oid, iid)
        buf = io.StringIO()
        csv.writer(buf).writerows(imports.report_rows(iid))
        return Response(("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="member-import-{iid}.csv"', "Cache-Control": "no-store"})

    return r
