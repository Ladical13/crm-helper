# Canvasser — the door-knocking app

*Directory-scoped notes: these load when work touches this directory, so the
root `CLAUDE.md` can stay short enough to be read every session. The traps
that cross all four apps — mobile, the clock, the session and mount rules,
deploying — live there, and they apply here too.*

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

## Hail by Address reads the radar archive

`hail/storms.py` was written to replace this endpoint — `history_at`'s own
docstring says so — and then nothing ever pointed at it, so the archive filled
while the tool the reps hold went on scanning five years of NOAA CSVs to answer
a weaker question slowly. `/api/hail/address` is that wiring.

- **SPC and MESH answer different questions and the response says which one
  answered.** SPC filtered reports are human call-ins, so the most they support
  is "a spotter reported 1.75 inch hail four miles from here"; MESH is radar
  over a continuous ~1km grid and answers "what size hail did radar estimate
  over THIS roof". The payload carries `source` (`mrms_mesh` / `noaa_spc`) and
  `renderHailAddressResults` branches on it rather than on the shape of the
  rows — presenting a call-in four miles off in the radar's language is the
  overclaim the archive exists to stop.
- **An empty archive falls back to SPC rather than to a blank screen**, and
  `_mesh_history()` returns `None` — never an empty result — to say so. This is
  the load-bearing distinction: a day with no qualifying hail IS recorded
  (`storms.record`), so "radar checked this roof and never saw hail" and
  "nobody ingested this period" are different answers, and only the first is
  safe to repeat on a doorstep. `ingested_dates()` is what tells them apart and
  `coverage` is what puts it on screen.
- **The reported size is this roof's own cell**, never a neighbour's and never
  interpolated — `Swath.size_at()`'s rule, and the whole reason MESH beats a
  radius search.
- **A lat/lng lookup touches no third-party service at all.** No geocoder, no
  NOAA. That is what makes "Hail here" answer instantly on the tapped
  structure. `_geocode_one()` reads `portal.geo`'s cache before Nominatim and
  writes hits back; misses are not cached, because `pgeo.lookup()` cannot tell
  a stored `nomatch` from an address it has never seen.
- Pinned by `canvasser/tests/test_hail_archive.py`.

## The overlay draws radar cells, not invented circles

The address lookup read MESH and the map beside it still drew SPC spotter
reports as `max(500, size * 800)`-metre circles — a damage footprint that
exists nowhere in the data, around points that are call-ins rather than
measurements. Two views of "where did it hail" on one screen, disagreeing
about both the data and the geometry.

`GET /api/hail/storms` lists the archive's own storm days (the picker, so a
rep chooses a storm instead of already having to know when it was) and
`GET /api/hail/cells` serves `hail.storms.cells_in()`.

- **Rectangles at the data's own resolution.** A cell is the only ground the
  radar made a claim about; a radius is a footprint somebody invented.
- **A cell hit on more than one day takes the MAXIMUM**, never a sum or a
  mean. MESH is already a maximum over its own window, and a mean shaves the
  peak off every multi-day range — the number that decides whether a street is
  worth knocking.
- **The viewport is filtered in SQL on the cell INDICES.** `cell_index()` turns
  the map bounds into an ri/ci range, so the database returns the screen rather
  than the state. All four sides or none: three sides of a box is not a box,
  and guessing the fourth returns the wrong ground.
- **Truncation keeps the BIGGEST hail and says so.** An arbitrary slice would
  hide the cells a rep most needs behind ones they do not. Same honesty rule as
  `list_pins`.
- **Three outcomes, never conflated**: cells drawn; no cells *with* coverage
  (radar looked at this ground and saw nothing — a useful fact); and no
  coverage at all, which falls through to the SPC reports, drawn as the circles
  they have always been and labelled in the popup as a call-in near there
  rather than a measurement of it.
- Pinned by `canvasser/tests/test_hail_overlay.py`.

## An appointment knows when it is, and books itself

Two gaps that were only worth closing together. An `appointment` pin mapped to
the `appt_set` stage carrying **no date at all**, so the pipeline asserted an
appointment existed and nothing anywhere knew when — and reaching the Pipeline
was a SECOND button, on a panel the rep had already walked away from, that only
appeared if a name happened to have been typed.

- **`pins.appointment_at` is LOCAL wall clock, stored without a timezone.**
  "Thursday at six" means six o'clock in that driveway. Converting on the way
  in is how 6pm becomes Friday for the six hours a day Colorado is behind UTC —
  the same trap `_company_today` exists for on the estimator side. The CRM's
  `due_at` IS UTC and is compared as text against UTC, so `apptToUtc()`
  converts once, in the browser, which is the only party that knows the rep's
  offset. It trims to the CRM's own second-precision spelling, because
  `...:00.000Z` sorts before `...:00Z` as text.
- **A mistyped time costs the rep the time, never the door.**
  `_clean_appointment_at()` returns `''` rather than raising: the knock is
  worth more than the field that was fumbled. Create and update both go
  through it, or the two paths store two different shapes.
- **A name is required on an appointment pin, and only on that one.** Without
  one the door cannot become a lead — no cadence, no task, no reminder, no
  leaderboard credit — so the rep does the hardest work of the day and the
  system records a coloured dot.
- **The handoff fires on save.** `handoffToPipeline()` is the single builder;
  `autoHandoff()` wraps it for the automatic path and the ✏️ Edit path, and the
  manual 📋 button calls the same function so the two can never produce
  different leads from one door. It is never fatal — the pin is already saved
  and the manual button is still there.
- **It runs again when a queued pin lands.** There was no network when the rep
  tapped Save, and the CRM — not the canvasser — owns what a lead is, so the
  lead cannot be queued beside the pin. `canHandoff()` refuses a pin that
  already carries `crm_lead_id`, which is what stops a retry making a second.
- Pinned by `canvasser/tests/test_appointments.py`.

## The offline outbox — a door saved in a dead zone is not a door lost

A pin POST that failed used to `alert()` and drop the pin: the one write this
tool exists to capture, thrown away at the exact moment it exists for. A rep
who loses a door once stops trusting the app.

- **Every queued save carries a `client_id` and `create_pin` is idempotent on
  it.** The common case is not a save that failed but a save that SUCCEEDED
  whose response never made it back, and the queue cannot tell those apart. A
  replay returns the stored row with **200** rather than 201. A duplicate is
  worse than a loss: a lost pin is a door nobody recorded, a duplicated one is
  a door two reps each believe the other knocked, and it inflates the
  leaderboard they are paid on.
- **The unique index is PARTIAL** (`WHERE client_id != ''`), because every pin
  written before this existed carries an empty string and a plain unique index
  would make the whole table one row. It is scoped `(rep, client_id)` — the id
  comes from a browser, so it is only unique per rep by construction, and an
  unscoped replay could hand one rep another rep's contact details.
- **The queue is IndexedDB.** iOS reclaiming a backgrounded tab is the normal
  end of a canvassing session, not an edge case.
- **No Background Sync, deliberately.** It is the textbook answer and it is the
  wrong one here: WebKit has never shipped it, and every rep runs this as an
  installed PWA on an iPhone. A `sync` handler would be dead code on precisely
  the devices the feature exists for, while reading as though the problem were
  solved. `flushOutbox()` is driven by the page instead — boot, `online`, and
  `visibilitychange`, which is what actually fires when a phone goes back in a
  pocket between streets.
- **A 4xx leaves the queue, a 5xx and a dead network stay in it.** A bad pin
  type will never succeed however often it is retried, and an outbox that never
  drains is invisible. A 401 stops the flush without dropping anything —
  `postPinRaw()` exists so a background flush reports instead of redirecting,
  because `api()` would throw a rep out of the app mid-street.
- **A queued pin is drawn for its own rep** (`pendingPinFrom`,
  `restorePendingPins`), dimmed and pulsing, and tapping it does NOT open the
  detail panel — it has no server id, so edit, delete and Add to Pipeline would
  all address a row that does not exist. Everything storage-related degrades to
  a no-op rather than throwing: private browsing and blocked site data are real
  states, and failing to drop a pin at all would be worse than losing the queue.
- Pinned by `canvasser/tests/test_outbox.py`.

## Known gaps, from the 2026-09-06 review

Found by reading the whole app, deliberately NOT fixed in the same pass, and
listed here because otherwise they live only in a chat log. Roughly in the
order they cost the business something.

- ~~A failed pin save is lost.~~ **Fixed 2026-09-20** — see the outbox note
  above. Left here because it was the top item for a reason: it was the gate
  on rolling this tool out to anyone.
- **No voice notes.** Typing at a door in February with gloves on does not
  happen, which makes this the highest-adoption feature available.
- **No photos on a pin**, so a rep at an `inspected` door has nowhere to put
  the hail strike on the downspout — the photo that is the whole adjuster
  conversation later. Per the customer screen note in `estimator/CLAUDE.md`,
  those belong on
  the CUSTOMER rather than on an estimate: the photo exists before an estimate
  does and must survive one being marked lost.
- ~~An `appointment` pin carries no date or time.~~ ~~"Add to Pipeline" is a
  second button a rep has to remember.~~ **Both fixed 2026-09-20** — see the
  appointment note above.
- **Nominatim is still used against its usage policy on the pin-drop path.**
  OSM's policy is 1 req/sec and forbids bulk use, and this runs from one
  Railway IP. Half-closed as of 2026-09-20: the hail-by-address *forward*
  geocode now goes through `portal.geo`'s cache first (`_geocode_one`), so a
  repeat lookup of the same street never leaves the box. Every **pin drop**
  still reverse-geocodes uncached, which is the higher-volume path of the two.
  When it is cut off, address autofill dies **silently** (`.catch(() => {})`).
- **`no_soliciting` is only a pin colour.** Fort Collins, Loveland and Greeley
  all run solicitation permits and no-knock lists; nothing warns the next rep
  walking up to one.
- ~~The map OVERLAY still draws NOAA SPC spotter reports.~~ **Fixed
  2026-09-20** — see the overlay note above. The SPC routes remain as the
  fallback for dates nobody has backfilled.
- **Nothing comes back from the CRM.** A pin gets `crm_lead_id` and then goes
  stale forever, so a door that became a signed roof still reads "Interested".
  That loop is the motivational payload of the whole tool.
- **No territory assignment and no re-knock protection**, so two reps can work
  the same street on the same day.
- **Every rep sees every rep's pins, including contact name, phone and email**
  — the opposite of the CRM's "reps see only their own leads". Worth being a
  decision rather than an accident of two codebases.
- **Map attribution is switched off** (`attributionControl: false`) while using
  Esri World Imagery and CARTO basemaps, both of which require it.
- **`hail_cache` grows forever** and nothing purges it.

Customer-facing, where the honest summary is that there is **nothing**: no
leave-behind for the 60–70% of doors that are Not Home, no way to text a
homeowner the storm report the tool already computes, no self-scheduling, and
no legitimacy artifact (rep photo, licence number, review link) for the
homeowner whose first question is whether this person is real.
