/** Payment channel order shared by the account service's settings and wallet displays. */
export const PAYMENT_METHODS = ['card', 'wechat_pay', 'alipay'] as const;
export type PaymentMethod = typeof PAYMENT_METHODS[number];
export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
  card: '银行卡', wechat_pay: '微信支付', alipay: '支付宝',
};

/** Only listed, understood channels, once each and in the server's canonical order.
 * Missing top-up options must never imply that all channels are available. */
export function paymentMethods(value: readonly PaymentMethod[] | null | undefined): PaymentMethod[] {
  return PAYMENT_METHODS.filter(method => Array.isArray(value) && value.includes(method));
}

/** Only an existing settings version without the field has the legacy three-channel meaning. */
export function configuredPaymentMethods(spec: { payment_methods?: PaymentMethod[] } | null): PaymentMethod[] {
  return spec === null ? [] : paymentMethods(spec.payment_methods === undefined ? PAYMENT_METHODS : spec.payment_methods);
}

export function paymentMethodsText(methods: readonly PaymentMethod[] | null | undefined, t: (key: string) => string, locale: string): string {
  return new Intl.ListFormat(locale, { style: 'long', type: 'conjunction' })
    .format(paymentMethods(methods).map(method => t(PAYMENT_METHOD_LABELS[method])));
}
