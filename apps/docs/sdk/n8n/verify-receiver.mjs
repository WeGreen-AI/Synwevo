// Loopback verifier companion for receive-documents.json; no Docs credentials or network fetches.
import { createServer } from 'node:http';
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, renameSync, openSync, closeSync, fsyncSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyWebhook, WebhookVerificationError } from '../typescript/synwevo-docs.mjs';

export function receiver({ authToken, stateDir, tenant, leaseMs = 60000 }) {
  if (!authToken || !tenant || !stateDir || leaseMs < 1000) throw new Error('Receiver configuration required');
  mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  const file = id => join(stateDir, createHash('sha256').update(id).digest('hex') + '.json');
  const read = id => { try { return JSON.parse(readFileSync(file(id), 'utf8')); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } };
  const save = row => {
    const temporary = file(row.id) + '.' + randomUUID();
    const fd = openSync(temporary, 'wx', 0o600);
    try { writeFileSync(fd, JSON.stringify(row) + '\n'); fsyncSync(fd); } finally { closeSync(fd); }
    renameSync(temporary, file(row.id));
    const dir = openSync(stateDir, 'r');
    try { fsyncSync(dir); } finally { closeSync(dir); }
  };
  return createServer(async (req, res) => {
    const reply = (code, value = {}) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    if (req.method === 'GET' && req.url === '/healthz') return reply(200, { status: 'ok', instance: 'test' });
    const got = Buffer.from(req.headers.authorization || ''); const want = Buffer.from(`Bearer ${authToken}`);
    if (got.length !== want.length || !timingSafeEqual(got, want)) return reply(401, { error: 'unauthorized' });
    if (req.method !== 'POST') return reply(405);
    try {
      const chunks = []; let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 1024 * 1024) return reply(413, { error: 'body_too_large' });
        chunks.push(chunk);
      }
      const raw = Buffer.concat(chunks);
      if (req.url === '/verify') {
        // n8n supplies this header from its encrypted Custom Auth credential, never from the event.
        const secret = req.headers['x-synwevo-signing-secret'];
        if (!secret) return reply(401, { error: 'missing_signing_credential' });
        const verified = verifyWebhook(req.headers, raw, secret);
        const event = verified.event;
        if (!event || event.id !== verified.id || !/^[A-Za-z0-9_-]+$/.test(event.id) || event.tenant !== tenant || event.type !== 'job.completed'
            || !/^[A-Za-z0-9_-]+$/.test(event.job?.id || '')) return reply(400, { error: 'unexpected_event' });
        const fingerprint = createHash('sha256').update(raw).digest('hex');
        const previous = read(event.id);
        if (previous && previous.fingerprint !== fingerprint) return reply(409, { error: 'event_id_reused' });
        if (previous?.completed) return reply(200, { action: 'duplicate', responseCode: 204, id: event.id });
        if (previous?.expires > Date.now()) return reply(200, { action: 'pending', responseCode: 503, id: event.id });
        const row = { id: event.id, fingerprint, tenant, jobId: event.job.id, lease: randomUUID(),
          expires: Date.now() + leaseMs, completed: false };
        save(row);
        return reply(200, { action: 'fetch', responseCode: 200, id: row.id, tenant, jobId: row.jobId, lease: row.lease });
      }
      if (req.url === '/complete' || req.url === '/release') {
        const input = JSON.parse(raw.toString('utf8'));
        if (typeof input.id !== 'string') return reply(400);
        const row = read(input.id);
        if (!row || row.lease !== input.lease || row.expires <= Date.now() || row.completed) return reply(409, { error: 'lease_lost' });
        row.completed = req.url === '/complete'; row.expires = 0;
        save(row);
        return reply(200, { responseCode: row.completed ? 204 : 503, id: row.id });
      }
      return reply(404);
    } catch (e) {
      // Never reflect request headers, credentials or raw bodies into errors/logs.
      return reply(e instanceof WebhookVerificationError ? 401 : 400, { error: e instanceof WebhookVerificationError ? e.code : 'invalid_request' });
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [authFile, stateDir, tenant, port] = process.argv.slice(2);
  if (!authFile || !stateDir || !tenant || !/^[0-9]+$/.test(port || '') || Number(port) < 1 || Number(port) > 65535) {
    console.error('Usage: node verify-receiver.mjs AUTH_TOKEN_FILE STATE_DIR TENANT LOOPBACK_PORT');
    process.exitCode = 2;
  } else {
    const server = receiver({ authToken: readFileSync(authFile, 'utf8').trim(), stateDir, tenant });
    server.listen(Number(port), '127.0.0.1');
    process.on('SIGTERM', () => server.close());
  }
}
