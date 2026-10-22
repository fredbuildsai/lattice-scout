import { Icon } from '../../ui/icons'

/**
 * Warning above the upload zone (DECISIONS 130): Scout cannot yet guarantee the confidentiality of stored
 * documents. Rendered only while the operator keeps `VAULT_UPLOAD_WARNING` on. Not colour-only: icon + bold heading.
 */
export function UploadWarning() {
  return (
    <div role="note" aria-labelledby="upload-warning-title" className="rounded-card border-2 border-warm bg-subtle p-4 text-sm">
      <p id="upload-warning-title" className="flex items-center gap-2 font-semibold text-warm">
        <Icon name="lock" size={16} /> Do not upload confidential material
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-2">
        <li>
          Do not upload confidential material such as grant proposals, unpublished manuscripts under embargo, or anything under an NDA or
          third-party confidentiality.
        </li>
        <li>Scout cannot guarantee the confidentiality of stored documents in this pilot.</li>
        <li>Only upload what you are happy to see stored on our server.</li>
        <li>You can delete any document at any time.</li>
      </ul>
    </div>
  )
}
