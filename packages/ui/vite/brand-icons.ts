import { createReadStream, existsSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

/** Where the shells' index.html point their icon links, relative to each shell's root. */
const PREFIX = "/packages/ui/src/brand/";
const BRAND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "brand");
const TYPES: Record<string, string> = { ".png": "image/png", ".ico": "image/x-icon" };

/** Serves the logo icons to `vite dev`.
 *
 *  Each shell's index.html links its tab and home-screen icons as
 *  `../../../packages/ui/src/brand/icon-32.png`. `vite build` resolves that path and
 *  fingerprints the file into the shell's own asset directory; the dev server leaves the href as
 *  written, the browser asks for `/packages/ui/src/brand/…`, and without this the dev server's
 *  HTML fallback would answer an image request with index.html. Build output is unaffected. */
export function brandIconsInDev(): Plugin {
  return {
    name: "synwevo-brand-icons",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const path = (req.url || "").split("?")[0];
        if (!path.startsWith(PREFIX)) return next();
        const name = basename(path);
        const type = TYPES[name.slice(name.lastIndexOf("."))];
        const file = join(BRAND_DIR, name);
        if (!type || name !== path.slice(PREFIX.length) || !existsSync(file)) return next();
        res.setHeader("Content-Type", type);
        res.setHeader("Cache-Control", "no-cache");
        createReadStream(file).pipe(res);
      });
    },
  };
}
