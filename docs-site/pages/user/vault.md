# Your private vault

The private vault is where you add your own documents so that Scout can read them and list what they contain. You can then decide what to do with each file.

!!! warning "Do not upload confidential material"
    Do not upload confidential material such as grant proposals, unpublished manuscripts under embargo, or anything under an NDA or third-party confidentiality. Scout cannot guarantee the confidentiality of stored documents in this pilot. Only upload what you are happy to see stored on our server. You can delete any document at any time. While this warning is on, the upload area asks you to tick **I am not uploading confidential material** before you can choose files.

Find it in the menu under **Private vault** (`/vault`).

## What you can upload

- **PDF** or **Word (.docx)** files. Old Word files (.doc) are not supported.
- Up to **25 MB** per file and **300 pages**.
- Files with **text you can select**. Scout cannot read scanned pages or photos of pages yet.
- Files that are **not password-protected**.
- Up to **50 documents** in your vault. Failed and refused files count too, until you delete them.
- If you upload the **same file again**, Scout keeps one copy and shows the document you already have.

Include reviewer reports if you have them. Scout works out what each file is. You can change its answer.

## What Scout does with a file

1. **Upload.** Scout checks the size and the type of the file. It looks at the content, not only the file name.
2. **Reading.** Scout reads the text and splits it into parts.
3. **Finding insights.** Scout asks an AI language model to list the aims, methods, findings, open questions and reviewer objections in your document. Scout then checks each item against the text (see below).
4. **Ready.** The insights are on the document. You can open them with **Show details**.

Scout sends excerpts of your document text to an AI language model to find the insights. The service operator chooses the providers; this version may use the free tiers of OpenRouter, Groq and Mistral. Scout does not train models on your files, but the providers have their own terms. Scout has a rule for this: text from your private documents is sent **only** to providers the operator has marked as approved for confidential data, and never to any other provider. Published literature data, such as paper titles, can go to any configured provider. If no provider is approved, Scout still stores your file and reads its text on its own server, but the document shows **Waiting for an approved provider** and nothing from it goes to any AI model. Text that Scout or a model derived from your documents (for example a draft opportunity written while your documents were in play) is treated the same way. Search terms sent to OpenAlex are built from your published work only. It is analysed automatically, within about ten minutes, once the operator approves a provider. Whichever providers are approved, do not upload confidential documents in this pilot.

Nothing from your vault is shared with other researchers in this version, and only your account can open your documents in Scout. That is an access rule, not a guarantee of confidentiality: the files and text sit on the operator's server.

## "Keep the original file"

Under the drop zone there is a box called **Keep the original file**. It is **unticked** by default.

- **Unticked.** Scout deletes the file once it has finished reading it. Scout keeps the insights, short quotes of reviewer comments (up to 300 characters each) and short summaries. It does not keep the full text. The document row says **insights only**. While a document is **waiting for an approved provider**, the uploaded file is already deleted but the text Scout extracted stays on the server until the document is analysed (then it is cut down to insights and short quotes) or you delete it; the row says **file removed, text kept until analysed**.
- **Ticked.** Scout keeps the file and its full text until you delete the document. The row says **original kept**.

If the box is unticked, the file is deleted even when reading fails. To try again, upload the file again.

Your service may be set up so that every file is kept, or every file is deleted after reading. In that case the box makes no difference, and the row shows what the service does.

## Status words

| Shown on the card | What it means |
|---|---|
| **Uploaded** | The file is stored and waiting to be read. |
| **Reading** | Scout is reading the text. |
| **Finding insights** | Scout is finding and checking the insights. |
| **Ready** | Done. You can see the insights. |
| **Not supported** | Scout cannot read this file. The message under the card says why. |
| **Couldn’t read** | Scout tried, but could not finish. The message under the card says why. |

The list updates by itself every 2 seconds while a file is being read.

Reading time depends on the size of the file and on the AI service. There is no retry button yet. To try again, delete the document and upload the file again.

## Why a file is refused

When an upload is refused, Scout shows one of these messages.

| Message on screen | What it means and what to do |
|---|---|
| This file is larger than 25 MB. | Send a smaller version, or split the document. |
| Only PDF and Word (.docx) files are supported for now. | Save or export the file as PDF or .docx. |
| Your vault is full. Delete a document to add another. | You have 50 documents. Delete one you no longer need. (Some versions show "The upload failed. Please try again." in this case.) |
| This file is empty. Check that it has content and upload it again. | The file has no content. Check that it opens, then upload it again. |
| This file could not be accepted. It may be empty, damaged or protected. | Check that the file opens and has content. Export it again. |
| The upload was interrupted. Check your connection and try again. | Check your internet connection and upload the file again. |
| The upload failed. Please try again. | Try again. If it keeps failing, contact the team running Scout. |

When reading fails, the card shows one of these messages.

| Message on screen | What it means and what to do |
|---|---|
| This file is password-protected. Remove the password and upload it again. | Remove the password in your PDF reader, save a copy, and upload that copy. |
| This looks like a scan with no readable text. Export a text PDF, or upload the Word version. | The pages are images, so Scout cannot read them. Export from the original Word file, or use a text PDF. |
| This document has too many pages for Scout to read. Upload the relevant part. | The limit is 300 pages. Upload the part you need. |
| This document has too much text for Scout to read. Upload the relevant part. | The document is longer than Scout reads at once. Upload the part you need. |
| This file looks damaged and could not be opened. Try exporting it again. | Export the file again from its program, then upload it. |
| This file was rejected because it unpacks to an unsafe size. | The Word file is unusually compressed. Save it again in Word. |
| Reading this file took too long. Try a smaller or simpler version. | Upload a shorter or simpler version. |
| This file is too complex for Scout to read reliably. Try a simpler export. | Export a simpler version, for example without many images. |
| The stored original is missing, so Scout cannot read it again. Please upload it again. | Upload the file again. |
| Reading this file stalled. Please delete it and upload it again. | Delete the document and upload the file again. |
| Scout could not read this file. Please delete it and upload the file again. | There is no retry button. Delete the document, then upload the file again. |
| Scout read the file but could not extract insights. Please delete it and upload the file again. | Delete the document, then upload the file again. |
| Storage was unavailable while reading this file. Please delete it and upload the file again. | Delete the document, then upload the file again. |
| Scout could not process this file. | Upload the file again. |

## What the insights are

The insights list what Scout found in your document, grouped as **Aims**, **Methods**, **Findings**, **Open questions** and **Reviewer objections**, with keywords.

- Each item shows its page number, for example **p. 9**, or **pp. 4 to 5**.
- Reviewer objections include a **quote**. Scout checks each quote against your file before it shows it.
- Scout only shows an item when it can match it to the text. If an item cannot be matched, Scout hides it. So the list can be shorter than the document.
- For a Word file without page breaks, Scout cannot tell the pages apart, so the page number shows **p. 1**.
- Tables in Word files become lines with the cells separated by " | ". Tables in PDF files become plain lines of text.

## Kind of document

Scout suggests a kind for each document, for example **Rejected grant** or **Lab notes**. The drop-down labelled **Scout thinks this is…** shows the kind. Change it if the suggestion is wrong. Your choice stays.

## Deleting a document

To delete a document, select **Delete** on its card, then **Delete** again in the box that opens. The box lists what will be removed.

Deleting removes:

- the extracted text,
- the insights,
- the quotes and summaries taken from the document,
- the stored original file, if it was kept.

The document disappears from your list straight away. The stored file is removed by a background job. If storage is slow, the job tries again.

**Deleting cannot be undone.** To get the document back, upload the file again.

## Delete everything

The privacy tab (Settings, Privacy) offers "Delete my vault". You confirm by typing DELETE. It removes every document you uploaded, the text and insights taken from them, and the stored files. Your profile, past runs and matches stay. Deleting your whole account removes those too.

## Delete your account

In the same tab, "Delete my account" asks you to type your e-mail address again. Your files, profile, runs, feedback and matches are then erased in the background and you are signed out. Matches that are still pending simply expire for the other person; they are not told why. This cannot be undone.

**What is not erased:**

- Cost records, which no longer name you.
- Ratings and free-text comments you gave on a match that both sides accepted stay visible to the other person. A comment may name you.
- After you delete only your vault: short paraphrases (up to 300 characters) inside ideas that Scout already showed you.
- The region and career band on a closed match proposal, for 30 days.
- Server logs and the sign-in provider's own audit log, which are outside Scout's database.

## Daily limit

If many people use Scout on the same day, new analyses can pause until the next day (UTC). A banner tells you when that happens; your documents and earlier results stay available.
