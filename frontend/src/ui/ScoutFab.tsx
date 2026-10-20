import { useState } from 'react'
import { track } from '../analytics/client'
import { Icon } from './icons'

/** Floating "Ask Scout" button. Placeholder in the MVP: records the click and says it is coming. */
export function ScoutFab() {
  const [note, setNote] = useState(false)
  return (
    <div className="fixed bottom-4 right-4 z-30 flex flex-col items-end gap-2">
      <p role="status" className={`rounded-lg bg-scout-50 px-3 py-2 text-sm text-scout-ink ${note ? '' : 'sr-only'}`}>
        {note ? 'Ask Scout is coming soon.' : ''}
      </p>
      <button
        type="button"
        onClick={() => {
          track('scout_fab_clicked')
          setNote(true)
        }}
        className="flex h-12 items-center gap-2 rounded-full bg-scout px-5 text-sm font-semibold text-white shadow-lg hover:bg-scout-ink"
      >
        <Icon name="sparkle" size={16} />
        Ask Scout
      </button>
    </div>
  )
}
