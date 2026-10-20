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
import { Layout } from './Layout'
import { ROUTES } from './routes'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

const IMPLEMENTED: Record<string, JSX.Element> = {
  '/': <HomePage />,
  '/signin': <SignInPage />,
  '/onboarding': <OnboardingPage />,
  '/settings/privacy': <PrivacyPage />,
}

/** Root component. Must be rendered inside a Router. `authClient` is injectable for tests. */
export function App({ authClient }: { authClient?: AuthClient }) {
  return (
    <QueryClientProvider client={queryClient}>
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
