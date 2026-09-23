import { useEffect, useState } from 'react'
import { Target, Plus, Trash2, CheckCircle2, AlertTriangle, X } from 'lucide-react'
import { api } from '@/lib/api'
import { FOOD_CATEGORIES } from '@/lib/food-categories'

// ── Helpers -- mirrors finance-tracker's own goals/index.tsx, which
// mirrors workspace-notes/RECURRING_AND_GOALS_SPEC.md's Goal Query shape
// (the cross-tracker standard every tracker's Goals implementation
// follows). `amount` on every domain_events row here is calories (this
// tracker's one universal per-entry number, see app/goal_query.py's
// module doc) -- the nutrition equivalent of finance's dollars. Richer
// macro-based goals (protein/carbs grams) aren't expressible yet: those
// live in nutrient_facts, not on the domain_events row itself, and the
// Goal Query model only aggregates over `amount` -- a known, accepted
// limitation, not a bug, same category as finance's own flat-category-
// vs-receipt-item-splitting note in RECURRING_AND_GOALS_SPEC.md.

function categoryFromQuery(query) {
  const filter = query?.filters?.find((f) => f.field === 'category' && (f.operator === 'eq' || f.operator === 'in'))
  if (!filter) return null
  const label = (v) => FOOD_CATEGORIES.find((c) => c.value === v)?.label ?? v
  if (Array.isArray(filter.value)) return filter.value.map(label).join(' + ')
  return typeof filter.value === 'string' ? label(filter.value) : null
}

function comparatorLabel(comparator, tolerancePercent) {
  switch (comparator) {
    case 'lte': return 'at most'
    case 'gte': return 'at least'
    case 'eq': return 'exactly'
    case 'within_tolerance_percent': return `within ±${tolerancePercent ?? 0}% of`
    default: return comparator
  }
}

function periodLabel(period) {
  switch (period) {
    case 'daily': return 'day'
    case 'weekly': return 'week'
    case 'monthly': return 'month'
    default: return 'period'
  }
}

function formatCalories(n) {
  return `${Math.round(n)} cal`
}

function goalSubtitle(goal) {
  const period = goal.measure_query.timeWindow.period
  if (goal.reference_query) {
    const ref = goal.reference_query
    const baselineDesc =
      ref.timeWindow.kind === 'trailing' ? `trailing ${ref.timeWindow.count}-${periodLabel(ref.timeWindow.period)} ${ref.aggregation}`
      : ref.timeWindow.kind === 'same_period_last_year' ? `same ${periodLabel(ref.timeWindow.period)} ${ref.timeWindow.count === 1 ? 'last year' : `${ref.timeWindow.count} years back`}`
      : ref.timeWindow.kind === 'all_time' ? 'all-time'
      : 'a fixed range'
    return `${goal.measure_query.aggregation} per ${periodLabel(period)}, vs. ${baselineDesc}`
  }
  return `${goal.measure_query.aggregation} per ${periodLabel(period)}`
}

function categoryFilters(categories) {
  if (categories.length === 0) return []
  if (categories.length === 1) return [{ field: 'category', operator: 'eq', value: categories[0] }]
  return [{ field: 'category', operator: 'in', value: categories }]
}

// ── Goal card ──────────────────────────────────────────────────────────

function GoalCard({ goal, status, onDelete }) {
  const category = categoryFromQuery(goal.measure_query) ?? 'Every category'
  const isWarning = goal.severity === 'warning'

  return (
    <div className="bg-card border border-border rounded-lg p-5 relative group">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div
            className={
              'h-9 w-9 rounded-full flex items-center justify-center shrink-0 ' +
              (status === undefined ? 'bg-secondary' : status.on_track ? 'bg-emerald-500/10' : isWarning ? 'bg-amber-500/10' : 'bg-destructive/10')
            }
          >
            <Target className={'h-4 w-4 ' + (status === undefined ? 'text-muted-foreground' : status.on_track ? 'text-emerald-600' : isWarning ? 'text-amber-600' : 'text-destructive')} />
          </div>
          <div className="min-w-0">
            <p className="font-medium text-foreground truncate">{goal.label || category}</p>
            <p className="text-xs text-muted-foreground truncate">{category}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-border text-muted-foreground">{isWarning ? 'Warning' : 'Target'}</span>
          <button onClick={() => onDelete(goal.id)} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-secondary" aria-label="Delete goal">
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground mt-3">
        {comparatorLabel(goal.comparator, goal.tolerance_percent)} {goal.reference_query ? 'a computed baseline' : formatCalories(goal.reference_amount ?? 0)} — {goalSubtitle(goal)}
      </p>

      <div className="mt-4">
        {status === undefined ? (
          <div className="h-2 w-full bg-secondary rounded-full animate-pulse" />
        ) : (
          <>
            <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
              <div className={'h-full rounded-full ' + (status.on_track ? 'bg-emerald-500' : isWarning ? 'bg-amber-500' : 'bg-destructive')} style={{ width: `${Math.min(status.percent, 100)}%` }} />
            </div>
            <div className="flex items-center justify-between mt-2">
              <span className="text-sm font-mono">
                {formatCalories(status.measure_value)} <span className="text-muted-foreground">/ {formatCalories(status.reference_value)}</span>
              </span>
              <span className={'text-xs font-medium flex items-center gap-1 ' + (status.on_track ? 'text-emerald-600' : isWarning ? 'text-amber-600' : 'text-destructive')}>
                {status.on_track ? (<><CheckCircle2 className="h-3.5 w-3.5" /> On track</>) : (<><AlertTriangle className="h-3.5 w-3.5" /> {isWarning ? 'Off track' : 'Exceeded'}</>)}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Category checkbox list -- FOOD_CATEGORIES is a fixed 10-item enum
// (unlike finance's user-extensible spending categories), so a plain
// checkbox list covers multi-select without needing a searchable
// combobox component this codebase doesn't have.

function CategoryCheckboxes({ selected, onChange }) {
  function toggle(value) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  }
  return (
    <div className="grid grid-cols-2 gap-1.5 max-h-40 overflow-y-auto border border-border rounded-md p-2">
      {FOOD_CATEGORIES.map((c) => (
        <label key={c.value} className="flex items-center gap-1.5 text-sm cursor-pointer">
          <input type="checkbox" checked={selected.includes(c.value)} onChange={() => toggle(c.value)} className="rounded border-border" />
          {c.label}
        </label>
      ))}
    </div>
  )
}

// ── New Goal modal ─────────────────────────────────────────────────────

const DEFAULT_ADVANCED = {
  measureCategories: [],
  measureAggregation: 'sum',
  measurePercentile: 95,
  period: 'daily',
  comparator: 'lte',
  tolerancePercent: 15,
  referenceMode: 'fixed',
  referenceAmount: 2000,
  refAggregation: 'mean',
  refPercentile: 95,
  baselineKind: 'trailing',
  trailingCount: 7,
  yearsBackCount: 1,
  fixedStart: '',
  fixedEnd: '',
  severity: 'target',
  notifyOnCrossing: true,
  label: '',
}

function buildReferenceTimeWindow(form) {
  switch (form.baselineKind) {
    case 'trailing': return { kind: 'trailing', period: form.period, count: form.trailingCount }
    case 'same_period_last_year': return { kind: 'same_period_last_year', period: form.period, count: form.yearsBackCount }
    case 'all_time': return { kind: 'all_time' }
    case 'fixed_range': return { kind: 'fixed_range', start: form.fixedStart, end: form.fixedEnd }
    default: return { kind: 'trailing', period: form.period, count: 1 }
  }
}

function buildAdvancedPayload(form) {
  const measure_query = {
    aggregation: form.measureAggregation,
    ...(form.measureAggregation === 'percentile' ? { percentile: form.measurePercentile } : {}),
    filters: categoryFilters(form.measureCategories),
    timeWindow: { kind: 'current_period', period: form.period },
  }

  const base = {
    label: form.label || null,
    severity: form.severity,
    comparator: form.comparator,
    measure_query,
    notify_on_crossing: form.notifyOnCrossing,
    ...(form.comparator === 'within_tolerance_percent' ? { tolerance_percent: form.tolerancePercent } : {}),
  }

  if (form.referenceMode === 'fixed') {
    return { ...base, reference_amount: form.referenceAmount }
  }

  const reference_query = {
    aggregation: form.refAggregation,
    ...(form.refAggregation === 'percentile' ? { percentile: form.refPercentile } : {}),
    filters: categoryFilters(form.measureCategories),
    timeWindow: buildReferenceTimeWindow(form),
  }
  return { ...base, reference_query }
}

function applyPresetToAdvancedForm(preset, category) {
  const form = { ...DEFAULT_ADVANCED, measureCategories: category ? [category] : [] }
  form.comparator = preset.comparator
  form.severity = 'target'
  if (preset.tolerance_percent !== undefined) form.tolerancePercent = preset.tolerance_percent
  if (preset.measure_query) {
    form.measureAggregation = preset.measure_query.aggregation
    if (preset.measure_query.percentile !== undefined) form.measurePercentile = preset.measure_query.percentile
    if (preset.measure_query.timeWindow.period) form.period = preset.measure_query.timeWindow.period
  }
  if (preset.reference_query) {
    form.referenceMode = 'computed'
    form.refAggregation = preset.reference_query.aggregation
    if (preset.reference_query.percentile !== undefined) form.refPercentile = preset.reference_query.percentile
    const tw = preset.reference_query.timeWindow
    form.baselineKind = tw.kind
    if (tw.count !== undefined) {
      if (tw.kind === 'trailing') form.trailingCount = tw.count
      if (tw.kind === 'same_period_last_year') form.yearsBackCount = tw.count
    }
  } else {
    form.referenceMode = 'fixed'
  }
  return form
}

function NewGoalModal({ onClose, onCreated }) {
  const [tab, setTab] = useState('basic')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [presets, setPresets] = useState(null)
  const [presetCategory, setPresetCategory] = useState('')

  // Basic tab state
  const [basicCategory, setBasicCategory] = useState('')
  const [basicComparator, setBasicComparator] = useState('lte')
  const [basicAmount, setBasicAmount] = useState(2000)
  const [basicPeriod, setBasicPeriod] = useState('daily')
  const [basicSeverity, setBasicSeverity] = useState('target')

  // Advanced tab state
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED)

  useEffect(() => {
    api('/goals/presets').then((r) => r.json()).then(setPresets).catch(() => setPresets({ basic: [], advanced: [] }))
  }, [])

  function updateAdvanced(patch) {
    setAdvanced((prev) => ({ ...prev, ...patch }))
  }

  async function submitBasic() {
    if (!basicCategory) return setError('Pick a category first.')
    setSubmitting(true)
    setError(null)
    try {
      const res = await api('/goals', {
        method: 'POST',
        body: JSON.stringify({ category: basicCategory, comparator: basicComparator, target_amount: basicAmount, period: basicPeriod, severity: basicSeverity }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not create goal')
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function submitAdvanced() {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api('/goals', { method: 'POST', body: JSON.stringify(buildAdvancedPayload(advanced)) })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not create goal')
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  function applyPreset(preset) {
    if (!presetCategory) return setError('Pick a category first.')
    if (preset.measure_query || preset.reference_query) {
      setAdvanced(applyPresetToAdvancedForm(preset, presetCategory))
      setTab('advanced')
    } else {
      setBasicCategory(presetCategory)
      setBasicComparator(preset.comparator === 'gte' ? 'gte' : 'lte')
      setBasicPeriod(preset.period ?? 'daily')
      setTab('basic')
    }
    setError(null)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold">New Goal</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary"><X className="h-4 w-4" /></button>
        </div>
        <p className="text-xs text-muted-foreground mb-4">Compare your calories against a hardcoded amount or a computed historical baseline.</p>

        <div className="flex gap-1 mb-4 border border-border rounded-md p-1">
          {['basic', 'presets', 'advanced'].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={'flex-1 text-sm py-1.5 rounded capitalize ' + (tab === t ? 'bg-secondary font-medium' : 'text-muted-foreground hover:bg-secondary/50')}
            >
              {t}
            </button>
          ))}
        </div>

        {error && <p className="text-sm text-destructive mb-3">{error}</p>}

        {tab === 'basic' && (
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">Category</label>
              <select value={basicCategory} onChange={(e) => setBasicCategory(e.target.value)} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                <option value="">Choose a category</option>
                {FOOD_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Comparator</label>
                <select value={basicComparator} onChange={(e) => setBasicComparator(e.target.value)} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                  <option value="lte">At most</option>
                  <option value="gte">At least</option>
                  <option value="eq">Exactly</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Calories</label>
                <input type="number" value={basicAmount} onChange={(e) => setBasicAmount(Number(e.target.value))} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Period</label>
                <select value={basicPeriod} onChange={(e) => setBasicPeriod(e.target.value)} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Severity</label>
                <select value={basicSeverity} onChange={(e) => setBasicSeverity(e.target.value)} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                  <option value="target">Target (hard cap)</option>
                  <option value="warning">Warning (soft)</option>
                </select>
              </div>
            </div>
            <button onClick={submitBasic} disabled={submitting} className="w-full bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium mt-2 disabled:opacity-60">
              {submitting ? 'Creating…' : 'Create Goal'}
            </button>
          </div>
        )}

        {tab === 'presets' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs text-muted-foreground">Apply to category</label>
              <select value={presetCategory} onChange={(e) => setPresetCategory(e.target.value)} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                <option value="">Choose a category</option>
                {FOOD_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            {!presets ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">Basic</p>
                  <div className="space-y-2">
                    {presets.basic.map((p) => (
                      <button key={p.name} onClick={() => applyPreset(p)} className="w-full text-left flex items-center justify-between p-3 border border-border rounded-md hover:bg-secondary/30 transition-colors">
                        <span className="text-sm font-medium">{p.name}</span>
                        <Plus className="h-3.5 w-3.5 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">Advanced</p>
                  <div className="space-y-2">
                    {presets.advanced.map((p) => (
                      <button key={p.name} onClick={() => applyPreset(p)} className="w-full text-left flex items-center justify-between p-3 border border-border rounded-md hover:bg-secondary/30 transition-colors">
                        <span className="text-sm font-medium">{p.name}</span>
                        <Plus className="h-3.5 w-3.5 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {tab === 'advanced' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs text-muted-foreground">Label (optional)</label>
              <input value={advanced.label} onChange={(e) => updateAdvanced({ label: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
            </div>

            <div className="border border-border rounded-md p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">What's being measured</p>
              <label className="text-xs text-muted-foreground">Categories (pick several to cap them combined — none picked means every category)</label>
              <div className="mt-1">
                <CategoryCheckboxes selected={advanced.measureCategories} onChange={(v) => updateAdvanced({ measureCategories: v })} />
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs text-muted-foreground">Aggregation</label>
                  <select value={advanced.measureAggregation} onChange={(e) => updateAdvanced({ measureAggregation: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                    <option value="sum">Sum</option>
                    <option value="mean">Average</option>
                    <option value="median">Median</option>
                    <option value="min">Min</option>
                    <option value="max">Max</option>
                    <option value="count">Count</option>
                    <option value="percentile">Percentile</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">This period</label>
                  <select value={advanced.period} onChange={(e) => updateAdvanced({ period: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </select>
                </div>
              </div>
              {advanced.measureAggregation === 'percentile' && (
                <div className="mt-3">
                  <label className="text-xs text-muted-foreground">Percentile</label>
                  <input type="number" min="0" max="100" value={advanced.measurePercentile} onChange={(e) => updateAdvanced({ measurePercentile: Number(e.target.value) })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                </div>
              )}
            </div>

            <div className="border border-border rounded-md p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Compared against</p>
              <div className="flex gap-1 border border-border rounded-md p-1 mb-3">
                <button onClick={() => updateAdvanced({ referenceMode: 'fixed' })} className={'flex-1 text-sm py-1.5 rounded ' + (advanced.referenceMode === 'fixed' ? 'bg-secondary font-medium' : 'text-muted-foreground')}>Fixed amount</button>
                <button onClick={() => updateAdvanced({ referenceMode: 'computed' })} className={'flex-1 text-sm py-1.5 rounded ' + (advanced.referenceMode === 'computed' ? 'bg-secondary font-medium' : 'text-muted-foreground')}>Computed baseline</button>
              </div>

              {advanced.referenceMode === 'fixed' ? (
                <div>
                  <label className="text-xs text-muted-foreground">Calories</label>
                  <input type="number" value={advanced.referenceAmount} onChange={(e) => updateAdvanced({ referenceAmount: Number(e.target.value) })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-muted-foreground">Baseline is computed from the same categories selected above.</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Aggregation</label>
                      <select value={advanced.refAggregation} onChange={(e) => updateAdvanced({ refAggregation: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                        <option value="sum">Sum</option>
                        <option value="mean">Average</option>
                        <option value="median">Median</option>
                        <option value="min">Min</option>
                        <option value="max">Max</option>
                        <option value="percentile">Percentile</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Baseline</label>
                      <select value={advanced.baselineKind} onChange={(e) => updateAdvanced({ baselineKind: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                        <option value="trailing">Trailing periods</option>
                        <option value="same_period_last_year">Same period last year</option>
                        <option value="all_time">All-time</option>
                        <option value="fixed_range">Fixed date range</option>
                      </select>
                    </div>
                  </div>
                  {advanced.baselineKind === 'trailing' && (
                    <div>
                      <label className="text-xs text-muted-foreground">Trailing how many {periodLabel(advanced.period)}s</label>
                      <input type="number" min="1" value={advanced.trailingCount} onChange={(e) => updateAdvanced({ trailingCount: Number(e.target.value) })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                    </div>
                  )}
                  {advanced.baselineKind === 'same_period_last_year' && (
                    <div>
                      <label className="text-xs text-muted-foreground">How many years back</label>
                      <input type="number" min="1" value={advanced.yearsBackCount} onChange={(e) => updateAdvanced({ yearsBackCount: Number(e.target.value) })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                    </div>
                  )}
                  {advanced.baselineKind === 'fixed_range' && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">Start</label>
                        <input type="date" value={advanced.fixedStart} onChange={(e) => updateAdvanced({ fixedStart: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">End</label>
                        <input type="date" value={advanced.fixedEnd} onChange={(e) => updateAdvanced({ fixedEnd: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs text-muted-foreground">Comparator</label>
                  <select value={advanced.comparator} onChange={(e) => updateAdvanced({ comparator: e.target.value })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background">
                    <option value="lte">At most</option>
                    <option value="gte">At least</option>
                    <option value="eq">Exactly</option>
                    <option value="within_tolerance_percent">Within a % band</option>
                  </select>
                </div>
                {advanced.comparator === 'within_tolerance_percent' && (
                  <div>
                    <label className="text-xs text-muted-foreground">Tolerance ±%</label>
                    <input type="number" value={advanced.tolerancePercent} onChange={(e) => updateAdvanced({ tolerancePercent: Number(e.target.value) })} className="w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background" />
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between">
              <label className="text-xs text-muted-foreground">Severity</label>
              <select value={advanced.severity} onChange={(e) => updateAdvanced({ severity: e.target.value })} className="border border-border rounded-md px-3 py-2 text-sm bg-background">
                <option value="target">Target (hard cap)</option>
                <option value="warning">Warning (soft)</option>
              </select>
            </div>

            <label className="flex items-center justify-between text-sm">
              Notify when this goal crosses
              <input type="checkbox" checked={advanced.notifyOnCrossing} onChange={(e) => updateAdvanced({ notifyOnCrossing: e.target.checked })} className="rounded border-border" />
            </label>

            <button onClick={submitAdvanced} disabled={submitting} className="w-full bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium disabled:opacity-60">
              {submitting ? 'Creating…' : 'Create Goal'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────

export default function Goals() {
  const [goals, setGoals] = useState([])
  const [statuses, setStatuses] = useState({})
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)

  function load() {
    setLoading(true)
    api('/goals?active=true').then((r) => r.json()).then((rows) => {
      setGoals(rows)
      setLoading(false)
      rows.forEach((g) => {
        api(`/goals/${g.id}/status`).then((r) => r.json()).then((s) => setStatuses((prev) => ({ ...prev, [g.id]: s }))).catch(() => {})
      })
    }).catch(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleDelete(id) {
    setGoals((prev) => prev.filter((g) => g.id !== id))
    await api(`/goals/${id}`, { method: 'DELETE' }).catch(() => {})
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold">Goals</h1>
        <button onClick={() => setShowModal(true)} className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-md px-3 py-1.5 text-sm font-medium">
          <Plus className="h-4 w-4" /> New Goal
        </button>
      </div>
      <p className="text-muted-foreground mb-6 text-sm">Set a target, hardcoded or computed from your own history, and get flagged the moment it's crossed.</p>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : goals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active goals yet.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {goals.map((g) => (
            <GoalCard key={g.id} goal={g} status={statuses[g.id]} onDelete={handleDelete} />
          ))}
        </div>
      )}

      {showModal && (
        <NewGoalModal
          onClose={() => setShowModal(false)}
          onCreated={() => { setShowModal(false); load() }}
        />
      )}
    </div>
  )
}
