/**
 * Every status pill, priority pill and role pill in the product, from one
 * place. Before this, "online" was a dot in MapView, a badge with a
 * differently-shaped border in Cameras, and a plain word in LiveAnpr — the
 * same fact rendered three ways depending which file happened to draw it.
 *
 * Colour always comes from the semantic tokens in `index.css`
 * (`status-*` / `priority-*`), never a raw Tailwind swatch — a stray
 * `amber-500` here would drift from `priority-high` the next time either one
 * is retuned.
 */

import type { ReactNode } from 'react'

import type { CameraStatus, Priority, Role } from '@/lib/types'

export type BadgeTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const TONE_CLASS: Record<BadgeTone, string> = {
  neutral: 'border-border bg-muted text-muted-foreground',
  primary: 'border-primary/40 bg-primary/15 text-primary',
  success: 'border-status-online/40 bg-status-online/15 text-status-online',
  warning: 'border-priority-high/40 bg-priority-high/15 text-priority-high',
  danger: 'border-status-offline/40 bg-status-offline/15 text-status-offline',
}

/** A generic labelled pill for one-off facts ("recorded demo", "validation
 *  only") that do not carry a camera/priority/role semantic of their own. */
export function Badge({
  tone = 'neutral',
  children,
  title,
}: {
  tone?: BadgeTone
  children: ReactNode
  title?: string
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  )
}

const STATUS_TONE: Record<CameraStatus, { dot: string; badge: string }> = {
  online: {
    dot: 'bg-status-online',
    badge: 'border-status-online/40 bg-status-online/15 text-status-online',
  },
  offline: {
    dot: 'bg-status-offline',
    badge: 'border-status-offline/40 bg-status-offline/15 text-status-offline',
  },
  degraded: {
    dot: 'bg-status-degraded',
    badge: 'border-status-degraded/40 bg-status-degraded/15 text-status-degraded',
  },
  unknown: {
    dot: 'bg-status-unknown',
    badge: 'border-status-unknown/40 bg-status-unknown/15 text-status-unknown',
  },
}

/** The dot alone — camera markers, list rows, anywhere a full badge is too
 *  heavy for the density of the row. */
export function StatusDot({
  status,
  className = '',
}: {
  status: CameraStatus
  className?: string
}) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${STATUS_TONE[status].dot} ${className}`}
    />
  )
}

export function StatusBadge({ status, label }: { status: CameraStatus; label?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${STATUS_TONE[status].badge}`}
    >
      <StatusDot status={status} />
      {label ?? status}
    </span>
  )
}

const PRIORITY_TONE: Record<Priority, { dot: string; badge: string; solid: string }> = {
  critical: {
    dot: 'bg-status-offline',
    badge: 'border-status-offline/40 bg-status-offline/15 text-status-offline',
    solid: 'bg-status-offline text-white',
  },
  high: {
    dot: 'bg-priority-high',
    badge: 'border-priority-high/40 bg-priority-high/15 text-priority-high',
    solid: 'bg-priority-high text-black',
  },
  medium: {
    dot: 'bg-primary',
    badge: 'border-primary/40 bg-primary/15 text-primary',
    solid: 'bg-primary text-primary-foreground',
  },
  low: {
    dot: 'bg-muted-foreground',
    badge: 'border-border bg-muted text-muted-foreground',
    solid: 'bg-muted text-muted-foreground',
  },
}

export function PriorityDot({
  priority,
  className = '',
}: {
  priority: Priority
  className?: string
}) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${PRIORITY_TONE[priority].dot} ${className}`}
    />
  )
}

/** `solid` is the loud variant, for the one or two badges on a screen that
 *  need to read before anything else (an alert row's priority). Everywhere
 *  else the outline variant keeps the palette from shouting. */
export function PriorityBadge({
  priority,
  solid = false,
}: {
  priority: Priority
  solid?: boolean
}) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${
        solid ? PRIORITY_TONE[priority].solid : `border ${PRIORITY_TONE[priority].badge}`
      }`}
    >
      {priority}
    </span>
  )
}

const ROLE_TONE: Record<Role, string> = {
  admin: 'border-status-offline/40 bg-status-offline/15 text-status-offline',
  supervisor: 'border-priority-high/40 bg-priority-high/15 text-priority-high',
  operator: 'border-primary/40 bg-primary/15 text-primary',
  analyst: 'border-border bg-muted text-muted-foreground',
  auditor: 'border-border bg-muted text-muted-foreground',
  api_client: 'border-border bg-muted text-muted-foreground',
}

export function RoleBadge({ role, title }: { role: Role; title?: string }) {
  return (
    <span
      title={title}
      className={`rounded border px-1.5 py-px text-[10px] font-medium uppercase ${ROLE_TONE[role]}`}
    >
      {role}
    </span>
  )
}
