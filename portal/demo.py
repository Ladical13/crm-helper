"""Demo sessions — a guest identity that can reach the estimator and nothing else.

Why this exists: the estimate tool is worth showing to people outside the
company (a peer contractor, a supplier, someone we might sell the thing to),
and every way of doing that today is bad. A screen share shows the tool badly.
Handing over a login hands over the customer list, every rep's phone number,
and the supplier costs the price book is built on. This is the third option:
one link, one guest session, no company data behind it.

It lives in `portal/` and not in `estimator/` for one specific reason. All four
apps share ONE cookie, and the app-switcher bar every page renders asks the
PORTAL's `/api/me` who it is looking at. A demo identity the portal did not
know about would fall into that endpoint's "row deleted out from under a live
cookie" branch, which calls `sign_out()` — so the bar would clear the guest's
own session and bounce them to a login page a second after the app loaded.
Identity is portal's job (CLAUDE.md: "Identity is portal/users.py only"), and a
guest identity is no exception; what a demo session may then *do* inside the
estimator is the estimator's, and lives in estimator/demo_store.py.

A demo session deliberately sets NEITHER `session['username']` NOR
`session['user']`. Those two keys are what the canvasser, the CRM and the
portal's own guard read, so a guest holding a demo cookie is anonymous to all
three and gets the login page — the estimator is the only app that asks this
module anything.

OFF until somebody turns it on, and there are two ways to do that:

  * an admin clicks "Create demo link" in ⚙ Settings, which writes a random
    token to PORTAL_DATA_DIR/demo_token.txt; or
  * P1_DEMO_TOKEN is set in the environment, which wins over the file.

The button is the intended path and the variable is the override. Making this
env-only was the first design and it was wrong in a specific way: the person
who needs the link is the person who cannot restart the service to get it, so
"off by default" turned into "off, and the only way on is a redeploy". A
feature nobody can switch on is not a safe feature, it is an unused one. The
token is the whole protection either way — 256 bits, exactly like the /sign
links this app already trusts with signed contracts — and revoking is now a
button rather than a variable somebody has to remember the name of.

With neither source set there is no route, no link and no session key honoured.
"""
import hmac
import os
import secrets

# Session key. Namespaced so it cannot collide with anything portal/session.py
# writes, and so `session.clear()` on sign-out takes it with everything else.
SESSION_KEY = 'p1_demo'

# The guest's username. Never enrolled in portal.users — nothing authenticates
# as this, there is no password for it, and it exists so the estimator has a
# non-empty string to hang ownership of the demo estimates on.
USERNAME = 'demo'
DISPLAY_NAME = 'Demo Guest'


#: Where a button-created token lives. Beside portal.db on the same volume,
#: because it is the same kind of thing: portal-owned session state that has to
#: survive a deploy and be the same in both gunicorn workers. NOT in DATA_DIR —
#: that is the estimator's volume (see users.db_path).
TOKEN_FILE = 'demo_token.txt'


def _token_path():
    """Resolved per call, not frozen at import — same reason as users.db_path:
    freezing it forces every test to set the env var before the import."""
    return os.path.join(os.environ.get('PORTAL_DATA_DIR') or
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        TOKEN_FILE)


def token():
    """The shared secret in the demo link, or '' when demo mode is off.

    Environment first so P1_DEMO_TOKEN stays an override that beats whatever is
    on the volume — the emergency kill is to set it to a value nobody has.

    Read from disk on every call rather than cached, so a link created in one
    gunicorn worker works on the very next request in the other, and a revoke
    takes effect immediately rather than at the next restart. It is a tiny read
    on a warm page cache, and a stale cached token here would be a demo link
    that keeps working after somebody deliberately revoked it.
    """
    env = (os.environ.get('P1_DEMO_TOKEN') or '').strip()
    if env:
        return env
    try:
        with open(_token_path(), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def env_override():
    """True when P1_DEMO_TOKEN is what is in force — the button cannot revoke
    it, and the UI has to say so rather than appearing to fail."""
    return bool((os.environ.get('P1_DEMO_TOKEN') or '').strip())


def create():
    """Mint a new demo token, replacing any existing one. Returns it.

    Creating a second time is how the link is ROTATED: the old URL stops
    working the moment this returns, which is what you want after handing it to
    someone you have since thought better of.
    """
    tok = secrets.token_urlsafe(32)
    path = _token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 0600 before anything is written to it, so the secret is never briefly
    # world-readable on a shared volume.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(tok)
    return tok


def revoke():
    """Delete the stored token. Idempotent.

    Returns False when P1_DEMO_TOKEN is set, because the file is then not what
    is in force and deleting it would report success while every live demo link
    kept working.
    """
    if env_override():
        return False
    try:
        os.remove(_token_path())
    except OSError:
        pass
    return True


def enabled():
    return bool(token())


def role():
    """Role the guest is treated as inside the estimator. Deliberately 'rep'.

    A rep sees the selling workflow — takeoff, packages, pricing, send, the
    customer's signing page — which is the part worth showing, and the front
    end hides the Price Book and Settings buttons from reps outright
    (applyRoleGates), so the guest is never offered a control that will 403.
    P1_DEMO_ROLE=manager opens those two read-only screens for a demo where
    that is the point; every write is still refused by the endpoint allowlist,
    so the Save buttons on them fail. Anything above manager is refused here —
    an admin demo could read the team list and every password-reset control.
    """
    r = (os.environ.get('P1_DEMO_ROLE') or 'rep').strip().lower()
    return r if r in ('rep', 'manager') else 'rep'


def matches(candidate):
    """True when `candidate` is the configured token. Constant-time."""
    t = token()
    if not t or not candidate:
        return False
    return hmac.compare_digest(str(candidate), t)


def activate(session):
    """Turn this session into a demo session. Caller must have checked the token."""
    session.permanent = True
    session[SESSION_KEY] = token()


def active(session):
    """True when this session is a live demo session.

    The token is stored in the session and re-checked here on every request,
    not just at activation, so changing P1_DEMO_TOKEN invalidates every demo
    link AND every session already opened with the old one. Clearing the
    variable turns the whole feature off retroactively.
    """
    return enabled() and matches(session.get(SESSION_KEY))


def link(base_url):
    """The shareable URL, or '' when demo mode is off."""
    return f"{base_url.rstrip('/')}/estimate/demo/{token()}" if enabled() else ''


def identity(mounts):
    """The payload portal's /api/me returns for a guest.

    `apps` carries the estimator alone: the switcher bar is built from this
    list, and a Canvass or Pipeline tab would be a tab that logs the guest out.
    """
    return {
        'authenticated': True,
        'demo': True,
        'username': USERNAME,
        'full_name': DISPLAY_NAME,
        'email': '',
        'role': role(),
        'is_admin': False,
        'is_manager': False,
        'must_change': False,
        'apps': [{k: m[k] for k in ('key', 'prefix', 'label', 'icon', 'blurb', 'accent')}
                 for m in mounts if m['key'] == 'estimate'],
        'admin_apps': [],
    }
