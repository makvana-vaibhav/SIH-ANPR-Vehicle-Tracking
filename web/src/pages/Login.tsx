import { useState, type FormEvent } from 'react'

import { useAuth } from '@/hooks/useAuth'

/** Demo accounts, shown on the login screen so a judge can switch roles. */
const DEMO_ACCOUNTS = [
  { username: 'admin', role: 'Full access, user administration' },
  { username: 'supervisor', role: 'Manage cameras, close alerts, read audit' },
  { username: 'operator', role: 'View streams, acknowledge alerts, search' },
  { username: 'analyst', role: 'Search and analyse — no live video' },
  { username: 'auditor', role: 'Read the audit trail — no plate search' },
]

export default function Login() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('Sentinel@2026')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-4xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl md:grid md:grid-cols-2">
        {/* Identity panel */}
        <div className="border-b border-border bg-gradient-to-br from-secondary/60 to-card p-8 md:border-b-0 md:border-r">
          <h1 className="text-3xl font-semibold tracking-tight text-primary">
            Sentinel<span className="text-foreground">-GJ</span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            સેન્ટિનલ — રાજ્યવ્યાપી સીસીટીવી ગુપ્તચર મંચ
          </p>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            Statewide CCTV intelligence platform for the Gujarat Police and Home
            Department. Federates existing departmental CCTV rather than
            replacing it.
          </p>

          <div className="mt-8">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Demo accounts
            </p>
            <ul className="mt-3 space-y-2">
              {DEMO_ACCOUNTS.map((account) => (
                <li key={account.username}>
                  <button
                    type="button"
                    onClick={() => {
                      setUsername(account.username)
                      setPassword('Sentinel@2026')
                    }}
                    className="w-full rounded-md border border-border/60 px-3 py-2 text-left transition hover:border-primary/50 hover:bg-secondary/40"
                  >
                    <span className="font-mono text-sm text-foreground">
                      {account.username}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      {account.role}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              All demo accounts use{' '}
              <span className="font-mono text-foreground/80">Sentinel@2026</span>
            </p>
          </div>
        </div>

        {/* Sign-in form */}
        <form onSubmit={handleSubmit} className="p-8">
          <h2 className="text-lg font-semibold">Sign in</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Every action you take is recorded in the audit trail.
          </p>

          <label className="mt-6 block">
            <span className="text-sm font-medium">Username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              className="mt-1.5 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </label>

          <label className="mt-4 block">
            <span className="text-sm font-medium">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="mt-1.5 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </label>

          {error && (
            <p className="mt-4 rounded border border-status-offline/40 bg-status-offline/10 px-3 py-2 text-sm text-status-offline">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-6 w-full rounded-md bg-primary px-4 py-2.5 font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-60"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
