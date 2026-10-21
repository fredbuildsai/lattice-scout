# E-mail templates (human step)

Scout sends its own mails (run finished, suggestion, mutual match, weekly digest) through `EMAIL_PROVIDER`. The sign-in link mail is sent by **Supabase Auth**, so its branding is set in the Supabase dashboard (Authentication, Email Templates, Magic Link). Paste the text below and set the sender name to "Scout".

Subject: `Your Scout sign-in link`

```text
Hello,

Use this link to sign in to Scout. It works once and expires soon.

{{ .ConfirmationURL }}

If you did not ask for it, ignore this mail.
```

Checklist before the pilot:

1. Set `EMAIL_PROVIDER=resend`, `EMAIL_API_KEY`, `EMAIL_FROM` (a verified sender domain) and `APP_BASE_URL`.
2. Send yourself one test mail of each kind from a staging account. Nothing is sent to real people before you do this.
3. Configure SPF and DKIM for the sender domain at the provider.
