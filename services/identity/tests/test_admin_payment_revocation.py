"""The finance administrator sees open top-up checkouts before revoking the payment keys, and an
explicit override is recorded (review payments-1; the rule itself: test_payment_key_revocation.py).

Fixtures only: fixture keys shaped like Stripe test-mode keys, an isolated data directory.
"""
from __future__ import annotations

import json

from .test_admin_payments import REASON, SPEC, publish, world  # noqa: F401 - fixture


def test_the_admin_sees_open_checkouts_and_an_override_is_audited(world):
    w = world
    c = w["login"]("fin")
    assert publish(c).status_code == 200
    store = w["store"]
    # FIXTURE: one open top-up checkout under the current keys.
    store.execute("INSERT INTO payment_topups(order_id,environment,provider,payer_kind,payer_id,initiator_id,amount_minor,"
                  "currency,unit,credit_units,settings_revision,status,created_at,expires_at) "
                  "VALUES ('top_fixture_open','test','stripe','user',?,?,1000,'CNY','CNY',10000000,1,'pending',?,?)",
                  (w["people"]["plain"]["id"], w["people"]["plain"]["id"], 1, 2**40))
    state = c.get("/api/admin/billing/payments").json()
    assert state["open_checkouts"] == {"count": 1, "until": 2**40}
    closed = {**SPEC, "enabled": False}
    r = publish(c, revision=1, spec=closed, credential={"action": "revoke"}, key="fixture_revoke_0001")
    assert r.status_code == 409 and r.json()["error"] == "payment_revoke_open_checkouts", r.text
    assert r.json()["detail"]["open_checkouts"] == 1
    r = publish(c, revision=1, spec=closed, credential={"action": "revoke", "override_open_checkouts": True},
                key="fixture_revoke_0002")
    assert r.status_code == 200, r.text
    assert r.json()["state"]["credential_configured"] is False
    meta = json.loads(store.one("SELECT meta FROM audit_events WHERE action='admin.billing.payments.publish' "
                                "AND result='ok' ORDER BY at DESC LIMIT 1")["meta"])
    assert meta["credential_action"] == "revoke" and meta["override_open_checkouts"] is True
    assert meta["open_checkouts"] == 1 and meta["reason"] == REASON
