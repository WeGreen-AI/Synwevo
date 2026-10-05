"""Revoking the payment keys cannot silently strand customers who are paying (review payments-1).

Business rule: a customer who completes a checkout that was open when the keys were revoked must
still be credited, unless an administrator explicitly decided otherwise. Revoking deletes the
webhook signing secret, after which no delivery can be verified, so revocation is refused while
any top-up checkout can still be paid; closing top-ups (keeping the keys) is always possible,
and once those checkouts have expired the revocation goes through. An administrator who must
revoke at once (e.g. the signing secret itself leaked) says so explicitly; that is recorded.

Fixtures only: the Stripe API is an httpx.MockTransport, deliveries are signed with a fixture
secret, keys are fixture values shaped like Stripe test-mode keys; isolated data directory.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from synwevo_core.errors import ServiceError

from .test_wallet_topups import SPEC, cny_booked, created, deliver, session_event, world  # noqa: F401 - fixture

CLOSED = {**SPEC, "enabled": False}


def test_revoking_while_a_checkout_is_open_is_refused_and_the_paying_customer_is_credited(world):
    world["configure"]()
    c, order, session = created(world)                     # a checkout is open (up to an hour)
    with pytest.raises(ServiceError) as refused:
        world["configure"](CLOSED, {"action": "revoke"})
    assert refused.value.code == "payment_revoke_open_checkouts"
    assert refused.value.detail["open_checkouts"] == 1 and refused.value.detail["open_until"]
    # Closing top-ups while keeping the keys is always possible...
    world["configure"](CLOSED, {"action": "keep"})
    # ...and the customer who pays the open checkout is credited.
    assert deliver(world, session_event(order, session_id=session)).json()["status"] == "processed"
    assert cny_booked(world) == 10**7


def test_once_the_open_checkouts_have_expired_the_keys_can_be_revoked(world):
    world["configure"]()
    c, order, session = created(world)
    world["store"].execute("UPDATE payment_topups SET expires_at=1 WHERE order_id=?", (order,))   # FIXTURE: expired
    world["configure"](CLOSED, {"action": "revoke"})
    assert world["api"].state.topups.settings.webhook_secret() is None


def test_an_explicit_override_revokes_at_once(world):
    world["configure"]()
    created(world)
    world["configure"](CLOSED, {"action": "revoke", "override_open_checkouts": True})
    assert world["api"].state.topups.settings.webhook_secret() is None
    with pytest.raises(ServiceError) as invalid:          # an override means nothing for another action
        world["configure"](CLOSED, {"action": "keep", "override_open_checkouts": True})
    assert invalid.value.code == "payment_config_invalid"


@pytest.mark.parametrize("change", ["revoke", "close", "reprice"])
def test_configuration_change_before_order_admission_prevents_a_checkout(world, monkeypatch, change):
    world["configure"]()
    topups = world["api"].state.topups
    ready, resume = Event(), Event()
    original = topups.frozen

    def after_configuration(*args):
        result = original(*args)
        ready.set()
        assert resume.wait(10), "fixture checkout was not resumed"
        return result

    monkeypatch.setattr(topups, "frozen", after_configuration)
    user = world["users"]["alice"]
    with ThreadPoolExecutor(1) as pool:
        creating = pool.submit(topups.create, initiator=user, payer_kind="user", payer_id=user["id"],
                               package_id="cny-10", amount_minor=None)
        try:
            assert ready.wait(10)
            assert topups.settings.open_checkouts()["count"] == 0
            if change == "reprice":
                world["configure"]({**SPEC, "packages": [{"id": "cny-10", "amount_minor": 1200, "label": None}]},
                                   {"action": "keep"})
            else:
                world["configure"](CLOSED, {"action": "revoke" if change == "revoke" else "keep"})
        finally:
            resume.set()
        with pytest.raises(ServiceError) as refused:
            creating.result(timeout=10)
    assert refused.value.code == ("payment_revision_conflict" if change == "reprice" else "topup_not_configured")
    assert world["stripe"].requests == []
    assert world["store"].all("SELECT * FROM payment_topups") == []
    assert world["store"].all("SELECT * FROM billing_orders") == []


def test_order_admission_before_revocation_keeps_the_payment_verifiable(world, monkeypatch):
    world["configure"]()
    topups = world["api"].state.topups
    ready, resume = Event(), Event()
    original = topups.provider_factory

    def before_provider(secret):
        ready.set()
        assert resume.wait(10), "fixture checkout was not resumed"
        return original(secret)

    monkeypatch.setattr(topups, "provider_factory", before_provider)
    user = world["users"]["alice"]
    with ThreadPoolExecutor(1) as pool:
        creating = pool.submit(topups.create, initiator=user, payer_kind="user", payer_id=user["id"],
                               package_id="cny-10", amount_minor=None)
        try:
            assert ready.wait(10)
            with pytest.raises(ServiceError) as refused:
                world["configure"](CLOSED, {"action": "revoke"})
            assert refused.value.code == "payment_revoke_open_checkouts"
            world["configure"](CLOSED, {"action": "keep"})
        finally:
            resume.set()
        order = creating.result(timeout=10)["order_id"]
    session = topups._row(order)["checkout_session_id"]
    assert deliver(world, session_event(order, session_id=session)).json()["status"] == "processed"
    assert cny_booked(world) == 10**7
