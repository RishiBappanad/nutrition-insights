import { useState, useEffect } from 'react'
import { Link } from 'wouter'
import { api } from '@/lib/api'
import { usePendingAction } from '@/lib/pending-action'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Plus, Trash2, CheckCheck, AlertTriangle, X, Refrigerator, Share2, ChefHat, ChevronDown, ChevronRight } from 'lucide-react'
import { todayIso } from '@/lib/dates'

// Fiber is deliberately NOT here — it is not a macro field on a pantry
// item; it's sent/read entirely via the `nutrients` map (key "Fiber,
// total dietary"), never extracted as a separate field.
const MACRO_NUTRIENT_NAMES = {
  calories: { name: 'Energy', unit: 'KCAL' },
  protein: { name: 'Protein' },
  carbs: { name: 'Carbohydrate, by difference' },
  fat: { name: 'Total lipid (fat)' },
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

const MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack']
const TRACKING_MODE_LABELS = {
  countable: 'Countable',
  bulk: 'Bulk',
  single: 'Single item',
}

export default function Pantry() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [consumingId, setConsumingId] = useState(null)
  const [removingId, setRemovingId] = useState(null)
  const [expiringOnly, setExpiringOnly] = useState(false)
  const [expiringItems, setExpiringItems] = useState(null)
  // Items with a buffered (not-yet-committed) removal/finish/delete/
  // consume action -- hidden from the list immediately (optimistic),
  // matching what the user just did, even though the real API call is
  // still buffered by the toast (see lib/pending-action.jsx) and hasn't
  // actually run yet.
  const [pendingHiddenIds, setPendingHiddenIds] = useState(new Set())
  const bufferAction = usePendingAction()

  function refresh() {
    setLoading(true)
    api('/pantry')
      .then((r) => r.json())
      .then((d) => setItems(d.items || []))
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  async function toggleExpiringFilter() {
    if (!expiringOnly) {
      const res = await api('/pantry/expiring?days=7')
      const data = await res.json()
      setExpiringItems(new Set(data.items.map((i) => i.id)))
    }
    setExpiringOnly(!expiringOnly)
  }

  function hideOptimistically(id) {
    setPendingHiddenIds((prev) => new Set(prev).add(id))
  }

  function unhide(id) {
    setPendingHiddenIds((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  function handleFinish(item) {
    hideOptimistically(item.id)
    bufferAction(
      `Finished ${item.food_name}`,
      async () => {
        await api(`/pantry/${item.id}/finish`, { method: 'POST' })
        refresh()
      },
      () => unhide(item.id),
    )
  }

  function handleDelete(item) {
    hideOptimistically(item.id)
    bufferAction(
      `Removed ${item.food_name}`,
      async () => {
        await api(`/pantry/${item.id}`, { method: 'DELETE' })
        refresh()
      },
      () => unhide(item.id),
    )
  }

  const visibleItems = (expiringOnly ? items.filter((i) => expiringItems?.has(i.id)) : items)
    .filter((i) => !pendingHiddenIds.has(i.id))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Pantry</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Track what's on hand, log directly from here when you eat it
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Item
        </button>
      </div>

      <button
        onClick={toggleExpiringFilter}
        className={cn(
          'inline-flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm font-medium transition-colors',
          expiringOnly ? 'bg-amber-500/10 border-amber-500/50 text-amber-500' : 'border-border text-muted-foreground hover:bg-muted'
        )}
      >
        <AlertTriangle className="h-4 w-4" />
        {expiringOnly ? 'Showing expiring soon (7 days)' : 'Show expiring soon'}
      </button>

      <MakeRecipeFromPantry />

      {showAdd && <AddItemForm onDone={() => { setShowAdd(false); refresh() }} onCancel={() => setShowAdd(false)} />}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : visibleItems.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground flex flex-col items-center gap-2">
            <Refrigerator className="h-8 w-8 opacity-40" />
            {expiringOnly ? 'Nothing expiring soon.' : 'Your pantry is empty. Add an item to get started.'}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {visibleItems.map((item) => (
            <PantryItemRow
              key={item.id}
              item={item}
              isConsuming={consumingId === item.id}
              onConsumeClick={() => setConsumingId(consumingId === item.id ? null : item.id)}
              onConsumed={() => { setConsumingId(null); refresh() }}
              isRemoving={removingId === item.id}
              onRemoveClick={() => setRemovingId(removingId === item.id ? null : item.id)}
              onRemoved={() => { setRemovingId(null); refresh() }}
              onFinish={() => handleFinish(item)}
              onDelete={() => handleDelete(item)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function PantryItemRow({ item, isConsuming, onConsumeClick, onConsumed, isRemoving, onRemoveClick, onRemoved, onFinish, onDelete }) {
  const isExpiringSoon = item.expiration_date && new Date(item.expiration_date) <= new Date(Date.now() + 7 * 86400000)

  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{item.food_name}</p>
            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
              <span>{TRACKING_MODE_LABELS[item.tracking_mode]}</span>
              {item.tracking_mode === 'countable' && (
                <span>· {item.remaining_servings} {item.serving_unit} remaining</span>
              )}
              {item.expiration_date && (
                <span className={cn(isExpiringSoon && 'text-amber-500 font-medium')}>
                  · Expires {item.expiration_date}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {item.tracking_mode === 'bulk' ? (
              <button
                onClick={onFinish}
                title="Mark as finished"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors"
              >
                <CheckCheck className="h-3.5 w-3.5" />
                Finish
              </button>
            ) : (
              <button
                onClick={onConsumeClick}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors"
              >
                <Plus className="h-3.5 w-3.5" />
                Log & Consume
              </button>
            )}
            {item.tracking_mode === 'countable' && (
              <button
                onClick={onRemoveClick}
                title="Remove servings without logging (e.g. shared with someone)"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors"
              >
                <Share2 className="h-3.5 w-3.5" />
                Remove
              </button>
            )}
            <button onClick={onDelete} title="Remove without logging" className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {isConsuming && <ConsumeForm item={item} onDone={onConsumed} onCancel={onConsumeClick} />}
        {isRemoving && <RemoveServingsForm item={item} onDone={onRemoved} onCancel={onRemoveClick} />}
      </CardContent>
    </Card>
  )
}

function RemoveServingsForm({ item, onDone, onCancel }) {
  const [servings, setServings] = useState(1)
  const [error, setError] = useState('')
  const bufferAction = usePendingAction()

  function handleSubmit() {
    const n = Number(servings)
    if (!n || n <= 0) {
      setError('Enter a positive amount')
      return
    }
    if (n > item.remaining_servings) {
      setError(`Only ${item.remaining_servings} available`)
      return
    }
    setError('')
    // Buffered: closes the form immediately (matches the user's intent
    // right away), but the real POST /pantry/{id}/remove call is
    // deferred until the toast's window elapses or the user navigates
    // away -- Undo on the toast means this never actually happens.
    // onDone (passed in) already triggers a refresh, but that refresh
    // runs BEFORE the buffered call actually lands -- refresh again
    // inside commit so the UI reflects the real decrement once it
    // actually happens, not just once the form closes.
    bufferAction(`Removed ${n} ${item.serving_unit} of ${item.food_name}`, async () => {
      await api(`/pantry/${item.id}/remove`, { method: 'POST', body: JSON.stringify({ servings: n }) })
      onDone()
    })
    onCancel() // close the form without the immediate refresh onDone would also trigger
  }

  return (
    <div className="mt-3 pt-3 border-t border-border space-y-3">
      <p className="text-xs text-muted-foreground">
        Takes servings out of the pantry without logging anything to your diary — for sharing, spoilage, etc.
      </p>
      <div className="flex items-center gap-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Servings ({item.remaining_servings} available)</label>
          <input
            type="number"
            min="0"
            max={item.remaining_servings}
            step="0.5"
            value={servings}
            onChange={(e) => setServings(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handleSubmit}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Confirm
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded-md border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors">
          Cancel
        </button>
        {error && <span className="text-sm text-destructive">{error}</span>}
      </div>
    </div>
  )
}

function ConsumeForm({ item, onDone, onCancel }) {
  const [servings, setServings] = useState(item.tracking_mode === 'single' ? 1 : 1)
  const [meal, setMeal] = useState('Snack')
  const [date, setDate] = useState(todayIso())
  const [error, setError] = useState('')
  const bufferAction = usePendingAction()

  function handleSubmit() {
    const n = Number(servings)
    if (!n || n <= 0) {
      setError('Enter a positive amount')
      return
    }
    if (item.tracking_mode === 'countable' && n > item.remaining_servings) {
      setError(`Only ${item.remaining_servings} available`)
      return
    }
    setError('')
    // Buffered: closes the form immediately, but the real
    // POST /pantry/{id}/consume call (which both logs to the diary AND
    // decrements the pantry item) is deferred -- Undo means neither the
    // diary entry nor the decrement ever happens. Nutrition is read
    // from the pantry item server-side (see routers/pantry.py), no
    // macro/nutrient payload needed here.
    bufferAction(`Logged ${n} ${item.serving_unit} of ${item.food_name} to ${meal}`, async () => {
      await api(`/pantry/${item.id}/consume`, {
        method: 'POST',
        body: JSON.stringify({ servings: n, date, meal }),
      })
      onDone()
    })
    onCancel()
  }

  return (
    <div className="mt-3 pt-3 border-t border-border space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Meal</label>
          <select value={meal} onChange={(e) => setMeal(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring">
            {MEALS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        {item.tracking_mode === 'countable' && (
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Servings ({item.remaining_servings} available)</label>
            <input
              type="number"
              min="0"
              max={item.remaining_servings}
              step="0.5"
              value={servings}
              onChange={(e) => setServings(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        )}
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handleSubmit}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Confirm
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded-md border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors">
          Cancel
        </button>
        {error && <span className="text-sm text-destructive">{error}</span>}
      </div>
    </div>
  )
}

function MakeRecipeFromPantry() {
  // Pantry-driven entry point into the SAME "make a recipe" flow the
  // recipe detail page has -- per explicit user request: someone
  // looking at their pantry should be able to jump straight to "what
  // can I make with what I already have," not just discover it from
  // the recipes page. Reuses GET /recipes/{id}/can-make and
  // POST /recipes/{id}/make exactly, no separate backend logic --
  // this is purely a different UI entry point onto existing endpoints.
  const [expanded, setExpanded] = useState(false)
  const [recipes, setRecipes] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [checks, setChecks] = useState({}) // recipeId -> can-make result
  const [makingId, setMakingId] = useState(null)
  const [status, setStatus] = useState('')

  async function load() {
    if (loaded) return
    setLoading(true)
    const res = await api('/recipes')
    const data = await res.json()
    const list = data.recipes || []
    setRecipes(list)
    setLoaded(true)
    setLoading(false)

    // Check every recipe against the pantry up front -- this is the
    // whole point of a pantry-driven view: show what's makeable RIGHT
    // NOW without the user having to open each recipe individually.
    const results = await Promise.all(
      list.map((r) => api(`/recipes/${r.id}/can-make`).then((res) => res.json()))
    )
    const byId = {}
    results.forEach((r) => { byId[r.recipe_id] = r })
    setChecks(byId)
  }

  function toggle() {
    setExpanded((e) => !e)
    if (!expanded) load()
  }

  async function handleMake(recipeId) {
    setMakingId(recipeId)
    setStatus('')
    const res = await api(`/recipes/${recipeId}/make`, { method: 'POST' })
    setMakingId(null)
    if (res.ok) {
      const data = await res.json()
      setStatus(`Added ${data.servings_added} servings to your pantry`)
      setLoaded(false)
      load() // pantry state changed for every recipe's ingredients, not just this one
      setTimeout(() => setStatus(''), 4000)
    } else {
      const data = await res.json().catch(() => ({}))
      const detail = data.detail
      setStatus(typeof detail === 'object' ? detail.message : detail || `Failed (${res.status})`)
    }
  }

  const makeable = recipes.filter((r) => checks[r.id]?.can_make)
  const notMakeable = recipes.filter((r) => checks[r.id] && !checks[r.id].can_make)

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={toggle}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ChefHat className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm font-medium">Make a Recipe from Your Pantry</CardTitle>
          </div>
          {expanded ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </div>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-3">
          {loading ? (
            <p className="text-sm text-muted-foreground">Checking your recipes against your pantry...</p>
          ) : recipes.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No recipes yet. <Link href="/recipes"><span className="text-primary underline cursor-pointer">Create one</span></Link> to see what you can make from your pantry.
            </p>
          ) : (
            <>
              {makeable.length === 0 && notMakeable.length === 0 && (
                <p className="text-sm text-muted-foreground">Checking...</p>
              )}
              {makeable.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-emerald-500">You can make right now:</p>
                  {makeable.map((r) => (
                    <div key={r.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-emerald-500/30 bg-emerald-500/5">
                      <div>
                        <p className="text-sm font-medium text-foreground">{r.name}</p>
                        <p className="text-xs text-muted-foreground">{r.servings_per_batch} servings per batch</p>
                      </div>
                      <button
                        onClick={() => handleMake(r.id)}
                        disabled={makingId === r.id}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors flex-shrink-0"
                      >
                        {makingId === r.id ? 'Making...' : 'Make It'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {notMakeable.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">Missing ingredients for:</p>
                  {notMakeable.map((r) => (
                    <Link key={r.id} href="/recipes">
                      <div className="px-3 py-2 rounded-md border border-border hover:bg-muted transition-colors cursor-pointer">
                        <p className="text-sm text-foreground">{r.name}</p>
                        <p className="text-xs text-muted-foreground">
                          Missing: {checks[r.id].missing.map((m) => m.food_name).join(', ') || 'unmatchable ingredients'}
                        </p>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </>
          )}
          {status && <p className="text-sm text-emerald-500">{status}</p>}
        </CardContent>
      )}
    </Card>
  )
}

function AddItemForm({ onDone, onCancel }) {
  const [foodName, setFoodName] = useState('')
  const [source, setSource] = useState(null)
  const [sourceId, setSourceId] = useState(null)
  const [trackingMode, setTrackingMode] = useState('countable')
  const [remainingServings, setRemainingServings] = useState(1)
  const [servingSize, setServingSize] = useState(1)
  const [servingUnit, setServingUnit] = useState('serving')
  const [expirationDate, setExpirationDate] = useState('')
  const [nutrition, setNutrition] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      return
    }
    setSearching(true)
    const timer = setTimeout(async () => {
      const res = await api(`/food/search?q=${encodeURIComponent(trimmed)}`)
      const data = await res.json()
      setResults(data.results || [])
      setSearching(false)
    }, 400)
    return () => clearTimeout(timer)
  }, [query])

  function selectResult(r) {
    // Store nutrition PER the result's own reference serving (serving_size/
    // serving_unit) alongside the pantry item -- so /consume and /remove
    // never need this data resupplied later (see routers/pantry.py).
    setFoodName(r.name)
    setSource(r.source)
    setSourceId(r.id)
    setServingSize(r.serving_size || 100)
    setServingUnit(r.serving_unit || 'g')
    setNutrition({
      calories: extractMacro(r.nutrients, 'calories'),
      protein: extractMacro(r.nutrients, 'protein'),
      carbs: extractMacro(r.nutrients, 'carbs'),
      fat: extractMacro(r.nutrients, 'fat'),
      nutrients: r.nutrients,
    })
    setQuery('')
    setResults([])
  }

  async function handleSave() {
    if (!foodName.trim()) {
      setError('Food name is required')
      return
    }
    if (trackingMode === 'countable' && (!remainingServings || remainingServings <= 0)) {
      setError('Countable items need a positive serving count')
      return
    }
    setSaving(true)
    setError('')
    const res = await api('/pantry', {
      method: 'POST',
      body: JSON.stringify({
        food_name: foodName,
        source, source_id: sourceId,
        serving_size: servingSize,
        serving_unit: servingUnit,
        tracking_mode: trackingMode,
        remaining_servings: trackingMode === 'countable' ? Number(remainingServings) : null,
        expiration_date: expirationDate || null,
        calories: nutrition?.calories || 0,
        protein: nutrition?.protein || 0,
        carbs: nutrition?.carbs || 0,
        fat: nutrition?.fat || 0,
        nutrients: nutrition?.nutrients || {},
      }),
    })
    setSaving(false)
    if (res.ok) {
      onDone()
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Add Pantry Item</CardTitle>
        <button onClick={onCancel} className="p-1 rounded-md text-muted-foreground hover:bg-muted transition-colors">
          <X className="h-4 w-4" />
        </button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="relative">
          <label className="text-xs font-medium text-muted-foreground">Food (search to auto-fill, or type manually)</label>
          <input
            type="text"
            placeholder="e.g. chicken breast"
            value={query || foodName}
            onChange={(e) => { setQuery(e.target.value); setFoodName(e.target.value); setSource(null); setSourceId(null) }}
            className="mt-1.5 w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {(searching || results.length > 0) && (
            <div className="absolute z-10 mt-1 w-full bg-card border border-border rounded-md shadow-lg max-h-60 overflow-y-auto">
              {searching && <div className="px-3 py-2 text-xs text-muted-foreground">Searching...</div>}
              {results.map((r) => (
                <button
                  key={`${r.source}-${r.id}`}
                  onClick={() => selectResult(r)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors"
                >
                  {r.name}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Tracking Mode</label>
          <div className="flex gap-2">
            {Object.entries(TRACKING_MODE_LABELS).map(([mode, label]) => (
              <button
                key={mode}
                onClick={() => setTrackingMode(mode)}
                className={cn(
                  'px-3 py-1.5 rounded-md text-sm font-medium transition-colors border',
                  trackingMode === mode ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:bg-muted'
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {trackingMode === 'countable' && 'A box with a known number of servings (e.g. 18 crackers) — decrements as you log.'}
            {trackingMode === 'bulk' && "Something you don't track an exact amount for (e.g. a spice jar) — just mark Finish when it's gone."}
            {trackingMode === 'single' && 'A single item (e.g. one apple) — logging it removes it entirely.'}
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {trackingMode === 'countable' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Servings ({servingUnit})</label>
              <input type="number" min="0" step="0.5" value={remainingServings} onChange={(e) => setRemainingServings(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
          )}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Expiration Date (optional)</label>
            <input type="date" value={expirationDate} onChange={(e) => setExpirationDate(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {saving ? 'Adding...' : 'Add to Pantry'}
          </button>
          {error && <span className="text-sm text-destructive">{error}</span>}
        </div>
      </CardContent>
    </Card>
  )
}
