import { useEffect, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import { track } from '../../analytics/client'
import { Icon, LogoMark } from '../../ui/icons'
import { MENU_GROUPS } from './menuItems'

/**
 * Slide-in navigation menu (design W11). Closes on Escape, backdrop click or navigation;
 * focus moves to the close button on open and the parent restores it on close.
 */
export function NavMenu({ onClose }: { onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') return onClose()
      if (e.key !== 'Tab' || !panelRef.current) return
      const f = panelRef.current.querySelectorAll<HTMLElement>('a[href],button,input')
      const first = f[0]
      const last = f[f.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last?.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[60] flex" role="dialog" aria-modal="true" aria-label="Menu">
      <nav
        ref={panelRef}
        aria-label="All destinations"
        className="flex h-full w-[340px] max-w-[88vw] flex-col overflow-y-auto border-r border-line bg-surface shadow-[12px_0_40px_rgba(20,23,26,0.18)]"
      >
        <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
          <LogoMark />
          <b className="flex-grow text-[17px]">Lattice Scout</b>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="flex h-11 w-11 items-center justify-center rounded-[10px] text-ink-2 hover:bg-subtle"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        {MENU_GROUPS.map((g) => (
          <div key={g.title} className="flex flex-col px-2 pb-1 pt-2">
            <span className="px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-[0.06em] text-ink-3">{g.title}</span>
            {g.items.map((i) => (
              <NavLink
                key={i.id}
                to={i.to}
                end
                onClick={() => {
                  track('menu_item_clicked', { item: i.id })
                  onClose()
                }}
                className={({ isActive }) =>
                  `flex min-h-12 items-center gap-3 rounded-[10px] px-2.5 py-2 text-ink no-underline hover:bg-subtle ${isActive ? 'bg-accent-50' : ''}`
                }
              >
                {({ isActive }) => (
                  <>
                    <span className={`flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg ${isActive ? 'bg-accent text-white' : 'bg-subtle text-ink-2'}`}>
                      <Icon name={i.icon} />
                    </span>
                    <span className="flex min-w-0 flex-grow flex-col">
                      <span className="text-sm font-semibold">{i.label}</span>
                      <span className="truncate text-xs text-ink-3">{i.desc}</span>
                    </span>
                  </>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <button type="button" tabIndex={-1} aria-label="Close menu" onClick={onClose} className="flex-grow cursor-pointer border-0 bg-ink/35 p-0" />
    </div>
  )
}
