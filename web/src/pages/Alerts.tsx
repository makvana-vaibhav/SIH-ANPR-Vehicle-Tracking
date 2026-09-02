/**
 * Alert triage.
 *
 * Live alerts arrive over the WebSocket and are merged into the list from the
 * database, so an alert raised while this screen is open appears without a
 * refresh and an alert raised before it was opened is still there. Getting
 * only one of those right is the usual way this screen goes wrong.
 *
 * Every action is a lifecycle transition the API records against the operator
 * who made it. `false_positive` sits alongside `closed` as a first-class
 * outcome: an operator who can only close an alert will close the wrong ones
 * silently, and those are exactly the ones worth knowing about.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Alert, AlertStatus, Camera, Priority } from '@/lib/types'

const PRIORITY_STYLE: Record<Priority, string> = {
  critical: 'border-status-offline bg-status-offline/10',
  high: 'border-amber-500 bg-amber-500/10',
  medium: 'border-primary bg-primary/10',
  low: 'border-border bg-card',
}

const PRIORITY_BADGE: Record<Priority, string> = {
  critical: 'bg-status-offline text-white',
  high: 'bg-amber-500 text-black',
  medium: 'bg-primary text-primary-foreground',
  low: 'bg-muted text-muted-foreground',
}

/** Which transitions an operator is offered, given where the alert is now. */
const NEXT_STEPS: Record<AlertStatus, { to: AlertStatus; label: string }[]> = {
  new: [
    { to: 'acknowledged', label: 'Acknowledge' },
    { to: 'false_positive', label: 'False positive' },
  ],
  acknowledged: [
    { to: 'dispatched', label: 'Dispatch' },
    { to: 'closed', label: 'Close' },
    { to: 'false_positive', label: 'False positive' },
  ],
  dispatched: [
    { to: 'closed', label: 'Close' },
    { to: 'false_positive', label: 'False positive' },
  ],
  closed: [],
  false_positive: [],
}

/**
 * Which grant each transition needs, mirroring the API's own table.
 *
 * They are separate permissions because in a control room they are separate
 * authorities: acknowledging is routine, dispatching commits a unit, closing
 * ends the record.
 */
const TRANSITION_PERMISSION: Record<AlertStatus, string> = {
  new: PERMISSIONS.alertRead,
  acknowledged: PERMISSIONS.alertAcknowledge,
  dispatched: PERMISSIONS.alertDispatch,
  closed: PERMISSIONS.alertClose,
  false_positive: PERMISSIONS.alertClose,
}

export default function Alerts() {
  const { can } = useAuth()
  const { alerts: liveAlerts, status: feedStatus } = useEventStream()
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [cameras, setCameras] = useState<Map<string, Camera>>(new Map())
  const [openOnly, setOpenOnly] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const page = await api.getAlerts({ open_only: openOnly, limit: 100 })
      setAlerts(page.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [openOnly])

  useEffect(() => {
    void load()
  }, [load])

  // A live alert carries only ids for the camera. Resolving codes once here
  // means the list does not fire a request per row.
  useEffect(() => {
    async function loadCameras() {
      try {
        const page = await api.getCameras({ limit: '500' })
        setCameras(new Map(page.items.map((c) => [c.id, c])))
      } catch {
        /* the list still works without camera names */
      }
    }
    void loadCameras()
  }, [])

  // An alert arriving on the socket is not yet in `alerts`. Refetch rather
  // than synthesising a row: the database record carries fields the live
  // payload does not, and a half-populated row would triage badly.
  useEffect(() => {
    if (liveAlerts.length === 0) return
    void load()
  }, [liveAlerts, load])

  async function act(alert: Alert, to: AlertStatus) {
    setWorking(alert.id)
    setError(null)
    try {
      await api.transitionAlert(alert.id, to)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setWorking(null)
    }
  }

  const critical = useMemo(
    () => alerts.filter((a) => a.priority === 'critical' && a.status === 'new'),
    [alerts],
  )

  return (
    <div className="space-y-4 overflow-y-auto p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Alerts</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {alerts.length} {openOnly ? 'open' : 'total'} · raised automatically
            when a watched plate is read
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
            <input
              type="checkbox"
              checked={openOnly}
              onChange={(e) => setOpenOnly(e.target.checked)}
              className="accent-primary"
            />
            open only
          </label>
          <span
            className={`flex items-center gap-1.5 rounded px-2 py-1 text-[11px] ${
              feedStatus === 'live'
                ? 'bg-status-online/15 text-status-online'
                : 'bg-amber-500/15 text-amber-400'
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                feedStatus === 'live'
                  ? 'animate-pulse-alert bg-status-online'
                  : 'bg-amber-400'
              }`}
            />
            {feedStatus}
          </span>
        </div>
      </header>

      {/* The banner exists so a critical hit cannot be missed by an operator
          looking at another part of the screen. */}
      {critical.length > 0 && (
        <div className="animate-pulse-alert rounded-md border-2 border-status-offline bg-status-offline/15 px-4 py-2">
          <p className="text-sm font-bold text-status-offline">
            {critical.length} critical alert{critical.length === 1 ? '' : 's'}{' '}
            awaiting acknowledgement
          </p>
        </div>
      )}

      {error && (
        <p className="rounded border border-status-offline/40 bg-status-offline/10 px-4 py-2 text-sm text-status-offline">
          {error}
        </p>
      )}

      {alerts.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-8 text-center">
          <p className="text-sm text-muted-foreground">No alerts.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Put a plate on the watchlist, and a sighting on any camera will
            raise one here.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((alert) => {
            const camera = alert.camera_id ? cameras.get(alert.camera_id) : null
            const steps = NEXT_STEPS[alert.status] ?? []
            return (
              <li
                key={alert.id}
                className={`rounded-md border-l-4 border-y border-r border-y-border border-r-border px-4 py-3 ${PRIORITY_STYLE[alert.priority]}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wider ${PRIORITY_BADGE[alert.priority]}`}
                      >
                        {alert.priority}
                      </span>
                      <span className="font-mono text-lg font-bold tracking-wide">
                        {alert.plate_normalised ?? '—'}
                      </span>
                      <span className="text-[11px] uppercase text-muted-foreground">
                        {alert.alert_type.replace(/_/g, ' ')}
                      </span>
                    </div>

                    <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
                      <div className="flex gap-1">
                        <dt>camera</dt>
                        <dd className="text-foreground">
                          {camera
                            ? `${camera.camera_code} — ${camera.name}`
                            : (alert.camera_id ?? 'unknown')}
                        </dd>
                      </div>
                      {camera?.city && (
                        <div className="flex gap-1">
                          <dt>location</dt>
                          <dd className="text-foreground">
                            {[camera.city, camera.district]
                              .filter(Boolean)
                              .join(', ')}
                          </dd>
                        </div>
                      )}
                      <div className="flex gap-1">
                        <dt>raised</dt>
                        <dd className="text-foreground">
                          {api.formatIST(alert.created_at)}
                        </dd>
                      </div>
                      {alert.confidence !== null && (
                        <div className="flex gap-1">
                          <dt>confidence</dt>
                          <dd className="text-foreground">
                            {alert.confidence.toFixed(2)}
                          </dd>
                        </div>
                      )}
                      <div className="flex gap-1">
                        <dt>status</dt>
                        <dd className="text-foreground">{alert.status}</dd>
                      </div>
                    </dl>

                    {alert.notes && (
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {alert.notes}
                      </p>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {steps
                      .filter((step) => can(TRANSITION_PERMISSION[step.to]))
                      .map((step) => (
                      <button
                        key={step.to}
                        type="button"
                        disabled={working === alert.id}
                        onClick={() => void act(alert, step.to)}
                        className={`rounded px-2 py-1 text-[11px] font-medium transition disabled:opacity-50 ${
                          step.to === 'false_positive'
                            ? 'border border-border text-muted-foreground hover:text-foreground'
                            : 'bg-primary text-primary-foreground hover:opacity-90'
                        }`}
                      >
                        {step.label}
                      </button>
                    ))}
                    {steps.filter((step) => can(TRANSITION_PERMISSION[step.to]))
                      .length === 0 && (
                      <span className="text-[11px] text-muted-foreground">
                        {steps.length > 0
                          ? `${alert.status.replace(/_/g, ' ')} — your role cannot advance this`
                          : alert.status.replace(/_/g, ' ')}
                      </span>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
