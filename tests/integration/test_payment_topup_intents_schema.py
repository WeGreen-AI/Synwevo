"""Additive payment-intent migration and durable deduplication; isolated records, no provider."""
import sqlite3

import pytest

from synwevo_core.identity import Identity
from synwevo_core.store import Store


def order(store, uid, oid, environment="test"):
    store.execute(
        "INSERT INTO payment_topups(order_id,environment,provider,payer_kind,payer_id,initiator_id,"
        "amount_minor,currency,unit,credit_units,settings_revision,status,created_at) "
        "VALUES(?,?,'stripe','user',?,?,1000,'CNY','CNY:6',10000000,1,'creating',1)",
        (oid, environment, uid, uid),
    )


def intent(store, uid, oid, environment="test", key="fixture-intent"):
    store.execute(
        "INSERT INTO payment_topup_intents VALUES(?,?,?,?,?,1)",
        (environment, uid, key, "a" * 64, oid),
    )


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path / "payment-intent-schema")
    ident = Identity(store)
    uids = [ident.create_user(f"{name}@example.test", None, status="active",
                             with_personal_workspace=False)["id"] for name in ("alice", "bob")]
    return store, uids


def test_upgrade_keeps_legacy_orders_without_inventing_client_keys(fixture, tmp_path):
    store, (uid, _) = fixture
    order(store, uid, "legacy-order")
    before = store.one("SELECT * FROM payment_topups WHERE order_id='legacy-order'")
    store.execute("DROP TABLE payment_topup_intents")  # Previous schema; dropping removes its triggers.
    root = tmp_path / "payment-intent-schema"
    upgraded = Store(root)
    assert upgraded.one("SELECT * FROM payment_topups WHERE order_id='legacy-order'") == before
    assert upgraded.all("SELECT * FROM payment_topup_intents") == []
    intent(upgraded, uid, "legacy-order")
    again = Store(root)
    assert again.one("SELECT order_id FROM payment_topup_intents")["order_id"] == "legacy-order"
    # Older application code can still insert a top-up without an intent.
    order(again, uid, "older-worker-order")
    assert again.one("SELECT COUNT(*) n FROM payment_topups")["n"] == 2
    assert again.one("SELECT COUNT(*) n FROM payment_topup_intents")["n"] == 1


def test_equal_keys_are_scoped_by_environment_and_initiator(fixture):
    store, (alice, bob) = fixture
    for uid, oid, env in ((alice, "a-test", "test"), (alice, "a-live", "live"), (bob, "b-test", "test")):
        order(store, uid, oid, env)
        intent(store, uid, oid, env)
    assert store.one("SELECT COUNT(*) n FROM payment_topup_intents")["n"] == 3
    order(store, alice, "duplicate-test")
    with pytest.raises(sqlite3.IntegrityError, match="immutable_payment_intent"):
        intent(store, alice, "duplicate-test")


@pytest.mark.parametrize("sql", [
    "UPDATE payment_topup_intents SET request_hash='changed'",
    "DELETE FROM payment_topup_intents",
    "INSERT OR REPLACE INTO payment_topup_intents SELECT environment,initiator_id,idempotency_key,"
    "'changed',order_id,created_at FROM payment_topup_intents",
    "INSERT OR REPLACE INTO payment_topup_intents SELECT environment,initiator_id,'different-key',"
    "request_hash,order_id,created_at FROM payment_topup_intents",
])
def test_completed_binding_cannot_be_rewritten_or_recycled(fixture, sql):
    store, (uid, _) = fixture
    order(store, uid, "fixed-order")
    intent(store, uid, "fixed-order")
    before = store.one("SELECT * FROM payment_topup_intents")
    with pytest.raises(sqlite3.IntegrityError, match="immutable_payment_intent"):
        store.execute(sql)
    assert store.one("SELECT * FROM payment_topup_intents") == before


@pytest.mark.parametrize("mismatch", ["actor", "environment", "missing_order"])
def test_intent_cannot_point_to_another_actors_or_environments_order(fixture, mismatch):
    store, (alice, bob) = fixture
    order(store, alice, "original-order")
    with pytest.raises(sqlite3.IntegrityError, match="payment_intent_order_scope"):
        intent(store, bob if mismatch == "actor" else alice,
               "missing" if mismatch == "missing_order" else "original-order",
               "live" if mismatch == "environment" else "test")
    assert store.all("SELECT * FROM payment_topup_intents") == []
