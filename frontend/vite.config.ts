import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backendTarget =
    process.env.VITE_BACKEND_TARGET ||
    env.VITE_BACKEND_TARGET ||
    "http://127.0.0.1:8123";
  const proxy = {
    "/api": {
      target: backendTarget,
      changeOrigin: true,
    },
    "/ws": {
      target: backendTarget.replace(/^http/, "ws"),
      ws: true,
      changeOrigin: true,
    },
  };

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 3000,
      strictPort: true,
      proxy,
    },
    preview: {
      host: "0.0.0.0",
      port: 3000,
      strictPort: true,
      proxy,
    },
    build: {
      target: "es2022",
      sourcemap: true,
    },
  };
});
