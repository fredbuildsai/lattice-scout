import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { EVENT_NAMES, EVENT_PROPERTIES } from './events'

/** Parses EVENT_CATALOGUE out of the backend Python file. */
function backendCatalogue(): Record<string, string[]> {
  const src = readFileSync(resolve(__dirname, '../../../backend/src/scout/ports/analytics_catalogue.py'), 'utf8')
  const body = src.slice(src.indexOf('EVENT_CATALOGUE'), src.indexOf('def validate_event'))
  const out: Record<string, string[]> = {}
  for (const m of body.matchAll(/"(\w+)":\s*frozenset\(([^)]*)\)/g)) {
    out[m[1]!] = [...(m[2] ?? '').matchAll(/"(\w+)"/g)].map((x) => x[1]!).sort()
  }
  return out
}

describe('event catalogue drift', () => {
  it('has the same names and property keys as the backend', () => {
    const be = backendCatalogue()
    expect(Object.keys(be).length).toBeGreaterThan(10)
    expect([...EVENT_NAMES].sort()).toEqual(Object.keys(be).sort())
    for (const n of EVENT_NAMES) expect([...EVENT_PROPERTIES[n]].sort()).toEqual(be[n])
  })
})
