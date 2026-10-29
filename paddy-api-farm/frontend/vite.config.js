// Normal route when npm can reach its registry: `npm install && npm run dev` (the dev server proxies /api to the
// Python backend on port 8000) or `npm run build` (writes dist/, which `python -m paddy serve` serves at /).
// Offline route used to build this copy: `node build.mjs` (esbuild only).
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } } },
  build: { outDir: 'dist', emptyOutDir: true },
});
