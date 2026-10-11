/** One Vite dev server per test file, for `ssrLoadModule` only.
 *
 *  Node runs the test files in parallel processes. Vite's client dependency optimizer writes
 *  `<cacheDir>/deps` through a temporary directory and a rename, so servers that shared this
 *  workspace's `node_modules/.vite` raced on it (ENOTEMPTY on rmdir, 2026-10-10, with the
 *  leftover `deps_temp_*` directories as evidence). SSR loads do not use the client bundle, so
 *  the optimizer is left off, and each server owns a private cache directory in the OS temp
 *  directory that it removes when it closes — never another process's cache. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, type InlineConfig, type ViteDevServer } from 'vite';

export const WEB = fileURLToPath(new URL('../', import.meta.url));

export async function testServer(config: InlineConfig = {}): Promise<ViteDevServer> {
  const cacheDir = mkdtempSync(join(tmpdir(), 'synwevo-suite-web-test-'));
  let server: ViteDevServer;
  try {
    server = await createServer({
      root: WEB, configFile: false, logLevel: 'error', appType: 'custom', cacheDir,
      optimizeDeps: { noDiscovery: true, include: [] },
      server: { middlewareMode: true, hmr: false, ws: false, watch: null },
      ...config,
    });
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
