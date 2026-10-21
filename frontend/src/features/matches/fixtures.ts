import type { AnonymousMatch, RevealedMatch } from './api'

export const anon = (over: Partial<AnonymousMatch> = {}): AnonymousMatch => ({
  kind: 'anonymous',
  id: 'm1',
  status: 'awaiting_you',
  score: 0.876,
  field_label: 'Photocatalysis',
  region: 'europe',
  career_band: 'mid',
  shared_topics: ['PFAS'],
  you_bring: [{ text: 'Fungal membrane', source_refs: [] }],
  they_bring: [{ text: 'Iron-oxide catalysts' }],
  why: 'Complementary methods.',
  my_decision: null,
  created_at: '2026-10-01T00:00:00Z',
  expires_at: '2026-11-01T00:00:00Z',
  ...over,
})

export const revealed = (over: Partial<RevealedMatch> = {}): RevealedMatch => ({
  kind: 'revealed',
  id: 'm2',
  status: 'mutual',
  score: 0.88,
  shared_topics: [],
  you_bring: [{ text: 'Membrane', source_refs: [] }],
  they_bring: [{ text: 'Catalysts', source_refs: [] }],
  why: 'Co-authored before.',
  contact: { display_name: 'Ada Example', institution: 'Example University', email: 'ada@example.edu', orcid: '0000-0001-6636-725X', openalex_ids: ['A123'], region: 'europe', career_band: 'mid' },
  ideas_status: 'ready',
  joint_ideas: [{ title: 'Capture and destroy', question: 'Can it regenerate?', uses_from_you: [{ text: 'Membrane', source_refs: [] }], uses_from_them: [{ text: 'Catalysts', source_refs: [] }], first_step: 'Call', possible_output: 'Paper' }],
  revealed_at: '2026-10-10T00:00:00Z',
  my_rating: null,
  ...over,
})
