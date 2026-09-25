/** UI theme (chrome only).
 *
 *  Synwevo ships one visual system: white surfaces, a very light neutral page and one brand blue.
 *  The earlier light / dark / system switch was retired with it. A preference remembered by an
 *  older build is still read, so nothing throws, but it no longer changes anything: `data-theme`
 *  is always "light", and the stylesheet ignores any other value. Document surfaces keep paper
 *  colours regardless, and the theme is never written into an output document. */
export type Theme = "light";
const NS = "synwevo";
/** The pre-rebrand namespace. Read only, and only when the new key is absent.
 *
 *  Renaming the prefix outright would not error — it would silently return the fallback for
 *  every remembered preference, so someone who had set a pane width would open the app one
 *  morning to find it reset and nothing to blame. So the old key is still read, and the value is
 *  written forward under the new one the first time it is used. Nothing is deleted: a rollback to
 *  the previous build finds its keys where it left them. */
const LEGACY_NS = "docweave";

export function storedTheme(): Theme {
  return "light";
}

export function effective(_t?: Theme): "light" {
  return "light";
}

export function applyTheme(_t?: Theme) {
  document.documentElement.dataset.theme = "light";
}

export function initTheme() {
  applyTheme("light");
}

/** Remembered UI layout (pane width, collapsed panel, zoom, connectors…).
 *  Storage can be unavailable (private mode, blocked site data) – the app must still work. */
export function remember<T>(key: string, value: T) {
  try {
    localStorage.setItem(`${NS}.${key}`, JSON.stringify(value));
  } catch {
    /* layout simply is not remembered */
  }
}

export function recall<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`${NS}.${key}`);
    if (raw !== null) return JSON.parse(raw) as T;
    const legacy = localStorage.getItem(`${LEGACY_NS}.${key}`);
    if (legacy === null) return fallback;
    const value = JSON.parse(legacy) as T;
    // Carry it forward once, and leave the old key in place so a rollback still finds it.
    try { localStorage.setItem(`${NS}.${key}`, legacy); } catch { /* not writable */ }
    return value;
  } catch {
    return fallback;
  }
}
