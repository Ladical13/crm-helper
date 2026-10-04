"""API access for the executive team and Jarvis: reads, plus one write.

The AI executive team (`Projects/p1r-exec-team`) and Jarvis need to read this
repo's numbers — the estimator's margins, the CRM's pipeline, the canvasser's
hail cache, Nimbus's marketing state — and Jarvis works the daily outreach
queue. Every endpoint here sits behind ``portal/app.py``'s default-deny
``_require_login()``, which accepts a session cookie and nothing else, so an
outside process had no way in.

This adds exactly one way in, and makes it as narrow as it can usefully be:

  1. ``POST /api/apibot/session`` with header ``X-P1-Token`` exchanges the
     shared secret for an ordinary session cookie belonging to a real portal
     user named ``apibot``.
  2. From then on ``apibot`` is treated as any signed-in manager would be —
     except that ``guard()`` refuses every GET outside ``ALLOWLIST`` and every
     other request outside ``WRITES``.

``WRITES`` holds one route: ``POST /crm/api/queue/log``, which records that an
outreach touch went out. It exists because the 7-day cooldown and the
leaderboard both key off the activity log: a touch Jarvis drafted that nobody
logs is a touch the queue serves again tomorrow, so the partner gets contacted
twice. That endpoint accepts outreach kinds only and credits the touch to the
rep who owns the lead, never to apibot. Approved by Luke 2026-10-04.

Reusing the real session stack rather than bolting on a second auth path is
deliberate: there is one login mechanism in this codebase, one cookie, one set
of role checks, and this does not become the exception.

``apibot`` cannot log in through the form — it is created with a random
password nobody holds — so the token is the only route to it, and revoking the
token (unset the env var, redeploy) closes it completely.

Not enabled unless ``P1_READONLY_TOKEN`` is set. Absent, the exchange endpoint
404s and the guard is inert.
"""
import hmac
import os
import secrets

from flask import jsonify, request, session

from portal import session as psession
from portal import throttle, users

USERNAME = 'apibot'

# GETs allowed. Prefix match against the FULL path including the mount prefix —
# DispatcherMiddleware strips it before the sub-app sees the request, so
# `request.path` inside the estimator is '/api/analytics', not
# '/estimate/api/analytics'. See _full_path().
#
# Every entry is reporting, with one deliberate exception: the outreach queue,
# which carries the contact details of the people due a touch TODAY, because
# drafting to them is the job. That is a day's work list, not the database.
# Nothing that returns a document or a photo belongs here, and the bulk lead
# list has its contact columns stripped for apibot (salescrm's
# `_redact_for_apibot()`), so a lost token is not a lost customer list.
ALLOWLIST = (
    # Estimator — the money. Revenue, margin by trade, close-rate cohorts,
    # funnel, pipeline aging, per-rep, monthly trend.
    '/estimate/api/analytics',
    '/estimate/api/goals',
    # Sales CRM — the pipeline, and today's outreach.
    '/crm/api/leads',
    '/crm/api/leaderboard',
    '/crm/api/goals',
    '/crm/api/tasks',
    '/crm/api/queue/today',
    '/crm/api/outreach/summary',
    # Canvasser — the hail cache the storm work already depends on.
    '/canvass/api/hail',
    # Nimbus — marketing state. Its gate admits apibot for GETs only.
    '/nimbus/api/',
)

# Entries that match themselves only, never a path beneath them. Without this,
# '/crm/api/leads' also opened '/crm/api/leads/<id>' (a lead's full contact
# record and activity log) and '/crm/api/leads/<id>/documents' (signed
# contracts).
EXACT = frozenset({
    '/crm/api/leads',
})

# The only non-GET requests apibot may make: (method, full path).
WRITES = frozenset({
    ('POST', '/crm/api/queue/log'),
})


class Disabled(RuntimeError):
    """P1_READONLY_TOKEN is not set, so the bridge does not exist."""


def configured_token():
    return os.environ.get('P1_READONLY_TOKEN', '').strip()


def enabled():
    return bool(configured_token())


def is_apibot(username=None):
    """True when the current session (or `username`) is the API principal."""
    return (username if username is not None else session.get('username')) == USERNAME


def _full_path():
    """Path as the outside world wrote it, including the mount prefix.

    `script_root` is '' on the portal app and '/estimate', '/crm' or
    '/canvass' inside a mounted sub-app.
    """
    return (request.script_root or '') + request.path


def path_allowed(full_path):
    """Prefix match, but only at a path boundary.

    A bare ``startswith`` would let ``/crm/api/leadsX`` through on the strength
    of ``/crm/api/leads`` — a different route, sharing a prefix by accident.
    An entry ending in ``/`` is an explicit subtree (``/nimbus/api/``); an
    entry in ``EXACT`` matches itself only; anything else must match exactly
    or be followed by ``/``.
    """
    path = (full_path or '').split('?', 1)[0]
    for allowed in ALLOWLIST:
        if allowed in EXACT:
            if path == allowed:
                return True
        elif allowed.endswith('/'):
            if path.startswith(allowed):
                return True
        elif path == allowed or path.startswith(allowed + '/'):
            return True
    return False


def write_allowed(method, full_path):
    return (method, (full_path or '').split('?', 1)[0]) in WRITES


def ensure_user():
    """Create the apibot principal if it doesn't exist yet. Idempotent.

    The password is random and immediately discarded, so the account is
    unreachable through the login form by construction rather than by a flag
    somebody could flip. Role is `manager` because several reporting endpoints
    gate on manager-or-above (the CRM's leaderboard, goals and other reps'
    queues do); `guard()` is what stops that role being used for anything but
    reading and the one logging write.
    """
    existing = users.get(USERNAME)
    if existing:
        return existing
    return users.create(
        USERNAME,
        password=secrets.token_urlsafe(64),
        role='manager',
        full_name='Jarvis / executive team (API)',
    )


def guard():
    """before_request hook. Registered on all four apps by session.configure().

    Returns None for everybody who isn't apibot, so the cost on a normal
    request is one dict lookup.
    """
    if not is_apibot():
        return None
    # Re-authenticating is always allowed. Without this exemption a client
    # holding an expiring cookie cannot refresh it — the guard refuses the
    # exchange because the caller is already apibot — and the only way out is
    # to clear cookies. The endpoint still checks the token itself.
    if request.endpoint == 'apibot_session':
        return None
    if request.method != 'GET':
        if write_allowed(request.method, _full_path()):
            return None
        return jsonify({'error': 'apibot is read-only'}), 403
    if not path_allowed(_full_path()):
        return jsonify({'error': 'path not available to apibot'}), 403
    return None


def register(app):
    """Add the token-exchange route. Portal app only — the guard is separate
    and goes on everything."""

    @app.route('/api/apibot/session', methods=['POST'])
    def apibot_session():
        expected = configured_token()
        if not expected:
            # Indistinguishable from a route that was never registered, so a
            # prober cannot learn whether the feature exists here.
            return jsonify({'error': 'not found'}), 404

        ip = request.remote_addr or 'unknown'
        wait = throttle.retry_after(USERNAME, ip)
        if wait:
            return jsonify({'error': 'too many attempts',
                            'retry_after': wait}), 429

        presented = request.headers.get('X-P1-Token', '')
        if not presented or not hmac.compare_digest(presented, expected):
            throttle.record_failure(USERNAME, ip)
            return jsonify({'error': 'bad token'}), 401

        throttle.clear(USERNAME, ip)
        user = ensure_user()
        psession.sign_in(session, user)
        return jsonify({
            'ok': True,
            'username': USERNAME,
            # Kept for existing callers: true for everything except `writes`.
            'read_only': True,
            'allowlist': list(ALLOWLIST),
            'writes': sorted(f'{m} {p}' for m, p in WRITES),
        })

    return app
