/** Command centre shell: navigation, session, and routing.
 *
 * v1 demo build: trimmed to the two screens shown to judges — the GIS map
 * and Live ANPR. The rest of the platform (analytics, alerts, search,
 * watchlist, fleet health, admin) still exists on `main`; this branch is
 * deliberately narrow so the demo has nothing extra to click into.
 */

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { ToastProvider } from '@/components/Toast'
import { Button, Spinner } from '@/components/ui'
import ChangePassword from '@/pages/ChangePassword'
import LiveAnpr from '@/pages/LiveAnpr'
import Login from '@/pages/Login'
import MapView from '@/pages/MapView'
import RequirePermission from '@/components/RequirePermission'
import { useAuth } from '@/hooks/useAuth'
import { EventStreamProvider } from '@/hooks/useEventStream'
import { PERMISSIONS, ROLE_SUMMARY, type Permission } from '@/lib/permissions'

interface NavItem {
  to: string
  label: string
  needs: Permission
}

const NAV: NavItem[] = [
  { to: '/map', label: 'GIS Map', needs: PERMISSIONS.cameraRead },
  { to: '/anpr', label: 'Live ANPR', needs: PERMISSIONS.streamView },
]

export default function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Spinner />
      </div>
    )
  }

  if (!user) {
    return <Login />
  }

  // One event-stream socket serves both screens inside it (live ANPR readings
  // and the map's camera status).
  return (
    <EventStreamProvider>
      <ToastProvider>
        <Shell />
      </ToastProvider>
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
            Contrail
          </span>
        </div>

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

          <Button variant="outline" onClick={() => void signOut()}>
            Sign out
          </Button>
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
          {/* Every signed-in account can change its own password; there is no
              permission for it because needing one would be circular. */}
          <Route path="/password" element={<ChangePassword />} />
          <Route path="*" element={<Navigate to={landing} replace />} />
        </Routes>
      </div>
    </div>
  )
}
