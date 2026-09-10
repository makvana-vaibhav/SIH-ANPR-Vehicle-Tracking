/**
 * The plate crop behind a detection or an alert.
 *
 * Why this is a component rather than an `<img>`: a crop is allowed to be
 * missing, and it must be missing *quietly*.
 *
 * The worker returns a crop's object key synchronously and uploads the bytes on
 * a background thread, so for a second or so after a detection the signed URL
 * points at an object that does not exist yet. An upload can also fail
 * outright. In both cases the browser gets a 404 and, left alone, would render
 * a broken-image icon on an alert card — which reads as "the system is broken"
 * rather than "the photograph has not landed".
 *
 * So a failed load hides the element. A crop is evidence *about* a sighting,
 * never the sighting itself, and its absence must not undermine the alert it
 * belongs to.
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
  const [failed, setFailed] = useState(false)

  // A new URL deserves a new attempt: the same alert re-fetched a minute later
  // may well have its crop by then.
  useEffect(() => setFailed(false), [url])

  if (!url || failed) return null

  return (
    <img
      src={url}
      alt={plate ? `Number plate crop for ${plate}` : 'Number plate crop'}
      height={height}
      style={{ height }}
      onError={() => setFailed(true)}
      loading="lazy"
      className={
        'w-auto rounded border border-border bg-black/40 object-contain ' + className
      }
    />
  )
}
