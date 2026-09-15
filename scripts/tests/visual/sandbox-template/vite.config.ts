import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  css: { preprocessorOptions: { less: { javascriptEnabled: true } } },
  server: { port: 5273 },
  preview: { port: 5273 },
})
