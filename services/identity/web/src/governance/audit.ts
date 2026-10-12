/** The unified before/after record of an audit event (admin coverage G24).
 *
 *  A state-changing administration action's `meta` carries `before` and `after` over the same
 *  keys and `changed`, the keys whose value differs. This turns that into rows for the audit
 *  table without knowing any action's own fields; an event without the record yields nothing. */
export type AuditChange = { key: string; before: string; after: string };

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A value as the audit table prints it: empty for none, JSON for structures, plain otherwise. */
export function auditValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function auditChanges(meta: Record<string, unknown> | null | undefined): AuditChange[] {
  if (!meta || !isRecord(meta.before) || !isRecord(meta.after)) return [];
  const before = meta.before, after = meta.after;
  const changed = Array.isArray(meta.changed)
    ? meta.changed.filter((k): k is string => typeof k === "string")
    : Object.keys(after).filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null));
  return changed.map((key) => ({ key, before: auditValue(before[key]), after: auditValue(after[key]) }));
}
