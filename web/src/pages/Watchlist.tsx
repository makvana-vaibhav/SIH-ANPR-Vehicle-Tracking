/**
 * Watchlist manager: put a plate on the list and the platform watches for it.
 *
 * Adding an entry is the single most consequential action an operator takes on
 * this system — it is what turns a passing car into a red alert — so the form
 * asks for a case reference and a reason rather than just a plate. Both are
 * carried into the alert, which is what makes a hit actionable instead of
 * merely noisy.
 *
 * Entries are retired, not deleted. The delete endpoint exists and is
 * admin-only precisely because watchlist history is evidence; this screen
 * offers deactivation, which is what a supervisor should reach for.
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { useAuth } from '@/hooks/useAuth'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Priority, WatchlistEntry } from '@/lib/types'

const CATEGORIES = ['stolen', 'suspect', 'wanted', 'bolo', 'expired'] as const
const PRIORITIES: Priority[] = ['low', 'medium', 'high', 'critical']

const PRIORITY_STYLE: Record<Priority, string> = {
  critical: 'bg-status-offline/15 text-status-offline border-status-offline/40',
  high: 'bg-amber-500/15 text-amber-400 border-amber-500/40',
  medium: 'bg-primary/15 text-primary border-primary/40',
  low: 'bg-muted text-muted-foreground border-border',
}

export default function Watchlist() {
  const { can } = useAuth()
  // Reading the watchlist and changing it are separate grants: an analyst may
  // see what is being looked for without being able to add to it.
  const mayAdd = can(PERMISSIONS.watchlistCreate)
  const mayAmend = can(PERMISSIONS.watchlistUpdate)

  const [entries, setEntries] = useState<WatchlistEntry[]>([])
  const [plate, setPlate] = useState('')
  const [category, setCategory] = useState<string>('stolen')
  const [priority, setPriority] = useState<Priority>('critical')
  const [caseRef, setCaseRef] = useState('')
  const [remarks, setRemarks] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setEntries(await api.getWatchlist({ limit: '200' }))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const normalised = plate.toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (normalised.length < 4) {
      setError('A plate needs at least four letters or digits to match on.')
      return
    }

    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await api.addToWatchlist({
        plate: normalised,
        category,
        priority,
        case_ref: caseRef.trim() || null,
        remarks: remarks.trim() || null,
      })
      setNotice(`${normalised} is now watched. A sighting will raise an alert.`)
      setPlate('')
      setCaseRef('')
      setRemarks('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function toggleActive(entry: WatchlistEntry) {
    setError(null)
    try {
      await api.updateWatchlistEntry(entry.id, { active: !entry.active })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const active = entries.filter((e) => e.active)
  const retired = entries.filter((e) => !e.active)

  return (
    <div className="space-y-6 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Watchlist</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {active.length} plate{active.length === 1 ? '' : 's'} being watched
          across the fleet. Matching runs on every settled read.
        </p>
      </header>

      {!mayAdd && (
        <p className="rounded border border-border bg-card px-4 py-2 text-xs text-muted-foreground">
          Your role can read the watchlist but not change it. Adding or retiring a
          plate needs <code className="font-mono">watchlist.create</code>.
        </p>
      )}

      {/* ── Add ─────────────────────────────────────────────────────── */}
      {mayAdd && (
      <form
        onSubmit={submit}
        className="rounded-md border border-border bg-card p-4"
      >
        <h2 className="text-sm font-medium">Add a plate</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="block lg:col-span-1">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Plate
            </span>
            <input
              value={plate}
              onChange={(e) => setPlate(e.target.value.toUpperCase())}
              placeholder="GJ03AB1234"
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-sm uppercase outline-none focus:border-primary"
            />
          </label>

          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Category
            </span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Priority
            </span>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority)}
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Case reference
            </span>
            <input
              value={caseRef}
              onChange={(e) => setCaseRef(e.target.value)}
              placeholder="FIR/2026/0142"
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
            />
          </label>

          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Remarks
            </span>
            <input
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Reported stolen, Rajkot City"
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
            />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            {busy ? 'Adding…' : 'Add to watchlist'}
          </button>
          {notice && <span className="text-xs text-status-online">{notice}</span>}
          {error && <span className="text-xs text-status-offline">{error}</span>}
        </div>
      </form>
      )}

      {/* ── Active ──────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-medium">Watched now</h2>
        {active.length === 0 ? (
          <p className="mt-2 rounded border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
            {mayAdd
              ? 'Nothing is being watched. Add a plate above.'
              : 'Nothing is being watched. A supervisor can add a plate.'}
          </p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="pb-1.5 pr-3">Plate</th>
                  <th className="pb-1.5 pr-3">Category</th>
                  <th className="pb-1.5 pr-3">Priority</th>
                  <th className="pb-1.5 pr-3">Case</th>
                  <th className="pb-1.5 pr-3">Remarks</th>
                  <th className="pb-1.5 pr-3">Added</th>
                  <th className="pb-1.5" />
                </tr>
              </thead>
              <tbody>
                {active.map((entry) => (
                  <tr key={entry.id} className="border-b border-border/50">
                    <td className="py-1.5 pr-3 font-mono font-semibold">
                      {entry.plate_normalised}
                    </td>
                    <td className="py-1.5 pr-3 text-xs">{entry.category}</td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={`rounded border px-1.5 py-px text-[10px] font-medium uppercase ${PRIORITY_STYLE[entry.priority]}`}
                      >
                        {entry.priority}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 text-xs text-muted-foreground">
                      {entry.case_ref || '—'}
                    </td>
                    <td className="max-w-48 truncate py-1.5 pr-3 text-xs text-muted-foreground">
                      {entry.remarks || '—'}
                    </td>
                    <td className="py-1.5 pr-3 text-[11px] text-muted-foreground">
                      {api.formatIST(entry.created_at)}
                    </td>
                    <td className="py-1.5 text-right">
                      {mayAmend && (
                        <button
                          type="button"
                          onClick={() => void toggleActive(entry)}
                          className="rounded border border-border px-2 py-0.5 text-[11px] transition hover:border-muted-foreground"
                        >
                          Retire
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Retired ─────────────────────────────────────────────────── */}
      {retired.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-muted-foreground">
            Retired ({retired.length})
          </h2>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            Kept rather than deleted — a watchlist entry is evidence of what was
            being looked for, and when.
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {retired.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  disabled={!mayAmend}
                  onClick={() => void toggleActive(entry)}
                  title={mayAmend ? 'Reactivate' : 'Reactivating needs watchlist.update'}
                  className="rounded border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground transition hover:border-primary hover:text-foreground"
                >
                  {entry.plate_normalised} ↩
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
