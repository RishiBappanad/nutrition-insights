import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Plus, Trash2, CheckCheck, AlertTriangle, X, Refrigerator } from 'lucide-react'

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
  const [expiringOnly, setExpiringOnly] = useState(false)
  const [expiringItems, setExpiringItems] = useState(null)

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

  async function handleFinish(id) {
    await api(`/pantry/${id}/finish`, { method: 'POST' })
    refresh()
  }

  async function handleDelete(id) {
    await api(`/pantry/${id}`, { method: 'DELETE' })
    refresh()
  }

  const visibleItems = expiringOnly ? items.filter((i) => expiringItems?.has(i.id)) : items

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
              onFinish={() => handleFinish(item.id)}
              onDelete={() => handleDelete(item.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function PantryItemRow({ item, isConsuming, onConsumeClick, onConsumed, onFinish, onDelete }) {
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
            <button onClick={onDelete} title="Remove without logging" className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {isConsuming && <ConsumeForm item={item} onDone={onConsumed} onCancel={onConsumeClick} />}
      </CardContent>
    </Card>
  )
}

function ConsumeForm({ item, onDone, onCancel }) {
  const [servings, setServings] = useState(item.tracking_mode === 'single' ? 1 : 1)
  const [meal, setMeal] = useState('Snack')
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit() {
    setSubmitting(true)
    setError('')
    // The pantry item doesn't store its own nutrition data (design doc:
    // it resolves back to the same USDA/CNF food entry) -- without a
    // cached nutrient payload from when the item was added, this consume
    // action logs 0 macros/nutrients rather than fabricating values.
    // A future improvement could re-fetch by source/source_id here.
    const res = await api(`/pantry/${item.id}/consume`, {
      method: 'POST',
      body: JSON.stringify({ servings: Number(servings), date, meal }),
    })
    setSubmitting(false)
    if (res.ok) {
      onDone()
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
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
          disabled={submitting}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {submitting ? 'Logging...' : 'Confirm'}
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded-md border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors">
          Cancel
        </button>
        {error && <span className="text-sm text-destructive">{error}</span>}
      </div>
    </div>
  )
}

function AddItemForm({ onDone, onCancel }) {
  const [foodName, setFoodName] = useState('')
  const [source, setSource] = useState(null)
  const [sourceId, setSourceId] = useState(null)
  const [trackingMode, setTrackingMode] = useState('countable')
  const [remainingServings, setRemainingServings] = useState(1)
  const [servingUnit, setServingUnit] = useState('serving')
  const [expirationDate, setExpirationDate] = useState('')
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
    setFoodName(r.name)
    setSource(r.source)
    setSourceId(r.id)
    setServingUnit(r.serving_unit || 'g')
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
        serving_unit: servingUnit,
        tracking_mode: trackingMode,
        remaining_servings: trackingMode === 'countable' ? Number(remainingServings) : null,
        expiration_date: expirationDate || null,
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
