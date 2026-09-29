"""The corpus checked in for the browser's live-table model (web/tests/fixtures/sheet_live_corpus.json)
is what the server's model produces today. Changing collab_sheet.py fails this until the corpus is
regenerated (``.venv/bin/python apps/docs/tests/sheet_live_corpus.py --write``); the node test
``sheetmodel.test.ts`` then says whether the browser model still agrees (docs-sheet-live.md §6)."""
from __future__ import annotations

from .sheet_live_corpus import CORPUS_PATH, build, dump


def test_the_checked_in_corpus_is_what_the_server_model_does():
    assert CORPUS_PATH.read_text(encoding="utf-8") == dump(build()), (
        "the server model changed: regenerate with .venv/bin/python apps/docs/tests/sheet_live_corpus.py --write "
        "and run the Docs web tests (sheetmodel.test.ts)")


def test_the_corpus_is_deterministic():
    assert dump(build()) == dump(build())
