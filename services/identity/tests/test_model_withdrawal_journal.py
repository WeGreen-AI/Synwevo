"""A restored backup must not bring back a withdrawn platform model key or a switched-off provider.

Isolated fixture prices, keys and accounts through the real admin routes (test_admin_model_provider
helpers); no model or payment request. A "restore" is simulated as the other restore tests do:
the database rows are put back the way an older backup holds them, then the journal is applied.
"""
from __future__ import annotations

import time

from synwevo_core import journal
from synwevo_core.billing.provider_settings import ProviderSettings

from .test_admin_model_provider import (  # noqa: F401 — fixtures
    DISABLED, KEY, REPLACEMENT, billing_environment, command, preview, provider_world, publish, world,
)


def _publish(w, revision, spec, credential):
    plan = preview(w, revision=revision, spec=spec, credential=credential)
    response = publish(w, command(plan, credential))
    assert response.status_code == 200, response.text
    return response.json()["state"]


def _entries(w, kind, since):
    return [e for e in journal.read(w["store"].root, since=since) if e["kind"] == kind]


def test_a_replaced_or_revoked_key_stays_withdrawn_after_a_restore(provider_world):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    assert _entries(w, "model_credential_withdrawn", started) == []          # nothing was withdrawn yet
    old_cipher = w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    _publish(w, 1, w["spec"], {"action": "replace", "api_key": KEY + "-rotated"})
    entries = _entries(w, "model_credential_withdrawn", started)
    assert [(e["environment"], e["credential_revision"]) for e in entries] == [("test", 1)]
    assert KEY not in journal.path(w["store"].root).read_text()
    # Restore a backup taken before the rotation: the old key's ciphertext is back, revision 1.
    w["store"].execute("UPDATE model_provider_credentials SET revision=1, secret_enc=?", (old_cipher,))
    counts = journal.apply(w["store"], entries)
    assert counts["model_credential_withdrawn"] == 1
    assert w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"] is None
    assert journal.apply(w["store"], entries)["model_credential_withdrawn"] == 0      # idempotent


def test_a_newer_key_in_the_restored_database_is_left_alone(provider_world):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    _publish(w, 1, w["spec"], {"action": "replace", "api_key": KEY + "-rotated"})
    entries = _entries(w, "model_credential_withdrawn", started)
    journal.apply(w["store"], entries)          # the database already holds revision 2: unchanged
    assert w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")["secret_enc"]


def test_a_switched_off_provider_stays_off_after_a_restore(provider_world):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    _publish(w, 1, DISABLED, {"action": "keep"})
    entries = _entries(w, "model_provider_disabled", started)
    assert len(entries) == 1
    settings = ProviderSettings(w["store"], environment="test")
    # A backup from before the switch-off: its newest version is the enabled one.
    w["store"].execute("DROP TRIGGER model_provider_no_delete")
    w["store"].execute("DELETE FROM model_provider_versions WHERE revision=2")
    assert settings.read()["spec"]["enabled"] is True
    assert journal.apply(w["store"], entries)["model_provider_disabled"] == 1
    state = settings.read()
    assert state["revision"] == 2 and state["spec"]["enabled"] is False and "Journal replay" in state["reason"]
    assert settings.resolve() is None
    assert journal.apply(w["store"], entries)["model_provider_disabled"] == 0         # once only
