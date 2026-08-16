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

# Default request-body cap for any app that doesn't ask for its own. Comfortably
# above the CRM's 25 MB per-document limit, which is the largest upload any app
# other than the estimator accepts.
DEFAULT_MAX_CONTENT_LENGTH = 32 * 1024 * 1024


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


# Sent on every response from all four apps. Railway's edge adds none of these.
#
# HSTS is the one with teeth and the one that is hard to undo: once a browser
# has seen it, that browser refuses plain http:// to this host for a year. That
# is exactly what we want in production and exactly what would break local dev,
# so it is added only when the cookies are already Secure (see _secure_cookies).
#
# There is deliberately no Content-Security-Policy here. All four front ends
# use inline event handlers and inline <style>, and the estimator's customer
# pages are built as inline-CSS HTML strings in app.py — a useful CSP would
# need 'unsafe-inline', which buys almost nothing, and a strict one would break
# the signing pages customers already have links to. Adding CSP properly means
# moving that markup first; tracked as its own job rather than shipped broken.
BASE_SECURITY_HEADERS = {
    # Stops a browser from second-guessing our Content-Type. Matters most on
    # /uploads and the CRM's document download, where a rep-supplied file is
    # served back: without this, a file sniffed as HTML can run script on our
    # own origin.
    'X-Content-Type-Options': 'nosniff',
    # No reason for any of these apps to be framed. Also the clickjacking
    # defence for the customer-facing sign pages.
    'X-Frame-Options': 'DENY',
    # Don't leak estimate share tokens (they live in the /sign/<token> path) to
    # third-party sites through the Referer header.
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    # The rep tools have no business reading location/camera/mic from an
    # embedded context. The canvasser reads GPS from its own top-level page,
    # which this does not affect.
    'Permissions-Policy': 'geolocation=(self), camera=(), microphone=()',
}

HSTS_VALUE = 'max-age=31536000; includeSubDomains'


def _add_security_headers(resp):
    for header, value in BASE_SECURITY_HEADERS.items():
        resp.headers.setdefault(header, value)
    if _secure_cookies():
        resp.headers.setdefault('Strict-Transport-Security', HSTS_VALUE)
    return resp


def configure(app, max_content_length=None):
    """Apply the shared secret, ProxyFix, the one true cookie config, and the
    shared security headers.

    Safe to call twice on the same app — portal/wsgi.py re-applies it to each
    sub-app at mount time so all four agree on the merged origin, and canvasser
    and salescrm already install their own ProxyFix. Double-wrapping ProxyFix
    would consume two hops of X-Forwarded-For and hand Flask the proxy's
    address as the client, so the wrap is guarded. The header hook is guarded
    the same way, so a second call doesn't register it twice.
    """
    app.secret_key = SECRET_KEY
    # Railway terminates TLS at the edge; without ProxyFix Flask builds http://
    # URLs and marks Secure cookies as unsendable. The estimator was missing
    # this and compensated with PUBLIC_URL.
    if not getattr(app, '_p1_proxyfix', False):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
        app._p1_proxyfix = True
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
    if not getattr(app, '_p1_security_headers', False):
        app.after_request(_add_security_headers)
        app._p1_security_headers = True
    # The read-only executive-team principal is bounded here rather than in
    # each app, for the same reason the cookie config is: four copies of a
    # security rule is three chances to forget one. Cheap on a normal request —
    # it returns immediately unless the session belongs to apibot.
    if not getattr(app, '_p1_apibot_guard', False):
        from portal import apibot
        app.before_request(apibot.guard)
        app._p1_apibot_guard = True
    # Cap request bodies. Werkzeug buffers an upload into memory once the code
    # calls .read() on it, so without a limit one oversized POST can take a
    # whole gunicorn worker down — and with only two workers that is half the
    # site. Every app gets the default; the estimator passes its own larger one
    # because it accepts roof photos and multi-page carrier PDFs.
    app.config['MAX_CONTENT_LENGTH'] = max_content_length or DEFAULT_MAX_CONTENT_LENGTH
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
