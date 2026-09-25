import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Python app (kk-gui) serves the built files from src/knowledge_keeper/web/static.
// During frontend development, `npm run dev` serves the React app with hot reload and
// forwards /api calls to a running backend (start it with: kk-gui --no-browser).
const backend = process.env.KK_BACKEND || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/knowledge_keeper/web/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: backend } },
  },
});
