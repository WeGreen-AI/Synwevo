"""The one-time "start using Synwevo Agent" step (send-and-go, D-5) and the person's own
maximum for one answer (D-2), through the real Suite factory and capability layer.

``agent.setup.get`` says what is still missing before the first automatic paid run — the
separate provider data consent, the one-time terms acceptance and the personal spending limits
(prefilled from the suggested values) — and ``agent.terms.accept`` records the acceptance.
``agent.settings.update`` stores the cap; ``agent.billing.status.run`` shows the bound a send
would get. Synthetic prices/funds, fixture CNY unit, no network (the governed fixture refuses
any transport); nothing here starts a model call.
"""
import json
import uuid

import pytest

from synwevo_core.agent_safety import PROVIDER_TERMS, ProviderConsents
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store
from synwevo_core.suite_schema.agent import COLUMNS
from synwevo_core.terms import DOCUMENTS, TermsAcceptances
from synwevo_suite.agent.setup import SUGGESTED_LIMITS_FEN

from .test_agent_governed_factory import configure, fund, governed  # noqa: F401 - fixture
from .test_agent_payers import cny  # noqa: F401 - fixture

CURRENT = [{"id": d, "version": v} for d, (v, _) in DOCUMENTS.items()]


def invoke(g, op, payload=None, **kw):
    return g.suite.caps.invoke(op, payload or {}, user_id=g.user["id"], idempotency_key=uuid.uuid4().hex, **kw)


def refused(fn, *args, **kwargs):
    with pytest.raises(ServiceError) as err:
        fn(*args, **kwargs)
    return err.value


def setup(g):
    return invoke(g, "agent.setup.get")["output"]


# ------------------------------------------------------------------ what the step shows
def test_the_step_lists_only_what_is_missing_and_is_complete_once_each_item_is_done(cny, governed):
    g = governed
    configure(g, payer_budget=False)
    uid = g.user["id"]
    s = setup(g)
    assert s["required"] is True and s["complete"] is False
    # The People fixture accepted the DeepSeek consent at its current version (conftest).
    assert s["provider_consent"] == {"provider": "deepseek", "version": PROVIDER_TERMS["deepseek"].version,
                                     "accepted": True, "accepted_version": PROVIDER_TERMS["deepseek"].version}
    assert s["terms"]["complete"] is False
    assert s["terms"]["documents"] == [{"id": d, "version": v, "effective": e, "accepted": False, "accepted_version": None}
                                       for d, (v, e) in DOCUMENTS.items()]
    # Suggested limits are the owner's fen amounts converted exactly into the sale unit (CNY, 6).
    assert s["limits"] == {"configured": False, "enabled": False, "revision": 0, "unit": {"code": "CNY", "scale": 6},
                           "suggested": {"daily_budget": "10000000", "weekly_budget": "30000000", "monthly_budget": "100000000"}}
    assert SUGGESTED_LIMITS_FEN == {"daily_budget": 1_000, "weekly_budget": 3_000, "monthly_budget": 10_000}
    # (b) the terms, recorded with the step's operation run as context
    accepted = invoke(g, "agent.terms.accept", {"documents": CURRENT})
    assert accepted["output"] == {"complete": True, "documents": [{"id": d, "version": v, "effective": e, "accepted": True,
                                                                    "accepted_version": v}
                                                                   for d, (v, e) in DOCUMENTS.items()]}
    rows = TermsAcceptances(g.store).history(uid)
    assert {r["context"] for r in rows} == {"agent_setup:" + accepted["run_id"]} and len(rows) == 3
    s = setup(g)
    assert s["terms"]["complete"] is True and s["complete"] is False, "上限还没有设置"
    # (c) the limits, saved through the existing operation with the revision the step read
    saved = invoke(g, "agent.billing.budget.set", {"unit_code": "CNY", "expected_revision": s["limits"]["revision"], "enabled": True,
                                                    **s["limits"]["suggested"]})
    assert saved["status"] == "succeeded"
    s = setup(g)
    assert s["limits"]["configured"] is True and s["limits"]["enabled"] is True and s["limits"]["revision"] == 1
    assert s["complete"] is True
    # Repeating the acceptance at the same versions records nothing new.
    invoke(g, "agent.terms.accept", {"documents": CURRENT})
    assert len(TermsAcceptances(g.store).history(uid)) == 3
    # (a) withdrawing the provider consent reopens the step; the terms stay accepted.
    ProviderConsents(g.store).withdraw(uid, "deepseek")
    s = setup(g)
    assert s["provider_consent"]["accepted"] is False and s["provider_consent"]["accepted_version"] is None
    assert s["terms"]["complete"] is True and s["complete"] is False


def test_the_step_is_not_required_without_a_paid_model(suite):
    suite.sign_in("alice")
    out = suite.caps.invoke("agent.setup.get", {}, user_id=suite.people.uid("alice"))["output"]
    assert out["required"] is False and out["complete"] is True
    assert out["limits"] == {"configured": False, "enabled": False, "revision": 0, "unit": None, "suggested": None}
    assert [d["id"] for d in out["terms"]["documents"]] == list(DOCUMENTS)


def test_suggested_limits_are_never_converted_into_a_unit_that_is_not_the_cny_wallet(governed):
    g = governed
    configure(g, payer_budget=False)
    s = setup(g)
    assert s["limits"]["unit"] == {"code": "FIXFACT", "scale": 6} and s["limits"]["suggested"] is None
    assert s["required"] is True and s["complete"] is False


# ------------------------------------------------------------------ accepting the terms
def test_an_incomplete_stale_or_duplicated_acceptance_records_nothing_and_names_the_current_versions(cny, governed):
    g = governed
    configure(g)
    uid = g.user["id"]
    expected = [{"id": d, "version": v, "effective": e} for d, (v, e) in DOCUMENTS.items()]
    for documents in (CURRENT[:2],                                                     # one document missing
                      [{**d, "version": "1999-12-31"} if d["id"] == "terms" else d for d in CURRENT],   # stale version
                      [CURRENT[0], CURRENT[0], CURRENT[1]]):                           # duplicated
        err = refused(invoke, g, "agent.terms.accept", {"documents": documents})
        assert (err.status, err.code) == (409, "terms_version_changed"), documents
        assert err.detail == {"documents": expected}
    assert TermsAcceptances(g.store).history(uid) == []
    assert setup(g)["terms"]["complete"] is False
    for documents in ([], CURRENT + [CURRENT[0]]):                                     # none, or more than three
        err = refused(invoke, g, "agent.terms.accept", {"documents": documents})
        assert (err.status, err.code) == (422, "invalid_input")
    assert TermsAcceptances(g.store).history(uid) == []


@pytest.mark.parametrize("via", ["flow", "automation", "system", "api", "agent"])
def test_the_step_and_the_acceptance_are_a_persons_own_never_a_machines(cny, governed, via):
    g = governed
    configure(g)
    extra = {"scopes": frozenset()} if via == "api" else {}
    err = refused(invoke, g, "agent.terms.accept", {"documents": CURRENT}, via=via, **extra)
    assert (err.status, err.code) == (403, "not_for_agent" if via == "agent" else "person_required")
    assert TermsAcceptances(g.store).history(g.user["id"]) == []
    if via in ("api", "agent"):
        err = refused(invoke, g, "agent.setup.get", {}, via=via, **extra)
        assert (err.status, err.code) == (403, "not_for_agent" if via == "agent" else "person_required")
    else:
        # A read that grants nothing: an unattended caller may look, never accept.
        assert invoke(g, "agent.setup.get", {}, via=via)["output"]["terms"]["complete"] is False


# ------------------------------------------------------------------ the person's own maximum for one answer
def test_the_cap_is_stored_in_the_sale_unit_shown_in_settings_and_bounds_the_next_send(cny, governed):
    g = governed
    configure(g)
    uid = g.user["id"]
    offer = invoke(g, "agent.billing.offer")["output"]
    hold = int(offer["per_call_hold"])
    assert invoke(g, "agent.settings.get")["output"]["billing"] == {"required": True, "run_budget_cap": None}
    status = invoke(g, "agent.billing.status")["output"]
    assert status["run"] == {"maximum": str(hold * 12), "default": str(hold * 12), "cap": None, "source": "default",
                             "per_call_hold": str(hold), "max_calls": 12, "run_seconds": status["run"]["run_seconds"], "usable": True,
                             "price_id": offer["price_id"], "route_version": offer["route_version"],
                             "price_key": status["run"]["price_key"]}
    assert status["run"]["price_key"].startswith("pk1:"), "所付价格的摘要（addendum-2 S2）"
    assert 3600 - 60 <= status["run"]["run_seconds"] <= 3600, "min(3600, 路线剩余有效期)"
    invoke(g, "agent.settings.update", {"run_budget_cap": str(2 * hold)})
    assert invoke(g, "agent.settings.get")["output"]["billing"]["run_budget_cap"] == {"amount": str(2 * hold), "unit": {"code": "CNY", "scale": 6}}
    row = g.store.one("SELECT run_budget_cap, run_budget_cap_unit FROM agent_settings WHERE user_id=?", (uid,))
    assert row == {"run_budget_cap": str(2 * hold), "run_budget_cap_unit": "CNY"}
    run = invoke(g, "agent.billing.status")["output"]["run"]
    assert (run["maximum"], run["cap"], run["source"], run["usable"]) == (str(2 * hold), str(2 * hold), "personal_cap", True)
    # A cap above the default never raises the bound: the governed default is the ceiling.
    invoke(g, "agent.settings.update", {"run_budget_cap": str(100 * hold)})
    run = invoke(g, "agent.billing.status")["output"]["run"]
    assert (run["maximum"], run["cap"], run["source"]) == (str(12 * hold), str(100 * hold), "personal_cap")
    # Below one call's hold: stored (only a positive integer is checked here), shown as unusable.
    invoke(g, "agent.settings.update", {"run_budget_cap": str(hold - 1)})
    run = invoke(g, "agent.billing.status")["output"]["run"]
    assert run["usable"] is False and run["cap"] == str(hold - 1) and run["maximum"] == str(12 * hold)
    # Clearing returns to the default; a clear together with a value clears.
    invoke(g, "agent.settings.update", {"clear_run_budget_cap": True, "run_budget_cap": "7"})
    assert invoke(g, "agent.settings.get")["output"]["billing"]["run_budget_cap"] is None
    assert invoke(g, "agent.billing.status")["output"]["run"]["source"] == "default"
    for bad in ("0", "-5", "1.5", "abc", "", "1" * 20):
        err = refused(invoke, g, "agent.settings.update", {"run_budget_cap": bad})
        assert (err.status, err.code) == (422, "invalid_input"), bad
    assert invoke(g, "agent.settings.get")["output"]["billing"]["run_budget_cap"] is None


def test_the_cap_cannot_be_set_without_a_paid_model(suite):
    suite.sign_in("alice")
    uid = suite.people.uid("alice")
    err = refused(suite.caps.invoke, "agent.settings.update", {"run_budget_cap": "100"}, user_id=uid, idempotency_key="cap-free")
    assert (err.status, err.code) == (409, "billing_not_configured")
    assert suite.people.store.one("SELECT run_budget_cap FROM agent_settings WHERE user_id=?", (uid,)) is None
    out = suite.caps.invoke("agent.settings.get", {}, user_id=uid)["output"]
    assert out["billing"] == {"required": False, "run_budget_cap": None}


# ------------------------------------------------------------------ the schema
def test_the_cap_columns_are_added_to_an_existing_settings_table_without_touching_its_rows(tmp_path):
    assert ("agent_settings", "run_budget_cap", "TEXT") in COLUMNS and ("agent_settings", "run_budget_cap_unit", "TEXT") in COLUMNS
    root = tmp_path / "synthetic-pre-cap"
    store = Store(root)
    store.execute("INSERT INTO agent_settings (user_id,effort,memory_enabled,updated_at) VALUES ('u_fixture','high',1,1)")
    store.execute("UPDATE agent_settings SET run_budget_cap='5', run_budget_cap_unit='CNY' WHERE user_id='u_fixture'")
    before = store.one("SELECT user_id,effort,memory_enabled,updated_at FROM agent_settings")
    # Structurally downgrade only this synthetic fixture database, never a user database.
    for column in ("run_budget_cap", "run_budget_cap_unit"):
        store.execute(f"ALTER TABLE agent_settings DROP COLUMN {column}")
    store._local.c.close()
    store._local.c = None
    for _ in range(2):                                      # idempotent: a second open changes nothing
        migrated = Store(root)
        names = {r["name"] for r in migrated.all("SELECT name FROM pragma_table_info('agent_settings')")}
        assert {"run_budget_cap", "run_budget_cap_unit"} <= names
        row = migrated.one("SELECT user_id,effort,memory_enabled,updated_at,run_budget_cap,run_budget_cap_unit FROM agent_settings")
        assert {k: row[k] for k in before} == before
        assert row["run_budget_cap"] is None and row["run_budget_cap_unit"] is None, "没有上限 = 治理默认值"
        migrated._local.c.close()
        migrated._local.c = None


# ------------------------------------------------------------------ nothing here moves money
def test_the_step_reads_grant_nothing_and_start_no_call(cny, governed):
    g = governed
    configure(g, payer_budget=False)
    fund(g)
    before = {t: g.store.all(f"SELECT * FROM {t}") for t in ("billing_calls", "billing_runs", "agent_turns", "agent_model_quotes")}
    setup(g)
    invoke(g, "agent.terms.accept", {"documents": CURRENT})
    assert {t: g.store.all(f"SELECT * FROM {t}") for t in before} == before
    assert g.seen == []
    run = g.store.one("SELECT output FROM op_runs WHERE op='agent.terms.accept'")["output"]
    assert (json.loads(run) if isinstance(run, str) else run)["complete"] is True, "回执只有接受状态，没有业务正文"


# ------------------------------------------------------------------ first use or an update (S4, supports W5)
def recorded(g, document, version, at):
    """FIXTURE: an acceptance an earlier version of the step recorded (document, version, when)."""
    g.store.execute("INSERT INTO agent_terms_acceptances (user_id,document,version,accepted_at,context) VALUES (?,?,?,?,?)",
                    (g.user["id"], document, version, at, "fixture:earlier_step"))


def test_each_document_says_which_version_the_person_last_accepted(cny, governed):
    """The card words a first use and a re-acceptance differently: it needs the person's last
    accepted version of each document, not only whether the current one is accepted."""
    g = governed
    configure(g)
    first = {d["id"]: d for d in setup(g)["terms"]["documents"]}
    assert all(d["accepted"] is False and d["accepted_version"] is None for d in first.values()), "从未接受：首次使用"
    # An earlier version of every document, two of them accepted twice: the latest acceptance counts.
    for document in DOCUMENTS:
        recorded(g, document, "2026-09-01", 100)
    recorded(g, "terms", "2026-10-01", 200)
    recorded(g, "pricing", "2026-10-02", 150)
    again = {d["id"]: d for d in setup(g)["terms"]["documents"]}
    assert {d: (again[d]["accepted"], again[d]["accepted_version"]) for d in DOCUMENTS} == {
        "terms": (False, "2026-10-01"), "ai-use": (False, "2026-09-01"), "pricing": (False, "2026-10-02")}, "版本已更新：重新接受"
    accepted = invoke(g, "agent.terms.accept", {"documents": CURRENT})["output"]
    assert accepted["complete"] is True
    assert [(d["id"], d["accepted"], d["accepted_version"]) for d in accepted["documents"]] == [
        (d, True, v) for d, (v, _) in DOCUMENTS.items()], "接受后报告当前版本"
    assert [d["accepted_version"] for d in setup(g)["terms"]["documents"]] == [v for v, _ in DOCUMENTS.values()]
    # The version in force, once accepted, is what it reports, whatever was recorded after it.
    recorded(g, "terms", "2026-08-01", 2**40)
    assert [d["accepted_version"] for d in setup(g)["terms"]["documents"]] == [v for v, _ in DOCUMENTS.values()]
