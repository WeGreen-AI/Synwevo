"""DocWeave operations CLI: API keys, UI users, OpenAPI export, maintenance.

    python backend/manage.py apikey create --name ci --tenant default --scopes read,write
    python backend/manage.py apikey list
    python backend/manage.py apikey revoke <id>
    python backend/manage.py apikey rotate <id>            # new key, old one revoked
    python backend/manage.py ui-user add <name> [--password ...]
    python backend/manage.py ui-user list|remove <name>
    python backend/manage.py openapi --out docs/integration/openapi.yaml
    python backend/manage.py jobs recover                  # resume jobs left by a stopped worker
    python backend/manage.py info                          # data dir, schema version, ports in use

The database is the one in DOCWEAVE_DATA_DIR (default ./data).  A key's secret is shown
once, at creation; only its hash is stored.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from docweave.auth import SCOPES, Auth  # noqa: E402
from docweave.identity import Identity, registration_mode  # noqa: E402
from docweave.passwords import PasswordError  # noqa: E402
from docweave.services import ServiceError  # noqa: E402
from docweave.store import Store, now  # noqa: E402


def data_dir(args) -> str:
    return args.data or os.environ.get("DOCWEAVE_DATA_DIR") or str(HERE.parent / "data")


def _ts(v) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"


def cmd_apikey(args) -> int:
    store = Store(data_dir(args))
    auth = Auth(store)
    if args.action == "create":
        scopes = tuple(s.strip() for s in (args.scopes or "read,write").split(",") if s.strip())
        token, row = auth.create_key(args.name, args.tenant, scopes, args.expires_days)
        print(f"密钥 ID : {row['id']}")
        print(f"租户    : {row['tenant']}")
        print(f"权限    : {row['scopes']}")
        print(f"过期    : {_ts(row['expires_at'])}")
        print("\n请立即保存下面这行（只显示一次，服务器只保存哈希）：\n")
        print(token)
        print("\n用法：Authorization: Bearer <上面的密钥>")
        return 0
    if args.action == "list":
        rows = auth.list_keys()
        if not rows:
            print("还没有 API 密钥。用 `apikey create` 创建。")
            return 0
        print(f"{'ID':<14}{'名称':<18}{'租户':<12}{'权限':<14}{'创建':<18}{'最近使用':<18}状态")
        for r in rows:
            state = "已撤销" if r["revoked_at"] else ("已过期" if r["expires_at"] and r["expires_at"] < time.time() else "有效")
            print(f"{r['id']:<14}{(r['name'] or '')[:16]:<18}{r['tenant']:<12}{r['scopes']:<14}{_ts(r['created_at']):<18}{_ts(r['last_used_at']):<18}{state}")
        return 0
    if args.action == "revoke":
        ok = auth.revoke_key(args.key_id)
        print("已撤销" if ok else "未找到该密钥（或已撤销）")
        return 0 if ok else 1
    if args.action == "rotate":
        old = next((k for k in auth.list_keys() if k["id"] == args.key_id), None)
        if not old:
            print("未找到该密钥")
            return 1
        token, row = auth.create_key(old["name"], old["tenant"], tuple(old["scopes"].split(",")), args.expires_days)
        auth.revoke_key(old["id"])
        print(f"已创建新密钥 {row['id']} 并撤销旧密钥 {old['id']}。新密钥（只显示一次）：\n\n{token}\n")
        return 0
    return 1


def cmd_bootstrap(args) -> int:
    """Create the first platform administrator.

    Deliberately a CLI action by whoever controls the deployment, not a web page: the first
    person to reach a public URL must not be able to make themselves an administrator. There is
    no default password in this product, so the account is created here with one the operator
    chooses, or via a single-use token they hand to a browser.
    """
    store = Store(data_dir(args))
    ident = Identity(store)

    if args.action == "status":
        admins = ident.platform_admins()
        print(f"平台管理员：{len(admins)} 名" + ("（需要引导）" if not admins else ""))
        for a in admins:
            print(f"  {a['email']}\t{a['id']}\t{_ts(a['created_at'])}")
        print(f"注册模式：{registration_mode()}（DOCWEAVE_REGISTRATION）")
        return 0

    if args.action == "token":
        if ident.platform_admins() and not args.force:
            print("已存在平台管理员；如确需再次引导，请加 --force（旧令牌会失效）")
            return 1
        raw = ident.create_bootstrap_token(ttl_s=args.ttl)
        print("一次性引导令牌（只显示一次，%d 秒后过期）：\n\n%s\n" % (args.ttl, raw))
        print("用法：在浏览器打开服务后按提示粘贴该令牌创建首位管理员。")
        print("说明：单次使用；生成新令牌会使旧令牌立即失效；过期后需重新生成。")
        return 0

    if args.action == "admin":
        if not args.email:
            print("需要 --email")
            return 1
        if ident.platform_admins() and not args.force:
            print("已存在平台管理员；如确需再加一名，请加 --force")
            return 1
        pw = args.password or getpass.getpass(f"为 {args.email} 设置口令：")
        if not args.password:
            again = getpass.getpass("再输入一次：")
            if pw != again:
                print("两次输入不一致")
                return 1
        try:
            u = ident.create_user(args.email, pw, display_name=args.name,
                                  status="active", platform_role="admin")
        except (ServiceError, PasswordError) as e:
            print(f"未创建：{getattr(e, 'message', str(e))}")
            return 1
        print(f"已创建平台管理员 {u['email']}（{u['id']}）。")
        print("该账户同时拥有一个个人工作空间；历史数据仍在「历史数据（待认领）」空间中，")
        print("需要在管理后台显式指派后才可访问。")
        return 0
    return 1


def cmd_ui_user(args) -> int:
    store = Store(data_dir(args))
    auth = Auth(store)
    if args.action == "add":
        pw = args.password or getpass.getpass(f"为 {args.username} 设置口令：")
        if len(pw) < 8:
            print("口令至少 8 个字符")
            return 1
        auth.add_ui_user(args.username, pw)
        print(f"已添加界面用户 {args.username}。设置 DOCWEAVE_UI_AUTH=basic 后生效。")
        return 0
    if args.action == "list":
        for u in auth.list_ui_users():
            print(f"{u['username']}\t{_ts(u['created_at'])}")
        return 0
    if args.action == "remove":
        ok = auth.remove_ui_user(args.username)
        print("已删除" if ok else "未找到该用户")
        return 0 if ok else 1
    return 1


def cmd_openapi(args) -> int:
    os.environ.setdefault("DOCWEAVE_DATA_DIR", data_dir(args))
    from docweave.api.app import create_app
    from docweave.api.v1 import openapi_subset
    api = create_app(data_dir(args), start_jobs=False)
    spec = openapi_subset(api)
    out = Path(args.out or (HERE.parent / "docs" / "integration" / "openapi.yaml"))
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


def _yaml_key(k: str) -> str:
    return f'"{k}"' if any(ch in k for ch in ':{}[]#&*!|>%@`"\'') or k.strip() != k else k


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(ch in s for ch in ":#{}[]&*!|>%@`\"'\n") or s.strip() != s or s.lower() in ("true", "false", "null", "yes", "no"):
        return json.dumps(s, ensure_ascii=False)
    return s


def cmd_jobs(args) -> int:
    from docweave.jobs import JobService
    from docweave.services import App
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
    from docweave.services import App
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
    from docweave.store import SCHEMA_VERSION
    ver = store.schema_version()
    if not ver:
        note = "（尚未初始化，启动服务后创建）"
    elif ver < SCHEMA_VERSION:
        note = f"（本程序为 v{SCHEMA_VERSION}，启动服务时会自动升级）"
    else:
        note = ""
    print(f"数据库        : {store.db_path}（schema v{ver}）{note}")
    print(f"监听配置      : DOCWEAVE_HOST={os.environ.get('DOCWEAVE_HOST', '127.0.0.1')} DOCWEAVE_PORT={os.environ.get('DOCWEAVE_PORT', '8765')}")
    print(f"界面鉴权      : DOCWEAVE_UI_AUTH={os.environ.get('DOCWEAVE_UI_AUTH', 'none')}")
    print(f"PDF 引擎      : DOCWEAVE_PDF_ENGINE={os.environ.get('DOCWEAVE_PDF_ENGINE', '(auto)')}")
    from docweave.mfa import key_is_external
    from docweave.passwords import parameters as pw_params
    pp = pw_params()
    print(f"口令哈希      : argon2id m={pp['memory_kib']}KiB t={pp['time_cost']} p={pp['parallelism']}，并发上限 {pp['concurrency']}")
    # Where this key lives decides whether encrypting TOTP seeds helps against a stolen database,
    # so it is printed rather than left for someone to infer from the absence of a warning.
    print("MFA 种子密钥  : " + ("来自 DOCWEAVE_SECRET（与数据分离）" if key_is_external()
                                else "取自库内 meta.server_secret —— 与密文同库，"
                                     "拿到数据库副本即可解出 TOTP 种子；建议设置 DOCWEAVE_SECRET"))
    if ver:
        counts = {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("projects", "templates", "jobs", "artifacts", "api_keys")}
        print("数据统计      : " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="数据目录（默认 $DOCWEAVE_DATA_DIR 或 ./data）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("apikey", help="机器接口密钥")
    k.add_argument("action", choices=["create", "list", "revoke", "rotate"])
    k.add_argument("key_id", nargs="?")
    k.add_argument("--name", default="api key")
    k.add_argument("--tenant", default="default", help="资源归属（default = 与界面共享）")
    k.add_argument("--scopes", default="read,write", help=f"逗号分隔，可选：{','.join(SCOPES)}")
    k.add_argument("--expires-days", type=float, default=None)
    k.set_defaults(func=cmd_apikey)

    b = sub.add_parser("bootstrap", help="建立首位平台管理员（无默认口令）")
    b.add_argument("action", choices=["status", "token", "admin"])
    b.add_argument("--email", help="admin：管理员邮箱")
    b.add_argument("--name", help="admin：显示名称")
    b.add_argument("--password", help="admin：口令（省略则交互输入，不回显）")
    b.add_argument("--ttl", type=int, default=900, help="token：有效期秒数（默认 900）")
    b.add_argument("--force", action="store_true", help="已有管理员时仍继续")
    b.set_defaults(func=cmd_bootstrap)

    u = sub.add_parser("ui-user", help="界面 HTTP Basic 用户")
    u.add_argument("action", choices=["add", "list", "remove"])
    u.add_argument("username", nargs="?")
    u.add_argument("--password")
    u.set_defaults(func=cmd_ui_user)

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

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
