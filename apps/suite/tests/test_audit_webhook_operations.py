"""The audit trail of what is done with a webhook subscription, and of each delivery attempt
(admin coverage G26, decision D7 §8).

What a person does to *where* their events go — create, update, rotate, pause, resume, delete —
is in the trail already (test_machine_webhooks.py). This file covers the rest:

* a ping and a redelivery each leave one row, on the delivery they made (``webhook_delivery``),
  naming the person, the delegated client and the request; a refused one — rationed, not
  redeliverable, unknown — leaves none;
* the dispatcher's own stops (a receiver answering 410 Gone, failing too long, a grant that ended)
  leave one ``suite.webhook.disable`` row under the system actor, written in the transaction that
  stops the subscription, with what was dropped with it; a decision made on an earlier reading of
  the row (the version guard) stops nothing and records nothing;
* every attempt records on its delivery row which key signed it — a short fingerprint, never the
  secret and never the signature — and when, by the service's clock; the public delivery views
  carry neither, and a rotation changes the name of the key for the attempts that follow.

Fixtures, receivers and the clock are those of test_machine_webhooks.py. The rows are read from
``audit_events`` as the audit page would read them; signatures are checked with the independent
verifier of that file, not with the code under test.
"""
from __future__ import annotations

import json
import re

from .test_machine_webhooks import BASE, a_record, a_table, grant, hub, refused, signed_by  # noqa: F401  (hub is a fixture)

FINGERPRINT = re.compile(r"^[0-9a-f]{12}$")
PING, REDELIVER, DISABLE = "suite.webhook.ping", "suite.webhook.redeliver", "suite.webhook.disable"


def trail(store, *actions: str) -> list[dict]:
    """The webhook rows of the audit trail, oldest first, each with its ``meta`` parsed."""
    narrowed = f" AND action IN ({','.join('?' * len(actions))})" if actions else ""
    rows = store.all("SELECT * FROM audit_events WHERE action LIKE 'suite.webhook.%'" + narrowed + " ORDER BY seq", actions)
    return [{**r, "meta": json.loads(r["meta"])} for r in rows]


def by_alice_for_crm_sync(hub, row: dict) -> None:
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"], row["result"]) == ("user", hub.p.uid("alice"), hub.p.a, "ok")
    assert row["meta"]["delegation"]["kind"] == "oauth_client" and row["meta"]["delegation"]["client_id"] == "crm-sync"
    assert row["request_id"], "a row written for a request names the request"


def names_the_key_without_telling_it(row: dict, request: dict, *secrets: str) -> None:
    """The fingerprint says which key signed and nothing more: it is not the secret, not a piece
    of it, and not a piece of any signature the receiver was shown."""
    fingerprint = row["signing_key"]
    assert FINGERPRINT.match(fingerprint), fingerprint
    written = json.dumps(dict(row))
    for secret in secrets:
        assert fingerprint.lower() not in secret.lower() and secret.lower() not in fingerprint.lower()
        assert secret not in written and secret[len("whsec_"):] not in written, "the secret itself is not on the row"
    assert fingerprint.lower() not in request["headers"]["webhook-signature"].lower()
    assert fingerprint.lower() not in request["body"].decode().lower()


# --------------------------------------------------------------------------------- ping and redelivery
def test_ping_and_redeliver_leave_one_row_each_on_the_delivery(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid = made["id"]
    # a ping: one row, on the delivery the ping made
    ping = hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json()
    assert (ping["event_type"], ping["status"]) == ("webhook.ping", "delivered")
    (row,) = trail(hub.store, PING)
    by_alice_for_crm_sync(hub, row)
    assert (row["object_kind"], row["object_id"]) == ("webhook_delivery", ping["id"])
    assert row["object_id"] == hub.rows(sid)[-1]["id"], "the newest delivery is the ping's"
    assert row["meta"]["subscription_id"] == sid
    # a rationed ping sends nothing, records nothing in the log, and leaves no row in the trail
    refused(hub.api.post(f"{BASE}/{sid}/ping", headers=alice), 429, "ping_rate_limited")
    assert len(trail(hub.store, PING)) == 1 and len(hub.rows(sid)) == 1 and len(rx.received) == 1
    # a real event, delivered, then asked for again: one row, on the new delivery, naming the first
    a_table(hub.api, alice)
    assert hub.service.tick() == 1
    original = hub.api.get(f"{BASE}/{sid}/deliveries", headers=alice).json()["items"][0]
    assert (original["event_type"], original["status"]) == ("tables.table.changed", "delivered")
    again = hub.api.post(f"{BASE}/{sid}/deliveries/{original['id']}/redeliver", headers=alice)
    assert again.status_code == 202, again.text
    new = again.json()
    assert new["id"] != original["id"] and new["redelivery_of"] == original["id"]
    (row,) = trail(hub.store, REDELIVER)
    by_alice_for_crm_sync(hub, row)
    assert (row["object_kind"], row["object_id"]) == ("webhook_delivery", new["id"])
    assert (row["meta"]["subscription_id"], row["meta"]["redelivery_of"], row["meta"]["event_type"]) == (
        sid, original["id"], "tables.table.changed")
    # the redelivery goes out as any delivery does; the row stays one
    assert hub.service.tick() == 1 and len(rx.received) == 3
    assert hub.api.get(f"{BASE}/{sid}/deliveries/{new['id']}", headers=alice).json()["status"] == "delivered"
    # a refused redelivery leaves none: a ping is not redeliverable, an unknown delivery does not exist
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/{ping['id']}/redeliver", headers=alice), 409, "not_redeliverable")
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/whdel_000000000000/redeliver", headers=alice), 404, "delivery_not_found")
    assert len(hub.rows(sid)) == 3
    # the whole trail of this subscription, in order: the rows on the subscription itself are as before
    assert [(e["action"], e["object_kind"], e["object_id"]) for e in trail(hub.store)] == [
        ("suite.webhook.create", "webhook_subscription", sid), (PING, "webhook_delivery", ping["id"]),
        (REDELIVER, "webhook_delivery", new["id"])]
    written = json.dumps([dict(e) for e in trail(hub.store)])
    assert made["secret"] not in written and "Fixture contacts" not in written


# --------------------------------------------------------------------------------- the dispatcher's own stops
def test_the_dispatcher_records_the_subscriptions_it_stops(hub):
    alice = grant(hub.api, "alice")["auth"]
    gone, fine = hub.receiver(lambda n: 410), hub.receiver()
    stopped = hub.subscribe(alice, gone.url, events=["tables.table.changed"])["id"]
    kept = hub.subscribe(alice, fine.url, events=["tables.table.changed"])["id"]
    a_table(hub.api, alice, "First", "t1")
    a_table(hub.api, alice, "Second", "t2")
    hub.service.tick()
    # the receiver said "gone" to the first event; the second, waiting behind it, was dropped with the stop
    sub = hub.api.get(f"{BASE}/{stopped}", headers=alice).json()
    assert (sub["status"], sub["disabled_reason"]) == ("disabled", "gone")
    first, second = hub.rows(stopped)
    assert (first["status"], first["last_code"]) == ("failed", 410)
    assert (second["status"], second["last_error"]) == ("dropped", "subscription_disabled:gone")
    (row,) = trail(hub.store, DISABLE)
    assert (row["actor_kind"], row["actor_id"]) == ("system", "webhook-dispatcher")
    assert (row["workspace_id"], row["object_kind"], row["object_id"], row["result"]) == (
        hub.p.a, "webhook_subscription", stopped, "ok")
    assert row["meta"] == {"reason": "gone", "unsent": first["event_seq"], "dropped": 1,
                           "before": {"status": "active"}, "after": {"status": "disabled"}, "changed": ["status"]}
    # the subscription that was delivered to is not stopped and not written about
    assert len(fine.received) == 2 and hub.api.get(f"{BASE}/{kept}", headers=alice).json()["status"] == "active"
    # a decision made on an earlier reading of the row stops nothing and records nothing
    version = hub.api.patch(f"{BASE}/{kept}", json={"description": "changed since it was read"}, headers=alice).json()["version"]
    assert hub.service.disable(kept, "fixture_reason", version=version - 1) is False
    assert hub.api.get(f"{BASE}/{kept}", headers=alice).json()["status"] == "active"
    assert len(trail(hub.store, DISABLE)) == 1
    # on the row as it stands: one more row, with nothing waiting to drop
    assert hub.service.disable(kept, "fixture_reason", version=version) is True
    rows = trail(hub.store, DISABLE)
    assert len(rows) == 2 and (rows[1]["actor_kind"], rows[1]["actor_id"], rows[1]["object_id"]) == (
        "system", "webhook-dispatcher", kept)
    assert rows[1]["meta"] == {"reason": "fixture_reason", "unsent": None, "dropped": 0,
                               "before": {"status": "active"}, "after": {"status": "disabled"}, "changed": ["status"]}
    # stopping what is stopped — judged or not — changes nothing and records nothing
    assert hub.service.disable(kept, "fixture_reason", version=version + 1) is False
    assert hub.service.disable(stopped, "gone") is False
    assert len(trail(hub.store, DISABLE)) == 2
    # (a description is not where events go: that PATCH is not an event for the trail)
    assert [e["action"] for e in trail(hub.store)] == ["suite.webhook.create", "suite.webhook.create", DISABLE, DISABLE]


# --------------------------------------------------------------------------------- which key signed, and when
def test_each_attempt_records_which_key_signed_it_and_when(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500 if n == 3 else 200)
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid, first_secret = made["id"], made["secret"]
    table = a_table(hub.api, alice)
    assert hub.service.tick() == 1
    (first,) = hub.rows(sid)
    sent = rx.received[0]
    assert first["status"] == "delivered" and signed_by(first_secret, sent)
    names_the_key_without_telling_it(first, sent, first_secret)
    # when: the second the signature was made, by the service's clock — the one in the header
    assert float(first["signed_at"]).is_integer()
    assert int(first["signed_at"]) == int(sent["headers"]["webhook-timestamp"])
    assert abs(first["signed_at"] - hub.clock()) < 30
    # the public views of the log say nothing of either
    listed = hub.api.get(f"{BASE}/{sid}/deliveries", headers=alice)
    single = hub.api.get(f"{BASE}/{sid}/deliveries/{first['id']}", headers=alice)
    for response in (listed, single):
        assert response.status_code == 200
        assert "signing_key" not in response.text and "signed_at" not in response.text
        assert first["signing_key"] not in response.text
    assert single.json() == listed.json()["items"][0] and single.json()["id"] == first["id"]
    # a rotation: the attempts that follow name the new key — during the overlap too, when both
    # secrets sign, because the new one is the current one
    second_secret = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 600}, headers=alice).json()["secret"]
    hub.clock.advance(60)
    a_record(hub.api, alice, table, "r1")
    assert hub.service.tick() == 1
    overlap = hub.rows(sid)[1]
    sent = rx.received[1]
    assert signed_by(first_secret, sent) and signed_by(second_secret, sent)
    assert len(sent["headers"]["webhook-signature"].split()) == 2
    names_the_key_without_telling_it(overlap, sent, first_secret, second_secret)
    assert overlap["signing_key"] != first["signing_key"]
    assert int(overlap["signed_at"]) == int(sent["headers"]["webhook-timestamp"]) and abs(overlap["signed_at"] - hub.clock()) < 30
    # after the overlap only the new key signs, and the name is the one the overlap attempt had
    hub.clock.advance(600)
    a_record(hub.api, alice, table, "r2")
    hub.service.tick()
    later = hub.rows(sid)[2]
    sent = rx.received[2]
    assert signed_by(second_secret, sent) and not signed_by(first_secret, sent)
    assert (later["status"], later["attempts"], later["last_code"]) == ("pending", 1, 500)
    assert later["signing_key"] == overlap["signing_key"]
    assert int(later["signed_at"]) == int(sent["headers"]["webhook-timestamp"]) and later["signed_at"] - overlap["signed_at"] >= 600
    # each attempt: the retry of the same delivery records its own time, under the same key
    hub.clock.advance(later["next_attempt_at"] - hub.clock() + 1)
    assert hub.service.tick() == 1
    retried = hub.rows(sid)[2]
    sent = rx.received[3]
    assert (retried["status"], retried["attempts"], retried["last_code"]) == ("delivered", 2, 200)
    assert retried["signing_key"] == later["signing_key"]
    assert int(retried["signed_at"]) == int(sent["headers"]["webhook-timestamp"]) > int(later["signed_at"])
    names_the_key_without_telling_it(retried, sent, first_secret, second_secret)
    # earlier attempts keep their record: a rotation rewrites nothing that was already signed
    assert (hub.rows(sid)[0]["signing_key"], hub.rows(sid)[0]["signed_at"]) == (first["signing_key"], first["signed_at"])
    assert len({first["signing_key"], overlap["signing_key"]}) == 2
    # and still nothing of it in the public log
    page = hub.api.get(f"{BASE}/{sid}/deliveries", headers=alice).text
    for fingerprint in (first["signing_key"], overlap["signing_key"]):
        assert fingerprint not in page
    assert "signing_key" not in page and "signed_at" not in page
