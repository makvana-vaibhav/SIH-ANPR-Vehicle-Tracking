/**
 * Live ANPR: watch a camera and see what the pipeline reads off it.
 *
 * Two sources of truth sit side by side deliberately. The video with its
 * overlay shows *where* a vehicle was when it was read; the feed beside it
 * shows *what* was read, with the evidence. The overlay can trail the picture
 * by a few seconds (see AnprOverlay) — the feed never lies about the reading
 * itself, so the two together are honest in a way either alone would not be.
 *
 * The camera list is the real fleet: the organisers' grid alongside our own
 * simulated cameras. Which is which is shown rather than hidden, because a
 * judge is entitled to know whether they are looking at a government feed or a
 * replayed clip.
 */

import { useEffect, useMemo, useState } from 'react'

import AnprOverlay from '@/components/AnprOverlay'
import LivePlateFeed from '@/components/LivePlateFeed'
import StreamPlayer from '@/components/StreamPlayer'
import { useCameraEvents, useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import type { Camera, Detection, StreamGrant } from '@/lib/types'

/** Cameras with recorded ANPR activity float to the top of the picker. */
const RECENT_WINDOW_HOURS = 6

export default function LiveAnpr() {
  const { status: streamStatus } = useEventStream()

  const [cameras, setCameras] = useState<Camera[]>([])
  const [selected, setSelected] = useState<Camera | null>(null)
  const [grant, setGrant] = useState<StreamGrant | null>(null)
  const [history, setHistory] = useState<Detection[]>([])
  const [showBoxes, setShowBoxes] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCodes, setActiveCodes] = useState<Set<string>>(new Set())

  const liveEvents = useCameraEvents(selected?.camera_code ?? null)

  // ── The fleet, and which cameras have actually produced plates ──────────
  useEffect(() => {
    async function load() {
      try {
        const [page, recent] = await Promise.all([
          api.getCameras({ limit: '500', anpr_enabled: 'true' }),
          api.getDetections({
            since: new Date(
              Date.now() - RECENT_WINDOW_HOURS * 3_600_000,
            ).toISOString(),
            limit: 200,
          }),
        ])
        setCameras(page.items)
        setActiveCodes(new Set(recent.items.map((d) => d.camera_code).filter(Boolean)))
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
  }, [])

  // Cameras that have read a plate first, then online, then the rest. The
  // picker should open on something worth looking at.
  const ordered = useMemo(() => {
    return [...cameras].sort((a, b) => {
      const activeA = activeCodes.has(a.camera_code) ? 0 : 1
      const activeB = activeCodes.has(b.camera_code) ? 0 : 1
      if (activeA !== activeB) return activeA - activeB
      const onlineA = a.status === 'online' ? 0 : 1
      const onlineB = b.status === 'online' ? 0 : 1
      if (onlineA !== onlineB) return onlineA - onlineB
      return a.camera_code.localeCompare(b.camera_code)
    })
  }, [cameras, activeCodes])

  useEffect(() => {
    if (!selected && ordered.length > 0) setSelected(ordered[0] ?? null)
  }, [ordered, selected])

  // ── Open the stream and load what this camera read before we arrived ────
  useEffect(() => {
    if (!selected) return
    let cancelled = false

    async function open(camera: Camera) {
      setGrant(null)
      setHistory([])
      try {
        const [streamGrant, past] = await Promise.all([
          api.openStream(camera.id),
          api.getDetections({ camera_id: camera.id, limit: 30 }),
        ])
        if (cancelled) return
        setGrant(streamGrant)
        setHistory(past.items)
        setError(null)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      }
    }

    void open(selected)
    return () => {
      cancelled = true
    }
  }, [selected])

  const isSandbox = selected?.vms_name?.toLowerCase().includes('sandbox') ?? false

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Live ANPR</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Plates read off the stream by the AI worker, as they happen.
          </p>
        </div>
        <span
          className={`flex items-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium ${
            streamStatus === 'live'
              ? 'bg-status-online/15 text-status-online'
              : 'bg-amber-500/15 text-amber-400'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              streamStatus === 'live'
                ? 'animate-pulse-alert bg-status-online'
                : 'bg-amber-400'
            }`}
          />
          event feed {streamStatus}
        </span>
      </header>

      {error && (
        <p className="rounded border border-status-offline/40 bg-status-offline/10 px-4 py-2 text-sm text-status-offline">
          {error}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)_300px]">
        {/* ── Camera picker ─────────────────────────────────────────── */}
        <aside className="space-y-1 lg:max-h-[70vh] lg:overflow-y-auto">
          <p className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {cameras.length} ANPR cameras
          </p>
          {ordered.map((camera) => {
            const active = camera.id === selected?.id
            return (
              <button
                key={camera.id}
                type="button"
                onClick={() => setSelected(camera)}
                className={`w-full rounded-md border px-2 py-1.5 text-left transition ${
                  active
                    ? 'border-primary bg-primary/10'
                    : 'border-border hover:border-muted-foreground/40'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono text-[11px] font-semibold">
                    {camera.camera_code}
                  </span>
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      camera.status === 'online'
                        ? 'bg-status-online'
                        : camera.status === 'offline'
                          ? 'bg-status-offline'
                          : 'bg-muted-foreground'
                    }`}
                  />
                </div>
                <p className="truncate text-[10px] text-muted-foreground">
                  {camera.name}
                </p>
                {activeCodes.has(camera.camera_code) && (
                  <span className="mt-0.5 inline-block rounded bg-status-online/15 px-1 text-[9px] text-status-online">
                    reading plates
                  </span>
                )}
              </button>
            )
          })}
        </aside>

        {/* ── Video ─────────────────────────────────────────────────── */}
        <section className="space-y-2">
          {selected && grant ? (
            <>
              <StreamPlayer
                whepUrl={grant.whep_url}
                hlsUrl={grant.hls_url}
                cameraCode={selected.camera_code}
                preferHls={isSandbox}
                overlay={<AnprOverlay events={liveEvents} enabled={showBoxes} />}
              />
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <div>
                  <p className="font-medium">{selected.name}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {[selected.city, selected.district, selected.vms_name]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>
                <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={showBoxes}
                    onChange={(e) => setShowBoxes(e.target.checked)}
                    className="accent-primary"
                  />
                  plate boxes
                </label>
              </div>
              <p className="text-[10px] leading-relaxed text-muted-foreground">
                Boxes mark where a vehicle was when it was read, and carry their
                own age. Inference runs on the worker, so a read lands a second
                or two after the frame it came from — the feed on the right is
                the authoritative record of what was read.
              </p>
            </>
          ) : (
            <div className="flex aspect-video items-center justify-center rounded-md border border-dashed border-border">
              <p className="text-xs text-muted-foreground">
                {selected ? 'Opening stream…' : 'Select a camera'}
              </p>
            </div>
          )}
        </section>

        {/* ── Plate feed ────────────────────────────────────────────── */}
        <section className="space-y-3 lg:max-h-[70vh] lg:overflow-y-auto">
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Reading now
            </h2>
            <div className="mt-1.5">
              <LivePlateFeed
                events={liveEvents}
                emptyMessage={
                  selected
                    ? `Nothing read on ${selected.camera_code} yet. Plates appear here the moment the worker reads one.`
                    : 'Select a camera.'
                }
              />
            </div>
          </div>

          {history.length > 0 && (
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Earlier on this camera
              </h2>
              <ul className="mt-1.5 space-y-1">
                {history.map((row) => (
                  <li
                    key={row.id}
                    className="flex items-baseline justify-between gap-2 rounded border border-border px-2 py-1"
                  >
                    <span className="font-mono text-xs">{row.plate}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {api.formatIST(row.ts, false)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
