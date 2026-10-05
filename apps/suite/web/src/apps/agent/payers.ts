/** Who pays for the next turn: the person's own wallet by default, or an organization that has
 *  granted this member payment (shared contract "Payer"; listed by GET /api/suite/agent/payers,
 *  package org-payments, synwevo_suite.agent.payers):
 *
 *    {configured, environment, currency: "CNY", unit: {code, scale} | null,
 *     payers: [{kind: "user", id, label: null, available, frozen},
 *              {kind: "organization", id, label, available, frozen,
 *               monthly_limit, spent_this_month, held, remaining_this_month}, ...],
 *     welcome: {state} | null}
 *
 *  Amounts are decimal strings of integer wallet units at `unit.scale` places. The list is the
 *  server's: a missing endpoint (404, before organization payment is deployed) or an unconfigured
 *  wallet means only the personal wallet exists, which is always available to oneself; nothing
 *  here invents an organization, a grant or a balance, and the ledger rechecks the grant and
 *  the limits on every reservation. */
export interface PayerRef { kind: 'user' | 'organization'; id: string }
export interface MoneyUnit { code: string; scale: number }
/** What the server listed for one payer, in wallet units of `unit`. */
export interface PayerFacts {
  unit: MoneyUnit;
  /** The wallet's available balance (booked minus held); signed, as the ledger reports it. */
  available: string;
  /** A frozen wallet (for example during a payment dispute) pays for nothing. */
  frozen: boolean;
  /** Organization payers only: this member's monthly allowance from the organization. */
  monthly?: { limit: string; spent: string; held: string; remaining: string };
}
export interface Payer extends PayerRef {
  /** The organization's name; empty for the personal wallet (it is labelled by the page). */
  name: string;
  facts: PayerFacts | null;
}
/** The one-time welcome allowance at first Agent use (package org-payments). */
export type WelcomeState = 'granted' | 'already' | 'email_unverified' | 'ineligible' | 'capacity' | 'deferred';
export interface PayerList {
  configured: boolean;
  environment: 'test' | 'live' | null;
  unit: MoneyUnit | null;
  payers: Payer[];
  welcome: WelcomeState | null;
}

const ID = /^[A-Za-z0-9_.:-]{1,200}$/;
const SIGNED = /^-?(0|[1-9][0-9]{0,30})$/;
const UNSIGNED = /^(0|[1-9][0-9]{0,30})$/;
const WELCOME: readonly WelcomeState[] = ['granted', 'already', 'email_unverified', 'ineligible', 'capacity', 'deferred'];

export const payerKey = (p: PayerRef) => `${p.kind}:${p.id}`;
export const personal = (userId: string, name = ''): Payer => ({ kind: 'user', id: userId, name, facts: null });
export const samePayer = (a: PayerRef | null | undefined, b: PayerRef | null | undefined) => !!a && !!b && a.kind === b.kind && a.id === b.id;

function unitOf(value: any): MoneyUnit | null {
  return value && typeof value === 'object' && typeof value.code === 'string' && /^[A-Za-z0-9_-]{1,32}$/.test(value.code)
    && Number.isInteger(value.scale) && value.scale >= 0 && value.scale <= 18 ? { code: value.code, scale: value.scale } : null;
}

function factsOf(item: any, unit: MoneyUnit | null, organization: boolean): PayerFacts | null {
  if (!unit || typeof item.available !== 'string' || !SIGNED.test(item.available) || typeof item.frozen !== 'boolean') return null;
  const facts: PayerFacts = { unit, available: item.available, frozen: item.frozen };
  if (!organization) return facts;
  const fields = [item.monthly_limit, item.spent_this_month, item.held, item.remaining_this_month];
  if (!fields.every((v) => typeof v === 'string' && UNSIGNED.test(v))) return null;
  const [limit, spent, held, remaining] = fields as string[];
  return { ...facts, monthly: { limit, spent, held, remaining } };
}

/** Strictly parse the server's list. The personal wallet always comes first; organization
 *  entries need a valid id and a label; duplicates and other people's wallets are dropped. An
 *  entry whose figures are malformed is still offered (the server listed it) without figures. */
export function parsePayers(value: unknown, me: { id: string; name?: string }): PayerList {
  const v = value && typeof value === 'object' ? value as Record<string, any> : {};
  const configured = v.configured === true;
  const unit = configured ? unitOf(v.unit) : null;
  const out: Payer[] = [personal(me.id, me.name ?? '')];
  const items = configured && Array.isArray(v.payers) ? v.payers : [];
  for (const item of items.slice(0, 50)) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !ID.test(item.id)) continue;
    if (item.kind === 'user') {
      if (item.id === me.id && !out[0].facts) out[0] = { ...out[0], facts: factsOf(item, unit, false) };
      continue;
    }
    const name = typeof item.label === 'string' ? item.label.trim() : '';
    if (item.kind !== 'organization' || !name || out.some((p) => payerKey(p) === payerKey(item))) continue;
    out.push({ kind: 'organization', id: item.id, name: name.slice(0, 200), facts: factsOf(item, unit, true) });
  }
  return {
    configured, unit, payers: out,
    environment: v.environment === 'test' || v.environment === 'live' ? v.environment : null,
    welcome: configured && WELCOME.includes(v.welcome?.state) ? v.welcome.state : null,
  };
}

/** What this payer can still spend on one run, in wallet units: the wallet's available balance,
 *  and for an organization also the member's remaining monthly allowance. Null when unknown. */
export function spendable(payer: Payer): bigint | null {
  const facts = payer.facts;
  if (!facts) return null;
  if (facts.frozen) return 0n;
  const available = BigInt(facts.available);
  if (!facts.monthly) return available;
  const remaining = BigInt(facts.monthly.remaining);
  return available < remaining ? available : remaining;
}

/** The payer a quote or consent was issued for: its `payer` field, or (terms issued before
 *  organization payment existed) the person themself. */
export function payerOf(terms: object | null | undefined, userId: string): PayerRef | null {
  const payer = (terms as { payer?: { kind?: unknown; id?: unknown } | null } | null | undefined)?.payer;
  if (payer == null) return { kind: 'user', id: userId };
  if (payer.kind === 'user') return payer.id == null || payer.id === userId ? { kind: 'user', id: userId } : null;
  return payer.kind === 'organization' && typeof payer.id === 'string' && ID.test(payer.id) ? { kind: 'organization', id: payer.id } : null;
}

/** The remembered choice when it is still offered, else the personal wallet (the default payer). */
export function choosePayer(payers: Payer[], remembered: PayerRef | null | undefined): Payer {
  return (remembered && payers.find((p) => payerKey(p) === payerKey(remembered))) || payers[0];
}

/** Extra quote/turn input naming a non-default payer; the personal default sends nothing extra. */
export function payerInput(payer: PayerRef | null | undefined, userId: string): Record<string, string> {
  if (!payer || (payer.kind === 'user' && payer.id === userId)) return {};
  return { payer_kind: payer.kind, payer_id: payer.id };
}

const storageKey = (userId: string) => `synwevo.agent.payer.${userId}`;
/** A per-viewer convenience only; the server decides on every send whether the payer may pay. */
export function recallPayer(userId: string, storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): PayerRef | null {
  try {
    const raw = storage?.getItem(storageKey(userId));
    const value = raw ? JSON.parse(raw) : null;
    return value && (value.kind === 'user' || value.kind === 'organization') && typeof value.id === 'string' && ID.test(value.id)
      ? { kind: value.kind, id: value.id } : null;
  } catch { return null; }
}
export function rememberPayer(userId: string, payer: PayerRef, storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage): void {
  try { storage?.setItem(storageKey(userId), JSON.stringify({ kind: payer.kind, id: payer.id })); } catch { /* private mode */ }
}
