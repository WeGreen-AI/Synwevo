"""Public portal documents; no identity or business data is rendered into these pages."""
from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from synwevo_core import env

PUBLIC_PATHS = {"/": "zh-Hans", "/zh-Hans": "zh-Hans", "/zh-Hant": "zh-Hant", "/en": "en"}
COPY = {
    "zh-Hans": (
        "Synwevo · 让数据成为文档", "在 Synwevo Docs 中选择数据、映射模板、检查并批量生成文档。通过统一账户进入你的工作空间。",
        "让数据成为文档", "注册账户", "登录", "Synwevo Workspace · 你的工作空间",
    ),
    "zh-Hant": (
        "Synwevo · 讓資料成為文件", "在 Synwevo Docs 中選取資料、對應範本、檢查並批次產生文件。透過統一帳戶進入你的工作空間。",
        "讓資料成為文件", "註冊帳戶", "登入", "Synwevo Workspace · 你的工作空間",
    ),
    "en": (
        "Synwevo · Turn data into documents", "Select data, map templates, check and generate documents in batches with Synwevo Docs. Access your workspaces with one account.",
        "Turn data into documents", "Create an account", "Sign in", "Synwevo Workspace · Your workspace",
    ),
}


def public_origin() -> str | None:
    """Canonical URLs come only from configuration, never an untrusted Host header."""
    value = (env.get("PORTAL_URL") or "").strip().rstrip("/")
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


def document(dist: Path, path: str, locale: str) -> HTMLResponse:
    source = (dist / "index.html").read_text(encoding="utf-8")
    title, description, heading, register, login, private_title = COPY[locale]
    public = path in PUBLIC_PATHS
    title = title if public else private_title
    robots = "index, follow" if public else "noindex, nofollow"
    source = re.sub(r'<html\s+lang="[^"]*"', f'<html lang="{locale}"', source, count=1)
    source = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", source, count=1)
    source = re.sub(r'<meta name="(?:description|robots)"[^>]*>', "", source)
    tags = (f'<meta name="description" content="{html.escape(description, quote=True)}" />'
            f'<meta name="robots" content="{robots}" />')
    origin = public_origin()
    if public and origin:
        canonical_path = "/" if locale == "zh-Hans" else f"/{locale}"
        tags += f'<link rel="canonical" href="{html.escape(origin + canonical_path, quote=True)}" />'
        for language, suffix in (("zh-Hans", "/"), ("zh-Hant", "/zh-Hant"), ("en", "/en"), ("x-default", "/")):
            tags += f'<link rel="alternate" hreflang="{language}" href="{html.escape(origin + suffix, quote=True)}" />'
    source = source.replace("</head>", tags + "</head>")
    if public:
        fallback = (f'<h1>{html.escape(heading)}</h1><p>{html.escape(description)}</p>'
                    f'<a href="/register?lang={locale}">{html.escape(register)}</a> · '
                    f'<a href="/auth/start?next=%2Fworkspace&amp;lang={locale}">{html.escape(login)}</a>')
        source = source.replace('<div id="root"></div>', f'<div id="root">{fallback}</div>')
    return HTMLResponse(source, headers={"Cache-Control": "no-store", "X-Robots-Tag": robots})


def install_public(api: FastAPI, dist: Path) -> None:
    router = APIRouter(include_in_schema=False)

    @router.get("/robots.txt")
    def robots():
        lines = ["User-agent: *", "Disallow: /", "Allow: /portal-assets/", *[f"Allow: {p}$" for p in PUBLIC_PATHS]]
        if origin := public_origin():
            lines.append(f"Sitemap: {origin}/sitemap.xml")
        return PlainTextResponse("\n".join(lines) + "\n")

    @router.get("/sitemap.xml")
    def sitemap():
        origin = public_origin()
        urls = "" if not origin else "".join(
            f"<url><loc>{html.escape(origin + p)}</loc></url>" for p in ("/", "/zh-Hant", "/en"))
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + urls + "</urlset>", media_type="application/xml")

    # Explicit routes precede the deployment composer's generic shell. Assets keep their
    # hashed-file caching, while account routes still belong to the SSO redirect router.
    if (dist / "index.html").is_file():
        def page(request: Request):
            path = request.url.path
            locale = PUBLIC_PATHS.get(path, "zh-Hans")
            return document(dist, path, locale)

        for path in (*PUBLIC_PATHS, "/workspace"):
            router.add_api_route(path, page, methods=["GET"])

    api.include_router(router)

    @api.middleware("http")
    async def private_indexing(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in PUBLIC_PATHS and not request.url.path.startswith("/portal-assets/"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
