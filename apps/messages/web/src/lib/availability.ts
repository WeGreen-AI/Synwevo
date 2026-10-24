/** B3: whether Synwevo Message is open in each of the reader's organizations, as the server says it
 *  — the list answer's `disabled_organizations` and `platform_disabled`, and each organization's
 *  `messages_enabled` and lifecycle (`status`, `recover_until`) from `/organizations`. Pure, so the
 *  banners and the refusal handling can be tested without a browser.
 *
 *  The server's precedence holds here too: an organization that is lost (deleted past its
 *  recovery window, or with none) says nothing — its conversations are gone for this reader, as
 *  after a removal; Synwevo Message switched off platform-wide is one notice for everything;
 *  switched off for an organization outranks the organization being paused (frozen, or deleted
 *  and still recoverable). Every source string here is in `messages.messages.ts`. */
import { ApiError } from "@synwevo/auth-client/api";

/** One organization of the reader as `/organizations` answers it (the fields this needs). */
export interface OrgState { id: string; name: string; status: string; recover_until?: number | null; messages_enabled?: boolean }
/** The additive fields of `GET /conversations` (B3). */
export interface ListAvailability { disabled_organizations?: string[]; platform_disabled?: boolean }
export interface Notice { key: string; text: string; params?: Record<string, string> }

export const PLATFORM_OFF = "Synwevo Message 已在平台停用";
export const ORG_OFF = "「{name}」已停用 Synwevo Message，相关会话暂时不可用";
export const ORG_OFF_UNNAMED = "该组织已停用 Synwevo Message，相关会话暂时不可用";
export const ORG_PAUSED = "「{name}」当前不可用，会话已暂停";

/** Deleted past its recovery window, or with none: gone, the same rule as the server's. */
export function lost(o: Pick<OrgState, "status" | "recover_until">, now = Date.now() / 1000): boolean {
  return o.status === "deleted" && !(typeof o.recover_until === "number" && o.recover_until > now);
}
/** An organization a conversation may be started in now: active, and Message on there. */
export const usable = (o: Pick<OrgState, "status" | "messages_enabled">) => o.status === "active" && o.messages_enabled !== false;

/** The notices the conversation list shows above itself, worst first per organization. */
export function availabilityNotices(orgs: OrgState[], list: ListAvailability | null, now = Date.now() / 1000): Notice[] {
  if (list?.platform_disabled) return [{ key: "platform", text: PLATFORM_OFF }];
  const off = new Set(list?.disabled_organizations ?? orgs.filter(o => o.messages_enabled === false).map(o => o.id));
  for (const o of orgs) if (o.messages_enabled === false) off.add(o.id);
  const out: Notice[] = [];
  for (const o of orgs) {
    if (lost(o, now)) continue;
    if (off.has(o.id)) out.push({ key: `off:${o.id}`, text: ORG_OFF, params: { name: o.name } });
    else if (o.status !== "active") out.push({ key: `paused:${o.id}`, text: ORG_PAUSED, params: { name: o.name } });
  }
  // switched off for an organization the organization list does not name (yet): the server's own sentence
  if ([...off].some(id => !orgs.some(o => o.id === id))) out.push({ key: "off:unnamed", text: ORG_OFF_UNNAMED });
  return out;
}

/** The refusals that close a conversation as a whole — the organization's or the platform's switch,
 *  the organization or the project's workspace paused — rather than refusing one action in it. */
export const CLOSED_CODES = new Set(["messages_disabled", "organization_unavailable", "workspace_unavailable"]);
export function closedRefusal(e: unknown): e is ApiError {
  return e instanceof ApiError && !e.upstream && CLOSED_CODES.has(e.code);
}
