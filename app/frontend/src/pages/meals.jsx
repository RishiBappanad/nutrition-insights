import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Plus, Save, CheckCircle, ArrowLeft, Layers, X } from 'lucide-react'
import { todayIso } from '@/lib/dates'

// Fiber is deliberately NOT here — it is not a macro field on a meal
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

/**
 * Meals: a simple named collection of items, logged together at face
 * value -- deliberately simpler than Recipes (no servings-per-batch, no
 * scaling, no pantry can-make check), matching routers/meals.py's design
 * ("a meal is a flat group of real foods, not a batch-divided recipe").
 */
export default function Meals() {
  const [view, setView] = useState('list')
  const [meals, setMeals] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState(null)

  function refresh() {
    setLoading(true)
    api('/meals')
      .then((r) => r.json())
      .then((d) => setMeals(d.meals || []))
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  if (view === 'edit') {
    return <MealEditor mealId={activeId} onDone={() => { setView('list'); refresh() }} onCancel={() => setView('list')} />
  }
  if (view === 'detail') {
    return <MealDetail mealId={activeId} onBack={() => { setView('list'); refresh() }} onEdit={() => setView('edit')} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Meals</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Save a group of items you eat together, log them all at once
          </p>
        </div>
        <button
          onClick={() => { setActiveId(null); setView('edit') }}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          New Meal
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : meals.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No saved meals yet. Save a group of items you eat together often.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {meals.map((m) => (
            <button
              key={m.id}
              onClick={() => { setActiveId(m.id); setView('detail') }}
              className="text-left px-4 py-3 rounded-md border border-border hover:bg-muted transition-colors flex items-center justify-between gap-3"
            >
              <p className="text-sm font-medium text-foreground">{m.name}</p>
              <Layers className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function MealDetail({ mealId, onBack, onEdit }) {
  const [meal, setMeal] = useState(null)
  const [loading, setLoading] = useState(true)
  const [logMeal, setLogMeal] = useState('Breakfast')
  const [logDate, setLogDate] = useState(todayIso())
  const [logging, setLogging] = useState(false)
  const [status, setStatus] = useState('')

  useEffect(() => {
    api(`/meals/${mealId}`)
      .then((r) => r.json())
      .then(setMeal)
      .finally(() => setLoading(false))
  }, [mealId])

  async function handleLog() {
    setLogging(true)
    setStatus('')
    const res = await api(`/meals/${mealId}/log`, {
      method: 'POST',
      body: JSON.stringify({ date: logDate, meal: logMeal }),
    })
    setLogging(false)
    if (res.ok) {
      setStatus('logged')
      setTimeout(() => setStatus(''), 3000)
    } else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  async function handleDelete() {
    await api(`/meals/${mealId}`, { method: 'DELETE' })
    onBack()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading...</p>
  if (!meal) return <p className="text-sm text-destructive">Meal not found.</p>

  const totalCalories = meal.items.reduce((sum, i) => sum + (i.calories || 0), 0)

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" /> Back to Meals
      </button>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{meal.name}</h1>
          <p className="text-muted-foreground text-sm mt-1">{Math.round(totalCalories)} kcal total</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={onEdit} className="px-3 py-2 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors">
            Edit
          </button>
          <button onClick={handleDelete} className="px-3 py-2 rounded-md border border-destructive/50 text-sm font-medium text-destructive hover:bg-destructive/10 transition-colors">
            Delete
          </button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {meal.items.map((item) => (
            <div key={item.id} className="flex items-center justify-between text-sm py-1 border-b border-border last:border-0">
              <span className="text-foreground">{item.food_name}</span>
              <span className="text-muted-foreground font-mono text-xs">{Math.round(item.calories)} kcal</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Log to Diary</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Date</label>
              <input type="date" value={logDate} onChange={(e) => setLogDate(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Meal</label>
              <select value={logMeal} onChange={(e) => setLogMeal(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring">
                {MEALS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleLog}
              disabled={logging}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <Plus className="h-4 w-4" />
              {logging ? 'Logging...' : `Log All ${meal.items.length} Items`}
            </button>
            {status === 'logged' && (
              <span className="inline-flex items-center gap-1 text-sm text-green-700"><CheckCircle className="h-4 w-4" /> Logged!</span>
            )}
            {status && status !== 'logged' && <span className="text-sm text-destructive">{status}</span>}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function MealEditor({ mealId, onDone, onCancel }) {
  const [name, setName] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(!!mealId)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')

  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    if (!mealId) return
    api(`/meals/${mealId}`)
      .then((r) => r.json())
      .then((d) => {
        setName(d.name)
        setItems(d.items.map((i) => ({
          food_name: i.food_name, source: i.source, source_id: i.source_id,
          serving_size: i.serving_size, serving_unit: i.serving_unit,
          calories: i.calories, protein: i.protein, carbs: i.carbs, fat: i.fat,
          nutrients: i.nutrients,
        })))
      })
      .finally(() => setLoading(false))
  }, [mealId])

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

  function addItem(result) {
    items.push({
      food_name: result.name, source: result.source, source_id: result.id,
      serving_size: result.serving_size || 100, serving_unit: result.serving_unit || 'g',
      calories: extractMacro(result.nutrients, 'calories'),
      protein: extractMacro(result.nutrients, 'protein'),
      carbs: extractMacro(result.nutrients, 'carbs'),
      fat: extractMacro(result.nutrients, 'fat'),
      nutrients: result.nutrients,
    })
    setItems([...items])
    setQuery('')
    setResults([])
  }

  function removeItem(idx) {
    setItems(items.filter((_, i) => i !== idx))
  }

  async function handleSave() {
    if (!name.trim()) {
      setStatus('Meal name is required')
      return
    }
    setSaving(true)
    setStatus('')
    const body = { name, items }
    const res = mealId
      ? await api(`/meals/${mealId}`, { method: 'PUT', body: JSON.stringify(body) })
      : await api('/meals', { method: 'POST', body: JSON.stringify(body) })
    setSaving(false)
    if (res.ok) {
      onDone()
    } else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading...</p>

  return (
    <div className="space-y-6">
      <button onClick={onCancel} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" /> Cancel
      </button>

      <h1 className="text-2xl font-semibold tracking-tight">{mealId ? 'Edit Meal' : 'New Meal'}</h1>

      <Card>
        <CardContent className="pt-6">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Meal Name</label>
            <input type="text" placeholder="e.g. My Usual Breakfast" value={name} onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <input
              type="text"
              placeholder="Search to add an item..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {(searching || results.length > 0) && (
              <div className="absolute z-10 mt-1 w-full bg-card border border-border rounded-md shadow-lg max-h-60 overflow-y-auto">
                {searching && <div className="px-3 py-2 text-xs text-muted-foreground">Searching...</div>}
                {results.map((r) => (
                  <button
                    key={`${r.source}-${r.id}`}
                    onClick={() => addItem(r)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors flex items-center justify-between gap-2"
                  >
                    <span className="truncate">{r.name}</span>
                    <Plus className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No items added yet.</p>
          ) : (
            <div className="space-y-2">
              {items.map((item, idx) => (
                <div key={idx} className="flex items-center gap-3 px-3 py-2 rounded-md border border-border">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground truncate">{item.food_name}</p>
                    <p className="text-xs text-muted-foreground">{Math.round(item.calories)} kcal</p>
                  </div>
                  <button onClick={() => removeItem(idx)} className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          <Save className="h-4 w-4" />
          {saving ? 'Saving...' : 'Save Meal'}
        </button>
        {status && <span className="text-sm text-destructive">{status}</span>}
      </div>
    </div>
  )
}
