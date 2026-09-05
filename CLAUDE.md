# Project One Roofing

Four apps in one repo, served as **one site behind one login**: the portal
(launcher + accounts), the canvasser, the sales CRM, and the estimator. Plus
`prospector/`, an offline tool that feeds the CRM partner lists.

## Working on this repo

```bash
pip install -r requirements-dev.txt   # one-time, covers everything
python -m portal.wsgi                 # run it all locally on :5010
python run_tests.py                   # all six suites, the way CI runs them
```

Then open <http://localhost:5010> — canvasser at `/canvass`, CRM at `/crm`,
estimator at `/estimate`.

**Saving work.** Upstreams are set, so day to day it is just:

```bash
git add -A && git commit -m "what changed" && git push
```

Every push runs the six suites on GitHub (**Actions** tab). Green means the
pricing math, the cache-busters and the per-rep visibility rules all still hold.

**Before committing, run `python run_tests.py`.** It is the same six commands
CI runs, and it is much faster to find a break here than in the Actions log.
Individual suites, when you only touched one app:

```bash
cd estimator  && pytest     # pricing parity, cache-buster, bundles
cd salescrm   && pytest     # pipeline, prospecting, queue, drafts, assets
cd portal     && pytest     # one login, migration, shell, hardening, these docs
python -m pytest prospector/tests   # offline, no network
cd agents     && pytest     # spend cap, cache, b2b/content sources
cd canvasser  && pytest     # vendored Leaflet, cache-buster, sw wiring
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

Back up the volume before any migration regardless, as the estimator and CRM
notes below already warn.

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

### Hardening (2026-08-12) — four settings that fail silently

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

**CI:** `.github/workflows/tests.yml` runs all six suites on every push and PR.
They run as six separate pytest invocations — one run collecting two apps
collides on the bare module name `conftest`. It installs **node**, because the
estimator's parity and fastening tests `skipif` it is missing and would
otherwise go green without checking pricing at all; a final step fails the run
if any suite reported a skip.

## Canvasser (`canvasser/`)

The door-knocking app: pins, team GPS, hail overlays, Pipeline handoff. Flask +
SQLite + Leaflet, mounted at `/canvass`.

**A knocked door becomes a lead in The Pipeline, not a job in The Den.**
`PIN_STAGE` maps pin type → CRM stage (`interested`/`come_back` → `contacted`,
`appointment` → `appt_set`, `inspected` → `inspected`, `closed` → `won`); the
three types not listed never become leads. The lead is created **by the browser**
against `/crm/api/leads` — all four apps share one origin and one cookie, so the
rep's own session authorizes it and the CRM keeps sole ownership of what a lead
is. `POST /api/pins/<id>/lead` only records the resulting id back onto the pin.

This replaced `crm_sync()`, which POSTed a `Contact` + `Project` straight to
Base44. Three things were wrong with it: the lead never entered the sales
pipeline, so nothing followed it up and it reached no leaderboard; the Project
carried `status: 'lead'`, which is not one of The Den's statuses; and neither
payload set `location_id`, so every record it made fell outside the Colorado
filter — the jobs existed and were invisible. It was also gated to `closed`
pins only, which meant the interested homeowner who needed a follow-up was
exactly the one the tool ignored. Storage is `CANVASSER_DATA_DIR/canvasser.db`
(gitignored — **set that variable explicitly**, it falls back to the estimator's
`DATA_DIR`).

```bash
cd canvasser && pytest                # vendored Leaflet, cache-buster, sw wiring
python -m portal.wsgi                 # dev: run the portal, canvasser is at /canvass
```

- **Leaflet is VENDORED under `static/vendor/`, pinned at 1.9.4** (markercluster
  1.5.3). It used to come from unpkg, which made a third-party CDN a hard
  dependency of the one tool that gets used in driveways on one bar of signal —
  slow or unreachable unpkg meant a rep staring at a blank screen. Upgrading is a
  deliberate re-download of all nine files (js, css, and the five `images/*.png`
  that `leaflet.css` references **relatively**, so they must stay beside it under
  `vendor/`). `test_leaflet_is_not_loaded_from_a_cdn` fails if a CDN URL returns.
- **The service worker gives it an offline app shell.** Verified with the server
  fully stopped: page, Leaflet, cluster, CSS, app.js and marker icons all serve
  from cache. **Map tiles do not** — they are cross-origin, come back opaque, and
  browsers charge opaque entries against the storage quota at a padded size, so
  caching them blind can evict the very shell the worker exists to guarantee.
  Doing tiles properly needs `crossOrigin` on the tile layer plus a bounded
  cache; deliberately not done.
- **`/api/*` is network-first, never stale.** A canvasser acting on a cached pin
  list knocks doors a teammate already worked.
- **`sw.js` is served from the app root (`/sw.js` in `app.py`), not `/static/`.**
  A worker can only claim a scope at or below its own path, so `/static/sw.js`
  could never control `/canvass/`. Same as the estimator and the CRM.
- **Cache-buster is `?v=N` in `static/index.html` and `sw.js`** (`CACHE` + every
  SHELL entry), bumped **by hand** — no `bump_version.py` here. Guarded by
  `tests/test_assets.py`.
- **`viewport-fit=cover` in the viewport meta is load-bearing**, not decoration:
  `style.css` derives `--safe-top`/`--safe-bot` from `env(safe-area-inset-*)` and
  uses them in five layout rules. Without it those resolve to `0` and, paired
  with the translucent status bar, the header sits under the notch.
  `user-scalable=no` stays — this is a full-screen map and page zoom on a stray
  pinch fights Leaflet's own gestures.

## Sales CRM — "The Pipeline" (`salescrm/`)

A sales-driven CRM that sits at the **top of the funnel** (The Den/Base44 is
production-centric; this is the outreach + pipeline layer). Same stack as the
canvasser: **Flask + SQLite + PWA**, mounted at `/crm` by the portal. Storage is
a single SQLite file (`SALESCRM_DATA_DIR/salescrm.db`, gitignored — back it up
before any migration). No pricing math lives here.

```bash
cd salescrm && pytest                 # pipeline, prospecting, queue, drafts
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

**Integrations.** The CRM is the **sales system of record**; The Den is the back
office and receives a job at exactly one moment — signature. Before that,
nothing is written to Base44 at all.

- **↔ Estimator (`portal/funnel.py`).** The two halves of the funnel are joined
  by a shared table in `portal.db`: one row per estimate, holding the lead it
  came from and the furthest state it reached (`draft→sent→viewed→signed`,
  `declined`). The estimator writes on send, first customer view and signature;
  the CRM drains on any lead/board/task/leaderboard read.
  **This is what makes a close rate computable.** Before it, `leads.estimate_id`
  was a column nothing ever wrote, the CRM knew only its own stages and the
  estimator only its own, and the question "of the doors we knocked, where do
  we lose people" had no answer in either app.
  - **States only move forward** (`_RANK` in `funnel.py`) and signature is
    terminal. That is what makes draining idempotent and re-runnable.
  - `POST /api/leads/<id>/start-estimate` writes **nothing** to The Den; it
    hands back the estimator URL carrying `&lead=<id>`. It used to create a
    Base44 contact here — see the Won-guard note below for why that was fatal.
- **Stages move on events, not on memory.** `_auto_advance()` applies them:
  estimate sent → `estimate_presented`, signed → `won`. **The rep is always
  allowed to be ahead** — a lead already at or past the target stage is left
  alone. The single exception is a signature, which outranks even a manual
  `lost`, because the customer signed.
- **Cadences enrol themselves.** `_cadence_for()` on lead creation and on stage
  change: homeowners get `new_lead_7touch`, partners get `partner_nurture`,
  and reaching `estimate_presented` starts `estimate_followup`. The four
  cadences existed long before anything enrolled a lead into one, so the
  follow-up engine only ever ran for reps who remembered to ask for it.
  Bulk prospect imports bypass `create_lead()` and are deliberately **not**
  enrolled — 36k open-data rows must not each grow a task.
- **→ The Den:** `_push_to_den()` fires at signature (or on a manual move to
  `won`), creating a `Contact` + `Project` and filing the signed contract as a
  `Document` linking to the estimator's hosted signing page. Not an upload:
  Base44 refuses `UploadFile` to our token (blanket 405).
  ⚠️ **The guard is `crm_project_id`, never `crm_contact_id`.** Guarding on the
  contact is what kept a single job from *ever* reaching The Den: `start-estimate`
  set that field, so every lead that reached an estimate read as "already
  pushed" and was skipped silently. Pinned by `tests/test_funnel.py`.
  ⚠️ **Both payloads carry `location_id`.** The Den scopes Colorado reporting to
  it and picks it from a dropdown in its own form, so anything created through
  the API without it is invisible to the estimator's contact search *and* to
  every executive-team skill. Whether Base44 honours the field on create is
  **unverified** — the activity log tells whoever picks the job up to check it.
  Projects land as `contracted`; they used to be pushed as `status: 'lead'`,
  which is not one of The Den's statuses at all.
  `POST /api/leads/<id>/convert?dry_run=1` returns the payloads without writing.
- Reuses the shared `BASE44_TOKEN` env var. Den calls degrade gracefully when unset.

**PWA cache-buster:** any `app.js`/`style.css` change must bump `?v=N` in
`static/index.html` (×2) **and** `static/sw.js` (`CACHE` + `SHELL`) — 5 spots, or PWA
clients keep the stale bundle. Bumped **by hand** — unlike the estimator there is no
`bump_version.py` here — but `tests/test_assets.py` now fails if the five disagree.
It drifted once before that test existed (`CACHE` at v13, `SHELL` still precaching
`?v=12`), which precached two files the page never requests and left the bundle it
*does* request out of the offline shell.

The CRM service worker is **cache-first with background revalidation**, not plain
cache-first. That matters because the portal's `/shell.js` and `/shell.css` carry no
`?v=`: under the old `hit || fetch(...)` it never refetched, so the app-switcher bar
froze at whatever was first cached until someone bumped `CACHE`. Guarded by
`test_service_worker_revalidates_in_the_background`.

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
python -m pytest prospector/tests                      # offline, no network
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
cd estimator && pytest                # pricing parity, cache-buster, bundles
```

**Open bug: `company_content.json` is seeded but not shipped.** `app.py`'s
`_seed_data_dir()` copies it into a fresh `DATA_DIR` alongside `price_book`,
`tier_defaults`, `permit_defaults`, `jurisdictions` and `commercial_fastening`
— all five of which are tracked. `company_content.json` is not: `.gitignore`
lists it under *"Estimator — user data"* next to `config.json` and
`users.json`, which really are sensitive. It is marketing copy, and looks
swept in by association.

Two consequences. The visible one: four tests asserting its seeded values
failed on every fresh clone and in CI from 2026-08-04 until a test fixture was
added on 2026-08-16 (`tests/fixtures/company_content.json`, used only when the
real file is absent — see `conftest.company_content_source`). The one still
open: `_seed_data_dir` copies `if os.path.exists(src)`, so **a rebuilt Railway
volume comes up with empty About Us / Warranty / Certifications / Reviews on
customer proposals** until an admin re-enters them in Settings. Today's volume
has the file because someone typed it in, not because the repo ships it.

Fix by tracking the real file once it is confirmed to hold no secrets, then
delete the fixture fallback. Do **not** fix by making the tests skip — the
workflow's final *"Fail if any test was skipped"* step exists precisely to
stop that, and would fail the build anyway.

**Still open, but it is no longer silent** (2026-09-05). The real fix needs the
live file, which only the volume has. Until then `get_company_content` logs
loudly when the content is empty and Settings shows an admin a banner saying
every proposal is currently going out with no About Us, Warranty,
Certifications or Reviews. `_company_content_missing()` is the shared check.
That turns a failure nobody would notice until a customer mentioned it into one
that is visible in the place where it gets fixed — it does not make the
proposals correct.

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

### Margin, expiry, and the safety net (2026-09-05)

Four traps that all shared one shape: the tool knew the right answer, said so
in a comment, and then had nothing that acted on it.

- **A blank margin box means INHERIT, never 0.** `setTierRate` wrote
  `parseFloat(v) || 0`, so clearing a Good/Better/Best margin stored a real `0`
  that the rate chain then honoured exactly as designed — the roof priced at
  cost, the screen looked completely normal, and `tests/test_parity.py` stayed
  green because `app.py` and `app.js` agreed perfectly about the wrong number.
  That is the trap `_resolveRate`'s own comment had warned about for months.
  All three setters (`setTierRate`, `setTradeOverride`, `setTradeTierRate`) now
  route through `_rateValue`, and `tests/test_margin_floor.py` fails if any of
  them stops. **An explicit `0` still sells at cost** — that is a real choice a
  rep can make and it is unchanged.
- **The margin floor is checked at SEND, never at save.** `_margin_floor_block`
  guards `/api/estimates/<id>/share` and `/api/estimates/<id>/send-email`; a rep
  may draft anything, and a half-built estimate must never be un-saveable. Two
  settings, both manager-up: `margin_floor_warn` (amber banner, default 35) and
  `margin_floor_block` (manager-only send, default 30). Warn deliberately equals
  `DEFAULT_RATE`, so a rep who never touches the margin box sits exactly on
  target and the banner only ever appears because someone moved it down.
  **Residential only** — `_margin_floor_exempt` excludes insurance (the carrier
  sets that price) and commercial (its pricing comes off a per-job supplier
  quote and the catalog ships $0 placeholder costs, so a floor would be
  measuring the placeholders). Three things are load-bearing. It reads **realized** margin, `(sell − cost) / sell`, via
  `estimate_margin_report` — not the `pricing.mode` rate, because a 30% markup
  is a 23% margin and comparing one against the other waves jobs through. It
  reads the **worst package on offer**, because the customer picks, not the rep.
  And a tier with **no cost reports an unknown margin, not a perfect one** — the
  commercial catalog ships $0 placeholder costs on purpose, and calling those
  100% would clear exactly the bids that have no supplier pricing yet.
  `_trade_cost_subtotal` MUST mirror `_trade_subtotal`'s inclusion rules line
  for line, and `tradeCostTotal` (`app.js`) mirrors both.
- **`valid_until` is enforced now.** It printed on the customer page, the PDF
  and the signed contract as "Pricing held until <date>" and nothing ever
  checked it, so a six-month-old link could still become a contract at
  six-month-old material prices. `_est_expired` withdraws the signature block
  at the one choke point all three layouts share (`_cv_sig_form`, which takes
  `est` for estimates and nothing for change orders), and the POST returns 410.
  The estimate itself still renders — someone reopening an expired quote is a
  warm lead, and `_notify_expired_view` tells the rep, once per estimate.
  An unparseable date means **no** expiry: a typo must not lock a customer out.
- **`setDirty()` is where crash recovery hangs.** It used to change a label and
  nothing else — no unload guard, no local copy, no autosave — so an iPad
  reclaimed by iOS took an hour of takeoff with it. Three layers now, kept
  independent so one failing never blocks the others: a `beforeunload` guard,
  a `localStorage` snapshot (`saveDraftLocally`, offered back by
  `offerDraftRecovery` at boot), and a debounced autosave that runs **only for
  estimates the server already has** — autosaving a brand-new one would put
  half-built records in everyone's Open list. `visibilitychange` matters as
  much as `beforeunload`: iOS never fires the latter when it reclaims a
  backgrounded tab, which is the exact case this exists for. The draft
  deliberately drops `visualizer` — megabytes, server-owned, and not at risk.

**Settings is tabbed, and its role gating finally does something.** Eight
unrelated editors sat in one scrolling column, and each gated section carried a
`hidden` class that matched **no rule in `style.css`** — which says so itself
next to `.gap-note.hidden`: there is no global `.hidden` utility, every use is
scoped. So `.field-group.hidden` styled nothing and the gate did nothing, and
every **manager** was seeing the admin-only contract and proposal editors.
(Reps were never affected — `applyRoleGates()` hides the Settings button from
them outright — and it was never a data leak, since the server refused the
writes. But the gate a reader would assume was working was not.) Panes now
need **both** the active tab and the absence of `hidden`:
`.settings-pane.is-active:not(.hidden)`. `renderSettingsTabs()` runs last in
`openSettings`, after the role checks, and builds the strip from whatever they
unhid — so a new section needs an entry in `SETTINGS_TABS` *and* the
`settings-pane` class, or it is unreachable / always-on. Guarded by
`tests/test_settings_tabs.py`, which also pins the loss-reason picker being a
modal rather than the stack of browser `prompt()` dialogs it shipped as.

Also landed with these, each pinned by a test:

- **The customer gets their own signed contract.** `send_customer_signed_copy`
  runs first in `_post_sign_pipeline`, ahead of the Base44 push and the packets,
  because the homeowner waiting on a receipt should not queue behind a back
  office integration. `sig_email` had been collected, stored in the certificate,
  echoed to the rep, and never used to send the customer anything.
- **`/sign/<token>/download.pdf` was NOT on `PUBLIC_ENDPOINTS`.** The "save a
  copy before you decide" card is on every `/sign` variant, and the button a
  *customer* clicked bounced them to a login page. Public does not mean
  unguarded — the token is still the whole protection, and a bad one still 404s.
- **Marking an estimate lost records why.** `LOST_REASONS` is served by
  `/api/lost-reasons` rather than mirrored in the front end, so the picker and
  the validator that accepts its value cannot drift. Moving back out of lost
  clears the reason — plenty get re-quoted, and a job that closes in March must
  not carry "went with someone else" into the month it was won. Estimates
  marked lost before the picker existed count as `unrecorded` rather than being
  dropped, which would inflate the share of every reason that *is* recorded.
- **Unassigned estimates are counted, not vanished.** `/api/analytics` still
  skips them from the per-rep math (`by_rep[sp]` is threaded through a dozen
  sites and a synthetic "(unassigned)" rep would rank as if it were a person),
  but the count and the dollars now come back in `unassigned` and show on the
  tab. The old bare `continue` dropped them from the funnel, revenue, aging,
  cities and YTD with nothing anywhere saying how many rows had gone.
- **The customer's package taps are recorded.** `selectCvTier` was pure DOM;
  `/sign/<token>/tier-interest` now takes a `sendBeacon` and
  `_tier_interest_summary` puts it in the rep's follow-up email. Capped
  (`TIER_INTEREST_CAP`), de-duplicated for a card tapped twice running, and
  ignored entirely for a logged-in team member previewing the link — the rep's
  own tapping is not a buying signal.

### Commercial estimates (third estimate type)

`🏠 Retail | 🏛 Insurance | 🏢 Commercial` in the sidebar. Commercial mode turns
off every other trade, hides the steep-slope roof + attic-ventilation panels,
and lands the rep on **Scope** (the bid is driven by the EagleView numbers).
Tests: `tests/test_commercial.py`, plus commercial fixtures in `test_parity.py`
and `test_bundles.py`.

- **`commercial` is a bundle trade that defaults to G/B/B**, selling by scope
  of work — coating, overlay, full replacement — the way roofing sells by
  shingle. Any tier's dropdown offers all ten packages plus Custom, so the
  seeded ladder is a starting point, not a coupling. The rep can still flip the
  whole trade to Simple for a building owner who wants one number.
  `effectiveTradeMode()` (`app.js`) / `_trade_mode()` (`app.py`) are the
  mirrored pair that decides this, along with `SIMPLE_MODE_TRADES` (gutters
  only now) and `_MODE_DEFAULT_FLIPPED`. **A bundle trade in simple mode must
  build FLAT items** (`unit_cost`/`unit_price`) via
  `buildSimpleItemsFromBundle`; per-tier items in a simple trade total **$0**
  while looking completely normal on screen — and since the default flipped,
  the same $0 arrives from the other direction, which is why an estimate with
  **no `mode` key is resolved by the SHAPE of its items**, not by today's
  default.
- **`_est_comm_system()` resolves coating / layover / tearoff against the tier
  being SOLD.** All three tiers share one `line_items` array, so scanning the
  whole array reports every three-tier bid as a layover — and the customer's
  process list then promises a roof that is never torn off. It drives the
  `/sign` process steps (`_PROCESS_COMMERCIAL_BY_SYSTEM`) and the packet's
  layover/coating crew rules.
- **Both labor lines ship in every commercial bundle.**
  `measurements.comm_work_type` (0 = re-roof @ $400/SQ, 1 = new construction @
  $250/SQ) zeroes the one that doesn't apply — zero-qty lines never price and
  never print. No new pricing math, so nothing to mirror.
- **Measurements use their own `comm_*` namespace** so a flat roof can never
  inherit a steep-slope number. Mirrored in `MEASURE_FIELDS` (`app.js`) and
  `MEASURE_LABELS` (`app.py`).
- **Material costs ship as `0` placeholders on purpose** — commercial pricing
  comes off the supplier quote per job — and the coating and layover packages
  have no labor rate yet either, so two of the three seeded tiers are partly
  unpriced. `unpricedBundleLines()` finds them and the pricing tab shows a red
  banner: **per tier** in G/B/B (naming which column), per bid in Simple. That
  banner is the only thing between a placeholder book and a bid that looks
  legitimate. `AWAITING_QUOTE_TIER_DEFAULTS` in `tests/test_commercial.py`
  names the exceptions so a second one cannot arrive silently.
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

### Where catalog/bundle data must live

**Bundle-trade data belongs in the `*_SEED` constants in `app.py`, not in
`price_book.json`.** `_seed_data_dir()` copies `price_book.json` to the volume
only when it is **absent**, so on any long-lived volume the repo's copy is inert
— editing it and deploying changes nothing. `_ensure_bundle_catalogs()` reads
the seeds on every GET and backfills them into the live book, which is the only
path that reaches production. Siding data was briefly in both; the copy in
`price_book.json` was removed and `test_siding_is_seeded_from_app_py_not_price_book_json`
now fails if it comes back. **Roofing followed on 2026-09-01** — its copy had
gone quietly stale (no bullets, no colors, old bundle copy, and a
`b_standing_seam` still listing shingle trim), so which source won depended on
which file someone happened to edit. `test_roofing_is_seeded_from_app_py_not_price_book_json`
now guards it. `price_book.json` is down to `intros`, `materials` and `presets`.

Five things behave differently once books are in the wild:

- **Products** append by id automatically — a new seed product always arrives.
- **Bundles do not.** The copy-field backfill reads a missing id as "the manager
  deleted it" and skips it, so a *new* bundle reaches nobody. List its id in
  `_LATE_BUNDLE_IDS` to have it appended, and drop it once live books have been
  saved past it. Deletion stays sticky for every id not on that list.
- **A bundle's `product_ids` do not either** — managers customize them, so
  they're not a copy field. `_LATE_BUNDLE_PRODUCTS` forces a product *into* a
  seeded bundle; `_BUNDLE_PRODUCT_SUPERSEDED` is the matching **removal**,
  scoped to one bundle because a product can be right in one package and wrong
  in another (`cl_labor_reroof` is correct tear-off labor on `cb_modbit` and a
  flat lie on a coating). `_PRODUCT_SUPERSEDED` is the global version.
- **Tier defaults do not either**, and this one is easy to miss:
  `_ensure_bundle_catalogs()` **`setdefault`s** `<trade>_tier_defaults`, and
  every live book already has the key — so a new ladder in the seed reaches
  nobody. `_TIER_DEFAULT_MIGRATIONS` rewrites a tier only while it still holds
  the *previous seed's* id, so a manager's own pick survives.
- **Costs do not either, and deliberately so** — a saved cost is the manager's
  price and the seed must never fight it on every GET. That protection is also
  why *correcting* a seed placeholder reaches nobody. `_PRODUCT_COST_MIGRATIONS`
  is the narrow exception: it rewrites a cost only while the live number still
  equals the *previous seed's* to the cent, which is the signature of a default
  nobody ever touched. (`_SEED_COST_BACKFILL_TRADES` is the older, blunter
  cousin — 0 → seed, commercial only, because that catalog shipped entirely
  unpriced.) Both are one-directional and both should be dropped once live books
  have saved past them.

### Jurisdiction code lookup (`/api/jurisdictions/<id>/verify`)

Fills the "this is the code your city enforces" block on the customer's sign
page and the permit packet. **A profile only reaches a customer after a manager
approves it** (`reviewed_at`) — that gate is the whole reason a model is
allowed near this at all, and it stays. Tests: `tests/test_jurisdiction_verify.py`.

Audited end-to-end on 2026-08-25 against the live API. It was failing 7 of 16
real jurisdictions; it now passes 16 of 16. What was wrong, so it does not
get rebuilt the same way:

- **The allowlist must trust each jurisdiction's OWN domain**
  (`jurisdiction_prompts.jurisdiction_hosts`). The static list is essentially
  `.gov`, but only 84 of the 273 Colorado cities in `jurisdictions.json` are on
  `.gov` — 90 are `.org`, 74 `.com`, 18 `.us`. A bare `.gov` rule rejected 69%
  of cities' own official sites: Aurora's real building-code page on
  `auroragov.org` was thrown out as untrustworthy and the verify failed. Do
  **not** "fix" this by widening the static list to whole TLDs — that admits
  every contractor blog. One extra domain per jurisdiction, matched at a label
  boundary so `notauroragov.org` cannot satisfy `auroragov.org`.
- **All 64 counties shipped with an empty `url`**, so they had no domain at
  all. `_JX_COUNTY_URL_SEED` backfills the 13 service-area counties **on read**
  (`_jx_backfill_urls`), never overwriting a manager's edit. It has to be on
  read: `_seed_data_dir()` copies `jurisdictions.json` to the volume **only if
  absent**, so a seed-only edit is inert in production — the same trap the
  price book's bundle catalogs hit.
- **A delegating jurisdiction has no adopted code of its own and that is a
  valid answer.** Colorado Springs contracts to the Pikes Peak Regional
  Building Department; the old rule failed on `adopted_code == 'unknown'` and
  threw away the correct, useful `delegated_to`. Now a delegation-only profile
  verifies, approves, and prints as **Permits Issued By** on the packet —
  getting that wrong costs the office a trip. `delegated_to` alone is the only
  carve-out; nothing else escapes the unknown check.
- **A cached rejection is retried once with `force_refresh`.** The 30-day cache
  stores the model's *answer*, but these rejections are decided downstream of
  it, so "↻ Re-verify" replayed the same cached answer into the same error for
  30 days. A *fresh* failure is not retried — that only doubles the spend.
- **This call uses `sonar-pro` (`_JX_MODEL`), not the global `sonar` default.**
  `sonar` returned "unknown" for Loveland, Longmont, Boulder and Colorado
  Springs. It is one lookup per jurisdiction, cached 30 days, read by a human
  before it ships — worth about a cent.
- **The direct-fetch tier only tries URLs that are about this jurisdiction**
  (`code_url`, then `url`, Wayback wrappers unwrapped). The Municode/amlegal
  slug guesses that used to live here hit **0 times out of 16** and cost a 10s
  timeout each: Municode serves a ~6 KB JavaScript shell with no code year in
  the HTML, and amlegal 403s us. City sites 403 a scripted User-Agent, hence
  `_JX_UA`. Expect Perplexity to answer essentially every verify.
- **`adopted_code` is tidied, never rewritten** (`_jx_normalize_code`). Only
  the unambiguous "IRC 2021"/"2021 IRC" pair is canonicalised; "Pikes Peak
  Regional Building Code 2023" is a genuinely different code and flattening it
  to an IRC year would state something false. Truncation lands on a word
  boundary — a hard slice once ended a customer-facing answer at "as part of t".
- The prompt asks for **one** short code governing a residential re-roof.
  Loosen it and `sonar-pro` returns 160-character sentences naming the
  commercial IBC, effective dates and transition plans, all of which land on
  the "Enforces" line of a customer's estimate.

**What this does not do: confirm the code year is legally correct.** It finds
and cites an authoritative page; the manager reading it before clicking approve
is the accuracy check. Answers do move between runs — Windsor came back
"2024 I-Codes" on one pass and "2018 IRC" on another.

### Estimate outcome — `lost`, and why the rename was the small half

`declined` is now **`lost`**, and the rename was the least of it. `estStatusOf()`
in `static/app.js` derived an estimate's bucket from `signed / first_viewed_at /
sent` and **never looked at the status at all**, so a declined estimate reported
itself as `viewed`: it stayed in Outstanding, kept counting toward the
outstanding dollar total, and kept appearing in the "⚠ Follow Up Needed" banner
forever. The only thing the status actually suppressed was the reminder email.
Marking one declined did nothing a rep could see, which is why the estimate area
silted up. `estStatusOf` now checks lost **before** viewed/sent, and that
ordering is the fix.

- **`lost` is canonical; `declined` is accepted forever on the way in and
  normalized on the way out** (`_norm_est_status`, `_is_lost`, `LOST_STATUSES`).
  Nothing rewrites stored records — those are real estimates on a live volume,
  and the read path is what normalizes them. Do not "tidy" this into a
  migration.
- **Change orders keep `declined`, deliberately.** A customer saying no to an
  add-on is not a lost job, and collapsing the two loses that distinction.
- **A signed estimate cannot change status** — the server rejects it, and the
  UI shows the fact instead of a control. A signature is what the customer did,
  not a field a rep can retract.
- **One control, not two.** The Status select buried at the bottom of *Estimate
  Details* is gone; `#est-status-bar` sits beside the customer instead. The old
  one wrote through the whole-estimate save, so it bypassed both the signed
  guard and the funnel notification — two controls for one field is how they end
  up disagreeing.
- Marking an estimate lost **does not lose the CRM lead**. Plenty get re-quoted,
  and losing the lead would close the tasks that win it back; the Pipeline
  timeline records it and the rep decides. See the salescrm `_FUNNEL_STAGE` note.
- Guarded by `tests/test_status.py` and `salescrm/tests/test_funnel.py`.

### The customer screen — many estimates, one customer

A homeowner is rarely one estimate: the roof in spring, the siding in autumn,
the re-quote after the adjuster comes back. **`renderClientPage` is where all
of it lives** — one page carrying their details, their notes, every estimate
they have (including the one on screen that has never been saved), the create
form, their files and the document generators. The estimate tab strip sits
behind a single **📝 Open Estimate →** button on it.

`openCustomer(name)` is how you get there, from the home search box, the ⋯
menu, the sidebar's 👤 button, or a `📁 N` badge on any dashboard/home row
whose customer has more than one.

Tests: `tests/test_customer_file.py` (+ `customer_key_runner.js`), plus the
header-badge/breakpoint guards in `tests/test_ui_wiring.py`.

This was three surfaces before. The Customer hub was a waypoint with two
doors; Documents was a page behind one of them; and a Customer File modal
listed the same estimates a third time. The modal and the Documents page were
two views of one question — *what does this customer have?* — and they had
already disagreed: only one knew about an unsaved estimate, so opening the
modal mid-estimate reported the customer had none. Rules that keep the merged
version working:

- **The current row is built from `S`, not from the fetched list.** An estimate
  that has never been saved has no id and is not in `/api/estimates` yet, so it
  would vanish from its own list — which was the whole bug. It renders first,
  marked current, and is not clickable (reloading yourself is a wasted request).
  `customerEstimateRows()` is the single builder; it splices the open estimate
  in **only** for the customer it belongs to, since the screen is reachable for
  any customer, and drops the fetched copy once saved so it is not listed twice.
- **`switchPage('documents')` aliases to `'client'`.** Documents is not a page
  any more, but the header badge, deep links and muscle memory all still say
  it. `#documents-content` **moved** into `#page-client` rather than being
  rebuilt, so the 11 in-page callers of `renderDocumentsPage()` — upload a
  file, delete one, generate a doc — keep working untouched.
- **`renderDocumentsPage()` stays synchronous** for that same reason: most of
  those callers are refresh-after-an-action and none should pay for a network
  round-trip. `refreshDocCustData()` does the fetch, only on navigation *into*
  the screen, from `switchPage`.
- **`docCreateEstimate()` re-lands on the customer screen deliberately.** Two
  things move underfoot: `newEstimateAction()` renders that screen from a
  BLANK estimate *before* the name is copied across (so it reads "No estimates
  yet" for a customer who has several), and `setEstimateType('commercial')`
  navigates to Scope on purpose. Assuming it never moved put a rep on Scope
  looking at an empty customer.
- **Opening a customer asks before binning an unsaved new estimate.** Getting
  there means loading one of their estimates, and every other route into a
  different estimate is a control the rep clicked deliberately — a `📁` badge
  on a dashboard row does not read like "discard my draft". Already inside
  that customer? It navigates instead of reloading.
- **The estimate name is editable after the fact** (`renameEstimate`). It was
  write-once for a long time even though `PATCH /api/estimates/<id>/label`
  existed and worked — the front end simply never called it. Renaming a saved
  estimate uses that narrow PATCH, never a full-doc save, so it cannot push
  stale in-memory state over newer server state; an unsaved one just sets
  `S.estimate_label` and marks the doc dirty. Clearing the name falls back to
  the type label. The label is **rep-facing only** — list projection, the
  `Copy of` marker, that endpoint, and no customer-facing page — which is why
  renaming a *signed* estimate is allowed.
- **`#estimate-label-badge` is a sibling of `#estimate-number`, never nested
  inside it** — same trap `#est-status-badge` already documents. It joins
  `.estimate-number` in being hidden on mobile rather than `.est-status-badge`
  in surviving: that header row has already overflowed a 375px phone once, and
  a label is informational where signed/sent state is not.

Audited 2026-08-25. It worked, but four things it did quietly did not:

- **One grouping key, `custKey()`, used on both sides.** The file grouped with
  a substring `.includes()` while `newEstimateForCustomer` matched with `===`,
  so "Jon Smith" and "Jon Smithson" were one customer to the file and two to
  the button that creates the next estimate. Lowercase, trimmed, internal
  whitespace collapsed. **Mirrored in `app.py` as `_cust_key()`**, which the
  customer-notes endpoints use — the browser decides whose file this is, and
  the notes have to land on the same customer. The notes read path falls back
  to the old `.lower().strip()` spelling rather than migrating: those are real
  notes on a live volume. The home search box stays a substring *search*;
  finding a customer and deciding two estimates share one are different jobs.
- **`CUSTOMER_LINK_FIELDS` rides along to every follow-on estimate**
  (`crm_contact_id`, `crm_project_id`, `crm_job_number`, `crm_lead_id`).
  Without them estimate #2 is an orphan in two directions: the funnel cannot
  attribute it to the lead the door-knock came from, so the close rate
  undercounts, and `_push_to_den()` files a **second Den contact** for someone
  The Den already has — confident, wrong bid-vs-actual, the exact failure the
  `crm_contact_id` note warns about. Copied **only when blank**, so a live CRM
  handoff (`?contact=…&lead=…`) still wins over an older estimate's copy.
- **Duplicating keeps the customer.** It used to rename them to `Copy of Jon
  Smith`, which moved the copy into a customer of its own — while duplicating
  is precisely how a rep builds the second estimate for someone. The `Copy of`
  marker lives on `estimate_label` now, which exists to tell one customer's
  estimates apart.
- **A customer name reaching an inline handler goes through `jsq()`, never bare
  `esc()`.** `esc()` escapes for HTML but not for the JS string literal the
  onclick drops it into, so **Maureen O'Brien's buttons were a syntax error** —
  dead, with nothing logged. JS-escape first, then HTML-escape.
- The create dialog drives its type buttons off `ESTIMATE_TYPES`, so a fourth
  estimate type cannot reach the sidebar and miss this dialog. Commercial did
  exactly that.
- **A row's signed-contract link tests `e.signed`, not `e.signature`.**
  `/api/estimates` returns the former and has no `signature` key at all, so the
  📄 download rendered for nobody — a signed contract was quietly unreachable
  from the one list built to show a customer's whole history.
- **`_media_block()` in `test_ui_wiring.py` is not what a breakpoint test
  wants** — `style.css` has three `@media (max-width: 767px)` blocks and it
  returns the first, a KPI-card block containing no header rules. Two mobile
  tests passed for years by finding nothing. Use `_media_block_with(css, query,
  marker)`, which returns the block that actually contains the rule under test.

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
