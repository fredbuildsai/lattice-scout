/** Session held by the browser. */
export interface AuthSession {
  accessToken: string
  email?: string
}

export type AuthMode = 'dev' | 'supabase'

/** Result of an e-mail sign-in: dev signs in directly, Supabase sends a magic link. */
export type EmailSignInResult = 'signed_in' | 'link_sent'

/** Abstraction over the identity provider. */
export interface AuthClient {
  readonly mode: AuthMode
  /** Restore the stored session. Must be awaited before `getSession`. */
  init(): Promise<void>
  getSession(): AuthSession | null
  signInWithEmail(email: string): Promise<EmailSignInResult>
  signInWithGoogle(): Promise<void>
  signOut(): Promise<void>
  /**
   * Called when the API answered 401 for `failedToken`. Optional: clients that can renew the
   * session try that once; the default is `signOut`.
   */
  handleUnauthorized?(failedToken: string): Promise<void>
  /** Listen for session changes; returns the unsubscribe function. */
  subscribe(listener: (session: AuthSession | null) => void): () => void
}

export const SESSION_STORAGE_KEY = 'scout.session'

/** Read a JSON session from localStorage; never throws. */
export function loadStoredSession(now = Date.now()): AuthSession | null {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as { accessToken?: unknown; email?: unknown; expiresAt?: unknown }
    if (typeof v.accessToken !== 'string' || !v.accessToken) return null
    if (typeof v.expiresAt === 'number' && v.expiresAt <= now) return null
    return { accessToken: v.accessToken, email: typeof v.email === 'string' ? v.email : undefined }
  } catch {
    return null
  }
}

/** Persist (or clear, with null) the session; storage failures are ignored. */
export function storeSession(session: (AuthSession & { expiresAt?: number }) | null): void {
  try {
    if (session) localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
    else localStorage.removeItem(SESSION_STORAGE_KEY)
  } catch {
    /* storage unavailable (private mode): session lives in memory only */
  }
}

/** Small listener set shared by the clients. */
export class Emitter {
  private listeners = new Set<(s: AuthSession | null) => void>()
  subscribe(l: (s: AuthSession | null) => void): () => void {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  }
  emit(s: AuthSession | null): void {
    this.listeners.forEach((l) => l(s))
  }
}
