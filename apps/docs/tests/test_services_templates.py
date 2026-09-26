"""Templates: viewing PDF templates with fields, version pinning, deletion of templates in use,
macro-enabled uploads, and edits that race each other."""
from __future__ import annotations

import io
import threading
import zipfile

import openpyxl

from .helpers import docx_text


# ------------------------------------------------------------------ F129
def test_a_pdf_template_with_a_field_can_be_viewed(api, fixtures):
    """The view imported `..services` (beyond the top-level package): every PDF or image
    template answered 500 as soon as it had one field."""
    pid = api.project("PDF 模板视图")
    t = api.template(pid, fixtures / "测试_通知书模板.pdf")
    api.ok(api.c.get(f"/api/templates/{t['id']}/view"))                  # no fields: the import was skipped
    api.slot(t["id"], kind="pdf_box", page=0, nrect=[0.1, 0.1, 0.4, 0.15], name="收件人")
    view = api.ok(api.c.get(f"/api/templates/{t['id']}/view"))
    assert view["slots"] and all(len(s["nrect"]) == 4 for s in view["slots"])


# ------------------------------------------------------------------ F332 / F130
def _pinned_project(api, fixtures):
    pid = api.project("固定模板版本")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")                    # version 2
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid, t["id"]


def test_a_pinned_template_version_is_what_jobs_generate_from(api, fixtures):
    pid, tid = _pinned_project(api, fixtures)
    head = api.ok(api.c.get(f"/api/templates/{tid}"))["version"]["no"]
    assert head == 2
    p = api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_version": 2}))
    assert p["template_version"] == 2
    # the template moves on: a new field over "000000", left unmapped, so it would come out empty
    api.text_slot(tid, "000000", "客户编号", ctx="客户编号 000000")
    assert api.ok(api.c.get(f"/api/templates/{tid}"))["version"]["no"] == 3
    summ = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    assert summ["template_version"] == 2 and summ["template_pinned"] is True
    job = api.job(pid)
    assert job["status"] == "completed" and job["template"]["version_no"] == 2
    text = docx_text(next(iter(api.outputs(job["id"]).values())))
    assert "客户编号 000000" in text                                     # generated from v2, not head
    # unpinning follows head again
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_version": None}))
    assert api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))["template_version"] == 3


def test_pinning_is_validated_and_follows_the_template(api, fixtures):
    pid, tid = _pinned_project(api, fixtures)
    r = api.c.patch(f"/api/projects/{pid}", json={"template_version": 99})
    assert r.status_code == 400 and r.json()["detail"]["field"] == "template_version"
    assert api.c.patch(f"/api/projects/{pid}", json={"template_version": "abc"}).status_code == 400
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_version": 1}))
    other = api.template(pid, fixtures / "测试_合同模板.docx", name="另一个")          # re-pointed: the pin belonged to the old one
    assert api.ok(api.c.get(f"/api/projects/{pid}"))["template_version"] is None
    assert api.ok(api.c.get(f"/api/projects/{pid}"))["template_id"] == other["id"]


def test_the_v1_pin_is_stored(api, client, fixtures):
    from synwevo_core.auth import Auth
    pid, tid = _pinned_project(api, fixtures)
    token, _ = Auth(client.app_state.app.store).create_key("pin", "default", ("read", "write"))
    h = {"Authorization": f"Bearer {token}"}
    r = api.c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid, "version": 2}, headers=h)
    assert r.status_code == 200 and r.json()["template"]["pinned"] is True and r.json()["template"]["version"] == 2


# ------------------------------------------------------------------ deletion
def test_a_template_in_use_is_not_deleted(api, fixtures):
    pid, tid = _pinned_project(api, fixtures)
    r = api.c.delete(f"/api/templates/{tid}")
    assert r.status_code == 409 and r.json()["error"] == "template_in_use"
    assert [p["id"] for p in r.json()["detail"]["projects"]] == [pid]
    assert api.ok(api.c.get(f"/api/projects/{pid}"))["template"]["id"] == tid     # nothing broke
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_id": None}))
    api.ok(api.c.delete(f"/api/templates/{tid}"))


def test_a_project_whose_template_was_deleted_still_opens(api, fixtures):
    """Data from before the refusal existed: the project must open so a template can be chosen."""
    pid, tid = _pinned_project(api, fixtures)
    api.c.app_state.app.store.execute("UPDATE templates SET deleted=1 WHERE id=?", (tid,))
    p = api.ok(api.c.get(f"/api/projects/{pid}"))
    assert p["template"] is None and p["template_missing"] is True
    r = api.c.get(f"/api/projects/{pid}/generation-summary")
    assert r.status_code == 400 and r.json()["error"] == "no_template" and "已被删除" in r.json()["message"]
    assert api.c.get(f"/api/projects/{pid}/preview").status_code == 400


# ------------------------------------------------------------------ F180 macro-enabled uploads
def _as_xlsm(xlsx: bytes) -> bytes:
    """TEST FIXTURE: turn an .xlsx into a macro-enabled workbook (content type + a VBA part)."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "[Content_Types].xml":
                data = data.replace(b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                data = data.replace(b"<Default ", b'<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/><Default ', 1)
            if info.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdVba" Type="http://schemas.microsoft.com/office/2006/'
                                                         b'relationships/vbaProject" Target="vbaProject.bin"/></Relationships>')
            z.writestr(info, data)
        z.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 not really VBA")
    return out.getvalue()


def _macro_free(data: bytes) -> None:
    z = zipfile.ZipFile(io.BytesIO(data))
    ct = z.read("[Content_Types].xml").decode()
    assert "macroEnabled" not in ct and "vbaProject" not in ct
    assert not any("vbaProject" in n for n in z.namelist())
    assert b"vbaProject" not in z.read("xl/_rels/workbook.xml.rels")
    assert "spreadsheetml.sheet.main+xml" in ct


def _macro_template(api, fixtures):
    xlsm = _as_xlsm((fixtures / "测试_订单模板.xlsx").read_bytes())
    pid = api.project("宏模板")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    t = api.ok(api.c.post("/api/templates/upload", files={"file": ("带宏.xlsm", xlsm)}, data={"project_id": pid}))
    return pid, t


def test_a_macro_enabled_upload_is_stored_macro_free(api, fixtures):
    pid, t = _macro_template(api, fixtures)
    assert t["format"] == "xlsx" and any("已从模板中移除" in n for n in t["info"]["notes"])
    _macro_free(api.ok_bytes(api.c.get(f"/api/templates/{t['id']}/download", params={"which": "working"})))
    _macro_free(api.ok_bytes(api.c.get(f"/api/templates/{t['id']}/download", params={"which": "original"})))


def test_a_macro_enabled_template_produces_real_xlsx_files(api, fixtures):
    pid, t = _macro_template(api, fixtures)
    s = api.slot(t["id"], kind="cell", sheet="订单", cell="B2", name="订单号")
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0}, "group": {"keys": [{"header": "订单号"}]}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": s["binding"]}}])
    job = api.job(pid)
    assert job["status"] == "completed"
    out = next(iter(api.outputs(job["id"]).values()))
    _macro_free(out)
    assert openpyxl.load_workbook(io.BytesIO(out))["订单"]["B2"].value


# ------------------------------------------------------------------ F140 / F227 concurrent edits
def test_a_template_edit_computed_from_an_old_head_is_refused(api, fixtures):
    pid = api.project("并发编辑")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    app = api.c.app_state.app
    v1 = app.get_version(t["id"], 1)
    api.text_slot(t["id"], "示例客户名称", "客户名称")                    # someone else's edit: head is now 2
    from synwevo_core.errors import ServiceError
    try:
        app.new_version(t["id"], app.store.get_blob(v1["working_blob"]), [], "旧页面的修改", base_head=1)
        raise AssertionError("stale edit accepted")
    except ServiceError as e:
        assert e.status == 409 and e.code == "stale_template"
    assert [s["name"] for s in api.ok(api.c.get(f"/api/templates/{t['id']}"))["version"]["slots"]] == ["客户名称"]


def test_concurrent_field_additions_never_lose_one(api, fixtures):
    pid = api.project("并发字段")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    view = api.view(t["id"])
    para = next(b for part in view["doc"]["parts"] if part["kind"] == "body"
                for b in part["blocks"] if b["t"] == "p" and "示例客户名称" in b["text"])
    start = para["text"].index("示例客户名称")
    results = []

    def add(name, s0, s1):
        r = api.c.post(f"/api/templates/{t['id']}/slots", json={"kind": "text", "part": para["part"], "path": para["path"],
                                                                 "start": s0, "end": s1, "name": name})
        results.append((name, r.status_code))

    threads = [threading.Thread(target=add, args=("甲", start, start + 2)), threading.Thread(target=add, args=("乙", start + 3, start + 5))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    names = {s["name"] for s in api.ok(api.c.get(f"/api/templates/{t['id']}"))["version"]["slots"]}
    ok = {n for n, code in results if code == 200}
    assert all(code in (200, 409) for _, code in results), results
    assert ok <= names, (results, names)                                  # every accepted field is really there


def test_a_builtin_save_from_a_stale_editor_is_refused(api):
    t = api.ok(api.c.post("/api/templates/builtin", json={"kind": "builtin_doc", "editor": {"type": "doc", "content": []}}))
    api.ok(api.c.put(f"/api/templates/{t['id']}/builtin", json={"editor": {"type": "doc", "content": []}, "base_version": 1}))
    r = api.c.put(f"/api/templates/{t['id']}/builtin", json={"editor": {"type": "doc", "content": []}, "base_version": 1})
    assert r.status_code == 409 and r.json()["error"] == "stale_template"


def test_concurrent_mapping_saves_from_the_same_base_accept_exactly_one(api, fixtures):
    """Six identical saves from one revision: one becomes a revision; the others arrive on a moved
    base, merge mapping by mapping to exactly what is saved and add nothing (they answer 200 with
    `unchanged`), so the history holds one new revision rather than five refusals or six copies."""
    pid, _ = _pinned_project(api, fixtures)
    base = api.ok(api.c.get(f"/api/projects/{pid}/mappings"))
    codes = []

    def save():
        codes.append(api.c.put(f"/api/projects/{pid}/mappings", json={"mappings": base["mappings"], "base_rev": base["rev"]}).status_code)

    threads = [threading.Thread(target=save) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert codes == [200] * 6, codes
    after = api.ok(api.c.get(f"/api/projects/{pid}/mappings"))
    assert after["rev"] == base["rev"] + 1 and after["mappings"] == [dict(m, updated_at=x["updated_at"]) for m, x in zip(base["mappings"], after["mappings"])]
    assert api.ok(api.c.get(f"/api/projects/{pid}/mappings"))["rev"] == base["rev"] + 1


# ------------------------------------------------------------------ F114 / F141 / F208
def test_desensitized_copies_download_without_adding_them_to_the_project(api, fixtures):
    pid = api.project("脱敏下载")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    res = api.ok(api.c.post(f"/api/projects/{pid}/desensitize", json={"columns": {"姓名": "pseudonym"}, "add_to_project": False}))
    f = res["files"][0]
    r = api.c.get(f"/api/desensitized/{f['blob']}")
    assert r.status_code == 200 and openpyxl.load_workbook(io.BytesIO(r.content)).active["B2"].value == "姓名0001"
    assert len(api.ok(api.c.get(f"/api/projects/{pid}/sources"))) == 1          # not added


# ------------------------------------------------------------------ F105 editor body limit
def test_an_oversized_chunked_editor_body_is_refused_while_it_arrives(api, monkeypatch):
    """A chunked body carries no Content-Length, and the old check (which only ran after FastAPI
    had read and parsed the whole body anyway) skipped it entirely."""
    import json as _json

    from synwevo_docs.engine.limits import LIMITS
    monkeypatch.setattr(LIMITS, "max_editor_body_bytes", 2000)
    big = _json.dumps({"kind": "builtin_doc", "editor": {"blob": "x" * 10_000}}).encode()

    def chunks(data):
        for i in range(0, len(data), 512):
            yield data[i:i + 512]

    r = api.c.post("/api/templates/builtin", content=chunks(big), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "editor_body_too_large"
    small = _json.dumps({"kind": "builtin_doc", "editor": {"type": "doc", "content": []}}).encode()
    assert api.c.post("/api/templates/builtin", content=chunks(small), headers={"Content-Type": "application/json"}).status_code == 200
    assert api.c.post("/api/templates/builtin", content=b"[1,2]", headers={"Content-Type": "application/json"}).status_code == 400
