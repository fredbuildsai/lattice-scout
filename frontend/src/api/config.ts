/** Base URL of the backend API (no trailing slash). */
export const API_URL: string = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

/** Analytics can be switched off with `VITE_ANALYTICS_ENABLED=false`. */
export const ANALYTICS_ENABLED: boolean = import.meta.env.VITE_ANALYTICS_ENABLED !== 'false'
