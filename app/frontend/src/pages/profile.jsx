import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Save, CheckCircle, User } from 'lucide-react'
import { cmToFeetInches, feetInchesToCm, kgToLb, lbToKg } from '@/lib/units'

const ACTIVITY_LEVELS = [
  { value: 'sedentary', label: 'Sedentary (little to no exercise)' },
  { value: 'light', label: 'Light (exercise 1-3 days/week)' },
  { value: 'moderate', label: 'Moderate (exercise 3-5 days/week)' },
  { value: 'active', label: 'Active (exercise 6-7 days/week)' },
  { value: 'very_active', label: 'Very Active (hard exercise daily, or physical job)' },
]

// Account profile: age, sex, height/weight, activity level — feeds
// PUT /profile, which seeds nutrition_targets from the DRI (Dietary
// Reference Intake) tables for this exact age/sex combination, and sets
// a sex-based default daily water goal. Without this, /targets/nutrients
// and /targets/progress have nothing to show (no DRI seed has run yet).
//
// Height/weight are ALWAYS stored as cm/kg on the backend (height_cm,
// weight_kg — see routers/profile.py) regardless of display unit; the
// metric/imperial toggle (from usePreferences, shared with every other
// page) only changes what's rendered in these two fields, converting to
// cm/kg right before the PUT request. This matches the project's
// established "store metric internally, unit preference is UI-only"
// pattern already used for water_target_ml.
export default function Profile() {
  const { unitSystem, updateUnitSystem } = usePreferences()

  const [age, setAge] = useState('')
  const [sex, setSex] = useState('')
  const [heightCm, setHeightCm] = useState(null) // canonical value, always cm
  const [weightKg, setWeightKg] = useState(null) // canonical value, always kg
  const [activityLevel, setActivityLevel] = useState('')

  // Display-only imperial input state (feet/inches, lb) — kept separate
  // from the canonical cm/kg values so switching unit systems doesn't
  // lose precision from repeated round-trip conversions on every keystroke.
  const [heightFeet, setHeightFeet] = useState('')
  const [heightInches, setHeightInches] = useState('')
  const [weightLb, setWeightLb] = useState('')

  const [existing, setExisting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    api('/profile')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) {
          setAge(d.age ?? '')
          setSex(d.sex ?? '')
          setActivityLevel(d.activity_level ?? '')
          setHeightCm(d.height_cm ?? null)
          setWeightKg(d.weight_kg ?? null)
          if (d.height_cm != null) {
            const { feet, inches } = cmToFeetInches(d.height_cm)
            setHeightFeet(String(feet))
            setHeightInches(String(inches))
          }
          if (d.weight_kg != null) {
            setWeightLb(kgToLb(d.weight_kg).toFixed(1))
          }
          setExisting(true)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  function handleHeightCmChange(value) {
    setHeightCm(value === '' ? null : Number(value))
  }

  function handleWeightKgChange(value) {
    setWeightKg(value === '' ? null : Number(value))
  }

  function handleHeightImperialChange(feet, inches) {
    setHeightFeet(feet)
    setHeightInches(inches)
    if (feet === '' && inches === '') {
      setHeightCm(null)
      return
    }
    setHeightCm(Math.round(feetInchesToCm(feet || 0, inches || 0) * 10) / 10)
  }

  function handleWeightLbChange(value) {
    setWeightLb(value)
    setWeightKg(value === '' ? null : Math.round(lbToKg(Number(value)) * 10) / 10)
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setStatus('')
    setResult(null)

    if (!age || !sex) {
      setStatus('Age and sex are required — they determine your DRI (Dietary Reference Intake) targets.')
      setSaving(false)
      return
    }

    const res = await api('/profile', {
      method: 'PUT',
      body: JSON.stringify({
        age: Number(age),
        sex,
        height_cm: heightCm,
        weight_kg: weightKg,
        activity_level: activityLevel || null,
      }),
    })
    setSaving(false)
    if (res.ok) {
      const data = await res.json()
      setResult(data)
      setExisting(true)
      setStatus('saved')
      setTimeout(() => setStatus(''), 4000)
    } else {
      const data = await res.json().catch(() => ({}))
      const detail = data.detail
      setStatus(
        Array.isArray(detail)
          ? detail.map((d) => d.msg).join(', ')
          : detail || `Failed (${res.status})`
      )
    }
  }

  if (loading) return null

  const isMetric = unitSystem === 'metric'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Used to calculate your personalized nutrient targets and water goal
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <User className="h-4 w-4 text-muted-foreground" />
            <CardTitle>Account Profile</CardTitle>
          </div>
          <CardDescription>
            Age and sex determine your Dietary Reference Intake (DRI) targets for every
            tracked macro and micronutrient. Activity level and body stats are optional but
            improve accuracy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSave} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Measurement Units</label>
              <div className="flex rounded-md border border-border overflow-hidden text-sm w-fit">
                <button
                  type="button"
                  onClick={() => updateUnitSystem('imperial')}
                  className={cn('px-3 py-1.5 font-medium transition-colors', !isMetric ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}
                >
                  Imperial (ft/in, lb)
                </button>
                <button
                  type="button"
                  onClick={() => updateUnitSystem('metric')}
                  className={cn('px-3 py-1.5 font-medium transition-colors', isMetric ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}
                >
                  Metric (cm, kg)
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                Applies across the whole app — dashboard water widget, etc.
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Age *</label>
                <input
                  type="number"
                  min="0"
                  max="130"
                  placeholder="30"
                  value={age}
                  onChange={(e) => setAge(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Sex *</label>
                <select
                  value={sex}
                  onChange={(e) => setSex(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="">Select...</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </select>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Height</label>
                {isMetric ? (
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min="0"
                      placeholder="175"
                      value={heightCm ?? ''}
                      onChange={(e) => handleHeightCmChange(e.target.value)}
                      className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span className="text-xs text-muted-foreground flex-shrink-0">cm</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min="0"
                      placeholder="5"
                      value={heightFeet}
                      onChange={(e) => handleHeightImperialChange(e.target.value, heightInches)}
                      className="w-16 px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span className="text-xs text-muted-foreground">ft</span>
                    <input
                      type="number"
                      min="0"
                      max="11"
                      placeholder="9"
                      value={heightInches}
                      onChange={(e) => handleHeightImperialChange(heightFeet, e.target.value)}
                      className="w-16 px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span className="text-xs text-muted-foreground">in</span>
                  </div>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Weight</label>
                <div className="flex items-center gap-2">
                  {isMetric ? (
                    <>
                      <input
                        type="number"
                        min="0"
                        placeholder="70"
                        value={weightKg ?? ''}
                        onChange={(e) => handleWeightKgChange(e.target.value)}
                        className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                      <span className="text-xs text-muted-foreground flex-shrink-0">kg</span>
                    </>
                  ) : (
                    <>
                      <input
                        type="number"
                        min="0"
                        placeholder="155"
                        value={weightLb}
                        onChange={(e) => handleWeightLbChange(e.target.value)}
                        className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                      <span className="text-xs text-muted-foreground flex-shrink-0">lb</span>
                    </>
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Activity Level</label>
              <select
                value={activityLevel}
                onChange={(e) => setActivityLevel(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">Select...</option>
                {ACTIVITY_LEVELS.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-3 pt-2">
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <Save className="h-4 w-4" />
                {saving ? 'Saving...' : existing ? 'Update Profile' : 'Save Profile'}
              </button>
              {status === 'saved' && (
                <span className="inline-flex items-center gap-1 text-sm text-green-700">
                  <CheckCircle className="h-4 w-4" />
                  Saved — {result?.dri_targets_seeded ?? 0} nutrient targets set
                </span>
              )}
              {status && status !== 'saved' && (
                <span className="text-sm text-destructive">{status}</span>
              )}
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
