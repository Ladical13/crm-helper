"""The single user store — one SQLite table the four apps all read.

Replaces three parallel stores that had drifted apart:

    estimator   DATA_DIR/users.json   dict keyed by username, 3-tier role
    canvasser   canvasser.db users    boolean is_admin only
    salescrm    salescrm.db users     3-tier role + full_name

The join key was already consistent across all three — the lowercase username,
which is also what `estimates.salesperson`, `pins.rep`, `leads.rep`, and
`rep_locations.username` reference, and what becomes the Base44 `assigned_to`
value as `<username>@projectoneroofing.com`. So the merge needs no ID
remapping; it only needs one schema that is the union of the three.

Schema is salescrm's (the richest) plus `email` and `must_change`. All three
old stores hashed with werkzeug, so the hashes migrate verbatim.
"""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

EMAIL_DOMAIN = 'projectoneroofing.com'
ROLES = ('rep', 'manager', 'admin')

# Roles that see everyone's data rather than only their own. salescrm's
# is_manager() and the estimator's _is_manager_up() both mean this.
ELEVATED = ('admin', 'manager')

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

_initialized = set()


def db_path():
    """Resolved lazily, not frozen at import.

    The three apps freeze their DATA_DIR into module constants at import time,
    which forces their test suites to set env vars *before* importing app.py.
    Resolving per call avoids inheriting that trap here.

    PORTAL_DATA_DIR is deliberately NOT allowed to fall back to DATA_DIR:
    DATA_DIR is the estimator's volume, and canvasser/salescrm already fall
    back to it, which is how all three databases end up in one directory by
    accident.
    """
    data_dir = os.environ.get('PORTAL_DATA_DIR') or _REPO_ROOT
    return os.path.join(data_dir, 'portal.db')


def get_db():
    path = db_path()
    if path not in _initialized:
        _init(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                pw_hash     TEXT NOT NULL,
                is_admin    INTEGER DEFAULT 0,
                role        TEXT DEFAULT 'rep',
                full_name   TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                must_change INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
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


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d['is_admin'] = bool(d['is_admin'])
    d['must_change'] = bool(d['must_change'])
    return d


# ── Users ────────────────────────────────────────────────────────────────────

def get(username):
    username = (username or '').strip().lower()
    if not username:
        return None
    with get_db() as db:
        return _row(db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone())


def all_users():
    with get_db() as db:
        rows = db.execute('SELECT * FROM users ORDER BY username').fetchall()
    return [_row(r) for r in rows]


def count():
    with get_db() as db:
        return db.execute('SELECT COUNT(*) c FROM users').fetchone()['c']


def verify(username, password):
    """Return the user dict when the password checks out, else None."""
    user = get(username)
    if not user or not password:
        return None
    if not check_password_hash(user['pw_hash'], password):
        return None
    return user


def create(username, password=None, role='rep', full_name='', email='',
           pw_hash=None, must_change=False):
    """Insert a user. Pass `pw_hash` to migrate an existing hash verbatim."""
    username = (username or '').strip().lower()
    if not username:
        raise ValueError('username required')
    if role not in ROLES:
        raise ValueError(f'unknown role {role!r}')
    if pw_hash is None:
        if not password:
            raise ValueError('password or pw_hash required')
        pw_hash = generate_password_hash(password)
    with get_db() as db:
        db.execute(
            'INSERT INTO users (username, pw_hash, is_admin, role, full_name, email,'
            ' must_change, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (username, pw_hash, 1 if role in ELEVATED else 0, role, full_name,
             email or f'{username}@{EMAIL_DOMAIN}', 1 if must_change else 0, _now()))
    return get(username)


def set_password(username, password, must_change=False):
    with get_db() as db:
        db.execute('UPDATE users SET pw_hash=?, must_change=? WHERE username=?',
                   (generate_password_hash(password), 1 if must_change else 0,
                    (username or '').strip().lower()))


def set_role(username, role):
    if role not in ROLES:
        raise ValueError(f'unknown role {role!r}')
    with get_db() as db:
        db.execute('UPDATE users SET role=?, is_admin=? WHERE username=?',
                   (role, 1 if role in ELEVATED else 0, (username or '').strip().lower()))


def update_profile(username, full_name=None, email=None):
    sets, params = [], []
    if full_name is not None:
        sets.append('full_name=?'); params.append(full_name)
    if email is not None:
        sets.append('email=?'); params.append(email)
    if not sets:
        return
    params.append((username or '').strip().lower())
    with get_db() as db:
        db.execute(f'UPDATE users SET {", ".join(sets)} WHERE username=?', params)


def delete(username):
    with get_db() as db:
        db.execute('DELETE FROM users WHERE username=?', ((username or '').strip().lower(),))


# ── Roles ────────────────────────────────────────────────────────────────────

def role_of(username):
    """'admin' | 'manager' | 'rep'. Unknown users are reps, never elevated."""
    user = get(username)
    if not user:
        return 'rep'
    return user['role'] if user['role'] in ROLES else 'rep'


def is_admin(username):
    """Strict admin — the estimator's _is_admin() semantics."""
    return role_of(username) == 'admin'


def is_manager_up(username):
    """Manager or admin — sees every rep's data."""
    return role_of(username) in ELEVATED


def display_name(username):
    user = get(username)
    if user and user['full_name']:
        return user['full_name']
    username = (username or '').strip().lower()
    return ' '.join(p.capitalize() for p in username.replace('.', ' ').split())


def email_of(username):
    user = get(username)
    if user and user['email']:
        return user['email']
    return f'{(username or "").strip().lower()}@{EMAIL_DOMAIN}'


# ── Invites ──────────────────────────────────────────────────────────────────
# Lifted from canvasser/app.py:219-254, which salescrm/app.py:426-461 had
# already copied byte-for-byte. One copy now.

def create_invite(created_by, username='', expires_days=7):
    username = (username or '').strip().lower()
    code = secrets.token_urlsafe(8)
    expires = (datetime.utcnow() + timedelta(days=min(int(expires_days or 7), 30))
               ).strftime('%Y-%m-%dT%H:%M:%SZ')
    with get_db() as db:
        db.execute('INSERT INTO invites (code, username, created_by, created_at, expires_at)'
                   ' VALUES (?,?,?,?,?)', (code, username, created_by, _now(), expires))
    return {'code': code, 'username': username, 'expires_at': expires}


def list_invites(limit=50):
    with get_db() as db:
        rows = db.execute('SELECT * FROM invites ORDER BY created_at DESC LIMIT ?',
                          (limit,)).fetchall()
    now = _now()
    out = []
    for r in rows:
        d = dict(r)
        d['status'] = ('used' if d['used_by'] else
                       'expired' if d['expires_at'] <= now else 'active')
        out.append(d)
    return out


def find_valid_invite(code):
    with get_db() as db:
        return db.execute("SELECT * FROM invites WHERE code=? AND used_by='' AND expires_at > ?",
                          (code, _now())).fetchone()


def consume_invite(code, username):
    with get_db() as db:
        db.execute('UPDATE invites SET used_by=?, used_at=? WHERE code=?',
                   (username, _now(), code))


def revoke_invite(code):
    with get_db() as db:
        db.execute('DELETE FROM invites WHERE code=?', (code,))
