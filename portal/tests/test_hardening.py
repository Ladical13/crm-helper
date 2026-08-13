"""Guards for the 2026-08-12 hardening pass: login throttling, security
headers, SQLite WAL, request-body caps, and the DISABLE_AUTH production block.

Each of these is the kind of setting that works by being present and fails
silently by being absent — nothing breaks visibly when a header stops being
sent or a database quietly drops back to the default journal mode. Hence tests.
"""
import os
import sqlite3
import sys

import pytest

from portal import dbtune, throttle, users
from portal.tests.conftest import SIGNUP_CODE


# ── Login throttling ─────────────────────────────────────────────────────────

def _bad_login(client, username='luke', password='wrongwrong'):
    return client.post('/login', data={'username': username, 'password': password})


def test_a_wrong_password_still_just_fails(client):
    """The throttle must not change the answer for an ordinary typo."""
    users.create('luke', password='roofroof1', role='admin')
    assert _bad_login(client).status_code == 401


def test_repeated_wrong_passwords_lock_the_account(client):
    users.create('luke', password='roofroof1', role='admin')
    codes = [_bad_login(client).status_code for _ in range(throttle.USER_MAX_FAILS)]
    assert codes[0] == 401, 'first attempt should be a plain failure'
    assert codes[-1] == 429, 'the attempt that hits the budget should lock out'
    # And it stays locked for the next attempt, rather than only rejecting the
    # one that tripped it.
    assert _bad_login(client).status_code == 429


def test_a_locked_out_account_rejects_even_the_right_password(client):
    """The check runs before the password is verified — that is the point, since
    verifying is the expensive part we are protecting."""
    users.create('luke', password='roofroof1', role='admin')
    for _ in range(throttle.USER_MAX_FAILS):
        _bad_login(client)
    resp = client.post('/login', data={'username': 'luke', 'password': 'roofroof1'})
    assert resp.status_code == 429


def test_the_lockout_response_says_how_long_to_wait(client):
    users.create('luke', password='roofroof1', role='admin')
    resp = None
    for _ in range(throttle.USER_MAX_FAILS):
        resp = _bad_login(client)
    assert resp.headers.get('Retry-After'), 'clients (and reps) need the wait'
    assert int(resp.headers['Retry-After']) > 0


def test_a_successful_login_clears_the_count(client):
    """Otherwise a rep who mistypes six times, gets in, then mistypes twice more
    tomorrow is locked out by yesterday's fumbling."""
    users.create('luke', password='roofroof1', role='admin')
    for _ in range(throttle.USER_MAX_FAILS - 1):
        _bad_login(client)
    assert client.post('/login', data={'username': 'luke',
                                       'password': 'roofroof1'}).status_code == 302
    assert _bad_login(client).status_code == 401, 'count should have been reset'


def test_one_username_lockout_does_not_lock_another(client):
    """Per-username is the tight counter; locking Bryan out because Luke was
    guessed at would take the crew off the road."""
    users.create('luke', password='roofroof1', role='admin')
    users.create('bryan', password='knockknock', role='rep')
    for _ in range(throttle.USER_MAX_FAILS):
        _bad_login(client, 'luke')
    assert client.post('/login', data={'username': 'bryan',
                                       'password': 'knockknock'}).status_code == 302


def test_spraying_across_usernames_trips_the_ip_counter(client):
    """The attack the per-username counter cannot see: one common password tried
    against many names, never enough failures on any single one."""
    codes = []
    for i in range(throttle.IP_MAX_FAILS + 1):
        codes.append(client.post('/login',
                                 data={'username': f'rep{i}', 'password': 'Summer2026'},
                                 environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code)
    assert 429 in codes, 'per-IP budget should have stopped the spray'


def test_a_second_ip_is_unaffected_by_the_first_ones_lockout(client):
    for i in range(throttle.IP_MAX_FAILS + 1):
        client.post('/login', data={'username': f'rep{i}', 'password': 'Summer2026'},
                    environ_base={'REMOTE_ADDR': '203.0.113.9'})
    users.create('luke', password='roofroof1', role='admin')
    resp = client.post('/login', data={'username': 'luke', 'password': 'roofroof1'},
                       environ_base={'REMOTE_ADDR': '198.51.100.4'})
    assert resp.status_code == 302


def test_an_unknown_client_address_still_gets_throttled(client):
    """If X-Forwarded-For ever goes missing, remote_addr is empty. That must
    bucket into one shared counter rather than skipping the per-IP check —
    otherwise the throttle quietly stops covering the spray case."""
    scopes = [s for s, _ in throttle._scopes('luke', '')]
    assert f'ip:{throttle.UNKNOWN_IP}' in scopes
    codes = [_bad_login(client, f'rep{i}', 'Summer2026').status_code
             for i in range(throttle.IP_MAX_FAILS + 1)]
    assert 429 in codes


def test_a_wrong_enrollment_code_counts_as_a_failure(client):
    """The setup code is the other guessable secret on the form, so grinding it
    has to cost the same as grinding a password."""
    codes = [client.post('/login', data={'username': 'newguy',
                                         'password': 'longenoughpw',
                                         'signup_code': 'not-the-code'}).status_code
             for _ in range(throttle.USER_MAX_FAILS)]
    assert codes[0] == 403, 'a wrong code should still read as forbidden'
    assert codes[-1] == 429


def test_escalation_outlasts_the_first_lockout(client):
    """Waiting out one lockout must not restore a full fresh budget, or the
    throttle only ever costs an attacker BASE_LOCK_SECONDS per batch."""
    users.create('luke', password='roofroof1', role='admin')
    for _ in range(throttle.USER_MAX_FAILS):
        _bad_login(client)
    first = throttle.retry_after('luke', '127.0.0.1')
    # Pretend the wait elapsed, then trip it again.
    throttle_row_expire('user:luke')
    for _ in range(throttle.USER_MAX_FAILS):
        _bad_login(client)
    second = throttle.retry_after('luke', '127.0.0.1')
    assert second > first, f'second lockout {second}s should exceed first {first}s'


def throttle_row_expire(scope):
    """Age a lock out without sleeping through it."""
    with users.get_db() as db:
        db.execute("UPDATE login_attempts SET locked_until='2000-01-01T00:00:00Z'"
                   ' WHERE scope=?', (scope,))


def test_an_admin_can_unlock_a_locked_out_rep(admin):
    """The release valve. Waiting 15 minutes is the wrong answer for a rep on a
    doorstep with a customer watching."""
    users.create('bryan', password='knockknock', role='rep')
    for _ in range(throttle.USER_MAX_FAILS):
        _bad_login(admin, 'bryan')
    assert _bad_login(admin, 'bryan').status_code == 429

    roster = {u['username']: u for u in admin.get('/api/users').json}
    assert roster['bryan']['locked'] > 0, 'the panel should show who is stuck'
    assert roster['luke']['locked'] == 0

    assert admin.post('/api/users/bryan/unlock').status_code == 200
    assert admin.post('/login', data={'username': 'bryan',
                                      'password': 'knockknock'}).status_code == 302


def test_only_admins_can_unlock(rep):
    assert rep.post('/api/users/luke/unlock').status_code == 403


def test_unlocking_does_not_clear_the_ip_spray_counter(client):
    """A per-username unlock must not hand a sprayer a fresh per-IP budget."""
    for i in range(throttle.IP_MAX_FAILS + 1):
        client.post('/login', data={'username': f'rep{i}', 'password': 'Summer2026'},
                    environ_base={'REMOTE_ADDR': '203.0.113.9'})
    throttle.unlock_user('rep0')
    assert throttle.retry_after('rep0', '203.0.113.9') > 0


def test_the_throttle_can_be_disabled_for_local_work(client, monkeypatch):
    users.create('luke', password='roofroof1', role='admin')
    monkeypatch.setenv('PORTAL_DISABLE_LOGIN_THROTTLE', '1')
    codes = [_bad_login(client).status_code
             for _ in range(throttle.USER_MAX_FAILS + 2)]
    assert set(codes) == {401}


# ── Security headers ─────────────────────────────────────────────────────────

HEADER_PATHS = ['/login', '/health', '/crm/', '/canvass/', '/estimate/']


@pytest.mark.parametrize('path', HEADER_PATHS)
def test_security_headers_on_every_app(client, path):
    """One after_request in portal/session.py has to cover all four apps — a
    header that only lands on the portal leaves the three tools bare."""
    resp = client.get(path)
    for header, expected in [('X-Content-Type-Options', 'nosniff'),
                             ('X-Frame-Options', 'DENY')]:
        assert resp.headers.get(header) == expected, f'{path} missing {header}'
    assert resp.headers.get('Referrer-Policy')
    assert resp.headers.get('Permissions-Policy')


def test_no_hsts_without_https(client):
    """HSTS is remembered by the browser for a year. Emitting it in local dev
    would make http://localhost unreachable for every other project too."""
    assert 'Strict-Transport-Security' not in client.get('/login').headers


def test_hsts_when_cookies_are_secure(monkeypatch):
    """Same signal that decides Secure cookies decides HSTS, so the two can't
    disagree about whether we're on HTTPS."""
    from flask import Flask

    from portal import session as psession
    monkeypatch.setenv('SESSION_COOKIE_SECURE', '1')
    app = Flask(__name__)
    psession.configure(app)
    app.add_url_rule('/x', 'x', lambda: 'ok')
    resp = app.test_client().get('/x')
    assert 'max-age=' in resp.headers.get('Strict-Transport-Security', '')


def test_configure_is_idempotent():
    """portal/wsgi.py re-applies configure() to every sub-app at mount time; a
    second registration would send each header twice."""
    from flask import Flask

    from portal import session as psession
    app = Flask(__name__)
    psession.configure(app)
    psession.configure(app)
    app.add_url_rule('/x', 'x', lambda: 'ok')
    resp = app.test_client().get('/x')
    assert resp.headers.getlist('X-Frame-Options') == ['DENY']


# ── Request body caps ────────────────────────────────────────────────────────

def test_every_app_caps_the_request_body():
    """Werkzeug buffers an upload into memory on .read(); with two workers one
    unbounded POST is half the site."""
    from portal.wsgi import application
    apps = [application.app] + list(application.mounts.values())
    for app in apps:
        limit = app.config.get('MAX_CONTENT_LENGTH')
        assert limit, f'{app.name} has no MAX_CONTENT_LENGTH'
        assert limit <= 64 * 1024 * 1024, f'{app.name} cap is too loose: {limit}'


def test_the_estimator_keeps_its_own_larger_cap():
    """It takes roof photos and multi-page carrier PDFs, so it asks for more —
    and the shared default must not quietly shrink it."""
    from estimator.app import app as estimator_app
    assert estimator_app.config['MAX_CONTENT_LENGTH'] == 30 * 1024 * 1024


# ── SQLite tuning ────────────────────────────────────────────────────────────

def test_the_portal_db_is_in_wal_mode():
    with users.get_db() as db:
        assert dbtune.journal_mode(db) == 'wal'


def test_every_apps_db_is_in_wal_mode():
    """Four databases, four separate get_db() functions — each one had to be
    wired up, so each one gets checked."""
    import canvasser.app as canv
    import salescrm.app as crm
    from agents import config as acfg
    for label, opener in [('salescrm', crm.get_db),
                          ('canvasser', canv.get_db),
                          ('nimbus cache', acfg.get_cache_db)]:
        with opener() as db:
            assert dbtune.journal_mode(db) == 'wal', f'{label} is not in WAL mode'


def test_statements_wait_for_a_lock_instead_of_failing():
    """The default is to raise 'database is locked' immediately, which surfaces
    to a rep as a failed save. busy_timeout makes it wait out the write."""
    with users.get_db() as db:
        timeout = db.execute('PRAGMA busy_timeout').fetchone()[0]
    assert timeout >= 1000, f'busy_timeout is only {timeout}ms'


def test_tune_survives_a_database_that_cannot_do_wal():
    """An in-memory DB reports journal_mode 'memory' and cannot be switched. A
    slow app beats a crashed one, so this must not raise."""
    conn = dbtune.tune(sqlite3.connect(':memory:'))
    assert dbtune.journal_mode(conn) in ('memory', 'wal')


def test_wal_sidecars_are_gitignored():
    """WAL adds -wal and -shm files beside every database, and the documented
    save routine is `git add -A`."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(repo_root, '.gitignore'), encoding='utf-8') as f:
        ignored = f.read()
    for pattern in ('*.db', '*.db-wal', '*.db-shm'):
        assert pattern in ignored, f'{pattern} is not gitignored'


# ── DISABLE_AUTH ─────────────────────────────────────────────────────────────

def test_disable_auth_is_ignored_in_a_deployed_environment(monkeypatch):
    """One variable turns off the guard on every estimator route. It must not be
    settable in production, however it gets there."""
    monkeypatch.setenv('DISABLE_AUTH', '1')
    monkeypatch.setenv('RAILWAY_ENVIRONMENT', 'production')
    for mod in [m for m in list(sys.modules) if m.startswith('estimator')]:
        sys.modules.pop(mod, None)
    import estimator.app as fresh
    try:
        assert fresh.DISABLE_AUTH is False
    finally:
        for mod in [m for m in list(sys.modules) if m.startswith('estimator')]:
            sys.modules.pop(mod, None)
        import estimator.app  # noqa: F401  (restore the shared instance)
