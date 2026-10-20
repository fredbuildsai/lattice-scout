import { useState, type FormEvent } from 'react'
import { useAuth } from '../../auth/AuthProvider'
import { Icon, LogoMark } from '../../ui/icons'

const FEATURES: Array<{ icon: 'lock' | 'sparkle' | 'people'; tone: string; title: string; body: string }> = [
  { icon: 'lock', tone: 'bg-accent-50 text-accent', title: 'A private vault for what never got published', body: 'Word, PDF, spreadsheets, slides, notebook photos. Never shared, never used to train models.' },
  { icon: 'sparkle', tone: 'bg-scout-50 text-scout', title: 'An AI research scout with a team of sub-agents', body: 'Reads your files, maps the field, finds white spots and synergies with your published work. Every idea cites its sources.' },
  { icon: 'people', tone: 'bg-teal-50 text-teal', title: 'Matches with complementary researchers', body: 'Anonymous until you both say yes. Only then are contacts and joint opportunities revealed.' },
]

/**
 * W0 / M0 sign-in page: Google and e-mail (no ORCID button in the MVP).
 * In dev mode the e-mail form logs in directly; with Supabase it sends a magic link.
 */
export function SignInPage() {
  const { client } = useAuth()
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in failed. Try again.')
    } finally {
      setBusy(false)
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    void run(async () => {
      if ((await client.signInWithEmail(email.trim())) === 'link_sent') setSent(true)
    })
  }

  return (
    <div className="grid min-h-screen bg-surface font-sans text-ink md:grid-cols-[1fr_1fr]">
      <section className="flex flex-col px-5 py-8 md:px-14 md:py-12">
        <div className="flex items-center gap-2">
          <LogoMark size={28} />
          <b className="text-lg">Lattice Scout</b>
          <span className="hidden rounded-btn border border-line px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-3 md:inline">by Lattice Press</span>
        </div>
        <div className="my-8 md:my-auto">
          <h1 className="max-w-[560px] font-serif text-4xl font-semibold leading-tight md:text-5xl">Find the paper hiding in your drawer.</h1>
          <p className="mt-5 max-w-[560px] text-lg text-ink-2">
            Upload unpublished results, funded and rejected grants, and proposals. Scout reads them against the literature and your lab&apos;s papers, and shows you what&apos;s worth doing next — and who could do it with you.
          </p>
          <ul className="mt-8 hidden max-w-[560px] space-y-5 md:block">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-4">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${f.tone}`}>
                  <Icon name={f.icon} size={18} />
                </span>
                <span>
                  <b className="block text-sm">{f.title}</b>
                  <span className="text-sm text-ink-3">{f.body}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="hidden text-xs text-ink-3 md:block">Your profile starts filled in from OpenAlex. You confirm what&apos;s yours.</p>
      </section>
      <section className="flex items-start justify-center bg-bg px-5 py-10 md:items-center md:border-l md:border-line">
        <div className="w-full max-w-[420px] rounded-card border border-line bg-surface p-6 md:p-8">
          <h2 className="text-center text-xl font-semibold">Sign in or create an account</h2>
          {client.mode === 'supabase' && (
            <>
              <button type="button" disabled={busy} onClick={() => void run(() => client.signInWithGoogle())} className="mt-6 flex h-12 w-full items-center justify-center rounded-btn border border-line-strong bg-surface font-semibold hover:bg-subtle">
                Continue with Google
              </button>
              <div className="my-5 flex items-center gap-3 text-xs text-ink-3">
                <span className="h-px flex-1 bg-line" />
                or with email
                <span className="h-px flex-1 bg-line" />
              </div>
            </>
          )}
          {sent ? (
            <p role="status" className="mt-6 text-ink-2">
              Check your inbox: we sent a sign-in link to <b>{email}</b>.
            </p>
          ) : (
            <form onSubmit={submit} className={client.mode === 'dev' ? 'mt-6' : ''}>
              <label htmlFor="email" className="block text-sm font-semibold">
                Work email
              </label>
              <input id="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@university.edu" className="mt-2 h-12 w-full rounded-btn border border-line-strong px-3" />
              {error && (
                <p role="alert" className="mt-2 text-sm text-warm">
                  {error}
                </p>
              )}
              <button type="submit" disabled={busy} className="mt-4 h-12 w-full rounded-btn bg-accent font-semibold text-white hover:bg-accent-dark disabled:opacity-60">
                {busy ? 'Signing in…' : 'Continue'}
              </button>
            </form>
          )}
          {client.mode === 'dev' && <p className="mt-4 text-center text-xs text-ink-3">Development sign-in: no password needed.</p>}
          <p className="mt-4 text-center text-xs text-ink-3">By continuing you accept the Terms and Privacy notice.</p>
        </div>
      </section>
    </div>
  )
}
