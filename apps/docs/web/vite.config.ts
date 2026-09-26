import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { brandIconsInDev } from "@synwevo/ui/vite";

// Synwevo Docs 自己的构建产物。三个外壳各自构建，各自的静态资源目录也不同（assets /
// portal-assets / account-assets）——单进程部署要同时提供其中两个，同名的 /assets 会互相遮盖。
export default defineConfig({
  plugins: [react(), brandIconsInDev()],
  // /favicon.ico at the site root, generated from the confirmed mark (scripts/brand_assets.py).
  publicDir: "../../../packages/ui/public-brand",
  server: {
    port: 5173,
    // Everything the sign-in round trip touches goes to the server, not only /api: RequireSession
    // sends a signed-out visitor to /auth/start (or /login), and the Vite server used to answer
    // those with the SPA's index.html — a redirect loop or a 页面不存在 instead of signing in.
    proxy: Object.fromEntries(["/api", "/auth", "/login", "/logout", "/oidc", "/account"].map((p) => [p, { target: "http://127.0.0.1:8765", changeOrigin: false }])),
  },
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
