/**
 * Live ANPR: watch a camera and see what the pipeline reads off it.
 *
 * Two sources of truth sit side by side deliberately. The video with its
 * overlay shows *where* a vehicle was when it was read; the feed beside it
 * shows *what* was read, with the evidence. The feed never lies about the
 * reading itself, so the two together are honest in a way either alone would
 * not be.
 *
 * ## Where the boxes come from, and why they are not late
 *
 * A box has to appear early and has to be drawn on the vehicle, which keeps
 * moving after the frame it was measured on. Four things carry that weight, and
 * none of them is a delay the viewer has to accept:
 *
 * **A box appears as soon as a plate is *localised*** — when the detector has
 * found a plate and can say where it is, before OCR has read it and whether or
 * not OCR ever does. Waiting for text cost about a second of consensus, and on
 * this fleet two-thirds of tracked vehicles never yield a reading at all. The
 * picture distinguishes the three states, so a rectangle meaning "there is a
 * plate here" is never mistaken for one meaning "this vehicle is identified".
 *
 * **The worker publishes one message per camera per tick** — `camera.tracks`,
 * carrying every drawable vehicle at once, up to five times a second (every
 * frame it analyses, when it is analysing fewer than that). Because that
 * message describes the whole camera, a box also goes away the moment the car
 * does, rather than waiting out a timeout.
 *
 * **The player says which capture instant is on screen** — read from the HLS
 * programme-date tags, or measured from WebRTC's own receive lag — so a box is
 * scheduled against the picture rather than against now. **The overlay then
 * predicts** from the vehicle's own measured velocity across the gap between
 * that instant and the position it was last told about.
 *
 * The extra buffer below is the last resort, not the mechanism: it holds the
 * picture deliberately behind live so that every event arrives before its frame
 * is shown. Worth spending when the worker is loaded heavily enough that
 * capture-to-event runs into seconds, and not otherwise.
 *
 * The camera list is the ANPR fleet. Every camera in it is labelled with where
 * its video comes from rather than left to be guessed at — a judge is entitled
 * to know whether they are looking at a live camera or a replayed clip, and a
 * platform that blurred the two would be misrepresenting the capability being
 * demonstrated. The demonstration fleet is three cameras on one corridor, all
 * replaying the same recorded footage, and each says so.
 *
 * Analysis does not depend on this screen. Opening a camera shows what it
 * found; it does not cause it to look.
 *
 * ANPR runs on the cameras a worker is assigned to, which by default is **one**
 * — every worker decodes its camera's full frame rate, so three of them sharing
 * a laptop tripled capture-to-event latency (measured 266 ms with one, 507 ms
 * with three, and 8.5 s when the footage was heavier still). A late reading
 * describes a car that has already moved on, and no amount of synchronisation
 * can put its box in the right place. `make ai-multi` starts all three when the
 * demo needs cross-camera linking.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import AnprOverlay from '@/components/AnprOverlay'
import PipelineDiagnostics from '@/components/PipelineDiagnostics'
import LivePlateFeed from '@/components/LivePlateFeed'
import StreamPlayer from '@/components/StreamPlayer'
import {
  Badge,
  Checkbox,
  ErrorBanner,
  InfoHint,
  PageHeader,
  SectionLabel,
  StatusDot,
} from '@/components/ui'
import { useCameraBoxes, useCameraEvents } from '@/hooks/useEventStream'
import * as api from '@/lib/api'
import { isPositionRefresh } from '@/lib/events'
import type { Camera, Detection, StreamGrant } from '@/lib/types'

/** Cameras with recorded ANPR activity float to the top of the picker. */
const RECENT_WINDOW_HOURS = 6

/**
 * How far behind live to hold the picture when the extra buffer is asked for.
 *
 * It has to cover capture-to-event on the worker plus the hop through Redis,
 * the API and the socket — otherwise a read arrives after its frame has already
 * been shown and the box never appears. It also has to stay inside what the
 * gateway keeps available: MediaMTX holds seven one-second segments, so beyond
 * about six seconds there is nothing left to play.
 *
 * Not needed for ordinary alignment any more. The gateway's low-latency HLS
 * already holds back ~600 ms and WebRTC reports its own lag, so the overlay
 * knows what is on screen either way; this buys headroom for a worker whose
 * latency has run into seconds, which is a load problem rather than a
 * synchronisation one.
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

export default function LiveAnpr() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [selected, setSelected] = useState<Camera | null>(null)
  const [grant, setGrant] = useState<StreamGrant | null>(null)
  const [history, setHistory] = useState<Detection[]>([])
  const [showBoxes, setShowBoxes] = useState(true)
  // Off by default, and now genuinely optional rather than the price of
  // correct boxes: the delay it adds is real and is the single biggest
  // contributor to the video feeling laggy when you open a camera, while
  // alignment no longer depends on it. Kept because a worker under enough load
  // to push capture-to-event into seconds cannot be aligned any other way.
  const [syncBoxes, setSyncBoxes] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeCodes, setActiveCodes] = useState<Set<string>>(new Set())
  // Diagnostics are opt-in: the panel is for proving an optimisation worked,
  // not something an operator needs on screen during normal use.
  const [showDiagnostics, setShowDiagnostics] = useState(false)
  // The player hands its capture clock to the overlay through a render prop;
  // the diagnostics panel needs the same clock to measure how far behind the
  // source the picture is, so it is captured here as it goes past.
  const videoClockRef = useRef<(() => number | null) | null>(null)

  const liveEvents = useCameraEvents(selected?.camera_code ?? null)
  // The live-boxes channel. One message per camera per tick carrying every
  // vehicle the pipeline has localised a plate on, which is what the overlay
  // draws from — earlier than a reading, and an order cheaper than sending the
  // same boxes one vehicle at a time.
  const liveBoxes = useCameraBoxes(selected?.camera_code ?? null)

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
    // overflow-hidden, not auto: the three columns below each scroll their own
    // body, so the page itself must hold still. A video wall that scrolls out
    // from under the operator is worse than one that clips.
    <div className="flex h-full flex-col gap-4 overflow-hidden p-6">
      {/* Says which cameras are *being read*, not just which exist. Every
          camera streams, but ANPR runs on the ones a worker is assigned to —
          one by default, so a reading lands within a few hundred milliseconds
          of the frame it came from and its box sits on the right vehicle. */}
      <PageHeader
        title="Live ANPR"
        subtitle={
          <>
            <span>{cameras.length} cameras streaming</span>
            <span className="text-muted-foreground/40">·</span>
            <span className={activeCodes.size > 0 ? 'text-status-online' : undefined}>
              {activeCodes.size} being read
            </span>
            <InfoHint label="How analysis is assigned">
              Every camera streams; ANPR runs only on the cameras a worker is
              assigned to, which is one by default. A worker decodes its
              camera&rsquo;s full frame rate, so three sharing a laptop tripled
              capture-to-event latency — and a late reading describes a car that
              has already moved on. <code className="font-mono">make ai-multi</code>{' '}
              starts all three when the demo needs cross-camera linking.
              Opening a camera shows what it found; it does not cause it to look.
            </InfoHint>
          </>
        }
      />

      {error && <ErrorBanner>{error}</ErrorBanner>}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[190px_minmax(0,1fr)_320px]">
        {/* ── Camera picker ─────────────────────────────────────────── */}
        <aside className="flex min-h-0 flex-col">
          <SectionLabel>Cameras</SectionLabel>
          {/* A dense list rather than a stack of bordered cards: this is a
              picker for twelve to sixty entries, and one bordered box per row
              made the rail read as sixty separate panels. */}
          <ul className="mt-1.5 min-h-0 flex-1 overflow-y-auto rounded-md border border-border">
            {ordered.map((camera) => {
              const active = camera.id === selected?.id
              const reading = activeCodes.has(camera.camera_code)
              return (
                <li key={camera.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(camera)}
                    aria-current={active}
                    className={`relative w-full border-b border-border/60 px-2.5 py-1.5 text-left transition last:border-0 ${
                      active ? 'bg-primary/10' : 'hover:bg-secondary/40'
                    }`}
                  >
                    {active && (
                      <span
                        aria-hidden
                        className="absolute inset-y-0 left-0 w-[3px] bg-primary"
                      />
                    )}
                    <span className="flex items-center gap-1.5">
                      <StatusDot status={camera.status} />
                      <span className="font-mono text-[11px] font-semibold">
                        {camera.camera_code}
                      </span>
                      {/* Provenance, always. A viewer should never have to
                          wonder whether a feed is a real camera or a clip —
                          but a dot carries it at this density. */}
                      {isDemoFeed(camera) && (
                        <span
                          title="Recorded clip, not a live camera"
                          className="h-1.5 w-1.5 rounded-full bg-priority-high"
                        />
                      )}
                      {reading && (
                        <span className="ml-auto text-[9px] font-semibold uppercase tracking-wider text-status-online">
                          reading
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                      {camera.name}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </aside>

        {/* ── Video ─────────────────────────────────────────────────── */}
        <section className="flex min-h-0 flex-col gap-2 overflow-y-auto">
          {selected && grant ? (
            <>
              <StreamPlayer
                whepUrl={grant.whep_url}
                hlsUrl={grant.hls_url}
                cameraCode={selected.camera_code}
                preferHls={isSandbox}
                syncDelayMs={syncBoxes ? SYNC_DELAY_MS : 0}
                overlay={(videoClock) => {
                  videoClockRef.current = videoClock
                  return (
                    <AnprOverlay
                      batch={liveBoxes}
                      // Still passed, and used only for a camera that
                      // publishes no batches — a worker predating the channel,
                      // or the simulator's load mode. The overlay ignores them
                      // for drawing once a batch has arrived.
                      events={liveEvents}
                      camera={selected.camera_code}
                      enabled={showBoxes}
                      // Always. The clock says which capture instant is on
                      // screen, and both transports can now answer — HLS from
                      // its programme-date tags, WebRTC from its own measured
                      // receive lag. Withholding it unless the extra buffer
                      // was ticked left the overlay guessing on the default
                      // path, which is the one everybody actually watches.
                      videoClock={videoClock}
                    />
                  )
                }}
              />
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{selected.name}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {[selected.city, selected.district, selected.vms_name]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Checkbox
                    checked={showBoxes}
                    onChange={(e) => setShowBoxes(e.target.checked)}
                    label="plate boxes"
                    labelClassName="gap-1.5 text-[11px] text-muted-foreground"
                  />
                  <span className="flex items-center gap-1">
                    <Checkbox
                      checked={syncBoxes}
                      onChange={(e) => setSyncBoxes(e.target.checked)}
                      label="extra buffer"
                      labelClassName="gap-1.5 text-[11px] text-muted-foreground"
                    />
                    <InfoHint label="What the extra buffer does" align="right">
                      Holds the picture {(SYNC_DELAY_MS / 1000).toFixed(1)}s
                      behind live, so every reading arrives before the frame it
                      describes is shown. Boxes are aligned to the picture either
                      way — turn this on only when the worker is loaded enough
                      that readings arrive seconds late.
                    </InfoHint>
                  </span>
                  <Checkbox
                    checked={showDiagnostics}
                    onChange={(e) => setShowDiagnostics(e.target.checked)}
                    label="latency"
                    labelClassName="gap-1.5 text-[11px] text-muted-foreground"
                  />
                </div>
              </div>
              {showDiagnostics && (
                <PipelineDiagnostics
                  events={liveEvents}
                  videoClock={videoClockRef.current}
                  transport={grant.whep_url ? 'webrtc/hls' : 'hls'}
                />
              )}

              {/* Provenance and transport, as facts rather than as an essay.
                  A viewer is entitled to know whether they are looking at a
                  live camera or a replayed clip — but that is one word plus a
                  marker, not a paragraph under every camera they open. The
                  reasoning is intact behind the hints. */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded border border-border bg-card/60 px-2.5 py-1.5 text-[11px] text-muted-foreground">
                {isDemoFeed(selected) ? (
                  <span className="flex items-center gap-1.5">
                    <Badge tone="warning">recorded</Badge>
                    same clip on 3 cameras, offset starts
                    <InfoHint label="Why this feed is recorded footage">
                      Recorded footage, not a live camera. The three cameras on
                      this corridor replay the same file, so the pipeline can be
                      demonstrated end to end on traffic close enough for plates
                      to be legible — and so a vehicle genuinely passes more than
                      one camera, which is what cross-camera linking needs in
                      order to have anything to link. Each camera is seeked to a
                      different point in the clip; they are not showing the same
                      instant.
                    </InfoHint>
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5">
                    <Badge tone="primary">live</Badge>
                    pulled on demand
                    <InfoHint label="What affects plate legibility here">
                      Plate legibility depends entirely on what the camera can
                      see. Vehicles are detected and tracked regardless, but a
                      plate at 40–60&nbsp;px in glare is frequently unreadable.
                      Nothing is invented when a plate cannot be read — the feed
                      simply stays empty.
                    </InfoHint>
                  </span>
                )}

                <span className="flex items-center gap-1.5">
                  {syncBoxes ? (
                    <>
                      <span className="text-foreground">
                        buffered {(SYNC_DELAY_MS / 1000).toFixed(1)}s
                      </span>
                      · boxes exact
                    </>
                  ) : (
                    <>
                      <span className="text-foreground">live</span>· boxes predicted
                    </>
                  )}
                  <InfoHint label="How the boxes are placed">
                    {syncBoxes ? (
                      <>
                        The picture is held {(SYNC_DELAY_MS / 1000).toFixed(1)}s
                        behind live, so every reading is already here before the
                        frame it describes is shown and no box has to be
                        predicted at all.
                      </>
                    ) : (
                      <>
                        The lowest-latency picture the transport allows. A box
                        appears as soon as a plate is located — dashed until it
                        has been read — placed against the capture instant on
                        screen and carried forward on the vehicle&rsquo;s own
                        measured velocity, so it lands on the car rather than
                        behind it.
                      </>
                    )}{' '}
                    Every box carries the pipeline&rsquo;s own capture-to-event
                    figure; tick <em>latency</em> to see it aggregated. The feed
                    on the right is the authoritative record of what was read.
                  </InfoHint>
                </span>
              </div>

              {/* What this camera read before the page was opened. It sits
                  here rather than beside the live feed because the two answer
                  different questions — "what is happening now" belongs next to
                  the picture, "what already happened" does not — and because
                  stacked in the right rail it was the thing that got cut off. */}
              {history.length > 0 && (
                <div className="pt-1">
                  <SectionLabel>Earlier on this camera</SectionLabel>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {history.map((row) => (
                      <li
                        key={row.id}
                        className="flex items-baseline gap-2 rounded border border-border bg-card px-2 py-1"
                      >
                        <span className="font-mono text-[11px]">{row.plate}</span>
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          {api.formatIST(row.ts, false)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
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
        <section className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <div>
            <SectionLabel>
              Reading now
              <InfoHint label="What this feed is">
                The authoritative record of what the pipeline read. Unlike the
                boxes on the video, nothing here depends on lining up with a
                moving picture — each row carries how many frames were involved,
                whether they agreed, and whether the grammar had to repair the
                string.
              </InfoHint>
            </SectionLabel>
            <div className="mt-1.5">
              <LivePlateFeed
                events={readings}
                emptyMessage={
                  selected
                    ? `Nothing read on ${selected.camera_code} yet.`
                    : 'Select a camera.'
                }
              />
            </div>
          </div>

        </section>
      </div>
    </div>
  )
}
