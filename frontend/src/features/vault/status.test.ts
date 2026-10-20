import { describe, expect, it } from 'vitest'
import { failureMessage, hasActiveDocuments, statusChip, uploadErrorMessage, validateFile } from './status'

describe('statusChip', () => {
  it.each([
    ['uploaded', 'Uploaded'],
    ['extracting', 'Reading'],
    ['extracted', 'Finding insights'],
    ['analysing', 'Finding insights'],
    ['ready', 'Ready'],
    ['failed', 'Couldn’t read'],
    ['rejected', 'Not supported'],
  ])('%s -> %s', (s, label) => expect(statusChip(s).label).toBe(label))
})

describe('messages', () => {
  const codes = ['encrypted', 'no_text_layer', 'too_many_pages', 'text_too_long', 'corrupt_file', 'zip_bomb', 'extraction_timeout', 'too_complex', 'original_missing', 'ingest_stalled', 'extraction_failed', 'insights_unavailable', 'storage_unavailable']
  it('has a distinct friendly message for every failure code', () => {
    const msgs = codes.map((c) => failureMessage(c))
    expect(new Set(msgs).size).toBe(codes.length)
    expect(failureMessage('mystery')).toMatch(/could not process/)
  })
  it('maps API error codes', () => {
    for (const c of ['file_too_large', 'unsupported_file_type', 'vault_limit_reached', 'file_rejected']) expect(uploadErrorMessage(c)).not.toMatch(/failed\. Please/)
  })
  it('validates type and size client-side', () => {
    expect(validateFile({ name: 'a.PDF', size: 10 })).toBeNull()
    expect(validateFile({ name: 'a.xlsx', size: 10 })).toBe('unsupported_file_type')
    expect(validateFile({ name: 'a.pdf', size: 26 * 1024 * 1024 })).toBe('file_too_large')
  })
})

describe('hasActiveDocuments', () => {
  it('is true only between uploaded and analysing', () => {
    expect(hasActiveDocuments([{ status: 'ready' }, { status: 'extracted' }])).toBe(true)
    expect(hasActiveDocuments([{ status: 'ready' }, { status: 'failed' }, { status: 'rejected' }])).toBe(false)
    expect(hasActiveDocuments(undefined)).toBe(false)
  })
})
