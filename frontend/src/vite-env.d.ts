/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_ANALYTICS_ENABLED?: string
  readonly VITE_AUTH_MODE?: string
  readonly VITE_SUPABASE_URL?: string
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string
}
