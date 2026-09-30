"""Strict, provider-neutral money contracts. No prices or exchange rates are seeded."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Mapping

MAX_INTEGER = 2**63 - 1
MAX_TOKENS = 10**12


class BillingError(ValueError):
    """A caller-visible refusal; ``code`` is stable, messages contain no supplied payload."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def integer(value: int, *, positive: bool = False, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not (int(positive) <= value <= maximum):
        raise BillingError("invalid_integer")
    return value


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or value.strip() != value:
        raise BillingError("invalid_identifier")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BillingError("invalid_identifier")
    return value


@dataclass(frozen=True)
class Unit:
    code: str
    scale: int

    def __post_init__(self):
        identifier(self.code)
        integer(self.scale, maximum=18)


@dataclass(frozen=True)
class Rate:
    """Rational smallest-unit price per ``PriceSnapshot.per_tokens`` tokens."""

    numerator: int
    denominator: int = 1

    def __post_init__(self):
        integer(self.numerator)
        integer(self.denominator, positive=True, maximum=10**9)

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


@dataclass(frozen=True)
class Usage:
    input_uncached: int
    input_cached: int
    output: int
    reasoning: int = 0

    def __post_init__(self):
        for value in asdict(self).values():
            integer(value, maximum=MAX_TOKENS)
        if self.reasoning > self.output or self.input_uncached + self.input_cached > MAX_TOKENS:
            raise BillingError("inconsistent_usage")


def normalize_usage(raw: Mapping) -> Usage:
    """Validate final counters, never add aliases, totals or reasoning a second time.

    This validates arithmetic, NOT authenticity. The adapter must establish that this is
    final provider usage before passing it to the trusted settlement boundary. Missing cache
    classification is unknown, not an assumption that every input token missed the cache.
    """
    if not isinstance(raw, Mapping):
        raise BillingError("invalid_usage")

    def counter(obj, key):
        if not isinstance(obj, Mapping) or key not in obj:
            raise BillingError("missing_usage")
        return integer(obj[key], maximum=MAX_TOKENS)

    prompt, output = counter(raw, "prompt_tokens"), counter(raw, "completion_tokens")
    details = raw.get("prompt_tokens_details", {})
    completion = raw.get("completion_tokens_details", {})
    if not isinstance(details, Mapping) or not isinstance(completion, Mapping):
        raise BillingError("invalid_usage")
    hit = counter(raw, "prompt_cache_hit_tokens") if "prompt_cache_hit_tokens" in raw else None
    miss = counter(raw, "prompt_cache_miss_tokens") if "prompt_cache_miss_tokens" in raw else None
    alias = counter(details, "cached_tokens") if "cached_tokens" in details else None
    if hit is not None and alias is not None and hit != alias:
        raise BillingError("inconsistent_usage")
    hit = hit if hit is not None else alias
    if hit is None and miss is not None:
        hit = prompt - miss
    if hit is None:
        raise BillingError("missing_cache_classification")
    if miss is None:
        miss = prompt - hit
    if hit < 0 or miss < 0 or hit + miss != prompt:
        raise BillingError("inconsistent_usage")
    if "total_tokens" in raw and counter(raw, "total_tokens") != prompt + output:
        raise BillingError("inconsistent_usage")
    reasoning = counter(completion, "reasoning_tokens") if "reasoning_tokens" in completion else 0
    return Usage(miss, hit, output, reasoning)


@dataclass(frozen=True)
class PriceSnapshot:
    price_id: str
    purpose: str
    unit: Unit
    provider: str
    model: str
    input_uncached: Rate
    input_cached: Rate
    output: Rate
    terms_ref: str
    effective_from: int
    per_tokens: int = 1_000_000

    def __post_init__(self):
        for value in (self.price_id, self.provider, self.model, self.terms_ref):
            identifier(value)
        if self.purpose not in ("sale", "cost"):
            raise BillingError("invalid_price_purpose")
        if not isinstance(self.unit, Unit) or not all(isinstance(r, Rate) for r in
                                                     (self.input_uncached, self.input_cached, self.output)):
            raise BillingError("invalid_price")
        integer(self.effective_from)
        integer(self.per_tokens, positive=True, maximum=10**9)

    def exact(self, usage: Usage) -> Fraction:
        if not isinstance(usage, Usage):
            raise BillingError("invalid_usage")
        return (usage.input_uncached * self.input_uncached.fraction
                + usage.input_cached * self.input_cached.fraction
                + usage.output * self.output.fraction) / self.per_tokens

    @classmethod
    def from_dict(cls, data: dict) -> PriceSnapshot:
        data = dict(data)
        data["unit"] = Unit(**data["unit"])
        for key in ("input_uncached", "input_cached", "output"):
            data[key] = Rate(**data[key])
        return cls(**data)


@dataclass(frozen=True)
class ChargeContext:
    payer_user_id: str
    actor_user_id: str
    run_id: str
    authorization_ref: str
    workspace_id: str | None = None
    delegation_id: str | None = None
    payer_kind: str = "personal"

    def __post_init__(self):
        if self.payer_kind != "personal":
            raise BillingError("organization_billing_not_supported")
        for value in (self.payer_user_id, self.actor_user_id, self.run_id, self.authorization_ref):
            identifier(value)
        for value in (self.workspace_id, self.delegation_id):
            if value is not None:
                identifier(value)
        if self.payer_user_id != self.actor_user_id and self.delegation_id is None:
            raise BillingError("payment_delegation_required")


@dataclass(frozen=True)
class AttemptTicket:
    attempt_key: str
    owner: str
    fence: str


@dataclass(frozen=True)
class UsageEvidence:
    provider: str
    request_id: str
    evidence_ref: str
    usage: Usage
    effective_model: str
    capability_version: str
    metered_at: int

    def __post_init__(self):
        for value in (self.provider, self.request_id, self.evidence_ref,
                      self.effective_model, self.capability_version):
            identifier(value)
        if not isinstance(self.usage, Usage):
            raise BillingError("invalid_usage")
        integer(self.metered_at)


@dataclass(frozen=True)
class PaymentReceipt:
    """Input to a mandatory, injected verifier of durable, signature-verified payment facts.

    Constructing this object does NOT prove payment. Never expose ledger methods as routes.
    ``amount_minor`` is payment currency; ``credit_units`` come only from the fixed order.
    """

    environment: str
    provider: str
    merchant_id: str
    payment_id: str
    event_id: str
    order_id: str
    amount_minor: int
    currency: str
    evidence_ref: str
    payload_sha256: str
    status: str = "succeeded"

    def __post_init__(self):
        for key in ("provider", "merchant_id", "payment_id", "event_id", "order_id", "currency", "evidence_ref"):
            identifier(getattr(self, key))
        if self.environment not in ("test", "live") or self.status != "succeeded":
            raise BillingError("payment_not_succeeded")
        integer(self.amount_minor, positive=True)
        if (not isinstance(self.payload_sha256, str) or len(self.payload_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.payload_sha256)):
            raise BillingError("invalid_evidence_digest")


@dataclass(frozen=True)
class Balance:
    unit: Unit
    booked: int
    held: int

    @property
    def available(self) -> int:
        return self.booked - self.held


@dataclass(frozen=True)
class Settlement:
    call_key: str
    attempt_key: str
    charged: int
    absorbed: Fraction
    cost: Fraction
    cost_unit: Unit
    late: bool
    outcome: str
