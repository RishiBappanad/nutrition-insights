import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export default function Log() {
  const [categories, setCategories] = useState({})
  const [columns, setColumns] = useState([
    'Energy (kcal)',
    'Protein (g)',
    'Weight (lbs)',
  ])
  const [openCat, setOpenCat] = useState('biometrics')
  const [days, setDays] = useState(7)
  const [lookback, setLookback] = useState(1)
  const [data, setData] = useState({})

  useEffect(() => {
    api('/data/chart?metrics=')
      .then((r) => r.json())
      .then((d) => setCategories(d.categories || {}))
  }, [])

  useEffect(() => {
    if (columns.length === 0) return
    api(
      `/data/chart?metrics=${columns.map(encodeURIComponent).join(',')}&lookback=${lookback}`
    )
      .then((r) => r.json())
      .then((d) => setData(d.series || {}))
  }, [columns, lookback])

  const toggleCol = (m) => {
    setColumns((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
    )
  }

  const catLabels = {
    biometrics: 'Biometrics',
    nutrition: 'Nutrition',
    exercise: 'Exercise',
  }

  const allDates = [
    ...new Set(Object.values(data).flatMap((s) => s.map((p) => p.date))),
  ]
    .sort()
    .slice(-days)

  const rows = allDates
    .map((date) => {
      const row = { date }
      columns.forEach((col) => {
        const pt = (data[col] || []).find((p) => p.date === date)
        row[col] = pt ? pt.value : '-'
      })
      return row
    })
    .reverse()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Log</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Tabular view of your tracked data
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Data Log</CardTitle>
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
                  columns.includes(m)
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-card text-foreground border-border hover:bg-muted'
                )}
                onClick={() => toggleCol(m)}
              >
                {m}
              </button>
            ))}
          </div>

          {/* Timeframe + Lookback */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">Days:</span>
            {[7, 14, 30, 60].map((d) => (
              <button
                key={d}
                className={cn(
                  'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                  days === d
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                )}
                onClick={() => setDays(d)}
              >
                {d}d
              </button>
            ))}
            {openCat === 'nutrition' && (
              <>
                <span className="text-xs text-muted-foreground ml-2">
                  Avg:
                </span>
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
              </>
            )}
          </div>

          {/* Table */}
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                    Date
                  </th>
                  {columns.map((c) => (
                    <th
                      key={c}
                      className="px-4 py-3 text-left font-medium text-muted-foreground"
                    >
                      {c.split('(')[0].trim()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.date} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3 font-mono text-xs">
                      {r.date}
                    </td>
                    {columns.map((c) => (
                      <td key={c} className="px-4 py-3 font-mono text-xs">
                        {typeof r[c] === 'number' ? r[c].toFixed(1) : r[c]}
                      </td>
                    ))}
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={columns.length + 1}
                      className="px-4 py-8 text-center text-muted-foreground"
                    >
                      No data available. Sync your accounts first.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
