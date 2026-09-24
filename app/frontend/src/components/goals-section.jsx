import { useEffect, useState } from 'react'
import { Target, Plus, Trash2, CheckCircle2, AlertTriangle, X } from 'lucide-react'
import { api } from '@/lib/api'
import { FOOD_CATEGORIES } from '@/lib/food-categories'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'

// Goals half of the unified Targets page -- mirrors finance-tracker's own
// Goals UI (Basic / Presets / Advanced), on the same cross-tracker Goal Query
// contract (workspace-notes/RECURRING_AND_GOALS_SPEC.md). A goal here
// measures either calories or any single nutrient (`measureField`,
// "nutrient:<Name>"), optionally narrowed to food categories, over a daily /
// weekly / monthly period -- so a daily target and a long-term goal are the
// same thing at different settings, and both live in the one goals table.
// The server restricts every goal to logged consumption (owner_type
// food_log), so nothing here has to.

const NUTRIENT_DISPLAY = {
  'Carbohydrate, by difference': 'Carbohydrates',
  'Total lipid (fat)': 'Fat',
}

function measureName(measureField) {
  if (!measureField) return 'Calories'
  const name = measureField.replace('nutrient:', '')
  return NUTRIENT_DISPLAY[name] ?? name
}

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

function formatAmount(n, unit) {
  const value = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10
  return unit ? `${value} ${unit}` : `${value}`
}

function termBadge(goal) {
  const period = goal.measure_query.timeWindow.period
  if (goal.reference_query) return 'Trend'
  return period === 'daily' ? 'Daily' : period === 'weekly' ? 'Weekly' : period === 'monthly' ? 'Monthly' : 'Goal'
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
  const measure = measureName(goal.measure_query.measureField)
  const category = categoryFromQuery(goal.measure_query)
  const isWarning = goal.severity === 'warning'
  const managed = goal.source === 'macro_target'
  const tone = status === undefined ? 'muted' : status.on_track ? 'ok' : isWarning ? 'warn' : 'bad'
  const iconBg = { muted: 'bg-secondary', ok: 'bg-emerald-500/10', warn: 'bg-amber-500/10', bad: 'bg-destructive/10' }[tone]
  const iconFg = { muted: 'text-muted-foreground', ok: 'text-emerald-600', warn: 'text-amber-600', bad: 'text-destructive' }[tone]
  const barFg = { muted: 'bg-secondary', ok: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-destructive' }[tone]

  return (
    <div className="bg-card border border-border rounded-lg p-5 relative group">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className={'h-9 w-9 rounded-full flex items-center justify-center shrink-0 ' + iconBg}>
            <Target className={'h-4 w-4 ' + iconFg} />
          </div>
          <div className="min-w-0">
            <p className="font-medium text-foreground truncate">{goal.label || measure}</p>
            <p className="text-xs text-muted-foreground truncate">
              {measure} · {category ?? 'all foods'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-border text-muted-foreground">{termBadge(goal)}</span>
          {!managed && (
            <button onClick={() => onDelete(goal.id)} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-secondary" aria-label="Delete goal">
              <Trash2 className="h-3.5 w-3.5 text-destructive" />
            </button>
          )}
        </div>
      </div>

      <p className="text-xs text-muted-foreground mt-3">
        {comparatorLabel(goal.comparator, goal.tolerance_percent)} {goal.reference_query ? 'a computed baseline' : formatAmount(goal.reference_amount ?? 0, goal.unit)} — {goalSubtitle(goal)}
        {managed && ' · managed by the macro targets above'}
      </p>

      <div className="mt-4">
        {status === undefined ? (
          <div className="h-2 w-full bg-secondary rounded-full animate-pulse" />
        ) : (
          <>
            <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
              <div className={'h-full rounded-full ' + barFg} style={{ width: `${Math.min(status.percent, 100)}%` }} />
            </div>
            <div className="flex items-center justify-between mt-2">
              <span className="text-sm font-mono">
                {formatAmount(status.measure_value, goal.unit)} <span className="text-muted-foreground">/ {formatAmount(status.reference_value, goal.unit)}</span>
              </span>
              <span className={'text-xs font-medium flex items-center gap-1 ' + iconFg}>
                {status.on_track ? (<><CheckCircle2 className="h-3.5 w-3.5" /> On track</>) : (<><AlertTriangle className="h-3.5 w-3.5" /> {isWarning ? 'Off track' : 'Not met'}</>)}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Form pieces ────────────────────────────────────────────────────────

const INPUT = 'w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background'

// FOOD_CATEGORIES is a fixed 10-item enum, so a checkbox list covers
// multi-select without a searchable combobox.
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

function MeasureSelect({ measures, value, onChange }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
      {measures.map((m) => (
        <option key={m.field ?? 'calories'} value={m.field ?? ''}>{m.label} ({m.unit})</option>
      ))}
    </select>
  )
}

function unitFor(measures, field) {
  return measures.find((m) => (m.field ?? '') === field)?.unit ?? 'cal'
}

// ── New goal modal ─────────────────────────────────────────────────────

const DEFAULT_BASIC = { measure: '', category: '', comparator: 'lte', amount: 2000, period: 'daily', severity: 'target' }

const DEFAULT_ADVANCED = {
  measure: '',
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
  const measureField = form.measure ? { measureField: form.measure } : {}
  const measure_query = {
    aggregation: form.measureAggregation,
    ...(form.measureAggregation === 'percentile' ? { percentile: form.measurePercentile } : {}),
    filters: categoryFilters(form.measureCategories),
    timeWindow: { kind: 'current_period', period: form.period },
    ...measureField,
  }

  const base = {
    label: form.label || null,
    severity: form.severity,
    comparator: form.comparator,
    measure_query,
    notify_on_crossing: form.notifyOnCrossing,
    ...(form.comparator === 'within_tolerance_percent' ? { tolerance_percent: form.tolerancePercent } : {}),
  }

  if (form.referenceMode === 'fixed') return { ...base, reference_amount: form.referenceAmount }

  const reference_query = {
    aggregation: form.refAggregation,
    ...(form.refAggregation === 'percentile' ? { percentile: form.refPercentile } : {}),
    filters: categoryFilters(form.measureCategories),
    timeWindow: buildReferenceTimeWindow(form),
    ...measureField,
  }
  return { ...base, reference_query }
}

function applyPresetToAdvancedForm(preset, category) {
  const form = { ...DEFAULT_ADVANCED, measureCategories: category ? [category] : [] }
  form.comparator = preset.comparator
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
  }
  return form
}

function NewGoalModal({ onClose, onCreated }) {
  const [tab, setTab] = useState('basic')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [presets, setPresets] = useState(null)
  const [measures, setMeasures] = useState([{ field: null, label: 'Calories', unit: 'cal' }])
  const [presetCategory, setPresetCategory] = useState('')
  const [basic, setBasic] = useState(DEFAULT_BASIC)
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED)

  useEffect(() => {
    api('/goals/presets').then((r) => r.json()).then(setPresets).catch(() => setPresets({ basic: [], advanced: [] }))
    api('/goals/measures').then((r) => r.json()).then((d) => setMeasures(d.measures)).catch(() => {})
  }, [])

  const updateBasic = (patch) => setBasic((prev) => ({ ...prev, ...patch }))
  const updateAdvanced = (patch) => setAdvanced((prev) => ({ ...prev, ...patch }))

  async function post(payload) {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api('/goals', { method: 'POST', body: JSON.stringify(payload) })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not create goal')
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  const submitBasic = () =>
    post({
      ...(basic.measure ? { measure_field: basic.measure } : {}),
      ...(basic.category ? { category: basic.category } : {}),
      comparator: basic.comparator,
      target_amount: basic.amount,
      period: basic.period,
      severity: basic.severity,
    })

  const submitAdvanced = () => post(buildAdvancedPayload(advanced))

  function applyPreset(preset) {
    if (preset.measure_query || preset.reference_query) {
      setAdvanced(applyPresetToAdvancedForm(preset, presetCategory))
      setTab('advanced')
    } else {
      setBasic({
        ...DEFAULT_BASIC,
        measure: preset.measure_field ?? '',
        category: presetCategory,
        comparator: preset.comparator,
        period: preset.period ?? 'daily',
        amount: preset.amount_hint ?? DEFAULT_BASIC.amount,
      })
      setTab('basic')
    }
    setError(null)
  }

  const basicUnit = unitFor(measures, basic.measure)
  const advancedUnit = unitFor(measures, advanced.measure)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold">New Goal</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary"><X className="h-4 w-4" /></button>
        </div>
        <p className="text-xs text-muted-foreground mb-4">
          Track calories or any nutrient against a fixed amount or your own history. Daily is a target; weekly and monthly are long-term goals.
        </p>

        <div className="flex gap-1 mb-4 border border-border rounded-md p-1">
          {['basic', 'presets', 'advanced'].map((t) => (
            <button key={t} onClick={() => setTab(t)} className={'flex-1 text-sm py-1.5 rounded capitalize ' + (tab === t ? 'bg-secondary font-medium' : 'text-muted-foreground hover:bg-secondary/50')}>
              {t}
            </button>
          ))}
        </div>

        {error && <p className="text-sm text-destructive mb-3">{error}</p>}

        {tab === 'basic' && (
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">Measure</label>
              <MeasureSelect measures={measures} value={basic.measure} onChange={(v) => updateBasic({ measure: v })} />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Food category (optional)</label>
              <select value={basic.category} onChange={(e) => updateBasic({ category: e.target.value })} className={INPUT}>
                <option value="">All foods</option>
                {FOOD_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Comparator</label>
                <select value={basic.comparator} onChange={(e) => updateBasic({ comparator: e.target.value })} className={INPUT}>
                  <option value="lte">At most</option>
                  <option value="gte">At least</option>
                  <option value="eq">Exactly</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Amount ({basicUnit})</label>
                <input type="number" value={basic.amount} onChange={(e) => updateBasic({ amount: Number(e.target.value) })} className={INPUT} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Period</label>
                <select value={basic.period} onChange={(e) => updateBasic({ period: e.target.value })} className={INPUT}>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly (total)</option>
                  <option value="monthly">Monthly (total)</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Severity</label>
                <select value={basic.severity} onChange={(e) => updateBasic({ severity: e.target.value })} className={INPUT}>
                  <option value="target">Target (hard)</option>
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
              <label className="text-xs text-muted-foreground">Limit to a food category (optional)</label>
              <select value={presetCategory} onChange={(e) => setPresetCategory(e.target.value)} className={INPUT}>
                <option value="">All foods</option>
                {FOOD_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            {!presets ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              [['Basic', presets.basic], ['Advanced', presets.advanced]].map(([title, list]) => (
                <div key={title}>
                  <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">{title}</p>
                  <div className="space-y-2">
                    {list.map((p) => (
                      <button key={p.name} onClick={() => applyPreset(p)} className="w-full text-left flex items-center justify-between p-3 border border-border rounded-md hover:bg-secondary/30 transition-colors">
                        <span className="text-sm font-medium">{p.name}</span>
                        <Plus className="h-3.5 w-3.5 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === 'advanced' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs text-muted-foreground">Label (optional)</label>
              <input value={advanced.label} onChange={(e) => updateAdvanced({ label: e.target.value })} className={INPUT} />
            </div>

            <div className="border border-border rounded-md p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">What's being measured</p>
              <label className="text-xs text-muted-foreground">Measure</label>
              <MeasureSelect measures={measures} value={advanced.measure} onChange={(v) => updateAdvanced({ measure: v })} />
              <label className="text-xs text-muted-foreground mt-3 block">Food categories (pick several to combine — none means all foods)</label>
              <div className="mt-1">
                <CategoryCheckboxes selected={advanced.measureCategories} onChange={(v) => updateAdvanced({ measureCategories: v })} />
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs text-muted-foreground">Aggregation</label>
                  <select value={advanced.measureAggregation} onChange={(e) => updateAdvanced({ measureAggregation: e.target.value })} className={INPUT}>
                    <option value="sum">Sum</option>
                    <option value="mean">Average per entry</option>
                    <option value="median">Median per entry</option>
                    <option value="min">Min</option>
                    <option value="max">Max</option>
                    <option value="count">Count</option>
                    <option value="percentile">Percentile</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">This period</label>
                  <select value={advanced.period} onChange={(e) => updateAdvanced({ period: e.target.value })} className={INPUT}>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </select>
                </div>
              </div>
              {advanced.measureAggregation === 'percentile' && (
                <div className="mt-3">
                  <label className="text-xs text-muted-foreground">Percentile</label>
                  <input type="number" min="0" max="100" value={advanced.measurePercentile} onChange={(e) => updateAdvanced({ measurePercentile: Number(e.target.value) })} className={INPUT} />
                </div>
              )}
            </div>

            <div className="border border-border rounded-md p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Compared against</p>
              <div className="flex gap-1 border border-border rounded-md p-1 mb-3">
                {[['fixed', 'Fixed amount'], ['computed', 'Computed baseline']].map(([mode, label]) => (
                  <button key={mode} onClick={() => updateAdvanced({ referenceMode: mode })} className={'flex-1 text-sm py-1.5 rounded ' + (advanced.referenceMode === mode ? 'bg-secondary font-medium' : 'text-muted-foreground')}>{label}</button>
                ))}
              </div>

              {advanced.referenceMode === 'fixed' ? (
                <div>
                  <label className="text-xs text-muted-foreground">Amount ({advancedUnit})</label>
                  <input type="number" value={advanced.referenceAmount} onChange={(e) => updateAdvanced({ referenceAmount: Number(e.target.value) })} className={INPUT} />
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-muted-foreground">Baseline uses the same measure and food categories as above.</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Aggregation</label>
                      <select value={advanced.refAggregation} onChange={(e) => updateAdvanced({ refAggregation: e.target.value })} className={INPUT}>
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
                      <select value={advanced.baselineKind} onChange={(e) => updateAdvanced({ baselineKind: e.target.value })} className={INPUT}>
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
                      <input type="number" min="1" value={advanced.trailingCount} onChange={(e) => updateAdvanced({ trailingCount: Number(e.target.value) })} className={INPUT} />
                    </div>
                  )}
                  {advanced.baselineKind === 'same_period_last_year' && (
                    <div>
                      <label className="text-xs text-muted-foreground">How many years back</label>
                      <input type="number" min="1" value={advanced.yearsBackCount} onChange={(e) => updateAdvanced({ yearsBackCount: Number(e.target.value) })} className={INPUT} />
                    </div>
                  )}
                  {advanced.baselineKind === 'fixed_range' && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">Start</label>
                        <input type="date" value={advanced.fixedStart} onChange={(e) => updateAdvanced({ fixedStart: e.target.value })} className={INPUT} />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">End</label>
                        <input type="date" value={advanced.fixedEnd} onChange={(e) => updateAdvanced({ fixedEnd: e.target.value })} className={INPUT} />
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs text-muted-foreground">Comparator</label>
                  <select value={advanced.comparator} onChange={(e) => updateAdvanced({ comparator: e.target.value })} className={INPUT}>
                    <option value="lte">At most</option>
                    <option value="gte">At least</option>
                    <option value="eq">Exactly</option>
                    <option value="within_tolerance_percent">Within a % band</option>
                  </select>
                </div>
                {advanced.comparator === 'within_tolerance_percent' && (
                  <div>
                    <label className="text-xs text-muted-foreground">Tolerance ±%</label>
                    <input type="number" value={advanced.tolerancePercent} onChange={(e) => updateAdvanced({ tolerancePercent: Number(e.target.value) })} className={INPUT} />
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between">
              <label className="text-xs text-muted-foreground">Severity</label>
              <select value={advanced.severity} onChange={(e) => updateAdvanced({ severity: e.target.value })} className="border border-border rounded-md px-3 py-2 text-sm bg-background">
                <option value="target">Target (hard)</option>
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

// ── Section ────────────────────────────────────────────────────────────

export function GoalsSection({ reloadKey = 0 }) {
  const [goals, setGoals] = useState([])
  const [statuses, setStatuses] = useState({})
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)

  function load() {
    setLoading(true)
    setStatuses({})
    api('/goals?active=true').then((r) => r.json()).then((rows) => {
      setGoals(rows)
      setLoading(false)
      rows.forEach((g) => {
        api(`/goals/${g.id}/status`).then((r) => r.json()).then((s) => setStatuses((prev) => ({ ...prev, [g.id]: s }))).catch(() => {})
      })
    }).catch(() => setLoading(false))
  }

  useEffect(load, [reloadKey])

  async function handleDelete(id) {
    setGoals((prev) => prev.filter((g) => g.id !== id))
    await api(`/goals/${id}`, { method: 'DELETE' }).catch(() => {})
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Goals</CardTitle>
            <CardDescription>
              Daily targets and long-term goals for calories or any nutrient — fixed amounts or compared against your own history. Live progress is for the current period.
            </CardDescription>
          </div>
          <button onClick={() => setShowModal(true)} className="shrink-0 flex items-center gap-1.5 bg-primary text-primary-foreground rounded-md px-3 py-1.5 text-sm font-medium">
            <Plus className="h-4 w-4" /> New Goal
          </button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : goals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No goals yet.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {goals.map((g) => <GoalCard key={g.id} goal={g} status={statuses[g.id]} onDelete={handleDelete} />)}
          </div>
        )}
      </CardContent>

      {showModal && <NewGoalModal onClose={() => setShowModal(false)} onCreated={() => { setShowModal(false); load() }} />}
    </Card>
  )
}
