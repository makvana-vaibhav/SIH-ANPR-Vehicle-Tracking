import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

// NagarNetra command centre build configuration.
//
// The production bundle is served by nginx (see Dockerfile). In development the
// dev server proxies /api and /ws to the API container so the browser talks to a
// single origin and no CORS configuration is needed locally.
export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      // This file is ESM (package.json sets "type": "module"), where
      // __dirname does not exist.
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // Bind mounts on macOS do not deliver filesystem events into the container.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://api:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://api:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    // Operations centres run on modest hardware; keep the bundle honest.
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Split the heavy, rarely-changing libraries (map, charts) out of the
        // main chunk so the dashboard paints fast on first load.
        manualChunks(id: string) {
          if (id.includes('node_modules')) {
            if (id.includes('maplibre')) return 'vendor-map'
            if (id.includes('recharts') || id.includes('d3-')) return 'vendor-charts'
            if (id.includes('react')) return 'vendor-react'
            return 'vendor'
          }
          return undefined
        },
      },
    },
  },
})
