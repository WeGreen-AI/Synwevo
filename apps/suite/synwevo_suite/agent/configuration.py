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
from synwevo_core.billing.org_payers import OrgPayers
from synwevo_core.billing.provider_settings import ProviderSettings
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.billing.traffic import ModelTraffic
from synwevo_core.billing.wallet_config import units_from_env
from synwevo_core.billing.welcome import WelcomeAllowance
from synwevo_core.errors import ServiceError

from .deepseek import upstream_base
from .gateway import MeteredDeepSeek, MeteredTurnProvider, Route
from .model_check import ModelCheck
from .provider import AnthropicProvider, Credential, ProviderError
from .quotes import RunQuotes

#: Test-only upstream origin for a labelled FIXTURE double; refused unless the billing
#: environment is "test" and the host is loopback (deepseek.upstream_base).
UPSTREAM_ENV = "SYNWEVO_DEEPSEEK_UPSTREAM"
#: How often GET /models re-validates the governed model id (also once at start).
MODEL_CHECK_ENV = "SYNWEVO_DEEPSEEK_MODEL_CHECK_SECONDS"
#: The closure states of a provider revision, as the refusal codes a person's turn carries. An
#: upstream-402 closure refuses with the platform breaker's own code (billing/risk.py), so the
#: chat says the same thing whichever mechanism closed paid dispatch.
CLOSURE_CODES = {"upstream_balance": "risk_breaker_open", "model_missing": "provider_model_missing"}


class GovernedDeepSeek(MeteredTurnProvider):
    def __init__(self, store, *, environment, upstream: str | None = None):
        self.settings = ProviderSettings(store, environment=environment)
        self.agent = None
        # Refuses (at startup) any override outside test or off loopback: the platform key
        # can only ever be sent to the official origin or a local FIXTURE double.
        self.base_url = upstream_base(environment, upstream)
        ledger = Ledger(store, environment=environment, namespace="agent")
        ledger.payer_budgets = PayerBudgets(ledger)
        # Organization payers: the organization's grant and monthly limits, checked by the ledger.
        ledger.org_payers = OrgPayers(ledger)
        costs = CostSettings(store, environment=environment)
        gateway = MeteredDeepSeek(ledger, current_authority=self.authorized,
            cost_admission=CostAdmission(ledger, current_policy=costs.policy),
            traffic=ModelTraffic(ledger, current_policy=costs.policy),
            base_url=self.base_url, route_guard=self.route_open, on_upstream_balance=self.upstream_balance)
        quotes = RunQuotes(gateway, current_route=self.route, current_authority=self.authorized)
        ledger.authorize = quotes.authorize
        # The one-time welcome allowance (off unless SYNWEVO_BILLING_WELCOME_MINOR and a CNY
        # wallet unit are configured), granted at the person's first Agent use.
        self.wallet_units = units_from_env()
        quotes.welcome = WelcomeAllowance.from_host(ledger, self.wallet_units)
        super().__init__(gateway, quotes=quotes, credential_resolver=self.credential)

    def route_open(self, route):
        """Refuse paid dispatch on a provider revision that a 402 or a model check closed.

        Flat and banded route versions both name their provider revision; a version that names
        none was not resolved from the governed configuration and is refused, never read as open."""
        revision = self.settings.route_revision(route.version)
        if revision is None:
            raise BillingError("model_configuration_changed")
        closure = self.settings.closure(revision)
        if closure is not None:
            raise BillingError(CLOSURE_CODES[closure["state"]])

    def upstream_balance(self, route):
        """The single seam an upstream HTTP 402 goes through: close paid dispatch on this
        revision (audited, administrators alerted) until an administrator publishes again.
        A platform-wide breaker that other controls share hooks in here as well."""
        self.settings.record_health(state="upstream_balance", requested_model=route.requested_model,
                                    reason_code="http_402", revision=self.settings.route_revision(route.version))

    def model_check(self, *, interval_seconds: float | None = None) -> ModelCheck:
        if interval_seconds is None:
            interval_seconds = float(os.environ.get(MODEL_CHECK_ENV, "3600"))
        return ModelCheck(self.settings, base_url=self.base_url, interval_seconds=interval_seconds,
                          transport=lambda: self.gateway.transport)

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
                or context.payer_kind not in ("personal", "organization") or context.delegation_id is not None):
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
        return GovernedDeepSeek(store, environment=os.environ.get("SYNWEVO_BILLING_ENVIRONMENT"),
                                upstream=os.environ.get(UPSTREAM_ENV))
    raise ValueError("unsupported SYNWEVO_AGENT_PROVIDER")
