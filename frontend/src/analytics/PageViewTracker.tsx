import { useEffect } from 'react'
import { matchRoutes, useLocation } from 'react-router-dom'
import { ROUTES } from '../routes'
import { analytics } from './client'

/** Maps a pathname to its route pattern (never raw ids). Unknown paths map to `*`. */
export function routePattern(pathname: string): string {
  const m = matchRoutes(ROUTES.map((r) => ({ path: r.path })), pathname)
  const p = m?.[0]?.route.path
  return p ?? '*'
}

let lastKey: string | null = null

/** Router listener: emits `page_view` on every navigation (once per location key). */
export function PageViewTracker() {
  const { pathname, key } = useLocation()
  useEffect(() => {
    if (lastKey === key) return // StrictMode double effect
    lastKey = key
    analytics.pageView(routePattern(pathname))
  }, [pathname, key])
  return null
}
