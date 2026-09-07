"""One address → one coordinate, shared by every app.

A hail swath is a polygon. The question that makes it worth anything — "which
of OUR people are underneath it" — is a point-in-polygon test, and that needs a
latitude and longitude for every customer, lead and past job we have. Today we
have almost none: an address is free text in `leads` (address/city/state/zip),
free text again on a Base44 Contact, and a rendered string on an estimate. Three
stores, no coordinates, so the join cannot be written at all.

This module is that missing layer, and it lives in `portal.db` for the same
reason `funnel.py` does: the apps keep separate databases, so anything genuinely
shared needs a home belonging to none of them.

Three things are load-bearing.

**The geocoder is the US Census bulk endpoint, not Nominatim.** OSM's usage
policy is one request a second and explicitly forbids bulk work; the canvasser
already leans on it for every pin drop and address search, from one Railway IP,
which is a ban waiting to happen. The Census geocoder is free, needs no API
key, is authoritative for US street addresses, and takes **10,000 addresses in
one POST** — which is the difference between backfilling the whole customer
list in a couple of minutes and not backfilling it at all.

**The cache stores misses.** A `No_Match` is written as `nomatch`, not dropped.
An address the geocoder cannot resolve is a permanent fact about that address,
and re-sending it on every backfill is how a batch job quietly grows until it
times out. `stale_keys()` is the deliberate way back in.

**Nothing here geocodes during a web request.** `lookup()` reads the cache and
returns None on a miss. Geocoding is a batch job (`portal.geocode_backfill`)
because a request that blocks on a third-party HTTP call is a request that
hangs a worker, and there are only two.
"""
import csv
import io
import os
import re
import sqlite3
from datetime import datetime

from portal import dbtune

try:
    import requests as http
except ImportError:  # pragma: no cover - exercised by the import guard alone
    http = None

# The Census batch endpoint. `benchmark=Public_AR_Current` is the rolling
# current vintage; pinning a numbered benchmark would freeze us on a snapshot
# that ages out of new subdivisions, which in a growth market is exactly the
# addresses we care about.
CENSUS_URL = ('https://geocoding.geo.census.gov/geocoder/locations/addressbatch')
CENSUS_BENCHMARK = 'Public_AR_Current'

# The endpoint's documented ceiling. Chunking is not optional: send 10,001 and
# the whole batch is rejected, not truncated.
BATCH_LIMIT = 10000

# Generous, because a full batch is a real upload and a real wait. This is a
# background job; nothing is watching a spinner.
BATCH_TIMEOUT = 300

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

_initialized = set()


def db_path():
    """Resolved per call, not frozen at import — same reason as funnel.py."""
    data_dir = os.environ.get('PORTAL_DATA_DIR') or _REPO_ROOT
    return os.path.join(data_dir, 'portal.db')


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
            CREATE TABLE IF NOT EXISTS geocoded_addresses (
                addr_key   TEXT PRIMARY KEY,
                raw        TEXT NOT NULL,
                lat        REAL,
                lng        REAL,
                matched    TEXT DEFAULT '',
                source     TEXT DEFAULT '',
                status     TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS geo_status_idx ON geocoded_addresses(status);
        ''')
        conn.commit()
    finally:
        conn.close()
    _initialized.add(path)


def reset_cache():
    """Forget which paths are initialized — for tests that swap PORTAL_DATA_DIR."""
    _initialized.clear()


def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


# ── Normalization ───────────────────────────────────────────────────────────
#
# Conservative on purpose. This key exists to stop us paying for the same
# address twice, not to decide that two households are one — that judgement
# belongs to the customer-identity work, where getting it wrong merges two
# people's roofs. So: case, whitespace and punctuation only, plus the street
# suffixes and directionals that are genuinely interchangeable in US postal
# usage. "123 Main St" and "123 MAIN STREET" are the same key. "123 Main St"
# and "123 Main St Apt 2" deliberately are not.

_SUFFIXES = {
    'STREET': 'ST', 'AVENUE': 'AVE', 'ROAD': 'RD', 'DRIVE': 'DR',
    'LANE': 'LN', 'COURT': 'CT', 'BOULEVARD': 'BLVD', 'CIRCLE': 'CIR',
    'PLACE': 'PL', 'TERRACE': 'TER', 'PARKWAY': 'PKWY', 'TRAIL': 'TRL',
    'HIGHWAY': 'HWY', 'SQUARE': 'SQ', 'POINT': 'PT',
}
_DIRECTIONS = {
    'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
    'NORTHEAST': 'NE', 'NORTHWEST': 'NW',
    'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW',
}
_WORDS = dict(_SUFFIXES)
_WORDS.update(_DIRECTIONS)


def norm_address(*parts):
    """Canonical key for an address. Accepts free text or street/city/state/zip.

    Returns '' for anything with no usable content, and callers must treat that
    as "not geocodable" rather than as a key — an empty key would collide every
    blank address in the database onto one row.
    """
    text = ' '.join(str(p) for p in parts if p and str(p).strip())
    text = text.upper()
    # Keep '#' (unit designators) and '-' (hyphenated house numbers); drop the
    # rest. Commas and periods carry no information once the parts are joined.
    text = re.sub(r'[^A-Z0-9#\- ]+', ' ', text)
    tokens = [_WORDS.get(t, t) for t in text.split()]

    # Drop a TRAILING ZIP. Our data has ragged zip coverage — a canvasser pin's
    # reverse-geocoded address usually has none, a CRM lead usually does — so
    # keeping it means the same house keys twice, gets geocoded twice, and a
    # coordinate cached from one source is never found from the other. That is
    # a silent join failure: it reports zero customers under a swath and reads
    # exactly like a quiet storm.
    #
    # Trailing only, and never the first token: "12345 Main St" is a house
    # number, not a zip, and stripping it would merge every address on the
    # street. City + state already carry the discrimination a zip would add.
    while len(tokens) > 1 and re.fullmatch(r'\d{5}(-\d{4})?', tokens[-1]):
        tokens.pop()
    return ' '.join(tokens).strip()


# ── Cache ───────────────────────────────────────────────────────────────────

def lookup(*parts):
    """Cached coordinate for an address, or None. Never touches the network.

    A `nomatch` row returns None too — the caller wants a coordinate, and the
    distinction between "never asked" and "asked, no answer" belongs to the
    backfill job, which reads it off `status` directly.
    """
    key = norm_address(*parts)
    if not key:
        return None
    with get_db() as db:
        row = db.execute(
            'SELECT * FROM geocoded_addresses WHERE addr_key=?', (key,)
        ).fetchone()
    if not row or row['status'] != 'ok':
        return None
    return {'lat': row['lat'], 'lng': row['lng'],
            'matched': row['matched'], 'source': row['source']}


def all_points():
    """Every cached hit as {addr_key: (lat, lng)} — one query, not N.

    `lookup()` opens a connection per address, which is right for the one-off
    question "where is this lead" and catastrophic for the bulk one. The hail
    join asks about every customer we have: at 40,000 leads that was 40,000
    round trips and 8.5 seconds, against 11ms for the geometry it was feeding.
    The cache has one row per address, so holding it in memory for the length
    of one join is cheap and bounded.
    """
    with get_db() as db:
        return {r['addr_key']: (r['lat'], r['lng']) for r in db.execute(
            "SELECT addr_key, lat, lng FROM geocoded_addresses WHERE status='ok'")}


def put(raw, lat=None, lng=None, matched='', source='census', status='ok',
        key=None):
    """Write one address into the cache. Returns the key it was stored under."""
    key = key or norm_address(raw)
    if not key:
        return ''
    with get_db() as db:
        db.execute('''INSERT INTO geocoded_addresses
                        (addr_key, raw, lat, lng, matched, source, status, updated_at)
                      VALUES (?,?,?,?,?,?,?,?)
                      ON CONFLICT(addr_key) DO UPDATE SET
                        raw=excluded.raw, lat=excluded.lat, lng=excluded.lng,
                        matched=excluded.matched, source=excluded.source,
                        status=excluded.status, updated_at=excluded.updated_at''',
                   (key, str(raw), lat, lng, matched, source, status, _now()))
    return key


def known_keys():
    """Every key already in the cache, hit or miss — what a backfill skips."""
    with get_db() as db:
        return {r['addr_key'] for r in
                db.execute('SELECT addr_key FROM geocoded_addresses')}


def stale_keys(status='nomatch'):
    """Keys stored with a given status, so a retry can be asked for explicitly.

    Misses are cached forever by design; this is the door back in when a
    geocoder vintage improves or someone fixes a typo upstream.
    """
    with get_db() as db:
        return {r['addr_key'] for r in
                db.execute('SELECT addr_key FROM geocoded_addresses WHERE status=?',
                           (status,))}


def counts():
    """{status: n} — what a backfill reports and what a health check watches."""
    with get_db() as db:
        return {r['status']: r['n'] for r in db.execute(
            'SELECT status, COUNT(*) AS n FROM geocoded_addresses GROUP BY status')}


# ── Census bulk geocoder ────────────────────────────────────────────────────

def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def parse_census_csv(text):
    """Parse a Census batch response into {row_id: result}.

    The response is headerless CSV:
        id, input address, match, match type, matched address, "lon,lat", tiger, side
    Unmatched rows are shorter — they stop after the match indicator.

    **The coordinate pair is lon,lat, not lat,lng.** Every other surface in this
    codebase says lat first; getting this backwards puts Fort Collins in the
    Indian Ocean and produces a swath join that silently matches nobody, which
    is far worse than an error. Pinned by test_census_coordinates_are_lon_lat.
    """
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        row_id, raw, indicator = row[0].strip(), row[1].strip(), row[2].strip()
        # A "Tie" means the geocoder found several equally good candidates. It
        # is not a match and must never be resolved by picking the first — an
        # arbitrary pick puts a real customer on a real, wrong roof.
        if indicator != 'Match' or len(row) < 6:
            out[row_id] = {'raw': raw, 'status': 'nomatch',
                           'lat': None, 'lng': None, 'matched': ''}
            continue
        matched = row[4].strip()
        try:
            lon_str, lat_str = row[5].split(',')
            lat, lng = float(lat_str), float(lon_str)
        except (ValueError, IndexError):
            out[row_id] = {'raw': raw, 'status': 'nomatch',
                           'lat': None, 'lng': None, 'matched': matched}
            continue
        out[row_id] = {'raw': raw, 'status': 'ok',
                       'lat': lat, 'lng': lng, 'matched': matched}
    return out


def _post_batch(rows):
    """POST one chunk to the Census geocoder, return the response text.

    Split out so tests can drive `geocode()` without a network, and so the one
    place that talks to a third party is small enough to read.
    """
    if http is None:
        raise RuntimeError('requests is not available')
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row_id, street, city, state, zipcode in rows:
        writer.writerow([row_id, street, city, state, zipcode])
    resp = http.post(
        CENSUS_URL,
        data={'benchmark': CENSUS_BENCHMARK},
        files={'addressFile': ('addresses.csv', buf.getvalue(), 'text/csv')},
        timeout=BATCH_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def geocode(addresses, post=None):
    """Geocode `addresses` and write every result to the cache.

    `addresses` is an iterable of (street, city, state, zip) tuples. Returns
    {status: n} for what was written. Anything already cached is the caller's
    job to filter — see `known_keys()` — because deciding whether to retry a
    miss is a policy question, not a lookup.

    `post` is the transport, injectable for tests.
    """
    post = post or _post_batch
    prepared, seen = [], {}
    for addr in addresses:
        street, city, state, zipcode = (list(addr) + ['', '', '', ''])[:4]
        key = norm_address(street, city, state, zipcode)
        if not key or key in seen:
            continue
        seen[key] = (street, city, state, zipcode)
        prepared.append((key, street, city, state, zipcode))

    written = {}
    for chunk in _chunks(prepared, BATCH_LIMIT):
        # Row ids are the index within the chunk: the Census response comes
        # back in arbitrary order, so the id is the only way home. The key
        # itself cannot be the id — it contains commas and blows up the CSV.
        indexed = [(str(i), s, c, st, z) for i, (_k, s, c, st, z) in enumerate(chunk)]
        results = parse_census_csv(post(indexed))
        for i, (key, street, city, state, zipcode) in enumerate(chunk):
            res = results.get(str(i))
            raw = ', '.join(p for p in (street, city, state, zipcode) if p)
            if res is None:
                # The endpoint dropped the row entirely. Recording it as an
                # error rather than a miss keeps it eligible for a retry.
                put(raw, status='error', source='census', key=key)
                written['error'] = written.get('error', 0) + 1
                continue
            put(raw, lat=res['lat'], lng=res['lng'], matched=res['matched'],
                source='census', status=res['status'], key=key)
            written[res['status']] = written.get(res['status'], 0) + 1
    return written
