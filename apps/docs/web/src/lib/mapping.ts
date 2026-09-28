/** Applying the settings chosen in the mapping dialog to a mapping that already exists.
 *
 *  Two paths used to drop what the dialog showed: merging a second source into an existing
 *  mapping kept the old transforms/aggregation/value mode/style and ignored the ones on screen,
 *  and editing spread `enabled: true` over a disabled mapping (re-enabling it silently) while a
 *  style switched back to 「沿用目标模板样式」 stayed "source". This is the one place both paths
 *  go through now.
 *
 *  No imports except types (erased): tested with `node --test`.
 */
import type { GroupBy, Mapping } from "./types";

export interface MappingSettings {
  transforms?: any[];
  agg?: string;
  agg_sep?: string;
  value_mode?: string;
  style?: "target" | "source";
  columns?: Mapping["columns"];
  /** a repeat content block's 分段字段; null removes it, undefined leaves it as it is */
  group_by?: GroupBy | null;
}

export function withSettings(m: Mapping, s: MappingSettings): Mapping {
  const out: any = { ...m };
  if (s.transforms !== undefined) out.transforms = s.transforms;
  if (s.columns !== undefined) out.columns = s.columns;
  for (const k of ["agg", "agg_sep", "value_mode"] as const) {
    if (s[k] !== undefined) out[k] = s[k];
  }
  if (s.style === "source") out.style = "source";
  else if (s.style === "target") delete out.style;
  if (s.group_by) out.group_by = s.group_by;
  else if (s.group_by === null) delete out.group_by;
  return out as Mapping;
}

/** A block split by 分段字段 takes one value per sub-group for each of its columns (`agg`, default
 *  「必须一致」); without a split the columns carry none. Constants need no rule. */
export function sectionColumns(columns: NonNullable<Mapping["columns"]>, aggs: Record<string, string>, split: boolean) {
  return columns.map((c) => {
    const out: any = { ...c };
    if (split && c.source?.kind !== "constant") out.agg = aggs[c.slot_col] || "unique";
    else delete out.agg;
    return out;
  });
}

/** Add one more source to an existing single-value mapping, with the dialog's settings. */
export function mergeSource(existing: Mapping, loc: any, combine: { op: string; sep?: string }, s: MappingSettings): Mapping {
  return withSettings({ ...existing, sources: [...existing.sources, loc], combine }, s);
}

/** Keep constants, duplicate-header occurrence and column transforms when an edit leaves the
 *  source column unchanged. Changing the header replaces just the locator, not its transforms. */
export function repeatColumns(keys: string[], headers: Record<string, string>, previous: Mapping["columns"] = []) {
  return keys.map((key) => {
    const old = previous?.find((c) => c.slot_col === key);
    const header = headers[key] || "";
    const unchanged = old && (old.source?.kind === "constant" ? !header : old.source?.header === header);
    return { ...old, slot_col: key, source: unchanged ? old.source : header ? { header } : { kind: "constant", value: "" } };
  });
}
