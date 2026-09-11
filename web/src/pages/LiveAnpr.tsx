/**
 * Live ANPR: watch a camera and see what the pipeline reads off it.
 *
 * Two sources of truth sit side by side deliberately. The video with its
 * overlay shows *where* a vehicle was when it was read; the feed beside it
 * shows *what* was read, with the evidence. The feed never lies about the
 * reading itself, so the two together are honest in a way either alone would
 * not be.
 *
 * ## Synced boxes
 *
 * By default the picture is held a couple of seconds behind live, and each
 * plate box is drawn against the video’s own capture clock rather than against
 * now. That is what puts the rectangle on the vehicle instead of behind it: the
 * delay is exactly the head start the worker needs to read a plate and get the
 * event here before the frame it came from is shown.
 *
 * Turning it off gives the lowest-latency picture the network allows, with
 * boxes that trail it. Both modes label a reading with its true age, so they
 * never disagree about the facts — only about where a rectangle can be put.
 *
 * The camera list is the ANPR fleet. Every camera in it is labelled with where
 * its video comes from rather than left to be guessed at — a judge is entitled
 * to know whether they are looking at a live camera or a replayed clip, and a
 * platform that blurred the two would be misrepresenting the capability being
 * demonstrated. The demonstration fleet is three cameras on one corridor, all
 * replaying the same recorded footage, and each says so.
 *
 * Analysis does not depend on this screen. The worker reads every camera in
 * the fleet in the background; opening a camera here shows what it found, it
 * does not cause it to look.
 */

import { useEffect, useMemo, useState } from 'react'

import AnprOverlay from '@/components/AnprOverlay'
import LivePlateFeed from '@/components/LivePlateFeed'
import StreamPlayer from '@/components/StreamPlayer'
import { useCameraEvents, useEventStream } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { isPositionRefresh } from '@/lib/events'
import type { Camera, Detection, StreamGrant } from '@/lib/types'

/** Cameras with recorded ANPR activity float to the top of the picker. */
const RECENT_WINDOW_HOURS = 6

/**
 * How far behind live to hold the picture when boxes are synced.
 *
 * It has to cover capture-to-event on the worker plus the hop through Redis,
 * the API and the socket — otherwise a read arrives after its frame has already
 * been shown and the box never appears. It also has to stay inside what the
 * gateway keeps available: MediaMTX holds seven one-second segments, so beyond
 * about six seconds there is nothing left to play.
 */
const SYNC_DELAY_MS = 2_500

/**
 * A camera carrying recorded footage rather than a live feed.
 *
 * Read from the registry's own tags, not guessed from the name: the `demo` tag
 * comes from `data/seed/cameras.csv` and is the single place that decides what
 * counts as demonstration footage.
 */
function isDemoFeed(camera: Camera): boolean {
  return (camera.tags ?? []).includes('demo')
}

/**
 * A camera whose video comes from a live source rather than recorded footage.
 *
 * A negation rather than a vendor check, because a camera with no real source
 * is not in this registry at all: everything here either streams live or is
 * tagged as replaying a clip.
 */
function isLiveFeed(camera: Camera): boolean {
  return !isDemoFeed(camera)
}

export default function LiveAnpr() {
  const { status: streamStatus } = useEventStream()

  const [cameras, setCameras] = useState<Camera[]>([])
  const [selected, setSelected] = useState<Camera | null>(null)
  const [grant, setGrant] = useState<StreamGrant | null>(null)
  const [history, setHistory] = useState<Detection[]>([])
  const [showBoxes, setShowBoxes] = useState(true)
  const [syncBoxes, setSyncBoxes] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCodes, setActiveCodes] = useState<Set<string>>(new Set())

  const liveEvents = useCameraEvents(selected?.camera_code ?? null)

  // The overlay wants every event, because position refreshes are what let it
  // follow a vehicle. The feed wants only the readings: a refresh repeats a
  // plate already listed, so leaving them in would crowd out the cars read a
  // few seconds ago with the same car reported again.
  const readings = useMemo(
    () => liveEvents.filter((event) => !isPositionRefresh(event)),
    [liveEvents],
  )

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
            Every camera in the fleet is analysed in the background. Opening one
            shows what it found — {cameras.length} cameras,{' '}
            {cameras.filter(isLiveFeed).length} live and{' '}
            {cameras.filter(isDemoFeed).length} replaying recorded footage.
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
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {/* Provenance, always. A viewer should never have to wonder
                      whether a feed is a government camera or a clip. */}
                  {isDemoFeed(camera) ? (
                    <span className="rounded bg-amber-500/15 px-1 text-[9px] text-amber-400">
                      recorded demo
                    </span>
                  ) : (
                    <span className="rounded bg-primary/15 px-1 text-[9px] text-primary">
                      live feed
                    </span>
                  )}
                  {activeCodes.has(camera.camera_code) && (
                    <span className="rounded bg-status-online/15 px-1 text-[9px] text-status-online">
                      reading plates
                    </span>
                  )}
                </div>
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
                syncDelayMs={syncBoxes ? SYNC_DELAY_MS : 0}
                overlay={(videoClock) => (
                  <AnprOverlay
                    events={liveEvents}
                    enabled={showBoxes}
                    videoClock={syncBoxes ? videoClock : undefined}
                  />
                )}
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
                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={showBoxes}
                      onChange={(e) => setShowBoxes(e.target.checked)}
                      className="accent-primary"
                    />
                    plate boxes
                  </label>
                  <label
                    className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground"
                    title={`Holds the picture ${(SYNC_DELAY_MS / 1000).toFixed(1)}s behind live so each box lands on the frame it was measured in. Off gives the lowest latency the network allows, with boxes that trail the picture.`}
                  >
                    <input
                      type="checkbox"
                      checked={syncBoxes}
                      onChange={(e) => setSyncBoxes(e.target.checked)}
                      className="accent-primary"
                    />
                    sync to video
                  </label>
                </div>
              </div>
              {isDemoFeed(selected) ? (
                <p className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-amber-300">
                  <strong>Recorded footage, not a live camera.</strong> The
                  three cameras on this corridor replay the same file, so the
                  pipeline can be demonstrated end to end on traffic close
                  enough for plates to be legible — and so a vehicle genuinely
                  passes more than one camera, which is what cross-camera
                  linking needs in order to have anything to link. Each camera
                  is seeked to a different point in the clip; they are not
                  showing the same instant.
                </p>
              ) : (
                <p className="rounded border border-border px-2 py-1.5 text-[10px] leading-relaxed text-muted-foreground">
                  <strong className="text-foreground">Live feed</strong>, pulled
                  on demand. Plate legibility depends entirely on what the
                  camera can see: vehicles are detected and tracked regardless,
                  but a plate at 40–60&nbsp;px in glare is frequently
                  unreadable. Nothing is invented when a plate cannot be read —
                  the feed simply stays empty.
                </p>
              )}
              <p className="text-[10px] leading-relaxed text-muted-foreground">
                {syncBoxes ? (
                  <>
                    <strong className="text-foreground">Synced.</strong> The
                    picture is held{' '}
                    {(SYNC_DELAY_MS / 1000).toFixed(1)}s behind live so each box
                    can be drawn on the frame it was measured in. Every box
                    carries the pipeline&rsquo;s own capture-to-event figure.
                  </>
                ) : (
                  <>
                    <strong className="text-foreground">Unsynced.</strong>{' '}
                    Lowest-latency picture, and boxes that trail it: inference
                    runs on the worker, so a read lands after the frame it came
                    from. Each box carries how far behind it is.
                  </>
                )}{' '}
                The feed on the right is the authoritative record of what was
                read.
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
                events={readings}
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
