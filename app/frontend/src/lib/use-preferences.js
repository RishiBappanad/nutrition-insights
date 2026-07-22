import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/api'

// Mirrors app/routers/preferences.py's defaults exactly — used only as a
// fallback if the /preferences request itself fails (e.g. offline), so
// the app degrades to the same fixed appearance/behavior it had before
// this feature existed rather than breaking. The backend's response is
// always the source of truth when reachable; this is not a second copy
// of user data, just a last-resort static fallback.
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
const FALLBACK_UNIT_SYSTEM = 'imperial'
const FALLBACK_MACRO_CHART_STYLE = 'pie'
const FALLBACK_IMPORTANT_NUTRIENTS = ['Vitamin B-12', 'Iron, Fe', 'Vitamin D (D2 + D3)']

/**
 * Shared hook for reading + writing user-configurable display
 * preferences: colors, the micronutrient sufficiency threshold, the
 * metric/imperial unit system, and the macro chart style (pie/bar).
 * Backed by a real API (GET/PUT/DELETE /preferences), not localStorage —
 * consistent across devices, matches this project's API-first
 * requirement.
 */
export function usePreferences() {
  const [colors, setColors] = useState(FALLBACK_COLORS)
  const [sufficiencyThresholdPct, setSufficiencyThresholdPct] = useState(FALLBACK_THRESHOLD)
  const [unitSystem, setUnitSystem] = useState(FALLBACK_UNIT_SYSTEM)
  const [macroChartStyle, setMacroChartStyle] = useState(FALLBACK_MACRO_CHART_STYLE)
  const [importantNutrients, setImportantNutrients] = useState(FALLBACK_IMPORTANT_NUTRIENTS)
  const [loading, setLoading] = useState(true)

  function applyResponse(d) {
    setColors(d.colors)
    setSufficiencyThresholdPct(d.sufficiency_threshold_pct)
    setUnitSystem(d.unit_system)
    setMacroChartStyle(d.macro_chart_style)
    if (d.important_nutrients) setImportantNutrients(d.important_nutrients)
  }

  const refresh = useCallback(() => {
    return api('/preferences')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) applyResponse(d)
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
    if (res.ok) applyResponse(await res.json())
    return res
  }

  async function updateThreshold(pct) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ sufficiency_threshold_pct: pct }),
    })
    if (res.ok) applyResponse(await res.json())
    return res
  }

  async function updateUnitSystem(system) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ unit_system: system }),
    })
    if (res.ok) applyResponse(await res.json())
    return res
  }

  async function updateMacroChartStyle(style) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ macro_chart_style: style }),
    })
    if (res.ok) applyResponse(await res.json())
    return res
  }

  async function updateImportantNutrients(nutrientNames) {
    const res = await api('/preferences', {
      method: 'PUT',
      body: JSON.stringify({ important_nutrients: nutrientNames }),
    })
    if (res.ok) applyResponse(await res.json())
    return res
  }

  async function resetToDefaults() {
    const res = await api('/preferences', { method: 'DELETE' })
    if (res.ok) applyResponse(await res.json())
    return res
  }

  return {
    colors,
    sufficiencyThresholdPct,
    unitSystem,
    macroChartStyle,
    importantNutrients,
    loading,
    updateColors,
    updateThreshold,
    updateUnitSystem,
    updateMacroChartStyle,
    updateImportantNutrients,
    resetToDefaults,
    refresh,
  }
}
