"""Storm events and their cells — the archive every other tool reads.

One row per storm day per source, plus the sparse cells that cleared the
threshold. This is the thing that stops being a canvasser feature: the
canvasser renders it, Nimbus joins against it, storm-scout reports it, the CRM
segments on it and the estimator cites it in a proposal. It lives in its own
database so none of them owns it.

**Re-ingesting a date is safe and is the normal case.** A day's MESH is
finalized hours after the fact, and the real-time product is a rolling maximum,
so the same date will be pulled more than once — while it is happening, again
that night, and again from the archive months later when someone widens the
service area. `record()` replaces a date's cells wholesale rather than merging,
because merging would let a partial early read leave phantom cells behind after
the fuller one landed. The event id is derived from date + source, so the
replacement is exact.

Storage is the same SQLite-with-WAL the rest of the system uses, through
`portal.dbtune`, for the same reasons: one writer must not block every reader,
and a contending statement should wait rather than raise.
"""
import os
import sqlite3
import json
import zlib
from datetime import datetime, timedelta

from portal import dbtune

from hail import grid as hgrid

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

_initialized = set()


def db_path():
    """Resolved per call, not frozen at import — same reason as portal/funnel.py.

    HAIL_DATA_DIR is explicit and falls back to PORTAL_DATA_DIR rather than to
    DATA_DIR. DATA_DIR is the estimator's volume, and the canvasser note in
    CLAUDE.md exists because something already landed there by accident.
    """
    data_dir = (os.environ.get('HAIL_DATA_DIR')
                or os.environ.get('PORTAL_DATA_DIR')
                or _REPO_ROOT)
    return os.path.join(data_dir, 'hail.db')


def get_db():
    path = db_path()
    if path not in _initialized:
        _init(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return dbtune.tune(conn)


def _init(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    from portal.migration_backup import before_upgrade
    before_upgrade(path, 'hail-coverage-v1')
    conn = dbtune.tune(sqlite3.connect(path))
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS storm_coverage (
                event_id TEXT PRIMARY KEY,
                metadata BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS storm_events (
                event_id     TEXT PRIMARY KEY,
                event_date   TEXT NOT NULL,
                source       TEXT NOT NULL,
                threshold_in REAL NOT NULL,
                cell_deg     REAL NOT NULL,
                max_size_in  REAL DEFAULT 0,
                cell_count   INTEGER DEFAULT 0,
                south        REAL, west REAL, north REAL, east REAL,
                ingested_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS storm_date_idx ON storm_events(event_date);
            CREATE TABLE IF NOT EXISTS storm_cells (
                event_id TEXT NOT NULL,
                ri       INTEGER NOT NULL,
                ci       INTEGER NOT NULL,
                size_in  REAL NOT NULL,
                PRIMARY KEY (event_id, ri, ci)
            );
            CREATE INDEX IF NOT EXISTS storm_cell_event_idx ON storm_cells(event_id);
            -- One row, id 1. A backfill takes minutes and gunicorn kills a
            -- worker at 60 seconds, so the admin endpoint runs it on a thread
            -- and the browser polls. The poll can land on the OTHER worker, so
            -- the state cannot live in process memory — same trap, and the
            -- same answer, as the estimator's carrier scan.
            CREATE TABLE IF NOT EXISTS backfill_job (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                status      TEXT NOT NULL,
                label       TEXT NOT NULL DEFAULT '',
                started_by  TEXT NOT NULL DEFAULT '',
                started_at  TEXT NOT NULL DEFAULT '',
                done        INTEGER NOT NULL DEFAULT 0,
                total       INTEGER NOT NULL DEFAULT 0,
                storm_days  INTEGER NOT NULL DEFAULT 0,
                failures    INTEGER NOT NULL DEFAULT 0,
                note        TEXT NOT NULL DEFAULT ''
            );
        ''')
        if 'heartbeat_at' not in {r[1] for r in conn.execute('PRAGMA table_info(backfill_job)')}:
            conn.execute("ALTER TABLE backfill_job ADD COLUMN heartbeat_at TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()
    _initialized.add(path)


def reset_cache():
    """Forget which paths are initialized — for tests that swap the data dir."""
    _initialized.clear()


def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def event_id(event_date, source='mrms_mesh'):
    """Derived, never random: it is what makes a re-ingest replace rather than
    duplicate. Two pulls of 2026-06-12 are the same event."""
    return f'{source}:{event_date}'


def record(event_date, swath, source='mrms_mesh'):
    """Store a day's swath, replacing anything already held for that date.

    Returns the event row. A swath with no cells is still recorded — "we looked
    and there was no qualifying hail" is a fact worth keeping, and without it a
    gap in the archive is indistinguishable from a day the ingest never ran.
    """
    eid = event_id(event_date, source)
    box = swath.bbox() or (None, None, None, None)
    with get_db() as db:
        db.execute('DELETE FROM storm_cells WHERE event_id=?', (eid,))
        db.execute('DELETE FROM storm_coverage WHERE event_id=?', (eid,))
        coverage = getattr(swath, 'coverage', None)
        if coverage is not None:
            db.execute('INSERT INTO storm_coverage VALUES (?, ?)',
                       (eid, zlib.compress(json.dumps(coverage).encode())))
        db.execute('''INSERT INTO storm_events
                        (event_id, event_date, source, threshold_in, cell_deg,
                         max_size_in, cell_count, south, west, north, east, ingested_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(event_id) DO UPDATE SET
                        threshold_in=excluded.threshold_in,
                        cell_deg=excluded.cell_deg,
                        max_size_in=excluded.max_size_in,
                        cell_count=excluded.cell_count,
                        south=excluded.south, west=excluded.west,
                        north=excluded.north, east=excluded.east,
                        ingested_at=excluded.ingested_at''',
                   (eid, event_date, source, swath.threshold_in, swath.cell_deg,
                    swath.max_size, len(swath), box[0], box[1], box[2], box[3],
                    _now()))
        if swath.cells:
            db.executemany(
                'INSERT INTO storm_cells (event_id, ri, ci, size_in) VALUES (?,?,?,?)',
                [(eid, ri, ci, size) for (ri, ci), size in swath.cells.items()])
    return get_event(eid)


def get_event(eid):
    with get_db() as db:
        row = db.execute('SELECT * FROM storm_events WHERE event_id=?', (eid,)).fetchone()
    return dict(row) if row else None


def load_swath(eid):
    """Rehydrate a stored event's Swath, or None if the event is unknown."""
    event = get_event(eid)
    if not event:
        return None
    with get_db() as db:
        rows = db.execute('SELECT ri, ci, size_in FROM storm_cells WHERE event_id=?',
                          (eid,)).fetchall()
    return hgrid.Swath({(r['ri'], r['ci']): r['size_in'] for r in rows},
                       cell_deg=event['cell_deg'],
                       threshold_in=event['threshold_in'])


def events(since=None, until=None, min_size=None, limit=500):
    """Stored events, newest first. Dates are 'YYYY-MM-DD' and compare as text."""
    clauses, params = [], []
    if since:
        clauses.append('event_date >= ?'); params.append(since)
    if until:
        clauses.append('event_date <= ?'); params.append(until)
    if min_size is not None:
        clauses.append('max_size_in >= ?'); params.append(min_size)
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(
            f'SELECT * FROM storm_events {where} ORDER BY event_date DESC LIMIT ?',
            params + [limit]).fetchall()
    return [dict(r) for r in rows]


def ingested_dates(source='mrms_mesh'):
    """Dates already held — what a backfill skips.

    Includes days recorded with zero cells, deliberately: those were looked at.
    """
    with get_db() as db:
        return {r['event_date'] for r in db.execute(
            'SELECT event_date FROM storm_events WHERE source=?', (source,))}


def history_at(lat, lng, since=None, source='mrms_mesh'):
    """Every stored storm that put hail over one point, newest first.

    This is what replaces the canvasser's "Hail by Address" — which today scans
    five years of daily NOAA CSVs, takes up to a minute on a cold area, and can
    still only answer "somebody reported hail a few miles away". This is one
    indexed query and answers about the roof itself.
    """
    ri, ci = hgrid.cell_index(lat, lng)
    clauses, params = ['c.ri=?', 'c.ci=?', 'e.source=?'], [ri, ci, source]
    if since:
        clauses.append('e.event_date >= ?'); params.append(since)
    with get_db() as db:
        rows = db.execute(
            f'''SELECT e.event_date, e.event_id, c.size_in
                FROM storm_cells c JOIN storm_events e ON e.event_id = c.event_id
                WHERE {' AND '.join(clauses)}
                ORDER BY e.event_date DESC''', params).fetchall()
    return [dict(r) for r in rows]


def storm_days(since=None, until=None, min_size=None, limit=200, source='mrms_mesh'):
    """Days the archive HOLDS, newest first, for a picker.

    Days with no qualifying hail are dropped here even though `record()` keeps
    them: this list is "which storms can I look at", and a row that draws
    nothing on the map is a row that reads as a broken link. The fact that the
    day was looked at still lives in `ingested_dates()`, which is what
    distinguishes "no hail" from "never ingested".
    """
    clauses, params = ['source=?', 'cell_count > 0'], [source]
    if since:
        clauses.append('event_date >= ?'); params.append(since)
    if until:
        clauses.append('event_date <= ?'); params.append(until)
    if min_size is not None:
        clauses.append('max_size_in >= ?'); params.append(min_size)
    with get_db() as db:
        rows = db.execute(
            f'''SELECT event_id, event_date, max_size_in, cell_count
                FROM storm_events WHERE {' AND '.join(clauses)}
                ORDER BY event_date DESC LIMIT ?''', params + [limit]).fetchall()
    return [dict(r) for r in rows]


def cells_in(bounds=None, since=None, until=None, min_size=None,
             limit=20000, source='mrms_mesh'):
    """Merged cells over a date range, for drawing. (rows, truncated).

    Each row is (south, west, north, east, size_in) — the cell's real extent,
    not a radius. The canvasser's old overlay drew `max(500, size * 800)`-metre
    circles around each spotter report, a damage footprint that exists nowhere
    in the data; these claim nothing beyond the ground the radar estimated
    over.

    Three things are load-bearing:

    **A cell hit on more than one day takes the MAXIMUM, never a sum or a
    mean.** MESH is already a maximum over its own window, and a mean would
    quietly shave the peak off every multi-day range — which is exactly the
    number that decides whether a neighbourhood is worth knocking.

    **The bounds filter runs in SQL on the cell INDICES**, not in Python after
    loading. A day's swath is thousands of cells and a season is millions;
    `cell_index` turns the viewport into an ri/ci range so the database returns
    the screen and nothing else.

    **Truncation keeps the BIGGEST hail.** A cap that returned an arbitrary
    slice would hide the cells the rep most needs to see behind ones they do
    not, so the order is by size descending and the caller is told it happened.
    Same honesty rule as the canvasser's pin list.
    """
    clauses, params = ['e.source=?'], [source]
    if since:
        clauses.append('e.event_date >= ?'); params.append(since)
    if until:
        clauses.append('e.event_date <= ?'); params.append(until)
    if min_size is not None:
        clauses.append('c.size_in >= ?'); params.append(min_size)
    if bounds:
        south, west, north, east = bounds
        # Built from the grid's own indexer so the filter and the stored ids
        # can never disagree about which cell a coordinate falls in.
        r0, c0 = hgrid.cell_index(south, west)
        r1, c1 = hgrid.cell_index(north, east)
        clauses.append('c.ri BETWEEN ? AND ?'); params += [min(r0, r1), max(r0, r1)]
        clauses.append('c.ci BETWEEN ? AND ?'); params += [min(c0, c1), max(c0, c1)]
    with get_db() as db:
        rows = db.execute(
            f'''SELECT c.ri, c.ci, e.cell_deg, MAX(c.size_in) AS size_in
                FROM storm_cells c JOIN storm_events e ON e.event_id = c.event_id
                WHERE {' AND '.join(clauses)}
                GROUP BY c.ri, c.ci, e.cell_deg
                ORDER BY size_in DESC
                LIMIT ?''', params + [limit + 1]).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    # cell_deg comes from each row's own event rather than from a constant: a
    # cell id only means anything against the lattice it was indexed on, so a
    # future ingest at a different resolution still draws in the right place.
    return ([hgrid.cell_bounds(r['ri'], r['ci'], r['cell_deg']) + (r['size_in'],)
             for r in rows], truncated)


# ── The admin backfill's job row ────────────────────────────────────────────
#
# Filling history is a one-off that has to run ON the server, because the
# archive lives on the Railway volume and `railway run` executes against local
# disk. So it is a background thread plus a polled status row, and the row is
# in SQLite rather than memory because the two gunicorn workers do not share
# memory and the poll may land on either one.

def backfill_claim(label, username, total):
    """Take the backfill slot. False when one is already running.

    A conditional INSERT OR REPLACE guarded by the current status, so two
    workers racing produce one runner and one refusal — the same shape as the
    Nimbus scheduler's claim, and for the same reason.
    """
    with get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        cutoff = (datetime.utcnow() - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        row = db.execute('SELECT * FROM backfill_job WHERE id=1').fetchone()
        if row and row['status'] == 'running' and (row['heartbeat_at'] or row['started_at']) > cutoff:
            return False
        db.execute('DELETE FROM backfill_job WHERE id=1')
        cur = db.execute(
            "INSERT INTO backfill_job (id, status, label, started_by, "
            "started_at, done, total, storm_days, failures, note) "
            "SELECT 1, 'running', ?, ?, ?, 0, ?, 0, 0, '' "
            "WHERE NOT EXISTS (SELECT 1 FROM backfill_job "
            "                  WHERE id = 1 AND status = 'running')",
            (label, username, _now(), int(total)))
        if not cur.rowcount:
            db.commit()
            return False
        db.execute('UPDATE backfill_job SET heartbeat_at=? WHERE id=1', (_now(),))
        db.commit()
        return True


def backfill_progress(done, storm_days, failures):
    with get_db() as db:
        db.execute('UPDATE backfill_job SET done = ?, storm_days = ?, '
                   'failures = ?, heartbeat_at=? WHERE id = 1',
                   (int(done), int(storm_days), int(failures), _now()))
        db.commit()


def backfill_finish(status, note=''):
    with get_db() as db:
        db.execute('UPDATE backfill_job SET status = ?, note = ? WHERE id = 1',
                   (status, str(note)[:400]))
        db.commit()


def backfill_state():
    """The job row, plus how much of the archive exists. None if never run."""
    with get_db() as db:
        row = db.execute('SELECT * FROM backfill_job WHERE id = 1').fetchone()
    held = ingested_dates()
    state = dict(row) if row else None
    if state and state['status'] == 'running':
        cutoff = (datetime.utcnow() - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        if (state['heartbeat_at'] or state['started_at']) < cutoff:
            state['status'] = 'interrupted'
            state['note'] = 'Worker stopped responding. Run the backfill again to resume.'
    return {'job': state,
            'archive': {'days_held': len(held),
                        'first': min(held) if held else '',
                        'last': max(held) if held else '',
                        'unverified_days': len(held - verified_dates())}}


def verified_dates():
    """Dates decoded with coverage metadata, excluding legacy ambiguous zeros."""
    with get_db() as db:
        return {r[0] for r in db.execute('SELECT e.event_date FROM storm_events e '
                'JOIN storm_coverage c USING(event_id) WHERE e.source=?', ('mrms_mesh',))}


def coverage(since, until, lat=None, lng=None):
    """Availability is separate from hail. A legacy day never proves a negative."""
    with get_db() as db:
        rows = db.execute('SELECT e.event_date,e.threshold_in,c.metadata FROM storm_events e '
                          'LEFT JOIN storm_coverage c USING(event_id) '
                          'WHERE e.source=? AND e.event_date BETWEEN ? AND ? '
                          'ORDER BY e.event_date', ('mrms_mesh', since, until)).fetchall()
    point = hgrid.cell_index(lat, lng) if lat is not None else None
    valid, thresholds, verified = [], [], 0
    for row in rows:
        thresholds.append(row['threshold_in'])
        if not row['metadata']:
            continue
        meta = json.loads(zlib.decompress(row['metadata']))
        verified += 1
        if point is None or any(r == point[0] and a <= point[1] <= b
                                for r, a, b in meta['valid_runs']):
            valid.append(row['event_date'])
    from hail.ingest import COLORADO
    outside = lat is not None and not (COLORADO[0] <= lat <= COLORADO[2]
                                       and COLORADO[1] <= lng <= COLORADO[3])
    requested = max(0, (datetime.fromisoformat(until) - datetime.fromisoformat(since)).days + 1)
    return {'days_held': len(rows), 'days_verified': len(valid),
            'point_checked': point is not None,
            'days_requested': requested, 'days_missing': max(0, requested - len(valid)),
            'unverified_days': len(rows) - verified,
            'first': rows[0]['event_date'] if rows else '',
            'last': rows[-1]['event_date'] if rows else '',
            'threshold_in': max(thresholds, default=hgrid.DEFAULT_THRESHOLD_IN),
            'bounds': list(COLORADO), 'outside_area': outside,
            'status': 'outside_area' if outside else ('complete' if len(valid) == requested and requested else 'partial'),
            'window_note': 'Each date labels a rolling 24-hour radar window ending at 23:30 UTC; local storm dates may differ.'}
