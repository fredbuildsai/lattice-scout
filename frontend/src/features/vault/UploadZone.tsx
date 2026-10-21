import { useId, useRef, useState } from 'react'
import { Icon } from '../../ui/icons'

/**
 * Drop zone and file picker for PDF/DOCX, with the "Keep the original file" checkbox
 * (unticked by default: otherwise only insights and short cited quotes are kept).
 */
export function UploadZone({ onFiles }: { onFiles: (files: File[], keepOriginal: boolean) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [keep, setKeep] = useState(false)
  const [over, setOver] = useState(false)
  const keepId = useId()
  const hintId = useId()
  const pick = (list: FileList | null) => {
    if (list && list.length) onFiles(Array.from(list), keep)
  }
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        pick(e.dataTransfer.files)
      }}
      className={`rounded-card border-2 border-dashed p-6 text-center md:p-10 ${over ? 'border-accent bg-accent-50' : 'border-line-strong bg-surface'}`}
    >
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-50 text-accent">
        <Icon name="upload" />
      </span>
      <h2 className="mt-3 text-lg font-semibold">Drop grants, proposals and results here</h2>
      <p className="mx-auto mt-1 max-w-[520px] text-sm text-ink-3">Funded or rejected — include reviewer reports if you have them. Scout works out what each file is.</p>
      <p className="mt-3 flex justify-center gap-2 font-mono text-xs text-ink-2">
        <span className="rounded-btn bg-subtle px-2 py-1">PDF</span>
        <span className="rounded-btn bg-subtle px-2 py-1">DOCX</span>
        <span className="rounded-btn bg-subtle px-2 py-1">up to 25 MB</span>
      </p>
      <input ref={input} type="file" multiple accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" aria-label="Choose files" className="sr-only" tabIndex={-1} onChange={(e) => { pick(e.target.files); e.target.value = '' }} />
      <button type="button" onClick={() => input.current?.click()} className="mt-4 min-h-11 rounded-btn bg-accent px-5 text-sm font-semibold text-white hover:bg-accent-dark">
        Choose files
      </button>
      <div className="mx-auto mt-4 flex max-w-[520px] items-start justify-center gap-2 text-left">
        <input id={keepId} type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} aria-describedby={hintId} className="mt-1 h-5 w-5 shrink-0 accent-accent" />
        <label htmlFor={keepId} className="text-sm">
          <span className="font-semibold">Keep the original file</span>
          <span id={hintId} className="block text-ink-3">
            Otherwise only the extracted insights and short cited quotes are kept, and the file is deleted after reading.
          </span>
        </label>
      </div>
    </div>
  )
}
