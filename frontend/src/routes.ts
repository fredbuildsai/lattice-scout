/** Route table (patterns are what analytics reports as `page`). */
export interface RouteDef {
  path: string
  title: string
}

export const ROUTES: RouteDef[] = [
  { path: '/', title: 'Home' },
  { path: '/vault', title: 'Private vault' },
  { path: '/runs/:id', title: 'Analysis run' },
  { path: '/opportunities', title: 'Opportunities' },
  { path: '/opportunities/:id', title: 'Opportunity' },
  { path: '/matches', title: 'Matches' },
  { path: '/signin', title: 'Sign in' },
  { path: '/onboarding', title: 'Claim your profile' },
  { path: '/settings/privacy', title: 'Profile and privacy' },
]
