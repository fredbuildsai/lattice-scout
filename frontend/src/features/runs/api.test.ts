import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as analytics from '../../analytics/client'
import { ApiError } from '../../api/client'
import { isRunActive, latestDone, useStartRun } from './api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: new QueryClient() }, children)

describe('runs api', () => {
  it('classifies active runs and picks the latest done one', () => {
    expect(isRunActive('queued')).toBe(true)
    expect(isRunActive('done')).toBe(false)
    expect(latestDone([{ id: 'a', status: 'running' }, { id: 'b', status: 'done' }, { id: 'c', status: 'done' }] as never)?.id).toBe('b')
    expect(latestDone([])).toBeNull()
  })

  it('POSTs document ids and tracks run_started', async () => {
    const track = vi.spyOn(analytics, 'track').mockImplementation(() => undefined)
    const f = vi.fn(async () => new Response(JSON.stringify({ id: 'r1', document_ids: ['d1', 'd2'] }), { status: 202 }))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(() => useStartRun(), { wrapper })
    result.current.mutate(['d1', 'd2'])
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].body).toBe(JSON.stringify({ document_ids: ['d1', 'd2'] }))
    expect(track).toHaveBeenCalledWith('run_started', { run_id: 'r1', documents: 2 })
  })

  it('surfaces the problem detail on 409', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'run_in_progress', detail: 'A run is already in progress.' }), { status: 409 })))
    const { result } = renderHook(() => useStartRun(), { wrapper })
    result.current.mutate(['d1'])
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(ApiError)
    expect((result.current.error as ApiError).detail).toBe('A run is already in progress.')
  })
})
