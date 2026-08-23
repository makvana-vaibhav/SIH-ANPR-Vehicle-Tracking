/**
 * Sentinel-GJ — system status.
 *
 * Phase 0 surface: a live readiness board for the platform tier. It polls the
 * backend and shows exactly which dependencies are up, which are degraded, and
 * what the platform falls back to when an optional one is missing.
 *
 * This is genuinely useful beyond bring-up — it is the same board the Admin
 * screen embeds in Phase 9, and it is what gets checked first if anything looks
 * wrong during the judge demo (see docs/PANIC.md).
 */

import { useCallback, useEffect, useState } from 'react'
import {
  formatIST,
  getReadiness,
  type DependencyHealth,
  type ReadinessResponse,
} from '@/lib/api'

const POLL_INTERVAL_MS = 5000

/** Human-readable role of each dependency, and what happens when it is lost. */
const DEPENDENCY_ROLE: Record<string, { role: string; onFailure: string }> = {
  postgres: {
    role: 'Registry, detections, audit — PostGIS + TimescaleDB',
    onFailure: 'Fatal. The platform cannot serve without it.',
  },
  redis: {
    role: 'Event bus — Redis Streams consumer groups',
    onFailure: 'Fatal in the base profile. Redpanda replaces it at scale.',
  },
  opensearch: {
    role: 'Fuzzy and partial plate search',
    onFailure: 'Degrades to Postgres trigram search. Demo continues.',
  },
  minio: {
    role: 'Plate crops, frames, clips (S3 API)',
    onFailure: 'Events continue; stored imagery is unavailable.',
  },
  mediamtx: {
    role: 'RTSP → WebRTC / HLS stream gateway',
    onFailure: 'Analytics continue; live viewing is unavailable.',
  },
}

const STATUS_STYLES: Record<DependencyHealth['status'], string> = {
  ok: 'bg-status-online/15 text-status-online border-status-online/30',
  degraded: 'bg-status-degraded/15 text-status-degraded border-status-degraded/30',
  down: 'bg-status-offline/15 text-status-offline border-status-offline/30',
}

const OVERALL_STYLES: Record<ReadinessResponse['status'], string> = {
  ready: 'text-status-online',
  degraded: 'text-status-degraded',
  unavailable: 'text-status-offline',
}

function StatusPill({ status }: { status: DependencyHealth['status'] }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide ${STATUS_STYLES[status]}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  )
}

function DependencyRow({
  name,
  health,
}: {
  name: string
  health: DependencyHealth
}) {
  const meta = DEPENDENCY_ROLE[name]
  const version =
    health.server_version ?? health.cluster_status ?? undefined

  return (
    <div className="flex flex-col gap-2 border-b border-border/60 py-4 last:border-b-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-sm font-semibold text-foreground">
            {name}
          </span>
          {health.critical ? (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              critical
            </span>
          ) : (
            <span className="rounded bg-muted/50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              optional
            </span>
          )}
        </div>

        <p className="mt-1 text-sm text-muted-foreground">{meta?.role}</p>

        {health.status !== 'ok' && (
          <p className="mt-1.5 text-xs text-muted-foreground">
            <span className="text-foreground/80">On failure:</span>{' '}
            {meta?.onFailure}
            {health.fallback && health.fallback !== 'none' && (
              <>
                {' '}
                Active fallback:{' '}
                <span className="font-mono text-status-degraded">
                  {health.fallback}
                </span>
              </>
            )}
          </p>
        )}

        {health.error && (
          <p className="mt-1.5 break-words font-mono text-xs text-status-offline/90">
            {health.error}
          </p>
        )}

        {health.extensions && Object.keys(health.extensions).length > 0 && (
          <p className="mt-1.5 font-mono text-xs text-muted-foreground">
            {Object.entries(health.extensions)
              .map(([ext, ver]) => `${ext} ${ver}`)
              .join('  ·  ')}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-3 sm:flex-col sm:items-end">
        <StatusPill status={health.status} />
        {version && (
          <span className="font-mono text-xs text-muted-foreground">
            {version}
          </span>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [loading, setLoading] = useState(true)

  const poll = useCallback(async () => {
    try {
      const data = await getReadiness()
      setReadiness(data)
      setError(null)
    } catch (err) {
      // The API itself being unreachable is a distinct state from any
      // dependency being down, and is reported as such.
      setError(err instanceof Error ? err.message : String(err))
      setReadiness(null)
    } finally {
      setLastChecked(new Date())
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void poll()
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [poll])

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/40">
        <div className="mx-auto flex max-w-5xl flex-col gap-1 px-6 py-7">
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-primary">
              Sentinel<span className="text-foreground">-GJ</span>
            </h1>
            <span className="text-sm text-muted-foreground">
              સેન્ટિનલ · Command Centre
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            Statewide CCTV intelligence platform — Gujarat Police / Home Department
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <section className="rounded-lg border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-6 py-4">
            <div>
              <h2 className="text-base font-semibold">Platform readiness</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {lastChecked
                  ? `Last checked ${formatIST(lastChecked.toISOString())} IST`
                  : 'Checking…'}
              </p>
            </div>

            {loading && !readiness && !error ? (
              // Skeleton rather than a blocking spinner — the UI never stalls.
              <div className="h-6 w-28 animate-pulse rounded bg-muted" />
            ) : error ? (
              <span className="font-mono text-sm font-semibold uppercase text-status-offline">
                api unreachable
              </span>
            ) : readiness ? (
              <div className="text-right">
                <span
                  className={`font-mono text-sm font-semibold uppercase ${OVERALL_STYLES[readiness.status]}`}
                >
                  {readiness.status}
                </span>
                <p className="text-xs text-muted-foreground">
                  probed in {readiness.probe_duration_ms} ms
                </p>
              </div>
            ) : null}
          </div>

          <div className="px-6">
            {error && (
              <div className="my-4 rounded border border-status-offline/30 bg-status-offline/10 p-4">
                <p className="text-sm font-medium text-status-offline">
                  Cannot reach the API tier.
                </p>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {error}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  Check <span className="font-mono">make logs</span>, or confirm
                  the api container is healthy with{' '}
                  <span className="font-mono">docker compose ps</span>.
                </p>
              </div>
            )}

            {loading && !readiness && !error && (
              <div className="space-y-4 py-6">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-12 animate-pulse rounded bg-muted/60" />
                ))}
              </div>
            )}

            {readiness &&
              Object.entries(readiness.dependencies).map(([name, health]) => (
                <DependencyRow key={name} name={name} health={health} />
              ))}
          </div>
        </section>

        <section className="mt-6 rounded-lg border border-border bg-card/60 px-6 py-5">
          <h3 className="text-sm font-semibold">Build state</h3>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Phase 0 — foundation. The command centre interface (live map, video
            wall, alerts, vehicle search and route replay) is built in Phase 9.
            Track progress in{' '}
            <span className="font-mono text-foreground/80">BUILD_STATE.md</span>.
          </p>
          <div className="mt-4 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <a className="underline hover:text-primary" href="/api/docs">
              API documentation
            </a>
            <a className="underline hover:text-primary" href="/api/ready">
              Raw readiness JSON
            </a>
          </div>
        </section>
      </main>
    </div>
  )
}
