import type { ContextItem, ContextKind, ContextPage, ContextReference, SelectedContext, Workspace } from './types';

export const MAX_CONTEXTS = 5;
export function contextKey(ref: Pick<ContextReference, 'kind' | 'workspace_id' | 'resource_id'>): string {
  return JSON.stringify([ref.kind, ref.workspace_id, ref.resource_id]);
}
export function contextSpaces(spaces: Workspace[], scope: string[] | null): Workspace[] {
  return spaces.filter((ws) => (scope === null || scope.includes(ws.workspace_id)) && ws.processing !== 'none');
}
export function addContext(selected: SelectedContext[], next: SelectedContext): SelectedContext[] {
  if (selected.length >= MAX_CONTEXTS || selected.some((item) => contextKey(item.reference) === contextKey(next.reference))) return selected;
  return [...selected, next];
}
/** Copy only the server-issued reference fields, preserving their exact values and order. */
export function contextReferences(selected: SelectedContext[]): ContextReference[] | undefined {
  return selected.length ? selected.map(({ reference: { kind, workspace_id, resource_id, snapshot } }) =>
    ({ kind, workspace_id, resource_id, snapshot })) : undefined;
}

/** Root-relative application links only, including protection against backslash/control normalization. */
export function safeContextPath(path: unknown): string | null {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || /[\\\u0000-\u0020\u007f]/.test(path)) return null;
  try {
    const decoded = decodeURIComponent(path);
    if (decoded.startsWith('//') || /[\\\u0000-\u001f\u007f]/.test(decoded)) return null;
    const url = new URL(path, 'https://context.invalid');
    return url.origin === 'https://context.invalid' ? path : null;
  } catch { return null; }
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
function invalid(): never { throw Object.assign(new Error('invalid_context_response'), { code: 'invalid_context_response' }); }
export function contextPage(value: unknown, workspace: string, kind: ContextKind, offset: number): ContextPage {
  if (!object(value) || !Array.isArray(value.items) || value.items.length > 50 || typeof value.limited !== 'boolean'
    || !(value.next_offset === null || (Number.isSafeInteger(value.next_offset) && Number(value.next_offset) > offset))) return invalid();
  const items = value.items.map((item) => {
    if (!object(item) || item.kind !== kind || item.workspace_id !== workspace || !nonempty(item.resource_id)
      || typeof item.title !== 'string') return invalid();
    return { kind, workspace_id: workspace, resource_id: item.resource_id, title: item.title };
  });
  if (value.next_offset !== null && !items.length) return invalid();
  return { items, next_offset: value.next_offset as number | null, limited: value.limited };
}
export function resolvedContext(value: unknown, item: ContextItem): SelectedContext {
  if (!object(value) || !object(value.reference) || typeof value.title !== 'string' || typeof value.workspace_name !== 'string'
    || !(value.open_path === null || typeof value.open_path === 'string')) return invalid();
  const ref = value.reference;
  if (ref.kind !== item.kind || ref.workspace_id !== item.workspace_id || ref.resource_id !== item.resource_id
    || typeof ref.snapshot !== 'string' || !/^[a-f0-9]{64}$/i.test(ref.snapshot)) return invalid();
  return { reference: { kind: item.kind, workspace_id: item.workspace_id, resource_id: item.resource_id, snapshot: ref.snapshot },
    title: value.title, workspace_name: value.workspace_name, open_path: safeContextPath(value.open_path) };
}

/** Recognize refusals without displaying raw server text in another language. */
export function contextErrorKey(error: unknown): 'changed' | 'unavailable' | 'tooLarge' | 'invalid' | 'outsideScope' | 'invalidResponse' | null {
  if (!object(error) || error.upstream) return null;
  const code = error.code === 'idempotent_failure' && object(error.detail) ? error.detail.code : error.code;
  if (code === 'context_changed') return 'changed';
  if (['context_unavailable', 'source_policy_changed', 'source_access_denied', 'source_access_changed', 'not_a_member',
    'form_not_found', 'table_not_found', 'permission_denied', 'forbidden', 'app_unavailable'].includes(String(code))) return 'unavailable';
  if (code === 'context_too_large') return 'tooLarge';
  if (code === 'invalid_context') return 'invalid';
  if (code === 'outside_conversation_scope') return 'outsideScope';
  if (code === 'invalid_context_response') return 'invalidResponse';
  return null;
}
