/** Session state for the command centre. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import * as api from '@/lib/api'
import type { UserProfile } from '@/lib/types'

interface AuthState {
  user: UserProfile | null
  loading: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  // Restore an existing session on load, so a refresh does not sign the
  // operator out mid-incident.
  useEffect(() => {
    let cancelled = false
    async function restore() {
      if (!api.isAuthenticated()) {
        setLoading(false)
        return
      }
      try {
        const profile = await api.getProfile()
        if (!cancelled) setUser(profile)
      } catch {
        api.setTokens(null, null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void restore()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    api.setSessionExpiredHandler(() => setUser(null))
  }, [])

  const signIn = useCallback(async (username: string, password: string) => {
    setUser(await api.login(username, password))
  }, [])

  const signOut = useCallback(async () => {
    await api.logout()
    setUser(null)
  }, [])

  /** Whether the signed-in role holds a permission, for hiding actions. */
  const can = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  )

  const value = useMemo(
    () => ({ user, loading, signIn, signOut, can }),
    [user, loading, signIn, signOut, can],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside an AuthProvider')
  }
  return context
}
