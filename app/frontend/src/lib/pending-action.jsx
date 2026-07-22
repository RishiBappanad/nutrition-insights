import { createContext, useContext, useRef, useState, useCallback, useEffect } from 'react'
import { useLocation } from 'wouter'
import { X, Undo2 } from 'lucide-react'

/**
 * Buffered "undo toast" for reversible pantry actions (consume/remove/
 * finish/delete). Per explicit user request/steering: rather than
 * mutate the database immediately and try to reverse it on Undo (which
 * needs server-side snapshot/reversal logic for every action type),
 * the action is BUFFERED — nothing is sent to the backend until the
 * toast's window closes. Undo (or the X) just discards the buffered
 * action; letting the timer run out, OR switching views/navigating
 * away, commits it (calls the real API).
 *
 * This is deliberately simple: only one pending action at a time (a
 * second buffered action while one is already pending immediately
 * commits the first one, then starts buffering the new one — matches
 * how a real toast queue would behave without needing an actual queue).
 */
const PendingActionContext = createContext(null)

const UNDO_WINDOW_MS = 5000

export function PendingActionProvider({ children }) {
  const [pending, setPending] = useState(null) // { label, commit, timeoutId }
  const pendingRef = useRef(null)
  const [location] = useLocation()
  const lastLocationRef = useRef(location)

  const commitNow = useCallback((p) => {
    if (!p) return
    clearTimeout(p.timeoutId)
    p.commit()
  }, [])

  const flush = useCallback(() => {
    const p = pendingRef.current
    if (p) {
      pendingRef.current = null
      setPending(null)
      commitNow(p)
    }
  }, [commitNow])

  // Switching views/routes commits whatever's still buffered — per
  // explicit steering ("buffer the changes until the popup window goes
  // away or the user switches views"), navigating away is NOT a way to
  // silently cancel an action, it's one of the two ways to commit it
  // (the other being the timeout expiring naturally).
  useEffect(() => {
    if (location !== lastLocationRef.current) {
      lastLocationRef.current = location
      flush()
    }
  }, [location, flush])

  const bufferAction = useCallback((label, commit, onUndo) => {
    // A new action while one's already pending: commit the old one
    // immediately (it's done being buffered) before starting the new
    // buffer window — never silently drops or overwrites a pending
    // commit.
    if (pendingRef.current) {
      commitNow(pendingRef.current)
    }
    const timeoutId = setTimeout(() => {
      pendingRef.current = null
      setPending(null)
      commit()
    }, UNDO_WINDOW_MS)
    const next = { label, commit, onUndo, timeoutId }
    pendingRef.current = next
    setPending(next)
  }, [commitNow])

  const undo = useCallback(() => {
    const p = pendingRef.current
    if (!p) return
    clearTimeout(p.timeoutId)
    pendingRef.current = null
    setPending(null)
    // Undo just discards -- commit() (the real API call) never runs.
    // onUndo (if given) lets the caller reverse any optimistic UI state
    // (e.g. un-hiding an item that was hidden immediately on action).
    p.onUndo?.()
  }, [])

  const dismiss = useCallback(() => {
    // The X button: per explicit steering, dismissing the toast is NOT
    // the same as undo -- it skips the waiting period and commits the
    // action right away instead of discarding it.
    flush()
  }, [flush])

  return (
    <PendingActionContext.Provider value={{ bufferAction }}>
      {children}
      {pending && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-3 rounded-lg border border-border bg-card shadow-lg text-sm">
          <span className="text-foreground">{pending.label}</span>
          <button
            onClick={undo}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-primary font-medium hover:bg-primary/10 transition-colors"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Undo
          </button>
          <button
            onClick={dismiss}
            title="Dismiss (applies the action now)"
            className="p-1 rounded-md text-muted-foreground hover:bg-muted transition-colors"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </PendingActionContext.Provider>
  )
}

/**
 * Returns `bufferAction(label, commit, onUndo?)` — call it instead of
 * directly calling the API for a reversible action. `label` is the
 * toast text (e.g. "Removed Chicken Breast" or "Logged 200 kcal to
 * diary"), `commit` is a function that performs the real API call(s)
 * and any follow-up (e.g. refresh()) once the buffer window elapses/
 * flushes, `onUndo` (optional) runs if the user clicks Undo — use it to
 * reverse any optimistic UI change made when the action was buffered
 * (e.g. un-hiding an item that was hidden immediately).
 */
export function usePendingAction() {
  const ctx = useContext(PendingActionContext)
  if (!ctx) throw new Error('usePendingAction must be used within a PendingActionProvider')
  return ctx.bufferAction
}
