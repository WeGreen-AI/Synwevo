#!/usr/bin/env python3
"""Measure the single sign-on chain against live local servers.

    python scripts/platform.py up
    SYNWEVO_VERIFY_PASSWORD=… .venv/bin/python tests/integration/verify_sso.py --email sso-check@example.test
    .venv/bin/python tests/integration/verify_sso.py --email sso-check@example.test   # prompts for it

This uses Python's `http.cookiejar`, which implements the real cookie rules — **host-scoped and
port-blind**. That is the point. A jar keyed by origin would report isolation that a browser
does not provide, so the claim "the three services have separate sessions" is only worth making
against a jar that would fail if they shared a hostname.

The tests in `tests/integration/test_sso_chain.py` drive the same redirects over ASGI and cover
the cases a live walk cannot express (a forged state, a stolen code). This script covers the one
thing they cannot: that it works over real sockets, on real hostnames, through real redirects.

Exit code 0 only if every check passes. Nothing is deployed, but this **does act as the account
it signs in with**: it logs in (a session, OIDC codes and tokens, audit rows), completes sign-in at
Synwevo Docs and the portal, and finally calls `/oidc/logout`, which ends that person's central
session and every per-application session — they are signed out everywhere. Use a dedicated test
account, never someone's real one.

The password is read from `SYNWEVO_VERIFY_PASSWORD` or prompted for. It is not taken as a
command-line argument by default, because arguments end up in shell history and in the process
list where any local user can read them.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ACCOUNTS = "http://accounts.localhost:8770"
PORTAL = "http://portal.localhost:8769"
DOCS = "http://docweave.localhost:8765"   # 与 scripts/platform.py 的默认端口一致

PASS, FAIL = "✓", "✗"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, title: str, detail: str = "") -> bool:
    results.append((bool(ok), title, detail))
    print(f"  {PASS if ok else FAIL} {title}" + (f"\n      {detail}" if detail else ""))
    return bool(ok)


class Browser:
    """A real cookie jar plus manual redirect following, so every hop can be inspected."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.trail: list[str] = []

    def open(self, url, data=None, headers=None, follow=True, max_hops=10):
        for _ in range(max_hops):
            self.trail.append(url)
            req = urllib.request.Request(url, data=data, headers=headers or {},
                                         method="POST" if data is not None else "GET")
            try:
                resp = self.opener.open(req, timeout=15)
            except urllib.error.HTTPError as e:
                resp = e
            if not follow or resp.status not in (301, 302, 303, 307, 308):
                return resp
            loc = resp.headers["Location"]
            url = loc if "://" in loc else urllib.parse.urljoin(url, loc)
            data = None
        raise SystemExit("重定向次数过多——可能是登录回环:\n  " + "\n  ".join(self.trail))

    def json_post(self, url, payload, headers=None):
        body = json.dumps(payload).encode()
        h = {"Content-Type": "application/json", **(headers or {})}
        return self.open(url, data=body, headers=h, follow=False)

    def cookies_for(self, host: str) -> dict:
        """What the jar would actually send to this host — the measurement that matters."""
        return {c.name: c.value for c in self.jar if c.domain.lstrip(".") == host}

    def cookie_objects(self, host: str) -> list:
        return [c for c in self.jar if c.domain.lstrip(".") == host]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="专用测试账户（脚本结束时会把它从所有应用登出）")
    ap.add_argument("--password", help=argparse.SUPPRESS)   # accepted for old scripts; warned against
    ap.add_argument("--accounts", default=ACCOUNTS)
    ap.add_argument("--portal", default=PORTAL)
    ap.add_argument("--docs", "--docweave", dest="docs", default=DOCS,
                    help="Synwevo Docs 地址（--docweave 是旧名字，仍然接受）")
    args = ap.parse_args()
    if args.password:
        print("!! 口令出现在命令行参数里：它会留在 shell 历史和进程列表中。"
              "改用 SYNWEVO_VERIFY_PASSWORD 或交互输入。", file=sys.stderr)
    else:
        args.password = os.environ.get("SYNWEVO_VERIFY_PASSWORD") or getpass.getpass(f"{args.email} 的口令：")
    acc, portal, dw = args.accounts.rstrip("/"), args.portal.rstrip("/"), args.docs.rstrip("/")
    acc_host = urllib.parse.urlparse(acc).hostname
    dw_host = urllib.parse.urlparse(dw).hostname
    portal_host = urllib.parse.urlparse(portal).hostname

    b = Browser()

    print("\n【1】账户服务是一个真实的 OIDC 发行方")
    d = json.load(b.open(f"{acc}/.well-known/openid-configuration", follow=False))
    check(d["issuer"] == acc, "discovery 的发行者与配置一致", d["issuer"])
    check(d["code_challenge_methods_supported"] == ["S256"], "只接受 PKCE S256")
    check(d["response_types_supported"] == ["code"], "只接受授权码流程")
    jwks = json.load(b.open(d["jwks_uri"], follow=False))
    check(len(jwks["keys"]) >= 1, f"JWKS 发布 {len(jwks['keys'])} 把公钥")
    check(all("d" not in k for k in jwks["keys"]), "JWKS 中没有私钥材料")

    print("\n【2】角色边界：一个二进制，三个角色")
    r = b.open(f"{dw}/.well-known/openid-configuration", follow=False)
    check(r.status == 404, "Synwevo Docs 不冒充发行方", f"HTTP {r.status}")
    r = b.open(f"{acc}/api/projects", follow=False)
    check(r.status == 404, "账户服务不提供业务接口", f"HTTP {r.status}")
    r = b.open(f"{dw}/api/auth/totp/begin", data=b"", follow=False)
    check(r.status == 404, "认证器管理只在账户服务上", f"HTTP {r.status}")

    print("\n【3】统一登录（在账户服务上输入一次口令）")
    r = b.json_post(f"{acc}/api/auth/login", {"email": args.email, "password": args.password})
    if r.status != 200:
        print(f"  {FAIL} 登录失败 HTTP {r.status}: {r.read()[:300].decode(errors='replace')}")
        return 1
    check(True, "账户服务登录成功")
    check(bool(b.cookies_for(acc_host).get("dw_session")), "中央会话 Cookie 设置在账户服务主机上")
    check(not b.cookies_for(dw_host), "此时 Synwevo Docs 主机上一个 Cookie 也没有",
          f"{dw_host}: {b.cookies_for(dw_host) or '（空）'}")

    print("\n【4】应用直达：Synwevo Docs → 统一身份 → 回到原页面")
    b.trail.clear()
    target = "/projects"
    r = b.open(f"{dw}/auth/start?next={urllib.parse.quote(target, safe='')}")
    check(r.status == 200, f"最终落地 HTTP {r.status}")
    check(b.trail[-1] == dw + target, "回到了原来请求的页面", b.trail[-1])
    check(any(u.startswith(acc + "/oidc/authorize") for u in b.trail),
          "确实经过了账户服务的授权端点")
    check(not any("/login" in u for u in b.trail), "已有统一登录态时没有再次要求口令")
    print("      链路：\n        " + "\n        ".join(
        u.split("?")[0] + ("?…" if "?" in u else "") for u in b.trail))

    print("\n【5】Cookie 隔离（这是用真实 Cookie 规则量出来的，不是按端口假设的）")
    a_cookies, d_cookies = b.cookies_for(acc_host), b.cookies_for(dw_host)
    check(bool(d_cookies.get("dw_session")), "Synwevo Docs 有了自己的会话 Cookie")
    check(a_cookies.get("dw_session") != d_cookies.get("dw_session"),
          "两个会话值不同：没有复制中央会话")
    for c in b.cookie_objects(dw_host):
        if c.name == "dw_session":
            check(c.domain.lstrip(".") == dw_host and not c.domain.startswith("."),
                  f"Synwevo Docs 的会话 Cookie 是 host-only（domain={c.domain}）")
            check(c.port is None, "Cookie 没有端口限定——端口不隔离 Cookie，主机才隔离")
    check(not any(c.domain.lstrip(".") in ("localhost", ".localhost") for c in b.jar),
          "没有任何 Cookie 落在 localhost 这样的共享父域上")

    print("\n【6】应用会话是应用自己的")
    me = json.load(b.open(f"{dw}/api/auth/me", follow=False))
    check(me["email"] == args.email, "Synwevo Docs 认得这个人", me["email"])
    bare = Browser()      # no cookies at all
    r = bare.open(f"{dw}/api/auth/me", follow=False)
    check(r.status == 401, "没有 Cookie 时 Synwevo Docs 拒绝", f"HTTP {r.status}")

    print("\n【7】平台入口：门户 → 同一个账号")
    b.trail.clear()
    r = b.open(f"{portal}/auth/start?next=%2F")
    check(r.status == 200, f"门户落地 HTTP {r.status}")
    check(b.trail[-1].startswith(portal), "留在门户上", b.trail[-1])
    p_cookies = b.cookies_for(portal_host)
    check(bool(p_cookies.get("dw_session")), "门户有了自己的第三个会话")
    check(len({a_cookies.get("dw_session"), d_cookies.get("dw_session"),
               p_cookies.get("dw_session")}) == 3, "三个来源 = 三个互不相同的会话")

    home = json.load(b.open(f"{portal}/api/portal/home", follow=False))
    check(home["user"]["email"] == args.email, "门户首页认得这个人")
    names = [a["id"] for a in home["apps"]]
    check(names == ["docweave"], f"只列出真实可用的应用：{names or '（空）'}")
    check(all(a["launch_url"] for a in home["apps"]), "每张卡片都有真实的启动地址")
    check("platform" not in names, "门户不把自己列成一个可以打开的应用")
    blob = json.dumps(home, ensure_ascii=False)
    check("p_" not in blob and "project" not in blob.lower(),
          "门户不聚合业务数据——没有项目标题、任务或文件元数据")
    check(bool(home["platform"]["name"]), f"平台名称可配置：{home['platform']['name']}")

    print("\n【8】跨应用登出")
    r = b.open(f"{acc}/oidc/logout?" + urllib.parse.urlencode(
        {"client_id": "docweave-web", "post_logout_redirect_uri": dw + "/signed-out"}))
    check(b.trail[-1].startswith(dw), "登出后回到登记过的地址", b.trail[-1])
    r = b.open(f"{dw}/api/auth/me", follow=False)
    check(r.status == 401, "Synwevo Docs 的会话也结束了（后通道登出生效）", f"HTTP {r.status}")
    r = b.open(f"{portal}/api/auth/me", follow=False)
    check(r.status == 401, "门户的会话也结束了——退出的是平台，不只是发起登出的那个应用",
          f"HTTP {r.status}")
    r = b.open(f"{acc}/api/auth/me", follow=False)
    check(r.status == 401, "中央会话结束", f"HTTP {r.status}")

    print("\n【9】未登记的登出跳转地址被拒绝，而不是被跟随")
    r = b.open(f"{acc}/oidc/logout?" + urllib.parse.urlencode(
        {"client_id": "docweave-web", "post_logout_redirect_uri": "http://evil.example.com/x"}),
        follow=False)
    check(r.status == 400, f"HTTP {r.status}")
    check("evil.example.com" not in (r.headers.get("Location") or ""), "没有把它放进 Location")

    bad = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{'─' * 60}\n{len(results) - bad}/{len(results)} 项通过"
          + (f"，{bad} 项失败" if bad else "。全部在本机完成，未部署。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
