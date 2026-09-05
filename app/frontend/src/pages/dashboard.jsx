import { useState, useEffect } from 'react'
import { Link, useSearchParams } from 'wouter'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { todayIso, addDays, friendlyDate, isToday } from '@/lib/dates'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { MacroCard } from '@/components/ui/macro-card'
import { MicronutrientCard } from '@/components/ui/micronutrient-card'
import { MealSections } from '@/components/ui/meal-sections'
import { WaterWidget } from '@/components/ui/water-widget'
import { EntryDetailPanel } from '@/components/ui/entry-detail-panel'
import { DashboardProvider, useDashboardContext } from '@/lib/dashboard-context'
import { RefreshCw, Flame, Activity, Calculator, UtensilsCrossed, BookOpen, Layers, ChevronLeft, ChevronRight, CalendarDays, Dumbbell, Scale, Check } from 'lucide-react'

export default function Dashboard() {
  const { colors, sufficiencyThresholdPct, unitSystem, macroChartStyle, importantNutrients, updateMacroChartStyle } = usePreferences()
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
  const [selectedEntry, setSelectedEntry] = useState(null)

  // Selected date lives in the URL (?date=YYYY-MM-DD), not local state --
  // per explicit user request, switching days must not reset whatever
  // page/view the user is on (this persists across reloads/back-forward
  // too, which plain useState wouldn't). Defaults to today when absent.
  const [searchParams, setSearchParams] = useSearchParams()
  const date = searchParams.get('date') || todayIso()

  function setDate(newDate) {
    const next = new URLSearchParams(searchParams)
    next.set('date', newDate)
    setSearchParams(next, { replace: true })
  }

  function loadNutritionData() {
    setNutritionLoading(true)
    return Promise.all([
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
  }

  function loadBmr() {
    return api('/data/bmr')
      .then((r) => r.json())
      .then((d) => {
        setBmr(d.bmr)
        setBmrMessage(d.bmr == null ? d.message : '')
      })
  }

  useEffect(() => {
    loadBmr().catch(() => {}).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadNutritionData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date])

  async function handleSync(target) {
    setSyncing(target)
    setSyncResult(null)
    const res = await api(`/sync/${target}`, { method: 'POST' })
    const data = await res.json()
    setSyncResult(data)
    setSyncing('')
    // Bug fix: a successful sync writes new food_log/tdee_log rows, but
    // nothing previously re-fetched the dashboard's data afterward — the
    // sync would genuinely succeed (confirmed against real Cloud Run
    // logs) while the UI kept showing pre-sync data until a manual
    // reload. Both refetches run regardless of date, since a Cronometer
    // sync can touch BMR (tdee_log) and/or the currently-viewed date's
    // diary.
    if (res.ok) {
      loadNutritionData()
      loadBmr()
    }
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
    <DashboardProvider value={{ date, setDate }}>
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Your daily nutrition overview
          </p>
        </div>

        {/* Calendar nav — the date lives in the URL (see above), so
            switching days here doesn't reset which page/view the user is
            on (e.g. staying on /report while paging back a few days). */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDate(addDays(date, -1))}
            className="p-2 rounded-md border border-border text-muted-foreground hover:bg-muted transition-colors"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border min-w-[120px] justify-center">
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
              className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors underline ml-1"
            >
              Today
            </button>
          )}
        </div>
      </div>

      {/* Prominent logging entry points — Log Food, Recipes, and Meals
          were pulled out of the persistent sidebar nav per user request
          (too much clutter for "ways to add something to today") and
          live here instead, next to the diary they feed. */}
      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
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
        <Link href="/exercise">
          <div className="flex items-center gap-2 justify-center px-4 py-3 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors cursor-pointer">
            <Dumbbell className="h-4 w-4" />
            Exercise
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
          <MicronutrientCard
            progress={progress}
            colors={colors}
            sufficiencyThresholdPct={sufficiencyThresholdPct}
            importantNutrients={importantNutrients}
            date={date}
          />
        </div>
      )}

      {/* Water tracking */}
      <WaterWidget date={date} unitSystem={unitSystem} />

      {/* Diary, grouped by meal/time-of-day */}
      <div>
        <h2 className="text-lg font-semibold mb-3">{isToday(date) ? "Today's" : friendlyDate(date)} Diary</h2>
        {nutritionLoading ? (
          <Skeleton className="h-40" />
        ) : (
          <MealSections entries={foodLog?.entries} onSelectEntry={setSelectedEntry} />
        )}
      </div>

      {selectedEntry && (
        <EntryDetailPanel
          entry={selectedEntry}
          onClose={() => setSelectedEntry(null)}
          onChanged={loadNutritionData}
        />
      )}

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

      <WeightLogCard />

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
    </DashboardProvider>
  )
}

function WeightLogCard() {
  const { date } = useDashboardContext()
  const [weight, setWeight] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  async function handleLog(e) {
    e.preventDefault()
    const value = Number(weight)
    if (!value || value <= 0) {
      setError('Enter a valid weight')
      return
    }
    setSaving(true)
    setError('')
    const res = await api('/data/weight', {
      method: 'POST',
      body: JSON.stringify({ date, weight_lbs: value }),
    })
    setSaving(false)
    if (res.ok) {
      setWeight('')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Weight</CardTitle>
        <Scale className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground mb-3">
          Log weight for {isToday(date) ? 'today' : friendlyDate(date)} — feeds both the Charts page and BMR/TDEE
        </p>
        <form onSubmit={handleLog} className="flex items-center gap-2">
          <input
            type="number"
            step="0.1"
            min="0"
            placeholder="lbs"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            className="w-24 px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {saved ? <Check className="h-4 w-4" /> : null}
            {saving ? 'Logging...' : saved ? 'Logged' : 'Log'}
          </button>
          {error && <span className="text-sm text-destructive">{error}</span>}
        </form>
      </CardContent>
    </Card>
  )
}
