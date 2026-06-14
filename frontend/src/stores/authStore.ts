import { create } from 'zustand'
import { api, setToken, getToken, type User } from '../services/api'
import { wsClient } from '../services/websocket'

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  error: string | null
  loading: boolean
  login: (username: string, password: string) => Promise<boolean>
  logout: () => void
  restoreSession: () => Promise<void>
}

// Synchronously read session storage at module load — avoids the auth-guard race.
function initialAuthState(): { user: User | null; isAuthenticated: boolean } {
  try {
    const token = getToken()
    const stored = sessionStorage.getItem('poc.user')
    if (token && stored) {
      const user = JSON.parse(stored) as User
      wsClient.connect(token)
      return { user, isAuthenticated: true }
    }
  } catch {
    // ignore
  }
  return { user: null, isAuthenticated: false }
}

const initial = initialAuthState()

export const useAuthStore = create<AuthState>((set) => ({
  user: initial.user,
  isAuthenticated: initial.isAuthenticated,
  error: null,
  loading: false,

  login: async (username, password) => {
    set({ loading: true, error: null })
    try {
      const res = await api.login(username, password)
      setToken(res.access_token)
      sessionStorage.setItem('poc.authed', '1')
      sessionStorage.setItem('poc.user', JSON.stringify(res.user))
      wsClient.connect(res.access_token)
      set({ user: res.user, isAuthenticated: true, loading: false })
      return true
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
      return false
    }
  },

  logout: () => {
    setToken(null)
    sessionStorage.removeItem('poc.authed')
    sessionStorage.removeItem('poc.user')
    wsClient.disconnect()
    set({ user: null, isAuthenticated: false, error: null })
  },

  restoreSession: async () => {
    const token = getToken()
    const stored = sessionStorage.getItem('poc.user')
    if (token && stored) {
      try {
        const user = JSON.parse(stored) as User
        wsClient.connect(token)
        set({ user, isAuthenticated: true })
      } catch {
        sessionStorage.removeItem('poc.authed')
        sessionStorage.removeItem('poc.user')
      }
    }
  },
}))
