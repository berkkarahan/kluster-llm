import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { createServer } from 'vite';

/** Lightweight Vite/Svelte setup for SSR-only component/controller tests. */
export function createSsrServer() {
  return createServer({
    configFile: false,
    root: process.cwd(),
    plugins: [svelte({ preprocess: vitePreprocess() })],
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom'
  });
}
