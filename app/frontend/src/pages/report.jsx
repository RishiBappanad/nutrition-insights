import { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'wouter'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { todayIso, addDays, friendlyDate, isToday } from '@/lib/dates'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { NutrientRow } from '@/components/ui/micronutrient-card'
import { ChevronLeft, ChevronRight, CalendarDays, ArrowLeft } from 'lucide-react'

/**
 * Full, unfiltered micronutrient report — every tracked nutrient with a
 * target or a logged amount, all at once, no grouping/filtering. The
 * explicit "show me everything" counterpart to the dashboard's
 * swipeable, curated MicronutrientCard (per user request: curated cards
 * for the everyday view, but "have an accessible view like a total
 * report that would have everything").
 *
 * Shares the same ?date= URL param as the dashboard (see dashboard.jsx)
 * so navigating here mid-date-browse keeps looking at the same day, and
 * the date nav here writes back to the same param — switching dates from
 * either place keeps you on whichever view you're currently on, per the
 * explicit "remember state" requirement.
 */
export default function Report() {
  const { colors, sufficiencyThresholdPct } = usePreferences()
  const [searchParams, setSearchParams] = useSearchParams()
  const date = searchParams.get('date') || todayIso()
  const [progress, setProgress] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api(`/targets/progress?date=${date}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setProgress(d?.progress ?? null))
      .finally(() => setLoading(false))
  }, [date])

  function setDate(newDate) {
    const next = new URLSearchParams(searchParams)
    next.set('date', newDate)
    setSearchParams(next, { replace: true })
  }

  const entries = Object.entries(progress || {})
    .filter(([, v]) => v.daily_target != null || v.actual > 0)
    .sort(([a], [b]) => a.localeCompare(b))

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/?date=${date}`}>
          <div className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer mb-2">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to Dashboard
          </div>
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Micronutrient Report</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Every tracked micronutrient for the selected day
        </p>
      </div>

      <div className="flex items-center justify-center gap-3">
        <button
          onClick={() => setDate(addDays(date, -1))}
          className="p-2 rounded-md border border-border text-muted-foreground hover:bg-muted transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border min-w-[140px] justify-center">
          <CalendarDays className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-sm font-medium">{friendlyDate(date)}</span>
        </div>
        <button
          onClick={() => setDate(addDays(date, 1))}
          disabled={isToday(date)}
          className="p-2 rounded-md border border-border text-muted-foreground hover:bg-muted disabled:opacity-30 transition-colors"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        {!isToday(date) && (
          <button
            onClick={() => setDate(todayIso())}
            className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors underline"
          >
            Jump to today
          </button>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">All Micronutrients</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No micronutrient targets set yet. Set up your profile to seed targets from DRI recommendations.
            </p>
          ) : (
            entries.map(([name, entry]) => (
              <NutrientRow
                key={name}
                name={name}
                entry={entry}
                colors={colors}
                sufficiencyThresholdPct={sufficiencyThresholdPct}
              />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}
