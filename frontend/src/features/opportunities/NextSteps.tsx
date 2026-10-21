import { useState } from 'react'

const key = (id: string) => `scout.nextsteps.${id}`

function load(id: string): number[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(key(id)) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is number => typeof x === 'number') : []
  } catch {
    return []
  }
}

function save(id: string, done: number[]) {
  try {
    localStorage.setItem(key(id), JSON.stringify(done))
  } catch {
    /* storage unavailable: state stays in memory */
  }
}

/** Next steps as a checklist. Ticks live in the browser only (localStorage per opportunity id); storage errors are ignored. */
export function NextSteps({ opportunityId, steps }: { opportunityId: string; steps: string[] }) {
  const [done, setDone] = useState<number[]>(() => load(opportunityId))
  const toggle = (i: number) => {
    const next = done.includes(i) ? done.filter((d) => d !== i) : [...done, i]
    setDone(next)
    save(opportunityId, next)
  }
  return (
    <ul className="space-y-2">
      {steps.map((s, i) => (
        <li key={i} className="rounded-card border border-line">
          <label className="flex min-h-11 cursor-pointer items-start gap-3 p-3 text-sm">
            <input type="checkbox" checked={done.includes(i)} onChange={() => toggle(i)} className="mt-0.5 h-5 w-5 accent-accent" />
            <span className={done.includes(i) ? 'text-ink-3 line-through' : ''}>{s}</span>
          </label>
        </li>
      ))}
    </ul>
  )
}
