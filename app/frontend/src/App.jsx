import React, { useState, useEffect } from 'react'

const API = ''

function api(path, opts = {}) {
  const token = localStorage.getItem('token')
  return fetch(`${API}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
      ...opts.headers,
    },
  })
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isRegister, setIsRegister] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    const endpoint = isRegister ? '/auth/register' : '/auth/login'
    const res = await api(endpoint, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json()
    if (data.token) {
      localStorage.setItem('token', data.token)
      onLogin()
    } else {
      setError(data.detail || 'Failed')
    }
  }

  return (
    <div className="card">
      <h2>{isRegister ? 'Sign Up' : 'Login'}</h2>
      <form onSubmit={handleSubmit}>
        <input placeholder="Username" value={username} onChange={e => setUsername(e.target.value)} />
        <input placeholder="Password" type="password" value={password} onChange={e => setPassword(e.target.value)} />
        <button type="submit">{isRegister ? 'Register' : 'Login'}</button>
      </form>
      {error && <p className="error">{error}</p>}
      <p className="toggle" onClick={() => setIsRegister(!isRegister)}>
        {isRegister ? 'Already have an account? Login' : 'New? Sign up'}
      </p>
    </div>
  )
}

function Credentials({ onSaved }) {
  const [form, setForm] = useState({ hevy_username: '', hevy_password: '', cronometer_username: '', cronometer_password: '' })
  const [status, setStatus] = useState('')

  async function handleSave(e) {
    e.preventDefault()
    const res = await api('/auth/credentials', { method: 'POST', body: JSON.stringify(form) })
    if (res.ok) { setStatus('Saved!'); setTimeout(onSaved, 1000) }
    else {
      const data = await res.json().catch(() => ({}))
      setStatus(data.detail || `Failed (${res.status})`)
    }
  }

  return (
    <div className="card">
      <h2>Connect Accounts</h2>
      <form onSubmit={handleSave}>
        <h3>Cronometer</h3>
        <input placeholder="Username/Email" value={form.cronometer_username} onChange={e => setForm({ ...form, cronometer_username: e.target.value })} />
        <input placeholder="Password" type="password" value={form.cronometer_password} onChange={e => setForm({ ...form, cronometer_password: e.target.value })} />
        <h3>Hevy</h3>
        <input placeholder="Username/Email" value={form.hevy_username} onChange={e => setForm({ ...form, hevy_username: e.target.value })} />
        <input placeholder="Password" type="password" value={form.hevy_password} onChange={e => setForm({ ...form, hevy_password: e.target.value })} />
        <button type="submit">Save Credentials</button>
      </form>
      {status && <p>{status}</p>}
    </div>
  )
}

function Chart() {
  const [categories, setCategories] = useState({})
  const [selected, setSelected] = useState(['Energy (kcal)'])
  const [series, setSeries] = useState({})
  const [openCat, setOpenCat] = useState('biometrics')
  const [lookback, setLookback] = useState(1)

  useEffect(() => {
    api(`/data/chart?metrics=${encodeURIComponent('Energy (kcal)')}`).then(r => r.json()).then(d => {
      setCategories(d.categories || {})
    })
  }, [])

  useEffect(() => {
    if (selected.length === 0) { setSeries({}); return }
    api(`/data/chart?metrics=${selected.map(encodeURIComponent).join(',')}&lookback=${lookback}`).then(r => r.json()).then(d => setSeries(d.series || {}))
  }, [selected, lookback])

  const toggleMetric = (m) => {
    setSelected(prev => {
      if (prev.includes(m)) return prev.filter(x => x !== m)
      if (prev.length >= 3) return prev
      return [...prev, m]
    })
  }

  const colors = ['#007aff', '#ff6b35', '#2ecc71']
  const catLabels = { biometrics: 'Biometrics', nutrition: 'Nutrition', exercise: 'Exercise' }

  // Merge all dates across series
  const allDates = [...new Set(Object.values(series).flatMap(s => s.map(p => p.date)))].sort()
  const W = 560, H = 200, PAD = 40

  return (
    <div className="card">
      <h2>Chart</h2>
      <div className="cat-tabs">
        {Object.keys(categories).map(cat => (
          <button key={cat} className={`cat-tab ${openCat === cat ? 'active' : ''}`} onClick={() => setOpenCat(cat)}>
            {catLabels[cat] || cat}
          </button>
        ))}
      </div>
      <div className="chip-row">
        {(categories[openCat] || []).map(m => (
          <button key={m} className={`chip ${selected.includes(m) ? 'active' : ''}`} onClick={() => toggleMetric(m)}>
            {m}
          </button>
        ))}
      </div>
      {selected.length > 0 && <p className="selected-label">Showing: {selected.join(', ')}</p>}
      {openCat === 'nutrition' && (
        <div className="timeframe">
          <span style={{fontSize: 11, color: '#666'}}>Rolling avg:</span>
          {[1, 2, 3].map(d => (
            <button key={d} className={`chip ${lookback === d ? 'active' : ''}`} onClick={() => setLookback(d)}>{d === 1 ? 'Daily' : `${d}d`}</button>
          ))}
        </div>
      )}
      {allDates.length >= 2 ? (
        <svg viewBox={`0 0 ${W} ${H + 30}`} style={{width: '100%', height: 'auto', marginTop: 12}}>
          {Object.entries(series).map(([name, points], idx) => {
            if (points.length < 2) return null
            const vals = points.map(p => p.value)
            const min = Math.min(...vals), max = Math.max(...vals)
            const range = max - min || 1
            const dateToX = (d) => PAD + ((allDates.indexOf(d)) / (allDates.length - 1)) * (W - PAD * 2)
            const valToY = (v) => H - PAD - ((v - min) / range) * (H - PAD * 2)
            const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${dateToX(p.date).toFixed(1)},${valToY(p.value).toFixed(1)}`).join(' ')
            return (
              <g key={name}>
                <path d={pathD} fill="none" stroke={colors[idx % 3]} strokeWidth="2" />
                <text x={W - PAD + 4} y={valToY(vals[vals.length - 1])} fill={colors[idx % 3]} fontSize="10">{name.split('(')[0].trim()}</text>
              </g>
            )
          })}
          <text x={PAD} y={H + 5} fontSize="8" fill="#999">{allDates[0]}</text>
          <text x={W - PAD} y={H + 5} fontSize="8" fill="#999" textAnchor="end">{allDates[allDates.length - 1]}</text>
        </svg>
      ) : <p style={{color:'#999', marginTop:12}}>Select metrics to display chart.</p>}
    </div>
  )
}

function LogView() {
  const [categories, setCategories] = useState({})
  const [columns, setColumns] = useState(['Energy (kcal)', 'Protein (g)', 'Weight (lbs)'])
  const [openCat, setOpenCat] = useState('biometrics')
  const [days, setDays] = useState(7)
  const [lookback, setLookback] = useState(1)
  const [data, setData] = useState({})

  useEffect(() => {
    api('/data/chart?metrics=').then(r => r.json()).then(d => setCategories(d.categories || {}))
  }, [])

  useEffect(() => {
    if (columns.length === 0) return
    api(`/data/chart?metrics=${columns.map(encodeURIComponent).join(',')}&lookback=${lookback}`).then(r => r.json()).then(d => setData(d.series || {}))
  }, [columns, lookback])

  const toggleCol = (m) => {
    setColumns(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m])
  }

  const catLabels = { biometrics: 'Biometrics', nutrition: 'Nutrition', exercise: 'Exercise' }

  // Merge all dates and limit to timeframe
  const allDates = [...new Set(Object.values(data).flatMap(s => s.map(p => p.date)))].sort().slice(-days)

  // Build table rows
  const rows = allDates.map(date => {
    const row = { date }
    columns.forEach(col => {
      const pt = (data[col] || []).find(p => p.date === date)
      row[col] = pt ? pt.value : '-'
    })
    return row
  }).reverse()

  return (
    <div className="card">
      <h2>Log</h2>
      <div className="cat-tabs">
        {Object.keys(categories).map(cat => (
          <button key={cat} className={`cat-tab ${openCat === cat ? 'active' : ''}`} onClick={() => setOpenCat(cat)}>{catLabels[cat]}</button>
        ))}
      </div>
      <div className="chip-row">
        {(categories[openCat] || []).map(m => (
          <button key={m} className={`chip ${columns.includes(m) ? 'active' : ''}`} onClick={() => toggleCol(m)}>{m}</button>
        ))}
      </div>
      <div className="timeframe">
        {[7, 14, 30, 60].map(d => (
          <button key={d} className={`chip ${days === d ? 'active' : ''}`} onClick={() => setDays(d)}>{d}d</button>
        ))}
        {openCat === 'nutrition' && <>
          <span style={{fontSize: 11, color: '#666', marginLeft: 8}}>Avg:</span>
          {[1, 2, 3].map(d => (
            <button key={d} className={`chip ${lookback === d ? 'active' : ''}`} onClick={() => setLookback(d)}>{d === 1 ? 'Daily' : `${d}d`}</button>
          ))}
        </>}
      </div>
      <div style={{overflowX: 'auto', marginTop: 10}}>
        <table>
          <thead>
            <tr><th>Date</th>{columns.map(c => <th key={c}>{c.split('(')[0].trim()}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.date}><td>{r.date}</td>{columns.map(c => <td key={c}>{typeof r[c] === 'number' ? r[c].toFixed(1) : r[c]}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function LiftInsights() {
  const [exercises, setExercises] = useState([])
  const [nutritionMetrics, setNutritionMetrics] = useState([])
  const [exercise, setExercise] = useState('')
  const [metric, setMetric] = useState('Energy (kcal)')
  const [lookback, setLookback] = useState(2)
  const [data, setData] = useState([])

  useEffect(() => {
    api('/data/lift-insights').then(r => r.json()).then(d => {
      setExercises(d.exercises || [])
      if (d.exercises?.length) setExercise(d.exercises[0])
    })
  }, [])

  useEffect(() => {
    if (!exercise) return
    api(`/data/lift-insights?exercise=${encodeURIComponent(exercise)}&nutrition_metric=${encodeURIComponent(metric)}&lookback=${lookback}`)
      .then(r => r.json()).then(d => {
        setData(d.data || [])
        if (d.nutrition_metrics) setNutritionMetrics(d.nutrition_metrics)
      })
  }, [exercise, metric, lookback])

  const W = 560, H = 200, PAD = 50
  const hasData = data.length >= 2

  return (
    <div className="card">
      <h2>Lift Insights</h2>
      <div className="insight-controls">
        <select value={exercise} onChange={e => setExercise(e.target.value)}>
          {exercises.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
        <select value={metric} onChange={e => setMetric(e.target.value)}>
          {nutritionMetrics.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <div className="chip-row">
          {[1, 2, 3].map(d => (
            <button key={d} className={`chip ${lookback === d ? 'active' : ''}`} onClick={() => setLookback(d)}>{d}d</button>
          ))}
        </div>
      </div>
      {hasData ? (
        <svg viewBox={`0 0 ${W} ${H + 20}`} style={{width: '100%', height: 'auto', marginTop: 12}}>
          {(() => {
            const xVals = data.map(d => d.avg_metric)
            const yVals = data.map(d => d.orm)
            const xMin = Math.min(...xVals), xMax = Math.max(...xVals)
            const yMin = Math.min(...yVals), yMax = Math.max(...yVals)
            const xRange = xMax - xMin || 1, yRange = yMax - yMin || 1
            const toX = v => PAD + ((v - xMin) / xRange) * (W - PAD * 2)
            const toY = v => H - PAD + 10 - ((v - yMin) / yRange) * (H - PAD * 2)
            return (
              <g>
                {data.map((d, i) => (
                  <circle key={i} cx={toX(d.avg_metric)} cy={toY(d.orm)} r="5" fill="#007aff" opacity="0.7" />
                ))}
                <text x={W / 2} y={H + 15} fontSize="9" fill="#666" textAnchor="middle">{metric.split('(')[0].trim()} ({lookback}d avg)</text>
                <text x={10} y={H / 2} fontSize="9" fill="#666" transform={`rotate(-90, 10, ${H / 2})`} textAnchor="middle">ORM (lbs)</text>
                <text x={PAD} y={H + 5} fontSize="8" fill="#999">{xMin.toFixed(0)}</text>
                <text x={W - PAD} y={H + 5} fontSize="8" fill="#999" textAnchor="end">{xMax.toFixed(0)}</text>
                <text x={PAD - 5} y={toY(yMax)} fontSize="8" fill="#999" textAnchor="end">{yMax.toFixed(0)}</text>
                <text x={PAD - 5} y={toY(yMin)} fontSize="8" fill="#999" textAnchor="end">{yMin.toFixed(0)}</text>
              </g>
            )
          })()}
        </svg>
      ) : <p style={{color:'#999', marginTop: 12}}>Need at least 2 lift sessions with prior nutrition data.</p>}
      {hasData && <p style={{fontSize: 11, color: '#666', marginTop: 4}}>{data.length} data points</p>}
    </div>
  )
}

function Dashboard() {
  const [bmr, setBmr] = useState(null)
  const [syncing, setSyncing] = useState('')
  const [syncResult, setSyncResult] = useState(null)
  const [view, setView] = useState('chart')

  useEffect(() => {
    api('/data/bmr').then(r => r.json()).then(d => setBmr(d.bmr))
  }, [])

  async function handleSync(target) {
    setSyncing(target)
    setSyncResult(null)
    const res = await api(`/sync/${target}`, { method: 'POST' })
    const data = await res.json()
    setSyncResult(data)
    setSyncing('')
    api('/data/bmr').then(r => r.json()).then(d => setBmr(d.bmr))
  }

  return (
    <div>
      <div className="card">
        <h2>BMR: {bmr ? `${bmr} kcal` : 'Sync to calculate'}</h2>
        <div className="sync-btns">
          <button onClick={() => handleSync('cronometer')} disabled={!!syncing}>
            {syncing === 'cronometer' ? 'Syncing...' : 'Sync Cronometer'}
          </button>
          <button onClick={() => handleSync('hevy')} disabled={!!syncing}>
            {syncing === 'hevy' ? 'Syncing...' : 'Sync Hevy'}
          </button>
        </div>
        {syncResult && <pre>{JSON.stringify(syncResult, null, 2)}</pre>}
      </div>

      <div className="view-tabs">
        <button className={`cat-tab ${view === 'chart' ? 'active' : ''}`} onClick={() => setView('chart')}>Chart</button>
        <button className={`cat-tab ${view === 'log' ? 'active' : ''}`} onClick={() => setView('log')}>Log</button>
        <button className={`cat-tab ${view === 'insights' ? 'active' : ''}`} onClick={() => setView('insights')}>Lift Insights</button>
      </div>

      {view === 'chart' && <Chart />}
      {view === 'log' && <LogView />}
      {view === 'insights' && <LiftInsights />}
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(!!localStorage.getItem('token'))
  const [showCreds, setShowCreds] = useState(false)

  if (!authed) return <Login onLogin={() => setAuthed(true)} />

  return (
    <div className="container">
      <header>
        <h1>Nutrition Insights</h1>
        <nav>
          <button onClick={() => setShowCreds(!showCreds)}>⚙️ Accounts</button>
          <button onClick={() => { localStorage.removeItem('token'); setAuthed(false) }}>Logout</button>
        </nav>
      </header>
      {showCreds && <Credentials onSaved={() => setShowCreds(false)} />}
      <Dashboard />
    </div>
  )
}
