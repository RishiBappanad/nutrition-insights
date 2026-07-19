import { useState } from 'react'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Save, CheckCircle, RotateCcw } from 'lucide-react'

const COLOR_GROUPS = [
  {
    title: 'Daily Macros (Dashboard)',
    fields: [
      { key: 'macro_protein', label: 'Protein' },
      { key: 'macro_carbs', label: 'Carbs' },
      { key: 'macro_fat', label: 'Fat' },
      { key: 'macro_alcohol', label: 'Alcohol' },
    ],
  },
  {
    title: 'Micronutrient Status (Dashboard)',
    fields: [
      { key: 'nutrient_insufficient', label: 'Not yet met' },
      { key: 'nutrient_sufficient', label: 'Sufficient' },
      { key: 'nutrient_excess', label: 'Exceeds upper limit' },
    ],
  },
  {
    title: 'Metric Trend Lines (Charts)',
    fields: [
      { key: 'chart_line_1', label: 'Line 1' },
      { key: 'chart_line_2', label: 'Line 2' },
      { key: 'chart_line_3', label: 'Line 3' },
    ],
  },
  {
    title: 'Lift Insights',
    fields: [{ key: 'lift_scatter', label: 'Scatter points' }],
  },
]

function AppearanceSettings() {
  const { colors, sufficiencyThresholdPct, loading, updateColors, updateThreshold, resetToDefaults } = usePreferences()
  const [status, setStatus] = useState('')

  async function handleColorChange(key, value) {
    const res = await updateColors({ [key]: value })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      setStatus(typeof data.detail === 'string' ? data.detail : `Failed (${res.status})`)
    }
  }

  async function handleThresholdChange(value) {
    const pct = Number(value)
    if (Number.isNaN(pct)) return
    await updateThreshold(pct)
  }

  async function handleReset() {
    await resetToDefaults()
    setStatus('reset')
    setTimeout(() => setStatus(''), 2000)
  }

  if (loading) {
    return null
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>
            Customize the colors used across your dashboard, charts, and lift insights
          </CardDescription>
        </div>
        <button
          onClick={handleReset}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-muted-foreground hover:bg-muted transition-colors"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset to defaults
        </button>
      </CardHeader>
      <CardContent className="space-y-6">
        {COLOR_GROUPS.map((group) => (
          <div key={group.title} className="space-y-3">
            <h3 className="text-sm font-semibold text-foreground">{group.title}</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {group.fields.map((field) => (
                <div key={field.key} className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-border">
                  <span className="text-sm text-foreground">{field.label}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-muted-foreground">{colors[field.key]}</span>
                    <input
                      type="color"
                      value={colors[field.key]}
                      onChange={(e) => handleColorChange(field.key, e.target.value)}
                      className="h-8 w-8 rounded cursor-pointer border border-border bg-transparent"
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}

        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">Micronutrient sufficiency threshold</h3>
          <p className="text-xs text-muted-foreground">
            A micronutrient is shown as "sufficient" once it reaches this percentage of its daily target.
          </p>
          <div className="flex items-center gap-3 max-w-xs">
            <input
              type="range"
              min="50"
              max="100"
              step="1"
              value={sufficiencyThresholdPct}
              onChange={(e) => handleThresholdChange(e.target.value)}
              className="flex-1"
            />
            <span className="text-sm font-mono text-foreground w-12 text-right">{sufficiencyThresholdPct}%</span>
          </div>
        </div>

        {status === 'reset' && (
          <span className="inline-flex items-center gap-1 text-sm text-green-700">
            <CheckCircle className="h-4 w-4" />
            Reset to defaults
          </span>
        )}
        {status && status !== 'reset' && (
          <span className="text-sm text-destructive">{status}</span>
        )}
      </CardContent>
    </Card>
  )
}

export default function Settings() {
  const [form, setForm] = useState({
    hevy_username: '',
    hevy_password: '',
    cronometer_username: '',
    cronometer_password: '',
  })
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setStatus('')
    const res = await api('/auth/credentials', {
      method: 'POST',
      body: JSON.stringify(form),
    })
    setSaving(false)
    if (res.ok) {
      setStatus('saved')
      setTimeout(() => setStatus(''), 3000)
    } else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Manage your connected accounts
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Connected Accounts</CardTitle>
          <CardDescription>
            Add your Cronometer and Hevy credentials to sync data
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSave} className="space-y-6">
            {/* Cronometer Section */}
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-foreground">Cronometer</h3>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Username / Email
                  </label>
                  <input
                    type="text"
                    placeholder="cronometer@example.com"
                    value={form.cronometer_username}
                    onChange={(e) =>
                      setForm({ ...form, cronometer_username: e.target.value })
                    }
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Password
                  </label>
                  <input
                    type="password"
                    placeholder="••••••••"
                    value={form.cronometer_password}
                    onChange={(e) =>
                      setForm({ ...form, cronometer_password: e.target.value })
                    }
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              </div>
            </div>

            {/* Hevy Section */}
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-foreground">Hevy</h3>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Username / Email
                  </label>
                  <input
                    type="text"
                    placeholder="hevy@example.com"
                    value={form.hevy_username}
                    onChange={(e) =>
                      setForm({ ...form, hevy_username: e.target.value })
                    }
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Password
                  </label>
                  <input
                    type="password"
                    placeholder="••••••••"
                    value={form.hevy_password}
                    onChange={(e) =>
                      setForm({ ...form, hevy_password: e.target.value })
                    }
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              </div>
            </div>

            {/* Submit */}
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <Save className="h-4 w-4" />
                {saving ? 'Saving...' : 'Save Credentials'}
              </button>
              {status === 'saved' && (
                <span className="inline-flex items-center gap-1 text-sm text-green-700">
                  <CheckCircle className="h-4 w-4" />
                  Saved!
                </span>
              )}
              {status && status !== 'saved' && (
                <span className="text-sm text-destructive">{status}</span>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      <AppearanceSettings />
    </div>
  )
}
