/** Hooks the api client uses to talk to the auth layer without importing it. */
export interface AuthHooks {
  getToken: () => string | null
  onUnauthorized: (failedToken: string) => void
  /** Called on a 403 `account_deleted`: sign out and show the deletion message. */
  onAccountDeleted?: () => void
}

let hooks: AuthHooks = { getToken: () => null, onUnauthorized: () => undefined }

export function setAuthHooks(h: AuthHooks): void {
  hooks = h
}
export function getAuthHooks(): AuthHooks {
  return hooks
}
