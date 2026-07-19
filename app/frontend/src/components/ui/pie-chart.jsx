import { useMemo } from 'react'

/**
 * Minimal hand-rolled SVG pie/donut chart — no charting library dependency,
 * matching the existing convention in pages/charts.jsx (which hand-computes
 * SVG line paths rather than using a library like recharts). Kept
 * dependency-free intentionally, consistent with this frontend's existing
 * choice to keep bundle size/dependencies minimal.
 *
 * segments: [{ label, value, color }]. Renders nothing (returns null) if
 * every value is 0 — an empty pie chart is not a meaningful "0% of
 * everything" visual, it's just no data yet.
 */
export function PieChart({ segments, size = 160, strokeWidth = 28 }) {
  const total = segments.reduce((sum, s) => sum + Math.max(s.value, 0), 0)

  const arcs = useMemo(() => {
    if (total <= 0) return []
    const radius = (size - strokeWidth) / 2
    const circumference = 2 * Math.PI * radius
    let offset = 0
    return segments
      .filter((s) => s.value > 0)
      .map((s) => {
        const fraction = s.value / total
        const dash = fraction * circumference
        const arc = {
          ...s,
          dashArray: `${dash} ${circumference - dash}`,
          dashOffset: -offset,
        }
        offset += dash
        return arc
      })
  }, [segments, total, size, strokeWidth])

  if (total <= 0) {
    return (
      <div
        className="rounded-full border-2 border-dashed border-muted-foreground/30 flex items-center justify-center text-xs text-muted-foreground"
        style={{ width: size, height: size }}
      >
        No data
      </div>
    )
  }

  const radius = (size - strokeWidth) / 2
  const center = size / 2

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {arcs.map((arc) => (
        <circle
          key={arc.label}
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={arc.color}
          strokeWidth={strokeWidth}
          strokeDasharray={arc.dashArray}
          strokeDashoffset={arc.dashOffset}
          transform={`rotate(-90 ${center} ${center})`}
        />
      ))}
    </svg>
  )
}
