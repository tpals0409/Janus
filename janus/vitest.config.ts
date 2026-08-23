import { resolve } from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': resolve('src/renderer/src') }
  },
  test: {
    environment: 'jsdom',
    include: ['src/renderer/src/**/*.test.{ts,tsx}'],
    setupFiles: [resolve('src/renderer/src/test/setup.ts')],
    environmentOptions: {
      jsdom: { url: 'http://localhost/' }
    },
    restoreMocks: true
  }
})
