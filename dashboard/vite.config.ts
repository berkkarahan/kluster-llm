import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    proxy: {
      '/v1': { target: 'http://127.0.0.1:18765', ws: true },
      '/health': 'http://127.0.0.1:18765'
    }
  }
});
