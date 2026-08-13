"""The 🔑 Team Logins panel's failed-login unlock.

The throttle that creates these lockouts lives in portal/throttle.py and is
tested there. What matters here is that the panel Luke actually uses can see a
locked rep and release them — a lockout nobody can clear is a rep who cannot
work until it expires on its own.
"""
from portal import throttle, users as portal_users


def _lock_out(username):
    for _ in range(throttle.USER_MAX_FAILS):
        throttle.record_failure(username, '203.0.113.7')


def _row(client, username):
    rows = client.get('/api/users').get_json()
    return next((r for r in rows if r['username'] == username), None)


def test_the_panel_reports_a_locked_rep(client):
    throttle.unlock_user('luke')
    assert _row(client, 'luke')['locked'] == 0
    _lock_out('luke')
    try:
        assert _row(client, 'luke')['locked'] > 0
    finally:
        throttle.unlock_user('luke')


def test_an_admin_can_unlock(client):
    _lock_out('luke')
    assert throttle.retry_after('luke', '203.0.113.7') > 0
    assert client.post('/api/users/luke/unlock').status_code == 200
    assert throttle.retry_after('luke', '') == 0
    assert _row(client, 'luke')['locked'] == 0


def test_a_rep_cannot_unlock(app):
    """Otherwise the throttle is advisory: anyone with a session clears it."""
    if not portal_users.get('bryan'):
        portal_users.create('bryan', password='test-only-password', role='rep')
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = 'bryan'
    assert c.post('/api/users/luke/unlock').status_code == 403


def test_unlock_requires_a_session(anon):
    assert anon.post('/api/users/luke/unlock').status_code in (401, 403)
