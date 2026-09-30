"""The real SSE generator honors live session authority and reconnect cursors.

No model substitute is required: the stream reads persisted events only.
"""
import asyncio
from types import SimpleNamespace

import pytest

from synwevo_core.sessions import COOKIE_NAME, Sessions


def setup_stream(suite, last_id="0"):
    suite.sign_in("alice")
    agent = suite.api.state.agent
    uid = suite.people.uid("alice")
    conversation = agent.create_conversation(uid, "stream verification", None)
    route = next(r for r in suite.api.routes if getattr(r, "path", None) == "/api/suite/agent/conversations/{cid}/events")

    async def disconnected():
        return False

    request = SimpleNamespace(state=SimpleNamespace(user=suite.people.users["alice"]),
                              cookies={COOKIE_NAME: suite.cookies.get(COOKIE_NAME)},
                              headers={"last-event-id": last_id}, is_disconnected=disconnected)
    return agent, uid, conversation["id"], route.endpoint, request


@pytest.mark.parametrize("revocation", ["session", "account", "app"])
def test_stream_stops_before_delivering_after_authority_revocation(suite, revocation):
    agent, uid, cid, endpoint, request = setup_stream(suite)
    agent._event(cid, uid, None, "text", {"delta": "must not leave after revocation"})

    async def scenario():
        response = await endpoint(cid, request)
        iterator = response.body_iterator
        assert await anext(iterator) == "retry: 2000\n\n"
        if revocation == "session":
            sessions = Sessions(suite.people.store)
            sessions.revoke(sessions.load(request.cookies[COOKIE_NAME])["id"], "test revoke")
        elif revocation == "account":
            suite.people.store.execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
        else:
            suite.people.store.execute("UPDATE apps SET status='disabled' WHERE id='agent'")
        event = await anext(iterator)
        assert "access.changed" in event
        assert "must not leave" not in event
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(scenario())


def test_last_event_id_resumes_after_persisted_cursor(suite):
    agent, uid, cid, endpoint, request = setup_stream(suite)
    agent._event(cid, uid, None, "tool", {"marker": "already received"})
    seq = agent.events_after(uid, cid, 0)[-1]["seq"]
    agent._event(cid, uid, None, "tool", {"marker": "new event"})
    request.headers["last-event-id"] = str(seq)

    async def scenario():
        response = await endpoint(cid, request, after=0)
        iterator = response.body_iterator
        await anext(iterator)
        event = await anext(iterator)
        assert "new event" in event
        assert "already received" not in event
        await iterator.aclose()

    asyncio.run(scenario())
