"""Connections to external MCP servers: whose they are, what they offer, who may use which tool,
and every call made through them.

A connection belongs to one person or to one workspace. The person manages their own; a
workspace's owners and administrators manage the workspace's, and its members may use it — while
the workspace is open to them as to every native operation (an active workspace of an active
organization they still belong to: `organizations.workspace_access_failure`). Using a
connection in the Agent is a second, personal decision (`mcp_agent_bindings`): nobody's Agent
gains tools because a workspace they belong to added a server. Each tool has a grant on the
connection (`off`, `confirm`, `auto`) pinned to the schema it was made for; a binding may narrow a
grant for one person, never widen it. What a server says of itself and its tools is stored bounded,
item by item and as a whole; a tool whose schema is too large to keep or to give a model, or that
the offer has no more room for, is recorded as unusable, with the reason, and cannot be granted.
What is recorded is the answer of the server the connection still points at.

Credentials are sealed with the platform's root key and only ever unsealed into an HTTP header
for one exchange, and only to the server they were given for: a new address forgets them, a
credential given without the address it is for, or for another address than the connection has
now, is refused (`expected_url`), and a caller holding the connection as it was before a change gets
neither the new server's token nor a call to the new address. Previews kept with calls are bounded
JSON of the arguments and the result text; the sealed values never enter them. A call's result — and
a resource list (`bounded_resources`) — is bounded to what its run record can hold (RESULT_BUDGET), so
that a call the server carried out is never recorded as one that failed.

A platform administrator may stop a connection (`platform_disabled_at`, synwevo_identity.admin_mcp):
its owner can then neither resume it nor give it a credential, nor delete it; only the platform
administration lifts the stop. The stop closes the connection's destination too
(suite_schema.mcp.destination_key): no connection, new or existing, of anybody reaches it until then.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from synwevo_core import sealed
from synwevo_core.auth import audit_event
from synwevo_core.capabilities import MAX_OUTPUT_BYTES, canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.machine import MANAGE_ROLES
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.organizations import workspace_access_failure
from synwevo_core.store import new_id, now
from synwevo_core.suite_schema.mcp import destination_key, settle_interrupted_calls, stopped_destinations

from .runtime import Discovery, McpRuntime, ToolOutcome

log = logging.getLogger("synwevo.mcp.client")

SEAL = "mcp-credential"
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_URL = 2048
MAX_PREVIEW = 4_000
MAX_ALIAS = 24
MODES = ("off", "confirm", "auto")
_SLUG = re.compile(r"[^a-z0-9]+")

#: What one discovered item may store (`_record_discovery`). The whole discovery is bounded on the
#: wire (runtime.MAX_DISCOVERY_BYTES); these keep one tool, resource or prompt from filling the
#: database, the pages or the model's context. A tool's input or output schema longer than
#: MAX_SCHEMA_CHARS as stored JSON — more than the Agent shows the model of a schema
#: (tools.McpTools.describe) — is not kept: the tool is recorded as unusable and cannot be granted.
MAX_SCHEMA_CHARS = 20_000
MAX_TITLE = 200
MAX_TOOL_DESCRIPTION = 4_000
MAX_ITEM_DESCRIPTION = 2_000
MAX_MIME_TYPE = 200
MAX_PROMPT_ARGUMENTS = 50
MAX_ARGUMENT_DESCRIPTION = 500
MAX_CAPABILITY_CHARS = 1_000
#: What one connection's whole offer may store, in UTF-8 bytes of the kept text: its tools first,
#: then its resources, then its prompts, each in the server's order and each kept whole while it
#: still fits. The page that shows a connection is one operation's answer (capabilities.
#: MAX_OUTPUT_BYTES, 1 MiB) and carries grants, calls and the connection itself beside the offer.
#: What no longer fits keeps only what naming and using it needs: a tool its name and hints, and it
#: is recorded as unusable (`mcp_offer_too_large`); a resource its address and name; a prompt its
#: name and its arguments' names.
MAX_OFFER_BYTES = 512 * 1024
#: The largest resource size kept (SQLite's INTEGER); a size beyond it, or not a count, is not kept.
MAX_SIZE = 2 ** 63 - 1
#: The tool annotations kept (MCP ToolAnnotations): the hints grants, pages and the Agent read.
ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
#: The server capabilities kept; anything else a server announces (experimental, extensions…) is not.
KNOWN_CAPABILITIES = ("tools", "resources", "prompts", "logging", "completions")
#: Why a tool is unusable (`mcp_connection_tools.unusable_code`), as the page and a refused grant say it.
UNUSABLE = {
    "mcp_schema_too_large": f"服务发布的工具输入或输出结构超过 {MAX_SCHEMA_CHARS} 个字符，无法安全地交给 Agent，这个工具不能授权",
    "mcp_schema_invalid": "服务发布的工具输入或输出结构不是 JSON 对象，无法检查，这个工具不能授权",
    "mcp_offer_too_large": f"服务发布的工具、资源和提示词合计超过 {MAX_OFFER_BYTES // 1024} KB，这个工具的定义没有保存，不能授权",
}
#: The bound each reason names (the page and a refused grant give it beside the reason).
UNUSABLE_LIMIT = {"mcp_schema_too_large": {"max_chars": MAX_SCHEMA_CHARS}, "mcp_schema_invalid": {"max_chars": MAX_SCHEMA_CHARS},
                  "mcp_offer_too_large": {"max_bytes": MAX_OFFER_BYTES}}
#: Stored in place of the input schema of an unusable tool: a schema no validator accepts, so that a
#: reader which ignored `unusable_code` still could not run the tool on unchecked input — the Agent
#: answers it as `mcp_schema_invalid` (tools.McpTools.prepare).
UNUSABLE_SCHEMA = {"type": "unusable"}

#: Failures the server, or the network on the way to it, answers for (docs/platform/mcp.md §4.7):
#: they mark the connection 'failed' and count toward its circuit breaker. A refused credential
#: (`mcp_auth_required`) marks it 'auth_required' and counts toward nothing; every other refusal —
#: the caller's own input, local admission and policy, a cancellation — leaves both alone.
SERVER_FAILURES = frozenset({"mcp_unreachable", "mcp_unresolvable", "mcp_timeout", "mcp_protocol_error",
                             "mcp_server_error", "mcp_not_found", "mcp_internal"})
#: How many of the people behind one failure count are remembered (Connections._health): no fewer
#: than the policy's largest threshold (McpPolicySpec.breaker_failures ≤ 100) can need.
MAX_FAILURE_USERS = 100
#: An OAuth access token this close to expiry is refreshed before use.
REFRESH_EARLY_S = 30
#: The lease one caller holds on refreshing a connection's token — longer than one refresh
#: exchange may take (oauth.DISCOVERY_TIMEOUT_S * 2) — and how often a waiting caller looks again.
REFRESH_CLAIM_S = 120
REFRESH_POLL_S = 0.2
#: What one call's result may take as its run record holds it (canonical JSON in UTF-8 bytes): the
#: record refuses more than capabilities.MAX_OUTPUT_BYTES once the handler has run — after the
#: external server acted — so the result is cut to fit first, with room to spare (`_fit`).
RESULT_BUDGET = MAX_OUTPUT_BYTES - 64 * 1024

_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(cid: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(cid, threading.Lock())


class _UnderLease:
    """The connection service as one token refresh sees it (oauth.OutboundOAuth.refresh): the token
    set the refresh stores is fenced by the lease this caller holds, which every change that voids the
    authorization clears — a new address or authentication, a revoked credential, a platform stop, a
    new authorization — and by the sealed credential the refresh started from, so a refresh such a
    change overtook cannot seal its result."""

    def __init__(self, connections: "Connections", lease: float, started_from: str):
        self._connections, self._lease, self._started_from = connections, lease, started_from

    def __getattr__(self, name):
        return getattr(self._connections, name)

    def store_tokens(self, cid: str, tokens: dict, **kw) -> None:
        self._connections.store_tokens(cid, tokens, lease=self._lease, started_from=self._started_from, **kw)


def slugify(name: str) -> str:
    base = _SLUG.sub("-", (name or "").lower()).strip("-")[:MAX_ALIAS].strip("-")
    return base or "mcp"


def _j(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def preview(value: Any) -> str:
    text = canonical(value) if not isinstance(value, str) else value
    return text if len(text) <= MAX_PREVIEW else text[:MAX_PREVIEW] + "…"


def _text(value: Any, limit: int) -> str | None:
    """Server-provided text as stored: a string cut to `limit`; anything else is not kept."""
    return value[:limit] if isinstance(value, str) else None


def _json(value: Any) -> str | None:
    """`value` as stored JSON (UTF-8 text, so that Chinese is not counted six times over), or None
    when it cannot be written."""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return None


def _schema(value: Any) -> tuple[str | None, str | None]:
    """A tool's schema as stored, or why it cannot be: (JSON, None) or (None, unusable code)."""
    text = _json(value) if isinstance(value, dict) else None
    if text is None:
        return None, "mcp_schema_invalid"
    return (text, None) if len(text) <= MAX_SCHEMA_CHARS else (None, "mcp_schema_too_large")


def _encoded_size(value: Any) -> int:
    """The bytes `value` takes in a run record (capabilities._finish: canonical JSON in UTF-8)."""
    return len(canonical(value).encode("utf-8", "surrogatepass"))


def _fit(result: dict, reductions, field: str) -> dict:
    """`result` as its run record can hold it (RESULT_BUDGET): unchanged when it fits; otherwise each
    of `reductions` (a function of the result) applied in turn until it does, and then the text in
    `field` cut to the longest beginning that fits — with `truncated` set."""
    if _encoded_size(result) <= RESULT_BUDGET:
        return result
    for reduce in reductions:
        result = {**reduce(result), "truncated": True}
        if _encoded_size(result) <= RESULT_BUDGET:
            return result
    text = result.get(field)
    result = {**result, field: "" if isinstance(text, str) else None, "truncated": True}
    room = RESULT_BUDGET - _encoded_size(result)
    if isinstance(text, str) and room > 0:
        # The longest beginning whose JSON (its quotes counted once, in the result above) still fits;
        # every character takes at least one byte, so no more than `room` of them can.
        lo, hi = 0, min(len(text), room)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _encoded_size(text[:mid]) - 2 <= room:
                lo = mid
            else:
                hi = mid - 1
        result[field] = text[:lo]
    return result


def bounded_resources(rows: list[dict]) -> tuple[list[dict], bool]:
    """A connection's resource list as one run record holds it (RESULT_BUDGET), and whether it was
    cut: whole when it fits; else without titles and descriptions; else the first entries, in order,
    that fit. Discovery keeps up to runtime.MAX_LISTED of them, and past the offer's room still each
    one's address and name — more, together, than a record holds."""
    if _encoded_size(rows) <= RESULT_BUDGET:
        return rows, False
    rows = [{k: v for k, v in r.items() if k not in ("title", "description")} for r in rows]
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _encoded_size(rows[:mid]) <= RESULT_BUDGET:
            lo = mid
        else:
            hi = mid - 1
    return rows[:lo], True


def _content_ref(content: Any) -> dict:
    """One resource content as a result past its room keeps it: its address and type, bounded."""
    c = content if isinstance(content, dict) else {}
    return {k: c[k][:limit] for k, limit in (("uri", MAX_URL), ("mimeType", MAX_MIME_TYPE)) if isinstance(c.get(k), str)}


def _unusable(code: str | None) -> dict | None:
    """Why a tool is unusable, as the page and a refused grant say it: the reason, its words and its bound."""
    if not code:
        return None
    return {"code": code, "message": UNUSABLE.get(code, UNUSABLE["mcp_schema_invalid"]),
            **UNUSABLE_LIMIT.get(code, UNUSABLE_LIMIT["mcp_schema_invalid"])}


class _Budget:
    """What is left of MAX_OFFER_BYTES while one discovery is recorded (`_record_discovery`)."""

    def __init__(self, total: int):
        self.left = total

    def take(self, *texts: str | None) -> bool:
        """Spend the stored size of `texts` if it still fits; False (nothing spent) if it does not."""
        cost = sum(len(t.encode("utf-8", "surrogatepass")) for t in texts if t)
        if cost > self.left:
            return False
        self.left -= cost
        return True


def _size(value: Any) -> int | None:
    """A resource's announced size as stored: a count SQLite can hold, else nothing."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SIZE else None


def _tool_row(tool: dict, budget: _Budget) -> dict:
    """What is stored of one discovered tool: bounded text, the known hints, and its schemas — or,
    when either schema cannot be kept, the reason and UNUSABLE_SCHEMA in place of both. A tool the
    connection's offer has no more room for (MAX_OFFER_BYTES) keeps only its name and hints and is
    unusable for that reason, unless it already is for its own."""
    input_json, bad_input = _schema(tool.get("inputSchema") or {"type": "object"})
    output = tool.get("outputSchema")
    output_json, bad_output = _schema(output) if output is not None else (None, None)
    unusable = bad_input or bad_output
    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    hints = {k: ann[k] for k in ANNOTATION_HINTS if isinstance(ann.get(k), bool)}
    kept = dict(hints)
    shown_as = _text(ann.get("title"), MAX_TITLE)
    if shown_as:
        kept["title"] = shown_as
    row = {"title": _text(tool.get("title"), MAX_TITLE), "description": _text(tool.get("description"), MAX_TOOL_DESCRIPTION) or "",
           "input": json.dumps(UNUSABLE_SCHEMA) if unusable else input_json, "output": None if unusable else output_json,
           "annotations": json.dumps(kept, ensure_ascii=False) if kept else None, "unusable": unusable}
    if budget.take(tool.get("name"), row["title"], row["description"], row["input"], row["output"], row["annotations"]):
        return row
    return {"title": None, "description": "", "input": json.dumps(UNUSABLE_SCHEMA), "output": None,
            "annotations": json.dumps(hints) if hints else None, "unusable": unusable or "mcp_offer_too_large"}


def _resource_row(res: dict, budget: _Budget) -> dict:
    """What is stored of one discovered resource: bounded text — or, past the offer's room, its
    address and name only (reading it needs no more)."""
    row = {"name": _text(res.get("name"), MAX_TITLE), "title": _text(res.get("title"), MAX_TITLE),
           "description": _text(res.get("description"), MAX_ITEM_DESCRIPTION) or "",
           "mime_type": _text(res.get("mimeType"), MAX_MIME_TYPE), "size": _size(res.get("size"))}
    if budget.take(res.get("uri"), row["name"], row["title"], row["description"], row["mime_type"]):
        return row
    return {**row, "title": None, "description": "", "mime_type": None}


def _prompt_row(pr: dict, budget: _Budget) -> dict:
    """What is stored of one discovered prompt: bounded text and arguments — or, past the offer's
    room, its name and its arguments' names and whether they are required (rendering it needs no more)."""
    arguments = _prompt_arguments(pr.get("arguments"))
    row = {"title": _text(pr.get("title"), MAX_TITLE), "description": _text(pr.get("description"), MAX_ITEM_DESCRIPTION) or "",
           "arguments": json.dumps(arguments, ensure_ascii=False)}
    if budget.take(pr.get("name"), row["title"], row["description"], row["arguments"]):
        return row
    return {"title": None, "description": "",
            "arguments": json.dumps([{"name": a["name"], "required": a["required"]} for a in arguments], ensure_ascii=False)}


def _prompt_arguments(value: Any) -> list[dict]:
    """A prompt's arguments as stored: named ones, at most MAX_PROMPT_ARGUMENTS, with bounded text."""
    out = []
    for arg in value if isinstance(value, list) else []:
        name = arg.get("name") if isinstance(arg, dict) else None
        if not isinstance(name, str) or not name or len(name) > 128:
            continue
        kept = {"name": name, "required": arg.get("required") is True}
        for key, limit in (("title", MAX_TITLE), ("description", MAX_ARGUMENT_DESCRIPTION)):
            text = _text(arg.get(key), limit)
            if text is not None:
                kept[key] = text
        out.append(kept)
        if len(out) >= MAX_PROMPT_ARGUMENTS:
            break
    return out


def _server_info(value: Any) -> dict | None:
    """Who the server says it is, as stored: the fields the pages show, bounded (no icons)."""
    if not isinstance(value, dict):
        return None
    kept = {k: _text(value.get(k), limit) for k, limit in (("name", MAX_TITLE), ("title", MAX_TITLE), ("version", MAX_TITLE),
                                                         ("description", MAX_ITEM_DESCRIPTION), ("websiteUrl", MAX_URL))}
    return {k: v for k, v in kept.items() if v is not None} or None


def _capabilities(value: Any) -> dict:
    """The capabilities a server announced, as stored: the known ones, each with its options when
    they are small (a capability whose options are not is kept as present, without them)."""
    caps = value if isinstance(value, dict) else {}
    out = {}
    for k in KNOWN_CAPABILITIES:
        if isinstance(caps.get(k), dict):
            text = _json(caps[k])
            out[k] = caps[k] if text is not None and len(text) <= MAX_CAPABILITY_CHARS else {}
    return out


def tool_hash(tool: dict) -> str:
    """What a grant is pinned to: the tool's name, input and output schemas, and the two hints a
    grant's mode depends on. A new description does not void a grant (it is untrusted text
    anyway); a new shape or a flipped read-only hint does."""
    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    return fingerprint({"name": tool.get("name"), "input": tool.get("inputSchema"), "output": tool.get("outputSchema"),
                        "readOnly": ann.get("readOnlyHint"), "destructive": ann.get("destructiveHint")})


class Connections:
    def __init__(self, store, identity, runtime: McpRuntime):
        self.store = store
        self.identity = identity
        self.runtime = runtime
        self.policy = McpPolicy(store)
        # Records a stopped process left 'running' (a crash, a restart) are settled as 'unknown'.
        settle_interrupted_calls(store)

    # ------------------------------------------------------------------ policy and rights
    def _spec(self):
        return self.policy.current()

    def require_outbound(self) -> None:
        if not self._spec().outbound_enabled:
            raise ServiceError(403, "mcp_outbound_disabled", "本部署尚未开放连接外部 MCP 服务")

    def _row(self, cid: str) -> dict:
        row = self.store.one("SELECT * FROM mcp_connections WHERE id=? AND deleted_at IS NULL", (cid,))
        if not row:
            raise ServiceError(404, "mcp_connection_not_found", "连接不存在")
        return row

    def _workspace_open(self, workspace_id: str, user_id: str) -> bool:
        """Whether the workspace is open to this person: active, and through the gate every native
        operation passes (capabilities.check_workspace) — of an organization that is active and that
        they still belong to. A frozen or deleted organization's workspace connections serve nobody:
        not the Agent, not a stored result, not a page."""
        ws = self.identity.get_workspace(workspace_id)
        return bool(ws and ws["status"] == "active" and workspace_access_failure(self.store, workspace_id, user_id) is None)

    def _manager_role(self, user_id: str, workspace_id: str) -> bool:
        member = self.identity.membership(workspace_id, user_id)
        return bool(member and member["role"] in MANAGE_ROLES)

    def manages(self, user_id: str, owner_kind: str, owner_id: str) -> bool:
        if owner_kind == "user":
            return owner_id == user_id
        return self._manager_role(user_id, owner_id) and self._workspace_open(owner_id, user_id)

    def may_use(self, user_id: str, conn: dict) -> bool:
        if conn["owner_kind"] == "user":
            return conn["owner_id"] == user_id
        return bool(self.identity.membership(conn["owner_id"], user_id) and self._workspace_open(conn["owner_id"], user_id))

    def _manage(self, user_id: str, conn: dict) -> None:
        # Someone who may not even use the connection learns nothing of it — the same 404 as for an
        # id that does not exist; only a user of it who may not manage it is told why (403).
        self._use(user_id, conn)
        if not self.manages(user_id, conn["owner_kind"], conn["owner_id"]):
            raise ServiceError(403, "mcp_manage_forbidden", "只有连接的所有者（个人）或工作空间的所有者、管理员可以管理这个连接")

    def _use(self, user_id: str, conn: dict) -> None:
        if not self.may_use(user_id, conn):
            raise ServiceError(404, "mcp_connection_not_found", "连接不存在")

    def _platform_stop(self, conn: dict, *, destination: bool = True) -> None:
        """Refuse what would resume, re-authorize or re-point a connection a platform administrator
        stopped (synwevo_identity.admin_mcp) — and, with `destination`, what would use or authorize one
        whose address a stop closes: only the platform administration lifts that stop."""
        if conn.get("platform_disabled_at"):
            raise ServiceError(409, "mcp_platform_disabled", "平台管理员已停用这个连接，只有平台管理员可以解除")
        if destination and conn.get("url"):
            self._destination_open(conn["url"])

    def _destination_open(self, url: str, stopped: dict | None = None) -> None:
        """Refuse a destination a platform stop closes (suite_schema.mcp.stopped_destinations): the
        stopped connection's own address written any way, for any connection of anybody — a second
        connection to it, or one added again after the first was given up."""
        key = destination_key(url)
        if key is not None and key in (stopped if stopped is not None else stopped_destinations(self.store)):
            raise ServiceError(403, "mcp_destination_stopped", "平台管理员已停用这个 MCP 服务地址，只有平台管理员可以解除",
                               {"host": urlsplit(url).hostname, "field": "url"})

    @staticmethod
    def _destination_stopped(url: str, stopped: dict) -> bool:
        return destination_key(url) in stopped

    # ------------------------------------------------------------------ CRUD
    def _validate_url(self, url: str) -> str:
        url = (url or "").strip()
        if not url or len(url) > MAX_URL or any(ch.isspace() for ch in url):
            raise ServiceError(400, "mcp_url_invalid", "请输入有效的 MCP 服务地址", {"field": "url"})
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.fragment or parts.username or parts.password:
            raise ServiceError(400, "mcp_url_invalid", "MCP 服务地址必须是 http(s) 绝对地址，不含片段或凭据", {"field": "url"})
        if parts.scheme == "http" and not self._spec().allow_http:
            # The same rule as every later use (runtime._destination): plain http only when the
            # platform policy allows it, loopback included — a saved connection must be usable.
            raise ServiceError(400, "mcp_url_invalid", "MCP 服务地址必须使用 https（管理员可在策略中放开 http）", {"field": "url"})
        refusal = self.policy.destination_refusal(parts.hostname)
        if refusal:
            raise ServiceError(403, refusal, "平台策略不允许连接这个地址", {"host": parts.hostname, "field": "url"})
        if self._is_self(parts.hostname):
            # The same rule as every later use (runtime._destination): this deployment's own origins
            # are not an external server — the Agent would act on Synwevo with a token for Synwevo —
            # unless the operator listed the host explicitly.
            raise ServiceError(403, "mcp_destination_is_self",
                               "不能把本部署自己的地址当作外部 MCP 服务（平台管理员可在策略的允许主机中明确列出它）",
                               {"host": parts.hostname, "field": "url"})
        self._destination_open(url)
        return url

    def _is_self(self, host: str) -> bool:
        from .runtime import is_own_origin, listed_explicitly
        return is_own_origin(host, store=self.store) and not listed_explicitly(host, self._spec())

    def _count_limit(self, owner_kind: str, owner_id: str) -> None:
        spec = self._spec()
        limit = spec.max_connections_per_user if owner_kind == "user" else spec.max_connections_per_workspace
        n = self.store.one("SELECT COUNT(*) AS n FROM mcp_connections WHERE owner_kind=? AND owner_id=? AND deleted_at IS NULL",
                           (owner_kind, owner_id))["n"]
        if n >= limit:
            raise ServiceError(409, "mcp_connection_limit", "连接数已达上限", {"limit": limit})

    def create(self, user: dict, *, owner_kind: str, owner_id: str | None, name: str, url: str, description: str = "",
               auth_kind: str = "none", credential: str | None = None, oauth_client: dict | None = None) -> dict:
        self.require_outbound()
        uid = user["id"]
        owner_id = uid if owner_kind == "user" else owner_id
        if owner_kind not in ("user", "workspace") or not owner_id:
            raise ServiceError(400, "invalid_input", "请指明连接属于个人还是某个工作空间")
        if owner_kind == "workspace":
            if not self._manager_role(uid, owner_id):
                raise ServiceError(403, "mcp_manage_forbidden", "只有工作空间的所有者或管理员可以为工作空间添加连接")
            if not self._workspace_open(owner_id, uid):
                raise ServiceError(404, "workspace_not_found", "工作空间不存在或不可用")
        name = (name or "").strip()
        if not name or len(name) > MAX_NAME:
            raise ServiceError(400, "invalid_input", "请输入连接名称（最多 80 字）", {"field": "name"})
        if len(description or "") > MAX_DESCRIPTION:
            raise ServiceError(400, "invalid_input", "说明最多 500 字", {"field": "description"})
        if auth_kind not in ("none", "bearer", "oauth"):
            raise ServiceError(400, "invalid_input", "未知的认证方式", {"field": "auth_kind"})
        if auth_kind == "bearer" and not (credential or "").strip():
            raise ServiceError(400, "invalid_input", "请提供访问令牌", {"field": "credential"})
        url = self._validate_url(url)
        self._count_limit(owner_kind, owner_id)
        slug = self._free_slug(owner_kind, owner_id, slugify(name))
        cid, t = new_id("mcpc"), now()
        client_json = None
        secret_sealed = None
        if auth_kind == "oauth" and oauth_client:
            # Pre-registered at the remote authorization server by the person: a client id and,
            # for a confidential client, its secret (sealed; shown to nobody again).
            client_id = (oauth_client.get("client_id") or "").strip()
            if client_id and len(client_id) <= 512:
                client_json = json.dumps({"kind": "preregistered", "client_id": client_id})
                secret = (oauth_client.get("client_secret") or "").strip()
                if secret:
                    secret_sealed = sealed.seal(self.store, SEAL, secret)
        with self.store.tx():
            self.store.execute(
                "INSERT INTO mcp_connections (id, owner_kind, owner_id, slug, name, description, url, auth_kind, "
                "credential_sealed, credential_hint, oauth_client_json, oauth_client_secret_sealed, status, health, "
                "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)",
                (cid, owner_kind, owner_id, slug, name, (description or "").strip(), url, auth_kind,
                 sealed.seal(self.store, SEAL, credential.strip()) if auth_kind == "bearer" else None,
                 ("…" + credential.strip()[-4:]) if auth_kind == "bearer" and len(credential.strip()) >= 8 else None,
                 client_json, secret_sealed, "auth_required" if auth_kind == "oauth" else "unknown", uid, t, t))
            audit_event(self.store, "suite.agent.mcp.connection.create", actor_kind="user", actor_id=uid,
                        workspace_id=owner_id if owner_kind == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid,
                        meta={"owner_kind": owner_kind, "host": urlsplit(url).hostname, "auth_kind": auth_kind})
        return self.summary(self._row(cid), uid)

    def _free_slug(self, owner_kind: str, owner_id: str, base: str, *, besides: str | None = None) -> str:
        slug, n = base, 1
        while self.store.one("SELECT 1 FROM mcp_connections WHERE owner_kind=? AND owner_id=? AND slug=? AND deleted_at IS NULL "
                             "AND id IS NOT ?", (owner_kind, owner_id, slug, besides)):
            n += 1
            slug = f"{base[:MAX_ALIAS - len(str(n)) - 1]}-{n}"
        return slug

    def update(self, user: dict, cid: str, *, name: str | None = None, description: str | None = None,
               url: str | None = None, auth_kind: str | None = None, credential: str | None = None,
               oauth_client: dict | None = None, expected_url: str | None = None) -> dict:
        """Change what a manager may change. `expected_url` is the address the change was made
        against (the page sends the one it shows): a new address, authentication or credential is then
        written only while the connection still has it — otherwise 409 `mcp_address_changed`, so that a
        token typed for one server never reaches the server another manager pointed it at meanwhile. A
        credential or OAuth client given without a `url` requires it (422 `invalid_input`, `field` expected_url)."""
        conn = self._row(cid)
        uid = user["id"]
        self._manage(uid, conn)
        sets, args, changed = [], [], []
        if name is not None:
            name = name.strip()
            if not name or len(name) > MAX_NAME:
                raise ServiceError(400, "invalid_input", "请输入连接名称（最多 80 字）", {"field": "name"})
            sets += ["name=?"]; args += [name]; changed.append("name")
        if description is not None:
            if len(description) > MAX_DESCRIPTION:
                raise ServiceError(400, "invalid_input", "说明最多 500 字", {"field": "description"})
            sets += ["description=?"]; args += [description.strip()]; changed.append("description")
        new_url = url is not None and url.strip() != conn["url"]
        touches_auth = auth_kind is not None or credential is not None or oauth_client is not None
        if new_url or touches_auth:
            # Not the destination of the address being left: pointing a connection away from a stopped
            # destination is no use of it (the new address is checked below).
            self._platform_stop(conn, destination=False)
            if expected_url is not None and expected_url.strip() != conn["url"]:
                self._address_changed()
        kind = auth_kind or conn["auth_kind"]
        if kind not in ("none", "bearer", "oauth"):
            raise ServiceError(400, "invalid_input", "未知的认证方式", {"field": "auth_kind"})
        given = (credential or "").strip()
        forget_flows = False
        if touches_auth and not new_url:
            # A credential for the address it has now, which a stop may close.
            self._destination_open(conn["url"])
            if (credential is not None or oauth_client is not None) and url is None and expected_url is None:
                # A secret is given for one server: every caller (the page, the API, the CLI, a script)
                # says which — the address it was shown, or the one it sets — or another manager's new
                # address could be the one that receives it. An input the operation cannot take without
                # it, refused as the operation's own input check refuses one (capabilities: 422).
                raise ServiceError(422, "invalid_input", "提供凭据时请一并给出它对应的服务地址（expected_url）",
                                   {"field": "expected_url"})
        if new_url:
            self.require_outbound()
            url = self._validate_url(url)
            # A new address is a new server. Nothing discovered, granted, bound or authorized for the
            # old one carries over, and nothing the old one was given may reach the new one: the
            # bearer token, the OAuth token set, the OAuth client (its secret and registration belong
            # to the old server's authorization server) and the flows in progress are all forgotten,
            # and the connection says it needs authentication again — unless this same change gives
            # the new server its own credential (below).
            sets += ["url=?", "discovered_at=NULL", "discovery_hash=NULL", "server_info_json=NULL",
                     "capabilities_json=NULL", "protocol_version=NULL", "instructions=NULL", "failure_streak=0",
                     "failure_users_json=NULL", "open_until=NULL", "credential_sealed=NULL", "credential_hint=NULL",
                     "oauth_client_json=NULL", "oauth_client_secret_sealed=NULL",
                     "health=?", "last_error_code=NULL", "last_error_message=NULL", "last_error_cause=NULL"]
            args += [url, "unknown" if kind == "none" else "auth_required"]
            changed.append("url")
            forget_flows = True
        if touches_auth:
            sets += ["auth_kind=?"]; args += [kind]; changed.append("auth")
            if kind != conn["auth_kind"]:
                forget_flows = True
            if kind == "bearer":
                if given:
                    sets += ["credential_sealed=?", "credential_hint=?", "oauth_client_json=NULL", "oauth_client_secret_sealed=NULL",
                             "health='unknown'", "failure_streak=0", "failure_users_json=NULL", "open_until=NULL"]
                    args += [sealed.seal(self.store, SEAL, given), ("…" + given[-4:]) if len(given) >= 8 else None]
                elif conn["auth_kind"] != "bearer":
                    raise ServiceError(400, "invalid_input", "请提供访问令牌", {"field": "credential"})
            elif kind == "none":
                sets += ["credential_sealed=NULL", "credential_hint=NULL", "oauth_client_json=NULL",
                         "oauth_client_secret_sealed=NULL"]
                if not new_url:
                    sets += ["health=CASE WHEN health='auth_required' THEN 'unknown' ELSE health END"]
            else:
                if oauth_client is not None:
                    client_id = (oauth_client.get("client_id") or "").strip()
                    sets += ["oauth_client_json=?", "oauth_client_secret_sealed=?", "credential_sealed=NULL",
                             "credential_hint=NULL", "health='auth_required'"]
                    secret = (oauth_client.get("client_secret") or "").strip()
                    args += [json.dumps({"kind": "preregistered", "client_id": client_id}) if client_id else None,
                             sealed.seal(self.store, SEAL, secret) if secret else None]
                    forget_flows = True
                elif conn["auth_kind"] != "oauth":
                    sets += ["credential_sealed=NULL", "credential_hint=NULL", "health='auth_required'"]
        if forget_flows:
            # A token refresh under way spends an authorization this change voids: clearing its lease
            # keeps it from sealing the result (store_tokens).
            sets += ["refresh_claim_until=NULL"]
        if not sets:
            return self.summary(conn, uid)
        t = now()
        with self.store.tx():
            # A re-pointing or re-authorizing write is conditional, so that a platform stop — or another
            # manager's new address — landing after the checks above still holds: a credential is
            # written for the address it was given for, or not at all.
            guard, extra = (" AND platform_disabled_at IS NULL AND url=?", (conn["url"],)) if (new_url or touches_auth) else ("", ())
            if not self.store.execute(f"UPDATE mcp_connections SET {', '.join(sets)}, updated_at=? WHERE id=?{guard}",
                                      (*args, t, cid, *extra)):
                current = self._row(cid)
                self._platform_stop(current, destination=False)
                self._address_changed()
            if "url" in changed:
                self.store.execute("UPDATE mcp_connection_tools SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (t, cid))
                self.store.execute("UPDATE mcp_connection_resources SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (t, cid))
                self.store.execute("UPDATE mcp_connection_prompts SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (t, cid))
                # Grants were decisions about the old server's tools: a same-named tool of the new one
                # is not covered by them.
                self.store.execute("DELETE FROM mcp_tool_grants WHERE connection_id=?", (cid,))
                # Each person decided to let their Agent use the old server; the new one enters nobody's
                # Agent until its person turns the binding on again (alias and narrowing are kept).
                self.store.execute("UPDATE mcp_agent_bindings SET enabled=0, resources_enabled=0, updated_at=? WHERE connection_id=?",
                                   (t, cid))
            if forget_flows:
                self.store.execute("DELETE FROM mcp_oauth_flows WHERE connection_id=?", (cid,))
            meta = {"changed": changed}
            if "url" in changed:
                meta["host"] = urlsplit(url).hostname
                meta["credential_forgotten"] = bool(conn.get("credential_sealed") or conn.get("oauth_client_json")
                                                    or conn.get("oauth_client_secret_sealed"))
            audit_event(self.store, "suite.agent.mcp.connection.update", actor_kind="user", actor_id=uid,
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid, meta=meta)
        return self.summary(self._row(cid), uid)

    @staticmethod
    def _address_changed() -> None:
        raise ServiceError(409, "mcp_address_changed", "这个连接的服务地址已被更改，这次修改没有保存；请核对新的地址后再提供凭据",
                           {"field": "url"})

    def set_status(self, user: dict, cid: str, status: str) -> dict:
        if status not in ("active", "disabled"):
            raise ServiceError(400, "invalid_input", "状态只能是 active 或 disabled")
        conn = self._row(cid)
        self._manage(user["id"], conn)
        if status == "active":
            self._platform_stop(conn)
        with self.store.tx():
            # Conditional, so that a platform stop landing between the check and the write still holds.
            n = self.store.execute("UPDATE mcp_connections SET status=?, updated_at=? WHERE id=? "
                                   "AND (? = 'disabled' OR platform_disabled_at IS NULL)", (status, now(), cid, status))
            if not n:
                raise ServiceError(409, "mcp_platform_disabled", "平台管理员已停用这个连接，只有平台管理员可以解除")
            audit_event(self.store, "suite.agent.mcp.connection.disable" if status == "disabled" else "suite.agent.mcp.connection.enable",
                        actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid)
        return self.summary(self._row(cid), user["id"])

    def delete(self, user: dict, cid: str) -> dict:
        conn = self._row(cid)
        self._manage(user["id"], conn)
        # A stopped connection is the record of the platform's stop of its destination
        # (stopped_destinations): deleting it would lift the stop. Its owner keeps it disabled instead.
        refused = ServiceError(409, "mcp_platform_disabled", "平台管理员已停用这个连接；解除之前不能删除它，它会保持停用")
        if conn.get("platform_disabled_at"):
            raise refused
        with self.store.tx():
            # The sealed credentials go with the row's usefulness: nothing can be unsealed again.
            # Conditional, so that a platform stop landing between the check and the write still holds.
            if not self.store.execute("UPDATE mcp_connections SET deleted_at=?, status='disabled', credential_sealed=NULL, "
                                      "credential_hint=NULL, oauth_client_secret_sealed=NULL, updated_at=? WHERE id=? "
                                      "AND deleted_at IS NULL AND platform_disabled_at IS NULL", (now(), now(), cid)):
                self._row(cid)
                raise refused
            self.store.execute("DELETE FROM mcp_agent_bindings WHERE connection_id=?", (cid,))
            self.store.execute("DELETE FROM mcp_oauth_flows WHERE connection_id=?", (cid,))
            audit_event(self.store, "suite.agent.mcp.connection.delete", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid)
        return {"deleted": cid}

    def revoke_credential(self, user: dict, cid: str) -> dict:
        """Forget the credential (a bearer token, an OAuth token set) without deleting the connection."""
        conn = self._row(cid)
        self._manage(user["id"], conn)
        with self.store.tx():
            # The refresh lease goes too, so that a refresh under way cannot seal a new token set over
            # the revocation (store_tokens).
            self.store.execute("UPDATE mcp_connections SET credential_sealed=NULL, credential_hint=NULL, refresh_claim_until=NULL, "
                               "health=CASE WHEN auth_kind='none' THEN health ELSE 'auth_required' END, updated_at=? WHERE id=?",
                               (now(), cid))
            audit_event(self.store, "suite.agent.mcp.credential.revoke", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid)
        return self.summary(self._row(cid), user["id"])

    # ------------------------------------------------------------------ credentials
    def _unsealed(self, conn: dict) -> str | None:
        if conn["auth_kind"] == "none" or not conn.get("credential_sealed"):
            return None
        try:
            return sealed.unseal(self.store, SEAL, conn["credential_sealed"])
        except Exception:  # noqa: BLE001 — a credential that no longer opens is as good as none
            log.warning("mcp credential of connection %s could not be unsealed", conn["id"])
            return None

    def _tokens(self, conn: dict) -> dict | None:
        tokens = _j(self._unsealed(conn), {})
        return tokens if isinstance(tokens, dict) and tokens else None

    @staticmethod
    def _due(tokens: dict) -> bool:
        return bool(tokens.get("expires_at") and tokens["expires_at"] <= now() + REFRESH_EARLY_S and tokens.get("refresh_token"))

    def credential(self, conn: dict) -> str | None:
        """The secret for one exchange with the server at `conn`'s address: the bearer token, or the
        OAuth access token — refreshed first when it is about to expire (the request is not sent
        without a credential: `mcp_auth_required` when the authorization server refuses the refresh,
        its own failure code when it cannot be reached, `mcp_connection_changed` when the connection
        no longer points where `conn` says)."""
        raw = self._unsealed(conn)
        if raw is None:
            return None
        if conn["auth_kind"] == "bearer":
            return raw
        tokens = _j(raw, {})
        if not isinstance(tokens, dict):
            return None
        if self._due(tokens):
            return self._refreshed(conn["id"], conn["url"])
        return tokens.get("access_token")

    def _refreshed(self, cid: str, url: str) -> str | None:
        """Refresh the connection's OAuth token once for everyone who needs it now. One caller per
        process (a lock per connection) and per deployment (a lease on the row, `refresh_claim_until`)
        spends the refresh token; the others wait and use what it stored. With refresh-token rotation
        a second exchange of the same refresh token is refused, and a server that detects the reuse
        revokes the whole token family. Only a token for `url` — the address the caller will send it
        to — is handed out (`_stored_tokens`)."""
        with _refresh_lock(cid):
            give_up = time.monotonic() + REFRESH_CLAIM_S
            while True:
                tokens = self._stored_tokens(cid, url)[1]
                if not self._due(tokens):
                    return tokens.get("access_token")          # refreshed meanwhile, here or elsewhere
                t = now()
                lease = t + REFRESH_CLAIM_S
                if self.store.execute("UPDATE mcp_connections SET refresh_claim_until=? WHERE id=? "
                                      "AND (refresh_claim_until IS NULL OR refresh_claim_until<?)", (lease, cid, t)):
                    break
                if tokens["expires_at"] > t:
                    return tokens.get("access_token")          # another process is refreshing; this one still serves
                if time.monotonic() >= give_up:
                    raise ServiceError(429, "mcp_connection_busy", "这个连接正在刷新令牌，请稍后再试",
                                       {"retry_after": 2, "retryable": True})
                time.sleep(REFRESH_POLL_S)
            try:
                # Read again under the lease: another process may have refreshed and let go between the
                # read above and the claim. Then its token serves, and the refresh token read above —
                # which it spent — is not sent again.
                conn, tokens = self._stored_tokens(cid, url)
                if not self._due(tokens):
                    return tokens.get("access_token")
                from .oauth import OutboundOAuth
                try:
                    fresh = OutboundOAuth(_UnderLease(self, lease, conn["credential_sealed"])).refresh(conn, tokens)
                except ServiceError as err:
                    # As the refresh said it: a refused grant asks for a new authentication
                    # (`mcp_auth_required`), an authorization server that cannot be reached or fails is
                    # the server's failure under its own code, and a change that overtook the refresh is
                    # `mcp_connection_changed` — what the caller records (_failed) depends on which.
                    log.info("mcp token refresh of connection %s failed: %s", cid, err.code)
                    raise
            finally:
                # Only this caller's own lease: one that a change cleared, or that expired and another
                # caller took, is not this caller's to end.
                self.store.execute("UPDATE mcp_connections SET refresh_claim_until=NULL WHERE id=? AND refresh_claim_until=?",
                                   (cid, lease))
            return fresh.get("access_token")

    def _stored_tokens(self, cid: str, url: str) -> tuple[dict, dict]:
        """The connection and its token set as stored now — for a caller that will send the token to
        `url`. A connection pointed elsewhere, or given another authentication, since that caller read
        it holds what was given for another server: refused (`mcp_connection_changed`), never handed
        to the old address."""
        conn = self.store.one("SELECT * FROM mcp_connections WHERE id=? AND deleted_at IS NULL", (cid,))
        if conn and (conn["url"] != url or conn["auth_kind"] != "oauth"):
            raise ServiceError(409, "mcp_connection_changed", "连接在这次请求开始后改了地址或认证方式，请求没有发出，请重试")
        tokens = self._tokens(conn) if conn else None
        if not tokens:
            raise ServiceError(401, "mcp_auth_required", "需要重新认证这个连接")
        return conn, tokens

    def store_tokens(self, cid: str, tokens: dict, *, flow_id: str | None = None, lease: float | None = None,
                     started_from: str | None = None) -> None:
        """Seal a token set on the connection — only while it is still the OAuth connection the
        authorization was made for: not once its address or authentication changed (which forgets its
        flows and clears the refresh lease) or its credential was revoked, not under a platform stop,
        not after deletion.

        `flow_id` names the authorization whose code was exchanged (oauth callback): that flow must
        still exist, since a newer flow — for the server the connection now points at — does not vouch
        for tokens of the old one. A callback's tokens are a new authorization: they end any refresh
        lease, so that a refresh of the old token family under way cannot seal its result over them.
        `lease` is the refresh lease the caller holds (`_refreshed`): it must still be the connection's,
        and `started_from` — the sealed credential the refresh began with — still its credential.
        Without `flow_id`, some flow of the connection must exist."""
        keep = {k: tokens.get(k) for k in ("access_token", "refresh_token", "expires_at", "scope", "token_type")}
        if flow_id is not None:
            cond, extra = " AND EXISTS (SELECT 1 FROM mcp_oauth_flows WHERE id=? AND connection_id=?)", (flow_id, cid)
        else:
            cond, extra = " AND EXISTS (SELECT 1 FROM mcp_oauth_flows WHERE connection_id=?)", (cid,)
        if lease is not None:
            cond, extra = cond + " AND refresh_claim_until=? AND credential_sealed=?", extra + (lease, started_from)
        voids_refresh = ", refresh_claim_until=NULL" if flow_id is not None and lease is None else ""
        n = self.store.execute("UPDATE mcp_connections SET credential_sealed=?, credential_hint=?, health='unknown', "
                               "failure_streak=0, failure_users_json=NULL, open_until=NULL, updated_at=?" + voids_refresh +
                               " WHERE id=? AND deleted_at IS NULL AND auth_kind='oauth' AND platform_disabled_at IS NULL" + cond,
                               (sealed.seal(self.store, SEAL, json.dumps(keep)), "oauth", now(), cid, *extra))
        if not n:
            raise ServiceError(409, "mcp_connection_changed", "连接在认证期间被修改、停用或删除，请重新认证")

    # ------------------------------------------------------------------ health and breaker
    def _failed(self, cid: str, uid: str | None, err: ServiceError, *, url: str | None = None) -> None:
        """What one failed exchange with the server at `url` says about the connection (SERVER_FAILURES).
        One that failed at the authorization server, refreshing the token (`cause` "token_refresh",
        oauth.OutboundOAuth.refresh), says so beside its code: the MCP server was not asked."""
        cause = err.detail.get("cause") if isinstance(err.detail, dict) else None
        cause = cause if cause == "token_refresh" else None
        if err.code == "mcp_auth_required":
            self._health(cid, "auth_required", err.code, err.message, url=url, cause=cause)
        elif err.code in SERVER_FAILURES:
            self._health(cid, "failed", err.code, err.message, user_id=uid, url=url, cause=cause)

    def _health(self, cid: str, state: str, code: str | None = None, message: str | None = None, *,
                user_id: str | None = None, url: str | None = None, cause: str | None = None) -> None:
        """Record what an exchange said of the connection's server. With `url` (the address the
        exchange went to), only while the connection still points there: a verdict on the old server
        is not one on the server a new address names (update)."""
        t = now()
        same = (" AND url=?", (url,)) if url is not None else ("", ())
        if state == "ok":
            self.store.execute("UPDATE mcp_connections SET health='ok', health_checked_at=?, last_ok_at=?, last_error_code=NULL, "
                               "last_error_message=NULL, last_error_cause=NULL, failure_streak=0, failure_users_json=NULL, "
                               "open_until=NULL, updated_at=? "
                               "WHERE id=?" + same[0], (t, t, t, cid, *same[1]))
            return
        if state == "auth_required":
            # The server refused the credential: a person has to authenticate again, and nothing says
            # the server is failing — no streak, no breaker.
            self.store.execute("UPDATE mcp_connections SET health='auth_required', health_checked_at=?, last_error_code=?, "
                               "last_error_message=?, last_error_cause=?, updated_at=? WHERE id=?" + same[0],
                               (t, code, (message or "")[:500], cause, t, cid, *same[1]))
            return
        spec = self._spec()
        with self.store.tx():
            row = self.store.one("SELECT owner_kind, owner_id, failure_streak, failure_users_json FROM mcp_connections WHERE id=?"
                                 + same[0], (cid, *same[1]))
            if not row:
                return
            # `failure_streak` counts every failure since the last success (what the pages show);
            # `failure_users_json` holds what the circuit decides on: the streak at its last opening
            # ("since") and, since then, how many failures each person met ("by").
            evidence = _j(row["failure_users_json"], {})
            evidence = evidence if isinstance(evidence, dict) else {}
            since = evidence.get("since") if isinstance(evidence.get("since"), int) else 0
            by = {u: n for u, n in (evidence.get("by") or {}).items() if isinstance(u, str) and isinstance(n, int) and n > 0}
            key = user_id or ""
            if key in by or len(by) < MAX_FAILURE_USERS:
                by[key] = by.get(key, 0) + 1
            streak = (row["failure_streak"] or 0) + 1
            opens = self._opens(row, max(0, streak - since), by, spec.breaker_failures)
            # An opening starts the count again: once the window ends, stopping the connection again
            # takes the same evidence anew, not one more failure of whoever is still trying.
            evidence = {"since": streak, "by": {}} if opens else {"since": since, "by": by}
            self.store.execute("UPDATE mcp_connections SET health='failed', health_checked_at=?, last_error_code=?, "
                               "last_error_message=?, last_error_cause=?, failure_streak=?, failure_users_json=?, "
                               "open_until=CASE WHEN ? THEN ? ELSE open_until END, updated_at=? WHERE id=?",
                               (t, code, (message or "")[:500], cause, streak, json.dumps(evidence), int(opens),
                                t + spec.breaker_open_s, t, cid))

    def _opens(self, row: dict, counted: int, by: dict[str, int], threshold: int) -> bool:
        """Whether `counted` failures in a row — `by[person]` of them each person's — stop the
        connection for everyone who uses it.

        A person who manages the connection (its owner, or an owner or administrator of its
        workspace) counts in full: they could disable it anyway. Anyone else counts for at most half
        the threshold, rounded up, so a member — a viewer included — can neither stop a shared
        connection by themselves nor do it on the back of someone else's stray failure: the others
        must have met a real share of the failures too. Failures with nobody on record count in full.
        Opening takes the threshold and either two people or one who manages the connection."""
        share = (threshold + 1) // 2
        managers = {u for u in by if u and self.manages(u, row["owner_kind"], row["owner_id"])}
        excess = sum(max(0, n - share) for u, n in by.items() if u not in managers)
        unattributed = max(0, counted - sum(by.values()))
        people = len(by) + (1 if unattributed else 0)
        return counted - excess >= threshold and (people >= 2 or bool(managers))

    def _ready(self, conn: dict, *, for_use: bool = True) -> None:
        self._platform_stop(conn)
        if conn["status"] != "active":
            raise ServiceError(409, "mcp_connection_disabled", "这个连接已停用")
        if for_use and conn.get("open_until") and conn["open_until"] > now():
            raise ServiceError(503, "mcp_circuit_open", "这个连接连续失败，暂时停止调用；稍后自动恢复",
                               {"retry_after": int(conn["open_until"] - now()) + 1, "retryable": True})
        if conn["auth_kind"] != "none" and not conn.get("credential_sealed"):
            raise ServiceError(409, "mcp_auth_required", "这个连接需要先完成认证")

    # ------------------------------------------------------------------ discovery
    def test(self, user: dict, cid: str, *, origin: str = "test") -> dict:
        """A real handshake and capability read, recorded as the connection's health and offer."""
        self.require_outbound()
        conn = self._row(cid)
        self._manage(user["id"], conn) if origin == "test" else self._use(user["id"], conn)
        self._ready(conn, for_use=False)
        spec = self._spec()
        call_id = self._start_call(conn, user["id"], origin, "discover", None)
        try:
            found = self.runtime.discover(conn, self.credential(conn), user_id=user["id"], timeout=spec.discover_timeout_s)
        except ServiceError as err:
            self._finish_call(call_id, "failed" if err.code != "mcp_timeout" else "timeout", error=err)
            self._failed(cid, user["id"], err, url=conn["url"])
            raise
        try:
            with self.store.tx():
                # The answer came from the server at the address read above. Pointed elsewhere since, the
                # connection is a new server (update): this answer is neither its offer nor its health,
                # and does not use up the first look its person gets at it. Read under the write lock.
                current = self.store.one("SELECT url, discovered_at FROM mcp_connections WHERE id=? AND deleted_at IS NULL", (cid,))
                if current is None:
                    raise ServiceError(404, "mcp_connection_not_found", "连接不存在")
                if current["url"] != conn["url"]:
                    raise ServiceError(409, "mcp_address_changed", "测试期间连接改了地址，这次发现的结果没有记录，请重新测试")
                first = current["discovered_at"] is None and conn["owner_kind"] == "user"
                self._record_discovery(conn, found, grant_new=first, granted_by=user["id"])
                self._health(cid, "ok")
                self._finish_call(call_id, "succeeded", result={"tools": len(found.tools), "resources": len(found.resources),
                                                                "prompts": len(found.prompts), "protocol": found.protocol_version})
        except ServiceError as err:
            self._finish_call(call_id, "failed", error=err)
            raise
        except Exception:
            # The exchange succeeded but recording what it found did not (and rolled back): the call
            # record still ends, as this side's failure, not the server's.
            self._finish_call(call_id, "failed", error=ServiceError(500, "mcp_record_failed", "无法记录这次发现的结果"))
            raise
        return self.detail(user, cid)

    def _record_discovery(self, conn: dict, found: Discovery, *, grant_new: bool, granted_by: str) -> None:
        cid, t = conn["id"], now()
        budget = _Budget(MAX_OFFER_BYTES)
        seen_tools = set()
        for i, tool in enumerate(found.tools):
            name = tool.get("name")
            if not isinstance(name, str) or not name or len(name) > 128:
                continue
            seen_tools.add(name)
            # The hash covers the tool as the server published it, so an unusable schema that shrinks
            # back, or grows, is a new shape for every grant — while room in the offer is not: a tool
            # that had none and finds some again is the same tool, under the grant it had.
            h = tool_hash(tool)
            kept = _tool_row(tool, budget)
            values = (kept["title"], kept["description"], kept["input"], kept["output"], kept["annotations"], kept["unusable"], h, i)
            row = self.store.one("SELECT schema_hash FROM mcp_connection_tools WHERE connection_id=? AND name=?", (cid, name))
            if row:
                self.store.execute("UPDATE mcp_connection_tools SET title=?, description=?, input_schema_json=?, output_schema_json=?, "
                                   "annotations_json=?, unusable_code=?, schema_hash=?, position=?, last_seen_at=?, removed_at=NULL "
                                   "WHERE connection_id=? AND name=?", (*values, t, cid, name))
            else:
                self.store.execute("INSERT INTO mcp_connection_tools (connection_id, name, title, description, input_schema_json, "
                                   "output_schema_json, annotations_json, unusable_code, schema_hash, position, first_seen_at, "
                                   "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (cid, name, *values, t, t))
            if grant_new and not kept["unusable"]:
                # A person's own first look at their own server — a new connection, or one pointed at a
                # new address, which forgot every grant: every usable tool usable, each one asking before
                # it runs, whether or not the old server had a tool of that name. Tools that arrive later
                # are not enabled by their arrival.
                self.store.execute("INSERT OR IGNORE INTO mcp_tool_grants (connection_id, tool, mode, schema_hash, granted_by, "
                                   "granted_at, updated_at) VALUES (?,?,?,?,?,?,?)", (cid, name, "confirm", h, granted_by, t, t))
        gone = self.store.all("SELECT name FROM mcp_connection_tools WHERE connection_id=? AND removed_at IS NULL", (cid,))
        for g in gone:
            if g["name"] not in seen_tools:
                self.store.execute("UPDATE mcp_connection_tools SET removed_at=? WHERE connection_id=? AND name=?", (t, cid, g["name"]))
        seen_res = set()
        for res in found.resources:
            uri = res.get("uri")
            if not isinstance(uri, str) or not uri or len(uri) > 2048:
                continue
            seen_res.add(uri)
            kept = _resource_row(res, budget)
            self.store.execute("INSERT INTO mcp_connection_resources (connection_id, uri, name, title, description, mime_type, size, "
                               "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id, uri) DO UPDATE SET "
                               "name=excluded.name, title=excluded.title, description=excluded.description, mime_type=excluded.mime_type, "
                               "size=excluded.size, last_seen_at=excluded.last_seen_at, removed_at=NULL",
                               (cid, uri, kept["name"], kept["title"], kept["description"], kept["mime_type"], kept["size"], t, t))
        for g in self.store.all("SELECT uri FROM mcp_connection_resources WHERE connection_id=? AND removed_at IS NULL", (cid,)):
            if g["uri"] not in seen_res:
                self.store.execute("UPDATE mcp_connection_resources SET removed_at=? WHERE connection_id=? AND uri=?", (t, cid, g["uri"]))
        seen_prompts = set()
        for pr in found.prompts:
            name = pr.get("name")
            if not isinstance(name, str) or not name or len(name) > 128:
                continue
            seen_prompts.add(name)
            kept = _prompt_row(pr, budget)
            self.store.execute("INSERT INTO mcp_connection_prompts (connection_id, name, title, description, arguments_json, "
                               "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(connection_id, name) DO UPDATE SET "
                               "title=excluded.title, description=excluded.description, arguments_json=excluded.arguments_json, "
                               "last_seen_at=excluded.last_seen_at, removed_at=NULL",
                               (cid, name, kept["title"], kept["description"], kept["arguments"], t, t))
        for g in self.store.all("SELECT name FROM mcp_connection_prompts WHERE connection_id=? AND removed_at IS NULL", (cid,)):
            if g["name"] not in seen_prompts:
                self.store.execute("UPDATE mcp_connection_prompts SET removed_at=? WHERE connection_id=? AND name=?", (t, cid, g["name"]))
        info = _server_info(found.server_info)
        self.store.execute("UPDATE mcp_connections SET protocol_version=?, server_info_json=?, capabilities_json=?, instructions=?, "
                           "discovered_at=?, discovery_hash=?, updated_at=? WHERE id=?",
                           (_text(found.protocol_version, 64), json.dumps(info, ensure_ascii=False) if info else None,
                            json.dumps(_capabilities(found.capabilities)), _text(found.instructions, 4000) or None, t,
                            fingerprint({"tools": sorted(seen_tools), "resources": sorted(seen_res), "prompts": sorted(seen_prompts)}),
                            t, cid))

    # ------------------------------------------------------------------ grants and bindings
    def set_grant(self, user: dict, cid: str, tool: str, mode: str) -> dict:
        conn = self._row(cid)
        self._manage(user["id"], conn)
        if mode not in MODES:
            raise ServiceError(400, "invalid_input", "模式只能是 off、confirm 或 auto", {"field": "mode"})
        row = self.store.one("SELECT * FROM mcp_connection_tools WHERE connection_id=? AND name=? AND removed_at IS NULL", (cid, tool))
        if not row:
            raise ServiceError(404, "mcp_tool_not_found", "这个工具不存在或已被服务移除")
        if row["unusable_code"] and mode != "off":
            why = _unusable(row["unusable_code"])
            raise ServiceError(409, "mcp_tool_unusable", why.pop("message"), {"reason": why.pop("code"), **why})
        ann = _j(row["annotations_json"], {}) or {}
        if mode == "auto" and ann.get("readOnlyHint") is not True:
            raise ServiceError(409, "mcp_auto_requires_read_only", "只有服务标记为只读的工具才可以不经确认执行")
        t = now()
        with self.store.tx():
            self.store.execute("INSERT INTO mcp_tool_grants (connection_id, tool, mode, schema_hash, granted_by, granted_at, updated_at) "
                               "VALUES (?,?,?,?,?,?,?) ON CONFLICT(connection_id, tool) DO UPDATE SET mode=excluded.mode, "
                               "schema_hash=excluded.schema_hash, granted_by=excluded.granted_by, updated_at=excluded.updated_at",
                               (cid, tool, mode, row["schema_hash"], user["id"], t, t))
            audit_event(self.store, "suite.agent.mcp.grant.set", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid, meta={"tool": tool, "mode": mode})
        return self.detail(user, cid)

    def bind(self, user: dict, cid: str, *, enabled: bool | None = None, alias: str | None = None,
             resources_enabled: bool | None = None, tool_modes: dict | None = None) -> dict:
        conn = self._row(cid)
        self._use(user["id"], conn)
        uid, t = user["id"], now()
        current = self.store.one("SELECT * FROM mcp_agent_bindings WHERE user_id=? AND connection_id=?", (uid, cid))
        if alias is not None:
            alias = slugify(alias)
            clash = self.store.one("SELECT connection_id FROM mcp_agent_bindings WHERE user_id=? AND alias=? AND connection_id<>?",
                                   (uid, alias, cid))
            if clash:
                raise ServiceError(409, "mcp_alias_taken", "这个别名已用于另一个连接", {"field": "alias"})
        modes = None
        if tool_modes is not None:
            if not isinstance(tool_modes, dict) or len(tool_modes) > 500:
                raise ServiceError(400, "invalid_input", "工具模式必须是对象", {"field": "tool_modes"})
            modes = {}
            for name, mode in tool_modes.items():
                if not isinstance(name, str) or mode not in ("off", "confirm"):
                    # A person may hold a tool back or make it ask; widening is the connection's grant to give.
                    raise ServiceError(400, "invalid_input", "个人只能把工具设为 off 或 confirm", {"field": "tool_modes"})
                modes[name] = mode
        with self.store.tx():
            if current is None:
                base = alias or self._free_alias(uid, conn["slug"])
                self.store.execute("INSERT INTO mcp_agent_bindings (user_id, connection_id, alias, enabled, resources_enabled, "
                                   "tool_modes_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                   (uid, cid, base, int(True if enabled is None else enabled),
                                    int(bool(resources_enabled)), json.dumps(modes) if modes else None, t, t))
            else:
                self.store.execute("UPDATE mcp_agent_bindings SET alias=COALESCE(?, alias), enabled=COALESCE(?, enabled), "
                                   "resources_enabled=COALESCE(?, resources_enabled), tool_modes_json=CASE WHEN ? THEN ? ELSE tool_modes_json END, "
                                   "updated_at=? WHERE user_id=? AND connection_id=?",
                                   (alias, None if enabled is None else int(enabled),
                                    None if resources_enabled is None else int(resources_enabled),
                                    int(modes is not None), json.dumps(modes) if modes else None, t, uid, cid))
            audit_event(self.store, "suite.agent.mcp.binding.set", actor_kind="user", actor_id=uid,
                        object_kind="mcp_connection", object_id=cid,
                        meta={"enabled": enabled, "resources_enabled": resources_enabled, "tools": sorted(modes) if modes else None})
        return self.binding_json(uid, cid)

    def _free_alias(self, uid: str, base: str) -> str:
        alias, n = base, 1
        while self.store.one("SELECT 1 FROM mcp_agent_bindings WHERE user_id=? AND alias=?", (uid, alias)):
            n += 1
            alias = f"{base[:MAX_ALIAS - len(str(n)) - 1]}-{n}"
        return alias

    def unbind(self, user: dict, cid: str) -> dict:
        self.store.execute("DELETE FROM mcp_agent_bindings WHERE user_id=? AND connection_id=?", (user["id"], cid))
        return {"connection_id": cid, "bound": False}

    def binding_json(self, uid: str, cid: str) -> dict:
        b = self.store.one("SELECT * FROM mcp_agent_bindings WHERE user_id=? AND connection_id=?", (uid, cid))
        if not b:
            return {"connection_id": cid, "bound": False}
        return {"connection_id": cid, "bound": True, "alias": b["alias"], "enabled": bool(b["enabled"]),
                "resources_enabled": bool(b["resources_enabled"]), "tool_modes": _j(b["tool_modes_json"], {}) or {}}

    # ------------------------------------------------------------------ what the Agent sees
    def effective_tools(self, uid: str) -> list[dict]:
        """Every tool this person's Agent may use now: an enabled binding on a live connection, a
        usable tool (UNUSABLE), a grant that is not off and still matches the tool's schema, narrowed
        by the person's own modes. Deterministic order (alias, tool)."""
        return [entry for entry, _ in self._effective(uid)]

    def _live_bindings(self, uid: str, *, resources: bool = False) -> list[dict]:
        """This person's enabled bindings on connections they may use now, each row the binding's
        alias and modes beside the connection as read here: active, not stopped by the platform, not
        at a destination a stop closes."""
        if not self._spec().outbound_enabled:
            return []
        stopped = stopped_destinations(self.store)
        rows = self.store.all("SELECT b.alias, b.enabled, b.resources_enabled, b.tool_modes_json, c.* FROM mcp_agent_bindings b "
                              "JOIN mcp_connections c ON c.id=b.connection_id WHERE b.user_id=? AND b.enabled=1 AND c.deleted_at IS NULL "
                              "AND c.status='active' AND c.platform_disabled_at IS NULL"
                              + (" AND b.resources_enabled=1" if resources else "") + " ORDER BY b.alias", (uid,))
        return [r for r in rows if not self._destination_stopped(r["url"], stopped) and self.may_use(uid, r)]

    def _effective(self, uid: str) -> list[tuple[dict, dict]]:
        """effective_tools, each entry beside the connection row it was read from (call_tool compares
        the address the grant was read for with the one the call would go to)."""
        out = []
        for r in self._live_bindings(uid):
            personal_modes = _j(r["tool_modes_json"], {}) or {}
            tools = self.store.all("SELECT t.*, g.mode AS grant_mode, g.schema_hash AS grant_hash FROM mcp_connection_tools t "
                                   "LEFT JOIN mcp_tool_grants g ON g.connection_id=t.connection_id AND g.tool=t.name "
                                   "WHERE t.connection_id=? AND t.removed_at IS NULL AND t.unusable_code IS NULL "
                                   "ORDER BY t.position, t.name", (r["id"],))
            for t in tools:
                mode = t["grant_mode"] or "off"
                stale = bool(t["grant_mode"]) and t["grant_hash"] != t["schema_hash"]
                if stale:
                    mode = "off"
                own = personal_modes.get(t["name"])
                if own == "off" or (own == "confirm" and mode == "auto"):
                    mode = own
                if mode == "off":
                    continue
                ann = _j(t["annotations_json"], {}) or {}
                out.append(({"alias": r["alias"], "connection_id": r["id"], "connection_name": r["name"],
                             "owner_kind": r["owner_kind"], "owner_id": r["owner_id"], "tool": t["name"],
                             "title": t["title"], "description": t["description"] or "",
                             "input_schema": _j(t["input_schema_json"], {"type": "object"}),
                             "output_schema": _j(t["output_schema_json"]), "annotations": ann, "mode": mode,
                             "read_only": ann.get("readOnlyHint") is True, "schema_hash": t["schema_hash"],
                             "resources_enabled": bool(r["resources_enabled"])}, r))
        return out

    def resource_connections(self, uid: str) -> list[dict]:
        return [{"alias": r["alias"], "connection_id": r["id"], "connection_name": r["name"]}
                for r in self._live_bindings(uid, resources=True)]

    def _current(self, read: dict, unavailable: ServiceError) -> dict:
        """The connection row now, for a call admitted on `read` (the row its grant or binding was read
        from): refused as `unavailable` when it points elsewhere since — update() voided every grant
        and binding for the new server, and the request must not go there on the old ones."""
        conn = self._row(read["id"])
        if conn["url"] != read["url"]:
            raise unavailable
        return conn

    # ------------------------------------------------------------------ calls
    def _start_call(self, conn: dict, uid: str | None, origin: str, kind: str, name: str | None, *,
                    arguments: Any = None, conversation_id: str | None = None, turn_id: str | None = None,
                    tool_call_id: str | None = None) -> str:
        from synwevo_core.context import request_id
        call_id = new_id("mcpcall")
        self.store.execute("INSERT INTO mcp_calls (id, connection_id, user_id, origin, kind, name, conversation_id, turn_id, tool_call_id, "
                           "request_id, status, input_hash, input_preview, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,'running',?,?,?)",
                           (call_id, conn["id"], uid, origin, kind, name, conversation_id, turn_id, tool_call_id, request_id(),
                            fingerprint(arguments) if arguments is not None else None,
                            preview(arguments) if arguments is not None else None, now()))
        return call_id

    def _finish_call(self, call_id: str, status: str, *, result: Any = None, error: ServiceError | None = None,
                     duration_ms: int | None = None) -> None:
        t = now()
        row = self.store.one("SELECT started_at FROM mcp_calls WHERE id=?", (call_id,))
        dur = duration_ms if duration_ms is not None else (int((t - row["started_at"]) * 1000) if row else None)
        self.store.execute("UPDATE mcp_calls SET status=?, result_preview=?, error_code=?, error_message=?, finished_at=?, duration_ms=? "
                           "WHERE id=?", (status, preview(result) if result is not None else None,
                                          error.code if error else None, (error.message if error else None),
                                          t, dur, call_id))

    def call_tool(self, user: dict, alias: str, tool: str, arguments: dict, *, origin: str = "agent",
                  conversation_id: str | None = None, turn_id: str | None = None, tool_call_id: str | None = None,
                  should_stop=None, require_mode: str | None = None) -> dict:
        """One tool call for this person through a bound connection, recorded whatever happens.

        `require_mode="auto"` is the carrier that runs without asking (`agent.mcp.tool.read`): the
        tool must, now, be granted 'auto' and marked read-only by its server — a grant narrowed to
        'confirm' or a tool no longer read-only since the model was shown it is refused before any
        record or request."""
        self.require_outbound()
        uid = user["id"]
        unavailable = ServiceError(404, "mcp_tool_unavailable", "这个工具当前不可用：连接已停用、工具未授权，或服务更改了它的定义")
        entry, read = next(((e, r) for e, r in self._effective(uid) if e["alias"] == alias and e["tool"] == tool), (None, None))
        if entry is None:
            raise unavailable
        if require_mode == "auto" and not (entry["mode"] == "auto" and entry["read_only"]):
            raise ServiceError(409, "mcp_confirmation_required", "这个工具现在需要先经你确认才能执行")
        conn = self._current(read, unavailable)
        self._ready(conn)
        spec = self._spec()
        # A record exists before the request leaves, so a crash between the two leaves 'running'
        # rather than nothing — and the record never holds a credential.
        call_id = self._start_call(conn, uid, origin, "tools/call", tool, arguments=arguments,
                                   conversation_id=conversation_id, turn_id=turn_id, tool_call_id=tool_call_id)
        started = time.perf_counter()
        try:
            outcome: ToolOutcome = self.runtime.call_tool(conn, self.credential(conn), tool, arguments, user_id=uid,
                                                          timeout=spec.call_timeout_s, should_stop=should_stop)
        except ServiceError as err:
            status = {"mcp_timeout": "timeout", "mcp_cancelled": "cancelled"}.get(err.code, "failed")
            self._finish_call(call_id, status, error=err, duration_ms=int((time.perf_counter() - started) * 1000))
            self._failed(conn["id"], uid, err, url=conn["url"])
            raise
        text, cut = outcome.text, False
        if len(text) > spec.max_result_chars:
            text, cut = text[:spec.max_result_chars], True
        structured = outcome.structured
        if structured is not None and len(canonical(structured)) > spec.max_result_chars:
            structured, cut = None, True
        # Within the policy's characters, text and structured content together may still be more bytes
        # than the run record holds once this has returned — after the server acted: cut to fit first.
        result = _fit({"connection": alias, "connection_name": conn["name"], "tool": tool, "is_error": outcome.is_error,
                       "text": text, "structured": structured, "truncated": cut, "duration_ms": outcome.duration_ms,
                       "content_kinds": sorted({b.get("type", "?") for b in outcome.content}), "call_id": call_id},
                      (lambda r: {**r, "structured": None},), "text")
        self._finish_call(call_id, "succeeded", result={"is_error": outcome.is_error, "text": result["text"][:MAX_PREVIEW]},
                          duration_ms=outcome.duration_ms)
        self._health(conn["id"], "ok", url=conn["url"])
        return result

    def read_resource(self, user: dict, alias: str, uri: str, *, origin: str = "agent", conversation_id=None, turn_id=None,
                      tool_call_id=None) -> dict:
        self.require_outbound()
        uid = user["id"]
        unavailable = ServiceError(404, "mcp_resources_unavailable", "这个连接的资源没有开放给 Agent")
        read = next((r for r in self._live_bindings(uid, resources=True) if r["alias"] == alias), None)
        if read is None:
            raise unavailable
        conn = self._current(read, unavailable)
        return self._read(user, conn, alias, uri, origin=origin, conversation_id=conversation_id, turn_id=turn_id,
                          tool_call_id=tool_call_id)

    def read_resource_direct(self, user: dict, conn: dict, uri: str) -> dict:
        """A person reading a resource on the page: their own right to use the connection, no binding."""
        self.require_outbound()
        self._use(user["id"], conn)
        return self._read(user, conn, None, uri, origin="page")

    def _read(self, user: dict, conn: dict, alias: str | None, uri: str, *, origin: str, conversation_id=None, turn_id=None,
              tool_call_id=None) -> dict:
        uid = user["id"]
        self._ready(conn)
        spec = self._spec()
        if not isinstance(uri, str) or not uri or len(uri) > 2048:
            raise ServiceError(400, "invalid_input", "请提供资源地址", {"field": "uri"})
        call_id = self._start_call(conn, uid, origin, "resources/read", uri, conversation_id=conversation_id, turn_id=turn_id,
                                   tool_call_id=tool_call_id)
        try:
            out = self.runtime.read_resource(conn, self.credential(conn), uri, user_id=uid, timeout=spec.call_timeout_s,
                                             max_bytes=spec.max_resource_bytes)
        except ServiceError as err:
            self._finish_call(call_id, "timeout" if err.code == "mcp_timeout" else "failed", error=err)
            self._failed(conn["id"], uid, err, url=conn["url"])
            raise
        text = out.text
        cut = False
        if text is not None and len(text) > spec.max_result_chars:
            text, cut = text[:spec.max_result_chars], True
        self._finish_call(call_id, "succeeded", result={"bytes": out.bytes_total}, duration_ms=out.duration_ms)
        self._health(conn["id"], "ok", url=conn["url"])
        # What each content says of itself beyond its text is the server's to make as long as it likes:
        # past what the run record holds, only each one's address and type are kept, then none.
        return _fit({"connection": alias or conn["slug"], "connection_name": conn["name"], "uri": uri, "text": text, "truncated": cut,
                     "contents": [{k: v for k, v in c.items() if k != "text"} for c in out.contents], "bytes": out.bytes_total,
                     "duration_ms": out.duration_ms, "call_id": call_id},
                    (lambda r: {**r, "contents": [_content_ref(c) for c in r["contents"]]}, lambda r: {**r, "contents": []}), "text")

    def list_resources(self, user: dict, cid: str) -> list[dict]:
        conn = self._row(cid)
        self._use(user["id"], conn)
        return self.store.all("SELECT uri, name, title, description, mime_type, size, last_seen_at FROM mcp_connection_resources "
                              "WHERE connection_id=? AND removed_at IS NULL ORDER BY uri", (cid,))

    def render_prompt(self, user: dict, cid: str, name: str, arguments: dict) -> dict:
        """A server prompt rendered for the person to read and, if they choose, send as their own
        message: it never reaches the model by itself and never changes the Agent's rules."""
        self.require_outbound()
        conn = self._row(cid)
        self._use(user["id"], conn)
        self._ready(conn)
        row = self.store.one("SELECT * FROM mcp_connection_prompts WHERE connection_id=? AND name=? AND removed_at IS NULL", (cid, name))
        if not row:
            raise ServiceError(404, "mcp_prompt_not_found", "这个提示词不存在或已被服务移除")
        declared = {a.get("name"): a for a in (_j(row["arguments_json"], []) or []) if isinstance(a, dict)}
        clean = {}
        for key, value in (arguments or {}).items():
            if key not in declared:
                raise ServiceError(400, "invalid_input", "提示词不接受这个参数", {"field": key})
            if not isinstance(value, str) or len(value) > 4000:
                raise ServiceError(400, "invalid_input", "提示词参数必须是字符串（最多 4000 字）", {"field": key})
            clean[key] = value
        missing = [n for n, a in declared.items() if a.get("required") and n not in clean]
        if missing:
            raise ServiceError(400, "invalid_input", "缺少必填参数", {"missing": missing})
        spec = self._spec()
        call_id = self._start_call(conn, user["id"], "page", "prompts/get", name, arguments=clean)
        try:
            out = self.runtime.get_prompt(conn, self.credential(conn), name, clean, user_id=user["id"], timeout=spec.call_timeout_s)
        except ServiceError as err:
            self._finish_call(call_id, "timeout" if err.code == "mcp_timeout" else "failed", error=err)
            self._failed(conn["id"], user["id"], err, url=conn["url"])
            raise
        text = out.text[:spec.max_result_chars]
        self._finish_call(call_id, "succeeded", result={"chars": len(out.text)}, duration_ms=out.duration_ms)
        self._health(conn["id"], "ok", url=conn["url"])
        return _fit({"name": name, "description": out.description, "text": text, "truncated": len(out.text) > len(text),
                     "messages": out.messages, "duration_ms": out.duration_ms},
                    (lambda r: {**r, "messages": []}, lambda r: {**r, "description": None}), "text")

    # ------------------------------------------------------------------ views
    def _owner_name(self, conn: dict) -> str | None:
        if conn["owner_kind"] == "workspace":
            ws = self.identity.get_workspace(conn["owner_id"])
            return ws["name"] if ws else None
        return None

    def summary(self, conn: dict, uid: str, *, stopped: dict | None = None) -> dict:
        counts = self.store.one("SELECT (SELECT COUNT(*) FROM mcp_connection_tools WHERE connection_id=? AND removed_at IS NULL) AS tools, "
                                "(SELECT COUNT(*) FROM mcp_connection_resources WHERE connection_id=? AND removed_at IS NULL) AS resources, "
                                "(SELECT COUNT(*) FROM mcp_connection_prompts WHERE connection_id=? AND removed_at IS NULL) AS prompts, "
                                "(SELECT COUNT(*) FROM mcp_tool_grants g JOIN mcp_connection_tools t ON t.connection_id=g.connection_id "
                                " AND t.name=g.tool WHERE g.connection_id=? AND g.mode<>'off' AND t.removed_at IS NULL "
                                " AND t.unusable_code IS NULL AND g.schema_hash=t.schema_hash) AS granted, "
                                "(SELECT MAX(started_at) FROM mcp_calls WHERE connection_id=? AND origin='agent') AS last_call_at",
                                (conn["id"], conn["id"], conn["id"], conn["id"], conn["id"]))
        parts = urlsplit(conn["url"])
        return {"id": conn["id"], "owner_kind": conn["owner_kind"], "owner_id": conn["owner_id"],
                "owner_name": self._owner_name(conn), "slug": conn["slug"], "name": conn["name"],
                "description": conn["description"], "url": conn["url"], "host": parts.hostname,
                "auth_kind": conn["auth_kind"], "credential_hint": conn.get("credential_hint"),
                "has_credential": bool(conn.get("credential_sealed")),
                "oauth_client": _j(conn.get("oauth_client_json")), "status": conn["status"],
                "platform_disabled": bool(conn.get("platform_disabled_at")), "platform_disabled_at": conn.get("platform_disabled_at"),
                # Another connection's platform stop closes this one's address too (_destination_open).
                "destination_stopped": not conn.get("platform_disabled_at") and self._destination_stopped(
                    conn["url"], stopped if stopped is not None else stopped_destinations(self.store)),
                "health": conn["health"], "health_checked_at": conn.get("health_checked_at"), "last_ok_at": conn.get("last_ok_at"),
                "last_error": {"code": conn["last_error_code"], "message": conn["last_error_message"],
                               "cause": conn.get("last_error_cause")} if conn.get("last_error_code") else None,
                "circuit_open_until": conn.get("open_until") if (conn.get("open_until") or 0) > now() else None,
                "protocol_version": conn.get("protocol_version"), "server_info": _j(conn.get("server_info_json")),
                "capabilities": _j(conn.get("capabilities_json"), {}) or {}, "discovered_at": conn.get("discovered_at"),
                "counts": {"tools": counts["tools"], "resources": counts["resources"], "prompts": counts["prompts"],
                           "granted": counts["granted"]},
                "last_call_at": counts["last_call_at"], "manage": self.manages(uid, conn["owner_kind"], conn["owner_id"]),
                "binding": self.binding_json(uid, conn["id"]),
                "created_at": conn["created_at"], "updated_at": conn["updated_at"]}

    def list(self, user: dict, *, q: str = "", workspace_id: str | None = None) -> list[dict]:
        uid = user["id"]
        rows = self.store.all("SELECT * FROM mcp_connections WHERE deleted_at IS NULL AND ((owner_kind='user' AND owner_id=?) "
                              "OR (owner_kind='workspace' AND owner_id IN (SELECT workspace_id FROM memberships WHERE user_id=?))) "
                              "ORDER BY owner_kind DESC, name", (uid, uid))
        q = (q or "").strip().lower()
        stopped = stopped_destinations(self.store)
        out = []
        for r in rows:
            if workspace_id is not None and not (r["owner_kind"] == "workspace" and r["owner_id"] == workspace_id):
                continue
            if not self.may_use(uid, r):
                continue
            if q and q not in r["name"].lower() and q not in r["url"].lower() and q not in (r["description"] or "").lower():
                continue
            out.append(self.summary(r, uid, stopped=stopped))
        return out

    def detail(self, user: dict, cid: str) -> dict:
        conn = self._row(cid)
        uid = user["id"]
        self._use(uid, conn)
        base = self.summary(conn, uid)
        tools = self.store.all("SELECT t.*, g.mode AS grant_mode, g.schema_hash AS grant_hash, g.granted_by, g.updated_at AS granted_at "
                               "FROM mcp_connection_tools t LEFT JOIN mcp_tool_grants g ON g.connection_id=t.connection_id AND g.tool=t.name "
                               "WHERE t.connection_id=? ORDER BY t.removed_at IS NOT NULL, t.position, t.name", (cid,))
        personal = base["binding"].get("tool_modes") or {}
        base["tools"] = [{
            "name": t["name"], "title": t["title"], "description": t["description"],
            # An unusable tool's schema was not kept: the page shows why instead (`unusable`).
            "input_schema": None if t["unusable_code"] else _j(t["input_schema_json"], {}),
            "output_schema": None if t["unusable_code"] else _j(t["output_schema_json"]),
            "annotations": _j(t["annotations_json"], {}) or {}, "unusable": _unusable(t["unusable_code"]),
            "schema_hash": t["schema_hash"], "removed": t["removed_at"] is not None,
            "grant": {"mode": t["grant_mode"] or "off", "stale": bool(t["grant_mode"]) and t["grant_hash"] != t["schema_hash"],
                      "granted_by": t["granted_by"], "granted_at": t["granted_at"]},
            "personal_mode": personal.get(t["name"]), "first_seen_at": t["first_seen_at"], "last_seen_at": t["last_seen_at"]}
            for t in tools]
        base["resources"] = self.store.all("SELECT uri, name, title, description, mime_type, size FROM mcp_connection_resources "
                                           "WHERE connection_id=? AND removed_at IS NULL ORDER BY uri", (cid,))
        base["prompts"] = [{**p, "arguments": _j(p.pop("arguments_json"), []) or []} for p in self.store.all(
            "SELECT name, title, description, arguments_json FROM mcp_connection_prompts WHERE connection_id=? AND removed_at IS NULL "
            "ORDER BY name", (cid,))]
        base["instructions"] = conn.get("instructions")
        base["calls"] = self.calls(user, cid, limit=20)
        return base

    def calls(self, user: dict, cid: str, *, limit: int = 50, before: float | None = None) -> list[dict]:
        conn = self._row(cid)
        uid = user["id"]
        self._use(uid, conn)
        settle_interrupted_calls(self.store, connection_id=cid)
        manage = self.manages(uid, conn["owner_kind"], conn["owner_id"])
        limit = max(1, min(int(limit), 200))
        if before is None:
            rows = self.store.all("SELECT * FROM mcp_calls WHERE connection_id=? ORDER BY started_at DESC LIMIT ?", (cid, limit))
        else:
            rows = self.store.all("SELECT * FROM mcp_calls WHERE connection_id=? AND started_at<? ORDER BY started_at DESC LIMIT ?",
                                  (cid, float(before), limit))
        out = []
        for r in rows:
            own = r["user_id"] == uid
            out.append({"id": r["id"], "origin": r["origin"], "kind": r["kind"], "name": r["name"], "status": r["status"],
                        "user_id": r["user_id"] if (manage or own) else None,
                        "conversation_id": r["conversation_id"] if own else None, "turn_id": r["turn_id"] if own else None,
                        "tool_call_id": r["tool_call_id"] if own else None, "request_id": r["request_id"],
                        "input_preview": r["input_preview"] if own else None, "result_preview": r["result_preview"] if own else None,
                        "error": {"code": r["error_code"], "message": r["error_message"]} if r["error_code"] else None,
                        "started_at": r["started_at"], "finished_at": r["finished_at"], "duration_ms": r["duration_ms"]})
        return out

    def my_bindings(self, user: dict) -> list[dict]:
        uid = user["id"]
        stopped = stopped_destinations(self.store)
        return [self.summary(c, uid, stopped=stopped) for c in self.store.all(
            "SELECT c.* FROM mcp_agent_bindings b JOIN mcp_connections c ON c.id=b.connection_id WHERE b.user_id=? AND c.deleted_at IS NULL "
            "ORDER BY b.alias", (uid,)) if self.may_use(uid, c)]
