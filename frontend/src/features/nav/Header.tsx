import { useCallback, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { track } from '../../analytics/client'
import { useAuth } from '../../auth/AuthProvider'
import { LogoMark } from '../../ui/icons'
import { NavMenu } from './NavMenu'
import { TOP_LINKS } from './menuItems'

/** App header: burger button, logo, and top links (from 768 px). */
export function Header() {
  const [open, setOpen] = useState(false)
  const { client } = useAuth()
  const burger = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => {
    setOpen(false)
    burger.current?.focus()
  }, [])

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface">
      <div className="mx-auto flex h-16 max-w-[1240px] items-center gap-3 px-4 md:px-0">
        <button
          ref={burger}
          type="button"
          aria-label="Open menu"
          aria-expanded={open}
          onClick={() => {
            setOpen(true)
            track('menu_opened')
          }}
          className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-line bg-surface text-ink"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <NavLink to="/" className="flex items-center gap-2 text-ink no-underline">
          <LogoMark />
          <b className="text-base">Lattice Scout</b>
        </NavLink>
        <nav aria-label="Primary" className="ml-4 hidden h-full items-stretch gap-1 md:flex">
          {TOP_LINKS.map((l) => (
            <NavLink
              key={l.id}
              to={l.to}
              end
              className={({ isActive }) =>
                `flex items-center border-b-2 px-3 text-sm ${isActive ? 'border-accent font-semibold text-ink' : 'border-transparent text-ink-2 hover:text-ink'}`
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <button type="button" onClick={() => void client.signOut()} className="ml-auto h-11 rounded-btn px-3 text-sm text-ink-2 hover:text-ink">
          Sign out
        </button>
      </div>
      {open && <NavMenu onClose={close} />}
    </header>
  )
}
