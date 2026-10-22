import { useRef, useState } from 'react'
import { Camera, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'

// Hidden <input capture="environment"> opens the device's native camera
// app and returns one photo -- same pattern finance-tracker's receipt
// capture already uses, rather than a custom live-video UI for a
// single-shot photo (that's only needed for the continuous-decode barcode
// scanner, a genuinely different interaction).
export function LabelPhotoCaptureButton({ onResult, onError, disabled }) {
  const inputRef = useRef(null)
  const [scanning, setScanning] = useState(false)

  async function handleFile(e) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-selecting the same file next time
    if (!file) return

    setScanning(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await api('/label-scanner/scan', { method: 'POST', body: formData })
      if (!res.ok) {
        onError?.(`Scan failed (${res.status})`)
        return
      }
      onResult(await res.json())
    } catch (e) {
      onError?.(e.message || 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={handleFile}
        className="hidden"
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || scanning}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
      >
        {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
        {scanning ? 'Scanning...' : 'Scan Label'}
      </button>
    </>
  )
}
