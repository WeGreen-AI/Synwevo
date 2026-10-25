/** The shapes the MCP integration centre reads (agent.mcp.* operations, connections.py) and the
 *  pure decisions its pages make: which badge a health state gets, which refusal sentence a code
 *  gets, which workspaces a person may add a connection for, and the client configuration
 *  examples derived from the deployment's real addresses. Nothing here guesses a host. */
import { integrationCopy, type IntegrationText } from './integrations.messages';

export type Health = 'unknown' | 'ok' | 'failed' | 'auth_required';
export type GrantMode = 'off' | 'confirm' | 'auto';
export type PersonalMode = 'off' | 'confirm';
export type AuthKind = 'none' | 'bearer' | 'oauth';

export interface Binding {
  connection_id: string; bound: boolean; alias?: string; enabled?: boolean; resources_enabled?: boolean;
  tool_modes?: Record<string, PersonalMode>;
}
export interface OAuthClient { kind: 'preregistered' | 'dynamic' | 'cimd'; client_id: string; issuer?: string }
export interface ConnectionSummary {
  id: string; owner_kind: 'user' | 'workspace'; owner_id: string; owner_name: string | null; slug: string; name: string;
  description: string; url: string; host: string | null; auth_kind: AuthKind; credential_hint: string | null;
  has_credential: boolean; oauth_client: OAuthClient | null; status: 'active' | 'disabled'; health: Health;
  /** A platform administrator stopped the connection (synwevo_identity.admin_mcp): only the
   *  platform lifts it; its managers may still rename, disable or delete it. */
  platform_disabled?: boolean; platform_disabled_at?: number | null;
  health_checked_at: number | null; last_ok_at: number | null; last_error: { code: string; message: string } | null;
  circuit_open_until: number | null; protocol_version: string | null;
  server_info: { name?: string; version?: string; title?: string } | null; capabilities: Record<string, unknown>;
  discovered_at: number | null; counts: { tools: number; resources: number; prompts: number; granted: number };
  last_call_at: number | null; manage: boolean; binding: Binding; created_at: number; updated_at: number;
}
export interface ToolAnnotations { readOnlyHint?: boolean; destructiveHint?: boolean; idempotentHint?: boolean; openWorldHint?: boolean; title?: string }
export interface Tool {
  name: string; title: string | null; description: string | null; input_schema: unknown; output_schema: unknown;
  annotations: ToolAnnotations; schema_hash: string; removed: boolean;
  /** Why the tool cannot be granted (its schema was not kept: `mcp_schema_too_large`, `mcp_schema_invalid`,
   *  or the server's whole offer did not fit, `mcp_offer_too_large`). */
  unusable?: { code: string; max_chars?: number; max_bytes?: number } | null;
  grant: { mode: GrantMode; stale: boolean; granted_by: string | null; granted_at: number | null };
  personal_mode: PersonalMode | null; first_seen_at: number; last_seen_at: number;
}
export interface Resource { uri: string; name: string | null; title: string | null; description: string | null; mime_type: string | null; size: number | null }
export interface PromptArgument { name: string; description?: string; required?: boolean }
export interface Prompt { name: string; title: string | null; description: string | null; arguments: PromptArgument[] }
export interface CallRecord {
  id: string; origin: string; kind: string; name: string | null; status: string; user_id: string | null;
  conversation_id: string | null; turn_id: string | null; tool_call_id: string | null; request_id: string | null;
  input_preview: string | null; result_preview: string | null; error: { code: string; message: string } | null;
  started_at: number; finished_at: number | null; duration_ms: number | null;
}
export interface ConnectionDetail extends ConnectionSummary {
  tools: Tool[]; resources: Resource[]; prompts: Prompt[]; instructions: string | null; calls: CallRecord[];
}
export interface Policy {
  revision: number; outbound_enabled: boolean; inbound_enabled: boolean; allow_http: boolean;
  allowed_hosts: string[] | null; blocked_hosts: string[];
  limits: Record<string, number>; client_registration: 'cimd_and_preregistered' | 'preregistered_only';
}
export interface InboundInfo {
  enabled: boolean; endpoint: string; metadata_url: string; issuer: string; authorization_server_metadata: string;
  scopes: string[]; explicit_scopes?: string[]; protocol_versions: string[]; client_registration: 'cimd_and_preregistered' | 'preregistered_only';
  transport: string;
}
export interface ResourcePage { connection: string; connection_name: string; uri: string; text: string | null; truncated: boolean;
  contents: { uri?: string; mimeType?: string; blob?: string }[]; bytes: number; duration_ms: number; call_id: string }
export interface RenderedPrompt { name: string; description: string | null; text: string; truncated: boolean; messages: unknown[]; duration_ms: number }
export interface OAuthStart { authorization_url: string; issuer: string; registration: string; expires_at: number }
export interface BindingList { items: ConnectionSummary[]; tools: { alias: string; tool: string; mode: GrantMode }[] }

export const CONNECTION_ID = /^mcpc_[a-f0-9]{12}$/;
export const MAX_ALIAS = 24;

/** The health badge's wording and tone; a state this page does not know is shown as its code. */
export function healthView(health: string): { key: IntegrationText; tone: string } {
  switch (health) {
    case 'ok': return { key: 'healthOk', tone: 'ok' };
    case 'failed': return { key: 'healthFailed', tone: 'err' };
    case 'auth_required': return { key: 'healthAuthRequired', tone: 'warn' };
    default: return { key: 'healthUnknown', tone: '' };
  }
}

export function callStatusView(status: string): { key: IntegrationText; tone: string } {
  switch (status) {
    case 'running': return { key: 'callRunning', tone: 'info' };
    case 'succeeded': return { key: 'callSucceeded', tone: 'ok' };
    case 'failed': return { key: 'callFailed', tone: 'err' };
    case 'timeout': return { key: 'callTimeout', tone: 'warn' };
    case 'cancelled': return { key: 'callCancelled', tone: '' };
    // Settled after the process that made it stopped (suite_schema.mcp.settle_interrupted_calls).
    case 'unknown': return { key: 'callUnknown', tone: 'warn' };
    default: return { key: 'callFailed', tone: '' };
  }
}

export const ORIGIN_TEXT: Record<string, IntegrationText> = { agent: 'originAgent', page: 'originPage', test: 'originTest', discover: 'originDiscover' };
export const KIND_TEXT: Record<string, IntegrationText> = {
  'tools/call': 'kindTool', 'resources/read': 'kindResource', 'prompts/get': 'kindPrompt', discover: 'kindDiscover',
};

/** The plain-language hints a tool's annotations earn. Hints are the server's claims about
 *  itself, so they are shown as declarations, never as a verified property. */
export function toolHints(annotations: ToolAnnotations | null | undefined): IntegrationText[] {
  const a = annotations ?? {};
  const out: IntegrationText[] = [];
  if (a.readOnlyHint === true) out.push('hintReadOnly');
  else out.push('hintWrites');
  if (a.destructiveHint === true && a.readOnlyHint !== true) out.push('hintDestructive');
  if (a.openWorldHint === true) out.push('hintOpenWorld');
  return out;
}
export const isReadOnly = (tool: Pick<Tool, 'annotations'>) => tool.annotations?.readOnlyHint === true;

/** How a tool's grant is shown, and whether it can be granted again in its previous mode.
 *  A grant whose tool changed shape (`stale`) is suspended: the Agent treats the tool as off until
 *  a manager grants it again, and `agent.mcp.grant.set` with the same mode pins the new
 *  definition (connections.set_grant). The select therefore shows no mode while suspended (a
 *  choice of any mode is a change), and the previous mode is offered as one explicit action;
 *  `auto` only while the server still declares the tool read-only. A stale `off` suspends nothing. */
export interface GrantView { value: GrantMode | ''; suspended: GrantMode | null; reconfirm: boolean }
export function grantView(tool: Pick<Tool, 'grant' | 'removed' | 'annotations'> & Partial<Pick<Tool, 'unusable'>>): GrantView {
  const suspended = tool.grant.stale && !tool.removed && tool.grant.mode !== 'off' ? tool.grant.mode : null;
  // An unusable tool (its schema was not kept) can only be turned off (connections.set_grant).
  const reconfirm = suspended !== null && !tool.unusable && (suspended !== 'auto' || isReadOnly(tool));
  return { value: suspended ? '' : tool.grant.mode, suspended, reconfirm };
}

/** A platform administrator's stop, as the summary reports it. While it holds, enabling, testing,
 *  authenticating, calling, reading, rendering and changing the address or credential are refused
 *  (`mcp_platform_disabled`); renaming, disabling, deleting, grants and bindings are not. */
export const platformStopped = (summary: Pick<ConnectionSummary, 'platform_disabled'>) => summary.platform_disabled === true;

/** A bearer connection without its token (revoked, or forgotten with an old address). */
export const needsToken = (summary: Pick<ConnectionSummary, 'auth_kind' | 'has_credential'>) =>
  summary.auth_kind === 'bearer' && !summary.has_credential;

/** A connection pointed at another address and not yet discovered there (connections.update): a new
 *  address is the only change that clears `discovered_at`, and it marks every tool of the old server
 *  removed. (An old server that offered no tools leaves no such trace; the page then reads as a
 *  connection not yet tested.) */
export function pointedElsewhere(detail: Pick<ConnectionDetail, 'discovered_at' | 'tools'>): boolean {
  return detail.discovered_at == null && detail.tools.some((tool) => tool.removed);
}

/** What has to happen, and by whom, before an Agent uses a connection pointed elsewhere again: the
 *  credential, grants and every binding of the old server were dropped with its address. The first
 *  discovery of a personal connection there sets every usable tool to `confirm`
 *  (connections._record_discovery); a workspace's tools wait for a manager's grant. */
export function repointSteps(detail: Pick<ConnectionDetail, 'manage' | 'auth_kind' | 'has_credential' | 'binding'> & Partial<Pick<ConnectionDetail, 'owner_kind'>>): IntegrationText[] {
  const steps: IntegrationText[] = [];
  if (detail.manage) {
    if (detail.auth_kind === 'oauth' && !detail.has_credential) steps.push('nextAuthenticate');
    if (needsToken(detail)) steps.push('nextToken');
    steps.push('nextTest', detail.owner_kind === 'user' ? 'nextGrantPersonal' : 'nextGrant');
  } else {
    steps.push('nextManagers');
  }
  if (detail.binding.bound && !detail.binding.enabled) steps.push('nextBind');
  return steps;
}

/** Workspaces the person may add a workspace connection for: the roles connections.py accepts
 *  (owner, admin), on a workspace that is still active. */
export interface SessionWorkspace { id: string; name: string; role: string; status?: string; kind?: string }
export function manageableWorkspaces(workspaces: readonly SessionWorkspace[] | null | undefined): SessionWorkspace[] {
  return (workspaces ?? []).filter((ws) => (ws.role === 'owner' || ws.role === 'admin') && (ws.status === undefined || ws.status === 'active'));
}

/** What the OAuth callback left in the address: ?auth=ok, or ?auth=error&code=… */
export type OAuthOutcome = { ok: true } | { ok: false; code: string } | null;
export function oauthOutcome(search: URLSearchParams): OAuthOutcome {
  const auth = search.get('auth');
  if (auth === 'ok') return { ok: true };
  if (auth === 'error') return { ok: false, code: safeCode(search.get('code')) || 'mcp_oauth_failed' };
  return null;
}

/** A refusal code as the page may print it: bounded, no spaces, no markup. */
export const safeCode = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9_.-]{1,120}$/.test(value) ? value : '';

/** The authorization address the server returned, accepted only as an absolute http(s) URL. */
export function safeAuthorizationUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 8192) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch { return null; }
}

/** The catalogue key for an mcp_* refusal: exact where the backend's code is known, the
 *  family's sentence otherwise, null for anything that is not an MCP refusal. */
export function mcpErrorKey(code: string): IntegrationText | null {
  if (!code.startsWith('mcp_')) return null;
  if (Object.hasOwn(integrationCopy, code)) return code as IntegrationText;
  if (code.startsWith('mcp_oauth_')) return 'mcp_oauth_failed';
  return 'mcp_failed';
}
/** The zh-Hans sentence for an mcp_* refusal (the chat's agentErrorText translates it). */
export function mcpErrorText(code: string): string | null {
  const key = mcpErrorKey(code);
  return key ? integrationCopy[key][0] : null;
}

/** The code an error carries, unwrapping an idempotent replay of a refused request. */
export function errorCodeOf(error: unknown): string {
  if (!error || typeof error !== 'object') return '';
  const e = error as { code?: unknown; detail?: { code?: unknown } | null; upstream?: unknown };
  if (e.upstream) return '';
  const code = e.code === 'idempotent_failure' ? e.detail?.code : e.code;
  return safeCode(code);
}

export function formatDuration(ms: number | null | undefined): { key: IntegrationText; n: number } | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  return ms >= 10_000 ? { key: 'durationS', n: Math.round(ms / 1000) } : { key: 'durationMs', n: Math.round(ms) };
}

/** The alias the server would store (connections.slugify): lower case, hyphens, ≤ 24 characters. */
export function slugifyAlias(value: string): string {
  const base = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, MAX_ALIAS).replace(/-+$/g, '');
  return base;
}

/** The person's narrowing of every tool, as `agent.mcp.binding.set` replaces it whole. */
export function personalModes(tools: readonly Pick<Tool, 'name' | 'personal_mode'>[], change?: { tool: string; mode: PersonalMode | null }): Record<string, PersonalMode> {
  const out: Record<string, PersonalMode> = {};
  for (const tool of tools) if (tool.personal_mode) out[tool.name] = tool.personal_mode;
  if (change) {
    if (change.mode) out[change.tool] = change.mode;
    else delete out[change.tool];
  }
  return out;
}

/** The client configuration examples, generated from the deployment's own addresses.
 *
 *  The Python example is the official SDK's OAuth client as this server needs it: a pre-registered
 *  client id (the default policy) or, when the platform accepts Client ID Metadata Documents, the
 *  client's own document address; the callback returns code, state and `iss` (RFC 9207, which this
 *  authorization server always sends). The redirect address and the client id are the
 *  integrator's, read from the environment rather than invented here. */
export interface ClientExamples { generic: string; claude: string; python: string }
export function clientExamples(info: Pick<InboundInfo, 'endpoint' | 'scopes'> & Partial<Pick<InboundInfo, 'client_registration'>>): ClientExamples {
  const endpoint = info.endpoint;
  const generic = JSON.stringify({ mcpServers: { synwevo: { type: 'http', url: endpoint } } }, null, 2);
  const claude = `claude mcp add --transport http synwevo ${shellQuote(endpoint)}`;
  const scope = info.scopes.join(' ');
  const cimd = info.client_registration === 'cimd_and_preregistered';
  const python = [
    'import asyncio',
    'import os',
    'from urllib.parse import parse_qs, urlparse',
    '',
    'import httpx2',
    'from mcp import Client',
    'from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider',
    'from mcp.client.streamable_http import streamable_http_client',
    'from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken',
    '',
    `ENDPOINT = ${pyString(endpoint)}`,
    `SCOPE = ${pyString(scope)}`,
    '# Your client\'s callback address, one of those registered for it.',
    'REDIRECT_URI = os.environ["SYNWEVO_MCP_REDIRECT_URI"]',
    ...(cimd ? [
      '# The https address of your client\'s metadata document; it is the client id.',
      'CLIENT_METADATA_URL = os.environ["SYNWEVO_MCP_CLIENT_METADATA_URL"]',
    ] : [
      '# The client id the platform administrator registered for this client.',
      'CLIENT_ID = os.environ["SYNWEVO_MCP_CLIENT_ID"]',
    ]),
    '',
    '',
    'class MemoryTokens:',
    '    """A TokenStorage. Keep the issued tokens somewhere safe; this example keeps them in memory."""',
    '',
    '    def __init__(self, client: OAuthClientInformationFull | None = None):',
    '        self.tokens: OAuthToken | None = None',
    '        self.client = client',
    '',
    '    async def get_tokens(self): return self.tokens',
    '    async def set_tokens(self, tokens): self.tokens = tokens',
    '    async def get_client_info(self): return self.client',
    '    async def set_client_info(self, info): self.client = info',
    '',
    '',
    'async def open_browser(url: str) -> None:',
    '    print("Open this address, sign in and approve:", url)',
    '',
    '',
    'async def wait_for_callback() -> AuthorizationCodeResult:',
    '    query = parse_qs(urlparse(input("Paste the address you were sent back to: ")).query)',
    '    return AuthorizationCodeResult(code=query["code"][0], state=query["state"][0],',
    '                                   iss=query.get("iss", [None])[0])',
    '',
    '',
    'async def main() -> None:',
    '    auth = OAuthClientProvider(',
    '        server_url=ENDPOINT,',
    '        client_metadata=OAuthClientMetadata(',
    '            client_name="My MCP client",',
    '            redirect_uris=[REDIRECT_URI],',
    '            grant_types=["authorization_code", "refresh_token"],',
    '            response_types=["code"],',
    '            token_endpoint_auth_method="none",',
    '            scope=SCOPE,',
    '        ),',
    ...(cimd ? [
      '        storage=MemoryTokens(),',
      '        client_metadata_url=CLIENT_METADATA_URL,',
    ] : [
      '        storage=MemoryTokens(OAuthClientInformationFull(',
      '            client_id=CLIENT_ID, redirect_uris=[REDIRECT_URI], token_endpoint_auth_method="none")),',
    ]),
    '        redirect_handler=open_browser,',
    '        callback_handler=wait_for_callback,',
    '    )',
    '    async with httpx2.AsyncClient(auth=auth, timeout=httpx2.Timeout(30.0, read=300.0)) as http:',
    '        async with Client(streamable_http_client(ENDPOINT, http_client=http)) as client:',
    '            who = await client.call_tool("synwevo.whoami", {})',
    '            print(who.structured_content)',
    '            tools = await client.list_tools()',
    '            print([tool.name for tool in tools.tools])',
    '',
    '',
    'asyncio.run(main())',
  ].join('\n');
  return { generic, claude, python };
}

function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./:@%+=-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}
function pyString(value: string): string {
  return JSON.stringify(value);
}
