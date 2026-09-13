/**
 * The navigation rail.
 *
 * Eleven screens used to share the top bar with the wordmark and the account
 * block, in one undifferentiated row. Nothing said that Alerts and Audit are
 * different kinds of thing, the account block was squeezed against the window
 * edge, and a twelfth screen had nowhere to go.
 *
 * Three groups, because there are three jobs here: watching what is happening
 * now, asking questions of what already happened, and running the estate. An
 * operator lives in the first, an analyst in the second, an administrator in
 * the third, and each can learn to ignore the other two.
 *
 * There is deliberately no top bar. The rail carries every piece of global
 * chrome — brand, navigation, feed state, account — which leaves each page the
 * entire remaining viewport and, more usefully, means a page's own header is
 * the only title on screen. A top bar naming the current page above a page
 * naming itself is the duplication this layout is avoiding.
 */

import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { BrandMark, Icon, type IconName } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useEventStream } from '@/hooks/useEventStream'
import { PERMISSIONS, ROLE_SUMMARY, type Permission } from '@/lib/permissions'

interface NavItem {
  to: string
  label: string
  icon: IconName
  /** The permission that makes a tab usable at all. */
  needs: Permission
}

interface NavGroup {
  label: string
  items: NavItem[]
}

/**
 * `needs` is what makes a tab appear. A role without it does not see the tab —
 * an auditor shown "Vehicle Search" only to be refused on arrival learns that
 * this system's errors are noise, which is an expensive thing to teach a
 * control room.
 */
const GROUPS: NavGroup[] = [
  {
    label: 'Operations',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: 'dashboard', needs: PERMISSIONS.cameraRead },
      { to: '/map', label: 'GIS Map', icon: 'map', needs: PERMISSIONS.cameraRead },
      { to: '/anpr', label: 'Live ANPR', icon: 'video', needs: PERMISSIONS.streamView },
      { to: '/alerts', label: 'Alerts', icon: 'alert', needs: PERMISSIONS.alertRead },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { to: '/traffic', label: 'Traffic', icon: 'chart', needs: PERMISSIONS.analyticsRead },
      { to: '/vehicles', label: 'Vehicle Search', icon: 'search', needs: PERMISSIONS.searchExecute },
      { to: '/watchlist', label: 'Watchlist', icon: 'bookmark', needs: PERMISSIONS.watchlistRead },
    ],
  },
  {
    label: 'Administration',
    items: [
      { to: '/health', label: 'Fleet Health', icon: 'pulse', needs: PERMISSIONS.cameraRead },
      { to: '/cameras', label: 'Cameras', icon: 'camera', needs: PERMISSIONS.cameraRead },
      { to: '/users', label: 'Accounts', icon: 'users', needs: PERMISSIONS.userRead },
      { to: '/audit', label: 'Audit', icon: 'ledger', needs: PERMISSIONS.auditRead },
    ],
  },
]

/** Every route in the rail, flattened — used to pick a landing screen. */
export const NAV_ITEMS: NavItem[] = GROUPS.flatMap((group) => group.items)

const COLLAPSE_KEY = 'contrail.nav.collapsed'

/**
 * Remembering the rail state is a convenience for one browser and nothing
 * more, so localStorage is right — but it throws in a private window and
 * returns nothing after a clear, and a nav that fails to render because a
 * preference could not be read would be a poor trade for remembering a width.
 */
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

export default function AppNav() {
  const { user, signOut, can } = useAuth()
  const [collapsed, setCollapsed] = useState(readCollapsed)

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
    } catch {
      /* the rail works either way; only the preference is lost */
    }
  }, [collapsed])

  return (
    <nav
      aria-label="Primary"
      className={`flex shrink-0 flex-col border-r border-border bg-card transition-[width] duration-200 ${
        collapsed ? 'w-[60px]' : 'w-56'
      }`}
    >
      {/* ── Brand ──────────────────────────────────────────────────── */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <BrandMark size={20} className="text-primary" />
        {!collapsed && (
          <span className="truncate text-sm font-semibold tracking-tight">Contrail</span>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className={`rounded p-1 text-muted-foreground transition hover:bg-secondary hover:text-foreground ${
            collapsed ? 'mx-auto mt-0' : 'ml-auto'
          }`}
        >
          <Icon name="chevronLeft" size={15} className={collapsed ? 'rotate-180' : ''} />
        </button>
      </div>

      {/* ── The screens ────────────────────────────────────────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto py-2">
        {GROUPS.map((group) => {
          const visible = group.items.filter((item) => can(item.needs))
          // A group whose every screen is out of this role's reach is not
          // rendered as an empty heading — an auditor should not be shown
          // "Operations" with nothing under it.
          if (visible.length === 0) return null
          return (
            <div key={group.label} className="mb-1 px-2 pb-1">
              {!collapsed && (
                <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/60">
                  {group.label}
                </p>
              )}
              {/* Collapsed, the groups still need separating or the eleven
                  icons read as one undifferentiated column. */}
              {collapsed && <div className="mx-2 mb-2 mt-2 border-t border-border/60" />}
              <ul className="space-y-px">
                {visible.map((item) => (
                  <li key={item.to}>
                    <NavItemLink item={item} collapsed={collapsed} />
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>

      <FeedStatus collapsed={collapsed} />
      <AccountBlock
        collapsed={collapsed}
        username={user?.username ?? ''}
        fullName={user?.full_name ?? null}
        role={user?.role ?? null}
        mustChangePassword={Boolean(user?.must_change_password)}
        onSignOut={() => void signOut()}
      />
    </nav>
  )
}

function NavItemLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  return (
    <NavLink
      to={item.to}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        `relative flex items-center gap-2.5 rounded-md py-1.5 text-sm transition ${
          collapsed ? 'justify-center px-0' : 'px-2'
        } ${
          isActive
            ? 'bg-secondary font-medium text-foreground'
            : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
        }`
      }
    >
      {({ isActive }) => (
        <>
          {/* The active marker is a rule against the rail edge rather than a
              background alone: at a glance down a column of eleven rows, a
              filled row and a hovered row look alike. */}
          {isActive && (
            <span
              aria-hidden
              className="absolute -left-2 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-primary"
            />
          )}
          <Icon name={item.icon} size={17} className={isActive ? 'text-primary' : ''} />
          {!collapsed && <span className="truncate">{item.label}</span>}
          {item.to === '/alerts' && <AlertCount collapsed={collapsed} />}
        </>
      )}
    </NavLink>
  )
}

/**
 * Unacknowledged alerts, on the navigation itself.
 *
 * An operator triaging one camera has to learn that another one just fired
 * without being on the alerts screen to see it.
 */
function AlertCount({ collapsed }: { collapsed: boolean }) {
  const { counts } = useEventStream()
  if (counts.alerts === 0) return null

  // Collapsed there is no room for the number, so it becomes a dot on the
  // icon — still says "something arrived", which is the part that matters.
  if (collapsed) {
    return (
      <span
        aria-label={`${counts.alerts} unacknowledged alerts`}
        className="absolute right-2 top-1.5 h-2 w-2 rounded-full bg-status-offline ring-2 ring-card"
      />
    )
  }
  return (
    <span className="ml-auto rounded-full bg-status-offline px-1.5 py-px text-[10px] font-bold leading-tight text-white">
      {counts.alerts}
    </span>
  )
}

/**
 * The live event feed's state, always on screen.
 *
 * Every counter in this product is historical and none of them stops looking
 * healthy when the pipeline dies. This is the one indicator that says whether
 * what you are looking at is still being updated, so it belongs in the global
 * chrome rather than on the one screen that happens to show it.
 */
function FeedStatus({ collapsed }: { collapsed: boolean }) {
  const { status } = useEventStream()
  const live = status === 'live'
  const title = live
    ? 'Connected to the live event feed'
    : 'Not connected — every figure on screen is historical and nothing new will appear'

  return (
    <div
      title={title}
      className={`flex shrink-0 items-center gap-2 border-t border-border px-3 py-2 text-[11px] ${
        collapsed ? 'justify-center' : ''
      }`}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          live ? 'animate-pulse-alert bg-status-online' : 'bg-priority-high'
        }`}
      />
      {!collapsed && (
        <span className={live ? 'text-status-online' : 'text-priority-high'}>
          event feed {status}
        </span>
      )}
    </div>
  )
}

function AccountBlock({
  collapsed,
  username,
  fullName,
  role,
  mustChangePassword,
  onSignOut,
}: {
  collapsed: boolean
  username: string
  fullName: string | null
  role: string | null
  mustChangePassword: boolean
  onSignOut: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // A menu that only closes by re-clicking its own trigger is a menu that gets
  // left open behind whatever the operator does next.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onPointerDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onPointerDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative shrink-0 border-t border-border p-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={collapsed ? `${username} · ${role ?? ''}` : undefined}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition hover:bg-secondary/60 ${
          collapsed ? 'justify-center px-0' : ''
        }`}
      >
        <span
          aria-hidden
          className="relative grid h-6 w-6 shrink-0 place-items-center rounded-full bg-secondary text-[11px] font-semibold uppercase"
        >
          {username.slice(0, 2)}
          {/* A password an administrator set is a shared secret, and nothing
              this account does is attributable until it is replaced. That is
              worth a persistent mark rather than a one-off notice. */}
          {mustChangePassword && (
            <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-priority-high ring-2 ring-card" />
          )}
        </span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs leading-tight">
                {fullName || username}
              </span>
              <span className="block truncate text-[10px] uppercase tracking-wide text-muted-foreground">
                {role}
              </span>
            </span>
            <Icon name="chevronDown" size={13} className="text-muted-foreground" />
          </>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-[calc(100%+4px)] left-2 right-2 z-40 overflow-hidden rounded-md border border-border bg-popover shadow-xl"
        >
          {role && (
            <p className="border-b border-border px-3 py-2 text-[11px] leading-snug text-muted-foreground">
              {ROLE_SUMMARY[role]}
            </p>
          )}
          <NavLink
            to="/password"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-3 py-2 text-xs transition hover:bg-secondary"
          >
            <Icon name="key" size={14} />
            {mustChangePassword ? 'Set your password' : 'Change password'}
            {mustChangePassword && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-priority-high" />
            )}
          </NavLink>
          <button
            type="button"
            role="menuitem"
            onClick={onSignOut}
            className="flex w-full items-center gap-2 border-t border-border px-3 py-2 text-xs transition hover:bg-secondary"
          >
            <Icon name="logout" size={14} />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
