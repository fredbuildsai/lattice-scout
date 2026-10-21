import type { components } from '../../api/client'

export type DocStatus = components['schemas']['DocStatus']
export type DocKind = components['schemas']['DocKind']

export type ChipTone = 'neutral' | 'busy' | 'ok' | 'bad'

/** Statuses during which the worker is still busy, so the list must be polled. */
export const ACTIVE_STATUSES: readonly string[] = ['uploaded', 'extracting', 'extracted', 'analysing']

/** True while any document is between uploaded and analysing. */
export function hasActiveDocuments(items: readonly { status: string }[] | undefined): boolean {
  return !!items?.some((d) => ACTIVE_STATUSES.includes(d.status))
}

/** Plain-language status chip for a document status. */
export function statusChip(status: string): { label: string; tone: ChipTone } {
  switch (status) {
    case 'uploaded':
      return { label: 'Uploaded', tone: 'neutral' }
    case 'extracting':
      return { label: 'Reading', tone: 'busy' }
    case 'extracted':
    case 'analysing':
      return { label: 'Finding insights', tone: 'busy' }
    case 'awaiting_approved_provider':
      return { label: 'Waiting for an approved provider', tone: 'neutral' }
    case 'ready':
      return { label: 'Ready', tone: 'ok' }
    case 'rejected':
      return { label: 'Not supported', tone: 'bad' }
    case 'failed':
      return { label: 'Couldn’t read', tone: 'bad' }
    case 'deleting':
      return { label: 'Removing', tone: 'neutral' }
    default:
      return { label: 'Uploaded', tone: 'neutral' }
  }
}

/** Shown under a document that is stored but not yet read by a language model (ADR 016). */
export const AWAITING_PROVIDER_NOTE =
  'Scout has read this file on its server but not sent it to any AI model, because no approved AI provider is configured. Its extracted text stays here (the uploaded file is removed if you chose to discard it) and is analysed automatically once the operator approves a provider. Meanwhile your analyses use your published work only.'

const FAILURES: Record<string, string> = {
  encrypted: 'This file is password-protected. Remove the password and upload it again.',
  no_text_layer: 'This looks like a scan with no readable text. Export a text PDF, or upload the Word version.',
  too_many_pages: 'This document has too many pages for Scout to read. Upload the relevant part.',
  text_too_long: 'This document has too much text for Scout to read. Upload the relevant part.',
  corrupt_file: 'This file looks damaged and could not be opened. Try exporting it again.',
  zip_bomb: 'This file was rejected because it unpacks to an unsafe size.',
  extraction_timeout: 'Reading this file took too long. Try a smaller or simpler version.',
  too_complex: 'This file is too complex for Scout to read reliably. Try a simpler export.',
  original_missing: 'The stored original is missing, so Scout cannot read it again. Please upload it again.',
  ingest_stalled: 'Reading this file stalled. Please delete it and upload it again.',
  extraction_failed: 'Scout could not read this file. Please delete it and upload the file again.',
  insights_unavailable: 'Scout read the file but could not extract insights. Please delete it and upload the file again.',
  storage_unavailable: 'Storage was unavailable while reading this file. Please delete it and upload the file again.',
}

/** Friendly explanation for a document `failure_code`. */
export function failureMessage(code?: string | null): string {
  return (code && FAILURES[code]) || 'Scout could not process this file.'
}

const ERRORS: Record<string, string> = {
  file_too_large: 'This file is larger than 25 MB.',
  unsupported_file_type: 'Only PDF and Word (.docx) files are supported for now.',
  vault_limit_reached: 'Your vault is full. Delete a document to add another.',
  file_rejected: 'This file could not be accepted. It may be empty, damaged or protected.',
  upload_limit_reached: 'You have reached today’s upload limit. Please try again tomorrow.',
  service_paused: 'Scout is paused for today to keep costs in check. New analyses resume after midnight UTC.',
  network_error: 'The upload was interrupted. Check your connection and try again.',
}

/** Friendly message for an upload error `code` from the API (problem+json). */
export function uploadErrorMessage(code?: string | null, reason?: string | null): string {
  if (code === 'file_rejected' && reason === 'empty_file') return 'This file is empty. Check that it has content and upload it again.'
  return (code && ERRORS[code]) || 'The upload failed. Please try again.'
}

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024

/** Client-side check mirroring the server; returns an error code or null. */
export function validateFile(file: { name: string; size: number }): string | null {
  if (!/\.(pdf|docx)$/i.test(file.name)) return 'unsupported_file_type'
  if (file.size > MAX_UPLOAD_BYTES) return 'file_too_large'
  return null
}

export const KIND_LABELS: Record<DocKind, string> = {
  funded_grant: 'Funded grant',
  declined_grant: 'Rejected grant',
  proposal: 'Proposal',
  unpublished_results: 'Unpublished results',
  raw_data_description: 'Raw data',
  lab_notes: 'Lab notes',
  other: 'Other',
}

/** Document kinds in selector order. */
export const KINDS = Object.keys(KIND_LABELS) as DocKind[]

/** "1.2 MB" style size. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
