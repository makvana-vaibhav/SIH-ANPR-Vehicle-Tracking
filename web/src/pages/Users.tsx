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

import { useToast } from '@/components/Toast'
import {
  Button,
  Field,
  InfoHint,
  Input,
  PageHeader,
  RoleBadge,
  SectionLabel,
  Select,
  Table,
  Td,
  Th,
  Thead,
  Tr,
} from '@/components/ui'
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

export default function Users() {
  const toast = useToast()
  const { user: me, can } = useAuth()
  const mayCreate = can(PERMISSIONS.userCreate)
  const mayUpdate = can(PERMISSIONS.userUpdate)

  const [users, setUsers] = useState<ManagedUser[]>([])
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [busy, setBusy] = useState(false)
  const [resetting, setResetting] = useState<string | null>(null)
  const [resetValue, setResetValue] = useState('')

  const load = useCallback(async () => {
    try {
      setUsers((await api.getUsers()).items)
    } catch (err) {
      toast.error(err)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  async function create(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api.createUser({
        username: username.trim(),
        password,
        full_name: fullName.trim() || null,
        role,
      })
      toast.success(
        `${username.trim()} created. They must set their own password before ` +
          'the account can be relied on for attribution.',
      )
      setUsername('')
      setFullName('')
      setPassword('')
      await load()
    } catch (err) {
      toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  async function change(target: ManagedUser, changes: Parameters<typeof api.updateUser>[1]) {
    try {
      await api.updateUser(target.id, changes)
      toast.success(`${target.username} updated.`)
      await load()
    } catch (err) {
      toast.error(err)
    }
  }

  async function submitReset(target: ManagedUser) {
    try {
      await api.resetUserPassword(target.id, resetValue)
      toast.success(`${target.username} must set a new password at next sign-in.`)
      setResetting(null)
      setResetValue('')
      await load()
    } catch (err) {
      toast.error(err)
    }
  }

  return (
    <div className="h-full space-y-6 overflow-y-auto p-6">
      <PageHeader
        title="Accounts"
        subtitle={
          <>
            <span>{users.length} accounts</span>
            <span className="text-muted-foreground/40">·</span>
            <span>every action is recorded against one of them</span>
            <InfoHint label="Why accounts are deactivated rather than deleted">
              Each account must belong to a named person, because the audit
              trail attributes every action to one. Deleting a user who has
              acted orphans every row naming them, so the API refuses it once an
              account has history — deactivation is what a supervisor should
              reach for. A role change takes effect on the holder's next
              request, not their next sign-in.
            </InfoHint>
          </>
        }
      />

      {/* ── Create ──────────────────────────────────────────────────── */}
      {mayCreate && (
        <form onSubmit={create} className="rounded-md border border-border bg-card p-4">
          <h2 className="text-sm font-semibold">Create an account</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="insp.desai"
                pattern="[a-zA-Z0-9._\-]+"
                required
                className="font-mono"
              />
            </Field>
            <Field label="Full name">
              <Input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Insp. R Desai"
              />
            </Field>
            <Field label="Role">
              <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Initial password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={12}
                required
              />
            </Field>
          </div>

          <p className="mt-2 text-[11px] text-muted-foreground">
            {ROLE_SUMMARY[role]}. At least 12 characters, and not a credential
            documented in this repository. The holder must change it before the
            account is attributable.
          </p>

          <Button type="submit" disabled={busy} className="mt-3">
            {busy ? 'Creating…' : 'Create account'}
          </Button>
        </form>
      )}

      {/* ── The accounts ────────────────────────────────────────────── */}
      <section>
        <SectionLabel>All accounts</SectionLabel>
        <div className="mt-1.5">
      <Table className="min-w-[760px]">
        <Thead>
          <tr>
            <Th>Username</Th>
            <Th>Name</Th>
            <Th>Role</Th>
            <Th>Status</Th>
            <Th>Last sign-in</Th>
            <Th />
          </tr>
        </Thead>
        <tbody>
          {users.map((account) => {
            const self = account.id === me?.id
            return (
              <Tr key={account.id} className="align-top">
                <Td className="font-mono text-xs">
                  {account.username}
                  {self && (
                    <span className="ml-1.5 text-[11px] text-muted-foreground">
                      (you)
                    </span>
                  )}
                </Td>
                <Td className="text-xs text-muted-foreground">
                  {account.full_name || '—'}
                </Td>
                <Td>
                  {mayUpdate && !self && !MACHINE_ROLES.includes(account.role) ? (
                    <Select
                      value={account.role}
                      onChange={(e) =>
                        void change(account, { role: e.target.value as Role })
                      }
                      title={ROLE_SUMMARY[account.role]}
                      className="mt-0 px-1.5 py-0.5 text-[11px]"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <RoleBadge role={account.role} title={ROLE_SUMMARY[account.role]} />
                  )}
                </Td>
                <Td>
                  <div className="flex flex-col gap-0.5">
                    <span
                      className={`text-[11px] ${account.is_active ? 'text-status-online' : 'text-muted-foreground'}`}
                    >
                      {account.is_active ? 'active' : 'deactivated'}
                    </span>
                    {account.must_change_password && (
                      <span
                        className="text-[11px] text-priority-high"
                        title="An administrator set this password, so two people know it. Nothing this account does is attributable until the holder changes it."
                      >
                        password not yet personal
                      </span>
                    )}
                  </div>
                </Td>
                <Td className="text-[11px] text-muted-foreground">
                  {account.last_login_at
                    ? api.formatIST(account.last_login_at)
                    : 'never'}
                </Td>
                <Td className="text-right">
                  {mayUpdate && !self && (
                    <div className="flex flex-col items-end gap-1">
                      <div className="flex gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            void change(account, { is_active: !account.is_active })
                          }
                          title={
                            account.is_active
                              ? 'Deactivating keeps their history readable; deleting would orphan it'
                              : 'Restore access'
                          }
                        >
                          {account.is_active ? 'Deactivate' : 'Reactivate'}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            setResetting(resetting === account.id ? null : account.id)
                          }
                        >
                          Reset password
                        </Button>
                      </div>

                      {resetting === account.id && (
                        <div className="flex items-center gap-1.5">
                          <Input
                            type="password"
                            value={resetValue}
                            onChange={(e) => setResetValue(e.target.value)}
                            placeholder="new password"
                            minLength={12}
                            className="mt-0 w-40 px-2 py-0.5 text-[11px]"
                          />
                          <Button size="sm" onClick={() => void submitReset(account)}>
                            Set
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                </Td>
              </Tr>
            )
          })}
        </tbody>
      </Table>
        </div>
      </section>

    </div>
  )
}
