import React, { useEffect, useState } from 'react'
import { ThinkingOrb } from 'thinking-orbs'
import type { OrbSize, OrbState } from 'thinking-orbs'
import { C, F } from '../theme'

// Thinking orbs (thinking-orbs, MIT, Jakub Antalik): dotted, monochrome canvas animations for "the system is
// working". The ink follows the page's dark / light mode by itself (it reads `data-theme` on <html>), pauses
// offscreen and in hidden tabs, and shows a still frame to anyone who asked their system for reduced motion.
//
// One verb per kind of work, so the same motion always means the same thing:
//   searching  — reading data (order books, research, the audit log)
//   solving    — deciding (risk checks, the algorithm choice, the analyst)
//   connecting — reaching other machines (exchanges, the RAFT cluster, the gateway)
//   composing  — writing an answer (the copilot's language model)
//   working    — anything else that runs
//   breathing  — idle but alive (the copilot waiting for a question)

export type { OrbState }

/** A thinking orb that fades in when it appears. */
export function Orb({ state = 'working', size = 20, speed, paused, label, style }: {
  state?: OrbState
  size?: OrbSize
  speed?: number
  paused?: boolean
  label?: string
  style?: React.CSSProperties
}) {
  return (
    <ThinkingOrb
      state={state} size={size} speed={speed} paused={paused} aria-label={label}
      style={{ flexShrink: 0, animation: 'orbIn .35s ease both', ...style }}
    />
  )
}

/**
 * "Something is loading": an orb and a short line. Inline by default (a row inside text); `block` centres a
 * larger orb in the space a card or table would fill.
 */
export function Loading({ label = 'Loading…', state = 'searching', block = false }: {
  label?: React.ReactNode
  state?: OrbState
  block?: boolean
}) {
  if (block) {
    return (
      <div role="status" style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12,
        padding: '28px 12px', color: C.dim, fontSize: F.sm, textAlign: 'center',
      }}>
        <Orb state={state} size={32} />
        <span>{label}</span>
      </div>
    )
  }
  return (
    <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: C.dim, fontSize: F.sm }}>
      <Orb state={state} size={20} />
      <span>{label}</span>
    </span>
  )
}

/** Steps through `states`, one every `ms`, while it is mounted. */
export function useOrbCycle(states: OrbState[], ms = 2400): OrbState {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (states.length < 2) return
    const t = setInterval(() => setI((n) => (n + 1) % states.length), ms)
    return () => clearInterval(t)
  }, [states.length, ms])
  return states[i % states.length]
}

/** The copilot's "thinking" line: the orb changes its motion as the wait goes on, the text shimmers softly. */
export function Thinking({ label, states = ['searching', 'solving', 'composing'] }: {
  label: string
  states?: OrbState[]
}) {
  const state = useOrbCycle(states)
  return (
    <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <Orb key={state} state={state} size={20} />
      <span style={{
        fontSize: F.sm, fontWeight: 500,
        background: `linear-gradient(90deg, ${C.dim} 0%, ${C.text} 50%, ${C.dim} 100%)`, backgroundSize: '200% 100%',
        WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent',
        animation: 'orbShimmer 2.4s linear infinite',
      }}>
        {label}
      </span>
    </div>
  )
}
