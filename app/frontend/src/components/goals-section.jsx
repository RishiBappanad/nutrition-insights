import { useEffect, useMemo, useState } from 'react'
import { Target, Plus, Trash2, CheckCircle2, AlertTriangle, X, Pencil, RotateCcw, Scale } from 'lucide-react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'

// The Goals half of the unified Targets page -- mirrors finance-tracker's own
// Goals UI (Basic / Presets / Advanced), on the same cross-tracker Goal Query
// contract (workspace-notes/RECURRING_AND_GOALS_SPEC.md).
//
// What a goal is asserted on, and how it's organized:
//   - a NUTRIENT total (calories or any nutrient), grouped Macros / Vitamins /
//     Minerals / Other by what the nutrient IS -- not by the food's category
//     (fruit, dairy...). Every goal shows its standard unit (g / mg / µg / kcal).
//   - a VITAL (weight, body fat): the latest reading.
// The DRI micronutrient targets live in this same list as autofilled presets:
// edit the amount in place, or reset it to the default. Two goals asserting the
// same thing (two daily protein floors) are refused by the server; this UI
// surfaces that as "you already have this".

const INPUT = 'w-full mt-1 border border-border rounded-md px-3 py-2 text-sm bg-background'
const SMALL_INPUT = 'w-24 border border-border rounded-md px-2 py-1 text-sm bg-background'

const GROUPS = ['Macros', 'Vitamins', 'Minerals', 'Other', 'Vitals']

async function errorMessage(res, fallback) {
  const body = await res.json().catch(() => ({}))
  const detail = body.detail
  if (typeof detail === 'string') return detail
  return detail?.message || fallback
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
  if (n === null || n === undefined) return '—'
  const value = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10
  return unit ? `${value} ${unit}` : `${value}`
}

function termBadge(goal) {
  if (goal.vital) return 'Latest'
  const window = goal.measure_query.timeWindow
  if (goal.reference_query) return 'Trend'
  return window.period === 'daily' ? 'Daily' : window.period === 'weekly' ? 'Weekly' : window.period === 'monthly' ? 'Monthly' : 'Goal'
}

// "a computed baseline" for a same-measure trend; "0.0625 × Calories" when the
// goal is compared against a computation on another measure (a ratio).
function referenceText(goal) {
  const ref = goal.reference_measure
  const scale = goal.reference_scale ?? 1
  if (ref && (ref.label !== goal.measure_label || scale !== 1)) {
    return `${scale === 1 ? '' : `${scale} × `}${ref.label}${ref.unit ? ` (${ref.unit})` : ''}`
  }
  return 'a computed baseline'
}

function goalSubtitle(goal) {
  const mq = goal.measure_query
  if (goal.vital && !goal.reference_query) return 'latest reading'
  const period = mq.timeWindow.period
  if (goal.reference_query) {
    const ref = goal.reference_query
    const baselineDesc =
      ref.timeWindow.kind === 'trailing' ? `trailing ${ref.timeWindow.count}-${periodLabel(ref.timeWindow.period)} ${ref.aggregation}`
      : ref.timeWindow.kind === 'same_period_last_year' ? `same ${periodLabel(ref.timeWindow.period)} ${ref.timeWindow.count === 1 ? 'last year' : `${ref.timeWindow.count} years back`}`
      : ref.timeWindow.kind === 'all_time' ? 'all-time'
      : 'a fixed range'
    return `${mq.aggregation} ${period ? `per ${periodLabel(period)}` : 'overall'}, vs. ${baselineDesc}`
  }
  return `${mq.aggregation} per ${periodLabel(period)}`
}

// ── Goal card ──────────────────────────────────────────────────────────

function GoalCard({ goal, status, onSaveAmount, onSaveScale, onReset, onDelete, onLogVital }) {
  const isWarning = goal.severity === 'warning'
  const tone = status === undefined ? 'muted' : !status.has_data ? 'muted' : status.on_track ? 'ok' : isWarning ? 'warn' : 'bad'
  const iconBg = { muted: 'bg-secondary', ok: 'bg-emerald-500/10', warn: 'bg-amber-500/10', bad: 'bg-destructive/10' }[tone]
  const iconFg = { muted: 'text-muted-foreground', ok: 'text-emerald-600', warn: 'text-amber-600', bad: 'text-destructive' }[tone]
  const barFg = { muted: 'bg-secondary', ok: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-destructive' }[tone]
  const Icon = goal.vital ? Scale : Target

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [reading, setReading] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // A fixed-amount goal edits its amount; a goal compared against a computed
  // value edits the multiplier on it (the ratio).
  const scaleMode = goal.reference_query !== null
  const title = goal.label || goal.measure_label

  async function run(action) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const saveAmount = () => run(async () => {
    const value = Number(draft)
    if (draft === '' || Number.isNaN(value) || value < 0 || (scaleMode && value === 0)) throw new Error(scaleMode ? 'Enter a number above 0' : 'Enter a number')
    await (scaleMode ? onSaveScale(goal, value) : onSaveAmount(goal, value))
    setEditing(false)
  })

  const logReading = () => run(async () => {
    const value = Number(reading)
    if (reading === '' || Number.isNaN(value) || value <= 0) throw new Error('Enter a reading')
    await onLogVital(goal, value)
    setReading('')
  })

  return (
    <div className="bg-card border border-border rounded-lg p-4 relative group">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className={'h-9 w-9 rounded-full flex items-center justify-center shrink-0 ' + iconBg}>
            <Icon className={'h-4 w-4 ' + iconFg} />
          </div>
          <div className="min-w-0">
            <p className="font-medium text-foreground truncate">{title}</p>
            <p className="text-xs text-muted-foreground truncate">
              {goal.group}
              {goal.is_preset && (goal.is_modified ? ' · Customized preset' : ' · Preset')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-border text-muted-foreground">{termBadge(goal)}</span>
          {goal.is_modified && (
            <button
              onClick={() => run(() => onReset(goal))}
              disabled={busy}
              title={`Reset to the default (${formatAmount(goal.preset_amount, goal.unit)})`}
              className="p-1 rounded hover:bg-secondary"
              aria-label="Reset to default"
            >
              <RotateCcw className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
          <button
            onClick={() => run(() => onDelete(goal))}
            title={goal.is_preset ? 'Remove this preset (restore it any time with Reset presets)' : 'Delete goal'}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 rounded hover:bg-secondary"
            aria-label={goal.is_preset ? 'Remove preset' : 'Delete goal'}
          >
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </button>
        </div>
      </div>

      <div className="text-xs text-muted-foreground mt-3 flex items-center flex-wrap gap-x-1.5 gap-y-1">
        {editing ? (
          <>
            <span>{comparatorLabel(goal.comparator, goal.tolerance_percent)}{scaleMode ? ' ×' : ''}</span>
            <input
              type="number" min="0" step="any" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') saveAmount(); if (e.key === 'Escape') setEditing(false) }}
              className={SMALL_INPUT}
            />
            <span>{scaleMode ? (goal.reference_measure?.label ?? '') : goal.unit}</span>
            <button onClick={saveAmount} disabled={busy} className="text-primary font-medium hover:underline">Save</button>
            <button onClick={() => setEditing(false)} className="hover:underline">Cancel</button>
          </>
        ) : (
          <>
            <span>
              {comparatorLabel(goal.comparator, goal.tolerance_percent)}{' '}
              {goal.reference_query ? referenceText(goal) : formatAmount(goal.reference_amount ?? 0, goal.unit)} — {goalSubtitle(goal)}
            </span>
            {(
              <button
                onClick={() => { setDraft(String(scaleMode ? (goal.reference_scale ?? 1) : (goal.reference_amount ?? ''))); setEditing(true); setError(null) }}
                className="p-0.5 rounded hover:bg-secondary" aria-label={scaleMode ? 'Edit multiplier' : 'Edit amount'}
              >
                <Pencil className="h-3 w-3" />
              </button>
            )}
          </>
        )}
      </div>

      {goal.duplicate_of && (
        <p className="text-xs text-amber-600 mt-2 flex items-start gap-1">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px" />
          Duplicate of another goal asserting the same thing{goal.is_preset ? ' — remove one of them' : ' — delete this one'}.
        </p>
      )}
      {error && <p className="text-xs text-destructive mt-2">{error}</p>}

      <div className="mt-3">
        {status === undefined ? (
          <div className="h-2 w-full bg-secondary rounded-full animate-pulse" />
        ) : !status.has_data ? (
          <p className="text-sm text-muted-foreground">No readings yet — log your first below.</p>
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

      {goal.vital && (
        <div className="mt-3 flex items-center gap-2">
          <input
            type="number" min="0" step="0.1" placeholder={`Today's ${goal.measure_label.toLowerCase()}`} value={reading}
            onChange={(e) => setReading(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') logReading() }}
            className={SMALL_INPUT + ' flex-1'}
          />
          <span className="text-xs text-muted-foreground">{goal.unit}</span>
          <button onClick={logReading} disabled={busy} className="text-xs font-medium border border-border rounded-md px-2 py-1 hover:bg-secondary">Log</button>
        </div>
      )}
    </div>
  )
}

// ── Measure picker (category → measure) ────────────────────────────────

function measureKey(m) {
  return m.kind === 'vital' ? `vital:${m.vital}` : (m.field ?? 'calories')
}

// Pick a category (Macros / Vitamins / Minerals / Other / Vitals) and then a
// specific measure inside it. Categories come from what the nutrient is, so
// "Vitamins" narrows the list to vitamins, and every option shows its unit.
function MeasurePicker({ measures, value, onChange }) {
  const current = measures.find((m) => measureKey(m) === value) ?? measures[0]
  const groups = GROUPS.filter((g) => measures.some((m) => m.group === g))
  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        <label className="text-xs text-muted-foreground">Category</label>
        <select
          value={current?.group ?? ''}
          onChange={(e) => onChange(measureKey(measures.find((m) => m.group === e.target.value)))}
          className={INPUT}
        >
          {groups.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </div>
      <div>
        <label className="text-xs text-muted-foreground">Measure</label>
        <select value={measureKey(current ?? {})} onChange={(e) => onChange(e.target.value)} className={INPUT}>
          {measures.filter((m) => m.group === current?.group).map((m) => (
            <option key={measureKey(m)} value={measureKey(m)}>{m.label} ({m.unit})</option>
          ))}
        </select>
      </div>
    </div>
  )
}

// ── New goal modal ─────────────────────────────────────────────────────

const DEFAULT_BASIC = { measure: 'calories', comparator: 'lte', amount: 2000, period: 'daily', severity: 'target' }

const DEFAULT_ADVANCED = {
  measure: 'calories',
  measureAggregation: 'sum',
  measurePercentile: 95,
  period: 'daily',
  comparator: 'lte',
  tolerancePercent: 15,
  referenceMode: 'fixed',
  referenceAmount: 2000,
  refAggregation: 'mean',
  refPercentile: 95,
  // Compare against a computation on ANOTHER measure: '' = the same measure as
  // above (a trend against your own history); a measure key = a ratio between
  // the two. `scale` multiplies the reference (and converts its unit).
  refMeasure: '',
  scale: 1,
  baselineKind: 'trailing',
  trailingCount: 7,
  yearsBackCount: 1,
  fixedStart: '',
  fixedEnd: '',
  severity: 'target',
  notifyOnCrossing: true,
  label: '',
}

// A vital is read as "the latest reading" by default, so its advanced form
// starts on that; a nutrient is a period total.
function advancedDefaultsFor(measure) {
  return measure.startsWith('vital:')
    ? { measureAggregation: 'last', period: 'all_time', referenceAmount: 0 }
    : { measureAggregation: 'sum', period: 'daily' }
}

function buildReferenceTimeWindow(form) {
  const period = form.period === 'all_time' ? 'weekly' : form.period
  switch (form.baselineKind) {
    case 'current_period': return form.period === 'all_time' ? { kind: 'all_time' } : { kind: 'current_period', period }
    case 'trailing': return { kind: 'trailing', period, count: form.trailingCount }
    case 'same_period_last_year': return { kind: 'same_period_last_year', period, count: form.yearsBackCount }
    case 'all_time': return { kind: 'all_time' }
    case 'fixed_range': return { kind: 'fixed_range', start: form.fixedStart, end: form.fixedEnd }
    default: return { kind: 'trailing', period, count: 1 }
  }
}

function measureScope(measure) {
  if (measure.startsWith('vital:')) {
    return {
      filters: [
        { field: 'owner_type', operator: 'eq', value: 'vital' },
        { field: 'category', operator: 'eq', value: measure.slice('vital:'.length) },
      ],
    }
  }
  return { filters: [], ...(measure !== 'calories' ? { measureField: measure } : {}) }
}

function buildAdvancedPayload(form) {
  const scope = measureScope(form.measure)
  const measure_query = {
    aggregation: form.measureAggregation,
    ...(form.measureAggregation === 'percentile' ? { percentile: form.measurePercentile } : {}),
    ...scope,
    timeWindow: form.period === 'all_time' ? { kind: 'all_time' } : { kind: 'current_period', period: form.period },
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
    ...(form.refMeasure ? measureScope(form.refMeasure) : scope),
    timeWindow: buildReferenceTimeWindow(form),
    ...(form.scale !== 1 ? { scale: form.scale } : {}),
  }
  return { ...base, reference_query }
}

// The picker key a stored query measures (inverse of measureScope).
function queryMeasureKey(query) {
  const vital = query?.filters?.find((f) => f.field === 'owner_type' && f.value === 'vital')
  if (vital) return `vital:${query.filters.find((f) => f.field === 'category')?.value}`
  return query?.measureField ?? 'calories'
}

function applyPresetToAdvancedForm(preset) {
  const form = { ...DEFAULT_ADVANCED }
  if (preset.measure_query) form.measure = queryMeasureKey(preset.measure_query)
  form.comparator = preset.comparator
  if (preset.tolerance_percent !== undefined) form.tolerancePercent = preset.tolerance_percent
  if (preset.measure_query) {
    form.measureAggregation = preset.measure_query.aggregation
    if (preset.measure_query.percentile !== undefined) form.measurePercentile = preset.measure_query.percentile
    if (preset.measure_query.timeWindow.period) form.period = preset.measure_query.timeWindow.period
  }
  if (preset.reference_query) {
    form.referenceMode = 'computed'
    const refKey = queryMeasureKey(preset.reference_query)
    form.refMeasure = refKey === form.measure ? '' : refKey
    form.scale = preset.reference_query.scale ?? 1
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

function presetMeasureKey(preset) {
  if (preset.vital) return `vital:${preset.vital}`
  return preset.measure_field ?? 'calories'
}

function NewGoalModal({ onClose, onCreated }) {
  const [tab, setTab] = useState('basic')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [presets, setPresets] = useState(null)
  const [measures, setMeasures] = useState([{ field: null, kind: 'nutrient', group: 'Macros', label: 'Calories', unit: 'kcal' }])
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
      if (!res.ok) throw new Error(await errorMessage(res, 'Could not create goal'))
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  const submitBasic = () => {
    const shared = { comparator: basic.comparator, target_amount: basic.amount, severity: basic.severity }
    if (basic.measure.startsWith('vital:')) return post({ ...shared, vital: basic.measure.slice('vital:'.length) })
    return post({
      ...shared,
      ...(basic.measure !== 'calories' ? { measure_field: basic.measure } : {}),
      period: basic.period,
    })
  }

  const submitAdvanced = () => post(buildAdvancedPayload(advanced))

  function applyPreset(preset) {
    if (preset.measure_query || preset.reference_query) {
      setAdvanced(applyPresetToAdvancedForm(preset))
      setTab('advanced')
    } else {
      setBasic({
        ...DEFAULT_BASIC,
        measure: presetMeasureKey(preset),
        comparator: preset.comparator,
        period: preset.period ?? 'daily',
        amount: preset.amount_hint ?? DEFAULT_BASIC.amount,
      })
      setTab('basic')
    }
    setError(null)
  }

  const unitOf = (key) => measures.find((m) => measureKey(m) === key)?.unit ?? 'kcal'
  const basicIsVital = basic.measure.startsWith('vital:')
  const advancedIsVital = advanced.measure.startsWith('vital:')
  const advancedUnit = unitOf(advanced.measure)
  const refIsVital = (advanced.refMeasure || advanced.measure).startsWith('vital:')
  const labelOf = (key) => measures.find((m) => measureKey(m) === key)?.label ?? key
  const otherMeasureDefault = advanced.measure === 'calories' ? 'nutrient:Protein' : 'calories'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold">New Goal</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary"><X className="h-4 w-4" /></button>
        </div>
        <p className="text-xs text-muted-foreground mb-4">
          Set a goal on calories, any nutrient, or a vital like weight or body fat. Daily is a target; weekly and monthly are long-term goals.
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
            <MeasurePicker measures={measures} value={basic.measure} onChange={(v) => updateBasic({ measure: v })} />
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
                <label className="text-xs text-muted-foreground">Amount ({unitOf(basic.measure)})</label>
                <input type="number" value={basic.amount} onChange={(e) => updateBasic({ amount: Number(e.target.value) })} className={INPUT} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {basicIsVital ? (
                <p className="text-xs text-muted-foreground self-end pb-2">Checked against your latest reading.</p>
              ) : (
                <div>
                  <label className="text-xs text-muted-foreground">Period</label>
                  <select value={basic.period} onChange={(e) => updateBasic({ period: e.target.value })} className={INPUT}>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly (total)</option>
                    <option value="monthly">Monthly (total)</option>
                  </select>
                </div>
              )}
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
            <p className="text-xs text-muted-foreground">
              Pick a starting point — it fills in the form so you can change any of it (amount, comparator, period) before creating.
              Presets you already have are marked; edit those from the goals list instead.
            </p>
            {!presets ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              [['Basic', presets.basic], ['Advanced', presets.advanced]].map(([title, list]) => (
                <div key={title}>
                  <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">{title}</p>
                  <div className="space-y-2">
                    {list.map((p) => {
                      const have = p.existing_goal_id != null
                      return (
                        <button
                          key={p.name}
                          onClick={() => !have && applyPreset(p)}
                          disabled={have}
                          className="w-full text-left flex items-center justify-between p-3 border border-border rounded-md hover:bg-secondary/30 transition-colors disabled:opacity-60 disabled:hover:bg-transparent"
                        >
                          <span>
                            <span className="text-sm font-medium block">{p.name}</span>
                            {p.group && <span className="text-xs text-muted-foreground">{p.group}{p.unit ? ` · ${p.unit}` : ''}</span>}
                          </span>
                          {have ? <span className="text-xs text-muted-foreground">Already added</span> : <Plus className="h-3.5 w-3.5 text-muted-foreground" />}
                        </button>
                      )
                    })}
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
              <MeasurePicker measures={measures} value={advanced.measure} onChange={(v) => updateAdvanced({ measure: v, ...(v.startsWith('vital:') !== advancedIsVital ? advancedDefaultsFor(v) : {}) })} />
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs text-muted-foreground">Aggregation</label>
                  <select value={advanced.measureAggregation} onChange={(e) => updateAdvanced({ measureAggregation: e.target.value })} className={INPUT}>
                    {advancedIsVital ? (
                      <>
                        <option value="last">Latest reading</option>
                        <option value="mean">Average</option>
                        <option value="min">Min</option>
                        <option value="max">Max</option>
                      </>
                    ) : (
                      <>
                        <option value="sum">Sum</option>
                        <option value="mean">Average per entry</option>
                        <option value="median">Median per entry</option>
                        <option value="min">Min</option>
                        <option value="max">Max</option>
                        <option value="count">Count</option>
                        <option value="percentile">Percentile</option>
                      </>
                    )}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">This period</label>
                  <select value={advanced.period} onChange={(e) => updateAdvanced({ period: e.target.value })} className={INPUT}>
                    {advancedIsVital && <option value="all_time">Any time (latest overall)</option>}
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
                  <label className="flex items-center justify-between text-sm">
                    Compare against a different measure (a ratio)
                    <input
                      type="checkbox" checked={advanced.refMeasure !== ''} className="rounded border-border"
                      onChange={(e) => updateAdvanced({ refMeasure: e.target.checked ? otherMeasureDefault : '', scale: 1, ...(e.target.checked ? { baselineKind: 'current_period', refAggregation: 'sum' } : {}) })}
                    />
                  </label>
                  {advanced.refMeasure !== '' && (
                    <>
                      <MeasurePicker measures={measures} value={advanced.refMeasure} onChange={(v) => updateAdvanced({ refMeasure: v, ...(v.startsWith('vital:') ? { baselineKind: 'all_time', refAggregation: 'last' } : {}) })} />
                      <div>
                        <label className="text-xs text-muted-foreground">Multiplier</label>
                        <input type="number" min="0" step="any" value={advanced.scale} onChange={(e) => updateAdvanced({ scale: Number(e.target.value) })} className={INPUT} />
                        <p className="text-xs text-muted-foreground mt-1">
                          {labelOf(advanced.measure)} ({advancedUnit}) {comparatorLabel(advanced.comparator, advanced.tolerancePercent)} {advanced.scale} × {labelOf(advanced.refMeasure)} ({unitOf(advanced.refMeasure)}).
                          The multiplier also converts units — e.g. 25% of calories as protein grams is 0.0625 (25% ÷ 4 kcal per gram).
                        </p>
                      </div>
                    </>
                  )}
                  {advanced.refMeasure === '' && <p className="text-xs text-muted-foreground">Baseline uses the same measure as above.</p>}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Aggregation</label>
                      <select value={advanced.refAggregation} onChange={(e) => updateAdvanced({ refAggregation: e.target.value })} className={INPUT}>
                        {refIsVital && <option value="last">Latest reading</option>}
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
                        <option value="current_period">This same period</option>
                        <option value="trailing">Trailing periods</option>
                        <option value="same_period_last_year">Same period last year</option>
                        <option value="all_time">All-time</option>
                        <option value="fixed_range">Fixed date range</option>
                      </select>
                    </div>
                  </div>
                  {advanced.baselineKind === 'trailing' && (
                    <div>
                      <label className="text-xs text-muted-foreground">Trailing how many {periodLabel(advanced.period === 'all_time' ? 'weekly' : advanced.period)}s</label>
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

export function GoalsSection({ reloadKey = 0, onChanged }) {
  const [goals, setGoals] = useState([])
  // Presets the user removed (kept server-side as inactive rows). Only the DRI
  // ones count here: "Reset presets" restores those, whereas a removed macro
  // target comes back by saving the macro editor again.
  const [removedPresets, setRemovedPresets] = useState([])
  const [statuses, setStatuses] = useState({})
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [tab, setTab] = useState('Macros')
  const [notice, setNotice] = useState(null)

  function loadStatuses() {
    api('/goals/statuses').then((r) => r.json()).then((d) => setStatuses(d.statuses ?? {})).catch(() => {})
  }

  function load() {
    setLoading(true)
    setStatuses({})
    api('/goals?include_system=true').then((r) => r.json()).then((all) => {
      const rows = all.filter((g) => g.is_active)
      setGoals(rows)
      setRemovedPresets(all.filter((g) => !g.is_active && (g.source === 'dri_default' || g.source === 'user_target')))
      setLoading(false)
      loadStatuses()
    }).catch(() => setLoading(false))
  }

  useEffect(load, [reloadKey])

  const replaceGoal = (updated) => setGoals((prev) => prev.map((g) => (g.id === updated.id ? { ...g, ...updated } : g)))

  async function saveAmount(goal, amount) {
    const res = await api(`/goals/${goal.id}`, { method: 'PATCH', body: JSON.stringify({ reference_amount: amount }) })
    if (!res.ok) throw new Error(await errorMessage(res, 'Could not save'))
    replaceGoal(await res.json())
    loadStatuses()
    onChanged?.()
  }

  async function saveScale(goal, scale) {
    const res = await api(`/goals/${goal.id}`, { method: 'PATCH', body: JSON.stringify({ reference_scale: scale }) })
    if (!res.ok) throw new Error(await errorMessage(res, 'Could not save'))
    replaceGoal(await res.json())
    loadStatuses()
    onChanged?.()
  }

  async function resetGoal(goal) {
    const res = await api(`/goals/${goal.id}/reset`, { method: 'POST' })
    if (!res.ok) throw new Error(await errorMessage(res, 'Could not reset'))
    replaceGoal(await res.json())
    loadStatuses()
    onChanged?.()
  }

  async function deleteGoal(goal) {
    const res = await api(`/goals/${goal.id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(await errorMessage(res, 'Could not delete'))
    setGoals((prev) => prev.filter((g) => g.id !== goal.id))
    if (goal.source === 'dri_default' || goal.source === 'user_target') setRemovedPresets((prev) => [...prev, goal])
    onChanged?.()
  }

  async function logVital(goal, value) {
    const res = await api('/vitals', { method: 'POST', body: JSON.stringify({ metric: goal.vital, value }) })
    if (!res.ok) throw new Error(await errorMessage(res, 'Could not log reading'))
    loadStatuses()
  }

  async function resetAll() {
    setNotice(null)
    const res = await api('/goals/presets/reset', { method: 'POST' })
    if (!res.ok) {
      setNotice(await errorMessage(res, 'Could not reset presets'))
      return
    }
    const { reset } = await res.json()
    setNotice(reset ? `Reset ${reset} preset${reset === 1 ? '' : 's'} to their defaults.` : 'Every preset is already at its default.')
    load()
    onChanged?.()
  }

  const counts = useMemo(() => {
    const c = { All: goals.length }
    GROUPS.forEach((g) => { c[g] = goals.filter((x) => x.group === g).length })
    return c
  }, [goals])
  const tabs = ['All', ...GROUPS.filter((g) => counts[g] > 0)]
  const visible = tab === 'All' ? goals : goals.filter((g) => g.group === tab)
  const anyModified = goals.some((g) => g.is_modified)
  const canResetPresets = anyModified || removedPresets.length > 0

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Goals</CardTitle>
            <CardDescription>
              Your nutrient and vital goals, by category. Presets are filled in from your profile — edit any amount, reset it to the default, or remove the ones you don't want. Live progress is for the current period.
            </CardDescription>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            {canResetPresets && (
              <button
                onClick={resetAll}
                className="flex items-center gap-1.5 border border-border rounded-md px-3 py-1.5 text-sm hover:bg-secondary"
                title="Put every customized preset back to its default and bring back any you removed"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Reset presets{removedPresets.length > 0 ? ` (${removedPresets.length} removed)` : ''}
              </button>
            )}
            <button onClick={() => setShowModal(true)} className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-md px-3 py-1.5 text-sm font-medium">
              <Plus className="h-4 w-4" /> New Goal
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {notice && <p className="text-xs text-muted-foreground mb-3">{notice}</p>}
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : goals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No goals yet. Set your profile (age + sex) to autofill nutrient presets, or add your own.</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5 mb-4">
              {tabs.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={'text-sm px-3 py-1 rounded-full border ' + (tab === t ? 'bg-secondary font-medium border-border' : 'border-transparent text-muted-foreground hover:bg-secondary/50')}
                >
                  {t} <span className="text-xs text-muted-foreground">{counts[t]}</span>
                </button>
              ))}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {visible.map((g) => (
                <GoalCard
                  key={g.id} goal={g} status={statuses[g.id]}
                  onSaveAmount={saveAmount} onSaveScale={saveScale} onReset={resetGoal} onDelete={deleteGoal} onLogVital={logVital}
                />
              ))}
            </div>
          </>
        )}
      </CardContent>

      {showModal && <NewGoalModal onClose={() => setShowModal(false)} onCreated={() => { setShowModal(false); load(); onChanged?.() }} />}
    </Card>
  )
}
