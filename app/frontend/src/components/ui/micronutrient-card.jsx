import { useState, useEffect, useRef } from 'react'
import { Link } from 'wouter'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { ChevronLeft, ChevronRight, ClipboardList, Settings2 } from 'lucide-react'

/**
 * `progress` is the shape returned by GET /targets/progress:
 * { [nutrient_name]: { unit, daily_target, max_threshold, actual, percent_of_target } }
 * `colors` (nutrient_insufficient/nutrient_sufficient/nutrient_excess) and
 * `sufficiencyThresholdPct` come from usePreferences — both are
 * user-configurable, not hardcoded, so this component never decides on
 * its own what "close to 100%" means or what color that state gets.
 *
 * Redesigned per explicit user request: don't show every tracked
 * micronutrient in one always-visible card ("don't shove everything down
 * the user's throat"). Instead this is a swipeable deck of small cards —
 * "Important to me" (user-customizable, from usePreferences) first, then
 * one card per backend-defined group (Vitamins/Minerals, from
 * GET /targets/nutrient-groups) — plus a link to a full, unfiltered
 * report for anyone who wants everything at once.
 */
export function statusFor(entry, sufficiencyThresholdPct) {
  if (!entry) return 'insufficient'
  if (entry.max_threshold != null && entry.actual > entry.max_threshold) return 'excess'
  if (entry.percent_of_target != null && entry.percent_of_target >= sufficiencyThresholdPct) return 'sufficient'
  return 'insufficient'
}

export function NutrientRow({ name, entry, colors, sufficiencyThresholdPct }) {
  const statusStyles = {
    insufficient: colors.nutrient_insufficient,
    sufficient: colors.nutrient_sufficient,
    excess: colors.nutrient_excess,
  }
  const status = statusFor(entry, sufficiencyThresholdPct)
  const color = statusStyles[status]
  const pct = entry?.percent_of_target ?? 0
  const barWidth = Math.min(pct, 100)

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-foreground font-medium">{name}</span>
        <span className="font-mono" style={{ color }}>
          {Math.round(entry?.actual ?? 0)}
          {entry?.unit ?? ''}
          {entry?.daily_target != null && (
            <span className="text-muted-foreground"> / {Math.round(entry.daily_target)}{entry.unit}</span>
          )}
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${entry ? barWidth : 0}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}

export function MicronutrientCard({ progress, colors, sufficiencyThresholdPct, importantNutrients, date }) {
  const [groups, setGroups] = useState(null)
  const [slide, setSlide] = useState(0)
  const touchStartX = useRef(null)

  useEffect(() => {
    api('/targets/nutrient-groups')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setGroups(d?.groups ?? null))
  }, [])

  const entries = Object.entries(progress || {})
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

  // Slide 0 is always "Important to me" (user-customizable); the rest
  // are the backend-defined groups (Vitamins, Minerals, ...), in
  // whatever order GET /targets/nutrient-groups returns them.
  const groupNames = groups ? Object.keys(groups) : []
  const slides = [
    { key: 'important', label: 'Important to Me', names: importantNutrients || [] },
    ...groupNames.map((g) => ({ key: g, label: g, names: groups[g] })),
  ]
  const current = slides[Math.min(slide, slides.length - 1)]
  const visibleNames = current.names.filter((n) => progress?.[n])

  function go(delta) {
    setSlide((s) => Math.max(0, Math.min(slides.length - 1, s + delta)))
  }

  function handleTouchStart(e) {
    touchStartX.current = e.touches[0].clientX
  }

  function handleTouchEnd(e) {
    if (touchStartX.current == null) return
    const delta = e.changedTouches[0].clientX - touchStartX.current
    if (Math.abs(delta) > 40) go(delta > 0 ? -1 : 1)
    touchStartX.current = null
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Micronutrients</CardTitle>
        <div className="flex items-center gap-1">
          <Link href="/profile#micronutrient-settings" title="Customize 'Important to Me'">
            <div className="p-1 rounded-md text-muted-foreground hover:bg-muted transition-colors cursor-pointer">
              <Settings2 className="h-3.5 w-3.5" />
            </div>
          </Link>
          <Link href={`/report?date=${date}`} title="Full micronutrient report">
            <div className="p-1 rounded-md text-muted-foreground hover:bg-muted transition-colors cursor-pointer">
              <ClipboardList className="h-3.5 w-3.5" />
            </div>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        <div
          className="flex items-center justify-between mb-3"
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
          <button
            onClick={() => go(-1)}
            disabled={slide === 0}
            className="p-1 rounded-md text-muted-foreground hover:bg-muted disabled:opacity-30 transition-colors"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-xs font-medium text-muted-foreground">{current.label}</span>
          <button
            onClick={() => go(1)}
            disabled={slide === slides.length - 1}
            className="p-1 rounded-md text-muted-foreground hover:bg-muted disabled:opacity-30 transition-colors"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3" onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
          {visibleNames.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4">
              {current.key === 'important'
                ? 'No nutrients pinned yet — customize this from your profile.'
                : 'Nothing tracked in this group yet.'}
            </p>
          ) : (
            visibleNames.map((name) => (
              <NutrientRow
                key={name}
                name={name}
                entry={progress[name]}
                colors={colors}
                sufficiencyThresholdPct={sufficiencyThresholdPct}
              />
            ))
          )}
        </div>

        <div className="flex justify-center gap-1.5 mt-3">
          {slides.map((s, i) => (
            <button
              key={s.key}
              onClick={() => setSlide(i)}
              className={`h-1.5 rounded-full transition-all ${i === slide ? 'w-4 bg-foreground' : 'w-1.5 bg-muted-foreground/30'}`}
              aria-label={`Go to ${s.label}`}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
