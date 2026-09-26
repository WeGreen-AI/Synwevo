import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { brandIconsInDev } from "@synwevo/ui/vite";

// 门户外壳。它不是 Synwevo Docs SPA 里的一条路由：一个外壳同时拥有两者，就意味着门户的路由器也拥有
// 了 Synwevo Docs 的每一条路径——这正是一个新入口"吞掉"它本该并列的应用的方式。
export default defineConfig({
  plugins: [react(), brandIconsInDev()],
  // /favicon.ico at the site root, generated from the confirmed mark (scripts/brand_assets.py).
  publicDir: "../../../packages/ui/public-brand",
  server: {
    port: 5174,
    proxy: { "/api": { target: "http://127.0.0.1:8769", changeOrigin: false } },
  },
  build: { outDir: "dist", assetsDir: "portal-assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
