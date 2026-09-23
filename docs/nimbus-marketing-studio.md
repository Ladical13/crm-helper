# Nimbus Marketing Studio

Implemented September 16, 2026. Open **Nimbus → Marketing Studio** at
`/nimbus/marketing/social`. This is local source work until the whole portal is deployed.

## Weekly workflow

1. Create a plan with an audience, area, objective, services, channels and start date.
2. Load the source library and team questions, or request current public research.
   Each idea shows the question, why it matters, an approach, source links,
   a research date and the kind of evidence available.
3. Choose ideas yourself or use **Suggest a balanced week**. The suggestion mixes
   themes and puts similar recently used topics later. Review the selection and
   choose photo, carousel or reel formats. Up to seven ideas fit one plan.
4. Generate channel packages, or create blank drafts to write manually.
   Facebook, Instagram, LinkedIn, Google Business Profile, website articles and
   email newsletters share the same brand-aware writer. A Google update uses
   a photo format; articles/newsletters use text. Copy stays separate from
   photo directions, alternative text, slide copy, scripts and shot lists.
5. Edit the copy and creative brief, assign an owner/date, and attach a link to
   the finished visual. Save before approving. Copy or creative edits reset
   approval. Earlier saved versions remain available. Unsaved edits to other
   cards survive an in-page refresh, but are not stored across a browser reload.
6. Approve, check the visual, and mark ready. Copy the caption, open the platform,
   post there, then record the published URL. Dates are editorial planning dates;
   they do not cause publication. CSV and JSON exports include the full handoff.
7. Enter organic results at a consistent age, such as seven days after posting.
   Unknown metrics stay blank. The results view shows coverage, totals and topics
   to investigate based on saves, shares and inquiries. Reach across platforms
   is not deduplicated and results are not automatically imported.

## Research behind the starter library

The initial library is editorial research, not a claim that these are the most
popular searches. It includes estimate comparison and contractor selection
([FTC](https://consumer.ftc.gov/articles/how-avoid-home-improvement-scam)),
comfort and drafts around windows/doors
([CSU Extension](https://extension.colostate.edu/resource/air-sealing-colorado-homes/)),
and questions to prepare for an insurer
([Colorado Division of Insurance](https://doi.colorado.gov/homeowners-hoainsurancetoolkit)).
These pages were checked September 16, 2026; their dates remain visible even
when a new campaign imports the library later.

Current research uses the existing Perplexity connection and spending cap.
Only research ideas whose evidence URLs also appear in the provider's citations
are accepted. This reduces fabricated URLs; it does not establish that every
interpretation is correct. Open the sources during review. The interface clearly
reports unavailable research and keeps the curated library usable.

Team questions are human-entered paraphrases, with no customer identity needed.
Raw CRM activity notes are not sent to the writer by this feature. No private
Facebook groups are read and no social popularity figures are invented.

## Storage and review controls

Additive tables in Nimbus's existing SQLite database:
`marketing_campaigns`, `marketing_ideas`, `marketing_posts`, and
`marketing_post_history`. `content_drafts.creative_json` stores production
instructions independently of public copy. Existing drafts remain intact.
Back up the production volume before deploying schema changes.

Research/drafting use Nimbus's existing shared background-job lease. Repeated
creation skips already drafted channels and failed channels can be retried.
Each finished package is saved in a transaction. Post edits require the saved
revision; stale updates return 409. The older review APIs cannot bypass Studio
review controls. Posted copy is retained as a historical record; metrics can
still be updated. CSV cells are protected against spreadsheet formula execution.

The older topic writer now uses the same brand prompt and validation. Its copy
button collision is fixed and draft-start errors are surfaced.

## Boundaries

This build produces copy and production briefs, stores links to finished assets,
and supports manual posting. It does not render finished Canva designs or videos,
upload assets, publish automatically, connect social accounts, or retrieve live
social analytics. Those integrations remain separate work. The existing weekly
social scheduler retains its older candidate-selection workflow; Studio plans
are created and run from the Studio interface.

Tests cover source filtering and fallback, research deduplication, balanced
selection, format adaptation, malformed model output, shared writer validation,
revision conflicts, approval resets, asset gates, immutable posted copy, metrics,
CSV exports, admin access and shared job locking. Browser checks use an isolated
local database and never touch production or real social accounts.
