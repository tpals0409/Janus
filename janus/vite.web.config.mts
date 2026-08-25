// Temporary web-only renderer config for design review (no Electron). Safe to delete.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const RENDERER = '/Users/kimsemin/Desktop/로컬 LLM 실습/janus/src/renderer'

export default defineConfig({
  root: RENDERER,
  resolve: { alias: { '@': RENDERER + '/src' } },
  plugins: [react(), tailwindcss()],
})
