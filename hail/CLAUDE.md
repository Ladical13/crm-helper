# Hail — the storm archive every tool reads

*Directory-scoped notes: these load when work touches this directory, so the
root `CLAUDE.md` can stay short enough to be read every session. The traps
that cross all four apps — mobile, the clock, the session and mount rules,
deploying — live there, and they apply here too.*

Hail is **not a canvasser feature**. It is the company's primary data product,
so it lives in its own package with its own database (`HAIL_DATA_DIR/hail.db`,
falling back to `PORTAL_DATA_DIR` — never to `DATA_DIR`, which is the
estimator's volume). The canvasser looks addresses up in it
(`/api/hail/address`) and draws it (`/api/hail/cells`), Nimbus joins against
it, storm-scout reports it, the CRM segments on it.

```bash
cd hail && pytest          # grid quantization, units, re-ingest, the join
```

**The archive is kept current by `_check_hail_nightly()`** in the estimator's
hourly loop — the same loop as the backups, which runs unconditionally and needs
no env var. Three steps, each independent: ingest, then the CRM books follow-ups
for leads under a 1"+ storm (`storm_nightly`), then `_send_storm_alert()` mails a
brief. It looks back `HAIL_WINDOW_DAYS` (7) so a missed night leaves no permanent
hole, and re-fetches the most recent `HAIL_REFETCH_DAYS` (2) even when held,
because `MESH_Max_1440min` is a rolling maximum still moving when read early.
`HAIL_NIGHTLY=0` switches it off.

*It used to pull two days and only two, which keeps up and can never catch up: a
night it did not run left a hole nothing would fill, and an old hole is
indistinguishable from a quiet day. A second nightly ingest was nearly added to
`agents/scheduler.py` before this one was found — it is easy to miss, because it
imports `backfill` under an alias. `agents/scheduler.DAILY` is what remains of
that, unused and tested, for the next daily job.*

**History is filled by `POST /api/hail/backfill`** (`?season=<year>` or
`?days=<n>`, manager-up), not by the CLI. It has to run ON the server: the
archive lives on the Railway volume and `railway run` executes against local
disk, so the command fills a database nobody reads. 202 days take about 170
seconds, which is well past gunicorn's 60s worker timeout — hence a background
thread and a polled `GET`, with the job row in SQLite rather than memory
because the poll can land on the other worker. Days already held are skipped,
so the button is safe to press twice. `hail/backfill.py`'s `run()` is the one
implementation the CLI and the endpoint share.

*The cost of the archive being empty was not theoretical. MESH holds a 1.91"
storm over Loveland on 2024-07-21, and the address lookup answered "no hail in
five years" about a roof inside that swath — because it fell through to the SPC
spotter reports, which had no call-in within ten miles. The tool behaved
exactly as designed and told a homeowner something false, because the database
it asked had never been filled.*

⚠️ **MESH reports sizes above the largest hailstone ever recorded.** The 2026
Colorado season holds 37 cells at or above 4.00 inches and two at 8.85 and 8.56,
against a US record of 8.0 (Vivian, South Dakota, 2010). MESH is a radar
*estimate* of maximum expected size, not a measurement, and it is known to run
high. Nothing currently flags this, so a rep can be shown — and can repeat to a
homeowner — a number that is not physically credible. That is the overclaim this
package exists to prevent, arriving from the other direction. Decide what to do
about it before the hail tools reach a rep.

**Why this exists at all: the canvasser's hail engine read the wrong data
product.** (Fixed 2026-09-20, both halves — the address lookup and the map
overlay now read this archive; the SPC routes stay as the fallback for dates
nobody has backfilled.) NOAA SPC filtered storm reports
(`canvasser/app.py`) are *human-called-in points* — a spotter phoned it in — so
they are sparse and biased toward where people are. A subdivision can be shelled at 2am and produce
zero reports. MRMS **MESH** (Maximum Estimated Size of Hail) is radar-derived
over a continuous ~1km grid, every cell, whether or not anyone was standing
there. SPC answers "did anybody report hail near here"; MESH answers "what size
hail did radar estimate over this roof, on this date". Only the second one
closes a homeowner. `docs/storm-to-contract.html` is the full build plan.

- **There are no polygons, deliberately.** The question the business asks is a
  *cell lookup*, not point-in-polygon. Contouring would need shapely and numpy,
  add interpolation error between the data and what a customer is told, and buy
  nothing. `cell_rects()` serves rendering at the data's real resolution. The
  package therefore has **no third-party dependencies** — worth keeping on a
  two-worker box.
- **Three units live in this system.** MRMS MESH is **millimetres**, the SPC
  CSV is **hundredths of an inch**, everything a human sees is **inches**.
  Conversion happens once on the way in and `MM_PER_INCH` is the only spelling.
  Getting it backwards is a 25.4x error: every storm either always or never
  clears the 1" threshold.
- **Quantization floors, and rounds before flooring.** `int()` truncates toward
  zero, which puts every western-hemisphere cell one index off — Colorado is
  entirely west of the meridian. And `151.2 / 0.01` is `15119.999999999998`, so
  a point exactly on a cell edge floors into the wrong cell and then reports
  bounds that do not contain it. Both are pinned in `hail/tests/test_grid.py`.
- **Cells are anchored to the GLOBAL 0.01° lattice**, not to a clip box, so a
  cell id means the same ground forever. Anchor to a bounding box and every
  stored id becomes meaningless the first time someone widens the service area.
- **Re-ingesting a date replaces its cells, never merges them.** A day's MESH
  is finalized hours later and the real-time product is a rolling maximum, so
  the same date is pulled repeatedly. Merging would let a partial early read
  leave phantom cells behind — hail on a street that never got any, and a rep
  knocking it for nothing.
- **A day with no qualifying hail is still recorded.** Otherwise "no hail" and
  "the ingest never ran" are the same absence, and a backfill can never tell
  which days it still owes.
- **`join.affected()` counts records it could not place** and callers must
  report that number. Silently dropping un-geocoded customers makes a storm
  brief say "40 affected" when the truth is 400 — which reads as a small storm,
  and nobody investigates a small storm.
- **Tier order is a business rule, not a sort key.** A Roof Care Plan
  subscriber is a contractual obligation and outranks bigger hail on a cold
  address. It lives in `join.TIERS` so the map, the drafts and the canvassing
  zones cannot disagree.

## The ingest (`hail/ingest.py`) — AWS, not the NOAA site

```bash
python -m hail.backfill --days 1        # what a nightly job runs
python -m hail.backfill --season 2026   # Mar-Oct of one year
python -m hail.backfill --dry-run --days 30
```

**`mrms.ncep.noaa.gov` and the Iowa State archive are both refused by the
egress proxy** as an organization policy denial, and re-testing them is a waste
of a turn. **NOAA mirrors MRMS to AWS Open Data and S3 is reachable**, so the
ingest runs against `noaa-mrms-pds.s3.amazonaws.com`. Everything here was
verified against a real message rather than read from documentation:

- **Coverage starts 2020-10-14** (`ingest.EARLIEST`), not 2014. Still years
  past Colorado's one-year claim window. Deeper history would need MTArchive,
  which this environment cannot reach.
- **One file per day, not 48.** The bucket publishes every 30 minutes, but
  `MESH_Max_1440min` is a rolling 24-hour maximum, so the 23:30 file already
  holds the day's peak everywhere. `DAY_STAMP` is that choice; taking 00:00
  would report the *previous* day.
- **Section 7 is a PNG** (Data Representation Template 41), which is why this
  needs no eccodes and no new system package: **Pillow already ships** for the
  estimator's proposals. Any other template raises rather than being decoded as
  PNG, because a misread grid is a confident wrong answer about a real roof.
- **The scan flags are read, not assumed.** The product scans west→east then
  north→south; a file that ever shipped south-to-north would otherwise decode
  upside down and still look like a plausible map.
- **Flag values are dropped by the threshold, not by a list.** MRMS encodes
  "no coverage" and "no hail" as negatives (−3.0 and −1.0), and every threshold
  a roofer cares about is far above zero — so a new flag cannot slip past a
  list nobody updated.
- Clipping to `COLORADO` happens **before** any value is unpacked: CONUS is
  24.5M cells, Colorado is ~283k. About a second and a megabyte per day.

✅ **`MM_PER_INCH = 25.4` is confirmed against real data**, replacing the
warning that used to sit here. The observed CONUS daily maximum was 125.5,
which is 4.9 inches of hail — extreme but real; read as inches it would be 125
inches. The grid is also confirmed as the 0.01° lattice `CELL_DEG` assumes.

`hail/tests/test_ingest.py` builds its own GRIB2 messages rather than committing a
megabyte fixture. `test_the_live_product_still_decodes` is marked `live` and
**deselected by default** (`addopts = -m "not live"`) so the suite stays
offline; run it deliberately with `pytest -m live` to catch the day NOAA
changes the packing, the grid or the units.


## The alert (`hail/alert.py`) — nobody reads a database

A storm at 2am is worth knowing about at 6am, not whenever someone next opens
the map and thinks to check. `send_new()` mails a brief; the estimator's
`_send_storm_alert()` calls it.

- **It alerts on GROUND, not on our leads.** The CRM already joins a storm to
  the leads under it and books follow-ups from that — working the book we have.
  This is the other question, and for a roofer the bigger one: did hail fall
  somewhere we could be knocking. A street with no lead on it is *more*
  interesting, not less, because nobody has been there yet.
- **All of Colorado, in two sections, and the order is the point.**
  `SERVICE_AREA` (Northern Colorado, overridable with `HAIL_ALERT_BOUNDS`)
  leads the brief and sets the subject; `STATEWIDE` is the rest, under it. A
  storm two counties over is where the next crew goes, so it is reported — but
  mixing the two would bury six cells over Platteville that a crew can be on by
  breakfast beneath three hundred on the eastern plains nobody is driving to.
  When nothing hit the service area the subject says *not our area*, so the
  answer to "did it hit us" is on a phone screen without opening anything.
  `HAIL_ALERT_AREA_ONLY=1` drops the statewide half. A malformed
  `HAIL_ALERT_BOUNDS` falls back rather than silently switching alerts off.
- **`max_size` is what landed on US**, never the state's worst. Letting one
  leak into the other overstates our own storm by whatever fell three counties
  away — and that number is what decides whether anyone gets deployed.
- **`places()` buckets by TOWN, and the distance is shown rather than grouped
  on.** Keying on the distance put 1,348 cells across the plains into forty
  near-identical "open county" rows. A section caps at `SECTION_ROWS`, worst
  first, and says how many it dropped — the same honesty rule `cells_in`
  follows when it truncates a viewport.
- **`send_email` is INJECTED**, the same as `portal/backup.py` and
  `portal/crm_digest.py`. The sender lives in the estimator with the SMTP and
  SendGrid config, and importing it here would drag 24,000 lines into the storm
  archive to send one message.
- **Sent once per storm, and only on a send that reported success.** The nightly
  re-fetches settling days, so without `storm_alerts` the same storm would mail
  every night; and marking a failed send as sent is the one failure nobody
  would notice.
- **The window has both ends.** `storm_days(since=...)` has no ceiling of its
  own, so a floor alone is the whole archive from that date onward — which is
  how a first version would have mailed three years of history the moment
  someone backfilled a season. `ALERT_WINDOW_DAYS` is 3.
- **A cell belongs to its NEAREST town only**, or a swath between two of them
  counts into both and reads as twice the storm. Hail further than
  `NEAR_MILES` from any of them still gets a bearing — "open county (nearest
  Ault, 14 mi)" — because "open county" alone is true and useless.
- Guarded by `hail/tests/test_alert.py`.

**A thin archive must not answer as a confident negative.** `renderMeshHistory`
printed "No hail on record" in headline type whenever the server returned a MESH
answer with no storms — and the server returns that shape when the archive holds
*some* day in the window, which is not the same as holding the window. A
five-year lookup on a real Loveland address answered "No hail on record" off two
days of coverage, with the caveat in a grey footnote, while MESH held a 1.91"
storm over that town on 2024-07-21. The headline now states the coverage when
the coverage is thin. Pinned by `canvasser/tests/test_hail_archive.py`.
