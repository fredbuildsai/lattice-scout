import { afterEach, describe, expect, it, vi } from 'vitest'
import { uploadDocument, VaultError } from './api'

class FakeXhr {
  static last: FakeXhr
  status = 0
  responseText = ''
  upload: { onprogress?: unknown } = {}
  onload?: () => void
  onerror?: () => void
  onabort?: () => void
  ontimeout?: () => void
  aborted = false
  constructor() {
    FakeXhr.last = this
  }
  open() {}
  setRequestHeader() {}
  send() {}
  abort() {
    this.aborted = true
    this.onabort?.()
  }
}

const file = () => new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })

afterEach(() => vi.unstubAllGlobals())

describe('uploadDocument', () => {
  it('rejects with http_error when a 201 body is not JSON (does not hang)', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const p = uploadDocument(file(), false, () => undefined)
    FakeXhr.last.status = 201
    FakeXhr.last.responseText = '<html>oops</html>'
    FakeXhr.last.onload?.()
    await expect(p).rejects.toMatchObject({ code: 'http_error', status: 201 })
  })

  it('carries the problem reason', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const p = uploadDocument(file(), false, () => undefined)
    FakeXhr.last.status = 422
    FakeXhr.last.responseText = JSON.stringify({ code: 'file_rejected', reason: 'empty_file' })
    FakeXhr.last.onload?.()
    await expect(p).rejects.toMatchObject({ code: 'file_rejected', reason: 'empty_file' })
  })

  it('rejects on timeout and on abort', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const t = uploadDocument(file(), false, () => undefined)
    FakeXhr.last.ontimeout?.()
    await expect(t).rejects.toMatchObject({ code: 'network_error' })

    const ctl = new AbortController()
    const a = uploadDocument(file(), false, () => undefined, ctl.signal)
    ctl.abort()
    expect(FakeXhr.last.aborted).toBe(true)
    await expect(a).rejects.toBeInstanceOf(VaultError)
    await expect(a).rejects.toMatchObject({ code: 'aborted' })
  })
})
