"""Tables of the business applications on the workplace origin (``synwevo_suite``).

`synwevo_core.store.Store` is the only place that runs schema migrations (docs/architecture/
monorepo.md §3), so the applications' tables are declared here, one module per application, and
applied by the store on every start. Everything is additive (`CREATE … IF NOT EXISTS`, guarded
`ADD COLUMN`): older code ignores these tables, so a rollback needs no data restore.

Each module may define ``SCHEMA`` (SQL script), ``COLUMNS`` (``(table, column, declaration)``
added when missing), ``INDEXES`` (``(name, table, columns)``) and ``rebuild(c)`` (an idempotent
table rebuild run outside any transaction, for the one change ``ALTER TABLE`` cannot make).
"""
from __future__ import annotations

import importlib

#: One module per application, applied in this order; ``runs`` (the capability layer's run records
#: and event outbox) first.
MODULES = ("runs", "forms", "tables", "dataprep", "approvals", "extract", "agent", "agent_files", "flows", "billing", "model_gateway", "model_prices", "model_quotes", "model_cost_admission", "model_cost_policy", "model_provider", "payer_budgets", "model_consents", "model_traffic", "model_risk", "webhooks", "payments", "org_billing", "wallet_usage", "agent_safety")


def apply(c) -> None:
    for name in MODULES:
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"{__name__}.{name}":
                continue                     # that application has no tables yet
            raise
        schema = getattr(mod, "SCHEMA", None)
        if schema:
            c.executescript(schema)
        rebuild = getattr(mod, "rebuild", None)
        if rebuild is not None:
            # A table rebuild (widening a CHECK) owns its transaction: SQLite only lets foreign
            # keys be switched off outside one (synwevo_core.schema_widen).
            rebuild(c)
        columns = getattr(mod, "COLUMNS", ())
        indexes = getattr(mod, "INDEXES", ())
        migrate = getattr(mod, "migrate", None)
        if not columns and not indexes and migrate is None:
            continue
        c.execute("BEGIN IMMEDIATE")
        try:
            for table, col, decl in columns:
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            for iname, table, cols in indexes:
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
                if set(cols) <= have:
                    c.execute(f"CREATE INDEX IF NOT EXISTS {iname} ON {table}({', '.join(cols)})")
            if migrate is not None:
                migrate(c)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
