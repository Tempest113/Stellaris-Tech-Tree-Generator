import { existsSync, readFileSync } from "node:fs";
import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig, type Plugin } from "vite";

/**
 * Names the page after the dataset it is built with, in index.html itself, so
 * the browser tab and link previews (which run no script) show it from the start.
 */
function datasetTitle(): Plugin {
  return {
    name: "dataset-title",
    transformIndexHtml(html) {
      const path = new URL("./public/data/dataset.json", import.meta.url);
      if (!existsSync(path)) return html;
      const title = (JSON.parse(readFileSync(path, "utf-8")) as { meta?: { title?: string } }).meta?.title;
      if (!title) return html;
      const escaped = title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return html.replace(/<title>[^<]*<\/title>/, `<title>${escaped}</title>`);
    },
  };
}

// `npm run dev:phone` serves over HTTPS on the local network, with a
// self-signed certificate the phone asks to accept once. Phone browsers that
// force HTTPS refuse the plain dev server, and the clipboard behind Copy Link
// only works on a secure page. Plain `npm run dev` stays HTTP, so the desktop
// preview needs no certificate.
export default defineConfig(({ mode }) => ({
  base: "./",
  build: { target: "es2022", assetsInlineLimit: 0 },
  plugins: [datasetTitle(), ...(mode === "phone" ? [basicSsl()] : [])],
}));
