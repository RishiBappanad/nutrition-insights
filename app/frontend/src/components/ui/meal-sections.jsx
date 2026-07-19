import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

// Fixed display order for the standard meal times; anything that doesn't
// match one of these exactly falls into "Uncategorized" rather than being
// silently folded into an arbitrary bucket (e.g. the backend's food_log.meal
// column defaults to 'Snack' for entries with no explicit meal — that's a
// storage default, not something this view should treat as a real category
// match for grouping purposes beyond its literal string value).
const MEAL_ORDER = ['Breakfast', 'Lunch', 'Dinner', 'Snack']
const UNCATEGORIZED = 'Uncategorized'

function groupByMeal(entries) {
  const groups = {}
  for (const entry of entries) {
    const meal = MEAL_ORDER.includes(entry.meal) ? entry.meal : UNCATEGORIZED
    if (!groups[meal]) groups[meal] = []
    groups[meal].push(entry)
  }
  return groups
}

/**
 * Diary entries grouped by meal/time-of-day (Breakfast/Lunch/Dinner/Snack),
 * matching Cronometer's diary layout. `entries` is GET /food/log's
 * `entries` array. Sections with no entries are omitted rather than shown
 * empty — an empty "Breakfast" header with nothing under it isn't useful
 * information for a given day.
 */
export function MealSections({ entries }) {
  const groups = groupByMeal(entries || [])
  const orderedMeals = [...MEAL_ORDER, UNCATEGORIZED].filter((m) => groups[m]?.length)

  if (orderedMeals.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          No food logged yet today.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {orderedMeals.map((meal) => {
        const mealEntries = groups[meal]
        const mealCalories = mealEntries.reduce((sum, e) => sum + (e.calories || 0), 0)
        return (
          <Card key={meal}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">{meal}</CardTitle>
              <span className="text-xs text-muted-foreground font-mono">{Math.round(mealCalories)} kcal</span>
            </CardHeader>
            <CardContent className="space-y-2">
              {mealEntries.map((entry) => (
                <div key={entry.id} className="flex items-center justify-between text-sm py-1">
                  <div className="min-w-0">
                    <p className="truncate text-foreground">{entry.food_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {entry.serving_size} {entry.serving_unit}
                    </p>
                  </div>
                  <span className="font-mono text-xs text-muted-foreground flex-shrink-0 ml-3">
                    {Math.round(entry.calories || 0)} kcal
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
