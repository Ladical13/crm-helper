# Project One Roofing

Four apps in one repo, served as **one site behind one login**: the portal
(launcher + accounts), the canvasser, the sales CRM, and the estimator. Plus
`prospector/`, an offline tool that feeds the CRM partner lists.

## Working on this repo

```bash
pip install -r requirements-dev.txt   # one-time, covers everything
python -m portal.wsgi                 # run it all locally on :5010
python run_tests.py                   # all four suites, the way CI runs them
```

Then open <http://localhost:5010> — canvasser at `/canvass`, CRM at `/crm`,
estimator at `/estimate`.

**Saving work.** Upstreams are set, so day to day it is just:

```bash
git add -A && git commit -m "what changed" && git push
```

Every push runs the four suites on GitHub (**Actions** tab). Green means the
pricing math, the cache-busters and the per-rep visibility rules all still hold.

**Before committing, run `python run_tests.py`.** It is the same four commands
CI runs, and it is much faster to find a break here than in the Actions log.
Individual suites, when you only touched one app:

```bash
cd estimator && pytest      # 400 — pricing parity, cache-buster, bundles
cd salescrm  && pytest      #  85 — pipeline, prospecting, queue, drafts
cd portal    && pytest      #  28 — one login across all three, migration
python -m pytest prospector/tests   # 27 — offline, no network
```

**Deploying.** ONE Railway service, whole repo — see the deploy note at the end
of the portal section. Never deploy a subdirectory.

**Nothing is backed up yet — open gap, decided 2026-07-29 to defer.** Two
separate things, and they are easy to confuse:

- *On this laptop*, `estimator/estimates/`, every `*.db`, and
  `prospector/inbox/` are gitignored, so a push never includes them. But these
  are mostly **dev scratch** (a few MB) — losing them costs little.
- *The real data* — live estimates, signed contracts, leads, canvasser pins —
  lives on the **Railway volume**, not here. Nothing currently copies it
  anywhere. A dead volume, a bad migration or a fat-fingered delete loses it
  outright, and pushing to GitHub does nothing to protect it.

When picking this back up: the shape that fits is a manager-only export
endpoint plus a scheduled pull into OneDrive (`C:\Users\ldurn\OneDrive` exists).
Back up the volume before any migration regardless, as the estimator and CRM
notes below already warn.

## The Portal — one login, one site (`portal/`)

The three rep tools used to be three Railway services with three passwords.
Since 2026-07-27 they are **one origin, one login, one PWA icon**, with an
app-switcher bar across the top of all three.

```bash
pip install -r requirements-dev.txt   # one-time, covers all three apps
python -m portal.wsgi                 # local dev on :5010 (all three mounted)
cd portal && pytest                   # 28 tests
```

`portal/wsgi.py` mounts the three **unchanged** Flask apps with
`DispatcherMiddleware`:

```
/            portal      login, launcher, user admin, compat redirects
/canvass/*   canvasser
/crm/*       salescrm
/estimate/*  estimator
```

Every route in every app stays registered at its own root — prefix mounting is
what avoided renaming ~130 colliding routes. Rules that keep it working:

- **All four apps must call `portal.session.configure(app)`.** They share one
  cookie and each re-saves it whenever it touches `session`; one mismatched
  `SESSION_COOKIE_SECURE` logs reps out at random.
- **Identity is `portal/users.py` only** (SQLite `PORTAL_DATA_DIR/portal.db`).
  Never reintroduce a per-app user table or login route. `is_admin()` is strict
  admin; `is_manager_up()` is manager-or-above — both meanings are load-bearing.
- **Front ends derive their prefix from the URL** (`const BASE = ...`), so the JS
  bundle works mounted or standalone. New `fetch` calls go through the app's
  `api()` helper (or the estimator's `window.fetch` wrapper); calls to the
  *portal's* API use `portalApi()`, which deliberately omits BASE. Note the
  **`index.html` files do hardcode their mount prefix** on the `<link>`/`<script>`
  tags, so serving an app at the root anyway gives an unstyled page — mounted is
  the only path that is actually exercised.
- **Service workers scope to their mount** via `self.registration.scope`. Do not
  hardcode `scope: '/'` — the estimator's and the CRM's would fight.
- **`/sign/<token>`, `/sign-co/<token>`, `/uploads/<f>` stay at the root** as
  redirects in `portal/app.py`. Signed-contract links already in customers'
  inboxes point there. Never remove them.
- **Set `CANVASSER_DATA_DIR` and `SALESCRM_DATA_DIR` explicitly** — both fall
  back to `DATA_DIR`, which is the estimator's volume.

**Migration:** `python -m portal.migrate_users` (dry run) → `--apply`. Merges the
three old stores on lowercase username, estimator password wins. Must be run on
the volume before the cutover deploy.

**Deploy:** ONE service. Root `Procfile` is
`gunicorn portal.wsgi:application`; deploy the whole repo, not a subdirectory.

**CI:** `.github/workflows/tests.yml` runs all four suites on every push and PR.
They run as four separate pytest invocations — one run collecting two apps
collides on the bare module name `conftest`. It installs **node**, because the
estimator's parity and fastening tests `skipif` it is missing and would
otherwise go green without checking pricing at all; a final step fails the run
if any suite reported a skip.

## Sales CRM — "The Pipeline" (`salescrm/`)

A sales-driven CRM that sits at the **top of the funnel** (The Den/Base44 is
production-centric; this is the outreach + pipeline layer). Same stack as the
canvasser: **Flask + SQLite + PWA**, mounted at `/crm` by the portal. Storage is
a single SQLite file (`SALESCRM_DATA_DIR/salescrm.db`, gitignored — back it up
before any migration). No pricing math lives here.

```bash
cd salescrm && pytest                 # 85 tests, <2s
python -m portal.wsgi                 # dev: run the portal, CRM is at /crm
```

`python salescrm/app.py` still boots, but `static/index.html` hardcodes the
`/crm` prefix on its CSS and JS, so the standalone page loads unstyled. Develop
against the portal.

**Core model:** `leads` move through ordered `STAGES`
(`new → contacted → appt_set → inspected → estimate_presented → follow_up → won/lost`);
`activities` (immutable timeline), `tasks` (the "next action" engine, drives My Day),
`cadences` (follow-up templates in `cadences.json` that auto-materialize the next task),
`coaching_notes` + `goals`. Lead types differentiate homeowners from partners
(realtors/HOAs/insurance agents/property managers — the **Partners** view tracks
referrals via `referred_by`). Objection/script library is `playbook.json`.

**Visibility:** reps see only their own leads; `is_admin` (manager) sees everyone +
the Numbers/Coaching tabs. Enrollment is the portal's job — there is no signup or
login route left in this app, and `SALESCRM_SIGNUP_CODE` is gone (`PORTAL_SIGNUP_CODE`
bootstraps the first admin; after that, admin-created invite links).

**Pipeline search runs on the server** (`?q=` → SQL `LIKE`, escaped so a typed
`%` stays literal). It used to filter the fetched page in the browser, which
silently searched only the most recently updated 1000 leads — invisible with a
few hundred homeowners, and hid most of the table once prospecting imported
partners by the thousand. Don't move it back to the client. The board debounces
input and drops out-of-order responses, and deliberately does **not** overwrite
`S.leadCache` while a search is active: the sidebar stage counts and the drawer's
"referred by" list read that and want the whole pipeline, not the current match.
Guarded by `test_search_reaches_past_the_limit_window` and
`test_search_treats_wildcards_literally`.

**Integrations (the Base44 `contact_id` is the shared join key):**
- **→ The Den:** moving a lead to **`won`** auto-runs `_push_to_den()` — creates a
  Base44 `Contact` + `Project` and stores `crm_contact_id`/`crm_project_id` back on the
  lead (dedups on phone/email first). Adapted from `crm_sync()` in `canvasser/app.py`.
  `POST /api/leads/<id>/convert?dry_run=1` returns the payloads without writing.
- **↔ Estimator:** `POST /api/leads/<id>/start-estimate` ensures a Base44 contact exists,
  then hands back the estimator URL (the estimator's own contact search picks it up).
  `GET /api/leads/<id>/estimate` reads job/document status back by `contact_id`.
- Reuses the shared `BASE44_TOKEN` env var. Den calls degrade gracefully when unset.

**PWA cache-buster:** any `app.js`/`style.css` change must bump `?v=N` in
`static/index.html` (×2) **and** `static/sw.js` (`CACHE` + `SHELL`) — 5 spots, or PWA
clients keep the stale bundle. Bumped **by hand**: unlike the estimator, this app has
no `bump_version.py` and no test guarding it.

**Deploy:** nothing CRM-specific. Since the portal merge this ships with everything
else as the ONE service described above — the old standalone `project-one-crm`
service is retired, so don't deploy to it. App-specific env: `SALESCRM_DATA_DIR`
(set it explicitly — it falls back to the estimator's `DATA_DIR`), plus optional
`ESTIMATOR_URL`, `SALESCRM_DAILY_TARGET`, `SALESCRM_COOLDOWN_DAYS`,
`SALESCRM_STALL_DAYS`. `BASE44_TOKEN` and `SESSION_SECRET` are shared.

### Partner prospecting (`prospector/` + the import path)

Feeds the partner queue from **free Colorado open data** — no API keys, no
per-contact credits. `prospector/README.md` has the segment table and counts.

```bash
python -m prospector segments --count                  # live counts
python -m prospector pull dora:hoa --out prospector/inbox/hoa.json
python -m prospector push prospector/inbox/hoa.json --user luke --dry-run
python -m pytest prospector/tests                      # 27 tests, offline
```

- **`prospector/` is deliberately dumb.** It doesn't know what's already in the
  CRM, doesn't filter opt-outs, doesn't assign reps. Dedupe, suppression and
  assignment all live server-side in `app.py`, which is what makes **re-running
  any pull safe**. Import is idempotent: the same rows twice insert nothing.
- **Dedupe lives in `/api/prospects/import` and nowhere else.** `POST /api/leads`
  stays duplicate-friendly on purpose — the cross-sell "Pitch" button creates a
  second lead for the same person as a separate deal. Matching on contact details
  there would break it. Guarded by
  `test_cross_sell_still_creates_a_second_lead`.
- **`source_ref` is the load-bearing dedupe key**, not phone/email. Most open-data
  rows have *no* contact details — a DORA HOA record is a name, a city and a
  licence — so `source_ref` (`dora:4zse-6bnw:51739`) is the only stable thing to
  match on. A row with no phone, email, licence *or* `source_ref` is rejected
  rather than imported, because it would duplicate on every future run.
- **`phone_norm`/`email_norm` are written on create AND update.** `_norm_phone`
  collapses `(970) 555-1212`, `970-555-1212` and `+1 970 555 1212` to one value.
  Edit `phone` without updating `phone_norm` and the row dedupes against the
  number it replaced. Unrelated to `_find_existing_contact`, which does its own
  exact-string match against Base44 and stays that way.
- **Suppression beats everything** and is checked on import *and* on every queue
  build — an opt-out must not resurface tomorrow from a different dataset.
  Adding one also sets `dnc=1` on matching existing leads. Removing one is
  manager-only.
- **Always `--dry-run` first.** It classifies every row, reports intra-batch
  duplicates exactly as the real run would, and writes nothing.
- **`dora:broker` (40,264 individual brokers) is off by default.** DORA publishes
  no contact details, so that segment is the paid-enrichment tier. Work
  `dora:brokerage` — one office visit reaches every agent in it.
- New table? **Add it to `tests/conftest.py TABLES`** or state leaks between
  tests (the temp DB is per-session, not per-test).

### The outreach queue (⚡ Outreach tab)

`GET /api/queue/today` is what a rep works: **cadence re-touches due today plus
just enough net-new cold cards to reach the daily target** (`SALESCRM_DAILY_TARGET`,
default 40). `POST /api/queue/assign` hands a manager's imported batch out
round-robin.

- **Re-touches count toward the number, and that's the design.** For partner
  development repeat contact beats unique contact, so about half the day is
  people already met. That also halves net-new sourcing demand, which is what
  makes ~36k free records last. Guarded by
  `test_due_tasks_appear_and_count_against_the_target`.
- **Three filters run on every queue build**, not just at import: `dnc`, live
  suppression re-check, and the `SALESCRM_COOLDOWN_DAYS` (default 7) window. A
  domain suppressed this morning drops leads imported last week.
- **Assignment only moves untouched imported leads** (`last_activity_at=''`,
  `import_batch!=''`) so a rep never loses a partner they've spoken to and a
  hand-entered lead is never reassigned out from under them.
- **Every card action logs through `POST /api/leads/<id>/activities`**, so the
  leaderboard counts the day with no new reporting code. A *skip* deliberately
  logs nothing.
- **`leads_queue_idx` (`rep, stage, icp_score DESC, created_at`) is what keeps
  the net-new top-up cheap.** Without it SQLite picks `leads_stage_idx` and
  scans every `new` lead — and in a prospecting DB almost everything is `new`,
  so that index selects nothing. It also satisfies the ORDER BY, dropping a temp
  B-tree sort over the whole candidate set. Measured at 36k leads: 19ms → 5ms.

### Outreach drafts (`outreach_templates.json`)

Per `lead_type`, three steps chosen by prior outreach count (0 → `first`,
1–2 → `followup`, 3+ → `breakup`). Rendered server-side by `_render_draft`.

- **Draft only, never sent.** The card opens a *Gmail compose window in the
  rep's own Workspace account* (`gmailUrl()` in `app.js`). Because the mail is
  genuinely 1:1 from a real person, this needs **no sending subdomain, no
  SPF/DKIM/DMARC work and no warmup** — do not "improve" it into bulk sending.
- **`{hook}` sits in its own paragraph** so an unresearched lead gets a shorter
  email instead of a visible gap. `_fill()` drops paragraphs left empty.
- **A nameless lead gets "Hi there,"** — most HOA records are a company with no
  person, and "Hi ," on 8,500 emails is the kind of thing reps get blamed for.
- **`banned_phrases` is enforced by a test.** Add a template that opens with
  "just checking in" and the suite fails.
- Templates load at import — **editing the file needs a process restart**.

## Estimator — tests & invariants

```bash
cd estimator && pytest                # 400 tests, <6s
```

**Run `pytest` before every estimator commit.** Two invariants it guards, both
of which have already broken once:

1. **Pricing parity (`tests/test_parity.py`).** Pricing math is implemented
   twice on purpose — the rep's browser needs instant recalc as they type, and
   the server must compute money independently because it can't trust the
   client (PDFs, customer view, signed contracts). That duplication is *by
   design and staying*. The parity test prices the same fixtures with the real
   `app.js` functions (extracted and run under node) and with `app.py`, and
   fails if they disagree by a cent. If it fails, you changed pricing in one
   file and not the other.
   - Rate rule, mirrored both sides: per-trade override → tier rate → global
     rate → `DEFAULT_RATE` (35). A source counts only if it parses as a number;
     `0` counts, `None`/`''`/junk fall through. **The default is 35, never 0** —
     a 0% fallback silently sells a roof at cost.
   - Mode rule: absent `mode` means margin; any non-`'margin'` value means markup.
   - `parity_runner.js` extracts functions from `app.js` by name. Rename or
     restructure `tierRate`/`tradeTotal`/`grandTotal`/`selectedTotal`/
     `effectiveTradeMode` and the runner must be updated — it fails loudly
     rather than silently passing. It also lifts `RETAIL_TRADE_KEYS` and
     `SIMPLE_MODE_TRADES` out of `app.js` rather than restating them, so a new
     trade can't go unpriced on one side only.

2. **PWA cache-buster (`tests/test_assets.py`).** The version appears in 5
   spots across `index.html` and `sw.js`. Never edit them by hand:
   ```bash
   python bump_version.py           # v103 -> v104
   python bump_version.py --check   # verify they agree
   ```
   Any `app.js`/`style.css` change needs a bump or PWA clients keep the stale
   bundle.

Tests run against a temp `DATA_DIR`, so they never touch real estimates.
`estimator/estimates/` is gitignored — there is no git safety net for that data;
back it up before any migration.

### Commercial estimates (third estimate type)

`🏠 Retail | 🏛 Insurance | 🏢 Commercial` in the sidebar. Commercial mode turns
off every other trade, hides the steep-slope roof + attic-ventilation panels,
and lands the rep on **Scope** (the bid is driven by the EagleView numbers).
Tests: `tests/test_commercial.py`, plus commercial fixtures in `test_parity.py`
and `test_bundles.py`.

- **`commercial` is a bundle trade that defaults to `mode:'simple'`** — one
  system, one price. The rep can still flip it to G/B/B per estimate.
  `SIMPLE_MODE_TRADES` (`app.js`) / `SIMPLE_MODE_TRADES` + `_trade_mode()`
  (`app.py`) are the mirrored pair that decides this. **A bundle trade in
  simple mode must build FLAT items** (`unit_cost`/`unit_price`) via
  `buildSimpleItemsFromBundle`; per-tier items in a simple trade total **$0**
  while looking completely normal on screen.
- **Both labor lines ship in every commercial bundle.**
  `measurements.comm_work_type` (0 = re-roof @ $400/SQ, 1 = new construction @
  $250/SQ) zeroes the one that doesn't apply — zero-qty lines never price and
  never print. No new pricing math, so nothing to mirror.
- **Measurements use their own `comm_*` namespace** so a flat roof can never
  inherit a steep-slope number. Mirrored in `MEASURE_FIELDS` (`app.js`) and
  `MEASURE_LABELS` (`app.py`).
- **Material costs ship as `0` placeholders on purpose** — commercial pricing
  comes off the supplier quote per job. Only the two labor rates are real. The
  pricing tab shows a red banner while every line is still $0.
- **Print gates on `estType !== 'insurance'`, never `=== 'retail'`.** The old
  form printed a blank PDF for any new type.
- Complexity flags (`S.commercial.flags`) are rep-only and **never price** —
  they ride to the production packet via `COMM_FLAG_LABELS`.

### Commercial fastener calculator

Fastener density is set by roof zone, so `commercialFastening(m, table)` /
`commercial_fastening(m, table)` compute counts per ASCE 7 zone (field /
perimeter / corner) × layer (insulation boards, membrane seam). Tests:
`tests/test_fastening.py` (the math), `tests/test_fastening_wiring.py` (catalog,
migration, packet).

- **The table is DATA, not a mirrored constant.** `commercial_fastening.json` is
  served by `/api/commercial-fastening` and fetched into `_fastenTable`; only the
  *algorithm* is duplicated, and `tests/fastening_runner.js` holds the two
  implementations to the same numbers. (Contrast `atticVentilation`, whose
  constants are mirrored in both files — that runner now covers it too.)
- **Pass the table in.** Both functions take `(m, table)` so they stay pure and
  testable. Don't reach for module state inside them.
- **When it doesn't know, it returns 0 and shouts** — red Scope banner,
  `NOT CALCULATED` in the packet. Never a plausible guess. Sending is *not*
  blocked (deliberate), so those two warnings carry the whole weight.
- **`comm_uplift` is the psf number** (FM 1-60 → 60), because `setMeasurement`
  does `parseFloat(v)||0` and can't hold a string. It **starts blank** — no
  default, since a default is a guess about someone else's building. Lookup
  rounds **up**, never down, and sorts keys numerically (`"105" < "60"` as text).
- **`corner_shape` is a table field**: ASCE 7-10 square corners = `4a²`,
  ASCE 7-16 L-corners = `12a²`. A 3× swing in the highest-uplift zone; defaults
  to `L`. **Confirm against the adopted code before trusting it on a real roof.**
- **`comm_insul_layers`: missing means 1, explicit `0` means 0** (a recover with
  no new insulation). Same trap shape as any `parseFloat(v)||0` field.
- **Attachment comes from the product, not the bundle name.** Membranes carry
  `attach: mechanical|adhered|coating`; `_syncCommAttachment()` resolves it
  *after* any rebuild into `comm_seam_attach`/`comm_insul_attach` so the pure
  calculator and the server packet both see it. Unknown **fails closed on seam**.
- **The seeded densities are invented and generic.** `source_note` must keep
  travelling with them into the panel and the packet.
- `_ensure_bundle_catalogs` now backfills seed products by id and swaps
  superseded ones (`_PRODUCT_SUPERSEDED`) into **seeded** bundles only — a price
  book saved before a product existed would otherwise never get it.

### Monthly trends & sales goals

The analytics tab's **📈 Monthly Trends & Goals** panel is the "did we make our
number?" view. Goals live in `DATA_DIR/sales_goals.json` (`/api/goals`, GET for
everyone, PUT manager-up) as a company default plus per-month overrides, and the
same shape per rep. Rules the tests in `tests/test_analytics.py` hold down:

- **No goal is `None`, not 0%.** A month nobody set a target for must not render
  as a failed month.
- **Month override beats the scope default**; roofing is seasonal, so a flat
  monthly number is wrong half the year.
- **Rep names are matched lowercase.** Goals are stored lowercase but
  `salesperson` is whatever was typed — "Luke" must not become a second rep.
- **Two revenue bases, never mixed in one ratio.** A month's `revenue` is
  `_estimate_total` (what was signed, what goals measure); `trade_revenue`/
  `trade_cost` are the per-trade priced figures and exist only for `margin_pct`.
- **`close_rate` is a sent cohort** — of the estimates *sent* that month, how
  many have closed. Signed revenue buckets on the signature date instead.
- **Trailing averages exclude the current month**, which is still partial and
  would drag every benchmark down.
- The series is gap-filled and always reaches the current month, capped at 24.

## Base44 CRM API

### Base URL
```
https://base44.app/api/apps/69320ef0c647fee442697971
```

### Authentication
All requests require a Bearer token header:
```
Authorization: Bearer <BASE44_TOKEN>
```

**Never commit the token to source.** It lives in the `BASE44_TOKEN` environment
variable (set in Railway). Rotation steps and expiry are tracked in the memory note
`base44-crm-token`. Token belongs to `luke@projectoneroofing.com`; a 401 from the CRM
means it has expired or is unset — rotate in Base44 and update `BASE44_TOKEN`.

### Key Endpoints

| Endpoint | Description |
|---|---|
| `/entities/Contact` | Homeowner/customer contacts |
| `/entities/Project` | Roofing projects/jobs |
| `/entities/ContactProperty` | Properties linked to contacts |
| `/entities/Document` | Documents (estimates, contracts, etc.) |
| `/entities/ReferralPartner` | Referral partner records |

### How to Query

**Fetch all records (GET):**
```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://base44.app/api/apps/69320ef0c647fee442697971/entities/Contact"
```

**Fetch single record by ID (GET):**
```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://base44.app/api/apps/69320ef0c647fee442697971/entities/Contact/<id>"
```

**Filtering (query params — verify support as needed):**
```
?assigned_to=luke@projectoneroofing.com
?state=TX
```

### Contact Entity Fields
```
id, name, first_name, last_name, phone, email,
street_address, city, state, zip_code, address (full),
source, notes, assigned_to, is_red_flag_customer,
location_id, converted_lead_id,
created_date, updated_date, created_by, created_by_id, is_sample
```

### Team Members (assigned_to values)
aaron, bryan, casey, chris, chris.rollins, clint, cole, dalton,
derik, eric, gabriel, jacob, jeremy, jonathan, kyle, logan,
luke, richard, ryan, shiloh, ted — all @projectoneroofing.com

### Notes
- Two market locations: **TX** (Tyler/Longview area) and **CO** (Northern Colorado)
- Source values vary — free-text in addition to known values: `referral`,
  `door_knock`, `phone_call`, `website`, `social_media`, `other`
- Python runs this repo, so just use it (`requests`, or `curl` piped to
  `python -m json.tool`) rather than hand-rolling PowerShell JSON parsing.
  Write scratch JSON to the session scratchpad, not the repo.
