"""Demo guests, from the composed site's point of view.

estimator/tests/test_demo.py covers what a guest can do inside the estimator.
This file covers the part only the merged origin can break: all four apps share
ONE cookie, so a guest identity that the portal does not understand either
signs itself out or reaches apps it must never reach. Both failures are silent
in the ordinary suite — nothing errors, the demo just stops working, or the
canvasser quietly opens.
"""
import pytest

from portal import demo


TOKEN = 'portal-demo-token-for-tests'


@pytest.fixture(autouse=True)
def demo_on(monkeypatch):
    monkeypatch.setenv('P1_DEMO_TOKEN', TOKEN)
    monkeypatch.delenv('P1_DEMO_ROLE', raising=False)


@pytest.fixture
def guest(client):
    assert client.get(f'/estimate/demo/{TOKEN}').status_code == 302
    return client


def test_the_link_lands_in_the_estimator(client):
    r = client.get(f'/estimate/demo/{TOKEN}')
    # script_root is '/estimate' under the mount, so the redirect has to carry
    # the prefix — a bare '/' would land on the portal launcher and bounce the
    # guest straight to a login page.
    assert r.headers['Location'].rstrip('/').endswith('/estimate')
    assert client.get('/estimate/').status_code == 200


#: Paths outside the estimator that a demo cookie must buy nothing on. The
#: canvasser's and the CRM's index pages are deliberately NOT here: their SPA
#: shells already serve to anyone anonymous (the shell loads, its own fetches
#: 401, and it redirects to login), so a guest reaching one is not a change —
#: what matters is that the data behind them stays shut.
OFF_LIMITS = ['/', '/nimbus/', '/api/users',
              '/canvass/api/pins', '/crm/api/leads', '/crm/api/board']


@pytest.mark.parametrize('path', OFF_LIMITS)
def test_a_demo_cookie_buys_nothing_outside_the_estimator(client, path):
    """The cookie is shared across all four apps; the identity is not. A demo
    session sets neither session['username'] nor session['user'], and those two
    keys are exactly what the canvasser, the CRM and the portal's own guard
    read — so a guest is anonymous to all three.

    Asserted as "same answer as an anonymous visitor" rather than against a
    fixed status, so this keeps holding if one of those guards changes what it
    returns. The point is that the demo cookie makes no difference.
    """
    anon_status = client.get(path).status_code
    client.get(f'/estimate/demo/{TOKEN}')
    assert client.get(path).status_code == anon_status
    assert anon_status in (302, 401, 403, 404), f'{path} was open to anonymous'


def test_a_guest_cannot_reach_portal_user_admin(guest):
    assert guest.get('/api/users').status_code == 401
    assert guest.post('/api/users/luke/reset').status_code == 401


def test_the_switcher_bar_does_not_sign_the_guest_out(guest):
    """/api/me is what shell.js renders the app-switcher bar from. Before the
    portal knew about demo sessions this fell into the "row deleted out from
    under a live cookie" branch, which calls sign_out() and returns 401 — so
    the bar cleared the guest's own session and redirected them to /login a
    second after the demo loaded."""
    me = guest.get('/api/me').get_json()
    assert me['authenticated'] is True
    assert me['demo'] is True
    assert [a['key'] for a in me['apps']] == ['estimate']
    assert me['admin_apps'] == []
    # ...and the session survived the call.
    assert guest.get('/api/me').get_json()['demo'] is True


def test_a_signed_in_rep_is_never_a_demo_guest(rep):
    me = rep.get('/api/me').get_json()
    assert not me.get('demo')
    assert {a['key'] for a in me['apps']} > {'estimate'}


def test_the_role_is_capped_at_manager(monkeypatch):
    """P1_DEMO_ROLE=admin would hand a guest the team list and every
    password-reset control, so it is refused rather than honoured."""
    monkeypatch.setenv('P1_DEMO_ROLE', 'admin')
    assert demo.role() == 'rep'
    monkeypatch.setenv('P1_DEMO_ROLE', 'manager')
    assert demo.role() == 'manager'
    monkeypatch.setenv('P1_DEMO_ROLE', 'nonsense')
    assert demo.role() == 'rep'


def test_demo_is_off_by_default(monkeypatch):
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    assert not demo.enabled()
    assert demo.link('https://example.com') == ''
    assert not demo.matches('')
    assert not demo.matches('anything')


def test_the_link_is_built_from_the_token():
    assert demo.link('https://example.com/') == \
        f'https://example.com/estimate/demo/{TOKEN}'


def test_ending_the_demo_clears_the_session(guest):
    guest.get('/logout')
    r = guest.get('/estimate/')
    assert r.status_code == 302 and '/login' in r.headers['Location']


# ── The admin control ──────────────────────────────────────────────────────

def test_the_stored_token_works_with_no_environment_variable(client, monkeypatch):
    """The whole reason this exists: an admin can switch the demo on without a
    Railway variable and a redeploy."""
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    assert not demo.enabled()
    tok = demo.create()
    try:
        assert demo.enabled() and demo.matches(tok)
        assert client.get(f'/estimate/demo/{tok}').status_code == 302
        assert client.get('/api/me').get_json()['demo'] is True
    finally:
        demo.revoke()


def test_the_environment_variable_beats_the_stored_token(monkeypatch):
    """P1_DEMO_TOKEN is the override and the emergency kill: setting it to a
    value nobody has switches every stored link off at the next boot."""
    stored = demo.create()
    try:
        monkeypatch.setenv('P1_DEMO_TOKEN', 'from-the-environment')
        assert demo.token() == 'from-the-environment'
        assert not demo.matches(stored)
        assert demo.env_override()
        # ...and the button cannot pretend to clear what it does not control.
        assert demo.revoke() is False
        assert demo.token() == 'from-the-environment'
    finally:
        monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
        demo.revoke()


def test_revoke_is_idempotent(monkeypatch):
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    demo.create()
    assert demo.revoke() is True
    assert demo.revoke() is True
    assert not demo.enabled()


def test_the_token_is_not_stored_on_the_estimators_volume(monkeypatch, tmp_path):
    """PORTAL_DATA_DIR, never DATA_DIR — the same rule portal.db follows, and
    for the same reason: DATA_DIR is the estimator's volume."""
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'estimator'))
    monkeypatch.delenv('P1_DEMO_TOKEN', raising=False)
    demo.create()
    assert (tmp_path / demo.TOKEN_FILE).exists()
    assert not (tmp_path / 'estimator' / demo.TOKEN_FILE).exists()
