"""Legacy `dw_` keys: workspace state, rotation with a lineage, revocation that ends it,
signed links that remember who minted them, and a CLI that never handles a secret on argv.

Each test starts from a real platform composed with `synwevo_server.app.create_app` on a
temporary data directory. Nothing here touches `data/`.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from synwevo_core import authority, journal
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.auth import MAX_ROTATION_GRACE_S, Auth
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    return {"api": api, "store": store, "ident": ident, "auth": Auth(store), "ws": ws,
            "data": str(tmp_path / "d"), "tmp": tmp_path}


def call(env, token: str, path="/api/v1/projects", method="GET", **kw):
    return TestClient(env["api"]).request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)


def code(resp) -> str:
    body = resp.json().get("error") or {}
    return body.get("code")


def reason(resp) -> str | None:
    return ((resp.json().get("error") or {}).get("details") or {}).get("reason")


# ------------------------------------------------------------------ workspace state (F011/F107/F316/F343)
def test_freezing_the_workspace_stops_its_keys(env):
    key, _ = env["auth"].create_key("ci", env["ws"], ("read", "write"))
    assert call(env, key).status_code == 200
    env["store"].execute("UPDATE workspaces SET status='frozen' WHERE id=?", (env["ws"],))
    r = call(env, key)
    assert r.status_code == 403 and reason(r) == "workspace_frozen", r.text


def test_deleting_the_workspace_stops_its_keys(env):
    key, _ = env["auth"].create_key("ci", env["ws"], ("read",))
    env["store"].execute("UPDATE workspaces SET status='deleted', deleted_at=? WHERE id=?", (time.time(), env["ws"]))
    r = call(env, key)
    assert r.status_code == 403 and reason(r) == "workspace_deleted"


def test_a_workspace_deleted_by_the_platform_pauses_its_keys_and_mints_none(env, monkeypatch):
    """A44: the platform's deletion (workspace_lifecycle) refuses the workspace's legacy keys and
    any new key for it; a recovery gives the paused key back (it was never revoked). Key minting is
    an allow-list past the legacy workspace: a state nobody has decided about mints nothing (the
    schema's CHECK refuses an unknown status, so that row is stubbed — FIXTURE)."""
    from synwevo_core import workspace_lifecycle
    owner = env["ident"].by_email("alice@example.com")
    team = env["ident"].create_workspace("团队空间（夹具）", owner_id=owner["id"])["id"]
    key, row = env["auth"].create_key("ci", team, ("read",))
    assert call(env, key).status_code == 200 and env["auth"].key_tenant_problem(team) is None
    workspace_lifecycle.platform_transition(env["store"], team, owner["id"], "delete", reason="Isolated fixture: deletion")
    r = call(env, key)
    assert r.status_code == 403 and reason(r) == "workspace_deleted", r.text
    assert env["auth"].key_tenant_problem(team) == f"工作空间 {team} 已删除"
    assert team not in [w["id"] for w in env["auth"].key_tenant_candidates()]
    desc = {"kind": "api_key", "key_id": row["id"], "tenant": team}
    assert authority.check(env["store"], desc, need="read", tenant=team) == (False, "workspace_deleted")
    workspace_lifecycle.platform_transition(env["store"], team, owner["id"], "recover", reason="Isolated fixture: recovery")
    assert call(env, key).status_code == 200 and env["auth"].key_tenant_problem(team) is None
    original = Store.one

    def stubbed(store, sql, args=()):
        found = original(store, sql, args)
        if sql.startswith("SELECT id, status, deleted_at FROM workspaces") and found:
            return {**found, "status": "archived"}
        return found
    monkeypatch.setattr(Store, "one", stubbed)
    try:
        assert env["auth"].key_tenant_problem(team) == f"工作空间 {team} 当前不可用（archived），不能为它签发密钥"
    finally:
        monkeypatch.setattr(Store, "one", original)


def test_disabling_docs_for_the_workspace_stops_legacy_keys_too(env):
    """The verifier's repro: service accounts were stopped, dw_ keys were not."""
    key, _ = env["auth"].create_key("ci", env["ws"], ("read", "write"))
    assert call(env, key).status_code == 200
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (env["ws"],))
    r = call(env, key)
    assert r.status_code == 403 and reason(r) == "app_not_enabled_for_workspace"


def test_disabling_docs_platform_wide_stops_every_key(env):
    key, _ = env["auth"].create_key("ci", env["ws"], ("read",))
    env["store"].execute("UPDATE apps SET status='disabled' WHERE id='docweave'")
    assert call(env, key).status_code == 403


def test_a_platform_wide_disable_also_halts_queued_work_of_legacy_workspace_keys(env):
    """Requests and deferred work agree: a key of the unclaimed legacy workspace (or of a
    free-form tenant) is refused at the gate when Docs is off platform-wide, and so is the work
    it already queued."""
    env["store"].execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                         "VALUES ('default','历史数据（待认领）','legacy','pending_claim',0,0)")
    for tenant in ("default", "free-form-tenant"):
        key, k = env["auth"].create_key("legacy", tenant, ("read", "write"))
        desc = {"kind": "api_key", "key_id": k["id"], "tenant": tenant}
        assert authority.check(env["store"], desc, need="write", tenant=tenant) == (True, None)
        env["store"].execute("UPDATE apps SET status='disabled' WHERE id='docweave'")
        r = call(env, key)
        assert r.status_code == 403 and reason(r) == "app_disabled", r.text
        assert authority.check(env["store"], desc, need="write", tenant=tenant) == (False, "app_disabled")
        env["store"].execute("UPDATE apps SET status='active' WHERE id='docweave'")


def test_the_unclaimed_legacy_workspace_stays_usable_by_operator_keys(env):
    """Operator-issued keys predate the platform; `--tenant default` is how
    docs/integration/api.md §1 has always told operators to automate against that data."""
    env["store"].execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                         "VALUES ('default','历史数据（待认领）','legacy','pending_claim',0,0)")
    key, _ = env["auth"].create_key("legacy", "default", ("read", "write"))
    assert call(env, key).status_code == 200
    assert call(env, key, method="POST", json={"name": "历史"}).status_code in (200, 201)


# ------------------------------------------------------------------ rotation keeps the lineage
def test_rotation_continues_the_lineage_and_stops_the_old_key(env):
    a = env["auth"]
    old_tok, old = a.create_key("ci", env["ws"], ("read", "write"))
    new_tok, new, before = a.rotate_key(old["id"])
    assert new["lineage"] == old["id"] and before["replaced_by"] == new["id"]
    assert before["revoke_reason"] == "rotated" and before["status"] == "rotated"
    r = call(env, old_tok)
    assert r.status_code == 401 and code(r) == "key_rotated"
    assert call(env, new_tok).status_code == 200
    # Work queued under the old key is the same automation's work: still authorised.
    desc = authority.describe({"kind": "api_key", "key_id": old["id"], "lineage": old["id"],
                               "tenant": env["ws"], "scopes": ("read", "write")})
    assert authority.check(env["store"], desc, need="write", tenant=env["ws"]) == (True, None)


def test_a_rotation_grace_keeps_the_old_key_working_for_a_bounded_time(env):
    a = env["auth"]
    old_tok, old = a.create_key("ci", env["ws"], ("read",))
    new_tok, new, before = a.rotate_key(old["id"], grace_s=300)
    assert before["status"] == "grace" and before["expires_at"] <= time.time() + 301
    assert call(env, old_tok).status_code == 200
    assert call(env, new_tok).status_code == 200
    env["store"].execute("UPDATE api_keys SET expires_at=? WHERE id=?", (time.time() - 1, old["id"]))
    r = call(env, old_tok)
    assert r.status_code == 401 and code(r) == "key_rotated"
    with pytest.raises(ServiceError):
        a.rotate_key(new["id"], grace_s=MAX_ROTATION_GRACE_S + 1)


def test_a_grace_never_extends_the_old_key(env):
    a = env["auth"]
    _, old = a.create_key("ci", env["ws"], ("read",), expires_in_days=1 / 24 / 60)   # one minute left
    _, _, before = a.rotate_key(old["id"], grace_s=3600)
    assert before["expires_at"] <= old["expires_at"] + 1e-6


def test_rotation_keeps_the_keys_lifetime(env):
    """F125/F354: rotating an expiring key used to produce a key that never expires."""
    a = env["auth"]
    _, old = a.create_key("ci", env["ws"], ("read",), expires_in_days=30)
    _, new, _ = a.rotate_key(old["id"])
    assert new["expires_at"] is not None
    assert abs((new["expires_at"] - new["created_at"]) - 30 * 86400) < 5
    _, newer, _ = a.rotate_key(new["id"], keep_absolute_expiry=True)
    assert newer["expires_at"] == new["expires_at"], "自行轮换不能延长密钥寿命"


def test_one_key_has_exactly_one_successor_under_concurrency(env):
    a = env["auth"]
    _, old = a.create_key("ci", env["ws"], ("read",))
    results, errors = [], []
    # One store per contender, like separate worker processes: each has its own connection and
    # its own in-process lock, so only the database's write transaction keeps them apart.
    contenders = [Auth(Store(env["data"])) for _ in range(6)]
    gate = threading.Barrier(len(contenders))

    def go(auth):
        gate.wait()
        try:
            results.append(auth.rotate_key(old["id"]))
        except ServiceError as e:
            errors.append(e.code)

    threads = [threading.Thread(target=go, args=(c,)) for c in contenders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1, (results, errors)
    assert set(errors) <= {"already_rotated", "key_revoked"}
    lineage = env["store"].all("SELECT id FROM api_keys WHERE lineage=?", (old["id"],))
    assert len(lineage) == 2


@pytest.mark.parametrize("grace", [float("nan"), float("inf"), -1, MAX_ROTATION_GRACE_S + 1, True, "60"])
def test_rotation_refuses_a_grace_that_is_not_a_bounded_number(env, grace):
    """NaN compares false to everything: it used to pass the range check, and `t + NaN` was
    stored as NULL — an old key in grace for ever."""
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",), expires_in_days=1)
    with pytest.raises(ServiceError) as e:
        a.rotate_key(k["id"], grace_s=grace)
    assert e.value.code == "bad_grace"
    after = a.get_key(k["id"])
    assert after["status"] == "active" and after["replaced_by"] is None
    assert len(env["store"].all("SELECT id FROM api_keys")) == 1


@pytest.mark.parametrize("days", [float("nan"), float("inf"), 0, -1])
def test_a_lifetime_must_be_a_finite_positive_number_of_days(env, days):
    """`--expires-days nan` passed `<= 0` and stored expires_at NULL: a permanent key."""
    a = env["auth"]
    with pytest.raises(ServiceError) as e:
        a.create_key("ci", env["ws"], ("read",), expires_in_days=days)
    assert e.value.code == "bad_expiry"
    _, k = a.create_key("ci", env["ws"], ("read",), expires_in_days=1)
    with pytest.raises(ServiceError) as e:
        a.rotate_key(k["id"], expires_in_days=days)
    assert e.value.code == "bad_expiry"
    assert a.get_key(k["id"])["status"] == "active"
    assert all(r["expires_at"] is not None for r in env["store"].all("SELECT expires_at FROM api_keys"))


def test_a_lineage_never_has_more_than_two_valid_keys(env):
    """Chained graced rotations used to leave every key of the chain valid at once (and, with
    rate buckets per key, multiplied the credential's rate limit)."""
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    tokens, kid = [tok], k["id"]
    for _ in range(5):
        new_tok, new, _ = a.rotate_key(kid, grace_s=86400)
        tokens.append(new_tok)
        kid = new["id"]
        valid = [t for t in tokens if call(env, t).status_code == 200]
        assert valid == tokens[-2:], "当前密钥 + 仅上一把在宽限期内"
    statuses = [x["status"] for x in a.lineage_keys(k["id"])]
    assert statuses == ["rotated"] * 4 + ["grace", "active"]
    # Deferred work of the lineage is unaffected: it belongs to the lineage, not to one key.
    desc = {"kind": "api_key", "key_id": k["id"], "lineage": k["id"], "tenant": env["ws"]}
    assert authority.check(env["store"], desc, need="read", tenant=env["ws"]) == (True, None)


def test_rotation_is_journalled_so_a_restore_does_not_revive_the_old_key(env):
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    before = time.time()
    new_tok, new, _ = a.rotate_key(k["id"])
    entries = [e for e in journal.read(env["data"], since=before - 1) if e["kind"] == "api_key_superseded"]
    assert [e["key_id"] for e in entries] == [k["id"]]
    raw = (env["tmp"] / "d" / journal.FILENAME).read_text()
    assert tok.split("_", 2)[2] not in raw and new_tok.split("_", 2)[2] not in raw
    # A backup taken before the rotation: the old key alive, its successor absent, and no receipt
    # of the rotation's journal line (journal._intent commits it with the rotation).
    env["store"].execute("DELETE FROM api_keys WHERE id=?", (new["id"],))
    env["store"].execute("UPDATE api_keys SET revoked_at=NULL, revoke_reason=NULL, replaced_by=NULL WHERE id=?",
                         (k["id"],))
    env["store"].execute("DELETE FROM meta WHERE key=?", ("journal_receipt:" + entries[0]["attempt_id"],))
    assert call(env, tok).status_code == 200
    journal.apply(env["store"], entries)
    assert call(env, tok).status_code == 401


def test_a_graced_rotation_is_journalled_too(env):
    a = env["auth"]
    _, k = a.create_key("ci", env["ws"], ("read",))
    before = time.time()
    a.rotate_key(k["id"], grace_s=600)
    assert [e["key_id"] for e in journal.read(env["data"], since=before - 1)
            if e["kind"] == "api_key_superseded"] == [k["id"]]


def test_a_rotation_that_cannot_be_journalled_changes_nothing(env):
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    jpath = env["tmp"] / "d" / journal.FILENAME
    if jpath.exists():
        jpath.unlink()
    jpath.mkdir()                               # FIXTURE: the journal cannot be opened for append
    try:
        with pytest.raises(ServiceError) as e:
            a.rotate_key(k["id"], grace_s=60)
        assert e.value.code == "journal_failed"
    finally:
        jpath.rmdir()
    assert a.get_key(k["id"])["status"] == "active"
    assert len(env["store"].all("SELECT id FROM api_keys")) == 1
    assert call(env, tok).status_code == 200


def test_a_revoked_or_rotated_key_cannot_be_rotated_again(env):
    a = env["auth"]
    _, k = a.create_key("ci", env["ws"], ("read",))
    a.rotate_key(k["id"])
    with pytest.raises(ServiceError) as e:
        a.rotate_key(k["id"])
    assert e.value.code == "already_rotated"
    _, k2 = a.create_key("ci2", env["ws"], ("read",))
    a.revoke_key(k2["id"])
    with pytest.raises(ServiceError) as e:
        a.rotate_key(k2["id"])
    assert e.value.code == "key_revoked"


# ------------------------------------------------------------------ revocation ends the lineage
def test_revoking_ends_the_whole_lineage_including_a_key_in_grace(env):
    a = env["auth"]
    old_tok, old = a.create_key("ci", env["ws"], ("read", "write"))
    new_tok, new, _ = a.rotate_key(old["id"], grace_s=600)
    assert call(env, old_tok).status_code == 200
    n = a.revoke_key(new["id"])
    assert n == 2
    for tok in (old_tok, new_tok):
        r = call(env, tok)
        assert r.status_code == 401 and code(r) == "key_revoked", r.text
    desc = {"kind": "api_key", "key_id": old["id"], "lineage": old["id"], "tenant": env["ws"]}
    assert authority.check(env["store"], desc, need="read", tenant=env["ws"]) == (False, "credential_revoked")


def test_revocation_is_journalled_so_a_restore_cannot_bring_the_key_back(env):
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    new_tok, new, _ = a.rotate_key(k["id"], grace_s=600)
    before = time.time()
    a.revoke_key(new["id"])
    entries = [e for e in journal.read(env["data"], since=before - 1) if e["kind"] == "api_key_revoked"]
    assert entries and entries[-1]["key_id"] == k["id"], "记录的是密钥链的根，恢复出的旧库里一定有它"
    raw = (env["tmp"] / "d" / journal.FILENAME).read_text()
    assert tok.split("_", 2)[2] not in raw and new_tok.split("_", 2)[2] not in raw
    # Simulate a restore of a backup taken before the rotation: only the root exists, alive, and
    # neither journal line's receipt is there.
    env["store"].execute("DELETE FROM api_keys WHERE id=?", (new["id"],))
    env["store"].execute("UPDATE api_keys SET revoked_at=NULL, revoke_reason=NULL, replaced_by=NULL, expires_at=NULL "
                         "WHERE id=?", (k["id"],))
    env["store"].execute("DELETE FROM meta WHERE key LIKE 'journal_receipt:%'")
    assert call(env, tok).status_code == 200
    journal.apply(env["store"], entries)
    assert call(env, tok).status_code == 401


# ------------------------------------------------------------------ signed links carry the principal
def test_a_signed_link_carries_a_compact_descriptor_of_who_minted_it(env):
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    principal = a.verify(f"Bearer {tok}") | {"kind": "api_key"}
    link, _ = a.sign_link("artifact", "a_1", env["ws"], 60, principal=principal)
    data = a.open_link(link)
    assert data["p"] == {"kind": "api_key", "key_id": k["id"], "tenant": env["ws"]}
    assert authority.check(env["store"], data["p"], need="read", tenant=data["t"]) == (True, None)
    a.revoke_key(k["id"])
    ok, why = authority.check(env["store"], data["p"], need="read", tenant=data["t"])
    assert not ok and why == "credential_revoked"


def test_a_link_survives_rotation_but_not_a_frozen_workspace(env):
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    link, _ = a.sign_link("archive", "j_1", env["ws"], 60,
                          principal=a.verify(f"Bearer {tok}") | {"kind": "api_key"})
    a.rotate_key(k["id"])
    p = a.open_link(link)["p"]
    assert authority.check(env["store"], p, need="read", tenant=env["ws"])[0]
    env["store"].execute("UPDATE workspaces SET status='frozen' WHERE id=?", (env["ws"],))
    assert authority.check(env["store"], p, need="read", tenant=env["ws"]) == (False, "workspace_frozen")


def test_links_minted_without_a_principal_keep_the_old_shape(env):
    a = env["auth"]
    link, _ = a.sign_link("artifact", "a_1", env["ws"], 60)
    assert "p" not in a.open_link(link)


def legacy_link(a: Auth, body: dict) -> str:
    """FIXTURE: a link in the format minted before payloads were sealed (`<base64 JSON>.<HMAC>`)."""
    import base64
    import hashlib
    import hmac
    payload = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{payload}.{hmac.new(a.secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]}"


def test_the_descriptor_is_signed(env):
    """Editing `p` in the URL (to point at a live key) must break the link."""
    import base64
    a = env["auth"]
    tok, k = a.create_key("ci", env["ws"], ("read",))
    # The legacy format is still accepted until its links expire, so its signature still matters.
    body = {"k": "artifact", "i": "a_1", "t": env["ws"], "e": int(time.time()) + 60,
            "p": {"kind": "api_key", "key_id": k["id"]}}
    link = legacy_link(a, body)
    assert a.open_link(link)["p"]["key_id"] == k["id"]
    payload, _, sig = link.partition(".")
    body["p"]["key_id"] = "someoneelse"
    forged = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    with pytest.raises(ServiceError) as e:
        a.open_link(f"{forged}.{sig}")
    assert e.value.code == "bad_link"
    # The current format is sealed: any change to it is refused. The flips land inside complete
    # base64 quanta: a padding-stripped tail carries 2 or 4 unused bits, and a flip that only
    # touches those decodes to the very same bytes — no change to the content, so nothing for the
    # seal to refuse (the last-character flip failed that way once on 2026-09-27).
    sealed, _ = a.sign_link("artifact", "a_1", env["ws"], 60, principal=a.verify(f"Bearer {tok}") | {"kind": "api_key"})
    blob_start = sealed.index(".") + 1
    last_full = blob_start + (len(sealed) - blob_start) // 4 * 4 - 1
    for i in (3, len(sealed) // 2, last_full):
        flipped = sealed[:i] + ("A" if sealed[i] != "A" else "B") + sealed[i + 1:]
        with pytest.raises(ServiceError) as e:
            a.open_link(flipped)
        assert e.value.code == "bad_link"


def test_a_link_does_not_show_its_holder_who_minted_it(env):
    """A link is handed to third parties. It used to carry the minting key's id and lineage — or
    a person's user id, client id and grant family — in readable base64."""
    import base64
    a = env["auth"]
    _, k = a.create_key("ci", env["ws"], ("read",))
    tok, new, _ = a.rotate_key(k["id"])           # key id and lineage now differ: both would show
    key_principal = a.verify(f"Bearer {tok}") | {"kind": "api_key"}
    delegated = {"kind": "delegated", "user_id": "u_person_42", "client_id": "client-xyz",
                 "family_id": "fam_secretive", "tenant": env["ws"]}
    for principal in (key_principal, delegated):
        link, _ = a.sign_link("artifact", "a_visible_id", env["ws"], 60, principal=principal)
        blob = link.split(".", 1)[1]
        decoded = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
        for ident in (k["id"], new["id"], "u_person_42", "client-xyz", "fam_secretive", "a_visible_id", env["ws"]):
            assert ident not in link and ident.encode() not in decoded, ident
        opened = a.open_link(link)
        assert opened["i"] == "a_visible_id" and opened["t"] == env["ws"] and opened["p"]["kind"] == principal["kind"]
    assert a.open_link(a.sign_link("artifact", "a_1", env["ws"], 60, principal=key_principal)[0])["p"] == \
        {"kind": "api_key", "key_id": new["id"], "lineage": k["id"], "tenant": env["ws"]}


def test_a_link_minted_before_sealing_is_honoured_until_it_expires(env):
    a = env["auth"]
    live = legacy_link(a, {"k": "artifact", "i": "a_1", "t": env["ws"], "e": int(time.time()) + 60})
    assert a.open_link(live)["i"] == "a_1"
    dead = legacy_link(a, {"k": "artifact", "i": "a_1", "t": env["ws"], "e": int(time.time()) - 1})
    with pytest.raises(ServiceError) as e:
        a.open_link(dead)
    assert e.value.code == "link_expired"


@pytest.mark.parametrize("token", ["é.é", "eyJ.ü" + "0" * 31, "l2.ünicode", "l2.", "l2.AAAA", ""])
def test_a_malformed_link_is_a_bad_link_not_a_server_error(env, token):
    """Non-ASCII input reached hmac.compare_digest as str and raised TypeError: a 500."""
    with pytest.raises(ServiceError) as e:
        env["auth"].open_link(token)
    assert e.value.code == "bad_link"


def test_a_non_ascii_download_path_is_refused_cleanly(env):
    r = TestClient(env["api"]).get("/api/v1/download/%C3%A9.%C3%A9")
    assert r.status_code == 403 and code(r) == "bad_link", r.text


# ------------------------------------------------------------------ principal shape
def test_an_api_key_principal_carries_its_lineage(env):
    a = env["auth"]
    _, k = a.create_key("ci", env["ws"], ("read",))
    tok, new, _ = a.rotate_key(k["id"])
    from synwevo_core.machine import MachineAuth
    p = MachineAuth(env["store"], env["ident"], a).verify(f"Bearer {tok}")
    assert p["kind"] == "api_key" and p["key_id"] == new["id"] and p["lineage"] == k["id"]
    assert p["grant_family"] is None


# ------------------------------------------------------------------ the CLI
def cli(env, *args, stdin: str | None = None):
    e = {**os.environ, "SYNWEVO_DATA_DIR": env["data"]}
    e.pop("DOCWEAVE_DATA_DIR", None)
    return subprocess.run([sys.executable, "-m", "synwevo_docs.cli", "--data", env["data"], "apikey", *args],
                          capture_output=True, text=True, input=stdin, env=e, timeout=120)


def test_cli_create_requires_an_explicit_existing_workspace(env):
    r = cli(env, "create", "--name", "ci")
    assert r.returncode == 2 and "--tenant" in r.stderr
    r = cli(env, "create", "--name", "ci", "--tenant", "no-such-workspace")
    assert r.returncode == 2 and "找不到工作空间" in r.stderr
    env["store"].execute("UPDATE workspaces SET status='frozen' WHERE id=?", (env["ws"],))
    r = cli(env, "create", "--name", "ci", "--tenant", env["ws"])
    assert r.returncode == 2 and "冻结" in r.stderr
    env["store"].execute("UPDATE workspaces SET status='active' WHERE id=?", (env["ws"],))
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (env["ws"],))
    r = cli(env, "create", "--name", "ci", "--tenant", env["ws"])
    assert r.returncode == 2 and "尚未启用" in r.stderr
    assert not env["store"].one("SELECT 1 FROM api_keys")


def test_cli_create_rotate_revoke_round_trip_and_audit(env):
    r = cli(env, "create", "--name", "ci", "--tenant", env["ws"], "--scopes", "read,write", "--expires-days", "30")
    assert r.returncode == 0, r.stderr
    token = next(line.strip() for line in r.stdout.splitlines() if line.strip().startswith("dw_"))
    kid = token.split("_", 2)[1]
    assert call(env, token).status_code == 200

    lst = cli(env, "list")
    assert kid in lst.stdout and token.split("_", 2)[2] not in lst.stdout

    r = cli(env, "rotate", kid, "--grace-minutes", "10")
    assert r.returncode == 0, r.stderr
    new_token = next(line.strip() for line in r.stdout.splitlines() if line.strip().startswith("dw_"))
    # Count printed keys, not the substring: the random part of a secret can itself contain
    # "dw_" (seen once in 2026-09-27's run), and that is not a second key being printed.
    assert sum(1 for line in r.stdout.splitlines() if line.strip().startswith("dw_")) == 1, "只打印一次新密钥"
    assert token not in r.stdout, "旧密钥从不打印"
    assert call(env, token).status_code == 200            # still inside the grace
    assert call(env, new_token).status_code == 200
    new = Auth(env["store"]).get_key(new_token.split("_", 2)[1])
    assert new["lineage"] == kid and new["expires_at"] and new["expires_at"] - new["created_at"] > 29 * 86400

    r = cli(env, "revoke", new["id"])
    assert r.returncode == 0, r.stderr
    assert call(env, token).status_code == 401 and call(env, new_token).status_code == 401

    actions = [x["action"] for x in env["store"].all(
        "SELECT action FROM audit_events WHERE object_kind='api_key' ORDER BY seq")]
    assert actions == ["apikey.create", "apikey.rotate", "apikey.revoke"]
    for row in env["store"].all("SELECT meta FROM audit_events WHERE object_kind='api_key'"):
        assert token.split("_", 2)[2] not in row["meta"] and new_token.split("_", 2)[2] not in row["meta"]


def test_cli_never_takes_a_full_key_on_the_command_line(env):
    token, k = env["auth"].create_key("ci", env["ws"], ("read",))
    r = cli(env, "revoke", token)
    assert r.returncode == 2
    assert token.split("_", 2)[2] not in (r.stdout + r.stderr), "不回显密文"
    assert k["id"] in r.stderr
    assert call(env, token).status_code == 200, "拒绝了就什么都不做"


def test_cli_revokes_a_key_read_from_stdin(env):
    token, k = env["auth"].create_key("ci", env["ws"], ("read",))
    assert cli(env, "revoke", "--stdin", stdin="dw_" + k["id"] + "_wrong\n").returncode == 2
    r = cli(env, "revoke", "--stdin", stdin=token + "\n")
    assert r.returncode == 0, r.stderr
    assert call(env, token).status_code == 401
    assert token.split("_", 2)[2] not in (r.stdout + r.stderr)


def test_cli_rotate_refuses_an_unbounded_grace(env):
    _, k = env["auth"].create_key("ci", env["ws"], ("read",))
    r = cli(env, "rotate", k["id"], "--grace-minutes", str(MAX_ROTATION_GRACE_S // 60 + 1))
    assert r.returncode == 2
    assert Auth(env["store"]).get_key(k["id"])["status"] == "active"


def test_openapi_export_does_not_open_the_data_directory(tmp_path):
    """F125: exporting the spec used to create and migrate whatever --data pointed at."""
    target = tmp_path / "real-data"
    out = tmp_path / "openapi.json"
    e = {**os.environ}
    e.pop("SYNWEVO_DATA_DIR", None)
    e.pop("DOCWEAVE_DATA_DIR", None)
    r = subprocess.run([sys.executable, "-m", "synwevo_docs.cli", "--data", str(target), "openapi", "--out", str(out)],
                       capture_output=True, text=True, env=e, timeout=180)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["paths"]
    assert not target.exists(), "导出 OpenAPI 不应创建或升级数据目录"


# ------------------------------------------------------------------ root secret (F007)
def test_changing_the_root_secret_under_sealed_data_is_refused(tmp_path, monkeypatch):
    """Configuring SYNWEVO_SECRET on an install whose TOTP seeds were sealed under the generated
    root used to make every seed silently undecryptable."""
    from synwevo_core import sealed
    from synwevo_core.auth import RootSecretMismatch
    monkeypatch.delenv("SYNWEVO_SECRET", raising=False)
    monkeypatch.delenv("DOCWEAVE_SECRET", raising=False)
    store = Store(tmp_path / "d")
    sealed_seed = sealed.seal(store, "mfa-seed", "JBSWY3DPEHPK3PXP")
    assert sealed.unseal(store, "mfa-seed", sealed_seed) == "JBSWY3DPEHPK3PXP"
    monkeypatch.setenv("SYNWEVO_SECRET", "a-different-root-configured-later")
    with pytest.raises(RootSecretMismatch):
        Auth(store).secret()
