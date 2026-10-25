"""The platform's MCP policy: versioned, non-secret, read on every decision.

Platform administrators decide whether the Agent may connect to external MCP servers at all,
whether external MCP clients may call Synwevo, which network destinations are allowed, how many
connections and concurrent calls a person or a workspace may have, how long a call may take, and
how external clients may register (Client ID Metadata Documents, or pre-registered clients only).

Revision 0 is the built-in default: both directions **off**, public destinations only, no
Client ID Metadata Documents. A deployment that has not decided releases nothing. Every edit
appends a complete snapshot inside the caller's transaction (the Account console's signed-preview
operation layer owns authorization, confirmation, audit and idempotency); there is no cache and
no environment override, so what is read is what was published.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ServiceError
from .store import Store, now

_MAX_REVISION = (1 << 63) - 1


class McpPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    #: The Agent may connect to external MCP servers (connections can be created and used).
    outbound_enabled: bool = False
    #: External MCP clients may call the Synwevo MCP server (tokens for its audience are honoured).
    inbound_enabled: bool = False
    #: Plain http to a destination that is not loopback. Off: https only.
    allow_http: bool = False
    #: Hosts or networks (CIDR) that may be connected to although they are not public addresses —
    #: an operator's own deployment on a private network. Empty: public addresses only.
    allow_private_networks: list[str] = Field(default_factory=list, max_length=100)
    #: Destination hosts the Agent may connect to (exact host or ".example.com" suffix). Null: any
    #: public host. An empty list allows nothing.
    allowed_hosts: list[str] | None = None
    #: Destination hosts refused whatever else says (same syntax as allowed_hosts).
    blocked_hosts: list[str] = Field(default_factory=list, max_length=200)
    max_connections_per_user: int = Field(default=10, ge=0, le=1000)
    max_connections_per_workspace: int = Field(default=20, ge=0, le=1000)
    max_concurrent_calls_per_connection: int = Field(default=4, ge=1, le=64)
    max_concurrent_calls_per_user: int = Field(default=8, ge=1, le=256)
    #: Wall-clock bound of one tool call / resource read / prompt render, seconds.
    call_timeout_s: int = Field(default=60, ge=5, le=600)
    #: Wall-clock bound of a connection test or capability refresh, seconds.
    discover_timeout_s: int = Field(default=30, ge=5, le=300)
    #: The most of a tool result that enters the model's context (characters of canonical JSON).
    max_result_chars: int = Field(default=24_000, ge=1_000, le=200_000)
    #: The most of a resource that is read (bytes).
    max_resource_bytes: int = Field(default=1_000_000, ge=10_000, le=50_000_000)
    #: Consecutive failures that open a connection's circuit, and for how long.
    breaker_failures: int = Field(default=5, ge=1, le=100)
    breaker_open_s: int = Field(default=120, ge=10, le=3600)
    #: How an external MCP client may register with the account service: by presenting a Client ID
    #: Metadata Document (its https URL is its client id), or only when an operator pre-registered it.
    client_registration: Literal["cimd_and_preregistered", "preregistered_only"] = "preregistered_only"
    #: Domains whose Client ID Metadata Documents are accepted (exact host or ".example.com" suffix).
    #: Empty: any https host.
    cimd_allowed_domains: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allow_private_networks", "allowed_hosts", "blocked_hosts", "cimd_allowed_domains")
    @classmethod
    def _host_entries(cls, value):
        if value is None:
            return value
        for item in value:
            if not isinstance(item, str) or not 1 <= len(item) <= 253 or any(ch.isspace() for ch in item):
                raise ValueError("invalid host entry")
        return value


def _host_matches(host: str, entries: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in entries:
        e = entry.lower().rstrip(".")
        if e.startswith("."):
            if host == e[1:] or host.endswith(e):
                return True
        elif host == e:
            return True
    return False


class McpPolicy:
    """Read and append the MCP policy. `append` runs inside the caller's `Store.tx()`."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def default() -> McpPolicySpec:
        return McpPolicySpec()

    @staticmethod
    def validate(spec) -> McpPolicySpec:
        try:
            return McpPolicySpec.model_validate(spec)
        except ValidationError as err:
            raise ServiceError(400, "mcp_policy_invalid", "MCP 策略不符合要求，请检查各项取值",
                               {"errors": [{"loc": [str(p) for p in e.get("loc", ())], "message": e.get("msg")}
                                           for e in err.errors()[:20]]}) from None

    def read(self) -> dict:
        row = self.store.one("SELECT * FROM mcp_policy_versions ORDER BY revision DESC LIMIT 1")
        if not row:
            return {"revision": 0, "spec": self.default().model_dump(), "actor_id": None, "reason": None,
                    "created_at": None, "source": "builtin_default"}
        try:
            spec = McpPolicySpec.model_validate(json.loads(row["spec_json"]))
        except (ValueError, ValidationError):
            # A damaged or future snapshot must not silently fall back to permissive defaults.
            raise ServiceError(503, "mcp_policy_unavailable", "MCP 策略记录暂不可用，请联系管理员检查") from None
        return {"revision": row["revision"], "spec": spec.model_dump(), "actor_id": row["actor_id"],
                "reason": row["reason"], "created_at": row["created_at"], "source": "published"}

    def current(self) -> McpPolicySpec:
        return McpPolicySpec.model_validate(self.read()["spec"])

    def append(self, spec, *, expected_revision: int, actor_id: str, reason: str,
               operation_id: str | None = None) -> dict:
        clean = self.validate(spec)
        if type(expected_revision) is not int or not 0 <= expected_revision <= _MAX_REVISION:
            raise ServiceError(400, "mcp_policy_bad_revision", "策略版本无效，请重新读取当前策略")
        current = self.read()
        if current["revision"] != expected_revision:
            raise ServiceError(409, "mcp_policy_revision_conflict", "MCP 策略已变化，请重新预览",
                               {"current": current})
        revision = expected_revision + 1
        self.store.execute(
            "INSERT INTO mcp_policy_versions (revision, spec_json, actor_id, reason, operation_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (revision, json.dumps(clean.model_dump(), sort_keys=True, separators=(",", ":")),
             actor_id, reason, operation_id, now()))
        return {"revision": revision, "spec": clean.model_dump()}

    def history(self, *, before_revision: int | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        if before_revision is None:
            rows = self.store.all("SELECT * FROM mcp_policy_versions ORDER BY revision DESC LIMIT ?", (limit,))
        else:
            rows = self.store.all("SELECT * FROM mcp_policy_versions WHERE revision<? ORDER BY revision DESC LIMIT ?",
                                  (int(before_revision), limit))
        out = []
        for r in rows:
            try:
                spec = json.loads(r["spec_json"])
            except ValueError:
                spec = None
            out.append({"revision": r["revision"], "spec": spec, "actor_id": r["actor_id"], "reason": r["reason"],
                        "operation_id": r["operation_id"], "created_at": r["created_at"]})
        return out

    # ------------------------------------------------------------------ decisions
    def destination_refusal(self, host: str) -> str | None:
        """Why the policy refuses connecting to `host`, or None. Address-level checks (private
        ranges, DNS) are the resolver's (`webhook_delivery.resolve_checked`)."""
        spec = self.current()
        if _host_matches(host, spec.blocked_hosts):
            return "mcp_host_blocked"
        if spec.allowed_hosts is not None and not _host_matches(host, spec.allowed_hosts):
            return "mcp_host_not_allowed"
        return None

    def cimd_allowed(self, host: str) -> bool:
        spec = self.current()
        if spec.client_registration != "cimd_and_preregistered":
            return False
        return not spec.cimd_allowed_domains or _host_matches(host, spec.cimd_allowed_domains)
