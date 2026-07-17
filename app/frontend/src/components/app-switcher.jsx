import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Wallet, Apple, LayoutDashboard } from 'lucide-react'

// Icon map — add new icons here as you add apps
const ICON_MAP = {
  Wallet,
  Apple,
  LayoutDashboard,
}

const CURRENT_APP = 'nutrition'

// The app registry lives on trackstack-auth (every app already depends on
// it for identity), not a relative /apps.json — each TrackStack app is now
// deployed standalone on its own origin (Cloud Run), so a same-origin
// static file can't be the source of truth for cross-origin links anymore.
const TRACKSTACK_AUTH_URL = import.meta.env.VITE_TRACKSTACK_AUTH_URL ?? ''

function useAppRegistry() {
  const [apps, setApps] = useState([])

  useEffect(() => {
    if (!TRACKSTACK_AUTH_URL) return
    fetch(`${TRACKSTACK_AUTH_URL}/apps`)
      .then(r => r.json())
      .then(setApps)
      .catch(() => {})
  }, [])

  return apps
}

export function AppSwitcher() {
  const apps = useAppRegistry()

  if (apps.length === 0) return null

  return (
    <aside className="w-16 flex-shrink-0 bg-switcher border-r border-switcher-border hidden md:flex flex-col items-center py-4 gap-3">
      {apps.map((app) => {
        const Icon = ICON_MAP[app.icon] || LayoutDashboard
        const isActive = app.id === CURRENT_APP
        return (
          <a
            key={app.id}
            href={app.href}
            title={app.label}
            className={cn(
              'w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-200',
              isActive
                ? 'bg-switcher-active text-primary-foreground ring-2 ring-sidebar-ring'
                : 'text-switcher-foreground hover:bg-switcher-active/50 hover:text-sidebar-foreground'
            )}
          >
            <Icon className="h-5 w-5" />
          </a>
        )
      })}
    </aside>
  )
}

export function MobileAppSwitcher() {
  const apps = useAppRegistry()

  if (apps.length === 0) return null

  return (
    <div className="flex md:hidden items-center gap-2 px-2 py-1.5 border-b border-border bg-switcher">
      {apps.map((app) => {
        const Icon = ICON_MAP[app.icon] || LayoutDashboard
        const isActive = app.id === CURRENT_APP
        return (
          <a
            key={app.id}
            href={app.href}
            title={app.label}
            className={cn(
              'w-8 h-8 rounded-md flex items-center justify-center transition-all duration-200',
              isActive
                ? 'bg-switcher-active text-primary-foreground ring-1 ring-sidebar-ring'
                : 'text-switcher-foreground hover:bg-switcher-active/50'
            )}
          >
            <Icon className="h-4 w-4" />
          </a>
        )
      })}
    </div>
  )
}
