# Estimator — tests & invariants

Where the money math lives. Two independent implementations held to the cent.

*Directory-scoped notes: these load when work touches this directory, so the
root `CLAUDE.md` can stay short enough to be read every session. The traps
that cross all four apps — mobile, the clock, the session and mount rules,
deploying — live there, and they apply here too.*

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

## The homeowner's claim, explained

`estimator/claim_explainer.py` + `build_claim_explainer_pdf()`. 📄 Explain This
Claim on the Insurance tab; `POST /api/estimates/<id>/claim-explainer` hands
back a one-page PDF.

We parse every line of a carrier estimate — RCV, ACV, depreciation split
recoverable from non-recoverable, deductible, O&P, tax per authority — and had
never told the homeowner any of it. *"Why is the check smaller than the
estimate?"* is the question every insurance customer asks, the answer is
recoverable depreciation, and a homeowner who does not understand it concludes
either that their carrier is cheating them or that we are.

- **Explain, never recalculate.** Every figure is one the carrier already
  wrote, copied across. A homeowner may repeat any of them to their adjuster,
  so a number this page produced would be a number we invented. Same boundary
  as `carrier_scan`'s "transcribe, never calculate", and the reason
  `_insurance_rcv_total` exists on the other side of it.
- **The carrier's own arithmetic is CHECKED, not performed.** `reconciles()`
  tests ACV + depreciation = RCV against their figures. When it does not hold —
  legitimately, for non-recoverable depreciation or pay-when-incurred lines —
  the page says the figures may not subtract evenly instead of printing a
  subtraction the homeowner can catch being wrong, which is what would stop
  them believing the rest of it.
- **A missing figure stays missing.** `_num()` returns None rather than 0, the
  row is skipped, and the writer is handed only the keys that exist: "your
  carrier withheld $0.00" is a sentence about a claim nobody imported, and a
  None in the payload is an invitation to invent one.
- **`has_enough()` refuses to build a page with nothing to say** — RCV plus a
  depreciation or a deductible is the floor. A logo over a paragraph of
  generalities is worse than not offering the document.
- **Nothing of ours is on their page**: no pricing, no margin, no build cost.
  Pinned by a test that puts our figures on the estimate and checks none of
  them reach the PDF.
- **It builds with no API key.** `fallback_narrative()` is a template over the
  same figures — less warm, equally correct, and it carries the one paragraph
  that answers their actual question. A homeowner waiting to understand their
  claim should not be held up by a key.
- Guarded by `tests/test_claim_explainer.py`.

## The work order in Spanish, beside the English

`estimator/crew_spanish.py`. The crews building these roofs are substantially
Spanish-speaking and this sheet was English-only. That matters most at the one
place the document is designed to catch an error: the ventilation block prints
installed square inches against required and says *SHORT by N*, a number put
there so a wrong calculation fails in front of whoever is on the roof rather
than silently in a test. In a language the crew does not read, it fails
silently anyway.

- **English prints FIRST and stays the authority.** It is what the contract,
  the inspector and the office speak, and a translation nobody in the office
  can check is one nobody should trust. Side by side, a bad line is visible to
  anyone who glances at the sheet.
- **Fixed labels are a STATIC TABLE, not a model call.** `LABELS` is closed and
  finite — free, offline, instant, reviewable in a diff, and incapable of
  drifting between two printings of one sheet. Asking a model to translate the
  word "Customer" on every build would buy latency and variance for a worse
  answer. `test_crew_spanish.py` reads every `L('…')` call and every
  `detail_rows.append` label out of `app.py` and fails on one the table has
  never heard of, so the two cannot drift.
- **Only the rep's free-text crew notes reach a model**, because they are the
  one part of the sheet nobody can know in advance.
- **A figure that changed in translation is REFUSED.** `numbers_survived()`
  compares the numeric tokens as a multiset and `_work_order_notes_es()` throws
  the translation away and prints English only when one went missing. Nobody
  here reads Spanish well enough to catch a changed quantity, and a changed
  quantity is what the crew would build to. The ventilation verdict never goes
  near a model at all — `state_line()` carries its figure across by formatting.
- **Nothing about this can break a work order.** No key, no network, a refusal
  or an empty answer all return `''` and the English prints. Most of the sheet
  is labels, so the bulk of the value costs nothing and works offline.
- `work_order_bilingual` in ⚙ Settings → 👷 Crew Docs, **default ON** (absence
  means on). A new pane needs its `SETTINGS_TABS` entry as well as the
  `settings-pane` class, or it is unreachable.

**RoofR sits beside the carrier import now.** Same shape of problem, no
translation involved: an insurance job needs two documents and they were in
different places — the carrier PDF had a button on the Insurance tab, the
measurement report was three levels into the ⋮ menu. Without the measurements
the cost side is sized off nothing, the margin is unknowable, and the Claim
Check that finds a supplement has nothing to compare against; Xactimate exports
carry no measurements at all and Xactimate is most of this company's volume.
`roofrImportBtn()` is one builder serving both sites, and it says whether the
report is already in — "do I still need to do this?" is the only question a rep
has when they look at it.

## Review before sending (`estimator/estimate_review.py`)

A second estimator reading the job before it reaches a homeowner — the pass a
two-rep company cannot staff. 🔍 Review beside Send / Sign;
`POST /api/estimates/<id>/review`, rep-level, because it is their estimate and
their send.

Everything it checks, the tool already knew. `_vent_nfa_report` prints
installed square inches against required, `estimate_margin_report` knows the
worst package on offer and which tiers have no cost behind them, `_est_expired`
knows whether the pricing still stands. What did not exist was anything reading
all of it AT ONCE, at the one moment it matters.

- **It computes NOTHING.** `_review_facts()` assembles numbers from the
  functions that already own them. In margin mode sell derives FROM cost, so a
  review that re-derived a margin would be a third implementation of the money
  math this repo keeps exactly two of and holds to the cent.
- **Two independent layers.** `deterministic_findings()` is rules over those
  numbers — free, offline, no API key — and it is the layer worth acting on: an
  expired quote, ventilation short of code, a tier with no cost, an insurance
  job with no measurement report. `ai_findings()` is a reader for what no rule
  expresses. A reader that is down, rate-limited or unconfigured never costs
  the rule findings, and `reviewer_error` says so out loud, because a review
  that quietly half-ran reads exactly like a clean estimate.
- **It informs; it never gates.** `_margin_floor_block` is the one thing in
  this system that stops an estimate leaving, with its own settings and its own
  tests. A second gate that disagreed with the first is how a rep ends up
  unable to send a job neither of them can explain.
  `test_no_send_path_consults_the_review` walks every send route rather than
  naming one, so a new one cannot quietly acquire a gate.
- **The reader is never shown a price.** `_review_trade_summary()` passes line
  names, quantities and units and no money at all. It is asked what is MISSING
  from a scope, never whether a price is right — what a roof should sell for is
  between this company and its market, and a number absent from the payload
  cannot reach a finding.
- Guarded by `tests/test_estimate_review.py`.

## Margin, expiry, and the safety net

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
  **Expiry is measured in Colorado, not UTC** (`_company_today`, 2026-09-15).
  The server runs in UTC, so from 6pm Mountain "today" is already tomorrow
  there: an estimate held until today showed the expired card, and 410'd the
  signature, for the last six hours of its own last day. No tz database falls
  back to UTC, which can only expire a quote early — never late.
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

## Optional upgrades — what the homeowner elects for themselves

Priced extras the rep offers but does not include: gutter guards, an
impact-rated shingle, a second run of ice & water. The homeowner ticks the ones
they want on the /sign page, next to the color picker and the initials, and
what they ticked joins the contract they sign. The insurance T&C had promised
this for as long as it existed — *"plus the cost of any non-covered upgrades
elected by the Homeowner"* — with nowhere in the tool to record one, so every
upgrade a rep actually sold was a verbal agreement and a change order later.

The rep builds the list in 🎁 **Optional Upgrades** on the Pricing tab
(`renderUpgradesPanel()`), typing a row or pricing one off the price book. The
data lives in `est['upgrades']` — `{enabled, items:[{id, name, description,
price, cost, accepted}]}` — outside `trades`, because an upgrade belongs to the
job rather than to one trade's tab.

Three rules carry it, all pinned by `estimator/tests/test_optional_upgrades.py`:

- **The price is STORED, never derived.** A price-book pick prices the upgrade
  once, through the same margin chain as any other line, and writes the number
  down. Deriving it on read would tie an upgrade's price to whichever package
  the customer happens to be looking at, so a homeowner who ticked $1,450 of
  gutter guards and then tapped Good would watch the number move under them —
  and next week's price book would silently reprice a contract somebody already
  holds a link to. Same rule, and the same reason, as a bundle pick COPYING its
  tagline onto the estimate.
- **Nothing counts until the customer ticks it.** `accepted` is written by the
  /sign POST and by nothing else, so an offered upgrade is worth $0 in every
  total, in the margin floor and in the funnel until a homeowner elects one.
  That is what keeps an upgrade an option rather than a quiet price rise, and
  it is why adding `upgrades_total()` to `calc_selected_total()` moved not one
  unsigned estimate. `upgrades_offered()` is also the gate on `accepted_upgrades()`,
  so switching the block off or blanking a price withdraws the election with it
  rather than leaving a total nothing on screen explains.
- **An elected upgrade with no cost reports an UNKNOWN margin, not a perfect
  one.** `upgrade_cost()` treats a blank *and* a 0 as "not costed" — deliberately
  the opposite of the rate chain, where an explicit 0 is a real choice a rep can
  make. `estimate_margin_report()` then leaves the upgrades out of both sell and
  cost rather than adding price to one side and nothing to the other, which
  would raise the reported margin in exactly the flattering direction, and names
  them in `upgrades_uncosted` instead.

Four more things are load-bearing:

- **A signature is a price agreement.** Every tick row carries the price the
  page showed it in a hidden `upgrade_price_<id>` field, and the POST 409s
  ("refresh and choose again") when it no longer matches — the same answer a
  stale package pick already gets. Without it a homeowner ticks $1,450 and signs
  an $1,850 contract. A tick with no price echoed back is refused for the same
  reason: fail closed. So is a tick on an upgrade the rep has since withdrawn,
  because the customer believed they were buying it and signing them up for the
  job without it is the one outcome nobody would notice.
- **The election is the customer's, and a whole-doc save may not undo it.**
  `SERVER_MANAGED_FIELDS` cannot cover `accepted` — that rule restores a key
  only when the save omits it, and a rep editing the panel always sends one — so
  `save_estimate()` re-applies it by id. A rep with the estimate open from before
  the signature would otherwise autosave the election straight off the contract
  and take the total with it. Adding, editing and deleting rows still works;
  only the tick is not theirs to set.
- **An insurance claim total is never inflated by an upgrade.** `_estimate_total()`
  is the CONTRACT (claim + elected upgrades, which is what the funnel and the
  leaderboard should see); `_insurance_rcv_total()` is the carrier's own number
  and is what `insurance_cost_report()`'s `claim_total`, the /sign sticky bar and
  the printed claim total read. A customer may repeat that figure to their
  adjuster. The PDF relabels its total bar to *Claim + Upgrades* rather than
  quietly restating the claim as a bigger number.
- **The cost of an elected upgrade files as MATERIAL in the permit packet**, for
  the same reason job extras do — the packet prints Cost Total = materials +
  labor. The row lives in `_packet_cost_rows()` rather than
  `_cost_split_by_trade()`, whose every row is walked back into `est['trades']`
  by `tests/test_cost_split.py`; without it the packet's rows stopped adding up
  to its own TOTAL line, which prints `_estimate_total()`. `_packet_upgrade_note()`
  says so when an elected upgrade has no cost to contribute.

Mirrored pairs: `upgrades_total()` / `upgradesTotal()`, `upgrade_cost()` /
`upgradeCost()`, `upgrades_offered()` / `upgradesOffered()`, `accepted_upgrades()`
/ `acceptedUpgrades()`, `upgrades_cost_total()` / `upgradesCostTotal()`. They are
part of the money math `selectedTotal()` prices with, so `tests/parity_runner.js`
lifts them and `tests/test_parity.py` carries elected-upgrade fixtures;
`tests/insurance_cost_runner.js` lifts them too.

The customer-facing renderers are `_cv_upgrades_block()` (the tick list, in the
sign form), `_upgrades_cv_table()` (the signed page) and `_printUpgradesHtml()`
(the browser-built PDF). The last two print the MENU with no subtotal on an
unsigned document and the ELECTION with one on a signed document: a customer
laying two bids side by side must not read an optional extra as part of the
price. The `Offer to customer` switch in the panel is the one control for
whether the block appears — deliberately no Print Pages chip beside it, because
two controls for one field is how they end up disagreeing.

## Insurance job margin

On a retail job the rep sets the price and the margin follows. On an insurance
job **the carrier sets the price and the margin is whatever is left after we
build the roof** — and the tool could not see it at all. Insurance line items
carry the carrier's `unit_price` and never our cost, so an insurance estimate
reported no margin, was excluded from every margin figure on the analytics tab,
and the question "is insurance work worth doing" had no answer here. Most of
this company's work is insurance.

The cost side is **derived, not typed**: a carrier export runs 30-80 lines and
nobody was ever going to cost them by hand. Picking the roofing system being
installed builds cost lines from the price book, sized by `measuredQty` off the
same `S.measurements` the retail side uses — which is why importing the
measurement report matters on an insurance job. `insurance_cost_report`
(app.py) and `insuranceCostReport` (app.js) are the mirrored pair; the runner
`tests/insurance_cost_runner.js` holds them to the same numbers, the same way
`parity_runner.js` does for retail pricing.

**RoofR is the source of truth; the carrier's numbers are a claim about it.**
The Claim Check panel above the margin does two jobs the margin cannot do
alone. It splits the carrier's lines into roof and not-roof, because adjusters
routinely file gutters, fascia and interior drywall under a roof plan and
those dollars have **no matching cost on our side** — counting them reads as
pure profit, and flatters exactly the claims where the adjuster bundled in the
most. And it compares `S.measurements` against the carrier's approved
quantities, because a shortfall there is a supplement, which is the only lever
an insurance job's margin has.

Three rules keep that honest:

- **Classification is a stored DECISION, never re-derived.**
  `classifyCarrierItem` (app.js) guesses from keywords and the rep can
  override; `_roof_only_rcv` (app.py) reads whatever is stored. A second
  classifier would be a second thing to drift. A line with no `scope_class`
  counts as roof — what the tool did before any of this existed.
- **An unrecognised line is `review`, not a guess.** It counts toward roof so
  the total never silently shrinks, and it is listed for the rep to confirm.
- **A measure takes the LARGEST matching carrier line, not the sum.** A
  tear-off and an install of the same roof are two lines describing one
  surface; adding them reports double the roof and invents a supplement that
  is not there.

Five more things are load-bearing:

- **The cost items live OUTSIDE `trades`**, under `insurance_cost`. Nothing
  that builds a customer-facing document can reach them, which is the only
  thing standing between an internal cost sheet and a homeowner reading our
  labor rate off their own proposal.
- **A job with no cost entered has an UNKNOWN margin, not a 100% one.** The
  same rule the retail floor already follows — otherwise every un-costed claim
  sorts to the top of the profitability table.
- **A cost line priced at $0 makes the margin a fiction, and it is named.** A
  freshly seeded roofing bundle ships Tear-Off Labor, Install Labor, drip edge,
  ridge cap and starter at `0`, so an uncorrected book reports a roof that
  costs only its shingles and the margin lands 20-30 points high — in the
  direction that makes a bad job look good. `unpricedInsuranceCostLines` finds
  them, the panel names them in red and labels the figure *overstated*, and
  **`/api/analytics` leaves that job out of the margin entirely** rather than
  banking a number the price book cannot support. Same trap and the same answer
  as `unpricedBundleLines` on the commercial side.
- **Supplements are revenue, and they are the only lever.** A rep cannot raise
  a carrier's price by editing the estimate, so `supplements` adds to the
  revenue side and is where a thin claim gets fixed. This is also why the
  margin floor deliberately does **not** gate an insurance send — a block there
  would be a wall with no door.
- **`refreshInsuranceCostQuantities` runs from `applyMeasurements`.** A
  measurement report is imported *after* the system is picked as often as
  before it; without this the margin quietly reports the cost of a zero-square
  roof. Hand-edited quantities set `qty_locked` and are never recomputed.

Guarded by `tests/test_insurance_margin.py`.

**Ice & Water was billing linear feet at a per-square price** (fixed
2026-09-08). `a_ice_water` carried `unit: 'SQ'`, `cost: 46.46` and
`measure: 'eave_valley'` — which returns LINEAR FEET — with no `bundle_lf`, so
400 LF of eave+valley billed as 400 squares: **$18,584** of membrane on a
32-square roof, a 33× overcharge. Not an insurance-only fault: the insurance
cost sheet was measured against the retail builder and both produced
$15,539.92, which is what proved the derivation faithful and the price book
wrong. In margin mode sell derives FROM cost, so it inflated the retail *quote*
too. It was first fixed as $95 a roll (`bundle_lf: 66.67`); **since 2026-09-15
it is priced per LINEAR FOOT** — `unit: 'LF'`, no pack size, `cost: 1.43`
($95 ÷ 66.67) — so the price follows the roof instead of jumping $95 at every
roll boundary. Whole rolls, with waste, are the material order sheet's job
(`_ORDER_PACK`), not the price's. Two things are load-bearing:

- **The unit and the cost always move together.** Per-SQ cost on LF footage was
  the 33× overcharge; the live book then carried the roll size with a per-FOOT
  price ($1.55), which quoted 400 LF as 6 × $1.55 = $9.30. `_PER_FOOT_CONVERSIONS`
  drops the roll size only while the cost already reads per-foot (under the pack
  size) — a manager's $98 roll keeps its roll, or it would bill $98 a foot.
  `_PRODUCT_COST_MIGRATIONS` walks an untouched 46.46 or 95 default to 1.43
  first. Line items already on estimates keep their stored roll count until the
  roofing package is re-picked.
- `tests/test_ice_water_fix.py` pins all of it, including the $18,584 figure as
  the thing that must never come back.

**Ridge vent is sized for the FULL code exhaust, never the shortfall**
(2026-09-16). Ticking Install Ridge Vent also decks over every existing box
vent — `injectVentItem` adds the Vent Plug line — and the ridge footage was
sized on what was left *after* crediting those same vents. A 30 SQ attic with
six turtles ordered 6 sticks: 432 sq in against 720 required, **40% short**,
and the more vents the house already had the shorter it came out. The vents
being removed cannot pay for the ones being installed. `needs_ridge` is still
the deficit question — *is this roof short as it stands* — and that is what the
below-code banner reads; only the SIZING changed. `ridge_vent_code` is
ungated for the same reason: a roof whose box vents already met code ordered
0 LF and then lost them. NFA constants are `NFA_RIDGE_SQIN_LF` 18 per LF (72 a
4-ft stick, confirmed by Luke 2026-09-16), `NFA_TURTLE_SQIN` 50 and
`NFA_INTAKE_SQIN_LF` 9. Attic area still falls back to roof squares × 100 when
Attic Area is blank — the sloped area, so ~12% high on a 6/12, which
over-vents rather than under-vents. Pinned by `tests/test_ventilation.py`.

**The vent math prints itself** (`_vent_nfa_report` / `ventNfaReport`). The
sizing above was wrong for two months and every test agreed with it, because
the tests were written from the code: the example used a roof with no box
vents, where both rules give the same answer, and the other test pinned the bug
as correct. Parity tests did not help either — both copies agreed perfectly
about the wrong number. So the Scope panel and the work order print **installed
sq in against required**, per side, and say *SHORT by N* when they are. A test
fails when someone runs it; this fails in front of whoever is on the roof. The
ridge line's quantity is STICKS (it carries `bundle_lf`), so the pack comes off
before multiplying by NFA per foot, and box vents count only while no Vent Plug
line is decking them over.

**Intake vent is sized by code, not by the eave** (2026-09-15). The checkbox and
the `a_intake_vent` product both used `measure: 'eave'`, so a 250 LF eave billed
250 LF of intake where a 3,000 SF attic needs 80 (720 sq in ÷ 9 per LF) — and
Landmark and IKO Nordic carry the product inside the bundle, so that was every
job on those packages. `intake_vent_code` is half the 1/300 area ÷
`NFA_INTAKE_SQIN_LF`, **capped at the eave run**, and deliberately not gated on
the exhaust side: turtle vents say nothing about intake. Mirrored as
`intake_lf_required` in both `atticVentilation` copies; the work order prints the
priced footage. Pinned by `tests/test_intake_vent.py`.

**The PDF import did not work on an iPhone** (fixed 2026-09-08). Three separate
things in that path were true of a desktop browser and not of iOS, none of them
errored, and together they meant a rep tapped their RoofR report and nothing
happened. All three applied equally to the Xactimate import and are fixed in
both. `tests/test_pdf_upload_ios.py` pins each against the behaviour that
breaks it.

- **`accept=".pdf"` is not enough.** iOS resolves `accept` to UTIs to decide
  what is selectable in the Files picker, and a bare extension is handled
  inconsistently across versions where the MIME type is not — so the picker
  opens and every PDF in it is unselectable. Both inputs now say
  `application/pdf,.pdf`, which is what the photo input on the same page has
  always said. That input was the clue: whoever wrote it spelled both
  deliberately.
- **A `File` from `<input type=file>` is a handle iOS can take back.** Both
  importers did `_file = input.files[0]; input.value = ''` and then held that
  File until the rep tapped Apply — a minute later, across a `saveEstimate()`
  round-trip — to upload it as the attachment the customer file keeps. Clearing
  the input releases WebKit's backing store, so the later read comes back
  empty: the parse looks fine and the report silently never lands, taking the
  rasterized pages the ridge-vent markup tool reads with it.
  `snapshotPickedFile()` reads the bytes into a Blob we own and clears the
  input *after*, which still lets a rep re-pick the same file after a failed
  parse (`change` does not fire twice for one value).
- **The server gated on the filename.** `f.filename.lower().endswith('.pdf')`
  is a claim about what iOS chose to call the file rather than about the file,
  and a report picked from iCloud Drive or handed over by a share sheet does
  not reliably carry its extension. `_read_pdf_upload()` checks `%PDF-` and
  treats the name as a hint. An empty upload gets its own message, because
  that is what a released iOS file handle looks like on this end and
  "Could not read PDF: EOF" sends the next reader into the parser.

⚠️ **Not reproduced on a physical iPhone** — the fixes are all sound
independently, but which of the three was Luke's actual symptom is unconfirmed.

**Carrier imports: the layout is read, the result is proven, a miss is kept**
(2026-09-14). Xactimate's columns are the adjuster's choice, not a fixed
layout — Allstate prints AGE/LIFE, COND and DEP% with no TAX; Auto-Owners
prints TAX and none of the three — and a parser written for one matched zero
lines of the other. Three layers, and each one exists because the others can
fail silently:

- **Read the header.** `_xact_columns()` turns the PDF's own column row into a
  column list and each line is matched against THAT. An unrecognised column
  returns `None` rather than a guess: guessing is how a TAX figure becomes an
  RCV. Totals rows print only the columns that add up, so they are read
  against the header too, right-aligned. A new layout is one entry in
  `_XACT_COLUMN_WORDS`, never a new regex.
- **Prove it.** `_carrier_reconcile()` holds the lines to the carrier's own
  arithmetic: RCV = ACV + depreciation per line, and the document's Line Item
  Totals. The review modal shows the verdict green or red; a miss never blocks
  Load, it just cannot look clean. Section subtotals say where a miss is but
  do not decide one — two rooms sharing a name merge into one section.
- **Keep the miss.** Anything that did not reconcile, did not parse or named
  an unknown column is saved by `_keep_failed_carrier_pdf()` to
  `DATA_DIR/carrier_import_failures/`, newest 50, and listed in ⚙ Settings →
  📥 Import Failures. **Admin-only, not manager-up**: each one is a homeowner's
  claim. Real sample PDFs live in a gitignored `carrier_samples` folder beside
  the estimator and `estimator/scripts/check_carrier_pdfs.py` must pass on all
  of them before a parser change ships; the committed fixtures are synthetic.

**Symbility varies by carrier too, just not in its columns** (2026-09-16). Safeco
and Liberty Mutual print the same nine columns and differ in everything around
them: Liberty Mutual nests areas inside a plan and closes on the *plan's*
subtotal with no bare `Subtotal` row, so the plan subtotals are the checksum
(only when every plan with work printed one). It also rewords every claim-totals
label and itemises the tax per authority. A new wording is one more spelling
on a key in `_SYM_SUMMARY_LABELS`, never a second key.

**A scanned estimate is read off its page images** (`estimator/carrier_scan.py`,
2026-09-16). `_pdf_has_text()` catches a PDF with no text layer before format
detection, and Claude transcribes the pages into the same shape the two
parsers return, so the review modal and `_carrier_reconcile()` run unchanged.
Four things are load-bearing:

- **Transcribe, never calculate.** The prompt forbids correcting a figure, so a
  misread surfaces as a line that fails its own arithmetic instead of being
  quietly made to agree. That is also why `math_off` exists: a misread unit
  price or quantity leaves RCV, ACV and the carrier total untouched, so only
  qty × price + tax + O&P = RCV can see it, and it counts as a line off.
- **It runs as a job the browser polls** (`/api/parse-xactimate/scan/<id>`),
  because a vision read runs past gunicorn's 60s worker timeout. Job files sit
  on the volume, not in memory, since the poll can land on the other worker;
  each is a homeowner's claim, so it is deleted on collection, readable only
  by the rep who started it, and swept after `CARRIER_SCAN_STALE_S`.
- **No `ANTHROPIC_API_KEY`, no scan reading** — `carrier_scan.available()` is
  false and the rep is asked for the carrier's emailed PDF instead, exactly as
  before this existed.
- **An Xactimate scan still returns no measurements**, matching its parser:
  RoofR is the source of truth for those.

**Still open: Xactimate exports carry no measurements.** `_parse_symbility_pdf`
returns `roof_squares`; `_parse_xactimate_pdf` returns no `measurements` key at
all, despite the comment claiming both parsers return the same shape. It fails
safely — the front end reads `data.measurements || {}` — but it means an
Xactimate import leaves the rep to key the squares by hand, and Xactimate is
the majority of this company's volume. Deliberately NOT fixed by deriving squares from the
carrier's own `SQ` lines: RoofR is the source of truth by decision, and the
Claim Check exists precisely to catch the carrier being short. Importing the
RoofR report is the step that makes an insurance job costable.

## Commercial estimates (third estimate type)

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

## Commercial fastener calculator

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

## The internal cost split — Material vs Labor

The rep-only **Cost & Profit** panel reported `Labor $0.00` on every estimate
ever written, and the permit packet printed $0 in the Labor column beside it.
Labor was priced the whole time — `l_install` has been $145/SQ on production,
`sl_install` $450, `cl_labor_reroof` $400 — it was just filed as material,
because a catalog product carries ONE `cost` and every seeding path drops all
of it into `material_unit_cost` and hard-writes `labor_unit_cost: 0`.

Nothing about the money was wrong. material + labor was always the same number,
which is exactly why it survived so long. The split was the lie.

- **`cost_class` on a catalog product is `'material'` or `'labor'`, and ABSENCE
  MEANS MATERIAL** — which is what the tool did before the field existed, so an
  unclassified product moves no number anywhere.
- **It may only ever influence the SPLIT.** Never a total, a sell price, a
  customer-visible gate, a margin floor or a quantity. If that ever stops being
  true, a manager reclassifying a product retroactively changes what a customer
  was charged. `tests/test_cost_split.py` pins it.
- **Derived at READ time; nothing stored is ever rewritten.** Same house rule as
  `_norm_est_status`. This is the whole reason it could be applied to every old
  estimate: in margin mode sell derives FROM cost, so a pass that rewrote saved
  costs would move prices on estimates customers already hold links to.
- **A whole-line bucket assignment, never a ratio**, so material + labor equals
  the stored cost BY CONSTRUCTION rather than by arithmetic that rounds well.
- **One classifier, and it only ever WRITES.** `_guess_cost_class` /
  `guessCostClass` run at seed time, at backfill time and in the Price Book
  editor; `_cost_class_of` / `costClassOf` read `cost_class` and stop. Same
  contract as `classifyCarrierItem` — the guess is a starting point, the stored
  decision is the answer. `cost_split_runner.js` holds the two to the same
  decision on the same row.
- **The exclusion list runs FIRST**, and that ordering is the trick:
  `x_ss_delivery` is "Metal Delivery & Rollformer Set-Up", a supplier charge a
  keyword match on "Set-Up" files as crew time. And **`crew` is deliberately not
  a labor word** — `a_ss_clips` is "Seam Clips + Pancake Sc**rew**s".
- **Job extras are material, not a third bucket.** The permit packet prints
  `Cost Total = materials + labor`, so an `'other'` bucket either drops out of
  that column or gets folded back in anyway. Two values is the decision.
- **`_PRODUCT_BACKFILL_FIELDS` only walks SEED ids**, so a second loop in
  `_ensure_bundle_catalogs` classifies every live product the seed has never
  heard of. Six of the labor products actually sold on production are
  manager-created `p_<uid>` rows — between them the labor on every metal and
  painted-siding job.
- **Build-time routing was considered and rejected.** Writing the catalog cost
  into `cell.labor_unit_cost` cannot replace the read-time rule (old estimates
  still need it), freezes a misclassification where read-time self-heals on the
  next open, creates a permanent third data state, and `setTradeMode` destroys
  it on one mode toggle anyway. The `lab > 0` branch keeps the door open.

**A second reader checks the split** (`estimator/cost_class_review.py`,
2026-09-20). `_guess_cost_class` is a keyword match, and its carve-outs are a
record of the traps somebody already hit rather than of the traps that exist —
`crew` is not a labor word because `a_ss_clips` is "Seam Clips + Pancake
ScREWs", and the exclusion list runs first because `x_ss_delivery` is a $368
supplier charge that reads like crew time. ⚖️ Check Material/Labor in the Price
Book Audit modal asks something that reads a name the way a person would.

Four things keep it safe, and all four are the house rules rather than new ones:

- **It only ever PROPOSES.** `review()` returns a diff; `apply()` takes only the
  ids a manager ticked. Same contract as `classifyCarrierItem` and the
  jurisdiction verifier — the guess is a starting point, the stored decision is
  the answer.
- **It is never in the request path.** `_ensure_bundle_catalogs()` runs on every
  price-book GET, so a model call there would put money and latency on a screen
  a rep opens all day.
- **`_guess_cost_class` stays, and stays first.** Free, instant, no API key and
  no network, and it classifies every new product the moment it is created.
  With no `ANTHROPIC_API_KEY` the review reports unavailable and the price book
  behaves exactly as it did before the module existed.
- **A proposal is checked back against what was sent.** An id the book does not
  have, a class that is not one of the two, and a "change" to the class already
  stored are all dropped before a manager sees them: the model is a second
  reader, not a second source of ids.

Both endpoints are manager-up, like the audit beside them. `apply()` can only
move a cost between the two internal columns — never a total, a sell price, a
margin floor or a quantity, which is the contract `tests/test_cost_split.py`
already holds down — so the worst an approved mistake does is misreport the
split it was meant to fix. Guarded by `tests/test_cost_class_review.py`.

**The Simple-mode tier collapse is diagnosed, not repaired.** `setTradeMode`
GBB→Simple folds three tiers into one flat `unit_cost` but leaves
`tier_bundles` pointing at three bundles — so the rep shows a customer three
packages priced identically and cannot see why. The per-tier costs are
genuinely gone and nothing can recover them. `simpleTradeTierConflicts()` names
the contradiction and offers `setTradeMode(trade,'gbb')`, and the panel renders
ONE column headed *Flat priced* rather than three identical ones. It also uses
`enabledTiers()` now, so a rep who turned Best off stops seeing a Best column
here when they see it nowhere else.

## Where catalog/bundle data must live

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
  have saved past them. A cost migration's value is a **list of steps**, because
  one product can need to reach today's number from more than one previous seed
  — `m_standing_seam` has to arrive from the original $400 placeholder *and*
  from the $320.25 that replaced it, or a book that never saw the first
  correction is stranded on it. The first matching step wins and stops.
- **Nor does anything else `_PRODUCT_BACKFILL_FIELDS` covers, once it is
  present.** That backfill fires only on ABSENCE, which is right for a field a
  manager may have cleared and useless for one that needs *correcting*:
  `a_ss_zeecee` has had a `measure` in every live book since the day it
  shipped, so fixing the seed reached nobody. `_PRODUCT_FIELD_MIGRATIONS` is
  the non-cost twin of the cost migration and applies the same equality test —
  rewrite only while the live value is still the previous seed's.

**Where a deliberate cost buffer belongs.** Standing seam costs are stored
DELIVERED — supplier pre-tax price × `_SS_UPLIFT` (`_SS_TAX` × `_SS_BUFFER`),
with `_SS_PRETAX` holding what the sheet actually said. Two reasons to keep it
that shape. Material sales tax is a real cost the book has no line for
anywhere, so every trade's cost is ~4% light. And a cushion put in *one factor*
is the same cushion on every roof; when it was instead an accident of which
unit prices happened to be stale, it measured +5.3% on a simple gable and
−3.6% on a wall-heavy one — the cut-up roofs that most need a cushion were the
ones without it. `tests/test_standing_seam.py` holds every literal to
`_SS_PRETAX × _SS_UPLIFT`, which is what keeps the buffer a decision rather
than a residue.

**PBR exposed-fastener metal (`b_pbr`) is the same supplier on the same
roof** (EFC38429 vs EFC38421), so `_PBR_PRETAX` takes the same `_SS_UPLIFT`.
One trap between the two sheets: the `(N LIN)` on a panel line means
**different things per profile**. On snap-lock `20 LIN` is the coil and a 16"
panel comes off it; on PBR `36 LIN` is the net coverage. Check which by
multiplying the ordered LF by the coverage — it has to cover the roof, and on
EFC38429 anything under 36" does not. `tests/test_pbr_metal.py` reprices the
whole quote off its Roofr report to within 0.5%.

## Price book audit (`/api/pricebook/audit`)

Everything this tool says about money is derived from the price book: retail
quotes (in margin mode sell is derived FROM cost), the margin floors, the
insurance job margin, and every margin figure on the analytics tab. A wrong
cost is not one wrong number, it is four — and the more the tool is trusted the
more confidently wrong it gets. Two real faults were found *by hand* in the
first bundle anyone opened, which is what this exists to stop.

`pricebook_audit()` reports the SHAPE of an error and never the right value —
what a square of shingles costs is between the manager and the supplier
invoice. 🔍 Audit in the Price Book modal, manager-up. What it looks for:

- **`unpriced`** — a product a bundle actually sells, with no cost. Products
  nobody sells are ignored: 780 products and 744 findings is noise, and a
  noisy audit is one nobody finishes.
- **`unit_mismatch`** — the fault that motivated this. A product priced per
  `unit` whose quantity comes from a `measure` returning a different dimension,
  with no `bundle_lf` conversion. `MEASURE_DIMENSIONS` records what each
  MEASURE_DEF returns, read off its own on-screen label ("Eave + Valley LF" is
  linear feet), and `tests/test_pricebook_audit.py` parses `app.js` and fails
  if a measure is missing from it or its label stops agreeing — a measure that
  escaped the map would make the audit go quiet rather than fail.
- **`pack_cost_unconverted`** — a product bought in packs whose cost looks like
  the per-foot price. `bundle_lf` is a DIVISOR, so the quantity is a count of
  packs and `cost` has to be the price of one pack; `a_ice_water` went live at
  `1.55` with `bundle_lf: 66.67`, which is $1.55 a roll where a roll is ~$95 —
  400 LF of eave+valley costed at $9.30 instead of $570, and in margin mode
  under-quoted the customer too. The test is `cost < bundle_lf`, i.e. "the
  implied rate is under a dollar a foot": a SHAPE, never a value. Nothing
  rewrites the number — `_PRODUCT_COST_MIGRATIONS` fires only on the exact
  previous seed, and a live `1.55` is a manager-typed value, so the audit is
  the only honest mechanism for it.
- **`orphan`** — a bundle selling a product id the catalog does not have.

**Commercial is exempt from the no-cost check.** Its $0 material costs are
deliberate — pricing comes off a per-job supplier quote and
`unpricedBundleLines` already warns per bid. Listing ~40 intentional
placeholders would bury the faults that are faults.

## Jurisdiction code lookup (`/api/jurisdictions/<id>/verify`)

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

## Demo mode — one link, no company data

`/estimate/demo/<token>` is a guest session for showing the estimate tool to
someone outside the company. **An admin creates the link in ⚙ Settings →
🎬 Demo Link**, which writes a 256-bit token to `PORTAL_DATA_DIR/demo_token.txt`;
`P1_DEMO_TOKEN` overrides the file and is the emergency kill. With neither
there is no route and no session key honoured. Add `?reset=1` to put the
seeded estimates back before the next audience. Tests:
`estimator/tests/test_demo.py`, `portal/tests/test_demo.py`.

*It was env-var-only first, and that was wrong in a specific way worth
remembering: the admin who needs the link is the one who cannot restart the
service to get it, so "off by default" meant "off, and the only way on is a
redeploy". The protection is identical either way — the token is the whole
thing, exactly as it is for `/sign/<token>` — and revoking is now a button
rather than a variable somebody has to remember the name of.* `POST` rotates
(the old URL dies on the spot, including for sessions already open, because
`active()` re-checks the token on every request); `DELETE` switches it off.
`/api/demo-link` is **admin-only, not manager-up** — a demo can be capped at
manager via `P1_DEMO_ROLE`, so a manager minting one could out-reach
themselves — and it is deliberately absent from `ALLOWED_ENDPOINTS`, or a
guest would hold the key to their own session.

Two modules because two different things are being decided. `portal/demo.py`
owns the guest IDENTITY, and has to: all four apps share one cookie and the
app-switcher bar renders from the PORTAL's `/api/me`, which would otherwise
take a demo session down its "row deleted out from under a live cookie" branch,
call `sign_out()` and bounce the guest to a login page a second after the app
loaded. `estimator/demo_store.py` owns what that guest may then do.

Four things are load-bearing:

- **A demo session sets neither `session['username']` nor `session['user']`.**
  Those two keys are what the canvasser, the CRM and the portal's own guard
  read, so a guest holding the shared cookie is anonymous to all three. The
  estimator is the only app that asks `demo.active()` anything.
- **`demo.active()` routes the LIST operations; `demo.owns(id)` routes the
  BY-ID ones**, and conflating them breaks the demo's best screen. Two paths
  reach a demo estimate with no demo cookie in sight — the customer's browser
  POSTing a signature to the public `/sign` link, and the background thread it
  starts. Routed by session, those go to the real store, where the id does not
  exist, and signing the demo estimate fails with "no longer available for
  signing". Every `est_*` helper in `app.py` therefore checks `_demo_est()`,
  which is either.
- **`ALLOWED_ENDPOINTS` is default-deny**, same shape and same reason as
  `PUBLIC_ENDPOINTS`: a route added tomorrow is closed to a guest until someone
  opens it on purpose. Config GETs are open and every matching write is not —
  one guest saving the price book changes what every rep quotes.
- **The document, not the session, is what says "demo" on the way out.**
  `_post_sign_pipeline`, `send_signature_notification`, `_notify_expired_view`
  and `_funnel_record` all run from the public `/sign` route or a thread it
  spawned, so they check `is_demo_doc()`. Without that, signing the demo mails
  a real address, files a Contact and a Project in The Den, and puts a
  fictional dollar value into the CRM funnel — which the CRM drains on every
  board read, so it would land in the pipeline and on a leaderboard.

**Costs are shifted, not zeroed** (`scrub_costs`, `P1_DEMO_REAL_COSTS=1` to
turn it off). The price book is the one genuinely competitive thing in this
app and a demo link gets forwarded. Zeroing was the obvious alternative and
does not work: in margin mode sell is derived FROM cost, so a zeroed book
prices every package at $0. The factor is a hash of the product's own id — so
it is stable across workers and reloads — and is never exactly 1, because "most
of these are wrong" is not a property anyone can rely on.

Deliberately NOT in the demo: photo upload, the visualizer (metered fal API),
the RoofR and Xactimate parsers (arbitrary file upload from a stranger),
jurisdiction verify (metered Perplexity), and everything that sends mail.

## Estimate outcome — `lost`, and why the rename was the small half

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
  timeline records it and the rep decides. See the `_FUNNEL_STAGE` note in `salescrm/CLAUDE.md`.
- Guarded by `tests/test_status.py` and `salescrm/tests/test_funnel.py`.

## The package tagline, and the Design Studio customer switch

**A bundle pick COPIES the price book's tagline into the estimate.** Editing
the book afterwards never reaches an estimate that already picked it, which is
why "I changed it and it didn't update" kept happening. The Pricing tab has a
tagline box per package column; ↺ Price book pulls the book's current line.
What the rep types is flagged in `tier_tagline_edited`, and that flag is the
whole rule: bundle copy goes stale with its bundle (Custom tier, gutted tier),
the rep's own line does not. Re-picking a bundle clears it. `_tier_tagline_edited`
(app.py) and `tierTaglineEdited` (app.js) are the mirrored pair. Two more traps:
a product's own tagline beats its bundle's (the bundle editor now says so), and
seed taglines are copy fields filled only on absence — shortening one needs its
old wording in `_BUNDLE_DESCRIPTION_MIGRATIONS` or it reaches no live book.

**A card is a promise list, not a parts list** (2026-09-15). It printed ten
bullets and then "+ 11 more items" — an inventory, to a homeowner holding two
bids side by side. An untouched card now shows the first `_CARD_BULLET_DEFAULT`
(6) bullets of whatever built it, and the Pricing tab has a What's Included box
per package; once the rep writes the list it prints exactly as typed, however
long. `tier_features_edited` is that flag and, like the tagline's, it survives
the staleness rule — a bundle pick clears it, because those bullets are the
bundle's. **Nothing truncates with a "+ N more" line any more**, on the /sign
card, the comparison PDF or the printed card. `_card_bullets` (app.py) and
`cardBullets`/`tierCardBullets` (app.js) are the mirrored set, with
`autofillTierBullets` mirroring `_autofill_tier_features` so the PDF the
browser builds and the page the server renders describe one package.
Pinned by `tests/test_package_bullets.py`.

**Customers do not see an estimate's Design Studio unless that estimate says
so.** It is a section toggle like the others — the 🎨 Design Studio chip in the
Print Pages bar, `page_visibility.design` — except it defaults OFF (only a
literal `true` shows it), because the studio is unfinished and every existing
estimate must start hidden. That inversion is why `togglePagePrint` has a
default-off list: the default-on flip formula needs two clicks to turn such a
chip on. `_design_studio_customer_on()` gates every customer surface — the
/sign block, the signed PDF page, the `/design/<token>` link (which 404s,
including links already sent) and minting that link — and nothing reps use.
Tests about the renderings themselves take the `design_studio_on` fixture.
Guarded by `tests/test_tagline_and_design_switch.py`.

**The ventilation markup marks two maps with one editor.** `VENT_MAP_MODES`
(app.js) switches `openVentCutinEditor` between the ridge cut-in
(`S.vent_cutin`, red) and the intake run (`S.vent_intake`, blue). Separate keys
and separate flattened images, so re-marking one never erases the other; the
work order prints each on its own sheet. Intake needs a map for the same reason
the ridge does: code footage is a fraction of the run, and the crew has to know
which fraction. Guarded by `tests/test_intake_vent.py`.

## Reassigning an estimate

Ownership is visibility — reps see only their own estimates — so who owns one
is not an ordinary field. `PATCH /api/estimates/<id>/salesperson` is the only
path that changes it: manager-up, except that a rep may claim an unassigned
estimate for themselves. **A whole-estimate save keeps the stored owner**, or a
tab opened before a reassignment autosaves the job straight back. The picker
reads `_roster()` (team.json plus portal accounts) instead of a hardcoded list,
and the funnel row's rep is moved with it. Guarded by `tests/test_reassign.py`.

## The customer screen — many estimates, one customer

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

## Monthly trends & sales goals

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
