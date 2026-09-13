/** Command centre shell: navigation, session, and routing. */

import { Navigate, Route, Routes } from 'react-router-dom'

import AppNav, { NAV_ITEMS } from '@/components/AppNav'
import { ToastProvider } from '@/components/Toast'
import { Spinner } from '@/components/ui'
import Alerts from '@/pages/Alerts'
import TrafficIntelligence from '@/pages/TrafficIntelligence'
import AuditLog from '@/pages/AuditLog'
import ChangePassword from '@/pages/ChangePassword'
import Dashboard from '@/pages/Dashboard'
import FleetHealthPage from '@/pages/FleetHealth'
import Cameras from '@/pages/Cameras'
import LiveAnpr from '@/pages/LiveAnpr'
import Login from '@/pages/Login'
import MapView from '@/pages/MapView'
import Users from '@/pages/Users'
import VehicleSearch from '@/pages/VehicleSearch'
import Watchlist from '@/pages/Watchlist'
import RequirePermission from '@/components/RequirePermission'
import { useAuth } from '@/hooks/useAuth'
import { EventStreamProvider } from '@/hooks/useEventStream'
import { PERMISSIONS } from '@/lib/permissions'

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

  // The provider sits above the shell because the navigation itself shows a
  // live alert count and the feed's own connection state. One socket serves
  // every screen inside it.
  return (
    <EventStreamProvider>
      <ToastProvider>
        <Shell />
      </ToastProvider>
    </EventStreamProvider>
  )
}

function Shell() {
  const { can } = useAuth()
  // Where to send someone whose role cannot open the default screen.
  const landing = NAV_ITEMS.find((item) => can(item.needs))?.to ?? '/no-access'

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <AppNav />

      {/* min-w-0 so a wide table or a map inside a page cannot push the rail
          off the left edge — a flex child defaults to min-content width. */}
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Routes>
          <Route path="/" element={<Navigate to={landing} replace />} />
          <Route
            path="/dashboard"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="The dashboard">
                <Dashboard />
              </RequirePermission>
            }
          />
          <Route
            path="/map"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="The GIS map">
                <MapView />
              </RequirePermission>
            }
          />
          <Route
            path="/traffic"
            element={
              <RequirePermission anyOf={[PERMISSIONS.analyticsRead]} label="Traffic intelligence">
                <TrafficIntelligence />
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
            path="/cameras"
            element={
              <RequirePermission anyOf={[PERMISSIONS.cameraRead]} label="Cameras">
                <Cameras />
              </RequirePermission>
            }
          />
          {/* The screen was called Integration until it grew the ability to
              actually onboard something. Old links should still land. */}
          <Route path="/integration" element={<Navigate to="/cameras" replace />} />
          {/* Analytics absorbed the traffic feature rather than sitting beside
              it — the two described the same roads. Same reasoning. */}
          <Route path="/analytics" element={<Navigate to="/traffic" replace />} />
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
      </main>
    </div>
  )
}
