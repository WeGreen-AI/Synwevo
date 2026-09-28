"""Execution plan: resolve every mapping for one record into slot values.

``build_plan`` is the single code path used by preview and by batch generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from . import tabular, transforms
from . import values as V
from .records import record_context
from .resolve import ResolveError, SourceDoc, extract_detail, extract_matrix, extract_scalar

SCALAR_SLOT_KINDS = {"text", "cell", "pdf_box", "pdf_field", "pptx_text", "docx_cell"}
REGION_SLOT_KINDS = {"region"}
REPEAT_SLOT_KINDS = {"repeat"}


@dataclass
class Plan:
    record: dict
    values: dict[str, dict] = field(default_factory=dict)     # slot id -> fill value
    issues: list[dict] = field(default_factory=list)
    trace: dict[str, dict] = field(default_factory=dict)      # slot id -> provenance

    @property
    def blocking(self) -> list[dict]:
        return [i for i in self.issues if i["level"] == "error"]

    def to_json(self) -> dict:
        def enc(fv):
            if fv["kind"] == "scalar":
                return {"kind": "scalar", "value": fv["value"].to_json()}
            if fv["kind"] == "matrix":
                return {"kind": "matrix", "rows": [[c.to_json() for c in r] for r in fv["rows"]]}
            out = {"kind": "rows", "rows": [{k: v.to_json() for k, v in row.items()} for row in fv["rows"]]}
            if "sections" in fv:                # a block's sub-groups (分段) / a nested table's rows per copy
                out["sections"] = fv["sections"]
            if "groups" in fv:
                out["groups"] = [[{k: v.to_json() for k, v in row.items()} for row in g] for g in fv["groups"]]
            return out
        return {"record": {k: self.record.get(k) for k in ("index", "key", "label", "ident", "file_id")},
                "values": {k: enc(v) for k, v in self.values.items()}, "issues": self.issues, "trace": self.trace}


def slot_kind_class(slot: dict) -> str:
    k = slot.get("kind")
    if k in REPEAT_SLOT_KINDS:
        return "repeat"
    if k in REGION_SLOT_KINDS:
        return "region"
    return "single"


def static_check(mappings: list[dict], slots: list[dict], rule: dict | None) -> list[dict]:
    """Checks that do not depend on data: conflicts, dangling targets, type mismatches."""
    issues = []
    by_binding: dict[str, list[dict]] = {}
    slot_by_id = {s["id"]: s for s in slots}
    bindings = {s.get("binding") or s["id"] for s in slots}
    for m in mappings:
        if not m.get("enabled", True):
            continue
        tgt = (m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")
        if tgt not in bindings:
            issues.append({"level": "error", "code": "target_missing", "mapping": m["id"],
                           "message": f"映射 #{m.get('no')} 的目标字段已不存在（模板已修改），请重新绑定或删除该映射"})
            continue
        by_binding.setdefault(tgt, []).append(m)
        tslots = [s for s in slots if (s.get("binding") or s["id"]) == tgt]
        cls = {slot_kind_class(s) for s in tslots}
        mtype = m.get("type", "single")
        if len(cls) > 1:
            # one value cannot be a single value, a fixed area and detail rows at once
            names = {"single": "单值", "region": "固定区域", "repeat": "重复明细"}
            issues.append({"level": "error", "code": "binding_mixed", "mapping": m["id"],
                           "message": f"映射 #{m.get('no')} 的目标同时绑定了{'、'.join(names[c] for c in sorted(cls))}字段；请取消这些字段之间的共享绑定"})
        elif cls and mtype not in cls:
            names = {"single": "单值", "region": "固定区域", "repeat": "重复明细"}
            issues.append({"level": "error", "code": "type_mismatch", "mapping": m["id"],
                           "message": f"映射 #{m.get('no')} 是{names.get(mtype, mtype)}映射，但目标是{names.get(next(iter(cls)), '')}字段"})
        if mtype == "single" and len(m.get("sources") or []) > 1 and not (m.get("combine") or {}).get("op"):
            issues.append({"level": "error", "code": "combine_missing", "mapping": m["id"],
                           "message": f"映射 #{m.get('no')} 有多个来源，需要设置组合方式（拼接 / 换行 / 求和）"})
        if m.get("group_by") and not any(s.get("block") for s in tslots):
            issues.append({"level": "error", "code": "group_by_unsupported", "mapping": m["id"],
                           "message": f"映射 #{m.get('no')}：分段目前只支持 Word 模板的重复内容块；Excel / PPT / PDF 模板和 Word 表格行暂不支持嵌套明细"})
        nested = next((s for s in tslots if s.get("parent")), None)
        if mtype == "repeat" and nested is not None:
            block = slot_by_id.get(nested["parent"]) or {}
            pb = block.get("binding") or nested["parent"]
            if not any(x.get("enabled", True) and x.get("group_by") and _target(x) == pb for x in mappings):
                issues.append({"level": "error", "code": "nested_needs_group_by", "mapping": m["id"], "slot": nested["id"],
                               "message": _needs_split(nested, block)})
        errs = transforms.validate(m.get("transforms"))
        for e in errs:
            issues.append({"level": "error", "code": "bad_transform", "mapping": m["id"], "message": f"映射 #{m.get('no')}：{e}"})
        if rule and rule.get("mode") == "per_row":
            for src in m.get("sources") or []:
                if src.get("kind") in ("table", "docx_table") and mtype == "repeat":
                    issues.append({"level": "error", "code": "mode_mismatch", "mapping": m["id"],
                                   "message": f"映射 #{m.get('no')}：「每行一份」模式下每条样本只有一行，不能作为重复明细；如需把多行写入同一文档，请改用「每文件一份」或「每组一份」"})
    for tgt, ms in by_binding.items():
        if len(ms) > 1:
            nums = "、".join(f"#{m.get('no')}" for m in ms)
            name = next((s.get("name") for s in slots if (s.get("binding") or s["id"]) == tgt), tgt)
            issues.append({"level": "error", "code": "target_conflict", "mappings": [m["id"] for m in ms],
                           "message": f"映射 {nums} 同时写入字段「{name}」。请合并为一个多来源映射并设置拼接 / 换行 / 求和规则，或删除多余映射"})
    return issues


def _target(m: dict) -> str | None:
    return (m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")


def _needs_split(child: dict, block: dict) -> str:
    return (f"明细表「{child.get('name')}」在重复内容块「{block.get('name') or child.get('parent')}」里："
            "请在这个内容块的映射中设置分段字段，每一段才会只写入自己的明细行")


def build_plan(record: dict, rule: dict, srcs: dict[str, SourceDoc], mappings: list[dict], slots: list[dict]) -> Plan:
    plan = Plan(record)
    try:
        ctx = record_context(record, rule, srcs)
    except ResolveError as e:
        plan.issues.append({"level": "error", "code": e.code, "message": str(e)})
        return plan
    plan.issues.extend(static_check(mappings, slots, rule))
    bad_mappings = {i.get("mapping") for i in plan.issues if i["level"] == "error"}
    for i in plan.issues:
        for mid in i.get("mappings", []) or []:
            bad_mappings.add(mid)
    mapped_bindings: dict[str, dict] = {}
    mapping_of: dict[str, dict] = {}
    for m in mappings:
        if not m.get("enabled", True) or m["id"] in bad_mappings:
            continue
        tgt = (m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")
        try:
            fv, trace, warns = _resolve_mapping(m, ctx, srcs, slots, tgt)
            mapped_bindings[tgt] = fv
            mapping_of[tgt] = m
            for sl in slots:
                if (sl.get("binding") or sl["id"]) == tgt:
                    plan.trace[sl["id"]] = trace
            for w in warns:
                plan.issues.append({"level": "warning", "code": "transform", "mapping": m["id"], "message": f"映射 #{m.get('no')}：{w}"})
        except transforms.ConflictError as e:
            plan.issues.append({"level": "error", "code": "group_conflict", "mapping": m["id"],
                                "message": f"映射 #{m.get('no')}：{e}：{' / '.join(e.values[:6])}。请在映射中明确选择取值规则（取第一条、拼接、求和等）",
                                "values": e.values})
        except ResolveError as e:
            issue = {"level": "error", "code": e.code, "mapping": m["id"], "message": f"映射 #{m.get('no')}：{e}"}
            if e.detail:
                issue["detail"] = e.detail
            plan.issues.append(issue)
        except (transforms.TransformError, ValueError, ArithmeticError, TypeError) as e:
            # ArithmeticError: decimal.InvalidOperation on "Infinity"/"NaN"/overlong numbers is not
            # a ValueError; one bad cell must fail its own record with a located issue, not the call
            plan.issues.append({"level": "error", "code": "value_error", "mapping": m["id"], "message": f"映射 #{m.get('no')}：{e}"})
    _nest(slots, mapped_bindings, mappings, plan)
    # every slot gets a value: mapped value, explicit default, or empty – never the template's sample text
    for sl in slots:
        b = sl.get("binding") or sl["id"]
        cls = slot_kind_class(sl)
        fv = mapped_bindings.get(b)
        if fv is None:
            mapped = any(((m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")) == b
                         and m.get("enabled", True) for m in mappings)
            if mapped:
                continue  # mapping failed: already reported
            if sl.get("required") and cls == "single" and not _has_default(sl):
                plan.issues.append({"level": "error", "code": "unmapped_required", "slot": sl["id"],
                                    "message": f"必填字段「{sl.get('name')}」没有映射"})
                continue
            if cls == "single":
                fv = {"kind": "scalar", "value": V.empty()}
            elif cls == "region":
                fv = {"kind": "matrix", "rows": [[V.empty()] * int(sl.get("cols", 1)) for _ in range(int(sl.get("rows", 1)))]}
            else:
                fv = {"kind": "rows", "rows": []}
        want_kind = {"single": "scalar", "region": "matrix", "repeat": "rows"}[cls]
        if fv.get("kind") != want_kind:
            continue    # a shared binding across slot kinds: reported by static_check (binding_mixed)
        if cls == "single":
            val = fv["value"]
            if _blank(val) and _has_default(sl):
                # the field panel promises 「值为空时的默认值」: it stands in for an empty mapped
                # value as well as for a field nobody mapped
                val = V.text(str(sl["default"]))
            fmt_ops = sl.get("format") or []
            if fmt_ops:
                warns: list[str] = []
                try:
                    val = transforms.apply(val, fmt_ops, warns)
                except (transforms.TransformError, ValueError, ArithmeticError, TypeError) as e:
                    plan.issues.append({"level": "error", "code": "format_error", "slot": sl["id"],
                                        "message": f"字段「{sl.get('name')}」的格式设置无法应用：{e}"})
                    continue
                for w in warns:
                    plan.issues.append({"level": "warning", "code": "transform", "slot": sl["id"],
                                        "message": f"字段「{sl.get('name')}」：{w}"})
            fv = {**fv, "kind": "scalar", "value": val}
            if sl.get("required") and _blank(val):
                plan.issues.append({"level": "error", "code": "required_empty", "slot": sl["id"],
                                    "message": f"必填字段「{sl.get('name')}」的值为空"})
            elif _blank(val):
                # not blocking, but never silent: the document will show nothing here
                issue = {"level": "warning", "code": "optional_empty", "slot": sl["id"],
                         "message": f"字段「{sl.get('name')}」的值为空，生成的文档中该处将留空"}
                m = mapping_of.get(b)
                if m is not None:
                    issue["mapping"] = m["id"]
                plan.issues.append(issue)
        elif cls == "region":
            rows = fv["rows"]
            want_r, want_c = int(sl.get("rows", 0)), int(sl.get("cols", 0))
            got_r, got_c = len(rows), max((len(r) for r in rows), default=0)
            if (want_r, want_c) != (got_r, got_c):
                plan.issues.append({"level": "error", "code": "region_size", "slot": sl["id"],
                                    "message": f"区域字段「{sl.get('name')}」大小为 {want_r} 行 × {want_c} 列，来源数据为 {got_r} 行 × {got_c} 列，尺寸不一致。请调整来源区域或目标区域（不会自动截断、补齐或转置）"})
                continue
        elif cls == "repeat":
            if "groups" in fv and sl.get("empty_policy") == "error":
                for k, g in enumerate(fv["groups"]):
                    if not g:
                        sec = (fv.get("sections") or [])[k] if k < len(fv.get("sections") or []) else str(k + 1)
                        plan.issues.append({"level": "error", "code": "no_detail", "slot": sl["id"],
                                            "message": f"明细表「{sl.get('name')}」在「{sec}」这一段没有明细行"})
                block = next((x for x in slots if x["id"] == sl.get("parent")), {})
                if not fv["groups"] and block.get("empty_policy") == "placeholder":
                    # no section at all: the block's placeholder copy holds this table, with no row
                    plan.issues.append({"level": "error", "code": "no_detail", "slot": sl["id"],
                                        "message": f"重复明细「{sl.get('name')}」没有数据行"})
            elif not fv["rows"] and sl.get("empty_policy") == "error":
                plan.issues.append({"level": "error", "code": "no_detail", "slot": sl["id"],
                                    "message": f"重复明细「{sl.get('name')}」没有数据行"})
            lay = sl.get("layout")
            if lay and sl.get("overflow", "error") == "error":     # PDF area with a fixed number of rows
                cap = max(0, int((lay["trect"][3] - lay["trect"][1]) / lay["row_height"] + 1e-6))
                if len(fv["rows"]) > cap:
                    plan.issues.append({"level": "error", "code": "repeat_overflow", "slot": sl["id"],
                                        "message": f"明细「{sl.get('name')}」有 {len(fv['rows'])} 行，PDF 区域只能容纳 {cap} 行。可在字段设置中把「超出时」改为「复制本页续写」或「压缩行高」"})
        plan.values[sl["id"]] = fv
    return plan


def _blank(val: V.Value) -> bool:
    return val.is_empty or (val.t == V.STR and not str(val.v).strip())


def _has_default(sl: dict) -> bool:
    d = sl.get("default")
    # 0 is a real default; only None / "" mean "no default"
    return d is not None and str(d) != ""


def _sources_for(ctx: dict) -> list[SourceDoc]:
    if ctx["mode"] == "per_group":
        return ctx.get("srcs") or [ctx["src"]]
    return [ctx["src"]]


def _loc_for(m: dict, loc: dict, src: SourceDoc) -> dict:
    overrides = m.get("overrides") or {}
    ov = overrides.get(src.file_id)
    if ov is None and "~" in src.file_id:
        # a per-block sample "<file id>~<n>": the override was set on the real file and,
        # like the mapping's own locator, addresses each block relative to its start
        ov = overrides.get(src.file_id.rpartition("~")[0])
    if ov and isinstance(ov, dict):
        idx = ov.get("source_index", 0)
        if ov.get("locator") and (m.get("sources") or [None])[idx] is loc:
            return ov["locator"]
    return loc


def _resolve_mapping(m: dict, ctx: dict, srcs, slots, binding):
    mtype = m.get("type", "single")
    warns: list[str] = []
    trace = {"mapping": m["id"], "no": m.get("no"), "sources": []}
    if mtype == "single":
        vals = []
        for loc0 in m.get("sources") or []:
            v = _scalar_for_record(m, loc0, ctx, trace)
            vals.append(v)
        if not vals:
            raise ResolveError("no_source", "映射没有来源")
        if len(vals) > 1:
            comb = m.get("combine") or {}
            val = transforms.combine(vals, comb.get("op", "concat"), comb.get("sep", " "))
        else:
            val = vals[0]
        val = transforms.apply(val, m.get("transforms"), warns)
        if m.get("value_mode") == "display":
            val = V.Value(V.STR, val.text, val.text, None, val.src)
        if m.get("style") == "source" and vals and vals[0].style:
            val.style = vals[0].style
        trace["value"] = val.text
        return {"kind": "scalar", "value": val, "value_mode": m.get("value_mode", "auto")}, trace, warns
    if mtype == "region":
        loc0 = (m.get("sources") or [None])[0]
        if loc0 is None:
            raise ResolveError("no_source", "映射没有来源")
        srcs_ = _sources_for(ctx)
        if len(srcs_) > 1:
            mats = [extract_matrix(s, _loc_for(m, loc0, s), ctx) for s in srcs_]
            if any([[c.text for c in r] for r in x] != [[c.text for c in r] for r in mats[0]] for x in mats[1:]):
                raise transforms.ConflictError("同一组来自多个文件的区域数据不一致", [s.filename for s in srcs_])
            mat = mats[0]
        else:
            s = srcs_[0]
            mat = extract_matrix(s, _loc_for(m, loc0, s), ctx)
        ops = m.get("transforms")
        if ops:
            mat = [[transforms.apply(c, ops, warns) for c in row] for row in mat]
        trace["sources"].append({"kind": loc0.get("kind"), "range": loc0.get("range")})
        return {"kind": "matrix", "rows": mat}, trace, warns
    if mtype == "repeat":
        loc0 = (m.get("sources") or [None])[0]
        if loc0 is None:
            raise ResolveError("no_source", "映射没有来源")
        if ctx["mode"] == "per_group":
            used = loc0
            heads, rows = extract_detail(ctx["src"], loc0, ctx)
        else:
            s = ctx["src"]
            used = _loc_for(m, loc0, s)
            heads, rows = extract_detail(s, used, ctx)
        out_rows = []
        cols = m.get("columns") or []
        if not cols:
            raise ResolveError("no_columns", "重复明细映射还没有设置列对应关系")
        missing = []
        head_count: dict[str, int] = {}
        for h in heads:
            head_count[h] = head_count.get(h, 0) + 1
        from .resolve import missing_in_group
        partly: list[str] = []
        for col in cols:
            src_h = tabular.normalize_header((col.get("source") or {}).get("header", ""))
            if col.get("source", {}).get("kind") == "constant":
                continue
            if src_h not in head_count:
                missing.append((col.get("source") or {}).get("header", ""))
            elif head_count[src_h] > 1:
                raise ResolveError("ambiguous_column", f"明细表中有 {head_count[src_h]} 个同名列「{src_h}」，请改名后再映射")
            else:
                lacking = missing_in_group(ctx, src_h)
                if lacking:
                    partly.append(f"「{src_h}」（{'、'.join(lacking[:3])}）")
        for k in (m.get("group_by") or {}).get("keys") or []:
            key_h = tabular.normalize_header((k or {}).get("header", ""))
            lacking = missing_in_group(ctx, key_h) if key_h in head_count else []
            if lacking:                 # a merged file without the split column is not "an empty key"
                partly.append(f"「{key_h}」（{'、'.join(lacking[:3])}）")
        if missing:
            raise ResolveError("missing_column", f"明细表中找不到列：{'、'.join(missing)}")
        if partly:
            raise ResolveError("missing_column", f"跨文件分组中，这些列在部分文件里不存在：{'；'.join(partly)}。"
                                                 f"这些文件的明细行不会被填成空白，请统一各文件的表头")
        if m.get("group_by"):
            fv, trace, warns = _sectioned_rows(m, cols, rows, head_count, warns, trace)
            return {**fv, "row_ids": _row_ids(rows), "read": used}, trace, warns
        blank: list[int] = []
        read = [c for c in cols if (c.get("source") or {}).get("kind") != "constant"]
        for idx, row in enumerate(rows):
            o = {}
            for col in cols:
                srcd = col.get("source") or {}
                if srcd.get("kind") == "constant":
                    v = V.text(srcd.get("value", ""))
                else:
                    v = row.get(tabular.normalize_header(srcd.get("header", "")), V.empty())
                v = transforms.apply(v, col.get("transforms"), warns)
                if col.get("value_mode") == "display":
                    v = V.Value(V.STR, v.text, v.text, None, v.src)
                o[col["slot_col"]] = v
            out_rows.append(o)
            # every mapped (non-constant) column empty: dropped when this table is nested in a block
            if read and all(_blank(o[c["slot_col"]]) for c in read):
                blank.append(idx)
        trace["sources"].append({"kind": loc0.get("kind"), "rows": len(out_rows)})
        trace["value"] = f"{len(out_rows)} 行明细"
        return {"kind": "rows", "rows": out_rows, "blank": blank, "source_rows": len(rows), "row_ids": _row_ids(rows), "read": used}, trace, warns
    raise ResolveError("bad_mapping", f"未知的映射类型：{mtype}")


def _sectioned_rows(m: dict, cols: list[dict], rows: list[dict], head_count: dict, warns: list[str], trace: dict):
    """A repeat content block whose mapping sets 分段字段 (`group_by`): one block copy per
    sub-group of the detail rows, in the order each sub-group first appears (the sample rule's
    sort applies first); the block's own columns take one value per sub-group (`agg`, default
    `unique`: two different values are a conflict, not a guess). `partition` tells the nested
    tables inside the block which rows belong to which copy (docs-nested-details.md §1, §3)."""
    gb = m.get("group_by") or {}
    keys = [tabular.normalize_header((k or {}).get("header", "")) for k in gb.get("keys") or []]
    if not keys or not all(keys):
        raise ResolveError("bad_group_by", "请为这个重复内容块选择分段字段")
    lacking = [k for k in keys if k not in head_count]
    if lacking:
        raise ResolveError("missing_column", f"明细表中找不到分段字段：{'、'.join(lacking)}")
    twice = [k for k in keys if head_count[k] > 1]
    if twice:
        raise ResolveError("ambiguous_column", f"明细表中有多个同名列「{twice[0]}」，请改名后再用作分段字段")
    parts: dict[tuple, list[int]] = {}
    order: list[tuple] = []
    empty: list[int] = []
    for idx, row in enumerate(rows):
        kv = tuple((row.get(k) or V.empty()).text.strip() for k in keys)
        if not any(kv):
            empty.append(idx)
            continue
        if kv not in parts:
            parts[kv] = []
            order.append(kv)
        parts[kv].append(idx)
    skipped: list[int] = []
    if empty:
        if gb.get("empty_key", "error") == "skip":
            skipped = empty
            warns.append(f"{len(empty)} 行明细的分段字段为空，已跳过")
        else:
            def where(i: int) -> str:              # the source cell when known, else the row's place in the detail table
                src = (rows[i].get(keys[0]) or V.empty()).src or {}
                return f"{src['sheet']}!{src['cell']}" if src.get("cell") and src.get("sheet") else f"第 {i + 1} 行"
            cells = "、".join(where(i) for i in empty[:5]) + (" 等" if len(empty) > 5 else "")
            raise ResolveError("subgroup_key_empty", f"{len(empty)} 行明细的分段字段为空：{cells}。请补全，或在映射中选择跳过这些行")
    out_rows = []
    for kv in order:
        members = [rows[i] for i in parts[kv]]
        o = {}
        for col in cols:
            srcd = col.get("source") or {}
            if srcd.get("kind") == "constant":
                v = V.text(srcd.get("value", ""))
            else:
                h = tabular.normalize_header(srcd.get("header", ""))
                vals = [r.get(h, V.empty()) for r in members]
                how = col.get("agg") or "unique"
                if how == "count":
                    # rows of this section where the column has a value: an order row without
                    # lines (dropped from the nested table) is not counted as a line
                    n = sum(1 for x in vals if not _blank(x))
                    v = V.Value(V.INT, str(n), str(n))
                else:
                    try:
                        v = transforms.aggregate(vals, how)
                    except transforms.ConflictError as e:
                        raise transforms.ConflictError(f"「{' / '.join(kv)}」这一段里「{h}」有多个不同值", e.values) from None
                    fmts = {x.fmt for x in vals if not x.is_empty}
                    if how == "sum" and not v.is_empty and len(fmts) == 1 and next(iter(fmts)):
                        # a total reads like the numbers it adds up (their shared number format)
                        from .numfmt import format_value
                        f = next(iter(fmts))
                        v = V.Value(v.t, v.v, format_value(Decimal(v.v), f)[0], f)
            v = transforms.apply(v, col.get("transforms"), warns)
            if col.get("value_mode") == "display":
                v = V.Value(V.STR, v.text, v.text, None, v.src)
            o[col["slot_col"]] = v
        out_rows.append(o)
    trace["sources"].append({"kind": ((m.get("sources") or [{}])[0]).get("kind"), "rows": len(rows)})
    trace["value"] = f"{len(out_rows)} 段，{sum(len(parts[k]) for k in order)} 行明细"
    return {"kind": "rows", "rows": out_rows, "partition": [parts[k] for k in order], "sections": [" / ".join(k) for k in order],
            "skipped": skipped, "source_rows": len(rows)}, trace, warns


def _row_ids(rows: list[dict]) -> list[tuple] | None:
    """Where each detail row was read from — (file, sheet, row number) — when the source says so;
    None when it does not (a table inside a Word document)."""
    out = []
    for row in rows:
        rid = None
        for v in row.values():
            src = getattr(v, "src", None) or {}
            hit = re.match(r"[A-Z]+(\d+)$", str(src.get("cell") or ""))
            if hit:
                rid = (src.get("file_id"), src.get("sheet"), int(hit.group(1)))
                break
        if rid is None:
            return None
        out.append(rid)
    return out


def _nest(slots: list[dict], mapped: dict[str, dict], mappings: list[dict], plan: "Plan") -> None:
    """Each detail table inside a repeat content block gets its rows split by the block's
    sub-groups (`groups[i]`: the rows of copy i). A row goes to the copy whose sub-group read the
    same source row (file, sheet, row); a row the block did not read is refused, never placed by
    position. Refused as well: a block without 分段字段, and a nested table sharing its binding."""
    by_id = {s["id"]: s for s in slots}
    for sl in slots:
        pid = sl.get("parent")
        if not pid:
            continue
        cb = sl.get("binding") or sl["id"]
        block = by_id.get(pid) or {}
        pb = block.get("binding") or pid
        bname = block.get("name") or pid
        if sum(1 for x in slots if (x.get("binding") or x["id"]) == cb) > 1:
            plan.issues.append({"level": "error", "code": "nested_shared_binding", "slot": sl["id"],
                                "message": f"明细表「{sl.get('name')}」在重复内容块「{bname}」里，不能与其他字段共用同一个绑定：请先取消共用绑定"})
            mapped.pop(cb, None)
            continue
        child = mapped.get(cb)
        if child is None or child.get("kind") != "rows":
            continue
        pfv = mapped.get(pb)
        if pfv is None and any(_target(pm_) == pb and pm_.get("enabled", True) and pm_.get("group_by") for pm_ in mappings):
            mapped.pop(cb, None)            # the block's own mapping failed: already reported, nothing to split by
            continue
        if pfv is None or "partition" not in pfv:
            plan.issues.append({"level": "error", "code": "nested_needs_group_by", "slot": sl["id"], "message": _needs_split(sl, block)})
            mapped.pop(cb, None)
            continue
        blank = set(child.get("blank") or [])
        groups: list[list[dict]] = [[] for _ in pfv["partition"]]
        c_ids, p_ids = child.get("row_ids"), pfv.get("row_ids")
        if c_ids is not None and p_ids is not None:
            where = {p_ids[i]: k for k, part in enumerate(pfv["partition"]) for i in part}
            skipped = {p_ids[i] for i in pfv.get("skipped") or []}
            ok = True
            for j, row in enumerate(child["rows"]):
                if j in blank or c_ids[j] in skipped:
                    continue
                k = where.get(c_ids[j])
                if k is None:
                    ok = False             # a detail row the block never read (another range, sheet or filter)
                    break
                groups[k].append(row)
        else:
            # no row provenance (a table in a Word document): both must read the very same rows
            same = lambda loc: {k: v for k, v in (loc or {}).items() if k != "headers"}  # noqa: E731
            ok = same(child.get("read")) == same(pfv.get("read")) and child.get("source_rows") == pfv.get("source_rows")
            if ok:
                groups = [[child["rows"][i] for i in part if i not in blank and i < len(child["rows"])] for part in pfv["partition"]]
        if not ok:
            plan.issues.append({"level": "error", "code": "nested_source_mismatch", "slot": sl["id"],
                                "message": f"明细表「{sl.get('name')}」与重复内容块「{bname}」读取的明细行不一致：请让两者使用同一张来源表格的同一范围"})
            mapped.pop(cb, None)
            continue
        mapped[cb] = {**child, "groups": groups, "sections": pfv.get("sections") or []}


def _scalar_for_record(m: dict, loc: dict, ctx: dict, trace: dict) -> V.Value:
    mode = ctx["mode"]
    kind = loc.get("kind")
    sctx = dict(ctx)
    sctx["mapping_id"] = m["id"]
    sctx["mapping_sources"] = len(m.get("sources") or [])
    if mode == "per_group" and kind == "column":
        vals = []
        for s, tv, r in ctx["group_rows"]:
            from .resolve import column_index
            c = column_index(tv, loc, s)
            vals.append(V.Value(**{**tv.sheet.cell(r, c).__dict__, "src": {"file_id": s.file_id, "file": s.filename,
                                                                         "sheet": tv.sheet.name, "cell": tabular.cell_ref(r, c)}}))
        how = m.get("agg") or "unique"
        v = transforms.aggregate(vals, how, (m.get("agg_sep") or "、"))
        trace["sources"].append({"kind": "column", "header": loc.get("header"), "agg": how, "rows": len(vals)})
        return v
    if mode == "per_group" and kind not in ("constant", "record_meta"):
        vals = []
        for s in ctx.get("srcs") or [ctx["src"]]:
            vals.append(extract_scalar(s, _loc_for(m, loc, s), sctx))
        how = m.get("agg") or "unique"
        v = transforms.aggregate(vals, how, (m.get("agg_sep") or "、")) if len(vals) > 1 else vals[0]
        trace["sources"].append({"kind": kind, "files": len(vals)})
        return v
    s = ctx["src"]
    v = extract_scalar(s, _loc_for(m, loc, s), sctx)
    trace["sources"].append({"kind": kind, **({"cell": v.src.get("cell")} if v.src and v.src.get("cell") else {}),
                             "file": s.filename})
    return v
