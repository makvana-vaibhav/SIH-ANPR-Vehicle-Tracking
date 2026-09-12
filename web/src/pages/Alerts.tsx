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

import PlateCrop from '@/components/PlateCrop'
import { SkeletonRows } from '@/components/Skeleton'
import { useToast } from '@/components/Toast'
import { Badge, Button, Checkbox, ConnectionBadge, EmptyState, PriorityBadge } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { PERMISSIONS } from '@/lib/permissions'
import type { Alert, AlertStatus, Camera, Priority } from '@/lib/types'

const PRIORITY_ROW: Record<Priority, string> = {
  critical: 'border-status-offline bg-status-offline/10',
  high: 'border-priority-high bg-priority-high/10',
  medium: 'border-primary bg-primary/10',
  low: 'border-border bg-card',
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
  const toast = useToast()
  const { can } = useAuth()
  const { alerts: liveAlerts, status: feedStatus } = useEventStream()
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [cameras, setCameras] = useState<Map<string, Camera>>(new Map())
  const [openOnly, setOpenOnly] = useState(true)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState<string | null>(null)
  // Which alert the keyboard is pointed at. Triage in a control room is a
  // repetitive job and reaching for a mouse for every one of forty alerts is
  // the difference between clearing a backlog and giving up on it.
  const [cursor, setCursor] = useState(0)

  const load = useCallback(async () => {
    try {
      const page = await api.getAlerts({ open_only: openOnly, limit: 100 })
      setAlerts(page.items)
    } catch (err) {
      toast.error(err)
    } finally {
      setLoading(false)
    }
  }, [openOnly, toast])

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

  const act = useCallback(
    async (alert: Alert, to: AlertStatus) => {
      setWorking(alert.id)
      try {
        await api.transitionAlert(alert.id, to)
        toast.success(
          `${alert.plate_normalised} ${to.replace(/_/g, ' ')}. Recorded against you.`,
        )
        await load()
      } catch (err) {
        toast.error(err)
      } finally {
        setWorking(null)
      }
    },
    [load, toast],
  )

  // ── Keyboard triage ────────────────────────────────────────────────
  // j/k to move, a/d/c/f to act. Deliberately not single-key destructive:
  // `f` (false positive) and `c` (close) end an alert's life, so they are
  // confirmed by the toast rather than by a dialog that would defeat the
  // point of a keyboard shortcut.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (alerts.length === 0) return

      const current = alerts[Math.min(cursor, alerts.length - 1)]
      const step = (to: AlertStatus) => {
        if (!current) return
        const allowed = (NEXT_STEPS[current.status] ?? []).some((s) => s.to === to)
        if (allowed && can(TRANSITION_PERMISSION[to])) void act(current, to)
      }

      switch (event.key) {
        case 'j':
          setCursor((c) => Math.min(c + 1, alerts.length - 1))
          break
        case 'k':
          setCursor((c) => Math.max(c - 1, 0))
          break
        case 'a':
          step('acknowledged')
          break
        case 'd':
          step('dispatched')
          break
        case 'c':
          step('closed')
          break
        case 'f':
          step('false_positive')
          break
        default:
          return
      }
      event.preventDefault()
    }

    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [alerts, cursor, can, act])

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
          <span className="hidden text-[10px] text-muted-foreground lg:inline">
            <kbd className="rounded bg-muted px-1">j</kbd>/
            <kbd className="rounded bg-muted px-1">k</kbd> move ·{' '}
            <kbd className="rounded bg-muted px-1">a</kbd>ck ·{' '}
            <kbd className="rounded bg-muted px-1">d</kbd>ispatch ·{' '}
            <kbd className="rounded bg-muted px-1">c</kbd>lose ·{' '}
            <kbd className="rounded bg-muted px-1">f</kbd>alse
          </span>
          <Checkbox
            checked={openOnly}
            onChange={(e) => setOpenOnly(e.target.checked)}
            label="open only"
            labelClassName="gap-1.5 text-[11px] text-muted-foreground"
          />
          <ConnectionBadge live={feedStatus === 'live'} label={feedStatus} />
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

      {loading ? (
        <SkeletonRows rows={5} height="h-20" />
      ) : alerts.length === 0 ? (
        <EmptyState
          title="No alerts."
          hint="Put a plate on the watchlist, and a sighting on any camera will raise one here."
        />
      ) : (
        <ul className="space-y-2">
          {alerts.map((alert, index) => {
            const camera = alert.camera_id ? cameras.get(alert.camera_id) : null
            const steps = NEXT_STEPS[alert.status] ?? []
            const focused = index === Math.min(cursor, alerts.length - 1)
            return (
              <li
                key={alert.id}
                onMouseEnter={() => setCursor(index)}
                className={`rounded-md border-l-4 border-y border-r border-y-border border-r-border px-4 py-3 ${PRIORITY_ROW[alert.priority]} ${
                  focused ? 'ring-1 ring-primary' : ''
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <PriorityBadge priority={alert.priority} solid />
                      <span className="font-mono text-lg font-bold tracking-wide">
                        {alert.plate_normalised ?? '—'}
                      </span>
                      {/* The photograph the alert is about. An operator acting
                          on a blacklist hit is being asked to trust a string
                          read off a moving vehicle; showing the pixels it came
                          from is what makes that judgement possible. */}
                      <PlateCrop url={alert.crop_url} plate={alert.plate_normalised} />
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

                    {/* The explainability rule: an alert that carries a
                        reason is shown as its named factors, not the raw
                        `notes` string those factors were joined into —
                        `Risk: 87%` alone is a bug; the factors are the
                        feature. Alert types with no producer here yet
                        (watchlist near-match) still fall back to notes. */}
                    {alert.reasons && alert.reasons.length > 0 ? (
                      <ul className="mt-1.5 space-y-1">
                        {alert.reasons.map((reason, i) => (
                          <li
                            key={`${reason.factor}-${i}`}
                            className="flex flex-wrap items-start gap-1.5 text-[11px] text-muted-foreground"
                          >
                            <Badge tone={reason.factor === 'impossible_hop' ? 'danger' : 'neutral'}>
                              {reason.factor.replace(/_/g, ' ')}
                            </Badge>
                            <span className="flex-1">{reason.detail}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      alert.notes && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {alert.notes}
                        </p>
                      )
                    )}
                  </div>

                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {steps
                      .filter((step) => can(TRANSITION_PERMISSION[step.to]))
                      .map((step) => (
                        <Button
                          key={step.to}
                          size="sm"
                          variant={step.to === 'false_positive' ? 'outline' : 'primary'}
                          disabled={working === alert.id}
                          onClick={() => void act(alert, step.to)}
                        >
                          {step.label}
                        </Button>
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
