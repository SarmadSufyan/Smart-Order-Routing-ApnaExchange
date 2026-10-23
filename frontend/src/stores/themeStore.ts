import { create } from 'zustand'

export type ThemeMode = 'dark' | 'light'

/** The look: Studio (the design system, default), the team's Liquid Glass, or the original Terminal. */
export type ThemeStyle = 'studio' | 'glass' | 'terminal'

export const THEME_STYLES: { id: ThemeStyle; name: string; by: string }[] = [
  { id: 'studio',   name: 'Studio',       by: 'ApnaSOR design system' },
  { id: 'glass',    name: 'Liquid Glass', by: 'Mahnoor & Maham' },
  { id: 'terminal', name: 'Terminal',     by: 'the original look' },
]

const MODE_KEY = 'poc.theme'
const STYLE_KEY = 'poc.style'

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    if (v && (allowed as readonly string[]).includes(v)) return v as T
  } catch {}
  return fallback
}

function applyToDocument(mode: ThemeMode, style: ThemeStyle) {
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = mode
    document.documentElement.dataset.style = style
  }
}

function store(key: string, value: string) {
  try { localStorage.setItem(key, value) } catch {}
}

interface ThemeState {
  mode: ThemeMode
  style: ThemeStyle
  setMode: (m: ThemeMode) => void
  toggleMode: () => void
  setStyle: (s: ThemeStyle) => void
}

const initialMode = readStored<ThemeMode>(MODE_KEY, ['dark', 'light'], 'dark')
const initialStyle = readStored<ThemeStyle>(STYLE_KEY, ['studio', 'glass', 'terminal'], 'studio')
applyToDocument(initialMode, initialStyle)

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: initialMode,
  style: initialStyle,
  setMode: (mode) => {
    applyToDocument(mode, get().style)
    store(MODE_KEY, mode)
    set({ mode })
  },
  toggleMode: () => get().setMode(get().mode === 'dark' ? 'light' : 'dark'),
  setStyle: (style) => {
    applyToDocument(get().mode, style)
    store(STYLE_KEY, style)
    set({ style })
  },
}))
