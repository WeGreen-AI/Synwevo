"""Synwevo Docs 运维 CLI：机器接口密钥、OpenAPI 导出、任务与项目维护。

    python -m synwevo_docs.cli apikey create --name ci --tenant <工作空间 ID> --scopes read,write
    python -m synwevo_docs.cli apikey list
    python -m synwevo_docs.cli apikey rotate <id> [--grace-minutes N]   # 同一密钥链，旧密钥可有宽限期
    python -m synwevo_docs.cli apikey revoke <id>                       # 结束整条密钥链（安全操作）
    python -m synwevo_docs.cli apikey revoke --stdin < leaked.txt       # 按完整密钥撤销，不经命令行参数
    python -m synwevo_docs.cli openapi --out docs/integration/openapi.yaml
    python -m synwevo_docs.cli jobs recover                  # resume jobs left by a stopped worker
    python -m synwevo_docs.cli projects                      # 清单与清理（按来源证据，默认 dry-run）
    python -m synwevo_docs.cli info                          # 数据目录、schema 版本、运行配置

账户、应用注册与 OIDC 客户端不在这里：它们属于账户服务，见 `python -m synwevo_identity.cli`。
一个能同时改业务数据和账户权限的命令行，是"业务管理员顺手成了平台管理员"的最短路径。

数据库取 `$SYNWEVO_DATA_DIR`，默认仓库根目录的 `data/`。密钥的明文只在创建或轮换时显示一次，库里只存哈希；
完整密钥从不经命令行参数读入（会留在 shell 历史与进程列表里）。
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from synwevo_core import env
from synwevo_core.auth import MAX_ROTATION_GRACE_S, SCOPES, Auth
from synwevo_core.errors import ServiceError
from synwevo_core.http import data_dir_default
from synwevo_core.store import Store, now


def data_dir(args) -> str:
    return args.data or env.get("DATA_DIR") or str(data_dir_default())


def _ts(v) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"


_KEY_STATUS = {"active": "有效", "grace": "已轮换（宽限中）", "rotated": "已轮换", "revoked": "已撤销",
               "expired": "已过期"}
_FULL_TOKEN = re.compile(r"^dw_[0-9a-fA-F]+_.+")


def _cli_actor() -> str:
    """Who ran the command, for the audit trail. The OS account is the only identity a CLI has."""
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - no passwd entry in some containers
        return "cli"


def _audit(store, action: str, key: dict | None, *, result: str = "ok", meta: dict | None = None) -> None:
    from synwevo_core.auth import audit_event
    audit_event(store, action, actor_kind="system", actor_id=_cli_actor(),
                workspace_id=(key or {}).get("tenant"), object_kind="api_key",
                object_id=(key or {}).get("id"), result=result, meta=meta)


def _key_arg(args, auth: Auth) -> tuple[str | None, int]:
    """The key an action targets: its id on the command line, or a full key read from stdin.

    A full key is never accepted as an argument: arguments land in shell history and in the
    process list, where anyone on the machine can read them. `--stdin` is the way to act on a
    key you hold (for instance one that leaked) without knowing its id.
    """
    if args.stdin:
        token = sys.stdin.readline().strip()
        kid = auth.key_id_from_token(token)
        if not kid:
            print("标准输入里的不是本部署的有效 API 密钥（或密钥 ID 与密文不匹配）", file=sys.stderr)
            return None, 2
        return kid, 0
    raw = (args.key_id or "").strip()
    if not raw:
        print(f"请给出密钥 ID：apikey {args.action} <key_id>，或用 --stdin 从标准输入读入完整密钥", file=sys.stderr)
        return None, 2
    if _FULL_TOKEN.match(raw):
        kid = raw.split("_", 2)[1]
        print("不要把完整密钥写在命令行参数里：它会留在 shell 历史和进程列表中。\n"
              f"只传密钥 ID（这一个是 {kid}），或用 --stdin 从标准输入读入完整密钥。\n"
              "如果这把密钥可能已经泄露，请撤销它：apikey revoke " + kid, file=sys.stderr)
        return None, 2
    return raw, 0


def _print_key_tenants(auth: Auth) -> None:
    """The workspaces a key can be created for, so the next attempt can be copied from here."""
    rows = auth.key_tenant_candidates()
    if not rows:
        print("\n当前没有可以签发密钥的工作空间：需要一个启用了 Synwevo Docs 的有效工作空间"
              "（python -m synwevo_identity.cli app enable docweave --workspace <工作空间 ID>）。", file=sys.stderr)
        return
    print("\n可以签发密钥的工作空间：", file=sys.stderr)
    for r in rows:
        note = "  （未认领的历史数据空间）" if r["status"] == "pending_claim" else ""
        print(f"  {r['id']:<24}{(r['name'] or '')[:30]}{note}", file=sys.stderr)
    print("\n例如：python -m synwevo_docs.cli apikey create --name ci --tenant "
          f"{rows[0]['id']} --scopes read,write", file=sys.stderr)


def cmd_apikey(args) -> int:
    store = Store(data_dir(args))
    auth = Auth(store)
    if args.action == "create":
        if not args.tenant:
            print("必须指定 --tenant <工作空间 ID>。\n"
                  "旧版本默认把密钥放进 default（未认领的历史数据空间），那恰恰是不应该被顺手访问到的地方。",
                  file=sys.stderr)
            _print_key_tenants(auth)
            return 2
        problem = auth.key_tenant_problem(args.tenant)
        if problem:
            print(f"不能为 {args.tenant!r} 创建密钥：{problem}", file=sys.stderr)
            _print_key_tenants(auth)
            return 2
        scopes = tuple(s.strip() for s in (args.scopes or "read,write").split(",") if s.strip())
        try:
            token, row = auth.create_key(args.name, args.tenant, scopes, args.expires_days)
        except ServiceError as e:
            print(e.message, file=sys.stderr)
            return 2
        _audit(store, "apikey.create", row, meta={"scopes": row["scopes"], "expires_at": row["expires_at"]})
        ws = store.one("SELECT status FROM workspaces WHERE id=?", (row["tenant"],))
        print(f"密钥 ID : {row['id']}")
        print(f"租户    : {row['tenant']}")
        print(f"权限    : {row['scopes']}")
        print(f"过期    : {_ts(row['expires_at'])}")
        if ws and ws["status"] == "pending_claim":
            print("注意    : 这是尚未认领的历史数据空间；认领之后请为新的归属另发密钥。")
        print("\n请立即保存下面这行（只显示一次，服务器只保存哈希）：\n")
        print(token)
        print("\n用法：Authorization: Bearer <上面的密钥>")
        return 0
    if args.action == "list":
        rows = auth.list_keys()
        if not rows:
            print("还没有 API 密钥。用 `apikey create --tenant <工作空间 ID>` 创建。")
            return 0
        print(f"{'ID':<14}{'密钥链':<14}{'名称':<18}{'租户':<14}{'权限':<16}{'创建':<18}{'过期':<18}{'最近使用':<18}状态")
        for r in rows:
            state = _KEY_STATUS.get(r["status"], r["status"])
            if r["status"] == "grace":
                state += f"，至 {_ts(r['expires_at'])}"
            if r["replaced_by"]:
                state += f" → {r['replaced_by']}"
            print(f"{r['id']:<14}{r['lineage']:<14}{(r['name'] or '')[:16]:<18}{r['tenant'][:13]:<14}{r['scopes']:<16}"
                  f"{_ts(r['created_at']):<18}{_ts(r['expires_at']):<18}{_ts(r['last_used_at']):<18}{state}")
        return 0
    if args.action == "revoke":
        kid, rc = _key_arg(args, auth)
        if rc:
            return rc
        key = auth.get_key(kid)
        if not key:
            print("未找到该密钥", file=sys.stderr)
            return 1
        from synwevo_core.auth import JournalWriteFailed
        try:
            n = auth.revoke_key(kid)
        except JournalWriteFailed as e:
            _audit(store, "apikey.revoke", key, result="error",
                   meta={"lineage": key["lineage"], "revoked": e.revoked, "journal": "failed"})
            print(f"已撤销密钥链 {key['lineage']}（{e.revoked} 个密钥，立即生效），但写入安全日志失败：{e.why}\n"
                  "从备份恢复数据库时，这次撤销不会被自动重放。请修复数据目录的写权限后再执行一次本命令。",
                  file=sys.stderr)
            return 3
        _audit(store, "apikey.revoke", key, meta={"lineage": key["lineage"], "revoked": n})
        if not n:
            print(f"密钥链 {key['lineage']} 已经全部撤销，没有需要改动的。")
            return 0
        print(f"已撤销密钥链 {key['lineage']}：{n} 个密钥立即失效（包括轮换宽限期内的旧密钥）。"
              "由这条密钥链提交、尚未完成的任务会被停止。")
        return 0
    if args.action == "rotate":
        kid, rc = _key_arg(args, auth)
        if rc:
            return rc
        g = args.grace_minutes
        if g is not None and (not math.isfinite(g) or not 0 <= g * 60 <= MAX_ROTATION_GRACE_S):
            print(f"--grace-minutes 必须是 0 到 {MAX_ROTATION_GRACE_S // 60} 之间的分钟数", file=sys.stderr)
            return 2
        try:
            token, new, old = auth.rotate_key(kid, grace_s=(g or 0) * 60,
                                              expires_in_days=args.expires_days)
        except ServiceError as e:
            print(e.message, file=sys.stderr)
            if e.code == "journal_failed":
                print(f"原因：{e.__cause__}", file=sys.stderr)
                return 3
            return 1 if e.status == 404 else 2
        _audit(store, "apikey.rotate", old, meta={"lineage": new["lineage"], "replaced_by": new["id"],
                                                  "grace_minutes": args.grace_minutes or 0,
                                                  "expires_at": new["expires_at"]})
        if old["status"] == "grace":
            until = f"旧密钥 {old['id']} 在宽限期内继续有效，至 {_ts(old['expires_at'])}。"
        else:
            until = f"旧密钥 {old['id']} 已立即停用。"
        print(f"已创建新密钥 {new['id']}（同一密钥链 {new['lineage']}，过期：{_ts(new['expires_at'])}）。{until}")
        print("由这条密钥链提交的任务不受影响。新密钥（只显示一次，服务器只保存哈希）：\n")
        print(token)
        print()
        return 0
    return 1


def cmd_openapi(args) -> int:
    """Export the machine API's OpenAPI document.

    Built against a throwaway data directory: the document depends only on the routes, and
    building an app opens — and migrates — whatever store it is given. Exporting a spec from a
    checkout must not upgrade the schema of the database that happens to sit next to it.
    """
    import tempfile
    from synwevo_docs.api.app import create_app
    from synwevo_docs.api.v1 import openapi_subset
    with tempfile.TemporaryDirectory(prefix="synwevo-openapi-") as tmp:
        saved = {k: os.environ.get(k) for k in ("SYNWEVO_DATA_DIR", "DOCWEAVE_DATA_DIR")}
        # Anything that falls back to the environment during the build sees the same throwaway
        # directory; both spellings, because differing values are a refused configuration.
        os.environ["SYNWEVO_DATA_DIR"] = tmp
        if saved["DOCWEAVE_DATA_DIR"] is not None:
            os.environ["DOCWEAVE_DATA_DIR"] = tmp
        try:
            api = create_app(tmp, start_jobs=False)
            spec = openapi_subset(api)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    from synwevo_core.http import repo_root
    root = repo_root()
    out = Path(args.out or (root / "docs" / "integration" / "openapi.yaml"))
    out.parent.mkdir(parents=True, exist_ok=True)
    text = to_yaml(spec) if out.suffix in (".yaml", ".yml") else json.dumps(spec, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    print(f"已写出 {out}（{len(spec['paths'])} 个路径）")
    return 0


def to_yaml(obj, indent: int = 0) -> str:
    """Minimal YAML writer (no external dependency); values are quoted conservatively."""
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}\n"
        out = []
        for k, v in obj.items():
            key = _yaml_key(str(k))
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{key}:\n{to_yaml(v, indent + 1)}")
            elif isinstance(v, dict):
                out.append(f"{pad}{key}: {{}}\n")
            elif isinstance(v, list):
                out.append(f"{pad}{key}: []\n")
            else:
                out.append(f"{pad}{key}: {_yaml_scalar(v)}\n")
        return "".join(out)
    if isinstance(obj, list):
        if not obj:
            return pad + "[]\n"
        out = []
        for v in obj:
            if isinstance(v, (dict, list)) and v:
                inner = to_yaml(v, indent + 1)
                first, _, rest = inner.partition("\n")
                out.append(f"{pad}- {first.strip()}\n{rest}")
            elif isinstance(v, dict):
                out.append(f"{pad}- {{}}\n")
            elif isinstance(v, list):
                out.append(f"{pad}- []\n")
            else:
                out.append(f"{pad}- {_yaml_scalar(v)}\n")
        return "".join(out)
    return pad + _yaml_scalar(obj) + "\n"


# Plain scalars a YAML 1.1 loader (PyYAML's safe_load, and most OpenAPI tooling) resolves to
# something other than a string: booleans, null, and every number form, including the ones YAML
# 1.1 adds (hex, octal, `_` separators, base-60 `1:30`, `.inf`, `.nan`) and dates. Written
# unquoted, the response code `200` became an integer key and `version: 1.0` a float, which a
# strict OpenAPI validator rejects.
_YAML_WORDS = frozenset({"", "~", "null", "true", "false", "yes", "no", "on", "off", "y", "n", "=", "<<"})
_YAML_NUMBER = re.compile(r"""^[-+]?(
      [0-9][0-9_]*(\.[0-9_]*)?([eE][-+]?[0-9]+)?     # 1  1_000  1.0  1.  1e3
    | \.[0-9_]+([eE][-+]?[0-9]+)?                    # .5
    | 0[xX][0-9a-fA-F_]+ | 0[oO]?[0-7_]+ | 0[bB][01_]+ # hex, octal, binary
    | [0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?        # base 60
    | \.(inf|Inf|INF|nan|NaN|NAN)
)$""", re.X)
_YAML_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
_YAML_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"


def _yaml_needs_quotes(s: str) -> bool:
    return (s.lower() in _YAML_WORDS or bool(_YAML_NUMBER.match(s)) or bool(_YAML_DATE.match(s))
            or s[0] in _YAML_INDICATORS or s.strip() != s
            or any(ch in s for ch in ":#{}[]&*!|>%@`\"'\n\r\t")
            or not s.isprintable())


def _yaml_key(k: str) -> str:
    return json.dumps(k, ensure_ascii=False) if _yaml_needs_quotes(k) else k


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return ".nan"
        if math.isinf(v):
            return ".inf" if v > 0 else "-.inf"
        r = repr(v)
        mant, e, exp = r.partition("e")
        # YAML 1.1 only reads a number with a dot as a float: `1e-05` would come back a string.
        return r if "." in mant else f"{mant}.0{e}{exp}"
    s = str(v)
    # A JSON string literal is a valid YAML double-quoted scalar with the same escapes.
    return json.dumps(s, ensure_ascii=False) if _yaml_needs_quotes(s) else s


def cmd_jobs(args) -> int:
    from synwevo_docs.jobs import JobService
    from synwevo_docs.services import App
    store = Store(data_dir(args))
    app = App(store)
    svc = JobService(app, start=False)
    if args.action == "recover":
        ids = svc.recover()
        print(f"已恢复 {len(ids)} 个任务：{', '.join(ids) or '（无）'}")
        return 0
    if args.action == "list":
        for j in store.all("SELECT id, status, created_at, counts FROM jobs ORDER BY created_at DESC LIMIT 20"):
            print(f"{j['id']}\t{j['status']:<22}{_ts(j['created_at'])}\t{json.dumps(j['counts'], ensure_ascii=False)}")
        return 0
    return 1


def _project_evidence(store, row: dict) -> dict:
    """Everything known about where a project came from.  A cleanup decision must rest on
    this, never on the name — three of the test-named projects in the real database were
    edited by the user hours after any script ran."""
    pid = row["id"]
    jobs = store.all("SELECT origin, created_at FROM jobs WHERE project_id=? ORDER BY created_at", (pid,))
    origins = sorted({(j.get("origin") or "?") for j in jobs})
    srcs = store.one("SELECT COUNT(*) AS n FROM source_files WHERE project_id=? AND removed=0", (pid,))["n"]
    arts = store.one("SELECT COUNT(*) AS n FROM artifacts a JOIN jobs j ON a.job_id=j.id "
                     "WHERE j.project_id=? AND a.deleted_at IS NULL", (pid,))["n"]
    return {
        "id": pid, "name": row["name"], "origin": row.get("origin"),
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "archived": bool(row.get("archived_at")),
        "jobs": len(jobs), "job_origins": origins, "sources": srcs, "artifacts": arts,
        # Edited well after creation => a human touched it, whatever its name says.
        "edited_after_create": bool(row.get("updated_at") and row.get("created_at")
                                    and row["updated_at"] - row["created_at"] > 600),
    }


def _classify(e: dict) -> tuple[str, str]:
    """(verdict, why).  Only `test` is ever eligible for automatic action."""
    if e["origin"] and e["origin"].startswith("test:"):
        return "test", f"来源标记为 {e['origin']}"
    if e["origin"] in ("ui", None) and e["edited_after_create"]:
        return "keep", "创建后被人工修改过" + (f"（来源 {e['origin']}）" if e["origin"] else "（来源未知）")
    if e["origin"]:
        return "keep", f"来源为 {e['origin']}"
    return "unknown", "没有来源记录（v3 之前创建），无法证明是测试数据"


def cmd_projects(args) -> int:
    from synwevo_docs.services import App
    store = Store(data_dir(args))
    App(store)                       # ensures migrations have run
    rows = store.all("SELECT * FROM projects WHERE deleted=0 ORDER BY updated_at DESC")
    ev = [_project_evidence(store, r) for r in rows]
    for e in ev:
        e["verdict"], e["why"] = _classify(e)

    if args.action == "list":
        if args.json:
            print(json.dumps(ev, ensure_ascii=False, indent=1, default=str))
            return 0
        print(f"{'ID':<16}{'判定':<8}{'来源':<22}{'任务':>4}{'来源文件':>8}{'产物':>6}  名称 / 依据")
        for e in ev:
            print(f"{e['id']:<16}{e['verdict']:<8}{str(e['origin'] or '-'):<22}{e['jobs']:>4}{e['sources']:>8}{e['artifacts']:>6}  "
                  f"{e['name'][:28]}  · {e['why']}")
        n = {v: sum(1 for e in ev if e["verdict"] == v) for v in ("test", "keep", "unknown")}
        print(f"\n共 {len(ev)} 个项目：可自动处理 {n['test']}，明确保留 {n['keep']}，来源不明 {n['unknown']}")
        if n["unknown"]:
            print("来源不明的项目不会被自动归档或删除——v3 之前创建的项目没有来源记录，"
                  "只能由你逐个确认。请用 --id 明确指定。")
        return 0

    if args.action == "prune":
        targets = [e for e in ev if e["verdict"] == "test" and not e["archived"]]
        if args.id:
            wanted = set(args.id)
            chosen = [e for e in ev if e["id"] in wanted]
            missing = wanted - {e["id"] for e in chosen}
            if missing:
                print(f"找不到这些项目：{', '.join(sorted(missing))}")
                return 2
            targets = chosen                      # explicit ids bypass the verdict, on purpose
        if not targets:
            print("没有可处理的项目。（来源不明的项目不会被自动选中，请用 --id 指定。）")
            return 0

        print(f"{'将要' if args.apply else '[dry-run] 将要'}{'删除' if args.delete else '归档'} {len(targets)} 个项目：")
        for e in targets:
            print(f"  {e['id']}  {e['name'][:36]}  来源={e['origin'] or '未知'}  任务={e['jobs']} "
                  f"来源文件={e['sources']} 产物={e['artifacts']}  · {e['why']}")
        if not args.apply:
            print("\n这是 dry-run，什么都没有改动。确认无误后加 --apply。")
            return 0
        if args.delete and not args.i_understand_delete_is_permanent:
            print("\n删除是不可逆的。归档（默认）可以随时撤销。"
                  "确实要删除请同时加 --i-understand-delete-is-permanent。")
            return 2
        for e in targets:
            if args.delete:
                store.update("projects", {"id": e["id"]}, {"deleted": 1, "updated_at": now()})
            else:
                store.update("projects", {"id": e["id"]}, {"archived_at": now(), "updated_at": now()})
        print(f"\n已{'删除' if args.delete else '归档'} {len(targets)} 个项目。"
              + ("" if args.delete else "归档可以在界面「显示已归档」中撤销。"))
        return 0
    return 1


def cmd_info(args) -> int:
    d = data_dir(args)
    store = Store(d, migrate=False)          # inspect only: never upgrade the directory being looked at
    print(f"数据目录      : {Path(d).resolve()}")
    from synwevo_core.store import SCHEMA_VERSION
    ver = store.schema_version()
    if not ver:
        note = "（尚未初始化，启动服务后创建）"
    elif ver < SCHEMA_VERSION:
        note = f"（本程序为 v{SCHEMA_VERSION}，启动服务时会自动升级）"
    else:
        note = ""
    print(f"数据库        : {store.db_path}（schema v{ver}）{note}")
    print(f"监听配置      : SYNWEVO_HOST={os.environ.get('SYNWEVO_HOST', '127.0.0.1')} SYNWEVO_PORT={os.environ.get('SYNWEVO_PORT', '8765')}")
    print(f"界面鉴权      : SYNWEVO_UI_AUTH={os.environ.get('SYNWEVO_UI_AUTH', 'none')}")
    print(f"PDF 引擎      : SYNWEVO_DOCS_PDF_ENGINE={os.environ.get('SYNWEVO_DOCS_PDF_ENGINE', '(auto)')}")
    # 口令参数、MFA 种子密钥与注册策略属于账户服务：`python -m synwevo_identity.cli info`
    if ver:
        counts = {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("projects", "templates", "jobs", "artifacts", "api_keys")}
        print("数据统计      : " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return 0




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="数据目录（默认 $SYNWEVO_DATA_DIR 或仓库根目录 data/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("apikey", help="机器接口密钥")
    k.add_argument("action", choices=["create", "list", "revoke", "rotate"])
    k.add_argument("key_id", nargs="?", help="密钥 ID（不是完整密钥；完整密钥请用 --stdin）")
    k.add_argument("--name", default="api key")
    k.add_argument("--tenant", help="create 必填：密钥所属的工作空间 ID")
    k.add_argument("--scopes", default="read,write", help=f"逗号分隔，可选：{','.join(SCOPES)}")
    k.add_argument("--expires-days", type=float, default=None,
                   help="有效期（天）。rotate 不给时沿用旧密钥原来的有效期长度")
    k.add_argument("--grace-minutes", type=float, default=None,
                   help=f"rotate：旧密钥继续有效的分钟数（默认 0 = 立即停用，最多 {MAX_ROTATION_GRACE_S // 60}）")
    k.add_argument("--stdin", action="store_true",
                   help="revoke/rotate：从标准输入读入完整密钥，而不是在命令行上传密钥 ID")
    k.set_defaults(func=cmd_apikey)

    o = sub.add_parser("openapi", help="导出机器接口的 OpenAPI 文件")
    o.add_argument("--out")
    o.set_defaults(func=cmd_openapi)

    j = sub.add_parser("jobs", help="任务维护")
    j.add_argument("action", choices=["recover", "list"])
    j.set_defaults(func=cmd_jobs)

    pr = sub.add_parser("projects", help="项目清单与清理（按来源证据，默认 dry-run）")
    pr.add_argument("action", choices=["list", "prune"])
    pr.add_argument("--id", action="append", help="明确指定项目 ID（可重复）；指定后不再依据判定自动选择")
    pr.add_argument("--apply", action="store_true", help="真正执行；不加就是 dry-run")
    pr.add_argument("--delete", action="store_true", help="硬删除而不是归档（不可逆）")
    pr.add_argument("--i-understand-delete-is-permanent", action="store_true", help=argparse.SUPPRESS)
    pr.add_argument("--json", action="store_true", help="list 输出 JSON")
    pr.set_defaults(func=cmd_projects)

    i = sub.add_parser("info", help="部署信息")
    i.set_defaults(func=cmd_info)

    # Webhook subscriptions of a workspace: CLI-only administration (there is no product UI).
    from .cli_webhooks import register as register_webhooks
    register_webhooks(sub)


    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
