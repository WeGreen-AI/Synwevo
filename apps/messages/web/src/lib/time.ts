/** Time labels for the list and the thread. Pure: they take `now` so tests can pin them.
 *  Every label is a source key with parameters; the component translates it. */

export type DayLabel = { key: "今天" } | { key: "昨天" } | { key: "weekday"; ts: number } | { key: "date"; ts: number };

function startOfDay(ms: number): number { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); }

/** The separator above a day's first message: 今天 / 昨天, the weekday within the last week,
 *  else the date (with the year when it is not this year). */
export function dayLabel(ts: number, now = Date.now()): DayLabel {
  const days = Math.round((startOfDay(now) - startOfDay(ts * 1000)) / 86_400_000);
  if (days <= 0) return { key: "今天" };
  if (days === 1) return { key: "昨天" };
  if (days < 7) return { key: "weekday", ts };
  return { key: "date", ts };
}

/** The time beside a conversation in the list: the clock today, 昨天, the weekday within a
 *  week, else a short date. */
export type ListTime = { key: "time"; ts: number } | { key: "昨天" } | { key: "weekday"; ts: number } | { key: "date"; ts: number };
export function listTime(ts: number, now = Date.now()): ListTime {
  const days = Math.round((startOfDay(now) - startOfDay(ts * 1000)) / 86_400_000);
  if (days <= 0) return { key: "time", ts };
  if (days === 1) return { key: "昨天" };
  if (days < 7) return { key: "weekday", ts };
  return { key: "date", ts };
}

export function formatDayLabel(l: DayLabel | ListTime, locale: string, t: (s: string) => string): string {
  const sameYear = "ts" in l && new Date(l.ts * 1000).getFullYear() === new Date().getFullYear();
  switch (l.key) {
    case "今天": return t("今天");
    case "昨天": return t("昨天");
    case "time": return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(l.ts * 1000);
    case "weekday": return new Intl.DateTimeFormat(locale, { weekday: "long" }).format(l.ts * 1000);
    case "date": return new Intl.DateTimeFormat(locale, sameYear ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" }).format(l.ts * 1000);
  }
}

export function clock(ts: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(ts * 1000);
}

export function fullDate(ts: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(ts * 1000);
}
