/** The template editor's refusals, placed next to the input they concern.
 *
 *  A refused template comes back as `invalid_input` with `detail.fields`: one `{loc, type}` per
 *  error, where `loc` is the input's path in the operation's input (`definition.fields.<n>.minimum`,
 *  `definition.fields.<n>.rule.prefix`, `title`). This turns that into a catalogue key per input
 *  (`fields[n]["rule.prefix"]`) so the editor words each one beside its input, not as one generic
 *  sentence. Kept free of React so it is tested on its own (tests/extract-editor-errors.test.ts). */

export interface EditorProblems {
  /** Field index → input path inside the field (`minimum`, `rule.prefix`, `rule` …) → catalogue key. */
  fields: Record<number, Record<string, string>>;
  /** Catalogue key for a refused template name, if it was. */
  title?: string;
  /** True when every error could be placed beside an input. */
  placed: boolean;
}

const DECIMAL = "请填写不带指数的十进制数，例如 1000 或 -2.5";
/** By error type, then by input where the same type means different things. */
const BY_TYPE: Record<string, string> = {
  string_pattern_mismatch: DECIMAL,
  bounds_order: "最大值不能小于最小值",
  prefix_required: "请填写行首文字（不能只有空格）",
  header_required: "请填写列标题（不能只有空格）",
  string_too_short: "请填写此项",
  missing: "请填写此项",
  string_too_long: "内容超过允许的长度",
  greater_than_equal: "请填写允许范围内的整数",
  less_than_equal: "请填写允许范围内的整数",
  int_parsing: "请填写允许范围内的整数",
  int_from_float: "请填写允许范围内的整数",
};
export const FALLBACK = "此项不符合要求，请检查后重新保存";

/** Every catalogue key this module can return (the catalogue test holds them to three languages). */
export const EDITOR_ERROR_KEYS = [...new Set([...Object.values(BY_TYPE), FALLBACK, "请修正标出的字段后再保存"])];

export function editorProblems(error: unknown): EditorProblems | null {
  const failure = error as { code?: unknown; detail?: { fields?: unknown } } | null;
  if (!failure || failure.code !== "invalid_input" || !Array.isArray(failure.detail?.fields)) return null;
  const out: EditorProblems = { fields: {}, placed: true };
  for (const item of failure.detail.fields as { loc?: unknown; type?: unknown }[]) {
    const loc = Array.isArray(item?.loc) ? item.loc.map(String) : [];
    const key = BY_TYPE[String(item?.type)] ?? FALLBACK;
    if (loc[0] === "title" && loc.length === 1) { out.title ??= key; continue; }
    const index = Number(loc[2]);
    if (loc[0] !== "definition" || loc[1] !== "fields" || loc.length < 3 || !Number.isInteger(index)) { out.placed = false; continue; }
    // A whole-field refusal (no input below the field) is shown at the top of that field.
    const input = loc.slice(3).join(".") || "field";
    (out.fields[index] ??= {})[input] ??= key;
  }
  return out;
}

/** The refusal for one input of field `index`, if there is one. */
export const problemAt = (problems: EditorProblems | null, index: number, input: string) => problems?.fields[index]?.[input];
