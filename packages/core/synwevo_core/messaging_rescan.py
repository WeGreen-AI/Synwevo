"""Rescan the message attachments stored before malware scanning was switched on (CI-90).

    python -m synwevo_messages.cli rescan [--apply] [--limit N]
    python -m synwevo_messages.cli quarantine list | release <sha256> | purge <sha256> --yes

The logic lives next to `synwevo_core.messaging`, which owns ``message_attachments``; Synwevo
Message's command line only wires it up. Selection, digest binding and verdicts follow
`synwevo_core.rescan` (shared with Synwevo Docs):

* ``rescan`` alone is a preview; ``--apply`` scans and writes;
* rows with ``status='accepted'`` whose scan record is not ``clean``;
* ``clean`` changes only the scan record; ``infected`` makes every attachment row on the same
  bytes ``rejected`` — the download (`Messaging.attachment_for_download`, 410
  ``rejected_malware``), inline display and forwarding all refuse it — and moves the bytes to the
  quarantine; ``error`` / ``too_large`` record the attempt and change nothing else; ``encrypted``
  (password-protected, cannot be checked) records the finding and leaves the attachment as it
  was — withdrawing it is the owner's decision (docs/security/file-acceptance.md §8.1).

Nobody is told in the inbox: the notification catalogue has no category for "an attachment you
sent was withdrawn" (``docs.file_rejected`` names a Docs project), and this change does not invent
one — the summary says so, and the rows' ``scan`` record carries the verdict.
"""
from __future__ import annotations

import sys

from . import malware as M
from . import quarantine as Q
from . import rescan as RS
from .auth import audit_event
from .store import Store

KIND = "附件"
MESSAGE_KINDS = frozenset({"message_attachments"})


class AttachmentRescan:
    def __init__(self, store: Store, report: RS.Report, by: str):
        self.store = store
        self.report = report
        self.by = by
        self._rejected: set[str] = set()

    def groups(self) -> list[dict]:
        """Accepted rows whose record is not clean — and, whatever their record says, rows on
        bytes whose quarantine verdict stands (marked without a scan). One order across them."""
        held = Q.standing(self.store)
        groups: dict[str, list[dict]] = {}
        for r in self.store.all("SELECT id, conversation_id, blob, size, status, scan, created_at FROM message_attachments "
                                "WHERE status='accepted' AND blob IS NOT NULL"):
            if r["blob"] in held or RS.selectable(r.get("scan")):
                groups.setdefault(r["blob"], []).append(r)
        out = [{"sha256": b, "rows": rows,
                "key": RS.UNDER_VERDICT if b in held else min(RS.order_key(r.get("scan"), r.get("created_at")) for r in rows)}
               for b, rows in groups.items()]
        return sorted(out, key=lambda g: g["key"])

    def run(self, ex: RS.Examiner, *, limit: int | None) -> None:
        """`limit` counts scans: units that need none (a standing verdict, missing or mismatched
        bytes) never hold a batch back."""
        groups = self.groups()
        self.report.total = len(groups)
        for n, g in enumerate(groups):
            if limit and ex.attempts >= limit:
                self.report.deferred = len(groups) - n
                break
            try:
                self._unit(ex, g)
            except RS.ScannerLost:
                raise
            except Exception as e:  # noqa: BLE001 - one broken unit must not take the summary with it
                self.report.failed(KIND, ",".join(r["id"] for r in g["rows"][:3]), g["sha256"], e)

    def _unit(self, ex: RS.Examiner, g: dict) -> None:
        sha, rows = g["sha256"], g["rows"]
        ident = ",".join(r["id"] for r in rows[:3]) + ("…" if len(rows) > 3 else "")
        standing = Q.verdict(self.store, sha)
        if standing:                          # the verdict stands until a release withdraws it
            self._reject(sha, standing.get("signature"), standing.get("engine"), None)
            self.report.unit(KIND, ident, sha, "applied", len(rows), standing.get("signature") or "")
            return
        v = ex.examine(sha)
        if v.outcome == "infected":
            self._reject(sha, v.signature, v.engine, v.data)
            self.report.unit(KIND, ident, sha, "infected", len(rows), v.signature or "")
            return
        if v.outcome == "missing":
            standing = Q.verdict(self.store, sha)
            if standing:
                self._reject(sha, standing.get("signature"), standing.get("engine"), None)
                self.report.unit(KIND, ident, sha, "applied", len(rows), standing.get("signature") or "")
                return
        if v.outcome in ("missing", "mismatch"):
            self.report.unit(KIND, ident, sha, v.outcome, len(rows))
            return
        if v.outcome == "clean":
            made = [self._publish(r, RS.clean_record(v.result, sha, r.get("scan"))) for r in rows]
            self.report.unit(KIND, ident, sha, "clean" if any(made) else "changed", len(rows))
            return
        if v.outcome == "encrypted":
            # Not malware, and not something this command withdraws: recorded, access unchanged.
            made = [self._publish(r, RS.encrypted_record(v, sha, r.get("scan"))) for r in rows]
            self.report.unit(KIND, ident, sha, "encrypted" if any(made) else "changed", len(rows), v.reason or "")
            return
        limit = ex.scanner.max_bytes
        for r in rows:
            prev = r.get("scan") or {}
            if v.outcome == "too_large" and RS.scan_status(prev) == "too_large" and prev.get("limit") == limit:
                continue
            extra = {"limit": limit} if v.outcome == "too_large" else {}
            self._publish(r, RS.attempt_record(v.outcome, v.reason or "", sha, prev, **extra))
        detail = f"size={rows[0].get('size')} limit={limit}" if v.outcome == "too_large" else (v.reason or "")
        self.report.unit(KIND, ident, sha, v.outcome, len(rows), detail)

    def _publish(self, row: dict, rec: dict) -> bool:
        """Only the scan record, and only if the row is still the accepted row on the same bytes
        with the record that was read (a forward, a recall or another writer wins)."""
        with self.store.tx():
            cur = self.store.one("SELECT blob, status, scan FROM message_attachments WHERE id=?", (row["id"],))
            if not cur or cur["blob"] != row["blob"] or cur["status"] != "accepted" or cur.get("scan") != row.get("scan"):
                return False
            self.store.update("message_attachments", {"id": row["id"]}, {"scan": rec})
        return True

    def _reject(self, sha: str, signature: str | None, engine: str | None, data: bytes | None) -> None:
        if sha in self._rejected:
            return
        self._rejected.add(sha)
        store = self.store
        query = ("SELECT a.id, a.status, a.scan, c.organization_id FROM message_attachments a "
                 "LEFT JOIN conversations c ON c.id=a.conversation_id WHERE a.blob=? AND a.status<>'rejected'")
        found = store.all(query, (sha,))
        rows = [{"kind": "message_attachments", "id": r["id"], "workspace": r.get("organization_id"),
                 "previous": {"status": r["status"], "scan": r.get("scan")}} for r in found]
        Q.hold(store, sha, data, signature=signature, engine=engine, rows=rows, by=self.by)
        rec = RS.infected_record(signature, engine, sha)
        known = {r["id"] for r in found}
        late = []
        with store.tx():
            for r in store.all(query, (sha,)):
                store.update("message_attachments", {"id": r["id"]}, {"status": "rejected", "scan": rec})
                if r["id"] not in known:
                    late.append({"kind": "message_attachments", "id": r["id"], "workspace": r.get("organization_id"),
                                 "previous": {"status": r["status"], "scan": r.get("scan")}})
                    found.append(r)
        if late:
            Q.hold(store, sha, None, signature=signature, engine=engine, rows=late, by=self.by)
        if Q.bytes_path(store, sha).is_file():
            Q.remove_from_store(store, sha)
        for r in found:
            audit_event(store, "attachment.rescan_rejected", actor_kind="system", actor_id=self.by,
                        object_kind="message_attachment", object_id=r["id"],
                        meta={"signature": signature, "engine": engine, "organization_id": r.get("organization_id")})
        lines = [f"附件 {len(found)} 行：{', '.join(r['id'] for r in found[:20])}" + ("…" if len(found) > 20 else ""),
                 "上传者没有收到通知：通知目录没有「附件被撤回」的类别（docs.file_rejected 只适用于 Docs 项目），附件的 scan 记录写明了结论"]
        others = Q.unmarked_references(store, sha).get("docs") or 0
        if others:
            lines.append(f"Synwevo Docs 仍有 {others} 行引用这些字节：请在 docs 容器运行 python -m synwevo_docs.cli rescan --apply")
        self.report.quarantined.append({"sha256": sha, "signature": signature, "lines": lines})


def release(store: Store, sha: str, by: str, out=None) -> int:
    """Put the bytes back and make this application's rows accepted again — only once the current
    engine says they are clean (the false positive was withdrawn from the signature database).
    Takes the Message run lock, so it never interleaves with a rescan of the same rows."""
    out = out or sys.stdout
    try:
        with RS.run_lock(store, "messages"):
            return _release(store, sha, by, out)
    except RS.RescanBusy as e:
        print(f"{e}；等它结束后再恢复。", file=sys.stderr)
        return RS.EXIT_USAGE


def _release(store: Store, sha: str, by: str, out) -> int:
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
    mine = Q.pending(entry, set(MESSAGE_KINDS))
    if not mine:
        print(f"{sha} 没有待恢复的 Synwevo Message 附件（其余行由各自的应用恢复）", file=out)
        return RS.EXIT_OK
    try:
        scanner, _ = RS.open_scanner()
        data = Q.read_any(store, sha)
        res = scanner.scan(data, sha256=sha)
    except RS.ScannerUnavailable as e:
        print(str(e), file=sys.stderr)
        return RS.EXIT_SCANNER
    except M.ScanUnavailable as e:
        print(f"扫描器不可达：{str(e)[:200]}", file=sys.stderr)
        return RS.EXIT_SCANNER
    except Q.QuarantineError as e:
        print(str(e), file=sys.stderr)
        return RS.EXIT_FINDINGS
    if res.status != "clean":
        print(RS.release_refused(res, sha), file=out)
        return RS.EXIT_FINDINGS
    Q.put_back(store, sha, data)                 # a damaged store copy is replaced, never kept
    restored, kept = 0, []
    done: set[tuple[str, str]] = set()
    with store.tx():
        for r in mine:
            cur = store.one("SELECT status, scan FROM message_attachments WHERE id=?", (r["id"],))
            if not cur or cur["status"] != "rejected":
                done.add((r["kind"], r["id"]))     # recalled/removed since, or no longer rejected: nothing held here
                continue
            if (cur.get("scan") or {}).get("sha256") != sha:
                kept.append(r["id"])
                continue
            prev = r.get("previous") or {}
            store.update("message_attachments", {"id": r["id"]},
                         {"status": "accepted", "scan": RS.clean_record(res, sha, prev.get("scan"), released=True)})
            restored += 1
            done.add((r["kind"], r["id"]))
    Q.release(store, sha, done, by=by, engine=res.engine)
    audit_event(store, "quarantine.released", actor_kind="system", actor_id=by, object_kind="quarantine", object_id=sha,
                meta={"engine": res.engine, "app": "messages", "message_attachments": restored, "kept": len(kept)})
    print(f"已恢复 sha256={sha}（当前引擎 {res.engine} 判定干净）：附件 {restored} 行。", file=out)
    if kept:
        print(f"没有改动、仍在隔离中（状态已被他人改动）：{', '.join(kept)}", file=out)
    left = Q.pending(Q.load(store, sha) or {})
    if left:
        print(f"仍有 {len(left)} 行待各自的应用恢复（Synwevo Docs 用 python -m synwevo_docs.cli quarantine release {sha}）", file=out)
    # Nothing came back although rows were waiting: someone has to look (exit 1), as for a rescan.
    return RS.EXIT_FINDINGS if kept and not restored else RS.EXIT_OK


def main_rescan(args, data: str) -> int:
    if args.limit is not None and args.limit < 1:
        print("--limit 必须是正整数", file=sys.stderr)
        return RS.EXIT_USAGE
    store = Store(data)
    runner = AttachmentRescan(store, RS.Report("Synwevo Message"), RS.cli_actor("messages"))
    if not getattr(args, "apply", False):
        try:
            max_bytes = M.policy().max_bytes
        except ValueError:
            max_bytes = M.DEFAULT_MAX_BYTES
        groups = runner.groups()
        by_status: dict[str, int] = {}
        for g in groups:
            for r in g["rows"]:
                st = RS.scan_status(r.get("scan")) or "unscanned"
                by_status[st] = by_status.get(st, 0) + 1
        big = sum(1 for g in groups if max(r.get("size") or 0 for r in g["rows"]) > max_bytes)
        runner.report.say("补扫预览（Synwevo Message，只读：不扫描、不写入；加 --apply 才会扫描并写入）")
        runner.report.say(f"附件：{len(groups)} 份内容（{sum(len(g['rows']) for g in groups)} 行）；按原记录："
                          + ("，".join(f"{k} {n}" for k, n in sorted(by_status.items())) or "无")
                          + f"；超过扫描上限（{max_bytes} 字节）{big} 份")
        if args.limit:
            runner.report.say(f"--limit {args.limit}：一次运行最多扫描 {args.limit} 份（共 {len(groups)} 份待看；已有结论、缺失、超限的不计入）")
        state = RS.scanner_state()
        runner.report.say(f"扫描器：{state}")
        return RS.EXIT_OK if state.startswith("可用") else RS.EXIT_SCANNER
    try:
        scanner, engine = RS.open_scanner()
    except RS.ScannerUnavailable as e:
        print(f"{e}。没有读取或改动任何内容。", file=sys.stderr)
        return RS.EXIT_SCANNER
    try:
        with RS.run_lock(store, "messages"):
            rep = runner.report
            rep.say(f"补扫 Synwevo Message：扫描引擎 {engine}，扫描上限 {scanner.max_bytes} 字节"
                    + (f"，本次最多 {args.limit} 份" if args.limit else ""))
            for st in Q.settle(store):
                rep.say(f"上次中断留下的存储副本：sha256={st['sha256']} "
                        + {"removed": "已从存储移走", "moved": "已移入隔离区", "store_copy_damaged": "与摘要不符，未动（请人工核对）"}[st["action"]])
            ex = RS.Examiner(store, scanner)
            try:
                runner.run(ex, limit=args.limit)
            except RS.ScannerLost as e:
                rep.stopped = str(e)
            audit_event(store, "rescan.run", actor_kind="system", actor_id=runner.by, object_kind="rescan", object_id="messages",
                        meta={"engine": engine, "limit": args.limit, "scans": ex.scans, "stopped": bool(rep.stopped),
                              "counts": {k: dict(c) for k, c in rep.counts.items()}})
            return rep.summary(engine)
    except RS.RescanBusy as e:
        print(f"{e}；等它结束后再运行。", file=sys.stderr)
        return RS.EXIT_USAGE


def main_quarantine(args, data: str) -> int:
    store = Store(data)
    by = RS.cli_actor("messages")
    if args.action == "list":
        return RS.print_entries(store)
    if not args.sha256:
        print(f"quarantine {args.action} 需要完整的 SHA-256", file=sys.stderr)
        return RS.EXIT_USAGE
    sha = args.sha256.strip().lower()
    if args.action == "purge":
        return RS.purge(store, sha, yes=args.yes, by=by, app="messages")
    return release(store, sha, by)
