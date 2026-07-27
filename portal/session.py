"""One session cookie, shared by all four apps.

Every app mounted in the portal MUST call `configure(app)` instead of setting
its own secret key and cookie config. The reason is subtle and bites hard: the
apps share one cookie, but *each* app re-saves that cookie whenever it touches
`session`. If the estimator saves it with SESSION_COOKIE_SECURE=False while the
portal saves it with True, they clobber each other's cookie on alternating
requests and the rep gets logged out at random. Identical config in all four
processes is the only way that works.

The three apps previously derived the Secure flag from two different env vars
(estimator from DATA_DIR, the other two from RAILWAY_ENVIRONMENT). That is
consolidated here.
"""
import os
import secrets
from datetime import timedelta

from werkzeug.middleware.proxy_fix import ProxyFix

# Generated once per process and shared by every app that imports this module,
# so local dev works without SESSION_SECRET set. In production it MUST come
# from the environment — a generated secret means every deploy (and every
# gunicorn worker) signs cookies differently and nobody stays logged in.
SECRET_KEY = os.environ.get('SESSION_SECRET') or secrets.token_hex(32)

# Deliberately not Flask's default 'session'. Renaming the cookie invalidates
# every session issued by the three standalone apps, which is exactly what the
# cutover wants: a stale estimator cookie carrying only session['user'] would
# otherwise satisfy the estimator's guard while the portal considers the rep
# signed out.
COOKIE_NAME = 'p1session'

LIFETIME_DAYS = 14


def _secure_cookies():
    """True when we're serving over HTTPS.

    SESSION_COOKIE_SECURE=1 forces it on; otherwise Railway's marker env var
    decides. Local dev over plain http://localhost must leave it off or the
    browser silently drops the cookie and login appears to do nothing.
    """
    override = os.environ.get('SESSION_COOKIE_SECURE', '').strip().lower()
    if override in ('1', 'true', 'yes'):
        return True
    if override in ('0', 'false', 'no'):
        return False
    return bool(os.environ.get('RAILWAY_ENVIRONMENT'))


def configure(app, max_content_length=None):
    """Apply the shared secret, ProxyFix, and the one true cookie config."""
    app.secret_key = SECRET_KEY
    # Railway terminates TLS at the edge; without ProxyFix Flask builds http://
    # URLs and marks Secure cookies as unsendable. The estimator was missing
    # this and compensated with PUBLIC_URL.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config.update(
        SESSION_COOKIE_NAME=COOKIE_NAME,
        # Explicit '/' so the cookie is shared across the mount prefixes.
        # Flask would default this to APPLICATION_ROOT, which is easy to set
        # per-app by accident and would scope the cookie to /crm or /estimate.
        SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=_secure_cookies(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=LIFETIME_DAYS),
    )
    if max_content_length is not None:
        app.config['MAX_CONTENT_LENGTH'] = max_content_length
    return app


def sign_in(session, user):
    """Write every key name the three existing auth guards read.

    canvasser + salescrm  ->  session['username'], session['is_admin']
    estimator             ->  session['user']

    Keeping all three populated is what lets `login_required`,
    `admin_required`, and the estimator's `_require_login` keep working
    verbatim after the merge.
    """
    session.permanent = True
    session['username'] = user['username']
    session['user'] = user['username']
    session['role'] = user['role']
    # salescrm treats is_admin as manager-or-above (that's what gates the
    # Numbers/Coaching tabs), so mirror that here rather than role == 'admin'.
    session['is_admin'] = user['role'] in ('admin', 'manager')


def sign_out(session):
    session.clear()
