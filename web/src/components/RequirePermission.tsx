/**
 * A screen the current role is allowed to open.
 *
 * Hiding a nav item stops it being *clicked*; it does not stop it being
 * typed, bookmarked, or linked to from a handover note. Without this, an
 * auditor who opens `/vehicles` gets the screen, watches it fire requests, and
 * sees an error panel — which reads as a fault rather than as a boundary.
 *
 * The API refuses these calls regardless. This exists so the refusal is
 * legible: it says which permission is missing and who to ask, rather than
 * leaving somebody to guess whether the system is broken.
 */

import { Link } from 'react-router-dom'

import { useAuth } from '@/hooks/useAuth'
import { ROLE_SUMMARY, type Permission } from '@/lib/permissions'

interface Props {
  /** Any one of these is enough to open the screen. */
  anyOf: Permission[]
  /** What the screen is, for the refusal message. */
  label: string
  children: React.ReactNode
}

export default function RequirePermission({ anyOf, label, children }: Props) {
  const { user, can } = useAuth()

  if (anyOf.some((permission) => can(permission))) {
    return <>{children}</>
  }

  const summary = user?.role ? ROLE_SUMMARY[user.role] : undefined

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md rounded-md border border-border bg-card px-6 py-5 text-center">
        <p className="text-sm font-medium">{label} is not available to your role</p>

        <p className="mt-2 text-xs text-muted-foreground">
          You are signed in as{' '}
          <span className="font-medium text-foreground">{user?.username}</span> with
          the role{' '}
          <span className="font-medium text-foreground">{user?.role}</span>
          {summary ? <> — {summary.toLowerCase()}.</> : '.'}
        </p>

        {/* Naming the permission turns "it doesn't work" into a request an
            administrator can act on without a conversation. */}
        <p className="mt-2 text-[11px] text-muted-foreground">
          This screen needs{' '}
          <code className="rounded bg-muted px-1 py-px font-mono">
            {anyOf.join(' or ')}
          </code>
          . An administrator can grant it by changing your role.
        </p>

        <Link
          to="/"
          className="mt-4 inline-block rounded bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90"
        >
          Back to what you can see
        </Link>
      </div>
    </div>
  )
}
