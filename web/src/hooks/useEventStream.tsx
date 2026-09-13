/**
 * The live event feed, shared by every screen that needs it.
 *
 * One WebSocket per browser tab, not one per component. A control room may have
 * the map, the alert list and two camera views open at once; four sockets would
 * mean the server fans the same event out four times and each consumer sees a
 * slightly different moment.
 *
 * Reconnects with exponential backoff, because the interesting failure is not
 * the socket dropping — it is the operator not noticing it dropped. `status` is
 * exported so screens can say so rather than quietly going stale.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { API_BASE_URL, getAccessToken } from '@/lib/api'
import { isPositionRefresh, isTrackBatch } from '@/lib/events'
import type {
  LiveAlertEvent,
  LiveEvent,
  LiveTrackBatchEvent,
  LiveVehicleEvent,
} from '@/lib/types'

export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'offline'

/** How many recent items each buffer holds. Bounded: this runs for hours. */
const MAX_DETECTIONS = 200
const MAX_ALERTS = 50
/**
 * Per-camera buffer of *readings*.
 *
 * Deeper than a plate feed needs, because it was sized when this buffer also
 * carried the position refreshes that moved boxes. Boxes now come from
 * `camera.tracks` instead, so this holds readings alone — and the depth is
 * still earned: a camera reading steadily should not evict a plate before
 * anyone beside the video has had a chance to read it.
 */
const CAMERA_BUFFER = 120

const BACKOFF_START_MS = 1_000
const BACKOFF_MAX_MS = 30_000

export interface EventStream {
  status: StreamStatus
  /** Newest first. Only readable plates — an unreadable one has nothing to show. */
  detections: LiveVehicleEvent[]
  /** Newest first. */
  alerts: LiveAlertEvent[]
  /** Events seen since the page loaded, including ones no buffer kept. */
  counts: { detections: number; alerts: number }
  /** Subscribe to raw events. Returns an unsubscribe function. */
  subscribe: (handler: (event: LiveEvent) => void) => () => void
  /** Forget the buffered alerts, e.g. after the operator has triaged them. */
  clearAlerts: () => void
}

const StreamContext = createContext<EventStream | null>(null)

function socketUrl(token: string): string {
  const base = API_BASE_URL || window.location.origin
  const url = new URL(base, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = '/ws/events'
  url.search = `?token=${encodeURIComponent(token)}`
  return url.toString()
}

function isVehicle(event: LiveEvent): event is LiveVehicleEvent {
  return event.event === 'vehicle.observed' || event.event === 'vehicle.completed'
}

function isAlert(event: LiveEvent): event is LiveAlertEvent {
  return event.event === 'alert.raised'
}

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const [detections, setDetections] = useState<LiveVehicleEvent[]>([])
  const [alerts, setAlerts] = useState<LiveAlertEvent[]>([])
  const [counts, setCounts] = useState({ detections: 0, alerts: 0 })

  const socketRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<number>(BACKOFF_START_MS)
  const timerRef = useRef<number | null>(null)
  const closedRef = useRef(false)
  // Subscribers are held in a ref so adding one does not tear down the socket.
  const handlersRef = useRef(new Set<(event: LiveEvent) => void>())

  const subscribe = useCallback((handler: (event: LiveEvent) => void) => {
    handlersRef.current.add(handler)
    return () => {
      handlersRef.current.delete(handler)
    }
  }, [])

  const clearAlerts = useCallback(() => setAlerts([]), [])

  useEffect(() => {
    closedRef.current = false

    const connect = () => {
      const token = getAccessToken()
      if (!token) {
        // Not signed in yet. Poll rather than fail: the login flow will put a
        // token in place and this picks it up without a page reload.
        setStatus('offline')
        timerRef.current = window.setTimeout(connect, 2_000)
        return
      }

      let socket: WebSocket
      try {
        socket = new WebSocket(socketUrl(token))
      } catch {
        setStatus('reconnecting')
        scheduleRetry()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        retryRef.current = BACKOFF_START_MS
        setStatus('live')
      }

      socket.onmessage = (message) => {
        let event: LiveEvent
        try {
          event = JSON.parse(message.data as string) as LiveEvent
        } catch {
          return // a frame we cannot parse is not worth tearing the feed down for
        }

        if (event.event === 'keepalive' || event.event === 'connected') return

        for (const handler of handlersRef.current) handler(event)

        if (isVehicle(event)) {
          if (!event.plate?.readable || !event.plate.text) return
          // A position refresh is the same reading again, sent so an overlay
          // has somewhere current to draw. Subscribers above have already had
          // it; buffering it here would list the same car several times a
          // second and count each redraw as a fresh sighting.
          if (isPositionRefresh(event)) return
          setCounts((c) => ({ ...c, detections: c.detections + 1 }))
          setDetections((current) => [event, ...current].slice(0, MAX_DETECTIONS))
        } else if (isAlert(event)) {
          setCounts((c) => ({ ...c, alerts: c.alerts + 1 }))
          setAlerts((current) => [event, ...current].slice(0, MAX_ALERTS))
        }
      }

      socket.onerror = () => {
        // onclose always follows; retrying here too would double the backoff.
      }

      socket.onclose = () => {
        socketRef.current = null
        if (closedRef.current) return
        setStatus('reconnecting')
        scheduleRetry()
      }
    }

    const scheduleRetry = () => {
      const delay = retryRef.current
      retryRef.current = Math.min(delay * 2, BACKOFF_MAX_MS)
      timerRef.current = window.setTimeout(connect, delay)
    }

    connect()

    return () => {
      closedRef.current = true
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [])

  const value = useMemo<EventStream>(
    () => ({ status, detections, alerts, counts, subscribe, clearAlerts }),
    [status, detections, alerts, counts, subscribe, clearAlerts],
  )

  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>
}

export function useEventStream(): EventStream {
  const context = useContext(StreamContext)
  if (!context) {
    throw new Error('useEventStream must be used inside an EventStreamProvider')
  }
  return context
}

/**
 * Live events for one camera only.
 *
 * Filtering here rather than in each component keeps a camera view from
 * re-rendering on every event in the fleet — at 50 cameras that is most of
 * them.
 */
export function useCameraEvents(cameraCode: string | null): LiveVehicleEvent[] {
  const { subscribe } = useEventStream()
  const [events, setEvents] = useState<LiveVehicleEvent[]>([])

  useEffect(() => {
    // Clear on every change of camera, not just on clearing the selection.
    // Without this, switching cameras leaves the previous camera's plates on
    // screen attributed to the new one — which for a camera that reads nothing
    // is the worst possible failure: it invents results for a dead feed.
    setEvents([])
    if (!cameraCode) return

    const wanted = cameraCode.toLowerCase()
    return subscribe((event) => {
      if (!isVehicle(event)) return
      if ((event.source?.camera_id ?? '').toLowerCase() !== wanted) return
      if (!event.plate?.readable || !event.plate.text) return
      // Deeper than the 40 it used to hold. The worker now re-emits a vehicle
      // several times a second to say where it has got to, so a shallow buffer
      // is filled by the cars currently in view and evicts the ones read a few
      // seconds ago before anyone has read them.
      setEvents((current) => [event, ...current].slice(0, CAMERA_BUFFER))
    })
  }, [cameraCode, subscribe])

  return events
}

/**
 * The newest batch of live boxes for one camera.
 *
 * Only the newest, because a batch is **authoritative**: it describes every
 * vehicle currently drawable on that camera, so an older one is not history
 * worth keeping — it is a strictly worse answer to the same question. A
 * vehicle absent from the batch in hand is no longer on the camera.
 *
 * One state update per batch rather than one per vehicle, which is the whole
 * point of the channel: at five batches a second this re-renders five times a
 * second whether there is one car in frame or twenty.
 *
 * Ordering needs no handling here. Redis Streams and the WebSocket both
 * preserve it, so the last batch to arrive is the last one sent.
 */
export function useCameraBoxes(cameraCode: string | null): LiveTrackBatchEvent | null {
  const { subscribe } = useEventStream()
  const [batch, setBatch] = useState<LiveTrackBatchEvent | null>(null)

  useEffect(() => {
    // Cleared on every change of camera, for the same reason `useCameraEvents`
    // clears its buffer: boxes from the camera we just left drawn over the one
    // we just opened would invent results for a feed that may be reading
    // nothing at all.
    setBatch(null)
    if (!cameraCode) return

    const wanted = cameraCode.toLowerCase()
    return subscribe((event) => {
      if (!isTrackBatch(event)) return
      if ((event.source?.camera_id ?? '').toLowerCase() !== wanted) return
      setBatch(event)
    })
  }, [cameraCode, subscribe])

  return batch
}
