"""Context binding through real quotes/consents and transaction rollback; isolated ledger.

The metered gateway fixture replaces the provider transport and never calls a live provider.
"""
import pytest

from synwevo_core.errors import ServiceError
from synwevo_suite.agent.context import ContextResolve, Contexts
from .test_agent_metering import gateway  # noqa: F401
from .test_agent_run_quotes import paid  # noqa: F401


def select(g):
    wid = g.agent.identity.create_workspace("Fixture context payer", owner_id=g.user["id"])["id"]
    def invoke(name, payload, key):
        return g.agent.caps.invoke(name, payload, user_id=g.user["id"], workspace_id=wid,
                                   via="ui", idempotency_key=key)["output"]
    invoke("agent.workspace_policy.set", {"processing": "platform", "providers": ["deepseek"]}, "context-policy")
    form = invoke("forms.form.create", {"title": "Fixture billed context"}, "context-form")
    g.agent.update_conversation(g.user["id"], g.conversation["id"], title=None, scope=[wid], status=None, head_id=None)
    reference = Contexts(g.agent).resolve(g.user["id"], ContextResolve(kind="form", workspace_id=wid, resource_id=form["id"]))["reference"]
    return reference


def start(g, refs, **auth):
    return g.agent.start_turn(g.user, g.conversation["id"], "fixture question", parent_id=None,
                              regenerate_of=None, context_refs=refs, **auth)


def test_quoted_context_cannot_be_omitted_or_replaced_and_failed_bind_is_atomic(paid):
    g = paid
    ref = select(g)
    quote = g.issue(context_refs=[ref])
    with pytest.raises(ServiceError) as caught:
        start(g, [], quote_id=quote["id"])
    assert caught.value.code == "billing_quote_context_changed"
    assert g.store.all("SELECT * FROM agent_messages") == []
    assert g.store.all("SELECT * FROM agent_turns") == []
    assert g.quotes.get(g.user["id"], quote["id"])["turn_id"] is None
    assert not g.seen and g.balance().held == 0
    turn = start(g, [ref], quote_id=quote["id"])
    g.agent._run(turn["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (turn["turn_id"],))["status"] == "succeeded"
    assert len(g.seen) == 1 and g.balance().booked == 958


def test_continuing_consent_issues_quote_bound_to_current_selected_resource(paid):
    g = paid
    ref = select(g)
    quote = g.issue(context_refs=[ref])
    with g.store.tx():
        consent = g.quotes.consents.create(g.user["id"], quote["id"], max_runs=2,
                                           expires_at=None, until_revoked=True)
    turn = start(g, [ref], consent_id=consent["id"])
    row = g.store.one("SELECT * FROM agent_model_quotes WHERE accepted_turn_id=?", (turn["turn_id"],))
    assert row and row["binding_digest"] == g.quotes._row(g.user["id"], quote["id"])["binding_digest"]
    assert g.store.one("SELECT consent_id FROM agent_quote_consents WHERE quote_id=?", (row["id"],))["consent_id"] == consent["id"]
    g.agent._run(turn["turn_id"])
    assert len(g.seen) == 1 and g.balance().booked == 958
