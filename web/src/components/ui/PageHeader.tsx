/**
 * The band every screen opens with.
 *
 * Twelve pages each hand-rolled this as a `<header className="flex flex-wrap
 * items-end justify-between gap-3">` with an `h1` at one of three sizes and a
 * subtitle at one of four. The result was that moving between screens felt
 * like moving between applications.
 *
 * `subtitle` is deliberately typed as a string and meant to stay under about a
 * dozen words: it says what the screen is scoped to right now ("last 24 h · all
 * corridors", "58 cameras · 3 being read"), not what the screen is for. The
 * explaining goes in an InfoHint.
 */

import type { ReactNode } from 'react'

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  /** Current scope, not a description. Keep it short. */
  subtitle?: ReactNode
  /** Filters, toggles and the connection badge. */
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold leading-tight tracking-tight">{title}</h1>
        {subtitle && (
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            {subtitle}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-end gap-3">{actions}</div>
      )}
    </header>
  )
}

/**
 * The small uppercase label above a group of content.
 *
 * Was written out inline as `text-xs font-semibold uppercase tracking-wider
 * text-muted-foreground` in nineteen places, with the size drifting between
 * `text-xs`, `text-[11px]` and `text-[10px]` depending on the file.
 */
export function SectionLabel({
  children,
  action,
}: {
  children: ReactNode
  /** A link or control that belongs to this group, set to the right. */
  action?: ReactNode
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        {children}
      </h2>
      {action}
    </div>
  )
}
