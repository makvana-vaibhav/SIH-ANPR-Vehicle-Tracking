/**
 * Fleet health and gap analysis.
 *
 * The gap report answers the question an operations lead actually asks —
 * *where is the city blind?* — which is not the same as *which cameras are
 * broken*. A district with no camera at all never appears in a fault list.
 */

import { useEffect, useState } from 'react'

import {
  ErrorBanner,
  InfoHint,
  PageHeader,
  StatTile,
  Table,
  Td,
  Th,
  Thead,
  Tr,
} from '@/components/ui'
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
      <PageHeader
        title="Fleet health"
        subtitle={
          <>
            <span>availability across the estate, and where coverage is missing</span>
            {health && (
              <>
                <span className="text-muted-foreground/40">·</span>
                <span>updated {api.relativeTime(health.generated_at)}</span>
              </>
            )}
          </>
        }
      />

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
      {/* The column count follows the number of tiles actually rendered. A
          `gap-px bg-border` grid draws its own dividers, so a fixed four
          columns holding three tiles paints a fourth, empty, bordered cell. */}
      <section
        className={`grid gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-2 ${
          health != null && health.unknown > 0 ? 'md:grid-cols-4' : 'md:grid-cols-3'
        }`}
      >
        <StatTile
          variant="strip"
          label="Availability"
          value={health?.availability_pct != null ? `${health.availability_pct}%` : '—'}
          detail={`${health?.online ?? '—'} of ${health?.total ?? '—'} reachable`}
          tone={health ? (health.availability_pct < 90 ? 'bad' : 'good') : undefined}
        />
        <StatTile
          variant="strip"
          label="Offline"
          value={health?.offline ?? '—'}
          detail="reachable integration, no video"
          tone={health && health.offline > 0 ? 'bad' : undefined}
        />
        <StatTile
          variant="strip"
          label="Degraded"
          value={health?.degraded ?? '—'}
          detail="video arriving late or dropping frames"
          tone={health && health.degraded > 0 ? 'warn' : undefined}
        />
        {/* Only when it is non-zero. A card permanently reading 0, explaining
            a state that no longer normally occurs, is how a screen stops being
            read at all. */}
        {health != null && health.unknown > 0 && (
          <StatTile
            variant="strip"
            label="Not yet probed"
            value={health.unknown}
            detail="state unknown, not offline"
            info="Normally a camera onboarded in the last minute. Availability counts these as not reachable, which is the cautious reading — never-probed and offline are different facts and this keeps them apart."
          />
        )}
      </section>

      {/* Per department */}
      <section className="rounded-md border border-border bg-card">
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
      <section className="rounded-md border border-border bg-card">
        <h2 className="flex items-center gap-1.5 border-b border-border px-4 py-3 text-sm font-semibold">
          Federated VMS vendors
          <InfoHint>
            One adapter interface reaching every vendor here. Adding another is
            one class plus one registry entry; nothing else in the platform
            changes.
          </InfoHint>
        </h2>
        {/* A wrapping row rather than a divided grid: the vendor count is
            whatever the estate happens to federate, and a three-column grid
            holding one vendor drew two empty cells beside it. */}
        <ul className="flex flex-wrap gap-2 p-4">
          {health?.by_vendor.map((row) => (
            <li
              key={row.vendor}
              className="min-w-44 rounded-md border border-border px-3 py-2"
            >
              <p className="font-mono text-sm">{row.vendor}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {row.total} cameras · {row.online} live
              </p>
            </li>
          ))}
        </ul>
      </section>

      {/* Failure causes */}
      {health && health.top_errors.length > 0 && (
        <section className="rounded-md border border-border bg-card">
          <h2 className="flex items-center gap-1.5 border-b border-border px-4 py-3 text-sm font-semibold">
            Failure causes (last hour)
            <InfoHint>
              Grouped by error code, so one root cause reads as one problem
              rather than as forty separate failures.
            </InfoHint>
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
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          Gap analysis
          <InfoHint>
            Four distinct failure modes, kept apart because each needs a
            different response. This answers where the city is blind, which is
            not the same question as which cameras are broken.
          </InfoHint>
        </h2>

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
          <div className="mt-4 rounded-md border border-status-degraded/30 bg-status-degraded/5 p-4">
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
          <div className="mt-4 overflow-hidden rounded-md border border-border bg-card">
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
