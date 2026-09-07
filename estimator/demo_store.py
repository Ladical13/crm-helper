"""Demo mode, estimator half — what a guest session may reach, and on what data.

portal/demo.py decides WHO a demo guest is (and has to, because the shared
cookie and the app-switcher bar are the portal's). This module decides what
that guest can do once they are inside the estimator. Three separations have to
hold at once, because a demo link is by definition handed to someone we do not
control and will be forwarded to people we never meet:

1. DATA. `store()` is a process-local dict seeded from demo_seed.json, and
   every est_* helper in app.py checks `active()` before touching the real
   store. That is ONE choke point covering all 98 routes — the alternative was
   auditing each route for what it reads, which is the bookkeeping that let the
   estimator's data APIs leak before the default-deny guard replaced it.
   Nothing a guest does reaches DATA_DIR, Postgres, or portal.db.

2. REACH. `ALLOWED_ENDPOINTS` below is default-deny, the same shape as
   PUBLIC_ENDPOINTS in app.py and for the same reason: a route added tomorrow
   is closed to demo until somebody opens it here on purpose. An allowlist that
   has to be extended is a nuisance once; a denylist that was never extended is
   a leak nobody notices.

3. SIDE EFFECTS. Nothing a guest touches sends mail, writes to Base44, records
   to the CRM funnel, writes a file, or calls a metered API (Perplexity for
   jurisdiction lookups, fal for the visualizer). Most of that falls out of the
   allowlist. The exception is the post-signature pipeline, which runs from the
   CUSTOMER's browser on a public /sign link rather than from a demo session —
   `is_demo_doc()` is what app.py checks there.

Costs are scrambled by default (`scrub_costs`). The price book is the one thing
in this app that is genuinely competitive: it is the supplier pricing every bid
is built on, and "show them the tool" is not a decision to publish it. See that
function for the trade-off and the escape hatch.
"""
import copy
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta

from flask import has_request_context, session

from portal import demo as pdemo

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_FILE = os.path.join(HERE, 'demo_seed.json')


def active():
    """True when the request in flight belongs to a demo guest.

    Guarded on `has_request_context()` so the est_* helpers can call it from a
    background thread (the post-signature pipeline runs in one) without raising.
    Outside a request there is no demo session, which is the safe answer: it
    means "use the real store".
    """
    return bool(has_request_context()) and pdemo.active(session)


# ── What a guest may reach ─────────────────────────────────────────────────
# Endpoint names, not URLs — `request.endpoint`, which is the view function's
# name. app.py's PUBLIC_ENDPOINTS are allowed on top of these (a guest has to
# be able to open the customer signing link they just generated).
ALLOWED_ENDPOINTS = frozenset({
    # The app shell and the boot fetches in app.js's DOMContentLoaded handler.
    'index', 'enter_demo', 'me', 'server_info',
    'get_templates', 'get_pricebook', 'get_tier_defaults', 'get_app_settings',

    # Config the estimate screens read. All GET; every matching PUT is absent
    # from this list on purpose, so a guest can look at how the tool is set up
    # and change nothing for anybody.
    'get_company_content', 'get_permit_defaults', 'get_commercial_fastening',
    'get_jurisdictions', 'get_lost_reasons',
    'get_exterior_catalog', 'roof_certificate_defaults',
    'visualizer_capabilities', 'visualizer_operations',

    # Estimates — all of these land in the demo store, never the real one.
    'list_estimates', 'create_estimate', 'get_estimate', 'save_estimate',
    'duplicate_estimate', 'delete_estimate', 'update_estimate_label',
    'update_estimate_status', 'get_customer_notes', 'set_customer_notes',

    # The selling flow: build the customer link, open it, watch it come back
    # signed. `create_share_link` only mints a token — `email_estimate_link`
    # is the one that sends mail, and it is deliberately not here.
    'create_share_link', 'present_estimate', 'view_signed_estimate',

    # Change orders. They write to the estimate doc and nowhere else, so they
    # are demo-scoped for free; `change_order_email` is not, and is not here.
    'change_orders_collection', 'change_order_item', 'change_order_status',
    'change_order_share', 'change_order_pdf',
})

# The analytics tab is CLOSED, and not because of the estimates it counts —
# those come out of the demo store and would be the three seeded ones. It is
# closed because `/api/analytics` embeds `_load_goals()`: the company's real
# monthly revenue and job targets, which have nothing to do with which
# estimates it is summing and would go out to a guest verbatim. `/api/goals` is
# closed for the same reason. Opening either one means scrubbing the goals
# first, the way the price book's costs are scrubbed.


# Everything a demo guest asks for that is not on that list gets this, rather
# than a bare 403: the front end has no idea it is in a demo and would show
# "access denied", which reads like the tool is broken rather than fenced.
DENIED = {'error': 'Not available in the demo — this is a live company system.',
          'demo': True}


def endpoint_allowed(endpoint):
    return endpoint in ALLOWED_ENDPOINTS


# ── The demo's own data ────────────────────────────────────────────────────
# Process-local and in-memory on purpose. It is throwaway by definition, it
# must not survive into anything backed up, and a restart putting the demo back
# to a known-good state is the behaviour you want when the last guest left it
# half-edited. Two gunicorn workers therefore hold two copies: a guest can see
# an estimate they created vanish on the next request if they land on the other
# worker. That is a real rough edge and it is the cheap side of the trade —
# persisting it means picking a store, and every store here is one a guest must
# not be able to write to.
_LOCK = threading.RLock()
_STATE = {'estimates': None, 'notes': None}

_DATE_RE = re.compile(r'\{\{(TODAY|NOW)([+-]\d+)?\}\}')


def _materialize_dates(obj):
    """Replace {{TODAY-6}} / {{NOW+30}} with real dates, offset in days.

    The seed carries offsets rather than fixed dates so the demo never goes
    stale: a checked-in `valid_until` would quietly start rendering "expired"
    a month after it was written, and `_est_expired` would withdraw the
    signature block from the very screen the link exists to show off.
    """
    if isinstance(obj, dict):
        return {k: _materialize_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_materialize_dates(v) for v in obj]
    if not isinstance(obj, str):
        return obj

    def sub(m):
        when = datetime.utcnow() + timedelta(days=int(m.group(2) or 0))
        return when.strftime('%Y-%m-%d') if m.group(1) == 'TODAY' \
            else when.isoformat(timespec='seconds') + 'Z'
    return _DATE_RE.sub(sub, obj)


def _load_seed():
    try:
        with open(SEED_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        # An empty demo is a poor demo, but a demo that 500s on the home screen
        # is worse, and this must never take the real app down with it.
        print(f'[demo] seed unreadable ({exc}) — starting with no estimates')
        return {}
    out = {}
    for doc in _materialize_dates(raw.get('estimates') or []):
        doc['demo'] = True          # what is_demo_doc() keys off, below
        out[doc['estimate_id']] = doc
    return out


def reset():
    """Restore the seeded estimates, discarding whatever the last guest did."""
    with _LOCK:
        _STATE['estimates'] = _load_seed()
        _STATE['notes'] = {}
    return _STATE['estimates']


def store():
    """estimate_id -> doc, seeded on first use."""
    with _LOCK:
        if _STATE['estimates'] is None:
            reset()
        return _STATE['estimates']


def notes():
    """Customer-level notes, demo-scoped. Same shape as customer_notes.json."""
    with _LOCK:
        if _STATE['notes'] is None:
            reset()
        return _STATE['notes']


def owns(est_id):
    """True when this id belongs to the demo store, session or no session.

    The session is not enough to route a read or a write. Two paths reach a
    demo estimate with no demo cookie anywhere in sight, and both matter:
    the customer's browser POSTing a signature to the public /sign link, and
    the background thread that runs afterwards. Routing those by session sends
    them to the real store, where the id does not exist — so signing the demo
    estimate would fail with "no longer available for signing", which is the
    single screen the demo most needs to reach.

    Safe because an id only lands in this store two ways: it was seeded, or a
    demo session created it. A caller with no demo session can only get here
    holding an id or a token it was already given.
    """
    return bool(est_id) and pdemo.enabled() and str(est_id) in store()


def is_demo_doc(doc):
    """True for an estimate that belongs to the demo.

    Read off the document, not off the session, because the paths that matter
    most — the signature POST and the background pipeline it starts — run from
    the customer's browser, where there is no demo session to ask.
    """
    return bool(isinstance(doc, dict) and doc.get('demo'))


def find_by(field, value):
    """First demo doc whose top-level `field` equals `value`, or None.

    Used for share/design token lookups, which are checked against the demo
    store even for callers with no demo session: the whole point of generating
    a customer link in the demo is that it opens, and it will be opened in
    another tab, on a phone, by somebody the guest forwarded it to.
    """
    if not value:
        return None
    # Snapshotted: another worker thread saving an estimate mid-scan would
    # otherwise raise "dictionary changed size during iteration".
    for doc in list(store().values()):
        if doc.get(field) == value:
            return doc
    return None


# ── Costs ──────────────────────────────────────────────────────────────────

_COST_KEYS = ('cost', 'unit_cost', 'material_unit_cost', 'labor_unit_cost')


def scramble_costs_enabled():
    return (os.environ.get('P1_DEMO_REAL_COSTS') or '').strip().lower() \
        not in ('1', 'true', 'yes')


def scrub_costs(pb):
    """Return the price book with every cost shifted by a per-product factor.

    The structure, the product names, the bundles and the margin rates all stay
    exactly as they are — the tool has to price a real-looking job or there is
    nothing to demo. What changes is the one number that is nobody else's
    business: what we actually pay per square. Zeroing the costs was the
    obvious alternative and it does not work — in margin mode sell is derived
    FROM cost, so a zeroed book prices every package at $0 and the demo shows
    an empty estimate.

    The factor is derived from a hash of the product's own id, so it is stable
    across workers and restarts (two guests comparing screens see the same
    numbers, and a guest reloading does not watch the totals drift) but tells
    you nothing about the real figure. sha256 rather than hash(), which is
    salted per process.

    Set P1_DEMO_REAL_COSTS=1 to serve the true book — for a demo where the
    pricing IS the point and the audience is under an NDA.
    """
    if not scramble_costs_enabled():
        return pb
    return _scrub(copy.deepcopy(pb), '')


def _factor(label):
    """±4–15%, deterministic in `label`, and NEVER exactly 1.

    The excluded midpoint is the point. A factor picked from a range that
    happens to contain 1.0 leaves a few percent of products sitting at their
    true cost, and "most of these are wrong" is not a property you can explain
    to anyone — the guarantee has to be that none of them is right.
    """
    h = hashlib.sha256(label.encode('utf-8')).digest()
    pct = 4 + (h[0] % 12)                    # 4..15
    return 1 + (-pct if h[1] & 1 else pct) / 100.0


def _scrub(node, label):
    if isinstance(node, list):
        return [_scrub(v, label) for v in node]
    if not isinstance(node, dict):
        return node
    # An id/name on the node itself is a better seed than the path: it keeps a
    # product's factor stable when a manager reorders the catalog.
    here = str(node.get('id') or node.get('name') or label)
    for k, v in node.items():
        if k in _COST_KEYS and isinstance(v, (int, float)) and v:
            node[k] = round(v * _factor(f'{here}:{k}'), 2)
        else:
            node[k] = _scrub(v, here)
    return node
