"""Which sessions may exercise platform administration (sessions.admin_capable).

Every existing administrator path stays admin-capable: password alone, a verified passkey, any
password + second factor pair. The newer single-factor paths are not, and become so only when a
second, distinct factor is proved.
"""
import pytest

from synwevo_core.sessions import admin_capable, factor_kinds


@pytest.mark.parametrize("amr, capable", [
    ("password", True), ("password+totp", True), ("password+recovery", True), ("passkey_uv", True),
    ("email+totp", True), ("email+password", True), ("idp:google+totp", True), ("idp:google+password", True),
    ("email", False), ("idp:google", False), ("idp:microsoft", False), ("passkey", False), ("", False),
    ("email+email", False), ("idp:google+idp:microsoft", False),
])
def test_admin_capability_by_authentication_method(amr, capable):
    assert admin_capable(amr) is capable


def test_factor_kinds_collapse_providers_and_split_verified_passkeys():
    assert factor_kinds("idp:google+totp") == {"idp", "totp"}
    assert factor_kinds("passkey_uv") == {"passkey", "user_verification"}
    assert factor_kinds("password+password") == {"password"}
