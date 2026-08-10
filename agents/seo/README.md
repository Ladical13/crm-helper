# Public-Research Local SEO Strategist v1

Produces a weekly SEO strategy report and a ranked, approval-required
recommendation queue — **without Search Console, GA4, CMS credentials, or
Business Profile API credentials.** Those are owned at the franchise level (see
[../MARKETING_PLAN.md](../MARKETING_PLAN.md)) and nothing here waits on them.

```bash
cd agents && pytest tests/test_seo.py      # 39 tests, no network
```

In Nimbus: **🔍 Local SEO** (admin only). "Dry run" writes nothing at all.

## Inputs

| Input | Source |
|---|---|
| Approved services, service area, banned phrases | `../marketing_profile.json` |
| Our own pages | Polite crawl of `MARKETING_SITE_URL` (or the profile's website) |
| Page metadata + structured data | Parsed from the crawl, stdlib `html.parser` |
| Customer questions, competitor topics | Perplexity, cached and spend-capped |

## Outputs

- A Markdown weekly report saved to `seo_reports`
- Ranked recommendations in `seo_recommendations`, each `pending` until a human
  approves or rejects

Every recommendation carries: category, city/service context, the customer
question or search intent, the action, the rationale, evidence URLs, a
confidence, and what a human must verify first.

### Categories

`improve_existing_page` · `create_service_page` ·
`create_city_or_service_area_page` · `faq_or_content_brief` ·
`internal_linking_opportunity` · `technical_website_fix` ·
`google_business_profile_opportunity`

## The honesty rules, and how they are enforced

Not conventions — code, with tests.

| Rule | Enforcement |
|---|---|
| No rankings, search volume, traffic, conversions, or competitor performance | `honesty._FABRICATED_METRIC_PATTERNS` rejects the recommendation. Eight phrasings are tested |
| Indirect evidence says so | `honesty.label_for()` → "Public-research opportunity" vs "Observed on our site" |
| No claim without a source | A recommendation with no evidence URL is dropped, not softened |
| No invented reviews, certifications, prices, storm events, insurance rules | `honesty.RESEARCH_SYSTEM` forbids it in the prompt; the profile bounds what may be claimed |
| Only approved services | Checked against `marketing_profile.json` |
| Banned phrases | Reuses `config.banned_phrases()` — one list, one place |

`evidence_basis: "owned_data"` is **rejected outright** in v1. No owned
analytics source is connected, so a claim of one is wrong by construction — and
would license exactly the numeric claims the rest of the module prevents.

Rejected items are *reported*, in the manifest and at the foot of the report. A
run that drops half its output must not look like a run that found little.

## Crawl manners

- **robots.txt obeyed**, checked against our own user-agent
- **Identifying user-agent** with a contact URL
- **GET only** — a test greps the package for `requests.post/put/patch/delete`
- **Bounded**: 40 pages, depth 3, 15s timeout, 1s between live requests
- **Same origin only** — competitors are researched, never crawled
- **24h cache** in `seo_page_cache`; a re-run inside the window costs no requests

## Degradation

A run loses inputs rather than collapsing:

| Failure | Result |
|---|---|
| No Perplexity key / spend cap hit | Crawl-derived recommendations still ship; the report says research was skipped |
| No sitemap | Falls back to a link crawl from the homepage |
| robots.txt unreachable | Treated as allow, per the RFC — and noted in the report |
| A page 404s or times out | Becomes a `technical_website_fix` finding |
| **No readable pages at all** | The run fails — with no site there is nothing to reason about |

## What it never does

No publishing, no email, no CMS edits, no Business Profile changes, no CRM
writes. Approving a recommendation records a decision; a human does the work.
