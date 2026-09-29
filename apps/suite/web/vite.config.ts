import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { brandIconsInDev } from "@synwevo/ui/vite";

// The business applications' shell (Forms, Tables, DataPrep, Approvals, Extract, the personal
// Agent), served by the portal role on the workplace origin under each application's path. Its
// own asset directory: that origin also serves the portal shell (`portal-assets`), and the
// single-process deployment serves every shell on one origin, where same-named directories hide
// each other.
export default defineConfig({
  plugins: [react(), brandIconsInDev()],
  publicDir: "../../../packages/ui/public-brand",
  server: {
    port: 5177,
    proxy: { "/api": { target: "http://127.0.0.1:8769", changeOrigin: false } },
  },
  build: { outDir: "dist", assetsDir: "suite-assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
