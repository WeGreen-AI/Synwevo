/** What a live table says to the person, in their language (docs-sheet-live.md §5: "拒绝码由客户端按
 *  code + detail 组句"): the sync status, why the room closed, each refusal and conflict with whose
 *  value it was, what undo skipped, what others just changed (for screen readers). Only codes,
 *  numbers, names and cell references come from the server — never its prose. */
import { t } from "@synwevo/ui/i18n-core";
import type { Attention, Announcement, OpLabel, Person, SyncStatus } from "./sheetsync.ts";
import type { Skipped } from "./sheetundo.ts";

/** "Sheet!A1" when the table has more than one sheet, "A1" otherwise (and the sheet alone without a cell). */
export function whereText(label: { sheet: string | null; ref: string | null }, sheets = 2): string {
  if (label.ref) return label.sheet && sheets > 1 ? `${label.sheet}!${label.ref}` : label.ref;
  return label.sheet ? t("工作表「{v1}」", { v1: label.sheet }) : t("表格");
}

export function statusText(status: SyncStatus, pending: number): string {
  switch (status) {
    case "live": return t("实时协同中");
    case "syncing": return t("同步中（{count}）", { count: pending });
    case "offline": return pending ? t("离线：{count} 处修改待同步", { count: pending }) : t("离线：正在重新连接");
    case "single": return t("单人编辑");
    default: return t("连接中…");
  }
}

/** Why the room closed this connection for good (44xx), as one sentence. */
export function closeText(code: number, reason: string): string {
  if (code === 4409) return reason === "superseded" ? t("这个标签页的实时连接已被另一个连接接管；请刷新页面继续。") : t("表格已被恢复或替换；请刷新页面继续。");
  if (code === 4410) return t("实时协同不可用：模板不存在或已删除。");
  if (code === 4429) return reason === "room_full" ? t("这张表格同时编辑的标签页已满；请稍后刷新再试。") : t("你在这张表格上打开的可编辑标签页太多（最多 8 个）；请关闭其他标签页后刷新。");
  if (code === 4401) return t("登录已失效：实时协同已停止，请重新登录后刷新。");
  if (code === 4403) return t("实时协同不可用：你在该工作空间的权限不允许加入编辑。");
  if (code === 4404) return t("实时协同不可用：模板不存在或已删除。");
  return t("实时协同不可用（{v1}）。", { v1: reason || String(code) });
}

/** A close the browser retries by itself (not 44xx): what the person sees while it does. */
export function retryText(code: number): string {
  if (code === 4503) return t("表格正在写入上一位成员的修改，稍后自动重新连接。");
  if (code === 1011) return t("服务器暂时出错，正在自动重新连接。");
  return t("连接中断，正在自动重新连接。");
}

/** Why the room is not live (hello.live_reason / the live frame). */
export function liveReasonText(reason: any): string {
  if (reason && reason.code === "too_large" && reason.what === "cells") return t("这张表格有 {size} 个单元格，超过实时协同的上限（{max}）：改为保存时合并。", { size: reason.size ?? "?", max: reason.max ?? "?" });
  if (reason && reason.code === "too_large") return t("这张表格的内容超过实时协同的大小上限：改为保存时合并。");
  return t("这张表格暂时不能实时协同：改为保存时合并。");
}

const personName = (people: Record<string, Person>, id: any): string | null => (typeof id === "string" ? people[id]?.name ?? null : null);
function whoText(people: Record<string, Person>, id: any): string {
  return personName(people, id) || t("其他成员");
}
function peopleText(people: Record<string, Person>, ids: any): string {
  const names = (Array.isArray(ids) ? ids : []).map((id) => personName(people, id)).filter((x): x is string => !!x);
  return names.length ? names.join("、") : t("其他成员");
}

function valueText(x: any): string {
  if (x && typeof x === "object" && !Array.isArray(x)) {
    if ("v" in x) return x.v === null || x.v === undefined ? "" : String(x.v);
    if ("name" in x && typeof x.name === "string") return x.name;
    if ("ref" in x && typeof x.ref === "string") return x.ref;
    return "";
  }
  return x === null || x === undefined ? "" : String(x);
}

const PART: Record<string, string> = { v: "值", t: "类型", s: "格式", sz: "字号", color: "文字颜色", bg: "底色", ha: "水平对齐", va: "垂直对齐", b: "格式", i: "格式", u: "格式", wrap: "格式", bd: "格式", nf: "数字格式" };
const THING: Record<string, string> = { row: "行", col: "列", sheet: "工作表", merge: "合并区域", field: "字段" };

/** One refusal of the server (or the tab's prediction of it) as a sentence. */
export function refusalText(code: string, d: Record<string, any>, people: Record<string, Person>, label: OpLabel, sheets = 2): string {
  const where = whereText(label, sheets);
  switch (code) {
    case "conflict":
      return t("{v1} 已先修改了 {v2}（现在是「{v3}」）。", { v1: whoText(people, d.by), v2: where, v3: valueText(d.theirs) });
    case "covered":
      return t("{v1} 在 {v2} 合并的 {v3} 内，只能在合并区域的左上角写入值。", { v1: d.ref || where, v2: whoText(people, d.by), v3: d.range || "" });
    case "merge_overlap":
      return t("无法合并 {v1}：它与 {v2} 合并的 {v3} 重叠。", { v1: d.want || where, v2: whoText(people, d.by), v3: d.range || "" });
    case "covers_values":
      return t("无法合并 {v1}：会隐藏 {count} 个有值的单元格（{v2}），请先清空它们。", { v1: d.want || where, count: d.count ?? 0, v2: (Array.isArray(d.cells) ? d.cells : []).join("、") });
    case "merged_inner":
    case "merged":
    case "vertical_merge":
    case "field_overlap":
    case "duplicate_repeat":
      return placementText(code, d, people, where);
    case "id_taken":
      return d.what === "field" ? t("字段 ID「{v1}」已被字段「{v2}」使用。", { v1: d.id ?? "", v2: d.name ?? d.id ?? "" }) : t("内部编号恰好冲突，请再做一次。");
    case "sheet_name_taken":
      return t("已有名为「{v1}」的工作表（名称不区分大小写）。", { v1: d.name ?? "" });
    case "sheet_deleted":
      return t("工作表「{v1}」已被 {v2} 删除，修改未应用。", { v1: d.name ?? label.sheet ?? "", v2: whoText(people, d.by) });
    case "last_sheet":
      return t("至少要保留一个工作表。");
    case "not_found":
      return t("要修改的{v1}已不存在（可能已被其他成员删除或移动）。", { v1: t(THING[d.what] || "对象") });
    case "too_large":
      return tooLargeText(d);
    case "invalid":
      return invalidText(d, where);
    case "resync":
      return t("表格在你看到之后已保存并整理过，{v1} 的修改未应用；请确认后再做一次。", { v1: where });
    case "read_only":
      return t("你现在只能查看这个模板，{v1} 的修改未应用。", { v1: where });
    case "not_live":
      return t("表格已退出实时协同，{v1} 的修改未应用。", { v1: where });
    case "reset":
      return t("表格已从已保存的版本重新开始，{v1} 的修改未应用。", { v1: where });
    case "closed":
      return t("实时连接已断开，{v1} 的修改没有同步。", { v1: where });
    case "unknown":
      return t("无法确认 {v1} 的修改是否已生效（连接中断期间服务器已不记得这个标签页）；请核对后决定是否再做一次。", { v1: where });
    case "error":
      return t("服务器处理 {v1} 的修改时出错，修改未应用。", { v1: where });
    default:
      return t("{v1} 的修改未应用（{v2}）。", { v1: where, v2: code });
  }
}

function placementText(code: string, d: Record<string, any>, people: Record<string, Person>, where: string): string {
  // a merge refused because of a field names the field; a field refused because of a merge names the merge
  if (d.field !== undefined && d.want !== undefined) {
    const f = d.name || d.field || "";
    if (code === "merged_inner") return t("无法合并 {v1}：字段「{v2}」（{v3}）会落在合并区域内部。", { v1: d.want, v2: f, v3: d.ref ?? "" });
    if (code === "merged") return t("无法合并 {v1}：它与区域字段「{v2}」（{v3}）相交。", { v1: d.want, v2: f, v3: d.ref ?? "" });
    return t("无法合并 {v1}：它会纵向跨过重复明细行「{v2}」（{v3}）。", { v1: d.want, v2: f, v3: d.ref ?? "" });
  }
  const at = d.ref || where;
  if (code === "merged_inner") return t("无法把 {v1} 设为字段：它在 {v2} 合并的 {v3} 内（请用左上角）。", { v1: at, v2: whoText(people, d.by), v3: d.range ?? "" });
  if (code === "merged") return t("区域字段 {v1} 不能包含合并区域 {v2}。", { v1: at, v2: d.range ?? "" });
  if (code === "vertical_merge") return t("重复明细行 {v1} 与纵向合并的 {v2} 相交。", { v1: at, v2: d.range ?? "" });
  if (code === "duplicate_repeat") return t("这一行已有重复明细行「{v1}」。", { v1: d.name ?? d.field ?? "" });
  return t("字段位置 {v1} 与字段「{v2}」重叠。", { v1: at, v2: d.name ?? d.field ?? "" });
}

function tooLargeText(d: Record<string, any>): string {
  const max = d.max ?? "?";
  switch (d.what) {
    case "value": return t("单元格内容超过 {max} 个字符。", { max });
    case "formula": return t("公式超过 {max} 个字符。", { max });
    case "cells": return t("表格已达到 {max} 个单元格的上限。", { max });
    case "grid": return t("行列总数已达上限（{max}）。", { max });
    case "rows": return t("行数已达上限（{max}）。", { max });
    case "cols": return t("列数已达上限（{max}）。", { max });
    case "merge": return t("合并区域过大（最多 {max} 个单元格）。", { max });
    case "tx": return t("一次操作的步骤过多（最多 {max} 步）。", { max });
    case "sheets": return t("工作表数量已达上限（{max}）。", { max });
    case "table": return t("表格已达到实时协同的大小上限：现在只能删减内容。");
    default: return t("内容超过上限。");
  }
}

function invalidText(d: Record<string, any>, where: string): string {
  switch (d.reason) {
    case "cell": return t("{v1} 的{v2}无效。", { v1: where, v2: t(PART[d.field] || "内容") });
    case "unwritable": return t("{v1} 的内容含有无法写入 Excel 文件的字符。", { v1: where });
    case "number_format": return t("数字格式无效（最多 255 个字符）。");
    case "style": return t("{v1} 的格式无效。", { v1: where });
    case "size": return t("行高或列宽须在 {min}–{max} 之间。", { min: d.min ?? 1, max: d.max ?? 2000 });
    case "not_in_grid": return t("这一行或列不在表格范围内。");
    case "single_cell_merge": return t("至少选择两个单元格才能合并。");
    case "sheet_name": return t("工作表名称无效：不能为空，首尾不能有空格或单引号，不能含 \\ / * ? : [ ]，最长 {max} 个字符，也不能是 History。", { max: d.max ?? 31 });
    case "field_name": return t("字段名称不能为空，最长 {max} 个字符。", { max: d.max ?? 100 });
    case "field_id": case "field": return t("字段设置无效。");
    case "count": return t("一次最多添加 {max} 行或列。", { max: d.max ?? 5000 });
    default: return t("这次修改无效（{v1}）。", { v1: String(d.reason ?? "invalid") });
  }
}

/** One "needs you" item: what happened, in a sentence. */
export function attentionText(a: Attention, sheets = 2): string {
  if (a.kind === "overwritten") {
    return t("{v1} 保存的新版本替换了你在 {v2} 的值。", { v1: whoText(a.people, a.detail.by), v2: whereText(a.label, sheets) });
  }
  return refusalText(a.code, a.detail, a.people, a.label, sheets);
}

/** What undo left alone, and why. */
export function skippedText(s: Skipped, sheets = 2): string {
  if (s.reason === "append") return t("新增的行和列不能撤销。");
  if (s.reason === "idless") return t("没有 ID 的字段「{v1}」不能恢复。", { v1: s.field ?? "" });
  const where = s.field ? t("字段「{v1}」", { v1: s.field }) : whereText({ sheet: s.sheet, ref: s.ref }, sheets);
  return t("未撤销 {v1}：其他成员之后改过它。", { v1: where });
}

/** Others' changes for screen readers. */
export function announcementText(a: Announcement): string {
  const who = a.by || t("其他成员");
  return a.count > 1 ? t("{v1} 修改了 {count} 个单元格（{v2} 等）", { v1: who, count: a.count, v2: a.where }) : t("{v1} 修改了 {v2}", { v1: who, v2: a.where });
}

/** The answer of the room's save (POST …/collab/save), or its refusal, as one sentence and a tone. */
export function saveText(res: any): [string, "ok" | "info"] {
  const no = res?.version?.no ?? res?.head ?? "?";
  if (res?.unchanged) return [t("内容与已保存的第 {v1} 版相同，无需新版本。", { v1: no }), "info"];
  if (res?.live?.pending) return [t("已保存为第 {v1} 版；表格稍后标记为已保存。", { v1: no }), "ok"];
  return [t("已保存为第 {v1} 版。", { v1: no }), "ok"];
}
export function saveRefusalText(e: any): string | null {
  if (e?.code === "not_live") return t("表格已不在实时协同中，不能这样保存；请刷新页面。");
  if (e?.code === "stale_template") return t("刚有人保存了新版本，表格正在合并它；请稍后再保存。");
  return null;
}
