"""Connections to external MCP servers: whose they are, what they offer, who may use which tool,
and every call made through them.

A connection belongs to one person or to one workspace. The person manages their own; a
workspace's owners and administrators manage the workspace's, and its members may use it. Using a
connection in the Agent is a second, personal decision (`mcp_agent_bindings`): nobody's Agent
gains tools because a workspace they belong to added a server. Each tool has a grant on the
connection (`off`, `confirm`, `auto`) pinned to the schema it was made for; a binding may narrow a
grant for one person, never widen it.

Credentials are sealed with the platform's root key and only ever unsealed into an HTTP header
for one exchange. Previews kept with calls are bounded JSON of the arguments and the result text;
the sealed values never enter them.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit

from synwevo_core import sealed
from synwevo_core.auth import audit_event
from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.machine import MANAGE_ROLES
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.store import new_id, now

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


def tool_hash(tool: dict) -> str:
    """What a grant is pinned to: the tool's name, input and output schemas, and the two hints a
    grant's mode depends on. A new description does not void a grant (it is untrusted text
    anyway); a new shape or a flipped read-only hint does."""
    ann = tool.get("annotations") or {}
    return fingerprint({"name": tool.get("name"), "input": tool.get("inputSchema"), "output": tool.get("outputSchema"),
                        "readOnly": ann.get("readOnlyHint"), "destructive": ann.get("destructiveHint")})


class Connections:
    def __init__(self, store, identity, runtime: McpRuntime):
        self.store = store
        self.identity = identity
        self.runtime = runtime
        self.policy = McpPolicy(store)

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

    def manages(self, user_id: str, owner_kind: str, owner_id: str) -> bool:
        if owner_kind == "user":
            return owner_id == user_id
        member = self.identity.membership(owner_id, user_id)
        return bool(member and member["role"] in MANAGE_ROLES)

    def may_use(self, user_id: str, conn: dict) -> bool:
        if conn["owner_kind"] == "user":
            return conn["owner_id"] == user_id
        ws = self.identity.get_workspace(conn["owner_id"])
        return bool(ws and ws["status"] == "active" and self.identity.membership(conn["owner_id"], user_id))

    def _manage(self, user_id: str, conn: dict) -> None:
        if not self.manages(user_id, conn["owner_kind"], conn["owner_id"]):
            raise ServiceError(403, "mcp_manage_forbidden", "只有连接的所有者（个人）或工作空间的所有者、管理员可以管理这个连接")

    def _use(self, user_id: str, conn: dict) -> None:
        if not self.may_use(user_id, conn):
            raise ServiceError(404, "mcp_connection_not_found", "连接不存在")

    # ------------------------------------------------------------------ CRUD
    def _validate_url(self, url: str) -> str:
        url = (url or "").strip()
        if not url or len(url) > MAX_URL or any(ch.isspace() for ch in url):
            raise ServiceError(400, "mcp_url_invalid", "请输入有效的 MCP 服务地址", {"field": "url"})
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.fragment or parts.username or parts.password:
            raise ServiceError(400, "mcp_url_invalid", "MCP 服务地址必须是 http(s) 绝对地址，不含片段或凭据", {"field": "url"})
        if parts.scheme == "http" and not self._spec().allow_http and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ServiceError(400, "mcp_url_invalid", "MCP 服务地址必须使用 https（管理员可在策略中放开 http）", {"field": "url"})
        refusal = self.policy.destination_refusal(parts.hostname)
        if refusal:
            raise ServiceError(403, refusal, "平台策略不允许连接这个地址", {"host": parts.hostname, "field": "url"})
        return url

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
        if not self.manages(uid, owner_kind, owner_id):
            raise ServiceError(403, "mcp_manage_forbidden", "只有工作空间的所有者或管理员可以为工作空间添加连接")
        if owner_kind == "workspace":
            ws = self.identity.get_workspace(owner_id)
            if not ws or ws["status"] != "active":
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
               oauth_client: dict | None = None) -> dict:
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
        if url is not None and url.strip() != conn["url"]:
            self.require_outbound()
            url = self._validate_url(url)
            # A new address is a new server: what was discovered and granted for the old one is void.
            sets += ["url=?", "health='unknown'", "discovered_at=NULL", "discovery_hash=NULL", "server_info_json=NULL",
                     "capabilities_json=NULL", "protocol_version=NULL", "failure_streak=0", "open_until=NULL"]
            args += [url]; changed.append("url")
        if auth_kind is not None or credential is not None or oauth_client is not None:
            kind = auth_kind or conn["auth_kind"]
            if kind not in ("none", "bearer", "oauth"):
                raise ServiceError(400, "invalid_input", "未知的认证方式", {"field": "auth_kind"})
            sets += ["auth_kind=?"]; args += [kind]; changed.append("auth")
            if kind == "bearer":
                if credential is None or not credential.strip():
                    if conn["auth_kind"] != "bearer":
                        raise ServiceError(400, "invalid_input", "请提供访问令牌", {"field": "credential"})
                else:
                    sets += ["credential_sealed=?", "credential_hint=?", "health='unknown'", "failure_streak=0", "open_until=NULL"]
                    args += [sealed.seal(self.store, SEAL, credential.strip()),
                             ("…" + credential.strip()[-4:]) if len(credential.strip()) >= 8 else None]
            elif kind == "none":
                sets += ["credential_sealed=NULL", "credential_hint=NULL", "oauth_client_json=NULL",
                         "oauth_client_secret_sealed=NULL"]
            else:
                if oauth_client is not None:
                    client_id = (oauth_client.get("client_id") or "").strip()
                    sets += ["oauth_client_json=?", "oauth_client_secret_sealed=?", "credential_sealed=NULL",
                             "credential_hint=NULL", "health='auth_required'"]
                    secret = (oauth_client.get("client_secret") or "").strip()
                    args += [json.dumps({"kind": "preregistered", "client_id": client_id}) if client_id else None,
                             sealed.seal(self.store, SEAL, secret) if secret else None]
                elif conn["auth_kind"] != "oauth":
                    sets += ["credential_sealed=NULL", "credential_hint=NULL", "health='auth_required'"]
        if not sets:
            return self.summary(conn, uid)
        with self.store.tx():
            self.store.execute(f"UPDATE mcp_connections SET {', '.join(sets)}, updated_at=? WHERE id=?", (*args, now(), cid))
            if "url" in changed:
                self.store.execute("UPDATE mcp_connection_tools SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (now(), cid))
                self.store.execute("UPDATE mcp_connection_resources SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (now(), cid))
                self.store.execute("UPDATE mcp_connection_prompts SET removed_at=COALESCE(removed_at, ?) WHERE connection_id=?", (now(), cid))
            audit_event(self.store, "suite.agent.mcp.connection.update", actor_kind="user", actor_id=uid,
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid, meta={"changed": changed})
        return self.summary(self._row(cid), uid)

    def set_status(self, user: dict, cid: str, status: str) -> dict:
        if status not in ("active", "disabled"):
            raise ServiceError(400, "invalid_input", "状态只能是 active 或 disabled")
        conn = self._row(cid)
        self._manage(user["id"], conn)
        with self.store.tx():
            self.store.execute("UPDATE mcp_connections SET status=?, updated_at=? WHERE id=?", (status, now(), cid))
            audit_event(self.store, "suite.agent.mcp.connection.disable" if status == "disabled" else "suite.agent.mcp.connection.enable",
                        actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid)
        return self.summary(self._row(cid), user["id"])

    def delete(self, user: dict, cid: str) -> dict:
        conn = self._row(cid)
        self._manage(user["id"], conn)
        with self.store.tx():
            # The sealed credentials go with the row's usefulness: nothing can be unsealed again.
            self.store.execute("UPDATE mcp_connections SET deleted_at=?, status='disabled', credential_sealed=NULL, "
                               "credential_hint=NULL, oauth_client_secret_sealed=NULL, updated_at=? WHERE id=?",
                               (now(), now(), cid))
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
            self.store.execute("UPDATE mcp_connections SET credential_sealed=NULL, credential_hint=NULL, "
                               "health=CASE WHEN auth_kind='none' THEN health ELSE 'auth_required' END, updated_at=? WHERE id=?",
                               (now(), cid))
            audit_event(self.store, "suite.agent.mcp.credential.revoke", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid)
        return self.summary(self._row(cid), user["id"])

    # ------------------------------------------------------------------ credentials
    def credential(self, conn: dict) -> str | None:
        if conn["auth_kind"] == "none" or not conn.get("credential_sealed"):
            return None
        try:
            raw = sealed.unseal(self.store, SEAL, conn["credential_sealed"])
        except Exception:  # noqa: BLE001 — a credential that no longer opens is as good as none
            log.warning("mcp credential of connection %s could not be unsealed", conn["id"])
            return None
        if conn["auth_kind"] == "bearer":
            return raw
        tokens = _j(raw, {})
        if not isinstance(tokens, dict):
            return None
        if tokens.get("expires_at") and tokens["expires_at"] <= now() + 30 and tokens.get("refresh_token"):
            from .oauth import OutboundOAuth
            try:
                tokens = OutboundOAuth(self).refresh(conn, tokens)
            except ServiceError as err:
                self._health(conn["id"], "auth_required", err.code, err.message)
                return None
        return tokens.get("access_token")

    def store_tokens(self, cid: str, tokens: dict) -> None:
        keep = {k: tokens.get(k) for k in ("access_token", "refresh_token", "expires_at", "scope", "token_type")}
        self.store.execute("UPDATE mcp_connections SET credential_sealed=?, credential_hint=?, health='unknown', "
                           "failure_streak=0, open_until=NULL, updated_at=? WHERE id=?",
                           (sealed.seal(self.store, SEAL, json.dumps(keep)), "oauth", now(), cid))

    # ------------------------------------------------------------------ health and breaker
    def _health(self, cid: str, state: str, code: str | None = None, message: str | None = None) -> None:
        t = now()
        if state == "ok":
            self.store.execute("UPDATE mcp_connections SET health='ok', health_checked_at=?, last_ok_at=?, last_error_code=NULL, "
                               "last_error_message=NULL, failure_streak=0, open_until=NULL, updated_at=? WHERE id=?", (t, t, t, cid))
            return
        spec = self._spec()
        self.store.execute("UPDATE mcp_connections SET health=?, health_checked_at=?, last_error_code=?, last_error_message=?, "
                           "failure_streak=failure_streak+1, updated_at=? WHERE id=?",
                           (state, t, code, (message or "")[:500], t, cid))
        row = self.store.one("SELECT failure_streak FROM mcp_connections WHERE id=?", (cid,))
        if row and row["failure_streak"] >= spec.breaker_failures:
            self.store.execute("UPDATE mcp_connections SET open_until=? WHERE id=?", (t + spec.breaker_open_s, cid))

    def _ready(self, conn: dict, *, for_use: bool = True) -> None:
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
            self._health(cid, "auth_required" if err.code == "mcp_auth_required" else "failed", err.code, err.message)
            raise
        first = conn["discovered_at"] is None and conn["owner_kind"] == "user"
        with self.store.tx():
            self._record_discovery(conn, found, grant_new=first, granted_by=user["id"])
            self._health(cid, "ok")
            self._finish_call(call_id, "succeeded", result={"tools": len(found.tools), "resources": len(found.resources),
                                                            "prompts": len(found.prompts), "protocol": found.protocol_version})
        return self.detail(user, cid)

    def _record_discovery(self, conn: dict, found: Discovery, *, grant_new: bool, granted_by: str) -> None:
        cid, t = conn["id"], now()
        seen_tools = set()
        for i, tool in enumerate(found.tools):
            name = tool.get("name")
            if not isinstance(name, str) or not name or len(name) > 128:
                continue
            seen_tools.add(name)
            h = tool_hash(tool)
            ann = tool.get("annotations") or {}
            row = self.store.one("SELECT schema_hash FROM mcp_connection_tools WHERE connection_id=? AND name=?", (cid, name))
            if row:
                self.store.execute("UPDATE mcp_connection_tools SET title=?, description=?, input_schema_json=?, output_schema_json=?, "
                                   "annotations_json=?, schema_hash=?, position=?, last_seen_at=?, removed_at=NULL "
                                   "WHERE connection_id=? AND name=?",
                                   (tool.get("title"), (tool.get("description") or "")[:4000], json.dumps(tool.get("inputSchema") or {"type": "object"}),
                                    json.dumps(tool.get("outputSchema")) if tool.get("outputSchema") is not None else None,
                                    json.dumps(ann) if ann else None, h, i, t, cid, name))
            else:
                self.store.execute("INSERT INTO mcp_connection_tools (connection_id, name, title, description, input_schema_json, "
                                   "output_schema_json, annotations_json, schema_hash, position, first_seen_at, last_seen_at) "
                                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (cid, name, tool.get("title"), (tool.get("description") or "")[:4000],
                                    json.dumps(tool.get("inputSchema") or {"type": "object"}),
                                    json.dumps(tool.get("outputSchema")) if tool.get("outputSchema") is not None else None,
                                    json.dumps(ann) if ann else None, h, i, t, t))
                if grant_new:
                    # A person's own first look at their own server: every tool usable, each one
                    # asking before it runs. Tools that arrive later are not enabled by their arrival.
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
            self.store.execute("INSERT INTO mcp_connection_resources (connection_id, uri, name, title, description, mime_type, size, "
                               "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id, uri) DO UPDATE SET "
                               "name=excluded.name, title=excluded.title, description=excluded.description, mime_type=excluded.mime_type, "
                               "size=excluded.size, last_seen_at=excluded.last_seen_at, removed_at=NULL",
                               (cid, uri, res.get("name"), res.get("title"), (res.get("description") or "")[:2000], res.get("mimeType"),
                                res.get("size") if isinstance(res.get("size"), int) else None, t, t))
        for g in self.store.all("SELECT uri FROM mcp_connection_resources WHERE connection_id=? AND removed_at IS NULL", (cid,)):
            if g["uri"] not in seen_res:
                self.store.execute("UPDATE mcp_connection_resources SET removed_at=? WHERE connection_id=? AND uri=?", (t, cid, g["uri"]))
        seen_prompts = set()
        for pr in found.prompts:
            name = pr.get("name")
            if not isinstance(name, str) or not name or len(name) > 128:
                continue
            seen_prompts.add(name)
            self.store.execute("INSERT INTO mcp_connection_prompts (connection_id, name, title, description, arguments_json, "
                               "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(connection_id, name) DO UPDATE SET "
                               "title=excluded.title, description=excluded.description, arguments_json=excluded.arguments_json, "
                               "last_seen_at=excluded.last_seen_at, removed_at=NULL",
                               (cid, name, pr.get("title"), (pr.get("description") or "")[:2000],
                                json.dumps(pr.get("arguments") or []), t, t))
        for g in self.store.all("SELECT name FROM mcp_connection_prompts WHERE connection_id=? AND removed_at IS NULL", (cid,)):
            if g["name"] not in seen_prompts:
                self.store.execute("UPDATE mcp_connection_prompts SET removed_at=? WHERE connection_id=? AND name=?", (t, cid, g["name"]))
        self.store.execute("UPDATE mcp_connections SET protocol_version=?, server_info_json=?, capabilities_json=?, instructions=?, "
                           "discovered_at=?, discovery_hash=?, updated_at=? WHERE id=?",
                           (found.protocol_version, json.dumps(found.server_info) if found.server_info else None,
                            json.dumps(found.capabilities), (found.instructions or "")[:4000] or None, t,
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
        grant that is not off and still matches the tool's schema, narrowed by the person's own
        modes. Deterministic order (alias, tool)."""
        if not self._spec().outbound_enabled:
            return []
        out = []
        rows = self.store.all("SELECT b.alias, b.enabled, b.resources_enabled, b.tool_modes_json, c.* FROM mcp_agent_bindings b "
                              "JOIN mcp_connections c ON c.id=b.connection_id WHERE b.user_id=? AND b.enabled=1 AND c.deleted_at IS NULL "
                              "AND c.status='active' ORDER BY b.alias", (uid,))
        for r in rows:
            if not self.may_use(uid, r):
                continue
            personal_modes = _j(r["tool_modes_json"], {}) or {}
            tools = self.store.all("SELECT t.*, g.mode AS grant_mode, g.schema_hash AS grant_hash FROM mcp_connection_tools t "
                                   "LEFT JOIN mcp_tool_grants g ON g.connection_id=t.connection_id AND g.tool=t.name "
                                   "WHERE t.connection_id=? AND t.removed_at IS NULL ORDER BY t.position, t.name", (r["id"],))
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
                out.append({"alias": r["alias"], "connection_id": r["id"], "connection_name": r["name"],
                            "owner_kind": r["owner_kind"], "owner_id": r["owner_id"], "tool": t["name"],
                            "title": t["title"], "description": t["description"] or "",
                            "input_schema": _j(t["input_schema_json"], {"type": "object"}),
                            "output_schema": _j(t["output_schema_json"]), "annotations": ann, "mode": mode,
                            "read_only": ann.get("readOnlyHint") is True, "schema_hash": t["schema_hash"],
                            "resources_enabled": bool(r["resources_enabled"])})
        return out

    def resource_connections(self, uid: str) -> list[dict]:
        if not self._spec().outbound_enabled:
            return []
        rows = self.store.all("SELECT b.alias, c.* FROM mcp_agent_bindings b JOIN mcp_connections c ON c.id=b.connection_id "
                              "WHERE b.user_id=? AND b.enabled=1 AND b.resources_enabled=1 AND c.deleted_at IS NULL AND c.status='active' "
                              "ORDER BY b.alias", (uid,))
        return [{"alias": r["alias"], "connection_id": r["id"], "connection_name": r["name"]} for r in rows if self.may_use(uid, r)]

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
                  should_stop=None) -> dict:
        """One tool call for this person through a bound connection, recorded whatever happens."""
        self.require_outbound()
        uid = user["id"]
        entry = next((e for e in self.effective_tools(uid) if e["alias"] == alias and e["tool"] == tool), None)
        if entry is None:
            raise ServiceError(404, "mcp_tool_unavailable", "这个工具当前不可用：连接已停用、工具未授权，或服务更改了它的定义")
        conn = self._row(entry["connection_id"])
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
            if err.code not in ("mcp_cancelled",):
                self._health(conn["id"], "auth_required" if err.code == "mcp_auth_required" else "failed", err.code, err.message)
            raise
        text, cut = outcome.text, False
        if len(text) > spec.max_result_chars:
            text, cut = text[:spec.max_result_chars], True
        structured = outcome.structured
        if structured is not None and len(canonical(structured)) > spec.max_result_chars:
            structured, cut = None, True
        result = {"connection": alias, "connection_name": conn["name"], "tool": tool, "is_error": outcome.is_error,
                  "text": text, "structured": structured, "truncated": cut, "duration_ms": outcome.duration_ms,
                  "content_kinds": sorted({b.get("type", "?") for b in outcome.content}), "call_id": call_id}
        self._finish_call(call_id, "succeeded", result={"is_error": outcome.is_error, "text": text[:MAX_PREVIEW]},
                          duration_ms=outcome.duration_ms)
        self._health(conn["id"], "ok")
        return result

    def read_resource(self, user: dict, alias: str, uri: str, *, origin: str = "agent", conversation_id=None, turn_id=None,
                      tool_call_id=None) -> dict:
        self.require_outbound()
        uid = user["id"]
        entry = next((e for e in self.resource_connections(uid) if e["alias"] == alias), None)
        if entry is None:
            raise ServiceError(404, "mcp_resources_unavailable", "这个连接的资源没有开放给 Agent")
        conn = self._row(entry["connection_id"])
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
            if err.code != "mcp_resource_too_large":
                self._health(conn["id"], "auth_required" if err.code == "mcp_auth_required" else "failed", err.code, err.message)
            raise
        text = out.text
        cut = False
        if text is not None and len(text) > spec.max_result_chars:
            text, cut = text[:spec.max_result_chars], True
        self._finish_call(call_id, "succeeded", result={"bytes": out.bytes_total}, duration_ms=out.duration_ms)
        self._health(conn["id"], "ok")
        return {"connection": alias or conn["slug"], "connection_name": conn["name"], "uri": uri, "text": text, "truncated": cut,
                "contents": [{k: v for k, v in c.items() if k != "text"} for c in out.contents], "bytes": out.bytes_total,
                "duration_ms": out.duration_ms, "call_id": call_id}

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
            self._health(conn["id"], "auth_required" if err.code == "mcp_auth_required" else "failed", err.code, err.message)
            raise
        text = out.text[:spec.max_result_chars]
        self._finish_call(call_id, "succeeded", result={"chars": len(out.text)}, duration_ms=out.duration_ms)
        self._health(conn["id"], "ok")
        return {"name": name, "description": out.description, "text": text, "truncated": len(out.text) > len(text),
                "messages": out.messages, "duration_ms": out.duration_ms}

    # ------------------------------------------------------------------ views
    def _owner_name(self, conn: dict) -> str | None:
        if conn["owner_kind"] == "workspace":
            ws = self.identity.get_workspace(conn["owner_id"])
            return ws["name"] if ws else None
        return None

    def summary(self, conn: dict, uid: str) -> dict:
        counts = self.store.one("SELECT (SELECT COUNT(*) FROM mcp_connection_tools WHERE connection_id=? AND removed_at IS NULL) AS tools, "
                                "(SELECT COUNT(*) FROM mcp_connection_resources WHERE connection_id=? AND removed_at IS NULL) AS resources, "
                                "(SELECT COUNT(*) FROM mcp_connection_prompts WHERE connection_id=? AND removed_at IS NULL) AS prompts, "
                                "(SELECT COUNT(*) FROM mcp_tool_grants g JOIN mcp_connection_tools t ON t.connection_id=g.connection_id "
                                " AND t.name=g.tool WHERE g.connection_id=? AND g.mode<>'off' AND t.removed_at IS NULL "
                                " AND g.schema_hash=t.schema_hash) AS granted, "
                                "(SELECT MAX(started_at) FROM mcp_calls WHERE connection_id=? AND origin='agent') AS last_call_at",
                                (conn["id"], conn["id"], conn["id"], conn["id"], conn["id"]))
        parts = urlsplit(conn["url"])
        return {"id": conn["id"], "owner_kind": conn["owner_kind"], "owner_id": conn["owner_id"],
                "owner_name": self._owner_name(conn), "slug": conn["slug"], "name": conn["name"],
                "description": conn["description"], "url": conn["url"], "host": parts.hostname,
                "auth_kind": conn["auth_kind"], "credential_hint": conn.get("credential_hint"),
                "has_credential": bool(conn.get("credential_sealed")),
                "oauth_client": _j(conn.get("oauth_client_json")), "status": conn["status"],
                "health": conn["health"], "health_checked_at": conn.get("health_checked_at"), "last_ok_at": conn.get("last_ok_at"),
                "last_error": {"code": conn["last_error_code"], "message": conn["last_error_message"]} if conn.get("last_error_code") else None,
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
        out = []
        for r in rows:
            if workspace_id is not None and not (r["owner_kind"] == "workspace" and r["owner_id"] == workspace_id):
                continue
            if r["owner_kind"] == "workspace":
                ws = self.identity.get_workspace(r["owner_id"])
                if not ws or ws["status"] != "active":
                    continue
            if q and q not in r["name"].lower() and q not in r["url"].lower() and q not in (r["description"] or "").lower():
                continue
            out.append(self.summary(r, uid))
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
            "name": t["name"], "title": t["title"], "description": t["description"], "input_schema": _j(t["input_schema_json"], {}),
            "output_schema": _j(t["output_schema_json"]), "annotations": _j(t["annotations_json"], {}) or {},
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
        return [self.summary(c, uid) for c in self.store.all(
            "SELECT c.* FROM mcp_agent_bindings b JOIN mcp_connections c ON c.id=b.connection_id WHERE b.user_id=? AND c.deleted_at IS NULL "
            "ORDER BY b.alias", (uid,)) if self.may_use(uid, c)]
