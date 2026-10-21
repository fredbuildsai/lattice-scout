import type { Config } from 'tailwindcss'

/** Design tokens from docs/screens/README.md. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#F3F4F1',
        surface: '#FFFFFF',
        ink: { DEFAULT: '#14171A', 2: '#3A4047', 3: '#555C63' },
        line: { DEFAULT: '#D9DCD6', strong: '#C4C8C1' },
        subtle: { DEFAULT: '#F1F2EF', 2: '#ECEEE9' },
        accent: { DEFAULT: '#2446C7', dark: '#16308F', 50: '#E8ECFA' },
        scout: { DEFAULT: '#5B3FB5', 50: '#EEE9F8', 100: '#F4F1FB', ink: '#4A2F9A' },
        teal: { DEFAULT: '#0F6E6E', dark: '#0F5E5E', 50: '#E3F1EF' },
        warm: { DEFAULT: '#7A3A06', mid: '#B4570F', 50: '#FBF0E4' },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
        serif: ['"Source Serif 4"', 'Georgia', 'serif'],
      },
      borderRadius: { card: '12px', btn: '6px' },
    },
  },
  plugins: [],
} satisfies Config
