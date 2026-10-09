# Sales CRM — "The Pipeline"

*Directory-scoped notes: these load when work touches this directory, so the
root `CLAUDE.md` can stay short enough to be read every session. The traps
that cross all four apps — mobile, the clock, the session and mount rules,
deploying — live there, and they apply here too.*

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
else as the ONE service the root `CLAUDE.md` describes — the old standalone `project-one-crm`
service is retired, so don't deploy to it. App-specific env: `SALESCRM_DATA_DIR`
(set it explicitly — it falls back to the estimator's `DATA_DIR`), plus optional
`ESTIMATOR_URL`, `SALESCRM_DAILY_TARGET`, `SALESCRM_COOLDOWN_DAYS`,
`SALESCRM_STALL_DAYS`, `SALESCRM_JARVIS_EMAIL_REPS`. `BASE44_TOKEN` and
`SESSION_SECRET` are shared.

## Partner prospecting (`prospector/` + the import path)

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
- **A warm import is a different thing from a cold one, and the batch says
  which.** The importer stamps every row `prospecting` / `cold` / `new`, which
  is right for open data and wrong for the Den's customers: landed that way, a
  homeowner whose roof we replaced is offered a free hail inspection. Naming a
  `lead_source` from `WARM_SOURCES` switches a batch to warm, and only then may
  a row carry its real `stage`, `won_at` and `created_at`, and the batch start
  a cadence (`stagger_per_day` spreads the first touches so fifty customers are
  not all due the next morning). On a cold batch those fields are ignored, so
  an open-data pull can never invent a customer. Dating a customer truthfully
  is what keeps a spring roof out of this month's close rate, and `won_at` is
  what the Do Not Call exemption in `_dnc_clause()` reads.
  `python -m prospector pull den:customers` (also `den:open_jobs`,
  `den:partners`) produces these batches; `prospector/sources/den.py` holds
  the rules for who is left out.

## The outreach queue (⚡ Outreach tab)

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
- **Jarvis works the same queue through `portal/apibot.py`.** It reads
  `/api/queue/today?rep=<u>&target=N` (apibot gets a 400 without `rep` — it owns
  no leads, so its own queue would look like a quiet day) and drafts in Gmail.
  Once a draft is in Sent it calls `POST /api/queue/log`
  (`queue_log()`), the **one write** apibot is allowed. That endpoint takes
  outreach kinds only, credits the lead's owner (never apibot), completes the
  card's task through `_complete_task()` so the cadence advances exactly as from
  the card, and dedupes on `ref` (the Gmail id) so re-scanning Sent is safe.
  Skip that call and the cooldown never starts: tomorrow re-serves today. The
  bulk `/api/leads` list strips contact columns for apibot
  (`_redact_for_apibot()`) — the queue is where contacts legitimately arrive,
  one day's work at a time.
  Jarvis also records **how a partner replied** through `PATCH
  /api/leads/<id>/outreach-status`, limited to the outcomes in
  `_APIBOT_OUTREACH_STATUSES`. `appt_set` is deliberately not one of them: an
  appointment is booked by a person.
- **`dnc` as an outreach status drops a lead out of cadence re-touches**, not
  just out of the fresh top-up. It used to stop only new cards, so a partner
  who said "stop" kept coming back as a due task. Guarded by
  `test_a_partner_marked_do_not_contact_drops_out_of_re_touches`.
- **A card is on exactly one side of the day.** A rep working alone does not
  write forty emails: Jarvis drafts them in Gmail and the rep presses send,
  while calls and texts stay on the Outreach tab. Both read this queue, so
  without a rule the same partner is drafted to at seven and dialled at nine.
  `_card_channel()` is that rule and the only place it lives: a scheduled task
  goes by its kind, a net-new card is `email` when the lead has an address and
  `phone` when it does not. `?channel=email|phone` asks for one side; a rep
  named in `SALESCRM_JARVIS_EMAIL_REPS` (`luke:40`) gets the phone side by
  default, with the day's whole count in `day`. Unset, the queue is one list
  for everybody, as before. Guarded by
  `test_no_card_is_on_both_sides_of_the_day`.
- **A touch logged with an `outcome` books the next one.** `_apply_outcome()`
  is shared by the card's `log_outcome()` and Jarvis's `queue_log()`, so an
  email logged as `emailed` books the call three days later either way. Logged
  WITHOUT an outcome it books nothing — correct only for a card that already
  carries a cadence task — and a fresh card worked through Gmail then comes
  back a week later as if nobody had written. apibot may log every outcome
  except `appt_set`.
- **`leads_queue_idx` (`rep, stage, icp_score DESC, created_at`) is what keeps
  the net-new top-up cheap.** Without it SQLite picks `leads_stage_idx` and
  scans every `new` lead — and in a prospecting DB almost everything is `new`,
  so that index selects nothing. It also satisfies the ORDER BY, dropping a temp
  B-tree sort over the whole candidate set. Measured at 36k leads: 19ms → 5ms.

## Outreach drafts (`outreach_templates.json`)

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
- **The signature carries the postal address and a way to make it stop.** It
  is unsolicited business email; those two lines are what the law asks of it.
  Guarded by `test_every_email_carries_a_postal_address_and_a_way_out`.
- **A past-customer template may be for one stage only** (`TEMPLATE_STAGES`):
  `won` gets the review and referral asks, `lost` the requote, and `follow_up`
  — the Den's open job, inspected or quoted and never decided — its own
  "where did it land?". A review ask to someone who never bought is worse than
  no template.
- Templates load at import — **editing the file needs a process restart**.

## Canvasser source handoff

`salescrm/canvass.py` owns the pin-specific handoff transaction. `canvass_links`
maps a pin to one lead and one appointment, preserves the last source values
and retains exact door coordinates for storm matching. Add it to the test reset
list whenever refactoring the schema. General lead creation remains duplicate
friendly; source idempotency must not break cross-sell deals.
