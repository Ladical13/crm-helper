"""Colorado property reports: immutable evidence snapshots shared by the apps."""
import datetime as dt
import json
import math
import uuid
import zlib
from zoneinfo import ZoneInfo

from hail import grid, ingest, storms
from portal import clock, users


def point(lat, lng):
    lat, lng = float(lat), float(lng)
    s, w, n, e = ingest.COLORADO
    if not (math.isfinite(lat) and math.isfinite(lng) and s <= lat <= n and w <= lng <= e):
        raise ValueError('Choose a property inside the Colorado radar coverage area.')
    return lat, lng


def candidates(http, query):
    from portal.interactive_geo import request
    query = str(query or '').strip()
    if len(query) < 5 or len(query) > 300:
        raise ValueError('Enter a street address and Colorado city or ZIP code.')
    rows = request(http, 'search', {'q': query, 'format': 'json', 'limit': 5,
        'countrycodes': 'us', 'addressdetails': 1, 'bounded': 1,
        'viewbox': '-109.06,41.01,-102.04,36.99'}, 'ProjectOneRoofing/1.0 (Colorado property hail search)')
    result = []
    for row in rows:
        a = row.get('address') or {}
        if a.get('state') != 'Colorado':
            continue
        try:
            lat, lng = point(row['lat'], row['lon'])
        except (ValueError, TypeError, KeyError):
            continue
        result.append({'label': row['display_name'][:500], 'lat': lat, 'lng': lng,
                       'address': {'street': ' '.join(filter(None, [a.get('house_number'), a.get('road')])),
                        'city': a.get('city') or a.get('town') or a.get('village') or '',
                        'state': 'CO', 'zip': a.get('postcode', '')}})
    return result


def snapshot(candidate, lat, lng, days=1825):
    lat, lng = point(lat, lng)
    days = max(1, min(int(days), 1825))
    until = clock.company_today() - dt.timedelta(days=1)
    since = until - dt.timedelta(days=days - 1)
    cov = storms.coverage(str(since), str(until), lat, lng)
    events = []
    for hit in storms.history_at(lat, lng, since=str(since), source='mrms_mesh'):
        date = dt.date.fromisoformat(hit['event_date'])
        if date > until:
            continue
        end = dt.datetime.combine(date, dt.time(23, 30), tzinfo=dt.timezone.utc)
        start = end - dt.timedelta(days=1)
        events.append({'date': str(date), 'size': round(hit['size_in'], 2),
            'event_id': hit['event_id'], 'note': grid.size_note(hit['size_in']),
            'window_start_utc': start.isoformat(), 'window_end_utc': end.isoformat(),
            'local_window': start.astimezone(ZoneInfo('America/Denver')).strftime('%b %d, %Y %I:%M %p %Z') +
                ' to ' + end.astimezone(ZoneInfo('America/Denver')).strftime('%b %d, %Y %I:%M %p %Z'),
            'source_url': ingest.day_url(date)})
    events.sort(key=lambda e: e['date'], reverse=True)
    verified = storms.verified_dates()
    missing = [str(since + dt.timedelta(days=i)) for i in range(days)
               if str(since + dt.timedelta(days=i)) not in verified]
    worst = max(events, key=lambda e: e['size'], default=None)
    # A fixed extent and exact grid cells, never an invented damage polygon.
    # Match the PDF map aspect ratio in approximate ground distances.
    half_lng = .045 * (180 / 65) / math.cos(math.radians(lat))
    bounds = [lat - .045, lng - half_lng, lat + .045, lng + half_lng]
    cells, truncated = storms.cells_in(bounds=bounds, since=worst['date'], until=worst['date'],
        source='mrms_mesh', limit=2000) if worst else ([], False)
    return {'id': str(uuid.uuid4()), 'schema': 1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'label': candidate['label'], 'address': candidate['address'], 'lat': lat, 'lng': lng,
        'location_confirmed': True, 'since': str(since), 'until': str(until),
        'source': 'NOAA MRMS MESH_Max_1440min', 'coverage': cov,
        'missing_archive_dates': missing, 'storms': events,
        'max_size': max((e['size'] for e in events), default=0),
        'map': {'bounds': bounds, 'date': worst['date'] if worst else '',
                'cells': [list(c) for c in cells], 'truncated': truncated},
        'disclaimer': 'Radar estimates describe the surrounding grid cell, not measured hail at the roof or verified roof damage. '
            'Archive dates label rolling 24-hour windows, not exact local storm times. Missing or unchecked data cannot establish no hail.'}


def _ready():
    with users.get_db() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='hail_property_reports'").fetchone()
    if not exists:
        from portal.migration_backup import before_upgrade
        before_upgrade(users.db_path(), 'property-hail-reports-v1')
        with users.get_db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hail_property_reports '
                       '(id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL, snapshot BLOB NOT NULL)')


def save(report, owner):
    _ready()
    with users.get_db() as db:
        db.execute('INSERT INTO hail_property_reports VALUES (?,?,?,?)',
            (report['id'], owner, report['created_at'], zlib.compress(json.dumps(report).encode())))
    return report


def load(report_id, owner, manager=False):
    _ready()
    with users.get_db() as db:
        row = db.execute('SELECT owner,snapshot FROM hail_property_reports WHERE id=?', (report_id,)).fetchone()
    if not row or (row['owner'] != owner and not manager):
        return None
    return json.loads(zlib.decompress(row['snapshot']))
