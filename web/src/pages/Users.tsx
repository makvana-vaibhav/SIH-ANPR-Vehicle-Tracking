/**
 * Account administration.
 *
 * The screen is deliberately blunt about consequences, because the actions
 * here are the ones that decide whether the audit trail means anything:
 *
 * * **Deactivate, don't delete.** Deleting a user who has acted orphans every
 *   audit row naming them. The API refuses it outright once an account has
 *   history; the button here says so before you reach that refusal.
 * * **A password an administrator sets is a shared secret.** Until the holder
 *   replaces it, two people can sign in as them and nothing that account does
 *   is attributable. Accounts in that state are flagged in the table.
 * * **Role changes take effect on the holder's next request**, not their next
 *   login — the API checks permissions per request, so removing a grant stops
 *   the next action rather than the next session.
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { useAuth } from '@/hooks/useAuth'
import * as api from '@/lib/api'
import { PERMISSIONS, ROLE_SUMMARY } from '@/lib/permissions'
import type { ManagedUser, Role } from '@/lib/types'

/** Roles a person can be given. */
const ROLES: Role[] = ['admin', 'supervisor', 'operator', 'analyst', 'auditor']

/**
 * `api_client` is a machine identity, not a person, and is deliberately absent
 * from the list above — nobody should be able to turn an officer into one, or
 * one into an officer, from a dropdown.
 *
 * It still has to *render*: a `<select>` whose value matches no option shows
 * the first option instead, which displayed the camera-onboarding client as an
 * administrator.
 */
const MACHINE_ROLES: Role[] = ['api_client']

const ROLE_STYLE: Record<string, string> = {
  admin: 'bg-status-offline/15 text-status-offline border-status-offline/40',
  supervisor: 'bg-amber-500/15 text-amber-400 border-amber-500/40',
  operator: 'bg-primary/15 text-primary border-primary/40',
  analyst: 'bg-muted text-muted-foreground border-border',
  auditor: 'bg-muted text-muted-foreground border-border',
  api_client: 'bg-muted text-muted-foreground border-border',
}

export default function Users() {
  const { user: me, can } = useAuth()
  const mayCreate = can(PERMISSIONS.userCreate)
  const mayUpdate = can(PERMISSIONS.userUpdate)

  const [users, setUsers] = useState<ManagedUser[]>([])
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [resetting, setResetting] = useState<string | null>(null)
  const [resetValue, setResetValue] = useState('')

  const load = useCallback(async () => {
    try {
      setUsers((await api.getUsers()).items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function create(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await api.createUser({
        username: username.trim(),
        password,
        full_name: fullName.trim() || null,
        role,
      })
      setNotice(
        `${username.trim()} created. They must set their own password before ` +
          'the account can be relied on for attribution.',
      )
      setUsername('')
      setFullName('')
      setPassword('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function change(target: ManagedUser, changes: Parameters<typeof api.updateUser>[1]) {
    setError(null)
    try {
      await api.updateUser(target.id, changes)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function submitReset(target: ManagedUser) {
    setError(null)
    try {
      await api.resetUserPassword(target.id, resetValue)
      setNotice(`${target.username} must set a new password at next sign-in.`)
      setResetting(null)
      setResetValue('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="space-y-6 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Accounts</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {users.length} accounts. Every action on this platform is recorded
          against one of them, so each must belong to a named person.
        </p>
      </header>

      {error && (
        <p className="rounded border border-status-offline/40 bg-status-offline/10 px-4 py-2 text-sm text-status-offline">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded border border-status-online/40 bg-status-online/10 px-4 py-2 text-sm text-status-online">
          {notice}
        </p>
      )}

      {/* ── Create ──────────────────────────────────────────────────── */}
      {mayCreate && (
        <form onSubmit={create} className="rounded-md border border-border bg-card p-4">
          <h2 className="text-sm font-medium">Create an account</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Username">
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="insp.desai"
                pattern="[a-zA-Z0-9._\-]+"
                required
                className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-sm outline-none focus:border-primary"
              />
            </Field>
            <Field label="Full name">
              <input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Insp. R Desai"
                className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
              />
            </Field>
            <Field label="Role">
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Initial password">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={12}
                required
                className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
              />
            </Field>
          </div>

          <p className="mt-2 text-[11px] text-muted-foreground">
            {ROLE_SUMMARY[role]}. At least 12 characters, and not a credential
            documented in this repository. The holder must change it before the
            account is attributable.
          </p>

          <button
            type="submit"
            disabled={busy}
            className="mt-3 rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            {busy ? 'Creating…' : 'Create account'}
          </button>
        </form>
      )}

      {/* ── The accounts ────────────────────────────────────────────── */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="pb-1.5 pr-3">Username</th>
              <th className="pb-1.5 pr-3">Name</th>
              <th className="pb-1.5 pr-3">Role</th>
              <th className="pb-1.5 pr-3">Status</th>
              <th className="pb-1.5 pr-3">Last sign-in</th>
              <th className="pb-1.5" />
            </tr>
          </thead>
          <tbody>
            {users.map((account) => {
              const self = account.id === me?.id
              return (
                <tr key={account.id} className="border-b border-border/50 align-top">
                  <td className="py-2 pr-3 font-mono text-xs">
                    {account.username}
                    {self && (
                      <span className="ml-1.5 text-[10px] text-muted-foreground">
                        (you)
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">
                    {account.full_name || '—'}
                  </td>
                  <td className="py-2 pr-3">
                    {mayUpdate && !self && !MACHINE_ROLES.includes(account.role) ? (
                      <select
                        value={account.role}
                        onChange={(e) =>
                          void change(account, { role: e.target.value as Role })
                        }
                        title={ROLE_SUMMARY[account.role]}
                        className="rounded border border-border bg-background px-1.5 py-0.5 text-[11px] outline-none focus:border-primary"
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span
                        title={ROLE_SUMMARY[account.role]}
                        className={`rounded border px-1.5 py-px text-[10px] font-medium uppercase ${ROLE_STYLE[account.role] ?? ''}`}
                      >
                        {account.role}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-col gap-0.5">
                      <span
                        className={`text-[11px] ${account.is_active ? 'text-status-online' : 'text-muted-foreground'}`}
                      >
                        {account.is_active ? 'active' : 'deactivated'}
                      </span>
                      {account.must_change_password && (
                        <span
                          className="text-[10px] text-amber-400"
                          title="An administrator set this password, so two people know it. Nothing this account does is attributable until the holder changes it."
                        >
                          password not yet personal
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-3 text-[11px] text-muted-foreground">
                    {account.last_login_at
                      ? api.formatIST(account.last_login_at)
                      : 'never'}
                  </td>
                  <td className="py-2 text-right">
                    {mayUpdate && !self && (
                      <div className="flex flex-col items-end gap-1">
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() =>
                              void change(account, { is_active: !account.is_active })
                            }
                            title={
                              account.is_active
                                ? 'Deactivating keeps their history readable; deleting would orphan it'
                                : 'Restore access'
                            }
                            className="rounded border border-border px-2 py-0.5 text-[11px] transition hover:border-muted-foreground"
                          >
                            {account.is_active ? 'Deactivate' : 'Reactivate'}
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              setResetting(resetting === account.id ? null : account.id)
                            }
                            className="rounded border border-border px-2 py-0.5 text-[11px] transition hover:border-muted-foreground"
                          >
                            Reset password
                          </button>
                        </div>

                        {resetting === account.id && (
                          <div className="flex items-center gap-1.5">
                            <input
                              type="password"
                              value={resetValue}
                              onChange={(e) => setResetValue(e.target.value)}
                              placeholder="new password"
                              minLength={12}
                              className="w-40 rounded border border-border bg-background px-2 py-0.5 text-[11px] outline-none focus:border-primary"
                            />
                            <button
                              type="button"
                              onClick={() => void submitReset(account)}
                              className="rounded bg-primary px-2 py-0.5 text-[11px] text-primary-foreground"
                            >
                              Set
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] leading-relaxed text-muted-foreground">
        Accounts are deactivated rather than deleted. A deleted user id turns
        every audit entry naming them into an orphan, and the API refuses the
        deletion once an account has any history.
      </p>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  )
}
