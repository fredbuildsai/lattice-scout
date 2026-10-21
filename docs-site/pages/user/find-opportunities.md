# Find opportunities

Scout looks at your profile and at the documents you choose from your private vault. It also searches published research. Then it suggests research ideas that look open, and it shows you the evidence behind each one.

Each idea is a suggestion, not a fact. Scout checks what it can, and it tells you what backs each claim. You decide what is worth your time.

The screens for this page are still being built. The rules on this page apply to every run.

## Before you start

- **Claim your profile** first (see [Getting started](getting-started.md)). Scout needs your published works to know your field.
- **Choose documents** from your [private vault](vault.md), if you want. A document must be **ready** before you can use it. You can choose up to 10 documents. You can also run Scout with no documents. Then Scout uses your published works only.
- You can start a limited number of runs each day. Scout also has a limit on the spending for each day. The service sets both limits.

## What a run does

1. **Reads your profile.** Scout reads your topics and recent titles. It reads the key points of your chosen documents: aims, methods, findings, open questions and reviewer objections.
2. **Searches published papers.** Scout searches [OpenAlex](https://openalex.org), a free catalogue of research papers. It looks at papers from the last six years in your field.
3. **Groups the papers by topic.** Papers on the same topic form a group. Scout looks for groups where your own work is thin or missing.
4. **Counts matching papers.** For each group, Scout counts how many papers from the last five years match the idea. This is the **novelty count**. Scout counts the papers. It does not guess.
5. **Drafts ideas.** An AI model writes up to five draft ideas per batch. Each idea has claims, and each claim cites its sources.
6. **Checks every citation.** Scout checks each quote against its source. A second AI check, the **reviewer**, then looks at the claims.
7. **Shows the ideas that pass.** A run shows at most 10 ideas.

Scout does not show you the ideas that do not pass. It keeps them only so the team can review how well Scout works. You never see them.

## Progress

While a run works, the page shows the current stage and a percentage. The stages are:

| Stage | What Scout is doing |
|---|---|
| **Retrieving** | Searching for papers. |
| **Clustering** | Grouping papers by topic. |
| **Analysing** | Writing draft ideas and counting papers. |
| **Verifying** | Checking citations and running the reviewer. |

A run can take a while. You can leave the page and come back later.

If a run reaches a limit (for the day, or for the run itself), it still finishes. It is marked **partial**, and the page says which step was cut short. Fewer ideas may appear in that case.

You can **stop** a run. A stopped run shows no ideas.

## What "verified" means

A claim is **verified** when Scout finds its support in the source it cites:

- **A published paper.** The quote appears in the paper's abstract, after light tidying of spaces and punctuation.
- **Your document.** The quote appears in your document. If you chose not to keep the original file, the quote is the short quote Scout kept from it.
- **A count.** The number of matching papers was counted in OpenAlex.
- **Your own work.** The work is in your profile.

Quotes are at most 300 characters.

An idea is **shown** only when all of these are true:

- At least **two different sources** back its claims.
- At least **one of them is a published paper**.
- Scout has **counted** the matching papers, so the novelty count is measured.

Claims that are not verified never appear on screen.

## Why an idea may not appear

- Fewer than two sources back it, or no published paper is among them.
- Scout could not count the matching papers.
- The reviewer found that the work is already done, or that the claims do not hold up.
- It repeats an idea you have already seen, saved or dismissed.
- The run reached a limit before Scout could check the idea.
- A document it relied on has been deleted. An idea disappears when it no longer meets the rules above.
- The run was stopped or did not finish.

## Open the evidence

Each claim has markers. Select a marker to open its source:

- **Your document, kept:** the passage, with its page numbers.
- **Your document, not kept:** only the short quote Scout kept.
- **A published paper:** a link to the paper (its DOI, or its OpenAlex page).
- **A count:** a search in OpenAlex for the same words and years.

## Save or dismiss an idea

- **Save** an idea to keep it in your list for later.
- **Dismiss** an idea to hide it. A dismissed idea does not come back in later runs, even if the wording is similar.
- You can move an idea between **shown**, **saved** and **dismissed** at any time.

Only you can see your ideas.

## What happens to your data

- While a run works, Scout reads your chosen documents. It keeps only references to the passages, and quotes of at most 300 characters.
- Scout sends the text of your documents to the AI model that the Scout service uses. The service operator chooses the model provider. Scout does not send author names to the model. See [Your private vault](vault.md) for how documents are kept.
- If you **delete a document**, its quotes are removed straight away. An idea that still meets the rules stays on your list. An idea that no longer meets the rules disappears.
- The team can review how Scout's checks went, including claims that were not verified. Vault quotes are hidden in that review unless the service is set to show them. Each review is recorded.