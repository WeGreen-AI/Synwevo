"""CI-86: a refusal whose sentence carries a number or a name also carries it in `detail`.

The workbench words these refusals in the reader's language from `detail` (web/src/lib/refusals.ts):
a limit is a deployment setting and a file name is data, so a sentence that quotes them cannot be
matched by its text. `web/tests/refusals.test.ts` holds the presentation to the server's sentences;
this holds the server to sending what the presentation needs.
"""
from __future__ import annotations

import pytest

from synwevo_core.errors import ServiceError


def _docx_project(api, fixtures):
    pid = api.project("拒绝详情")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid, t


def test_upload_limits_name_the_file_and_the_limit(api, fixtures, monkeypatch):
    from synwevo_docs.engine.limits import LIMITS
    pid = api.project("上传上限")
    monkeypatch.setattr(LIMITS, "max_file_bytes", 1024 * 1024)
    r = api.c.post(f"/api/projects/{pid}/sources", files=[("files", ("大文件.xlsx", b"x" * (1024 * 1024 + 1)))])
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert r.json()["detail"] == {"filename": "大文件.xlsx", "limit_mb": 1}
    assert r.json()["message"] == "文件「大文件.xlsx」超过 1 MB 上限"


def test_a_template_in_use_names_itself_and_a_project(api, fixtures):
    pid, t = _docx_project(api, fixtures)
    r = api.c.delete(f"/api/templates/{t['id']}")
    assert r.status_code == 409 and r.json()["error"] == "template_in_use"
    d = r.json()["detail"]
    assert d["template"] == t["name"] and d["total"] == 1 and d["projects"][0]["id"] == pid


def test_an_unusable_template_format_is_named(api, fixtures):
    pid = api.project("模板格式")
    r = api.c.post("/api/templates/upload", files={"file": ("说明.txt", "纯文本不能作为模板".encode())}, data={"project_id": pid})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_template", r.text
    assert r.json()["detail"] == {"format": "TXT"}


def test_generation_refusals_carry_their_numbers(api, fixtures, monkeypatch):
    from synwevo_docs.engine.limits import LIMITS
    pid, _ = _docx_project(api, fixtures)
    # a format this template cannot produce: the format and the reason, separately
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["csv"]})
    assert r.status_code == 400 and r.json()["error"] == "format_unavailable"
    d = r.json()["detail"]
    assert d["format"] == "CSV" and d["reason"] and d["reason"] in r.json()["message"]
    # more samples than one job may take
    monkeypatch.setattr(LIMITS, "max_records_per_job", 1)
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]})
    assert r.status_code == 400 and r.json()["error"] == "too_many"
    assert r.json()["detail"] == {"records": 2, "limit": 1}
    monkeypatch.setattr(LIMITS, "max_records_per_job", 20000)
    # files still being parsed: how many, not only the first twenty names
    api.c.app_state.app.store.execute("UPDATE source_files SET status='parsing' WHERE project_id=?", (pid,))
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]})
    assert r.status_code == 409 and r.json()["error"] == "sources_not_ready"
    assert r.json()["detail"]["parsing_total"] == 1 and r.json()["detail"]["files"] == ["测试_客户100.xlsx"]


def test_ocr_of_every_scanned_page_does_not_ask_for_page_numbers(api, fixtures, monkeypatch):
    """The workbench's OCR button sends no page list, so 「请分批指定页码」 was advice its reader
    could not follow. The refusal says whether pages were given, and the sentence without them
    names what can be done."""
    from synwevo_docs import views as V
    from synwevo_docs.engine import ocr
    pid = api.project("OCR 上限")
    api.upload(pid, fixtures / "测试_申请表.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    monkeypatch.setattr(ocr, "ocr_png", lambda png: pytest.fail("no page may be recognised"))   # TEST FIXTURE
    monkeypatch.setattr(V.Views, "MAX_OCR_PAGES", 1)
    monkeypatch.setattr(V.pe, "inspect", lambda data: {"pages": [{"index": 0, "kind": "scanned"}, {"index": 1, "kind": "scanned"}]})
    r = api.c.post(f"/api/sources/{sid}/ocr", json={})
    assert r.status_code == 400 and r.json()["error"] == "too_many_pages"
    assert r.json()["detail"] == {"field": "pages", "limit": 1, "requested": 2, "pages_given": False}
    assert "拆分" in r.json()["message"]
    r = api.c.post(f"/api/sources/{sid}/ocr", json={"pages": [0, 1]})
    assert r.json()["detail"]["pages_given"] is True and r.json()["message"].endswith("请分批指定页码")


def test_an_invalid_marker_expression_carries_the_parser_error(api, fixtures):
    pid = api.project("正则标记")
    r = api.c.patch(f"/api/projects/{pid}", json={"record_rule": {"mode": "per_block", "block": {"match": "regex", "anchor": "("}}})
    assert r.status_code == 400 and r.json()["error"] == "bad_rule"
    assert r.json()["detail"]["field"] == "block.anchor" and r.json()["detail"]["regex_error"]


def test_comment_and_editor_limits_are_in_detail():
    from synwevo_docs.comments import MAX_BODY, clean_body
    from synwevo_docs.editor_contract import validate_editor
    with pytest.raises(ServiceError) as e:
        clean_body("字" * (MAX_BODY + 1))
    assert e.value.detail == {"field": "body", "limit": MAX_BODY}
    with pytest.raises(ServiceError) as e:
        validate_editor("builtin_doc", {"page": {"margins": {"left": 150}}})
    assert e.value.code == "bad_editor" and e.value.detail == {"field": "editor.page.margins.left", "min": 0, "max": 100}


def test_a_copy_that_did_not_run_is_explained_in_the_recipients_language():
    """The copy.failed notification body used to be a Chinese sentence in every language, or the
    stored reason code itself (`target:…`, an exception's class name)."""
    from synwevo_docs.copies import copy_failure_reason
    assert copy_failure_reason("approval_expired", "en") == "The approval has expired."
    assert copy_failure_reason("source_changed", "zh-Hant") == "來源項目在批准後有改動，批准範圍不再有效。"
    assert copy_failure_reason("target:target_write_required", "zh-Hans") == "申请人已不能把项目复制到目标工作空间。"
    assert copy_failure_reason("source:not_found", "en") == "The source project is no longer available."
    assert copy_failure_reason("KeyError", "en") == "Copying failed and nothing was created."
    assert copy_failure_reason(None, "fr") == "Copying failed and nothing was created."


def test_row_limits_are_whole_sentences(monkeypatch):
    """The worksheet's name used to be spliced into the sentence as a Chinese fragment
    (「工作表「x」」 + 「的行数…」), which no catalogue can hold as one sentence."""
    from synwevo_docs.engine import tabular
    from synwevo_docs.engine.limits import LIMITS, LimitError
    monkeypatch.setattr(LIMITS, "max_rows", 10)
    with pytest.raises(LimitError, match=r"^CSV 的行数超过上限 10（"):
        tabular._check_rows(11, None)
    with pytest.raises(LimitError, match=r"^工作表「明细」的行数超过上限 10（"):
        tabular._check_rows(11, "明细")
