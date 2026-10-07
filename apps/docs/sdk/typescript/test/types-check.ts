// Compile-only check of synwevo-docs.d.ts (never executed): the main flow must type-check and the
// `@ts-expect-error` lines must stay errors. From the repository root:
//   node_modules/.bin/tsc --noEmit --strict --module nodenext --moduleResolution nodenext \
//     --target es2022 apps/docs/sdk/typescript/test/types-check.ts
import type { IncomingHttpHeaders } from 'node:http';

import {
  ApiKey, Client, WorkspaceClient, ConflictError, credentialsFromEnv, isTerminal, SUCCESS_STATUSES, verifyWebhook,
  WaitTimeoutError, WebhookVerificationError,
  type Artifact, type Delivery, type Job, type JobStatus, type SourceFile, type WebhookEvent,
  type JobOptions, type RawJobCounts, type JobRun, type JobItem, type JobEvent, type Page, type Upload,
} from '@synwevo/docs-sdk';
// The vendored-copy import from the README resolves to the same declarations.
import * as vendored from '../synwevo-docs.mjs';

const sameClient: typeof Client = vendored.Client;

export async function redeliveryCompatibility(client: Client): Promise<Delivery> {
  await client.redeliver('subscription', 'delivery');
  await client.redeliver('subscription', 'delivery', {});
  const receipt = await client.redeliver('subscription', 'delivery', { idempotencyKey: 'types:redelivery' });
  const replayed: boolean = receipt.$meta.replayed;
  // @ts-expect-error explicit keys must be strings
  await client.redeliver('subscription', 'delivery', { idempotencyKey: 123 });
  // @ts-expect-error retries are controlled by the transport, not a redelivery option
  await client.redeliver('subscription', 'delivery', { retry: true });
  console.log(replayed);
  return receipt;
}

export async function uploadBoundary(client: Client, filePath: string): Promise<Upload> {
  const upload = await client.resumableUpload(filePath, { purpose: 'source' });
  const location: string = upload.upload_url;
  const bytes: number = upload.received;
  const nullable: Upload = { ...upload, sha256: null, purpose: null };
  // @ts-expect-error Uploads do not have a source parser's ready state.
  const impossible: Upload = { ...upload, status: 'ready' };
  // @ts-expect-error The digest may be null before completion.
  const digest: string = upload.sha256;
  console.log(location, bytes, impossible, digest);
  return nullable;
}

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

// ---------------------------------------------------------------- business applications
import { SuiteClient, UserToken, SUITE_RESOURCE, type RotatedTokens, type SuiteEvent, type SuiteEventPage,
  type SuiteResult, type SuiteRun, type SuiteWebhook, type SuiteWebhookDelivery, type SuiteWebhookEvent } from '@synwevo/docs-sdk';

export async function suiteBoundary(stored: { refreshToken: string }, save: (t: RotatedTokens) => Promise<void>): Promise<string[]> {
  const token = new UserToken({ refreshToken: stored.refreshToken, clientId: 'crm-sync', clientSecret: 'secret',
    tokenUrl: 'https://accounts.synwevo.com/oidc/token', resource: SUITE_RESOURCE, onRotate: save });
  const suite = new SuiteClient(undefined, token);
  const who = await suite.whoami();
  const role: 'owner' | 'admin' | 'editor' | 'viewer' = who.role;
  const made = await suite.invoke<{ id: string }>('tables.record.create',
    { table_id: 't', cells: {} }, { idempotencyKey: 'order-42', correlationId: 'sync-7' });
  const plain: SuiteResult<{ id: string }> = made;
  const replayed: boolean = made.$meta.replayed && plain.replayed && made.output.id.length > 0;
  const run: SuiteRun = await suite.wait(made.run_id, { timeoutMs: 1000 });
  const hidden: boolean = run.snapshot_hidden;
  const granted = (await suite.operations({ app: 'tables' })).operations.filter((o) => o.granted).map((o) => o.scope);
  // @ts-expect-error An API key is not a person's delegation.
  const wrong = new SuiteClient(undefined, new ApiKey('dw_x_y'));
  // @ts-expect-error A run's input is never returned.
  const input: string = run.input;
  // @ts-expect-error Scopes are a closed set.
  const scope: 'agent.read' = who.scopes[0];
  console.log(role, replayed, hidden, wrong, input, scope);
  return granted;
}

export async function suiteEvents(suite: SuiteClient, saved: string | undefined, save: (cursor: string) => Promise<void>): Promise<string[]> {
  const opened: string[] = [];
  for await (const page of suite.eventPages({ cursor: saved, types: ['tables.record.changed'], pageSize: 100 })) {
    const typed: SuiteEventPage = page;
    for (const event of typed.items) {
      const e: SuiteEvent = event;
      const table: string | undefined = e.refs.table_id;
      const mine: string | null = e.run_id;
      opened.push(`${e.object.kind}:${e.object.id}:${table}:${mine}`);
      // @ts-expect-error An event never carries the record's data.
      void e.data;
    }
    await save(page.next_cursor);
  }
  const first = await suite.eventsPage({ start: 'now', limit: 1 });
  const more: boolean = first.has_more;
  for await (const event of suite.events({ cursor: first.next_cursor, follow: true, intervalMs: 1000 })) opened.push(event.id);
  // @ts-expect-error types is a list of names, not a comma-separated string.
  await suite.eventsPage({ types: 'tables.record.changed' });
  // @ts-expect-error A feed starts at the oldest retained event or now.
  await suite.eventsPage({ start: 'yesterday' });
  console.log(more);
  return opened;
}

export async function suiteWebhooks(suite: SuiteClient, keep: (secret: string) => Promise<void>, raw: string): Promise<string> {
  const made = await suite.createWebhook({ url: 'https://hooks.example.com/synwevo', events: ['tables.*', 'forms.submission.created'] });
  await keep(made.secret);
  const read: SuiteWebhook = await suite.getWebhook(made.id);
  // @ts-expect-error The secret is only in the answer to create and rotate.
  void read.secret;
  const changed = await suite.updateWebhook(made.id, { events: ['*'] }, { version: read.version });
  const state: 'active' | 'paused' | 'disabled' = changed.status;
  const rotated = await suite.rotateWebhookSecret(made.id, { overlapS: 3600 });
  await keep(rotated.secret);
  const ping: SuiteWebhookDelivery = await suite.pingWebhook(made.id);
  const log = await suite.webhookDeliveries(made.id, { status: 'failed', limit: 20 });
  for (const d of log.items) if (d.status === 'failed') await suite.redeliverWebhook(made.id, d.id);
  // @ts-expect-error A delivery is pending, delivered, failed or dropped.
  await suite.webhookDeliveries(made.id, { status: 'sent' });
  // @ts-expect-error events is a list.
  await suite.createWebhook({ url: 'https://hooks.example.com/x', events: 'tables.*' });
  const body = JSON.parse(raw) as SuiteWebhookEvent;
  const where = body.object ? `${body.object.kind}:${body.object.id}` : 'ping';
  // @ts-expect-error A delivery never carries the record's data.
  void body.data;
  await suite.deleteWebhook(made.id);
  return `${state}:${ping.last_code}:${where}:${body.workspace_id}`;
}

export async function suiteFiles(suite: SuiteClient, path: string): Promise<number> {
  const file = await suite.extractUpload(path, { idempotencyKey: 'invoice-00127' });
  const id: string = file.id;
  const original: Uint8Array = await suite.extractDownload(id);
  const staged = await suite.tablesImportUpload('tbl_1', new Uint8Array([1]), { filename: 'codes.csv' });
  const rows: number = staged.row_count;
  const sheet: Uint8Array = await suite.dataprepExport('dset_1', { format: 'xlsx' });
  // @ts-expect-error An import upload has no idempotency key: a repeat stages a second import.
  await suite.tablesImportUpload('tbl_1', path, { idempotencyKey: 'k' });
  // @ts-expect-error Only csv, xlsx and json are exported.
  await suite.dataprepExport('dset_1', { format: 'pdf' });
  return original.length + rows + sheet.length;
}
