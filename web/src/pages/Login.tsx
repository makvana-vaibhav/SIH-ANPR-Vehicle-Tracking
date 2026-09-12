import { useState, type FormEvent } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { Button, ErrorBanner, Field, Input } from '@/components/ui'

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
  const [password, setPassword] = useState('NagarNetra@2026')
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
            Con<span className="text-foreground">trail</span>
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            City-wide vehicle intelligence platform. Connects observations from
            hundreds of city cameras into searchable vehicle journeys, traffic
            intelligence and real-time alerts.
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
                      setPassword('NagarNetra@2026')
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
              <span className="font-mono text-foreground/80">NagarNetra@2026</span>
            </p>
          </div>
        </div>

        {/* Sign-in form */}
        <form onSubmit={handleSubmit} className="p-8">
          <h2 className="text-lg font-semibold">Sign in</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Every action you take is recorded in the audit trail.
          </p>

          <div className="mt-6">
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="font-mono"
              />
            </Field>
          </div>

          <div className="mt-4">
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                className="font-mono"
              />
            </Field>
          </div>

          {error && (
            <div className="mt-4">
              <ErrorBanner>{error}</ErrorBanner>
            </div>
          )}

          <Button type="submit" disabled={busy} className="mt-6 w-full py-2.5">
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </div>
    </div>
  )
}
