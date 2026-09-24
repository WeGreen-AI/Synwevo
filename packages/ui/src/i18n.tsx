import { useEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import { getLocale, setLocale, subscribeLocale, translate, registerMessages, type Locale, type Translator } from './i18n-core';
import { messages } from './messages';
registerMessages(messages);
export * from './i18n-core';

export function useI18n(): { locale: Locale; t: Translator; setLocale: (locale: Locale) => void } {
  const locale = useSyncExternalStore(subscribeLocale, getLocale, () => 'zh-Hans' as const);
  const t = useMemo<Translator>(() => (source, params) => translate(locale, source, params), [locale]);
  return { locale, t, setLocale };
}
/** No locale keys or subtree remounts: input, selection and editor state survive switches. */
export function I18nProvider({ children, title }: { children: ReactNode; title?: string }) {
  const { locale, t } = useI18n();
  useEffect(() => {
    document.documentElement.lang = locale;
    if (title) document.title = t(title);
  }, [locale, title, t]);
  useEffect(() => {
    const sync = (e: StorageEvent) => {
      if (e.key === 'synwevo.locale' && (e.newValue === 'en' || e.newValue === 'zh-Hans' || e.newValue === 'zh-Hant')) setLocale(e.newValue);
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  return children;
}
export function LanguageMenu({ className = '' }: { className?: string }) {
  const { locale, t, setLocale } = useI18n();
  return <label className={`language-menu ${className}`}><span className="sr-only">{t('语言')}</span>
    <select className="input sm" aria-label={t('语言')} value={locale} onChange={e => setLocale(e.target.value as Locale)}>
      <option value="zh-Hans" lang="zh-Hans">简体中文</option>
      <option value="zh-Hant" lang="zh-Hant">繁體中文</option>
      <option value="en" lang="en">English</option>
    </select>
  </label>;
}
