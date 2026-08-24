"""The login — the only thing between a public URL and a personal log.

This app is one password on the open internet. Everything here is about the
ways that goes wrong quietly: a missing variable that opens the door instead of
closing it, a password box nobody rate-limits, a cookie that survives a sign-out.
"""
import pytest

from conftest import PASSWORD, sign_in


# ── Default deny ────────────────────────────────────────────────────────────

def test_every_endpoint_needs_a_session(anon):
    for path in ('/api/workouts', '/api/workouts/active', '/api/exercises',
                 '/api/records', '/api/stats', '/api/routines', '/api/settings'):
        assert anon.get(path).status_code == 401, f'{path} answered signed out'


def test_signing_in_through_the_form_opens_the_app(anon):
    r = anon.post('/login', data={'password': PASSWORD})
    assert r.status_code == 302
    assert anon.get('/api/workouts').status_code == 200
    assert 'id="tab-lift"' in anon.get('/').get_data(as_text=True)


def test_the_wrong_password_does_not(anon):
    r = anon.post('/login', data={'password': 'not-it'})
    assert r.status_code == 401
    assert 'Wrong password' in r.get_data(as_text=True)
    assert anon.get('/api/workouts').status_code == 401


def test_signing_out_really_signs_out(client):
    assert client.get('/api/workouts').status_code == 200
    assert client.get('/logout').status_code == 200
    assert client.get('/api/workouts').status_code == 401
    assert 'type="password"' in client.get('/').get_data(as_text=True)


def test_an_empty_password_is_never_accepted(anon, monkeypatch):
    """`if not password` in the wrong place would make the empty string a
    master key."""
    assert anon.post('/login', data={'password': ''}).status_code == 401
    assert anon.post('/login', data={}).status_code == 401


# ── Fail closed ─────────────────────────────────────────────────────────────

def test_with_no_password_set_it_refuses_to_serve_in_production(anon, monkeypatch):
    """The one that matters: an unset variable must never be the difference
    between a login and an open door. It serves an error, not the app."""
    monkeypatch.delenv('WORKOUT_PASSWORD', raising=False)
    monkeypatch.setenv('RAILWAY_ENVIRONMENT', 'production')
    page = anon.get('/')
    assert page.status_code == 503
    assert 'Not configured' in page.get_data(as_text=True)
    assert anon.get('/api/workouts').status_code == 503
    assert anon.post('/login', data={'password': 'anything'}).status_code == 503


def test_with_no_password_set_it_opens_up_locally(anon, monkeypatch):
    """...and the same missing variable on a laptop just works, which is why
    the production check has to be explicit rather than implied."""
    monkeypatch.delenv('WORKOUT_PASSWORD', raising=False)
    monkeypatch.delenv('RAILWAY_ENVIRONMENT', raising=False)
    assert anon.get('/api/workouts').status_code == 200


def test_a_signed_in_session_still_works_without_the_password_variable(client, monkeypatch):
    """Rotating the password must not sign the owner out mid-session; the
    cookie is the authority once it exists."""
    monkeypatch.setenv('WORKOUT_PASSWORD', 'a-brand-new-password')
    assert client.get('/api/workouts').status_code == 200


# ── Throttle ────────────────────────────────────────────────────────────────

def test_guessing_gets_locked_out(anon, app):
    """A public password box with no rate limit is a password box being
    guessed at right now."""
    from auth import MAX_FAILS
    for _ in range(MAX_FAILS):
        assert anon.post('/login', data={'password': 'wrong'}).status_code == 401
    blocked = anon.post('/login', data={'password': 'wrong'})
    assert blocked.status_code == 429
    assert 'Too many attempts' in blocked.get_data(as_text=True)
    # And the RIGHT password is refused too while the lockout stands — otherwise
    # the lockout is only a delay for whoever eventually guesses it.
    assert anon.post('/login', data={'password': PASSWORD}).status_code == 429


def test_the_lockout_survives_a_restart(anon, app):
    """Counted in SQLite, not in memory: two workers would each keep their own
    counter and hand out double the allowance, and a redeploy would reset it."""
    from auth import MAX_FAILS
    for _ in range(MAX_FAILS):
        anon.post('/login', data={'password': 'wrong'})
    with app.get_db() as db:
        row = db.execute('SELECT * FROM login_fails').fetchone()
    assert row is not None and row['locked_until'], (
        'the failure count is not in the database, so it is per-process')


def test_a_good_password_clears_the_count(anon, app):
    anon.post('/login', data={'password': 'wrong'})
    anon.post('/login', data={'password': PASSWORD})
    with app.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM login_fails').fetchone()['c'] == 0


def test_an_unreadable_client_address_is_still_counted(anon, app):
    """Fail closed: an address that cannot be read buckets into 'unknown'
    rather than skipping the check, or the way around the throttle would be to
    send a broken header."""
    from auth import MAX_FAILS
    blind = {'REMOTE_ADDR': None}
    for _ in range(MAX_FAILS):
        anon.post('/login', data={'password': 'wrong'}, environ_overrides=blind)
    assert anon.post('/login', data={'password': 'wrong'},
                     environ_overrides=blind).status_code == 429
    with app.get_db() as db:
        assert db.execute("SELECT 1 FROM login_fails WHERE ip='unknown'").fetchone()


# ── Cookie and headers ──────────────────────────────────────────────────────

def test_the_session_cookie_is_locked_down(app):
    cfg = app.app.config
    assert cfg['SESSION_COOKIE_HTTPONLY'] is True
    assert cfg['SESSION_COOKIE_SAMESITE'] == 'Lax'
    assert cfg['SESSION_COOKIE_NAME'] == 'p1lift'


def test_security_headers_ship_on_every_response(anon):
    h = anon.get('/').headers
    assert h['X-Content-Type-Options'] == 'nosniff'
    assert h['X-Frame-Options'] == 'DENY'


def test_hsts_is_tied_to_the_secure_flag(anon, monkeypatch):
    """A browser remembers HSTS for a year. Emitting it from localhost would
    break plain http:// for every other project on the laptop, so it is gated on
    the same signal as the Secure cookie flag — the two can never disagree."""
    assert 'Strict-Transport-Security' not in anon.get('/').headers
    monkeypatch.setenv('WORKOUT_COOKIE_SECURE', '1')
    assert 'Strict-Transport-Security' in anon.get('/').headers


def test_signing_out_also_clears_the_offline_cache(client):
    """The service worker keeps API responses so history reads with no signal.
    Those would outlive the session on the device — on a borrowed phone, "sign
    out" has to mean the log is gone, not just that the next request is
    refused."""
    page = client.get('/logout').get_data(as_text=True)
    assert 'caches.delete' in page
    assert 'unregister' in page
    assert "location.replace('/login')" in page
    # The cookie is cleared server-side regardless of what the script manages.
    assert client.get('/api/workouts').status_code == 401
