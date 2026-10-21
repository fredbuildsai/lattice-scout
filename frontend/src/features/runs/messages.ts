import type { Run } from './api'

/** The 8 pipeline steps in order, with plain labels. */
export const STEP_LABELS: Record<string, { label: string; desc: string }> = {
  plan_queries: { label: 'Plan searches', desc: 'Turning your documents and profile into literature searches' },
  retrieve: { label: 'Find papers', desc: 'Pulling matching papers from OpenAlex' },
  group_by_topic: { label: 'Group by topic', desc: 'Clustering the papers into approaches' },
  coverage: { label: 'Check your coverage', desc: 'Comparing the clusters with your methods and files' },
  measure_novelty: { label: 'Measure novelty', desc: 'Counting existing papers for each candidate idea' },
  analyse: { label: 'Look for opportunities', desc: 'Drafting white spots, synergies and resubmissions' },
  verify: { label: 'Check every claim', desc: 'Dropping anything without a verified source' },
  score_persist: { label: 'Score and save', desc: 'Scoring fit, novelty, readiness and effort' },
}

const FAILURES: Record<string, string> = {
  budget_exhausted: 'This run reached its cost limit before it could finish. Try again with fewer documents.',
  corpus_empty: 'Scout could not find enough papers to compare against. Add more documents or check your profile topics, then try again.',
  profile_missing: 'Scout needs your claimed profile first. Finish “Claim your profile”, then run the analysis again.',
  llm_unavailable: 'Scout’s reasoning service was unavailable. Nothing is lost; please try again in a few minutes.',
  literature_unavailable: 'The literature service (OpenAlex) was unavailable. Please try again in a few minutes.',
  stalled: 'The run stopped making progress and was ended. Please try again.',
  internal_error: 'Something went wrong on our side. Please try again.',
}

/** Plain wording for a failed run's `error_code`. */
export function failureMessage(code: string | null | undefined): string {
  return (code && FAILURES[code]) || FAILURES['internal_error']!
}

/** Why a finished run has nothing to show (empty-state wording). */
export function emptyReason(run: Run | null | undefined): string {
  if (!run) return 'No analysis yet. Pick documents in your vault and ask Scout to analyse them.'
  if (run.status === 'failed') return failureMessage(run.error_code)
  if (run.status === 'cancelled') return 'The last run was cancelled before it found anything. Start a new one from your vault.'
  if (run.status === 'queued' || run.status === 'running') return 'Scout is still working on your first ideas.'
  if (run.counts.candidates > 0 && run.counts.shown === 0)
    return 'Scout drafted some ideas but none had enough verified evidence (at least two checked sources and a measured novelty count), so it shows nothing rather than guess. More documents or a broader profile usually help.'
  return 'Scout did not find a well-supported opportunity in this run. Adding more documents usually helps.'
}
