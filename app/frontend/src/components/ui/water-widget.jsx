import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { mlToFlOz, mlToCups } from '@/lib/units'
import { GlassWater } from 'lucide-react'

// One "glass" = 8 fl oz (~237 mL), matching the standard "8 glasses a
// day" convention Cronometer's own widget uses (support.cronometer.com:
// tap a glass to add one, keep tapping past the goal to log more).
const GLASS_SIZE_ML = 236.588

/**
 * Water tracking widget: a row of glass icons filled up to today's
 * total, tap an empty glass (or the trailing "+") to log one glass via
 * POST /water/log. Exceeding the goal is allowed — more glasses simply
 * appear, matching Cronometer's own behavior ("you can also add water
 * above and beyond your goal").
 */
export function WaterWidget({ date, unitSystem }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [logging, setLogging] = useState(false)

  function refresh() {
    setLoading(true)
    api(`/water/log?date=${date}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [date])

  async function addGlass() {
    setLogging(true)
    await api('/water/log', {
      method: 'POST',
      body: JSON.stringify({ date, amount_ml: GLASS_SIZE_ML }),
    })
    await refresh()
    setLogging(false)
  }

  async function removeLastGlass() {
    if (!data?.entries?.length) return
    const last = data.entries[data.entries.length - 1]
    setLogging(true)
    await api(`/water/log/${last.id}`, { method: 'DELETE' })
    await refresh()
    setLogging(false)
  }

  if (loading || !data) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium">Water</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Loading...</p></CardContent>
      </Card>
    )
  }

  const glassCount = Math.round(data.total_ml / GLASS_SIZE_ML)
  const goalGlasses = data.target_ml ? Math.round(data.target_ml / GLASS_SIZE_ML) : null
  // Always show at least the goal's worth of glass slots (or a
  // reasonable default of 8 if no goal is set yet), plus any extra
  // glasses already logged past the goal — matches Cronometer's "keep
  // tapping and more cups appear" behavior rather than capping the row.
  const slotsToShow = Math.max(goalGlasses ?? 8, glassCount)

  const displayTotal = unitSystem === 'metric'
    ? `${Math.round(data.total_ml)} mL`
    : `${mlToCups(data.total_ml).toFixed(1)} cups`
  const displayGoal = data.target_ml
    ? unitSystem === 'metric'
      ? `${Math.round(data.target_ml)} mL`
      : `${mlToCups(data.target_ml).toFixed(1)} cups`
    : null

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Water</CardTitle>
        <GlassWater className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline justify-between mb-3">
          <span className="text-2xl font-bold font-mono">{displayTotal}</span>
          {displayGoal && <span className="text-xs text-muted-foreground">of {displayGoal} goal</span>}
        </div>

        <div className="flex flex-wrap gap-1.5">
          {Array.from({ length: slotsToShow }).map((_, i) => {
            const filled = i < glassCount
            return (
              <button
                key={i}
                onClick={filled && i === glassCount - 1 ? removeLastGlass : addGlass}
                disabled={logging}
                title={filled ? 'Click to remove last glass' : 'Click to add a glass'}
                className={cn(
                  'h-8 w-8 rounded-md border flex items-center justify-center transition-colors disabled:opacity-50',
                  filled ? 'bg-primary/20 border-primary text-primary' : 'border-border text-muted-foreground hover:bg-muted'
                )}
              >
                <GlassWater className="h-4 w-4" />
              </button>
            )
          })}
          <button
            onClick={addGlass}
            disabled={logging}
            title="Add a glass"
            className="h-8 w-8 rounded-md border border-dashed border-border text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50 flex items-center justify-center text-lg font-medium"
          >
            +
          </button>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          {glassCount} glass{glassCount === 1 ? '' : 'es'} ({Math.round(GLASS_SIZE_ML)}mL each)
        </p>
      </CardContent>
    </Card>
  )
}
