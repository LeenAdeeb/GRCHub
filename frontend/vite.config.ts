import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by the FastAPI backend at /ui/. In `npm run dev` the API paths are proxied.
const API = "http://127.0.0.1:8000";
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: {
      "/requirements": API,
      "/stats": API,
      "/frameworks": API,
      "/subdomains": API,
      "/health": API,
    },
  },
});
