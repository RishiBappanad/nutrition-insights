import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { todayIso } from '@/lib/dates'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Plus, Trash2, Flame, Timer, Pencil, X, Save } from 'lucide-react'

/**
 * Manual exercise/activity log — Cronometer's "Exercise" diary tab
 * equivalent: named activities (e.g. "Running", 30 min, 300 kcal), not
 * structured strength-training sets (that's Lifts/lift-insights.jsx,
 * logged via POST /lifts/log). A day can have any number of these.
 */
export default function Exercise() {
  const [date, setDate] = useState(todayIso())
  const [entries, setEntries] = useState([])
  const [totalCalories, setTotalCalories] = useState(0)
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)

  const [activityName, setActivityName] = useState('')
  const [durationMinutes, setDurationMinutes] = useState('')
  const [caloriesBurned, setCaloriesBurned] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function refresh() {
    setLoading(true)
    api(`/exercise?date=${date}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setEntries(d?.entries || [])
        setTotalCalories(d?.total_calories_burned || 0)
      })
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [date])

  async function handleLog(e) {
    e.preventDefault()
    if (!activityName.trim()) {
      setError('Activity name is required')
      return
    }
    setSaving(true)
    setError('')
    const res = await api('/exercise', {
      method: 'POST',
      body: JSON.stringify({
        date,
        activity_name: activityName,
        duration_minutes: durationMinutes ? Number(durationMinutes) : null,
        calories_burned: Number(caloriesBurned) || 0,
        notes: notes || null,
      }),
    })
    setSaving(false)
    if (res.ok) {
      setActivityName('')
      setDurationMinutes('')
      setCaloriesBurned('')
      setNotes('')
      refresh()
    } else {
      const data = await res.json().catch(() => ({}))
      setError(data.detail || `Failed (${res.status})`)
    }
  }

  async function handleDelete(id) {
    await api(`/exercise/${id}`, { method: 'DELETE' })
    refresh()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Exercise</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Log named activities and their calorie burn — separate from your structured lift sets
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Log Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleLog} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-4">
              <div className="space-y-1.5 md:col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Activity</label>
                <input
                  type="text"
                  placeholder="e.g. Running, Cycling, Yoga"
                  value={activityName}
                  onChange={(e) => setActivityName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Duration (min)</label>
                <input
                  type="number"
                  min="0"
                  value={durationMinutes}
                  onChange={(e) => setDurationMinutes(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Calories Burned</label>
                <input
                  type="number"
                  min="0"
                  value={caloriesBurned}
                  onChange={(e) => setCaloriesBurned(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Date</label>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Notes (optional)</label>
                <input
                  type="text"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <Plus className="h-4 w-4" />
                {saving ? 'Logging...' : 'Log Activity'}
              </button>
              {error && <span className="text-sm text-destructive">{error}</span>}
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Activities for {date}</CardTitle>
          <div className="flex items-center gap-1.5 text-sm font-mono text-muted-foreground">
            <Flame className="h-4 w-4" />
            {Math.round(totalCalories)} kcal
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No activities logged for this date yet.</p>
          ) : (
            <div className="space-y-2">
              {entries.map((entry) => (
                <div key={entry.id} className="rounded-md border border-border">
                  <div className="flex items-center justify-between gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-foreground truncate">{entry.activity_name}</p>
                      <p className="text-xs text-muted-foreground flex items-center gap-2">
                        {entry.duration_minutes != null && (
                          <span className="inline-flex items-center gap-1"><Timer className="h-3 w-3" />{entry.duration_minutes} min</span>
                        )}
                        {entry.source !== 'manual' && <span>· {entry.source}</span>}
                        {entry.notes && <span>· {entry.notes}</span>}
                      </p>
                    </div>
                    <span className="text-sm font-mono text-muted-foreground flex-shrink-0">
                      {Math.round(entry.calories_burned)} kcal
                    </span>
                    <button
                      onClick={() => setEditingId(editingId === entry.id ? null : entry.id)}
                      title="Edit"
                      className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors flex-shrink-0"
                    >
                      {editingId === entry.id ? <X className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                    </button>
                    <button
                      onClick={() => handleDelete(entry.id)}
                      title="Delete"
                      className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors flex-shrink-0"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {editingId === entry.id && (
                    <EditEntryForm
                      entry={entry}
                      onDone={() => { setEditingId(null); refresh() }}
                      onCancel={() => setEditingId(null)}
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function EditEntryForm({ entry, onDone, onCancel }) {
  const [activityName, setActivityName] = useState(entry.activity_name)
  const [durationMinutes, setDurationMinutes] = useState(entry.duration_minutes ?? '')
  const [caloriesBurned, setCaloriesBurned] = useState(entry.calories_burned ?? '')
  const [notes, setNotes] = useState(entry.notes ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSave() {
    if (!activityName.trim()) {
      setError('Activity name is required')
      return
    }
    setSaving(true)
    setError('')
    const res = await api(`/exercise/${entry.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        activity_name: activityName,
        duration_minutes: durationMinutes === '' ? null : Number(durationMinutes),
        calories_burned: Number(caloriesBurned) || 0,
        notes: notes || null,
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
    <div className="border-t border-border px-3 py-3 space-y-3 bg-muted/30">
      <div className="grid gap-3 md:grid-cols-4">
        <div className="space-y-1.5 md:col-span-2">
          <label className="text-xs font-medium text-muted-foreground">Activity</label>
          <input
            type="text"
            value={activityName}
            onChange={(e) => setActivityName(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Duration (min)</label>
          <input
            type="number"
            min="0"
            value={durationMinutes}
            onChange={(e) => setDurationMinutes(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Calories Burned</label>
          <input
            type="number"
            min="0"
            value={caloriesBurned}
            onChange={(e) => setCaloriesBurned(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground">Notes (optional)</label>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded-md border border-border text-xs font-medium text-muted-foreground hover:bg-muted transition-colors"
        >
          Cancel
        </button>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </div>
  )
}
