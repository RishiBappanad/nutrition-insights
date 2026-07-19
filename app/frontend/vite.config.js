import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.VITE_BASE_PATH || '/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/nutrition/auth': { target: 'http://localhost:8000', rewrite: (path) => path.replace('/nutrition', '') },
      '/nutrition/sync': { target: 'http://localhost:8000', rewrite: (path) => path.replace('/nutrition', '') },
      '/nutrition/data': { target: 'http://localhost:8000', rewrite: (path) => path.replace('/nutrition', '') },
      '/nutrition/food': { target: 'http://localhost:8000', rewrite: (path) => path.replace('/nutrition', '') },
      '/auth': 'http://localhost:8000',
      '/sync': 'http://localhost:8000',
      '/data': 'http://localhost:8000',
      '/food': 'http://localhost:8000',
    },
  },
})
