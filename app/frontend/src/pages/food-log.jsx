import { useState } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Search, Plus, CheckCircle } from 'lucide-react'

const MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack']

// USDA FoodData Central reports macros as regular nutrient entries, not
// separate fields — these are the exact nutrient names USDA uses
// (confirmed against a live API response), mapped to the 5 hardcoded
// food_log macro columns. "Energy" appears twice per food (kJ and kcal)
// — must match on unit, not just name, or this silently grabs the wrong
// one and multiplies calories by ~4.
const MACRO_NUTRIENT_NAMES = {
  calories: { name: 'Energy', unit: 'KCAL' },
  protein: { name: 'Protein' },
  carbs: { name: 'Carbohydrate, by difference' },
  fat: { name: 'Total lipid (fat)' },
  fiber: { name: 'Fiber, total dietary' },
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
 * Food search + log page. Search hits GET /food/search (USDA + CNF),
 * picking a result opens an inline logger where the user chooses a meal,
 * date, and a gram amount to scale to — POST /food/log with `scale_to`
 * does the actual scaling server-side (portion_scaling.py), this page
 * never computes scaled nutrients itself.
 */
export default function FoodLog() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState(null)
  const [logging, setLogging] = useState(false)
  const [status, setStatus] = useState('')

  async function handleSearch(e) {
    e.preventDefault()
    if (query.trim().length < 2) return
    setSearching(true)
    setStatus('')
    const res = await api(`/food/search?q=${encodeURIComponent(query)}`)
    const data = await res.json()
    setResults(data.results || [])
    setSearching(false)
  }

  function selectResult(result) {
    // USDA results without an explicit servingSize are reported per
    // 100g by default — 100g is the correct implicit reference in that
    // case, not a guess.
    const referenceGrams = result.serving_size || 100
    setSelected({
      result,
      referenceGrams,
      targetGrams: referenceGrams,
      meal: 'Snack',
      date: new Date().toISOString().slice(0, 10),
    })
    setStatus('')
  }

  async function handleLog() {
    if (!selected) return
    setLogging(true)
    setStatus('')

    const { result, referenceGrams, targetGrams, meal, date } = selected
    const res = await api('/food/log', {
      method: 'POST',
      body: JSON.stringify({
        date,
        meal,
        food_name: result.name,
        source: result.source,
        source_id: result.id,
        serving_size: targetGrams,
        serving_unit: 'g',
        calories: extractMacro(result.nutrients, 'calories'),
        protein: extractMacro(result.nutrients, 'protein'),
        carbs: extractMacro(result.nutrients, 'carbs'),
        fat: extractMacro(result.nutrients, 'fat'),
        fiber: extractMacro(result.nutrients, 'fiber'),
        nutrients: result.nutrients,
        scale_to: { mode: 'grams', from_grams: referenceGrams, to_grams: targetGrams },
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

  const factor = selected ? selected.targetGrams / selected.referenceGrams : 1
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
          <form onSubmit={handleSearch} className="flex gap-2">
            <input
              type="text"
              placeholder="e.g. banana, chicken breast, oatmeal"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              type="submit"
              disabled={searching || query.trim().length < 2}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <Search className="h-4 w-4" />
              {searching ? 'Searching...' : 'Search'}
            </button>
          </form>

          {results.length > 0 && (
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {results.map((r) => (
                <button
                  key={`${r.source}-${r.id}`}
                  onClick={() => selectResult(r)}
                  className={cn(
                    'w-full text-left px-3 py-2 rounded-md border transition-colors flex items-center justify-between gap-3',
                    selected?.result === r ? 'border-primary bg-accent' : 'border-border hover:bg-muted'
                  )}
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
              ))}
            </div>
          )}

          {!searching && query.trim().length >= 2 && results.length === 0 && (
            <p className="text-sm text-muted-foreground">No results. Try a different search term.</p>
          )}
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>{selected.result.name}</CardTitle>
            <CardDescription>
              Reference: {selected.referenceGrams}g — enter the actual amount you're eating
              and the nutrients scale automatically
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-3">
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
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Amount (g)</label>
                <input
                  type="number"
                  min="0"
                  value={selected.targetGrams}
                  onChange={(e) => setSelected({ ...selected, targetGrams: Number(e.target.value) || 0 })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>

            <p className="text-sm text-muted-foreground">
              ≈ <span className="font-mono text-foreground">{previewCalories} kcal</span> for {selected.targetGrams}g
            </p>

            <div className="flex items-center gap-3">
              <button
                onClick={handleLog}
                disabled={logging || selected.targetGrams <= 0}
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
