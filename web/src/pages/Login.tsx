import { useState, type FormEvent } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { Button, ErrorBanner, Field, Icon, Input } from '@/components/ui'

/**
 * The seeded accounts, so a reviewer can move between roles.
 *
 * Behind a disclosure that is closed by default. A sign-in screen listing five
 * usernames and printing the shared password beside them is the single least
 * production-looking thing in the product — but these are seeded demo
 * credentials on a local stack, and a reviewer with no documentation in front
 * of them still has to be able to see what an auditor sees. Closed by default
 * settles both: the screen reads as a sign-in, and the accounts are one click
 * away for whoever needs them.
 */
const DEMO_ACCOUNTS = [
  { username: 'admin', role: 'Full access, user administration' },
  { username: 'supervisor', role: 'Manage cameras, close alerts, read audit' },
  { username: 'operator', role: 'View streams, acknowledge alerts, search' },
  { username: 'analyst', role: 'Search and analyse — no live video' },
  { username: 'auditor', role: 'Read the audit trail — no plate search' },
]

const DEMO_PASSWORD = 'NagarNetra@2026'

export default function Login() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState(DEMO_PASSWORD)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showAccounts, setShowAccounts] = useState(false)

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
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid h-8 w-8 place-items-center rounded-md bg-primary text-base font-bold text-primary-foreground"
          >
            C
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight tracking-tight">Contrail</h1>
            <p className="text-[11px] text-muted-foreground">
              City-wide vehicle intelligence
            </p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="mt-5 rounded-md border border-border bg-card p-5"
        >
          <h2 className="text-sm font-semibold">Sign in</h2>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            Every action you take is recorded in the audit trail.
          </p>

          <div className="mt-4 space-y-3">
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="font-mono"
              />
            </Field>
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
            <div className="mt-3">
              <ErrorBanner>{error}</ErrorBanner>
            </div>
          )}

          <Button type="submit" disabled={busy} className="mt-4 w-full py-2">
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        {/* ── Seeded accounts ──────────────────────────────────────── */}
        <div className="mt-3 overflow-hidden rounded-md border border-border">
          <button
            type="button"
            onClick={() => setShowAccounts((v) => !v)}
            aria-expanded={showAccounts}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-muted-foreground transition hover:bg-secondary/40 hover:text-foreground"
          >
            <Icon
              name="chevronDown"
              size={13}
              className={`transition-transform ${showAccounts ? '' : '-rotate-90'}`}
            />
            Demo accounts
            <span className="ml-auto text-[11px] uppercase tracking-wide text-muted-foreground/70">
              seeded credentials
            </span>
          </button>

          {showAccounts && (
            <ul className="border-t border-border">
              {DEMO_ACCOUNTS.map((account) => (
                <li key={account.username}>
                  <button
                    type="button"
                    onClick={() => {
                      setUsername(account.username)
                      setPassword(DEMO_PASSWORD)
                    }}
                    className="flex w-full flex-col border-b border-border/60 px-3 py-1.5 text-left transition last:border-0 hover:bg-secondary/40"
                  >
                    <span className="font-mono text-xs">{account.username}</span>
                    <span className="text-[11px] leading-snug text-muted-foreground">
                      {account.role}
                    </span>
                  </button>
                </li>
              ))}
              <li className="border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
                All use <span className="font-mono text-foreground/80">{DEMO_PASSWORD}</span>
              </li>
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
