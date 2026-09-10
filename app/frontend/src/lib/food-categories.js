// Mirrors app/food_category.py's FoodCategory enum -- kept as plain
// display labels here since this is just presentation data, not logic
// (the actual resolution -- auto-compute vs. override -- happens once,
// server-side, via food_category.resolve_category()). Previously
// duplicated only in recipes.jsx's own Category dropdown; extracted here
// once a second consumer (food-preview-card.jsx) needed the same list.
export const FOOD_CATEGORIES = [
  { value: 'produce', label: 'Produce' },
  { value: 'protein', label: 'Protein' },
  { value: 'dairy', label: 'Dairy' },
  { value: 'grains_starches', label: 'Grains & Starches' },
  { value: 'legumes_nuts', label: 'Legumes & Nuts' },
  { value: 'fats_oils', label: 'Fats & Oils' },
  { value: 'beverages', label: 'Beverages' },
  { value: 'alcohol', label: 'Alcohol' },
  { value: 'snacks_sweets', label: 'Snacks & Sweets' },
  { value: 'prepared_restaurant', label: 'Prepared / Restaurant' },
]

export function categoryLabel(value) {
  return FOOD_CATEGORIES.find((c) => c.value === value)?.label ?? null
}
