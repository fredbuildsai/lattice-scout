# Sample vault documents (fictional)

Three **fictional** documents used as the demo user's private vault in tests, the M3 acceptance run and the docs screenshots. They were written for this project; they are not authored by, and must never be presented as the work of, any real person. Every file starts with a "SAMPLE" banner that the app keeps.

| File | DocKind | Used for |
|---|---|---|
| `declined-proposal-lignin-foams.md` | `declined_grant` | resubmission opportunity, verbatim reviewer objections |
| `unpublished-results-cnf-membrane.md` | `unpublished_results` | white-spot opportunity, readiness |
| `lab-notes-pfas-membrane.md` | `lab_notes` | synergy opportunity (matching with a photocatalysis researcher) |

M2 adds `scripts/make_sample_docs.py`, which renders each Markdown file to **PDF** (e.g. with `reportlab` or `weasyprint`) and **DOCX** (`python-docx`) into `tests/fixtures/vault/build/` (git-ignored), so ingestion is tested on real file formats. Page breaks are marked with `<!-- page -->`.
