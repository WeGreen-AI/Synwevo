// Compile-only check of synwevo-docs.d.ts (never executed): the main flow must type-check and the
// `@ts-expect-error` lines must stay errors. From the repository root:
//   node_modules/.bin/tsc --noEmit --strict --module nodenext --moduleResolution nodenext \
//     --target es2022 apps/docs/sdk/typescript/test/types-check.ts
import type { IncomingHttpHeaders } from 'node:http';

import {
  ApiKey, Client, WorkspaceClient, ConflictError, credentialsFromEnv, isTerminal, SUCCESS_STATUSES, verifyWebhook,
  WaitTimeoutError, WebhookVerificationError,
  type Artifact, type Job, type JobStatus, type SourceFile, type WebhookEvent,
  type JobOptions, type RawJobCounts, type JobRun, type JobItem, type JobEvent, type Page,
} from '@synwevo/docs-sdk';
// The vendored-copy import from the README resolves to the same declarations.
import * as vendored from '../synwevo-docs.mjs';

const sameClient: typeof Client = vendored.Client;

// Historical and in-flight wire states must remain representable, without inventing defaults.
export function jobBoundaries(job: Job, item: JobItem, run: JobRun, event: JobEvent, page: Page<Job>): void {
  const emptyCounts: RawJobCounts = {};
  const historicOptions: JobOptions = { formats: ['docx'], error_policy: 'skip' };
  const unplanned: JobRun = { ...run, items: null };
  const historical: Job = { ...job, template: { id: null, name: null, format: null, version_no: null }, options: {} };
  const nullableCounts: Job['counts'] = { ...job.counts, documents: null };
  const diagnostics: JobItem = { ...item, issues: [{ code: 'render_failed', detail: [null, 1, 'context'] }, 'legacy'] };
  const cursor: Page<Job> = { ...page, offset: null, next_offset: null, next_cursor: null };
  const oldEvent: JobEvent = { ...event, type: 'job.future_event', data: null };
  // @ts-expect-error detail counters are required even when stored list counts were empty
  const missingCounts: Job['counts'] = {};
  // @ts-expect-error replay appears only on replayed requests
  const replay: boolean = job.idempotent_replay;
  // @ts-expect-error planning runs can have no item count yet
  const count: number = run.items;
  // @ts-expect-error diagnostics contain JSON, not callbacks
  const unsafeIssues: JobItem['issues'] = () => 'not JSON';
  console.log(emptyCounts, historicOptions, unplanned, historical, nullableCounts, diagnostics, cursor, oldEvent,
    missingCounts, replay, count, unsafeIssues);
}

export async function directoryFlow(client: WorkspaceClient): Promise<string> {
  const who = await client.whoami();
  const invite = await client.createInvitation({ email: 'synthetic@example.invalid', role: 'viewer' });
  const resent = await client.resendInvitation(invite.id);
  await client.revokeInvitation(resent.id);
  for await (const member of client.iterMembers({ role: 'viewer', limit: 1 })) {
    await client.changeMember(member.user_id, { role: 'editor', expectedRole: 'viewer' });
    await client.removeMember(member.user_id, { expectedRole: 'editor', reason: 'Synthetic cleanup' });
  }
  // @ts-expect-error a concurrency precondition is required
  await client.changeMember('user', { role: 'viewer' });
  // @ts-expect-error a directory integration cannot manage elevated roles
  await client.createInvitation({ email: 'synthetic@example.invalid', role: 'owner' });
  // @ts-expect-error workspace clients do not expose Docs operations
  await client.createProject({ name: 'not a directory action' });
  return who.workspace.id;
}

export async function copyFlow(client: Client, pid: string, targetWorkspaceId: string): Promise<string> {
  const targets = await client.copyTargets();
  const personal: boolean = targets.source_is_personal;
  const manifest = await client.copyManifest(pid, { sources: true });
  const request = await client.requestCopy(pid, { targetWorkspaceId, sources: true, purpose: 'Types check' }, { idempotencyKey: 'types:copy' });
  const listed = await client.listCopyRequests(pid, { status: 'pending', limit: 10 });
  const current = await client.getCopyRequest(request.id);
  const withdrawn = await client.withdrawCopyRequest(request.id);
  // @ts-expect-error approval is an interactive action, not a machine request field
  await client.requestCopy(pid, { targetWorkspaceId, decide: true });
  // @ts-expect-error request status values are stable machine identifiers
  await client.listCopyRequests(pid, { status: 'complete' });
  return `${personal} ${manifest.digest} ${listed.items.length} ${current.version} ${withdrawn.status}`;
}

export async function mainFlow(): Promise<string[]> {
  const client = new Client('http://127.0.0.1:8765', new ApiKey('dw_example_key'), {
    maxRetries: 2,
    onRetry: (r) => console.error(`retry ${r.attempt} after ${r.delayMs} ms (${r.status} ${r.code ?? ''})`),
  });

  const project = await client.createProject({ name: 'Types check' }, { idempotencyKey: 'types-check:project' });
  const replayed: boolean = project.$meta.replayed;

  const uploaded: SourceFile = await client.uploadSource(project.id, './customers.xlsx');
  const file = await client.waitForFile(uploaded.id, { timeoutMs: 60_000 });
  if (file.status === 'needs_ocr') {
    const ocr = await client.runSourceOcr(file.id, { pages: [0, 1] });
    const nowReady: boolean = ocr.status === 'ready';
    console.log(ocr.recognized.length, ocr.words, nowReady);
  }

  const template = await client.uploadTemplate('./template.docx', { name: 'Letter' });
  const created = await client.createFields(template.id, {
    schema: 'docweave.template-fields',
    version: 1,
    fields: [{ kind: 'text', name: 'Customer', find: 'Example Customer', occurrence: 1 }],
  });
  const fieldId = created.created[0]?.id;
  if (fieldId) {
    const head = (await client.getTemplate(template.id)).head;
    const edited = await client.updateField(template.id, fieldId, { baseHead: head, required: true, align: 'center' });
    const newHead: number = edited.head;
    console.log(newHead);
  }

  const current = await client.getProject(project.id);
  await client.updateProject(project.id, { baseSettingsRev: current.settings_rev, output: { formats: ['docx'], layout: 'warn' } });
  await client.bindTemplate(project.id, template.id);

  const submitted: Job = await client.createJob(project.id, { formats: ['docx', 'pdf'], errorPolicy: 'skip', layout: 'warn' });
  let job: Job;
  try {
    job = await client.waitForJob(submitted.id, {
      timeoutMs: 120_000,
      onEvent: (event) => console.log(event.seq, event.type, event.data),
    });
  } catch (e) {
    if (e instanceof WaitTimeoutError) await client.cancelJob(submitted.id);
    throw e;
  }
  const done: boolean = isTerminal(job);
  const succeeded = (SUCCESS_STATUSES as readonly JobStatus[]).includes(job.status);
  console.log(done, succeeded, job.counts.documents, job.counts.files);

  const paths: string[] = [];
  for await (const artifact of client.artifacts(job.id)) {
    const a: Artifact = artifact;
    const saved = await client.downloadArtifact(a, { outDir: './out' });
    paths.push(saved.reused ? saved.path : `${saved.path} (${saved.verified ? 'verified' : 'unverified'})`);
  }
  return paths;
}

export function handleDelivery(headers: IncomingHttpHeaders, rawBody: Buffer, secrets: string[]): string {
  try {
    const { id, event } = verifyWebhook(headers, rawBody, secrets);
    if (!event) return id;
    return describe(event);
  } catch (e) {
    if (e instanceof WebhookVerificationError) return `rejected: ${e.code}`;
    throw e;
  }
}

function describe(event: WebhookEvent): string {
  switch (event.type) {
    case 'file.ready':
    case 'file.needs_ocr':
    case 'file.rejected':
    case 'file.failed':
      return `${event.type} ${event.data.file_id} in ${event.data.project_id}`;
    case 'webhook.ping':
      return `ping ${event.data.subscription_id ?? ''}`;
    case 'copy.requested':
    case 'copy.approved':
    case 'copy.rejected':
    case 'copy.withdrawn':
    case 'copy.expired':
    case 'copy.done':
    case 'copy.failed':
    case 'copy.stale':
      return `${event.type} ${event.data.copy_id} → ${event.data.target_workspace_id}`;
    default:
      return `${event.type} ${event.job.id} ${event.job.status}`;
  }
}

export async function fromEnvironment(): Promise<void> {
  const client = new Client(process.env.SYNWEVO_BASE_URL ?? '', await credentialsFromEnv());
  try {
    await client.deleteField('tpl', 'field', { baseHead: 3, projectId: 'p', cascade: false });
  } catch (e) {
    if (e instanceof ConflictError && e.code === 'stale_template') console.log(e.details?.head);
  }
}

// ---------------------------------------------------------------- misuse the declarations must catch
export function misuse(client: Client): void {
  // @ts-expect-error baseHead (the template head you read) is required
  void client.updateField('tpl', 'field', { required: true });
  // @ts-expect-error `colour` is not a property the server lets a field change
  void client.updateField('tpl', 'field', { baseHead: 1, colour: '#000000' });
  // @ts-expect-error 'done' is not a job status
  const status: JobStatus = 'done';
  // @ts-expect-error error_policy is block or skip
  void client.createJob('p', { errorPolicy: 'ignore' });
  // @ts-expect-error baseSettingsRev is required
  void client.updateProject('p', { name: 'Renamed' });
  // @ts-expect-error a preview names one sample by index or by key, not both
  void client.downloadPreview('p', './preview.docx', { index: 0, key: 'k' });
  console.log(status, sameClient);
}
