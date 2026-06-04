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

function Dashboard() {
  const [bmr, setBmr] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const [log, setLog] = useState([])

  useEffect(() => {
    api('/data/bmr').then(r => r.json()).then(d => setBmr(d.bmr))
    api('/data/tdee-log').then(r => r.json()).then(d => setLog(d.entries || []))
  }, [])

  async function handleSync() {
    setSyncing(true)
    setSyncResult(null)
    const res = await api('/sync/all', { method: 'POST' })
    const data = await res.json()
    setSyncResult(data)
    setSyncing(false)
    // Refresh data
    api('/data/bmr').then(r => r.json()).then(d => setBmr(d.bmr))
    api('/data/tdee-log').then(r => r.json()).then(d => setLog(d.entries || []))
  }

  const recent = log.slice(-7).reverse()

  return (
    <div>
      <div className="card">
        <h2>BMR: {bmr ? `${bmr} kcal` : 'calculating...'}</h2>
        <button onClick={handleSync} disabled={syncing}>
          {syncing ? 'Syncing...' : '🔄 Sync All'}
        </button>
        {syncResult && <pre>{JSON.stringify(syncResult, null, 2)}</pre>}
      </div>

      <div className="card">
        <h2>Recent Log</h2>
        <table>
          <thead>
            <tr><th>Date</th><th>Weight</th><th>Calories</th><th>Burned</th></tr>
          </thead>
          <tbody>
            {recent.map(r => (
              <tr key={r.Date}>
                <td>{r.Date}</td>
                <td>{r.Weight_lbs || '-'}</td>
                <td>{r.Calories_Consumed || '-'}</td>
                <td>{r.Active_Calories_Burned || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
