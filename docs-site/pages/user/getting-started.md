# Getting started

Scout starts from your public publication record on OpenAlex, a free catalogue of research papers. You confirm which records are yours, decide whether Scout may suggest you to other researchers, and Scout builds your profile.

This takes about five minutes.

## 1. Sign in

1. Open Scout and type your work email address.
2. Choose **Continue**.
3. Use the sign-in method the page offers:
   - **Continue with Google**, or
   - the sign-in link Scout e-mails to you.

On a test installation the form signs you in straight away, with no password.

Scout keeps the e-mail address you sign in with.

## 2. Find yourself

1. Open **Claim profile** (or go to `/onboarding`). The page is called **Is this you?**
2. Type your name. Add your institution if you want fewer results.
3. Choose **Search**.
4. Scout shows one card for each profile it finds. A card shows the name, the institutions, the number of works and citations, and the main topics.
5. Choose **This is me** on your card.

A card can say **includes 2 records**. OpenAlex sometimes lists one researcher under several author records. Records that share your ORCID (a researcher ID number) are shown together on one card.

If nothing matches, try another spelling of your name, or add your institution.

## 3. Choose which records are yours

Under your card you see **Records to include**. A ticked record counts as yours.

- Records that share your ORCID are ticked for you.
- A record marked **possibly a namesake, left unticked** has a similar name at your institution, but Scout is not sure it is yours. Tick it only if you know it is.
- The main record (the one with the most works) stays ticked.

## 4. Confirm and wait

1. Choose your matching preference (see step 6).
2. Choose **Confirm**.
3. The page **Building your profile** appears. Scout copies your works from OpenAlex. This takes a few seconds.
4. When it is done, you arrive on **Home**.

If the import fails, the page says so. Reload it to try again.

## 5. Check your works

**Home** shows how many of your works are included, for example "149 of 159 works included", and your main topics.

Scout leaves some works unticked when it is not sure they are yours:

- works that are not journal articles or reviews, such as datasets
- works where the author names do not match the names on your profile

Changing single works is not available in the app yet. When Scout refreshes your works later, your own choices are kept.

## 6. Set your privacy choices

Open **Profile and privacy** (or go to `/settings/privacy`). There are two switches. Both are off until you turn them on.

- **Suggest me to complementary researchers.** You stay anonymous. Contacts are shared only if you both accept. Matching is not live yet, so this switch changes nothing you can see at the moment.
- **Also use topic signals from my private vault.** Scout uses only topic signals, never your files. This switch needs the first one. Turning the first switch off turns this one off too. The private vault is not part of this release yet.

## What Scout stores

- your account e-mail address
- your researcher profile: name, institution, country, topics, and the OpenAlex records and works you confirmed
- your two privacy choices
- a log of when you confirmed your profile and when you changed a privacy choice

Other people cannot see your profile through Scout. At the moment nothing is shared with other researchers.

If someone else also claims one of your OpenAlex records, Scout does not tell you or them. Behind the scenes both profiles are held back from matching until the Scout team can check ownership.
