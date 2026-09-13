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

import { Icon } from './Icon'
import { InfoHint } from './InfoHint'

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
  info,
  tone,
  to,
  variant = 'card',
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  /** How the figure is arrived at, when that is not obvious from the label. */
  info?: ReactNode
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
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        {label}
        {info && <InfoHint>{info}</InfoHint>}
      </p>
      {/* Tracking is pulled in because these are large lining figures set in a
          mono face — at 24px the default letter-spacing makes "12,480" read as
          separated digits rather than as one number. */}
      <p
        className={`mt-1 font-mono text-2xl leading-none tracking-tight ${tone ? TONE_CLASS[tone] : ''}`}
      >
        {value}
      </p>
      {detail && (
        <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">{detail}</p>
      )}
    </>
  )

  // The arrow is the affordance that says a tile is a way into another screen.
  // Without it a linked tile and a static one are indistinguishable until the
  // cursor happens to land on one.
  return to ? (
    <Link
      to={to}
      className={`group ${shell} relative hover:border-muted-foreground/50`}
    >
      {body}
      <span className="absolute right-3 top-3 text-muted-foreground/40 transition group-hover:text-primary">
        <Icon name="arrowRight" size={14} />
      </span>
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  )
}
