/** CNY amounts for the money administration pages, exact (no binary floats) and in the reader's
 *  locale: the digits come from `walletAmount` (BigInt), the currency sign from Intl. */
import { walletAmount } from "../pages/account/money";

function currencySign(locale: string): { sign: string; before: boolean } {
  const parts = new Intl.NumberFormat(locale, { style: "currency", currency: "CNY" }).formatToParts(1);
  const at = parts.findIndex((p) => p.type === "currency");
  const integer = parts.findIndex((p) => p.type === "integer");
  return { sign: at >= 0 ? parts[at].value : "CNY", before: at >= 0 && at < integer };
}

/** `units` integer wallet units of `scale` decimal places (fen are scale 2). */
export function cnyText(units: number | string, scale: number, locale: string): string {
  const exact = walletAmount(String(units), scale, locale);
  const { sign, before } = currencySign(locale);
  return before ? `${sign}${exact}` : `${exact} ${sign}`;
}

/** Fen to text. */
export const fenText = (fen: number, locale: string) => cnyText(fen, 2, locale);

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
