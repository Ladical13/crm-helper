import io
import os
import re
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
)

# Shared setup code for first-time password enrollment. Set SIGNUP_CODE in the
# environment; blank it out once everyone has enrolled to disable new sign-ups.
SIGNUP_CODE = os.environ.get('SIGNUP_CODE', '').strip()

TEAM_MEMBERS = [
    'avery', 'bryan', 'derik', 'luke', 'phil',
]

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

# ── Default-deny auth guard ─────────────────────────────────────────────────
# Every request requires a logged-in session EXCEPT the explicit allowlist below.
# This is the opposite of decorating each protected route (which is what let the
# data APIs leak — a route added without the decorator was silently public).
PUBLIC_ENDPOINTS = {
    'login',          # the login page / form
    'logout',         # clears the session
    'customer_sign',  # /sign/<token> — public, protected by the 192-bit token
    'serve_upload',   # /uploads/<file> — cover photos shown on the customer view
    'static',         # JS/CSS for the login + app shell (non-sensitive client code)
    'pwa_manifest',   # /manifest.json — needed for PWA install before login
    'service_worker', # /sw.js — service worker scope must be public
}

@app.before_request
def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or session.get('user'):
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
CUSTOMER_NOTES_FILE  = os.path.join(DATA_DIR, 'customer_notes.json')
TEAM_CONFIG_FILE     = os.path.join(DATA_DIR, 'team.json')

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

os.makedirs(ESTIMATES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def _seed_data_dir():
    """On first run with a new DATA_DIR (e.g. Railway persistent volume),
    copy seed files from the app directory so defaults are available."""
    if DATA_DIR == BASE_DIR:
        return  # local dev — nothing to seed
    for fname in ('price_book.json', 'tier_defaults.json'):
        src = os.path.join(BASE_DIR, fname)
        dst = os.path.join(DATA_DIR, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

_seed_data_dir()

_contact_cache = None


def crm_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def fetch_all_contacts():
    global _contact_cache
    if _contact_cache is not None:
        return _contact_cache
    if http is None:
        _contact_cache = []
        return _contact_cache
    try:
        r = http.get(f"{BASE_URL}/entities/Contact", headers=crm_headers(), timeout=15)
        r.raise_for_status()
        all_contacts = r.json()
        _contact_cache = [c for c in all_contacts if c.get('location_id') == CO_LOCATION_ID]
    except Exception as e:
        print(f"[CRM] fetch failed: {e}")
        _contact_cache = []
    return _contact_cache


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
  font-size:14px;background:#fff;margin-bottom:14px;
  appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%236b7280' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center}}
select:focus{{outline:none;border-color:#1a3a5c;box-shadow:0 0 0 3px rgba(26,58,92,.12)}}
input{{width:100%;padding:11px 14px;border:1px solid #d1d5db;border-radius:6px;
  font-size:14px;background:#fff;margin-bottom:14px}}
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
    role         = data.get('role', 'rep')
    if not username:
        return jsonify({'error': 'username required'}), 400
    if role not in ('admin', 'manager', 'rep'):
        role = 'rep'
    team = load_team()
    if any(m['username'] == username for m in team):
        return jsonify({'error': 'user already exists'}), 409
    team.append({'username': username, 'display_name': display_name or _display_name(username)})
    save_team(team)
    users = load_users()
    rec = users.get(username) or {}
    rec['role']     = role
    rec['is_admin'] = (role == 'admin')
    users[username] = rec
    save_users(users)
    return jsonify({'ok': True, 'username': username}), 201


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
    return calc_tier_total(est, est.get('selected_tier', 'better'))


@app.route('/api/estimates', methods=['GET'])
def list_estimates():
    result = []
    try:
        files = sorted(os.listdir(ESTIMATES_DIR), reverse=True)
    except OSError:
        return jsonify([])
    for fname in files:
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ESTIMATES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
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
            })
        except Exception:
            pass
    return jsonify(result)


@app.route('/api/estimates', methods=['POST'])
def create_estimate():
    data = request.get_json(force=True)
    est_id = data.get('estimate_id') or str(uuid.uuid4())
    data['estimate_id'] = est_id
    now = datetime.utcnow().isoformat() + 'Z'
    data.setdefault('created_at', now)
    data['updated_at'] = now
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'estimate_id': est_id}), 201


@app.route('/api/estimates/<est_id>', methods=['GET'])
def get_estimate(est_id):
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    user = session.get('user', '')
    if _get_role(user) == 'rep' and d.get('salesperson') != user:
        return jsonify({'error': 'access denied'}), 403
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
    data = request.get_json(force=True)
    data['estimate_id'] = est_id
    data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            for field in SERVER_MANAGED_FIELDS:
                if not data.get(field) and existing.get(field):
                    data[field] = existing[field]
            # A signed estimate stays accepted even if a stale tab says draft
            if existing.get('signature') and data.get('status') in (None, 'draft', 'sent'):
                data['status'] = existing.get('status', 'accepted')
        except Exception:
            pass
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'estimate_id': est_id})


@app.route('/api/estimates/<est_id>/duplicate', methods=['POST'])
def duplicate_estimate(est_id):
    src = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if not os.path.exists(src):
        return jsonify({'error': 'Not found'}), 404
    with open(src, 'r', encoding='utf-8') as f:
        est = json.load(f)
    new_id = str(uuid.uuid4())
    est['estimate_id'] = new_id
    est['status'] = 'draft'
    est['share_token'] = None
    est['signature'] = None
    est['sent_at'] = None
    est['first_viewed_at'] = None
    est['last_viewed_at'] = None
    est['view_count'] = 0
    est['created_at'] = datetime.utcnow().isoformat() + 'Z'
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    c = est.get('customer', {})
    if c.get('name') and not c['name'].startswith('Copy of '):
        c['name'] = 'Copy of ' + c['name']
    dest = os.path.join(ESTIMATES_DIR, f"{new_id}.json")
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(est, f, indent=2)
    src_dir = os.path.join(UPLOADS_DIR, est_id)
    if os.path.exists(src_dir):
        shutil.copytree(src_dir, os.path.join(UPLOADS_DIR, new_id))
    return jsonify({'estimate_id': new_id})


@app.route('/api/estimates/<est_id>', methods=['DELETE'])
def delete_estimate(est_id):
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if os.path.exists(path):
        os.remove(path)
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
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        est = json.load(f)
    est['estimate_label'] = label
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(est, f, indent=2)
    return jsonify({'ok': True, 'label': label})


@app.route('/api/estimates/<est_id>/status', methods=['PATCH'])
def update_estimate_status(est_id):
    VALID = {'draft', 'sent', 'accepted', 'declined'}
    status = (request.json or {}).get('status')
    if status not in VALID:
        return jsonify({'error': 'Invalid status'}), 400
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        est = json.load(f)
    est['status'] = status
    est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(est, f, indent=2)
    return jsonify({'ok': True, 'status': status})


# ── Photo uploads ──────────────────────────────────────────────────────────

@app.route('/api/uploads/<est_id>', methods=['POST'])
def upload_photo(est_id):
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
    return jsonify({'filename': f"{est_id}/{safe_name}", 'url': f"/uploads/{est_id}/{safe_name}"}), 201


@app.route('/api/uploads/<est_id>/<filename>', methods=['DELETE'])
def delete_photo(est_id, filename):
    path = os.path.join(UPLOADS_DIR, est_id, filename)
    if os.path.exists(path):
        os.remove(path)
    return jsonify({'ok': True})


# ── RoofR PDF import ───────────────────────────────────────────────────────

def _parse_roofr_lf(s):
    """Convert RoofR linear-foot string ('358ft 4in') to decimal feet."""
    m = re.match(r'(\d+)ft\s+(\d+)in', s.strip())
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 12, 2)
    m = re.match(r'(\d[\d,]*)ft', s.replace(',', ''))
    return float(m.group(1)) if m else 0.0

def _parse_roofr_pdf(file_bytes):
    if _pypdf is None:
        raise RuntimeError('pypdf not installed')
    reader = _pypdf.PdfReader(io.BytesIO(file_bytes))
    full_text = '\n'.join(p.extract_text() or '' for p in reader.pages)

    def find_lf(label):
        # [\s:]+ handles both "Label 358ft 4in" and "Label: 358ft 4in" formats
        m = re.search(rf'{re.escape(label)}[\s:]+(\d+ft\s+\d+in)', full_text)
        return _parse_roofr_lf(m.group(1)) if m else None

    sq = re.search(r'Squares[\s:]+([\d.]+)', full_text)
    squares = float(sq.group(1)) if sq else None

    # "Hips + ridges" is the precomputed combined value in the Report Summary
    ridge_hip_m = re.search(r'Hips\s*\+\s*ridges[\s:]+(\d+ft\s+\d+in)', full_text)
    ridge_hip = _parse_roofr_lf(ridge_hip_m.group(1)) if ridge_hip_m else None
    if ridge_hip is None:
        h = find_lf('Total hips') or 0
        r = find_lf('Total ridges') or 0
        ridge_hip = round(h + r, 2) or None

    eave   = find_lf('Total eaves')
    valley = find_lf('Total valleys')
    rake   = find_lf('Total rakes')
    step   = find_lf('Total step flashing')

    addr_m = re.search(
        r'(\d+\s+[^\n,]+),\s+([A-Za-z][A-Za-z\s]+),\s+([A-Z]{2})\s+(\d{5})',
        full_text)

    meas = {k: v for k, v in {
        'roof_squares':  squares,
        'waste_pct':     10,
        'ridge_hip_lf':  ridge_hip,
        'eave_lf':       eave,
        'valley_lf':     valley,
        'rake_lf':       rake,
        'step_flash_lf': step,
        'gutter_lf':     eave,
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

def find_by_token(token):
    """Return (est_dict, filepath) for the estimate matching share_token, or None."""
    if not token:
        return None
    for fname in os.listdir(ESTIMATES_DIR):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ESTIMATES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                est = json.load(f)
            if est.get('share_token') == token:
                return est, path
        except Exception:
            pass
    return None

def calc_tier_total(est, tier):
    """Compute grand sell total for a given tier (excludes insurance)."""
    pricing = est.get('pricing', {})
    mode    = pricing.get('mode', 'margin')
    grate   = float(pricing.get('global_rate') or 35)
    ovr     = pricing.get('per_trade_overrides', {})
    trades  = est.get('trades', {})
    total   = 0.0

    def rate(tk):
        v = ovr.get(tk)
        return float(v) if v is not None else grate

    def sell(cost, r):
        return cost / (1 - r / 100) if mode == 'margin' and r < 100 else cost * (1 + r / 100)

    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if not td.get('enabled'):
            continue
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
        r = rate(tk)
        for item in td.get('line_items', []):
            qty = float(item.get('quantity') or 0)
            if trade_mode == 'simple':
                sp = float(item.get('unit_price') or 0)
            else:
                t    = (item.get('tiers') or {}).get(tier, {})
                if t.get('included') is False:
                    continue  # item excluded from this package tier
                cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
                sp   = sell(cost, r)
            total += sp * qty
    return total


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

    def _rate(pricing, tk, tier):
        ovr = pricing.get('per_trade_overrides', {})
        v   = ovr.get(tk)
        if v is not None:
            return float(v)
        tr = pricing.get('tier_rates', {})
        tv = tr.get(tier) if tr else None
        if tv is not None:
            return float(tv)
        return float(pricing.get('global_rate') or 35)

    def _sell(cost, r, mode):
        if mode == 'margin':
            return cost / (1 - r / 100) if r < 100 else 0
        return cost * (1 + r / 100)

    try:
        fnames = sorted(os.listdir(ESTIMATES_DIR))
    except OSError:
        return jsonify({'by_trade': {}, 'by_rep': {}})

    for fname in fnames:
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(ESTIMATES_DIR, fname), 'r', encoding='utf-8') as f:
                est = json.load(f)
        except Exception:
            continue

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

        tier       = est.get('selected_tier', 'better')
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
            r     = _rate(pricing, tk, tier)
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
                    tsell += _sell(cost, r, mode) * qty
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


def render_line_items(est, tier=None):
    """Build trade line-item tables for customer view. Returns (html, grand_total)."""
    if tier is None:
        tier = est.get('selected_tier', 'better')
    pricing  = est.get('pricing', {})
    mode     = pricing.get('mode', 'margin')
    grate    = float(pricing.get('global_rate') or 35)
    ovr      = pricing.get('per_trade_overrides', {})

    def rate(trade):
        v = ovr.get(trade)
        return float(v) if v is not None else grate

    def sell(cost, r):
        return cost / (1 - r / 100) if mode == 'margin' and r < 100 else cost * (1 + r / 100)

    labels  = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters', other='Other / Misc')
    trades  = est.get('trades', {})
    parts   = []
    gtotal  = 0.0

    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if not td.get('enabled') or not td.get('line_items'):
            continue
        # Determine trade mode: gutters always simple; others default gbb
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
        r    = rate(tk)
        rows = []
        sub  = 0.0
        hidden_count = 0
        for item in td['line_items']:
            qty  = float(item.get('quantity') or 0)
            if qty <= 0:
                continue  # zero-quantity items are hidden from the customer
            if trade_mode == 'simple':
                sp   = float(item.get('unit_price') or 0)
                desc = (item.get('description') or '').strip()
            else:
                t    = (item.get('tiers') or {}).get(tier, {})
                if t.get('included') is False:
                    continue  # item excluded from this package tier
                cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
                sp   = sell(cost, r)
                desc = t.get('description', '')
            line  = sp * qty
            sub  += line
            if not item.get('customer_visible', True):
                hidden_count += 1
                continue
            rows.append(f'''<tr>
              <td class="cvn">{he(item.get("name",""))}
                {'<div class="cvd">'+he(desc)+'</div>' if desc else ''}</td>
              <td class="cvc">{qty:g}</td>
              <td class="cvc">{he(item.get("unit",""))}</td></tr>''')
        if hidden_count:
            rows.append(f'<tr><td colspan="3" class="cvhidden-note">Additional materials &amp; supplies included in total</td></tr>')
        if not rows:
            continue  # nothing priced to show the customer for this trade
        gtotal += sub
        lbl = labels.get(tk, tk.title())
        parts.append(f'''<div class="cvtrade">
          <div class="cvtrade-hd">{lbl}</div>
          <table class="cvt"><thead><tr>
            <th>Description</th><th class="cvth-c">Qty</th>
            <th class="cvth-c">Unit</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
          <tfoot><tr><td colspan="2" class="cvsub-l">{lbl} Subtotal</td>
            <td class="cvr cvsub">{fc(sub)}</td></tr></tfoot>
          </table></div>''')

    return '\n'.join(parts), gtotal


_CV_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:14px;color:#1f2937;background:#f3f4f6}
.cvhdr{background:#fff;padding:16px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px;box-shadow:0 2px 10px rgba(0,0,0,.08)}
.cvhdr-logo-wrap{display:inline-flex;align-items:center}
.cvhdr img{height:56px;width:auto;display:block}
.cvhdr-contact{text-align:right;line-height:1.45}
.cvhdr-contact a{color:#1a3a5c;font-weight:800;font-size:16px;text-decoration:none;display:block}
.cvhdr-contact span{color:#6b7280;font-size:11px}
.cvbrand-stripe{height:6px;background:linear-gradient(90deg,#22c7da 0 33.3%,#ffd400 33.3% 66.6%,#ee3d42 66.6% 100%)}
.cvhero{background:linear-gradient(135deg,#1a3a5c,#0e2440);color:#fff;padding:30px 20px;text-align:center;position:relative}
.cvhero-brand{font-size:12px;font-weight:800;letter-spacing:2.5px;text-transform:uppercase;color:#22c7da;margin-bottom:9px}
.cvhero h1{font-size:22px;font-weight:800;margin-bottom:5px}
.cvhero p{font-size:13px;opacity:.85}
.cvhero.ok{background:linear-gradient(135deg,#16a34a,#14532d)}
.cvcover{position:relative;overflow:hidden;background:#0e2440}
.cvcover img{width:100%;height:min(500px,62vh);object-fit:contain;display:block}
.cvcover-shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,25,41,.08) 0%,rgba(10,25,41,.12) 45%,rgba(10,25,41,.82) 100%)}
.cvcover-text{position:absolute;left:0;right:0;bottom:0;padding:30px 20px 34px;text-align:center;color:#fff}
.cvcover-text h1{font-size:27px;font-weight:800;margin-bottom:7px;text-shadow:0 2px 10px rgba(0,0,0,.5)}
.cvcover-text p{font-size:14px;opacity:.95;text-shadow:0 1px 5px rgba(0,0,0,.55)}
@media(max-width:520px){.cvcover img{height:320px}.cvcover-text h1{font-size:20px}}
.cv-check{font-size:52px;line-height:1;margin-bottom:8px}
.cv-print-btn{margin-top:14px;background:rgba(255,255,255,.2);border:2px solid rgba(255,255,255,.5);
  color:#fff;padding:10px 22px;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}
.cv-print-btn:hover{background:rgba(255,255,255,.3)}
.cvc-card{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin:14px 14px 0;padding:16px}
.cvgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.cvgi label{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;font-weight:700;display:block;margin-bottom:2px}
.cvgi strong{font-size:13px;color:#111}
.cvpkg{text-align:center;padding:18px;border-radius:8px}
.cvpkg-lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}
.cvpkg-total{font-size:34px;font-weight:900;margin-bottom:5px}
.cvpkg-desc{font-size:12px;color:#555;font-style:italic}
.cvtrade{margin:14px 14px 0}
.cvtrade-hd{background:#1a3a5c;color:#fff;padding:8px 14px;font-size:12px;font-weight:700;border-radius:6px 6px 0 0}
.cvt{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.1);border-radius:0 0 6px 6px;overflow:hidden}
.cvt th{padding:7px 10px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.4px;background:#f9fafb;border-bottom:1px solid #e5e7eb;color:#6b7280}
.cvth-c{width:48px;text-align:center !important}
.cvth-r{width:82px;text-align:right !important}
.cvt td{padding:7px 10px;border-bottom:1px solid #f3f4f6;font-size:12px}
.cvt tr:last-child td{border-bottom:none}
.cvn{font-weight:500}
.cvd{font-size:10px;color:#6b7280;font-style:italic;margin-top:2px}
.cvc{text-align:center;color:#6b7280}
.cvc-desc{font-weight:400;color:#6b7280;font-style:italic}
.cvr{text-align:right;font-weight:600}
.cvt tfoot td{background:#f9fafb;font-weight:700;padding:8px 10px;border-top:2px solid #e5e7eb;font-size:12px}
.cvsub-l{text-align:right;color:#6b7280;font-size:11px;padding-right:12px}
.cvsub{color:#1a3a5c}
.cvgrand{margin:14px 14px 0;background:#1a3a5c;color:#fff;padding:13px 16px;border-radius:6px;
  display:flex;justify-content:space-between;align-items:center}
.cvgrand-lbl{font-size:12px;font-weight:600;opacity:.8}
.cvgrand-amt{font-size:22px;font-weight:800}
.cvnotes{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin:14px 14px 0;padding:14px}
.cvnotes h3{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;margin-bottom:6px}
.cvnotes p{font-size:13px;line-height:1.6;color:#374151;white-space:pre-wrap}
.cvcontract{margin:14px;background:#fff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden}
.cvcontract summary{padding:13px 16px;cursor:pointer;font-weight:600;font-size:13px;color:#1a3a5c;list-style:none}
.cvcontract summary::-webkit-details-marker{display:none}
.cvcontract[open] summary{border-bottom:1px solid #e5e7eb}
.cvcontract-body{padding:14px 16px;font-size:11px;line-height:1.7;color:#4b5563;white-space:pre-wrap;
  max-height:280px;overflow-y:auto;background:#f9fafb}
.cvsig{margin:14px;padding:20px;background:#fff;border-radius:8px;border:2px solid #1a3a5c;
  box-shadow:0 4px 14px rgba(0,0,0,.1)}
.cvsig h2{font-size:17px;font-weight:800;color:#1a3a5c;margin-bottom:4px}
.cvsig .sub{font-size:12px;color:#6b7280;margin-bottom:16px}
.cvinput{width:100%;border:1px solid #d1d5db;border-radius:6px;padding:11px 13px;font-size:14px;
  margin-bottom:10px;font-family:inherit;outline:none;color:#111}
.cvinput:focus{border-color:#1a3a5c;box-shadow:0 0 0 3px rgba(26,58,92,.12)}
.cvagree{display:flex;align-items:flex-start;gap:10px;font-size:12px;color:#374151;
  margin-bottom:16px;line-height:1.5;cursor:pointer}
.cvagree input{margin-top:2px;flex-shrink:0;width:16px;height:16px;cursor:pointer}
.cvbtn{width:100%;padding:15px;background:#1a3a5c;color:#fff;border:none;border-radius:8px;
  font-size:16px;font-weight:700;cursor:pointer;margin-bottom:10px;transition:background .15s}
.cvbtn:hover{background:#0e2440}
.cvlegal{font-size:10px;color:#9ca3af;text-align:center;line-height:1.5}
.cv-shingle{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:13px 14px;margin-bottom:14px}
.cv-shingle-label{font-size:12px;font-weight:800;color:#0c4a6e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.cv-shingle-locked{font-size:17px;font-weight:700;color:#1a3a5c}
.cv-shingle-select{margin-bottom:0;background:#fff}
.cv-initials{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:13px 14px;margin-bottom:14px}
.cv-initials-title{font-size:12px;font-weight:800;color:#92400e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}
.cv-initial-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid #fef3c7}
.cv-initial-row:first-of-type{border-top:none}
.cv-initial-text{flex:1;font-size:13px;color:#374151;line-height:1.45}
.cv-initial-box{width:78px;flex-shrink:0;border:2px solid #1a3a5c;border-radius:6px;padding:10px 8px;
  font-size:15px;font-weight:700;text-align:center;text-transform:uppercase;outline:none;color:#1a3a5c;background:#fff}
.cv-initial-box:focus{box-shadow:0 0 0 3px rgba(26,58,92,.15)}
.cv-att-list{display:flex;flex-direction:column;gap:8px}
.cv-att{display:inline-flex;align-items:center;gap:8px;background:#f8fafc;border:1px solid #e2e8f0;
  border-radius:6px;padding:11px 14px;font-size:14px;font-weight:600;color:#1a3a5c;text-decoration:none}
.cv-att:hover{background:#eef2f7;border-color:#94a3b8}
.cvinit-tbl td:first-child{width:auto;text-transform:none;letter-spacing:0;font-size:12px;color:#374151;font-weight:500}
.cvinit-val{font-weight:800!important;color:#1a3a5c!important;text-transform:uppercase;width:70px!important;text-align:right}
.cert{margin:14px;background:#fff;border:2px solid #1a3a5c;border-radius:8px;padding:18px}
.cert-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1.5px;color:#1a3a5c;
  margin-bottom:13px;padding-bottom:10px;border-bottom:2px solid #1a3a5c}
.cert-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px}
.cert-tbl td{padding:5px 0;vertical-align:top}
.cert-tbl td:first-child{color:#6b7280;font-weight:600;width:110px;font-size:10px;text-transform:uppercase;letter-spacing:.4px}
.cert-tbl td:last-child{font-weight:600;color:#111;word-break:break-all}
.cert-legal{font-size:10px;color:#9ca3af;line-height:1.5;border-top:1px solid #f3f4f6;padding-top:10px}
.mono{font-family:monospace;font-size:10px}
.cvftr{text-align:center;padding:28px 16px 36px;background:#1a3a5c;color:rgba(255,255,255,.6);line-height:1.6;margin-top:20px}
.cvftr-logo{height:42px;width:auto;background:#fff;padding:7px 15px;border-radius:8px;margin-bottom:13px}
.cvftr strong{color:#fff;font-size:15px;display:block;margin-bottom:4px;letter-spacing:.3px}
.cvftr-c{font-size:12px}
.cvftr-sub{font-size:10px;margin-top:8px;opacity:.7}
.cvhidden-note{font-size:10px;color:#9ca3af;font-style:italic;padding:5px 10px;text-align:left}
.cv-tier-section{margin:0 14px}
.cv-tier-heading{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:#1a3a5c;padding:16px 0 10px}
.cv-tier-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:4px}
.cv-tier-card{border:2.5px solid #d1d5db;border-radius:10px;padding:14px 10px;text-align:center;
  cursor:pointer;transition:all .18s;background:#fff;position:relative;-webkit-user-select:none;user-select:none}
.cv-tier-card:hover{transform:translateY(-2px);box-shadow:0 4px 14px rgba(0,0,0,.12)}
.cv-tier-card.cv-tier-selected{box-shadow:0 4px 16px rgba(0,0,0,.15);transform:translateY(-2px)}
.cv-tier-popular{position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#16a34a;
  color:#fff;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;
  padding:2px 8px;border-radius:20px;white-space:nowrap}
.cv-tier-name{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;margin-bottom:3px}
.cv-tier-price{font-size:22px;font-weight:900;margin-bottom:5px}
.cv-tier-desc{font-size:10px;color:#6b7280;font-style:italic;margin-bottom:6px;line-height:1.4}
.cv-tier-check{font-size:11px;font-weight:700;color:#6b7280;border:1px solid #d1d5db;border-radius:20px;
  padding:3px 9px;display:inline-block;margin-top:2px;transition:all .15s}
.cv-tier-selected .cv-tier-check{background:#6b7280;color:#fff}
@media(max-width:600px){.cvgrid{grid-template-columns:1fr}.cvpkg-total{font-size:26px}.cv-tier-cards{grid-template-columns:1fr}.cvinput{font-size:16px}.cvinput:focus{font-size:16px}}
@media print{.cv-print-btn{display:none}body{background:#fff}.cert{border-width:1.5pt;page-break-inside:avoid}}
"""


def _cover_photo_url(est):
    """Public URL of the estimate's assigned cover photo, or ''."""
    pid = est.get('cover_photo_id')
    if not pid:
        return ''
    for p in est.get('photos', []):
        if p.get('id') == pid and p.get('filename'):
            return f"/uploads/{p['filename']}"
    return ''


def _cv_hero(est, title, subtitle):
    """Hero block for customer views — full-bleed cover photo when assigned,
    plain branded banner otherwise."""
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
  </div>
</div>'''
    return f'''<div class="cvhero">
  <div class="cvhero-brand">Project One Roofing</div>
  <h1>{he(title)}</h1>
  <p>{he(subtitle)}</p>
</div>'''


def _visible_initials(est):
    """Initial statements with non-empty text, in order."""
    return [i for i in (est.get('contract_initials') or []) if (i.get('text') or '').strip()]


def _cv_shingle_block(est):
    """Shingle-color step for the sign form. Locked display if the rep already
    chose a color; otherwise a required dropdown for the customer."""
    ss = est.get('shingle_selection') or {}
    if not ss.get('enabled', False):
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
    """Customer-visible PDF documents as view links."""
    atts = [a for a in (est.get('attachments') or [])
            if a.get('show_in_estimate', True) and a.get('filename')]
    if not atts:
        return ''
    links = ''
    for a in atts:
        label = (a.get('label') or a.get('original_name') or 'Document').strip()
        links += (f'<a class="cv-att" href="/uploads/{he(a["filename"])}" '
                  f'target="_blank" rel="noopener">&#128196; {he(label)}</a>')
    return f'<div class="cvnotes"><h3>Documents &amp; Reports</h3><div class="cv-att-list">{links}</div></div>'


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
    sections    = ins_td.get('sections', [])
    # Migrate old flat line_items format
    if not sections and ins_td.get('line_items'):
        sections = [{'id': '_legacy', 'name': '', 'items': ins_td.get('line_items', [])}]
    carrier     = (ins_td.get('carrier') or '').strip()
    claim_num   = (ins_td.get('claim_number') or '').strip()
    scope_notes = (ins_td.get('scope_notes') or '').strip()

    ins_total = sum(
        float(i.get('acv') or 0) + float(i.get('depreciation') or 0)
        for sec in sections for i in sec.get('items', [])
    )

    notes_html  = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html  = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html     = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''
    carrier_row = f'<div class="cvgi"><label>Insurance Carrier</label><strong>{he(carrier)}</strong></div>' if carrier else ''
    claim_row   = f'<div class="cvgi"><label>Claim #</label><strong>{he(claim_num)}</strong></div>' if claim_num else ''
    scope_html  = f'<div class="cvnotes"><h3>Scope of Work</h3><p>{he(scope_notes)}</p></div>' if scope_notes else ''

    # Build per-section tables
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
              <td class="cvr">{fc(acv)}</td>
              <td class="cvr">{fc(dep)}</td>
              <td class="cvr">{fc(acv+dep)}</td></tr>'''
        hd = he(sec_name) if sec_name else 'Insurance Estimate Items'
        sections_html += f'''<div class="cvtrade">
          <div class="cvtrade-hd">{hd}</div>
          <table class="cvt"><thead><tr>
            <th>Item Name</th><th>Description</th>
            <th class="cvth-r">ACV</th><th class="cvth-r">Depreciation</th>
            <th class="cvth-r">RCV</th></tr></thead>
          <tbody>{rows}</tbody>
          <tfoot><tr><td colspan="4" class="cvsub-l">{(he(sec_name)+' Subtotal') if sec_name else 'Subtotal'}</td>
            <td class="cvr cvsub">{fc(sec_total)}</td></tr></tfoot>
          </table></div>'''

    if active_sections:
        ins_table = sections_html + f'''<div class="cvgrand">
          <span class="cvgrand-lbl">Insurance Claim Total</span>
          <span class="cvgrand-amt">{fc(ins_total)}</span>
        </div>'''
    else:
        ins_table = '<div class="cvnotes" style="text-align:center;color:#9ca3af">No insurance line items entered yet.</div>'

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Your Insurance Estimate &mdash; Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707760945">970-776-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

{_cv_hero(est, 'Your Insurance Estimate is Ready', 'Review your scope below, then sign at the bottom to accept')}

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

{ins_table}
{scope_html}
{notes_html}
{_cv_attachments_block(est)}
{ctext_html}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the insurance estimate above and all terms &amp; conditions.</p>
  <form method="POST" action="/sign/{he(token)}">
    <input type="hidden" name="selected_tier" value="insurance">
    {_cv_shingle_block(est)}
    {_cv_initials_block(est)}
    <input class="cvinput" name="sig_name" placeholder="Your full legal name *" required autocomplete="name">
    <input class="cvinput" name="sig_email" placeholder="Email address (optional)" type="email" autocomplete="email">
    <label class="cvagree">
      <input type="checkbox" name="agree" required>
      I have read this insurance estimate and I agree to all terms &amp; conditions.
    </label>
    <button type="submit" class="cvbtn">&#10003; Accept &mdash; Sign Electronically</button>
    <p class="cvlegal">By clicking Accept, you are electronically signing this contract. This signature is legally
    binding under the federal E-SIGN Act (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>
</div>

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-776-0945 &middot; projectoneroofingcolorado.com</div>
</div>
</body></html>'''


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

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Your Estimate — Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707760945">970-776-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

{_cv_hero(est, 'Your Estimate is Ready to Review', 'Review your estimate below, then sign at the bottom to accept')}

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

{li_html}

<div class="cvgrand" style="margin-top:14px">
  <span class="cvgrand-lbl">Total</span>
  <span class="cvgrand-amt">{fc(grand_total)}</span>
</div>

{notes_html}
{_cv_attachments_block(est)}
{ctext_html}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the estimate above and all terms &amp; conditions.</p>
  <form method="POST" action="/sign/{he(token)}">
    <input type="hidden" name="selected_tier" value="{he(tier)}">
    {_cv_shingle_block(est)}
    {_cv_initials_block(est)}
    <input class="cvinput" name="sig_name" placeholder="Your full legal name *" required autocomplete="name">
    <input class="cvinput" name="sig_email" placeholder="Email address (optional)" type="email" autocomplete="email">
    <label class="cvagree">
      <input type="checkbox" name="agree" required>
      I have read this estimate and I agree to all terms &amp; conditions.
    </label>
    <button type="submit" class="cvbtn">&#10003; Accept &mdash; Sign Electronically</button>
    <p class="cvlegal">By clicking Accept, you are electronically signing this contract. This signature is legally
    binding under the federal E-SIGN Act (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>
</div>

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-776-0945 &middot; projectoneroofingcolorado.com</div>
</div>
</body></html>'''


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
    default_tier = est.get('selected_tier', 'better')
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    sp     = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()
    tdesc  = est.get('tier_descriptions') or {}

    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    # Pre-render line items for all 3 tiers
    tier_data = {}
    for t in ['good', 'better', 'best']:
        li_html, total = render_line_items(est, tier=t)
        tier_data[t] = {'html': li_html, 'total': total}

    tier_clrs = dict(good='#2563eb', better='#16a34a', best='#b45309')
    tier_bgs  = dict(good='#dbeafe', better='#dcfce7', best='#fef3c7')
    tier_lbls = dict(good='Good',    better='Better',  best='Best')

    # Build the 3 package selection cards
    cards_html = ''
    for t in ['good', 'better', 'best']:
        total  = tier_data[t]['total']
        desc   = (tdesc.get(t) or '').strip()
        clr    = tier_clrs[t]
        bg     = tier_bgs[t]
        lbl    = tier_lbls[t]
        is_sel = t == default_tier
        popular_badge = '<div class="cv-tier-popular">Most Popular</div>' if t == 'better' else ''
        desc_el = f'<div class="cv-tier-desc">{he(desc)}</div>' if desc else ''
        cards_html += f'''<div class="cv-tier-card {'cv-tier-selected' if is_sel else ''}"
          data-tier="{t}" data-total="{total:.2f}"
          style="border-color:{clr};{'background:'+bg if is_sel else ''}"
          onclick="selectCvTier('{t}')">
          {popular_badge}
          <div class="cv-tier-name" style="color:{clr}">{lbl}</div>
          <div class="cv-tier-price" style="color:{clr}">{fc(total)}</div>
          {desc_el}
          <div class="cv-tier-check" id="cv-check-{t}">{'&#10003; Selected' if is_sel else 'Select'}</div>
        </div>'''

    # Build hidden/visible line item blocks for each tier
    tier_blocks_html = ''
    for t in ['good', 'better', 'best']:
        vis = '' if t == default_tier else 'display:none'
        tier_blocks_html += f'<div id="tier-items-{t}" style="{vis}">{tier_data[t]["html"]}</div>\n'

    default_total = tier_data[default_tier]['total']
    default_lbl   = tier_lbls[default_tier]

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Your Estimate — Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707760945">970-776-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

{_cv_hero(est, 'Your Estimate is Ready to Review', 'Choose your package below, then sign at the bottom to accept')}

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

<div class="cv-tier-section">
  <div class="cv-tier-heading">Step 1 &mdash; Choose Your Package</div>
  <div class="cv-tier-cards" id="tier-cards">
    {cards_html}
  </div>
</div>

{tier_blocks_html}

<div class="cvgrand" style="margin-top:14px" id="cv-grand-bar">
  <span class="cvgrand-lbl" id="cv-grand-lbl">Total &mdash; {he(default_lbl)} Package</span>
  <span class="cvgrand-amt" id="cv-grand-amt">{fc(default_total)}</span>
</div>

{notes_html}
{_cv_attachments_block(est)}
{ctext_html}

<div class="cvsig">
  <h2>Step 2 &mdash; Sign to Accept</h2>
  <p class="sub" id="cv-sig-sub">Your electronic signature confirms you have reviewed and agreed to the
    <strong id="cv-sig-tier">{he(default_lbl)}</strong> Package and all terms above.</p>
  <form method="POST" action="/sign/{he(token)}">
    <input type="hidden" name="selected_tier" id="cv-tier-input" value="{he(default_tier)}">
    {_cv_shingle_block(est)}
    {_cv_initials_block(est)}
    <input class="cvinput" name="sig_name" placeholder="Your full legal name *" required autocomplete="name">
    <input class="cvinput" name="sig_email" placeholder="Email address (optional)" type="email" autocomplete="email">
    <label class="cvagree">
      <input type="checkbox" name="agree" required>
      I have read this estimate, selected my package, and I agree to all terms &amp; conditions.
    </label>
    <button type="submit" class="cvbtn" id="cv-sign-btn">&#10003; Accept &mdash; Sign Electronically</button>
    <p class="cvlegal">By clicking Accept, you are electronically signing this contract. This signature is legally
    binding under the federal E-SIGN Act (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>
</div>

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-776-0945 &middot; projectoneroofingcolorado.com</div>
</div>

<script>
var _tier_totals = {{"good":{tier_data['good']['total']:.2f},"better":{tier_data['better']['total']:.2f},"best":{tier_data['best']['total']:.2f}}};
var _tier_lbls   = {{good:'Good',better:'Better',best:'Best'}};
var _tier_clrs   = {{good:'#2563eb',better:'#16a34a',best:'#b45309'}};
var _tier_bgs    = {{good:'#dbeafe',better:'#dcfce7',best:'#fef3c7'}};
var _cur_tier    = '{he(default_tier)}';
function _fmt(n){{return'$'+Math.abs(n).toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g,',');}}
function selectCvTier(tier){{
  _cur_tier=tier;
  ['good','better','best'].forEach(function(t){{
    var card=document.querySelector('[data-tier="'+t+'"]');
    var chk=document.getElementById('cv-check-'+t);
    if(t===tier){{
      card.classList.add('cv-tier-selected');
      card.style.background=_tier_bgs[t];
      chk.innerHTML='&#10003; Selected';
    }}else{{
      card.classList.remove('cv-tier-selected');
      card.style.background='';
      chk.innerHTML='Select';
    }}
    document.getElementById('tier-items-'+t).style.display=(t===tier?'':'none');
  }});
  document.getElementById('cv-grand-lbl').textContent='Total — '+_tier_lbls[tier]+' Package';
  document.getElementById('cv-grand-amt').textContent=_fmt(_tier_totals[tier]);
  document.getElementById('cv-grand-bar').style.borderLeftColor=_tier_clrs[tier];
  document.getElementById('cv-tier-input').value=tier;
  document.getElementById('cv-sig-tier').textContent=_tier_lbls[tier];
  document.getElementById('cv-sign-btn').textContent='✓ Accept — '+_tier_lbls[tier]+' Package';
}}
</script>
</body></html>'''


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
    tlbl = dict(good='Good', better='Better', best='Best').get(tier, tier.title())
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    li_html, gtotal = render_line_items(est)

    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract" open><summary>&#128203; Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    email_row  = f'<tr><td>Email</td><td>{he(semail)}</td></tr>' if semail else ''
    hash_disp  = (dhash[:32] + '&hellip;') if len(dhash) > 32 else he(dhash)

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estimate Accepted &mdash; Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707760945">970-776-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

<div class="cvhero ok">
  <div class="cvhero-brand" style="color:#86efac">Project One Roofing</div>
  <div class="cv-check">&#10003;</div>
  <h1>Estimate Accepted!</h1>
  <p>Thank you, {he(sname)}. Project One Roofing will be in touch soon to schedule your project.</p>
  <button class="cv-print-btn" onclick="window.print()">&#128424; Save / Print Signed Copy</button>
</div>

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
    <div class="cvgi"><label>Package</label><strong>{he(tlbl)}</strong></div>
  </div>
</div>

{_signed_extras_html(est)}

{li_html}

<div class="cvgrand" style="margin-top:14px">
  <span class="cvgrand-lbl">Total &mdash; {he(tlbl)} Package</span>
  <span class="cvgrand-amt">{fc(gtotal)}</span>
</div>

{notes_html}
{ctext_html}

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-776-0945 &middot; projectoneroofingcolorado.com</div>
  <div class="cvftr-sub">Signed: {he(stime_fmt)} &middot; IP: {he(ip)}</div>
</div>
</body></html>'''


# ── E-Signature routes ──────────────────────────────────────────────────────

@app.route('/api/server-info', methods=['GET'])
def server_info():
    """Return network info so the frontend can build share URLs correctly."""
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'base_url': base, 'lan_ip': LAN_IP, 'public_url': PUBLIC_URL})


@app.route('/api/server-info', methods=['PUT'])
def save_server_info():
    """Persist a custom public_url to config.json."""
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
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'ok': True, 'base_url': base})


@app.route('/api/estimates/<est_id>/share', methods=['POST'])
def create_share_link(est_id):
    path = os.path.join(ESTIMATES_DIR, f'{est_id}.json')
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        est = json.load(f)
    token = est.get('share_token') or secrets.token_urlsafe(24)
    est['share_token'] = token
    if not est.get('sent_at'):
        est['sent_at'] = datetime.utcnow().isoformat() + 'Z'
    if est.get('status') in (None, '', 'draft'):
        est['status'] = 'sent'
    est['updated_at']  = datetime.utcnow().isoformat() + 'Z'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(est, f, indent=2)
    # Use PUBLIC_URL if set, otherwise fall back to the LAN IP so customers can reach the link
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'token': token, 'url': f'/sign/{token}', 'full_url': f'{base}/sign/{token}'})


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
    base     = PUBLIC_URL or 'http://localhost:5000'
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
    tlbl     = dict(good='Good', better='Better', best='Best').get(tier, tier.title())

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

    base     = PUBLIC_URL or 'http://localhost:5000'
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
        info_rows.append(('Package', dict(good='Good', better='Better',
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
        grate   = float(pricing.get('global_rate') or 35)
        ovr     = pricing.get('per_trade_overrides', {})
        labels  = dict(roofing='Roofing', siding='Siding', windows='Windows',
                       gutters='Gutters', other='Other / Misc')
        cols = [('Description', 92, 'L'), ('Qty', 16, 'R'), ('Unit', 16, 'C'),
                ('Unit Price', 29, 'R'), ('Total', 29, 'R')]
        for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
            td = est.get('trades', {}).get(tk, {})
            if not td.get('enabled') or not td.get('line_items'):
                continue
            trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
            ov = ovr.get(tk)
            r  = float(ov) if ov is not None else grate
            # Skip the whole trade if nothing will print (all zero-qty / excluded)
            if not any(
                    float(it.get('quantity') or 0) > 0 and
                    (trade_mode == 'simple'
                     or (it.get('tiers') or {}).get(tier, {}).get('included') is not False)
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
                    desc = (it.get('description') or '').strip()
                else:
                    t    = (it.get('tiers') or {}).get(tier, {})
                    if t.get('included') is False:
                        continue  # item excluded from this package tier
                    cost = (float(t.get('material_unit_cost') or 0)
                            + float(t.get('labor_unit_cost') or 0))
                    sp_  = (cost / (1 - r / 100) if mode == 'margin' and r < 100
                            else cost * (1 + r / 100))
                    desc = t.get('description', '')
                line = sp_ * qty
                sub += line
                if not it.get('customer_visible', True):
                    hidden += 1
                    continue
                name = it.get('name', '')
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
        total_label = f'TOTAL - {tier.upper()} PACKAGE'

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


def push_contract_to_crm(est_id):
    """Upload the signed contract PDF to Base44 and create a Document record
    tagged 'contract' on the linked CRM job. Runs in a background thread —
    logs everything, never raises."""
    try:
        path = os.path.join(ESTIMATES_DIR, f'{est_id}.json')
        if not os.path.exists(path):
            print(f'[crm-push] estimate {est_id} not found')
            return
        with open(path, 'r', encoding='utf-8') as f:
            est = json.load(f)

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
            base = PUBLIC_URL or f'http://{LAN_IP}:5000'
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
            with open(path, 'r', encoding='utf-8') as f:
                est = json.load(f)
            est['crm_document_id'] = doc_id
            est['crm_pushed_at']   = datetime.utcnow().isoformat() + 'Z'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(est, f, indent=2)
        except Exception:
            pass
        print(f'[crm-push] SUCCESS — contract for {cname} pushed to CRM job {proj_id} (doc {doc_id})')
    except Exception as exc:
        print(f'[crm-push] unexpected failure for {est_id}: {exc}')


@app.route('/api/estimates/<est_id>/signed', methods=['GET'])
def view_signed_estimate(est_id):
    """Return the signed confirmation page (printable HTML for PDF download)."""
    path = os.path.join(ESTIMATES_DIR, f'{est_id}.json')
    if not os.path.exists(path):
        return '<h2 style="font-family:sans-serif;padding:40px">Estimate not found.</h2>', 404
    with open(path, 'r', encoding='utf-8') as f:
        est = json.load(f)
    if not est.get('signature'):
        return ('<h2 style="font-family:sans-serif;padding:40px">'
                'This estimate has not been signed yet.</h2>'), 404
    html = build_signed_confirmation(est)
    return Response(html, mimetype='text/html')


@app.route('/sign/<token>', methods=['GET', 'POST'])
def customer_sign(token):
    result = find_by_token(token)
    if not result:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404
    est, path = result

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
        ss = est.get('shingle_selection') or {}
        if ss.get('enabled') and not (ss.get('chosen') or '').strip() and not shingle_color:
            return 'Please choose a shingle color before signing.', 400

        # Save the customer-chosen tier back to the estimate
        if selected_tier in ('good', 'better', 'best'):
            est['selected_tier'] = selected_tier

        # Persist the chosen shingle color into the document (part of what is hashed)
        if shingle_color:
            est.setdefault('shingle_selection', {})['chosen'] = shingle_color
            roof = est.get('trades', {}).get('roofing')
            if isinstance(roof, dict):
                roof.setdefault('colors', {})['shingle_color'] = shingle_color

        # Hash the document BEFORE adding signature so hash represents what was signed
        content   = json.dumps(est, sort_keys=True, separators=(',', ':')).encode('utf-8')
        doc_hash  = hashlib.sha256(content).hexdigest()
        signed_at = datetime.utcnow()
        est['signature'] = {
            'name':          sig_name,
            'email':         sig_email,
            'signed_at':     signed_at.isoformat() + 'Z',
            'ip_address':    request.remote_addr,
            'user_agent':    request.headers.get('User-Agent', ''),
            'document_hash': doc_hash,
            'token':         token,
            'selected_tier': est.get('selected_tier', 'better'),
            'shingle_color': shingle_color or (ss.get('chosen') or '').strip(),
            'initials':      initials_captured,
        }
        est['status']     = 'accepted'
        est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(est, f, indent=2)
        # Signature is saved above — everything below is best-effort. Run the rep
        # notification AND the CRM push in background threads so a slow or
        # unreachable SMTP/CRM endpoint can never block (or 500) the customer's
        # signing request. The customer always gets their confirmation instantly.
        threading.Thread(target=send_signature_notification,
                         args=(est,), daemon=True).start()
        threading.Thread(target=push_contract_to_crm,
                         args=(est.get('estimate_id'),), daemon=True).start()
        return build_signed_confirmation(est)

    # Already signed — show the confirmation instead of the form
    if est.get('signature'):
        return build_signed_confirmation(est)

    # Record the customer view; notify the rep on the first one
    try:
        now_iso    = datetime.utcnow().isoformat() + 'Z'
        first_view = not est.get('first_viewed_at')
        if first_view:
            est['first_viewed_at'] = now_iso
        est['last_viewed_at'] = now_iso
        est['view_count']     = int(est.get('view_count') or 0) + 1
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(est, f, indent=2)
        if first_view:
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
    ],
    "siding": [
        {"name": "Vinyl Siding", "unit": "SQ", "measure": "siding_squares_waste",
         "desc_good": "Economy Vinyl", "desc_better": "Premium Vinyl", "desc_best": "Engineered Wood / Fiber Cement",
         "notes_good": "Economy-grade vinyl siding provides durable, low-maintenance protection at an accessible price point.",
         "notes_better": "Premium vinyl siding with thicker wall construction, deeper shadow lines, and a wider color palette. Resists fading and impact for decades with zero maintenance.",
         "notes_best": "Engineered wood or fiber cement siding offers the natural look of real wood with dramatically superior durability and fire resistance. The premium choice for lasting curb appeal."},
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
            'notes_good', 'notes_better', 'notes_best', 'measure')

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
    return jsonify(pb)


@app.route('/api/pricebook', methods=['PUT'])
def put_pricebook():
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


# ── Tier defaults (global G/B/B package bullet points) ─────────────────────

def _load_tier_defaults():
    if os.path.exists(TIER_DEFAULTS_FILE):
        try:
            with open(TIER_DEFAULTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {'good': [], 'better': [], 'best': []}

@app.route('/api/tier-defaults', methods=['GET'])
def get_tier_defaults():
    return jsonify(_load_tier_defaults())

@app.route('/api/tier-defaults', methods=['PUT'])
def put_tier_defaults():
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
    data = request.get_json(force=True)
    with open(APP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


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
    base  = PUBLIC_URL or 'http://localhost:5000'
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


def _check_reminders():
    if not os.environ.get('SMTP_HOST', '').strip():
        return
    now = datetime.utcnow()
    try:
        files = os.listdir(ESTIMATES_DIR)
    except OSError:
        return
    for fname in files:
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ESTIMATES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                est = json.load(f)
        except Exception:
            continue
        if est.get('signature') or not est.get('share_token'):
            continue
        if est.get('status') == 'declined':
            continue
        sent_at = est.get('sent_at')
        if not sent_at:
            # Shared before this feature existed — start its clock now, no email
            est['sent_at'] = now.isoformat() + 'Z'
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(est, f, indent=2)
            except Exception:
                pass
            continue
        try:
            sent_dt = datetime.fromisoformat(sent_at.replace('Z', ''))
        except Exception:
            continue
        days = (now - sent_dt).days
        for d in REMINDER_DAYS:
            if days < d:
                continue
            lock = os.path.join(REMINDER_LOCKS_DIR, f"{est.get('estimate_id','x')}_{d}.lock")
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                continue
            except OSError:
                continue
            try:
                send_followup_reminder(est, days)
            except Exception as exc:
                print(f'[reminders] send failed for {fname}: {exc}')


# ── Backups ─────────────────────────────────────────────────────────────────
# Two layers: a nightly email with all estimate JSONs (the irreplaceable data),
# and an on-demand full-archive download (everything incl. photos).

BACKUP_EMAIL = os.environ.get('BACKUP_EMAIL', 'luke@projectoneroofing.com').strip()


def _build_backup_zip(include_uploads=True):
    """Zip the data directory into memory. Returns bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root_name, root_dir in [('estimates', ESTIMATES_DIR),
                                    ('uploads', UPLOADS_DIR)]:
            if root_name == 'uploads' and not include_uploads:
                continue
            for dirpath, _dirs, files in os.walk(root_dir):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    rel  = os.path.join(root_name, os.path.relpath(full, root_dir))
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
    near = float(request.args.get('total', 688))
    tolerance = float(request.args.get('tol', 50))
    result = []
    try:
        for fname in sorted(os.listdir(ESTIMATES_DIR)):
            if not fname.endswith('.json'): continue
            try:
                with open(os.path.join(ESTIMATES_DIR, fname), 'r', encoding='utf-8') as f:
                    d = json.load(f)
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
    result = []
    try:
        for fname in sorted(os.listdir(ESTIMATES_DIR)):
            if not fname.endswith('.json'):
                continue
            try:
                with open(os.path.join(ESTIMATES_DIR, fname), 'r', encoding='utf-8') as f:
                    d = json.load(f)
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
    """Full on-demand backup: estimates + photos + config."""
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
    n_est = len([f for f in os.listdir(ESTIMATES_DIR) if f.endswith('.json')])
    size_mb = len(data) / 1048576

    base = PUBLIC_URL or 'http://localhost:5000'
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
    if not os.environ.get('SMTP_HOST', '').strip():
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
