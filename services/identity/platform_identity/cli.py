"""账户服务运维 CLI：首位管理员、应用与使用资格、OIDC 客户端与签名密钥。

    python -m platform_identity.cli bootstrap token
    python -m platform_identity.cli ui-user add <name> [--password ...]
    python -m platform_identity.cli app list|enable|disable|grant|revoke
    python -m platform_identity.cli client register --client-id portal-web --app platform \
        --name 平台门户 --redirect http://portal.localhost:8769/auth/callback
    python -m platform_identity.cli client list|show|disable
    python -m platform_identity.cli keys list|rotate|retire            # OIDC 签名密钥
    python -m platform_identity.cli info                               # 账户侧配置

刻意只有 CLI。应用注册表是平台配置，不是产品功能；一个能把应用授权给工作空间的网页，就是一个能
授予数据访问权的网页。

数据目录默认取 `$DOCWEAVE_DATA_DIR`，再退回仓库根目录的 `data/`——和业务应用同一个库，因为账户
权威只能有一个。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from platform_core.auth import Auth
from platform_core.errors import ServiceError
from platform_core.http import data_dir_default
from platform_core.identity import Identity, registration_mode
from platform_core.passwords import PasswordError
from platform_core.store import Store, now


def data_dir(args) -> str:
    return args.data or os.environ.get("DOCWEAVE_DATA_DIR") or str(data_dir_default())


def _ts(v) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"


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


def _platform(args):
    store = Store(data_dir(args))
    ident = Identity(store)
    from platform_core.apps import Apps
    from platform_core import oidc
    return store, ident, Apps(store, ident), oidc


def cmd_app(args) -> int:
    """Applications and who may use them.

    Deliberately CLI-only. An application registry is platform configuration, not a product
    feature, and a web page that could enable an application for a workspace would be a web page
    that could grant access to data.
    """
    store, ident, apps, _ = _platform(args)
    if args.action == "list":
        rows = store.all("SELECT * FROM apps ORDER BY name")
        if not rows:
            print("（没有已登记的应用）")
        for a in rows:
            n = store.one("SELECT COUNT(*) AS n FROM workspace_apps WHERE app_id=?", (a["id"],))["n"]
            print(f"{a['id']:<16} {a['name']:<16} {a['status']:<9} 策略={a['access_policy']:<9} "
                  f"已启用空间={n}  {a['launch_url']}")
        return 0
    if args.action in ("enable", "disable") and not args.workspace:
        # Enabling/disabling the application itself, platform-wide.
        if not apps.get(args.app):
            print(f"没有名为 {args.app} 的应用", file=sys.stderr)
            return 2
        store.execute("UPDATE apps SET status=?, updated_at=? WHERE id=?",
                      ("active" if args.action == "enable" else "disabled", now(), args.app))
        print(f"应用 {args.app} 已{'启用' if args.action == 'enable' else '停用'}")
        return 0
    if not args.workspace:
        print("需要 --workspace", file=sys.stderr)
        return 2
    ws = ident.get_workspace(args.workspace)
    if not ws:
        print(f"没有 id 为 {args.workspace} 的工作空间", file=sys.stderr)
        return 2
    if ws["kind"] == "legacy":
        # The legacy workspace holds every pre-identity file and has zero members on purpose.
        print("workspace default 是待认领的历史数据空间，不能在这里直接授权；请使用认领流程。",
              file=sys.stderr)
        return 2
    if args.action == "enable":
        apps.enable_for_workspace(args.workspace, args.app)
        print(f"工作空间 {args.workspace} 已启用 {args.app}")
    elif args.action == "disable":
        store.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                      (args.workspace, args.app))
        print(f"工作空间 {args.workspace} 已停用 {args.app}")
    elif args.action in ("grant", "revoke"):
        if not args.user:
            print("需要 --user（用户 ID 或邮箱）", file=sys.stderr)
            return 2
        u = ident.get_user(args.user) or ident.by_email(args.user)
        if not u:
            print(f"找不到用户 {args.user}", file=sys.stderr)
            return 2
        (apps.grant if args.action == "grant" else apps.revoke)(args.workspace, args.app, u["id"])
        print(f"{u['email']} 在 {args.workspace} 的 {args.app} 使用资格已"
              f"{'授予' if args.action == 'grant' else '撤销'}")
    return 0


def cmd_client(args) -> int:
    """OIDC client registrations.

    There is no dynamic client registration endpoint and there will not be one: anything that
    lets an unauthenticated caller create a client with its own redirect URI is a way to have
    authorization codes delivered somewhere else.
    """
    store, ident, apps, oidc = _platform(args)
    clients = oidc.Clients(store)
    if args.action == "list":
        rows = store.all("SELECT * FROM oauth_clients ORDER BY created_at")
        if not rows:
            print("（没有已登记的客户端）")
        for c in rows:
            state = "停用" if c["disabled_at"] else "启用"
            kind = "服务账户" if c["kind"] == "service" else "用户客户端"
            extra = f"  空间={c['workspace_id']}  权限={c['api_scopes'] or '无'}" \
                if c["kind"] == "service" else f"  PKCE={'必需' if c['require_pkce'] else '否'}"
            print(f"{c['client_id']:<18} app={c['app_id']:<12} {kind:<6} {state}  "
                  f"认证={c['token_endpoint_auth_method']}{extra}")
        return 0
    if args.action == "show":
        c = clients.get(args.client_id)
        if not c:
            print("找不到该客户端", file=sys.stderr)
            return 2
        c.pop("secret_hash", None)                        # never printed, not even hashed
        print(json.dumps(c, ensure_ascii=False, indent=2))
        return 0
    if args.action == "disable":
        n = store.execute("UPDATE oauth_clients SET disabled_at=? WHERE client_id=? AND disabled_at IS NULL",
                          (now(), args.client_id))
        print(f"{'已停用' if n else '该客户端不存在或已停用'} {args.client_id}")
        return 0

    # register
    service = bool(getattr(args, "service", False))
    if not args.redirect and not service:
        print("至少需要一个 --redirect", file=sys.stderr)
        return 2
    if service and args.workspace and not ident.get_workspace(args.workspace):
        print(f"找不到工作空间 {args.workspace}", file=sys.stderr)
        return 2
    try:
        row, secret = clients.register(
            client_id=args.client_id, app_id=args.app, name=args.name or args.client_id,
            # A service account has no browser and therefore no callback. Requiring one would
            # mean inventing a URL that nothing ever visits.
            redirect_uris=list(args.redirect or []), scopes=args.scopes,
            audiences=list(args.audience or []),
            confidential=bool(args.confidential) or service,
            post_logout_redirect_uris=list(args.post_logout or []),
            backchannel_logout_uri=args.backchannel,
            kind="service" if service else "user",
            workspace_id=args.workspace if service else None,
            api_scopes=args.api_scopes or "")
    except ServiceError as e:
        print(f"登记失败：{e.message}", file=sys.stderr)
        return 2
    print(f"client_id      : {row['client_id']}")
    print(f"应用           : {row['app_id']}")
    print(f"类型           : {'服务账户（机器身份）' if service else '用户客户端'}")
    if service:
        print(f"工作空间       : {row['workspace_id']}")
        print(f"接口权限       : {row['api_scopes'] or '（无）'}")
    else:
        print(f"回调地址       : {', '.join(row['redirect_uris'])}")
    print(f"认证方式       : {row['token_endpoint_auth_method']}")
    if secret:
        print(f"client_secret  : {secret}")
        print("这是唯一一次显示；只保存了它的哈希。写入服务的环境变量，不要提交到仓库。")
    else:
        print("公共客户端：没有密钥，安全性由 PKCE(S256) 与精确回调匹配提供。")
    return 0


def cmd_keys(args) -> int:
    """OIDC signing keys. Persisted on purpose — a key regenerated at startup would invalidate
    every issued token on restart, which presents as a mass logout with no cause."""
    store, ident, apps, oidc = _platform(args)
    keys = oidc.Keys(store)
    if args.action == "list":
        for k in store.all("SELECT kid,alg,status,created_at,retired_at FROM oidc_keys ORDER BY created_at"):
            print(f"{k['kid']:<18} {k['alg']}  {k['status']:<9} 创建 {_ts(k['created_at'])}  "
                  f"退役 {_ts(k['retired_at'])}")
        return 0
    if args.action == "rotate":
        kid, retiring = keys.rotate()
        print(f"新的签名密钥：{kid}")
        if retiring:
            print(f"{retiring} 个旧密钥转为 retiring，仍在 JWKS 中发布，因此此前签发的令牌继续可验证。")
            print("等待最长的令牌有效期过去之后，再执行 keys retire <kid>。")
        else:
            print("此前没有活动密钥，这是第一把；没有需要保留验证的旧令牌。")
        return 0
    if args.action == "retire":
        if not args.kid:
            print("需要 --kid", file=sys.stderr)
            return 2
        keys.retire(args.kid)
        print(f"{args.kid} 已退役，不再发布；用它签发的令牌从此验证失败。")
        return 0
    return 0




def cmd_info(args) -> int:
    """What the account side of this deployment is configured to do."""
    d = data_dir(args)
    store = Store(d, migrate=False)          # inspect only: never upgrade the directory being looked at
    print(f"数据目录      : {Path(d).resolve()}")
    print(f"数据库        : {store.db_path}（schema v{store.schema_version()}）")
    print(f"发行者        : {os.environ.get('DOCWEAVE_ISSUER', '(未设置)')}")
    print(f"角色          : DOCWEAVE_ROLE={os.environ.get('DOCWEAVE_ROLE', 'all')}")
    print(f"注册策略      : {registration_mode()}")
    from platform_core.passwords import parameters as pw_params
    from .mfa import key_is_external
    pp = pw_params()
    print(f"口令哈希      : argon2id m={pp['memory_kib']}KiB t={pp['time_cost']} p={pp['parallelism']}，并发上限 {pp['concurrency']}")
    # Where this key lives decides whether encrypting TOTP seeds helps against a stolen database,
    # so it is printed rather than left for someone to infer from the absence of a warning.
    print("MFA 种子密钥  : " + ("来自 DOCWEAVE_SECRET（与数据分离）" if key_is_external()
                                else "取自库内 meta.server_secret —— 与密文同库，"
                                     "拿到数据库副本即可解出 TOTP 种子；建议设置 DOCWEAVE_SECRET"))
    if store.schema_version():
        counts = {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                  for t in ("users", "workspaces", "memberships", "app_entitlements", "sessions", "oauth_clients")}
        print("账户统计      : " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="数据目录（默认 $DOCWEAVE_DATA_DIR 或仓库根目录 data/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    a = sub.add_parser("app", help="平台应用与使用资格（无产品界面，仅 CLI）")
    a.add_argument("action", choices=["list", "enable", "disable", "grant", "revoke"])
    a.add_argument("app", nargs="?", default="docweave", help="应用 ID，默认 docweave")
    a.add_argument("--workspace", help="工作空间 ID；省略时 enable/disable 作用于应用本身")
    a.add_argument("--user", help="用户 ID 或邮箱（grant/revoke）")
    a.set_defaults(func=cmd_app)

    cl = sub.add_parser("client", help="OIDC 客户端登记（无动态注册端点）")
    cl.add_argument("action", choices=["register", "list", "show", "disable"])
    cl.add_argument("--client-id", dest="client_id", help="客户端 ID")
    cl.add_argument("--app", default="docweave", help="所属应用 ID")
    cl.add_argument("--name", help="显示名称")
    cl.add_argument("--redirect", action="append", help="回调地址，可重复；精确匹配，无通配符")
    cl.add_argument("--post-logout", dest="post_logout", action="append", help="登出后跳转地址，可重复")
    cl.add_argument("--backchannel", help="后通道登出接收地址")
    cl.add_argument("--audience", action="append", help="该客户端可获得令牌的 API 受众，可重复")
    cl.add_argument("--scopes", default="openid profile email")
    cl.add_argument("--confidential", action="store_true", help="机密客户端：生成一次性 client_secret")
    cl.add_argument("--service", action="store_true",
                    help="登记为服务账户（机器身份）：必须机密，必须绑定一个工作空间")
    cl.add_argument("--workspace", help="服务账户所属工作空间 ID")
    cl.add_argument("--api-scopes", dest="api_scopes", default="",
                    help="接口权限，空格分隔：docweave.read docweave.write")
    cl.set_defaults(func=cmd_client)

    ky = sub.add_parser("keys", help="OIDC 签名密钥")
    ky.add_argument("action", choices=["list", "rotate", "retire"])
    ky.add_argument("--kid", help="retire 时指定")
    ky.set_defaults(func=cmd_keys)


    i = sub.add_parser("info", help="账户侧配置")
    i.set_defaults(func=cmd_info)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
