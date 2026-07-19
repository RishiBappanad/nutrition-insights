import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/api'

// Mirrors app/routers/preferences.py's DEFAULT_COLORS exactly — used only
// as a fallback if the /preferences request itself fails (e.g. offline),
// so the app degrades to the same fixed appearance it had before this
// feature existed rather than breaking. The backend's response is always
// the source of truth when reachable; this is not a second copy of user
// data, just a last-resort static fallback.
const FALLBACK_COLORS = {
  macro_protein: '#3b82f6',
  macro_carbs: '#10b981',
  macro_fat: '#f59e0b',
  macro_alcohol: '#8b5cf6',
  nutrient_insufficient: '#6b7280',
  nutrient_sufficient: '#10b981',
  nutrient_excess: '#ef4444',
  chart_line_1: '#2d5344',
  chart_line_2: '#b2804d',
  chart_line_3: '#526d7a',
  lift_scatter: '#2d5344',
}
const FALLBACK_THRESHOLD = 90

/**
 * Shared hook for reading + writing user-configurable display colors and
 * the micronutrient sufficiency threshold. Backed by a real API
 * (GET/PUT/DELETE /preferences), not localStorage — consistent across
 * devices, matches this project's API-first requirement.
 */
export function usePreferences() {
  const [colors, setColors] = useState(FALLBACK_COLORS)
  const [sufficiencyThresholdPct, setSufficiencyThresholdPct] = useState(FALLBACK_THRESHOLD)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    return api('/preferences')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) {
          setColors(d.colors)
          setSufficiencyThresholdPct(d.sufficiency_threshold_pct)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function updateColors(partialColors) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ colors: partialColors }),
    })
    if (res.ok) {
      const data = await res.json()
      setColors(data.colors)
      setSufficiencyThresholdPct(data.sufficiency_threshold_pct)
    }
    return res
  }

  async function updateThreshold(pct) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ sufficiency_threshold_pct: pct }),
    })
    if (res.ok) {
      const data = await res.json()
      setColors(data.colors)
      setSufficiencyThresholdPct(data.sufficiency_threshold_pct)
    }
    return res
  }

  async function resetToDefaults() {
    const res = await api('/preferences', { method: 'DELETE' })
    if (res.ok) {
      const data = await res.json()
      setColors(data.colors)
      setSufficiencyThresholdPct(data.sufficiency_threshold_pct)
    }
    return res
  }

  return { colors, sufficiencyThresholdPct, loading, updateColors, updateThreshold, resetToDefaults, refresh }
}
