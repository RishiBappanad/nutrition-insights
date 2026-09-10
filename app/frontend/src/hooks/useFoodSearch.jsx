import { useState, useEffect, useRef } from 'react'
import { api } from '@/lib/api'
import { SearchSourceChips } from '@/components/ui/search-source-chips'

const SEARCH_DEBOUNCE_MS = 400

// Mirrors backend/app/routers/food.py's VALID_SEARCH_SOURCES/
// DEFAULT_SEARCH_SOURCES -- "pantry" defaults off there (added as a
// selectable source after USDA/CNF/recipe/meal already existed, so an
// unfiltered caller shouldn't suddenly see pantry items that weren't in
// results before), matched here so a fresh page load's filter chips
// reflect what actually gets searched by default.
export const SEARCH_SOURCES = [
  { key: 'USDA', label: 'USDA' },
  { key: 'CNF', label: 'CNF' },
  { key: 'recipe', label: 'My Recipes' },
  { key: 'meal', label: 'My Meals' },
  { key: 'pantry', label: 'My Pantry' },
]
export const DEFAULT_SEARCH_SOURCES = ['USDA', 'CNF', 'recipe', 'meal']

/**
 * Shared GET /food/search state + fetch logic -- every page that searches
 * food (food-log.jsx, recipes.jsx, meals.jsx, pantry.jsx) needs the exact
 * same query/debounce/source-filter mechanics to add an ingredient/item,
 * previously hand-duplicated in each of the 4 files independently (query
 * state, results state, searching state, and the debounced fetch effect,
 * each copy drifting slightly). One hook now, so a change here (e.g. a
 * new selectable source) reaches every search box automatically instead
 * of needing to be hand-copied into each page again.
 *
 * Each page keeps its OWN results-list rendering and its own "what
 * happens when a result is picked" logic (log to diary vs. add as a
 * recipe/meal item vs. add to pantry all differ) -- only the part that's
 * actually identical across all four is shared here.
 *
 * Also returns `sourceChips`, an already-wired <SearchSourceChips>
 * element -- a caller just renders {sourceChips} directly rather than
 * importing SearchSourceChips itself and re-passing activeSources/
 * toggleSource by hand. This is why this file is .jsx, not .js: a hook
 * returning a ready-to-render element is the point (one import per page
 * instead of two), not just a style choice.
 */
export function useFoodSearch() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [activeSources, setActiveSources] = useState(DEFAULT_SEARCH_SOURCES)

  function toggleSource(key) {
    setActiveSources((prev) =>
      prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key]
    )
  }

  // Guards against a slow earlier request's results overwriting a
  // newer one's — without this, typing "chi" then quickly "chicken"
  // could show "chi"'s results last if that request happens to resolve
  // after "chicken"'s, since fetches aren't guaranteed to resolve in
  // the order they were sent.
  const latestQueryRef = useRef('')

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      setSearching(false)
      setSearchError('')
      return
    }

    setSearching(true)
    setSearchError('')
    const timer = setTimeout(async () => {
      latestQueryRef.current = trimmed
      try {
        const params = new URLSearchParams({ q: trimmed, sources: activeSources.join(',') })
        const res = await api(`/food/search?${params}`)
        if (latestQueryRef.current !== trimmed) return // a newer query has since started
        if (!res.ok) {
          setSearchError(`Search failed (${res.status})`)
          setResults([])
        } else {
          const data = await res.json()
          setResults(data.results || [])
        }
      } catch {
        if (latestQueryRef.current === trimmed) {
          setSearchError('Network error')
          setResults([])
        }
      } finally {
        if (latestQueryRef.current === trimmed) setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [query, activeSources])

  function clear() {
    setQuery('')
    setResults([])
  }

  const sourceChips = <SearchSourceChips activeSources={activeSources} onToggle={toggleSource} />

  return { query, setQuery, results, searching, searchError, activeSources, toggleSource, clear, sourceChips }
}
