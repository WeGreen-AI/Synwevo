"""改品牌之后，旧的 `DOCWEAVE_*` 配置必须继续可用——并且在真正危险的地方必须拒绝猜。

这是本轮兼容面里唯一有外部使用者的一层：别人的部署脚本、systemd unit、CI secrets 里写的是旧名字。
模块名可以干净地改掉（没有外部调用方），配置不行。
"""
from __future__ import annotations

import pytest

from synwevo_core import env


@pytest.fixture(autouse=True)
def _fresh_warnings():
    env._warned.clear()
    yield
    env._warned.clear()


def test_the_new_name_is_used_when_only_it_is_set(monkeypatch):
    monkeypatch.delenv("DOCWEAVE_ISSUER", raising=False)
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    assert env.get("ISSUER") == "https://accounts.example.test"


def test_the_legacy_name_still_works(monkeypatch, capsys):
    """Someone's existing deployment keeps running after the rename."""
    monkeypatch.delenv("SYNWEVO_ISSUER", raising=False)
    monkeypatch.setenv("DOCWEAVE_ISSUER", "https://old.example.test")
    assert env.get("ISSUER") == "https://old.example.test"
    out = capsys.readouterr().out
    assert "DOCWEAVE_ISSUER" in out and "SYNWEVO_ISSUER" in out, "旧名字可用，但要说一次"


def test_the_notice_is_printed_once_not_on_every_read(monkeypatch, capsys):
    monkeypatch.delenv("SYNWEVO_MAIL_MODE", raising=False)
    monkeypatch.setenv("DOCWEAVE_MAIL_MODE", "file")
    for _ in range(5):
        env.get("MAIL_MODE")
    assert capsys.readouterr().out.count("DOCWEAVE_MAIL_MODE") == 1


def test_agreeing_values_are_not_a_conflict(monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", "/srv/same")
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", "/srv/same")
    assert env.get("DATA_DIR") == "/srv/same"


def test_a_conflict_on_the_data_directory_refuses_rather_than_guesses(monkeypatch):
    """Two different data directories is the one case where picking either is worse than stopping.

    A wrong guess does not fail: SQLite creates a database at the wrong path, the service starts,
    and it looks exactly like "the data is gone".
    """
    monkeypatch.setenv("SYNWEVO_DATA_DIR", "/srv/new")
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", "/srv/old")
    with pytest.raises(env.ConfigConflict) as e:
        env.get("DATA_DIR")
    assert "/srv/new" in str(e.value) and "/srv/old" in str(e.value)


@pytest.mark.parametrize("suffix", ["SECRET", "ISSUER", "RP_ID", "UI_AUTH", "IMPORT_ROOTS"])
def test_every_security_sensitive_key_refuses_a_conflict(monkeypatch, suffix):
    monkeypatch.setenv(f"SYNWEVO_{suffix}", "a")
    monkeypatch.setenv(f"DOCWEAVE_{suffix}", "b")
    with pytest.raises(env.ConfigConflict):
        env.get(suffix)


def test_an_ordinary_key_prefers_the_new_name_and_says_so(monkeypatch, capsys):
    monkeypatch.setenv("SYNWEVO_SESSION_IDLE_S", "60")
    monkeypatch.setenv("DOCWEAVE_SESSION_IDLE_S", "120")
    assert env.get("SESSION_IDLE_S") == "60"
    assert "不一致" in capsys.readouterr().out


def test_the_docs_scope_prefers_its_own_prefix(monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_PDF_ENGINE", "pdfium")
    monkeypatch.setenv("DOCWEAVE_PDF_ENGINE", "pymupdf")
    assert env.docs("PDF_ENGINE") == "pdfium"


def test_the_docs_scope_falls_back_to_the_legacy_name(monkeypatch):
    monkeypatch.delenv("SYNWEVO_DOCS_PDF_ENGINE", raising=False)
    monkeypatch.delenv("SYNWEVO_PDF_ENGINE", raising=False)
    monkeypatch.setenv("DOCWEAVE_PDF_ENGINE", "pdfium")
    assert env.docs("PDF_ENGINE") == "pdfium"


def test_a_platform_level_name_also_reaches_the_docs_scope(monkeypatch):
    """`SYNWEVO_DATA_DIR` is read inside the docs process too; both spellings must work there."""
    monkeypatch.delenv("SYNWEVO_DOCS_DATA_DIR", raising=False)
    monkeypatch.delenv("DOCWEAVE_DATA_DIR", raising=False)
    monkeypatch.setenv("SYNWEVO_DATA_DIR", "/srv/platform")
    assert env.docs("DATA_DIR") == "/srv/platform"


# ---------------------------------------------------------------- docs scope precedence
def test_a_leftover_legacy_value_cannot_override_the_platform_name_in_the_docs_scope(monkeypatch):
    """`docs()` compared the legacy name only with `SYNWEVO_DOCS_*`. With the platform spelling
    set and a stale legacy one left behind, the legacy value came back — for the import
    allow-list, that silently widened it to the whole filesystem."""
    monkeypatch.delenv("SYNWEVO_DOCS_IMPORT_ROOTS", raising=False)
    monkeypatch.setenv("SYNWEVO_IMPORT_ROOTS", "/srv/allowed")
    monkeypatch.setenv("DOCWEAVE_IMPORT_ROOTS", "/")
    with pytest.raises(env.ConfigConflict):
        env.docs("IMPORT_ROOTS")


def test_the_docs_scope_prefers_the_platform_name_over_the_legacy_one(monkeypatch, capsys):
    monkeypatch.delenv("SYNWEVO_DOCS_PDF_ENGINE", raising=False)
    monkeypatch.setenv("SYNWEVO_PDF_ENGINE", "pdfium")
    monkeypatch.setenv("DOCWEAVE_PDF_ENGINE", "pymupdf")
    assert env.docs("PDF_ENGINE") == "pdfium"
    assert "不一致" in capsys.readouterr().out


def test_the_docs_scope_wins_over_the_platform_name(monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_PDF_ENGINE", "pdfium")
    monkeypatch.setenv("SYNWEVO_PDF_ENGINE", "pymupdf")
    monkeypatch.delenv("DOCWEAVE_PDF_ENGINE", raising=False)
    assert env.docs("PDF_ENGINE") == "pdfium"


def test_agreeing_values_across_all_three_names_are_fine(monkeypatch):
    for name in ("SYNWEVO_DOCS_WEBHOOK_SECRET", "SYNWEVO_WEBHOOK_SECRET", "DOCWEAVE_WEBHOOK_SECRET"):
        monkeypatch.setenv(name, "same")
    assert env.docs("WEBHOOK_SECRET") == "same"
