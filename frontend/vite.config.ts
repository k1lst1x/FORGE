import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],

  // Three pages, not one. index.html is the landing, console.html is the
  // operator console; both are plain HTML that load their JS from public/,
  // so they need no build step and no React. app.html is the original React
  // entry, kept so nothing is lost.
  build: {
    rollupOptions: {
      input: {
        main: "index.html",
        dashboard: "dashboard.html",
        console: "console.html",
        app: "app.html",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
