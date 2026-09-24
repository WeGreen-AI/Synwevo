/** UI theme (chrome only).  Document surfaces keep paper colours so the preview matches
 *  the generated file; the theme is never written into an output document. */
export type Theme = "light" | "dark" | "system";
const NS = "synwevo";
/** The pre-rebrand namespace. Read only, and only when the new key is absent.
 *
 *  Renaming the prefix outright would not error — it would silently return the fallback for
 *  every remembered preference, so someone who had set a dark theme and a pane width would
 *  open the app one morning to find both reset and nothing to blame. So the old key is still
 *  read, and the value is written forward under the new one the first time it is used. Nothing
 *  is deleted: a rollback to the previous build finds its keys where it left them. */
const LEGACY_NS = "docweave";

export function storedTheme(): Theme {
  const v = recall<string>("theme", "system");
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}

export function effective(t: Theme): "light" | "dark" {
  if (t !== "system") return t;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(t: Theme) {
  remember("theme", t);
  document.documentElement.dataset.theme = effective(t);
}

export function initTheme() {
  applyTheme(storedTheme());
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (storedTheme() === "system") applyTheme("system");
  });
}

export const THEME_NAME: Record<Theme, string> = { system: "跟随系统", light: "浅色", dark: "深色" };
export const nextTheme = (t: Theme): Theme => (t === "system" ? "light" : t === "light" ? "dark" : "system");

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
