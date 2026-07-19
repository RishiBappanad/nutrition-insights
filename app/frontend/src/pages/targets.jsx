import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Save, CheckCircle, ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'

/**
 * Macro + micronutrient target settings. Macros get a simple,
 * always-visible editor (fixed calories/protein/carbs/fat, or a
 * calorie+ratio mode where grams are derived server-side). Micronutrients
 * get a collapsed "Advanced" section — the full DRI-seeded list, with a
 * per-nutrient override toggle — matching Cronometer's actual UX split
 * (see nutrition-diary-design.md) rather than putting ~25 nutrient
 * fields on the main screen.
 */
export default function Targets() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Nutrition Targets</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Set your daily calorie and macro goals, and fine-tune individual micronutrients
        </p>
      </div>
      <MacroTargets />
      <MicronutrientTargets />
    </div>
  )
}

function MacroTargets() {
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
  }, [])

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

function MicronutrientTargets() {
  const [expanded, setExpanded] = useState(false)
  const [targets, setTargets] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [edits, setEdits] = useState({})
  const [savingKey, setSavingKey] = useState(null)
  const [status, setStatus] = useState('')

  function load() {
    if (loaded) return
    setLoading(true)
    api('/targets/nutrients')
      .then((r) => r.json())
      .then((d) => {
        setTargets(d.targets || [])
        setLoaded(true)
      })
      .finally(() => setLoading(false))
  }

  function toggleExpanded() {
    setExpanded((e) => !e)
    if (!expanded) load()
  }

  async function handleOverride(nutrient) {
    const draft = edits[nutrient.nutrient_name]
    const dailyTarget = draft?.daily_target !== undefined ? Number(draft.daily_target) : nutrient.daily_target
    const maxThreshold = draft?.max_threshold !== undefined ? Number(draft.max_threshold) : nutrient.max_threshold

    setSavingKey(nutrient.nutrient_name)
    const res = await api(`/targets/nutrients/${encodeURIComponent(nutrient.nutrient_name)}`, {
      method: 'PUT',
      body: JSON.stringify({
        nutrient_name: nutrient.nutrient_name,
        daily_target: dailyTarget,
        max_threshold: maxThreshold,
        is_custom: true,
      }),
    })
    setSavingKey(null)
    if (res.ok) {
      setTargets((prev) => prev.map((t) =>
        t.nutrient_name === nutrient.nutrient_name
          ? { ...t, daily_target: dailyTarget, max_threshold: maxThreshold, is_custom: true }
          : t
      ))
      setStatus('saved')
      setTimeout(() => setStatus(''), 2000)
    } else {
      setStatus(`Failed to update ${nutrient.nutrient_name}`)
    }
  }

  async function handleRevertToDRI(nutrient) {
    setSavingKey(nutrient.nutrient_name)
    const res = await api(`/targets/nutrients/${encodeURIComponent(nutrient.nutrient_name)}`, {
      method: 'PUT',
      body: JSON.stringify({
        nutrient_name: nutrient.nutrient_name,
        daily_target: nutrient.daily_target,
        max_threshold: nutrient.max_threshold,
        is_custom: false,
      }),
    })
    setSavingKey(null)
    if (res.ok) {
      setTargets((prev) => prev.map((t) =>
        t.nutrient_name === nutrient.nutrient_name ? { ...t, is_custom: false } : t
      ))
    }
  }

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={toggleExpanded}>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Advanced: Micronutrients</CardTitle>
            <CardDescription>
              Every tracked nutrient defaults to its DRI-recommended amount — override any of
              them individually
            </CardDescription>
          </div>
          {expanded ? <ChevronDown className="h-5 w-5 text-muted-foreground" /> : <ChevronRight className="h-5 w-5 text-muted-foreground" />}
        </div>
      </CardHeader>
      {expanded && (
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : targets.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No micronutrient targets yet — set up your profile (age + sex) to seed DRI-based
              defaults.
            </p>
          ) : (
            <div className="space-y-2">
              {targets.map((t) => {
                const draft = edits[t.nutrient_name] || {}
                return (
                  <div key={t.nutrient_name} className="flex items-center gap-3 px-3 py-2 rounded-md border border-border">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground truncate">{t.nutrient_name}</p>
                      <p className="text-xs text-muted-foreground">
                        {t.is_custom ? 'Custom' : 'DRI default'} · {t.unit}
                      </p>
                    </div>
                    <input
                      type="number"
                      placeholder="Target"
                      defaultValue={t.daily_target ?? ''}
                      onChange={(e) => setEdits({ ...edits, [t.nutrient_name]: { ...draft, daily_target: e.target.value } })}
                      className="w-24 px-2 py-1.5 rounded-md border bg-background text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <input
                      type="number"
                      placeholder="Max"
                      defaultValue={t.max_threshold ?? ''}
                      onChange={(e) => setEdits({ ...edits, [t.nutrient_name]: { ...draft, max_threshold: e.target.value } })}
                      className="w-24 px-2 py-1.5 rounded-md border bg-background text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <button
                      onClick={() => handleOverride(t)}
                      disabled={savingKey === t.nutrient_name}
                      className="px-2.5 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
                    >
                      Save
                    </button>
                    {t.is_custom && (
                      <button
                        onClick={() => handleRevertToDRI(t)}
                        disabled={savingKey === t.nutrient_name}
                        title="Revert to DRI default"
                        className="p-1.5 rounded-md border border-border text-muted-foreground hover:bg-muted transition-colors"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {status && (
            <p className={cn('text-xs mt-3', status === 'saved' ? 'text-green-700' : 'text-destructive')}>
              {status === 'saved' ? 'Saved' : status}
            </p>
          )}
        </CardContent>
      )}
    </Card>
  )
}
