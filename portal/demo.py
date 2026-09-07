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

OFF unless P1_DEMO_TOKEN is set. No token, no route, no session key honoured:
an unset variable must never leave a door open, which is the same reason
DISABLE_AUTH refuses to engage on Railway.
"""
import hmac
import os

# Session key. Namespaced so it cannot collide with anything portal/session.py
# writes, and so `session.clear()` on sign-out takes it with everything else.
SESSION_KEY = 'p1_demo'

# The guest's username. Never enrolled in portal.users — nothing authenticates
# as this, there is no password for it, and it exists so the estimator has a
# non-empty string to hang ownership of the demo estimates on.
USERNAME = 'demo'
DISPLAY_NAME = 'Demo Guest'


def token():
    """The shared secret in the demo link, or '' when demo mode is off.

    Read on every call rather than captured at import so a test can turn the
    feature on and off, and so rotating the variable takes effect on restart
    without anything else having cached the old value.
    """
    return (os.environ.get('P1_DEMO_TOKEN') or '').strip()


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
