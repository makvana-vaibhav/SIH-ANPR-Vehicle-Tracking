/**
 * The command centre's landing screen.
 *
 * What an operator needs in the first two seconds of a shift: is the fleet up,
 * is anything demanding attention, and is the pipeline actually reading
 * anything right now.
 *
 * ## Three decisions worth stating
 *
 * **Every number says what it counts.** "31 cameras" is meaningless when the
 * registry holds 281 and only 31 have a feed; a tile that reads `31 / 281`
 * with "analysed / registered" underneath cannot be misread as the whole
 * fleet. The temptation on a dashboard is to show the flattering number.
 *
 * **The live ticker is the honest one.** Counters can look healthy while the
 * pipeline has quietly stopped — the numbers are all historical. A feed that
 * is visibly still or visibly moving tells an operator in a glance which of
 * those they are looking at. The connection state itself lives in the
 * navigation rail, where it is on screen from every other page too.
 *
 * **The page does not scroll.** This is a wall display. Anything that grows —
 * the plate ticker, the alert queue — scrolls inside its own panel so the
 * layout holds still, because a screen nobody is standing at is a screen
 * nobody is going to scroll.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import LivePlateFeed from '@/components/LivePlateFeed'
import { SkeletonRows, SkeletonStat } from '@/components/Skeleton'
import {
  EmptyState,
  ErrorBanner,
  Icon,
  InfoHint,
  PageHeader,
  PriorityDot,
  ScrollPanel,
  SectionLabel,
  StatTile,
} from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Alert, FleetHealth } from '@/lib/types'

/** Refresh cadence for the counters. The ticker is live and needs no polling. */
const REFRESH_MS = 30_000

export default function Dashboard() {
  const { can } = useAuth()
  const { detections, alerts: liveAlerts, counts } = useEventStream()

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
        setOpenAlerts((await api.getAlerts({ open_only: true, limit: 20 })).items)
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

  const answering = health ? health.online + health.offline : 0

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-6">
      <PageHeader
        title="Operations overview"
        subtitle={`${new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          dateStyle: 'full',
        }).format(new Date())} · all times IST`}
      />

      {error && <ErrorBanner>{error}</ErrorBanner>}

      {critical.length > 0 && (
        <Link
          to="/alerts"
          className="flex shrink-0 items-center gap-2.5 rounded-md border-2 border-status-offline bg-status-offline/15 px-4 py-2.5 transition hover:bg-status-offline/25"
        >
          <span className="animate-pulse-alert">
            <Icon name="alert" size={16} className="text-status-offline" />
          </span>
          <p className="shrink-0 text-sm font-bold text-status-offline">
            {critical.length} critical alert{critical.length === 1 ? '' : 's'}{' '}
            awaiting acknowledgement
          </p>
          <span className="truncate font-mono text-xs text-status-offline/80">
            {critical.map((a) => a.plate_normalised).join(', ')}
          </span>
          <Icon name="arrowRight" size={14} className="ml-auto shrink-0 text-status-offline" />
        </Link>
      )}

      {/* ── The counters ──────────────────────────────────────────────
          One divided strip rather than five floating cards. These are
          readings off the same instrument, and boxing each separately made
          them read as five unrelated facts. */}
      <section className="grid shrink-0 grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3 lg:grid-cols-5">
        {health === null ? (
          <>
            <SkeletonStat />
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
              variant="strip"
              label="Feeds answering"
              value={`${health.online} / ${answering}`}
              detail={
                health.unknown > 0
                  ? `${health.unknown} of ${health.total} never probed`
                  : `${health.availability_pct.toFixed(1)}% of the registry`
              }
              info="Counts only cameras that have actually been probed. One that has never been reached is unknown rather than offline — a different fact, and one the availability figure treats cautiously by counting it as not reachable."
              tone={
                answering === 0
                  ? 'warn'
                  : health.online / answering > 0.8
                    ? 'good'
                    : 'bad'
              }
              to="/health"
            />
            <StatTile
              variant="strip"
              label="Analysed for plates"
              value={String(anprFleet ?? '—')}
              detail={`of ${health.total} registered cameras`}
              info="How many cameras are enabled for analysis — not how many are being read this second. Every camera streams; ANPR runs only on the ones a worker is assigned to."
              to="/anpr"
            />
            <StatTile
              variant="strip"
              label="Plates read"
              value={readsToday === null ? '—' : readsToday.toLocaleString('en-IN')}
              detail="last 24 hours"
            />
            {maySeeAlerts && (
              <StatTile
                variant="strip"
                label="Open alerts"
                value={openAlerts === null ? '—' : String(openAlerts.length)}
                detail={
                  critical.length
                    ? `${critical.length} critical`
                    : openAlerts?.length
                      ? 'none critical'
                      : 'nothing open'
                }
                tone={critical.length ? 'bad' : openAlerts?.length ? 'warn' : 'good'}
                to="/alerts"
              />
            )}
            {/* The one figure here that is not historical. Every other tile
                would keep reading the same number with the pipeline stopped;
                this one is evidence the socket is delivering right now. */}
            <StatTile
              variant="strip"
              label="This session"
              value={String(counts.detections)}
              detail="plate reads seen live"
              info="Counted on this browser since the page was opened, not a database total. Every other figure on this strip is historical and would look unchanged if the pipeline had stopped; this one would not."
            />
          </>
        )}
      </section>

      {/* ── Live work ─────────────────────────────────────────────────
          The two things an operator acts on, side by side, each scrolling
          in its own box so the page behind them holds still. */}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <ScrollPanel
          title="Reading now"
          info="Plates as the pipeline settles them, newest first. This is the authoritative record of what was read — unlike the boxes drawn over a video, nothing here depends on lining up with a moving picture."
          action={
            can(PERMISSIONS.streamView) && (
              <Link
                to="/anpr"
                className="flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                Live ANPR
                <Icon name="arrowRight" size={12} />
              </Link>
            )
          }
        >
          <LivePlateFeed
            events={detections.slice(0, 24)}
            showCamera
            emptyMessage="Nothing read yet. Plates appear the moment a worker reads one."
          />
        </ScrollPanel>

        {maySeeAlerts && (
          <ScrollPanel
            title="Needs attention"
            action={
              <Link
                to="/alerts"
                className="flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                All alerts
                <Icon name="arrowRight" size={12} />
              </Link>
            }
          >
            {openAlerts === null ? (
              <SkeletonRows rows={5} />
            ) : openAlerts.length === 0 ? (
              <EmptyState
                title="Nothing open."
                hint="A watched plate passing any camera raises one here by itself."
              />
            ) : (
              <ul className="space-y-1.5">
                {openAlerts.map((alert) => (
                  <li key={alert.id}>
                    <Link
                      to="/alerts"
                      className="flex items-center gap-2 rounded-md border border-border px-3 py-2 transition hover:border-muted-foreground/50 hover:bg-secondary/30"
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
                      <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted-foreground">
                        {alert.alert_type.replace(/_/g, ' ')}
                      </span>
                      <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
                        {api.relativeTime(alert.created_at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </ScrollPanel>
        )}
      </div>

      {/* ── Fleet by department ─────────────────────────────────────── */}
      {health && health.by_department.length > 0 && (
        <section className="shrink-0">
          <SectionLabel>
            Fleet by department
            <InfoHint>
              Cameras reachable against cameras registered, per owning
              department. A department below full strength is a maintenance
              question rather than an alert.
            </InfoHint>
          </SectionLabel>
          {/* A row of narrow bars inside one panel, not one card per
              department: with three departments the card grid stretched each
              to a third of the screen and the bar inside became decoration. */}
          <ul className="mt-1.5 flex flex-wrap gap-x-6 gap-y-2 rounded-md border border-border bg-card px-3 py-2.5">
            {health.by_department.slice(0, 8).map((row) => {
              const pct = row.total ? (row.online / row.total) * 100 : 0
              return (
                // A fixed width rather than flex-1: stretched across a third
                // of the screen, a bar reading 6/6 stops being a measurement
                // and becomes a rule.
                <li key={row.department} className="w-48">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-xs">{row.department}</span>
                    <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
                      {row.online}/{row.total}
                    </span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
                    <div
                      className={`h-full ${pct > 80 ? 'bg-status-online' : pct > 40 ? 'bg-priority-high' : 'bg-status-offline'}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </div>
  )
}
