/**
 * Camera detail panel: metadata, live health, and stream access.
 *
 * Opening a stream calls the API for a short-lived, camera-scoped token — and
 * that issuance writes an audit row. The panel says so, because a system that
 * records who watched what should be visible about it rather than quietly
 * logging in the background.
 */

import { useCallback, useEffect, useState } from 'react'

import StreamPlayer from '@/components/StreamPlayer'
import * as api from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import type {
  CameraFeatureProperties,
  CameraHealthHistory,
  StreamGrant,
} from '@/lib/types'

const STATUS_STYLE = {
  online: 'bg-status-online/15 text-status-online border-status-online/30',
  offline: 'bg-status-offline/15 text-status-offline border-status-offline/30',
  degraded: 'bg-status-degraded/15 text-status-degraded border-status-degraded/30',
  unknown: 'bg-status-unknown/15 text-status-unknown border-status-unknown/30',
} as const

const STATUS_EXPLANATION = {
  online: 'Verified delivering video.',
  offline: 'Reachable integration reports no video from this camera.',
  degraded: 'Delivering video, but below expected frame rate or above latency budget.',
  unknown:
    'Registered in the estate but no live feed is attached yet. Not a fault — under on-demand publishing this is normal until a feed is pulled.',
} as const

interface Props {
  camera: CameraFeatureProperties
  onClose: () => void
  onRefresh: () => void
}

export default function CameraPanel({ camera, onClose, onRefresh }: Props) {
  const { can } = useAuth()
  const [health, setHealth] = useState<CameraHealthHistory | null>(null)
  const [stream, setStream] = useState<StreamGrant | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    setHealth(null)
    setStream(null)
    setMessage(null)
    api
      .getCameraHealth(camera.id)
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [camera.id])

  const handleProbe = useCallback(async () => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await api.probeCamera(camera.id)
      setMessage(
        `Probe: ${String(result.status)}${
          result.error_code ? ` (${String(result.error_code)})` : ''
        }${result.latency_ms ? ` · ${String(result.latency_ms)}ms` : ''}`,
      )
      setHealth(await api.getCameraHealth(camera.id))
      onRefresh()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [camera.id, onRefresh])

  const handleOpenStream = useCallback(async () => {
    setBusy(true)
    setMessage(null)
    try {
      setStream(await api.openStream(camera.id))
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [camera.id])

  return (
    <aside className="flex w-96 shrink-0 flex-col overflow-y-auto border-l border-border bg-card">
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="min-w-0">
          <p className="camera-code text-sm text-primary">{camera.camera_code}</p>
          <h2 className="truncate text-base font-semibold">{camera.name}</h2>
          <p className="text-xs text-muted-foreground">
            {[camera.junction, camera.city, camera.district]
              .filter(Boolean)
              .join(' · ')}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="rounded p-1 text-muted-foreground transition hover:bg-secondary hover:text-foreground"
        >
          ✕
        </button>
      </header>

      <div className="space-y-5 p-4">
        {/* Status */}
        <section>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide ${
              STATUS_STYLE[camera.status]
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {camera.status}
          </span>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {STATUS_EXPLANATION[camera.status]}
          </p>
        </section>

        {/* Ownership and integration */}
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Ownership &amp; integration
          </h3>
          <dl className="mt-2 space-y-1.5 text-sm">
            <Row label="Department" value={camera.department_name ?? camera.department_code} />
            <Row label="VMS vendor" value={camera.vendor} />
            <Row label="Type" value={camera.camera_type} />
            <Row
              label="ANPR"
              value={camera.anpr_enabled ? 'Enabled' : 'Not enabled'}
            />
            {camera.heading_deg !== null && (
              <Row label="Facing" value={`${camera.heading_deg}°`} />
            )}
          </dl>
        </section>

        {/* Health */}
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Health (24h)
          </h3>
          {health ? (
            <>
              <dl className="mt-2 space-y-1.5 text-sm">
                <Row
                  label="Uptime"
                  value={
                    health.uptime.uptime_pct !== null
                      ? `${health.uptime.uptime_pct}%`
                      : 'No probes yet'
                  }
                />
                <Row label="Probes" value={String(health.uptime.probes)} />
                {health.uptime.avg_latency_ms !== null && (
                  <Row
                    label="Avg latency"
                    value={`${health.uptime.avg_latency_ms} ms`}
                  />
                )}
              </dl>

              {/* Uptime strip — most recent probe on the right. */}
              {health.history.length > 0 && (
                <div className="mt-3">
                  <div className="flex h-6 gap-px overflow-hidden rounded">
                    {[...health.history]
                      .reverse()
                      .slice(-60)
                      .map((h, i) => (
                        <span
                          key={`${h.ts}-${i}`}
                          title={`${api.formatIST(h.ts)} — ${
                            h.reachable ? 'reachable' : (h.error_code ?? 'unreachable')
                          }`}
                          className={`flex-1 ${
                            h.reachable ? 'bg-status-online/70' : 'bg-status-offline/70'
                          }`}
                        />
                      ))}
                  </div>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Each bar is one probe · newest on the right
                  </p>
                </div>
              )}
            </>
          ) : (
            <div className="mt-2 h-16 animate-pulse rounded bg-muted/50" />
          )}
        </section>

        {/* Live video */}
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Live view
          </h3>

          {!can('stream.view') ? (
            <p className="mt-2 rounded border border-border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
              Your role does not include <span className="font-mono">stream.view</span>.
              Analysts and auditors work over recorded detections rather than live video.
            </p>
          ) : camera.status !== 'online' ? (
            // Honesty over a broken button: this camera has no feed attached,
            // so offering "play" would produce a black rectangle and no
            // explanation. Say why, and offer the action that helps.
            <div className="mt-2 rounded border border-border bg-secondary/30 px-3 py-2">
              <p className="text-xs text-muted-foreground">
                {camera.status === 'unknown'
                  ? 'No live feed is attached to this camera yet, so there is nothing to play. In the demo only a subset of the estate is streamed; in a deployment this camera would be pulled on demand from its VMS.'
                  : 'This camera is not currently delivering video.'}
              </p>
              {can('camera.update') && (
                <button
                  type="button"
                  onClick={handleProbe}
                  disabled={busy}
                  className="mt-2 rounded border border-border px-2 py-1 text-xs transition hover:border-primary/50 disabled:opacity-60"
                >
                  Probe now
                </button>
              )}
            </div>
          ) : stream ? (
            <div className="mt-2">
              <StreamPlayer
                whepUrl={stream.whep_url}
                hlsUrl={stream.hls_url}
                cameraCode={stream.camera_code}
              />
              <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                Viewing token scoped to this camera, valid {stream.expires_in}s.
                Recorded in the audit trail as{' '}
                <span className="font-mono text-foreground/80">camera.view</span>.
              </p>
            </div>
          ) : (
            <button
              type="button"
              onClick={handleOpenStream}
              disabled={busy}
              className="mt-2 w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-60"
            >
              {busy ? 'Requesting access…' : 'Open live stream'}
            </button>
          )}
        </section>

        {/* Diagnostics */}
        {can('camera.update') && camera.status === 'online' && (
          <section>
            <button
              type="button"
              onClick={handleProbe}
              disabled={busy}
              className="w-full rounded-md border border-border px-3 py-2 text-sm transition hover:border-primary/50 disabled:opacity-60"
            >
              Probe now
            </button>
          </section>
        )}

        {message && (
          <p className="rounded border border-border bg-secondary/40 px-3 py-2 font-mono text-xs">
            {message}
          </p>
        )}

      </div>
    </aside>
  )
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-right text-sm">{value ?? '—'}</dd>
    </div>
  )
}
