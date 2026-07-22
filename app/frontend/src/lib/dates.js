// Local-calendar-date helpers. `Date.prototype.toISOString()` reports UTC,
// which silently rolls over to "tomorrow" for anyone west of UTC once
// local time passes UTC midnight (e.g. 8pm EDT) -- every page that used
// `new Date().toISOString().slice(0, 10)` for "today" had this same
// latent bug (today's logged food would appear to vanish from "today"'s
// view for several hours every evening). Centralized here so it's fixed
// once, not five times with five chances to regress.

export function todayIso() {
  return toIso(new Date())
}

export function toIso(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function fromIso(iso) {
  // New Date('YYYY-MM-DD') parses as UTC midnight in most engines, which
  // can shift a day backward when displayed in a negative-UTC-offset
  // timezone -- parse the parts directly as LOCAL instead.
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function addDays(iso, delta) {
  const date = fromIso(iso)
  date.setDate(date.getDate() + delta)
  return toIso(date)
}

export function isToday(iso) {
  return iso === todayIso()
}

// Friendly display: "Today", "Yesterday", or a short date like "Jul 18".
export function friendlyDate(iso) {
  if (iso === todayIso()) return 'Today'
  if (iso === addDays(todayIso(), -1)) return 'Yesterday'
  if (iso === addDays(todayIso(), 1)) return 'Tomorrow'
  return fromIso(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
