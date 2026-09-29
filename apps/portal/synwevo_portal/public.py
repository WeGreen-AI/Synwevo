"""Public portal documents; no identity or business data is rendered into these pages.

Three public pages, each in three languages: the home, 数据与隐私 (`/privacy`) and 服务说明
(`/service`). They are server-rendered so that a crawler, a reader without JavaScript and the
React shell all see the same summary; the shell then replaces the fallback with the full page.

`/public/deployment.json` is the one document here that is not static copy. The privacy and
service pages describe *this* deployment, and a few of their statements depend on how the
account service and Synwevo Docs are configured (malware scanning, human verification, external
sign-in, registration policy, the mail sender). Those are read from the running services and
shown as they are, so the pages track the deployment instead of repeating a claim that was true
when the copy was written. The record of every fact is docs/platform/public-pages.md.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from synwevo_core import env

LOCALES = ("zh-Hans", "zh-Hant", "en")
PAGES = ("home", "privacy", "service")


def public_path(page: str, locale: str) -> str:
    """The canonical path of a public page in a language: `/`, `/en`, `/zh-Hant/privacy`, …"""
    prefix = "" if locale == "zh-Hans" else f"/{locale}"
    if page == "home":
        return prefix or "/"
    return f"{prefix}/{page}"


#: path → locale, for every path the portal answers with a public document. `/zh-Hans/...` is a
#: non-canonical alias of the unprefixed path (the shell normalises it).
PUBLIC_PATHS: dict[str, str] = {}
#: path → page id.
PUBLIC_PAGES: dict[str, str] = {}
for _page in PAGES:
    for _locale in LOCALES:
        PUBLIC_PATHS[public_path(_page, _locale)] = _locale
        PUBLIC_PAGES[public_path(_page, _locale)] = _page
    _alias = "/zh-Hans" if _page == "home" else f"/zh-Hans/{_page}"
    PUBLIC_PATHS[_alias] = "zh-Hans"
    PUBLIC_PAGES[_alias] = _page

# The server-rendered copy. Per language: the links every page carries, the private shell title,
# then per page its <title>, meta description, <h1> and the summary paragraphs a reader without
# JavaScript sees. The React shell renders the same words from apps/portal/web/src/messages.ts.
COPY: dict[str, dict] = {
    "zh-Hans": {
        "register": "注册账户", "login": "登录", "home": "首页",
        "private_title": "Synwevo Workspace · 你的工作空间",
        "pages": {
            "home": {
                "title": "Synwevo · 工作流程自动化平台",
                "description": "文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。",
                "heading": "让工作，自然相连。",
                "paragraphs": [
                    "Synwevo，取意于协同与编织。分散的文档、数据与沟通，在这里被织成有序的工作；想法在协作中成形，日常在持续完善中变得从容。",
                    "今天可用的应用：Synwevo Docs（文档自动化）与 Synwevo Message（组织消息）。它们共用同一个账户、同一套工作空间与授权。",
                ],
                "label": "首页",
            },
            "privacy": {
                "title": "数据与隐私 · Synwevo",
                "description": "Synwevo 如何处理你的账户、文档与消息数据：存储位置、Cookie、保留期限、导出与删除。",
                "heading": "你的数据，说清楚。",
                "paragraphs": [
                    "账户数据可以随时导出，也可以申请删除；删除有 7 天缓冲期，期间可以撤回。",
                    "数据保存在位于日本东京的单台服务器上；传输经 HTTPS 加密，服务器端不做端到端加密。",
                    "只用本站自己的 Cookie 维持登录；平台管理员读不到业务文档与对话；没有任何 AI 处理你的内容。",
                ],
                "label": "数据与隐私",
            },
            "service": {
                "title": "服务说明 · Synwevo",
                "description": "Synwevo 是什么、有哪些应用与角色、如何注册、有哪些限制、遇到问题找谁。",
                "heading": "这项服务是什么，怎么用。",
                "paragraphs": [
                    "Synwevo 是一个工作流程自动化平台：统一账户、工作空间、组织与通知，加上两个应用——Synwevo Docs 与 Synwevo Message。",
                    "注册后经邮箱验证即可使用；机构邮箱同样可用。限额、角色与删除规则都写在这一页。",
                ],
                "label": "服务说明",
            },
        },
    },
    "zh-Hant": {
        "register": "註冊帳戶", "login": "登入", "home": "首頁",
        "private_title": "Synwevo Workspace · 你的工作空間",
        "pages": {
            "home": {
                "title": "Synwevo · 工作流程自動化平台",
                "description": "文件自動化、團隊協作與訊息溝通，匯聚於同一個工作空間。",
                "heading": "讓工作，自然相連。",
                "paragraphs": [
                    "Synwevo，取意於協同與編織。分散的文件、資料與溝通，在這裡被織成有序的工作；想法在協作中成形，日常在持續完善中變得從容。",
                    "今天可用的應用：Synwevo Docs（文件自動化）與 Synwevo Message（組織訊息）。它們共用同一個帳戶、同一套工作空間與授權。",
                ],
                "label": "首頁",
            },
            "privacy": {
                "title": "資料與私隱 · Synwevo",
                "description": "Synwevo 如何處理你的帳戶、文件與訊息資料：儲存位置、Cookie、保留期限、匯出與刪除。",
                "heading": "你的資料，說清楚。",
                "paragraphs": [
                    "帳戶資料可以隨時匯出，也可以申請刪除；刪除有 7 天緩衝期，期間可以撤回。",
                    "資料保存在位於日本東京的單一伺服器上；傳輸經 HTTPS 加密，伺服器端不做端對端加密。",
                    "只用本站自己的 Cookie 維持登入；平台管理員讀不到業務文件與對話；沒有任何 AI 處理你的內容。",
                ],
                "label": "資料與私隱",
            },
            "service": {
                "title": "服務說明 · Synwevo",
                "description": "Synwevo 是甚麼、有哪些應用與角色、如何註冊、有哪些限制、遇到問題找誰。",
                "heading": "這項服務是甚麼，怎麼用。",
                "paragraphs": [
                    "Synwevo 是一個工作流程自動化平台：統一帳戶、工作空間、組織與通知，加上兩個應用——Synwevo Docs 與 Synwevo Message。",
                    "註冊後經電郵驗證即可使用；機構電郵同樣可用。限額、角色與刪除規則都寫在這一頁。",
                ],
                "label": "服務說明",
            },
        },
    },
    "en": {
        "register": "Create an account", "login": "Sign in", "home": "Home",
        "private_title": "Synwevo Workspace · Your workspace",
        "pages": {
            "home": {
                "title": "Synwevo · Workflow automation platform",
                "description": "Document automation, team collaboration and messaging, brought together in one workspace.",
                "heading": "Work that comes together.",
                "paragraphs": [
                    "Synwevo takes its name from synergy and weaving. Scattered documents, data and conversations are woven here into orderly work; ideas take shape in collaboration, and the everyday grows calmer as it keeps improving.",
                    "Available today: Synwevo Docs (document automation) and Synwevo Message (organisation messaging). They share one account, one set of workspaces and one permission model.",
                ],
                "label": "Home",
            },
            "privacy": {
                "title": "Data & privacy · Synwevo",
                "description": "How Synwevo handles your account, document and message data: where it is stored, cookies, retention, export and deletion.",
                "heading": "Your data, plainly stated.",
                "paragraphs": [
                    "You can export your account data at any time and request deletion; deletion has a 7-day grace period during which it can be cancelled.",
                    "Data is stored on a single server in Tokyo, Japan; transport is encrypted with HTTPS, and there is no end-to-end encryption on the server.",
                    "Only this site’s own cookies keep you signed in; platform administrators cannot read business documents or conversations; no AI processes your content.",
                ],
                "label": "Data & privacy",
            },
            "service": {
                "title": "About this service · Synwevo",
                "description": "What Synwevo is, its applications and roles, how registration works, which limits apply and where to turn when something goes wrong.",
                "heading": "What this service is, and how it works.",
                "paragraphs": [
                    "Synwevo is a workflow automation platform: one account, workspaces, organisations and notifications, plus two applications — Synwevo Docs and Synwevo Message.",
                    "Register, verify your mailbox and start; institutional mailboxes work too. Limits, roles and deletion rules are all on this page.",
                ],
                "label": "About this service",
            },
        },
    },
}


def public_origin() -> str | None:
    """Canonical URLs come only from configuration, never an untrusted Host header: the public
    site's address (`SYNWEVO_SITE_URL`), else the portal's own (`SYNWEVO_PORTAL_URL`)."""
    return _valid_origin(env.get("SITE_URL") or env.get("PORTAL_URL"))


def workspace_origin() -> str | None:
    """The portal process's own address, where the workspace lives (`SYNWEVO_PORTAL_URL`)."""
    return _valid_origin(env.get("PORTAL_URL"))


def split_hosts() -> tuple[str, str] | None:
    """(site origin, workspace origin) when the public site and the workspace are on different
    hosts (production: synwevo.com and workplace.synwevo.com), else None — one host serves both,
    as on a single-host install and in local development."""
    site, work = public_origin(), workspace_origin()
    if site and work and urlsplit(site).hostname != urlsplit(work).hostname:
        return site, work
    return None


def request_host(request: Request) -> str:
    """The host this request was addressed to, only for *choosing between the configured
    surfaces*. Every absolute URL a response contains still comes from configuration."""
    return (request.headers.get("host") or "").split(":")[0].strip().lower()


#: Paths of the workspace surface that the public site hands to the workspace host (GET only):
#: the workspace itself, the sign-in start and the signed-out page. The sign-in *callback* and
#: back-channel logout are not moved: an authorization code is only ever redeemed where it was
#: issued for, never replayed to another address.
WORKSPACE_PATHS = ("/workspace", "/auth/start", "/signed-out",
                   # The business applications served on the workplace origin (synwevo_suite;
                   # docs/agent-work/DECISIONS.md D1). Listed by path, not imported: the portal
                   # does not depend on that module.
                   "/forms", "/tables", "/dataprep", "/approvals", "/extract", "/agent")


def _valid_origin(raw: str | None) -> str | None:
    value = (raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path
                or any(ch.isspace() or ch in '<>"\\' for ch in value)):
            return None
        _ = parts.port
    except ValueError:
        return None
    return value


def document(dist: Path, path: str, locale: str, *, surface: str | None = None) -> HTMLResponse:
    source = (dist / "index.html").read_text(encoding="utf-8")
    if surface:
        # The shell decides its page from the path before the session loads; on the workspace
        # host "/" is the workspace, not the public home, and the server says so up front.
        source = source.replace("</head>", f'<meta name="synwevo-surface" content="{html.escape(surface, quote=True)}" /></head>', 1)
    words = COPY[locale]
    public = path in PUBLIC_PATHS
    page = PUBLIC_PAGES.get(path, "home")
    copy = words["pages"][page]
    title = copy["title"] if public else words["private_title"]
    description = copy["description"]
    robots = "index, follow" if public else "noindex, nofollow"
    source = re.sub(r'<html\s+lang="[^"]*"', f'<html lang="{locale}"', source, count=1)
    source = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", source, count=1)
    source = re.sub(r'<meta name="(?:description|robots)"[^>]*>', "", source)
    tags = (f'<meta name="description" content="{html.escape(description, quote=True)}" />'
            f'<meta name="robots" content="{robots}" />')
    origin = public_origin()
    if public and origin:
        tags += f'<link rel="canonical" href="{html.escape(origin + public_path(page, locale), quote=True)}" />'
        for language in (*LOCALES, "x-default"):
            href = origin + public_path(page, "zh-Hans" if language == "x-default" else language)
            tags += f'<link rel="alternate" hreflang="{language}" href="{html.escape(href, quote=True)}" />'
    if public:
        tags += share_tags(page, locale, title, description, origin)
    source = source.replace("</head>", tags + "</head>")
    if public:
        source = source.replace('<div id="root"></div>', f'<div id="root">{_fallback(page, locale)}</div>')
    return HTMLResponse(source, headers={"Cache-Control": "no-store", "X-Robots-Tag": robots})


#: Open Graph locale names for the three interface languages.
OG_LOCALE = {"zh-Hans": "zh_CN", "zh-Hant": "zh_HK", "en": "en_US"}
#: The link-preview card, generated from the confirmed logo by scripts/brand_assets.py and copied
#: to each shell's root (packages/ui/public-brand/share-card.png).
SHARE_CARD = ("/share-card.png", 1200, 630)


def share_tags(page: str, locale: str, title: str, description: str, origin: str | None) -> str:
    """What a chat app or social site shows when someone pastes a public page's address: the
    page's own title and summary in its language, the site name, and — only when the deployment
    has a configured public origin, never a Host header — the canonical address and the card
    image. Private pages (the workspace, sign-in) get none of this: they are not shared."""
    e = lambda v: html.escape(str(v), quote=True)  # noqa: E731
    out = (f'<meta property="og:site_name" content="Synwevo" />'
           f'<meta property="og:type" content="website" />'
           f'<meta property="og:title" content="{e(title)}" />'
           f'<meta property="og:description" content="{e(description)}" />'
           f'<meta property="og:locale" content="{OG_LOCALE[locale]}" />')
    for other in LOCALES:
        if other != locale:
            out += f'<meta property="og:locale:alternate" content="{OG_LOCALE[other]}" />'
    out += '<meta name="twitter:card" content="summary_large_image" />'
    if origin:
        path, width, height = SHARE_CARD
        out += (f'<meta property="og:url" content="{e(origin + public_path(page, locale))}" />'
                f'<meta property="og:image" content="{e(origin + path)}" />'
                f'<meta property="og:image:width" content="{width}" />'
                f'<meta property="og:image:height" content="{height}" />'
                f'<meta property="og:image:alt" content="Synwevo" />')
    return out


def _fallback(page: str, locale: str) -> str:
    """What a reader without JavaScript (or a crawler) gets: the heading, the summary and the
    real links — never `/workspace`, which only means something with a session."""
    words = COPY[locale]
    copy = words["pages"][page]
    body = f'<h1>{html.escape(copy["heading"])}</h1><p>{html.escape(copy["description"])}</p>'
    body += "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in copy["paragraphs"])
    links = [f'<a href="/register?lang={locale}">{html.escape(words["register"])}</a>',
             f'<a href="/auth/start?next=%2Fworkspace&amp;lang={locale}">{html.escape(words["login"])}</a>']
    for other in PAGES:
        if other != page:
            links.append(f'<a href="{public_path(other, locale)}">{html.escape(words["pages"][other]["label"])}</a>')
    return body + "<p>" + " · ".join(links) + "</p>"


# ------------------------------------------------------------------ deployment facts
FACTS_TTL_S = 60
FACTS_TIMEOUT_S = 3.0
_facts_lock = threading.Lock()
_facts_cache: tuple[float, dict] | None = None


def _scanner_state(value) -> str | None:
    """`/readyz` `checks.malware_scanner` → on | off | down, or None when unreadable."""
    if not isinstance(value, str):
        return None
    if value == "ok":
        return "on"
    if value == "off":
        return "off"
    if value.startswith("down"):
        return "down"
    return None


def _origin_of(url: str | None) -> str | None:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _docs_origin(store, ident) -> str | None:
    if store is None or ident is None:
        return None
    try:
        from synwevo_core.apps import Apps, DOCS_APP_ID
        app = Apps(store, ident).get(DOCS_APP_ID)
    except Exception:  # noqa: BLE001 - a missing registry row is "unknown", never an error page
        return None
    return _origin_of((app or {}).get("launch_url"))


#: No ambient proxy: these requests carry nothing secret, but a `HTTP_PROXY` nobody chose must
#: not decide what "this deployment" answers (the relying party makes the same choice).
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
#: The reader says who it is. In production the account service and Docs are reached through the
#: public edge, which answers 403 to the library's default `Python-urllib/x.y` agent (bot
#: filtering) while letting a named client through — found on the first deployment, when every
#: fact on the privacy page read "could not be read".
FACTS_USER_AGENT = "Synwevo-portal/1 (+deployment facts)"


def _read_json(url: str) -> dict | None:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": FACTS_USER_AGENT})
        with _opener.open(request, timeout=FACTS_TIMEOUT_S) as response:
            raw = response.read(65536)
    except urllib.error.HTTPError as e:
        # /readyz answers 503 with the same body while a component is down; the body is the point.
        if e.code != 503:
            return None
        try:
            raw = e.read(65536)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001 - unreachable means "unknown"
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def deployment_facts(store=None, ident=None, *, force: bool = False) -> dict:
    """What the privacy and service pages read at runtime, from the services themselves.

    Only configured origins are contacted (the account service's issuer and the registered
    Synwevo Docs launch URL), never anything from the request; the result is cached for a minute
    so an anonymous page cannot turn into a probe amplifier. Anything unreadable is `null`, and
    the pages say "could not be read" rather than guessing.
    """
    global _facts_cache
    with _facts_lock:
        if not force and _facts_cache and _facts_cache[0] > time.monotonic():
            return _facts_cache[1]
        facts = {
            "checked_at": time.time(),
            "accounts": {"malware_scanner": None, "human_verification": None, "external_providers": None,
                         "registration": None, "mail_sender": None, "member_import_cleanup": None},
            "docs": {"malware_scanner": None},
        }
        try:
            from synwevo_auth_client.rp import issuer
            accounts = _origin_of(issuer())
            docs = _docs_origin(store, ident)
            if accounts:
                ready = _read_json(accounts + "/readyz")
                if ready is not None:
                    facts["accounts"]["malware_scanner"] = _scanner_state((ready.get("checks") or {}).get("malware_scanner"))
                state = _read_json(accounts + "/api/auth/state")
                if state is not None:
                    facts["accounts"]["human_verification"] = bool(state.get("challenge"))
                    providers = state.get("external_providers")
                    facts["accounts"]["external_providers"] = [
                        str(p.get("label") or p.get("id"))[:40] for p in providers if isinstance(p, dict)
                    ] if isinstance(providers, list) else None
                    registration = state.get("registration")
                    facts["accounts"]["registration"] = registration if registration in ("open", "invite", "closed") else None
                    cleanup = state.get("member_import_cleanup")
                    facts["accounts"]["member_import_cleanup"] = cleanup if isinstance(cleanup, bool) else None
                    sender = state.get("mail_sender")
                    facts["accounts"]["mail_sender"] = (
                        {"name": str(sender.get("name") or "")[:80], "address": str(sender.get("address") or "")[:120]}
                        if isinstance(sender, dict) else False)
            if docs:
                ready = _read_json(docs + "/readyz")
                if ready is not None:
                    facts["docs"]["malware_scanner"] = _scanner_state((ready.get("checks") or {}).get("malware_scanner"))
        except Exception:  # noqa: BLE001 - the page renders "unknown"; it never fails because a probe did
            pass
        _facts_cache = (time.monotonic() + FACTS_TTL_S, facts)
        return facts


def install_public(api: FastAPI, dist: Path, store=None, ident=None) -> None:
    router = APIRouter(include_in_schema=False)

    @router.get("/robots.txt")
    def robots(request: Request):
        split = split_hosts()
        if split and request_host(request) == urlsplit(split[1]).hostname:
            return PlainTextResponse("User-agent: *\nDisallow: /\n")      # the workspace host is not for search
        lines = ["User-agent: *", "Disallow: /", "Allow: /portal-assets/", *[f"Allow: {p}$" for p in PUBLIC_PATHS]]
        if origin := public_origin():
            lines.append(f"Sitemap: {origin}/sitemap.xml")
        return PlainTextResponse("\n".join(lines) + "\n")

    @router.get("/sitemap.xml")
    def sitemap():
        origin = public_origin()
        urls = "" if not origin else "".join(
            f"<url><loc>{html.escape(origin + public_path(page, locale))}</loc></url>"
            for page in PAGES for locale in LOCALES)
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + urls + "</urlset>", media_type="application/xml")

    @router.get("/public/deployment.json")
    def deployment():
        # Anonymous by construction (not under /api/), read by the privacy and service pages.
        return JSONResponse(deployment_facts(store, ident), headers={"Cache-Control": "no-store"})

    # Explicit routes precede the deployment composer's generic shell. Assets keep their
    # hashed-file caching, while account routes still belong to the SSO redirect router.
    if (dist / "index.html").is_file():
        def page(request: Request):
            path = request.url.path
            locale = PUBLIC_PATHS.get(path, "zh-Hans")
            split = split_hosts()
            if split and request_host(request) == urlsplit(split[1]).hostname:
                # The workspace host: its root is the workspace; the public pages live on the site.
                if path in PUBLIC_PATHS and path != "/":
                    return _moved(split[0], request)
                return document(dist, "/workspace", locale, surface="workspace")
            return document(dist, path, locale)

        for path in (*PUBLIC_PATHS, "/workspace"):
            router.add_api_route(path, page, methods=["GET"])

    api.include_router(router)

    @api.middleware("http")
    async def private_indexing(request: Request, call_next):
        split = split_hosts()
        host = request_host(request)
        path = request.url.path
        if (split and host == urlsplit(split[0]).hostname and request.method in ("GET", "HEAD")
                and any(path == p or path.startswith(p + "/") for p in WORKSPACE_PATHS)):
            # The public site hands workspace addresses (old links, bookmarks, a sign-in started
            # from the home page) to the workspace host, path and query kept.
            return _moved(split[1], request)
        response = await call_next(request)
        on_workspace_host = bool(split) and host == urlsplit(split[1]).hostname
        if on_workspace_host or (path not in PUBLIC_PATHS and not path.startswith("/portal-assets/")):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response


def _moved(origin: str, request: Request) -> Response:
    """A permanent, method-keeping redirect to the same path on a configured origin."""
    target = origin + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return Response(status_code=308, headers={"Location": target, "Cache-Control": "no-store"})
