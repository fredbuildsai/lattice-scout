import { describe, expect, it } from 'vitest'
import { introDraft } from './intro'

const base = {
  contactName: 'Ada Example',
  myName: 'Johan Foster',
  sharedTopics: ['PFAS', 'Membranes'],
  youBring: [{ text: 'Fungal membrane' }, { text: 'BDD electrochemistry' }],
  theyBring: [{ text: 'Iron-oxide catalysts' }],
}

describe('introDraft', () => {
  it('is deterministic and uses topics and both bring lists', () => {
    const d = introDraft(base)
    expect(introDraft(base)).toBe(d)
    expect(d).toContain('Dear Ada Example,')
    expect(d).toContain('PFAS, Membranes')
    expect(d).toContain('- Fungal membrane')
    expect(d).toContain('- Iron-oxide catalysts')
    expect(d.trimEnd().endsWith('Johan Foster')).toBe(true)
  })

  it('names nobody but the contact and the sender and ignores the why text', () => {
    const d = introDraft({
      ...base,
      why: 'You co-authored with Raphaell Moreira',
    } as typeof base)
    expect(d).not.toContain('Moreira')
    expect(d).not.toMatch(/co-authored/)
  })

  it('leaves out empty sections and the sign-off name when unknown', () => {
    const d = introDraft({
      contactName: 'Ada Example',
      sharedTopics: [],
      youBring: [],
      theyBring: [],
    })
    expect(d).not.toContain('What I could bring')
    expect(d).not.toContain('share an interest')
    expect(d.trimEnd().endsWith('Best wishes')).toBe(true)
  })

  it('caps the number and length of items', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({
      text: `item${i} ${'x'.repeat(400)}`,
    }))
    const d = introDraft({ ...base, youBring: many })
    expect(d.split('\n').filter((l) => l.startsWith('- item'))).toHaveLength(3)
    expect(d.length).toBeLessThan(1500)
  })
})
