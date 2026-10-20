import type { SupabaseClient } from '@supabase/supabase-js'
import { Emitter, type AuthClient, type AuthSession, type EmailSignInResult } from './AuthClient'

/** Supabase Auth: Google OAuth and e-mail magic link. The library is imported lazily. */
export class SupabaseAuthClient implements AuthClient {
  readonly mode = 'supabase' as const
  private client: SupabaseClient | null = null
  private session: AuthSession | null = null
  private emitter = new Emitter()
  private refreshing: Promise<boolean> | null = null
  private lastRefreshedToken: string | null = null

  constructor(private url: string, private key: string) {}

  private toSession(s: { access_token: string; user?: { email?: string } } | null): AuthSession | null {
    return s ? { accessToken: s.access_token, email: s.user?.email } : null
  }

  async init(): Promise<void> {
    const { createClient } = await import('@supabase/supabase-js')
    this.client = createClient(this.url, this.key)
    const { data } = await this.client.auth.getSession()
    this.session = this.toSession(data.session)
    this.client.auth.onAuthStateChange((_event, s) => {
      this.session = this.toSession(s)
      this.emitter.emit(this.session)
    })
  }
  private need(): SupabaseClient {
    if (!this.client) throw new Error('Auth client not initialised')
    return this.client
  }
  getSession(): AuthSession | null {
    return this.session
  }
  async signInWithEmail(email: string): Promise<EmailSignInResult> {
    const { error } = await this.need().auth.signInWithOtp({ email, options: { emailRedirectTo: window.location.origin + '/' } })
    if (error) throw new Error('Could not send the sign-in link. Check the address and try again.')
    return 'link_sent'
  }
  async signInWithGoogle(): Promise<void> {
    const { error } = await this.need().auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin + '/' } })
    if (error) throw new Error('Google sign-in failed. Try again.')
  }
  /** One session refresh before giving up: a token that expired just before the refresh must not log the user out. */
  async handleUnauthorized(failedToken: string): Promise<void> {
    if (this.session && this.session.accessToken !== failedToken && !this.refreshing) return // already renewed
    if (!this.refreshing) {
      this.refreshing = (async () => {
        try {
          if (failedToken === this.lastRefreshedToken) return false // the renewed token was refused too
          const { data, error } = await this.need().auth.refreshSession()
          const next = this.toSession(data.session)
          if (error || !next || next.accessToken === failedToken) return false
          this.lastRefreshedToken = next.accessToken
          this.session = next
          this.emitter.emit(next)
          return true
        } catch {
          return false
        }
      })().finally(() => {
        this.refreshing = null
      })
    }
    if (!(await this.refreshing)) await this.signOut()
  }
  async signOut(): Promise<void> {
    await this.need().auth.signOut()
  }
  subscribe(l: (s: AuthSession | null) => void): () => void {
    return this.emitter.subscribe(l)
  }
}
