import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { track } from '../../analytics/client'
import { Icon } from '../../ui/icons'
import { DocumentRow } from './DocumentRow'
import { UploadZone } from './UploadZone'
import { uploadDocument, useDocuments, VaultError, type Uploader } from './api'
import { uploadErrorMessage, validateFile } from './status'

interface UploadItem {
  key: number
  name: string
  progress: number
  state: 'uploading' | 'done' | 'duplicate' | 'error'
  message?: string
}

let nextKey = 1

/**
 * Private vault (W3 / M3): upload drop zone, per-file upload progress, and the list of
 * documents with status chips (polled every 2 s while any is being processed).
 * `upload` is injectable for tests.
 */
export function VaultPage({ upload = uploadDocument }: { upload?: Uploader }) {
  const docs = useDocuments()
  const qc = useQueryClient()
  const [items, setItems] = useState<UploadItem[]>([])
  const patch = (key: number, p: Partial<UploadItem>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...p } : x)))
  // One controller for every in-flight upload: leaving the page aborts the XHRs.
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    const c = new AbortController()
    controller.current = c
    return () => c.abort()
  }, [])

  const onFiles = (files: File[], keep: boolean) => {
    for (const file of files) {
      const key = nextKey++
      const invalid = validateFile(file)
      const ext = file.name.toLowerCase().endsWith('.docx') ? 'docx' : 'pdf'
      if (invalid) {
        setItems((xs) => [...xs, { key, name: file.name, progress: 0, state: 'error', message: uploadErrorMessage(invalid) }])
        track('upload_failed', { reason: invalid })
        continue
      }
      setItems((xs) => [...xs, { key, name: file.name, progress: 0, state: 'uploading' }])
      track('upload_started', { kind: ext })
      const signal = controller.current?.signal
      upload(file, keep, (f) => patch(key, { progress: f }), signal)
        .then(({ document, duplicate }) => {
          patch(key, { progress: 1, state: duplicate ? 'duplicate' : 'done' })
          if (!duplicate) track('upload_completed', { document_id: document.id, kind: ext })
          return qc.invalidateQueries({ queryKey: ['documents'] })
        })
        .catch((e: unknown) => {
          if (signal?.aborted) return
          const code = e instanceof VaultError ? e.code : 'network_error'
          patch(key, { state: 'error', message: uploadErrorMessage(code, e instanceof VaultError ? e.reason : undefined) })
          track('upload_failed', { reason: code })
        })
    }
  }

  const list = docs.data?.items ?? []
  return (
    <section aria-labelledby="page-title" className="mx-auto max-w-[1000px]">
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
          Private vault
        </h1>
        <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink-2">
          <Icon name="lock" size={14} /> Private to you
        </span>
      </div>
      <p className="mt-2 max-w-[640px] text-ink-2">Everything that never made it into a paper. Scout reads it to find opportunities — nobody else can see it, and it is never used to train models.</p>

      <div className="mt-6">
        <UploadZone onFiles={onFiles} />
      </div>

      {items.length > 0 && (
        <ul aria-label="Uploads" className="mt-4 space-y-2">
          {items.map((u) => (
            <li key={u.key} className="rounded-card border border-line bg-surface p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 break-words font-semibold">{u.name}</span>
                <span role={u.state === 'error' ? 'alert' : 'status'} className={u.state === 'error' ? 'text-warm' : 'text-ink-3'}>
                  {u.state === 'uploading' && `Uploading ${Math.round(u.progress * 100)}%`}
                  {u.state === 'done' && 'Uploaded'}
                  {u.state === 'duplicate' && 'Already in your vault'}
                  {u.state === 'error' && u.message}
                </span>
              </div>
              {u.state === 'uploading' && <progress className="mt-2 h-2 w-full" max={1} value={u.progress} aria-label={`Upload progress for ${u.name}`} />}
              {u.state !== 'uploading' && (
                <button type="button" onClick={() => setItems((xs) => xs.filter((x) => x.key !== u.key))} className="mt-1 min-h-11 text-xs font-semibold text-accent hover:underline" aria-label={`Dismiss ${u.name}`}>
                  Dismiss
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2 className="mt-8 text-lg font-semibold">Your documents</h2>
      {docs.isPending && (
        <p role="status" className="mt-3 text-ink-3">
          Loading your vault…
        </p>
      )}
      {docs.isError && (
        <p role="alert" className="mt-3 text-warm">
          We could not load your documents.{' '}
          <button type="button" className="underline" onClick={() => void docs.refetch()}>
            Try again
          </button>
        </p>
      )}
      {docs.data && list.length === 0 && <p className="mt-3 text-ink-2">Nothing here yet. Add a proposal, a reviewer report or unpublished results to get started.</p>}
      {list.length > 0 && (
        <ul aria-label="Documents" className="mt-3 space-y-3">
          {list.map((d) => (
            <DocumentRow key={d.id} doc={d} />
          ))}
        </ul>
      )}
    </section>
  )
}
