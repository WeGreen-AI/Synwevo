"""A person's personal-Agent records in their account export, and what account deletion erases.

The account service exports and deletes accounts; the Agent's tables belong to the workplace
origin. Both live in the one database (`Store` applies the suite schema everywhere), so the
account service reads and erases them here without importing the Agent.

Export — what the person can see of their own Agent, as data:
conversations with the visible text of each message (never the provider's private replay,
reasoning, tool inputs or results), the operations it ran (name, workspace, state), memories,
settings (whether a personal key is set, never the key or its hint), the model-provider consent
and its history, payment quotes and continuing payment consents (fixed terms, no prices of cost),
the reports the person filed, and the personal wallet statement.

Deletion — at the end of the grace period, and again whenever the security journal replays the
deletion after a restore: conversations, messages, turns, events, tool calls, memories, settings
(including the sealed personal key) and the content of reports are erased. Financial records —
quotes, payment consents, the ledger, usage and settlement evidence — are kept under the account's
permanent, now nameless id, because they are the platform's accounts; consent and safety records
carry no content and are kept the same way. Unused wallet balance is not paid out: top-ups are
final (the deletion dialog says so before the person confirms).
"""
from __future__ import annotations

import json

from .store import Store

#: Tables erased with an account, each keyed by ``user_id`` (order: dependants first).
ERASED = ("agent_events", "agent_tool_calls", "agent_turns", "agent_messages", "agent_conversations",
          "agent_memories", "agent_settings")


def _table(store: Store, name: str) -> bool:
    return bool(store.one("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)))


def _terms(raw: str | None) -> dict:
    try:
        terms = json.loads(raw or "{}")
    except ValueError:
        return {}
    route, sale = terms.get("route") or {}, terms.get("sale") or {}
    return {"model": route.get("requested_model"), "price_id": sale.get("price_id"), "unit": sale.get("unit"),
            "budget": None if terms.get("budget") is None else str(terms["budget"]),
            "max_calls": terms.get("max_calls")}


def export(store: Store, user_id: str) -> dict:
    """The person's Agent records (see the module docstring for what is and is not included)."""
    if not _table(store, "agent_conversations"):
        return {}
    conversations = []
    for conv in store.all("SELECT id,title,status,created_at,updated_at FROM agent_conversations WHERE user_id=? "
                          "ORDER BY created_at", (user_id,)):
        messages = store.all("SELECT id,parent_id,role,status,body,error_code,created_at FROM agent_messages "
                             "WHERE conversation_id=? AND user_id=? ORDER BY created_at, rowid", (conv["id"], user_id))
        operations = store.all("SELECT turn_id,tool,operation,workspace_id,status,created_at FROM agent_tool_calls "
                               "WHERE conversation_id=? AND user_id=? ORDER BY created_at, rowid", (conv["id"], user_id))
        conversations.append({**conv, "messages": messages, "operations": operations})
    settings = store.one("SELECT effort,memory_enabled,prefer_personal_key,personal_key_sealed IS NOT NULL AS personal_key_set,"
                         "updated_at FROM agent_settings WHERE user_id=?", (user_id,))
    out = {
        "notice": "AI-generated answers may be inaccurate; they are provided for reference only.",
        "conversations": conversations,
        "memories": store.all("SELECT body,created_at,updated_at FROM agent_memories WHERE user_id=? ORDER BY created_at", (user_id,)),
        "settings": {**settings, "personal_key_set": bool(settings["personal_key_set"])} if settings else None,
    }
    if _table(store, "agent_provider_consents"):
        out["provider_consents"] = store.all("SELECT provider,version,accepted_at,withdrawn_at FROM agent_provider_consents "
                                             "WHERE user_id=?", (user_id,))
        out["provider_consent_history"] = store.all("SELECT provider,version,action,at FROM agent_provider_consent_log "
                                                    "WHERE user_id=? ORDER BY at, rowid", (user_id,))
        out["reports"] = store.all("SELECT conversation_id,message_id,category,note,excerpt,status,created_at,resolved_at "
                                   "FROM agent_message_reports WHERE user_id=? ORDER BY created_at", (user_id,))
    if _table(store, "agent_terms_acceptances"):
        out["terms_accepted"] = store.all("SELECT document,version,accepted_at,context FROM agent_terms_acceptances "
                                          "WHERE user_id=? ORDER BY accepted_at, rowid", (user_id,))
    if _table(store, "agent_model_quotes"):
        out["quotes"] = [{**{k: q[k] for k in ("id", "environment", "conversation_id", "created_at", "expires_at",
                                                 "accepted_turn_id", "revoked_at")}, **_terms(q["terms_json"])}
                         for q in store.all("SELECT * FROM agent_model_quotes WHERE user_id=? ORDER BY created_at", (user_id,))]
    if _table(store, "agent_model_consents"):
        out["payment_consents"] = [{**{k: c[k] for k in ("id", "environment", "conversation_id", "max_runs", "created_at",
                                                          "expires_at", "revoked_at")}, **_terms(c["terms_json"])}
                                   for c in store.all("SELECT * FROM agent_model_consents WHERE user_id=? ORDER BY created_at",
                                                      (user_id,))]
    if _table(store, "billing_journal"):
        # The personal wallet's own lines: amount in the unit's smallest step, positive = credited.
        out["wallet_statement"] = store.all(
            "SELECT j.environment, j.unit, j.kind, j.created_at, "
            "CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END AS amount "
            "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account, j.credit_account) "
            "WHERE a.kind='wallet' AND a.owner=? ORDER BY j.created_at, j.rowid", (user_id,))
    return out


def erase(store: Store, user_id: str) -> int:
    """Erase the account's Agent content (idempotent; callers hold a write transaction)."""
    if not _table(store, "agent_conversations"):
        return 0
    n = 0
    for table in ERASED:
        n += store.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    if _table(store, "agent_message_reports"):
        # The report stays as a moderation record; what the person wrote or quoted in it goes.
        n += store.execute("UPDATE agent_message_reports SET note=NULL, excerpt=NULL WHERE user_id=? "
                           "AND (note IS NOT NULL OR excerpt IS NOT NULL)", (user_id,))
    return n
