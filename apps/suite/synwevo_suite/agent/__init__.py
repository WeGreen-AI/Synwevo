"""Synwevo Agent: the person's own conversational assistant across all their workspaces.

It belongs to the person (its conversations, settings and memories are keyed by user id and are
visible to nobody else) and acts in workspaces only with that person's rights, one explicitly named
workspace per operation, through the same operations the applications' pages use.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from synwevo_core.capabilities import AppManifest
from synwevo_core.errors import ServiceError
from synwevo_core.sessions import COOKIE_NAME

MANIFEST = AppManifest(
    id="agent", name="Synwevo Agent", path="/agent", icon="agent", scope="personal",
    summary={"zh-Hans": "你自己的助手：在你有权使用的所有工作空间里查询、比较和办理，写入前先经你确认。",
             "zh-Hant": "你自己的助理：在你有權使用的所有工作空間裡查詢、比較和辦理，寫入前先經你確認。",
             "en": "Your own assistant: look things up, compare and get things done across every workspace you can use — "
                   "anything that changes data waits for your confirmation."},
    object_kinds=("conversation",))

#: A stream stays open this long; the page reconnects with the last event number it saw.
STREAM_S = 300


def install(ctx) -> None:
    from ..common.web import signed_in
    from .engine import Agent
    from .ops import register
    from .configuration import GovernedDeepSeek, configured_provider

    provider = getattr(ctx.api.state, "agent_provider", None) or configured_provider(ctx.store)
    agent = Agent(ctx.store, ctx.identity, ctx.capabilities, provider)
    if isinstance(provider, GovernedDeepSeek):
        provider.bind(agent)
    ctx.api.state.agent = agent
    register(ctx.registry, agent, ctx.store, ctx.identity)
    hooks = getattr(ctx.api.state, "shutdown_hooks", None)
    if hooks is not None:
        hooks.append(agent.close)
    if ctx.start_jobs:
        agent.recover()
    # A paid provider is installed only by trusted deployment composition. Recovery itself
    # never discovers credentials, enables billing, dispatches a model or replays a tool.
    from .gateway import MeteredTurnProvider
    if isinstance(agent.provider, MeteredTurnProvider):
        from .recovery import MeteringRecovery
        recovery = MeteringRecovery(gateway=agent.provider.gateway)
        ctx.api.state.agent_metering_recovery = recovery
        if hooks is not None:
            hooks.append(recovery.close)
        if ctx.start_jobs:
            recovery.start()
        # Upstream balance, breaker thresholds, reconciliation and ledger audit (billing/risk_monitor.py):
        # one round at a time across processes; nothing runs before a cost policy is published.
        from synwevo_core.billing.risk_monitor import RiskMonitor
        monitor = RiskMonitor(ctx.store, environment=agent.provider.gateway.ledger.environment)
        ctx.api.state.model_risk_monitor = monitor
        if hooks is not None:
            hooks.append(monitor.close)
        if ctx.start_jobs:
            monitor.start()

    @ctx.api.get("/api/suite/agent/conversations/{cid}/events", tags=["agent"])
    async def events(cid: str, request: Request, after: int = 0):
        """The conversation's events after ``after``, as server-sent events, for up to five minutes.
        The same rows the page loads on refresh — a reconnect resumes where it left off."""
        user = signed_in(request)
        agent.conversation(user["id"], cid)             # 404 for anyone but the owner
        uid = user["id"]
        session_cookie = request.cookies.get(COOKIE_NAME)

        def authorized():
            # A stream outlives its opening HTTP request. Logout, application suspension and
            # account revocation must apply to an already connected browser too.
            session = ctx.plat.sessions.load(session_cookie, touch=False)
            if not session or session["user_id"] != uid:
                raise ServiceError(401, "unauthorized", "请先登录")
            ctx.capabilities.authorize(ctx.registry.get("agent.conversation.get"), uid, None)
            agent.conversation(uid, cid)

        authorized()

        async def gen():
            try:
                resumed = int(request.headers.get("last-event-id", "0"))
            except ValueError:
                resumed = 0
            seq = max(0, int(after), resumed)
            started = time.monotonic()
            last_beat = started
            seen = -1
            last_check = 0.0
            yield "retry: 2000\n\n"
            while time.monotonic() - started < STREAM_S:
                if await request.is_disconnected():
                    return
                v = agent.version(cid)
                # Another process can write events without touching this process's signal.
                if v != seen or time.monotonic() - last_check >= 1:
                    try:
                        await asyncio.to_thread(authorized)
                    except ServiceError:
                        yield 'event: access.changed\ndata: {}\n\n'
                        return
                    seen = v
                    last_check = time.monotonic()
                    rows = await asyncio.to_thread(agent.events_after, uid, cid, seq)
                    for ev in rows:
                        seq = ev["seq"]
                        yield f"id: {seq}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if rows:
                        continue
                if time.monotonic() - last_beat > 15:
                    last_beat = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.2)
            yield "event: reconnect\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
