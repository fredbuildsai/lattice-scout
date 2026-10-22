import { describe, expect, it } from 'vitest'
import { clearSession, SESSION_TIMEOUT_MS, touchSession, uuidv7 } from './session'

describe('session', () => {
  it('clearSession (sign-out, account deletion) makes the next touch a new session', () => {
    sessionStorage.clear()
    const a = touchSession(5)
    clearSession()
    const b = touchSession(6)
    expect(b.rotated).toBe(true)
    expect(b.id).not.toBe(a.id)
  })

  it('keeps the id within 30 minutes and rotates after', () => {
    sessionStorage.clear()
    const a = touchSession(1_000_000)
    expect(a.rotated).toBe(true)
    const b = touchSession(1_000_000 + SESSION_TIMEOUT_MS)
    expect(b).toEqual({ id: a.id, rotated: false })
    const c = touchSession(1_000_000 + 2 * SESSION_TIMEOUT_MS + 1)
    expect(c.rotated).toBe(true)
    expect(c.id).not.toBe(a.id)
  })

  it('activity extends the session', () => {
    sessionStorage.clear()
    const a = touchSession(0)
    touchSession(SESSION_TIMEOUT_MS - 1)
    expect(touchSession(2 * SESSION_TIMEOUT_MS - 2).id).toBe(a.id)
  })

  it('produces UUID v7', () => {
    expect(uuidv7()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
})
