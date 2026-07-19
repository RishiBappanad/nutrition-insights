import { useState } from 'react'
import { Switch, Route, Router } from 'wouter'
import { Layout } from '@/components/layout'
import { isAuthenticated, logout } from '@/lib/api'
import Login from '@/pages/login'
import Dashboard from '@/pages/dashboard'
import Charts from '@/pages/charts'
import Log from '@/pages/log'
import LiftInsights from '@/pages/lift-insights'
import Settings from '@/pages/settings'

// Extract the trackstack-auth token from the URL hash BEFORE AuthProvider
// reads localStorage. This runs synchronously at module load time.
// trackstack-auth's /google/callback redirects with #trackstack_token=...
// (see trackstack-auth/src/routes.ts).
(function extractGoogleToken() {
  const hash = window.location.hash;
  const match = hash.match(/trackstack_token=([^&]+)/);
  if (match) {
    localStorage.setItem("token", match[1]);
    window.location.hash = "";
  }
})();

const BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

function AppRoutes({ onLogout }) {
  return (
    <Layout onLogout={onLogout}>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/charts" component={Charts} />
        <Route path="/log" component={Log} />
        <Route path="/lift-insights" component={LiftInsights} />
        <Route path="/settings" component={Settings} />
        <Route>
          <div className="text-center py-12">
            <h2 className="text-xl font-semibold">Page Not Found</h2>
            <p className="text-muted-foreground mt-2">
              The page you're looking for doesn't exist.
            </p>
          </div>
        </Route>
      </Switch>
    </Layout>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(isAuthenticated())

  if (!authed) {
    return <Login onLogin={() => setAuthed(true)} />
  }

  return (
    <Router base={BASE}>
      <AppRoutes
        onLogout={() => {
          logout()
          setAuthed(false)
        }}
      />
    </Router>
  )
}
