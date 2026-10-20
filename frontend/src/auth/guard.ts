export interface GuardInput {
  authenticated: boolean
  hasResearcher: boolean
  pathname: string
}

/** Where to send the visitor, or null to render the requested page. */
export function guardRedirect({ authenticated, hasResearcher, pathname }: GuardInput): string | null {
  if (!authenticated) return pathname === '/signin' ? null : '/signin'
  if (pathname === '/signin') return hasResearcher ? '/' : '/onboarding'
  if (!hasResearcher && pathname !== '/onboarding') return '/onboarding'
  return null
}
