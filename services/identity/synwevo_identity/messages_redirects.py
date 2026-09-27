"""The account origin's former messages pages, `/messages` and `/messages/{cid}`.

Since CI-66 the conversations live on Synwevo Message. A bookmark, a mail written before the move
or a shell that still links here lands on a **server-side 303** to that application's sign-in
start — `<messages_url>/auth/start?next=/c/<cid>` — so the person arrives signed in, on the page
they wanted. Only `/auth/start` mints the request row that makes the round trip verifiable, and
the destination is the registry's launch URL (written by the messages process), never anything
from the query string: the one origin that must never have an open redirect does not get one.

On a deployment that has not added the application the registry has no row and these paths fall
through to whatever the caller passes as the fallback (the account shell, whose router answers
"page not found" — an honest answer, not a page that pretends to be the conversations).
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from synwevo_core.store import Store

# Conversation ids as `synwevo_core.messaging` mints them; anything else is not carried over.
CID = re.compile(r"^cv_[A-Za-z0-9_-]{1,64}$")


def messages_handoff(store: Store, cid: str | None, lang: str | None = None) -> str | None:
    """The absolute sign-in-start URL on the messages origin for a conversation (or the list),
    or None while the application is not registered."""
    from synwevo_core.apps import messages_launch_url
    base = messages_launch_url(store)
    if not base:
        return None
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(base))
    params = {"next": f"/c/{cid}" if cid and CID.match(cid) else "/"}
    if lang in ("zh-Hans", "zh-Hant", "en"):
        params["lang"] = lang
    return origin + "/auth/start?" + urllib.parse.urlencode(params)


def build_router(store: Store, fallback: Callable[[str], Response]) -> APIRouter:
    r = APIRouter(tags=["messaging"], include_in_schema=False)

    def go(request: Request, cid: str | None):
        url = messages_handoff(store, cid, request.query_params.get("lang"))
        if url:
            return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
        return fallback(request.url.path.lstrip("/"))

    @r.get("/messages")
    def messages_list(request: Request):
        return go(request, None)

    @r.get("/messages/{cid}")
    def messages_thread(request: Request, cid: str):
        return go(request, cid)

    return r
