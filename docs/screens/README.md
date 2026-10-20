# Screens: design reference for the MVP

The PNGs are the visual source of truth for layout, hierarchy, copy and tokens. `source/` has the original design files (HTML-like `.dc.html`, readable as text for exact copy, colours and spacing; they do not run outside the design tool).

**Demo data.** The persona uses the **public** OpenAlex records of Johan Foster (UBC) and Raphaell Moreira (Bremen). All vault documents, reviewer comments, unpublished results and match details shown are **invented sample data** for the design. Never seed them as real; the app uses real API data and the synthetic seed (plan M5.8).

## Design tokens (implement as the Tailwind theme)

| Token | Value | Use |
|---|---|---|
| `bg` | `#F3F4F1` | page background |
| `surface` | `#FFFFFF` | cards |
| `ink` | `#14171A` | text |
| `ink-2` / `ink-3` | `#3A4047` / `#555C63` | body text / secondary text |
| `line` / `line-strong` | `#D9DCD6` / `#C4C8C1` | borders, dividers |
| `subtle` | `#F1F2EF` (also `#ECEEE9`) | muted fills, inputs |
| `accent` / `accent-dark` / `accent-50` | `#2446C7` / `#16308F` / `#E8ECFA` | primary actions, links, active nav |
| `scout` / `scout-50` / `scout-ink` | `#5B3FB5` / `#EEE9F8` (also `#F4F1FB`) / `#4A2F9A` | everything Scout (AI) says or does |
| `teal` / `teal-dark` / `teal-50` | `#0F6E6E` / `#0F5E5E` / `#E3F1EF` | matching, synergy, positive state |
| `warm` / `warm-mid` / `warm-50` | `#7A3A06` / `#B4570F` / `#FBF0E4` | resubmission, warnings |
| Fonts | IBM Plex Sans (UI), IBM Plex Mono (labels, numbers), Source Serif 4 (titles of ideas and matches) | self-host via `@fontsource/*` |

Radii 8–12 px on cards, 6 px on buttons and chips. Scout's voice is always purple with the sparkle icon; evidence markers are small numbered circles.

## Screen index

MVP column: ✅ build in the MVP · ◐ build partly (see notes) · ⏳ roadmap, do not build.

### Web (1440 px)

| Id | File | Route | MVP | Milestone | Endpoints | Notes |
|---|---|---|---|---|---|---|
| W0 | `web/W0-signin.png` | `/signin` | ✅ | M1 | Supabase Auth, `POST /dev/login` (dev) | Google + email magic link. **No ORCID button** in the MVP. |
| W1 | `web/W1-claim-profile.png` | `/onboarding` | ✅ | M1 | `GET /profile/candidates`, `POST /profile/claim`, `PATCH /me/consent` | Merged split profiles as one card ("includes N records"); uncertain works unticked; matching consent toggle. |
| W2 | `web/W2-home.png` | `/` | ◐ | M4 (match teaser M5) | `GET /runs?latest`, `GET /opportunities?limit=3`, `GET /matches`, `GET /documents` | Briefing, top 3 ideas, match teaser, vault summary. Reach mini-map: roadmap. |
| W3 | `web/W3-vault.png` | `/vault` | ✅ | M2 | `/documents` CRUD | Files only (PDF, DOCX). Retention choice ("keep original" unticked, ADR-009). |
| W4 | `web/W4-analysis-run.png` | `/runs/:id` | ◐ | M4 | `GET /runs/{id}` (poll) | Step list + progress bar. No live sub-agent log in the MVP. |
| W5 | `web/W5-opportunities.png` | `/opportunities` | ◐ | M4 | `GET /opportunities`, `PATCH /opportunities/{id}`, `POST /feedback` | MVP kinds: white spot, synergy, resubmission. "Funding call" and "Collaboration" filters: roadmap. "Lab colleagues' work" filter: roadmap. Chart uses real novelty × readiness. |
| W6 | `web/W6-opportunity-detail.png` | `/opportunities/:id` | ✅ | M4 | `GET /opportunities/{id}`, `GET /evidence/{id}/open` | Claims with numbered `<SourceMarker>`s, evidence tabs, measured novelty, scores with numbers behind them, next-steps checklist (client state). |
| W7 | `web/W7-matches.png` | `/matches` | ✅ | M5 | `/matches`, `/matches/{id}/decision`, `/matches/{id}/rating` | Anonymous card → waiting → mutual reveal with contact + joint ideas → rating. A decline is never shown to the other side. |
| W8 | `web/W8-reach.png` | `/reach` | ⏳ | — | — | World map and network graph: after the MVP. Keep the menu entry hidden behind a flag. |
| W9 | `web/W9-ask-scout.png` | (panel) | ⏳ | — | — | Chat, voice, photo: after the MVP. M0 adds only a FAB placeholder. |
| W10 | `web/W10-profile-privacy.png` | `/profile` | ◐ | M1 (profile), M6 (privacy, notifications) | `GET /profile`, `PATCH /me/consent`, `DELETE /me` | Sources (OpenAlex), consents, delete vault, delete account. |
| W11 | `web/W11-menu.png` | (component) | ✅ | M0 | — | Burger menu in every header, `current` highlights the active page. Hide roadmap entries. |

### Mobile (390 px; the responsive web app at phone width, PWA)

| Id | File | Web equivalent | MVP | Notes |
|---|---|---|---|---|
| M0 | `mobile/M0-signin.png` | W0 | ✅ | |
| M1 | `mobile/M1-claim-profile.png` | W1 | ✅ | |
| M2 | `mobile/M2-home.png` | W2 | ◐ | Bottom tab bar: Home, Ideas, (Scout FAB), Matches, Reach. Reach tab hidden in the MVP. |
| M3 | `mobile/M3-add-to-vault.png` | W3 | ◐ | Files only. Camera and voice note: roadmap. |
| M4 | `mobile/M4-analysis-run.png` | W4 | ◐ | |
| M5 | `mobile/M5-opportunities.png` | W5 | ◐ | |
| M6 | `mobile/M6-opportunity-detail.png` | W6 | ✅ | Markers open as bottom sheets on tap. |
| M7 | `mobile/M7-match-proposal.png` | W7 | ✅ | |
| M8 | `mobile/M8-mutual-match.png` | W7 | ✅ | |
| M9 | `mobile/M9-reach.png` | W8 | ⏳ | |
| M10 | `mobile/M10-ask-scout-chat.png` | W9 | ⏳ | |
| M11 | `mobile/M11-ask-scout-voice.png` | W9 | ⏳ | |
| M12 | `mobile/M12-profile-privacy.png` | W10 | ◐ | |

The MVP ships one responsive React app; "mobile" screens are its 390 px layout (DoD §0.7 requires every MVP screen to work at 390 px). Native apps are not part of the MVP.

Animated walkthroughs of the full product vision (including roadmap features) were delivered separately as GIF/MP4; they are inspiration, not specification.
