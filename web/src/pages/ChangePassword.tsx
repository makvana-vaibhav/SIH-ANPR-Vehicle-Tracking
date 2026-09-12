/**
 * Change your own password.
 *
 * Separate from the admin reset screen because the two are different acts. An
 * administrator resetting a password creates a shared secret; a person setting
 * their own ends one. Only the second makes an account attributable, which is
 * why the API re-verifies the current password here and does not there.
 */

import { useState, type FormEvent } from 'react'

import { Button, ErrorBanner, Field, Input } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import * as api from '@/lib/api'

const MIN_LENGTH = 12

export default function ChangePassword() {
  const { user } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (next !== confirm) {
      setError('The two new passwords do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.changeOwnPassword(current, next)
      setDone(true)
      setCurrent('')
      setNext('')
      setConfirm('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-md">
        <h1 className="text-lg font-semibold tracking-tight">Change password</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Signed in as{' '}
          <span className="font-medium text-foreground">{user?.username}</span>.
        </p>

        {user?.must_change_password && (
          <p className="mt-3 rounded border border-priority-high/40 bg-priority-high/10 px-4 py-2 text-xs text-priority-high">
            Your password was set by an administrator, so more than one person
            knows it. Until you change it, actions taken by this account cannot
            be attributed to you alone.
          </p>
        )}

        {done ? (
          <p className="mt-4 rounded border border-status-online/40 bg-status-online/10 px-4 py-3 text-sm text-status-online">
            Password changed. It takes effect immediately; existing sessions
            elsewhere stay signed in until their tokens expire.
          </p>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-3">
            <Field label="Current password">
              <Input
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                required
                autoComplete="current-password"
              />
            </Field>

            <Field label="New password">
              <Input
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                minLength={MIN_LENGTH}
                required
                autoComplete="new-password"
              />
            </Field>

            <Field label="New password again">
              <Input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                minLength={MIN_LENGTH}
                required
                autoComplete="new-password"
              />
            </Field>

            {/* The API is the authority on all of this; the hint exists so the
                rules are known before the form is submitted, not after. */}
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              At least {MIN_LENGTH} characters. It must not contain your
              username, must not be a credential documented in this repository,
              and must not be a single repeated character or a keyboard run.
            </p>

            {error && <ErrorBanner>{error}</ErrorBanner>}

            <Button type="submit" disabled={busy}>
              {busy ? 'Changing…' : 'Change password'}
            </Button>
          </form>
        )}
      </div>
    </div>
  )
}
