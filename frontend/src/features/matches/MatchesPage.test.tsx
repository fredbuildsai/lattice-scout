import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as analytics from "../../analytics/client";
import { ME_WITH_RESEARCHER } from "../../test-utils";
import { anon, revealed } from "./fixtures";
import { MatchesPage } from "./MatchesPage";
import type { MatchCard } from "./api";

interface Opts {
  enabled?: boolean;
  items?: MatchCard[];
  feedback?: unknown;
  decisionResponse?: { status: number; body: unknown };
}
function setup({
  enabled = true,
  items = [],
  feedback = null,
  decisionResponse,
}: Opts = {}) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const patches: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        return json({ ...ME_WITH_RESEARCHER, consent_matching: true });
      }
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        if (url.includes("/rating"))
          return json({
            target_type: "match",
            target_id: "m2",
            rating: null,
            stars: 4,
            reasons: [],
            comment: null,
            updated_at: "x",
          });
        return decisionResponse
          ? json(decisionResponse.body, decisionResponse.status)
          : json(anon({ status: "waiting_on_them" }));
      }
      if (url.includes("/api/v1/matches"))
        return json({ matching_enabled: enabled, items, next_cursor: null });
      if (url.includes("/api/v1/feedback"))
        return feedback ? json(feedback) : new Response("{}", { status: 404 });
      if (url.includes("/api/v1/me")) return json(ME_WITH_RESEARCHER);
      return new Response("{}", { status: 404 });
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <MatchesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { posts, patches };
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MatchesPage", () => {
  it("shows an awaiting card with percentage and accepts", async () => {
    const track = vi
      .spyOn(analytics, "track")
      .mockImplementation(() => undefined);
    const { posts } = setup({ items: [anon()] });
    const user = userEvent.setup();
    expect(await screen.findByText("88%")).toBeInTheDocument();
    expect(screen.getByText("Anonymous match")).toBeInTheDocument();
    expect(screen.getByText("Europe")).toBeInTheDocument();
    expect(screen.getByText("Iron-oxide catalysts")).toBeInTheDocument();
    expect(track).toHaveBeenCalledWith("match_viewed", { match_id: "m1" });
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: "accept",
        never_similar: false,
      }),
    );
    expect(track).toHaveBeenCalledWith("match_decision", {
      match_id: "m1",
      decision: "accept",
    });
  });

  it("confirms a decline and sends never_similar", async () => {
    const { posts } = setup({ items: [anon()] });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Decline" }));
    expect(posts).toHaveLength(0);
    await user.click(screen.getByLabelText("Never suggest this person again"));
    await user.click(screen.getByRole("button", { name: "Confirm decline" }));
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: "decline",
        never_similar: true,
      }),
    );
  });

  it("shows the waiting state with a withdraw button", async () => {
    const { posts } = setup({
      items: [anon({ status: "waiting_on_them", my_decision: "accept" })],
    });
    const user = userEvent.setup();
    expect(
      await screen.findByText(/Waiting for the other researcher/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Withdraw" }));
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: "withdraw",
        never_similar: false,
      }),
    );
  });

  it("uses neutral wording for declined and expired cards", async () => {
    setup({
      items: [
        anon({ id: "a", status: "declined_by_you", my_decision: "decline" }),
        anon({ id: "b", status: "expired" }),
      ],
    });
    await screen.findByText(/You passed on this proposal/);
    expect(screen.getByText(/expired on/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(
      /they declined|declined you|rejected you|turned you down/i,
    );
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("shows the server detail when a decision conflicts", async () => {
    setup({
      items: [anon()],
      decisionResponse: {
        status: 409,
        body: { detail: "This proposal has expired." },
      },
    });
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This proposal has expired.",
    );
  });

  it("renders a revealed card with contact links, ideas and rating prefill", async () => {
    const { posts } = setup({
      items: [revealed()],
      feedback: {
        target_type: "match",
        target_id: "m2",
        rating: null,
        stars: 3,
        reasons: ["methods_fit"],
        comment: "nice",
        updated_at: "x",
      },
    });
    const user = userEvent.setup();
    expect(await screen.findByText("Ada Example")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "ada@example.edu" }),
    ).toHaveAttribute("href", "mailto:ada@example.edu");
    expect(screen.getByRole("link", { name: /ORCID 0000/ })).toHaveAttribute(
      "href",
      "https://orcid.org/0000-0001-6636-725X",
    );
    expect(screen.getByRole("link", { name: /OpenAlex A123/ })).toHaveAttribute(
      "href",
      "https://openalex.org/A123",
    );
    const ideas = screen.getByRole("list", { name: "Joint opportunities" });
    expect(within(ideas).getByText("Capture and destroy")).toBeInTheDocument();
    expect(within(ideas).getByText("Can it regenerate?")).toBeInTheDocument();
    await vi.waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Methods fit" }),
      ).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByLabelText("Anything else?")).toHaveValue("nice");
    expect(screen.getByRole("button", { name: "3 stars" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "4 stars" }));
    await user.click(screen.getByRole("button", { name: "Shared timeline" }));
    await user.click(screen.getByRole("button", { name: "Send feedback" }));
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        stars: 4,
        reasons: ["methods_fit", "shared_timeline"],
        comment: "nice",
      }),
    );
    expect(posts[0]?.url).toContain("/matches/m2/rating");
  });

  it("shows pending and failed idea states", async () => {
    setup({
      items: [
        revealed({ id: "p", ideas_status: "pending", joint_ideas: [] }),
        revealed({ id: "f", ideas_status: "failed", joint_ideas: [] }),
      ],
    });
    expect(
      await screen.findByText(/Ideas are being prepared/),
    ).toBeInTheDocument();
    expect(screen.getByText(/could not write joint ideas/)).toBeInTheDocument();
  });

  it("offers the opt-in when matching is off and PATCHes consent", async () => {
    const { patches } = setup({ enabled: false });
    await userEvent
      .setup()
      .click(
        await screen.findByRole("button", {
          name: /Suggest me to complementary/,
        }),
      );
    await vi.waitFor(() =>
      expect(patches).toEqual([{ consent_matching: true }]),
    );
  });

  it("shows the invite message when enabled but empty", async () => {
    setup({ items: [] });
    expect(
      await screen.findByText(
        /Matching opens when more researchers in your field join/,
      ),
    ).toBeInTheDocument();
  });
});
