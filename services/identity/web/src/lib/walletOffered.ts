import type { AuthState } from "@synwevo/auth-client";

/** Whether this deployment offers the paid Agent wallet (the server's `wallet_offered` in the auth
 *  state: a billing environment and wallet units are configured). Off is a deliberate state —
 *  production ships with billing off — so the account shells draw no wallet entry then, rather
 *  than a link to a page that can only fail. Unknown (no state yet) counts as not offered. */
export function walletOffered(state: Pick<AuthState, "wallet_offered"> | null | undefined): boolean {
  return state?.wallet_offered === true;
}
