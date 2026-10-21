import { API_URL } from '../api/config'
import { Emitter, loadStoredSession, storeSession, type AuthClient, type AuthSession, type EmailSignInResult } from './AuthClient'

/** Development sign-in: the e-mail is exchanged for a token by `POST /api/v1/dev/login`. */
export class DevAuthClient implements AuthClient {
  readonly mode = 'dev' as const
  private session: AuthSession | null = null
  private emitter = new Emitter()

  constructor(private fetchImpl: typeof fetch = (...a) => fetch(...a), private baseUrl = API_URL) {}

  async init(): Promise<void> {
    this.session = loadStoredSession()
  }
  getSession(): AuthSession | null {
    return this.session
  }
  async signInWithEmail(email: string): Promise<EmailSignInResult> {
    const res = await this.fetchImpl(`${this.baseUrl}/api/v1/dev/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    })
    if (!res.ok) throw new Error(res.status === 422 ? 'Enter a valid e-mail address.' : 'Sign-in failed. Try again.')
    const body = (await res.json()) as { access_token: string; expires_in?: number }
    const expiresAt = body.expires_in ? Date.now() + body.expires_in * 1000 : undefined
    this.session = { accessToken: body.access_token, email }
    storeSession({ ...this.session, expiresAt })
    this.emitter.emit(this.session)
    return 'signed_in'
  }
  async signInWithGoogle(): Promise<void> {
    throw new Error('Google sign-in is not available in dev mode.')
  }
  async signOut(): Promise<void> {
    this.session = null
    storeSession(null)
    this.emitter.emit(null)
  }
  subscribe(l: (s: AuthSession | null) => void): () => void {
    return this.emitter.subscribe(l)
  }
}
