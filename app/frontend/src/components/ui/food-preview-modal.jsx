import { X } from 'lucide-react'
import { FoodPreviewCard } from '@/components/ui/food-preview-card'

// Shared by every page with a food-search preview (food-log, pantry, meals,
// recipes) -- each previously rendered FoodPreviewCard inline below its own
// results list, where the results list itself could sit on top of (or push
// below the fold) the preview. A fixed, top-of-stack overlay -- same
// convention as barcode-scanner-modal.jsx -- guarantees the preview is
// always fully visible regardless of where the triggering list sits on the
// page, with a click-outside-or-X-to-close affordance.
export function FoodPreviewModal({ result, onClose }) {
  if (!result) return null
  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-md max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          title="Close preview"
          className="absolute -top-3 -right-3 p-2 rounded-full bg-background border border-border z-10"
        >
          <X className="h-4 w-4" />
        </button>
        <FoodPreviewCard result={result} />
      </div>
    </div>
  )
}
