import { useState, useEffect } from 'react'
import { Link } from 'wouter'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { MacroCard } from '@/components/ui/macro-card'
import { MicronutrientCard } from '@/components/ui/micronutrient-card'
import { MealSections } from '@/components/ui/meal-sections'
import { WaterWidget } from '@/components/ui/water-widget'
import { RefreshCw, Flame, Activity, Calculator, UtensilsCrossed, BookOpen, Layers } from 'lucide-react'

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

export default function Dashboard() {
  const { colors, sufficiencyThresholdPct, unitSystem, macroChartStyle, updateMacroChartStyle } = usePreferences()
  const [bmr, setBmr] = useState(null)
  const [bmrMessage, setBmrMessage] = useState('')
  const [syncing, setSyncing] = useState('')
  const [syncResult, setSyncResult] = useState(null)
  const [calculatingBmr, setCalculatingBmr] = useState(false)
  const [loading, setLoading] = useState(true)

  const [foodLog, setFoodLog] = useState(null)
  const [macroTarget, setMacroTarget] = useState(null)
  const [progress, setProgress] = useState(null)
  const [nutritionLoading, setNutritionLoading] = useState(true)

  const date = todayIso()

  useEffect(() => {
    api('/data/bmr')
      .then((r) => r.json())
      .then((d) => {
        setBmr(d.bmr)
        setBmrMessage(d.bmr == null ? d.message : '')
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    setNutritionLoading(true)
    Promise.all([
      api(`/food/log?date=${date}`).then((r) => (r.ok ? r.json() : null)),
      // Macro targets are optional (404 if the user has never set them,
      // e.g. before completing profile setup) — treat that as "no target"
      // rather than an error the dashboard needs to surface.
      api('/targets/macros').then((r) => (r.ok ? r.json() : null)),
      api(`/targets/progress?date=${date}`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([log, macros, prog]) => {
        setFoodLog(log)
        setMacroTarget(macros)
        setProgress(prog?.progress ?? null)
      })
      .finally(() => setNutritionLoading(false))
  }, [date])

  async function handleSync(target) {
    setSyncing(target)
    setSyncResult(null)
    const res = await api(`/sync/${target}`, { method: 'POST' })
    const data = await res.json()
    setSyncResult(data)
    setSyncing('')
  }

  async function handleCalculateBmr() {
    setCalculatingBmr(true)
    setBmrMessage('')
    const res = await api('/sync/bmr', { method: 'POST' })
    const data = await res.json()
    setCalculatingBmr(false)
    if (res.ok) {
      setBmr(data.bmr)
      setBmrMessage(data.bmr == null ? data.message : '')
    } else {
      setBmrMessage(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Your daily nutrition overview
        </p>
      </div>

      {/* Prominent logging entry points — Log Food, Recipes, and Meals
          were pulled out of the persistent sidebar nav per user request
          (too much clutter for "ways to add something to today") and
          live here instead, next to the diary they feed. */}
      <div className="grid gap-3 grid-cols-3">
        <Link href="/food-log">
          <div className="flex items-center gap-2 justify-center px-4 py-3 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors cursor-pointer">
            <UtensilsCrossed className="h-4 w-4" />
            Log Food
          </div>
        </Link>
        <Link href="/recipes">
          <div className="flex items-center gap-2 justify-center px-4 py-3 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors cursor-pointer">
            <BookOpen className="h-4 w-4" />
            Recipes
          </div>
        </Link>
        <Link href="/meals">
          <div className="flex items-center gap-2 justify-center px-4 py-3 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors cursor-pointer">
            <Layers className="h-4 w-4" />
            Meals
          </div>
        </Link>
      </div>

      {/* Macro + Micronutrient summary */}
      {nutritionLoading ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <MacroCard
            totals={foodLog?.totals}
            target={macroTarget}
            nutrientTotals={foodLog?.nutrient_totals}
            colors={colors}
            chartStyle={macroChartStyle}
            onChartStyleChange={updateMacroChartStyle}
          />
          <MicronutrientCard progress={progress} colors={colors} sufficiencyThresholdPct={sufficiencyThresholdPct} />
        </div>
      )}

      {/* Water tracking */}
      <WaterWidget date={date} unitSystem={unitSystem} />

      {/* Diary, grouped by meal/time-of-day */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Today's Diary</h2>
        {nutritionLoading ? (
          <Skeleton className="h-40" />
        ) : (
          <MealSections entries={foodLog?.entries} />
        )}
      </div>

      {/* BMR Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            Basal Metabolic Rate
          </CardTitle>
          <Flame className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-8 w-32" />
          ) : (
            <div className="text-3xl font-bold font-mono">
              {bmr ? `${bmr} kcal` : '—'}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-1">
            {bmrMessage || (bmr ? 'Calculated from your synced data' : 'Recalculate after syncing data')}
          </p>
          <button
            onClick={handleCalculateBmr}
            disabled={calculatingBmr}
            className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
          >
            <Calculator className={`h-4 w-4 ${calculatingBmr ? 'animate-pulse' : ''}`} />
            {calculatingBmr ? 'Calculating...' : 'Recalculate BMR'}
          </button>
        </CardContent>
      </Card>

      {/* Sync Controls */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Cronometer</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground mb-3">
              Pull nutrition &amp; biometric data from Cronometer (does not affect your BMR —
              use "Recalculate BMR" above for that)
            </p>
            <button
              onClick={() => handleSync('cronometer')}
              disabled={!!syncing}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <RefreshCw
                className={`h-4 w-4 ${syncing === 'cronometer' ? 'animate-spin' : ''}`}
              />
              {syncing === 'cronometer' ? 'Syncing...' : 'Sync'}
            </button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Hevy</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground mb-3">
              Sync workout &amp; lift data
            </p>
            <button
              onClick={() => handleSync('hevy')}
              disabled={!!syncing}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <RefreshCw
                className={`h-4 w-4 ${syncing === 'hevy' ? 'animate-spin' : ''}`}
              />
              {syncing === 'hevy' ? 'Syncing...' : 'Sync'}
            </button>
          </CardContent>
        </Card>
      </div>

      {/* Sync Result */}
      {syncResult && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Sync Result</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs font-mono bg-muted p-3 rounded-md overflow-x-auto">
              {JSON.stringify(syncResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
