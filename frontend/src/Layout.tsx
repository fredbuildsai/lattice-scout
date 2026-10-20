import { Outlet } from 'react-router-dom'
import { Header } from './features/nav/Header'
import { ScoutFab } from './ui/ScoutFab'

/** Shell: skip link, header, page outlet, Scout FAB. */
export function Layout() {
  return (
    <div className="min-h-screen bg-bg font-sans text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-btn focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Header />
      <main id="main" className="mx-auto max-w-[1240px] px-4 py-8 md:px-0">
        <Outlet />
      </main>
      <ScoutFab />
    </div>
  )
}
