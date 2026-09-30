"""Isolated price governance through actual identity HTTP and the shared ledger.

FIX_ units and rates below are synthetic test choices, not Synwevo retail prices.
No payment or provider request is made by this suite.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import sqlite3
import threading
import types
import uuid

import pytest
from fastapi.testclient import TestClient

from synwevo_core.billing import Ledger, Usage
from synwevo_core.billing.catalogue import PriceCatalogue
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store
from synwevo_identity import admin_model_prices

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, post, world

BASE = "/api/admin/billing/prices"
REASON = "Fixture: publish independently calculated test prices"
SPEC = {"name": "Fixture retail tariff", "purpose": "sale", "provider": "deepseek", "model": "fixture-route",
        "unit": {"code": "FIX_UNIT", "scale": 6}, "input_uncached": "0.30", "input_cached": "0.006",
        "output": "1.20", "terms_ref": "fixture-terms:v1", "effective_from": 1000, "previous_price_id": None}


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")


def create(w, spec=None, key=None):
    response = post(w["admin"], BASE + "/drafts", {"spec": spec or SPEC, "idempotency_key": key or uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def preview(w, draft):
    r = post(w["admin"], BASE + f"/drafts/{draft['id']}/preview", {"expected_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def command(plan):
    return {"expected_revision": plan["draft"]["revision"], "preview_token": plan["preview_token"],
            "idempotency_key": uuid.uuid4().hex, "reason": REASON}


def publish(w, draft, body=None, client=None, **kwargs):
    return post(client or w["admin"], BASE + f"/drafts/{draft['id']}/publish", body or command(preview(w, draft)), **kwargs)


def update(w, draft, spec=None, client=None):
    client = client or w["admin"]
    return client.patch(BASE + f"/drafts/{draft['id']}", json={"expected_revision": draft["revision"], "spec": spec or SPEC},
                        headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def test_no_seed_quote_preserves_money_precision_and_published_price_is_ledger_source(world):
    w = world
    assert w["admin"].get(BASE).json() == {"environment": "test", "items": [], "next_before": None}
    assert not w["store"].all("SELECT * FROM billing_units")
    draft = create(w)
    assert draft["status"] == "draft" and draft["price_id"] is None
    body = {"expected_revision": 1, "input_uncached": 1000, "input_cached": 1000, "output": 1000}
    quote = post(w["admin"], BASE + f"/drafts/{draft['id']}/calculate", body).json()
    assert quote == {"unit": {"code": "FIX_UNIT", "scale": 6}, "exact": {"numerator": "1506", "denominator": "1"},
                     "display_amount": "0.001506", "charge_units_if_no_prior_remainder": "1506", "per_tokens": 1000000}
    plan = preview(w, draft)
    assert plan["draft"] == draft and plan["environment"] == "test"
    assert not w["store"].all("SELECT * FROM billing_prices")
    result = publish(w, draft, command(plan))
    assert result.status_code == 200, result.text
    saved = result.json()["draft"]
    assert saved["status"] == "published" and saved["revision"] == 2
    price = PriceCatalogue(w["store"], environment="test").published(saved["price_id"])
    assert price.exact(Usage(1000, 1000, 1000)) == 1506
    ledger = Ledger(w["store"], environment="test", namespace="price-fixture")
    with ledger._tx() as t:
        assert ledger._price(t, saved["price_id"], "sale") == price
    for table in ("billing_journal", "billing_runs", "billing_attempts", "billing_orders", "billing_accounts"):
        assert not w["store"].all(f"SELECT * FROM {table}")


def test_exact_fractional_quote_is_not_rounded_up_per_small_call(world):
    draft = create(world, {**SPEC, "input_uncached": "0.000000001"})
    quote = post(world["admin"], BASE + f"/drafts/{draft['id']}/calculate",
        {"expected_revision": 1, "input_uncached": 1, "input_cached": 0, "output": 0}).json()
    assert quote["exact"] == {"numerator": "1", "denominator": "1000000000"}
    assert quote["display_amount"] == "0.000000000000001" and quote["charge_units_if_no_prior_remainder"] == "0"


def test_publish_idempotency_and_immutable_facts_and_new_version_rollback(world):
    w = world
    key = uuid.uuid4().hex
    draft = create(w, key=key)
    assert create(w, key=key) == draft
    body = command(preview(w, draft))
    first = publish(w, draft, body).json()
    assert publish(w, draft, body).json() == first
    assert create(w, key=key) == first["draft"]
    assert publish(w, draft, {**body, "reason": REASON + " changed"}).status_code == 409
    assert update(w, first["draft"], {**SPEC, "output": "2"}).json()["error"] == "price_published"
    for sql in ("UPDATE model_price_drafts SET status='draft'", "DELETE FROM model_price_publications",
                "UPDATE billing_prices SET snapshot='{}'",
                "INSERT OR REPLACE INTO model_price_publications SELECT * FROM model_price_publications",
                "INSERT OR REPLACE INTO model_price_drafts SELECT * FROM model_price_drafts"):
        with pytest.raises(sqlite3.IntegrityError):
            w["store"].execute(sql)
    restored = create(w, {**SPEC, "previous_price_id": first["draft"]["price_id"], "effective_from": 2000})
    second = publish(w, restored).json()
    assert second["draft"]["price_id"] != first["draft"]["price_id"]
    assert len(w["store"].all("SELECT * FROM billing_prices")) == 2
    assert len(w["store"].all("SELECT * FROM admin_operations WHERE action='admin.model_price.publish'")) == 2


def test_draft_stale_revision_and_precise_unit_conflict_are_rejected(world):
    w = world
    draft = create(w)
    old = command(preview(w, draft))
    updated = update(w, draft, {**SPEC, "output": "2"}).json()
    assert updated["revision"] == 2
    stale = publish(w, draft, old)
    assert stale.status_code == 409 and stale.json()["error"] == "price_revision_conflict"
    assert update(w, draft).json()["detail"]["current"] == updated
    other = create(w, {**SPEC, "unit": {"code": "FIX_UNIT", "scale": 2}})
    assert publish(w, updated).status_code == 200
    assert publish(w, other, {**old, "idempotency_key": uuid.uuid4().hex}).status_code == 409
    refusal = post(w["admin"], BASE + f"/drafts/{other['id']}/preview", {"expected_revision": 1})
    assert refusal.status_code == 409 and refusal.json()["error"] == "price_unit_conflict"


@pytest.mark.parametrize("change", ["actor", "origin", "signature", "expired", "environment", "draft", "revision"])
def test_preview_cannot_change_authority_environment_snapshot_or_target(world, monkeypatch, change):
    w = world
    draft, other = create(w), create(w)
    plan = preview(w, draft)
    body, kwargs, target = command(plan), {}, draft
    if change == "actor":
        actor = make_platform_admin(w["store"], w["ident"].create_user("price-other@example.test", PW, status="active"))
        client = w["login"](actor)
        w["reauth"](client)
        kwargs["client"] = client
    elif change == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif change == "signature":
        body["preview_token"] += "tamper"
    elif change == "expired":
        monkeypatch.setattr(admin_model_prices, "time", types.SimpleNamespace(time=lambda: plan["expires_at"]+1))
    elif change == "environment":
        signer = admin_model_prices.PriceOperations(w["store"], None, "test")
        data = signer._verify(body["preview_token"])
        data["environment"] = "live"
        body["preview_token"] = signer._sign(data)
    elif change == "draft":
        target = other
    else:
        body["expected_revision"] = 2
    result = publish(w, target, body, **kwargs)
    assert result.status_code == 409, result.text
    assert not w["store"].all("SELECT * FROM billing_prices")


def test_permission_fresh_auth_csrf_and_revoked_replay(world):
    w = world
    assert w["ordinary"].get(BASE).status_code == 404
    assert post(w["ordinary"], BASE+"/drafts", {"spec": SPEC, "idempotency_key": uuid.uuid4().hex}).status_code == 404
    actor = make_platform_admin(w["store"], w["ident"].create_user("price-admin@example.test", PW, status="active"))
    client = w["login"](actor)
    draft = create(w)
    plan = post(client, BASE+f"/drafts/{draft['id']}/preview", {"expected_revision": 1}).json()
    body = command(plan)
    assert publish(w, draft, body, client=client).json()["error"] == "reauth_required"
    w["reauth"](client)
    assert client.post(BASE+f"/drafts/{draft['id']}/publish", json=body).status_code == 403
    assert publish(w, draft, body, client=client).status_code == 200
    end_platform_grants(w["store"], actor)
    assert publish(w, draft, body, client=client).status_code == 404


def test_audit_failure_rolls_back_price_publication_and_allows_safe_retry(world, monkeypatch):
    w = world
    draft = create(w)
    body = command(preview(w, draft))
    original = Store.execute
    def fail(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and "admin.model_price.publish" in params and "ok" in params:
            raise OSError("fixture secret failure")
        return result
    monkeypatch.setattr(Store, "execute", fail)
    assert publish(w, draft, body).status_code == 500
    for table in ("billing_prices", "billing_units", "model_price_publications", "admin_operations"):
        assert not w["store"].all(f"SELECT * FROM {table}")
    assert w["admin"].get(BASE + f"/drafts/{draft['id']}").json() == draft
    assert "fixture secret failure" not in json.dumps(w["store"].all("SELECT * FROM audit_events"))
    monkeypatch.setattr(Store, "execute", original)
    assert publish(w, draft, body).status_code == 200


@pytest.mark.parametrize("withdrawal", ["permission", "session", "freshness"])
def test_publication_rechecks_authority_after_acquiring_writer_lock(world, monkeypatch, withdrawal):
    w = world
    draft = create(w)
    body = command(preview(w, draft))
    original, happened = admin_model_prices.require_platform, False
    def admitted(request, *args, **kwargs):
        nonlocal happened
        actor = original(request, *args, **kwargs)
        if request.url.path.endswith("/publish") and not happened:
            happened = True
            separate = Store(w["store"].root)
            if withdrawal == "permission":
                end_platform_grants(separate, w["root"])
            else:
                session = Sessions(separate).load(w["admin"].cookies.get(COOKIE_NAME), touch=False)
                if withdrawal == "session":
                    Sessions(separate).revoke(session["id"], "fixture_logout")
                else:
                    separate.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE id=?", (session["id"],))
        return actor
    monkeypatch.setattr(admin_model_prices, "require_platform", admitted)
    response = publish(w, draft, body)
    assert response.status_code == {"permission": 404, "session": 401, "freshness": 403}[withdrawal]
    assert not w["store"].all("SELECT * FROM billing_prices")
    assert not w["store"].all("SELECT * FROM model_price_publications")


@pytest.mark.parametrize("same_key", [False, True])
def test_two_instances_publish_one_immutable_price(world, same_key):
    w = world
    from synwevo_identity.app import create_app
    client = TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False)
    client.cookies.update(w["admin"].cookies)
    draft = create(w)
    first = command(preview(w, draft))
    second = dict(first) if same_key else command(preview(w, draft))
    barrier = threading.Barrier(2, timeout=10)
    def run(args):
        peer, body = args
        barrier.wait()
        return publish(w, draft, body, client=peer)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [(w["admin"], first), (client, second)]))
    client.close()
    assert sorted(r.status_code for r in results) == ([200, 200] if same_key else [200, 409]), [r.text for r in results]
    assert len(w["store"].all("SELECT * FROM billing_prices")) == 1
    assert len(w["store"].all("SELECT * FROM model_price_publications")) == 1


def test_price_openapi_is_typed_and_matches_runtime_response(world):
    schema = world["api"].openapi()
    paths, models = schema["paths"], schema["components"]["schemas"]
    for path, methods in paths.items():
        if not path.startswith(BASE):
            continue
        for method, operation in methods.items():
            if method not in {"get", "post", "patch"}:
                continue
            assert "422" not in operation["responses"]
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            if method in {"post", "patch"}:
                ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
                contract = models[ref.rsplit("/", 1)[1]]
                assert contract["additionalProperties"] is False and contract["required"]
    create_schema = models["PriceCreate"]
    spec_name = create_schema["properties"]["spec"]["$ref"].rsplit("/", 1)[1]
    assert models[spec_name]["properties"]["output"]["type"] == "string"
    assert models[spec_name]["properties"]["effective_from"]["type"] == "integer"
    draft = create(world)
    assert admin_model_prices.PriceDraft.model_validate(draft).model_dump() == draft
    quote = post(world["admin"], BASE+f"/drafts/{draft['id']}/calculate",
        {"expected_revision": 1, "input_uncached": 1, "input_cached": 0, "output": 0})
    assert admin_model_prices.PriceCalculation.model_validate(quote.json()).model_dump() == quote.json()


@pytest.mark.parametrize("body", [None, ["fixture-secret"], "fixture-secret", {"spec": "fixture-secret"}])
def test_malformed_typed_price_body_never_echoes_input(world, body):
    response = post(world["admin"], BASE+"/drafts", body)
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "price_invalid" and "fixture-secret" not in response.text
    assert not world["store"].all("SELECT * FROM model_price_drafts")


def test_typed_price_revision_and_usage_do_not_coerce_strings_or_booleans(world):
    draft = create(world)
    url = BASE+f"/drafts/{draft['id']}"
    for revision in (True, "1"):
        result = post(world["admin"], url+"/preview", {"expected_revision": revision})
        assert result.status_code == 400
    for usage in (True, "100", 1.2, 10**9+1):
        result = post(world["admin"], url+"/calculate",
            {"expected_revision": 1, "input_uncached": usage, "input_cached": 0, "output": 0})
        assert result.status_code == 400
    assert not world["store"].all("SELECT * FROM billing_prices")


@pytest.mark.parametrize("field,value", [("output", 1.2), ("output", "NaN"), ("output", "1e3"), ("output", "-1"),
    ("output", "0.1234567891"), ("effective_from", True), ("provider", "arbitrary-provider"),
    ("unit", {"code": "FIX_UNIT", "scale": True}), ("unit", {"code": "FIX_UNIT", "scale": 18}),
    ("terms_ref", "fixture-secret?api_key=123"), ("key", "fixture-secret")])
def test_invalid_metadata_never_changes_money_or_echoes_untrusted_values(world, field, value):
    spec = copy.deepcopy(SPEC)
    spec[field] = value
    r = post(world["admin"], BASE+"/drafts", {"spec": spec, "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 400, r.text
    assert "fixture-secret" not in r.text
    assert not world["store"].all("SELECT * FROM model_price_drafts")
    assert "fixture-secret" not in json.dumps(world["store"].all("SELECT * FROM audit_events"))


def test_environment_partition_read_pagination_and_unconfigured_are_real(world, monkeypatch):
    w = world
    drafts = [create(w) for _ in range(3)]
    first = w["admin"].get(BASE+"?limit=2").json()
    assert [x["id"] for x in first["items"]] == [d["id"] for d in reversed(drafts[1:])]
    last = w["admin"].get(BASE+"?limit=2&before="+first["next_before"]).json()
    assert last["items"] == [drafts[0]] and last["next_before"] is None
    assert PriceCatalogue(w["store"], environment="live").list()["items"] == []
    from synwevo_identity.app import create_app
    monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT")
    client = TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False)
    client.cookies.update(w["admin"].cookies)
    assert client.get(BASE).json() == {"environment": None, "items": [], "next_before": None}
    response = post(client, BASE+"/drafts", {"spec": SPEC, "idempotency_key": uuid.uuid4().hex})
    assert response.status_code == 503 and response.json()["error"] == "billing_not_configured"
    client.close()
