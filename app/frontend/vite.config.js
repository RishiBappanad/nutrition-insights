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

// Defaults to the native-dev case (this process running on the host,
// backend also on the host at localhost:8000). When this dev server
// itself runs inside a container instead (workspace-notes/docker-compose.yml's
// nutrition-frontend service), `localhost` would resolve to that
// container, not its nutrition-backend sibling -- VITE_PROXY_TARGET
// overrides it to the Docker service name in that case.
const backendTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

const proxy = {}
for (const prefix of API_PREFIXES) {
  proxy[`/${prefix}`] = backendTarget
  proxy[`/nutrition/${prefix}`] = {
    target: backendTarget,
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
    // Binds all interfaces, not just loopback -- required for the
    // container case (Docker's published port forwards to the
    // container's network interface, not its loopback), and harmless
    // for native dev.
    host: true,
  },
})
