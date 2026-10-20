import type { IconName } from '../../ui/icons'

export interface MenuItem {
  id: string
  label: string
  desc: string
  to: string
  icon: IconName
}
export interface MenuGroup {
  title: string
  items: MenuItem[]
}

/** Menu content from the NavMenu design. Roadmap entries (Reach, Ask Scout, Recent) are hidden. */
export const MENU_GROUPS: MenuGroup[] = [
  {
    title: 'Workspace',
    items: [
      { id: 'home', label: 'Home', desc: 'Scout’s briefing', to: '/', icon: 'home' },
      { id: 'vault', label: 'Private vault', desc: 'Your unpublished documents', to: '/vault', icon: 'vault' },
      { id: 'opportunities', label: 'Opportunities', desc: 'All ideas, ranked by fit', to: '/opportunities', icon: 'idea' },
    ],
  },
  {
    title: 'People',
    items: [{ id: 'matches', label: 'Matches', desc: 'Double opt-in matching', to: '/matches', icon: 'people' }],
  },
  {
    title: 'Account',
    items: [
      { id: 'privacy', label: 'Profile and privacy', desc: 'Sources, consents and data', to: '/settings/privacy', icon: 'lock' },
      { id: 'onboarding', label: 'Claim profile', desc: 'Review your claimed profile', to: '/onboarding', icon: 'redo' },
      { id: 'signin', label: 'Sign in', desc: 'Google or email link', to: '/signin', icon: 'user' },
    ],
  },
]

/** Top-bar links shown from tablet width up. */
export const TOP_LINKS = MENU_GROUPS[0]!.items.concat(MENU_GROUPS[1]!.items)
