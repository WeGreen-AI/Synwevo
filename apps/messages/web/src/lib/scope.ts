/** Which organization the conversation list is scoped to (§7.16), remembered per account on this
 *  browser. Storage can be missing, blocked or full (a private window, a preview): every access is
 *  guarded and a failure means "every organization" — never an error. A remembered organization the
 *  person no longer belongs to is dropped rather than applied. */

export const ALL_ORGS = "";

export function scopeKey(uid: string): string { return `synwevo.msg.org.${uid}`; }

type StorageLike = { getItem: (k: string) => string | null; setItem?: (k: string, v: string) => void; removeItem?: (k: string) => void } | null | undefined;

/** The remembered scope, if it is still one of `orgIds` (the person's active organizations); the
 *  filter is only offered with more than one, so with one or none it is always "every organization".
 *  `orgIds` null = not known yet: the remembered value is kept until the list arrives. */
export function readScope(storage: StorageLike, uid: string, orgIds: string[] | null): string {
  if (!uid) return ALL_ORGS;
  let v: string | null = null;
  try { v = storage ? storage.getItem(scopeKey(uid)) : null; } catch { v = null; }
  if (!v) return ALL_ORGS;
  if (orgIds === null) return v;
  return orgIds.length > 1 && orgIds.includes(v) ? v : ALL_ORGS;
}

export function writeScope(storage: StorageLike, uid: string, oid: string): void {
  if (!uid || !storage) return;
  try {
    if (oid) storage.setItem?.(scopeKey(uid), oid); else storage.removeItem?.(scopeKey(uid));
  } catch { /* storage unavailable: the choice lasts for this page only */ }
}

/** The list request for a scope: the server filters (`organization_id`), so a person in several
 *  organizations receives only that organization's rows for this view. */
export function conversationsPath(prefix: string, oid: string): string {
  return oid ? `${prefix}/conversations?organization_id=${encodeURIComponent(oid)}` : `${prefix}/conversations`;
}

/** The browser's storage, or null where reading the property itself throws. */
export function localStore(): Storage | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}
