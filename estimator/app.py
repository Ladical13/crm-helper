import io
import os
import re
import sys
import copy
import math
import json
import calendar
import time
import uuid
import shutil
import socket
import secrets
import hashlib
import smtplib
import zipfile
import threading
import html as _html
from datetime import datetime, timedelta, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from functools import wraps
from urllib.parse import quote
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, session, redirect, make_response

# The portal package lives one directory up. Put the repo root on the path so
# this app works both mounted by portal/wsgi.py and run standalone (its test
# suite imports app.py directly with the repo root nowhere in sight).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portal import funnel as pfunnel     # noqa: E402
from portal import session as psession   # noqa: E402
from portal import throttle as pthrottle  # noqa: E402
from portal import users as pusers       # noqa: E402

try:
    import requests as http
except ImportError:
    http = None

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

try:
    import pypdf as _pypdf
except ImportError:
    _pypdf = None

app = Flask(__name__, static_folder='static')
# Secret key, ProxyFix, and cookie settings identical to the other three apps.
# They share one cookie and each re-saves it whenever it touches session, so a
# mismatched flag here logs the rep out of all of them at random. This app used
# to derive SESSION_COOKIE_SECURE from DATA_DIR while the other two used
# RAILWAY_ENVIRONMENT — exactly the kind of drift that breaks.
psession.configure(app, max_content_length=30 * 1024 * 1024)  # cap uploads at 30 MB

TEAM_MEMBERS = [
    'avery', 'bryan', 'derik', 'luke', 'phil',
]

# Fallback office number shown when a rep hasn't set their own cell/email
# override in team.json (Team Logins panel).
COMPANY_PHONE_DIGITS  = '9707760945'
COMPANY_PHONE_DISPLAY = '970-776-0945'

# Full display names — username → "First Last"
TEAM_DISPLAY_NAMES = {
    'avery': 'Avery Schroeder',
    'bryan': 'Bryan Samsel',
    'derik': 'Derik Lints',
    'luke':  'Luke Durnbaugh',
    'phil':  'Phil Hunt',
}

def _display_name(username):
    if username in TEAM_DISPLAY_NAMES:
        return TEAM_DISPLAY_NAMES[username]
    return ' '.join(p.capitalize() for p in username.replace('.', ' ').split())

# ── User accounts ───────────────────────────────────────────────────────────
# Accounts used to live in DATA_DIR/users.json. They now live in the portal's
# shared store (portal/users.py) so one password works across the estimator,
# the canvasser, and the sales CRM. portal/migrate_users.py moved the existing
# records across, hashes intact. team.json below is a different thing and stays
# here: it is estimator display-name/phone/email config, not authentication.

def load_team():
    """Return [{username, display_name}] from team.json, seeding from hardcoded list on first run."""
    if os.path.exists(TEAM_CONFIG_FILE):
        try:
            with open(TEAM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return [{'username': u, 'display_name': TEAM_DISPLAY_NAMES.get(u, '')} for u in TEAM_MEMBERS]

def save_team(team):
    with open(TEAM_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(team, f, indent=2)

def _get_role(username):
    """Return 'admin', 'manager', or 'rep' for the given username."""
    return pusers.role_of(username)

def _is_admin(username):
    return pusers.is_admin(username)

def _current_user():
    return session.get('user', '')

def _is_manager_up(username=None):
    """Managers and admins can see/touch everything; reps only their own."""
    return _get_role(username if username is not None else _current_user()) in ('admin', 'manager')

def _can_touch_estimate(est):
    """Ownership check for estimate reads/writes. Reps may act on their own
    (or still-unassigned) estimates; managers and admins on any."""
    if _is_manager_up():
        return True
    sp = (est.get('salesperson') or '').strip()
    return not sp or sp == _current_user()

def _forbid():
    return jsonify({'error': 'access denied'}), 403

def _safe_path_id(s):
    """Guard ids/filenames that end up in filesystem paths."""
    return bool(s) and bool(re.fullmatch(r'[A-Za-z0-9._-]+', s)) and '..' not in s

# ── Default-deny auth guard ─────────────────────────────────────────────────
# Every request requires a logged-in session EXCEPT the explicit allowlist below.
# This is the opposite of decorating each protected route (which is what let the
# data APIs leak — a route added without the decorator was silently public).
PUBLIC_ENDPOINTS = {
    'customer_sign',     # /sign/<token> — public, protected by the 192-bit token
    'sign_change_order', # /sign-co/<token> — same token protection as /sign
    'serve_upload',      # /uploads/<file> — cover photos shown on the customer view
    'static',            # JS/CSS for the login + app shell (non-sensitive client code)
    'pwa_manifest',      # /manifest.json — needed for PWA install before login
    'service_worker',    # /sw.js — service worker scope must be public
}

DISABLE_AUTH = os.environ.get('DISABLE_AUTH', '').strip().lower() in ('1', 'true', 'yes')

# ...but never in production, whatever the variable says. This one flag turns
# off the guard for every route below — estimates, signed contracts, customer
# PII, the price book. It exists so a developer can poke at the app locally
# without enrolling, and the cost of that convenience is one fat-fingered
# Railway variable away from publishing the whole estimator. RAILWAY_ENVIRONMENT
# is set by the platform and is the same signal portal/session.py uses to decide
# the cookies are going over HTTPS.
if DISABLE_AUTH and os.environ.get('RAILWAY_ENVIRONMENT'):
    print('[auth] DISABLE_AUTH is set but ignored: refusing to disable '
          'authentication in a deployed environment.')
    DISABLE_AUTH = False

@app.before_request
def _require_login():
    if DISABLE_AUTH or request.endpoint in PUBLIC_ENDPOINTS or session.get('user'):
        return
    # Unauthenticated: JSON 401 for API calls (the SPA redirects), else to login.
    if request.path.startswith('/api/'):
        return jsonify({'error': 'authentication required'}), 401
    # '/login' is root-absolute on purpose: the portal owns it, and under the
    # mount this app's own paths are all under /estimate. script_root is that
    # prefix ('' standalone), so `next` sends the rep back where they were.
    return redirect('/login?next=' + quote(request.script_root + request.path, safe='/'))

BASE_URL = "https://base44.app/api/apps/69320ef0c647fee442697971"
# Base44 API token — MUST be supplied via the BASE44_TOKEN env var (never commit
# a token to source). NOTE: tokens expire (check the JWT exp claim). 401s from the
# CRM = expired or missing token; rotate in Base44 and update BASE44_TOKEN.
TOKEN = os.environ.get('BASE44_TOKEN', '').strip()
if not TOKEN:
    print('[CRM] WARNING: BASE44_TOKEN is not set — CRM lookups will fail until it is configured.')
CO_LOCATION_ID = "6984bb86d86d9c92d6827a17"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR: where estimates, uploads, and config files live.
# Set DATA_DIR env var to a persistent volume path on Railway (e.g. /data).
# Falls back to BASE_DIR so local development works unchanged.
#
# Normalized to an absolute path even when the env var is relative, because
# send_from_directory (Flask 2+) joins a relative directory against
# app.root_path, not os.getcwd() — so a relative DATA_DIR would let writes
# land in <cwd>/uploads while reads looked in <app_root>/uploads. An
# already-absolute path passes through unchanged.
DATA_DIR = os.path.abspath(os.environ.get('DATA_DIR', BASE_DIR))

ESTIMATES_DIR = os.path.join(DATA_DIR, 'estimates')
UPLOADS_DIR   = os.path.join(DATA_DIR, 'uploads')
ALLOWED_EXT   = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.pdf'}

PRICE_BOOK_FILE      = os.path.join(DATA_DIR, 'price_book.json')
TIER_DEFAULTS_FILE   = os.path.join(DATA_DIR, 'tier_defaults.json')
PERMIT_DEFAULTS_FILE = os.path.join(DATA_DIR, 'permit_defaults.json')
JURISDICTIONS_FILE   = os.path.join(DATA_DIR, 'jurisdictions.json')
CUSTOMER_NOTES_FILE  = os.path.join(DATA_DIR, 'customer_notes.json')
TEAM_CONFIG_FILE     = os.path.join(DATA_DIR, 'team.json')
COMPANY_CONTENT_FILE = os.path.join(DATA_DIR, 'company_content.json')
SALES_GOALS_FILE     = os.path.join(DATA_DIR, 'sales_goals.json')
COMM_FASTENING_FILE  = os.path.join(DATA_DIR, 'commercial_fastening.json')

# Optional override for the public-facing base URL (e.g. ngrok or a real domain).
# Set PUBLIC_URL in environment or in estimator/config.json as {"public_url": "https://..."}
def _load_public_url():
    env = os.environ.get('PUBLIC_URL', '').rstrip('/')
    if env:
        return env
    cfg = os.path.join(DATA_DIR, 'config.json')
    if os.path.exists(cfg):
        try:
            with open(cfg) as f:
                return json.load(f).get('public_url', '').rstrip('/')
        except Exception:
            pass
    return ''

def _get_lan_ip():
    """Return this machine's LAN IP (fallback: 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

PUBLIC_URL = _load_public_url()
LAN_IP     = _get_lan_ip()

def get_public_url():
    """Re-read config on every call so a public URL saved through the UI takes
    effect in every gunicorn worker — not just the one that handled the save."""
    return _load_public_url()

def _base_url():
    return get_public_url() or f'http://{LAN_IP}:5000'

os.makedirs(ESTIMATES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def _seed_data_dir():
    """On first run with a new DATA_DIR (e.g. Railway persistent volume),
    copy seed files from the app directory so defaults are available."""
    if DATA_DIR == BASE_DIR:
        return  # local dev — nothing to seed
    for fname in ('price_book.json', 'tier_defaults.json', 'permit_defaults.json',
                  'jurisdictions.json', 'commercial_fastening.json',
                  'company_content.json'):
        src = os.path.join(BASE_DIR, fname)
        dst = os.path.join(DATA_DIR, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

_seed_data_dir()

# ── Estimate storage layer ──────────────────────────────────────────────────
# ALL estimate persistence goes through these helpers — never open an estimate
# file directly. Two backends behind the same functions:
#   * Postgres (jsonb doc store) when DATABASE_URL is set — production on
#     Railway. Survives redeploys/volume loss and serializes the 2 gunicorn
#     workers properly (est_update runs SELECT ... FOR UPDATE).
#   * Flat JSON files in ESTIMATES_DIR otherwise — local dev, unchanged.
# On first boot with an empty table, existing estimates/*.json are migrated
# in (files are left in place as a cold backup — never deleted or renamed).

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if DATABASE_URL.startswith('postgres://'):        # Railway sometimes emits the
    DATABASE_URL = 'postgresql://' + DATABASE_URL[len('postgres://'):]  # old scheme

if DATABASE_URL:
    import psycopg
    from psycopg.types.json import Json as _PgJson

    def _db_conn():
        return psycopg.connect(DATABASE_URL)

    def _db_init():
        """Create schema; one-time migrate estimates/*.json into an empty
        table. The advisory lock serializes the gunicorn workers so only
        one runs the migration; inserts are idempotent regardless."""
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(727100001)')
            try:
                cur.execute('CREATE TABLE IF NOT EXISTS estimates ('
                            'id text PRIMARY KEY, doc jsonb NOT NULL, '
                            'updated_at timestamptz NOT NULL DEFAULT now())')
                cur.execute("CREATE INDEX IF NOT EXISTS idx_est_share_token "
                            "ON estimates ((doc->>'share_token'))")
                cur.execute('SELECT count(*) FROM estimates')
                if cur.fetchone()[0] == 0:
                    n = 0
                    try:
                        fnames = [f for f in os.listdir(ESTIMATES_DIR)
                                  if f.endswith('.json')]
                    except OSError:
                        fnames = []
                    for fname in sorted(fnames):
                        try:
                            with open(os.path.join(ESTIMATES_DIR, fname), 'r',
                                      encoding='utf-8') as f:
                                doc = json.load(f)
                            est_id = doc.get('estimate_id') or fname[:-5]
                            cur.execute('INSERT INTO estimates (id, doc) VALUES (%s, %s) '
                                        'ON CONFLICT (id) DO NOTHING', (est_id, _PgJson(doc)))
                            n += 1
                        except Exception as exc:
                            print(f'[db] skipped {fname} during migration: {exc}')
                    if n:
                        print(f'[db] migrated {n} estimates from {ESTIMATES_DIR} '
                              f'(JSON files left in place as cold backup)')
            finally:
                cur.execute('SELECT pg_advisory_unlock(727100001)')
        print('[db] Postgres estimate storage active')

    _db_init()


def _est_path(est_id):
    return os.path.join(ESTIMATES_DIR, f"{est_id}.json")


def est_load(est_id):
    """Return the estimate doc, or None when missing/unreadable."""
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT doc FROM estimates WHERE id = %s', (str(est_id),))
            row = cur.fetchone()
            return row[0] if row else None
    try:
        with open(_est_path(est_id), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def est_save(doc):
    """Persist an estimate doc keyed by doc['estimate_id']."""
    est_id = doc.get('estimate_id')
    if not est_id:
        raise ValueError('estimate doc missing estimate_id')
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO estimates (id, doc) VALUES (%s, %s) '
                        'ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, '
                        'updated_at = now()', (str(est_id), _PgJson(doc)))
        return
    with open(_est_path(est_id), 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)


def est_exists(est_id):
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM estimates WHERE id = %s', (str(est_id),))
            return cur.fetchone() is not None
    return os.path.exists(_est_path(est_id))


def est_delete(est_id):
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM estimates WHERE id = %s', (str(est_id),))
        return
    try:
        os.remove(_est_path(est_id))
    except OSError:
        pass


def est_ids():
    """All estimate ids, ascending."""
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM estimates ORDER BY id')
            return [r[0] for r in cur.fetchall()]
    try:
        return sorted(f[:-5] for f in os.listdir(ESTIMATES_DIR)
                      if f.endswith('.json'))
    except OSError:
        return []


def est_count():
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM estimates')
            return cur.fetchone()[0]
    return len(est_ids())


def est_iter(reverse=False):
    """Yield every readable estimate doc; unreadable files are skipped."""
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM estimates ORDER BY id {'DESC' if reverse else 'ASC'}")
            for row in cur.fetchall():
                yield row[0]
        return
    for est_id in sorted(est_ids(), reverse=reverse):
        doc = est_load(est_id)
        if doc is not None:
            yield doc


def est_find_by_token(token):
    """Return the estimate doc matching share_token, or None."""
    if not token:
        return None
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT doc FROM estimates WHERE doc->>'share_token' = %s "
                        'LIMIT 1', (str(token),))
            row = cur.fetchone()
            return row[0] if row else None
    for doc in est_iter():
        if doc.get('share_token') == token:
            return doc
    return None


def est_update(est_id, mutator):
    """Atomic read-modify-write. `mutator(doc_or_None)` returns the doc to
    store, or None to abort without writing. Returns the stored doc (or None).

    DB mode runs inside a SELECT ... FOR UPDATE transaction, so concurrent
    writers (2 gunicorn workers: sign POST vs rep save vs CRM write-back)
    serialize instead of losing updates. File mode is plain load-mutate-save —
    fine for single-user local dev."""
    if DATABASE_URL:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT doc FROM estimates WHERE id = %s FOR UPDATE',
                        (str(est_id),))
            row = cur.fetchone()
            doc = mutator(row[0] if row else None)
            if doc is None:
                return None
            cur.execute('INSERT INTO estimates (id, doc) VALUES (%s, %s) '
                        'ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, '
                        'updated_at = now()', (str(est_id), _PgJson(doc)))
            return doc
    doc = mutator(est_load(est_id))
    if doc is None:
        return None
    est_save(doc)
    return doc


# Contacts — refreshed every 5 minutes (was cached forever, so new CRM
# contacts never showed up in search until a server restart)
_contact_cache = {'data': None, 'fetched_at': 0}
CONTACT_CACHE_TTL = 300


def crm_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def fetch_all_contacts():
    now = time.time()
    if _contact_cache['data'] is not None and now - _contact_cache['fetched_at'] < CONTACT_CACHE_TTL:
        return _contact_cache['data']
    if http is None:
        return _contact_cache['data'] or []
    try:
        r = http.get(f"{BASE_URL}/entities/Contact", headers=crm_headers(), timeout=15)
        r.raise_for_status()
        all_contacts = r.json()
        _contact_cache['data'] = [c for c in all_contacts if c.get('location_id') == CO_LOCATION_ID]
        _contact_cache['fetched_at'] = now
    except Exception as e:
        print(f"[CRM] fetch failed: {e}")
        if _contact_cache['data'] is None:
            _contact_cache['data'] = []
    return _contact_cache['data']


# Projects (Jobs) — refreshed every 5 minutes since new jobs are created daily
_project_cache = {'data': None, 'fetched_at': 0}
PROJECT_CACHE_TTL = 300

def fetch_all_projects():
    now = time.time()
    if _project_cache['data'] is not None and now - _project_cache['fetched_at'] < PROJECT_CACHE_TTL:
        return _project_cache['data']
    if http is None:
        return _project_cache['data'] or []
    try:
        r = http.get(f"{BASE_URL}/entities/Project", headers=crm_headers(), timeout=20)
        r.raise_for_status()
        _project_cache['data'] = r.json()
        _project_cache['fetched_at'] = now
    except Exception as e:
        print(f"[CRM] project fetch failed: {e}")
        if _project_cache['data'] is None:
            _project_cache['data'] = []
    return _project_cache['data']


# ── Static ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


# ── Identity ───────────────────────────────────────────────────────────
# The login page and /logout moved to the portal (portal/app.py) when the three
# tools merged onto one origin — the estimator no longer renders its own sign-in
# form, and the TEAM_MEMBERS dropdown that gated it is gone with it (which also
# fixes reps who had a password here but were missing from that hardcoded list).
#
# The Team Logins panel stays, because a manager still needs it. Its routes now
# read and write portal.users instead of users.json, so there is exactly one
# user store even though two apps expose an editor for it.

@app.route('/api/me')
def me():
    user = session.get('user', '')
    rec  = pusers.get(user) if user else None
    return jsonify({
        'username': user,
        'display_name': _display_name(user) if user else '',
        'email': pusers.email_of(user) if user else '',
        'is_admin': bool(rec and rec['role'] == 'admin'),
        'role': rec['role'] if rec else 'rep',
        # True when an admin set a temporary password the user must replace.
        'must_change': bool(rec and rec['must_change']),
    })


@app.route('/api/users', methods=['GET'])
def list_users():
    """Admin-only: enrollment status for every team member."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    accounts = {u['username']: u for u in pusers.all_users()}
    locked = pthrottle.locked_usernames()
    return jsonify([
        {'username': m['username'],
         'display_name': m.get('display_name') or _display_name(m['username']),
         'phone':        m.get('phone', ''),
         'email':        m.get('email', ''),
         # An account in the portal store IS enrollment now; there is no
         # "invited but no pw_hash" half-state any more.
         'enrolled':     m['username'] in accounts,
         'must_change':  bool(accounts.get(m['username'], {}).get('must_change')),
         'is_admin':     _get_role(m['username']) == 'admin',
         'role':         _get_role(m['username']),
         # Seconds left on a failed-login lockout, 0 when not locked. Surfaced
         # here because this panel is where user trouble actually gets fixed.
         'locked':       locked.get(m['username'], 0)}
        for m in load_team()
    ])


@app.route('/api/users/<username>/unlock', methods=['POST'])
def unlock_user_account(username):
    """Admin-only: clear a rep's failed-login lockout.

    The throttle in portal/throttle.py locks an account for 15+ minutes after
    repeated wrong passwords. That is right for an attacker and wrong for a rep
    on a doorstep, so an admin needs to be able to release it from the panel
    they already use for passwords.
    """
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    pthrottle.unlock_user(username)
    return jsonify({'ok': True})


@app.route('/api/users/<username>/set-password', methods=['POST'])
def set_user_password(username):
    """Admin-only: set a one-time password for a team member. They are forced to
    choose their own password the next time they sign in (must_change)."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    if not any(m['username'] == username for m in load_team()):
        return jsonify({'error': 'unknown team member'}), 400
    password = (request.get_json(force=True) or {}).get('password') or ''
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
    if pusers.get(username):
        pusers.set_password(username, password, must_change=True)
    else:
        pusers.create(username, password=password, must_change=True,
                      full_name=_display_name(username))
    return jsonify({'ok': True, 'username': username})


@app.route('/api/users/<username>/reset', methods=['POST'])
def reset_user(username):
    """Admin-only: un-enroll a user so they can sign up fresh.

    Previously this dropped pw_hash and left the record behind. The portal
    store has no password-less state, so un-enrolling means removing the
    account; the rep re-enrols through the portal with the signup code.
    """
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    if username == session.get('user', ''):
        return jsonify({'error': "You can't reset your own login"}), 400
    pusers.delete(username)
    return jsonify({'ok': True, 'reset': username})


@app.route('/api/users/<username>/set-role', methods=['POST'])
def set_user_role(username):
    """Admin-only: assign a role (admin, manager, rep) to a team member."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    role = (request.get_json(force=True) or {}).get('role', '')
    if role not in pusers.ROLES:
        return jsonify({'error': 'role must be admin, manager, or rep'}), 400
    if not pusers.get(username):
        return jsonify({'error': 'that user has not enrolled yet'}), 400
    pusers.set_role(username, role)
    return jsonify({'ok': True, 'username': username, 'role': role})


@app.route('/api/team', methods=['POST'])
def add_team_member():
    """Admin-only: add a new team member."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    data         = request.get_json(force=True) or {}
    username     = (data.get('username') or '').strip().lower().replace(' ', '_')
    display_name = (data.get('display_name') or '').strip()
    phone        = (data.get('phone') or '').strip()
    email        = (data.get('email') or '').strip()
    role         = data.get('role', 'rep')
    if not username:
        return jsonify({'error': 'username required'}), 400
    if role not in ('admin', 'manager', 'rep'):
        role = 'rep'
    team = load_team()
    if any(m['username'] == username for m in team):
        return jsonify({'error': 'user already exists'}), 409
    team.append({'username': username, 'display_name': display_name or _display_name(username),
                 'phone': phone, 'email': email})
    save_team(team)
    # Adding someone to the roster records their intended role, but does not
    # create an account — they enrol themselves through the portal with an
    # invite, or an admin sets a temporary password via Team Logins.
    if pusers.get(username):
        pusers.set_role(username, role)
    return jsonify({'ok': True, 'username': username}), 201


@app.route('/api/team/<username>', methods=['PATCH'])
def edit_team_member(username):
    """Admin-only: edit a team member's display name or customer-facing
    contact override (phone/email shown on the /sign page's contact card)."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    team = load_team()
    m = next((t for t in team if t['username'] == username), None)
    if m is None:
        return jsonify({'error': 'unknown team member'}), 404
    data = request.get_json(force=True) or {}
    if 'display_name' in data:
        m['display_name'] = (data.get('display_name') or '').strip() or _display_name(username)
    if 'phone' in data:
        m['phone'] = (data.get('phone') or '').strip()
    if 'email' in data:
        m['email'] = (data.get('email') or '').strip()
    save_team(team)
    return jsonify({'ok': True, 'username': username, 'member': m})


@app.route('/api/team/<username>', methods=['DELETE'])
def remove_team_member(username):
    """Admin-only: remove a team member (cannot remove yourself)."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    if username == session.get('user'):
        return jsonify({'error': 'cannot remove yourself'}), 400
    team = [m for m in load_team() if m['username'] != username]
    save_team(team)
    # Removing them from the roster also removes their login, so they lose
    # access to all three tools rather than just disappearing from this one.
    pusers.delete(username)
    return jsonify({'ok': True, 'removed': username})


@app.route('/api/account/password', methods=['POST'])
def change_own_password():
    """Any signed-in user sets/replaces their own password."""
    user = session.get('user', '')
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    password = (request.get_json(force=True) or {}).get('password') or ''
    if len(password) < 8:
        return jsonify({'error': 'Choose a password of at least 8 characters.'}), 400
    if not pusers.get(user):
        return jsonify({'error': 'no account for this user'}), 400
    pusers.set_password(user, password, must_change=False)
    return jsonify({'ok': True})

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


@app.route('/manifest.json')
def pwa_manifest():
    resp = send_from_directory(app.static_folder, 'manifest.json')
    resp.headers['Content-Type'] = 'application/manifest+json'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(app.static_folder, 'sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ── Estimates CRUD ─────────────────────────────────────────────────────────

# `declined` was the old name for `lost`. It is still written on every estimate
# that reached that outcome before the rename, and those are real records on a
# live volume — so nothing rewrites them. `lost` is what gets written from now
# on, and everything that reads a status comes through here.
#
# Change orders keep `declined`, deliberately: a customer saying no to an
# add-on is not a lost job, and collapsing the two would lose that distinction.
LOST_STATUSES = ('lost', 'declined')


def _norm_est_status(status):
    """The canonical name for a stored estimate status."""
    return 'lost' if (status or '') in LOST_STATUSES else (status or 'draft')


def _is_lost(est):
    return (est.get('status') or '') in LOST_STATUSES


def _estimate_total(est):
    """Grand total for any estimate type (insurance sections-aware)."""
    if est.get('estimate_type') == 'insurance':
        ins_td   = est.get('trades', {}).get('insurance', {})
        sections = ins_td.get('sections') or (
            [{'items': ins_td.get('line_items', [])}] if ins_td.get('line_items') else [])
        # RCV (price) = ACV + Depreciation
        return sum(float(i.get('acv') or 0) + float(i.get('depreciation') or 0)
                   for sec in sections for i in sec.get('items', []))
    return calc_selected_total(est)


def _funnel_record(est, state, at=''):
    """Report an estimate's funnel state to the shared join. Never raises.

    This is what lets the CRM answer "of the doors we knocked, where do we
    lose people" — the estimator is the only system that knows an estimate was
    sent, opened or signed, and the CRM is the only one that knows which door
    it started at. Best-effort on purpose: a customer signing a contract must
    never fail because a reporting table was locked.
    """
    try:
        c = est.get('customer', {}) or {}
        pfunnel.record(
            est.get('estimate_id') or '',
            state,
            lead_id=c.get('crm_lead_id') or '',
            contact_id=c.get('crm_contact_id') or '',
            rep=(est.get('salesperson') or '').strip(),
            value=round(_estimate_total(est), 2),
            # Carried so the CRM can file the signed contract in The Den
            # without reaching back into the estimator: Base44 refuses file
            # uploads from our token (405), so that Document is a link to the
            # hosted signing page anyway, and the token is the whole link.
            share_token=est.get('share_token') or '',
            at=at or None)
    except Exception as exc:
        print(f'[funnel] {state} not recorded for '
              f'{est.get("estimate_id", "?")}: {exc}')


@app.route('/api/estimates', methods=['GET'])
def list_estimates():
    result = []
    # Reps only see their own (or unassigned) estimates — the list carries live
    # share tokens and totals, so server-side filtering matters, not just the UI.
    only_own = not _is_manager_up()
    user     = _current_user()
    for d in est_iter(reverse=True):
        # Ownership is decided BEFORE the summarize try/except: a doc that blows
        # up mid-summary must not fall through into the error row below and leak
        # its existence to a rep who isn't allowed to see it. The check itself
        # fails closed — an unreadable salesperson means "not yours", never a
        # raised exception that takes the whole dashboard down.
        if not isinstance(d, dict):
            print(f'[list] skipping non-object estimate document: {type(d).__name__}')
            continue
        sp = d.get('salesperson')
        if sp is None:
            owner = ''                      # unassigned — any rep may claim it
        elif isinstance(sp, str):
            owner = sp.strip()
        else:
            owner = None                    # unreadable — nobody owns it
        if only_own and owner not in ('', user):
            continue
        try:
            c = d.get('customer', {})
            a = c.get('address', {})
            sig = d.get('signature') or {}
            result.append({
                'estimate_id':     d.get('estimate_id', ''),
                # The join key to The Den. Empty on estimates created before
                # the link was captured, and on any started without going
                # through the CRM — treat empty as "not matchable", never as
                # a reason to fall back to name matching.
                'crm_contact_id':  c.get('crm_contact_id') or '',
                'customer_name':   c.get('name', ''),
                'city':            a.get('city', ''),
                'estimate_date':   d.get('estimate_date', ''),
                # Normalized, so the front end only ever sees one spelling and
                # a record written before the rename behaves like a new one.
                'status':          _norm_est_status(d.get('status')),
                'estimate_type':   d.get('estimate_type', 'retail'),
                'selected_tier':   d.get('selected_tier', 'better'),
                'salesperson':     d.get('salesperson', ''),
                'total':           round(_estimate_total(d), 2),
                'share_token':     d.get('share_token') or '',
                'sent':            bool(d.get('share_token')),
                'sent_at':         d.get('sent_at', ''),
                'first_viewed_at': d.get('first_viewed_at', ''),
                'last_viewed_at':  d.get('last_viewed_at', ''),
                'view_count':      int(d.get('view_count') or 0),
                'signed':          bool(d.get('signature')),
                'signed_at':       sig.get('signed_at', ''),
                'updated_at':      d.get('updated_at', ''),
                'estimate_label':  d.get('estimate_label', ''),
                'co_count':        len(d.get('change_orders') or []),
                'co_pending':      sum(1 for x in d.get('change_orders') or []
                                       if x.get('status') in ('draft', 'sent')),
                'co_total':        round(_accepted_co_total(d), 2),
            })
        except Exception as e:
            # A malformed estimate must never silently vanish from a rep's
            # dashboard — surface a minimal row they can still open, and say why.
            eid = d.get('estimate_id', '')
            print(f'[list] estimate {eid or "<unknown id>"} failed to summarize: {e!r}')
            if eid:
                result.append({
                    'estimate_id':   eid,
                    'customer_name': '⚠ Could not read this estimate',
                    'status':        d.get('status', 'draft'),
                    'total':         0,
                    'load_error':    str(e),
                })
    return jsonify(result)


@app.route('/api/estimates', methods=['POST'])
def create_estimate():
    data = request.get_json(force=True)
    est_id = data.get('estimate_id') or str(uuid.uuid4())
    data['estimate_id'] = est_id
    now = datetime.utcnow().isoformat() + 'Z'
    data.setdefault('created_at', now)
    data['updated_at'] = now
    est_save(data)
    return jsonify({'estimate_id': est_id}), 201


@app.route('/api/estimates/<est_id>', methods=['GET'])
def get_estimate(est_id):
    d = est_load(est_id)
    if d is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(d):
        return _forbid()
    return jsonify(d)


# Fields written by the server (sign page, share link, CRM push) that a stale
# client save must never wipe. If incoming value is missing/falsy but the file
# has one, keep the file's value.
SERVER_MANAGED_FIELDS = [
    'share_token', 'sent_at', 'first_viewed_at', 'last_viewed_at',
    'view_count', 'signature', 'crm_document_id', 'crm_pushed_at',
]

@app.route('/api/estimates/<est_id>', methods=['PUT'])
def save_estimate(est_id):
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    data = request.get_json(force=True)
    data['estimate_id'] = est_id
    data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    # Permission check outside the write lock (cheap read; verdict can't change)
    existing_pre = est_load(est_id)
    if existing_pre and not _can_touch_estimate(existing_pre):
        return _forbid()

    def _merge(existing):
        if existing:
            for field in SERVER_MANAGED_FIELDS:
                if not data.get(field) and existing.get(field):
                    data[field] = existing[field]
            # A signed estimate stays accepted even if a stale tab says draft
            if existing.get('signature') and data.get('status') in (None, 'draft', 'sent'):
                data['status'] = existing.get('status', 'accepted')
            # Server-generated attachments (production packet) must survive a
            # save from a tab opened before they existed. The UI offers no
            # delete for them — regenerate replaces — so resurrecting is right.
            incoming_ids = {x.get('id') for x in (data.get('attachments') or [])}
            for x in (existing.get('attachments') or []):
                if x.get('server_generated') and x.get('id') not in incoming_ids:
                    data.setdefault('attachments', []).append(x)
        # Change orders are server-authoritative (signed legal documents managed
        # through their own endpoints) — a full-doc save can never alter them.
        data.pop('change_orders', None)
        if existing and existing.get('change_orders'):
            data['change_orders'] = existing['change_orders']
        # Paid inference throttling is server-owned, even on a full-doc save.
        data.pop('_visualizer_detection_attempts', None)
        if existing and existing.get('_visualizer_detection_attempts'):
            data['_visualizer_detection_attempts'] = existing['_visualizer_detection_attempts']
        return data

    est_update(est_id, _merge)
    return jsonify({'estimate_id': est_id})


@app.route('/api/estimates/<est_id>/duplicate', methods=['POST'])
def duplicate_estimate(est_id):
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    new_id = str(uuid.uuid4())
    est['estimate_id'] = new_id
    est['status'] = 'draft'
    est['share_token'] = None
    est['signature'] = None
    est['sent_at'] = None
    est['first_viewed_at'] = None
    est['last_viewed_at'] = None
    est['view_count'] = 0
    est.pop('change_orders', None)   # signed legal docs — never copied
    est['created_at'] = datetime.utcnow().isoformat() + 'Z'
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    # The copy stays in the SAME customer's file. This used to rename the
    # customer to "Copy of Jon Smith", which moved the duplicate into a
    # customer of its own — the one thing a duplicate must never do, since
    # duplicating is how a rep builds the second estimate for someone. The
    # "Copy of" marker belongs on estimate_label, which exists precisely to
    # tell one customer's estimates apart.
    label = (est.get('estimate_label') or '').strip()
    if not label.startswith('Copy of '):
        est['estimate_label'] = ('Copy of ' + label) if label else 'Copy'
    est_save(est)
    src_dir = os.path.join(UPLOADS_DIR, est_id)
    if os.path.exists(src_dir):
        shutil.copytree(src_dir, os.path.join(UPLOADS_DIR, new_id))
    return jsonify({'estimate_id': new_id})


@app.route('/api/estimates/<est_id>', methods=['DELETE'])
def delete_estimate(est_id):
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    if est_exists(est_id):
        est = est_load(est_id)
        # Unreadable file: only managers/admins may force-delete it
        if (est is not None and not _can_touch_estimate(est)) or \
           (est is None and not _is_manager_up()):
            return _forbid()
        est_delete(est_id)
    upload_dir = os.path.join(UPLOADS_DIR, est_id)
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir)
    return jsonify({'ok': True})


def _cust_key(name):
    """The customer-grouping key. Mirrors `custKey()` in static/app.js — the
    browser decides which estimates share a customer file, and these notes have
    to land on the same customer it picked. Lowercase, trimmed, and internal
    runs of whitespace collapsed, so "Jon  Smith" typed on a phone keyboard is
    not a second person with his own notes."""
    return ' '.join((name or '').lower().split())


def _read_customer_notes():
    try:
        if os.path.exists(CUSTOMER_NOTES_FILE):
            with open(CUSTOMER_NOTES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


@app.route('/api/customer-notes/<path:name>', methods=['GET'])
def get_customer_notes(name):
    """Return the customer-level notes string for a given customer name."""
    notes = _read_customer_notes()
    key = _cust_key(name)
    if key in notes:
        return jsonify({'notes': notes[key]})
    # Notes written before the key collapsed internal whitespace are still on
    # the volume under the old spelling. Read through to them rather than
    # migrating: these are real notes about real customers, and the read path
    # is the cheap place to be forgiving.
    return jsonify({'notes': notes.get((name or '').lower().strip(), '')})


@app.route('/api/customer-notes/<path:name>', methods=['PUT'])
def set_customer_notes(name):
    """Persist customer-level notes for a given customer name."""
    text = (request.get_json(force=True) or {}).get('notes', '')
    notes = _read_customer_notes()
    key = _cust_key(name)
    notes[key] = text
    # A save adopts the canonical key, so drop the legacy spelling rather than
    # leaving a stale copy that a future read could pick up instead.
    legacy = (name or '').lower().strip()
    if legacy != key:
        notes.pop(legacy, None)
    with open(CUSTOMER_NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(notes, f, indent=2)
    return jsonify({'ok': True})


@app.route('/api/estimates/<est_id>/label', methods=['PATCH'])
def update_estimate_label(est_id):
    """Quick-patch just the estimate_label field without a full save."""
    label = (request.get_json(force=True) or {}).get('label', '')
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    est['estimate_label'] = label
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    est_save(est)
    return jsonify({'ok': True, 'label': label})


@app.route('/api/estimates/<est_id>/status', methods=['PATCH'])
def update_estimate_status(est_id):
    VALID = {'draft', 'sent', 'accepted', 'lost', 'declined'}
    status = (request.json or {}).get('status')
    if status not in VALID:
        return jsonify({'error': 'Invalid status'}), 400
    status = _norm_est_status(status)          # `declined` in, `lost` stored
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    # A signature is a fact about what the customer did, not a status a rep
    # can retract. Reopening a signed job is a change order, not an edit here.
    if est.get('signature') and status != 'accepted':
        return jsonify({'error': 'A signed estimate cannot change status.'}), 400
    est['status'] = status
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    est_save(est)
    # Tell the CRM. Marking an estimate lost does NOT lose the lead — plenty
    # get re-quoted, and losing it would close the tasks that win it back — but
    # the pipeline should say so on the timeline.
    if status == 'lost':
        _funnel_record(est, 'lost')
    return jsonify({'ok': True, 'status': status})


# ── Photo uploads ──────────────────────────────────────────────────────────

_PDF_PAGE_CAP = 20  # defensive limit on rasterized pages per attachment


def _rasterize_pdf_pages(est_id, filename):
    """Render every page of an uploaded PDF to <stem>_pN.jpg alongside it and
    return the page filenames ('<est_id>/<stem>_pN.jpg', page order). Cached:
    if the page images already exist on disk they are reused. Returns [] on
    any failure (missing/corrupt/encrypted PDF, pymupdf unavailable) — the
    callers fall back to a plain link."""
    if not _safe_path_id(est_id) or not _safe_path_id(filename):
        return []
    pdf_path = os.path.join(UPLOADS_DIR, est_id, filename)
    if not os.path.exists(pdf_path) or not filename.lower().endswith('.pdf'):
        return []
    stem = os.path.splitext(filename)[0]
    dest_dir = os.path.join(UPLOADS_DIR, est_id)

    # Disk cache: reuse existing page images, sorted by integer page number
    # (lexicographic would put _p10 before _p2).
    existing = [fn for fn in os.listdir(dest_dir)
                if re.fullmatch(re.escape(stem) + r'_p(\d+)\.jpg', fn)]
    if existing:
        existing.sort(key=lambda fn: int(re.search(r'_p(\d+)\.jpg$', fn).group(1)))
        return [f"{est_id}/{fn}" for fn in existing]

    try:
        import fitz  # pymupdf — lazy so the app still boots without it
    except ImportError:
        return []
    pages = []
    try:
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            if i >= _PDF_PAGE_CAP:
                break
            try:
                zoom = min(4, max(1, 1200 / max(page.rect.width, 1)))
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                page_name = f"{stem}_p{i + 1}.jpg"
                pix.save(os.path.join(dest_dir, page_name), jpg_quality=85)
                pages.append(f"{est_id}/{page_name}")
            except Exception as exc:
                print(f'[pdf-pages] page {i + 1} of {filename} failed: {exc}')
        doc.close()
    except Exception as exc:
        print(f'[pdf-pages] could not rasterize {filename}: {exc}')
        return []
    return pages


@app.route('/api/uploads/<est_id>', methods=['POST'])
def upload_photo(est_id):
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is not None and not _can_touch_estimate(est):
        return _forbid()
    if 'file' not in request.files:
        return jsonify({'error': 'No file field'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({'error': 'File type not allowed'}), 400
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = str(uuid.uuid4()) + ext
    f.save(os.path.join(dest_dir, safe_name))
    resp = {'filename': f"{est_id}/{safe_name}", 'url': f"/uploads/{est_id}/{safe_name}"}
    if ext == '.pdf':
        # Page images let attachments render as full documents in the
        # customer view and the printed estimate instead of a bare link.
        resp['pages'] = _rasterize_pdf_pages(est_id, safe_name)
    return jsonify(resp), 201


@app.route('/api/pdf-pages/<est_id>/<filename>')
def pdf_pages(est_id, filename):
    """Page images for an already-uploaded PDF — rasterizes (and caches) on
    first request. Lets estimates with attachments from before page rendering
    existed backfill their `pages` list."""
    if not _safe_path_id(est_id) or not _safe_path_id(filename):
        return jsonify({'error': 'invalid path'}), 400
    est = est_load(est_id)
    if est is not None and not _can_touch_estimate(est):
        return _forbid()
    return jsonify({'pages': _rasterize_pdf_pages(est_id, filename)})


@app.route('/api/uploads/<est_id>/<filename>', methods=['DELETE'])
def delete_photo(est_id, filename):
    if not _safe_path_id(est_id) or not _safe_path_id(filename):
        return jsonify({'error': 'invalid path'}), 400
    est = est_load(est_id)
    if est is not None and not _can_touch_estimate(est):
        return _forbid()
    path = os.path.join(UPLOADS_DIR, est_id, filename)
    if os.path.exists(path):
        os.remove(path)
    # A deleted PDF also takes its rasterized page images with it
    if filename.lower().endswith('.pdf'):
        stem = os.path.splitext(filename)[0]
        dest_dir = os.path.join(UPLOADS_DIR, est_id)
        if os.path.isdir(dest_dir):
            for fn in os.listdir(dest_dir):
                if re.fullmatch(re.escape(stem) + r'_p\d+\.jpg', fn):
                    try:
                        os.remove(os.path.join(dest_dir, fn))
                    except OSError:
                        pass
    return jsonify({'ok': True})


# ── Visualizer ────────────────────────────────────────────────────────────
# The Visualizer tab lets the rep upload a photo of the house, paint roof,
# siding, and door masks, then produce a Good/Better/Best rendering with colors picked
# from the actual estimate bundles. State lives entirely under `est.visualizer`
# — a top-level key the server does not whitelist, so it round-trips through
# the normal PUT unchanged (see SERVER_MANAGED_FIELDS and _merge).
#
# Two endpoints:
#  - POST .../visualizer/asset stores an image blob (base image, mask, or tier
#    render). Base64 in JSON because canvas.toDataURL is base64 and it saves
#    the frontend a form-encode step. Writes to the same UPLOADS_DIR as the
#    normal upload path, so files serve at /uploads/<est_id>/<name>.
#  - PUT .../visualizer/state updates the JSON selections/timestamp atomically
#    without loading + re-saving the whole estimate.
#
# Auth mirrors POST /api/uploads/<est_id>: _can_touch_estimate on an existing
# estimate, unauthenticated allowed when the estimate is missing (mirrors how
# upload_photo handles new-estimate uploads).

_VISUALIZER_MAX_BYTES = 12 * 1024 * 1024   # 12 MB — comfortably above a
# camera photo but well under the 30 MB global request cap, so a bad client
# can't fill the disk with one call.


@app.route('/api/estimates/<est_id>/visualizer/asset', methods=['POST'])
def visualizer_asset(est_id):
    """Store a Visualizer image blob and update the pointer field in
    est.visualizer. Body: JSON {kind, tier?, role?, content_b64, ext}.

    kind='base'   -> visualizer.base_image
    kind='mask'   -> visualizer.roof_mask | siding_mask | door_mask (role required)
    kind='render' -> visualizer.tier_renders[tier] (tier required)
    """
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is not None and not _can_touch_estimate(est):
        return _forbid()

    body = request.get_json(silent=True) or {}
    kind = (body.get('kind') or '').strip()
    ext  = (body.get('ext') or '').strip().lower().lstrip('.')
    b64  = body.get('content_b64') or ''
    tier = (body.get('tier') or '').strip()
    role = (body.get('role') or '').strip()

    if kind not in ('base', 'mask', 'render'):
        return jsonify({'error': 'invalid kind'}), 400
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'error': 'invalid ext'}), 400
    if kind == 'render' and tier not in ('good', 'better', 'best'):
        return jsonify({'error': 'render requires tier'}), 400
    if kind == 'mask' and role not in ('roof', 'siding', 'door'):
        return jsonify({'error': 'mask requires role'}), 400

    import base64
    # Strip a data-URI prefix if the client sent one — cheaper here than
    # asking every caller to slice it off. Everything after the first comma
    # is the payload.
    if ',' in b64 and b64.startswith('data:'):
        b64 = b64.split(',', 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except Exception:
        return jsonify({'error': 'invalid base64 payload'}), 400
    if not data:
        return jsonify({'error': 'empty payload'}), 400
    if len(data) > _VISUALIZER_MAX_BYTES:
        return jsonify({'error': 'payload too large'}), 400

    if ext == 'jpeg':
        ext = 'jpg'
    prefix = {'base': 'vb', 'mask': 'vm', 'render': 'vr'}[kind]
    safe_name = f'{prefix}_{uuid.uuid4().hex}.{ext}'
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, safe_name), 'wb') as f:
        f.write(data)
    stored_ref = f'{est_id}/{safe_name}'
    url = f'/uploads/{est_id}/{safe_name}'

    def mutate(doc):
        if doc is None:
            # No estimate to attach to (new-draft flow). Return the URL so
            # the frontend can hold the reference and PUT it once the
            # estimate exists — mirrors upload_photo's no-est allowance.
            return None
        vz = doc.setdefault('visualizer', {})
        if kind == 'base':
            vz['base_image'] = stored_ref
            for field in ('roof_mask', 'siding_mask', 'door_mask'):
                vz.pop(field, None)
            vz['tier_renders'] = {}
        elif kind == 'mask':
            vz[f'{role}_mask'] = stored_ref
        else:
            vz.setdefault('tier_renders', {})[tier] = stored_ref
        vz['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        return doc

    est_update(est_id, mutate)
    return jsonify({'filename': stored_ref, 'url': url, 'kind': kind,
                    'tier': tier or None, 'role': role or None}), 201


@app.route('/api/estimates/<est_id>/visualizer/state', methods=['PUT'])
def visualizer_state(est_id):
    """Update the JSON portion of the Visualizer state — selections, notes —
    without a full-estimate PUT round-trip. Blob pointers stay set by
    visualizer_asset and are not overwritten here."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is not None and not _can_touch_estimate(est):
        return _forbid()
    body = request.get_json(silent=True) or {}
    selections = body.get('selections')
    if selections is not None and not isinstance(selections, dict):
        return jsonify({'error': 'selections must be an object'}), 400

    def mutate(doc):
        if doc is None:
            return None
        vz = doc.setdefault('visualizer', {})
        if selections is not None:
            vz['selections'] = selections
        vz['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        return doc

    doc = est_update(est_id, mutate)
    if doc is None:
        return jsonify({'error': 'estimate not found'}), 404
    return jsonify({'visualizer': doc.get('visualizer', {})})


# ── Optional automatic exterior surface selection ──────────────────────────

@app.route('/api/visualizer/capabilities')
def visualizer_capabilities():
    from estimator import exterior_detection as detection
    return jsonify({'auto_detect': detection.configured(), 'provider': 'fal / SAM 3'})


@app.route('/api/estimates/<est_id>/visualizer/detection', methods=['POST', 'GET'])
def visualizer_detection(est_id):
    """Submit/poll one surface without tying up a web worker during inference.

    Tickets expire after ten minutes and bind the upstream job to the current
    user, estimate, and photo. They carry no credential. This endpoint never
    publishes the results to the estimate: the rep reviews and saves them.
    """
    from itsdangerous import URLSafeTimedSerializer, BadData
    from estimator import exterior_detection as detection
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Save the estimate before detecting surfaces.'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    if not detection.configured():
        return jsonify({'error': 'Automatic selection needs setup: enable EXTERIOR_AUTO_DETECT and configure FAL_KEY on the server.'}), 503
    signer = URLSafeTimedSerializer(app.secret_key, salt='exterior-detection-v1')
    try:
        if request.method == 'GET':
            try:
                ticket = signer.loads(request.args.get('ticket', ''), max_age=600)
            except BadData:
                return jsonify({'error': 'Detection session expired or is invalid.'}), 400
            if ticket.get('estimate_id') != est_id or ticket.get('user') != _current_user():
                return _forbid()
            result = detection.poll(ticket['job'])
            result.update(role=ticket['role'], photo_key=ticket['photo_key'])
            return jsonify(result)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('role') not in detection.PROMPTS:
            return jsonify({'error': 'Choose roof, siding, or door.'}), 400
        role = body['role']
        photo_key = body.get('photo_key')
        if not isinstance(photo_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', photo_key):
            return jsonify({'error': 'Invalid photo identifier.'}), 400
        # Validate before consuming a submission slot or contacting fal.
        detection.decode_image(body.get('image'))
        limited = False
        now = time.time()

        def reserve(doc):
            nonlocal limited
            if doc is None:
                limited = True
                return None
            attempts = doc.setdefault('_visualizer_detection_attempts', {})
            if now - attempts.get(role, 0) < 30:
                limited = True
            else:
                attempts[role] = now
            return doc

        est_update(est_id, reserve)
        if limited:
            return jsonify({'error': 'Please wait 30 seconds before detecting this surface again.'}), 429
        job = detection.submit(role, body['image'])
        ticket = signer.dumps({'estimate_id': est_id, 'user': _current_user(),
                               'role': role, 'photo_key': photo_key, 'job': job})
        return jsonify({'ticket': ticket, 'status': 'pending'}), 202
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except detection.DetectionError as exc:
        return jsonify({'error': str(exc)}), 502


# ── RoofR PDF import ───────────────────────────────────────────────────────

def _parse_roofr_lf(s):
    """Convert RoofR linear-foot string ('358ft 4in') to decimal feet."""
    m = re.match(r'(\d+)ft\s+(\d+)in', s.strip())
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 12, 2)
    m = re.match(r'(\d[\d,]*)ft', s.replace(',', ''))
    return float(m.group(1)) if m else 0.0

def _parse_roofr_pitches(full_text):
    """Extract (rise, area_sqft) pairs from RoofR's pitch table.

    Real RoofR exports print a columnar block, not a row per pitch:
        Pitch 0/12 2/12 4/12
        Area (sqft) 514 106 4,675
        Squares 5.2 1.1 46.8
    Multi-structure properties (garage, shed, addition) repeat this block once
    per structure, then once more for the whole-property 'Report summary' —
    the LAST such block in the document is that total, so it wins. Flat
    facets are already folded into the low-rise buckets here (0/12 area plus
    2/12 area lines up with the report's separately-stated flat-area total,
    within rounding) — nothing needs adding back on top.

    Falls back to an older row-per-pitch layout ('4/12  1,551.7 ft²  79.6%')
    if the columnar table isn't found, in case some export differs."""
    table_re = re.compile(
        r'^\s*Pitch\b\s+((?:\d{1,2}\s*/\s*12\s*)+)$\s*'
        r'^\s*Area\s*\(\s*sqft\s*\)\s*([\d,.\s]+?)\s*$',
        re.I | re.M)
    matches = list(table_re.finditer(full_text))
    if matches:
        m     = matches[-1]
        rises = [int(r) for r in re.findall(r'(\d{1,2})\s*/\s*12', m.group(1))]
        areas = [float(a.replace(',', '')) for a in re.findall(r'[\d,]+(?:\.\d+)?', m.group(2))]
        if rises and len(rises) == len(areas):
            return list(zip(rises, areas))

    # Fallback: older assumed row-per-pitch layout (unverified against a real
    # export, kept in case some report template differs from the above).
    head = re.search(r'areas?\s*(?:per|by)\s*pitch|pitch\s*breakdown', full_text, re.I)
    if not head:
        return []
    window = full_text[head.end():head.end() + 2500]

    total_m = re.search(r'total\s+roof\s+area\D{0,10}([\d,]+(?:\.\d+)?)', full_text, re.I)
    total_sqft = float(total_m.group(1).replace(',', '')) if total_m else 0.0

    # Row format: "4/12  1,551.7 ft²  79.6%" — pitch, then the first number,
    # then optionally a unit or %. A % means the row is percentage-only and the
    # area resolves against the roof total. First value per pitch wins (the
    # same pitch can echo later in the window). RoofR sometimes labels a
    # dead-flat section as the word "Flat" instead of "0/12" — treated as
    # pitch 0 so it still counts toward low-slope/rolled roofing.
    def _row_area(raw, unit):
        num = float(raw.replace(',', ''))
        if unit == '%':
            return (total_sqft * num / 100) if total_sqft else None
        # Guard against pitch-diagram label runs ('6/12 8/12 4/12'): a bare
        # small integer with no unit is the next facet's rise, not an area.
        if not unit and num <= 12 and '.' not in raw and ',' not in raw:
            return None
        return num

    pitches = {}
    for m in re.finditer(
            r'(\d{1,2})\s*/\s*12\s*[:\-]?\s*([\d,]+(?:\.\d+)?)\s*(%|sq\.?\s*ft|sqft|ft2|ft²|sf)?',
            window, re.I):
        area = _row_area(m.group(2), (m.group(3) or '').strip().lower())
        if area is not None:
            pitches.setdefault(int(m.group(1)), area)
    for m in re.finditer(
            r'\bflat\b\s*[:\-]?\s*([\d,]+(?:\.\d+)?)\s*(%|sq\.?\s*ft|sqft|ft2|ft²|sf)?',
            window, re.I):
        area = _row_area(m.group(1), (m.group(2) or '').strip().lower())
        if area is not None:
            pitches.setdefault(0, area)
    return list(pitches.items())


def _parse_roofr_pdf(file_bytes):
    if _pypdf is None:
        raise RuntimeError('pypdf not installed')
    reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
    full_text = '\n'.join(p.extract_text() or '' for p in reader.pages)

    # Multi-structure properties (garage, shed, addition) repeat every
    # "Total X" measurement once per structure, THEN once more under a final
    # "Report summary" with the whole-property totals. A plain re.search
    # (first match) would silently grab Structure #1's subset instead of the
    # total — verified against a real 3-structure RoofR export where that
    # gave eaves 273'9" instead of the correct 421'0". Scoping every
    # measurement regex below to text from the first "Report summary" onward
    # fixes this; falls back to the full document when there's no such
    # section (a single-structure report may go straight to one summary).
    rs_matches  = list(re.finditer(r'report\s+summary', full_text, re.I))
    search_text = full_text[rs_matches[0].start():] if rs_matches else full_text

    def find_lf(label):
        # [\s:]+ handles both "Label 358ft 4in" and "Label: 358ft 4in" formats
        m = re.search(rf'{re.escape(label)}[\s:]+(\d+ft\s+\d+in)', search_text)
        return _parse_roofr_lf(m.group(1)) if m else None

    # Prefer the whole-property "Total roof area <N> sqft" over a bare
    # "Squares X" label — the latter can match a per-pitch breakdown row
    # ("Squares 5.2 1.1 46.8") and grab only the first bucket instead of the
    # total (also verified against the same real multi-structure export).
    area_m = re.search(r'total\s+roof\s+area\D{0,10}([\d,]+(?:\.\d+)?)', search_text, re.I)
    if area_m:
        squares = round(float(area_m.group(1).replace(',', '')) / 100, 1)
    else:
        sq = re.search(r'Squares[\s:]+([\d.]+)', search_text)
        squares = float(sq.group(1)) if sq else None

    # "Hips + ridges" is the precomputed combined value in the Report Summary
    ridge_hip_m = re.search(r'Hips\s*\+\s*ridges[\s:]+(\d+ft\s+\d+in)', search_text)
    ridge_hip = _parse_roofr_lf(ridge_hip_m.group(1)) if ridge_hip_m else None
    if ridge_hip is None:
        h = find_lf('Total hips') or 0
        r = find_lf('Total ridges') or 0
        ridge_hip = round(h + r, 2) or None

    # Ridges ALONE (excludes hips) — ridge vent installs on horizontal ridges,
    # so this is what we order the full-ridge stick count against. Separate from
    # the combined ridge+hip above, which still drives the ridge+hip line item.
    ridge_only = find_lf('Total ridges')

    eave   = find_lf('Total eaves')
    valley = find_lf('Total valleys')
    rake   = find_lf('Total rakes')
    step   = find_lf('Total step flashing')

    # Pitch breakdown → low-slope (≤2/12, rolled roofing) and steep (≥7/12,
    # steep charge) areas in squares. Set explicitly (even 0) whenever the
    # report has a pitch table so a re-import clears stale values; omitted
    # entirely when it doesn't, leaving manual entries alone.
    low_slope_sq = steep_sq = None
    pitch_rows = _parse_roofr_pitches(search_text)
    if pitch_rows:
        low_slope_sq = round(sum(a for p, a in pitch_rows if p <= 2) / 100, 1)
        steep_sq     = round(sum(a for p, a in pitch_rows if p >= 7) / 100, 1)

    # RoofR's cover page places "Predominant pitch N/12" directly adjacent to
    # the address with no separator in the extracted text (e.g. "...pitch
    # 4/125416 South Timberline Road..."), which glues the pitch digits onto
    # the street number. Strip it before matching so the address starts clean.
    # Capture the pitch on the way through so the work order has it.
    predom_pitch = None
    pp_m = re.search(r'predominant\s+pitch\s*(\d{1,2})\s*/\s*12', full_text, flags=re.I)
    if pp_m:
        try:
            predom_pitch = int(pp_m.group(1))
        except ValueError:
            predom_pitch = None
    # Fallback: predominant pitch = the pitch with the most area, when RoofR
    # gives us the per-pitch table but no explicit label.
    if predom_pitch is None and pitch_rows:
        predom_pitch = max(pitch_rows, key=lambda pa: pa[1])[0]
    addr_search_text = re.sub(r'predominant\s+pitch\s*\d{1,2}\s*/\s*12', '', full_text, flags=re.I)
    addr_m = re.search(
        r'(\d+\s+[^\n,]+),\s+([A-Za-z][A-Za-z\s]+),\s+([A-Z]{2})\s+(\d{5})',
        addr_search_text)

    meas = {k: v for k, v in {
        'roof_squares':  squares,
        'waste_pct':     10,
        'ridge_hip_lf':  ridge_hip,
        'ridge_lf':      ridge_only,
        'eave_lf':       eave,
        'valley_lf':     valley,
        'rake_lf':       rake,
        'step_flash_lf': step,
        'gutter_lf':     eave,
        'low_slope_squares': low_slope_sq,
        'steep_squares':     steep_sq,
        'predominant_pitch': predom_pitch,
    }.items() if v is not None}

    addr = {}
    if addr_m:
        addr = {
            'street': addr_m.group(1).strip(),
            'city':   addr_m.group(2).strip(),
            'state':  addr_m.group(3),
            'zip':    addr_m.group(4),
        }

    return {'measurements': meas, 'address': addr}

@app.route('/api/parse-roofr', methods=['POST'])
def parse_roofr():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file.'}), 400
    try:
        data = _parse_roofr_pdf(f.read())
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {e}'}), 400
    if not data['measurements'].get('roof_squares'):
        return jsonify({'error': "Couldn’t find RoofR measurements in this PDF. Make sure it’s a RoofR report."}), 422
    return jsonify(data)


# ── Xactimate (insurance carrier estimate) PDF import ──────────────────────
# Parses a carrier's Xactimate estimate export (e.g. Allstate) into sections
# of line items carrying ACV/depreciation, plus claim metadata and the claim
# summary (deductible, net claim, recoverable depreciation). Parse-only: the
# endpoint persists nothing; the browser review modal decides what to apply.

def _xact_num(s):
    return float(str(s).replace(',', '').replace('$', ''))

# Numeric tail of a line item:
#   QTY UNIT PRICE RCV AGE/LIFE COND DEP%[ [M]] (DEPREC) ACV
# Verified shapes: "28.74 SQ 75.56 2,171.59 23/30 yrs Avg. NA (0.00) 2,171.59",
# "... 76.67% (7,397.88) ...", "... 90% [M] (97.65) ...", "0/NA Avg.",
# "Abv. Avg.", and guide-style glued qty+unit ("685.47SF").
_XACT_ITEM_RE = re.compile(
    r'^(?P<no>\d{1,3})\.\s+(?P<desc>.+?)\s+'
    r'(?P<qty>[\d,]+\.\d{2})\s*(?P<unit>[A-Z]{2,3})\s+'
    r'(?P<price>[\d,]+\.\d{2})\s+(?P<rcv>[\d,]+\.\d{2})\s+'
    r'(?P<age>\d+/(?:\d+|NA))\s*(?:yrs)?\s+'
    r'(?P<cond>New|Avg\.|Abv\.\s*Avg\.|Bel\.\s*Avg\.)\s+'
    r'(?P<dep_pct>NA|<?[\d.]+\s*%)\s*(?:\[[A-Z%]\])?\s*'
    r'\((?P<deprec>[\d,]+\.\d{2})\)\s+(?P<acv>[\d,]+\.\d{2})\s*$')

_XACT_ITEM_START_RE = re.compile(r'^\d{1,3}\.\s+\S')
_XACT_HEADER_RE     = re.compile(r'^DESCRIPTION\s+QUANTITY\s+UNIT\s+RCV', re.I)
_XACT_TOTALS_RE     = re.compile(
    r'^Totals?:\s+(?P<name>.+?)\s+(?P<rcv>[\d,]+\.\d{2})\s+'
    r'(?P<dep>[\d,]+\.\d{2})\s+(?P<acv>[\d,]+\.\d{2})\s*$')
_XACT_GRAND_RE      = re.compile(
    r'Line Item Totals:\s*\S+\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})')
_XACT_NOISE_RES = [re.compile(p, re.I) for p in (
    r'^Options?:', r'^Auto Calculated Waste', r'^This line item include',
    r'^The above line item', r'^Bundle Rounding', r'^CONTINUED\s*-',
    r'^Page:\s*\d+\s*$', r'Page:\s*\d+\s*$',
    r'^\d+\s*$', r'^P\.?O\.? Box', r'^Fax:', r'^www\.', r'^Exposure\b',
    r'^[A-Za-z .]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*$',  # carrier address line
)]

# Per-coverage summary labels, summed across every coverage block (AA-Dwelling
# + BB-Other Structures + ... adds up to the whole-claim figure; verified the
# sums match the "Recap by Category" totals on a real Allstate export).
_XACT_SUMMARY_LABELS = {
    'line_item_total':        r'^Line Item Total\s',
    'material_sales_tax':     r'^Material Sales Tax\s',
    'rcv_total':              r'^Replacement Cost Value\s',
    'depreciation_total':     r'^Less Depreciation\s',
    'acv_total':              r'^Actual Cash Value\s',
    'deductible':             r'^Less Deductible\s',
    'net_claim':              r'^Net Claim\s+\$?\(?[\d,]',
    'recoverable_depreciation': r'^Total Recoverable Depreciation\s',
    'net_claim_if_recovered': r'^Net Claim if Depreciation is Recovered\s',
}


def _xact_is_noise(line, extra_noise=()):
    if line in extra_noise:
        return True
    return any(rx.search(line) for rx in _XACT_NOISE_RES)


def _parse_xactimate_items(lines, extra_noise=()):
    """State machine over the document's lines → flat [(line_no, section, item)]."""
    out = []
    current_section = None
    recent = []          # raw non-item lines, for section-name lookback
    pending = None       # buffered numbered line whose numeric tail wrapped
    pending_count = 0
    open_item = None     # last emitted item, may take ONE description continuation

    def emit(m):
        item = {
            'line_no':     int(m.group('no')),
            'description': re.sub(r'\s+', ' ', m.group('desc')).strip(),
            'qty':         _xact_num(m.group('qty')),
            'unit':        m.group('unit'),
            'unit_price':  _xact_num(m.group('price')),
            'rcv':         _xact_num(m.group('rcv')),
            'age_life':    m.group('age'),
            'dep_pct':     m.group('dep_pct').replace(' ', ''),
            'depreciation': _xact_num(m.group('deprec')),
            'acv':         _xact_num(m.group('acv')),
        }
        out.append([item['line_no'], current_section, item])

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        cont = re.match(r'^CONTINUED\s*-\s*(.+?)(?:\s{2,}|$)', line, re.I)
        if cont:
            current_section = cont.group(1).strip()
            open_item = pending = None
            continue

        if _XACT_HEADER_RE.search(line):
            # Section name = nearest preceding line that isn't noise, isn't a
            # measurement row ("270.38 Total Perimeter Length" starts with a
            # digit), and isn't itself an item.
            for prev in reversed(recent):
                if (prev and not prev[0].isdigit()
                        and not _xact_is_noise(prev, extra_noise)
                        and not _XACT_ITEM_START_RE.match(prev)
                        and not _XACT_TOTALS_RE.match(prev)):
                    current_section = prev
                    break
            recent = []
            open_item = pending = None
            continue

        tm = _XACT_TOTALS_RE.match(line)
        if tm:
            open_item = pending = None
            recent.append(line)
            continue

        if _xact_is_noise(line, extra_noise):
            open_item = pending = None
            continue

        m = _XACT_ITEM_RE.match(line)
        if m:
            emit(m)
            open_item = out[-1][2]
            pending = None
            continue

        if _XACT_ITEM_START_RE.match(line):
            pending, pending_count, open_item = line, 1, None
            continue

        if pending is not None:
            joined = pending + ' ' + line
            m = _XACT_ITEM_RE.match(joined)
            if m:
                emit(m)
                open_item = out[-1][2]
                pending = None
            else:
                pending_count += 1
                pending = joined if pending_count < 4 else None
            continue

        # One short free-text continuation extends the previous item's
        # description ("shingle rfg. - w/ felt", '5"', "Metal"). Accept only
        # when it reads like a fragment (starts lowercase/dash/digit) or the
        # description visibly dangles (trailing '-'); this keeps Xactimate
        # category labels like "Components" from gluing on.
        if (open_item is not None and len(line) <= 45
                and (not line[0].isupper()
                     or open_item['description'].rstrip().endswith('-'))):
            open_item['description'] = (open_item['description'] + ' ' + line).strip()
            open_item = None
            continue

        open_item = None
        recent.append(line)
        if len(recent) > 8:
            recent.pop(0)

    return out


def _parse_xactimate_pdf(file_bytes):
    if _pypdf is None:
        raise RuntimeError('pypdf not installed')
    reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
    pages = [p.extract_text() or '' for p in reader.pages]

    # Carrier exports interleave static instructional pages ("Your guide to
    # reading your adjuster summary") full of FAKE example line items. Real
    # Xactimate pages all carry a running "<estimate id> <date> Page: N"
    # header — keep only those (fall back to everything if none match).
    real_pages = [t for t in pages if re.search(r'Page:\s*\d+', t)]
    if not real_pages:
        real_pages = pages
    text = '\n'.join(real_pages)
    page1 = real_pages[0]

    # ── metadata (page 1) ──
    meta = {}
    for ln in page1.split('\n'):
        s = ln.strip()
        if s and not s.isdigit():
            meta['carrier'] = s
            break
    pairs = {
        'claim_number':  r'Claim Number:\s*(\S+)',
        'policy_number': r'Policy Number:\s*(\S+)',
        'type_of_loss':  r'Type of Loss:\s*([^\n]+)',
        'price_list':    r'Price List:\s*(\S+)',
        'date_of_loss':  r'Date of Loss:\s*([\d/]+)',
    }
    for key, pat in pairs.items():
        m = re.search(pat, page1)
        if m:
            meta[key] = m.group(1).strip()
    m = re.search(r'Insured:\s*(.+?)(?:\s+(?:Home|Business|Cell(?:ular)?|E-mail|Phone):|$)',
                  page1, re.M)
    if m:
        meta['insured'] = m.group(1).strip()

    addr = {}
    m = re.search(r'Property:\s*([^\n]+)\n\s*([A-Za-z .]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?',
                  page1)
    if m:
        addr = {'street': m.group(1).strip(), 'city': m.group(2).strip().title(),
                'state': m.group(3), 'zip': m.group(4)}

    # ── line items ──
    # One continuous stream across pages so a section whose items start after
    # a page break still finds its name; the carrier's running header lines
    # (name + address) are filtered as noise so they can't pose as sections.
    warnings = []
    extra_noise = {meta['carrier']} if meta.get('carrier') else set()
    flat = _parse_xactimate_items(text.split('\n'), extra_noise)

    # Split into runs of strictly-increasing line numbers, then pick the run
    # whose RCV sum matches the document's "Line Item Totals" checksum. Any
    # stray example items (guide pages that slipped the page filter, sample
    # blocks) restart numbering and land in a losing run.
    runs = []
    for entry in flat:
        if runs and entry[0] > runs[-1][-1][0]:
            runs[-1].append(entry)
        else:
            runs.append([entry])
    grand = None
    gm = list(_XACT_GRAND_RE.finditer(text))
    if gm:
        grand = tuple(_xact_num(g) for g in gm[-1].groups())
    chosen = None
    if grand is not None:
        for run in runs:
            if abs(sum(e[2]['rcv'] for e in run) - grand[0]) <= 0.05:
                chosen = run
                break
    if chosen is None and runs:
        chosen = max(runs, key=len)
        if grand is not None:
            warnings.append('Line items did not match the document total — review carefully.')
        elif len(runs) > 1:
            warnings.append('Could not verify totals — review lines carefully.')

    sections = []
    by_name = {}
    for _, sec_name, item in (chosen or []):
        name = sec_name or 'Estimate'
        if abs(item['rcv'] - (item['acv'] + item['depreciation'])) > 0.02:
            warnings.append(
                f"Line {item['line_no']}: RCV {item['rcv']:.2f} ≠ ACV + depreciation "
                f"({item['acv'] + item['depreciation']:.2f}) — ACV/depreciation kept.")
        if name not in by_name:
            by_name[name] = {'name': name, 'items': [], 'totals': None}
            sections.append(by_name[name])
        by_name[name]['items'].append(item)

    # Per-section "Totals: <name> RCV DEP ACV" checksums
    for ln in text.split('\n'):
        tm = _XACT_TOTALS_RE.match(ln.strip())
        if tm and tm.group('name').strip() in by_name:
            by_name[tm.group('name').strip()]['totals'] = {
                'rcv': _xact_num(tm.group('rcv')),
                'dep': _xact_num(tm.group('dep')),
                'acv': _xact_num(tm.group('acv')),
            }

    # ── claim summary: sum each label across the per-coverage blocks ──
    summary = {}
    for key, pat in _XACT_SUMMARY_LABELS.items():
        rx = re.compile(pat)
        total = 0.0
        found = False
        for ln in text.split('\n'):
            s = ln.strip()
            if key == 'net_claim' and re.match(r'^Net Claim if', s):
                continue
            if rx.match(s):
                # Per-coverage summary rows carry exactly one figure; the
                # "Recap by Category" page repeats some labels with three
                # (RCV/Dep/ACV) — skip those so coverages aren't double-counted.
                nums = re.findall(r'[\d,]+\.\d{2}', s)
                if len(nums) == 1:
                    total += _xact_num(nums[0])
                    found = True
        if found:
            summary[key] = round(total, 2)
    if grand is not None:
        summary['line_items_rcv'] = grand[0]
        summary['line_items_depreciation'] = grand[1]
        summary['line_items_acv'] = grand[2]

    return {'meta': meta, 'address': addr, 'sections': sections,
            'summary': summary, 'warnings': warnings}


# ── Symbility (insurance carrier estimate) PDF import ──────────────────────
# Symbility/Cotality exports (Safeco, Liberty Mutual, Farmers …) use a
# different grid from Xactimate — nine columns:
#   DESCRIPTION QUANTITY UNIT-PRICE PER TOTAL-O&P TOTAL-TAXES RC DEPRECIATION ACV
# where "RC" is Xactimate's RCV and "Per" is the unit. Items group under plans
# ("EXTERIOR PLAN: Exterior Plan", "ROOFPLAN: DWELLING ROOF"), each closing
# with a "<name> - Subtotal (N items)" checksum row. Output is the same shape
# the Xactimate importer returns, so the review modal renders it unchanged.
#
# Parsed in pypdf's LAYOUT mode, unlike Xactimate. In default mode Symbility
# splits an item's numeric tail across two lines whenever depreciation is
# non-zero, and loses the column geometry that separates a wrapped description
# from an adjuster's free-text note. Layout mode keeps each item on one line
# and keeps the indentation that tells those two apart.

_SYM_MONEY = r'\$?\(?-?[\d,]+\.\d{2}\)?'
_SYM_QTY = r'[\d,]+(?:\.\d+)?'

# The column header, in layout mode and in the right-to-left run that default
# extraction produces. Either one identifies the format.
_SYM_HEADER_RE = re.compile(
    r'Description\s+Quantity\s+Unit\s*Price\s+Per\s+Total\s*O&P\s+'
    r'Total\s*Taxes\s+RC\s+Depreciation\s+ACV', re.I)
_SYM_HEADER_FLAT_RE = re.compile(
    r'ACVDepreciationRCTotal\s*TaxesTotal\s*O&PPerUnit\s*PriceQuantityDescription', re.I)

# "16  ITEL, Shingles,   11.87 (12.00)  $135.59  SQ  $0.00  $135.05  $1,762.13  $293.74  $1,468.39"
# The parenthesised second quantity is Symbility's materials bundle rounding —
# it is the quantity actually priced (RC = it × unit price + taxes + O&P), so
# it wins; the pre-rounding figure is kept as qty_calculated.
_SYM_ITEM_RE = re.compile(
    r'^(?P<lead>\s*)(?P<no>\d{1,3})\s+(?P<desc>\S.*?)\s{2,}'
    r'(?P<qty>' + _SYM_QTY + r')'
    r'(?:\s*\((?P<qty_billed>' + _SYM_QTY + r')\))?\s+'
    r'\$(?P<price>[\d,]+\.\d{2})\s+'
    r'(?P<unit>[A-Za-z][A-Za-z0-9/]{0,4})\s+'
    r'(?P<oandp>' + _SYM_MONEY + r')\s+'
    r'(?P<tax>' + _SYM_MONEY + r')\s+'
    r'(?P<rc>' + _SYM_MONEY + r')\s+'
    r'(?P<dep>' + _SYM_MONEY + r')\s+'
    r'(?P<acv>' + _SYM_MONEY + r')\s*$')

# "EXTERIOR PLAN: Exterior Plan", "ROOFPLAN: DWELLING ROOF", "INTERIOR PLAN: …"
_SYM_PLAN_RE = re.compile(r'^([A-Z][A-Z ]*PLAN)\s*:\s*(.+?)\s*$')
_SYM_SUBTOTAL_RE = re.compile(
    r'^(?P<name>.+?)\s+-\s+Subtotal\s+\(\d+\s+items?\)\s+'
    r'(?P<oandp>' + _SYM_MONEY + r')\s+(?P<tax>' + _SYM_MONEY + r')\s+'
    r'(?P<rc>' + _SYM_MONEY + r')\s+(?P<dep>' + _SYM_MONEY + r')\s+'
    r'(?P<acv>' + _SYM_MONEY + r')\s*$')
_SYM_GRAND_RE = re.compile(
    r'^Subtotal\s+(?P<oandp>' + _SYM_MONEY + r')\s+(?P<tax>' + _SYM_MONEY + r')\s+'
    r'(?P<rc>' + _SYM_MONEY + r')\s+(?P<dep>' + _SYM_MONEY + r')\s+'
    r'(?P<acv>' + _SYM_MONEY + r')\s*$')
# "Roof area: 2,678.53 SF  Squares: 26.8 SQ  Soffit: 627.62 SF"
_SYM_MEASURE_RE = re.compile(
    r'([A-Z][A-Za-z ()/]*?):\s+([\d,]+(?:\.\d+)?)\s+([A-Z]{2})(?=\s|$)')

_SYM_NOISE_RES = [re.compile(p, re.I) for p in (
    r'^Includes\s+[\d.]+%\s+waste', r'^Materials quantity bundle rounding',
    r'^This line item includes', r'^This item has been applied',
    r'^For more information', r'^verified by ITEL',
    r'^ESTIMATE:', r'^Completed$', r'^Description\s+Quantity',
    r'^Claim\s+\S+\s+Page', r'^Page\s+\d+', r'^P\.?O\.? Box', r'^Fax:',
    r'^www\.', r'^[A-Za-z .]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*$',
)]

# Claim-totals labels (last page). One occurrence each and unambiguous — unlike
# Xactimate these need no per-coverage summing.
_SYM_SUMMARY_LABELS = {
    'line_item_total':          r'^Subtotal:',
    'material_sales_tax':       r'^Total taxes:',
    'rcv_total':                r'^Replacement cost value:',
    'paid_when_incurred':       r'^Less costs payable when incurred:',
    'acv_total':                r'^Actual cash value:',
    'deductible':               r'^Applied deductible:',
    'net_claim':                r'^Net actual cash value:',
    # These two run long enough that a carrier's page width can wrap the label
    # onto a second line, stranding the colon away from the figure, so neither
    # is anchored on one.
    'recoverable_depreciation': r'^Less Recoverable depreciation',
    'net_claim_if_recovered':   r'^Amount payable if depreciation is recovered',
}
# Carrier accounting writes money coming off the claim as negatives; the
# estimate's claim card wants magnitudes, matching the Xactimate importer.
_SYM_ABS_KEYS = {'deductible', 'recoverable_depreciation', 'paid_when_incurred'}


def _sym_num(s):
    """'$1,234.56' → 1234.56; '$(678.24)' → -678.24 (parens are a negative)."""
    s = str(s).strip()
    neg = s.startswith('-') or s.lstrip('$').startswith('(')
    v = float(re.sub(r'[^\d.]', '', s) or 0)
    return -v if neg else v


def _sym_is_noise(line):
    return any(rx.match(line) for rx in _SYM_NOISE_RES)


def _sym_pages(file_bytes):
    """(layout_pages, flat_pages) — both text extractions of every page."""
    if _pypdf is None:
        raise RuntimeError('pypdf not installed')
    reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
    layout, flat = [], []
    for p in reader.pages:
        flat.append(p.extract_text() or '')
        try:
            layout.append(p.extract_text(extraction_mode='layout') or '')
        except Exception:
            layout.append(flat[-1])      # pypdf too old for layout mode
    return layout, flat


def _sym_cell(text, pattern, cont=False):
    """Value of a "Label:  value" cell in the two-column page-1 form.

    Page 1 is a grid, so a value ends at the next run of 2+ spaces — the gutter
    before the right-hand column. With cont=True a wrapped second line is
    appended when it starts in the same column and carries no label of its own,
    which is how "Pricing Database" spills its year onto the next row.
    """
    lines = text.split('\n')
    rx = re.compile(pattern)
    for i, ln in enumerate(lines):
        m = rx.search(ln)
        if not m:
            continue
        rest = ln[m.end():]
        if not rest.strip():
            continue
        val = re.split(r'\s{2,}', rest.strip())[0].strip()
        if cont and i + 1 < len(lines):
            col = m.end() + (len(rest) - len(rest.lstrip()))
            nxt = lines[i + 1]
            seg = re.split(r'\s{2,}', nxt.strip())[0].strip() if nxt.strip() else ''
            start = len(nxt) - len(nxt.lstrip())
            if seg and ':' not in seg and abs(start - col) <= 3:
                val = f'{val} {seg}'
        return val
    return None


# "FORT COLLINS CO  80526-4410" at the tail of a row. Anchored at the end and
# started on a letter so it cannot swallow the left-hand column of the same
# grid row ("Deductible:  $3,878.00        FORT COLLINS CO  80526-4410").
_SYM_CITY_RE = re.compile(
    r"([A-Za-z][A-Za-z .'-]*?)\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$")


def _sym_address(page1):
    """Property address. "Loss address:" is the risk location; the bare
    "Address:" cell is the insured's mailing address and only stands in when
    the loss address is blank."""
    lines = page1.split('\n')
    for pat in (r'Loss address:', r'(?<!Contact )\bAddress:'):
        rx = re.compile(pat)
        for i, ln in enumerate(lines):
            m = rx.search(ln)
            if not m or not ln[m.end():].strip():
                continue
            rest = ln[m.end():]
            street = re.split(r'\s{2,}', rest.strip())[0].strip()
            col = m.end() + (len(rest) - len(rest.lstrip()))
            for nxt in lines[i + 1:i + 3]:
                cm = _SYM_CITY_RE.search(nxt)
                if cm and abs(cm.start() - col) <= 4:
                    return {'street': street, 'city': cm.group(1).strip().title(),
                            'state': cm.group(2), 'zip': cm.group(3)}
    return {}


def _parse_symbility_meta(page1):
    meta = {}
    for ln in page1.split('\n'):
        s = ln.strip()
        if s and not s.isdigit():
            meta['carrier'] = s
            break
    for key, pat in (
            ('claim_number',  r'CLAIM NO\.?:'),
            ('insured',       r'INSURED:'),
            ('date_of_loss',  r'Date of Loss:'),
            ('policy_number', r'Policy No\.?:'),
            ('policy_type',   r'Policy Type:'),
            ('type_of_loss',  r'Type of Claim:'),
            ('adjuster',      r'Claim Rep:')):
        v = _sym_cell(page1, pat)
        if v:
            meta[key] = v
    v = _sym_cell(page1, r'Pricing Database:', cont=True)
    if v:
        meta['price_list'] = v
    return meta


def _parse_symbility_items(layout_pages):
    """Item pages → (sections, grand, warnings).

    A section is one plan. Its items carry the ALL-CAPS category label they sat
    under ("SOFFIT", "UNDERLAYMENTS") for reference; the plan is the grouping
    because that is the level Symbility gives a subtotal checksum for.
    """
    sections, by_key, warnings = [], {}, []
    plan = None
    category = None
    open_item = None      # last emitted item; may absorb wrapped description
    desc_col = 0
    cont = 0
    body_indent = None    # column the item numbers start in, on this page
    grand = None

    def section_for(name):
        key = name.casefold()
        if key not in by_key:
            by_key[key] = {'name': name.title() if name.isupper() else name,
                           'raw_name': name, 'items': [], 'totals': None,
                           'measurements': {}}
            sections.append(by_key[key])
        return by_key[key]

    for page in layout_pages:
        if not _SYM_HEADER_RE.search(page):
            continue                      # cover letter / summary page
        for raw in page.split('\n'):
            line = raw.rstrip()
            s = line.strip()
            if not s:
                continue
            indent = len(line) - len(line.lstrip())

            m = _SYM_ITEM_RE.match(line)
            if m:
                qty = _sym_num(m.group('qty'))
                billed = m.group('qty_billed')
                item = {
                    'line_no':      int(m.group('no')),
                    'description':  re.sub(r'\s+', ' ', m.group('desc')).strip(),
                    'qty':          _sym_num(billed) if billed else qty,
                    'unit':         m.group('unit'),
                    'unit_price':   _sym_num(m.group('price')),
                    'overhead_profit': _sym_num(m.group('oandp')),
                    'tax':          _sym_num(m.group('tax')),
                    'rcv':          _sym_num(m.group('rc')),
                    'depreciation': _sym_num(m.group('dep')),
                    'acv':          _sym_num(m.group('acv')),
                }
                if billed:
                    item['qty_calculated'] = qty
                if category:
                    item['category'] = category
                section_for(plan or 'Estimate')['items'].append(item)
                open_item, desc_col, cont = item, m.start('desc'), 0
                body_indent = len(m.group('lead'))
                continue

            pm = _SYM_PLAN_RE.match(s)
            if pm:
                plan = pm.group(2).strip()
                section_for(plan)
                category = open_item = None
                if body_indent is None:
                    body_indent = indent
                continue

            sm = _SYM_SUBTOTAL_RE.match(s)
            if sm:
                sec = by_key.get(sm.group('name').strip().casefold())
                if sec is not None:
                    sec['totals'] = {'rcv': _sym_num(sm.group('rc')),
                                     'dep': _sym_num(sm.group('dep')),
                                     'acv': _sym_num(sm.group('acv'))}
                open_item = None
                continue

            gm = _SYM_GRAND_RE.match(s)
            if gm:
                grand = (_sym_num(gm.group('rc')), _sym_num(gm.group('dep')),
                         _sym_num(gm.group('acv')))
                open_item = None
                continue

            # A wrapped description resumes in the description column; an
            # adjuster's note is indented well past it. That gap is the only
            # thing distinguishing them, which is why layout mode is required.
            if (open_item is not None and cont < 4 and abs(indent - desc_col) <= 2
                    and not _sym_is_noise(s)):
                open_item['description'] = re.sub(
                    r'\s+', ' ', f"{open_item['description']} {s}").strip()
                cont += 1
                continue

            if _sym_is_noise(s):
                open_item = None
                continue

            if '$' not in s and _SYM_MEASURE_RE.search(s):
                sec = by_key.get((plan or '').casefold())
                if sec is not None:
                    for lbl, val, unit in _SYM_MEASURE_RE.findall(s):
                        sec['measurements'][lbl.strip()] = {
                            'value': _sym_num(val), 'unit': unit}
                open_item = None
                continue

            # ALL-CAPS group label ("SOFFIT", "VENTS AND FLASHINGS"). Adjuster
            # comments are typed in caps too, so two things separate a heading
            # from a sentence: headings sit at or left of the item-number
            # column (comments are indented past it, including their wrapped
            # second line), and headings are short — a comment left at that
            # column runs long ("BACK LOWER T-LOCK SECTION PAID FOR ON …").
            if (body_indent is not None and indent <= body_indent
                    and len(s) <= 45 and any(c.isalpha() for c in s)
                    and not any(c.islower() for c in s)):
                category = s
            open_item = None

    for sec in sections:
        for it in sec['items']:
            if abs(it['rcv'] - (it['acv'] + it['depreciation'])) > 0.02:
                warnings.append(
                    f"Line {it['line_no']}: RC {it['rcv']:.2f} ≠ ACV + depreciation "
                    f"({it['acv'] + it['depreciation']:.2f}) — ACV/depreciation kept.")
        t = sec['totals']
        if t and abs(sum(i['rcv'] for i in sec['items']) - t['rcv']) > 0.05:
            warnings.append(
                f"{sec['name']}: line items total "
                f"{sum(i['rcv'] for i in sec['items']):,.2f} but the section subtotal "
                f"says {t['rcv']:,.2f} — review carefully.")

    total_rcv = sum(i['rcv'] for s in sections for i in s['items'])
    if grand is not None and abs(total_rcv - grand[0]) > 0.05:
        warnings.append(
            f'Line items total {total_rcv:,.2f} but the estimate subtotal says '
            f'{grand[0]:,.2f} — review carefully.')

    return [s for s in sections if s['items']], grand, warnings


def _parse_symbility_summary(flat_text, grand):
    summary = {}
    for key, pat in _SYM_SUMMARY_LABELS.items():
        rx = re.compile(pat, re.I)
        for ln in flat_text.split('\n'):
            s = ln.strip()
            if not rx.match(s):
                continue
            nums = re.findall(r'\$\(?-?[\d,]+\.\d{2}\)?', s)
            if nums:
                v = _sym_num(nums[-1])
                summary[key] = round(abs(v) if key in _SYM_ABS_KEYS else v, 2)
                break
    if 'recoverable_depreciation' in summary:
        summary.setdefault('depreciation_total', summary['recoverable_depreciation'])
    if grand is not None:
        summary['line_items_rcv'] = grand[0]
        summary['line_items_depreciation'] = grand[1]
        summary['line_items_acv'] = grand[2]
    return summary


# Symbility plan measurement labels → the estimator's measurement fields, in
# the same {'measurements': {...}} shape /api/parse-roofr returns so the two
# importers stay interchangeable. Only labels that map without inventing a
# definition are here: Soffit, Footprint, Subtractions and the exterior wall
# areas have no unambiguous counterpart and are left out rather than guessed
# at — a wrong take-off figure is worse than a missing one.
_SYM_MEASURE_MAP = {
    'squares': 'roof_squares',
    'eaves':   'eave_lf',
    'ridge':   'ridge_lf',
}


def _symbility_measurements(sections):
    """Roof measurements, summed across the plans that actually carry work.

    Plans the adjuster zeroed out are already dropped for having no items,
    which is what keeps an uncovered shed's squares out of the roof total.
    """
    out = {}
    for sec in sections:
        for label, m in sec.get('measurements', {}).items():
            key = _SYM_MEASURE_MAP.get(label.strip().casefold())
            if key:
                out[key] = round(out.get(key, 0.0) + m['value'], 2)
    if 'roof_squares' not in out:
        # A plan that prints its area but not its squares still gives the one
        # figure the estimator cannot work without.
        area = sum(m['value'] for sec in sections
                   for label, m in sec.get('measurements', {}).items()
                   if label.strip().casefold() == 'roof area')
        if area:
            out['roof_squares'] = round(area / 100, 2)
    return out


def _parse_symbility_pdf(file_bytes):
    layout_pages, flat_pages = _sym_pages(file_bytes)
    page1 = layout_pages[0] if layout_pages else ''
    meta = _parse_symbility_meta(page1)
    addr = _sym_address(page1)
    sections, grand, warnings = _parse_symbility_items(layout_pages)
    summary = _parse_symbility_summary('\n'.join(flat_pages), grand)
    return {'format': 'symbility', 'meta': meta, 'address': addr,
            'sections': sections, 'summary': summary, 'warnings': warnings,
            'measurements': _symbility_measurements(sections)}


def _detect_carrier_format(file_bytes):
    """'symbility' or 'xactimate'. Neither product stamps its own name on the
    export, so this goes by the column header, which differs completely."""
    if _pypdf is None:
        raise RuntimeError('pypdf not installed')
    reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
    for p in reader.pages:
        flat = p.extract_text() or ''
        if _SYM_HEADER_FLAT_RE.search(flat):
            return 'symbility'
        if _XACT_HEADER_RE.search(flat):
            return 'xactimate'
        try:
            if _SYM_HEADER_RE.search(p.extract_text(extraction_mode='layout') or ''):
                return 'symbility'
        except Exception:
            pass
    return 'xactimate'


@app.route('/api/parse-xactimate', methods=['POST'])
def parse_xactimate():
    """Carrier estimate import — Xactimate or Symbility.

    The two products share nothing but the job they do, so the format is
    sniffed and dispatched. Both parsers return the same shape, so the review
    modal renders either one; the route stays parse-only and persists nothing.
    The path keeps its original name because the browser posts here.
    """
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file.'}), 400
    raw = f.read()
    try:
        fmt = _detect_carrier_format(raw)
        data = (_parse_symbility_pdf if fmt == 'symbility'
                else _parse_xactimate_pdf)(raw)
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {e}'}), 400
    data.setdefault('format', 'xactimate')
    if not any(s.get('items') for s in data['sections']):
        label = 'Symbility' if data['format'] == 'symbility' else 'Xactimate'
        return jsonify({'error': f"Couldn’t find {label} line items in this PDF. "
                                 "Make sure it’s the carrier’s estimate export."}), 422
    return jsonify(data)


# ── CRM proxy ──────────────────────────────────────────────────────────────

@app.route('/api/crm/contacts')
def search_contacts():
    q = request.args.get('q', '').lower().strip()
    contacts = fetch_all_contacts()
    if q:
        contacts = [c for c in contacts if
                    q in (c.get('name') or '').lower() or
                    q in (c.get('phone') or '').lower() or
                    q in (c.get('email') or '').lower()]
    slim = [{
        'id':             c.get('id'),
        'name':           c.get('name', ''),
        'phone':          c.get('phone', ''),
        'email':          c.get('email', ''),
        'street_address': c.get('street_address', ''),
        'city':           c.get('city', ''),
        'state':          c.get('state', ''),
        'zip_code':       c.get('zip_code', ''),
    } for c in contacts[:25]]
    return jsonify(slim)


@app.route('/api/crm/contacts/<contact_id>')
def get_contact(contact_id):
    for c in fetch_all_contacts():
        if c.get('id') == contact_id:
            return jsonify(c)
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/crm/jobs')
def search_jobs():
    q = request.args.get('q', '').lower().strip()
    projects = fetch_all_projects()
    if q:
        projects = [p for p in projects if
                    q in (p.get('name') or '').lower() or
                    q in (p.get('client_name') or '').lower() or
                    q in (p.get('client_phone') or '').lower() or
                    q in (p.get('client_email') or '').lower() or
                    q in (p.get('job_number') or '').lower() or
                    q in (p.get('address') or '').lower()]
    projects = sorted(projects, key=lambda p: p.get('created_date') or '', reverse=True)
    slim = [{
        'id':                   p.get('id'),
        # The Den's contact id. Carried through so an estimate started from
        # this picker records which job it belongs to — without it, the only
        # way to match a bid to its actual costs later is by customer name,
        # which silently mismatches. salescrm sets this on every project it
        # creates (`project_payload['contact_id']`).
        'contact_id':           p.get('contact_id', ''),
        'name':                 p.get('name', ''),
        'job_number':           p.get('job_number', ''),
        'client_name':          p.get('client_name', ''),
        'client_phone':         p.get('client_phone', ''),
        'client_email':         p.get('client_email', ''),
        'address':              p.get('address', ''),
        'status':               p.get('status', ''),
        'assigned_salesperson': p.get('assigned_salesperson', ''),
    } for p in projects[:25]]
    return jsonify(slim)


# ── E-Signature helpers ────────────────────────────────────────────────────

def he(s):
    """HTML-escape a value."""
    return _html.escape(str(s)) if s is not None else ''

def fc(n):
    """Format as currency."""
    try:
        return f'${float(n):,.2f}'
    except Exception:
        return '$0.00'

# ── Shared pricing math — MUST mirror app.js (tierRate / lineTotalEffective) ─
# The frontend prices with per-tier rates (pricing.tier_rates) and honors
# per-line price_override. Everything server-rendered (customer sign page,
# signed PDF, list totals, emails, analytics) must price identically or the
# customer sees different numbers than the rep quoted.

GBB_TRADES = ['roofing', 'siding', 'windows', 'gutters', 'commercial', 'other']

# Trades that sell as one price rather than Good/Better/Best unless the rep
# says otherwise. MUST mirror SIMPLE_MODE_TRADES in app.js — if the two
# disagree the server prices a trade differently than the browser showed.
SIMPLE_MODE_TRADES = ('gutters', 'commercial')


def _trade_mode(tk, td):
    """Effective pricing mode for a trade. Mirrors effectiveTradeMode in app.js,
    including treating an empty-string mode as unset."""
    return (td or {}).get('mode') or ('simple' if tk in SIMPLE_MODE_TRADES else 'gbb')


DEFAULT_RATE = 35.0


def _rate_value(v):
    """A rate source counts as set only if it parses as a number. 0 counts — a
    rep really can sell at cost — but None/''/junk do not. Returns None when the
    source is unset so the caller falls through. MUST mirror _rateValue (app.js)."""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tier_rate(pricing, trade, tier):
    """Effective margin/markup %, most specific first: per-trade-per-tier
    (trade_rates[trade][tier]) → legacy flat per-trade override → global per-tier
    rate → global rate → DEFAULT_RATE. Defaulting to 35 rather than 0 is
    deliberate: a 0% fallback would silently sell at cost. MUST mirror tierRate
    (app.js)."""
    trade_rates = (pricing.get('trade_rates') or {}).get(trade) or {}
    for v in (trade_rates.get(tier),
              (pricing.get('per_trade_overrides') or {}).get(trade),
              (pricing.get('tier_rates') or {}).get(tier),
              pricing.get('global_rate')):
        r = _rate_value(v)
        if r is not None:
            return r
    return DEFAULT_RATE


def _sell_price(cost, rate, mode):
    """Unit sell price from unit cost. Margin ≥100% is invalid → 0 (matches app.js)."""
    if mode == 'margin':
        return cost / (1 - rate / 100) if rate < 100 else 0.0
    return cost * (1 + rate / 100)


def _line_sell_total(item, tier, rate, mode):
    """GBB line sell total, honoring a locked price_override (a line total)."""
    t = (item.get('tiers') or {}).get(tier) or {}
    po = t.get('price_override')
    if po is not None and po != '':
        try:
            return float(po)
        except (TypeError, ValueError):
            pass
    cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
    qty  = float(item.get('quantity') or 0)
    return _sell_price(cost, rate, mode) * qty


def _trade_subtotal(est, trade, tier):
    """Sell subtotal for one trade at one tier (simple trades ignore the tier)."""
    pricing = est.get('pricing', {})
    mode    = pricing.get('mode', 'margin')
    td      = (est.get('trades') or {}).get(trade, {})
    if not td.get('enabled'):
        return 0.0
    trade_mode = _trade_mode(trade, td)
    r     = _tier_rate(pricing, trade, tier)
    total = 0.0
    for item in td.get('line_items', []):
        # Zero-qty items are "not in scope" — never priced, even when a
        # price_override is set (the customer view and signed PDF already
        # hide them; the total must agree). MUST mirror tradeTotal (app.js).
        if float(item.get('quantity') or 0) <= 0:
            continue
        if trade_mode == 'simple':
            total += float(item.get('unit_price') or 0) * float(item.get('quantity') or 0)
        else:
            t = (item.get('tiers') or {}).get(tier, {})
            if t.get('included') is False:
                continue  # item excluded from this package tier
            total += _line_sell_total(item, tier, r, mode)
    return total


def calc_tier_total(est, tier):
    """Compute grand sell total for a given tier (excludes insurance)."""
    return sum(_trade_subtotal(est, tk, tier) for tk in GBB_TRADES)


def _trade_tier(est, trade):
    """The tier chosen for one trade: signature's per-trade pick → estimate's
    per-trade pick → trade dict's own selected_tier → legacy estimate-level
    selected_tier. Legacy docs (no per-trade data) resolve exactly as before."""
    sig = est.get('signature') or {}
    for src in (sig.get('selected_tiers'), est.get('selected_tiers')):
        t = (src or {}).get(trade)
        if t in ('good', 'better', 'best'):
            return t
    td = (est.get('trades') or {}).get(trade) or {}
    t = td.get('selected_tier')
    if t in ('good', 'better', 'best'):
        return t
    t = sig.get('selected_tier') or est.get('selected_tier')
    return t if t in ('good', 'better', 'best') else 'better'


def calc_selected_total(est):
    """Grand sell total honoring each trade's own selected tier (mix-and-match).
    MUST mirror selectedTotal in app.js."""
    return sum(_trade_subtotal(est, tk, _trade_tier(est, tk)) for tk in GBB_TRADES)


def _gbb_trade_keys(est):
    """Enabled non-insurance trades offered as Good/Better/Best, in trade order."""
    out = []
    for tk in GBB_TRADES:
        td = (est.get('trades') or {}).get(tk) or {}
        if not td.get('enabled'):
            continue
        if _trade_mode(tk, td) != 'gbb':
            continue
        out.append(tk)
    return out


def _package_trade_keys(est):
    """Trades presented to the customer as a Good/Better/Best choice.

    Drops `other`, which is a G/B/B trade by data shape only — its Pricing tab
    shows one tier at a time and writes cost and description to all three, so
    offering it as a package renders three identical columns. It still gets its
    own line-item table everywhere. Mirrors packageTrades() in app.js."""
    return [tk for tk in _gbb_trade_keys(est) if tk != 'other']


def _tier_bullets_are_stale(pb, est, trade, tier):
    """Have this tier's stored bullets/tagline stopped describing what it sells?

    tier_features / tier_descriptions are written by a bundle pick (and, before
    it was retired, by the Options tab). Nothing rewrites them when a rep builds
    the package by hand, so a customer reads "Architectural laminate shingle
    system - lifetime limited warranty" over a rolled-roofing line item, and
    there is no editor left to correct it. Stale means either the tier is
    __custom__ - the rep said so on the dropdown, and that stands on its own -
    or it still names a bundle but none of that bundle's products is priced in
    the tier any more. A pre-bundle estimate carries no tier_bundles at all;
    those bullets were curated by hand and are left alone.

    MUST mirror tierBulletsAreStale in app.js."""
    td = (est.get('trades') or {}).get(trade) or {}
    tb = td.get('tier_bundles')
    if not isinstance(tb, dict):
        return False                      # pre-bundle estimate - hand-curated
    bid = str(tb.get(tier) or '').strip()
    # Custom is the rep saying, on the Product dropdown, that this tier is not
    # a package the book sells. Nothing else ever writes __custom__, so it is
    # evidence on its own and is judged BEFORE the catalog test below.
    # It used to be judged after, and that cost a real estimate: seeding a new
    # estimate loads the default shingle bundles, so building a rolled-roofing
    # job by hand means deleting those rows - which deletes the last catalog_id
    # in the trade, and a trade with no catalog_id was ruled "never built by a
    # bundle, bullets are the rep's own". The shingle tagline the bundle wrote
    # on the way past then printed over the rolled roofing. Evidence the rep
    # destroyed while doing exactly what the tab asks is not evidence.
    if bid == '__custom__':
        return True
    items = td.get('line_items') or []
    # A tier that still NAMES a bundle is judged only on a trade the bundles
    # actually built. Items created by applyBundleToTier carry catalog_id; a
    # hand-shaped trade has none, and without that evidence there is no bundle
    # whose leftover copy this could be - the bullets are the rep's own and stay.
    if not any(it.get('catalog_id') for it in items):
        return False
    if not bid:
        return False
    bundle = next((b for b in (pb.get(trade + '_bundles') or [])
                   if isinstance(b, dict) and b.get('id') == bid), None)
    ids = set(bundle.get('product_ids') or []) if bundle else set()
    if not ids:
        return False                      # bundle gone from the book - no call to make
    for it in items:
        if it.get('catalog_id') not in ids:
            continue
        if float(it.get('quantity') or 0) <= 0:
            continue
        if ((it.get('tiers') or {}).get(tier) or {}).get('included') is False:
            continue
        return False
    return True


def _tier_card_content(pb, est, trade, tier, tfeat, tdesc):
    """(bullets, tagline) for one package card — the stored pair when it still
    matches the tier's line items, the autofill built from those line items
    when it doesn't. One helper so the customer page, the presentation and the
    AI feed can never disagree about what a package includes."""
    if _tier_bullets_are_stale(pb, est, trade, tier):
        return _autofill_tier_features(est, trade, tier), ''
    feats = [str(f).strip() for f in (tfeat.get(tier) or []) if str(f).strip()]
    if not feats:
        feats = _autofill_tier_features(est, trade, tier)
    return feats, (tdesc.get(tier) or '').strip()


def _pick_summary_label(est):
    """Human label for what was/is selected: 'Better Package' for a single
    G/B/B product, 'Roofing: Better · Siding: Good' for a mix."""
    tls  = dict(roofing='Roofing', siding='Siding', windows='Windows',
                gutters='Gutters', commercial='Commercial', other='Other / Misc')
    lbls = dict(good='Good', better='Better', best='Best')
    tks  = [tk for tk in _package_trade_keys(est)
            if ((est.get('trades') or {}).get(tk) or {}).get('line_items')]
    if not tks:
        return ''
    if len(tks) == 1:
        return lbls.get(_trade_tier(est, tks[0]), 'Better') + ' Package'
    return ' · '.join(f'{tls[tk]}: {lbls.get(_trade_tier(est, tk), "Better")}' for tk in tks)


def _trade_tier_content(est, trade):
    """(features, descriptions) dicts for one trade's package cards. Legacy
    estimates carried ONE estimate-level set — that copy belongs to ROOFING,
    which is where G/B/B lived before it went per-trade. Handing it to whatever
    trade happened to be first in the list leaks roofing bullets onto a
    siding/windows card when roofing isn't enabled."""
    td = (est.get('trades') or {}).get(trade) or {}
    feats = td.get('tier_features')
    descs = td.get('tier_descriptions')
    if not isinstance(feats, dict):
        feats = est.get('tier_features') if trade == 'roofing' else {}
    if not isinstance(descs, dict):
        descs = est.get('tier_descriptions') if trade == 'roofing' else {}
    return (feats or {}), (descs or {})


def _autofill_tier_features(est, trade, tier):
    """Bullets built from priced, customer-visible line items — the fallback
    when the rep hasn't curated tier content on the Options tab. Mirrors the
    Auto-fill button in the UI so a blank card never reaches the customer."""
    td = (est.get('trades') or {}).get(trade) or {}
    if not td.get('enabled'):
        return []
    mode = _trade_mode(trade, td)
    out, seen = [], set()
    for item in td.get('line_items', []) or []:
        if item.get('customer_visible') is False:
            continue
        name = (item.get('name') or '').strip()
        if not name:
            continue
        qty  = float(item.get('quantity') or 0)
        if mode == 'simple':
            up = float(item.get('unit_price') or 0)
            if qty <= 0 and up <= 0:
                continue
            desc = (item.get('description') or '').strip()
        else:
            ti = (item.get('tiers') or {}).get(tier) or {}
            if ti.get('included') is False:
                continue
            cost = float(ti.get('material_unit_cost') or 0) + float(ti.get('labor_unit_cost') or 0)
            if qty <= 0 and cost <= 0:
                continue
            desc = (ti.get('description') or '').strip()
        line = f'{name} — {desc}' if desc and desc != name else name
        if line not in seen:
            seen.add(line); out.append(line)
    return out


def _tier_package_names(est, trade):
    """tier -> the rep's name for that package, for the trades that carry one.

    Only a CUSTOM package has a name here: a bundle tier is named by its bundle
    in the price book, and copying that name onto the estimate would let the two
    drift apart. Mirrors tierPackageName() in app.js — same field, same rule."""
    td = (est.get('trades') or {}).get(trade) or {}
    names = td.get('tier_bundle_names')
    return names if isinstance(names, dict) else {}


@app.route('/api/analytics')
def get_analytics():
    """Per-trade and per-rep revenue, cost, and margin across all estimates."""
    TRADE_NAMES = list(GBB_TRADES)
    by_trade = {}
    by_rep   = {}

    # 'YYYY-MM' → one month's full picture. Signed metrics bucket on the
    # signature date; sent metrics bucket on the send date, so `sent`/`sent_won`
    # read as a cohort ("of what we sent in June, how much has closed").
    #
    # `revenue` is the estimate total (what the customer actually signed, and
    # what goals are measured against). `trade_revenue`/`trade_cost` are the
    # per-trade priced figures and exist only to compute margin on a matching
    # basis — never mix the two in one ratio.
    monthly = {}

    def _mo(key):
        return monthly.setdefault(key, {
            'revenue': 0.0, 'jobs': 0, 'retail': 0.0, 'insurance': 0.0, 'commercial': 0.0,
            'trade_revenue': 0.0, 'trade_cost': 0.0,
            'sent': 0, 'sent_value': 0.0, 'sent_won': 0, 'sent_won_value': 0.0,
            'by_rep': {},
        })

    # ── New aggregations ──────────────────────────────────────────────
    funnel = {'total': 0, 'sent': 0, 'viewed': 0, 'signed': 0, 'lost': 0}
    pipeline_aging = {
        'fresh':  {'count': 0, 'value': 0.0},   # 0–3 days
        'active': {'count': 0, 'value': 0.0},   # 4–14 days
        'stale':  {'count': 0, 'value': 0.0},   # 15–30 days
        'cold':   {'count': 0, 'value': 0.0},   # 30+ days
    }
    by_type = {
        'retail':     {'revenue': 0.0, 'count': 0, 'pipeline': 0.0},
        'insurance':  {'revenue': 0.0, 'count': 0, 'pipeline': 0.0},
        'commercial': {'revenue': 0.0, 'count': 0, 'pipeline': 0.0},
    }
    top_cities   = {}   # city → signed revenue
    ytd_revenue  = 0.0
    all_dtc      = []   # company-wide days-to-close list
    now_dt       = datetime.utcnow()
    ytd_cutoff   = now_dt.replace(month=1, day=1, hour=0, minute=0, second=0)

    for est in est_iter():
        is_signed  = bool(est.get('signature'))
        is_sent    = bool(est.get('share_token'))
        sp         = (est.get('salesperson') or '').strip()
        if not sp:
            continue  # skip unassigned estimates

        # ── Funnel counting ───────────────────────────────────────────
        funnel['total'] += 1
        if is_sent:   funnel['sent']    += 1
        if est.get('first_viewed_at'): funnel['viewed'] += 1
        if is_signed: funnel['signed']  += 1
        if _is_lost(est): funnel['lost'] += 1

        # ── Pipeline aging (open, sent estimates only) ────────────────
        if is_sent and not is_signed and not _is_lost(est):
            sent_dt_str = est.get('sent_at') or ''
            est_total   = _estimate_total(est)
            try:
                sent_dt = datetime.fromisoformat(sent_dt_str.replace('Z','').replace('+00:00',''))
                age_days = (now_dt - sent_dt).days
                bucket = 'cold' if age_days > 30 else 'stale' if age_days > 14 else 'active' if age_days > 3 else 'fresh'
                pipeline_aging[bucket]['count'] += 1
                pipeline_aging[bucket]['value'] += est_total
            except Exception:
                pass

        # ── Revenue by type ───────────────────────────────────────────
        est_type = est.get('estimate_type', 'retail') or 'retail'
        if est_type not in by_type:
            by_type[est_type] = {'revenue': 0.0, 'count': 0, 'pipeline': 0.0}
        est_total = _estimate_total(est)
        if is_signed:
            by_type[est_type]['revenue'] += est_total
            by_type[est_type]['count']   += 1
        elif is_sent:
            by_type[est_type]['pipeline'] += est_total

        # ── Monthly: sent cohort ─────────────────────────────────────
        sent_month = (est.get('sent_at') or '')[:7]
        if is_sent and _GOAL_MONTH_RE.match(sent_month):
            m = _mo(sent_month)
            m['sent']       += 1
            m['sent_value'] += est_total
            if is_signed:
                m['sent_won']       += 1
                m['sent_won_value'] += est_total

        # ── YTD, city & monthly signed revenue ───────────────────────
        if is_signed:
            signed_dt_str = (est.get('signature') or {}).get('signed_at') or ''
            try:
                signed_dt = datetime.fromisoformat(signed_dt_str.replace('Z','').replace('+00:00',''))
                if signed_dt >= ytd_cutoff:
                    ytd_revenue += est_total
            except Exception:
                pass
            signed_month = signed_dt_str[:7]
            if _GOAL_MONTH_RE.match(signed_month):
                m = _mo(signed_month)
                m['revenue'] += est_total
                m['jobs']    += 1
                m[est_type if est_type in ('retail', 'insurance', 'commercial') else 'retail'] += est_total
                m['by_rep'][sp] = m['by_rep'].get(sp, 0.0) + est_total
            city = (est.get('customer') or {}).get('address', {}).get('city', '').strip()
            if city:
                top_cities[city] = top_cities.get(city, 0.0) + est_total

        pricing    = est.get('pricing', {})
        mode       = pricing.get('mode', 'margin')

        if sp not in by_rep:
            by_rep[sp] = {
                'sent': 0, 'signed': 0, 'revenue': 0, 'cost': 0,
                'pipeline': 0, 'pipeline_count': 0,
                'days_to_close': [],  # list of floats for averaging
                'stale': 0,           # sent 3+ days, not signed
                'deals': [],          # individual deal totals for distribution
            }
        if is_sent:
            by_rep[sp]['sent'] += 1
            # Stale = sent 3+ days and not signed
            sent_at = est.get('sent_at') or ''
            if sent_at and not is_signed:
                try:
                    days_sent = (datetime.utcnow() - datetime.fromisoformat(sent_at.replace('Z','+00:00').replace('+00:00',''))).days
                    if days_sent >= 3:
                        by_rep[sp]['stale'] += 1
                except Exception:
                    pass
        if is_signed:
            by_rep[sp]['signed'] += 1
            # Days to close
            sent_at   = est.get('sent_at') or ''
            signed_at = (est.get('signature') or {}).get('signed_at') or ''
            if sent_at and signed_at:
                try:
                    d1 = datetime.fromisoformat(sent_at.replace('Z','').replace('+00:00',''))
                    d2 = datetime.fromisoformat(signed_at.replace('Z','').replace('+00:00',''))
                    by_rep[sp]['days_to_close'].append(max(0, (d2 - d1).days))
                except Exception:
                    pass

        for tk in TRADE_NAMES:
            td = est.get('trades', {}).get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            tmode = _trade_mode(tk, td)
            tier  = _trade_tier(est, tk)   # each trade prices at its own chosen tier
            r     = _tier_rate(pricing, tk, tier)
            tsell = 0.0
            tcost = 0.0

            for item in td['line_items']:
                qty = float(item.get('quantity') or 0)
                if qty <= 0:
                    continue
                if tmode == 'simple':
                    tsell += float(item.get('unit_price') or 0) * qty
                    tcost += float(item.get('unit_cost')  or 0) * qty
                else:
                    t    = (item.get('tiers') or {}).get(tier, {})
                    if t.get('included') is False:
                        continue
                    cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
                    tsell += _line_sell_total(item, tier, r, mode)
                    tcost += cost * qty

            if tk not in by_trade:
                by_trade[tk] = {'revenue':0,'cost':0,'pipeline':0,'job_count':0,'pipeline_count':0}

            if is_signed:
                by_trade[tk]['revenue']   += tsell
                by_trade[tk]['cost']      += tcost
                by_trade[tk]['job_count'] += 1
                by_rep[sp]['revenue']     += tsell
                by_rep[sp]['cost']        += tcost
                # Monthly margin basis — sell and cost from the same trade math.
                month_key = ((est.get('signature') or {}).get('signed_at') or '')[:7]
                if _GOAL_MONTH_RE.match(month_key):
                    m = _mo(month_key)
                    m['trade_revenue'] += tsell
                    m['trade_cost']    += tcost
                by_rep[sp]['deals'].append(tsell)
                all_dtc.extend(by_rep[sp].get('days_to_close', [])[-1:])  # company wide
            elif is_sent:
                by_trade[tk]['pipeline']       += tsell
                by_trade[tk]['pipeline_count'] += 1
                by_rep[sp]['pipeline']         += tsell
                by_rep[sp]['pipeline_count']   += 1

    def _margin(rev, cost):
        return round((rev - cost) / rev * 100, 1) if rev > 0 and cost > 0 else None

    for d in by_trade.values():
        d['margin_pct'] = _margin(d['revenue'], d['cost'])
    for d in by_rep.values():
        dtc = d.pop('days_to_close')
        deals = d.pop('deals')
        d['margin_pct']       = _margin(d['revenue'], d['cost'])
        d['close_rate']       = round(d['signed'] / d['sent'] * 100) if d['sent'] > 0 else 0
        d['avg_days_to_close'] = round(sum(dtc) / len(dtc), 1) if dtc else None
        d['avg_deal']          = round(d['revenue'] / d['signed'], 0) if d['signed'] > 0 else 0

    top_cities_list = sorted(top_cities.items(), key=lambda x: -x[1])[:8]

    # ── Monthly series vs goals ───────────────────────────────────────────
    goals     = _load_goals()
    cur_month = now_dt.strftime('%Y-%m')

    def _month_add(key, delta):
        i = int(key[:4]) * 12 + int(key[5:7]) - 1 + delta
        return '%04d-%02d' % (i // 12, i % 12 + 1)

    # Contiguous keys so the chart has no holes — a month with zero signed work
    # is a real data point, not a gap to skip. Capped at 24 months back.
    have    = sorted(monthly)
    first   = min(have[0], cur_month) if have else cur_month
    last    = max(have[-1], cur_month) if have else cur_month
    first   = max(first, _month_add(cur_month, -23))
    series_keys, k = [], first
    while k <= last and len(series_keys) < 36:
        series_keys.append(k)
        k = _month_add(k, 1)

    rev_by_month = {mk: mv['revenue'] for mk, mv in monthly.items()}
    blank = {'revenue': 0.0, 'jobs': 0, 'retail': 0.0, 'insurance': 0.0, 'commercial': 0.0,
             'trade_revenue': 0.0, 'trade_cost': 0.0, 'sent': 0, 'sent_value': 0.0,
             'sent_won': 0, 'sent_won_value': 0.0, 'by_rep': {}}
    months_out = []
    for key in series_keys:
        m    = monthly.get(key, blank)
        g    = _goal_for(goals, key)
        rev  = round(m['revenue'], 2)
        prev = months_out[-1]['revenue'] if months_out else None
        ly   = rev_by_month.get(_month_add(key, -12))
        months_out.append({
            'month':       key,
            'revenue':     rev,
            'jobs':        m['jobs'],
            'avg_deal':    round(rev / m['jobs']) if m['jobs'] else 0,
            'margin_pct':  _margin(m['trade_revenue'], m['trade_cost']),
            'retail':      round(m['retail'], 2),
            'insurance':   round(m['insurance'], 2),
            'commercial':  round(m.get('commercial', 0.0), 2),
            'sent':        m['sent'],
            'sent_value':  round(m['sent_value'], 2),
            'sent_won':    m['sent_won'],
            # Cohort close rate: of the estimates SENT this month, how many have
            # closed. Not signed/sent within the month — those are different sets.
            'close_rate':  round(m['sent_won'] / m['sent'] * 100) if m['sent'] else None,
            'by_rep':      {r: round(v, 2) for r, v in
                            sorted(m['by_rep'].items(), key=lambda x: -x[1])},
            'goal':        g['revenue'],
            'goal_jobs':   g['jobs'],
            'pct_to_goal': round(rev / g['revenue'] * 100) if g['revenue'] > 0 else None,
            'mom_pct':     round((rev - prev) / prev * 100) if prev else None,
            'yoy_pct':     round((rev - ly) / ly * 100) if ly else None,
        })

    # Current-month pace. Straight-line: a month is "on pace" when today's run
    # rate, extended to month end, clears the goal.
    days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
    days_elapsed  = now_dt.day
    days_left     = max(0, days_in_month - days_elapsed)
    cur_row       = next((r for r in months_out if r['month'] == cur_month), None)
    cur_rev       = cur_row['revenue'] if cur_row else 0.0
    cur_goal      = _goal_for(goals, cur_month)
    projected     = round(cur_rev / days_elapsed * days_in_month, 2) if days_elapsed else 0.0
    gap           = round(max(0.0, cur_goal['revenue'] - cur_rev), 2)
    current_month = {
        'month':            cur_month,
        'revenue':          cur_rev,
        'jobs':             cur_row['jobs'] if cur_row else 0,
        'goal':             cur_goal['revenue'],
        'goal_jobs':        cur_goal['jobs'],
        'pct':              round(cur_rev / cur_goal['revenue'] * 100) if cur_goal['revenue'] > 0 else None,
        'days_elapsed':     days_elapsed,
        'days_in_month':    days_in_month,
        'days_left':        days_left,
        'expected_to_date': round(cur_goal['revenue'] * days_elapsed / days_in_month, 2),
        'projected':        projected,
        'gap':              gap,
        'per_day_needed':   round(gap / days_left, 2) if days_left else gap,
        'on_pace':          (projected >= cur_goal['revenue']) if cur_goal['revenue'] > 0 else None,
    }

    # Per-rep progress against this month's goal. Rep names are matched
    # case-insensitively — goals are stored lowercase, but the salesperson field
    # on an estimate is whatever was typed, and "Luke" must not become a second
    # rep with its own goal.
    cur_by_rep = {}
    for r, v in (monthly.get(cur_month) or blank)['by_rep'].items():
        cur_by_rep[r.strip().lower()] = cur_by_rep.get(r.strip().lower(), 0.0) + v
    rep_month = []
    for name in ({r.strip().lower() for r in by_rep} | set(cur_by_rep)
                 | set(goals.get('reps') or {})):
        g   = _goal_for(goals, cur_month, name)
        rev = round(cur_by_rep.get(name, 0.0), 2)
        if g['revenue'] <= 0 and rev <= 0:
            continue
        rep_month.append({
            'rep':       name,
            'revenue':   rev,
            'goal':      g['revenue'],
            'pct':       round(rev / g['revenue'] * 100) if g['revenue'] > 0 else None,
            'projected': round(rev / days_elapsed * days_in_month, 2) if days_elapsed else 0.0,
            'gap':       round(max(0.0, g['revenue'] - rev), 2),
        })
    rep_month.sort(key=lambda r: -r['revenue'])

    # Trailing averages over COMPLETED months only — the current partial month
    # would drag every average down and make next month's goal look easy.
    closed = [r['revenue'] for r in months_out if r['month'] != cur_month]
    def _avg(n):
        w = closed[-n:]
        return round(sum(w) / len(w), 2) if w else 0.0
    best = max(months_out, key=lambda r: r['revenue'], default=None)
    benchmarks = {
        'avg_3':  _avg(3),
        'avg_6':  _avg(6),
        'avg_12': _avg(12),
        'best_month': ({'month': best['month'], 'revenue': best['revenue']}
                       if best and best['revenue'] > 0 else None),
    }

    avg_dtc_all = None
    dtc_all = [d['avg_days_to_close'] for d in by_rep.values() if d.get('avg_days_to_close') is not None]
    if dtc_all:
        avg_dtc_all = round(sum(dtc_all) / len(dtc_all), 1)

    return jsonify({
        'by_trade':       by_trade,
        'by_rep':         by_rep,
        'monthly':        months_out,
        'current_month':  current_month,
        'rep_month':      rep_month,
        'benchmarks':     benchmarks,
        'goals':          goals,
        'funnel':         funnel,
        'pipeline_aging': pipeline_aging,
        'by_type':        by_type,
        'top_cities':     top_cities_list,
        'ytd_revenue':    round(ytd_revenue, 2),
        'avg_days_to_close': avg_dtc_all,
    })


# Mirrors TRADE_COLOR_FIELDS in static/app.js — keep keys/labels in sync so the
# rep-facing Product Selection page and this customer-facing summary agree.
TRADE_COLOR_FIELDS = {
    'roofing': [('shingle_color', 'Shingle Color'), ('manufacturer', 'Manufacturer'), ('product_line', 'Product Line'),
                ('drip_edge_color', 'Drip Edge Color'), ('ridge_cap_color', 'Ridge Cap Color')],
    'siding':  [('siding_color', 'Siding Color'), ('trim_color', 'Trim Color'), ('manufacturer', 'Manufacturer')],
    'windows': [('frame_color', 'Frame Color'), ('glass_package', 'Glass Package')],
    'gutters': [('gutter_color', 'Gutter Color'), ('material', 'Material')],
    'commercial': [('membrane_color', 'Membrane Color'), ('manufacturer', 'Manufacturer'), ('system_type', 'System')],
    'other':   [('color', 'Color / Finish')],
}
_PRODUCT_TRADE_LABELS = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters',
                             commercial='Commercial', other='Other')


def _cv_products_block(est):
    """Brand/model/color choices per trade (shingle color, drip edge, ridge
    cap, gutter color, etc.) for the customer-facing view — mirrors the
    rep-facing Product Selection page. Returns '' when nothing is filled in.
    Note: page_visibility.products only gates the printed/PDF estimate (see
    buildPrintContent in app.js) — the interactive online link always shows
    whatever is filled in, same as notes/attachments/contract text."""
    trades = est.get('trades', {})
    rows = []
    for trade, fields in TRADE_COLOR_FIELDS.items():
        td = trades.get(trade) or {}
        if not td.get('enabled'):
            continue
        colors = td.get('colors') or {}
        for key, label in fields:
            v = (colors.get(key) or '').strip()
            if v:
                rows.append((trade, label, v))
    if not rows:
        return ''
    trs = ''.join(
        f'<tr><td class="cvprod-trade">{he(_PRODUCT_TRADE_LABELS.get(trade, trade.title()))}</td>'
        f'<td class="cvprod-label">{he(label)}</td>'
        f'<td class="cvprod-value">{he(value)}</td></tr>'
        for trade, label, value in rows
    )
    return f'''<div class="cvproducts">
      <h2 data-eyebrow="Specification">The Materials We&rsquo;ll Use</h2>
      <table class="cvprod-tbl"><tbody>{trs}</tbody></table>
    </div>'''


def _est_structures(est):
    """Buildings on a complex. Each carries its own measurements in the same
    flat key namespace as est['measurements'], so every calculator that takes a
    measurements dict runs on one building unchanged.

    Mirrors estStructures() in app.js. Pricing needs none of this - quantities
    are resolved in the browser and stored on the items - so the server reads
    structures only where it recalculates from measurements, which today is the
    fastening schedule on the production packet."""
    sts = est.get('structures')
    return [s for s in sts if isinstance(s, dict)] if isinstance(sts, list) else []


def _trade_structures(est, trade):
    return [s for s in _est_structures(est) if (s.get('trade') or 'commercial') == trade]


def _measurement_sets(est):
    """(building name, measurements) pairs the packet reports over: one per
    building on a complex, or one unnamed set for a single roof. A crew lays out
    corners from these numbers, so seven roofs need seven fastening schedules
    and seven sets of measurements, not the first roof's printed once."""
    sts = _trade_structures(est, 'commercial')
    if sts:
        return [(str(s.get('name') or '').strip(), s.get('measurements') or {})
                for s in sts]
    return [('', est.get('measurements') or {})]


def _with_section(item, name):
    """Append the item's estimate-section (structure / roof area) to its name
    for flat PDF lists (signed contract, production packet) that have no
    grouped headers: 'Shingles [Detached Garage]'."""
    s = (item.get('section') or '').strip()
    return f'{name} [{s}]' if s else name


def render_line_items(est, tier=None, only_trades=None):
    """Build trade line-item tables for customer view. Returns (html, grand_total).
    tier=None prices each trade at its own selected tier (mix-and-match; legacy
    docs resolve to the old single selected_tier). only_trades limits output."""
    pricing  = est.get('pricing', {})
    mode     = pricing.get('mode', 'margin')
    # Mirror the PDF's "Line Prices" chip: unit price + line total columns
    # appear online exactly when they appear in print.
    show_lp  = (est.get('page_visibility') or {}).get('linePrices') is True
    ncols    = 5 if show_lp else 3

    labels  = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters',
                   commercial='Commercial Roofing', other='Other / Misc')
    trades  = est.get('trades', {})
    parts   = []
    gtotal  = 0.0

    for tk in GBB_TRADES:
        td = trades.get(tk, {})
        if only_trades is not None and tk not in only_trades:
            continue
        if not td.get('enabled') or not td.get('line_items'):
            continue
        # Determine trade mode: gutters always simple; others default gbb
        trade_mode = _trade_mode(tk, td)
        t_tier = tier or _trade_tier(est, tk)
        r    = _tier_rate(pricing, tk, t_tier)

        # Priced entries first (item, qty, line_total, desc) — then grouped by
        # section (structures / roof areas; mirrors groupedTradeItems in app.js)
        priced = []
        for item in td['line_items']:
            qty  = float(item.get('quantity') or 0)
            if qty <= 0:
                continue  # zero-quantity items are hidden from the customer
            if trade_mode == 'simple':
                line = float(item.get('unit_price') or 0) * qty
                desc = (item.get('description') or '').strip()
            else:
                t    = (item.get('tiers') or {}).get(t_tier, {})
                if t.get('included') is False:
                    continue  # item excluded from this package tier
                line = _line_sell_total(item, t_tier, r, mode)
                desc = t.get('description', '')
            priced.append((item, qty, line, desc))

        sub = sum(line for _i, _q, line, _d in priced)
        sections = [s for s in (td.get('sections') or []) if s]
        known = set(sections)

        def _sec_of(item):
            s = (item.get('section') or '').strip()
            return s if s in known else ''
        groups = [('', [e for e in priced if _sec_of(e[0]) == ''])]
        groups += [(name, [e for e in priced if _sec_of(e[0]) == name]) for name in sections]

        rows = []
        hidden_count = 0
        for gname, entries in groups:
            if not entries:
                continue
            grows = []
            for item, qty, line, desc in entries:
                if not item.get('customer_visible', True):
                    hidden_count += 1
                    continue
                lp_cells = ''
                if show_lp:
                    unit_sell = (line / qty) if qty else 0.0
                    lp_cells = (f'<td class="cvr" data-l="Each">{fc(unit_sell)}</td>'
                                f'<td class="cvr" data-l="Total">{fc(line)}</td>')
                grows.append(f'''<tr>
              <td class="cvn">{he(item.get("name",""))}
                {'<div class="cvd">'+he(desc)+'</div>' if desc else ''}</td>
              <td class="cvc" data-l="Qty">{qty:g}</td>
              <td class="cvc">{he(item.get("unit",""))}</td>{lp_cells}</tr>''')
            if sections:
                rows.append(f'<tr class="cv-section-row"><td colspan="{ncols}">{he(gname or "General")}</td></tr>')
            rows.extend(grows)
            if sections:
                # Section subtotal includes customer-hidden items so the
                # section subtotals always sum to the trade subtotal.
                sec_tot = sum(line for _i, _q, line, _d in entries)
                rows.append(f'<tr class="cv-section-sub"><td colspan="{ncols - 1}">{he(gname or "General")} Subtotal</td>'
                            f'<td class="cvr">{fc(sec_tot)}</td></tr>')
        if hidden_count:
            rows.append(f'<tr><td colspan="{ncols}" class="cvhidden-note">Additional materials &amp; supplies included in total</td></tr>')
        if not rows:
            continue  # nothing priced to show the customer for this trade
        gtotal += sub
        lbl = labels.get(tk, tk.title())
        lp_ths = '<th scope="col" class="cvth-r">Unit Price</th><th scope="col" class="cvth-r">Total</th>' if show_lp else ''
        parts.append(f'''<div class="cvtrade">
          <div class="cvtrade-hd">{lbl}</div>
          <table class="cvt"><thead><tr>
            <th>Description</th><th scope="col" class="cvth-c">Qty</th>
            <th scope="col" class="cvth-c">Unit</th>{lp_ths}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
          <tfoot><tr><td colspan="{ncols - 1}" class="cvsub-l">{lbl} Subtotal</td>
            <td class="cvr cvsub">{fc(sub)}</td></tr></tfoot>
          </table></div>''')

    return '\n'.join(parts), gtotal


_CV_CSS = """
/* Palette sampled from logo.png, not eyeballed. The page shipped for years on
   #1a3a5c as "the brand navy" — a slate blue that appears nowhere in the mark,
   so the letterhead and everything under it were two different colors. --navy
   is the real one. Cyan is the single accent; gold and red survive only in the
   one footer stripe (the Colorado nod), and amber marks the selected package
   and nothing else on the page. */
:root{--navy:#082878;--navy2:#05184a;--navy3:#1a3f96;--ink:#0c1830;--mut:#5a6478;--faint:#8b93a4;
--line:#e3e0da;--bg:#faf9f7;--cyan:#00a8b8;--gold:#f8d000;--red:#b81010;--green:#16a34a;--amber:#e88400;
--serif:'Source Serif 4',Georgia,'Times New Roman',serif;
--r:10px;--sh:0 1px 2px rgba(12,24,48,.04),0 8px 24px -18px rgba(12,24,48,.18);
/* type scale — every size in this sheet used to be a bare literal, which is
   exactly how it drifted out of step with the printed estimate */
--fz-micro:10px;--fz-fine:12px;--fz-sm:13.5px;--fz-body:15px;--fz-lead:16.5px;
--fz-h3:19px;--fz-h2:24px;
/* space scale */
--sp-1:6px;--sp-2:10px;--sp-3:16px;--sp-4:22px;--sp-5:28px;--sp-6:40px;
--gut:16px}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{font-family:'InterDoc',system-ui,-apple-system,'Segoe UI',sans-serif;font-size:var(--fz-body);
color:var(--ink);background:var(--bg);min-height:100vh;
font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
img{max-width:100%}
body.cv-has-stick{padding-bottom:96px}

/* ── header ── */
.cvhdr{background:rgba(255,255,255,.94);position:relative;z-index:5;padding:12px clamp(14px,4vw,28px);
display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:1px solid var(--line)}
.cvhdr-logo-wrap{display:inline-flex;align-items:center}
.cvhdr img{height:50px;width:auto;display:block}
.cvhdr-contact{display:flex;flex-direction:column;align-items:flex-end;gap:4px;text-align:right}
.cvhdr-contact a{display:inline-flex;align-items:center;gap:7px;background:var(--navy);color:#fff;
font-weight:600;font-size:var(--fz-sm);text-decoration:none;padding:9px 16px;border-radius:999px;
transition:transform .15s}
.cvhdr-contact a:active{transform:scale(.96)}
.cvhdr-contact span{color:var(--faint);font-size:var(--fz-micro);letter-spacing:.6px;text-transform:uppercase}
/* The Colorado tricolor. It used to fire three times on one page — header,
   signature card and footer — which is two times too many for a device that
   is supposed to read as a signature. It now appears once, at the footer. */
.cvbrand-stripe{height:3px;background:linear-gradient(90deg,var(--cyan) 0 33.3%,var(--gold) 33.3% 66.6%,var(--red) 66.6% 100%)}
.cvhdr+.cvbrand-stripe{height:1px;background:var(--line)}

/* ── hero ── */
.cvhero{position:relative;background:var(--navy2);
color:#fff;padding:var(--sp-6) 20px;text-align:center;overflow:hidden}
.cvhero-brand{position:relative;font-size:var(--fz-micro);font-weight:600;letter-spacing:3px;text-transform:uppercase;color:var(--cyan);margin-bottom:14px}
.cvhero h1{position:relative;font-family:var(--serif);font-size:clamp(26px,5.5vw,38px);font-weight:600;letter-spacing:-.5px;margin-bottom:10px}
.cvhero p{position:relative;font-size:var(--fz-lead);opacity:.8;max-width:520px;margin:0 auto;line-height:1.6}
.cvhero.ok{background:#0d5c33}
.cvsteps{position:relative;display:flex;justify-content:center;gap:8px;margin-top:var(--sp-5);flex-wrap:wrap}
.cvstep{display:inline-flex;align-items:center;gap:8px;background:transparent;
border:1px solid rgba(255,255,255,.28);border-radius:999px;padding:6px 15px 6px 7px;font-size:var(--fz-fine);font-weight:500;color:rgba(255,255,255,.88)}
.cvstep b{display:inline-flex;width:20px;height:20px;border-radius:50%;background:var(--cyan);color:var(--navy2);
font-size:11px;font-weight:700;align-items:center;justify-content:center}

/* ── cover-photo hero ── */
.cvcover{position:relative;overflow:hidden;background:var(--navy2)}
.cvcover img{width:100%;height:min(500px,62vh);object-fit:cover;display:block}
.cvcover-shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,25,41,.25) 0%,rgba(10,25,41,.05) 40%,rgba(10,25,41,.9) 100%)}
.cvcover-text{position:absolute;left:0;right:0;bottom:0;padding:34px 20px 30px;text-align:center;color:#fff}
.cvcover-text h1{font-family:var(--serif);font-size:clamp(26px,5.5vw,38px);font-weight:600;letter-spacing:-.5px;margin-bottom:8px;text-shadow:0 2px 14px rgba(0,0,0,.55)}
.cvcover-text p{font-size:var(--fz-lead);opacity:.95;text-shadow:0 1px 6px rgba(0,0,0,.6)}
@media(max-width:520px){.cvcover img{height:340px}}
.cv-check{width:76px;height:76px;margin:0 auto 16px;border-radius:50%;background:rgba(255,255,255,.14);
border:2.5px solid rgba(255,255,255,.9);display:flex;align-items:center;justify-content:center;
font-size:38px;line-height:1;animation:cvpop .6s cubic-bezier(.34,1.56,.64,1) both}
@keyframes cvpop{from{transform:scale(.25);opacity:0}to{transform:scale(1);opacity:1}}
.cv-print-btn{margin-top:18px;background:rgba(255,255,255,.16);border:1.5px solid rgba(255,255,255,.55);
color:#fff;padding:11px 24px;border-radius:999px;font-size:14px;font-weight:700;font-family:inherit;cursor:pointer;transition:background .15s}
.cv-print-btn:hover{background:rgba(255,255,255,.28)}

/* ── layout + cards ── */
/* The gutter lives on .cvmain, not on each card. It used to be per-card
   `margin:16px 16px 0`, and the three blocks that forgot it — .cvdet, .cvdl,
   .cvvz — ran edge-to-edge on a phone while every neighbour sat inset. */
.cvmain{max-width:900px;margin:0 auto;padding:var(--sp-1) var(--gut) var(--sp-6)}
@media(min-width:720px){.cvmain{--gut:22px;padding:var(--sp-3) var(--gut) 64px}}
.cvc-card,.cvnotes,.cvproducts,.cvintro,.cvphotos,.cvcond,.cvnext,.cvrep-card{background:#fff;border:1px solid var(--line);
border-radius:var(--r);box-shadow:var(--sh);margin:var(--sp-3) 0 0;padding:var(--sp-5)}
.cvgrid{display:grid;grid-template-columns:1fr 1fr;gap:var(--sp-4) var(--sp-3)}
.cvgi label{font-size:var(--fz-micro);text-transform:uppercase;letter-spacing:1.2px;color:var(--faint);font-weight:600;display:block;margin-bottom:5px}
.cvgi strong{font-size:var(--fz-body);font-weight:500;color:var(--ink);line-height:1.4}
/* Section heading: a small caps teal eyebrow over a serif line. Replaces the
   11.5px/800 uppercase run-in, which was a SaaS pattern on a document that
   wants to read like a proposal. */
.cvnotes h2,.cvproducts h2,.cvphotos h2,.cvcond>h2,.cvnext h2,.cvrep-card h2,.cvtrust h2{display:block;
font-family:var(--serif);font-size:var(--fz-h3);font-weight:600;text-transform:none;letter-spacing:-.2px;
color:var(--navy);margin:0 0 var(--sp-3)}
/* Attribute-gated so a heading without an eyebrow doesn't render an empty box
   above itself. */
.cvmain h2[data-eyebrow]::before,.cvmain h3[data-eyebrow]::before{
content:attr(data-eyebrow);display:block;font-family:'InterDoc',system-ui,sans-serif;font-size:var(--fz-micro);
font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--cyan);margin-bottom:var(--sp-1)}

/* ── at a glance ── */
.cvglance-list{margin:0}
.cvglance-row{display:grid;grid-template-columns:118px 1fr;gap:var(--sp-3);
padding:13px 0;border-bottom:1px solid var(--line)}
.cvglance-row:last-child{border-bottom:none}
.cvglance-row:first-child{padding-top:0}
.cvglance-k{font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.2px;
color:var(--faint);padding-top:2px}
.cvglance-v{font-size:var(--fz-body);line-height:1.55;color:var(--ink);margin:0}
.cvglance-v strong{font-weight:600;color:var(--navy)}
@media(max-width:640px){.cvglance-row{grid-template-columns:1fr;gap:var(--sp-1)}}
.cvnotes p{font-size:var(--fz-body);line-height:1.75;color:var(--mut);white-space:pre-wrap}
.cvintro{padding:var(--sp-5)}
.cvintro-logo{height:34px;width:auto;display:block;margin-bottom:var(--sp-4)}
.cvintro p{font-family:var(--serif);font-size:var(--fz-lead);line-height:1.8;color:var(--ink);white-space:pre-wrap}

/* ── photos + lightbox ── */
.cvph-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.cvph-fig{margin:0}
.cvph-wrap{position:relative;border-radius:12px;overflow:hidden;background:#f1f5f9;cursor:zoom-in;box-shadow:0 2px 8px -2px rgba(15,23,42,.15)}
.cvph-wrap img{width:100%;display:block;transition:transform .35s ease}
.cvph-wrap:hover img{transform:scale(1.04)}
.cvph-canvas{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.cvph-fig figcaption{font-size:12px;color:var(--mut);padding:7px 3px 0;line-height:1.45}
@media(max-width:520px){.cvph-grid{grid-template-columns:1fr 1fr;gap:10px}}
.cvlb{position:fixed;inset:0;z-index:100;background:rgba(8,15,26,.93);display:none;flex-direction:column;
align-items:center;justify-content:center;padding:20px;opacity:0;transition:opacity .2s}
.cvlb.on{opacity:1}
.cvlb-body{position:relative;max-width:min(1100px,94vw)}
.cvlb-body img{max-width:94vw;max-height:80vh;width:auto;height:auto;border-radius:10px;display:block}
.cvlb-cap{color:#cbd5e1;font-size:13.5px;margin-top:12px;text-align:center;max-width:640px}
.cvlb-x{position:absolute;top:14px;right:14px;width:42px;height:42px;border-radius:50%;background:rgba(255,255,255,.14);
color:#fff;border:none;font-size:24px;line-height:1;cursor:pointer;z-index:2;font-family:inherit}

/* ── product selection ── */
.cvprod-tbl{width:100%;border-collapse:collapse;font-size:13.5px}
.cvprod-tbl tr{border-bottom:1px solid #f1f5f9}
.cvprod-tbl tr:last-child{border-bottom:none}
.cvprod-tbl td{padding:8px 6px}
.cvprod-trade{color:var(--navy);font-weight:800;font-size:10px;text-transform:uppercase;letter-spacing:.5px;width:84px;white-space:nowrap}
.cvprod-label{color:var(--mut);width:150px}
.cvprod-value{font-weight:700;color:var(--ink)}
@media(max-width:480px){.cvprod-trade,.cvprod-label{width:auto}.cvprod-tbl td{padding:6px 4px;font-size:12.5px}}

/* ── package (tier) cards ── */
/* No hover lift, no pastel fills, no drop shadow bloom. Three cards competing
   with color and motion made the choice feel like a pricing page; the choice
   now reads through one amber rule on the selected card and nothing else. */
.cv-tier-section{margin:var(--sp-1) 0 0}
.cv-tier-heading{display:block;margin:0;font-family:var(--serif);font-size:var(--fz-h3);font-weight:600;text-transform:none;
letter-spacing:-.2px;color:var(--navy);padding:var(--sp-5) 0 var(--sp-3)}
.cv-tier-heading::before{content:attr(data-eyebrow);display:block;font-family:'InterDoc',system-ui,sans-serif;
font-size:var(--fz-micro);font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--cyan);margin-bottom:var(--sp-1)}
.cv-tier-cards{display:grid;gap:var(--sp-2);margin-bottom:var(--sp-1)}
.cv-tier-card{border:1px solid var(--line);border-top:2px solid var(--line);border-radius:var(--r);
padding:var(--sp-5) var(--sp-3) var(--sp-4);text-align:left;cursor:pointer;
transition:border-color .18s,background .18s;background:#fff;position:relative;
-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
.cv-tier-card:hover{border-color:var(--mut)}
.cv-tier-card.cv-tier-selected{border-color:var(--line);border-top-color:var(--amber);background:#fff}
.cv-tier-popular{position:absolute;top:-1px;right:var(--sp-3);background:transparent;
color:var(--green);font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.4px;padding:var(--sp-1) 0;
white-space:nowrap}
.cv-tier-name{font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.6px;margin-bottom:var(--sp-2)}
.cv-tier-system{font-size:var(--fz-sm);font-weight:500;color:var(--ink);margin-bottom:var(--sp-1);line-height:1.4}
.cv-tier-price{font-family:var(--serif);font-size:30px;font-weight:600;letter-spacing:-.5px;margin-bottom:var(--sp-1);font-variant-numeric:tabular-nums}
.cv-tier-desc{font-size:var(--fz-fine);color:var(--mut);margin-bottom:var(--sp-2);line-height:1.55}
.cv-tier-feats{list-style:none;margin:var(--sp-3) 0 var(--sp-1);padding:var(--sp-3) 0 0;border-top:1px solid var(--line);text-align:left;
font-size:var(--fz-fine);color:var(--mut);line-height:1.6}
.cv-tier-feats li{position:relative;padding:3px 0 3px 16px}
.cv-tier-feats li::before{content:'';position:absolute;left:0;top:11px;width:5px;height:1px;background:var(--faint)}
.cv-tier-feats .cv-tier-more{color:var(--faint);font-style:italic}
.cv-tier-feats .cv-tier-more::before{content:none}
.cv-tier-check{font-size:var(--fz-fine);font-weight:600;color:var(--mut);border:1px solid var(--line);border-radius:999px;
padding:8px 18px;display:inline-block;margin-top:var(--sp-3);transition:all .15s;background:#fff;letter-spacing:.3px}
.cv-tier-selected .cv-tier-check{background:var(--navy);border-color:var(--navy);color:#fff}

/* ── line-item tables ── */
.cvtrade{margin:var(--sp-3) 0 0;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh);background:#fff}
.cvtrade-hd{background:#fff;color:var(--navy);padding:var(--sp-4) var(--sp-4) var(--sp-2);
font-family:var(--serif);font-size:var(--fz-h3);font-weight:600;letter-spacing:-.2px;text-transform:none}
.cvt{width:100%;border-collapse:collapse;background:#fff}
.cvt th{padding:var(--sp-2) var(--sp-4);text-align:left;font-size:var(--fz-micro);text-transform:uppercase;letter-spacing:1.2px;background:#fff;
border-bottom:1px solid var(--navy);color:var(--faint);font-weight:600}
.cvth-c{width:52px;text-align:center !important}
.cvth-r{width:96px;text-align:right !important}
.cvt td{padding:13px var(--sp-4);border-bottom:1px solid var(--line);font-size:var(--fz-sm);vertical-align:top}
.cvt tbody tr:last-child td{border-bottom:none}
.cvn{font-weight:500;color:var(--ink)}
/* pre-wrap on both description cells: reps type multi-line line-item
   descriptions on the Pricing tabs, and plain HTML would collapse every one of
   those newlines into a space. */
.cvd{font-size:var(--fz-fine);color:var(--mut);font-weight:400;margin-top:4px;line-height:1.55;white-space:pre-wrap}
.cvc{text-align:center;color:var(--mut)}
.cvc-desc{font-weight:400;color:var(--mut);white-space:pre-wrap}
.cvr{text-align:right;font-weight:500;font-variant-numeric:tabular-nums;white-space:nowrap}
.cvt tfoot td{background:#fff;font-weight:600;padding:var(--sp-3) var(--sp-4);border-top:1px solid var(--navy);font-size:var(--fz-sm)}
.cvsub-l{text-align:right;color:var(--faint);font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.2px;padding-right:12px}
.cvsub{color:var(--navy);font-size:var(--fz-lead)}
.cvhidden-note{font-size:var(--fz-fine);color:var(--faint);font-style:italic;padding:var(--sp-2) var(--sp-4);text-align:left}
/* No tinted band; a rule and small caps carry the grouping just as well and
   don't stripe the table. */
.cv-section-row td{background:#fff!important;color:var(--navy);font-weight:600;font-size:var(--fz-micro);
text-transform:uppercase;letter-spacing:1.4px;padding:var(--sp-4) var(--sp-4) var(--sp-1)!important;border-bottom:1px solid var(--line)}
.cv-section-sub td{background:#fff;font-weight:600;font-size:var(--fz-fine);color:var(--mut);
border-bottom:1px solid var(--line);padding:var(--sp-2) var(--sp-4)}
.cv-section-sub td:first-child{text-align:right}

/* ── grand total ── */
/* The one filled element on the page. Everything above it got quieter so this
   is where the eye stops. */
.cvgrand{margin:var(--sp-3) 0 0;background:var(--navy);color:#fff;
padding:var(--sp-5);border-radius:var(--r);display:flex;justify-content:space-between;align-items:baseline;gap:12px;
position:relative;overflow:hidden;flex-wrap:wrap}
.cvgrand-lbl{font-size:var(--fz-micro);font-weight:600;letter-spacing:1.8px;text-transform:uppercase;color:rgba(255,255,255,.72)}
.cvgrand-amt{font-family:var(--serif);font-size:clamp(26px,6vw,34px);font-weight:600;letter-spacing:-.5px;font-variant-numeric:tabular-nums}

/* ── contract / terms ── */
.cvcontract{margin:var(--sp-3) 0 0;background:#fff;border-radius:var(--r);border:1px solid var(--line);overflow:hidden;box-shadow:var(--sh)}
.cvcontract summary{padding:15px 18px;cursor:pointer;font-weight:700;font-size:13.5px;color:var(--navy);list-style:none;
display:flex;align-items:center;justify-content:space-between;gap:10px}
.cvcontract summary::-webkit-details-marker{display:none}
.cvcontract summary::after{content:'▾';color:var(--faint);transition:transform .2s}
.cvcontract[open] summary::after{transform:rotate(180deg)}
.cvcontract[open] summary{border-bottom:1px solid var(--line)}
.cvcontract-body{padding:var(--sp-3) var(--sp-4);font-size:var(--fz-fine);line-height:1.8;color:var(--mut);white-space:pre-wrap;
max-height:300px;overflow-y:auto;background:#fff;border-top:1px solid var(--line)}

/* ── signature area ── */
/* Keeps its elevation — this should be the most important object on the page —
   but loses the tricolor bar (now the footer's alone) and the three differently
   tinted sub-panels, which made one form look like three unrelated widgets. */
.cvsig{margin:var(--sp-5) 0 0;padding:var(--sp-5);background:#fff;border-radius:var(--r);border:1px solid var(--line);
box-shadow:0 18px 44px -22px rgba(12,24,48,.3);position:relative;overflow:hidden}
.cvsig::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--navy)}
.cvsig h2{font-family:var(--serif);font-size:var(--fz-h2);font-weight:600;color:var(--navy);letter-spacing:-.4px;margin-bottom:var(--sp-1)}
.cvsig .sub{font-size:var(--fz-sm);color:var(--mut);margin-bottom:var(--sp-5);line-height:1.65}
.cvfield{display:block;margin-bottom:var(--sp-3)}
.cvfield>span{display:block;font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:var(--faint);margin-bottom:var(--sp-1)}
.cvfield em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--faint);font-weight:400}
/* 16px is deliberate — anything smaller makes iOS zoom the page on focus. */
.cvinput{width:100%;border:1px solid #d7dfe9;border-radius:8px;padding:13px 15px;font-size:16px;font-family:inherit;
outline:none;color:var(--ink);background:#fff;transition:border-color .15s,box-shadow .15s}
.cvinput:focus{border-color:var(--navy);box-shadow:0 0 0 3px rgba(8,40,120,.1);background:#fff}
.cv-sigpad{border:1px dashed #c3ccda;border-radius:8px;background:#fff;padding:var(--sp-3) var(--sp-3) var(--sp-2);text-align:center;margin:var(--sp-1) 0 var(--sp-3)}
#cv-sig-script{font-family:'Great Vibes','Segoe Script',cursive;font-size:38px;line-height:1.25;color:var(--navy);
min-height:48px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cv-sigpad-hint{font-size:var(--fz-micro);color:var(--faint);border-top:1px solid var(--line);margin-top:var(--sp-1);padding-top:var(--sp-2);
text-transform:uppercase;letter-spacing:1.2px;font-weight:600}
.cvagree{display:flex;align-items:flex-start;gap:11px;font-size:var(--fz-sm);color:var(--mut);margin-bottom:var(--sp-3);line-height:1.6;
cursor:pointer;background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px}
.cvagree input{margin-top:1px;flex-shrink:0;width:19px;height:19px;cursor:pointer;accent-color:var(--navy)}
.cvbtn{width:100%;padding:17px;background:var(--navy);color:#fff;border:none;border-radius:8px;
font-size:var(--fz-lead);font-weight:600;font-family:inherit;cursor:pointer;margin-bottom:var(--sp-3);letter-spacing:.2px;
transition:background .15s,transform .15s}
.cvbtn:hover{background:var(--navy3)}
.cvbtn:active{transform:scale(.99)}
.cvlegal{font-size:var(--fz-micro);color:var(--faint);text-align:center;line-height:1.7}
.cv-shingle,.cv-siding,.cv-initials{background:#fff;border:1px solid var(--line);border-radius:8px;padding:var(--sp-3);margin-bottom:var(--sp-3)}
.cv-shingle-label,.cv-siding-label,.cv-initials-title{font-size:var(--fz-micro);font-weight:600;color:var(--faint);
text-transform:uppercase;letter-spacing:1.2px;margin-bottom:var(--sp-2)}
.cv-shingle-locked,.cv-siding-locked{font-size:var(--fz-lead);font-weight:500;color:var(--navy)}
.cv-shingle-select,.cv-siding-select{margin-bottom:0;background:#fff}
.cv-initial-row{display:flex;align-items:center;gap:12px;padding:var(--sp-2) 0;border-top:1px solid var(--line)}
.cv-initial-row:first-of-type{border-top:none}
.cv-initial-text{flex:1;font-size:13px;color:#33415a;line-height:1.5}
.cv-initial-box{width:82px;flex-shrink:0;border:2px solid var(--navy);border-radius:9px;padding:10px 8px;font-size:16px;
font-weight:800;text-align:center;text-transform:uppercase;outline:none;color:var(--navy);background:#fff;font-family:inherit}
.cv-initial-box:focus{box-shadow:0 0 0 4px rgba(26,58,92,.14)}

/* ── what happens next ── */
.cvnext-list{list-style:none;margin:2px 0 0;padding:0}
.cvnext-it{position:relative;padding:0 0 18px 46px}
.cvnext-it:last-child{padding-bottom:2px}
.cvnext-it::after{content:'';position:absolute;left:15px;top:34px;bottom:4px;width:2px;background:var(--line)}
.cvnext-it:last-child::after{display:none}
.cvnext-n{position:absolute;left:0;top:0;width:30px;height:30px;border-radius:50%;
background:#fff;border:1px solid var(--line);color:var(--navy);font-weight:600;font-size:var(--fz-sm);
display:flex;align-items:center;justify-content:center}
.cvnext-t{font-weight:600;font-size:var(--fz-body);color:var(--ink);padding-top:4px;margin-bottom:4px}
.cvnext-d{font-size:var(--fz-sm);color:var(--mut);line-height:1.65}

/* ── your consultant ── */
.cvrep{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.cvrep-av{width:52px;height:52px;border-radius:50%;background:var(--navy);color:#fff;
font-family:var(--serif);font-weight:600;font-size:19px;display:flex;align-items:center;justify-content:center;letter-spacing:.5px;flex-shrink:0}
.cvrep-info{min-width:130px}
.cvrep-name{font-weight:600;font-size:var(--fz-lead);color:var(--ink)}
.cvrep-role{font-size:var(--fz-fine);color:var(--mut);margin-top:2px}
.cvrep-btns{display:flex;gap:8px;flex:1 1 100%;margin-top:6px}
@media(min-width:560px){.cvrep-btns{flex:0 0 auto;margin-top:0;margin-left:auto}}
.cvrep-btn{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:12px 16px;border-radius:8px;
font-size:var(--fz-sm);font-weight:600;text-decoration:none;border:1px solid var(--line);color:var(--navy);background:#fff;
white-space:nowrap;transition:transform .15s,background .15s}
.cvrep-btn:active{transform:scale(.97)}
.cvrep-btn.pri{background:var(--navy);border-color:var(--navy);color:#fff}

/* ── sticky sign bar ── */
.cvstick{position:fixed;left:0;right:0;bottom:0;z-index:60;padding:10px 12px calc(10px + env(safe-area-inset-bottom));
transform:translateY(130%);transition:transform .35s cubic-bezier(.22,.61,.36,1);pointer-events:none}
.cvstick.on{transform:none;pointer-events:auto}
.cvstick-in{max-width:660px;margin:0 auto;background:rgba(5,24,74,.97);backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,.1);border-radius:12px;box-shadow:0 20px 44px -16px rgba(5,24,74,.6);
display:flex;align-items:center;gap:14px;padding:12px 12px 12px 18px;color:#fff}
.cvstick-t{display:flex;flex-direction:column;min-width:0}
/* max-width:46vw ellipsised "Roofing: Better - Siding: Best" almost immediately
   on a 375px phone, and that label is the half that says WHAT is being priced.
   Two lines cost nothing. */
.cvstick-lbl{font-size:var(--fz-micro);font-weight:600;letter-spacing:1.2px;text-transform:uppercase;opacity:.62;
line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cvstick-amt{font-family:var(--serif);font-size:21px;font-weight:600;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
.cvstick-btn{margin-left:auto;background:#fff;color:var(--navy);border:none;border-radius:8px;
padding:13px 18px;font-size:var(--fz-sm);font-weight:600;font-family:inherit;cursor:pointer;white-space:nowrap;
transition:transform .15s}
.cvstick-btn:active{transform:scale(.96)}

/* ── attachments ── */
.cv-att-list{display:flex;flex-direction:column;gap:10px}
.cv-att{display:inline-flex;align-items:center;gap:9px;background:#fff;border:1px solid var(--line);border-radius:8px;
padding:13px 16px;font-size:var(--fz-sm);font-weight:600;color:var(--navy);text-decoration:none;transition:border-color .15s}
.cv-att:hover{border-color:var(--mut)}
.cv-att-doc{display:flex;flex-direction:column;gap:10px;margin-bottom:8px}
.cv-att-doc-title{font-family:var(--serif);font-size:var(--fz-lead);font-weight:600;color:var(--navy)}
.cv-att-page{width:100%;display:block;border:1px solid var(--line);border-radius:6px}

/* ── signed certificate ── */
.cvinit-tbl td:first-child{width:auto;text-transform:none;letter-spacing:0;font-size:12px;color:#33415a;font-weight:500}
.cvinit-val{font-weight:800!important;color:var(--navy)!important;text-transform:uppercase;width:70px!important;text-align:right}
.cert{margin:16px;background:#fff;border:1.5px solid var(--navy);border-radius:var(--r);padding:20px;box-shadow:var(--sh)}
.cert-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1.5px;color:var(--navy);
margin-bottom:13px;padding-bottom:10px;border-bottom:2px solid var(--navy)}
.cert-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:10px}
.cert-tbl td{padding:5px 0;vertical-align:top}
.cert-tbl td:first-child{color:var(--mut);font-weight:700;width:110px;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.cert-tbl td:last-child{font-weight:600;color:var(--ink);word-break:break-all}
.cert-legal{font-size:10px;color:var(--faint);line-height:1.6;border-top:1px solid #f1f5f9;padding-top:10px}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:10px}

/* ── footer ── */
.cvftr{position:relative;text-align:center;padding:44px 18px 52px;background:var(--navy2);color:rgba(255,255,255,.55);
line-height:1.7;margin-top:var(--sp-6)}
.cvftr::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;
background:linear-gradient(90deg,var(--cyan) 0 33.3%,var(--gold) 33.3% 66.6%,var(--red) 66.6% 100%)}
.cvftr-logo{height:40px;width:auto;background:#fff;padding:8px 16px;border-radius:8px;margin-bottom:var(--sp-3)}
.cvftr strong{color:#fff;font-family:var(--serif);font-size:var(--fz-h3);font-weight:600;display:block;margin-bottom:var(--sp-1);letter-spacing:0}
.cvftr-c{font-size:var(--fz-sm)}
.cvftr-c a{color:rgba(255,255,255,.78);text-decoration:none;font-weight:600}
.cvftr-sub{font-size:10.5px;margin-top:10px;opacity:.7}

/* ── condition report ── */
.cvcond-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:8px;margin-bottom:12px}
.cvcond-cell{text-align:left;border:none;border-top:1px solid var(--line);border-radius:0;padding:var(--sp-2) 0 0;background:#fff}
.cvcond-cell-lbl{font-size:var(--fz-micro);font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:1.2px;margin-bottom:var(--sp-1);line-height:1.35}
.cvcond-letter{font-family:var(--serif);font-size:28px;font-weight:600;width:auto;height:auto;line-height:1.1;border-radius:0;margin:0 0 3px;background:none!important}
.cvcond-word{font-size:var(--fz-fine);font-weight:500;color:var(--mut)}
.cvcond-exec{font-family:var(--serif);font-size:var(--fz-body);line-height:1.75;color:var(--ink);background:#fff;border-left:2px solid var(--cyan);
padding:2px 0 2px var(--sp-3);border-radius:0;margin-bottom:var(--sp-3)}
.cvcond-sec{margin-top:14px;border-top:1px solid var(--line);padding-top:13px}
.cvcond-sec-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.cvcond-sec-hd h4{font-family:var(--serif);font-size:var(--fz-lead);font-weight:600;color:var(--navy);margin:0}
.cvcond-badge{font-size:var(--fz-micro);font-weight:600;border-radius:999px;padding:4px 11px;white-space:nowrap;letter-spacing:1px;text-transform:uppercase;background:none!important;border:1px solid currentColor}
.cvcond-meta{font-size:11.5px;color:var(--mut);margin-bottom:6px}
.cvcond-summary{font-size:12.5px;line-height:1.65;color:#33415a;margin-bottom:8px}
.cvcond-sh{font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.4px;color:var(--faint);margin:var(--sp-3) 0 var(--sp-1)}
.cvcond-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px}
.cvcond-tbl th{text-align:left;font-size:var(--fz-micro);text-transform:uppercase;letter-spacing:1.2px;color:var(--faint);
background:#fff;padding:var(--sp-1) 8px;border-bottom:1px solid var(--navy)}
.cvcond-tbl th:first-child,.cvcond-tbl td:first-child{padding-left:0}
.cvcond-tbl td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.55}
.cvcond-tbl tr:last-child td{border-bottom:none}
.cvcond-cost-total td{font-weight:600;color:var(--navy);border-top:1px solid var(--navy);background:#fff}
.cvcond-foot{font-size:var(--fz-micro);color:var(--faint);line-height:1.7;margin-top:var(--sp-3);border-top:1px solid var(--line);padding-top:var(--sp-2)}
.cvcond .cvph-grid{margin-top:10px}

/* ── trust blocks ── */
.cvtrust-body p{font-size:var(--fz-body);line-height:1.75;color:var(--mut);margin-bottom:var(--sp-2)}
.cvtrust-body p:last-child{margin-bottom:0}
.cvtrust-certs{list-style:none;margin:0;padding:0}
.cvtrust-certs li{position:relative;padding:var(--sp-2) 0;padding-left:20px;font-size:var(--fz-sm);color:var(--ink);
line-height:1.6;border-bottom:1px solid var(--line)}
.cvtrust-certs li:last-child{border-bottom:none}
.cvtrust-certs li::before{content:'';position:absolute;left:0;top:19px;width:8px;height:1px;background:var(--cyan)}
.cvtrust-revs{display:grid;gap:var(--sp-4)}
@media(min-width:640px){.cvtrust-revs{grid-template-columns:1fr 1fr;gap:var(--sp-4) var(--sp-5)}}
.cvtrust-rev{background:#fff;border:none;border-left:1px solid var(--line);border-radius:0;padding:0 0 0 var(--sp-3)}
.cvtrust-rev-stars{color:var(--amber);font-size:var(--fz-sm);letter-spacing:2px;margin-bottom:var(--sp-1)}
.cvtrust-rev-text{font-family:var(--serif);font-size:var(--fz-body);line-height:1.7;color:var(--ink);font-style:normal}
.cvtrust-rev-name{font-size:var(--fz-micro);font-weight:600;color:var(--faint);margin-top:var(--sp-2);
text-transform:uppercase;letter-spacing:1.2px}

/* ── permits & code ── */
.cvperm-name{font-family:var(--serif);font-size:var(--fz-h2);font-weight:600;color:var(--navy);
line-height:1.25;margin-bottom:var(--sp-1)}
.cvperm-name.cvperm-unknown{font-size:var(--fz-h3);color:var(--mut)}
.cvperm-meta{font-size:var(--fz-sm);color:var(--mut);line-height:1.65}
.cvperm-lead{font-size:var(--fz-sm);color:var(--mut);line-height:1.7;margin-top:var(--sp-3);max-width:56ch}
.cvperm-meta a{color:var(--navy);text-decoration:none;border-bottom:1px solid var(--line)}
.cvperm-sub{margin-top:var(--sp-4);padding-top:var(--sp-3);border-top:1px solid var(--line)}
.cvperm-h{font-size:var(--fz-micro);font-weight:600;text-transform:uppercase;letter-spacing:1.4px;
color:var(--faint);margin:0 0 var(--sp-2)}
.cvperm-p{font-size:var(--fz-sm);color:var(--ink);line-height:1.7}
.cvperm-p a,.cvperm-list a{color:var(--navy);font-size:var(--fz-fine)}
.cvperm-list{list-style:none;margin:0;padding:0}
.cvperm-list li{position:relative;padding:6px 0 6px 16px;font-size:var(--fz-sm);color:var(--ink);
line-height:1.6}
.cvperm-list li::before{content:'';position:absolute;left:0;top:15px;width:7px;height:1px;background:var(--cyan)}
.cvperm-list.cvperm-muted li{color:var(--mut);font-size:var(--fz-fine)}
.cvperm-list.cvperm-muted li::before{background:var(--faint)}
.cvperm-basis{display:block;font-style:normal;color:var(--faint);font-size:var(--fz-fine);margin-top:2px}
.cvperm-stamp{margin-top:var(--sp-3);font-size:var(--fz-fine);color:var(--mut)}

/* ── estimate details block (AI-readable "About This Estimate") ── */
/* Horizontal margin removed on purpose: .cvmain owns the gutter now. This
   card, .cvdl and .cvvz were the three that ran edge-to-edge on a phone. */
.cvdet{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-5);margin:var(--sp-3) 0 0;
  box-shadow:var(--sh)}
.cvdet-hd{display:block;margin:0 0 var(--sp-1);font-family:var(--serif);font-size:var(--fz-h3);font-weight:600;color:var(--navy)}
.cvdet-lead{font-size:var(--fz-sm);color:var(--mut);line-height:1.65;margin-bottom:var(--sp-4)}
.cvdet section{border-top:1px solid var(--line);padding-top:var(--sp-4);margin-top:var(--sp-4)}
.cvdet section:first-of-type{border-top:none;padding-top:0;margin-top:0}
.cvdet h4{font-size:var(--fz-micro);font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:1.4px;margin-bottom:var(--sp-2)}
.cvdet p{font-size:var(--fz-sm);line-height:1.7;color:var(--mut);margin-bottom:var(--sp-1)}
.cvdet ul{list-style:none;padding:0;margin:var(--sp-1) 0}
.cvdet ul li{position:relative;padding:5px 0 5px 18px;font-size:var(--fz-sm);color:var(--mut);line-height:1.6}
.cvdet ul li::before{content:'';position:absolute;left:0;top:14px;width:6px;height:1px;background:var(--faint)}
.cvdet ul.chk li::before{background:var(--cyan)}
.cvdet .cvdet-tiers{display:grid;gap:10px;margin-top:6px}
@media(min-width:640px){.cvdet .cvdet-tiers{grid-template-columns:repeat(3,1fr)}}
.cvdet-tier{background:#fff;border:none;border-top:1px solid var(--line);border-radius:0;padding:var(--sp-2) 0 0}
.cvdet-tier-lbl{font-size:var(--fz-micro);font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--faint)}
.cvdet-tier-name{font-family:var(--serif);font-size:var(--fz-lead);font-weight:600;color:var(--navy);margin-top:3px}
.cvdet-tier-tag{font-size:var(--fz-fine);line-height:1.55;color:var(--mut);margin-top:var(--sp-1)}
.cvdet-tier ul{margin-top:8px}
.cvdet-tier ul li{font-size:12.5px}
.cvdet-code{background:#fff;border:1px solid var(--line);border-radius:8px;padding:var(--sp-3);margin-top:var(--sp-2)}
.cvdet-code strong{color:var(--navy);font-weight:600}
.cvdet-code-item{font-size:var(--fz-fine);color:var(--mut);padding:var(--sp-1) 0;border-bottom:1px solid var(--line)}
.cvdet-code-item:last-child{border-bottom:none}
.cvdet-code-item em{color:var(--faint);font-style:normal;font-size:var(--fz-micro);margin-left:6px}
.cvdet-vent{background:#fff;border:1px solid var(--line);border-left:2px solid var(--cyan);border-radius:0;padding:var(--sp-2) var(--sp-3);font-size:var(--fz-sm);line-height:1.65;color:var(--mut)}
.cvdet-vent strong{color:var(--navy);font-weight:600}
.cvdet-warr{display:grid;gap:var(--sp-3);margin-top:var(--sp-1)}
@media(min-width:640px){.cvdet-warr{grid-template-columns:repeat(3,1fr)}}
.cvdet-warr div{background:#fff;border:none;border-top:1px solid var(--line);border-radius:0;padding:var(--sp-2) 0 0}
.cvdet-warr div b{display:block;font-size:var(--fz-micro);letter-spacing:1.4px;text-transform:uppercase;color:var(--faint);margin-bottom:var(--sp-1);font-weight:600}

/* ── download PDF card ── */
.cvdl{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-4);margin:var(--sp-3) 0 0;
  display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--sp-3);
  box-shadow:var(--sh)}
.cvdl-t{flex:1 1 260px;min-width:0}
.cvdl-h{font-family:var(--serif);font-size:var(--fz-lead);font-weight:600;color:var(--navy);margin-bottom:3px}
.cvdl-d{font-size:var(--fz-sm);color:var(--mut);line-height:1.6}
.cvdl-btn{display:inline-flex;align-items:center;gap:7px;background:#fff;border:1px solid var(--navy);color:var(--navy);
  font-weight:600;font-size:var(--fz-sm);text-decoration:none;padding:11px 18px;border-radius:8px;transition:background .15s,color .15s;
  white-space:nowrap}
.cvdl-btn:hover{background:var(--navy);color:#fff}
@media print{.cvdl{display:none}}

/* ── scroll-reveal ── */
@media(prefers-reduced-motion:no-preference){
.cv-reveal{opacity:0;transform:translateY(18px);transition:opacity .6s cubic-bezier(.22,.61,.36,1),transform .6s cubic-bezier(.22,.61,.36,1)}
.cv-reveal.cv-in{opacity:1;transform:none}}

/* ── mobile ── */
@media(max-width:640px){
.cvgrid{grid-template-columns:1fr;gap:12px}
.cv-tier-cards{grid-template-columns:1fr !important}
.cvhdr img{height:40px}
.cvhdr-contact a{font-size:12.5px;padding:8px 13px}
.cvhdr-contact span{display:none}
.cvgrand-amt{font-size:22px}
.cvt thead{display:none}
.cvt tr{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:12px;row-gap:2px;padding:11px 14px;border-bottom:1px solid #f1f5f9}
.cvt tbody tr:last-child{border-bottom:none}
.cvt td{display:inline-block;border:none !important;padding:0 !important;font-size:13px}
.cvt td.cvn{flex:1 1 100%;padding-bottom:1px !important}
.cvt td.cvc-desc{flex:1 1 100%}
.cvt td[data-l]::before{content:attr(data-l);color:var(--faint);font-weight:600;font-size:9.5px;text-transform:uppercase;
letter-spacing:1px;margin-right:5px}
.cvt td.cvr:last-child{margin-left:auto}
.cvt tfoot tr{display:flex;justify-content:space-between;align-items:center;background:#f8fafc;border-top:2px solid var(--line);padding:11px 14px}
.cvt tfoot td{background:transparent;border-top:none !important;padding:0 !important}
.cvt tr.cv-section-row{background:#eef4fb;padding:7px 14px}
.cvt tr.cv-section-row td{background:transparent !important;padding:0 !important}
.cvt tr.cv-section-sub{background:#f8fafc;justify-content:space-between;padding:7px 14px}
.cvt tr.cv-section-sub td{background:transparent;border:none;padding:0 !important}
}
@media print{
.cv-print-btn,.cvstick,.cvlb,.cvhdr-contact a,.cvrep-btns{display:none}
body{background:#fff;padding-bottom:0 !important}
.cvc-card,.cvnotes,.cvtrade,.cvsig,.cert,.cvnext,.cvrep-card{box-shadow:none;break-inside:avoid}
.cvhero,.cvhero.ok{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.cert{border-width:1.5pt}
.cvcontract,.cvcontract[open]{break-inside:auto}
.cvcontract summary{display:none}
.cvcontract-body{max-height:none !important;overflow:visible !important;padding:16px 18px}
/* A trade table taller than one page HAS to break, and the browser's default
   for a split table is to repeat <tfoot> on every fragment — so the customer's
   PDF printed "Roofing Subtotal $10,101.84" at the foot of the page, then the
   remaining items, then the same subtotal again, as if there were two of them.
   table-row-group puts the footer back in normal flow: it prints once, at the
   real end. <thead> still repeats across pages, which is what we want — the
   continued rows keep their Description / Qty / Unit headings. */
.cvt tfoot{display:table-row-group}
/* Where the break lands, if it must land inside a table: never through the
   middle of a line item, never between a subtotal and the rows it sums, and
   never leaving a heading stranded alone at the bottom of a page. */
.cvt tr{break-inside:avoid}
.cvt thead{break-after:avoid}
.cvt tr.cv-section-row{break-after:avoid}
.cvt tfoot tr,.cvt tr.cv-section-sub{break-before:avoid}
.cvtrade-hd{break-after:avoid}
.cvgrand{break-inside:avoid}}
"""

# Shared client-side behavior for every public customer page: scroll-reveal,
# the sticky review-&-sign bar, the live signature preview, and the photo
# lightbox. Plain string (no f-string) so JS braces stay literal.
_CV_SHARED_JS = """
(function(){
var rm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
/* scroll-reveal — tier line-item wrappers are excluded: they toggle
   display:none and must never be left invisible by a missed observation */
if(!rm&&'IntersectionObserver' in window){
  var els=[].filter.call(document.querySelectorAll('.cvmain>*'),function(el){
    return !(el.id&&el.id.indexOf('tier-items-')===0);});
  var io=new IntersectionObserver(function(es){es.forEach(function(e){
    if(e.isIntersecting){e.target.classList.add('cv-in');io.unobserve(e.target);}});},
    {rootMargin:'0px 0px -8% 0px'});
  els.forEach(function(el){el.classList.add('cv-reveal');io.observe(el);});
  /* safety net: a signing page must never stay invisible if the observer
     is starved (odd embedded webviews) — force-reveal everything shortly */
  setTimeout(function(){els.forEach(function(el){el.classList.add('cv-in');});},1400);
}
/* sticky review-&-sign bar (plain rect math — no observer dependency) */
var stick=document.getElementById('cvstick'),sigBox=document.querySelector('.cvsig');
if(stick&&sigBox){
  document.body.classList.add('cv-has-stick');
  var hero=document.querySelector('.cvhero')||document.querySelector('.cvcover');
  function onScroll(){
    var past=window.scrollY>(hero?hero.offsetHeight*0.55:200);
    var r=sigBox.getBoundingClientRect();
    var sigVis=r.top<window.innerHeight-60&&r.bottom>0;
    stick.classList.toggle('on',past&&!sigVis);
  }
  addEventListener('scroll',onScroll,{passive:true});
  addEventListener('resize',onScroll);onScroll();
  var sbtn=document.getElementById('cvstick-btn');
  if(sbtn)sbtn.addEventListener('click',function(){
    sigBox.scrollIntoView({behavior:rm?'auto':'smooth',block:'start'});});
}
/* live signature preview from the typed legal name */
var si=document.querySelector('input[name="sig_name"]'),sp=document.getElementById('cv-sig-script');
if(si&&sp){var syncSig=function(){sp.textContent=si.value.trim()||'\\u00a0';};
  si.addEventListener('input',syncSig);syncSig();}
/* photo lightbox (annotations re-painted via _cvAnnPaint when present) */
var lb=null;
function closeLb(){if(!lb)return;lb.classList.remove('on');document.body.style.overflow='';
  setTimeout(function(){if(lb)lb.style.display='none';},200);}
function openLb(wrap){
  var img=wrap.querySelector('img');if(!img)return;
  var cv=wrap.querySelector('canvas');
  var fig=wrap.closest?wrap.closest('figure'):null;
  var capEl=fig?fig.querySelector('figcaption'):null;
  var cap=capEl?capEl.textContent:(img.getAttribute('alt')||'');
  if(!lb){lb=document.createElement('div');lb.className='cvlb';
    lb.innerHTML='<div class="cvlb-body"></div><div class="cvlb-cap"></div>'+
      '<button class="cvlb-x" aria-label="Close photo">\\u00d7</button>';
    document.body.appendChild(lb);
    lb.addEventListener('click',function(e){
      if(e.target===lb||e.target.className==='cvlb-x')closeLb();});
    document.addEventListener('keydown',function(e){if(e.key==='Escape')closeLb();});}
  var body=lb.querySelector('.cvlb-body');
  var nimg=document.createElement('img');nimg.src=img.getAttribute('src');nimg.alt=cap;
  var w=document.createElement('div');w.style.position='relative';w.appendChild(nimg);
  if(cv&&cv.dataset.ann){var ncv=document.createElement('canvas');ncv.className='cvph-canvas';
    ncv.dataset.ann=cv.dataset.ann;w.appendChild(ncv);}
  body.innerHTML='';body.appendChild(w);
  lb.querySelector('.cvlb-cap').textContent=cap;
  var pcv=body.querySelector('canvas');
  if(pcv&&window._cvAnnPaint)try{_cvAnnPaint(pcv);}catch(e){}
  document.body.style.overflow='hidden';
  lb.style.display='flex';
  requestAnimationFrame(function(){lb.classList.add('on');});
}
document.addEventListener('click',function(e){
  var t=e.target;
  while(t&&t!==document){if(t.classList&&t.classList.contains('cvph-wrap')){openLb(t);return;}t=t.parentNode;}
});
})();
"""


def _cv_meta_json_ld(manifest):
    """schema.org JSON-LD for a customer estimate.

    This is what a search preview or an assistant reads when the customer
    pastes the /sign link into a chat and asks whether the bid is any good.
    It carries the things that actually differentiate the quote — each package
    priced separately, the workmanship warranty term, certifications, the code
    basis, and the individual reviews — rather than one bare number, because a
    single price with no structure around it can only be compared on price.

    Everything here is drawn from _build_estimate_manifest; this function makes
    no claims the customer-visible page does not already make.
    """
    if not manifest:
        return ''
    m       = manifest
    company = m.get('company') or {}
    seller  = {
        '@type':   'RoofingContractor',
        'name':    company.get('name', 'Project One Roofing'),
        'telephone': company.get('phone', ''),
        'url':     f'https://{company.get("website", "")}' if company.get('website') else '',
        'address': {
            '@type':          'PostalAddress',
            'streetAddress':  company.get('street', ''),
            'addressLocality': company.get('city', ''),
            'addressRegion':  company.get('state', ''),
            'addressCountry': 'US',
        },
        'areaServed':      ['Colorado', 'Texas'],
        'foundingDate':    str(company.get('founded_year') or ''),
        'description':     company.get('about', ''),
    }
    certs = [str(x).strip() for x in (company.get('certifications') or []) if str(x).strip()]
    if certs:
        seller['hasCredential'] = certs

    is_ins  = bool(m.get('is_insurance'))
    total   = m.get('grand_total', 0)
    service = {
        '@type':      'Service',
        'serviceType': 'Roof replacement' if not is_ins
                       else 'Insurance-claim roofing scope',
        'provider':   {'@type': 'Organization',
                       'name': company.get('name', 'Project One Roofing')},
        'areaServed': f'{m.get("customer_city", "")}, {m.get("customer_state", "")}'.strip(', '),
    }

    # One Offer per package the rep is actually offering, so a reader can see
    # that this is a choice of three complete jobs rather than a single price.
    pkg_offers = []
    for trade in (m.get('trades') or []):
        for t in (trade.get('tiers') or []):
            sub = t.get('subtotal') or 0
            if sub <= 0:
                continue
            name = ' — '.join(x for x in (trade.get('label'),
                                          t.get('tier_label')) if x)
            desc_bits = [x for x in (t.get('package_name'), t.get('tagline')) if x]
            offer = {
                '@type': 'Offer',
                'name':  name,
                'price': round(float(sub), 2),
                'priceCurrency': 'USD',
                'itemOffered': service,
            }
            if desc_bits:
                offer['description'] = ' — '.join(desc_bits)
            if t.get('workmanship'):
                offer['warranty'] = {'@type': 'WarrantyPromise',
                                     'description': t['workmanship']}
            if t.get('is_selected'):
                offer['availability'] = 'https://schema.org/InStock'
            pkg_offers.append(offer)

    graph = {
        '@context':  'https://schema.org',
        '@type':     'Offer',
        'name':      'Roof replacement estimate',
        'description': m.get('summary', ''),
        'seller':    seller,
        'itemOffered': service,
        'priceSpecification': {
            '@type':         'PriceSpecification',
            'price':         total,
            'priceCurrency': 'USD',
            'valueAddedTaxIncluded': True,
        },
        'validThrough':   m.get('valid_until', ''),
        'availability':   'https://schema.org/InStock',
    }
    if m.get('estimate_number'):
        graph['identifier'] = m['estimate_number']

    if len(pkg_offers) > 1:
        prices = [o['price'] for o in pkg_offers]
        graph['addOn'] = {
            '@type':     'AggregateOffer',
            'offerCount': len(pkg_offers),
            'lowPrice':  min(prices),
            'highPrice': max(prices),
            'priceCurrency': 'USD',
            'offers':    pkg_offers,
        }
    elif pkg_offers:
        graph['addOn'] = pkg_offers[0]

    revs = (m.get('reviews') or {})
    if revs.get('count'):
        service['aggregateRating'] = {
            '@type':       'AggregateRating',
            'ratingValue': revs.get('average', 5),
            'reviewCount': revs.get('count', 0),
        }
        # Individual reviews, not just the average — an average alone is a
        # number anyone can type.
        items = []
        for r in (revs.get('items') or [])[:6]:
            if not (r.get('text') or '').strip():
                continue
            rv = {'@type': 'Review',
                  'reviewBody': r['text'].strip(),
                  'reviewRating': {'@type': 'Rating',
                                   'ratingValue': r.get('stars', 5),
                                   'bestRating': 5}}
            if r.get('name'):
                rv['author'] = {'@type': 'Person', 'name': r['name']}
            items.append(rv)
        if items:
            service['review'] = items

    warranty_body = (m.get('warranty_body') or '').strip()
    if warranty_body:
        graph['warranty'] = {
            '@type': 'WarrantyPromise',
            'description': warranty_body,
        }

    # Code compliance and ventilation math as additionalProperty — the parts a
    # competing bid usually cannot answer at all.
    props = []
    code = m.get('code') or {}
    if code.get('jurisdiction_name'):
        props.append(('Authority having jurisdiction', code['jurisdiction_name']))
    if code.get('verified'):
        props.append(('Jurisdiction code profile', 'Verified against the published local amendments'))
    _n_code = len(code.get('code_items') or [])
    if _n_code:
        props.append(('Code line items in scope', f'{_n_code} itemized'))
    for _v in (m.get('ventilation') or {}).values():
        if isinstance(_v, dict) and _v.get('code_basis'):
            props.append(('Ventilation code basis', _v['code_basis']))
            break
    if m.get('carrier'):
        props.append(('Insurance carrier', m['carrier']))
    if props:
        service['additionalProperty'] = [
            {'@type': 'PropertyValue', 'name': k, 'value': v} for k, v in props]

    return ('<script type="application/ld+json">'
            + json.dumps(graph, separators=(',', ':'))
            + '</script>')


def _cv_head(title, manifest=None):
    """Shared <head> + opening <body> for every public customer page.

    When `manifest` is provided (from _build_estimate_manifest), the head
    also carries meta description, OpenGraph tags and schema.org JSON-LD so
    a link preview or an AI assistant reading the page has a specific,
    machine-readable summary to work from — not just prices in a table."""
    desc = ''
    ld   = ''
    og   = ''
    if manifest:
        desc = (manifest.get('summary') or '').strip()
        if desc:
            desc_esc = he(desc)
            og += (f'<meta name="description" content="{desc_esc}">'
                   f'<meta property="og:title" content="{he(title)}">'
                   f'<meta property="og:description" content="{desc_esc}">'
                   f'<meta property="og:type" content="website">'
                   f'<meta name="twitter:card" content="summary">'
                   f'<meta name="twitter:description" content="{desc_esc}">')
        ld = _cv_meta_json_ld(manifest)
    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#05184a">
<link rel="icon" href="{_mount_path('/static/icon-192.png')}">
{og}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Great+Vibes&display=swap" rel="stylesheet">
<style>
/* Source Serif 4 + Inter are served from our own static dir rather than Google,
   so this page sets type identically to the printed estimate and the signed
   PDF — all three now read from estimator/static/fonts. Great Vibes stays on
   Google: it is decorative (the signature preview only) and degrades to a
   system cursive if it never loads. */
@font-face{{font-family:'Source Serif 4';
  src:url('{_mount_path('/static/fonts/SourceSerif4-var.woff2')}') format('woff2-variations');
  font-weight:200 900;font-style:normal;font-display:swap}}
@font-face{{font-family:'InterDoc';
  src:url('{_mount_path('/static/fonts/Inter-var.woff2')}') format('woff2-variations');
  font-weight:100 900;font-style:normal;font-display:swap}}
</style>
<title>{title}</title>
{ld}
<style>{_CV_CSS}</style></head><body>'''


def _cv_header():
    """Shared top bar: logo left, tap-to-call pill right, brand stripe."""
    return f'''<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="{_mount_path('/static/logo.png')}" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:{COMPANY_PHONE_DIGITS}">&#128222; {COMPANY_PHONE_DISPLAY}</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>'''


def _cv_footer(extra=''):
    """Shared footer + the shared behavior script. Closes the document."""
    return f'''<div class="cvftr">
  <img src="{_mount_path('/static/logo.png')}" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>
    <a href="tel:{COMPANY_PHONE_DIGITS}">{COMPANY_PHONE_DISPLAY}</a> &middot; projectoneroofingcolorado.com</div>
  {extra}
</div>
<script>{_CV_SHARED_JS}</script>
</body></html>'''


def _cv_sticky_bar(label, amount):
    """Floating total + jump-to-signature bar. The shared JS shows it once the
    customer scrolls past the hero and hides it while the sign form is on
    screen; the GBB page script live-updates the amount as packages change."""
    return f'''<div class="cvstick" id="cvstick"><div class="cvstick-in">
  <div class="cvstick-t"><span class="cvstick-lbl" id="cvstick-lbl">{label}</span>
  <span class="cvstick-amt" id="cvstick-amt">{amount}</span></div>
  <button type="button" class="cvstick-btn" id="cvstick-btn">Review &amp; Sign &darr;</button>
</div></div>'''


def _cv_download_card(token):
    """'Download PDF' card — lets a customer save/share/upload the estimate
    without signing. Sits just above the signature section on every /sign
    variant. Reads through the /sign/<token>/download.pdf route so a stale
    or revoked share token 404s the same way the /sign page does."""
    url = _mount_path(f'/sign/{he(token)}/download.pdf')
    return f'''<div class="cvdl">
  <div class="cvdl-t">
    <div class="cvdl-h">&#128190; Want to Think It Over?</div>
    <div class="cvdl-d">Download a PDF of this estimate to review offline, compare with other quotes, or share with a spouse.</div>
  </div>
  <a class="cvdl-btn" href="{url}" download>&#11015;&#65039; Download PDF</a>
</div>'''


def _cv_next_steps(signed=False, commercial=False):
    """'What happens next' timeline — sets expectations on the sign page and
    reassures on the confirmation page.

    The two middle steps are written for the building they are describing: a
    commercial re-roof is scheduled around an operating tenant and walked by a
    property manager, so promising to protect the landscaping and leave the
    home spotless lands as the wrong template on a warehouse bid."""
    steps = []
    if not signed:
        steps.append(('Review &amp; sign below',
                      'Look everything over, then sign electronically &mdash; it takes '
                      'less than a minute, right from your phone.'))
    steps += [
        ('We reach out to welcome you',
         'A member of our team will call within one business day to say hello and '
         'confirm the details of your project.'),
        ('Scheduling &amp; materials',
         'We order your materials and lock in an installation date that works '
         + ('around your building&rsquo;s operating hours.' if commercial
            else 'for your schedule.')),
        ('Installation day',
         'Our crew arrives on time, keeps the building watertight and in operation, '
         'completes the work, and leaves the site clean.' if commercial else
         'Our crew arrives on time, protects your landscaping and property, completes '
         'the work, and leaves your home spotless.'),
        ('Final walkthrough &amp; warranty',
         'We walk the finished project with you, answer every question, and register '
         'your workmanship warranty.'),
    ]
    items = ''.join(
        f'''<li class="cvnext-it"><span class="cvnext-n">{i + 1}</span>
      <div class="cvnext-t">{t}</div><div class="cvnext-d">{d}</div></li>'''
        for i, (t, d) in enumerate(steps))
    title = 'What Happens Next' if not signed else 'What Happens Next &mdash; You&rsquo;re All Set'
    return f'<div class="cvnext"><h2 data-eyebrow="Your project">{title}</h2><ol class="cvnext-list">{items}</ol></div>'


def _cv_contact_card(est):
    """'Questions?' card with the assigned rep's own contact info. Pulled from
    team.json (Settings → Team Logins → per-rep phone/email override); falls
    back to the derived @projectoneroofing.com address and the company office
    number when a rep hasn't set a personal override."""
    sp_raw = str(est.get('salesperson') or '').strip()
    team_rec = next((m for m in load_team() if m.get('username') == sp_raw), None) if sp_raw else None

    name = ((team_rec or {}).get('display_name') or '').strip() \
        or (_display_name(sp_raw) if sp_raw else '') or 'Project One Roofing'
    role = 'Your Project Consultant' if sp_raw else 'Loveland, Colorado'
    initials = ''.join(w[0] for w in name.split()[:2]).upper() or 'P1'

    phone_override = ((team_rec or {}).get('phone') or '').strip()
    phone_digits   = re.sub(r'\D', '', phone_override) or COMPANY_PHONE_DIGITS

    email = ((team_rec or {}).get('email') or '').strip()
    if not email and sp_raw and re.fullmatch(r'[A-Za-z0-9._-]+', sp_raw):
        email = f'{sp_raw}@projectoneroofing.com'
    email_btn = (f'<a class="cvrep-btn" href="mailto:{he(email)}">&#9993;&#65039; Email</a>'
                 if email else '')

    return f'''<div class="cvrep-card"><h2 data-eyebrow="Your consultant">Questions? We&rsquo;re Here to Help</h2>
  <div class="cvrep">
    <div class="cvrep-av">{he(initials)}</div>
    <div class="cvrep-info"><div class="cvrep-name">{he(name)}</div><div class="cvrep-role">{role}</div></div>
    <div class="cvrep-btns">
      <a class="cvrep-btn pri" href="tel:{phone_digits}">&#128222; Call</a>
      <a class="cvrep-btn" href="sms:{phone_digits}">&#128172; Text</a>
      {email_btn}
    </div>
  </div></div>'''


def _mount_path(path):
    """Prefix a root-absolute app path with this app's mount prefix
    ('/estimate' under the portal, '' standalone).

    Sign forms MUST post to the mounted path. The portal keeps root-level
    /sign/<token> and /sign-co/<token> routes so links already sitting in
    customers' inboxes still resolve, but those are GET-only redirects — a
    form posting to the bare root path got 405 Method Not Allowed and the
    signature was lost. Mirrors the script_root use in the auth guard."""
    try:
        return request.script_root + path
    except RuntimeError:      # called outside a request context
        return path


def _cv_sig_form(action, hidden='', extra_blocks='', agree_text='',
                 btn_text='&#10003; Accept &mdash; Sign Electronically', btn_id=''):
    """Shared sign form: labeled fields, live script-font signature preview
    (wired up by the shared JS), agreement checkbox, and the E-SIGN notice.
    Field names are part of the POST contract — do not rename."""
    bid = f' id="{btn_id}"' if btn_id else ''
    return f'''<form method="POST" action="{action}">
    {hidden}
    {extra_blocks}
    <label class="cvfield"><span>Full Legal Name *</span>
      <input class="cvinput" name="sig_name" placeholder="Type your full name" required
        autocomplete="name" autocapitalize="words"></label>
    <label class="cvfield"><span>Email Address <em>&mdash; optional, for your records</em></span>
      <input class="cvinput" name="sig_email" placeholder="you@example.com" type="email"
        autocomplete="email" inputmode="email"></label>
    <div class="cv-sigpad" aria-hidden="true"><div id="cv-sig-script">&nbsp;</div>
      <div class="cv-sigpad-hint">Signature preview &mdash; generated from your typed name</div></div>
    <label class="cvagree"><input type="checkbox" name="agree" required><span>{agree_text}</span></label>
    <button type="submit" class="cvbtn"{bid}>{btn_text}</button>
    <p class="cvlegal">&#128274; Secure e-signature &mdash; clicking the button above constitutes your legal
    electronic signature on this document, binding under the federal E-SIGN Act
    (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>'''


def _cover_photo_url(est):
    """Public URL of the estimate's assigned cover photo, or ''."""
    pid = est.get('cover_photo_id')
    if not pid:
        return ''
    for p in est.get('photos', []):
        if p.get('id') == pid and p.get('filename'):
            return f"/uploads/{p['filename']}"
    return ''


def _cv_hero(est, title, subtitle, steps=None):
    """Hero block for customer views — full-bleed cover photo when assigned,
    branded gradient banner otherwise. Optional numbered step chips set
    expectations for what the page asks of the customer."""
    steps_html = ''
    if steps:
        steps_html = ('<div class="cvsteps">'
                      + ''.join(f'<span class="cvstep"><b>{i + 1}</b>{s}</span>'
                                for i, s in enumerate(steps))
                      + '</div>')
    cover = _cover_photo_url(est)
    c     = est.get('customer', {})
    a     = c.get('address', {})
    addr  = ', '.join(filter(None, [a.get('street'), a.get('city'), a.get('state')]))
    if cover:
        who = he(c.get('name', ''))
        where = f' &mdash; {he(addr)}' if addr else ''
        sub = f'Prepared exclusively for {who}{where}' if who else he(subtitle)
        return f'''<div class="cvcover">
  <img src="{he(cover)}" alt="Property photo">
  <div class="cvcover-shade"></div>
  <div class="cvcover-text">
    <div class="cvhero-brand">Project One Roofing</div>
    <h1>{he(title)}</h1>
    <p>{sub}</p>
    {steps_html}
  </div>
</div>'''
    return f'''<div class="cvhero">
  <div class="cvhero-brand">Project One Roofing</div>
  <h1>{he(title)}</h1>
  <p>{he(subtitle)}</p>
  {steps_html}
</div>'''


# ── Customer-view parity blocks (mirror the printed estimate / PDF) ────────
# The sign page shows the same sections the printed estimate does: intro
# letter, photo report, and property condition report. Content and gating
# mirror buildPrintContent in app.js.

# Annotation renderer for customer-view photos — a direct port of drawAnn in
# app.js (oval / arrow / text, % coordinates, sw scaled by width/300). Included
# once per page via the window._cvAnnInit guard.
_CV_ANN_JS = """<script>
if(!window._cvAnnInit){window._cvAnnInit=1;
function _cvDrawAnn(ctx,a,W,H,s){var c=a.color||'#ef4444';var sw=(a.sw||3)*s;
ctx.save();ctx.strokeStyle=c;ctx.fillStyle=c;ctx.lineWidth=sw;ctx.lineCap='round';ctx.lineJoin='round';
if(a.type==='oval'){var x1=a.x1/100*W,y1=a.y1/100*H,x2=a.x2/100*W,y2=a.y2/100*H;
ctx.beginPath();ctx.ellipse((x1+x2)/2,(y1+y2)/2,Math.abs(x2-x1)/2||1,Math.abs(y2-y1)/2||1,0,0,Math.PI*2);ctx.stroke();}
else if(a.type==='arrow'){var x1=a.x1/100*W,y1=a.y1/100*H,x2=a.x2/100*W,y2=a.y2/100*H;
var ang=Math.atan2(y2-y1,x2-x1),hl=sw*6;
ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();
ctx.beginPath();ctx.moveTo(x2,y2);
ctx.lineTo(x2-hl*Math.cos(ang-0.45),y2-hl*Math.sin(ang-0.45));
ctx.lineTo(x2-hl*Math.cos(ang+0.45),y2-hl*Math.sin(ang+0.45));
ctx.closePath();ctx.fill();}
else if(a.type==='text'){var x=a.x/100*W,y=a.y/100*H,fs=Math.max(13,(a.sw||3)*5)*s;
ctx.font='bold '+fs+"px 'Segoe UI',sans-serif";ctx.textBaseline='top';
ctx.strokeStyle=(c==='#ffffff')?'#222':'#fff';ctx.lineWidth=Math.max(1,(a.sw||3))*2*s;
ctx.strokeText(a.text||'',x,y);ctx.fillStyle=c;ctx.fillText(a.text||'',x,y);}
ctx.restore();}
function _cvAnnPaint(cv){var img=cv.parentElement.querySelector('img');if(!img)return;
function paint(){var W=img.clientWidth,H=img.clientHeight;if(!W||!H)return;
cv.width=W;cv.height=H;var ctx=cv.getContext('2d');var anns=[];
try{anns=JSON.parse(cv.dataset.ann||'[]');}catch(e){}
anns.forEach(function(a){_cvDrawAnn(ctx,a,W,H,W/300);});}
if(img.complete&&img.naturalWidth)paint();else img.addEventListener('load',paint);
window.addEventListener('resize',paint);}
function _cvAnnAll(){document.querySelectorAll('canvas.cvph-canvas').forEach(_cvAnnPaint);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_cvAnnAll);
else _cvAnnAll();
}</script>"""


def _cv_intro_block(est):
    """Intro letter — same letter page the printed estimate opens with."""
    pv  = est.get('page_visibility') or {}
    txt = (est.get('intro_text') or '').strip()
    if not txt or pv.get('intro') is False:
        return ''
    return f'''<div class="cvintro">
  <img src="{_mount_path('/static/logo.png')}" class="cvintro-logo" alt="Project One Roofing">
  <p>{he(txt)}</p>
</div>'''


def _cv_photo_fig(photo):
    """One photo figure with an annotation-overlay canvas when annotated."""
    anns = photo.get('annotations') or []
    canvas = (f'<canvas class="cvph-canvas" data-ann="{he(json.dumps(anns))}"></canvas>'
              if anns else '')
    cap = (photo.get('caption') or '').strip()
    cap_el = f'<figcaption>{he(cap)}</figcaption>' if cap else ''
    return f'''<figure class="cvph-fig">
  <div class="cvph-wrap"><img src="/uploads/{he(photo["filename"])}" alt="{he(cap or "Project photo")}" loading="lazy">{canvas}</div>
  {cap_el}
</figure>'''


def _cv_photos_block(est):
    """Photo Report — the same show-in-estimate photos the PDF prints, with
    annotations drawn client-side (same math as the app's print bake)."""
    photos = [p for p in (est.get('photos') or [])
              if p.get('show_in_estimate') and p.get('filename')
              and p.get('id') != est.get('cover_photo_id')]
    if not photos:
        return ''
    figs = ''.join(_cv_photo_fig(p) for p in photos)
    return f'''<div class="cvphotos">
  <h2 data-eyebrow="Inspection">What We Found on Your Roof</h2>
  <div class="cvph-grid">{figs}</div>
</div>{_CV_ANN_JS}'''


def _cv_visualizer_block(est):
    """See the look — Good/Better/Best photo renderings on the customer page.

    Renders whichever tiers actually have a saved image; a partial save
    (rep only rendered Better) shows just that tile rather than a broken
    strip. Silent no-op when no renders are saved yet. Inline CSS lives
    with the rest of the customer view — the customer page shell does NOT
    load static/style.css."""
    vz = est.get('visualizer') or {}
    renders = vz.get('tier_renders') or {}
    tiers = [t for t in ('good', 'better', 'best') if renders.get(t)]
    if not tiers:
        return ''
    sels = vz.get('selections') or {}
    cards = ''
    for t in tiers:
        label = {'good': 'Good', 'better': 'Better', 'best': 'Best'}[t]
        rs = (sels.get('roofing') or {}).get(t) or {}
        ss = (sels.get('siding') or {}).get(t) or {}
        ds = (sels.get('doors') or {}).get(t) or {}
        cap = []
        if rs.get('color_name'):
            cap.append('Roof: ' + he(str(rs['color_name'])))
        if ss.get('color_name'):
            sname = ss.get('style_name') or ''
            side_lbl = 'Siding: ' + he(str(ss['color_name']))
            if sname:
                side_lbl += ' <span class="cvvz-style">(' + he(str(sname)) + ')</span>'
            cap.append(side_lbl)
        if ds.get('option_name'):
            door_lbl = 'Door: ' + he(str(ds['option_name']))
            if ds.get('color_name'):
                door_lbl += ' <span class="cvvz-style">(' + he(str(ds['color_name'])) + ')</span>'
            cap.append(door_lbl)
        caption = '<br>'.join(cap) if cap else '&nbsp;'
        src = '/uploads/' + he(renders[t])
        cards += (f'<figure class="cvvz-card">'
                  f'<figcaption class="cvvz-tier">{he(label)}</figcaption>'
                  f'<img src="{src}" alt="{he(label)} preview" loading="lazy">'
                  f'<div class="cvvz-cap">{caption}</div>'
                  f'</figure>')
    return f'''<div class="cvvz">
  <h2 data-eyebrow="Visualize">See It on Your Home</h2>
  <p class="cvvz-sub">Your home with the selected options blended onto your
  photo. Colors are indicative &mdash; the real material may look slightly
  different in person. Door previews show approximate finish only; confirm
  exact panel, glass, and hardware with your ProVia selection.</p>
  <div class="cvvz-grid">{cards}</div>
</div>
<style>
  /* This block used to ship its own visual language — hardcoded #1a3a5c and
     #e5e7eb, an 18px heading, no gutter — so it read as a different product
     bolted onto the page and ran edge-to-edge on phones. It now uses the
     page's tokens and inherits .cvmain's gutter like every other card. */
  .cvvz{{margin:var(--sp-3) 0 0;padding:var(--sp-5);border:1px solid var(--line);
    border-radius:var(--r);background:#fff;box-shadow:var(--sh)}}
  .cvvz-sub{{margin:0 0 var(--sp-4);color:var(--mut);font-size:var(--fz-sm);line-height:1.65}}
  .cvvz-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--sp-3)}}
  .cvvz-card{{margin:0;padding:0;border:none;background:#fff;display:flex;flex-direction:column}}
  .cvvz-tier{{font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:1.4px;
    font-size:var(--fz-micro);margin-bottom:var(--sp-1)}}
  .cvvz-card img{{width:100%;height:auto;border:1px solid var(--line);border-radius:6px;display:block;background:var(--bg)}}
  .cvvz-cap{{margin-top:var(--sp-1);font-size:var(--fz-fine);color:var(--mut);line-height:1.5}}
  .cvvz-style{{color:var(--faint)}}
</style>'''


# Property Condition Report constants — mirror PC_SECTIONS / PC_GRADES /
# RH_SEVERITIES / RH_PRIORITIES in app.js. Keep in sync.
_PC_SECTIONS = [('roof', 'Roofing', '🏠'), ('siding', 'Siding', '🏗'),
                ('windows', 'Windows', '🪟'), ('gutters', 'Gutters', '🌧'),
                ('other', 'Exterior / Other', '📋')]
_PC_GRADES = {'A': ('Excellent', '#16a34a', '#dcfce7'), 'B': ('Good', '#2563eb', '#dbeafe'),
              'C': ('Fair', '#d97706', '#fef3c7'),      'D': ('Poor', '#dc2626', '#fee2e2'),
              'F': ('Critical', '#7c3aed', '#ede9fe')}
_RH_SEV = {'low': ('Low', '#2563eb'), 'medium': ('Medium', '#d97706'), 'high': ('High', '#dc2626')}
_RH_PRI = {'immediate': 'Immediate', 'soon': '1–2 Years', 'monitor': 'Monitor'}


def _cv_condition_pc(est):
    """property_condition dict, migrating legacy roof_health data into a
    roof-only section when needed (mirrors pcGet in app.js)."""
    pc = est.get('property_condition')
    if pc:
        return pc
    rh = est.get('roof_health') or {}
    if not (rh.get('condition') or rh.get('findings') or rh.get('recommendations')):
        return None
    grade_map = dict(excellent='A', good='B', fair='C', poor='D', critical='F')
    return {
        'inspection_date':  rh.get('inspection_date', ''),
        'executive_notes':  '',
        'report_photo_ids': rh.get('report_photo_ids') or [],
        'sections': {'roof': {
            'enabled': True, 'grade': grade_map.get(rh.get('condition'), ''),
            'summary': rh.get('summary', ''), 'material_type': rh.get('material_type', ''),
            'age_years': rh.get('age_years', ''), 'pitch': rh.get('pitch', ''),
            'findings': rh.get('findings') or [],
            'recommendations': rh.get('recommendations') or [],
        }},
    }


def _pc_cost_lo(rec):
    """Low end of a recommendation's cost range — first number only, so
    '$500–$1,500' reads 500 (mirrors the print view's parse)."""
    m = re.search(r'[\d,]+(?:\.\d+)?', rec.get('cost_range') or '')
    return float(m.group(0).replace(',', '')) if m else 0.0


def _cv_condition_block(est):
    """Condition report — same grades, findings, recommendations and cost
    outlook the printed report shows. Gated by the Roof Health print chip
    (page_visibility.report), same as the PDF. Photos are NOT repeated here —
    they already appear in the Photo Report block above. Wording follows
    pc.audience ('homeowner' default | 'hoa'), mirroring _printConditionHTML
    in app.js."""
    pv = est.get('page_visibility') or {}
    if pv.get('report') is False:
        return ''
    pc = _cv_condition_pc(est)
    if not pc:
        return ''
    sections = pc.get('sections') or {}
    enabled = [(k, lbl, icon, sections.get(k)) for k, lbl, icon in _PC_SECTIONS
               if (sections.get(k) or {}).get('enabled') and (sections.get(k) or {}).get('grade')]
    if not enabled:
        return ''

    is_hoa       = pc.get('audience') == 'hoa'
    w_title      = 'Property Condition Report' if is_hoa else 'Home Condition Report'
    w_investment = 'Estimated Repair Investment' if is_hoa else 'Estimated Repair Costs'

    # Condition snapshot grid
    cells = ''
    for _k, lbl, icon, sec in enabled:
        word, clr, bg = _PC_GRADES.get(sec.get('grade'), ('—', '#333', '#f5f5f5'))
        cells += f'''<div class="cvcond-cell">
  <div class="cvcond-cell-lbl">{icon} {he(lbl)}</div>
  <div class="cvcond-letter" style="color:{clr};background:{bg}">{he(sec.get("grade"))}</div>
  <div class="cvcond-word" style="color:{clr}">{word}</div>
</div>'''

    exec_html = (f'<div class="cvcond-exec"><strong>Overall Assessment:</strong> {he(pc.get("executive_notes"))}</div>'
                 if (pc.get('executive_notes') or '').strip() else '')

    # Estimated repair investment (low-end totals per priority)
    cost_imm = cost_soon = cost_mon = 0.0
    for _k, _lbl, _icon, sec in enabled:
        for rec in (sec.get('recommendations') or []):
            lo = _pc_cost_lo(rec)
            pri = rec.get('priority')
            if pri == 'immediate':
                cost_imm += lo
            elif pri == 'soon':
                cost_soon += lo
            else:
                cost_mon += lo
    cost_total = cost_imm + cost_soon + cost_mon
    cost_html = ''
    if cost_total > 0:
        rows = [(lbl, v) for lbl, v in [('Immediate repairs (D/F)', cost_imm),
                                        ('Short-term (C grades)', cost_soon),
                                        ('Maintenance (B grades)', cost_mon)] if v > 0]
        trs = ''.join(f'<tr><td>{lbl}</td><td class="cvr">{fc(v)}+</td></tr>' for lbl, v in rows)
        cost_html = f'''<div class="cvcond-sh">{w_investment}</div>
<table class="cvcond-tbl">{trs}
<tr class="cvcond-cost-total"><td>Estimated Total</td><td class="cvr">{fc(cost_total)}+</td></tr></table>'''

    # Per-section detail
    sec_html = ''
    for key, lbl, icon, sec in enabled:
        word, clr, bg = _PC_GRADES.get(sec.get('grade'), ('—', '#333', '#f5f5f5'))
        meta_bits = []
        if key == 'roof':
            if sec.get('material_type'):
                meta_bits.append(f'Material: <strong>{he(sec["material_type"])}</strong>')
            if sec.get('age_years'):
                meta_bits.append(f'Est. Age: <strong>{he(sec["age_years"])} years</strong>')
            if sec.get('pitch'):
                meta_bits.append(f'Pitch: <strong>{he(sec["pitch"])}</strong>')
        meta_html = f'<div class="cvcond-meta">{" &middot; ".join(meta_bits)}</div>' if meta_bits else ''
        summary_html = (f'<div class="cvcond-summary">{he(sec.get("summary"))}</div>'
                        if (sec.get('summary') or '').strip() else '')

        find_rows = ''
        for f_ in (sec.get('findings') or []):
            if not (f_.get('description') or f_.get('area')):
                continue
            sev_lbl, sev_c = _RH_SEV.get(f_.get('severity'), (f_.get('severity') or '', '#666'))
            find_rows += (f'<tr><td style="font-weight:600">{he(f_.get("area") or "—")}</td>'
                          f'<td><span style="color:{sev_c};font-weight:700">{he(sev_lbl)}</span></td>'
                          f'<td>{he(f_.get("description") or "")}</td></tr>')
        find_html = (f'''<div class="cvcond-sh">Findings</div>
<table class="cvcond-tbl"><thead><tr><th scope="col">Area</th><th scope="col">Severity</th><th scope="col">Description</th></tr></thead>
<tbody>{find_rows}</tbody></table>''' if find_rows else '')

        rec_rows = ''
        for rec in (sec.get('recommendations') or []):
            if not rec.get('description'):
                continue
            rec_rows += (f'<tr><td style="white-space:nowrap"><strong>{he(_RH_PRI.get(rec.get("priority"), rec.get("priority") or ""))}</strong></td>'
                         f'<td>{he(rec.get("description") or "")}</td>'
                         f'<td style="white-space:nowrap">{he(rec.get("cost_range") or "—")}</td></tr>')
        rec_html = (f'''<div class="cvcond-sh">Recommendations</div>
<table class="cvcond-tbl"><thead><tr><th scope="col">Priority</th><th scope="col">Description</th><th scope="col">Est. Cost</th></tr></thead>
<tbody>{rec_rows}</tbody></table>''' if rec_rows else '')

        sec_html += f'''<div class="cvcond-sec">
  <div class="cvcond-sec-hd">
    <h4>{icon} {he(lbl)}</h4>
    <span class="cvcond-badge" style="color:{clr};background:{bg}">Grade {he(sec.get("grade"))} &mdash; {word}</span>
  </div>
  {meta_html}{summary_html}{find_html}{rec_html}
</div>'''

    insp_date = (pc.get('inspection_date') or '').strip()
    insp_html = f'<div class="cvcond-meta">Inspection Date: <strong>{he(insp_date)}</strong></div>' if insp_date else ''

    return f'''<div class="cvcond">
  <h2 data-eyebrow="Inspection">{w_title}</h2>
  {insp_html}
  <div class="cvcond-grid">{cells}</div>
  {exec_html}
  {cost_html}
  {sec_html}
  <div class="cvcond-foot">This {w_title} is a visual inspection summary prepared by Project One Roofing. Cost estimates are approximate ranges and do not constitute a formal bid. Contact us for a full assessment.</div>
</div>'''


def _visible_initials(est):
    """Initial statements with non-empty text, in order."""
    return [i for i in (est.get('contract_initials') or []) if (i.get('text') or '').strip()]


def _roofing_enabled(est):
    """Shingle color only makes sense when the roof is part of the job."""
    return bool(((est.get('trades') or {}).get('roofing') or {}).get('enabled'))


def _siding_enabled(est):
    """Siding color only makes sense when siding is part of the job."""
    return bool(((est.get('trades') or {}).get('siding') or {}).get('enabled'))


# Manufacturer-agnostic fallbacks — used only when a bundle carries no material
# with a colors[] array AND the rep didn't type a custom options list.
DEFAULT_SHINGLE_COLORS = [
    'Charcoal', 'Weathered Wood', 'Driftwood', 'Barkwood',
    'Pewter Gray', 'Estate Gray', 'Slate', 'Shakewood',
    'Hickory', 'Williamsburg Gray', 'Hunter Green', 'Mission Brown',
    'Black Walnut', 'Aged Copper', 'Birchwood', 'Oyster Gray',
]
DEFAULT_SIDING_COLORS = [
    'Arctic White', 'Iron Gray', 'Aged Pewter', 'Cobble Stone',
    'Timber Bark', 'Evening Blue', 'Boothbay Blue', 'Khaki Brown',
    'Sail Cloth', 'Deep Ocean',
]


def _bundle_id_for_tier(pb, est, trade, tier):
    """The bundle id chosen for this estimate's trade+tier.
    Estimate override (td.tier_bundles[tier]) → price-book default."""
    td = (est.get('trades') or {}).get(trade) or {}
    b = ((td.get('tier_bundles') or {}).get(tier) or '').strip()
    if b:
        return b
    return ((pb.get(trade + '_tier_defaults') or {}).get(tier) or '').strip()


def _material_product_for_bundle(pb, trade, bundle_id):
    """The bundle's material SKU (mirrors _vzMaterialForBundle in app.js):
    roofing → first `m_*` product; siding → first `s_*` that isn't sa_/sl_/sx_."""
    if not bundle_id:
        return None
    bundles = pb.get(trade + '_bundles') or []
    bundle = next((b for b in bundles
                   if isinstance(b, dict) and b.get('id') == bundle_id), None)
    if not bundle:
        return None
    catalog = pb.get(trade + '_catalog') or []
    by_id = {p.get('id'): p for p in catalog if isinstance(p, dict)}
    for pid in bundle.get('product_ids') or []:
        s = str(pid or '')
        if trade == 'roofing' and s.startswith('m_'):
            p = by_id.get(pid)
            if p:
                return p
        if trade == 'siding' and s.startswith('s_') and not s.startswith(('sa_', 'sl_', 'sx_')):
            p = by_id.get(pid)
            if p:
                return p
    return None


def _bundle_colors_for_tier(pb, est, trade, tier):
    """Color names carried by the material product in est's chosen bundle,
    for trade+tier. Empty when the bundle has no material with colors[]."""
    mat = _material_product_for_bundle(pb, trade, _bundle_id_for_tier(pb, est, trade, tier))
    if not mat:
        return []
    out = []
    for c in mat.get('colors') or []:
        if isinstance(c, dict):
            n = (c.get('name') or '').strip()
        else:
            n = str(c or '').strip()
        if n:
            out.append(n)
    return out


def _customer_color_options(pb, est, trade, tier, ss):
    """Ordered color names to offer the customer for trade+tier.
    Bundle colors → rep-typed ss.options → manufacturer-agnostic fallback."""
    seq = _bundle_colors_for_tier(pb, est, trade, tier)
    if seq:
        return seq
    seq = [str(o).strip() for o in (ss.get('options') or []) if str(o).strip()]
    if seq:
        return seq
    return list(DEFAULT_SHINGLE_COLORS if trade == 'roofing' else DEFAULT_SIDING_COLORS)


def _tier_colors_map(pb, est):
    """{trade: {tier: [color_name…]}} for every G/B/B trade with a color
    selection enabled. Inlined into the sign page so the color dropdown
    re-populates in the browser when the customer changes tiers."""
    out = {}
    picks = (
        ('roofing', est.get('shingle_selection') or {}),
        ('siding',  est.get('siding_selection')  or {}),
    )
    for trade, ss in picks:
        if not ss.get('enabled'):
            continue
        td = (est.get('trades') or {}).get(trade) or {}
        if not td.get('enabled'):
            continue
        row = {}
        for tier in ('good', 'better', 'best'):
            row[tier] = _customer_color_options(pb, est, trade, tier, ss)
        out[trade] = row
    return out


def _cv_color_block(est, pb, trade, ss, chosen_tier, label_pick, label_locked,
                    field_name, css_class):
    """Shared shingle/siding color step for the sign form. Locked display
    when the rep pre-picked a color; otherwise a required dropdown seeded
    from the tier's bundle colors — swapped in the browser when the
    customer changes tier."""
    if not ss.get('enabled', False):
        return ''
    chosen = (ss.get('chosen') or '').strip()
    if chosen:
        return f'''<div class="{css_class}">
      <div class="{css_class}-label">&#127912; {label_locked}</div>
      <div class="{css_class}-locked">{he(chosen)}</div>
      <input type="hidden" name="{field_name}" value="{he(chosen)}">
    </div>'''
    options = _customer_color_options(pb, est, trade, chosen_tier, ss)
    opts = ''.join(f'<option value="{he(o)}">{he(o)}</option>' for o in options)
    return f'''<div class="{css_class}" data-color-trade="{trade}">
      <div class="{css_class}-label">&#127912; {label_pick}</div>
      <select class="cvinput {css_class}-select" name="{field_name}" required
        id="cv-color-{trade}">
        <option value="">Select a color&hellip;</option>
        {opts}
      </select>
    </div>'''


def _cv_shingle_block(est, pb=None, chosen_tier='better'):
    """Shingle-color step for the sign form. Locked display if the rep
    already chose a color; otherwise a required dropdown for the customer,
    seeded from the current tier's bundle colors (IKO shows IKO colors,
    CertainTeed shows CertainTeed, …). The dropdown is re-populated in the
    browser when the customer changes packages — see _cv_tier_color_script."""
    if not _roofing_enabled(est):
        return ''
    if pb is None:
        pb = _ensure_bundle_catalogs(_load_price_book())
    return _cv_color_block(est, pb, 'roofing',
                           est.get('shingle_selection') or {},
                           chosen_tier,
                           label_pick='Choose Your Shingle Color *',
                           label_locked='Your Shingle Color',
                           field_name='shingle_color',
                           css_class='cv-shingle')


def _cv_siding_block(est, pb=None, chosen_tier='better'):
    """Siding-color step for the sign form. Same shape as _cv_shingle_block
    but for siding — restricts the customer's palette to the picked siding
    system (LP → LP colors, Hardie → Hardie colors, EDCO → EDCO colors)."""
    if not _siding_enabled(est):
        return ''
    if pb is None:
        pb = _ensure_bundle_catalogs(_load_price_book())
    return _cv_color_block(est, pb, 'siding',
                           est.get('siding_selection') or {},
                           chosen_tier,
                           label_pick='Choose Your Siding Color *',
                           label_locked='Your Siding Color',
                           field_name='siding_color',
                           css_class='cv-siding')


def _cv_tier_color_script(est, pb=None):
    """Inline JS that re-populates the color <select> when the customer
    changes the tier radio for a trade. Reads a JSON map inlined next to
    it. Emits nothing when no color picker is enabled — the map is empty."""
    if pb is None:
        pb = _ensure_bundle_catalogs(_load_price_book())
    tmap = _tier_colors_map(pb, est)
    if not tmap:
        return ''
    return f'''<script>
(function(){{
  var TCM = {json.dumps(tmap)};
  function _cvColorRepop(trade, tier){{
    var sel = document.getElementById('cv-color-'+trade); if(!sel) return;
    var opts = (TCM[trade] || {{}})[tier] || [];
    var cur = sel.value;
    var html = '<option value="">Select a color…</option>';
    for (var i=0;i<opts.length;i++){{
      var v = String(opts[i]).replace(/"/g,'&quot;');
      html += '<option value="'+v+'">'+v+'</option>';
    }}
    sel.innerHTML = html;
    if (cur && opts.indexOf(cur) !== -1) sel.value = cur;
  }}
  // Wrap selectCvTier so a tier change updates the color dropdown for that trade.
  var orig = window.selectCvTier;
  window.selectCvTier = function(trade, tier){{
    if (typeof orig === 'function') orig(trade, tier);
    _cvColorRepop(trade, tier);
  }};
}})();
</script>'''


def _cv_initials_block(est):
    """Per-clause initial boxes for the sign form."""
    inits = _visible_initials(est)
    if not inits:
        return ''
    rows = ''
    for idx, it in enumerate(inits):
        rows += f'''<label class="cv-initial-row">
          <span class="cv-initial-text">{he(it["text"])}</span>
          <input class="cv-initial-box" name="initial_{idx}" maxlength="6"
            placeholder="Initials" required autocomplete="off" inputmode="text">
        </label>'''
    return f'''<div class="cv-initials">
      <div class="cv-initials-title">Please initial each item below:</div>
      {rows}
    </div>'''


def _cv_attachments_block(est):
    """Customer-visible PDF documents, rendered as full-page images so the
    customer reads the whole document inline (with an open-original link).
    Falls back to a plain link when rasterization isn't available."""
    atts = [a for a in (est.get('attachments') or [])
            if a.get('show_in_estimate', True) and a.get('filename')]
    if not atts:
        return ''
    blocks = ''
    for a in atts:
        label = (a.get('label') or a.get('original_name') or 'Document').strip()
        fname = a['filename']
        pages = a.get('pages')
        if not pages and '/' in fname and fname.lower().endswith('.pdf'):
            # Attachment predates page rendering — rasterize (cached) on the fly
            pages = _rasterize_pdf_pages(*fname.split('/', 1))
        link = (f'<a class="cv-att" href="/uploads/{he(fname)}" '
                f'target="_blank" rel="noopener">&#128196; {he(label)}'
                f'{" — open original PDF" if pages else ""}</a>')
        if pages:
            imgs = ''.join(
                f'<img class="cv-att-page" src="/uploads/{he(p)}" alt="{he(label)} — page {i + 1}" loading="lazy">'
                for i, p in enumerate(pages))
            blocks += f'<div class="cv-att-doc"><div class="cv-att-doc-title">&#128196; {he(label)}</div>{imgs}{link}</div>'
        else:
            blocks += link
    return f'<div class="cvnotes"><h2 data-eyebrow="Attached">Documents &amp; Reports</h2><div class="cv-att-list">{blocks}</div></div>'


def _load_company_content():
    """Global trust-page content (About Us / Warranty / Certifications /
    Reviews) shown on customer proposals. Read fresh on every render so an
    admin edit takes effect in every gunicorn worker immediately."""
    try:
        if os.path.exists(COMPANY_CONTENT_FILE):
            with open(COMPANY_CONTENT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _cv_glance_block(est, manifest, sel_label='', sel_total=None):
    """The five-line digest that opens the proposal.

    A homeowner decides whether to read the rest of this page in about ten
    seconds, and the thing they forward to a spouse is whatever fits in a
    screenshot. This answers what we're doing, what the choices are, what it
    costs, what stands behind it and how long the price holds — before any
    table appears.

    It doubles as the passage an assistant quotes: when a customer pastes this
    link into a chat and asks "is this a good deal", this is the block that
    parses cleanly. Mirrors _printGlanceHTML in static/app.js — keep the two
    saying the same things.

    Rows with no data are dropped rather than rendered empty."""
    if not manifest:
        return ''
    m    = manifest
    rows = []

    if m.get('is_insurance'):
        carrier = (m.get('carrier') or '').strip()
        rows.append(('Your project',
                     'Insurance claim scope'
                     + (f' &mdash; <strong>{he(carrier)}</strong>' if carrier else '')))
    else:
        labels = [t.get('label', '') for t in (m.get('trades') or []) if t.get('label')]
        city   = ', '.join(x for x in (m.get('customer_city'), m.get('customer_state')) if x)
        if labels:
            scope = ' &middot; '.join(he(x) for x in labels)
            rows.append(('Your project',
                         f'<strong>{scope}</strong>'
                         + (f' at your home in {he(city)}' if city else '')))

        # Only claim a choice where one is actually offered.
        tiers = []
        for t in (m.get('trades') or []):
            for tier in (t.get('tiers') or []):
                lbl = tier.get('tier_label') or tier.get('tier')
                if lbl and lbl not in tiers:
                    tiers.append(lbl)
        if len(tiers) > 1:
            rows.append(('Your options',
                         f'{he(", ".join(tiers))} &mdash; each one a complete job, '
                         'priced in full further down this page'))

    # Deliberately NO price here. This block sits above the photographs and the
    # condition report, and a number on the second thing a homeowner reads
    # invites them to decide before they have seen why the work is needed —
    # which is the whole reason the page was reordered. The total lives after
    # the scope, where it can be judged against something.

    # Warranty headline: the selected tier's promise beats the generic body copy.
    warr = ''
    wbt  = m.get('warranty_by_tier') or {}
    sel  = (est.get('selected_tier') or '').strip().lower()
    if sel and wbt.get(sel):
        warr = wbt[sel]
    else:
        body = (m.get('warranty_body') or '').strip()
        if body:
            warr = re.split(r'\n|(?<=\.)\s+', body)[0].strip()
    if warr:
        rows.append(('Backed by', he(warr[:190] + '…' if len(warr) > 190 else warr)))

    insp = ((est.get('property_condition') or {}).get('inspection_date') or '').strip()
    if insp:
        rows.append(('Inspected',
                     f'<strong>{he(insp)}</strong> &mdash; full condition report below, '
                     'with photographs'))

    if m.get('valid_until'):
        rows.append(('Pricing held until',
                     f'<strong>{he(m["valid_until"])}</strong>'))

    if not rows:
        return ''
    body = ''.join(f'''<div class="cvglance-row">
        <dt class="cvglance-k">{he(k)}</dt><dd class="cvglance-v">{v}</dd>
      </div>''' for k, v in rows)
    return f'''<section class="cvnotes cvglance">
  <h2 data-eyebrow="Summary">At a Glance</h2>
  <dl class="cvglance-list">{body}</dl>
</section>'''


def _code_requirements(code, limit=10):
    """Plain-language install requirements that apply at this address.

    Merged most-specific-first — the jurisdiction's own rules, then its local
    amendments, then the code line items priced into the scope with their IRC
    citations stripped. Lightly de-duplicated: the same requirement usually
    appears in more than one of those sources (a roofing affidavit shows up as
    both a jurisdiction rule and an amendment), and printing it twice makes the
    list look padded.

    Returns [] when nothing is known, so the caller can drop the section rather
    than print a heading over an empty list.
    """
    if not code:
        return []
    out, seen = [], set()

    def add(text):
        t = ' '.join(str(text or '').split())
        if not t:
            return
        # Key on the first two significant words. The same requirement reaches
        # here from up to three sources phrased differently — "Roofing
        # affidavit required with the reroof permit" and "Roofing affidavit: A
        # signed roofing affidavit identifying..." are one rule, and a longer
        # key keeps both. Two words collapses them while leaving genuinely
        # different rules ("Ice barrier" vs "Ice and water") distinct.
        key = ' '.join(t.lower().replace('(', ' ').replace(')', ' ')
                       .replace(':', ' ').replace('—', ' ').split()[:2])
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    for pt in (code.get('jurisdiction_points') or []):
        add(pt)
    for a in ((code.get('verified_profile') or {}).get('amendments') or []):
        txt = (a.get('text') or '').strip()
        top = (a.get('topic') or '').strip()
        if txt:
            add(f'{top}: {txt}' if top else txt)
    for ci in (code.get('code_items') or []):
        # Label only — the IRC section number is documentation, not a
        # requirement a homeowner can act on.
        add(ci.get('label'))
    return out[:limit]


def _cv_permit_block(manifest):
    """Who holds the permit for THIS address, and what that office requires of
    the roof install.

    Deliberately short. The adopted code edition, amendment source links,
    submittal mechanics and IRC section numbers all live in the manifest and
    belong in the production packet — in a proposal they bury the two things a
    homeowner actually wants, which are the name of the authority and the list
    of things that authority makes us do.

    When no jurisdiction has been matched to the address the block says so
    plainly rather than dressing the Colorado statewide baseline up as local:
    claiming to know a customer's code authority when we don't is the one
    failure here that would actually cost trust.
    """
    if not manifest:
        return ''
    code = manifest.get('code') or {}
    if not code:
        return ''

    matched = code.get('matched')
    name    = (code.get('jurisdiction_name') or '').strip()
    reqs    = _code_requirements(code)

    if matched and name:
        meta = []
        if code.get('office'):
            meta.append(he(code['office']))
        if code.get('county'):
            meta.append(he(code['county']) + ' County')
        if code.get('phone'):
            meta.append(f'<a href="tel:{he(code["phone"])}">{he(code["phone"])}</a>')
        head = (f'<p class="cvperm-name">{he(name)}</p>'
                + (f'<p class="cvperm-meta">{" &middot; ".join(meta)}</p>' if meta else ''))
        lead = (f'<p class="cvperm-lead">{he(name)} issues the permit for this address and '
                'inspects the finished roof. Everything below is required there &mdash; it is '
                'priced into your estimate, not an add-on.</p>')
    else:
        if not reqs:
            return ''
        head = ('<p class="cvperm-name cvperm-unknown">Permitting authority not yet confirmed</p>')
        lead = ('<p class="cvperm-lead">Colorado has no statewide residential building code &mdash; '
                'the city or county adopts and enforces its own. We confirm the authority for this '
                'address before pulling the permit, and the permit is included in your price '
                'either way.</p>')

    reqs_html = ''
    if reqs:
        reqs_html = (f'<div class="cvperm-sub"><h3 class="cvperm-h">'
                     + (f'Required on your roof in {he(name)}' if matched and name
                        else 'Required on your roof')
                     + '</h3><ul class="cvperm-list">'
                     + ''.join(f'<li>{he(r)}</li>' for r in reqs)
                     + '</ul></div>')

    return f'''<section class="cvnotes cvperm">
  <h2 data-eyebrow="Permits &amp; code">Who Pulls Your Permit</h2>
  {head}
  {lead}
  {reqs_html}
</section>'''


def _cv_trust_blocks(est):
    """Company trust content rendered into every customer proposal, between
    the estimate body and the terms. Global content is admin-edited in
    Settings; each estimate can hide individual blocks via
    page_visibility.trust_* (default visible). Marketing content only —
    deliberately excluded from the signed document hash and the signed PDF."""
    cc = _load_company_content()
    pv = est.get('page_visibility') or {}

    def _blk(key):
        blk = cc.get(key) or {}
        if not blk.get('enabled', True) or pv.get(f'trust_{key}') is False:
            return None
        return blk

    is_comm = est.get('estimate_type') == 'commercial'

    out = ''
    for key, dflt_title, eyebrow in (('about', 'About Us', 'Who you&rsquo;re hiring'),
                                     ('warranty', 'Our Warranty', 'What backs the work')):
        blk = _blk(key)
        body = (blk.get('body') or '').strip() if blk else ''
        if not body:
            continue
        if is_comm:
            body = _commercial_voice(body)
        paras = ''.join(f'<p>{he(p.strip())}</p>'
                        for p in body.split('\n\n') if p.strip())
        title = (blk.get('title') or '').strip() or dflt_title
        out += f'''<div class="cvnotes cvtrust">
      <h2 data-eyebrow="{eyebrow}">{he(title)}</h2>
      <div class="cvtrust-body">{paras}</div>
    </div>'''

    blk = _blk('certifications')
    if blk:
        items = [str(i).strip() for i in (blk.get('items') or []) if str(i).strip()]
        if items:
            title = (blk.get('title') or '').strip() or 'Licenses & Certifications'
            lis = ''.join(f'<li>{he(i)}</li>' for i in items)
            out += f'''<div class="cvnotes cvtrust">
      <h2 data-eyebrow="Credentials">{he(title)}</h2>
      <ul class="cvtrust-certs">{lis}</ul>
    </div>'''

    blk = _blk('reviews')
    if blk:
        revs = [r for r in (blk.get('items') or [])
                if isinstance(r, dict) and (r.get('text') or '').strip()]
        if revs:
            title = (blk.get('title') or '').strip() or 'What Homeowners Say'
            cards = ''
            for r in revs[:6]:  # cap so a long review list can't swamp the page
                try:
                    n = max(1, min(5, int(r.get('stars') or 5)))
                except (TypeError, ValueError):
                    n = 5
                name = (r.get('name') or '').strip()
                who = f'<div class="cvtrust-rev-name">&mdash; {he(name)}</div>' if name else ''
                cards += f'''<div class="cvtrust-rev">
          <div class="cvtrust-rev-stars">{'&#9733;' * n}</div>
          <div class="cvtrust-rev-text">&ldquo;{he(r['text'].strip())}&rdquo;</div>
          {who}
        </div>'''
            out += f'''<div class="cvnotes cvtrust">
      <h2 data-eyebrow="Your neighbors">{he(title)}</h2>
      <div class="cvtrust-revs">{cards}</div>
    </div>'''

    return out


_TRADE_LABELS = dict(roofing='Roofing', siding='Siding', windows='Windows',
                     gutters='Gutters', commercial='Commercial', other='Other / Misc')

_WARRANTY_BY_TIER = {
    'good':   '5-year Project One workmanship warranty',
    'better': '5-year Project One workmanship warranty',
    'best':   'Lifetime Project One workmanship warranty (for as long as you own the home)',
}
# Same promise, said to a building owner. The commercial bid goes to a property
# manager or an owner's rep, and "for as long as you own the home" on a
# warehouse reads as a residential template nobody bothered to change.
_WARRANTY_BY_TIER_COMMERCIAL = dict(
    _WARRANTY_BY_TIER,
    best='Lifetime Project One workmanship warranty (for as long as you own the building)')


def _commercial_voice(text):
    """Say the same warranty promise to a building owner.

    The per-tier constants above were already switched, but the warranty
    block in company_content.json is admin-edited free text that says "for as
    long as you own the home" — and it reaches a commercial bid twice: once
    in the visible trust block and once inside the JSON-LD the sign page
    embeds. Rewriting the phrase keeps one editable source of truth instead
    of asking an admin to maintain a parallel commercial copy."""
    out = str(text or '')
    for a, b in (('you own the home', 'you own the building'),
                 ('your home', 'your building'),
                 ('the homeowner', 'the building owner')):
        out = out.replace(a, b).replace(a.capitalize(), b.capitalize())
    return out

# The seven process steps a customer reads. The residential list talks about
# landscaping, A/C units and the homeowner; a commercial re-roof is staged
# around an operating building instead. Same commitments, same order — only
# the things being protected and the person walking the job change.
_PROCESS_RESIDENTIAL = [
    'Building permit pulled with the local authority having jurisdiction and final inspection scheduled',
    'Property, landscaping and A/C units tarped and protected before tear-off',
    'Complete tear-off to the deck; damaged decking replaced sheet-for-sheet',
    'Installed to manufacturer specification by Project One crews (not day-labor subs)',
    'Full magnetic nail sweep of driveway, walks and yard within one business day',
    'Written change orders required before any out-of-scope work — no verbal upsells',
    'Final walkthrough with the homeowner, then manufacturer warranty registered in your name',
]
# A LAYOVER has no tear-off, so the two middle steps of the commercial list
# are simply untrue on one — and "torn off and dried in by section" is exactly
# the sentence a property manager will quote back when the invoice has no
# disposal on it. What replaces them is the recover prep GAF actually requires.
_PROCESS_COMMERCIAL_LAYOVER = [
    'Building permit pulled with the local authority having jurisdiction and final inspection scheduled',
    'Staging, roof access and material handling scheduled with building management before work starts',
    'Existing roof scanned for trapped moisture; any wet material removed and replaced before work begins',
    'Existing membrane cut and prepped to manufacturer requirement, then the new cover board laid over it',
    'Installed to manufacturer specification by Project One crews (not day-labor subs)',
    'Written change orders required before any out-of-scope work — no verbal upsells',
    'Final walkthrough with the owner or property manager, then the manufacturer system warranty registered in your name',
]
_PROCESS_COMMERCIAL = [
    'Building permit pulled with the local authority having jurisdiction and final inspection scheduled',
    'Staging, roof access and material handling scheduled with building management before work starts',
    'Torn off and dried in by section so the building stays watertight and in operation overnight',
    'Wet or damaged decking and insulation replaced, and the deck documented before the new system goes down',
    'Installed to manufacturer specification by Project One crews (not day-labor subs)',
    'Written change orders required before any out-of-scope work — no verbal upsells',
    'Final walkthrough with the owner or property manager, then the manufacturer system warranty registered in your name',
]


def _build_estimate_manifest(est):
    """Structured summary of what makes THIS estimate specific.

    Consumed by the /sign page (schema.org JSON-LD + meta description +
    a human-readable details block) and the signed PDF (About-This-Estimate
    page + document metadata). Pure — reads company_content.json and
    jurisdictions.json (same reads neighboring helpers already do) but
    never mutates the estimate."""
    cc = _load_company_content()
    c  = est.get('customer', {}) or {}
    a  = c.get('address', {}) or {}
    is_ins = ((est.get('estimate_type') == 'insurance')
              or ((est.get('trades', {}) or {}).get('insurance', {}) or {}).get('enabled'))
    # A commercial bid is read by an owner's rep or a property manager, so the
    # process steps and the warranty wording switch to the commercial pair.
    is_comm = est.get('estimate_type') == 'commercial'

    # Which packages the rep is offering — the customer only ever sees the
    # enabled subset, so the manifest must mirror that.
    te = est.get('tiers_enabled') or {}
    enabled_tiers = [t for t in ('good', 'better', 'best')
                     if te.get(t, True) is not False] or ['good', 'better', 'best']

    trades_out = []
    if not is_ins:
        pb = _ensure_bundle_catalogs(_load_price_book())
        for tk in GBB_TRADES:
            td = (est.get('trades') or {}).get(tk) or {}
            if not td.get('enabled') or not td.get('line_items'):
                continue
            tmode = _trade_mode(tk, td)
            label = _TRADE_LABELS.get(tk, tk.title())
            if tmode == 'simple':
                # Only surface line items actually in scope (qty > 0). A
                # commercial bundle ships both Re-Roof and New-Construction
                # labor lines and zeros out the one that does not apply;
                # _autofill_tier_features keeps priced zero-qty lines, so we
                # walk items directly here.
                feats = []
                seen  = set()
                for it in (td.get('line_items') or []):
                    if it.get('customer_visible') is False:
                        continue
                    if float(it.get('quantity') or 0) <= 0:
                        continue
                    nm = (it.get('name') or '').strip()
                    if not nm:
                        continue
                    desc = (it.get('description') or '').strip()
                    line = f'{nm} — {desc}' if desc and desc != nm else nm
                    if line in seen:
                        continue
                    seen.add(line)
                    feats.append(line)
                sub = _trade_subtotal(est, tk, 'better')
                if sub <= 0 and not feats:
                    continue
                trades_out.append({
                    'key': tk, 'label': label, 'mode': 'simple',
                    'subtotal': round(sub, 2),
                    'features': feats[:12],
                })
                continue
            selected = _trade_tier(est, tk)
            if selected not in enabled_tiers:
                selected = enabled_tiers[0]
            tfeat, tdesc = _trade_tier_content(est, tk)
            tnames = _tier_package_names(est, tk)
            tiers_info = []
            stale_by_tier = {t: _tier_bullets_are_stale(pb, est, tk, t)
                             for t in enabled_tiers}
            for t in enabled_tiers:
                sub = _trade_subtotal(est, tk, t)
                feats, tag_stored = _tier_card_content(pb, est, tk, t, tfeat, tdesc)
                bundle_id  = _bundle_id_for_tier(pb, est, tk, t)
                mat        = _material_product_for_bundle(pb, tk, bundle_id) or {}
                bundle_rec = None
                for b in (pb.get(tk + '_bundles') or []):
                    if isinstance(b, dict) and b.get('id') == bundle_id:
                        bundle_rec = b
                        break
                # Same rule as the tagline: a stale bundle must not name a
                # package it no longer supplies.
                pkg_name = (str(tnames.get(t) or '').strip()
                            or ('' if stale_by_tier[t]
                                else (bundle_rec.get('name') if bundle_rec else ''))
                            or '')
                # A hand-built tier gets no tagline at all — neither the stored
                # one nor the bundle's, since the bundle is no longer what this
                # package sells.
                tagline  = '' if stale_by_tier[t] else (
                    tag_stored
                    or (bundle_rec.get('description') if bundle_rec else '')
                    or '')
                tiers_info.append({
                    'tier':          t,
                    'tier_label':    dict(good='Good', better='Better', best='Best')[t],
                    'package_name':  pkg_name,
                    'tagline':       tagline,
                    'features':      feats[:12],
                    'subtotal':      round(sub, 2),
                    'is_selected':   t == selected,
                    'material_name': (mat.get('name') or '').strip(),
                    'material_bullets': [str(b).strip()
                                         for b in (mat.get('bullets') or [])
                                         if str(b).strip()][:6],
                    'workmanship':   _WARRANTY_BY_TIER[t],
                })
            trades_out.append({
                'key':            tk, 'label': label, 'mode': 'gbb',
                'selected_tier':  selected,
                'tiers':          tiers_info,
            })

    # Code compliance + permit
    jx        = _load_jurisdictions() or {}
    baseline  = jx.get('colorado_baseline') or {}
    perm      = est.get('permit_jurisdiction') or {}
    sel_id    = (perm.get('selected_id') or perm.get('auto_id') or '').strip()
    jur       = None
    if sel_id:
        for j in (jx.get('jurisdictions') or []):
            if isinstance(j, dict) and j.get('id') == sel_id:
                jur = j
                break
    # Every one of these points is steep-slope ASPHALT SHINGLE guidance: ice
    # barrier at the eave, attic ventilation on the 1/300 rule, hip and ridge
    # cap, Class 4 shingles, IRC R905 chapter and verse. On a bid with no
    # shingle roof in it — a commercial TPO flat roof, or a siding- or
    # window-only job — none of it is true, and quoting R905.2.8.5 drip edge
    # next to a welded membrane reads as a copy-paste bid to the property
    # manager reading it. The JURISDICTION still applies (a commercial reroof
    # pulls a permit too), so only the roof-covering content drops: the
    # jurisdiction name, the verified profile and the permit info all stay.
    shingle_scope = bool(((est.get('trades') or {}).get('roofing') or {}).get('enabled'))
    baseline_points = [str(p).strip() for p in (baseline.get('code_points') or [])
                       if str(p).strip()] if shingle_scope else []
    jur_points = ([str(p).strip() for p in ((jur or {}).get('code_points') or [])
                   if str(p).strip()]) if shingle_scope else []
    code_items = [{'label': str(i.get('label', '')).strip(),
                   'basis': str(i.get('basis', '')).strip()}
                  for i in (baseline.get('code_items') or [])
                  if isinstance(i, dict) and str(i.get('label') or '').strip()
                  ] if shingle_scope else []
    # Manager-approved per-jurisdiction profile — the adopted IRC year, local
    # amendments, and reroof permit info a customer actually wants to see.
    # Only surfaced when reviewed_at is set; an unreviewed profile might carry
    # unvetted Perplexity output and must never reach the customer view.
    vp = (jur.get('verified_profile') if isinstance(jur, dict) else None) or {}
    verified_profile = None
    if vp and (vp.get('reviewed_at') or '').strip():
        verified_profile = {
            'adopted_code':            str(vp.get('adopted_code') or '').strip(),
            'adopted_code_source_url': str(vp.get('adopted_code_source_url') or '').strip(),
            'amendments': [
                {'topic': str(a.get('topic') or '').strip(),
                 'text':  str(a.get('text')  or '').strip(),
                 'source_url': str(a.get('source_url') or '').strip()}
                for a in (vp.get('amendments') or [])
                if isinstance(a, dict) and str(a.get('text') or '').strip()
            ][:8],
            'reroof_permit': {
                'submittal_method': str((vp.get('reroof_permit') or {}).get('submittal_method') or '').strip(),
                'portal_url':       str((vp.get('reroof_permit') or {}).get('portal_url') or '').strip(),
                'fee_basis':        str((vp.get('reroof_permit') or {}).get('fee_basis') or '').strip(),
            },
            # Who actually issues the permit, when this jurisdiction
            # contracts inspections out (Colorado Springs → Pikes Peak
            # Regional Building Department). Blank for the common case.
            'delegated_to': str(vp.get('delegated_to') or '').strip(),
            'sources':      [str(s).strip() for s in (vp.get('sources') or []) if str(s).strip()][:6],
            'verified_at':  str(vp.get('verified_at') or '').strip(),
            'verified_via': str(vp.get('verified_via') or '').strip(),
        }
    code = None
    if jur or baseline_points or jur_points or code_items or verified_profile:
        code = {
            # matched is the honest flag: without it the customer view cannot
            # tell a real address match from the statewide fallback, and would
            # present generic Colorado guidance as if it were their city's.
            'matched':           bool(jur),
            'jurisdiction_name': (jur.get('name') if jur else '')
                                 or 'Colorado (statewide baseline)',
            'jurisdiction_kind': (jur.get('kind') if jur else ''),
            'county':            (jur.get('county') if jur else ''),
            'office':            (jur.get('office') if jur else ''),
            'phone':             (jur.get('phone') if jur else ''),
            'url':               (jur.get('url') if jur else ''),
            # Written for the office admin pulling the permit, NOT for the
            # customer — it names internal tooling. Never render this on a
            # customer-facing surface; see _cv_permit_block.
            'permit_process_internal': (jur.get('pull') if jur else ''),
            'baseline_points':   baseline_points,
            'jurisdiction_points': jur_points,
            'code_items':        code_items[:12],
            'verified':          bool(perm.get('verified')),
            'verified_profile':  verified_profile,
        }

    # Attic ventilation calc (mirrors what the crew packet already uses)
    vent = None
    if not is_ins:
        roof_td = (est.get('trades') or {}).get('roofing') or {}
        if roof_td.get('enabled'):
            m = est.get('measurements') or {}
            v = attic_ventilation(m)
            if float(v.get('attic_sqft', 0) or 0) > 0:
                vent = {
                    'attic_sqft':          round(float(v['attic_sqft']), 0),
                    'required_total_sqin': round(float(v['required_total']), 1),
                    'required_intake_sqin':  round(float(v['required_intake']), 1),
                    'required_exhaust_sqin': round(float(v['required_exhaust']), 1),
                    'provided_exhaust_sqin': round(float(v['provided_exhaust']), 1),
                    'deficit_exhaust_sqin':  round(float(v['deficit_exhaust']), 1),
                    'ridge_lf_required':   round(float(v['ridge_lf_required']), 1),
                    'intake_lf_suggested': int(v['intake_lf_suggested']),
                    'code_basis':          'IRC R806 (balanced 1/300 rule)',
                }

    # Reviews summary
    revs = [r for r in ((cc.get('reviews') or {}).get('items') or [])
            if isinstance(r, dict) and (r.get('text') or '').strip()]
    reviews = None
    if revs:
        stars = []
        for r in revs:
            try:
                stars.append(max(1, min(5, int(r.get('stars') or 5))))
            except (TypeError, ValueError):
                stars.append(5)
        reviews = {
            'count':   len(revs),
            'average': round(sum(stars) / len(stars), 2),
            'items':   [{'stars': s, 'name': (r.get('name') or '').strip(),
                         'text':  (r.get('text') or '').strip()}
                        for r, s in zip(revs[:6], stars[:6])],
        }

    # Company
    about = ((cc.get('about') or {}).get('body') or '').strip()
    certs = [str(i).strip() for i in ((cc.get('certifications') or {}).get('items') or [])
             if str(i).strip()]
    warranty_body = ((cc.get('warranty') or {}).get('body') or '').strip()
    if is_comm:
        warranty_body = _commercial_voice(warranty_body)

    # Grand total (customer's currently selected packages)
    if is_ins:
        _html, total = _insurance_cv_table(est)
    else:
        total = calc_selected_total(est)

    ins_td   = (est.get('trades') or {}).get('insurance') or {}
    enum     = _est_number(est)

    # One-line summary for meta description + PDF metadata
    if is_ins:
        summary = ('Insurance-claim roofing scope prepared by Project One Roofing'
                   + (f' for {c["name"]}' if c.get('name') else ''))
        if ins_td.get('carrier'):
            summary += f'; carrier {ins_td["carrier"]}'
    else:
        pkg = _pick_summary_label(est) or 'Roof replacement estimate'
        summary = pkg + (f' prepared for {c["name"]}' if c.get('name') else '')
    if a.get('city') and a.get('state'):
        summary += f', {a["city"]}, {a["state"]}'
    if total > 0:
        summary += f' — total {fc(total)}'

    return {
        'estimate_number': enum,
        'estimate_date':   est.get('estimate_date', ''),
        'valid_until':     est.get('valid_until', ''),
        'customer_name':   c.get('name', ''),
        'customer_city':   a.get('city', ''),
        'customer_state':  a.get('state', ''),
        'customer_zip':    a.get('zip', ''),
        'is_insurance':    bool(is_ins),
        'carrier':         (ins_td.get('carrier') or '').strip(),
        'claim_number':    (ins_td.get('claim_number') or '').strip(),
        'grand_total':     round(float(total or 0), 2),
        'trades':          trades_out,
        'code':            code,
        'ventilation':     vent,
        'warranty_by_tier': dict(_WARRANTY_BY_TIER_COMMERCIAL if is_comm else _WARRANTY_BY_TIER),
        'warranty_body':   warranty_body,
        'company': {
            'name':          'Project One Roofing',
            'phone':         COMPANY_PHONE_DISPLAY,
            'phone_digits':  COMPANY_PHONE_DIGITS,
            'city':          'Loveland',
            'state':         'CO',
            'street':        '115 E 5th St',
            'website':       'projectoneroofingcolorado.com',
            'about':         about,
            'certifications': certs,
            'founded_year':  2015,
        },
        'reviews':         reviews,
        'summary':         summary,
        'process': list(_PROCESS_COMMERCIAL_LAYOVER if is_comm and _est_is_layover(est)
                        else _PROCESS_COMMERCIAL if is_comm
                        else _PROCESS_RESIDENTIAL),
    }


def _cv_estimate_details_block(manifest, est=None):
    """Human-readable 'About This Estimate' card rendered before the T&C.

    Same content that drives the schema.org JSON-LD in the head — visible to
    the customer as a genuinely useful walkthrough, and organized so that an
    AI reader (Claude/ChatGPT, when the customer pastes the link into a chat)
    picks up specifics a generic estimate wouldn't have: manufacturer +
    warranty per package, code items with IRC citations, ventilation math,
    workmanship per tier, and the standing process."""
    if not manifest:
        return ''
    if est is not None:
        pv = (est.get('page_visibility') or {})
        if pv.get('estimate_details') is False:
            return ''

    m = manifest

    # ── Scope / materials by package ───────────────────────────────────
    scope_html = ''
    trades = m.get('trades') or []
    if trades:
        sections = []
        for tr in trades:
            head = f'<h4>{he(tr["label"])}</h4>'
            if tr.get('mode') == 'simple':
                feats = tr.get('features') or []
                bullets = ''.join(f'<li>{he(f)}</li>' for f in feats[:8])
                sections.append(head + (f'<ul class="chk">{bullets}</ul>' if bullets else ''))
                continue
            cards = ''
            for ti in tr.get('tiers') or []:
                bullets = ''.join(f'<li>{he(b)}</li>'
                                  for b in (ti.get('material_bullets') or [])[:5])
                tag = ti.get('tagline') or ''
                name = ti.get('package_name') or ti.get('tier_label')
                sel  = ' style="border-color:var(--navy);background:#fff"' if ti.get('is_selected') else ''
                cards += (f'<div class="cvdet-tier"{sel}>'
                          f'<div class="cvdet-tier-lbl">{he(ti["tier_label"])} '
                          f'{"(Selected)" if ti.get("is_selected") else ""}</div>'
                          f'<div class="cvdet-tier-name">{he(name)}</div>'
                          + (f'<div class="cvdet-tier-tag">{he(tag)}</div>' if tag else '')
                          + (f'<ul>{bullets}</ul>' if bullets else '')
                          + f'<div class="cvdet-tier-lbl" style="margin-top:8px">Workmanship</div>'
                          f'<div class="cvdet-tier-tag">{he(ti.get("workmanship", ""))}</div>'
                          + '</div>')
            sections.append(head + f'<div class="cvdet-tiers">{cards}</div>')
        scope_html = ('<section><h4>What&rsquo;s Included &mdash; Materials &amp; Workmanship</h4>'
                      + ''.join(sections) + '</section>')

    # ── Code compliance ────────────────────────────────────────────────
    code = m.get('code')
    code_html = ''
    if code:
        jname = code.get('jurisdiction_name') or ''
        cty   = code.get('county') or ''
        office = code.get('office') or ''
        header = f'<strong>Authority having jurisdiction:</strong> {he(jname)}'
        if cty and cty.lower() not in jname.lower():
            header += f' &middot; {he(cty)} County'
        if office:
            header += f'<br><span style="color:var(--faint);font-size:12.5px">{he(office)}</span>'
        # Manager-approved profile: adopted IRC year + local amendments +
        # reroof permit portal. Only present when a manager has stamped it,
        # so this is the "specific to your address, verified" content the
        # customer is meant to see. Baseline points still render below.
        vp = code.get('verified_profile') or {}
        verified_html = ''
        if vp:
            ac  = vp.get('adopted_code') or ''
            acu = vp.get('adopted_code_source_url') or ''
            va  = (vp.get('verified_at') or '')[:10]
            via = vp.get('verified_via') or ''
            src = ' &middot; '.join(filter(None, [
                f'verified {he(va)}' if va else '',
                f'source: {he(via)}' if via else '',
            ]))
            if ac:
                ac_html = f'<div class="cvdet-code-adopted"><strong>Enforces</strong> {he(ac)}'
                if acu:
                    ac_html += f' &middot; <a href="{he(acu)}" target="_blank" rel="noopener">source</a>'
                ac_html += '</div>'
                verified_html += ac_html
            dele = (vp.get('delegated_to') or '').strip()
            if dele:
                verified_html += ('<div class="cvdet-code-adopted"><strong>Permits issued by</strong> '
                                  f'{he(dele)}</div>')
            amends = vp.get('amendments') or []
            if amends:
                verified_html += ('<div style="margin-top:8px"><b style="font-size:12px;color:var(--faint)">'
                                  'Local amendments applied to this project</b>')
                for a in amends[:8]:
                    tp = (a.get('topic') or '').strip()
                    tx = (a.get('text')  or '').strip()
                    su = (a.get('source_url') or '').strip()
                    label = f'<strong>{he(tp)}:</strong> ' if tp else ''
                    link  = (f' <a href="{he(su)}" target="_blank" rel="noopener" '
                             f'style="font-size:11.5px">source</a>') if su else ''
                    verified_html += f'<div class="cvdet-code-item">{label}{he(tx)}{link}</div>'
                verified_html += '</div>'
            rp = vp.get('reroof_permit') or {}
            rp_bits = []
            if (rp.get('submittal_method') or '') and rp['submittal_method'].lower() != 'unknown':
                rp_bits.append(f'Submittal: {he(rp["submittal_method"])}')
            if (rp.get('portal_url') or '') and rp['portal_url'].lower() != 'unknown':
                rp_bits.append(f'<a href="{he(rp["portal_url"])}" target="_blank" rel="noopener">Permit portal</a>')
            if rp_bits:
                verified_html += ('<div style="margin-top:8px;font-size:12.5px;color:var(--faint)">'
                                  + ' &middot; '.join(rp_bits) + '</div>')
            if src:
                verified_html += (f'<div style="margin-top:6px;font-size:11.5px;color:var(--faint)">'
                                  f'✅ Code data {src}</div>')
        pts = (code.get('jurisdiction_points') or []) + (code.get('baseline_points') or [])
        pt_list = ''.join(f'<li>{he(p)}</li>' for p in pts[:8])
        items_html = ''
        if code.get('code_items'):
            items_html = '<div style="margin-top:8px"><b style="font-size:12px;color:var(--faint)">Code line items in this scope</b>'
            for ci in code['code_items'][:10]:
                lb = ci.get('label') or ''
                bs = ci.get('basis') or ''
                items_html += (f'<div class="cvdet-code-item">{he(lb)}'
                               + (f'<em>{he(bs)}</em>' if bs else '') + '</div>')
            items_html += '</div>'
        # Rendered by _cv_permit_block now, as its own card ahead of this
        # one — jurisdiction first, its requirements separated from the
        # statewide baseline. Kept out of here so the page says it once.
        code_html = ''

    # ── Attic ventilation ──────────────────────────────────────────────
    vent = m.get('ventilation')
    vent_html = ''
    if vent:
        parts = [f'Your attic is <strong>{int(vent["attic_sqft"])} sq ft</strong>.']
        parts.append(f' Colorado code (<strong>{he(vent.get("code_basis", ""))}</strong>) '
                     'requires balanced intake and exhaust totaling '
                     f'<strong>{vent["required_total_sqin"]:.0f} sq in</strong> of net free area '
                     f'({vent["required_intake_sqin"]:.0f} intake / '
                     f'{vent["required_exhaust_sqin"]:.0f} exhaust).')
        if vent.get('deficit_exhaust_sqin', 0) > 0:
            parts.append(f' This estimate cuts in <strong>{vent["ridge_lf_required"]:.0f} LF</strong> '
                         'of ridge vent and adds <strong>'
                         f'{vent["intake_lf_suggested"]} LF</strong> of intake venting at the eaves '
                         'to bring the attic to code.')
        else:
            parts.append(' Existing exhaust already meets code — no ridge vent required.')
        vent_html = ('<section><h4>Attic Ventilation Calculation</h4>'
                     f'<div class="cvdet-vent">{"".join(parts)}</div></section>')

    # ── Warranty summary (tiered) ─────────────────────────────────────
    wt = m.get('warranty_by_tier') or {}
    warr_html = ''
    if wt:
        warr_html = ('<section><h4>Workmanship Warranty</h4><div class="cvdet-warr">'
                     f'<div><b>Good Package</b>{he(wt.get("good", ""))}</div>'
                     f'<div><b>Better Package</b>{he(wt.get("better", ""))}</div>'
                     f'<div><b>Best Package</b>{he(wt.get("best", ""))}</div>'
                     '</div><p style="margin-top:8px;color:var(--faint);font-size:12.5px">'
                     'Manufacturer warranties on the materials themselves are registered '
                     'in your name once the final payment clears.</p></section>')

    # ── Process ────────────────────────────────────────────────────────
    proc = m.get('process') or []
    proc_html = ''
    if proc:
        lis = ''.join(f'<li>{he(p)}</li>' for p in proc)
        proc_html = ('<section><h4>Our Process</h4>'
                     f'<ul class="chk">{lis}</ul></section>')

    # ── Company + certifications ──────────────────────────────────────
    comp = m.get('company') or {}
    about = comp.get('about') or ''
    certs = comp.get('certifications') or []
    certs_html = ''
    if certs:
        certs_html = '<ul class="chk" style="margin-top:6px">' + ''.join(
            f'<li>{he(c)}</li>' for c in certs) + '</ul>'
    revs = m.get('reviews') or {}
    revs_line = ''
    if revs.get('count'):
        revs_line = (f'<p style="margin-top:6px"><strong>{revs["average"]}/5</strong> '
                     f'average across {revs["count"]} homeowner reviews.</p>')
    comp_html = ('<section><h4>About Project One Roofing</h4>'
                 + (f'<p>{he(about)}</p>' if about else '')
                 + f'<p><strong>{he(comp.get("name", ""))}</strong> &middot; '
                 f'{he(comp.get("city", ""))}, {he(comp.get("state", ""))} '
                 f'&middot; {he(comp.get("phone", ""))} &middot; '
                 f'{he(comp.get("website", ""))}</p>'
                 + certs_html + revs_line
                 + '</section>')

    return ('<div class="cvdet">'
            '<h2 class="cvdet-hd" data-eyebrow="The details">What&rsquo;s Included and Why</h2>'
            '<div class="cvdet-lead">A concise summary of what&rsquo;s in this bid '
            '&mdash; materials, code compliance, warranties, and our process &mdash; '
            'so you can compare it apples-to-apples with any other quote.</div>'
            + scope_html + code_html + vent_html + warr_html + proc_html + comp_html
            + '</div>')


def _signed_extras_html(est):
    """Chosen colors + captured initials, for the signed confirmation page."""
    sig = est.get('signature', {}) or {}
    out = ''
    shingle = (sig.get('shingle_color') or '').strip()
    siding  = (sig.get('siding_color')  or '').strip()
    color_cells = []
    if shingle:
        color_cells.append(
            f'<div class="cvgi"><label>Shingle Color</label><strong>{he(shingle)}</strong></div>')
    if siding:
        color_cells.append(
            f'<div class="cvgi"><label>Siding Color</label><strong>{he(siding)}</strong></div>')
    if color_cells:
        out += (f'<div class="cvc-card"><div class="cvgrid">'
                + ''.join(color_cells) + '</div></div>')
    inits = sig.get('initials') or []
    inits = [i for i in inits if (i.get('value') or '').strip()]
    if inits:
        rows = ''.join(
            f'<tr><td>{he(i["text"])}</td><td class="cvinit-val">{he(i["value"])}</td></tr>'
            for i in inits)
        out += (f'<div class="cert"><div class="cert-title">&#9999;&#65039; Initialed Acknowledgements</div>'
                f'<table class="cert-tbl cvinit-tbl">{rows}</table></div>')
    return out


def _insurance_sections(est):
    """Normalized insurance sections list (migrates old flat line_items)."""
    ins_td   = est.get('trades', {}).get('insurance', {})
    sections = ins_td.get('sections', [])
    if not sections and ins_td.get('line_items'):
        sections = [{'id': '_legacy', 'name': '', 'items': ins_td.get('line_items', [])}]
    return sections


def _insurance_cv_table(est):
    """Insurance sections rendered as customer-view tables. Returns (html, total).
    Shared by the sign page and the signed-confirmation page."""
    sections  = _insurance_sections(est)
    ins_total = sum(
        float(i.get('acv') or 0) + float(i.get('depreciation') or 0)
        for sec in sections for i in sec.get('items', [])
    )

    active_sections = [s for s in sections if s.get('items')]
    sections_html = ''
    for sec in active_sections:
        sec_name  = (sec.get('name') or '').strip()
        sec_items = sec.get('items', [])
        sec_total = sum(float(i.get('acv') or 0) + float(i.get('depreciation') or 0) for i in sec_items)
        rows = ''
        for item in sec_items:
            acv   = float(item.get('acv') or 0)
            dep   = float(item.get('depreciation') or 0)
            desc  = (item.get('description') or '').strip()
            rows += f'''<tr>
              <td class="cvn">{he(item.get("name",""))}</td>
              <td class="cvn cvc-desc">{he(desc)}</td>
              <td class="cvr" data-l="ACV">{fc(acv)}</td>
              <td class="cvr" data-l="Depreciation">{fc(dep)}</td>
              <td class="cvr" data-l="RCV">{fc(acv+dep)}</td></tr>'''
        hd = he(sec_name) if sec_name else 'Insurance Estimate Items'
        sections_html += f'''<div class="cvtrade">
          <div class="cvtrade-hd">{hd}</div>
          <table class="cvt cvt-ins"><thead><tr>
            <th scope="col">Item Name</th><th scope="col">Description</th>
            <th scope="col" class="cvth-r">ACV</th><th scope="col" class="cvth-r">Depreciation</th>
            <th scope="col" class="cvth-r">RCV</th></tr></thead>
          <tbody>{rows}</tbody>
          <tfoot><tr><td colspan="4" class="cvsub-l">{(he(sec_name)+' Subtotal') if sec_name else 'Subtotal'}</td>
            <td class="cvr cvsub">{fc(sec_total)}</td></tr></tfoot>
          </table></div>'''

    if active_sections:
        html = sections_html + f'''<div class="cvgrand">
          <span class="cvgrand-lbl">Insurance Claim Total</span>
          <span class="cvgrand-amt">{fc(ins_total)}</span>
        </div>'''
    else:
        html = '<div class="cvnotes" style="text-align:center;color:#9ca3af">No insurance line items entered yet.</div>'
    return html, ins_total


def _build_insurance_cv(est, token):
    """Customer-facing page for insurance-mode estimates (no GBB tier selection)."""
    c         = est.get('customer', {})
    a         = c.get('address', {})
    cs        = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr      = ', '.join(filter(None, [a.get('street'), cs]))
    eid       = est.get('estimate_id', '')
    enum      = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes     = (est.get('notes_customer') or '').strip()
    ctext     = (est.get('contract_text') or '').strip()
    sp        = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()

    ins_td      = est.get('trades', {}).get('insurance', {})
    carrier     = (ins_td.get('carrier') or '').strip()
    claim_num   = (ins_td.get('claim_number') or '').strip()
    scope_notes = (ins_td.get('scope_notes') or '').strip()

    ins_table, ins_total = _insurance_cv_table(est)

    notes_html  = f'<div class="cvnotes"><h2 data-eyebrow="Additional">Notes</h2><p>{he(notes)}</p></div>' if notes else ''
    ctext_html  = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html     = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''
    carrier_row = f'<div class="cvgi"><label>Insurance Carrier</label><strong>{he(carrier)}</strong></div>' if carrier else ''
    claim_row   = f'<div class="cvgi"><label>Claim #</label><strong>{he(claim_num)}</strong></div>' if claim_num else ''
    scope_html  = f'<div class="cvnotes"><h2 data-eyebrow="Scope">Scope of Work</h2><p>{he(scope_notes)}</p></div>' if scope_notes else ''

    manifest = _build_estimate_manifest(est)
    return _cv_head('Your Insurance Estimate &mdash; Project One Roofing', manifest) + _cv_header() + f'''
{_cv_hero(est, 'Your Insurance Estimate is Ready',
          'Review your scope below, then sign at the bottom to accept',
          steps=['Review Your Scope', 'Sign to Accept'])}

<main class="cvmain">
<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Prepared For</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Date</label><strong>{he(est.get("estimate_date","—"))}</strong></div>
    {carrier_row}
    {claim_row}
    {sp_html}
    <div class="cvgi"><label>Valid Until</label><strong>{he(est.get("valid_until","—"))}</strong></div>
  </div>
</div>

{_cv_glance_block(est, manifest)}

{_cv_intro_block(est)}

<!-- Evidence before price — see the note in build_customer_view. -->
{_cv_photos_block(est)}

{_cv_condition_block(est)}

{_cv_visualizer_block(est)}

{_cv_products_block(est)}

{ins_table}
{scope_html}
{notes_html}
{_cv_attachments_block(est)}
{_cv_permit_block(manifest)}
{_cv_estimate_details_block(manifest, est)}
{_cv_trust_blocks(est)}
{_cv_next_steps(commercial=est.get('estimate_type') == 'commercial')}
{_cv_contact_card(est)}
{_cv_download_card(token)}
{ctext_html}

<div class="cvsig" id="sign">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the insurance estimate above and all terms &amp; conditions.</p>
  {_cv_sig_form(_mount_path(f'/sign/{he(token)}'),
                hidden='<input type="hidden" name="selected_tier" value="insurance">',
                extra_blocks=(_cv_shingle_block(est) + _cv_siding_block(est)
                              + _cv_initials_block(est)),
                agree_text='I have read this insurance estimate and I agree to all terms &amp; conditions.')}
</div>
</main>

{_cv_sticky_bar('Insurance Claim Total', fc(ins_total))}
''' + _cv_footer()


def _all_trades_simple(est):
    """Return True when every enabled non-insurance trade is in simple mode.
    Requires at least one enabled trade — pure insurance estimates have no
    retail trades and must not accidentally match this check."""
    trades = est.get('trades', {})
    found_any = False
    for tk in GBB_TRADES:
        td = trades.get(tk, {})
        if not td.get('enabled') or not td.get('line_items'):
            continue
        found_any = True
        trade_mode = _trade_mode(tk, td)
        if trade_mode != 'simple':
            return False
    return found_any


def _build_simple_retail_cv(est, token):
    """Customer view when all enabled trades are simple mode — no GBB tier selection."""
    c     = est.get('customer', {})
    a     = c.get('address', {})
    cs    = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr  = ', '.join(filter(None, [a.get('street'), cs]))
    eid   = est.get('estimate_id', '')
    enum  = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes = (est.get('notes_customer') or '').strip()
    ctext = (est.get('contract_text') or '').strip()
    sp    = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()
    tier  = est.get('selected_tier', 'better')  # passed through for POST; irrelevant for pricing

    notes_html = f'<div class="cvnotes"><h2 data-eyebrow="Additional">Notes</h2><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    li_html, grand_total = render_line_items(est, tier=tier)
    manifest = _build_estimate_manifest(est)

    return _cv_head('Your Estimate — Project One Roofing', manifest) + _cv_header() + f'''
{_cv_hero(est, 'Your Estimate is Ready to Review',
          'Review your estimate below, then sign at the bottom to accept',
          steps=['Review Your Estimate', 'Sign to Accept'])}

<main class="cvmain">
<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Prepared For</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Date</label><strong>{he(est.get("estimate_date","—"))}</strong></div>
    {sp_html}
    <div class="cvgi"><label>Valid Until</label><strong>{he(est.get("valid_until","—"))}</strong></div>
  </div>
</div>

{_cv_glance_block(est, manifest, '', grand_total)}

{_cv_intro_block(est)}

<!-- Evidence before price — see the note in build_customer_view. -->
{_cv_photos_block(est)}

{_cv_condition_block(est)}

{_cv_visualizer_block(est)}

{_cv_products_block(est)}

{li_html}

<div class="cvgrand">
  <span class="cvgrand-lbl">Total</span>
  <span class="cvgrand-amt">{fc(grand_total)}</span>
</div>

{notes_html}
{_cv_attachments_block(est)}
{_cv_permit_block(manifest)}
{_cv_estimate_details_block(manifest, est)}
{_cv_trust_blocks(est)}
{_cv_next_steps(commercial=est.get('estimate_type') == 'commercial')}
{_cv_contact_card(est)}
{_cv_download_card(token)}
{ctext_html}

<div class="cvsig" id="sign">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the estimate above and all terms &amp; conditions.</p>
  {_cv_sig_form(_mount_path(f'/sign/{he(token)}'),
                hidden=f'<input type="hidden" name="selected_tier" value="{he(tier)}">',
                extra_blocks=(_cv_shingle_block(est, chosen_tier=tier)
                              + _cv_siding_block(est, chosen_tier=tier)
                              + _cv_initials_block(est)),
                agree_text='I have read this estimate and I agree to all terms &amp; conditions.')}
</div>
</main>

{_cv_sticky_bar('Your Estimate Total', fc(grand_total))}
''' + _cv_footer()


def build_customer_view(est, token):
    # Branch: insurance estimates — explicit type OR insurance trade is enabled
    ins_td = est.get('trades', {}).get('insurance', {})
    is_insurance = (est.get('estimate_type') == 'insurance') or ins_td.get('enabled', False)
    if is_insurance:
        return _build_insurance_cv(est, token)

    # Branch: all enabled trades are simple mode — skip tier selection
    if _all_trades_simple(est):
        return _build_simple_retail_cv(est, token)

    c    = est.get('customer', {})
    a    = c.get('address', {})
    cs   = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr = ', '.join(filter(None, [a.get('street'), cs]))
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    sp     = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()

    # Packages the rep chose to offer on this estimate (absent key = enabled,
    # so every pre-existing estimate shows all three). Mirrors tierEnabled in
    # app.js. The customer only ever sees the enabled subset.
    te = est.get('tiers_enabled') or {}
    enabled_tiers = [t for t in ('good', 'better', 'best') if te.get(t, True) is not False]
    if not enabled_tiers:
        enabled_tiers = ['good', 'better', 'best']

    notes_html = f'<div class="cvnotes"><h2 data-eyebrow="Additional">Notes</h2><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    tier_lbls = dict(good='Good',    better='Better',  best='Best')

    # Each G/B/B product gets its own package choice; simple-mode trades are
    # priced as-is and always shown. Total = sum of the customer's picks.
    # `other` falls into simple_tks and renders as a plain table — its own
    # tier math still applies, render_line_items reads _trade_mode itself.
    gbb_tks    = [tk for tk in _package_trade_keys(est)
                  if ((est.get('trades') or {}).get(tk) or {}).get('line_items')]
    simple_tks = [tk for tk in GBB_TRADES if tk not in gbb_tks]
    multi      = len(gbb_tks) > 1

    trade_lbls = dict(roofing='Roofing', siding='Siding', windows='Windows',
                      gutters='Gutters', other='Other / Misc')

    # Load once — reused by the shingle/siding color blocks and the tier→color
    # script so the customer's color dropdown swaps with the tier picker.
    pb = _ensure_bundle_catalogs(_load_price_book())

    defaults = {}      # trade → default-selected tier
    totals   = {}      # trade → {tier: subtotal}
    sections_html = ''
    for tk in gbb_tks:
        tfeat, tdesc = _trade_tier_content(est, tk)
        tnames = _tier_package_names(est, tk)
        d_tier = _trade_tier(est, tk)
        if d_tier not in enabled_tiers:
            d_tier = enabled_tiers[0]
        defaults[tk] = d_tier
        totals[tk]   = {t: _trade_subtotal(est, tk, t) for t in enabled_tiers}

        cards_html = ''
        for t in enabled_tiers:
            total  = totals[tk][t]
            # Bullets + tagline that actually match this tier's line items —
            # see _tier_card_content.
            feats, desc = _tier_card_content(pb, est, tk, t, tfeat, tdesc)
            lbl    = tier_lbls[t]
            is_sel = t == d_tier
            popular_badge = '<div class="cv-tier-popular">Most Popular</div>' if t == 'better' else ''
            desc_el = f'<div class="cv-tier-desc">{he(desc)}</div>' if desc else ''
            sysname = str(tnames.get(t) or '').strip()
            sys_el  = f'<div class="cv-tier-system">{he(sysname)}</div>' if sysname else ''
            feats_el = ''
            if feats:
                feats_el = ('<ul class="cv-tier-feats">'
                            + ''.join(f'<li>{he(f)}</li>' for f in feats[:8])
                            + (f'<li class="cv-tier-more">+ {len(feats) - 8} more included</li>' if len(feats) > 8 else '')
                            + '</ul>')
            cards_html += f'''<div class="cv-tier-card {'cv-tier-selected' if is_sel else ''}"
              data-trade="{tk}" data-tier="{t}"
              onclick="selectCvTier('{tk}','{t}')">
              {popular_badge}
              <div class="cv-tier-name">{lbl}</div>
              {sys_el}
              <div class="cv-tier-price">{fc(total)}</div>
              {desc_el}
              {feats_el}
              <div class="cv-tier-check" id="cv-check-{tk}-{t}">{'&#10003; Selected' if is_sel else 'Select'}</div>
            </div>'''

        heading = (f'{trade_lbls.get(tk, tk.title())} &mdash; Choose Your Package'
                   if multi else 'Choose Your Package')
        sections_html += f'''<div class="cv-tier-section">
  <h2 class="cv-tier-heading" data-eyebrow="Your options">{heading}</h2>
  <div class="cv-tier-cards" style="grid-template-columns:repeat({len(enabled_tiers)},1fr)">
    {cards_html}
  </div>
</div>\n'''
        # This product's line items, one block per offered tier
        for t in enabled_tiers:
            li_html, _tot = render_line_items(est, tier=t, only_trades=[tk])
            vis = '' if t == d_tier else 'display:none'
            sections_html += f'<div id="tier-items-{tk}-{t}" style="{vis}">{li_html}</div>\n'

    # Fixed-price (simple-mode) trades render once, after the package sections
    simple_html, simple_total = render_line_items(est, only_trades=simple_tks)

    def _pick_summary(picks):
        return ' &middot; '.join(f'{trade_lbls.get(tk, tk.title())}: {tier_lbls[picks[tk]]}' for tk in gbb_tks)

    default_total = simple_total + sum(totals[tk][defaults[tk]] for tk in gbb_tks)
    default_lbl   = (_pick_summary(defaults) if multi
                     else tier_lbls[defaults[gbb_tks[0]]] + ' Package' if gbb_tks
                     else 'Your Selection')
    # First product's pick doubles as the legacy selected_tier for old consumers
    default_tier  = defaults[gbb_tks[0]] if gbb_tks else est.get('selected_tier', 'better')
    manifest      = _build_estimate_manifest(est)

    return _cv_head('Your Estimate — Project One Roofing', manifest) + _cv_header() + f'''
{_cv_hero(est, 'Your Estimate is Ready to Review',
          'Choose your package below, then sign at the bottom to accept',
          steps=['Review', 'Choose Your Package', 'Sign to Accept'])}

<main class="cvmain">
<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Prepared For</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Date</label><strong>{he(est.get("estimate_date","—"))}</strong></div>
    {sp_html}
    <div class="cvgi"><label>Valid Until</label><strong>{he(est.get("valid_until","—"))}</strong></div>
  </div>
</div>

{_cv_glance_block(est, manifest, default_lbl, default_total)}

{_cv_intro_block(est)}

<!-- What we found: the photographs and the report that reads them, together
     and ABOVE the price. They used to sit on opposite sides of the total, so
     the homeowner met the number before the evidence for it. -->
{_cv_photos_block(est)}

{_cv_condition_block(est)}

{_cv_visualizer_block(est)}

{_cv_products_block(est)}

{sections_html}

{simple_html}

<div class="cvgrand" id="cv-grand-bar">
  <span class="cvgrand-lbl" id="cv-grand-lbl">Total &mdash; {default_lbl}</span>
  <span class="cvgrand-amt" id="cv-grand-amt">{fc(default_total)}</span>
</div>

{notes_html}
{_cv_attachments_block(est)}

<!-- Reassurance belongs between the number and the signature: that is where
     the objections are. -->
{_cv_permit_block(manifest)}
{_cv_estimate_details_block(manifest, est)}
{_cv_trust_blocks(est)}
{_cv_next_steps(commercial=est.get('estimate_type') == 'commercial')}
{_cv_contact_card(est)}
{_cv_download_card(token)}
{ctext_html}

<div class="cvsig" id="sign">
  <h2>Sign to Accept</h2>
  <p class="sub" id="cv-sig-sub">Your electronic signature confirms you have reviewed and agreed to the
    <strong id="cv-sig-tier">{default_lbl}</strong> and all terms above.</p>
  {_cv_sig_form(_mount_path(f'/sign/{he(token)}'),
                hidden=(f'<input type="hidden" name="selected_tier" id="cv-tier-input" value="{he(default_tier)}">'
                        + ''.join(f'<input type="hidden" name="tier_{tk}" id="cv-tier-input-{tk}" value="{he(defaults[tk])}">'
                                  for tk in gbb_tks)),
                extra_blocks=(_cv_shingle_block(est, pb=pb, chosen_tier=default_tier)
                              + _cv_siding_block(est, pb=pb, chosen_tier=default_tier)
                              + _cv_initials_block(est)),
                agree_text='I have read this estimate, selected my package, and I agree to all terms &amp; conditions.',
                btn_id='cv-sign-btn')}
</div>
</main>

{_cv_sticky_bar('Total &mdash; ' + default_lbl, fc(default_total))}

<script>
var _cv_tiers    = {json.dumps(enabled_tiers)};
var _cv_trades   = {json.dumps(gbb_tks)};
var _cv_multi    = {json.dumps(multi)};
var _cv_gbb      = {json.dumps({tk: {'cur': defaults[tk],
                                     'totals': {t: round(totals[tk][t], 2) for t in enabled_tiers}}
                                for tk in gbb_tks})};
var _cv_simple_total = {simple_total:.2f};
var _trade_lbls  = {json.dumps(trade_lbls)};
var _tier_lbls   = {{good:'Good',better:'Better',best:'Best'}};
function _fmt(n){{return'$'+Math.abs(n).toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g,',');}}
function selectCvTier(trade,tier){{
  var g=_cv_gbb[trade]; if(!g)return;
  g.cur=tier;
  _cv_tiers.forEach(function(t){{
    var card=document.querySelector('[data-trade="'+trade+'"][data-tier="'+t+'"]');
    var chk=document.getElementById('cv-check-'+trade+'-'+t);
    if(card&&chk){{
      if(t===tier){{
        card.classList.add('cv-tier-selected');
        chk.innerHTML='&#10003; Selected';
      }}else{{
        card.classList.remove('cv-tier-selected');
        chk.innerHTML='Select';
      }}
    }}
    var blk=document.getElementById('tier-items-'+trade+'-'+t);
    if(blk)blk.style.display=(t===tier?'':'none');
  }});
  var inp=document.getElementById('cv-tier-input-'+trade);
  if(inp)inp.value=tier;
  _cvRefreshTotal();
}}
function _cvRefreshTotal(){{
  var sum=_cv_simple_total, parts=[], first=null;
  _cv_trades.forEach(function(tr){{
    var g=_cv_gbb[tr];
    sum+=(g.totals[g.cur]||0);
    parts.push(_trade_lbls[tr]+': '+_tier_lbls[g.cur]);
    if(first===null)first=g.cur;
  }});
  var lbl=_cv_multi?parts.join(' · '):(first?_tier_lbls[first]+' Package':'Your Selection');
  document.getElementById('cv-grand-lbl').textContent='Total — '+lbl;
  document.getElementById('cv-grand-amt').textContent=_fmt(sum);
  document.getElementById('cv-sig-tier').textContent=lbl;
  var sa=document.getElementById('cvstick-amt'),sl=document.getElementById('cvstick-lbl');
  if(sa)sa.textContent=_fmt(sum);
  if(sl)sl.textContent='Total — '+lbl;
  if(first){{
    document.getElementById('cv-tier-input').value=first;
    if(!_cv_multi){{
      document.getElementById('cv-sign-btn').textContent='✓ Accept — '+_tier_lbls[first]+' Package';
    }}
  }}
}}
// Pre-select tiers from URL params (presentation handoff)
(function(){{
  var p=new URLSearchParams(window.location.search);
  _cv_trades.forEach(function(tr){{
    var t=p.get('tier_'+tr);
    if(t&&_cv_tiers.indexOf(t)>=0)selectCvTier(tr,t);
  }});
}})();
</script>
{_cv_tier_color_script(est, pb=pb)}
''' + _cv_footer()


# ── Tablet presentation mode ─────────────────────────────────────────────
# Slide-by-slide walkthrough for face-to-face estimate presentations on a
# tablet. The customer picks their G/B/B package live; selections carry
# forward to /sign/<token> via query params.

_PRES_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Plus Jakarta Sans',system-ui,sans-serif;background:#0e2440;
  color:#1e293b;overflow:hidden;height:100vh;height:100dvh}
.pw{height:100vh;height:100dvh;display:flex;flex-direction:column}
.ph{background:#0e2440;padding:8px 20px;display:flex;align-items:center;
  justify-content:space-between;flex-shrink:0;border-bottom:1px solid rgba(255,255,255,.08)}
.ph img{height:26px}
.ph-r{color:rgba(255,255,255,.6);font-size:12px;text-align:right}
.pv{flex:1;overflow:hidden;position:relative}
.ps{position:absolute;inset:0;padding:24px 24px 12px;overflow-y:auto;
  opacity:0;pointer-events:none;transition:opacity .32s,transform .32s;
  transform:translateX(30px);display:flex;justify-content:center;align-items:flex-start}
.ps.act{opacity:1;pointer-events:auto;transform:translateX(0)}
.ps.ex{transform:translateX(-30px)}
.pc{background:#fff;border-radius:16px;padding:40px;max-width:920px;width:100%;
  box-shadow:0 8px 40px rgba(0,0,0,.18)}
@media(max-width:600px){.pc{padding:24px 18px;border-radius:12px}}
/* nav */
.pn{background:#0e2440;padding:10px 20px;display:flex;align-items:center;
  justify-content:space-between;flex-shrink:0;border-top:1px solid rgba(255,255,255,.08)}
.pn-b{background:rgba(255,255,255,.1);color:#fff;border:none;padding:10px 28px;
  border-radius:8px;font:600 14px/1 'Plus Jakarta Sans',sans-serif;cursor:pointer;
  min-width:110px;transition:background .15s}
.pn-b:hover{background:rgba(255,255,255,.2)}
.pn-b:disabled{opacity:.25;cursor:default}
.pn-b.pri{background:#0ea5e9}.pn-b.pri:hover{background:#0284c7}
.pn-b.cta{background:#16a34a;font-size:15px;padding:12px 32px}.pn-b.cta:hover{background:#15803d}
.pd{display:flex;gap:6px;align-items:center}
.pd button{width:9px;height:9px;border-radius:9px;background:rgba(255,255,255,.2);
  border:none;cursor:pointer;padding:0;transition:all .2s}
.pd button.act{background:#0ea5e9;width:24px}
.pctr{color:rgba(255,255,255,.45);font-size:12px;margin:0 10px}
/* hero */
.ps-hero{text-align:center;padding:48px 32px}
.ps-hero-cover{width:100%;max-height:320px;object-fit:cover;border-radius:12px;margin-bottom:28px}
.ps-hero h1{font-size:clamp(26px,4vw,38px);font-weight:800;color:#0e2440;margin-bottom:6px}
.ps-hero .sub{font-size:17px;color:#64748b;margin-bottom:4px}
.ps-det{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:14px;text-align:left;margin-top:28px;padding-top:28px;border-top:1px solid #e2e8f0}
.ps-det label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:#94a3b8;margin-bottom:2px;display:block}
.ps-det strong{font-size:15px;color:#1e293b}
/* intro */
.ps-intro h2{font-size:22px;font-weight:700;color:#0e2440;margin-bottom:14px}
.ps-intro p{font-size:15px;line-height:1.75;color:#334155;white-space:pre-wrap}
/* photos */
.ps-ph-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.ps-ph-wrap{border-radius:10px;overflow:hidden;background:#f8fafc}
.ps-ph-wrap img{width:100%;display:block}
.ps-ph-wrap .ps-ph-canvas{position:absolute;top:0;left:0}
.ps-ph-cap{padding:6px 10px;font-size:12px;color:#475569}
/* package selection */
.ps-pkg h2{font-size:clamp(22px,3.5vw,30px);font-weight:800;color:#0e2440;
  text-align:center;margin-bottom:6px}
.ps-pkg .sub{text-align:center;font-size:15px;color:#64748b;margin-bottom:28px}
.ps-pkg-trade{margin-bottom:24px}
.ps-pkg-tn{font-size:16px;font-weight:700;color:#334155;margin-bottom:14px;
  padding-bottom:8px;border-bottom:2px solid #e2e8f0}
.pt-cards{display:grid;gap:14px}
@media(min-width:700px){.pt-cards{grid-template-columns:repeat(var(--cols,3),1fr)}}
.pt-c{border:3px solid #e2e8f0;border-radius:14px;padding:22px;cursor:pointer;
  transition:all .22s;position:relative;background:#fff}
.pt-c:hover{border-color:#94a3b8;box-shadow:0 4px 12px rgba(0,0,0,.06)}
.pt-c.sel{box-shadow:0 4px 20px rgba(0,0,0,.1)}
.pt-pop{position:absolute;top:-11px;right:16px;background:#16a34a;color:#fff;
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  padding:3px 10px;border-radius:16px}
.pt-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;flex-wrap:wrap;gap:4px}
.pt-name{font-size:20px;font-weight:800}
.pt-price{font-size:22px;font-weight:800}
.pt-sys{font-size:13px;font-weight:600;color:#475569;margin-bottom:4px}
.pt-desc{font-size:13px;color:#64748b;margin-bottom:10px}
.pt-feats{list-style:none;padding:0;margin:0}
.pt-feats li{font-size:13px;padding:3px 0;color:#334155}
.pt-feats li::before{content:'\\2713 ';color:#16a34a;font-weight:700}
.pt-chk{margin-top:14px;text-align:center;font-weight:700;font-size:14px;
  padding:10px;border-radius:8px;transition:all .15s}
/* what's-included disclosure inside a trade slide */
.ps-pkg-details{margin-top:22px;border:1px solid #e2e8f0;border-radius:10px;background:#fafbfc}
.ps-pkg-details summary{padding:12px 18px;cursor:pointer;font-weight:600;color:#0e2440;
  font-size:14px;list-style:none;user-select:none}
.ps-pkg-details summary::-webkit-details-marker{display:none}
.ps-pkg-details summary::before{content:'\\25B8 ';color:#94a3b8;margin-right:6px;
  display:inline-block;transition:transform .15s}
.ps-pkg-details[open] summary::before{transform:rotate(90deg)}
.ps-pkg-details .ps-items{padding:6px 18px 18px}
/* staff-only edit hint on a trade slide */
.ps-pkg-edit-hint{text-align:center;font-size:12px;color:#94a3b8;font-style:italic;
  margin:-14px 0 18px;padding:6px 12px;background:#f8fafc;border-radius:6px;
  border:1px dashed #e2e8f0}
/* total bar */
.ps-total{background:#0e2440;color:#fff;border-radius:12px;padding:18px 24px;
  display:flex;align-items:center;justify-content:space-between;margin-top:20px}
.ps-total-l{font-size:15px;font-weight:600;opacity:.8}
.ps-total-a{font-size:26px;font-weight:800}
/* items */
.ps-items h2{font-size:22px;font-weight:800;color:#0e2440;margin-bottom:16px}
.ps-items .cvtrade{margin-bottom:18px}
.ps-items .cvtrade-hd{font-size:15px;font-weight:700;color:#0e2440;margin-bottom:8px;
  padding-bottom:6px;border-bottom:2px solid #e2e8f0}
.ps-items .cvt{width:100%;border-collapse:collapse;font-size:13px}
.ps-items .cvt th{text-align:left;padding:8px 10px;background:#f1f5f9;font-weight:700;
  color:#334155;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.ps-items .cvth-c{text-align:center}
.ps-items .cvth-r{text-align:right}
.ps-items .cvt td{padding:8px 10px;border-bottom:1px solid #f1f5f9;color:#475569}
.ps-items .cvr{text-align:right;font-weight:600;color:#1e293b}
.ps-items .cvc{text-align:center}
.ps-items .cvn .cvd{font-size:11px;color:#94a3b8;margin-top:2px}
.ps-items .cvsub-l{text-align:right;font-weight:700;color:#1e293b}
.ps-items .cvsub{font-weight:700;color:#1e293b}
.ps-items .cvhidden-note{text-align:center;font-style:italic;color:#94a3b8;font-size:12px}
.ps-items .cv-section-row td{font-weight:700;color:#0e2440;background:#f8fafc;font-size:12px;
  text-transform:uppercase;letter-spacing:.5px;padding:6px 10px}
.ps-items .cv-section-sub td{font-weight:600;color:#334155;font-size:12px;background:#fafbfc}
/* trust */
.ps-trust h2{font-size:22px;font-weight:800;color:#0e2440;margin-bottom:20px;text-align:center}
.ps-trust-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.ps-trust-it{background:#f8fafc;padding:22px;border-radius:12px}
.ps-trust-it h3{font-size:15px;font-weight:700;color:#1e293b;margin-bottom:6px}
.ps-trust-it p{font-size:13px;color:#64748b;line-height:1.6}
.ps-trust-it ul{list-style:none;padding:0;margin:0}
.ps-trust-it li{font-size:13px;padding:3px 0;color:#334155}
.ps-trust-it li::before{content:'\\2713 ';color:#16a34a;font-weight:700}
.ps-rev{background:#fffbeb;padding:16px;border-radius:10px;margin-bottom:10px}
.ps-rev-stars{color:#f59e0b;font-size:16px;margin-bottom:4px}
.ps-rev-text{font-size:13px;color:#334155;font-style:italic;line-height:1.5}
.ps-rev-who{font-size:12px;color:#64748b;margin-top:4px}
/* sign CTA */
.ps-sign{text-align:center;padding:32px 20px}
.ps-sign h2{font-size:clamp(22px,3.5vw,30px);font-weight:800;color:#0e2440;margin-bottom:10px}
.ps-sign .sub{font-size:15px;color:#64748b;margin-bottom:28px}
.ps-sign-box{background:#f0fdf4;border:2px solid #16a34a;border-radius:14px;
  padding:24px;margin-bottom:28px;display:inline-block;min-width:280px}
.ps-sign-box .lbl{font-size:13px;color:#16a34a;font-weight:600;margin-bottom:4px}
.ps-sign-box .amt{font-size:clamp(28px,5vw,40px);font-weight:800;color:#15803d}
.ps-sign-btn{display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
  padding:16px 48px;border-radius:12px;font-size:17px;font-weight:700;
  transition:background .2s;border:none;cursor:pointer}
.ps-sign-btn:hover{background:#15803d}
.ps-sign-sel{font-size:14px;color:#475569;margin-bottom:20px}
/* next-steps mini timeline */
.ps-steps{text-align:left;max-width:500px;margin:28px auto 0}
.ps-steps ol{list-style:none;padding:0;counter-reset:step}
.ps-steps li{padding:10px 0 10px 44px;position:relative;border-left:2px solid #e2e8f0;margin-left:14px}
.ps-steps li:last-child{border-left-color:transparent}
.ps-steps li::before{counter-increment:step;content:counter(step);position:absolute;left:-14px;top:8px;
  width:26px;height:26px;background:#0ea5e9;color:#fff;border-radius:50%;font-size:12px;
  font-weight:700;display:flex;align-items:center;justify-content:center}
.ps-steps li strong{display:block;font-size:14px;color:#1e293b}
.ps-steps li span{font-size:12px;color:#64748b}
/* condition */
.ps-cond h2{font-size:22px;font-weight:800;color:#0e2440;margin-bottom:16px}
.ps-cond-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.ps-cond-it{background:#f8fafc;padding:14px;border-radius:10px}
.ps-cond-it label{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;display:block;margin-bottom:2px}
.ps-cond-it strong{font-size:14px;color:#1e293b}
.ps-cond-notes{margin-top:14px;font-size:13px;color:#475569;line-height:1.6;white-space:pre-wrap}
"""


def build_presentation_view(est, token):
    """Tablet presentation — swipeable slides for face-to-face walkthroughs.
    G/B/B package selection is live; the final slide links to /sign/<token>
    with selected tiers as query params."""
    c    = est.get('customer', {})
    a    = c.get('address', {})
    cs   = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr = ', '.join(filter(None, [a.get('street'), cs]))
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    sp   = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()
    pv   = est.get('page_visibility') or {}

    slides = []

    # ── Slide: Hero ────────────────────────────────────────────────────
    cover = _cover_photo_url(est)
    cover_img = f'<img class="ps-hero-cover" src="{he(cover)}" alt="Property">' if cover else ''
    det_items = [
        ('Prepared For', c.get('name', '—')),
        ('Estimate #', enum),
        ('Address', addr or '—'),
        ('Date', est.get('estimate_date', '—')),
    ]
    if sp:
        det_items.append(('Your Consultant', sp))
    det_items.append(('Valid Until', est.get('valid_until', '—')))
    det_html = ''.join(f'<div><label>{he(l)}</label><strong>{he(v)}</strong></div>'
                       for l, v in det_items if v)
    slides.append(('Welcome', f'''<div class="ps-hero">
      {cover_img}
      <h1>Your Estimate is Ready</h1>
      <div class="sub">Prepared exclusively for {he(c.get("name",""))} by Project One Roofing</div>
      <div class="ps-det">{det_html}</div>
    </div>'''))

    # ── Slide: Intro ───────────────────────────────────────────────────
    intro_text = (est.get('intro_text') or '').strip()
    if intro_text and pv.get('intro') is not False:
        slides.append(('Introduction', f'''<div class="ps-intro">
          <h2>A Message From Your Consultant</h2>
          <p>{he(intro_text)}</p>
        </div>'''))

    # ── Slide: Photos ──────────────────────────────────────────────────
    photos = [p for p in (est.get('photos') or [])
              if p.get('show_in_estimate') is not False and p.get('filename')]
    if photos:
        ph_cards = ''
        for p in photos[:12]:
            cap = (p.get('caption') or '').strip()
            cap_html = f'<div class="ps-ph-cap">{he(cap)}</div>' if cap else ''
            anns = p.get('annotations') or []
            ann_attr = f' data-ann=\'{json.dumps(anns)}\'' if anns else ''
            canvas = (f'<canvas class="ps-ph-canvas cvph-canvas"{ann_attr}></canvas>'
                      if anns else '')
            ph_cards += f'''<div class="ps-ph-wrap" style="position:relative">
              <img src="/uploads/{he(p['filename'])}" alt="{he(cap)}" loading="lazy">
              {canvas}{cap_html}</div>'''
        slides.append(('Photos', f'''<div>
          <h2 style="font-size:22px;font-weight:800;color:#0e2440;margin-bottom:16px">
            Photo Report</h2>
          <div class="ps-ph-grid">{ph_cards}</div>
        </div>{_CV_ANN_JS}'''))

    # ── Slide: Property Condition ──────────────────────────────────────
    cond = est.get('property_condition') or est.get('roof_health') or {}
    cond_items = [(k.replace('_', ' ').title(), str(v).strip())
                  for k, v in cond.items()
                  if k != 'notes' and str(v).strip() and str(v).strip().lower() != 'n/a']
    cond_notes = (cond.get('notes') or '').strip()
    if cond_items:
        ci_html = ''.join(f'<div class="ps-cond-it"><label>{he(l)}</label><strong>{he(v)}</strong></div>'
                          for l, v in cond_items)
        notes_h = f'<div class="ps-cond-notes">{he(cond_notes)}</div>' if cond_notes else ''
        slides.append(('Condition', f'''<div class="ps-cond">
          <h2>Property Condition Report</h2>
          <div class="ps-cond-grid">{ci_html}</div>
          {notes_h}
        </div>'''))

    # ── Slide: Package Selection (GBB, interactive) ────────────────────
    is_insurance = (est.get('estimate_type') == 'insurance') or \
                   (est.get('trades', {}).get('insurance', {}).get('enabled', False))

    te = est.get('tiers_enabled') or {}
    enabled_tiers = [t for t in ('good', 'better', 'best') if te.get(t, True) is not False]
    if not enabled_tiers:
        enabled_tiers = ['good', 'better', 'best']

    tier_clrs = dict(good='#2563eb', better='#16a34a', best='#b45309')
    tier_bgs  = dict(good='#dbeafe', better='#dcfce7', best='#fef3c7')
    tier_lbls = dict(good='Good',    better='Better',  best='Best')
    trade_lbls = dict(roofing='Roofing', siding='Siding', windows='Windows',
                      gutters='Gutters', other='Other / Misc')

    # Needed to tell a still-current bundle from one the rep replaced by hand —
    # see _tier_bullets_are_stale.
    pb = _ensure_bundle_catalogs(_load_price_book())

    gbb_tks    = [tk for tk in _package_trade_keys(est)
                  if ((est.get('trades') or {}).get(tk) or {}).get('line_items')]
    simple_tks = [tk for tk in GBB_TRADES if tk not in gbb_tks]
    multi      = len(gbb_tks) > 1

    defaults = {}
    totals   = {}

    if not is_insurance and gbb_tks:
        # Compute per-trade defaults + totals up front so the sign slide can
        # reach them, then emit ONE slide per trade — cards, details, subtotal.
        is_staff = bool(session.get('user'))
        # The Options tab that used to edit these is gone. Bullets now track the
        # Pricing tab's line items whenever the stored copy has gone stale, so
        # the hint points a rep at the place that actually changes them.
        edit_hint = ('<div class="ps-pkg-edit-hint">These bullets come from the '
                     'package bundle, or from your line items when you build a '
                     'package by hand — edit them on the Pricing tab</div>') if is_staff else ''
        for tk in gbb_tks:
            tfeat, tdesc = _trade_tier_content(est, tk)
            tnames = _tier_package_names(est, tk)
            d_tier = _trade_tier(est, tk)
            if d_tier not in enabled_tiers:
                d_tier = enabled_tiers[0]
            defaults[tk] = d_tier
            totals[tk] = {t: _trade_subtotal(est, tk, t) for t in enabled_tiers}

            cards = ''
            for t in enabled_tiers:
                total  = totals[tk][t]
                feats, desc = _tier_card_content(pb, est, tk, t, tfeat, tdesc)
                clr    = tier_clrs[t]
                bg     = tier_bgs[t]
                lbl    = tier_lbls[t]
                is_sel = t == d_tier
                pop    = '<div class="pt-pop">Most Popular</div>' if t == 'better' else ''
                desc_e = f'<div class="pt-desc">{he(desc)}</div>' if desc else ''
                sysname = str(tnames.get(t) or '').strip()
                sys_e   = f'<div class="pt-sys">{he(sysname)}</div>' if sysname else ''
                feats_e = ''
                if feats:
                    feats_e = ('<ul class="pt-feats">'
                               + ''.join(f'<li>{he(f)}</li>' for f in feats[:8])
                               + ('</ul>'))
                chk_txt = '&#10003; Selected' if is_sel else 'Tap to Select'
                chk_bg  = bg if is_sel else '#f8fafc'
                chk_clr = clr if is_sel else '#94a3b8'
                cards += f'''<div class="pt-c{'  sel' if is_sel else ''}"
                  data-trade="{tk}" data-tier="{t}"
                  style="border-color:{clr if is_sel else '#e2e8f0'};{'background:'+bg if is_sel else ''}"
                  onclick="presSelect('{tk}','{t}')">
                  {pop}
                  <div class="pt-top">
                    <div class="pt-name" style="color:{clr}">{lbl}</div>
                    <div class="pt-price" style="color:{clr}">{fc(total)}</div>
                  </div>
                  {sys_e}{desc_e}{feats_e}
                  <div class="pt-chk" style="background:{chk_bg};color:{chk_clr}">{chk_txt}</div>
                </div>'''

            # Line-item detail blocks for THIS trade at each tier (JS toggles
            # visibility as the customer taps a package).
            items_blocks = ''
            for t in enabled_tiers:
                li_html, _tot = render_line_items(est, tier=t, only_trades=[tk])
                vis = '' if t == d_tier else 'display:none'
                items_blocks += (f'<div id="pt-items-{tk}-{t}" '
                                 f'style="{vis}">{li_html}</div>\n')

            trade_lbl = trade_lbls.get(tk, tk.title())
            slides.append((f'{trade_lbl}', f'''<div class="ps-pkg">
              <h2>Your {he(trade_lbl)} Package</h2>
              <div class="sub">Tap to select &mdash; your subtotal updates instantly</div>
              {edit_hint}
              <div class="pt-cards" style="--cols:{len(enabled_tiers)}">{cards}</div>
              <details class="ps-pkg-details">
                <summary>What&rsquo;s included in this package</summary>
                <div class="ps-items">{items_blocks}</div>
              </details>
              <div class="ps-total">
                <div class="ps-total-l" id="pt-sub-{tk}-lbl">{he(trade_lbl)} &mdash;
                  {he(tier_lbls[d_tier])}</div>
                <div class="ps-total-a" id="pt-sub-{tk}-amt">{fc(totals[tk][d_tier])}</div>
              </div>
            </div>'''))

        simple_html_pres, simple_total = render_line_items(est, only_trades=simple_tks)
        default_total = simple_total + sum(totals[tk][defaults[tk]] for tk in gbb_tks)
        default_lbl   = (' · '.join(f'{trade_lbls.get(tk, tk.title())}: {tier_lbls[defaults[tk]]}'
                                     for tk in gbb_tks) if multi
                         else tier_lbls[defaults[gbb_tks[0]]] + ' Package')

        # Fixed-price simple trades ride their own slide (rare — usually
        # everything is G/B/B or nothing is)
        if simple_html_pres.strip():
            slides.append(('Also Included', f'''<div class="ps-items">
              <h2>Also Included</h2>
              <div class="sub" style="text-align:center;font-size:15px;color:#64748b;margin-bottom:20px">
                Fixed-price items in this estimate</div>
              {simple_html_pres}
              <div class="ps-total">
                <div class="ps-total-l">Fixed Items Subtotal</div>
                <div class="ps-total-a">{fc(simple_total)}</div>
              </div>
            </div>'''))

    elif is_insurance:
        ins_html, ins_total = _insurance_cv_table(est)
        slides.append(('Pricing', f'''<div class="ps-items">
          <h2>Insurance Claim Summary</h2>
          {ins_html}
        </div>'''))

    else:
        # Simple-mode-only estimate
        all_html, all_total = render_line_items(est)
        slides.append(('Pricing', f'''<div class="ps-items">
          <h2>Your Estimate</h2>
          {all_html}
          <div class="ps-total">
            <div class="ps-total-l">Total</div>
            <div class="ps-total-a">{fc(all_total)}</div>
          </div>
        </div>'''))

    # ── Slide: Trust blocks ────────────────────────────────────────────
    cc = _load_company_content()
    trust_items = []
    for key, dflt_title, icon in (('about', 'About Us', '&#127968;'),
                                   ('warranty', 'Our Warranty', '&#128737;&#65039;')):
        blk = cc.get(key) or {}
        if not blk.get('enabled', True) or pv.get(f'trust_{key}') is False:
            continue
        body = (blk.get('body') or '').strip()
        if not body:
            continue
        title = (blk.get('title') or '').strip() or dflt_title
        paras = ''.join(f'<p>{he(p.strip())}</p>' for p in body.split('\n\n') if p.strip())
        trust_items.append(f'<div class="ps-trust-it"><h3>{icon} {he(title)}</h3>{paras}</div>')

    blk = cc.get('certifications') or {}
    if blk.get('enabled', True) and pv.get('trust_certifications') is not False:
        items = [str(i).strip() for i in (blk.get('items') or []) if str(i).strip()]
        if items:
            title = (blk.get('title') or '').strip() or 'Licenses & Certifications'
            lis = ''.join(f'<li>{he(i)}</li>' for i in items)
            trust_items.append(f'<div class="ps-trust-it"><h3>&#127942; {he(title)}</h3><ul>{lis}</ul></div>')

    blk = cc.get('reviews') or {}
    revs_html = ''
    if blk.get('enabled', True) and pv.get('trust_reviews') is not False:
        revs = [r for r in (blk.get('items') or [])
                if isinstance(r, dict) and (r.get('text') or '').strip()]
        for r in revs[:4]:
            try: n = max(1, min(5, int(r.get('stars') or 5)))
            except (TypeError, ValueError): n = 5
            name = (r.get('name') or '').strip()
            who  = f'<div class="ps-rev-who">&mdash; {he(name)}</div>' if name else ''
            revs_html += f'''<div class="ps-rev">
              <div class="ps-rev-stars">{'&#9733;' * n}</div>
              <div class="ps-rev-text">&ldquo;{he(r["text"].strip())}&rdquo;</div>
              {who}</div>'''

    if trust_items or revs_html:
        slides.append(('Why Us', f'''<div class="ps-trust">
          <h2>Why Project One Roofing</h2>
          <div class="ps-trust-grid">{''.join(trust_items)}</div>
          {revs_html}
        </div>'''))

    # ── Slide: Sign CTA ───────────────────────────────────────────────
    if not is_insurance and gbb_tks:
        sign_total = default_total
        sign_lbl   = default_lbl
    elif is_insurance:
        sign_total = ins_total
        sign_lbl   = 'Insurance Claim Total'
    else:
        sign_total = all_total
        sign_lbl   = 'Your Estimate Total'

    sign_url = f'/sign/{he(token)}'
    steps_html = '''<div class="ps-steps"><ol>
      <li><strong>Sign electronically</strong><span>Takes less than a minute, right from this device</span></li>
      <li><strong>We reach out to welcome you</strong><span>A call within one business day to confirm details</span></li>
      <li><strong>Scheduling &amp; materials</strong><span>We order materials and lock in your installation date</span></li>
      <li><strong>Installation day</strong><span>Our crew arrives on time and leaves your home spotless</span></li>
      <li><strong>Final walkthrough &amp; warranty</strong><span>We walk the project with you and register your warranty</span></li>
    </ol></div>'''

    slides.append(('Ready to Sign', f'''<div class="ps-sign">
      <h2>Ready to Move Forward?</h2>
      <div class="sub" id="pt-sign-sel">{he(sign_lbl)}</div>
      <div class="ps-sign-box">
        <div class="lbl">Your Total</div>
        <div class="amt" id="pt-sign-amt">{fc(sign_total)}</div>
      </div>
      <div><a class="ps-sign-btn" id="pt-sign-link" href="{sign_url}#sign"
         data-base="{sign_url}">&#10003; Review &amp; Sign Electronically</a></div>
      {steps_html}
    </div>'''))

    # ── Assemble ───────────────────────────────────────────────────────
    slides_html = '\n'.join(
        f'<div class="ps{" act" if i == 0 else ""}" data-idx="{i}"><div class="pc">{html}</div></div>'
        for i, (_label, html) in enumerate(slides))
    dots = ''.join(
        f'<button class="{"act " if i == 0 else ""}" data-idx="{i}"></button>'
        for i in range(len(slides)))
    n = len(slides)

    # Build the JS (package selection + navigation + swipe)
    gbb_js = ''
    if not is_insurance and gbb_tks:
        _ptg_data = json.dumps({tk: {'cur': defaults[tk],
                                     'totals': {t: round(totals[tk][t], 2) for t in enabled_tiers}}
                                for tk in gbb_tks})
        gbb_js = f'''
var _pt={json.dumps(enabled_tiers)};
var _ptr={json.dumps(gbb_tks)};
var _ptm={json.dumps(multi)};
var _ptg={_ptg_data};
var _pts={simple_total:.2f};
var _tl={json.dumps(trade_lbls)};
var _tn={{good:'Good',better:'Better',best:'Best'}};
var _tc={{good:'#2563eb',better:'#16a34a',best:'#b45309'}};
var _tb={{good:'#dbeafe',better:'#dcfce7',best:'#fef3c7'}};
function _fmt(n){{return'$'+Math.abs(n).toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g,',');}}
function presSelect(trade,tier){{
  var g=_ptg[trade];if(!g)return;g.cur=tier;
  _pt.forEach(function(t){{
    var card=document.querySelector('[data-trade="'+trade+'"][data-tier="'+t+'"]');
    if(!card)return;
    var chk=card.querySelector('.pt-chk');
    if(t===tier){{
      card.classList.add('sel');
      card.style.borderColor=_tc[t];card.style.background=_tb[t];
      if(chk){{chk.innerHTML='\\u2713 Selected';chk.style.background=_tb[t];chk.style.color=_tc[t];}}
    }}else{{
      card.classList.remove('sel');
      card.style.borderColor='#e2e8f0';card.style.background='';
      if(chk){{chk.innerHTML='Tap to Select';chk.style.background='#f8fafc';chk.style.color='#94a3b8';}}
    }}
    var blk=document.getElementById('pt-items-'+trade+'-'+t);
    if(blk)blk.style.display=(t===tier?'':'none');
  }});
  _presTotal();
}}
function _presTotal(){{
  var sum=_pts,parts=[];
  _ptr.forEach(function(tr){{var g=_ptg[tr];var sub=g.totals[g.cur]||0;sum+=sub;
    parts.push(_tl[tr]+': '+_tn[g.cur]);
    // Per-trade subtotal on the trade's own slide
    var sblbl=document.getElementById('pt-sub-'+tr+'-lbl');
    if(sblbl)sblbl.textContent=_tl[tr]+' \\u2014 '+_tn[g.cur];
    var sbamt=document.getElementById('pt-sub-'+tr+'-amt');
    if(sbamt)sbamt.textContent=_fmt(sub);
  }});
  var lbl=_ptm?parts.join(' \\xb7 '):(_tn[_ptg[_ptr[0]].cur]+' Package');
  var el=document.getElementById('pt-total-lbl');if(el)el.textContent='Total \\u2014 '+lbl;
  var ea=document.getElementById('pt-total-amt');if(ea)ea.textContent=_fmt(sum);
  var sa=document.getElementById('pt-sign-amt');if(sa)sa.textContent=_fmt(sum);
  var sl=document.getElementById('pt-sign-sel');if(sl)sl.textContent=lbl;
  // Update sign link with tier params
  var link=document.getElementById('pt-sign-link');
  if(link){{var base=link.dataset.base,p=[];
    _ptr.forEach(function(tr){{p.push('tier_'+tr+'='+_ptg[tr].cur);}});
    if(_ptr.length)p.push('tier='+_ptg[_ptr[0]].cur);
    link.href=base+'?'+p.join('&')+'#sign';
  }}
}}'''

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<meta name="theme-color" content="#0e2440">
<link rel="icon" href="/estimate/static/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<title>Estimate Presentation — Project One Roofing</title>
<style>{_PRES_CSS}</style></head><body>
<div class="pw">
  <div class="ph">
    <img src="/estimate/static/logo.png" alt="Project One Roofing">
    <div class="ph-r">{he(c.get("name",""))} &middot; {he(enum)}</div>
  </div>
  <div class="pv" id="pv">{slides_html}</div>
  <div class="pn">
    <button class="pn-b" id="pres-prev" onclick="presNav(-1)" disabled>&#8249; Back</button>
    <div class="pd" id="pres-dots">{dots}</div>
    <span class="pctr" id="pres-ctr">1 / {n}</span>
    <button class="pn-b pri" id="pres-next" onclick="presNav(1)">Next &#8250;</button>
  </div>
</div>
<script>
var _cur=0,_n={n};
var _sl=document.querySelectorAll('.ps');
var _dt=document.querySelectorAll('.pd button');
function presGo(i){{
  if(i<0||i>=_n)return;
  _sl.forEach(function(s){{s.classList.remove('act','ex');}});
  _sl[i].classList.add('act');
  _dt.forEach(function(d,j){{d.classList.toggle('act',j===i);}});
  _cur=i;
  document.getElementById('pres-prev').disabled=(i===0);
  var nb=document.getElementById('pres-next');
  nb.style.display=(i===_n-1)?'none':'';
  document.getElementById('pres-ctr').textContent=(i+1)+' / '+_n;
}}
function presNav(d){{presGo(_cur+d);}}
_dt.forEach(function(d){{d.addEventListener('click',function(){{presGo(+d.dataset.idx);}});}});
// Swipe
var _tx=0,_ty=0;
document.getElementById('pv').addEventListener('touchstart',function(e){{
  _tx=e.touches[0].clientX;_ty=e.touches[0].clientY;}});
document.getElementById('pv').addEventListener('touchend',function(e){{
  var dx=e.changedTouches[0].clientX-_tx,dy=e.changedTouches[0].clientY-_ty;
  if(Math.abs(dx)>Math.abs(dy)&&Math.abs(dx)>50)presNav(dx<0?1:-1);
}});
// Keyboard
document.addEventListener('keydown',function(e){{
  if(e.key==='ArrowRight')presNav(1);if(e.key==='ArrowLeft')presNav(-1);
}});
{gbb_js}
</script></body></html>'''


def build_signed_confirmation(est):
    sig  = est.get('signature', {}) or {}
    sname = sig.get('name', '')
    semail= sig.get('email', '')
    stime = sig.get('signed_at', '')
    ip    = sig.get('ip_address', '')
    dhash = sig.get('document_hash', '')

    try:
        dt = datetime.fromisoformat(stime.replace('Z', '+00:00'))
        stime_fmt = dt.strftime('%B %d, %Y at %I:%M %p UTC')
    except Exception:
        stime_fmt = stime

    c    = est.get('customer', {})
    a    = c.get('address', {})
    cs   = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr = ', '.join(filter(None, [a.get('street'), cs]))
    tier = est.get('selected_tier', 'better')
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'

    # Line items + totals must match what the customer signed: insurance shows
    # the RCV sections table (with its own total bar), simple retail shows a
    # plain total, GBB retail shows the chosen package.
    is_ins = (est.get('estimate_type') == 'insurance'
              or bool((est.get('trades', {}).get('insurance') or {}).get('enabled')))
    if is_ins:
        tlbl = 'Insurance Claim'
        li_html, gtotal = _insurance_cv_table(est)
        total_bar = ''  # the insurance table already ends with its own total bar
    else:
        li_html, gtotal = render_line_items(est)
        if _all_trades_simple(est):
            tlbl      = 'Estimate'
            total_lbl = 'Total'
        else:
            tlbl      = _pick_summary_label(est) or \
                dict(good='Good', better='Better', best='Best').get(tier, tier.title()) + ' Package'
            total_lbl = f'Total &mdash; {he(tlbl)}'
        total_bar = f'''<div class="cvgrand" style="margin-top:14px">
  <span class="cvgrand-lbl">{total_lbl}</span>
  <span class="cvgrand-amt">{fc(gtotal)}</span>
</div>'''

    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    notes_html = f'<div class="cvnotes"><h2 data-eyebrow="Additional">Notes</h2><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract" open><summary>&#128203; Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    email_row  = f'<tr><td>Email</td><td>{he(semail)}</td></tr>' if semail else ''
    hash_disp  = (dhash[:32] + '&hellip;') if len(dhash) > 32 else he(dhash)

    fname = (sname.split() or [''])[0]
    hero_h1 = f'You&rsquo;re All Set, {he(fname)}!' if fname else 'Estimate Accepted!'
    manifest = _build_estimate_manifest(est)
    return _cv_head('Estimate Accepted &mdash; Project One Roofing', manifest) + _cv_header() + f'''
<div class="cvhero ok">
  <div class="cvhero-brand" style="color:#86efac">Project One Roofing</div>
  <div class="cv-check">&#10003;</div>
  <h1>{hero_h1}</h1>
  <p>Thank you &mdash; your signed copy is below. Project One Roofing will be in touch soon to schedule your project.</p>
  <button class="cv-print-btn" onclick="window.print()">&#128424; Save / Print Signed Copy</button>
</div>

<main class="cvmain">
{_cv_next_steps(signed=True, commercial=est.get('estimate_type') == 'commercial')}

<div class="cert">
  <div class="cert-title">&#128274; Electronic Signature Certificate</div>
  <table class="cert-tbl">
    <tr><td>Document</td><td>{he(enum)} &mdash; {he(c.get("name",""))}</td></tr>
    <tr><td>Signed By</td><td>{he(sname)}</td></tr>
    {email_row}
    <tr><td>Signed On</td><td>{he(stime_fmt)}</td></tr>
    <tr><td>IP Address</td><td>{he(ip)}</td></tr>
    <tr><td>Estimate ID</td><td><span class="mono">{he(eid)}</span></td></tr>
    <tr><td>Doc Hash</td><td><span class="mono">{hash_disp}</span></td></tr>
  </table>
  <p class="cert-legal">This document was electronically signed in accordance with the federal Electronic
  Signatures in Global and National Commerce Act (E-SIGN Act, 15 U.S.C. &sect;&nbsp;7001) and the Uniform
  Electronic Transactions Act (UETA). The electronic signature has the same legal effect as a handwritten
  signature. The Document Hash above is a SHA-256 fingerprint of the estimate at the time of signing &mdash;
  any modification to the document would produce a different hash value.</p>
</div>

<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Customer</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    {f'<div class="cvgi"><label>Package</label><strong>{he(tlbl)}</strong></div>' if tlbl != 'Estimate' else ''}
  </div>
</div>

{_signed_extras_html(est)}

{_cv_products_block(est)}

{li_html}

{total_bar}

{notes_html}
{ctext_html}
{_cv_contact_card(est)}
</main>
''' + _cv_footer(f'<div class="cvftr-sub">Signed: {he(stime_fmt)} &middot; IP: {he(ip)}</div>')


# ── E-Signature routes ──────────────────────────────────────────────────────

@app.route('/api/server-info', methods=['GET'])
def server_info():
    """Return network info so the frontend can build share URLs correctly."""
    return jsonify({'base_url': _base_url(), 'lan_ip': LAN_IP, 'public_url': get_public_url()})


@app.route('/api/server-info', methods=['PUT'])
def save_server_info():
    """Admin-only: persist a custom public_url to config.json — it changes the
    share links every rep generates."""
    if not _is_admin(_current_user()):
        return _forbid()
    data = request.get_json(force=True)
    new_url = (data.get('public_url') or '').strip().rstrip('/')
    cfg = os.path.join(DATA_DIR, 'config.json')
    try:
        cfg_data = json.load(open(cfg)) if os.path.exists(cfg) else {}
    except Exception:
        cfg_data = {}
    cfg_data['public_url'] = new_url
    with open(cfg, 'w') as f:
        json.dump(cfg_data, f, indent=2)
    global PUBLIC_URL
    PUBLIC_URL = new_url
    return jsonify({'ok': True, 'base_url': _base_url()})


def _ensure_share_token(est):
    """Mark an estimate sent and give it a share token if it lacks one.
    Atomic — a concurrent share/send can't mint two different tokens."""
    fresh_token = secrets.token_urlsafe(24)

    def _mark(doc):
        if doc is None:
            doc = est
        doc['share_token'] = doc.get('share_token') or fresh_token
        if not doc.get('sent_at'):
            doc['sent_at'] = datetime.utcnow().isoformat() + 'Z'
        if doc.get('status') in (None, '', 'draft'):
            doc['status'] = 'sent'
        doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        return doc

    stored = est_update(est.get('estimate_id'), _mark)
    est.update(stored or {})
    _funnel_record(est, 'sent', at=est.get('sent_at') or '')
    return est['share_token']


@app.route('/api/estimates/<est_id>/share', methods=['POST'])
def create_share_link(est_id):
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    token = _ensure_share_token(est)
    base = _base_url()
    return jsonify({'token': token, 'url': f'/sign/{token}', 'full_url': f'{base}/sign/{token}'})


@app.route('/api/estimates/<est_id>/send-email', methods=['POST'])
def email_estimate_link(est_id):
    """Email the customer their signing link directly from the app (SendGrid),
    so reps don't have to copy/paste on a phone."""
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()

    body    = request.get_json(silent=True) or {}
    to_addr = (body.get('email') or est.get('customer', {}).get('email') or '').strip()
    if not to_addr or '@' not in to_addr:
        return jsonify({'error': 'No customer email address on this estimate.'}), 400

    token    = _ensure_share_token(est)
    base     = get_public_url()
    if not base:
        return jsonify({'error': 'No public URL configured — the emailed link would not '
                                 'be reachable. Use Copy Link instead.'}), 400
    sign_url = f'{base}/sign/{token}'
    c        = est.get('customer', {})
    first    = (c.get('name') or 'there').split(' ')[0]
    enum     = _est_number(est)
    rep      = _display_name(est.get('salesperson')) if est.get('salesperson') else 'Project One Roofing'

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#1a3a5c;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">Your Estimate is Ready</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">Hi {he(first)} — your estimate {he(enum)} is ready to review and sign online.</p>
  </div>
  <div style="padding:22px 26px">
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 18px">
      Open the link below on any phone or computer to review your estimate,
      choose your options, and sign electronically. No app or account needed.</p>
    <a href="{he(sign_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:14px 24px;border-radius:6px;font-weight:700;font-size:15px;margin-bottom:18px">
      View &amp; Sign Your Estimate →</a>
    <p style="font-size:12px;color:#6b7280;line-height:1.6;margin:0">
      Questions? Just reply to this email or call us at 970-776-0945.<br>
      — {he(rep)}, Project One Roofing</p>
  </div>
</div>
</body></html>'''

    ok = _send_email(f'Your roofing estimate from Project One Roofing ({enum})',
                     html_body, to_addr, cc=_salesperson_email(est) or None)
    if not ok:
        return jsonify({'error': 'Email could not be sent — check the email settings.'}), 502
    return jsonify({'ok': True, 'sent_to': to_addr, 'full_url': sign_url})


def _send_email(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send an HTML email. Prefers the SendGrid HTTP API (HTTPS/443), which works
    on hosts that block outbound SMTP ports like Railway; falls back to SMTP when
    no API key is available. Logs errors, never raises.
    attachments: list of (filename, bytes) tuples."""
    if not to_addr:
        return False

    # Don't BCC the primary recipient — SendGrid would silently drop the duplicate,
    # but the intent ("I'm already getting this") is clearer this way.
    if bcc and to_addr and bcc.strip().lower() == to_addr.strip().lower():
        bcc = None

    # Prefer the SendGrid Web API when we have a key. SendGrid's SMTP login uses
    # the literal username "apikey" and the API key as the password, so we can
    # reuse SMTP_PASS as the API key when SENDGRID_API_KEY isn't set explicitly.
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key and os.environ.get('SMTP_USER', '').strip() == 'apikey':
        api_key = os.environ.get('SMTP_PASS', '').strip()
    if api_key and http is not None:
        if _send_via_sendgrid_api(api_key, subject, html_body, to_addr, cc, attachments, bcc):
            return True
        # API failed — try SMTP as a last resort (may also be blocked)
    return _send_via_smtp(subject, html_body, to_addr, cc, attachments, bcc)


def _send_via_sendgrid_api(api_key, subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send through SendGrid's v3 HTTP API over HTTPS. Returns True on success."""
    from email.utils import parseaddr
    smtp_from = (os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USER') or '').strip()
    from_name, from_email = parseaddr(smtp_from)
    if not from_email:
        # Fallback so SendGrid doesn't reject the request due to missing sender
        from_email = 'noreply@projectoneroofing.com'
        from_name  = 'Project One Roofing'

    personalization = {'to': [{'email': to_addr}]}
    if cc:
        cc_list = [{'email': x.strip()} for x in cc.split(',') if x.strip()]
        if cc_list:
            personalization['cc'] = cc_list
    if bcc:
        bcc_list = [{'email': x.strip()} for x in bcc.split(',') if x.strip()]
        if bcc_list:
            personalization['bcc'] = bcc_list

    payload = {
        'personalizations': [personalization],
        'from': {'email': from_email, 'name': from_name or 'Project One Roofing'},
        'subject': subject,
        'content': [{'type': 'text/html', 'value': html_body}],
    }
    if attachments:
        import base64
        payload['attachments'] = [{
            'content':     base64.b64encode(data).decode('ascii'),
            'filename':    fname,
            'type':        'application/pdf',
            'disposition': 'attachment',
        } for fname, data in attachments]

    try:
        resp = http.post('https://api.sendgrid.com/v3/mail/send',
                         json=payload,
                         headers={'Authorization': f'Bearer {api_key}',
                                  'Content-Type': 'application/json'},
                         timeout=15)
        if resp.status_code in (200, 201, 202):
            print(f'[email] Sent "{subject}" to {to_addr} via SendGrid API')
            return True
        print(f'[email] SendGrid API rejected "{subject}" to {to_addr}: '
              f'{resp.status_code} {resp.text[:300]}')
        return False
    except Exception as exc:
        print(f'[email] SendGrid API error for "{subject}" to {to_addr}: {exc}')
        return False


def _send_via_smtp(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send an HTML email via configured SMTP. Logs errors, never raises."""
    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    if not smtp_host or not to_addr:
        return False
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_pass = os.environ.get('SMTP_PASS', '').strip()
    smtp_from = os.environ.get('SMTP_FROM', smtp_user).strip() or smtp_user

    msg = MIMEMultipart('mixed' if attachments else 'alternative')
    msg['Subject'] = subject
    msg['From']    = smtp_from
    msg['To']      = to_addr
    recipients     = [to_addr]
    if cc:
        msg['Cc'] = cc
        recipients += [x.strip() for x in cc.split(',') if x.strip()]
    if bcc:
        # Deliberately NOT setting msg['Bcc'] — the whole point is that other
        # recipients can't see it. Just add to the envelope.
        recipients += [x.strip() for x in bcc.split(',') if x.strip()]
    msg.attach(MIMEText(html_body, 'html'))
    for fname, data in (attachments or []):
        part = MIMEApplication(data, Name=fname)
        part['Content-Disposition'] = f'attachment; filename="{fname}"'
        msg.attach(part)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as srv:
            srv.ehlo()
            srv.starttls()
            if smtp_user and smtp_pass:
                srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_from, recipients, msg.as_string())
        print(f'[email] Sent "{subject}" to {to_addr} via SMTP')
        return True
    except Exception as exc:
        print(f'[email] Failed to send "{subject}" to {to_addr} via SMTP: {exc}')
        return False


def _est_number(est):
    eid = est.get('estimate_id', '')
    return 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'


def _salesperson_email(est):
    sp = (est.get('salesperson') or '').strip()
    return f'{sp}@projectoneroofing.com' if sp else ''


def send_view_notification(est):
    """Email the rep the first time a customer opens their estimate link."""
    to_addr = _salesperson_email(est)
    if not to_addr:
        print('[notify] No salesperson on estimate — skipping view notification')
        return
    c        = est.get('customer', {})
    enum     = _est_number(est)
    total    = _estimate_total(est)
    cname    = c.get('name', 'Your customer')
    base     = _base_url()
    sign_url = f"{base}/sign/{est.get('share_token','')}"

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#0284c7;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">&#128064; Estimate Viewed</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">{he(cname)} just opened estimate {he(enum)} for the first time.</p>
  </div>
  <div style="padding:22px 26px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Customer</td><td style="padding:5px 0;font-size:13px;font-weight:700">{he(cname)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Estimate</td><td style="padding:5px 0;font-size:13px">{he(enum)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Value</td><td style="padding:5px 0;font-size:15px;font-weight:800;color:#0284c7">{fc(total)}</td></tr>
    </table>
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 18px">
      They're looking at it right now — this is the best moment to call and answer questions.</p>
    <a href="{he(sign_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:700;font-size:14px">
      See What They're Seeing →</a>
  </div>
</div>
</body></html>'''
    _send_email(f'👀 {cname} just viewed estimate {enum}', html_body, to_addr)


def send_signature_notification(est):
    """Email the salesperson when a customer signs."""
    notify_cc = os.environ.get('NOTIFY_CC', '').strip()  # optional extra CC

    sp = (est.get('salesperson') or '').strip()
    if not sp:
        print('[notify] No salesperson on estimate — skipping notification')
        return
    to_addr = f'{sp}@projectoneroofing.com'

    # Diagnostic: log what credentials are available so failures are visible in Railway logs
    _api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not _api_key and os.environ.get('SMTP_USER', '').strip() == 'apikey':
        _api_key = os.environ.get('SMTP_PASS', '').strip()
    _from = os.environ.get('SMTP_FROM', '').strip()
    print(f'[notify] Sending signature email to {to_addr} | api_key_set={bool(_api_key)} | from={_from!r}')

    sig      = est.get('signature', {}) or {}
    c        = est.get('customer', {})
    a        = c.get('address', {})
    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'), a.get('state'), a.get('zip')]))
    eid      = est.get('estimate_id', '')
    enum     = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    tier     = est.get('selected_tier', 'better')
    tlbl     = _pick_summary_label(est) or \
        dict(good='Good', better='Better', best='Best').get(tier, tier.title())

    if est.get('estimate_type') == 'insurance':
        tlbl = 'Insurance Claim'
    elif est.get('estimate_type') == 'commercial' and not tlbl:
        # Single-price commercial has no G/B/B pick to name.
        tlbl = 'Commercial Roofing'
    total = _estimate_total(est)

    sname = sig.get('name', 'Unknown')
    semail = sig.get('email', '')
    stime = sig.get('signed_at', '')
    try:
        dt = datetime.fromisoformat(stime.replace('Z', '+00:00'))
        stime_fmt = dt.strftime('%b %d, %Y at %I:%M %p UTC')
    except Exception:
        stime_fmt = stime

    base     = _base_url()
    # Link to the public token-gated signed page so the rep can open it straight
    # from the email without an app session (the est_id route now requires login).
    _tok     = est.get('share_token')
    view_url = f'{base}/sign/{_tok}' if _tok else f'{base}/api/estimates/{eid}/signed'

    subject  = f'✅ {enum} Signed — {c.get("name", "Customer")}'

    email_row = (f'<tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Customer Email</td>'
                 f'<td style="padding:5px 0;font-size:13px">{he(semail)}</td></tr>') if semail else ''

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#16a34a;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">✅ Estimate Signed!</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">{he(c.get("name",""))} just accepted their estimate.</p>
  </div>
  <div style="padding:22px 26px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Estimate #</td><td style="padding:5px 0;font-size:13px;font-weight:700">{he(enum)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Customer</td><td style="padding:5px 0;font-size:13px">{he(c.get("name","—"))}</td></tr>
      {email_row}
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Address</td><td style="padding:5px 0;font-size:13px">{he(addr_str or "—")}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Package</td><td style="padding:5px 0;font-size:13px">{he(tlbl)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Total</td><td style="padding:5px 0;font-size:15px;font-weight:800;color:#16a34a">{fc(total)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Signed By</td><td style="padding:5px 0;font-size:13px">{he(sname)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Signed At</td><td style="padding:5px 0;font-size:13px">{he(stime_fmt)}</td></tr>
    </table>
    <a href="{he(view_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:13px 24px;border-radius:6px;font-weight:700;font-size:14px;margin-bottom:18px">
      \U0001f4c4 View &amp; Download Signed Contract →
    </a>
    <p style="font-size:11px;color:#9ca3af;text-align:center;margin:0">
      Sent to {he(to_addr)} &mdash; you are the assigned salesperson on this estimate.
    </p>
  </div>
</div>
</body></html>'''

    _send_email(subject, html_body, to_addr,
                cc=notify_cc or None,
                bcc=os.environ.get('OWNER_NOTIFY_EMAIL', '').strip() or None)


# ── Signed-contract PDF + CRM push ──────────────────────────────────────────

# ── Shared PDF design tokens ────────────────────────────────────────────────
# Every customer-facing builder draws from this. Before it existed each one
# re-typed set_fill_color(26, 58, 92) inline, which is how five documents ended
# up on a navy that appears nowhere in logo.png. NAVY here is sampled from the
# logo's "ROOFING" wordmark and matches --doc-navy in static/style.css and
# --navy in _CV_CSS; change it in one place and all three surfaces follow.
_PDF_STYLE = {
    'ink':       (12, 24, 48),
    'navy':      (8, 40, 120),
    'navy_deep': (5, 24, 74),
    'mute':      (90, 100, 120),
    'faint':     (139, 147, 164),
    'rule':      (227, 224, 218),
    'paper':     (250, 249, 247),
    'teal':      (0, 168, 184),
    'amber':     (232, 132, 0),
    'white':     (255, 255, 255),
}

_PDF_FONT_DIR = os.path.join(BASE_DIR, 'static', 'fonts')
# (family, style, filename) — the same faces the browser surfaces load.
_PDF_FONT_FILES = [
    ('P1Sans',  '',  'Inter-Regular.ttf'),
    ('P1Sans',  'B', 'Inter-SemiBold.ttf'),
    ('P1Sans',  'I', 'Inter-Italic.ttf'),
    ('P1Serif', '',  'SourceSerif4-Regular.ttf'),
    ('P1Serif', 'B', 'SourceSerif4-SemiBold.ttf'),
]
_PDF_FONTS_PRESENT = all(
    os.path.exists(os.path.join(_PDF_FONT_DIR, f)) for _, _, f in _PDF_FONT_FILES)


def _pdf_fonts(pdf):
    """Register the document faces on `pdf` and return (sans, serif) family names.

    Falls back to Helvetica if the vendored TTFs are missing, so a deploy that
    somehow ships without static/fonts still produces a readable PDF instead of
    raising. The Inter statics carry tabular figures baked into the cmap, which
    is what lines the money columns up — fpdf2 only applies OpenType features
    when uharfbuzz is installed, and it isn't.
    """
    if not _PDF_FONTS_PRESENT:
        return 'Helvetica', 'Helvetica'
    for fam, style, fname in _PDF_FONT_FILES:
        try:
            pdf.add_font(fam, style, os.path.join(_PDF_FONT_DIR, fname))
        except Exception:
            return 'Helvetica', 'Helvetica'
    return 'P1Sans', 'P1Serif'


def _pdf_safe(s):
    """Latin-1 transliteration for the core-font (Helvetica) builders.

    fpdf2 raises FPDFUnicodeEncodingException rather than substituting when a
    core font meets a character it cannot encode, so the internal production
    and permit packets — which still draw in Helvetica — need this. The
    customer-facing documents embed real Unicode faces and use _pdf_rich
    instead, so their en-dashes, curly quotes and bullets survive.
    """
    if s is None:
        return ''
    s = str(s)
    for k, v in {'\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'",
                 '\u201c': '"', '\u201d': '"', '\u2022': '*', '\u00b7': '-',
                 '\u2713': '[x]', '\u00d7': 'x', '\u2026': '...',
                 '\u2192': '->', '\u00a0': ' '}.items():
        s = s.replace(k, v)
    return s.encode('latin-1', 'replace').decode('latin-1')


def _S(pdf):
    """Sans family registered on this document, or the core font.

    build_signed_pdf stamps _sans on the FPDF instance; the shared page
    helpers are also called from builders that never registered a face.
    """
    return getattr(pdf, '_sans', 'Helvetica')


def _pdf_rich(s):
    """Text for a PDF drawn with the embedded Unicode faces.

    Only normalizes the non-breaking space that Word-pasted scope notes drag
    in. Everything else is left alone — transliterating an em-dash into a
    hyphen in a document whose whole job is to look considered is pure loss.
    Falls back to _pdf_safe when the vendored fonts are missing, so a deploy
    without static/fonts still renders instead of raising.
    """
    if s is None:
        return ''
    if not _PDF_FONTS_PRESENT:
        return _pdf_safe(s)
    return str(s).replace('\u00a0', ' ')


def _pdf_oneline(s):
    """_pdf_safe, plus every run of whitespace flattened to one space.

    For pdf.cell() only — never multi_cell(), which wants the newlines.
    Reps type multi-line line-item descriptions on the Pricing tabs, and
    fpdf2's cell() does not wrap: it writes the raw newline straight into the
    PDF text string, where it renders as a junk glyph rather than a break.
    Flattening is the honest single-line rendering; the paths that can wrap
    (customer view, browser print) keep the breaks.
    """
    return ' '.join(_pdf_safe(s).split())


def _pdf_oneline_rich(s):
    """_pdf_oneline for the documents that embed Unicode faces."""
    return ' '.join(_pdf_rich(s).split())


_VISUALIZER_TIERS_ORDER = ('good', 'better', 'best')
_VISUALIZER_TIER_LABELS = {'good': 'Good', 'better': 'Better', 'best': 'Best'}


def _emit_visualizer_pdf_page(pdf, est, LM, W):
    """Draw the Good/Better/Best visualizer renderings on a fresh PDF page.

    Called from build_signed_pdf just before the signature block. Silent
    no-op when no renders exist — the tool is optional. Draws whichever
    tiers actually have a file; missing tiers get an empty slot with the
    label rather than a broken layout, so a partial save (rep only rendered
    Better) still reads clearly.
    """
    vz = est.get('visualizer') or {}
    renders = vz.get('tier_renders') or {}
    files = [(t, renders.get(t)) for t in _VISUALIZER_TIERS_ORDER
             if renders.get(t)]
    if not files:
        return

    # New page — three thumbnails don't fit alongside the last pricing table.
    pdf.add_page()
    pdf.set_font(getattr(pdf, '_serif', _S(pdf)), 'B', 13)
    pdf.set_text_color(*_PDF_STYLE['navy'])
    pdf.cell(0, 7, _pdf_rich('How your home will look'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(_S(pdf), '', 8)
    pdf.multi_cell(W, 4.2, _pdf_rich(
        'These renderings show the selected Good/Better/Best package '
        'options blended onto your home photo. Colors are indicative and '
        'may vary from the manufacturer swatch. Door previews show finish only; '
        'confirm the exact panel, glass and hardware separately.'))
    pdf.ln(3)

    # Three side-by-side landscape thumbnails. Sizing keeps the same layout
    # whether one, two, or three tiers are present, so a partial save reads
    # as "the ones the rep picked" rather than a broken page.
    n = 3
    gap = 4.0
    thumb_w = (W - gap * (n - 1)) / n
    thumb_h = thumb_w * 0.6      # landscape-ish ratio
    y_top = pdf.get_y()

    for i, tier in enumerate(_VISUALIZER_TIERS_ORDER):
        x = LM + i * (thumb_w + gap)
        pdf.set_xy(x, y_top)
        pdf.set_font(_S(pdf), 'B', 9)
        pdf.cell(thumb_w, 5, _VISUALIZER_TIER_LABELS[tier], align='C',
                 new_x='LMARGIN', new_y='NEXT')

        img_y = y_top + 5.5
        ref = renders.get(tier)
        path = os.path.join(UPLOADS_DIR, ref) if ref else None
        drew = False
        if path and os.path.isfile(path):
            try:
                pdf.image(path, x=x, y=img_y, w=thumb_w, h=thumb_h)
                drew = True
            except Exception:
                # A corrupt JPEG shouldn't take the whole PDF down.
                drew = False
        if not drew:
            pdf.set_draw_color(200, 200, 200)
            pdf.set_fill_color(245, 246, 248)
            pdf.rect(x, img_y, thumb_w, thumb_h, style='DF')
            pdf.set_xy(x, img_y + thumb_h / 2 - 2)
            pdf.set_font(_S(pdf), 'I', 8)
            pdf.set_text_color(140, 140, 140)
            pdf.cell(thumb_w, 4, _pdf_rich('(no rendering saved)'),
                     align='C')
            pdf.set_text_color(0, 0, 0)

        # Package label under the thumbnail: pull the selected bundle names
        # and color choice from est.visualizer.selections if they've been
        # saved, so the customer can tie the picture to what they're buying.
        sel = ((vz.get('selections') or {}).get('roofing') or {}).get(tier) or {}
        sel_s = ((vz.get('selections') or {}).get('siding') or {}).get(tier) or {}
        sel_d = ((vz.get('selections') or {}).get('doors') or {}).get(tier) or {}
        caption_parts = []
        if sel.get('color_name'):
            caption_parts.append(f"Roof: {sel['color_name']}")
        if sel_s.get('color_name'):
            style_bit = sel_s.get('style_name') or ''
            side_lbl = 'Siding: ' + sel_s['color_name']
            if style_bit:
                side_lbl += f" ({style_bit})"
            caption_parts.append(side_lbl)
        if sel_d.get('option_name'):
            door_lbl = 'Door: ' + str(sel_d['option_name'])
            if sel_d.get('color_name'):
                door_lbl += f" ({sel_d['color_name']})"
            caption_parts.append(door_lbl)
        pdf.set_xy(x, img_y + thumb_h + 1.5)
        pdf.set_font(_S(pdf), '', 7)
        pdf.multi_cell(thumb_w, 3.2,
                       _pdf_rich('  |  '.join(caption_parts) or ' '))

    # Advance below the row so the signature block doesn't overlap.
    pdf.set_y(y_top + 5.5 + thumb_h + 20)


def _render_estimate_details_page(pdf, est, manifest, LM, W):
    """Adds an 'About This Estimate' page to the signed PDF, sourced from the
    same manifest that drives the /sign page's JSON-LD and details block.

    This is the AI-friendly summary — a homeowner who uploads the signed PDF
    to Claude/ChatGPT for a second opinion will get specifics (manufacturer +
    warranty per package, code items with IRC basis, ventilation calc, tiered
    workmanship, standing process) rather than just line items and boilerplate.
    Kept tight — single column, tight leading, one printed page in typical cases."""
    if not manifest:
        return
    pdf.add_page()

    def _h1(eyebrow, txt):
        pdf.set_font(_S(pdf), '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['teal'])
        pdf.cell(0, 4, _pdf_rich(eyebrow.upper()), new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.set_font(getattr(pdf, '_serif', _S(pdf)), 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(txt), new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(2)

    def _h2(txt):
        pdf.ln(2)
        pdf.set_font(_S(pdf), '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.cell(0, 4.5, _pdf_rich(txt.upper()), new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.set_text_color(*_PDF_STYLE['ink'])

    def _p(txt):
        pdf.set_font(_S(pdf), '', 8.5)
        pdf.multi_cell(W, 4.4, _pdf_rich(txt),
                       new_x='LMARGIN', new_y='NEXT', align='L')

    def _bullets(items, mark='- '):
        pdf.set_font(_S(pdf), '', 8.5)
        for it in items:
            pdf.multi_cell(W, 4.4, _pdf_rich(mark + str(it)),
                       new_x='LMARGIN', new_y='NEXT', align='L')

    _h1('The details', 'What’s Included and Why')
    _p('A concise summary of what is in this bid — materials, code compliance, '
       'ventilation math, and warranties — so you can compare it apples-to-apples '
       'with any other quote.')
    pdf.ln(2)

    # ── Materials & packages ────────────────────────────────────────────
    trades = manifest.get('trades') or []
    if trades:
        _h2('Materials & Packages')
        for tr in trades:
            pdf.set_font(_S(pdf), 'B', 9)
            pdf.cell(0, 5, _pdf_rich(tr.get('label', '')), new_x='LMARGIN', new_y='NEXT', align='L')
            if tr.get('mode') == 'simple':
                feats = tr.get('features') or []
                _bullets(feats[:6])
            else:
                for ti in tr.get('tiers') or []:
                    pdf.set_font(_S(pdf), 'B', 8.5)
                    lbl = ti.get('tier_label', '')
                    if ti.get('is_selected'):
                        lbl += '  (Selected)'
                    pkg = ti.get('package_name') or ''
                    hdr = f'  {lbl} - {pkg}' if pkg else f'  {lbl}'
                    pdf.cell(0, 4.6, _pdf_rich(hdr), new_x='LMARGIN', new_y='NEXT', align='L')
                    tag = ti.get('tagline') or ''
                    if tag:
                        pdf.set_font(_S(pdf), 'I', 8)
                        pdf.multi_cell(W - 6, 4, _pdf_rich('    ' + tag),
                       new_x='LMARGIN', new_y='NEXT', align='L')
                    bullets = ti.get('material_bullets') or []
                    if bullets:
                        pdf.set_font(_S(pdf), '', 8)
                        for b in bullets[:4]:
                            pdf.multi_cell(W - 8, 4, _pdf_rich('    - ' + b),
                       new_x='LMARGIN', new_y='NEXT', align='L')
                    ws = ti.get('workmanship') or ''
                    if ws:
                        pdf.set_font(_S(pdf), '', 8)
                        pdf.multi_cell(W - 8, 4, _pdf_rich('    Workmanship: ' + ws),
                       new_x='LMARGIN', new_y='NEXT', align='L')
                    pdf.ln(0.8)
        pdf.ln(2)

    # ── Permits & code ──────────────────────────────────────────────────
    # Two facts only: who issues the permit for this address, and what that
    # office requires of the install. The adopted-code citation, amendment
    # sources, submittal mechanics and IRC section numbers are all still in the
    # manifest — they belong in the production packet, not in a proposal, where
    # they bury the part a homeowner can actually act on.
    code = manifest.get('code')
    reqs = _code_requirements(code)
    if code and (reqs or code.get('matched')):
        pdf.add_page()
        matched = code.get('matched')
        jname   = code.get('jurisdiction_name') or ''
        _h1('Permits & code', 'Who Pulls Your Permit')
        if matched and jname:
            pdf.set_font(getattr(pdf, '_serif', _S(pdf)), 'B', 15)
            pdf.set_text_color(*_PDF_STYLE['navy'])
            pdf.multi_cell(W, 7, _pdf_rich(jname),
                           new_x='LMARGIN', new_y='NEXT', align='L')
            pdf.set_text_color(*_PDF_STYLE['ink'])
            meta = [x for x in (code.get('office') or '',
                                (code.get('county') + ' County') if code.get('county') else '',
                                code.get('phone') or '') if x]
            if meta:
                pdf.set_font(_S(pdf), '', 8.5)
                pdf.set_text_color(*_PDF_STYLE['mute'])
                pdf.multi_cell(W, 4.4, _pdf_rich('  ·  '.join(meta)),
                               new_x='LMARGIN', new_y='NEXT', align='L')
                pdf.set_text_color(*_PDF_STYLE['ink'])
            pdf.ln(1.5)
            _p(f'{jname} issues the permit for this address and inspects the finished '
               'roof. Everything below is required there — it is priced into your '
               'estimate, not an add-on.')
        else:
            # Never dress the statewide fallback up as the customer's authority.
            _p('Permitting authority not yet confirmed. Colorado has no statewide '
               'residential building code — the city or county adopts and enforces its '
               'own. We confirm the authority for this address before pulling the '
               'permit, and the permit is included in your price either way.')
        if reqs:
            pdf.ln(1)
            _h2(f'Required on your roof in {jname}' if matched and jname
                else 'Required on your roof')
            _bullets(reqs)
        pdf.ln(2)

    # ── Ventilation ─────────────────────────────────────────────────────
    v = manifest.get('ventilation')
    if v:
        _h2('Attic Ventilation Calculation')
        parts = [
            f'Attic area: {int(v["attic_sqft"])} sq ft.',
            f'{v.get("code_basis", "")} requires balanced NFA of '
            f'{v["required_total_sqin"]:.0f} sq in total '
            f'({v["required_intake_sqin"]:.0f} intake / '
            f'{v["required_exhaust_sqin"]:.0f} exhaust).',
        ]
        if v.get('deficit_exhaust_sqin', 0) > 0:
            parts.append(f'This scope cuts in {v["ridge_lf_required"]:.0f} LF of ridge vent '
                         f'and {v["intake_lf_suggested"]} LF of intake venting to reach code.')
        else:
            parts.append('Existing exhaust already meets code — no additional ridge vent required.')
        _p(' '.join(parts))
        pdf.ln(2)

    # ── Workmanship warranty by tier ────────────────────────────────────
    wt = manifest.get('warranty_by_tier') or {}
    if wt:
        _h2('Workmanship Warranty by Package')
        _bullets([
            f'Good Package:   {wt.get("good", "")}',
            f'Better Package: {wt.get("better", "")}',
            f'Best Package:   {wt.get("best", "")}',
        ])
        _p('Manufacturer warranties on the materials themselves are registered in '
           "the homeowner's name once the final payment clears.")
        pdf.ln(2)

    # ── Process ─────────────────────────────────────────────────────────
    proc = manifest.get('process') or []
    if proc:
        _h2('Our Process')
        _bullets(proc)
        pdf.ln(2)

    # ── Company + certifications ───────────────────────────────────────
    comp = manifest.get('company') or {}
    about = comp.get('about') or ''
    certs = comp.get('certifications') or []
    _h2('About Project One Roofing')
    if about:
        _p(about)
    _p(f'{comp.get("name", "")}  |  {comp.get("city", "")}, {comp.get("state", "")}'
       f'  |  {comp.get("phone", "")}  |  {comp.get("website", "")}')
    if certs:
        _bullets(certs)
    revs = manifest.get('reviews') or {}
    if revs.get('count'):
        _p(f'{revs["average"]}/5 average across {revs["count"]} homeowner reviews.')


def build_signed_pdf(est, signed=None):
    """Render the estimate as a PDF (bytes).

    When `signed` is True (default when a signature exists), the title bar
    reads SIGNED CONTRACT and the tail carries the initialed acknowledgements
    and E-SIGN certificate. When `signed` is False (customer's Download-PDF
    button on the /sign page, or a rep previewing before send-out), the title
    reads ESTIMATE and the signature tail is skipped — everything else is
    identical, including the About-This-Estimate page and the T&C. The
    default is to auto-detect from est['signature'] so all existing call
    sites (email attachment, CRM upload) keep the signed behavior."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c    = est.get('customer', {})
    a    = c.get('address', {})
    sig  = est.get('signature', {}) or {}
    if signed is None:
        signed = bool(sig)
    enum = _est_number(est)
    is_ins = est.get('estimate_type') == 'insurance'
    tier = est.get('selected_tier', 'better')
    manifest = _build_estimate_manifest(est)

    LM = 16
    RM = 16
    _LOGO = os.path.join(BASE_DIR, 'static', 'logo.png')
    _W    = 215.9 - LM - RM

    class _PDF(FPDF):
        # `header()` did not exist before, so the letterhead appeared on page 1
        # only and every page after it started blank at the top margin. On a
        # document a customer prints and hands to their spouse, page 4 with no
        # company name on it is the tell.
        def header(self):
            if getattr(self, '_no_chrome', False):
                return
            y = 11
            if os.path.exists(_LOGO):
                try:
                    self.image(_LOGO, x=LM, y=y, h=6.5)
                except Exception:
                    pass
            self.set_xy(LM, y)
            self.set_font(self._sans, '', 7)
            self.set_text_color(*_PDF_STYLE['faint'])
            self.cell(_W, 4, _pdf_rich(self._eyebrow), align='R',
                      new_x='LMARGIN', new_y='NEXT')
            self.set_draw_color(*_PDF_STYLE['rule'])
            self.set_line_width(0.2)
            self.line(LM, y + 10.5, self.w - RM, y + 10.5)
            self.set_text_color(*_PDF_STYLE['ink'])
            self.set_y(y + 15)

        def footer(self):
            if getattr(self, '_no_chrome', False):
                return
            self.set_y(-12)
            self.set_draw_color(*_PDF_STYLE['rule'])
            self.set_line_width(0.2)
            self.line(LM, self.get_y(), self.w - RM, self.get_y())
            self.ln(2.5)
            self.set_font(self._sans, '', 6.5)
            self.set_text_color(*_PDF_STYLE['faint'])
            self.cell(0, 4, _pdf_rich('Project One Roofing  ·  970-776-0945  ·  '
                                      'projectoneroofingcolorado.com'), align='L')
            self.cell(0, 4, f'Page {self.page_no()} of {{nb}}',
                      align='R', new_x='LMARGIN', new_y='NEXT')
            self.set_text_color(*_PDF_STYLE['ink'])

    pdf = _PDF(orientation='P', unit='mm', format='Letter')
    SANS, SERIF = _pdf_fonts(pdf)
    pdf._sans = SANS
    pdf._serif = SERIF
    pdf._eyebrow = ''
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    # Top margin leaves room for the running letterhead the header() draws.
    pdf.set_margins(LM, 26, RM)
    # PDF document metadata — visible in Acrobat's Document Properties and
    # read by tools/AI parsers that inspect PDF metadata separately from the
    # rendered page content. Latin-1 safe: fpdf2's metadata strings are.
    _kind = ('Contract' if signed else 'Estimate')
    _pdf_meta_title = _pdf_rich(
        (f'Roof Replacement {_kind}' if not is_ins
         else f'Insurance-Claim Roofing {_kind}')
        + (f' - {c.get("name")}' if c.get('name') else '')
        + ' - Project One Roofing')
    pdf.set_title(_pdf_meta_title)
    pdf.set_author('Project One Roofing')
    pdf.set_subject(_pdf_rich(manifest.get('summary', '')) if manifest else '')
    pdf.set_keywords('roof replacement, Project One Roofing, Colorado licensed insured, '
                     'CertainTeed, IKO, Class 4 impact hail resistant, permit included, '
                     'workmanship warranty, code compliant, IRC R806 ventilation')
    pdf.set_creator('Project One Roofing Estimator')
    W = pdf.w - LM - RM

    if signed:
        title = 'Signed Contract  ·  Insurance Claim' if is_ins else 'Signed Contract'
    else:
        title = 'Insurance Claim Estimate' if is_ins else 'Your Project Estimate'
    pdf._eyebrow = f'{title}  ·  {enum}'

    # ── Cover ───────────────────────────────────────────────────────────────
    # The document used to open straight into a masthead and a filled navy bar,
    # which reads like an invoice. A cover costs one page and is the first
    # thing the customer sees.
    pdf._no_chrome = True
    pdf.add_page()
    pdf._no_chrome = False

    if os.path.exists(_LOGO):
        try:
            pdf.image(_LOGO, x=LM, y=26, h=13)
        except Exception:
            pass
    pdf.set_xy(LM, 46)
    pdf.set_font(SANS, '', 7.5)
    pdf.set_text_color(*_PDF_STYLE['teal'])
    pdf.cell(W, 4, _pdf_rich(title.upper()), new_x='LMARGIN', new_y='NEXT')

    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.set_line_width(0.2)
    pdf.line(LM, 56, LM + W, 56)

    pdf.set_xy(LM, 92)
    pdf.set_font(SERIF, 'B', 27)
    pdf.set_text_color(*_PDF_STYLE['navy'])
    pdf.cell(W, 12, _pdf_rich(c.get('name') or '—'), new_x='LMARGIN', new_y='NEXT')

    _cover_addr = ', '.join(filter(None, [a.get('street'),
                                          ', '.join(filter(None, [a.get('city'), a.get('state')]))]))
    if _cover_addr:
        pdf.set_x(LM)
        pdf.set_font(SANS, '', 11)
        pdf.set_text_color(*_PDF_STYLE['mute'])
        pdf.cell(W, 6, _pdf_rich(_cover_addr), new_x='LMARGIN', new_y='NEXT')

    # Meta strip — small caps label over value, evenly spaced.
    _meta = [('Estimate', enum), ('Date', est.get('estimate_date', '')),
             ('Valid Until', est.get('valid_until', ''))]
    _sp_cover = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()
    if _sp_cover:
        _meta.append(('Sales Rep', _sp_cover))
    _y = 128
    _cw = W / len(_meta)
    for i, (lbl, val) in enumerate(_meta):
        pdf.set_xy(LM + i * _cw, _y)
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.cell(_cw, 3.5, _pdf_rich(lbl.upper()), new_x='LMARGIN', new_y='NEXT')
        pdf.set_xy(LM + i * _cw, _y + 4.5)
        pdf.set_font(SANS, '', 10.5)
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.cell(_cw, 5, _pdf_rich(val or '—'), new_x='LMARGIN', new_y='NEXT')

    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.line(LM, 249, LM + W, 249)
    pdf.set_xy(LM, 253)
    pdf.set_font(SANS, '', 8.5)
    pdf.set_text_color(*_PDF_STYLE['faint'])
    pdf.cell(W, 4, _pdf_rich('115 E 5th St · Loveland, CO 80537 · 970-776-0945 · '
                             'projectoneroofingcolorado.com'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(*_PDF_STYLE['ink'])

    # ── Estimate detail page ────────────────────────────────────────────────
    pdf.add_page()

    # Info block — two columns
    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                       a.get('state'), a.get('zip')]))
    sp = (est.get('salesperson') or '').replace('.', ' ').title()
    signed_at = sig.get('signed_at', '')
    try:
        dt = datetime.fromisoformat(signed_at.replace('Z', '+00:00'))
        signed_fmt = dt.strftime('%B %d, %Y at %I:%M %p UTC')
    except Exception:
        signed_fmt = signed_at

    left_rows = [
        ('Customer', c.get('name', '')),
        ('Address', addr_str),
        ('Phone', c.get('phone', '')),
        ('Email', c.get('email', '')),
    ]
    right_rows = [
        ('Estimate #', enum),
        ('Date', est.get('estimate_date', '')),
        ('Salesperson', sp),
    ]
    job_num = c.get('crm_job_number') or ''
    if job_num:
        right_rows.append(('Job #', job_num))
    if is_ins:
        ins_td = est.get('trades', {}).get('insurance', {})
        if ins_td.get('carrier'):
            right_rows.append(('Carrier', ins_td['carrier']))
        if ins_td.get('claim_number'):
            right_rows.append(('Claim #', ins_td['claim_number']))
    else:
        right_rows.append(('Package', _pick_summary_label(est)
                          or dict(good='Good', better='Better',
                                  best='Best').get(tier, tier.title())))
    shingle_color = (sig.get('shingle_color') or '').strip()
    if shingle_color:
        right_rows.append(('Shingle Color', shingle_color))
    siding_color = (sig.get('siding_color') or '').strip()
    if siding_color:
        right_rows.append(('Siding Color', siding_color))

    col_w = W / 2
    y_start = pdf.get_y()

    def _info_col(rows, x_off):
        pdf.set_xy(LM + x_off, y_start)
        for label, val in rows:
            if not val:
                continue
            pdf.set_x(LM + x_off)
            pdf.set_font(SANS, '', 6.5)
            pdf.set_text_color(*_PDF_STYLE['faint'])
            pdf.cell(col_w, 3.5, _pdf_rich(label.upper()),
                     new_x='LMARGIN', new_y='NEXT')
            pdf.set_x(LM + x_off)
            pdf.set_font(SANS, '', 9.5)
            pdf.set_text_color(*_PDF_STYLE['ink'])
            pdf.cell(col_w, 5, _pdf_rich(val),
                     new_x='LMARGIN', new_y='NEXT')
            pdf.ln(2)

    _info_col(left_rows, 0)
    y_after_left = pdf.get_y()
    _info_col(right_rows, col_w)
    pdf.set_y(max(y_after_left, pdf.get_y()) + 5)

    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.set_line_width(0.2)
    pdf.line(LM, pdf.get_y(), LM + W, pdf.get_y())
    pdf.ln(8)

    # ── Table helpers ───────────────────────────────────────────────────────
    # Hand-placed cells are gone. pdf.table() wraps long descriptions instead
    # of clipping them at a character count, and repeats the column headings on
    # every page a table spills onto — before this, a table that crossed a page
    # boundary continued with no headings and (with no header()) no letterhead
    # either. It also drops the '  ' string-padding hack, which is what made the
    # old PDF extract as ragged text when a customer pasted it into a chat.
    from fpdf.fonts import FontFace
    from fpdf.enums import TableCellFillMode

    HEAD_FACE = FontFace(family=SANS, size_pt=6.5,
                         color=_PDF_STYLE['faint'], fill_color=None)

    def section_head(eyebrow, title):
        # Keep the heading with its table. Without this a trade heading could
        # land at the bottom of a page with its first row on the next one,
        # which is the classic orphan that makes a document look generated.
        if pdf.get_y() > pdf.h - 52:
            pdf.add_page()
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['teal'])
        pdf.cell(0, 4, _pdf_rich(eyebrow.upper()), new_x='LMARGIN', new_y='NEXT')
        pdf.set_font(SERIF, 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(title), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(1)

    def subtotal_row(label, amount, amt_w):
        pdf.set_draw_color(*_PDF_STYLE['navy'])
        pdf.set_line_width(0.3)
        pdf.line(LM, pdf.get_y(), LM + W, pdf.get_y())
        pdf.set_font(SANS, 'B', 9)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(W - amt_w, 8, _pdf_rich(label), align='R')
        pdf.cell(amt_w, 8, fc(amount), align='R')
        pdf.ln(11)
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)

    def open_table(widths, aligns):
        pdf.set_font(SANS, '', 8)
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)
        return pdf.table(
            col_widths=widths, text_align=aligns, width=W,
            borders_layout='HORIZONTAL_LINES',
            headings_style=HEAD_FACE,
            cell_fill_mode=TableCellFillMode.NONE,
            line_height=5, padding=(2.4, 2, 2.4, 0), v_align='T')

    grand = 0.0
    row_h = 6.5
    if is_ins:
        ins_td   = est.get('trades', {}).get('insurance', {})
        sections = ins_td.get('sections') or (
            [{'name': '', 'items': ins_td.get('line_items', [])}]
            if ins_td.get('line_items') else [])
        desc_w = W - 22 - 26 - 24
        widths = (desc_w * 0.38, desc_w * 0.62, 22, 26, 24)
        aligns = ('LEFT', 'LEFT', 'RIGHT', 'RIGHT', 'RIGHT')
        for si, sec in enumerate(sections):
            items = sec.get('items', [])
            if not items:
                continue
            section_head('Scope', sec.get('name') or 'Insurance Estimate Items')
            sub = 0.0
            with open_table(widths, aligns) as table:
                head = table.row()
                for h in ('Item', 'Description', 'ACV', 'Depreciation', 'RCV'):
                    head.cell(h)
                for it in items:
                    acv = float(it.get('acv') or 0)
                    dep = float(it.get('depreciation') or 0)
                    tot = acv + dep
                    sub += tot
                    row = table.row()
                    row.cell(_pdf_rich(it.get('name', '')))
                    row.cell(_pdf_oneline_rich(it.get('description', '')))
                    row.cell(fc(acv))
                    row.cell(fc(dep))
                    row.cell(fc(tot))
            grand += sub
            subtotal_row((sec.get('name') or 'Section') + ' Subtotal', sub, 24)
        total_label = 'Insurance Claim Total'
    else:
        pricing = est.get('pricing', {})
        mode    = pricing.get('mode', 'margin')
        labels  = dict(roofing='Roofing', siding='Siding', windows='Windows',
                       gutters='Gutters', other='Other / Misc')
        widths = (W - 14 - 14 - 28 - 28, 14, 14, 28, 28)
        aligns = ('LEFT', 'RIGHT', 'CENTER', 'RIGHT', 'RIGHT')
        for tk in GBB_TRADES:
            td = est.get('trades', {}).get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            trade_mode = _trade_mode(tk, td)
            t_tier = _trade_tier(est, tk)
            r = _tier_rate(pricing, tk, t_tier)
            if not any(
                    float(it.get('quantity') or 0) > 0 and
                    (trade_mode == 'simple'
                     or (it.get('tiers') or {}).get(t_tier, {}).get('included') is not False)
                    for it in td['line_items']):
                continue
            _pkg = dict(good='Good', better='Better', best='Best').get(t_tier, '')
            section_head(f'{_pkg} package' if _pkg and trade_mode != 'simple' else 'Scope',
                         labels.get(tk, tk.title()))
            sub = 0.0
            hidden = 0
            with open_table(widths, aligns) as table:
                head = table.row()
                for h in ('Description', 'Qty', 'Unit', 'Unit Price', 'Total'):
                    head.cell(h)
                for it in td['line_items']:
                    qty = float(it.get('quantity') or 0)
                    if qty <= 0:
                        continue
                    if trade_mode == 'simple':
                        sp_  = float(it.get('unit_price') or 0)
                        line = sp_ * qty
                        desc = (it.get('description') or '').strip()
                    else:
                        t    = (it.get('tiers') or {}).get(t_tier, {})
                        if t.get('included') is False:
                            continue
                        line = _line_sell_total(it, t_tier, r, mode)
                        sp_  = line / qty
                        desc = t.get('description', '')
                    sub += line
                    if not it.get('customer_visible', True):
                        hidden += 1
                        continue
                    name = _with_section(it, it.get('name', ''))
                    # The description wraps under the name now instead of being
                    # clipped at 78 characters mid-word.
                    if desc:
                        name = f'{name} — {_pdf_oneline_rich(desc)}'
                    row = table.row()
                    row.cell(_pdf_rich(name))
                    row.cell(f'{qty:g}')
                    row.cell(_pdf_rich(it.get('unit', '')))
                    row.cell(fc(sp_))
                    row.cell(fc(line))
            if hidden:
                pdf.set_font(SANS, 'I', 7)
                pdf.set_text_color(*_PDF_STYLE['faint'])
                pdf.cell(W, 5.5, _pdf_rich('Additional materials & supplies included in total'),
                         align='L', new_x='LMARGIN', new_y='NEXT')
                pdf.set_text_color(*_PDF_STYLE['ink'])
            grand += sub
            subtotal_row(labels.get(tk, tk.title()) + ' Subtotal', sub, 28)
        _sum = _pick_summary_label(est)
        total_label = (f'Total — {_sum}' if _sum
                       else 'Total — ' + dict(good='Good', better='Better',
                                              best='Best').get(tier, tier.title()) + ' Package')

    # Grand total — the one filled element in the document, which is why
    # everything above it stopped being filled.
    if pdf.get_y() > pdf.h - 46:
        pdf.add_page()
    pdf.ln(2)
    _gy = pdf.get_y()
    pdf.set_fill_color(*_PDF_STYLE['navy'])
    pdf.rect(LM, _gy, W, 18, style='F')
    pdf.set_xy(LM + 7, _gy + 4)
    pdf.set_font(SANS, '', 7)
    pdf.set_text_color(210, 218, 236)
    pdf.cell(W - 14, 4, _pdf_rich(total_label.upper()), new_x='LMARGIN', new_y='NEXT')
    pdf.set_xy(LM + 7, _gy + 8)
    pdf.set_font(SERIF, 'B', 17)
    pdf.set_text_color(*_PDF_STYLE['white'])
    pdf.cell(W - 14, 8, fc(grand), align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_y(_gy + 18)
    pdf.ln(9)
    pdf.set_text_color(*_PDF_STYLE['ink'])

    # Scope of work / notes
    if is_ins:
        scope = (est.get('trades', {}).get('insurance', {}).get('scope_notes') or '').strip()
        if scope:
            section_head('Scope', 'Scope of Work')
            pdf.set_font(SANS, '', 9)
            pdf.multi_cell(W, 5, _pdf_rich(scope), align='L')
            pdf.ln(6)
    notes = (est.get('notes_customer') or '').strip()
    if notes:
        section_head('Additional', 'Notes')
        pdf.set_font(SANS, '', 9)
        pdf.multi_cell(W, 5, _pdf_rich(notes), align='L')
        pdf.ln(6)

    # About This Estimate — the AI-friendly summary, inserted before the T&C
    # so a reader (or an AI reading a customer's uploaded PDF) hits the
    # differentiators (materials + warranties, code compliance, ventilation
    # math, workmanship, process) before the boilerplate legal text.
    _render_estimate_details_page(pdf, est, manifest, LM, W)

    # Terms & conditions
    ctext = (est.get('contract_text') or '').strip()
    if ctext:
        pdf.add_page()
        section_head('Legal', 'Terms & Conditions')
        pdf.ln(2)
        pdf.set_font(SANS, '', 7.5)
        pdf.set_text_color(*_PDF_STYLE['mute'])
        pdf.multi_cell(W, 4.3, _pdf_rich(ctext), align='L')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(6)

    # Initialed acknowledgements — only when this PDF represents an actual
    # signed contract. The unsigned preview shows the T&C without pretending
    # the customer has initialed anything.
    if signed:
        inits = [i for i in (sig.get('initials') or []) if (i.get('value') or '').strip()]
        if inits:
            if pdf.get_y() > pdf.h - 40:
                pdf.add_page()
            section_head('Acknowledged', 'Initialed by the Homeowner')
            for it in inits:
                _ry = pdf.get_y()
                pdf.set_font(SANS, 'B', 9)
                pdf.set_text_color(*_PDF_STYLE['navy'])
                pdf.cell(16, 5.5, _pdf_rich(it['value'].upper()))
                pdf.set_font(SANS, '', 8.5)
                pdf.set_text_color(*_PDF_STYLE['ink'])
                pdf.multi_cell(W - 16, 5.5, _pdf_rich(it['text']),
                               new_x='LMARGIN', new_y='NEXT', align='L')
                pdf.ln(1.5)
                pdf.set_draw_color(*_PDF_STYLE['rule'])
                pdf.set_line_width(0.2)
                pdf.line(LM, pdf.get_y(), LM + W, pdf.get_y())
                pdf.ln(2.5)
            pdf.ln(3)

    # ── Visualizer page ────────────────────────────────────────────────
    # When the rep has saved Good/Better/Best photo renders, show the
    # customer what they've selected before the signature. Skipped silently
    # when there are no renders — nothing to show is not an error.
    _emit_visualizer_pdf_page(pdf, est, LM, W)

    if not signed:
        # Unsigned preview PDF — a "Sign online" call-to-action instead of the
        # E-SIGN certificate. Nothing else changes.
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        y0 = pdf.get_y()
        pdf.set_draw_color(*_PDF_STYLE['teal'])
        pdf.set_line_width(0.6)
        pdf.line(LM, y0, LM, y0 + 19)
        pdf.set_xy(LM + 6, y0)
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['teal'])
        pdf.cell(0, 4, _pdf_rich('UNSIGNED PREVIEW'), new_x='LMARGIN', new_y='NEXT')
        pdf.set_xy(LM + 6, y0 + 5)
        pdf.set_font(SANS, '', 8.5)
        pdf.set_text_color(*_PDF_STYLE['mute'])
        pdf.multi_cell(W - 12, 4.8, _pdf_rich(
            'To accept this proposal, return to the online link and sign electronically. '
            'This preview is for review only and does not constitute a contract.'))
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)
        return bytes(pdf.output())

    # Signature block
    if pdf.get_y() > pdf.h - 70:
        pdf.add_page()
    y0 = pdf.get_y()
    pdf.set_draw_color(22, 163, 74)
    pdf.set_line_width(0.6)
    pdf.line(LM, y0, LM, y0 + 48)
    pdf.set_line_width(0.2)
    pdf.set_xy(LM + 6, y0)
    pdf.set_text_color(22, 101, 52)
    pdf.set_font(SANS, '', 6.5)
    pdf.cell(0, 4.5, _pdf_rich('ELECTRONICALLY SIGNED'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(LM + 6)
    pdf.set_text_color(*_PDF_STYLE['ink'])
    pdf.set_font(SERIF, 'B', 19)
    pdf.cell(0, 11, _pdf_rich(sig.get('name', '')), new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(LM + 6)
    pdf.set_font(SANS, '', 7.5)
    sig_lines = [
        f"Signed by: {sig.get('name', '')}"
        + (f"  ({sig.get('email')})" if sig.get('email') else ''),
        f"Date: {signed_fmt}",
        f"IP Address: {sig.get('ip_address', '')}",
        f"Document SHA-256: {sig.get('document_hash', '')[:32]}...",
    ]
    for line in sig_lines:
        pdf.cell(0, 4.4, _pdf_rich(line), new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(LM + 6)
    pdf.set_draw_color(*_PDF_STYLE['rule'])

    return bytes(pdf.output())


# Mirrors MEASURE_FIELDS in app.js (Scope page) — keep the two in sync.
MEASURE_LABELS = [
    ('Roof', [('roof_squares', 'Roof Area', 'SQ'), ('waste_pct', 'Waste', '%'),
              ('attic_sqft', 'Attic Area', 'SF'),
              ('low_slope_squares', 'Low Slope Area (2/12 or less) - rolled roofing', 'SQ'),
              ('steep_squares', 'Steep Area (7/12 and up)', 'SQ'),
              ('predominant_pitch', 'Predominant Pitch', '/12'),
              ('ridge_hip_lf', 'Ridge + Hip', 'LF'),
              # Ridges alone — ridge vent ORDERS the full ridge off this, so the
              # crew needs it on the packet. It was enterable in the UI but
              # missing here, so it never printed.
              ('ridge_lf', 'Ridges', 'LF'),
              ('valley_lf', 'Valley', 'LF'),
              ('eave_lf', 'Eaves', 'LF'), ('rake_lf', 'Rakes', 'LF'),
              ('step_flash_lf', 'Step Flashing', 'LF'), ('pipe_boots', 'Pipe Boots', 'EA'),
              ('turtle_vents', 'Turtle Vents', 'EA'), ('broan_4in', '4" Broan Vent', 'EA'),
              ('broan_8in', '8" Broan Vent', 'EA')]),
    ('Gutters', [('gutter_lf', 'Gutter', 'LF'), ('downspout_lf', 'Downspouts', 'LF')]),
    ('Siding', [('siding_squares', 'Siding Area', 'SQ'), ('siding_waste_pct', 'Waste', '%'),
                ('siding_openings_count', 'Window + Door Openings', 'EA'),
                ('siding_outside_corners_lf', 'Outside Corners', 'LF'),
                ('siding_inside_corners_lf', 'Inside Corners', 'LF'),
                # J-channel vs trim: EDCO bundles run J-channel, LP + Hardie
                # run 5/4 trim board — separate SKUs, separate footages.
                ('siding_j_channel_lf', 'J-Channel', 'LF'),
                # Trim split — sloped (rake/gable) runs long-cut and eats more
                # waste (/0.84) than vertical (/0.85) on the material order.
                ('siding_trim_sloped_lf', 'Trim — Sloped (rakes/gables)', 'LF'),
                ('siding_trim_vertical_lf', 'Trim — Vertical', 'LF'),
                ('siding_trim_width_default', 'Default Trim Width', 'in'),
                ('siding_starter_lf', 'Starter Strip', 'LF'),
                # Soffit prices by the foot; the width is the spec the crew
                # orders from. ≤24" = LF sticks, ≥25" routes to panel SKUs.
                ('siding_soffit_lf', 'Soffit', 'LF'),
                ('siding_soffit_width', 'Soffit Width', 'in'),
                ('siding_soffit_vented_pct', 'Soffit % Vented', '%'),
                # Fascia split into eaves vs rakes — supplier take-off uses
                # the same split. The old single field is a fallback.
                ('siding_fascia_eaves_lf', 'Fascia — Eaves', 'LF'),
                ('siding_fascia_rakes_lf', 'Fascia — Rakes', 'LF'),
                # Frieze board — the LP/Hardie band immediately under the
                # eaves and above every window/door.
                ('siding_frieze_eaves_lf', 'Frieze Board — Eaves (Sloped)', 'LF'),
                ('siding_frieze_level_lf', 'Frieze Board — Level', 'LF')]),
    ('Windows', [('windows_count', 'Windows', 'EA'), ('doors_count', 'Doors', 'EA')]),
    ('Commercial', [('comm_squares', 'Roof Area', 'SQ'), ('comm_waste_pct', 'Waste', '%'),
                    ('comm_perimeter_lf', 'Perimeter / Edge', 'LF'),
                    ('comm_parapet_lf', 'Parapet / Coping', 'LF'),
                    ('comm_penetrations', 'Penetrations', 'EA'),
                    ('comm_drains', 'Drains / Scuppers', 'EA'),
                    ('comm_curbs', 'HVAC Curbs', 'EA'),
                    ('comm_skylights', 'Skylights / Hatches', 'EA'),
                    ('comm_pitch_pans', 'Pitch Pans', 'EA'),
                    ('comm_walkway_pads', 'Walkway Pads', 'EA'),
                    ('comm_sections', 'Roof Levels / Sections', 'EA'),
                    # Fastening inputs. Rendered by the Scope panel rather than
                    # the plain number grid (panelOnly in MEASURE_FIELDS), but
                    # they are ordinary measurements and print on the packet.
                    ('comm_length_ft', 'Building Length', 'LF'),
                    ('comm_width_ft', 'Building Width', 'LF'),
                    ('comm_height_ft', 'Building Height', 'LF'),
                    ('comm_uplift', 'Uplift Rating', 'psf'),
                    ('comm_insul_layers', 'Fastened Layers', 'EA'),
                    ('comm_zone_field_sf', 'Zone: Field', 'SF'),
                    ('comm_zone_perim_sf', 'Zone: Perimeter', 'SF'),
                    ('comm_zone_corner_sf', 'Zone: Corner', 'SF')]),
]


# Attic ventilation calculator — MUST mirror atticVentilation() in app.js.
# 1/300 balanced rule: 1 sq ft of net free area (NFA) per 300 sq ft of attic,
# split 50% exhaust / 50% intake. Provided for parity / future PDF use; pricing
# does not depend on it (line-item quantities are stored by the frontend).
NFA_TURTLE_SQIN    = 50    # net free area per turtle/box vent
NFA_RIDGE_SQIN_LF  = 18    # net free area per LF of ridge vent
NFA_INTAKE_SQIN_LF = 9     # net free area per LF of soffit/intake vent
VENT_RULE_DIVISOR  = 300   # 1/300 balanced rule


def _mnum(v, dflt=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return dflt


def attic_ventilation(m):
    m = m or {}
    attic = _mnum(m.get('attic_sqft')) or _mnum(m.get('roof_squares')) * 100
    required_total   = (attic / VENT_RULE_DIVISOR) * 144 if attic > 0 else 0  # sq in
    required_exhaust = required_total / 2
    required_intake  = required_total / 2
    provided_exhaust = _mnum(m.get('turtle_vents')) * NFA_TURTLE_SQIN
    deficit_exhaust  = max(required_exhaust - provided_exhaust, 0)
    needs_ridge  = deficit_exhaust > 0
    needs_intake = needs_ridge
    ridge_lf_required   = deficit_exhaust / NFA_RIDGE_SQIN_LF if needs_ridge else 0
    ridge_sticks        = math.ceil(ridge_lf_required / 4)
    intake_lf_suggested = math.ceil(required_intake / NFA_INTAKE_SQIN_LF) if needs_intake else 0
    return {
        'attic_sqft': attic, 'required_total': required_total,
        'required_exhaust': required_exhaust, 'required_intake': required_intake,
        'provided_exhaust': provided_exhaust, 'deficit_exhaust': deficit_exhaust,
        'needs_ridge': needs_ridge, 'needs_intake': needs_intake,
        'ridge_lf_required': ridge_lf_required, 'ridge_sticks': ridge_sticks,
        'intake_lf_suggested': intake_lf_suggested,
    }


# ── Commercial fastener calculator ──────────────────────────────────────────
# MUST mirror _asceZoneWidth / commercialFastening in app.js. Unlike
# attic_ventilation, this pair IS parity-tested — tests/test_fastening.py runs
# both over the same fixtures and fails on any disagreement.
#
# The table is passed in rather than loaded here so the function stays pure and
# both implementations can be driven from identical fixture JSON.
_FASTEN_ZONES = ('field', 'perimeter', 'corner')


def _asce_zone_width(least, h, rule):
    """ASCE 7 zone width `a`: 10% of the least horizontal dimension or 40% of the
    mean roof height, whichever is SMALLER, floored at 4% of the least dimension
    and an absolute minimum (3 ft in ASCE, 4 ft in some FM approvals), and capped
    at half the least dimension so the zones cannot overlap."""
    rule = rule or {}
    if least <= 0 or h <= 0:
        return 0.0
    a = min(_mnum(rule.get('a_pct_least'), 0.10) * least,
            _mnum(rule.get('a_pct_height'), 0.40) * h)
    a = max(a, _mnum(rule.get('a_min_pct_least'), 0.04) * least,
            _mnum(rule.get('a_min_ft'), 3))
    return min(a, least / 2.0)


def commercial_fastening(m, table):
    """Fastener counts by roof zone and layer.

    Returns counts of 0 with ok=False whenever it cannot know the answer —
    never a plausible guess. `raw` is the un-rounded float (what parity
    compares); `total` is the waste-adjusted whole count the line item uses."""
    m = m or {}
    t = table or {}
    rule     = t.get('zone_rule') or {}
    board_sf = _mnum(t.get('board_sf'), 32) or 32
    waste    = _mnum(t.get('waste_pct'), 0)
    ratings  = t.get('ratings') or {}

    warnings = []
    zero_layer = {'applies': False,
                  'by_zone': {z: {'count': 0} for z in _FASTEN_ZONES},
                  'raw': 0.0, 'total': 0}

    def _bail(reason):
        return {'ok': False, 'reason': reason, 'a': 0.0,
                'rating': None, 'rating_requested': _mnum(m.get('comm_uplift')),
                'rating_label': '', 'rating_note': '',
                'zones': {z: {'sf': 0.0, 'source': 'none'} for z in _FASTEN_ZONES},
                'zone_source': 'none',
                'area_check': {'bbox_sf': 0.0, 'measured_sf': 0.0, 'delta_pct': 0.0, 'warn': False},
                'layers': 0, 'board_sf': board_sf, 'waste_pct': waste,
                'insul': dict(zero_layer), 'seam': dict(zero_layer),
                'warnings': warnings}

    if not ratings:
        return _bail('no_table')

    # ── uplift rating: exact match, else the smallest published row at or above
    # what was asked for. NEVER round down — that under-fastens. Keys are JSON
    # strings, so sort numerically ("105" sorts below "60" as text).
    requested = _mnum(m.get('comm_uplift'))
    if requested <= 0:
        return _bail('no_uplift_rating')
    keys = sorted(int(k) for k in ratings if str(k).lstrip('-').isdigit())
    if not keys:
        return _bail('no_table')
    chosen = next((k for k in keys if k >= requested), None)
    rating_note = ''
    if chosen is None:
        chosen = keys[-1]
        rating_note = (f'No published row at or above {requested:g} psf - using the highest '
                       f'available ({chosen:g} psf). Verify against the system approval.')
        warnings.append(rating_note)
    row = ratings.get(str(chosen)) or {}

    # ── zone areas: manual override wins, else computed from the bounding box
    L = _mnum(m.get('comm_length_ft'))
    W = _mnum(m.get('comm_width_ft'))
    H = _mnum(m.get('comm_height_ft'))
    ov = {'field': _mnum(m.get('comm_zone_field_sf')),
          'perimeter': _mnum(m.get('comm_zone_perim_sf')),
          'corner': _mnum(m.get('comm_zone_corner_sf'))}
    has_ov = any(v > 0 for v in ov.values())

    a = 0.0
    comp = {z: 0.0 for z in _FASTEN_ZONES}
    bbox = L * W
    if L > 0 and W > 0 and H > 0:
        a = _asce_zone_width(min(L, W), H, rule)
        band = bbox - max(L - 2 * a, 0) * max(W - 2 * a, 0)
        corner = (12 if (rule.get('corner_shape') or 'L') == 'L' else 4) * a * a
        corner = min(corner, band)
        comp = {'field': max(bbox - band, 0.0),
                'perimeter': max(band - corner, 0.0),
                'corner': corner}
    elif not has_ov:
        return _bail('missing_dimensions')

    zones, sources = {}, []
    for z in _FASTEN_ZONES:
        if ov[z] > 0:
            zones[z] = {'sf': ov[z], 'source': 'override'}
            sources.append('override')
        else:
            zones[z] = {'sf': comp[z], 'source': 'computed'}
            sources.append('computed')
    zone_source = sources[0] if len(set(sources)) == 1 else 'mixed'
    total_zone_sf = sum(zones[z]['sf'] for z in _FASTEN_ZONES)
    if total_zone_sf <= 0:
        return _bail('missing_dimensions')

    # ── reconciliation: the bounding box is an independent second area from the
    # measured roof. On an L-shaped building it is bigger AND the real roof has
    # more corners, so we surface the gap rather than scaling anything.
    measured_sf = _mnum(m.get('comm_squares')) * 100
    delta_pct = 0.0
    area_warn = False
    ref = bbox if bbox > 0 else total_zone_sf
    if measured_sf > 0 and ref > 0:
        delta_pct = abs(ref - measured_sf) / ref * 100
        if delta_pct > 10:
            area_warn = True
            warnings.append(
                f'Bounding box ({ref:,.0f} SF) differs from the measured roof area '
                f'({measured_sf:,.0f} SF) by {delta_pct:.0f}% - this roof is not a '
                f'rectangle. Enter zone areas manually.')
    if has_ov and ref > 0 and abs(total_zone_sf - ref) / ref * 100 > 2:
        warnings.append(
            f'Zone areas sum to {total_zone_sf:,.0f} SF but the roof is {ref:,.0f} SF - '
            f'check the override values.')
    if _mnum(m.get('comm_sections')) >= 2:
        warnings.append('Multiple roof levels - zones are computed for ONE rectangle. '
                        'Enter zone areas manually for a stepped or multi-level roof.')

    # ── layers. A cleared field stores an explicit 0, which legitimately means
    # "recover, no new insulation" — so MISSING means 1, but 0 means 0.
    raw_layers = m.get('comm_insul_layers')
    layers = 1.0 if raw_layers in (None, '') else _mnum(raw_layers)

    # Attachment: resolved client-side from the bundle's products and stored as
    # measurements so this function stays pure and the packet gets the same
    # answer without loading the price book. Absent = assume it applies for
    # insulation, and FAIL CLOSED for seam (an adhered system has no seam
    # fasteners; guessing yes would put thousands of phantom screws on a bid).
    insul_applies = m.get('comm_insul_attach') in (None, '') or _mnum(m.get('comm_insul_attach')) > 0
    seam_applies  = _mnum(m.get('comm_seam_attach')) > 0

    per_board = row.get('insul_per_board') or {}
    seam_spec = row.get('seam') or {}

    insul = {'applies': bool(insul_applies and layers > 0), 'by_zone': {}, 'raw': 0.0, 'total': 0}
    seam  = {'applies': bool(seam_applies), 'by_zone': {}, 'raw': 0.0, 'total': 0}

    for z in _FASTEN_ZONES:
        sf = zones[z]['sf']
        pb = _mnum(per_board.get(z))
        boards = sf / board_sf if board_sf > 0 else 0.0
        cnt = boards * pb * layers if insul['applies'] else 0.0
        insul['by_zone'][z] = {'boards': boards, 'per_board': pb, 'count': cnt}
        insul['raw'] += cnt

        spec = seam_spec.get(z) or {}
        sw = _mnum(spec.get('sheet_width_ft'))
        sp = _mnum(spec.get('spacing_in'))
        # Tributary area per fastener. A run of length L at spacing s truly has
        # L/s + 1 fasteners; at roof scale the +1 is swamped by waste_pct.
        per_fast = sw * (sp / 12.0) if sw > 0 and sp > 0 else 0.0
        scnt = (sf / per_fast) if (seam['applies'] and per_fast > 0) else 0.0
        seam['by_zone'][z] = {'sf_per_fastener': per_fast, 'spacing_in': sp,
                              'sheet_width_ft': sw, 'count': scnt}
        seam['raw'] += scnt

    for layer in (insul, seam):
        layer['total'] = math.ceil(layer['raw'] * (1 + waste / 100.0) - 1e-9)

    return {
        'ok': True, 'reason': '', 'a': a,
        'rating': chosen, 'rating_requested': requested,
        'rating_label': row.get('label') or f'{chosen:g} psf', 'rating_note': rating_note,
        'zones': zones, 'zone_source': zone_source,
        'area_check': {'bbox_sf': bbox, 'measured_sf': measured_sf,
                       'delta_pct': delta_pct, 'warn': area_warn},
        'layers': layers, 'board_sf': board_sf, 'waste_pct': waste,
        'insul': insul, 'seam': seam,
        'warnings': warnings,
    }


# ── Ordering pack sizes ─────────────────────────────────────────────────────
# The estimate measures in squares and linear feet; a supplier order is placed
# in bundles, sticks and rolls. The catalog carries a `unit` (SQ / LF / EA) but
# nothing about packaging, so this table is the bridge.
#
# THESE ARE INDUSTRY DEFAULTS, NOT VERIFIED PER SUPPLIER. Pack sizes vary by
# manufacturer and by what the branch stocks — CertainTeed Shadow Ridge runs
# 24 LF/bundle where IKO Hip & Ridge runs ~33, and starter varies more than
# that. Every converted row on the material order prints the arithmetic it
# used ("33.6 SQ x 3/SQ = 101 bundles") so a wrong factor is visible on the
# sheet rather than silently mis-ordering. Correct a number here and the sheet
# follows.
#
# Matching is on a lowercase substring of the line-item name, first hit wins,
# so put the specific keys above the general ones.
_ORDER_PACK = [
    # (name fragment, from-unit, per, order-unit, note)
    # Ridge VENT before ridge CAP: a line named "ridge vent" must not be
    # caught by the shingle rule and ordered in bundles.
    ('ridge vent',             'LF', 4.0,   'sticks',  '4 ft sticks'),
    ('intake vent',            'LF', 4.0,   'sticks',  '4 ft sticks'),
    ('ridge cap',              'LF', 25.0,  'bundles', 'ridge + hip'),
    ('ridge shingle',          'LF', 25.0,  'bundles', 'ridge + hip'),
    ('hip / ridge',            'LF', 25.0,  'bundles', 'ridge + hip'),
    ('starter strip',          'LF', 105.0, 'bundles', '105 LF / bundle'),
    ('starter',                'LF', 105.0, 'bundles', '105 LF / bundle'),
    # Drip edge and gutter apron come in 10 ft sticks but are lapped, so the
    # usable run is 9 ft — order against that, not the nominal length.
    ('drip edge',              'LF', 9.0,   'sticks',  '9 ft usable per stick'),
    ('gutter apron',           'LF', 9.0,   'sticks',  '9 ft usable per stick'),
    ('downspout',              'LF', 10.0,  'sticks',  '10 ft sticks'),
    ('ice & water',            'SQ', 2.0,   'rolls',   '36 in x 66.7 ft'),
    ('ice and water',          'SQ', 2.0,   'rolls',   '36 in x 66.7 ft'),
    ('synthetic underlayment', 'SQ', 10.0,  'rolls',   '10 SQ rolls'),
    ('underlayment',           'SQ', 10.0,  'rolls',   '10 SQ rolls'),
    # Asphalt shingles: 3 bundles to the square on every architectural and
    # impact-resistant line in the catalog. Metal, steel and rubber are sold by
    # the square or the panel and are deliberately absent — they fall through
    # to "order as measured" rather than being converted into a bundle count
    # that does not exist.
    ('landmark',               'SQ', 1 / 3.0, 'bundles', '3 bundles / SQ'),
    ('northgate',              'SQ', 1 / 3.0, 'bundles', '3 bundles / SQ'),
    ('nordic',                 'SQ', 1 / 3.0, 'bundles', '3 bundles / SQ'),
    ('shingles',               'SQ', 1 / 3.0, 'bundles', '3 bundles / SQ'),
]

# Named so the material order can print the list it actually used.
_ORDER_PACK_NOTE = ('Pack sizes are industry defaults, not supplier-verified. '
                    'Each converted row shows its arithmetic - check it against '
                    'your branch before ordering.')


def _order_pack_for(name, unit):
    """Pack rule for a line item, or None to order as measured."""
    n = ' '.join(str(name or '').lower().split())
    u = str(unit or '').strip().upper()
    for frag, from_unit, per, order_unit, note in _ORDER_PACK:
        if frag in n and from_unit == u:
            return {'per': per, 'order_unit': order_unit, 'note': note,
                    'from_unit': from_unit}
    return None


def material_order_rows(est):
    """What to actually buy, per line item, across every enabled trade.

    Returns a list of dicts:
      trade, name, qty, unit           - as the estimate measures it
      order_qty, order_unit            - what you place the order in
      math                             - the arithmetic, for checking

    Rows with no pack rule come back with order_qty None and are ordered as
    measured. Labor lines and anything with no quantity are dropped: this is a
    purchase order, not a scope list.
    """
    import math as _math
    trades = est.get('trades') or {}
    labels = _PRODUCT_TRADE_LABELS if '_PRODUCT_TRADE_LABELS' in globals() else {}
    out = []
    for tk in GBB_TRADES:
        td = trades.get(tk) or {}
        if not td.get('enabled') or not td.get('line_items'):
            continue
        tmode = _trade_mode(tk, td)
        tier = _trade_tier(est, tk)
        for it in (td.get('line_items') or []):
            try:
                qty = float(it.get('quantity') or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue
            if tmode != 'simple':
                t = (it.get('tiers') or {}).get(tier) or {}
                if t.get('included') is False:
                    continue
            name = (it.get('name') or '').strip()
            # Labor, fees and REMOVAL lines are not ordered. "Remove existing
            # gutters & downspouts" is 168 LF of tear-off, not 17 sticks of
            # downspout to buy — the word match alone would have ordered it.
            _n = name.lower()
            if any(w in _n for w in
                   ('labor', 'permit', 'inspection', 'cleanup', 'site protection',
                    'dumpster', 'tear off', 'tear-off', 'deck inspection',
                    'remove', 'removal', 'detach', 'dispose', 'disposal', 'haul')):
                continue
            unit = (it.get('unit') or '').strip().upper()
            rule = _order_pack_for(name, unit)
            row = {'trade': labels.get(tk, tk.title()), 'name': name,
                   'qty': qty, 'unit': unit,
                   'order_qty': None, 'order_unit': '', 'math': ''}
            if rule:
                per = rule['per']
                if rule['order_unit'] == 'bundles' and per < 1:
                    # squares -> bundles: 3 per square
                    n_units = _math.ceil(qty / per - 1e-9)
                    row['math'] = f'{qty:g} {unit} x {round(1 / per)}/{unit}'
                else:
                    n_units = _math.ceil(qty / per - 1e-9)
                    row['math'] = f'{qty:g} {unit} / {per:g} per {rule["order_unit"][:-1]}'
                row['order_qty'] = n_units
                row['order_unit'] = rule['order_unit']
                if rule['note']:
                    row['math'] += f'  ({rule["note"]})'
            out.append(row)
    return out


def siding_material_takeoff(est, tier):
    """Supplier-order piece counts for one signed siding tier.

    Mirrors the QXO LP/Hardie take-off sheet Luke's supplier uses: convert the
    Hover measurements into piece counts per SKU, at two waste factors — base
    (what the sheet calls 'PC/Each') and +15% for siding / +19% for trim
    (the sheet's PC/Each+15% column, i.e. /0.85 and /0.84 respectively).

    Returns a list of (section, product, size, pieces_base, pieces_plus_waste).
    Empty list when the tier is not on an LP/Hardie bundle or the sidding
    trade is not enabled — nothing to order.

    Does NOT change any sell price — this is packet output only.
    """
    trades = est.get('trades') or {}
    td = trades.get('siding') or {}
    if not td.get('enabled'):
        return []
    bundle_id = ((td.get('tier_bundles') or {}).get(tier) or '').strip()
    profile = _siding_profile(td, tier)
    if not bundle_id or not profile:
        return []
    cfg = SIDING_BUNDLE_PROFILES.get(bundle_id)
    if not cfg:
        return []
    mfg = cfg['mfg']
    pf = (SIDING_PROFILE_FACTORS.get(mfg) or {}).get(profile) or {}
    primary_pf = pf.get('primary') or {}
    if not primary_pf.get('pcs_per_sq'):
        return []

    m = est.get('measurements') or {}

    def num(key, default=0):
        try:
            v = m.get(key)
            if v is None or v == '':
                return float(default)
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    def waste_siding(pcs):
        # +15% column on the sheet = /0.85 (i.e., pcs / 0.85 ≈ pcs × 1.176).
        return math.ceil(pcs / 0.85 - 1e-9) if pcs > 0 else 0

    def waste_trim(pcs):
        # +19% column = /0.84.
        return math.ceil(pcs / 0.84 - 1e-9) if pcs > 0 else 0

    def waste_none(pcs):
        return math.ceil(pcs - 1e-9) if pcs > 0 else 0

    # Total wall SQ, with the estimate's own waste already baked in — same shape
    # as the primary siding line reads (measure: siding_sq_waste).
    total_sq = num('siding_squares') * (1 + num('siding_waste_pct', 10) / 100.0)
    # Prefer the split fascia / trim fields; fall back to the pre-split
    # legacy field so an in-flight estimate can still be re-quoted.
    fascia_eaves = num('siding_fascia_eaves_lf')
    fascia_rakes = num('siding_fascia_rakes_lf')
    if not (fascia_eaves + fascia_rakes):
        fascia_eaves = num('siding_fascia_lf')      # legacy — dump into eaves
    trim_sloped = num('siding_trim_sloped_lf')
    trim_vertical = num('siding_trim_vertical_lf')
    if not (trim_sloped + trim_vertical):
        trim_vertical = num('siding_trim_lf')       # legacy — dump into vertical
    trim_w = num('siding_trim_width_default', 4) or 4
    trim_w_lbl = f'5/4×{int(trim_w) if trim_w == int(trim_w) else trim_w}'
    j_channel = num('siding_j_channel_lf')
    corners_out = num('siding_outside_corners_lf')
    corners_in = num('siding_inside_corners_lf')
    starter = num('siding_starter_lf')
    soffit = num('siding_soffit_lf')
    soffit_w = num('siding_soffit_width', 12) or 12
    vented_pct = max(0.0, min(100.0, num('siding_soffit_vented_pct', 100)))
    frieze_eaves = num('siding_frieze_eaves_lf')
    frieze_level = num('siding_frieze_level_lf')
    openings = num('siding_openings_count')

    # Accessories default to 16' sticks on LP and 12' on Hardie (from the sheet).
    stick_ft = 16 if mfg == 'lp' else 12
    prod_prefix = 'LP Primed' if mfg == 'lp' and bundle_id == 'b_lp_standard' else \
                  'LP Expert Finish' if mfg == 'lp' and bundle_id == 'b_lp_expert' else \
                  'James Hardie Primed' if mfg == 'hardie' and bundle_id == 'b_hardie_primed' else \
                  'James Hardie Statement' if mfg == 'hardie' and bundle_id == 'b_hardie_statement' else \
                  cfg.get('mfg', '').title()

    rows = []

    # ── Primary siding ─────────────────────────────────────────────────────
    # pcs_per_sq is already per SQUARE (100 SF), so the multiplier is simply
    # total_sq × pcs_per_sq. Sheet's E5 = B5/100 * 11.11 = SQ * 11.11.
    primary_size = primary_pf.get('size', '')
    prim_pcs = total_sq * primary_pf['pcs_per_sq']
    rows.append(('Siding', f'{prod_prefix} — {SIDING_PROFILE_LABELS.get(profile, profile)}',
                 primary_size, prim_pcs, waste_siding(prim_pcs)))
    src_note = primary_pf.get('source_note')
    if src_note:
        rows.append(('Siding', f'    ⚠ {src_note}', '', 0, 0))

    battens_pf = pf.get('battens')
    if battens_pf and battens_pf.get('pcs_per_panel'):
        # Battens: 3 per panel (or whatever the sheet says), on the primary
        # panel count. Same waste class as siding (/0.85).
        bat_pcs = prim_pcs * battens_pf['pcs_per_panel']
        rows.append(('Siding', f'{prod_prefix} — Battens',
                     battens_pf.get('size', ''), bat_pcs, waste_siding(bat_pcs)))

    # ── Openings trim (window + door) ──────────────────────────────────────
    # Sheet convention: the rep enters TOTAL window+door trim LF on the
    # openings row; we roll that into the vertical/sloped split unless the
    # rep entered an openings-specific number elsewhere. For v1, treat
    # window+door trim as part of the vertical trim number the rep already
    # entered — the packet doesn't try to split it apart.

    # ── Trim: sloped / vertical ────────────────────────────────────────────
    if trim_vertical > 0:
        pcs = trim_vertical / stick_ft
        rows.append(('Trim', f'{prod_prefix} Trim — Vertical', trim_w_lbl, pcs, waste_trim(pcs)))
    if trim_sloped > 0:
        pcs = trim_sloped / stick_ft
        rows.append(('Trim', f'{prod_prefix} Trim — Sloped', trim_w_lbl, pcs, waste_trim(pcs)))

    # ── Corners ────────────────────────────────────────────────────────────
    # Outside corners = LF × 2 / stick (per sheet). Inside = LF / stick.
    if corners_out > 0:
        pcs = corners_out * 2.0 / stick_ft
        rows.append(('Corners', f'{prod_prefix} Corner Trim — Outside', f'{trim_w_lbl}×{stick_ft}\'',
                     pcs, waste_trim(pcs)))
    if corners_in > 0:
        pcs = corners_in / stick_ft
        rows.append(('Corners', f'{prod_prefix} Corner Trim — Inside', f'{trim_w_lbl}×{stick_ft}\'',
                     pcs, waste_trim(pcs)))
    if j_channel > 0:
        pcs = j_channel / stick_ft
        rows.append(('Corners', 'J-Channel', f'5/8"×{stick_ft}\'', pcs, waste_trim(pcs)))

    # ── Starter strip ──────────────────────────────────────────────────────
    if starter > 0:
        pcs = starter / stick_ft
        rows.append(('Starter', f'{prod_prefix} Starter Strip', f'{stick_ft}\' stick', pcs, waste_trim(pcs)))

    # ── Fascia (eaves + rakes) + frieze ────────────────────────────────────
    if fascia_eaves > 0:
        pcs = fascia_eaves / stick_ft
        rows.append(('Roofline', f'{prod_prefix} Fascia — Eaves', f'4/4×8"×{stick_ft}\'',
                     pcs, waste_trim(pcs)))
    if fascia_rakes > 0:
        pcs = fascia_rakes / stick_ft
        rows.append(('Roofline', f'{prod_prefix} Fascia — Rakes', f'4/4×8"×{stick_ft}\'',
                     pcs, waste_trim(pcs)))
    if frieze_eaves > 0:
        pcs = frieze_eaves / stick_ft
        rows.append(('Roofline', f'{prod_prefix} Frieze — Eaves (Sloped)', f'{trim_w_lbl}×{stick_ft}\'',
                     pcs, waste_trim(pcs)))
    if frieze_level > 0:
        pcs = frieze_level / stick_ft
        rows.append(('Roofline', f'{prod_prefix} Frieze — Level', f'{trim_w_lbl}×{stick_ft}\'',
                     pcs, waste_trim(pcs)))

    # ── Soffit ─────────────────────────────────────────────────────────────
    if soffit > 0:
        vented_lf = soffit * vented_pct / 100.0
        solid_lf = soffit - vented_lf
        if soffit_w >= 25:
            # Wide overhang: order 4×8/9/10 panels (SF / 100 × factor).
            panel_factor, panel_lbl = (3.125, '4×8') if mfg == 'lp' else (2.5, '4×10')
            sf = soffit * soffit_w / 12.0
            pcs = sf / 100.0 * panel_factor
            rows.append(('Soffit', f'{prod_prefix} Soffit Panel', f'{panel_lbl} NG',
                         pcs, waste_trim(pcs)))
        else:
            if vented_lf > 0:
                pcs = vented_lf / stick_ft
                rows.append(('Soffit', f'{prod_prefix} Vented Soffit', f'{int(soffit_w)}"×{stick_ft}\'',
                             pcs, waste_trim(pcs)))
            if solid_lf > 0:
                pcs = solid_lf / stick_ft
                rows.append(('Soffit', f'{prod_prefix} Solid Soffit', f'{int(soffit_w)}"×{stick_ft}\'',
                             pcs, waste_trim(pcs)))

    # ── Accessories (rolls, tubes, coils, packs) ───────────────────────────
    if total_sq > 0:
        wrap_name = 'HardieWrap' if mfg == 'hardie' else 'TriBuilt House Wrap'
        wrap_rolls = total_sq / 13.5
        rows.append(('Accessories', wrap_name, '9\' × 150\' Roll',
                     wrap_rolls, waste_none(wrap_rolls)))
        rows.append(('Accessories', 'Housewrap Tape', 'Roll',
                     wrap_rolls, waste_none(wrap_rolls)))
        tubes = total_sq * 1.5
        rows.append(('Accessories', 'Elastomeric Sealant / Caulk', 'Tube',
                     tubes, waste_none(tubes)))
        coils = total_sq / 20.0
        rows.append(('Accessories', 'Siding Coil Nails', '50# Coil',
                     coils, waste_none(coils)))
        trim_nail_divisor = 10.0 if bundle_id == 'b_lp_expert' else 15.0
        trim_nails = total_sq / trim_nail_divisor
        rows.append(('Accessories', 'Trim Nails', 'Box',
                     trim_nails, waste_none(trim_nails)))
        touchup_qt = total_sq / 20.0
        rows.append(('Accessories', 'Field Paint / Touch-Up', 'Quart',
                     touchup_qt, waste_none(touchup_qt)))

    if openings > 0:
        # Z-flash for window flashing — one 10' stick per ~4 openings.
        zflash = openings / 4.0
        rows.append(('Accessories', 'Z-Flash for Window Flashing', '1"×2"×10\'',
                     zflash, waste_none(zflash)))

    # Bear Skins pack — Hardie-only fastener pack of 55.
    if mfg == 'hardie' and prim_pcs > 0:
        packs = prim_pcs / 55.0
        rows.append(('Accessories', 'Bear Skins Fastener Pack', '55 pc / pack',
                     packs, waste_none(packs)))

    return rows


def _new_internal_pdf(eyebrow):
    """An internal document (work order, material order, permit sheet) with the
    same chrome as the customer PDF.

    These are the sheets that go to a crew on a roof and to a supplier's
    counter, and they were the last documents still drawing in core Helvetica
    on the old #1a3a5c navy with no page numbers at all — a three-page packet
    that gets separated in a truck has no way to be put back in order. Returns
    (pdf, SANS, SERIF, W).
    """
    LM = RM = 14
    _LOGO = os.path.join(BASE_DIR, 'static', 'logo.png')
    _W = 215.9 - LM - RM

    class _IntPDF(FPDF):
        def header(self):
            if getattr(self, '_no_chrome', False):
                return
            y = 11
            if os.path.exists(_LOGO):
                try:
                    self.image(_LOGO, x=LM, y=y, h=6.5)
                except Exception:
                    pass
            self.set_xy(LM, y)
            self.set_font(self._sans, '', 7)
            self.set_text_color(*_PDF_STYLE['faint'])
            self.cell(_W, 4, _pdf_rich(self._eyebrow), align='R',
                      new_x='LMARGIN', new_y='NEXT')
            self.set_draw_color(*_PDF_STYLE['rule'])
            self.set_line_width(0.2)
            self.line(LM, y + 10.5, self.w - RM, y + 10.5)
            self.set_text_color(*_PDF_STYLE['ink'])
            self.set_y(y + 15)

        def footer(self):
            if getattr(self, '_no_chrome', False):
                return
            self.set_y(-12)
            self.set_draw_color(*_PDF_STYLE['rule'])
            self.set_line_width(0.2)
            self.line(LM, self.get_y(), self.w - RM, self.get_y())
            self.ln(2.5)
            self.set_font(self._sans, '', 6.5)
            self.set_text_color(*_PDF_STYLE['faint'])
            self.cell(0, 4, _pdf_rich('Project One Roofing  ·  Internal document'), align='L')
            self.cell(0, 4, f'Page {self.page_no()} of {{nb}}',
                      align='R', new_x='LMARGIN', new_y='NEXT')
            self.set_text_color(*_PDF_STYLE['ink'])

    pdf = _IntPDF(orientation='P', unit='mm', format='Letter')
    SANS, SERIF = _pdf_fonts(pdf)
    pdf._sans, pdf._serif, pdf._eyebrow = SANS, SERIF, eyebrow
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(LM, 26, RM)
    pdf.add_page()
    return pdf, SANS, SERIF, _W


def _int_styles(pdf, SANS, SERIF, W):
    """Section heading / key-value / table helpers shared by the internal docs."""
    def section(eyebrow, title):
        if pdf.get_y() > pdf.h - 46:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['teal'])
        pdf.cell(0, 4, _pdf_rich(eyebrow.upper()), new_x='LMARGIN', new_y='NEXT')
        pdf.set_font(SERIF, 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(title), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(1)

    def kv(rows, label_w=44):
        """Key/value rows on hairlines. multi_cell for the value so a long
        address wraps instead of running off the page."""
        for label, val in rows:
            if val in (None, ''):
                continue
            y0 = pdf.get_y()
            pdf.set_font(SANS, '', 6.5)
            pdf.set_text_color(*_PDF_STYLE['faint'])
            pdf.cell(label_w, 5.6, _pdf_rich(str(label).upper()))
            pdf.set_font(SANS, '', 9.5)
            pdf.set_text_color(*_PDF_STYLE['ink'])
            pdf.multi_cell(W - label_w, 5.6, _pdf_rich(str(val)),
                           new_x='LMARGIN', new_y='NEXT', align='L')
            pdf.set_draw_color(*_PDF_STYLE['rule'])
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
            pdf.ln(1.6)

    return section, kv


def _tier_items(td, trade_mode, t_tier):
    """Line items in scope for one trade at one package tier.

    Module level rather than nested because the work order and the material
    order are separate documents now and both walk the same rows.
    """
    for it in td.get('line_items', []):
        qty = float(it.get('quantity') or 0)
        if qty <= 0:
            continue
        if trade_mode != 'simple':
            t = (it.get('tiers') or {}).get(t_tier, {})
            if t.get('included') is False:
                continue
            yield it, qty, t
        else:
            yield it, qty, {}


def build_work_order_pdf(est):
    """Work order for the SIGNED package — the sheet the crew works from.

    Job card, product selection, a one-line scope summary per trade and the
    notes. Deliberately contains NO pricing anywhere: it leaves the office.
    What to buy lives in build_material_order_pdf, which goes to whoever
    places the supplier order rather than to the roof. v1 reflects the signed
    contract only (change orders excluded)."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c      = est.get('customer', {})
    a      = c.get('address', {})
    sig    = est.get('signature', {}) or {}
    enum   = _est_number(est)
    is_ins = est.get('estimate_type') == 'insurance'
    tier   = sig.get('selected_tier') or est.get('selected_tier', 'better')
    trades = est.get('trades', {})
    labels = dict(roofing='Roofing', siding='Siding', windows='Windows',
                  gutters='Gutters', other='Other / Misc')

    pdf, SANS, SERIF, W = _new_internal_pdf(f'Work order  ·  {enum}')
    section, kv = _int_styles(pdf, SANS, SERIF, W)

    def title_bar(txt):
        pdf.set_font(SERIF, 'B', 20)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(W, 10, _pdf_rich(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
        pdf.ln(5)

    def section_title(txt, need=42):
        # `need` is the vertical room this section wants in mm. The default
        # carries the heading plus roughly five lines of body, which is what
        # stops a heading sitting alone at the foot of a page with its text on
        # the next one. Tables ask for more so they don't strand a stub.
        if pdf.get_y() > pdf.h - need:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font(SERIF, 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(1)

    def table_header(cols):
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.set_draw_color(*_PDF_STYLE['navy'])
        pdf.set_line_width(0.3)
        for txt, w, align in cols:
            pdf.cell(w, 7, _pdf_rich(str(txt).upper()), border='B', align=align)
        pdf.ln()
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)

    def trunc(s, n):
        # _pdf_oneline, not _pdf_safe: these are single-line pdf.cell() values.
        s = _pdf_oneline_rich(s)
        return s if len(s) <= n else s[:n - 1] + '...'

    # ── Page 1: Work Order ──
    title_bar('Work Order')

    signed_at = sig.get('signed_at', '')
    try:
        signed_fmt = datetime.fromisoformat(signed_at.replace('Z', '+00:00')).strftime('%B %d, %Y')
    except Exception:
        signed_fmt = signed_at
    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                       a.get('state'), a.get('zip')]))
    info_rows = [
        ('Customer',    c.get('name', '')),
        ('Phone',       c.get('phone', '')),
        ('Job Address', addr_str),
        ('Job #',       c.get('crm_job_number') or ''),
        ('Estimate #',  enum),
        ('Salesperson', (est.get('salesperson') or '').replace('.', ' ').title()),
        ('Signed',      signed_fmt),
    ]
    if not is_ins:
        info_rows.append(('Package', _pick_summary_label(est)
                          or dict(good='Good', better='Better',
                                  best='Best').get(tier, tier.title())))
    shingle_color = (sig.get('shingle_color') or '').strip()
    if shingle_color:
        info_rows.append(('Shingle Color', shingle_color))
    siding_color = (sig.get('siding_color') or '').strip()
    if siding_color:
        info_rows.append(('Siding Color', siding_color))
    for label, val in info_rows:
        if not val:
            continue
        pdf.set_font(SANS, 'B', 9)
        pdf.cell(40, 5.5, _pdf_rich(label))
        pdf.set_font(SANS, '', 9)
        pdf.cell(0, 5.5, _pdf_rich(val), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    # ── Job Details block ─────────────────────────────────────────────
    # The scheduling + install-plan facts the crew actually needs at the top
    # of the sheet: total squares installed, steep area + pitch, layers to
    # tear off, scheduled date, dish disposition, ridge vent y/n. Some come
    # from the estimate (measurements + roofing line items), some from the
    # work_order fields the rep fills in after signing.
    wo0 = est.get('work_order') or {}
    m0 = est.get('measurements') or {}

    def _mnum0(key):
        try:
            return float(m0.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    roof_sq_total   = _mnum0('roof_squares')
    low_slope_sq    = _mnum0('low_slope_squares')
    waste_pct       = _mnum0('waste_pct')
    steep_sq        = _mnum0('steep_squares')
    pitch_num       = _mnum0('predominant_pitch')
    # Match squares_waste in app.js/app.py: (roof_sq - low_slope) × (1 + waste)
    installed_sq    = max(roof_sq_total - low_slope_sq, 0.0) * (1 + waste_pct / 100.0)
    roofing_td      = trades.get('roofing') or {}
    _vent_roles     = {it.get('vent_role') for it in (roofing_td.get('line_items') or [])}
    has_ridge_vent  = 'ridge' in _vent_roles
    has_intake_vent = 'intake' in _vent_roles
    vent_cutin0     = est.get('vent_cutin') or {}

    # Blank-line fallback so an unfilled sheet still gives the crew somewhere
    # to write it down on the roof.
    def _wo(key, blank):
        v = wo0.get(key)
        v = str(v).strip() if v is not None else ''
        return v or blank

    detail_rows = []
    if roof_sq_total > 0:
        detail_rows.append(('Squares to Install',
                            f'{installed_sq:.1f} SQ (roof {roof_sq_total:g} SQ + {waste_pct:g}% waste'
                            + (f', minus {low_slope_sq:g} SQ low-slope' if low_slope_sq else '')
                            + ')'))
    detail_rows.append(('Scheduled Date',
                        _wo('scheduled_date', '____________ (TBD)')))
    detail_rows.append(('Tear-off Layers',
                        (str(wo0.get('tear_off_layers')) + ' layer(s)')
                        if wo0.get('tear_off_layers') is not None
                        else '____ (fill in)'))
    # Steep and height are the two adders that change crew size and day rate,
    # so they ride at the top rather than being inferred from the line items.
    if steep_sq > 0 or pitch_num > 0:
        pitch_txt = f' at {int(pitch_num)}/12 pitch' if pitch_num > 0 else ''
        steep_txt = f'{steep_sq:g} SQ steep{pitch_txt}' if steep_sq > 0 else \
                    f'Predominant pitch {int(pitch_num)}/12'
        detail_rows.append(('Steep Charge', steep_txt))
    else:
        detail_rows.append(('Steep Charge', 'None - walkable'))
    detail_rows.append(('Height / Access',
                        _wo('height_access', '____________ (1-story / 2-story / high)')))
    detail_rows.append(('Hand Load',
                        _wo('hand_load', '____________ (yes / no)')))
    detail_rows.append(('Ridge Vent',
                        'YES - see the Ventilation Layout page' if has_ridge_vent else 'NO'))
    detail_rows.append(('Intake Vent', 'YES' if has_intake_vent else 'NO'))
    detail_rows.append(('Satellite Dish',
                        _wo('satellite_dish', '____________ (confirm w/ HO)')))

    section_title('Job Details')
    pdf.set_font(SANS, '', 9)
    for label, val in detail_rows:
        pdf.set_font(SANS, 'B', 9)
        pdf.cell(40, 5.5, _pdf_rich(label))
        pdf.set_font(SANS, '', 9)
        pdf.multi_cell(W - 40, 5.5, _pdf_rich(val), new_x='LMARGIN', new_y='NEXT', align='L')
    pdf.ln(2)

    # No Product Selection or Scope of Work here. The colours the crew needs
    # are already in the header block above (shingle and siding, off the
    # signature), and the item list is the material order's document — this
    # sheet is the job card.

    # One Notes section with two labelled blocks rather than two headings. Two
    # separate section titles cost ~20mm of the sheet between them, which was
    # enough to push a two-line crew note onto a page of its own.
    notes = (est.get('notes_customer') or '').strip()
    crew  = (est.get('notes_internal') or '').strip()
    if notes or crew:
        section_title('Notes')
        for lbl, body in (('From the estimate', notes), ('Crew only - internal', crew)):
            if not body:
                continue
            pdf.set_font(SANS, '', 6.5)
            pdf.set_text_color(*_PDF_STYLE['faint'])
            pdf.cell(0, 4.4, _pdf_rich(lbl.upper()), new_x='LMARGIN', new_y='NEXT')
            pdf.set_font(SANS, '', 8.5)
            pdf.set_text_color(*_PDF_STYLE['ink'])
            pdf.multi_cell(W, 4.6, _pdf_rich(body), new_x='LMARGIN', new_y='NEXT', align='L')
            pdf.ln(2)

    # Ridge-vent cut-in is now printed inline under Job Details on page 1,
    # right next to the "Ridge Vent: YES" row — the crew doesn't have to
    # flip pages to find the picture.

    # ── Ventilation layout, on its own sheet ──────────────────────────────
    # The crew marks the as-installed run here, so it needs the full width. It
    # used to be a 150mm thumbnail tucked under the "Ridge Vent: YES" row on
    # the job card. Prints whether or not a map was marked: without one it is
    # still the sheet the ridge and intake footage gets written on.
    try:
        ridge_lf = float(m0.get('ridge_lf') or 0)
    except (TypeError, ValueError):
        ridge_lf = 0.0
    try:
        eave_lf = float(m0.get('eave_lf') or 0)
    except (TypeError, ValueError):
        eave_lf = 0.0
    img_fn = vent_cutin0.get('image_filename')
    img_path = os.path.join(UPLOADS_DIR, *str(img_fn).split('/')) if img_fn else ''

    if has_ridge_vent or has_intake_vent or img_fn:
        pdf.add_page()
        title_bar('Ventilation Layout')

        vinfo = attic_ventilation(m0)
        raw_cut = math.ceil(vinfo['ridge_lf_required'])
        cutin = min(raw_cut, int(ridge_lf)) if ridge_lf > 0 else raw_cut

        vrows = []
        if has_ridge_vent:
            full_sticks = math.ceil(ridge_lf / 4) if ridge_lf > 0 else 0
            vrows.append(('Ridge vent',
                          (f'Run the FULL ridge - {ridge_lf:g} LF, {full_sticks} stick(s)'
                           if ridge_lf > 0 else 'Run the full ridge')))
            vrows.append(('Cut in for code',
                          (f'~{cutin:g} LF of the ridge cut open'
                           + (' (the whole ridge)' if ridge_lf and cutin >= ridge_lf
                              else ' - see the map for locations'))))
        else:
            vrows.append(('Ridge vent', 'NOT on this job'))
        if has_intake_vent:
            intake_sticks = math.ceil(eave_lf / 4) if eave_lf > 0 else 0
            vrows.append(('Intake vent',
                          (f'{eave_lf:g} LF at the eaves, {intake_sticks} stick(s)'
                           if eave_lf > 0 else 'At the eaves')))
        else:
            vrows.append(('Intake vent', 'NOT on this job'))
        kv(vrows, label_w=42)

        note = (vent_cutin0.get('notes') or '').strip()
        if note:
            pdf.ln(1)
            pdf.set_font(SANS, '', 8.5)
            pdf.multi_cell(W, 4.6, _pdf_rich(note),
                           new_x='LMARGIN', new_y='NEXT', align='L')

        pdf.ln(3)
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.cell(0, 5, _pdf_rich('AS INSTALLED - FILL IN ON SITE'),
                 new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_font(SANS, '', 9.5)
        y0 = pdf.get_y() + 5
        for k, lbl in enumerate(('Ridge vent installed', 'Intake vent installed')):
            pdf.set_xy(pdf.l_margin + k * 92, y0)
            pdf.cell(88, 6, _pdf_rich(lbl + '   ____________ LF'))
        pdf.set_y(y0 + 12)

        if img_path and os.path.exists(img_path):
            try:
                pdf.set_font(SANS, '', 6.5)
                pdf.set_text_color(*_PDF_STYLE['faint'])
                pdf.cell(0, 5, _pdf_rich(
                    'MARKED CUT-IN MAP - HIGHLIGHTED RUNS ARE CUT OPEN FOR VENTILATION'),
                         new_x='LMARGIN', new_y='NEXT')
                pdf.set_text_color(*_PDF_STYLE['ink'])
                pdf.image(img_path, w=W)
            except Exception:
                pass
        else:
            pdf.set_font(SANS, '', 8)
            pdf.set_text_color(*_PDF_STYLE['mute'])
            pdf.multi_cell(W, 4.6, _pdf_rich(
                'No roof diagram marked for this job. Import the RoofR report, then use '
                '"Mark cut-in on roof" on the Scope tab to put the overhead here.'),
                new_x='LMARGIN', new_y='NEXT', align='L')
            pdf.set_text_color(*_PDF_STYLE['ink'])

    return bytes(pdf.output())


def build_material_order_pdf(est):
    """What to buy for the SIGNED package, in the units it is ordered in.

    Separate document from the work order: the crew does not need the buy list
    and the person ordering does not need the job card. Quantities are
    converted from the estimate's squares and linear feet into bundles, sticks
    and rolls by _ORDER_PACK, and every converted row prints the arithmetic it
    used so a wrong pack size is visible here rather than at the branch."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c      = est.get('customer', {})
    a      = c.get('address', {})
    sig    = est.get('signature', {}) or {}
    enum   = _est_number(est)
    is_ins = est.get('estimate_type') == 'insurance'
    tier   = sig.get('selected_tier') or est.get('selected_tier', 'better')
    trades = est.get('trades', {})
    labels = dict(roofing='Roofing', siding='Siding', windows='Windows',
                  gutters='Gutters', other='Other / Misc')

    pdf, SANS, SERIF, W = _new_internal_pdf(f'Material order  ·  {enum}')
    section, kv = _int_styles(pdf, SANS, SERIF, W)

    def section_title(txt, need=42):
        if pdf.get_y() > pdf.h - need:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font(SERIF, 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(1)

    def table_header(cols):
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.set_draw_color(*_PDF_STYLE['navy'])
        pdf.set_line_width(0.3)
        for txt, w, align in cols:
            pdf.cell(w, 7, _pdf_rich(str(txt).upper()), border='B', align=align)
        pdf.ln()
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)

    def trunc(s, n):
        s = _pdf_oneline_rich(s)
        return s if len(s) <= n else s[:n - 1] + '...'

    pdf.set_font(SERIF, 'B', 20)
    pdf.set_text_color(*_PDF_STYLE['navy'])
    pdf.cell(W, 10, _pdf_rich('Material Order'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(*_PDF_STYLE['ink'])
    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
    pdf.ln(5)

    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                       a.get('state'), a.get('zip')]))
    kv([('Customer', c.get('name', '')),
        ('Job Address', addr_str),
        ('Estimate #', enum),
        ('Package', _pick_summary_label(est) or tier.title())])
    pdf.ln(2)

    # ── What to order ──────────────────────────────────────────────────────
    order_rows = material_order_rows(est)
    if order_rows:
        section_title('Order This', need=70)
        pdf.set_font(SANS, '', 7.5)
        pdf.set_text_color(*_PDF_STYLE['mute'])
        pdf.multi_cell(W, 4.2, _pdf_rich(_ORDER_PACK_NOTE),
                       new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(2)
        table_header([('Trade', 22, 'L'), ('Material', 60, 'L'),
                      ('Order', 28, 'R'), ('Measured', 22, 'R'),
                      ('How that was worked out', 56, 'L')])
        pdf.set_font(SANS, '', 8)
        for r in order_rows:
            if r['order_qty']:
                order_txt = f"{r['order_qty']:g} {r['order_unit']}"
            else:
                order_txt = f"{r['qty']:g} {r['unit']}"
            pdf.cell(22, 6.4, trunc(r['trade'], 12), border='B')
            pdf.cell(60, 6.4, trunc(r['name'], 40), border='B')
            pdf.set_font(SANS, 'B', 8)
            pdf.cell(28, 6.4, trunc(order_txt, 16), border='B', align='R')
            pdf.set_font(SANS, '', 8)
            pdf.cell(22, 6.4, f"{r['qty']:g} {r['unit']}", border='B', align='R')
            pdf.set_font(SANS, '', 6.5)
            pdf.set_text_color(*_PDF_STYLE['faint'])
            pdf.cell(56, 6.4, trunc(r['math'] or 'order as measured', 52), border='B')
            pdf.set_text_color(*_PDF_STYLE['ink'])
            pdf.set_font(SANS, '', 8)
            pdf.ln()
        pdf.ln(3)

    # ── Reference detail: measurements, priced list, labor ────────────────

    m = est.get('measurements') or {}

    def _mnum(key):
        try:
            return float(m.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    meas_rows = []
    # A complex carries its numbers per building, so the Area column names the
    # building rather than repeating "Commercial" seven times - the crew has to
    # be able to tell Building 3's perimeter from Building 5's. A single-roof
    # job has one unnamed set and reads exactly as it did.
    for _bld, _bm in _measurement_sets(est):
        for group, fields in MEASURE_LABELS:
            for key, label, unit in fields:
                try:
                    v = float(_bm.get(key) or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v:
                    meas_rows.append((_bld if (_bld and group == 'Commercial') else group,
                                      label, v, unit))
    # iw_second_row is a 0/1 toggle, not a MEASURE_LABELS field: a 2nd course of
    # ice & water at the eaves (code). The I&W line qty already includes it —
    # this row tells the crew WHY the footage is doubled.
    if _mnum('iw_second_row'):
        meas_rows.append(('Roof', 'Ice & Water: 2ND ROW at eaves (code) - eave LF doubled', 2, 'ROWS'))
    # Same idea for commercial: comm_work_type is a 0/1 toggle that decides which
    # labor line carries quantity, so the crew needs to see which job this is.
    if est.get('estimate_type') == 'commercial':
        for _bld, _bm in _measurement_sets(est):
            meas_rows.append((_bld or 'Commercial',
                              'JOB TYPE: NEW CONSTRUCTION (install only)'
                              if float(_bm.get('comm_work_type') or 0) else
                              'JOB TYPE: RE-ROOF (tear-off & disposal included)', 1, ''))
    if meas_rows:
        section_title('Measurements', need=60)
        table_header([('Area', 30, 'L'), ('Measurement', 92, 'L'), ('Value', 36, 'R'), ('Unit', 24, 'C')])
        pdf.set_font(SANS, '', 8)
        for group, label, v, unit in meas_rows:
            pdf.cell(30, 6, trunc(group, 18), border=1)
            pdf.cell(92, 6, trunc(label, 62), border=1)
            pdf.cell(36, 6, f'{v:g}', border=1, align='R')
            pdf.cell(24, 6, _pdf_rich(unit), border=1, align='C')
            pdf.ln()
        waste = _mnum('waste_pct')
        if waste:
            pdf.set_font(SANS, 'I', 8)
            pdf.cell(0, 6, _pdf_rich(f'Order quantities above already include {waste:g}% waste.'),
                     new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

    # Commercial complexity flags — rep-entered, never priced, but the crew and
    # the scheduler need to know before they show up.
    comm = est.get('commercial') or {}
    flags = [lbl for key, lbl in COMM_FLAG_LABELS if (comm.get('flags') or {}).get(key)]
    notes = (comm.get('notes') or '').strip()
    if est.get('estimate_type') == 'commercial' and (flags or notes):
        section_title('Job Complexity')
        pdf.set_font(SANS, '', 9)
        for lbl in flags:
            pdf.cell(0, 5.5, _pdf_rich(f'- {lbl}'), new_x='LMARGIN', new_y='NEXT')
        if notes:
            pdf.set_font(SANS, 'I', 9)
            pdf.multi_cell(0, 5, _pdf_rich(notes), new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.ln(4)

    # ── Layover / recover requirements ──
    # A recover has rules a tear-off does not, and every one of them is decided
    # ON THE ROOF before the first board goes down — whether the building is
    # even allowed a second covering, whether the existing single-ply has been
    # cut into 10' squares, whether anything underneath is wet. The crew needs
    # them on the sheet in their hands, not in a manual back at the office.
    #
    # Keyed off the 1/4" cover board rather than the bundle id: that product IS
    # the layover assembly, so a manager who builds a custom recover package
    # still gets the requirements printed.
    if est.get('estimate_type') == 'commercial' and _est_is_layover(est):
        section_title('Layover / Recover Requirements')
        pdf.set_font(SANS, 'B', 9)
        pdf.set_text_color(170, 30, 30)
        # new_x/new_y on EVERY multi_cell: without them the cursor stays where
        # the last line ended, and the next call gets a width-0 box and throws
        # "Not enough horizontal space to render a single character".
        pdf.multi_cell(0, 5, _pdf_rich(
            'VERIFY BEFORE THE FIRST BOARD GOES DOWN - a recover that does not '
            'meet these does not pass inspection and does not carry a warranty.'),
            new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)
        pdf.set_font(SANS, '', 8.5)
        for rule in COMMERCIAL_LAYOVER_RULES:
            pdf.multi_cell(0, 4.6, _pdf_rich(f'- {rule}'),
                           new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

    # ── Fastening schedule ──
    # The crew lays the roof out from this: corner spacing is not the same as
    # field spacing, and getting it wrong is how a roof leaves in a windstorm.
    if est.get('estimate_type') == 'commercial':
        _ft = _load_commercial_fastening()
        section_title('Fastening Schedule')
        for _bld, _bm in _measurement_sets(est):
            fz = commercial_fastening(_bm, _ft)
            if _bld:
                pdf.set_font(SANS, 'B', 9)
                pdf.cell(0, 5.4, _pdf_rich(_bld), new_x='LMARGIN', new_y='NEXT')
            if not fz['ok']:
                # A silently-absent section is how a crew ends up guessing. Say it loudly.
                pdf.set_font(SANS, 'B', 10)
                pdf.set_text_color(170, 30, 30)
                pdf.cell(0, 6, 'NOT CALCULATED - DO NOT ORDER FASTENERS FROM THIS SHEET',
                         new_x='LMARGIN', new_y='NEXT')
                pdf.set_text_color(0, 0, 0)
                pdf.set_font(SANS, '', 8.5)
                _why = {'no_uplift_rating': 'No uplift rating was selected on the estimate.',
                        'missing_dimensions': 'Building length, width, and height were not entered.',
                        'no_table': 'No fastening table is configured.'}
                pdf.multi_cell(0, 4.6, _pdf_rich(
                    _why.get(fz['reason'], 'Required inputs were missing.') +
                    ' Fastener quantities on the Material Order are ZERO. Confirm the fastening '
                    'schedule against the system approval before the crew starts.'), new_x='LMARGIN', new_y='NEXT', align='L')
            else:
                pdf.set_font(SANS, '', 8.5)
                _zr = _ft.get('zone_rule') or {}
                pdf.cell(0, 4.8, _pdf_rich(
                    f"Uplift: {fz['rating_label']}   |   Zone width a = {fz['a']:.1f} ft   |   "
                    f"{_zr.get('standard', '')}"
                    f"{' (L-shaped corners)' if _zr.get('corner_shape') == 'L' else ' (square corners)'}"),
                    new_x='LMARGIN', new_y='NEXT')
                pdf.ln(1)
                table_header([('Zone', 26, 'L'), ('Area SF', 24, 'R'), ('Plates/Bd', 22, 'R'),
                              ('Insul Qty', 24, 'R'), ('Seam Spacing', 34, 'C'), ('Seam Qty', 24, 'R')])
                pdf.set_font(SANS, '', 8)
                for _z in ('field', 'perimeter', 'corner'):
                    _zi, _in, _se = fz['zones'][_z], fz['insul']['by_zone'][_z], fz['seam']['by_zone'][_z]
                    pdf.cell(26, 6, _z.title(), border=1)
                    pdf.cell(24, 6, f"{_zi['sf']:,.0f}", border=1, align='R')
                    pdf.cell(22, 6, f"{_in['per_board']:g}" if fz['insul']['applies'] else '-', border=1, align='R')
                    pdf.cell(24, 6, f"{math.ceil(_in['count']):,}" if fz['insul']['applies'] else '-', border=1, align='R')
                    pdf.cell(34, 6, (f"{_se['spacing_in']:g}\" o.c. / {_se['sheet_width_ft']:g}ft sheet"
                                     if fz['seam']['applies'] else '-'), border=1, align='C')
                    pdf.cell(24, 6, f"{math.ceil(_se['count']):,}" if fz['seam']['applies'] else '-', border=1, align='R')
                    pdf.ln()
                pdf.set_font(SANS, 'B', 8)
                pdf.cell(96, 6, f"TOTAL (incl. {fz['waste_pct']:g}% waste)", border=1)
                pdf.cell(24, 6, f"{fz['insul']['total']:,}" if fz['insul']['applies'] else '0', border=1, align='R')
                pdf.cell(34, 6, '', border=1)
                pdf.cell(24, 6, f"{fz['seam']['total']:,}" if fz['seam']['applies'] else '0', border=1, align='R')
                pdf.ln()
                pdf.set_font(SANS, 'I', 7.5)
                if not fz['seam']['applies']:
                    pdf.cell(0, 4.4, _pdf_rich('Seam fasteners: not applicable for this system.'),
                             new_x='LMARGIN', new_y='NEXT')
                if not fz['insul']['applies']:
                    pdf.cell(0, 4.4, _pdf_rich('Insulation fasteners: no fastened layers on this job.'),
                             new_x='LMARGIN', new_y='NEXT')
                # new_x/new_y on every multi_cell, per the note further up: without
                # them the cursor stays where the last line ended, and the NEXT
                # building's schedule gets a width-0 box and throws. Harmless
                # while this ran once; a complex runs it per building.
                for _w in fz['warnings']:
                    pdf.multi_cell(0, 4.2, _pdf_rich('! ' + _w),
                                   new_x='LMARGIN', new_y='NEXT')
                pdf.multi_cell(0, 4.2, _pdf_rich(_ft.get('source_note', '')),
                               new_x='LMARGIN', new_y='NEXT')
            pdf.ln(4)

    if not is_ins:
        # Materials: signed-tier lines with a material cost (or simple-mode lines)
        # The priced "Materials" list and the "Labor Summary" that used to sit
        # here are gone. Materials showed the same rows as "Order This" in
        # measured units, which that table already carries as a column; the
        # work items are what the crew does, so they moved to the work order.
        # This document is a purchase order.

        # ── Siding Material Take-off ─────────────────────────────────────
        # QXO-format supplier order: piece counts per SKU derived from the
        # signed measurements + tier profile. Shown alongside (not replacing)
        # the priced Materials section above. Ships base pieces + pieces at
        # the sheet's +15%/+19% waste columns so the ordering desk sees both.
        siding_tier = _trade_tier(est, 'siding')
        takeoff_rows = siding_material_takeoff(est, siding_tier)
        if takeoff_rows:
            profile = _siding_profile(trades.get('siding') or {}, siding_tier)
            profile_lbl = SIDING_PROFILE_LABELS.get(profile, profile) or profile
            section_title(f'Siding Material Take-off — {profile_lbl}')
            pdf.set_font(SANS, 'I', 8)
            pdf.multi_cell(W, 4.4, _pdf_rich(
                'Piece counts converted from the signed measurements per the QXO '
                'take-off sheet. \"Order\" column includes waste (siding +15%, trim +19%) '
                'and is rounded up to whole units.'), new_x='LMARGIN', new_y='NEXT', align='L')
            pdf.ln(1)
            table_header([('Section', 28, 'L'), ('Product', 96, 'L'),
                          ('Size', 32, 'L'), ('Pieces', 16, 'R'), ('Order', 16, 'R')])
            pdf.set_font(SANS, '', 8)
            for section, product, size, pieces_base, pieces_order in takeoff_rows:
                pdf.cell(28, 6, trunc(section, 16), border=1)
                pdf.cell(96, 6, trunc(product, 64), border=1)
                pdf.cell(32, 6, trunc(size, 20), border=1)
                if pieces_base <= 0 and pieces_order <= 0:
                    # Advisory row (source_note) — no numbers to show.
                    pdf.cell(16, 6, '', border=1, align='R')
                    pdf.cell(16, 6, '', border=1, align='R')
                else:
                    pdf.cell(16, 6, f'{pieces_base:.1f}', border=1, align='R')
                    pdf.cell(16, 6, f'{pieces_order:g}', border=1, align='R')
                pdf.ln()
            pdf.ln(4)

    pdf.set_font(SANS, 'I', 7.5)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, _pdf_rich('Generated from the signed contract. Excludes change orders. '
                             'Internal document - no pricing included.'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())




def build_production_packet_pdf(est):
    """Back-compat: the production packet is now two documents.

    Returns the work order, which is what every existing caller (the CRM push,
    the Documents tab regenerate) meant by "the packet". The material order is
    generated alongside it by generate_production_packet.
    """
    return build_work_order_pdf(est)


def push_contract_to_crm(est_id, pdf_bytes=None):
    """Upload the signed contract PDF to Base44 and create a Document record
    tagged 'contract' on the linked CRM job. Runs in a background thread —
    logs everything, never raises. Accepts a pre-built PDF so the post-sign
    pipeline can build it once and reuse it across steps."""
    try:
        est = est_load(est_id)
        if est is None:
            print(f'[crm-push] estimate {est_id} not found')
            return

        proj_id = (est.get('customer', {}).get('crm_project_id')
                   or est.get('crm_project_id'))
        if not proj_id:
            print(f'[crm-push] {est_id}: not linked to a CRM job — skipping push')
            return
        if http is None:
            print('[crm-push] requests not installed — cannot push')
            return

        c     = est.get('customer', {})
        sig   = est.get('signature', {}) or {}
        enum  = _est_number(est)
        cname = (c.get('name') or 'Customer').strip()
        safe_name = ''.join(ch if ch.isalnum() or ch in ' -_' else '' for ch in cname).strip().replace(' ', '_')
        fname = f'Signed_Contract_{enum}_{safe_name}.pdf'

        # 1) Build the PDF (unless the caller already did)
        if pdf_bytes is None:
            try:
                pdf_bytes = build_signed_pdf(est)
                print(f'[crm-push] built PDF ({len(pdf_bytes)} bytes)')
            except Exception as exc:
                print(f'[crm-push] PDF build failed: {exc}')

        # 2) Upload the file to Base44 storage
        file_url  = None
        file_type = 'application/pdf'
        file_size = None
        if pdf_bytes:
            try:
                r = http.post(f'{BASE_URL}/integrations/Core/UploadFile',
                              headers=crm_headers(),
                              files={'file': (fname, pdf_bytes, 'application/pdf')},
                              timeout=60)
                print(f'[crm-push] upload status {r.status_code}: {r.text[:300]}')
                r.raise_for_status()
                resp = r.json()
                file_url  = (resp.get('file_url') or resp.get('url')
                             or resp.get('file_uri') or resp.get('uri'))
                file_size = len(pdf_bytes)
            except Exception as exc:
                print(f'[crm-push] file upload failed: {exc}')

        # Fallback: link the CRM doc to our hosted signed page
        if not file_url:
            base = _base_url()
            _tok = est.get('share_token')
            file_url  = f'{base}/sign/{_tok}' if _tok else f'{base}/api/estimates/{est_id}/signed'
            file_type = 'text/html'
            print('[crm-push] using hosted signed-page link as file_url fallback')

        # 3) Create the Document record on the job
        signed_at = sig.get('signed_at', '')[:10]
        doc = {
            'name':              f'Signed Contract - {cname} ({enum})',
            'type':              'contract',
            'project_id':        proj_id,
            'file_url':          file_url,
            'file_type':         file_type,
            'description':       (f'Signed electronically by {sig.get("name", "")} on '
                                  f'{signed_at}. Uploaded automatically by Estimate Builder.'),
            'share_with_client': False,
        }
        if file_size:
            doc['file_size'] = file_size
        r2 = http.post(f'{BASE_URL}/entities/Document',
                       headers={**crm_headers(), 'Content-Type': 'application/json'},
                       json=doc, timeout=30)
        print(f'[crm-push] document create status {r2.status_code}: {r2.text[:300]}')
        r2.raise_for_status()
        doc_id = (r2.json() or {}).get('id', '')

        # 4) Record the push on the estimate
        try:
            def _mark(doc):
                if doc is None:
                    return None
                doc['crm_document_id'] = doc_id
                doc['crm_pushed_at']   = datetime.utcnow().isoformat() + 'Z'
                return doc
            est_update(est_id, _mark)
        except Exception:
            pass
        print(f'[crm-push] SUCCESS — contract for {cname} pushed to CRM job {proj_id} (doc {doc_id})')
    except Exception as exc:
        print(f'[crm-push] unexpected failure for {est_id}: {exc}')


def _crm_file_document(est, pdf_bytes, upload_name, hosted_url, doc_name,
                       doc_type, description):
    """Upload a PDF to Base44 storage and create a Document record on the
    estimate's linked CRM job. Returns (doc_id, error) — exactly one is set.

    Base44's external API doesn't expose the UploadFile integration to our
    token (blanket 405 as of 2026-07), so on upload failure the Document
    links to the PDF hosted on this app instead (/uploads/ is public and the
    UUID filename is unguessable). Unknown Document.type values 4xx — retry
    once as 'other'."""
    proj_id = (est.get('customer', {}).get('crm_project_id')
               or est.get('crm_project_id'))
    if not proj_id:
        return None, 'not_linked'
    if http is None:
        return None, 'requests not installed on server'

    file_url = None
    try:
        r = http.post(f'{BASE_URL}/integrations/Core/UploadFile',
                      headers=crm_headers(),
                      files={'file': (upload_name, pdf_bytes, 'application/pdf')},
                      timeout=60)
        r.raise_for_status()
        resp = r.json()
        file_url = (resp.get('file_url') or resp.get('url')
                    or resp.get('file_uri') or resp.get('uri'))
    except Exception as exc:
        print(f'[crm-doc] Base44 upload unavailable ({exc}) — using hosted link')
    if not file_url:
        file_url = hosted_url

    doc = {
        'name':              doc_name,
        'type':              doc_type,
        'project_id':        proj_id,
        'file_url':          file_url,
        'file_type':         'application/pdf',
        'file_size':         len(pdf_bytes),
        'description':       description,
        'share_with_client': False,
    }
    r2 = http.post(f'{BASE_URL}/entities/Document',
                   headers={**crm_headers(), 'Content-Type': 'application/json'},
                   json=doc, timeout=30)
    if r2.status_code >= 400 and doc_type != 'other':
        print(f'[crm-doc] create failed with type={doc_type!r} '
              f'({r2.status_code}: {r2.text[:200]}) — retrying as "other"')
        doc['type'] = 'other'
        r2 = http.post(f'{BASE_URL}/entities/Document',
                       headers={**crm_headers(), 'Content-Type': 'application/json'},
                       json=doc, timeout=30)
    print(f'[crm-doc] document create status {r2.status_code}: {r2.text[:200]}')
    if r2.status_code >= 400:
        return None, f'CRM document create failed ({r2.status_code})'
    return (r2.json() or {}).get('id', ''), None


@app.route('/api/estimates/<est_id>/push-document', methods=['POST'])
def push_document_to_crm(est_id):
    """Upload one of the estimate's Document-tab PDFs to the Base44 CRM and
    create a labeled Document record on the linked job (same pipeline the
    signed-contract push uses). Synchronous — the Documents tab shows the
    result immediately. Skips quietly when the estimate isn't CRM-linked."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'bad id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Estimate not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()

    d = request.get_json(force=True)
    filename = d.get('filename') or ''
    label    = (d.get('label') or 'Document').strip()
    doc_type = (d.get('doc_type') or 'other').strip()

    proj_id = (est.get('customer', {}).get('crm_project_id')
               or est.get('crm_project_id'))
    if not proj_id:
        return jsonify({'skipped': 'not_linked'})
    if http is None:
        return jsonify({'error': 'requests not installed on server'}), 500

    # The filename must be one of this estimate's own attachments
    att = next((a for a in est.get('attachments', []) if a.get('filename') == filename), None)
    parts = filename.split('/')
    if not att or len(parts) != 2 or parts[0] != est_id or not _safe_path_id(parts[1]):
        return jsonify({'error': 'unknown document'}), 404
    fpath = os.path.join(UPLOADS_DIR, parts[0], parts[1])
    if not os.path.exists(fpath):
        return jsonify({'error': 'file missing on server'}), 404

    with open(fpath, 'rb') as f:
        pdf_bytes = f.read()
    cname = (est.get('customer', {}).get('name') or 'Customer').strip()
    safe_label = ''.join(ch if ch.isalnum() or ch in ' -_' else '' for ch in label).strip().replace(' ', '_')
    fname = f'{safe_label or "Document"}.pdf'

    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=fname,
        hosted_url=f'{_base_url()}/uploads/{filename}',
        doc_name=f'{label} - {cname}', doc_type=doc_type,
        description=f'Uploaded from the Estimate Builder Documents tab by {_current_user()}.')
    if err:
        return jsonify({'error': err}), 502

    # Persist the link on the attachment server-side so it survives even if
    # the client never saves again (client mirrors it into S as well).
    try:
        def _mark(doc):
            if doc is None:
                return None
            for a in doc.get('attachments', []):
                if a.get('filename') == filename:
                    a['crm_document_id'] = doc_id
            return doc
        est_update(est_id, _mark)
    except Exception:
        pass
    print(f'[crm-push-doc] SUCCESS — "{label}" pushed to CRM job {proj_id} (doc {doc_id})')
    return jsonify({'crm_document_id': doc_id})


def generate_production_packet(est_id, push_to_crm=False):
    """Build the production-packet PDF for a signed estimate and store it as a
    server-generated attachment (replacing any previous packet). By default it
    does NOT file to the CRM — the packet contains post-signing fields (crew
    schedule, dish, tear-off layers) that the rep fills in later, so pushing
    at signing time would ship an unfinished doc. The rep clicks "↗ Push to
    Den" on the Documents tab when the packet is finalized. Returns the
    attachment dict. Raises on failure — callers decide whether that's fatal
    (endpoint) or logged (pipeline)."""
    est = est_load(est_id)
    if est is None:
        raise ValueError('estimate not found')
    if not est.get('signature'):
        raise ValueError('estimate is not signed')

    # Two documents, two audiences: the crew works from the work order, the
    # buy list goes to whoever places the supplier order. They used to be one
    # PDF, so the crew carried the ordering sheet and the buyer paged past the
    # job card.
    sig  = est.get('signature') or {}
    tier = sig.get('selected_tier') or est.get('selected_tier', 'better')
    if est.get('estimate_type') == 'insurance':
        pkg = ''
    elif est.get('estimate_type') == 'commercial' and not _pick_summary_label(est):
        pkg = ' - Commercial'
    else:
        pkg = f' - {_pick_summary_label(est) or tier.title()}'

    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)

    built = []
    for builder, doc_type, prefix, name in (
            (build_work_order_pdf,    'work_order',     'workorder', 'Work Order'),
            (build_material_order_pdf, 'material_order', 'material',  'Material Order')):
        body = builder(est)
        fn = f'{prefix}_{uuid.uuid4().hex[:8]}.pdf'
        with open(os.path.join(dest_dir, fn), 'wb') as f:
            f.write(body)
        built.append({
            'id':               uuid.uuid4().hex[:12],
            'filename':         f'{est_id}/{fn}',
            'label':            f'{name}{pkg}',
            'doc_type':         doc_type,
            'show_in_estimate': False,   # internal — never on the customer page
            'server_generated': True,
            'generated_at':     datetime.utcnow().isoformat() + 'Z',
        })
    att, mat_att = built
    pdf_bytes = None   # set below for the CRM push (work order)
    fname = built[0]['filename'].split('/')[-1]

    # Replace any previous packet (and clean up its files). Both packet
    # doc_types — other server-generated docs (signed change orders) stay put.
    def _is_packet(x):
        return (x.get('server_generated')
                and x.get('doc_type') in ('work_order', 'material_order'))

    def _swap_packet(doc):
        if doc is None:
            return None
        for old in filter(_is_packet, doc.get('attachments') or []):
            parts = (old.get('filename') or '').split('/')
            if len(parts) == 2 and parts[0] == est_id and _safe_path_id(parts[1]):
                try:
                    os.remove(os.path.join(UPLOADS_DIR, parts[0], parts[1]))
                except OSError:
                    pass
        doc['attachments'] = [x for x in doc.get('attachments') or []
                              if not _is_packet(x)] + built
        return doc

    est = est_update(est_id, _swap_packet) or est

    if not push_to_crm:
        return att

    # Explicit push (manual "↗ Push to Den" button from the Documents tab)
    c     = est.get('customer', {})
    cname = (c.get('name') or 'Customer').strip()
    enum  = _est_number(est)
    with open(os.path.join(dest_dir, fname), 'rb') as f:
        pdf_bytes = f.read()
    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=f'Work_Order_{enum}.pdf',
        hosted_url=f'{_base_url()}/uploads/{est_id}/{fname}',
        doc_name=f'Work Order - {cname} ({enum})', doc_type='work_order',
        description='Work order generated from the signed contract. The material '
                    'order is a separate document.')
    if doc_id:
        def _mark_pushed(doc):
            if doc is None:
                return None
            for x in doc.get('attachments', []):
                if x.get('id') == att['id']:
                    x['crm_document_id'] = doc_id
            return doc
        est_update(est_id, _mark_pushed)
        att['crm_document_id'] = doc_id
    elif err and err != 'not_linked':
        print(f'[packet] CRM push failed for {est_id}: {err}')
    return att


def _cost_split_by_trade(est):
    """(materials_cost, labor_cost, sell_total) per enabled non-insurance trade
    at its selected tier, for the permit packet's cost-breakdown table. Skips
    excluded lines and zero-qty items — same rules the priced totals use."""
    rows = []
    trades = est.get('trades') or {}
    for tk in GBB_TRADES:
        td = trades.get(tk) or {}
        if not td.get('enabled'):
            continue
        tier = _trade_tier(est, tk)
        trade_mode = _trade_mode(tk, td)
        mat_cost = lab_cost = 0.0
        for item in td.get('line_items') or []:
            qty = float(item.get('quantity') or 0)
            if qty <= 0:
                continue
            if trade_mode == 'simple':
                mat_cost += float(item.get('unit_cost') or 0) * qty
                # simple-mode carries no labor line — the material_cost/unit_cost
                # is already the all-in cost basis. Nothing to add to lab_cost.
            else:
                t = (item.get('tiers') or {}).get(tier) or {}
                if t.get('included') is False:
                    continue
                mat_cost += float(t.get('material_unit_cost') or 0) * qty
                lab_cost += float(t.get('labor_unit_cost') or 0) * qty
        sell = _trade_subtotal(est, tk, tier)
        if mat_cost > 0 or lab_cost > 0 or sell > 0:
            rows.append({'trade': tk, 'materials_cost': mat_cost,
                         'labor_cost': lab_cost, 'sell': sell})
    return rows


def _selected_permit_jurisdiction(est):
    """Return the manager-approved jurisdiction dict (from jurisdictions.json)
    the estimate points at — or the Colorado baseline when none is selected.
    Includes the verified_profile only when it has a reviewed_at timestamp
    (matches the customer-view rule)."""
    jx       = _load_jurisdictions() or {}
    baseline = jx.get('colorado_baseline') or {}
    perm     = est.get('permit_jurisdiction') or {}
    sel_id   = (perm.get('selected_id') or perm.get('auto_id') or '').strip()
    jur = None
    if sel_id:
        for j in (jx.get('jurisdictions') or []):
            if isinstance(j, dict) and j.get('id') == sel_id:
                jur = j
                break
    vp = (jur.get('verified_profile') if isinstance(jur, dict) else None) or {}
    if not (vp.get('reviewed_at') or '').strip():
        vp = {}
    return {
        'name':     (jur.get('name') if jur else '') or 'Colorado (statewide baseline)',
        'kind':     (jur.get('kind') if jur else ''),
        'county':   (jur.get('county') if jur else ''),
        'office':   (jur.get('office') if jur else ''),
        'verified': bool(perm.get('verified')),
        'adopted_code':    (vp.get('adopted_code') or '').strip(),
        'submittal_method': ((vp.get('reroof_permit') or {}).get('submittal_method') or '').strip(),
        'portal_url':       ((vp.get('reroof_permit') or {}).get('portal_url') or '').strip(),
        'fee_basis':        ((vp.get('reroof_permit') or {}).get('fee_basis') or '').strip(),
        'delegated_to':     (vp.get('delegated_to') or '').strip(),
    }


def build_permit_packet_pdf(est):
    """Permit-application prep sheet. Everything an office admin needs to pull
    a permit at any Colorado jurisdiction: where to apply, the job address,
    installed roofing squares + brand/color, and a materials/labor/total cost
    breakdown for the fee schedule. Not a filled application — the city's own
    form still gets filled in (the Loveland permit generator does that for one
    specific city). This is the info sheet."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c        = est.get('customer', {})
    a        = c.get('address', {})
    sig      = est.get('signature') or {}
    enum     = _est_number(est)
    trades   = est.get('trades') or {}
    m        = est.get('measurements') or {}
    jur      = _selected_permit_jurisdiction(est)

    def _mnum(k):
        try:
            return float(m.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    roof_sq   = _mnum('roof_squares')
    low_slope = _mnum('low_slope_squares')
    waste_pct = _mnum('waste_pct')
    installed = max(roof_sq - low_slope, 0.0) * (1 + waste_pct / 100.0)

    pdf, SANS, SERIF, W = _new_internal_pdf(f'Permit packet  ·  {enum}')
    section, kv = _int_styles(pdf, SANS, SERIF, W)
    pdf.set_font(SERIF, 'B', 20)
    pdf.set_text_color(*_PDF_STYLE['navy'])
    pdf.cell(W, 10, _pdf_rich('Permit Application Packet'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(*_PDF_STYLE['ink'])
    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
    pdf.ln(5)

    def section_title(txt):
        if pdf.get_y() > pdf.h - 46:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font(SERIF, 'B', 13)
        pdf.set_text_color(*_PDF_STYLE['navy'])
        pdf.cell(0, 7, _pdf_rich(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.ln(1)

    def table_header(cols):
        pdf.set_font(SANS, '', 6.5)
        pdf.set_text_color(*_PDF_STYLE['faint'])
        pdf.set_draw_color(*_PDF_STYLE['navy'])
        pdf.set_line_width(0.3)
        for txt, w, align in cols:
            pdf.cell(w, 7, _pdf_rich(str(txt).upper()), border='B', align=align)
        pdf.ln()
        pdf.set_text_color(*_PDF_STYLE['ink'])
        pdf.set_draw_color(*_PDF_STYLE['rule'])
        pdf.set_line_width(0.2)

    def kv_row(label, val):
        if not val:
            return
        pdf.set_font(SANS, 'B', 9)
        pdf.cell(50, 5.5, _pdf_rich(label))
        pdf.set_font(SANS, '', 9)
        pdf.multi_cell(W - 50, 5.5, _pdf_rich(val), new_x='LMARGIN', new_y='NEXT', align='L')

    # Where to apply
    section_title('Apply To')
    kv_row('Jurisdiction',      jur['name'])
    if jur['office']:
        kv_row('Building Office', jur['office'])
    # County deliberately not shown: the sheet only needs to say where the
    # permit gets pulled, and the office name already carries the jurisdiction.
    if jur['submittal_method']:
        kv_row('Submittal Method', jur['submittal_method'])
    if jur['portal_url']:
        kv_row('Portal URL',      jur['portal_url'])
    if jur['fee_basis']:
        kv_row('Fee Basis',       jur['fee_basis'])
    if jur['adopted_code']:
        kv_row('Adopted Code',    jur['adopted_code'])
    # When this jurisdiction contracts inspections out, the office admin has
    # to walk the permit somewhere else entirely — Colorado Springs jobs are
    # pulled at the Pikes Peak Regional Building Department, not at the city.
    # Getting this wrong costs a trip.
    if jur.get('delegated_to'):
        kv_row('Permits Issued By', jur['delegated_to'])
    if not jur['verified']:
        pdf.ln(1)
        pdf.set_font(SANS, 'I', 8)
        pdf.set_text_color(170, 30, 30)
        pdf.multi_cell(W, 4.4, _pdf_rich(
            'Jurisdiction has NOT been manager-verified for this address — '
            'confirm the correct AHJ before submitting.'), new_x='LMARGIN', new_y='NEXT', align='L')
        pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # Job
    section_title('Job')
    addr = ', '.join(filter(None, [a.get('street'), a.get('city'), a.get('state'), a.get('zip')]))
    kv_row('Job Address',   addr)
    kv_row('Customer',      c.get('name', ''))
    kv_row('Phone',         c.get('phone', ''))
    kv_row('Estimate #',    enum)
    signed_at = sig.get('signed_at', '')
    try:
        signed_fmt = datetime.fromisoformat(signed_at.replace('Z', '+00:00')).strftime('%B %d, %Y')
    except Exception:
        signed_fmt = signed_at
    if signed_fmt:
        kv_row('Contract Signed', signed_fmt)
    pdf.ln(3)

    # Squares installed
    section_title('Roofing Scope')
    if roof_sq > 0:
        detail = (f'{installed:.1f} SQ (roof {roof_sq:g} SQ + {waste_pct:g}% waste'
                  + (f', minus {low_slope:g} SQ low-slope' if low_slope else '') + ')')
        kv_row('Squares to Install', detail)
    steep = _mnum('steep_squares')
    pitch = _mnum('predominant_pitch')
    if steep > 0 or pitch > 0:
        pitch_txt = f' at {int(pitch)}/12' if pitch > 0 else ''
        kv_row('Steep Area', (f'{steep:g} SQ steep{pitch_txt}' if steep > 0
                              else f'Predominant pitch {int(pitch)}/12'))
    # The material actually going on the roof — the permit clerk needs the
    # covering, not just its color. Pulled from the signed tier's bundle so it
    # names the product ("CertainTeed Northgate"), which is what the roofing
    # affidavit and the Class-4 question both turn on.
    try:
        _mf = _build_estimate_manifest(est)
        for _t in (_mf.get('trades') or []):
            if _t.get('key') != 'roofing':
                continue
            for _ti in (_t.get('tiers') or []):
                if not _ti.get('is_selected'):
                    continue
                _name = (_ti.get('material_name') or _ti.get('package_name') or '').strip()
                if _name:
                    kv_row('Roof Covering', _name)
                _wm = (_ti.get('workmanship') or '').strip()
                if _wm:
                    kv_row('Workmanship', _wm)
                break
            break
    except Exception:
        pass
    pdf.ln(2)

    # Material brand/color per trade
    prod_rows = []
    for trade, fields in TRADE_COLOR_FIELDS.items():
        td = trades.get(trade) or {}
        if not td.get('enabled'):
            continue
        colors = td.get('colors') or {}
        for key, label in fields:
            v = (colors.get(key) or '').strip()
            if v:
                prod_rows.append((_PRODUCT_TRADE_LABELS.get(trade, trade.title()), label, v))
    # Shingle color/siding color from the signed record — falls back to the
    # trade's colors dict when the customer didn't pick during signing
    ss_pick = (sig.get('shingle_color') or '').strip()
    if ss_pick and not any(r[1] == 'Shingle Color' for r in prod_rows):
        prod_rows.insert(0, ('Roofing', 'Shingle Color', ss_pick))
    sd_pick = (sig.get('siding_color') or '').strip()
    if sd_pick and not any(r[1] == 'Siding Color' for r in prod_rows):
        prod_rows.append(('Siding', 'Siding Color', sd_pick))
    if prod_rows:
        section_title('Material Selection')
        pdf.set_fill_color(*_PDF_STYLE['paper'])
        pdf.set_font(SANS, 'B', 8)
        for txt, w, align in [('Trade', 30, 'L'), ('Item', 60, 'L'), ('Selection', 92, 'L')]:
            pdf.cell(w, 6.5, _pdf_rich(txt), border=1, fill=True, align=align)
        pdf.ln()
        pdf.set_font(SANS, '', 8)
        for trade, label, v in prod_rows:
            pdf.cell(30, 6, _pdf_rich(trade[:18]), border=1)
            pdf.cell(60, 6, _pdf_rich(label[:40]), border=1)
            pdf.cell(92, 6, _pdf_rich(v[:62]), border=1)
            pdf.ln()
        pdf.ln(3)

    # Cost breakdown
    section_title('Cost Breakdown')
    rows = _cost_split_by_trade(est)
    total_mat = sum(r['materials_cost'] for r in rows)
    total_lab = sum(r['labor_cost']     for r in rows)
    total_sell = _estimate_total(est)
    # Materials + Labor with NO margin on it is the number most fee schedules
    # ask for as job valuation, and it had to be added up by hand off the old
    # table — the TOTAL row carried the two costs and the contract value, but
    # never their sum. Contract value stays (some jurisdictions fee on it) but
    # no longer leads.
    cw = (38, 40, 40, 42, 40)
    table_header([('Trade', cw[0], 'L'), ('Materials', cw[1], 'R'),
                  ('Labor', cw[2], 'R'), ('Cost Total', cw[3], 'R'),
                  ('Contract Value', cw[4], 'R')])
    pdf.set_font(SANS, '', 8.5)
    for r in rows:
        pdf.cell(cw[0], 7, _pdf_rich(_PRODUCT_TRADE_LABELS.get(r['trade'], r['trade'].title())),
                 border='B')
        pdf.cell(cw[1], 7, f'${r["materials_cost"]:,.2f}', border='B', align='R')
        pdf.cell(cw[2], 7, f'${r["labor_cost"]:,.2f}',     border='B', align='R')
        pdf.cell(cw[3], 7, f'${r["materials_cost"] + r["labor_cost"]:,.2f}',
                 border='B', align='R')
        pdf.cell(cw[4], 7, f'${r["sell"]:,.2f}',           border='B', align='R')
        pdf.ln()
    pdf.set_draw_color(*_PDF_STYLE['navy'])
    pdf.set_line_width(0.3)
    pdf.set_font(SANS, 'B', 9)
    pdf.set_text_color(*_PDF_STYLE['navy'])
    pdf.cell(cw[0], 8, _pdf_rich('TOTAL'), border='T')
    pdf.cell(cw[1], 8, f'${total_mat:,.2f}', border='T', align='R')
    pdf.cell(cw[2], 8, f'${total_lab:,.2f}', border='T', align='R')
    pdf.cell(cw[3], 8, f'${total_mat + total_lab:,.2f}', border='T', align='R')
    pdf.cell(cw[4], 8, f'${total_sell:,.2f}', border='T', align='R')
    pdf.ln()
    pdf.set_text_color(*_PDF_STYLE['ink'])
    pdf.set_draw_color(*_PDF_STYLE['rule'])
    pdf.set_line_width(0.2)
    pdf.ln(2)
    pdf.set_font(SANS, '', 7.5)
    pdf.set_text_color(*_PDF_STYLE['mute'])
    pdf.multi_cell(W, 4.4, _pdf_rich(
        'Cost Total is materials plus labor at Project One Roofing cost basis, with '
        'no margin added — this is the job valuation most fee schedules ask for. '
        'Contract Value is the customer-facing price and matches the signed contract; '
        'some jurisdictions fee on that instead.'), new_x='LMARGIN', new_y='NEXT', align='L')
    pdf.set_text_color(*_PDF_STYLE['ink'])

    # No trailing "internal document" line — the running footer already says it
    # on every page, and the duplicate was spilling onto a second, otherwise
    # empty sheet.
    return bytes(pdf.output())


def generate_permit_packet(est_id, push_to_crm=True):
    """Build the permit-application PDF, save as a server-generated attachment
    (swaps any prior permit-packet row), and file it on the Base44 job. Permit
    info is derivable at signing time and doesn't need post-sign editing — safe
    to push automatically. Returns the attachment dict."""
    est = est_load(est_id)
    if est is None:
        raise ValueError('estimate not found')
    if not est.get('signature'):
        raise ValueError('estimate is not signed')

    pdf_bytes = build_permit_packet_pdf(est)
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    fname = f'permit_{uuid.uuid4().hex[:8]}.pdf'
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(pdf_bytes)

    c     = est.get('customer', {})
    cname = (c.get('name') or 'Customer').strip()
    enum  = _est_number(est)
    att = {
        'id':               uuid.uuid4().hex[:12],
        'filename':         f'{est_id}/{fname}',
        'label':            f'Permit Application Packet - {cname}',
        'doc_type':         'permit_packet',
        'show_in_estimate': False,
        'server_generated': True,
        'generated_at':     datetime.utcnow().isoformat() + 'Z',
    }

    def _is_permit(x):
        return x.get('server_generated') and x.get('doc_type') == 'permit_packet'

    def _swap(doc):
        if doc is None:
            return None
        for old in filter(_is_permit, doc.get('attachments') or []):
            parts = (old.get('filename') or '').split('/')
            if len(parts) == 2 and parts[0] == est_id and _safe_path_id(parts[1]):
                try:
                    os.remove(os.path.join(UPLOADS_DIR, parts[0], parts[1]))
                except OSError:
                    pass
        doc['attachments'] = [x for x in doc.get('attachments') or []
                              if not _is_permit(x)] + [att]
        return doc

    est = est_update(est_id, _swap) or est

    if not push_to_crm:
        return att

    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=f'Permit_Packet_{enum}.pdf',
        hosted_url=f'{_base_url()}/uploads/{est_id}/{fname}',
        doc_name=f'Permit Application Packet - {cname} ({enum})',
        doc_type='permit',
        description='Permit application info sheet generated from the signed contract.')
    if doc_id:
        def _mark_pushed(doc):
            if doc is None:
                return None
            for x in doc.get('attachments', []):
                if x.get('id') == att['id']:
                    x['crm_document_id'] = doc_id
            return doc
        est_update(est_id, _mark_pushed)
        att['crm_document_id'] = doc_id
    elif err and err != 'not_linked':
        print(f'[permit] CRM push failed for {est_id}: {err}')
    return att


def save_signed_contract_attachment(est_id, pdf_bytes):
    """Save the signed contract PDF as a server-generated attachment on the
    estimate so the rep can see it in the Documents tab. Swaps any previous
    signed-contract row (and cleans up its file). Internal — never surfaced on
    the customer page. Returns the attachment dict, or None if pdf_bytes was
    missing."""
    if not pdf_bytes:
        return None
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    fname = f'signed_{uuid.uuid4().hex[:8]}.pdf'
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(pdf_bytes)

    att = {
        'id':               uuid.uuid4().hex[:12],
        'filename':         f'{est_id}/{fname}',
        'label':            'Signed Contract',
        'doc_type':         'signed_contract',
        'show_in_estimate': False,   # already lives on the customer's copy
        'server_generated': True,
        'generated_at':     datetime.utcnow().isoformat() + 'Z',
    }

    def _is_signed(x):
        return x.get('server_generated') and x.get('doc_type') == 'signed_contract'

    def _swap(doc):
        if doc is None:
            return None
        for old in filter(_is_signed, doc.get('attachments') or []):
            parts = (old.get('filename') or '').split('/')
            if len(parts) == 2 and parts[0] == est_id and _safe_path_id(parts[1]):
                try:
                    os.remove(os.path.join(UPLOADS_DIR, parts[0], parts[1]))
                except OSError:
                    pass
        doc['attachments'] = [x for x in doc.get('attachments') or []
                              if not _is_signed(x)] + [att]
        return doc

    est_update(est_id, _swap)
    return att


# The sign route runs the pipeline on a thread with this name. Naming it is
# what lets a test join it and then assert in the foreground: starting a SECOND
# pipeline alongside the running one is exactly the concurrent read-modify-write
# the docstring below exists to prevent, and doing it intermittently wiped the
# estimate's attachment list on CI.
POST_SIGN_THREAD = 'post-sign-pipeline'


def _post_sign_pipeline(est_id):
    """Post-signature background work, run sequentially in ONE thread so two
    writers never read-modify-write the same estimate concurrently."""
    # Build the signed PDF once, then reuse it for the local Documents-tab
    # attachment and the CRM push.
    pdf_bytes = None
    try:
        est = est_load(est_id)
        if est is not None:
            pdf_bytes = build_signed_pdf(est)
    except Exception as exc:
        print(f'[post-sign] signed PDF build failed for {est_id}: {exc}')

    if pdf_bytes:
        try:
            att = save_signed_contract_attachment(est_id, pdf_bytes)
            if att:
                print(f"[signed] filed {att['filename']} in Documents for {est_id}")
        except Exception as exc:
            print(f'[signed] attachment save failed for {est_id}: {exc}')

    push_contract_to_crm(est_id, pdf_bytes=pdf_bytes)
    try:
        att = generate_production_packet(est_id)
        print(f"[packet] generated {att['filename']} for {est_id}")
    except Exception as exc:
        print(f'[packet] generation failed for {est_id}: {exc}')
    # Permit packet uses only data locked in at signing — safe to build and
    # push to Den right now (the rep doesn't need to fill anything in later).
    try:
        att = generate_permit_packet(est_id)
        print(f"[permit] generated {att['filename']} for {est_id}")
    except Exception as exc:
        print(f'[permit] generation failed for {est_id}: {exc}')


@app.route('/api/estimates/<est_id>/permit-packet', methods=['POST'])
def regenerate_permit_packet(est_id):
    """Manual (re)generate the permit packet from the Documents tab. Optional
    {\"push_to_crm\": true} — defaults to true because permit info is stable
    at signing (no post-sign fields to wait on)."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    if not est.get('signature'):
        return jsonify({'error': 'The permit packet is generated from the signed contract — '
                                 'this estimate has not been signed yet.'}), 400
    payload = request.get_json(silent=True) or {}
    push = payload.get('push_to_crm')
    if push is None:
        push = True
    try:
        att = generate_permit_packet(est_id, push_to_crm=bool(push))
    except Exception as exc:
        print(f'[permit] manual generation failed for {est_id}: {exc}')
        return jsonify({'error': f'Permit packet generation failed: {exc}'}), 500
    return jsonify({'attachment': att})


@app.route('/api/estimates/<est_id>/production-packet', methods=['POST'])
def regenerate_production_packet(est_id):
    """Manual (re)generate from the Documents tab. Signed estimates only.
    Accepts optional {\"push_to_crm\": true} to also file the fresh packet in
    Base44 — otherwise it stays local (the packet contains post-sign fields
    that aren't ready until the rep fills them in)."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    if not est.get('signature'):
        return jsonify({'error': 'The production packet is generated from the signed '
                                 'contract — this estimate has not been signed yet.'}), 400
    payload = request.get_json(silent=True) or {}
    push = bool(payload.get('push_to_crm'))
    try:
        att = generate_production_packet(est_id, push_to_crm=push)
    except Exception as exc:
        print(f'[packet] manual generation failed for {est_id}: {exc}')
        return jsonify({'error': f'Packet generation failed: {exc}'}), 500
    return jsonify({'attachment': att})


# Fields the rep fills in on the Documents tab AFTER signing — the scheduling
# details the sales rep didn't know at signing time. They live on the estimate
# doc so a packet re-generate always pulls the latest, and they're excluded
# from the signature hash (added after the fact, by design).
# Whitelist — anything not named here is silently dropped by
# _sanitize_work_order, so a new field on the form needs a line here too.
_WORK_ORDER_STR_FIELDS  = ('scheduled_date', 'satellite_dish', 'crew_notes',
                           'height_access', 'hand_load')
_WORK_ORDER_INT_FIELDS  = ('tear_off_layers',)


def _sanitize_work_order(payload):
    out = {}
    for k in _WORK_ORDER_STR_FIELDS:
        v = payload.get(k)
        if v is None:
            continue
        out[k] = str(v).strip()[:400]
    for k in _WORK_ORDER_INT_FIELDS:
        v = payload.get(k)
        if v in (None, ''):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 20:
            out[k] = n
    return out


@app.route('/api/estimates/<est_id>/work-order', methods=['PUT'])
def save_work_order_fields(est_id):
    """Save the post-signing work-order details (scheduled date, satellite
    dish disposition, tear-off layers, crew notes). Never regenerates the
    PDF — the UI calls production-packet after saving when a fresh PDF is
    wanted, so a distracted rep doesn't ship a half-filled packet."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    payload = request.get_json(silent=True) or {}
    cleaned = _sanitize_work_order(payload)

    def _apply(doc):
        if doc is None:
            return None
        wo = dict(doc.get('work_order') or {})
        wo.update(cleaned)
        wo['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        doc['work_order'] = wo
        return doc

    est_update(est_id, _apply)
    return jsonify({'work_order': cleaned})


# ── Roof certificate (realtor labor-only warranty) ───────────────────────────
# Sold standalone during a real-estate transaction: the rep inspects a roof
# Project One did not necessarily install, certifies its condition and
# remaining service life, and backs that with a SHORT, LABOR-ONLY leak-repair
# warranty. Deliberately not a workmanship warranty (there is no install to
# warrant) and not a manufacturer warranty — it covers our labor to chase down
# leaks that appear during the term, and nothing else.
#
# Stored on the estimate doc, like work_order. A certificate job IS an
# estimate whose only artifact is this PDF: that way the customer record, the
# CRM job link, the uploads dir, the Documents tab and the dashboard all work
# on it unchanged. Nothing new had to be introduced to store or find one.
#
# Unlike the production and permit packets this does NOT gate on a signature.
# There is no contract to sign — the certificate is issued, and the rep's
# signature at the bottom is the promise.
_ROOF_CERT_TERMS = (6, 12, 24)

_ROOF_CERT_STR_FIELDS = (
    'inspection_date', 'inspector', 'roof_material', 'roof_age',
    'remaining_life', 'condition', 'findings', 'repairs_made',
    'realtor_name', 'realtor_brokerage', 'realtor_phone', 'realtor_email',
    'buyer_name', 'seller_name', 'closing_date', 'exclusions',
)

# Default exclusions. Editable per certificate — a rep can call out a specific
# pre-existing condition — but this is the language that ships if nobody
# touches it, so it has to stand on its own.
ROOF_CERT_DEFAULT_EXCLUSIONS = (
    'This certificate covers LABOR ONLY to repair roof leaks originating in '
    'the certified roof area during the term shown above. Materials are not '
    'included and are billed separately at cost.\n\n'
    'This certificate does NOT cover: damage from hail, wind, snow or ice '
    'load, fire, lightning, falling limbs, or any other storm or casualty '
    'event; damage caused by foot traffic or by work performed by anyone '
    'other than Project One Roofing; gutters, skylights, chimneys, solar '
    'equipment, swamp coolers, siding, or any component that is not part of '
    'the roof covering; ice damming; condensation or moisture caused by '
    'inadequate attic ventilation; structural movement or deck deterioration '
    'not visible at the time of inspection; and any pre-existing condition '
    'noted in the findings above.\n\n'
    'This is not a manufacturer warranty and does not extend, replace, or '
    'affect any manufacturer warranty on the existing roof. Coverage begins '
    'on the inspection date and ends on the expiration date shown above. '
    'Project One Roofing must be notified of a leak within 30 days of its '
    'discovery and given reasonable access to inspect and repair.'
)


def _roof_cert_number(est):
    """Certificate number. Derived from the estimate id so it is stable across
    regenerations — a realtor holding an emailed copy and the office looking at
    the record are always talking about the same number."""
    eid = est.get('estimate_id', '')
    return 'RC-' + eid.split('-')[0].upper() if eid else 'RC-DRAFT'


def _add_months(d, months):
    """Date `months` after d, clamped to the last day of the target month so an
    inspection on the 31st does not roll into the following month."""
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _roof_cert_dates(cert):
    """(start, expiration) as dates, or (None, None). The term runs from the
    INSPECTION, not from whenever the PDF was generated — regenerating a
    certificate must never quietly extend its coverage."""
    raw = (cert.get('inspection_date') or '').strip()
    if not raw:
        return None, None
    try:
        start = date.fromisoformat(raw[:10])
    except ValueError:
        return None, None
    term = cert.get('term_months')
    if term not in _ROOF_CERT_TERMS:
        return start, None
    return start, _add_months(start, term)


def _sanitize_roof_cert(payload):
    out = {}
    for k in _ROOF_CERT_STR_FIELDS:
        v = payload.get(k)
        if v is None:
            continue
        # Findings/repairs/exclusions are paragraphs; the rest are one-liners.
        cap = 4000 if k in ('findings', 'repairs_made', 'exclusions') else 300
        out[k] = str(v).strip()[:cap]
    term = payload.get('term_months')
    if term not in (None, ''):
        try:
            n = int(term)
        except (TypeError, ValueError):
            n = None
        if n in _ROOF_CERT_TERMS:
            out['term_months'] = n
    price = payload.get('price')
    if price not in (None, ''):
        try:
            p = float(price)
        except (TypeError, ValueError):
            p = None
        if p is not None and 0 <= p <= 100000:
            out['price'] = round(p, 2)
    return out


def build_roof_certificate_pdf(est):
    """One-page roof certification + limited labor warranty, signed by the
    inspecting rep. Written to be handed to a realtor and dropped straight into
    a transaction file, so every fact a title company or buyer would ask about
    (what was inspected, when, by whom, what is covered, what is not, when it
    expires) is on the single page."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c    = est.get('customer', {})
    a    = c.get('address', {})
    cert = est.get('roof_certificate') or {}
    num  = _roof_cert_number(est)
    start, end = _roof_cert_dates(cert)
    term = cert.get('term_months')

    def _fmt(d):
        return d.strftime('%B %d, %Y') if d else '________________'

    pdf = FPDF(orientation='P', unit='mm', format='Letter')
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(14, 14, 14)
    pdf.add_page()
    W = pdf.w - 28

    logo = os.path.join(BASE_DIR, 'static', 'logo.png')
    if os.path.exists(logo):
        try:
            pdf.image(logo, x=14, y=12, h=16)
        except Exception:
            pass
    pdf.set_xy(14, 12)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(W, 5, 'PROJECT ONE ROOFING', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 8)
    pdf.cell(W, 4, '970-776-0945  -  projectoneroofingcolorado.com',
             align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_y(32)

    pdf.set_fill_color(26, 58, 92)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 13)
    pdf.cell(W, 10, '  ROOF CERTIFICATION & LIMITED LABOR WARRANTY',
             fill=True, new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)

    pdf.set_font('Helvetica', '', 8.5)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(W, 6, _pdf_safe(
        f'Certificate {num}    Issued {datetime.utcnow().strftime("%B %d, %Y")}'),
        align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    def section_title(txt):
        pdf.set_font('Helvetica', 'B', 10.5)
        pdf.set_text_color(26, 58, 92)
        pdf.cell(0, 7, _pdf_safe(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)

    # A certificate that runs to a second page reads as a form, not a
    # certificate, so the short facts pair up two to a line. Anything too long
    # for a half column falls back to its own full-width row — a value wrapped
    # inside a 55mm column is worse than one extra line. Both layouts share
    # LAB_W so the full-width rows line up with the columns above them.
    HALF   = W / 2
    LAB_W  = 38
    VAL_W  = HALF - LAB_W

    def kv_row(label, val):
        if not val:
            return
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(LAB_W, 5.5, _pdf_safe(label))
        pdf.set_font('Helvetica', '', 9)
        # new_x is explicit: fpdf2's multi_cell defaults to leaving the cursor
        # at the RIGHT edge of the cell, so without this every row after the
        # first starts at the right margin and wraps off the page.
        pdf.multi_cell(W - LAB_W, 5.5, _pdf_safe(val),
                       new_x='LMARGIN', new_y='NEXT')

    def _half(label, val, x):
        pdf.set_xy(x, pdf.get_y())
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(LAB_W, 5.5, _pdf_safe(label))
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(VAL_W, 5.5, _pdf_oneline(val))

    def kv_grid(pairs):
        pending = None
        for label, val in pairs:
            val = (val or '').strip()
            if not val:
                continue
            pdf.set_font('Helvetica', '', 9)
            if pdf.get_string_width(_pdf_oneline(val)) > VAL_W - 2:
                if pending:
                    _half(*pending, x=14)
                    pdf.ln(5.5)
                    pending = None
                kv_row(label, val)
            elif pending is None:
                pending = (label, val)
            else:
                _half(*pending, x=14)
                _half(label, val, x=14 + HALF)
                pdf.ln(5.5)
                pending = None
        if pending:
            _half(*pending, x=14)
            pdf.ln(5.5)

    def para(txt):
        pdf.set_font('Helvetica', '', 8.4)
        for block in str(txt).split('\n\n'):
            block = ' '.join(block.split())
            if not block:
                continue
            pdf.multi_cell(W, 4.15, _pdf_safe(block))
            pdf.ln(1.2)

    # ── Property ──
    section_title('Property Certified')
    addr = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                   a.get('state'), a.get('zip')]))
    kv_row('Address',         est.get('project_address') or addr)
    kv_row('Owner of Record', c.get('name', ''))
    pdf.ln(2)

    # ── Inspection ──
    section_title('Inspection')
    kv_grid([
        ('Inspection Date',     _fmt(start) if start else ''),
        ('Inspected By',        cert.get('inspector')
                                or (est.get('salesperson') or '').title()),
        ('Approximate Age',     cert.get('roof_age', '')),
        ('Condition',           cert.get('condition', '')),
        ('Est. Remaining Life', cert.get('remaining_life', '')),
        ('Roof Covering',       cert.get('roof_material', '')),
    ])
    if (cert.get('findings') or '').strip():
        pdf.ln(1)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(W, 5, 'Findings', new_x='LMARGIN', new_y='NEXT')
        para(cert['findings'])
    if (cert.get('repairs_made') or '').strip():
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(W, 5, 'Repairs Performed Prior to Certification',
                 new_x='LMARGIN', new_y='NEXT')
        para(cert['repairs_made'])
    pdf.ln(1)

    # ── The grant — the part everyone actually reads ──
    pdf.set_fill_color(240, 245, 250)
    pdf.set_draw_color(26, 58, 92)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(26, 58, 92)
    pdf.cell(W, 7, '  WARRANTY GRANTED', fill=True, border=1,
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 9)
    grant = (
        'Project One Roofing certifies the roof described above and warrants it '
        'against leaks for a period of '
        f'{term if term else "____"} MONTHS, LABOR ONLY, '
        f'from {_fmt(start)} through {_fmt(end)}.'
    )
    pdf.multi_cell(W, 5.2, _pdf_safe(grant), border='LR',
                   new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', 'I', 8.4)
    pdf.multi_cell(W, 4.6, _pdf_safe(
        'Coverage is limited to Project One Roofing labor to locate and repair '
        'leaks in the certified roof area. See exclusions below.'),
        border='LRB', new_x='LMARGIN', new_y='NEXT')
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(3)

    # ── Exclusions ──
    section_title('What This Certificate Does and Does Not Cover')
    para(cert.get('exclusions') or ROOF_CERT_DEFAULT_EXCLUSIONS)

    # ── Transaction / realtor ──
    if any((cert.get(k) or '').strip() for k in
           ('realtor_name', 'realtor_brokerage', 'buyer_name',
            'seller_name', 'closing_date')):
        pdf.ln(1)
        section_title('Real Estate Transaction')
        kv_grid([
            ('Requested By',  cert.get('realtor_name', '')),
            ('Brokerage',     cert.get('realtor_brokerage', '')),
            ('Realtor Phone', cert.get('realtor_phone', '')),
            ('Realtor Email', cert.get('realtor_email', '')),
            ('Buyer',         cert.get('buyer_name', '')),
            ('Seller',        cert.get('seller_name', '')),
            ('Closing Date',  cert.get('closing_date', '')),
        ])

    # ── Signature block ──
    # Keep the whole block together: a signature stranded alone on page 2 makes
    # the certificate look unsigned to anyone skimming page 1. The block needs
    # ~24mm and the break margin is 16mm, so 239 is the last y it still fits.
    if pdf.get_y() > 239:
        pdf.add_page()
    pdf.ln(4)
    inspector = (cert.get('inspector')
                 or (est.get('salesperson') or '').title()
                 or 'Project One Roofing')
    sig_w = W * 0.56
    gap   = 8
    # The rep's name set in a large Times italic over the rule reads as a
    # signature on a certificate. There is no drawn-signature capture on this
    # document by design — it is issued, not negotiated.
    pdf.set_font('Times', 'I', 19)
    pdf.cell(sig_w, 9, _pdf_oneline(inspector))
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(W - sig_w - gap, 9, _pdf_safe(_fmt(start)),
             new_x='LMARGIN', new_y='NEXT')
    rule_y = pdf.get_y()
    pdf.set_draw_color(60, 60, 60)
    pdf.line(14, rule_y, 14 + sig_w, rule_y)
    pdf.line(14 + sig_w + gap, rule_y, 14 + W, rule_y)
    pdf.ln(1)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(sig_w + gap, 4.5,
             _pdf_safe('Authorized Signature, Project One Roofing'))
    pdf.cell(W - sig_w - gap, 4.5, 'Inspection Date',
             new_x='LMARGIN', new_y='NEXT')
    pdf.cell(W, 4.5, _pdf_safe(f'{inspector}  -  Certificate {num}'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)

    out = pdf.output()
    return bytes(out) if not isinstance(out, bytes) else out


def generate_roof_certificate(est_id, push_to_crm=True):
    """Build the certificate PDF, save it as a server-generated attachment
    (swapping any prior one), and optionally file it on the linked CRM job.
    Returns the attachment dict. No signature gate — see the note above."""
    est = est_load(est_id)
    if est is None:
        raise ValueError('estimate not found')

    pdf_bytes = build_roof_certificate_pdf(est)
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    fname = f'roofcert_{uuid.uuid4().hex[:8]}.pdf'
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(pdf_bytes)

    c     = est.get('customer', {})
    cname = (c.get('name') or 'Customer').strip()
    num   = _roof_cert_number(est)
    att = {
        'id':               uuid.uuid4().hex[:12],
        'filename':         f'{est_id}/{fname}',
        'label':            f'Roof Certificate - {cname}',
        'doc_type':         'roof_certificate',
        'show_in_estimate': False,
        'server_generated': True,
        'generated_at':     datetime.utcnow().isoformat() + 'Z',
    }

    def _is_cert(x):
        return x.get('server_generated') and x.get('doc_type') == 'roof_certificate'

    def _swap(doc):
        if doc is None:
            return None
        for old in filter(_is_cert, doc.get('attachments') or []):
            parts = (old.get('filename') or '').split('/')
            if len(parts) == 2 and parts[0] == est_id and _safe_path_id(parts[1]):
                try:
                    os.remove(os.path.join(UPLOADS_DIR, parts[0], parts[1]))
                except OSError:
                    pass
        doc['attachments'] = [x for x in doc.get('attachments') or []
                              if not _is_cert(x)] + [att]
        return doc

    est = est_update(est_id, _swap) or est

    if not push_to_crm:
        return att

    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=f'Roof_Certificate_{num}.pdf',
        hosted_url=f'{_base_url()}/uploads/{est_id}/{fname}',
        doc_name=f'Roof Certificate - {cname} ({num})',
        doc_type='other',
        description='Roof certification with a limited labor-only leak warranty, '
                    'issued for a real-estate transaction.')
    if doc_id:
        def _mark_pushed(doc):
            if doc is None:
                return None
            for x in doc.get('attachments', []):
                if x.get('id') == att['id']:
                    x['crm_document_id'] = doc_id
            return doc
        est_update(est_id, _mark_pushed)
        att['crm_document_id'] = doc_id
    elif err and err != 'not_linked':
        print(f'[roofcert] CRM push failed for {est_id}: {err}')
    return att


@app.route('/api/roof-certificate-defaults', methods=['GET'])
def roof_certificate_defaults():
    """The standard exclusions text and the allowed terms. Served rather than
    duplicated in app.js so the legal language has exactly one home — a rep
    editing the exclusions on one certificate must not be editing a second
    copy that the PDF never reads."""
    return jsonify({'exclusions': ROOF_CERT_DEFAULT_EXCLUSIONS,
                    'terms': list(_ROOF_CERT_TERMS)})


@app.route('/api/estimates/<est_id>/roof-certificate', methods=['PUT'])
def save_roof_certificate_fields(est_id):
    """Save the certificate fields. Never regenerates the PDF — the UI POSTs
    afterwards when a fresh one is wanted, so a half-filled draft is never
    handed to a realtor."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    cleaned = _sanitize_roof_cert(request.get_json(silent=True) or {})

    def _apply(doc):
        if doc is None:
            return None
        rc = dict(doc.get('roof_certificate') or {})
        rc.update(cleaned)
        rc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        doc['roof_certificate'] = rc
        return doc

    est_update(est_id, _apply)
    return jsonify({'roof_certificate': cleaned})


@app.route('/api/estimates/<est_id>/roof-certificate', methods=['POST'])
def regenerate_roof_certificate(est_id):
    """(Re)generate the certificate PDF from the Documents tab. Optional
    {"push_to_crm": true}; defaults to false so the rep can eyeball the PDF
    before it lands in the job file."""
    if not _safe_path_id(est_id):
        return jsonify({'error': 'invalid estimate id'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    cert = est.get('roof_certificate') or {}
    if not (cert.get('inspection_date') or '').strip():
        return jsonify({'error': 'Set the inspection date first — the warranty '
                                 'term runs from it.'}), 400
    if cert.get('term_months') not in _ROOF_CERT_TERMS:
        return jsonify({'error': 'Choose a warranty term (6, 12, or 24 months).'}), 400
    push = bool((request.get_json(silent=True) or {}).get('push_to_crm'))
    try:
        att = generate_roof_certificate(est_id, push_to_crm=push)
    except Exception as exc:
        print(f'[roofcert] generation failed for {est_id}: {exc}')
        return jsonify({'error': f'Certificate generation failed: {exc}'}), 500
    return jsonify({'attachment': att})


# ── Change orders ────────────────────────────────────────────────────────────
# Signed addendums on an accepted estimate. Stored inside the estimate doc but
# SERVER-AUTHORITATIVE: the full-doc PUT strips them, every mutation goes
# through the endpoints below. Totals are computed server-side only with the
# same _sell_price math the GBB engine uses; the rep editor in app.js mirrors
# the formula for its live preview (parity rule, same as app.py:1041).

def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _co_line_total(item, pricing):
    """CO line sell total — price_override wins, else cost through margin/markup.
    Negatives are legal everywhere (credit change orders)."""
    po = item.get('price_override')
    if po is not None and po != '':
        try:
            return float(po)
        except (TypeError, ValueError):
            pass
    cost = _f(item.get('material_unit_cost')) + _f(item.get('labor_unit_cost'))
    return _sell_price(cost, _f(pricing.get('rate')), pricing.get('mode', 'margin')) \
        * _f(item.get('quantity'))


def _co_total(co):
    pricing = co.get('pricing') or {}
    return sum(_co_line_total(it, pricing) for it in co.get('line_items') or [])


def _accepted_co_total(est):
    return sum(_co_total(co) for co in est.get('change_orders') or []
               if co.get('status') == 'accepted')


def _co_view(co):
    """CO as returned to the rep UI — with computed totals attached."""
    out = dict(co)
    pricing = co.get('pricing') or {}
    out['line_totals'] = [round(_co_line_total(it, pricing), 2)
                          for it in co.get('line_items') or []]
    out['total'] = round(_co_total(co), 2)
    return out


def _find_co(est, co_id):
    return next((co for co in est.get('change_orders') or []
                 if co.get('id') == co_id), None)


def _clean_co_items(items):
    clean = []
    for it in (items or [])[:50]:
        if not isinstance(it, dict):
            continue
        po = it.get('price_override')
        row = {
            'name':               str(it.get('name') or '').strip()[:200],
            'description':        str(it.get('description') or '').strip()[:500],
            'quantity':           _f(it.get('quantity')),
            'unit':               str(it.get('unit') or '').strip()[:12],
            'material_unit_cost': _f(it.get('material_unit_cost')),
            'labor_unit_cost':    _f(it.get('labor_unit_cost')),
            'price_override':     None if po in (None, '') else _f(po),
        }
        if row['name']:
            clean.append(row)
    return clean


def _co_request_guard(est_id):
    """Shared auth/lookup for CO endpoints → (est, error_response_or_None)."""
    if not _safe_path_id(est_id):
        return None, (jsonify({'error': 'invalid estimate id'}), 400)
    est = est_load(est_id)
    if est is None:
        return None, (jsonify({'error': 'Not found'}), 404)
    if not _can_touch_estimate(est):
        return None, _forbid()
    return est, None


@app.route('/api/estimates/<est_id>/change-orders', methods=['GET', 'POST'])
def change_orders_collection(est_id):
    est, err = _co_request_guard(est_id)
    if err:
        return err
    if request.method == 'GET':
        return jsonify([_co_view(co) for co in est.get('change_orders') or []])

    if not est.get('signature'):
        return jsonify({'error': 'Change orders can only be added to a signed estimate.'}), 400
    d   = request.get_json(force=True) or {}
    cos = est.setdefault('change_orders', [])
    nums = [int(str(x.get('number', '')).split('-')[-1]) for x in cos
            if str(x.get('number', '')).split('-')[-1].isdigit()]
    # Default rate: what the signed tier actually carried for roofing
    parent_pricing = est.get('pricing') or {}
    tier = _trade_tier(est, 'roofing')   # CO default rate mirrors the signed roofing package
    d_pricing = d.get('pricing') if isinstance(d.get('pricing'), dict) else {}
    now = datetime.utcnow().isoformat() + 'Z'
    co = {
        'id':          uuid.uuid4().hex[:12],
        'number':      f'CO-{(max(nums) + 1) if nums else 1}',
        'title':       str(d.get('title') or '').strip()[:200],
        'description': str(d.get('description') or '').strip()[:4000],
        'line_items':  _clean_co_items(d.get('line_items')),
        'pricing': {'mode': parent_pricing.get('mode', 'margin'),
                    'rate': _f(d_pricing.get('rate'),
                               _tier_rate(parent_pricing, 'roofing', tier))},
        'status':      'draft',
        'share_token': None,
        'created_at':  now,
        'created_by':  _current_user(),
        'sent_at':     None,
        'viewed_at':   None,
        'view_count':  0,
        'signature':   None,
    }
    cos.append(co)
    est['updated_at'] = now
    est_save(est)
    return jsonify(_co_view(co)), 201


@app.route('/api/estimates/<est_id>/change-orders/<co_id>', methods=['PUT', 'DELETE'])
def change_order_item(est_id, co_id):
    est, err = _co_request_guard(est_id)
    if err:
        return err
    co = _find_co(est, co_id)
    if co is None:
        return jsonify({'error': 'Change order not found'}), 404

    if request.method == 'DELETE':
        if co.get('status') == 'accepted':
            return jsonify({'error': 'A signed change order cannot be deleted.'}), 400
        est['change_orders'] = [x for x in est['change_orders'] if x.get('id') != co_id]
        est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        est_save(est)
        return jsonify({'ok': True})

    if co.get('status') != 'draft':
        return jsonify({'error': 'Only draft change orders can be edited — '
                                 'move it back to draft first.'}), 400
    d = request.get_json(force=True) or {}
    co['title']       = str(d.get('title') or '').strip()[:200]
    co['description'] = str(d.get('description') or '').strip()[:4000]
    co['line_items']  = _clean_co_items(d.get('line_items'))
    if isinstance(d.get('pricing'), dict):
        co['pricing']['rate'] = _f(d['pricing'].get('rate'), co['pricing'].get('rate'))
    co['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    est['updated_at'] = co['updated_at']
    est_save(est)
    return jsonify(_co_view(co))


@app.route('/api/estimates/<est_id>/change-orders/<co_id>/share', methods=['POST'])
def change_order_share(est_id, co_id):
    est, err = _co_request_guard(est_id)
    if err:
        return err
    co = _find_co(est, co_id)
    if co is None:
        return jsonify({'error': 'Change order not found'}), 404
    if co.get('status') == 'accepted':
        return jsonify({'error': 'Already signed.'}), 400
    if not co.get('line_items'):
        return jsonify({'error': 'Add at least one line item before sending.'}), 400
    fresh_token = secrets.token_urlsafe(24)

    def _mark_sent(doc):
        co2 = _find_co(doc, co_id) if doc else None
        if co2 is None or co2.get('status') == 'accepted':
            return None  # signed concurrently — don't disturb it
        co2['share_token'] = co2.get('share_token') or fresh_token
        co2['status'] = 'sent'
        if not co2.get('sent_at'):
            co2['sent_at'] = datetime.utcnow().isoformat() + 'Z'
        return doc

    stored = est_update(est_id, _mark_sent)
    if stored is None:
        return jsonify({'error': 'Already signed.'}), 400
    tok = _find_co(stored, co_id)['share_token']
    return jsonify({'token': tok, 'url': f'/sign-co/{tok}',
                    'full_url': f'{_base_url()}/sign-co/{tok}'})


@app.route('/api/estimates/<est_id>/change-orders/<co_id>/status', methods=['POST'])
def change_order_status(est_id, co_id):
    """Rep marks a CO declined, or reverts sent/declined back to draft for
    editing. Reverting rotates the token so the customer's old link dies."""
    est, err = _co_request_guard(est_id)
    if err:
        return err
    co = _find_co(est, co_id)
    if co is None:
        return jsonify({'error': 'Change order not found'}), 404
    status = (request.get_json(force=True) or {}).get('status')
    if status not in ('draft', 'declined'):
        return jsonify({'error': 'Invalid status'}), 400
    if co.get('status') == 'accepted':
        return jsonify({'error': 'A signed change order cannot change status.'}), 400

    def _set_status(doc):
        co2 = _find_co(doc, co_id) if doc else None
        if co2 is None or co2.get('status') == 'accepted':
            return None  # signed while this request was in flight
        co2['status'] = status
        if status == 'draft':
            co2['share_token'] = None   # kill the customer's old link
        doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        return doc

    stored = est_update(est_id, _set_status)
    if stored is None:
        return jsonify({'error': 'A signed change order cannot change status.'}), 400
    return jsonify(_co_view(_find_co(stored, co_id)))


@app.route('/api/estimates/<est_id>/change-orders/<co_id>/send-email', methods=['POST'])
def change_order_email(est_id, co_id):
    est, err = _co_request_guard(est_id)
    if err:
        return err
    co = _find_co(est, co_id)
    if co is None:
        return jsonify({'error': 'Change order not found'}), 404
    if co.get('status') == 'accepted':
        return jsonify({'error': 'Already signed.'}), 400
    if not co.get('line_items'):
        return jsonify({'error': 'Add at least one line item before sending.'}), 400

    body    = request.get_json(silent=True) or {}
    to_addr = (body.get('email') or est.get('customer', {}).get('email') or '').strip()
    if not to_addr or '@' not in to_addr:
        return jsonify({'error': 'No customer email address on this estimate.'}), 400
    base = get_public_url()
    if not base:
        return jsonify({'error': 'No public URL configured — the emailed link would not '
                                 'be reachable. Use Copy Link instead.'}), 400

    if not co.get('share_token'):
        co['share_token'] = secrets.token_urlsafe(24)
    co['status'] = 'sent'
    if not co.get('sent_at'):
        co['sent_at'] = datetime.utcnow().isoformat() + 'Z'
    est_save(est)

    c        = est.get('customer', {})
    first    = (c.get('name') or 'there').split(' ')[0]
    enum     = _est_number(est)
    rep      = _display_name(est.get('salesperson')) if est.get('salesperson') else 'Project One Roofing'
    sign_url = f"{base}/sign-co/{co['share_token']}"
    total    = _co_total(co)
    kind     = 'credit to' if total < 0 else 'change to'

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#1a3a5c;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">Change Order {he(co.get('number', ''))} is Ready</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">Hi {he(first)} — a {kind} your project ({he(enum)}) is ready to review and sign online.</p>
  </div>
  <div style="padding:22px 26px">
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 18px">
      Open the link below to review the change order details and sign electronically.
      Your original contract stays in effect — this only covers the changes described.</p>
    <a href="{he(sign_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:14px 24px;border-radius:6px;font-weight:700;font-size:15px;margin-bottom:18px">
      Review &amp; Sign Change Order →</a>
    <p style="font-size:12px;color:#6b7280;line-height:1.6;margin:0">
      Questions? Just reply to this email or call us at 970-776-0945.<br>
      — {he(rep)}, Project One Roofing</p>
  </div>
</div>
</body></html>'''
    ok = _send_email(f'Change order {co.get("number", "")} for your project — Project One Roofing',
                     html_body, to_addr, cc=_salesperson_email(est) or None)
    if not ok:
        return jsonify({'error': 'Email could not be sent — check the email settings.'}), 502
    return jsonify({'ok': True, 'sent_to': to_addr, 'full_url': sign_url})


@app.route('/api/estimates/<est_id>/change-orders/<co_id>/pdf', methods=['GET'])
def change_order_pdf(est_id, co_id):
    est, err = _co_request_guard(est_id)
    if err:
        return err
    co = _find_co(est, co_id)
    if co is None:
        return jsonify({'error': 'Change order not found'}), 404
    data = build_co_pdf(est, co)
    return send_file(io.BytesIO(data), mimetype='application/pdf',
                     download_name=f"{co.get('number', 'CO')}_{_est_number(est)}.pdf")


def find_by_co_token(token):
    """Return (est, co) for the change order matching share_token, or None.
    Full scan like est_find_by_token — fine at this dataset size."""
    if not token:
        return None
    for est in est_iter():
        for co in est.get('change_orders') or []:
            if co.get('share_token') == token:
                return est, co
    return None


def _fcs(n):
    """Currency with an explicit leading minus for credits: -$1,200.00."""
    return ('-' if n < 0 else '') + fc(abs(n))


def build_co_sign_page(est, co, token):
    """Customer-facing change-order page — unsigned: review + sign form;
    signed: confirmation with the audit trail. Inline CSS (style.css does
    not apply to public pages), same shell as the estimate sign page."""
    c     = est.get('customer', {})
    a     = c.get('address', {})
    addr  = ', '.join(filter(None, [a.get('street'), a.get('city'), a.get('state')]))
    enum  = _est_number(est)
    sig   = co.get('signature')
    total = _co_total(co)
    pricing = co.get('pricing') or {}
    parent_total   = _estimate_total(est)
    prior_co_total = sum(_co_total(x) for x in est.get('change_orders') or []
                         if x.get('status') == 'accepted' and x.get('id') != co.get('id'))
    new_total = parent_total + prior_co_total + total

    rows = ''
    for it in co.get('line_items') or []:
        line = _co_line_total(it, pricing)
        qty  = _f(it.get('quantity'))
        desc = (it.get('description') or '').strip()
        rows += f'''<tr>
          <td class="cvn">{he(it.get('name', ''))}
            {'<div class="cvd">' + he(desc) + '</div>' if desc else ''}</td>
          <td class="cvc" data-l="Qty">{qty:g}</td>
          <td class="cvc">{he(it.get('unit', ''))}</td>
          <td class="cvr" data-l="Total" style="{'color:#b91c1c' if line < 0 else ''}">{_fcs(line)}</td></tr>'''

    desc_html = ''
    if (co.get('description') or '').strip():
        paras = ''.join(f'<p>{he(p.strip())}</p>'
                        for p in co['description'].split('\n\n') if p.strip())
        desc_html = f'<div class="cvnotes"><h3>What&rsquo;s Changing</h3>{paras}</div>'

    is_credit = total < 0
    total_lbl = 'Change Order Credit' if is_credit else 'Change Order Total'
    grand_style = 'background:#b91c1c' if is_credit else ''

    if sig:
        signed_fmt = sig.get('signed_at', '')
        try:
            signed_fmt = datetime.fromisoformat(signed_fmt.replace('Z', '+00:00')) \
                .strftime('%B %d, %Y at %I:%M %p UTC')
        except Exception:
            pass
        action_html = f'''<div class="cert">
      <div class="cert-title">&#9989; Change Order Signed</div>
      <table class="cert-tbl">
        <tr><td>Signed By</td><td>{he(sig.get('name', ''))}</td></tr>
        <tr><td>Signed At</td><td>{he(signed_fmt)}</td></tr>
        <tr><td>IP Address</td><td>{he(sig.get('ip_address', ''))}</td></tr>
        <tr><td>SHA-256</td><td class="mono">{he((sig.get('document_hash') or '')[:32])}&hellip;</td></tr>
      </table>
      <div class="cert-legal">This change order was signed electronically and is legally binding under
      the federal E-SIGN Act (15 U.S.C. &sect; 7001) and the Uniform Electronic Transactions Act.
      All terms &amp; conditions of the original contract remain in effect.</div>
    </div>'''
    else:
        action_html = f'''<div class="cvsig">
      <h2>Sign to Approve</h2>
      <p class="sub">Your electronic signature approves this change order as an addendum to your
        original contract ({he(enum)}). All original terms &amp; conditions remain in effect.</p>
      {_cv_sig_form(_mount_path(f'/sign-co/{he(token)}'),
                    agree_text='I have reviewed this change order and approve the changes and pricing above.',
                    btn_text='&#10003; Approve &mdash; Sign Electronically')}
    </div>'''

    steps_html = ('' if sig else
                  '<div class="cvsteps"><span class="cvstep"><b>1</b>Review the Changes</span>'
                  '<span class="cvstep"><b>2</b>Sign to Approve</span></div>')
    return _cv_head(f'Change Order {he(co.get("number", ""))} &mdash; Project One Roofing') + _cv_header() + f'''
<div class="cvhero{' ok' if sig else ''}">
  <div class="cvhero-brand"{' style="color:#86efac"' if sig else ''}>Project One Roofing</div>
  {'<div class="cv-check">&#10003;</div>' if sig else ''}
  <h1>{'Change Order Approved' if sig else f'Change Order {he(co.get("number", ""))}'}</h1>
  <p>{'Thank you — a copy has been added to your project records.' if sig
      else 'Review the changes below, then sign at the bottom to approve'}</p>
  {steps_html}
</div>

<main class="cvmain">
<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Customer</label><strong>{he(c.get('name', '—'))}</strong></div>
    <div class="cvgi"><label>Change Order</label><strong>{he(co.get('number', ''))}{(' — ' + he(co.get('title', ''))) if co.get('title') else ''}</strong></div>
    <div class="cvgi"><label>Original Contract</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or '—')}</strong></div>
  </div>
</div>

{desc_html}

<div class="cvtrade">
  <div class="cvtrade-hd">Change Order Items</div>
  <table class="cvt"><thead><tr>
    <th>Description</th><th scope="col" class="cvth-c">Qty</th>
    <th scope="col" class="cvth-c">Unit</th><th scope="col" class="cvth-r">Total</th></tr></thead>
  <tbody>{rows}</tbody></table>
</div>

<div class="cvgrand" style="margin-top:14px;{grand_style}">
  <span class="cvgrand-lbl">{total_lbl}</span>
  <span class="cvgrand-amt">{_fcs(total)}</span>
</div>

<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Original Contract</label><strong>{fc(parent_total)}</strong></div>
    <div class="cvgi"><label>Prior Change Orders</label><strong>{_fcs(prior_co_total)}</strong></div>
    <div class="cvgi"><label>This Change Order</label><strong>{_fcs(total)}</strong></div>
    <div class="cvgi"><label>New Contract Total</label><strong style="color:#16a34a">{fc(new_total)}</strong></div>
  </div>
</div>

{action_html}
{_cv_contact_card(est)}
</main>

{'' if sig else _cv_sticky_bar(total_lbl, _fcs(total))}
''' + _cv_footer()


@app.route('/sign-co/<token>', methods=['GET', 'POST'])
def sign_change_order(token):
    found = find_by_co_token(token)
    if not found:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404
    est, co = found

    co_id = co.get('id')

    if request.method == 'POST':
        if co.get('signature'):
            return build_co_sign_page(est, co, token)
        sig_name  = (request.form.get('sig_name') or '').strip()
        sig_email = (request.form.get('sig_email') or '').strip()
        if not sig_name:
            return 'Full name is required.', 400
        client_ip = request.remote_addr
        client_ua = request.headers.get('User-Agent', '')

        def _apply_co_signature(doc):
            if doc is None:
                return None
            co2 = _find_co(doc, co_id)
            # Vanished, already signed, or the rep pulled the link back — abort
            if co2 is None or co2.get('signature') or co2.get('share_token') != token:
                return None
            # Hash the CO (tied to its parent) BEFORE attaching the signature —
            # mirrors customer_sign: the hash covers exactly what was approved.
            content = json.dumps({'estimate_id': doc.get('estimate_id'), 'change_order': co2},
                                 sort_keys=True, separators=(',', ':')).encode('utf-8')
            co2['signature'] = {
                'name':          sig_name,
                'email':         sig_email,
                'signed_at':     datetime.utcnow().isoformat() + 'Z',
                'ip_address':    client_ip,
                'user_agent':    client_ua,
                'document_hash': hashlib.sha256(content).hexdigest(),
                'token':         token,
            }
            co2['status'] = 'accepted'
            doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            return doc

        stored = est_update(est.get('estimate_id'), _apply_co_signature)
        if stored is None:
            found = find_by_co_token(token)
            if not found:
                return ('<h2 style="font-family:sans-serif;padding:40px">This change order '
                        'is no longer available for signing.</h2>', 409)
            est, co = found
            return build_co_sign_page(est, co, token)
        est, co = stored, _find_co(stored, co_id)
        threading.Thread(target=_post_co_sign_pipeline,
                         args=(est.get('estimate_id'), co_id), daemon=True).start()
        return build_co_sign_page(est, co, token)

    if not co.get('signature'):
        try:
            now_iso = datetime.utcnow().isoformat() + 'Z'

            def _track_co_view(doc):
                if doc is None:
                    return None
                co2 = _find_co(doc, co_id)
                if co2 is None:
                    return None
                if not co2.get('viewed_at'):
                    co2['viewed_at'] = now_iso
                co2['view_count'] = int(co2.get('view_count') or 0) + 1
                return doc

            stored = est_update(est.get('estimate_id'), _track_co_view)
            if stored is not None:
                est, co = stored, _find_co(stored, co_id) or co
        except Exception as exc:
            print(f'[co-view-track] failed: {exc}')
    return build_co_sign_page(est, co, token)


def build_co_pdf(est, co):
    """Signed change-order PDF (bytes) — priced lines, contract summary, and
    the signature block. Same visual language as build_signed_pdf."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')
    c    = est.get('customer', {})
    a    = c.get('address', {})
    sig  = co.get('signature') or {}
    enum = _est_number(est)
    pricing = co.get('pricing') or {}
    total   = _co_total(co)

    pdf = FPDF(orientation='P', unit='mm', format='Letter')
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(14, 14, 14)
    pdf.set_title(_pdf_safe(
        f'Change Order {co.get("number", "")} - '
        + (c.get('name') or '') + ' - Project One Roofing'))
    pdf.set_author('Project One Roofing')
    pdf.set_subject(_pdf_safe(
        f'Signed change order to contract {enum}, '
        f'net {fc(total)}, from Project One Roofing.'))
    pdf.set_keywords('change order, roofing, Project One Roofing, signed contract addendum')
    pdf.set_creator('Project One Roofing Estimator')
    pdf.add_page()
    W = pdf.w - 28

    logo = os.path.join(BASE_DIR, 'static', 'logo.png')
    if os.path.exists(logo):
        try:
            pdf.image(logo, x=14, y=12, h=16)
        except Exception:
            pass
    pdf.set_xy(14, 12)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(W, 5, 'PROJECT ONE ROOFING', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 8)
    pdf.cell(W, 4, '970-776-0945  -  projectoneroofingcolorado.com', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_y(32)

    pdf.set_fill_color(26, 58, 92)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 13)
    title = f"CHANGE ORDER {co.get('number', '')}" + ('  -  SIGNED' if sig else '  -  DRAFT')
    pdf.cell(W, 10, f'  {_pdf_safe(title)}', fill=True, new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                       a.get('state'), a.get('zip')]))
    info_rows = [
        ('Customer',          c.get('name', '')),
        ('Address',           addr_str),
        ('Original Contract', enum),
        ('Job #',             c.get('crm_job_number') or ''),
        ('Change Order',      co.get('number', '')),
        ('Title',             co.get('title', '')),
        ('Created',           (co.get('created_at') or '')[:10]),
    ]
    pdf.set_font('Helvetica', '', 9)
    for label, val in info_rows:
        if not val:
            continue
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(40, 5.5, _pdf_safe(label))
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(0, 5.5, _pdf_safe(val), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    desc = (co.get('description') or '').strip()
    if desc:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, "What's Changing", new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 8.5)
        pdf.multi_cell(W, 4.6, _pdf_safe(desc))
        pdf.ln(3)

    def trunc(s, n):
        # _pdf_oneline, not _pdf_safe: these are single-line pdf.cell() values.
        s = _pdf_oneline(s)
        return s if len(s) <= n else s[:n - 1] + '...'

    pdf.set_fill_color(234, 239, 245)
    pdf.set_font('Helvetica', 'B', 8)
    for txt, w, align in [('Description', 98, 'L'), ('Qty', 16, 'R'), ('Unit', 16, 'C'),
                          ('Unit Price', 26, 'R'), ('Total', 26, 'R')]:
        pdf.cell(w, 6.5, txt, border=1, fill=True, align=align)
    pdf.ln()
    pdf.set_font('Helvetica', '', 8)
    for it in co.get('line_items') or []:
        line = _co_line_total(it, pricing)
        qty  = _f(it.get('quantity'))
        unit_price = line / qty if qty else line
        name = it.get('name', '')
        d2 = (it.get('description') or '').strip()
        if d2:
            name = f'{name} - {d2}'
        pdf.cell(98, 6, trunc(name, 66), border=1)
        pdf.cell(16, 6, f'{qty:g}', border=1, align='R')
        pdf.cell(16, 6, trunc(it.get('unit', ''), 8), border=1, align='C')
        pdf.cell(26, 6, _fcs(unit_price), border=1, align='R')
        pdf.cell(26, 6, _fcs(line), border=1, align='R')
        pdf.ln()
    pdf.ln(4)

    if total < 0:
        pdf.set_fill_color(185, 28, 28)
    else:
        pdf.set_fill_color(26, 58, 92)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 11)
    lbl = 'CHANGE ORDER CREDIT' if total < 0 else 'CHANGE ORDER TOTAL'
    pdf.cell(W - 40, 9, f'  {lbl}', fill=True)
    pdf.cell(40, 9, _fcs(total) + '  ', fill=True, align='R')
    pdf.ln(13)
    pdf.set_text_color(0, 0, 0)

    parent_total   = _estimate_total(est)
    prior_co_total = sum(_co_total(x) for x in est.get('change_orders') or []
                         if x.get('status') == 'accepted' and x.get('id') != co.get('id'))
    pdf.set_font('Helvetica', '', 9)
    for label, val in [('Original Contract', fc(parent_total)),
                       ('Prior Change Orders', _fcs(prior_co_total)),
                       ('This Change Order', _fcs(total)),
                       ('New Contract Total', fc(parent_total + prior_co_total + total))]:
        pdf.set_font('Helvetica', 'B' if label.startswith('New') else '', 9)
        pdf.cell(50, 5.5, _pdf_safe(label))
        pdf.cell(0, 5.5, val, new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)

    pdf.set_font('Helvetica', 'I', 7.5)
    pdf.multi_cell(W, 4, _pdf_safe('This change order is an addendum to the original signed '
                                   'contract referenced above. All terms & conditions of the '
                                   'original contract remain in full effect.'))
    pdf.ln(4)

    if sig:
        if pdf.get_y() > pdf.h - 75:
            pdf.add_page()
        signed_fmt = sig.get('signed_at', '')
        try:
            signed_fmt = datetime.fromisoformat(signed_fmt.replace('Z', '+00:00')) \
                .strftime('%B %d, %Y at %I:%M %p UTC')
        except Exception:
            pass
        pdf.set_draw_color(22, 163, 74)
        pdf.set_fill_color(240, 253, 244)
        y0 = pdf.get_y()
        pdf.rect(14, y0, W, 52, style='DF')
        pdf.set_xy(18, y0 + 4)
        pdf.set_text_color(22, 101, 52)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, 'ELECTRONICALLY SIGNED', new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(18)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Helvetica', 'I', 17)
        pdf.cell(0, 10, _pdf_safe(sig.get('name', '')), new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(18)
        pdf.set_font('Helvetica', '', 8)
        for line in [
            f"Signed by: {sig.get('name', '')}"
            + (f"  ({sig.get('email')})" if sig.get('email') else ''),
            f"Date: {signed_fmt}",
            f"IP Address: {sig.get('ip_address', '')}",
            f"Document SHA-256: {(sig.get('document_hash') or '')[:32]}...",
        ]:
            pdf.cell(0, 4.6, _pdf_safe(line), new_x='LMARGIN', new_y='NEXT')
            pdf.set_x(18)
        pdf.set_draw_color(0, 0, 0)

    return bytes(pdf.output())


def send_co_signature_notification(est, co):
    """Email the rep when a customer signs a change order."""
    to_addr = _salesperson_email(est)
    if not to_addr:
        print('[co-notify] No salesperson on estimate — skipping notification')
        return
    c     = est.get('customer', {})
    cname = c.get('name', 'Customer')
    sig   = co.get('signature') or {}
    total = _co_total(co)
    new_total = _estimate_total(est) + _accepted_co_total(est)
    view_url  = f"{_base_url()}/sign-co/{co.get('share_token', '')}"
    clr = '#b91c1c' if total < 0 else '#16a34a'

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#16a34a;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">&#9989; Change Order Signed!</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">{he(cname)} just approved {he(co.get('number', ''))}.</p>
  </div>
  <div style="padding:22px 26px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Change Order</td><td style="padding:5px 0;font-size:13px;font-weight:700">{he(co.get('number', ''))}{(' — ' + he(co.get('title', ''))) if co.get('title') else ''}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Customer</td><td style="padding:5px 0;font-size:13px">{he(cname)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">CO Amount</td><td style="padding:5px 0;font-size:15px;font-weight:800;color:{clr}">{_fcs(total)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">New Contract Total</td><td style="padding:5px 0;font-size:13px;font-weight:700">{fc(new_total)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Signed By</td><td style="padding:5px 0;font-size:13px">{he(sig.get('name', ''))}</td></tr>
    </table>
    <a href="{he(view_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:700;font-size:14px">
      View Signed Change Order →</a>
  </div>
</div>
</body></html>'''
    _send_email(f"✅ {co.get('number', 'CO')} signed — {cname} ({_fcs(total)})",
                html_body, to_addr,
                bcc=os.environ.get('OWNER_NOTIFY_EMAIL', '').strip() or None)


def push_co_to_crm(est_id, co_id):
    """Build the signed CO PDF, store it as a server-generated attachment
    (visible in Documents), and file it on the Base44 job."""
    est = est_load(est_id)
    co  = _find_co(est, co_id) if est else None
    if co is None:
        print(f'[co-crm] {est_id}/{co_id} not found')
        return
    pdf_bytes = build_co_pdf(est, co)
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    fname = f'co_{uuid.uuid4().hex[:8]}.pdf'
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(pdf_bytes)
    att = {
        'id':               uuid.uuid4().hex[:12],
        'filename':         f'{est_id}/{fname}',
        'label':            f"Signed {co.get('number', 'CO')}"
                            + (f" - {co.get('title', '')}" if co.get('title') else ''),
        'doc_type':         'change_order',
        'show_in_estimate': False,
        'server_generated': True,
        'generated_at':     datetime.utcnow().isoformat() + 'Z',
        'change_order_id':  co_id,
    }
    def _attach(doc):
        if doc is None:
            return None
        doc['attachments'] = [x for x in doc.get('attachments') or []
                              if x.get('change_order_id') != co_id] + [att]
        return doc

    est = est_update(est_id, _attach) or est

    cname = (est.get('customer', {}).get('name') or 'Customer').strip()
    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=f"Signed_{co.get('number', 'CO')}_{_est_number(est)}.pdf",
        hosted_url=f'{_base_url()}/uploads/{est_id}/{fname}',
        doc_name=f"Signed Change Order {co.get('number', '')} - {cname}",
        doc_type='change_order',
        description=f"Change order signed electronically by "
                    f"{(co.get('signature') or {}).get('name', '')}. "
                    f"Filed automatically by Estimate Builder.")
    if doc_id:
        def _mark_pushed(doc):
            if doc is None:
                return None
            co2 = _find_co(doc, co_id)
            if co2 is not None:
                co2['crm_document_id'] = doc_id
                co2['crm_pushed_at']   = datetime.utcnow().isoformat() + 'Z'
            for x in doc.get('attachments', []):
                if x.get('id') == att['id']:
                    x['crm_document_id'] = doc_id
            return doc
        est_update(est_id, _mark_pushed)
        print(f'[co-crm] SUCCESS — {co.get("number")} filed on CRM job (doc {doc_id})')
    elif err and err != 'not_linked':
        print(f'[co-crm] push failed for {est_id}/{co_id}: {err}')


def _post_co_sign_pipeline(est_id, co_id):
    """Post-CO-signature background work — one thread, sequential writers."""
    try:
        est = est_load(est_id)
        co  = _find_co(est, co_id) if est else None
        if co is not None:
            send_co_signature_notification(est, co)
    except Exception as exc:
        print(f'[co-notify] failed for {est_id}/{co_id}: {exc}')
    try:
        push_co_to_crm(est_id, co_id)
    except Exception as exc:
        print(f'[co-crm] failed for {est_id}/{co_id}: {exc}')


@app.route('/api/estimates/<est_id>/signed', methods=['GET'])
def view_signed_estimate(est_id):
    """Return the signed confirmation page (printable HTML for PDF download)."""
    est = est_load(est_id)
    if est is None:
        return '<h2 style="font-family:sans-serif;padding:40px">Estimate not found.</h2>', 404
    if not est.get('signature'):
        return ('<h2 style="font-family:sans-serif;padding:40px">'
                'This estimate has not been signed yet.</h2>'), 404
    html = build_signed_confirmation(est)
    return Response(html, mimetype='text/html')


@app.route('/present/<token>')
def present_estimate(token):
    """Tablet presentation mode — slide-by-slide estimate walkthrough."""
    est = est_find_by_token(token)
    if est is None:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404
    if est.get('signature'):
        return build_signed_confirmation(est)
    return build_presentation_view(est, token)


@app.route('/sign/<token>', methods=['GET', 'POST'])
def customer_sign(token):
    est = est_find_by_token(token)
    if est is None:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404

    if request.method == 'POST':
        sig_name      = (request.form.get('sig_name') or '').strip()
        sig_email     = (request.form.get('sig_email') or '').strip()
        selected_tier = (request.form.get('selected_tier') or '').strip()
        shingle_color = (request.form.get('shingle_color') or '').strip()
        siding_color  = (request.form.get('siding_color')  or '').strip()
        if not sig_name:
            return 'Full name is required.', 400

        # Capture per-clause initials in the same order the form rendered them
        init_defs = _visible_initials(est)
        initials_captured = []
        for idx, it in enumerate(init_defs):
            val = (request.form.get(f'initial_{idx}') or '').strip()
            initials_captured.append({'text': it['text'], 'value': val})
        if any(not i['value'] for i in initials_captured):
            return 'Please initial every required item before signing.', 400

        # Require a shingle color when the customer was asked to choose one
        # (only when roofing is part of the job — the form hides it otherwise)
        ss = est.get('shingle_selection') or {}
        if (ss.get('enabled') and _roofing_enabled(est)
                and not (ss.get('chosen') or '').strip() and not shingle_color):
            return 'Please choose a shingle color before signing.', 400
        # Same rule for the siding-color step, when siding is on the job
        sds = est.get('siding_selection') or {}
        if (sds.get('enabled') and _siding_enabled(est)
                and not (sds.get('chosen') or '').strip() and not siding_color):
            return 'Please choose a siding color before signing.', 400

        # Per-product package picks (tier_roofing, tier_siding, …). A page
        # rendered before per-product packages submits only selected_tier —
        # treat that as the pick for every G/B/B product (old semantics).
        tier_picks = {}
        for tk in _gbb_trade_keys(est):
            v = (request.form.get(f'tier_{tk}') or '').strip()
            if v not in ('good', 'better', 'best'):
                v = selected_tier if selected_tier in ('good', 'better', 'best') else ''
            if v:
                tier_picks[tk] = v

        # A stale sign page can submit a package the rep has since toggled
        # off — make the customer refresh and choose from what's offered now.
        te_now = est.get('tiers_enabled') or {}
        stale = [t for t in list(tier_picks.values()) + [selected_tier]
                 if t in ('good', 'better', 'best') and te_now.get(t, True) is False]
        if stale:
            return ('That package is no longer offered on this estimate — '
                    'please refresh the page and choose again.'), 409

        # Apply + sign atomically against the FRESH doc (a rep may have saved
        # since this page loaded; the hash must cover exactly what's stored).
        client_ip = request.remote_addr
        client_ua = request.headers.get('User-Agent', '')

        def _apply_signature(doc):
            if doc is None or doc.get('signature'):
                return None  # deleted, or a concurrent sign won — keep theirs
            # Record each product's package pick. A stale sign page could
            # submit a package the rep has since toggled off (tiers_enabled)
            # — never record a disabled tier.
            te_doc = doc.get('tiers_enabled') or {}
            picks  = {}
            for tk in _gbb_trade_keys(doc):
                v = tier_picks.get(tk)
                if v and te_doc.get(v, True) is not False:
                    picks[tk] = v
                    td = (doc.get('trades') or {}).get(tk)
                    if isinstance(td, dict):
                        td['selected_tier'] = v
            if picks:
                doc['selected_tiers'] = picks
                # First product's pick doubles as the legacy single tier
                doc['selected_tier'] = picks[next(iter(picks))]
            elif (selected_tier in ('good', 'better', 'best')
                    and te_doc.get(selected_tier, True) is not False):
                doc['selected_tier'] = selected_tier
            # Chosen shingle color becomes part of the hashed document
            if shingle_color:
                doc.setdefault('shingle_selection', {})['chosen'] = shingle_color
                roof = doc.get('trades', {}).get('roofing')
                if isinstance(roof, dict):
                    roof.setdefault('colors', {})['shingle_color'] = shingle_color
            # Same for the siding color pick — save to both the selection
            # blob (source of truth for the signing UI) and the siding trade's
            # colors dict (source for print rows and the packet).
            if siding_color:
                doc.setdefault('siding_selection', {})['chosen'] = siding_color
                side = doc.get('trades', {}).get('siding')
                if isinstance(side, dict):
                    side.setdefault('colors', {})['siding_color'] = siding_color
            # Hash BEFORE adding the signature so it represents what was signed
            content  = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode('utf-8')
            doc['signature'] = {
                'name':          sig_name,
                'email':         sig_email,
                'signed_at':     datetime.utcnow().isoformat() + 'Z',
                'ip_address':    client_ip,
                'user_agent':    client_ua,
                'document_hash': hashlib.sha256(content).hexdigest(),
                'token':         token,
                'selected_tier': doc.get('selected_tier', 'better'),
                'selected_tiers': doc.get('selected_tiers') or {},
                'shingle_color': shingle_color or (ss.get('chosen') or '').strip(),
                'siding_color':  siding_color  or (sds.get('chosen') or '').strip(),
                'initials':      initials_captured,
            }
            doc['status']     = 'accepted'
            doc['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            return doc

        stored = est_update(est.get('estimate_id'), _apply_signature)
        if stored is None:
            # Someone signed (or deleted) it in the meantime — show current state
            est = est_load(est.get('estimate_id')) or est
            return build_signed_confirmation(est) if est.get('signature') else \
                ('<h2 style="font-family:sans-serif;padding:40px">This estimate is no '
                 'longer available for signing.</h2>', 409)
        est = stored
        # The funnel gets the signature before any of the background work below:
        # it is a single local write, and it is what moves the CRM lead to Won
        # and hands the job to The Den. Recording it inline means a slow SMTP or
        # Base44 cannot leave the pipeline showing a deal that is already closed.
        _funnel_record(est, 'signed',
                       at=(est.get('signature') or {}).get('signed_at') or '')
        # Signature is saved above — everything below is best-effort. Run the rep
        # notification and the CRM/packet pipeline in background threads so a slow
        # or unreachable SMTP/CRM endpoint can never block (or 500) the customer's
        # signing request. The customer always gets their confirmation instantly.
        # (Contract push + packet generation share ONE thread — both write the
        # estimate doc, and sequencing them avoids a lost-update race.)
        threading.Thread(target=send_signature_notification,
                         args=(est,), daemon=True).start()
        threading.Thread(target=_post_sign_pipeline,
                         args=(est.get('estimate_id'),),
                         name=POST_SIGN_THREAD, daemon=True).start()
        return build_signed_confirmation(est)

    # Already signed — show the confirmation instead of the form
    if est.get('signature'):
        return build_signed_confirmation(est)

    # A logged-in team member opening the link is a preview, not a customer
    # view — don't skew the viewed/not-viewed analytics or email the rep.
    if session.get('user'):
        return build_customer_view(est, token)

    # Record the customer view; notify the rep on the first one
    try:
        now_iso    = datetime.utcnow().isoformat() + 'Z'
        first_view = [False]

        def _track(doc):
            if doc is None:
                return None
            first_view[0] = not doc.get('first_viewed_at')
            if first_view[0]:
                doc['first_viewed_at'] = now_iso
            doc['last_viewed_at'] = now_iso
            doc['view_count']     = int(doc.get('view_count') or 0) + 1
            return doc

        est = est_update(est.get('estimate_id'), _track) or est
        if first_view[0]:
            _funnel_record(est, 'viewed', at=now_iso)
            threading.Thread(target=send_view_notification, args=(est,), daemon=True).start()
    except Exception as exc:
        print(f'[view-track] failed: {exc}')

    return build_customer_view(est, token)


@app.route('/sign/<token>/download.pdf')
def customer_download_pdf(token):
    """Public PDF download of an estimate — customer can save/share/upload
    before deciding to sign. Reuses the signed-PDF renderer with signed=False,
    which swaps the title to ESTIMATE, skips the E-SIGN certificate, and adds
    an 'unsigned preview' notice at the tail.

    If the estimate is already signed, the customer gets the signed contract
    (same document the rep and CRM already have) rather than a stale preview."""
    est = est_find_by_token(token)
    if est is None:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404
    if FPDF is None:
        return '<h2 style="font-family:sans-serif;padding:40px">PDF generation is not available on this server.</h2>', 500
    try:
        already_signed = bool(est.get('signature'))
        pdf_bytes = build_signed_pdf(est, signed=already_signed)
    except Exception as exc:
        print(f'[customer-download] failed for est {est.get("estimate_id")}: {exc}')
        return '<h2 style="font-family:sans-serif;padding:40px">Sorry — we couldn\'t build the PDF. Please try again in a moment.</h2>', 500

    enum = _est_number(est)
    label = 'Contract' if already_signed else 'Estimate'
    fname = f'ProjectOneRoofing-{label}-{enum}.pdf'
    resp = make_response(pdf_bytes)
    resp.headers['Content-Type']        = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    resp.headers['Cache-Control']       = 'private, no-store'
    return resp


# ── Templates ──────────────────────────────────────────────────────────────

TEMPLATES = {
    "roofing": [
        {"name": "Shingles", "unit": "SQ", "measure": "squares_waste",
         "desc_good":   "3-Tab",
         "desc_better": "Architectural",
         "desc_best":   "Designer / Premium",
         "notes_good":   "3-Tab asphalt shingles provide reliable, code-compliant leak protection at a competitive price point. Clean, classic look with a 25-year manufacturer limited warranty.",
         "notes_better": "Architectural laminate shingles add dimensional shadow lines and a high-end appearance. Enhanced wind resistance rated up to 130 mph. Lifetime limited warranty — the most popular choice for long-term value.",
         "notes_best":   "Premium designer shingles replicate the look of natural slate or cedar shake with superior impact resistance. Class 4 impact rating may qualify your homeowner for an insurance premium discount. Lifetime limited warranty."},
        {"name": "Synthetic Underlayment", "unit": "SQ", "measure": "squares_waste",
         "desc_good":   "Standard Felt",
         "desc_better": "Synthetic",
         "desc_best":   "Premium Synthetic",
         "notes_good":   "Standard 15# felt paper provides a reliable moisture barrier during installation.",
         "notes_better": "Synthetic underlayment is 4× stronger than felt with superior tear resistance and moisture protection. Rated for 6-month UV exposure if left exposed — built for Colorado's unpredictable weather.",
         "notes_best":   "Premium synthetic underlayment with integrated self-sealing nail strips for maximum protection. Virtually eliminates fastener-driven moisture intrusion."},
        {"name": "Ice & Water Shield", "unit": "SQ", "measure": "eave_valley",
         "desc_good":   "Eaves & Valleys",
         "desc_better": "Eaves, Valleys & Penetrations",
         "desc_best":   "Full Deck Coverage",
         "notes_good":   "Self-adhering waterproof membrane installed at eaves and valleys — the minimum protection required for Colorado's freeze-thaw climate.",
         "notes_better": "Ice & water barrier installed at all eaves, rakes, valleys, and pipe penetrations. Protects the areas most vulnerable to ice damming and wind-driven rain infiltration.",
         "notes_best":   "Full-coverage ice & water barrier across the entire roof deck — the gold standard for hail country. Provides maximum protection regardless of weather severity."},
        {"name": "Rolled Roofing (Low Slope)", "unit": "SQ", "measure": "low_slope_waste",
         "desc_good":   "Mineral-Surfaced Rolled Roofing",
         "desc_better": "Self-Adhered Modified Bitumen",
         "desc_best":   "2-Layer Modified Bitumen System",
         "notes_good":   "Mineral-surfaced rolled roofing installed on all low-slope sections (2/12 pitch or less) where shingles cannot be warranted.",
         "notes_better": "Self-adhered modified bitumen membrane on all low-slope sections — a fully bonded, watertight system engineered for pitches too shallow for shingles.",
         "notes_best":   "Two-layer modified bitumen system (base + granulated cap sheet) on all low-slope sections for maximum durability and a finished look that complements your shingle roof."},
        {"name": "Steep Pitch Charge (7/12+)", "unit": "SQ", "measure": "steep",
         "desc_good":   "Steep-Slope Labor",
         "desc_better": "Steep-Slope Labor & Safety Equipment",
         "desc_best":   "Steep-Slope Labor & Safety Equipment",
         "notes_good":   "Additional labor for roof sections at 7/12 pitch or steeper.",
         "notes_better": "Additional labor and fall-protection equipment for roof sections at 7/12 pitch or steeper — steep roofs require slower, safer installation practices.",
         "notes_best":   "Additional labor and fall-protection equipment for roof sections at 7/12 pitch or steeper — steep roofs require slower, safer installation practices."},
        {"name": 'Decking (OSB 7/16")', "unit": "SQ",
         "desc_good":   "Replace Damaged Only",
         "desc_better": "Replace Damaged + Inspection",
         "desc_best":   "Full Inspection + Replace as Needed",
         "notes_good":   "7/16\" OSB panels replaced only where structurally compromised.",
         "notes_better": "7/16\" OSB structural sheathing replaced in all deteriorated sections discovered during tear-off. Full deck inspection ensures a solid nailing base for all roofing components.",
         "notes_best":   "Comprehensive deck inspection with replacement of all questionable panels. Nail protrusion check performed before installation to maximize shingle performance and warranty validity."},
        {"name": "Drip Edge", "unit": "LF", "measure": "eave_rake",
         "desc_good":   "Galvanized Steel",
         "desc_better": "Pre-finished Galvanized Steel",
         "desc_best":   "Heavy-Gauge Aluminum",
         "notes_good":   "Standard galvanized steel drip edge installed at eaves and rakes.",
         "notes_better": "Pre-finished galvanized steel drip edge installed at all eaves and rakes per manufacturer specs — directs water cleanly away from fascia and prevents wood rot.",
         "notes_best":   "Heavy-gauge pre-painted aluminum drip edge color-matched to your shingles for a sharp, finished appearance with maximum longevity."},
        {"name": "Ridge Cap", "unit": "LF", "measure": "ridge_hip",
         "desc_good":   "Cut-Shingle Ridge",
         "desc_better": "Pre-formed Hip & Ridge Cap",
         "desc_best":   "High-Definition Ridge Cap",
         "notes_good":   "Cut-shingle ridge cap provides a watertight seal at all peaks.",
         "notes_better": "Pre-formed hip and ridge cap shingles installed at all peaks and hips — a finished, professional look with superior wind resistance.",
         "notes_best":   "High-definition ridge cap with 4-layer construction delivers a bold architectural profile and enhanced ventilation at the ridge — the crown jewel of a premium installation."},
        {"name": "Starter Strip", "unit": "LF", "measure": "eave_rake",
         "desc_good":   "Standard Starter Strip",
         "desc_better": "Self-Sealing Starter Strip",
         "desc_best":   "Extended Self-Sealing Starter",
         "notes_good":   "Starter strip installed along eaves to seal the first course of shingles.",
         "notes_better": "Self-sealing starter strip installed at all eaves and rakes — seals the shingle edge and is a critical defense against wind uplift and blow-off.",
         "notes_best":   "Extended-width self-sealing starter strip with reinforced sealant bead provides maximum wind resistance — recommended for Colorado's high-wind regions."},
        {"name": "Pipe Boots", "unit": "EA", "measure": "pipe_boots",
         "desc_good":   "Standard Rubber Boots",
         "desc_better": "Rubber Boots + Aluminum Flashing",
         "desc_best":   "Premium Metal Boots",
         "notes_good":   "Standard rubber pipe boots seal all plumbing penetrations.",
         "notes_better": "Flexible rubber pipe boots with galvanized aluminum sleeves installed at every plumbing penetration — one of the most common leak points on any roof, done right.",
         "notes_best":   "Premium lead-flashed or heavy-gauge metal pipe boots — maximum lifespan and a weather-tight seal guaranteed at every penetration."},
        {"name": "Skylight Flashing", "unit": "EA", "measure": "skylights",
         "desc_good":   "Step & Counter Flashing",
         "desc_better": "Full Flashing Kit",
         "desc_best":   "Custom Fabricated Flashing",
         "notes_good":   "Step flashing and counter flashing installed at all skylights.",
         "notes_better": "Complete step, counter, and saddle flashing kit at all skylights — properly integrated with the roofing system to prevent leaks at this critical junction.",
         "notes_best":   "Custom-fabricated copper or heavy-gauge aluminum flashing at all skylights — the premium solution engineered for decades of leak-free performance."},
        {"name": "Step / Wall Flashing", "unit": "LF", "measure": "step",
         "desc_good":   "Aluminum Step Flashing",
         "desc_better": "Step + Counter Flashing",
         "desc_best":   "Copper / Stainless Flashing",
         "notes_good":   "Aluminum step flashing at all wall-to-roof junctions.",
         "notes_better": "Step flashing and counter flashing at all vertical wall transitions — properly integrated with housewrap and siding to manage water at every joint.",
         "notes_best":   "Copper or stainless step and counter flashing — the highest-performing solution for permanent, maintenance-free water management at all wall transitions."},
        {"name": "Tear-Off Labor", "unit": "SQ", "measure": "squares",
         "desc_good":   "Single Layer Tear-Off",
         "desc_better": "Full Tear-Off & Deck Inspection",
         "desc_best":   "Full Tear-Off + Nail Check",
         "notes_good":   "Removal and disposal of one layer of existing roofing materials.",
         "notes_better": "Complete removal and disposal of all existing roofing layers. Full deck inspection performed before installation begins — we find problems before they become your problem.",
         "notes_best":   "Full tear-off with detailed deck inspection, nail protrusion check across entire deck, and documentation of all replaced materials for your records."},
        {"name": "Install Labor", "unit": "SQ", "measure": "squares",
         "desc_good":   "Certified Crew Installation",
         "desc_better": "Factory-Certified Installation",
         "desc_best":   "Master Installer — Certified",
         "notes_good":   "Professional installation by our experienced, certified crew.",
         "notes_better": "Factory-certified professional installation following all manufacturer specifications — required to preserve the full manufacturer's warranty. Our crew lead brings 10+ years of roofing experience.",
         "notes_best":   "Master-installer-led crew with factory certification and documented installation photos provided for warranty registration. The peace of mind that comes with the best."},
        {"name": "Dumpster", "unit": "LS",
         "desc_good":   "Standard Dumpster Service",
         "desc_better": "Full-Service + Magnetic Sweep",
         "desc_best":   "Premium Cleanup Package",
         "notes_good":   "Dumpster for debris removal and off-site disposal.",
         "notes_better": "Full-service dumpster rental with on-site debris management. Magnetic sweep of driveway and lawn performed after job completion to collect nails and fasteners.",
         "notes_best":   "Premium cleanup — same-day debris removal, three-pass magnetic nail sweep, gutter check, and a final walkthrough with the homeowner before we leave the job site."},
        {"name": "Permit", "unit": "LS",
         "desc_good":   "Building Permit",
         "desc_better": "Permit + Inspection",
         "desc_best":   "Full Permit Management",
         "notes_good":   "Required local building permit obtained by Project One Roofing.",
         "notes_better": "All required local building permits pulled by Project One Roofing. Final inspection scheduled and passed — fully documented before project closeout.",
         "notes_best":   "Complete permit management — permit pulled, inspection scheduled and passed, full documentation package provided to homeowner for personal records and future property disclosure."},
        # Ventilation upgrades — added on demand from the Scope-page ventilation
        # panel, never auto-built (is_default False). Ridge vent qty is code-driven
        # (measure ridge_vent_code) and priced per 4-ft stick (bundle_lf 4).
        {"name": "Ridge Vent", "unit": "LF", "measure": "ridge_vent_code",
         "bundle_lf": 4, "bundle_unit": "sticks", "is_default": False,
         "desc_good":   "Externally-Baffled Ridge Vent",
         "desc_better": "High-Profile Ridge Vent",
         "desc_best":   "Premium Shingle-Over Ridge Vent",
         "notes_good":   "Continuous ridge vent installed at the peak to exhaust hot, moist attic air — sized to meet code net-free-area when balanced with intake at the eaves.",
         "notes_better": "High-profile externally-baffled ridge vent resists wind-driven rain and snow infiltration while providing continuous, even exhaust across the entire ridge line.",
         "notes_best":   "Premium shingle-over ridge vent with an external weather baffle and a finished, low-profile appearance — the best-performing balanced-exhaust solution for a long roof life."},
        {"name": "Vent Plug", "unit": "EA", "measure": "turtle_vents",
         "is_default": False,
         "desc_good":   "Remove & Deck-Over Existing Box Vents",
         "desc_better": "Remove & Deck-Over Existing Box Vents",
         "desc_best":   "Remove & Deck-Over Existing Box Vents",
         "notes_good":   "Existing turtle/box vents are removed and the deck patched and shingled over so the new ridge vent draws evenly instead of short-circuiting through the old openings.",
         "notes_better": "Existing turtle/box vents are removed and the deck patched and shingled over so the new ridge vent draws evenly instead of short-circuiting through the old openings.",
         "notes_best":   "Existing turtle/box vents are removed and the deck patched and shingled over so the new ridge vent draws evenly instead of short-circuiting through the old openings."},
        {"name": "Intake Vent", "unit": "LF", "measure": "eave",
         "is_default": False,
         "desc_good":   "Continuous Soffit Intake Vent",
         "desc_better": "Vented Soffit + Baffles",
         "desc_best":   "Continuous Intake w/ Insulation Baffles",
         "notes_good":   "Continuous soffit intake vent installed along the eaves — the low-side intake that makes ridge exhaust actually work and keeps the attic balanced.",
         "notes_better": "Vented soffit with insulation baffles keeps the intake path clear at every rafter bay so airflow isn't blocked by attic insulation.",
         "notes_best":   "Continuous eave intake paired with insulation baffles at every bay for maximum, unobstructed intake — a fully balanced, code-compliant ventilation system."},
    ],
    "siding": [
        {"name": "Vinyl Siding", "unit": "SQ", "measure": "siding_squares_waste",
         "desc_good": "Economy Vinyl", "desc_better": "Premium Vinyl", "desc_best": "Engineered Wood / Fiber Cement",
         "notes_good": "Economy-grade vinyl siding provides durable, low-maintenance protection at an accessible price point.",
         "notes_better": "Premium vinyl siding with thicker wall construction, deeper shadow lines, and a wider color palette. Resists fading and impact for decades with zero maintenance.",
         "notes_best": "Engineered wood or fiber cement siding offers the natural look of real wood with dramatically superior durability and fire resistance. The premium choice for lasting curb appeal."},
         # The per-tier variant menus that used to live here moved into
         # SIDING_CATALOG_SEED/SIDING_BUNDLES_SEED — siding is bundle-driven now,
         # same as roofing. Windows/gutters/other still use variants_<tier>.
        {"name": "House Wrap", "unit": "SQ", "measure": "siding_squares_waste",
         "desc_good": "Standard WRB", "desc_better": "Premium WRB", "desc_best": "Fully Adhered WRB",
         "notes_good": "Standard weather-resistant barrier installed under siding.",
         "notes_better": "Premium weather-resistant barrier with enhanced moisture management and air sealing properties — keeps your home dry and energy-efficient.",
         "notes_best": "Fully adhered self-sealing weather-resistant barrier — the ultimate moisture and air barrier for maximum energy performance and water protection."},
        {"name": "Trim Board", "unit": "LF", "desc_good": "Vinyl Trim", "desc_better": "PVC Trim", "desc_best": "Premium PVC / Composite",
         "notes_good": "Vinyl trim boards at corners, windows, and doors.", "notes_better": "Cellular PVC trim — rot-proof, paint-ready, and dimensionally stable for a crisp, lasting finish.", "notes_best": "Premium composite trim for the highest-quality appearance and zero maintenance."},
        {"name": "J-Channel", "unit": "LF", "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Standard",
         "notes_good": "J-channel at all window and door openings.", "notes_better": "J-channel at all window and door openings, properly lapped for water drainage.", "notes_best": "J-channel at all openings with additional caulking and flashing integration for maximum weather protection."},
        {"name": "Soffit", "unit": "SQ", "desc_good": "Solid Vinyl", "desc_better": "Vented Vinyl", "desc_best": "Aluminum / Premium Vented",
         "notes_good": "Solid vinyl soffit panels.", "notes_better": "Vented vinyl soffit promotes attic airflow, reduces moisture buildup, and protects eaves from pests and weather.", "notes_best": "Premium vented aluminum soffit for superior durability and enhanced attic ventilation."},
        {"name": "Fascia", "unit": "LF", "desc_good": "Vinyl Fascia Cover", "desc_better": "PVC Fascia", "desc_best": "Aluminum / Composite Fascia",
         "notes_good": "Vinyl fascia cover over existing wood.", "notes_better": "PVC fascia board — fully rot-proof replacement that provides a clean, finished edge and a solid gutter attachment point.", "notes_best": "Aluminum or composite fascia for maximum longevity and the cleanest appearance."},
        {"name": "Corner Posts", "unit": "EA", "desc_good": "Standard Posts", "desc_better": "Premium Posts", "desc_best": "Premium Posts",
         "notes_good": "Standard vinyl corner posts.", "notes_better": "Premium vinyl corner posts with built-in J-channel for a seamless, finished appearance.", "notes_best": "Heavy-gauge corner posts for maximum durability and a sharp architectural corner."},
        {"name": "Tear-Off Labor", "unit": "SQ", "measure": "siding_squares", "desc_good": "Remove Old Siding", "desc_better": "Remove + Inspect Sheathing", "desc_best": "Remove + Full Inspection",
         "notes_good": "Removal and disposal of existing siding.", "notes_better": "Complete removal of existing siding with sheathing inspection for rot and damage before new installation.", "notes_best": "Full removal with comprehensive sheathing inspection and documentation. All problem areas identified and reported before new materials are installed."},
        {"name": "Install Labor", "unit": "SQ", "measure": "siding_squares", "desc_good": "Professional Installation", "desc_better": "Certified Installation", "desc_best": "Master Installer",
         "notes_good": "Professional siding installation by our experienced crew.", "notes_better": "Factory-trained siding installers following manufacturer best practices for maximum warranty coverage.", "notes_best": "Master installer-led team delivering precise, detail-oriented workmanship documented with completion photos."},
        {"name": "Dumpster", "unit": "LS", "desc_good": "Dumpster Service", "desc_better": "Full Cleanup", "desc_best": "Premium Cleanup",
         "notes_good": "Dumpster for debris removal.", "notes_better": "Full-service cleanup with debris hauled off-site and site broom-swept upon completion.", "notes_best": "Premium cleanup package — complete debris removal and a homeowner walkthrough before we leave the property."},
        {"name": "Permit", "unit": "LS", "desc_good": "Building Permit", "desc_better": "Permit + Inspection", "desc_best": "Full Permit Management",
         "notes_good": "Required building permit.", "notes_better": "All required permits pulled and final inspection scheduled.", "notes_best": "Complete permit management with all documentation provided to homeowner."},
    ],
    "windows": [
        {"name": "Window Unit", "unit": "EA", "measure": "windows",
         "desc_good": "Double-Pane Vinyl", "desc_better": "Double-Pane Low-E", "desc_best": "Triple-Pane Low-E",
         "notes_good": "Double-pane vinyl window — reliable energy performance and low maintenance.",
         "notes_better": "Double-pane Low-E coated window with argon gas fill — significantly reduces heat transfer, UV fading, and outside noise. Energy Star certified.",
         "notes_best": "Triple-pane Low-E window with krypton gas fill — the highest energy performance available. Superior sound reduction and maximum insulation value for Colorado's climate extremes."},
        {"name": "Window Trim Kit", "unit": "EA", "measure": "windows",
         "desc_good": "Standard Trim", "desc_better": "PVC Trim Kit", "desc_best": "Premium Composite Trim",
         "notes_good": "Standard exterior trim kit for a finished appearance.", "notes_better": "PVC exterior trim kit — rot-proof, clean finish that protects the window rough opening for decades.", "notes_best": "Premium composite trim kit for the most refined exterior appearance and maximum longevity."},
        {"name": "Exterior Casing", "unit": "LF",
         "desc_good": "Standard Casing", "desc_better": "PVC Casing", "desc_best": "Premium Composite",
         "notes_good": "Exterior window casing and flashing.", "notes_better": "PVC exterior casing with proper flashing integration — rot-proof and maintenance-free.", "notes_best": "Premium composite casing with full flashing tape system for the ultimate weather protection."},
        {"name": "Install Labor", "unit": "EA", "measure": "windows",
         "desc_good": "Standard Install", "desc_better": "Certified Install", "desc_best": "Master Install",
         "notes_good": "Professional window installation by our trained crew.", "notes_better": "Certified window installation with proper flashing, insulation, and air sealing per manufacturer specs.", "notes_best": "Master installer ensures each window is perfectly level, plumb, and square with full foam insulation and documented completion."},
        {"name": "Permit", "unit": "LS",
         "desc_good": "Permit", "desc_better": "Permit + Inspection", "desc_best": "Full Permit Management",
         "notes_good": "Required building permit.", "notes_better": "All required permits pulled and inspection coordinated.", "notes_best": "Complete permit management with documentation provided to homeowner."},
    ],
    "gutters": [
        {"name": '5" K-Style Gutter', "unit": "LF", "measure": "gutter",
         "desc_good": '5" Aluminum', "desc_better": '5" Heavy-Gauge Aluminum', "desc_best": '5" Copper / Steel',
         "notes_good": 'Standard 5" K-style aluminum gutter — the most common residential gutter size, handles typical rainfall volume.',
         "notes_better": 'Heavy-gauge 5" K-style aluminum gutter — thicker walls resist denting, maintain shape, and last significantly longer than standard-gauge gutters.',
         "notes_best": 'Premium copper or galvanized steel 5" K-style gutter — the most durable and visually striking option, engineered for a lifetime of performance.'},
        {"name": '6" K-Style Gutter', "unit": "LF", "measure": "gutter",
         "desc_good": '6" Aluminum', "desc_better": '6" Heavy-Gauge Aluminum', "desc_best": '6" Copper / Steel',
         "notes_good": '6" K-style aluminum gutter — larger capacity for steep-pitch roofs or high-rainfall areas.',
         "notes_better": '6" heavy-gauge aluminum gutter — maximum capacity with superior durability. Recommended for complex rooflines and higher-elevation homes.',
         "notes_best": '6" copper or galvanized steel gutter — the premium choice for maximum capacity and lasting beauty.'},
        {"name": "Downspout", "unit": "LF", "measure": "downspout",
         "desc_good": "Standard", "desc_better": "Heavy-Gauge", "desc_best": "Copper / Steel",
         "notes_good": "Standard aluminum downspout directs water away from the foundation.", "notes_better": "Heavy-gauge aluminum downspout — resists denting and damage from ladders and yard equipment.", "notes_best": "Copper or galvanized steel downspout — maximum durability and visual impact."},
        {"name": "Gutter Guard / Screen", "unit": "LF", "measure": "gutter",
         "desc_good": "Mesh Screen", "desc_better": "Micro-Mesh Guard", "desc_best": "Premium Micro-Mesh",
         "notes_good": "Aluminum mesh screens keep large debris out of gutters and reduce cleaning frequency.", "notes_better": "Micro-mesh gutter guards block even small debris like pine needles and shingle grit while allowing full water flow. Dramatically reduces maintenance.", "notes_best": "Premium micro-mesh guards with stainless steel mesh — the most effective debris protection available, backed by a no-clog guarantee."},
        {"name": "End Caps", "unit": "EA",
         "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Copper / Matching",
         "notes_good": "End caps seal all gutter runs.", "notes_better": "Sealed end caps at all gutter terminations.", "notes_best": "Color-matched or copper end caps for a cohesive, finished appearance."},
        {"name": "Drop Outlets", "unit": "EA",
         "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Heavy-Gauge",
         "notes_good": "Drop outlets connecting gutters to downspouts.", "notes_better": "Properly positioned drop outlets for optimized water flow and drainage.", "notes_best": "Heavy-gauge drop outlets with sealed connections for maximum longevity."},
        {"name": "Remove Old Gutters", "unit": "LF", "measure": "gutter",
         "desc_good": "Remove & Haul", "desc_better": "Remove & Haul", "desc_best": "Remove & Haul",
         "notes_good": "Removal and disposal of existing gutter system.", "notes_better": "Complete removal of old gutters with inspection of fascia board condition before new installation.", "notes_best": "Full removal with fascia board inspection and documentation of any rot or damage discovered."},
        {"name": "Install Labor", "unit": "LF", "measure": "gutter",
         "desc_good": "Professional Install", "desc_better": "Certified Install", "desc_best": "Master Install",
         "notes_good": "Professional gutter installation by our experienced crew.", "notes_better": "Certified installation with proper slope (1/16\" per foot) and secure hanger spacing for optimal performance.", "notes_best": "Master installer-led installation with precision slope calibration, hidden hanger system, and completion documentation."},
        {"name": "Permit", "unit": "LS",
         "desc_good": "Permit", "desc_better": "Permit", "desc_best": "Permit",
         "notes_good": "Required building permit where applicable.", "notes_better": "Required building permit obtained and inspection coordinated.", "notes_best": "Full permit management."},
    ],
    "other": [
        {"name": "Custom Item", "unit": "EA",
         "desc_good": "", "desc_better": "", "desc_best": "",
         "notes_good": "", "notes_better": "", "notes_best": ""},
    ],
}


# ── Price Book helpers ─────────────────────────────────────────────────────

def _load_price_book():
    if os.path.exists(PRICE_BOOK_FILE):
        try:
            with open(PRICE_BOOK_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {'intros': [], 'materials': {}}

def _save_price_book(pb):
    with open(PRICE_BOOK_FILE, 'w') as f:
        json.dump(pb, f, indent=2)


# ── Catalog + bundles (two-level model) ─────────────────────────────────────
# Roofing and siding use a flat product catalog (one price each) + named
# "bundles" that pull products from the catalog. Each Good/Better/Best tier
# dropdown offers the bundles; picking one loads that bundle's items into the
# tier. Windows/gutters/other keep the older per-tier model. These SEED
# constants are injected into the price book response only when a book has no
# <trade>_catalog yet (fresh install or the live volume book), so the feature
# works out of the box; once the manager edits + saves in the Price Book, their
# version persists.
# Each product carries the customer-facing bullet(s) it contributes to a
# Good/Better/Best card in `bullets`. The card is BUILT from the products the
# bundle actually contains (bundleFeatures() in app.js) rather than from a copy
# blob stored on the bundle, so a bundle without soffit can't promise soffit and
# a manager-built bundle describes itself correctly. A product with no `bullets`
# key falls back to its name; an explicit [] contributes nothing.
#
# `colors` (and, on siding, `styles`) drive the in-app Visualizer — the rep
# uploads a photo of the house and paints the roof/siding masks, the pickers
# read these arrays. Hex values are hand-picked to sit close to the real
# manufacturer swatches when blended `multiply` over a photo; they are NOT a
# spec-sheet color match and are safe to edit. Seeded on material products
# only (bundle carries the material, and the color follows the material).
_ROOF_ASPHALT_COLORS = [
    {"name": "Weathered Wood",  "hex": "#5a4a3c"},
    {"name": "Charcoal Black",  "hex": "#2f2d2b"},
    {"name": "Moire Black",     "hex": "#1e1d1c"},
    {"name": "Driftwood",       "hex": "#7d6b52"},
    {"name": "Georgetown Gray", "hex": "#4a4a48"},
    {"name": "Colonial Slate",  "hex": "#3c4046"},
    {"name": "Burnt Sienna",    "hex": "#6b3a2a"},
]
_ROOF_METAL_COLORS = [
    {"name": "Charcoal Gray", "hex": "#2f2d2b"},
    {"name": "Matte Black",   "hex": "#191919"},
    {"name": "Regal Blue",    "hex": "#1a3252"},
    {"name": "Slate Gray",    "hex": "#4a4d4f"},
    {"name": "Copper Penny",  "hex": "#a65f2a"},
    {"name": "Burgundy",      "hex": "#5c1f26"},
    {"name": "Hemlock Green", "hex": "#2e3a2a"},
    {"name": "Bone White",    "hex": "#e6ded0"},
]
_ROOF_STONE_COLORS = [
    {"name": "Charcoal Shake",  "hex": "#2f2d2b"},
    {"name": "Weathered Timber","hex": "#5c4a35"},
    {"name": "Terracotta",      "hex": "#8a3f22"},
    {"name": "Slate Blend",     "hex": "#3c4046"},
]
_ROOF_RUBBER_COLORS = [
    {"name": "Beaumont Cedar",   "hex": "#5c4530"},
    {"name": "Beaumont Charcoal","hex": "#2a2826"},
    {"name": "Rundle Slate",     "hex": "#3c4046"},
]
ROOFING_CATALOG_SEED = [
    {"id": "m_landmark", "name": "CertainTeed Landmark (Architectural Shingle)", "unit": "SQ", "cost": 142, "measure": "squares_waste",
     "bullets": ["CertainTeed Landmark architectural laminate shingles", "Lifetime limited manufacturer warranty", "130 mph wind rating", "Dimensional shadow lines for depth and curb appeal"],
     "colors": _ROOF_ASPHALT_COLORS},
    {"id": "m_northgate", "name": "CertainTeed Northgate (Impact-Resistant Shingle)", "unit": "SQ", "cost": 175, "measure": "squares_waste",
     "bullets": ["CertainTeed Northgate SBS-modified impact-resistant shingles", "Class 4 impact rating — the highest hail rating there is", "May qualify for a homeowners insurance premium discount", "Lifetime limited manufacturer warranty", "130 mph wind rating"],
     "colors": _ROOF_ASPHALT_COLORS},
    {"id": "m_iko_nordic", "name": "IKO Nordic (Impact-Resistant Shingle)", "unit": "SQ", "cost": 175, "measure": "squares_waste",
     "bullets": ["IKO Nordic impact-resistant shingles", "Class 4 impact rating — the highest hail rating there is", "Built for extreme cold and freeze-thaw cycles", "May qualify for a homeowners insurance premium discount", "Limited lifetime manufacturer warranty"],
     "colors": _ROOF_ASPHALT_COLORS},
    {"id": "m_edco", "name": "EDCO Steel Shingle", "unit": "SQ", "cost": 300, "measure": "squares_waste",
     "bullets": ["EDCO steel shingles — architectural shingle look in real steel", "Class 4 impact rating, will not crack or lose granules to hail", "Limited lifetime warranty with hail damage coverage", "Baked-on finish that will not chip, peel, or fade"],
     "colors": _ROOF_METAL_COLORS},
    {"id": "m_stone", "name": "Stone-Coated Steel", "unit": "SQ", "cost": 330, "measure": "squares_waste",
     "bullets": ["Stone-coated steel panels with a textured shake/shingle profile", "Class 4 impact rating and 120+ mph wind rating", "Steel strength at a fraction of the weight of tile", "50-year limited manufacturer warranty"],
     "colors": _ROOF_STONE_COLORS},
    {"id": "m_standing_seam", "name": "Standing Seam Metal (24ga)", "unit": "SQ", "cost": 400, "measure": "squares_waste",
     "bullets": ["24ga standing seam metal panels with concealed fasteners", "No exposed screws to back out or leak over time", "50+ year service life — the last roof this house needs", "Class 4 impact rating and Kynar 500 finish warranty", "Clean modern lines in your choice of color"],
     "colors": _ROOF_METAL_COLORS},
    {"id": "m_euroshield", "name": "Euroshield (Rubber)", "unit": "SQ", "cost": 360, "measure": "squares_waste",
     "bullets": ["Euroshield recycled-rubber roofing in a slate or shake profile", "Class 4 impact rating — rubber absorbs hail instead of cracking", "Engineered for Colorado freeze-thaw cycles", "50-year limited manufacturer warranty", "Made from recycled tires — a genuinely green roof"],
     "colors": _ROOF_RUBBER_COLORS},
    {"id": "a_underlayment", "name": "Synthetic Underlayment", "unit": "SQ", "cost": 9.1, "measure": "squares_waste",
     "bullets": ["Synthetic underlayment over the full roof deck"]},
    {"id": "a_ice_water", "name": "Ice & Water Shield", "unit": "SQ", "cost": 46.46, "measure": "eave_valley",
     "bullets": ["Ice & water shield at eaves and valleys"]},
    {"id": "a_drip_edge", "name": "Drip Edge", "unit": "LF", "cost": 0, "measure": "eave_rake",
     "bullets": ["New drip edge at eaves and rakes"]},
    {"id": "a_ridge_cap", "name": "Ridge Cap", "unit": "LF", "cost": 0, "measure": "ridge_hip",
     "bullets": ["New ridge cap over every ridge and hip"]},
    {"id": "a_starter", "name": "Starter Strip", "unit": "LF", "cost": 0, "measure": "eave_rake",
     "bullets": ["New starter strip at eaves and rakes"]},
    {"id": "a_pipe_boots", "name": "Pipe Boots", "unit": "EA", "cost": 0, "measure": "pipe_boots",
     "bullets": ["New pipe boots on every roof penetration"]},
    {"id": "a_step_flash", "name": "Step / Wall Flashing", "unit": "LF", "cost": 0, "measure": "step",
     "bullets": ["New step and wall flashing"]},
    {"id": "a_skylight", "name": "Skylight Flashing", "unit": "EA", "cost": 0, "measure": "skylights",
     "bullets": ["New skylight flashing kits"]},
    {"id": "a_decking", "name": "Decking (OSB 7/16\")", "unit": "EA", "cost": 30,
     "bullets": ["Damaged decking replaced sheet for sheet"]},
    {"id": "a_ridge_vent", "name": "Ridge Vent", "unit": "LF", "cost": 34, "measure": "ridge_vent_code", "bundle_lf": 4, "bundle_unit": "sticks",
     "bullets": ["Continuous ridge vent cut in along the ridge"]},
    {"id": "a_intake_vent", "name": "Intake Vent", "unit": "LF", "cost": 4.5, "measure": "eave",
     "bullets": ["Intake venting at the eaves to balance the attic"]},
    {"id": "a_vent_plug", "name": "Vent Plug", "unit": "EA", "cost": 25, "measure": "turtle_vents",
     "bullets": ["Old turtle vents removed and decked over"]},
    {"id": "l_tearoff", "name": "Tear-Off Labor", "unit": "SQ", "cost": 0, "measure": "squares_waste",
     "bullets": ["Complete tear-off of existing roofing down to the deck"]},
    {"id": "l_install", "name": "Install Labor", "unit": "SQ", "cost": 0, "measure": "squares_waste",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "x_dumpster", "name": "Dumpster", "unit": "LS", "cost": 0,
     "bullets": ["Dumpster and full magnetic nail sweep"]},
    {"id": "x_permit", "name": "Permit", "unit": "LS", "cost": 0,
     "bullets": ["Permit pulled and final inspection scheduled"]},
]
_RS = ["a_underlayment", "a_ice_water", "a_drip_edge", "a_ridge_cap", "a_starter",
       "a_pipe_boots", "a_step_flash", "a_decking", "l_tearoff", "l_install", "x_dumpster", "x_permit"]
# Bullets no product owns. Everything else on the card comes from the products,
# so this list stays short — it closes the card, it doesn't describe the scope.
_RS_EXTRA = ["5-year Project One workmanship warranty"]
ROOFING_BUNDLES_SEED = [
    {"id": "b_landmark", "name": "CertainTeed Landmark", "product_ids": ["m_landmark"] + _RS, "description": "Architectural laminate shingle system — dimensional shadow lines, lifetime limited warranty.",
     "extra_features": _RS_EXTRA},
    {"id": "b_northgate", "name": "CertainTeed Northgate", "product_ids": ["m_northgate"] + _RS, "description": "Class 4 impact-resistant SBS shingle — hail-country durability, may qualify for an insurance discount.",
     "extra_features": _RS_EXTRA},
    {"id": "b_iko_nordic", "name": "IKO Nordic", "product_ids": ["m_iko_nordic"] + _RS, "description": "Class 4 impact-resistant shingle built for extreme cold and hail.",
     "extra_features": _RS_EXTRA},
    {"id": "b_edco", "name": "EDCO", "product_ids": ["m_edco"] + _RS, "description": "EDCO steel shingles — the look of architectural shingles in Class 4 impact-rated steel.",
     "extra_features": _RS_EXTRA},
    {"id": "b_stone", "name": "Stone-Coated Steel", "product_ids": ["m_stone"] + _RS, "description": "Stone-coated steel panels — steel strength with a textured shake/shingle look, wind-rated 120+ mph.",
     "extra_features": _RS_EXTRA},
    {"id": "b_standing_seam", "name": "Standing Seam", "product_ids": ["m_standing_seam"] + _RS, "description": "24ga standing seam metal with concealed fasteners — the premium 50+ year system.",
     "extra_features": _RS_EXTRA},
    {"id": "b_euroshield", "name": "Euroshield", "product_ids": ["m_euroshield"] + _RS, "description": "Recycled-rubber roofing with the look of slate/shake — Class 4 impact, freeze-thaw resistant.",
     "extra_features": _RS_EXTRA},
]
ROOFING_TIER_DEFAULTS_SEED = {"good": "b_landmark", "better": "b_northgate", "best": "b_standing_seam"}

# Siding catalog: one price per product, accessories named to MATCH the old
# siding template rows so an in-flight estimate's existing items get adopted by
# name instead of duplicated when a rep picks a bundle. Material costs come from
# the old per-tier variant menus; accessory/labor costs are PLACEHOLDERS (0) —
# the manager sets real numbers in Price Book → Siding → Products.
# The catalog is grouped by system so a manager scanning the Price Book sees
# every LP SKU together, every Hardie SKU together, etc. The `group` field is
# metadata-only — the Price Book UI reads it to insert subheader rows and it
# is backfilled onto pre-existing books via _PRODUCT_BACKFILL_FIELDS so a
# saved catalog picks up the groups on the next GET. Reordering the seed does
# not change customer pricing: every bundle carries its own product_ids in
# its own order, and bundleFeatures/apply logic reads that.
# Siding colors + styles for the Visualizer. Colors are hex; styles carry a
# `pattern_id` that keys into a frontend `SIDING_PATTERNS` bank of tileable
# SVG overlays. Seeded on the material products only. The style list per
# manufacturer covers what the family visually offers, not strictly what the
# individual SKU ships — reps sell mixed-style houses (lap on the field, shake
# on a gable) and the visualizer needs the flexibility to render both.
_SIDING_NEUTRAL_COLORS = [
    {"name": "Arctic White",  "hex": "#eae7de"},
    {"name": "Iron Gray",     "hex": "#4a4d4f"},
    {"name": "Aged Pewter",   "hex": "#6b6b64"},
    {"name": "Cobble Stone",  "hex": "#a89a86"},
    {"name": "Timber Bark",   "hex": "#7a6b5b"},
    {"name": "Evening Blue",  "hex": "#3a4553"},
    {"name": "Boothbay Blue", "hex": "#6b7f8f"},
    {"name": "Khaki Brown",   "hex": "#8b7a5c"},
    {"name": "Sail Cloth",    "hex": "#d4cdbf"},
    {"name": "Deep Ocean",    "hex": "#2a2f33"},
]
_SIDING_STEEL_COLORS = [
    {"name": "Charcoal",     "hex": "#2f2d2b"},
    {"name": "Silver Gray",  "hex": "#8a8c8d"},
    {"name": "Musket Brown", "hex": "#4a3527"},
    {"name": "Coastal Sage", "hex": "#6b7a5a"},
    {"name": "Slate",        "hex": "#4a4d4f"},
    {"name": "Regal Red",    "hex": "#7a1f26"},
]
# LP SmartSide ExpertFinish sales sheet LPEF01884 (01/25), supplied by the
# company on 2026-08-27. These RGB values are sampled from the solid digital
# swatches in that PDF; LP itself labels the displayed colors representative,
# not an exact physical match. Do not reuse this palette for field-painted LP.
_LP_EXPERTFINISH_COLORS = [
    {"name": "Snowscape White",  "hex": "#f2f1f1"},
    {"name": "Sand Dunes",       "hex": "#ece6d8"},
    {"name": "Desert Stone",     "hex": "#e8e4db"},
    {"name": "Quarry Gray",      "hex": "#c0c2b8"},
    {"name": "Prairie Clay",     "hex": "#bfbaa4"},
    {"name": "Terra Brown",      "hex": "#b6a892"},
    {"name": "Harvest Honey",    "hex": "#c4a87e"},
    {"name": "Timberland Suede", "hex": "#8f8673"},
    {"name": "Garden Sage",      "hex": "#717864"},
    {"name": "Redwood Red",      "hex": "#794946"},
    {"name": "Tundra Gray",      "hex": "#8d8681"},
    {"name": "Summit Blue",      "hex": "#859298"},
    {"name": "Rapids Blue",      "hex": "#42647d"},
    {"name": "Cavern Steel",     "hex": "#6b6e71"},
    {"name": "Midnight Shadow",  "hex": "#505757"},
    {"name": "Abyss Black",      "hex": "#2b3131"},
]
_STYLE_LAP   = {"id": "s_lap",   "name": "Lap Siding",     "pattern_id": "lap"}
_STYLE_BNB   = {"id": "s_bnb",   "name": "Board & Batten", "pattern_id": "bnb"}
_STYLE_SHAKE = {"id": "s_shake", "name": "Shingle-Style",  "pattern_id": "shake"}
_STYLE_PANEL = {"id": "s_panel", "name": "Vertical Panel", "pattern_id": "panel"}
_HARDIE_STYLES = [_STYLE_LAP, _STYLE_BNB, _STYLE_SHAKE, _STYLE_PANEL]
_LP_STANDARD_STYLES = [_STYLE_LAP, _STYLE_BNB, _STYLE_SHAKE]
_LP_EXPERTFINISH_STYLES = [
    {"id": "s_lp_lap_joint", "name": "Lap Joint Siding", "pattern_id": "lap"},
    {"id": "s_lp_shakes", "name": "Shakes", "pattern_id": "shake"},
    {"id": "s_lp_panel", "name": "Panel - NGSE", "pattern_id": "panel"},
    {"id": "s_lp_nickel_gap", "name": "Nickel Gap", "pattern_id": "nickel_gap"},
    {"id": "s_lp_vertical", "name": "Vertical Siding", "pattern_id": "panel"},
]
_EDCO_STYLES   = [_STYLE_LAP, _STYLE_PANEL]
_VINYL_STYLES  = [_STYLE_LAP, _STYLE_BNB]
SIDING_CATALOG_SEED = [
    # ── LP SmartSide (QXO 2026 line) ───────────────────────────────────────
    # Materials, then LP-specific trim, soffit, and paint.
    {"id": "s_lp_standard", "name": "LP SmartSide 8\" Cedar Text Lap", "group": "LP SmartSide", "unit": "SQ", "cost": 163.61, "measure": "siding_sq_waste",
     "bullets": ["LP SmartSide engineered wood lap, 8\" Cedar Text exposure",
                 "Field-painted in the color of your choice",
                 "SmartGuard-treated engineered wood resists rot, hail, and termites",
                 "5/50 year limited manufacturer warranty"],
     "colors": _SIDING_NEUTRAL_COLORS, "styles": _LP_STANDARD_STYLES},
    {"id": "s_lp_expert", "name": "LP SmartSide Expert Finish 8\" Lap", "group": "LP SmartSide", "unit": "SQ", "cost": 245.14, "measure": "siding_sq_waste",
     "bullets": ["LP SmartSide ExpertFinish Lap Joint engineered wood siding, 8\" nominal width",
                 "Pre-finished at the factory — no field painting required",
                 "Available in 16 ExpertFinish colors and cedar or brushed-smooth textures",
                 "SmartGuard-treated engineered wood resists rot, hail, and termites",
                 "5/15/50 prorated limited warranty — 5-year labor and materials, 15-year finish, 50-year substrate"],
     "colors": _LP_EXPERTFINISH_COLORS, "styles": _LP_EXPERTFINISH_STYLES},
    {"id": "sa_lp_standard_trim", "name": "LP SmartSide Trim 5/4×6", "group": "LP SmartSide", "unit": "LF", "cost": 1.83, "measure": "siding_trim",
     "bullets": ["LP SmartSide trim at corners, windows, and doors — painted to match"]},
    {"id": "sa_lp_expert_trim", "name": "LP Expert Finish Trim 5/4×5.5", "group": "LP SmartSide", "unit": "LF", "cost": 2.33, "measure": "siding_trim",
     "bullets": ["LP SmartSide Expert Finish trim at corners, windows, and doors — pre-finished to match"]},
    {"id": "sa_lp_standard_soffit", "name": "LP SmartSide Vented Soffit 24\"", "group": "LP SmartSide", "unit": "LF", "cost": 4.06, "measure": "siding_soffit",
     "bullets": ["LP SmartSide vented soffit — painted to match"]},
    {"id": "sa_lp_expert_soffit", "name": "LP Expert Finish Vented Soffit 24\"", "group": "LP SmartSide", "unit": "LF", "cost": 6.51, "measure": "siding_soffit",
     "bullets": ["LP SmartSide Expert Finish vented soffit — pre-finished to match"]},

    # ── James Hardie (QXO 2026 line) ───────────────────────────────────────
    # Materials, HardieWrap (Hardie-only weather barrier — required for the
    # system warranty), then Hardie-specific trim and soffit.
    {"id": "s_hardie_primed", "name": "James Hardie Primed 8.25\" Cedar Mill Lap", "group": "James Hardie", "unit": "SQ", "cost": 201.29, "measure": "siding_sq_waste",
     "bullets": ["James Hardie primed fiber cement lap, 8.25\" Cedar Mill woodgrain texture",
                 "Field-painted in the color of your choice for a custom look",
                 "Non-combustible fiber cement — will not feed a fire",
                 "Hail, pest, and rot proof; engineered for Colorado freeze-thaw",
                 "30-year limited manufacturer warranty"],
     "colors": _SIDING_NEUTRAL_COLORS, "styles": _HARDIE_STYLES},
    {"id": "s_hardie_statement", "name": "James Hardie Statement Collection 8.25\" Lap", "group": "James Hardie", "unit": "SQ", "cost": 248.71, "measure": "siding_sq_waste",
     "bullets": ["James Hardie Statement Collection fiber cement lap, 8.25\" exposure",
                 "ColorPlus factory finish — no field painting, backed by a 15-year finish warranty",
                 "Non-combustible fiber cement — will not feed a fire",
                 "Hail, pest, and rot proof; engineered for Colorado freeze-thaw",
                 "30-year limited manufacturer warranty"],
     "colors": _SIDING_NEUTRAL_COLORS, "styles": _HARDIE_STYLES},
    {"id": "sa_wrap_hardie", "name": "HardieWrap Weather Barrier", "group": "James Hardie", "unit": "SQ", "cost": 23.46, "measure": "siding_sq_waste",
     "bullets": ["HardieWrap weather-resistant barrier over the full wall area — required for the James Hardie system warranty"]},
    {"id": "sa_hardie_primed_trim", "name": "James Hardie Primed Trim 5/4×6", "group": "James Hardie", "unit": "LF", "cost": 2.51, "measure": "siding_trim",
     "bullets": ["James Hardie primed fiber cement trim at corners, windows, and doors — painted to match"]},
    {"id": "sa_hardie_statement_trim", "name": "James Hardie Statement Trim 5/4×5.5", "group": "James Hardie", "unit": "LF", "cost": 2.6, "measure": "siding_trim",
     "bullets": ["James Hardie Statement fiber cement trim at corners, windows, and doors — ColorPlus finished to match"]},
    {"id": "sa_hardie_primed_soffit", "name": "James Hardie Primed Vented Soffit 24\"", "group": "James Hardie", "unit": "LF", "cost": 2.35, "measure": "siding_soffit",
     "bullets": ["James Hardie primed vented fiber cement soffit — painted to match"]},
    {"id": "sa_hardie_statement_soffit", "name": "James Hardie Statement Vented Soffit 24\"", "group": "James Hardie", "unit": "LF", "cost": 3.66, "measure": "siding_soffit",
     "bullets": ["James Hardie Statement vented fiber cement soffit — ColorPlus finished to match"]},

    # ── EDCO Steel Siding (QXO 2026 line) ──────────────────────────────────
    # Materials, then EDCO-specific J-channel, corners, starter, soffit, fasteners.
    {"id": "s_edco_d4", "name": "EDCO D4\" TimberGrain Steel Siding", "group": "EDCO Steel", "unit": "SQ", "cost": 386.11, "measure": "siding_sq_waste",
     "bullets": ["EDCO D4\" 28ga steel siding with TimberGrain wood-look finish",
                 "Class 4 impact rated — will not crack or lose finish to hail",
                 "May qualify for a homeowners insurance premium discount",
                 "Non-combustible steel construction, baked-on finish that will not chip, peel, or fade",
                 "Limited lifetime manufacturer warranty"],
     "colors": _SIDING_STEEL_COLORS, "styles": _EDCO_STYLES},
    {"id": "s_edco_8", "name": "EDCO 8\" Enduragrain Steel Siding", "group": "EDCO Steel", "unit": "SQ", "cost": 386.11, "measure": "siding_sq_waste",
     "bullets": ["EDCO 8\" 28ga steel siding with Enduragrain finish",
                 "Class 4 impact rated — will not crack or lose finish to hail",
                 "May qualify for a homeowners insurance premium discount",
                 "Non-combustible steel construction, baked-on finish that will not chip, peel, or fade",
                 "Limited lifetime manufacturer warranty"],
     "colors": _SIDING_STEEL_COLORS, "styles": _EDCO_STYLES},
    {"id": "sa_edco_jchannel", "name": "EDCO 5/8\" J-Channel", "group": "EDCO Steel", "unit": "LF", "cost": 1.21, "measure": "j_channel",
     "bullets": ["EDCO 5/8\" steel J-channel around every window and door"]},
    {"id": "sa_edco_corner", "name": "EDCO Snap-On Corner Post", "group": "EDCO Steel", "unit": "LF", "cost": 2.94, "measure": "corners_out",
     "bullets": ["EDCO snap-on steel corner posts for a clean, finished outside corner"]},
    {"id": "sa_edco_starter", "name": "EDCO Starter Strip Steel", "group": "EDCO Steel", "unit": "LF", "cost": 1.05, "measure": "siding_starter",
     "bullets": ["EDCO steel starter strip that sets the first course dead level"]},
    {"id": "sa_edco_soffit", "name": "EDCO Soffit Panel 16\"×12'", "group": "EDCO Steel", "unit": "LF", "cost": 4.03, "measure": "siding_soffit",
     "bullets": ["EDCO 16\" steel soffit panels"]},
    {"id": "sa_edco_fasteners", "name": "EDCO Manufacturer-Approved Fasteners", "group": "EDCO Steel", "unit": "SQ", "cost": 0, "measure": "siding_sq_waste",
     "bullets": ["EDCO-approved corrosion-resistant fasteners per the manufacturer's schedule"]},

    # ── Shared Accessories ────────────────────────────────────────────────
    # System-agnostic add-ons every bundle can carry — TriBuilt wrap is the
    # default for anything not on HardieWrap; sa_starter/sa_fascia read the
    # generic starter/fascia measurements; sa_paint/sa_touchup cover
    # field-painted vs pre-finished; sa_rot_repair is a per-SF allowance.
    {"id": "sa_wrap_tribuilt", "name": "TriBuilt House Wrap", "group": "Shared", "unit": "SQ", "cost": 6.23, "measure": "siding_sq_waste",
     "bullets": ["TriBuilt weather-resistant house wrap over the full wall area"]},
    {"id": "sa_starter", "name": "Starter Strip", "group": "Shared", "unit": "LF", "cost": 0, "measure": "siding_starter",
     "bullets": ["New starter strip along the bottom course"]},
    {"id": "sa_fascia", "name": "Fascia", "group": "Shared", "unit": "LF", "cost": 0, "measure": "siding_fascia",
     "bullets": ["New fascia"]},
    {"id": "sa_kickout", "name": "Kickout Flashing", "group": "Shared", "unit": "EA", "cost": 0,
     "bullets": ["Kickout flashing at every roof-to-wall intersection — required to keep water out of the wall assembly and to satisfy the manufacturer warranty"]},
    {"id": "sa_sealant", "name": "Elastomeric Sealant / Caulk", "group": "Shared", "unit": "LS", "cost": 0,
     "bullets": ["Manufacturer-approved elastomeric sealant at butt joints and penetrations — required for the finish and system warranty"]},
    {"id": "sa_paint", "name": "Field Paint (Primed Siding)", "group": "Shared", "unit": "SQ", "cost": 0, "measure": "siding_sq_waste",
     "bullets": ["Two-coat exterior paint applied over the primed siding in the color of your choice"]},
    {"id": "sa_touchup", "name": "ColorPlus / Factory Touch-Up Paint", "group": "Shared", "unit": "LS", "cost": 0,
     "bullets": ["Factory touch-up paint kept on site for field cuts and fasteners"]},
    {"id": "sa_rot_repair", "name": "Rot Repair Allowance", "group": "Shared", "unit": "SF", "cost": 0,
     "bullets": ["Sheathing or trim rot repair discovered on tear-off — priced per square foot and billed only if needed"]},

    # ── Labor & Misc ──────────────────────────────────────────────────────
    # Labor prices into the package but is NOT broken out for the customer:
    # "Install Labor — $9,400" invites a line-item negotiation over the one
    # number that is really the crew, while the bullets keep promising the work.
    # `customer_visible: False` hides the ROW, not the promise — see
    # bundleFeatures() in app.js.
    {"id": "sl_tearoff", "name": "Tear-Off Labor", "group": "Labor & Misc", "unit": "SQ", "cost": 0, "measure": "siding_squares",
     "customer_visible": False,
     "bullets": ["Complete tear-off of existing siding"]},
    {"id": "sl_install", "name": "Install Labor", "group": "Labor & Misc", "unit": "SQ", "cost": 0, "measure": "siding_squares",
     "customer_visible": False,
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "sx_dumpster", "name": "Dumpster", "group": "Labor & Misc", "unit": "LS", "cost": 0,
     "bullets": ["Dumpster and full site cleanup"]},
    {"id": "sx_permit", "name": "Permit", "group": "Labor & Misc", "unit": "LS", "cost": 0,
     "bullets": ["Permit pulled and final inspection scheduled"]},

    # ── Legacy (pre-QXO vinyl + old LP/Hardie + generic accessories) ──────
    # Kept in the catalog so an in-flight estimate that references them still
    # renders, and so a manager can price them per house if they want to
    # continue offering the older systems. Nothing bundle-side ships to a new
    # estimate on these — the QXO products above are the current defaults.
    {"id": "s_vinyl_dutch", "name": "Vinyl - Dutch Lap 4\"", "group": "Legacy", "unit": "SQ", "cost": 165, "measure": "siding_sq_waste",
     "bullets": ["Vinyl siding in the classic 4\" Dutch lap profile", "Never needs paint - wash it once a year and it is done", "Color runs all the way through, so scratches do not show", "Lifetime limited manufacturer warranty"]},
    {"id": "s_vinyl_clap", "name": "Vinyl - Clapboard 4.5\"", "group": "Legacy", "unit": "SQ", "cost": 170, "measure": "siding_sq_waste",
     "bullets": ["Vinyl siding in a traditional 4.5\" clapboard profile", "Clean horizontal lines that suit almost any home style", "Fade-resistant color all the way through the panel", "Never needs paint", "Lifetime limited manufacturer warranty"]},
    {"id": "s_vinyl_bb", "name": "Vinyl - Board & Batten", "group": "Legacy", "unit": "SQ", "cost": 195, "measure": "siding_sq_waste",
     "bullets": ["Vertical board & batten vinyl panels", "Modern farmhouse curb appeal", "Zero-maintenance vinyl durability - never needs paint", "Lifetime limited manufacturer warranty"]},
    {"id": "s_lp_lap", "name": "LP SmartSide - Lap 8\"", "group": "Legacy", "unit": "SQ", "cost": 240, "measure": "siding_sq_waste",
     "bullets": ["LP SmartSide engineered wood lap siding, 8\" exposure", "The warmth and texture of real wood grain", "SmartGuard treated to resist rot, hail, and termites", "Holds paint far longer than natural wood", "50-year limited manufacturer warranty"]},
    {"id": "s_lp_panel", "name": "LP SmartSide - Panel / Board & Batten", "group": "Legacy", "unit": "SQ", "cost": 265, "measure": "siding_sq_waste",
     "bullets": ["LP SmartSide engineered wood panel and batten system", "Bold vertical lines with real wood texture", "SmartGuard treated to resist rot, hail, and termites", "50-year limited manufacturer warranty"]},
    {"id": "s_hardie_cedar", "name": "James Hardie - Plank Lap 8.25\" (Cedarmill)", "group": "Legacy", "unit": "SQ", "cost": 320, "measure": "siding_sq_waste",
     "bullets": ["James Hardie fiber cement lap siding, Cedarmill woodgrain texture", "Non-combustible - will not feed a fire", "Hail, pest, and rot proof", "ColorPlus factory finish backed for 15 years", "30-year limited manufacturer warranty"]},
    {"id": "s_hardie_smooth", "name": "James Hardie - Plank Lap 7\" (Smooth)", "group": "Legacy", "unit": "SQ", "cost": 315, "measure": "siding_sq_waste",
     "bullets": ["James Hardie fiber cement lap siding with a clean smooth finish", "Engineered specifically for Colorado freeze-thaw and hail", "Non-combustible, hail, pest, and rot proof", "ColorPlus factory finish backed for 15 years", "30-year limited manufacturer warranty"]},
    {"id": "s_hardie_shingle", "name": "James Hardie - Shingle / Panel", "group": "Legacy", "unit": "SQ", "cost": 360, "measure": "siding_sq_waste",
     "bullets": ["James Hardie shingle and panel siding", "Shake-style character without the maintenance of real cedar", "Ideal for gables, dormers, and accent walls", "Non-combustible, hail, pest, and rot proof", "30-year limited manufacturer warranty"]},
    {"id": "sa_house_wrap", "name": "House Wrap", "group": "Legacy", "unit": "SQ", "cost": 0, "measure": "siding_sq_waste",
     "bullets": ["House wrap weather barrier over the full wall area"]},
    {"id": "sa_j_channel", "name": "J-Channel", "group": "Legacy", "unit": "LF", "cost": 0, "measure": "j_channel",
     "bullets": ["New J-channel around every window and door"]},
    {"id": "sa_corner_out", "name": "Corner Posts", "group": "Legacy", "unit": "LF", "cost": 0, "measure": "corners_out",
     "bullets": ["New outside corner posts"]},
    {"id": "sa_corner_in", "name": "Inside Corners", "group": "Legacy", "unit": "LF", "cost": 0, "measure": "corners_in",
     "bullets": ["New inside corner trim"]},
    {"id": "sa_trim", "name": "Trim Board", "group": "Legacy", "unit": "LF", "cost": 0,
     "bullets": ["New trim boards"]},
    {"id": "sa_soffit", "name": "Soffit", "group": "Legacy", "unit": "LF", "cost": 0, "measure": "siding_soffit",
     "bullets": ["New soffit"]},
]
_SS = ["sa_house_wrap", "sa_starter", "sa_j_channel", "sa_corner_out", "sa_corner_in",
       "sa_trim", "sa_soffit", "sa_fascia", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"]
_SS_EXTRA = ["5-year Project One workmanship warranty"]
SIDING_BUNDLES_SEED = [
    {"id": "sb_vinyl_dutch", "name": "Vinyl - Dutch Lap", "product_ids": ["s_vinyl_dutch"] + _SS,
     "description": "Insulated-ready vinyl siding in the classic Dutch lap profile - low maintenance, never needs paint, lifetime limited warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_vinyl_clap", "name": "Vinyl - Clapboard", "product_ids": ["s_vinyl_clap"] + _SS,
     "description": "Traditional clapboard vinyl siding - clean horizontal lines, fade-resistant color all the way through.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_vinyl_bb", "name": "Vinyl - Board & Batten", "product_ids": ["s_vinyl_bb"] + _SS,
     "description": "Vertical board & batten vinyl - modern farmhouse curb appeal with zero-maintenance vinyl durability.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_lp_lap", "name": "LP SmartSide - Lap", "product_ids": ["s_lp_lap"] + _SS,
     "description": "LP SmartSide engineered wood lap siding - the warmth and texture of real wood, treated to resist rot, hail, and termites. 50-year limited warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_lp_panel", "name": "LP SmartSide - Board & Batten", "product_ids": ["s_lp_panel"] + _SS,
     "description": "LP SmartSide engineered wood panel and batten system - bold vertical lines with impact-resistant engineered wood strength.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_hardie_cedar", "name": "James Hardie - Cedarmill Lap", "product_ids": ["s_hardie_cedar"] + _SS,
     "description": "James Hardie fiber cement in the Cedarmill woodgrain texture - non-combustible, hail and pest proof, ColorPlus finish backed for 15 years.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_hardie_smooth", "name": "James Hardie - Smooth Lap", "product_ids": ["s_hardie_smooth"] + _SS,
     "description": "James Hardie fiber cement lap siding with a clean smooth finish - the premium look, engineered for Colorado freeze-thaw and hail.",
     "extra_features": _SS_EXTRA},
    {"id": "sb_hardie_shingle", "name": "James Hardie - Shingle / Panel", "product_ids": ["s_hardie_shingle"] + _SS,
     "description": "James Hardie shingle and panel siding - shake-style character in fiber cement, ideal for gables and accent walls.",
     "extra_features": _SS_EXTRA},
    {"id": "b_lp_standard", "name": "LP SmartSide", "product_ids": ["s_lp_standard", "sa_wrap_tribuilt", "sa_starter", "sa_lp_standard_trim", "sa_lp_standard_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_paint", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "Engineered wood lap siding — field-painted to any color, 5/50 year limited warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "b_lp_expert", "name": "LP SmartSide Expert Finish", "product_ids": ["s_lp_expert", "sa_wrap_tribuilt", "sa_starter", "sa_lp_expert_trim", "sa_lp_expert_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_touchup", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "Pre-finished engineered wood siding — no painting required, 5/50 year warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "b_hardie_primed", "name": "James Hardie Primed", "product_ids": ["s_hardie_primed", "sa_wrap_hardie", "sa_starter", "sa_hardie_primed_trim", "sa_hardie_primed_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_paint", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "Fiber cement lap siding — field-painted to any color, non-combustible, 30-year limited warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "b_hardie_statement", "name": "James Hardie Statement Collection", "product_ids": ["s_hardie_statement", "sa_wrap_hardie", "sa_starter", "sa_hardie_statement_trim", "sa_hardie_statement_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_touchup", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "Pre-finished fiber cement siding — no painting required, non-combustible, 15-year ColorPlus finish warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "b_edco_d4", "name": "EDCO D4\" TimberGrain", "product_ids": ["s_edco_d4", "sa_edco_jchannel", "sa_edco_corner", "sa_corner_in", "sa_edco_starter", "sa_edco_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_edco_fasteners", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "28ga steel siding — Class 4 impact-rated, TimberGrain wood-look finish, lifetime limited warranty.",
     "extra_features": _SS_EXTRA},
    {"id": "b_edco_8", "name": "EDCO 8\" Enduragrain", "product_ids": ["s_edco_8", "sa_edco_jchannel", "sa_edco_corner", "sa_corner_in", "sa_edco_starter", "sa_edco_soffit", "sa_fascia", "sa_kickout", "sa_sealant", "sa_edco_fasteners", "sa_rot_repair", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"],
     "description": "28ga steel siding — Class 4 impact-rated, Enduragrain finish, lifetime limited warranty.",
     "extra_features": _SS_EXTRA},
]
SIDING_TIER_DEFAULTS_SEED = {"good": "b_lp_standard", "better": "b_hardie_primed",
                             "best": "b_hardie_statement"}

# ── Siding profile factors ────────────────────────────────────────────────
# Piece-per-SQ conversions the supplier take-off sheet uses (LP Primed, LP
# Expert Finish, Hardie Primed, Hardie Statement tabs). Drives the Material
# Order piece counts on the production packet + the customer-facing profile
# name on the bundle card. Primary $/SQ COST does NOT change with profile —
# the supplier sheet has counts, not dollars, so per-profile costs stay a
# manager job until QXO supplies real B&B / Shake pricing.
#
# MUST mirror SIDING_PROFILE_FACTORS + SIDING_BUNDLE_PROFILES + SIDING_PROFILE_LABELS
# in app.js — the tests hold the two lists to the same shape.
SIDING_PROFILE_FACTORS = {
    'lp': {
        'lap_8':       {'primary': {'pcs_per_sq': 11.11, 'stick_ft': 16, 'size': '8" Cedar Text Lap'}},
        'bb_4x8':      {'primary': {'pcs_per_sq': 3.0,  'size': '4×8 Board'},
                        'battens': {'pcs_per_panel': 3, 'stick_ft': 16, 'size': '4/4×2 Batten'}},
        'cedar_shake': {'primary': {'pcs_per_sq': 7.5, 'stick_ft': 8, 'size': '12" Cedar Shake Panel',
                                    'source_note': 'PLACEHOLDER pcs/SQ — not in supplier sheet, confirm with QXO'}},
    },
    'hardie': {
        'lap_8_25':        {'primary': {'pcs_per_sq': 14.25, 'stick_ft': 12, 'size': '8.25" Cedar Mill Lap'}},
        'bb_4x10':         {'primary': {'pcs_per_sq': 2.5,  'size': '4×10 Board'},
                            'battens': {'pcs_per_panel': 3, 'stick_ft': 12, 'size': '4/4×2.5 Batten'}},
        'shake_straight':  {'primary': {'pcs_per_sq': 43, 'stick_ft': 4, 'size': '15.25×4 Straight-Edge Shake'}},
        'shake_staggered': {'primary': {'pcs_per_sq': 50, 'stick_ft': 4, 'size': '15.25×4 Staggered-Edge Shake'}},
    },
}
SIDING_BUNDLE_PROFILES = {
    'b_lp_standard':      {'mfg': 'lp',     'default': 'lap_8',    'options': ['lap_8', 'bb_4x8', 'cedar_shake']},
    'b_lp_expert':        {'mfg': 'lp',     'default': 'lap_8',    'options': ['lap_8', 'bb_4x8', 'cedar_shake']},
    'b_hardie_primed':    {'mfg': 'hardie', 'default': 'lap_8_25', 'options': ['lap_8_25', 'bb_4x10', 'shake_straight', 'shake_staggered']},
    'b_hardie_statement': {'mfg': 'hardie', 'default': 'lap_8_25', 'options': ['lap_8_25', 'bb_4x10', 'shake_straight', 'shake_staggered']},
}
SIDING_PROFILE_LABELS = {
    'lap_8':           '8" Lap',
    'bb_4x8':          'Board & Batten (4×8)',
    'cedar_shake':     'Cedar Shake',
    'lap_8_25':        '8.25" Lap',
    'bb_4x10':         'Board & Batten (4×10)',
    'shake_straight':  'Shake — Straight Edge',
    'shake_staggered': 'Shake — Staggered Edge',
}


def _siding_profile(td, tier):
    """Effective profile for a siding tier: stored per-tier value if valid for
    the picked bundle, otherwise the bundle's default. Mirrors sidingProfile
    (app.js)."""
    if not isinstance(td, dict):
        return ''
    bundle_id = ((td.get('tier_bundles') or {}).get(tier) or '').strip()
    cfg = SIDING_BUNDLE_PROFILES.get(bundle_id)
    if not cfg:
        return ''
    stored = ((td.get('tier_profiles') or {}).get(tier) or '').strip()
    if stored and stored in cfg['options']:
        return stored
    return cfg['default']

# Commercial low-slope catalog.
#
# The package matrix is membrane x attachment x substrate approach:
#
#                     TEAR-OFF                    LAYOVER (recover)
#   TPO     mechanically fastened / adhered   mechanically fastened / adhered
#   EPDM    mechanically fastened / adhered   mechanically fastened / adhered
#
# CARLISLE is the priced supplier: the 2026-08-19 quote covers TPO and EPDM
# from one house, which is what finally made the EPDM half of the matrix real.
# GAF numbers survive only where Carlisle quoted no equivalent (the 45/80-mil
# and specialty TPO membranes, the odd polyiso thicknesses, scuppers) — those
# came off a sheet that EXPIRED 2026-06-30 and need re-quoting before they are
# sold. Which supplier each cost came from is written on the line.
#
# Both sheets price by ROLL / BOX / carton; the catalog prices by SQ / LF / EA.
# Every conversion is written out on the line that uses it so the next person
# holding a newer sheet can redo it without guessing what was assumed.
COMMERCIAL_PRICE_SHEETS = {
    'carlisle': {
        'supplier': 'Carlisle',
        'quoted': '2026-08-19',
        'expires': '2026-08-30',
        'scope': 'Sure-Weld TPO and Sure-Seal EPDM, insulation, fasteners, accessories.',
        'excludes': 'Freight, fuel and material surcharges, and hazmat fees. Warehouse '
                    'truckload $200/truck. Tax figured at shipment.',
        'gaps': 'No REINFORCED EPDM (Sure-Tough) - so a mechanically fastened EPDM roof '
                'has no priced membrane. No 1/4" cover board. No retrofit drain or scupper.',
    },
    'gaf': {
        'supplier': 'GAF',
        'quoted': '2026-05-19',
        'expires': '2026-06-30',
        'expired': True,
        'scope': 'EverGuard TPO only. Retained for the membranes Carlisle did not quote.',
        'excludes': 'Manufacturer fuel surcharge and freight. Direct truckload freight '
                    '$750/load; warehouse truckload $200/truck. Tax figured at shipment.',
    },
}
# Kept as the "current" sheet for anything that asks for one.
COMMERCIAL_PRICE_SHEET = COMMERCIAL_PRICE_SHEETS['carlisle']

# What decides whether a LAYOVER is legal on a given building. Split by WHERE
# the rule comes from, because they carry different weight: the code ones are
# not negotiable by anybody, the material one is physics, and the manufacturer
# ones vary by the system actually specified.
#
# The manufacturer block below is GAF EverGuard's published recover procedure.
# It is retained because it is the one Project One has actually been working
# to, and because the steps (relieve trapped vapour, strip the roof back to a
# sound substrate, survey for moisture) are common to every single-ply recover.
# Carlisle publishes its own recover requirements for Sure-Weld and Sure-Seal,
# and THOSE govern on a Carlisle job — the closing line says so rather than
# quietly re-badging one manufacturer's procedure as another's.
COMMERCIAL_LAYOVER_RULES = [
    'CODE: a recover is not permitted where the existing roof already carries two or '
    'more roof coverings, or where it is water-soaked or deteriorated past serving as '
    'a base (IBC 1511.3 / 1512.2, adopted locally).',
    'MATERIAL: EPDM cannot contact asphalt - the bitumen migrates into the rubber and '
    'embrittles it. Over BUR or mod-bit the cover board is the required separation layer.',
    'Strip all ballast, loose gravel and debris; cut out blisters and ridges; remove all '
    'existing flashings, metal edge, drain leads, pipe boots and pitch pans.',
    'Existing single-ply must be cut into 10\' x 10\' sections maximum before the cover '
    'board is installed, to relieve trapped vapour.',
    'Moisture survey strongly recommended - and required where perlite or wood-fibre '
    'insulation stays in the assembly. Wet material must be removed and replaced.',
    'Full tear-off once more than 25% of the roof area is wet.',
    'No recover over a coal-tar pitch roof.',
    'Confirm the above against the published recover requirements for the system '
    'actually specified - the manufacturer\'s procedure governs the warranty.',
]

# Polyiso ships priced BY THE SQUARE on both sheets, so these lift straight
# across. R-value drives which one the spec calls for, so they are all offered
# rather than folded into one line: ~R-6 per inch of polyiso.
#
# Carlisle quoted 2.0" and 2.6" only. The rest are GAF's numbers off the
# EXPIRED sheet - usable to scope a job, not to sign one. Re-quote before use.
COMMERCIAL_ISO_SEED = [
    ("ca_iso_10", '1.0" Polyiso Insulation (~R-6)',   65.34, 'gaf'),
    ("ca_iso_15", '1.5" Polyiso Insulation (~R-9)',   74.15, 'gaf'),
    ("ca_iso_20", '2.0" Polyiso Insulation (~R-11)', 100.00, 'carlisle'),
    ("ca_iso_22", '2.2" Polyiso Insulation (~R-13)', 108.75, 'gaf'),
    ("ca_iso_30", '3.0" Polyiso Insulation (~R-17)', 148.30, 'gaf'),
    ("ca_iso_40", '4.0" Polyiso Insulation (~R-23)', 197.73, 'gaf'),
]

# Tapered polyiso, priced BY THE PANEL on the Carlisle sheet (4'x4'). Left as
# PC with a manual quantity on purpose: a tapered layout is engineered per roof
# off the drain locations, so the panel counts come from that layout and not
# from a square-foot measurement.
COMMERCIAL_TAPERED_SEED = [
    ("ca_taper_x", 'Tapered Polyiso - X panel, 1/4"/ft (0.5"-1.5", 4\'x4\')', 11.71),
    ("ca_taper_y", 'Tapered Polyiso - Y panel, 1/4"/ft (1.5"-2.5", 4\'x4\')', 23.41),
    ("ca_taper_q", 'Tapered Polyiso - Q panel, 1/2"/ft (0.5"-2.5", 4\'x4\')', 17.56),
]

COMMERCIAL_CATALOG_SEED = [
    # ── TPO membranes. CARLISLE Sure-Weld 060, priced per roll: a 10'x100'
    # roll is 1,000 sf = 10 SQ, so 806.82 / 10 = 80.68 per SQ. The 6'x100' roll
    # confirms it (600 sf, 484.09 -> the same 80.68). Both widths are stocked
    # because a mechanically fastened roof in a high-wind zone runs narrower
    # sheets to get more fastening rows; the cost per square is identical.
    {"id": "cm_tpo_ma", "name": "TPO Membrane 60-mil (Mechanically Fastened)", "unit": "SQ", "cost": 80.68, "measure": "comm_sq_waste", "attach": "mechanical",
     "bullets": ["60-mil Carlisle Sure-Weld TPO single-ply membrane", "Seams hot-air welded into a monolithic sheet - no adhesive, no tape", "Fastened at the wind-zone density the code requires", "Highly reflective white surface cuts cooling load", "20-year manufacturer system warranty available"]},
    {"id": "cm_tpo_fa", "name": "TPO Membrane 60-mil (Fully Adhered)", "unit": "SQ", "cost": 80.68, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["60-mil Carlisle Sure-Weld TPO single-ply membrane, fully adhered to the substrate", "Seams hot-air welded into a monolithic sheet", "Highest wind uplift rating - built for exposed and high-rise decks", "Smooth, flutter-free finished surface with no fastener pattern showing", "20-year manufacturer system warranty available"]},
    # Alternate thicknesses and specialty membranes — OPTIONS a rep swaps into
    # a package, not packages of their own: 45-mil where budget drives it,
    # 80-mil for hail and service traffic, self-adhered where fumes are a
    # problem, fleece-back to bond over a rough existing substrate.
    #
    # These are GAF EverGuard products at GAF's prices, and Carlisle did not
    # quote an equivalent. That sheet EXPIRED 2026-06-30 — good enough to scope
    # a job, not to sign one. Re-quote (from either house) before selling.
    {"id": "cm_tpo45_ma", "name": "TPO Membrane 45-mil (Mechanically Fastened)", "unit": "SQ", "cost": 72.73, "measure": "comm_sq_waste", "attach": "mechanical",
     "bullets": ["45-mil GAF EverGuard TPO single-ply membrane, hot-air welded seams", "The value membrane when budget drives the decision", "Highly reflective white surface cuts cooling load"]},
    {"id": "cm_tpo80_ma", "name": "TPO Membrane 80-mil (Mechanically Fastened)", "unit": "SQ", "cost": 135.23, "measure": "comm_sq_waste", "attach": "mechanical",
     "bullets": ["80-mil GAF EverGuard TPO - the thickest TPO GAF builds", "Extra thickness for hail resistance and heavy rooftop foot traffic", "Longest available TPO system warranty"]},
    {"id": "cm_tpo_sa", "name": "TPO Membrane 60-mil (Self-Adhered)", "unit": "SQ", "cost": 172.16, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["60-mil self-adhered TPO - factory-applied adhesive, no solvent bonding adhesive on site", "No open flame and no adhesive fumes - the low-disruption option over occupied space", "Highest wind uplift rating with a smooth finished surface"]},
    {"id": "cm_tpo_fb60", "name": "TPO Fleece-Back Membrane 60-mil (Fully Adhered)", "unit": "SQ", "cost": 139.21, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["60-mil fleece-back TPO - laminated fleece backing bonds over rough substrate", "The factory fleece doubles as a separation layer over an asphalt roof", "Extra puncture resistance and a cushioned walking surface"]},
    # ── EPDM membranes. Carlisle quoted 060 FR NON-REINFORCED Sure-Seal:
    # a 10'x100' roll is 1,000 sf = 10 SQ, so 965.91 / 10 = 96.59 per SQ
    # (the 10'x50' roll agrees at 482.95 / 5).
    #
    # The reinforced/non-reinforced split is a manufacturer requirement, not a
    # preference. A mechanically fastened system loads the sheet at every
    # plate, so it needs the scrim-REINFORCED membrane — Carlisle's Sure-Tough,
    # sold specifically for their Reinforced Mechanically Fastened system.
    # Non-reinforced Sure-Seal is specified for ADHERED and BALLASTED assemblies
    # only.
    #
    # The 2026-08-19 quote has NO Sure-Tough on it. So the fastened EPDM
    # package still has no membrane price, and putting the non-reinforced
    # number there would spec a roof the manufacturer does not warrant.
    # Ask Carlisle to add Sure-Tough reinforced EPDM to the quote.
    {"id": "cm_epdm_mf", "name": "EPDM Membrane 60-mil Reinforced (Mechanically Fastened)", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste", "attach": "mechanical",
     "bullets": ["60-mil scrim-reinforced EPDM rubber membrane", "Reinforced sheet is what a mechanically fastened EPDM system requires", "Seams spliced with primer and seam tape", "Decades of proven field performance in freeze-thaw climates"]},
    {"id": "cm_epdm_fa", "name": "EPDM Membrane 60-mil (Fully Adhered)", "unit": "SQ", "cost": 96.59, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["60-mil Carlisle Sure-Seal EPDM rubber single-ply membrane, fully adhered", "Seams spliced with primer and seam tape", "Excellent flexibility and hail resistance in freeze-thaw climates", "Smooth finished surface with no fastener pattern showing"]},
    # Same sheet with the splice tape factory-applied: 1,045.45 / 10 SQ =
    # 104.55. Against the plain roll at 96.59 plus 9.22 of SecureTape that is
    # a wash on material and saves the crew taping in the field.
    {"id": "cm_epdm_fa_taped", "name": "EPDM Membrane 60-mil, Factory-Taped (Fully Adhered)", "unit": "SQ", "cost": 104.55, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["60-mil Carlisle Sure-Seal EPDM with factory-applied splice tape", "Factory tape means a cleaner, more consistent seam than taping in the field", "Excellent flexibility and hail resistance in freeze-thaw climates"]},
    # The original generic EPDM line. Kept so estimates written against it still
    # load; new bids should use the reinforced/adhered pair above.
    {"id": "cm_epdm", "name": "EPDM Membrane 60-mil (legacy - use the fastened or adhered line)", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": []},
    {"id": "cm_modbit", "name": "Modified Bitumen, 2-Ply (Base + Cap Sheet)", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste", "attach": "adhered",
     "bullets": ["Two-ply modified bitumen base sheet and cap sheet", "Redundant membrane layers - a second line of waterproofing", "Stands up to foot traffic and rooftop equipment service", "Granulated cap surface for UV protection"]},
    {"id": "cm_coating", "name": "Silicone Restoration Coating System", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste", "attach": "coating",
     "bullets": ["Seamless silicone coating applied over the existing roof", "No tear-off - far less disruption to building operations", "Ponding-water resistant and highly reflective", "Renewable: recoat at the end of the warranty instead of re-roofing"]},

    # ── Build-up, tear-off systems
    # Default iso is 2.6" (~R-15), the common single-layer Colorado spec. The
    # other thicknesses ride in the catalog — see COMMERCIAL_ISO_SEED.
    {"id": "ca_iso", "name": '2.6" Polyiso Insulation (~R-15)', "unit": "SQ", "cost": 130.00, "measure": "comm_sq_waste",
     "bullets": ["Polyiso insulation to the specified R-value"]},
    # Carlisle 1/2" SecureShield HD, already priced per SQ on the sheet.
    {"id": "ca_cover", "name": "Cover Board (1/2\" HD)", "unit": "SQ", "cost": 96.34, "measure": "comm_sq_waste",
     "bullets": ["High-density cover board over the insulation"]},
    # ── Build-up, layover systems
    # The 1/4" board is the whole layover assembly: it is the smooth substrate
    # for the new membrane AND the separation layer the manufacturer requires
    # over an existing roof (mandatory under EPDM over anything asphaltic).
    # STILL not quoted. Carlisle's 2026-08-19 sheet carries 1/2" SecureShield
    # HD (see ca_cover, 96.34/SQ) but no 1/4" board — the 1/4" entries on that
    # sheet are TAPERED panels, where 1/4" is the SLOPE PER FOOT, not the
    # thickness. A true 1/4" recover board is gypsum (DensDeck Prime,
    # SecuRock) and needs its own quote.
    #
    # Two ways to close this: quote a 1/4" gypsum board, or respec the layover
    # on the 1/2" SecureShield HD that is already priced. Note a perlite
    # recover board is NOT allowed under a fully adhered single-ply, so the
    # cheap option is off the table for the two adhered layover packages.
    {"id": "ca_cover_quarter", "name": "Cover Board 1/4\" (Layover / Recover)", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste",
     "bullets": ["1/4\" high-density cover board installed over the existing roof", "Gives the new membrane a smooth, sound substrate without a tear-off", "Acts as the separation layer the membrane manufacturer requires over an existing roof"]},

    # ── Attachment
    # Bonding adhesive is an ADHERED-SYSTEM cost and is only in the adhered
    # packages. A mechanically fastened roof welds its seams and screws the
    # membrane down — it buys no field adhesive, and carrying this line at
    # $83/SQ on a fastened bid would add thousands of dollars of material that
    # never ships.
    #
    # Carlisle publishes the coverage rate this is derived from: Sure-Weld TPO
    # Bonding Adhesive covers ~60 sq ft of FINISHED surface per gallon, i.e.
    # 300 sq ft (3 SQ) per 5-gallon pail. 188.58 / 3 = 62.86 per SQ.
    # (GAF's equivalent pail worked out to 83.33 — this is the cheaper house.)
    {"id": "ca_adhesive", "name": "Bonding Adhesive (Fully Adhered Systems)", "unit": "SQ", "cost": 62.86, "measure": "comm_sq_waste",
     "bullets": ["Manufacturer-specified bonding adhesive at the published coverage rate"]},
    # EPDM does not weld. Its seams are spliced with primer and seam tape, so
    # an EPDM system buys a consumable that a TPO system simply does not have.
    # SecureTape 3"x100' at 92.21 is 0.9221 per LF of splice. A 10'-wide sheet
    # puts a seam every 10 ft, which is ~10 LF of splice per SQ of roof, so
    # 9.22/SQ of tape. HP-250 primer at 51.26/gal is the ASSUMED part: taken at
    # 300 LF of 3" splice per gallon -> 1.71/SQ. Check the primer figure
    # against real usage on the first EPDM job and correct it here.
    {"id": "ca_epdm_seam", "name": "EPDM Seam Tape & Splice Primer", "unit": "SQ", "cost": 10.93, "measure": "comm_sq_waste",
     "bullets": ["Seams primed and spliced with Carlisle SecureTape"]},
    # Carlisle 90-8-30A: ~60 sq ft of finished surface per gallon, so 3 SQ per
    # 5-gallon pail. 188.58 / 3 = 62.86 per SQ — same rate as the TPO adhesive.
    {"id": "ca_epdm_adhesive", "name": "EPDM Bonding Adhesive (Fully Adhered Systems)", "unit": "SQ", "cost": 62.86, "measure": "comm_sq_waste",
     "bullets": ["Carlisle 90-8-30A bonding adhesive at the published coverage rate"]},
    # Superseded by the two zone-calculated fastener lines — it stays in the
    # catalog for old estimates but has nothing left to say on a card.
    {"id": "ca_fasteners", "name": "Plates & Fasteners (legacy - replaced by the zone calculator)", "unit": "SQ", "cost": 0, "measure": "comm_sq_waste",
     "bullets": []},
    # Counts come from commercial_fastening(): zone area x the density table.
    # TEAR-OFF, insulation: one fastener + one plate sized for the default 2.6"
    # iso + 1/2" cover board (about 3.1" of build-up, so a 4" fastener):
    #   Carlisle 4" InsulFast   240.19 / 1,000 = 0.24019
    #   Carlisle 3" Insul Plate 263.40 / 1,000 = 0.26340
    {"id": "ca_fast_insul", "name": "Insulation Fasteners & Plates", "unit": "EA", "cost": 0.50, "measure": "comm_fast_insul",
     "bullets": ["Insulation fastened at the wind-zone density the code requires"]},
    # TEAR-OFF, membrane seam: the heavy-duty screw and the seam plate, which
    # cost more than the insulation pair — the seam is what holds the roof on.
    #   Carlisle 4" HP fastener 311.63 / 1,000 = 0.31163
    #   Carlisle 2" seam plate  306.27 / 1,000 = 0.30627
    {"id": "ca_fast_seam", "name": "Membrane Seam Fasteners & Plates", "unit": "EA", "cost": 0.62, "measure": "comm_fast_seam",
     "bullets": ["Membrane seams fastened at the wind-zone density the code requires"]},
    # LAYOVER fasteners are longer and cost more: they pass through the cover
    # board AND the entire existing roof to reach the deck. Priced at a 5"
    # screw, which suits roughly 3-4" of existing build-up:
    #   Carlisle 5" InsulFast   294.15 / 1,000 = 0.29415  (+ 3" plate 0.26340)
    #   Carlisle 5" HP fastener 409.07 / 1,000 = 0.40907  (+ 2" seam plate 0.30627)
    # A thicker existing assembly needs a longer screw again and the price
    # climbs the whole way, so check the core cut before ordering.
    {"id": "ca_fast_cover", "name": "Cover Board Fasteners & Plates (Layover)", "unit": "EA", "cost": 0.56, "measure": "comm_fast_insul",
     "bullets": ["Cover board fastened through the existing roof into the deck at the wind-zone density"]},
    {"id": "ca_fast_seam_lo", "name": "Membrane Seam Fasteners & Plates (Layover)", "unit": "EA", "cost": 0.72, "measure": "comm_fast_seam",
     "bullets": ["Membrane seams fastened through to the deck at the wind-zone density"]},

    # ── Perimeter — both fabricated in-house from Carlisle TPO coated metal,
    # 4'x10' at $383.65/sheet. Yield depends on the girth of the profile being
    # bent, which is why these two differ so much:
    #   edge/drip, ~8" girth  -> 6 strips x 10' = 60 LF/sheet -> 6.39/LF
    #   coping,   ~18" girth  -> 2 strips x 10' = 20 LF/sheet -> 19.18/LF
    # A parapet wider than ~16" needs a bigger girth and a new number.
    {"id": "ca_edge", "name": "Edge Metal / Drip", "unit": "LF", "cost": 6.39, "measure": "comm_perimeter",
     "bullets": ["New edge metal around the full perimeter"]},
    {"id": "ca_coping", "name": "Coping Cap", "unit": "LF", "cost": 19.18, "measure": "comm_parapet",
     "bullets": ["New coping cap on the parapet walls"]},
    # Carlisle termination bar 1"x10', 10' per piece at $18.52 -> 1.85/LF.
    {"id": "ca_termbar", "name": "Termination Bar / Wall Flashing", "unit": "LF", "cost": 1.85, "measure": "comm_parapet",
     "bullets": ["New termination bar and wall flashing"]},
    # ── Details
    # Carlisle TPO universal pipe boot, $42.41 each. (The EPDM equivalent, the
    # 1"-6" PS molded pipe seal, is $56.41 — swap it in on an EPDM job.)
    {"id": "ca_pipe_flash", "name": "Penetration Flashing / Pipe Boot", "unit": "EA", "cost": 42.41, "measure": "comm_penetrations",
     "bullets": ["Every penetration flashed and sealed"]},
    # NEITHER sheet has a retrofit drain, and Carlisle quoted no scupper at
    # all, so this is still the GAF scupper price (4"x6"x12" at $240.43) off
    # the EXPIRED sheet. A cast retrofit drain is a different part and a
    # different number — get it quoted, or price it per job.
    {"id": "ca_drain", "name": "Drain Assembly / Retrofit Drain", "unit": "EA", "cost": 240.43, "measure": "comm_drains",
     "bullets": ["Roof drains flashed and tied into the new membrane"]},
    # Curb flashing is fabricated, not bought, so this is built from the
    # flashing membrane: Carlisle 060 24"x50' at $440.40 = $8.81/LF, times a
    # 24 LF perimeter (a typical 8'x4' HVAC curb) = $211.44. Bigger curbs cost
    # more — this is a typical-curb allowance, not a measured quantity.
    {"id": "ca_curb", "name": "Curb Flashing (HVAC / Skylight)", "unit": "EA", "cost": 211.44, "measure": "comm_curbs",
     "bullets": ["HVAC and skylight curbs flashed"]},
    # Carlisle molded sealant pocket at $44.96, plus one pouch of one-part
    # pourable sealer (4-pouch carton $67.94 -> $16.99/pouch) = $61.95.
    {"id": "ca_pitchpan", "name": "Pitch Pan", "unit": "EA", "cost": 61.95, "measure": "comm_pitch_pans",
     "bullets": ["Pitch pans set and sealed at odd penetrations"]},
    # Cut from a Carlisle 34"x50' TPO walkway roll at $650.59 — ten 5' pads
    # per roll. On an EPDM job the pre-made 30"x30" PS walkpad is $42.27 and
    # needs no cutting; swap it in there.
    {"id": "ca_walkway", "name": "Walkway Pad", "unit": "EA", "cost": 65.06, "measure": "comm_walkway_pads",
     "bullets": ["Walkway pads at access points and service areas"]},

    # ── Labor. One line per package, because tearing a roof off is not the
    # same job as laying over it and welding TPO is not the same job as taping
    # EPDM. All eight are measured on comm_labor_reroof, so comm_work_type
    # still zeroes them on a new-construction bid.
    #
    # The four TEAR-OFF lines carry the existing $400/SQ Project One standard
    # as their starting number — that rate was set for tear-off work, so this
    # is the status quo rather than a new guess. The four LAYOVER lines are 0
    # and MUST be filled in before a layover is quoted; see the seeded-cost
    # test, which names them explicitly so the list shrinks as they land.
    {"id": "cl_tpo_to_mf", "name": "Tear-Off, Disposal & Install Labor - TPO Mechanically Fastened", "unit": "SQ", "cost": 400, "measure": "comm_labor_reroof",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "cl_tpo_to_fa", "name": "Tear-Off, Disposal & Install Labor - TPO Fully Adhered", "unit": "SQ", "cost": 400, "measure": "comm_labor_reroof",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "cl_epdm_to_mf", "name": "Tear-Off, Disposal & Install Labor - EPDM Mechanically Fastened", "unit": "SQ", "cost": 400, "measure": "comm_labor_reroof",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "cl_epdm_to_fa", "name": "Tear-Off, Disposal & Install Labor - EPDM Fully Adhered", "unit": "SQ", "cost": 400, "measure": "comm_labor_reroof",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    {"id": "cl_tpo_lo_mf", "name": "Layover Prep & Install Labor - TPO Mechanically Fastened", "unit": "SQ", "cost": 0, "measure": "comm_labor_reroof",
     "bullets": ["Existing roof prepped and cut to manufacturer requirement, then the new system installed by Project One crews"]},
    {"id": "cl_tpo_lo_fa", "name": "Layover Prep & Install Labor - TPO Fully Adhered", "unit": "SQ", "cost": 0, "measure": "comm_labor_reroof",
     "bullets": ["Existing roof prepped and cut to manufacturer requirement, then the new system installed by Project One crews"]},
    {"id": "cl_epdm_lo_mf", "name": "Layover Prep & Install Labor - EPDM Mechanically Fastened", "unit": "SQ", "cost": 0, "measure": "comm_labor_reroof",
     "bullets": ["Existing roof prepped and cut to manufacturer requirement, then the new system installed by Project One crews"]},
    {"id": "cl_epdm_lo_fa", "name": "Layover Prep & Install Labor - EPDM Fully Adhered", "unit": "SQ", "cost": 0, "measure": "comm_labor_reroof",
     "bullets": ["Existing roof prepped and cut to manufacturer requirement, then the new system installed by Project One crews"]},
    # The original single re-roof rate. Kept so estimates written against it
    # still load and still price; new bids use the per-package lines above.
    {"id": "cl_labor_reroof", "name": "Tear-Off, Disposal & Install Labor (Re-Roof)", "unit": "SQ", "cost": 400, "measure": "comm_labor_reroof",
     "bullets": ["Installed by Project One crews to manufacturer spec"]},
    # New construction rides in the tear-off packages only — there is nothing
    # to lay over on a building that has never been roofed.
    {"id": "cl_labor_new", "name": "Install Labor (New Construction)", "unit": "SQ", "cost": 250, "measure": "comm_labor_new",
     "bullets": []},

    # ── Misc — manual quantities, job-specific, so they stay unpriced.
    # Freight belongs here: Carlisle excludes freight, fuel and material
    # surcharges and hazmat fees, and charges $200 per truck out of the
    # warehouse.
    {"id": "cx_misc", "name": "Misc Accessories (lap sealant, pitch pans, pads)", "unit": "LS", "cost": 0,
     "bullets": []},
    {"id": "cx_freight", "name": "Material Freight / Delivery", "unit": "LS", "cost": 0,
     "bullets": ["Material delivered to the site"]},
    {"id": "cx_crane", "name": "Crane / Equipment", "unit": "LS", "cost": 0,
     "bullets": ["Crane and equipment for rooftop material handling"]},
    # Rides in every layover package: GAF strongly recommends a survey before
    # any recover and REQUIRES one where perlite or wood fibre stays in the
    # assembly, and it is what tells you whether a layover is legal at all.
    {"id": "cx_moisture_survey", "name": "Moisture Survey / Infrared Scan", "unit": "LS", "cost": 0,
     "bullets": ["Existing roof scanned for trapped moisture before any recover work begins"]},
    {"id": "cx_permit", "name": "Permit", "unit": "LS", "cost": 0,
     "bullets": ["Permit pulled and final inspection scheduled"]},
]
# The alternate insulation thicknesses share every field but name and cost.
COMMERCIAL_CATALOG_SEED.extend(
    {"id": _iid, "name": _iname, "unit": "SQ", "cost": _icost, "measure": "comm_sq_waste",
     "bullets": ["Polyiso insulation to the specified R-value"]}
    for _iid, _iname, _icost, _isrc in COMMERCIAL_ISO_SEED)
# Tapered panels carry no measure: the layout decides the count, not the area.
COMMERCIAL_CATALOG_SEED.extend(
    {"id": _tid, "name": _tname, "unit": "PC", "cost": _tcost,
     "bullets": ["Tapered polyiso to the engineered layout, sloping the roof to drain"]}
    for _tid, _tname, _tcost in COMMERCIAL_TAPERED_SEED)

# ── Package build-ups ───────────────────────────────────────────────────
# Perimeter, details and the lump sums are the same on all eight.
_C_DETAIL = ["ca_edge", "ca_coping", "ca_termbar", "ca_pipe_flash", "ca_drain",
             "ca_curb", "ca_pitchpan", "ca_walkway", "cx_misc", "cx_freight", "cx_permit"]
# Tear-off builds a new thermal assembly and can also serve new construction.
_C_TEAROFF = ["ca_iso", "ca_cover", "ca_fast_insul"]
# Layover keeps the existing insulation and adds the 1/4" board over the top.
# The survey is what establishes the recover is permitted in the first place.
_C_LAYOVER = ["ca_cover_quarter", "ca_fast_cover", "cx_moisture_survey"]


def _c_pkg(membrane, base, labor, *, fastened, epdm=False):
    """One commercial package's product list, in installed order.

    ADHESIVE is what actually differs by attachment: a mechanically fastened
    system buys none, and the adhesive line has no measurement gating it, so
    carrying it on a fastened bid would price thousands of dollars of material
    that never ships.

    The SEAM FASTENER line ships on every package regardless — same idiom as
    the two labor lines. comm_seam_attach (resolved from the membrane's attach
    tag) zeroes it on an adhered system, and a zero-quantity item never prices
    and never shows. Keeping it present means a rep who swaps an adhered
    package's membrane for a fastened one gets the screws priced instead of
    silently bidding a roof with nothing holding it down.

    EPDM adds seam tape either way, because EPDM splices rather than welds."""
    layover = "ca_fast_cover" in base
    ids = [membrane] + list(base)
    ids.append("ca_fast_seam_lo" if layover else "ca_fast_seam")
    if not fastened:
        ids.append("ca_epdm_adhesive" if epdm else "ca_adhesive")
    if epdm:
        ids.append("ca_epdm_seam")
    ids += _C_DETAIL + [labor]
    if "ca_iso" in base:          # tear-off packages can also bid new construction
        ids.append("cl_labor_new")
    return ids


_CS_EXTRA = ["5-year Project One workmanship warranty"]
_LAYOVER_EXTRA = _CS_EXTRA + [
    "No tear-off - the building stays dry and in operation throughout",
]
COMMERCIAL_BUNDLES_SEED = [
    # ── TPO, tear-off
    {"id": "cb_tpo_ma", "name": "TPO - Tear-Off, Mechanically Fastened",
     "product_ids": _c_pkg("cm_tpo_ma", _C_TEAROFF, "cl_tpo_to_mf", fastened=True),
     "description": "Full tear-off to the deck, new polyiso and cover board, 60-mil TPO mechanically fastened and hot-air welded - the workhorse commercial flat roof.",
     "extra_features": _CS_EXTRA},
    {"id": "cb_tpo_fa", "name": "TPO - Tear-Off, Fully Adhered",
     "product_ids": _c_pkg("cm_tpo_fa", _C_TEAROFF, "cl_tpo_to_fa", fastened=False),
     "description": "Full tear-off to the deck, new polyiso and cover board, 60-mil TPO fully adhered - smooth finished surface and the highest wind uplift rating.",
     "extra_features": _CS_EXTRA},
    # ── TPO, layover
    {"id": "cb_tpo_lo_mf", "name": "TPO - Layover, Mechanically Fastened",
     "product_ids": _c_pkg("cm_tpo_ma", _C_LAYOVER, "cl_tpo_lo_mf", fastened=True),
     "description": "1/4\" cover board over the existing roof, then 60-mil TPO mechanically fastened and hot-air welded - no tear-off, no disposal, and the building stays in operation.",
     "extra_features": _LAYOVER_EXTRA},
    {"id": "cb_tpo_lo_fa", "name": "TPO - Layover, Fully Adhered",
     "product_ids": _c_pkg("cm_tpo_fa", _C_LAYOVER, "cl_tpo_lo_fa", fastened=False),
     "description": "1/4\" cover board over the existing roof, then 60-mil TPO fully adhered - no tear-off, and a smooth finished surface with no fastener pattern.",
     "extra_features": _LAYOVER_EXTRA},
    # ── EPDM, tear-off
    {"id": "cb_epdm_mf", "name": "EPDM - Tear-Off, Mechanically Fastened",
     "product_ids": _c_pkg("cm_epdm_mf", _C_TEAROFF, "cl_epdm_to_mf", fastened=True, epdm=True),
     "description": "Full tear-off to the deck, new polyiso and cover board, 60-mil reinforced EPDM mechanically fastened with taped seams.",
     "extra_features": _CS_EXTRA},
    {"id": "cb_epdm", "name": "EPDM - Tear-Off, Fully Adhered",
     "product_ids": _c_pkg("cm_epdm_fa", _C_TEAROFF, "cl_epdm_to_fa", fastened=False, epdm=True),
     "description": "Full tear-off to the deck, new polyiso and cover board, 60-mil EPDM fully adhered - the longest field track record in low-slope roofing.",
     "extra_features": _CS_EXTRA},
    # ── EPDM, layover
    {"id": "cb_epdm_lo_mf", "name": "EPDM - Layover, Mechanically Fastened",
     "product_ids": _c_pkg("cm_epdm_mf", _C_LAYOVER, "cl_epdm_lo_mf", fastened=True, epdm=True),
     "description": "1/4\" cover board over the existing roof, then 60-mil reinforced EPDM mechanically fastened with taped seams - no tear-off, and the board keeps the rubber off any asphalt below.",
     "extra_features": _LAYOVER_EXTRA},
    {"id": "cb_epdm_lo_fa", "name": "EPDM - Layover, Fully Adhered",
     "product_ids": _c_pkg("cm_epdm_fa", _C_LAYOVER, "cl_epdm_lo_fa", fastened=False, epdm=True),
     "description": "1/4\" cover board over the existing roof, then 60-mil EPDM fully adhered - no tear-off, and the board keeps the rubber off any asphalt below.",
     "extra_features": _LAYOVER_EXTRA},
    # ── Still offered, still unpriced, not part of the eight-package matrix.
    # Built explicitly rather than through _c_pkg: mod-bit is torched or mopped,
    # so it buys neither the TPO bonding adhesive nor any seam fastener.
    {"id": "cb_modbit", "name": "Modified Bitumen (2-Ply)",
     "product_ids": (["cm_modbit"] + _C_TEAROFF + ["ca_fast_seam"] + _C_DETAIL
                     + ["cl_labor_reroof", "cl_labor_new"]),
     "description": "Two-ply modified bitumen base and cap sheet - redundant waterproofing for high-traffic roofs.",
     "extra_features": _CS_EXTRA},
    {"id": "cb_coating", "name": "Silicone Restoration Coating",
     "product_ids": (["cm_coating", "ca_fast_insul", "ca_fast_seam"]
                     + _C_DETAIL + ["cl_labor_reroof"]),
     "description": "Silicone restoration coating over the existing roof - extends service life without a tear-off.",
     "extra_features": _CS_EXTRA},
]
# The honest ladder: layover is the cheapest way to a new roof, tear-off
# mechanically fastened is the standard, tear-off fully adhered is the top.
# NOTE the Good tier is a layover, whose LABOR rate is still 0 — Good/Better/
# Best mode will under-bid until that number lands. Simple mode (the commercial
# default) sells cb_tpo_ma and is fully priced today.
COMMERCIAL_TIER_DEFAULTS_SEED = {"good": "cb_tpo_lo_mf", "better": "cb_tpo_ma",
                                 "best": "cb_tpo_fa"}
# The system loaded when the trade is in single-price (simple) mode, which is
# how a commercial bid sells by default.
COMMERCIAL_SIMPLE_DEFAULT = "cb_tpo_ma"

# Windows catalog. Costs are PLACEHOLDERS — the manager sets real numbers in
# Price Book -> Windows -> Products. Simonton is the value tier, ProVia is the
# step-up (Endure vinyl) and premium (Aeris fiberglass).
WINDOWS_CATALOG_SEED = [
    {"id": "w_simonton", "name": "Simonton Reflections 5500 Double-Pane Low-E", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["Simonton Reflections 5500 vinyl window",
                 "Double-pane insulated glass with Low-E coating and argon gas fill",
                 "Fusion-welded frame and sash for a tight, weather-resistant seal",
                 "Energy Star certified for Colorado's climate zone",
                 "Double lifetime limited manufacturer warranty"]},
    {"id": "w_provia_endure", "name": "ProVia Endure Vinyl Double-Pane Low-E", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["ProVia Endure premium vinyl window",
                 "Double-pane insulated glass with Low-E coating and argon gas fill",
                 "Heavier-gauge vinyl frame with foam-insulated chambers for a tighter U-factor",
                 "Wider color palette and interior wood-grain options",
                 "Lifetime limited manufacturer warranty"]},
    {"id": "w_provia_aeris", "name": "ProVia Aeris Fiberglass Triple-Pane Low-E", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["ProVia Aeris fiberglass window with a real-wood interior",
                 "Triple-pane insulated glass with Low-E coatings and argon gas fill",
                 "Fiberglass frame expands and contracts at the same rate as the glass — no seal fatigue",
                 "The highest energy performance available for Colorado's climate extremes",
                 "Premium sound reduction and maximum insulation value",
                 "Lifetime limited manufacturer warranty"]},
    {"id": "wa_wrap", "name": "Flashing Tape & Window Wrap", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["Self-adhered flashing tape and window wrap at every rough opening for a fully sealed installation"]},
    {"id": "wa_trim", "name": "Window Trim Kit", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["Interior and exterior trim kit at every window"]},
    {"id": "wa_casing", "name": "Exterior Casing", "unit": "LF", "cost": 0,
     "bullets": ["Exterior casing with proper flashing integration"]},
    {"id": "wa_screens", "name": "Insect Screens", "unit": "EA", "cost": 0, "measure": "windows",
     "bullets": ["Full insect screens on every operable window"]},
    # Labor prices into the package but is NOT broken out for the customer,
    # same rule as siding labor — the ROW is hidden but the promise is not.
    {"id": "wl_removal", "name": "Removal & Disposal Labor", "unit": "EA", "cost": 0, "measure": "windows",
     "customer_visible": False,
     "bullets": ["Careful removal and disposal of the existing windows"]},
    {"id": "wl_install", "name": "Install Labor", "unit": "EA", "cost": 0, "measure": "windows",
     "customer_visible": False,
     "bullets": ["Installed level, plumb, and square with full foam insulation and air sealing per manufacturer spec"]},
    {"id": "wx_dumpster", "name": "Dumpster", "unit": "LS", "cost": 0,
     "bullets": ["Dumpster and full site cleanup"]},
    {"id": "wx_permit", "name": "Permit", "unit": "LS", "cost": 0,
     "bullets": ["Permit pulled and final inspection scheduled"]},
]
_WS = ["wa_wrap", "wa_trim", "wa_casing", "wa_screens", "wl_removal", "wl_install",
       "wx_dumpster", "wx_permit"]
_WS_EXTRA = ["5-year Project One workmanship warranty"]
WINDOWS_BUNDLES_SEED = [
    {"id": "wb_simonton", "name": "Simonton Reflections", "product_ids": ["w_simonton"] + _WS,
     "description": "Simonton Reflections 5500 vinyl — Energy Star Low-E performance backed by a double lifetime warranty.",
     "extra_features": _WS_EXTRA},
    {"id": "wb_provia_endure", "name": "ProVia Endure", "product_ids": ["w_provia_endure"] + _WS,
     "description": "ProVia Endure premium vinyl — heavier-gauge frame, deeper color options, and a step up in energy performance.",
     "extra_features": _WS_EXTRA},
    {"id": "wb_provia_aeris", "name": "ProVia Aeris", "product_ids": ["w_provia_aeris"] + _WS,
     "description": "ProVia Aeris fiberglass with triple-pane Low-E — the highest energy performance and quietest interior available.",
     "extra_features": _WS_EXTRA},
]
WINDOWS_TIER_DEFAULTS_SEED = {"good": "wb_simonton", "better": "wb_provia_endure",
                              "best": "wb_provia_aeris"}

# trade -> (catalog seed, bundle seed, tier-default seed). Mirrored client-side
# by BUNDLE_TRADES in app.js — keep the two lists in sync.
BUNDLE_SEEDS = {
    'roofing': (ROOFING_CATALOG_SEED, ROOFING_BUNDLES_SEED, ROOFING_TIER_DEFAULTS_SEED),
    'siding':  (SIDING_CATALOG_SEED,  SIDING_BUNDLES_SEED,  SIDING_TIER_DEFAULTS_SEED),
    'windows': (WINDOWS_CATALOG_SEED, WINDOWS_BUNDLES_SEED, WINDOWS_TIER_DEFAULTS_SEED),
    'commercial': (COMMERCIAL_CATALOG_SEED, COMMERCIAL_BUNDLES_SEED, COMMERCIAL_TIER_DEFAULTS_SEED),
}
# trade -> bundle id used when the trade prices as a single package.
SIMPLE_BUNDLE_DEFAULTS = {'commercial': COMMERCIAL_SIMPLE_DEFAULT}

def _est_is_layover(est):
    """True when the commercial package being sold is a recover rather than a
    tear-off. The 1/4" cover board is the layover assembly, so its presence is
    the test — a manager's custom recover package answers correctly too."""
    td = (est.get('trades') or {}).get('commercial') or {}
    return any((it or {}).get('catalog_id') == 'ca_cover_quarter'
               for it in (td.get('line_items') or []))


# Rep-entered commercial complexity flags. Display only — these never price.
# MUST mirror COMM_FLAGS in app.js (the keys are what the estimate stores).
COMM_FLAG_LABELS = [
    ('penetrations_10plus', '10+ penetrations'),
    ('levels_3plus', '3+ roof levels / sections'),
    ('expansion_joints', 'Expansion joints'),
    ('heavy_hvac', 'Heavy rooftop HVAC'),
    # These two are what rule a LAYOVER out. Either one means the job is a
    # tear-off no matter what the customer would prefer to pay for.
    ('existing_layers_2plus', 'Existing roof already has 2+ coverings (no recover)'),
    ('wet_insulation', 'Ponding / suspected wet insulation'),
]


def _copy_seed_bundle(b):
    """Deep-enough copy so an edited response can never mutate the seed constant."""
    return dict(b, product_ids=list(b['product_ids']),
                extra_features=list(b.get('extra_features') or []))


def _copy_seed_product(p):
    """Same, for a catalog product — list fields (`bullets`, `colors`, `styles`)
    are deep-copied so the response can't mutate the seed constant. `colors`
    and `styles` are lists of dicts, so a shallow list() would still alias the
    inner dicts and a client PUT could edit the seed."""
    return copy.deepcopy(p)


# Customer-facing copy the server may fill in on a bundle the manager already owns.
# The What's Included bullets are NOT here — they are built from the bundle's
# products (bundleFeatures() in app.js), so `extra_features` only carries the
# bullets no product owns. A live book's legacy `features` blob is left alone and
# no longer read; the Price Book editor writes `extra_features` now.
_BUNDLE_COPY_FIELDS = ('description', 'extra_features')

# Product facts (not prices) the server may backfill onto a catalog product a
# manager already has. `attach` decides whether a system gets seam fasteners;
# `bullets` is the product's line(s) on a Good/Better/Best card;
# `customer_visible` is whether the priced row is broken out for the customer
# (labor is not) — a book saved before that call was made has no such key, and
# absence is the test, so a manager who ticked Show keeps it ticked.
# `measure` is safe to backfill because of the manual-qty contract: an explicit
# `measure: ''` is the manager choosing Manual and STAYS empty (the field is
# present, so absence-is-the-test skips it), while a missing key means the
# product predates the measurement and should adopt the seed's. Without it the
# live Fascia product — seeded long before a fascia measurement existed — keeps
# no measure and the Scope field it was added for silently fills nothing.
_PRODUCT_BACKFILL_FIELDS = ('attach', 'bullets', 'customer_visible', 'measure',
                            'group', 'colors', 'styles')

# Trades whose seeded costs are allowed to fill a live book's ZERO cost. See the
# backfill in _ensure_bundle_catalogs for why this is narrow and one-directional.
# Only commercial qualifies: it is the one catalog that shipped entirely
# unpriced by design, so a 0 there means "no supplier sheet yet", not a price.
_SEED_COST_BACKFILL_TRADES = {'commercial'}

# old product id -> the product(s) that replaced it, swapped into SEEDED bundles
# on read. The old product stays in the catalog: an estimate may reference it and
# the manager may have priced it.
_PRODUCT_SUPERSEDED = {'ca_fasteners': ['ca_fast_insul', 'ca_fast_seam']}

# Seed bundles that shipped AFTER their trade already had saved price books, so
# _ensure_bundle_catalogs must append them by id rather than assume a missing id
# means the manager deleted it. See the append loop for the full reasoning.
# The 2026 QXO siding line: production's volume already carried siding_bundles,
# so without this the six new bundles reached nobody.
_LATE_BUNDLE_IDS = {'b_lp_standard', 'b_lp_expert', 'b_hardie_primed',
                    'b_hardie_statement', 'b_edco_d4', 'b_edco_8',
                    # The layover and EPDM packages added when the commercial
                    # menu was restructured into the tear-off/layover matrix.
                    # Same trap: a book saved with the original five commercial
                    # bundles would otherwise never see them.
                    #
                    # Their LABOR lines deliberately do NOT go through
                    # _LATE_BUNDLE_PRODUCTS. A saved cb_tpo_ma still carries the
                    # old cl_labor_reroof, and appending cl_tpo_to_mf next to it
                    # would bill the tear-off twice.
                    'cb_tpo_lo_mf', 'cb_tpo_lo_fa',
                    'cb_epdm_mf', 'cb_epdm_lo_mf', 'cb_epdm_lo_fa'}

# Product ids added to a SEEDED bundle after the trade already had live books.
# Same trap as _LATE_BUNDLE_IDS but one level down: _BUNDLE_COPY_FIELDS does
# not include product_ids (managers customize them), so changing a seed
# bundle's product_ids never reaches production. This list forces specific
# products into specific seeded bundles when they're missing.
#
# Use it for MANUFACTURER-REQUIRED accessories (kickout flashing, sealant,
# paint on primed bundles, rot repair) — anything a bundle shouldn't be able
# to ship without. Do NOT use it to reintroduce discretionary items a manager
# may have deliberately removed. Drop an entry once live books have been
# saved past it.
#
# 2026-07-31: the QXO siding bundles shipped missing the accessories the
# manufacturer warranty depends on. This backfills them onto the six existing
# seeded bundles in production.
_LATE_BUNDLE_PRODUCTS = {
    'b_lp_standard':      ['sa_starter', 'sa_kickout', 'sa_sealant', 'sa_paint', 'sa_rot_repair'],
    'b_lp_expert':        ['sa_starter', 'sa_kickout', 'sa_sealant', 'sa_touchup', 'sa_rot_repair'],
    'b_hardie_primed':    ['sa_starter', 'sa_kickout', 'sa_sealant', 'sa_paint', 'sa_rot_repair'],
    'b_hardie_statement': ['sa_starter', 'sa_kickout', 'sa_sealant', 'sa_touchup', 'sa_rot_repair'],
    'b_edco_d4':          ['sa_corner_in', 'sa_kickout', 'sa_sealant', 'sa_edco_fasteners', 'sa_rot_repair'],
    'b_edco_8':           ['sa_corner_in', 'sa_kickout', 'sa_sealant', 'sa_edco_fasteners', 'sa_rot_repair'],
}

# Visual-only exterior-door catalogue. It deliberately sits outside the
# estimating catalog: choosing a door here changes the customer preview, not
# scope or pricing. A manager may replace this list in the saved price book
# with the exact door lines the company carries.
# Names verified against ProVia's current entry-door and paint-options pages
# (2026-08-26). Hex values are approximate preview colors, not manufacturer
# colorimetry. Series choices do NOT specify panel/glass/hardware, so never
# apply the old generic repeating panel textures as if they were ProVia art.
# https://www.provia.com/doors/entry-doors/
# https://www.provia.com/doors/paint-options/
_PROVIA_PREVIEW_COLORS = [
    {'name': 'Snow Mist', 'hex': '#e9e7e1'},
    {'name': 'Coal Black', 'hex': '#242424'},
    {'name': 'Nightfall', 'hex': '#47494a'},
    {'name': 'Rustic Bronze', 'hex': '#51463c'},
    {'name': 'Geneva Blue', 'hex': '#425970'},
    {'name': 'Forest Green', 'hex': '#334c40'},
    {'name': 'Autumn Red', 'hex': '#823d36'},
]
EXTERIOR_DOOR_OPTIONS_SEED = [
    {'id': 'provia-signet', 'name': 'ProVia Signet — Fiberglass',
     'brand': 'ProVia', 'series': 'Signet', 'preview_only': True, 'pattern_id': '',
     'colors': _PROVIA_PREVIEW_COLORS},
    {'id': 'provia-ascent', 'name': 'ProVia Ascent — Fiberglass',
     'brand': 'ProVia', 'series': 'Ascent', 'preview_only': True, 'pattern_id': '',
     'colors': _PROVIA_PREVIEW_COLORS},
    {'id': 'provia-legacy', 'name': 'ProVia Legacy — Steel',
     'brand': 'ProVia', 'series': 'Legacy', 'preview_only': True, 'pattern_id': '',
     'colors': _PROVIA_PREVIEW_COLORS},
]


# Manager-maintained Design Studio catalog.  This deliberately remains a
# visual catalog instead of pretending that a color swatch is a priced scope
# item.  `price_book_bundle` is an optional link back to the estimate's real
# bundle; choosing a look never changes that bundle or its price.
EXTERIOR_CATALOG_CATEGORIES = {'roof', 'siding', 'door', 'paint'}
EXTERIOR_CATALOG_SURFACES = {'siding', 'door'}
EXTERIOR_PATTERN_IDS = {'', 'lap', 'bnb', 'board_batten', 'shake', 'panel',
                        'vertical', 'nickel_gap'}
_EXTERIOR_PATTERN_ALIASES = {'board_batten': 'bnb', 'vertical': 'panel'}
_LP_EXPERTFINISH_EXTERIOR_MIGRATION = 'lp-expertfinish-lpef01884-2025'


def _exterior_text(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _exterior_bool(value, default=True):
    if isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    if not text:
        return default
    return text not in {'0', 'false', 'no', 'off', 'inactive'}


def _exterior_pattern(style, supplied=''):
    supplied = _exterior_text(supplied, 32).lower()
    if supplied in EXTERIOR_PATTERN_IDS:
        return _EXTERIOR_PATTERN_ALIASES.get(supplied, supplied)
    style_l = _exterior_text(style, 80).lower()
    if 'nickel' in style_l and 'gap' in style_l:
        return 'nickel_gap'
    if 'board' in style_l and 'batten' in style_l:
        return 'bnb'
    if 'shake' in style_l or 'shingle' in style_l:
        return 'shake'
    if 'vertical' in style_l or 'panel' in style_l:
        return 'panel'
    if 'lap' in style_l or 'clapboard' in style_l:
        return 'lap'
    return ''


def _exterior_ids(entry):
    product_parts = [entry[k].lower() for k in
                     ('category', 'brand', 'product', 'applies_to', 'price_book_bundle')]
    # Siding exposes profile as its own dropdown. Roof type, door model and
    # paint sheen are part of the product choice, so keep those as distinct
    # products instead of hiding them in a picker that does not exist.
    if entry['category'] != 'siding':
        product_parts.append(entry['style'].lower())
    product_id = 'extp_' + hashlib.sha1('|'.join(product_parts).encode()).hexdigest()[:14]
    color_parts = product_parts + [entry['style'].lower(), entry['color'].lower(),
                                   entry['color_code'].lower()]
    entry_id = 'ext_' + hashlib.sha1('|'.join(color_parts).encode()).hexdigest()[:16]
    return product_id, entry_id


def _normalize_exterior_entry(raw, row_number=None):
    if not isinstance(raw, dict):
        raise ValueError(f'Row {row_number or "?"}: expected an object')
    aliases = {'roofing': 'roof', 'roofs': 'roof', 'doors': 'door',
               'paints': 'paint'}
    category = aliases.get(_exterior_text(raw.get('category'), 20).lower(),
                           _exterior_text(raw.get('category'), 20).lower())
    if category not in EXTERIOR_CATALOG_CATEGORIES:
        raise ValueError(f'Row {row_number or "?"}: category must be roof, siding, door, or paint')
    product = _exterior_text(raw.get('product') or raw.get('series'), 120)
    color = _exterior_text(raw.get('color') or raw.get('color_name'), 80)
    if not product:
        raise ValueError(f'Row {row_number or "?"}: product is required')
    if not color:
        raise ValueError(f'Row {row_number or "?"}: color is required')
    hex_color = _exterior_text(raw.get('hex') or raw.get('color_hex'), 7).lower()
    if not re.fullmatch(r'#[0-9a-f]{6}', hex_color):
        raise ValueError(f'Row {row_number or "?"}: hex must look like #1a2b3c')
    applies = _exterior_text(raw.get('applies_to'), 20).lower()
    if category == 'paint':
        applies = applies or 'siding'
        if applies not in EXTERIOR_CATALOG_SURFACES:
            raise ValueError(f'Row {row_number or "?"}: paint applies_to must be siding or door')
    else:
        applies = {'roof': 'roof', 'siding': 'siding', 'door': 'door'}[category]
    entry = {
        'category': category,
        'brand': _exterior_text(raw.get('brand'), 80),
        'product': product,
        'style': _exterior_text(raw.get('style') or raw.get('profile'), 80),
        'color': color,
        'color_code': _exterior_text(raw.get('color_code') or raw.get('code'), 40),
        'hex': hex_color,
        'applies_to': applies,
        'pattern_id': _exterior_pattern(raw.get('style') or raw.get('profile'), raw.get('pattern_id')),
        'price_book_bundle': _exterior_text(
            raw.get('price_book_bundle') or raw.get('bundle_id'), 100),
        'active': _exterior_bool(raw.get('active'), True),
    }
    product_id, entry_id = _exterior_ids(entry)
    # Recompute identities from the editable fields. If a manager renames a
    # product/style it becomes a new visual choice; old estimates keep their
    # saved labels and colors instead of silently pointing at a different SKU.
    entry['product_id'] = product_id
    entry['id'] = entry_id
    return entry


def _normalize_exterior_catalog(rows):
    if not isinstance(rows, list):
        raise ValueError('entries must be a list')
    if len(rows) > 5000:
        raise ValueError('Exterior catalog is limited to 5,000 rows')
    normalized, seen = [], set()
    for i, raw in enumerate(rows, 1):
        entry = _normalize_exterior_entry(raw, i)
        key = (entry['category'], entry['brand'].lower(), entry['product'].lower(),
               entry['style'].lower(), entry['color'].lower(),
               entry['color_code'].lower(), entry['applies_to'],
               entry['price_book_bundle'])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def _lp_expertfinish_exterior_rows():
    """The 16 colors x five wall profiles shown in LP sheet LPEF01884."""
    return _normalize_exterior_catalog([
        {
            'category': 'siding', 'brand': 'LP SmartSide',
            'product': 'LP SmartSide ExpertFinish',
            'style': style['name'], 'pattern_id': style['pattern_id'],
            'color': color['name'], 'hex': color['hex'],
            'price_book_bundle': 'b_lp_expert', 'active': True,
        }
        for style in _LP_EXPERTFINISH_STYLES
        for color in _LP_EXPERTFINISH_COLORS
    ])


def _migrate_lp_expertfinish_visuals(pb):
    """Replace only the shipped generic LP placeholders with the 2025 sheet.

    A version marker makes manager deletions sticky after the updated book is
    saved. Custom LP rows and custom product metadata survive: only values that
    still exactly match our old seed are eligible for replacement.
    """
    versions = pb.get('exterior_catalog_seed_versions')
    if not isinstance(versions, list):
        versions = []
    if _LP_EXPERTFINISH_EXTERIOR_MIGRATION in versions:
        return

    live_product = next((p for p in pb.get('siding_catalog') or []
                         if isinstance(p, dict) and p.get('id') == 's_lp_expert'), None)
    if live_product is not None:
        if live_product.get('colors') == _SIDING_NEUTRAL_COLORS:
            live_product['colors'] = copy.deepcopy(_LP_EXPERTFINISH_COLORS)
        if live_product.get('styles') == _LP_STANDARD_STYLES:
            live_product['styles'] = copy.deepcopy(_LP_EXPERTFINISH_STYLES)

    legacy_colors = {c['name'].casefold() for c in _SIDING_NEUTRAL_COLORS}
    legacy_products = {'lp smartside expert finish', 'lp smartside expertfinish'}
    kept = []
    for row in _normalize_exterior_catalog(pb.get('exterior_catalog') or []):
        is_legacy = (
            row['category'] == 'siding'
            and row['brand'].casefold() == 'lp smartside'
            and row['product'].casefold() in legacy_products
            and row['price_book_bundle'] == 'b_lp_expert'
            and row['color'].casefold() in legacy_colors
        )
        if not is_legacy:
            kept.append(row)
    pb['exterior_catalog'] = _normalize_exterior_catalog(
        kept + _lp_expertfinish_exterior_rows())
    versions.append(_LP_EXPERTFINISH_EXTERIOR_MIGRATION)
    pb['exterior_catalog_seed_versions'] = versions


def _legacy_exterior_catalog(pb):
    """Flatten the shipped visual palettes into the same rows managers edit."""
    rows = []
    for trade, category, prefix in (('roofing', 'roof', 'm_'),
                                    ('siding', 'siding', 's_')):
        catalog = pb.get(trade + '_catalog') or []
        by_id = {p.get('id'): p for p in catalog if isinstance(p, dict)}
        for bundle in pb.get(trade + '_bundles') or []:
            material = None
            for pid in bundle.get('product_ids') or []:
                if not str(pid).startswith(prefix):
                    continue
                if trade == 'siding' and str(pid).startswith(('sa_', 'sl_', 'sx_')):
                    continue
                material = by_id.get(pid)
                if material:
                    break
            if not material:
                continue
            styles = material.get('styles') or [{}]
            for style in styles:
                for color in material.get('colors') or []:
                    if not isinstance(color, dict) or not color.get('name') or not color.get('hex'):
                        continue
                    rows.append({
                        'category': category,
                        'brand': material.get('group', ''),
                        'product': bundle.get('name') or material.get('name'),
                        'style': style.get('name', ''),
                        'pattern_id': style.get('pattern_id', ''),
                        'color': color.get('name'), 'hex': color.get('hex'),
                        'price_book_bundle': bundle.get('id', ''), 'active': True,
                    })
    for door in pb.get('exterior_doors') or []:
        for color in door.get('colors') or []:
            if isinstance(color, dict) and color.get('name') and color.get('hex'):
                rows.append({
                    'category': 'door', 'brand': door.get('brand', ''),
                    'product': door.get('series') or door.get('name'),
                    'style': door.get('style', ''), 'color': color.get('name'),
                    'hex': color.get('hex'), 'active': True,
                })
    return _normalize_exterior_catalog(rows)


def _ensure_bundle_catalogs(pb):
    """Inject each bundle trade's catalog/bundles/defaults into a price book that
    has none. Non-destructive (mutates the in-memory dict for the response only)."""
    for trade, (catalog, bundles, defaults) in BUNDLE_SEEDS.items():
        if not pb.get(trade + '_catalog'):
            pb[trade + '_catalog'] = [_copy_seed_product(p) for p in catalog]
            pb[trade + '_bundles'] = [_copy_seed_bundle(b) for b in bundles]
            pb[trade + '_tier_defaults'] = dict(defaults)
        else:
            pb.setdefault(trade + '_bundles', [])
            pb.setdefault(trade + '_tier_defaults', dict(defaults))
            # The live book predates a copy field (bundles shipped before
            # `features` existed), so backfill the seed's copy onto seeded
            # bundles that still lack it — otherwise new seed copy never
            # reaches a book that already has a catalog.
            #
            # Key ABSENCE is the test, never falsiness: a manager who clears a
            # bundle's bullets saves `features: []`, and the server must not
            # fight that on every GET. Same contract as the manual-measure rule.
            by_id = {b.get('id'): b for b in pb[trade + '_bundles'] if isinstance(b, dict)}
            for seed in bundles:
                live = by_id.get(seed['id'])
                if live is None:
                    continue        # manager deleted it — leave it deleted
                for field in _BUNDLE_COPY_FIELDS:
                    if field not in live and field in seed:
                        val = seed[field]
                        live[field] = copy.deepcopy(val) if isinstance(val, list) else val

            # ...but "absent" also covers a bundle the book has NEVER seen, and
            # the loop above cannot tell that apart from a deletion, so it skips
            # both. That means a bundle added to the seeds after books were
            # already saved can never reach them — the QXO siding line shipped
            # to production with every product present and no bundle to pick.
            #
            # Listing the late arrivals explicitly keeps deletion sticky for
            # everything else: an id is only appended while it is on this list,
            # so a manager who deletes one still gets it back until it ages off.
            # Add an id here when a seed bundle ships after its trade already
            # has live books; drop it once those books have been saved past it.
            for seed in bundles:
                if seed['id'] in _LATE_BUNDLE_IDS and seed['id'] not in by_id:
                    pb[trade + '_bundles'].append(_copy_seed_bundle(seed))

            # A price book saved before a seed product existed would never get
            # it: the branch above only runs when the catalog is ABSENT, and
            # PUT /api/pricebook persists whatever the client last saw. So
            # append seed products the live catalog is missing BY ID. A catalog
            # is a menu — an extra product nobody uses is harmless, whereas a
            # missing one silently drops its line from every bundle.
            live_cat = pb[trade + '_catalog']
            have = {p.get('id') for p in live_cat if isinstance(p, dict)}
            for seed_p in catalog:
                if seed_p['id'] not in have:
                    live_cat.append(_copy_seed_product(seed_p))
                    have.add(seed_p['id'])
                else:
                    # Backfill product facts added after the book was saved
                    # (e.g. `attach`, which decides whether a system gets seam
                    # fasteners, or `bullets`, the product's line on a package
                    # card). Absence is the test — a manager who cleared it
                    # keeps it cleared.
                    p_live = next(p for p in live_cat
                                  if isinstance(p, dict) and p.get('id') == seed_p['id'])
                    for field in _PRODUCT_BACKFILL_FIELDS:
                        if field in seed_p and field not in p_live:
                            val = seed_p[field]
                            p_live[field] = copy.deepcopy(val) if isinstance(val, list) else val
                    # Cost is deliberately NOT in _PRODUCT_BACKFILL_FIELDS —
                    # a saved cost is the manager's number and the seed must
                    # never overwrite it. The one exception is a product that
                    # shipped as an unpriced PLACEHOLDER: the whole commercial
                    # catalog was seeded at 0 pending a supplier sheet, so a
                    # live book holds 0 not because anyone chose 0 but because
                    # there was nothing to choose yet. Filling those in is the
                    # difference between the 2026-05-19 GAF numbers reaching
                    # production and the bid silently pricing no membrane.
                    #
                    # Strictly one-directional: 0 -> seed. A cost the manager
                    # actually set is never touched, and once they save the
                    # book their number sticks. Drop this block when the live
                    # books have all been saved past it.
                    if trade in _SEED_COST_BACKFILL_TRADES and _mnum(seed_p.get('cost')) > 0                             and _mnum(p_live.get('cost')) <= 0:
                        p_live['cost'] = seed_p['cost']

            # Seeded bundles that still carry a superseded product swap it for
            # its replacement(s). Manager-created bundles are left alone.
            for seed in bundles:
                live = by_id.get(seed['id'])
                if live is None or not isinstance(live.get('product_ids'), list):
                    continue
                for old, new_ids in _PRODUCT_SUPERSEDED.items():
                    if old in live['product_ids'] and old not in (seed.get('product_ids') or []):
                        at = live['product_ids'].index(old)
                        live['product_ids'][at:at + 1] = [
                            n for n in new_ids if n not in live['product_ids']]

            # Manufacturer-required accessories added to seeded bundles after
            # the trade already had live books. See _LATE_BUNDLE_PRODUCTS for
            # the contract — this appends listed products only when missing,
            # and only to bundles that still exist in the live book.
            for bundle_id, late_ids in _LATE_BUNDLE_PRODUCTS.items():
                live = by_id.get(bundle_id)
                if live is None or not isinstance(live.get('product_ids'), list):
                    continue
                for pid in late_ids:
                    if pid not in live['product_ids']:
                        live['product_ids'].append(pid)
    for trade, bundle_id in SIMPLE_BUNDLE_DEFAULTS.items():
        pb.setdefault(trade + '_simple_default', bundle_id)
    # Kept separate from saleable trade catalogs because the designer must
    # never imply that a visual door choice has been added to an estimate.
    # Retire the earlier generic placeholders in the menu. Existing estimate
    # selections remain unchanged; do not silently relabel a saved design.
    old_ids = {'steel-6-panel', 'fiberglass-3panel', 'fiberglass-glass', 'modern-flush'}
    if 'exterior_doors' not in pb:
        pb['exterior_doors'] = copy.deepcopy(EXTERIOR_DOOR_OPTIONS_SEED)
    elif any(d.get('id') in old_ids for d in pb['exterior_doors']):
        pb['exterior_doors'] = [d for d in pb['exterior_doors'] if d.get('id') not in old_ids]
        have = {d.get('id') for d in pb['exterior_doors']}
        pb['exterior_doors'].extend(copy.deepcopy(d) for d in EXTERIOR_DOOR_OPTIONS_SEED if d['id'] not in have)
    # Key absence means this live book has never used the uploader.  An
    # explicit [] is a manager intentionally clearing the menu and stays
    # empty, just like deleted Price Book bundles stay deleted.
    if 'exterior_catalog' not in pb:
        pb['exterior_catalog'] = _legacy_exterior_catalog(pb)
    _migrate_lp_expertfinish_visuals(pb)
    return pb


@app.route('/api/templates')
def get_templates():
    """Return the default item list per trade used by Load Defaults / auto-build.

    The saved price book is AUTHORITATIVE when present for a trade: the items it
    contains (with per-tier products/costs, measure/formula, visibility) are the
    full list — so adding or removing an item there changes what gets loaded.
    Rich marketing descriptions/notes from the hardcoded TEMPLATES are backfilled
    by name for any field the price book left blank. When a trade has no saved
    price book yet, the hardcoded TEMPLATES seed it."""
    pb = _load_price_book()
    pb_mats = pb.get('materials') or {}
    result = {}

    # Fields to backfill from hardcoded templates when the price book item omits them
    RICH = ('desc_good', 'desc_better', 'desc_best',
            'notes_good', 'notes_better', 'notes_best',
            'variants_good', 'variants_better', 'variants_best')

    for trade, items in TEMPLATES.items():
        hardcoded_by_name = {it.get('name', ''): it for it in items}
        pb_items = pb_mats.get(trade, [])

        if pb_items:
            # Price book is the authoritative list for this trade
            merged = []
            for it in pb_items:
                m = dict(it)
                if 'cost' not in m:
                    # Backward compat: old mat_better + lab_better format
                    m['cost'] = float(m.get('mat_better') or 0) + float(m.get('lab_better') or 0)
                m.setdefault('customer_visible', True)
                base = hardcoded_by_name.get(m.get('name', ''), {})
                for f in RICH:
                    if not m.get(f) and base.get(f):
                        m[f] = base[f]
                # measure is deliberately NOT in RICH: an explicit '' means the
                # user picked Manual in the price book, and that choice must
                # stick. Only a fully ABSENT key ("never set") may inherit the
                # template's measure or the pitch auto-link below.
                if 'measure' not in m and base.get('measure'):
                    m['measure'] = base['measure']
                # Pitch-driven auto-link by name: an existing price book item
                # like "Rolled Roofing" or "Steep Charge" picks up the RoofR
                # pitch measurements with zero setup. Only fires when the item
                # has never had a measure/formula set — an explicit choice
                # (including explicit Manual '') wins.
                if trade == 'roofing' and 'measure' not in m and not m.get('formula'):
                    n = (m.get('name') or '').lower()
                    if re.search(r'\broll(?:ed)?\b|low[\s-]?slope|mod(?:ified)?[\s-]?bit', n):
                        m['measure'] = 'low_slope_waste'
                    elif 'steep' in n:
                        m['measure'] = 'steep'
                merged.append(m)
            result[trade] = merged
        else:
            # No price book yet — seed from hardcoded templates
            seeded = []
            for item in items:
                m = dict(item)
                m.setdefault('cost', 0)
                m.setdefault('customer_visible', True)
                seeded.append(m)
            result[trade] = seeded

    # Trades that exist only in the price book (not in hardcoded TEMPLATES)
    for trade, pb_items in pb_mats.items():
        if trade not in result:
            result[trade] = pb_items

    return jsonify(result)


@app.route('/api/pricebook', methods=['GET'])
def get_pricebook():
    pb = _load_price_book()
    pb.setdefault('intros', [])
    pb.setdefault('materials', {})
    pb.setdefault('presets', {})   # brand preset bundles, keyed by trade
    _ensure_bundle_catalogs(pb)    # roofing/siding product catalogs + bundles (seed if absent)
    return jsonify(pb)


@app.route('/api/pricebook', methods=['PUT'])
def put_pricebook():
    if not _is_manager_up():
        return _forbid()
    _save_price_book(request.get_json(force=True))
    return jsonify({'ok': True})


@app.route('/api/exterior-catalog', methods=['GET'])
def get_exterior_catalog():
    pb = _ensure_bundle_catalogs(_load_price_book())
    entries = pb.get('exterior_catalog') or []
    counts = {category: sum(1 for e in entries if e.get('category') == category)
              for category in sorted(EXTERIOR_CATALOG_CATEGORIES)}
    return jsonify({'entries': entries, 'counts': counts})


@app.route('/api/exterior-catalog', methods=['PUT'])
def put_exterior_catalog():
    if not _is_manager_up():
        return _forbid()
    body = request.get_json(silent=True) or {}
    try:
        entries = _normalize_exterior_catalog(body.get('entries'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    pb = _load_price_book()
    _ensure_bundle_catalogs(pb)
    pb['exterior_catalog'] = entries
    _save_price_book(pb)
    return jsonify({'ok': True, 'entries': entries, 'count': len(entries)})


@app.route('/api/exterior-catalog/import', methods=['POST'])
def import_exterior_catalog():
    """Merge normalized CSV rows without replacing a manager's other lines."""
    if not _is_manager_up():
        return _forbid()
    body = request.get_json(silent=True) or {}
    try:
        incoming = _normalize_exterior_catalog(body.get('rows'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    pb = _load_price_book()
    _ensure_bundle_catalogs(pb)
    existing = _normalize_exterior_catalog(pb.get('exterior_catalog') or [])
    by_id = {e['id']: e for e in existing}
    before = len(by_id)
    for entry in incoming:
        # The normalized id is a stable identity for one product/color row, so
        # importing an updated sheet changes that row instead of multiplying it.
        by_id[entry['id']] = entry
    merged = list(by_id.values())
    if len(merged) > 5000:
        return jsonify({'error': 'Exterior catalog is limited to 5,000 rows'}), 400
    pb['exterior_catalog'] = merged
    _save_price_book(pb)
    return jsonify({'ok': True, 'entries': merged, 'imported': len(incoming),
                    'added': len(merged) - before, 'count': len(merged)})


@app.route('/api/exterior-catalog/template.csv')
def exterior_catalog_template():
    if not _is_manager_up():
        return _forbid()
    csv_text = (
        'category,brand,product,style,color,color_code,hex,applies_to,price_book_bundle,active\r\n'
        'roof,CertainTeed,Landmark,Architectural,Moire Black,,#292929,,b_landmark,true\r\n'
        'siding,LP,ExpertFinish,Board & Batten,Deep Ocean,,#263d4c,,b_lp_expert,true\r\n'
        'door,ProVia,Signet,460 Style,Autumn Red,,#823d36,,,true\r\n'
        'paint,Sherwin-Williams,Duration,Exterior Satin,Tricorn Black,SW 6258,#2f2f30,siding,,true\r\n'
    )
    return Response(csv_text, mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=exterior-catalog-template.csv'})


@app.route('/api/pricebook/intros', methods=['POST'])
def upsert_intro():
    pb = _load_price_book()
    pb.setdefault('intros', [])
    tpl = request.get_json(force=True)
    for t in pb['intros']:
        if t.get('id') == tpl.get('id'):
            t.update(tpl)
            _save_price_book(pb)
            return jsonify({'ok': True, 'intros': pb['intros']})
    if not tpl.get('id'):
        tpl['id'] = str(uuid.uuid4())[:8]
    pb['intros'].append(tpl)
    _save_price_book(pb)
    return jsonify({'ok': True, 'intros': pb['intros']})


@app.route('/api/pricebook/intros/<tid>', methods=['DELETE'])
def delete_intro(tid):
    pb = _load_price_book()
    pb['intros'] = [t for t in pb.get('intros', []) if t.get('id') != tid]
    _save_price_book(pb)
    return jsonify({'ok': True, 'intros': pb['intros']})


# ── Tier defaults (global G/B/B package content, keyed by trade) ───────────
# Shape: { trade: { 'descriptions': {good/better/best: str},
#                   'features':     {good/better/best: [str]} } }
# Legacy files were a single flat {good:[], better:[], best:[]} of roofing
# bullets — migrate those to the roofing key on read.

def _blank_trade_defaults():
    return {'descriptions': {'good': '', 'better': '', 'best': ''},
            'features':     {'good': [], 'better': [], 'best': []}}

def _load_tier_defaults():
    data = None
    if os.path.exists(TIER_DEFAULTS_FILE):
        try:
            with open(TIER_DEFAULTS_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    if 'good' in data or 'better' in data or 'best' in data:
        # Legacy flat shape — those bullets were always roofing copy
        data = {'roofing': {'descriptions': {'good': '', 'better': '', 'best': ''},
                            'features': {t: list(data.get(t) or []) for t in ('good', 'better', 'best')}}}
    for tk in GBB_TRADES:
        td = data.get(tk)
        if not isinstance(td, dict):
            data[tk] = _blank_trade_defaults()
            continue
        td.setdefault('descriptions', {'good': '', 'better': '', 'best': ''})
        td.setdefault('features', {'good': [], 'better': [], 'best': []})
    return data

@app.route('/api/tier-defaults', methods=['GET'])
def get_tier_defaults():
    return jsonify(_load_tier_defaults())

@app.route('/api/tier-defaults', methods=['PUT'])
def put_tier_defaults():
    if not _is_manager_up():
        return _forbid()
    data = request.get_json(force=True)
    with open(TIER_DEFAULTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── App settings (global, editable in the ⚙ Settings modal) ────────────────

APP_SETTINGS_FILE = os.path.join(DATA_DIR, 'app_settings.json')

@app.route('/api/settings', methods=['GET'])
def get_app_settings():
    if os.path.exists(APP_SETTINGS_FILE):
        try:
            with open(APP_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify({})

@app.route('/api/settings', methods=['PUT'])
def put_app_settings():
    if not _is_manager_up():
        return _forbid()
    data = request.get_json(force=True)
    with open(APP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── Sales goals (monthly targets the analytics tab measures against) ───────
#
# Shape on disk (sales_goals.json):
#   {"company": {"default": {"revenue": 250000, "jobs": 10},
#                "months":  {"2026-07": {"revenue": 300000, "jobs": 12}}},
#    "reps":    {"luke":    {"default": {...}, "months": {...}}}}
#
# A month with no override falls back to the default; a default of 0 means "no
# goal set", which the UI renders as a dash rather than as 0% attainment.

_GOAL_MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _empty_goals():
    return {'company': {'default': {'revenue': 0, 'jobs': 0}, 'months': {}},
            'reps': {}}


def _clean_goal(d):
    """One {'revenue','jobs'} pair, coerced to non-negative numbers."""
    out = {}
    for k in ('revenue', 'jobs'):
        try:
            v = float((d or {}).get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        out[k] = max(0, round(v, 2) if k == 'revenue' else int(v))
    return out


def _clean_goal_scope(d):
    """One company/rep entry: a default plus month overrides."""
    months = {}
    for mk, mv in ((d or {}).get('months') or {}).items():
        if _GOAL_MONTH_RE.match(str(mk)):
            months[str(mk)] = _clean_goal(mv)
    return {'default': _clean_goal((d or {}).get('default')), 'months': months}


def _load_goals():
    if os.path.exists(SALES_GOALS_FILE):
        try:
            with open(SALES_GOALS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            goals = {'company': _clean_goal_scope(raw.get('company')), 'reps': {}}
            for rep, rv in (raw.get('reps') or {}).items():
                goals['reps'][str(rep).strip().lower()] = _clean_goal_scope(rv)
            return goals
        except Exception:
            pass
    return _empty_goals()


def _goal_for(goals, month_key, rep=None):
    """Effective {'revenue','jobs'} goal for a month. `rep=None` is company-wide.
    Month override wins over the scope default; a missing scope is all zeros."""
    scope = (goals.get('reps') or {}).get((rep or '').strip().lower()) \
        if rep else goals.get('company')
    if not scope:
        return {'revenue': 0, 'jobs': 0}
    return (scope.get('months') or {}).get(month_key) or scope.get('default') \
        or {'revenue': 0, 'jobs': 0}


@app.route('/api/goals', methods=['GET'])
def get_sales_goals():
    """Readable by every rep — they see their own target on the analytics tab."""
    return jsonify(_load_goals())


@app.route('/api/goals', methods=['PUT'])
def put_sales_goals():
    if not _is_manager_up():
        return _forbid()
    raw = request.get_json(force=True) or {}
    clean = {'company': _clean_goal_scope(raw.get('company')), 'reps': {}}
    for rep, rv in (raw.get('reps') or {}).items():
        name = str(rep).strip().lower()
        if name:
            clean['reps'][name] = _clean_goal_scope(rv)
    with open(SALES_GOALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean, f, indent=2)
    return jsonify({'ok': True, 'goals': clean})


# ── Company trust content (About Us / Warranty / Certifications / Reviews) ──
# Rendered onto every customer proposal by _cv_trust_blocks(). Admin-edited.

@app.route('/api/company-content', methods=['GET'])
def get_company_content():
    return jsonify(_load_company_content())


@app.route('/api/company-content', methods=['PUT'])
def put_company_content():
    if not _is_admin(_current_user()):
        return _forbid()
    data = request.get_json(force=True) or {}
    # Keep only the known blocks so a bad client can't grow the file unbounded
    clean = {k: data[k] for k in ('about', 'warranty', 'certifications', 'reviews')
             if isinstance(data.get(k), dict)}
    with open(COMPANY_CONTENT_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean, f, indent=2)
    return jsonify({'ok': True})


# ── Loveland permit PDF filler ──────────────────────────────────────────────
# The City of Loveland reroof packet is a flat 2-page PDF (no form fields)
# that the city won't let us edit. We keep a pre-signed copy as a template
# (static/permit_templates/) and draw only the job-specific values onto it:
# an fpdf2 overlay page merged onto each template page with pypdf. Field
# positions live in permit_coords.py; sticky roofing-spec defaults in
# permit_defaults.json (same convention as tier_defaults.json).

import permit_coords as _permit_coords

PERMIT_TEMPLATE_PATH = os.path.join(BASE_DIR, 'static', 'permit_templates',
                                    'loveland_permit_affidavit.pdf')

_PERMIT_DEFAULTS_FALLBACK = {
    'roof_covering_type_default': 'Asphalt Composition Shingle',
    'roof_covering_class_default': 'Class 4',
    'replacing_sheathing_default': 'no',
    'metal_noncombustible_default': False,
    'astm_type_default': 'asphalt',
    'fastener_staples_default': '',
    'fastener_nails_default': '',
    'fastener_other_default': '',
    'underlayment_self_adhering_default': True,
    'underlayment_ice_barrier_default': True,
}

def _load_permit_defaults():
    if os.path.exists(PERMIT_DEFAULTS_FILE):
        try:
            with open(PERMIT_DEFAULTS_FILE, encoding='utf-8') as f:
                return {**_PERMIT_DEFAULTS_FALLBACK, **json.load(f)}
        except Exception:
            pass
    return dict(_PERMIT_DEFAULTS_FALLBACK)

@app.route('/api/permit-defaults', methods=['GET'])
def get_permit_defaults():
    return jsonify(_load_permit_defaults())

@app.route('/api/permit-defaults', methods=['PUT'])
def put_permit_defaults():
    if not _is_manager_up():
        return _forbid()
    data = request.get_json(force=True)
    with open(PERMIT_DEFAULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── Commercial fastening table ──────────────────────────────────────────────
# Fastener density on a low-slope roof is set by roof zone (ASCE 7 field /
# perimeter / corner), by the uplift the roof must resist, and by which layer is
# being fastened. This table holds those densities; commercial_fastening() below
# does the math.
#
# ONE copy of the table, served by this API — app.js fetches it rather than
# mirroring it. Only the ALGORITHM is duplicated JS<->PY, and tests/test_fastening.py
# holds the two implementations to the same numbers. (Contrast attic_ventilation,
# whose constants are mirrored in both files with nothing to catch drift.)
#
# The seeded densities are GENERIC and invented — see source_note. They are a
# starting point for a manager, not a manufacturer's approval.
_COMM_FASTENING_FALLBACK = {
    'version': 1,
    'source_note': ("GENERIC INDUSTRY-TYPICAL DEFAULTS - not a manufacturer's approval and not "
                    "tied to any manufacturer. Verify against the specified system's FM approval "
                    "or wind-uplift design guide before ordering or installing."),
    'zone_rule': {
        'standard': 'ASCE 7-16 low-slope (theta <= 7 deg)',
        'a_pct_least': 0.10, 'a_pct_height': 0.40,
        'a_min_pct_least': 0.04, 'a_min_ft': 3,
        # ASCE 7-10 drew the corner (Zone 3) as a square a x a -> 4a^2 total.
        # ASCE 7-16 redrew it as an L (two 2a x a legs) -> 3a^2 each, 12a^2 total.
        # That is 3x in the highest-uplift zone, so it is a setting, not a
        # constant, and defaults to the conservative reading.
        'corner_shape': 'L',
    },
    'board_sf': 32,
    'waste_pct': 5,
    'ratings': {
        '60': {'label': 'FM 1-60',
               'insul_per_board': {'field': 4, 'perimeter': 6, 'corner': 8},
               'seam': {'field':     {'sheet_width_ft': 10, 'spacing_in': 12},
                        'perimeter': {'sheet_width_ft': 6,  'spacing_in': 12},
                        'corner':    {'sheet_width_ft': 5,  'spacing_in': 12}}},
        '75': {'label': 'FM 1-75',
               'insul_per_board': {'field': 5, 'perimeter': 8, 'corner': 10},
               'seam': {'field':     {'sheet_width_ft': 10, 'spacing_in': 12},
                        'perimeter': {'sheet_width_ft': 5,  'spacing_in': 12},
                        'corner':    {'sheet_width_ft': 5,  'spacing_in': 9}}},
        '90': {'label': 'FM 1-90',
               'insul_per_board': {'field': 5, 'perimeter': 8, 'corner': 12},
               'seam': {'field':     {'sheet_width_ft': 8, 'spacing_in': 12},
                        'perimeter': {'sheet_width_ft': 5, 'spacing_in': 12},
                        'corner':    {'sheet_width_ft': 5, 'spacing_in': 6}}},
        '105': {'label': 'FM 1-105',
                'insul_per_board': {'field': 6, 'perimeter': 10, 'corner': 14},
                'seam': {'field':     {'sheet_width_ft': 6, 'spacing_in': 12},
                         'perimeter': {'sheet_width_ft': 5, 'spacing_in': 9},
                         'corner':    {'sheet_width_ft': 5, 'spacing_in': 6}}},
    },
    # {bundle_id: {insulation: bool, seam: bool, board_sf: n, ratings: {...partial...}}}
    'bundle_overrides': {},
}


def _load_commercial_fastening():
    """DATA_DIR file -> repo seed -> hardcoded fallback, merged per TOP-LEVEL key
    so a table saved under v1 still gains fields added in a later version."""
    for path in (COMM_FASTENING_FILE, os.path.join(BASE_DIR, 'commercial_fastening.json')):
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    merged = dict(_COMM_FASTENING_FALLBACK)
                    merged.update(saved)
                    # zone_rule gains fields over time; a v1 file must not lose them.
                    merged['zone_rule'] = {**_COMM_FASTENING_FALLBACK['zone_rule'],
                                           **(saved.get('zone_rule') or {})}
                    return merged
            except Exception:
                pass
    return json.loads(json.dumps(_COMM_FASTENING_FALLBACK))


@app.route('/api/commercial-fastening', methods=['GET'])
def get_commercial_fastening():
    return jsonify(_load_commercial_fastening())


@app.route('/api/commercial-fastening', methods=['PUT'])
def put_commercial_fastening():
    if not _is_manager_up():
        return _forbid()
    data = request.get_json(force=True)
    with open(COMM_FASTENING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── Permit jurisdiction & code reference (statewide Colorado) ────────────────
# jurisdictions.json is generated by scripts/build_jurisdictions.py (all 64 CO
# counties + 273 municipalities, parsed from Wikipedia). The Scope-page panel
# matches the customer address against it, then shows the permit office + code
# points (a shared colorado_baseline inherited by every entry, plus per-entry
# overrides). Reference/display only — never feeds pricing. Managers edit it via
# ⚙ Settings; same file+route pattern as permit_defaults.
_JURISDICTIONS_FALLBACK = {'version': 1, 'colorado_baseline': {'code_points': [], 'verify_note': ''},
                           'jurisdictions': []}

# All 64 counties shipped with an empty `url`, which left them with no domain
# of their own for the citation allowlist — and unlike a city, a county name
# does not reliably imply its domain (El Paso County is elpasoco.com, not
# elpaso.co.us). These are the counties the reps actually work, verified live.
#
# Backfilled on READ rather than only fixed in the seed file, because
# _seed_data_dir() copies jurisdictions.json to the volume ONLY when absent:
# on a long-lived volume the repo copy is inert, so a seed-only edit would
# never reach production. Same trap the price book's bundle catalogs hit.
_JX_COUNTY_URL_SEED = {
    'adams-county':      'https://www.adcogov.org/',
    'arapahoe-county':   'https://www.arapahoeco.gov/',
    'boulder-county':    'https://bouldercounty.gov/',
    'broomfield-county': 'https://www.broomfield.org/',
    'denver-county':     'https://www.denvergov.org/',
    'douglas-county':    'https://www.douglas.co.us/',
    'el-paso-county':    'https://elpasoco.com/',
    'fremont-county':    'https://fremontco.com/',
    'jefferson-county':  'https://www.jeffco.us/',
    'larimer-county':    'https://www.larimer.gov/',
    'pueblo-county':     'https://county.pueblo.org/',
    'teller-county':     'https://www.co.teller.co.us/',
    'weld-county':       'https://www.weld.gov/',
}


def _jx_backfill_urls(data):
    """Fill a missing county `url` from the seed. Never overwrites a value a
    manager set in Settings — an empty string is the only thing replaced."""
    for j in (data.get('jurisdictions') or []):
        if not isinstance(j, dict) or (j.get('url') or '').strip():
            continue
        seeded = _JX_COUNTY_URL_SEED.get(j.get('id'))
        if seeded:
            j['url'] = seeded
    return data


def _load_jurisdictions():
    # Prefer the live DATA_DIR copy; fall back to the committed seed beside the
    # app so a fresh checkout still serves the full statewide dataset.
    for path in (JURISDICTIONS_FILE, os.path.join(BASE_DIR, 'jurisdictions.json')):
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return _jx_backfill_urls(json.load(f))
            except Exception:
                pass
    return dict(_JURISDICTIONS_FALLBACK)

@app.route('/api/jurisdictions', methods=['GET'])
def get_jurisdictions():
    return jsonify(_load_jurisdictions())

@app.route('/api/jurisdictions', methods=['PUT'])
def put_jurisdictions():
    if not _is_manager_up():
        return _forbid()
    data = request.get_json(force=True)
    with open(JURISDICTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({'ok': True})


# ── Boundary verification: is this parcel inside city limits? ────────────────
# The mailing city is NOT the AHJ — a "Fort Collins, CO" address routinely sits
# in unincorporated Larimer County, and the city won't issue that permit. The
# Census geocoder settles it against real TIGER polygons: it returns the
# Incorporated Places geography containing the point, and returns NOTHING when
# the parcel falls outside every municipal boundary. That absence IS the answer
# — the county is the AHJ. Free, keyless, unmetered.
#
# Two caveats the panel surfaces rather than hides:
#   * TIGER place boundaries lag recent annexations (up to ~a year), so a newly
#     annexed parcel can still read as unincorporated.
#   * "Inside city limits" != "the city issues the permit" — some CO towns
#     contract building inspection out to the county.
# So this auto-selects and labels itself verified, but the rep can still
# override; never treat it as the final word.
_CENSUS_GEO_BASE = 'https://geocoding.geo.census.gov/geocoder/geographies'
_CENSUS_ARGS = {'benchmark': 'Public_AR_Current', 'vintage': 'Current_Current',
                'layers': 'Incorporated Places,Counties', 'format': 'json'}
_JX_PLACE_SUFFIX_RE  = re.compile(r'\s+(city and county|city|town|village|CDP|municipality)$', re.I)
_JX_COUNTY_SUFFIX_RE = re.compile(r'\s+County$', re.I)


def _jx_clean_place(name):
    """'Fort Collins city' → 'Fort Collins' (matches jurisdictions.json names)."""
    return _JX_PLACE_SUFFIX_RE.sub('', str(name or '').strip()).strip()


def _jx_clean_county(name):
    return _JX_COUNTY_SUFFIX_RE.sub('', str(name or '').strip()).strip()


def _jx_geographies(geo):
    """Census 'geographies' block → (place name or None, county name or None).
    A missing/empty 'Incorporated Places' list means unincorporated."""
    places   = geo.get('Incorporated Places') or []
    counties = geo.get('Counties') or []
    return ((places[0].get('NAME') if places else None),
            (counties[0].get('NAME') if counties else None))


def _jx_census_by_address(one_line):
    r = http.get(_CENSUS_GEO_BASE + '/onelineaddress',
                 params=dict(_CENSUS_ARGS, address=one_line), timeout=12)
    r.raise_for_status()
    matches = ((r.json().get('result') or {}).get('addressMatches') or [])
    if not matches:
        return None
    m = matches[0]
    place, county = _jx_geographies(m.get('geographies') or {})
    coords = m.get('coordinates') or {}
    return {'place': place, 'county': county, 'source': 'census-address',
            'matched_address': m.get('matchedAddress') or '',
            'lat': coords.get('y'), 'lon': coords.get('x')}


def _jx_census_by_coords(lat, lon):
    r = http.get(_CENSUS_GEO_BASE + '/coordinates',
                 params=dict(_CENSUS_ARGS, x=lon, y=lat), timeout=12)
    r.raise_for_status()
    place, county = _jx_geographies((r.json().get('result') or {}).get('geographies') or {})
    if not county:
        return None
    return {'place': place, 'county': county, 'source': 'osm+census-point',
            'matched_address': '', 'lat': lat, 'lon': lon}


def _jx_nominatim_point(one_line):
    """Census can't match rural/new-construction addresses that OSM has. Same
    free geocoder the canvasser uses — we only need a point to test."""
    r = http.get('https://nominatim.openstreetmap.org/search',
                 params={'q': one_line, 'format': 'json', 'limit': 1, 'countrycodes': 'us'},
                 headers={'User-Agent': 'ProjectOneRoofing-Estimator/1.0'}, timeout=12)
    r.raise_for_status()
    hits = r.json() or []
    if not hits:
        return None
    return float(hits[0]['lat']), float(hits[0]['lon'])


@app.route('/api/jurisdictions/verify')
def verify_jurisdiction():
    """Address → {incorporated, place, county}. Always 200 — the panel falls
    back to the manual picker on {ok:false} rather than showing an error."""
    if http is None:
        return jsonify({'ok': False, 'error': 'Address lookup is unavailable on this server.'})
    street = (request.args.get('street') or '').strip()
    city   = (request.args.get('city') or '').strip()
    state  = (request.args.get('state') or '').strip()
    zipc   = (request.args.get('zip') or '').strip()
    if not street or not (city or zipc):
        return jsonify({'ok': False, 'error': 'Need a street address plus a city or ZIP.'})
    tail = ' '.join(p for p in (state, zipc) if p)
    one_line = ', '.join(p for p in (street, city, tail) if p)

    res = None
    try:
        res = _jx_census_by_address(one_line)
    except Exception:
        res = None
    if res is None:
        try:
            pt = _jx_nominatim_point(one_line)
            if pt:
                res = _jx_census_by_coords(*pt)
        except Exception:
            res = None
    if not res or not res.get('county'):
        return jsonify({'ok': False, 'error':
                        "Couldn't place this address on a jurisdiction boundary — "
                        "pick the governing authority manually."})

    return jsonify({
        'ok':              True,
        'incorporated':    bool(res.get('place')),
        'place':           res.get('place') or '',
        'place_clean':     _jx_clean_place(res.get('place')),
        'county':          res.get('county') or '',
        'county_clean':    _jx_clean_county(res.get('county')),
        'matched_address': res.get('matched_address') or '',
        'lat':             res.get('lat'),
        'lon':             res.get('lon'),
        'source':          res.get('source'),
        'checked_at':      datetime.now().isoformat(timespec='seconds'),
    })


# ── Verified jurisdiction profile ────────────────────────────────────────────
# The Scope-page code block used to be "the CO baseline for everyone" — six
# generic IRC bullets identical across all 337 jurisdictions in the file. That
# is not what a customer needs to see. This layer looks up the specific
# adopted code + local amendments + reroof permit info FOR THIS jurisdiction
# and puts it in front of the customer on the sign page — but only after a
# manager has clicked "approve," so a model hallucination cannot reach a
# customer.
#
# Sourcing order (tiered, Perplexity as fallback per Luke's instruction):
#   1. curated `code_url` on the jurisdiction entry (if a manager added one)
#   2. publisher heuristics (library.municode.com/co/{slug}, ...)
#   3. the jurisdiction's own building-department page (`url`)
#   4. Perplexity, with the citation allowlist in jurisdiction_prompts.py
#
# The direct tiers return whatever adopted_code they can parse out of the
# fetched HTML — that's enough to skip Perplexity when the city's own page
# says the year. Amendments and permit portal details come from Perplexity
# (when direct fetch didn't produce them) or from a manager typing them into
# ⚙ Settings.
try:
    from . import jurisdiction_prompts as _jp
except ImportError:                            # pragma: no cover - script mode
    import jurisdiction_prompts as _jp

_CODE_YEAR_RE = re.compile(
    r'(?P<yb>20\d{2}|19\d{2})\s+(?:International\s+Residential\s+Code|IRC)\b'
    r'|(?:International\s+Residential\s+Code|IRC)[\s,]+(?:Edition\s+)?(?P<ya>20\d{2}|19\d{2})',
    re.IGNORECASE,
)


def _jx_extract_adopted_code(html, source_url):
    """Loose regex — matches '2021 IRC', 'IRC 2021', 'International Residential
    Code, 2018 Edition'. Never a guess: on no match we return None and the
    caller falls through."""
    if not html:
        return None
    m = _CODE_YEAR_RE.search(html)
    if not m:
        return None
    year = m.group('yb') or m.group('ya') or ''
    if not year:
        return None
    return {
        'adopted_code':            f'{year} IRC',
        'adopted_code_source_url': source_url,
    }


# Municipal sites sit behind WAFs that 403 an obviously-scripted User-Agent.
# A browser UA is not a disguise here — we are fetching one public page the
# jurisdiction publishes for contractors to read, at the rate of one per
# manager click. Measured: several CO city sites 403 our old UA.
_JX_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def _jx_fetch_html(url, timeout=10):
    if not url or http is None:
        return None
    try:
        r = http.get(url, timeout=timeout, allow_redirects=True,
                     headers={'User-Agent': _JX_UA,
                              'Accept': 'text/html,application/xhtml+xml'})
    except Exception:
        return None
    if not r.ok:
        return None
    ct = (r.headers.get('Content-Type') or '').lower()
    if 'html' not in ct and 'text' not in ct:
        return None
    return r.text


def _jx_direct_urls(j):
    """Order the direct-fetch pipeline tries. First hit wins.

    The Municode/amlegal slug guesses that used to live here were removed
    after measuring them: they cannot work, and each one cost a 10s timeout
    on every verify. Municode serves a ~6 KB JavaScript app shell with no
    code year anywhere in the HTML, so the regex has nothing to match no
    matter how right the slug is; amlegal 403s us and the guessed slug 404s
    besides. Across a 16-jurisdiction sample the direct tier hit 0 times.

    What is left is the two URLs that are actually about this jurisdiction: a
    manager-curated `code_url`, then the building-department page on file.
    A Wayback snapshot is unwrapped to the page it archived — fetching
    web.archive.org gives us the 2020 copy of a site, which is not what we
    want to quote a customer."""
    urls, seen = [], set()

    def add(u):
        u = _jp._unwrap_archive(u).strip()
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    add(j.get('code_url'))
    add(j.get('url'))
    return urls


def _jx_direct_profile(j):
    """Try the direct-fetch tier. Return {profile, source, citations} or None."""
    for url in _jx_direct_urls(j):
        html = _jx_fetch_html(url)
        if not html:
            continue
        code = _jx_extract_adopted_code(html, url)
        if not code:
            continue
        # A confidence tag so the rep drawer can label where the answer came from.
        host = (url.split('/')[2] if '://' in url else '').lower()
        if 'municode.com' in host:
            source = 'municode'
        elif 'amlegal.com' in host:
            source = 'amlegal'
        elif 'ecode360.com' in host:
            source = 'ecode360'
        else:
            source = 'city-page'
        return {
            'profile': {
                'adopted_code':            code['adopted_code'],
                'adopted_code_source_url': code['adopted_code_source_url'],
                'amendments':              [],
                'reroof_permit':           {'submittal_method': 'unknown',
                                            'portal_url':       'unknown',
                                            'fee_basis':        'unknown'},
                'issues_permits_for_roofing': True,
                'delegated_to':            None,
            },
            'source':    source,
            'citations': [url],
        }
    return None


class _JxRetryable(RuntimeError):
    """A verify failure that a fresh Perplexity call might not repeat.

    `cached` records whether the answer we judged came out of the 30-day
    cache. That matters because the cache stores the model's ANSWER, while
    these failures are decided downstream of it — so re-running a failed
    verify replayed the same cached answer into the same error for 30 days,
    and the manager's "Re-verify" button was a no-op the whole time. When the
    bad answer was cached, `_verify_jurisdiction_profile` spends one more call
    to genuinely retry."""

    def __init__(self, message, cached=False):
        super().__init__(message)
        self.cached = bool(cached)


_JX_PURE_IRC_RE = re.compile(
    r'^(?:(?P<yb>(?:19|20)\d{2})\s+(?:IRC|International\s+Residential\s+Code)'
    r'|(?:IRC|International\s+Residential\s+Code)[\s,]+(?:Edition\s+)?'
    r'(?P<ya>(?:19|20)\d{2})(?:\s+Edition)?)$',
    re.IGNORECASE)


def _jx_normalize_code(text):
    """Tidy the model's free-text adopted_code without rewriting it.

    Across a 16-jurisdiction sample this field came back as "IRC 2021",
    "2021 IRC", "2024 I-Codes", "2024 International Codes with amendments"
    and "Pikes Peak Regional Building Code 2023" — all headed for the same
    line on a customer's estimate. We only canonicalise the case that is
    unambiguously the same fact written two ways ("IRC 2021" → "2021 IRC")
    and otherwise leave the wording alone: "Pikes Peak Regional Building
    Code" is a genuinely different code, not an IRC year, and flattening it
    would state something false. Whitespace is collapsed and the string
    capped so one runaway sentence cannot blow out the PDF layout."""
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    m = _JX_PURE_IRC_RE.match(t)
    if m:
        year = m.group('yb') or m.group('ya')
        if year:
            return f'{year} IRC'
    if len(t) <= 160:
        return t
    # Cut on a word boundary. A hard slice ended one real answer at
    # "...and 2024 International Building Code (for other structures), as
    # part of t", which looks like corruption on a customer's estimate.
    cut = t[:160].rstrip()
    space = cut.rfind(' ')
    if space > 80:
        cut = cut[:space].rstrip()
    return cut.rstrip(',;:') + '…'


# The verify call is the one place we pay for accuracy rather than speed.
# `sonar` (the global default in agents/config.py) returned "unknown" for the
# adopted code on Loveland, Longmont, Boulder and Colorado Springs; this is a
# once-per-jurisdiction lookup cached for 30 days and then read by a manager
# before it goes anywhere, so the better model is worth roughly a cent.
_JX_MODEL = 'sonar-pro'


def _jx_perplexity_profile(j, force_refresh=False):
    """Fallback — one Perplexity call, cached inside agents/perplexity.py.
    Returns {profile, source:'perplexity', citations, cached} or raises."""
    try:
        from agents.perplexity import search_json
    except ImportError as e:                       # pragma: no cover
        raise RuntimeError(f'Perplexity fallback unavailable: {e}') from e
    prompt = _jp.build_prompt(j)
    reason = f'jurisdiction verify: {j.get("id") or j.get("name")}'
    result = search_json(prompt, reason=reason, max_tokens=1200,
                         model=_JX_MODEL, force_refresh=force_refresh)
    cached = bool(result.get('cached'))
    data = result.get('data')
    if not isinstance(data, dict) or 'raw' in data:
        raise _JxRetryable('Perplexity returned an answer we could not parse '
                           'as JSON.', cached)
    # The jurisdiction's OWN domain counts as authoritative for its own code.
    # Without this the allowlist rejected 69% of Colorado cities' official
    # sites, because most of them are .org/.com/.us rather than .gov.
    citations = _jp.filter_allowed_citations(result.get('citations') or [],
                                             _jp.jurisdiction_hosts(j))
    if not citations:
        # No authoritative source cited — refuse to save it. The prompt asks
        # for real URLs on the allowlist; anything else fails closed rather
        # than reaching a customer.
        raise _JxRetryable('Perplexity did not cite an authoritative source '
                           '(its own official site, .gov, or a code '
                           'publisher).', cached)
    # Coerce into the shape the frontend/UI expects, dropping any extra keys
    # the model invented and defaulting missing ones to 'unknown'/[].
    def _s(k):
        v = data.get(k)
        return str(v).strip() if isinstance(v, (str, int, float)) else 'unknown'
    rp_in = data.get('reroof_permit') if isinstance(data.get('reroof_permit'), dict) else {}
    def _rp(k):
        v = rp_in.get(k)
        return str(v).strip() if isinstance(v, (str, int, float)) else 'unknown'
    amends_out = []
    for a in (data.get('amendments') or []):
        if not isinstance(a, dict):
            continue
        topic = str(a.get('topic') or '').strip()
        text  = str(a.get('text')  or '').strip()
        # Perplexity sometimes returns a placeholder row of `unknown`s when it
        # has nothing — treat those as absent rather than persisting garbage.
        if not text or text.lower() == 'unknown':
            continue
        amends_out.append({
            'topic':      topic if topic.lower() != 'unknown' else '',
            'text':       text,
            'source_url': str(a.get('source_url') or '').strip(),
        })
    delegated_to = (str(data.get('delegated_to')).strip()
                    if data.get('delegated_to') and str(data.get('delegated_to')).lower()
                    not in ('none', 'null', 'unknown', '') else None)
    adopted_code = _jx_normalize_code(_s('adopted_code'))
    # An 'unknown' adopted_code means the model refused to commit to a year.
    # There is no point saving that: the customer sees nothing useful and the
    # baseline is a better default. Fail closed so the rep re-tries later.
    #
    # EXCEPT when the model told us this jurisdiction does not set its own
    # code because it contracts the work out — Colorado Springs delegates to
    # the Pikes Peak Regional Building Department, and "who actually issues
    # your permit" is exactly what the office needs to know. Under the old
    # rule that answer was correct, useful, and thrown away.
    if not adopted_code or adopted_code.lower() == 'unknown':
        if not delegated_to:
            raise _JxRetryable('Perplexity could not determine an adopted '
                               'code year for this jurisdiction.', cached)
        adopted_code = ''
    return {
        'profile': {
            'adopted_code':            adopted_code,
            'adopted_code_source_url': _s('adopted_code_source_url'),
            'amendments':              amends_out,
            'reroof_permit': {
                'submittal_method': _rp('submittal_method'),
                'portal_url':       _rp('portal_url'),
                'fee_basis':        _rp('fee_basis'),
            },
            'issues_permits_for_roofing': bool(data.get('issues_permits_for_roofing', True)),
            'delegated_to': delegated_to,
        },
        'source':    'perplexity',
        'citations': citations,
        'cached':    cached,
    }


def _verify_jurisdiction_profile(j):
    """Full pipeline: direct fetch first, Perplexity fallback. Never raises —
    always returns a JSON-serializable dict for the API to hand back."""
    direct = _jx_direct_profile(j)
    if direct and direct['profile'].get('adopted_code'):
        return {'ok': True, **direct}
    try:
        pplx = _jx_perplexity_profile(j)
    except _JxRetryable as e:
        # The answer we rejected came from cache, so clicking Re-verify would
        # replay it unchanged. Spend one call on a real retry instead.
        if not e.cached:
            return {'ok': False, 'error': str(e), 'kind': type(e).__name__,
                    'direct': direct}
        try:
            pplx = _jx_perplexity_profile(j, force_refresh=True)
        except Exception as e2:
            return {'ok': False, 'error': str(e2), 'kind': type(e2).__name__,
                    'direct': direct, 'retried': True}
        return {'ok': True, 'retried': True, **pplx}
    except Exception as e:
        # SpendCapReached surfaces here with its class name in the message so
        # the frontend can style it differently.
        return {'ok': False,
                'error':  str(e),
                'kind':   type(e).__name__,
                'direct': direct}   # even a partial direct hit is worth showing
    return {'ok': True, **pplx}


def _jx_find_by_id(all_jx, jid):
    for j in (all_jx.get('jurisdictions') or []):
        if isinstance(j, dict) and j.get('id') == jid:
            return j
    return None


def _jx_save_atomic(data):
    """Same write pattern put_jurisdictions() uses — kept in one place so an
    approve/reject can share it with the settings PUT."""
    with open(JURISDICTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@app.route('/api/jurisdictions/<jid>/verify', methods=['POST'])
def verify_jurisdiction_profile(jid):
    if not _is_manager_up():
        return _forbid()
    data = _load_jurisdictions() or {}
    j = _jx_find_by_id(data, jid)
    if not j:
        return jsonify({'ok': False, 'error': 'Unknown jurisdiction id.'}), 404
    result = _verify_jurisdiction_profile(j)
    return jsonify(result)


@app.route('/api/jurisdictions/<jid>/approve', methods=['POST'])
def approve_jurisdiction_profile(jid):
    """Stamp reviewed_by/reviewed_at and persist. Body is the profile the rep
    just previewed (never trust an in-memory verify to survive a redeploy)."""
    if not _is_manager_up():
        return _forbid()
    body = request.get_json(force=True, silent=True) or {}
    profile = body.get('profile') or {}
    ac = (profile.get('adopted_code') or '').strip() if isinstance(profile, dict) else ''
    dl = (profile.get('delegated_to') or '').strip() if isinstance(profile, dict) else ''
    # A delegating jurisdiction has no adopted code of its own — Colorado
    # Springs contracts to the Pikes Peak Regional Building Department — and
    # naming who actually issues the permit is worth approving on its own.
    if (not ac or ac.lower() == 'unknown') and not dl:
        return jsonify({'ok': False, 'error':
                        'Profile is missing adopted_code — nothing to approve.'}), 400
    data = _load_jurisdictions() or {}
    j = _jx_find_by_id(data, jid)
    if not j:
        return jsonify({'ok': False, 'error': 'Unknown jurisdiction id.'}), 404
    verified = dict(profile)
    # Normalise here as well as on the way out of Perplexity: this endpoint
    # takes whatever the client posts, including a value a manager typed by
    # hand, and this is the single point where a profile becomes durable.
    verified['adopted_code'] = _jx_normalize_code(ac)
    verified['sources']     = [str(u).strip() for u in (body.get('citations') or []) if str(u).strip()]
    verified['verified_at'] = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
    verified['verified_via'] = str(body.get('source') or '').strip() or 'unknown'
    verified['reviewed_by'] = _current_user() or ''
    verified['reviewed_at'] = verified['verified_at']
    j['verified_profile'] = verified
    _jx_save_atomic(data)
    return jsonify({'ok': True, 'jurisdiction': j})


@app.route('/api/jurisdictions/<jid>/reject', methods=['POST'])
def reject_jurisdiction_profile(jid):
    """Clear any stored verified_profile — used to force a re-verify next time
    or to remove a profile that has since gone stale."""
    if not _is_manager_up():
        return _forbid()
    data = _load_jurisdictions() or {}
    j = _jx_find_by_id(data, jid)
    if not j:
        return jsonify({'ok': False, 'error': 'Unknown jurisdiction id.'}), 404
    if 'verified_profile' in j:
        del j['verified_profile']
        _jx_save_atomic(data)
    return jsonify({'ok': True})


_PERMIT_CHAR_MAP = str.maketrans({
    '—': '-', '–': '-', '‘': "'", '’': "'",
    '“': '"', '”': '"', '…': '...', ' ': ' ',
    '•': '-', '′': "'", '″': '"',
})

def _permit_text(s):
    """fpdf2's core Helvetica is latin-1 only. Swap the smart punctuation
    phones auto-insert (em-dashes, curly quotes) for ASCII and drop anything
    else it can't encode, so a stray character never 500s the permit."""
    s = str(s or '').translate(_PERMIT_CHAR_MAP)
    return s.encode('latin-1', 'replace').decode('latin-1')


def _permit_wrap(text, pdf, max_width, max_lines):
    """Split user text on hard newlines, then word-wrap each line to
    max_width points (measured with the overlay PDF's current font)."""
    lines = []
    for raw in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        words, cur = raw.split(), ''
        if not words:
            lines.append('')
            continue
        for w in words:
            trial = (cur + ' ' + w).strip()
            if pdf.get_string_width(trial) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines[:max_lines]


@app.route('/api/permits/loveland/generate', methods=['POST'])
def generate_loveland_permit():
    if FPDF is None or _pypdf is None:
        return jsonify({'error': 'PDF libraries not available on server'}), 500
    d = request.get_json(force=True)

    pw, ph = _permit_coords.PAGE_SIZE
    overlay = FPDF(unit='pt', format=(pw, ph))
    overlay.set_auto_page_break(False)
    overlay.set_margins(0, 0, 0)

    # Values keyed by permit_coords.FIELDS names. Checkbox fields draw an "X"
    # when truthy; everything else is drawn as text when non-empty.
    addr = d.get('job_site_address', '') or ''
    vals = {
        'job_site_address': addr,
        'valuation':        d.get('valuation', ''),
        'owner_name':       d.get('owner_name', ''),
        'owner_phone':      d.get('owner_phone', ''),
        'owner_address':    d.get('owner_address', ''),
        'owner_city':       d.get('owner_city', ''),
        'owner_state':      d.get('owner_state', ''),
        'owner_zip':        d.get('owner_zip', ''),
        'num_squares':      d.get('num_squares', ''),
        'work_description': d.get('work_description', ''),
        'date_p1':          d.get('date', ''),
        'affidavit_job_address':   d.get('affidavit_job_address', '') or addr,
        'roof_covering_type':      d.get('roof_covering_type', ''),
        'roof_covering_class':     d.get('roof_covering_class', ''),
        'replacing_sheathing_yes': d.get('replacing_sheathing') == 'yes',
        'replacing_sheathing_no':  d.get('replacing_sheathing') == 'no',
        'metal_noncombustible':    bool(d.get('metal_noncombustible')),
        'astm_asphalt':            d.get('astm_type', 'asphalt') == 'asphalt',
        'astm_other':              d.get('astm_type') == 'other',
        'astm_other_text':         d.get('astm_other_text', ''),
        'fastener_staples':        d.get('fastener_staples', ''),
        'fastener_nails':          d.get('fastener_nails', ''),
        'fastener_other':          d.get('fastener_other', ''),
        'underlayment_self_adhering': bool(d.get('underlayment_self_adhering')),
        'underlayment_ice_barrier':   bool(d.get('underlayment_ice_barrier')),
        'date_p2':                 d.get('date', ''),
    }

    for page_idx in (0, 1):
        overlay.add_page()
        for name, spec in _permit_coords.FIELDS.items():
            if spec['page'] != page_idx:
                continue
            val = vals.get(name)
            overlay.set_font('Helvetica', size=spec['size'])
            y_top = ph - spec['y']  # coords file is bottom-left origin
            if spec.get('mark'):
                if val:
                    overlay.text(spec['x'], y_top, 'X')
                continue
            txt = _permit_text(val).strip()
            if not txt:
                continue
            if 'max_width' in spec:
                for i, line in enumerate(_permit_wrap(txt, overlay,
                                                      spec['max_width'],
                                                      spec.get('max_lines', 4))):
                    if line:
                        overlay.text(spec['x'], y_top + i * spec['line_height'], line)
            else:
                overlay.text(spec['x'], y_top, txt)

    try:
        overlay_reader = _pypdf.PdfReader(io.BytesIO(bytes(overlay.output())))
        base_reader = _pypdf.PdfReader(PERMIT_TEMPLATE_PATH)
        writer = _pypdf.PdfWriter()
        for i, base_page in enumerate(base_reader.pages):
            if i < len(overlay_reader.pages):
                base_page.merge_page(overlay_reader.pages[i])
            writer.add_page(base_page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
    except Exception as e:
        print(f'[permit] generation failed: {e}')
        return jsonify({'error': f'PDF generation failed: {e}'}), 500

    owner = re.sub(r'[^A-Za-z0-9 ]+', '', d.get('owner_name', '')).strip().replace(' ', '_') or 'Permit'
    fname = f'Loveland_Permit_{owner}_{d.get("date", "")}.pdf'
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=fname)


# ── Follow-up reminders ─────────────────────────────────────────────────────
# Emails the rep when a sent estimate sits unsigned. Runs hourly in a daemon
# thread. Gunicorn runs multiple workers, so an atomic lock file per
# (estimate, reminder-day) on the shared volume prevents duplicate emails.

REMINDER_DAYS      = [3, 7]
REMINDER_LOCKS_DIR = os.path.join(DATA_DIR, 'reminder_locks')
os.makedirs(REMINDER_LOCKS_DIR, exist_ok=True)


def send_followup_reminder(est, days_out):
    to_addr = _salesperson_email(est)
    if not to_addr:
        return
    c     = est.get('customer', {})
    cname = c.get('name', 'Customer')
    enum  = _est_number(est)
    total = _estimate_total(est)
    base  = _base_url()
    sign_url = f"{base}/sign/{est.get('share_token','')}"

    views = int(est.get('view_count') or 0)
    if views:
        last = est.get('last_viewed_at', '')
        try:
            dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
            last_fmt = dt.strftime('%b %d')
            view_line = f'Opened {views} time{"s" if views != 1 else ""} — last on {last_fmt}.'
        except Exception:
            view_line = f'Opened {views} time{"s" if views != 1 else ""}.'
        hint = 'They’ve looked but haven’t signed — a quick call could close this.'
    else:
        view_line = 'Never opened.'
        hint = 'They haven’t even opened it yet — worth re-sending the link or following up by phone.'

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#d97706;padding:22px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:8px">Project One Roofing</div>
    <h1 style="margin:0;font-size:22px;font-weight:800">&#9200; Estimate Still Unsigned</h1>
    <p style="margin:7px 0 0;opacity:.9;font-size:13px">{he(cname)}&rsquo;s estimate has been out for {days_out} days.</p>
  </div>
  <div style="padding:22px 26px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:18px">
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Customer</td><td style="padding:5px 0;font-size:13px;font-weight:700">{he(cname)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Estimate</td><td style="padding:5px 0;font-size:13px">{he(enum)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Value</td><td style="padding:5px 0;font-size:15px;font-weight:800;color:#d97706">{fc(total)}</td></tr>
      <tr><td style="padding:5px 14px 5px 0;color:#6b7280;font-size:13px">Activity</td><td style="padding:5px 0;font-size:13px">{he(view_line)}</td></tr>
    </table>
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 18px">{he(hint)}</p>
    <a href="{he(sign_url)}" style="display:block;text-align:center;background:#1a3a5c;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:700;font-size:14px">
      View Customer Estimate →</a>
  </div>
</div>
</body></html>'''
    _send_email(f'⏰ {cname}’s estimate unsigned for {days_out} days ({enum})',
                html_body, to_addr)


def _email_configured():
    """True when any email path is configured — SendGrid API or SMTP.
    (Delivery prefers the SendGrid HTTP API; SMTP_HOST alone is not required.)"""
    return bool(os.environ.get('SENDGRID_API_KEY', '').strip()
                or os.environ.get('SMTP_HOST', '').strip()
                or (os.environ.get('SMTP_USER', '').strip() == 'apikey'
                    and os.environ.get('SMTP_PASS', '').strip()))


def _check_reminders():
    if not _email_configured():
        return
    now = datetime.utcnow()
    for est in est_iter():
        if est.get('signature') or not est.get('share_token'):
            continue
        if _is_lost(est):
            continue
        sent_at = est.get('sent_at')
        if not sent_at:
            # Shared before this feature existed — start its clock now, no email
            est['sent_at'] = now.isoformat() + 'Z'
            try:
                est_save(est)
            except Exception:
                pass
            continue
        try:
            sent_dt = datetime.fromisoformat(sent_at.replace('Z', ''))
        except Exception:
            continue
        days = (now - sent_dt).days
        # Claim locks for every crossed threshold, but send at most ONE email —
        # an estimate crossing 3d and 7d in the same check (e.g. shared before
        # this feature deployed) shouldn't get two identical reminders.
        newly_claimed = False
        for d in REMINDER_DAYS:
            if days < d:
                continue
            lock = os.path.join(REMINDER_LOCKS_DIR, f"{est.get('estimate_id','x')}_{d}.lock")
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                newly_claimed = True
            except (FileExistsError, OSError):
                continue
        if newly_claimed:
            try:
                send_followup_reminder(est, days)
            except Exception as exc:
                print(f"[reminders] send failed for {est.get('estimate_id','?')}: {exc}")


# ── Backups ─────────────────────────────────────────────────────────────────
# Two layers: a nightly email with all estimate JSONs (the irreplaceable data),
# and an on-demand full-archive download (everything incl. photos).

BACKUP_EMAIL = os.environ.get('BACKUP_EMAIL', 'luke@projectoneroofing.com').strip()


def _build_backup_zip(include_uploads=True):
    """Zip the data directory into memory. Returns bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Estimates come from the storage layer (not a directory walk) so the
        # backup keeps working when the backend moves off flat files.
        for doc in est_iter():
            est_id = doc.get('estimate_id') or 'unknown'
            try:
                zf.writestr(f'estimates/{est_id}.json', json.dumps(doc, indent=2))
            except Exception:
                pass
        if include_uploads:
            for dirpath, _dirs, files in os.walk(UPLOADS_DIR):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    rel  = os.path.join('uploads', os.path.relpath(full, UPLOADS_DIR))
                    try:
                        zf.write(full, rel)
                    except OSError:
                        pass
        # Every manager-edited config file belongs here — these are hand-entered
        # and unrecoverable. permit_defaults/jurisdictions were missing.
        for cfg in ('price_book.json', 'tier_defaults.json', 'config.json',
                    'sales_goals.json', 'permit_defaults.json',
                    'jurisdictions.json', 'commercial_fastening.json'):
            full = os.path.join(DATA_DIR, cfg)
            if os.path.exists(full):
                try:
                    zf.write(full, cfg)
                except OSError:
                    pass
    return buf.getvalue()


@app.route('/api/test-notification', methods=['POST'])
def test_notification():
    """Send a test email to the currently logged-in rep to verify notification delivery."""
    user = session.get('user', '')
    if not user:
        return jsonify({'error': 'not logged in'}), 401
    to_addr = f'{user}@projectoneroofing.com'
    ok = _send_email(
        f'🔔 Test notification — Project One Estimator',
        f'<p style="font-family:system-ui,sans-serif;padding:24px">This is a test notification sent to <strong>{to_addr}</strong>. '
        f'If you received this, signature notifications are working correctly.</p>',
        to_addr
    )
    return jsonify({'ok': bool(ok), 'sent_to': to_addr})


@app.route('/api/find-estimate')
def find_estimate():
    """Admin: find estimates by approximate total — helps recover data after accidental overwrites."""
    if not _is_admin(_current_user()):
        return _forbid()
    near = float(request.args.get('total', 688))
    tolerance = float(request.args.get('tol', 50))
    result = []
    try:
        for d in est_iter():
            try:
                t = _estimate_total(d)
                if abs(t - near) <= tolerance:
                    c = d.get('customer', {})
                    a = c.get('address', {})
                    result.append({
                        'estimate_id':  d.get('estimate_id', ''),
                        'customer':     c.get('name', ''),
                        'phone':        c.get('phone', ''),
                        'email':        c.get('email', ''),
                        'street':       a.get('street', ''),
                        'city':         a.get('city', ''),
                        'state':        a.get('state', ''),
                        'salesperson':  d.get('salesperson', ''),
                        'total':        round(t, 2),
                        'date':         d.get('estimate_date', ''),
                        'status':       d.get('status', ''),
                        'updated_at':   d.get('updated_at', ''),
                        'notes_internal': (d.get('notes_internal') or '')[:200],
                    })
            except Exception:
                pass
    except OSError:
        pass
    result.sort(key=lambda x: abs(x['total'] - near))
    return jsonify(result)


@app.route('/api/signed-estimates')
def list_signed_estimates():
    """Admin: list all signed estimates so phantom ones can be identified and deleted."""
    if not _is_admin(_current_user()):
        return _forbid()
    result = []
    try:
        for d in est_iter():
            try:
                if d.get('signature'):
                    sig = d.get('signature', {})
                    c   = d.get('customer', {})
                    result.append({
                        'estimate_id': d.get('estimate_id', ''),
                        'customer':    c.get('name', '(no name)'),
                        'salesperson': d.get('salesperson', ''),
                        'total':       round(_estimate_total(d), 2),
                        'signed_at':   sig.get('signed_at', ''),
                        'status':      d.get('status', ''),
                    })
            except Exception:
                pass
    except OSError:
        pass
    return jsonify(result)


@app.route('/api/backup')
def download_backup():
    """Admin-only: full on-demand backup — estimates + photos + config."""
    if not _is_admin(_current_user()):
        return _forbid()
    data = _build_backup_zip(include_uploads=True)
    stamp = datetime.utcnow().strftime('%Y-%m-%d')
    return send_file(io.BytesIO(data), mimetype='application/zip',
                     as_attachment=True,
                     download_name=f'p1_estimator_full_backup_{stamp}.zip')


def _send_nightly_backup():
    """Email the estimates-only backup zip (small, no photos)."""
    if not BACKUP_EMAIL:
        return
    data  = _build_backup_zip(include_uploads=False)
    stamp = datetime.utcnow().strftime('%Y-%m-%d')
    n_est = est_count()
    size_mb = len(data) / 1048576

    base = _base_url()
    if size_mb > 20:
        attachments = None
        body_extra = (f'<p style="font-size:13px;color:#b45309">The backup zip is '
                      f'{size_mb:.1f} MB — too large to attach. '
                      f'<a href="{he(base)}/api/backup">Download the full backup here</a> '
                      f'(sign-in required).</p>')
    else:
        attachments = [(f'p1_estimates_backup_{stamp}.zip', data)]
        body_extra = ''

    html_body = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#1a3a5c;padding:20px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:6px">Project One Roofing</div>
    <h1 style="margin:0;font-size:19px;font-weight:800">&#128190; Nightly Estimate Backup</h1>
  </div>
  <div style="padding:20px 26px">
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 12px">
      Attached is tonight&rsquo;s backup of all <strong>{n_est}</strong> estimates
      ({size_mb:.1f} MB zipped), including signatures and contract data.
      Photos are not included &mdash; grab a
      <a href="{he(base)}/api/backup">full backup with photos</a> anytime from the app.</p>
    {body_extra}
    <p style="font-size:11px;color:#9ca3af;margin:14px 0 0">
      Keep a few of these emails around — any one of them can fully restore your estimate data.</p>
  </div>
</div>
</body></html>'''
    _send_email(f'💾 Estimator nightly backup — {stamp} ({n_est} estimates)',
                html_body, BACKUP_EMAIL, attachments=attachments)


def _check_daily_backup():
    if not _email_configured():
        return
    stamp = datetime.utcnow().strftime('%Y-%m-%d')
    lock  = os.path.join(REMINDER_LOCKS_DIR, f'backup_{stamp}.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except (FileExistsError, OSError):
        return
    try:
        _send_nightly_backup()
    except Exception as exc:
        print(f'[backup] nightly backup failed: {exc}')


def _check_daily_db_backup():
    """Nightly off-platform copy of the three SQLite databases.

    Lives here rather than in the portal because this is where the working
    hourly loop and the configured email sender already are — under the merged
    deploy it is all one process anyway. The zip itself is built by
    portal/backup.py, which knows about WAL.

    Its own lockfile, separate from the estimator's: if one of the two jobs
    fails, the other must still run.
    """
    if not _email_configured():
        return
    stamp = datetime.utcnow().strftime('%Y-%m-%d')
    lock  = os.path.join(REMINDER_LOCKS_DIR, f'dbbackup_{stamp}.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except (FileExistsError, OSError):
        return
    try:
        from portal import backup as pbackup
        pbackup.nightly_email(_send_email, BACKUP_EMAIL, _base_url())
    except Exception as exc:
        print(f'[backup] nightly database backup failed: {exc}')


def _reminder_loop():
    time.sleep(30)  # let the app finish booting
    while True:
        try:
            _check_reminders()
        except Exception as exc:
            print(f'[reminders] check failed: {exc}')
        try:
            _check_daily_backup()
        except Exception as exc:
            print(f'[backup] check failed: {exc}')
        try:
            _check_daily_db_backup()
        except Exception as exc:
            print(f'[backup] database check failed: {exc}')
        time.sleep(3600)


threading.Thread(target=_reminder_loop, daemon=True).start()


# ── Launch ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import threading, webbrowser
    port = int(os.environ.get('PORT', 5000))
    if port == 5000:
        threading.Timer(1.2, lambda: webbrowser.open('http://localhost:5000')).start()
    base = PUBLIC_URL or f'http://{LAN_IP}:{port}'
    print(f"  Estimate Builder running at http://localhost:{port}")
    print(f"  Customer share links will use: {base}")
    if LAN_IP == '127.0.0.1' and not PUBLIC_URL:
        print("  ⚠  Could not detect LAN IP — share links will only work on this machine.")
        print("     Set a PUBLIC_URL in estimator/config.json for external access.")
    app.run(debug=False, port=port, host='0.0.0.0')
