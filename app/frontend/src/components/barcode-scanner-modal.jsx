import { useEffect, useRef, useState } from 'react'
import { BrowserMultiFormatReader } from '@zxing/browser'
import { X, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'

// Live camera view with continuous barcode decoding -- unlike the label
// scanner's single photo capture, a barcode needs a real-time video feed
// so the user can aim at the barcode and get instant feedback, which is
// why this is a modal with a <video> element rather than a file picker.
// No live-video code exists anywhere else in this app yet; @zxing/browser
// is the decode engine since this is still a browser/PWA context (no
// native Capacitor build to use ML Kit's camera APIs from).
export function BarcodeScannerModal({ onResult, onError, onClose }) {
  const videoRef = useRef(null)
  const controlsRef = useRef(null)
  const resolvedRef = useRef(false)
  const [resolving, setResolving] = useState(false)

  useEffect(() => {
    const reader = new BrowserMultiFormatReader()
    let cancelled = false

    reader
      .decodeFromVideoDevice(undefined, videoRef.current, async (result, err, controls) => {
        controlsRef.current = controls
        // zxing invokes this callback on every decoded frame, including
        // frames with no barcode in view (a NotFoundException on `err`,
        // not a real failure) -- only ever act on an actual successful
        // decode, and only the first one.
        if (cancelled || resolvedRef.current || !result) return
        resolvedRef.current = true
        controls.stop()
        setResolving(true)
        try {
          const res = await api('/barcode-scanner/scan', {
            method: 'POST',
            body: JSON.stringify({ barcode: result.getText() }),
          })
          if (!res.ok) {
            onError?.(`Scan failed (${res.status})`)
            return
          }
          onResult(await res.json())
        } catch (e) {
          onError?.(e.message || 'Scan failed')
        } finally {
          setResolving(false)
        }
      })
      .catch((e) => onError?.(`Camera unavailable: ${e.message}`))

    return () => {
      cancelled = true
      controlsRef.current?.stop()
    }
  }, [])

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
      <div className="relative w-full max-w-sm">
        <video ref={videoRef} className="w-full rounded-lg bg-black" muted playsInline />
        {resolving && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60 rounded-lg">
            <Loader2 className="h-8 w-8 text-white animate-spin" />
          </div>
        )}
        <button
          type="button"
          onClick={onClose}
          className="absolute -top-3 -right-3 p-2 rounded-full bg-background border border-border"
        >
          <X className="h-4 w-4" />
        </button>
        <p className="text-center text-sm text-white/80 mt-3">Point your camera at a barcode</p>
      </div>
    </div>
  )
}
