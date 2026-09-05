import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Every prefix registered via app.include_router(..., prefix=...) in
// app/backend/app/__init__.py -- kept as one list instead of hand-writing
// two proxy entries per router, since the old hand-maintained version
// silently fell 12 routers behind (only auth/sync/data/food were proxied)
// and any un-proxied path falls through to Vite's own SPA fallback,
// serving index.html for what looks like a JSON API call -- the
// "Unexpected token '<', <!DOCTYPE" errors this caused in local dev.
const API_PREFIXES = [
  'auth', 'sync', 'data', 'food', 'targets', 'water', 'notes', 'pantry',
  'profile', 'custom-foods', 'recipes', 'meals', 'label-scanner',
  'preferences', 'exercise', 'events', 'aggregations', 'lifts',
]

const proxy = {}
for (const prefix of API_PREFIXES) {
  proxy[`/${prefix}`] = 'http://localhost:8000'
  proxy[`/nutrition/${prefix}`] = {
    target: 'http://localhost:8000',
    rewrite: (path) => path.replace('/nutrition', ''),
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.VITE_BASE_PATH || '/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy,
  },
})
