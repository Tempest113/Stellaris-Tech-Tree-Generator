import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig } from "vite";

// `npm run dev:phone` serves over HTTPS on the local network, with a
// self-signed certificate the phone asks to accept once. Phone browsers that
// force HTTPS refuse the plain dev server, and the clipboard behind Copy Link
// only works on a secure page. Plain `npm run dev` stays HTTP, so the desktop
// preview needs no certificate.
export default defineConfig(({ mode }) => ({
  base: "./",
  build: { target: "es2022", assetsInlineLimit: 0 },
  plugins: mode === "phone" ? [basicSsl()] : [],
}));
