import { useSystemStatus } from './api'

/**
 * Non-blocking notice shown while `GET /system/status` says new analyses are paused
 * (shared daily budget used up). Renders nothing otherwise, and nothing while loading or on error.
 */
export function PausedBanner() {
  const status = useSystemStatus()
  if (status.data?.accepting_runs !== false) return null
  return (
    <div role="status" className="mt-4 rounded-card border border-warm-mid/40 bg-warm-50 p-3 text-sm text-warm">
      {status.data.message}
    </div>
  )
}
