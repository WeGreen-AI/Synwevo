"""B3: nothing about an organization where Synwevo Message is switched off leaves by mail or push.

The account service's dispatcher re-checks every item at send time (`Notifier.stale_reason` →
`messages_closed`): a mention in one of the organization's groups, and a report of a message in it
(the managers' to-do), are skipped with `messages_disabled` — not deferred — while the inbox items
stay; nothing skipped is sent once Message is back on, and later items are mailed and pushed as
before. A frozen organization's report items are `organization_unavailable` (G2).

On the composed server, with the world of test_notifications_paused_workspace (its loopback push
double — no real push service is contacted — and its clock moved by writing `not_before`)."""
from __future__ import annotations

from synwevo_core.apps import MESSAGES_APP_ID, Apps
from synwevo_core.organizations import Organizations
from synwevo_core.push import PushSubscriptions
from tests.integration.test_notifications_paused_workspace import (  # noqa: F401
    M, REASON, SECRET, _device, _due, _leaked, _mails, _mention, _outcomes, world)


def _group(w) -> str:
    """An ordinary group of the organization (not a project's discussion): owner, ann and ben."""
    r = w["c"]["owner"].post(f"{M}/groups", json={"organization_id": w["oid"], "name": "Switch group",
                                                   "member_ids": [w["u"]["ann"]["id"], w["ben"]]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_a_mention_in_an_organization_with_message_off_is_neither_mailed_nor_pushed_nor_sent_later(world):
    w = world
    store, ben = w["store"], w["ben"]
    cid = _group(w)
    _mention(w, cid, SECRET, "ms-1")
    apps = Apps(store, w["api"].state.identity)
    assert apps.disable_for_organization(w["oid"], MESSAGES_APP_ID, w["u"]["owner"]["id"]) is True
    live = w["c"]["ben"].get(f"{M}/conversations/{cid}/messages")
    assert (live.status_code, live.json()["error"]) == (403, "messages_disabled")
    _due(store)
    out = w["dispatch"].run_once()
    assert out["queued"] == 0 and out["pushed"] == 0, out
    # the mention, and the item about being added to the group: both skipped, neither deferred
    for table in ("email", "push"):
        assert _outcomes(w, table) == [("conversation.added", "skipped", "messages_disabled"),
                                       ("conversation.mention", "skipped", "messages_disabled")], table
    assert _mails(w) == [] and w["double"].requests == []
    assert "conversation.mention" in [x["kind"] for x in w["api"].state.notifier.list(ben)["items"]], "the inbox item stays"
    # back on: what was skipped stays skipped; a new mention is mailed and pushed as before
    assert apps.enable_for_organization(w["oid"], MESSAGES_APP_ID, w["u"]["owner"]["id"]) is True
    hb = w["c"]["ben"]
    last = hb.get(f"{M}/conversations/{cid}").json()["last_seq"]
    assert hb.post(f"{M}/conversations/{cid}/read", json={"seq": last}).status_code == 200
    _mention(w, cid, "after-the-switch", "ms-2")
    _due(store)
    out = w["dispatch"].run_once()
    assert out["queued"] == 1 and out["pushed"] == 1, out
    assert _outcomes(w, "email")[1] == ("conversation.mention", "skipped", "messages_disabled"), "nothing skipped is sent later"
    assert _outcomes(w, "email")[-1] == ("conversation.mention", "queued", None)
    assert _outcomes(w, "push")[-1] == ("conversation.mention", "sent", None)
    assert _leaked(w, "after-the-switch") and not _leaked(w, SECRET)


def test_a_report_in_an_organization_with_message_off_or_frozen_is_neither_mailed_nor_pushed(world):
    w = world
    store, n, owner = w["store"], w["api"].state.notifier, w["u"]["owner"]["id"]
    n.set_preferences(owner, {"categories": {"conversation": {"email": "digest", "push": True}}})
    PushSubscriptions(store).add(owner, None, _device(f"{w['double'].base}/push/owner"))
    cid = _group(w)
    rids = []
    for client_id in ("mr-1", "mr-2"):
        r = w["c"]["ann"].post(f"{M}/conversations/{cid}/messages", json={"body": SECRET, "client_id": client_id})
        assert r.status_code == 201, r.text
        r = w["c"]["ben"].post(f"{M}/messages/{r.json()['message']['id']}/report", json={"reason": "privacy", "note": "fixture note"})
        assert r.status_code == 201, r.text
        rids.append(r.json()["id"])

    def reported(table: str) -> list[tuple]:
        return [x for x in _outcomes(w, table, owner) if x[0] == "conversation.reported"]

    assert reported("email") == [("conversation.reported", "pending", None)] * 2
    Apps(store, w["api"].state.identity).disable_for_organization(w["oid"], MESSAGES_APP_ID, owner)
    # only the first mail is due now (FIXTURE clock): the second waits in its digest window, and
    # meets the frozen organization later; push is not held back, so both pushes are checked now
    store.execute("UPDATE notification_deliveries SET not_before=? WHERE notification_id IN "
                  "(SELECT id FROM notifications WHERE object_id=?)", (0, rids[0]))
    w["dispatch"].run_once()
    assert reported("email") == [("conversation.reported", "skipped", "messages_disabled"), ("conversation.reported", "pending", None)]
    assert reported("push") == [("conversation.reported", "skipped", "messages_disabled")] * 2
    # G2: Message back on, the organization frozen — the report's items are skipped for that
    Apps(store, w["api"].state.identity).enable_for_organization(w["oid"], MESSAGES_APP_ID, owner)
    Organizations(store, w["api"].state.identity).platform_transition(w["oid"], owner, "freeze", reason=REASON)
    _due(store)
    w["dispatch"].run_once()
    assert reported("email")[1] == ("conversation.reported", "skipped", "organization_unavailable")
    assert _mails(w, "owner@example.test") == [] and "/push/owner" not in w["double"].requests
    # moderation goes on: the to-dos stay in the managers' inbox
    assert [x["kind"] for x in n.list(owner)["items"]].count("conversation.reported") == 2
