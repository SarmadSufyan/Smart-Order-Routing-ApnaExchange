import { create } from 'zustand'

export type ThemeMode = 'dark' | 'light'

const STORAGE_KEY = 'poc.theme'

function readStoredMode(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {}
  return 'dark'
}

function applyToDocument(mode: ThemeMode) {
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = mode
  }
}

interface ThemeState {
  mode: ThemeMode
  setMode: (m: ThemeMode) => void
  toggleMode: () => void
}

const initialMode = readStoredMode()
applyToDocument(initialMode)

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: initialMode,
  setMode: (mode) => {
    applyToDocument(mode)
    try { localStorage.setItem(STORAGE_KEY, mode) } catch {}
    set({ mode })
  },
  toggleMode: () => {
    const next: ThemeMode = get().mode === 'dark' ? 'light' : 'dark'
    get().setMode(next)
  },
}))
