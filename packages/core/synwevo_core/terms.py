"""The published terms documents and which version each person accepted at their first paid use.

The public site carries three versioned documents (apps/portal, docs/platform/public-pages.md):
the terms of service, the AI use rules and the pricing page. Their versions and effective date are
these constants; the pages print the same values and a test keeps them equal. A person's first
paid Agent run (the run whose price and maximum budget they confirmed) records the versions in
force; a new version is recorded again at the next paid run after it takes effect.
"""
from __future__ import annotations

from .store import Store, now

#: document id (public page) → (version, effective date). Change a page's wording → new version.
DOCUMENTS: dict[str, tuple[str, str]] = {
    "terms": ("2026-10-05", "2026-10-05"),
    "ai-use": ("2026-10-05", "2026-10-05"),
    "pricing": ("2026-10-05", "2026-10-05"),
}


class TermsAcceptances:
    def __init__(self, store: Store):
        self.store = store

    def record_paid_use(self, user_id: str, *, context: str) -> list[str]:
        """Record the current version of each document unless already recorded; returns the
        documents newly recorded. Called inside the paid run's admission transaction."""
        added = []
        for document, (version, _) in DOCUMENTS.items():
            if self.store.execute("INSERT OR IGNORE INTO agent_terms_acceptances (user_id,document,version,accepted_at,context) "
                                  "VALUES (?,?,?,?,?)", (user_id, document, version, now(), context)):
                added.append(document)
        return added

    def history(self, user_id: str) -> list[dict]:
        return self.store.all("SELECT document, version, accepted_at, context FROM agent_terms_acceptances "
                              "WHERE user_id=? ORDER BY accepted_at, rowid", (user_id,))
