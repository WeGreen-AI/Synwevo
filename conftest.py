"""Repository-wide pytest configuration.

`testpaths` in pyproject.toml lists four suites with distinct module boundaries, and this file is
what makes running them together mean the same thing as running each on its own: the packages
are installed (editable) rather than reached by inserting directories into `sys.path`, so a test
that accidentally imports across a module boundary fails here exactly as it would in production.

It also makes the suites **hermetic**: the developer's shell does not leak into them. A shell with
`SYNWEVO_UI_AUTH=session` exported made every workbench test answer 401; a set
`SYNWEVO_DOCS_IMPORT_ROOTS` flipped an expected 400; `SYNWEVO_DATA_DIR` or `SYNWEVO_SECRET` point
at real things. Several of these are read once, when a module is imported — which happens while
tests are being *collected*, before any fixture could run — so they are removed here, when this
file is imported, which is before any test module is. Tests that need one set it themselves with
`monkeypatch.setenv`. Only settings that locate local tools are kept, because a test cannot
supply "where LibreOffice is installed on this machine".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Suffixes after SYNWEVO_DOCS_ / SYNWEVO_ / DOCWEAVE_ that may pass through: tool locations only.
TOOL_SETTINGS = frozenset({"SOFFICE", "TESSERACT", "TESSDATA", "CJK_FONTS", "CJK_FONT_FILE",
                           "LO_PROFILE"})
# ... and the controls of this test run itself, which no product code reads: CI says the real-room
# client tests must run (`required`: fail instead of skipping without Node) and how many seeds the
# random ones take. Scrubbed, `required` silently turned into a skip.
TEST_RUN_SETTINGS = frozenset({"SYNWEVO_SHEET_CLIENTS", "SYNWEVO_SHEET_FUZZ_SEEDS"})


def _scrub_ambient_configuration() -> list[str]:
    removed = []
    for key in list(os.environ):
        if key in TEST_RUN_SETTINGS:
            continue
        for prefix in ("SYNWEVO_DOCS_", "SYNWEVO_", "DOCWEAVE_"):
            if key.startswith(prefix):
                if key[len(prefix):] not in TOOL_SETTINGS:
                    removed.append(key)
                    del os.environ[key]
                break
    return removed


# Before importing anything from the repository: synwevo_core reads some settings at import time.
SCRUBBED = _scrub_ambient_configuration()

from synwevo_core.http import repo_root  # noqa: E402

ROOT = repo_root() or Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def platform_root() -> Path:
    return ROOT


def _release_factory_resources() -> None:
    """Release finished test applications retained by FastAPI 0.141.1's pure caches.

    Its three callable-classification LRUs strongly retain up to 4096 route closures each.
    Our factory routes close over services and sometimes the entire application: even a
    completed TestClient lifespan can therefore retain its Store and route graph. Only
    pytest clears these recomputable classifications; production caching is unchanged.
    """
    import gc

    from fastapi.dependencies.models import (
        _is_async_gen_callable_cached, _is_coroutine_callable_cached, _is_gen_callable_cached,
    )
    from synwevo_core.workers import stop_all

    left = stop_all(wait=3.0)
    _is_gen_callable_cached.cache_clear()
    _is_async_gen_callable_cached.cache_clear()
    _is_coroutine_callable_cached.cache_clear()
    gc.collect()
    assert not left, f"Background workers outlived test teardown: {left}"


@pytest.fixture(autouse=True)
def release_factory_resources():
    # Root fixtures finish after suite-local fixtures. stop_all is also safe here when a
    # test has no suite-specific cleanup; expose this same cleanup for lifecycle regressions.
    yield _release_factory_resources
    _release_factory_resources()
