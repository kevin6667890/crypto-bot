import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({mode}) => {
  const env=loadEnv(mode,".","");
  return {
    plugins: [react()],
    build: {
      chunkSizeWarningLimit: 300,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("node_modules/lightweight-charts")) return "charts-vendor";
            if (id.includes("node_modules/react") || id.includes("node_modules/framer-motion")) return "react-vendor";
            if (id.includes("node_modules/lucide-react")) return "icons-vendor";
          },
        },
      },
    },
    server: {
      proxy: {
        "/api": env.VITE_DEV_API_TARGET || "http://127.0.0.1:8765",
      },
    },
  };
});
