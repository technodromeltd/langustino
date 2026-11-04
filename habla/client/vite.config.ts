import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the Python voice server runs on :8777 and serves /ws + /api.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: {
      "/ws": { target: "ws://localhost:8777", ws: true },
      "/api": "http://localhost:8777",
    },
  },
});
