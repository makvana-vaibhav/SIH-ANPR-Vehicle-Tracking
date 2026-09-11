/**
 * One KPI tile. Dashboard's counters, the map's status strip and Fleet
 * Health's cards were three separate implementations of this same idea, each
 * slightly different in radius, padding and label size — the kind of drift
 * that makes a product feel like it was assembled from parts rather than
 * designed as one thing.
 *
 * `variant="strip"` drops the tile's own border and radius for the rare case
 * where the parent already draws the dividers (a `gap-px bg-border` grid) —
 * the composition stays, only the duplicated chrome goes.
 */

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export type StatTone = 'good' | 'warn' | 'bad'

const TONE_CLASS: Record<StatTone, string> = {
  good: 'text-status-online',
  warn: 'text-priority-high',
  bad: 'text-status-offline',
}

export function StatTile({
  label,
  value,
  detail,
  tone,
  to,
  variant = 'card',
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  tone?: StatTone
  to?: string
  variant?: 'card' | 'strip'
}) {
  const shell =
    variant === 'card'
      ? 'block rounded-md border border-border bg-card px-4 py-3 transition'
      : 'block bg-card px-4 py-3 transition'

  const body = (
    <>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 font-mono text-2xl leading-none ${tone ? TONE_CLASS[tone] : ''}`}>
        {value}
      </p>
      {detail && <p className="mt-1 text-[11px] text-muted-foreground">{detail}</p>}
    </>
  )

  return to ? (
    <Link to={to} className={`${shell} hover:border-muted-foreground/50`}>
      {body}
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  )
}
