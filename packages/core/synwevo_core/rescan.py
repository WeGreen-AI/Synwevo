"""What every "rescan the files stored before scanning" command shares (CI-90).

Production switched malware scanning on after files had been stored for weeks; those rows carry
``scan.status`` ``not_configured`` (or no record at all, read as ``unscanned``) and are readable
as usual. Synwevo Docs (sources, templates) and Synwevo Message (attachments) each own a command
that looks at their own rows; this module holds the parts that must behave the same in both —
docs/security/file-acceptance.md §8 is the operator's description:

* **A preview unless told otherwise.** ``rescan`` alone counts what a run would look at and
  checks the scanner; only ``rescan --apply`` scans and writes — a verdict notifies uploaders
  and moves bytes, which is not something to do by accident (the same rule as ``projects prune``).
* **Which rows.** Rows in use whose record is not ``clean``: no record, ``unscanned``,
  ``not_configured``, and ``error`` / ``too_large`` left by an earlier run — and, whatever their
  record says, rows on bytes whose quarantine verdict stands (the other application's rescan, or
  the upload gate, judged the same bytes): those are marked without a scan. Rows the application
  is handling itself (``scanning`` / ``scan_failed``) and rows already ``rejected`` are not
  selected for a scan. Rows under a verdict come first, then rows never tried, then earlier errors
  oldest attempt first, then files over the scan limit, across every kind of row.
* **``--limit N`` counts scans.** A run stops starting new units once it has sent N contents to
  the engine (answered or not — a timeout counts too); units that need no scan (a standing verdict, missing or mismatched bytes, a file over
  the limit it was already recorded against) do not count — so batches always make progress.
* **Finishing interrupted work.** Every apply run starts with `quarantine.settle`: bytes whose
  verdict stands are taken out of the blob store if a crash (or a restored backup) left them there.
* **Bound to the content.** The bytes read must hash to the digest the row records; a mismatch
  (or missing content) is reported and changes nothing. One scan per digest per run.
* **Verdicts.** ``clean`` changes only the scan record (``rescan: true``); ``infected`` rejects
  every row that shares the bytes and moves them to `synwevo_core.quarantine`; ``error`` and
  ``too_large`` record the attempt and leave the row as it was.
* **Refusing to guess.** A scanner that is off or unreachable at the start ends the command with
  exit status 3 before anything is read or written; one that goes away mid-run stops it there.
* **Output.** Ids, digests, sizes, signatures and counts — never file names, contents or other
  user text. Exit status 1 when anything was infected, errored, mismatched or missing.
"""
from __future__ import annotations

import fcntl
import getpass
import hashlib
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import malware as M
from . import quarantine as Q
from .store import Store

#: Scan statuses a rescan may act on. `None`/"" is "no record" (read as `unscanned`).
SELECTABLE = frozenset({None, "", "unscanned", "not_configured", "error", "too_large"})
NEVER_TRIED = frozenset({None, "", "unscanned", "not_configured"})

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_SCANNER = 0, 1, 2, 3

OUTCOME_WORDS = {
    "clean": "干净",
    "infected": "感染：已拒绝，字节已移入隔离区",
    "applied": "已有隔离结论：已拒绝",
    "error": "扫描出错：状态未改，下次运行重试",
    "too_large": "超过扫描上限：未检查，状态未改",
    "mismatch": "内容与记录的摘要不符：未改动",
    "missing": "内容不在存储中：未改动",
    "changed": "扫描期间被改动：未写入，下次运行重试",
}
#: Outcomes that make the exit status non-zero: someone should look.
ATTENTION = frozenset({"infected", "applied", "error", "mismatch", "missing"})


def scan_status(rec) -> str | None:
    return rec.get("status") if isinstance(rec, dict) else None


def selectable(rec) -> bool:
    return scan_status(rec) in SELECTABLE


#: The sort key of a unit on bytes whose verdict stands: first, and no scan is needed.
UNDER_VERDICT = (-1, 0, 0)


def order_key(rec, created_at) -> tuple:
    """Never-tried rows first; then earlier errors, oldest attempt first; then rows over the scan
    limit, which a run only reports (they would otherwise sort first for ever and starve `--limit`)."""
    st = scan_status(rec)
    checked = (rec or {}).get("checked_at") if isinstance(rec, dict) else None
    group = 0 if st in NEVER_TRIED else (2 if st == "too_large" else 1)
    return (group, checked or 0, created_at or 0)


def previous_status(rec) -> str:
    """What the row's record said before any rescan touched it."""
    if isinstance(rec, dict) and rec.get("rescan"):
        return rec.get("previous") or "unscanned"
    return scan_status(rec) or "unscanned"


def clean_record(res: M.ScanResult, sha: str, prev, **extra) -> dict:
    return {"status": "clean", "sha256": sha, "engine": res.engine, "checked_at": res.checked_at,
            "rescan": True, "previous": previous_status(prev), **extra}


def infected_record(signature: str | None, engine: str | None, sha: str) -> dict:
    return {"status": "infected", "signature": signature, "engine": engine, "sha256": sha,
            "checked_at": time.time(), "rescan": True, "quarantined": True}


def attempt_record(status: str, reason: str, sha: str, prev, **extra) -> dict:
    tries = int(prev.get("attempts") or 0) if isinstance(prev, dict) and prev.get("rescan") else 0
    return {"status": status, "reason": (reason or "")[:200], "sha256": sha, "checked_at": time.time(),
            "rescan": True, "attempts": tries + 1, "previous": previous_status(prev), **extra}


# ------------------------------------------------------------------ the scanner
class ScannerUnavailable(Exception):
    """Off, misconfigured or unreachable: the command must not act."""


def open_scanner() -> tuple[M.ClamdScanner, str]:
    try:
        pol = M.policy()
    except ValueError as e:
        raise ScannerUnavailable(f"扫描配置无效：{e}") from e
    if not pol.enabled:
        raise ScannerUnavailable("本进程没有开启恶意软件扫描（SYNWEVO_MALWARE_SCAN=off 或未设置 SYNWEVO_CLAMD_ADDRESS）；"
                                 "补扫必须在开启扫描的容器里运行")
    sc = M.scanner_for(pol)
    try:
        if not sc.ping():
            raise ScannerUnavailable(f"扫描器 {pol.address} 没有回应 PONG")
        engine = sc.version()
    except M.ScanUnavailable as e:
        raise ScannerUnavailable(f"扫描器不可达：{str(e)[:200]}") from e
    return sc, engine


def scanner_state() -> str:
    """One line for a dry run: what a real run would meet."""
    try:
        _, engine = open_scanner()
        return f"可用（{engine}）"
    except ScannerUnavailable as e:
        return f"不可用：{e}"


class ScannerLost(Exception):
    """The daemon stopped answering in the middle of a run."""


@dataclass
class Verdict:
    outcome: str                  # clean | infected | too_large | error | missing | mismatch
    sha256: str
    signature: str | None = None
    engine: str | None = None
    reason: str | None = None
    result: M.ScanResult | None = None
    data: bytes | None = field(default=None, repr=False)   # kept only for infected (the quarantine needs it)


def read_verified(store: Store, sha: str) -> tuple[str, bytes | None]:
    """('ok', bytes) | ('missing', None) | ('mismatch', None)."""
    try:
        path = store.blob_path(sha)
    except ValueError:
        return "mismatch", None
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing", None
    if hashlib.sha256(data).hexdigest() != sha:
        return "mismatch", None
    return "ok", data


class Examiner:
    """One verdict per digest per run, from bytes that were checked against their digest."""

    def __init__(self, store: Store, scanner: M.ClamdScanner):
        self.store = store
        self.scanner = scanner
        self.cache: dict[str, Verdict] = {}
        self.scans = 0                        # answers received
        self.attempts = 0                     # contents sent, answered or not: what --limit counts

    def examine(self, sha: str) -> Verdict:
        if sha in self.cache:
            return self.cache[sha]
        v = self._examine(sha)
        self.cache[sha] = v
        return v

    def _examine(self, sha: str) -> Verdict:
        try:
            size = self.store.blob_path(sha).stat().st_size
        except FileNotFoundError:
            return Verdict("missing", sha)
        except ValueError:
            return Verdict("mismatch", sha)
        if size > self.scanner.max_bytes:
            return Verdict("too_large", sha, reason=f"{size} bytes exceeds the scan limit of {self.scanner.max_bytes} bytes")
        state, data = read_verified(self.store, sha)
        if state != "ok":
            return Verdict(state, sha)
        return self._scan(sha, data)

    def examine_bytes(self, sha: str, data: bytes) -> Verdict:
        """A copy held outside the blob store (a Docs regression case): the same verdict rules,
        bound to the digest of the bytes themselves, still one scan per digest per run (a digest
        the store did not have is looked at here, from the copy)."""
        if sha in self.cache and self.cache[sha].outcome != "missing":
            return self.cache[sha]
        if hashlib.sha256(data).hexdigest() != sha:
            v = Verdict("mismatch", sha)
        elif len(data) > self.scanner.max_bytes:
            v = Verdict("too_large", sha, reason=f"{len(data)} bytes exceeds the scan limit of {self.scanner.max_bytes} bytes")
        else:
            v = self._scan(sha, data)
        self.cache[sha] = v
        return v

    def _scan(self, sha: str, data: bytes) -> Verdict:
        self.attempts += 1
        try:
            res = self.scanner.scan(data, sha256=sha)
            self.scans += 1
        except M.ScanUnavailable as e:
            try:
                alive = self.scanner.ping()
            except M.ScanUnavailable:
                alive = False
            if not alive:
                raise ScannerLost(str(e)[:200]) from e
            return Verdict("error", sha, reason=str(e)[:200])
        if res.status == "clean":
            return Verdict("clean", sha, engine=res.engine, result=res)
        if res.status == "infected":
            return Verdict("infected", sha, signature=res.signature, engine=res.engine, result=res, data=data)
        return Verdict("too_large", sha, engine=res.engine, reason=res.reason, result=res)


# ------------------------------------------------------------------ one run at a time
class RescanBusy(Exception):
    pass


@contextmanager
def run_lock(store: Store, name: str):
    """One quarantine operation at a time, across **both** applications: every ``rescan --apply``,
    ``quarantine release`` and ``quarantine purge``. They act on digests, not on one
    application's rows — a rescan starts by taking out of the blob store every copy whose verdict
    stands (`quarantine.settle`), a release puts copies back before it withdraws the verdict, a
    purge deletes the held copy — so a Docs release running beside a Message rescan could have
    the file it just restored taken out of the store again, and then out of the quarantine too.
    The second operation is refused (exit 2), never queued."""
    d = Q.directory(store, create=True)
    fh = open(d / ".rescan.lock", "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RescanBusy(f"另一个补扫、恢复或清除正在运行（{name} 这次没有开始）") from e
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def cli_actor(app: str) -> str:
    """Who ran the command, for the index and the audit trail: the OS account is all a CLI has."""
    try:
        return f"cli:{getpass.getuser()}@{app}"
    except Exception:  # noqa: BLE001 - no passwd entry in some containers
        return f"cli:{os.getuid()}@{app}"


# ------------------------------------------------------------------ what the operator reads
def short(sha: str | None) -> str:
    return (sha or "-")[:16]


class Report:
    """Progress lines and the final summary. Ids, digests, sizes, signatures and counts only."""

    def __init__(self, app: str, out=None):
        self.app = app
        self.out = out or sys.stdout
        self.counts: dict[str, Counter] = {}
        self.rows: dict[str, Counter] = {}
        self.findings: list[dict] = []
        self.quarantined: list[dict] = []
        self.stopped: str | None = None
        self.deferred = 0                     # units left for the next run by --limit
        self.i = 0
        self.total = 0

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def failed(self, kind: str, ident: str, sha: str | None, exc: BaseException) -> None:
        """A unit that raised: counted as an error (retried next run) so the rest of the run and
        its summary still happen. Only the exception's class — its text can quote a path or a name."""
        self.unit(kind, ident, sha, "error", detail=f"内部错误 {type(exc).__name__}")

    def unit(self, kind: str, ident: str, sha: str | None, outcome: str, rows: int = 1, detail: str = "") -> None:
        self.i += 1
        self.counts.setdefault(kind, Counter())[outcome] += 1
        self.rows.setdefault(kind, Counter())[outcome] += rows
        extra = f"（{rows} 行）" if rows > 1 else ""
        self.say(f"[{self.i}/{self.total}] {kind} {ident}{extra} sha256={short(sha)} {OUTCOME_WORDS[outcome]}"
                 + (f"：{detail}" if detail else ""))
        if outcome not in ("clean", "infected", "applied"):
            self.findings.append({"kind": kind, "id": ident, "sha256": sha, "outcome": outcome, "detail": detail})

    def exit_code(self) -> int:
        if self.stopped:
            return EXIT_SCANNER
        for c in self.counts.values():
            if any(c[o] for o in ATTENTION):
                return EXIT_FINDINGS
        return EXIT_OK

    def summary(self, engine: str | None) -> int:
        self.say()
        self.say(f"== 补扫结果（{self.app}）==")
        if engine:
            self.say(f"扫描引擎：{engine}")
        if not self.counts:
            self.say("没有需要补扫的内容。")
        for kind, c in self.counts.items():
            parts = "，".join(f"{OUTCOME_WORDS[o].split('：')[0]} {c[o]}" for o in OUTCOME_WORDS if c[o])
            self.say(f"{kind}：检查 {sum(c.values())} 份（{sum(self.rows[kind].values())} 行）— {parts}")
        for q in self.quarantined:
            self.say(f"已隔离 sha256={q['sha256']} 签名={q.get('signature') or '-'}")
            for line in q.get("lines", []):
                self.say(f"    {line}")
        by = {}
        for f in self.findings:
            by.setdefault(f["outcome"], []).append(f)
        for outcome, items in by.items():
            self.say(f"{OUTCOME_WORDS[outcome]}：{len(items)}")
            for f in items[:50]:
                self.say(f"    {f['kind']} {f['id']} sha256={f['sha256'] or '-'}" + (f" {f['detail']}" if f["detail"] else ""))
            if len(items) > 50:
                self.say(f"    …另有 {len(items) - 50} 项")
        if self.stopped:
            self.say(f"扫描器在运行中不可达，已停止：{self.stopped}。已完成的结论保留；再次运行会从未完成处继续。")
        if self.deferred:
            self.say(f"已达到 --limit 的扫描次数：还有 {self.deferred} 份留待下次运行。")
        code = self.exit_code()
        self.say(f"退出码：{code}")
        return code


# ------------------------------------------------------------------ quarantine commands (shared)
def print_entries(store: Store, out=None) -> int:
    out = out or sys.stdout
    items = Q.entries(store)
    if not items:
        print("隔离区是空的。", file=out)
        return EXIT_OK
    for e in items:
        rows = Counter(r["kind"] for r in Q.pending(e))
        released = Counter(r["kind"] for r in e.get("rows") or [] if r.get("released_at"))
        held = "，".join(f"{k} {n}" for k, n in sorted(rows.items())) or "无"
        back = "，".join(f"{k} {n}" for k, n in sorted(released.items())) or "无"
        at = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("quarantined_at") or 0))
        aside = Q.aside_copies(store, e["sha256"])
        print(f"sha256={e['sha256']} 状态={e.get('state')} 签名={e.get('signature') or '-'} 大小={e.get('size')} "
              f"隔离于={at} 仍拒绝的行：{held}；已恢复的行：{back}"
              + (f"；移在一旁的回归用例：{'、'.join(aside)}" if aside else ""), file=out)
    return EXIT_OK


def purge(store: Store, sha: str, *, yes: bool, by: str, app: str, out=None) -> int:
    """Delete the quarantined bytes of a confirmed verdict. Regression cases moved aside are
    never deleted: they hold other files too, and are listed for the operator to handle."""
    out = out or sys.stdout
    try:
        e = Q.load(store, sha)
    except Q.QuarantineError as err:
        print(str(err), file=sys.stderr)
        return EXIT_USAGE
    if not e:
        print(f"{sha} 没有隔离记录", file=sys.stderr)
        return EXIT_USAGE
    aside = Q.aside_copies(store, sha)
    kept = (f"移在一旁的 {len(aside)} 个回归用例不会被删除（其中还有别的文件），留在 quarantine/{sha}.d/ 下由人处理："
            + "、".join(aside) + "。") if aside else ""
    if not yes:
        print(f"将永久删除隔离区里 sha256={sha} 的内容（{e.get('size')} 字节，签名 {e.get('signature') or '-'}）。"
              f"引用它的 {len(Q.pending(e))} 行保持「已拒绝」。{kept}确认请加 --yes。", file=out)
        return EXIT_USAGE
    try:
        with run_lock(store, app):
            Q.settle(store)                    # a store copy left by an interrupted run goes first
            Q.purge(store, sha, by=by)
    except RescanBusy as err:
        print(f"{err}；等它结束后再清除。", file=sys.stderr)
        return EXIT_USAGE
    except Q.QuarantineError as err:
        print(str(err), file=sys.stderr)
        return EXIT_USAGE
    from .auth import audit_event
    audit_event(store, "quarantine.purged", actor_kind="system", actor_id=by, object_kind="quarantine",
                object_id=sha, meta={"signature": e.get("signature"), "rows": len(Q.pending(e)), "aside_kept": len(aside)})
    print(f"已清除 sha256={sha}：隔离区不再保存这份内容，引用它的行保持「已拒绝」。{kept}", file=out)
    return EXIT_OK


def add_arguments(sub, *, workspace: bool, kinds: tuple[str, ...]) -> None:
    """The `rescan` and `quarantine` subcommands, the same in both applications."""
    r = sub.add_parser("rescan", help="补扫扫描上线前存入的文件（默认只预览；见 docs/security/file-acceptance.md §8）")
    r.add_argument("--apply", action="store_true",
                   help="真正扫描并写入结论（会拒绝感染的行、移走字节、通知上传者）；不加就只统计将要检查的内容并报告扫描器是否可用")
    r.add_argument("--limit", type=int, default=None,
                   help="本次最多扫描 N 份内容（同一摘要的多行算一份；已有结论、缺失、超限的不需要扫描，不计入）；再次运行从未完成处继续")
    if workspace:
        r.add_argument("--workspace", help="只检查这个工作空间的行（感染时同一字节的其他行仍一起拒绝）")
    if len(kinds) > 1:
        r.add_argument("--only", choices=kinds, help="只检查一类")
    qz = sub.add_parser("quarantine", help="隔离区：列出、复核后恢复（误报）或永久清除")
    qz.add_argument("action", choices=["list", "release", "purge"])
    qz.add_argument("sha256", nargs="?", help="release / purge 的对象（完整 SHA-256）")
    qz.add_argument("--yes", action="store_true", help="purge：确认永久删除")
