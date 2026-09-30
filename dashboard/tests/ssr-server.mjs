import { resolve } from 'node:path';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { createServer } from 'vite';

/** Lightweight Vite/Svelte setup for SSR-only component/controller tests. */
export function createSsrServer() {
  return createServer({
    configFile: false,
    root: process.cwd(),
    plugins: [svelte({ preprocess: vitePreprocess() })],
    resolve: { alias: { '$lib': resolve(process.cwd(), 'src/lib') } },
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom'
  });
}
