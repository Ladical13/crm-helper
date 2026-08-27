# Marketing connections — setup checklist

Read-only data connections for the Nimbus marketing agents. Status lives at
**Nimbus → 🔌 Connections** (admin only).

**Nothing in this file is a secret, and nothing secret should ever be added to
it.** Keys, tokens and service-account files go into Railway environment
variables and nowhere else — not into this repo, not into a chat window, not
into a code comment.

---

## Status at a glance

| Connection | Tier | State |
|---|---|---|
| Website / sitemap | Active | ✅ Connected, no credential needed |
| **GDELT** — Colorado news + storms | Active | ✅ Connected, no credential needed |
| **Bing Webmaster Tools** | Active | Needs two env vars — **the only measured search data we can own** |
| ~~**Reddit** — customer language~~ | **Gated by Reddit** | ⛔ Needs Reddit's written commercial approval — not obtainable |
| Google Business Profile | Active | Pending Google's API access approval |
| Google Search Console | **Optional — owner access required** | Franchise-owned; we hold no role |
| Google Analytics 4 | **Optional — owner access required** | Franchise-owned; we hold no role |

### Reddit — blocked by Reddit, do not retry

**Attempted 2026-08-11 and abandoned.** The connector is built, tested and
switched off. Do not spend time on `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`:
setting them will not help.

Reddit's [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
now states:

> "**Approval is required**: You must request access and get explicit approval
> before accessing any Reddit data through our API"

and separately, for commercial use:

> "you'll need to get **explicit written approval**… our team will be in touch
> if your proposal fits our criteria."

Marketing research for a roofing company is commercial use. The old self-serve
script-app flow at `/prefs/apps` still renders its form, which is why it looks
like it ought to work — but submitting it bounces you to the Devvit developer
platform, which exists for building apps that run *on* Reddit and is not this.

**Still fine:** a person reading r/Denver, r/FortCollins or r/HomeImprovement in
a browser for content ideas. It is automated API access that is gated, not
reading the site.

The code stays in `agents/content/sources/reddit.py` — if approval ever arrives
it is two env vars away. `test_reddit_reports_the_approval_gate_not_missing_env_vars`
stops the Connections page presenting it as merely unconfigured.

### Bing Webmaster Tools — the one that un-blinds search

Search Console is owner-gated at the franchise. **Bing is not** — verification
is independent of Google, so a meta tag through the CMS makes the property
ours outright, with nobody outside the Colorado team involved.

1. **bing.com/webmasters** → Add site → `https://projectoneroofingcolorado.com`
2. Verify with the **meta tag** option, pasted into the site `<head>` via the CMS
3. **Settings → API access → API Key** → generate

| Variable | Value |
|---|---|
| `BING_WEBMASTER_API_KEY` | The generated key |
| `BING_SITE_URL` | The site exactly as Bing lists it |

Once connected, the weekly report gains a **Search performance (Bing only)**
section with real queries, impressions, clicks and average position — the only
measured numbers in the whole system. Every figure is labelled Bing and never
presented as "search": Bing is a minority of search, and a reader who takes a
Bing impression count for a Google one has been misled just as surely as by an
invented number.

One honest limit: Bing's basic API returns a current window, not a
period-over-period delta, so the report shows *what earns clicks* versus *what
is seen and ignored* rather than a trend. "Pages losing visibility" still needs
Search Console.

### GDELT — nothing to configure

Free public endpoint, no key, no account. It rate-limits transiently (HTTP
429); the client retries once and then reports it as a lost input rather than a
failure. One press release syndicated across dozens of outlets collapses to a
single signal, so nobody with a wire budget can set our content agenda.

**A quiet GDELT shows as amber "Source Unavailable", not a red error.** It
rate-limits *and* times out — observed unreachable for minutes at a stretch on
2026-08-26 — and there is no credential on our side to fix when it does, so a
red row would send someone hunting a fault that is not here. Amber is also not
"Connected": no data came back, and the run lost that input. A quiet *week*
(reachable, nothing to report) stays green.

That row was a red `Error` from the day it shipped until 2026-08-26 for an
unrelated reason: `connections.py` is a top-level module of `agents`, so its
`from ..content.sources import news_gdelt` reached past the package and every
probe died on `ImportError`. Only the Connections page was affected — the
weekly jobs import the same module correctly and always did. The probes import
their sources lazily, inside the function, which deferred the failure to the
one moment nobody watches; `test_every_deferred_import_in_connections_actually_resolves`
now resolves every deferred import in that file with no network and no probe.

**Search Console and GA4 are optional future enhancements, not broken
connections.** Confirmed 2026-08-10: both consoles show an empty screen for
`luke@projectoneroofing.com`. Nimbus displays them as *Owner access required*,
muted rather than red, and no marketing work waits on them — see
[MARKETING_PLAN.md](MARKETING_PLAN.md). The setup steps below stay documented
because the day someone grants access, setting the env vars is the entire
change.

## 1. Which connections use a read-only service account

Both share **one** service account, so there is one key to create and one key
to rotate. **Both are currently owner-gated** — the service account exists and
works; nobody has been able to grant it access.

| Connection | Read-only scope | Who must grant |
|---|---|---|
| Google Analytics 4 | `analytics.readonly` | A franchise property **Administrator**. No self-serve route exists |
| Google Search Console | `webmasters.readonly` | A franchise **Owner** — or self-verify a URL-prefix property via a CMS meta tag, which needs nobody |

A service account is the right fit here: it is not tied to a person, it can be
granted view-only access inside each product, and it needs no browser consent.

## 2. Which connections require a Google OAuth login

| Connection | Why |
|---|---|
| Google Business Profile | Google's Business Profile APIs **do not support service accounts.** Access must be authorised by a Google account that manages the profile. |

Two things to know before starting GBP:

- **There is no read-only scope.** Google publishes exactly one scope for these
  APIs, `business.manage`, and it can write. Read-only for GBP is enforced by
  Nimbus — `agents/connections.py` only ever issues GETs, and a test fails the
  build if a write verb appears in it. That is a weaker guarantee than GA4's
  and GSC's, where Google itself refuses writes. Worth knowing before granting.
- **Access needs Google's approval.** Beyond enabling the APIs you must submit
  Google's Business Profile API access request form. Approval is not instant.

**Website / CMS** needs no credential at all in v1 — it reads the public
`sitemap.xml`. No CMS login, nothing to grant, nothing to revoke.

## 3. Railway environment variables

Set these in the Railway dashboard on the `project-one-estimator` service
(Variables tab). Exact names — the app reads these and no others.

### Shared service account (GA4 + Search Console)

| Variable | Contents |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | The service-account JSON key file, base64-encoded |

`GOOGLE_SERVICE_ACCOUNT_JSON` (raw JSON) also works, but base64 is preferred:
the private key inside contains newlines, and pasting it raw into a dashboard
field is where it usually gets mangled.

Encode the key **straight to the clipboard**, so it never appears on screen or
in shell history:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\Downloads\key.json")) | Set-Clipboard
```

Paste into Railway, then delete the downloaded `.json` file. Do not commit it,
do not move it into the repo, do not paste it into a chat.

### Per-connection IDs and credentials

| Variable | Connection | Secret? |
|---|---|---|
| `GA4_PROPERTY_ID` | Google Analytics 4 | No |
| `GSC_SITE_URL` | Search Console | No |
| `GBP_OAUTH_CLIENT_ID` | Business Profile | Yes |
| `GBP_OAUTH_CLIENT_SECRET` | Business Profile | Yes |
| `GBP_OAUTH_REFRESH_TOKEN` | Business Profile | Yes |
| `GBP_ACCOUNT_ID` | Business Profile (optional) | No |
| `GBP_LOCATION_ID` | Business Profile (optional) | No |
| `MARKETING_SITE_URL` | Website (optional) | No |
| `BING_WEBMASTER_API_KEY` | Bing Webmaster Tools | Yes |
| `BING_SITE_URL` | Bing Webmaster Tools | No |
| `REDDIT_CLIENT_ID` | Reddit | Yes |
| `REDDIT_CLIENT_SECRET` | Reddit | Yes |
| `ANTHROPIC_API_KEY` | Supervisor (the 🧠 tab) | Yes |
| `NIMBUS_SCHEDULER` | Set to `1` to run the weekly jobs | No |

**`ANTHROPIC_API_KEY` is the only credential the Supervisor needs.** Without
it the tab loads and says so rather than erroring, and nothing else in Nimbus
changes behaviour. Get one at <https://console.anthropic.com> → API keys. Its
spend is capped separately from research spend
(`supervisor_monthly_cap_usd`, default $50) — see MARKETING_PLAN.md.

**`NIMBUS_SCHEDULER=1` is what makes "weekly" mean weekly.** Without it every
run is a button somebody has to remember to press. It is opt-in rather than
default-on so that importing the app — which the test suite does — never
starts background work by surprise.

`MARKETING_SITE_URL` defaults to the website in `marketing_profile.json`, so
you only need it if the two should differ.

## 4. The non-secret IDs you need to find

These are identifiers, not credentials. The Connections page displays them back
to you so you can confirm you pasted the right value.

### `GA4_PROPERTY_ID`
GA4 → **Admin** → **Property Settings** → *Property ID*.
Digits only — `123456789`, not `properties/123456789`. (The app strips a
`properties/` prefix if you include it anyway.) This is **not** the
`G-XXXXXXX` measurement ID, which is a different thing and will not work.

### `GSC_SITE_URL`
Search Console → the property picker at top left. The format depends on the
property type and must match exactly:

- Domain property → `sc-domain:projectoneroofingcolorado.com`
- URL-prefix property → `https://projectoneroofingcolorado.com/` (**with** the
  trailing slash)

### `GBP_ACCOUNT_ID` and `GBP_LOCATION_ID`
**Both are optional, and you discover them by connecting** — which is why
neither gates the probe. Set the three OAuth variables, and either:

- run `python -m agents.scripts.gbp_oauth`, which prints both at the end; or
- hit **Re-check** on the Connections page, which reports the account IDs it
  can read in the status line.

Set `GBP_ACCOUNT_ID` only to pin reads to one account. Leave `GBP_LOCATION_ID`
unset to read every location.

If the lookup comes back HTTP 403, that is the expected state while the
Business Profile API access request is still pending — the refresh token is
fine, Google just hasn't granted quota yet.

## 5. Verifying each connection without changing live data

Every check below is a single HTTP **GET**. None of them writes, publishes, or
modifies anything.

**The easy way:** Nimbus → 🔌 Connections → **Re-check all**. Each connector
reports Connected / Not Connected / Error with a reason. That is the same set
of calls listed here.

| Connection | Verification call | Proves |
|---|---|---|
| GA4 | `GET analyticsdata.googleapis.com/v1beta/properties/{id}/metadata` | The SA can read that property |
| Search Console | `GET webmasters/v3/sites/{siteUrl}` | The SA is a user on that property; returns its permission level |
| Business Profile | `GET mybusinessaccountmanagement.googleapis.com/v1/accounts` | OAuth works and the account manages ≥1 profile |
| Website | `GET {site}/sitemap.xml` | The sitemap is reachable and parseable |

Reading the reasons:

- **`HTTP 401/403`** — authentication worked, authorisation did not. The
  credential is valid but has not been granted access to that specific
  property. Re-check the sharing step, and give Google a few minutes.
- **`HTTP 404`** — the ID is wrong. For Search Console this is almost always
  the `sc-domain:` vs `https://` format, or a missing trailing slash.
- **`HTTP 403` on Business Profile only** — often means API access has not been
  approved yet, rather than a permissions problem.

---

## Getting the GBP refresh token safely

One-time, run on your own machine. The token is printed to **your local
terminal** and goes straight into Railway — it never touches this repo or a
chat window.

1. Google Cloud Console → **APIs & Services → Credentials** → *Create
   credentials* → **OAuth client ID** → application type **Desktop app**.
2. Note the client ID and client secret (leave them in the browser for now).
3. Run the helper, which starts a local loopback listener and opens Google's
   consent screen:

```bash
python -m agents.scripts.gbp_oauth
```

4. Sign in as the Google account that manages the Business Profile and approve.
5. The script prints the refresh token to your terminal. Copy it into Railway
   as `GBP_OAUTH_REFRESH_TOKEN`, along with the client ID and secret.
6. Close the terminal. The script writes nothing to disk.

---

## Rotating or revoking

- **Service account key** — Cloud Console → the service account → *Keys* →
  add a new key, update `GOOGLE_SERVICE_ACCOUNT_JSON_B64`, redeploy, then
  delete the old key. Revoking access entirely: remove the SA's access in GA4
  and Search Console, which takes effect immediately.
- **GBP OAuth** — the granting Google account can revoke Nimbus at
  <https://myaccount.google.com/permissions>. That invalidates the refresh
  token immediately.

## Scope of v1 — what these connections may NOT do

Read-only, enforced in code and by tests in `agents/tests/test_connections.py`:

- no publishing (no Business Profile posts, no review replies)
- no email sending
- no website or CMS editing
- no CRM writes

Adding any of those is a deliberate, separate change — not a config tweak.

## Still unspecified

**Colorado marketing data.** Listed as something to connect, but the system it
lives in has not been named — spreadsheet, ad account, BI export, something
else. It appears on the Connections page as an open item so it does not get
forgotten. Name the system and it becomes a real connector.

---

## B2B lead sources — what actually feeds a rep's queue

Separate from the marketing connections above. These are the sources behind
**Nimbus → a rep's tab → Run now**, registered in
`agents/b2b/sources/__init__.py` as *free first, Perplexity last*.

| Segment | Free source | Credential | Gives |
|---|---|---|---|
| `church` | IRS Exempt Organizations BMF | none | name, street, city, ZIP, **EIN** |
| `school` | NCES CCD via the Urban Institute API | none | name, street, **phone**, district, enrollment |
| `school_district` | NCES CCD district directory | none | district, **phone**, admin office, schools + pupils in territory |
| `commercial` | Larimer + Weld county assessors, then CO Secretary of State | none | owner, building address, sq ft, year, roof shape, **owner's mailing address**, often a **person's name** |
| `gc` | — (`cdle.py` is still a placeholder) | — | falls through to Perplexity |
| `realtor`, `insurance_agent`, `hoa`, `property_manager` | the offline `prospector/` open-data path | none | — |

### `commercial` — who owns the building is public record

Larimer publishes eight CSV extracts on Google Cloud Storage; Weld runs an
ArcGIS FeatureServer that takes a WHERE clause, so that half pulls one city
instead of a county. Measured 2026-08-26: **10,061 Larimer commercial
improvements** (2,457 flat-roofed, 837 of those 10,000+ sq ft) and **6,172
Weld commercial accounts**.

**Read the roof fields carefully.** `ROOFTYPE` is the *shape* (Flat / Gable /
Shed) and is filled for 68% of Larimer commercial rows — that is the useful
one, and a flat roof is what makes a building re-roofable. `ROOFCOVER` is the
*material* and is filled for 98% of **residential** but only **3.6%** of
commercial, so it is read when present and never depended on. Weld publishes
neither, nor year built. Absent must never score as old-and-flat, or every
unknown building floats to the top of a rep's queue — pinned by
`test_an_unknown_roof_is_not_scored_as_an_old_flat_one`.

Because Weld carries no roof or year data, its rows top out around 4 where a
Larimer row can reach 8. In a per-city run that is invisible. In a run
spanning both counties, Larimer cities will take the enrichment budget first.

**The Secretary of State turns the LLC into a person.** The assessor says
"EJS HOLDINGS LLC" owns it; the state's free business registry (3.1M
entities, 71% carrying an agent name) says that is Elizabeth Sampson. Looked
up only for the rows actually being returned — resolving every parcel in a
county would be thousands of calls to answer a question nobody asked. Three
traps, all found against live data and all pinned by tests: a prefix LIKE on
"HARMONY ROAD" returns a vet clinic and a massage therapist, so candidates
are matched back on a normalised core; the registry never deletes, so a
dissolved shell with the same name must lose to the live entity; and a
corporate agent service ("Registered Agents Inc") is a vendor's name, which
is worse than no name because a rep would ask for it at the front desk.

### `school_district` — the account above the schools

A principal does not buy a roof. Pulling Northern Colorado produced **116
school cards against 7 real accounts**, three of which held 99 of the schools
— Poudre alone is 42 buildings and 24,963 pupils under one facilities
director. The district directory carries the administration office's phone,
which is the number worth dialling. Counts are of the schools **inside the
requested city**, not the district's statewide totals, which would oversell a
district that only reaches a little way into the territory.

### Still falling through to Perplexity

`gc` (general contractors). Colorado has no statewide GC licence, so this
needs each city's contractor registry or building-permit feed aggregated.
Fort Collins is the only Northern Colorado city found publishing permits as
open data, and on 2026-08-26 its whole portal answered HTTP 503 and its
ArcGIS server reported "Could not access any server machines" — so the
schema has never been seen and nothing was written against it. Greeley and
Loveland publish no equivalent feed. Deliberately left unbuilt rather than
shipped blind.

Measured on the live files, 2026-08-26: **1,928 Colorado schools, every one
with a phone number**, and **3,988 active congregations, every one with a
street address** (PO-box-only rows are dropped — a PO box cannot be roofed).

**Why this replaced a Perplexity call per city.** Before it, every b2b
candidate came from `perplexity_gap`, and a live sample was 1-in-5 for a phone
number and 0-in-5 for an email. The model is told not to invent contact
details and complies by writing "unknown", which used to reach the rep as a
phone number to dial. The federal identifiers matter as much as the contact
data: `source_ref` is `irs_bmf:co:<EIN>` and `nces:<NCESSCH>`, so re-running a
pull is idempotent in a way a model's spelling of an organization's name can
never be.

Both files are cached on the volume under `AGENTS_DATA_DIR/opendata/` for 30
days, because they are tens of megabytes and change monthly at most. A failed
download returns no rows rather than raising, so the run falls through to
Perplexity and costs one input instead of the segment.

**Known limit: the BMF address is the organization's *mailing* address**,
which for a small congregation is often the treasurer's house rather than the
building. A row whose city looks wrong for its name is usually that, not a
parse error. The BMF carries no phone, no email and no website at all —
churches arrive as a name and a door, which is how partner development at a
church starts anyway.
