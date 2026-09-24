import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// DocWeave 自己的构建产物。三个外壳各自构建，各自的静态资源目录也不同（assets /
// portal-assets / account-assets）——单进程部署要同时提供其中两个，同名的 /assets 会互相遮盖。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
