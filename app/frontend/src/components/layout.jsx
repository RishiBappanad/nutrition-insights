import { useState } from 'react'
import { Link, useLocation } from 'wouter'
import {
  LayoutDashboard,
  BarChart3,
  ClipboardList,
  Dumbbell,
  Settings,
  LogOut,
  Menu,
  X,
  Apple,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { AppSwitcher, MobileAppSwitcher } from '@/components/app-switcher'

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/charts', label: 'Charts', icon: BarChart3 },
  { href: '/log', label: 'Log', icon: ClipboardList },
  { href: '/lift-insights', label: 'Lifts', icon: Dumbbell },
  { href: '/settings', label: 'Settings', icon: Settings },
]

export function Layout({ children, onLogout }) {
  const [location] = useLocation()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  return (
    <div className="flex min-h-screen w-full bg-background">
      {/* Desktop: App Switcher + Sidebar */}
      <div className="hidden md:flex">
        <AppSwitcher />
        <aside className="w-56 flex-shrink-0 bg-sidebar border-r border-sidebar-border flex flex-col">
          <div className="h-14 flex items-center px-5 border-b border-sidebar-border">
            <div className="flex items-center gap-2 text-sidebar-foreground">
              <Apple className="h-5 w-5 text-sidebar-primary" />
              <span className="font-semibold text-base tracking-tight">Nutrition</span>
            </div>
          </div>

          <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
            {navItems.map((item) => {
              const isActive =
                location === item.href ||
                (item.href !== '/' && location.startsWith(item.href))
              return (
                <Link key={item.href} href={item.href}>
                  <div
                    className={cn(
                      'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200 cursor-pointer',
                      isActive
                        ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm'
                        : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                    )}
                  >
                    <item.icon
                      className={cn(
                        'h-4 w-4',
                        isActive ? 'text-sidebar-primary' : 'opacity-70'
                      )}
                    />
                    {item.label}
                  </div>
                </Link>
              )
            })}
          </nav>

          <div className="p-3 border-t border-sidebar-border">
            <button
              onClick={onLogout}
              className="flex w-full items-center gap-3 px-3 py-2 text-sm font-medium text-sidebar-foreground/70 hover:text-sidebar-foreground cursor-pointer transition-colors rounded-md hover:bg-sidebar-accent/50"
            >
              <LogOut className="h-4 w-4 opacity-70" />
              Logout
            </button>
          </div>
        </aside>
      </div>

      {/* Main content wrapper */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile: App switcher bar + header */}
        <MobileAppSwitcher />
        <header className="h-12 md:hidden flex items-center justify-between px-4 border-b border-border bg-card">
          <div className="flex items-center gap-2">
            <Apple className="h-4 w-4 text-primary" />
            <span className="font-semibold text-sm">Nutrition</span>
          </div>
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="p-2 rounded-md hover:bg-muted transition-colors"
          >
            {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </header>

        {/* Mobile slide-down menu */}
        {mobileMenuOpen && (
          <div className="md:hidden bg-card border-b border-border px-4 py-2 space-y-0.5">
            {navItems.map((item) => {
              const isActive =
                location === item.href ||
                (item.href !== '/' && location.startsWith(item.href))
              return (
                <Link key={item.href} href={item.href}>
                  <div
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all cursor-pointer',
                      isActive
                        ? 'bg-accent text-accent-foreground'
                        : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
                    )}
                  >
                    <item.icon className="h-4 w-4" />
                    {item.label}
                  </div>
                </Link>
              )
            })}
            <button
              onClick={() => { onLogout(); setMobileMenuOpen(false); }}
              className="flex w-full items-center gap-3 px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground cursor-pointer transition-colors rounded-md hover:bg-accent/50"
            >
              <LogOut className="h-4 w-4" />
              Logout
            </button>
          </div>
        )}

        {/* Main Content */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <div className="max-w-5xl mx-auto">{children}</div>
        </main>

        {/* Mobile Bottom Nav */}
        <nav className="md:hidden flex items-center justify-around border-t border-border bg-card py-1.5 safe-bottom">
          {navItems.slice(0, 4).map((item) => {
            const isActive =
              location === item.href ||
              (item.href !== '/' && location.startsWith(item.href))
            return (
              <Link key={item.href} href={item.href}>
                <div
                  className={cn(
                    'flex flex-col items-center gap-0.5 px-3 py-1 rounded-md transition-colors cursor-pointer',
                    isActive ? 'text-primary' : 'text-muted-foreground'
                  )}
                >
                  <item.icon className="h-5 w-5" />
                  <span className="text-[10px] font-medium">{item.label}</span>
                </div>
              </Link>
            )
          })}
        </nav>
      </div>
    </div>
  )
}
