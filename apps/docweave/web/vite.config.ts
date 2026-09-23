import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));

// Two entries, not one app with a portal route. The portal is served from its own origin by a
// process running with DOCWEAVE_ROLE=portal, and a single SPA shell would mean the portal's
// router also owned every DocWeave path — which is exactly how a new entry point swallows the
// application it was meant to sit beside.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: {
        main: resolve(here, "index.html"),
        portal: resolve(here, "portal.html"),
        accounts: resolve(here, "accounts.html"),
      },
    },
  },
});
