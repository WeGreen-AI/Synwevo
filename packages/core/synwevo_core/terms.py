"""The published terms documents and which version each person accepted.

The public site carries three versioned documents (apps/portal, docs/platform/public-pages.md):
the terms of service, the AI use rules and the pricing page. Their versions and effective date are
these constants; the pages print the same values and a test keeps them equal.

A person accepts them once, before their first Agent run, in the Agent's one-time "start using"
step (``accept``, recorded with the step's operation run as context); a new version of a document
is asked for once more before the next run (``missing``). A send from the page needs that
acceptance beforehand, whatever authorization it carries (automatic, or a legacy quote or
continuing consent from an older page). Only a caller other than the page (a flow or an
automation) starting a paid run on the legacy quote path still records the versions in force at
that run (``record_paid_use``). A send that finds a document unaccepted is refused before
anything is stored, and the acceptance rows are kept with the account's other consent records.
"""
from __future__ import annotations

from .store import Store, now

#: document id (public page) → (version, effective date). Change a page's wording → new version.
DOCUMENTS: dict[str, tuple[str, str]] = {
    "terms": ("2026-10-05", "2026-10-05"),
    "ai-use": ("2026-10-05", "2026-10-05"),
    "pricing": ("2026-10-05", "2026-10-05"),
}


def current_documents() -> list[dict]:
    """Every document at its version in force: ``[{id, version, effective}]``."""
    return [{"id": document, "version": version, "effective": effective}
            for document, (version, effective) in DOCUMENTS.items()]


class TermsVersionChanged(Exception):
    """An acceptance that does not name every document at its current version. Nothing was
    recorded; ``documents`` is the current list to show the person."""

    def __init__(self, documents: list[dict]):
        super().__init__("terms_version_changed")
        self.documents = documents


class TermsAcceptances:
    def __init__(self, store: Store):
        self.store = store

    def record_paid_use(self, user_id: str, *, context: str) -> list[str]:
        """Record the current version of each document unless already recorded; returns the
        documents newly recorded. Called inside the paid run's admission transaction when a
        caller other than the page (a flow or an automation) starts a run on the legacy quote
        path. A send from the page never records here: it needs ``missing() == []`` beforehand."""
        added = []
        for document, (version, _) in DOCUMENTS.items():
            if self.store.execute("INSERT OR IGNORE INTO agent_terms_acceptances (user_id,document,version,accepted_at,context) "
                                  "VALUES (?,?,?,?,?)", (user_id, document, version, now(), context)):
                added.append(document)
        return added

    def accepted(self, user_id: str) -> dict[str, bool]:
        """document id → whether the person accepted its version in force."""
        rows = self.store.all("SELECT document, version FROM agent_terms_acceptances WHERE user_id=?", (user_id,))
        have = {(r["document"], r["version"]) for r in rows}
        return {document: (document, version) in have for document, (version, _) in DOCUMENTS.items()}

    def missing(self, user_id: str) -> list[dict]:
        """The documents whose version in force the person has not accepted: ``[{id, version,
        effective}]``, empty when a paid send from the page may start (automatic or legacy)."""
        accepted = self.accepted(user_id)
        return [d for d in current_documents() if not accepted[d["id"]]]

    def accept(self, user_id: str, versions: dict[str, str], *, context: str) -> list[str]:
        """Record the person's explicit acceptance of every document at its version in force.

        ``versions`` is what the person was shown and ticked (document id → version). It must
        name every document at exactly its current version; otherwise nothing is recorded and
        :class:`TermsVersionChanged` carries the current list. Returns the documents newly
        recorded (a repeat at the same versions records nothing and returns ``[]``)."""
        expected = {document: version for document, (version, _) in DOCUMENTS.items()}
        if not isinstance(versions, dict) or versions != expected:
            raise TermsVersionChanged(current_documents())
        added = []
        with self.store.tx():
            for document, version in expected.items():
                if self.store.execute("INSERT OR IGNORE INTO agent_terms_acceptances (user_id,document,version,accepted_at,context) "
                                      "VALUES (?,?,?,?,?)", (user_id, document, version, now(), context)):
                    added.append(document)
        return added

    def history(self, user_id: str) -> list[dict]:
        return self.store.all("SELECT document, version, accepted_at, context FROM agent_terms_acceptances "
                              "WHERE user_id=? ORDER BY accepted_at, rowid", (user_id,))
