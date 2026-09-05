import { createContext, useContext } from 'react'

// Shared state every dashboard card needs (currently just the selected
// date) without dashboard.jsx having to thread it through as a prop to
// each one by hand -- that manual wiring is exactly what broke
// WeightLogCard (it defaulted to today's date instead of receiving the
// dashboard's actual selected date, because nobody remembered to pass
// it as a prop). A new card calls useDashboardContext() and gets
// whatever's here automatically, with zero changes needed anywhere
// else. To add something new (e.g. the day's macro totals), add it to
// the value object passed to <DashboardProvider> in dashboard.jsx --
// every existing and future consumer picks it up without further
// wiring.
const DashboardContext = createContext(null)

export const DashboardProvider = DashboardContext.Provider

export function useDashboardContext() {
  const ctx = useContext(DashboardContext)
  if (!ctx) {
    throw new Error('useDashboardContext() was called outside <Dashboard> -- this hook only works for components rendered inside the dashboard page.')
  }
  return ctx
}
