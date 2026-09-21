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

**The archive is filled by `hail_daily` in `agents/scheduler.py`**, every day at
09:00 UTC — 3am Mountain, by which time the previous day's MESH file has
settled. It looks back `HAIL_WINDOW_DAYS` so a missed night leaves no permanent
hole, and re-fetches the most recent `HAIL_REFETCH_DAYS` even when held, because
`MESH_Max_1440min` is a rolling maximum that is still moving. **The scheduler is
off unless `NIMBUS_SCHEDULER=1`** — without it the archive stays empty and every
hail lookup silently falls through to the weaker SPC spotter reports.

*Nothing ran the ingest until 2026-09-21. `backfill.py` had said "what a nightly
cron runs" in its docstring since it was written and no cron ran it, so the
archive the canvasser, the CRM and storm-scout all read was empty in production
the whole time.*

**History is a one-off `python -m hail.backfill --season <year>`**, which is not
on any schedule and should not be: 202 days took 167 seconds, so it is a command
somebody runs once per year they want, not a job. The nightly job only ever
keeps up.

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
