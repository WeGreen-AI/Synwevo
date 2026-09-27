import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { brandIconsInDev } from "@synwevo/ui/vite";

// The messages shell (Synwevo Message). Its own build, not a route in the account shell it moved
// out of: the account service refuses the conversation API by role, so a shared shell would
// render a page whose every call is a correct refusal presented as a broken page. The asset
// directory is deliberately not `assets`: the single-process deployment serves this shell beside
// the workbench and the account pages on one origin, and same-named directories hide each other.
export default defineConfig({
  plugins: [react(), brandIconsInDev()],
  // /favicon.ico at the site root, generated from the confirmed mark (scripts/brand_assets.py).
  publicDir: "../../../packages/ui/public-brand",
  server: {
    port: 5176,
    proxy: { "/api": { target: "http://127.0.0.1:8768", changeOrigin: false } },
  },
  build: { outDir: "dist", assetsDir: "messages-assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
