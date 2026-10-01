import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on :8000 (uvicorn mrvsim.api.server:app); the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } } },
  build: { outDir: "dist" },
});
