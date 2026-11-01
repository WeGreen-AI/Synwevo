/** CNY amounts for the money administration and organization pages, through the shared formatter
 *  (`@synwevo/ui/money`): exact integer arithmetic (no binary floats), money at fen precision in
 *  the reader's locale, the same "¥10.00" here as on the top-up dialog. */
import { formatMinor, formatMoney } from "@synwevo/ui/money";

/** `units` integer wallet units of `scale` decimal places (fen are scale 2); `signed` adds the plus sign of a credit. */
export function cnyText(units: number | string, scale: number, locale: string, signed = false): string {
  return formatMoney(String(units), { code: "CNY", scale }, locale, { signed });
}

/** Fen to text. */
export const fenText = (fen: number, locale: string) => formatMinor(fen, "CNY", locale);

/** A yuan amount typed by an administrator ("10", "10.5", "10.50") to whole fen; null when invalid. */
export function parseYuan(text: string, max = 100000): number | null {
  const value = text.trim();
  if (!/^(0|[1-9]\d{0,7})(\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const fen = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return fen >= 1 && fen <= max * 100 ? fen : null;
}

/** Fen back to the text an input shows ("10.5" for 1050). */
export function yuanInput(fen: number): string {
  const whole = Math.floor(fen / 100), cents = fen % 100;
  return cents ? `${whole}.${String(cents).padStart(2, "0").replace(/0$/, "")}` : String(whole);
}
