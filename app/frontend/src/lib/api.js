// API base URL — controlled by VITE_API_BASE env var at build time.
// Standalone domain: leave unset (defaults to "")
// Behind proxy: set VITE_API_BASE=/nutrition
const API = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? '' : '')

export function api(path, opts = {}) {
  const token = localStorage.getItem('token')
  return fetch(`${API}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
      ...opts.headers,
    },
  })
}

export function logout() {
  localStorage.removeItem('token')
}

export function isAuthenticated() {
  return !!localStorage.getItem('token')
}
