/**
 * Generic labeled progress bar -- extracted from micronutrient-card.jsx's
 * NutrientRow (dashboard sufficiency bars) once a second consumer
 * (food-preview-card.jsx's %-of-daily-value bars) needed the exact same
 * "name, value label, filled bar, one color" shape. Purely
 * presentational: callers compute `percent` and `color` themselves --
 * this component doesn't know or care whether a percent means "% of
 * today's cumulative intake vs. target" (micronutrient-card.jsx) or
 * "% of daily value contributed by this one food" (food-preview-card.jsx).
 */
export function NutrientBar({ name, valueLabel, percent, color }) {
  const barWidth = Math.min(Math.max(percent ?? 0, 0), 100)

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs gap-2">
        <span className="text-foreground font-medium truncate">{name}</span>
        <span className="font-mono flex-shrink-0" style={{ color }}>{valueLabel}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${barWidth}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}
