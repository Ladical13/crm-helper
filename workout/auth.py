"""Login for a one-person app on the open internet.

This app used to borrow the portal's identity. It does not any more: it is its
own site, with its own cookie, and nothing here imports anything outside this
directory. What it needs is the smallest login that is honestly safe to put on
a public URL, and no more:

  - **One password**, `WORKOUT_PASSWORD`. There is no signup, no user table, no
    password reset and no email. A personal training log has exactly one person
    who should see it, so a user table would be four features protecting one
    row.
  - **Fail closed.** With no password set, the app serves nothing at all in
    production (`RAILWAY_ENVIRONMENT` set) — an unset variable must never be
    the difference between a login and an open door. Locally it opens up, so
    `python app.py` works with nothing configured.
  - **Throttled.** A public password box with no rate limit is a password box
    being guessed at right now. Failures are counted per IP in SQLite, not in
    memory, because two gunicorn workers would each keep their own counter and
    hand out double the allowance, and a redeploy would reset it.
  - **Constant-time comparison**, so the failure time does not leak the prefix.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (jsonify, redirect, request, session)

# Who the rows belong to. The schema carries a `user` on everything so the app
# could grow a second lifter without a migration; today it is one name.
OWNER = os.environ.get('WORKOUT_USER', 'me').strip() or 'me'

# Failure budget before a lockout, and how long the lockout lasts. Generous
# enough to survive fat fingers on a phone keyboard, small enough that guessing
# is hopeless.
MAX_FAILS = 8
LOCKOUT_MINUTES = (15, 60)      # escalates on repeat


def password():
    return os.environ.get('WORKOUT_PASSWORD', '')


def in_production():
    """Railway sets this. Used only to decide how strict to be, never to
    decide who someone is."""
    return bool(os.environ.get('RAILWAY_ENVIRONMENT'))


def secure_cookies():
    override = os.environ.get('WORKOUT_COOKIE_SECURE', '').strip().lower()
    if override in ('1', 'true', 'yes'):
        return True
    if override in ('0', 'false', 'no'):
        return False
    # Local dev is plain http://localhost, where a Secure cookie is silently
    # dropped and the login appears to do nothing at all.
    return in_production()


def configure(app):
    """Secret key, cookie policy and security headers. Call once at startup."""
    secret = os.environ.get('WORKOUT_SESSION_SECRET', '')
    if not secret:
        secret = secrets.token_hex(32)
        if in_production():
            # Each worker would generate its own, so a request served by the
            # other one looks signed out. Loud, because the symptom (random
            # logouts) never points at the cause.
            print('[workout] WARNING: WORKOUT_SESSION_SECRET is not set — each '
                  'worker signs cookies differently and nobody stays signed in.')
    app.secret_key = secret
    app.config.update(
        SESSION_COOKIE_NAME='p1lift',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=secure_cookies(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=60),
    )
    app.after_request(_security_headers)
    return app


def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    if secure_cookies():
        # Gated on the same signal as the Secure flag so the two can never
        # disagree: a browser remembers HSTS for a year, and emitting it from
        # localhost would break plain http:// for everything else on the laptop.
        resp.headers.setdefault('Strict-Transport-Security',
                                'max-age=31536000; includeSubDomains')
    return resp


def _now():
    return datetime.now(timezone.utc)


def misconfigured():
    """True when the app must refuse to serve.

    Production with no password would be a public training log; there is no
    sensible default to fall back to, so it serves an error instead of data.
    """
    return in_production() and not password()


def current_user(get_db):
    """The signed-in owner, or None.

    With no password set outside production this returns the owner outright —
    that is what makes `python app.py` work on a laptop with nothing
    configured. `misconfigured()` is what stops that path existing in
    production.
    """
    if session.get('lift_user'):
        return session['lift_user']
    if not password() and not in_production():
        return OWNER
    return None


def sign_in(sess):
    sess.permanent = True
    sess['lift_user'] = OWNER


def check_password(candidate):
    real = password()
    if not real:
        return False
    return hmac.compare_digest(_digest(candidate), _digest(real))


def _digest(value):
    return hashlib.sha256((value or '').encode('utf-8')).digest()


# ── Throttle ────────────────────────────────────────────────────────────────

def init_throttle(db):
    db.execute('''CREATE TABLE IF NOT EXISTS login_fails (
                      ip         TEXT PRIMARY KEY,
                      fails      INTEGER NOT NULL DEFAULT 0,
                      lockouts   INTEGER NOT NULL DEFAULT 0,
                      locked_until TEXT NOT NULL DEFAULT '',
                      updated_at TEXT NOT NULL
                  )''')


def client_ip():
    """The caller's address, trusting one proxy hop.

    Railway terminates TLS at the edge, so remote_addr is the proxy. An
    unreadable address buckets into 'unknown' rather than skipping the check —
    fail closed, or the way around the throttle is to make the header
    unparseable.
    """
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        first = fwd.split(',')[0].strip()
        if first:
            return first
    return request.remote_addr or 'unknown'


def locked_for(db, ip):
    """Seconds remaining on a lockout, 0 when not locked."""
    row = db.execute('SELECT locked_until FROM login_fails WHERE ip=?',
                     (ip,)).fetchone()
    if not row or not row['locked_until']:
        return 0
    try:
        until = datetime.strptime(row['locked_until'],
                                  '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, int((until - _now()).total_seconds()))


def record_failure(db, ip):
    row = db.execute('SELECT * FROM login_fails WHERE ip=?', (ip,)).fetchone()
    fails = (row['fails'] if row else 0) + 1
    lockouts = row['lockouts'] if row else 0
    locked_until = ''
    if fails >= MAX_FAILS:
        minutes = LOCKOUT_MINUTES[min(lockouts, len(LOCKOUT_MINUTES) - 1)]
        locked_until = (_now() + timedelta(minutes=minutes)
                        ).strftime('%Y-%m-%dT%H:%M:%SZ')
        fails, lockouts = 0, lockouts + 1
    db.execute(
        'INSERT INTO login_fails (ip, fails, lockouts, locked_until, updated_at) '
        'VALUES (?,?,?,?,?) ON CONFLICT(ip) DO UPDATE SET fails=excluded.fails, '
        'lockouts=excluded.lockouts, locked_until=excluded.locked_until, '
        'updated_at=excluded.updated_at',
        (ip, fails, lockouts, locked_until, _now().strftime('%Y-%m-%dT%H:%M:%SZ')))


def clear_failures(db, ip):
    db.execute('DELETE FROM login_fails WHERE ip=?', (ip,))


# ── Page ────────────────────────────────────────────────────────────────────

LOGIN_PAGE = '''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>P1 Lift</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; background:#0d1117; color:#e6edf3;
         font:16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  form {{ width:min(340px, 88vw); text-align:center; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:.5px; }}
  h1 span {{ color:#f97316; }}
  p {{ color:#8b949e; font-size:14px; margin:0 0 22px; }}
  input {{ width:100%; min-height:48px; padding:0 14px; margin-bottom:12px;
           background:#1c2230; border:1px solid #262d3a; border-radius:12px;
           color:#e6edf3; font-size:17px; box-sizing:border-box; }}
  input:focus {{ outline:none; border-color:#f97316; }}
  button {{ width:100%; min-height:48px; border:none; border-radius:12px;
            background:#f97316; color:#fff; font-size:16px; font-weight:700; }}
  .err {{ color:#ef4444; font-size:14px; margin-bottom:12px; }}
</style></head>
<body>
  <form method="post" action="{action}">
    <h1>P1 <span>Lift</span></h1>
    <p>{subtitle}</p>
    {error}
    <input type="password" name="password" placeholder="Password" autofocus
           autocomplete="current-password" autocapitalize="none">
    <button type="submit">Sign in</button>
  </form>
</body></html>'''


def login_page(action='/login', error='', subtitle='Log the set. Beat last week.'):
    return LOGIN_PAGE.format(
        action=action, subtitle=subtitle,
        error=f'<div class="err">{error}</div>' if error else '')


# Signing out has to clear the offline cache as well as the cookie. The service
# worker keeps API responses so history is readable in a basement with no
# signal, and those responses would otherwise outlive the session — on a shared
# or borrowed phone, "sign out" has to mean the log is gone from the device,
# not just that the next request is refused.
LOGOUT_PAGE = '''<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signing out…</title></head>
<body style="background:#0d1117;color:#8b949e;font-family:-apple-system,sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<p>Signing out…</p>
<script>
  // Belt and braces: clear the caches, drop the worker, then leave. Each step
  // is allowed to fail — an old browser that cannot do one of them must still
  // end up on the login page.
  (function () {
    function go() { location.replace('__LOGIN_URL__'); }
    var jobs = [];
    try {
      if (window.caches && caches.keys) {
        jobs.push(caches.keys().then(function (keys) {
          return Promise.all(keys.map(function (k) { return caches.delete(k); }));
        }));
      }
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        jobs.push(navigator.serviceWorker.getRegistrations().then(function (rs) {
          return Promise.all(rs.map(function (r) { return r.unregister(); }));
        }));
      }
    } catch (e) { /* fall through to the redirect */ }
    Promise.all(jobs).catch(function () {}).then(go);
    setTimeout(go, 2500);        // never leave somebody staring at this page
  })();
</script>
</body></html>'''


def logout_page(login_url):
    # A token swap rather than str.format: the page is mostly JavaScript, and
    # every brace in it would have to be doubled.
    return LOGOUT_PAGE.replace('__LOGIN_URL__', login_url)


CONFIG_PAGE = '''<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P1 Lift — not configured</title></head>
<body style="background:#0d1117;color:#e6edf3;font-family:-apple-system,sans-serif;
             padding:40px;line-height:1.5">
<h1 style="color:#f97316">Not configured</h1>
<p><code>WORKOUT_PASSWORD</code> is not set, so this app is refusing to serve
rather than publishing a training log to the whole internet.</p>
<p>Set it in the service's environment variables and redeploy.</p>
</body></html>'''


def login_required(get_db):
    """Decorator factory: every API route needs a signed-in owner."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if misconfigured():
                return jsonify({'error': 'WORKOUT_PASSWORD is not set'}), 503
            if not current_user(get_db):
                return jsonify({'error': 'authentication required'}), 401
            return f(*args, **kwargs)
        return wrapper
    return decorator
