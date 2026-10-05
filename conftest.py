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

import functools
import os
from pathlib import Path
import shutil

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


def pytest_addoption(parser):
    parser.addoption("--ci-clean-passed-tmp", action="store_true",
                     help="Ephemeral CI only: remove owned tmp_path directories after all test phases pass")


def pytest_configure(config):
    if config.getoption("ci_clean_passed_tmp"):
        if os.environ.get("CI") != "true" or config.getini("tmp_path_retention_policy") != "all":
            raise pytest.UsageError("--ci-clean-passed-tmp requires CI=true and tmp_path_retention_policy=all")
        config.stash[_CI_TMP_REMOVED] = 0
    config.addinivalue_line("markers", "production_password_hash: use the deployed Argon2 parameters "
                            "(tests of the parameters themselves, rehash upgrades and hash timing)")
    config.addinivalue_line("markers", "fresh_store_schema: build a new database from nothing instead "
                            "of starting from the session's migrated template")


_CI_TMP_PATH = pytest.StashKey[tuple[Path, Path]]()
_CI_TMP_PHASES = pytest.StashKey[dict[str, bool]]()
_CI_TMP_REMOVED = pytest.StashKey[int]()


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    path = yield
    if (request.config.getoption("ci_clean_passed_tmp") and fixturedef.argname == "tmp_path"
            and fixturedef.func.__module__ == "_pytest.tmpdir"):
        # Only pytest's own function-scoped allocation qualifies, never an overridden fixture
        # or an inferred directory name. Session templates and other tmpdirs stay untouched.
        base = request.getfixturevalue("tmp_path_factory").getbasetemp()
        request.node.stash[_CI_TMP_PATH] = (path, base)
    return path


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if item.config.getoption("ci_clean_passed_tmp") and _CI_TMP_PATH in item.stash:
        phases = item.stash.setdefault(_CI_TMP_PHASES, {})
        phases[report.when] = report.passed and not getattr(report, "wasxfail", False)
        if report.when == "teardown":
            path, base = item.stash[_CI_TMP_PATH]
            del item.stash[_CI_TMP_PATH]
            del item.stash[_CI_TMP_PHASES]
            # This report follows ALL fixture finalizers, including release_factory_resources.
            # Built-in retention_policy=failed runs too early and checks only the call phase.
            if (phases == {"setup": True, "call": True, "teardown": True}
                    and path.parent == base and not base.is_symlink() and not path.is_symlink()
                    and path.is_dir() and path.resolve().parent == base):
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    item.warn(pytest.PytestWarning(f"CI tmp_path cleanup failed: {exc}"))
                else:
                    item.config.stash[_CI_TMP_REMOVED] += 1
                    if item.config.stash[_CI_TMP_REMOVED] % 250 == 0:
                        _ci_tmp_diagnostic(item.config)
    return report


def _ci_tmp_diagnostic(config):
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter:
        base = config._tmp_path_factory.getbasetemp()
        reporter.write_line(f"CI tmp_path: removed={config.stash[_CI_TMP_REMOVED]}, "
                            f"disk_free_MiB={shutil.disk_usage(base).free // (1024 * 1024)}, base={base}")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if config.getoption("ci_clean_passed_tmp"):
        _ci_tmp_diagnostic(config)


def pytest_collection_finish(session):
    """Exempt what collection built from every later full collection.

    Imported modules, test functions and their parameters live for the whole session, yet each
    `gc.collect()` in the per-test cleanup below traversed all of them again: about 150 ms per
    test in a full run. Frozen objects are still freed by reference counting; only the cycle
    collector skips them. Everything created by a test stays collectable exactly as before.
    """
    import gc

    gc.collect()
    gc.freeze()


@pytest.fixture(scope="session", autouse=True)
def _migrated_store_template(tmp_path_factory):
    """Start each new test database from one migrated copy built for the session.

    Creating the schema from nothing runs ~70 additive ALTERs, each of which makes SQLite reparse
    a ~700-object schema: ~75-120 ms for every one of several thousand test databases. The
    template keeps only `schema_version` in `meta`, so the normal constructor still runs on the
    copy and performs every first-open step itself (instance stamping from the environment, the
    one-time markers); only the column additions it would find already present are skipped.
    Existing databases, read-only opens and tests marked fresh_store_schema are untouched.
    """
    import shutil
    import sqlite3

    from synwevo_core import store as store_module

    built = tmp_path_factory.mktemp("migrated-store-template")
    with store_module.Store(built).tx() as t:
        t.execute("DELETE FROM meta WHERE key<>'schema_version'")
    template = built / "template.sqlite3"
    source, target = sqlite3.connect(built / "docweave.sqlite3"), sqlite3.connect(template)
    try:
        source.backup(target)  # a consistent single file, WAL contents included
    finally:
        source.close()
        target.close()

    original = store_module.Store.__init__

    def from_template(self, root, migrate=True):
        db = Path(root) / "docweave.sqlite3"
        if migrate and not _FRESH_STORE_SCHEMA[0] and not db.exists():
            db.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, db)
        original(self, root, migrate)

    store_module.Store.__init__ = from_template
    try:
        yield
    finally:
        store_module.Store.__init__ = original


_FRESH_STORE_SCHEMA = [False]


@pytest.fixture(autouse=True)
def _fresh_store_schema(request):
    _FRESH_STORE_SCHEMA[0] = request.node.get_closest_marker("fresh_store_schema") is not None
    yield
    _FRESH_STORE_SCHEMA[0] = False


@pytest.fixture(autouse=True)
def _test_password_hasher(request, monkeypatch):
    """Argon2 at the deployed cost (46 MiB, t=2) is ~45-100 ms per hash and verification; fixtures
    create several accounts each, which made hashing about a fifth of a full run. Tests use the
    same algorithm and code path with test-only parameters; PHC strings carry their parameters,
    so hashes made either way still verify. Tests about the parameters, rehash upgrades and hash
    timing keep the real hasher with @pytest.mark.production_password_hash."""
    if request.node.get_closest_marker("production_password_hash"):
        return
    from synwevo_core import passwords

    monkeypatch.setattr(passwords, "_hasher", _test_hasher())


@functools.cache
def _test_hasher():
    from argon2 import PasswordHasher
    from argon2.low_level import Type

    return PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, hash_len=32, salt_len=16,
                          type=Type.ID)


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
