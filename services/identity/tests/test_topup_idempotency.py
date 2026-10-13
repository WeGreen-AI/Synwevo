"""Client retry regressions, using the isolated world and labelled provider test double only.
No socket, real credential, provider or payment is used.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from synwevo_core.billing.payment_settings import PaymentSettings
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_identity.payments import CREATING_GRACE_S, StripeCheckout, TopupService
from .test_wallet_topups import (world, post, deliver, session_event, cny_booked, SPEC, CNY,
                               FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET)

PATH = "/api/auth/wallet/topups"


def request_body(w, payer_kind="user", key="fixture-original-intent"):
    uid = w["users"]["alice"]["id"]
    pid = uid
    if payer_kind == "organization":
        pid = Organizations(w["store"], w["ident"]).create(
            uid, "Fixture Org", "Fixture WS", "fixture-retry-org")["organization"]["id"]
    return {"payer_kind": payer_kind, "payer_id": pid, "package_id": "cny-10", "idempotency_key": key,
            "initiator_id": uid, "environment": "test"}


@pytest.mark.parametrize("payer_kind", ["user", "organization"])
def test_concurrent_same_key_observes_one_dispatch_and_no_network_in_transaction(world, payer_kind):
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_pending": 1}})
    body = request_body(world, payer_kind)
    first, retry = world["login"]("alice"), world["login"]("alice")
    entered, release = threading.Event(), threading.Event()

    def blocked_provider(request):
        assert not getattr(world["store"]._local, "depth", 0), "provider must run outside Store.tx"
        entered.set()
        assert release.wait(5), "fixture must be released"
        return world["stripe"](request)

    world["api"].state.topups.provider_factory = lambda secret: StripeCheckout(secret, transport=httpx.MockTransport(blocked_provider))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, first, PATH, body)
        try:
            assert entered.wait(5)
            r = post(retry, PATH, body)
            assert r.status_code == 202 and r.json()["creation_status"] == "verifying"
            assert r.json()["checkout_url"] is None
            conflict = post(retry, PATH, {**body, "package_id": "cny-50"})
            assert conflict.status_code == 409 and conflict.json()["error"] == "topup_idempotency_conflict"
            assert world["store"].one("SELECT COUNT(*) n FROM payment_topups")["n"] == 1
        finally:
            release.set()
        completed = future.result(timeout=5)
    assert completed.status_code == 201
    assert completed.json()["order_id"] == r.json()["order_id"]
    assert len(world["stripe"].requests) == 1
    recovered = post(retry, PATH, body)
    assert recovered.status_code == 200 and recovered.json()["checkout_url"] == completed.json()["checkout_url"]


@pytest.mark.parametrize("failure", ["timeout", "truncated_body", "invalid_body"])
def test_unknown_provider_response_never_redispatches_and_late_evidence_credits_once(world, monkeypatch, failure):
    world["configure"]()
    body = request_body(world)
    secret_marker = "fixture-sensitive-upstream-body-not-for-response"

    def provider(request):
        created = world["stripe"](request)  # The isolated provider accepted creation before losing its response.
        if failure == "timeout":
            raise httpx.ReadTimeout(secret_marker, request=request)
        if failure == "truncated_body":
            return httpx.Response(200, content=b'{"secret":"' + secret_marker.encode())
        return httpx.Response(200, json={"secret": secret_marker, "id": created.json()["id"]})

    service = world["api"].state.topups
    service.provider_factory = lambda secret: StripeCheckout(secret, transport=httpx.MockTransport(provider))
    c = world["login"]("alice")
    first = post(c, PATH, body)
    assert first.status_code == 202 and first.json()["creation_status"] == "verifying"
    assert secret_marker not in first.text and FIXTURE_SECRET_KEY not in first.text
    oid = first.json()["order_id"]
    assert "failure_reason" not in first.json(), "an unknown outcome is not a refusal"
    started = service.clock()
    expires = world["store"].one("SELECT expires_at FROM payment_topups WHERE order_id=?", (oid,))["expires_at"]
    original_clock = service.clock
    # Until the requested expiry plus the grace, age alone is not evidence of anything.
    monkeypatch.setattr(service, "clock", lambda: expires + CREATING_GRACE_S - 1)
    for _ in range(2):
        retry = post(c, PATH, body)
        assert retry.status_code == 202 and retry.json()["order_id"] == oid
        assert retry.json()["status"] == "pending" and retry.json()["checkout_url"] is None
    assert c.get(f"{PATH}/{oid}").json()["creation_status"] == "verifying"
    # After it, any session that request created has expired at the provider: the order reads
    # expired/resolved, so the person can choose again. Still no second dispatch.
    monkeypatch.setattr(service, "clock", lambda: started + 7200)
    for _ in range(2):
        retry = post(c, PATH, body)
        assert retry.status_code == 200 and retry.json()["order_id"] == oid
        assert retry.json()["status"] == "expired" and retry.json()["creation_status"] == "resolved"
        assert retry.json()["checkout_url"] is None and "failure_reason" not in retry.json()
    assert c.get(f"{PATH}/{oid}").json()["status"] == "expired"
    assert world["store"].one("SELECT status FROM payment_topups WHERE order_id=?", (oid,))["status"] == "creating", \
        "reads never write"
    assert len(world["stripe"].requests) == 1 and cny_booked(world) == 0
    # Late signed evidence still credits, exactly once, even after the order read as expired.
    event = session_event(oid)
    assert deliver(world, event, at=started + 7200).json()["status"] == "processed"
    assert deliver(world, event, at=started + 7200).json()["status"] == "processed"
    monkeypatch.setattr(service, "clock", original_clock)
    paid = post(c, PATH, body).json()
    assert paid["order_id"] == oid and paid["status"] == "paid" and paid["checkout_url"] is None
    assert cny_booked(world) == 1000 * 10**4
    assert len(world["stripe"].requests) == 1


def test_key_is_scoped_to_actor_and_environment_and_ui_context_cannot_be_reinterpreted(world):
    world["configure"]()
    alice, bob = world["login"]("alice"), world["login"]("bob")
    body = request_body(world)
    one = post(alice, PATH, body)
    assert post(bob, PATH, body).json()["error"] == "topup_context_changed"
    assert post(alice, PATH, {**body, "environment": "live"}).json()["error"] == "topup_context_changed"
    other = post(bob, PATH, {"package_id": "cny-10", "idempotency_key": body["idempotency_key"]})
    assert other.status_code == 201 and one.json()["order_id"] != other.json()["order_id"]
    settings = PaymentSettings(world["store"], environment="live")
    with world["store"].tx():
        settings.append(SPEC, {"action": "replace", "secret_key": "sk_live_" + "fixture0not0a0real0key0000",
                              "webhook_secret": FIXTURE_WEBHOOK_SECRET}, expected_revision=0,
                        actor_id=world["users"]["carol"]["id"], reason="isolated live scope fixture", operation_id="fixture-live-settings")
    live = TopupService(world["store"], environment="live", units=[CNY],
                        provider_factory=lambda secret: StripeCheckout(secret, transport=httpx.MockTransport(world["stripe"])))
    result = live.create(initiator=world["users"]["alice"], payer_kind="user", payer_id=body["payer_id"],
                         package_id="cny-10", amount_minor=None, idempotency_key=body["idempotency_key"])
    assert result["order_id"] not in [one.json()["order_id"], other.json()["order_id"]]
    assert world["store"].one("SELECT COUNT(*) n FROM payment_topup_intents")["n"] == 3


@pytest.mark.parametrize("change", ["amount", "selector", "payer"])
def test_same_key_changed_original_request_conflicts_without_side_effect(world, change):
    world["configure"]()
    c = world["login"]("alice")
    body = request_body(world)
    first = post(c, PATH, body)
    if change == "amount":
        changed = {**body, "package_id": "cny-50"}
    elif change == "selector":
        changed = {k: v for k, v in body.items() if k != "package_id"}
        changed["amount_minor"] = 1000  # Same price is still a different original request.
    else:
        changed = request_body(world, "organization")
    r = post(c, PATH, changed)
    assert r.status_code == 409 and r.json()["error"] == "topup_idempotency_conflict"
    assert post(c, PATH, body).json()["order_id"] == first.json()["order_id"]
    assert len(world["stripe"].requests) == 1


def test_reprice_and_channel_changes_keep_original_order_terms_and_skip_challenge(world):
    world["configure"]()
    body = request_body(world)
    c = world["login"]("alice")
    first = post(c, PATH, body)
    world["configure"]({**SPEC, "packages": [{"id": "cny-10", "amount_minor": 1500, "label": "Fixture new price"}],
                        "payment_methods": ["card"]}, {"action": "keep"})
    service = world["api"].state.topups
    def forbidden_challenge():
        pytest.fail("an existing intent must not consume another challenge or dispatch")
    replay = service.create(initiator=world["users"]["alice"], payer_kind="user", payer_id=body["payer_id"],
                            package_id="cny-10", amount_minor=None, idempotency_key=body["idempotency_key"],
                            require_challenge=forbidden_challenge)
    assert replay["order_id"] == first.json()["order_id"] and replay["amount_minor"] == 1000
    assert replay["checkout_url"] == first.json()["checkout_url"]
    assert len(world["stripe"].requests) == 1


@pytest.mark.parametrize("change", ["close", "replace", "revoke"])
def test_current_configuration_never_releases_an_obsolete_checkout(world, change):
    world["configure"]()
    body = request_body(world)
    c = world["login"]("alice")
    first = post(c, PATH, body)
    if change == "replace":
        world["configure"]()  # A new credential revision, even if the fixture text happens to match.
    else:
        world["configure"]({**SPEC, "enabled": False}, {"action": "keep"} if change == "close" else
                            {"action": "revoke", "override_open_checkouts": True})
    reply = post(c, PATH, body)
    assert reply.status_code == 200 and reply.json()["creation_status"] == "blocked"
    assert reply.json()["checkout_url"] is None and reply.json()["order_id"] == first.json()["order_id"]
    assert len(world["stripe"].requests) == 1


@pytest.mark.parametrize("state", ["expired", "failed", "paid"])
def test_terminal_replays_never_release_old_urls(world, state):
    world["configure"]()
    c = world["login"]("alice")
    body = request_body(world)
    first = post(c, PATH, body).json()
    if state == "expired":
        world["store"].execute("UPDATE payment_topups SET expires_at=0 WHERE order_id=?", (first["order_id"],))
    else:
        event = session_event(first["order_id"], kind="checkout.session.async_payment_failed" if state == "failed" else "checkout.session.completed")
        assert deliver(world, event).json()["status"] == "processed"
    replay = post(c, PATH, body).json()
    assert replay["order_id"] == first["order_id"] and replay["status"] == state
    assert replay["checkout_url"] is None and replay["creation_status"] == "resolved"
    assert len(world["stripe"].requests) == 1


def test_org_initiator_loses_replay_and_order_read_access_when_billing_authority_is_removed(world):
    world["configure"]()
    body = request_body(world, "organization")
    c = world["login"]("alice")
    first = post(c, PATH, body).json()
    world["store"].execute("UPDATE organization_memberships SET role='member' WHERE organization_id=? AND user_id=?",
                           (body["payer_id"], body["initiator_id"]))
    replay = post(c, PATH, body)
    assert replay.status_code == 403 and replay.json()["error"] == "payer_forbidden"
    assert c.get(f"{PATH}/{first['order_id']}").status_code == 404
    assert len(world["stripe"].requests) == 1


@pytest.mark.parametrize("change", ["unverified", "inactive", "frozen"])
def test_current_personal_risk_checks_apply_on_replay(world, monkeypatch, change):
    world["configure"]()
    body = request_body(world)
    service = world["api"].state.topups
    args = dict(initiator=world["users"]["alice"], payer_kind="user", payer_id=body["payer_id"],
                package_id="cny-10", amount_minor=None, idempotency_key=body["idempotency_key"])
    service.create(**args)
    if change == "unverified":
        world["store"].execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (body["initiator_id"],))
    elif change == "inactive":
        world["store"].execute("UPDATE users SET status='suspended' WHERE id=?", (body["initiator_id"],))
    else:
        monkeypatch.setattr(service, "frozen", lambda *args: True)  # Explicit isolated wallet-risk test double.
    with pytest.raises(ServiceError) as exc:
        service.create(**args)
    assert exc.value.code == {"unverified": "email_verification_required", "inactive": "unauthorized", "frozen": "wallet_frozen"}[change]
    assert len(world["stripe"].requests) == 1


@pytest.mark.parametrize("change", ["revoke", "org_permission", "webhook_paid"])
def test_change_during_external_create_never_releases_an_unusable_or_unauthorized_url(world, change):
    world["configure"]()
    body = request_body(world, "organization" if change == "org_permission" else "user")
    c = world["login"]("alice")

    def provider(request):
        response = world["stripe"](request)
        # This executes on the provider call's thread, outside any DB transaction.
        assert not getattr(world["store"]._local, "depth", 0)
        if change == "revoke":
            world["configure"]({**SPEC, "enabled": False}, {"action": "revoke", "override_open_checkouts": True})
        elif change == "org_permission":
            world["store"].execute("UPDATE organization_memberships SET role='member' WHERE organization_id=? AND user_id=?",
                                   (body["payer_id"], body["initiator_id"]))
        else:
            oid = world["store"].one("SELECT order_id FROM payment_topups")["order_id"]
            assert deliver(world, session_event(oid)).json()["status"] == "processed"
        return response

    world["api"].state.topups.provider_factory = lambda secret: StripeCheckout(secret, transport=httpx.MockTransport(provider))
    result = post(c, PATH, body)
    if change == "org_permission":
        assert result.status_code == 403 and result.json()["error"] == "payer_forbidden"
    else:
        assert result.status_code == 201 and result.json()["checkout_url"] is None
        assert result.json()["creation_status"] == ("blocked" if change == "revoke" else "resolved")
        if change == "webhook_paid":
            assert result.json()["status"] == "paid" and cny_booked(world) == 1000 * 10**4
    assert len(world["stripe"].requests) == 1


def test_crash_after_intent_commit_does_not_recreate_or_dispatch_after_restart(world, monkeypatch):
    world["configure"]()
    body = request_body(world)
    service = world["api"].state.topups
    args = dict(initiator=world["users"]["alice"], payer_kind="user", payer_id=body["payer_id"],
                package_id="cny-10", amount_minor=None, idempotency_key=body["idempotency_key"])

    def crash(**kwargs):
        raise RuntimeError("fixture: crash before the ledger records its order")
    monkeypatch.setattr(service.ledger, "create_payment_order", crash)
    with pytest.raises(RuntimeError):
        service.create(**args)
    oid = world["store"].one("SELECT order_id FROM payment_topup_intents")["order_id"]
    restarted = TopupService(world["store"], environment="test", units=[CNY],
                             provider_factory=lambda secret: pytest.fail("retry must not dispatch"))
    replay = restarted.create(**args)
    assert replay["order_id"] == oid and replay["creation_status"] == "verifying"
    assert replay["checkout_url"] is None and not world["stripe"].requests
    assert world["store"].one("SELECT COUNT(*) n FROM payment_topups")["n"] == 1


@pytest.mark.parametrize("key", [None, "", "a b", "a/b", "x" * 201, [], 42])
def test_invalid_explicit_key_is_refused_without_creating(world, key):
    world["configure"]()
    r = post(world["login"]("alice"), PATH, {"package_id": "cny-10", "idempotency_key": key})
    assert r.status_code == 400 and r.json()["error"] == "topup_idempotency_invalid"
    assert not world["stripe"].requests
    assert not world["store"].all("SELECT * FROM payment_topups")
