import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

/**
 * `progress` is the shape returned by GET /targets/progress:
 * { [nutrient_name]: { unit, daily_target, max_threshold, actual, percent_of_target } }
 * `colors` (nutrient_insufficient/nutrient_sufficient/nutrient_excess) and
 * `sufficiencyThresholdPct` come from usePreferences — both are
 * user-configurable, not hardcoded, so this component never decides on
 * its own what "close to 100%" means or what color that state gets.
 */
function statusFor(entry, sufficiencyThresholdPct) {
  if (entry.max_threshold != null && entry.actual > entry.max_threshold) return 'excess'
  if (entry.percent_of_target != null && entry.percent_of_target >= sufficiencyThresholdPct) return 'sufficient'
  return 'insufficient'
}

export function MicronutrientCard({ progress, colors, sufficiencyThresholdPct }) {
  const statusStyles = {
    insufficient: colors.nutrient_insufficient,
    sufficient: colors.nutrient_sufficient,
    excess: colors.nutrient_excess,
  }

  const entries = Object.entries(progress || {})
    // Nutrients with no established daily_target ("No Target" per DRI
    // convention, e.g. potassium/B12 have no UL) still show if they have
    // a target to measure against; skip ones with neither a target nor
    // any logged amount, since there's nothing meaningful to show.
    .filter(([, v]) => v.daily_target != null || v.actual > 0)
    .sort(([a], [b]) => a.localeCompare(b))

  if (entries.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Micronutrients</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No micronutrient targets set yet. Set up your profile to seed targets from DRI recommendations.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Micronutrients</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {entries.map(([name, entry]) => {
          const status = statusFor(entry, sufficiencyThresholdPct)
          const color = statusStyles[status]
          const pct = entry.percent_of_target ?? 0
          // Cap the visual bar fill at 100% of the track width even when
          // percent_of_target exceeds 100 — the bar shows "how full toward
          // target," the excess color already signals the real overage,
          // a bar rendered at 300% width would just overflow the card.
          const barWidth = Math.min(pct, 100)

          return (
            <div key={name} className="space-y-1">
              <div className="flex items-baseline justify-between text-xs">
                <span className="text-foreground font-medium">{name}</span>
                <span className="font-mono" style={{ color }}>
                  {Math.round(entry.actual)}
                  {entry.unit}
                  {entry.daily_target != null && (
                    <span className="text-muted-foreground"> / {Math.round(entry.daily_target)}{entry.unit}</span>
                  )}
                </span>
              </div>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${barWidth}%`, backgroundColor: color }}
                />
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
