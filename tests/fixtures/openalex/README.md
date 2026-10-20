# OpenAlex fixtures

Recorded responses from the OpenAlex API (retrieved 2026-10-20, OpenAlex data is CC0) for the two demo researchers, so cloud sessions build and test **without network access**. Served by the `FixtureLiterature` adapter (`LITERATURE_BACKEND=fixtures`, plan M1 B3).

| Researcher | OpenAlex ID | ORCID | Notes |
|---|---|---|---|
| Earl Johan Foster (UBC) | `A5059588273` | 0000-0002-4103-8510 | 159 works. A second record `A5140167166` ("Johan Foster", 1 work) shares the ORCID: use it for the **merge split profiles** acceptance test. |
| Raphaell Moreira (UBC → Bremen) | `A5033675494` | 0000-0001-6636-725X | 29 works returned (author record says 30). Co-author of Foster on 5 works. |

## Layout

| Path | Port method | Content |
|---|---|---|
| `authors/<id>.json` | `get_author` | Author record (subset of fields) |
| `authors_search/johan-foster.json`, `raphaell-moreira.json` | `search_authors(name)` | Candidate lists, including namesakes and split records |
| `authors_search/orcid-<orcid>.json` | grouping split profiles by ORCID | All author records sharing the ORCID |
| `works_by_author/<id>.json` | `list_author_works` | One page (`next_cursor: null`) of works |
| `works_search/q1..q6.json` | `search_works` | 6 works each, abstracts as `abstract_inverted_index` (truncated to ~420 chars) |
| `works_count/n1..n4.json` | `count_works` | `meta.count` only, for novelty measurement |
| `topics.json` | — | Topic id → name, subfield |
| `manifest.json` | — | Every request (port method + params) → file. `FixtureLiterature` resolves requests through it. |

**Lookup rules for `FixtureLiterature`:** exact match on the manifest params first; `search_works` / `count_works` with an unknown query return the closest recorded query by token overlap (log a warning) so the fake pipeline always runs; `get_work(id)` searches every work in all files.

**The `FakeLLM` `QueryPlanner` fixture should emit the six recorded queries** (`q1`–`q6` in `manifest.json`) and the novelty step the four recorded count queries (`n1`–`n4`), so the M3 acceptance run works end to end on fixtures.

## Simplifications (documented on purpose)
- Fields are a subset of the OpenAlex schema (same names, so the real `OpenAlexClient` parser works on both).
- Foster's works list only Foster and, on joint works, Moreira as authors. Moreira's works carry `authorships_truncated: true` and `authors_count`.
- Topic objects have `subfield` but not `field`/`domain`.
- A few corpus abstracts had a leading "Abstract" label removed.

## Refreshing
`scripts/fetch_fixtures.py` (marked `needs_network`) re-records everything. OpenAlex needs a free API key in practice (`OPENALEX_API_KEY`), because the anonymous daily budget is shared per IP and is often used up on cloud machines.

## Privacy
These are public bibliographic records. Everything private shown in the designs (vault documents, reviewer comments, unpublished data) is **not** here and must never be attributed to these researchers outside files clearly labelled as samples.
