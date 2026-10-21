import { Link } from "react-router-dom";
import { Icon } from "../../ui/icons";
import { OpportunityCard } from "../opportunities/OpportunityCard";
import { Claim } from "../opportunities/Claim";
import { useOpportunities } from "../opportunities/api";
import { kindInfo } from "../opportunities/kinds";
import { latestDone, useRuns, type Run } from "../runs/api";
import { useDocuments } from "../vault/api";
import { KIND_LABELS, type DocKind } from "../vault/status";
import { useMatches } from "../matches/api";
import { useProfile } from "./api";

function Card({
  title,
  link,
  children,
}: {
  title: string;
  link?: { to: string; label: string };
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="rounded-card border border-line bg-surface p-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{title}</h2>
        {link && (
          <Link
            to={link.to}
            className="text-sm font-semibold text-accent hover:underline"
          >
            {link.label}
          </Link>
        )}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function briefing(run: Run | undefined): string {
  if (!run)
    return "I have not analysed anything yet. Add a few documents to your vault and ask me to analyse them; I will look for white spots, synergies and proposals worth resubmitting.";
  if (run.status === "queued" || run.status === "running")
    return `I am analysing your documents now (${run.percent}% done). The first ideas appear here as soon as every citation is checked.`;
  if (run.status === "failed")
    return "My last analysis did not finish. Nothing is lost; you can start it again from your vault.";
  if (run.status === "cancelled")
    return "You stopped the last analysis. Start a new one from your vault whenever you like.";
  const n = run.counts.shown;
  return `I checked ${run.counts.corpus} papers against your ${run.document_ids.length} ${run.document_ids.length === 1 ? "document" : "documents"} and found ${n} ${n === 1 ? "opportunity" : "opportunities"} with verified sources.`;
}

/**
 * Home (W2 / M2): greeting, Scout's briefing from the latest run, top 3 opportunities, the matches teaser
 * (empty until matching exists) and a vault summary by document kind.
 */
export function HomePage() {
  const profile = useProfile();
  const runs = useRuns();
  const docs = useDocuments();
  const run = latestDone(runs.data?.items);
  const latest = runs.data?.items[0];
  const matches = useMatches();
  const awaiting = (matches.data?.items ?? []).filter(
    (m) => m.status === "awaiting_you",
  ).length;
  const top = useOpportunities({ runId: run?.id, limit: 3, enabled: !!run });
  const cards = (top.data?.items ?? []).slice(0, 3);
  const p = profile.data;
  const first = p?.display_name.split(" ")[0];
  const items = docs.data?.items ?? [];
  const byKind = items.reduce<Record<string, number>>(
    (acc, d) => ({
      ...acc,
      [d.kind ?? "other"]: (acc[d.kind ?? "other"] ?? 0) + 1,
    }),
    {},
  );
  const date = new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return (
    <section aria-labelledby="page-title">
      <p className="text-sm text-ink-3">{date}</p>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-3">
        <h1
          id="page-title"
          className="font-serif text-3xl font-semibold md:text-4xl"
        >
          {first ? `Hello, ${first}` : "Home"}
        </h1>
        <Link
          to="/vault"
          className="inline-flex min-h-11 items-center gap-2 rounded-btn bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-dark"
        >
          <Icon name="upload" size={16} /> Upload documents
        </Link>
      </div>
      {profile.isError && (
        <p role="alert" className="mt-3 text-warm">
          We could not load your profile.{" "}
          <button
            type="button"
            className="underline"
            onClick={() => void profile.refetch()}
          >
            Try again
          </button>
        </p>
      )}
      {p && p.import.status !== "done" && (
        <p role="status" className="mt-3 text-sm text-ink-3">
          {p.import.status === "failed" ? (
            <>
              The last import failed.{" "}
              <Link
                to="/onboarding?step=works"
                className="font-semibold text-accent underline"
              >
                Retry it
              </Link>
            </>
          ) : (
            "Still importing your works…"
          )}
        </p>
      )}

      <section
        aria-label="Scout’s briefing"
        className="mt-6 rounded-card border border-scout-50 bg-scout-100 p-4 md:p-5"
      >
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-scout text-white">
            <Icon name="sparkle" size={18} />
          </span>
          <div className="min-w-0">
            <p className="font-mono text-[11px] uppercase tracking-wide text-scout-ink">
              Scout’s briefing
            </p>
            {runs.isPending ? (
              <p role="status" className="mt-1 text-ink-3">
                Loading…
              </p>
            ) : (
              <p className="mt-1 font-serif text-lg leading-relaxed">
                {briefing(latest)}
              </p>
            )}
            {runs.isError && (
              <p role="alert" className="text-warm">
                We could not load your runs.
              </p>
            )}
            {cards[0]?.why && (
              <p className="mt-2 text-sm text-ink-2">
                Top idea:{" "}
                <Claim claim={cards[0].why} opportunityId={cards[0].id} />
              </p>
            )}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {cards[0] && (
            <Link
              to={`/opportunities/${cards[0].id}`}
              className="inline-flex min-h-11 items-center rounded-btn bg-scout px-4 text-sm font-semibold text-white hover:bg-scout-ink"
            >
              See the top {kindInfo(cards[0].kind).label.toLowerCase()}
            </Link>
          )}
          {latest &&
            (latest.status === "queued" || latest.status === "running") && (
              <Link
                to={`/runs/${latest.id}`}
                className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold"
              >
                Watch progress
              </Link>
            )}
          {!latest && (
            <Link
              to="/vault"
              className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold"
            >
              Go to the vault
            </Link>
          )}
        </div>
      </section>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0">
          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">New opportunities</h2>
            {cards.length > 0 && (
              <Link
                to="/opportunities"
                className="text-sm font-semibold text-accent hover:underline"
              >
                See all
              </Link>
            )}
          </div>
          {top.isPending && !!run && (
            <p role="status" className="mt-3 text-ink-3">
              Loading opportunities…
            </p>
          )}
          {top.isError && (
            <p role="alert" className="mt-3 text-warm">
              We could not load your opportunities.
            </p>
          )}
          {cards.length === 0 && !top.isPending && (
            <p className="mt-3 text-ink-2">
              No opportunities yet. They appear here after an analysis.
            </p>
          )}
          {cards.length > 0 && (
            <ul aria-label="Top opportunities" className="mt-3 space-y-4">
              {cards.map((c, i) => (
                <OpportunityCard key={c.id} card={c} position={i + 1} compact />
              ))}
            </ul>
          )}
        </div>
        <div className="space-y-5">
          <Card
            title="Matches"
            link={
              matches.data?.matching_enabled
                ? { to: "/matches", label: "Open" }
                : undefined
            }
          >
            <p className="text-sm text-ink-2">
              {matches.isPending
                ? "Loading…"
                : matches.isError
                  ? "Open Matches to see your proposals."
                  : !matches.data?.matching_enabled
                    ? "Turn on matching to meet complementary researchers."
                    : awaiting > 0
                      ? `${awaiting} ${awaiting === 1 ? "match is" : "matches are"} waiting for your answer.`
                      : "No matches waiting for you."}
            </p>
          </Card>
          <Card
            title="Private vault"
            link={{
              to: "/vault",
              label: `${items.length} ${items.length === 1 ? "file" : "files"}`,
            }}
          >
            {docs.isPending && (
              <p role="status" className="text-sm text-ink-3">
                Loading…
              </p>
            )}
            {docs.isError && (
              <p role="alert" className="text-sm text-warm">
                We could not load your vault.
              </p>
            )}
            {docs.data && items.length === 0 && (
              <p className="text-sm text-ink-2">Nothing here yet.</p>
            )}
            <ul className="divide-y divide-line text-sm">
              {Object.entries(byKind).map(([k, n]) => (
                <li key={k} className="flex justify-between py-2">
                  <span>{KIND_LABELS[k as DocKind] ?? k}</span>
                  <b>{n}</b>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-scout-ink">Visible only to you</p>
          </Card>
        </div>
      </div>
    </section>
  );
}
