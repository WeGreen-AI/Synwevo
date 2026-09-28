"""Create / save templates made with the built-in editors."""
from __future__ import annotations

import json

from synwevo_core.errors import ServiceError
from .editor_contract import validate_editor
from synwevo_core.store import new_id, now

from .engine import builtin as B
from .services import App


def sync_editor_fields(kind: str, editor: dict, slots: list[dict]) -> dict:
    """The editor model with each field's name and required flag taken from the template's slots
    — what a field changed on the project page must carry, or the next editor save reverts it."""
    by_id = {s["id"]: s for s in slots or []}
    out = json.loads(json.dumps(editor))

    def apply(attrs: dict) -> None:
        sl = by_id.get(attrs.get("id"))
        if sl is None:
            return
        if sl.get("name"):
            attrs["name"] = sl["name"]
        attrs["required"] = bool(sl.get("required"))

    if kind == "builtin_sheet":
        for sh in out.get("sheets") or []:
            for f in sh.get("fields") or []:
                apply(f)
    else:
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") in ("field", "repeatBlock") and isinstance(node.get("attrs"), dict):
                    apply(node["attrs"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(out)
    return out


def _build(kind: str, editor: dict) -> tuple[bytes, list[dict], str]:
    editor = validate_editor(kind, editor)
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
    editor = body.get("editor", {})
    data, slots, fmt = _build(kind, editor)
    tid = new_id("t")
    t = now()
    blob = app.store.put_blob(data)
    from synwevo_core.context import tenant as current_tenant
    with app.store.tx():
        app.store.insert("templates", {"id": tid, "tenant": current_tenant(), "name": (body.get("name") or ("文档模板" if fmt == "docx" else "表格模板"))[:100],
                                   "kind": kind, "format": fmt, "original_blob": blob, "original_name": None, "head": 1,
                                   "created_at": t, "updated_at": t, "info": {"notes": []}})
        app.store.insert("template_versions", {"template_id": tid, "version_no": 1, "parent_no": None, "working_blob": blob,
                                               "slots": slots, "editor": editor, "note": "在软件内创建", "created_at": t})
    return app.template_detail(tid)


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _three_way(keys, kb: dict, kc: dict, ki: dict, force: set[str], tag, conflicts: list, reason_key) -> dict:
    """Generic per-key merge: what only one side touched goes through, the same change from both
    sides agrees, different changes (or a delete against an edit) conflict unless forced."""
    out = {}
    for k in keys:
        b, c, i = kb.get(k), kc.get(k), ki.get(k)
        fid = tag(k)
        if b is None:                                    # created since the base
            if c is not None and i is not None and not _same(c, i) and fid not in force:
                conflicts.append({**reason_key(k), "reason": "both_added"})
                continue
            chosen = i if (i is not None and (c is None or fid in force)) else c
            if chosen is not None:
                out[k] = chosen
            continue
        c_changed = c is None or not _same(c, b)
        i_changed = i is None or not _same(i, b)
        if not c_changed:
            if i is not None:
                out[k] = i
            continue
        if not i_changed:
            if c is not None:
                out[k] = c
            continue
        if c is not None and i is not None and _same(c, i):
            out[k] = c
            continue
        if fid in force:
            if i is not None:
                out[k] = i
            continue
        conflicts.append({**reason_key(k), "reason": "deleted_by_other" if c is None else "deleted_by_you" if i is None else "both_changed"})
    return out


def merge_sheets(base: dict, current: dict, incoming: dict, force_ids: set[str] | None = None) -> tuple[dict, list[dict]]:
    """Three-way merge of a table template's editor document at the granularity a person edits it:
    one cell, one field, one sheet's layout (columns, rows, merges), one sheet.

    ``base`` is the version the editor loaded, ``current`` the head someone else saved since,
    ``incoming`` the client's document. Force ids name what the person chose to keep of their own
    after seeing the other side: ``cell:Sheet!A1``, ``field:<id>``, ``layout:<sheet>``, ``sheet:<name>``."""
    force = force_ids or set()
    conflicts: list[dict] = []
    by = lambda doc: {sh.get("name"): sh for sh in (doc or {}).get("sheets", []) if isinstance(sh, dict)}  # noqa: E731
    sb, sc, si = by(base), by(current), by(incoming)
    order = list(sc) + [n for n in si if n not in sc]
    merged_sheets = []
    for name in order:
        b, c, i = sb.get(name), sc.get(name), si.get(name)
        sid = f"sheet:{name}"
        if b is None:                                    # a sheet added since the base
            if c is not None and i is not None and not _same(c, i) and sid not in force:
                conflicts.append({"kind": "sheet", "sheet": name, "reason": "both_added"})
                continue
            chosen = i if (i is not None and (c is None or sid in force)) else c
            merged_sheets.append(chosen)
            continue
        if c is None or i is None:                       # deleted on one side
            other = i if c is None else c
            if other is None or _same(other, b) or sid in force:
                if sid in force and i is not None:
                    merged_sheets.append(i)
                continue                                 # deleted, and the other side did not touch it: gone
            conflicts.append({"kind": "sheet", "sheet": name, "reason": "deleted_by_other" if c is None else "deleted_by_you"})
            continue
        cells = _three_way(sorted(set(b.get("cells", {})) | set(c.get("cells", {})) | set(i.get("cells", {}))),
                           b.get("cells", {}), c.get("cells", {}), i.get("cells", {}), force,
                           lambda ref: f"cell:{name}!{ref}", conflicts, lambda ref: {"kind": "cell", "sheet": name, "ref": ref})
        fb = {f["id"]: f for f in b.get("fields", []) if isinstance(f, dict) and f.get("id")}
        fc = {f["id"]: f for f in c.get("fields", []) if isinstance(f, dict) and f.get("id")}
        fi = {f["id"]: f for f in i.get("fields", []) if isinstance(f, dict) and f.get("id")}
        fields = _three_way(list(fb) + [x for x in fc if x not in fb] + [x for x in fi if x not in fb and x not in fc], fb, fc, fi, force,
                            lambda fid: f"field:{fid}", conflicts, lambda fid: {"kind": "field", "sheet": name, "id": fid, "name": (fi.get(fid) or fc.get(fid) or fb.get(fid) or {}).get("name")})
        # Columns and rows merge size by size (the editor pads the grid with default sizes as
        # people type further out, so two people padding differently is not a collision; the
        # same column resized differently by both is). Merged ranges stay one unit.
        chosen_layout = {}
        forced = f"layout:{name}" in force
        for part in ("cols", "rows"):
            lb, lc, li = b.get(part, []), c.get(part, []), i.get(part, [])
            out = []
            clash = False
            for k in range(max(len(lc), len(li))):
                eb, ec, ei = (lb[k] if k < len(lb) else None), (lc[k] if k < len(lc) else None), (li[k] if k < len(li) else None)
                if ec is None or ei is None:
                    out.append(ec if ei is None else ei)           # only one side has this row/column: keep it
                elif _same(ec, ei) or _same(ec, eb):
                    out.append(ei)
                elif _same(ei, eb):
                    out.append(ec)
                elif forced:
                    out.append(ei)
                else:
                    out.append(ec)
                    clash = True
            if clash:
                conflicts.append({"kind": "layout", "sheet": name, "part": part, "reason": "both_changed"})
            chosen_layout[part] = out
        lb, lc, li = b.get("merges", []), c.get("merges", []), i.get("merges", [])
        if _same(lc, lb) or _same(lc, li):
            chosen_layout["merges"] = li
        elif _same(li, lb):
            chosen_layout["merges"] = lc
        elif forced:
            chosen_layout["merges"] = li
        else:
            conflicts.append({"kind": "layout", "sheet": name, "part": "merges", "reason": "both_changed"})
            chosen_layout["merges"] = lc
        merged_sheets.append({**c, **chosen_layout, "name": name, "cells": cells,
                              "fields": [fields[k] for k in (list(fc) + [x for x in fi if x not in fc]) if k in fields] + [v for k, v in fields.items() if k not in fc and k not in fi]})
    return {**(current or {}), "sheets": merged_sheets}, conflicts


def save_builtin(app: App, tid: str, body: dict, *, merge: bool = False) -> dict:
    """Save the editor's document as a new version.

    ``base_version`` is the version the editor loaded. When it is sent and the template has
    moved on since (another tab, another person), the save is refused with 409 instead of
    silently replacing their fields and layout; without it, only a save that races this very
    request is caught. With ``merge`` (the editors in the browser), a table template moved on
    since is merged cell by cell first and only real collisions are refused; the machine API
    keeps the whole-document refusal its callers were written against."""
    r = app.get_template(tid)
    if r["kind"] not in ("builtin_doc", "builtin_sheet"):
        raise ServiceError(400, "not_builtin", "只有内置编辑器创建的模板可以在编辑器中修改")
    discard = body.get("discard_live", False)
    if not isinstance(discard, bool):
        raise ServiceError(400, "invalid", "discard_live 必须是布尔值", {"field": "discard_live"})
    # A save made outside the editing room replaces the live document: not while it holds edits
    # nobody saved as a version, unless asked explicitly (CI-113). A save from inside the room
    # saves that live document and goes through.
    from synwevo_core.context import actor as current_actor
    app._guard_live_edits(tid, discard, "save", saver=current_actor())
    base = body.get("base_version")
    editor = body.get("editor", {})
    merged_with = None
    force = body.get("force_ids")
    if force is not None and (not isinstance(force, list) or not all(isinstance(x, str) for x in force)):
        raise ServiceError(400, "bad_value", "force_ids 必须是字符串列表", {"field": "force_ids"})
    if base is not None:
        if isinstance(base, bool) or not isinstance(base, int) or base < 1:
            raise ServiceError(400, "bad_value", "base_version 必须是正整数版本号", {"field": "base_version"})
        if base != r["head"]:
            # A table template is merged cell by cell against the version the editor loaded; the
            # document editor's live copy is the shared state (collab.py), so its saves are not.
            base_row = app.store.one("SELECT editor FROM template_versions WHERE template_id=? AND version_no=?", (tid, base)) if (merge and r["kind"] == "builtin_sheet") else None
            if not base_row or not isinstance(editor, dict) or not isinstance(editor.get("sheets"), list):
                raise ServiceError(409, "stale_template", "模板已在其他窗口或被其他人修改，请刷新后再保存（你的编辑内容仍在编辑器中）",
                                   {"head": r["head"], "base_version": base})
            base_editor = base_row["editor"] if isinstance(base_row["editor"], dict) else json.loads(base_row["editor"] or "{}")
            head_editor = app.editor_of(tid, app.get_version(tid, r["head"]), r["kind"]) or {}
            validate_editor(r["kind"], editor)
            editor, conflicts = merge_sheets(base_editor, head_editor, editor, set(force or []))
            if conflicts:
                raise ServiceError(409, "stale_template", "有单元格或字段同时被其他人修改，请查看冲突后选择保留哪一方",
                                   {"head": r["head"], "base_version": base, "conflicts": conflicts})
            if _same(editor, head_editor):
                detail = app.template_detail(tid)
                detail["merged_with"] = r["head"]
                detail["unchanged"] = True
                return detail
            merged_with = r["head"]
    # The version's note: a name the person gives this checkpoint (9.13 D 「命名检查点」), or the
    # default wording. Bounded and plain text; never trusted as markup.
    raw_note = body.get("note")
    if raw_note is not None and not isinstance(raw_note, str):
        raise ServiceError(400, "invalid_field", "note 必须是字符串", {"field": "note"})
    note = " ".join((raw_note or "").split())
    if len(note) > 200:
        raise ServiceError(400, "invalid_field", "版本说明最多 200 个字符", {"field": "note", "max": 200})
    note = note or "在编辑器中保存"
    try:
        data, slots, _ = _build(r["kind"], editor)
    except ServiceError as e:
        if not merged_with or e.code != "bad_editor":
            raise
        # Each side's edits were fine on their own; together they make a table the template cannot
        # hold (e.g. a region field over cells the other person merged). Said as the stale-version
        # conflict it is — keep theirs, or save yours over it — not as a raw build error (CI-116).
        raise ServiceError(409, "stale_template", f"你的修改与其他人之后保存的修改合在一起无法生成模板：{e.message}",
                           {"head": merged_with, "base_version": base, "merge_error": e.message}) from e
    # keep extra slot settings (required, formats, defaults) for fields that still exist
    old = {s["id"]: s for s in app.get_version(tid, r["head"])["slots"]}
    for s in slots:
        prev = old.get(s["id"])
        if prev:
            # empty_policy / separator / names come from the editor itself (its attributes win)
            for k in ("required", "data_type", "format", "default", "placeholder", "repeat_header"):
                if k in prev and k not in ("required",) or (k == "required" and prev.get("required") and not s.get("required")):
                    s[k] = prev[k]
    with app.store.tx():
        detail = app.new_version(tid, data, slots, note + (f"（已与第 {merged_with} 版合并）" if merged_with else ""), editor, base_head=r["head"])
        app.store.update("templates", {"id": tid}, {"original_blob": app.store.put_blob(data)})
        if body.get("name"):
            app.store.update("templates", {"id": tid}, {"name": body["name"][:100]})
            detail = app.template_detail(tid)
    if merged_with:
        detail["merged_with"] = merged_with
    return detail
