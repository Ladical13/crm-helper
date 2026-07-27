import io
import os
import re
import math
import json
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
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash

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
app.secret_key = os.environ.get('SESSION_SECRET', secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Secure cookies over HTTPS in production (Railway). Allow plain HTTP for
    # local dev so the login flow works on http://localhost / the LAN IP.
    SESSION_COOKIE_SECURE=bool(os.environ.get('DATA_DIR')),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,  # cap uploads at 30 MB
)

# Shared setup code for first-time password enrollment. Set SIGNUP_CODE in the
# environment; blank it out once everyone has enrolled to disable new sign-ups.
SIGNUP_CODE = os.environ.get('SIGNUP_CODE', '').strip()

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

# ── User accounts (per-user passwords) ──────────────────────────────────────
# Stored as JSON in DATA_DIR alongside estimates/settings:
#   { "luke": {"pw_hash": "...", "is_admin": true}, ... }
# A user with no pw_hash has not enrolled yet; first login sets it (gated by
# SIGNUP_CODE). 'luke' is seeded as the admin who can reset other users.
# Path resolved lazily because DATA_DIR is defined further down this module.
def _users_file():
    return os.path.join(DATA_DIR, 'users.json')

def load_users():
    path = _users_file()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_users(users):
    with open(_users_file(), 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2)

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
    rec = load_users().get(username) or {}
    role = rec.get('role')
    if role in ('admin', 'manager', 'rep'):
        return role
    if rec.get('is_admin') or username == 'luke':
        return 'admin'
    return 'rep'

def _is_admin(username):
    return _get_role(username) == 'admin'

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
    'login',             # the login page / form
    'logout',            # clears the session
    'customer_sign',     # /sign/<token> — public, protected by the 192-bit token
    'sign_change_order', # /sign-co/<token> — same token protection as /sign
    'serve_upload',      # /uploads/<file> — cover photos shown on the customer view
    'static',            # JS/CSS for the login + app shell (non-sensitive client code)
    'pwa_manifest',      # /manifest.json — needed for PWA install before login
    'service_worker',    # /sw.js — service worker scope must be public
}

DISABLE_AUTH = os.environ.get('DISABLE_AUTH', '').strip().lower() in ('1', 'true', 'yes')

@app.before_request
def _require_login():
    if DISABLE_AUTH or request.endpoint in PUBLIC_ENDPOINTS or session.get('user'):
        return
    # Unauthenticated: JSON 401 for API calls (the SPA redirects), else to login.
    if request.path.startswith('/api/'):
        return jsonify({'error': 'authentication required'}), 401
    return redirect('/login')

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
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)

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
                  'jurisdictions.json'):
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


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        username  = (request.form.get('username') or '').strip().lower()
        password  = request.form.get('password') or ''
        code      = (request.form.get('signup_code') or '').strip()

        if username not in TEAM_MEMBERS:
            error = 'Please select your name from the list.'
        else:
            users = load_users()
            rec   = users.get(username) or {}
            if rec.get('pw_hash'):
                # Enrolled — verify password.
                if check_password_hash(rec['pw_hash'], password):
                    session.permanent = True
                    session['user'] = username
                    return redirect('/')
                error = 'Incorrect password. Try again.'
            else:
                # First time — enroll with the shared setup code + a new password.
                if not password and not code:
                    error = 'First time signing in? Enter the team setup code and choose a password.'
                elif not SIGNUP_CODE:
                    error = 'Sign-up is disabled. Ask Luke to set you up.'
                elif code != SIGNUP_CODE:
                    error = 'Incorrect setup code.'
                elif len(password) < 8:
                    error = 'Choose a password of at least 8 characters.'
                else:
                    users[username] = {
                        'pw_hash':  generate_password_hash(password),
                        'is_admin': username == 'luke',
                    }
                    save_users(users)
                    session.permanent = True
                    session['user'] = username
                    return redirect('/')

    options = ''.join(
        f'<option value="{u}">{_display_name(u)}</option>'
        for u in TEAM_MEMBERS
    )
    error_html = f'<p class="login-error">{error}</p>' if error else ''

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign In — Project One Roofing Estimator</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#f3f4f6;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:#fff;border-radius:10px;box-shadow:0 4px 24px rgba(0,0,0,.12);
  padding:40px;width:100%;max-width:360px;text-align:center}}
.card img{{height:64px;margin-bottom:22px}}
h1{{font-size:19px;font-weight:800;color:#1a3a5c;margin-bottom:4px}}
.sub{{font-size:13px;color:#6b7280;margin-bottom:24px}}
.stripe{{height:4px;border-radius:2px;margin-bottom:28px;
  background:linear-gradient(90deg,#22c7da 0 33%,#ffd400 33% 66%,#ee3d42 66% 100%)}}
select{{width:100%;padding:11px 14px;border:1px solid #d1d5db;border-radius:6px;
  font-size:16px;background:#fff;margin-bottom:14px;
  appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%236b7280' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center}}
select:focus{{outline:none;border-color:#1a3a5c;box-shadow:0 0 0 3px rgba(26,58,92,.12)}}
input{{width:100%;padding:11px 14px;border:1px solid #d1d5db;border-radius:6px;
  font-size:16px;background:#fff;margin-bottom:14px}}
input:focus{{outline:none;border-color:#1a3a5c;box-shadow:0 0 0 3px rgba(26,58,92,.12)}}
button{{width:100%;padding:12px;background:#1a3a5c;color:#fff;border:none;
  border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}}
button:hover{{background:#0e2440}}
.login-error{{color:#dc2626;font-size:13px;margin-bottom:12px}}
.login-hint{{font-size:11px;color:#9ca3af;margin:-6px 0 14px;line-height:1.45;text-align:left}}
.setup-row{{border-top:1px solid #eef0f3;margin-top:6px;padding-top:14px}}
.setup-row summary{{font-size:12px;color:#1a3a5c;cursor:pointer;margin-bottom:12px;
  font-weight:600;list-style:none}}
.setup-row summary::-webkit-details-marker{{display:none}}
</style></head><body>
<div class="card">
  <img src="/static/logo.png" alt="Project One Roofing">
  <h1>Estimate Builder</h1>
  <p class="sub">Sign in to continue</p>
  <div class="stripe"></div>
  {error_html}
  <form method="POST">
    <select name="username" required>
      <option value="">Select your name…</option>
      {options}
    </select>
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" required>
    <details class="setup-row">
      <summary>First time signing in?</summary>
      <p class="login-hint">Enter the team setup code (ask Luke) and choose a password above — it'll be saved as your login.</p>
      <input type="text" name="signup_code" placeholder="Team setup code" autocomplete="off">
    </details>
    <button type="submit">Sign In →</button>
  </form>
</div>
</body></html>'''


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/me')
def me():
    user = session.get('user', '')
    rec  = load_users().get(user) or {}
    return jsonify({
        'username': user,
        'display_name': _display_name(user) if user else '',
        'email': f'{user}@projectoneroofing.com' if user else '',
        'is_admin': bool(rec.get('is_admin')),
        'role': _get_role(user) if user else 'rep',
        # True when an admin set a temporary password the user must replace.
        'must_change': bool(rec.get('must_change')),
    })


@app.route('/api/users', methods=['GET'])
def list_users():
    """Admin-only: enrollment status for every team member."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    users = load_users()
    team  = load_team()
    return jsonify([
        {'username': m['username'],
         'display_name': m.get('display_name') or _display_name(m['username']),
         'phone':        m.get('phone', ''),
         'email':        m.get('email', ''),
         'enrolled':     bool((users.get(m['username']) or {}).get('pw_hash')),
         'must_change':  bool((users.get(m['username']) or {}).get('must_change')),
         'is_admin':     _get_role(m['username']) == 'admin',
         'role':         _get_role(m['username'])}
        for m in team
    ])


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
    users = load_users()
    rec = users.get(username) or {}
    rec['pw_hash']     = generate_password_hash(password)
    rec['must_change'] = True
    rec.setdefault('is_admin', username == 'luke')
    users[username] = rec
    save_users(users)
    return jsonify({'ok': True, 'username': username})


@app.route('/api/users/<username>/reset', methods=['POST'])
def reset_user(username):
    """Admin-only: clear a user's password so a fresh one can be set."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    users = load_users()
    if username in users:
        users[username].pop('pw_hash', None)
        users[username].pop('must_change', None)
        save_users(users)
    return jsonify({'ok': True, 'reset': username})


@app.route('/api/users/<username>/set-role', methods=['POST'])
def set_user_role(username):
    """Admin-only: assign a role (admin, manager, rep) to a team member."""
    if not _is_admin(session.get('user', '')):
        return jsonify({'error': 'admin only'}), 403
    username = (username or '').strip().lower()
    role = (request.get_json(force=True) or {}).get('role', '')
    if role not in ('admin', 'manager', 'rep'):
        return jsonify({'error': 'role must be admin, manager, or rep'}), 400
    users = load_users()
    rec = users.get(username) or {}
    rec['role']     = role
    rec['is_admin'] = (role == 'admin')
    users[username] = rec
    save_users(users)
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
    users = load_users()
    rec = users.get(username) or {}
    rec['role']     = role
    rec['is_admin'] = (role == 'admin')
    users[username] = rec
    save_users(users)
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
    users = load_users()
    users.pop(username, None)
    save_users(users)
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
    users = load_users()
    rec = users.get(user) or {}
    rec['pw_hash'] = generate_password_hash(password)
    rec['must_change'] = False
    rec.setdefault('is_admin', user == 'luke')
    users[user] = rec
    save_users(users)
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
                'customer_name':   c.get('name', ''),
                'city':            a.get('city', ''),
                'estimate_date':   d.get('estimate_date', ''),
                'status':          d.get('status', 'draft'),
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
    c = est.get('customer', {})
    if c.get('name') and not c['name'].startswith('Copy of '):
        c['name'] = 'Copy of ' + c['name']
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


@app.route('/api/customer-notes/<path:name>', methods=['GET'])
def get_customer_notes(name):
    """Return the customer-level notes string for a given customer name."""
    try:
        if os.path.exists(CUSTOMER_NOTES_FILE):
            with open(CUSTOMER_NOTES_FILE, 'r', encoding='utf-8') as f:
                notes = json.load(f)
            return jsonify({'notes': notes.get(name.lower().strip(), '')})
    except Exception:
        pass
    return jsonify({'notes': ''})


@app.route('/api/customer-notes/<path:name>', methods=['PUT'])
def set_customer_notes(name):
    """Persist customer-level notes for a given customer name."""
    text = (request.get_json(force=True) or {}).get('notes', '')
    notes = {}
    try:
        if os.path.exists(CUSTOMER_NOTES_FILE):
            with open(CUSTOMER_NOTES_FILE, 'r', encoding='utf-8') as f:
                notes = json.load(f)
    except Exception:
        pass
    notes[name.lower().strip()] = text
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
    VALID = {'draft', 'sent', 'accepted', 'declined'}
    status = (request.json or {}).get('status')
    if status not in VALID:
        return jsonify({'error': 'Invalid status'}), 400
    est = est_load(est_id)
    if est is None:
        return jsonify({'error': 'Not found'}), 404
    if not _can_touch_estimate(est):
        return _forbid()
    est['status'] = status
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    est_save(est)
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


@app.route('/api/parse-xactimate', methods=['POST'])
def parse_xactimate():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file.'}), 400
    try:
        data = _parse_xactimate_pdf(f.read())
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {e}'}), 400
    if not any(s.get('items') for s in data['sections']):
        return jsonify({'error': "Couldn’t find Xactimate line items in this PDF. "
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

GBB_TRADES = ['roofing', 'siding', 'windows', 'gutters', 'other']


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
    trade_mode = td.get('mode', 'simple' if trade == 'gutters' else 'gbb')
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
        if td.get('mode', 'simple' if tk == 'gutters' else 'gbb') != 'gbb':
            continue
        out.append(tk)
    return out


def _pick_summary_label(est):
    """Human label for what was/is selected: 'Better Package' for a single
    G/B/B product, 'Roofing: Better · Siding: Good' for a mix."""
    tls  = dict(roofing='Roofing', siding='Siding', windows='Windows',
                gutters='Gutters', other='Other / Misc')
    lbls = dict(good='Good', better='Better', best='Best')
    tks  = [tk for tk in _gbb_trade_keys(est)
            if ((est.get('trades') or {}).get(tk) or {}).get('line_items')]
    if not tks:
        return ''
    if len(tks) == 1:
        return lbls.get(_trade_tier(est, tks[0]), 'Better') + ' Package'
    return ' · '.join(f'{tls[tk]}: {lbls.get(_trade_tier(est, tk), "Better")}' for tk in tks)


def _trade_tier_content(est, trade):
    """(features, descriptions) dicts for one trade's package cards. Legacy
    estimates carried ONE estimate-level set — it belongs to the first GBB
    trade so pre-existing shared estimates keep their copy."""
    td = (est.get('trades') or {}).get(trade) or {}
    feats = td.get('tier_features')
    descs = td.get('tier_descriptions')
    if not isinstance(feats, dict):
        first = (_gbb_trade_keys(est) or [None])[0]
        feats = est.get('tier_features') if trade == first else {}
        if not isinstance(descs, dict):
            descs = est.get('tier_descriptions') if trade == first else {}
    if not isinstance(descs, dict):
        descs = {}
    return (feats or {}), (descs or {})


@app.route('/api/analytics')
def get_analytics():
    """Per-trade and per-rep revenue, cost, and margin across all estimates."""
    TRADE_NAMES = ['roofing', 'siding', 'windows', 'gutters', 'other']
    by_trade = {}
    by_rep   = {}
    monthly  = {}  # 'YYYY-MM' → cumulative signed revenue

    # ── New aggregations ──────────────────────────────────────────────
    funnel = {'total': 0, 'sent': 0, 'viewed': 0, 'signed': 0, 'declined': 0}
    pipeline_aging = {
        'fresh':  {'count': 0, 'value': 0.0},   # 0–3 days
        'active': {'count': 0, 'value': 0.0},   # 4–14 days
        'stale':  {'count': 0, 'value': 0.0},   # 15–30 days
        'cold':   {'count': 0, 'value': 0.0},   # 30+ days
    }
    by_type = {
        'retail':    {'revenue': 0.0, 'count': 0, 'pipeline': 0.0},
        'insurance': {'revenue': 0.0, 'count': 0, 'pipeline': 0.0},
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
        if est.get('status') == 'declined': funnel['declined'] += 1

        # ── Pipeline aging (open, sent estimates only) ────────────────
        if is_sent and not is_signed and est.get('status') != 'declined':
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

        # ── YTD & city ───────────────────────────────────────────────
        if is_signed:
            signed_dt_str = (est.get('signature') or {}).get('signed_at') or ''
            try:
                signed_dt = datetime.fromisoformat(signed_dt_str.replace('Z','').replace('+00:00',''))
                if signed_dt >= ytd_cutoff:
                    ytd_revenue += est_total
            except Exception:
                pass
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
            tmode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
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
                # Monthly revenue bucketing
                signed_at = (est.get('signature') or {}).get('signed_at') or ''
                if signed_at:
                    try:
                        month_key = signed_at[:7]  # 'YYYY-MM'
                        monthly[month_key] = monthly.get(month_key, 0.0) + tsell
                    except Exception:
                        pass
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

    sorted_months = sorted(monthly.items())[-12:]
    top_cities_list = sorted(top_cities.items(), key=lambda x: -x[1])[:8]

    # Re-collect all days-to-close across all reps
    all_dtc_flat = []
    for d in by_rep.values():
        pass  # already done per-rep; collect from finalized data

    avg_dtc_all = None
    dtc_all = [d['avg_days_to_close'] for d in by_rep.values() if d.get('avg_days_to_close') is not None]
    if dtc_all:
        avg_dtc_all = round(sum(dtc_all) / len(dtc_all), 1)

    return jsonify({
        'by_trade':       by_trade,
        'by_rep':         by_rep,
        'monthly':        sorted_months,
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
    'other':   [('color', 'Color / Finish')],
}
_PRODUCT_TRADE_LABELS = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters', other='Other')


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
      <h3>Product Selection</h3>
      <table class="cvprod-tbl"><tbody>{trs}</tbody></table>
    </div>'''


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

    labels  = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters', other='Other / Misc')
    trades  = est.get('trades', {})
    parts   = []
    gtotal  = 0.0

    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if only_trades is not None and tk not in only_trades:
            continue
        if not td.get('enabled') or not td.get('line_items'):
            continue
        # Determine trade mode: gutters always simple; others default gbb
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
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
        lp_ths = '<th class="cvth-r">Unit Price</th><th class="cvth-r">Total</th>' if show_lp else ''
        parts.append(f'''<div class="cvtrade">
          <div class="cvtrade-hd">{lbl}</div>
          <table class="cvt"><thead><tr>
            <th>Description</th><th class="cvth-c">Qty</th>
            <th class="cvth-c">Unit</th>{lp_ths}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
          <tfoot><tr><td colspan="{ncols - 1}" class="cvsub-l">{lbl} Subtotal</td>
            <td class="cvr cvsub">{fc(sub)}</td></tr></tfoot>
          </table></div>''')

    return '\n'.join(parts), gtotal


_CV_CSS = """
:root{--navy:#1a3a5c;--navy2:#0e2440;--navy3:#2c5580;--ink:#101a2c;--mut:#5b6b81;--faint:#93a1b5;
--line:#e3e9f1;--bg:#eef2f6;--cyan:#22c7da;--gold:#ffd400;--red:#ee3d42;--green:#16a34a;
--r:16px;--sh:0 1px 2px rgba(15,23,42,.05),0 10px 30px -18px rgba(15,23,42,.22)}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{font-family:'Plus Jakarta Sans',system-ui,-apple-system,'Segoe UI',sans-serif;font-size:14.5px;
color:var(--ink);background:linear-gradient(180deg,#f7f9fc 0%,var(--bg) 320px);min-height:100vh;
-webkit-font-smoothing:antialiased}
img{max-width:100%}
body.cv-has-stick{padding-bottom:96px}

/* ── header ── */
.cvhdr{background:rgba(255,255,255,.94);position:relative;z-index:5;padding:12px clamp(14px,4vw,28px);
display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:1px solid var(--line)}
.cvhdr-logo-wrap{display:inline-flex;align-items:center}
.cvhdr img{height:50px;width:auto;display:block}
.cvhdr-contact{display:flex;flex-direction:column;align-items:flex-end;gap:4px;text-align:right}
.cvhdr-contact a{display:inline-flex;align-items:center;gap:7px;background:var(--navy);color:#fff;
font-weight:700;font-size:13.5px;text-decoration:none;padding:9px 16px;border-radius:999px;
box-shadow:0 6px 14px -6px rgba(26,58,92,.55);transition:transform .15s}
.cvhdr-contact a:active{transform:scale(.96)}
.cvhdr-contact span{color:var(--faint);font-size:10.5px;letter-spacing:.3px}
.cvbrand-stripe{height:4px;background:linear-gradient(90deg,var(--cyan) 0 33.3%,var(--gold) 33.3% 66.6%,var(--red) 66.6% 100%)}

/* ── hero ── */
.cvhero{position:relative;background:radial-gradient(130% 150% at 88% -30%,var(--navy3) 0%,var(--navy) 48%,var(--navy2) 100%);
color:#fff;padding:46px 20px 42px;text-align:center;overflow:hidden}
.cvhero::before{content:'';position:absolute;width:360px;height:360px;border-radius:50%;
background:radial-gradient(circle,rgba(34,199,218,.16),transparent 65%);top:-150px;left:-110px}
.cvhero-brand{position:relative;font-size:11px;font-weight:800;letter-spacing:3px;text-transform:uppercase;color:var(--cyan);margin-bottom:12px}
.cvhero h1{position:relative;font-size:clamp(24px,5.5vw,34px);font-weight:800;letter-spacing:-.6px;margin-bottom:8px}
.cvhero p{position:relative;font-size:14.5px;opacity:.85;max-width:540px;margin:0 auto;line-height:1.55}
.cvhero.ok{background:radial-gradient(130% 150% at 88% -30%,#22a558 0%,#178a44 48%,#0d6630 100%)}
.cvsteps{position:relative;display:flex;justify-content:center;gap:8px;margin-top:22px;flex-wrap:wrap}
.cvstep{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.09);
border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:6px 14px 6px 7px;font-size:12px;font-weight:600;color:#fff}
.cvstep b{display:inline-flex;width:21px;height:21px;border-radius:50%;background:var(--cyan);color:var(--navy2);
font-size:11px;font-weight:800;align-items:center;justify-content:center}

/* ── cover-photo hero ── */
.cvcover{position:relative;overflow:hidden;background:var(--navy2)}
.cvcover img{width:100%;height:min(500px,62vh);object-fit:cover;display:block}
.cvcover-shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,25,41,.25) 0%,rgba(10,25,41,.05) 40%,rgba(10,25,41,.9) 100%)}
.cvcover-text{position:absolute;left:0;right:0;bottom:0;padding:34px 20px 30px;text-align:center;color:#fff}
.cvcover-text h1{font-size:clamp(24px,5.5vw,36px);font-weight:800;letter-spacing:-.6px;margin-bottom:8px;text-shadow:0 2px 14px rgba(0,0,0,.55)}
.cvcover-text p{font-size:14.5px;opacity:.95;text-shadow:0 1px 6px rgba(0,0,0,.6)}
@media(max-width:520px){.cvcover img{height:340px}}
.cv-check{width:76px;height:76px;margin:0 auto 16px;border-radius:50%;background:rgba(255,255,255,.14);
border:2.5px solid rgba(255,255,255,.9);display:flex;align-items:center;justify-content:center;
font-size:38px;line-height:1;animation:cvpop .6s cubic-bezier(.34,1.56,.64,1) both}
@keyframes cvpop{from{transform:scale(.25);opacity:0}to{transform:scale(1);opacity:1}}
.cv-print-btn{margin-top:18px;background:rgba(255,255,255,.16);border:1.5px solid rgba(255,255,255,.55);
color:#fff;padding:11px 24px;border-radius:999px;font-size:14px;font-weight:700;font-family:inherit;cursor:pointer;transition:background .15s}
.cv-print-btn:hover{background:rgba(255,255,255,.28)}

/* ── layout + cards ── */
.cvmain{max-width:960px;margin:0 auto;padding:6px 0 46px}
@media(min-width:720px){.cvmain{padding:14px 22px 60px}}
.cvc-card,.cvnotes,.cvproducts,.cvintro,.cvphotos,.cvcond,.cvnext,.cvrep-card{background:#fff;border:1px solid var(--line);
border-radius:var(--r);box-shadow:var(--sh);margin:16px 16px 0;padding:18px}
.cvgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}
.cvgi label{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:var(--faint);font-weight:700;display:block;margin-bottom:3px}
.cvgi strong{font-size:14px;font-weight:700;color:var(--ink);line-height:1.4}
.cvnotes h3,.cvproducts h3,.cvphotos h3,.cvcond>h3,.cvnext h3,.cvrep-card h3{display:flex;align-items:center;gap:9px;
font-size:11.5px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:var(--navy);margin-bottom:12px}
.cvnotes h3::before,.cvproducts h3::before,.cvphotos h3::before,.cvcond>h3::before,.cvnext h3::before,.cvrep-card h3::before{
content:'';width:20px;height:3.5px;border-radius:2px;background:linear-gradient(90deg,var(--cyan),var(--gold));flex-shrink:0}
.cvnotes p{font-size:14px;line-height:1.7;color:#33415a;white-space:pre-wrap}
.cvintro{padding:22px}
.cvintro-logo{height:40px;width:auto;display:block;margin-bottom:14px}
.cvintro p{font-size:14.5px;line-height:1.8;color:#33415a;white-space:pre-wrap}

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
.cv-tier-section{margin:6px 16px 0}
.cv-tier-heading{display:flex;align-items:center;gap:9px;font-size:12.5px;font-weight:800;text-transform:uppercase;
letter-spacing:1px;color:var(--navy);padding:20px 0 12px}
.cv-tier-heading::before{content:'';width:20px;height:3.5px;border-radius:2px;background:linear-gradient(90deg,var(--cyan),var(--gold));flex-shrink:0}
.cv-tier-cards{display:grid;gap:12px;margin-bottom:6px}
.cv-tier-card{border:2px solid var(--line);border-radius:var(--r);padding:22px 14px 18px;text-align:center;cursor:pointer;
transition:transform .18s,box-shadow .18s,background .18s;background:#fff;position:relative;
-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;box-shadow:0 2px 10px -4px rgba(15,23,42,.12)}
.cv-tier-card:hover{transform:translateY(-3px);box-shadow:0 14px 30px -14px rgba(15,23,42,.35)}
.cv-tier-card.cv-tier-selected{transform:translateY(-3px);box-shadow:0 18px 36px -16px rgba(15,23,42,.4)}
.cv-tier-popular{position:absolute;top:-11px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#1d8a4b,var(--green));
color:#fff;font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;padding:4px 12px;border-radius:999px;
white-space:nowrap;box-shadow:0 4px 10px -3px rgba(22,163,74,.6)}
.cv-tier-name{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:6px}
.cv-tier-price{font-size:27px;font-weight:800;letter-spacing:-.5px;margin-bottom:6px;font-variant-numeric:tabular-nums}
.cv-tier-desc{font-size:11.5px;color:var(--mut);margin-bottom:8px;line-height:1.5}
.cv-tier-feats{list-style:none;margin:10px 0 6px;padding:10px 2px 0;border-top:1px dashed var(--line);text-align:left;
font-size:11.5px;color:#33415a;line-height:1.55}
.cv-tier-feats li{position:relative;padding:2px 0 2px 18px}
.cv-tier-feats li::before{content:'✓';position:absolute;left:0;font-weight:800;color:var(--green)}
.cv-tier-feats .cv-tier-more{color:var(--faint);font-style:italic}
.cv-tier-feats .cv-tier-more::before{content:''}
.cv-tier-check{font-size:12.5px;font-weight:700;color:var(--mut);border:1.5px solid var(--line);border-radius:999px;
padding:7px 18px;display:inline-block;margin-top:8px;transition:all .15s;background:#fff}
.cv-tier-selected .cv-tier-check{background:var(--navy);border-color:var(--navy);color:#fff}

/* ── line-item tables ── */
.cvtrade{margin:16px 16px 0;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh);background:#fff}
.cvtrade-hd{background:linear-gradient(135deg,var(--navy),var(--navy2));color:#fff;padding:12px 18px;font-size:12px;
font-weight:800;letter-spacing:1px;text-transform:uppercase}
.cvt{width:100%;border-collapse:collapse;background:#fff}
.cvt th{padding:9px 14px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.6px;background:#f8fafc;
border-bottom:1px solid var(--line);color:var(--mut);font-weight:700}
.cvth-c{width:52px;text-align:center !important}
.cvth-r{width:92px;text-align:right !important}
.cvt td{padding:9px 14px;border-bottom:1px solid #f4f7fa;font-size:13px;vertical-align:top}
.cvt tbody tr:last-child td{border-bottom:none}
.cvn{font-weight:600}
.cvd{font-size:11px;color:var(--mut);font-weight:400;margin-top:2px;line-height:1.5}
.cvc{text-align:center;color:var(--mut)}
.cvc-desc{font-weight:400;color:var(--mut)}
.cvr{text-align:right;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.cvt tfoot td{background:#f8fafc;font-weight:800;padding:11px 14px;border-top:2px solid var(--line);font-size:13px}
.cvsub-l{text-align:right;color:var(--mut);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;padding-right:12px}
.cvsub{color:var(--navy);font-size:14px}
.cvhidden-note{font-size:10.5px;color:var(--faint);font-style:italic;padding:6px 14px;text-align:left}
.cv-section-row td{background:#eef4fb!important;color:var(--navy);font-weight:800;font-size:10px;
text-transform:uppercase;letter-spacing:.6px;padding:7px 14px!important}
.cv-section-sub td{background:#f8fafc;font-weight:700;font-size:11.5px;color:var(--navy);
border-bottom:1px solid var(--line);padding:7px 14px}
.cv-section-sub td:first-child{text-align:right}

/* ── grand total ── */
.cvgrand{margin:16px 16px 0;background:linear-gradient(135deg,var(--navy) 0%,var(--navy2) 100%);color:#fff;
padding:18px 22px;border-radius:var(--r);display:flex;justify-content:space-between;align-items:center;gap:12px;
box-shadow:0 14px 30px -14px rgba(14,36,64,.55);position:relative;overflow:hidden}
.cvgrand::before{content:'';position:absolute;left:0;top:0;bottom:0;width:5px;
background:linear-gradient(180deg,var(--cyan),var(--gold),var(--red))}
.cvgrand-lbl{font-size:13px;font-weight:600;opacity:.85}
.cvgrand-amt{font-size:clamp(22px,6vw,30px);font-weight:800;letter-spacing:-.5px;font-variant-numeric:tabular-nums}

/* ── contract / terms ── */
.cvcontract{margin:16px 16px 0;background:#fff;border-radius:var(--r);border:1px solid var(--line);overflow:hidden;box-shadow:var(--sh)}
.cvcontract summary{padding:15px 18px;cursor:pointer;font-weight:700;font-size:13.5px;color:var(--navy);list-style:none;
display:flex;align-items:center;justify-content:space-between;gap:10px}
.cvcontract summary::-webkit-details-marker{display:none}
.cvcontract summary::after{content:'▾';color:var(--faint);transition:transform .2s}
.cvcontract[open] summary::after{transform:rotate(180deg)}
.cvcontract[open] summary{border-bottom:1px solid var(--line)}
.cvcontract-body{padding:16px 18px;font-size:11.5px;line-height:1.75;color:#4b5a72;white-space:pre-wrap;
max-height:300px;overflow-y:auto;background:#fafbfd}

/* ── signature area ── */
.cvsig{margin:24px 16px 0;padding:26px 20px 20px;background:#fff;border-radius:var(--r);border:1px solid var(--line);
box-shadow:0 18px 44px -20px rgba(14,36,64,.35);position:relative;overflow:hidden}
.cvsig::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;
background:linear-gradient(90deg,var(--cyan) 0 33.3%,var(--gold) 33.3% 66.6%,var(--red) 66.6% 100%)}
.cvsig h2{font-size:20px;font-weight:800;color:var(--navy);letter-spacing:-.3px;margin-bottom:5px}
.cvsig .sub{font-size:13px;color:var(--mut);margin-bottom:18px;line-height:1.6}
.cvfield{display:block;margin-bottom:12px}
.cvfield>span{display:block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);margin-bottom:6px}
.cvfield em{font-style:normal;text-transform:none;letter-spacing:0;color:var(--faint);font-weight:500}
.cvinput{width:100%;border:1.5px solid #d7dfe9;border-radius:11px;padding:13px 15px;font-size:16px;font-family:inherit;
outline:none;color:var(--ink);background:#fbfcfe;transition:border-color .15s,box-shadow .15s}
.cvinput:focus{border-color:var(--navy);box-shadow:0 0 0 4px rgba(26,58,92,.1);background:#fff}
.cv-sigpad{border:1.5px dashed #b9c7d8;border-radius:12px;background:#fbfcfe;padding:16px 16px 10px;text-align:center;margin:4px 0 16px}
#cv-sig-script{font-family:'Great Vibes','Segoe Script',cursive;font-size:38px;line-height:1.25;color:var(--navy);
min-height:48px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cv-sigpad-hint{font-size:10px;color:var(--faint);border-top:1.5px solid #dbe3ee;margin-top:6px;padding-top:8px;
text-transform:uppercase;letter-spacing:.6px;font-weight:600}
.cvagree{display:flex;align-items:flex-start;gap:11px;font-size:13px;color:#33415a;margin-bottom:16px;line-height:1.55;
cursor:pointer;background:#f8fafc;border:1px solid var(--line);border-radius:11px;padding:13px 14px}
.cvagree input{margin-top:1px;flex-shrink:0;width:19px;height:19px;cursor:pointer;accent-color:var(--navy)}
.cvbtn{width:100%;padding:16px;background:linear-gradient(135deg,#1d8a4b,#16a34a);color:#fff;border:none;border-radius:12px;
font-size:16.5px;font-weight:800;font-family:inherit;cursor:pointer;margin-bottom:12px;
box-shadow:0 12px 26px -10px rgba(22,163,74,.55);transition:transform .15s,box-shadow .15s}
.cvbtn:hover{transform:translateY(-1px);box-shadow:0 16px 30px -10px rgba(22,163,74,.6)}
.cvbtn:active{transform:scale(.98)}
.cvlegal{font-size:10.5px;color:var(--faint);text-align:center;line-height:1.6}
.cv-shingle{background:#f0f9ff;border:1px solid #bae6fd;border-radius:12px;padding:14px 15px;margin-bottom:14px}
.cv-shingle-label{font-size:11px;font-weight:800;color:#0c4a6e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}
.cv-shingle-locked{font-size:17px;font-weight:800;color:var(--navy)}
.cv-shingle-select{margin-bottom:0;background:#fff}
.cv-initials{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 15px;margin-bottom:14px}
.cv-initials-title{font-size:11px;font-weight:800;color:#92400e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px}
.cv-initial-row{display:flex;align-items:center;gap:12px;padding:9px 0;border-top:1px solid #fef3c7}
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
.cvnext-n{position:absolute;left:0;top:0;width:31px;height:31px;border-radius:50%;
background:linear-gradient(135deg,var(--navy3),var(--navy2));color:#fff;font-weight:800;font-size:13px;
display:flex;align-items:center;justify-content:center;box-shadow:0 5px 12px -5px rgba(14,36,64,.6)}
.cvnext-t{font-weight:800;font-size:14px;color:var(--ink);padding-top:5px;margin-bottom:3px}
.cvnext-d{font-size:13px;color:var(--mut);line-height:1.6}

/* ── your consultant ── */
.cvrep{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.cvrep-av{width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,var(--navy3),var(--navy2));color:#fff;
font-weight:800;font-size:19px;display:flex;align-items:center;justify-content:center;letter-spacing:1px;flex-shrink:0;
box-shadow:0 6px 14px -6px rgba(14,36,64,.5)}
.cvrep-info{min-width:130px}
.cvrep-name{font-weight:800;font-size:15.5px;color:var(--ink)}
.cvrep-role{font-size:12px;color:var(--mut);margin-top:1px}
.cvrep-btns{display:flex;gap:8px;flex:1 1 100%;margin-top:6px}
@media(min-width:560px){.cvrep-btns{flex:0 0 auto;margin-top:0;margin-left:auto}}
.cvrep-btn{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:11px 16px;border-radius:11px;
font-size:13.5px;font-weight:700;text-decoration:none;border:1.5px solid var(--line);color:var(--navy);background:#fff;
white-space:nowrap;transition:transform .15s,background .15s}
.cvrep-btn:active{transform:scale(.97)}
.cvrep-btn.pri{background:var(--navy);border-color:var(--navy);color:#fff;box-shadow:0 8px 16px -8px rgba(26,58,92,.6)}

/* ── sticky sign bar ── */
.cvstick{position:fixed;left:0;right:0;bottom:0;z-index:60;padding:10px 12px calc(10px + env(safe-area-inset-bottom));
transform:translateY(130%);transition:transform .35s cubic-bezier(.22,.61,.36,1);pointer-events:none}
.cvstick.on{transform:none;pointer-events:auto}
.cvstick-in{max-width:660px;margin:0 auto;background:rgba(14,36,64,.97);backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,.08);border-radius:16px;box-shadow:0 20px 44px -14px rgba(14,36,64,.65);
display:flex;align-items:center;gap:14px;padding:12px 12px 12px 18px;color:#fff}
.cvstick-t{display:flex;flex-direction:column;min-width:0}
.cvstick-lbl{font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;opacity:.65;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46vw}
.cvstick-amt{font-size:20px;font-weight:800;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
.cvstick-btn{margin-left:auto;background:linear-gradient(135deg,#1d8a4b,#16a34a);color:#fff;border:none;border-radius:12px;
padding:13px 18px;font-size:14px;font-weight:800;font-family:inherit;cursor:pointer;white-space:nowrap;
box-shadow:0 10px 20px -8px rgba(22,163,74,.7);transition:transform .15s}
.cvstick-btn:active{transform:scale(.96)}

/* ── attachments ── */
.cv-att-list{display:flex;flex-direction:column;gap:10px}
.cv-att{display:inline-flex;align-items:center;gap:9px;background:#f8fafc;border:1px solid var(--line);border-radius:11px;
padding:13px 16px;font-size:14px;font-weight:700;color:var(--navy);text-decoration:none;transition:border-color .15s,background .15s}
.cv-att:hover{background:#eef2f7;border-color:var(--faint)}
.cv-att-doc{display:flex;flex-direction:column;gap:10px;margin-bottom:8px}
.cv-att-doc-title{font-size:13.5px;font-weight:800;color:var(--navy)}
.cv-att-page{width:100%;display:block;border:1px solid var(--line);border-radius:10px;box-shadow:0 2px 8px -3px rgba(15,23,42,.15)}

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
.cvftr{position:relative;text-align:center;padding:36px 18px 44px;background:var(--navy2);color:rgba(255,255,255,.55);
line-height:1.7;margin-top:28px}
.cvftr::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;
background:linear-gradient(90deg,var(--cyan) 0 33.3%,var(--gold) 33.3% 66.6%,var(--red) 66.6% 100%)}
.cvftr-logo{height:44px;width:auto;background:#fff;padding:8px 16px;border-radius:12px;margin-bottom:14px}
.cvftr strong{color:#fff;font-size:15.5px;display:block;margin-bottom:4px;letter-spacing:.3px}
.cvftr-c{font-size:12.5px}
.cvftr-c a{color:rgba(255,255,255,.78);text-decoration:none;font-weight:600}
.cvftr-sub{font-size:10.5px;margin-top:10px;opacity:.7}

/* ── condition report ── */
.cvcond-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:8px;margin-bottom:12px}
.cvcond-cell{text-align:center;border:1px solid var(--line);border-radius:12px;padding:11px 4px;background:#fbfcfe}
.cvcond-cell-lbl{font-size:10px;font-weight:700;color:#33415a;margin-bottom:6px;line-height:1.3}
.cvcond-letter{font-size:22px;font-weight:800;width:44px;height:44px;line-height:44px;border-radius:50%;margin:0 auto 5px}
.cvcond-word{font-size:10px;font-weight:800}
.cvcond-exec{font-size:12.5px;line-height:1.65;color:#33415a;background:#f8fafc;border-left:3px solid var(--navy);
padding:10px 12px;border-radius:0 10px 10px 0;margin-bottom:10px}
.cvcond-sec{margin-top:14px;border-top:1px solid var(--line);padding-top:13px}
.cvcond-sec-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.cvcond-sec-hd h4{font-size:13.5px;color:var(--navy);margin:0}
.cvcond-badge{font-size:11px;font-weight:800;border-radius:999px;padding:4px 11px;white-space:nowrap}
.cvcond-meta{font-size:11.5px;color:var(--mut);margin-bottom:6px}
.cvcond-summary{font-size:12.5px;line-height:1.65;color:#33415a;margin-bottom:8px}
.cvcond-sh{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:var(--navy);margin:9px 0 5px}
.cvcond-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px}
.cvcond-tbl th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);
background:#f8fafc;padding:5px 8px;border-bottom:1px solid var(--line)}
.cvcond-tbl td{padding:6px 8px;border-bottom:1px solid #f4f7fa;vertical-align:top;line-height:1.5}
.cvcond-tbl tr:last-child td{border-bottom:none}
.cvcond-cost-total td{font-weight:800;border-top:2px solid var(--line);background:#f8fafc}
.cvcond-foot{font-size:10px;color:var(--faint);line-height:1.6;margin-top:12px;border-top:1px solid #f1f5f9;padding-top:9px}
.cvcond .cvph-grid{margin-top:10px}

/* ── trust blocks ── */
.cvtrust-body p{font-size:13.5px;line-height:1.7;color:#33415a;margin-bottom:8px}
.cvtrust-body p:last-child{margin-bottom:0}
.cvtrust-certs{list-style:none;margin:0;padding:0}
.cvtrust-certs li{position:relative;padding:5px 0 5px 24px;font-size:13.5px;color:#33415a;line-height:1.55}
.cvtrust-certs li::before{content:'✓';position:absolute;left:2px;font-weight:800;color:var(--green)}
.cvtrust-revs{display:grid;gap:10px}
@media(min-width:640px){.cvtrust-revs{grid-template-columns:1fr 1fr}}
.cvtrust-rev{background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.cvtrust-rev-stars{color:#f59e0b;font-size:14px;letter-spacing:2px;margin-bottom:5px}
.cvtrust-rev-text{font-size:13px;line-height:1.65;color:#33415a;font-style:italic}
.cvtrust-rev-name{font-size:12px;font-weight:800;color:var(--navy);margin-top:7px}

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
.cvt td[data-l]::before{content:attr(data-l);color:var(--faint);font-weight:700;font-size:9.5px;text-transform:uppercase;
letter-spacing:.5px;margin-right:5px}
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
.cert{border-width:1.5pt}}
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


def _cv_head(title):
    """Shared <head> + opening <body> for every public customer page."""
    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0e2440">
<link rel="icon" href="/static/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Great+Vibes&display=swap" rel="stylesheet">
<title>{title}</title>
<style>{_CV_CSS}</style></head><body>'''


def _cv_header():
    """Shared top bar: logo left, tap-to-call pill right, brand stripe."""
    return f'''<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:{COMPANY_PHONE_DIGITS}">&#128222; {COMPANY_PHONE_DISPLAY}</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>'''


def _cv_footer(extra=''):
    """Shared footer + the shared behavior script. Closes the document."""
    return f'''<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
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


def _cv_next_steps(signed=False):
    """'What happens next' timeline — sets expectations on the sign page and
    reassures on the confirmation page."""
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
         'We order your materials and lock in an installation date that works for your schedule.'),
        ('Installation day',
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
    return f'<div class="cvnext"><h3>{title}</h3><ol class="cvnext-list">{items}</ol></div>'


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

    return f'''<div class="cvrep-card"><h3>Questions? We&rsquo;re Here to Help</h3>
  <div class="cvrep">
    <div class="cvrep-av">{he(initials)}</div>
    <div class="cvrep-info"><div class="cvrep-name">{he(name)}</div><div class="cvrep-role">{role}</div></div>
    <div class="cvrep-btns">
      <a class="cvrep-btn pri" href="tel:{phone_digits}">&#128222; Call</a>
      <a class="cvrep-btn" href="sms:{phone_digits}">&#128172; Text</a>
      {email_btn}
    </div>
  </div></div>'''


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
  <img src="/static/logo.png" class="cvintro-logo" alt="Project One Roofing">
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
  <h3>Photo Report</h3>
  <div class="cvph-grid">{figs}</div>
</div>{_CV_ANN_JS}'''


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
<table class="cvcond-tbl"><thead><tr><th>Area</th><th>Severity</th><th>Description</th></tr></thead>
<tbody>{find_rows}</tbody></table>''' if find_rows else '')

        rec_rows = ''
        for rec in (sec.get('recommendations') or []):
            if not rec.get('description'):
                continue
            rec_rows += (f'<tr><td style="white-space:nowrap"><strong>{he(_RH_PRI.get(rec.get("priority"), rec.get("priority") or ""))}</strong></td>'
                         f'<td>{he(rec.get("description") or "")}</td>'
                         f'<td style="white-space:nowrap">{he(rec.get("cost_range") or "—")}</td></tr>')
        rec_html = (f'''<div class="cvcond-sh">Recommendations</div>
<table class="cvcond-tbl"><thead><tr><th>Priority</th><th>Description</th><th>Est. Cost</th></tr></thead>
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
  <h3>{w_title}</h3>
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


def _cv_shingle_block(est):
    """Shingle-color step for the sign form. Locked display if the rep already
    chose a color; otherwise a required dropdown for the customer."""
    ss = est.get('shingle_selection') or {}
    if not ss.get('enabled', False) or not _roofing_enabled(est):
        return ''
    chosen  = (ss.get('chosen') or '').strip()
    options = [o for o in (ss.get('options') or []) if str(o).strip()]
    if chosen:
        return f'''<div class="cv-shingle">
      <div class="cv-shingle-label">&#127912; Your Shingle Color</div>
      <div class="cv-shingle-locked">{he(chosen)}</div>
      <input type="hidden" name="shingle_color" value="{he(chosen)}">
    </div>'''
    opts = ''.join(f'<option value="{he(o)}">{he(o)}</option>' for o in options)
    return f'''<div class="cv-shingle">
      <div class="cv-shingle-label">&#127912; Choose Your Shingle Color *</div>
      <select class="cvinput cv-shingle-select" name="shingle_color" required>
        <option value="">Select a color&hellip;</option>
        {opts}
      </select>
    </div>'''


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
    return f'<div class="cvnotes"><h3>Documents &amp; Reports</h3><div class="cv-att-list">{blocks}</div></div>'


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

    out = ''
    for key, dflt_title, icon in (('about', 'About Us', '&#127968;'),
                                  ('warranty', 'Our Warranty', '&#128737;&#65039;')):
        blk = _blk(key)
        body = (blk.get('body') or '').strip() if blk else ''
        if not body:
            continue
        paras = ''.join(f'<p>{he(p.strip())}</p>'
                        for p in body.split('\n\n') if p.strip())
        title = (blk.get('title') or '').strip() or dflt_title
        out += f'''<div class="cvnotes cvtrust">
      <h3>{icon} {he(title)}</h3>
      <div class="cvtrust-body">{paras}</div>
    </div>'''

    blk = _blk('certifications')
    if blk:
        items = [str(i).strip() for i in (blk.get('items') or []) if str(i).strip()]
        if items:
            title = (blk.get('title') or '').strip() or 'Licenses & Certifications'
            lis = ''.join(f'<li>{he(i)}</li>' for i in items)
            out += f'''<div class="cvnotes cvtrust">
      <h3>&#127942; {he(title)}</h3>
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
      <h3>&#11088; {he(title)}</h3>
      <div class="cvtrust-revs">{cards}</div>
    </div>'''

    return out


def _signed_extras_html(est):
    """Chosen shingle color + captured initials, for the signed confirmation page."""
    sig = est.get('signature', {}) or {}
    out = ''
    color = (sig.get('shingle_color') or '').strip()
    if color:
        out += (f'<div class="cvc-card"><div class="cvgrid">'
                f'<div class="cvgi"><label>Shingle Color</label><strong>{he(color)}</strong></div>'
                f'</div></div>')
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
            <th>Item Name</th><th>Description</th>
            <th class="cvth-r">ACV</th><th class="cvth-r">Depreciation</th>
            <th class="cvth-r">RCV</th></tr></thead>
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

    notes_html  = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html  = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html     = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''
    carrier_row = f'<div class="cvgi"><label>Insurance Carrier</label><strong>{he(carrier)}</strong></div>' if carrier else ''
    claim_row   = f'<div class="cvgi"><label>Claim #</label><strong>{he(claim_num)}</strong></div>' if claim_num else ''
    scope_html  = f'<div class="cvnotes"><h3>Scope of Work</h3><p>{he(scope_notes)}</p></div>' if scope_notes else ''

    return _cv_head('Your Insurance Estimate &mdash; Project One Roofing') + _cv_header() + f'''
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

{_cv_intro_block(est)}

{_cv_photos_block(est)}

{_cv_products_block(est)}

{ins_table}
{scope_html}
{notes_html}
{_cv_condition_block(est)}
{_cv_attachments_block(est)}
{_cv_trust_blocks(est)}
{ctext_html}
{_cv_next_steps()}
{_cv_contact_card(est)}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the insurance estimate above and all terms &amp; conditions.</p>
  {_cv_sig_form(f'/sign/{he(token)}',
                hidden='<input type="hidden" name="selected_tier" value="insurance">',
                extra_blocks=_cv_shingle_block(est) + _cv_initials_block(est),
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
    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if not td.get('enabled') or not td.get('line_items'):
            continue
        found_any = True
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
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

    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    li_html, grand_total = render_line_items(est, tier=tier)

    return _cv_head('Your Estimate — Project One Roofing') + _cv_header() + f'''
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

{_cv_intro_block(est)}

{_cv_photos_block(est)}

{_cv_products_block(est)}

{li_html}

<div class="cvgrand" style="margin-top:14px">
  <span class="cvgrand-lbl">Total</span>
  <span class="cvgrand-amt">{fc(grand_total)}</span>
</div>

{notes_html}
{_cv_condition_block(est)}
{_cv_attachments_block(est)}
{_cv_trust_blocks(est)}
{ctext_html}
{_cv_next_steps()}
{_cv_contact_card(est)}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the estimate above and all terms &amp; conditions.</p>
  {_cv_sig_form(f'/sign/{he(token)}',
                hidden=f'<input type="hidden" name="selected_tier" value="{he(tier)}">',
                extra_blocks=_cv_shingle_block(est) + _cv_initials_block(est),
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

    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    tier_clrs = dict(good='#2563eb', better='#16a34a', best='#b45309')
    tier_bgs  = dict(good='#dbeafe', better='#dcfce7', best='#fef3c7')
    tier_lbls = dict(good='Good',    better='Better',  best='Best')

    # Each G/B/B product gets its own package choice; simple-mode trades are
    # priced as-is and always shown. Total = sum of the customer's picks.
    gbb_tks    = [tk for tk in _gbb_trade_keys(est)
                  if ((est.get('trades') or {}).get(tk) or {}).get('line_items')]
    simple_tks = [tk for tk in GBB_TRADES if tk not in gbb_tks]
    multi      = len(gbb_tks) > 1

    trade_lbls = dict(roofing='Roofing', siding='Siding', windows='Windows',
                      gutters='Gutters', other='Other / Misc')

    defaults = {}      # trade → default-selected tier
    totals   = {}      # trade → {tier: subtotal}
    sections_html = ''
    for tk in gbb_tks:
        tfeat, tdesc = _trade_tier_content(est, tk)
        d_tier = _trade_tier(est, tk)
        if d_tier not in enabled_tiers:
            d_tier = enabled_tiers[0]
        defaults[tk] = d_tier
        totals[tk]   = {t: _trade_subtotal(est, tk, t) for t in enabled_tiers}

        cards_html = ''
        for t in enabled_tiers:
            total  = totals[tk][t]
            desc   = (tdesc.get(t) or '').strip()
            clr    = tier_clrs[t]
            bg     = tier_bgs[t]
            lbl    = tier_lbls[t]
            is_sel = t == d_tier
            popular_badge = '<div class="cv-tier-popular">Most Popular</div>' if t == 'better' else ''
            desc_el = f'<div class="cv-tier-desc">{he(desc)}</div>' if desc else ''
            # "What's Included" bullets the rep curates on the Options page — shown
            # to the customer making the Good/Better/Best decision.
            feats = [str(f).strip() for f in (tfeat.get(t) or []) if str(f).strip()]
            feats_el = ''
            if feats:
                feats_el = ('<ul class="cv-tier-feats">'
                            + ''.join(f'<li>{he(f)}</li>' for f in feats[:8])
                            + (f'<li class="cv-tier-more">+ {len(feats) - 8} more included</li>' if len(feats) > 8 else '')
                            + '</ul>')
            cards_html += f'''<div class="cv-tier-card {'cv-tier-selected' if is_sel else ''}"
              data-trade="{tk}" data-tier="{t}"
              style="border-color:{clr};{'background:'+bg if is_sel else ''}"
              onclick="selectCvTier('{tk}','{t}')">
              {popular_badge}
              <div class="cv-tier-name" style="color:{clr}">{lbl}</div>
              <div class="cv-tier-price" style="color:{clr}">{fc(total)}</div>
              {desc_el}
              {feats_el}
              <div class="cv-tier-check" id="cv-check-{tk}-{t}">{'&#10003; Selected' if is_sel else 'Select'}</div>
            </div>'''

        heading = (f'{trade_lbls.get(tk, tk.title())} &mdash; Choose Your Package'
                   if multi else 'Choose Your Package')
        sections_html += f'''<div class="cv-tier-section">
  <div class="cv-tier-heading">{heading}</div>
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

    return _cv_head('Your Estimate — Project One Roofing') + _cv_header() + f'''
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

{_cv_intro_block(est)}

{_cv_photos_block(est)}

{_cv_products_block(est)}

{sections_html}

{simple_html}

<div class="cvgrand" style="margin-top:14px" id="cv-grand-bar">
  <span class="cvgrand-lbl" id="cv-grand-lbl">Total &mdash; {default_lbl}</span>
  <span class="cvgrand-amt" id="cv-grand-amt">{fc(default_total)}</span>
</div>

{notes_html}
{_cv_condition_block(est)}
{_cv_attachments_block(est)}
{_cv_trust_blocks(est)}
{ctext_html}
{_cv_next_steps()}
{_cv_contact_card(est)}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub" id="cv-sig-sub">Your electronic signature confirms you have reviewed and agreed to the
    <strong id="cv-sig-tier">{default_lbl}</strong> and all terms above.</p>
  {_cv_sig_form(f'/sign/{he(token)}',
                hidden=(f'<input type="hidden" name="selected_tier" id="cv-tier-input" value="{he(default_tier)}">'
                        + ''.join(f'<input type="hidden" name="tier_{tk}" id="cv-tier-input-{tk}" value="{he(defaults[tk])}">'
                                  for tk in gbb_tks)),
                extra_blocks=_cv_shingle_block(est) + _cv_initials_block(est),
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
var _tier_clrs   = {{good:'#2563eb',better:'#16a34a',best:'#b45309'}};
var _tier_bgs    = {{good:'#dbeafe',better:'#dcfce7',best:'#fef3c7'}};
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
        card.style.background=_tier_bgs[t];
        chk.innerHTML='&#10003; Selected';
      }}else{{
        card.classList.remove('cv-tier-selected');
        card.style.background='';
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
</script>
''' + _cv_footer()


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
    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract" open><summary>&#128203; Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    email_row  = f'<tr><td>Email</td><td>{he(semail)}</td></tr>' if semail else ''
    hash_disp  = (dhash[:32] + '&hellip;') if len(dhash) > 32 else he(dhash)

    fname = (sname.split() or [''])[0]
    hero_h1 = f'You&rsquo;re All Set, {he(fname)}!' if fname else 'Estimate Accepted!'
    return _cv_head('Estimate Accepted &mdash; Project One Roofing') + _cv_header() + f'''
<div class="cvhero ok">
  <div class="cvhero-brand" style="color:#86efac">Project One Roofing</div>
  <div class="cv-check">&#10003;</div>
  <h1>{hero_h1}</h1>
  <p>Thank you &mdash; your signed copy is below. Project One Roofing will be in touch soon to schedule your project.</p>
  <button class="cv-print-btn" onclick="window.print()">&#128424; Save / Print Signed Copy</button>
</div>

<main class="cvmain">
{_cv_next_steps(signed=True)}

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


def _send_email(subject, html_body, to_addr, cc=None, attachments=None):
    """Send an HTML email. Prefers the SendGrid HTTP API (HTTPS/443), which works
    on hosts that block outbound SMTP ports like Railway; falls back to SMTP when
    no API key is available. Logs errors, never raises.
    attachments: list of (filename, bytes) tuples."""
    if not to_addr:
        return False

    # Prefer the SendGrid Web API when we have a key. SendGrid's SMTP login uses
    # the literal username "apikey" and the API key as the password, so we can
    # reuse SMTP_PASS as the API key when SENDGRID_API_KEY isn't set explicitly.
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key and os.environ.get('SMTP_USER', '').strip() == 'apikey':
        api_key = os.environ.get('SMTP_PASS', '').strip()
    if api_key and http is not None:
        if _send_via_sendgrid_api(api_key, subject, html_body, to_addr, cc, attachments):
            return True
        # API failed — try SMTP as a last resort (may also be blocked)
    return _send_via_smtp(subject, html_body, to_addr, cc, attachments)


def _send_via_sendgrid_api(api_key, subject, html_body, to_addr, cc=None, attachments=None):
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


def _send_via_smtp(subject, html_body, to_addr, cc=None, attachments=None):
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

    _send_email(subject, html_body, to_addr, cc=notify_cc or None)


# ── Signed-contract PDF + CRM push ──────────────────────────────────────────

def _pdf_safe(s):
    """fpdf2 core fonts are latin-1 only; swap common unicode for ASCII."""
    if s is None:
        return ''
    s = str(s)
    for k, v in {'—': '-', '–': '-', '‘': "'", '’': "'",
                 '“': '"', '”': '"', '•': '*', '·': '-',
                 '✓': '[x]', '×': 'x', '…': '...',
                 '→': '->', ' ': ' '}.items():
        s = s.replace(k, v)
    return s.encode('latin-1', 'replace').decode('latin-1')


def build_signed_pdf(est):
    """Render the signed contract as a PDF (bytes) for CRM upload."""
    if FPDF is None:
        raise RuntimeError('fpdf2 not installed')

    c    = est.get('customer', {})
    a    = c.get('address', {})
    sig  = est.get('signature', {}) or {}
    enum = _est_number(est)
    is_ins = est.get('estimate_type') == 'insurance'
    tier = est.get('selected_tier', 'better')

    pdf = FPDF(orientation='P', unit='mm', format='Letter')
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(14, 14, 14)
    pdf.add_page()
    W = pdf.w - 28  # content width

    # Header: logo + company info
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

    # Title bar
    pdf.set_fill_color(26, 58, 92)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 13)
    title = 'SIGNED CONTRACT  -  INSURANCE CLAIM' if is_ins else 'SIGNED CONTRACT'
    pdf.cell(W, 10, f'  {title}', fill=True, new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # Info block
    addr_str = ', '.join(filter(None, [a.get('street'), a.get('city'),
                                       a.get('state'), a.get('zip')]))
    sp = (est.get('salesperson') or '').replace('.', ' ').title()
    signed_at = sig.get('signed_at', '')
    try:
        dt = datetime.fromisoformat(signed_at.replace('Z', '+00:00'))
        signed_fmt = dt.strftime('%B %d, %Y at %I:%M %p UTC')
    except Exception:
        signed_fmt = signed_at

    info_rows = [
        ('Estimate #', enum),
        ('Customer', c.get('name', '')),
        ('Phone', c.get('phone', '')),
        ('Email', c.get('email', '')),
        ('Address', addr_str),
        ('Estimate Date', est.get('estimate_date', '')),
        ('Salesperson', sp),
    ]
    job_num = c.get('crm_job_number') or ''
    if job_num:
        info_rows.append(('Job #', job_num))
    if is_ins:
        ins_td = est.get('trades', {}).get('insurance', {})
        if ins_td.get('carrier'):
            info_rows.append(('Insurance Carrier', ins_td['carrier']))
        if ins_td.get('claim_number'):
            info_rows.append(('Claim #', ins_td['claim_number']))
    else:
        info_rows.append(('Package', _pick_summary_label(est)
                          or dict(good='Good', better='Better',
                                  best='Best').get(tier, tier.title())))
    shingle_color = (sig.get('shingle_color') or '').strip()
    if shingle_color:
        info_rows.append(('Shingle Color', shingle_color))

    pdf.set_font('Helvetica', '', 9)
    for label, val in info_rows:
        if not val:
            continue
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(40, 5.5, _pdf_safe(label))
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(0, 5.5, _pdf_safe(val), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)

    def table_header(cols):
        pdf.set_fill_color(234, 239, 245)
        pdf.set_font('Helvetica', 'B', 8)
        for txt, w, align in cols:
            pdf.cell(w, 6.5, _pdf_safe(txt), border=1, fill=True, align=align)
        pdf.ln()

    def trade_title(txt):
        pdf.set_font('Helvetica', 'B', 10.5)
        pdf.set_text_color(26, 58, 92)
        pdf.cell(0, 7, _pdf_safe(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)

    def trunc(s, n):
        s = _pdf_safe(s)
        return s if len(s) <= n else s[:n - 1] + '...'

    grand = 0.0
    if is_ins:
        ins_td   = est.get('trades', {}).get('insurance', {})
        sections = ins_td.get('sections') or (
            [{'name': '', 'items': ins_td.get('line_items', [])}]
            if ins_td.get('line_items') else [])
        cols = [('Item', 48, 'L'), ('Description', 60, 'L'),
                ('ACV', 22, 'R'), ('Depreciation', 28, 'R'), ('RCV', 24, 'R')]
        for sec in sections:
            items = sec.get('items', [])
            if not items:
                continue
            trade_title(sec.get('name') or 'Insurance Estimate Items')
            table_header(cols)
            pdf.set_font('Helvetica', '', 8)
            sub = 0.0
            for it in items:
                acv = float(it.get('acv') or 0)
                dep = float(it.get('depreciation') or 0)
                tot = acv + dep  # RCV
                sub += tot
                pdf.cell(48, 6, trunc(it.get('name', ''), 32), border=1)
                pdf.cell(60, 6, trunc(it.get('description', ''), 42), border=1)
                pdf.cell(22, 6, fc(acv), border=1, align='R')
                pdf.cell(28, 6, fc(dep), border=1, align='R')
                pdf.cell(24, 6, fc(tot), border=1, align='R')
                pdf.ln()
            grand += sub
            pdf.set_font('Helvetica', 'B', 8.5)
            pdf.cell(158, 6.5, _pdf_safe((sec.get('name') or 'Section') + ' Subtotal  '),
                     border=1, align='R')
            pdf.cell(24, 6.5, fc(sub), border=1, align='R')
            pdf.ln(10)
        total_label = 'INSURANCE CLAIM TOTAL'
    else:
        pricing = est.get('pricing', {})
        mode    = pricing.get('mode', 'margin')
        labels  = dict(roofing='Roofing', siding='Siding', windows='Windows',
                       gutters='Gutters', other='Other / Misc')
        cols = [('Description', 92, 'L'), ('Qty', 16, 'R'), ('Unit', 16, 'C'),
                ('Unit Price', 29, 'R'), ('Total', 29, 'R')]
        for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
            td = est.get('trades', {}).get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
            t_tier = _trade_tier(est, tk)   # each product at its signed package
            r = _tier_rate(pricing, tk, t_tier)
            # Skip the whole trade if nothing will print (all zero-qty / excluded)
            if not any(
                    float(it.get('quantity') or 0) > 0 and
                    (trade_mode == 'simple'
                     or (it.get('tiers') or {}).get(t_tier, {}).get('included') is not False)
                    for it in td['line_items']):
                continue
            trade_title(labels.get(tk, tk.title()))
            table_header(cols)
            pdf.set_font('Helvetica', '', 8)
            sub = 0.0
            hidden = 0
            for it in td['line_items']:
                qty = float(it.get('quantity') or 0)
                if qty <= 0:
                    continue  # zero-quantity items are hidden from the customer
                if trade_mode == 'simple':
                    sp_  = float(it.get('unit_price') or 0)
                    line = sp_ * qty
                    desc = (it.get('description') or '').strip()
                else:
                    t    = (it.get('tiers') or {}).get(t_tier, {})
                    if t.get('included') is False:
                        continue  # item excluded from this package tier
                    line = _line_sell_total(it, t_tier, r, mode)
                    sp_  = line / qty
                    desc = t.get('description', '')
                sub += line
                if not it.get('customer_visible', True):
                    hidden += 1
                    continue
                name = _with_section(it, it.get('name', ''))
                if desc:
                    name = f'{name} - {desc}'
                pdf.cell(92, 6, trunc(name, 62), border=1)
                pdf.cell(16, 6, f'{qty:g}', border=1, align='R')
                pdf.cell(16, 6, trunc(it.get('unit', ''), 8), border=1, align='C')
                pdf.cell(29, 6, fc(sp_), border=1, align='R')
                pdf.cell(29, 6, fc(line), border=1, align='R')
                pdf.ln()
            if hidden:
                pdf.set_font('Helvetica', 'I', 7.5)
                pdf.cell(182, 5.5, 'Additional materials & supplies included in total',
                         border=1, align='C')
                pdf.ln()
                pdf.set_font('Helvetica', '', 8)
            grand += sub
            pdf.set_font('Helvetica', 'B', 8.5)
            pdf.cell(153, 6.5, _pdf_safe(labels.get(tk, tk.title()) + ' Subtotal  '),
                     border=1, align='R')
            pdf.cell(29, 6.5, fc(sub), border=1, align='R')
            pdf.ln(10)
        _sum = _pick_summary_label(est)
        total_label = (f'TOTAL - {_sum.upper()}' if _sum
                       else f'TOTAL - {tier.upper()} PACKAGE')

    # Grand total bar
    pdf.set_fill_color(26, 58, 92)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(W - 40, 9, f'  {_pdf_safe(total_label)}', fill=True)
    pdf.cell(40, 9, fc(grand) + '  ', fill=True, align='R')
    pdf.ln(14)
    pdf.set_text_color(0, 0, 0)

    # Scope of work / notes
    if is_ins:
        scope = (est.get('trades', {}).get('insurance', {}).get('scope_notes') or '').strip()
        if scope:
            pdf.set_font('Helvetica', 'B', 10)
            pdf.cell(0, 6, 'Scope of Work', new_x='LMARGIN', new_y='NEXT')
            pdf.set_font('Helvetica', '', 8.5)
            pdf.multi_cell(W, 4.6, _pdf_safe(scope))
            pdf.ln(4)
    notes = (est.get('notes_customer') or '').strip()
    if notes:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, 'Notes', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 8.5)
        pdf.multi_cell(W, 4.6, _pdf_safe(notes))
        pdf.ln(4)

    # Terms & conditions
    ctext = (est.get('contract_text') or '').strip()
    if ctext:
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(0, 7, 'Terms & Conditions', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(1)
        pdf.set_font('Helvetica', '', 7.5)
        pdf.multi_cell(W, 4.0, _pdf_safe(ctext))
        pdf.ln(6)

    # Initialed acknowledgements
    inits = [i for i in (sig.get('initials') or []) if (i.get('value') or '').strip()]
    if inits:
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, 'Initialed Acknowledgements', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(1)
        for it in inits:
            pdf.set_font('Helvetica', 'B', 8.5)
            pdf.cell(16, 5.4, _pdf_safe(it['value'].upper()), border=0)
            pdf.set_font('Helvetica', '', 8.5)
            pdf.multi_cell(W - 16, 5.4, _pdf_safe(it['text']),
                           new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

    # Signature block
    if pdf.get_y() > pdf.h - 75:
        pdf.add_page()
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
    sig_lines = [
        f"Signed by: {sig.get('name', '')}"
        + (f"  ({sig.get('email')})" if sig.get('email') else ''),
        f"Date: {signed_fmt}",
        f"IP Address: {sig.get('ip_address', '')}",
        f"Document SHA-256: {sig.get('document_hash', '')[:32]}...",
    ]
    for line in sig_lines:
        pdf.cell(0, 4.6, _pdf_safe(line), new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(18)
    pdf.set_draw_color(0, 0, 0)

    return bytes(pdf.output())


# Mirrors MEASURE_FIELDS in app.js (Scope page) — keep the two in sync.
MEASURE_LABELS = [
    ('Roof', [('roof_squares', 'Roof Area', 'SQ'), ('waste_pct', 'Waste', '%'),
              ('attic_sqft', 'Attic Area', 'SF'),
              ('low_slope_squares', 'Low Slope Area (2/12 or less) - rolled roofing', 'SQ'),
              ('steep_squares', 'Steep Area (7/12 and up)', 'SQ'),
              ('ridge_hip_lf', 'Ridge + Hip', 'LF'), ('valley_lf', 'Valley', 'LF'),
              ('eave_lf', 'Eaves', 'LF'), ('rake_lf', 'Rakes', 'LF'),
              ('step_flash_lf', 'Step Flashing', 'LF'), ('pipe_boots', 'Pipe Boots', 'EA'),
              ('turtle_vents', 'Turtle Vents', 'EA'), ('broan_4in', '4" Broan Vent', 'EA'),
              ('broan_8in', '8" Broan Vent', 'EA')]),
    ('Gutters', [('gutter_lf', 'Gutter', 'LF'), ('downspout_lf', 'Downspouts', 'LF')]),
    ('Siding', [('siding_squares', 'Siding Area', 'SQ'), ('siding_waste_pct', 'Waste', '%'),
                ('siding_outside_corners_lf', 'Outside Corners', 'LF'),
                ('siding_inside_corners_lf', 'Inside Corners', 'LF'),
                ('siding_j_channel_lf', 'J-Channel / Trim', 'LF'),
                ('siding_starter_lf', 'Starter Strip', 'LF'),
                ('siding_soffit_lf', 'Soffit', 'LF')]),
    ('Windows', [('windows_count', 'Windows', 'EA'), ('doors_count', 'Doors', 'EA')]),
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


def build_production_packet_pdf(est):
    """Work order + material order for the SIGNED package, for the crew and
    supplier. Deliberately contains NO pricing anywhere — it leaves the
    office. v1 reflects the signed contract only (change orders excluded)."""
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
    pdf.cell(W, 4, '970-776-0945  -  projectoneroofingcolorado.com', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_y(32)

    def title_bar(txt):
        pdf.set_fill_color(26, 58, 92)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Helvetica', 'B', 13)
        pdf.cell(W, 10, f'  {txt}', fill=True, new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

    def section_title(txt):
        pdf.set_font('Helvetica', 'B', 10.5)
        pdf.set_text_color(26, 58, 92)
        pdf.cell(0, 7, _pdf_safe(txt), new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)

    def table_header(cols):
        pdf.set_fill_color(234, 239, 245)
        pdf.set_font('Helvetica', 'B', 8)
        for txt, w, align in cols:
            pdf.cell(w, 6.5, _pdf_safe(txt), border=1, fill=True, align=align)
        pdf.ln()

    def trunc(s, n):
        s = _pdf_safe(s)
        return s if len(s) <= n else s[:n - 1] + '...'

    # ── Page 1: Work Order ──
    title_bar('PRODUCTION PACKET  -  WORK ORDER')

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
    for label, val in info_rows:
        if not val:
            continue
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(40, 5.5, _pdf_safe(label))
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(0, 5.5, _pdf_safe(val), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    # Product selections (brand/model/color per trade)
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
    if prod_rows:
        section_title('Product Selection')
        table_header([('Trade', 30, 'L'), ('Item', 60, 'L'), ('Selection', 92, 'L')])
        pdf.set_font('Helvetica', '', 8)
        for trade, label, v in prod_rows:
            pdf.cell(30, 6, trunc(trade, 18), border=1)
            pdf.cell(60, 6, trunc(label, 40), border=1)
            pdf.cell(92, 6, trunc(v, 62), border=1)
            pdf.ln()
        pdf.ln(4)

    # Scope of work — every line on the signed package (including items the
    # customer view hides), no prices. The crew works from this list.
    def _tier_items(td, trade_mode, t_tier):
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

    section_title('Scope of Work')
    if is_ins:
        table_header([('Item', 70, 'L'), ('Description', 112, 'L')])
        pdf.set_font('Helvetica', '', 8)
        for sec in _insurance_sections(est):
            for it in sec.get('items', []):
                pdf.cell(70, 6, trunc(it.get('name', ''), 46), border=1)
                pdf.cell(112, 6, trunc(it.get('description', ''), 76), border=1)
                pdf.ln()
        scope = (trades.get('insurance', {}).get('scope_notes') or '').strip()
        if scope:
            pdf.ln(2)
            pdf.set_font('Helvetica', '', 8.5)
            pdf.multi_cell(W, 4.6, _pdf_safe(scope))
        pdf.ln(4)
    else:
        for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
            td = trades.get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
            rows = list(_tier_items(td, trade_mode, _trade_tier(est, tk)))
            if not rows:
                continue
            section_title(labels.get(tk, tk.title()))
            table_header([('Work Item', 138, 'L'), ('Qty', 20, 'R'), ('Unit', 24, 'C')])
            pdf.set_font('Helvetica', '', 8)
            for it, qty, t in rows:
                name = _with_section(it, it.get('name', ''))
                desc = (t.get('description') or it.get('description') or '').strip()
                if desc:
                    name = f'{name} - {desc}'
                pdf.cell(138, 6, trunc(name, 92), border=1)
                pdf.cell(20, 6, f'{qty:g}', border=1, align='R')
                pdf.cell(24, 6, trunc(it.get('unit', ''), 10), border=1, align='C')
                pdf.ln()
            pdf.ln(4)

    notes = (est.get('notes_customer') or '').strip()
    if notes:
        section_title('Customer Notes')
        pdf.set_font('Helvetica', '', 8.5)
        pdf.multi_cell(W, 4.6, _pdf_safe(notes))
        pdf.ln(3)
    crew = (est.get('notes_internal') or '').strip()
    if crew:
        section_title('Crew Notes (internal)')
        pdf.set_font('Helvetica', '', 8.5)
        pdf.multi_cell(W, 4.6, _pdf_safe(crew))
        pdf.ln(3)

    # ── Ventilation cut-in: how much ridge vent to actually cut open, and
    # where. We run ridge vent the full ridge for looks but only cut the
    # code-required footage — this tells the crew exactly what to cut.
    m0 = est.get('measurements') or {}
    roofing = trades.get('roofing', {})
    has_ridge = any(it.get('vent_role') == 'ridge' for it in roofing.get('line_items', []))
    vent_cutin = est.get('vent_cutin') or {}
    if has_ridge or vent_cutin.get('image_filename'):
        try:
            ridge_lf = float(m0.get('ridge_lf') or 0)
        except (TypeError, ValueError):
            ridge_lf = 0.0
        vinfo = attic_ventilation(m0)
        raw_cut = math.ceil(vinfo['ridge_lf_required'])
        cutin = min(raw_cut, int(ridge_lf)) if ridge_lf > 0 else raw_cut
        full_sticks = math.ceil(ridge_lf / 4) if ridge_lf > 0 else 0
        section_title('Ventilation - Ridge Vent Cut-In')
        pdf.set_font('Helvetica', '', 8.5)
        if ridge_lf > 0:
            line1 = (f'Install ridge vent the FULL ridge ({ridge_lf:g} LF, '
                     f'{full_sticks} stick(s)) for a uniform look.')
        else:
            line1 = 'Install ridge vent the full ridge for a uniform look.'
        pdf.multi_cell(W, 4.6, _pdf_safe(line1))
        pdf.set_font('Helvetica', 'B', 9)
        if ridge_lf > 0 and cutin >= ridge_lf:
            cut_msg = f'CUT IN the full ridge (~{cutin:g} LF) for code ventilation.'
        else:
            cut_msg = f'CUT IN only ~{cutin:g} LF for code-required ventilation - see map for locations.'
        pdf.multi_cell(W, 4.8, _pdf_safe(cut_msg))
        note = (vent_cutin.get('notes') or '').strip()
        if note:
            pdf.set_font('Helvetica', '', 8.5)
            pdf.multi_cell(W, 4.6, _pdf_safe(note))
        img_fn = vent_cutin.get('image_filename')
        if img_fn:
            img_path = os.path.join(UPLOADS_DIR, *str(img_fn).split('/'))
            if os.path.exists(img_path):
                try:
                    pdf.ln(2)
                    pdf.set_font('Helvetica', 'I', 7.5)
                    pdf.set_text_color(120, 120, 120)
                    pdf.cell(0, 5, _pdf_safe('Cut-In Map (highlighted = cut open for ventilation):'),
                             new_x='LMARGIN', new_y='NEXT')
                    pdf.set_text_color(0, 0, 0)
                    pdf.image(img_path, w=min(W, 150))
                except Exception:
                    pass
        pdf.ln(4)

    # ── Page 2: Material Order ──
    pdf.add_page()
    title_bar('MATERIAL ORDER')

    m = est.get('measurements') or {}

    def _mnum(key):
        try:
            return float(m.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    meas_rows = []
    for group, fields in MEASURE_LABELS:
        for key, label, unit in fields:
            v = _mnum(key)
            if v:
                meas_rows.append((group, label, v, unit))
    # iw_second_row is a 0/1 toggle, not a MEASURE_LABELS field: a 2nd course of
    # ice & water at the eaves (code). The I&W line qty already includes it —
    # this row tells the crew WHY the footage is doubled.
    if _mnum('iw_second_row'):
        meas_rows.append(('Roof', 'Ice & Water: 2ND ROW at eaves (code) - eave LF doubled', 2, 'ROWS'))
    if meas_rows:
        section_title('Measurements')
        table_header([('Area', 30, 'L'), ('Measurement', 92, 'L'), ('Value', 36, 'R'), ('Unit', 24, 'C')])
        pdf.set_font('Helvetica', '', 8)
        for group, label, v, unit in meas_rows:
            pdf.cell(30, 6, trunc(group, 18), border=1)
            pdf.cell(92, 6, trunc(label, 62), border=1)
            pdf.cell(36, 6, f'{v:g}', border=1, align='R')
            pdf.cell(24, 6, _pdf_safe(unit), border=1, align='C')
            pdf.ln()
        waste = _mnum('waste_pct')
        if waste:
            pdf.set_font('Helvetica', 'I', 8)
            pdf.cell(0, 6, _pdf_safe(f'Roof quantities below already include {waste:g}% waste.'),
                     new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

    if not is_ins:
        # Materials: signed-tier lines with a material cost (or simple-mode lines)
        mat_rows, lab_rows = [], []
        for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
            td = trades.get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
            for it, qty, t in _tier_items(td, trade_mode, _trade_tier(est, tk)):
                name = _with_section(it, it.get('name', ''))
                desc = (t.get('description') or it.get('description') or '').strip()
                unit = it.get('unit', '')
                trade_lbl = labels.get(tk, tk.title())
                if trade_mode == 'simple' or float(t.get('material_unit_cost') or 0) > 0:
                    mat_rows.append((trade_lbl, name, desc, qty, unit))
                if float(t.get('labor_unit_cost') or 0) > 0:
                    lab_rows.append((trade_lbl, name, qty, unit))
        if mat_rows:
            section_title('Materials')
            table_header([('Trade', 26, 'L'), ('Material', 112, 'L'), ('Qty', 20, 'R'), ('Unit', 24, 'C')])
            pdf.set_font('Helvetica', '', 8)
            for trade_lbl, name, desc, qty, unit in mat_rows:
                nm = f'{name} - {desc}' if desc else name
                pdf.cell(26, 6, trunc(trade_lbl, 15), border=1)
                pdf.cell(112, 6, trunc(nm, 74), border=1)
                pdf.cell(20, 6, f'{qty:g}', border=1, align='R')
                pdf.cell(24, 6, trunc(unit, 10), border=1, align='C')
                pdf.ln()
            pdf.ln(4)
        if lab_rows:
            section_title('Labor Summary')
            table_header([('Trade', 26, 'L'), ('Work Item', 112, 'L'), ('Qty', 20, 'R'), ('Unit', 24, 'C')])
            pdf.set_font('Helvetica', '', 8)
            for trade_lbl, name, qty, unit in lab_rows:
                pdf.cell(26, 6, trunc(trade_lbl, 15), border=1)
                pdf.cell(112, 6, trunc(name, 74), border=1)
                pdf.cell(20, 6, f'{qty:g}', border=1, align='R')
                pdf.cell(24, 6, trunc(unit, 10), border=1, align='C')
                pdf.ln()
            pdf.ln(4)

    pdf.set_font('Helvetica', 'I', 7.5)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, _pdf_safe('Generated from the signed contract. Excludes change orders. '
                             'Internal document - no pricing included.'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


def push_contract_to_crm(est_id):
    """Upload the signed contract PDF to Base44 and create a Document record
    tagged 'contract' on the linked CRM job. Runs in a background thread —
    logs everything, never raises."""
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

        # 1) Build the PDF
        pdf_bytes = None
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


def generate_production_packet(est_id):
    """Build the production-packet PDF for a signed estimate, store it as a
    server-generated attachment (replacing any previous packet), and file it
    on the linked CRM job. Returns the attachment dict. Raises on failure —
    callers decide whether that's fatal (endpoint) or logged (pipeline)."""
    est = est_load(est_id)
    if est is None:
        raise ValueError('estimate not found')
    if not est.get('signature'):
        raise ValueError('estimate is not signed')

    pdf_bytes = build_production_packet_pdf(est)
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    fname = f'packet_{uuid.uuid4().hex[:8]}.pdf'
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(pdf_bytes)

    sig  = est.get('signature') or {}
    tier = sig.get('selected_tier') or est.get('selected_tier', 'better')
    label = ('Production Packet' if est.get('estimate_type') == 'insurance'
             else f'Production Packet - {_pick_summary_label(est) or tier.title()}')
    att = {
        'id':               uuid.uuid4().hex[:12],
        'filename':         f'{est_id}/{fname}',
        'label':            label,
        'doc_type':         'work_order',
        'show_in_estimate': False,   # internal — never on the customer page
        'server_generated': True,
        'generated_at':     datetime.utcnow().isoformat() + 'Z',
    }

    # Replace any previous packet (and clean up its file). Only work_order
    # rows — other server-generated docs (signed change orders) stay put.
    def _is_packet(x):
        return x.get('server_generated') and x.get('doc_type') == 'work_order'

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
                              if not _is_packet(x)] + [att]
        return doc

    est = est_update(est_id, _swap_packet) or est

    # File it on the CRM job — best-effort, packet exists locally regardless
    c     = est.get('customer', {})
    cname = (c.get('name') or 'Customer').strip()
    enum  = _est_number(est)
    doc_id, err = _crm_file_document(
        est, pdf_bytes, upload_name=f'Production_Packet_{enum}.pdf',
        hosted_url=f'{_base_url()}/uploads/{est_id}/{fname}',
        doc_name=f'Production Packet - {cname} ({enum})', doc_type='work_order',
        description='Work order + material list generated automatically from the signed contract.')
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


def _post_sign_pipeline(est_id):
    """Post-signature background work, run sequentially in ONE thread so two
    writers never read-modify-write the same estimate concurrently."""
    push_contract_to_crm(est_id)
    try:
        att = generate_production_packet(est_id)
        print(f"[packet] generated {att['filename']} for {est_id}")
    except Exception as exc:
        print(f'[packet] generation failed for {est_id}: {exc}')


@app.route('/api/estimates/<est_id>/production-packet', methods=['POST'])
def regenerate_production_packet(est_id):
    """Manual (re)generate from the Documents tab. Signed estimates only."""
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
    try:
        att = generate_production_packet(est_id)
    except Exception as exc:
        print(f'[packet] manual generation failed for {est_id}: {exc}')
        return jsonify({'error': f'Packet generation failed: {exc}'}), 500
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
      {_cv_sig_form(f'/sign-co/{he(token)}',
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
    <th>Description</th><th class="cvth-c">Qty</th>
    <th class="cvth-c">Unit</th><th class="cvth-r">Total</th></tr></thead>
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
        s = _pdf_safe(s)
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
                html_body, to_addr)


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
        # Signature is saved above — everything below is best-effort. Run the rep
        # notification and the CRM/packet pipeline in background threads so a slow
        # or unreachable SMTP/CRM endpoint can never block (or 500) the customer's
        # signing request. The customer always gets their confirmation instantly.
        # (Contract push + packet generation share ONE thread — both write the
        # estimate doc, and sequencing them avoids a lost-update race.)
        threading.Thread(target=send_signature_notification,
                         args=(est,), daemon=True).start()
        threading.Thread(target=_post_sign_pipeline,
                         args=(est.get('estimate_id'),), daemon=True).start()
        return build_signed_confirmation(est)

    # Already signed — show the confirmation instead of the form
    if est.get('signature'):
        return build_signed_confirmation(est)

    # A logged-in team member opening the link is a preview, not a customer
    # view — don't skew the viewed/not-viewed analytics or email the rep.
    if session.get('user'):
        html = build_customer_view(est, token)
        ribbon = ('<div style="position:fixed;bottom:14px;left:50%;transform:translateX(-50%);'
                  'background:#1e293b;color:#fff;padding:7px 16px;border-radius:999px;'
                  'font:600 12px/1.4 sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.35);'
                  'z-index:9999;white-space:nowrap">&#128065; Team preview &mdash; views aren\'t counted</div>')
        return html.replace('</body>', ribbon + '</body>', 1)

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
            threading.Thread(target=send_view_notification, args=(est,), daemon=True).start()
    except Exception as exc:
        print(f'[view-track] failed: {exc}')

    return build_customer_view(est, token)


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
ROOFING_CATALOG_SEED = [
    {"id": "m_landmark", "name": "CertainTeed Landmark (Architectural Shingle)", "unit": "SQ", "cost": 142, "measure": "squares_waste"},
    {"id": "m_northgate", "name": "CertainTeed Northgate (Impact-Resistant Shingle)", "unit": "SQ", "cost": 175, "measure": "squares_waste"},
    {"id": "m_iko_nordic", "name": "IKO Nordic (Impact-Resistant Shingle)", "unit": "SQ", "cost": 175, "measure": "squares_waste"},
    {"id": "m_edco", "name": "EDCO Steel Shingle", "unit": "SQ", "cost": 300, "measure": "squares_waste"},
    {"id": "m_stone", "name": "Stone-Coated Steel", "unit": "SQ", "cost": 330, "measure": "squares_waste"},
    {"id": "m_standing_seam", "name": "Standing Seam Metal (24ga)", "unit": "SQ", "cost": 400, "measure": "squares_waste"},
    {"id": "m_euroshield", "name": "Euroshield (Rubber)", "unit": "SQ", "cost": 360, "measure": "squares_waste"},
    {"id": "a_underlayment", "name": "Synthetic Underlayment", "unit": "SQ", "cost": 9.1, "measure": "squares_waste"},
    {"id": "a_ice_water", "name": "Ice & Water Shield", "unit": "SQ", "cost": 46.46, "measure": "eave_valley"},
    {"id": "a_drip_edge", "name": "Drip Edge", "unit": "LF", "cost": 0, "measure": "eave_rake"},
    {"id": "a_ridge_cap", "name": "Ridge Cap", "unit": "LF", "cost": 0, "measure": "ridge_hip"},
    {"id": "a_starter", "name": "Starter Strip", "unit": "LF", "cost": 0, "measure": "eave_rake"},
    {"id": "a_pipe_boots", "name": "Pipe Boots", "unit": "EA", "cost": 0, "measure": "pipe_boots"},
    {"id": "a_step_flash", "name": "Step / Wall Flashing", "unit": "LF", "cost": 0, "measure": "step"},
    {"id": "a_skylight", "name": "Skylight Flashing", "unit": "EA", "cost": 0, "measure": "skylights"},
    {"id": "a_decking", "name": "Decking (OSB 7/16\")", "unit": "EA", "cost": 30},
    {"id": "a_ridge_vent", "name": "Ridge Vent", "unit": "LF", "cost": 34, "measure": "ridge_vent_code", "bundle_lf": 4, "bundle_unit": "sticks"},
    {"id": "a_intake_vent", "name": "Intake Vent", "unit": "LF", "cost": 4.5, "measure": "eave"},
    {"id": "a_vent_plug", "name": "Vent Plug", "unit": "EA", "cost": 25, "measure": "turtle_vents"},
    {"id": "l_tearoff", "name": "Tear-Off Labor", "unit": "SQ", "cost": 0, "measure": "squares_waste"},
    {"id": "l_install", "name": "Install Labor", "unit": "SQ", "cost": 0, "measure": "squares_waste"},
    {"id": "x_dumpster", "name": "Dumpster", "unit": "LS", "cost": 0},
    {"id": "x_permit", "name": "Permit", "unit": "LS", "cost": 0},
]
_RS = ["a_underlayment", "a_ice_water", "a_drip_edge", "a_ridge_cap", "a_starter",
       "a_pipe_boots", "a_step_flash", "a_decking", "l_tearoff", "l_install", "x_dumpster", "x_permit"]
# Every bundle carries its OWN customer story — the `description` tagline and the
# `features` bullet list that fill the Good/Better/Best card. Swapping a bundle on
# an estimate replaces both, so the copy can never describe last week's shingle.
_RS_FEATURES = [
    "Complete tear-off of existing roofing down to the deck",
    "Synthetic underlayment over the full roof deck",
    "Ice & water shield at eaves and valleys",
    "New drip edge, starter strip, pipe boots, and flashing",
    "Dumpster, permit, and full magnetic nail sweep",
    "5-year Project One workmanship warranty",
]
ROOFING_BUNDLES_SEED = [
    {"id": "b_landmark", "name": "CertainTeed Landmark", "product_ids": ["m_landmark"] + _RS, "description": "Architectural laminate shingle system — dimensional shadow lines, lifetime limited warranty.",
     "features": ["CertainTeed Landmark architectural laminate shingles", "Lifetime limited manufacturer warranty", "130 mph wind rating", "Dimensional shadow lines for depth and curb appeal"] + _RS_FEATURES},
    {"id": "b_northgate", "name": "CertainTeed Northgate", "product_ids": ["m_northgate"] + _RS, "description": "Class 4 impact-resistant SBS shingle — hail-country durability, may qualify for an insurance discount.",
     "features": ["CertainTeed Northgate SBS-modified impact-resistant shingles", "Class 4 impact rating — the highest hail rating there is", "May qualify for a homeowners insurance premium discount", "Lifetime limited manufacturer warranty", "130 mph wind rating"] + _RS_FEATURES},
    {"id": "b_iko_nordic", "name": "IKO Nordic", "product_ids": ["m_iko_nordic"] + _RS, "description": "Class 4 impact-resistant shingle built for extreme cold and hail.",
     "features": ["IKO Nordic impact-resistant shingles", "Class 4 impact rating — the highest hail rating there is", "Built for extreme cold and freeze-thaw cycles", "May qualify for a homeowners insurance premium discount", "Limited lifetime manufacturer warranty"] + _RS_FEATURES},
    {"id": "b_edco", "name": "EDCO", "product_ids": ["m_edco"] + _RS, "description": "EDCO steel shingles — the look of architectural shingles in Class 4 impact-rated steel.",
     "features": ["EDCO steel shingles — architectural shingle look in real steel", "Class 4 impact rating, will not crack or lose granules to hail", "Limited lifetime warranty with hail damage coverage", "Baked-on finish that will not chip, peel, or fade"] + _RS_FEATURES},
    {"id": "b_stone", "name": "Stone-Coated Steel", "product_ids": ["m_stone"] + _RS, "description": "Stone-coated steel panels — steel strength with a textured shake/shingle look, wind-rated 120+ mph.",
     "features": ["Stone-coated steel panels with a textured shake/shingle profile", "Class 4 impact rating and 120+ mph wind rating", "Steel strength at a fraction of the weight of tile", "50-year limited manufacturer warranty"] + _RS_FEATURES},
    {"id": "b_standing_seam", "name": "Standing Seam", "product_ids": ["m_standing_seam"] + _RS, "description": "24ga standing seam metal with concealed fasteners — the premium 50+ year system.",
     "features": ["24ga standing seam metal panels with concealed fasteners", "No exposed screws to back out or leak over time", "50+ year service life — the last roof this house needs", "Class 4 impact rating and Kynar 500 finish warranty", "Clean modern lines in your choice of color"] + _RS_FEATURES},
    {"id": "b_euroshield", "name": "Euroshield", "product_ids": ["m_euroshield"] + _RS, "description": "Recycled-rubber roofing with the look of slate/shake — Class 4 impact, freeze-thaw resistant.",
     "features": ["Euroshield recycled-rubber roofing in a slate or shake profile", "Class 4 impact rating — rubber absorbs hail instead of cracking", "Engineered for Colorado freeze-thaw cycles", "50-year limited manufacturer warranty", "Made from recycled tires — a genuinely green roof"] + _RS_FEATURES},
]
ROOFING_TIER_DEFAULTS_SEED = {"good": "b_landmark", "better": "b_northgate", "best": "b_standing_seam"}

# Siding catalog: one price per product, accessories named to MATCH the old
# siding template rows so an in-flight estimate's existing items get adopted by
# name instead of duplicated when a rep picks a bundle. Material costs come from
# the old per-tier variant menus; accessory/labor costs are PLACEHOLDERS (0) —
# the manager sets real numbers in Price Book → Siding → Products.
SIDING_CATALOG_SEED = [
    {"id": "s_vinyl_dutch", "name": "Vinyl - Dutch Lap 4\"", "unit": "SQ", "cost": 165, "measure": "siding_sq_waste"},
    {"id": "s_vinyl_clap", "name": "Vinyl - Clapboard 4.5\"", "unit": "SQ", "cost": 170, "measure": "siding_sq_waste"},
    {"id": "s_vinyl_bb", "name": "Vinyl - Board & Batten", "unit": "SQ", "cost": 195, "measure": "siding_sq_waste"},
    {"id": "s_lp_lap", "name": "LP SmartSide - Lap 8\"", "unit": "SQ", "cost": 240, "measure": "siding_sq_waste"},
    {"id": "s_lp_panel", "name": "LP SmartSide - Panel / Board & Batten", "unit": "SQ", "cost": 265, "measure": "siding_sq_waste"},
    {"id": "s_hardie_cedar", "name": "James Hardie - Plank Lap 8.25\" (Cedarmill)", "unit": "SQ", "cost": 320, "measure": "siding_sq_waste"},
    {"id": "s_hardie_smooth", "name": "James Hardie - Plank Lap 7\" (Smooth)", "unit": "SQ", "cost": 315, "measure": "siding_sq_waste"},
    {"id": "s_hardie_shingle", "name": "James Hardie - Shingle / Panel", "unit": "SQ", "cost": 360, "measure": "siding_sq_waste"},
    {"id": "sa_house_wrap", "name": "House Wrap", "unit": "SQ", "cost": 0, "measure": "siding_sq_waste"},
    {"id": "sa_starter", "name": "Starter Strip", "unit": "LF", "cost": 0, "measure": "siding_starter"},
    {"id": "sa_j_channel", "name": "J-Channel", "unit": "LF", "cost": 0, "measure": "j_channel"},
    {"id": "sa_corner_out", "name": "Corner Posts", "unit": "LF", "cost": 0, "measure": "corners_out"},
    {"id": "sa_corner_in", "name": "Inside Corners", "unit": "LF", "cost": 0, "measure": "corners_in"},
    {"id": "sa_trim", "name": "Trim Board", "unit": "LF", "cost": 0},
    {"id": "sa_soffit", "name": "Soffit", "unit": "LF", "cost": 0, "measure": "siding_soffit"},
    {"id": "sa_fascia", "name": "Fascia", "unit": "LF", "cost": 0},
    {"id": "sl_tearoff", "name": "Tear-Off Labor", "unit": "SQ", "cost": 0, "measure": "siding_squares"},
    {"id": "sl_install", "name": "Install Labor", "unit": "SQ", "cost": 0, "measure": "siding_squares"},
    {"id": "sx_dumpster", "name": "Dumpster", "unit": "LS", "cost": 0},
    {"id": "sx_permit", "name": "Permit", "unit": "LS", "cost": 0},
]
_SS = ["sa_house_wrap", "sa_starter", "sa_j_channel", "sa_corner_out", "sa_corner_in",
       "sa_trim", "sa_soffit", "sa_fascia", "sl_tearoff", "sl_install", "sx_dumpster", "sx_permit"]
_SS_FEATURES = [
    "Complete tear-off of existing siding",
    "House wrap weather barrier over the full wall area",
    "New starter strip, J-channel, corner posts, and trim",
    "Soffit and fascia included",
    "Dumpster, permit, and full site cleanup",
    "5-year Project One workmanship warranty",
]
SIDING_BUNDLES_SEED = [
    {"id": "sb_vinyl_dutch", "name": "Vinyl - Dutch Lap", "product_ids": ["s_vinyl_dutch"] + _SS,
     "description": "Insulated-ready vinyl siding in the classic Dutch lap profile - low maintenance, never needs paint, lifetime limited warranty.",
     "features": ["Vinyl siding in the classic 4\" Dutch lap profile", "Never needs paint - wash it once a year and it is done", "Color runs all the way through, so scratches do not show", "Lifetime limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_vinyl_clap", "name": "Vinyl - Clapboard", "product_ids": ["s_vinyl_clap"] + _SS,
     "description": "Traditional clapboard vinyl siding - clean horizontal lines, fade-resistant color all the way through.",
     "features": ["Vinyl siding in a traditional 4.5\" clapboard profile", "Clean horizontal lines that suit almost any home style", "Fade-resistant color all the way through the panel", "Never needs paint", "Lifetime limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_vinyl_bb", "name": "Vinyl - Board & Batten", "product_ids": ["s_vinyl_bb"] + _SS,
     "description": "Vertical board & batten vinyl - modern farmhouse curb appeal with zero-maintenance vinyl durability.",
     "features": ["Vertical board & batten vinyl panels", "Modern farmhouse curb appeal", "Zero-maintenance vinyl durability - never needs paint", "Lifetime limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_lp_lap", "name": "LP SmartSide - Lap", "product_ids": ["s_lp_lap"] + _SS,
     "description": "LP SmartSide engineered wood lap siding - the warmth and texture of real wood, treated to resist rot, hail, and termites. 50-year limited warranty.",
     "features": ["LP SmartSide engineered wood lap siding, 8\" exposure", "The warmth and texture of real wood grain", "SmartGuard treated to resist rot, hail, and termites", "Holds paint far longer than natural wood", "50-year limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_lp_panel", "name": "LP SmartSide - Board & Batten", "product_ids": ["s_lp_panel"] + _SS,
     "description": "LP SmartSide engineered wood panel and batten system - bold vertical lines with impact-resistant engineered wood strength.",
     "features": ["LP SmartSide engineered wood panel and batten system", "Bold vertical lines with real wood texture", "SmartGuard treated to resist rot, hail, and termites", "50-year limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_hardie_cedar", "name": "James Hardie - Cedarmill Lap", "product_ids": ["s_hardie_cedar"] + _SS,
     "description": "James Hardie fiber cement in the Cedarmill woodgrain texture - non-combustible, hail and pest proof, ColorPlus finish backed for 15 years.",
     "features": ["James Hardie fiber cement lap siding, Cedarmill woodgrain texture", "Non-combustible - will not feed a fire", "Hail, pest, and rot proof", "ColorPlus factory finish backed for 15 years", "30-year limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_hardie_smooth", "name": "James Hardie - Smooth Lap", "product_ids": ["s_hardie_smooth"] + _SS,
     "description": "James Hardie fiber cement lap siding with a clean smooth finish - the premium look, engineered for Colorado freeze-thaw and hail.",
     "features": ["James Hardie fiber cement lap siding with a clean smooth finish", "Engineered specifically for Colorado freeze-thaw and hail", "Non-combustible, hail, pest, and rot proof", "ColorPlus factory finish backed for 15 years", "30-year limited manufacturer warranty"] + _SS_FEATURES},
    {"id": "sb_hardie_shingle", "name": "James Hardie - Shingle / Panel", "product_ids": ["s_hardie_shingle"] + _SS,
     "description": "James Hardie shingle and panel siding - shake-style character in fiber cement, ideal for gables and accent walls.",
     "features": ["James Hardie shingle and panel siding", "Shake-style character without the maintenance of real cedar", "Ideal for gables, dormers, and accent walls", "Non-combustible, hail, pest, and rot proof", "30-year limited manufacturer warranty"] + _SS_FEATURES},
]
SIDING_TIER_DEFAULTS_SEED = {"good": "sb_vinyl_dutch", "better": "sb_lp_lap", "best": "sb_hardie_cedar"}

# trade -> (catalog seed, bundle seed, tier-default seed). Mirrored client-side
# by BUNDLE_TRADES in app.js — keep the two lists in sync.
BUNDLE_SEEDS = {
    'roofing': (ROOFING_CATALOG_SEED, ROOFING_BUNDLES_SEED, ROOFING_TIER_DEFAULTS_SEED),
    'siding':  (SIDING_CATALOG_SEED,  SIDING_BUNDLES_SEED,  SIDING_TIER_DEFAULTS_SEED),
}


def _copy_seed_bundle(b):
    """Deep-enough copy so an edited response can never mutate the seed constant."""
    return dict(b, product_ids=list(b['product_ids']), features=list(b.get('features') or []))


# Customer-facing copy the server may fill in on a bundle the manager already owns.
_BUNDLE_COPY_FIELDS = ('description', 'features')


def _ensure_bundle_catalogs(pb):
    """Inject each bundle trade's catalog/bundles/defaults into a price book that
    has none. Non-destructive (mutates the in-memory dict for the response only)."""
    for trade, (catalog, bundles, defaults) in BUNDLE_SEEDS.items():
        if not pb.get(trade + '_catalog'):
            pb[trade + '_catalog'] = [dict(p) for p in catalog]
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
                        live[field] = list(val) if isinstance(val, list) else val
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


# ── Permit jurisdiction & code reference (statewide Colorado) ────────────────
# jurisdictions.json is generated by scripts/build_jurisdictions.py (all 64 CO
# counties + 273 municipalities, parsed from Wikipedia). The Scope-page panel
# matches the customer address against it, then shows the permit office + code
# points (a shared colorado_baseline inherited by every entry, plus per-entry
# overrides). Reference/display only — never feeds pricing. Managers edit it via
# ⚙ Settings; same file+route pattern as permit_defaults.
_JURISDICTIONS_FALLBACK = {'version': 1, 'colorado_baseline': {'code_points': [], 'verify_note': ''},
                           'jurisdictions': []}

def _load_jurisdictions():
    # Prefer the live DATA_DIR copy; fall back to the committed seed beside the
    # app so a fresh checkout still serves the full statewide dataset.
    for path in (JURISDICTIONS_FILE, os.path.join(BASE_DIR, 'jurisdictions.json')):
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
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
        if est.get('status') == 'declined':
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
        for cfg in ('price_book.json', 'tier_defaults.json', 'config.json'):
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
