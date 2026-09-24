"""Execution plan: resolve every mapping for one record into slot values.

``build_plan`` is the single code path used by preview and by batch generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
            return {"kind": "rows", "rows": [{k: v.to_json() for k, v in row.items()} for row in fv["rows"]]}
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
            if not fv["rows"] and sl.get("empty_policy") == "error":
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
            heads, rows = extract_detail(ctx["src"], loc0, ctx)
        else:
            s = ctx["src"]
            heads, rows = extract_detail(s, _loc_for(m, loc0, s), ctx)
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
        if missing:
            raise ResolveError("missing_column", f"明细表中找不到列：{'、'.join(missing)}")
        if partly:
            raise ResolveError("missing_column", f"跨文件分组中，这些列在部分文件里不存在：{'；'.join(partly)}。"
                                                 f"这些文件的明细行不会被填成空白，请统一各文件的表头")
        for row in rows:
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
        trace["sources"].append({"kind": loc0.get("kind"), "rows": len(out_rows)})
        trace["value"] = f"{len(out_rows)} 行明细"
        return {"kind": "rows", "rows": out_rows}, trace, warns
    raise ResolveError("bad_mapping", f"未知的映射类型：{mtype}")


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
