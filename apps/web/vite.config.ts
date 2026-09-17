import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@shared": path.resolve(__dirname, "../../packages/shared-types/src"),
    },
  },
  server: {
    port: 5173,
    host: true,
    strictPort: false,
    // Proxy API calls to the FastAPI backend.
    // VITE_API_URL in .env should be EMPTY so the frontend uses these relative URLs.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
  optimizeDeps: {
    exclude: ["@shared/types"],
  },
});
