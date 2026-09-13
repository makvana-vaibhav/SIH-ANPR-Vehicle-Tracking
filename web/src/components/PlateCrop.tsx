/**
 * The plate crop behind a detection or an alert.
 *
 * Why this is a component rather than an `<img>`: a crop is allowed to be
 * missing, and it must be missing *quietly*.
 *
 * The worker returns a crop's object key synchronously and uploads the bytes on
 * a background thread, so for a second or so after a detection the signed URL
 * points at an object that does not exist yet. An upload can also fail
 * outright. Left to an ordinary `<img>`, both cases — and even the ordinary
 * gap before a good image finishes loading — render as the browser's own
 * broken-image icon next to its `alt` text sitting in the row, which reads as
 * "the system is broken" rather than "the photograph has not landed".
 *
 * So nothing is mounted into the page until the image has already loaded
 * successfully: an offscreen probe does the fetching, and only a load that
 * succeeds gets a visible `<img>` at all. Pending and failed look identical —
 * nothing here — and the visible image never has a chance to flash its alt
 * text. A crop is evidence *about* a sighting, never the sighting itself, and
 * its absence must not undermine the alert it belongs to.
 */

import { useEffect, useState } from 'react'

interface Props {
  url: string | null
  plate: string | null
  /** Rendered height in pixels. Plates are wide and short. */
  height?: number
  className?: string
}

export default function PlateCrop({ url, plate, height = 34, className = '' }: Props) {
  const [ready, setReady] = useState(false)

  // A new URL starts back at nothing rather than keeping the last one on
  // screen — a stale crop from a different sighting is worse than a blank.
  useEffect(() => {
    setReady(false)
    if (!url) return

    const probe = new Image()
    probe.onload = () => setReady(true)
    probe.src = url
    return () => {
      probe.onload = null
    }
  }, [url])

  if (!url || !ready) return null

  return (
    <img
      // Already sitting in the browser's cache from the probe above, so this
      // paints immediately rather than fetching a second time.
      src={url}
      alt={plate ? `Number plate crop for ${plate}` : 'Number plate crop'}
      height={height}
      style={{ height }}
      className={
        'w-auto rounded border border-border bg-black/40 object-contain ' + className
      }
    />
  )
}
