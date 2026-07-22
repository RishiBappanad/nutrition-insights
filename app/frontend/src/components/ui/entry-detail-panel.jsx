import { useState } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { X, Trash2, Layers } from 'lucide-react'

/**
 * Full nutrient breakdown for one already-logged diary entry — click a
 * row in MealSections to open this. Per explicit user request:
 *   - Works for plain foods, recipes, AND meals (same detail shape,
 *     since food_log rows for all three already carry a full
 *     nutrients_json breakdown).
 *   - "Explode" (convert a combined meal entry back into its per-item
 *     entries, see routers/meals.py's POST /meals/{id}/explode/{log_id})
 *     appears ONLY for source='meal' entries — explicitly NOT for
 *     recipes, which have no explode/combined concept at all (a recipe
 *     always logs as one entry, full stop).
 */
export function EntryDetailPanel({ entry, onClose, onChanged }) {
  const [exploding, setExploding] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')

  const nutrients = Object.entries(entry.nutrients || {}).sort(([a], [b]) => a.localeCompare(b))
  const isMeal = entry.source === 'meal'

  async function handleExplode() {
    setExploding(true)
    setError('')
    const res = await api(`/meals/${entry.source_id}/explode/${entry.id}`, { method: 'POST' })
    setExploding(false)
    if (res.ok) {
      onChanged()
      onClose()
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
  }

  async function handleDelete() {
    setDeleting(true)
    setError('')
    const res = await api(`/food/log/${entry.id}`, { method: 'DELETE' })
    setDeleting(false)
    if (res.ok) {
      onChanged()
      onClose()
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 sm:p-4" onClick={onClose}>
      <div className="w-full sm:max-w-md" onClick={(e) => e.stopPropagation()}>
        <Card className="rounded-b-none sm:rounded-b-lg max-h-[85vh] flex flex-col">
          <CardHeader className="flex flex-row items-start justify-between space-y-0 flex-shrink-0">
            <div>
              <CardTitle>{entry.food_name}</CardTitle>
              <p className="text-xs text-muted-foreground mt-1">
                {entry.meal} · {entry.serving_size} {entry.serving_unit}
                {entry.source && ` · ${entry.source}`}
              </p>
            </div>
            <button onClick={onClose} className="p-1 rounded-md text-muted-foreground hover:bg-muted transition-colors">
              <X className="h-4 w-4" />
            </button>
          </CardHeader>
          <CardContent className="overflow-y-auto flex-1 space-y-4">
            <div className="grid grid-cols-4 gap-2 text-center">
              {[
                { label: 'Calories', value: Math.round(entry.calories || 0), suffix: '' },
                { label: 'Protein', value: Math.round(entry.protein || 0), suffix: 'g' },
                { label: 'Carbs', value: Math.round(entry.carbs || 0), suffix: 'g' },
                { label: 'Fat', value: Math.round(entry.fat || 0), suffix: 'g' },
              ].map((m) => (
                <div key={m.label} className="rounded-md bg-muted py-2">
                  <p className="text-sm font-mono font-semibold">{m.value}{m.suffix}</p>
                  <p className="text-[10px] text-muted-foreground">{m.label}</p>
                </div>
              ))}
            </div>

            {nutrients.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-2">Full Nutrient Breakdown</p>
                <div className="space-y-1.5 max-h-56 overflow-y-auto">
                  {nutrients.map(([name, info]) => (
                    <div key={name} className="flex items-center justify-between text-xs">
                      <span className="text-foreground">{name}</span>
                      <span className="font-mono text-muted-foreground">{info.value}{info.unit}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {error && <p className="text-sm text-destructive">{error}</p>}
          </CardContent>
          <div className="flex items-center gap-2 p-4 pt-0 flex-shrink-0">
            {isMeal && (
              <button
                onClick={handleExplode}
                disabled={exploding || deleting}
                title="Split this meal back into its individual items"
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
              >
                <Layers className="h-3.5 w-3.5" />
                {exploding ? 'Exploding...' : 'Explode into items'}
              </button>
            )}
            <button
              onClick={handleDelete}
              disabled={exploding || deleting}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50 transition-colors ml-auto"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {deleting ? 'Removing...' : 'Remove'}
            </button>
          </div>
        </Card>
      </div>
    </div>
  )
}
