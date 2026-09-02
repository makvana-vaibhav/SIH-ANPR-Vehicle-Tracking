/** Command centre shell: navigation, session, and routing. */

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import Alerts from '@/pages/Alerts'
import AuditLog from '@/pages/AuditLog'
import ChangePassword from '@/pages/ChangePassword'
import FleetHealthPage from '@/pages/FleetHealth'
import Integration from '@/pages/Integration'
import LiveAnpr from '@/pages/LiveAnpr'
import Login from '@/pages/Login'
import MapView from '@/pages/MapView'
import Users from '@/pages/Users'
import VehicleSearch from '@/pages/VehicleSearch'
import Watchlist from '@/pages/Watchlist'
import RequirePermission from '@/components/RequirePermission'
import { useAuth } from '@/hooks/useAuth'
import { EventStreamProvider, useEventStream } from '@/hooks/useEventStream'
import { PERMISSIONS, ROLE_SUMMARY, type Permission } from '@/lib/permissions'

/**
 * Primary navigation, Gujarati alongside English.
 *
 * `needs` is the permission that makes a tab usable at all. A role without it
 * does not see the tab — an auditor shown "Vehicle Search" only to be refused
 * on arrival learns that this system's errors are noise, which is an expensive
 * thing to teach a control room.
 */
interface NavItem {
  to: string
  label: string
  gu: string
  needs: Permission
}

const NAV: NavItem[] = [
  { to: '/map', label: 'GIS Map', gu: 'નકશો', needs: PERMISSIONS.cameraRead },
  { to: '/anpr', label: 'Live ANPR', gu: 'લાઇવ ANPR', needs: PERMISSIONS.streamView },
  { to: '/alerts', label: 'Alerts', gu: 'ચેતવણી', needs: PERMISSIONS.alertRead },
  {
    to: '/vehicles',
    label: 'Vehicle Search',
    gu: 'વાહન શોધ',
    needs: PERMISSIONS.searchExecute,
  },
  {
    to: '/watchlist',
    label: 'Watchlist',
    gu: 'વોચલિસ્ટ',
    needs: PERMISSIONS.watchlistRead,
  },
  { to: '/health', label: 'Fleet Health', gu: 'આરોગ્ય', needs: PERMISSIONS.cameraRead },
  {
    to: '/integration',
    label: 'Integration',
    gu: 'એકીકરણ',
    needs: PERMISSIONS.cameraRead,
  },
  { to: '/users', label: 'Accounts', gu: 'ખાતાં', needs: PERMISSIONS.userRead },
  { to: '/audit', label: 'Audit', gu: 'ઓડિટ', needs: PERMISSIONS.auditRead },
]

/**
 * Unacknowledged alerts, on the navigation itself.
 *
 * An operator triaging one camera has to learn that another one just fired
 * without being on the alerts screen to see it.
 */
function AlertBadge() {
  const { counts } = useEventStream()
  if (counts.alerts === 0) return null
  return (
    <span className="ml-1.5 rounded-full bg-status-offline px-1.5 text-[10px] font-bold text-white">
      {counts.alerts}
    </span>
  )
}

export default function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (!user) {
    return <Login />
  }

  // The provider sits above the header because the navigation itself shows a
  // live alert count. One socket serves every screen inside it.
  return (
    <EventStreamProvider>
      <Shell />
    </EventStreamProvider>
  )
}

function Shell() {
  const { user, signOut, can } = useAuth()
  // Where to send someone whose role cannot open the default screen.
  const landing = NAV.find((item) => can(item.needs))?.to ?? '/no-access'

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex shrink-0 items-center gap-4 border-b border-border bg-card px-4 py-2.5">
        <div className="flex shrink-0 items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight text-primary">
            Sentinel<span className="text-foreground">-GJ</span>
          </span>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            સેન્ટિનલ
          </span>
        </div>

        {/* min-w-0 lets the nav shrink rather than push the identity block
            off the header; overflow-x-auto keeps every tab reachable on a
            narrow control-room monitor instead of hiding some. */}
        <nav className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {NAV.filter((item) => can(item.needs)).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm transition ${
                  isActive
                    ? 'bg-secondary text-foreground'
                    : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
                }`
              }
            >
              {item.label}
              {item.to === '/alerts' && <AlertBadge />}
              <span className="ml-1.5 hidden text-xs opacity-60 2xl:inline">
                {item.gu}
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-3">
          <div className="text-right">
            <p className="text-sm leading-tight">{user?.username}</p>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              <span title={user?.role ? ROLE_SUMMARY[user.role] : undefined}>
                {user?.role}
              </span>
            </p>
          </div>
          <NavLink
            to="/password"
            title="Change your password"
            className="rounded-md px-2 py-1.5 text-xs text-muted-foreground transition hover:bg-secondary/50 hover:text-foreground"
          >
            {user?.must_change_password ? '⚠ Set your password' : 'Password'}
          </NavLink>

          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<Navigate to={landing} replace />} />
          <Route
            path="/map"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="The GIS map">
                <MapView />
              </RequirePermission>
            }
          />
          <Route
            path="/anpr"
            element={
              <RequirePermission anyOf={[PERMISSIONS.streamView]} label="Live ANPR">
                <LiveAnpr />
              </RequirePermission>
            }
          />
          <Route
            path="/alerts"
            element={
              <RequirePermission anyOf={[PERMISSIONS.alertRead]} label="Alerts">
                <Alerts />
              </RequirePermission>
            }
          />
          <Route
            path="/vehicles"
            element={
              <RequirePermission
                anyOf={[PERMISSIONS.searchExecute]}
                label="Vehicle search"
              >
                <VehicleSearch />
              </RequirePermission>
            }
          />
          <Route
            path="/watchlist"
            element={
              <RequirePermission
                anyOf={[PERMISSIONS.watchlistRead]}
                label="The watchlist"
              >
                <Watchlist />
              </RequirePermission>
            }
          />
          <Route
            path="/health"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="Fleet health">
                <FleetHealthPage />
              </RequirePermission>
            }
          />
          <Route
            path="/integration"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="Integration">
                <Integration />
              </RequirePermission>
            }
          />
          <Route
            path="/users"
            element={
              <RequirePermission anyOf={[PERMISSIONS.userRead]} label="Account administration">
                <Users />
              </RequirePermission>
            }
          />
          <Route
            path="/audit"
            element={
              <RequirePermission anyOf={[PERMISSIONS.auditRead]} label="The audit trail">
                <AuditLog />
              </RequirePermission>
            }
          />
          {/* Every signed-in account can change its own password; there is no
              permission for it because needing one would be circular. */}
          <Route path="/password" element={<ChangePassword />} />
          <Route path="*" element={<Navigate to={landing} replace />} />
        </Routes>
      </div>
    </div>
  )
}
