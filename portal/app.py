"""Portal app — the login page, the launcher, and user administration.

Mounted at '/' by portal.wsgi. Everything under /canvass, /crm, and /estimate
is a different Flask app; this one owns identity and the front door.
"""
import io
import os

from flask import (Flask, jsonify, redirect, request, send_from_directory,
                   session)
from markupsafe import escape

from portal import session as psession
from portal import apibot
from portal import throttle
from portal import users
from portal.mounts import MOUNTS
from portal.nimbus_bp import nimbus_bp

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, 'static')

# Shared enrollment code. Replaces three separate codes (SIGNUP_CODE,
# CANVASSER_SIGNUP_CODE, SALESCRM_SIGNUP_CODE). Blank it once everyone has
# enrolled and only per-user invites will work.
SIGNUP_CODE = os.environ.get('PORTAL_SIGNUP_CODE', '').strip()

MIN_PASSWORD = 8

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
psession.configure(app)

# Nimbus — admin-only AI agent dashboard, mounted at /nimbus/*. The blueprint
# guards its own routes on is_admin(); the before_request auth guard below
# still blocks anonymous requests first.
app.register_blueprint(nimbus_bp)

# The executive team's read-only token exchange. Registered on the portal app
# only; the GET-only allowlist guard that bounds the principal it issues is
# applied to all four apps by psession.configure().
apibot.register(app)


# ── Auth guard ───────────────────────────────────────────────────────────────
# Default-deny, matching the estimator's model rather than the decorator model
# canvasser and salescrm use. The estimator switched to this after a route
# added without the decorator silently served data to anonymous callers
# (estimator/app.py:150-153); no reason to reintroduce that footgun here.

PUBLIC_ENDPOINTS = {
    'login', 'logout', 'health', 'shell_js', 'shell_css', 'static',
    'pwa_manifest', 'service_worker',
    # Customer-facing estimator links that predate the merge. See the compat
    # redirects at the bottom of this file.
    'compat_sign', 'compat_sign_co', 'compat_upload',
    # The executive team's token exchange. "Public" only in the sense that it
    # is reachable without a cookie — it authenticates on X-P1-Token, is
    # throttled per IP, and 404s unless P1_READONLY_TOKEN is set. The principal
    # it hands out is GET-only and allowlisted. See portal/apibot.py.
    'apibot_session',
}


@app.before_request
def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or session.get('username'):
        return
    # Any /api/ path (root portal API or a blueprint's API namespace such as
    # /nimbus/api/*) returns JSON 401 so the fetch client can react instead of
    # following a redirect to the login page's HTML.
    if request.path.startswith('/api/') or '/api/' in request.path:
        return jsonify({'error': 'authentication required'}), 401
    return redirect('/login?next=' + _quote_next(request.full_path or '/'))


def _quote_next(path):
    from urllib.parse import quote
    return quote(path.rstrip('?'), safe='/?=&')


def _safe_next(raw):
    """Only same-origin absolute paths. Blocks //evil.com and \\evil.com."""
    raw = (raw or '').strip()
    if not raw.startswith('/') or raw.startswith('//') or '\\' in raw:
        return '/'
    return raw


def _admin_only():
    if not users.is_admin(session.get('username')):
        return jsonify({'error': 'admin only'}), 403
    return None


# ── Login / logout ───────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    nxt = _safe_next(request.values.get('next'))
    # canvasser's invite links are /?invite=CODE&u=username — keep that shape
    # working now that the portal owns enrollment.
    invite_code = (request.values.get('invite') or '').strip()
    prefill = (request.values.get('u') or '').strip().lower()

    if request.method == 'GET':
        if session.get('username'):
            return redirect(nxt)
        return _login_page(next_url=nxt, invite=invite_code, username=prefill)

    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    code = (request.form.get('signup_code') or invite_code).strip()

    if not username or not password:
        return _login_page(error='Enter your username and password.',
                           next_url=nxt, invite=code, username=username), 400

    # Throttle check comes before the password check, so a locked-out attacker
    # never gets us to spend a hash on them. See portal/throttle.py.
    ip = request.remote_addr or ''
    wait = throttle.retry_after(username, ip)
    if wait:
        return _locked_out(wait, next_url=nxt, username=username)

    existing = users.get(username)

    if existing:
        user = users.verify(username, password)
        if not user:
            return _fail_login(username, ip, 'Wrong username or password.',
                               next_url=nxt)
        throttle.clear(username, ip)
        psession.sign_in(session, user)
        if user['must_change']:
            return redirect('/account/password?next=' + _quote_next(nxt))
        return redirect(nxt)

    # No account yet — enrollment, gated by an invite or the shared code.
    if len(password) < MIN_PASSWORD:
        return _login_page(error=f'Pick a password of at least {MIN_PASSWORD} characters.',
                           next_url=nxt, invite=code, username=username, mode='signup'), 400

    invite = users.find_valid_invite(code) if code else None
    if invite is not None:
        if invite['username'] and invite['username'] != username:
            return _login_page(error=f"That invite is for '{invite['username']}'.",
                               next_url=nxt, invite=code, username=username,
                               mode='signup'), 403
    elif not (SIGNUP_CODE and code == SIGNUP_CODE):
        # A wrong enrollment code counts as a failure too — it is the other
        # guessable secret on this form.
        return _fail_login(username, ip,
                           'That setup code is not valid. Ask Luke for an invite link.',
                           next_url=nxt, mode='signup')

    # First account on a fresh install bootstraps as admin.
    role = 'admin' if users.count() == 0 else 'rep'
    user = users.create(username, password=password, role=role)
    if invite is not None:
        users.consume_invite(invite['code'], username)
    throttle.clear(username, ip)
    psession.sign_in(session, user)
    return redirect(nxt)


def _fail_login(username, ip, error, next_url='/', mode='signin'):
    """Record the failed attempt, then render the error — or the lockout page if
    this attempt was the one that tripped the limit."""
    wait = throttle.record_failure(username, ip)
    if wait:
        return _locked_out(wait, next_url=next_url, username=username)
    status = 403 if mode == 'signup' else 401
    return _login_page(error=error, next_url=next_url, username=username,
                       mode=mode), status


def _locked_out(wait, next_url='/', username=''):
    minutes = max(1, round(wait / 60))
    resp = _login_page(
        error=f'Too many failed attempts. Try again in about {minutes} '
              f'minute{"s" if minutes != 1 else ""}.',
        next_url=next_url, username=username)
    return resp, 429, {'Retry-After': str(wait)}


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    psession.sign_out(session)
    return redirect('/login')


@app.route('/account/password', methods=['GET', 'POST'])
def change_password():
    nxt = _safe_next(request.values.get('next'))
    if request.method == 'GET':
        return _password_page(next_url=nxt)
    current = request.form.get('current') or ''
    new = request.form.get('new') or ''
    if not users.verify(session['username'], current):
        return _password_page(error='Current password is wrong.', next_url=nxt), 401
    if len(new) < MIN_PASSWORD:
        return _password_page(error=f'At least {MIN_PASSWORD} characters, please.',
                              next_url=nxt), 400
    users.set_password(session['username'], new, must_change=False)
    return redirect(nxt)


# ── Identity + roster ────────────────────────────────────────────────────────

@app.route('/api/me')
def me():
    user = users.get(session.get('username'))
    if not user:
        # Row deleted out from under a live cookie — sign them out rather than
        # leaving a session pointing at nobody.
        psession.sign_out(session)
        return jsonify({'authenticated': False}), 401
    # Nimbus is an admin-only tile. Included in `admin_apps` so the launcher
    # can render it in a separate row (or the same grid for admins) without
    # a second round-trip.
    admin_apps = []
    if user['role'] == 'admin':
        admin_apps.append({
            'key': 'nimbus', 'prefix': '/nimbus', 'label': 'Nimbus',
            'icon': '⛈️',
            'blurb': 'AI lead generation + social listening.',
            'accent': '#22d3ee',
        })
    return jsonify({
        'authenticated': True,
        'username': user['username'],
        'full_name': user['full_name'] or users.display_name(user['username']),
        'email': user['email'],
        'role': user['role'],
        'is_admin': user['role'] == 'admin',
        'is_manager': user['role'] in users.ELEVATED,
        'must_change': user['must_change'],
        'apps': [{k: m[k] for k in ('key', 'prefix', 'label', 'icon', 'blurb', 'accent')}
                 for m in MOUNTS],
        'admin_apps': admin_apps,
    })


@app.route('/api/users')
def list_users():
    """Everyone can read the roster; only admins mutate it.

    salescrm deliberately made this login-only (app.py:465-473) because reps
    need names for assignment dropdowns. Keeping that.

    `locked` carries the seconds remaining on a failed-login lockout (0 when
    not locked) so the admin panel can offer the unlock button to the right row.
    """
    locked = throttle.locked_usernames()
    return jsonify([dict({k: u[k] for k in
                          ('username', 'full_name', 'email', 'role', 'is_admin',
                           'created_at')},
                         locked=locked.get(u['username'], 0))
                    for u in users.all_users()])


@app.route('/api/users', methods=['POST'])
def create_user():
    denied = _admin_only()
    if denied:
        return denied
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    if not username or any(c.isspace() for c in username):
        return jsonify({'error': 'Username must have no spaces, e.g. "bryan"'}), 400
    if users.get(username):
        return jsonify({'error': 'Username taken'}), 409
    if len(password) < MIN_PASSWORD:
        return jsonify({'error': f'Password must be at least {MIN_PASSWORD} characters'}), 400
    role = data.get('role') or 'rep'
    if role not in users.ROLES:
        return jsonify({'error': 'Unknown role'}), 400
    user = users.create(username, password=password, role=role,
                        full_name=(data.get('full_name') or '').strip(),
                        must_change=True)
    return jsonify({k: user[k] for k in ('username', 'full_name', 'email', 'role')}), 201


@app.route('/api/users/<username>/password', methods=['POST'])
def admin_set_password(username):
    denied = _admin_only()
    if denied:
        return denied
    data = request.get_json(force=True, silent=True) or {}
    new = data.get('password') or ''
    if len(new) < MIN_PASSWORD:
        return jsonify({'error': f'Password must be at least {MIN_PASSWORD} characters'}), 400
    if not users.get(username):
        return jsonify({'error': 'No such user'}), 404
    users.set_password(username, new, must_change=True)
    return jsonify({'ok': True})


@app.route('/api/users/<username>/role', methods=['POST'])
def admin_set_role(username):
    denied = _admin_only()
    if denied:
        return denied
    if username == session['username']:
        return jsonify({'error': "You can't change your own role"}), 400
    data = request.get_json(force=True, silent=True) or {}
    role = data.get('role')
    if role not in users.ROLES:
        return jsonify({'error': 'Unknown role'}), 400
    if not users.get(username):
        return jsonify({'error': 'No such user'}), 404
    users.set_role(username, role)
    return jsonify({'ok': True})


@app.route('/api/users/<username>/unlock', methods=['POST'])
def admin_unlock_user(username):
    """Clear a failed-login lockout. Admin-only.

    Without this the only cure for a mistyped password is waiting, which is a
    poor answer for a rep on a doorstep. Reported in `locked` on GET /api/users
    so an admin can see who needs it.
    """
    denied = _admin_only()
    if denied:
        return denied
    throttle.unlock_user(username)
    return jsonify({'ok': True})


@app.route('/api/users/<username>', methods=['DELETE'])
def admin_delete_user(username):
    denied = _admin_only()
    if denied:
        return denied
    if username == session['username']:
        return jsonify({'error': "You can't delete yourself"}), 400
    users.delete(username)
    return jsonify({'ok': True})


@app.route('/api/account/password', methods=['POST'])
def self_set_password():
    data = request.get_json(force=True, silent=True) or {}
    if not users.verify(session['username'], data.get('current') or ''):
        return jsonify({'error': 'Current password is wrong'}), 401
    new = data.get('password') or ''
    if len(new) < MIN_PASSWORD:
        return jsonify({'error': f'Password must be at least {MIN_PASSWORD} characters'}), 400
    users.set_password(session['username'], new, must_change=False)
    return jsonify({'ok': True})


# ── Invites ──────────────────────────────────────────────────────────────────

def _invite_link(code, username):
    link = request.host_url.rstrip('/') + '/login?invite=' + code
    if username:
        link += '&u=' + username
    return link


@app.route('/api/invites', methods=['GET', 'POST'])
def invites():
    denied = _admin_only()
    if denied:
        return denied
    if request.method == 'GET':
        rows = users.list_invites()
        for r in rows:
            r['link'] = _invite_link(r['code'], r['username'])
        return jsonify(rows)
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip().lower()
    if any(c.isspace() for c in username):
        return jsonify({'error': 'Use their login username (no spaces), e.g. "bryan" —'
                                 ' or leave blank for an open invite'}), 400
    inv = users.create_invite(session['username'], username,
                              data.get('expires_days') or 7)
    inv['link'] = _invite_link(inv['code'], inv['username'])
    return jsonify(inv), 201


@app.route('/api/invites/<code>', methods=['DELETE'])
def revoke_invite(code):
    denied = _admin_only()
    if denied:
        return denied
    users.revoke_invite(code)
    return jsonify({'ok': True})


# ── Database backup ──────────────────────────────────────────────────────────

@app.route('/api/backup/databases')
def backup_databases():
    """Consistent snapshot of portal.db, salescrm.db and canvasser.db.

    **Admin, not manager.** The note this came from said "manager-only", but
    portal.db holds every password hash in the company, and a manager who can
    download it can take those hashes offline and work on an admin's. That is
    a privilege escalation dressed as a backup. Managers keep the reporting
    exports they already have; whole-database dumps stay with admins.

    Matches the estimator's /api/backup: synchronous, no size cap. Three small
    SQLite files zip to a few MB, and the snapshot never blocks a writer.
    """
    denied = _admin_only()
    if denied:
        return denied
    from flask import send_file

    from portal import backup as pbackup

    data, _manifest = pbackup.build_zip()
    return send_file(io.BytesIO(data), mimetype='application/zip',
                     as_attachment=True, download_name=pbackup.filename())


# ── Launcher + shell assets ──────────────────────────────────────────────────

@app.route('/')
def launcher():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/shell.js')
def shell_js():
    return send_from_directory(STATIC_DIR, 'shell.js', mimetype='text/javascript')


@app.route('/shell.css')
def shell_css():
    return send_from_directory(STATIC_DIR, 'shell.css', mimetype='text/css')


@app.route('/manifest.json')
def pwa_manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json')


@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(STATIC_DIR, 'sw.js', mimetype='text/javascript')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/health')
def health():
    return jsonify({'ok': True})


# ── Compatibility redirects ──────────────────────────────────────────────────
# Signed estimates, change orders, and cover photos are linked from emails and
# texts already in customers' hands, pointing at the estimator's old root
# paths. Those links must keep resolving after the estimator moves to
# /estimate, so forward them rather than 404. Do not remove these.

# POST is here on purpose. The estimator now posts its sign forms to the
# mounted path, but a customer who loaded the page before that change still
# has a form pointing at this root path — GET-only meant their submission
# came back 405 Method Not Allowed and the signature was lost. 307 (not 302)
# is required: it preserves the method AND the body, so the fields survive
# the hop. Do not downgrade these to 302.
def _compat_redirect(target):
    return redirect(target, code=307 if request.method == 'POST' else 302)


@app.route('/sign/<token>', methods=['GET', 'POST'])
def compat_sign(token):
    return _compat_redirect(f'/estimate/sign/{token}')


@app.route('/sign-co/<token>', methods=['GET', 'POST'])
def compat_sign_co(token):
    return _compat_redirect(f'/estimate/sign-co/{token}')


@app.route('/uploads/<path:filename>')
def compat_upload(filename):
    return redirect(f'/estimate/uploads/{filename}', code=302)


# ── Server-rendered pages ────────────────────────────────────────────────────

def _page(title, body):
    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<link rel="icon" href="/static/icon-192.png">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; padding:24px;
         background:#0d1117; color:#e6edf3;
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .card {{ width:100%; max-width:380px; background:#161b22; border:1px solid #2d333b;
           border-radius:14px; padding:28px 26px; }}
  .mark {{ width:52px; height:52px; margin:0 auto 14px; border-radius:14px;
           background:linear-gradient(135deg,#f97316,#00a8b5); color:#fff;
           display:flex; align-items:center; justify-content:center;
           font-weight:800; font-size:20px; letter-spacing:.5px; }}
  h1 {{ margin:0 0 4px; font-size:19px; text-align:center; }}
  .sub {{ margin:0 0 22px; text-align:center; color:#8b949e; font-size:13px; }}
  label {{ display:block; font-size:12px; color:#8b949e; margin:14px 0 5px;
           text-transform:uppercase; letter-spacing:.4px; }}
  input {{ width:100%; padding:11px 12px; border-radius:9px; font-size:16px;
           background:#0d1117; border:1px solid #2d333b; color:#e6edf3; }}
  input:focus {{ outline:none; border-color:#f97316; }}
  button {{ width:100%; margin-top:20px; padding:12px; border:0; border-radius:9px;
            background:#f97316; color:#fff; font-size:15px; font-weight:600;
            cursor:pointer; }}
  button:hover {{ background:#ea6a06; }}
  .err {{ margin-top:16px; padding:10px 12px; border-radius:9px; font-size:13px;
          background:#3d1a1a; border:1px solid #6b2020; color:#ffb4b4; }}
  details {{ margin-top:18px; }}
  summary {{ cursor:pointer; color:#8b949e; font-size:13px; }}
  .hint {{ margin-top:8px; color:#6e7681; font-size:12px; }}
  a {{ color:#8b949e; font-size:13px; }}
</style>
</head><body><div class="card">{body}</div></body></html>'''


def _login_page(error='', next_url='/', invite='', username='', mode='signin'):
    # Everything interpolated below is attacker-controllable (form fields and
    # query params), so it all goes through escape().
    err = f'<div class="err">{escape(error)}</div>' if error else ''
    open_attr = ' open' if (mode == 'signup' or invite) else ''
    sub = ('Set a password to finish setting up your account.'
           if mode == 'signup' else 'One login for Canvass, Pipeline, and Estimate.')
    return _page('Sign in · Project One', f'''
  <div class="mark">P1</div>
  <h1>Project One</h1>
  <p class="sub">{sub}</p>
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{escape(next_url)}">
    <label for="u">Username</label>
    <input id="u" name="username" value="{escape(username)}" autocomplete="username"
           autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
    <label for="p">Password</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    <details{open_attr}>
      <summary>First time here?</summary>
      <label for="c">Setup or invite code</label>
      <input id="c" name="signup_code" value="{escape(invite)}" autocomplete="off">
      <p class="hint">Enter the code with the username and password you want.
         Minimum {MIN_PASSWORD} characters.</p>
    </details>
    <button type="submit">Sign in</button>
  </form>
  {err}''')


def _password_page(error='', next_url='/'):
    err = f'<div class="err">{escape(error)}</div>' if error else ''
    return _page('Change password · Project One', f'''
  <div class="mark">P1</div>
  <h1>Choose a new password</h1>
  <p class="sub">You're signed in as {escape(session.get('username', ''))}.</p>
  <form method="post" action="/account/password">
    <input type="hidden" name="next" value="{escape(next_url)}">
    <label for="c">Current password</label>
    <input id="c" name="current" type="password" autocomplete="current-password" required autofocus>
    <label for="n">New password</label>
    <input id="n" name="new" type="password" autocomplete="new-password" required>
    <p class="hint">At least {MIN_PASSWORD} characters.</p>
    <button type="submit">Save password</button>
  </form>
  {err}
  <p style="margin-top:16px;text-align:center"><a href="/">Skip for now</a></p>''')


if __name__ == '__main__':
    app.run(debug=True, port=5003)
