/**
 * The audit trail.
 *
 * This is the screen that answers "who looked at whom", which for a
 * surveillance platform is the question that matters most after "does it
 * work". `AUDIT_READ` existed as a permission from Phase 1 with nothing behind
 * it; this is what it was always gating.
 *
 * Reading this page is itself recorded. That is deliberate and not hidden — a
 * reviewer who leaves no trace is a hole in the very control the page exists
 * to provide, and the note at the top says so rather than letting somebody
 * discover it in their own entry later.
 */

import { useCallback, useEffect, useState } from 'react'

import * as api from '@/lib/api'
import type { AuditEntry } from '@/lib/types'

const WINDOWS = [
  { label: '1 h', hours: 1 },
  { label: '24 h', hours: 24 },
  { label: '7 d', hours: 168 },
  { label: '30 d', hours: 720 },
] as const

/** The actions worth filtering to, in the order a reviewer asks for them. */
const ACTIONS = [
  { label: 'Everything', value: '' },
  { label: 'Plate searches', value: 'search.' },
  { label: 'Stream opens', value: 'stream.' },
  { label: 'Watchlist changes', value: 'watchlist.' },
  { label: 'Alert actions', value: 'alert.' },
  { label: 'Sign-ins', value: 'auth.' },
  { label: 'Account admin', value: 'user.' },
  { label: 'Audit reads', value: 'audit.' },
] as const

const RESULT_STYLE: Record<string, string> = {
  success: 'text-muted-foreground',
  denied: 'text-amber-400',
  failure: 'text-status-offline',
}

export default function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [hours, setHours] = useState<number>(24)
  const [action, setAction] = useState('')
  const [username, setUsername] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await api.getAudit({
        action: action || undefined,
        username: username.trim() || undefined,
        since: new Date(Date.now() - hours * 3_600_000).toISOString(),
        limit: 200,
      })
      setEntries(page.items)
      setTotal(page.total)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [action, username, hours])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-4 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Audit trail</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Every plate search, stream open, watchlist change and sign-in.{' '}
          <span className="text-amber-400">
            Opening this page is itself recorded.
          </span>
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Action
          </span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="mt-1 rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
          >
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            User
          </span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="any"
            className="mt-1 w-40 rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
          />
        </label>

        <div>
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Window
          </span>
          <div className="mt-1 flex rounded border border-border">
            {WINDOWS.map((w) => (
              <button
                key={w.hours}
                type="button"
                onClick={() => setHours(w.hours)}
                className={`px-2.5 py-1.5 text-xs transition first:rounded-l last:rounded-r ${
                  hours === w.hours
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>

        <span className="pb-1.5 text-[11px] text-muted-foreground">
          {loading ? 'loading…' : `${entries.length} shown of ${total} matching`}
        </span>
      </div>

      {error && (
        <p className="rounded border border-status-offline/40 bg-status-offline/10 px-4 py-2 text-sm text-status-offline">
          {error}
        </p>
      )}

      {entries.length === 0 && !loading ? (
        <p className="rounded border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          Nothing matching in this window.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="pb-1.5 pr-3">When (IST)</th>
                <th className="pb-1.5 pr-3">Who</th>
                <th className="pb-1.5 pr-3">Role</th>
                <th className="pb-1.5 pr-3">Action</th>
                <th className="pb-1.5 pr-3">Resource</th>
                <th className="pb-1.5 pr-3">From</th>
                <th className="pb-1.5">Result</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-border/50">
                  <td className="whitespace-nowrap py-1.5 pr-3 text-[11px] text-muted-foreground">
                    {api.formatIST(entry.ts)}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-xs">
                    {entry.username ?? '—'}
                  </td>
                  <td className="py-1.5 pr-3 text-[11px] text-muted-foreground">
                    {entry.role ?? '—'}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{entry.action}</td>
                  <td className="max-w-56 truncate py-1.5 pr-3 text-[11px] text-muted-foreground">
                    {entry.resource_id ?? entry.resource_type ?? '—'}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-muted-foreground">
                    {entry.ip ?? '—'}
                  </td>
                  <td
                    className={`py-1.5 text-[11px] ${RESULT_STYLE[entry.result] ?? ''}`}
                  >
                    {entry.result}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
