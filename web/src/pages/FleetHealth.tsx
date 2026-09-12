/**
 * Fleet health and gap analysis.
 *
 * Both are named Model 1 requirements (challenge FAQ Q15: "camera health
 * monitoring" and "gap-analysis reports").
 *
 * The gap report answers the question a commissioner actually asks — *where
 * are we blind?* — which is not the same as *which cameras are broken*.
 */

import { useEffect, useState } from 'react'

import { ErrorBanner, StatTile, Table, Td, Th, Thead, Tr } from '@/components/ui'
import * as api from '@/lib/api'
import type { FleetHealth as FleetHealthData, GapReport } from '@/lib/types'

export default function FleetHealthPage() {
  const [health, setHealth] = useState<FleetHealthData | null>(null)
  const [gaps, setGaps] = useState<GapReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const [h, g] = await Promise.all([api.getFleetHealth(), api.getGapReport(24)])
        setHealth(h)
        setGaps(g)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  if (error) {
    return (
      <div className="p-6">
        <ErrorBanner>{error}</ErrorBanner>
      </div>
    )
  }

  return (
    <div className="h-full space-y-6 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Fleet health &amp; gap analysis</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Live availability across the estate, and where coverage is missing.
          {health && (
            <span className="ml-1">
              Updated {api.relativeTime(health.generated_at)}.
            </span>
          )}
        </p>
      </header>

      {/* Availability.
       *
       * This used to lead with four numbers that were nearly the same thing:
       * integrated, availability-of-integrated, offline, awaiting-integration.
       * Three of them existed to describe a registry that held 250 cameras
       * with no video source at all — cameras that could not be unhealthy
       * because they were never anything. With those gone, the honest summary
       * is one figure and a breakdown.
       *
       * "Not yet probed" only appears when it is non-zero. A card permanently
       * reading 0, explaining a state that no longer normally occurs, is how
       * a screen stops being read. */}
      <section className="grid gap-4 md:grid-cols-3">
        <StatTile
          label="Availability"
          value={
            health?.availability_pct != null ? `${health.availability_pct}%` : '—'
          }
          detail={`${health?.online ?? '—'} of ${health?.total ?? '—'} cameras reachable`}
          tone={health ? (health.availability_pct < 90 ? 'bad' : 'good') : undefined}
        />
        <StatTile
          label="Offline"
          value={health?.offline ?? '—'}
          detail="Reachable integration, no video arriving"
          tone={health && health.offline > 0 ? 'bad' : undefined}
        />
        <StatTile
          label="Degraded"
          value={health?.degraded ?? '—'}
          detail="Video arriving, but late or dropping frames"
          tone={health && health.degraded > 0 ? 'warn' : undefined}
        />
      </section>

      {health != null && health.unknown > 0 && (
        <p className="rounded border border-status-unknown/40 bg-status-unknown/10 px-4 py-2 text-xs text-muted-foreground">
          <strong className="text-foreground">{health.unknown}</strong> camera
          {health.unknown === 1 ? ' has' : 's have'} not been probed yet, so
          {health.unknown === 1 ? ' its' : ' their'} state is unknown rather than
          offline — normally this is a camera onboarded in the last minute.
          Availability above counts {health.unknown === 1 ? 'it' : 'them'} as
          not reachable, which is the cautious reading.
        </p>
      )}

      {/* Per department */}
      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">
          Availability by department
        </h2>
        <div className="divide-y divide-border">
          {health?.by_department.map((row) => (
            <div key={row.department} className="flex items-center gap-4 px-4 py-3">
              <span className="w-28 shrink-0 font-mono text-sm">{row.department}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-status-online transition-all"
                  style={{
                    width: `${row.total ? (row.online / row.total) * 100 : 0}%`,
                  }}
                />
              </div>
              <span className="w-24 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {row.online}/{row.total} live
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* Vendor breadth — the interoperability evidence */}
      <section className="rounded-lg border border-border bg-card">
        <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">
          Federated VMS vendors
          <span className="ml-2 font-normal text-muted-foreground">
            one adapter interface, many vendors
          </span>
        </h2>
        <div className="grid gap-px bg-border md:grid-cols-3">
          {health?.by_vendor.map((row) => (
            <div key={row.vendor} className="bg-card px-4 py-3">
              <p className="font-mono text-sm">{row.vendor}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {row.total} cameras · {row.online} live
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Failure causes */}
      {health && health.top_errors.length > 0 && (
        <section className="rounded-lg border border-border bg-card">
          <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">
            Failure causes (last hour)
            <span className="ml-2 font-normal text-muted-foreground">
              grouped, so one root cause reads as one problem
            </span>
          </h2>
          <div className="divide-y divide-border">
            {health.top_errors.map((e) => (
              <div
                key={e.error_code}
                className="flex items-center justify-between px-4 py-2.5"
              >
                <span className="font-mono text-sm text-status-degraded">
                  {e.error_code}
                </span>
                <span className="font-mono text-xs tabular-nums text-muted-foreground">
                  {e.count}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Gap analysis */}
      <section>
        <h2 className="text-base font-semibold">Gap analysis</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Four distinct failure modes, because each needs a different response.
        </p>

        <div className="mt-4 grid gap-4 md:grid-cols-4">
          <StatTile
            label="Unavailable"
            value={gaps?.summary.unavailable_cameras ?? '—'}
            detail="Fix the camera"
          />
          <StatTile
            label="Below ANPR target"
            value={gaps?.summary.districts_below_anpr_target ?? '—'}
            detail="Districts needing plate readers"
          />
          <StatTile
            label="No cameras at all"
            value={gaps?.summary.districts_with_no_cameras ?? '—'}
            detail="Districts with zero coverage"
          />
          <StatTile
            label="Unreliable"
            value={gaps?.summary.unreliable_cameras ?? '—'}
            detail="Flapping in and out"
          />
        </div>

        {gaps && gaps.coverage_gaps.length > 0 && (
          <div className="mt-4 rounded-lg border border-status-degraded/30 bg-status-degraded/5 p-4">
            <h3 className="text-sm font-semibold text-status-degraded">
              Districts with no registered cameras
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              A vehicle can cross these entirely unobserved.
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {gaps.coverage_gaps.map((g) => (
                <span
                  key={g.district}
                  className="rounded border border-border bg-card px-2 py-0.5 text-xs"
                >
                  {g.district}
                </span>
              ))}
            </div>
          </div>
        )}

        {gaps && gaps.capability_gaps.length > 0 && (
          <div className="mt-4 overflow-hidden rounded-lg border border-border bg-card">
            <h3 className="border-b border-border px-4 py-3 text-sm font-semibold">
              Districts below the ANPR coverage target
            </h3>
            <Table bare>
              <Thead>
                <tr>
                  <Th>District</Th>
                  <Th className="text-right">Cameras</Th>
                  <Th className="text-right">ANPR</Th>
                  <Th className="text-right">Share</Th>
                </tr>
              </Thead>
              <tbody>
                {gaps.capability_gaps.map((g) => (
                  <Tr key={g.district}>
                    <Td>{g.district}</Td>
                    <Td className="text-right font-mono tabular-nums">{g.cameras}</Td>
                    <Td className="text-right font-mono tabular-nums">{g.anpr_cameras}</Td>
                    <Td className="text-right font-mono tabular-nums text-status-degraded">
                      {Math.round(g.anpr_share * 100)}%
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </section>
    </div>
  )
}
