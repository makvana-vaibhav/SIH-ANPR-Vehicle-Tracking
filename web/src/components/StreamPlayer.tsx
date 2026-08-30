/**
 * Live video player.
 *
 * Speaks WHEP (WebRTC-HTTP Egress Protocol) directly against MediaMTX using
 * the browser's own RTCPeerConnection — no player library, no external script,
 * which keeps the offline guarantee intact.
 *
 * WHEP handshake:
 *   1. create a recvonly peer connection
 *   2. POST the SDP offer to the WHEP endpoint as `application/sdp`
 *   3. apply the SDP answer that comes back
 *
 * ICE candidates are gathered fully before the offer is sent (non-trickle).
 * That costs a moment of setup but avoids needing the PATCH endpoint, and on a
 * local gateway gathering completes almost immediately.
 *
 * HLS is the fallback, and it plays **in this element** rather than opening a
 * tab. WebRTC is blocked on plenty of corporate and venue networks — and the
 * organisers' own grid publishes HLS precisely for that case — so the fallback
 * has to be a real player, not a link. Safari plays HLS natively; everywhere
 * else hls.js drives Media Source Extensions. It is bundled, not fetched from a
 * CDN, so the offline guarantee holds.
 *
 * When WebRTC fails the player switches to HLS by itself. An operator watching
 * a junction should not have to know which transport their network permits.
 */

import Hls from 'hls.js'
import { useCallback, useEffect, useRef, useState } from 'react'

type PlayerState = 'idle' | 'connecting' | 'playing' | 'failed'
type Transport = 'webrtc' | 'hls'

interface Props {
  whepUrl: string | null
  hlsUrl: string | null
  cameraCode: string
  /** Autoplay on mount. */
  autoStart?: boolean
  /**
   * Start on HLS instead of trying WebRTC first. Federated grids that publish
   * HLS on 443 and WebRTC on a blocked port should set this: attempting WHEP
   * costs a visible failure before the fallback that was always going to win.
   */
  preferHls?: boolean
}

/** Wait for ICE gathering, with a ceiling so a stalled gather cannot hang the UI. */
function waitForIceGathering(pc: RTCPeerConnection, timeoutMs = 2000): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()

  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener('icegatheringstatechange', check)
      window.clearTimeout(timer)
      resolve()
    }
    const check = () => {
      if (pc.iceGatheringState === 'complete') done()
    }
    // Local candidates are usually enough on a laptop gateway; do not block
    // playback waiting for a gather that may never complete.
    const timer = window.setTimeout(done, timeoutMs)
    pc.addEventListener('icegatheringstatechange', check)
  })
}

export default function StreamPlayer({
  whepUrl,
  hlsUrl,
  cameraCode,
  autoStart = true,
  preferHls = false,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const hlsRef = useRef<Hls | null>(null)
  const [state, setState] = useState<PlayerState>('idle')
  const [transport, setTransport] = useState<Transport>(preferHls ? 'hls' : 'webrtc')
  const [error, setError] = useState<string | null>(null)
  const [latencyNote, setLatencyNote] = useState<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const teardown = useCallback(() => {
    pcRef.current?.close()
    pcRef.current = null
    hlsRef.current?.destroy()
    hlsRef.current = null
    if (videoRef.current) {
      videoRef.current.srcObject = null
      videoRef.current.removeAttribute('src')
      videoRef.current.load()
    }
  }, [])

  /** Play the HLS ladder in this element. */
  const playHls = useCallback(() => {
    const video = videoRef.current
    if (!video || !hlsUrl) {
      setError('No HLS endpoint for this camera')
      setState('failed')
      return
    }

    teardown()
    setTransport('hls')
    setState('connecting')
    setError(null)
    const started = performance.now()

    // Safari (and iOS) play HLS natively; handing it to hls.js there is both
    // unnecessary and worse, because the native path uses hardware decoding.
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Wait for actual video before claiming to be live. Setting src and
      // reporting success immediately is how a dead camera ends up labelled
      // "LIVE" over a black rectangle — the worst kind of wrong, because it
      // tells an operator a feed is healthy when nothing is arriving.
      const onPlaying = () => {
        cleanup()
        setState('playing')
        setLatencyNote(`HLS (native) in ${Math.round(performance.now() - started)} ms`)
      }
      const onError = () => {
        cleanup()
        setState('failed')
        setError(
          video.error?.code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
            ? 'No video is being published for this camera'
            : `Playback error (${video.error?.message || 'unknown'})`,
        )
      }
      const stall = window.setTimeout(() => {
        cleanup()
        setState('failed')
        setError('No video arrived within 15 seconds — nothing is publishing this camera')
      }, 15000)
      const cleanup = () => {
        window.clearTimeout(stall)
        video.removeEventListener('playing', onPlaying)
        video.removeEventListener('error', onError)
      }

      video.addEventListener('playing', onPlaying)
      video.addEventListener('error', onError)
      video.src = hlsUrl
      video.play().catch(() => undefined)
      return
    }

    if (!Hls.isSupported()) {
      setState('failed')
      setError('This browser cannot play HLS')
      return
    }

    const hls = new Hls({
      // A live wall wants the newest picture, not a smooth buffered one. These
      // keep the player near the live edge and let it catch up after a stall
      // rather than drifting further behind with every hiccup.
      lowLatencyMode: true,
      liveSyncDurationCount: 2,
      backBufferLength: 10,
      manifestLoadingMaxRetry: 3,
      fragLoadingMaxRetry: 4,
    })
    hlsRef.current = hls

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      video.play().catch(() => undefined)
    })

    // A parsed manifest is not a picture. Report live only once frames are
    // actually rendering, for the same reason as the native path above.
    const onPlaying = () => {
      setState('playing')
      setLatencyNote(`HLS in ${Math.round(performance.now() - started)} ms`)
    }
    video.addEventListener('playing', onPlaying, { once: true })

    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal) return
      // Network and media errors are usually recoverable, and a live feed that
      // gives up on the first bad segment is useless — the grid's own guide
      // warns that decoder complaints at join are normal and self-correct.
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad()
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError()
      } else {
        hls.destroy()
        hlsRef.current = null
        setState('failed')
        // H.265/HEVC is common on newer Indian CCTV and is not decodable by
        // Media Source Extensions in Chrome or Firefox — Safari can. Saying so
        // is far more useful than "playback failed", because the fix is to open
        // it in Safari, not to retry.
        if (data.details === Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR) {
          setError(
            'This camera streams H.265/HEVC, which this browser cannot decode. ' +
              'Safari plays it; Chrome and Firefox do not.',
          )
        } else {
          setError(`HLS failed: ${data.details}`)
        }
      }
    })

    hls.loadSource(hlsUrl)
    hls.attachMedia(video)
  }, [hlsUrl, teardown])

  const connect = useCallback(async () => {
    if (!whepUrl) {
      // Nothing to negotiate — go straight to the transport that exists.
      if (hlsUrl) {
        playHls()
        return
      }
      setError('No WebRTC or HLS endpoint for this camera')
      setState('failed')
      return
    }
    setTransport('webrtc')

    teardown()
    setState('connecting')
    setError(null)

    const started = performance.now()
    // No STUN/TURN: the gateway is on the same host, and reaching out to a
    // public STUN server would break the zero-external-dependency guarantee.
    const pc = new RTCPeerConnection({ iceServers: [] })
    pcRef.current = pc

    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })

    pc.ontrack = (event) => {
      const [stream] = event.streams
      if (videoRef.current && stream) {
        videoRef.current.srcObject = stream
      }
    }

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') {
        setState('playing')
        setLatencyNote(`connected in ${Math.round(performance.now() - started)} ms`)
      } else if (pc.connectionState === 'failed') {
        // Do not make the operator diagnose their own network.
        if (hlsUrl) {
          playHls()
        } else {
          setState('failed')
          setError('WebRTC connection failed and this camera has no HLS endpoint')
        }
      }
    }

    try {
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)

      const response = await fetch(whepUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription?.sdp ?? offer.sdp ?? '',
      })

      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? 'No live stream is being published for this camera'
            : `Gateway refused the stream (HTTP ${response.status})`,
        )
      }

      const answer = await response.text()
      await pc.setRemoteDescription({ type: 'answer', sdp: answer })
    } catch (err) {
      teardown()
      const message = err instanceof Error ? err.message : String(err)
      if (hlsUrl) {
        // The common case on a restricted network: WHEP is unreachable and HLS
        // over 443 is not. Switch rather than report a failure the operator
        // cannot act on.
        playHls()
        setLatencyNote(`WebRTC unavailable (${message}) — using HLS`)
      } else {
        setState('failed')
        setError(message)
      }
    }
  }, [whepUrl, hlsUrl, playHls, teardown])

  useEffect(() => {
    if (!autoStart) return teardown
    if (preferHls && hlsUrl) {
      playHls()
    } else if (whepUrl || hlsUrl) {
      void connect()
    }
    return teardown
  }, [autoStart, preferHls, hlsUrl, whepUrl, connect, playHls, teardown])

  // Track fullscreen from the document, so the Escape key and the browser's own
  // controls keep the button label honest.
  useEffect(() => {
    const onChange = () => setIsFullscreen(document.fullscreenElement === shellRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const toggleFullscreen = useCallback(() => {
    const shell = shellRef.current
    if (!shell) return
    if (document.fullscreenElement) {
      void document.exitFullscreen()
    } else {
      void shell.requestFullscreen?.().catch(() => undefined)
    }
  }, [])

  return (
    <div className="space-y-2">
      <div
        ref={shellRef}
        className="group relative aspect-video overflow-hidden rounded-md border border-border bg-black"
      >
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="h-full w-full object-contain"
        />

        {state === 'connecting' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-xs text-muted-foreground">
              {transport === 'hls' ? 'Buffering HLS…' : 'Negotiating WebRTC…'}
            </p>
          </div>
        )}

        {state === 'failed' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
            <p className="text-sm font-medium text-status-offline">
              Cannot play this stream
            </p>
            <p className="text-xs text-muted-foreground">{error}</p>
          </div>
        )}

        {state === 'playing' && (
          <span className="absolute left-2 top-2 flex items-center gap-1.5 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-status-online">
            <span className="h-1.5 w-1.5 animate-pulse-alert rounded-full bg-status-online" />
            Live · {transport === 'hls' ? 'HLS' : 'WebRTC'}
          </span>
        )}

        {/* Expand sits on the video itself, where a viewer expects it. */}
        <button
          type="button"
          onClick={toggleFullscreen}
          title={isFullscreen ? 'Exit full screen' : 'Full screen'}
          aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'}
          className="absolute bottom-2 right-2 rounded bg-black/60 px-2 py-1 text-xs text-white/80 opacity-0 transition hover:bg-black/80 hover:text-white focus:opacity-100 group-hover:opacity-100"
        >
          {isFullscreen ? '⤢ Exit' : '⤢ Full screen'}
        </button>

        <span className="absolute right-2 top-2 rounded bg-black/60 px-2 py-0.5 font-mono text-[10px] text-white/80">
          {cameraCode}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {state !== 'playing' && (
          <button
            type="button"
            onClick={() => void connect()}
            className="rounded border border-border px-2 py-1 transition hover:border-primary/50"
          >
            {state === 'failed' ? 'Retry' : 'Play'}
          </button>
        )}
        {hlsUrl && transport !== 'hls' && (
          <button
            type="button"
            onClick={playHls}
            className="rounded border border-border px-2 py-1 text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
            title="Higher latency, but traverses networks that block WebRTC"
          >
            Switch to HLS
          </button>
        )}
        {whepUrl && transport === 'hls' && (
          <button
            type="button"
            onClick={() => void connect()}
            className="rounded border border-border px-2 py-1 text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
            title="Lower latency where the network allows it"
          >
            Try WebRTC
          </button>
        )}
        <button
          type="button"
          onClick={toggleFullscreen}
          className="rounded border border-border px-2 py-1 text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
        >
          Full screen
        </button>
        {latencyNote && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {latencyNote}
          </span>
        )}
      </div>
    </div>
  )
}
