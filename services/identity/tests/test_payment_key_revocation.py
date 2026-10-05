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
