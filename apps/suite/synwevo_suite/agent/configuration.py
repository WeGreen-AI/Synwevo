"""Trusted composition of governed model configuration, consent, costs and Agent authority.

Selecting DeepSeek on the host does not create a route, key, price, grant or balance.
Account governance publishes those settings; every actual send rechecks their revisions
and the person's current business rights under the ledger's transaction.
"""
from __future__ import annotations

import os

from synwevo_core.billing import BillingError, Ledger
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.provider_settings import ProviderSettings
from synwevo_core.errors import ServiceError

from .gateway import MeteredDeepSeek, MeteredTurnProvider, Route
from .provider import AnthropicProvider, Credential, ProviderError
from .quotes import RunQuotes


class GovernedDeepSeek(MeteredTurnProvider):
    def __init__(self, store, *, environment):
        self.settings = ProviderSettings(store, environment=environment)
        self.agent = None
        ledger = Ledger(store, environment=environment, namespace="agent")
        costs = CostSettings(store, environment=environment)
        gateway = MeteredDeepSeek(ledger, current_authority=self.authorized,
            cost_admission=CostAdmission(ledger, current_policy=costs.policy))
        quotes = RunQuotes(gateway, current_route=self.route, current_authority=self.authorized)
        ledger.authorize = quotes.authorize
        super().__init__(gateway, quotes=quotes, credential_resolver=self.credential)

    def bind(self, agent):
        if agent.store is not self.settings.store or agent.provider is not self or self.agent is not None:
            raise ValueError("model composition mismatch")
        self.agent = agent

    def credential(self, store, user_id):
        if store is not self.settings.store:
            return None
        try:
            resolved = self.settings.resolve()
        except (BillingError, ServiceError):
            # Corrupt/expired/revoked governance is closed, never an ambient-key fallback.
            return None
        if resolved is None:
            return None
        return Credential("platform", resolved.api_key, resolved.route["requested_model"],
                          provider=resolved.provider, revision=resolved.route["version"])

    def route(self):
        try:
            resolved = self.settings.resolve()
        except (BillingError, ServiceError):
            raise BillingError("model_configuration_unavailable") from None
        return Route(**resolved.route) if resolved is not None else None

    def authorized(self, context):
        agent = self.agent
        if (agent is None or agent._closed or context.actor_user_id != context.payer_user_id
                or context.payer_kind != "personal" or context.delegation_id is not None):
            return False
        store = self.settings.store
        turn = store.one("SELECT * FROM agent_turns WHERE id=? AND user_id=?",
                         (context.run_id, context.actor_user_id))
        if (not turn or turn["cancel_requested"] or
                turn["status"] not in ("queued", "running", "waiting_confirmation")):
            return False
        try:
            conv = agent.conversation(context.actor_user_id, turn["conversation_id"])
            if conv["status"] != "active":
                return False
            credential = self.credential(store, context.actor_user_id)
            agent._check_context(turn, credential)
        except (ProviderError, ServiceError, BillingError):
            return False
        return True


def configured_provider(store):
    # Existing deployments retain their configured Anthropic behavior. An unknown explicit
    # selection fails startup instead of accidentally sending data to a different provider.
    provider = os.environ.get("SYNWEVO_AGENT_PROVIDER", "anthropic")
    if provider == "anthropic":
        return AnthropicProvider()
    if provider == "deepseek":
        return GovernedDeepSeek(store, environment=os.environ.get("SYNWEVO_BILLING_ENVIRONMENT"))
    raise ValueError("unsupported SYNWEVO_AGENT_PROVIDER")
