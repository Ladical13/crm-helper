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
from datetime import datetime

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
    conn = dbtune.tune(sqlite3.connect(path))
    try:
        conn.executescript('''
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
        ''')
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
