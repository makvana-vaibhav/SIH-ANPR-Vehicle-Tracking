/**
 * The affordance that lets a panel explain itself without saying it out loud.
 *
 * This product knows an unusual amount about why its own numbers are the shape
 * they are — that density is vehicles in view rather than per kilometre, that
 * a speed figure is a lower bound, that the demo fleet replays a clip so its
 * timing cannot yield a speed at all. All of that is worth keeping and none of
 * it belongs in a paragraph under every heading: an operator reads a screen
 * like this a hundred times a shift and stops seeing prose that never changes.
 *
 * So the caveat moves behind a marker. It is one click or one tab-stop away,
 * it is still on the screen, and the screen reads as figures rather than as
 * documentation.
 *
 * Implemented with a real `<button>` and CSS-only reveal rather than a `title`
 * attribute: `title` never appears on a keyboard focus, is unreadable to touch
 * users, and cannot hold the several sentences some of these carry.
 */

import { useId, useState, type ReactNode } from 'react'

import { Icon } from './Icon'

export function InfoHint({
  children,
  label = 'Why this number is what it is',
  align = 'left',
}: {
  children: ReactNode
  /** Announced to screen readers in place of the glyph. */
  label?: string
  /** Which edge the popover hangs from. `right` for markers near the viewport edge. */
  align?: 'left' | 'right'
}) {
  // Hover alone would strand keyboard and touch users, so open state is real
  // rather than a `:hover` selector. Both inputs drive the same flag.
  const [open, setOpen] = useState(false)
  const id = useId()

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        className="rounded-full p-0.5 text-muted-foreground/70 transition hover:text-foreground"
      >
        <Icon name="info" size={13} />
      </button>

      {open && (
        <span
          id={id}
          role="tooltip"
          className={`absolute top-[calc(100%+6px)] z-30 w-72 rounded-md border border-border bg-popover px-3 py-2 text-xs font-normal normal-case leading-relaxed tracking-normal text-muted-foreground shadow-xl ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {children}
        </span>
      )}
    </span>
  )
}
