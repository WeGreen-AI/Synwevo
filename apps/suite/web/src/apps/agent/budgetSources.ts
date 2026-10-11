import { resolvedContext } from './context';
import { fileRecord } from './files';
import type { BudgetIntent } from './budget';
import type { ContextKind } from './types';

export interface BudgetSource { title: string; workspace: string | null; kind: 'file' | ContextKind }
export type SourceReader = (operation: string, input: Record<string, unknown>, workspace: string | null, signal: AbortSignal) => Promise<unknown>;
export class SourceReviewError extends Error {}

/** Review only the references the person chose. Reads use personal operations and the same
 * snapshot checks as the picker; never substitute a changed version into a payment intent.
 * No source body or link is retained in the review. The server rechecks at quote/admission. */
export async function resolveBudgetSources(intent: BudgetIntent, read: SourceReader, signal: AbortSignal): Promise<BudgetSource[]> {
  const refs = intent.context_refs ?? [], files = intent.file_refs ?? [];
  if (refs.length + files.length > 5) throw new SourceReviewError();
  try {
    return await Promise.all([
      ...refs.map(async (ref): Promise<BudgetSource> => {
        const { workspace_id, kind, resource_id } = ref;
        const value = resolvedContext(await read('agent.context.resolve', { workspace_id, kind, resource_id }, null, signal), { workspace_id, kind, resource_id, title: '' });
        if (value.reference.snapshot !== ref.snapshot) throw new SourceReviewError();
        return { title: value.title, workspace: value.workspace_name, kind };
      }),
      ...files.map(async (ref): Promise<BudgetSource> => {
        const value = fileRecord(await read('agent.file.get', { file_id: ref.file_id }, null, signal), ref.file_id);
        if (value.status !== 'ready' || value.reference?.snapshot !== ref.snapshot) throw new SourceReviewError();
        return { title: value.filename, workspace: null, kind: 'file' };
      }),
    ]);
  } catch { throw new SourceReviewError(); }
}
