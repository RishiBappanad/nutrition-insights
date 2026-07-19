import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Save, CheckCircle, User } from 'lucide-react'

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
export default function Profile() {
  const [form, setForm] = useState({
    age: '',
    sex: '',
    height_cm: '',
    weight_kg: '',
    activity_level: '',
  })
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
          setForm({
            age: d.age ?? '',
            sex: d.sex ?? '',
            height_cm: d.height_cm ?? '',
            weight_kg: d.weight_kg ?? '',
            activity_level: d.activity_level ?? '',
          })
          setExisting(true)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setStatus('')
    setResult(null)

    if (!form.age || !form.sex) {
      setStatus('Age and sex are required — they determine your DRI (Dietary Reference Intake) targets.')
      setSaving(false)
      return
    }

    const res = await api('/profile', {
      method: 'PUT',
      body: JSON.stringify({
        age: Number(form.age),
        sex: form.sex,
        height_cm: form.height_cm ? Number(form.height_cm) : null,
        weight_kg: form.weight_kg ? Number(form.weight_kg) : null,
        activity_level: form.activity_level || null,
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
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Age *</label>
                <input
                  type="number"
                  min="0"
                  max="130"
                  placeholder="30"
                  value={form.age}
                  onChange={(e) => setForm({ ...form, age: e.target.value })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Sex *</label>
                <select
                  value={form.sex}
                  onChange={(e) => setForm({ ...form, sex: e.target.value })}
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
                <label className="text-xs font-medium text-muted-foreground">Height (cm)</label>
                <input
                  type="number"
                  min="0"
                  placeholder="175"
                  value={form.height_cm}
                  onChange={(e) => setForm({ ...form, height_cm: e.target.value })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Weight (kg)</label>
                <input
                  type="number"
                  min="0"
                  placeholder="70"
                  value={form.weight_kg}
                  onChange={(e) => setForm({ ...form, weight_kg: e.target.value })}
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Activity Level</label>
              <select
                value={form.activity_level}
                onChange={(e) => setForm({ ...form, activity_level: e.target.value })}
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
