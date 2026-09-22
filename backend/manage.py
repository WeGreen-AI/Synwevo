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
from docweave.store import Store  # noqa: E402


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


def cmd_info(args) -> int:
    d = data_dir(args)
    store = Store(d)
    print(f"数据目录      : {Path(d).resolve()}")
    print(f"数据库        : {store.db_path}（schema v{store.schema_version()}）")
    print(f"监听配置      : DOCWEAVE_HOST={os.environ.get('DOCWEAVE_HOST', '127.0.0.1')} DOCWEAVE_PORT={os.environ.get('DOCWEAVE_PORT', '8765')}")
    print(f"界面鉴权      : DOCWEAVE_UI_AUTH={os.environ.get('DOCWEAVE_UI_AUTH', 'none')}")
    print(f"PDF 引擎      : DOCWEAVE_PDF_ENGINE={os.environ.get('DOCWEAVE_PDF_ENGINE', '(auto)')}")
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

    i = sub.add_parser("info", help="部署信息")
    i.set_defaults(func=cmd_info)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
