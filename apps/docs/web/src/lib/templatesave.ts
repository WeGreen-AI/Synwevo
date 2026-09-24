/** Saving a built-in template without silently replacing someone else's newer version.
 *
 *  The editor sends the version it loaded as `base_version`. When the template has moved on
 *  since (another tab, another person), the server answers 409 stale_template with the current
 *  head instead of writing. The person then decides: keep the other version (nothing is saved,
 *  their edits stay in the editor), or save theirs on top of it — sent against the head the
 *  server named, so a third save in between is refused again rather than overwritten.
 *
 *  Before this, the editors sent no base version at all and the last save won without a word.
 *
 *  No imports: tested with `node --test`.
 */
export interface StaleRefusal { code?: string; detail?: { head?: unknown } }

export function staleHead(e: unknown): number | null | undefined {
  const r = e as StaleRefusal | null;
  if (!r || r.code !== "stale_template") return undefined;          // not a stale refusal
  const h = Number(r.detail?.head);
  return Number.isInteger(h) && h > 0 ? h : null;
}

/** `send(base)` performs the save against `base`; `ask(theirs, mine)` asks whether to save over
 *  the newer version. Resolves to the saved template, or null when the person kept the other
 *  version. Any other failure is thrown to the caller unchanged. */
export async function saveOverStale<T>(
  send: (base: number | null) => Promise<T>,
  mine: number | null,
  ask: (theirs: number | null, mine: number | null) => Promise<boolean>,
): Promise<T | null> {
  try {
    return await send(mine);
  } catch (e) {
    const theirs = staleHead(e);
    if (theirs === undefined) throw e;
    if (!(await ask(theirs, mine))) return null;
    return await send(theirs);
  }
}

/** The version number a template detail response describes (the head it was loaded at). */
export function loadedVersion(t: { version?: { no?: unknown } | null; head?: unknown } | null | undefined): number | null {
  const n = Number(t?.version?.no ?? t?.head);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export const STALE_TITLE = "模板已被其他人修改";
export function staleText(theirs: number | null, mine: number | null): string {
  const was = mine ? `你打开的是 v${mine}` : "你打开这个模板之后";
  const now = theirs ? `，现在最新的是 v${theirs}` : "，它又被保存过";
  return `${was}${now}（在其他窗口或由其他人保存）。仍然保存会用你的内容生成一个新版本；对方保存的版本仍然保留，可以在项目中用模板的「撤销」回到它。`;
}
export const STALE_KEPT = "没有保存：你的修改仍在编辑器中。需要对方的版本时，先复制要保留的内容，再重新打开模板。";
