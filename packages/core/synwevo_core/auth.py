"""Authentication for the machine API and access control for the web UI.

* Machine API (`/api/v1`): `Authorization: Bearer dw_<id>_<secret>`.  Keys are created with the
  CLI (`python -m synwevo_docs.cli apikey create …`), stored only as a SHA-256 hash, carry a
  tenant and scopes, and can expire, be rotated (the lineage continues) or be revoked (the
  lineage ends).  Every resource belongs to a tenant and is only visible to keys of that tenant.
* Web UI (`/api/…`, static files): `SYNWEVO_UI_AUTH` decides:
  - `none`  – no check; only allowed when the server listens on loopback (default)
  - `basic` – HTTP Basic against users created with `python -m synwevo_identity.cli ui-user add`
  - `proxy` – the operator's reverse proxy authenticates (explicitly acknowledged)
  The server refuses to start when it listens on a non-loopback address with `none`
  (in a container it warns instead: there, exposure is decided by the port mapping).

Secrets never appear in responses, logs or the built frontend.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import sys
import secrets
import time

from . import env
from .errors import ServiceError
from .store import Store, new_id, now

SCOPES = ("read", "write", "admin")
UI_TENANT = env.get("UI_TENANT", "default")
KEY_PREFIX = "dw"
PBKDF2_ROUNDS = 240_000
# The longest an old key may keep working after a rotation. A grace period is a second valid
# credential: it exists so a running automation can be redeployed with the new key without an
# outage, not so a key someone wanted gone can linger. Same bound as OIDC client secrets.
MAX_ROTATION_GRACE_S = 24 * 3600
# Metadata columns of api_keys. Listed explicitly so `secret_hash` can never ride along into a
# listing, a response or a log line by way of `SELECT *`.
KEY_COLUMNS = ("id, name, tenant, scopes, created_at, expires_at, last_used_at, revoked_at, "
               "lineage, replaced_by, revoke_reason")


class JournalWriteFailed(RuntimeError):
    """A revocation took effect in the database but could not be appended to the security
    journal, so restoring an older backup would bring the credential back."""

    def __init__(self, revoked: int, why: str):
        super().__init__(why)
        self.revoked = revoked
        self.why = why


def _check_scopes(scopes) -> None:
    bad = [s for s in scopes if s not in SCOPES]
    if bad or not scopes:
        raise ServiceError(400, "bad_scope", f"未知权限范围：{bad}；可用：{', '.join(SCOPES)}")


def _new_key_material() -> tuple[str, str]:
    return secrets.token_hex(6), secrets.token_urlsafe(32)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _positive_days(value, field: str = "expires_days") -> None:
    """A lifetime in days must be a finite number above zero.

    `nan` and `inf` both parse as floats (argparse, JSON) and slip past a plain `<= 0`: NaN
    compares false to everything, and SQLite stores it as NULL — "never expires". A key minted
    or rotated with `--expires-days nan` was permanent."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        # 0 used to mean "never expires" by accident of truthiness, and a negative value
        # minted a key that was dead on arrival.
        raise ServiceError(400, "bad_expiry", "有效期必须是大于 0 的天数；不设有效期请省略该参数", {"field": field})


def key_status(row: dict, at: float | None = None) -> str:
    """active | grace (rotated, old key still accepted) | rotated | revoked | expired."""
    t = now() if at is None else at
    rotated = row.get("revoke_reason") == "rotated"
    if row.get("revoked_at"):
        return "rotated" if rotated else "revoked"
    if row.get("expires_at") and row["expires_at"] < t:
        return "rotated" if rotated else "expired"
    return "grace" if rotated else "active"


def _public(row: dict) -> dict:
    """Key metadata safe to print or return: never the hash."""
    out = {k: row.get(k) for k in ("id", "name", "tenant", "scopes", "created_at", "expires_at",
                                    "last_used_at", "revoked_at", "replaced_by", "revoke_reason")}
    out["lineage"] = row.get("lineage") or row.get("id")
    out["status"] = key_status(row)
    return out


def audit_event(store: Store, action: str, *, actor_kind: str | None = None, actor_id: str | None = None,
                actor: dict | None = None, workspace_id: str | None = None, object_kind: str | None = None,
                object_id: str | None = None, result: str = "ok", meta: dict | None = None,
                request=None, request_id: str | None = None) -> None:
    """Append a security event: the one writer every application and the platform use (admin
    coverage G26, decision D7). Same row shape everywhere and the same rule: identifiers and
    metadata only — never a key, a secret, a token, a hash or a person's content.

    Who acted, in order of precedence: an explicit ``actor_kind``/``actor_id`` (the account
    service names its administrators this way); else ``actor``, an authority descriptor
    (`authority.describe` / `describe_request`), mapped by `authority.audit_actor` — a person,
    a person behind a delegated client (named in ``meta.delegation``), a machine credential
    (``service:…`` / ``api:…``) or the system; else the request's principal the gate recorded
    (`context.principal()`), so a service needs no request in hand to say who wrote; else the
    system. An ``actor_id`` given without a kind is a person.

    The request id comes from the request, else from ``request_id``, else from the gate's
    context (`context.request_id()`), so a row written deep in a service still names the call.
    The address comes from `http.client_ip`, the same as before: behind the recommended reverse
    proxy (`SYNWEVO_TRUST_PROXY`) the socket peer is the proxy, and a row that records the proxy
    for every caller says nothing about who called.

    ``meta`` is stored as canonical JSON (sorted keys, no padding, non-JSON values as text),
    the one shape every reader parses and every test can match.

    A failure to write is raised, never swallowed: inside a `Store.tx()` it undoes the write it
    belongs to (the caller's transaction), which is the rule for every state change."""
    from . import context
    from .authority import audit_actor
    meta = dict(meta or {})
    if actor_kind is None and actor_id is None:
        desc = actor if actor is not None else context.principal()
        if desc is not None:
            actor_kind, actor_id, delegation = audit_actor(desc)
            if delegation and "delegation" not in meta:
                meta["delegation"] = delegation
        elif context.actor() is not None:
            # A live editing room or a hook acting for a signed-in person (`collab.acting`) has no
            # request principal; the person it acts for is the actor, never the system.
            actor_kind, actor_id = "user", context.actor()
    if actor_kind is None:
        actor_kind = "user" if actor_id is not None else "system"
    state = getattr(request, "state", None)
    ip = None
    if request is not None:
        from .http import client_ip           # imported here: http imports this module
        ip = client_ip(request) or None
    rid = getattr(state, "request_id", None) or request_id or context.request_id()
    store.execute(
        "INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,"
        "object_id,result,request_id,ip,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("ae"), time.time(), actor_kind, actor_id, workspace_id, action, object_kind, object_id,
         result, rid, ip, json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)))

UI_AUTH_MODES = ("none", "basic", "proxy", "session")


def ui_auth_mode() -> str:
    """How the browser surface is protected.

    `session` is the real account system: per-user identity, workspaces, revocable sessions.
    The older modes stay because deployments are already running on them — `basic` is what
    dw.wegreen.ltd uses today, and pulling it out from under a live instance to ship accounts
    would be the kind of migration that loses people their access. They are a shared password,
    not identity, and `docs/security/permissions.md` says so.
    """
    mode = (env.get("UI_AUTH") or "none").strip().lower()
    if mode not in UI_AUTH_MODES:
        raise ServiceError(500, "bad_config",
                           f"SYNWEVO_UI_AUTH 只能是 {' / '.join(UI_AUTH_MODES)}，当前为 {mode!r}")
    return mode


def is_loopback(host: str) -> bool:
    host = (host or "").strip()
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def in_container() -> bool:
    """Inside a container, binding 0.0.0.0 is how the port mapping reaches the process;
    what is actually exposed is decided by the mapping, which this process cannot see.

    `SYNWEVO_IN_CONTAINER=0` is an explicit "no": it overrides the marker files, so the strict
    refusal can be forced (and tested) inside a container too. Forcing it off only ever makes
    the guard stricter, never looser."""
    flag = (env.get("IN_CONTAINER") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


REMEDIES = (
    "请任选其一：\n"
    "  1) 仅本机监听：SYNWEVO_HOST=127.0.0.1（默认），由反向代理对外提供；\n"
    "  2) 启用内置口令：python -m synwevo_identity.cli ui-user add <用户名>，并设置 SYNWEVO_UI_AUTH=basic；\n"
    "  3) 由反向代理完成认证并显式声明：SYNWEVO_UI_AUTH=proxy。\n"
    "机器接口 /api/v1 始终需要 API 密钥，不受此设置影响。"
)


def check_listen_config(host: str, warn=None) -> None:
    """Refuse to start open to the network without deciding how the UI is protected.

    Must be called by whatever starts the server (``synwevo_server/run.py`` does).  In a container
    the bind address says nothing about exposure, so this warns loudly instead of refusing —
    the compose file publishes to 127.0.0.1 only.
    """
    if is_loopback(host) or ui_auth_mode() != "none":
        return
    if in_container():
        (warn or (lambda m: print(m, file=sys.stderr)))(
            f"警告：容器内监听 {host}，且 SYNWEVO_UI_AUTH=none（界面无访问控制）。\n"
            "容器的实际暴露范围由端口映射决定：请只映射到 127.0.0.1，并在前面加带认证的反向代理。\n" + REMEDIES)
        return
    raise SystemExit(
        f"拒绝启动：SYNWEVO_HOST={host} 会对外监听，但 SYNWEVO_UI_AUTH=none（界面无任何访问控制）。\n" + REMEDIES)


class RootSecretMismatch(RuntimeError):
    """The configured root secret is not the one sealed data was written with."""


_ROOT_VERIFIED: set = set()
_ROOT_MISMATCH = (
    "服务端根密钥与已加密数据不一致：当前生效的根密钥（SYNWEVO_SECRET，或库内生成的密钥）"
    "不是写入两步验证种子 / Webhook 签名密钥时使用的那一把。继续使用会让这些数据全部无法解密，"
    "因此拒绝启动。二选一：恢复原来的 SYNWEVO_SECRET 设置；或确认要更换根密钥后运行 "
    "`python -m synwevo_identity.cli secret rekey`，把已加密数据迁移到新根密钥。")


# ------------------------------------------------------------------ API keys
class Auth:
    def __init__(self, store: Store):
        # `ui_users` is created by the store's migrations like every other table. Creating it
        # here as well wrote to the database on every construction, and failed outright on a
        # store opened read-only for inspection (`Store(migrate=False)`).
        self.store = store
        self._last_seen: dict[str, float] = {}

    # ---------------------------------------------------------- server secret (signed links)
    def secret(self) -> bytes:
        """The root secret: signed links, and the key every sealed value is derived from.

        `SYNWEVO_SECRET` wins when set; otherwise one is generated into the database. Switching
        between the two — or changing `SYNWEVO_SECRET` — changes every derived key, so a root
        that does not match the one sealed data was written with is **refused** rather than
        used: using it would make every stored TOTP seed and webhook secret undecryptable with
        no error pointing at the cause. `python -m synwevo_identity.cli secret rekey` moves the
        sealed data to the new root.
        """
        external = env.get("SECRET")
        if external:
            root = external.encode()
        else:
            row = self.store.one("SELECT value FROM meta WHERE key='server_secret'")
            if row:
                root = bytes.fromhex(row["value"])
            else:
                s = secrets.token_bytes(32)
                self.store.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('server_secret', ?)", (s.hex(),))
                root = bytes.fromhex(self.store.one("SELECT value FROM meta WHERE key='server_secret'")["value"])
        self._guard_root(root)
        return root

    def _guard_root(self, root: bytes) -> None:
        from . import sealed
        want = sealed.kcv(root)
        cache_key = (str(self.store.db_path), want)
        if cache_key in _ROOT_VERIFIED:
            return
        row = self.store.one("SELECT value FROM meta WHERE key='root_kcv'")
        if row:
            if row["value"] != want:
                raise RootSecretMismatch(_ROOT_MISMATCH)
        else:
            # First time this store records which root it uses. If data was sealed under the
            # database-generated root and an external one has since been configured, that is the
            # silent-breakage case this guard exists for: refuse, do not adopt the new root.
            stored = self.store.one("SELECT value FROM meta WHERE key='server_secret'")
            if stored and bytes.fromhex(stored["value"]) != root:
                if sealed.opens_with(self.store, root) is False and \
                        sealed.opens_with(self.store, bytes.fromhex(stored["value"])):
                    raise RootSecretMismatch(_ROOT_MISMATCH)
            self.store.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('root_kcv', ?)", (want,))
            row = self.store.one("SELECT value FROM meta WHERE key='root_kcv'")
            if row["value"] != want:
                raise RootSecretMismatch(_ROOT_MISMATCH)
        _ROOT_VERIFIED.add(cache_key)

    # ---------------------------------------------------------- keys
    #
    # A key belongs to a **lineage**: the id of the first key in a chain of rotations. Rotation
    # (routine hygiene) continues the lineage, so work queued under the old key is still the same
    # automation's work; revocation (a security action) ends the whole lineage at once, including
    # a predecessor still inside its rotation grace period. `synwevo_core.authority` checks work
    # against the lineage, not against one key.
    def create_key(self, name: str, tenant: str, scopes: tuple[str, ...] = ("read", "write"),
                   expires_in_days: float | None = None) -> tuple[str, dict]:
        """Mint a key. Returns (the full token — shown once, never stored —, public metadata).

        `tenant` has no default on purpose: the old default was the legacy workspace, which is
        exactly the one nobody is supposed to reach by accident. Callers that take a tenant from
        an operator validate it first with `key_tenant_problem()`.
        """
        _check_scopes(scopes)
        if not tenant:
            raise ServiceError(400, "no_tenant", "API 密钥必须属于一个工作空间（tenant）")
        _positive_days(expires_in_days)
        t = now()
        kid, secret = _new_key_material()
        row = {"id": kid, "name": (name or "")[:100] or "api key", "tenant": tenant, "scopes": ",".join(scopes),
               "secret_hash": _digest(secret), "created_at": t,
               "expires_at": (t + expires_in_days * 86400) if expires_in_days else None,
               "lineage": kid}
        self.store.insert("api_keys", row)
        return f"{KEY_PREFIX}_{kid}_{secret}", _public(row)

    def get_key(self, kid: str) -> dict | None:
        """Metadata of one key. Never the hash."""
        row = self.store.one(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id=?", (kid,))
        return _public(row) if row else None

    def list_keys(self) -> list[dict]:
        return [_public(r) for r in self.store.all(f"SELECT {KEY_COLUMNS} FROM api_keys ORDER BY created_at")]

    def lineage_keys(self, kid: str) -> list[dict]:
        """Every key of the lineage `kid` belongs to, oldest first."""
        root = self.lineage_of(kid)
        if not root:
            return []
        return [_public(r) for r in self.store.all(
            f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id=? OR lineage=? ORDER BY created_at", (root, root))]

    def lineage_of(self, kid: str) -> str | None:
        row = self.store.one("SELECT id, lineage FROM api_keys WHERE id=?", (kid,))
        return (row["lineage"] or row["id"]) if row else None

    def key_id_from_token(self, token: str) -> str | None:
        """The key id of a full `dw_<id>_<secret>` token, **only if the secret matches**.

        Lets an operator act on a key they hold (for instance a leaked one) without typing its
        secret on a command line. Constant-time comparison, same as `verify`.
        """
        pieces = (token or "").strip().split("_", 2)
        if len(pieces) != 3 or pieces[0] != KEY_PREFIX:
            return None
        row = self.store.one("SELECT id, secret_hash FROM api_keys WHERE id=?", (pieces[1],))
        if not row or not hmac.compare_digest(row["secret_hash"], _digest(pieces[2])):
            return None
        return row["id"]

    def key_tenant_problem(self, tenant: str) -> str | None:
        """Why a *new* key must not be minted for `tenant`, or None when it may.

        * an active workspace that has Synwevo Docs enabled — yes;
        * the unclaimed legacy workspace (`pending_claim`) — yes: operator-issued keys predate
          the platform, and `--tenant default` is how docs/integration/api.md §1 has always told
          operators to automate against that data. Nobody reaches it through membership until it
          is claimed; an operator with shell access to the data directory is not "somebody
          through membership", and refusing them a key would not stop them reading the files;
        * the legacy operator tenant (`SYNWEVO_UI_TENANT`, default `default`) with no workspace
          row — yes: that is the tenant the pre-identity browser modes use;
        * frozen or deleted workspaces, any other state that is not `active` (an allow-list: a
          state added later is refused until someone decides otherwise), workspaces without
          Synwevo Docs, and names that are not a workspace at all — no. A typo used to mint a
          working key into a tenant nobody can see, and data written there was effectively lost.
        """
        from .apps import DOCS_APP_ID
        tenant = (tenant or "").strip()
        if not tenant:
            return "必须指定工作空间（--tenant <工作空间 ID>）"
        ws = self.store.one("SELECT id, status, deleted_at FROM workspaces WHERE id=?", (tenant,))
        if not ws:
            if tenant == UI_TENANT:
                return None
            return (f"找不到工作空间 {tenant!r}。API 密钥必须属于一个已存在的工作空间；"
                    "可用的工作空间 ID 见下方列表，或在平台管理后台的「工作空间」里查到")
        if ws["deleted_at"] or ws["status"] == "deleted":
            return f"工作空间 {tenant} 已删除"
        if ws["status"] == "frozen":
            return f"工作空间 {tenant} 已冻结，不能再为它签发密钥"
        if ws["status"] == "pending_claim":
            return None
        if ws["status"] != "active":
            return f"工作空间 {tenant} 当前不可用（{ws['status']}），不能为它签发密钥"
        app = self.store.one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))
        if app and app["status"] != "active":
            return "Synwevo Docs 已在平台范围停用"
        if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (tenant, DOCS_APP_ID)):
            return (f"工作空间 {tenant} 尚未启用 Synwevo Docs；请先由平台管理员在管理后台的「应用目录」"
                    "（按工作空间启用），或在账户服务命令行"
                    "（python -m synwevo_identity.cli app enable docweave --workspace <工作空间 ID>）为它启用")
        return None

    def key_tenant_candidates(self, limit: int = 50) -> list[dict]:
        """Workspaces a new key may be minted for, as {id, name, status}: exactly the ones
        `key_tenant_problem` accepts, so an operator who left out `--tenant` (or mistyped it) is
        shown ids that will work rather than told to go and look them up somewhere."""
        rows = self.store.all("SELECT id, name, status FROM workspaces WHERE deleted_at IS NULL "
                              "AND status IN ('active','pending_claim') ORDER BY created_at, id")
        out = [r for r in rows if self.key_tenant_problem(r["id"]) is None][:limit]
        if not any(r["id"] == UI_TENANT for r in rows) and self.key_tenant_problem(UI_TENANT) is None:
            out.append({"id": UI_TENANT, "name": "（界面租户，无工作空间记录）", "status": "legacy"})
        return out

    def rotate_key(self, kid: str, *, grace_s: float = 0, expires_in_days: float | None = None,
                   keep_absolute_expiry: bool = False) -> tuple[str, dict, dict]:
        """Replace a live key with a new one of the same lineage, name, tenant and scopes.

        Returns (new full token — shown once —, new key metadata, old key metadata).

        * `grace_s` (default 0 = the old key stops now) keeps the old key valid for a bounded
          time so a running automation can be redeployed without an outage. Bounded by
          `MAX_ROTATION_GRACE_S` and by the old key's own expiry — a grace never extends a key.
        * The lifetime carries over: a key that was issued for 90 days is replaced by one valid
          for 90 days from now, unless `expires_in_days` says otherwise. The old CLI silently
          turned an expiring key into a permanent one. That is the operator's rotation.
        * `keep_absolute_expiry` is the key holder's own rotation (the machine API): the new key
          expires exactly when the old one would have. Otherwise a key an operator issued for 30
          days could keep itself alive forever by rotating every 29.
        * One successor per key. The read and the conditional update run in one write
          transaction, so two concurrent rotations of the same key cannot both succeed and fork
          the lineage.
        * At most two valid keys per lineage: the current key and, during its grace, the one it
          replaced. Rotating a key whose own predecessor is still in grace ends that grace now.
          Otherwise a chain of graced rotations kept every key of the chain valid at once — and,
          since rate buckets are per key, multiplied the credential's rate limit.
        * The rotated-out key is written to the security journal (`api_key_superseded`) inside
          the same transaction: if the journal cannot be written nothing changes and the caller
          is told so, rather than handed a rotation that a restored backup would silently undo.
        """
        if grace_s is None:
            grace_s = 0
        # Written so that NaN fails it: NaN compares false to everything, so `grace_s < 0 or
        # grace_s > MAX` let it through, and `t + NaN` stored as NULL — a grace without end.
        if isinstance(grace_s, bool) or not isinstance(grace_s, (int, float)) \
                or not (0 <= grace_s <= MAX_ROTATION_GRACE_S):
            raise ServiceError(400, "bad_grace",
                               f"宽限期必须在 0 到 {MAX_ROTATION_GRACE_S // 60} 分钟之间",
                               {"field": "grace_minutes"})
        _positive_days(expires_in_days)
        t = now()
        with self.store.tx() as c:
            old = c.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id=?", (kid,)).fetchone()
            if not old:
                raise ServiceError(404, "not_found", "找不到该 API 密钥")
            old = dict(old)
            if old["replaced_by"]:
                raise ServiceError(409, "already_rotated",
                                   f"该密钥已经轮换为 {old['replaced_by']}；请改用新密钥，或对新密钥再次轮换",
                                   {"replaced_by": old["replaced_by"]})
            if old["revoked_at"]:
                raise ServiceError(409, "key_revoked", "已撤销的密钥不能轮换：撤销结束了整条密钥链，请新建一个密钥")
            if old["expires_at"] and old["expires_at"] <= t:
                raise ServiceError(409, "key_expired", "已过期的密钥不能轮换，请新建一个密钥")
            lineage = old["lineage"] or old["id"]
            if keep_absolute_expiry:
                expires_at = old["expires_at"]
            elif expires_in_days is not None:
                expires_at = t + expires_in_days * 86400
            elif old["expires_at"]:
                expires_at = t + max(0.0, old["expires_at"] - old["created_at"])
            else:
                expires_at = None
            new_kid, secret = _new_key_material()
            row = {"id": new_kid, "name": old["name"], "tenant": old["tenant"], "scopes": old["scopes"],
                   "secret_hash": _digest(secret), "created_at": t, "expires_at": expires_at,
                   "lineage": lineage}
            c.execute("INSERT INTO api_keys (id,name,tenant,scopes,secret_hash,created_at,expires_at,lineage) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (row["id"], row["name"], row["tenant"], row["scopes"], row["secret_hash"],
                       row["created_at"], row["expires_at"], row["lineage"]))
            if grace_s:
                until = t + grace_s
                if old["expires_at"]:
                    until = min(until, old["expires_at"])
                n = c.execute("UPDATE api_keys SET replaced_by=?, revoke_reason='rotated', lineage=?, expires_at=? "
                              "WHERE id=? AND revoked_at IS NULL AND replaced_by IS NULL",
                              (new_kid, lineage, until, kid)).rowcount
            else:
                n = c.execute("UPDATE api_keys SET replaced_by=?, revoke_reason='rotated', lineage=?, revoked_at=? "
                              "WHERE id=? AND revoked_at IS NULL AND replaced_by IS NULL",
                              (new_kid, lineage, t, kid)).rowcount
            if n != 1:
                raise ServiceError(409, "already_rotated", "该密钥刚刚被另一个请求轮换过")
            # End the grace of any earlier key of this lineage (see the docstring).
            ended = [r["id"] for r in c.execute(
                "SELECT id FROM api_keys WHERE (id=? OR lineage=?) AND id<>? AND id<>? "
                "AND revoke_reason='rotated' AND revoked_at IS NULL", (lineage, lineage, kid, new_kid)).fetchall()]
            if ended:
                c.execute(f"UPDATE api_keys SET revoked_at=? WHERE id IN ({','.join('?' * len(ended))}) "
                          "AND revoked_at IS NULL", (t, *ended))
            old_after = dict(c.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id=?", (kid,)).fetchone())
            from . import journal
            try:
                journal.record(self.store, "api_key_superseded", key_id=kid)
            except OSError as e:
                # No detail: the OS error names a server path, and this message reaches API
                # callers. The CLI prints the cause (`__cause__`) for the operator.
                raise ServiceError(500, "journal_failed",
                                   "轮换没有进行：写入安全日志失败（从备份恢复时无法重放这次轮换）。"
                                   "旧密钥保持不变；请检查数据目录的写权限后重试。") from e
        for k in (kid, *ended):
            self._last_seen.pop(k, None)
        return f"{KEY_PREFIX}_{new_kid}_{secret}", _public(row), _public(old_after)

    def revoke_key(self, kid: str, reason: str = "revoked") -> int:
        """End the lineage `kid` belongs to. Returns how many keys this call switched off.

        Revocation is the security action, so it is not scoped to the one key named: a leaked
        key's successor and a predecessor still inside its rotation grace are the same
        credential chain, and leaving either alive would make "revoked" mean "mostly revoked".
        Queued work of the lineage is halted by the job runner (`authority.check`).

        The withdrawal is also appended to the security journal, so restoring an older backup
        cannot bring the key back. Journalling happens after the database change — a revocation
        must take effect even if the journal cannot be written — and a journal failure is raised
        (`JournalWriteFailed`) so the caller can say the revocation is in force but not durable.
        """
        root = self.lineage_of(kid)
        if not root:
            return 0
        t = now()
        with self.store.tx() as c:
            n = c.execute("UPDATE api_keys SET revoked_at=?, revoke_reason=? "
                          "WHERE (id=? OR lineage=?) AND revoked_at IS NULL", (t, reason, root, root)).rowcount
        if n:
            for k in self.store.all("SELECT id FROM api_keys WHERE id=? OR lineage=?", (root, root)):
                self._last_seen.pop(k["id"], None)
            from . import journal
            try:
                # The lineage root, not the key named: a backup taken before a rotation holds the
                # root but not its successor, and replaying "revoke <successor>" there would
                # match nothing and leave the root alive.
                journal.record(self.store, "api_key_revoked", key_id=root)
            except OSError as e:
                raise JournalWriteFailed(n, str(e)) from e
        return n

    def verify(self, header: str | None) -> dict:
        """Authorization header → principal {key_id, lineage, tenant, scopes, name, expires_at};
        raises 401 otherwise."""
        token = ""
        if header:
            parts = header.split(None, 1)
            token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else header.strip()
        pieces = token.split("_", 2)
        if len(pieces) != 3 or pieces[0] != KEY_PREFIX:
            raise ServiceError(401, "unauthorized", "缺少或格式不正确的 API 密钥（Authorization: Bearer dw_<id>_<secret>）")
        kid, secret = pieces[1], pieces[2]
        row = self.store.one("SELECT * FROM api_keys WHERE id=?", (kid,))
        digest = _digest(secret)
        if not row or not hmac.compare_digest(row["secret_hash"], digest):
            raise ServiceError(401, "unauthorized", "API 密钥无效")
        rotated = row.get("revoke_reason") == "rotated"
        if row["revoked_at"]:
            if rotated:
                raise ServiceError(401, "key_rotated", "该 API 密钥已被轮换，请改用新密钥",
                                   {"replaced_by": row.get("replaced_by")})
            raise ServiceError(401, "key_revoked", "该 API 密钥已被撤销")
        if row["expires_at"] and row["expires_at"] < now():
            if rotated:
                raise ServiceError(401, "key_rotated", "该 API 密钥已被轮换，宽限期已过，请改用新密钥",
                                   {"replaced_by": row.get("replaced_by")})
            raise ServiceError(401, "key_expired", "该 API 密钥已过期")
        last = self._last_seen.get(kid, 0)
        if now() - last > 60:                      # throttled write: one row update per key per minute
            self._last_seen[kid] = now()
            self.store.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now(), kid))
        return {"key_id": kid, "lineage": row.get("lineage") or kid, "tenant": row["tenant"],
                "scopes": tuple(row["scopes"].split(",")), "name": row["name"],
                "expires_at": row["expires_at"]}

    @staticmethod
    def require(principal: dict, scope: str) -> None:
        if scope not in principal["scopes"] and "admin" not in principal["scopes"]:
            raise ServiceError(403, "forbidden", f"该密钥没有 {scope} 权限（当前：{'/'.join(principal['scopes'])}）")

    # ---------------------------------------------------------- UI users (HTTP Basic)
    def add_ui_user(self, username: str, password: str) -> None:
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
        self.store.execute("INSERT OR REPLACE INTO ui_users (username, salt, hash, rounds, created_at) VALUES (?,?,?,?,?)",
                           (username, salt, h, PBKDF2_ROUNDS, now()))

    def remove_ui_user(self, username: str) -> bool:
        return bool(self.store.execute("DELETE FROM ui_users WHERE username=?", (username,)))

    def list_ui_users(self) -> list[dict]:
        return self.store.all("SELECT username, created_at FROM ui_users ORDER BY username")

    def check_ui_basic(self, header: str | None) -> bool:
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(header.split(None, 1)[1]).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return False
        user, _, password = raw.partition(":")
        row = self.store.one("SELECT salt, hash, rounds FROM ui_users WHERE username=?", (user,))
        if not row:
            hashlib.pbkdf2_hmac("sha256", password.encode(), b"x" * 16, PBKDF2_ROUNDS)   # constant-ish work
            return False
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), int(row["rounds"])).hex()
        return hmac.compare_digest(h, row["hash"])

    # ---------------------------------------------------------- signed download links
    #
    # A link is a bearer credential meant to be handed to someone else, so its payload is
    # **sealed** (AES-256-GCM, key derived from the root secret for this purpose only), not just
    # signed: it names the minting credential — a key id and lineage, or a person's user id, a
    # client id and a grant family — and none of that is the recipient's business. The GCM tag
    # is the integrity check. Links minted before sealing (`<base64 JSON>.<HMAC>`) are still
    # accepted until they expire; nothing mints them any more.
    def sign_link(self, kind: str, ident: str, tenant: str, ttl_s: int = 900,
                  principal: dict | None = None) -> tuple[str, float]:
        """A sealed, expiring download URL token.

        `principal` — the `MachineAuth` principal that asked for the link, or an authority
        descriptor — is embedded (compacted) under ``p``. The download re-asks
        `authority.check` about *that* principal: once the key's lineage is revoked, the person
        leaves the workspace or the workspace is frozen, the links they minted stop working
        instead of living out their TTL. Links minted without a principal carry no ``p`` and
        keep the old behaviour until they expire.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        exp = time.time() + ttl_s
        body = {"k": kind, "i": ident, "t": tenant, "e": int(exp)}
        p = _link_descriptor(principal, tenant)
        if p:
            body["p"] = p
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(_link_key(self.secret())).encrypt(nonce, raw, LINK_AAD)
        return LINK_SEALED_PREFIX + _b64u(nonce + sealed), exp

    def open_link(self, token: str) -> dict:
        token = (token or "").strip()
        # Every token this server has ever minted is ASCII. Anything else is not a link, and
        # letting it reach hmac.compare_digest (str arguments must be ASCII) raised TypeError —
        # a 500 for what is simply a bad link.
        if not token or not token.isascii():
            raise ServiceError(403, "bad_link", "下载链接无效")
        if token.startswith(LINK_SEALED_PREFIX):
            data = self._open_sealed(token[len(LINK_SEALED_PREFIX):])
        else:
            data = self._open_legacy(token)
        if not isinstance(data, dict) or not isinstance(data.get("e"), (int, float)):
            raise ServiceError(403, "bad_link", "下载链接无效")
        if data["e"] < time.time():
            raise ServiceError(410, "link_expired", "下载链接已过期；请用 API 重新获取一个链接")
        p = data.get("p")
        if p is not None:
            if not isinstance(p, dict) or not p.get("kind"):
                raise ServiceError(403, "bad_link", "下载链接无效")
            # The tenant is left out of `p` to keep the URL short; put it back so a caller that
            # passes `p` to authority.check without a tenant still gets the workspace checks.
            p.setdefault("tenant", data.get("t"))
        return data

    def _open_sealed(self, blob: str) -> dict:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
            if len(raw) < 12 + 16:
                raise ValueError("short")
            plain = AESGCM(_link_key(self.secret())).decrypt(raw[:12], raw[12:], LINK_AAD)
            return json.loads(plain)
        except (InvalidTag, ValueError) as e:              # binascii.Error is a ValueError
            raise ServiceError(403, "bad_link", "下载链接无效") from e

    def _open_legacy(self, token: str) -> dict:
        """`<base64 JSON>.<HMAC-SHA256[:32]>`, as minted before payloads were sealed."""
        payload, _, sig = token.partition(".")
        want = hmac.new(self.secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not sig or not hmac.compare_digest(sig.encode(), want.encode()):
            raise ServiceError(403, "bad_link", "下载链接无效")
        try:
            return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception as e:  # noqa: BLE001
            raise ServiceError(403, "bad_link", "下载链接无效") from e


# Sealed download links. The label separates this key from every other key derived from the
# root secret (sealed.py's purposes, the root check value): changing either never affects the
# other. Changing the root secret (`secret rekey`) invalidates outstanding links, which live
# minutes; that is the intended trade.
LINK_SEALED_PREFIX = "l2."
LINK_KEY_LABEL = b"synwevo/download-link/v1"
LINK_AAD = b"synwevo-download-link"


def _link_key(root: bytes) -> bytes:
    return hmac.new(root, LINK_KEY_LABEL, hashlib.sha256).digest()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _link_descriptor(principal: dict | None, tenant: str) -> dict | None:
    """The authority descriptor to embed in a link, with what the link already says left out.

    Accepts either a `MachineAuth` principal (recognised by its `scopes`) or a descriptor from
    `authority.describe`. Only identifiers go in, never a secret: the payload is sealed, but a
    link is still a bearer credential and its contents must be harmless if it is opened.
    """
    if not principal:
        return None
    from . import authority
    desc = authority.describe(principal, tenant=tenant) if "scopes" in principal else dict(principal)
    out = {k: v for k, v in desc.items() if v is not None}
    if out.get("tenant") == tenant:
        out.pop("tenant")              # restored by open_link; a different tenant is kept so check() refuses
    if out.get("kind") == "api_key" and out.get("lineage") == out.get("key_id"):
        out.pop("lineage")             # authority treats a missing lineage as the key's own id
    return out


def rate_limiter(limit_per_min: int):
    """Token bucket per key (per process – documented in docs/integration/api.md)."""
    state: dict[str, list[float]] = {}

    def check(key: str) -> tuple[bool, float]:
        cap = max(1, limit_per_min)
        t = time.time()
        b = state.setdefault(key, [cap, t])
        tokens, last = b
        tokens = min(cap, tokens + (t - last) * cap / 60.0)
        if tokens < 1:
            b[0], b[1] = tokens, t
            return False, (1 - tokens) * 60.0 / cap
        b[0], b[1] = tokens - 1, t
        return True, 0.0

    return check


def anon_id(*parts: str) -> str:
    return new_id("req") if not parts else "req_" + hashlib.sha1("".join(parts).encode()).hexdigest()[:12]
