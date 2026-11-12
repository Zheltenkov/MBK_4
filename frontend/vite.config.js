import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// В dev режиме фронт ходит на http://localhost:5173, а /api/ проксируется на бэк.
// В prod nginx внутри контейнера serves SPA и проксирует /api/ → backend сервис.
export default defineConfig({
  plugins: [react()],
  server: {
    // Локальный dev-сервер не должен быть доступен другим устройствам в сети.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // Важно для SSE: не буферим.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache';
          });
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
