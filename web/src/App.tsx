/** Command centre shell: navigation, session, and routing. */

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import Alerts from '@/pages/Alerts'
import FleetHealthPage from '@/pages/FleetHealth'
import Integration from '@/pages/Integration'
import LiveAnpr from '@/pages/LiveAnpr'
import Login from '@/pages/Login'
import MapView from '@/pages/MapView'
import VehicleSearch from '@/pages/VehicleSearch'
import Watchlist from '@/pages/Watchlist'
import { useAuth } from '@/hooks/useAuth'
import { EventStreamProvider, useEventStream } from '@/hooks/useEventStream'

/** Gujarati alongside English on primary navigation. */
const NAV = [
  { to: '/map', label: 'GIS Map', gu: 'નકશો' },
  { to: '/anpr', label: 'Live ANPR', gu: 'લાઇવ ANPR' },
  { to: '/alerts', label: 'Alerts', gu: 'ચેતવણી' },
  { to: '/vehicles', label: 'Vehicle Search', gu: 'વાહન શોધ' },
  { to: '/watchlist', label: 'Watchlist', gu: 'વોચલિસ્ટ' },
  { to: '/health', label: 'Fleet Health', gu: 'આરોગ્ય' },
  { to: '/integration', label: 'Integration', gu: 'એકીકરણ' },
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
  const { user, signOut } = useAuth()

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex shrink-0 items-center gap-6 border-b border-border bg-card px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight text-primary">
            Sentinel<span className="text-foreground">-GJ</span>
          </span>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            સેન્ટિનલ
          </span>
        </div>

        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-sm transition ${
                  isActive
                    ? 'bg-secondary text-foreground'
                    : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
                }`
              }
            >
              {item.label}
              {item.to === '/alerts' && <AlertBadge />}
              <span className="ml-1.5 hidden text-xs opacity-60 lg:inline">
                {item.gu}
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <p className="text-sm leading-tight">{user?.username}</p>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {user?.role}
            </p>
          </div>
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
          <Route path="/" element={<Navigate to="/map" replace />} />
          <Route path="/map" element={<MapView />} />
          <Route path="/anpr" element={<LiveAnpr />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/vehicles" element={<VehicleSearch />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/health" element={<FleetHealthPage />} />
          <Route path="/integration" element={<Integration />} />
          <Route path="*" element={<Navigate to="/map" replace />} />
        </Routes>
      </div>
    </div>
  )
}
