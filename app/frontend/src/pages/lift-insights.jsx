import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/use-preferences'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export default function LiftInsights() {
  const { colors } = usePreferences()
  const [exercises, setExercises] = useState([])
  const [nutritionMetrics, setNutritionMetrics] = useState([])
  const [exercise, setExercise] = useState('')
  const [metric, setMetric] = useState('Energy (kcal)')
  const [lookback, setLookback] = useState(2)
  const [data, setData] = useState([])

  useEffect(() => {
    api('/data/lift-insights')
      .then((r) => r.json())
      .then((d) => {
        setExercises(d.exercises || [])
        if (d.exercises?.length) setExercise(d.exercises[0])
      })
  }, [])

  useEffect(() => {
    if (!exercise) return
    api(
      `/data/lift-insights?exercise=${encodeURIComponent(exercise)}&nutrition_metric=${encodeURIComponent(metric)}&lookback=${lookback}`
    )
      .then((r) => r.json())
      .then((d) => {
        setData(d.data || [])
        if (d.nutrition_metrics) setNutritionMetrics(d.nutrition_metrics)
      })
  }, [exercise, metric, lookback])

  const W = 560,
    H = 200,
    PAD = 50
  const hasData = data.length >= 2

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Lift Insights</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Correlate nutrition intake with lift performance
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Nutrition vs Performance</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Controls */}
          <div className="flex flex-wrap gap-3 items-center">
            <select
              value={exercise}
              onChange={(e) => setExercise(e.target.value)}
              className="px-3 py-2 rounded-md border bg-card text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {exercises.map((ex) => (
                <option key={ex} value={ex}>
                  {ex}
                </option>
              ))}
            </select>
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
              className="px-3 py-2 rounded-md border bg-card text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {nutritionMetrics.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-1">
              <span className="text-xs text-muted-foreground">Lookback:</span>
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
                  {d}d
                </button>
              ))}
            </div>
          </div>

          {/* Scatter Plot */}
          {hasData ? (
            <>
              <svg
                viewBox={`0 0 ${W} ${H + 20}`}
                className="w-full h-auto mt-3"
              >
                {(() => {
                  const xVals = data.map((d) => d.avg_metric)
                  const yVals = data.map((d) => d.orm)
                  const xMin = Math.min(...xVals),
                    xMax = Math.max(...xVals)
                  const yMin = Math.min(...yVals),
                    yMax = Math.max(...yVals)
                  const xRange = xMax - xMin || 1,
                    yRange = yMax - yMin || 1
                  const toX = (v) =>
                    PAD + ((v - xMin) / xRange) * (W - PAD * 2)
                  const toY = (v) =>
                    H - PAD + 10 - ((v - yMin) / yRange) * (H - PAD * 2)
                  return (
                    <g>
                      {data.map((d, i) => (
                        <circle
                          key={i}
                          cx={toX(d.avg_metric)}
                          cy={toY(d.orm)}
                          r="5"
                          fill={colors.lift_scatter}
                          opacity="0.7"
                        />
                      ))}
                      <text
                        x={W / 2}
                        y={H + 15}
                        fontSize="9"
                        fill="hsl(156, 10%, 40%)"
                        textAnchor="middle"
                      >
                        {metric.split('(')[0].trim()} ({lookback}d avg)
                      </text>
                      <text
                        x={10}
                        y={H / 2}
                        fontSize="9"
                        fill="hsl(156, 10%, 40%)"
                        transform={`rotate(-90, 10, ${H / 2})`}
                        textAnchor="middle"
                      >
                        ORM (lbs)
                      </text>
                      <text
                        x={PAD}
                        y={H + 5}
                        fontSize="8"
                        fill="hsl(156, 10%, 40%)"
                      >
                        {xMin.toFixed(0)}
                      </text>
                      <text
                        x={W - PAD}
                        y={H + 5}
                        fontSize="8"
                        fill="hsl(156, 10%, 40%)"
                        textAnchor="end"
                      >
                        {xMax.toFixed(0)}
                      </text>
                      <text
                        x={PAD - 5}
                        y={toY(yMax)}
                        fontSize="8"
                        fill="hsl(156, 10%, 40%)"
                        textAnchor="end"
                      >
                        {yMax.toFixed(0)}
                      </text>
                      <text
                        x={PAD - 5}
                        y={toY(yMin)}
                        fontSize="8"
                        fill="hsl(156, 10%, 40%)"
                        textAnchor="end"
                      >
                        {yMin.toFixed(0)}
                      </text>
                    </g>
                  )
                })()}
              </svg>
              <p className="text-xs text-muted-foreground">
                {data.length} data points
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground mt-3">
              Need at least 2 lift sessions with prior nutrition data.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
