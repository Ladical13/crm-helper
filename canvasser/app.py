import os
import json
import csv
import uuid
import secrets
import sqlite3
import io
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory, session
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import requests as http
except ImportError:
    http = None

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SESSION_SECRET', secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('RAILWAY_ENVIRONMENT')),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)

SIGNUP_CODE  = os.environ.get('CANVASSER_SIGNUP_CODE', '').strip()
BASE44_TOKEN = os.environ.get('BASE44_TOKEN', '')
BASE44_URL   = 'https://base44.app/api/apps/69320ef0c647fee442697971'
DATA_DIR     = os.environ.get('CANVASSER_DATA_DIR',
               os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__))))
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

# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pins_rep_idx ON pins(rep);
            CREATE INDEX IF NOT EXISTS pins_type_idx ON pins(pin_type);
        ''')

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
        with get_db() as db:
            user = db.execute('SELECT is_admin FROM users WHERE username=?',
                              (session['username'],)).fetchone()
        if not user or not user['is_admin']:
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper

# ── Static ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory('static', path)

# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    if not user or not check_password_hash(user['pw_hash'], password):
        return jsonify({'error': 'Invalid credentials'}), 401
    session.permanent = True
    session['username'] = username
    session['is_admin'] = bool(user['is_admin'])
    return jsonify({'username': username, 'is_admin': bool(user['is_admin'])})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/signup', methods=['POST'])
def signup():
    if not SIGNUP_CODE:
        return jsonify({'error': 'Signups are closed'}), 403
    data = request.get_json(force=True)
    code     = (data.get('signup_code') or '').strip()
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    if code != SIGNUP_CODE:
        return jsonify({'error': 'Invalid signup code'}), 403
    if not username or not password or len(password) < 6:
        return jsonify({'error': 'Username and password (min 6 chars) required'}), 400
    with get_db() as db:
        existing = db.execute('SELECT username FROM users WHERE username=?', (username,)).fetchone()
        if existing:
            return jsonify({'error': 'Username taken'}), 409
        # First user becomes admin
        count = db.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        is_admin = 1 if count == 0 else 0
        db.execute(
            'INSERT INTO users (username, pw_hash, is_admin, created_at) VALUES (?,?,?,?)',
            (username, generate_password_hash(password), is_admin, _now())
        )
    session.permanent = True
    session['username'] = username
    session['is_admin'] = bool(is_admin)
    return jsonify({'username': username, 'is_admin': bool(is_admin)}), 201

@app.route('/api/me')
def me():
    if 'username' not in session:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'username': session['username'],
                    'is_admin': session.get('is_admin', False)})

@app.route('/api/users')
@admin_required
def list_users():
    with get_db() as db:
        users = db.execute('SELECT username, is_admin, created_at FROM users ORDER BY username').fetchall()
    return jsonify([dict(u) for u in users])

@app.route('/api/users/<username>/reset', methods=['POST'])
@admin_required
def reset_user(username):
    data = request.get_json(force=True)
    new_pw = data.get('password') or ''
    if len(new_pw) < 6:
        return jsonify({'error': 'Password too short'}), 400
    with get_db() as db:
        db.execute('UPDATE users SET pw_hash=? WHERE username=?',
                   (generate_password_hash(new_pw), username))
    return jsonify({'ok': True})

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

# ── Hail data (NOAA SPC proxy) ────────────────────────────────────────────────

@app.route('/api/hail')
@login_required
def hail_data():
    date_param = request.args.get('date', 'today')  # 'today' or 'YYYYMMDD'
    if not http:
        return jsonify({'error': 'requests library not available'}), 500

    if date_param == 'today':
        url = 'https://www.spc.noaa.gov/climo/reports/today_filtered_hail.csv'
    else:
        # Convert YYYYMMDD → YYMMDD for NOAA SPC
        try:
            d = datetime.strptime(date_param, '%Y%m%d')
            ymd = d.strftime('%y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format, use YYYYMMDD'}), 400
        url = f'https://www.spc.noaa.gov/climo/reports/{ymd}_rpts_filtered_hail.csv'

    try:
        resp = http.get(url, timeout=15, headers={'User-Agent': 'P1Canvasser/1.0'})
        resp.raise_for_status()
    except Exception as e:
        return jsonify({'error': str(e), 'features': []}), 200

    # Parse CSV → GeoJSON FeatureCollection
    features = []
    try:
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            try:
                lat = float(row.get('Lat', row.get('lat', 0)))
                lon = float(row.get('Lon', row.get('lon', 0)))
                size = row.get('Size', row.get('size', '0'))
                try:
                    size_f = float(size)
                except Exception:
                    size_f = 0.0
                if lat == 0 and lon == 0:
                    continue
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                    'properties': {
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
    except Exception:
        pass

    return jsonify({'type': 'FeatureCollection', 'features': features,
                    'source_url': url, 'count': len(features)})

# ── CRM Sync ──────────────────────────────────────────────────────────────────

@app.route('/api/crm/sync/<pin_id>', methods=['POST'])
@login_required
def crm_sync(pin_id):
    if not BASE44_TOKEN:
        return jsonify({'error': 'BASE44_TOKEN not configured'}), 500
    if not http:
        return jsonify({'error': 'requests library not available'}), 500

    with get_db() as db:
        pin = db.execute('SELECT * FROM pins WHERE id=?', (pin_id,)).fetchone()
    if not pin:
        return jsonify({'error': 'Pin not found'}), 404
    pin = dict(pin)

    headers = {
        'Authorization': f'Bearer {BASE44_TOKEN}',
        'Content-Type': 'application/json',
    }

    contact_id = pin.get('crm_contact_id') or ''

    # Create or reuse contact
    if not contact_id:
        if not pin.get('contact_name'):
            return jsonify({'error': 'Contact name required to sync to CRM'}), 400

        name_parts = (pin['contact_name'] or '').strip().split(None, 1)
        first_name = name_parts[0] if name_parts else ''
        last_name  = name_parts[1] if len(name_parts) > 1 else ''

        contact_payload = {
            'name':           pin['contact_name'],
            'first_name':     first_name,
            'last_name':      last_name,
            'phone':          pin.get('contact_phone', ''),
            'email':          pin.get('contact_email', ''),
            'street_address': pin.get('address', ''),
            'source':         'door_knock',
            'assigned_to':    f"{pin['rep']}@projectoneroofing.com",
            'notes':          pin.get('notes', ''),
        }
        try:
            r = http.post(f'{BASE44_URL}/entities/Contact',
                          json=contact_payload, headers=headers, timeout=15)
            r.raise_for_status()
            contact_id = r.json().get('id', '')
        except Exception as e:
            return jsonify({'error': f'CRM contact creation failed: {e}'}), 502

        with get_db() as db:
            db.execute('UPDATE pins SET crm_contact_id=?, updated_at=? WHERE id=?',
                       (contact_id, _now(), pin_id))

    # Create Project
    project_payload = {
        'name':         f"Roofing - {pin.get('contact_name', 'Unknown')}",
        'contact_id':   contact_id,
        'source':       'door_knock',
        'assigned_to':  f"{pin['rep']}@projectoneroofing.com",
        'notes':        pin.get('notes', ''),
        'status':       'lead',
    }
    try:
        r = http.post(f'{BASE44_URL}/entities/Project',
                      json=project_payload, headers=headers, timeout=15)
        r.raise_for_status()
        project_id = r.json().get('id', '')
    except Exception as e:
        project_id = ''

    with get_db() as db:
        db.execute('UPDATE pins SET crm_project_id=?, updated_at=? WHERE id=?',
                   (project_id, _now(), pin_id))

    return jsonify({
        'ok': True,
        'crm_contact_id': contact_id,
        'crm_project_id': project_id,
    })

# ── Config endpoint (pin types) ───────────────────────────────────────────────

@app.route('/api/config')
def config():
    return jsonify({'pin_types': PIN_TYPES})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH})

if __name__ == '__main__':
    app.run(debug=True, port=5001)
