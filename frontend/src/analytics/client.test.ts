import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AnalyticsClient, FLUSH_AT, type Transport, type WireBatch } from './client'
import { SESSION_TIMEOUT_MS } from './session'

function setup(opts: { sendResults?: boolean[] } = {}) {
  sessionStorage.clear()
  let t = 1_000_000
  const sent: WireBatch[] = []
  const results = [...(opts.sendResults ?? [])]
  const transport: Transport = {
    send: vi.fn(async (b: WireBatch) => {
      sent.push(b)
      return results.length ? (results.shift() as boolean) : true
    }),
    beacon: vi.fn((b: WireBatch) => void sent.push(b)),
  }
  const scheduled: Array<() => void> = []
  const client = new AnalyticsClient({ enabled: true, now: () => t, transport, schedule: (f) => scheduled.push(f) })
  return { client, transport, sent, scheduled, advance: (ms: number) => (t += ms) }
}

describe('page_view', () => {
  it('sets prev_page and dwell_ms of the page being left, with increasing seq', async () => {
    const { client, sent, advance } = setup()
    client.pageView('/')
    advance(1500)
    client.pageView('/opportunities/:id')
    advance(300)
    client.pageView('/matches')
    await client.flush()
    const ev = sent[0]!.events
    expect(ev.map((e) => [e.name, e.page, e.prev_page, e.dwell_ms, e.seq])).toEqual([
      ['page_view', '/', null, null, 0],
      ['page_view', '/opportunities/:id', '/', 1500, 1],
      ['page_view', '/matches', '/opportunities/:id', 300, 2],
    ])
    expect(sent[0]!.session.entry_page).toBe('/')
  })
})

describe('batching', () => {
  it('schedules a flush at 20 events and sends them in one batch', async () => {
    const { client, scheduled, sent } = setup()
    for (let i = 0; i < FLUSH_AT - 1; i++) client.track('menu_opened')
    expect(scheduled).toHaveLength(0)
    client.track('menu_opened')
    expect(scheduled).toHaveLength(1)
    scheduled[0]!()
    await vi.waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]!.events).toHaveLength(FLUSH_AT)
    expect(client.pending).toBe(0)
  })

  it('splits at 100 events per request', async () => {
    const { client, sent } = setup()
    for (let i = 0; i < 230; i++) client.track('menu_opened')
    await client.flush()
    expect(sent.map((b) => b.events.length)).toEqual([100, 100, 30])
  })

  it('flushes on a 10 s timer', () => {
    vi.useFakeTimers()
    try {
      const { client, scheduled } = setup()
      client.track('menu_opened')
      vi.advanceTimersByTime(10_000)
      expect(scheduled).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses the beacon on page hide', () => {
    const { client, transport } = setup()
    const off = client.install()
    client.track('scout_fab_clicked')
    window.dispatchEvent(new Event('pagehide'))
    expect(transport.beacon).toHaveBeenCalledOnce()
    off()
  })

  it('retries once then drops, never throwing', async () => {
    const { client, transport } = setup({ sendResults: [false, false] })
    client.track('menu_opened')
    await expect(client.flush()).resolves.toBeUndefined()
    expect(transport.send).toHaveBeenCalledTimes(2)
    expect(client.pending).toBe(0)
  })

  it('swallows transport exceptions', async () => {
    const { client, transport } = setup()
    vi.mocked(transport.send).mockRejectedValue(new Error('offline'))
    client.track('menu_opened')
    await expect(client.flush()).resolves.toBeUndefined()
  })
})

describe('properties and sessions', () => {
  beforeEach(() => sessionStorage.clear())

  it('drops property keys outside the catalogue', async () => {
    const { client, sent } = setup()
    client.track('menu_item_clicked', { item: 'vault', email: 'x@y.z' } as never)
    await client.flush()
    expect(sent[0]!.events[0]!.properties).toEqual({ item: 'vault' })
  })

  it('rotates the session after inactivity and restarts seq', async () => {
    const { client, sent, advance } = setup()
    client.pageView('/')
    const first = client.getSessionId()
    advance(SESSION_TIMEOUT_MS + 1)
    client.pageView('/vault')
    await client.flush()
    expect(sent.length).toBeGreaterThanOrEqual(2)
    const last = sent[sent.length - 1]!
    expect(last.session.id).not.toBe(first)
    expect(last.events[0]!.seq).toBe(0)
  })

  it('does nothing when disabled', () => {
    const c = new AnalyticsClient({ enabled: false })
    c.pageView('/')
    c.track('menu_opened')
    expect(c.pending).toBe(0)
  })
})

describe('opt-out', () => {
  it('drops new events and discards queued ones when opted out, resumes when cleared', async () => {
    const { client, sent } = setup()
    client.track('menu_opened')
    client.setOptOut(true)
    expect(client.pending).toBe(0)
    client.pageView('/')
    client.track('menu_opened')
    await client.flush()
    expect(sent).toHaveLength(0)
    client.setOptOut(false)
    client.track('menu_opened')
    expect(client.pending).toBe(1)
  })
})
