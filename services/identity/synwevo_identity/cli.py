"""账户服务运维 CLI：首位管理员、应用与使用资格、OIDC 客户端与签名密钥。

    python -m synwevo_identity.cli bootstrap token
    python -m synwevo_identity.cli ui-user add <name> [--password ...]
    python -m synwevo_identity.cli app list|enable|disable|grant|revoke
    python -m synwevo_identity.cli client register --client-id portal-web --app platform \
        --name 平台门户 --redirect http://portal.localhost:8769/auth/callback
    python -m synwevo_identity.cli client list|show|disable|enable|revoke-tokens <client_id>
    python -m synwevo_identity.cli client rotate-secret <client_id> [--grace-minutes N]
    python -m synwevo_identity.cli keys list|rotate|retire            # OIDC 签名密钥
    python -m synwevo_identity.cli secret status|rekey [--to env|db]  # 服务端根密钥与已加密数据
    python -m synwevo_identity.cli info                               # 账户侧配置

每一个会改变状态的命令都写一条审计记录（actor_kind=system，meta 里记下 via=cli、操作系统用户与
主机名）；撤销访问的命令（停用客户端、轮换密钥、撤销使用资格）同时写入安全日志，恢复备份后会被
重新应用。

刻意只有 CLI。应用注册表是平台配置，不是产品功能；一个能把应用授权给工作空间的网页，就是一个能
授予数据访问权的网页。

数据目录默认取 `$SYNWEVO_DATA_DIR`，再退回仓库根目录的 `data/`——和业务应用同一个库，因为账户
权威只能有一个。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import time
from pathlib import Path

from synwevo_core import env
from synwevo_core.auth import Auth
from synwevo_core.errors import ServiceError
from synwevo_core.http import data_dir_default
from synwevo_core.identity import Identity, registration_mode
from synwevo_core.passwords import PasswordError
from synwevo_core.store import Store, now


def data_dir(args) -> str:
    return args.data or env.get("DATA_DIR") or str(data_dir_default())


def _ts(v) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"


def _audit(store: Store, action: str, *, object_kind: str | None = None, object_id: str | None = None,
           workspace_id: str | None = None, result: str = "ok", meta: dict | None = None) -> None:
    """Record a privileged CLI action in the same audit log the admin console reads.

    Operators act here with no session and no account, so the actor is `system` and the meta
    says who ran it from where. Without this, creating, rotating and disabling machine
    credentials or granting application access left no trace at all. Metadata only: no secret,
    hash or token is ever written.
    """
    from synwevo_core.store import new_id
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001 - some containers have no passwd entry for the uid
        who = str(os.getuid()) if hasattr(os, "getuid") else "unknown"
    store.execute(
        "INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
        "result,request_id,ip,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("ae"), time.time(), "system", None, workspace_id, action, object_kind, object_id, result,
         None, None, json.dumps({"via": "cli", "os_user": who, "host": socket.gethostname(), **(meta or {})},
                                ensure_ascii=False)))


def _journal(store: Store, kind: str, **fields) -> bool:
    from .withdrawals import record
    ok = record(store, kind, **fields)
    if not ok:
        print(f"警告：安全日志写入失败（{kind}）。撤销已经生效，但从备份恢复后可能需要重新执行。",
              file=sys.stderr)
    return ok


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
        print(f"可用的最高管理员：{len(admins)} 名" + ("（需要引导）" if not admins else ""))
        for a in admins:
            print(f"  {a['email']}\t{a['id']}\t{_ts(a['created_at'])}")
        print(f"注册模式：{registration_mode(store)}（持久策略优先于 SYNWEVO_REGISTRATION）")
        return 0

    if args.action == "token":
        if ident.platform_admins():
            # The web endpoint refuses every token once an administrator exists, so a token
            # printed here would be one nobody can use. `--force` used to print it anyway.
            print("已存在平台管理员，引导令牌在网页上不会再被接受。"
                  "如确需再加一名管理员，请用：bootstrap admin --email ... --force", file=sys.stderr)
            return 1
        raw = ident.create_bootstrap_token(ttl_s=args.ttl)
        _audit(store, "cli.bootstrap.token", meta={"ttl_s": args.ttl})
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
            u = ident.create_user(args.email, pw, display_name=args.name, status="active")
        except (ServiceError, PasswordError) as e:
            print(f"未创建：{getattr(e, 'message', str(e))}")
            return 1
        # A super administrator grant under host authority, in one transaction with its audit
        # line: whoever runs this command controls the deployment (platform_access.HOST_SOURCES).
        from synwevo_core.platform_access import SUPER_ADMIN, PlatformAccess
        with store.tx():
            PlatformAccess(store).host_grant(u["id"], SUPER_ADMIN, source="bootstrap:cli",
                                             reason="命令行 bootstrap admin 创建的最高管理员")
            _audit(store, "cli.bootstrap.admin", object_kind="user", object_id=u["id"],
                   meta={"email": u["email"], "forced": bool(args.force), "role": SUPER_ADMIN})
        print(f"已创建最高管理员 {u['email']}（{u['id']}）。")
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
        _audit(store, "cli.ui_user.add", object_kind="ui_user", object_id=args.username)
        print(f"已添加界面用户 {args.username}。设置 SYNWEVO_UI_AUTH=basic 后生效。")
        return 0
    if args.action == "list":
        for u in auth.list_ui_users():
            print(f"{u['username']}\t{_ts(u['created_at'])}")
        return 0
    if args.action == "remove":
        ok = auth.remove_ui_user(args.username)
        if ok:
            _audit(store, "cli.ui_user.remove", object_kind="ui_user", object_id=args.username)
        print("已删除" if ok else "未找到该用户")
        return 0 if ok else 1
    return 1


def _platform(args):
    store = Store(data_dir(args))
    ident = Identity(store)
    from synwevo_core.apps import Apps
    from synwevo_core import oidc
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
        _audit(store, f"cli.app.{args.action}", object_kind="app", object_id=args.app)
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
        _audit(store, "cli.app.workspace_enable", object_kind="app", object_id=args.app,
               workspace_id=args.workspace)
        print(f"工作空间 {args.workspace} 已启用 {args.app}")
    elif args.action == "disable":
        store.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                      (args.workspace, args.app))
        _audit(store, "cli.app.workspace_disable", object_kind="app", object_id=args.app,
               workspace_id=args.workspace)
        print(f"工作空间 {args.workspace} 已停用 {args.app}")
    elif args.action in ("grant", "revoke"):
        if not args.user:
            print("需要 --user（用户 ID 或邮箱）", file=sys.stderr)
            return 2
        u = ident.get_user(args.user) or ident.by_email(args.user)
        if not u:
            print(f"找不到用户 {args.user}", file=sys.stderr)
            return 2
        if args.action == "grant":
            apps.grant(args.workspace, args.app, u["id"])
        else:
            # The same explicit-revocation semantics as the workspace governance API: a revoked
            # row even when none existed, and the person's tokens for this app in this workspace
            # revoked with it.
            if not ident.membership(args.workspace, u["id"]):
                print(f"{u['email']} 不是工作空间 {args.workspace} 的成员", file=sys.stderr)
                return 2
            try:
                ident.set_entitlement(args.workspace, u["id"], args.app, grant=False)
            except ServiceError as e:
                print(f"未撤销：{e.message}", file=sys.stderr)
                return 2
            _journal(store, "entitlement_revoked", workspace_id=args.workspace, app_id=args.app,
                     user_id=u["id"])
        _audit(store, f"cli.app.{args.action}", object_kind="entitlement", object_id=u["id"],
               workspace_id=args.workspace, meta={"app_id": args.app})
        print(f"{u['email']} 在 {args.workspace} 的 {args.app} 使用资格已"
              f"{'授予' if args.action == 'grant' else '撤销'}")
    return 0


def cmd_client(args) -> int:
    """OIDC client registrations and their credential lifecycle.

    There is no dynamic client registration endpoint and there will not be one: anything that
    lets an unauthenticated caller create a client with its own redirect URI is a way to have
    authorization codes delivered somewhere else.
    """
    store, ident, apps, oidc = _platform(args)
    clients = oidc.Clients(store)
    cid = args.client_id or args.target
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
    if not cid:
        print("需要客户端 ID（位置参数或 --client-id）", file=sys.stderr)
        return 2
    if args.action == "show":
        c = clients.get_any(cid)
        if not c:
            print("找不到该客户端", file=sys.stderr)
            return 2
        c.pop("secret_hash", None)                        # never printed, not even hashed
        c["previous_secret_valid_until"] = clients.grace_until(cid)
        print(json.dumps(c, ensure_ascii=False, indent=2))
        return 0
    if args.action == "disable":
        if not clients.disable(cid):
            print(f"该客户端不存在或已停用：{cid}", file=sys.stderr)
            return 1
        # Its tokens go with it. A disabled client whose access tokens kept working until they
        # expired would be "disabled" in name only for up to a quarter of an hour.
        n = oidc.Grants(store).revoke_for_client(cid, "client_disabled")
        _journal(store, "client_disabled", client_id=cid)
        _audit(store, "cli.client.disable", object_kind="client", object_id=cid, meta={"tokens_revoked": n})
        print(f"已停用 {cid}，撤销了 {n} 个未过期的令牌。")
        return 0
    if args.action == "enable":
        if not clients.enable(cid):
            print(f"该客户端不存在或并未停用：{cid}", file=sys.stderr)
            return 1
        _audit(store, "cli.client.enable", object_kind="client", object_id=cid)
        print(f"已重新启用 {cid}。停用期间撤销的令牌不会恢复；机密客户端的密钥未变，如有泄露请再执行 rotate-secret。")
        return 0
    if args.action == "revoke-tokens":
        if not clients.get_any(cid):
            print("找不到该客户端", file=sys.stderr)
            return 2
        n = oidc.Grants(store).revoke_for_client(cid, "operator_revoked")
        _journal(store, "tokens_revoked", client_id=cid)
        _audit(store, "cli.client.revoke_tokens", object_kind="client", object_id=cid, meta={"tokens_revoked": n})
        print(f"已撤销 {cid} 的 {n} 个令牌。")
        return 0
    if args.action == "rotate-secret":
        grace_s = max(0, int(args.grace_minutes or 0)) * 60
        if grace_s > oidc.MAX_SECRET_GRACE_S:
            print(f"--grace-minutes 最多 {oidc.MAX_SECRET_GRACE_S // 60} 分钟", file=sys.stderr)
            return 2
        try:
            out = clients.rotate_secret(cid, grace_s=grace_s)
        except ServiceError as e:
            print(f"轮换失败：{e.message}", file=sys.stderr)
            return 2
        revoked = 0
        if not grace_s:
            # No grace period means "the old secret may be in someone else's hands": whatever it
            # already obtained is ended too, not left to run out its lifetime.
            revoked = oidc.Grants(store).revoke_for_client(cid, "secret_rotated")
        _journal(store, "client_secret_rotated", client_id=cid, secret_hash=out["secret_hash"])
        _audit(store, "cli.client.rotate_secret", object_kind="client", object_id=cid,
               meta={"grace_s": grace_s, "tokens_revoked": revoked})
        print(f"client_id      : {cid}")
        print(f"client_secret  : {out['secret']}")
        print("这是唯一一次显示；库里只保存了它的哈希。写入服务的环境变量，不要提交到仓库。")
        if grace_s:
            print(f"旧密钥在 {_ts(out['grace_until'])} 之前仍然有效，请在此之前完成替换；"
                  "如果旧密钥已经泄露，请立即再执行一次不带 --grace-minutes 的 rotate-secret。")
        else:
            print(f"旧密钥立即失效，已撤销用它换到的 {revoked} 个令牌。")
        return 0

    # register
    service = bool(getattr(args, "service", False))
    if not args.redirect and not service:
        print("至少需要一个 --redirect", file=sys.stderr)
        return 2
    if service and args.workspace and not ident.get_workspace(args.workspace):
        print(f"找不到工作空间 {args.workspace}", file=sys.stderr)
        return 2
    existed = bool(clients.get_any(cid))
    try:
        row, secret = clients.register(
            client_id=cid, app_id=args.app, name=args.name or cid,
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
    _audit(store, "cli.client.update" if existed else "cli.client.register", object_kind="client",
           object_id=row["client_id"],
           meta={"app_id": row["app_id"], "kind": row["kind"], "workspace_id": row["workspace_id"],
                 "scopes": row["scopes"], "api_scopes": row["api_scopes"], "audiences": row["audiences"],
                 "secret_issued": bool(secret)})
    print(f"client_id      : {row['client_id']}")
    print(f"应用           : {row['app_id']}")
    print(f"类型           : {'服务账户（机器身份）' if service else '用户客户端'}")
    if service:
        print(f"工作空间       : {row['workspace_id']}")
        print(f"接口权限       : {row['api_scopes'] or '（无）'}")
    else:
        print(f"回调地址       : {', '.join(row['redirect_uris'])}")
    if row["audiences"]:
        print(f"令牌受众       : {', '.join(row['audiences'])}")
    print(f"认证方式       : {row['token_endpoint_auth_method']}")
    if secret:
        print(f"client_secret  : {secret}")
        print("这是唯一一次显示；只保存了它的哈希。写入服务的环境变量，不要提交到仓库。")
    elif row["token_endpoint_auth_method"] != "none":
        # Said explicitly: re-registering used to print a fresh secret that was never stored.
        print("已更新登记信息；原有 client_secret 保持不变。需要新密钥请执行 client rotate-secret。")
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
        _audit(store, "cli.keys.rotate", object_kind="signing_key", object_id=kid, meta={"retiring": retiring})
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
        _audit(store, "cli.keys.retire", object_kind="signing_key", object_id=args.kid)
        print(f"{args.kid} 已退役，不再发布；用它签发的令牌从此验证失败。")
        return 0
    return 0


def _roots(store: Store) -> tuple[bytes | None, bytes | None]:
    """(root generated into the database, root from SYNWEVO_SECRET). Read directly, never through
    `Auth.secret()`, which refuses to hand out a root that does not match the sealed data — the
    very situation a rekey exists to resolve."""
    row = store.one("SELECT value FROM meta WHERE key='server_secret'")
    external = env.get("SECRET")
    return (bytes.fromhex(row["value"]) if row else None), (external.encode() if external else None)


def cmd_secret(args) -> int:
    """The server root secret and the data sealed under it (TOTP seeds, webhook signing secrets).

    `rekey` re-seals everything from one root to the other in one transaction. It refuses when
    the old root does not open the data: guessing which root a row was written with is how a
    database ends up half-readable.
    """
    from synwevo_core import sealed
    store = Store(data_dir(args))
    db_root, env_root = _roots(store)
    kcv = store.one("SELECT value FROM meta WHERE key='root_kcv'")
    kcv = kcv["value"] if kcv else None
    counts = {f"{t}.{c}": store.one(f"SELECT COUNT(*) AS n FROM {t} WHERE {c} IS NOT NULL")["n"]
              for t, _, c, _ in sealed.SEALED_COLUMNS}

    def which(root: bytes | None) -> str:
        return "是" if root is not None and kcv == sealed.kcv(root) else "否"

    if args.action == "status":
        print(f"库内生成的根密钥 : {'存在' if db_root else '不存在'}；已加密数据使用它：{which(db_root)}")
        print(f"SYNWEVO_SECRET   : {'已设置' if env_root else '未设置'}；已加密数据使用它：{which(env_root)}")
        print("已加密数据       : " + "，".join(f"{k}={v}" for k, v in counts.items()))
        if env_root and kcv and kcv != sealed.kcv(env_root):
            print("\n当前 SYNWEVO_SECRET 与已加密数据不一致，服务会拒绝启动。"
                  "确认要改用它，请运行：secret rekey --to env")
        return 0

    # rekey
    if args.to == "env":
        if not env_root:
            print("没有设置 SYNWEVO_SECRET：要迁移到外部根密钥，请先在环境里设置它再运行。", file=sys.stderr)
            return 2
        if args.from_env:
            old_raw = os.environ.get(args.from_env)
            if not old_raw:
                print(f"环境变量 {args.from_env} 未设置。", file=sys.stderr)
                return 2
            old, old_label = old_raw.encode(), f"${args.from_env}"
        else:
            old, old_label = db_root, "库内 meta.server_secret"
        new, new_label = env_root, "SYNWEVO_SECRET"
    else:
        if not env_root:
            print("迁回库内根密钥需要当前的 SYNWEVO_SECRET 仍然设置着（它是旧根密钥）。", file=sys.stderr)
            return 2
        old, old_label = env_root, "SYNWEVO_SECRET"
        if not db_root:
            import secrets as _secrets
            store.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('server_secret', ?)",
                          (_secrets.token_bytes(32).hex(),))
            db_root, _ = _roots(store)
        new, new_label = db_root, "库内 meta.server_secret"
    if old is None:
        print(f"没有可迁移的旧根密钥（{old_label} 不存在）。", file=sys.stderr)
        return 2
    if old == new:
        print("新旧根密钥相同，无需迁移。")
        return 0
    opens = sealed.opens_with(store, old)
    if opens is False:
        print(f"拒绝迁移：{old_label} 打不开库内已加密的数据，它不是写入这些数据时使用的根密钥。"
              "没有做任何修改。", file=sys.stderr)
        _audit(store, "cli.secret.rekey", result="denied", meta={"from": old_label, "to": new_label,
                                                                  "reason": "old_root_mismatch"})
        return 2
    if kcv and kcv not in (sealed.kcv(old), sealed.kcv(new)):
        print(f"拒绝迁移：库里记录的根密钥既不是 {old_label} 也不是 {new_label}。没有做任何修改。", file=sys.stderr)
        return 2
    if not args.yes:
        if not sys.stdin.isatty():
            print("这会重新加密全部两步验证种子与 Webhook 签名密钥。确认请加 --yes。", file=sys.stderr)
            return 2
        if input(f"把已加密数据从 {old_label} 迁移到 {new_label}？输入 yes 确认：").strip() != "yes":
            print("已取消。")
            return 1
    try:
        done = sealed.rekey(store, old, new)
    except Exception as e:  # noqa: BLE001 - the transaction rolled back; say so and stop
        print(f"迁移失败，已回滚，没有做任何修改：{type(e).__name__}", file=sys.stderr)
        _audit(store, "cli.secret.rekey", result="error", meta={"from": old_label, "to": new_label,
                                                                 "error": type(e).__name__})
        return 2
    _audit(store, "cli.secret.rekey", meta={"from": old_label, "to": new_label, "counts": done})
    print(f"已把已加密数据从 {old_label} 迁移到 {new_label}：" + "，".join(f"{k}={v}" for k, v in done.items()))
    print("注意：用旧根密钥签出的下载链接会失效（它们本来最多 15 分钟有效）。")
    if args.to == "db":
        print("现在可以从环境里删除 SYNWEVO_SECRET 再重启服务。")
    else:
        print("请确认所有服务进程都使用这个 SYNWEVO_SECRET 后再重启。")
    return 0


def cmd_info(args) -> int:
    """What the account side of this deployment is configured to do."""
    d = data_dir(args)
    store = Store(d, migrate=False)          # inspect only: never upgrade the directory being looked at
    print(f"数据目录      : {Path(d).resolve()}")
    print(f"数据库        : {store.db_path}（schema v{store.schema_version()}）")
    print(f"发行者        : {os.environ.get('SYNWEVO_ISSUER', '(未设置)')}")
    print(f"角色          : SYNWEVO_ROLE={os.environ.get('SYNWEVO_ROLE', 'all')}")
    print(f"注册策略      : {registration_mode(store)}")
    from synwevo_core.passwords import parameters as pw_params
    from .mfa import key_is_external
    pp = pw_params()
    print(f"口令哈希      : argon2id m={pp['memory_kib']}KiB t={pp['time_cost']} p={pp['parallelism']}，并发上限 {pp['concurrency']}")
    # Where this key lives decides whether encrypting TOTP seeds helps against a stolen database,
    # so it is printed rather than left for someone to infer from the absence of a warning. The
    # advice used to be only "set SYNWEVO_SECRET" — which, on a database that already held
    # seeds, made every enrolled authenticator fail. The migration step is part of the advice.
    print("MFA 种子密钥  : " + ("来自 SYNWEVO_SECRET（与数据分离）" if key_is_external()
                                else "取自库内 meta.server_secret —— 与密文同库，拿到数据库副本即可解出 TOTP 种子。"
                                     "要改用 SYNWEVO_SECRET：先设置它，再运行 "
                                     "`python -m synwevo_identity.cli secret rekey --to env`，最后重启服务；"
                                     "只设置变量而不迁移，服务会拒绝启动而不是让已登记的动态验证码全部失效"))
    if store.schema_version():
        counts = {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                  for t in ("users", "workspaces", "memberships", "app_entitlements", "sessions", "oauth_clients")}
        print("账户统计      : " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="数据目录（默认 $SYNWEVO_DATA_DIR 或仓库根目录 data/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="建立首位平台管理员（无默认口令）")
    b.add_argument("action", choices=["status", "token", "admin"])
    b.add_argument("--email", help="admin：管理员邮箱")
    b.add_argument("--name", help="admin：显示名称")
    b.add_argument("--password", help="admin：口令（省略则交互输入，不回显）")
    b.add_argument("--ttl", type=int, default=900, help="token：有效期秒数（默认 900）")
    b.add_argument("--force", action="store_true", help="admin：已有管理员时仍再加一名")
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

    cl = sub.add_parser("client", help="OIDC 客户端登记与凭据生命周期（无动态注册端点）")
    cl.add_argument("action", choices=["register", "list", "show", "disable", "enable",
                                       "rotate-secret", "revoke-tokens"])
    cl.add_argument("target", nargs="?", help="客户端 ID（也可用 --client-id）")
    cl.add_argument("--client-id", dest="client_id", help="客户端 ID")
    cl.add_argument("--app", default="docweave", help="所属应用 ID")
    cl.add_argument("--name", help="显示名称")
    cl.add_argument("--redirect", action="append", help="回调地址，可重复；精确匹配，无通配符")
    cl.add_argument("--post-logout", dest="post_logout", action="append", help="登出后跳转地址，可重复")
    cl.add_argument("--backchannel", help="后通道登出接收地址")
    cl.add_argument("--audience", action="append",
                    help="该客户端可获得令牌的 API 受众（RFC 8707 resource），可重复；"
                         "Synwevo Docs 的接口客户端（--app docweave）省略时默认为 SYNWEVO_DOCS_RESOURCE")
    cl.add_argument("--scopes", default="openid profile email",
                    help="该客户端可申请的权限，空格分隔；用户客户端要代表本人调用接口时在此列出 docweave.*")
    cl.add_argument("--confidential", action="store_true", help="机密客户端：生成一次性 client_secret")
    cl.add_argument("--service", action="store_true",
                    help="登记为服务账户（机器身份）：必须机密，必须绑定一个工作空间")
    cl.add_argument("--workspace", help="服务账户所属工作空间 ID")
    cl.add_argument("--api-scopes", dest="api_scopes", default="",
                    help="接口权限，空格分隔：docweave.read docweave.write docweave.manage")
    cl.add_argument("--grace-minutes", dest="grace_minutes", type=int, default=0,
                    help="rotate-secret：旧密钥继续有效的分钟数（默认 0 = 立即失效并撤销已签发令牌；最多 1440）")
    cl.set_defaults(func=cmd_client)

    ky = sub.add_parser("keys", help="OIDC 签名密钥")
    ky.add_argument("action", choices=["list", "rotate", "retire"])
    ky.add_argument("--kid", help="retire 时指定")
    ky.set_defaults(func=cmd_keys)

    se = sub.add_parser("secret", help="服务端根密钥：查看状态，或把已加密数据迁移到另一把根密钥")
    se.add_argument("action", choices=["status", "rekey"])
    se.add_argument("--to", choices=["env", "db"], default="env",
                    help="rekey：迁移到 SYNWEVO_SECRET（env，默认）或迁回库内生成的根密钥（db）")
    se.add_argument("--from-env", dest="from_env",
                    help="rekey --to env：旧根密钥所在的环境变量名（从一把外部密钥换到另一把时使用）")
    se.add_argument("--yes", action="store_true", help="不再交互确认")
    se.set_defaults(func=cmd_secret)

    i = sub.add_parser("info", help="账户侧配置")
    i.set_defaults(func=cmd_info)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
