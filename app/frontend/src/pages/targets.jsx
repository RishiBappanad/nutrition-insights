import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Save, CheckCircle } from 'lucide-react'
import { GoalsSection } from '@/components/goals-section'

/**
 * Targets and Goals, unified (2026-09-24): both are rows in the one goals
 * table. Macros get a simple, always-visible editor (fixed calories/protein/
 * carbs/fat, or a calorie+ratio mode where grams are derived server-side).
 * Everything else lives in the Goals section: the DRI micronutrient targets
 * appear there as autofilled, editable, resettable presets grouped by
 * Macros / Vitamins / Minerals, next to the user's own goals (any nutrient,
 * vitals like weight and body fat, weekly/monthly totals, trends against
 * their own history). The old standalone micronutrient card is gone -- it
 * edited the same rows.
 */
export default function Targets() {
  // Saving macro targets rewrites their goals; bump this so the Goals
  // section below re-fetches instead of showing stale amounts.
  const [goalsReloadKey, setGoalsReloadKey] = useState(0)
  // ...and the other direction: editing a macro goal's amount in the Goals
  // section changes what the macro editor should show.
  const [macrosReloadKey, setMacrosReloadKey] = useState(0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Targets & Goals</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Set your daily calorie and macro targets, edit any nutrient or vital goal, and add long-term ones
        </p>
      </div>
      <MacroTargets reloadKey={macrosReloadKey} onSaved={() => setGoalsReloadKey((k) => k + 1)} />
      <GoalsSection reloadKey={goalsReloadKey} onChanged={() => setMacrosReloadKey((k) => k + 1)} />
    </div>
  )
}

function MacroTargets({ onSaved, reloadKey = 0 }) {
  const [mode, setMode] = useState('fixed')
  const [fixed, setFixed] = useState({ calorie_target: '', protein_g: '', carbs_g: '', fat_g: '' })
  const [ratio, setRatio] = useState({ calorie_target: '', protein_pct: '', carbs_pct: '', fat_pct: '' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')

  useEffect(() => {
    api('/targets/macros')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return
        setMode(d.mode)
        if (d.mode === 'fixed') {
          setFixed({ calorie_target: d.calorie_target, protein_g: d.protein_g, carbs_g: d.carbs_g, fat_g: d.fat_g })
        }
      })
      .finally(() => setLoading(false))
  }, [reloadKey])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setStatus('')

    const body = mode === 'fixed'
      ? {
          mode: 'fixed',
          calorie_target: Number(fixed.calorie_target),
          protein_g: Number(fixed.protein_g),
          carbs_g: Number(fixed.carbs_g),
          fat_g: Number(fixed.fat_g),
        }
      : {
          mode: 'ratio',
          calorie_target: Number(ratio.calorie_target),
          protein_pct: Number(ratio.protein_pct),
          carbs_pct: Number(ratio.carbs_pct),
          fat_pct: Number(ratio.fat_pct),
        }

    const res = await api('/targets/macros', { method: 'PUT', body: JSON.stringify(body) })
    setSaving(false)
    if (res.ok) {
      const data = await res.json()
      if (data.mode === 'fixed') {
        setFixed({ calorie_target: data.calorie_target, protein_g: data.protein_g, carbs_g: data.carbs_g, fat_g: data.fat_g })
      }
      setStatus('saved')
      setTimeout(() => setStatus(''), 3000)
      onSaved?.()
    } else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  const ratioSum = (Number(ratio.protein_pct) || 0) + (Number(ratio.carbs_pct) || 0) + (Number(ratio.fat_pct) || 0)

  if (loading) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Macros</CardTitle>
        <CardDescription>
          Fixed values stay constant every day. Ratio mode recalculates grams automatically
          whenever your calorie target changes.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex rounded-md border border-border overflow-hidden text-sm w-fit mb-4">
          <button
            onClick={() => setMode('fixed')}
            className={cn('px-3 py-1.5 font-medium transition-colors', mode === 'fixed' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}
          >
            Fixed Values
          </button>
          <button
            onClick={() => setMode('ratio')}
            className={cn('px-3 py-1.5 font-medium transition-colors', mode === 'ratio' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}
          >
            Macro Ratios
          </button>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          {mode === 'fixed' ? (
            <div className="grid gap-4 md:grid-cols-4">
              <Field label="Calories" value={fixed.calorie_target} onChange={(v) => setFixed({ ...fixed, calorie_target: v })} />
              <Field label="Protein (g)" value={fixed.protein_g} onChange={(v) => setFixed({ ...fixed, protein_g: v })} />
              <Field label="Carbs (g)" value={fixed.carbs_g} onChange={(v) => setFixed({ ...fixed, carbs_g: v })} />
              <Field label="Fat (g)" value={fixed.fat_g} onChange={(v) => setFixed({ ...fixed, fat_g: v })} />
            </div>
          ) : (
            <div className="space-y-3">
              <div className="grid gap-4 md:grid-cols-4">
                <Field label="Calories" value={ratio.calorie_target} onChange={(v) => setRatio({ ...ratio, calorie_target: v })} />
                <Field label="Protein %" value={ratio.protein_pct} onChange={(v) => setRatio({ ...ratio, protein_pct: v })} />
                <Field label="Carbs %" value={ratio.carbs_pct} onChange={(v) => setRatio({ ...ratio, carbs_pct: v })} />
                <Field label="Fat %" value={ratio.fat_pct} onChange={(v) => setRatio({ ...ratio, fat_pct: v })} />
              </div>
              <p className={cn('text-xs', Math.abs(ratioSum - 100) > 0.5 ? 'text-destructive' : 'text-muted-foreground')}>
                Total: {ratioSum}% {Math.abs(ratioSum - 100) > 0.5 && '— percentages must sum to 100'}
              </p>
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              <Save className="h-4 w-4" />
              {saving ? 'Saving...' : 'Save Macro Targets'}
            </button>
            {status === 'saved' && (
              <span className="inline-flex items-center gap-1 text-sm text-green-700">
                <CheckCircle className="h-4 w-4" /> Saved
              </span>
            )}
            {status && status !== 'saved' && <span className="text-sm text-destructive">{status}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function Field({ label, value, onChange }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      <input
        type="number"
        min="0"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      />
    </div>
  )
}
