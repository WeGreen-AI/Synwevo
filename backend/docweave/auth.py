"""Authentication for the machine API and access control for the web UI.

* Machine API (`/api/v1`): `Authorization: Bearer dw_<id>_<secret>`.  Keys are created with the
  CLI (`python backend/manage.py apikey create …`), stored only as a SHA-256 hash, carry a
  tenant and scopes, and can expire or be revoked.  Every resource belongs to a tenant and is
  only visible to keys of that tenant.
* Web UI (`/api/…`, static files): `DOCWEAVE_UI_AUTH` decides:
  - `none`  – no check; only allowed when the server listens on loopback (default)
  - `basic` – HTTP Basic against users created with `manage.py ui-user add`
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
import os
import sys
import secrets
import time

from .services import ServiceError
from .store import Store, new_id, now

SCOPES = ("read", "write", "admin")
UI_TENANT = os.environ.get("DOCWEAVE_UI_TENANT", "default")
KEY_PREFIX = "dw"
PBKDF2_ROUNDS = 240_000

UI_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_users (
  username TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL, rounds INTEGER NOT NULL, created_at REAL
);
"""


def ui_auth_mode() -> str:
    mode = (os.environ.get("DOCWEAVE_UI_AUTH") or "none").strip().lower()
    if mode not in ("none", "basic", "proxy"):
        raise ServiceError(500, "bad_config", f"DOCWEAVE_UI_AUTH 只能是 none / basic / proxy，当前为 {mode!r}")
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
    what is actually exposed is decided by the mapping, which this process cannot see."""
    if (os.environ.get("DOCWEAVE_IN_CONTAINER") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


REMEDIES = (
    "请任选其一：\n"
    "  1) 仅本机监听：DOCWEAVE_HOST=127.0.0.1（默认），由反向代理对外提供；\n"
    "  2) 启用内置口令：python backend/manage.py ui-user add <用户名>，并设置 DOCWEAVE_UI_AUTH=basic；\n"
    "  3) 由反向代理完成认证并显式声明：DOCWEAVE_UI_AUTH=proxy。\n"
    "机器接口 /api/v1 始终需要 API 密钥，不受此设置影响。"
)


def check_listen_config(host: str, warn=None) -> None:
    """Refuse to start open to the network without deciding how the UI is protected.

    Must be called by whatever starts the server (``backend/run.py`` does).  In a container
    the bind address says nothing about exposure, so this warns loudly instead of refusing —
    the compose file publishes to 127.0.0.1 only.
    """
    if is_loopback(host) or ui_auth_mode() != "none":
        return
    if in_container():
        (warn or (lambda m: print(m, file=sys.stderr)))(
            f"警告：容器内监听 {host}，且 DOCWEAVE_UI_AUTH=none（界面无访问控制）。\n"
            "容器的实际暴露范围由端口映射决定：请只映射到 127.0.0.1，并在前面加带认证的反向代理。\n" + REMEDIES)
        return
    raise SystemExit(
        f"拒绝启动：DOCWEAVE_HOST={host} 会对外监听，但 DOCWEAVE_UI_AUTH=none（界面无任何访问控制）。\n" + REMEDIES)


# ------------------------------------------------------------------ API keys
class Auth:
    def __init__(self, store: Store):
        self.store = store
        with store.conn() as c:
            c.executescript(UI_USERS_SCHEMA)
        self._last_seen: dict[str, float] = {}

    # ---------------------------------------------------------- server secret (signed links)
    def secret(self) -> bytes:
        env = os.environ.get("DOCWEAVE_SECRET")
        if env:
            return env.encode()
        row = self.store.one("SELECT value FROM meta WHERE key='server_secret'")
        if row:
            return bytes.fromhex(row["value"])
        s = secrets.token_bytes(32)
        self.store.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('server_secret', ?)", (s.hex(),))
        return s

    # ---------------------------------------------------------- keys
    def create_key(self, name: str, tenant: str = "default", scopes: tuple[str, ...] = ("read", "write"),
                   expires_in_days: float | None = None) -> tuple[str, dict]:
        bad = [s for s in scopes if s not in SCOPES]
        if bad:
            raise ServiceError(400, "bad_scope", f"未知权限范围：{bad}；可用：{', '.join(SCOPES)}")
        kid = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        row = {"id": kid, "name": name[:100] or "api key", "tenant": tenant, "scopes": ",".join(scopes),
               "secret_hash": hashlib.sha256(secret.encode()).hexdigest(), "created_at": now(),
               "expires_at": (now() + expires_in_days * 86400) if expires_in_days else None}
        self.store.insert("api_keys", row)
        return f"{KEY_PREFIX}_{kid}_{secret}", {k: row[k] for k in ("id", "name", "tenant", "scopes", "created_at", "expires_at")}

    def list_keys(self) -> list[dict]:
        return self.store.all("SELECT id, name, tenant, scopes, created_at, expires_at, last_used_at, revoked_at FROM api_keys ORDER BY created_at")

    def revoke_key(self, kid: str) -> bool:
        return bool(self.store.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now(), kid)))

    def verify(self, header: str | None) -> dict:
        """Authorization header → principal {key_id, tenant, scopes}; raises 401 otherwise."""
        token = ""
        if header:
            parts = header.split(None, 1)
            token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else header.strip()
        pieces = token.split("_", 2)
        if len(pieces) != 3 or pieces[0] != KEY_PREFIX:
            raise ServiceError(401, "unauthorized", "缺少或格式不正确的 API 密钥（Authorization: Bearer dw_<id>_<secret>）")
        kid, secret = pieces[1], pieces[2]
        row = self.store.one("SELECT * FROM api_keys WHERE id=?", (kid,))
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not row or not hmac.compare_digest(row["secret_hash"], digest):
            raise ServiceError(401, "unauthorized", "API 密钥无效")
        if row["revoked_at"]:
            raise ServiceError(401, "key_revoked", "该 API 密钥已被撤销")
        if row["expires_at"] and row["expires_at"] < now():
            raise ServiceError(401, "key_expired", "该 API 密钥已过期")
        last = self._last_seen.get(kid, 0)
        if now() - last > 60:                      # throttled write: one row update per key per minute
            self._last_seen[kid] = now()
            self.store.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now(), kid))
        return {"key_id": kid, "tenant": row["tenant"], "scopes": tuple(row["scopes"].split(",")), "name": row["name"]}

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
    def sign_link(self, kind: str, ident: str, tenant: str, ttl_s: int = 900) -> tuple[str, float]:
        exp = time.time() + ttl_s
        payload = base64.urlsafe_b64encode(json.dumps({"k": kind, "i": ident, "t": tenant, "e": int(exp)},
                                                      separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(self.secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{payload}.{sig}", exp

    def open_link(self, token: str) -> dict:
        payload, _, sig = (token or "").partition(".")
        want = hmac.new(self.secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not sig or not hmac.compare_digest(sig, want):
            raise ServiceError(403, "bad_link", "下载链接无效")
        try:
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception as e:  # noqa: BLE001
            raise ServiceError(403, "bad_link", "下载链接无效") from e
        if data.get("e", 0) < time.time():
            raise ServiceError(410, "link_expired", "下载链接已过期；请用 API 重新获取一个链接")
        return data


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
