/**
 * A slide-over panel for work that has its own form.
 *
 * The alternative this replaces is putting the form permanently at the top of
 * the page, which is what Cameras did: a screen called "Cameras" opened on a
 * twenty-field onboarding form and pushed the actual list of cameras entirely
 * below the fold. Onboarding is the rarer act; looking at the fleet is the
 * common one, and the common one should be what the page opens on.
 *
 * A slide-over rather than a modal because these forms are long, and because
 * the list behind them is context worth keeping visible — an operator amending
 * a camera can still see the row they picked.
 */

import { useEffect, type ReactNode } from 'react'

import { Icon } from './Icon'

export function Drawer({
  open,
  title,
  description,
  onClose,
  children,
  width = 'w-[min(40rem,92vw)]',
}: {
  open: boolean
  title: string
  /** One line of scope. Not a place to explain the form. */
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  width?: string
}) {
  // Escape closes it. A panel this large with only a small × in the corner is
  // a panel people get stuck in.
  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-background/70 backdrop-blur-[2px]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`relative flex h-full flex-col border-l border-border bg-card shadow-2xl ${width}`}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">{title}</h2>
            {description && (
              <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close panel"
            className="rounded p-1 text-muted-foreground transition hover:bg-secondary hover:text-foreground"
          >
            <Icon name="close" size={16} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      </aside>
    </div>
  )
}
