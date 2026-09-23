# Project One Roofing

Four apps in one repo, served as **one site behind one login**: the portal
(launcher + accounts), the canvasser, the sales CRM, and the estimator. Plus
`prospector/`, an offline tool that feeds the CRM partner lists, `hail/`, the
storm archive they all read, and `agents/` (Nimbus).

## Read this first

Four things that will bite you on day one, in the order they cost the most.
Everything else in this file is detail; these are the ones where not knowing
costs money or breaks production before you have finished your first change.

1. **Pushing to `portal-merge` IS deploying.** Auto-deploy fires the moment CI
   goes green, so a push goes straight in front of the reps. There is no
   separate release step to forget. Run `python run_tests.py` first, every
   time, and for anything touching login/session, a DB migration, or pricing,
   agree it with Luke **before** pushing rather than after.

2. **Pricing math is implemented twice, on purpose, and must agree to the
   cent.** The rep's browser needs instant recalc as they type; the server
   cannot trust the client for PDFs, customer pages and signed contracts.
   `estimator/tests/test_parity.py` runs the real `app.js` under node against
   `app.py` and fails on a one-cent disagreement. If it fails, you changed
   pricing in one file and not the other. That duplication is by design and
   staying.

3. **Editing `estimator/price_book.json` does not change live pricing.**
   `_seed_data_dir()` copies the seed files to the volume **only if absent**,
   so on a long-lived volume the repo's copies are inert. Bundle-trade data
   belongs in the `*_SEED` constants in `app.py`, which `_ensure_bundle_catalogs()`
   backfills on every read — that is the only path that reaches production.
   Same trap bites `jurisdictions.json` and friends.

4. **A doc that lies is worse than no doc, so this file is tested.**
   `portal/tests/test_docs.py` checks every CLAUDE.md in the repo: each
   `` `foo()` `` has to resolve, each path has to exist, the suite count has to
   match `run_tests.py`, and headings may not carry dates. It cannot check
   prose — "a ＋ Create New Estimate button that pre-fills from the most recent
   estimate" was every-symbol-correct and simply no longer true. So keep
   volatile UI detail in code comments, where it travels in the same diff as
   the change, and keep these files for invariants and traps that survive a
   redesign.

## Where the rest lives

This file is what applies to **every** app: how to run and deploy the repo,
and the four traps that cross all of it — the portal's mount and session
rules, mobile, the company's clock, and backups.

Per-app detail lives beside the code, in a `CLAUDE.md` that loads when work
touches that directory. That split exists so this file stays short enough to
be read every session: it was one 2,300-line file, of which the estimator was
more than half, and a session working on the canvasser paid for all of it.

| Working on | Read |
|---|---|
| the estimator — pricing, margin, carrier imports, the customer screen | `estimator/CLAUDE.md` |
| the CRM — pipeline, cadences, prospecting, the outreach queue | `salescrm/CLAUDE.md` |
| the canvasser — pins, the outbox, hail overlays, appointments | `canvasser/CLAUDE.md` |
| the storm archive — MESH ingest, the grid, the join | `hail/CLAUDE.md` |
| Nimbus — the agents, networking events | `agents/CLAUDE.md` |

Those files are tested the same way this one is, and the rules here still
apply inside them.

## Working on this repo

```bash
pip install -r requirements-dev.txt   # one-time, covers everything
python -m portal.wsgi                 # run it all locally on :5010
python run_tests.py                   # all seven suites, the way CI runs them
```

Then open <http://localhost:5010> — canvasser at `/canvass`, CRM at `/crm`,
estimator at `/estimate`.

**Saving work.** Upstreams are set, so day to day it is just:

```bash
git add -A && git commit -m "what changed" && git push
```

Every push runs the seven suites on GitHub (**Actions** tab). Green means the
pricing math, the cache-busters and the per-rep visibility rules all still hold.

**Before committing, run `python run_tests.py`.** It is the same commands
CI runs, and it is much faster to find a break here than in the Actions log.
Individual suites, when you only touched one app:

```bash
cd estimator  && pytest     # pricing parity, cache-buster, bundles
cd salescrm   && pytest     # pipeline, prospecting, queue, drafts, assets
cd portal     && pytest     # one login, migration, shell, hardening, these docs
python -m pytest prospector/tests   # offline, no network
cd agents     && pytest     # spend cap, cache, b2b/content sources
cd canvasser  && pytest     # vendored Leaflet, cache-buster, sw wiring
cd hail       && pytest     # grid quantization, units, re-ingest, the join
```

**This file is tested** (`portal/tests/test_docs.py`). Every `` `foo()` `` it
names has to resolve in the codebase and every path it points at has to exist,
because it is the first thing loaded into each session's context — a stale
line here misleads everyone before they have read a line of code, and twice
now it has described a screen that had already been deleted. Rename a function
and this file fails with the rest of the suite, which on this repo means it
fails *before* the deploy rather than after.

Two things it deliberately does not do. It does not check prose: "a ＋ Create
New Estimate button that pre-fills from the most recent estimate" was
every-symbol-correct and simply no longer true. Keep volatile UI detail in
code comments, where it travels in the same diff as the change, and keep this
file for invariants and traps that survive a redesign. And it forbids
hardcoded suite counts — they were wrong by 38 before the test existed, and a
number that is wrong today is what teaches a reader to distrust the rest.

**Deploying.** ONE Railway service, whole repo — see the deploy note at the end
of the portal section. Never deploy a subdirectory. **Pushing to `portal-merge`
IS deploying**: auto-deploy fires as soon as CI is green, so a push goes
straight in front of the reps.

**Backups: all four are covered** — the estimator since day one, the three
databases since 2026-08-25 (audited 2026-09-01 against the running service).
*This section claimed the databases had nothing until that audit: the note was
written 2026-08-12 and the fix landed after it, so it spent a week telling
everyone to build something that already existed.* Three separate things, easy
to confuse:

- *On this laptop*, `estimator/estimates/`, every `*.db` (plus the `-wal`/`-shm`
  sidecars), and `prospector/inbox/` are gitignored, so a push never includes
  them. These are mostly **dev scratch** (a few MB) — losing them costs little.
- *The estimator's estimates*: `_check_daily_backup()` emails a zip of every
  estimate nightly to `BACKUP_EMAIL` (defaults to Luke), and admins can pull
  `/api/backup` for estimates + photos + config on demand. The nightly job takes
  an `O_EXCL` lockfile so two gunicorn workers can't both send it.
- *The three SQLite databases* — `salescrm.db` (leads, activities, prospecting
  history, documents), `canvasser.db` (pins, GPS) and `portal.db` (every
  password hash and invite) — are zipped by `portal/backup.py`.
  `_check_daily_db_backup()` mails that zip nightly and `/api/backup/databases`
  serves it on demand.

Four things about the database backup are load-bearing:

- **It uses SQLite's online backup API, never a file copy.** These databases run
  WAL (`portal/dbtune.py`), so the `.db` on disk is not the database — committed
  pages sit in the `-wal` sidecar until a checkpoint folds them in. Copying the
  `.db` alone can miss committed transactions, and copying the three files
  separately while a rep saves a lead can catch a checkpoint mid-flight and
  produce a snapshot that will not open. `snapshot_bytes()` takes a reader's
  locks and restarts itself if a writer commits underneath, so the result is one
  consistent point in time with no need to stop the site.
- **The snapshot is written back as non-WAL**, so restoring is unzip-and-go with
  no sidecars that have to travel together.
- **`/api/backup/databases` is admin-only, deliberately not manager-up.**
  `portal.db` is every password hash in the company, so handing it to the
  manager tier is a privilege escalation dressed as a backup. This is the one
  place the usual managers-get-the-reporting rule does not apply, and
  `portal/tests/test_backup.py` pins it.
- **`_check_daily_db_backup()` keeps its OWN lockfile**, separate from the
  estimator's — if one job fails the other still has to run.

Two things it is not. The row counts in the email body are there because *a
backup nobody reads is a backup nobody notices has gone silently empty* — seeing
`leads: 0` in an inbox is what catches that, so don't tidy them out. And above
`MAX_ATTACH_MB` (20) the mail links to the endpoint instead of attaching, at
which point the copy is no longer off-platform: retention is whatever sits in
`BACKUP_EMAIL`'s inbox, and there is still no scheduled pull to local storage
(`C:\Users\ldurn\OneDrive` exists if that is ever wanted).

Back up the volume before any migration regardless, as `estimator/CLAUDE.md`
and `salescrm/CLAUDE.md` both warn.

## The Portal — one login, one site (`portal/`)

The three rep tools used to be three Railway services with three passwords.
Since 2026-07-27 they are **one origin, one login, one PWA icon**, with an
app-switcher bar across the top of all three.

```bash
pip install -r requirements-dev.txt   # one-time, covers all three apps
python -m portal.wsgi                 # local dev on :5010 (all three mounted)
cd portal && pytest                   # one login, migration, shell, hardening
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
- **`--p1-shell-h` is the app-switcher bar's TOTAL height, notch included.** The
  bar is `--p1-shell-base` (44px) plus `env(safe-area-inset-top)` of padding, and
  `box-sizing: content-box` makes that padding real height. All three apps offset
  their own fixed header by `var(--p1-shell-h)`, so if that variable is just the
  base, every app header hides under the bar by exactly the inset — 48px on a
  notched iPhone, in installed-PWA mode, which is how the reps run it. Note
  `shell.js` sets the variable as an **inline style on `<html>`**, so it beats
  `shell.css`; a bare `'44px'` there silently reintroduces the bug. Both sides
  must keep the inset. Guarded by `portal/tests/test_shell.py`.
- **`/sign/<token>`, `/sign-co/<token>`, `/uploads/<f>` stay at the root** as
  redirects in `portal/app.py`. Signed-contract links already in customers'
  inboxes point there. Never remove them.
- **Set `CANVASSER_DATA_DIR` and `SALESCRM_DATA_DIR` explicitly** — both fall
  back to `DATA_DIR`, which is the estimator's volume.

**Migration:** `python -m portal.migrate_users` (dry run) → `--apply`. Merges the
three old stores on lowercase username, estimator password wins. Must be run on
the volume before the cutover deploy.

### Hardening — four settings that fail silently

All guarded by `portal/tests/test_hardening.py`, because every one of these is
invisible when it stops working.

- **Every SQLite connection goes through `portal/dbtune.py`.** `journal_mode=WAL`
  plus a 5s `busy_timeout`, applied in all four `get_db()`s (portal, salescrm,
  canvasser, Nimbus cache). Without WAL a single writer locks the whole file, so
  one rep saving a lead blocks every reader; without `busy_timeout` a contending
  statement raises `database is locked` **immediately** rather than waiting.
  Order matters inside `tune()` — the timeout is set first so the WAL switch
  itself waits out a lock instead of raising. WAL adds `-wal`/`-shm` sidecars
  beside every DB, which is why `.gitignore` now globs `*.db*` rather than naming
  files: the documented save routine is `git add -A`.
- **`/login` is throttled** (`portal/throttle.py`), per username (8 fails) *and*
  per IP (30 fails, catches one password sprayed across many names). State is in
  `portal.db`, **not** process memory — two workers would each keep their own
  counter and hand out double the allowance, and every deploy would reset it.
  The check runs *before* the password is verified, since the pbkdf2 hash is the
  expensive thing being protected. Lockouts escalate 15 → 60 min, and an
  unreadable client address buckets into `ip:unknown` rather than skipping the
  per-IP check — **fail closed**. `PORTAL_DISABLE_LOGIN_THROTTLE=1` for local
  work only.
  **The release valve is in 🔑 Passwords & Logins**: a locked rep shows a red
  `🔒 Locked Nm` badge with a `🔓 Unlock` button (admin-only, `POST
  /api/users/<u>/unlock` on both the estimator and the portal). Keep it — a
  lockout nobody can clear means a rep standing on a doorstep waiting 15
  minutes. Unlock is deliberately **per-username only**, so clearing one rep
  never hands a password-sprayer a fresh per-IP budget.
- **Security headers ship from `portal/session.py`'s `after_request`**, so all
  four apps get them from one place. HSTS is gated on `_secure_cookies()` — the
  same signal as the Secure flag, so the two can never disagree — because a
  browser remembers HSTS for a year and emitting it on localhost would break
  plain http:// for every other project on the laptop. **There is deliberately
  no CSP**: all four front ends use inline handlers and inline `<style>`, and the
  estimator's customer pages are inline-CSS strings in `app.py`, so a useful
  policy would need `unsafe-inline` and a strict one would break signing links
  customers already hold. Nothing embeds anything in an iframe, so
  `X-Frame-Options: DENY` is safe — check that before relaxing it.
- **`MAX_CONTENT_LENGTH` is now set for every app**, defaulting to 32 MB in
  `configure()`; the estimator still asks for its own 30 MB. Werkzeug buffers an
  upload into memory once code calls `.read()` on it (which the CRM's document
  upload does), so one unbounded POST takes out a worker — and there are only
  two.
- **`DISABLE_AUTH` refuses to engage when `RAILWAY_ENVIRONMENT` is set.** It
  turns off the guard on every estimator route; it existed one fat-fingered
  Railway variable away from publishing every estimate and signed contract.

### Mobile — the four rules that hold across all four apps

Every rep runs these on a phone, in a driveway, as an installed PWA; two of
them also run on an iPad. All four traps below fail *silently* — nothing
errors, the layout just quietly becomes unusable on the device it is used on —
so each is pinned by a test.

- **Gate touch rules on the POINTER, never on the width.** This is the one that
  keeps being got wrong, in both directions. The estimator's iOS focus-zoom
  guard lived in `@media (max-width: 767px)`, which covers phones and misses
  the iPad — and the iPad is where estimates get written, on a table full of
  13px numeric inputs, so tapping one zoomed the estimate to ~115% and left it
  there for the appointment. The CRM's 44px touch targets had the mirror-image
  bug: gated `(min-width: 768px) and (max-width: 1366px)`, so the *tablet* got
  comfortable sizes and the *phone* kept the desktop ones. A width query cannot
  express "this is a finger". `@media (pointer: coarse)` can, catches every
  touch device at any width, and leaves laptops (`pointer: fine`) alone.
- **iOS zooms any focused control under 16px and never zooms back out.**
  `maximum-scale=1` does not prevent it — iOS has ignored that since iOS 10.
  16px on every input/textarea/select under a coarse pointer is the only fix.
  Bumping a control's font size changes how wide it must be: the estimator's
  tablet money inputs are sized for 16px digits, and at their old 14px widths
  a five-figure total lost its last two characters.
- **Size anything full-height in `dvh`, keep `vh` above it as the fallback.**
  The Safari toolbar overlaps the bottom of `vh`, which is how the CRM's modal
  buttons, the canvasser's pin Save button and the login card all ended up
  below the fold. Order matters — `dvh` must come second to win.
  **And the pair belongs on the base rule, never inside a width query.** The
  estimator's modals had the fix gated behind `@media (max-width: 767px)`, so
  every phone was covered and every iPad was not — and the iPad is where
  estimates get written, with a Save button on the bottom row of a modal.
  That is the pointer-vs-width trap above arriving a third time, through a
  height query instead of a touch rule. `dvh` costs nothing on a desktop (no
  dynamic toolbar, so it equals `vh`), which is why gating it buys nothing and
  loses the device that needed it. Fixed app-wide 2026-09-05 and pinned by
  `estimator/tests/test_modal_viewport.py`, which walks every `*-modal-box`
  rule rather than naming them, so a new modal is covered on arrival.
- **Scrollable overlays need `overscroll-behavior: contain`, and anything
  pinned to the bottom needs `env(safe-area-inset-bottom)`.** Without the
  first, flicking past the end of the CRM's lead drawer scrolls the pipeline
  behind it and past the end of a canvasser panel pans the map off the street
  being worked. Without the second, the drawer's Convert/Delete pair sits under
  the home indicator.

Also pinned: `-webkit-text-size-adjust: 100%` in all four. Rotating to
landscape makes iOS inflate font sizes per-block, and Android applies its
accessibility text scaling the same way — either one overflows a fixed-width
input or a KPI tile.

### The company's clock (`portal/clock.py`)

The office is in Colorado; the server runs in UTC. For the six hours between
6pm Mountain and midnight, UTC has already rolled over — so for a quarter of
every day the question "what day is it" had two answers and the four apps kept
picking the wrong one.

**Stored timestamps stay UTC, and that is correct.** Nothing here migrates a
column. `created_at`, `signed_at`, `due_at` sort and compare across four apps,
and a local-time column would contain, once every November, an hour that
happens twice with no way to tell the two apart. What this module fixes is the
other thing: a DECISION about a day — which month did this land in, is this due
today, what does "the last 30 days" mean. **The boundary is Colorado's and the
value is still UTC**, which is what lets `start_of_today_utc()` drop straight
into `WHERE due_at >= ?` beside columns nobody is touching.

It lives in `portal/` for the reason `geo.py` and `funnel.py` do: the four apps
keep separate databases and anything genuinely shared needs a home belonging to
none of them. It replaced two independent copies — `estimator._company_today`
and `agents.events.company_today`, written weeks apart — which is how two
answers to one question start to drift. Both are now aliases onto it.

- **`month_of()` is the one that moves money.** A roof signed at 7pm Mountain
  on 30 September is stored `2026-10-01T01:00:00Z`, and the analytics tab
  bucketed it by slicing the first seven characters of that string: off
  September's revenue, off that rep's September number, and onto a month they
  had not started selling. Month end is exactly when reps push to close, so
  this was not a rare row. The same slice ran in four places — the sent cohort,
  signed revenue, and both margin-basis keys — and YTD had it too, where the
  night it gets wrong is New Year's Eve.
- **A fixed offset cannot do this job.** Colorado is UTC-6 on MDT and UTC-7 on
  MST, so salescrm's `replace(hour=13)  # ~7am Denver` was right for eight
  months a year and queued every winter storm's calls at 6am, before anyone is
  up, on a board a rep checks once. `at_hour_utc(7)` asks the tz database.
- **`end_of_today_utc()` is built as tomorrow minus a second**, never as
  23:59:59 of today, so the two DST days a year — one 23 hours, one 25 — land
  on the real end of the day.
- **Every function falls back to UTC when the tz database is missing**, on
  purpose: a slim image must not take the site down. That fallback is also
  invisible, and it would make every fix listed here a silent no-op while the
  whole suite stayed green — so `tzdata` is pinned in `requirements.txt` and
  `portal/tests/test_clock.py` asserts `available()` rather than trusting it.
  That one test is what guards the other twelve.
- **Not everything with a date in it is the company's day.** The SPC daily
  CSVs, the cache key that decides whether one is final, and the MESH archive's
  `event_date` are NOAA's calendar, which is UTC by definition — converting
  those would be the same category error in the other direction. They are left
  alone deliberately. Durations (the 15-minute team-location liveness window)
  are not day questions either.

**Background work runs in threads, not crons** — the Procfile runs ONE Railway
service and a second one to hold a cron would break that. There are **two**
runners and confusing them wastes a day:

- **The estimator's hourly loop** (`_reminder_loop`) runs unconditionally and is
  where anything daily belongs: both backups, the CRM digest, and
  `_check_hail_nightly()`. Each job takes its own `O_EXCL` lockfile so two
  workers cannot both run it.
- **`agents/scheduler.py`** is Nimbus's weekly runner and is **off unless
  `NIMBUS_SCHEDULER=1`**, which is easy to miss: the jobs exist, the page lists
  them, and none of them ever fire. It also supports `DAILY`, currently unused.

A second nightly hail ingest was nearly added to the scheduler because
`_check_hail_nightly` imports `hail.backfill` under an alias and a grep missed
it. **Look in both places before adding a scheduled job.**

**Deploy:** ONE service. Root `Procfile` is
`gunicorn portal.wsgi:application`; deploy the whole repo, not a subdirectory.

**Pushing to `portal-merge` IS deploying.** The service is connected to
`Ladical13/crm-helper` with auto-deploy and "Wait for CI" both on, so a push
that goes green in Actions ships itself, straight in front of the reps. There
is nothing to run by hand.

*(This inverted on 2026-08-16. Before that there was no auto-deploy and the
rule was the opposite — `railway up` or nothing. Older notes and habits still
say so; they are wrong.)*

Treat every push as a production deploy. Run `python run_tests.py` first, and
for anything touching login/session, a DB migration, or pricing, agree it with
Luke **before** pushing rather than after. The green suite is the only gate, so
never push with a known-failing test "to fix later": that either blocks every
deploy or ships the bug.

`railway up` still exists and still works, but it uploads **the local working
directory** — untracked files included, CI skipped. Reach for it only to ship
something deliberately unpushed, and know that it supersedes the git-built
image. Running it right after a push just deploys the same code twice, the
second time from a less trustworthy source.

```bash
railway status            # confirm project-one-estimator / production
railway deployment list   # watch the automatic deploy reach SUCCESS
```

After any deploy, confirm what is actually live rather than assuming:

```bash
curl -s https://project-one-estimator-production.up.railway.app/estimate/static/index.html | grep -o 'v=[0-9]*' | sort -u
```

That cache-buster is the fastest honest answer to "is my change live?" — it
should match `python estimator/bump_version.py --check` locally.

Note `_seed_data_dir()` copies `price_book.json`, `tier_defaults.json`,
`jurisdictions.json` and friends to the volume **only if absent**. On a
long-lived volume the deployed repo copies are inert — editing
`estimator/price_book.json` and deploying does not change live pricing.

**CI:** `.github/workflows/tests.yml` runs all seven suites on every push and PR.
They run as seven separate pytest invocations — one run collecting two apps
collides on the bare module name `conftest`. It installs **node**, because the
estimator's parity and fastening tests `skipif` it is missing and would
otherwise go green without checking pricing at all; a final step fails the run
if any suite reported a skip.

**The Python version is pinned in `.python-version`, and it has to stay equal
to CI's.** This repo is **3.12-only** — `estimator/app.py` uses PEP 701
f-strings, which an older interpreter cannot *parse* — so a builder that picks
3.11 does not fail a test, it fails at import with all seven suites green.
Nothing pinned it until this file existed: Railway's builder chose production's
interpreter, CI chose its own in the workflow, and the two agreeing was a
coincidence nobody checked. `portal/tests/test_runtime_pin.py` now holds the
pin, the workflow and the floor the source actually needs to the same answer.

Two things about it are worth knowing before debugging a version problem:

- **A `NIXPACKS_PYTHON_VERSION` Railway variable OUTRANKS the file.** Nixpacks
  reads the environment variable first, so a stale one makes the pin inert
  while every test above still passes. If production disagrees with
  `.python-version`, look there before anything else.
- **One pin file, deliberately.** Nixpacks also honours `runtime.txt`,
  `Pipfile` and `.tool-versions`, in that order after `.python-version`.
  Adding a second is two answers to one question with a precedence rule
  between them that nobody remembers — the same trap `portal/clock.py` exists
  to close.

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
