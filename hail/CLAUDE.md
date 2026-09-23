# Hail — shared radar archive

The archive is `HAIL_DATA_DIR/hail.db`, falling back to `PORTAL_DATA_DIR`, then
the repository root. It serves the canvasser, CRM storm matching, Nimbus and
storm alerts. `portal/backup.py` includes it in the database snapshot bundle.

## Ingestion and evidence

- `hail/ingest.py` reads NOAA's public S3 MRMS MESH archive. The source is
  `MESH_Max_1440min`, in millimetres; human-facing values are inches. Conversion
  happens once using `MM_PER_INCH`. Pillow decodes the GRIB2 PNG packing.
- One snapshot per completed date, ending at 23:30 UTC. This is a rolling
  twenty-four-hour maximum, not the exact calendar date or time hail fell in
  Colorado. Never quote the archive label as a precise local event timestamp.
- The current clip is Colorado. A date in the archive does not establish Texas
  coverage. Its 0.01-degree cells represent radar estimates, not roof inspections.
- The default stored threshold is one inch. An empty result means no retained
  qualifying cell in the available data, not that no hail fell or no damage exists.
- Missing HTTP files raise an ingest error. A failed refetch preserves the old
  event. Only a successfully decoded day may establish new coverage.
- `storm_coverage` stores compressed valid-cell row runs and the clip/window
  metadata. Invalid radar flags are not valid zeros. Legacy dates without this
  metadata remain queryable for positive detections but cannot prove negatives.
- `verified_dates()` drives backfill skipping. Filling a season repairs legacy
  dates as well as gaps. `ingested_dates()` remains the inventory of stored events.
- Re-ingestion replaces the event's cells and metadata transactionally. Do not
  merge cells: an early or invalid swath must not leave phantom hail behind.
- Cells use the global lattice, floor negative coordinates, and round float
  noise before flooring. Source cell centers are on the half-cell offset.
- A cell hit on multiple dates renders its maximum, never the average or sum.

## Operations

The estimator's `_check_hail_nightly()` runs once per company day after 12 UTC.
It reads the previous seven completed dates, refetching the most recent two,
then invokes CRM follow-ups and storm alerts independently. `HAIL_NIGHTLY=0`
disables it. This is not the Nimbus weekly scheduler.

Historical fill runs on the live server through the manager-only canvasser
backfill endpoint. Running the CLI locally does not fill Railway's volume.
Backfill uses a reusable SQLite job row and heartbeat expiry; a crashed worker
must not block all future fills. Progress is readable by either web worker.
Failed days remain outstanding rather than being marked checked.

`size_caveat()` and `size_note()` travel with large radar estimates. Preserve
source values rather than clamping them. All outputs must state that these are
estimates and do not prove damage. The historical `impossible` code identifies
an extreme estimate requiring verification; it must not be presented as a
physical bound on what weather can produce.

`hail/alert.py` prioritizes the Northern Colorado service area, with statewide
activity separately labeled. It marks sent only after the injected sender
reports success. Historical fills never directly send email. `join.affected()`
counts records it cannot place so callers can report missing coordinates.

## Verification

`hail/tests` checks grid arithmetic, encoding, units, event replacement, joins,
coverage and backfill recovery. The live ingest test is deliberately deselected
by default; explicitly run it with `pytest -m live` to verify a real NOAA file.
Run the full repository suites before committing changes used by other apps.

Schema upgrades take a local SQLite snapshot before adding tables or columns.
Take and verify an off-platform production backup before deploying migrations.
