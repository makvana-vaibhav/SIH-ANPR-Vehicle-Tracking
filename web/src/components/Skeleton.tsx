/**
 * Placeholders shown while data is on its way.
 *
 * CLAUDE.md's UI rule is that a screen must never block on a slow request.
 * The failure that rule guards against is subtler than a spinner: a screen
 * that renders empty and *then* fills reads as "there is nothing here" for as
 * long as the request takes, and an operator who concludes there are no alerts
 * stops looking.
 *
 * These occupy the shape of the thing that is coming, so the page does not
 * jump when it arrives and nothing is ever silently reported as absent.
 */

interface Props {
  className?: string
}

export function Skeleton({ className = '' }: Props) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded bg-muted/40 ${className}`}
    />
  )
}

/** A stand-in for a list of cards, e.g. alerts or plate reads. */
export function SkeletonRows({ rows = 4, height = 'h-14' }: { rows?: number; height?: string }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`w-full ${height}`} />
      ))}
    </div>
  )
}

/** A stand-in for a KPI tile. */
export function SkeletonStat() {
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3">
      <Skeleton className="h-2.5 w-20" />
      <Skeleton className="mt-2 h-6 w-14" />
    </div>
  )
}
