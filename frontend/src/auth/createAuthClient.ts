import type { AuthClient } from './AuthClient'
import { DevAuthClient } from './DevAuthClient'
import { SupabaseAuthClient } from './SupabaseAuthClient'

export interface AuthEnv {
  VITE_AUTH_MODE?: string
  VITE_SUPABASE_URL?: string
  VITE_SUPABASE_PUBLISHABLE_KEY?: string
}

/** Select the client from env: `supabase` needs URL and publishable key, anything else is dev. */
export function createAuthClient(env: AuthEnv = import.meta.env): AuthClient {
  if (env.VITE_AUTH_MODE === 'supabase') {
    if (!env.VITE_SUPABASE_URL || !env.VITE_SUPABASE_PUBLISHABLE_KEY) {
      throw new Error('VITE_AUTH_MODE=supabase requires VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY')
    }
    return new SupabaseAuthClient(env.VITE_SUPABASE_URL, env.VITE_SUPABASE_PUBLISHABLE_KEY)
  }
  return new DevAuthClient()
}
