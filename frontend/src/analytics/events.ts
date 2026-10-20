/**
 * Typed event catalogue. Mirrors backend/src/scout/ports/analytics_catalogue.py;
 * a Vitest drift test fails when the two differ.
 */
export const EVENT_PROPERTIES = {
  page_view: [],
  web_vital: ['metric', 'value', 'rating'],
  upload_started: ['kind'],
  upload_completed: ['document_id', 'kind'],
  upload_failed: ['reason'],
  document_deleted: ['document_id'],
  document_kind_set: ['document_id', 'kind'],
  run_started: ['run_id', 'documents'],
  run_viewed: ['run_id'],
  opportunity_opened: ['opportunity_id', 'position'],
  evidence_marker_opened: ['opportunity_id', 'claim_id'],
  evidence_source_clicked: ['opportunity_id', 'evidence_id', 'source_type'],
  opportunity_saved: ['opportunity_id'],
  opportunity_dismissed: ['opportunity_id'],
  feedback_given: ['target_type', 'target_id', 'rating'],
  match_viewed: ['match_id'],
  match_decision: ['match_id', 'decision'],
  match_revealed: ['match_id'],
  match_rated: ['match_id', 'rating'],
  menu_opened: [],
  menu_item_clicked: ['item'],
  scout_fab_clicked: [],
} as const satisfies Record<string, readonly string[]>

export type EventName = keyof typeof EVENT_PROPERTIES

/** All allowed event names. */
export const EVENT_NAMES = Object.keys(EVENT_PROPERTIES) as EventName[]

type Scalar = string | number | boolean | null

/** Properties allowed for event `N` (keys from the catalogue, scalar values only). */
export type EventProps<N extends EventName> = {
  [K in (typeof EVENT_PROPERTIES)[N][number]]?: Scalar
}
