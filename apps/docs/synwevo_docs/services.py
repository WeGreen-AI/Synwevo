"""Application services shared by the HTTP API and background workers."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from synwevo_core import quarantine as Q
from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError, not_found
from synwevo_core.store import Store, new_id, now
from synwevo_core import workers

from . import authz
from . import malware as M

# Acceptance events for the operator's log: ids, hashes, verdicts and engine versions — never
# file names chosen by users, never bytes, never paths (docs/security/file-acceptance.md §8).
acceptance_log = logging.getLogger("synwevo_docs.acceptance")
from .engine import sources as S
from .engine import templates as T
from .engine.detect import FAMILY
from .engine.limits import LIMITS
from .engine.resolve import SourceDoc


def scan_out(rec: dict | None) -> dict:
    """What clients see of a scan verdict: never the engine's raw reply, never a path.
    Rows written before scanning existed are `unscanned` — honest, and distinct from clean."""
    if not rec:
        return {"status": "unscanned"}
    keys = ("status", "signature", "engine", "reason", "sha256", "checked_at", "attempts", "retry_at")
    return {k: rec[k] for k in keys if k in rec}

def template_blobs(store: Store, t: dict) -> list[str]:
    """Every stored content of a template: its working file, an image template's upload and the
    working file of every version. Ordered, without repeats (CI-90: what a verdict can apply to)."""
    out: list[str] = []
    info = t.get("info") if isinstance(t.get("info"), dict) else {}
    img = (info or {}).get("image") or {}
    for b in [t.get("original_blob"), img.get("original_blob")] + [
            v["working_blob"] for v in store.all("SELECT working_blob FROM template_versions WHERE template_id=? ORDER BY version_no", (t["id"],))]:
        if b and b not in out:
            out.append(b)
    return out


def earlier_malicious(filename: str) -> ServiceError:
    """These bytes carry a standing verdict (quarantined or purged): refused, never stored again."""
    return ServiceError(400, "malware_detected", f"文件「{filename}」的内容此前已被判定为恶意内容，已拒绝",
                        {"filename": filename, "earlier": True})


PALETTE = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed", "#0891b2", "#dc2626", "#65a30d", "#c026d3",
           "#0d9488", "#ea580c", "#4f46e5", "#be123c", "#15803d", "#a16207", "#0369a1"]


class App:
    def __init__(self, store: Store):
        self.store = store
        self.parse_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="parse")
        workers.register(self)                          # so a process shutdown releases the pool
        # Malware scanning policy (synwevo_docs.malware): decided once at start-up so a bad
        # configuration fails the process instead of silently scanning nothing.
        self.scan_policy = M.policy()
        self.scanner = M.scanner_for(self.scan_policy)
        self._scan_retry_lock = threading.Lock()
        self._scan_timers: dict[str, threading.Timer] = {}
        self._src_cache: dict[tuple, SourceDoc] = {}
        self._cache_lock = threading.Lock()
        self.on_event = None          # set by the API layer: called after a job event is stored
        # Set by the API layer: (tid, head, editor, reason) after a template version operation, so an
        # open live-editing room follows a save, restore, undo, redo or deletion.
        self.on_template_change = None
        # Set by the API layer: (pid, kind, detail) after a change to a project's sources, mappings or
        # settings — recorded as attributed activity and pushed to whoever has the project open.
        self.on_project_change = None
        # Set by the API layer: (tid) -> {dirty, online, last_by} — whether the template's live
        # editing room holds edits not yet saved as a version, so a restore/undo/redo does not
        # silently throw other people's work away (9.13: never overwrite unsynced content unasked).
        self.live_template_state = None

    # ================================================================ projects
    def create_project(self, name: str, origin: str | None = None) -> dict:
        """``origin`` records who created this project ("ui", "api:<key id>", "test:<run id>").
        It is the only evidence a cleanup tool can act on — a name prefix is not proof."""
        pid = new_id("p")
        t = now()
        self.store.insert("projects", {"id": pid, "name": (name or "未命名项目").strip()[:100], "created_at": t, "updated_at": t,
                                       "tenant": current_tenant(), "origin": (origin or "ui")[:80],
                                       "record_rule": {}, "output": {"naming": "{序号}", "formats": [], "error_policy": "block"},
                                       "mapping_rev": 0, "rev": 1, "step": "sources"})
        self.store.insert("mapping_revs", {"project_id": pid, "rev": 0, "parent_rev": None, "data": [], "note": "初始", "created_at": t})
        return self.get_project(pid)

    _PROJECT_COLS = "id, name, created_at, updated_at, template_id, record_rule, step, origin, archived_at, template_version, settings_rev"
    _PROJECT_SORT = {"updated": "updated_at", "created": "created_at", "name": "name COLLATE NOCASE"}

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        """Three queries regardless of project count.

        This used to run a COUNT and a last-job SELECT per project inside a Python loop —
        81 SQLite round-trips for 40 projects, growing linearly with the list."""
        tenant = current_tenant()
        arch = "" if include_archived else " AND archived_at IS NULL"
        rows = self.store.all(
            f"SELECT {self._PROJECT_COLS} "
            f"FROM projects WHERE deleted=0 AND tenant=?{arch} ORDER BY updated_at DESC", (tenant,))
        return self._with_list_details(rows)

    def list_projects_page(self, q: str | None = None, sort: str | None = None, order: str | None = None,
                           offset: int = 0, limit: int = 50, include_archived: bool = False) -> dict:
        """One page of the project list, searched and sorted in SQL.

        The unpaged list sends every project with its details on every visit to the home page;
        with a few thousand projects that is the whole table, every time."""
        sort = sort or "updated"
        order = (order or ("asc" if sort == "name" else "desc")).lower()
        if sort not in self._PROJECT_SORT:
            raise ServiceError(400, "bad_value", "sort 只能是 updated / created / name", {"field": "sort"})
        if order not in ("asc", "desc"):
            raise ServiceError(400, "bad_value", "order 只能是 asc / desc", {"field": "order"})
        try:
            offset, limit = int(offset), int(limit)
        except (TypeError, ValueError) as e:
            raise ServiceError(400, "bad_value", "offset / limit 必须是整数", {"field": "limit"}) from e
        if offset < 0:
            raise ServiceError(400, "bad_value", "offset 不能为负数", {"field": "offset"})
        if not 1 <= limit <= 200:
            raise ServiceError(400, "bad_value", "limit 取值范围是 1–200", {"field": "limit"})
        where = "deleted=0 AND tenant=?" + ("" if include_archived else " AND archived_at IS NULL")
        args: tuple = (current_tenant(),)
        q = (q or "").strip()
        if q:
            # What the list shows is what can be searched: the project name, the template's name
            # and the sample mode's label (the list used to be filtered on the client by name and
            # template name). LIKE wildcards in the search text are literal characters.
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            conds = ["name LIKE ? ESCAPE '\\'",
                     "template_id IN (SELECT id FROM templates WHERE deleted=0 AND tenant=? AND name LIKE ? ESCAPE '\\')"]
            args += (like, current_tenant(), like)
            from .engine.records import MODES
            modes = [k for k, text in MODES.items() if q.lower() in text.split("：", 1)[0].lower()]
            if modes:
                conds.append(f"json_extract(record_rule, '$.mode') IN ({','.join('?' * len(modes))})")
                args += tuple(modes)
            where += " AND (" + " OR ".join(conds) + ")"
        total = self.store.one(f"SELECT COUNT(*) AS n FROM projects WHERE {where}", args)["n"]
        direction = "ASC" if order == "asc" else "DESC"
        rows = self.store.all(f"SELECT {self._PROJECT_COLS} FROM projects WHERE {where} "
                              f"ORDER BY {self._PROJECT_SORT[sort]} {direction}, id {direction} LIMIT ? OFFSET ?",
                              args + (limit, offset))
        items = self._with_list_details(rows)
        nxt = offset + len(items)
        return {"items": items, "total": total, "offset": offset, "limit": limit, "next_offset": nxt if nxt < total else None}

    _IN_CHUNK = 500          # well under SQLite's variable limit (999 on older builds)

    def _chunks(self, ids: list) -> list[list]:
        return [ids[i:i + self._IN_CHUNK] for i in range(0, len(ids), self._IN_CHUNK)]

    def _with_list_details(self, rows: list[dict]) -> list[dict]:
        """Source count, last job and template name for exactly these projects.

        The ids are bound in chunks: the unpaged list passes every project of the workspace,
        and one ``IN (…)`` parameter per project fails past SQLite's variable limit."""
        if not rows:
            return rows
        counts: dict = {}
        last: dict = {}
        for ids in self._chunks([r["id"] for r in rows]):
            marks = ",".join("?" * len(ids))
            counts.update({r["project_id"]: r["n"] for r in self.store.all(
                f"SELECT project_id, COUNT(*) AS n FROM source_files WHERE removed=0 AND project_id IN ({marks}) GROUP BY project_id",
                tuple(ids))})
            last.update({r["project_id"]: r for r in self.store.all(
                "SELECT id, project_id, status, counts, created_at FROM ("
                "  SELECT id, project_id, status, counts, created_at,"
                "         ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at DESC, id DESC) AS rn"
                f"  FROM jobs WHERE project_id IN ({marks})) WHERE rn = 1", tuple(ids))})
        names: dict = {}
        for tids in self._chunks(sorted({r["template_id"] for r in rows if r.get("template_id")})):
            names.update({r["id"]: r["name"] for r in self.store.all(
                f"SELECT id, name FROM templates WHERE deleted=0 AND tenant=? AND id IN ({','.join('?' * len(tids))})",
                (current_tenant(), *tids))})
        for r in rows:
            r["sources"] = counts.get(r["id"], 0)
            j = last.get(r["id"])
            r["last_job"] = {k: j[k] for k in ("id", "status", "counts", "created_at")} if j else None
            r["template_name"] = names.get(r.get("template_id"))
        return rows

    def get_project(self, pid: str) -> dict:
        p = self.store.one("SELECT * FROM projects WHERE id=? AND deleted=0", (pid,))
        if not p or (p.get("tenant") or "default") != current_tenant():
            not_found("项目")          # a foreign tenant gets 404, never "exists but forbidden"
        return p

    def update_project(self, pid: str, values: dict) -> dict:
        p = self.get_project(pid)
        # A caller that says which settings revision it edited is refused when someone else's
        # edit landed first (409 stale_project, with the current revision so it can resync).
        # Callers that say nothing keep the old behaviour; machine clients rarely edit settings.
        base = values.get("base_settings_rev")
        if base is not None:
            try:
                base = int(base)
            except (TypeError, ValueError):
                raise ServiceError(400, "bad_value", "base_settings_rev 必须是整数", {"field": "base_settings_rev"})
            if base != int(p.get("settings_rev") or 0):
                raise ServiceError(409, "stale_project", "项目设置已在其他窗口或被其他人修改，请刷新后再改",
                                   {"settings_rev": int(p.get("settings_rev") or 0), "base_settings_rev": base})
        allowed = {k: v for k, v in values.items() if k in ("name", "record_rule", "output", "template_id", "step")}
        if "archived" in values:
            # Archiving only hides a project from the default list; it stays fully usable and
            # reversible.  Deleting is the separate, destructive `deleted` flag.
            allowed["archived_at"] = now() if values["archived"] else None
        if "record_rule" in allowed:
            self._validate_rule(allowed["record_rule"])
        if "output" in allowed:
            allowed["output"] = self._validate_output(allowed["output"])
        if "template_id" in allowed and allowed["template_id"]:
            self.get_template(allowed["template_id"])
        if "template_version" in values:
            # Pinning used to be accepted and dropped here, so a caller who pinned version N got
            # 200 and then jobs generated from whatever head the template had moved to.
            allowed["template_version"] = self._validate_pin(values["template_version"],
                                                             allowed.get("template_id") or p.get("template_id"))
        elif "template_id" in allowed and allowed["template_id"] != p.get("template_id"):
            allowed["template_version"] = None          # a pin belongs to the template it was made on
        allowed["updated_at"] = now()
        if set(allowed) & {"name", "record_rule", "output", "template_id", "template_version"}:
            allowed["settings_rev"] = int(p.get("settings_rev") or 0) + 1
        self.store.update("projects", {"id": pid}, allowed)
        if set(allowed) - {"name", "step", "updated_at", "output", "settings_rev"}:
            self.store.bump_project(pid)
        # The activity feed shows the values before and after (9.13 D: a sample rule or output
        # setting change reads as what it was → what it is), for the settings that carry meaning.
        shown = [k for k in ("name", "record_rule", "output", "template_id", "template_version") if k in allowed and allowed[k] != p.get(k)]
        self._project_changed(pid, "project.update", {"fields": sorted(k for k in allowed if k not in ("updated_at", "settings_rev")),
                                                      "before": {k: p.get(k) for k in shown}, "after": {k: allowed[k] for k in shown}})
        return self.get_project(pid)

    def _validate_pin(self, version, tid: str | None) -> int | None:
        if version is None or version == "":
            return None
        if isinstance(version, bool):
            raise ServiceError(400, "bad_value", "模板版本必须是正整数", {"field": "template_version"})
        try:
            no = int(version)
        except (TypeError, ValueError) as e:
            raise ServiceError(400, "bad_value", "模板版本必须是正整数", {"field": "template_version"}) from e
        if not tid:
            raise ServiceError(400, "no_template", "请先绑定模板，再固定模板版本", {"field": "template_version"})
        self.get_template(tid)
        if not self.store.one("SELECT 1 AS x FROM template_versions WHERE template_id=? AND version_no=?", (tid, no)):
            raise ServiceError(400, "bad_value", f"模板没有第 {no} 版", {"field": "template_version"})
        return no

    @staticmethod
    def _validate_output(out) -> dict:
        """Project output defaults are checked when they are saved, not coerced when a job runs:
        a stored layout "fitt" used to become "off" at generation time and the job succeeded."""
        if out is None:
            return {}
        if not isinstance(out, dict):
            raise ServiceError(400, "bad_output", "output 必须是对象", {"field": "output"})
        if out.get("layout") not in (None, "", "off", "warn", "fit"):
            raise ServiceError(400, "bad_layout", "layout 只能是 off / warn / fit", {"field": "output.layout"})
        if out.get("error_policy") not in (None, "", "block", "skip"):
            raise ServiceError(400, "bad_error_policy", "error_policy 只能是 block 或 skip", {"field": "output.error_policy"})
        naming = out.get("naming")
        if naming is not None and (not isinstance(naming, str) or len(naming) > 300):
            raise ServiceError(400, "bad_naming", "naming 必须是不超过 300 个字符的文本", {"field": "output.naming", "limit": 300})
        fmts = out.get("formats")
        if fmts is not None and (not isinstance(fmts, list) or not all(isinstance(f, str) for f in fmts)):
            raise ServiceError(400, "bad_formats", "formats 必须是格式名称的数组", {"field": "output.formats"})
        return out

    def delete_project(self, pid: str) -> None:
        self.get_project(pid)
        self.store.update("projects", {"id": pid}, {"deleted": 1, "updated_at": now()})

    @staticmethod
    def _validate_rule(rule: dict) -> None:
        if not isinstance(rule, dict):
            raise ServiceError(400, "bad_rule", "样本规则格式错误")
        mode = rule.get("mode")
        if mode and mode not in ("per_row", "per_file", "per_group", "per_block"):
            raise ServiceError(400, "bad_rule", "未知的样本模式")
        if mode == "per_block":
            spec = rule.get("block") or {}
            if spec.get("match") not in (None, "contains", "exact", "starts", "regex"):
                raise ServiceError(400, "bad_rule", "标记匹配方式无效")
            if spec.get("match") == "regex":
                import re as _re
                try:
                    _re.compile(spec.get("anchor") or "")
                except _re.error as e:
                    raise ServiceError(400, "bad_rule", f"标记的正则表达式无效：{e}",
                                       {"field": "block.anchor", "regex_error": str(e)}) from e

    # ================================================================ sources
    def add_source(self, pid: str, filename: str, data: bytes, actor: str | None = None) -> dict:
        """`actor` is the signed-in person who uploaded (None for machine principals): they are the
        one told if the file is later rejected by the security check."""
        self.get_project(pid)
        if len(data) > LIMITS.max_file_bytes:
            raise ServiceError(413, "too_large", f"文件「{filename}」超过 {LIMITS.max_file_bytes // (1024 * 1024)} MB 上限",
                               {"filename": filename, "limit_mb": LIMITS.max_file_bytes // (1024 * 1024)})
        filename = safe_display_name(filename)
        blob = hashlib.sha256(data).hexdigest()
        if Q.verdict(self.store, blob) or (
                self.scanner is not None and self.store.one("SELECT 1 AS x FROM source_files WHERE blob=? AND status='rejected'", (blob,))):
            # The same bytes were already judged malicious: no second look, no new row — and they
            # are not written back into the store the verdict took them out of (CI-90). A standing
            # quarantine verdict holds even where no scanner is configured.
            raise earlier_malicious(filename)
        blob = self.store.put_blob(data)
        sid = new_id("s")
        mx = self.store.one("SELECT MAX(ord) AS m FROM source_files WHERE project_id=?", (pid,))["m"] or 0
        t = now()
        scanning = self.scanner is not None
        self.store.insert("source_files", {"id": sid, "project_id": pid, "filename": filename, "ord": mx + 1, "blob": blob,
                                           "size": len(data), "status": "scanning" if scanning else "parsing", "notes": [], "structure": {},
                                           "options": {}, "version": 1, "created_at": t, "updated_at": t, "created_by": actor,
                                           "ocr": {}, "corrections": {},
                                           "scan": {"status": "pending"} if scanning else {"status": "not_configured"}})
        has_rep = self.store.one("SELECT 1 AS x FROM source_files WHERE project_id=? AND representative=1 AND removed=0", (pid,))
        if not has_rep:
            self.store.update("source_files", {"id": sid}, {"representative": 1})
        self.store.bump_project(pid)
        self.parse_pool.submit(self._scan_then_parse if scanning else self._parse_source, sid)
        self._project_changed(pid, "source.add", {"source_id": sid, "name": filename})
        return self.get_source(sid)

    # ------------------------------------------------------------------ malware scanning
    SCAN_RETRY_DELAYS = (30, 60, 120, 300, 600)

    def _scan_then_parse(self, sid: str) -> None:
        """The acceptance gate for a source file: nothing parses until the engine said OK.

        Outcomes are terminal states the API and the page can explain: `parsing` (clean, the
        parser takes over), `rejected` (a signature matched; the bytes are quarantined — the
        blob is unlinked and every row sharing that hash is rejected), `scan_failed` (the engine
        was unreachable, timed out or refused the size; the file stays unusable and is retried
        with backoff — an engine outage never lets a file through). The verdict is published
        only if the row is the version and blob that were scanned."""
        with self.store.tx():
            row = self.store.one("SELECT s.* FROM source_files s JOIN projects p ON p.id=s.project_id "
                                 "WHERE s.id=? AND s.removed=0 AND COALESCE(p.deleted,0)=0", (sid,))
        if not row or row["status"] not in ("scanning", "scan_failed"):
            return
        if self.scanner is None:
            self._publish_scan(row, {"status": "not_configured"}, "parsing")
            self._parse_source(sid)
            return
        try:
            data = self.store.get_blob(row["blob"])
        except OSError:
            self._publish_scan(row, {"status": "error", "reason": "content missing"}, "error", error="来源文件内容已不在存储中")
            return
        attempt = int((row.get("scan") or {}).get("attempts") or 0) + 1
        try:
            res = self.scanner.scan(data, sha256=row["blob"])
        except M.ScanUnavailable as e:
            rec = {"status": "error", "reason": str(e)[:200], "attempts": attempt, "checked_at": now()}
            delay = self.SCAN_RETRY_DELAYS[min(attempt, len(self.SCAN_RETRY_DELAYS)) - 1]
            rec["retry_at"] = now() + delay
            self._publish_scan(row, rec, "scan_failed", error="安全检查服务暂不可用，文件已隔离，稍后自动重试")
            acceptance_log.warning("scan.unavailable source=%s tenant=%s sha256=%s attempt=%s retry_in=%ss reason=%s",
                                   sid, row.get("tenant") or "-", row["blob"], attempt, delay, str(e)[:160])
            self._schedule_scan_retry(sid, delay)
            return
        rec = res.as_record() | {"attempts": attempt}
        if res.status == "clean":
            self._publish_scan(row, rec, "parsing", error=None)
            acceptance_log.info("scan.clean source=%s tenant=%s sha256=%s engine=%s attempt=%s",
                                sid, row.get("tenant") or "-", row["blob"], res.engine, attempt)
            self._parse_source(sid)
        elif res.status == "infected":
            self._publish_scan(row, rec, "rejected", error=f"安全检查未通过：文件被判定为恶意内容（{res.signature}），已拒绝并隔离")
            acceptance_log.warning("scan.rejected source=%s tenant=%s sha256=%s signature=%s engine=%s",
                                   sid, row.get("tenant") or "-", row["blob"], res.signature, res.engine)
            self._quarantine_blob(row["blob"], res)
            self._notify_rejected(row)
        else:   # too_large: not scanned is never clean
            self._publish_scan(row, rec, "scan_failed", error=f"文件超过安全检查的大小上限（{self.scan_policy.max_bytes // (1024 * 1024)} MB），无法接纳；请拆分或压缩后重新上传")
            acceptance_log.warning("scan.too_large source=%s tenant=%s sha256=%s size=%s limit=%s",
                                   sid, row.get("tenant") or "-", row["blob"], len(data), self.scan_policy.max_bytes)

    def _notify_rejected(self, row: dict) -> None:
        """The uploader hears that their file was refused (inbox + immediate mail per the
        catalogue); nobody else does, and machine uploads have nobody to tell."""
        who = row.get("created_by")
        if not who:
            return
        try:
            from synwevo_core.notifications import Notifier
            p = self.store.one("SELECT name, tenant FROM projects WHERE id=?", (row["project_id"],)) or {}
            Notifier(self.store).emit(who, "docs.file_rejected", params={"filename": row["filename"], "project": p.get("name") or "—"},
                                      workspace_id=p.get("tenant"), object_kind="source", object_id=row["id"],
                                      dedupe_key=f"source:{row['id']}:rejected", actionable=True,
                                      target={"origin": "docs", "path": f"/p/{row['project_id']}"})
        except Exception:  # noqa: BLE001 - the refusal itself must stand even if the inbox write fails
            logging.getLogger("synwevo_docs.services").exception("docs.file_rejected notification failed for %s", row.get("id"))

    def _publish_scan(self, row: dict, rec: dict, status: str, error: str | None = "") -> None:
        values = {"scan": rec, "status": status, "updated_at": now()}
        if error != "":
            values["error"] = error
        with self.store.tx():
            current = self.store.one("SELECT version, blob, status FROM source_files WHERE id=? AND removed=0", (row["id"],))
            if not current or current["version"] != row["version"] or current["blob"] != row["blob"]:
                return
            if current.get("status") == "rejected" and status != "rejected":
                return          # a rescan rejected these bytes while this scan ran (CI-90): it stands
            self.store.update("source_files", {"id": row["id"]}, values)

    def _schedule_scan_retry(self, sid: str, delay: float) -> None:
        def fire():
            with self._scan_retry_lock:
                self._scan_timers.pop(sid, None)
            self.parse_pool.submit(self._scan_then_parse, sid)
        with self._scan_retry_lock:
            old = self._scan_timers.pop(sid, None)
            if old:
                old.cancel()
            t = threading.Timer(delay, fire)
            t.daemon = True
            self._scan_timers[sid] = t
            t.start()

    def _quarantine_blob(self, blob: str, res: M.ScanResult) -> None:
        """Malicious bytes leave the store. Every source row that shares the hash is rejected
        with the same verdict (same bytes, same judgement), then the file is unlinked so no
        download, preview or parser path can read it back.

        Unless the same bytes are also something stored before scanning (CI-90) — an earlier
        source (the same file uploaded again), a template, a publication or a message attachment:
        that copy may be the only one and a false positive has to come back, so the bytes are held
        in the quarantine with a standing verdict instead, every row recorded with the state it
        had. Every read path of both applications honours the verdict at once, and their rescans
        apply it to their own rows."""
        # Rows that were in use before this upload's verdict: historical copies of the same bytes.
        prior = {r["id"]: r for r in self.store.all(
            "SELECT id, status, scan, created_by, project_id, filename, removed FROM source_files WHERE blob=? "
            "AND COALESCE(status,'') NOT IN ('rejected','scanning','scan_failed')", (blob,))}
        with self.store.tx():
            for r in self.store.all("SELECT id FROM source_files WHERE blob=? AND status!='rejected'", (blob,)):
                self.store.update("source_files", {"id": r["id"]}, {
                    "status": "rejected", "scan": res.as_record(),
                    "error": f"安全检查未通过：文件被判定为恶意内容（{res.signature}），已拒绝并隔离", "updated_at": now()})
        # The uploader of an earlier copy hears that it was withdrawn too, as after a rescan.
        for r in prior.values():
            if not r.get("removed"):
                self._notify_rejected(r)
        others = Q.unmarked_references(self.store, blob)
        if prior or any(others.values()):
            try:
                data = self.store.blob_path(blob).read_bytes()
                if hashlib.sha256(data).hexdigest() == blob:
                    # A row that was being checked goes back to a parse on release; a historical
                    # row goes back to the state it had.
                    rows = [{"kind": "source_files", "id": r["id"], "workspace": None,
                             "previous": ({"status": prior[r["id"]].get("status"), "scan": prior[r["id"]].get("scan")}
                                          if r["id"] in prior else {"status": "scanning", "scan": None})}
                            for r in self.store.all("SELECT id FROM source_files WHERE blob=? AND status='rejected'", (blob,))]
                    Q.hold(self.store, blob, data, signature=res.signature, engine=res.engine, by="upload-gate", rows=rows)
                    Q.remove_from_store(self.store, blob)
            except (OSError, Q.QuarantineError) as e:
                # The rows are rejected either way; the bytes stay in the store for the rescan
                # (never deleted when another row may need them).
                acceptance_log.warning("scan.hold_failed sha256=%s error=%s", blob, type(e).__name__)
            return
        try:
            path = self.store.blob_path(blob)
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def require_accepted(self, row: dict) -> None:
        """The original bytes of a source may leave the server only once the scan said OK — and
        not while a quarantine verdict on them stands, although this row is not marked yet
        (another application's rescan found the bytes first, or a restored backup predates the
        verdict; CI-90). Whatever this process cached of such a row is dropped."""
        st = row.get("status")
        if st == "rejected":
            raise ServiceError(410, "rejected_malware", "该文件未通过安全检查，已被拒绝并隔离", {"scan": scan_out(row.get("scan"))})
        if st in ("scanning", "scan_failed"):
            raise ServiceError(409, "not_yet_accepted", "该文件尚未通过安全检查，暂不能下载", {"status": st, "scan": scan_out(row.get("scan"))})
        held = Q.standing(self.store)
        if held and held.intersection(b for b in (row.get("blob"), row.get("working_blob")) if b):
            with self._cache_lock:
                for k in [k for k in self._src_cache if k[0] == row.get("id")]:
                    del self._src_cache[k]
            raise ServiceError(410, "rejected_malware", "该文件未通过安全检查，已被拒绝并隔离", {"scan": scan_out(row.get("scan"))})

    @staticmethod
    def template_rejected(row: dict) -> bool:
        """A template whose stored contents a rescan found infected (CI-90). Templates have no
        status column: the verdict is the scan record, and every read path asks this."""
        scan = row.get("scan")
        return isinstance(scan, dict) and scan.get("status") == "infected"

    def template_under_verdict(self, row: dict) -> bool:
        """Marked infected, or holding a content whose quarantine verdict stands although this
        row was not marked yet (another application's rescan found the bytes first, or a restored
        backup predates the verdict)."""
        if self.template_rejected(row):
            return True
        held = Q.standing(self.store)
        return bool(held) and bool(held.intersection(template_blobs(self.store, row)))

    def require_template_accepted(self, row: dict) -> None:
        """Preview, download, editing, publishing, copying and generation with a template all
        stop here once its bytes were judged malicious; only listing and deleting it remain."""
        if self.template_under_verdict(row):
            raise ServiceError(410, "rejected_malware", "该文件未通过安全检查，已被拒绝并隔离",
                               {"scan": scan_out(row.get("scan")), "template_id": row.get("id")})

    def scanner_health(self) -> str:
        """For /readyz: `ok`, `off` (no scanner configured — visible, not silent) or `down: <why>`."""
        h = M.health(self.scan_policy)
        if h["status"] == "ok":
            return "ok"
        if h["status"] == "off":
            return "off"
        return "down: " + (h.get("error") or "no PONG")[:120]

    def scan_template_bytes(self, filename: str, data: bytes) -> dict:
        """Templates are small and parsed on the request: the scan is synchronous and a bad
        verdict is the request's error, so nothing is stored."""
        if self.scanner is None:
            return {"status": "not_configured"}
        sha = hashlib.sha256(data).hexdigest()
        try:
            res = self.scanner.scan(data, sha256=sha)
        except M.ScanUnavailable as e:
            acceptance_log.warning("scan.unavailable template sha256=%s reason=%s", sha, str(e)[:160])
            raise ServiceError(503, "scan_unavailable", "安全检查服务暂不可用，请稍后重试", {"retry_after": 30, "reason": str(e)[:120]}) from e
        if res.status == "infected":
            acceptance_log.warning("scan.rejected template sha256=%s signature=%s engine=%s", sha, res.signature, res.engine)
            raise ServiceError(400, "malware_detected", f"文件「{filename}」被判定为恶意内容（{res.signature}），已拒绝",
                               {"signature": res.signature, "filename": filename})
        if res.status == "too_large":
            acceptance_log.warning("scan.too_large template sha256=%s size=%s limit=%s", sha, len(data), self.scan_policy.max_bytes)
            raise ServiceError(413, "scan_limit", f"文件超过安全检查的大小上限（{self.scan_policy.max_bytes // (1024 * 1024)} MB），无法接纳",
                               {"limit_mb": self.scan_policy.max_bytes // (1024 * 1024)})
        return res.as_record()

    def resume_parsing(self) -> int:
        """Parse again whatever a previous process left half-parsed.

        Parsing runs on an in-memory pool, so a restart during an upload left the file
        'parsing' forever: API clients told to poll until it changes polled forever, and job
        creation (which waits for parsing to finish) could never start. Parsing is a pure
        function of the stored bytes and options, so doing it twice is harmless."""
        rows = self.store.all("SELECT s.id, s.status FROM source_files s JOIN projects p ON p.id=s.project_id "
                              "WHERE s.status IN ('parsing','scanning','scan_failed') AND s.removed=0 AND p.deleted=0")
        for r in rows:
            self.parse_pool.submit(self._scan_then_parse if r["status"] in ("scanning", "scan_failed") else self._parse_source, r["id"])
        return len(rows)

    def _parse_source(self, sid: str) -> None:
        # An idempotent upload schedules us before its outer transaction commits. Wait for
        # that writer so an uncommitted source is not mistaken for a missing one. Release
        # the transaction before parsing; a rolled-back upload correctly leaves no row.
        with self.store.tx():
            row = self.store.one("SELECT s.* FROM source_files s JOIN projects p ON p.id=s.project_id "
                                 "WHERE s.id=? AND s.removed=0 AND COALESCE(p.deleted,0)=0", (sid,))
        if not row or row["status"] in ("scanning", "scan_failed", "rejected"):
            return              # not accepted (yet): the scan gate decides, never the parser
        ps = None
        try:
            data = self.store.get_blob(row["blob"])
            ps = S.parse_source(data, row["filename"], row.get("options") or {})
            values = {"format": ps.format, "family": ps.family, "status": ps.status, "error": ps.error, "notes": ps.notes,
                      "structure": ps.structure, "signature": ps.signature, "updated_at": now()}
        except Exception as e:  # noqa: BLE001
            values = {"status": "error", "error": f"解析失败：{e}", "updated_at": now()}
        # Parsing can outlive removal, project deletion or a newer options request. Publish
        # only the version we read, atomically with its derived references and revision.
        with self.store.tx():
            current = self.store.one("SELECT s.version,s.blob,s.status FROM source_files s JOIN projects p ON p.id=s.project_id "
                                     "WHERE s.id=? AND s.removed=0 AND COALESCE(p.deleted,0)=0", (sid,))
            if not current or current["version"] != row["version"] or current["blob"] != row["blob"]:
                return
            if current.get("status") in ("rejected", "scanning", "scan_failed"):
                return          # rejected (e.g. by a rescan) while this parse ran: the verdict stands
            if ps is not None:
                try:
                    if ps.workbook is not None:
                        values["parsed_blob"] = self.store.put_json_blob(ps.workbook)
                    if ps.working_bytes is not None:
                        values["working_blob"] = self.store.put_blob(ps.working_bytes)
                except Exception:  # noqa: BLE001 - leave a retryable terminal state, not eternal parsing
                    values = {"status": "error", "error": "解析结果保存失败，请检查存储空间后重试", "updated_at": now()}
            self.store.update("source_files", {"id": sid}, values)
            self.store.bump_project(row["project_id"])

    def list_sources(self, pid: str) -> list[dict]:
        # get_project carries the tenant check and 404s, so the listing cannot enumerate another
        # workspace's files — which it did, sha256 included.
        self.get_project(pid)
        rows = self.store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,))
        return [self._source_out(r) for r in rows]

    def _source_out(self, r: dict) -> dict:
        out = {k: r.get(k) for k in ("id", "project_id", "filename", "ord", "size", "format", "family", "status", "error", "notes",
                                     "structure", "version", "representative", "created_at", "updated_at", "options")}
        out["representative"] = bool(r.get("representative"))
        out["ocr_pages"] = sorted(int(k) for k in (r.get("ocr") or {}).keys())
        out["corrections"] = r.get("corrections") or {}
        out["sha256"] = r.get("blob")
        out["scan"] = scan_out(r.get("scan"))
        return out

    def get_source(self, sid: str, raw: bool = False) -> dict:
        """Scoped by the project the file belongs to.

        This looked the row up by id alone, and source_files has no tenant column of its own, so
        every caller — view, download, search, page image, per-word text, OCR, delete — reached
        across workspaces. Not-found rather than forbidden, so an id cannot be probed for
        existence.
        """
        r = authz.source_in_scope(self.store, sid)
        if not r:
            not_found("来源文件")
        return r if raw else self._source_out(r)

    def remove_source(self, sid: str) -> None:
        r = self.get_source(sid, raw=True)
        self.store.update("source_files", {"id": sid}, {"removed": 1, "representative": 0, "updated_at": now()})
        if r["representative"]:
            nxt = self.store.one("SELECT id FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord LIMIT 1", (r["project_id"],))
            if nxt:
                self.store.update("source_files", {"id": nxt["id"]}, {"representative": 1})
        self.store.bump_project(r["project_id"])
        self._project_changed(r["project_id"], "source.remove", {"source_id": sid, "name": r.get("name") or r.get("filename")})

    def reorder_sources(self, pid: str, ids: list[str]) -> list[dict]:
        self.get_project(pid)      # the per-row WHERE pins the project but never checked it is ours
        for i, sid in enumerate(ids):
            self.store.execute("UPDATE source_files SET ord=? WHERE id=? AND project_id=?", (i + 1, sid, pid))
        self.store.bump_project(pid)
        self._project_changed(pid, "source.order", {})
        return self.list_sources(pid)

    def set_representative(self, sid: str) -> dict:
        r = self.get_source(sid, raw=True)
        self.store.execute("UPDATE source_files SET representative=0 WHERE project_id=?", (r["project_id"],))
        self.store.update("source_files", {"id": sid}, {"representative": 1})
        self._project_changed(self.get_source(sid, raw=True)["project_id"], "source.representative", {"source_id": sid})
        return self.get_source(sid)

    def set_source_options(self, sid: str, options: dict) -> dict:
        with self.store.tx():
            r = self.get_source(sid, raw=True)
            opts = dict(r.get("options") or {})
            opts.update(options or {})
            self.store.update("source_files", {"id": sid}, {"options": opts, "status": "parsing", "version": r["version"] + 1,
                                                            "updated_at": now()})
            self.store.bump_project(r["project_id"])
        self._parse_source(sid)
        self._project_changed(r["project_id"], "source.options", {"source_id": sid})
        return self.get_source(sid)

    def source_doc(self, sid: str) -> SourceDoc:
        r = self.get_source(sid, raw=True)
        return self._doc_from_row(r)

    def doc_for(self, r: dict, block: int | None = None) -> SourceDoc:
        """The source document, or one of its form blocks when the project splits files into blocks."""
        doc = self._doc_from_row(r)
        if block is None:
            return doc
        rule = self.get_project(r["project_id"]).get("record_rule") or {}
        if rule.get("mode") != "per_block" or not rule.get("block"):
            raise ServiceError(400, "not_block_mode", "当前样本模式不是「每块一份」，没有表单块")
        from .engine import blocks as B
        from .engine.resolve import ResolveError
        try:
            bl = B.split(doc, rule["block"])
        except ResolveError as e:
            raise ServiceError(400, e.code, str(e)) from e
        if not 0 <= int(block) < len(bl):
            raise ServiceError(404, "block_missing", f"「{r['filename']}」只有 {len(bl)} 个表单块",
                               {"filename": r["filename"], "blocks": len(bl)})
        return bl[int(block)].doc

    def desensitize_sources(self, pid: str, body: dict) -> dict:
        """Desensitized copies of source files (local, consistent pseudonyms across the files)."""
        from .engine import desensitize as D
        self.get_project(pid)
        ids = body.get("file_ids") or [r["id"] for r in self.project_sources(pid, ready_only=False)
                                       if (r.get("status") or "") not in ("rejected", "scanning", "scan_failed")]
        sess = D.Session(seed=body.get("seed"), words=body.get("words") or [], patterns=body.get("patterns"))
        out, skipped = [], []
        for sid in ids:
            r = self.get_source(sid, raw=True)
            if r["project_id"] != pid:
                continue
            self.require_accepted(r)      # rejected (410) or still being checked (409): its bytes are not read
            try:
                data, ext = D.desensitize(self.store.get_blob(r["blob"]), r["filename"], sess, body.get("columns") or {})
            except (D.DesensitizeError, ValueError) as e:
                skipped.append({"file": r["filename"], "reason": str(e)})
                continue
            stem = r["filename"].rsplit(".", 1)[0]
            name = f"脱敏_{stem}.{ext}"
            blob = self.store.put_blob(data)
            # The copy is offered for download whether or not it is added to the project; without a
            # grant nothing vouched for it and every download link answered 404.
            authz.grant_blob(self.store, blob, f"desensitized:{r['id']}")   # names its source (CI-90)
            item = {"source": r["filename"], "filename": name, "blob": blob, "size": len(data)}
            if body.get("add_to_project"):
                item["source_id"] = self.add_source(pid, name, data)["id"]
            out.append(item)
        # the random seed is deliberately not returned: with it, replaced digits could be brute-forced back
        return {"files": out, "skipped": skipped, "counts": sess.counts, "mapping_size": len(sess.map)}

    def source_blocks(self, sid: str) -> dict:
        r = self.get_source(sid, raw=True)
        rule = self.get_project(r["project_id"]).get("record_rule") or {}
        if rule.get("mode") != "per_block" or not rule.get("block"):
            return {"blocks": [], "mode": rule.get("mode")}
        from .engine import blocks as B
        from .engine.resolve import ResolveError
        try:
            bl = B.split(self._doc_from_row(r), rule["block"])
        except ResolveError as e:
            return {"blocks": [], "error": str(e), "code": e.code}
        return {"blocks": [{"index": b.index, "label": b.label, "where": b.where} for b in bl], "mode": "per_block"}

    def _doc_from_row(self, r: dict) -> SourceDoc:
        # Every viewer, search, page image, word layer, OCR run, locator and mapping of a source
        # comes through here. A row that is not accepted may still have a converted working copy
        # and a parsed workbook from before a rescan rejected it (CI-90); neither is read.
        # A standing quarantine verdict on the bytes refuses the row the same way (require_accepted).
        self.require_accepted(r)
        key = (r["id"], r["version"], r.get("parsed_blob"), r.get("working_blob"), json.dumps(r.get("ocr") or {}, sort_keys=True)[:64],
               json.dumps(r.get("corrections") or {}, sort_keys=True))
        with self._cache_lock:
            if key in self._src_cache:
                return self._src_cache[key]
        data = self.store.get_blob(r["working_blob"] or r["blob"])
        wb = self.store.get_json_blob(r["parsed_blob"]) if r.get("parsed_blob") else None
        fmt = r["format"]
        if r.get("working_blob"):
            fmt = {"doc": "docx", "odt": "docx", "rtf": "docx", "ods": "xlsx", "ppt": "pptx", "odp": "pptx"}.get(fmt, fmt)
        doc = SourceDoc(r["id"], r["filename"], fmt, r["family"] or FAMILY.get(fmt, "unknown"), data, wb,
                        r.get("ocr") or {}, r.get("corrections") or {})
        with self._cache_lock:
            # Older versions of the same file (before a correction, an OCR run, new options)
            # are dropped: each held the raw bytes and the parsed workbook until the cache
            # passed 64 entries. Holders of an old version (a running job) keep their reference.
            for k in [k for k in self._src_cache if k[0] == r["id"] and k != key]:
                del self._src_cache[k]
            if len(self._src_cache) > 64:
                self._src_cache.clear()
            self._src_cache[key] = doc
        return doc

    def project_sources(self, pid: str, ready_only: bool = True) -> list[dict]:
        rows = self.store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,))
        if ready_only:
            # A row on bytes whose quarantine verdict stands is left out like a rejected one, not
            # read (which would refuse the whole project's records, previews and jobs) — until
            # this application's rescan marks it (CI-90).
            held = Q.standing(self.store)
            rows = [r for r in rows if r["status"] in ("ready", "needs_ocr")
                    and not (held and held.intersection(b for b in (r.get("blob"), r.get("working_blob")) if b))]
        return rows

    # ================================================================ templates
    def list_templates(self) -> list[dict]:
        rows = self.store.all("SELECT * FROM templates WHERE deleted=0 AND tenant=? ORDER BY updated_at DESC", (current_tenant(),))
        from .org_templates import OrgTemplates
        published = OrgTemplates(self, None).status_for_templates([r["id"] for r in rows])
        out = []
        for r in rows:
            v = self.store.one("SELECT slots FROM template_versions WHERE template_id=? AND version_no=?", (r["id"], r["head"]))
            out.append({**self._template_out(r), "slot_count": len((v or {}).get("slots") or []), "publication": published.get(r["id"])})
        return out

    def _template_out(self, r: dict) -> dict:
        out = {k: r.get(k) for k in ("id", "name", "kind", "format", "original_name", "head", "created_at", "updated_at", "info")}
        out["scan"] = scan_out(r.get("scan"))
        img = (out.get("info") or {}).get("image")
        if img:   # the ICC profile can be large; clients only need to know it is kept
            out["info"] = {**out["info"], "image": {k: v for k, v in img.items() if k not in ("icc", "original_blob")} | {"icc": bool(img.get("icc"))}}
        return out

    @staticmethod
    def fill_options(template_row: dict) -> dict:
        """Template-specific options every fill needs (image size / DPI / ICC for image templates).

        Metadata only: this is stored verbatim in a job snapshot, so the image's pixels must
        not go in here.  Use :meth:`with_image_bytes` at fill time to attach them."""
        img = (template_row.get("info") or {}).get("image")
        return {"image": img} if img else {}

    def with_image_bytes(self, opts: dict) -> dict:
        """Attach an image template's original pixels, resolved from the blob store.

        The raster fill path edits the picture itself, so it needs the bytes; the snapshot
        only carries the blob id, which keeps job snapshots small and stable."""
        img = (opts or {}).get("image") or {}
        blob = img.get("original_blob")
        if not blob or opts.get("image_bytes"):
            return opts
        try:
            return {**opts, "image_bytes": self.store.get_blob(blob)}
        except Exception:  # noqa: BLE001 - a missing blob is reported by the fill itself
            return opts

    def get_template(self, tid: str, *, accepted: bool = True) -> dict:
        """The template of the current workspace. ``accepted`` (the default) also refuses one whose
        contents were judged malicious (410 ``rejected_malware``): almost every caller goes on to
        read, render, copy or fill its bytes. Listing, usage and deletion pass ``accepted=False``."""
        r = self.store.one("SELECT * FROM templates WHERE id=? AND deleted=0", (tid,))
        if not r or (r.get("tenant") or "default") != current_tenant():
            not_found("模板")
        if accepted:
            self.require_template_accepted(r)
        return r

    def template_detail(self, tid: str) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        if r["format"] in PDF_LIKE and v["slots"]:
            v = dict(v)
            v["slots"] = pdf_slots_with_nrect(self.store.get_blob(v["working_blob"]), v["slots"])
        if r["format"] == "docx" and v["slots"]:
            from .engine.templates import assign_parents, may_nest
            if may_nest(v["slots"]):     # nested details, also for a version stored before them
                v = dict(v)
                v["slots"] = assign_parents(self.store.get_blob(v["working_blob"]), v["slots"])
        versions = self.store.all("SELECT version_no, parent_no, note, created_at FROM template_versions WHERE template_id=? ORDER BY version_no", (tid,))
        redo = [x for x in versions if x["parent_no"] == r["head"]]
        parent = v.get("parent_no")
        return {**self._template_out(r), "version": {"no": v["version_no"], "slots": v["slots"], "note": v["note"],
                                                      "editor": self.editor_of(tid, v, r.get("kind")), "created_at": v["created_at"]},
                "can_undo": parent is not None, "can_redo": bool(redo), "versions": versions[-50:],
                "exports": self._exports_for(r["format"])}

    def _exports_for(self, fmt: str) -> list[dict]:
        from .engine.converters import soffice_path
        from .engine.generate import export_formats
        return export_formats(fmt, bool(soffice_path()))

    def _fields_by_name(self, project: dict) -> dict[str, list[str]] | None:
        """Field name -> bindings, for the template version this project generates with.

        None when no template is bound. A pinned project resolves against its pinned version,
        because that is the version its jobs will use.
        """
        tid = project.get("template_id")
        if not tid:
            return None
        t = self.store.one("SELECT head FROM templates WHERE id=?", (tid,))
        if not t:
            return None
        no = project.get("template_version") or t["head"]
        v = self.store.one("SELECT slots FROM template_versions WHERE template_id=? AND version_no=?", (tid, no))
        out: dict[str, list[str]] = {}
        for s in (v or {}).get("slots") or []:
            b = s.get("binding") or s["id"]
            names = out.setdefault((s.get("name") or "").strip(), [])
            if b not in names:
                names.append(b)
        return out

    def get_version(self, tid: str, no: int) -> dict:
        v = self.store.one("SELECT * FROM template_versions WHERE template_id=? AND version_no=?", (tid, no))
        if not v:
            not_found("模板版本")
        return v

    def editor_of(self, tid: str, version: dict, kind: str | None) -> dict | None:
        """A version's editor model. A field changed on the project page used to save a built-in
        template's new version without one (CI-112): the spreadsheet editor then refused the template
        and the document editor opened — or reset a live room to — an empty document. Such a version
        takes the nearest earlier version's model, with the fields' current names and flags."""
        ed = version.get("editor")
        if isinstance(ed, str):
            ed = json.loads(ed) if ed else None
        if ed or kind not in ("builtin_doc", "builtin_sheet"):
            return ed
        cur, seen = version, set()
        while cur.get("parent_no") and cur["parent_no"] not in seen:
            seen.add(cur["parent_no"])
            try:
                cur = self.get_version(tid, cur["parent_no"])
            except ServiceError:
                return None
            prev = cur.get("editor")
            if isinstance(prev, str):
                prev = json.loads(prev) if prev else None
            if prev:
                from .builtin_service import sync_editor_fields
                return sync_editor_fields(kind, prev, version.get("slots") or [])
        return None

    def upload_template(self, filename: str, data: bytes, name: str | None = None) -> dict:
        from .engine.detect import detect
        filename = safe_display_name(filename)
        if Q.verdict(self.store, hashlib.sha256(data).hexdigest()):
            raise earlier_malicious(filename)
        scan_record = self.scan_template_bytes(filename, data)
        try:
            info = detect(data, filename)
        except ValueError as e:
            raise ServiceError(400, "bad_template", str(e)) from e
        if info["encrypted"]:
            raise ServiceError(400, "encrypted", "模板文件已加密，请移除密码后再上传")
        fmt = info["format"]
        notes = list(info["notes"])
        working = data
        if info.get("convert_via") and info["convert_via"] in ("docx", "xlsx", "pptx"):
            from .engine.converters import ConversionError, convert
            try:
                working = convert(data, fmt, info["convert_via"])
                notes.append(f"{fmt.upper()} 模板已经格式转换为 {info['convert_via'].upper()}；输出为 {info['convert_via'].upper()}。转换可能改变部分排版")
                fmt = info["convert_via"]
            except ConversionError as e:
                raise ServiceError(400, "convert_failed", str(e)) from e
        elif fmt == "xls":
            from .engine.converters import ConversionError, convert
            try:
                working = convert(data, "xls", "xlsx")
                notes.append("XLS 模板已经格式转换为 XLSX；输出为 XLSX")
                fmt = "xlsx"
            except ConversionError as e:
                raise ServiceError(400, "convert_failed", str(e)) from e
        if info["macros"] or fmt in ("docm", "xlsm", "pptm"):
            # Outputs are named .docx/.xlsx/.pptx. Keeping the macro-enabled main-part content
            # type (and the VBA project) inside a file with that extension is exactly what Office
            # refuses to open ("file format or extension is not valid"), while LibreOffice-based
            # checks pass — so the macros are removed from the template, not "kept but disabled".
            try:
                working = strip_macros(working)
            except ValueError as e:
                raise ServiceError(400, "bad_template", f"模板无法解析：{e}") from e
        fmt = {"docm": "docx", "xlsm": "xlsx", "pptm": "pptx"}.get(fmt, fmt)
        image_meta = None
        from .engine import pdf_engine as _pe
        if fmt == "pdf" and not _pe.pdf_templates_available():
            raise ServiceError(400, "engine_unavailable", _pe.TEMPLATES_UNAVAILABLE)
        if fmt in ("png", "jpeg", "tiff", "bmp", "webp") and not _pe.image_templates_available():
            raise ServiceError(400, "engine_unavailable", _pe.IMAGE_TEMPLATES_UNAVAILABLE)
        if fmt in ("png", "jpeg", "tiff", "bmp", "webp"):
            from .engine.image_template import ImageTemplateError, make_wrapper
            try:
                working, image_meta, img_notes = make_wrapper(data)
            except ImageTemplateError as e:
                raise ServiceError(400, e.code, str(e)) from e
            notes.extend(img_notes)
            image_meta["original_blob"] = self.store.put_blob(data)    # the upload itself, unchanged
            fmt = image_meta["native"]
        if fmt not in ("docx", "xlsx", "pdf", "pptx", "png", "jpeg"):
            raise ServiceError(400, "unsupported_template", f"{fmt.upper()} 不能作为模板。可作为模板的格式：DOCX、XLSX、PDF、PPTX、PNG、JPEG（以及可转换的 DOC/XLS/PPT/ODT/ODS/ODP/RTF、BMP/TIFF/WEBP）",
                               {"format": fmt.upper()})
        try:
            check = template_inventory(fmt, working)
        except Exception as e:  # noqa: BLE001
            raise ServiceError(400, "bad_template", f"模板无法解析：{e}") from e
        notes.extend(check.get("notes", []))
        if info["macros"]:
            notes.append("模板包含宏：宏不会被执行，并已从模板中移除。输出文件为不含宏的标准格式（.docx/.xlsx/.pptx）")
        # What is stored is the converted/stripped working copy (and an image template's upload):
        # neither may be bytes a quarantine verdict took out of the store.
        if any(Q.verdict(self.store, hashlib.sha256(b).hexdigest()) for b in (working,)):
            raise earlier_malicious(filename)
        tid = new_id("t")
        t = now()
        orig = self.store.put_blob(working)
        self.store.insert("templates", {"id": tid, "tenant": current_tenant(), "name": (name or filename.rsplit(".", 1)[0])[:100], "kind": "uploaded",
                                        "format": fmt, "original_blob": orig, "original_name": filename, "head": 1,
                                        "created_at": t, "updated_at": t, "info": {"notes": notes, "inventory": check.get("inventory", {}),
                                                                                    "pdf": check.get("pdf"), "image": image_meta}, "scan": scan_record})
        self.store.insert("template_versions", {"template_id": tid, "version_no": 1, "parent_no": None, "working_blob": orig,
                                                "slots": [], "note": "上传原始模板", "created_at": t})
        return self.template_detail(tid)

    def new_version(self, tid: str, working: bytes, slots: list[dict], note: str, editor: dict | None = None,
                    base_head: int | None = None) -> dict:
        """Add a version and make it head — only if head is still ``base_head`` (the version the
        edit was computed from).

        Read-head, insert and move-head used to be three separate statements: two concurrent
        edits (two tabs, or the UI and POST /templates/{tid}/fields) either collided on the
        primary key with a 500, or the later one was built from the old slot list and silently
        dropped the other's field."""
        tpl = self.get_template(tid)
        if tpl.get("format") == "docx":
            # which repeat rows sit inside a repeat content block, from the file itself (nested details)
            from .engine.templates import assign_parents
            slots = assign_parents(working, slots)
        blob = self.store.put_blob(working)
        t = now()
        with self.store.tx() as c:
            head = c.execute("SELECT head FROM templates WHERE id=?", (tid,)).fetchone()["head"]
            if base_head is not None and head != base_head:
                raise ServiceError(409, "stale_template", "模板已在其他窗口或被其他人修改，请刷新后再操作",
                                   {"head": head, "base_version": base_head})
            no = (c.execute("SELECT MAX(version_no) AS m FROM template_versions WHERE template_id=?", (tid,)).fetchone()["m"] or 0) + 1
            c.execute("INSERT INTO template_versions (template_id, version_no, parent_no, working_blob, slots, note, editor, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (tid, no, head, blob, json.dumps(slots, ensure_ascii=False), note,
                                                   json.dumps(editor, ensure_ascii=False) if editor is not None else None, t))
            c.execute("UPDATE templates SET head=?, updated_at=? WHERE id=?", (no, t, tid))
        self._bump_projects_using(tid)
        self._template_changed(tid, no, editor, "save", previous=head)
        return self.template_detail(tid)

    def _template_changed(self, tid: str, head: int | None, editor: dict | None, reason: str, *,
                          previous: int | None = None, source: int | None = None) -> None:
        """`previous`: the head this change replaced; `source`: the version a restore copied — so
        the change history can say what became what, not only the new number."""
        hook = self.on_template_change
        if hook is None:
            return
        try:
            hook(tid, head, editor, reason, previous=previous, source=source)
        except Exception:                                    # noqa: BLE001 — the version is saved; the room catches up on reopen
            logging.getLogger("synwevo.docs").exception("template change hook failed for %s (%s)", tid, reason)

    def close(self) -> None:
        """Release the parse pool at the end of the process. A parse that had not started stays
        'parsing' in the store and is picked up by the next process (`resume_parsing`)."""
        self.parse_pool.shutdown(wait=False, cancel_futures=True)

    stop = close                                        # the worker registry speaks stop()

    def _project_changed(self, pid: str, kind: str, detail: dict | None = None) -> None:
        hook = self.on_project_change
        if hook is None:
            return
        try:
            hook(pid, kind, detail or {})
        except Exception:                                    # noqa: BLE001 — the change is saved; the feed catches up on reload
            logging.getLogger("synwevo.docs").exception("project change hook failed for %s (%s)", pid, kind)

    def _bump_projects_using(self, tid: str) -> None:
        for p in self.store.all("SELECT id FROM projects WHERE template_id=? AND deleted=0", (tid,)):
            self.store.bump_project(p["id"])

    def _guard_live_edits(self, tid: str, discard_live: bool, action: str, *, saver: str | None = None,
                          field_change: bool = False) -> None:
        """A version operation replaces the live document for everyone in the editing room. When
        that document carries edits nobody has saved as a version yet, refuse with 409
        ``live_edits`` naming who is there, unless the caller said ``discard_live`` explicitly.
        ``saver``: a person saving *from inside the room* saves the live document itself — that is
        not a replacement (CI-113: saves from outside it used to drop a persisted live draft)."""
        hook = self.live_template_state
        if hook is None or discard_live:
            return
        try:
            live = hook(tid)
        except Exception:                                    # noqa: BLE001 — the room is a cache of the store; not knowing must not wedge versions
            logging.getLogger("synwevo.docs").exception("live state of %s unavailable before %s", tid, action)
            return
        if live.get("dirty"):
            if saver is not None and saver in (live.get("online_ids") or []):
                return
            detail = {"action": action, "online": live.get("online") or [], "last_by": live.get("last_by")}
            if field_change:          # the field panel has no "discard" path: say what to do instead
                raise ServiceError(409, "live_edits", "模板编辑器里有尚未保存为版本的实时修改：请先在编辑器里保存，再修改字段", detail)
            raise ServiceError(409, "live_edits", "编辑器里有尚未保存为版本的实时修改；这一步会丢弃它们，请确认后再试", detail)

    def _move_template_head(self, tid: str, base_head: int | None, redo: bool, *, discard_live: bool = False) -> None:
        """Undo (to the parent) or redo (to the newest child), decided and applied in one
        transaction. ``base_head`` is the version the client's step was decided on: when the
        template moved on since (another tab, another person), the step would undo *their*
        edit, so it is refused with 409 ``stale_template``. Live edits not yet saved as a version
        are refused with 409 ``live_edits`` unless ``discard_live`` (see ``_guard_live_edits``)."""
        self.get_template(tid)
        self._guard_live_edits(tid, discard_live, "redo" if redo else "undo")
        with self.store.tx() as c:
            head = c.execute("SELECT head FROM templates WHERE id=?", (tid,)).fetchone()["head"]
            if base_head is not None and base_head != head:
                raise ServiceError(409, "stale_template", "模板已在其他窗口或被其他人修改，请刷新后再撤销或重做",
                                   {"head": head, "base_version": base_head})
            if redo:
                kid = c.execute("SELECT version_no FROM template_versions WHERE template_id=? AND parent_no=? "
                                "ORDER BY version_no DESC LIMIT 1", (tid, head)).fetchone()
                if not kid:
                    raise ServiceError(400, "nothing_to_redo", "没有可重做的模板修改")
                target = kid["version_no"]
            else:
                v = c.execute("SELECT parent_no FROM template_versions WHERE template_id=? AND version_no=?", (tid, head)).fetchone()
                if not v or v["parent_no"] is None:
                    raise ServiceError(400, "nothing_to_undo", "没有可撤销的模板修改")
                target = v["parent_no"]
            c.execute("UPDATE templates SET head=?, updated_at=? WHERE id=?", (target, now(), tid))
        self._bump_projects_using(tid)
        self._template_changed(tid, target, self.editor_of(tid, self.get_version(tid, target), self.get_template(tid).get("kind")),
                               "redo" if redo else "undo", previous=head)

    def template_usage(self, tid: str) -> dict:
        """Who follows this template: the projects bound to it, split into those that follow the
        head and those pinned to a version — what a restore will and will not change."""
        self.get_template(tid, accepted=False)          # metadata only: who to re-point before deleting
        rows = self.store.all("SELECT id, name, template_version FROM projects WHERE template_id=? AND deleted=0 AND tenant=? ORDER BY updated_at DESC",
                              (tid, current_tenant()))
        items = [{"id": r["id"], "name": r["name"], "pinned_version": r.get("template_version")} for r in rows]
        return {"projects": items, "following": sum(1 for r in items if not r["pinned_version"]), "pinned": sum(1 for r in items if r["pinned_version"])}

    def restore_version(self, tid: str, version_no: int, base_head: int | None = None, *, discard_live: bool = False) -> dict:
        """Make an earlier version current *as a new version*: its file and fields are copied
        into version head+1 (parent = the current head), so the history keeps every step and
        the undo/redo chain stays linear — the way Google Docs and OneDrive restore. Refused
        with 409 stale_template when the head moved since the client decided, and with 409
        live_edits when the editing room holds unsaved edits and ``discard_live`` was not given."""
        tpl = self.get_template(tid)
        target = dict(self.get_version(tid, version_no))
        target["editor"] = self.editor_of(tid, target, tpl.get("kind"))   # a version saved without one gets it back
        self._guard_live_edits(tid, discard_live, "restore")
        t = now()
        with self.store.tx() as c:
            head = c.execute("SELECT head FROM templates WHERE id=?", (tid,)).fetchone()["head"]
            if base_head is not None and head != base_head:
                raise ServiceError(409, "stale_template", "模板已在其他窗口或被其他人修改，请刷新后再恢复", {"head": head, "base_version": base_head})
            if version_no == head:
                raise ServiceError(400, "already_current", "这已经是当前版本")
            no = (c.execute("SELECT MAX(version_no) AS m FROM template_versions WHERE template_id=?", (tid,)).fetchone()["m"] or 0) + 1
            c.execute("INSERT INTO template_versions (template_id, version_no, parent_no, working_blob, slots, note, editor, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (tid, no, head, target["working_blob"], json.dumps(target["slots"] or [], ensure_ascii=False), f"恢复自 v{version_no}",
                       json.dumps(target.get("editor"), ensure_ascii=False) if target.get("editor") is not None else None, t))
            c.execute("UPDATE templates SET head=?, updated_at=? WHERE id=?", (no, t, tid))
        self._bump_projects_using(tid)
        self._template_changed(tid, no, target.get("editor"), "restore", previous=head, source=version_no)
        return self.template_detail(tid)

    def template_undo(self, tid: str, base_head: int | None = None, *, discard_live: bool = False) -> dict:
        self._move_template_head(tid, base_head, redo=False, discard_live=discard_live)
        return self.template_detail(tid)

    def template_redo(self, tid: str, base_head: int | None = None, *, discard_live: bool = False) -> dict:
        self._move_template_head(tid, base_head, redo=True, discard_live=discard_live)
        return self.template_detail(tid)

    def duplicate_template(self, tid: str, name: str | None = None) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        nid = new_id("t")
        t = now()
        self.store.insert("templates", {"id": nid, "tenant": current_tenant(), "name": (name or r["name"] + " 副本")[:100], "kind": r["kind"], "format": r["format"],
                                        "original_blob": r["original_blob"], "original_name": r["original_name"], "head": 1,
                                        "created_at": t, "updated_at": t, "info": r.get("info"), "scan": r.get("scan")})
        self.store.insert("template_versions", {"template_id": nid, "version_no": 1, "parent_no": None, "working_blob": v["working_blob"],
                                                "slots": v["slots"], "editor": self.editor_of(tid, v, r.get("kind")), "note": f"复制自「{r['name']}」v{v['version_no']}",
                                                "created_at": t})
        return self.template_detail(nid)

    def rename_template(self, tid: str, name: str) -> dict:
        self.get_template(tid)
        self.store.update("templates", {"id": tid}, {"name": name.strip()[:100], "updated_at": now()})
        return self.template_detail(tid)

    def delete_template(self, tid: str) -> None:
        """Delete a template no project uses.

        Deleting one that projects still pointed at left those projects unopenable (their detail
        request 404ed on the template) and every job on them failed. Refused instead, with the
        projects named, so the person can re-point or delete them first. Archived projects count:
        they are still usable. A template a rescan rejected can be deleted like any other."""
        t = self.get_template(tid, accepted=False)
        users = self.store.all("SELECT id, name FROM projects WHERE template_id=? AND deleted=0 AND COALESCE(tenant,'default')=? "
                               "ORDER BY updated_at DESC", (tid, current_tenant()))
        if users:
            raise ServiceError(409, "template_in_use",
                               f"模板「{t['name']}」正被 {len(users)} 个项目使用（如「{users[0]['name']}」）。"
                               "请先在这些项目中更换模板或删除这些项目，再删除模板",
                               {"projects": [{"id": u["id"], "name": u["name"]} for u in users[:20]], "total": len(users),
                                "template": t["name"]})
        self.store.update("templates", {"id": tid}, {"deleted": 1, "updated_at": now()})
        self._template_changed(tid, None, None, "delete")

    def add_slot(self, tid: str, req: dict) -> dict:
        r = self.get_template(tid)
        self._refuse_builtin_field_change(r)
        v = self.get_version(tid, r["head"])
        slots = list(v["slots"] or [])
        work = self.store.get_blob(v["working_blob"])
        name = (req.get("name") or "").strip() or "字段"
        fmt = r["format"]
        if fmt == "docx" and req.get("kind") == "block_column":
            try:
                work2, blk = T.docx_add_block_column(work, req, slots)
            except T.TemplateError as e:
                raise ServiceError(400, e.code, str(e)) from e
            slots = [blk if s["id"] == blk["id"] else s for s in slots]
            detail = self.new_version(tid, work2, slots, f"为重复内容块「{blk['name']}」添加列「{blk['columns'][-1]['name']}」",
                                      base_head=r["head"])
            detail["created_slot"] = blk
            return detail
        try:
            if fmt == "docx":
                work2, slot = T.docx_add_slot(work, req, slots)
            elif fmt == "xlsx":
                work2, slot = T.xlsx_add_slot(work, req, slots)
            elif fmt in PDF_LIKE:
                from .engine import pdf_engine as _pe
                img_meta = (r.get("info") or {}).get("image") or {}
                if fmt != "pdf":
                    req = {**req, "image_template": True}
                if fmt != "pdf" and not _pe.pdf_templates_available():
                    # raster authoring: the wrapper PDF is left untouched (it is only the
                    # preview surface); the field is analysed straight from the pixels
                    slot = T.image_add_slot(self.store.get_blob(img_meta["original_blob"]), req, slots)
                    work2 = work
                else:
                    slot = T.pdf_add_slot(self.store.get_blob(r["original_blob"]), req, slots)
                    work2, warns = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), slots + [slot])
                    if warns:
                        slot["warning"] = "；".join(warns)
            elif fmt == "pptx":
                from .engine.pptx_engine import pptx_add_slot
                work2, slot = pptx_add_slot(work, req, slots)
            else:
                raise ServiceError(400, "unsupported", "该模板格式不支持创建字段")
        except T.TemplateError as e:
            raise ServiceError(400, e.code, str(e)) from e
        for k in ("required", "data_type", "format", "default"):
            if k in req:
                slot[k] = req[k]
        # same display name → offer shared binding (explicit flag from client)
        if req.get("share_binding_with"):
            other = next((s for s in slots if s["id"] == req["share_binding_with"]), None)
            if other:
                slot["binding"] = other.get("binding") or other["id"]
        slots.append(slot)
        detail = self.new_version(tid, work2, slots, f"添加字段「{slot['name']}」", base_head=r["head"])
        detail["created_slot"] = slot
        return detail

    def pdf_repeat_detect(self, tid: str, page: int, nrect: list) -> dict:
        r = self.get_template(tid)
        if r["format"] not in PDF_LIKE:
            raise ServiceError(400, "not_pdf", "只有 PDF / 图片模板需要识别明细区域的行列")
        if len(nrect) != 4:
            raise ServiceError(400, "bad_rect", "请先在页面上框选明细区域")
        try:
            return T.pdf_detect_repeat(self.store.get_blob(r["original_blob"]), page, [float(x) for x in nrect])
        except T.TemplateError as e:
            raise ServiceError(400, e.code, str(e)) from e

    @staticmethod
    def _refuse_builtin_field_change(r: dict) -> None:
        """A built-in template's fields live in its editor model; adding or removing one on the
        project page changed only the file, and the next editor save undid it (CI-112)."""
        if r.get("kind") in ("builtin_doc", "builtin_sheet"):
            raise ServiceError(409, "builtin_fields", "这个模板由内置编辑器维护：请在编辑器里添加或删除字段（在这里改动，下次在编辑器保存时会丢失）")

    def update_slot(self, tid: str, slot_id: str, values: dict) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        slots = [dict(s) for s in v["slots"]]
        sl = next((s for s in slots if s["id"] == slot_id), None)
        if not sl:
            not_found("字段")
        editable = {"name", "required", "data_type", "format", "default", "font_size", "min_font_size", "align", "valign",
                    "empty_policy", "placeholder", "repeat_header", "keep_header_when_empty", "binding", "mode", "columns",
                    "overflow", "min_row_scale", "color", "separator", "remove_column", "shrink", "shrink_min"}
        for k, val in values.items():
            if k not in editable:
                continue
            if k == "columns":
                names = {c["key"]: c.get("name") for c in val or []}
                for c in sl.get("columns", []):
                    if c["key"] in names and names[c["key"]]:
                        c["name"] = str(names[c["key"]])[:60]
                aligns = {c["key"]: c.get("align") for c in val or [] if c.get("align") in ("left", "center", "right")}
                if sl.get("layout") and aligns:
                    sl["layout"] = {**sl["layout"], "cols": [{**c, "align": aligns.get(c["key"], c.get("align", "left"))} for c in sl["layout"]["cols"]]}
                continue
            if k == "shrink":
                val = bool(val)
            if k == "shrink_min":
                val = min(72.0, max(4.0, float(val)))
            if k == "separator" and val not in ("none", "blank", "page_break"):
                raise ServiceError(400, "bad_value", "分隔方式无效")
            if k == "remove_column":
                if not sl.get("block") or not any(c["key"] == val for c in sl.get("columns", [])):
                    raise ServiceError(400, "bad_value", "该列不存在")
                sl["columns"] = [c for c in sl["columns"] if c["key"] != val]
                sl["_remove_column"] = val
                continue
            if k == "overflow" and r["format"] == "pptx" and val not in ("warn", "error", "continue"):
                raise ServiceError(400, "bad_value", "超出处理方式无效：演示文稿的明细只能是提示、视为错误或复制本张幻灯片续写")
            if k == "overflow" and r["format"] != "pptx" and val not in ("error", "shrink", "continue"):
                raise ServiceError(400, "bad_value", "超出处理方式无效")
            if k == "empty_policy":
                # the valid set depends on what kind of repeat this slot is
                kindset = "block" if sl.get("block") else ("area" if sl.get("layout") else "table")
                allowed = T.EMPTY_POLICIES[kindset]
                if val not in allowed:
                    raise ServiceError(400, "bad_value", f"empty_policy 只能是 {' / '.join(allowed)}；收到「{val}」",
                                       {"field": "empty_policy", "allowed": list(allowed)})
            if k == "data_type" and val not in ("auto", "text", "number", "date"):
                raise ServiceError(400, "bad_value", "data_type 只能是 auto / text / number / date", {"field": "data_type"})
            if k in ("align", "valign") and val not in (("left", "center", "right") if k == "align" else ("top", "middle", "bottom")):
                raise ServiceError(400, "bad_value", f"{k} 取值无效", {"field": k})
            if k == "overflow" and val == "continue" and r["format"] in ("png", "jpeg"):
                raise ServiceError(400, "no_continue", "图片模板只有一页，不能复制页面续写；请选择「视为错误」或「压缩行高」")
            if k == "color":
                val = T._valid_hex(val)
                if not val:
                    raise ServiceError(400, "bad_value", "颜色格式应为 #RRGGBB")
            if k == "min_row_scale":
                val = min(1.0, max(0.3, float(val)))
            if k == "mode" and r["format"] not in ("xlsx",):
                continue  # text replace/insert mode is fixed at creation for documents
            sl[k] = val
        from .engine import transforms
        errs = transforms.validate(sl.get("format"))
        if errs:
            raise ServiceError(400, "bad_format", "；".join(errs))
        work = self.store.get_blob(v["working_blob"])
        if r["format"] in PDF_LIKE:
            work, _ = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), slots)
        note = f"修改字段「{sl['name']}」"
        if sl.get("_remove_column"):
            key = sl.pop("_remove_column")
            work = T.docx_remove_block_column(work, sl, key)
            note = f"从重复内容块「{sl['name']}」移除一列"
        editor = None
        if r.get("kind") in ("builtin_doc", "builtin_sheet"):
            # a new version replaces the live document: not while it holds edits nobody saved (CI-113)
            self._guard_live_edits(tid, False, "field", field_change=True)
            # the editor model travels with the version, with this field's name and flag in it:
            # without it the editors could no longer open the template (CI-112)
            from .builtin_service import sync_editor_fields
            base = self.editor_of(tid, v, r["kind"])
            editor = sync_editor_fields(r["kind"], base, slots) if base else None
        return self.new_version(tid, work, slots, note, editor=editor, base_head=r["head"])

    def remove_slot(self, tid: str, slot_id: str, project_id: str | None = None, cascade: bool = True) -> dict:
        r = self.get_template(tid)
        self._refuse_builtin_field_change(r)
        v = self.get_version(tid, r["head"])
        slots = list(v["slots"])
        sl = next((s for s in slots if s["id"] == slot_id), None)
        if not sl:
            not_found("字段")
        work = self.store.get_blob(v["working_blob"])
        fmt = r["format"]
        remaining = [s for s in slots if s["id"] != slot_id]
        if fmt == "docx":
            work = T.docx_remove_slot(work, sl)
        elif fmt == "xlsx":
            work = T.xlsx_remove_slot(work, sl)
        elif fmt in PDF_LIKE:
            work, _ = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), remaining)
        elif fmt == "pptx":
            from .engine.pptx_engine import pptx_remove_slot
            work = pptx_remove_slot(work, sl)
        removed_maps, keep = [], None
        binding = sl.get("binding") or sl["id"]
        still_bound = any((s.get("binding") or s["id"]) == binding for s in remaining)
        if cascade and project_id and not still_bound:
            ms = self.get_mappings(project_id)["mappings"]
            keep = [m for m in ms if ((m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")) != binding]
            removed_maps = [m for m in ms if m not in keep]
        # The version first: if it is refused as stale, the mappings must still be as they were.
        detail = self.new_version(tid, work, remaining, f"删除字段「{sl['name']}」" + (f"（同时移除 {len(removed_maps)} 个映射）" if removed_maps else ""),
                                  base_head=r["head"])
        if removed_maps:
            self.save_mappings(project_id, keep, f"删除字段「{sl['name']}」并移除相关映射")
        detail["removed_mappings"] = [m["id"] for m in removed_maps]
        return detail

    # ================================================================ mappings
    def get_mappings(self, pid: str) -> dict:
        p = self.get_project(pid)
        row = self.store.one("SELECT * FROM mapping_revs WHERE project_id=? AND rev=?", (pid, p["mapping_rev"]))
        kids = self.store.all("SELECT rev FROM mapping_revs WHERE project_id=? AND parent_rev=?", (pid, p["mapping_rev"]))
        return {"rev": p["mapping_rev"], "mappings": (row or {}).get("data") or [], "can_undo": bool(row and row["parent_rev"] is not None),
                "can_redo": bool(kids), "note": (row or {}).get("note")}

    @staticmethod
    def _mapping_key(m: dict) -> str:
        """A mapping's content, for equality: its identity and what it maps, not when it was saved."""
        return json.dumps({k: v for k, v in m.items() if k not in ("updated_at",)}, sort_keys=True, ensure_ascii=False)

    @classmethod
    def merge_mappings(cls, base: list[dict], current: list[dict], incoming: list[dict], force_ids: set[str] | None = None) -> tuple[list[dict], list[dict]]:
        """Three-way merge of the mapping list at the granularity of one mapping (its stable id).

        ``base`` is the revision the client loaded, ``current`` what the project holds now
        (someone else saved since), ``incoming`` the client's list. A mapping only one side
        touched takes that side; one both sides changed the same way is fine; one both sides
        changed differently — or one side deleted and the other edited — is a conflict, unless its
        id is in ``force_ids`` (the person chose their version after seeing the other's).
        Returns (merged list, conflicts). Order: the current order, then the client's additions."""
        force_ids = force_ids or set()
        kb = {m["id"]: m for m in base if m.get("id")}
        kc = {m["id"]: m for m in current if m.get("id")}
        ki = {m["id"]: m for m in incoming if m.get("id")}
        key = cls._mapping_key
        merged: dict[str, dict] = {}
        conflicts: list[dict] = []
        for mid in list(kb) + [x for x in kc if x not in kb] + [x for x in ki if x not in kb and x not in kc]:
            b, c, i = kb.get(mid), kc.get(mid), ki.get(mid)
            if b is None:                                    # created since the base, by one side or the other
                chosen = i if i is not None and (c is None or mid in force_ids) else c
                if i is not None and c is not None and key(i) != key(c) and mid not in force_ids:
                    conflicts.append({"id": mid, "no": (c or i).get("no"), "reason": "both_added"})
                    continue
                merged[mid] = chosen
                continue
            c_changed = c is None or key(c) != key(b)
            i_changed = i is None or key(i) != key(b)
            if not c_changed:                                # only the client touched it (or nobody)
                if i is not None:
                    merged[mid] = i
                continue                                     # client deleted, nobody else touched: gone
            if not i_changed:                                # only someone else touched it
                if c is not None:
                    merged[mid] = c
                continue                                     # they deleted it; the client did not touch it
            if c is not None and i is not None and key(c) == key(i):
                merged[mid] = c                              # the same change from both sides
                continue
            if mid in force_ids:
                if i is not None:
                    merged[mid] = i
                continue
            conflicts.append({"id": mid, "no": (c or i or b).get("no"),
                              "reason": "deleted_by_other" if c is None else "deleted_by_you" if i is None else "both_changed"})
        order = [m["id"] for m in current if m.get("id")] + [m["id"] for m in incoming if m.get("id") and m["id"] not in kc]
        out = [merged[mid] for mid in order if mid in merged]
        out += [m for mid, m in merged.items() if mid not in order]
        return out, conflicts

    @staticmethod
    def _mapping_label(m: dict) -> dict:
        target = (m.get("target") or {}).get("binding") or (m.get("target") or {}).get("field") or ""
        srcs = []
        for src in m.get("sources") or []:
            if isinstance(src, dict):
                srcs.append(src.get("header") or src.get("cell") or src.get("locator", {}).get("text") if isinstance(src.get("locator"), dict) else src.get("header") or src.get("cell") or src.get("kind") or "")
        return {"id": m.get("id"), "no": m.get("no"), "target": str(target)[:80], "sources": [str(x)[:60] for x in srcs if x][:4],
                "enabled": bool(m.get("enabled", True)), "type": m.get("type")}

    @classmethod
    def mapping_diff(cls, before: list[dict], after: list[dict]) -> dict:
        kb = {m["id"]: m for m in before if m.get("id")}
        ka = {m["id"]: m for m in after if m.get("id")}
        added = [cls._mapping_label(m) for mid, m in ka.items() if mid not in kb]
        removed = [cls._mapping_label(m) for mid, m in kb.items() if mid not in ka]
        changed = [{"before": cls._mapping_label(kb[mid]), "after": cls._mapping_label(m)} for mid, m in ka.items()
                   if mid in kb and cls._mapping_key(kb[mid]) != cls._mapping_key(m)]
        return {"added": added, "removed": removed, "changed": changed}

    def save_mappings(self, pid: str, mappings: list[dict], note: str = "修改映射", base_rev: int | None = None,
                      force_ids: list[str] | None = None) -> dict:
        p = self.get_project(pid)
        # Merging by mapping needs the client to know the saved mappings' ids: entries without an
        # id are then additions. A list that names no id at all (a definition posted by a program,
        # an empty list) can only replace the whole revision, so a stale base refuses it as before.
        mergeable = bool(mappings) and any(isinstance(m, dict) and m.get("id") for m in mappings)
        clean = []
        used_no = set()
        by_name = self._fields_by_name(p)
        for i, m in enumerate(mappings):
            if not isinstance(m, dict):
                raise ServiceError(400, "bad_mapping", f"第 {i + 1} 个映射必须是对象", {"field": f"mappings[{i}]"})
            m = dict(m)
            m.setdefault("id", new_id("m"))
            m.setdefault("enabled", True)
            m.setdefault("type", "single")
            if not isinstance(m.get("sources"), list):
                raise ServiceError(400, "bad_mapping", "映射缺少来源")
            # A mapping may name its target field by name (`field`) instead of by binding: that is
            # how a definition written by hand, or by a program that never saw the UI, refers to
            # a field. It is resolved here, against the template version this project actually
            # uses, and an unknown or ambiguous name is refused — saving it unresolved would make
            # a mapping that fills nothing and a document that keeps the template's placeholder.
            if m.get("field") is not None and not (m.get("target") or {}).get("binding"):
                name = m.pop("field")
                hits = by_name.get(str(name).strip(), []) if by_name is not None else []
                if by_name is None:
                    raise ServiceError(400, "bad_mapping", "项目还没有绑定模板，不能按字段名建立映射",
                                       {"field": f"mappings[{i}].field"})
                if not hits:
                    raise ServiceError(400, "bad_mapping", f"第 {i + 1} 个映射的目标字段「{name}」在模板中不存在",
                                       {"field": f"mappings[{i}].field"})
                if len({h for h in hits}) > 1:
                    raise ServiceError(400, "bad_mapping", f"模板中有多个名为「{name}」的字段，请改用 target.binding 指明",
                                       {"field": f"mappings[{i}].field"})
                m["target"] = {"binding": hits[0]}
            if not m.get("no") or m["no"] in used_no:
                m["no"] = max(used_no | {0}) + 1
            used_no.add(m["no"])
            m.setdefault("color", PALETTE[(m["no"] - 1) % len(PALETTE)])
            m["updated_at"] = now()
            clean.append(m)
        # Check, read MAX(rev), insert and move the project's pointer in one transaction: the
        # base_rev check used to run before three separate statements, so two saves in the same
        # moment could both pass it (or collide on the primary key).
        merged_with = None
        unchanged = False
        with self.store.tx() as c:
            cur = c.execute("SELECT mapping_rev FROM projects WHERE id=?", (pid,)).fetchone()["mapping_rev"]
            cur_row = c.execute("SELECT data FROM mapping_revs WHERE project_id=? AND rev=?", (pid, cur)).fetchone()
            current = json.loads(cur_row["data"]) if cur_row and cur_row["data"] else []
            if isinstance(current, str):
                current = json.loads(current)
            if base_rev is not None and base_rev != cur:
                # Someone else saved since this list was loaded. Merge mapping by mapping instead of
                # refusing outright: what only one side touched goes through, what both touched
                # is refused with the ids, so the person sees exactly which mappings collided.
                base_row = c.execute("SELECT data FROM mapping_revs WHERE project_id=? AND rev=?", (pid, base_rev)).fetchone()
                if not base_row or not mergeable:
                    raise ServiceError(409, "stale", "映射已在其他窗口被修改，请刷新后重试", {"rev": cur, "base_rev": base_rev})
                base = json.loads(base_row["data"]) if base_row["data"] else []
                if isinstance(base, str):
                    base = json.loads(base)
                clean, conflicts = self.merge_mappings(base, current, clean, set(force_ids or []))
                if conflicts:
                    raise ServiceError(409, "stale", "有映射同时被其他人修改，请查看冲突后选择保留哪一方",
                                       {"rev": cur, "base_rev": base_rev, "conflicts": conflicts})
                merged_with = cur
                note = f"{note}（已与第 {cur} 版合并）"
                if [self._mapping_key(x) for x in clean] == [self._mapping_key(x) for x in current]:
                    unchanged = True                         # nothing of theirs differs from what is saved: no revision to add
            if unchanged:
                mx = cur - 1
            else:
                mx = c.execute("SELECT MAX(rev) AS m FROM mapping_revs WHERE project_id=?", (pid,)).fetchone()["m"] or 0
                c.execute("INSERT INTO mapping_revs (project_id, rev, parent_rev, data, note, created_at) VALUES (?,?,?,?,?,?)",
                          (pid, mx + 1, cur, json.dumps(clean, ensure_ascii=False), note, now()))
                c.execute("UPDATE projects SET mapping_rev=? WHERE id=?", (mx + 1, pid))
        if not unchanged:
            self.store.bump_project(pid)
            self._project_changed(pid, "mappings.save", {"rev": mx + 1, "note": note, "merged_with": merged_with, **self.mapping_diff(current, clean)})
        out = self.get_mappings(pid)
        if merged_with is not None:
            out["merged_with"] = merged_with
            out["unchanged"] = unchanged
        return out

    def _move_mapping_rev(self, pid: str, base_rev: int | None, redo: bool) -> None:
        """Undo / redo of the mapping list, checked against ``base_rev`` (the revision the
        client's step was decided on) inside the transaction that moves the pointer."""
        self.get_project(pid)
        with self.store.tx() as c:
            cur = c.execute("SELECT mapping_rev FROM projects WHERE id=?", (pid,)).fetchone()["mapping_rev"]
            if base_rev is not None and base_rev != cur:
                raise ServiceError(409, "stale", "映射已在其他窗口被修改，请刷新后再撤销或重做", {"rev": cur, "base_rev": base_rev})
            if redo:
                kid = c.execute("SELECT rev FROM mapping_revs WHERE project_id=? AND parent_rev=? ORDER BY rev DESC LIMIT 1",
                                (pid, cur)).fetchone()
                if not kid:
                    raise ServiceError(400, "nothing_to_redo", "没有可重做的映射修改")
                target = kid["rev"]
            else:
                row = c.execute("SELECT parent_rev FROM mapping_revs WHERE project_id=? AND rev=?", (pid, cur)).fetchone()
                if not row or row["parent_rev"] is None:
                    raise ServiceError(400, "nothing_to_undo", "没有可撤销的映射修改")
                target = row["parent_rev"]
            c.execute("UPDATE projects SET mapping_rev=? WHERE id=?", (target, pid))
        self.store.bump_project(pid)

    def mappings_undo(self, pid: str, base_rev: int | None = None) -> dict:
        self._move_mapping_rev(pid, base_rev, redo=False)
        self._project_changed(pid, "mappings.undo", {"rev": self.get_project(pid)["mapping_rev"]})
        return self.get_mappings(pid)

    def mappings_redo(self, pid: str, base_rev: int | None = None) -> dict:
        self._move_mapping_rev(pid, base_rev, redo=True)
        self._project_changed(pid, "mappings.redo", {"rev": self.get_project(pid)["mapping_rev"]})
        return self.get_mappings(pid)


# ------------------------------------------------------------------ helpers
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_display_name(name: str) -> str:
    name = unicodedata.normalize("NFC", (name or "file").replace("\\", "/").split("/")[-1])
    name = _ILLEGAL.sub("_", name).strip(" .") or "file"
    if len(name.encode("utf-8")) > 200:
        base, dot, ext = name.rpartition(".")
        base = base or name
        while len((base + dot + ext).encode("utf-8")) > 200 and base:
            base = base[:-1]
        name = base + dot + ext
    return name


def safe_output_name(name: str, max_bytes: int = 150) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("..", "_")
    name = _ILLEGAL.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "输出"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.strip(" .") or "输出"


def template_inventory(fmt: str, data: bytes) -> dict:
    """Inspect a template for objects the fill engine does or does not preserve."""
    notes = []
    inv = {}
    if fmt in ("docx", "xlsx", "pptx"):
        from .engine.ooxml import Package
        pkg = Package(data)
        inv = pkg.inventory()
        if fmt == "docx":
            from .engine.fonts import docx_fonts, missing_fonts
            miss = missing_fonts(docx_fonts(pkg))
            if miss:
                notes.append(f"模板使用的字体在服务器上未安装：{'、'.join(miss[:8])}。DOCX 文件本身不受影响（在用户电脑上由 Word 显示）；服务器导出 PDF 时这些字体会被替换，版式可能略有变化")
            if inv.get("charts"):
                notes.append(f"含 {inv['charts']} 个图表：原样保留（不参与填充）")
            if inv.get("comments"):
                notes.append("含批注：原样保留")
            if inv.get("signatures"):
                notes.append("含数字签名：填充后签名将失效")
        if fmt == "xlsx":
            if inv.get("charts"):
                notes.append(f"含 {inv['charts']} 个图表：保留；插入明细行时引用本表的图表数据范围会随之调整")
            if inv.get("pivot"):
                notes.append("含数据透视表：保留，但透视缓存不会自动刷新（打开后需在 Excel 中刷新）")
            if inv.get("externalLinks"):
                notes.append("含外部链接：保留但不会更新，也不会访问外部文件")
            if inv.get("slicers"):
                notes.append("含切片器：保留，未验证插入行后的行为")
    if fmt in ("png", "jpeg"):
        from .engine import pdf_engine
        return {"notes": notes, "inventory": {}, "pdf": pdf_engine.inspect(data)}
    if fmt == "pdf":
        from .engine import pdf_engine
        meta = pdf_engine.inspect(data)
        if meta["signed"]:
            notes.append("PDF 含数字签名：填充后签名将失效")
        if meta["has_js"]:
            notes.append("PDF 含 JavaScript：不会执行")
        if meta["kind"] == "scanned":
            notes.append("扫描型 PDF 模板：框选区域时会真正清除该区域的图像像素（填白）再写入新值")
        return {"notes": notes, "inventory": {}, "pdf": meta}
    return {"notes": notes, "inventory": inv}


PDF_LIKE = ("pdf", "png", "jpeg")

def strip_macros(data: bytes) -> bytes:
    """The package with its VBA project removed and a macro-free main-part content type.

    The work is done by the engine's ``ooxml.strip_vba`` (it also runs on desensitized copies);
    one implementation, so templates and copies cannot drift apart on what "macro-free" means."""
    from .engine.ooxml import strip_vba
    return strip_vba(data)


def pdf_slots_with_nrect(pdf_bytes: bytes, slots: list[dict]) -> list[dict]:
    """Add the on-screen (rotated, normalised) rectangle of each PDF box for display."""
    from .engine import pdf_engine as pe
    out = [dict(s) for s in slots]
    if not pe.pdf_templates_available():
        # Image templates: the page IS the pixel grid (1 px = 1 pt, no rotation), so the
        # normalised rect is plain arithmetic and needs no PDF engine.
        for s in out:
            loc = s.get("locator") or {}
            size = loc.get("page_size")
            if "rect" in loc and size and float(size[0]) > 0 and float(size[1]) > 0:
                x0, y0, x1, y1 = (float(v) for v in loc["rect"])
                w, h = float(size[0]), float(size[1])
                s["nrect"] = [x0 / w, y0 / h, x1 / w, y1 / h]
        return out

    import pymupdf
    doc = pe.open_pdf(pdf_bytes)
    for s in out:
        loc = s.get("locator") or {}
        if "rect" in loc and loc.get("page", 0) < doc.page_count:
            page = doc[loc["page"]]
            s["nrect"] = pe.unrot_to_visual_norm(page, pymupdf.Rect(loc["rect"]))
            lay = s.get("layout")
            if lay:  # PDF repeat area: column dividers and row lines for display
                from .engine import pdf_repeat as pr
                s["ngrid"] = [{"kind": kind, "r": pe.unrot_to_visual_norm(page, r)} for r, kind in pr.grid_lines(lay)]
    doc.close()
    return out
