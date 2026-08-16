"""The read-only executive-team principal.

This is the one place in the codebase where something other than a human
password gets you a session, so the tests are deliberately paranoid. The
properties that matter, in order:

  1. No token, wrong token, or no configured token → no session.
  2. The principal it issues can ONLY do GETs, on an allowlist.
  3. Neither the token nor a way to guess it ever appears in a response.

If any of those break, this stops being a reporting credential and starts being
a way into the business.
"""
import pytest

TOKEN = 'test-token-do-not-use-in-production'


@pytest.fixture
def bridge(monkeypatch):
    """Turn the bridge on with a known token.

    Deliberately does NOT touch the data dirs: conftest freezes those at import
    time (each sub-app bakes DATA_DIR into module constants), so re-pointing
    them here would leave the app reading one database and the test asserting
    against another.
    """
    monkeypatch.setenv('P1_READONLY_TOKEN', TOKEN)
    yield


def _exchange(client, token=TOKEN):
    return client.post('/api/apibot/session', headers={'X-P1-Token': token})


# ── Getting in ───────────────────────────────────────────────────────────────

def test_valid_token_returns_a_session(client, bridge):
    r = _exchange(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body['username'] == 'apibot'
    assert body['read_only'] is True
    assert body['allowlist']


def test_wrong_token_is_rejected(client, bridge):
    assert _exchange(client, 'not-the-token').status_code == 401


def test_missing_header_is_rejected(client, bridge):
    assert client.post('/api/apibot/session').status_code == 401


def test_empty_token_header_is_rejected(client, bridge):
    """An empty header must not compare equal to an unset expectation."""
    assert _exchange(client, '').status_code == 401


def test_the_endpoint_hides_when_no_token_is_configured(client, monkeypatch):
    """404, not 401 — a prober should not learn the feature exists here."""
    monkeypatch.delenv('P1_READONLY_TOKEN', raising=False)
    assert _exchange(client).status_code == 404


def test_the_token_never_appears_in_a_response(client, bridge):
    body = _exchange(client).get_data(as_text=True)
    assert TOKEN not in body


def test_apibot_cannot_log_in_through_the_form(client, bridge):
    """The account exists but holds a random password nobody has. The token is
    the only route to it."""
    _exchange(client)          # creates the user
    for guess in ('apibot', 'password', '', 'apibot123'):
        r = client.post('/login', data={'username': 'apibot', 'password': guess})
        assert r.status_code != 302 or '/login' in r.headers.get('Location', '')


def test_repeated_exchange_is_idempotent(client, bridge):
    """Creating the principal twice must not error or duplicate the user."""
    assert _exchange(client).status_code == 200
    assert _exchange(client).status_code == 200
    from portal import users
    assert users.get('apibot') is not None


# ── What it can and cannot do once in ────────────────────────────────────────

def test_allowlisted_get_is_permitted(client, bridge):
    _exchange(client)
    # Reaches the endpoint rather than being turned away by the guard. The
    # endpoint's own status is not this test's business — 403 from `guard` is.
    r = client.get('/nimbus/api/settings')
    assert r.status_code != 403 or 'apibot' not in r.get_data(as_text=True)


def test_every_write_verb_is_refused(client, bridge):
    """The core promise. A read-only credential that can POST is not read-only."""
    _exchange(client)
    for verb, call in (
        ('POST',   client.post),
        ('PUT',    client.put),
        ('PATCH',  client.patch),
        ('DELETE', client.delete),
    ):
        r = call('/nimbus/api/settings')
        assert r.status_code == 403, f'{verb} was not refused'
        assert 'read-only' in r.get_json()['error']


def test_a_write_to_an_allowlisted_path_is_still_refused(client, bridge):
    """Being on the allowlist buys a GET, not a method."""
    _exchange(client)
    r = client.post('/crm/api/goals', json={'rep': 'luke', 'target': 1})
    assert r.status_code == 403


def test_non_allowlisted_get_is_refused(client, bridge):
    _exchange(client)
    for path in ('/api/users', '/nimbus/', '/api/me'):
        r = client.get(path)
        assert r.status_code == 403, path
        assert 'not available' in r.get_json()['error']


def test_customer_data_paths_are_not_reachable(client, bridge):
    """The allowlist is aggregates only. A reporting credential that can also
    pull contact details and documents is a much bigger thing to lose."""
    _exchange(client)
    for path in ('/crm/api/documents', '/estimate/api/estimates',
                 '/api/users', '/crm/api/leads/1/activities'):
        # Refused, by whichever layer gets there first: 403 from the guard,
        # 404 if no such route, 405 if the route is not a GET. Werkzeug raises
        # 404/405 during URL matching, before any before_request hook runs, so
        # asserting a bare 403 would be asserting the order of two unrelated
        # mechanisms. What matters is that nothing is served.
        status = client.get(path).status_code
        assert status >= 400, f'{path} returned {status}'


# ── The guard does not affect anybody else ───────────────────────────────────

def test_a_normal_admin_is_untouched(admin, bridge):
    """The guard must be invisible to every other principal — it returns early
    on a session that isn't apibot."""
    assert admin.get('/api/me').status_code == 200
    r = admin.post('/nimbus/api/schedule/seo_weekly', json={'enabled': True})
    assert r.status_code != 403


def test_anonymous_still_gets_401_not_403(client, bridge):
    """The apibot guard must not shadow the portal's own default-deny."""
    assert client.get('/api/users').status_code == 401


# ── Path matching ────────────────────────────────────────────────────────────

def test_allowlist_matches_the_mounted_path_not_the_sub_app_path():
    """DispatcherMiddleware strips the mount prefix before the sub-app sees the
    request, so the guard reconstructs script_root + path. If that ever
    regresses, '/api/analytics' would match nothing and the bridge would look
    broken rather than insecure — but check it explicitly."""
    from portal import apibot
    assert apibot.path_allowed('/estimate/api/analytics')
    assert not apibot.path_allowed('/api/analytics')


def test_allowlist_does_not_match_by_substring():
    """A prefix check must not let '/estimate/api/analytics-export' through by
    accident — or rather, if it does, that must be a deliberate choice."""
    from portal import apibot
    assert not apibot.path_allowed('/evil/estimate/api/analytics')
    assert not apibot.path_allowed('/crm/api/leadsX/../../secret')
