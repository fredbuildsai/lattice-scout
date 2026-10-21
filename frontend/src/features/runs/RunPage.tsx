import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { track } from '../../analytics/client'
import { Icon } from '../../ui/icons'
import { isRunActive, useCancelRun, useRun, type Run } from './api'
import { failureMessage, STEP_LABELS } from './messages'

const STATUS_LABEL: Record<string, string> = { pending: 'Queued', running: 'Running', done: 'Done', degraded: 'Done, limited' }
const STATUS_TONE: Record<string, string> = {
  pending: 'bg-subtle text-ink-2',
  running: 'bg-scout-50 text-scout-ink',
  done: 'bg-teal-50 text-teal-dark',
  degraded: 'bg-warm-50 text-warm',
}

/** One step row: label, status chip and its own progress bar. */
function StepRow({ step }: { step: Run['steps'][number] }) {
  const info = STEP_LABELS[step.name] ?? { label: step.name, desc: '' }
  const pct = step.status === 'done' || step.status === 'degraded' ? 100 : Math.round(step.progress * 100)
  return (
    <li className={`rounded-card border bg-surface p-3 ${step.status === 'running' ? 'border-scout' : 'border-line'}`}>
      <div className="flex items-center justify-between gap-2">
        <b>{info.label}</b>
        <span className={`rounded-btn px-2 py-0.5 text-xs font-semibold ${STATUS_TONE[step.status] ?? STATUS_TONE['pending']}`}>{STATUS_LABEL[step.status] ?? step.status}</span>
      </div>
      <p className="mt-1 text-sm text-ink-2">{info.desc}</p>
      <div className="mt-2 h-1 rounded-full bg-scout-50" aria-hidden="true">
        <div className={`h-1 rounded-full ${step.status === 'done' ? 'bg-teal' : 'bg-scout'}`} style={{ width: `${pct}%` }} />
      </div>
    </li>
  )
}

/**
 * Analysis run (W4 / M4): headline, progress bar from `percent`, cancel, the 8 steps, a note when a step ran degraded, and a link to the
 * results when done. Polls every 2 s while the run is queued or running. Failed and cancelled runs get plain wording.
 */
export function RunPage() {
  const { id = '' } = useParams()
  const run = useRun(id)
  const cancel = useCancelRun(id)
  useEffect(() => {
    if (id) track('run_viewed', { run_id: id })
  }, [id])
  const r = run.data
  const n = r?.document_ids.length ?? 0
  const active = isRunActive(r?.status)
  const heading = !r ? 'Analysis run' : active ? `Scout is analysing ${n} ${n === 1 ? 'document' : 'documents'}` : r.status === 'done' ? 'Analysis finished' : r.status === 'cancelled' ? 'Analysis cancelled' : 'Analysis did not finish'
  return (
    <section aria-labelledby="page-title" className="mx-auto max-w-[1000px]">
      <p className="text-xs text-ink-3">
        <Link to="/vault" className="underline">
          Vault
        </Link>{' '}
        / Analysis run
      </p>
      <h1 id="page-title" className="mt-2 font-serif text-3xl font-semibold md:text-4xl">
        {heading}
      </h1>
      {run.isPending && <p role="status" className="mt-4 text-ink-3">Loading the run…</p>}
      {run.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load this run.{' '}
          <button type="button" className="underline" onClick={() => void run.refetch()}>
            Try again
          </button>
        </p>
      )}
      {r && (
        <>
          <div className="mt-5 rounded-card border border-line bg-surface p-4 md:p-5">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-scout text-white">
                <Icon name="sparkle" size={18} />
              </span>
              <div className="min-w-0 flex-1">
                {active && <p>You can leave this page. The run continues, and the results will be under Opportunities when it is done.</p>}
                {r.status === 'done' && (
                  <p>
                    Scout showed <b>{r.counts.shown}</b> {r.counts.shown === 1 ? 'opportunity' : 'opportunities'} after checking {r.counts.corpus} papers in {r.counts.clusters} topic groups.
                  </p>
                )}
                {r.status === 'cancelled' && <p>You stopped this run. Nothing was added to your opportunities.</p>}
                {r.status === 'failed' && <p role="alert">{failureMessage(r.error_code)}</p>}
              </div>
              {active && (
                <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
                  Cancel run
                </button>
              )}
            </div>
            <div
              role="progressbar"
              aria-label="Analysis progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={r.percent}
              aria-valuetext={`${r.percent}%`}
              className="mt-4 h-2 rounded-full bg-scout-50"
            >
              <div className="h-2 rounded-full bg-scout transition-[width]" style={{ width: `${r.percent}%` }} />
            </div>
            <p className="mt-1 text-xs text-ink-3">{r.percent}% complete</p>
            {cancel.isError && <p role="alert" className="mt-2 text-sm text-warm">We could not cancel the run. It may have just ended.</p>}
            {r.status === 'done' && (
              <Link to={`/opportunities?run=${r.id}`} className="mt-3 inline-flex min-h-11 items-center rounded-btn bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-dark">
                See the opportunities
              </Link>
            )}
            {(r.status === 'failed' || r.status === 'cancelled') && (
              <Link to="/vault" className="mt-3 inline-flex min-h-11 items-center font-semibold text-accent hover:underline">
                Back to the vault
              </Link>
            )}
          </div>

          {r.degraded.length > 0 && (
            <div role="note" className="mt-4 rounded-card border border-warm-mid/40 bg-warm-50 p-3 text-sm text-warm">
              Some steps ran with limited data, so results may be thinner than usual:
              <ul className="mt-1 list-disc pl-5">
                {r.degraded.map((d, i) => (
                  <li key={i}>
                    {STEP_LABELS[d.step]?.label ?? d.step}: {d.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <h2 className="mt-8 text-lg font-semibold">Steps</h2>
          <ol aria-label="Analysis steps" className="mt-3 grid gap-3 md:grid-cols-2">
            {r.steps.map((s) => (
              <StepRow key={s.name} step={s} />
            ))}
          </ol>
        </>
      )}
    </section>
  )
}
