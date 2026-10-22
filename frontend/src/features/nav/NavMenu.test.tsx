import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fakeAuthClient, mockApi, ME_WITH_RESEARCHER, PROFILE } from '../../test-utils'
import { analytics } from '../../analytics/client'
import { App } from '../../App'
import { routePattern } from '../../analytics/PageViewTracker'

describe('routePattern', () => {
  it('maps paths to patterns, never raw ids', () => {
    expect(routePattern('/opportunities/abc-123')).toBe('/opportunities/:id')
    expect(routePattern('/runs/9')).toBe('/runs/:id')
    expect(routePattern('/nope')).toBe('*')
  })
})

describe('header menu', () => {
  beforeEach(() => {
    mockApi({ '/api/v1/me': ME_WITH_RESEARCHER, '/api/v1/profile': PROFILE })
  })

  it('opens, navigates, closes, and tracks', async () => {
    const track = vi.spyOn(analytics, 'track')
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <App authClient={fakeAuthClient()} />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { level: 1, name: 'Home' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    expect(track).toHaveBeenCalledWith('menu_opened', undefined)
    expect(screen.getByRole('navigation', { name: 'All destinations' })).toBeInTheDocument()
    await user.click(within(screen.getByRole('navigation', { name: 'All destinations' })).getByRole('link', { name: /Matches/ }))
    expect(track).toHaveBeenCalledWith('menu_item_clicked', { item: 'matches' })
    expect(screen.getByRole('heading', { level: 1, name: 'Matches' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'All destinations' })).toBeNull()
  })

  it('closes on Escape and shows no Ask Scout button', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <App authClient={fakeAuthClient()} />
      </MemoryRouter>,
    )
    await user.click(await screen.findByRole('button', { name: 'Open menu' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('navigation', { name: 'All destinations' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ask Scout' })).toBeNull()
  })
})
