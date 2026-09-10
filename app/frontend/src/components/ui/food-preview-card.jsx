import { useState, useEffect } from 'react'
import { Link } from 'wouter'
import { api } from '@/lib/api'
import { PieChart } from '@/components/ui/pie-chart'
import { NutrientBar } from '@/components/ui/nutrient-bar'
import { usePreferences } from '@/lib/use-preferences'
import { categoryLabel } from '@/lib/food-categories'
import { Settings2 } from 'lucide-react'

// Same 4/4/9 kcal-per-gram convention as macro-card.jsx (and
// app/nutrition_targets.py's KCAL_PER_GRAM server-side) -- standard
// nutrition science, kept in sync intentionally across all three.
const KCAL_PER_GRAM = { protein: 4, carbs: 4, fat: 9 }
const MACRO_NUTRIENT_NAMES = {
  protein: 'Protein',
  carbs: 'Carbohydrate, by difference',
  fat: 'Total lipid (fat)',
}
const ALCOHOL_NUTRIENT_NAME = 'Alcohol, ethyl'
const ALCOHOL_KCAL_PER_GRAM = 7

const SOURCE_LABELS = {
  USDA: 'USDA',
  CNF: 'Canadian Nutrient File',
  recipe: 'Your Recipe',
  meal: 'Your Meal',
  pantry: 'Your Pantry',
}

/**
 * Quick preview/insights for a single food-search result, before
 * committing to log/add it -- what a user asked for after noticing no
 * unified "click any food card" preview existed. Deliberately
 * self-contained (fetches its own preferences + targets rather than
 * taking them as props) so any of the 4 search pages can render it from
 * a single prop (`result`) without extra wiring at each call site.
 *
 * Shows %-of-daily-value bars, not bare numbers -- a raw "2.5mg" means
 * little to most users without a reference point (explicit user
 * feedback: "most users cant tell you what these base values mean").
 * `daily_target` for macros comes from GET /targets/macros (protein_g/
 * carbs_g/fat_g), for micros from GET /targets/nutrients (DRI default or
 * the user's own override, whichever is active) -- the SAME reference
 * values the dashboard's own progress bars use, just interpreted
 * differently: "% of today's cumulative intake" there vs. "% of daily
 * value THIS ONE FOOD alone contributes" here. A nutrient with no target
 * on file (or no macro targets set at all) falls back to a plain value
 * with no bar rather than fabricating a percentage.
 *
 * Three sections, per explicit user request:
 *   1. Macro pie (protein/carbs/fat/alcohol, calories-share) using the
 *      user's own configured dashboard colors (usePreferences().colors,
 *      macro_* keys) -- retaining the user's own colors here, not a
 *      second hardcoded palette, was an explicit requirement. Below the
 *      pie, each macro also gets a %DV bar.
 *   2. Fixed metadata that isn't user-configurable: source, serving
 *      size/unit, food type/category.
 *   3. User-configurable micronutrients, each as a %DV bar -- reuses the
 *      EXACT SAME "important to me" preference already used by the
 *      dashboard's MicronutrientCard (usePreferences().importantNutrients),
 *      not a second parallel list. Scrollable (not a fixed 2-column grid)
 *      since a user can select many nutrients in settings -- a fixed-size
 *      view was truncating them (explicit user feedback).
 */
export function FoodPreviewCard({ result }) {
  const { colors, importantNutrients, loading: preferencesLoading } = usePreferences()
  const [macroTargets, setMacroTargets] = useState(null) // null = none set (404) or not yet loaded
  const [nutrientTargets, setNutrientTargets] = useState({}) // { [name]: { daily_target, unit } }

  useEffect(() => {
    api('/targets/macros').then((r) => (r.ok ? r.json() : null)).then(setMacroTargets)
    api('/targets/nutrients').then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (!d) return
      const byName = {}
      for (const t of d.targets) byName[t.nutrient_name] = t
      setNutrientTargets(byName)
    })
  }, [])

  if (!result) return null
  const nutrients = result.nutrients || {}

  const macroColors = {
    protein: colors.macro_protein,
    carbs: colors.macro_carbs,
    fat: colors.macro_fat,
    alcohol: colors.macro_alcohol,
  }
  const alcoholGrams = nutrients[ALCOHOL_NUTRIENT_NAME]?.value ?? 0
  const macros = [
    { key: 'protein', label: 'Protein', grams: nutrients[MACRO_NUTRIENT_NAMES.protein]?.value ?? 0 },
    { key: 'carbs', label: 'Carbs', grams: nutrients[MACRO_NUTRIENT_NAMES.carbs]?.value ?? 0 },
    { key: 'fat', label: 'Fat', grams: nutrients[MACRO_NUTRIENT_NAMES.fat]?.value ?? 0 },
  ].map((m) => ({ ...m, calories: m.grams * KCAL_PER_GRAM[m.key] }))
  if (alcoholGrams > 0) {
    macros.push({ key: 'alcohol', label: 'Alcohol', grams: alcoholGrams, calories: alcoholGrams * ALCOHOL_KCAL_PER_GRAM })
  }
  const totalCalories = macros.reduce((sum, m) => sum + m.calories, 0)
  const segments = macros.map((m) => ({ label: m.label, value: m.calories, color: macroColors[m.key] }))

  // "Type of food" -- recipe/meal/pantry are their own kind of result
  // (no food_category.py category resolution applies the same way a
  // plain USDA/CNF food's does), everything else falls back to the
  // resolved FoodCategory label when present.
  const typeLabel = result.recipe ? 'Recipe' : result.meal ? 'Meal' : categoryLabel(result.category)

  const microRows = importantNutrients
    .map((name) => ({ name, entry: nutrients[name], target: nutrientTargets[name] }))
    .filter((r) => r.entry != null)

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-4">
      <div>
        <p className="text-sm font-medium text-foreground">{result.name}</p>
        {result.brand && <p className="text-xs text-muted-foreground">{result.brand}</p>}
      </div>

      <div className="flex items-start gap-6">
        <div className="relative flex-shrink-0">
          <PieChart segments={segments} size={100} strokeWidth={16} />
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-sm font-bold font-mono">{Math.round(totalCalories)}</span>
            <span className="text-[9px] text-muted-foreground">kcal</span>
          </div>
        </div>
        <div className="flex-1 space-y-2.5">
          {macros.map((m) => {
            const targetGrams = macroTargets?.[`${m.key}_g`]
            const percent = targetGrams ? (m.grams / targetGrams) * 100 : 0
            const valueLabel = (
              <>
                {Math.round(m.grams)}g
                {targetGrams != null && <span className="text-muted-foreground"> / {Math.round(targetGrams)}g</span>}
              </>
            )
            return (
              <NutrientBar key={m.key} name={m.label} valueLabel={valueLabel} percent={percent} color={macroColors[m.key]} />
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs border-t border-border pt-3">
        <div>
          <p className="text-muted-foreground">Source</p>
          <p className="text-foreground font-medium">{SOURCE_LABELS[result.source] ?? result.source}</p>
        </div>
        <div>
          <p className="text-muted-foreground">Serving</p>
          <p className="text-foreground font-medium">{result.serving_size}{result.serving_unit}</p>
        </div>
        <div>
          <p className="text-muted-foreground">Type</p>
          <p className="text-foreground font-medium">{typeLabel ?? '—'}</p>
        </div>
      </div>

      <div className="border-t border-border pt-3 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">Micronutrients</p>
          <Link href="/profile#micronutrient-settings" title="Customize which micronutrients show here">
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer">
              <Settings2 className="h-3 w-3" />
              Customize
            </span>
          </Link>
        </div>
        {preferencesLoading ? (
          <p className="text-xs text-muted-foreground">Loading...</p>
        ) : microRows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            None of your customized micronutrients are tracked for this food.
          </p>
        ) : (
          <div className="space-y-2.5 max-h-96 overflow-y-auto pr-1">
            {microRows.map(({ name, entry, target }) => {
              const percent = target?.daily_target ? (entry.value / target.daily_target) * 100 : 0
              const valueLabel = (
                <>
                  {Math.round(entry.value * 100) / 100}{entry.unit}
                  {target?.daily_target != null && (
                    <span className="text-muted-foreground"> / {Math.round(target.daily_target)}{target.unit}</span>
                  )}
                </>
              )
              return (
                <NutrientBar key={name} name={name} valueLabel={valueLabel} percent={percent} color={colors.nutrient_sufficient} />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
