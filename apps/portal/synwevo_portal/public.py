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
from synwevo_core.terms import DOCUMENTS as TERMS_DOCUMENTS

LOCALES = ("zh-Hans", "zh-Hant", "en")
#: The public documents. terms, ai-use and pricing carry a version and an effective date
#: (synwevo_core.terms.DOCUMENTS), printed in their first paragraph.
PAGES = ("home", "privacy", "service", "terms", "ai-use", "pricing")


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
                    "只用本站自己的 Cookie 维持登录；平台管理员读不到业务文档与对话。只有 Synwevo Agent 会把内容发给 AI 模型：在你使用它并单独同意后，发送给模型提供方 DeepSeek；组织工作空间的内容默认不发送。",
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
            "terms": {
                "title": "服务条款 · Synwevo",
                "description": "使用 Synwevo 的条款：账户、钱包与充值（不可退款）、按量计费、免费额度、组织付费与 AI 输出。",
                "heading": "使用 Synwevo 的约定。",
                "paragraphs": [
                    "版本 {terms}，自 {terms_effective} 起生效。首次付费使用 Synwevo Agent 时，记录你接受的版本。",
                    "充值一经完成不可退款；钱包余额只能用于 Synwevo 的服务，不能转让、提现或兑换现金。AI 生成的内容可能有误，仅供参考。",
                ],
                "label": "服务条款",
            },
            "ai-use": {
                "title": "AI 使用规范 · Synwevo",
                "description": "使用 Synwevo Agent 时不允许的内容与行为、内容安全如何执行，以及 AI 生成内容的标识。",
                "heading": "负责任地使用 AI。",
                "paragraphs": [
                    "版本 {ai-use}，自 {ai-use_effective} 起生效。",
                    "消息与回答都经过内容安全规则检查；命中规则的消息不会发送，命中的回答会停止并不再显示。违规可能导致 Agent 使用权限被暂停。",
                ],
                "label": "AI 使用规范",
            },
            "pricing": {
                "title": "定价 · Synwevo",
                "description": "Synwevo Agent 如何计费：人民币钱包、按实际用量扣费、运行前确认价格与上限、免费额度与组织付费。",
                "heading": "用多少，付多少。",
                "paragraphs": [
                    "版本 {pricing}，自 {pricing_effective} 起生效。目前只有 Synwevo Agent 按用量收费，以人民币计价。",
                    "每次运行前显示当前单价与价格版本，并由你确认最高预算；充值不可退款。",
                ],
                "label": "定价",
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
                    "只用本站自己的 Cookie 維持登入；平台管理員讀不到業務文件與對話。只有 Synwevo Agent 會把內容傳給 AI 模型：在你使用它並單獨同意後，傳送給模型提供方 DeepSeek；組織工作空間的內容預設不傳送。",
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
            "terms": {
                "title": "服務條款 · Synwevo",
                "description": "使用 Synwevo 的條款：帳戶、錢包與儲值（不可退款）、按量計費、免費額度、組織付費與 AI 輸出。",
                "heading": "使用 Synwevo 的約定。",
                "paragraphs": [
                    "版本 {terms}，自 {terms_effective} 起生效。首次付費使用 Synwevo Agent 時，記錄你接受的版本。",
                    "儲值一經完成不可退款；錢包餘額只能用於 Synwevo 的服務，不能轉讓、提現或兌換現金。AI 產生的內容可能有誤，僅供參考。",
                ],
                "label": "服務條款",
            },
            "ai-use": {
                "title": "AI 使用規範 · Synwevo",
                "description": "使用 Synwevo Agent 時不允許的內容與行為、內容安全如何執行，以及 AI 產生內容的標識。",
                "heading": "負責任地使用 AI。",
                "paragraphs": [
                    "版本 {ai-use}，自 {ai-use_effective} 起生效。",
                    "訊息與回答都經過內容安全規則檢查；命中規則的訊息不會傳送，命中的回答會停止並不再顯示。違規可能導致 Agent 使用權限被暫停。",
                ],
                "label": "AI 使用規範",
            },
            "pricing": {
                "title": "定價 · Synwevo",
                "description": "Synwevo Agent 如何計費：人民幣錢包、按實際用量扣費、執行前確認價格與上限、免費額度與組織付費。",
                "heading": "用多少，付多少。",
                "paragraphs": [
                    "版本 {pricing}，自 {pricing_effective} 起生效。目前只有 Synwevo Agent 按用量收費，以人民幣計價。",
                    "每次執行前顯示當前單價與價格版本，並由你確認最高預算；儲值不可退款。",
                ],
                "label": "定價",
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
                    "Only this site’s own cookies keep you signed in; platform administrators cannot read business documents or conversations. Only Synwevo Agent sends content to an AI model: when you use it and after your separate consent, to the model provider DeepSeek; content of organisation workspaces is not sent by default.",
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
            "terms": {
                "title": "Terms of service · Synwevo",
                "description": "The terms for using Synwevo: account, wallet and top-ups (non-refundable), usage-based charges, the free allowance, organisation payment and AI output.",
                "heading": "The terms of using Synwevo.",
                "paragraphs": [
                    "Version {terms}, in effect from {terms_effective}. Your first paid use of Synwevo Agent records the version you accepted.",
                    "Top-ups are final and non-refundable; wallet balance can be used only for Synwevo services and cannot be transferred, withdrawn or exchanged for cash. AI-generated content may be wrong and is for reference only.",
                ],
                "label": "Terms of service",
            },
            "ai-use": {
                "title": "AI use rules · Synwevo",
                "description": "What is not allowed when using Synwevo Agent, how content safety is enforced, and how AI-generated content is labelled.",
                "heading": "Using AI responsibly.",
                "paragraphs": [
                    "Version {ai-use}, in effect from {ai-use_effective}.",
                    "Messages and answers are checked against content safety rules; a message that matches is not sent, and an answer that matches is stopped and no longer shown. Violations can lead to your Agent access being suspended.",
                ],
                "label": "AI use rules",
            },
            "pricing": {
                "title": "Pricing · Synwevo",
                "description": "How Synwevo Agent is charged: a CNY wallet, charges for actual usage, the price and maximum confirmed before each run, the free allowance and organisation payment.",
                "heading": "Pay for what you use.",
                "paragraphs": [
                    "Version {pricing}, in effect from {pricing_effective}. Today only Synwevo Agent is charged by usage, priced in Chinese yuan (CNY).",
                    "Before each run you see the current unit prices and price version and confirm a maximum budget; top-ups are non-refundable.",
                ],
                "label": "Pricing",
            },
        },
    },
}


def _versioned(copy: dict) -> None:
    """Print each document's version and effective date (synwevo_core.terms) into its copy."""
    values = {}
    for document, (version, effective) in TERMS_DOCUMENTS.items():
        values["{" + document + "}"], values["{" + document + "_effective}"] = version, effective
    for words in copy.values():
        for page in words["pages"].values():
            for i, paragraph in enumerate(page["paragraphs"]):
                for key, value in values.items():
                    paragraph = paragraph.replace(key, value)
                page["paragraphs"][i] = paragraph


_versioned(COPY)


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


def agent_fact(store, ident) -> dict:
    """Synwevo Agent on this deployment, from the application registry and the model settings —
    so the public pages say whether content can reach an AI model instead of asserting it.

    ``released``: the registry row ``agent`` is active (the deployment released it).
    ``provider``: the configured model provider (``SYNWEVO_AGENT_PROVIDER``; deepseek | anthropic).
    ``model_configured``: that provider has a platform model ready — for DeepSeek an enabled,
    governed route; for the legacy provider a configured platform key (only its presence is read).
    Unreadable parts are ``None``; the pages then say "could not be read"."""
    out = {"released": None, "provider": None, "model_configured": None}
    if store is None or ident is None:
        return out
    try:
        from synwevo_core.apps import Apps
        row = Apps(store, ident).get("agent")
        out["released"] = bool(row and row.get("status") == "active")
    except Exception:  # noqa: BLE001 - an unreadable registry is "unknown"; a missing row is "not released"
        out["released"] = None
    provider = (env.get("AGENT_PROVIDER") or "anthropic").strip().lower()
    out["provider"] = provider if provider in ("deepseek", "anthropic") else None
    try:
        if provider == "deepseek":
            from synwevo_core.billing.provider_settings import ProviderSettings
            environment = env.get("BILLING_ENVIRONMENT")
            spec = ProviderSettings(store, environment=environment).read()["spec"] if environment in ("test", "live") else None
            out["model_configured"] = bool(spec and spec.get("enabled"))
        elif provider == "anthropic":
            out["model_configured"] = bool((env.get("AGENT_ANTHROPIC_API_KEY") or "").strip())
    except Exception:  # noqa: BLE001
        out["model_configured"] = None
    return out


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
            "agent": agent_fact(store, ident),
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
