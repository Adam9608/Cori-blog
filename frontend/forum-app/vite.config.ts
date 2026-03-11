import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const publicBase = process.env.FORUM_WEB_BASE || "/forum-next/";

export default defineConfig({
  base: publicBase,
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
});
