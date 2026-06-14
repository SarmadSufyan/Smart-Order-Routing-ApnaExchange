/**
 * Theme tokens — CSS variable references.
 *
 * Components keep using `C.bg`, `C.text`, etc., but the values are CSS
 * variables. Swapping `<html data-theme="light">` flips the whole UI without
 * any React re-renders — variables resolve at paint time.
 *
 * Concrete hex values live in `themeStyles.ts` (injected into <head>).
 */

export const C = {
  bg:       'var(--c-bg)',
  surface:  'var(--c-surface)',
  surface2: 'var(--c-surface2)',
  border:   'var(--c-border)',
  border2:  'var(--c-border2)',
  text:     'var(--c-text)',
  muted:    'var(--c-muted)',
  dim:      'var(--c-dim)',
  accent:   'var(--c-accent)',
  blue:     'var(--c-blue)',
  green:    'var(--c-green)',
  red:      'var(--c-red)',
  orange:   'var(--c-orange)',
  purple:   'var(--c-purple)',
  gold:     'var(--c-gold)',
} as const

/** Font sizes — second bump for projector readability. */
export const F = {
  xs:   '12px',  // tiny labels
  sm:   '13px',  // chrome / chips
  base: '14px',  // body / table cells
  md:   '15px',  // emphasized body
  lg:   '17px',  // section headers
  xl:   '20px',  // page titles
  xxl:  '26px',  // big stat numbers
} as const

/**
 * Mix a color with transparent at `pct` percent.
 * Use this instead of `${C.green}15` hex-alpha tricks, which don't work
 * with CSS variables.
 *
 *   background: tint(C.green, 15)   // 15% green over current background
 */
export function tint(color: string, pct: number): string {
  return `color-mix(in srgb, ${color} ${pct}%, transparent)`
}

/**
 * Resolve a token string (which may be `var(--c-blue)`) to the actual
 * color value at the current theme. Use this for `<canvas>` strokeStyle
 * and fillStyle, which can't parse CSS variables.
 *
 *   ctx.strokeStyle = resolveColor(C.blue)
 */
export function resolveColor(token: string): string {
  if (typeof document === 'undefined') return token
  if (!token.startsWith('var(')) return token
  const name = token.slice(4, -1).trim()  // 'var(--c-blue)' → '--c-blue'
  const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return val || '#000'
}
