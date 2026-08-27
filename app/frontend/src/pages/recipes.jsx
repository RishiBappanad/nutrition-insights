import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Plus, Trash2, Save, CheckCircle, Search, ArrowLeft, ChefHat, X, DownloadCloud, Loader2 } from 'lucide-react'
import { todayIso } from '@/lib/dates'

// Protein/carbs/fat/fiber are deliberately NOT here — none of them are
// macro fields on a recipe item; they're sent/read entirely via the
// `nutrients` map, never extracted as separate fields.
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

const MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack']

export default function Recipes() {
  const [view, setView] = useState('list') // 'list' | 'edit' | 'detail'
  const [recipes, setRecipes] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState(null)

  // Cronometer Recipe Import State
  const [showCronoModal, setShowCronoModal] = useState(false)
  const [cronoRecipes, setCronoRecipes] = useState([])
  const [cronoLoading, setCronoLoading] = useState(false)
  const [cronoError, setCronoError] = useState(null)
  const [importingId, setImportingId] = useState(null)
  const [importedIds, setImportedIds] = useState(new Set())

  function refresh() {
    setLoading(true)
    api('/recipes')
      .then((r) => r.json())
      .then((d) => setRecipes(d.recipes || []))
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  function fetchCronometerRecipes() {
    setCronoLoading(true)
    setCronoError(null)
    api('/sync/cronometer/recipes')
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json()
          throw new Error(err.detail || 'Failed to fetch Cronometer recipes')
        }
        return res.json()
      })
      .then((data) => {
        const list = data.recipes || []
        setCronoRecipes(list)
        const alreadyImported = list.filter((r) => r.is_imported).map((r) => r.food_id)
        setImportedIds((prev) => new Set([...prev, ...alreadyImported]))
      })
      .catch((err) => setCronoError(err.message))
      .finally(() => setCronoLoading(false))
  }

  function handleImport(foodId) {
    setImportingId(foodId)
    api(`/sync/cronometer/recipes/${foodId}/import`, { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json()
          throw new Error(err.detail || 'Failed to import recipe')
        }
        return res.json()
      })
      .then(() => {
        setImportedIds((prev) => new Set([...prev, foodId]))
        refresh()
      })
      .catch((err) => setCronoError(err.message))
      .finally(() => setImportingId(null))
  }

  if (view === 'edit') {
    return <RecipeEditor recipeId={activeId} onDone={() => { setView('list'); refresh() }} onCancel={() => setView('list')} />
  }
  if (view === 'detail') {
    return <RecipeDetail recipeId={activeId} onBack={() => { setView('list'); refresh() }} onEdit={() => setView('edit')} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Recipes</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Aggregate ingredients into a batch, log a serving to your diary
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setShowCronoModal(true)
              fetchCronometerRecipes()
            }}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-background text-sm font-medium hover:bg-muted transition-colors"
          >
            <DownloadCloud className="h-4 w-4 text-muted-foreground" />
            Import from Cronometer
          </button>
          <button
            onClick={() => { setActiveId(null); setView('edit') }}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-4 w-4" />
            New Recipe
          </button>
        </div>
      </div>

      {showCronoModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">Cronometer Custom Recipes</h2>
                <p className="text-xs text-muted-foreground mt-0.5">Select a recipe to resolve ingredients and import into TrackStack</p>
              </div>
              <button
                onClick={() => setShowCronoModal(false)}
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {cronoError && (
              <div className="p-3 text-xs rounded bg-destructive/10 text-destructive border border-destructive/20">
                {cronoError}
              </div>
            )}

            {cronoLoading ? (
              <div className="py-8 flex flex-col items-center justify-center gap-2 text-muted-foreground text-sm">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span>Connecting to Cronometer &amp; resolving custom recipes...</span>
              </div>
            ) : cronoRecipes.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                No custom recipes found in your Cronometer account.
              </div>
            ) : (
              <div className="max-h-80 overflow-y-auto space-y-2 pr-1">
                {cronoRecipes.map((r) => {
                  const isImported = importedIds.has(r.food_id)
                  const isImporting = importingId === r.food_id

                  return (
                    <div
                      key={r.food_id}
                      className="p-3 rounded-md border border-border flex items-center justify-between gap-3 hover:bg-muted/50 transition-colors"
                    >
                      <div>
                        <p className="text-sm font-medium text-foreground">{r.name}</p>
                        <p className="text-xs text-muted-foreground">{r.ingredient_count} ingredient{r.ingredient_count === 1 ? '' : 's'}</p>
                      </div>

                      {isImported ? (
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center gap-1 text-xs text-emerald-500 font-medium">
                            <CheckCircle className="h-4 w-4" />
                            Imported
                          </span>
                          <button
                            disabled={isImporting}
                            onClick={() => handleImport(r.food_id)}
                            className="px-2.5 py-1 text-xs font-medium rounded border border-border hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                            title="Re-import / refresh recipe ingredients from Cronometer"
                          >
                            {isImporting ? 'Syncing...' : 'Re-sync'}
                          </button>
                        </div>
                      ) : (
                        <button
                          disabled={isImporting}
                          onClick={() => handleImport(r.food_id)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                        >
                          {isImporting && <Loader2 className="h-3 w-3 animate-spin" />}
                          {isImporting ? 'Importing...' : 'Import'}
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            <div className="flex justify-end pt-2">
              <button
                onClick={() => setShowCronoModal(false)}
                className="px-4 py-2 text-sm rounded-md border border-border hover:bg-muted transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : recipes.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No recipes yet. Create one to aggregate ingredients into a reusable batch.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {recipes.map((r) => (
            <button
              key={r.id}
              onClick={() => { setActiveId(r.id); setView('detail') }}
              className="text-left px-4 py-3 rounded-md border border-border hover:bg-muted transition-colors flex items-center justify-between gap-3"
            >
              <div>
                <p className="text-sm font-medium text-foreground">{r.name}</p>
                <p className="text-xs text-muted-foreground">{r.servings_per_batch} servings per batch</p>
              </div>
              <ChefHat className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function RecipeDetail({ recipeId, onBack, onEdit }) {
  const [recipe, setRecipe] = useState(null)
  const [loading, setLoading] = useState(true)
  const [canMake, setCanMake] = useState(null)
  const [checkingCanMake, setCheckingCanMake] = useState(false)
  const [making, setMaking] = useState(false)
  const [makeStatus, setMakeStatus] = useState('')
  const [logServings, setLogServings] = useState(1)
  const [logMeal, setLogMeal] = useState('Lunch')
  const [logDate, setLogDate] = useState(todayIso())
  const [logging, setLogging] = useState(false)
  const [status, setStatus] = useState('')

  useEffect(() => {
    api(`/recipes/${recipeId}`)
      .then((r) => r.json())
      .then(setRecipe)
      .finally(() => setLoading(false))
  }, [recipeId])

  async function checkCanMake() {
    setCheckingCanMake(true)
    const res = await api(`/recipes/${recipeId}/can-make`)
    setCanMake(await res.json())
    setCheckingCanMake(false)
  }

  async function handleMake() {
    setMaking(true)
    setMakeStatus('')
    const res = await api(`/recipes/${recipeId}/make`, { method: 'POST' })
    setMaking(false)
    if (res.ok) {
      const data = await res.json()
      setMakeStatus(`made:${data.servings_added}`)
      setCanMake(null) // pantry state changed -- force a fresh check if they look again
    } else {
      const data = await res.json().catch(() => ({}))
      const detail = data.detail
      setMakeStatus(typeof detail === 'object' ? detail.message : detail || `Failed (${res.status})`)
    }
  }

  async function handleLog() {
    setLogging(true)
    setStatus('')
    const res = await api(`/recipes/${recipeId}/log`, {
      method: 'POST',
      body: JSON.stringify({ date: logDate, meal: logMeal, servings: Number(logServings) }),
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
    await api(`/recipes/${recipeId}`, { method: 'DELETE' })
    onBack()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading...</p>
  if (!recipe) return <p className="text-sm text-destructive">Recipe not found.</p>

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" /> Back to Recipes
      </button>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{recipe.name}</h1>
          <p className="text-muted-foreground text-sm mt-1">{recipe.servings_per_batch} servings per batch</p>
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
          <CardTitle>Ingredients</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {recipe.items.map((item) => (
            <div key={item.id} className="flex items-center justify-between text-sm py-1 border-b border-border last:border-0">
              <span className="text-foreground">{item.food_name}</span>
              <span className="text-muted-foreground font-mono text-xs">
                {item.amount_grams ? `${item.amount_grams}g` : item.amount_multiple ? `${item.amount_multiple}x` : ''}
                {' · '}{Math.round(item.calories)} kcal
              </span>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Nutrition</CardTitle>
            <CardDescription>Per serving ({recipe.servings_per_batch} servings total)</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            {Object.entries(recipe.per_serving_totals.macros).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-muted-foreground capitalize">{k}</span>
                <span className="font-mono text-foreground">{Math.round(v)}{k === 'calories' ? ' kcal' : 'g'}</span>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Can I make this?</CardTitle>
            <CardDescription>Check against your pantry</CardDescription>
          </CardHeader>
          <CardContent>
            {!canMake ? (
              <button
                onClick={checkCanMake}
                disabled={checkingCanMake}
                className="px-4 py-2 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
              >
                {checkingCanMake ? 'Checking...' : 'Check Pantry'}
              </button>
            ) : (
              <div className="space-y-3">
                <p className={cn('text-sm font-medium', canMake.can_make ? 'text-emerald-500' : 'text-amber-500')}>
                  {canMake.can_make ? 'You have everything!' : 'Missing some ingredients'}
                </p>
                {canMake.missing.length > 0 && (
                  <div className="text-xs text-muted-foreground">
                    Missing: {canMake.missing.map((m) => m.food_name).join(', ')}
                  </div>
                )}
                {canMake.unmatchable.length > 0 && (
                  <div className="text-xs text-muted-foreground">
                    Can't check: {canMake.unmatchable.map((m) => m.food_name).join(', ')} (no pantry match)
                  </div>
                )}
                {canMake.can_make && (
                  <div className="pt-1">
                    <button
                      onClick={handleMake}
                      disabled={making}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
                    >
                      <ChefHat className="h-4 w-4" />
                      {making ? 'Making...' : 'Make It'}
                    </button>
                    <p className="text-xs text-muted-foreground mt-1.5">
                      Decrements the ingredients used, then adds the finished batch ({recipe.servings_per_batch} servings) to
                      your pantry — it won't be logged to your diary until you consume it from there.
                    </p>
                  </div>
                )}
                {makeStatus.startsWith('made:') && (
                  <p className="text-sm text-emerald-500 flex items-center gap-1">
                    <CheckCircle className="h-4 w-4" />
                    Added {makeStatus.split(':')[1]} servings to your pantry
                  </p>
                )}
                {makeStatus && !makeStatus.startsWith('made:') && (
                  <p className="text-sm text-destructive">{makeStatus}</p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Log to Diary</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
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
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Servings</label>
              <input type="number" min="0" step="0.5" value={logServings} onChange={(e) => setLogServings(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleLog}
              disabled={logging || Number(logServings) <= 0}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <Plus className="h-4 w-4" />
              {logging ? 'Logging...' : 'Log Entry'}
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

function RecipeEditor({ recipeId, onDone, onCancel }) {
  const [name, setName] = useState('')
  const [servingsPerBatch, setServingsPerBatch] = useState(1)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(!!recipeId)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')

  // Item search state
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    if (!recipeId) return
    api(`/recipes/${recipeId}`)
      .then((r) => r.json())
      .then((d) => {
        setName(d.name)
        setServingsPerBatch(d.servings_per_batch)
        setItems(d.items.map((i) => ({
          food_name: i.food_name, source: i.source, source_id: i.source_id,
          amount_grams: i.amount_grams, amount_multiple: i.amount_multiple,
          calories: i.calories,
          nutrients: i.nutrients,
        })))
      })
      .finally(() => setLoading(false))
  }, [recipeId])

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
    const referenceGrams = result.serving_size || 100
    items.push({
      food_name: result.name, source: result.source, source_id: result.id,
      amount_grams: referenceGrams,
      calories: extractMacro(result.nutrients, 'calories'),
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
      setStatus('Recipe name is required')
      return
    }
    if (servingsPerBatch <= 0) {
      setStatus('Servings per batch must be positive')
      return
    }
    setSaving(true)
    setStatus('')
    const body = { name, servings_per_batch: Number(servingsPerBatch), items }
    const res = recipeId
      ? await api(`/recipes/${recipeId}`, { method: 'PUT', body: JSON.stringify(body) })
      : await api('/recipes', { method: 'POST', body: JSON.stringify(body) })
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

      <h1 className="text-2xl font-semibold tracking-tight">{recipeId ? 'Edit Recipe' : 'New Recipe'}</h1>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Recipe Name</label>
              <input type="text" placeholder="e.g. Lasagna" value={name} onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Servings Per Batch</label>
              <input type="number" min="0.5" step="0.5" value={servingsPerBatch} onChange={(e) => setServingsPerBatch(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Ingredients</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <input
              type="text"
              placeholder="Search to add an ingredient..."
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
            <p className="text-sm text-muted-foreground">No ingredients added yet.</p>
          ) : (
            <div className="space-y-2">
              {items.map((item, idx) => (
                <div key={idx} className="flex items-center gap-3 px-3 py-2 rounded-md border border-border">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground truncate">{item.food_name}</p>
                    <p className="text-xs text-muted-foreground">{Math.round(item.calories)} kcal</p>
                  </div>
                  <input
                    type="number"
                    min="0"
                    value={item.amount_grams ?? ''}
                    onChange={(e) => {
                      items[idx].amount_grams = Number(e.target.value) || 0
                      setItems([...items])
                    }}
                    className="w-20 px-2 py-1.5 rounded-md border bg-background text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <span className="text-xs text-muted-foreground">g</span>
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
          {saving ? 'Saving...' : 'Save Recipe'}
        </button>
        {status && <span className="text-sm text-destructive">{status}</span>}
      </div>
    </div>
  )
}
