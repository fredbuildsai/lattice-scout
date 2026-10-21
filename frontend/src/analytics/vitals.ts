import { track } from './client'

/** Reports LCP, INP and CLS as `web_vital` events. Loaded lazily; failures are ignored. */
export async function startWebVitals(): Promise<void> {
  try {
    const { onLCP, onINP, onCLS } = await import('web-vitals')
    const report = (m: { name: string; value: number; rating: string }) =>
      track('web_vital', { metric: m.name, value: Math.round(m.value * 1000) / 1000, rating: m.rating })
    onLCP(report)
    onINP(report)
    onCLS(report)
  } catch {
    /* analytics must never break the app */
  }
}
