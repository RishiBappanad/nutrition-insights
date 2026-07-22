import { useEffect } from 'react'

/**
 * Scrolls the element matching the current URL hash (e.g.
 * "#micronutrient-settings") into view after mount/update. Needed
 * because client-side route changes (wouter's <Link>) don't trigger the
 * browser's native "scroll to element matching the hash" behavior the
 * way a full page navigation does -- without this, a link like
 * <Link href="/profile#micronutrient-settings"> correctly navigates to
 * /profile but silently does nothing with the hash fragment, landing
 * the user at the top of the page instead of the section it was meant
 * to point at (a real gap found via a full frontend integration audit,
 * not by design).
 *
 * Call once near the top of any page that has anchor targets other
 * pages might link to.
 */
export function useScrollToHash(deps = []) {
  useEffect(() => {
    if (!window.location.hash) return
    const id = window.location.hash.slice(1)
    const el = document.getElementById(id)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
