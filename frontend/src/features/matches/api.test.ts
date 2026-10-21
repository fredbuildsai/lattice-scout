import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { errorText, openAlexEntityUrl, orcidUrl, scorePercent, useMatchFeedback, useMatches } from './api'

afterEach(() => vi.unstubAllGlobals())
const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }, children)

describe('matches api', () => {
  it('builds links and percentages', () => {
    expect(orcidUrl('0000-0001-6636-725X')).toBe('https://orcid.org/0000-0001-6636-725X')
    expect(openAlexEntityUrl('A1')).toBe('https://openalex.org/A1')
    expect(openAlexEntityUrl('https://openalex.org/A1')).toBe('https://openalex.org/A1')
    expect(scorePercent(0.876)).toBe(88)
  })
  it('loads the list with a limit', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ matching_enabled: true, items: [], next_cursor: null }), { status: 200 }))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(() => useMatches(10), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(String((f.mock.calls[0] as unknown as [string])[0])).toContain('/api/v1/matches?limit=10')
  })
  it('treats a 404 feedback as not rated', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })))
    const { result } = renderHook(() => useMatchFeedback('m1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
  })
  it('shows problem detail, and a gone message for 404', () => {
    expect(errorText(new ApiError(409, '/x', 'This proposal has expired.'))).toBe('This proposal has expired.')
    expect(errorText(new ApiError(404, '/x'))).toMatch(/no longer available/)
    expect(errorText(new Error('x'))).toMatch(/went wrong/)
  })
})
