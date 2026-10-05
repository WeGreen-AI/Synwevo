/** Pure rules of the peak/off-peak calendar and markup editors (no React, no network).
 *
 *  They mirror `synwevo_core.billing.bands` and `catalogue.MarkupSpec` so an administrator
 *  sees a mistake before asking for a preview; the server still decides every value. */

export type PeakWindow = { weekdays: number[]; start: string; end: string };
export type Holiday = { date: string; name: string };
export type ScheduleSpec = {
  peak_windows: PeakWindow[]; holiday_utc_offset_minutes: number; holidays: Holiday[]; source_ref: string;
};
export type Band = 'flat' | 'peak' | 'off_peak';
export type RateKey = 'input_uncached' | 'input_cached' | 'output';
export type BandRates = Record<RateKey, string>;

export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
export const MAX_RANGE_DAYS = 31;
export const MAX_HOLIDAYS = 1000;
export const MAX_WINDOWS = 14;
const TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const SOURCE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/;
const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
/** 1–100 with at most four decimals; 100 only as 100 or 100.0…: selling below cost is refused. */
const MARKUP = /^(100(\.0{1,4})?|[1-9][0-9]?(\.[0-9]{1,4})?)$/;

export const validTime = (value: string, end = false) => TIME.test(value) || (end && value === '24:00');
export const minutes = (value: string) => Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5));
export const validMarkup = (value: string) => MARKUP.test(value);
export const validSource = (value: string) => SOURCE.test(value);

/** A real calendar date in ISO form (2026-02-30 is refused, not normalised). */
export function validDate(value: string): boolean {
  if (!DATE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validHolidayName(value: string): boolean {
  return value.length > 0 && Array.from(value).length <= 40 && value === value.trim() && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Every date from `from` to `to` inclusive; null when invalid, reversed or longer than a month. */
export function expandDates(from: string, to: string): string[] | null {
  if (!validDate(from) || !validDate(to || from)) return null;
  const start = Date.parse(`${from}T00:00:00Z`), end = Date.parse(`${to || from}T00:00:00Z`);
  const days = Math.round((end - start) / 86_400_000) + 1;
  if (days < 1 || days > MAX_RANGE_DAYS) return null;
  return Array.from({ length: days }, (_, i) => new Date(start + i * 86_400_000).toISOString().slice(0, 10));
}

/** Add (or rename) holidays; the result is ascending and distinct, as the server requires. */
export function mergeHolidays(existing: Holiday[], dates: string[], name: string): Holiday[] {
  const byDate = new Map(existing.map(h => [h.date, h.name]));
  for (const date of dates) byDate.set(date, name);
  return [...byDate].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([date, holidayName]) => ({ date, name: holidayName }));
}

export type ScheduleProblem = 'windows' | 'window_days' | 'window_time' | 'window_order' | 'window_duplicate'
  | 'holiday_date' | 'holiday_name' | 'holiday_order' | 'holidays_many' | 'source';

/** The first problem of each kind, keyed for the form; empty when the spec may be previewed. */
export function scheduleProblems(spec: ScheduleSpec): ScheduleProblem[] {
  const out = new Set<ScheduleProblem>();
  if (!spec.peak_windows.length || spec.peak_windows.length > MAX_WINDOWS) out.add('windows');
  const seen = new Set<string>();
  for (const w of spec.peak_windows) {
    const days = w.weekdays;
    if (!days.length || days.some((d, i) => !WEEKDAYS.includes(d as 1) || (i > 0 && d <= days[i - 1]))) out.add('window_days');
    if (!validTime(w.start) || !validTime(w.end, true)) out.add('window_time');
    else if (minutes(w.start) >= minutes(w.end)) out.add('window_order');
    const key = `${days.join(',')}|${w.start}|${w.end}`;
    if (seen.has(key)) out.add('window_duplicate');
    seen.add(key);
  }
  if (spec.holidays.length > MAX_HOLIDAYS) out.add('holidays_many');
  spec.holidays.forEach((h, i) => {
    if (!validDate(h.date)) out.add('holiday_date');
    if (!validHolidayName(h.name)) out.add('holiday_name');
    if (i > 0 && h.date <= spec.holidays[i - 1].date) out.add('holiday_order');
  });
  if (!validSource(spec.source_ref)) out.add('source');
  return [...out];
}

/** The band an instant (Unix seconds) falls in, exactly as the server decides it at dispatch. */
export function bandAt(spec: ScheduleSpec, seconds: number): 'peak' | 'off_peak' {
  const moment = new Date(seconds * 1000);
  const local = new Date(moment.getTime() + spec.holiday_utc_offset_minutes * 60_000).toISOString().slice(0, 10);
  if (spec.holidays.some(h => h.date === local)) return 'off_peak';
  const minute = moment.getUTCHours() * 60 + moment.getUTCMinutes();
  const weekday = moment.getUTCDay() === 0 ? 7 : moment.getUTCDay();
  return spec.peak_windows.some(w => w.weekdays.includes(weekday) && minutes(w.start) <= minute && minute < minutes(w.end))
    ? 'peak' : 'off_peak';
}

/** A JSON text whose key order cannot make an unchanged calendar look changed. */
export function canonicalSchedule(spec: ScheduleSpec): string {
  return JSON.stringify({
    peak_windows: spec.peak_windows.map(w => ({ weekdays: [...w.weekdays], start: w.start, end: w.end })),
    holiday_utc_offset_minutes: spec.holiday_utc_offset_minutes,
    holidays: spec.holidays.map(h => ({ date: h.date, name: h.name })), source_ref: spec.source_ref,
  });
}

/** "UTC+08:00" for an offset in minutes. */
export function utcOffset(minutesEast: number): string {
  const sign = minutesEast < 0 ? '-' : '+', abs = Math.abs(minutesEast);
  return `UTC${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
}

/** "HH:MM" of a UTC time-of-day shifted by an offset, wrapping past midnight ("24:00" + 8 h = "08:00"). */
export function shiftedTime(value: string, offsetMinutes: number): string {
  const total = (((minutes(value) + offsetMinutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** Same rule as the server's rate fields: no exponent, sign, separator or extra leading zero. */
export const validRate = (value: string) => /^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$/.test(value);

/** -1, 0 or 1 for two validated decimal strings, exactly (money never becomes a float). */
export function compareDecimal(a: string, b: string): number {
  const [ai, af = ''] = a.split('.'), [bi, bf = ''] = b.split('.');
  // Validated decimals have no leading zeros, so a longer integer part is the larger number.
  if (ai.length !== bi.length) return ai.length < bi.length ? -1 : 1;
  if (ai !== bi) return ai < bi ? -1 : 1;
  const width = Math.max(af.length, bf.length), x = af.padEnd(width, '0'), y = bf.padEnd(width, '0');
  return x === y ? 0 : x < y ? -1 : 1;
}

/** Local `datetime-local` text for Unix seconds (the browser's time zone). */
export function localInput(seconds: number): string {
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${String(date.getFullYear()).padStart(4, '0')}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Unix seconds of a local date-time; null for impossible dates and times skipped by daylight saving. */
export function localTimestamp(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) return null;
  const seconds = new Date(value).getTime() / 1000;
  return Number.isSafeInteger(seconds) && localInput(seconds) === (value.length === 16 ? `${value}:00` : value) ? seconds : null;
}

export type MarkupSpec = {
  name: string; cost_price_id: string; markup: string; off_peak_markup: string | null; terms_ref: string; effective_from: number;
};
export type MarkupForm = { name: string; cost_price_id: string; markup: string; off_peak_markup: string; terms_ref: string; effective: string };
export type MarkupProblem = 'name' | 'cost_price_id' | 'markup' | 'off_peak_markup' | 'terms_ref' | 'effective';
export const emptyMarkupForm = (): MarkupForm => ({ name: '', cost_price_id: '', markup: '', off_peak_markup: '', terms_ref: '', effective: '' });

/** The markup a form asks for, or the fields that keep it from a preview. `banded` is whether the
 *  chosen cost price has off-peak rates: only then may off-peak have its own markup. */
export function validateMarkup(form: MarkupForm, banded: boolean): { spec: MarkupSpec | null; problems: MarkupProblem[] } {
  const problems: MarkupProblem[] = [];
  if (!form.name.trim() || Array.from(form.name).length > 80 || /[\u0000-\u001f\u007f]/.test(form.name)) problems.push('name');
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(form.cost_price_id)) problems.push('cost_price_id');
  if (!validMarkup(form.markup)) problems.push('markup');
  if (form.off_peak_markup !== '' && (!banded || !validMarkup(form.off_peak_markup))) problems.push('off_peak_markup');
  if (!validSource(form.terms_ref)) problems.push('terms_ref');
  const effective = localTimestamp(form.effective);
  if (effective === null || effective < 0 || effective > 253402300799) problems.push('effective');
  if (problems.length) return { spec: null, problems };
  return { problems, spec: { name: form.name.trim(), cost_price_id: form.cost_price_id, markup: form.markup,
    off_peak_markup: form.off_peak_markup || null, terms_ref: form.terms_ref, effective_from: effective! } };
}
