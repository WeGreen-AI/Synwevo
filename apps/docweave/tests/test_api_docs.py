"""The committed OpenAPI file and the example definition must match the running code."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_file_is_in_sync(tmp_path):
    out = tmp_path / "openapi.yaml"
    p = subprocess.run([sys.executable, str(ROOT / "backend" / "manage.py"), "--data", str(tmp_path / "data"),
                        "openapi", "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-800:]
    committed = (ROOT / "docs" / "integration" / "openapi.yaml").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed, "docs/integration/openapi.yaml 与代码不一致：运行 python backend/manage.py openapi"


def test_openapi_describes_only_machine_routes_and_needs_auth():
    import yaml
    spec = yaml.safe_load((ROOT / "docs" / "integration" / "openapi.yaml").read_text(encoding="utf-8"))
    paths = set(spec["paths"])
    assert {"/healthz", "/readyz"} <= paths
    assert all(p.startswith("/api/v1") for p in paths - {"/healthz", "/readyz"})
    assert not any(p.startswith("/api/") and "/v1" not in p for p in paths)      # no UI-internal routes
    assert spec["security"] == [{"apiKey": []}]
    assert spec["components"]["securitySchemes"]["apiKey"]["scheme"] == "bearer"
    for probe in ("/healthz", "/readyz"):
        assert spec["paths"][probe]["get"]["security"] == []
    # Component schemas are pruned to what the kept paths reach. FastAPI emits one per route in
    # the whole app, so without pruning this file describes UI and OIDC request bodies it does
    # not document — including, once the account service was added, the OIDC form bodies.
    names = " ".join(spec["components"].get("schemas", {}))
    assert "oidc" not in names.lower()
    for name in spec["components"].get("schemas", {}):
        if name.startswith("Body_"):
            assert "_api_v1_" in name, f"{name} 属于非 v1 路由，不应出现在机器接口文档中"


def test_example_definition_matches_the_documented_shape(api, fixtures):
    """The definition shipped with the docs really drives a template + rule + mappings."""
    doc = json.loads((ROOT / "docs" / "integration" / "example-definition.json").read_text(encoding="utf-8"))
    pid = api.project("示例定义")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    from docweave import definitions as D
    created = D.import_template_fields(api.c.app_state.app, t["id"], {"schema": "docweave.template-fields", "version": 1,
                                                                      "fields": doc["fields"]})
    names = [c["name"] for c in created["created"]]
    assert names == [f["name"] for f in doc["fields"]]
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_id": t["id"], "record_rule": doc["rule"]}))
    fields = {s["name"]: s for s in api.ok(api.c.get(f"/api/templates/{t['id']}"))["version"]["slots"]}
    maps = []
    for m in doc["mappings"]:
        m = dict(m)
        maps.append({**m, "target": {"binding": fields[m.pop("field")]["binding"]}})
    api.maps(pid, maps)
    job = api.job(pid, naming="{客户编号}_{客户名称}")
    assert job["status"] == "completed" and job["counts"]["success"] == 10
    import docx, io
    out = api.outputs(job["id"])
    name = sorted(out)[2]
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(out[name])).paragraphs)
    assert "年" in text and "月" in text and "," in text          # date and thousands formats from the definition
