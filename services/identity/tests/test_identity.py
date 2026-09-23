"""Accounts, passwords, sessions and the properties that are easy to claim and hard to hold.

The ones worth stating: an unauthenticated caller cannot learn whether an address has an account
(including by timing), failing logins against someone else's account cannot lock them out, and a
session is revocable the moment the database says so.
"""
from __future__ import annotations

import time

import pytest

from platform_core import passwords
from platform_core.identity import Identity, normalise_email, valid_email
from platform_core.errors import ServiceError
from platform_core.sessions import Sessions, csrf_ok, describe_client
from platform_core.store import Store


@pytest.fixture()
def ident(tmp_path):
    return Identity(Store(tmp_path / "d"))


@pytest.fixture()
def sess(ident):
    return Sessions(ident.store)


LONG_OK = "correct horse battery staple and more"


# ---------------------------------------------------------------- password policy
def test_single_factor_floor_is_fifteen_characters():
    with pytest.raises(passwords.PasswordError) as e:
        passwords.check_policy("short1234", single_factor=True)
    assert "15" in str(e.value)
    passwords.check_policy("一个足够长的中文口令用来做测试", single_factor=True)   # 15 chars, accepted


def test_mfa_floor_is_lower_but_never_below_eight():
    passwords.check_policy("12345678x", single_factor=False)
    with pytest.raises(passwords.PasswordError):
        passwords.check_policy("1234567", single_factor=False)


def test_long_and_unicode_passwords_are_accepted_and_not_truncated():
    long = "🔐 " + "这是一个很长的口令 " * 10
    passwords.check_policy(long)
    phc = passwords.hash_password(long)
    assert passwords.verify(phc, long)
    # a password that merely shares a long prefix must not verify — i.e. nothing was truncated
    assert not passwords.verify(phc, long[:-1])


def test_spaces_are_preserved_not_stripped():
    p = "  leading and trailing spaces matter  "
    phc = passwords.hash_password(p)
    assert passwords.verify(phc, p)
    assert not passwords.verify(phc, p.strip())


def test_nfkc_normalisation_makes_equivalent_input_methods_match():
    # full-width and half-width digits normalise to the same string under NFKC
    wide, narrow = "ｐａｓｓｗｏｒｄ１２３４５６７８", "password12345678"
    assert passwords.normalise(wide) == narrow
    assert passwords.verify(passwords.hash_password(wide), narrow)


def test_common_and_service_related_passwords_are_refused():
    for bad in ("password123456789", "docweave-is-my-password", "aaaaaaaaaaaaaaaaaa"):
        with pytest.raises(passwords.PasswordError):
            passwords.check_policy(bad)


def test_password_may_not_contain_the_account_email_or_name():
    with pytest.raises(passwords.PasswordError):
        passwords.check_policy("zhangsan-my-long-password", email="zhangsan@example.com")


def test_hash_is_argon2id_with_the_measured_parameters():
    phc = passwords.hash_password(LONG_OK)
    assert phc.startswith("$argon2id$")
    p = passwords.parameters()
    assert f"m={p['memory_kib']}" in phc and f"t={p['time_cost']}" in phc and f"p={p['parallelism']}" in phc
    assert p["memory_kib"] >= 19456, "不得低于 OWASP 的内存下限"


def test_same_password_hashes_differently_each_time():
    assert passwords.hash_password(LONG_OK) != passwords.hash_password(LONG_OK)


def test_verify_is_total_and_never_raises():
    assert passwords.verify("not-a-phc-string", LONG_OK) is False
    assert passwords.verify("", "") is False


# ---------------------------------------------------------------- accounts
def test_email_normalisation_and_validation():
    assert normalise_email("  Alice@Example.COM ") == "alice@example.com"
    # dots and +tags are provider-specific; treating them as equal would merge distinct addresses
    assert normalise_email("a.b+x@example.com") == "a.b+x@example.com"
    assert valid_email("a@b.co") and not valid_email("a@b") and not valid_email("no-at-sign.com")


def test_creating_an_account_gives_it_a_personal_workspace_it_owns(ident):
    u = ident.create_user("alice@example.com", LONG_OK, display_name="Alice")
    spaces = ident.workspaces_for(u["id"])
    assert len(spaces) == 1 and spaces[0]["kind"] == "personal" and spaces[0]["role"] == "owner"


def test_duplicate_email_is_refused_case_insensitively(ident):
    ident.create_user("alice@example.com", LONG_OK)
    with pytest.raises(ServiceError) as e:
        ident.create_user("ALICE@example.com", LONG_OK)
    assert e.value.code == "email_taken"


def test_legacy_workspace_never_appears_for_anyone(ident):
    """The whole point of the migration design, asserted from the account side."""
    ts = __import__("platform_core.store", fromlist=["now"]).now()
    ident.store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                        "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    u = ident.create_user("alice@example.com", LONG_OK)
    assert [w["id"] for w in ident.workspaces_for(u["id"])] != ["default"]
    assert "default" not in [w["id"] for w in ident.workspaces_for(u["id"])]


# ---------------------------------------------------------------- authentication
def test_login_succeeds_and_upgrades_a_stale_hash(ident, monkeypatch):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    # simulate a hash made with weaker parameters
    import argon2
    from argon2.low_level import Type
    weak = argon2.PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID).hash(LONG_OK)
    ident.store.execute("UPDATE user_passwords SET phc=? WHERE user_id=?", (weak, u["id"]))

    r = ident.authenticate("alice@example.com", LONG_OK, ip="1.2.3.4")
    assert r["ok"] and r["user"]["id"] == u["id"]
    after = ident.store.one("SELECT phc FROM user_passwords WHERE user_id=?", (u["id"],))["phc"]
    assert after != weak and f"m={passwords.parameters()['memory_kib']}" in after


def test_wrong_password_and_unknown_account_are_indistinguishable(ident):
    ident.create_user("alice@example.com", LONG_OK, status="active")
    a = ident.authenticate("alice@example.com", "the-wrong-password-entirely", ip="1.1.1.1")
    b = ident.authenticate("nobody@example.com", "the-wrong-password-entirely", ip="2.2.2.2")
    assert a["ok"] is False and b["ok"] is False
    assert a["user"] is not None and b["user"] is None      # internal detail only
    # what a caller could observe must not differ
    assert (a["ok"], "retry_after" in a) == (b["ok"], "retry_after" in b)


def test_unknown_account_still_pays_for_a_hash(ident):
    """Returning early for an unknown address is enumeration by stopwatch."""
    ident.create_user("alice@example.com", LONG_OK, status="active")

    def timed(email, ip):
        t0 = time.perf_counter()
        ident.authenticate(email, "the-wrong-password-entirely", ip=ip)
        return time.perf_counter() - t0

    known = min(timed("alice@example.com", f"10.0.0.{i}") for i in range(3))
    unknown = min(timed("nobody@example.com", f"10.1.0.{i}") for i in range(3))
    # the unknown path must not be dramatically faster; both run one real Argon2 verification
    assert unknown > known * 0.5, f"未知账户明显更快：{unknown:.3f}s vs {known:.3f}s"


def test_suspended_account_is_rejected_only_after_the_password_is_correct(ident):
    """Otherwise an attacker learns an account is suspended without knowing its password."""
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    ident.set_status(u["id"], "suspended")
    bad = ident.authenticate("alice@example.com", "wrong-password-here-ok", ip="3.3.3.3")
    good = ident.authenticate("alice@example.com", LONG_OK, ip="3.3.3.4")
    assert bad["reason"] == "bad_password"
    assert good["ok"] is False and good["reason"] == "status_suspended"


def test_failed_logins_do_not_let_one_person_lock_another_out(ident):
    """The account bucket backs off; it must not become a denial-of-service lever."""
    ident.create_user("victim@example.com", LONG_OK, status="active")
    for i in range(12):
        ident.authenticate("victim@example.com", "wrong-password-here-ok", ip=f"9.9.9.{i}")
    # the real owner is slowed, not locked out: after the backoff elapses the password still works
    ident.store.execute("UPDATE auth_attempts SET last_at=last_at-10000 WHERE bucket LIKE 'user:%'")
    assert ident.authenticate("victim@example.com", LONG_OK, ip="9.9.9.99")["ok"] is True


def test_repeated_failures_from_one_address_are_clamped(ident):
    ident.create_user("alice@example.com", LONG_OK, status="active")
    for _ in range(35):
        ident.store.execute("UPDATE auth_attempts SET last_at=last_at-10000 WHERE bucket LIKE 'user:%'")
        ident.authenticate("alice@example.com", "wrong-password-here-ok", ip="5.5.5.5")
    ident.store.execute("UPDATE auth_attempts SET last_at=last_at-10000 WHERE bucket LIKE 'user:%'")
    r = ident.authenticate("alice@example.com", LONG_OK, ip="5.5.5.5")
    assert r["ok"] is False and r["reason"] == "address_blocked"


# ---------------------------------------------------------------- sessions
def test_session_cookie_is_not_stored(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    assert raw not in str(row) and row["id"] != raw
    assert sess.store.one("SELECT COUNT(*) AS n FROM sessions WHERE id=?", (raw,))["n"] == 0
    assert sess.load(raw)["user_id"] == u["id"]


def test_revocation_takes_effect_immediately(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    assert sess.load(raw)
    sess.revoke(row["id"], "test")
    assert sess.load(raw) is None


def test_sign_out_everywhere_can_keep_the_current_session(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    keep_raw, keep = sess.create(u["id"], workspace_id=None, amr="password")
    other_raw, _ = sess.create(u["id"], workspace_id=None, amr="password")
    sess.revoke_all_for_user(u["id"], "password_changed", except_session=keep["id"])
    assert sess.load(keep_raw) is not None and sess.load(other_raw) is None


def test_idle_and_absolute_expiry_are_both_enforced_on_read(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    sess.store.execute("UPDATE sessions SET idle_expires_at=? WHERE id=?", (time.time() - 1, row["id"]))
    assert sess.load(raw) is None

    raw2, row2 = sess.create(u["id"], workspace_id=None, amr="password")
    sess.store.execute("UPDATE sessions SET absolute_expires_at=? WHERE id=?", (time.time() - 1, row2["id"]))
    assert sess.load(raw2) is None


def test_touch_extends_idle_but_never_the_absolute_deadline(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    absolute = row["absolute_expires_at"]
    sess.store.execute("UPDATE sessions SET absolute_expires_at=? WHERE id=?", (time.time() + 60, row["id"]))
    sess.load(raw)
    after = sess.store.one("SELECT idle_expires_at, absolute_expires_at FROM sessions WHERE id=?", (row["id"],))
    assert after["absolute_expires_at"] < absolute            # untouched by the refresh
    assert after["idle_expires_at"] <= after["absolute_expires_at"], "空闲期不能越过绝对上限"


def test_reauth_is_scoped_so_one_proof_does_not_unlock_everything(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    sess.mark_reauth(row["id"], "add_passkey")
    s = sess.load(raw)
    assert Sessions.reauth_fresh(s, "add_passkey")
    assert not Sessions.reauth_fresh(s, "delete_workspace")


def test_reauth_expires(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password")
    sess.store.execute("UPDATE sessions SET reauth_at=? WHERE id=?", (time.time() - 10_000, row["id"]))
    assert not Sessions.reauth_fresh(sess.load(raw), "anything")


def test_session_list_marks_the_current_one_and_claims_no_location(ident, sess):
    u = ident.create_user("alice@example.com", LONG_OK, status="active")
    raw, row = sess.create(u["id"], workspace_id=None, amr="password",
                           user_agent="Mozilla/5.0 (Macintosh) Chrome/120 Safari/537", ip="1.2.3.4")
    sess.create(u["id"], workspace_id=None, amr="password", user_agent="Mozilla/5.0 (Windows NT) Firefox/1")
    items = sess.list_for_user(u["id"], current_id=row["id"])
    assert len(items) == 2 and sum(i["current"] for i in items) == 1
    assert {"macOS 上的 Chrome", "Windows 上的 Firefox"} == {i["client"] for i in items}
    assert all("city" not in i and "location" not in i for i in items)


def test_unknown_user_agent_is_admitted_not_invented():
    assert describe_client(None) == "未知设备" and describe_client("curl/8") == "未知设备"


# ---------------------------------------------------------------- CSRF
def test_csrf_requires_the_header_to_echo_the_cookie():
    assert csrf_ok("abc", "abc") and not csrf_ok("abc", "abd")
    assert not csrf_ok(None, "abc") and not csrf_ok("abc", None) and not csrf_ok("", "")


# ---------------------------------------------------------------- memberships
def test_last_owner_cannot_be_removed_or_demoted(ident):
    a = ident.create_user("a@example.com", LONG_OK, status="active")
    ws = ident.workspaces_for(a["id"])[0]
    with pytest.raises(ServiceError) as e:
        ident.remove_member(ws["id"], a["id"])
    assert e.value.code == "last_owner"
    with pytest.raises(ServiceError):
        ident.change_role(ws["id"], a["id"], "editor")


def test_owner_can_be_demoted_once_a_second_owner_exists(ident):
    a = ident.create_user("a@example.com", LONG_OK, status="active")
    b = ident.create_user("b@example.com", LONG_OK, status="active")
    ws = ident.workspaces_for(a["id"])[0]
    ident.add_member(ws["id"], b["id"], "owner")
    assert ident.change_role(ws["id"], a["id"], "editor")["role"] == "editor"


def test_viewer_gets_no_export_permission_by_default(ident):
    a = ident.create_user("a@example.com", LONG_OK, status="active")
    b = ident.create_user("b@example.com", LONG_OK, status="active")
    ws = ident.workspaces_for(a["id"])[0]
    assert ident.add_member(ws["id"], b["id"], "viewer")["can_export"] == 0
    assert ident.add_member(ws["id"], b["id"], "editor")["can_export"] == 1


def test_last_platform_admin_cannot_be_demoted(ident):
    a = ident.create_user("a@example.com", LONG_OK, status="active", platform_role="admin")
    with pytest.raises(ServiceError) as e:
        ident.set_platform_role(a["id"], "none")
    assert e.value.code == "last_admin"


# ---------------------------------------------------------------- bootstrap
def test_bootstrap_token_is_single_use_and_supersedes_the_previous_one(ident):
    first = ident.create_bootstrap_token()
    second = ident.create_bootstrap_token()
    assert ident.consume_bootstrap_token(first, user_id="u1") is False, "旧令牌必须失效"
    assert ident.consume_bootstrap_token(second, user_id="u1") is True
    assert ident.consume_bootstrap_token(second, user_id="u2") is False, "令牌只能用一次"


def test_expired_bootstrap_token_is_refused(ident):
    raw = ident.create_bootstrap_token(ttl_s=-1)
    assert ident.consume_bootstrap_token(raw, user_id="u1") is False


def test_bootstrap_is_needed_until_an_active_admin_exists(ident):
    assert ident.bootstrap_needed()
    ident.create_user("root@example.com", LONG_OK, status="active", platform_role="admin")
    assert not ident.bootstrap_needed()
