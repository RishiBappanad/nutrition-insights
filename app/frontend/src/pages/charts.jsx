import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export default function Charts() {
  const { colors } = usePreferences()
  const [categories, setCategories] = useState({})
  const [selected, setSelected] = useState(['Energy (kcal)'])
  const [series, setSeries] = useState({})
  const [openCat, setOpenCat] = useState('biometrics')
  const [lookback, setLookback] = useState(1)
  const [hoverIdx, setHoverIdx] = useState(null)

  useEffect(() => {
    api(`/data/chart?metrics=${encodeURIComponent('Energy (kcal)')}`)
      .then((r) => r.json())
      .then((d) => {
        setCategories(d.categories || {})
      })
  }, [])

  useEffect(() => {
    if (selected.length === 0) {
      setSeries({})
      return
    }
    api(
      `/data/chart?metrics=${selected.map(encodeURIComponent).join(',')}&lookback=${lookback}`
    )
      .then((r) => r.json())
      .then((d) => setSeries(d.series || {}))
  }, [selected, lookback])

  const toggleMetric = (m) => {
    setSelected((prev) => {
      if (prev.includes(m)) return prev.filter((x) => x !== m)
      if (prev.length >= 3) return prev
      return [...prev, m]
    })
  }

  const chartColors = [colors.chart_line_1, colors.chart_line_2, colors.chart_line_3]
  const catLabels = {
    biometrics: 'Biometrics',
    nutrition: 'Nutrition',
    exercise: 'Exercise',
  }

  const allDates = [
    ...new Set(Object.values(series).flatMap((s) => s.map((p) => p.date))),
  ].sort()
  const W = 560,
    H = 200,
    PAD = 40

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Charts</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Visualize your metrics over time
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metric Trends</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Category Tabs */}
          <div className="flex gap-1">
            {Object.keys(categories).map((cat) => (
              <button
                key={cat}
                className={cn(
                  'px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                  openCat === cat
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                )}
                onClick={() => setOpenCat(cat)}
              >
                {catLabels[cat] || cat}
              </button>
            ))}
          </div>

          {/* Metric Chips */}
          <div className="flex flex-wrap gap-2">
            {(categories[openCat] || []).map((m) => (
              <button
                key={m}
                className={cn(
                  'px-3 py-1 rounded-full text-xs font-medium transition-colors border',
                  selected.includes(m)
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-card text-foreground border-border hover:bg-muted'
                )}
                onClick={() => toggleMetric(m)}
              >
                {m}
              </button>
            ))}
          </div>

          {selected.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Showing: {selected.join(', ')}
            </p>
          )}

          {/* Rolling avg for nutrition */}
          {openCat === 'nutrition' && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">Rolling avg:</span>
              {[1, 2, 3].map((d) => (
                <button
                  key={d}
                  className={cn(
                    'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                    lookback === d
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  )}
                  onClick={() => setLookback(d)}
                >
                  {d === 1 ? 'Daily' : `${d}d`}
                </button>
              ))}
            </div>
          )}

          {/* SVG Chart */}
          {allDates.length >= 2 ? (
            <svg
              viewBox={`0 0 ${W} ${H + 30}`}
              className="w-full h-auto mt-3"
              onMouseMove={(e) => {
                const rect = e.currentTarget.getBoundingClientRect()
                const svgX =
                  ((e.clientX - rect.left) / rect.width) * W
                const idx = Math.round(
                  ((svgX - PAD) / (W - PAD * 2)) * (allDates.length - 1)
                )
                setHoverIdx(
                  Math.max(0, Math.min(allDates.length - 1, idx))
                )
              }}
              onMouseLeave={() => setHoverIdx(null)}
            >
              {Object.entries(series).map(([name, points], idx) => {
                if (points.length < 2) return null
                const vals = points.map((p) => p.value)
                const min = Math.min(...vals),
                  max = Math.max(...vals)
                const range = max - min || 1
                const dateToX = (d) =>
                  PAD +
                  (allDates.indexOf(d) / (allDates.length - 1)) *
                    (W - PAD * 2)
                const valToY = (v) =>
                  H - PAD - ((v - min) / range) * (H - PAD * 2)
                const pathD = points
                  .map(
                    (p, i) =>
                      `${i === 0 ? 'M' : 'L'}${dateToX(p.date).toFixed(1)},${valToY(p.value).toFixed(1)}`
                  )
                  .join(' ')
                const hoverPoint =
                  hoverIdx !== null
                    ? points.find((p) => p.date === allDates[hoverIdx])
                    : null
                return (
                  <g key={name}>
                    <path
                      d={pathD}
                      fill="none"
                      stroke={chartColors[idx % 3]}
                      strokeWidth="2"
                    />
                    <text
                      x={W - PAD + 4}
                      y={valToY(vals[vals.length - 1])}
                      fill={chartColors[idx % 3]}
                      fontSize="10"
                    >
                      {name.split('(')[0].trim()}
                    </text>
                    {hoverPoint && (
                      <circle
                        cx={dateToX(hoverPoint.date)}
                        cy={valToY(hoverPoint.value)}
                        r="4"
                        fill={chartColors[idx % 3]}
                      />
                    )}
                  </g>
                )
              })}
              {hoverIdx !== null && (
                <g>
                  <line
                    x1={
                      PAD +
                      (hoverIdx / (allDates.length - 1)) * (W - PAD * 2)
                    }
                    y1={0}
                    x2={
                      PAD +
                      (hoverIdx / (allDates.length - 1)) * (W - PAD * 2)
                    }
                    y2={H - PAD + 10}
                    stroke="#999"
                    strokeWidth="0.5"
                    strokeDasharray="3,2"
                  />
                  <text
                    x={
                      PAD +
                      (hoverIdx / (allDates.length - 1)) * (W - PAD * 2)
                    }
                    y={H + 15}
                    fontSize="9"
                    fill="hsl(156, 30%, 12%)"
                    textAnchor="middle"
                  >
                    {allDates[hoverIdx]}
                  </text>
                  {Object.entries(series).map(([name, points], idx) => {
                    const pt = points.find(
                      (p) => p.date === allDates[hoverIdx]
                    )
                    if (!pt) return null
                    return (
                      <text
                        key={name}
                        x={
                          PAD +
                          (hoverIdx / (allDates.length - 1)) *
                            (W - PAD * 2) +
                          6
                        }
                        y={16 + idx * 12}
                        fontSize="9"
                        fill={chartColors[idx % 3]}
                      >
                        {name.split('(')[0].trim()}: {pt.value.toFixed(1)}
                      </text>
                    )
                  })}
                </g>
              )}
              {hoverIdx === null && (
                <text x={PAD} y={H + 5} fontSize="8" fill="hsl(156, 10%, 40%)">
                  {allDates[0]}
                </text>
              )}
              {hoverIdx === null && (
                <text
                  x={W - PAD}
                  y={H + 5}
                  fontSize="8"
                  fill="hsl(156, 10%, 40%)"
                  textAnchor="end"
                >
                  {allDates[allDates.length - 1]}
                </text>
              )}
            </svg>
          ) : (
            <p className="text-sm text-muted-foreground mt-3">
              Select metrics to display chart.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
