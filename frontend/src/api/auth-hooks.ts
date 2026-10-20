/** Hooks the api client uses to talk to the auth layer without importing it. */
export interface AuthHooks {
  getToken: () => string | null
  onUnauthorized: () => void
}

let hooks: AuthHooks = { getToken: () => null, onUnauthorized: () => undefined }

export function setAuthHooks(h: AuthHooks): void {
  hooks = h
}
export function getAuthHooks(): AuthHooks {
  return hooks
}
