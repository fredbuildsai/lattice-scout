import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { PageViewTracker } from './analytics/PageViewTracker'
import { AuthProvider } from './auth/AuthProvider'
import { RouteGuard } from './auth/RouteGuard'
import type { AuthClient } from './auth/AuthClient'
import { SignInPage } from './features/auth/SignInPage'
import { EmptyPage } from './features/pages/EmptyPage'
import { HomePage } from './features/profile/HomePage'
import { OnboardingPage } from './features/profile/OnboardingPage'
import { PrivacyPage } from './features/profile/PrivacyPage'
import { OpportunitiesPage } from './features/opportunities/OpportunitiesPage'
import { OpportunityDetailPage } from './features/opportunities/OpportunityDetailPage'
import { MatchesPage } from './features/matches/MatchesPage'
import { RunPage } from './features/runs/RunPage'
import { VaultPage } from './features/vault/VaultPage'
import { Layout } from './Layout'
import { ROUTES } from './routes'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

const IMPLEMENTED: Record<string, JSX.Element> = {
  '/': <HomePage />,
  '/vault': <VaultPage />,
  '/runs/:id': <RunPage />,
  '/opportunities': <OpportunitiesPage />,
  '/opportunities/:id': <OpportunityDetailPage />,
  '/matches': <MatchesPage />,
  '/signin': <SignInPage />,
  '/onboarding': <OnboardingPage />,
  '/settings/privacy': <PrivacyPage />,
}

/** Root component. Must be rendered inside a Router. `authClient` and the query `client` are injectable for tests. */
export function App({ authClient, client = queryClient }: { authClient?: AuthClient; client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <AuthProvider client={authClient}>
        <PageViewTracker />
        <Routes>
          <Route element={<RouteGuard />}>
            <Route path="/signin" element={IMPLEMENTED['/signin']} />
            <Route element={<Layout />}>
              {ROUTES.filter((r) => r.path !== '/signin').map((r) => (
                <Route key={r.path} path={r.path} element={IMPLEMENTED[r.path] ?? <EmptyPage title={r.title} />} />
              ))}
              <Route path="*" element={<EmptyPage title="Page not found" />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </QueryClientProvider>
  )
}
