/**
 * The command centre's landing screen.
 *
 * What an operator needs in the first two seconds of a shift: is the fleet up,
 * is anything demanding attention, and is the pipeline actually reading
 * anything right now.
 *
 * ## Two decisions worth stating
 *
 * **Every number says what it counts.** "31 cameras" is meaningless when the
 * registry holds 281 and only 31 have a feed; a tile that reads `31 / 281`
 * with "analysed / registered" underneath cannot be misread as the whole
 * fleet. The temptation on a dashboard is to show the flattering number.
 *
 * **The live ticker is the honest one.** Counters can look healthy while the
 * pipeline has quietly stopped — the numbers are all historical. A feed that
 * is visibly still or visibly moving tells an operator in a glance which of
 * those they are looking at, which is why the connection state is shown next
 * to it rather than hidden.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import LivePlateFeed from '@/components/LivePlateFeed'
import { SkeletonRows, SkeletonStat } from '@/components/Skeleton'
import { ConnectionBadge, EmptyState, ErrorBanner, PriorityDot, StatTile } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Alert, FleetHealth } from '@/lib/types'

/** Refresh cadence for the counters. The ticker is live and needs no polling. */
const REFRESH_MS = 30_000

export default function Dashboard() {
  const { user, can } = useAuth()
  const { detections, alerts: liveAlerts, status: feedStatus, counts } = useEventStream()

  const [health, setHealth] = useState<FleetHealth | null>(null)
  const [anprFleet, setAnprFleet] = useState<number | null>(null)
  const [openAlerts, setOpenAlerts] = useState<Alert[] | null>(null)
  const [readsToday, setReadsToday] = useState<number | null>(null)
  // Alerts carry only a camera id; resolving codes once keeps the list from
  // firing a request per row.
  const [cameras, setCameras] = useState<Map<string, { camera_code: string }>>(
    new Map(),
  )
  const [error, setError] = useState<string | null>(null)

  const maySeeAlerts = can(PERMISSIONS.alertRead)

  const load = useCallback(async () => {
    try {
      const since = new Date(Date.now() - 24 * 3_600_000).toISOString()
      const [fleet, summary, detectionPage] = await Promise.all([
        api.getFleetHealth(),
        api.getFleetSummary(),
        api.getDetections({ since, limit: 1 }),
      ])
      setHealth(fleet)
      try {
        const page = await api.getCameras({ limit: '500' })
        setCameras(new Map(page.items.map((c) => [c.id, { camera_code: c.camera_code }])))
      } catch {
        /* the list still reads without camera codes */
      }
      setAnprFleet(summary.anpr_enabled)
      setReadsToday(detectionPage.total)

      if (maySeeAlerts) {
        setOpenAlerts((await api.getAlerts({ open_only: true, limit: 8 })).items)
      } else {
        setOpenAlerts([])
      }
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [maySeeAlerts])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [load])

  // An alert arriving on the socket should be reflected without waiting out
  // the polling interval — that wait is exactly when an operator is watching.
  useEffect(() => {
    if (liveAlerts.length > 0) void load()
  }, [liveAlerts, load])

  const critical = useMemo(
    () => (openAlerts ?? []).filter((a) => a.priority === 'critical'),
    [openAlerts],
  )

  return (
    <div className="h-full space-y-4 overflow-y-auto p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">
            Good {greeting()}, {user?.full_name || user?.username}
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {new Intl.DateTimeFormat('en-IN', {
              timeZone: 'Asia/Kolkata',
              dateStyle: 'full',
            }).format(new Date())}{' '}
            · all times IST
          </p>
        </div>
        <ConnectionBadge
          live={feedStatus === 'live'}
          label={`event feed ${feedStatus}`}
          title={
            feedStatus === 'live'
              ? 'Connected to the live event feed'
              : 'Not connected — the counters above are historical and nothing new will appear'
          }
        />
      </header>

      {error && <ErrorBanner>{error}</ErrorBanner>}

      {critical.length > 0 && (
        <Link
          to="/alerts"
          className="block animate-pulse-alert rounded-md border-2 border-status-offline bg-status-offline/15 px-4 py-2.5 transition hover:bg-status-offline/25"
        >
          <p className="text-sm font-bold text-status-offline">
            {critical.length} critical alert{critical.length === 1 ? '' : 's'}{' '}
            awaiting acknowledgement — {critical.map((a) => a.plate_normalised).join(', ')}
          </p>
        </Link>
      )}

      {/* ── The counters ────────────────────────────────────────────── */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {health === null ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : (
          <>
            {/* "1 / 281" reads as a fleet that is down. It is not: 280 of
                those have never been probed, and never-probed is a different
                fact from offline. The tile counts what has actually been
                answered and says separately how much is unknown. */}
            <StatTile
              label="Feeds answering"
              value={`${health.online} / ${health.online + health.offline}`}
              detail={
                health.unknown > 0
                  ? `${health.unknown} of ${health.total} never probed`
                  : `${health.availability_pct.toFixed(1)}% of the registry`
              }
              tone={
                health.online + health.offline === 0
                  ? 'warn'
                  : health.online / (health.online + health.offline) > 0.8
                    ? 'good'
                    : 'bad'
              }
              to="/health"
            />
            <StatTile
              label="Analysed for plates"
              value={String(anprFleet ?? '—')}
              detail={`of ${health.total} registered cameras`}
              to="/anpr"
            />
            <StatTile
              label="Plates read"
              value={readsToday === null ? '—' : readsToday.toLocaleString('en-IN')}
              detail="last 24 hours"
            />
            {maySeeAlerts ? (
              <StatTile
                label="Open alerts"
                value={openAlerts === null ? '—' : String(openAlerts.length)}
                detail={
                  critical.length
                    ? `${critical.length} critical`
                    : openAlerts?.length
                      ? 'none critical'
                      : 'nothing open'
                }
                tone={
                  critical.length ? 'bad' : openAlerts?.length ? 'warn' : 'good'
                }
                to="/alerts"
              />
            ) : (
              <StatTile
                label="This session"
                value={String(counts.detections)}
                detail="plate reads seen live"
              />
            )}
          </>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── Live ticker ───────────────────────────────────────────── */}
        <section>
          <div className="flex items-baseline justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Reading now
            </h2>
            {can(PERMISSIONS.streamView) && (
              <Link to="/anpr" className="text-[11px] text-primary hover:underline">
                Live ANPR →
              </Link>
            )}
          </div>
          <div className="mt-1.5 max-h-96 overflow-y-auto">
            <LivePlateFeed
              events={detections.slice(0, 12)}
              showCamera
              emptyMessage={
                feedStatus === 'live'
                  ? 'Connected and waiting. Plates appear the moment a worker reads one.'
                  : 'The event feed is not connected, so nothing can appear here.'
              }
            />
          </div>
        </section>

        {/* ── Alerts ────────────────────────────────────────────────── */}
        {maySeeAlerts && (
          <section>
            <div className="flex items-baseline justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Needs attention
              </h2>
              <Link to="/alerts" className="text-[11px] text-primary hover:underline">
                All alerts →
              </Link>
            </div>

            <div className="mt-1.5">
              {openAlerts === null ? (
                <SkeletonRows rows={4} />
              ) : openAlerts.length === 0 ? (
                <EmptyState title="Nothing open. A watched plate passing any camera will appear here by itself." />
              ) : (
                <ul className="space-y-1.5">
                  {openAlerts.map((alert) => (
                    <li key={alert.id}>
                      <Link
                        to="/alerts"
                        className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 transition hover:border-muted-foreground/50"
                      >
                        <PriorityDot priority={alert.priority} />
                        {/* A camera-down alert has no plate; showing an empty
                            gap where the identifier belongs makes the row look
                            broken rather than different. */}
                        <span className="truncate font-mono text-sm font-semibold">
                          {alert.plate_normalised ??
                            (alert.camera_id
                              ? (cameras.get(alert.camera_id)?.camera_code ?? 'camera')
                              : '—')}
                        </span>
                        <span className="shrink-0 text-[10px] uppercase text-muted-foreground">
                          {alert.alert_type.replace(/_/g, ' ')}
                        </span>
                        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                          {api.relativeTime(alert.created_at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}
      </div>

      {/* ── Fleet by department ─────────────────────────────────────── */}
      {health && health.by_department.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Fleet by department
          </h2>
          <div className="mt-1.5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {health.by_department.slice(0, 6).map((row) => {
              const pct = row.total ? (row.online / row.total) * 100 : 0
              return (
                <div
                  key={row.department}
                  className="rounded-md border border-border bg-card px-3 py-2"
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-xs">{row.department}</span>
                    <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                      {row.online}/{row.total}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded bg-muted">
                    <div
                      className={`h-full ${pct > 80 ? 'bg-status-online' : pct > 40 ? 'bg-priority-high' : 'bg-status-offline'}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

function greeting(): string {
  const hour = Number(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false,
    }).format(new Date()),
  )
  if (hour < 12) return 'morning'
  if (hour < 17) return 'afternoon'
  return 'evening'
}
