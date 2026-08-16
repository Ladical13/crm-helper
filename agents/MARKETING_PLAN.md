# Nimbus marketing plan

The living plan for the marketing side of Nimbus. Setup mechanics live in
[CONNECTIONS.md](CONNECTIONS.md); this file is what we are building and why,
and what we have deliberately decided not to wait for.

## Two franchises, two websites

| Site | Whose | Treat as |
|---|---|---|
| **projectoneroofingcolorado.com** | Colorado franchise — **us** | The site all marketing work targets |
| projectoneroofing.com | Texas franchises (Tyler / Longview) | Same brand, different business. Not ours, not a competitor |

The Texas site is live and **deliberately not redirected** here. Recorded in
`marketing_profile.json` under `company.sibling_sites`, and
`agents/seo/research.py` filters it out of competitor results, content gaps
and citations — reporting it as a rival would benchmark us against ourselves,
and treating it as ours would produce copy for a market we do not serve.

## The constraint that shapes everything

**Google Search Console and Google Analytics 4 are owned at the franchise
level and we hold no role on either.** Not a limited role — no access at all.
Confirmed 2026-08-10: both consoles show an empty "get started" screen for
`luke@projectoneroofing.com`.

That is a permanent-until-someone-else-acts condition, so **nothing in the
marketing roadmap blocks on it.** Both appear in Nimbus as
**Owner access required · Optional — future enhancement**, styled muted rather
than red. They are not broken, not misconfigured, and not a to-do item we keep
failing to complete. A dashboard that always shows two red rows trains people
to stop reading it.

If access is ever granted, setting the env vars is the whole change — the
probe code is already written and tested.

**OPEN — worth re-testing.** That conclusion was reached while the website was
projectoneroofing.com, the Texas domain. The Colorado franchise now has its
own site, and a Search Console property is **per domain**: whoever set up
projectoneroofingcolorado.com may hold its property, and that may not be the
same people who hold the Texas one. Before writing GA4 and Search Console off
permanently, check whether a property for the Colorado domain exists and who
owns it. If nobody has claimed it, the CMS self-verification route makes it
ours outright — no franchise involved.

## Tiers

### Active — built and working without anyone's permission

| Capability | Data source | Status |
|---|---|---|
| **Local SEO Strategist** | Our public site + public research | ✅ v1 shipped |
| **Content Brief bot** | Approved recommendations | ✅ Shipped |
| Public website / sitemap | `projectoneroofingcolorado.com/sitemap.xml` | ✅ Connected |
| Marketing profile | `marketing_profile.json` (version-controlled) | ✅ In use |
| **GDELT** — Colorado news + storm events | Free public endpoint, no key | ✅ Connected |
| ~~Reddit~~ — customer language | Reddit's written commercial approval | ⛔ **Gated by Reddit**, see CONNECTIONS.md. Built, tested, switched off |
| Perplexity research | `PERPLEXITY_API_KEY`, capped monthly | ✅ In use |

**Why the free sources matter more than their row count suggests.** The
cross-source bonus in `content/score.py` was written as an intention long
before anything could trigger it — with only Perplexity reporting, every topic
had exactly one source and the bonus was dead code. A question asked on Reddit
*and* covered in local news *and* surfaced by Perplexity now scores
meaningfully higher than one that appears once. Wiring free sources upgrades
the ranking, not just the volume.

Reddit is also a **primary** source where everything else is secondary: the
homeowner's own words with a permalink, rather than a model's summary of them.

**Social media is not a ranking lever.** Google does not use social signals as
a ranking factor. Social earns its place here as (a) a source of real customer
language and (b) a driver of branded search and Google Business Profile
engagement — not because posting moves rankings.

### Pending someone else's clock

| Capability | Blocked on | Notes |
|---|---|---|
| Google Business Profile | Google's API access approval | Manager access is enough. Form submitted → wait |
| Franchise CRM scorecard | Answers from Base44 | See the six questions below |

### Optional future enhancements — owner access required

| Capability | Who must act | What it would add |
|---|---|---|
| Search Console | A franchise Owner, **or** self-verify a URL-prefix property via a CMS meta tag | Real queries, impressions, positions. Would upgrade SEO recommendations from public-research opportunities to measured findings |
| Google Analytics 4 | A franchise property Administrator | Sessions, landing pages, conversions. No self-serve route exists |

Search Console has a route around the franchise; **Analytics does not.** Worth
remembering when prioritising which ask to chase.

## What the absence of those two actually costs

Being precise, because it determines what we may claim:

- We **cannot** know our rankings, our search volume, our traffic, our
  conversion rates, or any competitor's performance.
- We **can** know what is on our own pages, what our sitemap contains, what
  questions the public web says Northern Colorado homeowners ask, and what
  topics competitors publish.

So the Local SEO Strategist reports *opportunities with sources*, never
*results*. That is not a hedge — it is the honest limit of the inputs, and
`agents/seo/honesty.py` enforces it in code rather than trusting prose.

## Local SEO Strategist v1

`agents/seo/` — see [seo/README.md](seo/README.md).

Weekly run → a saved Markdown report plus a ranked queue of recommendations in
seven categories, each requiring human approval. Approving records a decision;
it never performs the work.

Read-only throughout: no publishing, no email, no CMS edits, no Business
Profile changes, no CRM writes.

## Franchise CRM scorecard — blocked on answers, not on code

The franchise CRM (Base44) holds lead → inspection → estimate → sold-job
outcomes. Six questions decide what is buildable:

1. Can we get a **read-only** API token?
2. Do the entity endpoints support **filtering and pagination**?
3. What is the full **`Project.status`** vocabulary?
4. Are **stage-transition dates** readable, or only `updated_date`?
5. Is there a **sold amount** field on `Project`?
6. Can `source` become a **picklist**, and can a **UTM/campaign field** be added?

**4 and 6 are the decisive ones.** Without transition dates there are no true
conversion rates. Without UTM capture every website lead collapses into one
bucket called `website`, so no amount of SEO work can be tied to revenue.

## The build order

Agreed sequence, with where each stands.

| # | Stage | Status |
|---|---|---|
| 1 | **SEO Strategist** | ✅ Built. Weekly report + ranked queue |
| 2 | **Content Brief bot** | ✅ Built. Approved recommendation → structured brief |
| 3 | **Social + GBP post bot** | ✅ Built. Facebook, Instagram, LinkedIn, Business Profile — drafts only |
| 4 | **Weekly scheduler** | ✅ Built. `NIMBUS_SCHEDULER=1` |
| 5 | **Bing Webmaster Tools** | ✅ Built. Needs two env vars — real query data, no franchise involvement |
| 5.5 | **Supervisor** | ✅ Built. Needs `ANTHROPIC_API_KEY` — see below |
| 6 | **Draft Creator (long-form pages)** | Not started. A brief must come first |
| 7 | **GBP reputation** (reviews, replies, review requests) | ⛔ Blocked on Google's API approval |
| 8 | **Scorecard** | ⛔ Blocked on the franchise CRM answers. Last by design |

### On publishing

The post bot writes; it never publishes. That is a decision, not a missing
feature. Auto-posting to Facebook, Instagram or LinkedIn needs app review on
each platform, and Business Profile needs the API approval still pending — but
even with all four wired, posting without a person reading it first would
abandon the one rule the whole system is built around. The handoff is a copy
button and a link to the platform.

Every post passes the same honesty guard as an SEO recommendation. A post
claiming "the #1 roofer in Fort Collins" is the same fabrication as a report
claiming it, and is rejected rather than softened.

## Supervisor — the layer you talk to

`agents/supervisor/` — the 🧠 tab in Nimbus. A conversation that can read
everything the other bots produced and start any of them running.

It exists because the rest of Nimbus is six narrow bots writing into six
screens, and nobody reads six screens. The supervisor is the one place to ask
"what needs my attention" and get an answer assembled from all of them.

**It cannot approve anything.** Not an SEO recommendation, not a social draft,
not marking a post published. It also cannot raise its own spend cap or move a
rep's territory. Those stay a human click, because the rule the whole system is
built around is that a person reads copy before a customer does — and a
supervisor that could approve its own team's output would quietly delete that
rule. `tools.FORBIDDEN_ROUTES` states it in code and
`test_supervisor_cannot_approve` fails if a tool ever reaches one.

**Every tool is an HTTP call to Nimbus's own API, made with the signed-in
admin's cookie.** Nothing in `tools.py` touches the database or imports an
agent module. So the supervisor is exactly as powerful as the person at the
dashboard, it inherits every guard already written (the admin gate, the
"already running" 409, the NotApproved check on briefs), and there is no
second copy of those rules to drift out of sync.

**Its spend has its own cap**, `supervisor_monthly_cap_usd` (default $50),
tracked in the same `spend_ledger` under source `anthropic`. Separate from the
research cap on purpose: the two fail differently. Exhausting Perplexity stops
research; exhausting this stops the only thing a human talks to. One shared
cap would let a chatty afternoon silently starve next week's SEO run.

The honesty rule is in its system prompt, not just in this document. It is
told what we cannot measure — rankings, traffic, conversion rates, competitor
performance — and told to say so plainly rather than produce a number. If it
ever quotes a traffic figure, that is a bug worth fixing the same day.

### 4 — Google Business Profile and reputation (not built)

Flagging new reviews, drafting replies, spotting jobs eligible for a review
request, drafting local posts, surfacing unanswered questions. Draft-only until
it earns trust.

**Not started because it cannot be verified.** The GBP connector has no
credentials — Google has not approved API access — so every part of this would
be written against mocks and shipped untested against reality. Two of its five
jobs also need the franchise CRM: "completed jobs eligible for a review
request" is a CRM query, not a Google one.

Build it when the approval email lands.

### 5 — Scorecard (not built, and last for good reason)

Organic leads, booked inspections from organic, estimates and sold jobs from
those leads, cost per qualified lead, visibility by city/service, GBP calls and
actions, content that influenced leads.

**Today we can produce roughly one row of that honestly.** Every other line
needs either Search Console/GA4 (organic and visibility), the GBP API (calls
and actions), or the franchise CRM with stage-transition dates and real source
attribution (leads, inspections, sold jobs, influence).

Building it now would produce a dashboard of blanks and estimates, which is
worse than no dashboard — it invites people to read the estimates as numbers.
The right measure of readiness is the six CRM questions below, not our appetite
to ship it.

**Agreed anti-metrics:** post count, impressions alone, and "AI activity" are
not success. Nothing in the scorecard should reward volume.
