import { redirectToLogin } from 'trackstack-ui'

// API base URL — controlled by VITE_API_BASE env var at build time.
// Standalone domain: leave unset (defaults to "")
// Behind proxy: set VITE_API_BASE=/nutrition
const API = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? '' : '')
const AUTH_BASE_URL = import.meta.env.VITE_TRACKSTACK_AUTH_URL ?? ''

export async function api(path, opts = {}) {
  const token = localStorage.getItem('token')
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
      ...opts.headers,
    },
  })

  // App.jsx's SSO check only runs once, on mount -- a token that goes
  // stale mid-session (expires, gets revoked) is never caught by that,
  // so every individual call silently 401'd with no indication the user
  // needed to log back in. This is the one choke point every API call
  // already goes through, so catching it here fixes it for the whole
  // app at once rather than needing every call site's own error handling
  // to know about auth. Only acts on a token that was actually sent --
  // an already-logged-out call legitimately 401s and shouldn't redirect.
  if (res.status === 401 && token) {
    localStorage.removeItem('token')
    redirectToLogin(AUTH_BASE_URL, window.location.href)
  }

  return res
}
