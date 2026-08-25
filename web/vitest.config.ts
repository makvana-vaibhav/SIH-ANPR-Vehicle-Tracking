/**
 * Test configuration, kept separate from vite.config.ts.
 *
 * vitest ships its own bundled copy of vite, so importing `defineConfig` from
 * 'vitest/config' inside vite.config.ts makes tsc compare two structurally
 * identical but nominally different Vite type trees and fail. Splitting the
 * configs avoids that entirely, and this file is deliberately outside the
 * tsconfig `include` list so it is never type-checked against the app's tree.
 */

import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: false,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
