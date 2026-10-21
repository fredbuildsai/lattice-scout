import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { doiUrl, openAlexUrl, pageLabel, useFeedback, useOpportunities } from './api'

afterEach(() => vi.unstubAllGlobals())
const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }, children)

describe('opportunities api', () => {
  it('builds source links', () => {
    expect(doiUrl('10.1/abc')).toBe('https://doi.org/10.1/abc')
    expect(doiUrl('https://doi.org/10.1/abc')).toBe('https://doi.org/10.1/abc')
    expect(openAlexUrl('W1')).toBe('https://openalex.org/W1')
    expect(openAlexUrl('https://openalex.org/W1')).toBe('https://openalex.org/W1')
    expect(pageLabel(3, 3)).toBe('p. 3')
    expect(pageLabel(3, 5)).toBe('pp. 3–5')
    expect(pageLabel(null, null)).toBeNull()
  })

  it('passes run, status and limit as query parameters', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(() => useOpportunities({ runId: 'r1', status: 'dismissed', limit: 3 }), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const url = String((f.mock.calls[0] as unknown as [string])[0])
    expect(url).toContain('/api/v1/opportunities?')
    expect(url).toContain('run_id=r1')
    expect(url).toContain('status=dismissed')
    expect(url).toContain('limit=3')
  })

  it('treats a 404 feedback as no rating yet', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })))
    const { result } = renderHook(() => useFeedback('o1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
  })
})
