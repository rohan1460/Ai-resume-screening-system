import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The app always calls /api/*. In dev Vite proxies that to the local API; in the
// container nginx does the same. Either way the browser sees one origin, so there is
// no CORS to configure and no API URL baked into the bundle.
export default defineConfig({
  plugins: [react()],
  build: {
    // three.js is deliberately one large async chunk: it is dynamically imported
    // after first paint and never blocks the initial bundle, so the default warning
    // is noise rather than a signal here.
    chunkSizeWarningLimit: 800,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
