import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

interface Props {
  title: string
  children: ReactNode
  /** Text the user must type exactly (for example `DELETE` or their e-mail). */
  expected: string
  /** Label of the typed field. */
  inputLabel: string
  confirmLabel: string
  busy: boolean
  /** Inline error from the server, shown with role=alert. */
  error?: string | null
  onCancel: () => void
  onConfirm: (typed: string) => void
}

/**
 * Destructive-action confirm dialog (same pattern as the vault delete dialog) that also requires typing
 * `expected`. The confirm button stays disabled until the text matches. Escape cancels; focus is trapped and starts on the input.
 */
export function TypedConfirmDialog({ title, children, expected, inputLabel, confirmLabel, busy, error, onCancel, onConfirm }: Props) {
  const [typed, setTyped] = useState('')
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLFormElement>(null)
  const onCancelRef = useRef(onCancel)
  onCancelRef.current = onCancel
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    input.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCancelRef.current()
        return
      }
      if (e.key !== 'Tab' || !box.current) return
      const items = Array.from(box.current.querySelectorAll<HTMLElement>('button:not([disabled]), input'))
      if (items.length === 0) return
      const first = items[0]!
      const last = items[items.length - 1]!
      const active = document.activeElement
      if (!box.current.contains(active)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [])
  const matches = typed === expected && expected !== ''
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (matches && !busy) onConfirm(typed)
        }}
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-body`}
        className="w-full max-w-[460px] rounded-card bg-surface p-5 shadow-lg"
      >
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {title}
        </h2>
        <div id={`${id}-body`} className="mt-2 text-sm text-ink-2">
          {children}
        </div>
        <label htmlFor={`${id}-input`} className="mt-4 block text-sm font-semibold">
          {inputLabel}
        </label>
        <input
          ref={input}
          id={`${id}-input`}
          type="text"
          autoComplete="off"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          className="mt-1 min-h-11 w-full rounded-btn border border-line-strong bg-surface px-3 text-sm"
        />
        {error && (
          <p role="alert" className="mt-2 text-sm text-warm">
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
            Cancel
          </button>
          <button type="submit" disabled={!matches || busy} className="min-h-11 rounded-btn bg-warm px-4 text-sm font-semibold text-white disabled:opacity-60">
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  )
}
