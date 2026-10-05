"""The risk tables arrive through Store's additive migration and keep their history immutable."""
import sqlite3

import pytest

from synwevo_core.billing.risk import RiskControls
from synwevo_core.identity import Identity
from synwevo_core.store import Store

TABLES = ("model_risk_suspensions", "model_risk_breaker_events", "model_risk_allowlist", "model_risk_balance_readings",
          "model_risk_upstream_topups", "model_risk_reconciliations", "model_risk_jobs")


def test_an_older_database_gains_the_risk_tables_on_open_and_keeps_its_data(tmp_path):
    root = tmp_path / "isolated-older-database"
    store = Store(root)
    uid = Identity(store).create_user("older@example.test", None, status="active", with_personal_workspace=False)["id"]
    with store.conn() as c:  # Simulate the previous release's schema: none of the risk tables exist.
        for table in TABLES:
            c.execute(f"DROP TABLE {table}")
    reopened = Store(root)
    have = {r["name"] for r in reopened.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= have
    assert reopened.one("SELECT id FROM users WHERE id=?", (uid,))["id"] == uid
    Store(root)  # Idempotent on every later open.


def test_suspension_lifts_once_and_history_cannot_be_rewritten(tmp_path):
    store = Store(tmp_path / "isolated-risk-schema")
    uid = Identity(store).create_user("schema@example.test", None, status="active", with_personal_workspace=False)["id"]
    controls = RiskControls(store, environment="test", clock=lambda: 2_000_000_000)
    row = controls.record_trip({"scope_kind": "route", "scope_id": "fixture-route", "reason_code": "route_velocity",
                                "unit": "FIXTURE", "window_s": 60, "threshold_units": 1, "observed_units": 2})
    for sql in ("UPDATE model_risk_suspensions SET observed_units=1", "DELETE FROM model_risk_suspensions"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable_risk_suspension"):
            store.execute(sql)
    controls.lift(row["id"], actor_id=uid, reason="reviewed fixture route spend")
    with pytest.raises(sqlite3.IntegrityError, match="immutable_risk_suspension"):
        store.execute("UPDATE model_risk_suspensions SET lifted_at=NULL, lifted_by=NULL, lift_reason=NULL")
    event = controls.open_breaker(source="admin", reason_code="admin_opened", actor_id=uid, reason="fixture kill switch")
    for sql in ("UPDATE model_risk_breaker_events SET state='closed'", "DELETE FROM model_risk_breaker_events"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable_risk_breaker"):
            store.execute(sql)
    assert controls.breaker()["id"] == event["id"]
