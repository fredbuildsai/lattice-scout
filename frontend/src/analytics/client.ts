import { API_URL, ANALYTICS_ENABLED } from '../api/config'
import { EVENT_PROPERTIES, type EventName, type EventProps } from './events'
import { touchSession } from './session'

export const FLUSH_INTERVAL_MS = 10_000
export const FLUSH_AT = 20
export const MAX_BATCH = 100

export interface WireEvent {
  name: string
  page: string | null
  prev_page: string | null
  dwell_ms: number | null
  properties: Record<string, string | number | boolean | null>
  client_ts: string
  seq: number
}

export interface WireBatch {
  session: {
    id: string
    viewport: string
    app_version: string
    entry_page: string | null
    referrer_domain: string | null
  }
  events: WireEvent[]
}

export interface Transport {
  /** Normal send; resolves true on a 2xx. Must not throw (a rejection counts as failure). */
  send(batch: WireBatch, sessionId: string, page: string | null): Promise<boolean>
  /** Unload-time send; fire and forget. */
  beacon(batch: WireBatch, sessionId: string, page: string | null): void
}

export interface AnalyticsDeps {
  enabled?: boolean
  now?: () => number
  transport?: Transport
  storage?: Storage
  /** Schedules non-urgent work (requestIdleCallback with a timeout fallback). */
  schedule?: (fn: () => void) => void
  appVersion?: string
}

/** Default transport: fetch with keepalive; sendBeacon (text/plain) on unload. */
export const httpTransport: Transport = {
  async send(batch, sessionId, page) {
    const res = await fetch(`${API_URL}/api/v1/events`, {
      method: 'POST',
      keepalive: true,
      headers: {
        'Content-Type': 'application/json',
        'X-Session-Id': sessionId,
        ...(page ? { 'X-Page': page } : {}),
      },
      body: JSON.stringify(batch),
    })
    return res.ok
  },
  beacon(batch, sessionId, page) {
    const body = JSON.stringify(batch)
    const url = `${API_URL}/api/v1/events`
    if (typeof navigator.sendBeacon === 'function' && navigator.sendBeacon(url, new Blob([body], { type: 'text/plain' })))
      return
    void fetch(url, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId, ...(page ? { 'X-Page': page } : {}) },
      body,
    }).catch(() => undefined)
  },
}

function idle(fn: () => void): void {
  if (typeof requestIdleCallback === 'function') requestIdleCallback(fn, { timeout: 2000 })
  else setTimeout(fn, 0)
}

/**
 * Batching analytics client. Events queue in memory and flush every 10 s, at 20 events,
 * and on page hide. A failed send is retried once, then dropped. Nothing here ever throws.
 */
export class AnalyticsClient {
  private queue: WireEvent[] = []
  private seq = 0
  private sessionId: string | null = null
  private entryPage: string | null = null
  private currentPage: string | null = null
  private pageSince = 0
  private timer: ReturnType<typeof setInterval> | null = null
  private flushScheduled = false
  private readonly enabled: boolean
  private readonly now: () => number
  private readonly transport: Transport
  private readonly storage: Storage | undefined
  private readonly schedule: (fn: () => void) => void
  private readonly appVersion: string

  constructor(deps: AnalyticsDeps = {}) {
    this.enabled = deps.enabled ?? ANALYTICS_ENABLED
    this.now = deps.now ?? Date.now
    this.transport = deps.transport ?? httpTransport
    this.storage = deps.storage
    this.schedule = deps.schedule ?? idle
    this.appVersion = deps.appVersion ?? '0.1.0'
  }

  /** Route pattern of the page currently shown (sent as `X-Page`). */
  get page(): string | null {
    return this.currentPage
  }

  /** Current session id (creates one if needed; does not count as activity). */
  getSessionId(): string {
    if (!this.sessionId) this.sessionId = touchSession(this.now(), this.storage).id
    return this.sessionId
  }

  /** Records a navigation: emits `page_view` with `prev_page` and the dwell time of the page being left. */
  pageView(page: string): void {
    try {
      const t = this.now()
      const prev = this.currentPage
      const dwell = prev === null ? null : Math.max(0, Math.round(t - this.pageSince))
      if (this.entryPage === null) this.entryPage = page
      this.enqueue('page_view', {}, { page, prev_page: prev, dwell_ms: dwell })
      this.currentPage = page
      this.pageSince = t
    } catch {
      /* never throw */
    }
  }

  /** Records a typed feature event on the current page. */
  track<N extends EventName>(name: N, properties?: EventProps<N>): void {
    try {
      const allowed: readonly string[] = EVENT_PROPERTIES[name]
      const props: Record<string, string | number | boolean | null> = {}
      for (const [k, v] of Object.entries(properties ?? {})) {
        if (allowed.includes(k) && v !== undefined) props[k] = typeof v === 'string' ? v.slice(0, 64) : (v as number | boolean | null)
      }
      this.enqueue(name, props, { page: this.currentPage, prev_page: null, dwell_ms: null })
    } catch {
      /* never throw */
    }
  }

  private enqueue(
    name: string,
    properties: WireEvent['properties'],
    extra: Pick<WireEvent, 'page' | 'prev_page' | 'dwell_ms'>,
  ): void {
    if (!this.enabled) return
    const t = this.now()
    const { id, rotated } = touchSession(t, this.storage)
    if (rotated && this.sessionId !== null) {
      // Inactivity rotation: events of the old session go out first, sequence restarts.
      this.flushNow(false)
      this.seq = 0
      this.entryPage = extra.page
    }
    this.sessionId = id
    this.queue.push({
      name,
      ...extra,
      properties,
      client_ts: new Date(t).toISOString(),
      seq: this.seq++,
    })
    this.ensureTimer()
    if (this.queue.length >= FLUSH_AT) this.requestFlush()
  }

  private ensureTimer(): void {
    if (this.timer === null && typeof setInterval === 'function') {
      this.timer = setInterval(() => this.requestFlush(), FLUSH_INTERVAL_MS)
    }
  }

  private requestFlush(): void {
    if (this.flushScheduled) return
    this.flushScheduled = true
    this.schedule(() => {
      this.flushScheduled = false
      void this.flush()
    })
  }

  private takeBatch(): { batch: WireBatch; sid: string } | null {
    if (this.queue.length === 0) return null
    const events = this.queue.splice(0, MAX_BATCH)
    const sid = this.getSessionId()
    return {
      sid,
      batch: {
        session: {
          id: sid,
          viewport: `${window.innerWidth}x${window.innerHeight}`,
          app_version: this.appVersion,
          entry_page: this.entryPage,
          referrer_domain: referrerDomain(),
        },
        events,
      },
    }
  }

  /** Sends everything queued (in batches of at most 100); one retry per batch, then drops. */
  async flush(): Promise<void> {
    let next = this.takeBatch()
    while (next) {
      const { batch, sid } = next
      try {
        let ok = false
        try {
          ok = await this.transport.send(batch, sid, this.currentPage)
        } catch {
          ok = false
        }
        if (!ok) await this.transport.send(batch, sid, this.currentPage)
      } catch {
        /* dropped */
      }
      next = this.takeBatch()
    }
  }

  /** Synchronous unload-time flush through sendBeacon / keepalive fetch. */
  flushNow(unloading = true): void {
    try {
      let next = this.takeBatch()
      while (next) {
        if (unloading) this.transport.beacon(next.batch, next.sid, this.currentPage)
        else void this.transport.send(next.batch, next.sid, this.currentPage).catch(() => undefined)
        next = this.takeBatch()
      }
    } catch {
      /* never throw */
    }
  }

  /** Number of queued events (tests). */
  get pending(): number {
    return this.queue.length
  }

  /** Registers page-hide flushing and stops on dispose. Returns a cleanup function. */
  install(): () => void {
    const onHide = () => {
      if (document.visibilityState === 'hidden') this.flushNow()
    }
    const onPageHide = () => this.flushNow()
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('pagehide', onPageHide)
      if (this.timer !== null) clearInterval(this.timer)
      this.timer = null
    }
  }
}

function referrerDomain(): string | null {
  try {
    return document.referrer ? new URL(document.referrer).hostname : null
  } catch {
    return null
  }
}

/** Shared client used by the app. */
export const analytics = new AnalyticsClient()

/** Typed feature-event helper. Never throws. */
export function track<N extends EventName>(name: N, properties?: EventProps<N>): void {
  analytics.track(name, properties)
}
