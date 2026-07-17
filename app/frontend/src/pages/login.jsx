import { useState } from 'react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card'
import { Apple } from 'lucide-react'

// Identity (registration, login, Google OAuth) is owned by trackstack-auth,
// not this app's own backend — this page talks to trackstack-auth directly
// instead of a local /auth/register or /auth/login endpoint, so the same
// account works across every TrackStack app.
const TRACKSTACK_AUTH_URL = import.meta.env.VITE_TRACKSTACK_AUTH_URL ?? ''

function GoogleIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  )
}

export default function Login({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isRegister, setIsRegister] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    const endpoint = isRegister ? '/register' : '/login'
    try {
      const res = await fetch(`${TRACKSTACK_AUTH_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (data.token) {
        localStorage.setItem('token', data.token)
        onLogin()
      } else {
        setError(data.error || 'Failed')
      }
    } catch {
      setError('Network error')
    }
    setLoading(false)
  }

  async function handleGoogleLogin() {
    try {
      const returnTo = window.location.origin
      const res = await fetch(`${TRACKSTACK_AUTH_URL}/google?returnTo=${encodeURIComponent(returnTo)}`)
      const data = await res.json()
      if (data.url) window.location.href = data.url
    } catch {
      setError('Google login unavailable')
    }
  }

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 w-10 h-10 rounded-lg bg-primary flex items-center justify-center">
            <Apple className="h-5 w-5 text-primary-foreground" />
          </div>
          <CardTitle className="text-xl">
            {isRegister ? 'Create Account' : 'Welcome Back'}
          </CardTitle>
          <CardDescription>
            {isRegister
              ? 'Sign up for TrackStack — used by Nutrition Insights and every other TrackStack app'
              : 'Sign in with your TrackStack account'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Google Sign In */}
          <button
            type="button"
            onClick={handleGoogleLogin}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-md border border-border bg-card text-sm font-medium hover:bg-muted transition-colors"
          >
            <GoogleIcon />
            Continue with Google
          </button>

          <div className="relative flex items-center gap-2">
            <div className="flex-1 h-px bg-border" />
            <span className="text-xs text-muted-foreground">or</span>
            <div className="flex-1 h-px bg-border" />
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Email
              </label>
              <input
                type="email"
                placeholder="Enter email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                required
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Password
              </label>
              <input
                type="password"
                placeholder="Enter password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                required
              />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {loading
                ? 'Please wait...'
                : isRegister
                  ? 'Create Account'
                  : 'Sign In'}
            </button>
          </form>
        </CardContent>
        <CardFooter className="justify-center">
          <button
            onClick={() => {
              setIsRegister(!isRegister)
              setError('')
            }}
            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            {isRegister
              ? 'Already have an account? Sign in'
              : "Don't have an account? Sign up"}
          </button>
        </CardFooter>
      </Card>
    </div>
  )
}
