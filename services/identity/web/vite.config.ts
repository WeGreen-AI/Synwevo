import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { brandIconsInDev } from "@synwevo/ui/vite";

// 账户与安全外壳。独立入口而不是 Synwevo Docs SPA 里的一条路由：账户服务的接口按角色拒绝业务调用，
// 共用外壳会渲染出一份随后被自己拒绝的项目列表——一次正确的拒绝被呈现成一个坏掉的页面。
export default defineConfig({
  plugins: [react(), brandIconsInDev()],
  // /favicon.ico at the site root, generated from the confirmed mark (scripts/brand_assets.py).
  publicDir: "../../../packages/ui/public-brand",
  server: {
    port: 5175,
    proxy: { "/api": { target: "http://127.0.0.1:8770", changeOrigin: false } },
  },
  build: { outDir: "dist", assetsDir: "account-assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
