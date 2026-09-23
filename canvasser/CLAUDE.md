# Canvasser — field capture and Pipeline handoff

Flask + SQLite + Leaflet, mounted at `/canvass` behind the portal login.
Identity comes from `portal/users.py`; every authenticated route checks the
current account, and edits use the current role rather than a stale cookie.
The shared team map still exposes team pins; changing that visibility is a
separate product decision. Storage is `CANVASSER_DATA_DIR/canvasser.db`.

## Saved doors

- The server deduplicates retries with the partial unique `(rep, client_id)` index.
- The IndexedDB outbox writes before trying the network. A storage failure may
  still save online, but must never say an offline door was saved durably.
- Every queued entry carries its owner. Server and browser both refuse to
  transfer it to a different signed-in account. Older ownerless entries need
  the user's explicit recovery confirmation; they never flush silently.
- Validation failures stay on the phone and can be exported from the outbox
  badge. A failed save leaves the form open. No Background Sync dependency:
  boot, visibility and network recovery drive retries on iOS.
- Offline reopening uses the most recent local account/config snapshot, valid
  for at most fourteen days. Server actions still require a live session.
  Signing out through the canvasser, portal or shared shell clears that snapshot.
  Map tiles and live teammate data require a connection.
- Render contact fields and notes as escaped text. Never interpolate their raw
  values into HTML or attributes. The portal's shared origin makes a stored
  scripting vulnerability affect every mounted tool.

## Pipeline handoff

`POST /api/pins/<id>/handoff` reads the saved pin and calls
`salescrm/canvass.py`. The CRM transaction commits the lead, notes, appointment
and durable pin mapping together. A lost response or pin-link failure can be
retried without creating a second lead. The generic CRM lead-create endpoint
remains duplicate-friendly for separate deals.

`_sync_pin()` delegates to `sync_pin()` and records the saved fingerprint.
The old `crm_sync()` path that pushed doorstep contacts straight into Base44
is gone; the Pipeline owns subsequent conversion into production work.

- A new lead belongs to the pin's original rep even when a manager initiates sync.
- Come Back/Interested maps to Contacted; Appointment to Appointment Set;
  Inspected/Deal Closed to Inspected. A doorstep tap never claims a signed win.
- Pins retain local appointment time plus an IANA timezone. CRM tasks use UTC.
  Rescheduling updates the same meeting; clearing the appointment completes it.
- Retries preserve independent CRM edits unless the corresponding source field
  changed. Stage updates only advance and never undo a signed or lost result.
- `canvass_links` preserves source coordinates, so storm matching does not have
  to geocode a door the map already located. Do not move this into generic
  contact deduplication or throw away the source coordinates.
- New pins and edits attempt sync on the server. Unsynced fingerprints remain
  visible and the browser retries while open. No success notice may promise a
  meeting before its transaction succeeded.
- Team pins refresh periodically, preserving pending local entries. Nearby
  recorded doors appear as a warning in the drop-pin form.

## Hail

`hail/storms.py` owns the radar archive. Address history and map rectangles read
it; SPC reports remain a clearly labeled fallback. A radar grid estimate is
not proof of roof damage. Dates label rolling windows ending at 23:30 UTC.

Coverage is separate from hail detections: missing files, invalid radar cells,
legacy dates without metadata, out-of-area points and below-threshold results
must never collapse into a claim of no hail. The default archive threshold is
one inch. Map movement reloads the viewport and discards outdated responses.
Backfill is manager-only, runs on the server, and resumes completed or expired
jobs using a SQLite claim. It refetches legacy dates that lack coverage metadata.

Interactive geocoding uses `portal/interactive_geo.py`: cached responses and a
shared SQLite rate slot prevent the two workers exceeding Nominatim's limit.

## Assets and tests

Leaflet and markercluster are vendored under `static/vendor/`; no CDN dependency.
Each service worker deletes only old caches with its own app prefix. URL scope
does not isolate CacheStorage on the shared origin. APIs are not precached.
Any JS/CSS change bumps every version in `static/index.html` and `static/sw.js`.
Keep `viewport-fit=cover` and the safe-area CSS for installed iPhone layouts.

`tests/reliability_runner.js` executes actual browser functions under Node for
storage failure, ownership, retry, escaping, coverage and cache isolation.
Backend tests cover current identity, pin replay and archive behavior; the CRM
suite covers the transactional handoff and appointment updates.

Run `python run_tests.py` before committing. Deploy the whole repository as the
single service described by the root instructions. New additive schema changes
snapshot existing databases with `portal/migration_backup.py`; also take an
off-platform production backup before deployment.
