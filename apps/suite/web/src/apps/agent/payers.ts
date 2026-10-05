/** Who pays for the next turn: the person's own wallet by default, or an organization that has
 *  granted this member payment (shared contract "Payer"; listed by GET /api/suite/agent/payers).
 *
 *  The list is the server's. A missing endpoint (404, before organization payment is deployed)
 *  means only the personal wallet exists, which is always available to oneself; nothing here
 *  invents an organization, a grant or a balance. */
export interface PayerRef { kind: 'user' | 'organization'; id: string }
export interface Payer extends PayerRef {
  name: string;
  /** Wallet facts the server chose to include for this payer (decimal smallest units). */
  balance: { available: string; currency: string; scale: number } | null;
}

const ID = /^[A-Za-z0-9_.:-]{1,200}$/;

export const payerKey = (p: PayerRef) => `${p.kind}:${p.id}`;
export const personal = (userId: string, name: string): Payer => ({ kind: 'user', id: userId, name, balance: null });

function balanceOf(value: any): Payer['balance'] {
  const b = value?.balance;
  if (!b || typeof b !== 'object') return null;
  const available = b.available ?? b.available_minor;
  const currency = b.currency ?? b.unit?.code ?? b.unit;
  const scale = b.scale ?? b.unit?.scale;
  if (typeof available !== 'string' || !/^-?(0|[1-9][0-9]{0,30})$/.test(available) || typeof currency !== 'string' || !currency
    || !Number.isInteger(scale) || scale < 0 || scale > 18) return null;
  return { available, currency, scale };
}

/** Strictly parse the server's list. The personal wallet always comes first; organization
 *  entries need a valid id and a name; duplicates and other users' wallets are dropped. */
export function parsePayers(value: unknown, me: { id: string; name: string }): Payer[] {
  const out: Payer[] = [personal(me.id, me.name)];
  const items = value && typeof value === 'object' && Array.isArray((value as any).payers) ? (value as any).payers : [];
  for (const item of items.slice(0, 50)) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !ID.test(item.id)) continue;
    if (item.kind === 'user') {
      if (item.id === me.id) out[0] = { ...out[0], balance: balanceOf(item) };
      continue;
    }
    const name = [item.name, item.label, item.display_name].find((v) => typeof v === 'string' && v.trim());
    if (item.kind !== 'organization' || !name || out.some((p) => payerKey(p) === payerKey(item))) continue;
    if (item.available === false) continue;
    out.push({ kind: 'organization', id: item.id, name: String(name).trim().slice(0, 200), balance: balanceOf(item) });
  }
  return out;
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
