/** Command centre shell: navigation, session, and routing. */

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import FleetHealthPage from '@/pages/FleetHealth'
import Integration from '@/pages/Integration'
import Login from '@/pages/Login'
import MapView from '@/pages/MapView'
import { useAuth } from '@/hooks/useAuth'

/** Gujarati alongside English on primary navigation. */
const NAV = [
  { to: '/map', label: 'GIS Map', gu: 'નકશો' },
  { to: '/health', label: 'Fleet Health', gu: 'આરોગ્ય' },
  { to: '/integration', label: 'Integration', gu: 'એકીકરણ' },
]

export default function App() {
  const { user, loading, signOut } = useAuth()

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
              <span className="ml-1.5 hidden text-xs opacity-60 lg:inline">
                {item.gu}
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <p className="text-sm leading-tight">{user.username}</p>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {user.role}
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
          <Route path="/health" element={<FleetHealthPage />} />
          <Route path="/integration" element={<Integration />} />
          <Route path="*" element={<Navigate to="/map" replace />} />
        </Routes>
      </div>
    </div>
  )
}
