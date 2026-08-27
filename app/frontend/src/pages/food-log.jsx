import { useState, useEffect, useRef } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { todayIso } from '@/lib/dates'
import { Search, Plus, CheckCircle, Info } from 'lucide-react'

const MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack']
const SEARCH_DEBOUNCE_MS = 400

// USDA FoodData Central reports macros as regular nutrient entries, not
// separate fields — "Energy" is the exact nutrient name USDA uses
// (confirmed against a live API response), mapped to the sole hardcoded
// food_log macro column (calories — TrackStack's "amount" for this
// tracker). "Energy" appears twice per food (kJ and kcal) — must match
// on unit, not just name, or this silently grabs the wrong one and
// multiplies calories by ~4. Protein/carbs/fat/fiber are deliberately
// NOT here — none of them are macro columns; they're sent/read entirely
// via the `nutrients` map, never extracted as separate fields.
const MACRO_NUTRIENT_NAMES = {
  calories: { name: 'Energy', unit: 'KCAL' },
}

function extractMacro(nutrients, key) {
  const spec = MACRO_NUTRIENT_NAMES[key]
  for (const [name, info] of Object.entries(nutrients || {})) {
    if (name === spec.name && (!spec.unit || info.unit === spec.unit)) {
      return Number(info.value) || 0
    }
  }
  return 0
}

/**
 * Food search + log page. Live/autocomplete search: as the user types,
 * a debounced request hits GET /food/search (USDA + CNF) automatically
 * — no submit button required to see results, matching what "search
 * autocomplete" actually means (the earlier explicit-submit-only version
 * required pressing Enter/clicking Search, which isn't autocomplete).
 * Picking a result opens an inline logger where the user chooses a meal,
 * date, and a gram amount to scale to — POST /food/log with `scale_to`
 * does the actual scaling server-side (portion_scaling.py), this page
 * never computes scaled nutrients itself.
 */
export default function FoodLog() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [selected, setSelected] = useState(null)
  const [previewing, setPreviewing] = useState(null)
  const [logging, setLogging] = useState(false)
  const [status, setStatus] = useState('')

  // Guards against a slow earlier request's results overwriting a
  // newer one's — without this, typing "chi" then quickly "chicken"
  // could show "chi"'s results last if that request happens to resolve
  // after "chicken"'s, since fetches aren't guaranteed to resolve in
  // the order they were sent.
  const latestQueryRef = useRef('')

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      setSearching(false)
      setSearchError('')
      return
    }

    setSearching(true)
    setSearchError('')
    const timer = setTimeout(async () => {
      latestQueryRef.current = trimmed
      try {
        const res = await api(`/food/search?q=${encodeURIComponent(trimmed)}`)
        if (latestQueryRef.current !== trimmed) return // a newer query has since started
        if (!res.ok) {
          setSearchError(`Search failed (${res.status})`)
          setResults([])
        } else {
          const data = await res.json()
          setResults(data.results || [])
        }
      } catch {
        if (latestQueryRef.current === trimmed) {
          setSearchError('Network error')
          setResults([])
        }
      } finally {
        if (latestQueryRef.current === trimmed) setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [query])

  function selectResult(result) {
    // Recipes are returned as search results per-serving (serving_size:
    // 1, serving_unit: 'serving' -- see routers/food.py's
    // _search_user_recipes) not per-gram like USDA/CNF results, so
    // scaling a recipe means "how many servings," not "how many grams."
    // Using gram-based scaling on a recipe result would silently divide
    // its already-correct per-serving nutrition by ~1g instead of
    // treating 1 as "the whole reference serving" -- caught before
    // shipping, not a live bug users hit.
    //
    // Meals have NO scaling concept at all (routers/meals.py: "a meal
    // always logs at face value") -- a meal result logs via
    // POST /meals/{id}/log (multiple food_log rows, one per item), not
    // POST /food/log, so there's no amount/serving picker for it either.
    const isRecipe = result.source === 'recipe'
    const isMeal = result.source === 'meal'
    const referenceGrams = (isRecipe || isMeal) ? null : (result.serving_size || 100)
    setSelected({
      result,
      isRecipe,
      isMeal,
      referenceGrams,
      targetGrams: isRecipe ? 1 : referenceGrams,
      meal: 'Snack',
      date: todayIso(),
    })
    setStatus('')
  }

  async function handleLog() {
    if (!selected) return
    setLogging(true)
    setStatus('')

    const { result, isRecipe, isMeal, referenceGrams, targetGrams, meal, date } = selected

    if (isMeal) {
      const res = await api(`/meals/${result.id}/log`, {
        method: 'POST',
        body: JSON.stringify({ date, meal, combined: true }),
      })
      setLogging(false)
      if (res.ok) {
        setStatus('logged')
        setSelected(null)
        setTimeout(() => setStatus(''), 3000)
      } else {
        const data = await res.json().catch(() => ({}))
        setStatus(data.detail || `Failed (${res.status})`)
      }
      return
    }

    const res = await api('/food/log', {
      method: 'POST',
      body: JSON.stringify({
        date,
        meal,
        food_name: result.name,
        source: result.source,
        source_id: result.id,
        serving_size: targetGrams,
        serving_unit: isRecipe ? 'serving' : 'g',
        calories: extractMacro(result.nutrients, 'calories'),
        // Protein/carbs/fat/fiber are not top-level fields — they're
        // already inside `nutrients` below (as "Protein", "Carbohydrate,
        // by difference", "Total lipid (fat)", "Fiber, total dietary"),
        // same as every other non-macro nutrient.
        nutrients: result.nutrients,
        scale_to: isRecipe
          ? { mode: 'multiple', servings_requested: targetGrams }
          : { mode: 'grams', from_grams: referenceGrams, to_grams: targetGrams },
      }),
    })
    setLogging(false)
    if (res.ok) {
      setStatus('logged')
      setSelected(null)
      setTimeout(() => setStatus(''), 3000)
    } else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  const factor = selected ? (selected.isRecipe ? selected.targetGrams : selected.targetGrams / selected.referenceGrams) : 1
  const previewCalories = selected
    ? Math.round(extractMacro(selected.result.nutrients, 'calories') * factor)
    : 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Log Food</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Search USDA and Canadian Nutrient File databases
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Search</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="e.g. banana, chicken breast, oatmeal"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-muted-foreground">
              <Search className={cn('h-4 w-4', searching && 'animate-pulse')} />
              {searching ? 'Searching...' : ''}
            </div>
          </div>

          {results.length > 0 && (
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {results.map((r) => (
                <div
                  key={`${r.source}-${r.id}`}
                  className={cn(
                    'w-full rounded-md border transition-colors flex items-center gap-2',
                    selected?.result === r ? 'border-primary bg-accent' : 'border-border hover:bg-muted'
                  )}
                >
                  <button
                    onClick={() => selectResult(r)}
                    className="flex-1 text-left px-3 py-2 flex items-center justify-between gap-3 min-w-0"
                  >
                    <div className="min-w-0">
                      <p className="text-sm text-foreground truncate">{r.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {r.source}{r.brand ? ` · ${r.brand}` : ''}
                        {r.serving_size ? ` · ${r.serving_size}${r.serving_unit || 'g'} serving` : ' · per 100g'}
                      </p>
                    </div>
                    <Plus className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  </button>
                  <button
                    onClick={() => setPreviewing(previewing === r ? null : r)}
                    title="Preview full nutrient breakdown"
                    className="p-2 mr-1 rounded-md text-muted-foreground hover:bg-muted transition-colors flex-shrink-0"
                  >
                    <Info className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {previewing && (
            <div className="rounded-md border border-border p-3 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-foreground">{previewing.name} — nutrient breakdown</p>
                <button onClick={() => setPreviewing(null)} className="text-xs text-muted-foreground hover:text-foreground">
                  Close
                </button>
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {/* Not re-sorted — GET /food/search already returns
                    nutrients in TrackStack's canonical display order
                    (nutrient_groups.order_nutrients). */}
                {Object.entries(previewing.nutrients || {})
                  .map(([name, info]) => (
                    <div key={name} className="flex items-center justify-between text-xs">
                      <span className="text-foreground">{name}</span>
                      <span className="font-mono text-muted-foreground">{info.value}{info.unit}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {!searching && query.trim().length >= 2 && results.length === 0 && !searchError && (
            <p className="text-sm text-muted-foreground">No results. Try a different search term.</p>
          )}
          {searchError && (
            <p className="text-sm text-destructive">{searchError}</p>
          )}
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>{selected.result.name}</CardTitle>
            <CardDescription>
              {selected.isMeal
                ? `Logs this meal as one combined entry (${selected.result.item_count ?? ''} items) — explode it later from the diary if you want to edit individual items`
                : selected.isRecipe
                ? 'Enter how many servings you\'re eating and the nutrients scale automatically'
                : `Reference: ${selected.referenceGrams}g — enter the actual amount you're eating and the nutrients scale automatically`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className={cn('grid gap-4', selected.isMeal ? 'md:grid-cols-2' : 'md:grid-cols-3')}>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Date</label>
                <input
                  type="date"
                  value={selected.date}
                  onChange={(e) => setSelected({ ...selected, date: e.target.value })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Meal</label>
                <select
                  value={selected.meal}
                  onChange={(e) => setSelected({ ...selected, meal: e.target.value })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {MEALS.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              {!selected.isMeal && (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    {selected.isRecipe ? 'Servings' : 'Amount (g)'}
                  </label>
                  <input
                    type="number"
                    min="0"
                    step={selected.isRecipe ? '0.5' : '1'}
                    value={selected.targetGrams}
                    onChange={(e) => setSelected({ ...selected, targetGrams: Number(e.target.value) || 0 })}
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              )}
            </div>

            {!selected.isMeal && (
              <p className="text-sm text-muted-foreground">
                ≈ <span className="font-mono text-foreground">{previewCalories} kcal</span> for {selected.targetGrams}{selected.isRecipe ? ' serving(s)' : 'g'}
              </p>
            )}

            <div className="flex items-center gap-3">
              <button
                onClick={handleLog}
                disabled={logging || (!selected.isMeal && selected.targetGrams <= 0)}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <Plus className="h-4 w-4" />
                {logging ? 'Logging...' : 'Log Entry'}
              </button>
              <button
                onClick={() => setSelected(null)}
                className="px-4 py-2 rounded-md border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
            </div>
          </CardContent>
        </Card>
      )}

      {status === 'logged' && (
        <div className="inline-flex items-center gap-2 text-sm text-green-700">
          <CheckCircle className="h-4 w-4" />
          Logged! View it on your Dashboard.
        </div>
      )}
      {status && status !== 'logged' && (
        <p className="text-sm text-destructive">{status}</p>
      )}
    </div>
  )
}
