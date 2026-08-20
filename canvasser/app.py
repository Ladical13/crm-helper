"""P1 Canvasser — door-knocking map: pins, team GPS, hail overlays.

Identity is not owned here. The portal (portal/app.py) handles login and the
user table; this app only reads who the shared session says you are.
"""
import os
import sys
import json
import csv
import uuid
import sqlite3
import io
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory, session

# The portal package lives one directory up. Put the repo root on the path so
# this app works both mounted by portal/wsgi.py and run standalone.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portal import dbtune                # noqa: E402
from portal import session as psession   # noqa: E402
from portal import users as pusers       # noqa: E402

try:
    import requests as http
except ImportError:
    http = None

app = Flask(__name__, static_folder='static')
# Secret key, ProxyFix, and cookie settings identical to the other three apps.
# ProxyFix matters here because Railway terminates TLS at the edge: without it
# Flask thinks every request is http:// and marks the Secure cookie unsendable.
psession.configure(app)

HERE         = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR   = os.path.join(HERE, 'static')
BASE44_TOKEN = os.environ.get('BASE44_TOKEN', '')
BASE44_URL   = 'https://base44.app/api/apps/69320ef0c647fee442697971'
# CANVASSER_DATA_DIR must be set explicitly under the portal: the DATA_DIR
# fallback is the estimator's volume, so leaving it unset drops canvasser.db
# into the estimator's directory.
DATA_DIR     = os.environ.get('CANVASSER_DATA_DIR',
               os.environ.get('DATA_DIR', HERE))
DB_PATH      = os.path.join(DATA_DIR, 'canvasser.db')

PIN_TYPES = {
    'not_home':      {'label': 'Not Home',       'color': '#6B7280'},
    'come_back':     {'label': 'Come Back',       'color': '#F59E0B'},
    'no_interest':   {'label': 'No Interest',     'color': '#EF4444'},
    'interested':    {'label': 'Interested',      'color': '#3B82F6'},
    'appointment':   {'label': 'Appt Set',        'color': '#8B5CF6'},
    'inspected':     {'label': 'Inspected',       'color': '#F97316'},
    'closed':        {'label': 'Deal Closed',     'color': '#10B981'},
    'no_soliciting': {'label': 'No Soliciting',   'color': '#1F2937'},
}

# Which Pipeline stage a knocked door becomes.
#
# Pins used to sync straight to The Den, which meant a door-knocked lead
# skipped the entire follow-up apparatus — no stage, no cadence, no task, no
# place in the outreach queue and no line on the leaderboard. The Den is for
# jobs; a knock is a lead, and a lead belongs in The Pipeline. These stages are
# the honest translation of what the rep saw at the door.
#
# The three types not listed never become leads: `not_home` has nobody to
# follow up with yet, and `no_interest` and `no_soliciting` are answers.
PIN_STAGE = {
    'come_back':   'contacted',
    'interested':  'contacted',
    'appointment': 'appt_set',
    'inspected':   'inspected',
    'closed':      'won',
}

# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return dbtune.tune(conn)

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username  TEXT PRIMARY KEY,
                pw_hash   TEXT NOT NULL,
                is_admin  INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pins (
                id            TEXT PRIMARY KEY,
                lat           REAL NOT NULL,
                lng           REAL NOT NULL,
                address       TEXT DEFAULT '',
                pin_type      TEXT NOT NULL,
                rep           TEXT NOT NULL,
                notes         TEXT DEFAULT '',
                contact_name  TEXT DEFAULT '',
                contact_phone TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                crm_contact_id TEXT DEFAULT '',
                crm_project_id TEXT DEFAULT '',
                crm_lead_id   TEXT DEFAULT '',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pins_rep_idx ON pins(rep);
            CREATE INDEX IF NOT EXISTS pins_type_idx ON pins(pin_type);
            CREATE TABLE IF NOT EXISTS invites (
                code       TEXT PRIMARY KEY,
                username   TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_by    TEXT DEFAULT '',
                used_at    TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS hail_cache (
                date TEXT PRIMARY KEY,
                features_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rep_locations (
                username   TEXT PRIMARY KEY,
                lat        REAL NOT NULL,
                lng        REAL NOT NULL,
                accuracy   REAL DEFAULT 0,
                heading    REAL DEFAULT -1,
                updated_at TEXT NOT NULL
            );
        ''')
        # `CREATE TABLE IF NOT EXISTS` above only ever describes a fresh
        # database, so a column added later has to be applied explicitly or it
        # exists on a developer's laptop and nowhere else.
        cols = [r['name'] for r in db.execute('PRAGMA table_info(pins)')]
        if 'crm_lead_id' not in cols:
            db.execute("ALTER TABLE pins ADD COLUMN crm_lead_id TEXT DEFAULT ''")

init_db()

# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        # Re-read the store rather than trusting the cookie, so a demotion
        # takes effect on the next request instead of at next sign-in.
        if not pusers.is_manager_up(session['username']):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper

# ── Static ────────────────────────────────────────────────────────────────────

# Absolute STATIC_DIR, not the relative 'static': these only resolved because
# gunicorn ran with --chdir canvasser. Under the portal the working directory
# is the repo root and a relative path would 404 every asset.
@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


# Served from the app root, not /static/, exactly like the estimator's and the
# CRM's. A worker can only claim a scope at or below its own path, so a worker
# at /canvass/static/sw.js could never control /canvass/ — serving it here is
# what makes the scope come out right without a Service-Worker-Allowed header.
@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(STATIC_DIR, 'sw.js', mimetype='text/javascript')
    # The worker checks for its own updates; a cached copy would pin clients to
    # an old shell list and there would be no way to ship a fix.
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

# ── Identity ────────────────────────────────────────────────────────────────
# Login, logout, signup, invites and user administration moved to the portal
# (portal/app.py) when the three tools merged onto one origin. What is left is
# read-only: who the shared session says you are.

@app.route('/api/me')
def me():
    if 'username' not in session:
        return jsonify({'authenticated': False})
    user = pusers.get(session['username'])
    if not user:
        # Row deleted out from under a live cookie.
        session.clear()
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True,
                    'username': user['username'],
                    'full_name': user['full_name'],
                    'is_admin': user['role'] in pusers.ELEVATED})


@app.route('/api/users')
@login_required
def list_users():
    """The roster, for the rep filter chips on the map and the leaderboard."""
    return jsonify([{'username': u['username'], 'full_name': u['full_name'],
                     'is_admin': u['role'] in pusers.ELEVATED,
                     'created_at': u['created_at']}
                    for u in pusers.all_users()])


# ── Pin endpoints ─────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def _row_to_pin(row):
    d = dict(row)
    d['pin_meta'] = PIN_TYPES.get(d['pin_type'], {'label': d['pin_type'], 'color': '#6B7280'})
    return d

@app.route('/api/pins', methods=['GET'])
@login_required
def list_pins():
    rep    = request.args.get('rep')
    ptype  = request.args.get('type')
    limit  = min(int(request.args.get('limit', 2000)), 5000)
    clauses, params = [], []
    if rep:
        clauses.append('rep=?'); params.append(rep)
    if ptype:
        clauses.append('pin_type=?'); params.append(ptype)
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(
            f'SELECT * FROM pins {where} ORDER BY created_at DESC LIMIT ?',
            params + [limit]
        ).fetchall()
    return jsonify([_row_to_pin(r) for r in rows])

@app.route('/api/pins', methods=['POST'])
@login_required
def create_pin():
    data = request.get_json(force=True)
    lat  = data.get('lat')
    lng  = data.get('lng')
    pin_type = data.get('pin_type', 'not_home')
    if lat is None or lng is None:
        return jsonify({'error': 'lat/lng required'}), 400
    if pin_type not in PIN_TYPES:
        return jsonify({'error': 'Invalid pin type'}), 400
    pin = {
        'id':            str(uuid.uuid4()),
        'lat':           float(lat),
        'lng':           float(lng),
        'address':       data.get('address', ''),
        'pin_type':      pin_type,
        'rep':           session['username'],
        'notes':         data.get('notes', ''),
        'contact_name':  data.get('contact_name', ''),
        'contact_phone': data.get('contact_phone', ''),
        'contact_email': data.get('contact_email', ''),
        'crm_contact_id': '',
        'crm_project_id': '',
        'crm_lead_id':   '',
        'created_at':    _now(),
        'updated_at':    _now(),
    }
    with get_db() as db:
        db.execute('''INSERT INTO pins
            (id,lat,lng,address,pin_type,rep,notes,contact_name,contact_phone,
             contact_email,crm_contact_id,crm_project_id,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (pin['id'], pin['lat'], pin['lng'], pin['address'], pin['pin_type'],
             pin['rep'], pin['notes'], pin['contact_name'], pin['contact_phone'],
             pin['contact_email'], '', '', pin['created_at'], pin['updated_at'])
        )
    pin['pin_meta'] = PIN_TYPES[pin_type]
    return jsonify(pin), 201

@app.route('/api/pins/<pin_id>', methods=['GET'])
@login_required
def get_pin(pin_id):
    with get_db() as db:
        row = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_row_to_pin(row))

@app.route('/api/pins/<pin_id>', methods=['PUT'])
@login_required
def update_pin(pin_id):
    with get_db() as db:
        row = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    # Only the rep who created it or an admin can edit
    if row['rep'] != session['username'] and not session.get('is_admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json(force=True)
    allowed = ['pin_type', 'address', 'notes', 'contact_name', 'contact_phone', 'contact_email']
    sets, params = [], []
    for field in allowed:
        if field in data:
            if field == 'pin_type' and data[field] not in PIN_TYPES:
                return jsonify({'error': 'Invalid pin type'}), 400
            sets.append(f'{field}=?')
            params.append(data[field])
    if not sets:
        return jsonify({'error': 'Nothing to update'}), 400
    sets.append('updated_at=?')
    params.append(_now())
    params.append(pin_id)
    with get_db() as db:
        db.execute(f'UPDATE pins SET {", ".join(sets)} WHERE id=?', params)
        row = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
    return jsonify(_row_to_pin(row))

@app.route('/api/pins/<pin_id>', methods=['DELETE'])
@login_required
def delete_pin(pin_id):
    with get_db() as db:
        row = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['rep'] != session['username'] and not session.get('is_admin'):
        return jsonify({'error': 'Forbidden'}), 403
    with get_db() as db:
        db.execute('DELETE FROM pins WHERE id=?', (pin_id,))
    return jsonify({'ok': True})

# ── Leaderboard ───────────────────────────────────────────────────────────────

@app.route('/api/leaderboard')
@login_required
def leaderboard():
    with get_db() as db:
        rows = db.execute('''
            SELECT rep,
                   COUNT(*) as total_doors,
                   SUM(CASE WHEN pin_type='appointment'   THEN 1 ELSE 0 END) as appointments,
                   SUM(CASE WHEN pin_type='inspected'     THEN 1 ELSE 0 END) as inspections,
                   SUM(CASE WHEN pin_type='closed'        THEN 1 ELSE 0 END) as closed,
                   SUM(CASE WHEN pin_type='interested'    THEN 1 ELSE 0 END) as interested,
                   MAX(created_at) as last_activity
            FROM pins
            GROUP BY rep
            ORDER BY total_doors DESC
        ''').fetchall()
    return jsonify([dict(r) for r in rows])

# ── Live team locations ───────────────────────────────────────────────────────

@app.route('/api/location', methods=['POST'])
@login_required
def update_location():
    data = request.get_json(force=True)
    lat, lng = data.get('lat'), data.get('lng')
    if lat is None or lng is None:
        return jsonify({'error': 'lat/lng required'}), 400
    with get_db() as db:
        db.execute('''INSERT INTO rep_locations (username, lat, lng, accuracy, heading, updated_at)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(username) DO UPDATE SET
                        lat=excluded.lat, lng=excluded.lng, accuracy=excluded.accuracy,
                        heading=excluded.heading, updated_at=excluded.updated_at''',
                   (session['username'], float(lat), float(lng),
                    float(data.get('accuracy') or 0), float(data.get('heading') or -1), _now()))
    return jsonify({'ok': True})

@app.route('/api/team-locations')
@login_required
def team_locations():
    # Only locations updated in the last 15 minutes count as "live"
    cutoff = (datetime.utcnow() - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with get_db() as db:
        rows = db.execute(
            'SELECT * FROM rep_locations WHERE updated_at > ? ORDER BY username', (cutoff,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

# ── Hail data (NOAA SPC proxy) ────────────────────────────────────────────────

def _fetch_hail_csv(date_param):
    """Fetch one day of NOAA SPC filtered hail reports, return list of feature dicts."""
    if date_param == 'today':
        url = 'https://www.spc.noaa.gov/climo/reports/today_filtered_hail.csv'
    else:
        d = datetime.strptime(date_param, '%Y%m%d')  # raises ValueError on bad input
        url = f"https://www.spc.noaa.gov/climo/reports/{d.strftime('%y%m%d')}_rpts_filtered_hail.csv"
    resp = http.get(url, timeout=15, headers={'User-Agent': 'P1Canvasser/1.0'})
    resp.raise_for_status()
    features = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        try:
            lat = float(row.get('Lat', row.get('lat', 0)))
            lon = float(row.get('Lon', row.get('lon', 0)))
            if lat == 0 and lon == 0:
                continue
            try:
                size_f = float(row.get('Size', row.get('size', '0'))) / 100.0
            except Exception:
                size_f = 0.0
            # SPC reports size in hundredths of an inch (e.g. 175 = 1.75")
            if size_f > 10:  # already divided but still huge → bad row
                continue
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {
                    'date':     date_param,
                    'time':     row.get('Time', ''),
                    'size':     size_f,
                    'location': row.get('Location', ''),
                    'county':   row.get('County', ''),
                    'state':    row.get('State', ''),
                    'comments': row.get('Comments', ''),
                }
            })
        except Exception:
            continue
    return features

def _fetch_hail_cached(date_str):
    """Cached daily hail fetch. Past days are immutable so cache forever;
    'today'/current-day results are never cached."""
    today_str = datetime.utcnow().strftime('%Y%m%d')
    cacheable = date_str != 'today' and date_str < today_str
    if cacheable:
        with get_db() as db:
            row = db.execute('SELECT features_json FROM hail_cache WHERE date=?',
                             (date_str,)).fetchone()
        if row:
            return json.loads(row['features_json'])
    features = _fetch_hail_csv(date_str)
    if cacheable:
        with get_db() as db:
            db.execute('INSERT OR REPLACE INTO hail_cache (date, features_json, fetched_at) VALUES (?,?,?)',
                       (date_str, json.dumps(features), _now()))
    return features

def _fetch_hail_days(date_strs):
    """Fetch many days in parallel, returning {date_str: [features]}. Missing days → []."""
    from concurrent.futures import ThreadPoolExecutor
    def one(ds):
        try:
            return ds, _fetch_hail_cached(ds)
        except Exception:
            return ds, []
    # 16 workers keeps a 5-year first-cold scan (~1200 severe-season days)
    # inside gunicorn's 60s worker timeout; every day is then cached forever.
    with ThreadPoolExecutor(max_workers=16) as ex:
        return dict(ex.map(one, date_strs))

@app.route('/api/hail')
@login_required
def hail_data():
    date_param = request.args.get('date', 'today')  # 'today' or 'YYYYMMDD'
    if not http:
        return jsonify({'error': 'requests library not available'}), 500
    try:
        features = _fetch_hail_csv(date_param)
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYYMMDD'}), 400
    except Exception as e:
        return jsonify({'error': str(e), 'features': []}), 200
    return jsonify({'type': 'FeatureCollection', 'features': features, 'count': len(features)})

@app.route('/api/hail/range')
@login_required
def hail_range():
    """Hail reports across a date range (max 31 days), merged into one collection."""
    if not http:
        return jsonify({'error': 'requests library not available'}), 500
    try:
        start = datetime.strptime(request.args.get('start', ''), '%Y%m%d')
        end   = datetime.strptime(request.args.get('end', ''), '%Y%m%d')
    except ValueError:
        return jsonify({'error': 'start and end required as YYYYMMDD'}), 400
    if end < start:
        start, end = end, start
    if (end - start).days > 31:
        return jsonify({'error': 'Range too large (max 31 days)'}), 400
    date_strs = [(start + timedelta(days=i)).strftime('%Y%m%d')
                 for i in range((end - start).days + 1)]
    results = _fetch_hail_days(date_strs)
    features = [f for feats in results.values() for f in feats]
    days_with_hail = sum(1 for feats in results.values() if feats)
    return jsonify({'type': 'FeatureCollection', 'features': features,
                    'count': len(features), 'days_with_hail': days_with_hail})

def _haversine_miles(lat1, lon1, lat2, lon2):
    import math
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

@app.route('/api/geocode')
@login_required
def geocode():
    """Free geocoding via OpenStreetMap Nominatim."""
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'q required'}), 400
    if not http:
        return jsonify({'error': 'requests library not available'}), 500
    try:
        r = http.get('https://nominatim.openstreetmap.org/search',
                     params={'q': q, 'format': 'json', 'limit': 3, 'countrycodes': 'us'},
                     headers={'User-Agent': 'P1Canvasser/1.0 (projectoneroofingcolorado.com)'},
                     timeout=10)
        r.raise_for_status()
        results = [{'display_name': x['display_name'],
                    'lat': float(x['lat']), 'lng': float(x['lon'])} for x in r.json()]
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/geocode/reverse')
@login_required
def reverse_geocode():
    """lat/lng → street address via Nominatim."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({'error': 'lat/lng required'}), 400
    if not http:
        return jsonify({'error': 'requests library not available'}), 500
    try:
        r = http.get('https://nominatim.openstreetmap.org/reverse',
                     params={'lat': lat, 'lon': lng, 'format': 'json', 'zoom': 18},
                     headers={'User-Agent': 'P1Canvasser/1.0 (projectoneroofingcolorado.com)'},
                     timeout=10)
        r.raise_for_status()
        data = r.json()
        addr = data.get('address', {})
        street = ' '.join(x for x in [addr.get('house_number'), addr.get('road')] if x)
        return jsonify({
            'display_name': data.get('display_name', ''),
            'street': street,
            'city': addr.get('city') or addr.get('town') or addr.get('village') or '',
            'state': addr.get('state', ''),
            'zip': addr.get('postcode', ''),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/hail/address')
@login_required
def hail_at_address():
    """Hail history near an address (or lat/lng) over a lookback window.

    Params: q=<address> OR lat=&lng=, days=<lookback, default 1825, max 1825>,
            radius=<miles, default 10>
    Scans NOAA SPC daily CSVs; to keep it fast we only fetch days, so we cap
    the scan by sampling: full scan for <=90 days, else the monthly summaries.
    """
    if not http:
        return jsonify({'error': 'requests library not available'}), 500
    q = (request.args.get('q') or '').strip()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    resolved_name = ''
    if lat is None or lng is None:
        if not q:
            return jsonify({'error': 'q (address) or lat/lng required'}), 400
        try:
            r = http.get('https://nominatim.openstreetmap.org/search',
                         params={'q': q, 'format': 'json', 'limit': 1, 'countrycodes': 'us'},
                         headers={'User-Agent': 'P1Canvasser/1.0 (projectoneroofingcolorado.com)'},
                         timeout=10)
            r.raise_for_status()
            hits = r.json()
        except Exception as e:
            return jsonify({'error': f'Geocoding failed: {e}'}), 502
        if not hits:
            return jsonify({'error': 'Address not found'}), 404
        lat, lng = float(hits[0]['lat']), float(hits[0]['lon'])
        resolved_name = hits[0]['display_name']

    radius = min(float(request.args.get('radius', 10)), 50)
    days   = min(int(request.args.get('days', 1825)), 1825)

    # Severe-weather season heuristic: scan Mar–Oct days plus the last 45 days
    # fully; deep-winter hail in CO/TX is rare enough to skip. NOAA has no free
    # point-history API, so we scan daily CSVs (cached + parallel).
    now = datetime.utcnow()
    date_strs = []
    for i in range(1, days + 1):
        d = now - timedelta(days=i)
        if d.month in (3, 4, 5, 6, 7, 8, 9, 10) or i <= 45:
            date_strs.append(d.strftime('%Y%m%d'))
    results = _fetch_hail_days(date_strs)
    reports, scanned = [], len(date_strs)
    for ds, feats in results.items():
        for f in feats:
            flat, flon = f['geometry']['coordinates'][1], f['geometry']['coordinates'][0]
            dist = _haversine_miles(lat, lng, flat, flon)
            if dist <= radius:
                p = f['properties']
                reports.append({
                    'date': f'{ds[:4]}-{ds[4:6]}-{ds[6:]}', 'time': p['time'],
                    'size': p['size'], 'distance_miles': round(dist, 1),
                    'location': p['location'], 'county': p['county'],
                    'state': p['state'], 'lat': flat, 'lng': flon,
                })

    reports.sort(key=lambda r: (r['date'], -r['size']), reverse=True)
    max_size = max((r['size'] for r in reports), default=0)
    return jsonify({
        'query': q, 'resolved': resolved_name, 'lat': lat, 'lng': lng,
        'radius_miles': radius, 'lookback_days': days, 'days_scanned': scanned,
        'report_count': len(reports), 'max_size': max_size,
        'reports': reports[:100],
    })

# ── Pipeline handoff ──────────────────────────────────────────────────────────
#
# A knocked door becomes a lead in The Pipeline, not a job in The Den.
#
# This used to POST a Contact and a Project straight to Base44. Three things
# were wrong with that. The lead never entered the sales pipeline, so nothing
# followed it up and it appeared on no leaderboard. The Project was created
# with `status: 'lead'`, which is not one of The Den's statuses. And neither
# payload carried a `location_id`, so every record it made fell outside the
# Colorado filter that the estimator's contact search and every executive-team
# skill apply — the jobs existed and were invisible.
#
# The lead itself is created by the browser against `/crm/api/leads`: all four
# apps sit on one origin behind one cookie, so the rep's own session authorizes
# it, and the CRM keeps sole ownership of what a lead is. This endpoint only
# records the resulting id back onto the pin.

@app.route('/api/pins/<pin_id>/lead', methods=['POST'])
@login_required
def link_pin_to_lead(pin_id):
    """Record the Pipeline lead a pin became."""
    lead_id = (request.get_json(force=True) or {}).get('lead_id', '').strip()
    if not lead_id:
        return jsonify({'error': 'lead_id required'}), 400
    with get_db() as db:
        pin = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
        if not pin:
            return jsonify({'error': 'Pin not found'}), 404
        if pin['rep'] != session['username'] and not pusers.is_manager_up(session['username']):
            return jsonify({'error': 'Forbidden'}), 403
        db.execute('UPDATE pins SET crm_lead_id=?, updated_at=? WHERE id=?',
                   (lead_id, _now(), pin_id))
    return jsonify({'ok': True, 'crm_lead_id': lead_id})

# ── Config endpoint (pin types) ───────────────────────────────────────────────

@app.route('/api/config')
def config():
    return jsonify({'pin_types': PIN_TYPES, 'pin_stage': PIN_STAGE})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH})

if __name__ == '__main__':
    app.run(debug=True, port=5001)
