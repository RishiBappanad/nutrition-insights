import { useState } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { todayIso } from '@/lib/dates'
import { Search, Plus, CheckCircle, Info, ScanLine } from 'lucide-react'
import { useFoodSearch } from '@/hooks/useFoodSearch'
import { FoodPreviewModal } from '@/components/ui/food-preview-modal'
import { LabelPhotoCaptureButton } from '@/components/label-photo-capture-button'
import { BarcodeScannerModal } from '@/components/barcode-scanner-modal'

const MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack']

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
  const { query, setQuery, results, searching, searchError, sourceChips } = useFoodSearch()
  const [selected, setSelected] = useState(null)
  const [previewing, setPreviewing] = useState(null)
  const [logging, setLogging] = useState(false)
  const [status, setStatus] = useState('')
  const [scanResult, setScanResult] = useState(null)
  const [scanError, setScanError] = useState('')
  const [savingScan, setSavingScan] = useState(false)
  const [showBarcodeScanner, setShowBarcodeScanner] = useState(false)

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

  // Shared by both the label-photo scanner and the barcode scanner --
  // they return slightly different fields (LabelScanResult has
  // reference_amount/reference_unit, BarcodeScanResult doesn't) but both
  // carry food_name/brand/reference_grams/calories/nutrients/
  // suggested_matches/status, which is all this page needs.
  function handleScanResult(result) {
    setShowBarcodeScanner(false)
    if (result.status === 'manual_required' || result.status === 'failed' || result.status === 'not_found') {
      setScanError(result.error || 'Could not read that scan.')
      setScanResult(null)
      return
    }
    setScanError('')
    setScanResult(result)
  }

  // A suggested match is a normal search result (same source/id/nutrients
  // shape food_search.py already produces) -- selecting one reuses the
  // exact same logging flow as typing a search query, no separate
  // "linked item" concept needed.
  function selectSuggestedMatch(match) {
    selectResult(match)
    setScanResult(null)
  }

  async function saveScanAsNewFood() {
    if (!scanResult) return
    setSavingScan(true)
    try {
      const res = await api('/custom-foods', {
        method: 'POST',
        body: JSON.stringify({
          food_name: scanResult.food_name,
          brand: scanResult.brand || null,
          reference_amount: 1,
          reference_unit: scanResult.reference_grams ? 'g' : (scanResult.reference_unit || 'serving'),
          reference_grams: scanResult.reference_grams ?? null,
          calories: scanResult.calories || 0,
          nutrients: scanResult.nutrients || {},
        }),
      })
      if (!res.ok) {
        setScanError('Could not save this food.')
        return
      }
      const created = await res.json()
      selectResult({
        source: 'custom',
        id: created.id,
        name: scanResult.food_name,
        brand: scanResult.brand,
        nutrients: scanResult.nutrients,
        serving_size: scanResult.reference_grams || 100,
        serving_unit: scanResult.reference_grams ? 'g' : 'serving',
      })
      setScanResult(null)
    } finally {
      setSavingScan(false)
    }
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

          <div className="flex items-center gap-2">
            <LabelPhotoCaptureButton onResult={handleScanResult} onError={setScanError} />
            <button
              type="button"
              onClick={() => { setScanError(''); setShowBarcodeScanner(true) }}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors"
            >
              <ScanLine className="h-4 w-4" />
              Scan Barcode
            </button>
          </div>

          {scanError && (
            <p className="text-sm text-destructive">{scanError}</p>
          )}

          {scanResult && (
            <div className="space-y-3 border border-border rounded-md p-3">
              <div>
                <p className="text-sm font-medium text-foreground">{scanResult.food_name}</p>
                <p className="text-xs text-muted-foreground">
                  {scanResult.brand && `${scanResult.brand} · `}
                  {Math.round(scanResult.calories || 0)} kcal
                  {scanResult.reference_grams ? ` per ${scanResult.reference_grams}g` : ''}
                </p>
              </div>

              {scanResult.suggested_matches?.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-muted-foreground">
                    Matches an existing food in our database — use this instead of creating a duplicate:
                  </p>
                  {scanResult.suggested_matches.map((m) => (
                    <button
                      key={`${m.source}-${m.id}`}
                      onClick={() => selectSuggestedMatch(m)}
                      className="w-full text-left px-3 py-2 rounded-md border border-border hover:bg-muted transition-colors flex items-center justify-between gap-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-foreground truncate">{m.name}</p>
                        <p className="text-xs text-muted-foreground">{m.source}{m.brand ? ` · ${m.brand}` : ''}</p>
                      </div>
                      <Plus className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                    </button>
                  ))}
                </div>
              )}

              <div className="flex items-center gap-3">
                <button
                  onClick={saveScanAsNewFood}
                  disabled={savingScan}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  <Plus className="h-4 w-4" />
                  {savingScan ? 'Saving...' : 'Save as new food'}
                </button>
                <button
                  onClick={() => setScanResult(null)}
                  className="px-4 py-2 rounded-md border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors"
                >
                  Discard
                </button>
              </div>
            </div>
          )}

          {sourceChips}

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

      {showBarcodeScanner && (
        <BarcodeScannerModal
          onResult={handleScanResult}
          onError={(msg) => { setScanError(msg); setShowBarcodeScanner(false) }}
          onClose={() => setShowBarcodeScanner(false)}
        />
      )}

      <FoodPreviewModal result={previewing} onClose={() => setPreviewing(null)} />
    </div>
  )
}
