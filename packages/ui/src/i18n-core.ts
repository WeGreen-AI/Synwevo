/** Local, text-only catalogs. Source messages are Simplified Chinese; data never goes here. */
export const LOCALES = ['zh-Hans', 'zh-Hant', 'en'] as const;
export type Locale = typeof LOCALES[number];
export type Params = Record<string, string | number | null | undefined>;
export type Translation = string | { one?: string; other: string };
export type Messages = Record<string, { en: Translation; 'zh-Hant': Translation }>;
export type Translator = (source: string, params?: Params) => string;
export const LOCALE_STORAGE_KEY = 'synwevo.locale';
const catalog: Messages = Object.create(null);
// UI state may retain a rendered toast/error while the locale changes. Keep its source and
// parameters, never infer translations from arbitrary content. Bound the transient cache.
type Rendered = { source: string; params: Params } | { parts: string[]; separator: string };
const rendered = new Map<string, Rendered>();
function remember(result: string, provenance: Rendered): string {
  rendered.delete(result);
  rendered.set(result, provenance);
  if (rendered.size > 1000) rendered.delete(rendered.keys().next().value!);
  return result;
}
/** Compose already translated UI messages without losing their locale provenance. */
export function joinMessages(parts: string[], separator = ' · '): string {
  const result = parts.join(separator);
  // A one-part error (e.g. a gateway response without a request ID) must retain its
  // original provenance, not replace it with a composition pointing to itself.
  if (parts.includes(result)) return result;
  return remember(result, { parts: [...parts], separator });
}
export const hasMessage = (source: string): boolean => Object.hasOwn(catalog, source);

export function normalizeLocale(value: unknown): Locale | null {
  if (typeof value !== 'string') return null;
  const tag = value.toLowerCase().replace(/_/g, '-');
  if (/^en(?:-|$)/.test(tag)) return 'en';
  if (!/^zh(?:-|$)/.test(tag)) return null;
  if (tag.includes('-hans')) return 'zh-Hans';
  if (tag.includes('-hant') || /-(tw|hk|mo)(?:-|$)/.test(tag)) return 'zh-Hant';
  return 'zh-Hans';
}
export function resolveLocale(saved?: unknown, languages: readonly string[] = [], navigationChoice?: unknown): Locale {
  return (LOCALES.includes(navigationChoice as Locale) ? navigationChoice as Locale : null)
    || normalizeLocale(saved) || languages.map(normalizeLocale).find(Boolean) || 'zh-Hans';
}
export function messageParams(message: string): string[] {
  return [...new Set([...message.matchAll(/\{([a-zA-Z][\w]*)\}/g)].map(m => m[1]))].sort();
}
export function validateMessages(messages: Messages): string[] {
  const errors: string[] = [];
  for (const [source, entries] of Object.entries(messages)) {
    const expected = messageParams(source).join(',');
    for (const locale of ['en', 'zh-Hant'] as const) {
      const value = entries[locale];
      const forms = typeof value === 'string' ? [value] : value && value.other ? Object.values(value) : [];
      if (!forms.length) errors.push(`${locale}: missing ${source}`);
      for (const form of forms) {
        if (!form || messageParams(form).join(',') !== expected) errors.push(`${locale}: parameters differ for ${source}`);
      }
    }
  }
  return errors;
}
export function registerMessages(messages: Messages): void {
  const errors = validateMessages(messages);
  if (errors.length) throw new Error(errors.join('\n'));
  // Domains may reuse common source text. The first registered wording wins consistently.
  for (const [source, value] of Object.entries(messages)) if (!catalog[source]) catalog[source] = value;
}
/** The number a plural form is chosen by: an explicit `count`, otherwise the only numeric parameter. */
function pluralCount(params: Params): number {
  if (params.count !== undefined && params.count !== null) return Number(params.count);
  const numbers = Object.values(params).filter((v): v is number => typeof v === 'number');
  return numbers.length === 1 ? numbers[0] : 0;
}
export function translate(locale: Locale, source: string, params: Params = {}): string {
  const previous = !hasMessage(source) && !Object.keys(params).length ? rendered.get(source) : undefined;
  if (previous && 'parts' in previous) return previous.parts.map(part => translate(locale, part)).join(previous.separator);
  if (previous) { source = previous.source; params = previous.params; }
  const value = locale === 'zh-Hans' ? source : catalog[source]?.[locale] ?? source;
  const message = typeof value === 'string' ? value : (new Intl.PluralRules(locale).select(pluralCount(params)) === 'one' ? value.one : undefined) || value.other;
  // Replace once; a parameter containing braces or markup remains literal text.
  const result = message.replace(/\{([a-zA-Z][\w]*)\}/g, (token, name: string) =>
    Object.hasOwn(params, name) ? typeof params[name] === 'number' ? new Intl.NumberFormat(locale).format(params[name]) : String(params[name] ?? "") : token);
  if (hasMessage(source) && result !== source) {
    remember(result, { source, params: { ...params } });
  }
  return result;
}

let current: Locale = 'zh-Hans';
let initialized = false;
const listeners = new Set<() => void>();
export function getLocale(): Locale {
  if (!initialized && typeof window !== 'undefined') {
    initialized = true;
    let saved: unknown;
    try { saved = localStorage.getItem(LOCALE_STORAGE_KEY); if (saved?.toString().startsWith('"')) saved = JSON.parse(saved as string); } catch { /* private browsing */ }
    let navigationChoice: string | null = null;
    try {
      const url = new URL(window.location.href);
      navigationChoice = url.searchParams.get('lang');
      if (LOCALES.includes(navigationChoice as Locale)) {
        url.searchParams.delete('lang');
        // Only consume this one explicit preference; keep launch markers, router state and hash.
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
      }
    } catch { /* Storage/history restrictions must not prevent rendering. */ }
    current = resolveLocale(saved, navigator.languages || [navigator.language], navigationChoice);
    if (LOCALES.includes(navigationChoice as Locale)) {
      try { localStorage.setItem(LOCALE_STORAGE_KEY, current); } catch { /* private browsing */ }
    }
  }
  return current;
}
export function setLocale(locale: Locale): void {
  const normalized = normalizeLocale(locale);
  if (!normalized) return;
  const changed = current !== normalized;
  current = normalized;
  initialized = true;
  try { localStorage.setItem(LOCALE_STORAGE_KEY, normalized); } catch { /* works without persistence */ }
  if (changed) for (const notify of listeners) notify();
}
export function subscribeLocale(notify: () => void): () => void {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
}
/** Non-React presentation helpers; React consumers subscribe with useI18n. */
export const t: Translator = (source, params) => translate(getLocale(), source, params);
export const formatNumber = (value: number | null | undefined, options?: Intl.NumberFormatOptions, locale = getLocale()) => value == null ? '' : new Intl.NumberFormat(locale, options).format(value);
export const formatDate = (value: Date | number, options?: Intl.DateTimeFormatOptions, locale = getLocale()) => new Intl.DateTimeFormat(locale, options).format(value);
