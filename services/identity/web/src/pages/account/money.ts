/** Format integer wallet units without converting an amount to a binary float. */
export function walletAmount(units: string, scale: number, locale: string, signed = false): string {
  if (!/^-?(0|[1-9]\d{0,30})$/.test(units) || !Number.isInteger(scale) || scale < 0 || scale > 18) {
    throw new RangeError('Invalid wallet amount');
  }
  const amount = BigInt(units);
  const absolute = amount < 0n ? -amount : amount;
  const divisor = 10n ** BigInt(scale);
  const whole = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(absolute / divisor);
  const digits = (absolute % divisor).toString().padStart(scale, '0').replace(/0+$/, '');
  const decimal = new Intl.NumberFormat(locale).formatToParts(1.1).find(part => part.type === 'decimal')?.value || '.';
  const signParts = new Intl.NumberFormat(locale, { signDisplay: 'always' }).formatToParts(amount < 0n ? -1 : 1);
  const prefix = amount < 0n ? signParts.find(part => part.type === 'minusSign')?.value || '-'
    : signed && amount > 0n ? signParts.find(part => part.type === 'plusSign')?.value || '+' : '';
  return prefix + whole + (scale && digits ? decimal + digits : '');
}
