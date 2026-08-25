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
 * HLS is offered as a manual fallback: WebRTC is blocked on some corporate and
 * venue networks, and a demo that dies on hostile wifi is no demo at all.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

type PlayerState = 'idle' | 'connecting' | 'playing' | 'failed'

interface Props {
  whepUrl: string | null
  hlsUrl: string | null
  cameraCode: string
  /** Autoplay on mount. */
  autoStart?: boolean
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
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const [state, setState] = useState<PlayerState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [latencyNote, setLatencyNote] = useState<string | null>(null)

  const teardown = useCallback(() => {
    pcRef.current?.close()
    pcRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  const connect = useCallback(async () => {
    if (!whepUrl) {
      setError('No WebRTC endpoint for this camera')
      setState('failed')
      return
    }

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
        setState('failed')
        setError('WebRTC connection failed — try the HLS fallback below')
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
      setState('failed')
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [whepUrl, teardown])

  useEffect(() => {
    if (autoStart && whepUrl) void connect()
    return teardown
  }, [autoStart, whepUrl, connect, teardown])

  return (
    <div className="space-y-2">
      <div className="relative aspect-video overflow-hidden rounded-md border border-border bg-black">
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
            <p className="text-xs text-muted-foreground">Negotiating WebRTC…</p>
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
            Live · WebRTC
          </span>
        )}

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
        {hlsUrl && (
          <a
            href={hlsUrl}
            target="_blank"
            rel="noreferrer"
            className="rounded border border-border px-2 py-1 text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
            title="Higher latency, but traverses networks that block WebRTC"
          >
            HLS fallback ↗
          </a>
        )}
        {latencyNote && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {latencyNote}
          </span>
        )}
      </div>
    </div>
  )
}
