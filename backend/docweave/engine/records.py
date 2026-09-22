"""Sample (record) splitting.  A record is exactly the data that becomes one output
document.  The three modes are explicit; nothing is inferred from mappings."""
from __future__ import annotations

from decimal import Decimal

from . import tabular
from . import values as V
from .resolve import ResolveError, SourceDoc, column_index, table_view

MODES = {
    "per_row": "每行一份：表格中的每条有效数据行生成一份文档",
    "per_file": "每文件一份：每个来源文件生成一份文档",
    "per_group": "每组一份：按选定字段（如订单号）分组，每组生成一份文档",
    "per_block": "每块一份：一个文件里有多个表单块时，按每块开头的标记文字切分，每块生成一份文档",
}


def build_records(sources: list[SourceDoc], rule: dict) -> dict:
    mode = rule.get("mode")
    if mode not in MODES:
        raise ValueError("请先选择样本模式（每行一份 / 每文件一份 / 每组一份 / 每块一份）")
    result = {"mode": mode, "records": [], "file_errors": [], "excluded": {"blank": 0, "filtered": 0, "empty_key": 0},
              "warnings": [], "files_used": []}
    if mode == "per_file":
        for src in sources:
            result["records"].append({"index": len(result["records"]), "key": src.file_id, "label": src.filename,
                                      "file_id": src.file_id, "file_ids": [src.file_id], "where": "整个文件"})
            result["files_used"].append(src.file_id)
        return result
    if mode == "per_block":
        from . import blocks as B
        spec = rule.get("block") or {}
        if not (spec.get("anchor") or "").strip():
            raise ValueError("请填写每个表单块开头的标记文字")
        result["blocks"] = {}
        for src in sources:
            try:
                bl = B.split(src, spec)
            except ResolveError as e:
                result["file_errors"].append({"file_id": src.file_id, "file": src.filename, "code": e.code, "message": str(e)})
                continue
            result["blocks"][src.file_id] = len(bl)
            for b in bl:
                result["records"].append({"index": len(result["records"]), "key": b.doc.file_id, "file_id": b.doc.file_id,
                                          "file_ids": [src.file_id], "block": b.index, "ident": b.label,
                                          "label": f"{src.filename} · 第 {b.index + 1} 块" + (f"「{b.label[:24]}」" if b.label else ""),
                                          "where": b.where})
            result["files_used"].append(src.file_id)
        return result
    table = rule.get("table") or {}
    if not table.get("sheet") and table.get("sheet_index") is None:
        raise ValueError("请指定数据所在的工作表与表头行")
    per_file_views = []
    for src in sources:
        if src.workbook_json is None:
            result["file_errors"].append({"file_id": src.file_id, "file": src.filename, "code": "not_table",
                                          "message": f"「{src.filename}」不是表格文件，不能按行/按组切分样本"})
            continue
        try:
            tv = table_view(src, table)
        except ResolveError as e:
            result["file_errors"].append({"file_id": src.file_id, "file": src.filename, "code": e.code, "message": str(e)})
            continue
        result["excluded"]["blank"] += tv.excluded_blank
        result["excluded"]["filtered"] += tv.excluded_filtered
        per_file_views.append((src, tv))
        result["files_used"].append(src.file_id)
    id_col = rule.get("id_column")
    if mode == "per_row":
        seen_ids: dict[str, list[str]] = {}
        for src, tv in per_file_views:
            idc = None
            if id_col:
                try:
                    idc = column_index(tv, id_col, src)
                except ResolveError as e:
                    result["file_errors"].append({"file_id": src.file_id, "file": src.filename, "code": e.code, "message": str(e)})
                    continue
            for r in tv.data_rows:
                ident = tv.sheet.cell(r, idc).text if idc is not None else ""
                label = (ident + " · " if ident else "") + f"{src.filename} · {tv.sheet.name} 第 {r + 1} 行"
                result["records"].append({"index": len(result["records"]), "key": f"{src.file_id}:{tv.sheet.name}:{r + 1}",
                                          "label": label, "ident": ident, "file_id": src.file_id, "file_ids": [src.file_id],
                                          "sheet": tv.sheet.name, "row": r, "where": f"{tv.sheet.name}!{r + 1}"})
                if ident:
                    seen_ids.setdefault(ident, []).append(src.filename)
        dups = {k: v for k, v in seen_ids.items() if len(set(v)) > 1}
        if dups:
            sample = "、".join(list(dups)[:5])
            result["warnings"].append(f"{len(dups)} 个编号在多个文件中重复出现（如 {sample}），它们各自作为独立样本生成，不会合并或去重")
        return result
    # per_group
    group = rule.get("group") or {}
    keys = group.get("keys") or []
    if not keys:
        raise ValueError("分组模式需要选择分组字段")
    scope = group.get("scope", "file")
    sort = group.get("sort") or []
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for src, tv in per_file_views:
        try:
            kidx = [column_index(tv, k, src) for k in keys]
            sidx = [(column_index(tv, s, src), s.get("dir", "asc"), s.get("numeric", False)) for s in sort]
        except ResolveError as e:
            result["file_errors"].append({"file_id": src.file_id, "file": src.filename, "code": e.code, "message": str(e)})
            continue
        for r in tv.data_rows:
            kv = tuple(tv.sheet.cell(r, c).text.strip() for c in kidx)
            if all(not x for x in kv):
                result["excluded"]["empty_key"] += 1
                continue
            gk = kv if scope == "cross_file" else (src.file_id,) + kv
            if gk not in groups:
                groups[gk] = {"key_values": kv, "rows": [], "files": []}
                order.append(gk)
            g = groups[gk]
            g["rows"].append((src.file_id, tv.sheet.name, r, [tv.sheet.cell(r, c) for c, _, _ in sidx]))
            if src.file_id not in g["files"]:
                g["files"].append(src.file_id)
    name_by_id = {s.file_id: s.filename for s in sources}
    label_count: dict[str, int] = {}
    for gk in order:
        label_count[" / ".join(groups[gk]["key_values"])] = label_count.get(" / ".join(groups[gk]["key_values"]), 0) + 1
    for gk in order:
        g = groups[gk]
        rows = g["rows"]
        if sort:
            for pos in reversed(range(len(sort))):
                desc = sort[pos].get("dir", "asc") == "desc"
                numeric = sort[pos].get("numeric", False)
                rows.sort(key=lambda x, p=pos, n=numeric: _sort_key(x[3][p], n), reverse=desc)
        kl = " / ".join(g["key_values"])
        label = kl
        if scope != "cross_file" and label_count[kl] > 1:
            label = f"{kl} · {name_by_id[g['files'][0]]}"
        rec = {"index": len(result["records"]), "key": ("*|" if scope == "cross_file" else g["files"][0] + "|") + kl,
               "label": label, "ident": kl, "file_id": g["files"][0], "file_ids": g["files"],
               "group_values": list(g["key_values"]),
               "rows": [[fid, sheet, r] for fid, sheet, r, _ in rows], "where": f"{len(rows)} 行"}
        if scope == "cross_file" and len(g["files"]) > 1:
            rec["merged_from"] = [name_by_id[f] for f in g["files"]]
        result["records"].append(rec)
    if scope != "cross_file":
        dup = [k for k, n in label_count.items() if n > 1]
        if dup:
            result["warnings"].append(f"{len(dup)} 个分组编号在多个文件中出现（如 {'、'.join(dup[:5])}），默认按文件分别成组、不会合并；如需跨文件合并请开启「跨文件按编号分组」")
    else:
        merged = [r for r in result["records"] if r.get("merged_from")]
        if merged:
            result["warnings"].append(f"已开启跨文件分组：{len(merged)} 个分组的数据来自多个文件并已合并，请在样本清单中核对")
    return result


def _sort_key(v: V.Value, numeric: bool):
    if numeric:
        d = v.as_decimal()
        return (0, d) if d is not None else (1, Decimal(0))
    return (0, v.text)


def record_context(record: dict, rule: dict, srcs: dict[str, SourceDoc]) -> dict:
    """Runtime context used by resolvers (rebuilds table views)."""
    mode = rule["mode"]
    ctx = {"mode": mode, "index": record["index"], "record_key": record["key"], "group_label": record.get("ident", "")}
    src = srcs.get(record["file_id"])
    if src is None and mode == "per_block" and "~" in record["file_id"]:
        from .blocks import block_doc
        src = block_doc(srcs, record["file_id"], rule.get("block") or {})
    if src is None:
        raise ResolveError("file_missing", "样本对应的来源文件已被移除")
    ctx["src"] = src
    if mode == "per_row":
        tv = table_view(src, rule["table"])
        ctx.update({"table_view": tv, "row": record["row"], "sheet": tv.sheet.name, "table_spec": rule["table"]})
    elif mode == "per_group":
        views = {}
        rows = []
        for fid, _sheet, r in record["rows"]:
            s = srcs[fid]
            if fid not in views:
                views[fid] = table_view(s, rule["table"])
            rows.append((s, views[fid], r))
        ctx.update({"group_rows": rows, "table_spec": rule["table"], "sheet": rows[0][1].sheet.name if rows else "",
                    "srcs": [srcs[f] for f in record["file_ids"]]})
    else:
        ctx["table_spec"] = rule.get("table")
    return ctx


def summarize(result: dict, limit: int = 200, offset: int = 0) -> dict:
    recs = result["records"]
    return {"mode": result["mode"], "total": len(recs), "excluded": result["excluded"], "file_errors": result["file_errors"],
            "warnings": result["warnings"], "files_used": len(result["files_used"]), "blocks": result.get("blocks"),
            "records": [{k: r.get(k) for k in ("index", "key", "label", "ident", "file_id", "where", "merged_from")}
                        for r in recs[offset: offset + limit]]}
