import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { PageViewTracker } from './analytics/PageViewTracker'
import { EmptyPage } from './features/pages/EmptyPage'
import { Layout } from './Layout'
import { ROUTES } from './routes'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

/** Root component. Must be rendered inside a Router. */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PageViewTracker />
      <Routes>
        <Route element={<Layout />}>
          {ROUTES.map((r) => (
            <Route key={r.path} path={r.path} element={<EmptyPage title={r.title} />} />
          ))}
          <Route path="*" element={<EmptyPage title="Page not found" />} />
        </Route>
      </Routes>
    </QueryClientProvider>
  )
}
