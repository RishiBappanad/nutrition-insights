import { useEffect, useState } from 'react'
import { useTrackStackAuth, redirectToLogin } from 'trackstack-ui'
import { Switch, Route, Router } from 'wouter'
import { Layout } from '@/components/layout'
import { PendingActionProvider } from '@/lib/pending-action'
import Dashboard from '@/pages/dashboard'
import Charts from '@/pages/charts'
import Log from '@/pages/log'
import FoodLog from '@/pages/food-log'
import LiftInsights from '@/pages/lift-insights'
import Profile from '@/pages/profile'
import Targets from '@/pages/targets'
import Goals from '@/pages/goals'
import Pantry from '@/pages/pantry'
import Recipes from '@/pages/recipes'
import Meals from '@/pages/meals'
import Report from '@/pages/report'
import Exercise from '@/pages/exercise'
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
const AUTH_BASE_URL = import.meta.env.VITE_TRACKSTACK_AUTH_URL ?? ''

function AppRoutes({ onLogout }) {
  return (
    <PendingActionProvider>
      <Layout onLogout={onLogout}>
        <Switch>
          <Route path="/" component={Dashboard} />
          <Route path="/food-log" component={FoodLog} />
          <Route path="/charts" component={Charts} />
          <Route path="/log" component={Log} />
          <Route path="/lift-insights" component={LiftInsights} />
          <Route path="/profile" component={Profile} />
          <Route path="/targets" component={Targets} />
          <Route path="/goals" component={Goals} />
          <Route path="/pantry" component={Pantry} />
          <Route path="/recipes" component={Recipes} />
          <Route path="/meals" component={Meals} />
          <Route path="/report" component={Report} />
          <Route path="/exercise" component={Exercise} />
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
    </PendingActionProvider>
  )
}

export default function App() {
  // One shared useTrackStackAuth instance for the whole app -- Login
  // needs auth.login/register/loginWithGoogle, AppRoutes needs
  // auth.logout, and both need to see the SAME auth.isAuthenticated
  // flip when either happens. A separate useTrackStackAuth() call inside
  // Login itself would have its own independent token state that
  // wouldn't reactively update this component (the hook's cross-tab
  // `storage` listener only fires for OTHER tabs' writes, never this
  // tab's own), so the hook is called once here and passed down instead.
  const auth = useTrackStackAuth({ tokenKey: 'token', authBaseUrl: AUTH_BASE_URL })

  // Single sign-on: before bouncing to Home's login, silently check
  // whether trackstack-auth's own session cookie already authenticates
  // this browser (e.g. the user logged into a DIFFERENT TrackStack app
  // earlier). Skipped entirely if a local token already exists -- no
  // need to round-trip when auth.isAuthenticated is already true.
  const [ssoChecked, setSsoChecked] = useState(false)
  useEffect(() => {
    if (auth.isAuthenticated) {
      setSsoChecked(true)
      return
    }
    auth.trySilentSSO().finally(() => setSsoChecked(true))
    // Intentionally mount-only -- trySilentSSO only makes sense once,
    // before the first render decides which UI to show.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // There is no local login page anymore -- the only login UI in the
  // whole system is TrackStack Home's (per-tracker login pages were
  // scrapped 2026-09-20). Once silent SSO has been tried and there's
  // still no user, bounce to Home with returnTo set to this exact URL,
  // so a successful login there sends the browser right back here.
  useEffect(() => {
    if (ssoChecked && !auth.isAuthenticated) {
      redirectToLogin(AUTH_BASE_URL, window.location.href)
    }
  }, [ssoChecked, auth.isAuthenticated])

  if (!ssoChecked || !auth.isAuthenticated) {
    return null
  }

  // No `returnTo` on logout, deliberately -- an explicit logout means the
  // user chose to leave, so they should land on Home itself to decide
  // where to go next, not get immediately bounced right back here.
  const logout = () => {
    auth.logout()
    redirectToLogin(AUTH_BASE_URL)
  }

  return (
    <Router base={BASE}>
      <AppRoutes onLogout={logout} />
    </Router>
  )
}
