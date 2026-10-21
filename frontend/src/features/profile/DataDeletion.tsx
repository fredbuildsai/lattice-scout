import { useState } from 'react'
import { ApiError } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { TypedConfirmDialog } from '../../ui/TypedConfirmDialog'
import { useDeleteAllDocuments } from '../vault/api'
import { useDeleteAccount } from './api'

/** Wording for a failed account deletion: 422 `confirmation_mismatch` is shown inline. */
export function deleteAccountError(e: unknown): string {
  if (e instanceof ApiError && e.code === 'confirmation_mismatch') return 'That e-mail does not match your account. Check it and try again.'
  return 'We could not delete your account. Please try again.'
}

/**
 * Privacy and data controls (W10 / M12): the no-training statement, what deletion removes and keeps,
 * "Delete my vault" (type DELETE) and "Delete my account" (retype your e-mail).
 */
export function DataDeletion({ email }: { email: string }) {
  const [dialog, setDialog] = useState<'vault' | 'account' | null>(null)
  const [deletedCount, setDeletedCount] = useState<number | null>(null)
  const delAll = useDeleteAllDocuments()
  const delAccount = useDeleteAccount()
  const { accountDeleted } = useAuth()
  const close = () => {
    setDialog(null)
    delAll.reset()
    delAccount.reset()
  }
  return (
    <div className="mt-8 space-y-4">
      <h2 className="text-lg font-semibold">Your data</h2>
      <p className="font-semibold">Scout does not train models on your files.</p>
      <p className="text-sm text-ink-2">
        Do not upload confidential material: Scout cannot guarantee the confidentiality of stored documents in this pilot.
        Text from your documents goes only to AI providers the operator has approved for confidential data, and
        never to any other provider. If no provider is approved, Scout reads your files on its server but does not send them
        to any AI model, and your analyses and matches use your published work only; they say so. The providers have
        their own terms; ask the operator which ones are approved.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-card border border-line bg-surface p-4">
          <h3 className="text-sm font-bold">Deleting removes</h3>
          <ul className="mt-1 list-disc pl-5 text-sm text-ink-2">
            <li>Your uploaded files and the text Scout extracted</li>
            <li>The insights and quotes taken from them</li>
            <li>For an account: your profile, matches and sign-in</li>
          </ul>
        </div>
        <div className="rounded-card border border-line bg-surface p-4">
          <h3 className="text-sm font-bold">What stays</h3>
          <ul className="mt-1 list-disc pl-5 text-sm text-ink-2">
            <li>Cost records without your identity, kept so we can track spend</li>
            <li>
              Ratings and comments you gave on a match stay visible to the other person (a comment may name you)
            </li>
            <li>Short paraphrases (up to 300 characters) in ideas already shown, after you delete only your vault</li>
            <li>Region and career band on a closed match proposal, for 30 days</li>
            <li>Server logs and the sign-in provider&apos;s own audit log, which are outside our database</li>
          </ul>
        </div>
      </div>

      {deletedCount !== null && (
        <p role="status" className="text-sm text-teal-dark">
          {deletedCount} {deletedCount === 1 ? 'document is' : 'documents are'} being deleted.
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <button type="button" onClick={() => setDialog('vault')} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold text-warm hover:bg-warm-50">
          Delete my vault
        </button>
        <button type="button" onClick={() => setDialog('account')} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold text-warm hover:bg-warm-50">
          Delete my account
        </button>
      </div>

      {dialog === 'vault' && (
        <TypedConfirmDialog
          title="Delete your whole vault?"
          expected="DELETE"
          inputLabel="Type DELETE to confirm"
          confirmLabel="Delete my vault"
          busy={delAll.isPending}
          error={delAll.isError ? 'We could not delete your vault. Please try again.' : null}
          onCancel={close}
          onConfirm={() =>
            delAll.mutate(undefined, {
              onSuccess: (r) => {
                setDeletedCount(r.deleting)
                close()
              },
            })
          }
        >
          This removes every document, its extracted text, insights and quotes, and any stored originals. It cannot be undone.
        </TypedConfirmDialog>
      )}
      {dialog === 'account' && (
        <TypedConfirmDialog
          title="Delete your account?"
          expected={email}
          inputLabel="Retype your e-mail to confirm"
          confirmLabel="Delete my account"
          busy={delAccount.isPending}
          error={delAccount.isError ? deleteAccountError(delAccount.error) : null}
          onCancel={close}
          onConfirm={(typed) => delAccount.mutate(typed, { onSuccess: () => accountDeleted() })}
        >
          This removes your profile, files, opportunities and matches, and signs you out. It cannot be undone.
        </TypedConfirmDialog>
      )}
    </div>
  )
}
