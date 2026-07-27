"""The invariants that make three apps behave like one site.

The whole merge rests on a single mechanism: four Flask apps on one origin
sharing one signed cookie. If that breaks, a rep gets bounced to the login page
halfway through building an estimate. These tests pin it down.
"""
from conftest import SIGNUP_CODE

from portal import users as pusers


# ── One login, three apps ────────────────────────────────────────────────────

def test_one_login_is_accepted_by_all_three_apps(admin):
    """The point of the whole exercise: sign in once, use everything."""
    assert admin.get('/estimate/api/me').status_code == 200
    assert admin.get('/canvass/api/me').get_json()['authenticated'] is True
    assert admin.get('/crm/api/me').get_json()['authenticated'] is True
    # And real data endpoints, not just the identity ones.
    assert admin.get('/crm/api/leads').status_code == 200
    assert admin.get('/canvass/api/pins').status_code == 200
    assert admin.get('/estimate/api/estimates').status_code == 200


def test_apps_agree_on_who_you_are(admin):
    """Three /api/me endpoints, three payload shapes, one identity."""
    assert admin.get('/estimate/api/me').get_json()['username'] == 'luke'
    assert admin.get('/canvass/api/me').get_json()['username'] == 'luke'
    assert admin.get('/crm/api/me').get_json()['username'] == 'luke'


def test_signing_out_signs_out_of_everything(admin):
    admin.get('/logout')
    assert admin.get('/crm/api/leads').status_code == 401
    assert admin.get('/canvass/api/pins').status_code == 401
    assert admin.get('/estimate/api/estimates').status_code == 401


def test_role_is_shared_across_apps(rep):
    """A rep is a rep everywhere — salescrm gates its Numbers tab on this."""
    assert rep.get('/crm/api/me').get_json()['is_manager'] is False
    assert rep.get('/estimate/api/me').get_json()['role'] == 'rep'
    # ...and admin-only surfaces stay closed.
    assert rep.get('/estimate/api/users').status_code == 403
    assert rep.post('/api/invites', json={'username': 'x'}).status_code == 403


def test_manager_sees_manager_surfaces(client):
    pusers.create('casey', password='managerpw1', role='manager')
    client.post('/login', data={'username': 'casey', 'password': 'managerpw1'})
    crm_me = client.get('/crm/api/me').get_json()
    assert crm_me['is_manager'] is True
    # A manager is not a full admin: strict-admin surfaces stay shut.
    assert client.get('/estimate/api/users').status_code == 403


# ── Default-deny ─────────────────────────────────────────────────────────────

def test_anonymous_api_calls_are_rejected(client):
    for path in ('/crm/api/leads', '/canvass/api/pins', '/estimate/api/estimates',
                 '/api/me'):
        assert client.get(path).status_code == 401, path


def test_anonymous_pages_redirect_to_the_portal_login(client):
    r = client.get('/estimate/')
    assert r.status_code == 302
    assert r.headers['Location'].startswith('/login')
    assert client.get('/').headers['Location'].startswith('/login')


def test_login_bounces_back_to_where_you_were(client):
    """A rep deep-linked into an estimate should land there, not on the launcher."""
    dest = client.get('/estimate/').headers['Location']
    assert 'next=' in dest and '/estimate' in dest


def test_open_redirects_are_refused(client):
    for evil in ('//evil.com/', 'https://evil.com', '/\\evil.com'):
        r = client.post('/login', data={'username': 'luke', 'password': 'roofroof1',
                                        'signup_code': SIGNUP_CODE, 'next': evil})
        assert r.headers['Location'] == '/', evil
        client.get('/logout')


# ── Enrollment ───────────────────────────────────────────────────────────────

def test_bad_signup_code_rejected(client):
    r = client.post('/login', data={'username': 'mallory', 'password': 'longenough1',
                                    'signup_code': 'wrong'})
    assert r.status_code == 403
    assert pusers.get('mallory') is None


def test_first_user_bootstraps_as_admin_and_the_next_does_not(client):
    client.post('/login', data={'username': 'luke', 'password': 'roofroof1',
                                'signup_code': SIGNUP_CODE})
    assert pusers.role_of('luke') == 'admin'
    client.get('/logout')
    client.post('/login', data={'username': 'bryan', 'password': 'knockknock',
                                'signup_code': SIGNUP_CODE})
    assert pusers.role_of('bryan') == 'rep'


def test_invite_locked_to_one_username(admin):
    code = admin.post('/api/invites', json={'username': 'derik'}).get_json()['code']
    admin.get('/logout')
    wrong = admin.post('/login', data={'username': 'someone', 'password': 'longenough1',
                                       'signup_code': code})
    assert wrong.status_code == 403
    right = admin.post('/login', data={'username': 'derik', 'password': 'longenough1',
                                       'signup_code': code})
    assert right.status_code == 302
    assert pusers.get('derik') is not None


def test_wrong_password_does_not_enroll_over_an_existing_account(client):
    pusers.create('luke', password='roofroof1', role='admin')
    r = client.post('/login', data={'username': 'luke', 'password': 'not-the-password',
                                    'signup_code': SIGNUP_CODE})
    assert r.status_code == 401
    # The real password must still work — enrollment must not have overwritten it.
    assert pusers.verify('luke', 'roofroof1')


# ── Customer-facing links that predate the merge ─────────────────────────────

def test_old_customer_links_still_resolve(client):
    """Signed estimates and change orders are already in customers' inboxes
    pointing at the estimator's old root paths. These must never 404."""
    assert client.get('/sign/abc123').headers['Location'] == '/estimate/sign/abc123'
    assert client.get('/sign-co/abc123').headers['Location'] == '/estimate/sign-co/abc123'
    assert client.get('/uploads/photo.jpg').headers['Location'] == '/estimate/uploads/photo.jpg'


def test_compat_redirects_do_not_require_a_login(client):
    """The customer signing the contract has no account."""
    for path in ('/sign/abc123', '/sign-co/abc123', '/uploads/photo.jpg'):
        assert client.get(path).status_code == 302, path


# ── Mounting ─────────────────────────────────────────────────────────────────

def test_each_app_serves_its_own_shell_and_assets(client):
    for path in ('/canvass/', '/crm/', '/canvass/static/app.js', '/crm/static/app.js',
                 '/estimate/static/app.js', '/shell.js', '/shell.css', '/manifest.json'):
        assert client.get(path).status_code == 200, path


def test_service_workers_are_scoped_to_their_mount(client):
    """Served from the mount path, so the browser cannot let either one claim
    '/' — which is how the estimator's and the CRM's used to collide."""
    for path in ('/estimate/sw.js', '/crm/sw.js', '/sw.js'):
        assert client.get(path).status_code == 200, path


def test_only_the_portal_answers_login(client):
    """Two sign-in forms on one origin means two apps writing the session."""
    assert client.get('/login').status_code == 200
    for path in ('/estimate/login', '/crm/api/login', '/canvass/api/login'):
        assert client.get(path).status_code != 200, path
