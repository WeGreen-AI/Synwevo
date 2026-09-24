"""Create / save templates made with the built-in editors."""
from __future__ import annotations

from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

from .engine import builtin as B
from .services import App


def _build(kind: str, editor: dict) -> tuple[bytes, list[dict], str]:
    try:
        if kind == "builtin_doc":
            data, slots = B.build_docx(editor)
            return data, slots, "docx"
        if kind == "builtin_sheet":
            data, slots = B.build_xlsx(editor)
            return data, slots, "xlsx"
    except B.BuiltinError as e:
        raise ServiceError(400, "bad_editor", str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise ServiceError(400, "bad_editor", f"模板生成失败：{e}") from e
    raise ServiceError(400, "bad_kind", "未知的内置模板类型")


def create_builtin(app: App, body: dict) -> dict:
    kind = body.get("kind")
    editor = body.get("editor") or {}
    data, slots, fmt = _build(kind, editor)
    tid = new_id("t")
    t = now()
    blob = app.store.put_blob(data)
    from synwevo_core.context import tenant as current_tenant
    app.store.insert("templates", {"id": tid, "tenant": current_tenant(), "name": (body.get("name") or ("文档模板" if fmt == "docx" else "表格模板"))[:100],
                                   "kind": kind, "format": fmt, "original_blob": blob, "original_name": None, "head": 1,
                                   "created_at": t, "updated_at": t, "info": {"notes": []}})
    app.store.insert("template_versions", {"template_id": tid, "version_no": 1, "parent_no": None, "working_blob": blob,
                                           "slots": slots, "editor": editor, "note": "在软件内创建", "created_at": t})
    return app.template_detail(tid)


def save_builtin(app: App, tid: str, body: dict) -> dict:
    r = app.get_template(tid)
    if r["kind"] not in ("builtin_doc", "builtin_sheet"):
        raise ServiceError(400, "not_builtin", "只有内置编辑器创建的模板可以在编辑器中修改")
    editor = body.get("editor") or {}
    data, slots, _ = _build(r["kind"], editor)
    # keep extra slot settings (required, formats, defaults) for fields that still exist
    old = {s["id"]: s for s in app.get_version(tid, r["head"])["slots"]}
    for s in slots:
        prev = old.get(s["id"])
        if prev:
            # empty_policy / separator / names come from the editor itself (its attributes win)
            for k in ("required", "data_type", "format", "default", "placeholder", "repeat_header"):
                if k in prev and k not in ("required",) or (k == "required" and prev.get("required") and not s.get("required")):
                    s[k] = prev[k]
    app.store.update("templates", {"id": tid}, {"original_blob": app.store.put_blob(data)})
    if body.get("name"):
        app.store.update("templates", {"id": tid}, {"name": body["name"][:100]})
    return app.new_version(tid, data, slots, "在编辑器中保存", editor)
