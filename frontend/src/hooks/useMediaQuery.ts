import { useEffect, useState } from 'react'

/**
 * Screen-width breakpoints shared by the app shell and the pages, so every
 * layout switches at the same widths. Inline styles can't hold @media rules,
 * so components read these through useMediaQuery instead.
 */
export const MQ = {
  phone:   '(max-width: 599px)',   // labels dropped from the top bar
  narrow:  '(max-width: 899px)',   // sidebar becomes a slide-in menu
  compact: '(max-width: 1099px)',  // top bar hides the clock
} as const

/** True while the media query matches; follows window resizes and rotation. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()  // the query may have changed since the first render
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/**
 * gridTemplateColumns for a row of summary tiles: all in one row on wide
 * screens, two per row on medium ones, stacked on phones (never 3 + 1).
 */
export function useTileColumns(count: number): string {
  const isPhone   = useMediaQuery(MQ.phone)
  const isCompact = useMediaQuery(MQ.compact)
  const n = isPhone ? 1 : isCompact ? Math.min(2, count) : count
  return `repeat(${n}, minmax(0, 1fr))`
}
