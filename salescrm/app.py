"""
Project One — Sales CRM ("The Pipeline")
========================================
A sales-driven CRM that sits at the top of the funnel. Reps manage their own
pipeline; managers coach off the numbers. Hands off downstream:

  • The Den (Base44): moving a lead to "won" auto-creates a Contact + Project.
  • Estimator: the shared Base44 contact_id is the join key, so a lead that
    reaches a full estimate links up and its status reads back here.

Mirrors the canvasser app: Flask + SQLite + PWA. Storage is a single SQLite
file (DATA_DIR/salescrm.db). No pricing math lives here. Identity is NOT here
either — the portal owns login and the user table (see portal/users.py); this
app only reads who the session says you are.
"""
import os
import sys
import json
import uuid
import sqlite3
from datetime import datetime, timedelta, date
from functools import wraps
from urllib.parse import quote

from flask import Flask, request, jsonify, send_from_directory, session

# The portal package lives one directory up. Put the repo root on the path so
# this app works both mounted by portal/wsgi.py and run standalone (its test
# suite imports app.py directly with the repo root nowhere in sight).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portal import session as psession   # noqa: E402
from portal import users as pusers       # noqa: E402

try:
    import requests as http
except ImportError:
    http = None

app = Flask(__name__, static_folder='static')
# Secret key, ProxyFix, and cookie settings identical to the other three apps.
# They share one cookie and each re-saves it whenever it touches session, so a
# mismatched flag here logs the rep out of all of them at random.
psession.configure(app)

HERE         = os.path.dirname(os.path.abspath(__file__))
BASE44_TOKEN = os.environ.get('BASE44_TOKEN', '')
BASE44_URL   = 'https://base44.app/api/apps/69320ef0c647fee442697971'
# Deep-link target for "Start estimate". Same origin now that the estimator is
# mounted at /estimate, so the rep keeps their session and their tab.
ESTIMATOR_URL = os.environ.get('ESTIMATOR_URL', '/estimate')
EMAIL_DOMAIN  = pusers.EMAIL_DOMAIN
# SALESCRM_DATA_DIR must be set explicitly under the portal: the DATA_DIR
# fallback is the estimator's volume, so leaving it unset drops salescrm.db
# into the estimator's directory.
DATA_DIR      = os.environ.get('SALESCRM_DATA_DIR', os.environ.get('DATA_DIR', HERE))
DB_PATH       = os.path.join(DATA_DIR, 'salescrm.db')

# A deal with no activity for this many days is "stalled" — a coaching cue.
STALL_DAYS = int(os.environ.get('SALESCRM_STALL_DAYS', '5'))

# ── Domain config (PIN_TYPES-style; drives kanban + funnel math) ──────────────

# Ordered pipeline. `won`/`lost` are terminal. `open` flags stages still in play.
STAGES = [
    {'key': 'new',                'label': 'New Lead',          'color': '#6B7280', 'open': True},
    {'key': 'contacted',          'label': 'Contacted',         'color': '#3B82F6', 'open': True},
    {'key': 'appt_set',           'label': 'Appt Set',          'color': '#8B5CF6', 'open': True},
    {'key': 'inspected',          'label': 'Inspected',         'color': '#F97316', 'open': True},
    {'key': 'estimate_presented', 'label': 'Estimate Presented','color': '#EAB308', 'open': True},
    {'key': 'follow_up',          'label': 'Follow-up',         'color': '#F59E0B', 'open': True},
    {'key': 'won',                'label': 'Won',               'color': '#10B981', 'open': False},
    {'key': 'lost',               'label': 'Lost',              'color': '#EF4444', 'open': False},
]
STAGE_KEYS  = [s['key'] for s in STAGES]
STAGE_META  = {s['key']: s for s in STAGES}
OPEN_STAGES = [s['key'] for s in STAGES if s['open']]

LEAD_TYPES = [
    {'key': 'homeowner',         'label': 'Homeowner',         'partner': False},
    {'key': 'realtor',           'label': 'Realtor',           'partner': True},
    {'key': 'hoa',               'label': 'HOA',               'partner': True},
    {'key': 'insurance_agent',   'label': 'Insurance Agent',   'partner': True},
    {'key': 'property_manager',  'label': 'Property Manager',  'partner': True},
    {'key': 'adjuster',          'label': 'Adjuster',          'partner': True},
    {'key': 'commercial',        'label': 'Commercial',        'partner': False},
    {'key': 'referral_partner',  'label': 'Referral Partner',  'partner': True},
]
LEAD_TYPE_KEYS = [t['key'] for t in LEAD_TYPES]
PARTNER_TYPES  = [t['key'] for t in LEAD_TYPES if t['partner']]

SOURCES     = ['referral', 'door_knock', 'phone_call', 'website', 'social_media', 'storm',
               'existing_customer', 'other']
TEMPERATURE = ['hot', 'warm', 'cold']

# Service lines. Every lead is a deal for ONE service; pitching a second service
# to the same customer creates a second lead (see the clone flow in app.js).
SERVICES = [
    {'key': 'roofing',              'label': 'Roofing',              'icon': '🏠'},
    {'key': 'window_cleaning',      'label': 'Window Cleaning',      'icon': '🪟'},
    {'key': 'exterior_maintenance', 'label': 'Exterior Maintenance', 'icon': '🏡'},
]
SERVICE_KEYS = [s['key'] for s in SERVICES]
SERVICE_META = {s['key']: s for s in SERVICES}

# Recurring billing. '' = one-time job. months-per-period drives MRR normalization
# (a won recurring deal is an ACTIVE PLAN — its monthly value counts toward MRR).
BILLING_KEYS   = ['', 'monthly', 'quarterly', 'annual']
BILLING_MONTHS = {'monthly': 1, 'quarterly': 3, 'annual': 12}
BILLING_SUFFIX = {'monthly': '/mo', 'quarterly': '/qtr', 'annual': '/yr'}

# Which local activity kinds count as "outreach" for scorecards.
OUTREACH_KINDS = ('call', 'text', 'email', 'door', 'meeting')

# ── Contact normalization ─────────────────────────────────────────────────────
# Defined up here rather than beside the other lead helpers because migrate_db()
# backfills with them, and that runs at import time before those are bound.
#
# These exist so the bulk prospect importer can dedupe. Nothing else compares
# contact details: the Base44 dedup in _find_existing_contact() does its own
# exact-string match against the Den, and deliberately stays that way.

def _norm_phone(s):
    """Digits only, US country code dropped. '' when it can't be a real number.

    '(970) 555-1212', '970-555-1212' and '+1 970 555 1212' all collapse to
    '9705551212' so the importer sees one contact, not three.
    """
    digits = ''.join(c for c in (s or '') if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if len(digits) == 10 else ''

def _norm_email(s):
    return (s or '').strip().lower()

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username   TEXT PRIMARY KEY,
                pw_hash    TEXT NOT NULL,
                is_admin   INTEGER DEFAULT 0,
                role       TEXT DEFAULT 'rep',
                full_name  TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invites (
                code       TEXT PRIMARY KEY,
                username   TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_by    TEXT DEFAULT '',
                used_at    TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS leads (
                id             TEXT PRIMARY KEY,
                lead_type      TEXT NOT NULL DEFAULT 'homeowner',
                service        TEXT NOT NULL DEFAULT 'roofing',
                plan           TEXT DEFAULT '',
                billing        TEXT DEFAULT '',
                first_name     TEXT DEFAULT '',
                last_name      TEXT DEFAULT '',
                company        TEXT DEFAULT '',
                phone          TEXT DEFAULT '',
                email          TEXT DEFAULT '',
                address        TEXT DEFAULT '',
                city           TEXT DEFAULT '',
                state          TEXT DEFAULT '',
                zip            TEXT DEFAULT '',
                source         TEXT DEFAULT '',
                temperature    TEXT DEFAULT 'warm',
                stage          TEXT NOT NULL DEFAULT 'new',
                rep            TEXT NOT NULL,
                est_value      REAL DEFAULT 0,
                referred_by    TEXT DEFAULT '',
                crm_contact_id TEXT DEFAULT '',
                crm_project_id TEXT DEFAULT '',
                estimate_id    TEXT DEFAULT '',
                lost_reason    TEXT DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                last_activity_at TEXT DEFAULT '',
                next_action_at TEXT DEFAULT '',
                won_at         TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS leads_rep_idx   ON leads(rep);
            CREATE INDEX IF NOT EXISTS leads_stage_idx ON leads(stage);
            CREATE INDEX IF NOT EXISTS leads_type_idx  ON leads(lead_type);
            CREATE INDEX IF NOT EXISTS leads_next_idx  ON leads(next_action_at);

            CREATE TABLE IF NOT EXISTS activities (
                id         TEXT PRIMARY KEY,
                lead_id    TEXT NOT NULL,
                rep        TEXT NOT NULL,
                kind       TEXT NOT NULL,
                outcome    TEXT DEFAULT '',
                body       TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS act_lead_idx ON activities(lead_id);
            CREATE INDEX IF NOT EXISTS act_rep_idx  ON activities(rep);

            CREATE TABLE IF NOT EXISTS tasks (
                id            TEXT PRIMARY KEY,
                lead_id       TEXT NOT NULL,
                rep           TEXT NOT NULL,
                kind          TEXT DEFAULT 'call',
                title         TEXT DEFAULT '',
                due_at        TEXT NOT NULL,
                done          INTEGER DEFAULT 0,
                done_at       TEXT DEFAULT '',
                enrollment_id TEXT DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS task_rep_idx  ON tasks(rep, done);
            CREATE INDEX IF NOT EXISTS task_lead_idx ON tasks(lead_id);

            CREATE TABLE IF NOT EXISTS cadence_enrollments (
                id          TEXT PRIMARY KEY,
                lead_id     TEXT NOT NULL,
                cadence_id  TEXT NOT NULL,
                step_idx    INTEGER DEFAULT 0,
                started_at  TEXT NOT NULL,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS coaching_notes (
                id          TEXT PRIMARY KEY,
                subject_rep TEXT NOT NULL,
                author      TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS coach_rep_idx ON coaching_notes(subject_rep);

            CREATE TABLE IF NOT EXISTS goals (
                id      TEXT PRIMARY KEY,
                rep     TEXT NOT NULL,
                period  TEXT NOT NULL,
                metric  TEXT NOT NULL,
                target  REAL DEFAULT 0
            );
        ''')

# Prospecting columns, added after the first release. Additive only — SQLite
# cannot drop a column, so nothing here is ever removed, only stopped being read.
_PROSPECT_COLS = [
    ('website',      "TEXT DEFAULT ''"),
    ('license_no',   "TEXT DEFAULT ''"),   # DORA licence number — a dedupe key
    ('icp_score',    'INTEGER DEFAULT 0'),  # queue ordering
    ('source_ref',   "TEXT DEFAULT ''"),   # dataset + row id, for provenance
    ('hook',         "TEXT DEFAULT ''"),   # the one personalization slot needing judgment
    ('import_batch', "TEXT DEFAULT ''"),   # '' means hand-entered, never deduped
    ('dnc',          'INTEGER DEFAULT 0'),
    ('phone_norm',   "TEXT DEFAULT ''"),
    ('email_norm',   "TEXT DEFAULT ''"),
]

def migrate_db():
    """Additive column migrations for DBs created before a field existed."""
    with get_db() as db:
        cols = [r['name'] for r in db.execute('PRAGMA table_info(leads)')]
        if 'service' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN service TEXT DEFAULT 'roofing'")
        if 'plan' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN plan TEXT DEFAULT ''")
        if 'billing' not in cols:
            db.execute("ALTER TABLE leads ADD COLUMN billing TEXT DEFAULT ''")
        for name, decl in _PROSPECT_COLS:
            if name not in cols:
                db.execute(f'ALTER TABLE leads ADD COLUMN {name} {decl}')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                id          TEXT PRIMARY KEY,
                lead_id     TEXT NOT NULL,
                filename    TEXT NOT NULL,
                orig_name   TEXT NOT NULL,
                size        INTEGER DEFAULT 0,
                uploaded_by TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS doc_lead_idx ON documents(lead_id);

            -- Opt-outs. Checked on import AND on every queue build, because a
            -- partner who asks to be left alone must not resurface tomorrow in
            -- a batch sourced from a different dataset.
            CREATE TABLE IF NOT EXISTS suppressions (
                id         TEXT PRIMARY KEY,
                kind       TEXT NOT NULL CHECK (kind IN ('email','phone','domain')),
                value      TEXT NOT NULL,
                reason     TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS supp_val_idx ON suppressions(kind, value);

            CREATE INDEX IF NOT EXISTS leads_phone_idx ON leads(phone_norm);
            CREATE INDEX IF NOT EXISTS leads_email_idx ON leads(email_norm);
            CREATE INDEX IF NOT EXISTS leads_batch_idx ON leads(import_batch);

            -- The queue's net-new top-up, which is the one query that grows with
            -- the prospect list. Without this SQLite picks leads_stage_idx and
            -- scans every 'new' lead in the table -- and in a prospecting DB
            -- almost every lead is 'new', so that index selects nothing. The
            -- trailing icp_score/created_at also satisfy the ORDER BY, dropping
            -- the temp B-tree sort over the whole candidate set.
            CREATE INDEX IF NOT EXISTS leads_queue_idx
                ON leads(rep, stage, icp_score DESC, created_at);
        ''')
        _backfill_norms(db)

def _backfill_norms(db):
    """Populate phone_norm/email_norm for rows written before they existed.

    Idempotent and cheap: only rows with contact details but no normalized form
    are touched, so this is a no-op on every run after the first.
    """
    rows = db.execute(
        "SELECT id, phone, email FROM leads "
        "WHERE (phone != '' AND phone_norm = '') OR (email != '' AND email_norm = '')"
    ).fetchall()
    for r in rows:
        db.execute('UPDATE leads SET phone_norm=?, email_norm=? WHERE id=?',
                   (_norm_phone(r['phone']), _norm_email(r['email']), r['id']))

init_db()
migrate_db()

# ── JSON config (cadences, playbook) ──────────────────────────────────────────

def _load_json(name, default):
    try:
        with open(os.path.join(HERE, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[config] could not load {name}: {e}')
        return default

CADENCES = _load_json('cadences.json', [])
PLAYBOOK = _load_json('playbook.json', {'objections': [], 'scripts': [], 'principles': []})
PLANS    = _load_json('plans.json', [])
TEMPLATES = _load_json('outreach_templates.json',
                       {'signature': '', 'banned_phrases': [], 'templates': {}})
CADENCE_BY_ID = {c['id']: c for c in CADENCES}
PLAN_BY_ID    = {p['id']: p for p in PLANS}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def _now_dt():
    return datetime.utcnow()

def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

# ── Auth ──────────────────────────────────────────────────────────────────────

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

def is_manager():
    """Admins/managers see every rep's pipeline and the coaching views."""
    return pusers.is_manager_up(session.get('username'))

def current_rep():
    return session.get('username')

# ── Static ────────────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(HERE, 'static')

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(STATIC_DIR, path)

@app.route('/manifest.json')
def manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory(STATIC_DIR, 'sw.js')

# ── Identity ──────────────────────────────────────────────────────────────────
# Login, logout, signup, invites, password resets and role changes all moved to
# the portal (portal/app.py) when the three tools merged onto one origin. What
# is left here is read-only: who the shared session says you are, and the
# roster the pipeline UI needs to render names and assignment dropdowns.

def _me_payload(user):
    return {
        'username':   user['username'],
        # is_admin means manager-or-above here — it is what gates the Numbers
        # and Coaching tabs, which managers are meant to see.
        'is_admin':   user['role'] in pusers.ELEVATED,
        'role':       user['role'],
        'full_name':  user['full_name'],
        'is_manager': user['role'] in pusers.ELEVATED,
    }

@app.route('/api/me')
def me():
    if 'username' not in session:
        return jsonify({'authenticated': False})
    user = pusers.get(session['username'])
    if not user:
        # Row deleted out from under a live cookie.
        session.clear()
        return jsonify({'authenticated': False})
    payload = _me_payload(user)
    payload['authenticated'] = True
    return jsonify(payload)

@app.route('/api/users')
@login_required
def list_users():
    """The roster, for assignment dropdowns and rendering names.

    Deliberately login-only rather than admin-only: reps need the names. Only
    the portal mutates the roster.
    """
    return jsonify([{'username': u['username'], 'is_admin': u['role'] in pusers.ELEVATED,
                     'role': u['role'], 'full_name': u['full_name'],
                     'created_at': u['created_at']}
                    for u in pusers.all_users()])

# ── Lead helpers ──────────────────────────────────────────────────────────────

def _lead_row(row):
    d = dict(row)
    meta = STAGE_META.get(d['stage'], {'label': d['stage'], 'color': '#6B7280'})
    d['stage_label'] = meta['label']
    d['stage_color'] = meta['color']
    smeta = SERVICE_META.get(d.get('service') or 'roofing', SERVICES[0])
    d['service_label'] = smeta['label']
    d['service_icon']  = smeta['icon']
    d['value_suffix']  = BILLING_SUFFIX.get(d.get('billing') or '', '')
    plan = PLAN_BY_ID.get(d.get('plan') or '')
    d['plan_name'] = plan['name'] if plan else ''
    d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company'] or '(no name)'
    # Days since last activity (stall detector). Empty = never touched.
    d['stalled'] = _is_stalled(d)
    d['overdue'] = bool(d['next_action_at']) and d['next_action_at'] <= _now()
    return d

def _is_stalled(d):
    if d['stage'] not in OPEN_STAGES:
        return False
    ref = d['last_activity_at'] or d['created_at']
    try:
        ref_dt = datetime.strptime(ref, '%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return False
    return (_now_dt() - ref_dt) > timedelta(days=STALL_DAYS)

def _lead_visible(db, lead_id):
    """Return the lead row if the current user may see it, else None."""
    row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not row:
        return None
    if not is_manager() and row['rep'] != current_rep():
        return None
    return row

def _log_activity(db, lead_id, kind, body='', outcome='', rep=None):
    db.execute('INSERT INTO activities (id, lead_id, rep, kind, outcome, body, created_at) '
               'VALUES (?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), lead_id, rep or current_rep(), kind, outcome, body, _now()))
    if kind in OUTREACH_KINDS:
        db.execute('UPDATE leads SET last_activity_at=?, updated_at=? WHERE id=?',
                   (_now(), _now(), lead_id))

def _refresh_next_action(db, lead_id):
    """leads.next_action_at = the soonest incomplete task's due date (or '')."""
    row = db.execute('SELECT MIN(due_at) m FROM tasks WHERE lead_id=? AND done=0', (lead_id,)).fetchone()
    db.execute('UPDATE leads SET next_action_at=?, updated_at=? WHERE id=?',
               (row['m'] or '', _now(), lead_id))

# ── Leads ─────────────────────────────────────────────────────────────────────

@app.route('/api/leads', methods=['GET'])
@login_required
def list_leads():
    rep     = request.args.get('rep')
    stage   = request.args.get('stage')
    ltype   = request.args.get('type')
    service = request.args.get('service')
    q       = (request.args.get('q') or '').strip().lower()
    limit   = min(int(request.args.get('limit', 1000)), 5000)

    clauses, params = [], []
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())   # reps see only their own
    elif rep:
        clauses.append('rep=?'); params.append(rep)
    if stage:
        clauses.append('stage=?'); params.append(stage)
    if ltype:
        clauses.append('lead_type=?'); params.append(ltype)
    if service:
        clauses.append('service=?'); params.append(service)
    if q:
        # Matched in SQL, not in Python afterwards: filtering the page the LIMIT
        # already returned would search only the most recently updated `limit`
        # rows, so a partner outside that window could not be found at all. With
        # tens of thousands of imported prospects that is most of the table.
        # `_` and `%` are escaped so a literal one is not read as a wildcard.
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clauses.append(
            "LOWER(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') || ' ' ||"
            "      COALESCE(phone,'')      || ' ' || COALESCE(email,'')     || ' ' ||"
            "      COALESCE(address,'')    || ' ' || COALESCE(company,''))"
            " LIKE ? ESCAPE '\\'")
        params.append(f'%{esc}%')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where} ORDER BY updated_at DESC LIMIT ?',
                          params + [limit]).fetchall()
    return jsonify([_lead_row(r) for r in rows])

@app.route('/api/leads', methods=['POST'])
@login_required
def create_lead():
    data = request.get_json(force=True)
    lead_type = data.get('lead_type', 'homeowner')
    if lead_type not in LEAD_TYPE_KEYS:
        return jsonify({'error': 'Invalid lead type'}), 400
    service = data.get('service', 'roofing')
    if service not in SERVICE_KEYS:
        return jsonify({'error': 'Invalid service'}), 400
    billing = data.get('billing', '')
    if billing not in BILLING_KEYS:
        return jsonify({'error': 'Invalid billing'}), 400
    stage = data.get('stage', 'new')
    if stage not in STAGE_KEYS:
        stage = 'new'
    # Managers may assign to any rep; reps own what they create.
    rep = data.get('rep') if is_manager() and data.get('rep') else current_rep()
    lid = str(uuid.uuid4())
    fields = {
        'id': lid, 'lead_type': lead_type, 'service': service,
        'plan': data.get('plan', ''), 'billing': billing,
        'first_name': data.get('first_name', ''), 'last_name': data.get('last_name', ''),
        'company': data.get('company', ''), 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
        'city': data.get('city', ''), 'state': data.get('state', ''), 'zip': data.get('zip', ''),
        'source': data.get('source', ''), 'temperature': data.get('temperature', 'warm'),
        'stage': stage, 'rep': rep, 'est_value': float(data.get('est_value') or 0),
        'referred_by': data.get('referred_by', ''),
        'phone_norm': _norm_phone(data.get('phone', '')),
        'email_norm': _norm_email(data.get('email', '')),
        'created_at': _now(), 'updated_at': _now(),
    }
    cols = ','.join(fields.keys())
    ph   = ','.join('?' * len(fields))
    with get_db() as db:
        db.execute(f'INSERT INTO leads ({cols}) VALUES ({ph})', list(fields.values()))
        _log_activity(db, lid, 'system', body=f'Lead created in stage "{STAGE_META[stage]["label"]}"')
        row = db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
    return jsonify(_lead_row(row)), 201

@app.route('/api/leads/<lead_id>', methods=['GET'])
@login_required
def get_lead(lead_id):
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        d = _lead_row(row)
        d['activities'] = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
        d['tasks'] = [dict(t) for t in db.execute(
            'SELECT * FROM tasks WHERE lead_id=? ORDER BY done, due_at', (lead_id,)).fetchall()]
        d['enrollments'] = [dict(e) for e in db.execute(
            'SELECT * FROM cadence_enrollments WHERE lead_id=? AND active=1', (lead_id,)).fetchall()]
        if d['referred_by']:
            ref = db.execute('SELECT first_name,last_name,company FROM leads WHERE id=?',
                             (d['referred_by'],)).fetchone()
            d['referred_by_name'] = (f"{ref['first_name']} {ref['last_name']}".strip()
                                     or ref['company']) if ref else ''
        # Partners carry their referred "projects" so the UI can show them inline.
        if d['lead_type'] in PARTNER_TYPES:
            q, params = 'SELECT * FROM leads WHERE referred_by=?', [lead_id]
            if not is_manager():
                q += ' AND rep=?'; params.append(current_rep())
            d['referrals'] = [_lead_row(r) for r in db.execute(
                q + ' ORDER BY created_at DESC', params).fetchall()]
    for e in d['enrollments']:
        cad = CADENCE_BY_ID.get(e['cadence_id'])
        e['cadence_name'] = cad['name'] if cad else e['cadence_id']
    return jsonify(d)

# Fields a rep may PUT directly. source_ref/import_batch/phone_norm/email_norm
# are server-owned — they record where a row came from and must not be editable.
LEAD_EDITABLE = ['lead_type', 'service', 'plan', 'billing', 'first_name', 'last_name',
                 'company', 'phone', 'email', 'address', 'city', 'state', 'zip', 'source',
                 'temperature', 'est_value', 'referred_by', 'lost_reason', 'estimate_id', 'rep',
                 'website', 'license_no', 'icp_score', 'dnc', 'hook']

@app.route('/api/leads/<lead_id>', methods=['PUT'])
@login_required
def update_lead(lead_id):
    data = request.get_json(force=True)
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        sets, params = [], []
        for f in LEAD_EDITABLE:
            if f in data:
                if f == 'rep' and not is_manager():
                    continue                      # only managers reassign owner
                if f == 'lead_type' and data[f] not in LEAD_TYPE_KEYS:
                    return jsonify({'error': 'Invalid lead type'}), 400
                if f == 'service' and data[f] not in SERVICE_KEYS:
                    return jsonify({'error': 'Invalid service'}), 400
                if f == 'billing' and data[f] not in BILLING_KEYS:
                    return jsonify({'error': 'Invalid billing'}), 400
                if f == 'est_value':
                    sets.append('est_value=?'); params.append(float(data[f] or 0)); continue
                if f in ('icp_score', 'dnc'):
                    sets.append(f'{f}=?'); params.append(int(data[f] or 0)); continue
                # Keep the normalized forms in step, or an edited phone number
                # would still dedupe against the value it replaced.
                if f == 'phone':
                    sets.append('phone_norm=?'); params.append(_norm_phone(data[f]))
                if f == 'email':
                    sets.append('email_norm=?'); params.append(_norm_email(data[f]))
                sets.append(f'{f}=?'); params.append(data[f])
        if not sets:
            return jsonify({'error': 'Nothing to update'}), 400
        sets.append('updated_at=?'); params.append(_now()); params.append(lead_id)
        db.execute(f'UPDATE leads SET {", ".join(sets)} WHERE id=?', params)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    return jsonify(_lead_row(row))

@app.route('/api/leads/<lead_id>/stage', methods=['PATCH'])
@login_required
def set_stage(lead_id):
    data = request.get_json(force=True)
    new_stage = data.get('stage')
    if new_stage not in STAGE_KEYS:
        return jsonify({'error': 'Invalid stage'}), 400
    den_result = None
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        old = row['stage']
        if old == new_stage:
            return jsonify(_lead_row(row))
        won_at = _now() if new_stage == 'won' else (row['won_at'] or '')
        lost_reason = data.get('lost_reason', row['lost_reason'])
        db.execute('UPDATE leads SET stage=?, won_at=?, lost_reason=?, updated_at=? WHERE id=?',
                   (new_stage, won_at, lost_reason, _now(), lead_id))
        _log_activity(db, lead_id, 'stage_change',
                      body=f'{STAGE_META[old]["label"]} → {STAGE_META[new_stage]["label"]}')
        # Terminal stages close out any pending follow-up tasks + cadences.
        if new_stage in ('won', 'lost'):
            db.execute("UPDATE tasks SET done=1, done_at=? WHERE lead_id=? AND done=0",
                       (_now(), lead_id))
            db.execute("UPDATE cadence_enrollments SET active=0 WHERE lead_id=?", (lead_id,))
        _refresh_next_action(db, lead_id)
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()

    # Auto-handoff to The Den on Won (unless already pushed).
    if new_stage == 'won' and not row['crm_contact_id']:
        den_result = _push_to_den(lead_id)

    with get_db() as db:
        row = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    resp = _lead_row(row)
    if den_result is not None:
        resp['den'] = den_result
    return jsonify(resp)

@app.route('/api/leads/<lead_id>', methods=['DELETE'])
@login_required
def delete_lead(lead_id):
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        db.execute('DELETE FROM leads WHERE id=?', (lead_id,))
        db.execute('DELETE FROM activities WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM tasks WHERE lead_id=?', (lead_id,))
        db.execute('DELETE FROM cadence_enrollments WHERE lead_id=?', (lead_id,))
    return jsonify({'ok': True})

# ── Activities ────────────────────────────────────────────────────────────────

@app.route('/api/leads/<lead_id>/activities', methods=['POST'])
@login_required
def add_activity(lead_id):
    data = request.get_json(force=True)
    kind = data.get('kind', 'note')
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        _log_activity(db, lead_id, kind, body=data.get('body', ''), outcome=data.get('outcome', ''))
        acts = [dict(a) for a in db.execute(
            'SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC LIMIT 200',
            (lead_id,)).fetchall()]
    return jsonify(acts), 201

# ── Tasks (the "next action" engine) ──────────────────────────────────────────

@app.route('/api/tasks', methods=['GET'])
@login_required
def list_tasks():
    rep   = request.args.get('rep') if is_manager() else current_rep()
    scope = request.args.get('scope', 'open')     # open | today | overdue | all
    clauses, params = ['t.done=0'], []
    if scope == 'all':
        clauses = []
    if rep:
        clauses.append('t.rep=?'); params.append(rep)
    if scope == 'today':
        clauses.append('t.due_at <= ?'); params.append(_iso(_now_dt().replace(hour=23, minute=59, second=59)))
    elif scope == 'overdue':
        clauses.append('t.due_at <= ?'); params.append(_now())
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with get_db() as db:
        rows = db.execute(f'''
            SELECT t.*, l.first_name, l.last_name, l.company, l.phone, l.stage, l.lead_type
            FROM tasks t JOIN leads l ON l.id = t.lead_id
            {where} ORDER BY t.done, t.due_at LIMIT 500''', params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['lead_name'] = (f"{r['first_name']} {r['last_name']}".strip() or r['company'] or '(no name)')
        d['overdue'] = (not d['done']) and d['due_at'] <= _now()
        out.append(d)
    return jsonify(out)

@app.route('/api/leads/<lead_id>/tasks', methods=['POST'])
@login_required
def add_task(lead_id):
    data = request.get_json(force=True)
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        due = data.get('due_at') or _iso(_now_dt() + timedelta(days=1))
        tid = str(uuid.uuid4())
        db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, created_at) '
                   'VALUES (?,?,?,?,?,?,?)',
                   (tid, lead_id, row['rep'], data.get('kind', 'call'),
                    data.get('title', ''), due, _now()))
        _refresh_next_action(db, lead_id)
    return jsonify({'ok': True, 'id': tid}), 201

@app.route('/api/tasks/<task_id>', methods=['PATCH'])
@login_required
def update_task(task_id):
    data = request.get_json(force=True)
    with get_db() as db:
        t = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not t:
            return jsonify({'error': 'Not found'}), 404
        if not is_manager() and t['rep'] != current_rep():
            return jsonify({'error': 'Forbidden'}), 403
        if 'done' in data:
            done = 1 if data['done'] else 0
            db.execute('UPDATE tasks SET done=?, done_at=? WHERE id=?',
                       (done, _now() if done else '', task_id))
            if done:
                # Completing a task logs it and advances any cadence it belongs to.
                _log_activity(db, t['lead_id'], 'note',
                              body=f'✓ Completed: {t["title"] or t["kind"]}', rep=t['rep'])
                if t['enrollment_id']:
                    _advance_cadence(db, t['enrollment_id'])
        if 'due_at' in data:
            db.execute('UPDATE tasks SET due_at=? WHERE id=?', (data['due_at'], task_id))
        _refresh_next_action(db, t['lead_id'])
    return jsonify({'ok': True})

# ── Cadences ──────────────────────────────────────────────────────────────────

@app.route('/api/cadences')
@login_required
def list_cadences():
    return jsonify(CADENCES)

def _create_step_task(db, lead_id, rep, enrollment_id, started_at, step):
    """Materialize one cadence step as a task, due started_at + offset_days."""
    try:
        start_dt = datetime.strptime(started_at, '%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        start_dt = _now_dt()
    due = _iso(start_dt + timedelta(days=int(step.get('offset_days', 0))))
    db.execute('INSERT INTO tasks (id, lead_id, rep, kind, title, due_at, enrollment_id, created_at) '
               'VALUES (?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), lead_id, rep, step.get('kind', 'call'),
                step.get('title', ''), due, enrollment_id, _now()))

@app.route('/api/leads/<lead_id>/enroll', methods=['POST'])
@login_required
def enroll_cadence(lead_id):
    cadence_id = request.get_json(force=True).get('cadence_id')
    cad = CADENCE_BY_ID.get(cadence_id)
    if not cad:
        return jsonify({'error': 'Unknown cadence'}), 400
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # One active enrollment per cadence per lead.
        exists = db.execute('SELECT id FROM cadence_enrollments WHERE lead_id=? AND cadence_id=? AND active=1',
                            (lead_id, cadence_id)).fetchone()
        if exists:
            return jsonify({'error': 'Already enrolled in this cadence'}), 409
        eid = str(uuid.uuid4())
        started = _now()
        db.execute('INSERT INTO cadence_enrollments (id, lead_id, cadence_id, step_idx, started_at, active) '
                   'VALUES (?,?,?,0,?,1)', (eid, lead_id, cadence_id, started))
        if cad['steps']:
            _create_step_task(db, lead_id, row['rep'], eid, started, cad['steps'][0])
        _log_activity(db, lead_id, 'system', body=f'Enrolled in cadence: {cad["name"]}')
        _refresh_next_action(db, lead_id)
    return jsonify({'ok': True, 'enrollment_id': eid}), 201

def _advance_cadence(db, enrollment_id):
    e = db.execute('SELECT * FROM cadence_enrollments WHERE id=? AND active=1', (enrollment_id,)).fetchone()
    if not e:
        return
    cad = CADENCE_BY_ID.get(e['cadence_id'])
    if not cad:
        return
    next_idx = e['step_idx'] + 1
    if next_idx >= len(cad['steps']):
        db.execute('UPDATE cadence_enrollments SET active=0, step_idx=? WHERE id=?', (next_idx, enrollment_id))
        _log_activity(db, e['lead_id'], 'system', body=f'Completed cadence: {cad["name"]}')
        return
    db.execute('UPDATE cadence_enrollments SET step_idx=? WHERE id=?', (next_idx, enrollment_id))
    # Enrollment has no rep column; the task owner is the lead's rep.
    lead = db.execute('SELECT rep FROM leads WHERE id=?', (e['lead_id'],)).fetchone()
    _create_step_task(db, e['lead_id'], lead['rep'] if lead else '', enrollment_id,
                      e['started_at'], cad['steps'][next_idx])

# ── The Den (Base44) handoff ──────────────────────────────────────────────────

def _crm_headers():
    return {'Authorization': f'Bearer {BASE44_TOKEN}', 'Content-Type': 'application/json'}

def _den_payloads(lead):
    """Build the Contact + Project payloads (also used by the dry-run endpoint)."""
    name = (f"{lead['first_name']} {lead['last_name']}").strip() or lead['company'] or 'Unknown'
    assigned = f"{lead['rep']}@{EMAIL_DOMAIN}"
    contact = {
        'name': name, 'first_name': lead['first_name'], 'last_name': lead['last_name'],
        'phone': lead['phone'], 'email': lead['email'],
        'street_address': lead['address'], 'city': lead['city'],
        'state': lead['state'], 'zip_code': lead['zip'],
        'source': lead['source'] or 'referral', 'assigned_to': assigned,
    }
    service_label = SERVICE_META.get(lead.get('service') or 'roofing', SERVICES[0])['label']
    # Recurring plans carry their plan name + billing cadence into the project name/notes
    # so The Den/production can see it's a maintenance agreement, not a one-time job.
    pname = f"{service_label} - {name}"
    notes = ''
    plan = PLAN_BY_ID.get(lead.get('plan') or '')
    if plan:
        pname = f"{plan['name']} ({service_label}) - {name}"
        suffix = BILLING_SUFFIX.get(lead.get('billing') or '', '')
        notes = f"Recurring maintenance plan: {plan['name']}"
        if lead.get('est_value'):
            notes += f" — {int(lead['est_value'])}{suffix}"
    project = {
        'name': pname, 'source': lead['source'] or 'referral',
        'assigned_to': assigned, 'status': 'lead', 'notes': notes,
    }
    return contact, project

def _find_existing_contact(lead):
    """Dedup: reuse a Base44 contact matching phone/email before creating one."""
    if not http:
        return ''
    try:
        r = http.get(f'{BASE44_URL}/entities/Contact', headers=_crm_headers(), timeout=15)
        r.raise_for_status()
        phone = (lead['phone'] or '').strip()
        email = (lead['email'] or '').strip().lower()
        for c in r.json():
            if phone and (c.get('phone') or '').strip() == phone:
                return c.get('id', '')
            if email and (c.get('email') or '').strip().lower() == email:
                return c.get('id', '')
    except Exception as e:
        print(f'[Den] dedup lookup failed: {e}')
    return ''

def _push_to_den(lead_id):
    """Create (or reuse) a Base44 Contact + Project. Returns a result dict."""
    if not BASE44_TOKEN:
        return {'ok': False, 'error': 'BASE44_TOKEN not configured'}
    if not http:
        return {'ok': False, 'error': 'requests library unavailable'}
    with get_db() as db:
        lead = db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()
    if not lead:
        return {'ok': False, 'error': 'Lead not found'}
    lead = dict(lead)
    contact_payload, project_payload = _den_payloads(lead)

    contact_id = lead.get('crm_contact_id') or _find_existing_contact(lead)
    if not contact_id:
        if not contact_payload['name'] or contact_payload['name'] == 'Unknown':
            return {'ok': False, 'error': 'A name is required to push to The Den'}
        try:
            r = http.post(f'{BASE44_URL}/entities/Contact', json=contact_payload,
                          headers=_crm_headers(), timeout=15)
            r.raise_for_status()
            contact_id = r.json().get('id', '')
        except Exception as e:
            return {'ok': False, 'error': f'Contact create failed: {e}'}

    project_payload['contact_id'] = contact_id
    project_id = ''
    try:
        r = http.post(f'{BASE44_URL}/entities/Project', json=project_payload,
                      headers=_crm_headers(), timeout=15)
        r.raise_for_status()
        project_id = r.json().get('id', '')
    except Exception as e:
        print(f'[Den] project create failed: {e}')

    with get_db() as db:
        db.execute('UPDATE leads SET crm_contact_id=?, crm_project_id=?, updated_at=? WHERE id=?',
                   (contact_id, project_id, _now(), lead_id))
        _log_activity(db, lead_id, 'system',
                      body=f'Pushed to The Den (contact {contact_id[:8]}…)', rep=lead['rep'])
    return {'ok': True, 'crm_contact_id': contact_id, 'crm_project_id': project_id}

@app.route('/api/leads/<lead_id>/convert', methods=['POST'])
@login_required
def convert_lead(lead_id):
    """Manual 'Push to Den'. dry_run=1 returns the payloads without writing."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    if request.args.get('dry_run') == '1':
        contact, project = _den_payloads(dict(row))
        return jsonify({'dry_run': True, 'contact': contact, 'project': project})
    return jsonify(_push_to_den(lead_id))

# ── Estimator link ────────────────────────────────────────────────────────────

@app.route('/api/leads/<lead_id>/start-estimate', methods=['POST'])
@login_required
def start_estimate(lead_id):
    """Ensure a Base44 contact exists (the estimator's join key), then hand back
    the estimator URL. The estimator's own contact search picks up the contact."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    lead = dict(row)
    contact_id = lead['crm_contact_id']
    if not contact_id:
        contact_id = _find_existing_contact(lead)
        if not contact_id and BASE44_TOKEN and http:
            contact_payload, _ = _den_payloads(lead)
            try:
                r = http.post(f'{BASE44_URL}/entities/Contact', json=contact_payload,
                              headers=_crm_headers(), timeout=15)
                r.raise_for_status()
                contact_id = r.json().get('id', '')
            except Exception as e:
                return jsonify({'error': f'Could not create contact for estimate: {e}'}), 502
        if contact_id:
            with get_db() as db:
                db.execute('UPDATE leads SET crm_contact_id=?, updated_at=? WHERE id=?',
                           (contact_id, _now(), lead_id))
                _log_activity(db, lead_id, 'system', body='Started an estimate', rep=lead['rep'])
    name = quote((f"{lead['first_name']} {lead['last_name']}").strip() or lead['company'])
    return jsonify({'ok': True, 'contact_id': contact_id,
                    'estimator_url': f'{ESTIMATOR_URL}/?contact={contact_id}&name={name}'})

@app.route('/api/leads/<lead_id>/estimate', methods=['GET'])
@login_required
def lead_estimate(lead_id):
    """Read estimate/job status back from The Den by shared contact_id."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    contact_id = row['crm_contact_id']
    if not contact_id or not (BASE44_TOKEN and http):
        return jsonify({'linked': False, 'documents': [], 'projects': []})
    docs, projects = [], []
    try:
        rd = http.get(f'{BASE44_URL}/entities/Document', headers=_crm_headers(), timeout=15)
        if rd.ok:
            docs = [d for d in rd.json() if d.get('contact_id') == contact_id]
    except Exception as e:
        print(f'[Den] document read failed: {e}')
    try:
        rp = http.get(f'{BASE44_URL}/entities/Project', headers=_crm_headers(), timeout=15)
        if rp.ok:
            projects = [p for p in rp.json() if p.get('contact_id') == contact_id]
    except Exception as e:
        print(f'[Den] project read failed: {e}')
    return jsonify({'linked': True, 'contact_id': contact_id,
                    'documents': docs[:20], 'projects': projects[:20]})

# ── Partners ──────────────────────────────────────────────────────────────────

@app.route('/api/partners')
@login_required
def list_partners():
    """Referral sources (realtors/HOAs/etc.) with how many leads they've sent."""
    clauses = ['lead_type IN (%s)' % ','.join('?' * len(PARTNER_TYPES))]
    params = list(PARTNER_TYPES)
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())
    where = 'WHERE ' + ' AND '.join(clauses)
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where} ORDER BY updated_at DESC', params).fetchall()
        counts = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' GROUP BY referred_by"
        ).fetchall()}
        won = {r['referred_by']: r['c'] for r in db.execute(
            "SELECT referred_by, COUNT(*) c FROM leads WHERE referred_by != '' AND stage='won' GROUP BY referred_by"
        ).fetchall()}
    out = []
    for r in rows:
        d = _lead_row(r)
        d['referrals_total'] = counts.get(r['id'], 0)
        d['referrals_won']   = won.get(r['id'], 0)
        out.append(d)
    return jsonify(out)

# ── Prospecting: bulk import, dedupe, suppression ─────────────────────────────
#
# Partner prospects arrive in bulk from prospector/ (Colorado open data) or from
# a browser harvest dropped into prospector/inbox. Two rules govern every row:
#
#   • Suppression beats everything. A partner who asked to be left alone must
#     not resurface tomorrow in a batch sourced from a different dataset.
#   • Import is idempotent. Re-running a batch inserts nothing, so a run that
#     died halfway is always safe to retry.
#
# Dedupe lives HERE and nowhere else. `POST /api/leads` stays duplicate-friendly
# on purpose: the cross-sell "Pitch" button deliberately creates a second lead
# for the same person as a separate deal, and matching on contact details there
# would break it.

# Text keys read off an incoming prospect row. Anything else in the row is
# ignored rather than rejected, so a source can carry extra provenance fields
# without this needing to know about them.
PROSPECT_TEXT_FIELDS = ['first_name', 'last_name', 'company', 'phone', 'email',
                        'address', 'city', 'state', 'zip', 'website', 'license_no',
                        'source_ref', 'hook']

# What makes two rows the same partner. `source_ref` carries the weight for
# open-data rows that have no contact details at all — a DORA HOA record is a
# name, a city and a licence number, and without a stable key every re-import
# would duplicate it.
_DEDUPE_KEYS = ('phone_norm', 'email_norm', 'license_no', 'source_ref')

def _host_of(url):
    """Bare hostname from a URL or domain, lowercased, no scheme and no www."""
    s = (url or '').strip().lower()
    if '://' in s:
        s = s.split('://', 1)[1]
    s = s.split('/', 1)[0].split('?', 1)[0]
    return s[4:] if s.startswith('www.') else s

def _email_domain(email_norm):
    return email_norm.rsplit('@', 1)[1] if '@' in email_norm else ''

def _suppression_index(db):
    """{kind: set(values)} — one query, rather than one per imported row."""
    idx = {'email': set(), 'phone': set(), 'domain': set()}
    for r in db.execute('SELECT kind, value FROM suppressions'):
        idx[r['kind']].add(r['value'])
    return idx

def _suppressed_by(supp, phone_norm, email_norm, website):
    """Which suppression rule blocks this contact, or '' if none does."""
    if email_norm and email_norm in supp['email']:
        return 'email'
    if phone_norm and phone_norm in supp['phone']:
        return 'phone'
    domain = _email_domain(email_norm) or _host_of(website)
    if domain and domain in supp['domain']:
        return 'domain'
    return ''

def _dedupe_index(db):
    """Existing lead ids keyed by each dedupe value, e.g. {'phone_norm': {...}}.

    Held in memory for the length of one import so rows are also checked against
    *each other* — a single batch routinely lists the same brokerage twice.
    """
    idx = {k: {} for k in _DEDUPE_KEYS}
    cols = ', '.join(_DEDUPE_KEYS)
    for r in db.execute(f'SELECT id, {cols} FROM leads'):
        for k in _DEDUPE_KEYS:
            if r[k]:
                idx[k].setdefault(r[k], r['id'])
    return idx

def _assignment_pool(assign):
    """Usernames to spread a batch across. Returns (pool, error_message)."""
    if assign == 'round_robin':
        users = pusers.all_users()
        pool = [u['username'] for u in users if u['role'] == 'rep']
        # A one-person shop has no 'rep'-role accounts; fall back to everyone
        # rather than silently importing nothing.
        return (pool or [u['username'] for u in users], '')
    if assign:
        if not pusers.get(assign):
            return ([], f'Unknown rep "{assign}"')
        return ([assign], '')
    return ([current_rep()], '')

@app.route('/api/prospects/import', methods=['POST'])
@admin_required
def import_prospects():
    """Bulk-create partner leads from a sourced list. Manager-only.

    Body: {rows[], lead_type, source, assign, dry_run}
      assign  — 'round_robin', a username, or omitted (assigns to the caller)
      dry_run — classify every row and write nothing
    """
    data = request.get_json(force=True)
    rows = data.get('rows')
    if not isinstance(rows, list):
        return jsonify({'error': 'rows must be a list'}), 400
    if len(rows) > 5000:
        return jsonify({'error': 'Batch too large (max 5000 rows)'}), 400

    lead_type = data.get('lead_type', 'referral_partner')
    if lead_type not in LEAD_TYPE_KEYS:
        return jsonify({'error': 'Invalid lead type'}), 400
    service = data.get('service', 'roofing')
    if service not in SERVICE_KEYS:
        return jsonify({'error': 'Invalid service'}), 400

    pool, err = _assignment_pool((data.get('assign') or '').strip())
    if err:
        return jsonify({'error': err}), 400

    dry_run = bool(data.get('dry_run'))
    source  = (data.get('source') or 'prospecting').strip()
    batch   = (data.get('batch') or f'{source}-{_now()}').strip()

    counts  = {'inserted': 0, 'duplicate': 0, 'suppressed': 0, 'invalid': 0}
    details = []

    with get_db() as db:
        supp = _suppression_index(db)
        seen = _dedupe_index(db)

        for i, raw in enumerate(rows):
            if not isinstance(raw, dict):
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'reason': 'not an object'})
                continue

            row = {f: str(raw.get(f) or '').strip() for f in PROSPECT_TEXT_FIELDS}
            row['phone_norm'] = _norm_phone(row['phone'])
            row['email_norm'] = _norm_email(row['email'])
            label = (f"{row['first_name']} {row['last_name']}").strip() or row['company']

            if not label:
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'reason': 'no name or company'})
                continue
            if not any(row[k] for k in _DEDUPE_KEYS):
                # Nothing stable to match on: importing it would duplicate on
                # every future run. Reject rather than poison the list.
                counts['invalid'] += 1
                details.append({'row': i, 'status': 'invalid', 'name': label,
                                'reason': 'no phone, email, licence or source_ref to dedupe on'})
                continue

            blocked = _suppressed_by(supp, row['phone_norm'], row['email_norm'], row['website'])
            if blocked:
                counts['suppressed'] += 1
                details.append({'row': i, 'status': 'suppressed', 'name': label,
                                'reason': f'{blocked} on the suppression list'})
                continue

            hit = next((seen[k][row[k]] for k in _DEDUPE_KEYS
                        if row[k] and row[k] in seen[k]), None)
            if hit:
                counts['duplicate'] += 1
                details.append({'row': i, 'status': 'duplicate', 'name': label,
                                'lead_id': hit})
                continue

            lid = str(uuid.uuid4())
            rep = pool[counts['inserted'] % len(pool)]
            fields = dict(row)
            fields.update({
                'id': lid, 'lead_type': lead_type, 'service': service,
                'source': 'prospecting', 'temperature': 'cold', 'stage': 'new',
                'rep': rep, 'import_batch': batch,
                'est_value': float(raw.get('est_value') or 0),
                'icp_score': int(raw.get('icp_score') or 0),
                'created_at': _now(), 'updated_at': _now(),
            })
            if not dry_run:
                cols = ','.join(fields.keys())
                ph   = ','.join('?' * len(fields))
                db.execute(f'INSERT INTO leads ({cols}) VALUES ({ph})', list(fields.values()))
                _log_activity(db, lid, 'system', rep=rep,
                              body=f'Imported from {source} (batch {batch})')
            # Index it either way, so a dry run reports intra-batch duplicates
            # exactly as the real run would.
            for k in _DEDUPE_KEYS:
                if row[k]:
                    seen[k].setdefault(row[k], lid)
            counts['inserted'] += 1
            details.append({'row': i, 'status': 'inserted', 'name': label,
                            'lead_id': lid, 'rep': rep})

        if dry_run:
            db.rollback()

    return jsonify({'batch': batch, 'source': source, 'lead_type': lead_type,
                    'dry_run': dry_run, 'assigned_to': pool, 'total': len(rows),
                    'counts': counts, 'details': details[:500],
                    'details_truncated': len(details) > 500}), 200 if dry_run else 201

@app.route('/api/prospects/batches')
@admin_required
def list_batches():
    """Import history, derived from leads.import_batch — no separate table."""
    with get_db() as db:
        rows = db.execute(
            "SELECT import_batch AS batch, COUNT(*) AS leads, MIN(created_at) AS first_at, "
            "       MAX(created_at) AS last_at, "
            "       SUM(CASE WHEN stage='won' THEN 1 ELSE 0 END) AS won, "
            "       SUM(CASE WHEN last_activity_at != '' THEN 1 ELSE 0 END) AS touched "
            "FROM leads WHERE import_batch != '' "
            "GROUP BY import_batch ORDER BY first_at DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

# ── Suppressions (opt-outs) ───────────────────────────────────────────────────

SUPPRESSION_KINDS = ('email', 'phone', 'domain')

def _norm_suppression(kind, value):
    """Store suppressions in the same shape the lead columns are matched in."""
    if kind == 'email':
        return _norm_email(value)
    if kind == 'phone':
        return _norm_phone(value)
    return _host_of(value)

@app.route('/api/suppressions', methods=['GET'])
@login_required
def list_suppressions():
    with get_db() as db:
        rows = db.execute('SELECT * FROM suppressions ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/suppressions', methods=['POST'])
@login_required
def add_suppression():
    """Any rep can opt a contact out — asking twice is the thing to prevent."""
    data  = request.get_json(force=True)
    kind  = (data.get('kind') or '').strip()
    if kind not in SUPPRESSION_KINDS:
        return jsonify({'error': f'kind must be one of {", ".join(SUPPRESSION_KINDS)}'}), 400
    value = _norm_suppression(kind, data.get('value'))
    if not value:
        return jsonify({'error': f'Not a usable {kind}'}), 400
    sid = str(uuid.uuid4())
    with get_db() as db:
        existing = db.execute('SELECT * FROM suppressions WHERE kind=? AND value=?',
                              (kind, value)).fetchone()
        if existing:
            return jsonify(dict(existing)), 200      # already opted out; not an error
        db.execute('INSERT INTO suppressions (id, kind, value, reason, created_by, created_at) '
                   'VALUES (?,?,?,?,?,?)',
                   (sid, kind, value, (data.get('reason') or '').strip(),
                    current_rep(), _now()))
        # Flag the matching leads so they drop out of any queue built from here
        # on, not just out of future imports.
        if kind == 'email':
            db.execute('UPDATE leads SET dnc=1, updated_at=? WHERE email_norm=?', (_now(), value))
        elif kind == 'phone':
            db.execute('UPDATE leads SET dnc=1, updated_at=? WHERE phone_norm=?', (_now(), value))
        row = db.execute('SELECT * FROM suppressions WHERE id=?', (sid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/suppressions/<sid>', methods=['DELETE'])
@admin_required
def delete_suppression(sid):
    """Manager-only: undoing an opt-out is not a rep's call."""
    with get_db() as db:
        cur = db.execute('DELETE FROM suppressions WHERE id=?', (sid,))
        if not cur.rowcount:
            return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})

# ── Outreach drafts ───────────────────────────────────────────────────────────
#
# Rendered server-side and handed to the rep as a *draft* — the UI opens it in
# their own Gmail compose window. Nothing here ever sends. That is what keeps
# this 1:1 mail from a real person rather than bulk mail, which in turn is why
# the whole thing needs no sending subdomain, no DKIM setup and no warmup.

# 0 prior touches opens, 1-2 follows up, 3+ closes the loop. Partners who have
# ignored three emails are not persuaded by a fourth.
def _draft_step(touches):
    if touches <= 0:
        return 'first'
    return 'followup' if touches < 3 else 'breakup'

def _fill(text, ctx):
    """Substitute slots, then drop paragraphs an empty slot left blank.

    `{hook}` sits in its own paragraph precisely so an unresearched lead gets a
    shorter email rather than a visible gap where the personal line should be.
    """
    for key, val in ctx.items():
        text = text.replace('{' + key + '}', val)
    return '\n\n'.join(p for p in (p.strip() for p in text.split('\n\n')) if p)

def _render_draft(lead, step, rep_name):
    """{'subject','body','step'} for a lead, or None if no template applies."""
    tpls = TEMPLATES.get('templates', {})
    tpl = tpls.get(lead.get('lead_type')) or tpls.get('referral_partner')
    if not tpl or step not in tpl:
        return None
    first = (lead.get('first_name') or '').strip()
    ctx = {
        'greeting':   f'Hi {first},' if first else 'Hi there,',
        'first_name': first,
        'company':    (lead.get('company') or '').strip(),
        'city':       (lead.get('city') or '').strip() or 'the Front Range',
        'hook':       (lead.get('hook') or '').strip(),
        'rep_name':   rep_name,
        'rep_first':  rep_name.split(' ')[0] if rep_name else '',
    }
    sig = TEMPLATES.get('signature', '')
    body = _fill(tpl[step]['body'], ctx)
    if sig:
        body += '\n\n' + _fill(sig, ctx)
    return {'subject': _fill(tpl[step]['subject'], ctx), 'body': body, 'step': step}

@app.route('/api/leads/<lead_id>/draft')
@login_required
def lead_draft(lead_id):
    """The email a rep would send this partner right now."""
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
        touches = db.execute(
            'SELECT COUNT(*) c FROM activities WHERE lead_id = ? AND kind IN (%s)'
            % ','.join('?' * len(OUTREACH_KINDS)),
            [lead_id] + list(OUTREACH_KINDS)).fetchone()['c']
    step = request.args.get('step') or _draft_step(touches)
    draft = _render_draft(dict(row), step, pusers.display_name(current_rep()))
    if not draft:
        return jsonify({'error': 'No template for this lead type'}), 404
    return jsonify(draft)

# ── Outreach queue ────────────────────────────────────────────────────────────
#
# What a rep actually works. Two halves, and the split is the point:
#
#   due  — tasks already scheduled, i.e. cadence re-touches on partners they've
#          met. For partner development this is the half that converts; a
#          realtor who sees you five times refers, a hundred who see you once
#          don't.
#   new  — net-new cold cards, only enough to top the day up to the target.
#
# Because re-touches count toward the number, sourcing demand is roughly half
# the daily target, which is what keeps the free data lasting.

DAILY_TARGET  = int(os.environ.get('SALESCRM_DAILY_TARGET', '40'))
# Never put the same partner back in front of a rep inside this window. Mirrors
# the non-negotiable 7-day cooldown the outreach skills already enforce.
COOLDOWN_DAYS = int(os.environ.get('SALESCRM_COOLDOWN_DAYS', '7'))

def _end_of_today():
    return _iso(_now_dt().replace(hour=23, minute=59, second=59))

def _start_of_today():
    return _iso(_now_dt().replace(hour=0, minute=0, second=0))

@app.route('/api/queue/today')
@login_required
def queue_today():
    """Today's touch list for one rep, capped at the daily target."""
    rep = request.args.get('rep') or current_rep()
    if rep != current_rep() and not is_manager():
        return jsonify({'error': 'Forbidden'}), 403
    target = max(1, min(int(request.args.get('target') or DAILY_TARGET), 200))
    cooldown = _iso(_now_dt() - timedelta(days=COOLDOWN_DAYS))

    with get_db() as db:
        supp = _suppression_index(db)

        # Already-scheduled work: cadence steps and manual follow-ups due by
        # end of day. dnc=0 keeps opted-out partners out even mid-cadence.
        due = [dict(r) for r in db.execute(
            'SELECT t.id, t.kind, t.title, t.due_at, t.lead_id, '
            '       l.first_name, l.last_name, l.company, l.phone, l.email, '
            '       l.website, l.city, l.stage, l.lead_type, l.icp_score, l.hook '
            'FROM tasks t JOIN leads l ON l.id = t.lead_id '
            'WHERE t.rep = ? AND t.done = 0 AND t.due_at <= ? AND l.dnc = 0 '
            'ORDER BY t.due_at LIMIT ?', (rep, _end_of_today(), target)).fetchall()]
        for d in due:
            d['name'] = (f"{d['first_name']} {d['last_name']}").strip() or d['company']
            d['overdue'] = d['due_at'] < _now()

        done_today = db.execute(
            'SELECT COUNT(*) c FROM activities WHERE rep = ? AND created_at >= ? '
            'AND kind IN (%s)' % ','.join('?' * len(OUTREACH_KINDS)),
            [rep, _start_of_today()] + list(OUTREACH_KINDS)).fetchone()['c']

        # Top up with net-new. Anything with an open task is already in `due`,
        # and anything touched inside the cooldown is deliberately left alone.
        room = max(0, target - len(due) - done_today)
        fresh = []
        if room:
            rows = db.execute(
                "SELECT * FROM leads "
                "WHERE rep = ? AND stage = 'new' AND dnc = 0 "
                "  AND (last_activity_at = '' OR last_activity_at < ?) "
                "  AND id NOT IN (SELECT lead_id FROM tasks WHERE done = 0) "
                "ORDER BY icp_score DESC, created_at ASC LIMIT ?",
                (rep, cooldown, room * 3)).fetchall()
            for r in rows:
                # Re-check suppression here, not just at import: a domain added
                # to the list this morning has to drop rows imported last week.
                if _suppressed_by(supp, r['phone_norm'], r['email_norm'], r['website']):
                    continue
                fresh.append(_lead_row(r))
                if len(fresh) >= room:
                    break

        # Attach the email each card would send. One grouped query for the
        # touch counts rather than one per card.
        ids = [d['lead_id'] for d in due] + [f['id'] for f in fresh]
        touches = {}
        if ids:
            rows = db.execute(
                'SELECT lead_id, COUNT(*) c FROM activities '
                'WHERE lead_id IN (%s) AND kind IN (%s) GROUP BY lead_id'
                % (','.join('?' * len(ids)), ','.join('?' * len(OUTREACH_KINDS))),
                ids + list(OUTREACH_KINDS)).fetchall()
            touches = {r['lead_id']: r['c'] for r in rows}

    rep_name = pusers.display_name(rep)
    for item, lid in ([(d, d['lead_id']) for d in due] + [(f, f['id']) for f in fresh]):
        item['draft'] = _render_draft(item, _draft_step(touches.get(lid, 0)), rep_name)

    return jsonify({
        'rep': rep, 'target': target, 'done_today': done_today,
        'cooldown_days': COOLDOWN_DAYS,
        'due': due, 'new': fresh,
        'remaining': max(0, target - done_today),
    })

@app.route('/api/queue/assign', methods=['POST'])
@admin_required
def queue_assign():
    """Spread a manager's unworked prospects across the reps who'll call them.

    Import parks a batch on whoever ran it unless told otherwise; this is the
    "now hand it out" step. Only untouched `new` leads move, so a rep never
    loses a partner they've already spoken to.
    """
    data = request.get_json(force=True)
    from_rep = (data.get('from_rep') or current_rep()).strip()
    limit    = max(1, min(int(data.get('limit') or 1000), 5000))

    reps = data.get('reps')
    if reps:
        unknown = [r for r in reps if not pusers.get(r)]
        if unknown:
            return jsonify({'error': f'Unknown rep(s): {", ".join(unknown)}'}), 400
    else:
        pool, err = _assignment_pool('round_robin')
        if err:
            return jsonify({'error': err}), 400
        reps = pool
    if not reps:
        return jsonify({'error': 'No reps to assign to'}), 400

    with get_db() as db:
        rows = db.execute(
            "SELECT id FROM leads WHERE rep = ? AND stage = 'new' AND dnc = 0 "
            "AND last_activity_at = '' AND import_batch != '' "
            "ORDER BY icp_score DESC, created_at ASC LIMIT ?",
            (from_rep, limit)).fetchall()
        if not data.get('dry_run'):
            for i, r in enumerate(rows):
                db.execute('UPDATE leads SET rep = ?, updated_at = ? WHERE id = ?',
                           (reps[i % len(reps)], _now(), r['id']))

    per = {}
    for i in range(len(rows)):
        rep = reps[i % len(reps)]
        per[rep] = per.get(rep, 0) + 1
    return jsonify({'from_rep': from_rep, 'moved': len(rows), 'per_rep': per,
                    'dry_run': bool(data.get('dry_run'))})

# ── Dashboard / scorecards / coaching ─────────────────────────────────────────

def _date_bounds(days):
    start = _iso((_now_dt() - timedelta(days=days)).replace(hour=0, minute=0, second=0))
    return start

@app.route('/api/dashboard')
@login_required
def dashboard():
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    rep_filter = request.args.get('rep')
    # Reps only ever see their own numbers.
    rep = current_rep() if not is_manager() else rep_filter

    lead_where, lead_params = [], []
    if rep:
        lead_where.append('rep=?'); lead_params.append(rep)
    lw = ('WHERE ' + ' AND '.join(lead_where)) if lead_where else ''

    with get_db() as db:
        # Funnel: count of leads that have EVER reached each stage would need history;
        # here we use current stage counts + won/lost for conversion snapshot.
        stage_counts = {s: 0 for s in STAGE_KEYS}
        for r in db.execute(f'SELECT stage, COUNT(*) c FROM leads {lw} GROUP BY stage', lead_params):
            stage_counts[r['stage']] = r['c']

        new_where = lead_where + ['created_at >= ?']
        np = lead_params + [since]
        new_leads = db.execute(f'SELECT COUNT(*) c FROM leads WHERE {" AND ".join(new_where)}', np).fetchone()['c']

        won_where = lead_where + ["stage='won'", 'won_at >= ?']
        wp = lead_params + [since]
        won_row = db.execute(f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v '
                             f'FROM leads WHERE {" AND ".join(won_where)}', wp).fetchone()
        won_count, won_value = won_row['c'], won_row['v']

        lost_where = lead_where + ["stage='lost'", 'updated_at >= ?']
        lost_count = db.execute(f'SELECT COUNT(*) c FROM leads WHERE {" AND ".join(lost_where)}',
                                lead_params + [since]).fetchone()['c']

        # Open pipeline value (all open stages, regardless of date).
        open_where = lead_where + ['stage IN (%s)' % ','.join('?' * len(OPEN_STAGES))]
        pipe = db.execute(f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                          f'WHERE {" AND ".join(open_where)}', lead_params + OPEN_STAGES).fetchone()

        # Activity volume in window.
        act_where, act_params = ['created_at >= ?'], [since]
        if rep:
            act_where.append('rep=?'); act_params.append(rep)
        act_rows = {r['kind']: r['c'] for r in db.execute(
            f'SELECT kind, COUNT(*) c FROM activities WHERE {" AND ".join(act_where)} GROUP BY kind',
            act_params)}

        # Source attribution + location split.
        by_source = {r['source'] or 'unknown': r['c'] for r in db.execute(
            f'SELECT source, COUNT(*) c FROM leads {lw} GROUP BY source', lead_params)}
        by_state = {(r['state'] or '??').upper(): r['c'] for r in db.execute(
            f'SELECT state, COUNT(*) c FROM leads {lw} GROUP BY state', lead_params)}
        # Service-line split: open pipeline + won revenue per service.
        by_service = {}
        for s in SERVICES:
            row = db.execute(
                f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                f'WHERE {" AND ".join(lead_where + ["service=?", "stage IN (%s)" % ",".join("?"*len(OPEN_STAGES))])}',
                lead_params + [s['key']] + OPEN_STAGES).fetchone()
            wrow = db.execute(
                f'SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                f'WHERE {" AND ".join(lead_where + ["service=?", "stage=?", "won_at >= ?"])}',
                lead_params + [s['key'], 'won', since]).fetchone()
            by_service[s['key']] = {'label': s['label'], 'icon': s['icon'],
                                    'open': row['c'], 'open_value': row['v'],
                                    'won': wrow['c'], 'won_value': wrow['v']}

        # Recurring revenue: every WON deal with a billing cadence is an ACTIVE PLAN.
        # MRR normalizes each to a monthly figure (a $300/qtr plan = $100 MRR).
        # This is current-state (running book of business), not date-windowed.
        mrr = 0.0
        active_plans = 0
        plan_mix = {}
        recur_where = lead_where + ["stage='won'", "billing != ''"]
        for r in db.execute(f'SELECT est_value, billing, plan FROM leads '
                            f'WHERE {" AND ".join(recur_where)}', lead_params):
            months = BILLING_MONTHS.get(r['billing'], 1)
            mrr += (r['est_value'] or 0) / months
            active_plans += 1
            pname = (PLAN_BY_ID.get(r['plan']) or {}).get('name', 'Custom / no plan')
            plan_mix[pname] = plan_mix.get(pname, 0) + 1
        mrr = round(mrr, 0)

    decided = won_count + lost_count
    win_rate = round(100 * won_count / decided, 1) if decided else 0.0
    avg_deal = round(won_value / won_count, 0) if won_count else 0.0
    outreach = sum(act_rows.get(k, 0) for k in OUTREACH_KINDS)

    return jsonify({
        'days': days, 'rep': rep,
        'stage_counts': stage_counts,
        'stages': STAGES,
        'new_leads': new_leads,
        'won_count': won_count, 'won_value': won_value,
        'lost_count': lost_count, 'win_rate': win_rate, 'avg_deal': avg_deal,
        'pipeline_count': pipe['c'], 'pipeline_value': pipe['v'],
        'activity': act_rows, 'outreach_total': outreach,
        'by_source': by_source, 'by_state': by_state, 'by_service': by_service,
        'mrr': mrr, 'arr': round(mrr * 12, 0), 'active_plans': active_plans, 'plan_mix': plan_mix,
    })

@app.route('/api/leaderboard')
@login_required
def leaderboard():
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    with get_db() as db:
        reps = [r['rep'] for r in db.execute('SELECT DISTINCT rep FROM leads').fetchall()]
        board = []
        for rep in reps:
            acts = db.execute('SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? '
                              'AND kind IN (%s)' % ','.join('?' * len(OUTREACH_KINDS)),
                              [rep, since] + list(OUTREACH_KINDS)).fetchone()['c']
            won = db.execute("SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads "
                             "WHERE rep=? AND stage='won' AND won_at >= ?", (rep, since)).fetchone()
            appts = db.execute("SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? "
                               "AND kind='stage_change' AND body LIKE '%→ Appt Set%'",
                               (rep, since)).fetchone()['c']
            board.append({'rep': rep, 'outreach': acts, 'appts_set': appts,
                          'won': won['c'], 'won_value': won['v']})
    board.sort(key=lambda x: (x['won'], x['won_value'], x['outreach']), reverse=True)
    return jsonify(board)

@app.route('/api/scorecard/<rep>')
@login_required
def scorecard(rep):
    if not is_manager() and rep != current_rep():
        return jsonify({'error': 'Forbidden'}), 403
    days = min(int(request.args.get('days', 30)), 365)
    since = _date_bounds(days)
    with get_db() as db:
        activity = {r['kind']: r['c'] for r in db.execute(
            'SELECT kind, COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? GROUP BY kind',
            (rep, since))}
        won = db.execute("SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads "
                         "WHERE rep=? AND stage='won' AND won_at >= ?", (rep, since)).fetchone()
        lost = db.execute("SELECT COUNT(*) c FROM leads WHERE rep=? AND stage='lost' AND updated_at >= ?",
                          (rep, since)).fetchone()['c']
        new_leads = db.execute('SELECT COUNT(*) c FROM leads WHERE rep=? AND created_at >= ?',
                               (rep, since)).fetchone()['c']
        open_pipe = db.execute('SELECT COUNT(*) c, COALESCE(SUM(est_value),0) v FROM leads '
                               'WHERE rep=? AND stage IN (%s)' % ','.join('?' * len(OPEN_STAGES)),
                               [rep] + OPEN_STAGES).fetchone()
        estimates = db.execute("SELECT COUNT(*) c FROM activities WHERE rep=? AND created_at >= ? "
                               "AND kind='stage_change' AND body LIKE '%→ Estimate Presented%'",
                               (rep, since)).fetchone()['c']
        # Avg sales cycle (days) for won deals in window.
        cyc = db.execute("SELECT created_at, won_at FROM leads WHERE rep=? AND stage='won' AND won_at >= ?",
                         (rep, since)).fetchall()
        stalled = db.execute('SELECT COUNT(*) c FROM leads WHERE rep=? AND stage IN (%s)'
                             % ','.join('?' * len(OPEN_STAGES)), [rep] + OPEN_STAGES).fetchall()
        goals = [dict(g) for g in db.execute('SELECT * FROM goals WHERE rep=?', (rep,)).fetchall()]
    cycles = []
    for c in cyc:
        try:
            a = datetime.strptime(c['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            b = datetime.strptime(c['won_at'], '%Y-%m-%dT%H:%M:%SZ')
            cycles.append((b - a).days)
        except Exception:
            pass
    avg_cycle = round(sum(cycles) / len(cycles), 1) if cycles else None
    decided = won['c'] + lost
    return jsonify({
        'rep': rep, 'days': days,
        'activity': activity,
        'outreach_total': sum(activity.get(k, 0) for k in OUTREACH_KINDS),
        'new_leads': new_leads, 'estimates_presented': estimates,
        'won': won['c'], 'won_value': won['v'], 'lost': lost,
        'win_rate': round(100 * won['c'] / decided, 1) if decided else 0.0,
        'avg_deal': round(won['v'] / won['c'], 0) if won['c'] else 0.0,
        'avg_cycle_days': avg_cycle,
        'open_pipeline_count': open_pipe['c'], 'open_pipeline_value': open_pipe['v'],
        'goals': goals,
    })

@app.route('/api/stalled')
@login_required
def stalled_leads():
    """Open leads with no activity in STALL_DAYS — coaching cues."""
    clauses, params = ['stage IN (%s)' % ','.join('?' * len(OPEN_STAGES))], list(OPEN_STAGES)
    if not is_manager():
        clauses.append('rep=?'); params.append(current_rep())
    elif request.args.get('rep'):
        clauses.append('rep=?'); params.append(request.args.get('rep'))
    where = 'WHERE ' + ' AND '.join(clauses)
    with get_db() as db:
        rows = db.execute(f'SELECT * FROM leads {where}', params).fetchall()
    return jsonify([_lead_row(r) for r in rows if _is_stalled(dict(r))])

# ── Coaching notes + goals (manager) ──────────────────────────────────────────

@app.route('/api/coaching/<rep>', methods=['GET', 'POST'])
@login_required
def coaching_notes(rep):
    if not is_manager():
        return jsonify({'error': 'Forbidden'}), 403
    if request.method == 'POST':
        body = (request.get_json(force=True).get('body') or '').strip()
        if not body:
            return jsonify({'error': 'Empty note'}), 400
        with get_db() as db:
            db.execute('INSERT INTO coaching_notes (id, subject_rep, author, body, created_at) '
                       'VALUES (?,?,?,?,?)',
                       (str(uuid.uuid4()), rep, current_rep(), body, _now()))
        return jsonify({'ok': True}), 201
    with get_db() as db:
        rows = db.execute('SELECT * FROM coaching_notes WHERE subject_rep=? ORDER BY created_at DESC',
                          (rep,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals', methods=['GET', 'POST'])
@login_required
def goals():
    if request.method == 'POST':
        if not is_manager():
            return jsonify({'error': 'Forbidden'}), 403
        d = request.get_json(force=True)
        gid = str(uuid.uuid4())
        with get_db() as db:
            db.execute('INSERT INTO goals (id, rep, period, metric, target) VALUES (?,?,?,?,?)',
                       (gid, d.get('rep'), d.get('period'), d.get('metric'), float(d.get('target') or 0)))
        return jsonify({'ok': True, 'id': gid}), 201
    rep = request.args.get('rep') if is_manager() else current_rep()
    with get_db() as db:
        rows = db.execute('SELECT * FROM goals WHERE rep=?' if rep else 'SELECT * FROM goals',
                          (rep,) if rep else ()).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/goals/<goal_id>', methods=['DELETE'])
@admin_required
def delete_goal(goal_id):
    with get_db() as db:
        db.execute('DELETE FROM goals WHERE id=?', (goal_id,))
    return jsonify({'ok': True})

# ── Documents (per-lead files on the persistent volume) ───────────────────────

# Absolute so send_from_directory resolves regardless of the process CWD.
DOCS_DIR = os.path.abspath(os.path.join(DATA_DIR, 'documents'))
ALLOWED_DOC_EXT = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'heic', 'webp', 'doc', 'docx',
                   'xls', 'xlsx', 'csv', 'txt', 'zip'}
MAX_DOC_BYTES = 25 * 1024 * 1024   # 25 MB per file

def _doc_row(r):
    d = dict(r)
    d.pop('filename', None)                      # never expose the on-disk name
    d['url'] = f"/api/documents/{d['id']}/download"
    return d

@app.route('/api/leads/<lead_id>/documents', methods=['GET', 'POST'])
@login_required
def lead_documents(lead_id):
    with get_db() as db:
        row = _lead_visible(db, lead_id)
        if not row:
            return jsonify({'error': 'Not found'}), 404
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'error': 'No file'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_DOC_EXT:
            return jsonify({'error': f'File type .{ext} not allowed'}), 400
        blob = f.read()
        if len(blob) > MAX_DOC_BYTES:
            return jsonify({'error': 'File too large (25 MB max)'}), 400
        os.makedirs(DOCS_DIR, exist_ok=True)
        did = str(uuid.uuid4())
        stored = f'{did}.{ext}'
        with open(os.path.join(DOCS_DIR, stored), 'wb') as out:
            out.write(blob)
        orig = os.path.basename(f.filename)
        with get_db() as db:
            db.execute('INSERT INTO documents (id, lead_id, filename, orig_name, size, '
                       'uploaded_by, created_at) VALUES (?,?,?,?,?,?,?)',
                       (did, lead_id, stored, orig, len(blob), current_rep(), _now()))
            _log_activity(db, lead_id, 'system', body=f'📎 Uploaded document: {orig}')
        return jsonify({'ok': True, 'id': did}), 201
    with get_db() as db:
        rows = db.execute('SELECT * FROM documents WHERE lead_id=? ORDER BY created_at DESC',
                          (lead_id,)).fetchall()
    return jsonify([_doc_row(r) for r in rows])

@app.route('/api/documents/<doc_id>/download')
@login_required
def download_document(doc_id):
    with get_db() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            return jsonify({'error': 'Not found'}), 404
        if not _lead_visible(db, doc['lead_id']):     # inherit the lead's visibility
            return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory(DOCS_DIR, doc['filename'], as_attachment=True,
                               download_name=doc['orig_name'])

@app.route('/api/documents/<doc_id>', methods=['DELETE'])
@login_required
def delete_document(doc_id):
    with get_db() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            return jsonify({'error': 'Not found'}), 404
        if not _lead_visible(db, doc['lead_id']):
            return jsonify({'error': 'Forbidden'}), 403
        db.execute('DELETE FROM documents WHERE id=?', (doc_id,))
    try:
        os.remove(os.path.join(DOCS_DIR, doc['filename']))
    except OSError:
        pass
    return jsonify({'ok': True})

# ── Playbook / plans / config / health ────────────────────────────────────────

@app.route('/api/playbook')
@login_required
def playbook():
    return jsonify(PLAYBOOK)

@app.route('/api/plans')
@login_required
def plans():
    return jsonify(PLANS)

@app.route('/api/config')
def config():
    return jsonify({
        'stages': STAGES, 'lead_types': LEAD_TYPES, 'sources': SOURCES,
        'temperature': TEMPERATURE, 'partner_types': PARTNER_TYPES,
        'services': SERVICES, 'plans': PLANS,
        'billing_options': [
            {'key': '',          'label': 'One-time'},
            {'key': 'monthly',   'label': 'Monthly'},
            {'key': 'quarterly', 'label': 'Quarterly'},
            {'key': 'annual',    'label': 'Annual'},
        ],
        'stall_days': STALL_DAYS,
        'daily_target': DAILY_TARGET,
        'cooldown_days': COOLDOWN_DAYS,
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH, 'den': bool(BASE44_TOKEN),
                    'plans': len(PLANS)})

if __name__ == '__main__':
    app.run(debug=True, port=5002)
