import { useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { PieChart } from '@/components/ui/pie-chart'
import { cn } from '@/lib/utils'
import { PieChart as PieChartIcon, BarChart3 } from 'lucide-react'

// Same 4/4/9 kcal-per-gram convention already used server-side in
// app/nutrition_targets.py's KCAL_PER_GRAM — kept in sync intentionally,
// this is standard nutrition science, not a value that should ever
// diverge between backend and frontend.
const KCAL_PER_GRAM = { protein: 4, carbs: 4, fat: 9 }

// Alcohol isn't one of the 3 macro columns on food_log (calories/protein/
// carbs/fat/fiber) — it comes through as a regular USDA/CNF nutrient
// ("Alcohol, ethyl", grams) in food_log_nutrients, the same way any other
// micronutrient does. 7 kcal/g is the standard Atwater factor (matches
// Cronometer's own handling — alcohol shows as its own breakdown category
// in their daily report, not folded into any of the 3 macros). Shown as
// a 4th pie segment only when actually present in the day's nutrient
// totals — never fabricated or assumed to be 0 vs. "not tracked."
const ALCOHOL_NUTRIENT_NAME = 'Alcohol, ethyl'
const ALCOHOL_KCAL_PER_GRAM = 7

/**
 * Daily macros as a pie chart (calories-from-each-macro), with a
 * calories/grams toggle for the legend values — matches Cronometer's
 * macro summary card. `totals` is the day's logged macro totals
 * (calories/protein/carbs/fat from GET /food/log), `target` is optional
 * (from GET /targets/macros) for a "X / Y" comparison under the chart.
 * `nutrientTotals` is GET /food/log's `nutrient_totals` — used only to
 * pull out alcohol grams, since alcohol isn't one of food_log's 5
 * hardcoded macro columns.
 * `colors` is the user's configured segment colors (from usePreferences,
 * keys macro_protein/macro_carbs/macro_fat/macro_alcohol) — required,
 * not optional, so this component never silently falls back to its own
 * hardcoded palette; the one source of truth for "what color is
 * protein" is the /preferences response (or its documented fallback),
 * not two different defaults living in two files.
 * `chartStyle` ('pie' | 'bar') and `onChartStyleChange` let the user
 * switch between the pie chart and a set of progress bars (one per
 * macro, filled toward its target) — both views show the same
 * underlying data, this is purely a display preference, persisted via
 * usePreferences so it's consistent across visits/devices.
 */
export function MacroCard({ totals, target, nutrientTotals, colors, chartStyle = 'pie', onChartStyleChange }) {
  const [unit, setUnit] = useState('calories') // 'calories' | 'grams'

  const macroColors = {
    protein: colors.macro_protein,
    carbs: colors.macro_carbs,
    fat: colors.macro_fat,
    alcohol: colors.macro_alcohol,
  }

  const alcoholGrams = nutrientTotals?.[ALCOHOL_NUTRIENT_NAME]?.value ?? 0

  const macros = [
    { key: 'protein', label: 'Protein', grams: totals?.protein ?? 0 },
    { key: 'carbs', label: 'Carbs', grams: totals?.carbs ?? 0 },
    { key: 'fat', label: 'Fat', grams: totals?.fat ?? 0 },
  ].map((m) => ({ ...m, calories: m.grams * KCAL_PER_GRAM[m.key] }))

  // Alcohol only appears as a segment/legend row when actually logged —
  // an always-present "Alcohol: 0g" row would be visual noise for the
  // overwhelming majority of days/users who don't drink.
  if (alcoholGrams > 0) {
    macros.push({
      key: 'alcohol',
      label: 'Alcohol',
      grams: alcoholGrams,
      calories: alcoholGrams * ALCOHOL_KCAL_PER_GRAM,
    })
  }

  const totalCalories = totals?.calories != null
    ? totals.calories + alcoholGrams * ALCOHOL_KCAL_PER_GRAM
    : macros.reduce((sum, m) => sum + m.calories, 0)

  const segments = macros.map((m) => ({
    label: m.label,
    value: m.calories,
    color: macroColors[m.key],
  }))

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Daily Macros</CardTitle>
        <div className="flex items-center gap-2">
          {onChartStyleChange && (
            <div className="flex rounded-md border border-border overflow-hidden text-xs">
              <button
                onClick={() => onChartStyleChange('pie')}
                title="Pie chart"
                className={cn(
                  'p-1.5 transition-colors',
                  chartStyle === 'pie' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
                )}
              >
                <PieChartIcon className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => onChartStyleChange('bar')}
                title="Progress bars"
                className={cn(
                  'p-1.5 transition-colors',
                  chartStyle === 'bar' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
                )}
              >
                <BarChart3 className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <div className="flex rounded-md border border-border overflow-hidden text-xs">
            <button
              onClick={() => setUnit('calories')}
              className={cn(
                'px-2.5 py-1 font-medium transition-colors',
                unit === 'calories' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
              )}
            >
              kcal
            </button>
            <button
              onClick={() => setUnit('grams')}
              className={cn(
                'px-2.5 py-1 font-medium transition-colors',
                unit === 'grams' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
              )}
            >
              grams
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {chartStyle === 'bar' ? (
          <div className="space-y-3">
            <div className="flex items-baseline justify-between mb-1">
              <span className="text-xl font-bold font-mono">{Math.round(totalCalories)} kcal</span>
              {target?.calorie_target ? (
                <span className="text-xs text-muted-foreground">of {Math.round(target.calorie_target)} kcal</span>
              ) : null}
            </div>
            {macros.map((m) => {
              const targetGrams = target?.[`${m.key}_g`]
              const targetCalories = targetGrams != null ? targetGrams * (KCAL_PER_GRAM[m.key] ?? ALCOHOL_KCAL_PER_GRAM) : null
              const pct = targetCalories ? Math.min((m.calories / targetCalories) * 100, 100) : 0
              const value = unit === 'calories' ? Math.round(m.calories) : Math.round(m.grams)
              const suffix = unit === 'calories' ? 'kcal' : 'g'
              return (
                <div key={m.key} className="space-y-1">
                  <div className="flex items-baseline justify-between text-xs">
                    <span className="text-foreground font-medium">{m.label}</span>
                    <span className="font-mono text-muted-foreground">
                      {value}{suffix}
                      {targetGrams != null && unit === 'grams' && (
                        <span className="text-muted-foreground/60"> / {Math.round(targetGrams)}g</span>
                      )}
                    </span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${targetCalories ? pct : 0}%`, backgroundColor: macroColors[m.key] }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="flex items-center gap-6">
            <div className="relative flex-shrink-0">
              <PieChart segments={segments} size={140} strokeWidth={24} />
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-xl font-bold font-mono">{Math.round(totalCalories)}</span>
                <span className="text-[10px] text-muted-foreground">kcal</span>
                {target?.calorie_target ? (
                  <span className="text-[10px] text-muted-foreground">of {Math.round(target.calorie_target)}</span>
                ) : null}
              </div>
            </div>

            <div className="flex-1 space-y-2.5">
              {macros.map((m) => {
                const targetGrams = target?.[`${m.key}_g`]
                const value = unit === 'calories' ? Math.round(m.calories) : Math.round(m.grams)
                const suffix = unit === 'calories' ? 'kcal' : 'g'
                return (
                  <div key={m.key} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: macroColors[m.key] }}
                      />
                      <span className="text-foreground">{m.label}</span>
                    </div>
                    <span className="font-mono text-muted-foreground">
                      {value}
                      {suffix}
                      {targetGrams != null && unit === 'grams' && (
                        <span className="text-muted-foreground/60"> / {Math.round(targetGrams)}g</span>
                      )}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
