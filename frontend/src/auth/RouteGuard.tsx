import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useMe } from '../features/profile/api'
import { useAuth } from './AuthProvider'
import { guardRedirect } from './guard'

/** Redirects by auth state: signed out -> /signin, no researcher -> /onboarding. */
export function RouteGuard() {
  const { ready, session } = useAuth()
  const { pathname } = useLocation()
  const me = useMe(ready && !!session)

  if (!ready) return <Loading />
  if (session && me.isPending) return <Loading />
  if (session && me.isError && !(me.error instanceof ApiError && me.error.status === 401)) {
    return (
      <div role="alert" className="p-8 text-ink">
        <p>We could not load your account.</p>
        <button type="button" onClick={() => void me.refetch()} className="mt-3 rounded-btn border border-line-strong px-4 py-2">
          Try again
        </button>
      </div>
    )
  }
  const to = guardRedirect({ authenticated: !!session, hasResearcher: !!me.data?.researcher, pathname })
  if (to) return <Navigate to={to} replace />
  return <Outlet />
}

function Loading() {
  return (
    <p role="status" className="p-8 text-ink-3">
      Loading…
    </p>
  )
}
