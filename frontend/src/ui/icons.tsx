import type { ReactNode } from 'react'

/** Icon paths from the NavMenu design. */
export const ICON_PATHS = {
  home: 'M3 11l9-7 9 7v9h-6v-6H9v6H3z',
  vault: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
  idea: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z',
  people: 'M7 11a3 3 0 1 0 0-.1M17 11a3 3 0 1 0 0-.1M2 21c0-3 2-5 5-5s5 2 5 5M12 21c0-3 2-5 5-5s5 2 5 5',
  user: 'M12 12a4 4 0 1 0 0-8a4 4 0 1 0 0 8M4 21c0-4 4-6 8-6s8 2 8 6',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
  redo: 'M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
} as const

export type IconName = keyof typeof ICON_PATHS

/** Decorative stroke icon. */
export function Icon({ name, size = 18 }: { name: IconName; size?: number }): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  )
}

/** Lattice Scout logo mark. */
export function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" stroke="#2446C7" strokeWidth="2" aria-hidden="true">
      <rect x="3" y="3" width="22" height="22" />
      <path d="M3 14h22M14 3v22" />
      <circle cx="14" cy="14" r="3" fill="#5B3FB5" stroke="#5B3FB5" />
    </svg>
  )
}
