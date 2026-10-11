/** One Vite dev server per test file, for `ssrLoadModule` only.
 *
 *  Node runs the test files in parallel processes. Vite's client dependency optimizer writes
 *  `<cacheDir>/deps` through a temporary directory and a rename, so servers that shared this
 *  workspace's `node_modules/.vite` raced on it (ENOTEMPTY on rmdir — the Suite tests failed
 *  that way on 2026-10-10, and this workspace's cache held five leftover `deps_temp_*`
 *  directories on 2026-10-11). SSR loads do not use the client bundle, so the optimizer is
 *  left off, and each server owns a private cache directory in the OS temp directory that it
 *  removes when it closes — never another process's cache. Same contract as
 *  apps/suite/web/tests/_vite.ts. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

export const WEB = fileURLToPath(new URL('../', import.meta.url));

/** `config` is the test's own Vite config; the private cache and the disabled optimizer are added to it. */
export async function testServer(config = {}) {
  const cacheDir = mkdtempSync(join(tmpdir(), 'synwevo-account-web-test-'));
  let server;
  try {
    server = await createServer({ root: WEB, cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, ...config });
  } catch (error) {
    rmSync(cacheDir, { recursive: true, force: true });
    throw error;
  }
  const close = server.close.bind(server);
  server.close = async () => {
    try { await close(); } finally { rmSync(cacheDir, { recursive: true, force: true }); }
  };
  return server;
}
