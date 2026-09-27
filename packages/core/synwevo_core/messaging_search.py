"""Full-text search over message bodies: an SQLite FTS5 external-content index (CI-74).

Decision record (docs/platform/messaging.md §7.7). The index is *external content*: the FTS
table stores no copy of the text, it reads `messages.body` through the row's rowid when it needs
it (snippets, rebuilds), so a recall or an edit changes one place and the index follows through
three triggers. What the index knows about a row is only the tokens of a **text** message; system
rows (JSON) are never indexed, and a blanked body (recall, removal, moderation) replaces the
tokens with nothing — the "search index is cleared" rule is the UPDATE trigger, not a second
code path someone can forget.

Tokenizer: `trigram` when this SQLite has it (≥ 3.34; every deployment image does — Debian
trixie ships 3.46), else `unicode61`. Trigram gives substring search that works for Chinese
(unicode61 treats a run of CJK characters as one token, so 「周报」 would not find 「交周报」);
its price is that a query shorter than three characters cannot match — those fall back to the
bounded LIKE the search always had. Which engine and tokenizer a process runs with is read from
`sqlite_master`, never assumed, and reported in `capabilities.search` so the shell and the tests
see the same answer.

The FTS5 module can be absent from a Python build. Then nothing is created, `describe()` says
`engine: like`, and search keeps working the way it did before this module existed. Scope
(memberships, `joined_seq`, hidden-for-me rows) is applied by the caller's JOINs on the messages
row the index points at; the index itself is not a permission boundary.

Rowids: the index is keyed by `messages.rowid`. The table has a TEXT primary key, so its rowid is
implicit and would be renumbered by `VACUUM`; this platform never runs `VACUUM` (backups use the
online backup API, which preserves rowids), and a rebuild (`rebuild()`) restores the index from
the content table if that ever changes.
"""
from __future__ import annotations

import sqlite3

TABLE = "messages_fts"
#: Marks `snippet()` puts around a match; turned into offsets by `split_snippet` so the shell
#: never has to parse markup out of user text.
_OPEN, _CLOSE = "", ""
#: Shortest query the trigram tokenizer can match; shorter ones use LIKE.
TRIGRAM_MIN_CHARS = 3
#: unicode61 tokens are whole words: two characters is the same floor the LIKE search had.
UNICODE61_MIN_CHARS = 2

_TRIGGERS = f"""
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages WHEN new.kind='text' BEGIN
  INSERT INTO {TABLE}(rowid, body) VALUES (new.rowid, new.body);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages WHEN old.kind='text' BEGIN
  INSERT INTO {TABLE}({TABLE}, rowid, body) VALUES ('delete', old.rowid, old.body);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF body ON messages WHEN old.kind='text' BEGIN
  INSERT INTO {TABLE}({TABLE}, rowid, body) VALUES ('delete', old.rowid, old.body);
  INSERT INTO {TABLE}(rowid, body) VALUES (new.rowid, new.body);
END;
"""


def fts5_tokenizer_available(conn: sqlite3.Connection | None = None) -> str | None:
    """`trigram`, `unicode61`, or None when FTS5 is not compiled into this SQLite. Probed on a
    scratch in-memory database so the answer never depends on what a data directory holds."""
    probe = sqlite3.connect(":memory:")
    try:
        for tok in ("trigram", "unicode61"):
            try:
                probe.execute(f"CREATE VIRTUAL TABLE t_{tok} USING fts5(body, tokenize='{tok}')")
                return tok
            except sqlite3.OperationalError:
                continue
        return None
    finally:
        probe.close()


def ensure_index(c: sqlite3.Connection) -> dict:
    """Create the index and its triggers when they are missing and backfill it from the content
    table once. Idempotent; called by `Store._migrate` on every open. Returns `describe(c)`."""
    existing = _existing(c)
    if existing:
        c.executescript(_TRIGGERS)          # triggers are IF NOT EXISTS; an older index keeps its tokenizer
        return existing
    tok = fts5_tokenizer_available()
    if tok is None:
        return {"engine": "like", "tokenizer": None, "min_chars": UNICODE61_MIN_CHARS}
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5(body, content='messages', content_rowid='rowid', tokenize='{tok}')")
    c.executescript(_TRIGGERS)
    # Backfill: every text row that exists now. The rebuild reads the content table, so rows
    # blanked before this index existed index as empty, like the ones blanked after.
    c.execute(f"INSERT INTO {TABLE}({TABLE}) VALUES ('rebuild')")
    return describe(c)


def rebuild(c: sqlite3.Connection) -> bool:
    """Re-read the whole content table into the index (after a restore, or if rowids moved)."""
    if not _existing(c):
        return False
    c.execute(f"INSERT INTO {TABLE}({TABLE}) VALUES ('rebuild')")
    return True


def _existing(c: sqlite3.Connection) -> dict | None:
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    if not row:
        return None
    sql = row[0] or ""
    tok = "trigram" if "tokenize='trigram'" in sql else "unicode61"
    return {"engine": "fts5", "tokenizer": tok, "min_chars": TRIGRAM_MIN_CHARS if tok == "trigram" else UNICODE61_MIN_CHARS}


def describe(c: sqlite3.Connection) -> dict:
    """What this database searches with: `{engine: fts5|like, tokenizer, min_chars}`."""
    return _existing(c) or {"engine": "like", "tokenizer": None, "min_chars": UNICODE61_MIN_CHARS}


def usable_for(info: dict, q: str) -> bool:
    """Whether the index can answer `q`, or the caller should use LIKE: too short for the
    tokenizer, or CJK under unicode61 (whole-run tokens cannot match a substring)."""
    if info.get("engine") != "fts5":
        return False
    if len(q) < int(info.get("min_chars") or TRIGRAM_MIN_CHARS):
        return False
    if info.get("tokenizer") == "unicode61" and any(_is_cjk(ch) for ch in q):
        return False
    return True


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF


def match_expression(q: str) -> str:
    """The user's text as one FTS5 *phrase*. Quoting it makes every character literal — `*`,
    `NEAR`, `AND`, parentheses, Chinese punctuation — so nothing a person types can become an
    operator or a syntax error; a double quote inside is doubled per the FTS5 grammar."""
    return '"' + q.replace('"', '""') + '"'


def snippet_sql(tokens: int = 16) -> str:
    """The `snippet()` call to select alongside the message row (column 0 is `body`)."""
    return f"snippet({TABLE}, 0, '{_OPEN}', '{_CLOSE}', '…', {int(tokens)})"


def split_snippet(marked: str | None) -> tuple[str, list[list[int]]]:
    """Turn a marked snippet into `(text, [[start, end], …])` with offsets in code points of
    `text`, so the shell highlights ranges instead of interpreting markup inside user content."""
    if not marked:
        return "", []
    out, ranges, start = [], [], None
    pos = 0
    for ch in marked:
        if ch == _OPEN:
            start = pos
        elif ch == _CLOSE:
            if start is not None and pos > start:
                ranges.append([start, pos])
            start = None
        else:
            out.append(ch)
            pos += 1
    return "".join(out), ranges
