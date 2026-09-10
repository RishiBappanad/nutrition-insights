import { cn } from '@/lib/utils'
import { SEARCH_SOURCES } from '@/hooks/useFoodSearch'

/** Shared filter-chip row for every GET /food/search box in the app --
 * see useFoodSearch's own docstring for why this is a hook + component
 * pair instead of hand-copied per page. */
export function SearchSourceChips({ activeSources, onToggle }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {SEARCH_SOURCES.map((s) => {
        const active = activeSources.includes(s.key)
        return (
          <button
            key={s.key}
            type="button"
            onClick={() => onToggle(s.key)}
            className={cn(
              'px-2.5 py-1 rounded-full border text-xs font-medium transition-colors',
              active
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border text-muted-foreground hover:bg-muted'
            )}
          >
            {s.label}
          </button>
        )
      })}
    </div>
  )
}
