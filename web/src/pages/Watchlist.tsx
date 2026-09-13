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

import { SkeletonRows } from '@/components/Skeleton'
import { useToast } from '@/components/Toast'
import {
  Button,
  EmptyState,
  Field,
  InfoHint,
  Input,
  PageHeader,
  PriorityBadge,
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
import { PERMISSIONS } from '@/lib/permissions'
import type { Priority, WatchlistEntry } from '@/lib/types'

const CATEGORIES = ['stolen', 'suspect', 'wanted', 'bolo', 'expired'] as const
const PRIORITIES: Priority[] = ['low', 'medium', 'high', 'critical']

export default function Watchlist() {
  const toast = useToast()
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
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setEntries(await api.getWatchlist({ limit: '200' }))
    } catch (err) {
      toast.error(err)
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
  }, [load])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const normalised = plate.toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (normalised.length < 4) {
      toast.error('A plate needs at least four letters or digits to match on.')
      return
    }

    setBusy(true)
    try {
      await api.addToWatchlist({
        plate: normalised,
        category,
        priority,
        case_ref: caseRef.trim() || null,
        remarks: remarks.trim() || null,
      })
      toast.success(`${normalised} is now watched. A sighting will raise an alert.`)
      setPlate('')
      setCaseRef('')
      setRemarks('')
      await load()
    } catch (err) {
      toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  async function toggleActive(entry: WatchlistEntry) {
    try {
      await api.updateWatchlistEntry(entry.id, { active: !entry.active })
      toast.success(
        entry.active
          ? `${entry.plate_normalised} retired — it will no longer raise alerts.`
          : `${entry.plate_normalised} is being watched again.`,
      )
      await load()
    } catch (err) {
      toast.error(err)
    }
  }

  const active = entries.filter((e) => e.active)
  const retired = entries.filter((e) => !e.active)

  return (
    <div className="h-full space-y-6 overflow-y-auto p-6">
      <PageHeader
        title="Watchlist"
        subtitle={
          <>
            <span>
              {active.length} plate{active.length === 1 ? '' : 's'} watched across the fleet
            </span>
            <span className="text-muted-foreground/40">·</span>
            <span>matched on every settled read</span>
            <InfoHint label="What adding a plate here does">
              This is the most consequential action on the platform — it is what
              turns a passing car into a red alert. The case reference and reason
              are carried into the alert, which is what makes a hit actionable
              rather than merely noisy.
            </InfoHint>
          </>
        }
      />

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
        <h2 className="text-sm font-semibold">Add a plate</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-1">
            <Field label="Plate">
              <Input
                value={plate}
                onChange={(e) => setPlate(e.target.value.toUpperCase())}
                placeholder="GJ03AB1234"
                className="font-mono uppercase"
              />
            </Field>
          </div>

          <Field label="Category">
            <Select value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Priority">
            <Select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Case reference">
            <Input
              value={caseRef}
              onChange={(e) => setCaseRef(e.target.value)}
              placeholder="FIR/2026/0142"
            />
          </Field>

          <Field label="Remarks">
            <Input
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Reported stolen, Rajkot City"
            />
          </Field>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? 'Adding…' : 'Add to watchlist'}
          </Button>
        </div>
      </form>
      )}

      {/* ── Active ──────────────────────────────────────────────────── */}
      <section>
        <SectionLabel>Watched now</SectionLabel>
        {loading ? (
          <div className="mt-2">
            <SkeletonRows rows={3} height="h-9" />
          </div>
        ) : active.length === 0 ? (
          <div className="mt-2">
            <EmptyState
              title={
                mayAdd
                  ? 'Nothing is being watched. Add a plate above.'
                  : 'Nothing is being watched. A supervisor can add a plate.'
              }
            />
          </div>
        ) : (
          <div className="mt-2">
            <Table className="min-w-[640px]">
              <Thead>
                <tr>
                  <Th>Plate</Th>
                  <Th>Category</Th>
                  <Th>Priority</Th>
                  <Th>Case</Th>
                  <Th>Remarks</Th>
                  <Th>Added</Th>
                  <Th />
                </tr>
              </Thead>
              <tbody>
                {active.map((entry) => (
                  <Tr key={entry.id}>
                    <Td className="font-mono font-semibold">{entry.plate_normalised}</Td>
                    <Td className="text-xs">{entry.category}</Td>
                    <Td>
                      <PriorityBadge priority={entry.priority} />
                    </Td>
                    <Td className="text-xs text-muted-foreground">
                      {entry.case_ref || '—'}
                    </Td>
                    <Td className="max-w-48 truncate text-xs text-muted-foreground">
                      {entry.remarks || '—'}
                    </Td>
                    <Td className="text-[11px] text-muted-foreground">
                      {api.formatIST(entry.created_at)}
                    </Td>
                    <Td className="text-right">
                      {mayAmend && (
                        <Button variant="outline" size="sm" onClick={() => void toggleActive(entry)}>
                          Retire
                        </Button>
                      )}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </section>

      {/* ── Retired ─────────────────────────────────────────────────── */}
      {retired.length > 0 && (
        <section>
          <SectionLabel>
            Retired ({retired.length})
            <InfoHint>
              Kept rather than deleted. A watchlist entry is evidence of what
              was being looked for and when, so entries are retired — the delete
              endpoint exists and is admin-only for exactly that reason.
            </InfoHint>
          </SectionLabel>
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
