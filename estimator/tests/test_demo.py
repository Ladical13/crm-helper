"""Demo mode — the fence around a link handed to someone outside the company.

Every test here pins something that fails SILENTLY. A demo that leaks the
customer list still looks like a working demo; a demo that mails a real
customer looks like a working demo right up until somebody replies; a demo
whose estimates land in the real store looks fine until a rep opens their
dashboard. So each separation gets a test rather than a comment.

See estimator/demo_store.py for the design and portal/demo.py for the identity.
"""
import os
import threading

import pytest

import app as estimator_app
import demo_store
from portal import demo as pdemo


TOKEN = 'demo-token-for-tests-only'


@pytest.fixture(autouse=True)
def no_stored_token():
    """No token file, so the env var below is what is in force.

    Without this the admin-control tests below and the session tests fight over
    the same file: one creates a token, the next reads it and passes for the
    wrong reason.
    """
    pdemo.revoke()
    yield
    if not pdemo.env_override():
        pdemo.revoke()


@pytest.fixture(autouse=True)
def demo_on(monkeypatch, no_stored_token):
    """Turn demo mode on for the duration of a test, and reset the store.

    The store is process-global (one dict shared by every request a worker
    serves), so a test that signs the seeded estimate would otherwise leave it
    signed for the next one.
    """
    monkeypatch.setenv('P1_DEMO_TOKEN', TOKEN)
    monkeypatch.delenv('P1_DEMO_ROLE', raising=False)
    monkeypatch.delenv('P1_DEMO_REAL_COSTS', raising=False)
    demo_store.reset()
    yield
    demo_store.reset()


@pytest.fixture
def guest(app):
    """A browser that has followed the demo link."""
    c = app.test_client()
    assert c.get(f'/demo/{TOKEN}').status_code == 302
    return c


# ── The door ───────────────────────────────────────────────────────────────

def test_the_link_opens_the_app(guest):
    assert guest.get('/').status_code == 200
    me = guest.get('/api/me').get_json()
    assert me['demo'] is True
    assert me['username'] == pdemo.USERNAME


def test_a_wrong_token_is_a_404_not_a_403(app):
    """404, so a guess cannot confirm the feature exists to guess at."""
    assert app.test_client().get('/demo/not-the-token').status_code == 404


def test_demo_is_off_when_the_variable_is_unset(app, monkeypatch):
    """The fail-closed default. An unset variable must not leave a door open."""
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    assert app.test_client().get(f'/demo/{TOKEN}').status_code == 404
    assert not pdemo.enabled()


def test_a_stale_demo_cookie_dies_with_the_token(app, monkeypatch):
    """Rotating P1_DEMO_TOKEN has to kill sessions already open on the old one.

    The token is re-checked on every request rather than trusted because it was
    checked once at activation — otherwise revoking a leaked link would mean
    waiting out every browser that already holds a cookie.
    """
    c = app.test_client()
    c.get(f'/demo/{TOKEN}')
    assert c.get('/api/estimates').status_code == 200
    monkeypatch.setenv('P1_DEMO_TOKEN', 'a-different-token')
    assert c.get('/api/estimates').status_code == 401


def test_a_guest_is_not_a_user_in_any_other_app(guest):
    """session['user'] / ['username'] stay empty — those are what the canvasser,
    the CRM and the portal's own guard read."""
    with guest.session_transaction() as s:
        assert not s.get('user')
        assert not s.get('username')
        assert s.get(pdemo.SESSION_KEY) == TOKEN


# ── Data isolation ─────────────────────────────────────────────────────────

def test_a_guest_sees_only_the_seeded_estimates(guest, client):
    """Not one real customer name reaches the demo's dashboard."""
    client.post('/api/estimates',
                json={'customer': {'name': 'A Real Homeowner'}, 'salesperson': 'luke'})
    names = {e['customer_name'] for e in guest.get('/api/estimates').get_json()}
    assert 'A Real Homeowner' not in names
    assert names == {d['customer']['name'] for d in demo_store.store().values()}


def test_a_rep_never_sees_demo_estimates(client):
    """The other direction, which is the one that would look like a data bug
    rather than a leak: demo customers must not appear on a rep's dashboard."""
    names = {e['customer_name'] for e in client.get('/api/estimates').get_json()}
    for doc in demo_store.store().values():
        assert doc['customer']['name'] not in names


def test_a_guest_cannot_open_a_real_estimate_by_id(guest, client):
    r = client.post('/api/estimates', json={'customer': {'name': 'Private'},
                                            'salesperson': 'luke'})
    real_id = r.get_json()['estimate_id']
    assert guest.get(f'/api/estimates/{real_id}').status_code == 404


def test_what_a_guest_writes_never_reaches_the_real_store(guest):
    before = sorted(os.listdir(estimator_app.ESTIMATES_DIR))
    r = guest.post('/api/estimates', json={'customer': {'name': 'Guest Made This'}})
    new_id = r.get_json()['estimate_id']
    assert sorted(os.listdir(estimator_app.ESTIMATES_DIR)) == before
    assert new_id in demo_store.store()


def test_customer_notes_are_demo_scoped(guest, client):
    """Real notes are about real people; a guest must not read one by guessing
    a name, and must not overwrite one by using the same name."""
    client.put('/api/customer-notes/Dana Whitfield',
               json={'notes': 'REAL NOTE — not for guests'})
    assert guest.get('/api/customer-notes/Dana Whitfield').get_json()['notes'] == ''
    guest.put('/api/customer-notes/Dana Whitfield', json={'notes': 'guest typed this'})
    assert client.get('/api/customer-notes/Dana Whitfield').get_json()['notes'] \
        == 'REAL NOTE — not for guests'


# ── Reach ──────────────────────────────────────────────────────────────────

# Not an exhaustive list — the allowlist is the mechanism and the test below
# covers the rest. These are the ones whose exposure would matter most.
@pytest.mark.parametrize('method,path', [
    ('get',    '/api/users'),            # every enrolled account
    ('get',    '/api/team'),             # rep names, phones, emails
    ('get',    '/api/backup'),           # every estimate + photo + config
    ('get',    '/api/crm/contacts?q=a'), # Base44 customer search
    ('get',    '/api/find-estimate'),
    ('get',    '/api/signed-estimates'),
    ('put',    '/api/pricebook'),
    ('put',    '/api/settings'),
    ('put',    '/api/company-content'),
    ('put',    '/api/server-info'),
    ('post',   '/api/test-notification'),
    ('get',    '/api/jurisdictions/verify?jid=loveland'),  # metered (Perplexity)
    ('post',   '/api/parse-roofr'),
    # Both carry the company's real monthly revenue and job targets:
    # /api/analytics embeds _load_goals() alongside the estimates it sums, so
    # closing /api/goals alone would not have been enough.
    ('get',    '/api/goals'),
    ('get',    '/api/analytics'),
])
def test_the_allowlist_refuses_everything_it_should(guest, method, path):
    r = getattr(guest, method)(path)
    assert r.status_code == 403, f'{method.upper()} {path} was not refused'
    assert r.get_json().get('demo') is True


def test_every_endpoint_is_denied_unless_it_was_opened_on_purpose(app):
    """The allowlist is default-deny, and this is what keeps it that way.

    A route added to app.py tomorrow is closed to a guest until someone puts it
    in ALLOWED_ENDPOINTS. This test does not check that the current list is
    RIGHT — it checks that the list is the only thing granting access, so the
    decision is always made rather than inherited.
    """
    known = demo_store.ALLOWED_ENDPOINTS | estimator_app.PUBLIC_ENDPOINTS
    for rule in app.url_map.iter_rules():
        if rule.endpoint in known:
            continue
        assert not demo_store.endpoint_allowed(rule.endpoint)


#: Every writer of a file on the shared volume that a demo GET can read. One
#: guest saving the price book changes what every rep quotes tomorrow, so the
#: read half being open must never be read as licence to open the write half.
#: Estimates and customer notes are absent on purpose — those writes ARE open,
#: and go to the demo's own store (est_save / _read_customer_notes).
SHARED_CONFIG_WRITERS = [
    'put_pricebook', 'upsert_intro', 'delete_intro', 'put_tier_defaults',
    'put_app_settings', 'put_company_content', 'put_permit_defaults',
    'put_commercial_fastening', 'put_jurisdictions', 'put_sales_goals',
    'put_exterior_catalog', 'import_exterior_catalog', 'upload_exterior_texture',
    'upload_exterior_placement_image', 'save_server_info',
    'add_team_member', 'edit_team_member', 'remove_team_member',
    'set_user_password', 'set_user_role', 'reset_user',
]


def test_no_shared_config_writer_is_reachable(app):
    known = {r.endpoint for r in app.url_map.iter_rules()}
    for endpoint in SHARED_CONFIG_WRITERS:
        # Catches a rename: the list is only worth anything while it names
        # routes that exist.
        assert endpoint in known, f'{endpoint} is no longer a route — update this list'
        assert not demo_store.endpoint_allowed(endpoint)


# ── Side effects ───────────────────────────────────────────────────────────

def test_the_customer_link_works_without_a_demo_cookie(app, guest):
    """The whole point of generating a share link is that somebody else opens
    it. The signing page has no demo session — it finds the estimate by token."""
    doc = next(d for d in demo_store.store().values() if d.get('share_token'))
    r = app.test_client().get(f"/sign/{doc['share_token']}")
    assert r.status_code == 200
    assert doc['customer']['name'] in r.get_data(as_text=True)


def test_signing_the_demo_sends_nothing_and_pushes_nothing(app, monkeypatch):
    """The signature has to save (it is the screen worth showing) while every
    side effect behind it — mail, the CRM funnel, The Den, the packets — is
    skipped. All of that runs with no demo session: from the customer's own
    browser and from the thread it starts, so the DOCUMENT is what says demo.
    """
    sent, recorded, pipelines = [], [], []
    monkeypatch.setattr(estimator_app, '_send_email',
                        lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(estimator_app.pfunnel, 'record',
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(estimator_app, 'push_contract_to_crm',
                        lambda *a, **k: pipelines.append(a))

    doc = next(d for d in demo_store.store().values()
               if d.get('share_token') and not d.get('signature'))
    r = app.test_client().post(f"/sign/{doc['share_token']}",
                               data={'sig_name': 'Sample Customer',
                                     'sig_email': 'sample@example.com',
                                     'selected_tier': 'better'})
    assert r.status_code == 200
    # The pipeline runs in a thread the route spawns; join it or the assertions
    # below race it and pass for the wrong reason. Same helper shape as
    # test_signed_document._await_post_sign.
    for t in threading.enumerate():
        if t.name == estimator_app.POST_SIGN_THREAD:
            t.join(30)
            assert not t.is_alive()

    assert demo_store.store()[doc['estimate_id']]['signature']['name'] == 'Sample Customer'
    assert sent == [], 'a demo signature sent mail'
    assert recorded == [], 'a demo signature reached the CRM funnel'
    assert pipelines == [], 'a demo signature reached The Den'


def test_email_is_suppressed_even_if_a_route_ever_reaches_it(app, monkeypatch):
    """The backstop. The allowlist keeps a guest off every sending route, but
    this is the one function they all funnel through, and it is the one that
    puts the company's name on a message to a stranger."""
    from flask import session as flask_session
    delivered = []
    monkeypatch.setattr(estimator_app, '_send_via_sendgrid_api',
                        lambda *a, **k: delivered.append(a) or True)
    monkeypatch.setattr(estimator_app, '_send_via_smtp',
                        lambda *a, **k: delivered.append(a) or True)

    with app.test_request_context('/'):
        flask_session[pdemo.SESSION_KEY] = TOKEN
        assert estimator_app._send_email('subject', '<p>body</p>',
                                         'someone@example.com') is False
    assert delivered == []

    # ...and still sends for everybody else, so the guard cannot pass by
    # having broken email outright.
    with app.test_request_context('/'):
        assert estimator_app._send_email('subject', '<p>body</p>',
                                         'someone@example.com') is True
    assert len(delivered) == 1


# ── Costs ──────────────────────────────────────────────────────────────────

def test_costs_are_shifted_and_nothing_else_is(guest, client):
    """Structure, product names, bundles and margins stay real — the tool has
    to price a real-looking job. The per-square cost is the one thing that is
    nobody else's business."""
    real = client.get('/api/pricebook').get_json()
    shown = guest.get('/api/pricebook').get_json()

    real_costs = {p['id']: p.get('cost') for p in real.get('roofing_catalog') or []}
    demo_costs = {p['id']: p.get('cost') for p in shown.get('roofing_catalog') or []}
    assert real_costs and demo_costs.keys() == real_costs.keys()
    for pid, cost in real_costs.items():
        if cost:
            assert demo_costs[pid] != cost, f'{pid} shipped its real cost'

    assert [b['id'] for b in shown.get('roofing_bundles') or []] == \
           [b['id'] for b in real.get('roofing_bundles') or []]
    assert [p['name'] for p in shown['roofing_catalog']] == \
           [p['name'] for p in real['roofing_catalog']]


def test_shifted_costs_are_stable(guest):
    """Two guests comparing screens, or one guest reloading, must see the same
    numbers — a per-request jitter would make the demo look broken."""
    first = guest.get('/api/pricebook').get_json()
    second = guest.get('/api/pricebook').get_json()
    assert first == second


def test_costs_stay_usable_so_the_demo_can_price(guest, client):
    """The obvious alternative — zero the costs — does not work: in margin mode
    sell is derived FROM cost, so a zeroed book prices every package at $0 and
    the demo shows an empty estimate. A priced product stays priced.

    (A product whose real cost is 0 stays 0: those are the commercial catalog's
    deliberate placeholders, and inventing a cost for one would make an
    unpriced bid look legitimate — see the commercial notes in CLAUDE.md.)
    """
    real = {p['id']: p.get('cost')
            for p in client.get('/api/pricebook').get_json().get('roofing_catalog') or []}
    shown = {p['id']: p.get('cost')
             for p in guest.get('/api/pricebook').get_json().get('roofing_catalog') or []}
    assert real
    for pid, cost in real.items():
        assert bool(shown[pid]) == bool(cost), f'{pid} changed priced/unpriced'

    totals = [e['total'] for e in guest.get('/api/estimates').get_json()]
    assert totals and all(t > 0 for t in totals)


def test_the_real_book_is_available_behind_a_variable(guest, client, monkeypatch):
    monkeypatch.setenv('P1_DEMO_REAL_COSTS', '1')
    assert guest.get('/api/pricebook').get_json() == client.get('/api/pricebook').get_json()


# ── The seed ───────────────────────────────────────────────────────────────

def test_the_seed_never_expires(guest):
    """Dates are offsets, materialized on load. A checked-in valid_until would
    start rendering as expired a month after it was written — and _est_expired
    would then withdraw the signature block from the screen the demo exists to
    show."""
    import datetime
    today = datetime.datetime.utcnow().date()
    for doc in demo_store.store().values():
        vu = datetime.date.fromisoformat(doc['valid_until'])
        assert vu > today, f"{doc['estimate_id']} seeds an already-expired quote"
        assert '{{' not in str(doc), 'a date placeholder was left unmaterialized'


def test_reset_puts_the_demo_back(guest):
    guest.delete(f'/api/estimates/{sorted(demo_store.store())[0]}')
    assert len(demo_store.store()) == 2
    guest.get(f'/demo/{TOKEN}?reset=1')
    assert len(demo_store.store()) == 3


def test_the_seed_carries_no_real_contact_details():
    """example.com and 555 numbers only. This file is in the repo and the demo
    link goes to people outside the company — a real customer's phone number
    must never get in here by way of "making the demo look better"."""
    for doc in demo_store.store().values():
        c = doc.get('customer') or {}
        assert c.get('email', '').endswith('@example.com')
        assert '555-' in c.get('phone', '')
        assert 'projectoneroofing' not in str(c).lower()


# ── The admin control ──────────────────────────────────────────────────────
# The link has to be creatable by the person who wants it. Making this
# environment-only was the first design and it was wrong in a specific way: the
# admin who needs the link is the one who cannot restart the service to get it,
# so "off by default" meant "off, and the only way on is a redeploy".

def test_an_admin_can_create_the_link_without_touching_the_environment(
        client, monkeypatch):
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    assert client.get('/api/demo-link').get_json()['enabled'] is False

    d = client.post('/api/demo-link').get_json()
    assert d['enabled'] is True and '/estimate/demo/' in d['url']
    assert client.get('/api/demo-link').get_json()['url'] == d['url']


def test_a_created_link_actually_opens_the_demo(app, client, monkeypatch):
    """The end-to-end the first design could not do: create the link in
    Settings, paste it into another browser, and land in the demo."""
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    url = client.post('/api/demo-link').get_json()['url']
    guest = app.test_client()
    assert guest.get(url.split('/estimate', 1)[1]).status_code == 302
    assert guest.get('/api/me').get_json()['demo'] is True


def test_rotating_kills_the_old_url_at_once(app, client, monkeypatch):
    """The reason to rotate is that the old link is somewhere it should not be,
    so it has to stop working on the spot — not at the next restart."""
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    old = client.post('/api/demo-link').get_json()['url']
    new = client.post('/api/demo-link').get_json()['url']
    assert old != new
    assert app.test_client().get(old.split('/estimate', 1)[1]).status_code == 404
    assert app.test_client().get(new.split('/estimate', 1)[1]).status_code == 302


def test_revoking_switches_the_demo_off(app, client, monkeypatch):
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    url = client.post('/api/demo-link').get_json()['url']
    assert client.delete('/api/demo-link').get_json()['enabled'] is False
    assert app.test_client().get(url.split('/estimate', 1)[1]).status_code == 404


def test_a_live_guest_session_dies_with_the_link(app, client, monkeypatch):
    """Revoking has to reach sessions already open, not just new ones — the
    token is re-checked on every request for exactly this."""
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    url = client.post('/api/demo-link').get_json()['url']
    guest = app.test_client()
    guest.get(url.split('/estimate', 1)[1])
    assert guest.get('/api/estimates').status_code == 200
    client.delete('/api/demo-link')
    assert guest.get('/api/estimates').status_code == 401


def test_the_environment_variable_still_wins(client):
    """P1_DEMO_TOKEN is the override and the emergency kill, so the buttons
    must report that they cannot fight it rather than appearing to work."""
    d = client.get('/api/demo-link').get_json()
    assert d['enabled'] is True and d['env_override'] is True
    assert client.post('/api/demo-link').status_code == 409
    assert client.delete('/api/demo-link').status_code == 409
    # ...and the variable's token is what the link carries.
    assert d['url'].endswith(f'/estimate/demo/{TOKEN}')


def test_the_link_is_admin_only(app, monkeypatch):
    """Not manager-up. Handing out this link decides what leaves the company,
    and a demo session can be capped at manager (P1_DEMO_ROLE) — a manager
    minting one could out-reach themselves."""
    from portal import users as pusers
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    if not pusers.get('a-manager'):
        pusers.create('a-manager', password='test-only-password', role='manager')
    mgr = app.test_client()
    with mgr.session_transaction() as s:
        s['user'] = 'a-manager'
        s['username'] = 'a-manager'
    assert mgr.get('/api/demo-link').status_code == 403
    assert mgr.post('/api/demo-link').status_code == 403
    assert mgr.delete('/api/demo-link').status_code == 403


@pytest.mark.parametrize('method', ['get', 'post', 'delete'])
def test_a_guest_cannot_read_or_rotate_its_own_key(guest, method):
    """A guest holding this endpoint holds the key to their own session, and
    could rotate it out from under the person who invited them."""
    assert getattr(guest, method)('/api/demo-link').status_code == 403


def test_the_stored_token_is_not_world_readable(client, monkeypatch):
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    client.post('/api/demo-link')
    mode = os.stat(pdemo._token_path()).st_mode & 0o777
    assert mode == 0o600, f'demo token file is mode {mode:o}'
