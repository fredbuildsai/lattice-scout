/** Analytics session: UUID in sessionStorage, rotated after 30 minutes of inactivity. */

export const SESSION_TIMEOUT_MS = 30 * 60 * 1000
const KEY = 'scout.session'

export interface SessionState {
  id: string
  lastActivity: number
}

/** UUID v7 (time-ordered). */
export function uuidv7(now: number = Date.now()): string {
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  const t = BigInt(now)
  for (let i = 0; i < 6; i++) b[i] = Number((t >> BigInt(8 * (5 - i))) & 0xffn)
  b[6] = (b[6]! & 0x0f) | 0x70
  b[8] = (b[8]! & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

function read(storage: Storage): SessionState | null {
  try {
    const raw = storage.getItem(KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<SessionState>
    if (typeof v.id === 'string' && typeof v.lastActivity === 'number') return { id: v.id, lastActivity: v.lastActivity }
  } catch {
    /* storage unavailable or corrupt: start a new session */
  }
  return null
}

/**
 * Returns the current session id and records activity at `now`.
 * `rotated` is true when a new id was created (first load or after inactivity).
 */
export function touchSession(
  now: number = Date.now(),
  storage: Storage = sessionStorage,
): { id: string; rotated: boolean } {
  const cur = read(storage)
  const rotated = !cur || now - cur.lastActivity > SESSION_TIMEOUT_MS
  const id = rotated ? uuidv7(now) : cur!.id
  try {
    storage.setItem(KEY, JSON.stringify({ id, lastActivity: now } satisfies SessionState))
  } catch {
    /* ignore */
  }
  return { id, rotated }
}

/** Current session id without recording activity (creates one if missing). */
export function peekSessionId(storage: Storage = sessionStorage): string {
  return read(storage)?.id ?? touchSession(Date.now(), storage).id
}
