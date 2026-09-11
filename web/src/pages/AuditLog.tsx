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

import {
  Button,
  EmptyState,
  ErrorBanner,
  Field,
  Input,
  SegmentedControl,
  Select,
  Table,
  Td,
  Th,
  Thead,
  Tr,
} from '@/components/ui'
import * as api from '@/lib/api'
import { downloadCsv, stampedName } from '@/lib/csv'
import type { AuditEntry } from '@/lib/types'

const WINDOWS = [
  { value: '1', label: '1 h', hours: 1 },
  { value: '24', label: '24 h', hours: 24 },
  { value: '168', label: '7 d', hours: 168 },
  { value: '720', label: '30 d', hours: 720 },
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
  denied: 'text-priority-high',
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
          <span className="text-priority-high">
            Opening this page is itself recorded.
          </span>
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Action">
          <Select value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </Select>
        </Field>

        <div className="w-40">
          <Field label="User">
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="any"
            />
          </Field>
        </div>

        <div>
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Window
          </span>
          <div className="mt-1">
            <SegmentedControl
              value={String(hours)}
              onChange={(v) => setHours(Number(v))}
              options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
            />
          </div>
        </div>

        <span className="pb-1.5 text-[11px] text-muted-foreground">
          {loading ? 'loading…' : `${entries.length} shown of ${total} matching`}
        </span>

        <Button
          variant="outline"
          size="sm"
          className="mb-0.5"
          disabled={entries.length === 0}
          onClick={() =>
            downloadCsv(stampedName('audit'), entries, [
              { header: 'timestamp_utc', value: (e) => e.ts },
              { header: 'username', value: (e) => e.username },
              { header: 'role', value: (e) => e.role },
              { header: 'action', value: (e) => e.action },
              { header: 'resource_type', value: (e) => e.resource_type },
              { header: 'resource_id', value: (e) => e.resource_id },
              { header: 'ip', value: (e) => e.ip },
              { header: 'result', value: (e) => e.result },
              { header: 'params', value: (e) => e.params },
            ])
          }
          title="Export the rows shown. Timestamps are UTC in the file; the table displays IST."
        >
          Export CSV
        </Button>
      </div>

      {error && <ErrorBanner>{error}</ErrorBanner>}

      {entries.length === 0 && !loading ? (
        <EmptyState title="Nothing matching in this window." />
      ) : (
        <Table className="min-w-[880px]">
          <Thead>
            <tr>
              <Th>When (IST)</Th>
              <Th>Who</Th>
              <Th>Role</Th>
              <Th>Action</Th>
              <Th>Resource</Th>
              <Th>From</Th>
              <Th>Result</Th>
            </tr>
          </Thead>
          <tbody>
            {entries.map((entry) => (
              <Tr key={entry.id}>
                <Td className="whitespace-nowrap text-[11px] text-muted-foreground">
                  {api.formatIST(entry.ts)}
                </Td>
                <Td className="font-mono text-xs">{entry.username ?? '—'}</Td>
                <Td className="text-[11px] text-muted-foreground">
                  {entry.role ?? '—'}
                </Td>
                <Td className="font-mono text-[11px]">{entry.action}</Td>
                <Td className="max-w-56 truncate text-[11px] text-muted-foreground">
                  {entry.resource_id ?? entry.resource_type ?? '—'}
                </Td>
                <Td className="font-mono text-[10px] text-muted-foreground">
                  {entry.ip ?? '—'}
                </Td>
                <Td className={`text-[11px] ${RESULT_STYLE[entry.result] ?? ''}`}>
                  {entry.result}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}
