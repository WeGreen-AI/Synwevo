"""OAuth Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document-00): an MCP
client that has no prior relationship with this platform identifies itself by an https URL, and
the document at that URL says who it is and where its callbacks go.

The authorization server fetches the document when it first sees such a `client_id`, checks it,
and keeps what it accepted as an ordinary client registration (`oauth_clients`, with
``registration='cimd'``) so the console can see it, disable it, and the token endpoint can
authenticate it like any other public client. The fetch is the one outbound request the account
service makes on an external party's say-so, so it goes through the same address check and
pinned connection as webhook deliveries (`webhook_delivery`): https only, public addresses only,
no redirects, a bounded body, a deadline — only when the platform's MCP policy allows this
registration mechanism at all, for the domains it names, and only for a request a signed-in person
made (`resolve(may_fetch=...)`): an anonymous caller cannot make this server fetch and store a
document for every URL it can name.

A stored registration is not a standing permission. Every authorization of it asks the policy
again and fetches the document again once it is older than `REFRESH_AFTER_S`, and the tokens it
holds stop working when the policy no longer accepts it (`withdrawn_by_policy`, asked by the token
endpoint and the MCP server's gate) — the policy decides who may use the mechanism, not only who
may start using it.

What a document may and may not do: it registers a **public** client (``token_endpoint_auth_method``
``none``, PKCE required) with the redirect URIs it lists and the identity + MCP scopes; it never
gets a secret, a service-account kind, or an audience other than the MCP server's. Its name is
shown to the person on the consent page, so it is bounded and stripped of control characters. The
one thing this server knows about where the client comes from is the client id itself, the address
it fetched the document from; a `client_uri` on any other origin is the document's unchecked claim
(a document at evil.example can name https://claude.ai) and is not kept.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import oidc
from .errors import ServiceError
from .mcp_policy import McpPolicy
from .store import Store, now
from .webhook_delivery import RequestDeadline, UrlRejected, pinned_opener, resolve_checked

log = logging.getLogger("synwevo.oidc.cimd")

MAX_DOCUMENT_BYTES = 64 * 1024
FETCH_TIMEOUT_S = 10.0
#: How long an accepted document stands before it is fetched again on the next authorization.
REFRESH_AFTER_S = 24 * 3600
MAX_REDIRECT_URIS = 10
MAX_NAME_LEN = 120
USER_AGENT = "Synwevo-Account/1 (+client-id-metadata-document)"


def is_client_id_url(client_id: str) -> bool:
    """Whether a `client_id` is a Client ID Metadata Document URL: https, a host, a path, and nothing
    that could not be a document address (no query, no fragment, no credentials)."""
    try:
        u = urlsplit(client_id)
    except ValueError:
        return False
    return (u.scheme == "https" and bool(u.hostname) and u.path not in ("", "/") and not u.query
            and not u.fragment and u.username is None and u.password is None)


def _clean_name(value) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch.isprintable() and ch not in "\r\n\t").strip()
    return text[:MAX_NAME_LEN]


def _origin(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) of an https address without credentials, or None."""
    try:
        u = urlsplit(url)
        port = u.port or 443
    except ValueError:
        return None
    if u.scheme != "https" or not u.hostname or u.username is not None or u.password is not None:
        return None
    return u.scheme, u.hostname.lower().rstrip("."), port


def _own_client_uri(client_id: str, value) -> str | None:
    """The document's `client_uri` when it is on the client id's own origin — the one origin this
    server fetched from, so the one it knows the client controls. Anything else is dropped."""
    if not isinstance(value, str) or _origin(value) is None:
        return None
    return value if _origin(value) == _origin(client_id) else None


def withdrawn_by_policy(store: Store, client: dict) -> bool:
    """Whether a self-registered client (``registration='cimd'``) is one the MCP policy no longer
    accepts: the mechanism turned off, or its host no longer among the allowed domains. Its
    registration and tokens stay as they are — re-allowing restores them — but nothing it holds is
    honoured while the policy refuses it. Operator registrations are never withdrawn by this."""
    if client.get("registration") != "cimd":
        return False
    client_id = client.get("client_id") or ""
    if not is_client_id_url(client_id):
        return True
    return not McpPolicy(store).cimd_allowed(urlsplit(client_id).hostname or "")


def fetch_document(client_id: str, *, owner: str = "cimd") -> dict:
    """GET the document at `client_id`, pinned to a checked public address, without redirects."""
    try:
        validated = resolve_checked(client_id, owner=owner, allow_http=False, timeout=FETCH_TIMEOUT_S,
                                    http_refusal="客户端元数据文档地址必须是 https")
    except UrlRejected as e:
        raise ServiceError(400, "invalid_client", f"客户端元数据文档地址不可用：{e}") from None
    deadline = RequestDeadline(FETCH_TIMEOUT_S)
    opener = pinned_opener(validated, deadline)
    req = urllib.request.Request(client_id, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype not in ("application/json", ""):
                raise ServiceError(400, "invalid_client", "客户端元数据文档不是 JSON")
            raw = resp.read(MAX_DOCUMENT_BYTES + 1)
    except ServiceError:
        raise
    except urllib.error.HTTPError as e:
        raise ServiceError(400, "invalid_client", f"客户端元数据文档无法读取（HTTP {e.code}）") from None
    except Exception as e:  # noqa: BLE001 — a redirect, a timeout, a TLS failure: all "could not be fetched"
        log.info("client metadata document fetch failed for %s: %s", client_id, type(e).__name__)
        raise ServiceError(400, "invalid_client", "客户端元数据文档无法读取") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ServiceError(400, "invalid_client", "客户端元数据文档过大")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ServiceError(400, "invalid_client", "客户端元数据文档不是有效 JSON") from None
    if not isinstance(doc, dict):
        raise ServiceError(400, "invalid_client", "客户端元数据文档不是 JSON 对象")
    return doc


def validate_document(client_id: str, doc: dict) -> dict:
    """The parts of a document this server acts on, checked. Everything else is kept for display."""
    if doc.get("client_id") != client_id:
        raise ServiceError(400, "invalid_client", "客户端元数据文档的 client_id 与其地址不一致")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS \
            or not all(isinstance(u, str) and u for u in uris):
        raise ServiceError(400, "invalid_client", "客户端元数据文档必须列出 1–10 个回调地址")
    for uri in uris:
        try:
            oidc._check_redirect(uri)
        except ServiceError as e:
            raise ServiceError(400, "invalid_client", f"客户端元数据文档的回调地址不可用：{e.message}") from None
    method = doc.get("token_endpoint_auth_method", "none")
    if method != "none":
        # A document can promise a secret or a key, but this server issues neither to a client it
        # has never met: it registers public clients, authenticated by PKCE.
        raise ServiceError(400, "invalid_client", "客户端元数据文档登记的客户端只能是公共客户端（token_endpoint_auth_method=none）")
    grants = doc.get("grant_types", ["authorization_code"])
    if not isinstance(grants, list) or "authorization_code" not in grants:
        raise ServiceError(400, "invalid_client", "客户端元数据文档必须支持 authorization_code")
    name = _clean_name(doc.get("client_name")) or urlsplit(client_id).hostname or client_id
    return {"name": name, "redirect_uris": list(dict.fromkeys(uris)),
            "client_uri": _own_client_uri(client_id, doc.get("client_uri"))}


class ClientMetadataDocuments:
    """Registration by document: policy, fetch, validate, store."""

    def __init__(self, store: Store):
        self.store = store
        self.clients = oidc.Clients(store)
        self.policy = McpPolicy(store)

    def resolve(self, client_id: str, *, fetch=None, at: float | None = None,
                may_fetch: bool = True) -> tuple[dict | None, bool]:
        """(client row, fetched) for a URL `client_id`. Every authorization of a self-registered
        client comes here, stored or not: refused when the policy does not allow this mechanism or
        this host (now — a withdrawn policy ends a registration's use, not only new ones); the stored
        registration while it is fresh; else the document fetched again and the registration replaced
        by what it says now, so a callback the document dropped stops being accepted.

        `may_fetch=False` — the request has no signed-in person behind it — answers (None, False)
        wherever a fetch would be needed: the caller sends the person to sign in first, and nothing is
        fetched or stored for an anonymous request."""
        if not is_client_id_url(client_id):
            raise ServiceError(400, "invalid_client", "未知的客户端")
        host = urlsplit(client_id).hostname or ""
        if not self.policy.cimd_allowed(host):
            raise ServiceError(400, "invalid_client",
                               "本平台不接受该客户端的自描述登记（client_id 为元数据文档地址）；请联系管理员登记客户端")
        existing = self.clients.get_any(client_id)
        t = at if at is not None else now()
        if existing and existing.get("disabled_at"):
            raise ServiceError(400, "invalid_client", "该客户端已被平台停用")
        if existing and existing.get("registration") == "cimd" and (existing.get("metadata_fetched_at") or 0) > t - REFRESH_AFTER_S:
            return existing, False
        if existing and existing.get("registration") != "cimd":
            # An operator registered this exact id by hand: the operator's registration stands.
            return existing, False
        if not may_fetch:
            return None, False
        doc = (fetch or fetch_document)(client_id)
        clean = validate_document(client_id, doc)
        from .apps import PLATFORM_APP_ID
        scopes = " ".join(("openid", "profile", "email", "offline_access", *oidc.MCP_SCOPES))
        with self.store.tx():
            # One write: a refresh never leaves the new callbacks with the old fetch time, or the reverse.
            self.clients.register(client_id=client_id, app_id=PLATFORM_APP_ID, name=clean["name"],
                                  redirect_uris=clean["redirect_uris"], scopes=scopes,
                                  audiences=[oidc.mcp_resource()], confidential=False, kind="user")
            self.store.execute("UPDATE oauth_clients SET registration='cimd', client_uri=?, metadata_json=?, "
                               "metadata_fetched_at=?, updated_at=? WHERE client_id=?",
                               (clean["client_uri"], json.dumps(doc, ensure_ascii=False)[:MAX_DOCUMENT_BYTES], t, t, client_id))
        return self.clients.get(client_id), True
